"""Run a pipeline step on rented machines instead of this one (RunPod serverless, docs/09).

A job is one step -- or one piece of a long step -- of one episode:

    {"step": "lip_scan", "inputs": {"video": <signed GET>}, "args": {"range": [0, 15]},
     "outputs": {"result": <signed PUT>}}

Two images, and the app picks the endpoint rather than the image name:

  sameer27488/dubstudio-worker:pipeline   CPU. Lip scan, Whisper, and the other CPU steps. On RUNPOD_CPU_ENDPOINT.
  sameer27488/dubstudio-worker:face-gpu    CUDA 13. Face map only (InsightFace buffalo_l + yolo26l). On RUNPOD_GPU_ENDPOINT.

Nothing is downloaded while a job runs. **No key ever leaves this machine** unless CLOUD_AI is on, and then only the
OpenRouter key, for the steps that ask Claude to name a face. The worker otherwise gets signed links that expire.
The endpoint keeps no workers when the queue is empty and starts more when it grows, so a long step is split
into pieces that run side by side -- which is the only parallelism that helps (measured: several processes on ONE
machine are slower than one, because the lip scan is CPU-bound).

Settings (app/.env):
  RUNPOD_API_KEY        the account key
  RUNPOD_CPU_ENDPOINT   the CPU image endpoint
  RUNPOD_GPU_ENDPOINT   the face-gpu image endpoint
  CLOUD_STEPS           which steps go to the cloud, e.g. "lip_gpu,mksrt_src,mksrt_dub,facemap"
                        "match", "cut", "scene" and "tail" are whole visits (one machine each); "all" is every visit
  CLOUD_PIECES          how many pieces a long step is split into (default 8)
"""
import json, tarfile, time, urllib.error, urllib.request
from pathlib import Path

from . import envfile, storage

API = "https://api.runpod.ai/v2"
PIECE_SECONDS = 15.0        # lipgpu's own piece size: keep it, so the cloud writes the same files
UA = "dubstudio/1.0"


# ---------------- settings ----------------
def endpoint(kind="cpu"):
    return envfile.get(f"RUNPOD_{kind.upper()}_ENDPOINT")


def available(kind="cpu"):
    return bool(envfile.get("RUNPOD_API_KEY") and endpoint(kind))


def steps():
    """The steps the owner has moved to the cloud ("lip_gpu,render" or "all")."""
    raw = (envfile.get("CLOUD_STEPS", "") or "").strip()
    return {s.strip() for s in raw.split(",") if s.strip()}


# These ask Claude. On a rented machine that means sending it an OpenRouter key, which is the owner's call, not
# ours -- so unless CLOUD_AI is on they stay here. Running them in the cloud without a key does not fail: it
# quietly gives a *worse* answer (facemap: "groups without a clear vote stay 'unknown'"), and a quietly worse
# answer is the thing to avoid. The scene edit is the same kind of call.
NEEDS_AI_KEY = {"facemap", "qc", "library", "scene_edit"}

# The render tail chains **big videos**: `render` writes the cut, `verify` measures it, `bookends` makes the clean
# master from it, `transitions` reads that, `subs` burns captions onto it, `qc` and `check` look at all three. Each
# step reads the previous step's multi-GB file, and the work folder records where it is. One step per rented
# machine therefore does not work: the machine that runs `verify` is not the machine that ran `render`, and the
# file would have to travel up and down -- gigabytes each way, per step. That chain belongs on ONE machine.
# Measured, not assumed: the only work files over 1 MB in a finished episode are videos; everything else (the lip
# scan's 42 range files, the meshes, the manifests) is a few kB and travels fine.
#
# Individual tail steps stay refused (`enabled_for` is False). They go together via `run_chain` when the owner
# asks for them (`CLOUD_STEPS` includes `all`, `tail`, `render` or `render_tail`). `trans_plan` is JSON only and
# stays here, so the editor's picks are applied before the videos are cut. The final check is added to that
# same visit by the runner, after the scene-edit sheet, and only when CLOUD_AI is on (`qc` asks Claude).
ONE_MACHINE = {"render", "verify", "bookends", "transitions", "subs_dub", "subs_en", "dynsrt", "check"}
CHAIN = ("render", "verify", "bookends", "transitions", "subs_dub", "subs_en")
CHAIN_NAMES = {"all", "tail", "render", "render_tail"}
# Two CPU visits before the face map, split only by the decision pause (that pause stays on the website).
# The face map is not in either list: it stays on the GPU. The scene edit is its own visit after the face map.
MATCH_CHAIN = ("analyze", "merged")
CUT_CHAIN = ("sheet", "manifest", "lipsync", "groups", "library")
# `sh`, not `/bin/sh`: an absolute path that exists on this machine is uploaded as a file, and the worker
# then tries to execute that file.
MERGED_CMD = ["sh", "-c", "{PY} {SCRIPTS}/merged.py {WORK} 95 > {WORK}/merged.txt"]


