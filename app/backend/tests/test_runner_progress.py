from pathlib import Path

from dubstudio.db import DB
from dubstudio.runner import Runner, NOISE, is_key_limit_error


def test_progress_from_tool_lines():
    p = Runner._parse_progress
    assert abs(p("22:58:50 done 45-60 in 23 s (so far 71 s); free RAM 3.2 GB", 741) - 60 / 741) < 1e-9   # lipgpu
    assert abs(p("chunk 12.0-27.5s", 100) - 0.275) < 1e-9
    assert p("rendered 3/12", None) == 0.25
    assert p("nothing here", 100) is None


def test_the_decision_check_uses_a_worker_when_the_scripts_are_not_here(tmp_path, monkeypatch):
    """The website has no dubstitch.py. Claude's decisions still have to pass that check, on a rented machine."""
    import json
    from dubstudio.pipeline import decisions as dec
    prep = {"dub": [{"text": "Hallo", "start_ms": 0, "end_ms": 1000}],
            "src": [{"text": "Hello", "start_ms": 0, "end_ms": 1000}],
            "sim": [], "auto": {"0": {"src": 0, "kind": "DP match"}}}
    (tmp_path / "prep.json").write_text(json.dumps(prep))
    seen = {}

    def fake(work, spec, labels, counts_and_scratch=None, database=None, show=None, episode=None):
        seen["work"] = work
        seen["scratch"] = counts_and_scratch[0]
        return {"ok": True, "problems": [], "overlaps": [], "counts": counts_and_scratch[1], "log": ["0ms rows: []"]}

    monkeypatch.setattr(dec, "_check_on_worker", fake)
    got = dec.check_spec(tmp_path, 'C(0,"ok")\n', "python", tmp_path / "missing-scripts", "DE,US")
    assert got["ok"] and seen["work"] == tmp_path
    assert (seen["scratch"] / "prep.json").is_file()
    assert (seen["scratch"] / "decisions.json").is_file()


def test_retry_check_without_rerender_waits_for_translate_and_does_not_start_render(tmp_path, monkeypatch):
    """A tail that died on a missing English SRT must not render again. Translate first, then check."""
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    db = DB(tmp_path / "t.db")
    sid = db.insert("shows", name="TAB", code="TAB", folder=str(tmp_path), profile={})
    eid = db.insert("episodes", show_id=sid, number=17, status="failed",
                    work_dir=str(tmp_path), output_dir=str(tmp_path))
    db.insert("jobs", episode_id=eid, run=1, step="translate", ord=1, status="waiting", needs=[], resource="cpu")
    db.insert("jobs", episode_id=eid, run=1, step="render", ord=2, status="failed", needs=[], resource="cpu")
    db.insert("jobs", episode_id=eid, run=1, step="qc", ord=3, status="blocked", needs=["render"], resource="cpu")
    db.insert("jobs", episode_id=eid, run=1, step="check", ord=4, status="blocked", needs=["qc"], resource="cpu")
    r = Runner(db)
    r._keep_awake = lambda on: None
    try:
        r.retry_check_without_rerender(eid)
        assert False, "should refuse until translate is done"
    except ValueError as ex:
        assert "translate" in str(ex)
    db.update("jobs", db.one("SELECT id FROM jobs WHERE step='translate'")["id"], status="done")
    out = r.retry_check_without_rerender(eid)
    assert out == {"retried": "check"}
    by = {j["step"]: j for j in db.q("SELECT * FROM jobs WHERE episode_id=?", (eid,))}
    assert by["render"]["status"] == "done"
    assert by["qc"]["status"] == "done"
    assert by["check"]["status"] == "waiting"
    assert db.one("SELECT status FROM episodes WHERE id=?", (eid,))["status"] == "processing"


def test_abandon_scratch_run_retries_the_failed_step_of_the_previous_run(tmp_path, monkeypatch):
    """Process again after a failed render used to start lip-scan from zero. Drop that new run and
    retry the old render instead."""
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    db = DB(tmp_path / "t.db")
    sid = db.insert("shows", name="TAB", code="TAB", folder=str(tmp_path), profile={})
    eid = db.insert("episodes", show_id=sid, number=17, status="processing",
                    work_dir=str(tmp_path), output_dir=str(tmp_path))
    db.insert("jobs", episode_id=eid, run=1, step="review", ord=1, status="done", needs=[], resource="none")
    rid = db.insert("jobs", episode_id=eid, run=1, step="render", ord=2, status="failed",
                    needs=["review"], resource="cpu", message="translated SRT missing")
    db.insert("jobs", episode_id=eid, run=1, step="transitions", ord=3, status="failed",
              needs=["render"], resource="cpu")
    db.insert("jobs", episode_id=eid, run=2, step="fetch", ord=0, status="done", needs=[], resource="cpu")
    db.insert("jobs", episode_id=eid, run=2, step="lip_gpu", ord=1, status="running",
              needs=["fetch"], resource="gpu")
    db.insert("jobs", episode_id=eid, run=2, step="render", ord=2, status="cancelled",
              needs=["lip_gpu"], resource="cpu")
    r = Runner(db)
    r._keep_awake = lambda on: None
    out = r.abandon_scratch_run(eid)
    assert out == {"restored_run": 1, "retried": "render"}
    assert r.latest_run(eid) == 1
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE episode_id=? AND run=2", (eid,))["n"] == 0
    assert db.one("SELECT status FROM jobs WHERE id=?", (rid,))["status"] == "waiting"
    assert db.one("SELECT status FROM episodes WHERE id=?", (eid,))["status"] == "processing"


