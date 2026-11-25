"""The episode cap applies to Render as well as Process.

Past the cap, an episode waits with the jobs it already has. A free slot continues that run.
It does not start a new one.
"""
import os
os.environ["MAX_ACTIVE_EPISODES"] = "2"

import threading

from dubstudio.db import DB
from dubstudio.runner import Runner


def _show(db, tmp_path):
    return db.insert("shows", name="IMTS", code="IMTSDEUS", folder=str(tmp_path), profile={})


def _ep(db, sid, tmp_path, number, status):
    return db.insert("episodes", show_id=sid, number=number, status=status,
                     work_dir=str(tmp_path / f"w{number}"), output_dir=str(tmp_path / f"o{number}"))


def _job(db, eid, step, status, ord_, needs=None):
    return db.insert("jobs", episode_id=eid, run=1, step=step, ord=ord_, status=status,
                     needs=needs or [], resource="cpu")


def test_overflow_waits_and_a_free_slot_continues_the_same_run(tmp_path, monkeypatch):
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    db = DB(tmp_path / "t.db")
    sid = _show(db, tmp_path)
    ids = {}
    for n in (210, 211, 216):
        ids[n] = _ep(db, sid, tmp_path, n, "processing")
        _job(db, ids[n], "review", "done", 1)
        _job(db, ids[n], "render", "waiting", 2, ["review"])
    r = Runner(db)
    r._keep_awake = lambda on: None
    r._enforce_cap()
    by_n = {row["number"]: row for row in db.q("SELECT id, number, status, resume FROM episodes ORDER BY number")}
    assert by_n[210]["status"] == "processing" and not by_n[210]["resume"]
    assert by_n[211]["status"] == "processing" and not by_n[211]["resume"]
    assert by_n[216]["status"] == "queued" and by_n[216]["resume"] == 1
    assert db.one("SELECT status FROM jobs WHERE episode_id=? AND step='render'", (ids[216],))["status"] == "waiting"

    db.update("episodes", ids[210], status="done")
    called = []
    monkeypatch.setattr(r, "create_run", lambda eid: called.append(eid))
    r.start_next()
    assert called == []
    assert db.one("SELECT status FROM episodes WHERE id=?", (ids[216],))["status"] == "processing"
    assert db.one("SELECT MAX(run) AS r FROM jobs WHERE episode_id=?", (ids[216],))["r"] == 1


def test_render_past_the_cap_is_queued(tmp_path, monkeypatch):
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    db = DB(tmp_path / "t.db")
    sid = _show(db, tmp_path)
    for n in (1, 2):
        eid = _ep(db, sid, tmp_path, n, "processing")
        _job(db, eid, "render", "running", 1)
    third = _ep(db, sid, tmp_path, 3, "cancelled")
    _job(db, third, "review", "done", 1)
    render_id = _job(db, third, "render", "cancelled", 2, ["review"])
    r = Runner(db)
    r._keep_awake = lambda on: None
    steps = r.rerun_from(third, "render")
    assert steps[0] == "render"
    row = db.one("SELECT status, resume FROM episodes WHERE id=?", (third,))
    assert row["status"] == "queued" and row["resume"] == 1
    assert db.one("SELECT status FROM jobs WHERE id=?", (render_id,))["status"] == "waiting"


def test_a_queued_episode_is_not_started(tmp_path, monkeypatch):
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    db = DB(tmp_path / "t.db")
    sid = _show(db, tmp_path)
    keep = _ep(db, sid, tmp_path, 1, "processing")
    keep_job = _job(db, keep, "render", "waiting", 1)
    parked = _ep(db, sid, tmp_path, 2, "queued")
    db.update("episodes", parked, resume=1, queued=1)
    parked_job = _job(db, parked, "render", "waiting", 1)
    r = Runner(db)
    r._keep_awake = lambda on: None
    r._maybe_daily_cleanup = lambda: None
    r._tick_link_imports = lambda: None
    started = []

    class FakeThread:
        def __init__(self, target, args, daemon=True):
            self.args = args
        def start(self):
            started.append(self.args[0])

    monkeypatch.setattr(threading, "Thread", FakeThread)
    r._tick()
    assert started == [keep_job]
    assert db.one("SELECT status FROM jobs WHERE id=?", (parked_job,))["status"] == "waiting"


def _assets(r, db):
    r.episode_context = lambda eid: (
        db.one("SELECT * FROM shows WHERE id=?", (db.one("SELECT show_id FROM episodes WHERE id=?", (eid,))["show_id"],)),
        db.one("SELECT * FROM episodes WHERE id=?", (eid,)),
        {"src_video": "v.mp4", "dub_audio": "a.wav"},
    )


