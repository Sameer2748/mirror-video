"""Cloud storage (Cloudflare R2): the signed S3 requests, the upload queue of an episode, and the database copy.

No real bucket is touched: a small S3-shaped HTTP server runs on localhost and keeps the objects in memory.
The live round trip against the owner's real bucket is `test_live_r2` (only with DUBSTUDIO_LIVE_R2=1).
"""
import os, threading, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import httpx
import pytest

from conftest import wait_for

BUCKET = "test-bucket"


class FakeS3(BaseHTTPRequestHandler):
    objects = {}
    parts = {}                                   # (key, upload id) -> {part number: bytes}

    def log_message(self, *a):
        pass

    # ---- helpers ----
    def _key(self):
        path = unquote(urlparse(self.path).path).lstrip("/")
        assert path.startswith(BUCKET), path
        return path[len(BUCKET):].lstrip("/")

    def _signed(self):
        q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        head = self.headers.get("Authorization") or ""
        if head:
            return head.startswith("AWS4-HMAC-SHA256 Credential=") and "Signature=" in head \
                and bool(self.headers.get("x-amz-date")) and bool(self.headers.get("x-amz-content-sha256"))
        return bool(q.get("X-Amz-Signature") and q.get("X-Amz-Credential") and q.get("X-Amz-Date"))

    def _deny(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)                      # read the body first, or the client sees a broken pipe
        self.send_response(403); self.end_headers()
        self.wfile.write(b"<Error><Message>SignatureDoesNotMatch</Message></Error>")

    # ---- the verbs the app uses ----
    def do_PUT(self):
        if not self._signed():
            return self._deny()
        q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if q.get("uploadId"):                            # one part of a big upload
            uid, part = q["uploadId"][0], int(q["partNumber"][0])
            FakeS3.parts.setdefault((self._key(), uid), {})[part] = body
            self.send_response(200); self.send_header("ETag", f'"etag-{part}"'); self.end_headers(); return
        FakeS3.objects[self._key()] = body
        self.send_response(200); self.send_header("ETag", '"fake"'); self.end_headers()

    def do_POST(self):
        if not self._signed():
            return self._deny()
        q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        key = self._key()
        if "uploads" in q:                               # start a big upload
            data = b"<InitiateMultipartUploadResult><UploadId>up-1</UploadId></InitiateMultipartUploadResult>"
        elif q.get("uploadId"):                          # finish it: the parts become one object, in order
            uid = q["uploadId"][0]
            got = FakeS3.parts.pop((key, uid), {})
            assert b"<CompleteMultipartUpload>" in body
            FakeS3.objects[key] = b"".join(got[k] for k in sorted(got))
            data = b"<CompleteMultipartUploadResult><ETag>\"done\"</ETag></CompleteMultipartUploadResult>"
        else:
            self.send_response(400); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._signed():
            return self._deny()
        q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        if q.get("list-type") == ["2"]:
            prefix = (q.get("prefix") or [""])[0]
            body = "<ListBucketResult>" + "".join(
                f"<Contents><Key>{k}</Key><Size>{len(v)}</Size><LastModified>2026-10-30T00:00:00Z</LastModified></Contents>"
                for k, v in sorted(FakeS3.objects.items()) if k.startswith(prefix)) + "</ListBucketResult>"
            data = body.encode()
        else:
            key = self._key()
            if key not in FakeS3.objects:
                self.send_response(404); self.end_headers(); self.wfile.write(b"<Error><Message>NoSuchKey</Message></Error>"); return
            data = FakeS3.objects[key]
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self):
        if not self._signed():
            return self._deny()
        key = self._key()
        if key not in FakeS3.objects:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Length", str(len(FakeS3.objects[key])))
        self.send_header("Last-Modified", "Thu, 30 Oct 2026 00:00:00 GMT"); self.end_headers()

    def do_DELETE(self):
        if not self._signed():
            return self._deny()
        q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        if q.get("uploadId"):                            # give up on a big upload
            FakeS3.parts.pop((self._key(), q["uploadId"][0]), None)
        else:
            FakeS3.objects.pop(self._key(), None)
        self.send_response(204); self.end_headers()


