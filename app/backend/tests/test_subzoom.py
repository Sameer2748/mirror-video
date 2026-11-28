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
    broll = D.hide_frame({"video": "/clip.mp4"}, a["zoom"], 640, 360)
    assert broll is not None and broll == D.hide_frame({}, a["zoom"], 640, 360)
    assert D.hide_frame({}, 1.0, 640, 360) is None
    assert D.hide_frame({}, a["zoom"], 640, 360) is not None
    assert D.hide_frame(None, a["zoom"], 640, 360) is not None
    frame = D.hide_frame({"zoom": 1.1}, a["zoom"], 640, 360)
    assert frame.startswith(f"crop=iw/{a['zoom']:.4f}:ih/{a['zoom']:.4f}:")
    assert frame.endswith(",scale=640:360")
    bigger = D.hide_frame({"zoom": a["zoom"] + 0.4}, a["zoom"], 640, 360)
    assert f"iw/{a['zoom'] + 0.4:.4f}" in bigger


def test_render_zooms_source_and_imported_broll(tmp_path):
    """Library / Claude B-roll is often a source take: same zoom, so burned-in EN never stays on screen."""
    src = tmp_path / "src.mp4"
    _clip(src, 300, "burned in")
    other = tmp_path / "broll.mp4"
    _clip(other, 300, "broll subs")
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
    assert not _bottom_is_caption(imp_png)
    raw_imp = tmp_path / "raw_imp.png"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-i", str(other), "-frames:v", "1", str(raw_imp)], check=True)
    raw = np.asarray(Image.open(raw_imp).convert("L"))
    assert float(raw[300:336, :].mean()) > 160


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


def test_bookends_subs_zoom_flag_hides_opening_captions(tmp_path, monkeypatch):
    """Render already passes --subs zoom. Bookends must get the same flag or pads stay unzoomed."""
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
    (work / "config.json").write_text(json.dumps({
        "src_video": str(src), "dub_audio": str(dub), "src_srt": str(srt),
        "manifest": "manifest_groups.json", "out_fps": 10, "video_out": str(main),
    }))
    (work / "manifest_groups.json").write_text(json.dumps({"segments": [
        {"it_idx": 0, "it_start_ms": 0, "it_end_ms": 400, "us_start_ms": 1000, "us_end_ms": 1400,
         "stretch_factor": 1, "it_text": "hi"},
    ]}))
    monkeypatch.setattr(D, "detect_caption_box", lambda *_a, **_k: None)
    out = work / "clean_noglow.mp4"
    D.cmd_bookends(Namespace(work=str(work), out=str(out), input=str(main), min_len=0.5, fade=0.05,
                             no_ending=True, subs="zoom"))
    png = tmp_path / "opening.png"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-i", str(work / "bookends" / "opening.mp4"),
                    "-frames:v", "1", str(png)], check=True)
    assert not _bottom_is_caption(png)


def test_bookends_zooms_opening_when_auto_zoom_file_is_on(tmp_path, monkeypatch):
    """Per-episode auto-zoom: opening/ending still zoom even if render has not stored source_sub_zoom.

    Caption-box trimming is skipped: those pads stay, and the same zoom pushes source subs off."""
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
    (work / "hide_source_subs.json").write_text(json.dumps({"on": True}), encoding="utf-8")
    (work / "config.json").write_text(json.dumps({
        "src_video": str(src), "dub_audio": str(dub), "src_srt": str(srt),
        "manifest": "manifest_groups.json", "out_fps": 10, "video_out": str(main),
    }))
    (work / "manifest_groups.json").write_text(json.dumps({"segments": [
        {"it_idx": 0, "it_start_ms": 0, "it_end_ms": 400, "us_start_ms": 1000, "us_end_ms": 1400,
         "stretch_factor": 1, "it_text": "hi"},
    ]}))
    monkeypatch.setattr(D, "detect_caption_box", lambda *_a, **_k: (0, 40))
    monkeypatch.setattr(D, "scan_captions", lambda *_a, **_k: [0.05])
    out = work / "clean_noglow.mp4"
    D.cmd_bookends(Namespace(work=str(work), out=str(out), input=str(main), min_len=0.5, fade=0.05,
                             no_ending=True, subs="zoom"))
    opening = work / "bookends" / "opening.mp4"
    assert opening.is_file()
    png = tmp_path / "opening.png"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-i", str(opening), "-frames:v", "1", str(png)], check=True)
    assert not _bottom_is_caption(png)
    assert D.source_hide_zoom(str(work), json.loads((work / "config.json").read_text())) > 1.1


