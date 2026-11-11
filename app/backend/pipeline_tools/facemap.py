"""Face map of the source video (owner, 2026-10-29): for every shot of the B-roll library WHO is on screen, WHERE (left /
centre / right), FACE / PROFILE / BACK, and WHO TALKS WHEN -- from the picture, not guessed from the words.

Tested on TGS ep34 (40 shots incl. all 18 two-person shots, 67 heard lines) against a hand-checked key: people found 98%,
1 wrong person, face/back 56/58, position 33/37, on-screen speaker 35/37 with no speaker given to narration (Claude's
3-frame labels: 95%, 2 wrong, speaker 34/37 + 3 given to narration; Gemini 3.5 Flash on the clips: 97%, 5 wrong, 28/37).

How:
  1. every shot at --fps frames/s, full size: InsightFace 2.1 buffalo_l (face box, head pose, ArcFace identity) and
     YOLO26-L (person boxes; cell phones: a face inside a phone is a photo, never a person);
  2. all faces of the episode -> identity groups (cosine similarity >= 0.40); a group is named by VOTE of the library's
     one-person shots it appears in (Claude already named those); a group without a clear vote: Claude names it against
     reference faces (one call; skipped if the AI gateway is not reachable -> "unknown");
  3. a person box that holds no face = someone seen from behind: named by elimination (the library's people of that shot
     minus the faces found), else by Claude from a body crop with the others greyed out;
  4. who talks: the lip scan's face tracks with their OWN TalkNet scores (lipfast.py keeps them per face since
     2026-10-29); a track gets the name of the faces it covers; talking = score > 0 for at least 0.3 s.
Writes <library dir>/face_map.json. Run with the face environment (venv_face: insightface, onnxruntime, ultralytics).
Usage: facemap.py --video V --library <dir>/broll_library.json --lips <work>/lips_nets [--fps 5] [--app <dub-studioo/app/backend>]
"""
import argparse, base64, io, json, os, subprocess, sys, time
import numpy as np, cv2

def keep_named_groups(gname, how, big):
    """Keep names only for identity groups that exist.

    Vote naming only writes ids from `big` (≥4 faces). Claude's JSON can invent a group number
    (TAB EP48: KeyError 83 when a named id was not a real cluster)."""
    keep, how2 = {}, {}
    for g, n in gname.items():
        if g not in big:
            print(f"Claude named group {g} which is not a real identity group -- skipped")
            continue
        keep[g] = n
        if g in how:
            how2[g] = how[g]
    return keep, how2

p = argparse.ArgumentParser(); p.add_argument("--video", required=True); p.add_argument("--library", required=True)
p.add_argument("--lips", required=True); p.add_argument("--fps", type=float, default=5); p.add_argument("--yolo", default="yolo26l.pt")
p.add_argument("--app", default=os.environ.get("DUB_APP_BACKEND", "")); p.add_argument("--thr", type=float, default=0.40)
a = p.parse_args()
LIB = json.load(open(a.library)); SH = LIB["shots"]; cast = (LIB.get("cast") or {}).get("cast", []); NAMES = [c["name"] for c in cast]
OUT = os.path.join(os.path.dirname(a.library), "face_map.json")
ov = lambda a0, a1, b0, b1: max(0.0, min(a1, b1) - max(a0, b0))
def iou(x, y):
    xa, ya, xb, yb = max(x[0], y[0]), max(x[1], y[1]), min(x[2], y[2]), min(x[3], y[3]); i = max(0, xb - xa) * max(0, yb - ya)
    return i / max((x[2] - x[0]) * (x[3] - x[1]) + (y[2] - y[0]) * (y[3] - y[1]) - i, 1e-6)
ai = None
if a.app:
    try:
        sys.path.insert(0, a.app); from dubstudio import ai as _ai; _ai.key_info(); ai = _ai
    except Exception as ex:
        print("AI gateway not reachable -- groups without a clear vote stay 'unknown':", str(ex)[:100]); ai = None
pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", a.video],
                    capture_output=True, text=True).stdout.strip().split(","); VW, VH = int(pr[0]), int(pr[1])
