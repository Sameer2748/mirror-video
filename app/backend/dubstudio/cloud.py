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
import json, re, shutil, tarfile, time, uuid, urllib.error, urllib.request
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
# asks for them (`CLOUD_STEPS` includes `all`, `tail`, `render` or `render_tail`). `trans_plan`, dynamic cards
# and the final check ride on that same CPU so Render never ffprobes masters. `qc` still needs CLOUD_AI.
ONE_MACHINE = {"render", "verify", "bookends", "trans_plan", "transitions", "subs_dub", "subs_en",
               "check", "dynsrt", "scene_sheet", "qc"}
CHAIN = ("render", "verify", "bookends", "trans_plan", "transitions", "subs_dub", "subs_en")
CHAIN_NAMES = {"all", "tail", "render", "render_tail"}
# Visit A: Whisper → matching. Decision pause stays on the website. Lip scan stays parallel from ingest.
# Visit B: sheet → library (face map is GPU, parallel after library). Then scene → fast trim on one CPU.
# Visit C: render → check on one CPU (videos never hop back to Render).
SCRIPT_CHAIN = ("mksrt_dub", "mksrt_src", "words_dub", "words_src", "sentences_src", "analyze", "merged")
MATCH_CHAIN = SCRIPT_CHAIN
CUT_CHAIN = ("sheet", "manifest", "lipsync", "groups", "library")
SCENE_CHAIN = ("scene_edit", "use_scene", "fast_trim")
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
    if not available("cpu"):
        return False
    want = steps()
    if name == "scene":
        return "scene" in want or "scene_edit" in want or "all" in want
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
    if step in SCENE_CHAIN and stretch("scene") and ai_key_allowed():
        return True
    if step in ("dynsrt", "qc", "scene_sheet", "check") and chain_wanted():
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
    named = ("all" in want or step in want or (step == "scene_edit" and "scene" in want)
             or (step == "dynsrt" and chain_wanted()))
    if not named or not capable(step):
        return False
    return available(kind or kind_for(step))


def chain_wanted(kind="cpu"):
    """The render tail as one job on one machine. Individual tail steps stay refused."""
    want = steps()
    return available(kind) and bool(want & CHAIN_NAMES)


def pieces():
    return max(1, envfile.number("CLOUD_PIECES", 8))


def holds_cpu_machine(step):
    """True when this website job is (or will be) a rented CPU box, including lip pieces.

    Lip is planned as resource `none` so it does not eat cpu_slots, but it still occupies RunPod.
    One episode must not also send Whisper/proxy/translate while that is happening."""
    if step in ("lip_gpu", "lip_mesh", "lip_merge"):
        return True
    if claimed_by_stretch(step) and kind_for(step) == "cpu":
        return True
    return bool(enabled_for(step) and kind_for(step) == "cpu")


