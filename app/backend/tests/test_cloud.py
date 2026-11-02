"""Splitting a lip scan across rented machines (dubstudio/cloud.py).

No RunPod is touched: `submit` / `wait` are replaced by a fake endpoint that writes the files a real worker would
PUT, and R2 is a folder in tmp_path. What is checked is the part that was wrong twice on the real run: the pieces
must be **lipgpu's own pieces** with lipgpu's own names, and `ranges.json` -- which the two steps after the scan
read -- must hold the whole list, not one machine's share.
"""
import json, shutil, tarfile
from pathlib import Path

import pytest

from dubstudio import cloud


def test_the_pieces_are_lipgpus_pieces_including_the_last_one():
    """lipgpu steps `while s < L`, so the last piece runs past the end of the video. Clamping it to the duration
    writes `range_615_627.json` -- a name nothing downstream looks for."""
    assert cloud.make_ranges(627.0)[-1] == [615.0, 630.0]
    assert len(cloud.make_ranges(627.0)) == 42
    assert cloud.make_ranges(30.0) == [[0.0, 15.0], [15.0, 30.0]]
    assert cloud.make_ranges(1.0) == [[0.0, 15.0]]            # a very short clip is still one whole piece
    assert cloud.make_ranges(0) == []


class FakeCloud:
    """A fake endpoint. `fail_first` shares die on the first attempt, as a reclaimed machine does."""

    def __init__(self, bucket, fail_first=0):
        self.bucket = Path(bucket)
        self.fail_first = fail_first
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
                (nets / f"range_{s:g}_{e:g}.json").write_text(json.dumps({"window": [s, e]}))
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
    assert cloud.enabled_for("groups")

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

    def fake_submit(job):
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


def test_a_step_that_fails_in_the_cloud_reads_like_a_local_failure(tmp_path, monkeypatch):
    show, ep = _episode(tmp_path)
    monkeypatch.setattr(cloud, "submit", lambda job: "j")
    monkeypatch.setattr(cloud, "wait", lambda ids, **kw: (
        {"j": {"output": {"ok": False, "rc": 1, "log": "Traceback …\nValueError: no rows in the manifest"}}}, {}))
    monkeypatch.setattr(cloud.storage, "work_key", lambda s, e, rel: str(rel))
    monkeypatch.setattr(cloud.storage, "presign", lambda key, *a, **k: "https://r2/x")
    with pytest.raises(RuntimeError, match="no rows in the manifest"):
        cloud.run_command({}, show, ep, "manifest", ["{py}", "x"], FakeCfg(tmp_path), log=lambda m: None)


def test_the_two_steps_that_need_an_ai_key_stay_here_unless_the_owner_says_so(monkeypatch):
    """facemap, qc and library ask the app's AI client. Without a key facemap and qc do not fail -- they give a
    *worse* answer -- so they must not go to a rented machine by accident."""
    settings = {"RUNPOD_API_KEY": "k", "RUNPOD_CPU_ENDPOINT": "e", "CLOUD_STEPS": "all"}
    monkeypatch.setattr(cloud.envfile, "get", lambda k, d=None: settings.get(k, d))
    monkeypatch.setattr(cloud.envfile, "flag", lambda k, d=False: str(settings.get(k, d)).lower() == "true")
    assert cloud.enabled_for("groups")
    assert not cloud.enabled_for("facemap") and not cloud.enabled_for("qc") and not cloud.enabled_for("library")
    settings["CLOUD_AI"] = "true"
    assert cloud.enabled_for("facemap") and cloud.enabled_for("qc") and cloud.enabled_for("library")


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

    monkeypatch.setattr(cloud, "submit", lambda job: "j")
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
    for step in ("render", "verify", "bookends", "transitions", "subs_dub", "subs_en", "check"):
        assert not cloud.enabled_for(step), step
    for step in ("mksrt_dub", "words_src", "analyze", "manifest", "lip_mesh", "lip_merge", "groups", "translate"):
        assert cloud.enabled_for(step), step
    # asking for one by name does not get round it either
    settings["CLOUD_STEPS"] = "render"
    assert not cloud.enabled_for("render")


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
