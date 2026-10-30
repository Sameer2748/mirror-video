"""Slow-clip rule (owner, 2026-10-26): a clip that plays slower than 0.8x gets more footage instead of slow motion.

The clip keeps its own picture at about normal speed and the rest of the Italian line is filled from the SAME scene:
  1. run-on    the free seconds right after (narration: also right before) the clip in the same shot -- no new cut;
  2. B-roll    an unused shot of the same scene (same place / people, found by look + time): narration gets a
               reaction / cutaway with no mouth moving; a lip clip keeps the speaker first and continues over the
               listener (a face with a still mouth) or over more of the same camera setup. Claude picks the shot
               from frames when the AI is set up; otherwise the best-ranked shot is used;
  3. reuse     (last resort; off with --no-reuse) footage of the same scene the episode already shows, at least 30 s
               away in the episode (8 s for more of the same camera setup), a different part of it, played with an 8%
               zoom-in so it does not read as a repeat.
Every piece plays at one speed (about 1.0x). Every B-roll is stored (W/slowfix.json, "Slow clips" tab of the groups
review sheet) and marked for the editor (yellow "B-roll" flag); nothing is hidden.

CLI: python slowfix.py <work> [--threshold 0.8] [--target 0.9] [--no-ai] [--no-reuse] [--max-seconds N]
Exit 0 = done, 3 = Claude's answers not all in yet (run again, it resumes).
"""
import argparse, json, math, os, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))          # app/backend: dubstudio.*
from dubstudio.pipeline import fastfix as ff                          # noqa: E402  (scenes, frame_b64, _load)

THRESHOLD, TARGET = 0.8, 0.9    # the owner's table: 0.8-1.25x plays fine; a filled clip ends at about 0.9x
MIN_FILL = 0.3          # s: less missing footage than this is left as a small slow-down
MIN_PIECE = 0.8         # s: a B-roll piece is at least this long when the footage allows ...
MIN_PART = 0.5          # s: ... and never shorter than this (a flash)
MAX_AFTER = 1.15        # a filled line never plays faster than this
SCENE_WIN = 45.0        # s: B-roll only from shots this close in the source (the same scene)
SIM_MIN = 0.5           # look similarity (0-1) for "same scene"
SAME_SETUP = 0.80       # look similarity for "same camera setup"
REUSE_GAP = 20.0        # s: reused footage must be this far (in the episode) from where it is already shown ...
REUSE_GAP_SETUP = 6.0   # s: ... or this far for more of the SAME camera setup (owner's car example: 41-43 s again at 47 s)
ZOOM = 1.08             # zoom-in on reused footage (bottom-anchored: burned-in captions stay under the strip)
MARGIN = 0.04           # s kept free around used footage
SHOT_GAP = 10.0         # s: the same B-roll shot is not cut in twice within this time of the episode

PROMPT = """You are a film editor fixing a SLOW clip in a dubbed episode. The Italian line is longer than the source clip
under it, so the clip would play in slow motion ({speed:.2f}x). Instead, the clip plays at normal speed and {need:.1f} s of
the line must be covered with other footage from the SAME scene (a B-roll).

Italian line: "{it_text}"
Source line of the clip: "{us_text}"
Kind: {kind}

Rules:
- narration (nobody speaks on screen): the B-roll must show NO mouth talking. A reaction of the person the line is
  about, the place, an object, or a wide shot is best. It may go before or after the clip.
- lip-sync (a face speaks this line): the speaker's clip stays FIRST; the B-roll goes after it: best the listener's face
  (mouth still), or more of the same speaker in the same camera setup (the voice continues over it).
- Same place, same people, same moment of the story. Never a shot that shows something that has not happened yet.

Image 1 is the clip itself. The other images are the candidate shots, numbered:
{cands}

Answer only JSON: {{"order": [candidate numbers, best first, at most 3; [] if none fits], "place": "after" or "before",
"reason": "one sentence"}}"""


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def subtract(a, b, cuts):
    """[a, b] minus every interval in cuts -> list of free pieces"""
    out, x = [], a
    for c0, c1 in sorted(cuts):
        if c1 <= x or c0 >= b: continue
        if c0 > x: out.append((x, c0))
        x = max(x, c1)
    if x < b: out.append((x, b))
    return out