def ai_key_allowed():
    return envfile.flag("CLOUD_AI", False)


def capable(step):
    """Can this step run on its own, on a machine that has only the episode's files and the small work files?"""
    if step in ONE_MACHINE:
        return False
    return step not in NEEDS_AI_KEY or ai_key_allowed()


# The face map runs on its own CUDA image (`app/worker/Dockerfile.gpu`): the CPU pipeline image has no insightface
# or ultralytics, so without a GPU endpoint the face map stays here rather than failing on a worker.
GPU_STEPS = {"facemap"}


def kind_for(step):
    return "gpu" if step in GPU_STEPS else "cpu"


def stretch(name):
    """One named visit is on: match, cut, scene, or the render tail. "all" means every visit."""
    if name == "tail":
        return chain_wanted()
    if name == "scene":
        return enabled_for("scene_edit")
    if not available("cpu"):
        return False
    want = steps()
    return name in want or "all" in want


def claimed_by_stretch(step):
    """This step rides inside a visit, so it is not also sent as its own job."""
    if step in MATCH_CHAIN and _visit_on("match"):
        return True
    if step in CUT_CHAIN and _visit_on("cut"):
        if step == "library" and not ai_key_allowed():
            return False
        return True
    if step == "captions" and _visit_on("cut"):
        return True
    if step == "qc" and chain_wanted():
        return True
    if step in CHAIN and chain_wanted():
        return True
    return False


def _visit_on(name):
    """Like `stretch`, but it does not ask `enabled_for` (that calls back into here)."""
    if not available("cpu"):
        return False
    want = steps()
    return name in want or "all" in want


def enabled_for(step, kind=None):
    """`CLOUD_STEPS` asks; `capable` decides. A step that belongs to a visit is not also sent on its own.
    "all" means every visit, not "try the render one step at a time and fail at the videos"."""
    if claimed_by_stretch(step):
        return False
    want = steps()
    named = "all" in want or step in want or (step == "scene_edit" and "scene" in want)
    if not named or not capable(step):
        return False
    return available(kind or kind_for(step))


def chain_wanted(kind="cpu"):
    """The render tail as one job on one machine. Individual tail steps stay refused."""
    want = steps()
    return available(kind) and bool(want & CHAIN_NAMES)


def pieces():
    return max(1, envfile.number("CLOUD_PIECES", 8))


# ---------------- the queue ----------------
def _call(path, body=None, timeout=60, kind="cpu"):
    key = envfile.get("RUNPOD_API_KEY")
    ep = endpoint(kind)
    if not ep:
        raise RuntimeError(f"no RUNPOD_{kind.upper()}_ENDPOINT set")
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{API}/{ep}{path}", data=data,
                               headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                        "User-Agent": UA})
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def submit(job_input, kind="cpu"):
    return _call("/run", {"input": job_input}, kind=kind)["id"]


def status(job_id, kind="cpu"):
    return _call(f"/status/{job_id}", timeout=30, kind=kind)


def health(kind="cpu"):
    return _call("/health", timeout=30, kind=kind)


def cancel(job_id, kind="cpu"):
    """Ask RunPod to stop a job. Used when a try is abandoned so we stop paying for it."""
    return _call(f"/cancel/{job_id}", body={}, timeout=30, kind=kind)