def _yellow_src(path: Path, seconds=3.0, fps=25):
    """1080 square source like KOTU: yellow burned-in captions on the bottom."""
    w = h = 1080
    im = Image.new("RGB", (w, h), (40, 70, 50))
    d = ImageDraw.Draw(im)
    d.rectangle((80, 80, 400, 360), fill=(160, 50, 40))
    d.rectangle((90, 900, w - 90, 1040), fill=(250, 210, 20))
    d.text((w // 2 - 80, 940), "YELLOW SUBS", fill=(20, 20, 20))
    png = path.with_suffix(".png")
    im.save(png)
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-loop", "1", "-i", str(png),
                    "-t", str(seconds), "-r", str(fps), "-pix_fmt", "yuv420p", str(path)], check=True)
    return png


def _bottom_is_yellow(path: Path) -> bool:
    im = np.asarray(Image.open(path).convert("RGB"))
    band = im[920:1040, :, :] if im.shape[0] >= 1040 else im[-160:, :, :]
    return float(band[:, :, 0].mean()) > 160 and float(band[:, :, 1].mean()) > 140 and float(band[:, :, 2].mean()) < 90


def test_bookends_cli_zooms_yellow_opening_and_ending(tmp_path):
    """Same path the worker runs: dubstitch.py bookends --subs zoom on a 1080 source with yellow subs."""
    src = tmp_path / "src.mp4"
    _yellow_src(src)
    dub = tmp_path / "dub.wav"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    str(dub)], check=True)
    main = tmp_path / "main.mp4"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=green:s=1080x1080:d=1:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-pix_fmt", "yuv420p",
                    "-shortest", str(main)], check=True)
    work = tmp_path / "W"
    work.mkdir()
    (work / "src.srt").write_text("1\n00:00:01,000 --> 00:00:01,400\nHi\n", encoding="utf-8")
    (work / "config.json").write_text(json.dumps({
        "src_video": str(src), "dub_audio": str(dub), "src_srt": str(work / "src.srt"),
        "manifest": "manifest_groups.json", "out_fps": 25, "video_out": str(main),
    }))
    (work / "manifest_groups.json").write_text(json.dumps({"segments": [
        {"it_idx": 0, "it_start_ms": 0, "it_end_ms": 400, "us_start_ms": 1000, "us_end_ms": 1400,
         "stretch_factor": 1, "it_text": "hi"},
    ]}))
    # Same as after Render: the measured zoom is already in the work folder (KOTU 124 was 1.713x).
    measured = D.measure_source_sub_zoom(str(src))
    if not measured.get("found"):
        measured = {"found": True, "zoom": 1.713, "height": 1080, "band_top": 644, "samples": 12}
    (work / "source_zoom.json").write_text(json.dumps(measured), encoding="utf-8")
    src_png = tmp_path / "src_frame.png"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-i", str(src), "-frames:v", "1", str(src_png)], check=True)
    assert _bottom_is_yellow(src_png)
    tool = Path(__file__).resolve().parents[1] / "pipeline_tools" / "dubstitch.py"
    py = sys.executable
    out_zoom = work / "clean_zoom.mp4"
    ran = subprocess.run([py, str(tool), "bookends", "--work", str(work), "--out", str(out_zoom),
                          "--in", str(main), "--min-len", "0.5", "--fade", "0.05", "--subs", "zoom"],
                         cwd=str(tool.parent), capture_output=True, text=True)
    log = (ran.stdout or "") + (ran.stderr or "")
    assert "source subs zoom" in log, log[-800:]
    assert (work / "bookends" / "opening.mp4").is_file()
    assert (work / "bookends" / "ending.mp4").is_file()
    opening = tmp_path / "opening_zoom.png"
    ending = tmp_path / "ending_zoom.png"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-i", str(work / "bookends" / "opening.mp4"),
                    "-frames:v", "1", str(opening)], check=True)
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-sseof", "-0.2", "-i", str(work / "bookends" / "ending.mp4"),
                    "-frames:v", "1", str(ending)], check=True)
    assert not _bottom_is_yellow(opening)
    assert not _bottom_is_yellow(ending)
    from dubstudio.pipeline.steps import plan
    from dubstudio import presets
    show = {"code": "KOTU", "profile": {**presets.preset("NEW"), "hide_source_subs": True}}
    ep = {"number": 124, "work_dir": str(work), "output_dir": str(tmp_path / "out")}
    assets = {"src_video": str(src), "dub_audio": str(dub)}
    cmds = {s["step"]: s["cmd"] for s in plan(show, ep, assets)[1]}
    bo = cmds["bookends"]
    tr = cmds["transitions"]
    assert "--subs" in bo and bo[bo.index("--subs") + 1] == "zoom"
    assert "--subs" in tr and tr[tr.index("--subs") + 1] == "zoom"


