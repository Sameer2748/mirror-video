"""Cloudflare R2 (S3-compatible) storage: the finished videos and sheets of an episode, and a copy of the database.

R2 speaks the S3 API, so this is a small signed-request client (AWS Signature V4 over `httpx`, no extra package).
Settings come from app/.env (environment variables win -- `envfile.py`); never print or log them:

  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
  R2_ENDPOINT      https://<account>.r2.cloudflarestorage.com   (built from the account id when missing)
  R2_PREFIX        optional folder inside the bucket (default: none)
  R2_AUTO_UPLOAD   true (default): every finished render is sent up by itself
  R2_DB_SYNC       true (default): the database file is copied up after each daily backup and on request

What goes up:
  shows/<SHOW>/ep<N>/<file>     the deliverables (3 videos, English SRT, mapping / scene-edit / QC sheets, Premiere XML)
  db/dubstudio.db               the newest copy of the database, plus db/history/dubstudio-<date>.db

R2 is **object storage, not a database**: SQLite stays the live database on the Mac (it needs local file locking)
and the file is mirrored to R2, so the data also lives off the machine. See docs/08_ACCOUNTS_AND_STORAGE.md.
"""
import hashlib, hmac, mimetypes, re, shutil, sqlite3, threading, time
from fnmatch import fnmatch
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from . import envfile
from .events import bus

ALGO = "AWS4-HMAC-SHA256"
SERVICE = "s3"
UNSIGNED = "UNSIGNED-PAYLOAD"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
PART = 1 << 20                                  # read 1 MiB at a time while uploading
_worker = {"thread": None, "stop": False}


# ---------------- settings ----------------
def settings():
    acc = envfile.get("R2_ACCOUNT_ID", "")
    end = (envfile.get("R2_ENDPOINT") or (f"https://{acc}.r2.cloudflarestorage.com" if acc else "")).rstrip("/")
    return {"account": acc, "endpoint": end, "bucket": envfile.get("R2_BUCKET", ""),
            "key_id": envfile.get("R2_ACCESS_KEY_ID", ""), "secret": envfile.get("R2_SECRET_ACCESS_KEY", ""),
            "region": envfile.get("R2_REGION", "auto"), "prefix": (envfile.get("R2_PREFIX", "") or "").strip("/"),
            "auto_upload": envfile.flag("R2_AUTO_UPLOAD", True), "db_sync": envfile.flag("R2_DB_SYNC", True)}


def configured():
    s = settings()
    return bool(s["endpoint"] and s["bucket"] and s["key_id"] and s["secret"])


def public_settings():
    """What the Settings page may see: no keys, only whether they are set."""
    s = settings()
    return {"configured": configured(), "endpoint": s["endpoint"], "bucket": s["bucket"], "prefix": s["prefix"],
            "keys_set": bool(s["key_id"] and s["secret"]), "auto_upload": s["auto_upload"], "db_sync": s["db_sync"]}


def _key(*parts):
    s = settings()
    bits = [s["prefix"]] + [str(p).strip("/") for p in parts]
    return "/".join(b for b in bits if b)