def wait(job_ids, on_progress=None, timeout=7200, poll=10, kind="cpu"):
    """Wait for every job. -> ({job id: status}, [failed job ids with their reason]).
    A rented machine can be slow or taken away mid-job, so a failure is reported, not raised: the caller decides
    what to send again. The pieces that finished are kept."""
    done, failed, start = {}, {}, time.time()
    while len(done) + len(failed) < len(job_ids):
        if time.time() - start > timeout:
            for jid in job_ids:
                if jid not in done and jid not in failed:
                    failed[jid] = f"still running after {timeout} s"
            break
        for jid in job_ids:
            if jid in done or jid in failed:
                continue
            try:
                st = status(jid, kind=kind)
            except (urllib.error.URLError, TimeoutError):
                continue
            if st.get("status") == "COMPLETED":
                done[jid] = st
            elif st.get("status") in ("FAILED", "CANCELLED", "TIMED_OUT"):
                failed[jid] = str(st.get("error") or st.get("output") or st.get("status"))[:300]
        if on_progress:
            try:
                h = health(kind=kind).get("workers", {})
            except Exception:
                h = {}
            on_progress(len(done), len(job_ids), h)
        if len(done) + len(failed) < len(job_ids):
            time.sleep(poll)
    return done, failed


# ---------------- the lip scan, split into pieces ----------------
def make_ranges(duration_s, piece=PIECE_SECONDS):
    """The pieces the local step would cut, **exactly** -- this is `lipgpu.make_ranges` written out:

        s = 0; while s < L: [s, s + piece]; s += piece

    The last piece therefore runs PAST the end of the video (a 627 s episode ends with `[615, 630]`, not
    `[615, 627]`), and lipgpu names the file after the range it was asked for. Clamping it looks tidier and is
    wrong: the file comes back as `range_615_627.json`, which is not the name the rest of the pipeline looks for.
    """
    out, s = [], 0.0
    while s < duration_s:
        out.append([s, s + piece])
        s += piece
    return out


def ensure_input_in_cloud(db, show, ep, kind="src_video"):
    """The worker reads from R2: make sure this episode's file is there (a show whose files live on the Mac has
    not uploaded it yet). -> the key."""
    a = db.one("SELECT * FROM assets WHERE episode_id=? AND kind=? AND role='input'", (ep["id"], kind))
    if not a:
        raise RuntimeError(f"the episode has no {kind}")
    if a.get("r2_key") and storage.head(a["r2_key"]):
        return a["r2_key"]
    key = storage.input_key(show, ep, kind, Path(a["path"]).name)
    storage.put_big_file(Path(a["path"]), key)
    db.update("assets", a["id"], r2_key=key)
    return key


def lip_scan(db, show, ep, duration_s, n_pieces=None, log=print, on_progress=None, tries=3):
    """Scan the whole source video on rented machines and write `<work>/lips_nets` exactly as the local step would.

    The episode is cut into the pipeline's own pieces and shared between machines. A share that fails (a slow or
    reclaimed worker) is simply sent again -- the shares that finished are kept, so one bad machine costs one
    share, not the episode. -> {pieces, files, seconds, worker_seconds, retries}."""
    n = n_pieces or pieces()
    key = ensure_input_in_cloud(db, show, ep)
    cuts = make_ranges(duration_s)
    shares = [c for c in (cuts[i::n] for i in range(n)) if c]          # round-robin: a fair mix for every machine
    log(f"{len(cuts)} pieces of {PIECE_SECONDS:.0f} s across {len(shares)} machines ({duration_s:.0f} s of video)")

    t0, worker_s, retries = time.time(), 0.0, 0
    nets = Path(ep["work_dir"]) / "lips_nets"
    nets.mkdir(parents=True, exist_ok=True)
    todo = list(enumerate(shares))
    for attempt in range(tries):
        if not todo:
            break
        video = storage.presign(key, 10800)                            # fresh links for each attempt
        jobs = {}
        for i, mine in todo:
            ok = storage.work_key(show, ep, f"lips_cloud/piece_{i:02d}.tar.gz")
            jid = submit({"step": "lip_scan", "inputs": {"video": video},
                          "args": {"ranges": mine, "device": "cpu"},
                          "outputs": {"result": storage.presign(ok, 10800, method="PUT")}})
            jobs[jid] = (i, mine, ok)
        done, failed = wait(list(jobs), on_progress=on_progress)
        for jid, st in done.items():
            i, mine, ok = jobs[jid]
            worker_s += st.get("executionTime", 0) / 1000
            tar = nets.parent / f"_piece_{i:02d}.tar.gz"
            storage.download(ok, tar)
            with tarfile.open(tar) as tf:
                tf.extractall(nets.parent / "_piece")
            for f in (nets.parent / "_piece" / "lips_nets").glob("range_*.json"):
                f.replace(nets / f.name)
            tar.unlink(missing_ok=True)
        todo = [jobs[jid][:2] for jid in failed]
        if todo:
            retries += len(todo)
            log(f"attempt {attempt + 1}: {len(failed)} share(s) failed ({list(failed.values())[0][:80]}); sending them again")
    if todo:
        raise RuntimeError(f"{len(todo)} share(s) still failing after {tries} tries: pieces "
                           f"{[c for _, mine in todo for c in mine][:6]}…")
    # lipgpu writes this next to the pieces, and the two steps after the scan read it (`lipmesh --ranges`,
    # `lipmerge` defaults to NETS_DIR/ranges.json). Each worker only knows its own share, so the whole list is
    # written here -- without it the scan "succeeds" and the next step has nothing to work from.
    (nets / "ranges.json").write_text(json.dumps(cuts))
    files = len(list(nets.glob("range_*.json")))
    want = len(cuts)
    if files < want:
        raise RuntimeError(f"the cloud returned {files} of {want} range files")
    missing = [c for c in cuts if not (nets / f"range_{c[0]:g}_{c[1]:g}.json").exists()]
    if missing:                                            # the right count is not the right pieces
        raise RuntimeError(f"{len(missing)} pieces are missing, e.g. {missing[:3]}")
    log(f"{files} range files in {time.time() - t0:.0f} s (workers used {worker_s:.0f} s, {retries} retries)")
    return {"pieces": len(shares), "files": files, "seconds": round(time.time() - t0, 1),
            "worker_seconds": round(worker_s, 1), "retries": retries}


