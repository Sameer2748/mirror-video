#!/usr/bin/env python3
"""
lipmesh.py -- the third lip measure of `lipdetect.py --range` alone: MediaPipe Face Mesh lip opening/closing
(mouth, aperture), decoded exactly as lipdetect.py decodes it. Resumable. Light: about 0.15 s per second of video.
Used with lipgpu.py (S3FD + TalkNet on the GPU); lipmerge.py joins the two halves.
Run it on LINUX when you can (a Linux machine, VM, WSL or Docker): MediaPipe's macOS build measures lip opening
slightly differently (ep24 test: 2 of 174 sentences flipped when Face Mesh ran on the Mac; 0 when it ran on Linux).
It can run at the same time as lipgpu.py.

  python3 lipmesh.py --video SRC.mp4 --out-dir W/lips_mesh [--ranges W/lips_nets/ranges.json | --piece 15] [--max-seconds 150]
"""
import argparse, json, os, subprocess, sys, time
import numpy as np
np.int = int
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lipfast as LF


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", required=True); p.add_argument("--out-dir", required=True)
    p.add_argument("--ranges", help="the same ranges file lipgpu.py used (OUT/ranges.json); default: --piece pieces over the video")
    p.add_argument("--piece", type=float, default=15)
    p.add_argument("--max-seconds", type=float, default=1e9); p.add_argument("--size", type=int, default=540)
    a = p.parse_args(); os.makedirs(a.out_dir, exist_ok=True); t_start = time.time(); left = 0
    if a.ranges: ranges = json.load(open(a.ranges))
    else:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import lipgpu
        L = lipgpu.video_length(a.video); ranges = []; s = 0
        while s < L: ranges.append([s, s + a.piece]); s += a.piece
    try: os.nice(5)
    except Exception: pass
    for s, e in ranges:
        fp = os.path.join(a.out_dir, f"mesh_{s:g}_{e:g}.json")
        if os.path.exists(fp): continue
        if time.time() - t_start > a.max_seconds: left += 1; continue
        t = time.time(); size = a.size
        # same decode as lipdetect.py --range's mouth pass (fps 25, scaled to size x size)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", a.video, "-vf",
                              f"fps=25,scale={size}:{size}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        fr = np.frombuffer(raw, np.uint8).reshape(-1, size, size, 3)
        mo, ap, _ = LF.mesh_pass(fr)
        json.dump(dict(window=[s, e], frames=len(fr), mouth=[round(float(v), 4) for v in mo], aperture=[round(float(v), 4) for v in ap]),
                  open(fp + ".part", "w")); os.replace(fp + ".part", fp)
        print(f"mesh {s:g}-{e:g} done ({time.time() - t:.0f}s)", flush=True)
    print("ALL DONE" if not left else f"{left} pieces left -- run again (resumes)")
    sys.exit(0 if not left else 3)


if __name__ == "__main__":
    main()
