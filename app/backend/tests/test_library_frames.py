"""Shot library: an empty ffmpeg PNG (last shot past the pictures) must not crash PIL."""
import io, subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "dubstudio" / "pipeline" / "library.py"


def _helpers():
    src = LIB.read_text()
    start = src.index("PNG_MAGIC =")
    end = src.index("\ndef camera_cuts")
    ns = {"io": io, "subprocess": subprocess}
    exec(src[start:end], ns)
    return ns


def test_empty_png_becomes_black():
    pytest.importorskip("PIL")
    ns = _helpers()
    im = ns["image_from_png"](b"")
    assert im.size == (256, 256) and im.getpixel((0, 0)) == (0, 0, 0)


def test_garbage_png_becomes_black():
    pytest.importorskip("PIL")
    ns = _helpers()
    im = ns["image_from_png"](b"not a png at all")
    assert im.getpixel((0, 0)) == (0, 0, 0)


def test_grab_png_retries_accurate_seek_when_fast_seek_is_empty(monkeypatch):
    ns = _helpers()
    calls = []

    class Proc:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(cmd, capture_output=True, text=False):
        calls.append(cmd)
        ss, inn = cmd.index("-ss"), cmd.index("-i")
        if ss < inn:
            return Proc(b"")
        return Proc(ns["PNG_MAGIC"] + b"payload")

    monkeypatch.setattr(subprocess, "run", fake_run)
    png = ns["grab_png"]("VID.mp4", 500.0)
    assert png.startswith(ns["PNG_MAGIC"])
    assert len(calls) == 2
    assert calls[0].index("-ss") < calls[0].index("-i")
    assert calls[1].index("-i") < calls[1].index("-ss")


def test_video_seconds_uses_the_shorter_stream(monkeypatch):
    ns = _helpers()
    outs = iter(["615.0", "390.0"])

    class Proc:
        def __init__(self, stdout):
            self.stdout = stdout

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc(next(outs)))
    assert ns["video_seconds"]("VID.mp4") == 390.0
