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

Large videos are PUT to `outputs.loose` (per-file signed URLs). The work/out tars stay small
JSON/SRT so the website never downloads a master onto Render.
"""
import http.client, io, json, os, shutil, signal, subprocess, sys, tarfile, time
import urllib.error, urllib.parse, urllib.request

try:
    import runpod
except ImportError:  # tests import this file without the RunPod SDK
    runpod = None

WORK = "/work"                    # the episode's work folder, as {WORK}
OUT = "/out"                      # the show's output folder, as {OUT}
FILES = "/files"                  # big inputs land here
CODE = "/opt/dubstudio"
PIPE_PY = "/opt/pipeline_env/bin/python"      # the pipeline's own python, as on the Mac (see the Dockerfile)
# Face map has its own env on the GPU image (`/opt/venv_face`). The CPU image has no such env, so {FPY}
# falls back to the pipeline python there -- same as before.
FACE_PY = "/opt/venv_face/bin/python" if os.path.isfile("/opt/venv_face/bin/python") else PIPE_PY
PLACES = {"{PY}": PIPE_PY, "{FPY}": FACE_PY, "{TOOLS}": CODE, "{SCRIPTS}": f"{CODE}/scripts",
          "{SCENE}": "/opt/scene/pipeline", "{MODELS}": "/opt/models", "{WORK}": WORK, "{OUT}": OUT,
          "{APP}": "/opt/app"}
SMALL = 1 << 20                 # same cut as the website work tar
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4a", ".wav", ".aac", ".mp3"}
WORK_SKIP = ("input/", "lips_nets.tar.gz", "segs/", "segs_groups/", "segs_groups.auto/",
             "bookends/", "pieces/", "thumbs/")


# ---------------- moving files ----------------
def fetch(url, path, tries=4):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    last = None
    t = time.time()
    for a in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=1800) as r, open(path, "wb") as f:
                shutil.copyfileobj(r, f, 8 << 20)
            return {"bytes": os.path.getsize(path), "seconds": round(time.time() - t, 1)}
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (500, 502, 503, 504) or a == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException) as e:
            last = e
            if a == tries - 1:
                raise
        time.sleep(2 * 2 ** a)
    raise last


def put(url, path, ctype="application/octet-stream", tries=4):
    # Stream the open file in chunks. Content-Length is the file size, so a multi-GB render is not loaded into RAM.
    size = os.path.getsize(path)
    u = urllib.parse.urlsplit(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    target = (u.path or "/") + (("?" + u.query) if u.query else "")
    last = None
    for a in range(tries):
        conn = cls(u.hostname, u.port or cls.default_port, timeout=1800)
        conn.blocksize = 8 << 20          # same chunk size as fetch(); the file itself stays on disk
        try:
            with open(path, "rb") as f:
                conn.request("PUT", target, body=f,
                             headers={"Content-Type": ctype, "Content-Length": str(size)})
                resp = conn.getresponse()
                status, body = resp.status, resp.read()
            if 200 <= status < 300:
                return status
            last = urllib.error.HTTPError(url, status, resp.reason, resp.headers, io.BytesIO(body))
            if status not in (500, 502, 503, 504) or a == tries - 1:
                raise last
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException) as e:
            last = e
            if a == tries - 1:
                raise
        finally:
            conn.close()
        time.sleep(2 * 2 ** a)
    raise last


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


def _posix(rel):
    return (rel or "").replace("\\", "/").lstrip("./")


def is_large_output(rel, path):
    """Videos (any size) and anything over 1 MB stay out of the return tar. JSON/SRT go in the tar."""
    rel = _posix(rel)
    suf = os.path.splitext(rel)[1].lower()
    if suf in VIDEO_EXT:
        return True
    try:
        return os.path.getsize(path) > SMALL
    except OSError:
        return False


def split_changed(root, before, skip_prefixes=()):
    """What the step wrote, split into small-tar members vs large files the worker PUTs itself."""
    after = snapshot(root)
    small, large = [], []
    for rel, v in after.items():
        rel_p = _posix(rel)
        if any(rel_p == p.rstrip("/") or rel_p.startswith(p) for p in skip_prefixes):
            continue
        if before.get(rel) == v:
            continue
        path = os.path.join(root, rel)
        if is_large_output(rel_p, path):
            large.append(rel_p)
        else:
            small.append(rel_p)
    return small, large


def tar_changed(root, before, tar_path, skip_prefixes=()):
    """Tar only what the step wrote or changed. -> [relative paths]"""
    small, large = split_changed(root, before, skip_prefixes=skip_prefixes)
    # Old website still downloads this tar. Keep large files in it only when there is no loose PUT URL.
    changed = small + large
    if not changed:
        return []
    with tarfile.open(tar_path, "w:gz") as t:
        for rel in changed:
            t.add(os.path.join(root, rel), arcname=rel)
    return changed


def tar_small(root, names, tar_path):
    if not names:
        return []
    with tarfile.open(tar_path, "w:gz") as t:
        for rel in names:
            t.add(os.path.join(root, rel), arcname=rel)
    return names


def loose_url(loose, side, rel):
    """outputs.loose is {work: {rel: url}, out: {rel: url}} or a flat {rel: url}."""
    if not loose:
        return None
    rel = _posix(rel)
    side_map = loose.get(side) if isinstance(loose.get(side), dict) else None
    if side_map and side_map.get(rel):
        return side_map[rel]
    if isinstance(loose.get(rel), str):
        return loose[rel]
    return None


# ---------------- the jobs ----------------
def wipe_dirs(dirs):
    """Clear a job's folders and make them again. The next job on this machine must not see the last one."""
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)


