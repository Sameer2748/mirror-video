"""Run a pipeline step on rented machines instead of this one (RunPod serverless, docs/09).

A job is one step -- or one piece of a long step -- of one episode:

    {"step": "lip_scan", "inputs": {"video": <signed GET>}, "args": {"range": [0, 15]},
     "outputs": {"result": <signed PUT>}}

The worker image (`sameer27488/dubstudio-worker:cpu`) already holds the pipeline scripts and the model weights, so
nothing is downloaded while a job runs. **No key ever leaves this machine**: the worker only gets signed links that
expire. The endpoint keeps no workers when the queue is empty and starts more when it grows, so a long step is split
into pieces that run side by side -- which is the only parallelism that helps (measured: several processes on ONE
machine are slower than one, because the lip scan is CPU-bound).

Settings (app/.env):
  RUNPOD_API_KEY        the account key
  RUNPOD_CPU_ENDPOINT   the serverless endpoint id for the CPU steps
  CLOUD_STEPS           which steps go to the cloud, e.g. "lip_gpu" (empty = everything runs here, as before)
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


def enabled_for(step, kind="cpu"):
    want = steps()
    return available(kind) and ("all" in want or step in want)


def pieces():
    return max(1, envfile.number("CLOUD_PIECES", 8))


# ---------------- the queue ----------------
def _call(path, body=None, timeout=60):
    key = envfile.get("RUNPOD_API_KEY")
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{API}/{endpoint()}{path}", data=data,
                               headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                        "User-Agent": UA})
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def submit(job_input):
    return _call("/run", {"input": job_input})["id"]


def status(job_id):
    return _call(f"/status/{job_id}", timeout=30)


def health():
    return _call("/health", timeout=30)


def wait(job_ids, on_progress=None, timeout=7200, poll=10):
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
                st = status(jid)
            except (urllib.error.URLError, TimeoutError):
                continue
            if st.get("status") == "COMPLETED":
                done[jid] = st
            elif st.get("status") in ("FAILED", "CANCELLED", "TIMED_OUT"):
                failed[jid] = str(st.get("error") or st.get("output") or st.get("status"))[:300]
        if on_progress:
            try:
                h = health().get("workers", {})
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
