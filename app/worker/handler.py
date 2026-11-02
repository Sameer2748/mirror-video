"""The Dub Studio pipeline worker (RunPod serverless).

Two kinds of job:

  lip_scan   the Step 4B lip scan of one or more 15 s pieces (what the first worker image did)
  command    ANY pipeline step: the command the app already built, with this machine's paths filled in

A `command` job looks like:

    {"step": "command",
     "cmd": ["{PY}", "{SCRIPTS}/dubstitch.py", "render", "--work", "{WORK}", ...],
     "work_in":  "<signed GET of a tar of the episode's small work files>",
     "out_in":   "<signed GET of a tar of the episode's output folder>"      (optional)
     "files":    {"{F0}": "<signed GET>", ...}   big inputs: the source video, the dub audio, a font
     "names":    {"{F0}": "TGS EPISODE 34.mp4", ...}  what each one must be called on disk
     "outputs":  {"work": "<signed PUT>", "out": "<signed PUT>"}}

It answers with the log and the exit code, and PUTs back **only the files that changed** -- so a step that writes
one JSON sends back one JSON, and a render sends back the video.

No key ever reaches here: every URL is a signed link that expires.
"""
import json, os, shutil, subprocess, sys, tarfile, time, urllib.request

import runpod

WORK = "/work"                    # the episode's work folder, as {WORK}
OUT = "/out"                      # the show's output folder, as {OUT}
FILES = "/files"                  # big inputs land here
CODE = "/opt/dubstudio"
PIPE_PY = "/opt/pipeline_env/bin/python"      # the pipeline's own python, as on the Mac (see the Dockerfile)
PLACES = {"{PY}": PIPE_PY, "{FPY}": PIPE_PY, "{TOOLS}": CODE, "{SCRIPTS}": f"{CODE}/scripts",
          "{SCENE}": "/opt/scene/pipeline", "{MODELS}": "/opt/models", "{WORK}": WORK, "{OUT}": OUT,
          "{APP}": "/opt/app"}


# ---------------- moving files ----------------
def fetch(url, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    t = time.time()
    with urllib.request.urlopen(url, timeout=1800) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f, 8 << 20)
    return {"bytes": os.path.getsize(path), "seconds": round(time.time() - t, 1)}


def put(url, path, ctype="application/octet-stream"):
    with open(path, "rb") as f:
        req = urllib.request.Request(url, data=f.read(), method="PUT",
                                     headers={"Content-Type": ctype})
        with urllib.request.urlopen(req, timeout=1800) as r:
            return r.status


def untar_from(url, dest):
    os.makedirs(dest, exist_ok=True)
    tmp = "/tmp/in.tar.gz"
    fetch(url, tmp)
    with tarfile.open(tmp) as t:
        t.extractall(dest)
    os.remove(tmp)


