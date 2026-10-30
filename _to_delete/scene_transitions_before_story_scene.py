"""transitions plan for a scene-edited episode: a transition only where the story moves to another scene (library scene
changes between two pieces); every cut inside a scene stays a straight cut (dialogue / reaction cutting)."""
import json, sys
W, LIBJ = sys.argv[1], sys.argv[2]
SH = json.load(open(LIBJ))["shots"]
def scene_at(t):
    return next((r["scene"] for r in SH if r["start"] <= t < r["end"]), None)
M = json.load(open(f"{W}/manifest_groups.json"))["segments"]
cuts, n_scene = {}, 0
for a, b in zip(M, M[1:]):
    if abs(b["us_start_ms"] - a["us_end_ms"]) <= 50: continue          # not a switch
    sa = scene_at((a["us_start_ms"] + a["us_end_ms"]) / 2000); sb = scene_at((b["us_start_ms"] + b["us_end_ms"]) / 2000)
    cuts[str(round(b["it_start_ms"]))] = "auto" if sa != sb else "none"
    n_scene += sa != sb
json.dump({"default": "none", "cuts": cuts}, open(f"{W}/transitions_plan.json", "w"), indent=1)
print(f"{len(cuts)} clip switches: {n_scene} scene changes get a transition, {len(cuts) - n_scene} straight cuts")
