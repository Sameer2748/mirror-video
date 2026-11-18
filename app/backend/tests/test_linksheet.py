"""Link-sheet download: parse the four columns, fetch the files, one download at a time.

Runs against a throwaway database and a local file server. It never starts the pipeline loop, so a
download cannot take a CPU slot.
"""
import os
os.environ["DATABASE_URL"] = "sqlite"

import copy
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dubstudio import linksheet, presets, storage
from dubstudio.db import DB, now
from dubstudio.runner import Runner

storage.configured = lambda: False

ROOT = Path(tempfile.mkdtemp(prefix="linksheet-"))
MEDIA = ROOT / "media"
MEDIA.mkdir()


def _make_media():
    import subprocess
    subprocess.check_call([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "1",
        "-pix_fmt", "yuv420p", str(MEDIA / "ep.mp4"),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        str(MEDIA / "ep.mp3"),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (MEDIA / "ep.txt").write_text("Francis looked at Lewis.\n", encoding="utf-8")
    (MEDIA / "page.html").write_text("<html><body>not a file</body></html>", encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        name = self.path.split("?")[0].lstrip("/")
        path = MEDIA / name
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        return


def _wait(db, iid, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        row = db.one("SELECT status, message FROM link_imports WHERE id=?", (iid,))
        if row["status"] in ("done", "failed"):
            return row
        time.sleep(0.05)
    raise AssertionError(f"import {iid} still running")


def main():
    _make_media()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    csv_path = ROOT / "links.csv"
    csv_path.write_text(
        "Episode number,Source video link,Audio link,Script link\n"
        f"7,{base}/ep.mp4,{base}/ep.mp3,{base}/ep.txt\n"
        f"8,{base}/ep.mp4,{base}/ep.mp3,{base}/ep.txt\n",
        encoding="utf-8",
    )
    rows = linksheet.parse_sheet(csv_path)
    assert [r["number"] for r in rows] == [7, 8], rows

    headed = ROOT / "headed.csv"
    headed.write_text(
        "episode,source video url,target audio,target script\n"
        f"151,{base}/ep.mp4,{base}/ep.mp3,{base}/ep.txt\n",
        encoding="utf-8",
    )
    one = linksheet.parse_sheet(headed)
    assert one[0]["number"] == 151 and one[0]["video"].endswith("/ep.mp4"), one

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([9, f"{base}/ep.mp4", f"{base}/ep.mp3", f"{base}/ep.txt"])
    xlsx = ROOT / "plain.xlsx"
    wb.save(xlsx)
    assert linksheet.parse_sheet(xlsx)[0]["number"] == 9

    dup = ROOT / "dup.csv"
    dup.write_text(f"1,{base}/a,{base}/b,{base}/c\n1,{base}/a,{base}/b,{base}/c\n", encoding="utf-8")
    try:
        linksheet.parse_sheet(dup)
        raise SystemExit("duplicate episode numbers should be refused")
    except ValueError as ex:
        assert "twice" in str(ex)

    assert "export=download" in linksheet.direct_url("https://drive.google.com/file/d/abc123/view")
    assert linksheet.direct_url("https://docs.google.com/document/d/DOCID/edit").endswith("/export?format=docx")
    assert linksheet.direct_url("https://www.dropbox.com/s/xyz/ep.mp4?dl=0").endswith("dl=1")

    try:
        linksheet.fetch(f"{base}/page.html", ROOT / "bad", "src_video")
        raise SystemExit("a web page should be refused")
    except ValueError as ex:
        assert "web page" in str(ex) or "did not come back" in str(ex)

    db = DB(ROOT / "test.db")
    work = ROOT / "show"
    profile = copy.deepcopy(presets.BASE)
    profile.update(dub_lang="de", src_lang="en", labels="DE,US", dub_label="DE", sub_style="box")
    sid = db.insert("shows", name="Link test", code="LINKTEST", folder=str(work), profile=profile,
                    created=now(), updated=now())
    ids = {}
    for number in (7, 8):
        ep_dir = work / f"ep{number}"
        eid = db.insert("episodes", show_id=sid, number=number, status="new",
                        work_dir=str(ep_dir / "work"), output_dir=str(ep_dir / "out"),
                        created=now(), updated=now())
        ids[number] = eid
        src = next(r for r in rows if r["number"] == number)
        db.insert("link_imports", show_id=sid, episode_id=eid, number=number,
                  src_url=src["video"], audio_url=src["audio"], script_url=src["script"],
                  status="waiting", message="waiting", created=now())

    held = threading.Event()
    release = threading.Event()
    real_fetch = linksheet.fetch

    def slow_fetch(url, dest, kind):
        if kind == "src_video" and not held.is_set():
            held.set()
            assert release.wait(20)
        return real_fetch(url, dest, kind)

    linksheet.fetch = slow_fetch
    runner = Runner(db)
    first = db.one("SELECT id FROM link_imports WHERE number=7")["id"]
    second = db.one("SELECT id FROM link_imports WHERE number=8")["id"]
    running_before = db.one("SELECT COUNT(*) AS n FROM jobs WHERE status='running'")["n"]
    runner._tick_link_imports()
    assert held.wait(10), "the first download never started"
    time.sleep(0.2)
    runner._tick_link_imports()
    assert db.one("SELECT status FROM link_imports WHERE id=?", (second,))["status"] == "waiting"
    assert db.one("SELECT status FROM link_imports WHERE id=?", (first,))["status"] == "running"
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE status='running'")["n"] == running_before
    release.set()
    done = _wait(db, first)
    assert done["status"] == "done", done
    ep = db.one("SELECT status FROM episodes WHERE id=?", (ids[7],))
    assert ep["status"] == "processing", ep
    assets = db.q("SELECT kind, path FROM assets WHERE episode_id=? AND role='input'", (ids[7],))
    kinds = {a["kind"] for a in assets}
    assert kinds == {"src_video", "dub_audio", "dub_script"}, kinds
    for a in assets:
        assert Path(a["path"]).is_file() and Path(a["path"]).stat().st_size > 0
    waiting_jobs = db.q("SELECT status, resource FROM jobs WHERE episode_id=?", (ids[7],))
    assert waiting_jobs and all(j["status"] == "waiting" for j in waiting_jobs)
    assert not any(j["status"] == "running" for j in db.q("SELECT status FROM jobs"))

    runner._tick_link_imports()
    done2 = _wait(db, second)
    assert done2["status"] == "done", done2
    assert db.one("SELECT COUNT(*) AS n FROM link_imports WHERE status='running'")["n"] == 0
    print("link sheet ok:", len(waiting_jobs), "pipeline steps queued, none started by the download")
    server.shutdown()
    shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
