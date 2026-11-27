"""When scene-edit may cut more than one B-roll into a leftover.

A ~2 s hole must stay one shot or none (two–three flashes look jumpy).
A long leftover that needs ≥8 s of picture to reach 0.80× may use several
ranked same-people / same-place shots (each ≥2 s), in a pause — not on a
talking mouth.
"""
import time
from pathlib import Path

LONG_BROLL_S = 8.0
MAX_BROLL_PIECES = 3
LO = 0.8


def hole_broll_s(dub_s, src_s, lo=LO):
    """Seconds of B-roll needed so the own clip would play at `lo`×."""
    if dub_s <= 0:
        return 0.0
    return max(0.0, float(dub_s) - float(src_s) / lo)


def is_long_broll_hole(dub_s, src_s, lo=LO, long_s=LONG_BROLL_S):
    return hole_broll_s(dub_s, src_s, lo) >= long_s


def max_broll_shots(dub_s, src_s, lo=LO, long_s=LONG_BROLL_S, max_pieces=MAX_BROLL_PIECES):
    """1 = one-or-none (short hole). >1 = several ranked shots (long hole)."""
    if is_long_broll_hole(dub_s, src_s, lo, long_s):
        return max_pieces
    return 1


SCENE_EDIT_CACHE_RELS = (
    "scene/scene_manifest.par_cache.json",
    "scene_slowfix/broll_plan_editor.json",
    "scene_slowfix/decisions.json",
)


def park_scene_edit_cache(work_dir):
    """Move local scene-edit caches aside so Redo cannot reuse the last Opus plan."""
    w = Path(work_dir)
    old = w / "scene" / "_old" / time.strftime("%Y%m%d_%H%M%S")
    parked = []
    for rel in SCENE_EDIT_CACHE_RELS:
        cache = w / rel
        if not cache.is_file():
            continue
        old.mkdir(parents=True, exist_ok=True)
        dest = old / f"{cache.parent.name}_{cache.name}"
        cache.rename(dest)
        parked.append(str(dest))
    return parked