def test_a_transition_does_not_use_the_unused_source_between_two_takes():
    """EP105-style jump: 9.58s → 10.49s. The dissolve must stay inside each take, not the leftover in between."""
    cfg = {"opening_s": 0}
    segs = [
        {"it_start_ms": 0, "it_end_ms": 8680, "us_start_ms": 860, "us_end_ms": 9580},
        {"it_start_ms": 8680, "it_end_ms": 12810, "us_start_ms": 10490, "us_end_ms": 13410},
    ]
    sw = D.clip_switches(cfg, segs, 50)
    assert len(sw) == 1
    cut = sw[0]
    assert abs(cut["t"] - 8.68) < 0.02
    assert cut["out"]["u0"] == 0.860 and cut["out"]["u1"] == 9.580
    assert cut["inn"]["u0"] == 10.490 and cut["inn"]["u1"] == 13.410
    assert cut["out"]["u1"] <= cut["inn"]["u0"]
    assert cut["out"].get("video") is None
    assert D.hide_frame({"video": "/broll.mp4"}, 1.236, 1080, 1080) == D.hide_frame({}, 1.236, 1080, 1080)


def test_transitions_zoom_imported_broll_so_source_subs_do_not_flash(tmp_path):
    """Same vf transitions grab() uses on an imported clip: captioned B-roll must leave the frame."""
    src = tmp_path / "src.mp4"
    broll = tmp_path / "broll.mp4"
    _clip(src, 300, "src subs")
    _clip(broll, 300, "broll subs")
    z = subzoom.measure(src)
    assert z["found"] and z["zoom"] > 1.1
    hf = D.hide_frame({"video": str(broll)}, z["zoom"], 640, 360)
    assert hf is not None
    grabbed = tmp_path / "grab.png"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-i", str(broll), "-frames:v", "1", "-vf", hf,
                    str(grabbed)], check=True)
    assert not _bottom_is_caption(grabbed)
    raw = tmp_path / "broll_raw.png"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-i", str(broll), "-frames:v", "1", str(raw)], check=True)
    assert float(np.asarray(Image.open(raw).convert("L"))[300:336, :].mean()) > 160
    segs = [
        {"it_start_ms": 0, "it_end_ms": 800, "us_start_ms": 0, "us_end_ms": 800},
        {"it_start_ms": 800, "it_end_ms": 1600, "us_start_ms": 0, "us_end_ms": 800, "video": str(broll)},
    ]
    cut = D.clip_switches({}, segs, 50)[0]
    assert cut["inn"]["video"] == str(broll)
