from dubstudio.db import DB
from dubstudio.runner import Runner, NOISE


def test_progress_from_tool_lines():
    p = Runner._parse_progress
    assert abs(p("22:58:50 done 45-60 in 23 s (so far 71 s); free RAM 3.2 GB", 741) - 60 / 741) < 1e-9   # lipgpu
    assert abs(p("chunk 12.0-27.5s", 100) - 0.275) < 1e-9
    assert p("rendered 3/12", None) == 0.25
    assert p("nothing here", 100) is None


def test_a_blocked_step_starts_when_the_failed_one_has_finished(tmp_path, monkeypatch):
    """A failed German SRT blocks matching. Retrying the SRT used to leave matching blocked even after the
    SRT and the sentence times had finished. The next tick should start matching on its own."""
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    db = DB(tmp_path / "t.db")
    sid = db.insert("shows", name="TGS", code="TGS", folder=str(tmp_path), profile={})
    eid = db.insert("episodes", show_id=sid, number=34, status="failed", work_dir=str(tmp_path), output_dir=str(tmp_path))
    db.insert("jobs", episode_id=eid, run=1, step="mksrt_dub", ord=1, status="done", needs=[], resource="cpu")
    db.insert("jobs", episode_id=eid, run=1, step="words_dub", ord=2, status="done", needs=["mksrt_dub"], resource="cpu")
    aid = db.insert("jobs", episode_id=eid, run=1, step="analyze", ord=3, status="blocked",
                    needs=["words_dub"], resource="cpu", message="waiting for a failed step")
    mid = db.insert("jobs", episode_id=eid, run=1, step="merged", ord=4, status="blocked",
                    needs=["analyze"], resource="none", message="waiting for a failed step")
    db.insert("jobs", episode_id=eid, run=1, step="bad", ord=5, status="failed", needs=[], resource="cpu")
    still = db.insert("jobs", episode_id=eid, run=1, step="sheet", ord=6, status="blocked",
                      needs=["bad"], resource="cpu", message="waiting for a failed step")
    started = []

    class T:
        def __init__(self, target, args, daemon):
            self.args = args
        def start(self):
            started.append(self.args[0])

    monkeypatch.setattr("dubstudio.runner.threading.Thread", T)
    r = Runner(db)
    r._keep_awake = lambda on: None
    r._tick()
    by = {j["step"]: j for j in db.q("SELECT * FROM jobs WHERE episode_id=?", (eid,))}
    assert by["analyze"]["status"] == "running" and aid in started
    assert by["merged"]["status"] == "waiting"          # matching is running, so the reading view waits
    assert by["sheet"]["status"] == "blocked"           # its own dependency is still missing
    assert mid not in started and still not in started


def test_library_warnings_are_not_the_last_line():
    assert NOISE.search("box_utils.py:103: UserWarning: An output with one or more elements was resized")
    assert NOISE.search("  torch.index_select(x1, 0, idx, out=xx1)")
    assert not NOISE.search("22:58:50 done 45-60 in 23 s")
