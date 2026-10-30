#!/usr/bin/env python3
"""compare_lips.py <lips_nets A> <lips_nets B> -- proof that two lip scans give the same result (Step 4B.6):
every piece in B (range_<s>_<e>.json) against the same piece in A: tracks, face boxes, per-frame talking/visual scores and
the talking intervals. OK = same tracks, boxes within 2 px, every talking-interval edge within one frame (0.04 s) and the
scores within 0.5. Score differences of 0.1-0.35 are normal between an Apple GPU (MPS) scan and any CPU scan (plain PyTorch
too, TGS ep32) and change nothing; ONNX vs PyTorch on the same device differ by about 1e-5."""
import json, os, sys
import numpy as np


def main():
    A, B = sys.argv[1], sys.argv[2]; bad = n = 0
    for f in sorted(x for x in os.listdir(B) if x.startswith("range_") and x.endswith(".json")):
        if not os.path.exists(os.path.join(A, f)): continue
        a, b = json.load(open(os.path.join(A, f))), json.load(open(os.path.join(B, f))); n += 1
        ds = np.abs(np.array(a["scores"], float) - np.array(b["scores"], float)) if a["scores"] else np.zeros(1)
        dv = np.abs(np.array(a["visual"], float) - np.array(b["visual"], float)) if a["visual"] else np.zeros(1)
        same_tracks = len(a["tracks"]) == len(b["tracks"]) and a["faces_frames"] == b["faces_frames"]
        box = max((max(abs(p - q) for p, q in zip(ta["box"], tb["box"])) for ta, tb in zip(a["tracks"], b["tracks"])), default=0)
        edges = [abs(p - q) for ia, ib in zip(a["intervals"], b["intervals"]) for p, q in zip(ia, ib)]
        same_iv = len(a["intervals"]) == len(b["intervals"]) and max(edges, default=0) <= 0.04
        ok = same_tracks and box <= 2 and ds.max() < 0.5 and dv.max() < 0.5 and same_iv
        bad += not ok
        print(f"{f}: tracks {'same' if same_tracks else 'DIFFERENT'}, box {box:.1f} px, score diff {ds.max():.4f}, visual diff "
              f"{dv.max():.4f}, intervals {'same' if same_iv else 'DIFFERENT'} -> {'OK' if ok else 'CHECK'}")
    print(f"{n} pieces compared: " + ("ALL PIECES MATCH" if not bad else f"{bad} pieces to look at"))
    sys.exit(1 if bad or not n else 0)


if __name__ == "__main__":
    main()
