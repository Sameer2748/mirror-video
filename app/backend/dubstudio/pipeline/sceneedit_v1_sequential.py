"""Scene editor (owner, 2026-10-27): Claude cuts the picture of the dub like a film editor, from the story.

Why: the row-by-row matching + groups + B-roll fill each line on its own. That put "Paul... warte!" (her mouth) over the
silence before the voice, and Paul's "Ja?" clip during the narration before it. A person edits the scene as a whole:
first Paul walking to the door, THEN the girl calling "wait" exactly when the voice says it; a narration about Paul shows
Paul; an action is shown when it is told.

The dub range is cut into windows of about 30 s (at a pause between lines). For each window, in order, Claude gets:
  - every dub line with its exact voice times, marked dialogue (quotes) or narration;
  - every source shot of the scene(s) the automatic match uses there (from the B-roll library): frames, who, shot type,
    what happens, when a mouth moves, the source narration over it, where the automatic match used it;
  - the footage already used by the earlier windows, and the last pieces before this window (continuity);
and returns an edit decision list (EDL): back-to-back pieces of shots. The script checks it (tiling, footage inside its
shot, speed 0.8-1.25x, piece >= 0.5 s, every dialogue line starts on a moving mouth within 0.3 s, action/talking footage
only once in the episode, reaction/neutral repeats 8 s apart) and sends the problems back to Claude (up to 4 rounds).

Usage: sceneedit.py <work> <library dir> <from s> <to s> <out manifest json> [--window 30] [--model M]
Writes the manifest (segments for render) and <out>.edl.json (every window: EDL with reasons, check rounds).
"""
import base64, json, os, re, sys
from pathlib import Path

W, LIBD, A0, A1, OUTM = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), Path(sys.argv[5])
opt = lambda k, d: type(d)(sys.argv[sys.argv.index(k) + 1]) if k in sys.argv else d
WIN = opt("--window", 30.0); MODEL = opt("--model", "")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))          # app/backend
from dubstudio import ai  # noqa: E402

SPEED_LO, SPEED_HI, MIN_PIECE, LIP_TOL, REUSE_GAP = 0.8, 1.25, 0.5, 0.3, 8.0
LIB = json.load(open(LIBD / "broll_library.json")); SH = {r["n"]: r for r in LIB["shots"]}
cast = LIB.get("cast", {}).get("cast", [])
dub = [d for d in json.load(open(W / "sentences_dub.json"))["sentences"] if d.get("start_ms") is not None]
src = json.load(open(W / "sentences_src.json"))["sentences"]
lip_iv = [tuple(v) for v in (json.load(open(W / "lipsync.json")).get("intervals") or [])]
auto = json.load(open(W / "manifest_groups.json"))["segments"]
ov = lambda a0, a1, b0, b1: max(0.0, min(a1, b1) - max(a0, b0))
QUOTE = re.compile(r"[„“\"«»]")
for d in dub:
    d["dialogue"] = bool(QUOTE.search(d["text"]))


def windows(a0, a1):
    """cut points in the pauses between lines, about every WIN s"""
    gaps = [(dub[i]["end_ms"] / 1000 + dub[i + 1]["start_ms"] / 1000) / 2 for i in range(len(dub) - 1)
            if dub[i]["end_ms"] / 1000 > a0 and dub[i + 1]["start_ms"] / 1000 < a1]
    cuts, t = [a0], a0
    while a1 - t > WIN * 1.4:
        cand = [g for g in gaps if t + WIN * 0.6 <= g <= t + WIN * 1.4]
        if not cand: break
        t = min(cand, key=lambda g: abs(g - (t + WIN))); cuts.append(round(t, 2))
    return list(zip(cuts, cuts[1:] + [a1]))


