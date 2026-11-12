"""Splitting a lip scan across rented machines (dubstudio/cloud.py).

No RunPod is touched: `submit` / `wait` are replaced by a fake endpoint that writes the files a real worker would
PUT, and R2 is a folder in tmp_path. What is checked is the part that was wrong twice on the real run: the pieces
must be **lipgpu's own pieces** with lipgpu's own names, and `ranges.json` -- which the two steps after the scan
read -- must hold the whole list, not one machine's share.
"""
import json, shutil, tarfile
from pathlib import Path

import pytest

from dubstudio import cloud, scan


def test_picture_duration_uses_the_shorter_video_stream():
    """Container 615 s with pictures stopping at 390 s must not cut 16 empty lip-scan pieces."""
    assert scan.picture_duration({"duration": 615, "video": {"duration": 390}}) == 390
    assert scan.picture_duration({"duration": 615, "video": {}}) == 615
    assert scan.picture_duration({"duration": 615}) == 615


def test_the_pieces_are_lipgpus_pieces_including_the_last_one():
    """lipgpu steps `while s < L`, so the last piece runs past the end of the video. Clamping it to the duration
    writes `range_615_627.json` -- a name nothing downstream looks for."""
    assert cloud.make_ranges(627.0)[-1] == [615.0, 630.0]
    assert len(cloud.make_ranges(627.0)) == 42
    assert cloud.make_ranges(30.0) == [[0.0, 15.0], [15.0, 30.0]]
    assert cloud.make_ranges(1.0) == [[0.0, 15.0]]            # a very short clip is still one whole piece
    assert cloud.make_ranges(0) == []


class FakeCloud:
    """A fake endpoint. `fail_first` shares die on the first attempt, as a reclaimed machine does.
    `empty_first` pieces write frames:0 once, then a real file — TAB EP20 390-405."""

    def __init__(self, bucket, fail_first=0, empty_first=None, empty_always=None):
        self.bucket = Path(bucket)
        self.fail_first = fail_first
        self.empty_first = {tuple(c) for c in (empty_first or [])}
        self.empty_always = {tuple(c) for c in (empty_always or [])}
        self.emptied = set()
        self.jobs = {}                                        # job id -> (ranges, key)
        self.attempts = []

    def submit(self, job):
        jid = f"job{len(self.jobs)}"
        key = job["outputs"]["result"].split("r2/", 1)[1]
        self.jobs[jid] = (job["args"]["ranges"], key)
        return jid

    def wait(self, ids, on_progress=None, **kw):
        self.attempts.append(list(ids))
        done, failed = {}, {}
        for i, jid in enumerate(ids):
            rngs, key = self.jobs[jid]
            if len(self.attempts) == 1 and i < self.fail_first:
                failed[jid] = "executionTimeout exceeded"
                continue
            nets = self.bucket / jid / "lips_nets"
            nets.mkdir(parents=True, exist_ok=True)
            for s, e in rngs:                                 # lipgpu names the file after the range it was given
                cut = (s, e)
                if cut in self.empty_always or (cut in self.empty_first and cut not in self.emptied):
                    payload = {"window": [s, e], "frames": 0, "scores": []}
                    self.emptied.add(cut)
                else:
                    payload = {"window": [s, e], "frames": 375, "scores": [1] * 8}
                (nets / f"range_{s:g}_{e:g}.json").write_text(json.dumps(payload))
            (nets / "ranges.json").write_text(json.dumps(rngs))     # the worker only knows its own share
            out = self.bucket / key
            out.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(out, "w:gz") as t:
                t.add(nets, arcname="lips_nets")
            done[jid] = {"executionTime": 1000}
        return done, failed

    def download(self, key, path, **kw):
        Path(path).write_bytes((self.bucket / key).read_bytes())

    def install(self, monkeypatch):
        monkeypatch.setattr(cloud, "submit", self.submit)
        monkeypatch.setattr(cloud, "wait", self.wait)
        monkeypatch.setattr(cloud, "ensure_input_in_cloud", lambda *a, **k: "input/video.mp4")
        monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
        monkeypatch.setattr(cloud.storage, "work_key", lambda show, ep, rel: str(rel))
        monkeypatch.setattr(cloud.storage, "download", self.download)
        return self


@pytest.fixture()
def ep(tmp_path):
    work = tmp_path / "work"; work.mkdir()
    return {"code": "TGS"}, {"id": 1, "number": 34, "work_dir": str(work)}


def test_a_scan_writes_every_piece_and_the_ranges_file(tmp_path, monkeypatch, ep):
    show, episode = ep
    fake = FakeCloud(tmp_path / "bucket").install(monkeypatch)
    res = cloud.lip_scan({}, show, episode, duration_s=627.0, n_pieces=8, log=lambda m: None)

    nets = Path(episode["work_dir"]) / "lips_nets"
    want = cloud.make_ranges(627.0)
    assert res["files"] == 42 and res["retries"] == 0 and res["pieces"] == 8
    assert sorted(p.name for p in nets.glob("range_*.json")) == \
           sorted(f"range_{s:g}_{e:g}.json" for s, e in want)
    # the whole list, not the last machine's share -- `lipmesh --ranges` and `lipmerge` read this file
    assert json.loads((nets / "ranges.json").read_text()) == want
    asked = [r for rngs, _ in fake.jobs.values() for r in rngs]
    assert sorted(asked) == sorted(want) and len(fake.jobs) == 8   # every piece asked for exactly once


def test_a_failed_share_is_sent_again_and_the_finished_ones_are_kept(tmp_path, monkeypatch, ep):
    show, episode = ep
    fake = FakeCloud(tmp_path / "bucket", fail_first=1).install(monkeypatch)
    res = cloud.lip_scan({}, show, episode, duration_s=60.0, n_pieces=4, log=lambda m: None)

    nets = Path(episode["work_dir"]) / "lips_nets"
    assert res["retries"] == 1 and res["files"] == 4
    assert len(fake.attempts) == 2 and len(fake.attempts[0]) == 4 and len(fake.attempts[1]) == 1
    assert sorted(p.name for p in nets.glob("range_*.json")) == \
           ["range_0_15.json", "range_15_30.json", "range_30_45.json", "range_45_60.json"]


def test_an_empty_piece_is_scanned_again_without_redoing_the_rest(tmp_path, monkeypatch, ep):
    """TAB EP20: 47 files came back, but 390-405 had frames:0. Merge then died. Resend only that slice."""
    show, episode = ep
    fake = FakeCloud(tmp_path / "bucket", empty_first=[[15.0, 30.0]]).install(monkeypatch)
    res = cloud.lip_scan({}, show, episode, duration_s=60.0, n_pieces=4, log=lambda m: None)
    nets = Path(episode["work_dir"]) / "lips_nets"
    assert res["files"] == 4 and res["retries"] == 1
    assert json.loads((nets / "range_15_30.json").read_text())["frames"] == 375
    assert len(fake.attempts) == 2 and fake.attempts[1]  # second visit is the empty-piece retry


def test_only_the_last_overshoot_piece_is_filled_silent(tmp_path, monkeypatch, ep):
    """make_ranges runs the last window past EOF. That one empty file is silent scores, not a crash."""
    show, episode = ep
    FakeCloud(tmp_path / "bucket", empty_always=[[45.0, 60.0]]).install(monkeypatch)
    res = cloud.lip_scan({}, show, episode, duration_s=60.0, n_pieces=4, log=lambda m: None)
    nets = Path(episode["work_dir"]) / "lips_nets"
    d = json.loads((nets / "range_45_60.json").read_text())
    assert res["files"] == 4
    assert d["frames"] == 375 and d["scores"] == [-9.0] * 375
    assert d["method"]["filled"] == "empty-eof"


def test_a_hole_in_the_middle_is_not_scored_as_no_lips(tmp_path, monkeypatch, ep):
    """TAB EP26-30: 16 pieces from 390s onward were empty. Filling them as 'no lips' would skip 4 minutes."""
    show, episode = ep
    FakeCloud(tmp_path / "bucket", empty_always=[[15.0, 30.0]]).install(monkeypatch)
    with pytest.raises(RuntimeError, match="no pictures after 15"):
        cloud.lip_scan({}, show, episode, duration_s=60.0, n_pieces=4, log=lambda m: None)


def test_a_long_empty_tail_is_dropped_not_scored_as_no_lips(tmp_path, monkeypatch, ep):
    """TAB EP51: container 674 s, pictures stop at 585 s. Drop the empty tail; do not fill it as 'no lips'."""
    show, episode = ep
    notes = []
    FakeCloud(tmp_path / "bucket", empty_always=[[15.0, 30.0], [30.0, 45.0], [45.0, 60.0]]).install(monkeypatch)
    res = cloud.lip_scan({}, show, episode, duration_s=60.0, n_pieces=4, log=notes.append)
    nets = Path(episode["work_dir"]) / "lips_nets"
    assert res["files"] == 1
    assert json.loads((nets / "ranges.json").read_text()) == [[0.0, 15.0]]
    assert json.loads((nets / "range_0_15.json").read_text())["frames"] == 375
    assert not (nets / "range_15_30.json").exists()
    assert not (nets / "range_45_60.json").exists()
    assert any("dropping the empty tail" in m for m in notes)


def test_ep51_empty_tail_after_585s_is_dropped(tmp_path, monkeypatch, ep):
    """Same numbers as TAB EP51: 45 pieces across 674 s, pictures gone from 585 s."""
    show, episode = ep
    cuts = cloud.make_ranges(674.0)
    empty = [c for c in cuts if c[0] >= 585.0]
    assert empty[:3] == [[585.0, 600.0], [600.0, 615.0], [615.0, 630.0]] and len(empty) == 6
    FakeCloud(tmp_path / "bucket", empty_always=empty).install(monkeypatch)
    res = cloud.lip_scan({}, show, episode, duration_s=674.0, n_pieces=8, log=lambda m: None)
    kept = [c for c in cuts if c[0] < 585.0]
    nets = Path(episode["work_dir"]) / "lips_nets"
    assert res["files"] == len(kept)
    assert json.loads((nets / "ranges.json").read_text()) == kept
    assert not (nets / "range_585_600.json").exists()