@pytest.fixture()
def r2(env, monkeypatch):
    """The app with R2 switched on, pointing at the fake bucket on localhost."""
    FakeS3.objects = {}; FakeS3.parts = {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeS3)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("R2_ENDPOINT", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("R2_BUCKET", BUCKET)
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "fake-key-id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "fake-secret")
    try:
        yield {**env, "objects": FakeS3.objects, "url": f"http://127.0.0.1:{srv.server_address[1]}"}
    finally:
        srv.shutdown()


# ---------------- the signing itself ----------------
def test_signing_key_matches_the_published_example():
    """AWS's own documented example of a derived signing key (so the signature maths is right)."""
    from dubstudio import storage
    key = storage._sign_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20150830", "us-east-1", "iam")
    assert key.hex() == "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9"


def test_off_until_the_keys_are_there(env):
    from dubstudio import storage
    assert storage.configured() is False
    assert env["client"].get("/api/storage/status").json()["configured"] is False
    with pytest.raises(RuntimeError):
        storage.put_bytes("x", b"y")


def test_put_get_head_list_and_delete(r2):
    from dubstudio import storage
    storage.put_bytes("folder/hello.txt", b"ciao", "text/plain")
    assert r2["objects"][f"folder/hello.txt"] == b"ciao"
    assert storage.get_bytes("folder/hello.txt") == b"ciao"
    assert storage.head("folder/hello.txt")["size"] == 4
    assert storage.head("folder/missing.txt") is None
    assert [o["key"] for o in storage.listing("folder")["objects"]] == ["folder/hello.txt"]
    storage.delete("folder/hello.txt")
    assert "folder/hello.txt" not in r2["objects"]
    st = storage.status()
    assert st["ok"] is True and st["bucket"] == BUCKET


def test_a_wrong_secret_is_refused(r2, monkeypatch):
    """The fake bucket only answers signed requests: an unsigned call must fail."""
    import httpx
    r = httpx.put(f"{r2['url']}/{BUCKET}/nope.txt", content=b"x")
    assert r.status_code == 403


def test_a_folder_inside_the_bucket(r2, monkeypatch):
    from dubstudio import storage
    monkeypatch.setenv("R2_PREFIX", "dubstudio")
    storage.put_bytes(storage._key("a", "b.txt"), b"1")
    assert "dubstudio/a/b.txt" in r2["objects"]


def test_a_big_file_goes_up_in_parts(r2, monkeypatch):
    """A source video is hundreds of MB or more: it is sent in parts, so a dropped line costs one part, not the file."""
    from dubstudio import storage
    monkeypatch.setattr(storage, "PART_BIG", 1024)
    monkeypatch.setattr(storage, "MULTIPART_FROM", 2048)
    big = Path(r2["tmp"]) / "big.bin"
    big.write_bytes(bytes(range(256)) * 40)                      # 10 240 bytes -> 10 parts
    seen = []
    out = storage.put_big_file(big, "shows/X/ep1/input/src_video/big.bin", on_progress=seen.append)
    assert out["parts"] == 10 and seen[-1] == 10240
    assert r2["objects"]["shows/X/ep1/input/src_video/big.bin"] == big.read_bytes()


def test_a_part_link_can_be_signed_for_the_browser(r2):
    from dubstudio import storage
    uid = storage.multipart_start("k/big.mp4")
    url = storage.multipart_url("k/big.mp4", uid, 3)
    assert "partNumber=3" in url and f"uploadId={uid}" in url and "X-Amz-Signature=" in url
    storage.multipart_abort("k/big.mp4", uid)


def test_download_link_works_without_keys(r2):
    from dubstudio import storage
    import httpx
    storage.put_bytes("shows/X/ep1/clean.mp4", b"video-bytes")
    url = storage.presign("shows/X/ep1/clean.mp4", seconds=300, download_name="clean.mp4")
    assert "X-Amz-Signature=" in url and "X-Amz-Expires=300" in url
    assert httpx.get(url).content == b"video-bytes"              # no Authorization header on this one


# ---------------- an episode's finished files ----------------
def episode_with_outputs(c, show_folder, names=("_final_clean.mp4", "_mapping.xlsx")):
    s = c.post("/api/shows", json={"name": "Show", "code": "SHOW", "folder": str(show_folder), "preset": "WBM"}).json()
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    d = c.get(f"/api/episodes/{ep['id']}/deliverables").json()
    made = []
    from pathlib import Path
    for f in d["files"]:
        if any(f["name"].endswith(x) for x in names):
            p = Path(f["path"]); p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * 2048)
            made.append(f["name"])
    assert made, [f["name"] for f in d["files"]]
    return s, ep, made


