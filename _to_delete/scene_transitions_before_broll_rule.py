"""transitions plan for a scene-edited episode: a transition only where the STORY moves to another scene; every cut inside a
scene stays a straight cut (dialogue / reaction cutting).
Story scene at a moment = the scene of the dub line there (W/scene/line_places.json from sceneedit.py, Claude's reading of
the story). Without that file: the library scene of the footage (older runs). A reaction shot of the same location taken
from another scene (location guard) is NOT a scene change.
Usage: scene_transitions.py <work> <broll_library.json>"""
import json, os, sys
W, LIBJ = sys.argv[1], sys.argv[2]
SH = json.load(open(LIBJ))["shots"]
M = json.load(open(f"{W}/manifest_groups.json"))["segments"]
LP = os.path.join(W, "scene", "line_places.json")
places = json.load(open(LP))["lines"] if os.path.exists(LP) else None
dub = [d for d in json.load(open(f"{W}/sentences_dub.json"))["sentences"] if d.get("start_ms") is not None] if places else []


def story_scene(ms):
    """scene of the line playing at dub ms (or the last line before it)"""
    best = None
    for d in dub:
        if d["start_ms"] <= ms: best = d
        else: break
    return (places.get(str(best["idx"])) or {}).get("scene") if best else None


def scene_at(t):
    return next((r["scene"] for r in SH if r["start"] <= t < r["end"]), None)


cuts, n_scene = {}, 0
cur = (places.get(str(dub[0]["idx"])) or {}).get("scene") if places and dub else None   # story scene at the last switch
for a, b in zip(M, M[1:]):
    if abs(b["us_start_ms"] - a["us_end_ms"]) <= 50: continue          # not a switch
    if places:                                                         # the first switch after the story changed scene
        sa, sb = cur, story_scene(b["it_start_ms"] + 1)
        if sb: cur = sb
    else:
        sa = scene_at((a["us_start_ms"] + a["us_end_ms"]) / 2000); sb = scene_at((b["us_start_ms"] + b["us_end_ms"]) / 2000)
    cuts[str(round(b["it_start_ms"]))] = "auto" if sa != sb else "none"
    n_scene += sa != sb
json.dump({"default": "none", "cuts": cuts}, open(f"{W}/transitions_plan.json", "w"), indent=1)
print(f"{len(cuts)} clip switches: {n_scene} scene changes get a transition, {len(cuts) - n_scene} straight cuts"
      + (" (story scenes from line_places.json)" if places else " (library scenes of the footage)"))