def looks(video, dur, fps=2, w=64):
    """One small RGB thumbnail every 1/fps s of the whole video -> list of histograms + thumbnails (for similarity)."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", f"fps={fps},scale={w}:{w}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, w, w, 3)
    q = (fr // 64).astype(np.int32)                                      # 4 levels per channel -> 64-bin histogram
    idx = q[..., 0] * 16 + q[..., 1] * 4 + q[..., 2]
    hist = np.stack([np.bincount(i.ravel(), minlength=64) for i in idx]).astype(np.float32)
    hist /= hist.sum(1, keepdims=True)
    gray = fr.mean(3)[:, ::4, ::4].reshape(len(fr), -1).astype(np.float32)   # 16x16 layout
    gray = (gray - gray.mean(1, keepdims=True)) / (gray.std(1, keepdims=True) + 1e-6)
    return fps, hist, gray


def shot_feature(L, a, b):
    import numpy as np
    fps, hist, gray = L
    i0, i1 = int(a * fps), max(int(a * fps) + 1, int(b * fps))
    i1 = min(i1, len(hist)); i0 = min(i0, i1 - 1)
    return hist[i0:i1].mean(0), gray[i0:i1].mean(0)


def similarity(f1, f2):
    import numpy as np
    h = float(np.minimum(f1[0], f2[0]).sum())                           # colours / light of the place
    g = float(np.dot(f1[1], f2[1]) / (np.linalg.norm(f1[1]) * np.linalg.norm(f2[1]) + 1e-6))   # layout of the frame
    return 0.6 * h + 0.4 * max(g, 0)


def main(a):
    W = Path(a.work)
    M = ff._load(W / "manifest_groups.json"); cfg = ff._load(W / "config.json", {}) or {}
    if not M or not M.get("segments") or not cfg.get("src_video"):
        print("slow-clip rule: no edit list yet, nothing to do"); return 0
    video = cfg["src_video"]
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                               capture_output=True, text=True).stdout)
    Lp = ff._load(W / "lipsync.json", {}) or {}
    lip_rows = {int(k): v for k, v in (Lp.get("rows") or {}).items()}
    lip_iv = [tuple(x) for x in (Lp.get("intervals") or [])]
    cuts = ff.scenes(W, video)
    bounds = [0.0] + [c for c in cuts if 0 < c < dur] + [dur]
    shots = [(x, y) for x, y in zip(bounds, bounds[1:]) if y - x > 0.05]
    feat_cache = W / "slowfix_looks.npz"
    import numpy as np
    if feat_cache.exists():
        z = np.load(feat_cache); LK = (int(z["fps"]), z["hist"], z["gray"])
    else:
        LK = looks(video, dur); np.savez(feat_cache, fps=LK[0], hist=LK[1], gray=LK[2])
    SF = [shot_feature(LK, x, y) for x, y in shots]
    # the per-character B-roll library (broll_library.py, owner 2026-10-27), when it exists: Claude's label of every shot.
    # Only shots it marks B-roll (reaction / neutral / establishing; never a story action like picking up the pastry) and
    # only shots of the clip's own story scene may fill a slow clip.
    LIB = ff._load(W / "broll_library.json", {}) or {}
    lib_shots = LIB.get("shots") or []
    def lib_at(t):
        return next((r for r in lib_shots if r["start"] <= t < r["end"]), None)
    shot_of = lambda t: next((k for k, (x, y) in enumerate(shots) if x <= t < y), len(shots) - 1)
    lips_in = lambda x, y: sum(overlap(x, y, p, q) for p, q in lip_iv)
    segs = M["segments"]
    used = [(s["us_start_ms"] / 1000 - MARGIN, s["us_end_ms"] / 1000 + MARGIN, k) for k, s in enumerate(segs) if not s.get("video")]
    # the source's own opening and ending belong to `bookends` (played at 1x before/after the episode): never B-roll
    src_segs = [s for s in segs if not s.get("video")]
    if src_segs:
        used += [(0.0, min(s["us_start_ms"] for s in src_segs) / 1000, -1), (max(s["us_end_ms"] for s in src_segs) / 1000, dur, -1)]
    use_ai = not a.no_ai
    if use_ai:
        try:
            from dubstudio import ai
            use_ai = ai.available()
        except Exception:
            use_ai = False

    # ---- 1. the slow clips ----
    jobs = []
    for k, s in enumerate(segs):
        if s.get("video"): continue
        d = (s["it_end_ms"] - s["it_start_ms"]) / 1000; u = (s["us_end_ms"] - s["us_start_ms"]) / 1000
        if d <= 0.6 or u <= 0 or u / d >= a.threshold: continue
        i = s["it_idx"]; lp = lip_rows.get(i); s0, s1 = s["us_start_ms"] / 1000, s["us_end_ms"] / 1000
        is_lip = bool(lp) and overlap(s0, s1, lp["sentence"][0] / 1000, lp["sentence"][1] / 1000) > 0.3 * (s1 - s0)
        # a lip clip must start so its mouth starts with the voice (owner, TGS ep31 "Paul... warte!" spoken at 3.3 s of a 0-4.5 s
        # slot: the girl's mouth moved at 0-1.4 s over silence). The silence before the voice is a LEAD-IN to fill.
        lead = 0.0
        if is_lip:
            sp0 = (lp.get("speech") or lp.get("slot") or [s["it_start_ms"]])[0]
            lon = min([max(x, s0) for x, y in lip_iv if y > s0 and x < s1] or [s0])
            lead = (sp0 - s["it_start_ms"]) / 1000 - (lon - s0)
            lead = max(0.0, min(lead, d - u))
            if lead < MIN_PART: lead = 0.0
        jobs.append(dict(k=k, row=i, lip=is_lip, kind="lip-sync" if is_lip else "narration", s0=s0, s1=s1, slot=d,
                         speed=u / d, need=d * a.target - u, shot=shot_of((s0 + s1) / 2), lead=round(lead, 2)))
    print(f"slow-clip rule (<{a.threshold}x): {len(jobs)} clips, {sum(j['slot'] for j in jobs):.1f} s of the episode")

    def free_parts(x, y, me, no_lips):
        others = [(p, q) for p, q, kk in used if kk != me]
        parts = subtract(x, y, others)
        if no_lips:
            parts = [pp for p, q in parts for pp in subtract(p, q, lip_iv)]
        return [(p, q) for p, q in parts if q - p > 0.05]

    # ---- 2. candidates per clip (B-roll shots of the same scene) ----
    def reuse_parts(x, y, j):
        """footage of shot (x, y) the episode already shows, but not near this clip in the episode: the part shown
        within REUSE_GAP s (REUSE_GAP_SETUP for the same camera setup) and this clip's own footage are left out"""
        me = j["k"]; it_mid = (segs[me]["it_start_ms"] + segs[me]["it_end_ms"]) / 2000
        gap = REUSE_GAP_SETUP if similarity(SF[j["shot"]], SF[shot_of((x + y) / 2)]) >= SAME_SETUP else REUSE_GAP
        near = [(p, q) for p, q, kk in used if overlap(x, y, p, q) > 0 and (kk == me or kk < 0 or
                abs((segs[kk]["it_start_ms"] + segs[kk]["it_end_ms"]) / 2000 - it_mid) < gap)]
        parts = subtract(x, y, near)
        if not j["lip"]: parts = [pp for p, q in parts for pp in subtract(p, q, lip_iv)]
        return [(p, q) for p, q in parts if q - p > 0.05]

    def parts_for(c, j):
        if c["reuse"]: return reuse_parts(c["x"], c["y"], j)
        return free_parts(c["x"], c["y"], j["k"], no_lips=not j["lip"] or c["sim"] < SAME_SETUP)

    def candidates(j):
        """same-scene shots with footage for this clip: unused first, then (with reuse) footage shown far away"""
        f0 = SF[j["shot"]]; free_c, reuse_c = [], []
        for n, (x, y) in enumerate(shots):
            if n == j["shot"]: continue
            mid = (x + y) / 2
            if abs(mid - (j["s0"] + j["s1"]) / 2) > (SCENE_WIN * 3 if lib_shots else SCENE_WIN): continue   # the library knows the scene
            sim = similarity(f0, SF[n])
            lr = lib_at(mid) if lib_shots else None
            if lib_shots:
                me_lr = lib_at((j["s0"] + j["s1"]) / 2)
                if not lr or lr.get("broll") != "yes": continue           # a story action / talking / object: never
                if me_lr and lr.get("scene") != me_lr.get("scene"): continue   # another story scene: never
            elif sim < SIM_MIN: continue
            lips = lips_in(x, y)
            role = ("same setup" if sim >= SAME_SETUP else "listener / reaction" if lips < 0.2 * (y - x) else "same scene")
            if lr:
                role = f"{lr.get('type')}: {', '.join(lr.get('people') or []) or 'no person'} - {lr.get('action', '')}"[:120]
            base = dict(shot=n, x=x, y=y, sim=round(sim, 3), lips=round(lips, 2), role=role, lib=(lr or {}).get("n"),
                        dist=round(abs(mid - (j["s0"] + j["s1"]) / 2), 1))
            for reuse, L in ((False, free_c), (True, reuse_c)):
                if reuse and not a.reuse: continue
                c = dict(base, reuse=reuse)
                parts = [(p, q) for p, q in parts_for(c, j) if q - p >= MIN_PART]
                if parts:
                    L.append(dict(c, parts=parts, free=round(sum(q - p for p, q in parts), 2)))
        # rank: a listener (still mouth) for lip clips; similar look; close in time; unused before reused
        def score(c):
            sc = c["sim"] - c["dist"] / (SCENE_WIN * 4)
            if j["lip"] and (c["role"] == "listener / reaction" or c["role"].startswith("reaction")): sc += 0.15
            return sc
        return sorted(free_c, key=score, reverse=True)[:4] + sorted(reuse_c, key=score, reverse=True)[:3]

    def ask(j, cands):
        from dubstudio import ai
        seg = segs[j["k"]]
        txt = "\n".join(f"  {n + 2}. shot {c['x']:.1f}-{c['y']:.1f} s ({c['dist']:.0f} s from the clip), {c['role']}, "
                        + (f"ALREADY SHOWN elsewhere in the episode (would be reused, zoomed in 8%: use only if no unused shot fits), "
                           if c["reuse"] else "unused, ")
                        + f"{c['free']:.1f} s available, mouth moving {c['lips']:.1f} s" for n, c in enumerate(cands))
        q = PROMPT.format(speed=j["speed"], need=j["need"], it_text=seg.get("it_text", ""), us_text=seg.get("us_text", ""),
                          kind=j["kind"], cands=txt)
        if j.get("lead"):
            q += (f"\n\nTIMING (important): the voice starts speaking this line only {j['lead']:.1f} s into its slot. The speaker's "
                  f"clip must start exactly with the voice, so the mouth moves on the words. The first {j['lead']:.1f} s need a "
                  f"LEAD-IN shot: what the scene shows just before the line is said (the person the line is said to, or the "
                  f"place) and NOT the speaker talking. After the speaker's clip, about {max(0, j['need'] - j['lead']):.1f} s "
                  f"need a listener reaction. Answer only JSON: {{\"before\": [candidate numbers for the lead-in, best first], "
                  f"\"order\": [candidate numbers for after the clip, best first], \"place\": \"after\", \"reason\": \"one sentence\"}}")
        content = [{"type": "text", "text": q}, {"type": "text", "text": "image 1 = the clip:"},
                   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + ff.frame_b64(video, (j["s0"] + j["s1"]) / 2)}}]
        for n, c in enumerate(cands):
            p, qq = c["parts"][0]
            content += [{"type": "text", "text": f"image {n + 2} = candidate {n + 2}:"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + ff.frame_b64(video, (p + qq) / 2)}}]
        text, _u = ai.chat(content, max_tokens=3000)
        ans = ai.extract_json(text, "order") or {}
        order = [int(x) - 2 for x in ans.get("order") or [] if str(x).lstrip("-").isdigit() and 0 <= int(x) - 2 < len(cands)]
        place = "after" if j["lip"] else (ans.get("place") if ans.get("place") in ("before", "after") else "after")
        before = [int(x) - 2 for x in ans.get("before") or [] if str(x).lstrip("-").isdigit() and 0 <= int(x) - 2 < len(cands)]
        return dict(order=order, before=before, place=place, reason=(ans.get("reason") or "")[:300], by="Claude")

    # Claude's answers are cached (resumable with --max-seconds)
    cache_p = W / "slowfix_cache.json"; cache = ff._load(cache_p, {}) or {}
    lock = threading.Lock(); t_end = time.time() + a.max_seconds
    for j in jobs:
        j["cands"] = candidates(j)
    key = lambda j: (f"{j['row']}|{j['s0']:.3f}|{j['s1']:.3f}|" + ",".join(f"{c['x']:.2f}" for c in j["cands"])
                     + (f"|lead{j['lead']:.1f}" if j.get("lead") else ""))

    def decide(j):
        if not j["cands"]: return dict(order=[], place="after", reason="no free shot of the same scene", by="rule")
        kk = key(j)
        if kk in cache: return cache[kk]
        if not use_ai: return dict(order=list(range(len(j["cands"]))), place="after", reason="best-ranked shot (no AI)", by="rule")
        if time.time() > t_end: return None
        try:
            r = ask(j, j["cands"])
        except Exception as ex:
            r = dict(order=list(range(len(j["cands"]))), place="after", reason=f"AI error: {str(ex)[:100]}", by="rule (AI error)")
        with lock:
            cache[kk] = r; json.dump(cache, open(cache_p, "w"), indent=1, ensure_ascii=False)
        return r
    with ThreadPoolExecutor(4) as ex:
        answers = list(ex.map(decide, jobs))
    if any(x is None for x in answers):
        print(f"{sum(x is None for x in answers)} of {len(jobs)} slow clips still to ask Claude -- run again (it resumes)")
        return 3

    # ---- 3. fill each clip, one after another (footage given to one clip is no longer free for the next) ----
    report = dict(threshold=a.threshold, target=a.target, ai=use_ai, reuse=a.reuse, clips=[])
    plan = {}; broll_shots = []          # (shot, episode time) of every B-roll given out
    for j, ans in zip(jobs, answers):
        need = j["need"]; s0, s1 = j["s0"], j["s1"]; me = j["k"]
        sx, sy = shots[j["shot"]]
        # 1. run-on in the same shot: after (and, for narration, before) the clip, free and (narration) mouth-free
        run_after = run_before = 0.0
        aft = [p for p in free_parts(s1, sy, me, no_lips=not j["lip"]) if abs(p[0] - s1) < 0.06]
        if aft: run_after = min(need, aft[0][1] - s1)
        if not j["lip"] and need - run_after > 0.05:
            bef = [p for p in free_parts(sx, s0, me, no_lips=True) if abs(p[1] - s0) < 0.06]
            if bef: run_before = min(need - run_after, s0 - bef[-1][0])
        n0, n1 = s0 - run_before, s1 + run_after
        rest = need - run_before - run_after
        used.append((n0 - MARGIN, n1 + MARGIN, me))
        pieces = []; before = []
        it_mid = (segs[me]["it_start_ms"] + segs[me]["it_end_ms"]) / 2000
        if j.get("lead"):
            # the lead-in: the same shot just before the clip (the speaker before speaking) if free, else Claude's lead-in shots
            want = j["lead"]
            bef = [p for p in free_parts(sx, s0, me, no_lips=True) if abs(p[1] - s0) < 0.06 and p[1] - p[0] >= MIN_PART]
            if bef:
                ln = min(want, bef[-1][1] - bef[-1][0])
                before.append(dict(s0=s0 - ln, s1=s0, shot=j["shot"], role="lead-in: the same shot just before the line",
                                   sim=1.0, zoom=None, by="rule")); used.append((s0 - ln - MARGIN, s0, me)); want -= ln
            border = list(ans.get("before") or []) if ans.get("by") == "Claude" else []
            for ci in border:
                if want < MIN_PART: break
                c = j["cands"][ci]
                for p, q in parts_for(c, j):
                    if q - p < MIN_PART: continue
                    ln = min(q - p, want); st = p + (q - p - ln) / 2
                    if ln < MIN_PART: continue
                    before.append(dict(s0=st, s1=st + ln, shot=c["shot"], role="lead-in: " + (("reuse, zoomed in - " + c["role"]) if c["reuse"] else c["role"]),
                                       sim=c["sim"], zoom=ZOOM if c["reuse"] else None, by="Claude"))
                    used.append((st - MARGIN, st + ln + MARGIN, me)); broll_shots.append((c["shot"], it_mid)); want -= ln
                    break
            rest -= sum(b["s1"] - b["s0"] for b in before)
        # 2. B-roll from the same scene: Claude's order first, then the rest of the ranked candidates
        cap = j["slot"] * MAX_AFTER - (n1 - n0) - sum(b["s1"] - b["s0"] for b in before)   # never so much that the line plays fast
        # Claude's choice is final: a shot it left out (e.g. "Aiden asleep: that has not happened yet") is never used;
        # only without an AI answer does the ranked list decide
        order = list(ans["order"]) if ans.get("by") == "Claude" else list(range(len(j["cands"])))
        for ci in order:
            if rest <= 0.05 or cap < MIN_PART: break
            c = j["cands"][ci]
            if any(sh == c["shot"] and abs(t - it_mid) < SHOT_GAP for sh, t in broll_shots): continue   # no insert twice in a row
            for p, q in parts_for(c, j):
                if q - p < MIN_PART: continue
                ln = min(q - p, max(rest, MIN_PIECE), cap)
                if ln < MIN_PART: continue
                st = p + (q - p - ln) / 2                              # the middle of the free part
                pieces.append(dict(s0=st, s1=st + ln, shot=c["shot"], role=("reuse, zoomed in - " + c["role"]) if c["reuse"] else c["role"],
                                   sim=c["sim"], zoom=ZOOM if c["reuse"] else None,
                                   by=ans["by"] if ci in ans["order"] else "rule"))
                used.append((st - MARGIN, st + ln + MARGIN, me)); rest -= ln; cap -= ln
                broll_shots.append((c["shot"], it_mid))
                if rest <= 0.05 or cap < MIN_PART: break            # else: the next free part of the same shot too
        if not pieces and not before and run_before + run_after < 0.05:
            report["clips"].append(dict(row=j["row"], kind=j["kind"], it0=segs[me]["it_start_ms"], it1=segs[me]["it_end_ms"],
                                        speed_before=round(j["speed"], 3), speed_after=round(j["speed"], 3), runon_s=0,
                                        broll=[], by=ans["by"], reason=ans["reason"] or "no free footage of the same scene",
                                        it_text=segs[me].get("it_text", ""), status="still slow", cands=len(j["cands"])))
            continue
        plan[me] = dict(main=(n0, n1), pieces=pieces, place=ans["place"], before=before, lead=j.get("lead", 0))
        total = (n1 - n0) + sum(p["s1"] - p["s0"] for p in pieces + before)
        report["clips"].append(dict(
            row=j["row"], kind=j["kind"], it0=segs[me]["it_start_ms"], it1=segs[me]["it_end_ms"], us0=s0 * 1000, us1=s1 * 1000,
            speed_before=round(j["speed"], 3), speed_after=round(total / j["slot"], 3), runon_s=round(run_before + run_after, 2),
            broll=[dict(us0=round(p["s0"] * 1000), us1=round(p["s1"] * 1000), role=p["role"], sim=p["sim"], zoom=p["zoom"],
                        by=p["by"]) for p in before + pieces], place=ans["place"], lead_s=j.get("lead", 0),
            by=("Claude" if any(p["by"] == "Claude" for p in pieces + before) else "rule") if pieces or before else "rule (run-on)",
            reason=ans["reason"] if pieces or before else "more of the same shot", it_text=segs[me].get("it_text", ""),
            us_text=segs[me].get("us_text", ""), status="ok" if total / j["slot"] >= a.threshold - 0.01 else "still slow",
            cands=len(j["cands"])))

    # ---- 4. apply: each slow segment becomes its clip (+ run-on) and its B-roll pieces, at one speed ----
    out = []
    for k, s in enumerate(segs):
        if k not in plan:
            out.append(s); continue
        P = plan[k]; main_ = dict(s0=P["main"][0], s1=P["main"][1], role="clip", zoom=None)
        if P.get("lead"):
            # lead-in pieces in the silence, then the speaker at 1x so the mouth starts with the voice, then the listener
            slot = s["it_end_ms"] - s["it_start_ms"]; fb = sum(p["s1"] - p["s0"] for p in P["before"]) * 1000
            B = min(P["lead"] * 1000, fb / a.threshold) if fb else 0.0
            um = (main_["s1"] - main_["s0"]) * 1000
            rem = slot - B - um
            parts = P["before"] + [main_] + (P["pieces"] if rem >= MIN_PART * 1000 else [])
            fa = sum(p["s1"] - p["s0"] for p in P["pieces"]) * 1000 if rem >= MIN_PART * 1000 else 0
            dur = [ (p["s1"] - p["s0"]) * 1000 / fb * B for p in P["before"] ]
            dur += [um if fa else slot - B]
            dur += [(p["s1"] - p["s0"]) * 1000 / fa * rem for p in P["pieces"]] if fa else []
            share = dur
        else:
            parts = ([main_] + P["pieces"]) if P["place"] == "after" else (P["pieces"] + [main_])
        tot = sum(p["s1"] - p["s0"] for p in parts); t = s["it_start_ms"]; slot = s["it_end_ms"] - s["it_start_ms"]
        # screen time per piece in proportion to its footage, but no piece shorter than MIN_PART on screen (a sliver of a
        # clip played at 1x would flash: TOLR DE-UK ep9 had a 0.09 s lip piece)
        if not P.get("lead"): share = [(p["s1"] - p["s0"]) / tot * slot for p in parts]
        short = [k for k, x in enumerate(share) if x < MIN_PART * 1000]
        if not P.get("lead") and short and len(parts) > 1 and slot >= MIN_PART * 1000 * len(parts):
            fix = MIN_PART * 1000; rest_ = slot - fix * len(short); big = sum(x for k, x in enumerate(share) if k not in short)
            share = [fix if k in short else x * rest_ / big for k, x in enumerate(share)]
        for n, p in enumerate(parts):
            t1 = s["it_end_ms"] if n == len(parts) - 1 else t + share[n]
            note = (s.get("note") or "")
            if p["role"] == "clip":
                note = (note + "; slow-clip run-on").lstrip("; ") if (p["s1"] - p["s0"]) * 1000 > (s["us_end_ms"] - s["us_start_ms"]) + 50 else note
            else:
                note = (note + f"; slow-clip B-roll ({p['role']})").lstrip("; ")
            ns = dict(s, it_start_ms=round(t, 3), it_end_ms=round(t1, 3), us_start_ms=round(p["s0"] * 1000, 3),
                      us_end_ms=round(p["s1"] * 1000, 3), stretch_factor=round((t1 - t) / ((p["s1"] - p["s0"]) * 1000), 3), note=note)
            if p.get("zoom"): ns["zoom"] = p["zoom"]
            if p["role"] != "clip": ns["broll"] = p["role"]
            out.append(ns); t = t1
    for x, y in zip(out, out[1:]):
        assert abs(x["it_end_ms"] - y["it_start_ms"]) < 1, "timeline not contiguous"
    if not (W / "manifest_groups.before_slowfix.json").exists():
        json.dump(M, open(W / "manifest_groups.before_slowfix.json", "w"), indent=1, ensure_ascii=False)
    M2 = dict(M, segments=out)
    json.dump(M2, open(W / "manifest_groups.json", "w"), indent=1, ensure_ascii=False)
    def switches(m):
        S = sorted(m["segments"], key=lambda x: x["it_start_ms"])
        return [y["it_start_ms"] for x, y in zip(S, S[1:]) if abs(y["us_start_ms"] - x["us_end_ms"]) > 50]
    old_c = switches(M)
    new_c = [c for c in switches(M2) if min((abs(c - o) for o in old_c), default=1e9) > 60]
    json.dump({"new_cuts_ms": [round(c) for c in new_c]}, open(W / "slowfix_cuts.json", "w"))
    # the speed of the whole episode, before and after
    def share(m, lo):
        tot = sum(s["it_end_ms"] - s["it_start_ms"] for s in m["segments"])
        sl = sum(s["it_end_ms"] - s["it_start_ms"] for s in m["segments"] if not s.get("video") and s["it_end_ms"] > s["it_start_ms"]
                 and (s["us_end_ms"] - s["us_start_ms"]) / (s["it_end_ms"] - s["it_start_ms"]) < lo)
        return sl / max(tot, 1)
    report["slow_share_before"] = round(share(M, a.threshold), 4); report["slow_share_after"] = round(share(M2, a.threshold), 4)
    report["new_cuts"] = len(new_c)
    json.dump(report, open(W / "slowfix.json", "w"), indent=1, ensure_ascii=False)
    try:
        write_sheet(W, cfg, report)
    except Exception as ex:
        print(f"slow-clip sheet not written: {ex}")
    C = report["clips"]; br = [c for c in C if c["broll"]]
    roles = {}
    for c in br:
        for b in c["broll"]: roles[b["role"]] = roles.get(b["role"], 0) + 1
    print(f"slow clips: {len(C)}; filled with run-on only {sum(1 for c in C if c['runon_s'] and not c['broll'])}, "
          f"with B-roll {len(br)} ({roles}); Claude chose {sum(1 for c in br if c['by'] == 'Claude')}; "
          f"still under {a.threshold}x {sum(1 for c in C if c['status'] == 'still slow')}; new cuts {len(new_c)}")
    print(f"time under {a.threshold}x: {report['slow_share_before']:.1%} -> {report['slow_share_after']:.1%} of the episode")
    return 0


