"""The editor's Export button cuts the marked In→Out into an mp4 the other episode's bin can import."""
import subprocess
from pathlib import Path

from dubstudio import imports, scan


def _video(path, seconds):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True)


def test_a_marked_range_becomes_a_clip_the_bin_can_import(tmp_path):
    src = tmp_path / "source.mp4"
    _video(src, 4)
    out = tmp_path / "cut.mp4"
    imports.cut_range(src, out, 1000, 2500)
    pr = scan.probe(out)
    assert pr.get("video")
    assert abs(pr["duration"] - 1.5) < 0.25
    it = imports.add_path(tmp_path / "work", out)
    assert it["duration_ms"] > 1000
    assert Path(imports.path_of(tmp_path / "work", it["id"])).is_file()


def test_a_range_that_is_not_a_clip_is_refused(tmp_path):
    src = tmp_path / "source.mp4"
    _video(src, 1)
    try:
        imports.cut_range(src, tmp_path / "no.mp4", 0, 50)
    except ValueError as ex:
        assert "0.1" in str(ex)
    else:
        raise AssertionError("a 50 ms range was exported")
    try:
        imports.cut_range(src, tmp_path / "long.mp4", 0, imports.MAX_EXPORT_MS + 1)
    except ValueError as ex:
        assert "3 minutes" in str(ex)
    else:
        raise AssertionError("a range past the limit was exported")
