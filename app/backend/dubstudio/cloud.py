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


def wait(job_ids, on_progress=None, timeout=3600, poll=10):
    """Wait for every job. -> {job id: status dict}. Raises when one fails, with the worker's own traceback."""
    out, start = {}, time.time()
    while len(out) < len(job_ids):
        if time.time() - start > timeout:
            raise RuntimeError(f"the cloud took longer than {timeout} s: {len(out)} of {len(job_ids)} pieces done")
        for jid in job_ids:
            if jid in out:
                continue
            try:
                st = status(jid)
            except (urllib.error.URLError, TimeoutError):
                continue
            if st.get("status") == "COMPLETED":
                out[jid] = st
            elif st.get("status") == "FAILED":
                raise RuntimeError(f"a cloud job failed: {str(st.get('error') or st.get('output'))[:600]}")
        if on_progress:
            try:
                h = health().get("workers", {})
            except Exception:
                h = {}
            on_progress(len(out), len(job_ids), h)
        if len(out) < len(job_ids):
            time.sleep(poll)
    return out


# ---------------- the lip scan, split into pieces ----------------
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


def lip_scan(db, show, ep, duration_s, n_pieces=None, log=print, on_progress=None):
    """Scan the whole source video on rented machines, in pieces, and write `<work>/lips_nets` exactly as the local
    step would. -> {pieces, seconds, worker_seconds}."""
    n = n_pieces or pieces()
    key = ensure_input_in_cloud(db, show, ep)
    video = storage.presign(key, 10800)
    # the SAME pieces the local step would cut (lipgpu --piece 15), only shared out between machines, so the range
    # files come back identical to a scan done here -- not merely equivalent
    cuts = [[round(x * PIECE_SECONDS, 2), round(min((x + 1) * PIECE_SECONDS, duration_s), 2)]
            for x in range(int((duration_s + PIECE_SECONDS - 1) // PIECE_SECONDS))]
    share = [cuts[i::n] for i in range(n)]                     # round-robin: every machine gets a fair mix
    jobs, out_keys = [], []
    for i, mine in enumerate(share):
        if not mine:
            continue
        ok = storage.work_key(show, ep, f"lips_cloud/piece_{i:02d}.tar.gz")
        out_keys.append(ok)
        jobs.append(submit({"step": "lip_scan", "inputs": {"video": video},
                            "args": {"ranges": mine, "device": "cpu"},
                            "outputs": {"result": storage.presign(ok, 10800, method="PUT")}}))
    log(f"{len(cuts)} pieces of {PIECE_SECONDS:.0f} s across {len(jobs)} machines ({duration_s:.0f} s of video)")
    t0 = time.time()
    done = wait(jobs, on_progress=on_progress)
    nets = Path(ep["work_dir"]) / "lips_nets"
    nets.mkdir(parents=True, exist_ok=True)
    files = 0
    for i, ok in enumerate(out_keys):
        tar = nets.parent / f"_piece_{i:02d}.tar.gz"
        storage.download(ok, tar)
        with tarfile.open(tar) as t:
            t.extractall(nets.parent / "_piece")
        for f in (nets.parent / "_piece" / "lips_nets").glob("range_*.json"):
            f.replace(nets / f.name); files += 1
        tar.unlink(missing_ok=True)
    worker_s = sum(s.get("executionTime", 0) for s in done.values()) / 1000
    log(f"{files} range files from {n} pieces in {time.time() - t0:.0f} s (workers used {worker_s:.0f} s)")
    return {"pieces": n, "files": files, "seconds": round(time.time() - t0, 1), "worker_seconds": round(worker_s, 1)}
