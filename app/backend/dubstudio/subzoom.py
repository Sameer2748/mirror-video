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
    """Top of the bright block that sits on the bottom of this frame, as a fraction of height.

    A row counts when it has some bright pixels but is not a full-frame flash. The block
    is whatever run of those rows reaches the bottom, so a taller caption starts higher
    and a short one stays low. None when the bottom of the frame has no such block."""
    h, w = gray.shape
    if h < 8 or w < 8:
        return None
    frac = (gray > 185).mean(axis=1)
    rows = frac > 0.035
    y = h - 1
    floor = int(h * 0.55)
    while y > floor and not rows[y]:
        y -= 1
    if y <= floor:
        return None
    bottom = y
    gap = 0
    top = bottom
    limit = int(h * 0.28)
    while y > limit:
        if rows[y]:
            top = y
            gap = 0
        else:
            gap += 1
            if gap > 12:
                break
        y -= 1
    if y <= limit and gap <= 12:
        return None
    if bottom - top < 2:
        return None
    return top / h


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