def shot_txt(r, auto_w):
    lips = [(max(a, r["start"]), min(b, r["end"])) for a, b in lip_iv if ov(a, b, r["start"], r["end"]) > 0.05]
    said = " ".join(s["text"] for s in src if ov(s["start_ms"] / 1000, s["end_ms"] / 1000, r["start"], r["end"]) > 0.1)
    auto_at = [f"{s['it_start_ms'] / 1000:.1f}-{s['it_end_ms'] / 1000:.1f}" for s in auto_w
               if ov(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000, r["start"], r["end"]) > 0.1]
    return (f"shot {r['n']}: source {r['start']:.2f}-{r['end']:.2f} s ({r['length']} s) | {', '.join(r['people']) or 'nobody'} | "
            f"{r['type']} | {r['action']} | mouth moving at: {', '.join(f'{a:.2f}-{b:.2f}' for a, b in lips) or 'never'} | "
            f"source narration over it: \"{said[:160]}\"" + (f" | the automatic match used it at dub {', '.join(auto_at)}" if auto_at else ""))


def prompt(T0, T1):
    return f"""You are the film editor of a dubbed episode. The dub's voice track is fixed. Cut the PICTURE for dub time {T0:.2f}-{T1:.2f} s
using only the source shots below (frames attached). Think about the story like an editor: what must the viewer see at each
moment so picture and voice tell the same story?

Characters: {'; '.join(f"{c['name']}: {c['look']}" for c in cast)}
(The dub may use other names for the same people; match them by the story.)

Rules:
1. The pieces are back to back and cover {T0:.2f}-{T1:.2f} s exactly (first t0 = {T0:.2f}, each t0 = the previous t1, last t1 = {T1:.2f}).
2. A piece plays one shot from "from" to "to" (source seconds, inside that shot). speed = (to - from) / (t1 - t0) must be
   between {SPEED_LO} and {SPEED_HI} (1.0 is best). A piece lasts at least {MIN_PIECE} s.
3. DIALOGUE line (in quotes): show the SPEAKER with a moving mouth, timed so the mouth starts moving when the voice starts:
   the source time where the mouth starts moving must land on the line's start (within {LIP_TOL} s). You may cut to the
   listener after the first words of a long line.
4. NARRATION line: show what it tells, and keep the person it is about on screen ("Paul looked down at himself" shows Paul).
   An action (walking to the door, turning round, picking something up) is shown when the voice tells it, not earlier, once.
5. Before a dialogue line, show what leads to it (e.g. the person being called walking away, then the caller says "wait").
6. Never show an event before the voice has told it. Action and talking footage is shown ONCE in the whole episode (see the
   footage already used below). Only reaction / neutral shots (a face listening, looking) may be shown again, at least
   {REUSE_GAP:.0f} s after the last time, with "reuse": true.
7. Prefer longer pieces and few cuts; follow the story order of the footage where you can; continue smoothly from the
   pieces just before this stretch.

Answer ONLY JSON: {{"edl": [{{"t0": s, "t1": s, "shot": n, "from": s, "to": s, "line": dub line number or null, "reuse": false,
"why": "what the viewer sees and why here"}}], "notes": "anything that could not be done"}}"""


def b64(n):
    return "data:image/jpeg;base64," + base64.b64encode(open(LIBD / "_frames" / f"shot_{n:03d}.jpg", "rb").read()).decode()