def test_episode_files_go_up_and_come_back_as_links(r2):
    c = r2["client"]
    s, ep, made = episode_with_outputs(c, r2["show"])
    r = c.post(f"/api/episodes/{ep['id']}/uploads")
    assert r.status_code == 200 and len(r.json()["queued"]) == len(made)
    rows = wait_for(lambda: [u for u in c.get(f"/api/episodes/{ep['id']}/uploads").json()["uploads"]
                             if u["status"] == "done"] or None, 20)
    assert len(rows) == len(made)
    assert all(u["key"].startswith("shows/SHOW/ep17/") for u in rows)
    assert all(r2["objects"][u["key"]] == b"x" * 2048 for u in rows)
    link = c.get(f"/api/uploads/{rows[0]['id']}/link").json()
    assert "X-Amz-Signature=" in link["url"] and link["name"] == rows[0]["name"]
    # the same file again is not sent twice
    again = c.post(f"/api/episodes/{ep['id']}/uploads").json()["queued"]
    assert [u["id"] for u in again] == sorted(u["id"] for u in rows)


def test_nothing_to_send_says_so(r2):
    c = r2["client"]
    s = c.post("/api/shows", json={"name": "Show", "code": "SHOW", "folder": str(r2["show"]), "preset": "WBM"}).json()
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 18}).json()
    r = c.post(f"/api/episodes/{ep['id']}/uploads")
    assert r.status_code == 409 and "render the episode first" in r.json()["detail"]


def test_a_failed_upload_can_be_tried_again(r2):
    c = r2["client"]
    s, ep, made = episode_with_outputs(c, r2["show"])
    from pathlib import Path
    import dubstudio.main as main
    rows = c.post(f"/api/episodes/{ep['id']}/uploads").json()["queued"]
    wait_for(lambda: all(u["status"] in ("done", "failed") for u in c.get(f"/api/episodes/{ep['id']}/uploads").json()["uploads"]), 20)
    one = c.get(f"/api/episodes/{ep['id']}/uploads").json()["uploads"][0]
    Path(one["path"]).unlink()                                   # the file is gone: the next try must fail clearly
    main.db().update("uploads", one["id"], status="waiting", sent=0)
    import dubstudio.storage as storage
    storage.start_worker(main.db())
    bad = wait_for(lambda: next((u for u in c.get(f"/api/episodes/{ep['id']}/uploads").json()["uploads"]
                                 if u["id"] == one["id"] and u["status"] == "failed"), None), 20)
    assert "the file is gone" in bad["error"]
    Path(one["path"]).write_bytes(b"x" * 10)
    assert c.post(f"/api/uploads/{one['id']}/retry").status_code == 200
    ok = wait_for(lambda: next((u for u in c.get(f"/api/episodes/{ep['id']}/uploads").json()["uploads"]
                                if u["id"] == one["id"] and u["status"] == "done"), None), 20)
    assert r2["objects"][ok["key"]] == b"x" * 10


