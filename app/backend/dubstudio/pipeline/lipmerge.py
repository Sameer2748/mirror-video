#!/usr/bin/env python3
"""
Join the two halves of the lip scan. Lives in the API image so merge does not depend on the rented
worker's older script (that one still dies on a 1-frame 29.97→25 fps rounding gap).
"""
import argparse, json, os, sys


def _cut(seq, n):
    return seq[:n] if isinstance(seq, list) else seq


def merge_pair(d, m, label=""):
    """Copy mouth/aperture onto the nets file. Trim a 1–2 frame ffmpeg drift. -> (out, note|None)."""
    n, nm = int(d["frames"]), int(m["frames"])
    note = None
    if n != nm:
        if abs(n - nm) > 2:
            raise ValueError(f"frame count differs in {label}: nets {n} mesh {nm}")
        keep = min(n, nm)
        note = f"trimmed to {keep} frames (nets {n}, mesh {nm})"
        d = dict(d)
        d["frames"] = keep
        for key in ("scores", "visual"):
            if key in d:
                d[key] = _cut(d[key], keep)
        m = dict(m)
        m["mouth"] = _cut(m.get("mouth") or [], keep)
        m["aperture"] = _cut(m.get("aperture") or [], keep)
    d["mouth"], d["aperture"] = m["mouth"], m["aperture"]
    d.setdefault("method", {})["mesh"] = "lipmesh.py"
    return d, note


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--nets", required=True)
    p.add_argument("--mesh", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--ranges", help="default: NETS_DIR/ranges.json")
    a = p.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    missing = []
    ranges = json.load(open(a.ranges or os.path.join(a.nets, "ranges.json")))
    for s, e in ranges:
        label = f"{s:g}-{e:g}"
        nf = os.path.join(a.nets, f"range_{s:g}_{e:g}.json")
        mf = os.path.join(a.mesh, f"mesh_{s:g}_{e:g}.json")
        if not (os.path.exists(nf) and os.path.exists(mf)):
            missing.append(label)
            continue
        out, note = merge_pair(json.load(open(nf)), json.load(open(mf)), label)
        json.dump(out, open(os.path.join(a.out, f"range_{s:g}_{e:g}.json"), "w"))
        if note:
            print(f"note: {label} {note}", flush=True)
    if missing:
        sys.exit(f"not merged (a half is missing -- run lipgpu.py / lipmesh.py again): {missing}")
    print(f"merged {len(ranges)} pieces into {a.out}")


if __name__ == "__main__":
    try:
        main()
    except ValueError as ex:
        sys.exit(str(ex))