# ---------------- any step, on a rented machine ----------------
# A deployed API box can run none of the 37 pipeline steps: it has no Whisper, no models and no pipeline scripts.
# So rather than an image per step, there is one image with the whole pipeline in it, and the app sends it the
# command it was going to run anyway -- with this machine's paths swapped for placeholders the worker fills in with
# its own. The pipeline scripts are never touched, which is the rule in CLAUDE.md.
#
#   {PY} {FPY}   the pipeline's python (the worker has one python for both)
#   {SCRIPTS}    adaptation-tool/scripts        {TOOLS}   adaptation-tool
#   {SCENE}      the scene-edit scripts         {MODELS}  the Whisper / sentence models
#   {WORK}       the episode's work folder      {OUT}     the show's output folder
#   {F0} {F1} …  one big input file each (the source video, the dub audio, the font): a signed link
SMALL = 1 << 20                  # a file this size or under travels in the work tar, not on its own
# Where the worker image puts things (it is our image, so this is known, not guessed).
WORKER = {"{WORK}": "/work", "{OUT}": "/out", "{FILES}": "/files"}
# The work folder's own `config.json` records **absolute paths on this Mac** -- the source video, the dub audio,
# the models, the sheets, the big intermediates -- and many pipeline stages read file paths out of it instead of
# from the command line (dubstitch.py: `cfg["src_video"]`, `cfg.get("video_out")`, ...). So it is translated on the
# way out and translated back on the way in. Only paths change; nothing else in the file is touched.
CONFIG = "config.json"
# The edit lists the render reads. A segment the editor filled with an imported clip names that file in `video`.
EDIT_LISTS = ("manifest_groups.json", "manifest.json")


def _swap_paths(value, pairs):
    """Rewrite whole path prefixes inside a loaded JSON value. -> a new value.

    Doing this as a text replace looks simpler and is badly wrong: `/work` is a substring of `/workspaces` and
    `/out` is a substring of `/output`, so a plain replace turned `/Users/…/conductor/workspaces/…` into
    `/Users/…/conductor/<the work dir>spaces/…` and `…/TGS US_DE/output/…` into `…/TGS US_DE/<the out dir>put/…`.
    It corrupted every path in the file that merely contained those letters -- found by comparing a real run's
    config.json with the Mac's. Matching whole path segments on the parsed JSON cannot do that."""
    if isinstance(value, str):
        for a, b in sorted(pairs, key=lambda kv: len(kv[0]), reverse=True):
            a = str(a).rstrip("/")
            if a and (value == a or value.startswith(a + "/")):
                return str(b).rstrip("/") + value[len(a):]
        return value
    if isinstance(value, list):
        return [_swap_paths(v, pairs) for v in value]
    if isinstance(value, dict):
        return {k: _swap_paths(v, pairs) for k, v in value.items()}
    return value


