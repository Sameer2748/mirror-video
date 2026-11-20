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


FPS = 25
SILENT = -9.0  # same as dubstitch merged_lip_intervals: no lips on this frame


def _count(d, keys):
    n = int(d.get("frames") or 0)
    return min(n or 10**9, *(_nlist(d.get(k), n) for k in keys))


def _window_frames(d, m, label=""):
    for src in (d, m):
        w = src.get("window")
        if isinstance(w, (list, tuple)) and len(w) >= 2:
            return max(1, int(round((float(w[1]) - float(w[0])) * FPS)))
    if label and "-" in label:
        try:
            a, b = label.split("-", 1)
            return max(1, int(round((float(b) - float(a)) * FPS)))
        except ValueError:
            pass
    return 1


def merge_pair(d, m, label=""):
    """Copy mouth/aperture onto the nets file. Align both halves to the shorter count.

    A 1–2 frame gap is 29.97→25 fps rounding. A bigger gap is the last piece of the video:
    one half stops at EOF (e.g. 258 frames) and the other wrote a full 15 s window (376).
    Taking the shorter side keeps real frames and does not invent mouth data.

    A half with 0 frames is the slice past EOF (TAB EP26-30 390-405: nets 0, mesh 375 closed-mouth).
    Fill that half silent / closed-mouth so merge does not kill the episode. -> (out, note|None)."""
    n = _count(d, ("scores", "visual"))
    nm = _count(m, ("mouth", "aperture"))
    if n >= 10**9:
        n = 0
    if nm >= 10**9:
        nm = 0
    note = None
    if n < 1 and nm < 1:
        keep = _window_frames(d, m, label)
        d, m = dict(d), dict(m)
        d["frames"] = keep
        d["scores"] = [SILENT] * keep
        d["visual"] = [SILENT] * keep
        m["mouth"] = [0.0] * keep
        m["aperture"] = [0.0] * keep
        note = f"both halves empty; wrote {keep} silent frames"
        n = nm = keep
    elif n < 1:
        d = dict(d)
        d["frames"] = nm
        d["scores"] = [SILENT] * nm
        d["visual"] = [SILENT] * nm
        if "window" not in d and m.get("window"):
            d["window"] = m["window"]
        note = f"empty nets filled with {nm} silent frames (mesh {nm})"
        n = nm
    elif nm < 1:
        m = dict(m)
        m["mouth"] = [0.0] * n
        m["aperture"] = [0.0] * n
        note = f"empty mesh filled with {n} closed-mouth frames (nets {n})"
        nm = n
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


def list_range_files(nets):
    """Every range_S_E.json on disk, in time order. The cloud ranges.json can omit windows
    that were actually scanned (EP70/71: only 4 of ~36 pieces listed)."""
    pieces = []
    if not os.path.isdir(nets):
        return pieces
    for fn in os.listdir(nets):
        if not (fn.startswith("range_") and fn.endswith(".json")):
            continue
        try:
            a, b = fn[6:-5].split("_")
            pieces.append([float(a), float(b)])
        except ValueError:
            continue
    pieces.sort()
    return pieces


def rebuild_ranges(nets, duration=None):
    """Write ranges.json from the files that exist (optionally drop windows at/after video end)."""
    pieces = list_range_files(nets)
    if duration is not None:
        pieces = [[s, e] for s, e in pieces if s < float(duration)]
    os.makedirs(nets, exist_ok=True)
    json.dump(pieces, open(os.path.join(nets, "ranges.json"), "w"))
    return pieces


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--nets", required=True)
    p.add_argument("--mesh", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--ranges", help="default: NETS_DIR/ranges.json")
    p.add_argument("--duration", type=float, help="drop windows that start at or after this (video length, s)")
    a = p.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    missing = []
    listed = []
    rp = a.ranges or os.path.join(a.nets, "ranges.json")
    if os.path.isfile(rp):
        listed = json.load(open(rp))
    disk = rebuild_ranges(a.nets, a.duration)
    if len(disk) > len(listed):
        print(f"ranges.json had {len(listed)} windows; using {len(disk)} files on disk", flush=True)
    ranges = disk or listed
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
