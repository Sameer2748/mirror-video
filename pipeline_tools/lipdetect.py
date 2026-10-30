#!/usr/bin/env python3
"""
lipdetect.py -- find WHERE a character's lips move on screen, for every spoken source line.

Uses TalkNet-ASD (open source, MIT; Tao et al. 2021) on CPU: S3FD face detection -> face tracks per shot ->
TalkNet audio-visual score per face per frame (> 0 = this face is speaking the sound we hear).
Runs only on the source lines that `dubstitch.py speech` marked 'speech' or 'mixed' (+ a small margin),
resumable line by line, so it fits machines that cap each command's run time.

  python3 lipdetect.py --work W --talknet /path/to/TalkNet-ASD [--max-seconds 150]

Writes W/lips/<SRC#>.json per line (+ a small .jpg of the talking face) and, when all are done, W/lips.json.
"""
import argparse, json, math, os, subprocess, sys, time
import numpy as np
np.int = int            # TalkNet's face detector still uses the removed numpy alias

FPS = 25                                   # TalkNet works on 25 fps video and 100 MFCC frames per second

def load_talknet(tn):
    sys.path.insert(0, tn)
    import torch
    torch.set_num_threads(max(os.cpu_count() or 1, 1))
    from model.talkNetModel import talkNetModel
    from loss import lossAV, lossV
    from model.faceDetector.s3fd.nets import S3FDNet
    model, head, vhead = talkNetModel(), lossAV(), lossV()
    st = torch.load(os.path.join(tn, "pretrain_TalkSet.model"), map_location="cpu")
    ms, hs, vs = model.state_dict(), head.state_dict(), vhead.state_dict()
    for k, v in st.items():
        k = k.replace("module.", "")
        if k.startswith("model.") and k[6:] in ms: ms[k[6:]].copy_(v)
        elif k.startswith("lossAV.") and k[7:] in hs: hs[k[7:]].copy_(v)
        elif k.startswith("lossV.") and k[6:] in vs: vs[k[6:]].copy_(v)
    model.eval(); head.eval(); vhead.eval(); head.vhead = vhead
    det = S3FDNet(device="cpu"); det.load_state_dict(torch.load(os.path.join(tn, "model/faceDetector/s3fd/sfd_face.pth"), map_location="cpu"))
    det.eval()
    return model, head, det

def detect_faces(det, img, conf=0.9, scale=0.5):
    """S3FD on one RGB frame (as in the TalkNet demo, run at a reduced scale). -> [[x0,y0,x1,y1,score]]"""
    import torch, cv2
    from model.faceDetector.s3fd.box_utils import nms_
    h, w = img.shape[:2]
    x = cv2.resize(img, (0, 0), fx=scale, fy=scale).astype("float32").transpose(2, 0, 1)
    x = x[[2, 1, 0], :, :] - np.array([104., 117., 123.], "float32")[:, None, None]
    with torch.no_grad():
        y = det(torch.from_numpy(x)[None])
    d = y.data; sc = torch.Tensor([w, h, w, h]); out = []
    for i in range(d.size(1)):
        j = 0
        while j < d.size(2) and d[0, i, j, 0] > conf:
            out.append(list((d[0, i, j, 1:] * sc).numpy()) + [float(d[0, i, j, 0])]); j += 1
    if not out: return []
    b = np.array(out); keep = nms_(b, 0.1)
    return b[keep].tolist()

def iou(a, b):
    xa, ya, xb, yb = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, xb - xa) * max(0, yb - ya)
    return inter / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter, 1e-6)

def shots(frames, thr=28.0):
    """Hard cuts: big change of a 32x32 grey thumbnail between consecutive frames."""
    import cv2
    th = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_RGB2GRAY), (32, 32)).astype(float) for f in frames]
    cuts = [0] + [k for k in range(1, len(th)) if np.abs(th[k] - th[k - 1]).mean() > thr] + [len(frames)]
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b > a]