def write_sheet(W, cfg, r):
    """'Slow clips' tab of the groups review workbook: every clip, what filled it, who chose it, why."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    p = cfg.get("groups_review")
    if not p or not os.path.exists(p): return
    wb = openpyxl.load_workbook(p)
    if "Slow clips" in wb.sheetnames: del wb["Slow clips"]
    ws = wb.create_sheet("Slow clips")
    ws["A1"] = (f"Slow-clip rule: a clip slower than {r['threshold']}x keeps its picture at about {r['target']}x and the rest of "
                f"the line is filled from the same scene (run-on, B-roll{', reuse' if r['reuse'] else ''}). Check every row in the editor.")
    ws["A1"].font = Font(bold=True)
    hd = ["Row", "Kind", "Italian time", "Italian line", "Speed before", "Speed after", "Run-on s", "B-roll (source s, role)",
          "Place", "Chosen by", "Why", "Status"]
    for c, h in enumerate(hd, 1):
        ws.cell(3, c, h).font = Font(bold=True)
    fm = lambda ms: f"{int(ms // 60000):02d}:{ms % 60000 / 1000:05.2f}"
    red = PatternFill("solid", fgColor="F8CBAD"); grn = PatternFill("solid", fgColor="C6EFCE")
    for i, c in enumerate(r["clips"], 4):
        vals = [c["row"], c["kind"], f"{fm(c['it0'])}-{fm(c['it1'])}", c.get("it_text", ""), c["speed_before"], c["speed_after"],
                c["runon_s"], "; ".join(f"{b['us0'] / 1000:.1f}-{b['us1'] / 1000:.1f} {b['role']}" + (" zoom" if b.get("zoom") else "")
                                         for b in c["broll"]), c.get("place", ""), c["by"], c["reason"], c["status"]]
        for k, v in enumerate(vals, 1):
            ws.cell(i, k, v)
        ws.cell(i, 12).fill = grn if c["status"] == "ok" else red
    for col, wd in zip("ABCDEFGHIJKL", (6, 10, 16, 50, 9, 9, 8, 40, 7, 12, 60, 10)):
        ws.column_dimensions[col].width = wd
    wb.save(p)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("work"); ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--target", type=float, default=TARGET); ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--no-reuse", dest="reuse", action="store_false", help="no reuse of footage the episode already shows")
    ap.add_argument("--max-seconds", type=float, default=1e9)
    sys.exit(main(ap.parse_args()))