def test_a_share_that_keeps_failing_is_reported_not_hidden(tmp_path, monkeypatch, ep):
    show, episode = ep
    monkeypatch.setattr(cloud, "submit", lambda job: "j")
    monkeypatch.setattr(cloud, "wait", lambda ids, **kw: ({}, {i: "gone" for i in ids}))
    monkeypatch.setattr(cloud, "ensure_input_in_cloud", lambda *a, **k: "key")
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: "https://r2/x")
    monkeypatch.setattr(cloud.storage, "work_key", lambda show, ep_, rel: str(rel))
    with pytest.raises(RuntimeError, match="still failing after"):
        cloud.lip_scan({}, show, episode, duration_s=30.0, n_pieces=2, log=lambda m: None)


def test_which_steps_go_to_the_cloud(monkeypatch):
    """`CLOUD_STEPS` decides. Read from app/.env on the real app, so the settings are faked here rather than
    depending on what the owner's file happens to say."""
    settings = {}
    monkeypatch.setattr(cloud.envfile, "get", lambda k, d=None: settings.get(k, d))

    settings.update(RUNPOD_API_KEY="k", RUNPOD_CPU_ENDPOINT="e", CLOUD_STEPS="lip_gpu")
    assert cloud.enabled_for("lip_gpu") and not cloud.enabled_for("groups")

    settings["CLOUD_STEPS"] = "lip_gpu, groups"               # a list, spaces allowed
    assert cloud.enabled_for("lip_gpu") and cloud.enabled_for("groups")

    settings["CLOUD_STEPS"] = "all"
    assert cloud.enabled_for("words_src")                 # a step that still goes on its own
    assert not cloud.enabled_for("groups")                # groups rides in the cut visit

    settings["CLOUD_STEPS"] = ""                              # nothing set = everything runs here, as before
    assert not cloud.enabled_for("lip_gpu") and cloud.steps() == set()

    settings.update(CLOUD_STEPS="all", RUNPOD_API_KEY=None)
    assert not cloud.enabled_for("lip_gpu")                   # no key, no cloud


# ---------------- any step on a rented machine ----------------
class FakeCfg:
    """The paths of a Mac, so tokenise has something real to swap out."""
    def __init__(self, root):
        self.tools_dir = root / "adaptation-tool"
        self.scripts = self.tools_dir / "scripts"
        self.scene_dir = root / "app/backend/dubstudio/pipeline"
        self.models_dir = self.tools_dir / "data/models"
        self.pipeline_python = str(root / "app/pipeline_env/bin/python")
        self.face_python = str(self.tools_dir / "mac_lipscan/venv_face/bin/python")


def test_a_command_is_rewritten_with_placeholders(tmp_path):
    """Every path on this Mac becomes a placeholder; a file somewhere else becomes a token with a signed link,
    so the worker needs to know nothing about this machine's layout."""
    cfg = FakeCfg(tmp_path)
    work, out = tmp_path / "library/work/TGS_ep34", tmp_path / "show/output/ep34"
    video = tmp_path / "TGS show/TGS EPISODE 34.mp4"
    video.parent.mkdir(parents=True); video.write_bytes(b"v" * 32)
    cmd = [cfg.pipeline_python, f"{cfg.scripts}/dubstitch.py", "words", "--work", str(work),
           "--srt", f"{work}/TGS_EP34_EN_sentences.srt", "--media", str(video),
           "--models", str(cfg.models_dir), "--out", f"{out}/mapping.xlsx"]
    got, files = cloud.tokenise(cmd, work, out, cfg)
    assert got == ["{PY}", "{SCRIPTS}/dubstitch.py", "words", "--work", "{WORK}",
                   "--srt", "{WORK}/TGS_EP34_EN_sentences.srt", "--media", "{F0}",
                   "--models", "{MODELS}", "--out", "{OUT}/mapping.xlsx"]
    assert files == {"{F0}": str(video)}
    # {SCRIPTS} lives inside {TOOLS}: the longer prefix has to win, or the script path breaks
    assert cloud.tokenise([f"{cfg.tools_dir}/data/x", f"{cfg.scripts}/y"], work, out, cfg)[0] == \
           ["{TOOLS}/data/x", "{SCRIPTS}/y"]
    # the runner's own placeholders are understood too
    assert cloud.tokenise(["{py}", "{fpy}"], work, out, cfg)[0] == ["{PY}", "{FPY}"]


def _episode(tmp_path):
    work = tmp_path / "work/TGS_ep34"; out = tmp_path / "out/ep34"
    work.mkdir(parents=True); out.mkdir(parents=True)
    return {"code": "TGS"}, {"id": 1, "number": 34, "work_dir": str(work), "output_dir": str(out)}


def test_two_episodes_do_not_share_a_scratch_tar(tmp_path, monkeypatch):
    """A bulk Render used to write every episode's work tar to the same `_render_tail_work_in.tar.gz` in the
    shared work parent. The PUT then sent the wrong number of bytes (Too much/little data for Content-Length)."""
    show = {"code": "TAB"}
    seen = []
    def fake_put(p, key):
        seen.append(Path(p).name)
        Path(p).write_bytes(b"x")  # keep the file so unlink in run_command is fine
    monkeypatch.setattr(cloud.storage, "put_file", fake_put)
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: f"{e['id']}/{rel}")
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
    monkeypatch.setattr(cloud, "submit", lambda job, **kw: "j")
    monkeypatch.setattr(cloud, "wait", lambda ids, **kw: ({"j": {"output": {"ok": True, "rc": 0, "seconds": 1, "sent": {}}}}, {}))
    monkeypatch.setattr(cloud, "_file_in_cloud", lambda db, s, e, path: "input/v.mp4")
    cfg = FakeCfg(tmp_path)
    paths = []
    for n, eid in ((16, 20), (17, 21)):
        work = tmp_path / f"work/TAB_ep{n}"; out = tmp_path / f"out/ep{n}"
        work.mkdir(parents=True); out.mkdir(parents=True)
        (work / "manifest_groups.json").write_text("{}")
        ep = {"id": eid, "number": n, "work_dir": str(work), "output_dir": str(out)}
        cloud.run_command({}, show, ep, "render_tail", [cfg.pipeline_python, "x"], cfg, log=lambda m: None)
        paths.append(cloud._scratch_tar(work, ep, "render_tail", "work_in").name)
    assert paths[0] != paths[1]
    assert "20_render_tail_work_in.tar.gz" in paths[0]
    assert "21_render_tail_work_in.tar.gz" in paths[1]
    assert seen[0] != seen[1]


def test_only_the_small_work_files_go_up_and_only_the_changes_come_back(tmp_path, monkeypatch):
    show, ep = _episode(tmp_path)
    work, out = Path(ep["work_dir"]), Path(ep["output_dir"])
    (work / "manifest.json").write_text('{"a": 1}')                     # small: travels
    (work / "lips_nets").mkdir(); (work / "lips_nets" / "range_0_15.json").write_text("{}")
    (work / "clean.mp4").write_bytes(b"x" * (2 << 20))                  # big: stays here
    (work / "input" / "src_video").mkdir(parents=True)
    (work / "input" / "src_video" / "v.mp4").write_text("tiny but an input")   # never travels in the tar
    bucket = tmp_path / "bucket"

    sent_job = {}

    def fake_submit(job, **kw):
        sent_job.update(job)
        return "job0"

    def fake_wait(ids, **kw):
        # the "worker": unpack what it was sent, write a result, send back only what changed
        got = tarfile.open(bucket / "cloud/words_work_in.tar.gz")
        names = sorted(got.getnames())
        box = tmp_path / "worker"; box.mkdir(exist_ok=True)
        got.extractall(box); got.close()
        (box / "rows.json").write_text('{"rows": []}')
        back = bucket / "cloud/words_work_out.tar.gz"
        back.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(back, "w:gz") as t:
            t.add(box / "rows.json", arcname="rows.json")
        return {"job0": {"output": {"ok": True, "rc": 0, "seconds": 12.3, "log": "all rows done",
                                    "sent": {"work": {"files": 1, "names": ["rows.json"]}},
                                    "_tar_names": names}}}, {}

    monkeypatch.setattr(cloud, "submit", fake_submit)
    monkeypatch.setattr(cloud, "wait", fake_wait)
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
    monkeypatch.setattr(cloud.storage, "put_file", lambda p, key: (bucket / key).parent.mkdir(parents=True, exist_ok=True)
                        or shutil.copy(p, bucket / key))
    monkeypatch.setattr(cloud.storage, "download", lambda key, path, **kw: shutil.copy(bucket / key, path))

    cfg = FakeCfg(tmp_path)
    cmd = [cfg.pipeline_python, f"{cfg.scripts}/dubstitch.py", "words", "--work", str(work)]
    res = cloud.run_command({}, show, ep, "words", cmd, cfg, log=lambda m: None)

    assert res["ok"] and res["seconds"] == 12.3
    names = res["_tar_names"]
    assert "manifest.json" in names and "lips_nets/range_0_15.json" in names
    assert "clean.mp4" not in names                                     # too big to post about
    assert not any(n.startswith("input/") for n in names)               # inputs travel as links, not in the tar
    assert (work / "rows.json").read_text() == '{"rows": []}'           # what the step wrote is back here
    assert sent_job["cmd"][0] == "{PY}" and sent_job["name"] == "words"


