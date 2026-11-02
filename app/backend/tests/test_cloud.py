"""Splitting a lip scan across rented machines (dubstudio/cloud.py).

No RunPod is touched: `submit` / `wait` are replaced by a fake endpoint that writes the files a real worker would
PUT, and R2 is a folder in tmp_path. What is checked is the part that was wrong twice on the real run: the pieces
must be **lipgpu's own pieces** with lipgpu's own names, and `ranges.json` -- which the two steps after the scan
read -- must hold the whole list, not one machine's share.
"""
import json, tarfile
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
    assert cloud.enabled_for("lip_gpu") and not cloud.enabled_for("render")

    settings["CLOUD_STEPS"] = "lip_gpu, render"               # a list, spaces allowed
    assert cloud.enabled_for("lip_gpu") and cloud.enabled_for("render")

    settings["CLOUD_STEPS"] = "all"
    assert cloud.enabled_for("render")

    settings["CLOUD_STEPS"] = ""                              # nothing set = everything runs here, as before
    assert not cloud.enabled_for("lip_gpu") and cloud.steps() == set()

    settings.update(CLOUD_STEPS="all", RUNPOD_API_KEY=None)
    assert not cloud.enabled_for("lip_gpu")                   # no key, no cloud