def test_an_upload_belongs_to_its_episode(r2):
    """Another account may not list, link or retry an upload of someone else's episode."""
    from test_auth import second_client
    c = r2["client"]
    s, ep, made = episode_with_outputs(c, r2["show"])
    rows = wait_for(lambda: [u for u in c.post(f"/api/episodes/{ep['id']}/uploads").json()["queued"]] or None, 20)
    other, _ = second_client(r2)
    assert other.get(f"/api/episodes/{ep['id']}/uploads").status_code == 403
    assert other.get(f"/api/uploads/{rows[0]['id']}/link").status_code == 403
    assert other.post(f"/api/uploads/{rows[0]['id']}/retry").status_code == 403
    assert other.get("/api/storage/objects").status_code == 403
    assert other.post("/api/storage/db/push").status_code == 403


# ---------------- the database copy ----------------
def test_the_database_is_copied_up_and_can_come_back(r2):
    import dubstudio.main as main
    c = r2["client"]
    out = c.post("/api/storage/db/push")
    assert out.status_code == 200, out.text
    if main.db().kind == "postgres":          # Postgres keeps its own backups: there is no file to copy up
        assert "Postgres" in out.json()["skipped"]
        return
    assert "db/dubstudio.db" in r2["objects"] and len(r2["objects"]["db/dubstudio.db"]) > 1000
    assert any(k.startswith("db/history/dubstudio-") for k in r2["objects"])
    st = c.get("/api/storage/status").json()
    assert st["database"]["present"] is True and st["database"]["size"] > 1000
    back = c.post("/api/storage/db/pull").json()
    from pathlib import Path
    p = Path(back["path"])
    assert p.exists() and "restored" in p.parts and p.read_bytes()[:15] == b"SQLite format 3"
    import sqlite3
    assert sqlite3.connect(str(p)).execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 1


def test_a_finished_episode_is_sent_up_by_itself(r2):
    """The runner queues the deliverables when an episode turns "done" (R2_AUTO_UPLOAD, on by default)."""
    c = r2["client"]
    s, ep, made = episode_with_outputs(c, r2["show"])
    import dubstudio.main as main
    main.runner()._cloud_upload(ep["id"])
    rows = wait_for(lambda: [u for u in c.get(f"/api/episodes/{ep['id']}/uploads").json()["uploads"]
                             if u["status"] == "done"] or None, 20)
    assert len(rows) == len(made)


def test_auto_upload_can_be_switched_off(r2, monkeypatch):
    c = r2["client"]
    s, ep, made = episode_with_outputs(c, r2["show"])
    monkeypatch.setenv("R2_AUTO_UPLOAD", "false")
    import dubstudio.main as main
    main.runner()._cloud_upload(ep["id"])
    time.sleep(0.5)
    assert c.get(f"/api/episodes/{ep['id']}/uploads").json()["uploads"] == []


# ---------------- the editor's media on a machine that has no files (docs/09 §4) ----------------
def test_the_preview_copy_is_mirrored_and_then_served_from_the_cloud(r2):
    """On this Mac the proxy copy is on disk and a copy goes up; take the disk copy away (a deployed box, or after
    the scratch clean) and the editor is sent to R2 instead of getting a 404."""
    c = r2["client"]
    s = c.post("/api/shows", json={"name": "Show", "code": "SHOW", "folder": str(r2["show"]), "preset": "WBM"}).json()
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 21}).json()
    import httpx
    import dubstudio.main as main, dubstudio.timeline as tlm
    from dubstudio import storage
    e = main.db().one("SELECT * FROM episodes WHERE id=?", (ep["id"],))
    proxy = Path(e["work_dir"]) / tlm.PROXY
    proxy.parent.mkdir(parents=True, exist_ok=True)
    proxy.write_bytes(b"proxy-bytes")

    r = c.get(f"/api/episodes/{ep['id']}/media/src_proxy")
    assert r.status_code == 200 and r.content == b"proxy-bytes"          # served from disk
    key = storage.work_key(s, e, tlm.PROXY)
    wait_for(lambda: r2["objects"].get(key), 10)                         # and mirrored in the background
    assert r2["objects"][key] == b"proxy-bytes"

    proxy.unlink()                                                       # now this machine has nothing
    r = c.get(f"/api/episodes/{ep['id']}/media/src_proxy", follow_redirects=False)
    assert r.status_code == 307 and "X-Amz-Signature=" in r.headers["location"]
    assert httpx.get(r.headers["location"]).content == b"proxy-bytes"     # the browser streams it straight from R2