def test_a_big_file_already_in_the_work_folder_is_sent_on_its_own(tmp_path, monkeypatch):
    """The preview copy is written into the work folder and is far over 1 MB, so the next step (sharpness)
    cannot find it in the work tar. It has to travel as its own link, and the tar must still leave it out."""
    show, ep = _episode(tmp_path)
    work = Path(ep["work_dir"])
    proxy = work / "proxy_540.mp4"
    proxy.write_bytes(b"x" * (2 << 20))
    (work / "manifest.json").write_text("{}")
    bucket = tmp_path / "bucket"
    sent_job = {}

    def fake_submit(job, **kw):
        sent_job.update(job)
        return "job0"

    def fake_wait(ids, **kw):
        got = tarfile.open(bucket / "cloud/sharpness_work_in.tar.gz")
        names = sorted(got.getnames())
        got.close()
        back = bucket / "cloud/sharpness_work_out.tar.gz"
        back.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(back, "w:gz") as t:
            pass
        return {"job0": {"output": {"ok": True, "rc": 0, "seconds": 1, "sent": {}, "_tar_names": names}}}, {}

    monkeypatch.setattr(cloud, "submit", fake_submit)
    monkeypatch.setattr(cloud, "wait", fake_wait)
    monkeypatch.setattr(cloud, "_file_in_cloud", lambda db, s, e, path: f"input/{Path(path).name}")
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
    monkeypatch.setattr(cloud.storage, "put_file", lambda p, key: (bucket / key).parent.mkdir(parents=True, exist_ok=True)
                        or shutil.copy(p, bucket / key))
    monkeypatch.setattr(cloud.storage, "download", lambda key, path, **kw: shutil.copy(bucket / key, path))

    cfg = FakeCfg(tmp_path)
    cmd = [cfg.pipeline_python, str(tmp_path / "sharpness_tool.py"), "--video", str(proxy), "--out", str(work / "sharpness.json")]
    res = cloud.run_command({}, show, ep, "sharpness", cmd, cfg, log=lambda m: None)

    assert "{F0}" in sent_job["cmd"] and "{WORK}/proxy_540.mp4" not in sent_job["cmd"]
    assert sent_job["files"]["{F0}"].endswith("proxy_540.mp4") or "proxy_540" in sent_job["files"]["{F0}"]
    assert "proxy_540.mp4" not in res["_tar_names"]
    assert "manifest.json" in res["_tar_names"]


def test_a_script_in_the_input_folder_is_sent_even_though_it_is_small(tmp_path, monkeypatch):
    """Uploaded scripts live in work/input/, and that folder is left out of the work tar on purpose (the video
    is huge). The German SRT step then opens the .docx by the path it was given. A 40 KB script was neither in
    the tar nor sent as its own file, so the worker died with 'Package not found at /work/input/…docx'."""
    show, ep = _episode(tmp_path)
    work = Path(ep["work_dir"])
    script = work / "input" / "dub_script" / "Episode_34_Ehekrach_cleaned.docx"
    script.parent.mkdir(parents=True)
    script.write_bytes(b"PK" + b"x" * 40)
    (work / "note.txt").write_text("stays in the tar")
    bucket = tmp_path / "bucket"
    sent_job = {}

    def fake_submit(job, **kw):
        sent_job.update(job)
        return "job0"

    def fake_wait(ids, **kw):
        got = tarfile.open(bucket / "cloud/mksrt_dub_work_in.tar.gz")
        names = sorted(got.getnames())
        got.close()
        return {"job0": {"output": {"ok": True, "rc": 0, "seconds": 1, "sent": {}, "_tar_names": names}}}, {}

    monkeypatch.setattr(cloud, "submit", fake_submit)
    monkeypatch.setattr(cloud, "wait", fake_wait)
    monkeypatch.setattr(cloud, "_file_in_cloud", lambda db, s, e, path: f"input/{Path(path).name}")
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
    monkeypatch.setattr(cloud.storage, "put_file", lambda p, key: (bucket / key).parent.mkdir(parents=True, exist_ok=True)
                        or shutil.copy(p, bucket / key))
    monkeypatch.setattr(cloud.storage, "download", lambda key, path, **kw: shutil.copy(bucket / key, path))

    cfg = FakeCfg(tmp_path)
    cmd = [cfg.pipeline_python, f"{cfg.scripts}/mksrt.py", "--work", str(work), "--script", str(script)]
    res = cloud.run_command({}, show, ep, "mksrt_dub", cmd, cfg, log=lambda m: None)

    assert "{F0}" in sent_job["cmd"] and "{WORK}/input/" not in " ".join(sent_job["cmd"])
    assert sent_job["names"]["{F0}"] == "Episode_34_Ehekrach_cleaned.docx"
    assert "note.txt" in res["_tar_names"]
    assert not any(n.startswith("input/") for n in res["_tar_names"])


def test_a_step_that_fails_in_the_cloud_reads_like_a_local_failure(tmp_path, monkeypatch):
    show, ep = _episode(tmp_path)
    monkeypatch.setattr(cloud, "submit", lambda job, **kw: "j")
    monkeypatch.setattr(cloud, "wait", lambda ids, **kw: (
        {"j": {"output": {"ok": False, "rc": 1, "log": "Traceback …\nValueError: no rows in the manifest"}}}, {}))
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: "https://r2/x")
    with pytest.raises(RuntimeError, match="no rows in the manifest"):
        cloud.run_command({}, show, ep, "manifest", ["{py}", "x"], FakeCfg(tmp_path), log=lambda m: None)


def test_library_job_ships_this_boxes_ai_not_the_baked_image(tmp_path):
    """The rented /opt/app still dies on truncated locations JSON. The job takes THIS ai.py/library.py in W/_app
    so a Retry of library on a worker uses the closer without rebuilding the image."""
    import importlib.util
    work = tmp_path / "W"; work.mkdir()
    baked = tmp_path / "opt/scene/pipeline/library.py"
    baked.parent.mkdir(parents=True); baked.write_text("# old image\n")
    cmd = ["python", str(baked), str(work), str(work / "broll_library")]
    out = cloud.point_library_at_staged_app(cmd, work)
    staged = Path(out[1])
    assert staged == work / "_app" / "dubstudio" / "pipeline" / "library.py" and staged.is_file()
    lib = staged.read_text()
    assert "grab_png" in lib and "image_from_png" in lib
    assert out[2:] == [str(work), str(work / "broll_library")]
    spec = importlib.util.spec_from_file_location("shipped_ai", work / "_app" / "dubstudio" / "ai.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    text = '{"locations": {\n"Albert\'s study": "study",\n"Night forest, then Albe'
    got = mod.extract_json(text, "locations")
    assert got["locations"]["Albert's study"] == "study"
    assert "Night forest" not in json.dumps(got)


def test_lip_mesh_job_ships_this_boxs_script_not_the_baked_image(tmp_path):
    """The rented lipmesh.py still does frames[0] on an empty ffmpeg slice. The job takes THIS copy."""
    work = tmp_path / "W"; work.mkdir()
    baked = tmp_path / "opt/dubstudio/scripts/lipmesh.py"
    baked.parent.mkdir(parents=True); baked.write_text("# old image\n")
    cmd = ["python", str(baked), "--video", "/files/F0/ep.mp4", "--out-dir", str(work / "lips_mesh")]
    out = cloud.point_lipmesh_at_staged(cmd, work)
    staged = Path(out[1])
    assert staged == work / "_pipe" / "lipmesh.py" and staged.is_file()
    text = staged.read_text()
    assert "accurate seek" in text and "closed-mouth" in text
    assert out[2:] == ["--video", "/files/F0/ep.mp4", "--out-dir", str(work / "lips_mesh")]


def _keep_named_groups():
    """Load keep_named_groups from facemap.py without running the InsightFace script."""
    src = Path(__file__).resolve().parents[1] / "pipeline_tools" / "facemap.py"
    if not src.is_file():
        src = Path(__file__).resolve().parents[3] / "pipeline_tools" / "facemap.py"
    text = src.read_text()
    start = text.index("def keep_named_groups")
    chunk = text[start:]
    end = chunk.index("\np = argparse")
    ns = {}
    exec(compile(chunk[:end], str(src), "exec"), ns)
    return ns["keep_named_groups"]


def test_facemap_drops_claude_group_ids_that_are_not_clusters():
    """TAB EP48: Claude named group 83, which was not a ≥4-face identity cluster. Building
    centroids from gname via big[g] KeyError'd and killed a finished 5-minute scan."""
    keep = _keep_named_groups()
    big = {1: [0, 1, 2, 3]}
    gname, how = keep({1: "Mira", 83: "hallucinated"}, {1: "vote 3", 83: "Claude (reference faces)"}, big)
    assert gname == {1: "Mira"} and how == {1: "vote 3"}
    cent = {g: big[g] for g in gname}          # the crash was KeyError 83 on this lookup
    assert list(cent) == [1]


def test_facemap_job_ships_this_boxs_script_not_the_baked_image(tmp_path):
    """The rented facemap.py still KeyErrors on a Claude-invented group id. The job takes THIS copy."""
    work = tmp_path / "W"; work.mkdir()
    baked = tmp_path / "opt/dubstudio/scripts/facemap.py"
    baked.parent.mkdir(parents=True); baked.write_text("# old image\n")
    cmd = ["python", str(baked), "--video", "/files/F0/ep.mp4", "--library", str(work / "broll_library.json")]
    out = cloud.point_facemap_at_staged(cmd, work)
    staged = Path(out[1])
    assert staged == work / "_pipe" / "facemap.py" and staged.is_file()
    text = staged.read_text()
    assert "keep_named_groups" in text and "not a real identity group" in text
    assert "KeyError 83" in text
    assert out[2:] == ["--video", "/files/F0/ep.mp4", "--library", str(work / "broll_library.json")]


def test_render_tail_job_ships_this_boxs_dubstitch_not_the_baked_image(tmp_path):
    """The rented dubstitch concatenates leftover .ok files when the mp4s never arrived. The job takes THIS copy."""
    work = tmp_path / "W"; work.mkdir()
    baked = tmp_path / "opt/dubstudio/scripts/dubstitch.py"
    baked.parent.mkdir(parents=True); baked.write_text("# old image\n")
    cmd = ["python", str(baked), "render", "--work", str(work)]
    out = cloud.point_dubstitch_at_staged(cmd, work)
    staged = Path(out[1])
    assert staged == work / "_pipe" / "dubstitch.py" and staged.is_file()
    text = staged.read_text()
    assert "segment_mp4_ready" in text and "write_black_segment" in text
    assert "ffmpeg_err_is_truncated" in text and "clean master is the render" in text
    assert (work / "_pipe" / "pngsubs.py").is_file()
    assert (work / "_pipe" / "dynsubs.py").is_file()
    assert out[2:] == ["render", "--work", str(work)]


def test_decode_piece_retries_accurate_seek_when_fast_seek_is_empty(monkeypatch):
    from dubstudio.pipeline import lipmesh
    calls = []

    class Proc:
        def __init__(self, stdout, returncode=0):
            self.stdout, self.stderr, self.returncode = stdout, b"", returncode

    def fake_run(cmd, capture_output=True):
        calls.append(cmd)
        ss_i, in_i = cmd.index("-ss"), cmd.index("-i")
        if ss_i < in_i:                       # fast seek: empty, as TAB EP26-30
            return Proc(b"")
        return Proc(bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]))   # one 2×2 rgb frame

    monkeypatch.setattr(lipmesh.subprocess, "run", fake_run)
    fr, rc, err = lipmesh.decode_piece("VID.mp4", 240, 255, size=2)
    assert len(fr) == 1 and rc == 0
    assert len(calls) == 2
    assert calls[0].index("-ss") < calls[0].index("-i")
    assert calls[1].index("-i") < calls[1].index("-ss")