def lip_episode_budget(cpu_slots=None, n_pieces=None):
    """How many episodes may run the 8-piece lip scan at once. 30 CPUs / 8 pieces = 3, not 10."""
    slots = int(cpu_slots if cpu_slots is not None else envfile.number("DUBSTUDIO_CPU_SLOTS", 8))
    n = int(n_pieces if n_pieces is not None else pieces())
    return max(1, slots // max(1, n))


def cpu_job_weight(step):
    """How many rented CPUs this website job will occupy. Lip is 8 pieces; everything else is 1 box."""
    return pieces() if step == "lip_gpu" else 1


def waiting_on_rented_cpu(message):
    """Website 'running' but the box is already RunPod's. Must not consume cpu_slots (P8)."""
    m = (message or "").lower()
    return "waiting for a free machine" in m or "on a machine" in m or "on one rented" in m


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


_LOGGED_JOB = re.compile(r"job ([0-9a-f]{8}-[0-9a-f-]{20,})")


def release_previous(log_path, kind="cpu", log=None):
    """Drop machine jobs this step already sent that are still queued or running.

    A restart sends the step again. The new send is the one the site watches. The job that
    was already on a machine would keep that machine and its result would never be picked up,
    so that job is cancelled before the new one is sent. A finished job is left alone.
    """
    path = Path(log_path) if log_path else None
    if path is None or not path.is_file():
        return []
    ids, seen = [], set()
    for match in _LOGGED_JOB.finditer(path.read_text(encoding="utf-8", errors="replace")):
        jid = match.group(1)
        if jid not in seen:
            seen.add(jid)
            ids.append(jid)
    released = []
    for jid in ids:
        try:
            state = (status(jid, kind=kind) or {}).get("status")
        except Exception:
            continue
        if state not in ("IN_QUEUE", "IN_PROGRESS"):
            continue
        try:
            cancel(jid, kind=kind)
        except Exception as ex:
            if log:
                log(f"could not release the previous machine ({state}): {ex}")
            continue
        released.append(jid)
    if released and log:
        log(f"released {len(released)} earlier machine job(s) for this step, so only the new one is kept")
    return released


def new_send():
    """A new attempt of a step. Its cloud files use this id, so a late upload from the
    attempt we just cancelled cannot land in the file this attempt will read."""
    global _send
    _send = uuid.uuid4().hex[:8]
    return _send


_send = ""


def step_object(name, part, send=None):
    """Cloud tar for one attempt: cloud/<step>_<part>_<send>.tar.gz."""
    token = send or _send or "send"
    return f"cloud/{name}_{part}_{token}.tar.gz"


def step_wait_message(label, info):
    """What this step is doing. Counts only this step's own jobs, never the whole pool."""
    info = info or {}
    on_machine = int(info.get("on_machine") or 0)
    waiting = int(info.get("waiting") or 0)
    if on_machine and waiting:
        return f"{label}: {on_machine} on a machine, {waiting} waiting for a free machine"
    if on_machine:
        return f"{label}: on a machine"
    return f"{label}: waiting for a free machine"


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
        on_machine = waiting = 0
        for jid in job_ids:
            if jid in done or jid in failed:
                continue
            try:
                st = status(jid, kind=kind)
            except urllib.error.HTTPError as ex:
                # A missing job will never start. Polling it for two hours looks like "on a machine".
                if getattr(ex, "code", None) == 404:
                    failed[jid] = "the machine job is gone"
                continue
            except (urllib.error.URLError, TimeoutError):
                continue
            state = st.get("status")
            if state == "COMPLETED":
                done[jid] = st
            elif state in ("FAILED", "CANCELLED", "TIMED_OUT"):
                failed[jid] = str(st.get("error") or st.get("output") or state)[:300]
            elif state == "IN_PROGRESS":
                on_machine += 1
            elif state == "IN_QUEUE":
                waiting += 1
        if on_progress:
            # This step's own jobs. The pool size (how many machines are busy for other episodes) is not this step.
            on_progress(len(done), len(job_ids), {"on_machine": on_machine, "waiting": waiting})
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
    if a.get("r2_key"):
        return a["r2_key"]
    key = storage.input_key(show, ep, kind, Path(a["path"]).name)
    src = Path(a["path"])
    if src.is_file():
        storage.put_big_file(src, key)
        db.update("assets", a["id"], r2_key=key)
        return key
    return key


def _empty_lip_pieces(nets, cuts):
    """Pieces whose range file exists but has frames: 0. Files with no `frames` key are left alone
    (tests write a stub `{window}`; a real lipgpu file always records frames). -> [[s, e], ...]
    Empty files are deleted so the next scan overwrites them."""
    empty = []
    for s, e in cuts:
        p = Path(nets) / f"range_{s:g}_{e:g}.json"
        if not p.is_file():
            continue
        try:
            d = json.loads(p.read_text())
        except ValueError:
            empty.append([s, e])
            p.unlink(missing_ok=True)
            continue
        if "frames" in d and int(d.get("frames") or 0) < 1:
            empty.append([s, e])
            p.unlink(missing_ok=True)
    return empty


def _write_silent_lip_piece(nets, s, e):
    want = max(1, int(round((float(e) - float(s)) * 25)))
    (Path(nets) / f"range_{s:g}_{e:g}.json").write_text(json.dumps({
        "idx": -1, "cls": "range", "text": f"{s:g}-{e:g}s",
        "start_s": s, "end_s": e, "window": [s, e], "frames": want,
        "scores": [-9.0] * want, "visual": [-9.0] * want,
        "tracks": [], "intervals": [], "intervals_visual": [],
        "method": {"filled": "empty-eof"},
    }))
    return want


def _keep_or_fail_empty_lip_pieces(empty, cuts, nets, duration_s, log):
    """After retries, decide what empty range files mean.

    The last make_ranges window can run past EOF (one 15 s overshoot). Fill that.

    A contiguous empty tail (TAB EP28/EP51: container longer than the pictures) is
    dropped. Those minutes are not scored as 'no lips'.

    A hole in the middle (empty piece with pictures after it) still fails — that is
    a real scan miss, not a truncated file."""
    if not empty:
        return list(cuts)
    empty_keys = {(float(s), float(e)) for s, e in empty}
    first_empty_i = None
    hole = False
    for i, c in enumerate(cuts):
        if (float(c[0]), float(c[1])) in empty_keys:
            if first_empty_i is None:
                first_empty_i = i
        elif first_empty_i is not None:
            hole = True
    start = empty[0][0]
    if hole or first_empty_i is None or first_empty_i == 0:
        raise RuntimeError(
            f"{len(empty)} piece(s) have no pictures after {start:.0f}s "
            f"(file duration {float(duration_s):.0f}s, e.g. {empty[:3]}). "
            f"The source video's pictures stop there — not a leftover empty frame. "
            f"Re-upload the source video, or check it actually plays past {start:.0f}s.")
    if first_empty_i == len(cuts) - 1:
        s, e = cuts[-1]
        want = _write_silent_lip_piece(nets, s, e)
        log(f"{s:g}-{e:g}: past the pictures; wrote {want} silent frames")
        return list(cuts)
    kept = cuts[:first_empty_i]
    log(f"{len(empty)} piece(s) have no pictures after {start:.0f}s "
        f"(file duration {float(duration_s):.0f}s). Source pictures stop there — "
        f"keeping the scan through {start:.0f}s and dropping the empty tail.")
    return kept


def lip_scan(db, show, ep, duration_s, n_pieces=None, log=print, on_progress=None, tries=3, release_log=None):
    """Scan the whole source video on rented machines and write `<work>/lips_nets` exactly as the local step would.

    The episode is cut into the pipeline's own pieces and shared between machines. A share that fails (a slow or
    reclaimed worker) is simply sent again -- the shares that finished are kept, so one bad machine costs one
    share, not the episode. -> {pieces, files, seconds, worker_seconds, retries}."""
    if release_log:
        release_previous(release_log, kind="cpu", log=log)
    send = new_send()
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
            ok = storage.work_key(show, ep, f"lips_cloud/{send}_piece_{i:02d}.tar.gz")
            jid = submit({"step": "lip_scan", "inputs": {"video": video},
                          "args": {"ranges": mine, "device": "cpu"},
                          "outputs": {"result": storage.presign(ok, 10800, method="PUT")}})
            log(f"lip piece {i}: sent to a rented cpu machine (job {jid})")
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
    # A share can succeed and still write frames:0 for one slice (TAB EP20 390-405). Merge then dies
    # on "empty lip scan". Drop those files and send just those pieces again.
    empty = _empty_lip_pieces(nets, cuts)
    extra_i = len(shares)
    for extra in range(tries):
        if not empty:
            break
        log(f"{len(empty)} piece(s) came back empty; scanning them again")
        video = storage.presign(key, 10800)
        ok = storage.work_key(show, ep, f"lips_cloud/{send}_empty_{extra_i + extra:02d}.tar.gz")
        jid = submit({"step": "lip_scan", "inputs": {"video": video},
                      "args": {"ranges": empty, "device": "cpu"},
                      "outputs": {"result": storage.presign(ok, 10800, method="PUT")}})
        log(f"lip empty pieces: sent to a rented cpu machine (job {jid})")
        done, failed = wait([jid], on_progress=on_progress)
        if failed:
            retries += 1
            log(f"empty-piece retry {extra + 1} failed: {list(failed.values())[0][:80]}")
            continue
        st = list(done.values())[0] or {}
        worker_s += st.get("executionTime", 0) / 1000
        tar = nets.parent / f"_piece_{extra_i + extra:02d}.tar.gz"
        storage.download(ok, tar)
        with tarfile.open(tar) as tf:
            tf.extractall(nets.parent / "_piece")
        for f in (nets.parent / "_piece" / "lips_nets").glob("range_*.json"):
            f.replace(nets / f.name)
        tar.unlink(missing_ok=True)
        retries += 1
        empty = _empty_lip_pieces(nets, cuts)
    if empty:
        cuts = _keep_or_fail_empty_lip_pieces(empty, cuts, nets, duration_s, log)
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
KEEP_LOCAL_MAX = 8 << 20         # JSON/xlsx may land here; videos never do
STREAM_TAR_MIN = 8 << 20         # bigger return tars are unpacked from the HTTP stream, not saved
WEBSITE_SKIP_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4a", ".wav", ".aac", ".mp3"}
SCRIPT_EXT = {".docx", ".doc", ".txt"}
GET_EXT = WEBSITE_SKIP_EXT | SCRIPT_EXT
LOOSE_WORK_FILES = (
    "proxy_540.mp4", "proxy.mp4", "dub_preview.m4a",
    "clean_noglow.mp4", "video_groups.mp4", "video_groups.auto.mp4",
)
# Segment caches the render visit writes under /work. Rebuildable, multi-GB, and the reason TAB EP53
# died with ENOSPC on this box after CHAIN OK: extractall unpacked segs_groups onto the 50 GB disk.
WORK_BACK_SKIP = ("segs/", "segs_groups/", "segs_groups.auto/", "bookends/", "pieces/", "thumbs/")
LOW_DISK = 8 * 10**9             # drop leftover caches across episodes when this much is not free
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
        # `subs_outputs` is keyed by the video path. Values used to come back as local paths
        # while the keys stayed `/out/...`; local check then reported the files missing (IMTS EP69/70).
        out = {}
        for k, v in value.items():
            nk = _swap_paths(k, pairs) if isinstance(k, str) else k
            out[nk] = _swap_paths(v, pairs)
        return out
    return value


def _point_named_files(value, named):
    """A leftover `/work/input/…` or `/files/F0/…` path becomes the file we actually sent, matched by name.

    TAB EP21/23: after the first render, config.json still said `/work/input/src_video/….mp4`. The tar
    skips `input/`, so the worker probed a missing file even though the same name was in `/files/F0`."""
    if isinstance(value, str):
        name = Path(value).name
        if name in named and (value.startswith(("/work/", "/files/", "/out/")) or not Path(value).is_file()):
            return named[name]
        return value
    if isinstance(value, list):
        return [_point_named_files(v, named) for v in value]
    if isinstance(value, dict):
        return {(_point_named_files(k, named) if isinstance(k, str) else k): _point_named_files(v, named)
                for k, v in value.items()}
    return value


def _swap(text, pairs):
    """`config.json` with its paths pointing at the other machine's copies of the same files."""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    data = _swap_paths(data, pairs)
    named = {}
    for a, b in pairs:
        b = str(b)
        if b == "/files" or b.startswith("/files/"):
            named[Path(a).name] = b
    if named:
        data = _point_named_files(data, named)
    return json.dumps(data, ensure_ascii=False)


KEEP_CONFIG_KEYS = ("translation",)
INPUT_CONFIG_KEYS = ("src_video", "dub_audio", "dub_script", "src_script")


def _keep_local_config_keys(previous, incoming_text):
    """A later visit can unpack an older config.json and wipe keys an earlier step wrote.

    TAB EP31: scene/cut packed config before the AI translation finished, then unpacked
    over `translation.out`. The English SRT file stayed; the pointer did not.

    TAB EP15: a later visit left `src_video` as `/files/F0/15.mp4`. ffprobe of that path
    on the next render is empty (`expected 3, got 1` / `float ''`)."""
    try:
        incoming = json.loads(incoming_text)
        old = json.loads(previous)
    except ValueError:
        return incoming_text
    if not isinstance(incoming, dict) or not isinstance(old, dict):
        return incoming_text
    changed = False
    for k in KEEP_CONFIG_KEYS:
        if k in old and not incoming.get(k):
            incoming[k] = old[k]
            changed = True
    for k in INPUT_CONFIG_KEYS:
        old_p = old.get(k)
        if isinstance(old_p, str) and Path(old_p).is_file() and incoming.get(k) != old_p:
            incoming[k] = old_p
            changed = True
    return json.dumps(incoming, ensure_ascii=False) if changed else incoming_text


def ensure_episode_inputs(db, show, ep, log=print):
    """Website-only: the 540px proxy still needs the source on this box. Cloud jobs must not call this
    (masters stay in R2; the worker GETs a signed link)."""
    if db is None or not isinstance(ep, dict) or ep.get("id") is None:
        return
    try:
        storage.fetch_inputs(db, show or {}, ep, kinds=list(INPUT_CONFIG_KEYS), log=log)
    except Exception as ex:
        log(f"could not fetch the source video/audio: {ex}")


def heal_config_inputs(db, ep, W):
    """If config.json still names a rented-machine path, point src_video / dub_audio at this episode's files."""
    one = getattr(db, "one", None)
    if not callable(one) or not isinstance(ep, dict) or ep.get("id") is None:
        return
    conf = Path(W) / CONFIG
    if not conf.is_file():
        return
    try:
        data = json.loads(conf.read_text(encoding="utf-8"))
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    changed = False
    for kind in INPUT_CONFIG_KEYS:
        a = one("SELECT path FROM assets WHERE episode_id=? AND kind=? AND role='input'", (ep["id"], kind))
        local = (a or {}).get("path")
        if not local:
            continue
        cur = data.get(kind)
        workerish = isinstance(cur, str) and cur.startswith(("/work/", "/files/", "/out/"))
        # Masters are not on Render. Still point config at the asset path so the worker GET
        # uses the same name as the R2 object (EP103 mksrt looked at a leftover /files/F1/…).
        if workerish or not (isinstance(cur, str) and Path(cur).is_file()):
            data[kind] = local
            changed = True
    if changed:
        conf.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# Which earlier step writes each small file (used so tests with a half graph still start).
FILE_PRODUCER = {
    "sentences_src.json": "sentences_src",
    "prep.json": "analyze",
    "config.json": "analyze",
    "lips_nets/ranges.json": "lip_gpu",
    "broll_library/broll_library.json": "library",
    "decisions.json": "decisions",
}

# Files a later step reads. Do not send / start that step until these exist (on this box or in R2).
STEP_INPUT_FILES = {
    "match": (),
    "analyze": ("sentences_src.json",),
    "merged": ("prep.json",),
    "decisions": ("prep.json",),
    "translate": (CONFIG,),
    "lips": ("lips_nets/ranges.json",),
    "lip_mesh": ("lips_nets/ranges.json",),
    "lip_merge": ("lips_nets/ranges.json",),
    "facemap": ("lips_nets/ranges.json", "broll_library/broll_library.json"),
    "cut": ("prep.json", "decisions.json"),
    "sheet": ("prep.json", "decisions.json"),
}
# Files the visit must bring back before we mark it done (so dependents do not start empty).
STEP_OUTPUT_FILES = {
    "match": ("prep.json",),
    "analyze": ("prep.json",),
    "merged": ("merged.txt",),
    "lip_gpu": ("lips_nets/ranges.json",),
    "lips": ("lips_range",),
    "library": ("broll_library/broll_library.json",),
    "scene": ("manifest_groups.json",),
}


def required_work_files(name):
    """Small files this visit cannot run without. Early steps (lip, words) may still start with no config.

    Render/dynsrt packed without config.json on IT-DE 17 after cleanup. That send must fail here, not on the worker."""
    extra = list(STEP_INPUT_FILES.get(name) or ())
    if name in CHAIN or name in CHAIN_NAMES or name in ("check", "dynsrt", "translate"):
        need = [CONFIG]
        if name in CHAIN or name in CHAIN_NAMES or name == "check":
            need.append("manifest_groups.json")
        for n in extra:
            if n not in need:
                need.append(n)
        return need
    return extra


def produced_work_files(name):
    """Small files this visit must leave in the work folder before dependents may start."""
    return list(STEP_OUTPUT_FILES.get(name) or ())


def ensure_work_for_cloud(show, ep, name, log=print):
    """Fetch small work from R2 (loose files, then a previous tar). Refuse to send if still missing.

    Do not fetch masters onto this box. Do not Process again — Retry this step after the files exist."""
    W = Path(ep["work_dir"])
    need = required_work_files(name)
    missing = [n for n in need if not (W / n).is_file()]
    if missing or not (W / CONFIG).is_file():
        try:
            storage.fetch_work_files(show, ep, log=log, force=bool(missing or not (W / CONFIG).is_file()))
        except Exception as ex:
            log(f"could not fetch work files: {ex}")
    missing = [n for n in need if not (W / n).is_file()]
    if missing:
        raise RuntimeError(
            f"cannot send {name}: missing {', '.join(missing)} (not on this box and not in the cloud work files). "
            "Retry this step after those files are in R2 — do not Process again")


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
        if arg.startswith("/") and (Path(arg).is_file() or Path(arg).suffix.lower() in GET_EXT):
            # Missing locally: previous visit PUT it to R2. Tokenise so the worker GETs it.
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
            media = Path(rel).suffix.lower() in GET_EXT
            # input/ and previous-visit videos are not in the work tar. Tokenise even when this box has no copy.
            if left_out or media or (local.is_file() and local.stat().st_size > SMALL):
                token = next((t for t, p in files.items() if p == str(local)), None)
                if token is None:
                    token = f"{{F{len(files)}}}"
                    files[token] = str(local)
                arg = token
        out.append(arg)
    return out, files


def _resolve_media_path(W, O, value):
    """A path from config.json / the edit list, including leftover worker paths, as a file on this box."""
    if not isinstance(value, str) or not value.startswith("/"):
        return None
    p = Path(value)
    if p.is_file():
        return p
    if value == "/work" or value.startswith("/work/"):
        q = Path(W) / value[len("/work"):].lstrip("/")
        if q.is_file():
            return q
    if value == "/out" or value.startswith("/out/"):
        q = Path(O) / value[len("/out"):].lstrip("/")
        if q.is_file():
            return q
    if value.startswith("/files/"):
        name = Path(value).name
        for folder in (Path(W) / "input", Path(W)):
            if not folder.is_dir():
                continue
            hits = [h for h in folder.rglob(name) if h.is_file()]
            if hits:
                return hits[0]
    return None


def _walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


def _files_named_in_config(W, O, already):
    """The big files a step needs may be named **only inside config.json**, never on the command line.
    `dubstitch.py manifest --work W` is the whole command, and `cmd_manifest` then does
    `probe_duration_ms(cfg["src_video"])`. Found the hard way: the first real cloud `manifest` died on
    `could not convert string to float: ''` -- ffprobe of a file that was never sent. -> {token: path}

    A path that is not on this box still goes as a token: the worker GETs it from R2 (previous visit)."""
    conf = W / CONFIG
    if not conf.exists():
        return {}
    try:
        data = json.loads(conf.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    out, seen = {}, set(already.values())
    root_w, root_o = str(W), str(O)
    for value in _walk_strings(data):
        if not isinstance(value, str) or not value.startswith("/") or value in seen:
            continue
        p = _resolve_media_path(W, O, value)
        if p is None:
            if Path(value).suffix.lower() not in GET_EXT:
                continue
            if value.startswith("/work/"):
                p = Path(W) / value[len("/work/"):]
            elif value.startswith("/out/"):
                p = Path(O) / value[len("/out/"):]
            else:
                p = Path(value)
        value = str(p)
        if value in seen:
            continue
        try:
            size = p.stat().st_size if p.is_file() else SMALL + 1
        except OSError:
            size = SMALL + 1
        rel = value[len(root_w) + 1:] if value.startswith(root_w + "/") else None
        in_out = value.startswith(root_o + "/") or value.startswith("/out/")
        if in_out:
            if not p.is_file() or size <= SMALL:
                continue
        elif rel is not None and not (rel == "input" or rel.startswith("input/") or size > SMALL):
            continue
        elif not p.is_file() and Path(value).suffix.lower() not in GET_EXT:
            continue
        out[f"{{F{len(already) + len(out)}}}"] = value
        seen.add(value)
    return out


def _fold_name(name):
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def _localise_cmd_inputs(cmd, db, ep):
    """A leftover `/files/F1/….docx` from a previous send becomes this episode's script/video path.

    Matching and lip opening both died on python-docx opening that worker path with no GET (WBT EP101)."""
    one_q = getattr(db, "q", None) if db is not None else None
    if not callable(one_q) or not isinstance(ep, dict) or ep.get("id") is None:
        return list(cmd or [])
    try:
        rows = one_q("SELECT path FROM assets WHERE episode_id=? AND role='input'", (ep["id"],))
    except Exception:
        return list(cmd or [])
    by_fold = {}
    for a in rows or []:
        p = a.get("path") or ""
        if p:
            by_fold[_fold_name(Path(p).name)] = p
    out = []
    for c in cmd or []:
        if isinstance(c, str) and c.startswith("/files/") and Path(c).suffix.lower() in GET_EXT:
            hit = by_fold.get(_fold_name(Path(c).name))
            out.append(hit or c)
        else:
            out.append(c)
    return out


def _input_asset_files(db, ep, already):
    """Point the job at src_video / dub_audio. Local file if this box still has it; otherwise the
    asset path is enough for `_file_in_cloud` to presign the R2 object (worker pulls; Render does not)."""
    out, seen = {}, set(already.values())
    q = getattr(db, "q", None) if db is not None else None
    if not callable(q) or not isinstance(ep, dict) or ep.get("id") is None:
        return out
    try:
        rows = q("SELECT path, r2_key FROM assets WHERE episode_id=? AND kind IN "
                 "('src_video','dub_audio','dub_script','src_script') AND role='input'",
                 (ep["id"],))
    except Exception:
        return out
    for a in rows or []:
        p = Path(a.get("path") or "")
        key = str(p) if str(p) not in ("", ".") else (a.get("r2_key") or "")
        if not key or key in seen:
            continue
        # input/ is never in the work tar. A 40 KB .docx used to be skipped as "small" and the worker
        # opened /files/F1/….docx with nothing there (WBT lip opening / matching).
        in_input = "input" in p.parts or Path(key).suffix.lower() in SCRIPT_EXT
        if p.is_file() and p.stat().st_size <= SMALL and not in_input:
            continue
        out[f"{{F{len(already) + len(out)}}}"] = key
        seen.add(key)
    return out


def _media_place_list(files, W):
    """Where the rented machine must put each big file, including `/work/input/…` if that is what config.json says."""
    place, W = [], Path(W)
    for token, path in files.items():
        p = Path(path)
        dests = [f"{WORKER['{FILES}']}/{token.strip('{}')}/{p.name}"]
        try:
            dests.append("/work/" + p.resolve().relative_to(W.resolve()).as_posix())
        except ValueError:
            if "input" in p.parts:
                dests.append("/work/" + "/".join(p.parts[p.parts.index("input"):]))
        place.append({"name": p.name, "dests": dests})
    return place


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
            p = _resolve_media_path(W, W, value)
            if p is None:
                continue
            value = str(p)
            if value in seen:
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


def stage_current_app(work):
    """Copy this process's `dubstudio` (ai.py + library.py) into W/_app.

    The baked worker image has an older `/opt/app`. library.py's `_app_dir` is parents[2] of the script, so
    `{WORK}/_app/dubstudio/pipeline/library.py` imports THIS closer / cache lock, not the image's. -> the script path."""
    pkg = Path(__file__).resolve().parent
    dest = Path(work) / "_app" / "dubstudio"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in ("__init__.py", "ai.py"):
        src = pkg / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    pipe = dest / "pipeline"
    pipe.mkdir()
    src_pipe = pkg / "pipeline"
    for name in ("__init__.py", "library.py"):
        src = src_pipe / name
        if src.is_file():
            shutil.copy2(src, pipe / name)
    return pipe / "library.py"


def stage_lipmesh(work):
    """Copy this image's lipmesh.py into W/_pipe. The rented image still crashes on an empty ffmpeg slice."""
    src = Path(__file__).resolve().parent / "pipeline" / "lipmesh.py"
    dest_dir = Path(work) / "_pipe"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "lipmesh.py"
    shutil.copy2(src, dest)
    return dest


def stage_lipmerge(work):
    """Copy this image's lipmerge.py into W/_pipe. The rented image still drops a 1-frame gap."""
    src = Path(__file__).resolve().parent / "pipeline" / "lipmerge.py"
    dest_dir = Path(work) / "_pipe"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "lipmerge.py"
    shutil.copy2(src, dest)
    return dest


def point_lipmerge_at_staged(cmd, work):
    """Rewrite a lip_merge command so the worker runs the copy we just staged under W/_pipe."""
    script = stage_lipmerge(work)
    out = []
    for c in cmd or []:
        if str(c).endswith("lipmerge.py"):
            out.append(str(script))
        else:
            out.append(c)
    return out


def stage_scene(work):
    """Copy the scene edit this box is running into W/_app, plus the grouping helper and dubstitch it imports.

    The rented image would otherwise run an older scene edit. The script looks for dubstudio next to itself
    and for dubstitch under pipeline_tools two folders up. -> the slowfix_edit.py path."""
    pkg = Path(__file__).resolve().parent
    app = Path(work) / "_app"
    dest = app / "dubstudio"
    dest.mkdir(parents=True, exist_ok=True)
    init = pkg / "__init__.py"
    if init.is_file():
        shutil.copy2(init, dest / "__init__.py")
    shutil.copy2(pkg / "ai.py", dest / "ai.py")
    pipe = dest / "pipeline"
    pipe.mkdir(parents=True, exist_ok=True)
    pinit = pkg / "pipeline" / "__init__.py"
    if pinit.is_file():
        shutil.copy2(pinit, pipe / "__init__.py")
    for name in ("slowfix_edit.py", "keep_grouping.py", "apply_scene.py"):
        src = pkg / "pipeline" / name
        if src.is_file():
            shutil.copy2(src, pipe / name)
    timeline = pkg / "timeline.py"
    if timeline.is_file():
        shutil.copy2(timeline, dest / "timeline.py")
    stage_dubstitch(work)
    tools = app / "pipeline_tools"
    tools.mkdir(parents=True, exist_ok=True)
    for name in ("dubstitch.py", "pngsubs.py", "dynsubs.py"):
        src = Path(work) / "_pipe" / name
        if src.is_file():
            shutil.copy2(src, tools / name)
    return pipe / "slowfix_edit.py"


def point_lipmesh_at_staged(cmd, work):
    """Rewrite a lip_mesh command so the worker runs the copy we just staged under W/_pipe."""
    script = stage_lipmesh(work)
    out = []
    for c in cmd or []:
        if str(c).endswith("lipmesh.py"):
            out.append(str(script))
        else:
            out.append(c)
    return out


def point_library_at_staged_app(cmd, work):
    """Rewrite a library command so the worker runs the copy we just staged under W/_app."""
    script = stage_current_app(work)
    out = []
    for c in cmd or []:
        if str(c).endswith("library.py"):
            out.append(str(script))
        else:
            out.append(c)
    return out


def stage_dubstitch(work):
    """Copy this image's dubstitch.py and the modules it imports from the same folder (pngsubs, dynsubs).

    The rented image concatenates leftover .ok files without the mp4. Staging only dubstitch.py made
    `import pngsubs` look in /work/_pipe and miss the worker's /opt/dubstudio/scripts copy (TAB EP20)."""
    src_dir = Path(__file__).resolve().parents[1] / "pipeline_tools"
    dest_dir = Path(work) / "_pipe"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "dubstitch.py"
    shutil.copy2(src_dir / "dubstitch.py", dest)
    for name in ("pngsubs.py", "dynsubs.py"):
        src = src_dir / name
        if src.is_file():
            shutil.copy2(src, dest_dir / name)
    return dest


def point_dubstitch_at_staged(cmd, work):
    """Rewrite a command so the worker runs the copy we just staged under W/_pipe."""
    script = stage_dubstitch(work)
    out = []
    for c in cmd or []:
        if str(c).endswith("dubstitch.py"):
            out.append(str(script))
        else:
            out.append(c)
    return out


def stage_mksrt(work):
    """Copy this image's mksrt.py and srtclean.py into W/_pipe.

    The rented image still pastes a "Word Count: N" header, and a lowercase
    [ambience voice] note, onto the dub SRT. Both files travel with the job:
    mksrt imports srtclean from its own folder, and dubstitch from the scripts
    folder the worker starts in."""
    here = Path(__file__).resolve()
    src_dir = None
    candidates = [here.parents[1] / "pipeline_tools"]
    if len(here.parents) > 3:
        candidates.append(here.parents[3] / "pipeline_tools")
    for folder in candidates:
        if (folder / "mksrt.py").is_file() and (folder / "srtclean.py").is_file():
            src_dir = folder
            break
    if src_dir is None:
        raise FileNotFoundError("mksrt.py")
    dest_dir = Path(work) / "_pipe"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in ("mksrt.py", "srtclean.py"):
        shutil.copy2(src_dir / name, dest_dir / name)
    return dest_dir / "mksrt.py"


def point_mksrt_at_staged(cmd, work):
    """Rewrite an mksrt command so the worker runs the copy we just staged under W/_pipe."""
    script = stage_mksrt(work)
    out = []
    for c in cmd or []:
        if str(c).endswith("mksrt.py"):
            out.append(str(script))
        else:
            out.append(c)
    return out


def stage_sentences(work):
    """Copy claude_sentences.py and ai.py into W/_app so matching does not use /opt/scene/pipeline.

    The rented image has no claude_sentences.py there (WBT 101–109: exit 2, can't open file).
    parents[2] of the staged script is W/_app, which has dubstudio/ai.py."""
    pkg = Path(__file__).resolve().parent
    dest = Path(work) / "_app" / "dubstudio"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "ai.py"):
        src = pkg / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    pipe = dest / "pipeline"
    pipe.mkdir(parents=True, exist_ok=True)
    pinit = pkg / "pipeline" / "__init__.py"
    if pinit.is_file():
        shutil.copy2(pinit, pipe / "__init__.py")
    src = pkg / "pipeline" / "claude_sentences.py"
    if not src.is_file():
        raise FileNotFoundError("claude_sentences.py")
    shutil.copy2(src, pipe / "claude_sentences.py")
    return pipe / "claude_sentences.py"


def point_sentences_at_staged(cmd, work):
    """Rewrite a sentences_src command so the worker runs the copy under W/_app."""
    script = stage_sentences(work)
    out = []
    for c in cmd or []:
        if str(c).endswith("claude_sentences.py"):
            out.append(str(script))
        else:
            out.append(c)
    return out


def stage_facemap(work):
    """Copy this image's facemap.py into W/_pipe.

    The rented GPU image dies when Claude names a group that is not a ≥4-face cluster (TAB EP48 KeyError 83).
    Staging this copy means a Retry uses the skip, without rebuilding `:face-gpu`."""
    here = Path(__file__).resolve()
    src = None
    for p in (here.parents[1] / "pipeline_tools" / "facemap.py",
              here.parents[3] / "pipeline_tools" / "facemap.py" if len(here.parents) > 3 else None):
        if p is not None and p.is_file():
            src = p
            break
    if src is None:
        raise FileNotFoundError("facemap.py")
    dest_dir = Path(work) / "_pipe"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "facemap.py"
    shutil.copy2(src, dest)
    story = Path(__file__).resolve().parent / "pipeline" / "storynames.py"
    if story.is_file():
        shutil.copy2(story, dest_dir / "storynames.py")
    return dest


def point_facemap_at_staged(cmd, work):
    """Rewrite a facemap command so the worker runs the copy we just staged under W/_pipe."""
    script = stage_facemap(work)
    out = []
    for c in cmd or []:
        if str(c).endswith("facemap.py"):
            out.append(str(script))
        else:
            out.append(c)
    return out


def _scratch_tar(work, ep, name, part):
    """Local tar used while talking to R2. W.parent is the shared library/work folder, so the name must include
    the episode: eleven render tails used to write `_render_tail_work_in.tar.gz` at once, and the PUT
    (Content-Length of one file, body of another) failed with Too much/little data."""
    return Path(work).parent / f"_{ep['id']}_{name}_{part}.tar.gz"


def skip_work_back(name):
    """True when this member of the work return tar is a throwaway render cache."""
    name = (name or "").replace("\\", "/").lstrip("./")
    return any(name == p.rstrip("/") or name.startswith(p) for p in WORK_BACK_SKIP)


def keep_returned_on_website(name, size=0, kind="work"):
    """Small JSON/SRT/xlsx stay on this box. Videos stay in R2 (worker PUT / stream)."""
    name = (name or "").replace("\\", "/").lstrip("./")
    if kind == "work" and skip_work_back(name):
        return False
    if Path(name).suffix.lower() in WEBSITE_SKIP_EXT:
        return False
    if size and size > KEEP_LOCAL_MAX:
        return False
    return True


def _put_returned_large(show, ep, kind, name, src, size):
    """Park a video the worker produced in R2 without writing it under library/."""
    if not show or not ep or not src:
        _discard(src)
        return None
    name = (name or "").replace("\\", "/").lstrip("./")
    if kind == "out":
        key = storage.out_key(show, ep, Path(name).name)
    else:
        key = storage.work_key(show, ep, name)
    try:
        if size and size <= KEEP_LOCAL_MAX:
            data = src.read()
            tmp_parent = Path(ep.get("work_dir") or ".")
            tmp = tmp_parent.parent / f"_{ep.get('id')}_loose_{Path(name).name}"
            tmp.write_bytes(data)
            try:
                storage.put_file(tmp, key)
            finally:
                tmp.unlink(missing_ok=True)
        else:
            storage.put_stream(key, src, int(size or 0), ctype="application/octet-stream")
        return key
    except Exception:
        _discard(src)
        raise


def _discard(src):
    if src is None:
        return
    try:
        while src.read(8 << 20):
            pass
    except Exception:
        pass


def unpack_returned_tar(tar_path, root, kind="work", show=None, ep=None, stream=None):
    """Write the rented machine's small files onto this box. -> (wrote, skipped).

    Videos are not written here: they are PUT to R2 (by the worker, or streamed from this tar).
    Work segment caches stay off this disk (TAB EP53: extractall of segs_groups after CHAIN OK)."""
    wrote = skipped = 0
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    base = root.resolve()

    def _one(m, src):
        nonlocal wrote, skipped
        name = (m.name or "").replace("\\", "/").lstrip("./")
        if not name or name.endswith("/") or not getattr(m, "isreg", lambda: False)():
            return
        size = int(getattr(m, "size", 0) or 0)
        if kind == "work" and skip_work_back(name):
            skipped += 1
            _discard(src)
            return
        if not keep_returned_on_website(name, size, kind=kind):
            skipped += 1
            if Path(name).suffix.lower() in WEBSITE_SKIP_EXT or (size and size > KEEP_LOCAL_MAX):
                try:
                    _put_returned_large(show, ep, kind, name, src, size)
                except Exception:
                    _discard(src)
            else:
                _discard(src)
            return
        dest = (root / name).resolve()
        try:
            dest.relative_to(base)
        except ValueError:
            _discard(src)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src is None:
            return
        with open(dest, "wb") as f:
            shutil.copyfileobj(src, f)
        wrote += 1

    if stream is not None:
        with tarfile.open(fileobj=stream, mode="r|gz") as t:
            for m in t:
                if not m.isreg():
                    continue
                src = t.extractfile(m)
                _one(m, src)
        return wrote, skipped
    with tarfile.open(tar_path) as t:
        for m in t.getmembers():
            if not m.isreg():
                continue
            src = t.extractfile(m)
            if src is None:
                continue
            with src:
                _one(m, src)
    return wrote, skipped


def ingest_returned_tar(key, tar_path, root, kind, show=None, ep=None, log=print, size_hint=0):
    """Bring a worker return tar onto this box: stream when it is large so Render never stores the videos.

    Size comes from the worker's `sent.bytes`. Do not HEAD R2 here — a missing key waits 30s and
    hangs tests / the tick when the env has real credentials."""
    size = int(size_hint or 0)
    if size >= STREAM_TAR_MIN:
        client = ctx = resp = None
        try:
            client, ctx, resp = storage.object_stream(key)
            wrote, skipped = unpack_returned_tar(
                None, root, kind=kind, show=show, ep=ep, stream=storage._HttpFile(resp))
        finally:
            if ctx is not None:
                ctx.__exit__(None, None, None)
            if client is not None:
                client.close()
        return wrote, skipped
    storage.download(key, tar_path)
    try:
        return unpack_returned_tar(tar_path, root, kind=kind, show=show, ep=ep)
    finally:
        Path(tar_path).unlink(missing_ok=True)
        for leftover in Path(tar_path).parent.glob(Path(tar_path).name + "*.part"):
            leftover.unlink(missing_ok=True)


def _maybe_free_disk(work, log=print, db=None, keep_episode_id=None):
    """When this box is tight, drop leftover caches, leftover tars, and published videos already in R2."""
    W = Path(work)
    parent = W.parent if W.parent.is_dir() else W
    try:
        free = shutil.disk_usage(parent).free
    except OSError:
        return
    if free >= LOW_DISK:
        return
    log(f"disk has {free / 1e9:.1f} GB free; making room")
    storage.free_disk(W, log=log, db=db, keep_episode_id=keep_episode_id)


def _queue_returned_outputs(db, show, ep, log=print):
    """The /out files of this step go to R2 now, not at the publish step after the whole episode."""
    if not (db and show and show.get("profile") and storage.configured()
            and storage.settings().get("auto_upload")):
        return
    from . import deliver
    files = deliver.deliverables(show, ep, None)
    if not any(f.get("exists") for f in files):
        return
    rows = storage.queue_uploads(db, show, ep, files, user_id=show.get("owner_id"))
    log(f"{len(rows)} output file(s) queued for the cloud (this step, not waiting for publish)")


def _transient_cloud_error(msg):
    """A rented machine died on a 5xx while fetching or putting a signed file. Safe to send the same job once more."""
    s = str(msg).lower()
    return any(x in s for x in (
        "http error 500", "http error 502", "http error 503", "http error 504",
        "internal server error", "bad gateway", "gateway timeout", "service unavailable",
        "connection reset", "remote disconnected", "timed out",
    ))


def _loose_output_puts(show, ep):
    """Signed PUTs so the worker uploads videos itself. Unknown names still ride in the tar."""
    work, out = {}, {}
    if not show or not ep:
        return work, out
    for rel in LOOSE_WORK_FILES:
        work[rel] = storage.presign(storage.work_key(show, ep, rel), 10800, method="PUT")
    try:
        from . import presets
        prof = (show or {}).get("profile") or {}
        if prof.get("file_prefix") and prof.get("labels"):
            for name in presets.names(show["code"], ep["number"], prof).values():
                if name:
                    out[Path(name).name] = storage.presign(
                        storage.out_key(show, ep, Path(name).name), 10800, method="PUT")
    except Exception:
        pass
    return work, out


def run_command(db, show, ep, name, cmd, cfg, log=print, on_progress=None, timeout=7200, preset_files=None, kind="cpu",
                release_log=None, retry_wait=60):
    """Run one pipeline step on a rented machine. -> the worker's answer.

    The episode's small work files go up as one tar, the big inputs as signed GET links. The worker GETs
    those and PUTs videos to signed keys; this box only unpacks JSON/SRT. Raises RuntimeError with the
    step's own log when it fails, so a failure reads like a local one.
    `preset_files` keeps `{F0}` etc. assigned by a caller that already tokenised several commands (the render tail)."""
    if release_log:
        release_previous(release_log, kind=kind, log=log)
    send = new_send()
    W, O = Path(ep["work_dir"]), Path(ep["output_dir"])
    _maybe_free_disk(W, log, db=db, keep_episode_id=ep.get("id"))
    # Small JSON only. Do not fetch src/audio onto Render (IMTS IT-DE 23/35 dynsrt, docs/17 C6).
    ensure_work_for_cloud(show, ep, name, log=log)
    heal_config_inputs(db, ep, W)
    cmd = _localise_cmd_inputs(cmd, db, ep)
    if name == "library":
        cmd = point_library_at_staged_app(cmd, W)
        log("library: this box's ai.py/library.py travel with the job (the rented image is older)")
    if name == "lip_mesh":
        cmd = point_lipmesh_at_staged(cmd, W)
        log("lip_mesh: this box's lipmesh.py travels with the job (the rented image still crashes on an empty slice)")
    if name == "facemap" or any(str(c).endswith("facemap.py") for c in (cmd or [])):
        cmd = point_facemap_at_staged(cmd, W)
        log("facemap: this box's facemap.py travels with the job (the rented image dies when Claude names a missing group)")
    if any(str(c).endswith("lipmerge.py") for c in (cmd or [])):
        cmd = point_lipmerge_at_staged(cmd, W)
        log("lip merge: this box's lipmerge.py travels with the job (the rented image drops a 1-frame gap)")
    if any(str(c).endswith("dubstitch.py") for c in (cmd or [])):
        cmd = point_dubstitch_at_staged(cmd, W)
        log("this box's dubstitch.py travels with the job (the rented image concatenates leftover .ok files without the mp4)")
    if any(str(c).endswith("mksrt.py") for c in (cmd or [])):
        cmd = point_mksrt_at_staged(cmd, W)
        log("mksrt: this box's mksrt.py and srtclean.py travel with the job "
            "(the rented image pastes Word Count and ambience notes onto the dub SRT)")
    if any(str(c).endswith("claude_sentences.py") for c in (cmd or [])):
        cmd = point_sentences_at_staged(cmd, W)
        log("sentences: this box's claude_sentences.py travels with the job "
            "(the rented image has no /opt/scene/pipeline copy)")
    cmd_t, files = tokenise(cmd, W, O, cfg)
    if preset_files:
        files.update(preset_files)
    files.update(_files_named_in_config(W, O, files))
    files.update(_input_asset_files(db, ep, files))
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

    work_in_key = out_in_key = None
    # the work folder: the small files only (the big ones are either inputs, sent as links, or outputs we want back)
    small = [p.relative_to(W) for p in W.rglob("*")
             if p.is_file() and p.stat().st_size <= SMALL and not str(p.relative_to(W)).startswith("input/")]
    if small:
        tar = _scratch_tar(W, ep, name, "work_in")
        try:
            n = _tar(small, W, tar, patch=_work_patches(W, there) or None)
        except OSError as e:
            if e.errno != 28:
                raise
            log("disk full packing the work tar; making room")
            tar.unlink(missing_ok=True)
            _maybe_free_disk(W, log, db=db, keep_episode_id=ep.get("id"))
            n = _tar(small, W, tar, patch=_work_patches(W, there) or None)
        work_in_key = storage.work_key(show, ep, step_object(name, "work_in", send))
        storage.put_file(tar, work_in_key)
        tar.unlink(missing_ok=True)
        job["work_in"] = storage.presign(work_in_key, 10800)
        log(f"{n} work files sent up")
    if O.is_dir():
        osmall = [p.relative_to(O) for p in O.rglob("*") if p.is_file() and p.stat().st_size <= SMALL]
        if osmall:
            tar = _scratch_tar(W, ep, name, "out_in")
            _tar(osmall, O, tar)
            out_in_key = storage.work_key(show, ep, step_object(name, "out_in", send))
            storage.put_file(tar, out_in_key)
            tar.unlink(missing_ok=True)
            job["out_in"] = storage.presign(out_in_key, 10800)

    # the big inputs, each as a signed link (uploaded once; an episode's video is reused by every step)
    file_keys = {}
    if files:
        job["files"], job["names"] = {}, {}
        for token, path in files.items():
            r2k = _file_in_cloud(db, show, ep, Path(path))
            file_keys[token] = r2k
            job["files"][token] = storage.presign(r2k, 10800)
            job["names"][token] = Path(path).name
        job["place"] = _media_place_list(files, W)
        for item, token in zip(job["place"], files):
            item["url"] = job["files"][token]
        log(f"{len(files)} big input file(s) ready in the cloud")

    back_work = storage.work_key(show, ep, step_object(name, "work_out", send))
    back_out = storage.work_key(show, ep, step_object(name, "out_out", send))
    loose_work, loose_out = _loose_output_puts(show, ep)
    job["outputs"] = {"work": storage.presign(back_work, 10800, method="PUT"),
                      "out": storage.presign(back_out, 10800, method="PUT"),
                      "loose": {"work": loose_work, "out": loose_out}}

    if ai_key_allowed():
        job["env"] = {k: v for k in ("OPENROUTER_API_KEY", "OPENROUTER_CLAUDE_MODEL", "OPENROUTER_FAST_MODEL",
                                     "OPENROUTER_BASE", "AI_PROVIDER", "AI_CACHE")
                      if (v := envfile.get(k))}

    def _resign():
        if work_in_key:
            job["work_in"] = storage.presign(work_in_key, 10800)
        if out_in_key:
            job["out_in"] = storage.presign(out_in_key, 10800)
        for token, r2k in file_keys.items():
            job["files"][token] = storage.presign(r2k, 10800)
        loose_work, loose_out = _loose_output_puts(show, ep)
        job["outputs"] = {"work": storage.presign(back_work, 10800, method="PUT"),
                          "out": storage.presign(back_out, 10800, method="PUT"),
                          "loose": {"work": loose_work, "out": loose_out}}

    jid = submit(job, kind=kind)
    log(f"{name}: sent to a rented {kind} machine (job {jid})")
    done, failed = wait([jid], on_progress=on_progress, timeout=timeout, kind=kind)
    if failed and _transient_cloud_error(list(failed.values())[0]):
        log(f"{name}: rented machine hit a temporary error; waiting {retry_wait:.0f} s and sending the same job once more")
        time.sleep(retry_wait)
        _resign()
        jid = submit(job, kind=kind)
        log(f"{name}: retry sent to a rented {kind} machine (job {jid})")
        done, failed = wait([jid], on_progress=on_progress, timeout=timeout, kind=kind)
    if failed:
        raise RuntimeError(f"the cloud could not run {name}: {list(failed.values())[0]}")
    res = (list(done.values())[0] or {}).get("output") or {}
    if res.get("log"):
        log(res["log"])

    sent = res.get("sent") or {}
    _maybe_free_disk(W, log, db=db, keep_episode_id=ep.get("id"))
    prev_cfg = (W / CONFIG).read_text(encoding="utf-8") if (W / CONFIG).is_file() else None
    # /out first. Do not download videos onto this box (P1). Small JSON/SRT only.
    # Still ingest when the visit failed: TAB EP31 check died after /out was already PUT.
    loose = sent.get("loose") or {}
    if isinstance(loose, dict) and (loose.get("out") or loose.get("work")):
        log("worker uploaded output videos to the cloud (not downloaded here)")
    for which, key, root in (("out", back_out, O), ("work", back_work, W)):
        if not sent.get(which):
            continue
        tar = _scratch_tar(W, ep, name, f"{which}_back")
        hint = (sent.get(which) or {}).get("bytes") if isinstance(sent.get(which), dict) else 0
        try:
            wrote, skipped = ingest_returned_tar(
                key, tar, root, which, show=show, ep=ep, log=log, size_hint=hint)
        except FileNotFoundError:
            log("the download file vanished; fetching the results again")
            wrote, skipped = ingest_returned_tar(
                key, tar, root, which, show=show, ep=ep, log=log, size_hint=hint)
        except OSError as e:
            if e.errno != 28:
                raise
            log("disk full while unpacking; making room and retrying")
            _maybe_free_disk(W, log, db=db, keep_episode_id=ep.get("id"))
            wrote, skipped = ingest_returned_tar(
                key, tar, root, which, show=show, ep=ep, log=log, size_hint=hint)
        if skipped:
            log(f"{which}: kept {wrote} small files, left {skipped} videos/caches in the cloud")
        if which == "out":
            try:
                _queue_returned_outputs(db, show, ep, log)
            except Exception as e:
                log(f"outputs not queued yet: {e}")
        else:
            storage.drop_render_scratch(root)
            back_pairs = [(b, a) for a, b in there]
            for listed in (CONFIG, *EDIT_LISTS):
                back = root / listed
                if back.is_file():
                    swapped = _swap(back.read_text(encoding="utf-8"), back_pairs)
                    if listed == CONFIG and prev_cfg:
                        swapped = _keep_local_config_keys(prev_cfg, swapped)
                    back.write_text(swapped, encoding="utf-8")
            try:
                storage.publish_outputs(db, show, ep, [], work_dir=str(root), log=log)
            except Exception as e:
                log(f"work files not copied to the cloud yet: {e}")
    if not res.get("ok"):
        raise RuntimeError(f"{name} failed on the rented machine (exit {res.get('rc')}): "
                           f"{(res.get('log') or res.get('error') or '')[-300:]}")
    log(f"{name}: done in {res.get('seconds')} s; "
        f"{sum(v.get('files', 0) for v in sent.values())} files came back")
    return res


# The existing worker only knows one command (`step: command`). The render tail is several commands that must
# share one /work and one /out, so we send a tiny driver as the command and the real steps as a JSON list next
# to it. No image rebuild, no pipeline change: the worker runs `{PY} {WORK}/_cloud_chain.py` the same way it
# runs any other command.
CHAIN_DRIVER = '''\
import json, os, shutil, subprocess, sys, urllib.request
here = os.path.dirname(os.path.abspath(__file__))
jobs = json.loads(open(os.path.join(here, "_cloud_chain.json"), encoding="utf-8").read())
by_name = {}

def remember_files():
    if os.path.isdir("/files"):
        for root, _, names in os.walk("/files"):
            for n in names:
                by_name[n] = os.path.join(root, n)
    if os.path.isdir("/work"):
        for root, _, names in os.walk("/work"):
            for n in names:
                by_name.setdefault(n, os.path.join(root, n))

def geturl(url, dest):
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    print("media: downloading %s -> %s" % (os.path.basename(dest), dest), flush=True)
    with urllib.request.urlopen(url, timeout=1800) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 8 << 20)
    by_name[os.path.basename(dest)] = dest
    return dest

def pull_place():
    remember_files()
    for item in jobs.get("place") or []:
        name = item.get("name")
        src = by_name.get(name)
        url = item.get("url")
        for dest in item.get("dests") or []:
            if os.path.isfile(dest) and os.path.getsize(dest) > 64:
                by_name[name] = dest
                continue
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            if src and os.path.isfile(src):
                if not os.path.exists(dest):
                    try:
                        os.symlink(src, dest)
                    except OSError:
                        shutil.copy2(src, dest)
                print("media: %s -> %s" % (name, dest), flush=True)
            elif url:
                src = geturl(url, dest)

def pull_missing(cmd):
    """Any previous-step file this command names: GET from R2 if it is not already on disk."""
    pull_place()
    for a in cmd or []:
        if not isinstance(a, str) or not a.startswith("/") or os.path.isfile(a):
            continue
        name = os.path.basename(a)
        item = next((x for x in (jobs.get("place") or [])
                     if x.get("name") == name or a in (x.get("dests") or [])), None)
        src = by_name.get(name)
        if src and os.path.isfile(src):
            os.makedirs(os.path.dirname(a) or ".", exist_ok=True)
            if not os.path.exists(a):
                try:
                    os.symlink(src, a)
                except OSError:
                    shutil.copy2(src, a)
            print("needed: %s -> %s" % (name, a), flush=True)
        elif item and item.get("url"):
            geturl(item["url"], a)

def prefer_staged(cmd):
    staged = {
        "claude_sentences.py": "/work/_app/dubstudio/pipeline/claude_sentences.py",
        "lipmesh.py": "/work/_pipe/lipmesh.py",
    }
    out = []
    for a in cmd or []:
        if isinstance(a, str):
            hit = staged.get(os.path.basename(a))
            if hit and os.path.isfile(hit):
                a = hit
        out.append(a)
    return out

remember_files()
pull_place()
for item in jobs["steps"]:
    name, cmd = item["name"], prefer_staged(item["cmd"])
    print(f"=== {name} ===", flush=True)
    pull_missing(cmd)
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
# Segment caches are rebuildable. Sending them back filled Render's 50 GB after a finished
# visit (TAB EP53 extractall ENOSPC). /out already has the deliverables.
for name in ("segs", "segs_groups", "segs_groups.auto", "bookends", "pieces", "thumbs"):
    shutil.rmtree(os.path.join(here, name), ignore_errors=True)
print("scratch dropped (not sent back)", flush=True)
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
              name="render_tail", release_log=None):
    """Run several pipeline steps on ONE rented machine, in order. -> the worker's answer.

    `named_cmds` is [(name, cmd), ...] with this machine's paths, exactly as the runner would have run them
    locally. They go up as one job; only what the last step changed comes back -- which for the render visit
    is the videos. The pipeline scripts themselves are not touched. `name` is the job name (`match`, `cut`,
    `render_tail`), so two visits do not share one upload."""
    W, O = Path(ep["work_dir"]), Path(ep["output_dir"])
    _maybe_free_disk(W, log, db=db, keep_episode_id=ep.get("id"))
    ensure_work_for_cloud(show, ep, name, log=log)
    heal_config_inputs(db, ep, W)
    files, packed = {}, []
    staged = False
    for step_name, cmd in named_cmds:
        cmd = _localise_cmd_inputs(cmd, db, ep)
        if any(str(c).endswith("library.py") for c in (cmd or [])):
            cmd = point_library_at_staged_app(cmd, W)
            log("library: this box's ai.py/library.py travel with the job (the rented image is older)")
        if any(str(c).endswith("lipmesh.py") for c in (cmd or [])):
            cmd = point_lipmesh_at_staged(cmd, W)
        if any(str(c).endswith("lipmerge.py") for c in (cmd or [])):
            cmd = point_lipmerge_at_staged(cmd, W)
            log("lip merge: this box's lipmerge.py travels with the job (the rented image drops a 1-frame gap)")
        if any(str(c).endswith("dubstitch.py") for c in (cmd or [])):
            cmd = point_dubstitch_at_staged(cmd, W)
            staged = True
        if any(str(c).endswith("mksrt.py") for c in (cmd or [])):
            cmd = point_mksrt_at_staged(cmd, W)
        if any(str(c).endswith("claude_sentences.py") for c in (cmd or [])):
            cmd = point_sentences_at_staged(cmd, W)
            log("sentences: this box's claude_sentences.py travels with the job "
                "(the rented image has no /opt/scene/pipeline copy)")
        cmd_t, files = tokenise(cmd, W, O, cfg, files=files)
        # Do this before {WORK} is frozen to /work. The dub audio lives in input/, which the work tar skips,
        # so a chain that only said /work/input/dub_audio/….mp3 arrived with no file (matching, 2 Nov 2026).
        cmd_t, files = _send_large_work_files(cmd_t, W, files)
        packed.append({"name": step_name, "cmd": cmd_t})
    files.update(_files_named_in_config(W, O, files))
    files.update(_input_asset_files(db, ep, files))
    place = _media_place_list(files, W)
    for item, path in zip(place, files.values()):
        try:
            item["url"] = storage.presign(_file_in_cloud(db, show, ep, Path(path)), 10800)
        except Exception:
            pass
    places = dict(WORKER_PLACES)
    for token, path in files.items():
        places[token] = f"{WORKER['{FILES}']}/{token.strip('{}')}/{Path(path).name}"
    for item in packed:
        item["cmd"] = [_fill_tokens(a, places) for a in item["cmd"]]
    (W / "_cloud_chain.json").write_text(json.dumps({"steps": packed, "place": place},
                                                    ensure_ascii=False), encoding="utf-8")
    (W / "_cloud_chain.py").write_text(CHAIN_DRIVER, encoding="utf-8")
    if staged:
        log("this box's dubstitch.py travels with the job (the rented image concatenates leftover .ok files without the mp4)")
    log(f"{len(packed)} steps on one rented machine: " + ", ".join(n for n, _ in named_cmds))
    return run_command(db, show, ep, name,
                       [cfg.pipeline_python if hasattr(cfg, "pipeline_python") else "{py}",
                        str(W / "_cloud_chain.py")],
                       cfg, log=log, on_progress=on_progress, timeout=timeout, preset_files=files, kind=kind,
                       release_log=release_log)


def _file_in_cloud(db, show, ep, path):
    """A big input file in R2, uploading it once if it is not there yet. -> the key."""
    path = Path(path)
    a = None
    one = getattr(db, "one", None) if db is not None else None
    if callable(one) and isinstance(ep, dict) and ep.get("id") is not None:
        a = one("SELECT * FROM assets WHERE episode_id=? AND path=? AND role='input'", (ep["id"], str(path)))
        if not a:
            a = one("SELECT * FROM assets WHERE episode_id=? AND role='input' AND path LIKE ?",
                    (ep["id"], "%" + path.name))
    if a and a.get("r2_key"):
        return a["r2_key"]
    # Worker already PUT work/out videos. Do not fetch them onto Render to re-upload.
    for folder, key_fn in (
        (ep.get("work_dir"), lambda rel: storage.work_key(show, ep, rel)),
        (ep.get("output_dir"), lambda rel: storage.out_key(show, ep, Path(rel).name)),
    ):
        if not folder:
            continue
        try:
            rel = path.resolve().relative_to(Path(folder).resolve()).as_posix()
        except (ValueError, OSError):
            if path.name and folder:
                rel = path.name
            else:
                continue
        try:
            k = key_fn(rel)
            if path.suffix.lower() in GET_EXT and not path.is_file():
                return k
            if storage.head(k):
                return k
        except Exception:
            continue
    kind = (a or {}).get("kind") or "file"
    fname = Path(a["path"]).name if a and a.get("path") else path.name
    key = storage.input_key(show, ep, kind, fname)
    if path.is_file():
        storage.put_big_file(path, key)
        if a and a.get("id") and callable(getattr(db, "update", None)):
            try:
                db.update("assets", a["id"], r2_key=key)
            except Exception:
                pass
        return key
    if a or kind in ("src_video", "dub_audio", "dub_script", "src_script"):
        return key
    if storage.head(key):
        return key
    raise FileNotFoundError(f"the file is gone: {path}")