def test_a_media_file_that_is_nowhere_is_still_a_404(r2):
    c = r2["client"]
    s = c.post("/api/shows", json={"name": "Show", "code": "SHOW", "folder": str(r2["show"]), "preset": "WBM"}).json()
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 22}).json()
    assert c.get(f"/api/episodes/{ep['id']}/media/src_proxy").status_code == 404


def test_the_waveform_travels_with_the_episode(r2):
    """Measured once from the audio, kept in R2: a box without the audio file still draws the track."""
    c = r2["client"]
    s = c.post("/api/shows", json={"name": "Show", "code": "SHOW", "folder": str(r2["show"]), "preset": "WBM"}).json()
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 23}).json()
    import dubstudio.main as main
    from dubstudio import storage
    e = main.db().one("SELECT * FROM episodes WHERE id=?", (ep["id"],))
    cache = Path(e["work_dir"]) / "waveform_50.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text('{"per_s": 50, "peaks": [1, 2, 3]}')
    storage.put_file(cache, storage.work_key(s, e, cache.name))
    cache.unlink()
    assert c.get(f"/api/episodes/{ep['id']}/waveform").json()["peaks"] == [1, 2, 3]


def test_the_small_work_files_come_down_so_the_editor_can_open(r2):
    """The editor is built out of the small work files (the edit list, rows, lip-sync, SRTs). On a box that never
    processed the episode they have to come down from R2 -- but only those: not the big ones."""
    from dubstudio import storage
    c = r2["client"]
    s = c.post("/api/shows", json={"name": "Show", "code": "SHOW", "folder": str(r2["show"]), "preset": "WBM"}).json()
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 24}).json()
    import dubstudio.main as main
    e = main.db().one("SELECT * FROM episodes WHERE id=?", (ep["id"],))

    storage.put_bytes(storage.work_key(s, e, "manifest_groups.json"), b'{"segments": []}')
    storage.put_bytes(storage.work_key(s, e, "rows.json"), b'{"rows": []}')
    storage.put_bytes(storage.work_key(s, e, "SHOW_EP24.srt"), b"1\n")
    storage.put_bytes(storage.work_key(s, e, "scene/answer.json"), b"{}")
    storage.put_bytes(storage.work_key(s, e, "final_check.jpg"), b"\xff\xd8jpeg")
    storage.put_bytes(storage.work_key(s, e, "clean_noglow.mp4"), b"x" * 4096)      # big: stays in the cloud
    storage.put_bytes(storage.work_key(s, e, "thumbs/00001000_120.jpg"), b"thumb")  # thousands of these: not wanted

    got = storage.fetch_work_files(s, e)
    work = Path(e["work_dir"])
    assert got == 5
    assert (work / "manifest_groups.json").read_bytes() == b'{"segments": []}'
    assert (work / "scene" / "answer.json").exists() and (work / "final_check.jpg").exists()
    assert not (work / "clean_noglow.mp4").exists()
    assert not (work / "thumbs" / "00001000_120.jpg").exists()
    assert storage.fetch_work_files(s, e) == 0                 # once per episode, not on every request


def test_the_bin_lists_a_file_that_is_only_in_the_cloud(r2):
    """After `cleanup` the SRTs are not on disk any more. The bin still offers them, and downloading one goes
    straight to R2."""
    from dubstudio import storage
    from test_uploads_queue import episode_with_files
    c = r2["client"]
    s = c.post("/api/shows", json={"name": "Show", "code": "SHOW", "folder": str(r2["show"]), "preset": "WBM"}).json()
    ep = episode_with_files(c, s, number=25)                   # the episode needs its files for the plan to build
    import dubstudio.main as main
    e = main.db().one("SELECT * FROM episodes WHERE id=?", (ep["id"],))
    show, ep_row, assets, ctx = main._ep_ctx(ep["id"])
    srt = ctx.get("sub_srt")
    assert srt and not Path(srt).exists()                      # nothing was processed here
    storage.put_bytes(storage.work_key(s, e, Path(srt).name), b"1\n00:00:01,000 --> 00:00:02,000\nciao\n")

    files = c.get(f"/api/episodes/{ep['id']}/bin").json()["files"]
    row = next((f for f in files if f["kind"] == "dub_srt"), None)
    assert row and row["in_cloud"] is True and row["size"] > 0
    r = c.get(f"/api/episodes/{ep['id']}/bin/file/dub_srt", follow_redirects=False)
    assert r.status_code == 307 and "X-Amz-Signature=" in r.headers["location"]
    assert httpx.get(r.headers["location"]).content.endswith(b"ciao\n")


