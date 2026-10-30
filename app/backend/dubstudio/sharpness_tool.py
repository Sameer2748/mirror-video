"""How sharp the source picture is over time, for the editor's "blurry" flag (design: "Review flags").

Run with the pipeline's Python (numpy):  python sharpness_tool.py --video V --out sharpness.json [--fps 2]
Frames are decoded by ffmpeg in grey at a small size (the 540-px preview copy is enough); sharpness = variance of
the Laplacian (high = crisp edges, low = soft or blurred). Writes {"fps": f, "values": [one per sample]}.
Without numpy it still works, on smaller frames (slower, used by the tests' fake pipeline)."""
import argparse, json, subprocess, sys


def lap_var_np(np, buf, w, h):
    a = np.frombuffer(buf, dtype=np.uint8).reshape(h, w).astype(np.float32)
    lap = a[1:-1, :-2] + a[1:-1, 2:] + a[:-2, 1:-1] + a[2:, 1:-1] - 4 * a[1:-1, 1:-1]
    return float(lap.var())


def lap_var_py(buf, w, h):
    vals = []
    for y in range(1, h - 1):
        r0, r1, r2 = y * w - w, y * w, y * w + w
        for x in range(1, w - 1):
            vals.append(buf[r1 + x - 1] + buf[r1 + x + 1] + buf[r0 + x] + buf[r2 + x] - 4 * buf[r1 + x])
    m = sum(vals) / len(vals)
    return sum((v - m) ** 2 for v in vals) / len(vals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=2.0)
    a = ap.parse_args()
    try:
        import numpy as np
    except ImportError:
        np = None
    w = 240 if np is not None else 64
    h = w                                              # square sample: the measure is relative to the episode
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", a.video, "-vf", f"fps={a.fps},scale={w}:{h},format=gray",
                          "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    vals, n = [], w * h
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        vals.append(round(lap_var_np(np, buf, w, h) if np is not None else lap_var_py(buf, w, h), 2))
        if len(vals) % 200 == 0:
            print(f"{len(vals)} frames", flush=True)
    p.wait()
    if not vals:
        sys.exit("no frames decoded")
    json.dump({"fps": a.fps, "values": vals}, open(a.out, "w"))
    print(f"{len(vals)} frames measured -> {a.out}")


if __name__ == "__main__":
    main()