def check(edl, T0, T1, lines, before):
    probs = []
    if not edl: return ["empty EDL"]
    if abs(edl[0]["t0"] - T0) > 0.02: probs.append(f"first t0 must be {T0}")
    if abs(edl[-1]["t1"] - T1) > 0.02: probs.append(f"last t1 must be {T1}")
    seen = [(e["from"], e["to"], e["t0"], e["t1"], f"earlier piece at dub {e['t0']:.1f} s", SH.get(int(e["shot"]), {}).get("type")) for e in before]
    for k, e in enumerate(edl):
        r = SH.get(int(e["shot"]))
        d = e["t1"] - e["t0"]; u = e["to"] - e["from"]
        if k and abs(e["t0"] - edl[k - 1]["t1"]) > 0.02: probs.append(f"piece {k}: t0 {e['t0']} != previous t1 {edl[k - 1]['t1']}")
        if not r: probs.append(f"piece {k}: shot {e['shot']} is not in the list"); continue
        if e["from"] < r["start"] - 0.05 or e["to"] > r["end"] + 0.05 or u <= 0:
            probs.append(f"piece {k}: from/to {e['from']}-{e['to']} not inside shot {r['n']} ({r['start']:.2f}-{r['end']:.2f})")
        if d < MIN_PIECE - 0.01: probs.append(f"piece {k}: {d:.2f} s is shorter than {MIN_PIECE} s")
        elif u > 0 and not (SPEED_LO - 0.01 <= u / d <= SPEED_HI + 0.01):
            probs.append(f"piece {k}: speed {u / d:.2f} outside {SPEED_LO}-{SPEED_HI} (footage {u:.2f} s on {d:.2f} s)")
        for a, b, t0_, t1_, name, typ in reversed(seen):
            if ov(a, b, e["from"], e["to"]) > 0.2:
                if r["type"] not in ("reaction", "neutral", "establishing"):
                    probs.append(f"piece {k}: shot {r['n']} is a {r['type']} shot and its footage {e['from']:.2f}-{e['to']:.2f} is already "
                                 f"shown ({name}); action/talking footage is shown once: use other footage or let pieces run slower (down to {SPEED_LO})")
                elif not e.get("reuse"):
                    probs.append(f"piece {k}: footage already shown ({name}); mark reuse or use other footage")
                elif e["t0"] - t1_ < REUSE_GAP:
                    probs.append(f"piece {k}: repeats footage {e['t0'] - t1_:.1f} s after it was shown ({name}); a repeat needs {REUSE_GAP:.0f} s in between")
                break
        seen.append((e["from"], e["to"], e["t0"], e["t1"], f"piece {k}", r["type"]))
    for d in lines:                                 # dialogue: a moving mouth must start within LIP_TOL of the voice
        if not d["dialogue"]: continue
        ls = d["start_ms"] / 1000
        if not (T0 <= ls < T1): continue
        e = next((e for e in edl if e["t0"] - 0.01 <= ls < e["t1"]), None)
        if not e: continue
        sp = (e["to"] - e["from"]) / max(e["t1"] - e["t0"], 1e-6)
        src_at = e["from"] + (ls - e["t0"]) * sp
        lips = [(a, b) for a, b in lip_iv if ov(a, b, e["from"], e["to"]) > 0.05]
        starts = [max(a, e["from"]) for a, b in lips]
        good = any(abs(st - src_at) / sp <= LIP_TOL for st in starts) or any(a < src_at - 0.05 < b for a, b in lips)
        if not good:
            probs.append(f"dialogue line {d['idx']} \"{d['text'][:40]}\" starts at {ls:.2f} s but the piece on screen then (shot "
                         f"{e['shot']}, source {src_at:.2f} s) has no mouth starting to move there (mouth moving: "
                         f"{', '.join(f'{a:.2f}-{b:.2f}' for a, b in lips) or 'never'})")
    return probs