def test_a_running_retry_is_processing_even_if_a_later_step_is_still_failed(tmp_path, monkeypatch):
    """Retrying render used to keep the table on Failed because the old transitions row was still
    failed, even while the new tail was on a rented machine."""
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    db = DB(tmp_path / "t.db")
    sid = db.insert("shows", name="TAB", code="TAB", folder=str(tmp_path), profile={})
    eid = db.insert("episodes", show_id=sid, number=20, status="failed",
                    work_dir=str(tmp_path), output_dir=str(tmp_path))
    db.insert("jobs", episode_id=eid, run=1, step="render", ord=1, status="running",
              needs=[], resource="cpu", message="render tail on a rented machine")
    db.insert("jobs", episode_id=eid, run=1, step="verify", ord=2, status="waiting",
              needs=["render"], resource="cpu")
    db.insert("jobs", episode_id=eid, run=1, step="transitions", ord=3, status="failed",
              needs=["verify"], resource="cpu", message="old upload race")
    r = Runner(db)
    r._keep_awake = lambda on: None
    r._episode_status()
    assert db.one("SELECT status FROM episodes WHERE id=?", (eid,))["status"] == "processing"

    db.update("jobs", db.one("SELECT id FROM jobs WHERE step='render'")["id"], status="waiting")
    db.update("episodes", eid, status="failed")
    r._episode_status()
    assert db.one("SELECT status FROM episodes WHERE id=?", (eid,))["status"] == "processing"

    db.update("jobs", db.one("SELECT id FROM jobs WHERE step='render'")["id"], status="failed")
    db.update("jobs", db.one("SELECT id FROM jobs WHERE step='verify'")["id"], status="blocked")
    db.update("episodes", eid, status="processing")
    r._episode_status()
    assert db.one("SELECT status FROM episodes WHERE id=?", (eid,))["status"] == "failed"


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


def test_a_local_command_does_not_require_the_mac_scripts_folder(tmp_path, monkeypatch):
    """The transition plan runs on the website before the rented machine renders. The website has no
    adaptation-tool scripts folder, and starting a command there raises FileNotFoundError."""
    from dubstudio.config import cfg
    monkeypatch.setattr(type(cfg), "scripts", property(lambda self: tmp_path / "no-such-scripts"))
    assert Runner._local_cwd() is None
    here = tmp_path / "scripts"
    here.mkdir()
    monkeypatch.setattr(type(cfg), "scripts", property(lambda self: here))
    assert Runner._local_cwd() == str(here)


def test_openrouter_key_limit_retries_the_job_once_not_the_claude_call(tmp_path, monkeypatch):
    """TAB EP79: translate died on the $500 OpenRouter cap. Other episodes started after the cap
    was raised. Retry the job once; do not ask Claude three times for the same 403."""
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    monkeypatch.setattr(Runner, "_spend", staticmethod(lambda: "NA"))
    db = DB(tmp_path / "t.db")
    sid = db.insert("shows", name="TAB", code="TAB", folder=str(tmp_path), profile={})
    eid = db.insert("episodes", show_id=sid, number=79, status="processing",
                    work_dir=str(tmp_path), output_dir=str(tmp_path))
    log = tmp_path / "translate.log"; log.write_text("")
    jid = db.insert("jobs", episode_id=eid, run=1, step="translate", ord=1, status="running",
                    needs=[], resource="cpu", attempt=0, log_path=str(log),
                    params={"fn": "translate"})
    r = Runner(db)
    r._keep_awake = lambda on: None
    hits = {"n": 0}

    def boom(j):
        hits["n"] += 1
        raise RuntimeError(
            'AI gateway HTTP 403: b\'{"error":{"message":"Key limit exceeded (total limit). '
            '$500.62 of $500.00 used","code":403,"metadata":{"limit_source":"openrouter_key_limit"}}}\'')

    r._fn_translate = boom
    r._run_job(jid)
    j = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    assert hits["n"] == 1
    assert j["status"] == "waiting" and j["attempt"] == 1
    assert "key limit" in (j["message"] or "").lower()
    assert "trying again once" in log.read_text()

    db.update("jobs", jid, status="running")
    r._run_job(jid)
    j = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    assert hits["n"] == 2
    assert j["status"] == "failed" and j["attempt"] == 1
    assert "HTTP 403" in (j["message"] or "")


def test_key_limit_error_is_not_an_empty_claude_reply():
    assert is_key_limit_error('AI gateway HTTP 403: b\'{"error":{"message":"Key limit exceeded (total limit)."')
    assert is_key_limit_error('openrouter_key_limit')
    assert not is_key_limit_error("the AI did not translate a batch of 1 sentences")
    assert not is_key_limit_error("AI gateway HTTP 500: overloaded")


