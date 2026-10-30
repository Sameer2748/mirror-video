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

Usage: sceneedit.py <work> <library dir> <from s> <to s> <out manifest json> [--window 30] [--model M] [--parallel N]
  --parallel N: all stretches at the same time, N Claude calls at once (see PARALLEL MODE below); without it: one by one.
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


# =====================================================================================================================
# PARALLEL MODE (--parallel N, owner 2026-10-27): every stretch is cut at the same time, without waiting for the one before.
#   1. stretches are cut at scene changes first (different scenes share no shots), then about every WIN s inside a scene;
#   2. inside a scene the footage is shared out BEFORE the calls: the action/talking/object seconds the automatic match
#      gave to a stretch belong to it (free seconds of a shot go to the nearest owner); reaction/neutral/establishing shots
#      are shared by all stretches;
#   3. the context at the joins comes from the automatic edit (its pieces just before/after), not from Claude's real cut;
#   4. after all replies the software joins them and checks the whole episode (action footage twice, reaction repeats
#      closer than 8 s, jump cuts at the joins); only the stretches with a problem go back to Claude, also in parallel,
#      now with the real neighbouring pieces. Repeats of shared shots are marked (zoom 8%) by the code, not by Claude.
# =====================================================================================================================
import threading, time
from concurrent.futures import ThreadPoolExecutor

PAR = int(opt("--parallel", 0))
SHARED = ("reaction", "neutral", "establishing")
LOCK = threading.Lock()
USAGE = dict(calls=0, prompt_tokens=0, completion_tokens=0, call_seconds=0.0)
fmt_iv = lambda iv: ", ".join(f"{a:.2f}-{b:.2f}" for a, b in iv)


def ask_claude(c):
    t = time.time()
    text, u = ai.chat(c, model=MODEL or None, max_tokens=24000)
    with LOCK:
        USAGE["calls"] += 1; USAGE["call_seconds"] += time.time() - t
        USAGE["prompt_tokens"] += int((u or {}).get("prompt_tokens") or 0); USAGE["completion_tokens"] += int((u or {}).get("completion_tokens") or 0)
    return text


def scene_at(t):
    return next((r["scene"] for r in LIB["shots"] if r["start"] <= t < r["end"]), None)


def line_scene(d):
    tot = {}
    for s in auto:
        o = ov(d["start_ms"], d["end_ms"], s["it_start_ms"], s["it_end_ms"])
        if o > 0 and not s.get("video"):
            sc = scene_at((s["us_start_ms"] + s["us_end_ms"]) / 2000); tot[sc] = tot.get(sc, 0) + o
    return max(tot, key=tot.get) if tot else None


def windows_par(a0, a1):
    L = [d for d in dub if d["end_ms"] / 1000 > a0 and d["start_ms"] / 1000 < a1]
    sc = [line_scene(d) for d in L]
    br = [a0]
    for i in range(len(L) - 1):
        if sc[i] and sc[i + 1] and sc[i] != sc[i + 1]:
            g = round((L[i]["end_ms"] + L[i + 1]["start_ms"]) / 2000, 2)
            if g - br[-1] >= 8 and a1 - g >= 8: br.append(g)          # a scene bit under 8 s stays with its neighbour
    br.append(a1)
    out = []
    for s0, s1 in zip(br, br[1:]): out += windows(s0, s1)
    return out


def cands_for(T0, T1):
    auto_w = [s for s in auto if ov(T0 * 1000, T1 * 1000, s["it_start_ms"], s["it_end_ms"]) > 0]
    used_src = [(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000) for s in auto_w if not s.get("video")]
    if not used_src:
        near = min((s for s in auto if not s.get("video")), key=lambda s: abs(s["it_start_ms"] / 1000 - T0))
        used_src = [(near["us_start_ms"] / 1000, near["us_end_ms"] / 1000)]
    hi = max(b for a, b in used_src)
    scenes = {SH[n]["scene"] for n in SH if any(ov(SH[n]["start"], SH[n]["end"], a, b) > 0.05 for a, b in used_src)}
    shots = [r for r in LIB["shots"] if r["scene"] in scenes and (r["start"] <= hi + 40 or r["broll"] == "yes")]
    return auto_w, used_src, shots