def test_an_input_is_found_even_when_the_row_has_no_key(r2):
    """An episode made from a Mac folder has no `r2_key`, and the copy in R2 may be under another name (a cloud
    step put it there). The one file in that kind's folder is used -- but only if there is exactly one."""
    from dubstudio import storage
    c = r2["client"]
    s = c.post("/api/shows", json={"name": "Show", "code": "SHOW", "folder": str(r2["show"]), "preset": "WBM"}).json()
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 26}).json()
    import dubstudio.main as main
    e = main.db().one("SELECT * FROM episodes WHERE id=?", (ep["id"],))
    # an episode made from a Mac folder: a path on disk, no r2_key
    main.db().insert("assets", episode_id=e["id"], kind="dub_audio", role="input",
                     path=str(Path(e["work_dir"]) / "Episode 26 original name.mp3"), size=5)
    a = main.db().one("SELECT * FROM assets WHERE episode_id=? AND kind='dub_audio'", (e["id"],))
    assert main._input_in_cloud(s, e, a, "dub_audio") is None

    storage.put_bytes(f"{storage.episode_prefix(s, e)}/input/dub_audio/dub.mp3", b"audio")
    assert main._input_in_cloud(s, e, a, "dub_audio").endswith("/dub_audio/dub.mp3")
    r = c.get(f"/api/episodes/{ep['id']}/media/dub_audio", follow_redirects=False)
    assert r.status_code == 307

    storage.put_bytes(f"{storage.episode_prefix(s, e)}/input/dub_audio/another.mp3", b"audio")
    assert main._input_in_cloud(s, e, a, "dub_audio") is None   # two candidates: do not guess


def test_the_owner_can_save_the_keys_from_the_settings_page(r2, monkeypatch):
    """PUT /api/settings/storage writes app/.env; the keys are never sent back."""
    c = r2["client"]
    for k in ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(k, raising=False)                     # the env wins over the file: take it away first
    r = c.put("/api/settings/storage", json={"account_id": "acc123", "bucket": "mybucket", "endpoint": r2["url"],
                                             "key_id": "k1", "secret": "s1", "prefix": "studio"})
    assert r.status_code == 200
    body = r.json()
    assert body["bucket"] == "mybucket" and body["prefix"] == "studio" and body["keys_set"] is True
    assert "secret" not in str(body) and "s1" not in str(body)
    env_text = (r2["tmp"] / ".env").read_text()
    assert "R2_BUCKET=mybucket" in env_text and "R2_SECRET_ACCESS_KEY=s1" in env_text
    assert oct((r2["tmp"] / ".env").stat().st_mode)[-3:] == "600"
    # an empty key field keeps the saved key
    c.put("/api/settings/storage", json={"bucket": "mybucket", "endpoint": r2["url"], "key_id": "", "secret": ""})
    assert "R2_SECRET_ACCESS_KEY=s1" in (r2["tmp"] / ".env").read_text()