k = 1080 / max(VW, VH); FW, FH = int(round(VW * k / 2) * 2), int(round(VH * k / 2) * 2)      # analysis size: long side 1080
from insightface.app import FaceAnalysis
fa = FaceAnalysis(name="buffalo_l", allowed_modules=["detection", "recognition", "landmark_3d_68"]); fa.prepare(ctx_id=0, det_size=(640, 640))
from ultralytics import YOLO
import torch
yolo = YOLO(a.yolo); ydev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
T = dict(decode=0.0, faces=0.0, persons=0.0); t_all = time.time(); faces, frames = [], {}
for r in SH:
    t = time.time()
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{r['start']:.3f}", "-t", f"{r['end'] - r['start']:.3f}", "-i", a.video, "-vf",
                          f"fps={a.fps},scale={FW}:{FH}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, FH, FW, 3); T["decode"] += time.time() - t; recs = []
    for i, img in enumerate(fr):
        img = np.ascontiguousarray(img); tt = round(r["start"] + (i + 0.5) / a.fps, 3)
        if i % 2 == 0:                   # bodies / phones move slowly: every 2nd analysed frame is enough (YOLO is the slow part)
            t = time.time(); y = yolo.predict(img, classes=[0, 67], conf=0.3, device=ydev, verbose=False)[0]; T["persons"] += time.time() - t
            boxes = y.boxes.xyxy.cpu().numpy().tolist(); cl = y.boxes.cls.cpu().numpy().tolist(); cf = y.boxes.conf.cpu().numpy().tolist()
            persons = [b for b, c, s in zip(boxes, cl, cf) if c == 0 and s >= 0.5]; phones = [b for b, c in zip(boxes, cl) if c == 67]
        t = time.time(); fl = []
        for f in fa.get(img):
            b = [float(v) for v in f.bbox]; cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            if f.det_score < 0.6 or (b[3] - b[1]) < 40: continue
            if any(q[0] <= cx <= q[2] and q[1] <= cy <= q[3] for q in phones): continue        # a face in a photo on a phone
            pose = [float(v) for v in f.pose] if getattr(f, "pose", None) is not None else [0, 0, 0]
            fl.append(len(faces)); faces.append(dict(shot=r["n"], t=tt, box=b, yaw=abs(pose[1]), emb=f.normed_embedding.astype(np.float32)))
        T["faces"] += time.time() - t
        recs.append(dict(t=tt, faces=fl, persons=persons))
    frames[r["n"]] = recs
print(f"{len(SH)} shots, {len(faces)} faces; decode {T['decode']:.0f} s, faces {T['faces']:.0f} s, persons {T['persons']:.0f} s", flush=True)

# ---- identity groups ----
from scipy.cluster.hierarchy import linkage, fcluster
X = np.stack([f["emb"] for f in faces]); X /= np.linalg.norm(X, axis=1, keepdims=True)
lab = fcluster(linkage(X, "average", metric="cosine"), 1 - a.thr, "distance")
groups = {}
for i, (f, l) in enumerate(zip(faces, lab)): f["g"] = int(l); groups.setdefault(int(l), []).append(i)
big = {g: v for g, v in groups.items() if len(v) >= 4}
one = {r["n"]: r["people"][0] for r in SH if len(r.get("people") or []) == 1 and r["people"][0] in NAMES}
gname, how = {}, {}
for g, v in big.items():
    votes = {}
    for i in v:
        n = one.get(faces[i]["shot"])
        if n: votes[n] = votes.get(n, 0) + 1
    tot = sum(votes.values())
    if votes and tot >= 3 and max(votes.values()) >= 0.6 * tot:
        gname[g] = max(votes, key=votes.get); how[g] = f"vote {votes}"

def crop(i, pad=0.12, size=160, box=None, t=None, mask=()):
    t = faces[i]["t"] if t is None else t; box = faces[i]["box"] if box is None else box
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", a.video, "-frames:v", "1", "-vf", f"scale={FW}:{FH}",
                          "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
    from PIL import Image, ImageDraw
    im = Image.open(io.BytesIO(raw)).convert("RGB"); d = ImageDraw.Draw(im)
    for m in mask: d.rectangle(list(m), fill=(128, 128, 128))
    w, h = box[2] - box[0], box[3] - box[1]
    im = im.crop((max(0, box[0] - pad * w), max(0, box[1] - pad * h), min(FW, box[2] + pad * w), min(FH, box[3] + pad * h))); im.thumbnail((size, size))
    return im