def ownership(wins, cand, used):
    """(shot, stretch) -> source intervals of a talking/action/object shot that stretch may use"""
    def w_of(s):
        m = (s["it_start_ms"] + s["it_end_ms"]) / 2000
        return next((k for k, (a, b) in enumerate(wins) if a <= m < b), None)
    marks = {}
    for s in auto:
        k = w_of(s)
        if s.get("video") or k is None: continue
        a, b = s["us_start_ms"] / 1000, s["us_end_ms"] / 1000
        for r in LIB["shots"]:
            if ov(a, b, r["start"], r["end"]) > 0.05:
                marks.setdefault(r["n"], []).append((max(a, r["start"]), min(b, r["end"]), k))
    own = {}
    for r in LIB["shots"]:
        if r["type"] in SHARED: continue
        n = r["n"]; ws = [k for k in range(len(wins)) if n in cand[k]]
        m = sorted(marks.get(n, []), key=lambda x: (x[0] + x[1]) / 2)
        if not m:
            if ws:
                c = (r["start"] + r["end"]) / 2
                k = min(ws, key=lambda k: min((abs(c - (a + b) / 2) for a, b in used[k]), default=1e9))
                own[(n, k)] = [(r["start"], r["end"])]
            continue
        bnd = []
        for i in range(len(m) - 1):
            x = (m[i][1] + m[i + 1][0]) / 2
            bnd.append(max(x, bnd[-1]) if bnd else x)
        edges = [r["start"]] + bnd + [r["end"]]
        regs = []
        for i, (_, _, k) in enumerate(m):
            lo, hi = edges[i], edges[i + 1]
            if regs and regs[-1][2] == k: regs[-1][1] = hi
            else: regs.append([lo, hi, k])
        for lo, hi, k in regs:
            if hi - lo > 0.05: own.setdefault((n, k), []).append((round(lo, 2), round(hi, 2)))
    return own


def auto_reuse(pieces):
    """pieces in dub order: a piece whose footage was already shown is a repeat (zoomed 8% at render)"""
    for j, e in enumerate(pieces):
        e["reuse"] = any(ov(f["from"], f["to"], e["from"], e["to"]) > 0.2 for f in pieces[:j])


def parse_edl(ans):
    try:
        return [dict(e, t0=float(e["t0"]), t1=float(e["t1"]), **{"from": float(e["from"]), "to": float(e["to"])}) for e in ans.get("edl") or []]
    except Exception:
        return []


def check_par(edl, T0, T1, lines, k, own, others=(), prev_last=None):
    edl = sorted(edl, key=lambda e: e["t0"]); auto_reuse(edl)
    probs = check(edl, T0, T1, lines, [])                       # tiling, inside shot, speed, length, repeats inside, mouths
    for i, e in enumerate(edl):
        r = SH.get(int(e["shot"]))
        if not r or r["type"] in SHARED: continue
        regs = own.get((r["n"], k), [])
        if (e["to"] - e["from"]) - sum(ov(a, b, e["from"], e["to"]) for a, b in regs) > 0.2:
            probs.append(f"piece {i}: shot {r['n']} is a {r['type']} shot and footage {e['from']:.2f}-{e['to']:.2f} is outside what this "
                         f"stretch may use ({fmt_iv(regs) if regs else 'nothing: all of it belongs to other stretches'}); use that part, "
                         f"a reaction/neutral shot, or let pieces run slower (down to {SPEED_LO})")
    for i, e in enumerate(edl):
        r = SH.get(int(e["shot"]))
        if not r: continue
        hits = [f for f in others if ov(f["from"], f["to"], e["from"], e["to"]) > 0.2]
        if not hits: continue
        f = min(hits, key=lambda f: abs(f["t0"] - e["t0"]))
        if r["type"] not in SHARED:
            probs.append(f"piece {i}: footage {e['from']:.2f}-{e['to']:.2f} of {r['type']} shot {r['n']} is already shown at dub "
                         f"{f['t0']:.1f}-{f['t1']:.1f} s by another stretch; action/talking footage is shown once")
        else:
            gap = e["t0"] - f["t1"] if f["t1"] <= e["t0"] else f["t0"] - e["t1"]
            if gap < REUSE_GAP:
                probs.append(f"piece {i}: repeats footage {e['from']:.2f}-{e['to']:.2f} that another stretch shows at dub "
                             f"{f['t0']:.1f}-{f['t1']:.1f} s ({max(gap, 0):.1f} s away); a repeat needs {REUSE_GAP:.0f} s in between")
    if prev_last and edl and int(edl[0]["shot"]) == int(prev_last["shot"]) and abs(edl[0]["from"] - prev_last["to"]) > 0.3:
        probs.append(f"piece 0: the stretch before ends on shot {prev_last['shot']} at source {prev_last['to']:.2f}; starting the same "
                     f"shot at {edl[0]['from']:.2f} is a jump cut: continue from {prev_last['to']:.2f} or start with another shot")
    return probs