def _swap(text, pairs):
    """`config.json` with its paths pointing at the other machine's copies of the same files."""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    return json.dumps(_swap_paths(data, pairs), ensure_ascii=False)


def tokenise(cmd, work_dir, out_dir, cfg, files=None):
    """One command line as the worker should see it. -> (cmd, {token: local path of a big file})

    Everything that is a path on this machine becomes a placeholder. What is left over and is a real file -- the
    source video, the dub audio, the subtitle font -- gets a token of its own and travels as a signed link.
    `files` is reused when several commands share one job, so the same video is always `{F0}`."""
    app_dir = str(Path(__file__).resolve().parent.parent)       # app/backend: facemap and qc import `dubstudio`
    prefixes = [("{SCRIPTS}", str(cfg.scripts)), ("{SCENE}", str(cfg.scene_dir)), ("{MODELS}", str(cfg.models_dir)),
                ("{TOOLS}", str(cfg.tools_dir)), ("{WORK}", str(work_dir)), ("{OUT}", str(out_dir)),
                ("{APP}", app_dir)]
    prefixes.sort(key=lambda kv: len(kv[1]), reverse=True)      # longest first: {SCRIPTS} is inside {TOOLS}
    exact = {str(cfg.pipeline_python): "{PY}", str(cfg.face_python): "{FPY}", "{py}": "{PY}", "{fpy}": "{FPY}"}
    out, files = [], (files if files is not None else {})
    for arg in cmd:
        if arg in exact:
            out.append(exact[arg]); continue
        arg = arg.replace("{scripts}", str(cfg.scripts))
        for token, real in prefixes:
            if real and arg.startswith(real):
                arg = token + arg[len(real):]
                break
        if arg.startswith("/") and Path(arg).is_file():          # a file somewhere else on this Mac
            token = next((t for t, p in files.items() if p == arg), None)
            if token is None:
                token = f"{{F{len(files)}}}"
                files[token] = arg
            arg = token
        out.append(arg)
    return out, files


def _send_large_work_files(cmd, work_dir, files):
    """Replace a `{WORK}/…` path with a file token when the work tar will not carry that file.

    The tar skips anything over 1 MB, and it skips the whole `input/` folder (the uploaded video, audio and
    script live there, and the big ones already travel as their own links). A script is small, so it used to
    be neither: the worker was told `/work/input/…/script.docx` and the file was not in the tar. The render
    tail does not come through here one step at a time, so a multi-GB master is not uploaded between those
    steps either. -> (cmd, files)"""
    out = []
    for arg in cmd:
        if isinstance(arg, str) and arg.startswith("{WORK}/"):
            rel = arg[len("{WORK}/"):]
            local = Path(work_dir) / rel
            left_out = rel == "input" or rel.startswith("input/")
            if local.is_file() and (local.stat().st_size > SMALL or left_out):
                token = next((t for t, p in files.items() if p == str(local)), None)
                if token is None:
                    token = f"{{F{len(files)}}}"
                    files[token] = str(local)
                arg = token
        out.append(arg)
    return out, files