def track_faces(dets, a, b, max_miss=10, min_len=10):
    """Link detections inside one shot into face tracks (IoU > 0.5), interpolate missing frames (TalkNet demo logic)."""
    pool = {f: list(dets[f]) for f in range(a, b)}; tracks = []
    while True:
        tr = []
        for f in range(a, b):
            for d in list(pool[f]):
                if not tr or (f - tr[-1][0] <= max_miss and iou(d, tr[-1][1]) > 0.5):
                    if tr and f == tr[-1][0]: continue
                    tr.append((f, d)); pool[f].remove(d); break
            if tr and f - tr[-1][0] > max_miss: break
        if not tr: break
        if len(tr) > min_len:
            fr = np.array([t[0] for t in tr]); bb = np.array([t[1][:4] for t in tr])
            fi = np.arange(fr[0], fr[-1] + 1)
            tracks.append(dict(frame=fi, bbox=np.stack([np.interp(fi, fr, bb[:, k]) for k in range(4)], 1)))
    return tracks

def crop_track(frames, tr, cs=0.40):
    """Face crops exactly like the TalkNet demo: square around the (median-smoothed) box, 224 px, grey, centre 112."""
    import cv2
    from scipy import signal
    s = np.array([max(b[3] - b[1], b[2] - b[0]) / 2 for b in tr["bbox"]]); y = (tr["bbox"][:, 1] + tr["bbox"][:, 3]) / 2
    x = (tr["bbox"][:, 0] + tr["bbox"][:, 2]) / 2
    k = min(13, len(s) - (1 - len(s) % 2))
    if k >= 3: s, x, y = signal.medfilt(s, k), signal.medfilt(x, k), signal.medfilt(y, k)
    out = []
    for i, f in enumerate(tr["frame"]):
        bs = s[i]; bsi = int(bs * (1 + 2 * cs))
        img = np.pad(frames[f], ((bsi, bsi), (bsi, bsi), (0, 0)), "constant", constant_values=110)
        my, mx = y[i] + bsi, x[i] + bsi
        face = img[int(my - bs):int(my + bs * (1 + 2 * cs)), int(mx - bs * (1 + cs)):int(mx + bs * (1 + cs))]
        g = cv2.resize(cv2.cvtColor(cv2.resize(face, (224, 224)), cv2.COLOR_RGB2GRAY), (224, 224))
        out.append(g[56:168, 56:168])
    return np.array(out), dict(s=s, x=x, y=y)

def talk_scores(model, head, faces, audio16k, durations=(1, 2, 4)):
    import torch, python_speech_features
    mf = python_speech_features.mfcc(audio16k, 16000, numcep=13, winlen=0.025, winstep=0.010)
    L = min((mf.shape[0] - mf.shape[0] % 4) / 100, faces.shape[0] / FPS)
    mf = mf[:int(round(L * 100))]; vf = faces[:int(round(L * FPS))]
    allsc, allv = [], []
    with torch.no_grad():
        for du in durations:
            sc, sv = [], []
            for i in range(int(math.ceil(L / du))):
                A = torch.FloatTensor(mf[i * du * 100:(i + 1) * du * 100]).unsqueeze(0)
                V = torch.FloatTensor(vf[i * du * FPS:(i + 1) * du * FPS]).unsqueeze(0)
                if A.shape[1] < 4 or V.shape[1] < 1: continue
                eA = model.forward_audio_frontend(A); eV = model.forward_visual_frontend(V)
                eA, eV = model.forward_cross_attention(eA, eV)
                sc.extend(head.forward(model.forward_audio_visual_backend(eA, eV), labels=None))
                ov = head.vhead.FC(model.forward_visual_backend(eV).squeeze(1))      # picture only: does the mouth move like speech?
                sv.extend((ov[:, 1] - ov[:, 0]).reshape(-1).numpy())
            allsc.append(sc[:len(vf)]); allv.append(sv[:len(vf)])
    n = min(len(s) for s in allsc + allv)
    return np.mean(np.array([s[:n] for s in allsc]), 0), np.mean(np.array([s[:n] for s in allv]), 0)