def global_check(res):
    allp = sorted([dict(e, _w=k) for k, edl in enumerate(res) for e in edl], key=lambda e: e["t0"])
    auto_reuse(allp)
    probs = {k: [] for k in range(len(res))}
    for j, e in enumerate(allp):
        r = SH.get(int(e["shot"]), {})
        earlier = [f for f in allp[:j] if f["_w"] != e["_w"] and ov(f["from"], f["to"], e["from"], e["to"]) > 0.2]
        if not earlier: continue
        f = earlier[-1]
        if r.get("type") not in SHARED:
            probs[e["_w"]].append(f"the piece at dub {e['t0']:.1f} s shows {r.get('type')} footage {e['from']:.2f}-{e['to']:.2f} (shot {e['shot']}) "
                                  f"that is already shown at dub {f['t0']:.1f} s; action/talking footage is shown once")
        elif e["t0"] - f["t1"] < REUSE_GAP:
            probs[e["_w"]].append(f"the piece at dub {e['t0']:.1f} s repeats footage {e['from']:.2f}-{e['to']:.2f} (shot {e['shot']}) shown at dub "
                                  f"{f['t0']:.1f}-{f['t1']:.1f} s, only {e['t0'] - f['t1']:.1f} s before; a repeat needs {REUSE_GAP:.0f} s in between")
    for k in range(1, len(res)):
        if res[k] and res[k - 1]:
            a, b = res[k - 1][-1], res[k][0]
            if int(a["shot"]) == int(b["shot"]) and abs(b["from"] - a["to"]) > 0.3:
                probs[k].append(f"the stretch before ends on shot {a['shot']} at source {a['to']:.2f}; your first piece starts the same shot at "
                                f"{b['from']:.2f}: a jump cut. Continue from {a['to']:.2f} or start with another shot")
    return probs, allp