# ---------------- signing ----------------
def _sign_key(secret, date, region, service=SERVICE):
    k = ("AWS4" + secret).encode()
    for part in (date, region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    return k


def _canonical_query(params):
    items = sorted((quote(str(k), safe="-_.~"), quote(str(v), safe="-_.~")) for k, v in (params or {}).items())
    return "&".join(f"{k}={v}" for k, v in items)


def _url_path(key):
    s = settings()
    return "/" + quote(f"{s['bucket']}/{key}".replace("//", "/"), safe="/-_.~")


def _auth_headers(method, key, params=None, headers=None, payload_hash=UNSIGNED):
    """Signature V4 in the Authorization header. Returns the full header dict."""
    s = settings()
    host = s["endpoint"].split("://", 1)[1].split("/", 1)[0]
    now = datetime.now(timezone.utc)
    stamp, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    head = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": stamp, **{k.lower(): str(v) for k, v in (headers or {}).items()}}
    signed = ";".join(sorted(head))
    canon_headers = "".join(f"{k}:{head[k].strip()}\n" for k in sorted(head))
    canon = "\n".join([method, _url_path(key), _canonical_query(params), canon_headers, signed, payload_hash])
    scope = f"{date}/{s['region']}/{SERVICE}/aws4_request"
    to_sign = "\n".join([ALGO, stamp, scope, hashlib.sha256(canon.encode()).hexdigest()])
    sig = hmac.new(_sign_key(s["secret"], date, s["region"]), to_sign.encode(), hashlib.sha256).hexdigest()
    head["Authorization"] = f"{ALGO} Credential={s['key_id']}/{scope}, SignedHeaders={signed}, Signature={sig}"
    return {k: v for k, v in head.items() if k != "host"}


def presign(key, seconds=3600, method="GET", download_name=None, params=None):
    """A link that works without the keys (the browser downloads from, or uploads to, R2 by itself)."""
    _require()
    s = settings()
    now = datetime.now(timezone.utc)
    stamp, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    scope = f"{date}/{s['region']}/{SERVICE}/aws4_request"
    params = {**(params or {}),
              "X-Amz-Algorithm": ALGO, "X-Amz-Credential": f"{s['key_id']}/{scope}", "X-Amz-Date": stamp,
              "X-Amz-Expires": str(int(max(1, min(seconds, 604800)))), "X-Amz-SignedHeaders": "host"}
    if download_name:
        params["response-content-disposition"] = f'attachment; filename="{download_name}"'
    host = s["endpoint"].split("://", 1)[1].split("/", 1)[0]
    canon = "\n".join([method, _url_path(key), _canonical_query(params), f"host:{host}\n", "host", UNSIGNED])
    to_sign = "\n".join([ALGO, stamp, scope, hashlib.sha256(canon.encode()).hexdigest()])
    sig = hmac.new(_sign_key(s["secret"], date, s["region"]), to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{s['endpoint']}{_url_path(key)}?{_canonical_query(params)}&X-Amz-Signature={sig}"


def _require():
    if not configured():
        raise RuntimeError("R2 storage is not set up: put R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY "
                           "and R2_BUCKET in app/.env")


def _call(method, key, params=None, headers=None, content=None, payload_hash=None, timeout=120.0, stream=False):
    _require()
    s = settings()
    if payload_hash is None:
        payload_hash = EMPTY_SHA if content is None else UNSIGNED
    head = _auth_headers(method, key, params, headers, payload_hash)
    url = f"{s['endpoint']}{_url_path(key)}"
    client = httpx.Client(timeout=timeout, follow_redirects=False)
    try:
        r = client.request(method, url, params=params, headers=head, content=content)
        if r.status_code >= 400:
            raise RuntimeError(f"R2 {method} {key}: HTTP {r.status_code} {_error_text(r.text)}")
        return r
    finally:
        client.close()


def _error_text(xml):
    import re
    m = re.search(r"<Message>(.*?)</Message>", xml or "", re.S)
    return (m.group(1) if m else (xml or ""))[:200]


# ---------------- objects ----------------
def content_type(path):
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def put_bytes(key, data, ctype="application/octet-stream"):
    _call("PUT", key, headers={"content-type": ctype, "content-length": str(len(data))}, content=data,
          payload_hash=hashlib.sha256(data).hexdigest())
    return {"key": key, "size": len(data)}


def put_file(path, key, on_progress=None, timeout=3600.0):
    """Send one file up, reading it 1 MiB at a time (`on_progress(bytes_sent)` as it goes)."""
    p = Path(path)
    size = p.stat().st_size

    def body():
        sent = 0
        with p.open("rb") as f:
            while True:
                chunk = f.read(PART)
                if not chunk:
                    break
                sent += len(chunk)
                if on_progress:
                    on_progress(sent)
                yield chunk

    _call("PUT", key, headers={"content-type": content_type(p), "content-length": str(size)}, content=body(),
          payload_hash=UNSIGNED, timeout=timeout)
    return {"key": key, "size": size}


# ---------------- big files: multipart upload (S3 API) ----------------
# One PUT is capped at 5 GB and has to start again from zero if the line drops, so anything over PART_BIG goes up
# in parts: each part is signed on its own, failed parts are retried, and the browser can send them one by one.
PART_BIG = 64 << 20                              # 64 MiB per part -> 10 000 parts = 640 GB, far past any episode
MULTIPART_FROM = 96 << 20                        # files larger than this use multipart


def _xml_value(text, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text or "", re.S)
    return m.group(1) if m else None


def multipart_start(key, ctype="application/octet-stream"):
    r = _call("POST", key, params={"uploads": ""}, headers={"content-type": ctype}, content=b"",
              payload_hash=EMPTY_SHA, timeout=60.0)
    upload_id = _xml_value(r.text, "UploadId")
    if not upload_id:
        raise RuntimeError(f"R2 did not start the upload of {key}: {r.text[:200]}")
    return upload_id


def multipart_url(key, upload_id, part, seconds=3600):
    """A signed link for one part, so the browser can send it straight to R2."""
    return presign(key, seconds=seconds, method="PUT", params={"partNumber": str(part), "uploadId": upload_id})


def multipart_put(key, upload_id, part, data, timeout=900.0):
    r = _call("PUT", key, params={"partNumber": str(part), "uploadId": upload_id},
              headers={"content-length": str(len(data))}, content=data,
              payload_hash=hashlib.sha256(data).hexdigest(), timeout=timeout)
    return (r.headers.get("etag") or "").strip('"')


def multipart_finish(key, upload_id, etags):
    """etags: [(part number, etag)] in order."""
    body = "<CompleteMultipartUpload>" + "".join(
        f"<Part><PartNumber>{n}</PartNumber><ETag>\"{e}\"</ETag></Part>" for n, e in sorted(etags)) + \
        "</CompleteMultipartUpload>"
    data = body.encode()
    r = _call("POST", key, params={"uploadId": upload_id}, headers={"content-type": "application/xml",
              "content-length": str(len(data))}, content=data, payload_hash=hashlib.sha256(data).hexdigest(),
              timeout=300.0)
    if "<Error" in r.text:
        raise RuntimeError(f"R2 could not finish {key}: {_error_text(r.text)}")
    return {"key": key, "etag": (_xml_value(r.text, "ETag") or "").strip('"')}


def multipart_abort(key, upload_id):
    try:
        _call("DELETE", key, params={"uploadId": upload_id}, timeout=60.0)
    except Exception:
        pass


def put_big_file(path, key, on_progress=None, timeout=900.0):
    """Send a file of any size: one PUT when it is small, parts when it is not (resumes part by part on a retry)."""
    p = Path(path); size = p.stat().st_size
    if size < MULTIPART_FROM:
        return put_file(p, key, on_progress=on_progress, timeout=max(timeout, 3600.0))
    upload_id = multipart_start(key, content_type(p))
    etags, sent, n = [], 0, 0
    try:
        with p.open("rb") as f:
            while True:
                chunk = f.read(PART_BIG)
                if not chunk:
                    break
                n += 1
                for attempt in range(3):
                    try:
                        etags.append((n, multipart_put(key, upload_id, n, chunk, timeout=timeout))); break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(2 * 2 ** attempt)
                sent += len(chunk)
                if on_progress:
                    on_progress(sent)
        multipart_finish(key, upload_id, etags)
    except Exception:
        multipart_abort(key, upload_id)
        raise
    return {"key": key, "size": size, "parts": n}


def put_stream(key, fileobj, size, ctype="application/octet-stream", timeout=3600.0):
    """Send an open file straight up (used by the browser upload the API passes through)."""
    def body():
        while True:
            chunk = fileobj.read(PART)
            if not chunk:
                break
            yield chunk
    _call("PUT", key, headers={"content-type": ctype, "content-length": str(size)}, content=body(),
          payload_hash=UNSIGNED, timeout=timeout)
    return {"key": key, "size": size}


def get_bytes(key):
    return _call("GET", key).content


def download(key, path, timeout=3600.0, on_progress=None):
    """Bring an object down to a file, a megabyte at a time (a source video is hundreds of MB)."""
    _require()
    s = settings()
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    head = _auth_headers("GET", key, None, None, EMPTY_SHA)
    tmp = p.with_name(p.name + ".part")
    got = 0
    with httpx.Client(timeout=timeout) as client:
        with client.stream("GET", f"{s['endpoint']}{_url_path(key)}", headers=head) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"R2 GET {key}: HTTP {r.status_code} {_error_text(r.read().decode(errors='replace'))}")
            total = int(r.headers.get("content-length") or 0)
            try:
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes(PART):
                        f.write(chunk); got += len(chunk)
                        if on_progress:
                            on_progress(got, total)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
    tmp.replace(p)
    return {"path": str(p), "size": p.stat().st_size}


def head(key):
    try:
        r = _call("HEAD", key, timeout=30.0)
    except RuntimeError:
        return None
    return {"key": key, "size": int(r.headers.get("content-length") or 0), "etag": (r.headers.get("etag") or "").strip('"'),
            "modified": r.headers.get("last-modified")}


def delete(key):
    _call("DELETE", key, timeout=60.0)
    return {"deleted": key}


def listing(prefix="", limit=200):
    """[{key, size, modified}] under a prefix (the bucket's own prefix is added)."""
    import re
    full = _key(prefix) if prefix else _key()
    s = settings()
    r = _call("GET", "", params={"list-type": "2", "prefix": full, "max-keys": str(min(limit, 1000))}, timeout=60.0)
    out = []
    for block in re.findall(r"<Contents>(.*?)</Contents>", r.text, re.S):
        g = lambda tag: (re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S) or [None, ""])[1]
        out.append({"key": g("Key"), "size": int(g("Size") or 0), "modified": g("LastModified")})
    return {"bucket": s["bucket"], "prefix": full, "objects": out[:limit]}