def test_process_while_cap_full_does_not_wipe_an_open_run(tmp_path, monkeypatch):
    """Cap full + Process must wait and keep the same run. A bulk line must not re-pay lip/verify."""
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    db = DB(tmp_path / "t.db")
    sid = _show(db, tmp_path)
    for n in (1, 2):
        eid = _ep(db, sid, tmp_path, n, "processing")
        _job(db, eid, "render", "running", 1)
    third = _ep(db, sid, tmp_path, 23, "failed")
    _job(db, third, "review", "done", 1)
    _job(db, third, "verify", "waiting", 2, ["review"])
    r = Runner(db)
    r._keep_awake = lambda on: None
    _assets(r, db)
    wiped = []
    monkeypatch.setattr(r, "create_run", lambda eid: wiped.append(eid) or 2)
    out = r.submit(third)
    assert out["queued"] is True
    row = db.one("SELECT status, resume FROM episodes WHERE id=?", (third,))
    assert row["status"] == "queued" and row["resume"] == 1
    assert wiped == []
    db.update("episodes", db.one("SELECT id FROM episodes WHERE number=1")["id"], status="done")
    r.start_next()
    assert wiped == []
    assert db.one("SELECT status FROM episodes WHERE id=?", (third,))["status"] == "processing"
    assert db.one("SELECT MAX(run) AS r FROM jobs WHERE episode_id=?", (third,))["r"] == 1
    assert db.one("SELECT status FROM jobs WHERE episode_id=? AND step='verify'", (third,))["status"] == "waiting"


def test_start_next_does_not_wipe_a_queued_open_run_without_resume(tmp_path, monkeypatch):
    """Old Process-while-full rows used resume=None. A free slot must not create_run over waiting jobs."""
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    db = DB(tmp_path / "t.db")
    sid = _show(db, tmp_path)
    keep = _ep(db, sid, tmp_path, 1, "processing")
    _job(db, keep, "render", "running", 1)
    parked = _ep(db, sid, tmp_path, 35, "queued")
    db.update("episodes", parked, queued=1, resume=None)
    _job(db, parked, "verify", "waiting", 1)
    r = Runner(db)
    r._keep_awake = lambda on: None
    wiped = []
    monkeypatch.setattr(r, "create_run", lambda eid: wiped.append(eid) or 2)
    db.update("episodes", keep, status="done")
    r.start_next()
    assert wiped == []
    assert db.one("SELECT status FROM episodes WHERE id=?", (parked,))["status"] == "processing"
    assert db.one("SELECT MAX(run) AS r FROM jobs WHERE episode_id=?", (parked,))["r"] == 1


def test_first_process_while_cap_full_starts_a_run_when_a_slot_opens(tmp_path, monkeypatch):
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    db = DB(tmp_path / "t.db")
    sid = _show(db, tmp_path)
    keep = _ep(db, sid, tmp_path, 1, "processing")
    _job(db, keep, "render", "running", 1)
    keep2 = _ep(db, sid, tmp_path, 2, "processing")
    _job(db, keep2, "render", "running", 1)
    fresh = _ep(db, sid, tmp_path, 3, "new")
    r = Runner(db)
    r._keep_awake = lambda on: None
    _assets(r, db)
    started = []

    def fake_create(eid):
        started.append(eid)
        db.update("episodes", eid, status="processing", queued=None, resume=None)
        return 1

    monkeypatch.setattr(r, "create_run", fake_create)
    out = r.submit(fresh)
    assert out["queued"] is True
    assert not db.one("SELECT resume FROM episodes WHERE id=?", (fresh,))["resume"]
    assert started == []
    db.update("episodes", keep, status="done")
    r.start_next()
    assert started == [fresh]


def test_queue_in_order_process_again_does_not_resume(tmp_path, monkeypatch):
    """WBT 101-200 failed/processing/cancelled start a new run when the slot opens."""
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    db = DB(tmp_path / "t.db")
    sid = _show(db, tmp_path)
    keep = _ep(db, sid, tmp_path, 153, "processing")
    _job(db, keep, "fetch", "running", 1)
    wbt = _ep(db, sid, tmp_path, 101, "failed")
    _job(db, wbt, "fetch", "failed", 1)
    other = _ep(db, sid, tmp_path, 1, "processing")
    _job(db, other, "fetch", "running", 1)
    kotu = _ep(db, sid, tmp_path, 50, "queued")
    db.update("episodes", kotu, queued=1.0, resume=None)
    r = Runner(db)
    r._keep_awake = lambda on: None
    r.start_next = lambda: None
    r.queue_in_order([keep, wbt], from_scratch=[wbt])
    assert db.one("SELECT status FROM episodes WHERE id=?", (keep,))["status"] == "processing"
    row = db.one("SELECT status, resume, queued FROM episodes WHERE id=?", (wbt,))
    assert row["status"] == "queued" and not row["resume"]
    parked = db.one("SELECT status, resume FROM episodes WHERE id=?", (other,))
    assert parked["status"] == "queued" and parked["resume"] == 1
    behind = db.one("SELECT queued, resume FROM episodes WHERE id=?", (kotu,))
    assert behind["queued"] > row["queued"] and not behind["resume"]