def mouth_series(frames, fps=FPS, win=5):
    """MediaPipe Face Mesh (Google, open source): how wide each face's lips are open in every frame, relative to the
    face height, and how much that opening changes around each frame (rolling std over +-win frames).
    Moving lips = the opening keeps changing. Works on any face, also when the picture is not lip-synced to the sound."""
    import mediapipe as mp
    fm = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=3, min_detection_confidence=0.5)
    n = len(frames); faces = []                         # faces: [{"pos": (x,y), "ap": {frame: aperture}}]
    for k, f in enumerate(frames):
        r = fm.process(np.ascontiguousarray(f))
        for lm in (r.multi_face_landmarks or []):
            P = lambda i: np.array([lm.landmark[i].x, lm.landmark[i].y])
            h = np.linalg.norm(P(10) - P(152)); 
            if h < 0.08: continue                           # tiny background face
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
            seg = a[max(i - win, 0):i + win + 1]
            m = float(seg.std())
            if m > motion[k]: motion[k] = m; aper[k] = a[i]
    return motion, aper

def run_line(r, video, nets, out_dir, margin, thr, merge, min_len, size, every=2):
    import cv2
    model, head, det = nets
    t0 = max(r["start_ms"] / 1000 - margin, 0); t1 = r["end_ms"] / 1000 + margin; dur = t1 - t0
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", video, "-vf",
                          f"fps={FPS},scale={size}:{size}:force_original_aspect_ratio=decrease,pad={size}:{size}:(ow-iw)/2:(oh-ih)/2",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, size, size, 3)
    au = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
                         "-f", "s16le", "-"], capture_output=True).stdout
    audio = np.frombuffer(au, np.int16)
    n = len(frames); dets = [detect_faces(det, f) if k % every == 0 else [] for k, f in enumerate(frames)]   # tracks interpolate the rest
    best = np.full(n, -9.0); who = np.full(n, -1); vbest = np.full(n, -9.0); tracks_out = []
    for a, b in shots(frames):
        for tr in track_faces(dets, a, b, min_len=max(10 // every, 3)):
            faces, sm = crop_track(frames, tr)
            f0, f1 = int(tr["frame"][0]), int(tr["frame"][-1]) + 1
            sc, sv = talk_scores(model, head, faces, audio[int(f0 / FPS * 16000):int(f1 / FPS * 16000)])
            if not len(sc): continue
            smooth = lambda q: np.array([np.mean(q[max(i - 2, 0):min(i + 3, len(q))]) for i in range(len(q))])   # demo smoothing
            ss, vv = smooth(sc), smooth(sv)
            ti = len(tracks_out)
            tracks_out.append(dict(first_s=round(t0 + f0 / FPS, 3), last_s=round(t0 + (f0 + len(ss)) / FPS, 3),
                                   box=[round(float(v)) for v in np.median(tr["bbox"][:len(ss)], 0)], size=size,
                                   mean_score=round(float(ss.mean()), 2), mean_visual=round(float(vv.mean()), 2),
                                   talking_s=round(float((ss >= thr).sum()) / FPS, 2), mouth_s=round(float((vv >= 0).sum()) / FPS, 2)))
            for i in range(len(ss)):
                if ss[i] > best[f0 + i]: best[f0 + i] = ss[i]
                if vv[i] > vbest[f0 + i]: vbest[f0 + i] = vv[i]; who[f0 + i] = ti
    lo = int(round((r["start_ms"] / 1000 - t0) * FPS)); hi = min(int(round((r["end_ms"] / 1000 - t0) * FPS)), n)
    def spans(on):                                        # talking stretches inside the line itself (not the margin)
        ivs, k = [], lo
        while k < hi:
            if not on[k]: k += 1; continue
            j = k
            while j < hi and on[j]: j += 1
            if ivs and k - ivs[-1][1] <= merge * FPS: ivs[-1][1] = j
            else: ivs.append([k, j])
            k = j
        return [v for v in ivs if v[1] - v[0] >= min_len * FPS]
    def as_json(ivs, sc):
        return [dict(start_s=round(t0 + a / FPS, 3), end_s=round(t0 + b / FPS, 3),
                     face=int(np.bincount(who[a:b][who[a:b] >= 0]).argmax()) if (who[a:b] >= 0).any() else -1,
                     score=round(float(sc[a:b].mean()), 2)) for a, b in ivs]
    iv_av, iv_v = spans(best >= thr), spans(vbest >= 0)
    ivs = iv_v or iv_av
    res = dict(idx=r["idx"], cls=r["cls"], text=r["text"], start_s=r["start_ms"] / 1000, end_s=r["end_ms"] / 1000,
               window=[round(t0, 3), round(t1, 3)], frames=n, faces_frames=int(sum(1 for d in dets[lo:hi] if d)) * every,
               tracks=tracks_out, scores=[round(float(v), 2) for v in best], visual=[round(float(v), 2) for v in vbest],
               intervals=as_json(iv_av, best), intervals_visual=as_json(iv_v, vbest))
    if ivs:                                                           # small picture of the talking face, for the QC sheet
        a, b = max(ivs, key=lambda v: v[1] - v[0]); m = (a + b) // 2; img = frames[m].copy()
        t = tracks_out[who[m]] if who[m] >= 0 else None
        if t:
            x0, y0, x1, y1 = t["box"]; cv2.rectangle(img, (x0, y0), (x1, y1), (0, 255, 0), 4)
        cv2.imwrite(os.path.join(out_dir, f"{r['idx']:04d}.jpg"), cv2.cvtColor(cv2.resize(img, (270, 270)), cv2.COLOR_RGB2BGR))
    return res

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work", required=True); p.add_argument("--talknet", required=True, help="folder of TalkNet-ASD with its weights")
    p.add_argument("--margin", type=float, default=0.3, help="seconds analysed before/after each line")
    p.add_argument("--threshold", type=float, default=0.0, help="TalkNet score at/above which a face counts as talking")
    p.add_argument("--merge", type=float, default=0.3, help="join talking stretches separated by less than this (s)")
    p.add_argument("--min-len", type=float, default=0.3, help="ignore talking stretches shorter than this (s)")
    p.add_argument("--size", type=int, default=540, help="frame size used for detection (px)")
    p.add_argument("--det-every", type=int, default=2, help="detect faces on every Nth frame (tracking fills the rest)")
    p.add_argument("--max-seconds", type=float, default=1e9, help="stop starting new lines after this long (resume by re-running)")
    p.add_argument("--only", help="comma-separated SRC# to (re)do")
    p.add_argument("--video", help="the source video (default: from <work>/speech.json)")
    p.add_argument("--range", help="analyse any stretch of the source video instead of the spoken lines: START,END in seconds")
    p.add_argument("--mouth", action="store_true", help="add the Face Mesh lip-opening measure to lines already done")
    a = p.parse_args()
    sp = os.path.join(a.work, "speech.json")
    S = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
    if a.video: S["src_video"] = a.video
    if "src_video" not in S: raise SystemExit("pass --video (or run `dubstitch.py speech` first)")
    if a.range:                                                     # one free stretch: report every talking moment in it
        t0, t1 = (float(x) for x in a.range.split(","))
        r = dict(idx=-1, cls="range", text=f"{t0}-{t1}s", start_ms=int(t0 * 1000), end_ms=int(t1 * 1000))
        cwd = os.getcwd(); os.chdir(a.talknet); nets = load_talknet(a.talknet); os.chdir(cwd)
        out_dir = os.path.join(a.work, "lips_range"); os.makedirs(out_dir, exist_ok=True)
        res = run_line(r, S["src_video"], nets, out_dir, 0.0, a.threshold, a.merge, a.min_len, a.size, a.det_every)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{res['window'][0]:.3f}", "-t", f"{res['window'][1] - res['window'][0]:.3f}",
                              "-i", S["src_video"], "-vf", f"fps={FPS},scale={a.size}:{a.size}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True).stdout
        mo, ap = mouth_series(np.frombuffer(raw, np.uint8).reshape(-1, a.size, a.size, 3))
        res["mouth"] = [round(float(v), 4) for v in mo]; res["aperture"] = [round(float(v), 4) for v in ap]
        json.dump(res, open(os.path.join(out_dir, f"range_{t0:g}_{t1:g}.json"), "w"))
        print(f"faces found in {res['faces_frames']}/{res['frames']} frames; tracks: " +
              "; ".join(f"{t['first_s']:.2f}-{t['last_s']:.2f}s talknet {t['mean_score']:+.1f} picture {t['mean_visual']:+.1f}" for t in res["tracks"]))
        for k in range(0, len(mo), 5):
            t = res["window"][0] + k / FPS
            print(f"  {t:6.2f}s  lip-opening change {mo[k]:.4f}  opening {ap[k]:.3f}  talknet {res['scores'][k]:+.1f}  picture-only {res['visual'][k]:+.1f}")
        return
    todo = [r for r in S["rows"] if r["cls"] != "narration"]
    if a.only: todo = [r for r in S["rows"] if r["idx"] in {int(x) for x in a.only.split(",")}]
    out_dir = os.path.join(a.work, "lips"); os.makedirs(out_dir, exist_ok=True)
    sig = json.dumps([a.margin, a.threshold, a.merge, a.min_len, a.size, a.det_every])
    nets = None; t_stop = time.time() + a.max_seconds; done = 0
    if a.mouth:
        for r in todo:
            fp = os.path.join(out_dir, f"{r['idx']:04d}.json"); d = json.load(open(fp))
            if "mouth" in d: done += 1; continue
            if time.time() > t_stop: break
            t0, t1 = d["window"]; size = a.size
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", S["src_video"], "-vf",
                                  f"fps={FPS},scale={size}:{size}:force_original_aspect_ratio=decrease,pad={size}:{size}:(ow-iw)/2:(oh-ih)/2",
                                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
            fr = np.frombuffer(raw, np.uint8).reshape(-1, size, size, 3)
            mo, ap = mouth_series(fr)
            d["mouth"] = [round(float(v), 4) for v in mo]; d["aperture"] = [round(float(v), 4) for v in ap]
            json.dump(d, open(fp, "w"), ensure_ascii=False); done += 1
            print(f"SRC#{r['idx']:3d} mouth: max motion {mo.max():.3f}, frames > 0.01: {(mo > 0.01).sum()}/{len(mo)}", flush=True)
        print(f"done {done}/{len(todo)} lines"); sys.exit(0 if done == len(todo) else 3)
    for r in todo:
        fp = os.path.join(out_dir, f"{r['idx']:04d}.json")
        if not a.only and os.path.exists(fp) and json.load(open(fp)).get("sig") == sig + r["text"]:
            done += 1; continue
        if time.time() > t_stop: break
        if nets is None:
            cwd = os.getcwd(); os.chdir(a.talknet); nets = load_talknet(a.talknet); os.chdir(cwd)
        t = time.time(); res = run_line(r, S["src_video"], nets, out_dir, a.margin, a.threshold, a.merge, a.min_len, a.size, a.det_every)
        res["sig"] = sig + r["text"]; json.dump(res, open(fp, "w"), ensure_ascii=False); done += 1
        talk = sum(v["end_s"] - v["start_s"] for v in res["intervals"]); mo = sum(v["end_s"] - v["start_s"] for v in res["intervals_visual"])
        print(f"SRC#{r['idx']:3d} {r['cls']:6s} {res['frames']:4d} frames, {len(res['tracks'])} faces, mouth moving {mo:4.1f}s, in sync with voice {talk:4.1f}s of "
              f"{res['end_s'] - res['start_s']:.1f}s  ({time.time() - t:.0f}s)  {r['text'][:50]!r}", flush=True)
    print(f"done {done}/{len(todo)} lines")
    if done < len(todo):
        print("not finished -- run the same command again (it resumes)"); sys.exit(3)
    if a.only: return
    allr = [json.load(open(os.path.join(out_dir, f"{r['idx']:04d}.json"))) for r in todo]
    for r in allr: r.pop("scores", None); r.pop("visual", None); r.pop("sig", None)
    json.dump(dict(talknet=os.path.abspath(a.talknet), settings=json.loads(sig), lines=allr),
              open(os.path.join(a.work, "lips.json"), "w"), ensure_ascii=False, indent=1)
    n_l = sum(1 for r in allr if r["intervals"])
    print(f"lips.json: {n_l}/{len(allr)} spoken lines show a talking face on screen "
          f"({sum(v['end_s'] - v['start_s'] for r in allr for v in r['intervals']):.0f}s of lip movement)")

if __name__ == "__main__":
    main()