def status():
    """What the Settings page shows: is R2 set up, and does the bucket answer?"""
    out = public_settings()
    if not configured():
        out["ok"] = False
        return out
    try:
        r = _call("GET", "", params={"list-type": "2", "max-keys": "1"}, timeout=20.0)
        out.update(ok=True, objects=("<Contents>" in r.text))
    except Exception as ex:
        out.update(ok=False, error=str(ex)[:300])
    return out


# ---------------- where an episode's files live (docs/09 §2) ----------------
def safe_name(name):
    """A file name that is safe as a key: no folders, no spaces that break a signed link."""
    name = Path(str(name or "file")).name.strip() or "file"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120]


def show_prefix(show):
    return _key("shows", show["code"])


def episode_prefix(show, ep):
    return _key("shows", show["code"], f"ep{ep['number']}")


def input_key(show, ep, kind, filename):
    return f"{episode_prefix(show, ep)}/input/{kind}/{safe_name(filename)}"


def out_key(show, ep, filename):
    return f"{episode_prefix(show, ep)}/out/{safe_name(filename)}"


def work_key(show, ep, rel):
    return f"{episode_prefix(show, ep)}/work/{str(rel).strip('/')}"


def sheet_key(show, filename):
    return f"{show_prefix(show)}/localisation/{safe_name(filename)}"


def cache_path(ep, kind, filename):
    """Where a worker keeps an uploaded file while it runs (the pipeline scripts take plain paths)."""
    return str(Path(ep["work_dir"]) / "input" / kind / safe_name(filename))