def _files_named_in_config(W, O, already):
    """The big files a step needs may be named **only inside config.json**, never on the command line.
    `dubstitch.py manifest --work W` is the whole command, and `cmd_manifest` then does
    `probe_duration_ms(cfg["src_video"])`. Found the hard way: the first real cloud `manifest` died on
    `could not convert string to float: ''` -- ffprobe of a file that was never sent. -> {token: path}"""
    conf = W / CONFIG
    if not conf.exists():
        return {}
    try:
        data = json.loads(conf.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    out, seen = {}, set(already.values())
    root_w, root_o = str(W), str(O)
    for value in data.values():
        if not isinstance(value, str) or not value.startswith("/") or value in seen:
            continue
        p = Path(value)
        if not p.is_file():
            continue
        # A small file in the work folder rides in the tar, and the path is rewritten to /work.
        # The uploaded video and audio live in input/, which the tar skips, and anything over 1 MB is
        # skipped too. Leaving those as /work/… makes ffprobe read an empty file (the edit list, 2 Nov 2026).
        rel = value[len(root_w) + 1:] if value.startswith(root_w + "/") else None
        in_out = value.startswith(root_o + "/")
        if rel is not None and not (rel == "input" or rel.startswith("input/") or p.stat().st_size > SMALL):
            continue
        if in_out and p.stat().st_size <= SMALL:
            continue
        out[f"{{F{len(already) + len(out)}}}"] = str(p)
        seen.add(value)
    return out


def _used_clip_files(W, already):
    """Imported clips the editor actually placed on the timeline.

    The edit list stores each one as a segment's `video` path. A file of 1 MB or under already rides in the
    work tar. A bigger one does not, so it goes as its own link, the same way the source video does. A clip
    that was imported and never used is left here. -> {token: path}"""
    out, seen = {}, set(already.values())
    root = str(W)
    for name in EDIT_LISTS:
        path = W / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for seg in data.get("segments") or []:
            value = seg.get("video") if isinstance(seg, dict) else None
            if not isinstance(value, str) or not value.startswith("/") or value in seen:
                continue
            p = Path(value)
            if not p.is_file():
                continue
            inside = value == root or value.startswith(root + "/")
            if inside and p.stat().st_size <= SMALL:
                continue
            out[f"{{F{len(already) + len(out)}}}"] = str(p)
            seen.add(value)
    return out


def _work_patches(W, there):
    """Rewrite this machine's paths inside the files the worker reads for its inputs.

    config.json names the source video. The edit list names an imported clip. Both have to say where the
    file is on the worker, or the render looks for a path that only exists here."""
    patch = {}
    for name in (CONFIG, *EDIT_LISTS):
        if (W / name).is_file():
            patch[name] = lambda s, pairs=there: _swap(s, pairs)
    return patch


def _tar(paths, root, dest, patch=None):
    """Tar a list of paths (relative to root), rewriting the text of any file named in `patch`.
    -> how many went in."""
    import io
    n = 0
    with tarfile.open(dest, "w:gz") as t:
        for p in paths:
            full = Path(root) / p
            if not full.is_file():
                continue
            fn = (patch or {}).get(str(p))
            if fn:
                data = fn(full.read_text(encoding="utf-8")).encode()
                info = tarfile.TarInfo(str(p)); info.size = len(data)
                t.addfile(info, io.BytesIO(data))
            else:
                t.add(full, arcname=str(p))
            n += 1
    return n


def run_command(db, show, ep, name, cmd, cfg, log=print, on_progress=None, timeout=7200, preset_files=None, kind="cpu"):
    """Run one pipeline step on a rented machine. -> the worker's answer.

    The episode's small work files go up as one tar, the big inputs as signed links, and only what the step
    **changed** comes back -- so a step that writes one JSON brings back one JSON, and a render brings back the
    video. Raises RuntimeError with the step's own log when it fails, so a failure reads like a local one.
    `preset_files` keeps `{F0}` etc. assigned by a caller that already tokenised several commands (the render tail)."""
    W, O = Path(ep["work_dir"]), Path(ep["output_dir"])
    cmd_t, files = tokenise(cmd, W, O, cfg)
    if preset_files:
        files.update(preset_files)
    files.update(_files_named_in_config(W, O, files))
    # A later step can need a big file an earlier step wrote into the work folder (the 540 px preview).
    # The work tar only carries files of 1 MB or under, so that file goes as its own link, like the source video.
    cmd_t, files = _send_large_work_files(cmd_t, W, files)
    clips = _used_clip_files(W, files)
    if clips:
        files.update(clips)
        log(f"{len(clips)} imported clip(s) the editor used, sent with this job")

    job = {"step": "command", "name": name, "cmd": cmd_t, "outputs": {}}

    # how this Mac's paths read on the worker, for config.json (and back again afterwards)
    there = [(str(W), WORKER["{WORK}"]), (str(O), WORKER["{OUT}"]), (str(cfg.models_dir), "/opt/models"),
             (str(cfg.scripts), "/opt/dubstudio/scripts"), (str(cfg.tools_dir), "/opt/dubstudio")]
    for token, path in files.items():
        there.append((path, f"{WORKER['{FILES}']}/{token.strip('{}')}/{Path(path).name}"))

    # the work folder: the small files only (the big ones are either inputs, sent as links, or outputs we want back)
    small = [p.relative_to(W) for p in W.rglob("*")
             if p.is_file() and p.stat().st_size <= SMALL and not str(p.relative_to(W)).startswith("input/")]
    if small:
        tar = W.parent / f"_{name}_work_in.tar.gz"
        n = _tar(small, W, tar, patch=_work_patches(W, there) or None)
        key = storage.work_key(show, ep, f"cloud/{name}_work_in.tar.gz")
        storage.put_file(tar, key)
        tar.unlink(missing_ok=True)
        job["work_in"] = storage.presign(key, 10800)
        log(f"{n} work files sent up")
    if O.is_dir():
        osmall = [p.relative_to(O) for p in O.rglob("*") if p.is_file() and p.stat().st_size <= SMALL]
        if osmall:
            tar = W.parent / f"_{name}_out_in.tar.gz"
            _tar(osmall, O, tar)
            key = storage.work_key(show, ep, f"cloud/{name}_out_in.tar.gz")
            storage.put_file(tar, key)
            tar.unlink(missing_ok=True)
            job["out_in"] = storage.presign(key, 10800)

    # the big inputs, each as a signed link (uploaded once; an episode's video is reused by every step)
    if files:
        job["files"], job["names"] = {}, {}
        for token, path in files.items():
            key = _file_in_cloud(db, show, ep, Path(path))
            job["files"][token] = storage.presign(key, 10800)
            job["names"][token] = Path(path).name
        log(f"{len(files)} big input file(s) ready in the cloud")

    back_work = storage.work_key(show, ep, f"cloud/{name}_work_out.tar.gz")
    back_out = storage.work_key(show, ep, f"cloud/{name}_out_out.tar.gz")
    job["outputs"] = {"work": storage.presign(back_work, 10800, method="PUT"),
                      "out": storage.presign(back_out, 10800, method="PUT")}

    if ai_key_allowed():
        job["env"] = {k: v for k in ("OPENROUTER_API_KEY", "OPENROUTER_CLAUDE_MODEL", "OPENROUTER_FAST_MODEL",
                                     "OPENROUTER_BASE", "AI_PROVIDER", "AI_CACHE")
                      if (v := envfile.get(k))}
    jid = submit(job, kind=kind)
    log(f"{name}: sent to a rented {kind} machine (job {jid})")
    done, failed = wait([jid], on_progress=on_progress, timeout=timeout, kind=kind)
    if failed:
        raise RuntimeError(f"the cloud could not run {name}: {list(failed.values())[0]}")
    res = (list(done.values())[0] or {}).get("output") or {}
    if res.get("log"):
        log(res["log"])
    if not res.get("ok"):
        raise RuntimeError(f"{name} failed on the rented machine (exit {res.get('rc')}): "
                           f"{(res.get('log') or res.get('error') or '')[-300:]}")

    sent = res.get("sent") or {}
    for which, key, root in (("work", back_work, W), ("out", back_out, O)):
        if sent.get(which):
            tar = W.parent / f"_{name}_{which}_back.tar.gz"
            storage.download(key, tar)
            root.mkdir(parents=True, exist_ok=True)
            with tarfile.open(tar) as t:
                t.extractall(root)
            tar.unlink(missing_ok=True)
            # the step may have recorded new paths: read them as ours
            back_pairs = [(b, a) for a, b in there]
            for listed in (CONFIG, *EDIT_LISTS):
                back = root / listed
                if back.is_file():
                    back.write_text(_swap(back.read_text(encoding="utf-8"), back_pairs), encoding="utf-8")
    log(f"{name}: done in {res.get('seconds')} s; "
        f"{sum(v.get('files', 0) for v in sent.values())} files came back")
    return res


# The existing worker only knows one command (`step: command`). The render tail is several commands that must
# share one /work and one /out, so we send a tiny driver as the command and the real steps as a JSON list next
# to it. No image rebuild, no pipeline change: the worker runs `{PY} {WORK}/_cloud_chain.py` the same way it
# runs any other command.
CHAIN_DRIVER = '''\
import json, os, subprocess, sys
jobs = json.loads(open(os.path.join(os.path.dirname(__file__), "_cloud_chain.json"), encoding="utf-8").read())
for item in jobs["steps"]:
    name, cmd = item["name"], item["cmd"]
    print(f"=== {name} ===", flush=True)
    while True:
        p = subprocess.run(cmd)
        if p.returncode == 3:
            print(f"(resuming {name})", flush=True)
            continue
        if p.returncode != 0:
            # verify exits 1 when one picture does not match. The videos are already on this machine;
            # stopping here throws the render away and the next step never sees the clean master.
            if name in ("verify", "scene_sheet"):
                print(f"({name} exit {p.returncode}: noted, the visit continues)", flush=True)
                break
            sys.exit(p.returncode)
        break
print("CHAIN OK", flush=True)
'''

# Where the worker image puts things -- same map as handler.py PLACES. Used to write the inner commands with
# the worker's own paths so the driver does not have to expand tokens.
WORKER_PLACES = {"{PY}": "/opt/pipeline_env/bin/python", "{FPY}": "/opt/pipeline_env/bin/python",
                 "{TOOLS}": "/opt/dubstudio", "{SCRIPTS}": "/opt/dubstudio/scripts",
                 "{SCENE}": "/opt/scene/pipeline", "{MODELS}": "/opt/models",
                 "{WORK}": "/work", "{OUT}": "/out", "{APP}": "/opt/app"}


def _fill_tokens(arg, places):
    for token, real in places.items():
        if token in arg:
            arg = arg.replace(token, str(real))
    return arg


def run_chain(db, show, ep, named_cmds, cfg, log=print, on_progress=None, timeout=7200, kind="cpu",
              name="render_tail"):
    """Run several pipeline steps on ONE rented machine, in order. -> the worker's answer.

    `named_cmds` is [(name, cmd), ...] with this machine's paths, exactly as the runner would have run them
    locally. They go up as one job; only what the last step changed comes back -- which for the render visit
    is the videos. The pipeline scripts themselves are not touched. `name` is the job name (`match`, `cut`,
    `render_tail`), so two visits do not share one upload."""
    W, O = Path(ep["work_dir"]), Path(ep["output_dir"])
    files, packed = {}, []
    for step_name, cmd in named_cmds:
        cmd_t, files = tokenise(cmd, W, O, cfg, files=files)
        # Do this before {WORK} is frozen to /work. The dub audio lives in input/, which the work tar skips,
        # so a chain that only said /work/input/dub_audio/….mp3 arrived with no file (matching, 2 Nov 2026).
        cmd_t, files = _send_large_work_files(cmd_t, W, files)
        packed.append({"name": step_name, "cmd": cmd_t})
    places = dict(WORKER_PLACES)
    for token, path in files.items():
        places[token] = f"{WORKER['{FILES}']}/{token.strip('{}')}/{Path(path).name}"
    for item in packed:
        item["cmd"] = [_fill_tokens(a, places) for a in item["cmd"]]
    (W / "_cloud_chain.json").write_text(json.dumps({"steps": packed}, ensure_ascii=False), encoding="utf-8")
    (W / "_cloud_chain.py").write_text(CHAIN_DRIVER, encoding="utf-8")
    log(f"{len(packed)} steps on one rented machine: " + ", ".join(n for n, _ in named_cmds))
    return run_command(db, show, ep, name,
                       [cfg.pipeline_python if hasattr(cfg, "pipeline_python") else "{py}",
                        str(W / "_cloud_chain.py")],
                       cfg, log=log, on_progress=on_progress, timeout=timeout, preset_files=files, kind=kind)


def _file_in_cloud(db, show, ep, path):
    """A big input file in R2, uploading it once if it is not there yet. -> the key."""
    a = db.one("SELECT * FROM assets WHERE episode_id=? AND path=? AND role='input'", (ep["id"], str(path)))
    if a and a.get("r2_key") and storage.head(a["r2_key"]):
        return a["r2_key"]
    kind = (a or {}).get("kind") or "file"
    key = storage.input_key(show, ep, kind, path.name)
    if not storage.head(key):
        storage.put_big_file(path, key)
    if a:
        db.update("assets", a["id"], r2_key=key)
    return key