def test_library_is_claimed_by_the_cut_visit_so_it_goes_as_its_own_cloud_job(monkeypatch):
    """force_local is off; CLOUD_STEPS includes cut, so library is not enabled_for (would be skipped as a visit
    rider) but claimed_by_stretch is True — the runner sends it via _run_cmd_cloud with the staged app."""
    settings = {"RUNPOD_API_KEY": "k", "RUNPOD_CPU_ENDPOINT": "e", "CLOUD_STEPS": "cut", "CLOUD_AI": "true"}
    monkeypatch.setattr(cloud.envfile, "get", lambda k, d=None: settings.get(k, d))
    monkeypatch.setattr(cloud.envfile, "flag", lambda k, d=False: str(settings.get(k, d)).lower() == "true")
    assert cloud.claimed_by_stretch("library") and not cloud.enabled_for("library")


def test_the_two_steps_that_need_an_ai_key_stay_here_unless_the_owner_says_so(monkeypatch):
    """facemap, qc and library ask the app's AI client. Without a key facemap and qc do not fail -- they give a
    *worse* answer -- so they must not go to a rented machine by accident."""
    settings = {"RUNPOD_API_KEY": "k", "RUNPOD_CPU_ENDPOINT": "e", "CLOUD_STEPS": "all"}
    monkeypatch.setattr(cloud.envfile, "get", lambda k, d=None: settings.get(k, d))
    monkeypatch.setattr(cloud.envfile, "flag", lambda k, d=False: str(settings.get(k, d)).lower() == "true")
    assert not cloud.enabled_for("groups")                # the cut visit, not its own job
    assert not cloud.enabled_for("facemap") and not cloud.enabled_for("qc") and not cloud.enabled_for("library")
    assert not cloud.enabled_for("scene_edit")            # Claude, and no key yet
    settings["CLOUD_AI"] = "true"
    # qc and the shot library ride in a visit. They are not sent alone, and not at all without the key above.
    assert not cloud.enabled_for("qc") and not cloud.enabled_for("library")
    assert cloud.enabled_for("scene_edit") and cloud.stretch("scene")
    # the face map needs the CUDA image: no GPU endpoint, no cloud face map
    assert not cloud.enabled_for("facemap")
    settings["RUNPOD_GPU_ENDPOINT"] = "g"
    assert cloud.enabled_for("facemap") and cloud.kind_for("facemap") == "gpu" and cloud.kind_for("qc") == "cpu"


def test_the_work_folders_config_is_translated_both_ways(tmp_path, monkeypatch):
    """`config.json` holds absolute paths on this Mac, and pipeline stages read file paths out of it rather than
    from the command line. So it must arrive at the worker pointing at the worker's own files -- and what comes
    back must point at ours again, or the next local step looks for /work/… and fails."""
    show, ep = _episode(tmp_path)
    work, out = Path(ep["work_dir"]), Path(ep["output_dir"])
    video = tmp_path / "show/US/TGS EPISODE 34.mp4"
    video.parent.mkdir(parents=True); video.write_bytes(b"v" * 32)
    cfg = FakeCfg(tmp_path)
    (work / "config.json").write_text(json.dumps({
        "src_video": str(video), "models": str(cfg.models_dir),
        "dub_srt": f"{work}/TGS_EP34_DE.srt", "clean_final": f"{out}/final.mp4"}))
    bucket = tmp_path / "bucket"
    seen = {}

    def fake_wait(ids, **kw):
        with tarfile.open(bucket / "cloud/render_work_in.tar.gz") as t:
            seen["config"] = json.loads(t.extractfile("config.json").read())
        # the worker's render records where it put the video -- in the worker's own layout
        box = tmp_path / "w"; box.mkdir(exist_ok=True)
        (box / "config.json").write_text(json.dumps({**seen["config"], "video_out": "/work/video_groups.mp4"}))
        back = bucket / "cloud/render_work_out.tar.gz"
        back.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(back, "w:gz") as t:
            t.add(box / "config.json", arcname="config.json")
        return {"j": {"output": {"ok": True, "rc": 0, "seconds": 1,
                                 "sent": {"work": {"files": 1}}}}}, {}

    monkeypatch.setattr(cloud, "submit", lambda job, **kw: "j")
    monkeypatch.setattr(cloud, "wait", fake_wait)
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
    monkeypatch.setattr(cloud.storage, "put_file", lambda p, key: (bucket / key).parent.mkdir(parents=True, exist_ok=True)
                        or shutil.copy(p, bucket / key))
    monkeypatch.setattr(cloud.storage, "download", lambda key, path, **kw: shutil.copy(bucket / key, path))
    monkeypatch.setattr(cloud, "_file_in_cloud", lambda db, s, e, path: f"input/{Path(path).name}")

    cloud.run_command({}, show, ep, "render", [cfg.pipeline_python, f"{cfg.scripts}/dubstitch.py", "render",
                                               "--work", str(work), "--video", str(video)],
                      cfg, log=lambda m: None)

    # what the worker saw: its own paths, nothing of this Mac
    assert seen["config"]["src_video"] == "/files/F0/TGS EPISODE 34.mp4"
    assert seen["config"]["dub_srt"] == "/work/TGS_EP34_DE.srt"
    assert seen["config"]["clean_final"] == "/out/final.mp4"
    assert seen["config"]["models"] == "/opt/models"
    # what came back: ours again, including the path the step newly recorded
    now = json.loads((work / "config.json").read_text())
    assert now["src_video"] == str(video)
    assert now["dub_srt"] == f"{work}/TGS_EP34_DE.srt"
    assert now["video_out"] == f"{work}/video_groups.mp4"
    assert not [v for v in now.values() if isinstance(v, str)
                and (v.startswith("/work/") or v.startswith("/out/") or v.startswith("/files/"))]


def test_the_render_tail_is_refused_rather_than_broken(monkeypatch):
    """`render` onward each read the previous step's multi-GB video, and the work folder records where it is. One
    step per rented machine cannot work, so "all" must not mean "try it and fail at step 25"."""
    settings = {"RUNPOD_API_KEY": "k", "RUNPOD_CPU_ENDPOINT": "e", "CLOUD_STEPS": "all", "CLOUD_AI": "true"}
    monkeypatch.setattr(cloud.envfile, "get", lambda k, d=None: settings.get(k, d))
    monkeypatch.setattr(cloud.envfile, "flag", lambda k, d=False: str(settings.get(k, d)).lower() == "true")
    for step in ("render", "verify", "bookends", "transitions", "subs_dub", "subs_en", "check",
                 "analyze", "merged", "sheet", "manifest", "lipsync", "groups", "library", "qc"):
        assert not cloud.enabled_for(step), step
    for step in ("mksrt_dub", "words_src", "lip_mesh", "lip_merge", "translate", "scene_edit"):
        assert cloud.enabled_for(step), step
    assert cloud.stretch("match") and cloud.stretch("cut") and cloud.stretch("tail")
    assert "facemap" not in cloud.MATCH_CHAIN and "facemap" not in cloud.CUT_CHAIN
    # naming one visit does not turn the others on, and does not send its steps one by one
    settings["CLOUD_STEPS"] = "match"
    assert cloud.stretch("match") and not cloud.stretch("cut") and not cloud.chain_wanted()
    assert not cloud.enabled_for("analyze") and not cloud.enabled_for("sheet")
    # asking for one by name does not send it alone -- it turns on the one-machine chain instead
    settings["CLOUD_STEPS"] = "render"
    assert not cloud.enabled_for("render")
    assert cloud.chain_wanted()
    settings["CLOUD_STEPS"] = "lip_gpu"
    assert not cloud.chain_wanted()
    settings["CLOUD_STEPS"] = "all"
    assert cloud.chain_wanted()
    settings["CLOUD_STEPS"] = "tail"
    assert cloud.chain_wanted()


