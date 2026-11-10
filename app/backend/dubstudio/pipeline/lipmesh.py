#!/usr/bin/env python3
"""
lipmesh.py -- MediaPipe Face Mesh lip opening/closing, one 15 s piece at a time.

The rented worker image still crashes when ffmpeg returns no frames (`frames[0]` on an empty array).
This copy ships with the cloud job so a Retry does not need a new image.

  python3 lipmesh.py --video SRC.mp4 --out-dir W/lips_mesh [--ranges W/lips_nets/ranges.json | --piece 15]
"""
import argparse, json, os, subprocess, sys, time

FPS = 25


def _np():
    import numpy as np
    np.int = int
    return np


def decode_piece(video, s, e, size):
    """25 fps square frames, same filter as lipfast.run_range.

    Fast `-ss` before `-i` is empty on some slices (a hole, a trailing GOP, a 29.97 seek). Accurate seek
    (`-ss` after `-i`) usually recovers the frames. -> (frames, rc, stderr_tail)."""
    np = _np()
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


def mesh_pass(frames, win=5):
    """Face Mesh mouth motion / aperture. Empty input is closed-mouth zeros, not a crash."""
    np = _np()
    n = len(frames)
    if n == 0:
        return np.zeros(0), np.zeros(0)
    import mediapipe as mp
    fm = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=3, min_detection_confidence=0.5)
    faces = []
    H, W = frames[0].shape[:2]
    for k, f in enumerate(frames):
        r = fm.process(np.ascontiguousarray(f))
        for lm in (r.multi_face_landmarks or []):
            P = lambda i: np.array([lm.landmark[i].x, lm.landmark[i].y])
            h = np.linalg.norm(P(10) - P(152))
            if h < 0.08:
                continue
            ap = np.linalg.norm(P(13) - P(14)) / h
            pos = P(1)
            near = [q for q in faces if k - max(q["ap"]) <= 3 and np.linalg.norm(q["pos"] - pos) < 0.12]
            q = min(near, key=lambda q: np.linalg.norm(q["pos"] - pos)) if near else None
            if q is None or k in q["ap"]:
                q = dict(pos=pos, ap={}, h=h)
                faces.append(q)
            q["ap"][k] = ap
            q["pos"] = pos
    fm.close()
    motion = np.zeros(n)
    aper = np.zeros(n)
    for q in faces:
        ks = sorted(q["ap"])
        if len(ks) < 2 * win:
            continue
        a = np.array([q["ap"][k] for k in ks])
        for i, k in enumerate(ks):
            m = float(a[max(i - win, 0):i + win + 1].std())
            if m > motion[k]:
                motion[k] = m
                aper[k] = a[i]
    return motion, aper


def write_mesh(path, s, e, frames, mouth, aperture):
    json.dump(dict(window=[s, e], frames=int(len(frames)),
                   mouth=[round(float(v), 4) for v in mouth],
                   aperture=[round(float(v), 4) for v in aperture]),
              open(path + ".part", "w"))
    os.replace(path + ".part", path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--ranges", help="the same ranges file lipgpu.py used; default: --piece pieces over the video")
    p.add_argument("--piece", type=float, default=15)
    p.add_argument("--max-seconds", type=float, default=1e9)
    p.add_argument("--size", type=int, default=540)
    a = p.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    t_start = time.time()
    left = 0
    if a.ranges:
        ranges = json.load(open(a.ranges))
    else:
        import lipgpu
        L = lipgpu.video_length(a.video)
        ranges, s = [], 0
        while s < L:
            ranges.append([s, s + a.piece])
            s += a.piece
    try:
        os.nice(5)
    except Exception:
        pass
    for s, e in ranges:
        fp = os.path.join(a.out_dir, f"mesh_{s:g}_{e:g}.json")
        if os.path.exists(fp):
            continue
        if time.time() - t_start > a.max_seconds:
            left += 1
            continue
        t = time.time()
        fr, rc, err = decode_piece(a.video, s, e, a.size)
        if len(fr) == 0:
            want = max(1, int(round((float(e) - float(s)) * FPS)))
            print(f"mesh {s:g}-{e:g}: no frames after both seeks (rc={rc}); "
                  f"writing {want} closed-mouth frames", flush=True)
            if err.strip():
                print(err.strip(), flush=True)
            write_mesh(fp, s, e, [0] * want, [0.0] * want, [0.0] * want)
            continue
        mo, ap = mesh_pass(fr)
        write_mesh(fp, s, e, fr, mo, ap)
        print(f"mesh {s:g}-{e:g} done ({time.time() - t:.0f}s, {len(fr)} frames)", flush=True)
    print("ALL DONE" if not left else f"{left} pieces left -- run again (resumes)")
    sys.exit(0 if not left else 3)


if __name__ == "__main__":
    main()
