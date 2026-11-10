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
    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode, self.stderr, self.stdout = returncode, stderr, stdout


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


EP28_STDERR = (
    "[in#0/mov,mp4,m4a,3gp,3g2,mj2 @ 0x5e2729568cc0] stream 0, offset 0x1dd6969e: partial file\n"
    "[h264 @ 0x5e2729c789c0] Invalid NAL unit size (48259 > 25621).\n"
    "[h264 @ 0x5e2729c789c0] Error splitting the input into NAL units.\n"
    "[vist#0:0/h264 @ 0x5e27295dc200] [dec:h264 @ 0x5e272956e3c0] Decoding error: Invalid data found when processing input\n"
)


def test_ep28_nal_stderr_is_truncated():
    assert D.ffmpeg_err_is_truncated(EP28_STDERR) is True
    assert D.ffmpeg_err_is_truncated("") is False
    assert D.ffmpeg_err_is_truncated("frame=  12 fps=25") is False


def _bookends_work(tmp_path):
    """TAB EP28 sizes: render 391.24s, source container 526.93s, last matched clip 450.92s."""
    work = tmp_path / "W"; work.mkdir()
    src = tmp_path / "src.mp4"; src.write_bytes(b"S" * 200)
    dub = tmp_path / "dub.mp3"; dub.write_bytes(b"D" * 200)
    main = work / "video_groups_nosubs.mp4"; main.write_bytes(b"M" * 200)
    srt = tmp_path / "src.srt"
    srt.write_text("1\n00:00:00,030 --> 00:00:01,000\nHi\n\n"
                   "2\n00:05:49,940 --> 00:05:50,940\nBye\n")
    (work / "config.json").write_text(json.dumps({
        "src_video": str(src), "dub_audio": str(dub), "src_srt": str(srt),
        "manifest": "manifest_groups.json", "out_fps": 25, "video_out": str(main),
    }))
    (work / "manifest_groups.json").write_text(json.dumps({
        "segments": [_seg(0, 0, 450_920)],
    }))
    out = work / "clean_noglow.mp4"
    return work, src, main, out


def _bookends_args(work, out):
    return Namespace(work=str(work), out=str(out), input=None, min_len=0.5, fade=0.25, no_ending=False)


def test_truncated_source_ending_is_left_out(tmp_path, monkeypatch):
    """Source pictures die before the container's last 76s. Skip the ending; keep the render."""
    work, src, main, out = _bookends_work(tmp_path)

    monkeypatch.setattr(D, "probe_video", lambda p: (64, 64, 25))
    monkeypatch.setattr(D, "probe_duration_ms", lambda p: 526_930 if str(p) == str(src) else 391_240)
    monkeypatch.setattr(D, "source_fade_ms", lambda *a, **k: None)
    monkeypatch.setattr(D, "video_decodes", lambda path, ss=None: ss is None or float(ss) < 1)

    def fake_run(cmd, **kw):
        if cmd[0] == "ffprobe":
            return Proc(stdout="48000,2")
        return Proc()

    monkeypatch.setattr(D.subprocess, "run", fake_run)
    D.cmd_bookends(_bookends_args(work, out))
    assert out.is_file() and out.read_bytes() == main.read_bytes()
    cfg = json.loads((work / "config.json").read_text())
    assert cfg["ending_s"] == 0
    assert cfg["opening_s"] == 0
    assert cfg["clean_final"] == str(out)


def test_truncated_bookends_concat_falls_back_to_the_render(tmp_path, monkeypatch):
    """Cut wrote an ending, concat -c copy kept the render length and NAL errors. Don't sys.exit(1)."""
    work, src, main, out = _bookends_work(tmp_path)

    monkeypatch.setattr(D, "probe_video", lambda p: (64, 64, 25))
    monkeypatch.setattr(D, "probe_duration_ms", lambda p: 526_930 if str(p) == str(src) else 391_240)
    monkeypatch.setattr(D, "source_fade_ms", lambda *a, **k: None)
    monkeypatch.setattr(D, "video_decodes", lambda path, ss=None: True)

    def fake_run(cmd, **kw):
        if cmd[0] == "ffprobe":
            return Proc(stdout="48000,2")
        if cmd[0] == "ffmpeg" and "-f" in cmd and cmd[cmd.index("-f") + 1] == "concat":
            Path(cmd[-1]).write_bytes(b"C" * 2000)
            return Proc(stderr=EP28_STDERR)
        if cmd[0] == "ffmpeg" and "-frames:v" in cmd:
            Path(cmd[-1]).write_bytes(b"E" * 2000)
            return Proc()
        return Proc()

    monkeypatch.setattr(D.subprocess, "run", fake_run)
    D.cmd_bookends(_bookends_args(work, out))
    assert out.is_file() and out.read_bytes() == main.read_bytes()
    cfg = json.loads((work / "config.json").read_text())
    assert cfg["ending_s"] == 0
    assert cfg["clean_final"] == str(out)
