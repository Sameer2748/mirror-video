#!/usr/bin/env python3
"""
lipmesh.py -- the third lip measure of `lipdetect.py --range` alone: MediaPipe Face Mesh lip opening/closing
(mouth, aperture). Resumable. Light: about 0.15 s per second of video.
Used with lipgpu.py (S3FD + TalkNet on the GPU); lipmerge.py joins the two halves.

An empty ffmpeg slice (fast seek lands in a hole) is retried with accurate seek, then written as
closed-mouth zeros so one bad 15 s window does not kill the episode.

  python3 lipmesh.py --video SRC.mp4 --out-dir W/lips_mesh [--ranges W/lips_nets/ranges.json | --piece 15] [--max-seconds 150]
"""
import argparse, json, os, subprocess, sys, time
import numpy as np
np.int = int
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lipfast as LF

FPS = 25


def decode_piece(video, s, e, size):
    """25 fps square frames, same filter as lipfast.run_range. Accurate seek if fast seek is empty."""
    dur = max(float(e) - float(s), 0.04)
    vf = (f"fps={FPS},scale={size}:{size}:force_original_aspect_ratio=decrease,"
          f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2")
    frame_n = size * size * 3

    def run(accurate):
        cmd = ["ffmpeg", "-v", "error", "-hide_banner", "-threads", "0"]
        if accurate:
            cmd += ["-i", video, "-ss", f"{s:.3f}", "-t", f"{dur:.3f}"]
        else:
            cmd += ["-ss", f"{s:.3f}", "-t", f"{dur:.3f}", "-i", video]
        cmd += ["-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        p = subprocess.run(cmd, capture_output=True)
        raw = p.stdout or b""
        usable = (len(raw) // frame_n) * frame_n
        fr = (np.frombuffer(raw[:usable], np.uint8).reshape(-1, size, size, 3)
              if usable else np.zeros((0, size, size, 3), np.uint8))
        err = (p.stderr or b"").decode("utf-8", "replace")[-400:]
        return fr, p.returncode, err

    fr, rc, err = run(False)
    if len(fr) == 0:
        print(f"mesh {s:g}-{e:g}: fast seek empty (rc={rc}); trying accurate seek", flush=True)
        if err.strip():
            print(err.strip(), flush=True)
        fr, rc, err = run(True)
    want = max(1, int(round((float(e) - float(s)) * FPS)))
    if len(fr) > want:
        fr = fr[:want]
    return fr, rc, err


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
        t = time.time()
        fr, rc, err = decode_piece(a.video, s, e, a.size)
        if len(fr) == 0:
            want = max(1, int(round((float(e) - float(s)) * FPS)))
            print(f"mesh {s:g}-{e:g}: no frames after both seeks (rc={rc}); "
                  f"writing {want} closed-mouth frames", flush=True)
            if err.strip():
                print(err.strip(), flush=True)
            json.dump(dict(window=[s, e], frames=want, mouth=[0.0] * want, aperture=[0.0] * want),
                      open(fp + ".part", "w")); os.replace(fp + ".part", fp)
            continue
        mo, ap, _ = LF.mesh_pass(fr)
        json.dump(dict(window=[s, e], frames=len(fr), mouth=[round(float(v), 4) for v in mo],
                       aperture=[round(float(v), 4) for v in ap]),
                  open(fp + ".part", "w")); os.replace(fp + ".part", fp)
        print(f"mesh {s:g}-{e:g} done ({time.time() - t:.0f}s, {len(fr)} frames)", flush=True)
    print("ALL DONE" if not left else f"{left} pieces left -- run again (resumes)")
    sys.exit(0 if not left else 3)


if __name__ == "__main__":
    main()