# ---------------- the editor's own media (proxy copy, dub preview, waveform, thumbs) ----------------
# These are made by ffmpeg from the episode's big files, so on a deployed API box -- which has neither the show
# folder nor the originals -- they have to come from R2. Mirrored once when they are made, then either handed to the
# browser as a signed link (playback: R2 serves the range requests, the API box passes no bytes) or pulled down to
# the work folder when something here needs a real file (a thumbnail, the waveform).
_mirrored = set()


def mirror(show, ep, rel, background=True):
    """Copy `<work>/rel` to R2 once, so another machine can serve it. Quiet: a failure never breaks the editor."""
    p = Path(ep["work_dir"]) / rel
    key = work_key(show, ep, rel)
    if not configured() or not p.exists() or key in _mirrored:
        return None
    def go():
        try:
            if not head(key):
                (put_big_file if p.stat().st_size >= MULTIPART_FROM else put_file)(p, key)
            _mirrored.add(key)
        except Exception:
            pass
    if background:
        threading.Thread(target=go, daemon=True).start()
    else:
        go()
    return key


def media_link(show, ep, rel, seconds=10800):
    """A signed link to `<work>/rel` in R2, or None if it is not there."""
    if not configured():
        return None
    key = work_key(show, ep, rel)
    return presign(key, seconds) if head(key) else None


def ensure_local(show, ep, rel):
    """A real file at `<work>/rel`: the one here, or the copy from R2. -> Path or None."""
    p = Path(ep["work_dir"]) / rel
    if p.exists():
        return p
    if not configured():
        return None
    key = work_key(show, ep, rel)
    if not head(key):
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".part")
    try:
        download(key, tmp)
        tmp.replace(p)
        _mirrored.add(key)
        return p
    except Exception:
        tmp.unlink(missing_ok=True)
        return None


def upload_mode():
    """'proxy' (the API passes the bytes through, works everywhere) or 'direct' (the browser PUTs straight to R2,
    needs a CORS rule on the bucket -- see docs/09 §3)."""
    mode = (envfile.get("UPLOAD_MODE", "proxy") or "proxy").lower()
    return mode if mode in ("proxy", "direct") else "proxy"


def queue_uploads(db, show, ep, files, user_id=None):
    """Put the files of one episode in the upload queue (newest first in the UI). `files`: deliver.deliverables()."""
    rows = []
    for f in files:
        if not f.get("exists"):
            continue
        key = out_key(show, ep, Path(f["path"]).name)
        old = db.one("SELECT * FROM uploads WHERE episode_id=? AND key=? ORDER BY id DESC LIMIT 1", (ep["id"], key))
        if old and old["status"] in ("waiting", "sending"):
            rows.append(old); continue
        if old and old["status"] == "done" and (old["size"] or 0) == (f.get("size") or 0):
            rows.append(old); continue                          # already up there, unchanged
        uid = db.insert("uploads", episode_id=ep["id"], kind=f.get("kind"), name=Path(f["path"]).name, path=f["path"],
                        bucket=settings()["bucket"], key=key, size=f.get("size") or 0, sent=0, status="waiting",
                        user_id=user_id, created=time.time())
        rows.append(db.one("SELECT * FROM uploads WHERE id=?", (uid,)))
    if rows:
        start_worker(db)
    return rows


def uploads_of(db, eid, limit=100):
    return db.q("SELECT * FROM uploads WHERE episode_id=? ORDER BY id DESC LIMIT ?", (eid, limit))


def retry_upload(db, uid):
    db.update("uploads", uid, status="waiting", error=None, sent=0, started=None, finished=None)


# ---------------- the uploader (one file at a time, in the background) ----------------
def start_worker(db):
    if not configured():
        return False
    t = _worker["thread"]
    if t and t.is_alive():
        return True
    _worker["stop"] = False
    _worker["thread"] = threading.Thread(target=_loop, args=(db,), name="r2-uploader", daemon=True)
    _worker["thread"].start()
    return True


def stop_worker():
    _worker["stop"] = True


def _loop(db, idle_rounds=10):
    """Sends waiting uploads, then stops after a few quiet rounds (it starts again with the next upload)."""
    quiet = 0
    while not _worker["stop"] and quiet < idle_rounds:
        row = db.one("SELECT * FROM uploads WHERE status='waiting' ORDER BY id LIMIT 1")
        if not row:
            quiet += 1; time.sleep(1.0); continue
        quiet = 0
        send_one(db, row)
    _worker["thread"] = None


def send_one(db, row):
    db.update("uploads", row["id"], status="sending", started=time.time(), error=None, sent=0)
    bus.publish("upload", episode_id=row["episode_id"], store=False, id=row["id"], status="sending", name=row["name"])
    last = [0.0]

    def progress(sent):
        if time.time() - last[0] > 0.5:
            last[0] = time.time()
            db.update("uploads", row["id"], sent=sent)
            bus.publish("upload", episode_id=row["episode_id"], store=False, id=row["id"], status="sending",
                        name=row["name"], sent=sent, size=row["size"])
    try:
        p = Path(row["path"])
        if not p.exists():
            raise FileNotFoundError(f"the file is gone: {p}")
        size = p.stat().st_size
        put_big_file(p, row["key"], on_progress=progress)      # videos are GB: they go up in parts
        db.update("uploads", row["id"], status="done", sent=size, size=size, finished=time.time())
        bus.publish("upload", episode_id=row["episode_id"], store=False, id=row["id"], status="done", name=row["name"])
        return True
    except Exception as ex:
        db.update("uploads", row["id"], status="failed", error=str(ex)[:300], finished=time.time())
        bus.publish("upload", episode_id=row["episode_id"], store=False, id=row["id"], status="failed",
                    name=row["name"], error=str(ex)[:200])
        return False


