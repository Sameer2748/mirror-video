"""Compare two manifest_groups.json: for every 0.1 s of the dub timeline, which source moment is on screen.
Usage: python3 compare_timelines.py REFERENCE/manifest_groups.json OTHER/manifest_groups.json"""
import json, sys
def load(p): return json.load(open(p))
def src_at(m, step=100):
    out = []
    segs = sorted(m["segments"], key=lambda s: s["it_start_ms"]); k = 0
    for t in range(0, int(m["total_ms"]), step):
        while k < len(segs) - 1 and segs[k]["it_end_ms"] <= t: k += 1
        s = segs[k]
        if s.get("kind") != "clip" or s.get("us_start_ms") is None or not (s["it_start_ms"] <= t < s["it_end_ms"]):
            out.append(None); continue
        f = (t - s["it_start_ms"]) / max(s["it_end_ms"] - s["it_start_ms"], 1)
        out.append(s["us_start_ms"] + f * (s["us_end_ms"] - s["us_start_ms"]))
    return out
a, b = load(sys.argv[1]), load(sys.argv[2])
A, B = src_at(a), src_at(b); n = min(len(A), len(B))
bins = {"<1s": 0, "1-3s": 0, ">3s": 0, "no clip": 0}
for x, y in zip(A[:n], B[:n]):
    if x is None or y is None: bins["no clip"] += 1
    elif abs(x - y) < 1000: bins["<1s"] += 1
    elif abs(x - y) < 3000: bins["1-3s"] += 1
    else: bins[">3s"] += 1
print("total ms", a["total_ms"], b["total_ms"], "| segments", len(a["segments"]), len(b["segments"]), "| groups", len(a["groups"]), len(b["groups"]))
print({k: f"{100*v/n:.1f}%" for k, v in bins.items()})
def switches(m):
    segs = sorted([s for s in m["segments"] if s.get("kind") == "clip"], key=lambda s: s["it_start_ms"])
    return sum(1 for p, q in zip(segs, segs[1:]) if abs(q["us_start_ms"] - p["us_end_ms"]) > 200)
def slow(m): return sum(1 for s in m["segments"] if s.get("kind") == "clip" and (s.get("stretch_factor") or 1) > 1.25)
print("clip switches", switches(a), switches(b), "| slow segments (>1.25x)", slow(a), slow(b))
print("kinds", sorted({s.get("kind") for s in a["segments"]}), sorted({s.get("kind") for s in b["segments"]}))
