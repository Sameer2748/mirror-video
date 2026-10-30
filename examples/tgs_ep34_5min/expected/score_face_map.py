"""Score a face_map.json against the hand-checked answer key (40 ep34 shots).
Usage: python score_face_map.py <face_map.json>"""
"""Score every method against the answer key (gt.json). Usage: score.py [method files...]"""
import json, sys
W = "/".join(__file__.split("/")[:-2]) if "/" in __file__ else ".."
import os
HERE = os.path.dirname(os.path.abspath(__file__)); WK = os.path.join(HERE, "..", "work")
gt = json.load(open(os.path.join(HERE, "face_answer_key_40_shots.json"))); S = {r["n"]: r for r in json.load(open(os.path.join(WK, "broll_library", "broll_library.json")))["shots"]}
src = json.load(open(os.path.join(WK, "scene", "sentences_src.json")))["sentences"]; lip = json.load(open(os.path.join(WK, "scene", "lipsync.json")))["intervals"]
spk = json.load(open(os.path.join(WK, "scene", "speakers.json")))["shots"]
CAST = {"Ava", "Aiden", "Charlotte"}
ov = lambda a0, a1, b0, b1: max(0.0, min(a1, b1) - max(a0, b0))
def norm(n):
    n = (n or "").strip()
    for c in CAST:
        if n.lower() == c.lower(): return c
    if not n or n.lower() in ("none", "photo", "unknown face", "unknown", "narrator", "off-screen"): return None
    return "other"
def gnorm(n): return n if n in CAST else "other"
def lines_of(sn):
    s = S[int(sn)]; g = {round(x["t"], 1): x for x in gt[sn]["s"]}
    out = []
    for x in src:
        a0 = x["start_ms"] / 1000
        if ov(a0, x["end_ms"] / 1000, s["start"], s["end"]) <= 0.15: continue
        k = next((k for k in g if abs(k - a0) < 0.25), None)
        out.append((a0, g[k]["speaker"] if k is not None and g[k]["on_screen"] else None))
    return out
def baseline():
    res = {}
    for sn in gt:
        s = S[int(sn)]; P = [dict(name=n, pos=None, view=None) for n in s["people"]]
        L = []
        for a0, _ in lines_of(sn):
            x = next(x for x in src if abs(x["start_ms"] / 1000 - a0) < 1e-6)
            m = sum(ov(a, b, a0, x["end_ms"] / 1000) for a, b in lip if ov(a, b, s["start"], s["end"]) > 0)
            t = spk.get(sn) or []
            L.append(dict(t=a0, speaker=t[0] if (m > 0.3 and t) else None))
        res[sn] = dict(persons=P, lines=L)
    return res
def gem(path):
    G = json.load(open(path)); res = {}
    for sn, r in G.items():
        P = [dict(name=p.get("name"), pos={"left": "L", "center": "C", "right": "R"}.get((p.get("position") or "").lower()),
                  view=("back" if ((p.get("view") or "").startswith("back") or "not visible" in (p.get("view") or "")) else "face") if p.get("view") else None) for p in r.get("persons") or []]
        L = []
        for i, t in enumerate(r.get("line_times") or []):
            def li(l):
                try: return int(l.get("line", -1))
                except Exception: return -1
            l = next((l for l in r.get("lines") or [] if li(l) == i), {})
            L.append(dict(t=t, speaker=l.get("speaker") if l.get("mouth_visible") else None))
        res[sn] = dict(persons=P, lines=L)
    return res
import os
FR, MX = float(os.environ.get("FRAC", 0.15)), float(os.environ.get("MAXS", 9))
def spk_rule(tracks):
    ok = [t for t in tracks if t["frac"] >= FR or t["max"] >= MX]
    if not ok: return None
    b = max(ok, key=lambda t: (t["frac"], t["mean"]))
    return b["name"] or "unknown face"
def local(path):
    return {sn: dict(persons=r["persons"], lines=[dict(t=l["t"], speaker=spk_rule(l["tracks"])) for l in r["lines"]])
            for sn, r in json.load(open(path))["shots"].items()}