def grid(ims, labels, cols=6, cell=160):
    from PIL import Image, ImageDraw
    rows = (len(ims) + cols - 1) // cols; G = Image.new("RGB", (cols * cell, rows * (cell + 18)), "white"); d = ImageDraw.Draw(G)
    for j, (im, lb) in enumerate(zip(ims, labels)):
        x, y = (j % cols) * cell, (j // cols) * (cell + 18); G.paste(im, (x, y + 18)); d.text((x + 3, y + 2), lb, fill="black")
    b = io.BytesIO(); G.save(b, "JPEG", quality=85); return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
def picks(v, n):
    v = sorted(v, key=lambda i: -(faces[i]["box"][3] - faces[i]["box"][1])); return [v[int(j * (len(v) - 1) / max(n - 1, 1))] for j in range(min(n, len(v)))]
LOOKS = ("Characters:\n" + "\n".join(f"- {c['name']}: {c['look']}" for c in cast) +
         "\nThe same character can wear other clothes in a flashback: judge by the FACE (shape, hair, age), not the clothes.")
rest = [g for g in big if g not in gname]
if rest and ai:
    refs = {}
    for g in sorted(gname, key=lambda g: -len(big[g])):
        if gname[g] not in refs: refs[gname[g]] = picks(big[g], 2)
    ims = [crop(i) for n in refs for i in refs[n]]; lbs = [f"{n} (reference)" for n in refs for i in refs[n]]
    for g in rest:
        for i in picks(big[g], 3): ims.append(crop(i)); lbs.append(f"group {g}")
    q = ("The first pictures are REFERENCE faces of known characters (labelled with the name). The others are face crops "
         "labelled with a group number. " + LOOKS + "\nFor every group: the character name if it is the same person as a "
         "reference or fits a character, a short description if it is nobody from the list, or \"photo\" if the face is inside "
         "a photo / screen. Answer only JSON: {\"groups\": {\"<group number>\": \"name\"}}")
    try:
        text, _ = ai.chat([{"type": "text", "text": q}, {"type": "image_url", "image_url": {"url": grid(ims, lbs)}}], max_tokens=2000)
        for kk, v in ((ai.extract_json(text, "groups") or {}).get("groups") or {}).items():
            try:
                gid = int(kk)
            except (TypeError, ValueError):
                print(f"Claude named group {kk!r} which is not a number -- skipped")
                continue
            if gid not in big:
                print(f"Claude named group {gid} which is not a real identity group -- skipped")
                continue
            gname[gid] = v; how[gid] = "Claude (reference faces)"
    except Exception as ex:
        print("group naming by Claude failed:", str(ex)[:120])
gname, how = keep_named_groups(gname, how, big)
cent = {g: (lambda m: m / np.linalg.norm(m))(X[big[g]].mean(0)) for g in gname}
for i, f in enumerate(faces):
    if f["g"] in gname: f["name"] = gname[f["g"]]; continue
    best = max(cent, key=lambda g: float(X[i] @ cent[g])) if cent else None
    f["name"] = gname[best] if best is not None and float(X[i] @ cent[best]) >= a.thr - 0.05 else None
print("groups:", {g: (gname.get(g, "unknown"), len(big[g]), how.get(g, "")) for g in sorted(big, key=lambda g: -len(big[g]))}, flush=True)

# ---- lip scan face tracks (per face TalkNet scores) ----
tracks = []
for fn in sorted(os.listdir(a.lips)):
    if not fn.startswith("range_"): continue
    for tr in json.load(open(os.path.join(a.lips, fn))).get("tracks") or []:
        if "score" in tr: tracks.append(tr)
if not tracks: print("WARNING: the lip scan has no per-face scores (run lipgpu.py again with the 2026-10-29 lipfast.py): no talking info")
LS = (tracks[0]["size"] if tracks else 540); ks = max(VW, VH) / LS; px, py = (LS - VW / ks) / 2, (LS - VH / ks) / 2
to_fr = lambda b: [(b[0] - px) * ks * k, (b[1] - py) * ks * k, (b[2] - px) * ks * k, (b[3] - py) * ks * k]
pos = lambda b: "left" if (b[0] + b[2]) / 2 < FW * 0.38 else ("right" if (b[0] + b[2]) / 2 > FW * 0.62 else "centre")
view = lambda yaw: "face" if yaw < 35 else ("profile" if yaw < 70 else "back")

out = {}; backs = []
for r in SH:
    n = r["n"]; recs = frames[n]; fs = [faces[i] for rc in recs for i in rc["faces"] if faces[i].get("name") and faces[i]["name"] != "photo"]
    P = {}
    for f in fs: P.setdefault(f["name"], []).append(f)
    persons = []
    for nm, v in list(P.items()):
        if len(v) < 2 and len(recs) > 3: P.pop(nm); continue      # a single stray frame is not a person
        persons.append(dict(name=nm, pos=pos(np.median([f["box"] for f in v], 0)), view=view(float(np.median([f["yaw"] for f in v]))),
                            from_s=min(f["t"] for f in v), to_s=max(f["t"] for f in v), frames=len(v)))
    nf = []
    for rc in recs:                      # bodies without a face: each face belongs to the smallest body whose upper part holds it
        fb = [faces[i]["box"] for i in rc["faces"]]; pb = rc["persons"]; has = set()
        for b in fb:
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            cand = [j for j, q in enumerate(pb) if q[0] <= cx <= q[2] and q[1] - 0.1 * (q[3] - q[1]) <= cy <= q[1] + 0.6 * (q[3] - q[1])]
            if cand: has.add(min(cand, key=lambda j: (pb[j][2] - pb[j][0]) * (pb[j][3] - pb[j][1])))
        for j, q in enumerate(pb):
            if j not in has: nf.append((rc["t"], q, [x for jj, x in enumerate(pb) if jj != j] + fb))
    gl = []
    for t, b, oth in nf:
        g = next((g for g in gl if iou(g[-1][1], b) > 0.3), None); (g.append((t, b, oth)) if g else gl.append([(t, b, oth)]))
    for g in gl:
        if len(g) < max(2, 0.25 * len(recs)): continue
        t, b, oth = g[len(g) // 2]; o = [x for x in oth if iou(x, b) < 0.9]
        if o:
            lx, rx = min(x[0] for x in o), max(x[2] for x in o); L_, R_ = max(0, lx - b[0]), max(0, b[2] - rx)
            ps = "centre" if max(L_, R_) < 80 else ("left" if L_ > R_ else "right")
        else: ps = pos(b)
        left = [x for x in (r.get("people") or []) if x in NAMES and x not in P]
        e = dict(name=left[0] if len(left) == 1 else None, pos=ps, view="back", from_s=g[0][0], to_s=g[-1][0], frames=len(g),
                 how="elimination" if len(left) == 1 else None)
        persons.append(e)
        if not e["name"]: backs.append((n, e, t, b, o))
    # who talks: per lip-scan face track inside this shot
    talk = []
    for tr in tracks:
        t0 = tr["f0"]; m = len(tr["score"])
        if ov(t0, t0 + m / 25, r["start"], r["end"]) < 0.3: continue
        votes = {}
        for j in range(0, m, 5):
            tt = t0 + j / 25; bx = to_fr(tr["boxes"][j])
            for f in fs:
                if abs(f["t"] - tt) < 0.11 and iou(f["box"], bx) > 0.3: votes[f["name"]] = votes.get(f["name"], 0) + 1
        who = max(votes, key=votes.get) if votes else None
        runs, cur = [], None
        for j, sc in enumerate(tr["score"]):
            tt = t0 + j / 25
            if not (r["start"] <= tt < r["end"]): continue
            if sc > 0: cur = [tt, tt + 0.04] if cur is None else [cur[0], tt + 0.04]
            elif cur: runs.append(cur); cur = None
        if cur: runs.append(cur)
        merged = []
        for x in runs:
            if merged and x[0] - merged[-1][1] < 0.3: merged[-1][1] = x[1]
            else: merged.append(list(x))
        for x in merged:
            if x[1] - x[0] >= 0.3: talk.append(dict(name=who or "unknown face", from_s=round(x[0], 2), to_s=round(x[1], 2)))
    out[str(n)] = dict(persons=persons, talking=sorted(talk, key=lambda x: x["from_s"]))
if backs and ai:
    ims = [crop(None, pad=0.0, size=200, box=b, t=t, mask=o) for n, e, t, b, o in backs]
    q = ("Each numbered picture shows ONE person seen from behind / without a clear face in a drama episode (other people are "
         "greyed out). " + LOOKS + "\nWho is each one (hair, clothes, build)? A name from the list, a short description of anyone "
         "else, or \"none\" if no person is left. Answer only JSON: {\"people\": {\"<number>\": \"name\"}}")
    try:
        text, _ = ai.chat([{"type": "text", "text": q}, {"type": "image_url", "image_url": {"url": grid(ims, [str(j) for j in range(len(ims))], cols=5, cell=200)}}], max_tokens=2000)
        bn = (ai.extract_json(text, "people") or {}).get("people") or {}
        for j, (n, e, t, b, o) in enumerate(backs):
            if bn.get(str(j)) and bn[str(j)] != "none": e["name"] = bn[str(j)]; e["how"] = "Claude (body crop)"
    except Exception as ex:
        print("back naming by Claude failed:", str(ex)[:120])
for v in out.values(): v["persons"] = [x for x in v["persons"] if x["name"]] + [dict(x, name="unknown person") for x in v["persons"] if not x["name"]]
json.dump(dict(video=a.video, fps=a.fps, groups={str(g): dict(name=gname.get(g, "unknown"), faces=len(big[g]), how=how.get(g, "")) for g in big},
               shots=out, seconds=round(time.time() - t_all), timing={x: round(y) for x, y in T.items()}), open(OUT, "w"), indent=1, ensure_ascii=False)
two = sum(1 for v in out.values() if len(v["persons"]) >= 2); tk = sum(1 for v in out.values() if v["talking"])
print(f"saved {OUT}: {len(out)} shots ({two} with 2+ people, {tk} with someone talking) in {time.time() - t_all:.0f} s")
