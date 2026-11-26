"""Apply the scene-edit JSON as the edit list (same as Runner.use_scene). Runs on the rented CPU
after slowfix so fast_trim sees the scene cut on that machine."""
import json, os, shutil, sys
from pathlib import Path

def apply(W):
    W = str(W)
    grouping = f"{W}/manifest_groups.grouping.json"
    auto = f"{W}/manifest_groups.auto.json"
    if not os.path.exists(auto):
        src = grouping if os.path.exists(grouping) else f"{W}/manifest_groups.json"
        shutil.copy(src, auto)
    slow = f"{W}/scene_slowfix/manifest_slowfix.json"
    scene = f"{W}/scene/scene_manifest.json"
    src_scene = slow if os.path.exists(slow) else scene
    a = json.load(open(f"{W}/manifest_groups.auto.json"))
    s = json.load(open(src_scene))
    try:
        from dubstudio import timeline as tlm
        s["segments"] = tlm.fill_scene_from_matching(s.get("segments") or [], a.get("segments") or [],
                                                     a["total_ms"])
    except Exception:
        pass
    s["total_ms"] = a["total_ms"]
    if not s["segments"]:
        s["segments"] = list(a.get("segments") or [])
    L = s["segments"][-1]
    span = L["it_end_ms"] - L["it_start_ms"]
    if span > 0 and abs(L["it_end_ms"] - a["total_ms"]) > 1:
        k = (a["total_ms"] - L["it_start_ms"]) / span
        L["us_end_ms"] = L["us_start_ms"] + (L["us_end_ms"] - L["us_start_ms"]) * k
        L["it_end_ms"] = a["total_ms"]
    json.dump(s, open(f"{W}/manifest_groups.json", "w"), indent=1, ensure_ascii=False)
    return len(s["segments"])


if __name__ == "__main__":
    n = apply(sys.argv[1] if len(sys.argv) > 1 else "/work")
    print(f"the scene edit is the edit list: {n} pieces")
