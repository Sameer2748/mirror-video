"""Export CSV: a single run of finished episodes from the start of the show.

The first episode that is not done, or whose subtitle video is not in the cloud, ends the list.
Later episodes that are already done are not included.

Throwaway SQLite database, R2 faked: no real bucket is touched.
"""
import os
os.environ["DATABASE_URL"] = "sqlite"

import csv
import io
import tempfile
import time
from pathlib import Path

from fastapi import HTTPException

from dubstudio import storage
from dubstudio.db import DB, now

storage.configured = lambda: True
storage._export_secret = lambda: b"test-export-secret"
storage.presign = lambda key, seconds=3600, method="GET", download_name=None, params=None: (
    f"https://r2.test/{key}?expires={seconds}&name={download_name}")

from starlette.requests import Request

from dubstudio import main  # noqa: E402  (after the patches above)


def fake_request():
    return Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "https", "path": "/export", "raw_path": b"/export",
        "query_string": b"", "headers": [(b"host", b"mirror.test")],
        "client": ("127.0.0.1", 1), "server": ("mirror.test", 443),
    })

ROOT = Path(tempfile.mkdtemp(prefix="export-csv-"))
db = DB(ROOT / "test.db")
main.state["db"] = db          # the endpoint reads the database from here; no server is started


def make_show(code, numbers, statuses, uploads=None):
    sid = db.insert("shows", code=code, name=code, folder=str(ROOT / code), profile={}, created=now(), updated=now())
    eids = {}
    for n, st in zip(numbers, statuses):
        eid = db.insert("episodes", show_id=sid, number=n, status=st, work_dir=str(ROOT / code / f"w{n}"),
                        output_dir=str(ROOT / code / f"o{n}"), created=now(), updated=now())
        eids[n] = eid
        if uploads and n in uploads:
            db.insert("uploads", episode_id=eid, kind="dub_subs", name=f"{code}_EP{n}_final_it_subs.mp4",
                      path="x", bucket="b", key=f"shows/{code}/ep{n}/out/{code}_EP{n}_final_it_subs.mp4",
                      size=1, sent=1, status="done", created=time.time())
    return sid


def run(sid):
    main.state["db"] = db
    try:
        resp = main.export_show_csv(sid, request=fake_request())
        return resp.status_code, resp.body.decode()
    except HTTPException as ex:
        return ex.status_code, ex.detail


def rows_of(text):
    return list(csv.reader(io.StringIO(text)))


def main_test():
    # 1. every episode done and each has its subtitle video -> CSV with one row per episode
    sid = make_show("ALLDONE", [11, 12, 13], ["done", "done", "done"], uploads=[11, 12, 13])
    code, body = run(sid)
    assert code == 200, (code, body)
    rows = rows_of(body)
    assert rows[0] == ["media_file_title", "media_url"], rows[0]
    assert [r[0] for r in rows[1:]] == ["11", "12", "13"], rows
    url = rows[1][1]
    assert url.startswith("https://pub-f3cfc243bd7b45dab6be4e8f05fe937d.r2.dev/"), url
    assert url.endswith("/shows/ALLDONE/ep11/out/ALLDONE_EP11_final_it_subs.mp4"), url
    assert "/ep12/out/ALLDONE_EP12_final_it_subs.mp4" in rows[2][1]
    assert "/ep13/out/ALLDONE_EP13_final_it_subs.mp4" in rows[3][1]
    assert "ep11" not in rows[2][1] and "ep12" not in rows[1][1]
    assert "?" not in url, url                              # MediaConvert strips query strings
    assert url.endswith(".mp4"), url
    print("ok: all done -> CSV with query-free .mp4 links")

    # 2. episode 2 still processing, episode 4 already done -> stop at 2, only episode 1
    sid = make_show("NOTDONE", [1, 2, 4], ["done", "processing", "done"], uploads=[1, 2, 4])
    code, body = run(sid)
    assert code == 200, (code, body)
    rows = rows_of(body)
    assert [r[0] for r in rows[1:]] == ["1"], rows
    print("ok: list stops at the first unfinished episode")

    # 3. a done episode with no subtitle video ends the list; a later done episode is not included
    sid = make_show("NOUP", [5, 6, 7], ["done", "done", "done"], uploads=[5, 7])
    code, body = run(sid)
    assert code == 200, (code, body)
    rows = rows_of(body)
    assert [r[0] for r in rows[1:]] == ["5"], rows
    print("ok: missing cloud video ends the list")

    # 3b. nothing finished -> refused
    sid = make_show("NONE", [8, 9], ["processing", "review"], uploads=[8])
    code, detail = run(sid)
    assert code == 409 and "no finished" in detail, (code, detail)
    print("ok: nothing finished refused:", detail)

    # 4. a show with no episodes -> refused
    sid = make_show("EMPTY", [], [])
    code, detail = run(sid)
    assert code == 409, (code, detail)
    print("ok: empty show refused")

    # 5. R2 not configured -> refused
    storage.configured = lambda: False
    sid = make_show("NOR2", [7], ["done"], uploads=[7])
    code, detail = run(sid)
    assert code == 409 and "R2" in detail, (code, detail)
    print("ok: R2 off refused")
    print("ALL PASS")


def test_public_mp4_redirects_only_out_files(monkeypatch):
    monkeypatch.setattr(storage, "configured", lambda: True)
    monkeypatch.setattr(storage, "head", lambda key: {"size": 1})
    monkeypatch.setattr(storage, "presign", lambda key, seconds=3600, **k: f"https://r2.test/{key}?sig=1")
    monkeypatch.setattr(storage, "out_key", lambda show, ep, name: f"shows/{show['code']}/ep{ep['number']}/out/{name}")

    class _DB:
        def one(self, *a, **k):
            return {"id": 1, "code": "IMTSITDE"}

    monkeypatch.setattr(main, "db", lambda: _DB())
    redir = main.public_mp4("IMTSITDE", 1, "IMTSITDE_EP1_IT_final_it_subs.mp4")
    assert redir.status_code == 302
    assert "IMTSITDE_EP1_IT_final_it_subs.mp4" in redir.headers["location"]
    from fastapi import HTTPException
    try:
        main.public_mp4("IMTSITDE", 1, "view")
        assert False
    except HTTPException as ex:
        assert ex.status_code == 404


def test_export_show_csv_rows():
    main_test()


if __name__ == "__main__":
    main_test()
