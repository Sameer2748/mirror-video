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
        assert "/opt/dubstudio/scripts/dubstitch.py" in joined
        assert str(tmp_path) not in joined
        assert "/work" in joined
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


def test_the_runner_sends_each_visit_as_one_chain(tmp_path, monkeypatch):
    """The cut is mkdec, the sheet, the edit list, lip-sync, groups, the library, then captions.
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
    assert sent["steps"] == ["mkdec", "sheet", "manifest", "lipsync", "groups", "library", "captions"]
    assert "facemap" not in sent["steps"]
    assert [s for s, st in marked if st == "done"] == ["captions", "library", "groups", "lipsync", "manifest", "sheet"]

    marked.clear()
    runner._run_match(by["analyze"])
    assert sent["name"] == "match" and sent["steps"] == ["analyze", "merged"]

    closing, jobs = runner._tail_closing(
        {"code": "TGS"}, {"number": 34},
        {"scene_sheet": job("scene_sheet", 9, None),
         "qc": job("qc", 10, ["{py}", "qc"]),
         "check": job("check", 11, ["{py}", "check"])})
    assert [n for n, _ in closing] == ["scene_sheet", "groups_review", "qc", "check"]
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
    }
    there = json.loads(cloud._swap(json.dumps(conf), pairs))
    assert there["src_video"] == "/Users/me/Downloads/TGS US_DE/US/ep34.mp4"
    assert there["dub_srt"] == "/work/x.srt"
    assert there["clean_final"] == "/out/final.mp4"
    assert there["work_itself"] == "/work"
    assert there["sibling"] == "/Users/me/conductor/workspaces/app/library/work/TGS_ep35/y.srt"
    assert there["nested"]["list"] == ["/work/z.json", "not a path"]
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
