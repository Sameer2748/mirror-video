"""Auto zoom follows the burned-in caption: a higher caption needs more zoom, and that zoom
hides the caption when the picture is scaled from the top."""
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from dubstudio import subzoom
from dubstudio.config import cfg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline_tools"))
import dubstitch as D


def _clip(path: Path, bar_top: int, text: str):
    w, h = 640, 360
    im = Image.new("RGB", (w, h), (70, 110, 90))
    d = ImageDraw.Draw(im)
    d.rectangle((40, 40, 220, 160), fill=(180, 60, 40))
    # a burned-in caption: a bright block whose top sits at bar_top. Taller or higher
    # captions start further up the frame.
    d.rectangle((80, bar_top, w - 80, bar_top + 36), fill=(245, 245, 245))
    d.text((w // 2 - 70, bar_top + 10), text, fill=(20, 20, 20))
    png = path.with_suffix(".png")
    im.save(png)
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-loop", "1", "-i", str(png), "-t", "2",
                    "-r", "10", "-pix_fmt", "yuv420p", str(path)], check=True)
    return png


def _zoomed(src: Path, dest: Path, zoom: float):
    """The crop Render uses: same shape, anchored at the top centre, scaled back to the frame."""
    z = float(zoom)
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-i", str(src), "-frames:v", "1",
                    "-vf", f"crop=iw/{z:.4f}:ih/{z:.4f}:(iw-iw/{z:.4f})/2:0,scale=640:360", str(dest)], check=True)


def _bottom_is_caption(path: Path) -> bool:
    im = np.asarray(Image.open(path).convert("L"))
    return float(im[-12:, :].mean()) > 160


def test_saved_zoom_is_read_without_the_source_file(tmp_path):
    subzoom.write(tmp_path, {"found": True, "zoom": 1.42, "height": 720, "band_top": 510, "samples": 6})
    got = subzoom.saved(tmp_path)
    assert got["zoom"] == 1.42 and got["found"] is True
    (tmp_path / "source_zoom.json").unlink()
    (tmp_path / "config.json").write_text(json.dumps({"source_sub_zoom": 1.19}))
    got = subzoom.saved(tmp_path)
    assert got["zoom"] == 1.19 and got["found"] is True
    (tmp_path / "config.json").write_text("{}")
    assert subzoom.saved(tmp_path) is None
    (tmp_path / "config.json").write_text("{}")
    subzoom.write(tmp_path, {"found": True, "zoom": 1.33, "height": 720, "band_top": 500, "samples": 4})
    assert json.loads((tmp_path / "config.json").read_text())["source_sub_zoom"] == 1.33


def test_a_higher_caption_zooms_more_and_both_are_pushed_off_the_bottom(tmp_path):
    low = tmp_path / "low.mp4"
    high = tmp_path / "high.mp4"
    low_png = _clip(low, 300, "one line")
    high_png = _clip(high, 230, "two lines of source subs")
    a = subzoom.measure(low)
    b = subzoom.measure(high)
    assert a["found"] and b["found"]
    assert b["zoom"] > a["zoom"] + 0.1
    assert a["band_top"] > b["band_top"]
    out_a, out_b = tmp_path / "low_zoom.png", tmp_path / "high_zoom.png"
    _zoomed(low_png, out_a, a["zoom"])
    _zoomed(high_png, out_b, b["zoom"])
    assert not _bottom_is_caption(out_a)
    assert not _bottom_is_caption(out_b)
    # the red block near the top of the picture is still in frame
    assert float(np.asarray(Image.open(out_a).convert("RGB"))[80:120, 60:100, 0].mean()) > 120
    # the worker measures with the same rule, and the render filter matches the preview
    w = D.measure_source_sub_zoom(str(low))
    assert w["found"] and abs(w["zoom"] - a["zoom"]) < 0.02
    assert D.hide_frame({"video": "/clip.mp4"}, a["zoom"], 640, 360) is None
    assert D.hide_frame({}, 1.0, 640, 360) is None
    frame = D.hide_frame({"zoom": 1.1}, a["zoom"], 640, 360)
    assert frame.startswith(f"crop=iw/{a['zoom']:.4f}:ih/{a['zoom']:.4f}:")
    assert frame.endswith(",scale=640:360")
    bigger = D.hide_frame({"zoom": a["zoom"] + 0.4}, a["zoom"], 640, 360)
    assert f"iw/{a['zoom'] + 0.4:.4f}" in bigger