def run_par(A0, A1):
    t_start = time.time()
    wins = windows_par(A0, A1)
    info = [cands_for(T0, T1) for T0, T1 in wins]
    cand = [{r["n"] for r in x[2]} for x in info]; used = [x[1] for x in info]
    own = ownership(wins, cand, used)
    cache_p = OUTM.with_suffix(".par_cache.json"); cache = json.load(open(cache_p)) if cache_p.exists() else {}
    LINES = [[d for d in dub if d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1] for T0, T1 in wins]
    print(f"parallel: {len(wins)} stretches ({len({line_scene(d) for d in dub})} scenes), {PAR} calls at a time", flush=True)

    def base_content(k):
        T0, T1 = wins[k]; auto_w, _, shots = info[k]
        def auto_pc(s):
            return f"- {s['it_start_ms'] / 1000:.1f}-{s['it_end_ms'] / 1000:.1f} s source {s['us_start_ms'] / 1000:.2f}-{s['us_end_ms'] / 1000:.2f} (shot {next((r['n'] for r in LIB['shots'] if r['start'] <= (s['us_start_ms'] + s['us_end_ms']) / 2000 < r['end']), '?')})"
        before = [s for s in auto if s["it_end_ms"] <= T0 * 1000 + 1 and not s.get("video")][-3:]
        after = [s for s in auto if s["it_start_ms"] >= T1 * 1000 - 1 and not s.get("video")][:2]
        def stxt(r):
            t = shot_txt(r, auto_w)
            if r["type"] in SHARED: return t + " | SHARED shot: may be used (repeats are fine 8 s apart)"
            regs = own.get((r["n"], k))
            return t + (f" | footage for THIS stretch: {fmt_iv(regs)} (the rest of the shot belongs to other stretches)" if regs
                        else " | ALL its footage belongs to other stretches: do NOT use it")
        line_txt = "\n".join(f"line {d['idx']}: {d['start_ms'] / 1000:.2f}-{d['end_ms'] / 1000:.2f} s  "
                             f"{'DIALOGUE' if d['dialogue'] else 'narration'}: {d['text']}" for d in LINES[k])
        rule8 = (f"\n8. The other stretches of the episode are cut AT THE SAME TIME as this one. For talking / action / object shots use "
                 f"ONLY the footage marked 'for THIS stretch' (the rest is shown by other stretches). SHARED shots (reaction / "
                 f"neutral / establishing) may be used by any stretch; the software marks repeats.")
        head = (prompt(T0, T1) + rule8 + "\n\nDUB LINES (voice times):\n" + line_txt +
                "\n\nTHE AUTOMATIC EDIT'S PIECES JUST BEFORE THIS STRETCH (rough guide for a smooth join; the real cut there is made at the same time):\n" +
                ("\n".join(auto_pc(s) for s in before) or "- this is the start of the episode") +
                "\nAND JUST AFTER IT:\n" + ("\n".join(auto_pc(s) for s in after) or "- this is the end") +
                "\n\nSOURCE SHOTS:\n" + "\n".join(stxt(r) for r in shots) + "\n\nFrames of each shot (3 frames, left to right):")
        content = [{"type": "text", "text": head}]
        for r in shots:
            content += [{"type": "text", "text": f"shot {r['n']}:"}, {"type": "image_url", "image_url": {"url": b64(r["n"])}}]
        return content

    def run(k, tag, nr, extra0="", others=(), prev_last=None, prev_edl=None):
        T0, T1 = wins[k]; t = time.time()
        content = base_content(k); extra = extra0; rounds = []; edl = prev_edl or []
        for rnd in range(nr):
            key = f"par|{T0:.2f}-{T1:.2f}|{tag}{rnd}"
            with LOCK: ans = cache.get(key)
            if ans is None:
                c = content + ([{"type": "text", "text": extra}] if extra else [])
                ans = {}
                for attempt in range(3):
                    try:
                        ans = ai.extract_json(ask_claude(c), "edl") or {}
                        if ans.get("edl"): break
                    except Exception as ex:
                        print(f"  stretch {k} {T0:.1f}-{T1:.1f}: reply not readable ({str(ex)[:80]}), asking again", flush=True)
                with LOCK:
                    cache[key] = ans; json.dump(cache, open(cache_p, "w"), indent=1, ensure_ascii=False)
            new = parse_edl(ans)
            if new: edl = sorted(new, key=lambda e: e["t0"])
            probs = check_par(edl, T0, T1, LINES[k], k, own, others, prev_last)
            rounds.append(dict(round=len(rounds), stage=tag, problems=probs, notes=ans.get("notes", "")))
            print(f"  stretch {k:2d} {T0:6.1f}-{T1:6.1f} [{tag}{rnd}]: {len(edl)} pieces, {len(probs)} problems"
                  + (": " + " | ".join(p[:110] for p in probs[:3]) if probs else ""), flush=True)
            if not probs: break
            extra = (extra0 + "\n\n" if extra0 else "") + ("Your previous EDL:\n" + json.dumps(ans.get("edl"), ensure_ascii=False) +
                     "\n\nIt has these problems, fix them all and answer the complete corrected JSON again:\n- " + "\n- ".join(probs))
        return edl, rounds, time.time() - t

    with ThreadPoolExecutor(PAR) as ex:
        out = list(ex.map(lambda k: run(k, "a", 3), range(len(wins))))
    res = [o[0] for o in out]; rounds = [o[1] for o in out]; wsec = [o[2] for o in out]
    t_first = time.time() - t_start
    print(f"first pass: {len(wins)} stretches in {t_first:.0f} s", flush=True)

    fixes = []
    for g in range(2):
        gp, _ = global_check(res)
        bad = [k for k in gp if gp[k]]
        print(f"whole-episode check {g}: {sum(len(v) for v in gp.values())} problems in stretches {bad}", flush=True)
        for k in bad:
            for p in gp[k]: print(f"    stretch {k}: {p[:160]}", flush=True)
        fixes.append({str(k): gp[k] for k in bad})
        if not bad: break

        def fix(k):
            T0, T1 = wins[k]
            others = [e for kk in range(len(res)) if kk != k for e in res[kk]]
            shots_k = cand[k]
            pl = res[k - 1][-1] if k and res[k - 1] else None
            nf = res[k + 1][0] if k + 1 < len(res) and res[k + 1] else None
            shown = "\n".join(f"- shot {e['shot']} source {e['from']:.2f}-{e['to']:.2f} at dub {e['t0']:.1f}-{e['t1']:.1f} s"
                              for e in sorted(others, key=lambda e: e["t0"]) if int(e["shot"]) in shots_k) or "- none"
            extra0 = ("NOW THE OTHER STRETCHES ARE CUT.\nThe real piece just before this stretch: " +
                      (f"shot {pl['shot']} source {pl['from']:.2f}-{pl['to']:.2f} at dub {pl['t0']:.1f}-{pl['t1']:.1f} s ({pl.get('why', '')[:100]})" if pl else "none") +
                      "\nThe real piece just after it: " +
                      (f"shot {nf['shot']} source {nf['from']:.2f}-{nf['to']:.2f} at dub {nf['t0']:.1f}-{nf['t1']:.1f} s" if nf else "none") +
                      "\nFootage of these shots shown by the other stretches:\n" + shown +
                      "\n\nYour EDL for this stretch:\n" + json.dumps([{x: e[x] for x in ("t0", "t1", "shot", "from", "to", "line", "why") if x in e} for e in res[k]], ensure_ascii=False) +
                      "\n\nThe whole-episode check found these problems; fix them all (keep what is fine) and answer the complete corrected JSON:\n- " + "\n- ".join(gp[k]))
            return run(k, f"g{g}_", 3, extra0, others, pl, res[k])

        with ThreadPoolExecutor(PAR) as ex:
            fo = list(ex.map(fix, bad))
        for k, (edl, rr, sec) in zip(bad, fo):
            res[k] = edl; rounds[k] += rr; wsec[k] += sec
    gp, allp = global_check(res)
    left_global = sum(len(v) for v in gp.values())
    wall = time.time() - t_start
    report = [dict(window=list(wins[k]), scene=line_scene(LINES[k][0]) if LINES[k] else None, lines=LINES[k], shots=sorted(cand[k]),
                   edl=[e for e in allp if e["_w"] == k], rounds=rounds[k], seconds=round(wsec[k], 1)) for k in range(len(wins))]
    stats = dict(mode=f"parallel {PAR}", wall_seconds=round(wall), first_pass_seconds=round(t_first), usage=USAGE,
                 global_fixes=fixes, global_problems_left={str(k): v for k, v in gp.items() if v})
    return [dict(e) for e in allp], report, stats


