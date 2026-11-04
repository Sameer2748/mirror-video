#!/usr/bin/env python3
"""
lipfast.py -- faster drop-in for `lipdetect.py --range` (same measures, same output file format).

Same three measures as lipdetect.py: TalkNet-ASD audio-visual score, TalkNet picture-only score, MediaPipe Face Mesh
lip-opening change. What changes is only how the work is done:
  * the video is decoded ONCE (lipdetect decodes it twice: once for TalkNet, once for Face Mesh);
  * face detection options (--det):
      s3fd  : the same S3FD detector, run on batches of frames (--batch) instead of one frame at a time;
      yunet : OpenCV YuNet face detector (tiny, fast) instead of S3FD;
      mesh  : no separate detector -- face boxes come from the Face Mesh landmarks that are computed anyway;
  * --det-every N : detect on every Nth frame (tracking interpolates the rest, as lipdetect already does with 2).

  python3 lipfast.py --work W --video V --talknet TN --range START,END [--det s3fd|yunet|mesh] [--det-every 2]
         [--out-dir DIR]   # default W/lips_range
"""
import argparse, json, os, subprocess, sys, time
import numpy as np
np.int = int
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lipdetect as L

FPS = 25

def s3fd_batch(det, frames, idx, conf=0.9, scale=0.5, batch=8):
    import torch, cv2
    from model.faceDetector.s3fd.box_utils import nms_
    out = {}
    for b0 in range(0, len(idx), batch):
        ks = idx[b0:b0 + batch]
        h, w = frames[ks[0]].shape[:2]
        xs = []
        for k in ks:
            x = cv2.resize(frames[k], (0, 0), fx=scale, fy=scale).astype("float32").transpose(2, 0, 1)
            xs.append(x[[2, 1, 0], :, :] - np.array([104., 117., 123.], "float32")[:, None, None])
        with torch.no_grad():
            y = det(torch.from_numpy(np.stack(xs)))
        d = y.data; sc = torch.Tensor([w, h, w, h])
        for bi, k in enumerate(ks):
            res = []
            for i in range(d.size(1)):
                j = 0
                while j < d.size(2) and d[bi, i, j, 0] > conf:
                    res.append(list((d[bi, i, j, 1:] * sc).numpy()) + [float(d[bi, i, j, 0])]); j += 1
            if res:
                bb = np.array(res); keep = nms_(bb, 0.1); res = bb[keep].tolist()
            out[k] = res
    return out

_yunet = None
def yunet_dets(frames, idx, model_path, conf=0.7, scale=0.5):
    import cv2
    global _yunet
    h, w = frames[0].shape[:2]; sw, sh = int(w * scale), int(h * scale)
    if _yunet is None:
        _yunet = cv2.FaceDetectorYN.create(model_path, "", (sw, sh), conf, 0.3, 50)
    out = {}
    for k in idx:
        img = cv2.cvtColor(cv2.resize(frames[k], (sw, sh)), cv2.COLOR_RGB2BGR)
        _, f = _yunet.detect(img)
        res = []
        for r in (f if f is not None else []):
            x, y, bw, bh = r[:4] / scale
            res.append([x, y, x + bw, y + bh, float(r[-1])])
        out[k] = res
    return out

def mesh_pass(frames, win=5):
    """Face Mesh exactly as lipdetect.mouth_series, but also returns per-frame face boxes from the landmarks."""
    import mediapipe as mp
    fm = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=3, min_detection_confidence=0.5)
    n = len(frames); faces = []; boxes = {}
    H, W = frames[0].shape[:2]
    for k, f in enumerate(frames):
        r = fm.process(np.ascontiguousarray(f)); boxes[k] = []
        for lm in (r.multi_face_landmarks or []):
            P = lambda i: np.array([lm.landmark[i].x, lm.landmark[i].y])
            xs = np.array([p.x for p in lm.landmark]); ys = np.array([p.y for p in lm.landmark])
            boxes[k].append([xs.min() * W, ys.min() * H, xs.max() * W, ys.max() * H, 1.0])
            h = np.linalg.norm(P(10) - P(152))
            if h < 0.08: continue
            ap = np.linalg.norm(P(13) - P(14)) / h; pos = P(1)
            near = [q for q in faces if k - max(q["ap"]) <= 3 and np.linalg.norm(q["pos"] - pos) < 0.12]
            q = min(near, key=lambda q: np.linalg.norm(q["pos"] - pos)) if near else None
            if q is None or k in q["ap"]: q = dict(pos=pos, ap={}, h=h); faces.append(q)
            q["ap"][k] = ap; q["pos"] = pos
    fm.close()
    motion = np.zeros(n); aper = np.zeros(n)
    for q in faces:
        ks = sorted(q["ap"])
        if len(ks) < 2 * win: continue
        a = np.array([q["ap"][k] for k in ks])
        for i, k in enumerate(ks):
            m = float(a[max(i - win, 0):i + win + 1].std())
            if m > motion[k]: motion[k] = m; aper[k] = a[i]
    return motion, aper, boxes

