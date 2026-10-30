#!/usr/bin/env python3
"""
lipmerge.py -- join the two halves of the lip scan into the normal lips_range files that `dubstitch.py lipsync` reads:
  NETS_DIR/range_<s>_<e>.json  (S3FD + TalkNet, from lipgpu.py)
  MESH_DIR/mesh_<s>_<e>.json   (Face Mesh, from lipmesh.py)
-> OUT_DIR/range_<s>_<e>.json  (identical format to lipdetect.py --range)

  python3 lipmerge.py --nets W/lips_nets --mesh W/lips_mesh --out W/lips_range [--ranges W/lips_nets/ranges.json]
"""
import argparse, json, os, sys
p = argparse.ArgumentParser(); p.add_argument("--nets", required=True); p.add_argument("--mesh", required=True)
p.add_argument("--out", required=True); p.add_argument("--ranges", help="default: NETS_DIR/ranges.json")
a = p.parse_args(); os.makedirs(a.out, exist_ok=True); missing = []
ranges = json.load(open(a.ranges or os.path.join(a.nets, "ranges.json")))
for s, e in ranges:
    nf = os.path.join(a.nets, f"range_{s:g}_{e:g}.json"); mf = os.path.join(a.mesh, f"mesh_{s:g}_{e:g}.json")
    if not (os.path.exists(nf) and os.path.exists(mf)): missing.append(f"{s:g}-{e:g}"); continue
    d = json.load(open(nf)); m = json.load(open(mf))
    if m["frames"] != d["frames"]: sys.exit(f"frame count differs in {s:g}-{e:g}: nets {d['frames']} mesh {m['frames']}")
    d["mouth"], d["aperture"] = m["mouth"], m["aperture"]; d.setdefault("method", {})["mesh"] = "lipmesh.py"
    json.dump(d, open(os.path.join(a.out, f"range_{s:g}_{e:g}.json"), "w"))
if missing: sys.exit(f"not merged (a half is missing -- run lipgpu.py / lipmesh.py again): {missing}")
print(f"merged {len(ranges)} pieces into {a.out}")