def test_the_render_tail_is_one_job_with_every_command(tmp_path, monkeypatch):
    """The six video steps go to one machine as one command -- the existing worker image, a tiny driver, no
    pipeline change. Inner commands already have the worker's paths, and the same source video is one file."""
    show, ep = _episode(tmp_path)
    work, out = Path(ep["work_dir"]), Path(ep["output_dir"])
    video = tmp_path / "show/US/TGS EPISODE 34.mp4"
    video.parent.mkdir(parents=True); video.write_bytes(b"v" * 32)
    font = tmp_path / "Poppins-Bold.ttf"; font.write_bytes(b"f" * 16)
    cfg = FakeCfg(tmp_path)
    (work / "config.json").write_text(json.dumps({"src_video": str(video), "dub_srt": f"{work}/x.srt"}))
    bucket = tmp_path / "bucket"
    sent = {}

    def fake_wait(ids, **kw):
        with tarfile.open(bucket / "cloud/render_tail_work_in.tar.gz") as t:
            sent["driver"] = t.extractfile("_cloud_chain.py").read().decode()
            sent["jobs"] = json.loads(t.extractfile("_cloud_chain.json").read())
        back = bucket / "cloud/render_tail_work_out.tar.gz"
        back.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(back, "w:gz") as t:
            pass
        return {"j": {"output": {"ok": True, "rc": 0, "seconds": 9, "sent": {}}}}, {}

    def fake_submit(job, **kw):
        sent["job"] = job
        sent["kind"] = kw.get("kind", "cpu")
        return "j"
    monkeypatch.setattr(cloud, "submit", fake_submit)
    monkeypatch.setattr(cloud, "wait", fake_wait)
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
    monkeypatch.setattr(cloud.storage, "put_file", lambda p, key: (bucket / key).parent.mkdir(parents=True, exist_ok=True)
                        or shutil.copy(p, bucket / key))
    monkeypatch.setattr(cloud.storage, "download", lambda key, path, **kw: shutil.copy(bucket / key, path)
                        if (bucket / key).exists() else Path(path).write_bytes(b""))
    monkeypatch.setattr(cloud, "_file_in_cloud", lambda db, s, e, path: f"input/{Path(path).name}")

    cmds = [
        ("render", [cfg.pipeline_python, f"{cfg.scripts}/dubstitch.py", "render", "--work", str(work)]),
        ("verify", [cfg.pipeline_python, f"{cfg.scripts}/dubstitch.py", "verify", "--work", str(work)]),
        ("subs_dub", [cfg.pipeline_python, f"{cfg.scripts}/dubstitch.py", "subs", "--work", str(work),
                      "--font", str(font), "--out", f"{out}/dub.mp4"]),
    ]
    res = cloud.run_chain({}, show, ep, cmds, cfg, log=lambda m: None)
    assert res["ok"] and res["seconds"] == 9
    assert sent["job"]["name"] == "render_tail"
    assert sent["job"]["cmd"] == ["{PY}", "{WORK}/_cloud_chain.py"]
    assert "CHAIN OK" in sent["driver"]
    names = [s["name"] for s in sent["jobs"]["steps"]]
    assert names == ["render", "verify", "subs_dub"]
    # worker paths, nothing of this Mac
    for item in sent["jobs"]["steps"]:
        joined = " ".join(item["cmd"])
        assert "/opt/pipeline_env/bin/python" in joined
        assert "/work/_pipe/dubstitch.py" in joined
        assert "/opt/dubstudio/scripts/dubstitch.py" not in joined
        assert str(tmp_path) not in joined
        assert "/work" in joined
    assert (work / "_pipe" / "pngsubs.py").is_file()
    assert (work / "_pipe" / "dynsubs.py").is_file()
    font_args = sent["jobs"]["steps"][2]["cmd"]
    assert any(a.startswith("/files/F") and a.endswith("Poppins-Bold.ttf") for a in font_args)
    assert any(a == "/out/dub.mp4" for a in font_args)
    # the two extra files (video via config, font via the command) both travelled
    assert len(sent["job"]["files"]) >= 2


def test_matching_is_one_visit_and_the_face_map_is_not_in_it(tmp_path, monkeypatch):
    """Automatic matching and the reading view share one machine. The face map is a different machine."""
    show, ep = _episode(tmp_path)
    work = Path(ep["work_dir"])
    cfg = FakeCfg(tmp_path)
    (work / "config.json").write_text("{}")
    bucket = tmp_path / "bucket"
    sent = {}

    def fake_wait(ids, **kw):
        with tarfile.open(bucket / "cloud/match_work_in.tar.gz") as t:
            sent["jobs"] = json.loads(t.extractfile("_cloud_chain.json").read())
        back = bucket / "cloud/match_work_out.tar.gz"
        back.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(back, "w:gz") as t:
            pass
        return {"j": {"output": {"ok": True, "rc": 0, "seconds": 4, "sent": {}}}}, {}

    def fake_submit(job, **kw):
        sent["job"] = job
        sent["kind"] = kw.get("kind", "cpu")
        return "j"

    monkeypatch.setattr(cloud, "submit", fake_submit)
    monkeypatch.setattr(cloud, "wait", fake_wait)
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
    monkeypatch.setattr(cloud.storage, "put_file", lambda p, key: (bucket / key).parent.mkdir(parents=True, exist_ok=True)
                        or shutil.copy(p, bucket / key))
    monkeypatch.setattr(cloud.storage, "download", lambda key, path, **kw: shutil.copy(bucket / key, path)
                        if (bucket / key).exists() else Path(path).write_bytes(b""))
    monkeypatch.setattr(cloud, "_file_in_cloud", lambda db, s, e, path: f"input/{Path(path).name}")

    cloud.run_chain({}, show, ep, [
        ("analyze", [cfg.pipeline_python, f"{cfg.scripts}/dubstitch.py", "analyze", "--work", str(work)]),
        ("merged", list(cloud.MERGED_CMD)),
    ], cfg, log=lambda m: None, name="match")
    assert sent["job"]["name"] == "match" and sent["kind"] == "cpu"
    names = [s["name"] for s in sent["jobs"]["steps"]]
    assert names == ["analyze", "merged"]
    assert "facemap" not in names
    shell = sent["jobs"]["steps"][1]["cmd"]
    assert shell[0] == "sh" and shell[1] == "-c"
    assert shell[2].startswith("/opt/pipeline_env/bin/python /opt/dubstudio/scripts/merged.py /work ")
    assert shell[2].endswith("> /work/merged.txt")
    assert str(tmp_path) not in shell[2]


def test_matching_sends_the_dub_audio_that_lives_in_the_input_folder(tmp_path, monkeypatch):
    """Matching is one chain. The chain used to freeze /work/input/dub_audio/….mp3 into its command list
    before the input folder was lifted out of the tar, so analyze died with '--dub-audio missing'."""
    show, ep = _episode(tmp_path)
    work = Path(ep["work_dir"])
    audio = work / "input" / "dub_audio" / "Episode_34_Ehekrach.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"a" * (cloud.SMALL + 8))
    cfg = FakeCfg(tmp_path)
    bucket = tmp_path / "bucket"
    sent = {}

    def fake_wait(ids, **kw):
        with tarfile.open(bucket / "cloud/match_work_in.tar.gz") as t:
            sent["jobs"] = json.loads(t.extractfile("_cloud_chain.json").read())
            sent["tar"] = t.getnames()
        back = bucket / "cloud/match_work_out.tar.gz"
        back.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(back, "w:gz") as t:
            pass
        return {"j": {"output": {"ok": True, "rc": 0, "seconds": 1, "sent": {}}}}, {}

    monkeypatch.setattr(cloud, "submit", lambda job, **kw: sent.update(job) or "j")
    monkeypatch.setattr(cloud, "wait", fake_wait)
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
    monkeypatch.setattr(cloud.storage, "put_file", lambda p, key: (bucket / key).parent.mkdir(parents=True, exist_ok=True)
                        or shutil.copy(p, bucket / key))
    monkeypatch.setattr(cloud.storage, "download", lambda key, path, **kw: shutil.copy(bucket / key, path)
                        if (bucket / key).exists() else Path(path).write_bytes(b""))
    monkeypatch.setattr(cloud, "_file_in_cloud", lambda db, s, e, path: f"input/{Path(path).name}")

    cloud.run_chain({}, show, ep, [
        ("analyze", [cfg.pipeline_python, f"{cfg.scripts}/dubstitch.py", "analyze", "--work", str(work),
                     "--dub-audio", str(audio)]),
        ("merged", list(cloud.MERGED_CMD)),
    ], cfg, log=lambda m: None, name="match")
    analyze = sent["jobs"]["steps"][0]["cmd"]
    assert any(a == "/files/F0/Episode_34_Ehekrach.mp3" for a in analyze)
    assert not any("/work/input/" in a for a in analyze)
    assert sent["names"]["{F0}"] == "Episode_34_Ehekrach.mp3"
    assert not any(n.startswith("input/") for n in sent["tar"])


