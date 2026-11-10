"""Render concat must not treat leftover .ok files as done when the mp4s never arrived.

TAB EP20 died on `Impossible to open '/work/segs_groups/0000.mp4'` after printing
`rendered 190/190 segments`. The .ok signatures had come back in the work tar; the
mp4s had not. An empty last-frame seek must write a black clip instead of concatenating
a hole.
"""
import json, sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline_tools"))
import dubstitch as D


def test_a_leftover_ok_file_without_the_mp4_is_not_ready(tmp_path):
    mp4 = tmp_path / "0000.mp4"
    (tmp_path / "0000.mp4.ok").write_text("sig")
    assert D.segment_mp4_ready(str(mp4), "sig") is False
    mp4.write_bytes(b"x" * 80)
    assert D.segment_mp4_ready(str(mp4), "sig") is True
    assert D.segment_mp4_ready(str(mp4), "other") is False
    mp4.write_bytes(b"tiny")
    assert D.segment_mp4_ready(str(mp4), "sig") is False


def _seg(i, start, end):
    return dict(it_idx=i, it_start_ms=start, it_end_ms=end, us_start_ms=start, us_end_ms=end,
                stretch_factor=1.0, it_text="", us_text="")


def _work(tmp_path):
    work = tmp_path / "W"; work.mkdir()
    src = tmp_path / "src.mp4"; src.write_bytes(b"src")
    dub = tmp_path / "dub.mp3"; dub.write_bytes(b"dub")
    (work / "config.json").write_text(json.dumps({"src_video": str(src), "dub_audio": str(dub),
                                                  "manifest": "manifest_groups.json", "out_fps": 25}))
    (work / "manifest_groups.json").write_text(json.dumps({"segments": [_seg(0, 0, 40), _seg(1, 40, 80)]}))
    return work, src, dub


def _args(work, workers=1):
    return Namespace(work=str(work), manifest=None, out=None, subs="keep", workers=workers, max_seconds=100000)


class Proc:
    def __init__(self, returncode=0, stderr=""):
        self.returncode, self.stderr, self.stdout = returncode, stderr, ""


def test_stale_ok_files_are_re_rendered_and_empty_seeks_become_black(tmp_path, monkeypatch):
    """EP20: .ok files from the last rented machine, no mp4s, first seek empty."""
    work, src, dub = _work(tmp_path)
    segs = work / "segs_groups"; segs.mkdir()
    sig0 = D.seg_sig(_seg(0, 0, 40), 1, None, 64, 64, 25)
    (segs / "0000.mp4.ok").write_text(sig0)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = cmd[-1]
        if cmd[0] == "ffmpeg" and "-f" in cmd and cmd[cmd.index("-f") + 1] == "lavfi":
            Path(out).write_bytes(b"B" * 80)
            return Proc()
        if cmd[0] == "ffmpeg" and "-f" in cmd and cmd[cmd.index("-f") + 1] == "concat":
            listed = Path(cmd[cmd.index("-i") + 1]).read_text()
            for line in listed.splitlines():
                p = Path(line.split("'")[1])
                assert p.is_file() and p.stat().st_size >= 64, p
            Path(out).write_bytes(b"C" * 80)
            return Proc()
        if cmd[0] == "ffmpeg" and "-i" in cmd:
            src_i = cmd.index("-i")
            src_path = cmd[src_i + 1]
            if src_path == str(src):
                # empty seek: rc 0, no file — last empty frame / GOP hole
                return Proc()
            Path(out).write_bytes(b"M" * 80)
            return Proc()
        return Proc()

    monkeypatch.setattr(D, "probe_video", lambda p: (64, 64, 25))
    monkeypatch.setattr(D, "probe_duration_ms", lambda p: 80)
    monkeypatch.setattr(D.subprocess, "run", fake_run)
    D.cmd_render(_args(work))
    assert (segs / "0000.mp4").stat().st_size >= 64
    assert (segs / "0001.mp4").stat().st_size >= 64
    assert any("-f" in c and "lavfi" in c for c in calls)
    assert any("-f" in c and "concat" in c for c in calls)
    assert json.loads((work / "config.json").read_text())["video_out"].endswith("video_groups_nosubs.mp4")


def test_concat_is_refused_when_a_segment_mp4_is_still_missing(tmp_path, monkeypatch):
    work, src, dub = _work(tmp_path)

    def fake_run(cmd, **kw):
        p = Proc(1, "no frames")
        if kw.get("check"):
            raise D.subprocess.CalledProcessError(1, cmd, stderr="no frames")
        return p

    monkeypatch.setattr(D, "probe_video", lambda p: (64, 64, 25))
    monkeypatch.setattr(D.subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as e:
        D.cmd_render(_args(work))
    assert e.value.code == 3