def score(name, R):
    tp = fp = fn = 0; vok = vn = 0; pok = pn = 0; lok = ln = 0; errs = []; dn = [0, 0]; nn = [0, 0]
    for sn, g in gt.items():
        r = R.get(sn)
        if r is None: errs.append(f"#{sn}: no answer"); fn += len(g["p"]); continue
        G = {}
        for p in g["p"]: G.setdefault(gnorm(p["name"]), []).append(p)
        Pn = {}
        for p in r["persons"]:                    # one entry per person: the same name twice (e.g. face, then back) = one person
            n = norm(p.get("name"))
            if not n: continue
            if n in Pn and n != "other":
                q = Pn[n][0]
                if p.get("view") == "face": q["view"] = "face"
                continue
            Pn.setdefault(n, []).append(dict(p))
        if "other" in Pn and "other" in G: Pn["other"] = Pn["other"][:len(G["other"])] + Pn["other"][len(G["other"]):]
        for n in set(G) | set(Pn):
            a, b = len(G.get(n, [])), len(Pn.get(n, []))
            tp += min(a, b); fn += max(0, a - b); fp += max(0, b - a)
            if a > b: errs.append(f"#{sn}: missed {n}")
            if b > a: errs.append(f"#{sn}: extra {n}")
            for gp, pp in zip(G.get(n, []), Pn.get(n, [])):
                if pp.get("view"):
                    vn += 1; ok = (gp["view"] == "face") == (pp["view"] == "face"); vok += ok
                    if not ok: errs.append(f"#{sn}: {n} view {pp['view']} (is {gp['view']})")
                if pp.get("pos") and len(g["p"]) > 1:
                    pn += 1; pok += gp["pos"] == pp["pos"]
                    if gp["pos"] != pp["pos"]: errs.append(f"#{sn}: {n} at {pp['pos']} (is {gp['pos']})")
        for a0, want in lines_of(sn):
            l = next((l for l in r["lines"] if abs(l["t"] - a0) < 0.3), None)
            got = norm(l["speaker"]) if l else None; ln += 1; lok += got == want
            if want: dn[0] += 1; dn[1] += got == want
            else: nn[0] += 1; nn[1] += got is not None
            if got != want: errs.append(f"#{sn} line {a0:.1f}: on-screen speaker {got} (is {want})")
    pr = tp / max(tp + fp, 1); rc = tp / max(tp + fn, 1)
    print(f"\n== {name}: people found {rc:.0%} (missed {fn}), wrong people {fp}, precision {pr:.0%}; face/back {vok}/{vn}; "
          f"left/centre/right {pok}/{pn} (two-person shots); on-screen speaker per line {lok}/{ln} = dialogue on screen {dn[1]}/{dn[0]} right, narration/off-screen lines given a wrong speaker {nn[1]}/{nn[0]}")
    for e in errs: print("   ", e)
    return dict(found=rc, precision=pr, missed=fn, extra=fp, view=f"{vok}/{vn}", pos=f"{pok}/{pn}", speaker=f"{lok}/{ln}", dialogue=f"{dn[1]}/{dn[0]}", false_speaker=f"{nn[1]}/{nn[0]}")
def fmap(path):
    F = json.load(open(path))["shots"]; res = {}
    for sn in gt:
        f = F.get(sn, {"persons": [], "talking": []}); s = S[int(sn)]
        P = [dict(name=p["name"], pos={"left": "L", "centre": "C", "right": "R"}.get(p["pos"]), view="face" if p["view"] in ("face", "profile") else "back") for p in f["persons"]]
        L = []
        for a0, _ in lines_of(sn):
            x = next(x for x in src if abs(x["start_ms"] / 1000 - a0) < 1e-6); a1 = x["end_ms"] / 1000
            o = {}
            for t in f["talking"]: o[t["name"]] = o.get(t["name"], 0) + ov(t["from_s"], t["to_s"], a0, a1)
            best = max(o, key=o.get) if o else None
            L.append(dict(t=a0, speaker=best if best and o[best] >= min(0.3, 0.3 * (a1 - a0)) else None))
        res[sn] = dict(persons=P, lines=L)
    return res
out = {"baseline (today)": score("baseline (today: library labels + speaker call)", baseline())}
for f in sys.argv[1:]:
    R = gem(f) if "gemini" in f else (fmap(f) if "face_map" in f else local(f))
    out[f] = score(f, R)
json.dump(out, open(os.path.join(HERE, "scores.json"), "w"), indent=1)