@pytest.mark.skipif(not os.environ.get("DUBSTUDIO_LIVE_R2"), reason="set DUBSTUDIO_LIVE_R2=1 for a real round trip")
def test_live_r2(env):
    """One real object in the owner's bucket: put, list, download link, delete (a few bytes, costs nothing)."""
    import dubstudio.envfile as envfile_mod, importlib
    from pathlib import Path
    monkey = Path(__file__).resolve().parents[2] / ".env"        # the real app/.env (not the test one)
    envfile_mod.ROOT = monkey.parent
    envfile_mod._cache["mtime"] = 0
    from dubstudio import storage
    assert storage.configured(), "no R2 keys in app/.env"
    key = storage._key("checks", f"live-{int(time.time())}.txt")
    storage.put_bytes(key, b"dub studio live check", "text/plain")
    try:
        assert storage.get_bytes(key) == b"dub studio live check"
        assert any(o["key"] == key for o in storage.listing("checks")["objects"])
        import httpx
        assert httpx.get(storage.presign(key, 120)).content == b"dub studio live check"
    finally:
        storage.delete(key)
    assert storage.head(key) is None


# ---------------- the queue / events services (local Docker: app/docker-compose.yml) ----------------
@pytest.mark.skipif(not os.environ.get("REDIS_URL"), reason="set REDIS_URL (docker-compose up) for the Redis check")
def test_live_events_go_through_redis():
    """With REDIS_URL set, every event goes through Redis, so a browser on any API replica sees a step running on
    any worker. Without it the events stay in this process (one machine)."""
    import asyncio, queue
    from dubstudio.events import Bus
    a, b = Bus(), Bus()                                   # two 'processes'
    assert a.connect_redis(os.environ["REDIS_URL"]) and b.connect_redis(os.environ["REDIS_URL"])
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    q = b.subscribe(loop)
    time.sleep(0.4)                                       # the subscriber is listening
    a.publish("job", 7, 3, store=False, step="render", status="running")
    got = asyncio.run_coroutine_threadsafe(asyncio.wait_for(q.get(), 5), loop).result()
    assert got["kind"] == "job" and got["episode_id"] == 7 and got["data"]["step"] == "render"
    loop.call_soon_threadsafe(loop.stop)


def test_clean_scratch_always_drops_segment_caches(tmp_path):
    """CLEAN_WORK defaults off so the editor's proxy stays, but segs_groups must still go or Render fills up
    (TAB EP53: 50 GB gone after CHAIN OK because cleanup never ran and segs were not in the delete list)."""
    from dubstudio import storage
    work = tmp_path / "work"
    (work / "segs_groups" / "000.mp4").parent.mkdir(parents=True)
    (work / "segs_groups" / "000.mp4").write_bytes(b"x" * 200)
    (work / "segs" / "a.mp4").parent.mkdir(parents=True)
    (work / "segs" / "a.mp4").write_bytes(b"y")
    (work / "proxy.mp4").write_bytes(b"proxy")
    (work / "config.json").write_text("{}")
    (work / "silent_groups.mp4").write_bytes(b"silent")
    (work / "thumbs" / "00000000_80.jpg").parent.mkdir()
    (work / "thumbs" / "00000000_80.jpg").write_bytes(b"jpg")

    class FakeDb:
        def q(self, *a, **k):
            return []

    out = storage.clean_scratch(FakeDb(), {"id": 1, "work_dir": str(work)}, keep_work=True, log=lambda m: None)
    assert "segs_groups" in out["removed"] and "segs" in out["removed"]
    assert "silent_groups.mp4" in out["removed"]
    assert not (work / "segs_groups").exists()
    assert not (work / "segs").exists()
    assert not (work / "silent_groups.mp4").exists()
    assert (work / "proxy.mp4").exists()
    assert (work / "config.json").exists()
    assert (work / "thumbs" / "00000000_80.jpg").exists()


def test_drop_render_scratch_tree_clears_neighbour_episodes(tmp_path):
    from dubstudio import storage
    lib = tmp_path / "work"
    for n in (52, 53):
        segs = lib / f"TAB_ep{n}" / "segs_groups"
        segs.mkdir(parents=True)
        (segs / "000.mp4").write_bytes(b"x" * 50)
    out = storage.drop_render_scratch_tree(lib, log=lambda m: None)
    assert out["folders"] == 2
    assert not (lib / "TAB_ep52" / "segs_groups").exists()
    assert not (lib / "TAB_ep53" / "segs_groups").exists()