def snapshot(root):
    """{relative path: (size, mtime)} of everything under root, to find what a step changed."""
    seen = {}
    for base, _, names in os.walk(root):
        for n in names:
            p = os.path.join(base, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            seen[os.path.relpath(p, root)] = (st.st_size, st.st_mtime)
    return seen


def tar_changed(root, before, tar_path, skip_prefixes=()):
    """Tar only what the step wrote or changed. -> [relative paths]"""
    after = snapshot(root)
    changed = [rel for rel, v in after.items()
               if before.get(rel) != v and not any(rel.startswith(p) for p in skip_prefixes)]
    if not changed:
        return []
    with tarfile.open(tar_path, "w:gz") as t:
        for rel in changed:
            t.add(os.path.join(root, rel), arcname=rel)
    return changed


# ---------------- the jobs ----------------
def lip_scan(job_in):
    """The Step 4B lip scan of one or more pieces. Same script, same weights as the Mac."""
    os.makedirs(WORK, exist_ok=True)
    got = fetch(job_in["inputs"]["video"], f"{WORK}/video.mp4")
    args = job_in.get("args", {})
    rngs = args.get("ranges") or ([args["range"]] if args.get("range") else None)
    out = f"{WORK}/lips_nets"
    cmd = [PIPE_PY, f"{CODE}/scripts/lipgpu.py", "--video", f"{WORK}/video.mp4", "--out-dir", out,
           "--talknet", f"{CODE}/third_party/TalkNet-ASD", "--device", args.get("device", "auto")]
    if rngs:
        with open(f"{WORK}/ranges.json", "w") as f:
            json.dump(rngs, f)
        cmd += ["--ranges", f"{WORK}/ranges.json"]
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    secs = round(time.time() - t, 1)
    if p.returncode != 0:
        return {"ok": False, "step": "lip_scan", "ranges": rngs, "seconds": secs,
                "error": (p.stderr or p.stdout)[-1500:]}
    tar = f"{WORK}/lips_nets.tar.gz"
    with tarfile.open(tar, "w:gz") as t_:
        t_.add(out, arcname="lips_nets")
    code = put(job_in["outputs"]["result"], tar, "application/gzip")
    return {"ok": True, "step": "lip_scan", "ranges": rngs, "seconds": secs, "fetch": got,
            "files": len(os.listdir(out)), "upload_status": code, "log_tail": p.stdout[-800:]}


def command(job_in):
    """Any pipeline step: the app's own command line, with this machine's paths filled in."""
    for d in (WORK, OUT, FILES):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)

    brought = {}
    if job_in.get("work_in"):
        untar_from(job_in["work_in"], WORK)
    if job_in.get("out_in"):
        untar_from(job_in["out_in"], OUT)
    names = job_in.get("names") or {}
    places = dict(PLACES)
    for token, url in (job_in.get("files") or {}).items():
        name = names.get(token) or token.strip("{}")
        path = os.path.join(FILES, token.strip("{}"), name)      # its own folder: two files may share a name
        brought[name] = fetch(url, path)
        places[token] = path

    cmd = [_fill(c, places) for c in job_in["cmd"]]
    before_work, before_out = snapshot(WORK), snapshot(OUT)

    t = time.time()
    env = dict(os.environ, PYTHONUNBUFFERED="1", HF_HOME="/opt/models", HUGGINGFACE_HUB_CACHE="/opt/models",
               SENTENCE_TRANSFORMERS_HOME="/opt/models", DUBSTUDIO_MODELS="/opt/models",
               **{k: v for k, v in (job_in.get("env") or {}).items()})
    p = subprocess.run(cmd, cwd=f"{CODE}/scripts", capture_output=True, text=True, env=env)
    secs = round(time.time() - t, 1)
    log = (p.stdout or "") + (("\n--- stderr ---\n" + p.stderr) if p.stderr else "")

    # rc 3 means "not finished, run me again (it resumes)" -- the app decides, so it is reported, not hidden
    res = {"ok": p.returncode == 0, "step": job_in.get("name") or "command", "rc": p.returncode,
           "seconds": secs, "cmd": cmd, "fetched": brought, "log": log[-12000:]}
    if p.returncode not in (0, 3):
        return res

    sent = {}
    skip = ("input/", "lips_nets.tar.gz")
    for root, before, key in ((WORK, before_work, "work"), (OUT, before_out, "out")):
        url = (job_in.get("outputs") or {}).get(key)
        if not url:
            continue
        tar = f"/tmp/{key}_out.tar.gz"
        changed = tar_changed(root, before, tar, skip_prefixes=skip if root == WORK else ())
        if changed:
            put(url, tar, "application/gzip")
            sent[key] = {"files": len(changed), "bytes": os.path.getsize(tar), "names": sorted(changed)[:40]}
    res["sent"] = sent
    return res


def _fill(arg, places):
    for token, real in places.items():
        if token in arg:
            arg = arg.replace(token, str(real))
    return arg


def run(job):
    job_in = job["input"]
    step = job_in.get("step")
    if step == "lip_scan":
        return lip_scan(job_in)
    if step == "command":
        return command(job_in)
    return {"ok": False, "error": f"unknown step {step!r}"}


runpod.serverless.start({"handler": run})