def test_render_zooms_the_source_and_leaves_an_imported_clip(tmp_path):
    """One episode render: the source segment loses its caption; an imported clip does not."""
    src = tmp_path / "src.mp4"
    _clip(src, 300, "burned in")
    other = tmp_path / "import.mp4"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=1:r=10",
                    "-pix_fmt", "yuv420p", str(other)], check=True)
    dub = tmp_path / "dub.wav"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    str(dub)], check=True)
    work = tmp_path / "W"
    work.mkdir()
    (work / "config.json").write_text(json.dumps({
        "src_video": str(src), "dub_audio": str(dub), "manifest": "manifest_groups.json", "out_fps": 10,
    }))
    (work / "manifest_groups.json").write_text(json.dumps({"segments": [
        {"it_idx": 0, "it_start_ms": 0, "it_end_ms": 400, "us_start_ms": 0, "us_end_ms": 400,
         "stretch_factor": 1, "it_text": "source"},
        {"it_idx": 1, "it_start_ms": 400, "it_end_ms": 800, "us_start_ms": 0, "us_end_ms": 400,
         "stretch_factor": 1, "it_text": "import", "video": str(other)},
    ]}))
    D.cmd_render(Namespace(work=str(work), manifest=None, out=None, subs="zoom", workers=1, max_seconds=100000))
    cfgj = json.loads((work / "config.json").read_text())
    assert cfgj["source_sub_zoom"] > 1.1
    segs = work / "segs_groups"
    src_png, imp_png = tmp_path / "src.png", tmp_path / "imp.png"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-i", str(segs / "0000.mp4"), "-frames:v", "1", str(src_png)], check=True)
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-i", str(segs / "0001.mp4"), "-frames:v", "1", str(imp_png)], check=True)
    assert not _bottom_is_caption(src_png)
    imp = np.asarray(Image.open(imp_png).convert("RGB"))
    assert float(imp[:, :, 2].mean()) > float(imp[:, :, 0].mean()) + 20


def test_bookends_opening_uses_the_same_zoom_as_the_episode(tmp_path, monkeypatch):
    """Opening is cut from the source: it must use the episode zoom so burned-in captions leave the frame."""
    src = tmp_path / "src.mp4"
    _clip(src, 300, "burned in")
    dub = tmp_path / "dub.wav"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    str(dub)], check=True)
    main = tmp_path / "main.mp4"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=green:s=640x360:d=1:r=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-pix_fmt", "yuv420p",
                    "-shortest", str(main)], check=True)
    work = tmp_path / "W"
    work.mkdir()
    srt = work / "src.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:01,400\nHi\n", encoding="utf-8")
    z = subzoom.measure(src)
    assert z["found"] and z["zoom"] > 1.1
    (work / "config.json").write_text(json.dumps({
        "src_video": str(src), "dub_audio": str(dub), "src_srt": str(srt),
        "manifest": "manifest_groups.json", "out_fps": 10, "video_out": str(main),
        "source_sub_zoom": z["zoom"],
    }))
    (work / "manifest_groups.json").write_text(json.dumps({"segments": [
        {"it_idx": 0, "it_start_ms": 0, "it_end_ms": 400, "us_start_ms": 1000, "us_end_ms": 1400,
         "stretch_factor": 1, "it_text": "hi"},
    ]}))
    monkeypatch.setattr(D, "detect_caption_box", lambda *_a, **_k: None)
    out = work / "clean_noglow.mp4"
    D.cmd_bookends(Namespace(work=str(work), out=str(out), input=str(main), min_len=0.5, fade=0.05, no_ending=True))
    opening = work / "bookends" / "opening.mp4"
    assert opening.is_file()
    png = tmp_path / "opening.png"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-i", str(opening), "-frames:v", "1", str(png)], check=True)
    assert not _bottom_is_caption(png)
    assert D.source_hide_zoom(str(work), {"source_sub_zoom": z["zoom"]}) == z["zoom"]