# ---------------- what a worker does with storage (docs/09 §3) ----------------
MEDIA_KINDS = ("src_video", "dub_audio")                 # the big ones: fetched by the `fetch` step
WORK_KEEP = ("*.json", "*.tsv", "*.txt", "*.srt", "*.py", "*.jpg", "scene/*.json", "broll_library/*.json",
             "broll_library/*.xlsx")                     # small work files worth keeping (a rerun then costs $0)
# `*.jpg` is the top level only (the QC sheet and the final-check frame), not the thousands of thumbs/*.jpg.

_work_fetched = set()


def _work_wanted(rel):
    """Is this work file one of the ones `publish_outputs` keeps? The same patterns, matched the same way.

    `Path.glob("*.jpg")` is the top level only, but `PurePosixPath("thumbs/x.jpg").match("*.jpg")` is True -- so
    matching with `match` would pull down every one of an episode's thousands of thumbnails. A pattern without a
    slash must match a name without a slash."""
    for pat in WORK_KEEP:
        if "/" in pat:
            if fnmatch(rel, pat):
                return True
        elif "/" not in rel and fnmatch(rel, pat):
            return True
    return False


def fetch_work_files(show, ep, log=None):
    """Bring this episode's small work files down from R2 if they are not here.

    The editor is built out of them -- the edit list, the rows, the lip-sync, the sentences, the SRTs -- so on a
    machine that never processed this episode (a deployed API box, or this Mac after `cleanup`) the editor cannot
    open without them. They are the same files `publish_outputs` sends up, they are small, and they are fetched once
    per episode per process. -> how many came down."""
    # keyed on the work folder, not the episode id: two libraries can hold the same ids
    key = str(ep["work_dir"])
    if not configured() or key in _work_fetched:
        return 0
    W = Path(ep["work_dir"])
    prefix = f"{episode_prefix(show, ep)}/work/"
    got = 0
    try:
        objects = listing(f"shows/{safe_name(show['code'])}/ep{ep['number']}/work", limit=1000)["objects"]
    except Exception as ex:
        if log:
            log(f"could not look in the cloud for the work files: {ex}")
        return 0
    for o in objects:
        rel = o["key"].split(prefix, 1)[-1] if prefix in o["key"] else None
        if not rel or o["size"] >= 64 << 20:
            continue
        if not _work_wanted(rel):
            continue
        p = W / rel
        if p.exists() and p.stat().st_size == o["size"]:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            download(o["key"], p)
            got += 1
        except Exception:
            continue
    _work_fetched.add(key)
    if got and log:
        log(f"{got} work files came down from the cloud")
    return got


def inputs_to_fetch(db, ep, kinds=None):
    """Input files of an episode that are in R2 but not (or no longer) on this machine."""
    out = []
    for a in db.q("SELECT * FROM assets WHERE episode_id=? AND role='input' AND r2_key IS NOT NULL", (ep["id"],)):
        if kinds and a["kind"] not in kinds:
            continue
        p = Path(a["path"])
        if p.exists() and (not a["size"] or p.stat().st_size == a["size"]):
            continue
        out.append(a)
    return out


def fetch_inputs(db, show, ep, kinds=None, log=print, on_progress=None):
    """Bring this episode's uploaded files down from R2 into the work folder, so every pipeline step finds a plain
    file path (the pipeline itself never changes). Files already there, with the right size, are left alone."""
    todo = inputs_to_fetch(db, ep, kinds)
    for a in todo:
        log(f"fetching {a['kind']}: {a['name'] if 'name' in a else Path(a['path']).name} ({(a['size'] or 0) / 1e6:.0f} MB)")
        download(a["r2_key"], a["path"], on_progress=(lambda got, total, k=a["kind"]: on_progress(k, got, total)) if on_progress else None)
        db.update("assets", a["id"], mtime=Path(a["path"]).stat().st_mtime)
    if show.get("sheet_key"):                                  # the show's localisation sheet (small, always wanted)
        p = Path(ep["work_dir"]) / "input" / "names" / Path(show["sheet_key"]).name
        if not p.exists():
            download(show["sheet_key"], p)
        prof = show["profile"]
        if prof.get("names_sheet") != str(p):
            prof["names_sheet"] = str(p)
            db.update("shows", show["id"], profile=prof)
            show["profile"] = prof
    return todo


def publish_outputs(db, show, ep, files, user_id=None, work_dir=None, log=print):
    """Everything the episode delivers goes up to `…/ep<N>/out/`, and the small work files to `…/ep<N>/work/`
    (Claude's caches and the edit lists, so a rerun anywhere costs nothing)."""
    sent = queue_uploads(db, show, ep, files, user_id=user_id)
    kept = 0
    W = Path(work_dir or ep["work_dir"])
    if W.is_dir():
        for pat in WORK_KEEP:
            for p in sorted(W.glob(pat)):
                if p.is_file() and p.stat().st_size < 64 << 20:
                    put_file(p, work_key(show, ep, p.relative_to(W)))
                    kept += 1
    log(f"{len(sent)} files queued for the cloud, {kept} work files kept")
    return {"uploads": len(sent), "work_files": kept}