if PAR:
    allE, report, stats = run_par(A0, A1)
    segs = []
    for e in allE:
        d = next((d for d in dub if d["start_ms"] / 1000 <= (e["t0"] + e["t1"]) / 2 <= d["end_ms"] / 1000), None)
        segs.append(dict(kind="clip", it_idx=e.get("line") if e.get("line") is not None else (d["idx"] if d else -1),
                         it_start_ms=round(e["t0"] * 1000, 3), it_end_ms=round(e["t1"] * 1000, 3),
                         us_start_ms=round(e["from"] * 1000, 3), us_end_ms=round(e["to"] * 1000, 3),
                         it_text=d["text"] if d else "", us_text="", stretch_factor=round((e["t1"] - e["t0"]) / max(e["to"] - e["from"], 1e-6), 3),
                         note="scene edit: " + e.get("why", "")[:200], **({"zoom": 1.08} if e.get("reuse") else {})))
    json.dump(dict(total_ms=round(A1 * 1000), segments=segs, bleed_report=[], groups=[]), open(OUTM, "w"), indent=1, ensure_ascii=False)
    json.dump(dict(range=[A0, A1], windows=report, stats=stats), open(OUTM.with_suffix(".edl.json"), "w"), indent=1, ensure_ascii=False)
    left = sum(len(w["rounds"][-1]["problems"]) for w in report)
    print(f"saved {OUTM} {len(segs)} pieces in {len(report)} stretches; problems left in stretches: {left}, whole-episode: "
          f"{sum(len(v) for v in stats['global_problems_left'].values())}; wall {stats['wall_seconds']} s; Claude calls {USAGE['calls']}, "
          f"tokens in {USAGE['prompt_tokens']} out {USAGE['completion_tokens']}", flush=True)
    sys.exit(0)


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