def cut_window(T0, T1, before, cache, wkey):
    lines = [d for d in dub if d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1]
    auto_w = [s for s in auto if ov(T0 * 1000, T1 * 1000, s["it_start_ms"], s["it_end_ms"]) > 0]
    used_src = [(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000) for s in auto_w if not s.get("video")]
    hi = max(b for a, b in used_src)
    scenes = {SH[n]["scene"] for n in SH if any(ov(SH[n]["start"], SH[n]["end"], a, b) > 0.05 for a, b in used_src)}
    shots = [r for r in LIB["shots"] if r["scene"] in scenes and (r["start"] <= hi + 40 or r["broll"] == "yes")]
    prev = before[-3:]
    used_txt = "\n".join(f"- shot {e['shot']} source {e['from']:.2f}-{e['to']:.2f} shown at dub {e['t0']:.1f}-{e['t1']:.1f} s"
                         for e in before if int(e["shot"]) in {r["n"] for r in shots}) or "- none"
    line_txt = "\n".join(f"line {d['idx']}: {d['start_ms'] / 1000:.2f}-{d['end_ms'] / 1000:.2f} s  "
                         f"{'DIALOGUE' if d['dialogue'] else 'narration'}: {d['text']}" for d in lines)
    head = (prompt(T0, T1) + "\n\nDUB LINES (voice times):\n" + line_txt +
            "\n\nTHE LAST PIECES BEFORE THIS STRETCH (continue from them):\n" +
            ("\n".join(f"- {e['t0']:.1f}-{e['t1']:.1f} s shot {e['shot']} source {e['from']:.2f}-{e['to']:.2f}: {e.get('why', '')[:120]}"
                       for e in prev) or "- this is the start") +
            "\n\nFOOTAGE OF THESE SHOTS ALREADY SHOWN EARLIER IN THE EPISODE:\n" + used_txt +
            "\n\nSOURCE SHOTS:\n" + "\n".join(shot_txt(r, auto_w) for r in shots) + "\n\nFrames of each shot (3 frames, left to right):")
    content = [{"type": "text", "text": head}]
    for r in shots:
        content += [{"type": "text", "text": f"shot {r['n']}:"}, {"type": "image_url", "image_url": {"url": b64(r["n"])}}]
    extra, rounds, edl = "", [], []
    for rnd in range(4):
        key = f"{wkey}|round{rnd}"
        if key in cache:
            ans = cache[key]
        else:
            c = content + ([{"type": "text", "text": extra}] if extra else [])
            ans = {}
            for attempt in range(3):                       # a cut-off or broken reply is asked again
                try:
                    text, _ = ai.chat(c, model=MODEL or None, max_tokens=24000)
                    ans = ai.extract_json(text, "edl") or {}
                    if ans.get("edl"): break
                except Exception as ex:
                    print(f"  window {T0:.1f}-{T1:.1f}: reply not readable ({str(ex)[:80]}), asking again", flush=True)
            cache[key] = ans; json.dump(cache, open(OUTM.with_suffix(".cache.json"), "w"), indent=1, ensure_ascii=False)
        try:
            edl = [dict(e, t0=float(e["t0"]), t1=float(e["t1"]), **{"from": float(e["from"]), "to": float(e["to"])}) for e in ans.get("edl") or []]
        except Exception as ex:
            edl = []
        probs = check(edl, T0, T1, lines, before)
        rounds.append(dict(round=rnd, problems=probs, notes=ans.get("notes", "")))
        print(f"  window {T0:.1f}-{T1:.1f}: round {rnd}: {len(edl)} pieces, {len(probs)} problems" + (": " + " | ".join(probs[:4]) if probs else ""), flush=True)
        if not probs: break
        extra = ("Your previous EDL:\n" + json.dumps(ans.get("edl"), ensure_ascii=False) + "\n\nIt has these problems, fix them all "
                 "and answer the complete corrected JSON again:\n- " + "\n- ".join(probs))
    return edl, rounds, lines, [r["n"] for r in shots]


cache_p = OUTM.with_suffix(".cache.json"); cache = json.load(open(cache_p)) if cache_p.exists() else {}
allE, report = [], []
for T0, T1 in windows(A0, A1):
    edl, rounds, lines, shots = cut_window(T0, T1, allE, cache, f"{T0:.2f}-{T1:.2f}")
    allE += edl
    report.append(dict(window=[T0, T1], lines=lines, shots=shots, edl=edl, rounds=rounds))

segs = []
for e in allE:
    d = next((d for d in dub if d["start_ms"] / 1000 <= (e["t0"] + e["t1"]) / 2 <= d["end_ms"] / 1000), None)
    segs.append(dict(kind="clip", it_idx=e.get("line") if e.get("line") is not None else (d["idx"] if d else -1),
                     it_start_ms=round(e["t0"] * 1000, 3), it_end_ms=round(e["t1"] * 1000, 3),
                     us_start_ms=round(e["from"] * 1000, 3), us_end_ms=round(e["to"] * 1000, 3),
                     it_text=d["text"] if d else "", us_text="", stretch_factor=round((e["t1"] - e["t0"]) / max(e["to"] - e["from"], 1e-6), 3),
                     note="scene edit: " + e.get("why", "")[:200], **({"zoom": 1.08} if e.get("reuse") else {})))
json.dump(dict(total_ms=round(A1 * 1000), segments=segs, bleed_report=[], groups=[]), open(OUTM, "w"), indent=1, ensure_ascii=False)
json.dump(dict(range=[A0, A1], windows=report), open(OUTM.with_suffix(".edl.json"), "w"), indent=1, ensure_ascii=False)
left = sum(len(w["rounds"][-1]["problems"]) for w in report)
print("saved", OUTM, len(segs), "pieces in", len(report), "windows;", "all checks OK" if not left else f"{left} problems left")
