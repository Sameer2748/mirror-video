#!/usr/bin/env python3
"""
Join the two halves of the lip scan. Lives in the API image so merge does not depend on the rented
worker's older script (that one still dies on a 1-frame 29.97→25 fps rounding gap).
"""
import argparse, json, os, sys


def _cut(seq, n):
    return seq[:n] if isinstance(seq, list) else seq


def _nlist(seq, fallback):
    return len(seq) if isinstance(seq, list) and seq else fallback


def merge_pair(d, m, label=""):
    """Copy mouth/aperture onto the nets file. Align both halves to the shorter count.

    A 1–2 frame gap is 29.97→25 fps rounding. A bigger gap is the last piece of the video:
    one half stops at EOF (e.g. 258 frames) and the other wrote a full 15 s window (376).
    Taking the shorter side keeps real frames and does not invent mouth data. -> (out, note|None)."""
    n, nm = int(d.get("frames") or 0), int(m.get("frames") or 0)
    n = min(n or 10**9, _nlist(d.get("scores"), n), _nlist(d.get("visual"), n))
    nm = min(nm or 10**9, _nlist(m.get("mouth"), nm), _nlist(m.get("aperture"), nm))
    if n < 1 or nm < 1:
        raise ValueError(f"empty lip scan in {label}: nets {n} mesh {nm}")
    note = None
    keep = min(n, nm)
    if n != nm:
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