def test_free_disk_drops_published_videos_when_scratch_is_already_gone(tmp_path, monkeypatch):
    """TAB EP31: 0.0 GB free, segs already absent, so the old scratch drop freed nothing and mksrt
    could not download a small SRT tar. Published mp4s and a neighbour's intermediates must go."""
    import shutil
    from dubstudio import storage
    lib = tmp_path / "library"
    work_root = lib / "work"
    cur = work_root / "TABPGCIT_ep31"
    other = work_root / "TABPGCIT_ep52"
    cur.mkdir(parents=True); other.mkdir(parents=True)
    (cur / "proxy_540.mp4").write_bytes(b"P" * 80)
    (other / "video_groups_nosubs.mp4").write_bytes(b"V" * 400)
    (other / "lips_nets" / "a.json").parent.mkdir()
    (other / "lips_nets" / "a.json").write_bytes(b"L" * 200)
    (other / "proxy_540.mp4").write_bytes(b"Q" * 120)
    published = tmp_path / "out" / "TABPGCIT_EP52_IT_final_clean.mp4"
    published.parent.mkdir()
    published.write_bytes(b"M" * 800)
    leftover = work_root / "_57_mksrt_dub_work_back.tar.gz"
    leftover.write_bytes(b"T" * 300)

    class DB:
        def q(self, *a, **k):
            return [{"path": str(published), "episode_id": 52}]

    monkeypatch.setattr(shutil, "disk_usage", lambda p: type("U", (), {"free": 0, "used": 50e9, "total": 50e9})())
    out = storage.free_disk(cur, log=lambda m: None, db=DB(), keep_episode_id=31)
    assert out["freed"] >= 800 + 400 + 300
    assert not published.exists()
    assert not leftover.exists()
    assert not (other / "video_groups_nosubs.mp4").exists()
    assert not (other / "lips_nets").exists()
    assert not (other / "proxy_540.mp4").exists()
    assert (cur / "proxy_540.mp4").exists()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="set DATABASE_URL (docker-compose up) for the Postgres check")
def test_the_app_runs_on_postgres(env):
    """The same suite runs end to end on Postgres; this one states it plainly."""
    import dubstudio.main as main
    assert main.db().kind == "postgres"
    c = env["client"]
    s = c.post("/api/shows", json={"name": "PG", "code": "PGS", "preset": "WBM"}).json()
    assert s["id"] and c.get(f"/api/shows/{s['id']}").json()["code"] == "PGS"
    assert c.get("/api/auth/me").json()["user"]["role"] == "admin"


@pytest.mark.skipif(not os.environ.get("DUBSTUDIO_PG"), reason="needs a Postgres (DUBSTUDIO_PG=postgresql://…)")
def test_two_processes_can_start_against_the_same_postgres_at_once(monkeypatch):
    """Render runs WEB_CONCURRENCY uvicorn processes, so two of them build the schema at the same moment.
    `CREATE TABLE IF NOT EXISTS` is not atomic against another session doing the same: both look, both see nothing,
    both create, and the loser died with "duplicate key value violates pg_type_typname_nsp_index". It happened on
    the very first real deploy."""
    import threading as th
    import psycopg
    from dubstudio.db import DB
    base = os.environ["DUBSTUDIO_PG"]
    name = "ds_race_test"
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name}")
        c.execute(f"CREATE DATABASE {name}")
    url = base.rsplit("/", 1)[0] + "/" + name
    monkeypatch.setenv("DATABASE_URL", url)

    errors, dbs, ready = [], [], th.Barrier(4)

    def start():
        try:
            ready.wait(timeout=20)                 # all four at the same instant, which is the whole point
            dbs.append(DB(None))
        except Exception as ex:
            errors.append(repr(ex))

    ts = [th.Thread(target=start) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert not errors, errors
    assert len(dbs) == 4
    assert dbs[0].one("SELECT COUNT(*) AS n FROM users")["n"] == 0
    for d in dbs:
        d.close()