# Segment caches only. `thumbs/` is the editor's clip previews — drop those and the timeline goes blank until
# ffmpeg remakes every frame (TAB editor after a visit).
RENDER_SCRATCH_DIRS = ("segs", "segs_groups", "segs_groups.auto", "bookends", "pieces")


def _dir_bytes(p):
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else 0


def drop_render_scratch(work, log=None):
    """Segment caches and concat leftovers. The editor uses proxy.mp4, not these. Always safe after a visit."""
    W = Path(work)
    freed, gone = 0, []
    if not W.is_dir():
        return {"freed": 0, "removed": []}
    for name in RENDER_SCRATCH_DIRS:
        p = W / name
        if p.is_dir():
            freed += _dir_bytes(p)
            shutil.rmtree(p, ignore_errors=True)
            gone.append(name)
    for p in [*W.glob("silent_*.mp4"), *W.glob("concat*.txt"), *W.glob("*.mp4.ok")]:
        if p.is_file():
            freed += p.stat().st_size
            p.unlink(missing_ok=True)
            gone.append(p.name)
    if log:
        log(f"dropped {freed / 1e9:.2f} GB of render scratch ({', '.join(gone) or 'none'})")
    return {"freed": freed, "removed": gone}


def drop_render_scratch_tree(root, log=None, skip_dirs=None):
    """Scratch folders in this directory and each immediate child (library/work/<show>_epN)."""
    root = Path(root)
    if not root.is_dir():
        return {"freed": 0, "folders": 0}
    skip = set()
    for p in skip_dirs or []:
        try:
            skip.add(str(Path(p).resolve()))
        except OSError:
            skip.add(str(p))
    targets = [root]
    try:
        targets.extend(p for p in root.iterdir() if p.is_dir())
    except OSError:
        pass
    freed = n = 0
    seen = set()
    for folder in targets:
        try:
            if str(folder.resolve()) in skip:
                continue
        except OSError:
            pass
        for name in RENDER_SCRATCH_DIRS:
            p = folder / name
            key = str(p)
            if key in seen or not p.is_dir():
                continue
            seen.add(key)
            freed += _dir_bytes(p)
            shutil.rmtree(p, ignore_errors=True)
            n += 1
    if log and n:
        log(f"disk: dropped {n} scratch folders ({freed / 1e9:.2f} GB) under {root}")
    return {"freed": freed, "folders": n}


def _library_work_root(work):
    """library/ and library/work/ from an episode work folder (`…/work/TABPGCIT_ep31`)."""
    W = Path(work).resolve()
    if W.parent.name == "work":
        return W.parent.parent, W.parent
    return W.parent, W.parent


def free_disk(work, log=print, db=None, keep_episode_id=None):
    """Make room on this box. Segment caches were not the only thing filling Render's 50 GB: published
    mp4s, leftover tars, and other episodes' lips/proxies stayed after CHAIN OK (TAB EP31 mksrt ENOSPC
    at 0.0 GB, scratch drop found none). Published files are already in R2."""
    W = Path(work)
    keep = W.resolve() if W.exists() else W
    _, work_root = _library_work_root(W)
    if not work_root.is_dir():
        work_root = W.parent if W.parent.is_dir() else W
    freed, gone = 0, []

    def drop_file(p, label=None):
        nonlocal freed
        if not p.is_file():
            return
        try:
            sz = p.stat().st_size
            p.unlink()
            freed += sz
            gone.append(label or p.name)
        except OSError:
            pass

    def drop_dir(p, label=None):
        nonlocal freed
        if not p.is_dir():
            return
        try:
            sz = _dir_bytes(p)
            shutil.rmtree(p, ignore_errors=True)
            freed += sz
            gone.append(label or p.name)
        except OSError:
            pass

    for folder in (work_root, W, Path("/tmp")):
        if not folder.is_dir():
            continue
        for p in [*folder.glob("_*.tar.gz"), *folder.glob("_*.tar.gz.part"), *folder.glob("*.tar.gz.part")]:
            drop_file(p)

    kids = [work_root]
    try:
        kids.extend(p for p in work_root.iterdir() if p.is_dir())
    except OSError:
        pass
    for folder in kids:
        r = drop_render_scratch(folder)
        freed += r["freed"]
        gone.extend(r["removed"])
        other = folder.resolve() != keep
        if other:
            for pat in ("video_*_nosubs.mp4", "clean_noglow.mp4", "video_groups.mp4", "video_groups.auto.mp4"):
                for p in folder.glob(pat):
                    drop_file(p, f"{folder.name}/{p.name}")
        for p in folder.glob("trans_*"):
            if p.is_dir():
                drop_dir(p, f"{folder.name}/{p.name}")

    if db is not None:
        try:
            rows = db.q("SELECT path, episode_id FROM uploads WHERE status='done'")
        except Exception:
            rows = []
        for u in rows:
            if keep_episode_id is not None and u.get("episode_id") == keep_episode_id:
                continue
            p = Path(u.get("path") or "")
            if p.suffix.lower() in (".mp4", ".mov", ".mkv"):
                drop_file(p, p.name)

    try:
        free = shutil.disk_usage(work_root).free
    except OSError:
        free = 0
    if free < 2 * 10**9:
        for folder in kids:
            if folder.resolve() == keep:
                continue
            for name in ("lips_nets", "lips_mesh"):
                drop_dir(folder / name, f"{folder.name}/{name}")
            for name in ("proxy_540.mp4", "proxy.mp4"):
                drop_file(folder / name, f"{folder.name}/{name}")

    if log:
        sample = ", ".join(gone[:10]) or "nothing extra"
        log(f"freed {freed / 1e9:.2f} GB on this box ({sample}{'…' if len(gone) > 10 else ''})")
    return {"freed": freed, "removed": gone}


