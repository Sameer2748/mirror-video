"""One episode keeps one machine, and the status line is about that episode.

A restart sends the step again. The job already on a machine has to be released first,
or the site watches the new job while the old one holds the machine. The status line
counts this step's jobs, not how many machines the whole pool is using.
"""
import urllib.error
from pathlib import Path

from dubstudio import cloud


OLD = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee-e1"
HELD = "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee-e1"
QUEUED = "cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee-e1"


def test_a_restart_releases_only_the_job_still_holding_the_machine(tmp_path, monkeypatch):
    log = tmp_path / "render.log"
    log.write_text(
        f"render_tail: sent to a rented cpu machine (job {OLD})\n"
        f"render_tail: sent to a rented cpu machine (job {HELD})\n"
        f"render_tail: sent to a rented cpu machine (job {QUEUED})\n"
    )
    states = {OLD: "COMPLETED", HELD: "IN_PROGRESS", QUEUED: "IN_QUEUE"}
    cancelled = []
    monkeypatch.setattr(cloud, "status", lambda jid, kind="cpu": {"status": states[jid]})
    monkeypatch.setattr(cloud, "cancel", lambda jid, kind="cpu": cancelled.append((jid, kind)))
    notes = []
    released = cloud.release_previous(log, kind="cpu", log=notes.append)
    assert released == [HELD, QUEUED]
    assert cancelled == [(HELD, "cpu"), (QUEUED, "cpu")]
    assert notes == ["released 2 earlier machine job(s) for this step, so only the new one is kept"]


def test_a_finished_job_is_not_released(tmp_path, monkeypatch):
    log = tmp_path / "render.log"
    log.write_text(f"sent (job {OLD})\n")
    monkeypatch.setattr(cloud, "status", lambda jid, kind="cpu": {"status": "COMPLETED"})
    monkeypatch.setattr(cloud, "cancel", lambda jid, kind="cpu": (_ for _ in ()).throw(AssertionError("cancelled")))
    assert cloud.release_previous(log) == []
    assert cloud.release_previous(tmp_path / "missing.log") == []


def test_the_new_send_releases_the_old_machine_before_it_packs(tmp_path, monkeypatch):
    log = tmp_path / "render.log"
    log.write_text(f"sent (job {HELD})\n")
    seen = []

    def release(path, kind="cpu", log=None):
        seen.append((Path(path), kind))
        raise RuntimeError("stop after release")

    monkeypatch.setattr(cloud, "release_previous", release)
    try:
        cloud.run_command(None, {}, {"work_dir": str(tmp_path), "output_dir": str(tmp_path / "out")},
                          "render_tail", ["echo"], object(), release_log=log, kind="cpu")
        raise AssertionError("packing continued after the release")
    except RuntimeError as ex:
        assert "stop after release" in str(ex)
    assert seen == [(log, "cpu")]


def test_the_status_line_counts_this_step_not_the_pool(monkeypatch):
    calls = {"n": 0}

    def status(jid, kind="cpu"):
        calls["n"] += 1
        if jid == "piece-a" and calls["n"] < 3:
            return {"status": "IN_PROGRESS"}
        if jid == "piece-b" and calls["n"] < 3:
            return {"status": "IN_QUEUE"}
        return {"status": "COMPLETED", "output": {}}

    def health(kind="cpu"):
        raise AssertionError("the pool size must not be read for this step's line")

    monkeypatch.setattr(cloud, "status", status)
    monkeypatch.setattr(cloud, "health", health)
    seen = []
    done, failed = cloud.wait(["piece-a", "piece-b"], on_progress=lambda d, t, info: seen.append(dict(info)), poll=0, timeout=5)
    assert not failed and set(done) == {"piece-a", "piece-b"}
    assert seen[0] == {"on_machine": 1, "waiting": 1}
    assert cloud.step_wait_message("render tail", seen[0]) == "render tail: 1 on a machine, 1 waiting for a free machine"
    assert cloud.step_wait_message("render tail", {"on_machine": 1, "waiting": 0}) == "render tail: on a machine"
    assert cloud.step_wait_message("render tail", {"on_machine": 0, "waiting": 1}) == "render tail: waiting for a free machine"
    assert "8 working" not in cloud.step_wait_message("render tail", {"on_machine": 0, "waiting": 1})


def test_each_attempt_gets_its_own_cloud_files():
    """A cancelled job can still upload. It must not be able to write the file the new attempt reads."""
    parts = ("work_in", "out_in", "work_out", "out_out")
    a = cloud.new_send()
    first = {cloud.step_object("render", part, a) for part in parts}
    b = cloud.new_send()
    second = {cloud.step_object("render", part, b) for part in parts}
    assert a != b and first.isdisjoint(second)
    assert all(name.startswith("cloud/render_") and name.endswith(".tar.gz") for name in first)


def test_a_missing_machine_job_fails_instead_of_waiting(monkeypatch):
    def status(jid, kind="cpu"):
        raise urllib.error.HTTPError("https://x", 404, "missing", {}, None)

    monkeypatch.setattr(cloud, "status", status)
    done, failed = cloud.wait(["gone"], poll=0, timeout=5)
    assert done == {}
    assert failed == {"gone": "the machine job is gone"}


def test_a_cancel_stops_the_step_and_clears_its_folders(tmp_path):
    """The rented machine cannot be picked by name. When the job is cancelled it must stop
    its own step, including children, and clear the folders before the machine is reused."""
    import importlib.util
    import os
    import signal
    import sys
    import threading
    import types

    runpod = sys.modules.get("runpod") or types.ModuleType("runpod")
    runpod.serverless = types.SimpleNamespace(start=lambda *a, **k: None)
    sys.modules["runpod"] = runpod
    path = Path(__file__).resolve().parents[2] / "worker" / "handler.py"
    spec = importlib.util.spec_from_file_location("worker_handler_under_test", path)
    handler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handler)
    work, out, files = tmp_path / "work", tmp_path / "out", tmp_path / "files"
    handler.WORK, handler.OUT, handler.FILES = str(work), str(out), str(files)
    for folder in (work, out, files):
        folder.mkdir()
        (folder / "left-by-the-old-job.mp4").write_bytes(b"old")

    def poke():
        import time
        time.sleep(0.4)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=poke, daemon=True).start()
    try:
        handler.run_logged([sys.executable, "-c", "import time; time.sleep(30)"])
        raise AssertionError("the cancelled step kept running")
    except SystemExit as ex:
        assert ex.code == 128 + signal.SIGTERM
    for folder in (work, out, files):
        assert folder.is_dir()
        assert list(folder.iterdir()) == []