def run_logged(cmd, cwd=None, env=None):
    """Run a step in its own process group.

    A cancel from the platform sends SIGTERM here. The step and every child it started
    (ffmpeg, python) are stopped, then the folders are cleared, so a reused machine does
    not keep the cancelled episode's files or a process that could still upload them.
    """
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True)

    def stop(signum, frame):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        wipe_dirs((WORK, OUT, FILES))
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        out, err = proc.communicate()
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    finally:
        signal.signal(signal.SIGTERM, previous)


def lip_scan(job_in):
    """The Step 4B lip scan of one or more pieces. Same script, same weights as the Mac."""
    wipe_dirs((WORK, OUT, FILES))
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
    p = run_logged(cmd)
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
    wipe_dirs((WORK, OUT, FILES))

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
    for item in job_in.get("place") or []:
        name = item.get("name") or ""
        url = item.get("url")
        src = next((os.path.join(FILES, token.strip("{}"), names.get(token) or token.strip("{}"))
                    for token in (job_in.get("files") or {})
                    if (names.get(token) or token.strip("{}")) == name), None)
        if src and not os.path.isfile(src):
            src = None
        for dest in item.get("dests") or []:
            if os.path.isfile(dest) and os.path.getsize(dest) > 64:
                continue
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            if src and os.path.isfile(src):
                if not os.path.exists(dest):
                    try:
                        os.symlink(src, dest)
                    except OSError:
                        shutil.copy2(src, dest)
            elif url:
                brought[name] = fetch(url, dest)
                src = dest

    cmd = [_fill(c, places) for c in job_in["cmd"]]
    place = job_in.get("place") or []
    for a in cmd:
        if not isinstance(a, str) or not a.startswith("/") or os.path.isfile(a):
            continue
        name = os.path.basename(a)
        item = next((x for x in place if x.get("name") == name or a in (x.get("dests") or [])), None)
        token = next((t for t, n in names.items() if n == name), None)
        url = (item or {}).get("url") or ((job_in.get("files") or {}).get(token) if token else None)
        if not url:
            continue
        os.makedirs(os.path.dirname(a) or ".", exist_ok=True)
        brought[name] = fetch(url, a)
        print("needed:", name, "->", a, flush=True)
    before_work, before_out = snapshot(WORK), snapshot(OUT)

    t = time.time()
    env = dict(os.environ, PYTHONUNBUFFERED="1", HF_HOME="/opt/models", HUGGINGFACE_HUB_CACHE="/opt/models",
               SENTENCE_TRANSFORMERS_HOME="/opt/models", DUBSTUDIO_MODELS="/opt/models",
               **{k: v for k, v in (job_in.get("env") or {}).items()})
    p = run_logged(cmd, cwd=f"{CODE}/scripts", env=env)
    secs = round(time.time() - t, 1)
    log = (p.stdout or "") + (("\n--- stderr ---\n" + p.stderr) if p.stderr else "")

    # rc 3 means "not finished, run me again (it resumes)" -- the app decides, so it is reported, not hidden
    res = {"ok": p.returncode == 0, "step": job_in.get("name") or "command", "rc": p.returncode,
           "seconds": secs, "cmd": cmd, "fetched": brought, "log": log[-12000:]}
    sent = {"loose": {"work": [], "out": []}}
    skip = WORK_SKIP
    # /out always: a failed final check used to skip the PUT, so TAB EP31's three videos died
    # on the rented machine. Work files only come back on success (or a resume, rc 3).
    send = [("out", OUT, before_out, ())]
    if p.returncode in (0, 3):
        send.append(("work", WORK, before_work, skip))
    loose = (job_in.get("outputs") or {}).get("loose") or {}
    for key, root, before, skip_prefixes in send:
        url = (job_in.get("outputs") or {}).get(key)
        small, large = split_changed(root, before, skip_prefixes=skip_prefixes)
        leftover = []
        for rel in large:
            dest = os.path.join(root, rel)
            u = loose_url(loose, key, rel)
            if not u:
                leftover.append(rel)
                continue
            put(u, dest)
            sent["loose"][key].append({"name": rel, "bytes": os.path.getsize(dest)})
        tar_names = small + leftover
        if url and tar_names:
            tar = f"/tmp/{key}_out.tar.gz"
            tar_small(root, tar_names, tar)
            put(url, tar, "application/gzip")
            sent[key] = {"files": len(tar_names), "bytes": os.path.getsize(tar),
                         "names": sorted(tar_names)[:40]}
            try:
                os.remove(tar)
            except OSError:
                pass
        elif url and not tar_names and not sent["loose"][key]:
            pass
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


if __name__ == "__main__" and runpod is not None:
    runpod.serverless.start({"handler": run})