# Daily sweeper: leftover pack tars, render segs, and local copies of finished episodes that are already in R2.
# CLEAN_WORK stays false on the per-episode cleanup step (proxy/lips stay for a same-day editor visit).
CLEANUP_INTERVAL = 24 * 3600
CLEANUP_MARKER = ".disk_cleanup_at"
KEEP_TAR_SECONDS = 2 * 3600          # a pack still being written
DONE_AGE = 24 * 3600                 # strip a finished episode the next day
TIGHT_DISK = 40 * 10**9              # also strip younger done episodes when free space is under this
LOW_WORK_DISK = 20 * 10**9           # then also drop proxy / lips of done episodes
KEEP_EPISODE_STATUSES = frozenset({"new", "queued", "processing", "review"})
INTERMEDIATE_VIDEO_PATS = ("video_*_nosubs.mp4", "clean_noglow.mp4", "video_groups.mp4", "video_groups.auto.mp4")


def cleanup_marker(library):
    return Path(library) / CLEANUP_MARKER


def cleanup_due(library, interval=CLEANUP_INTERVAL, now_ts=None):
    now_ts = now_ts or time.time()
    try:
        last = float(cleanup_marker(library).read_text().strip().split()[0])
    except Exception:
        return True
    return now_ts - last >= interval


def mark_cleanup(library, now_ts=None, freed=0):
    now_ts = now_ts or time.time()
    p = cleanup_marker(library)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{now_ts:.0f} {freed}\n")
    except OSError:
        pass


def _tar_leftovers(folder):
    if not folder.is_dir():
        return []
    try:
        return [*folder.glob("_*.tar.gz"), *folder.glob("_*.tar.gz.part"), *folder.glob("*.tar.gz.part")]
    except OSError:
        return []


def _resolved(p):
    try:
        return str(Path(p).resolve())
    except OSError:
        return str(p)


def daily_cleanup(db, library, now_ts=None, min_age=DONE_AGE, keep_episode_ids=None, log=print):
    """Drop leftover pack tars, render segs, and big local copies of finished episodes that are already in R2.

    Processing / queued / review / currently-running episodes are left alone. The editor's thumbs, json, srt
    stay. Inputs and published masters of a done episode come back from R2 if a later retry needs them."""
    now_ts = now_ts or time.time()
    lib = Path(library)
    work_root = lib / "work" if (lib / "work").is_dir() else lib
    keep_ids = set(keep_episode_ids or ())
    keep_dirs = set()
    done_eps = []
    try:
        rows = db.q("SELECT id, status, work_dir, output_dir, updated FROM episodes") if db is not None else []
    except Exception:
        rows = []
    for e in rows:
        active = e["id"] in keep_ids or (e.get("status") or "") in KEEP_EPISODE_STATUSES
        for d in (e.get("work_dir"), e.get("output_dir")):
            if d and active:
                keep_dirs.add(_resolved(d))
        if (e.get("status") or "") == "done" and e["id"] not in keep_ids:
            done_eps.append(e)

    try:
        free = shutil.disk_usage(lib if lib.exists() else Path.cwd()).free
    except OSError:
        free = 0
    age_cut = now_ts if free < TIGHT_DISK else (now_ts - min_age)
    targets = [e for e in done_eps if (e.get("updated") or 0) <= age_cut]
    keep_work = free >= LOW_WORK_DISK

    freed, gone = 0, []

    def drop_file(p, label=None):
        nonlocal freed
        if not p.is_file():
            return
        try:
            sz = p.stat().st_size
            p.unlink()
            freed += sz
            gone.append(label or p.name)
        except OSError:
            pass

    def drop_dir(p, label=None):
        nonlocal freed
        if not p.is_dir():
            return
        try:
            sz = _dir_bytes(p)
            shutil.rmtree(p, ignore_errors=True)
            freed += sz
            gone.append(label or p.name)
        except OSError:
            pass

    tar_roots = [lib, work_root, Path("/tmp")]
    try:
        tar_roots.extend(p for p in work_root.iterdir() if p.is_dir() and _resolved(p) not in keep_dirs)
    except OSError:
        pass
    for folder in tar_roots:
        for p in _tar_leftovers(folder):
            try:
                if now_ts - p.stat().st_mtime < KEEP_TAR_SECONDS:
                    continue
            except OSError:
                continue
            drop_file(p, p.name)

    skip = [Path(d) for d in keep_dirs]
    r = drop_render_scratch_tree(work_root, skip_dirs=skip)
    freed += r["freed"]
    if r["folders"]:
        gone.append(f"{r['folders']} scratch folders")

    for e in targets:
        W = Path(e.get("work_dir") or "")
        if W.is_dir() and _resolved(W) not in keep_dirs:
            out = clean_scratch(db, e, keep_work=keep_work, log=lambda m: None)
            freed += out["freed"]
            gone.extend(f"{W.name}/{n}" for n in out["removed"])
            for pat in INTERMEDIATE_VIDEO_PATS:
                for p in W.glob(pat):
                    drop_file(p, f"{W.name}/{p.name}")
            for p in W.glob("trans_*"):
                if p.is_dir():
                    drop_dir(p, f"{W.name}/{p.name}")

    if db is not None:
        try:
            uploads = db.q("SELECT path, episode_id FROM uploads WHERE status='done'")
        except Exception:
            uploads = []
        target_ids = {e["id"] for e in targets}
        for u in uploads:
            if u.get("episode_id") not in target_ids:
                continue
            p = Path(u.get("path") or "")
            if p.suffix.lower() in (".mp4", ".mov", ".mkv"):
                drop_file(p, p.name)

    if log:
        sample = ", ".join(gone[:8]) or "nothing extra"
        log(f"daily cleanup: {freed / 1e9:.2f} GB ({sample}{'…' if len(gone) > 8 else ''})")
    return {"freed": freed, "removed": gone, "episodes": len(targets)}


