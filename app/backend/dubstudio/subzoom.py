"""How far to zoom a source video so its burned-in subtitles fall off the bottom.

The subtitle block is measured from the frames. A one-line caption and a three-line
caption do not sit at the same height, so the zoom is the amount that puts the top
of that block on the bottom edge of the frame. The picture stays anchored to the top.
"""
import subprocess

import numpy as np

from .config import cfg
from . import scan


def zoom_for_band(top_frac: float) -> float:
    """Scale that pushes a band whose top is `top_frac` of the frame (0 at the top)
    just past the bottom edge, with a small margin so the glyphs are not cut in half."""
    if top_frac <= 0 or top_frac >= 0.98:
        return 1.0
    keep = min(0.97, max(top_frac - 0.012, 0.42))
    return round(min(2.4, max(1.0, 1.0 / keep)), 3)


def _band_top_fraction(gray: np.ndarray):
    """Top of the burned-in caption in the lower part of this frame, as a fraction of height.

    A caption is a short run of rows that are partly bright (the glyphs, or a solid bar)
    and that begins inside the lower part of the frame. A bright floor, a lamp or a white
    shirt is already bright at the top of that band, so that run is the picture, not a
    caption. None when no caption run is there."""
    h, w = gray.shape
    if h < 8 or w < 8:
        return None
    frac = (gray > 170).mean(axis=1)
    y0 = int(h * 0.58)
    rows = (frac[y0:] > 0.015) & (frac[y0:] < 0.92)
    runs = []
    start = None
    gap = 0
    last = None
    for i, on in enumerate(rows):
        if on:
            if start is None:
                start = i
            gap = 0
            last = i
        elif start is not None:
            gap += 1
            if gap > 5:
                runs.append((start, last))
                start = None
    if start is not None:
        runs.append((start, last))
    min_h = max(3, int(h * 0.018))
    max_h = max(min_h, int(h * 0.18))
    good = [(a, b) for a, b in runs if a > 0 and min_h <= (b - a + 1) <= max_h]
    if not good:
        return None
    a, _b = max(good, key=lambda r: r[1])
    return (y0 + a) / h


def measure(video, samples: int = 12) -> dict:
    """Sample the video and return the zoom that hides its burned-in subtitles.

    `found` is false when no bottom caption block shows up often enough to trust."""
    pr = scan.probe(video)
    vid = pr.get("video") or {}
    w, h = int(vid.get("width") or 0), int(vid.get("height") or 0)
    dur = float(pr.get("duration") or 0)
    if w < 16 or h < 16 or dur <= 0:
        return {"found": False, "zoom": 1.0, "height": h, "band_top": None, "samples": 0}
    n = samples if dur >= 2 else max(2, int(dur * 4))
    times = [dur * (i + 0.5) / n for i in range(n)]
    sw = 480
    sh = max(2, int(round(h * (sw / w))))
    if sh % 2:
        sh += 1
    tops = []
    for t in times:
        raw = subprocess.run(
            [cfg.ffmpeg, "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
             "-vf", f"scale={sw}:{sh},format=gray", "-f", "rawvideo", "-"],
            capture_output=True).stdout
        if len(raw) < sw * sh:
            continue
        im = np.frombuffer(raw[: sw * sh], np.uint8).reshape(sh, sw)
        top = _band_top_fraction(im)
        if top is not None:
            tops.append(top)
    if len(tops) < 2:
        return {"found": False, "zoom": 1.0, "height": h, "band_top": None, "samples": len(tops)}
    tops.sort()
    # The higher captions decide the zoom. One stray bright frame does not.
    top_frac = tops[max(0, int(round(0.15 * (len(tops) - 1))))]
    zoom = zoom_for_band(top_frac)
    return {
        "found": zoom > 1.01,
        "zoom": zoom,
        "height": h,
        "band_top": int(round(top_frac * h)),
        "samples": len(tops),
    }