def test_verify_failing_one_picture_does_not_stop_the_visit(tmp_path):
    """One weak frame used to exit verify, and the chain then never made the clean master.
    Transitions reported 'no clean master' even though the render itself had succeeded."""
    import sys
    work = tmp_path / "work"
    work.mkdir()
    (work / "_cloud_chain.json").write_text(json.dumps({"steps": [
        {"name": "verify", "cmd": [sys.executable, "-c", "import sys; print('BAD FRAME'); sys.exit(1)"]},
        {"name": "bookends", "cmd": [sys.executable, "-c", "print('BOOKENDS_RAN')"]},
    ]}))
    (work / "_cloud_chain.py").write_text(cloud.CHAIN_DRIVER)
    import subprocess
    p = subprocess.run([sys.executable, str(work / "_cloud_chain.py")], cwd=str(work), capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "BAD FRAME" in p.stdout and "BOOKENDS_RAN" in p.stdout and "CHAIN OK" in p.stdout


def test_the_runner_sends_each_visit_as_one_chain(tmp_path, monkeypatch):
    """The cut is mkdec, the sheet, the edit list, lip-sync, then captions.
    Groups stay on this Mac. The library rides along once groups has written its file.
    The face map is not in that list. The final check is on the render machine, after the sheet it reads."""
    from dubstudio.runner import Runner
    work = tmp_path / "work"
    work.mkdir()
    (work / "spec.py").write_text("# spec\n")
    log = tmp_path / "job.log"
    ctx = {"W": str(work), "scene_dir": str(work / "scene"), "library_dir": str(work / "lib"),
           "name_scene_sheet": str(tmp_path / "scene.xlsx"), "name_en_srt": str(tmp_path / "en.srt"),
           "en_srt_burn": str(tmp_path / "en.srt")}

    def job(step, jid, cmd):
        return {"id": jid, "episode_id": 1, "run": 1, "step": step, "status": "waiting",
                "log_path": str(log), "params": {"cmd": cmd, "ctx": ctx}}

    sheet = job("sheet", 1, ["{py}", "sheet"])
    by = {
        "sheet": sheet,
        "manifest": job("manifest", 2, ["{py}", "manifest"]),
        "lipsync": job("lipsync", 3, ["{py}", "lipsync"]),
        "groups": job("groups", 4, ["{py}", "groups"]),
        "library": job("library", 5, ["{py}", "library"]),
        "captions": job("captions", 6, ["{py}", "captions"]),
        "analyze": job("analyze", 7, ["{py}", "analyze"]),
        "merged": job("merged", 8, None),
    }
    sent = {}

    def fake_chain(db, show, ep, named, cfg, **kw):
        sent["name"] = kw.get("name")
        sent["steps"] = [n for n, _ in named]
        return {"ok": True, "rc": 0}

    monkeypatch.setattr(cloud, "run_chain", fake_chain)
    monkeypatch.setattr(cloud, "ai_key_allowed", lambda: True)
    runner = Runner(None)
    runner._jobs_by_step = lambda j: by
    runner._wait_for_step = lambda j, step: True
    runner._write_spec = lambda w: work / "spec.py"
    runner.episode_context = lambda eid: ({"code": "TGS", "id": 1}, {"id": 1, "number": 34, "work_dir": str(work)}, {})
    marked = []
    runner._set = lambda j, **vals: marked.append((j["step"], vals.get("status")))

    runner._run_cut(sheet)
    assert sent["name"] == "cut"
    # groups stays on this Mac (shared-footage trim); library waits until that file exists
    assert sent["steps"] == ["mkdec", "sheet", "manifest", "captions"]
    assert "facemap" not in sent["steps"] and "groups" not in sent["steps"]
    assert "lipsync" not in sent["steps"] and "library" not in sent["steps"]
    assert [s for s, st in marked if st == "done"] == ["captions", "manifest", "sheet"]

    (work / "manifest_groups.json").write_text("{}")
    marked.clear(); sent.clear()
    runner._run_cut(sheet)
    assert sent["steps"] == ["mkdec", "sheet", "manifest", "captions"]

    marked.clear()
    runner._run_match(by["analyze"])
    assert sent["name"] == "match" and sent["steps"] == ["analyze", "merged"]

    closing, jobs = runner._tail_closing(
        {"code": "TGS"}, {"number": 34},
        {"scene_sheet": job("scene_sheet", 9, None),
         "qc": job("qc", 10, ["{py}", "qc"]),
         "check": job("check", 11, ["{py}", "check"])})
    assert [n for n, _ in closing] == ["scene_sheet", "groups_review", "qc", "point_translation", "check"]
    assert [j["step"] for j in jobs] == ["scene_sheet", "qc", "check"]

    monkeypatch.setattr(cloud, "ai_key_allowed", lambda: False)
    closing, jobs = runner._tail_closing(
        {"code": "TGS"}, {"number": 34},
        {"scene_sheet": job("scene_sheet", 9, None),
         "qc": job("qc", 10, ["{py}", "qc"]),
         "check": job("check", 11, ["{py}", "check"])})
    assert [n for n, _ in closing] == ["scene_sheet", "groups_review"]
    assert [j["step"] for j in jobs] == ["scene_sheet"]


def test_a_file_named_only_in_config_still_travels(tmp_path):
    """`dubstitch.py manifest --work W` is the whole command -- and then `cmd_manifest` ffprobes
    `cfg["src_video"]`. The first real cloud run of it died on `could not convert string to float: ''`, which is
    ffprobe being handed a path that was never sent. Files named only in the config have to travel too."""
    show, ep = _episode(tmp_path)
    work, out = Path(ep["work_dir"]), Path(ep["output_dir"])
    video = tmp_path / "show/US/ep34.mp4"; video.parent.mkdir(parents=True); video.write_bytes(b"v" * 64)
    audio = tmp_path / "show/DE/ep34.mp3"; audio.parent.mkdir(parents=True); audio.write_bytes(b"a" * 32)
    (work / "config.json").write_text(json.dumps({
        "src_video": str(video), "dub_audio": str(audio),
        "dub_srt": f"{work}/x.srt",                  # inside the work folder: travels in the tar
        "clean_final": f"{out}/final.mp4",           # inside the output folder: same
        "models": str(tmp_path / "models"),          # a folder, not a file
        "labels": "DE,US"}))                         # not a path at all
    got = cloud._files_named_in_config(work, out, {})
    assert sorted(got.values()) == sorted([str(video), str(audio)])
    # one already on the command line is not sent twice
    got2 = cloud._files_named_in_config(work, out, {"{F0}": str(video)})
    assert list(got2.values()) == [str(audio)] and list(got2) == ["{F1}"]


def test_an_imported_clip_the_editor_used_is_sent_and_placed(tmp_path, monkeypatch):
    """A clip the editor dropped on the timeline is named only inside the edit list, and it is usually far
    over 1 MB, so the work tar leaves it behind. The render then looks for a path that exists only on the
    website. The clip has to travel, and the edit list the worker reads has to point at the worker's copy.
    A clip that was imported and never used stays here."""
    show, ep = _episode(tmp_path)
    work = Path(ep["work_dir"])
    used = work / "imports" / "impabc.mp4"
    small = work / "imports" / "impsmall.mp4"
    unused = work / "imports" / "impunused.mp4"
    used.parent.mkdir(parents=True)
    used.write_bytes(b"u" * (cloud.SMALL + 8))
    small.write_bytes(b"s" * 40)
    unused.write_bytes(b"n" * (cloud.SMALL + 8))
    manifest = {
        "total_ms": 5000,
        "segments": [
            {"it_start_ms": 0, "it_end_ms": 2000, "us_start_ms": 0, "us_end_ms": 2000},
            {"it_start_ms": 2000, "it_end_ms": 3500, "us_start_ms": 100, "us_end_ms": 1600, "video": str(used)},
            {"it_start_ms": 3500, "it_end_ms": 5000, "us_start_ms": 0, "us_end_ms": 1500, "video": str(small)},
        ],
    }
    (work / "manifest_groups.json").write_text(json.dumps(manifest))
    bucket = tmp_path / "bucket"
    sent = {}

    def fake_submit(job, **kw):
        sent.update(job)
        return "j"

    def fake_wait(ids, **kw):
        with tarfile.open(bucket / "cloud/render_work_in.tar.gz") as t:
            sent["manifest"] = json.loads(t.extractfile("manifest_groups.json").read())
            sent["tar_names"] = t.getnames()
        box = tmp_path / "w"; box.mkdir(exist_ok=True)
        back_m = dict(sent["manifest"])
        back_m["segments"] = [dict(s) for s in back_m["segments"]]
        (box / "manifest_groups.json").write_text(json.dumps(back_m))
        back = bucket / "cloud/render_work_out.tar.gz"
        back.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(back, "w:gz") as t:
            t.add(box / "manifest_groups.json", arcname="manifest_groups.json")
        return {"j": {"output": {"ok": True, "rc": 0, "seconds": 1, "sent": {"work": {"files": 1}}}}}, {}

    monkeypatch.setattr(cloud, "submit", fake_submit)
    monkeypatch.setattr(cloud, "wait", fake_wait)
    monkeypatch.setattr(cloud, "_file_in_cloud", lambda db, s, e, path: f"input/{Path(path).name}")
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
    monkeypatch.setattr(cloud.storage, "put_file", lambda p, key: (bucket / key).parent.mkdir(parents=True, exist_ok=True)
                        or shutil.copy(p, bucket / key))
    monkeypatch.setattr(cloud.storage, "download", lambda key, path, **kw: shutil.copy(bucket / key, path))

    cfg = FakeCfg(tmp_path)
    cloud.run_command({}, show, ep, "render", [cfg.pipeline_python, f"{cfg.scripts}/dubstitch.py", "render",
                                               "--work", str(work)], cfg, log=lambda m: None)

    assert list(sent["files"]) == ["{F0}"]
    assert sent["names"]["{F0}"] == "impabc.mp4"
    assert "impunused" not in json.dumps(sent["files"])
    segs = sent["manifest"]["segments"]
    assert "video" not in segs[0]
    assert segs[1]["video"] == "/files/F0/impabc.mp4"
    assert segs[2]["video"] == f"/work/imports/impsmall.mp4"
    assert "imports/impsmall.mp4" in sent["tar_names"]
    assert "imports/impabc.mp4" not in sent["tar_names"] and "imports/impunused.mp4" not in sent["tar_names"]
    # what comes back points at this machine again, so the next edit still finds the clip
    home = json.loads((work / "manifest_groups.json").read_text())
    assert home["segments"][1]["video"] == str(used)
    assert home["segments"][2]["video"] == str(small)


def test_the_video_named_in_config_is_sent_when_it_lives_in_the_input_folder(tmp_path, monkeypatch):
    """The edit list only says `--work`. It then ffprobes cfg['src_video'] and cfg['dub_audio'].
    For an uploaded episode those files live in work/input/, which is not in the tar, so the probe
    used to read nothing."""
    show, ep = _episode(tmp_path)
    work = Path(ep["work_dir"])
    video = work / "input" / "src_video" / "TGS_EPISODE_34.mp4"
    audio = work / "input" / "dub_audio" / "Episode_34_Ehekrach.mp3"
    video.parent.mkdir(parents=True)
    audio.parent.mkdir(parents=True)
    video.write_bytes(b"v" * 64)
    audio.write_bytes(b"a" * (cloud.SMALL + 8))
    (work / "config.json").write_text(json.dumps({"src_video": str(video), "dub_audio": str(audio), "labels": "DE,US"}))
    (work / "note.txt").write_text("stays")
    bucket = tmp_path / "bucket"
    sent = {}

    def fake_wait(ids, **kw):
        with tarfile.open(bucket / "cloud/cut_work_in.tar.gz") as t:
            sent["config"] = json.loads(t.extractfile("config.json").read())
            sent["tar"] = t.getnames()
        back = bucket / "cloud/cut_work_out.tar.gz"
        back.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(back, "w:gz") as t:
            pass
        return {"j": {"output": {"ok": True, "rc": 0, "seconds": 1, "sent": {}}}}, {}

    monkeypatch.setattr(cloud, "submit", lambda job, **kw: sent.update(job=job) or "j")
    monkeypatch.setattr(cloud, "wait", fake_wait)
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
    monkeypatch.setattr(cloud.storage, "put_file", lambda p, key: (bucket / key).parent.mkdir(parents=True, exist_ok=True)
                        or shutil.copy(p, bucket / key))
    monkeypatch.setattr(cloud.storage, "download", lambda key, path, **kw: shutil.copy(bucket / key, path)
                        if (bucket / key).exists() else Path(path).write_bytes(b""))
    monkeypatch.setattr(cloud, "_file_in_cloud", lambda db, s, e, path: f"input/{Path(path).name}")

    cfg = FakeCfg(tmp_path)
    cloud.run_chain({}, show, ep, [
        ("manifest", [cfg.pipeline_python, f"{cfg.scripts}/dubstitch.py", "manifest", "--work", str(work)]),
    ], cfg, log=lambda m: None, name="cut")
    assert sent["config"]["src_video"] == "/files/F0/TGS_EPISODE_34.mp4"
    assert sent["config"]["dub_audio"] == "/files/F1/Episode_34_Ehekrach.mp3"
    assert sent["job"]["names"]["{F0}"] == "TGS_EPISODE_34.mp4"
    assert sent["job"]["names"]["{F1}"] == "Episode_34_Ehekrach.mp3"
    assert "note.txt" in sent["tar"]
    assert not any(n.startswith("input/") for n in sent["tar"])


def test_a_path_that_merely_contains_work_or_out_is_not_mangled(tmp_path):
    """The reason this is not a text replace: `/work` is inside `/workspaces` and `/out` is inside `/output`.
    A plain replace turned the Mac's own paths into nonsense like
    `/Users/manish/conductor/<work dir>spaces/...` -- found by comparing a real cloud run's config.json."""
    pairs = [("/Users/me/conductor/workspaces/app/library/work/TGS_ep34", "/work"),
             ("/Users/me/Downloads/TGS US_DE/output/ep34", "/out")]
    conf = {
        "src_video": "/Users/me/Downloads/TGS US_DE/US/ep34.mp4",            # untouched
        "dub_srt": "/Users/me/conductor/workspaces/app/library/work/TGS_ep34/x.srt",
        "clean_final": "/Users/me/Downloads/TGS US_DE/output/ep34/final.mp4",
        "work_itself": "/Users/me/conductor/workspaces/app/library/work/TGS_ep34",
        "sibling": "/Users/me/conductor/workspaces/app/library/work/TGS_ep35/y.srt",   # a different episode
        "labels": "DE,US", "n": 7, "flag": True,
        "nested": {"list": ["/Users/me/conductor/workspaces/app/library/work/TGS_ep34/z.json", "not a path"]},
        "subs_outputs": {
            "/Users/me/Downloads/TGS US_DE/output/ep34/final_en_subs.mp4": {
                "srt": "/Users/me/Downloads/TGS US_DE/output/ep34/en.srt",
                "video": "/Users/me/Downloads/TGS US_DE/output/ep34/final.mp4",
            }
        },
    }
    there = json.loads(cloud._swap(json.dumps(conf), pairs))
    assert there["src_video"] == "/Users/me/Downloads/TGS US_DE/US/ep34.mp4"
    assert there["dub_srt"] == "/work/x.srt"
    assert there["clean_final"] == "/out/final.mp4"
    assert there["work_itself"] == "/work"
    assert there["sibling"] == "/Users/me/conductor/workspaces/app/library/work/TGS_ep35/y.srt"
    assert there["nested"]["list"] == ["/work/z.json", "not a path"]
    assert "/out/final_en_subs.mp4" in there["subs_outputs"]
    assert "/Users/me/Downloads/TGS US_DE/output/ep34/final_en_subs.mp4" not in there["subs_outputs"]
    assert there["subs_outputs"]["/out/final_en_subs.mp4"]["srt"] == "/out/en.srt"
    assert there["labels"] == "DE,US" and there["n"] == 7 and there["flag"] is True
    # and back again, exactly
    back = json.loads(cloud._swap(json.dumps(there), [(b, a) for a, b in pairs]))
    assert back == conf

    # the specific corruption: "workspaces" and "output" must survive a round trip untouched
    text = json.dumps({"a": "/Users/me/conductor/workspaces/elsewhere/f.mp4",
                       "b": "/Users/me/Downloads/TGS US_DE/output/other/f.mp4"})
    assert json.loads(cloud._swap(text, [(b, a) for a, b in pairs])) == json.loads(text)


def test_a_job_can_go_to_the_gpu_endpoint(monkeypatch):
    """Face map wants CUDA. The CPU endpoint's image has no CUDA libs, so submit/wait have to
    take a kind and hit RUNPOD_GPU_ENDPOINT, not silently reuse the CPU one."""
    seen = []

    def fake_call(path, body=None, timeout=60, kind="cpu"):
        seen.append((path, kind, (body or {}).get("input", {}).get("step")))
        if path == "/run":
            return {"id": "gpu-job"}
        if path.startswith("/status/"):
            return {"status": "COMPLETED", "output": {"ok": True, "seconds": 1}}
        if path == "/health":
            return {"workers": {}}
        return {}

    monkeypatch.setattr(cloud, "_call", fake_call)
    assert cloud.submit({"step": "command"}, kind="gpu") == "gpu-job"
    done, failed = cloud.wait(["gpu-job"], kind="gpu", poll=0)
    assert done and not failed
    assert seen[0] == ("/run", "gpu", "command")
    assert any(p.startswith("/status/") and k == "gpu" for p, k, _ in seen)


def test_skip_work_back_is_only_the_throwaway_caches():
    assert cloud.skip_work_back("segs_groups/000.mp4")
    assert cloud.skip_work_back("segs/a.mp4")
    assert cloud.skip_work_back("bookends/open.mp4")
    assert not cloud.skip_work_back("config.json")
    assert not cloud.skip_work_back("video_groups.mp4")
    assert not cloud.skip_work_back("manifest_groups.json")


def test_unpack_skips_segment_caches_and_keeps_config(tmp_path):
    """TAB EP53: extractall of segs_groups after CHAIN OK filled Render. Config and work videos still land."""
    src = tmp_path / "src"
    (src / "segs_groups" / "000.mp4").parent.mkdir(parents=True)
    (src / "segs_groups" / "000.mp4").write_bytes(b"seg")
    (src / "config.json").write_text("{}")
    (src / "video_groups.mp4").write_bytes(b"vid")
    tar = tmp_path / "back.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        t.add(src / "config.json", arcname="config.json")
        t.add(src / "video_groups.mp4", arcname="video_groups.mp4")
        t.add(src / "segs_groups", arcname="segs_groups")
    dest = tmp_path / "work"
    wrote, skipped = cloud.unpack_returned_tar(tar, dest, kind="work")
    assert (dest / "config.json").read_text() == "{}"
    assert (dest / "video_groups.mp4").read_bytes() == b"vid"
    assert not (dest / "segs_groups").exists()
    assert skipped >= 1 and wrote == 2


def test_unpack_out_keeps_the_finals(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "final_clean.mp4").write_bytes(b"final")
    tar = tmp_path / "out.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        t.add(src / "final_clean.mp4", arcname="final_clean.mp4")
    dest = tmp_path / "out"
    wrote, skipped = cloud.unpack_returned_tar(tar, dest, kind="out")
    assert skipped == 0 and wrote == 1
    assert (dest / "final_clean.mp4").read_bytes() == b"final"


def test_the_chain_driver_drops_segment_caches_after_ok(tmp_path):
    """The driver travels with the job, so this runs on the rented machine without an image rebuild."""
    import subprocess, sys
    work = tmp_path / "work"
    work.mkdir()
    (work / "segs_groups" / "000.mp4").parent.mkdir()
    (work / "segs_groups" / "000.mp4").write_bytes(b"x")
    (work / "_cloud_chain.json").write_text(json.dumps({"steps": [
        {"name": "check", "cmd": [sys.executable, "-c", "print('CHECK_OK')"]},
    ]}))
    (work / "_cloud_chain.py").write_text(cloud.CHAIN_DRIVER)
    p = subprocess.run([sys.executable, str(work / "_cloud_chain.py")], cwd=str(work),
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "CHAIN OK" in p.stdout and "scratch dropped" in p.stdout
    assert not (work / "segs_groups").exists()


def test_run_command_lands_outputs_before_work_and_skips_segs(tmp_path, monkeypatch):
    """After CHAIN OK the finals in /out must land even if the work tar is full of segs_groups."""
    show, ep = _episode(tmp_path)
    work, out = Path(ep["work_dir"]), Path(ep["output_dir"])
    video = tmp_path / "show/US/TGS EPISODE 34.mp4"
    video.parent.mkdir(parents=True); video.write_bytes(b"v" * 32)
    cfg = FakeCfg(tmp_path)
    (work / "config.json").write_text(json.dumps({
        "src_video": str(video), "dub_srt": f"{work}/x.srt", "clean_final": f"{out}/final.mp4"}))
    bucket = tmp_path / "bucket"

    def fake_wait(ids, **kw):
        box = tmp_path / "wback"; box.mkdir()
        (box / "config.json").write_text(json.dumps({"video_out": "/work/video_groups.mp4"}))
        (box / "segs_groups" / "000.mp4").parent.mkdir(parents=True)
        (box / "segs_groups" / "000.mp4").write_bytes(b"seg" * 100)
        back = bucket / "cloud/render_work_out.tar.gz"
        back.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(back, "w:gz") as t:
            t.add(box / "config.json", arcname="config.json")
            t.add(box / "segs_groups", arcname="segs_groups")
        obox = tmp_path / "oback"; obox.mkdir()
        (obox / "final_clean.mp4").write_bytes(b"final")
        with tarfile.open(bucket / "cloud/render_out_out.tar.gz", "w:gz") as t:
            t.add(obox / "final_clean.mp4", arcname="final_clean.mp4")
        return {"j": {"output": {"ok": True, "rc": 0, "seconds": 1,
                                 "sent": {"work": {"files": 2}, "out": {"files": 1}}}}}, {}

    monkeypatch.setattr(cloud, "submit", lambda job, **kw: "j")
    monkeypatch.setattr(cloud, "wait", fake_wait)
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
    monkeypatch.setattr(cloud.storage, "put_file", lambda p, key: (bucket / key).parent.mkdir(parents=True, exist_ok=True)
                        or shutil.copy(p, bucket / key))
    monkeypatch.setattr(cloud.storage, "download", lambda key, path, **kw: shutil.copy(bucket / key, path))
    monkeypatch.setattr(cloud, "_file_in_cloud", lambda db, s, e, path: f"input/{Path(path).name}")

    cloud.run_command({}, show, ep, "render",
                      [cfg.pipeline_python, f"{cfg.scripts}/dubstitch.py", "render", "--work", str(work)],
                      cfg, log=lambda m: None)

    assert (out / "final_clean.mp4").read_bytes() == b"final"
    assert not (work / "segs_groups").exists()
    now = json.loads((work / "config.json").read_text())
    assert now["video_out"] == f"{work}/video_groups.mp4"


def test_unpacking_an_older_config_keeps_the_english_srt_pointer():
    """TAB EP31: scene/cut unpacked a config packed before the AI translation wrote translation.out."""
    old = json.dumps({"translation": {"out": "/out/en.srt"}, "src_video": "/work/a.mp4"})
    incoming = json.dumps({"src_video": "/work/a.mp4", "video_out": "/work/v.mp4"})
    got = json.loads(cloud._keep_local_config_keys(old, incoming))
    assert got["translation"]["out"] == "/out/en.srt"
    assert got["video_out"] == "/work/v.mp4"


def test_unpacking_keeps_the_local_source_video_path(tmp_path):
    """TAB EP15: a later visit left src_video as /files/F0/15.mp4. The next render ffprobed that
    empty path (expected 3, got 1). Keep the real file this box already has."""
    src = tmp_path / "15.mp4"
    src.write_bytes(b"v" * 32)
    old = json.dumps({"src_video": str(src), "dub_audio": "/missing.wav"})
    incoming = json.dumps({"src_video": "/files/F0/15.mp4", "video_out": "/work/v.mp4"})
    got = json.loads(cloud._keep_local_config_keys(old, incoming))
    assert got["src_video"] == str(src)
    assert got["video_out"] == "/work/v.mp4"


def test_a_second_render_finds_the_source_video_when_config_still_names_work_input(tmp_path):
    """TAB EP21/23: after the first render, config.json still said `/work/input/src_video/….mp4`.
    The tar skips input/, so the next visit probed a missing file. Match by name and send it."""
    show, ep = _episode(tmp_path)
    work = Path(ep["work_dir"])
    video = work / "input" / "src_video" / "NO_SUB_TAB_FR_EP_21.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"v" * (cloud.SMALL + 8))
    (work / "config.json").write_text(json.dumps({
        "src_video": "/work/input/src_video/NO_SUB_TAB_FR_EP_21.mp4"}))

    class DB:
        def one(self, sql, args=()):
            return {"path": str(video), "kind": "src_video", "id": 1, "r2_key": None}
        def q(self, sql, args=()):
            return [{"path": str(video), "kind": "src_video"}]

    cloud.heal_config_inputs(DB(), ep, work)
    assert json.loads((work / "config.json").read_text())["src_video"] == str(video)
    got = cloud._files_named_in_config(work, Path(ep["output_dir"]), {})
    assert str(video) in got.values()
    extra = cloud._input_asset_files(DB(), ep, {})
    assert str(video) in extra.values()
    swapped = json.loads(cloud._swap(
        json.dumps({"src_video": "/work/input/src_video/NO_SUB_TAB_FR_EP_21.mp4"}),
        [(str(work), "/work"), (str(video), "/files/F0/NO_SUB_TAB_FR_EP_21.mp4")]))
    assert swapped["src_video"] == "/files/F0/NO_SUB_TAB_FR_EP_21.mp4"
    place = cloud._media_place_list({"{F0}": str(video)}, work)
    dests = place[0]["dests"]
    assert "/files/F0/NO_SUB_TAB_FR_EP_21.mp4" in dests
    assert "/work/input/src_video/NO_SUB_TAB_FR_EP_21.mp4" in dests


def test_heal_config_inputs_rewrites_a_worker_source_path(tmp_path):
    src = tmp_path / "input.mp4"
    src.write_bytes(b"v" * 32)
    work = tmp_path / "work"
    work.mkdir()
    (work / "config.json").write_text(json.dumps({"src_video": "/files/F0/15.mp4"}))

    class DB:
        def one(self, sql, args=()):
            return {"path": str(src)} if args[1] == "src_video" else None

    cloud.heal_config_inputs(DB(), {"id": 19}, work)
    assert json.loads((work / "config.json").read_text())["src_video"] == str(src)


def test_run_command_lands_outputs_even_when_check_failed(tmp_path, monkeypatch):
    """A failed check used to raise before downloading /out, so EP31's videos never landed."""
    show, ep = _episode(tmp_path)
    work, out = Path(ep["work_dir"]), Path(ep["output_dir"])
    video = tmp_path / "show/US/TGS EPISODE 34.mp4"
    video.parent.mkdir(parents=True); video.write_bytes(b"v" * 32)
    cfg = FakeCfg(tmp_path)
    (work / "config.json").write_text(json.dumps({
        "src_video": str(video), "translation": {"out": f"{out}/en.srt"}}))
    bucket = tmp_path / "bucket"

    def fake_wait(ids, **kw):
        back = bucket / "cloud/render_out_out.tar.gz"
        back.parent.mkdir(parents=True, exist_ok=True)
        obox = tmp_path / "oback"; obox.mkdir()
        (obox / "final_clean.mp4").write_bytes(b"final")
        with tarfile.open(back, "w:gz") as t:
            t.add(obox / "final_clean.mp4", arcname="final_clean.mp4")
        return {"j": {"output": {"ok": False, "rc": 1, "log": "FINAL CHECK FAILED: translated SRT missing",
                                 "sent": {"out": {"files": 1}}}}}, {}

    monkeypatch.setattr(cloud, "submit", lambda job, **kw: "j")
    monkeypatch.setattr(cloud, "wait", fake_wait)
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: f"https://r2/{key}")
    monkeypatch.setattr(cloud.storage, "put_file", lambda p, key: (bucket / key).parent.mkdir(parents=True, exist_ok=True)
                        or shutil.copy(p, bucket / key))
    monkeypatch.setattr(cloud.storage, "download", lambda key, path, **kw: shutil.copy(bucket / key, path))
    monkeypatch.setattr(cloud, "_file_in_cloud", lambda db, s, e, path: f"input/{Path(path).name}")

    with pytest.raises(RuntimeError, match="translated SRT missing"):
        cloud.run_command({}, show, ep, "render",
                          [cfg.pipeline_python, f"{cfg.scripts}/dubstitch.py", "render", "--work", str(work)],
                          cfg, log=lambda m: None)
    assert (out / "final_clean.mp4").read_bytes() == b"final"


def test_check_finds_the_english_srt_next_to_the_sheet(tmp_path):
    """The pointer can be missing; the file from AI translate is still next to the mapping sheet."""
    import importlib.util
    script = Path(__file__).resolve().parents[1] / "pipeline_tools" / "dubstitch.py"
    spec = importlib.util.spec_from_file_location("dubstitch_ep31", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "out"
    out.mkdir()
    srt = out / "TABPGCIT_EP31_IT_english_google.srt"
    srt.write_text("1\n")
    work = tmp_path / "work"; work.mkdir()
    found = mod._find_translated_srt(str(work), {"sheet_out": str(out / "mapping.xlsx")})
    assert found == str(srt)


def test_check_finds_subtitle_videos_when_subs_outputs_keys_still_say_out(tmp_path):
    """IMTS EP69/70: the videos landed next to the clean master, but config keys stayed `/out/...`."""
    import importlib.util
    script = Path(__file__).resolve().parents[1] / "pipeline_tools" / "dubstitch.py"
    spec = importlib.util.spec_from_file_location("dubstitch_imts", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "out"
    out.mkdir()
    clean = out / "IMTSDEUS_EP69_IT_final_clean.mp4"; clean.write_bytes(b"c")
    de = out / "IMTSDEUS_EP69_IT_final_de_subs.mp4"; de.write_bytes(b"d")
    en = out / "IMTSDEUS_EP69_IT_final_en_subs.mp4"; en.write_bytes(b"e")
    cfg = {
        "clean_final": str(clean),
        "sheet_out": str(out / "mapping.xlsx"),
        "subs_outputs": {
            "/out/IMTSDEUS_EP69_IT_final_de_subs.mp4": {"srt": "/out/de.srt"},
            "/out/IMTSDEUS_EP69_IT_final_en_subs.mp4": {"srt": "/out/en.srt"},
        },
    }
    assert mod._heal_subs_outputs(cfg)
    assert cfg["subs_outputs"][str(de)]["srt"] == "/out/de.srt"
    assert cfg["subs_outputs"][str(en)]["srt"] == "/out/en.srt"
    assert "/out/IMTSDEUS_EP69_IT_final_en_subs.mp4" not in cfg["subs_outputs"]