def maybe_daily_cleanup(db, library, now_ts=None, interval=CLEANUP_INTERVAL, min_age=DONE_AGE,
                        keep_episode_ids=None, force=False, log=print):
    """Run `daily_cleanup` at most once per day (or now if `force`)."""
    now_ts = now_ts or time.time()
    if not force and not cleanup_due(library, interval=interval, now_ts=now_ts):
        return None
    out = daily_cleanup(db, library, now_ts=now_ts, min_age=min_age,
                        keep_episode_ids=keep_episode_ids, log=log)
    mark_cleanup(library, now_ts=now_ts, freed=out.get("freed") or 0)
    return out


def clean_scratch(db, ep, keep_work=False, log=print):
    """After an episode is delivered and its files are in R2: drop the big local files (the uploaded video and
    audio, the preview copy, the rendered pieces) so the next episode has room. Segment caches always go;
    proxy / lips stay unless `keep_work` is false."""
    W = Path(ep["work_dir"])
    scratch = drop_render_scratch(W)
    freed, gone = scratch["freed"], list(scratch["removed"])
    for a in db.q("SELECT * FROM assets WHERE episode_id=? AND r2_key IS NOT NULL", (ep["id"],)):
        p = Path(a["path"])
        if p.exists():                                   # safe: the file is in R2 and comes back with `fetch`
            freed += p.stat().st_size; p.unlink(); gone.append(a["kind"])
    if not keep_work and W.is_dir():
        for name in ("proxy.mp4", "lips_nets", "lips_mesh", "clean_noglow.mp4"):
            p = W / name
            if p.is_file():
                freed += p.stat().st_size; p.unlink(); gone.append(name)
            elif p.is_dir():
                freed += _dir_bytes(p)
                shutil.rmtree(p, ignore_errors=True); gone.append(name)
    log(f"cleaned {freed / 1e9:.2f} GB ({', '.join(gone) or 'nothing to clean'})")
    return {"freed": freed, "removed": gone}


# ---------------- the database file ----------------
DB_KEY = "db/dubstudio.db"


def push_db(conn, library, history=True):
    """Copy the live database (SQLite's own backup, safe while the app writes) to R2: db/dubstudio.db and a dated
    copy under db/history/."""
    _require()
    if not isinstance(conn, sqlite3.Connection):      # Postgres: its own managed backups, nothing to copy up here
        return {"skipped": "the database is Postgres: its backups are the service's own", "at": time.time()}
    tmp = Path(library) / "backups" / "r2-upload.db"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    dst = sqlite3.connect(str(tmp))
    try:
        with dst:
            conn.backup(dst)
    finally:
        dst.close()
    key = _key(DB_KEY)
    out = put_file(tmp, key)
    if history:
        day = datetime.now().strftime("%Y-%m-%d")
        out["history"] = put_file(tmp, _key(f"db/history/dubstudio-{day}.db"))["key"]
    tmp.unlink(missing_ok=True)
    out["at"] = time.time()
    return out


def pull_db(library):
    """Bring the copy in R2 down next to the library (never over the live file): <library>/restored/."""
    _require()
    out = Path(library) / "restored" / f"dubstudio-r2-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    got = download(_key(DB_KEY), out)
    return {"path": got["path"], "size": got["size"], "key": _key(DB_KEY)}


def db_status():
    if not configured():
        return {"configured": False}
    h = head(_key(DB_KEY))
    return {"configured": True, "key": _key(DB_KEY), "size": (h or {}).get("size"), "modified": (h or {}).get("modified"),
            "present": bool(h)}