def test_retrying_transitions_rebuilds_the_video_first(tmp_path):
    """A failed render does not send the videos back. Retrying Transitions alone used to start on an
    empty machine and stop with 'no clean master'."""
    from dubstudio import cloud
    r = Runner.__new__(Runner)
    by = {s: {"id": i, "step": s, "params": {"cmd": ["{py}", s]}} for i, s in enumerate(cloud.CHAIN)}
    named, _ = r._tail_commands({"id": 99, "step": "transitions"}, by, tmp_path)
    names = [n for n, _ in named]
    assert names[0] == "render"
    assert names[names.index("transitions") - 1] == "clean_master"
    (tmp_path / "video_groups_nosubs.mp4").write_bytes(b"v")
    named, _ = r._tail_commands({"id": 99, "step": "transitions"}, by, tmp_path)
    names = [n for n, _ in named]
    assert names[0] == "bookends"
    # The automatic cut is rendered only when this run kept it, and it does not replace the finished video.
    by["transitions"]["params"]["cmd"] = ["{py}", "transitions", "--out", str(tmp_path / "TGS_EP34_DE_final_clean.mp4")]
    by["render"]["params"]["cmd"] = ["{py}", "dubstitch.py", "render", "--work", str(tmp_path), "--subs", "keep"]
    (tmp_path / "manifest_groups.auto.json").write_text("{}", encoding="utf-8")
    named, _ = r._tail_commands({"id": 1, "step": "render"}, by, tmp_path)
    names = [n for n, _ in named]
    assert names[:3] == ["before_render", "before_cleanup", "render"]
    assert named[0][1][-4:] == ["--manifest", "manifest_groups.auto.json", "--out",
                                 str(tmp_path / "TGS_EP34_DE_before.mp4")]
    assert "manifest_groups.json" in named[1][1][2]
    (tmp_path / "TGS_EP34_DE_before.mp4").write_bytes(b"before" * 20)
    named, _ = r._tail_commands({"id": 1, "step": "render"}, by, tmp_path)
    names = [n for n, _ in named]
    assert names[0] == "render"


def test_run_notes_are_written_without_the_mac_scripts_folder(tmp_path, monkeypatch):
    """The notes are plain text. Only the optional cost table needs run_report.py, which lives in the
    Mac scripts folder. Missing that folder must not fail the step."""
    import shutil
    from dubstudio.config import cfg
    from dubstudio.db import DB
    mac = Path("/Users/manish/conductor/workspaces/dub-studioo/hamburg/app/library/work/TGS_ep34")
    work = tmp_path / "work"
    out = tmp_path / "out"
    work.mkdir(); out.mkdir(); (work / "logs").mkdir()
    if (mac / "decisions.json").is_file():
        shutil.copy(mac / "decisions.json", work / "decisions.json")
    if (mac / "run_times.tsv").is_file():
        shutil.copy(mac / "run_times.tsv", work / "run_times.tsv")
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    monkeypatch.setattr(type(cfg), "scripts", property(lambda self: tmp_path / "no-such-scripts"))
    db = DB(tmp_path / "t.db")
    sid = db.insert("shows", name="TGS", code="TGS", folder=str(tmp_path), profile={})
    eid = db.insert("episodes", show_id=sid, number=34, status="processing", work_dir=str(work), output_dir=str(out))
    jid = db.insert("jobs", episode_id=eid, run=1, step="notes", ord=1, status="running", needs=[], resource="none",
                    params={"fn": "run_notes", "ctx": {"W": str(work), "OUT": str(out)}},
                    log_path=str(work / "logs" / "notes.log"))
    r = Runner(db)
    r._fn_run_notes(db.one("SELECT * FROM jobs WHERE id=?", (jid,)))
    text = (out / "run_notes.md").read_text(encoding="utf-8")
    assert "TGS ep34" in text
    assert "france-team" not in text
    row = db.one("SELECT status FROM jobs WHERE id=?", (jid,))
    assert row["status"] == "done"


def test_runner_starts_daily_disk_cleanup_once(tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr("dubstudio.db.database_url", lambda: None)
    db = DB(tmp_path / "t.db")
    seen = []
    gate = threading.Event()

    def fake(db_, library, **k):
        gate.wait(2)
        seen.append(library)
        return {"freed": 1, "removed": ["x"], "episodes": 1}

    monkeypatch.setattr("dubstudio.storage.cleanup_due", lambda *a, **k: True)
    monkeypatch.setattr("dubstudio.storage.maybe_daily_cleanup", fake)
    r = Runner(db)
    r._keep_awake = lambda on: None
    r._maybe_daily_cleanup()
    r._maybe_daily_cleanup()
    gate.set()
    r._cleanup_thread.join(2)
    assert len(seen) == 1


def test_library_warnings_are_not_the_last_line():
    assert NOISE.search("box_utils.py:103: UserWarning: An output with one or more elements was resized")
    assert NOISE.search("  torch.index_select(x1, 0, idx, out=xx1)")
    assert not NOISE.search("22:58:50 done 45-60 in 23 s")
