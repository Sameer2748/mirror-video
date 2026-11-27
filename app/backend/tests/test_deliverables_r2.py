"""Deliverables must list worker-PUT videos that live only in R2 (IMTS IT-DE EP17)."""
from dubstudio import deliver, presets


def test_cloud_only_clean_plus_draft_is_not_a_500():
    """WBT 101: cleanup left the mp4 in R2 (exists, no mtime) and an editor draft on disk."""
    assert deliver.editor_ahead_of_files(2, 2, {"saved": 1795764056.57}, {"kind": "clean", "exists": True, "mtime": None}) is False
    assert deliver.editor_ahead_of_files(3, 2, None, {"mtime": None}) is True
    assert deliver.editor_ahead_of_files(2, 2, {"saved": 200}, {"mtime": 100}) is True


def test_videos_in_r2_count_as_existing(tmp_path, monkeypatch):
    show = {"code": "IMTSITDE", "profile": presets.PRESETS["WBM"]}
    ep = {"number": 17, "output_dir": str(tmp_path / "out"), "work_dir": str(tmp_path / "work")}
    (tmp_path / "out").mkdir(); (tmp_path / "work").mkdir()
    sizes = {
        "IMTSITDE_EP17_IT_final_clean.mp4": 277,
        "IMTSITDE_EP17_IT_final_it_subs.mp4": 214,
        "IMTSITDE_EP17_IT_final_en_subs.mp4": 213,
    }

    def fake_head(key):
        name = key.rsplit("/", 1)[-1]
        if name in sizes:
            return {"size": sizes[name]}
        return None

    monkeypatch.setattr("dubstudio.storage.configured", lambda: True)
    monkeypatch.setattr("dubstudio.storage.head", fake_head)
    monkeypatch.setattr("dubstudio.storage.out_key", lambda s, e, n: f"shows/{s['code']}/ep{e['number']}/out/{n}")
    files = {f["kind"]: f for f in deliver.deliverables(show, ep, None)}
    assert files["clean"]["exists"] and files["clean"]["in_cloud"]
    assert files["dub_subs"]["exists"] and files["dub_subs"]["size"] == 214
    assert files["en_subs"]["exists"]

    (tmp_path / "out" / "IMTSITDE_EP17_IT_final_it_subs.mp4").write_bytes(b"local")
    files = {f["kind"]: f for f in deliver.deliverables(show, ep, None)}
    assert files["dub_subs"]["in_cloud"] is True


def test_record_existing_cloud_files_lists_worker_puts(tmp_path, monkeypatch):
    from dubstudio.db import DB
    from dubstudio import storage
    db = DB(tmp_path / "t.db")
    sid = db.insert("shows", name="IMTS", code="IMTSITDE", folder=str(tmp_path), profile={})
    eid = db.insert("episodes", show_id=sid, number=17, status="done",
                    work_dir=str(tmp_path), output_dir=str(tmp_path))
    monkeypatch.setattr(storage, "configured", lambda: True)
    monkeypatch.setattr(storage, "settings", lambda: {"bucket": "dubstudioprod"})
    monkeypatch.setattr(storage, "out_key", lambda s, e, n: f"shows/{s['code']}/ep{e['number']}/out/{n}")
    show = {"code": "IMTSITDE"}
    ep = {"id": eid, "number": 17}
    files = [{"kind": "clean", "name": "IMTSITDE_EP17_IT_final_clean.mp4", "path": "/missing/clean.mp4",
              "exists": True, "in_cloud": True, "size": 277}]
    rows = storage.record_existing_cloud_files(db, show, ep, files)
    assert len(rows) == 1 and rows[0]["status"] == "done" and rows[0]["size"] == 277
    again = storage.record_existing_cloud_files(db, show, ep, files)
    assert again[0]["id"] == rows[0]["id"]


def test_a_stuck_sending_row_is_marked_done_when_r2_already_has_the_file(tmp_path, monkeypatch):
    """IMTS DE 162: deploy left DE-subs at sending 0 KB while the object could still be PUT later."""
    from dubstudio.db import DB
    from dubstudio import storage
    db = DB(tmp_path / "t.db")
    sid = db.insert("shows", name="IMTS", code="IMTSDEUS", folder=str(tmp_path), profile={})
    eid = db.insert("episodes", show_id=sid, number=162, status="done",
                    work_dir=str(tmp_path), output_dir=str(tmp_path))
    key = "shows/IMTSDEUS/ep162/out/IMTSDEUS_EP162_IT_final_de_subs.mp4"
    uid = db.insert("uploads", episode_id=eid, kind="dub_subs", name="IMTSDEUS_EP162_IT_final_de_subs.mp4",
                    path=str(tmp_path / "de.mp4"), bucket="b", key=key, size=232, sent=0, status="sending")
    monkeypatch.setattr(storage, "configured", lambda: True)
    monkeypatch.setattr(storage, "head", lambda k: {"size": 232} if k == key else None)
    n = storage.recover_stuck_uploads(db)
    row = db.one("SELECT * FROM uploads WHERE id=?", (uid,))
    assert n == 1 and row["status"] == "done" and row["sent"] == 232

    db.update("uploads", uid, status="sending", sent=0)
    monkeypatch.setattr(storage, "head", lambda k: None)
    storage.recover_stuck_uploads(db)
    row = db.one("SELECT * FROM uploads WHERE id=?", (uid,))
    assert row["status"] == "waiting"
