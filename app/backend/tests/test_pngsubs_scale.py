"""Box subtitles are drawn on a 1080 canvas and must scale to the video height (4K / 8K)."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline_tools"))
import pngsubs

FONT = next(
    (p for p in (
        str(Path(__file__).resolve().parents[1] / "frontend_dist" / "fonts" / "Poppins-Bold.ttf"),  # app/backend/frontend_dist
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ) if Path(p).is_file()),
    None,
)


def test_overlay_filter_scales_layer_to_video_height():
    f = pngsubs.overlay_filter()
    assert "scale2ref" in f and "h=ih" in f
    assert "overlay=(W-w)/2:H-h" in f
    assert "[0:v][1:v]overlay" not in f


def _solid(path, w, h, dur=1.0):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x66aaff:s={w}x{h}:d={dur}:r=25",
         "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={dur}",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def _srt(path):
    path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHELLO BOX\n", encoding="utf-8")


def _dark_band_h(frame_jpg, frame_h):
    """How many rows from the bottom are darker than the blue field (the translucent box)."""
    from PIL import Image
    im = Image.open(frame_jpg).convert("L")
    pix = im.load()
    w, h = im.size
    assert h == frame_h
    cx = w // 2
    field = pix[cx, h // 4]
    n = 0
    for y in range(h - 1, -1, -1):
        if pix[cx, y] < field - 25:
            n += 1
        elif n:
            break
    return n


@pytest.mark.skipif(not FONT, reason="no bold font on this box")
def test_box_on_2160_is_twice_the_1080_box(tmp_path):
    srt = tmp_path / "a.srt"; _srt(srt)
    sizes = {}
    for side in (1080, 2160):
        vid = tmp_path / f"v{side}.mp4"
        out = tmp_path / f"o{side}.mp4"
        work = tmp_path / f"w{side}"
        _solid(vid, side, side, 1.0)
        pngsubs.START, pngsubs.MIN, pngsubs.BOTTOM = 38, 30, 71
        pngsubs.burn(str(vid), str(srt), str(out), str(work), chunk=2, max_seconds=120, font=FONT)
        frame = tmp_path / f"f{side}.jpg"
        subprocess.run(["ffmpeg", "-y", "-ss", "0.3", "-i", str(out), "-frames:v", "1", str(frame)],
                       check=True, capture_output=True)
        sizes[side] = _dark_band_h(frame, side)
    assert sizes[1080] > 20, sizes
    assert sizes[2160] > sizes[1080] * 1.6, sizes