def run_range(t0, t1, video, nets, det_kind, every, batch, size, yunet_path, yconf=0.85, key_every=6, thr=0.0, merge=0.3, min_len=0.3, timing=None):
    import cv2
    model, head, det = nets
    T = timing if timing is not None else {}
    t = time.time(); dur = t1 - t0
    raw = subprocess.run(["ffmpeg", "-v", "error", "-threads", "0", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", video, "-vf",
                          f"fps={FPS},scale={size}:{size}:force_original_aspect_ratio=decrease,pad={size}:{size}:(ow-iw)/2:(oh-ih)/2",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, size, size, 3)
    want = max(1, int(round(dur * FPS)))
    if len(frames) > want:
        frames = frames[:want]
    au = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
                         "-f", "s16le", "-"], capture_output=True).stdout
    audio = np.frombuffer(au, np.int16); n = len(frames)
    T["decode"] = T.get("decode", 0) + time.time() - t
    t = time.time(); mo, ap, mboxes = mesh_pass(frames); T["facemesh"] = T.get("facemesh", 0) + time.time() - t
    t = time.time(); idx = list(range(0, n, every))
    if det_kind == "s3fd": dd = s3fd_batch(det, frames, idx, batch=batch)
    elif det_kind == "yunet": dd = yunet_dets(frames, idx, yunet_path, conf=yconf)
    elif det_kind == "file":
        # S3FD boxes computed elsewhere (mac_fullscan.py --dets-only on the Mac GPU): dets_<t0>_<t1>.json
        D = json.load(open(os.path.join(yunet_path, f"dets_{t0:g}_{t1:g}.json")))
        if D["frames"] != n or D.get("every", 2) != every: raise SystemExit(f"dets file {t0:g}-{t1:g}: {D['frames']} frames / every {D.get('every')} vs {n} / {every}")
        dd = {int(k): v for k, v in D["dets"].items()}
    elif det_kind == "scrfd":
        import detlib; dd = detlib.scrfd_dets(frames, idx, yunet_path, conf=yconf, scale=0.5)
    elif det_kind == "hybrid":
        # S3FD (the original detector) on every key-th frame decides WHICH faces exist; YuNet (high recall at 0.5)
        # gives the boxes on the frames in between, kept only when S3FD saw that face on the key frame before or after.
        keys = list(range(0, n, key_every)); S = s3fd_batch(det, frames, keys, batch=1)
        rest = [k for k in idx if k not in S]; Y = yunet_dets(frames, rest, yunet_path, conf=0.5)
        dd = dict(S)
        for k in rest:
            kp = (k // key_every) * key_every; kn = kp + key_every
            ref = S.get(kp, []) + S.get(kn, [])
            dd[k] = [y for y in Y[k] if any(L.iou(y, r) > 0.3 for r in ref)]
        T["s3fd_calls"] = T.get("s3fd_calls", 0) + len(keys)
    else: dd = {k: mboxes[k] for k in idx}
    dets = [dd.get(k, []) if k % every == 0 else [] for k in range(n)]
    T["detect"] = T.get("detect", 0) + time.time() - t
    t = time.time(); tn = 0
    best = np.full(n, -9.0); who = np.full(n, -1); vbest = np.full(n, -9.0); tracks_out = []
    for a, b in L.shots(frames):
        for tr in L.track_faces(dets, a, b, min_len=max(10 // every, 3)):
            faces, sm = L.crop_track(frames, tr)
            f0, f1 = int(tr["frame"][0]), int(tr["frame"][-1]) + 1
            t2 = time.time(); sc, sv = L.talk_scores(model, head, faces, audio[int(f0 / FPS * 16000):int(f1 / FPS * 16000)]); tn += time.time() - t2
            if not len(sc): continue
            smooth = lambda q: np.array([np.mean(q[max(i - 2, 0):min(i + 3, len(q))]) for i in range(len(q))])
            ss, vv = smooth(sc), smooth(sv); ti = len(tracks_out)
            tracks_out.append(dict(first_s=round(t0 + f0 / FPS, 3), last_s=round(t0 + (f0 + len(ss)) / FPS, 3),
                                   box=[round(float(v)) for v in np.median(tr["bbox"][:len(ss)], 0)], size=size,
                                   mean_score=round(float(ss.mean()), 2), mean_visual=round(float(vv.mean()), 2),
                                   talking_s=round(float((ss >= thr).sum()) / FPS, 2), mouth_s=round(float((vv >= 0).sum()) / FPS, 2),
                                   # per face, frame by frame (owner 2026-10-29, face map): WHICH face talks, not only that one does
                                   f0=round(t0 + f0 / FPS, 3), score=[round(float(v), 2) for v in ss], visual=[round(float(v), 2) for v in vv],
                                   boxes=[[int(round(float(v))) for v in b] for b in tr["bbox"][:len(ss)]]))
            for i in range(len(ss)):
                if ss[i] > best[f0 + i]: best[f0 + i] = ss[i]
                if vv[i] > vbest[f0 + i]: vbest[f0 + i] = vv[i]; who[f0 + i] = ti
    T["talknet"] = T.get("talknet", 0) + tn; T["track"] = T.get("track", 0) + time.time() - t - tn
    res = dict(idx=-1, cls="range", text=f"{t0}-{t1}s", start_s=t0, end_s=t1, window=[round(t0, 3), round(t1, 3)], frames=n,
               faces_frames=int(sum(1 for d in dets if d)) * every, tracks=tracks_out,
               scores=[round(float(v), 2) for v in best], visual=[round(float(v), 2) for v in vbest],
               intervals=[], intervals_visual=[], method=dict(det=det_kind, every=every, batch=batch, size=size, model=os.path.basename(yunet_path) if det_kind in ('yunet', 'scrfd') else None, conf=yconf if det_kind in ('yunet', 'scrfd') else None, key_every=key_every if det_kind == 'hybrid' else None),
               mouth=[round(float(v), 4) for v in mo], aperture=[round(float(v), 4) for v in ap])
    return res

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work", required=True); p.add_argument("--talknet", required=True); p.add_argument("--video", required=True)
    p.add_argument("--range", required=True, help="START,END seconds; several pieces: START,END;START,END")
    p.add_argument("--det", choices=["s3fd", "yunet", "mesh", "hybrid", "scrfd", "file"], default="s3fd")
    p.add_argument("--key-every", type=int, default=6, help="hybrid: run S3FD on every Nth frame")
    p.add_argument("--det-every", type=int, default=2); p.add_argument("--batch", type=int, default=8)
    p.add_argument("--size", type=int, default=540); p.add_argument("--threads", type=int, default=0)
    p.add_argument("--yunet", default=None, help="model path (yunet/scrfd) or, for --det file, the folder with dets_<s>_<e>.json")
    p.add_argument("--yunet-conf", type=float, default=0.85, help="YuNet score threshold (0.85 matches S3FD at 0.9 best)")
    p.add_argument("--out-dir"); p.add_argument("--max-seconds", type=float, default=1e9, help="do not start a new piece after this long")
    a = p.parse_args()
    import torch
    out_dir = a.out_dir or os.path.join(a.work, "lips_range"); os.makedirs(out_dir, exist_ok=True)
    tl = time.time(); cwd = os.getcwd(); os.chdir(a.talknet); nets = L.load_talknet(a.talknet); os.chdir(cwd)
    if a.threads: torch.set_num_threads(a.threads)
    T = {"load": time.time() - tl}
    for piece in a.range.split(";"):
        if time.time() - tl > a.max_seconds: print("stopped (max-seconds); run again", flush=True); break
        t0, t1 = (float(x) for x in piece.split(","))
        ts = time.time()
        res = run_range(t0, t1, a.video, nets, a.det, a.det_every, a.batch, a.size, a.yunet, a.yunet_conf, a.key_every, timing=T)
        json.dump(res, open(os.path.join(out_dir, f"range_{t0:g}_{t1:g}.json"), "w"))
        print(f"done {t0:g}-{t1:g} ({time.time() - ts:.0f}s)", flush=True)
    print("timing", {k: round(v, 1) for k, v in T.items()})

if __name__ == "__main__":
    main()
