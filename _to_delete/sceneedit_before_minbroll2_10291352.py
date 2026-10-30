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
# dialogue = a line with a quote mark OR inside a quote that an earlier sentence opened (owner 2026-10-28: "Sei doch nicht
# so paranoid! | Das Fitnessstudio gehoert ... | Niemand wird uns ... | Komm schon ...“" is ONE quote over 4 sentences).
# German opens with „ and closes with “; other languages open with “ and close with ”; «»; a straight " toggles.
_de = any("„" in d["text"] for d in dub)
_OPEN, _CLOSE = ("„«", "“”»") if _de else ("“«", "”»")
_inside, _run = False, 0
for d in dub:
    has = bool(QUOTE.search(d["text"]))
    d["dialogue"] = has or _inside
    _run = 0 if has else _run + 1
    if _inside and _run > 4: _inside = False; d["dialogue"] = has      # a quote never closed: stop after 4 sentences
    for ch in d["text"]:
        if ch in _OPEN: _inside = True
        elif ch in _CLOSE: _inside = False
        elif ch == '"': _inside = not _inside
# LOCATION GUARD (owner 2026-10-28): a piece shows only the background of its line -- a shot of the same scene, or a
# reaction/neutral shot of the SAME location elsewhere in the episode; never another background. The library gives every
# shot a location (place descriptions grouped by Claude; "mixed" = flashback/montage); a line's location = the location of
# the footage the automatic match gives it.
HAS_LOC = any("location" in r for r in LIB["shots"])


def line_auto(d):
    return [(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000) for s in auto
            if not s.get("video") and ov(d["start_ms"], d["end_ms"], s["it_start_ms"], s["it_end_ms"]) > 0]


def line_loc(d):
    tot = {}
    for a, b in d["_auto"]:
        for r in LIB["shots"]:
            o = ov(a, b, r["start"], r["end"])
            if o > 0 and r.get("location") and r["location"] != "mixed": tot[r["location"]] = tot.get(r["location"], 0) + o
    return max(tot, key=tot.get) if tot else None


def line_places():
    """Claude reads the story once (text only): for every dub line the source scene it plays in and the location there.
    The automatic match is not enough for this: where it stretched one shot over several lines (TGS ep32 lines 99-106,
    still in the gym, got a parking-garage shot) it would give the wrong place. Cached in W/line_places.json."""
    cp = W / "line_places.json"; key = f"{len(dub)}|{len(LIB['shots'])}|{LIB['shots'][-1].get('scene')}|{dub[-1]['text'][:30]}"
    if cp.exists():
        c = json.load(open(cp))
        if c.get("key") == key: return c["lines"]
    scenes = []
    for r in LIB["shots"]:
        if not scenes or scenes[-1]["name"] != r["scene"]: scenes.append(dict(name=r["scene"], start=r["start"], end=r["end"], locs=[]))
        scenes[-1]["end"] = r["end"]
        if r.get("location") and r["location"] not in scenes[-1]["locs"]: scenes[-1]["locs"].append(r["location"])
    stxt = "\n".join(f"scene {i}: {sc['name']} | locations: {', '.join(sc['locs'])} | source narration: \""
                     + " ".join(x["text"] for x in src if sc["start"] <= x["start_ms"] / 1000 < sc["end"])[:900] + "\"" for i, sc in enumerate(scenes))
    q = ("A dubbed episode tells the same story as the source video, in the same order (lines may be reworded, added or cut). "
         "For EVERY dub line, say in which source scene it plays and at which of that scene's locations (the background on "
         "screen at that moment of the story). A line told as a memory / flashback gets the location \"mixed\". Answer only "
         "JSON: {\"lines\": [[dub line number, scene number, \"location\"], ...]} with one entry per dub line, in order.\n\n"
         "SOURCE SCENES, in order:\n" + stxt + "\n\nDUB LINES:\n" + "\n".join(f"{d['idx']}: {d['text']}" for d in dub))
    for attempt in range(3):
        try:
            text, _ = ai.chat(q, model=MODEL or None, max_tokens=16000)
            res = ai.extract_json(text, "lines")["lines"]
            if len(res) >= 0.9 * len(dub): break
        except Exception as ex:
            print("line places: reply not readable, asking again", str(ex)[:80], flush=True); res = []
    out = {}
    for row in res:
        try:
            a, b, c = int(row[0]), int(row[1]), str(row[2])
        except Exception:
            continue
        if not 0 <= b < len(scenes): continue
        sc = scenes[b]
        if c not in sc["locs"] and c != "mixed": c = sc["locs"][0] if sc["locs"] else c
        out[str(a)] = dict(scene=sc["name"], loc=c)
    json.dump(dict(key=key, lines=out), open(cp, "w"), indent=1, ensure_ascii=False)
    return out


PLACES = line_places() if HAS_LOC else {}
for d in dub:
    d["_auto"] = line_auto(d); pl = PLACES.get(str(d["idx"]))
    d["_near"] = [iv for x in dub if abs(x["idx"] - d["idx"]) <= 1 for iv in line_auto(x)]
    d["scene"], d["loc"] = (pl["scene"], pl["loc"]) if pl else (None, line_loc(d) if HAS_LOC else None)
NARR_MOUTH = 0.2          # owner 2026-10-28: NEVER a moving mouth while narration plays (more than 0.2 s of it = a problem)


def mouth_in(a, b):
    """seconds of moving mouth (lip scan) in source a-b"""
    return sum(ov(x, y, a, b) for x, y in lip_iv)


def eff_type(e, r):
    """footage with a moving mouth is talking footage (shown once), whatever the library calls the shot"""
    return "talking" if mouth_in(e["from"], e["to"]) > 0.3 else r["type"]


def narration_mouth(e, lines):
    """[(line, src a, src b, mouth s)] where this piece shows a moving mouth while a narration line is heard"""
    sp = (e["to"] - e["from"]) / max(e["t1"] - e["t0"], 1e-6); out = []
    for d in lines:
        if d["dialogue"]: continue
        a, b = max(e["t0"], d["start_ms"] / 1000), min(e["t1"], d["end_ms"] / 1000)
        if b - a <= 0.05: continue
        sa, sb = e["from"] + (a - e["t0"]) * sp, e["from"] + (b - e["t0"]) * sp
        m = mouth_in(sa, sb)
        if m > NARR_MOUTH: out.append((d, sa, sb, m))
    return out


def windows(a0, a1):
    """cut points in the pauses between lines, about every WIN s"""
    gaps = [(dub[i]["end_ms"] / 1000 + dub[i + 1]["start_ms"] / 1000) / 2 for i in range(len(dub) - 1)
            if dub[i]["end_ms"] / 1000 > a0 and dub[i + 1]["start_ms"] / 1000 < a1 and not same_grp(dub[i], dub[i + 1])]
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
            f"{r['type']}{' (talking: ' + ', '.join(talkers(r['n'])) + ')' if talkers(r['n']) else ''} | {r['action']} | mouth moving at: {', '.join(f'{a:.2f}-{b:.2f}' for a, b in lips) or 'never'} | "
            f"source narration over it: \"{said[:160]}\"" + (f" | the automatic match used it at dub {', '.join(auto_at)}" if auto_at else "")
            + (f" | LOCATION: {r.get('location')} (scene: {r['scene']})" if HAS_LOC else ""))


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
   A B-roll piece over narration NEVER shows a moving mouth (see "mouth moving at" of every shot, reaction shots too):
   use only seconds where nobody's mouth moves. The line's own footage stays as matched.
   An action (walking to the door, turning round, picking something up) is shown when the voice tells it, not earlier, once.
5. Before a dialogue line, show what leads to it (e.g. the person being called walking away, then the caller says "wait").
6. Never show an event before the voice has told it. Action and talking footage is shown ONCE in the whole episode (see the
   footage already used below). Only reaction / neutral shots (a face listening, looking) may be shown again, at least
   {REUSE_GAP:.0f} s after the last time, with "reuse": true.
7. Prefer longer pieces and few cuts; follow the story order of the footage where you can; continue smoothly from the
   pieces just before this stretch.
8. LOCATION: every dub line is marked with the location (background) where it plays. A B-roll piece shows ONLY that place: a
   shot of the line's scene (not a flashback shot), or a reaction / neutral shot of the SAME location from elsewhere in the
   episode. Never a shot with another background (another room, another scene's place, a flashback).
9. B-ROLL ONLY FOR SLOW LINES (hard rule): every line is marked with its OWN footage (what the automatic match plays during
   it) and its speed. Show each line with its own footage (or its neighbours' own footage, e.g. for lip timing or the
   story order). Lines marked GROUP share their own footage: place it in STORY ORDER inside the group -- an action
   (e.g. shoving the phone in his face) goes on the line that TELLS it, never on an earlier line of the group; the other
   lines of the group show its reaction / neutral footage. Only a SLOW line (own footage below 0.8x) may add B-roll, and only for the seconds given; use its own
   footage first (down to 0.8x). A FAST line (above 1.25x) shows a shorter part of its own footage (trim) -- never B-roll.
   A normal line (0.8-1.25x) never gets B-roll. B-roll = a reaction / neutral / establishing shot, never action or talking;
   only on a NOT FOUND (gap) line may it also be the action / object shot that shows exactly what that line tells.
   Every B-roll piece lasts at least 1.3 s (shorter cutaways look jumpy). A slow line that needs less than 1.3 s of
   B-roll takes either one 1.3-s B-roll (its own footage plays faster) or none (its own footage plays slower, down to 0.65x).
10. SPEAKER: a moving mouth during a dialogue line must be the mouth of the person who SAYS it (every dialogue line says
   who; every talking shot says who talks). Never show another person talking over it. A WRONG-SPEAKER line (its matched
   footage shows the wrong person talking) is shown with its speaker talking -- a SPEAKER shot of the same location, the
   mouth starting with the voice; it may repeat footage shown elsewhere in the episode -- or a face that does not talk.

Answer ONLY JSON: {{"edl": [{{"t0": s, "t1": s, "shot": n, "from": s, "to": s, "line": dub line number or null, "reuse": false,
"why": "what the viewer sees and why here (at most 15 words)"}}], "notes": "anything that could not be done (at most 40 words)"}}
Keep the answer short: no text outside the JSON."""


def b64(n):
    return "data:image/jpeg;base64," + base64.b64encode(open(LIBD / "_frames" / f"shot_{n:03d}.jpg", "rb").read()).decode()


# B-ROLL ONLY FOR SLOW LINES (owner, 2026-10-28, hard rule): every line shows its OWN footage (what the automatic match
# plays during it; the neighbouring lines' footage is allowed too, for lip timing and story order). Only a line whose own
# footage would play slower than 0.8x (SLOW) may add B-roll, and only for the time its own footage cannot fill at 0.8x.
# A fast line (above 1.25x) is TRIMMED: a shorter part of its own footage, never B-roll. Normal lines: own footage only.
# The own footage is used as matched, with no exceptions (owner, 2026-10-28: the talking mouths over narration came from
# B-roll shots). The B-roll quality rules (reaction / neutral / establishing shot only, no moving mouth under narration,
# the line's place, repeats 8 s apart) apply to the B-roll pieces.
SLOW, OWN_FRAC, OWN_PAD = 0.8, 0.5, 0.5
# owner, 2026-10-29: a B-roll of a few hundred ms or 1 s looks jumpy. Every B-roll piece lasts at least MIN_BROLL; a slow
# line that needs less either takes one B-roll of MIN_BROLL (its own footage then plays faster, up to 1.25x) or no B-roll
# and its own footage a little slower, down to SLOW_FLOOR.
MIN_BROLL, SLOW_FLOOR = 1.3, 0.65


def merge_iv(iv):
    out = []
    for a, b in sorted(tuple(x) for x in iv):
        if out and a <= out[-1][1]: out[-1][1] = max(out[-1][1], b)
        else: out.append([a, b])
    return out


def own_line(d):
    """(source intervals, seconds) the automatic match plays during this line = its own footage"""
    iv, sec = [], 0.0
    for s in auto:
        if s.get("video"): continue
        o0, o1 = max(d["start_ms"], s["it_start_ms"]), min(d["end_ms"], s["it_end_ms"])
        if o1 - o0 <= 0: continue
        k = (s["us_end_ms"] - s["us_start_ms"]) / max(s["it_end_ms"] - s["it_start_ms"], 1)
        a = (s["us_start_ms"] + (o0 - s["it_start_ms"]) * k) / 1000; b = (s["us_start_ms"] + (o1 - s["it_start_ms"]) * k) / 1000
        iv.append((a, b)); sec += b - a
    return merge_iv(iv), sec


def load_gaps():
    """dub rows Step 2 left without a source line (decisions.json "gap"): they have NO own footage -- the automatic edit
    only stretched a neighbour over them -- so they are covered with B-roll only (owner, 2026-10-29: an added event like
    "she smashed a coffee cup against the wall" must never show another line's footage, e.g. Ava saying "liar")."""
    for p_ in (W, W.parent, W.parent.parent):
        f = p_ / "decisions.json"
        if f.exists():
            try: return {int(k) for k in json.load(open(f)).get("gap", {})}
            except Exception: return set()
    return set()


GAPS = load_gaps()
for d in dub:
    d["_own"], _os = own_line(d); _dur = max((d["end_ms"] - d["start_ms"]) / 1000, 1e-6)
    d["gap"] = d["idx"] in GAPS
    if d["gap"]: d["_own"], _os, d["_near"] = [], 0.0, []
    d["own_speed"] = round(_os / _dur, 2); d["slow"] = d["own_speed"] < SLOW
    d["broll_budget"] = round(max(0.0, _dur - _os / SLOW), 2) if d["slow"] else 0.0

# SPEAKER (owner, 2026-10-29): ep34 0:01 -- Paul says "Anya, bitte! Das ist nicht das, wonach es aussieht!" but the matched
# footage showed Anya talking (the source line "If it's not what it looks like..." is hers), although Paul's talking
# footage of the same office exists. A moving mouth during a dialogue line must be its SPEAKER's mouth. Claude names once
# (text only, cached in W/speakers.json) who says every dub dialogue line and who talks in every source shot with a moving
# mouth. A dialogue line whose own footage shows someone else talking is a WRONG-SPEAKER line ("swap"): that footage is no
# longer its own; it is shown with a talking shot of its speaker at the same location (a SPEAKER shot -- talking footage
# shown elsewhere in the episode may be repeated for it, at least SPK_GAP s away, zoomed) or a face that does not talk.
SPK_GAP = 30.0
NAMES = [c["name"] for c in cast]


def speakers():
    cp = W / "speakers.json"
    talk = [r for r in LIB["shots"] if r["type"] == "talking" or mouth_in(r["start"], r["end"]) > 0.3]
    key = f"v2|{len(dub)}|{len(talk)}|{len(LIB['shots'])}|{dub[-1]['text'][:30]}"
    if cp.exists():
        c = json.load(open(cp))
        if c.get("key") == key: return c

    def said(r):
        return " ".join(x["text"] for x in src if ov(x["start_ms"] / 1000, x["end_ms"] / 1000, r["start"], r["end"]) > 0.1)[:220]
    q = ("A dubbed episode (another language; the dub may use other names for the same people) tells the story of a source "
         f"video. Characters (source names): {', '.join(NAMES)}.\n"
         "1. For EVERY dub line marked DIALOGUE say who SAYS it out loud: a source name from the list, \"other\" for someone not "
         "in the list, \"narration\" if it is really the narrator (or a thought, not spoken), \"unclear\" if the story does not tell.\n"
         "2. For EVERY source shot below say whose mouth TALKS in it (a list of source names, [] if you cannot tell), from the "
         "shot's description and the words heard over it.\n"
         "Answer only JSON: {\"lines\": [[dub line number, \"name\"], ...], \"shots\": [[shot number, [\"name\", ...]], ...]}\n\n"
         "DUB LINES:\n" + "\n".join(f"{d['idx']}: {'DIALOGUE' if d['dialogue'] else 'narration'}: {d['text']}" for d in dub) +
         "\n\nSOURCE SHOTS:\n" + "\n".join(f"shot {r['n']}: {', '.join(r['people']) or 'nobody'} | {r['type']} | {r['action']} | "
                                          f"words heard: \"{said(r)}\"" for r in talk))
    res = {}
    for attempt in range(3):
        try:
            text, _ = ai.chat(q, model=MODEL or None, max_tokens=16000)
            res = ai.extract_json(text, "lines") or {}
            if res.get("lines"): break
        except Exception as ex:
            print("speakers: reply not readable, asking again", str(ex)[:80], flush=True)
    low = {n.lower(): n for n in NAMES}
    lines, shots = {}, {}
    for row in res.get("lines") or []:
        try: lines[str(int(row[0]))] = low.get(str(row[1]).strip().lower(), str(row[1]))
        except Exception: pass
    for row in res.get("shots") or []:
        try:
            v = row[1] if isinstance(row[1], list) else [row[1]]
            shots[str(int(row[0]))] = [low[str(x).strip().lower()] for x in v if str(x).strip().lower() in low]
        except Exception: pass
    out = dict(key=key, lines=lines, shots=shots)
    if lines: json.dump(out, open(cp, "w"), indent=1, ensure_ascii=False)
    return out


def minus_iv(iv, cut):
    out = []
    for a, b in iv:
        segs = [(a, b)]
        for c0, c1 in cut:
            segs = [q for x, y in segs for q in ((x, min(y, c0)), (max(x, c1), y)) if q[1] - q[0] > 0.05]
        out += segs
    return out


SPK = speakers() if NAMES else dict(lines={}, shots={})
SHOT_SPK = {int(k): v for k, v in SPK.get("shots", {}).items()}
talkers = lambda n: SHOT_SPK.get(int(n), [])
WRONG = []
for d in dub:
    sp_ = SPK["lines"].get(str(d["idx"])) if d["dialogue"] else None
    d["speaker"] = sp_ if sp_ in NAMES else None
    d["swap"], d["_wrong"] = False, []
    if not d["speaker"] or d["gap"]: continue
    for a, b in d["_own"]:
        for r in LIB["shots"]:
            o0, o1 = max(a, r["start"]), min(b, r["end"])
            if o1 - o0 > 0.05 and mouth_in(o0, o1) > min(0.3, 0.5 * (o1 - o0)) and talkers(r["n"]) and d["speaker"] not in talkers(r["n"]):
                WRONG.append((o0, o1)); d["swap"] = True; d["_wrong"].append((r["n"], round(o0, 2), round(o1, 2), talkers(r["n"])))
for d in dub:
    d["_near"] = minus_iv(d["_near"], WRONG)
    if d["swap"]:
        d["_own"] = merge_iv(minus_iv(d["_own"], WRONG)); _os = sum(b - a for a, b in d["_own"])
        _dur = max((d["end_ms"] - d["start_ms"]) / 1000, 1e-6)
        d["own_speed"] = round(_os / _dur, 2); d["slow"] = d["own_speed"] < SLOW
        d["broll_budget"] = round(max(0.0, _dur - _os / SLOW), 2) if d["slow"] else 0.0
print(f"speakers: {sum(1 for d in dub if d['speaker'])} dialogue lines with a speaker; wrong-speaker lines: "
      + (", ".join(f"{d['idx']} ({d['speaker']}; footage shows {'/'.join(d['_wrong'][0][3])})" for d in dub if d["swap"]) or "none"), flush=True)


# GAP PLAN (owner, 2026-10-29): a NOT FOUND line is not "B-roll only, never a mouth". Claude reads the story and the
# B-roll book (every shot of the line's place: who, type, who talks, what happens) and decides per line WHO must be on
# screen and HOW: "talking" (the line is said out loud by a character on screen -> that character's lip clip, e.g. ep34
# 0:00 Paul's "Anya, bitte!"), "reaction" (a face: whose), "action" (the shot of exactly the event the line tells) or
# "neutral" (the place / a person standing). Cached in W/gap_plan.json; the scene edit follows it and code checks that
# the planned person is on screen and that a talking line starts on that person's moving mouth.
def gap_plan():
    gl = [d for d in dub if d["gap"]]
    if not gl: return {}
    cp = W / "gap_plan.json"; key = f"v1|{len(dub)}|{len(LIB['shots'])}|" + ",".join(str(d["idx"]) for d in gl)
    if cp.exists():
        c = json.load(open(cp))
        if c.get("key") == key: return c["lines"]
    locs = {d.get("loc") for d in gl}; scs = {d.get("scene") for d in gl}
    shots = [r for r in LIB["shots"] if r.get("location") in locs or r["scene"] in scs]

    def shot_line(r):
        m = mouth_in(r["start"], r["end"])
        return (f"shot {r['n']}: {r['start']:.1f}-{r['end']:.1f} s | {', '.join(r['people']) or 'nobody'} | {r['type']}"
                f"{' (talking: ' + ', '.join(talkers(r['n'])) + ')' if talkers(r['n']) else ''} | mouth moving {m:.1f} s | {r['action']} | "
                f"place: {r.get('location')} | scene: {r['scene']}")

    def ctx(d):
        i = dub.index(d)
        return " / ".join(f"[{x['idx']}] {x['text']}" for x in dub[max(0, i - 3):i + 4])
    q = (f"A dubbed episode retells the story of a source video. Characters (source names; the dub may call them differently): "
         f"{'; '.join(c['name'] + ': ' + c['look'][:120] for c in cast)}.\n"
         "The dub lines below have NO matching source line (the adaptation added them), so the picture must be chosen from the "
         "story. For EACH line decide like a film editor what the viewer must see while it is heard:\n"
         "- show \"talking\": the line is spoken out loud by a character who is in the scene -> that character's lip clip "
         "(a shot where THAT character talks, at the same place). Never another character talking.\n"
         "- show \"reaction\": a face listening / reacting (say whose: the person the line is about, or the listener).\n"
         "- show \"action\": the shot of exactly the event the line tells (an object, a movement, e.g. 'the spy in the car').\n"
         "- show \"neutral\": the place or a person standing, when nothing more exact fits.\n"
         "\"who\" = the source name of the person on screen (\"\" for none). \"shots\" = the best shots from the list, best "
         "first (at most 4, only shots at the line's place).\n"
         "Answer only JSON: {\"lines\": [{\"line\": n, \"show\": \"talking|reaction|action|neutral\", \"who\": \"name\", "
         "\"shots\": [n, ...], \"why\": \"at most 15 words\"}]}\n\nLINES WITHOUT A SOURCE LINE:\n" +
         "\n".join(f"line {d['idx']} ({'DIALOGUE' + (' said by ' + d['speaker'] if d.get('speaker') else '') if d['dialogue'] else 'narration'}; "
                   f"place: {d.get('loc')}; scene: {d.get('scene')}): \"{d['text']}\"\n   story around it: {ctx(d)}" for d in gl) +
         "\n\nSHOT LIST (the B-roll book for these places):\n" + "\n".join(shot_line(r) for r in shots))
    res = []
    for attempt in range(3):
        try:
            text, _ = ai.chat(q, model=MODEL or None, max_tokens=8000)
            res = (ai.extract_json(text, "lines") or {}).get("lines") or []
            if res: break
        except Exception as ex:
            print("gap plan: reply not readable, asking again", str(ex)[:80], flush=True)
    low = {n.lower(): n for n in NAMES}; out = {}
    for x in res:
        try:
            k = str(int(x["line"]))
            out[k] = dict(show=str(x.get("show", "neutral")).lower(), who=low.get(str(x.get("who", "")).strip().lower(), ""),
                          shots=[int(n) for n in (x.get("shots") or []) if int(n) in SH][:4], why=str(x.get("why", ""))[:120])
        except Exception:
            pass
    if out: json.dump(dict(key=key, lines=out), open(cp, "w"), indent=1, ensure_ascii=False)
    return out


GPLAN = gap_plan()
for d in dub:
    d["spk_ok"] = d["swap"]
    gp_ = GPLAN.get(str(d["idx"])) if d["gap"] else None
    if not gp_: continue
    d["gplan"] = gp_
    if gp_["show"] == "talking" and gp_["who"] and d["dialogue"]:
        d["speaker"] = gp_["who"]; d["spk_ok"] = True          # its speaker's lip clip (a SPEAKER shot)
print("gap plan: " + ("; ".join(f"{k}: {v['show']} {v['who']} {v['shots']}" for k, v in GPLAN.items()) or "no not-matched lines"), flush=True)


# STORY ORDER INSIDE A GROUP (owner, 2026-10-29, TGS ep34 0:05-0:28): Step 2 joins dub rows that tell the same source
# lines into one group (rows 3-6 = source S1-S4) and the automatic edit spreads that footage EVENLY over the rows, so
# "Ava shoved the photo in his face" (S1, the phone thrust) landed on row 3 ("over the past weeks she had belittled him")
# instead of row 5 ("she waved the phone wildly in front of his face") / row 6 ("but he recognized it at once"). In the
# scene edit the rows of a group SHARE the group's own footage: Claude places it in story order (each action on the row
# that tells it); speed and B-roll allowance are counted for the whole group; a stretch never cuts through a group.
def load_groups():
    for p_ in (W, W.parent, W.parent.parent):
        f = p_ / "decisions.json"
        if f.exists():
            try: return [[int(x) for x in g.get("members") or []] for g in json.load(open(f)).get("groups") or []]
            except Exception: return []
    return []


BYIDX = {d["idx"]: d for d in dub}
for _mem in load_groups():
    _ms = [BYIDX[i] for i in _mem if i in BYIDX and not BYIDX[i]["gap"]]
    if len(_ms) < 2: continue
    _pool = merge_iv([iv for d in _ms for iv in d["_own"]])
    _os = sum(b - a for a, b in _pool); _dur = sum((d["end_ms"] - d["start_ms"]) / 1000 for d in _ms)
    _sp = round(_os / max(_dur, 1e-6), 2); _slow = _sp < SLOW
    _bud = round(max(0.0, _dur - _os / SLOW), 2) if _slow else 0.0
    for d in _ms:
        d["_grp"], d["_gown"] = [x["idx"] for x in _ms], _pool
        d["_near"] = merge_iv(d["_near"] + _pool)
        d["own_speed"], d["slow"], d["broll_budget"] = _sp, _slow, _bud
same_grp = lambda a, b: bool(a.get("_grp")) and b["idx"] in a["_grp"]
print(f"groups: {sum(1 for d in dub if d.get('_grp') and d['_grp'][0] == d['idx'])} groups of lines share their footage", flush=True)


def spk_piece(e, r=None, L=None):
    """a talking shot of the speaker of a wrong-speaker line, played while that line is heard (a SPEAKER shot)"""
    r = r or SH.get(int(e["shot"]))
    if not r: return False
    L = piece_lines(e) if L is None else L
    sw = [d for d in L if d.get("spk_ok")]
    return bool(sw) and all(d["speaker"] in talkers(r["n"]) for d in sw)


def wrong_mouth(e, r, lines):
    """[(line, talkers)] where this piece shows someone else talking while a dialogue line is said"""
    out = []
    if not talkers(r["n"]): return out
    sp = (e["to"] - e["from"]) / max(e["t1"] - e["t0"], 1e-6)
    for d in lines:
        if not d.get("speaker"): continue
        a, b = max(e["t0"], d["start_ms"] / 1000), min(e["t1"], d["end_ms"] / 1000)
        if b - a <= 0.1: continue
        sa, sb = e["from"] + (a - e["t0"]) * sp, e["from"] + (b - e["t0"]) * sp
        if mouth_in(sa, sb) > min(0.3, 0.5 * (sb - sa)) and d["speaker"] not in talkers(r["n"]): out.append((d, talkers(r["n"])))
    return out


def line_before(t):
    return next((d for d in reversed(dub) if d["end_ms"] / 1000 <= t + 0.05), None)


def line_after(t):
    return next((d for d in dub if d["start_ms"] / 1000 >= t - 0.05), None)


def piece_lines(e):
    L = [d for d in dub if ov(e["t0"], e["t1"], d["start_ms"] / 1000, d["end_ms"] / 1000) > 0.1]
    return L or [d for d in (line_before(e["t0"]), line_after(e["t1"])) if d]          # a pause: the lines around it


def is_broll(e, L):
    """the piece shows footage that is not the own footage of these lines or their neighbours"""
    own = merge_iv([(a - OWN_PAD, b + OWN_PAD) for d in L for a, b in d["_near"]])
    return sum(ov(a, b, e["from"], e["to"]) for a, b in own) < OWN_FRAC * (e["to"] - e["from"])


def line_tag(d):
    if d.get("swap"):
        w = d["_wrong"][0]; own = fmt_iv0(d["_own"])
        return (f" [WRONG-SPEAKER: said by {d['speaker']}, but its matched footage (shot {w[0]}, {w[1]:.2f}-{w[2]:.2f}) shows "
                f"{'/'.join(w[3])} talking -- never show that here. Show {d['speaker']} talking: a SPEAKER shot of {d['speaker']} at "
                f"this location (talking: {d['speaker']}), the mouth starting with the voice; or a face that does not talk"
                + (f"; its other own footage {own} may stay" if d["_own"] else "") + "]")
    if d.get("gap") and d.get("gplan"):
        gp_ = d["gplan"]; dur = (d["end_ms"] - d["start_ms"]) / 1000; sh = ", ".join(str(n) for n in gp_["shots"]) or "-"
        if d.get("spk_ok"):
            how = (f"show {gp_['who']} TALKING: a SPEAKER shot of {gp_['who']} at this place (suggested shots {sh}), the mouth starting "
                   f"with the voice; it may repeat talking footage shown elsewhere ({SPK_GAP:.0f} s apart)")
        elif gp_["show"] == "action":
            how = f"show exactly the event it tells (suggested shots {sh}), no talking mouth"
        else:
            how = f"show {gp_['who'] or 'the place'} ({gp_['show']}, suggested shots {sh}), no talking mouth"
        return (f" [NOT FOUND (no source line), story plan: {how} -- {gp_['why'].rstrip('.')}. Whole line {dur:.1f} s; never another line's "
                f"matched footage]")
    if d.get("gap"):
        return (f" [NO source line (gap): B-roll only, for the whole line ({(d['end_ms'] - d['start_ms']) / 1000:.1f} s) -- a reaction / "
                f"neutral shot of this place, or the action / object shot that shows exactly what this line tells (e.g. 'the spy in "
                f"the car' -> the spy shot); never a talking mouth, never another line's matched footage]")
    own = fmt_iv0([(a, b) for a, b in (d.get("_gown") or d["_own"])])
    g = d.get("_grp")
    if g:
        pre = (f"GROUP of lines {g[0]}-{g[-1]}: they SHARE their own footage {own} -- place it in STORY ORDER inside the group "
               f"(each action / event on the line that tells it: compare the source narration over each shot with the lines), "
               f"not spread evenly; the group's other lines show its reaction / neutral footage. Group ")
        if d["slow"]:
            return (f" [{pre}speed {d['own_speed']}x SLOW: own footage first (down to 0.8x) + B-roll for at most "
                    f"{d['broll_budget']:.1f} s over the WHOLE group, each B-roll piece at least {MIN_BROLL} s]")
        return f" [{pre}speed {d['own_speed']}x: ONLY the group's own footage{' (trim it: too fast)' if d['own_speed'] > 1.25 else ''}]"
    if d["slow"] and d["broll_budget"] < MIN_BROLL:
        return (f" [own footage {own} = {d['own_speed']}x SLOW, needs only {d['broll_budget']:.1f} s: EITHER one B-roll of at least "
                f"{MIN_BROLL} s (own footage then faster, up to {SPEED_HI}x) OR no B-roll and own footage slower, down to {SLOW_FLOOR}x]")
    if d["slow"]:
        return (f" [own footage {own} = {d['own_speed']}x SLOW: show it (down to 0.8x) + B-roll for at most {d['broll_budget']:.1f} s, "
                f"each B-roll piece at least {MIN_BROLL} s]")
    return f" [own footage {own} = {d['own_speed']}x: ONLY its own footage{' (trim it: too fast)' if d['own_speed'] > 1.25 else ''}]"


fmt_iv0 = lambda iv: ", ".join(f"{a:.2f}-{b:.2f}" for a, b in iv) or "never"


def check(edl, T0, T1, lines, before):
    probs = []
    if not edl: return ["empty EDL"]
    if abs(edl[0]["t0"] - T0) > 0.02: probs.append(f"first t0 must be {T0}")
    if abs(edl[-1]["t1"] - T1) > 0.02: probs.append(f"last t1 must be {T1}")
    seen = [(e["from"], e["to"], e["t0"], e["t1"], f"earlier piece at dub {e['t0']:.1f} s", SH.get(int(e["shot"]), {}).get("type"), spk_piece(e)) for e in before]
    for k, e in enumerate(edl):
        r = SH.get(int(e["shot"]))
        d = e["t1"] - e["t0"]; u = e["to"] - e["from"]
        if k and abs(e["t0"] - edl[k - 1]["t1"]) > 0.02: probs.append(f"piece {k}: t0 {e['t0']} != previous t1 {edl[k - 1]['t1']}")
        if not r: probs.append(f"piece {k}: shot {e['shot']} is not in the list"); continue
        if e["from"] < r["start"] - 0.05 or e["to"] > r["end"] + 0.05 or u <= 0:
            probs.append(f"piece {k}: from/to {e['from']}-{e['to']} not inside shot {r['n']} ({r['start']:.2f}-{r['end']:.2f})")
        L = piece_lines(e); BR = bool(L) and is_broll(e, L)
        lo = SLOW_FLOOR if (not BR and L and all(x["slow"] and not x.get("gap") for x in L)) else SPEED_LO
        if d < MIN_PIECE - 0.01: probs.append(f"piece {k}: {d:.2f} s is shorter than {MIN_PIECE} s")
        elif u > 0 and not (lo - 0.01 <= u / d <= SPEED_HI + 0.01):
            probs.append(f"piece {k}: speed {u / d:.2f} outside {lo}-{SPEED_HI} (footage {u:.2f} s on {d:.2f} s)")
        if BR and d < MIN_BROLL - 0.01:
            probs.append(f"piece {k}: B-roll of only {d:.2f} s looks jumpy; a B-roll lasts at least {MIN_BROLL} s -- make it longer (the "
                         f"line's own footage may play faster, up to {SPEED_HI}x) or leave it out and let the own footage play slower (down to {SLOW_FLOOR}x)")
        SPKP = BR and spk_piece(e, r, L)
        for a, b, t0_, t1_, name, typ, sk in reversed(seen):
            if ov(a, b, e["from"], e["to"]) > 0.2:
                if SPKP or sk:
                    if abs(e["t0"] - t1_) < SPK_GAP:
                        probs.append(f"piece {k}: repeats talking footage {e['from']:.2f}-{e['to']:.2f} only {abs(e['t0'] - t1_):.1f} s after "
                                     f"{name}; a SPEAKER shot may repeat footage only {SPK_GAP:.0f} s away")
                elif eff_type(e, r) not in ("reaction", "neutral", "establishing"):
                    probs.append(f"piece {k}: shot {r['n']} is a {eff_type(e, r)} shot{' (a mouth moves in this footage)' if eff_type(e, r) != r['type'] else ''} and its footage {e['from']:.2f}-{e['to']:.2f} is already "
                                 f"shown ({name}); action/talking footage is shown once: use other footage or let pieces run slower (down to {SPEED_LO})")
                elif not e.get("reuse"):
                    probs.append(f"piece {k}: footage already shown ({name}); mark reuse or use other footage")
                elif e["t0"] - t1_ < REUSE_GAP:
                    probs.append(f"piece {k}: repeats footage {e['t0'] - t1_:.1f} s after it was shown ({name}); a repeat needs {REUSE_GAP:.0f} s in between")
                break
        seen.append((e["from"], e["to"], e["t0"], e["t1"], f"piece {k}", r["type"], SPKP))
        gap_only = bool(L) and all(x.get("gap") for x in L)        # owner 2026-10-29: a NOT FOUND line may show the shot of
        if BR and not SPKP and (eff_type(e, r) == "talking" or                    # exactly what it tells (an action / object, e.g. the spy)
                   (eff_type(e, r) not in ("reaction", "neutral", "establishing") and not gap_only)):
            probs.append(f"piece {k}: B-roll must be a reaction / neutral / establishing shot without a moving mouth; shot {r['n']} "
                         f"source {e['from']:.2f}-{e['to']:.2f} is {eff_type(e, r)} footage")
        for d in (lines if BR else []):
            if not d.get("loc") or ov(e["t0"], e["t1"], d["start_ms"] / 1000, d["end_ms"] / 1000) < 0.1: continue
            if r.get("location") == d["loc"] or (r["scene"] == d.get("scene") and (r.get("location") != "mixed" or d["loc"] == "mixed")): continue
            if r["scene"] == d.get("scene") and any(ov(a, b, e["from"], e["to"]) > 0.2 for a, b in d["_near"]): continue   # a shot with a
            # flashback cut inside (place "gym (with a flashback ...)") is fine where the automatic match put that footage
            probs.append(f"piece {k}: shot {r['n']} is at location \"{r.get('location')}\" but line {d['idx']} plays at \"{d['loc']}\"; "
                         f"show only the line's location (same scene, or a reaction/neutral shot of the same location) -- never another background")
            break
        if BR and not any(d["broll_budget"] > 0 for d in L):
            probs.append(f"piece {k}: shot {r['n']} source {e['from']:.2f}-{e['to']:.2f} is B-roll (not the own footage of line(s) "
                         f"{', '.join(str(d['idx']) for d in L)}), but {'that line is' if len(L) == 1 else 'those lines are'} not slow "
                         f"({', '.join(str(d['own_speed']) + 'x' for d in L)}): B-roll is ONLY for slow lines (below 0.8x); show the line's own "
                         f"footage ({'; '.join(fmt_iv0(d['_own']) for d in L)}), a shorter part of it if it is too fast")
        for d, who in wrong_mouth(e, r, lines):
            probs.append(f"piece {k}: shot {r['n']} shows {'/'.join(who)} talking while dialogue line {d['idx']} is said by {d['speaker']}; "
                         f"a moving mouth must be the speaker's: show {d['speaker']} talking (a SPEAKER shot of this place) or a face that does not talk")
        for d, sa, sb, m in (narration_mouth(e, lines) if BR else []):
            probs.append(f"piece {k}: shot {r['n']} shows a moving mouth ({m:.1f} s in source {sa:.2f}-{sb:.2f}; mouth moving at "
                         f"{fmt_iv0([(max(x, e['from']), min(y, e['to'])) for x, y in lip_iv if ov(x, y, e['from'], e['to']) > 0.05])}) while "
                         f"narration line {d['idx']} is heard; never a talking mouth over narration: use footage where nobody's mouth moves")
    for d in lines:                                 # a not-matched line shows the person its story plan names
        gp_ = d.get("gplan")
        if not gp_ or not gp_["who"] or not (T0 <= (d["start_ms"] + d["end_ms"]) / 2000 < T1): continue
        a0, a1 = d["start_ms"] / 1000, d["end_ms"] / 1000
        on = sum(ov(e["t0"], e["t1"], a0, a1) for e in edl if gp_["who"] in SH.get(int(e["shot"]), {}).get("people", []))
        if on < 0.5 * (a1 - a0):
            probs.append(f"not-matched line {d['idx']} \"{d['text'][:40]}\": its story plan shows {gp_['who']} ({gp_['show']}, suggested "
                         f"shots {gp_['shots']}), but {gp_['who']} is on screen only {on:.1f} of {a1 - a0:.1f} s")
    done_g = set()
    for d in lines:                                 # a slow line (or group): own footage first, B-roll only for the rest
        if d["broll_budget"] <= 0 or not (T0 <= (d["start_ms"] + d["end_ms"]) / 2000 < T1): continue
        grp = tuple(d.get("_grp") or [d["idx"]])
        if grp in done_g: continue
        done_g.add(grp); ms = [x for x in lines if x["idx"] in grp] or [d]
        used = sum(ov(e["t0"], e["t1"], x["start_ms"] / 1000, x["end_ms"] / 1000) for x in ms for e in edl if is_broll(e, [x]))
        if used > max(d["broll_budget"], MIN_BROLL) + 0.75:
            who = f"lines {grp[0]}-{grp[-1]} (one group)" if len(grp) > 1 else f"line {d['idx']}"
            probs.append(f"{who} show {used:.1f} s of B-roll but need at most {d['broll_budget']:.1f} s (own footage "
                         f"{fmt_iv0(d.get('_gown') or d['_own'])} plays at {d['own_speed']}x): show more of the own footage, down to 0.8x, before B-roll")
    for d in lines:                                 # dialogue: a moving mouth must start within LIP_TOL of the voice
        if not d["dialogue"] or (d.get("gap") and not d.get("spk_ok")): continue  # a gap line has no source footage of its speaker: B-roll only
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
    scenes |= {d["scene"] for d in dub if d.get("scene") and d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1}
    shots = [r for r in LIB["shots"] if r["scene"] in scenes and (r["start"] <= hi + 40 or r["broll"] == "yes")
             and (r.get("location") != "mixed" or any(ov(r["start"], r["end"], a, b) > 0.05 for a, b in used_src))]
    if HAS_LOC:
        locs = {d["loc"] for d in dub if d.get("loc") and d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1}
        mid = sum(a + b for a, b in used_src) / (2 * len(used_src))
        more = sorted((r for r in LIB["shots"] if r["scene"] not in scenes and r.get("location") in locs and r["broll"] == "yes"
                       and r["type"] in ("reaction", "neutral", "establishing")), key=lambda r: abs(r["start"] - mid))[:12]
        shots = sorted(shots + more, key=lambda r: r["n"])
    prev = before[-3:]
    used_txt = "\n".join(f"- shot {e['shot']} source {e['from']:.2f}-{e['to']:.2f} shown at dub {e['t0']:.1f}-{e['t1']:.1f} s"
                         for e in before if int(e["shot"]) in {r["n"] for r in shots}) or "- none"
    line_txt = "\n".join(f"line {d['idx']}: {d['start_ms'] / 1000:.2f}-{d['end_ms'] / 1000:.2f} s  "
                         f"{('DIALOGUE said by ' + d['speaker']) if d['dialogue'] and d.get('speaker') else 'DIALOGUE' if d['dialogue'] else 'narration'}{' [scene: ' + str(d.get('scene')) + ', location: ' + d['loc'] + ']' if d.get('loc') else ''}{line_tag(d)}: {d['text']}" for d in lines)
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
    text, u = ai.chat(c, model=MODEL or None, max_tokens=32000)
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
        if sc[i] and sc[i + 1] and sc[i] != sc[i + 1] and not same_grp(L[i], L[i + 1]):
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
    scenes |= {d["scene"] for d in dub if d.get("scene") and d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1}
    shots = [r for r in LIB["shots"] if r["scene"] in scenes and (r["start"] <= hi + 40 or r["broll"] == "yes")
             and (r.get("location") != "mixed" or any(ov(r["start"], r["end"], a, b) > 0.05 for a, b in used_src))]
    if HAS_LOC:
        locs = {d["loc"] for d in dub if d.get("loc") and d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1}
        mid = sum(a + b for a, b in used_src) / (2 * len(used_src))
        more = sorted((r for r in LIB["shots"] if r["scene"] not in scenes and r.get("location") in locs and r["broll"] == "yes"
                       and r["type"] in ("reaction", "neutral", "establishing")), key=lambda r: abs(r["start"] - mid))[:12]
        shots = sorted(shots + more, key=lambda r: r["n"])
    sw = [d for d in dub if d.get("spk_ok") and d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1]
    if sw:                                           # SPEAKER shots: the speaker talking at the line's location, unused footage first
        want = {d["speaker"] for d in sw}; locs = {d["loc"] for d in sw if d.get("loc")}; scs = {d["scene"] for d in sw if d.get("scene")}
        have = {r["n"] for r in shots}; mid = (T0 + T1) / 2
        def unused(r):
            return (r["end"] - r["start"]) - sum(ov(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000, r["start"], r["end"]) for s in auto if not s.get("video"))
        sp_ = sorted((r for r in LIB["shots"] if set(talkers(r["n"])) & want and r.get("location") != "mixed"
                      and (r.get("location") in locs or r["scene"] in scs) and r["n"] not in have),
                     key=lambda r: (-min(unused(r), 2.0), abs(r["start"] - mid)))[:8]
        shots = sorted(shots + sp_, key=lambda r: r["n"])
    have = {r["n"] for r in shots}
    for d in dub:
        if d.get("gplan") and d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1:
            for n in d["gplan"]["shots"]:
                if n not in have: shots.append(SH[n]); have.add(n)
    shots = sorted(shots, key=lambda r: r["n"])
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
    for e in pieces: e["reuse"] = False
    for j, e in enumerate(pieces):
        for f in pieces[:j]:
            if ov(f["from"], f["to"], e["from"], e["to"]) > 0.2:
                if spk_piece(f) and not spk_piece(e): f["reuse"] = True     # the SPEAKER piece is the repeat
                else: e["reuse"] = True


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
        if not r or r["type"] in SHARED or spk_piece(e, r): continue
        regs = own.get((r["n"], k), [])
        if i == 0 and prev_last and int(prev_last["shot"]) == r["n"] and abs(e["from"] - prev_last["to"]) <= 0.3:
            regs = regs + [(prev_last["to"] - 0.3, e["to"])]   # continuing the shot the stretch before ends on (no jump cut)
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
        if spk_piece(e, r) or spk_piece(f):
            gap = e["t0"] - f["t1"] if f["t1"] <= e["t0"] else f["t0"] - e["t1"]
            if gap < SPK_GAP:
                probs.append(f"piece {i}: talking footage {e['from']:.2f}-{e['to']:.2f} (shot {r['n']}) is shown by another stretch at dub "
                             f"{f['t0']:.1f}-{f['t1']:.1f} s ({max(gap, 0):.1f} s away); a SPEAKER shot may repeat footage only {SPK_GAP:.0f} s away")
        elif eff_type(e, r) not in SHARED:
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
        if spk_piece(e) or spk_piece(f):
            if e["t0"] - f["t1"] < SPK_GAP:
                probs[e["_w"]].append(f"the piece at dub {e['t0']:.1f} s repeats talking footage {e['from']:.2f}-{e['to']:.2f} (shot {e['shot']}) shown "
                                      f"at dub {f['t0']:.1f} s; a SPEAKER shot may repeat footage only {SPK_GAP:.0f} s away")
        elif (eff_type(e, r) if r else None) not in SHARED:
            probs[e["_w"]].append(f"the piece at dub {e['t0']:.1f} s shows {eff_type(e, r) if r else '?'} footage {e['from']:.2f}-{e['to']:.2f} (shot {e['shot']}) "
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
    try:                                             # fail fast when the AI gateway cannot be reached (VPN off)
        ai.key_info()
    except Exception as ex:
        sys.exit(f"AI gateway not reachable ({str(ex)[:120]}): connect the VPN and run again")
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
            if r["type"] in SHARED:
                return t + " | SHARED shot: may be used (repeats are fine 8 s apart)" + (
                    "; its mouth moves at the times above: never those seconds over narration" if mouth_in(r["start"], r["end"]) > 0.3 else "")
            regs = own.get((r["n"], k))
            sw = [d for d in LINES[k] if d.get("spk_ok") and d["speaker"] in talkers(r["n"])]
            if sw:
                return t + (f" | SPEAKER shot for wrong-speaker line(s) {', '.join(str(d['idx']) for d in sw)}: may be shown there even where "
                            f"its footage is shown elsewhere (at least {SPK_GAP:.0f} s apart)"
                            + (f"; for other lines only {fmt_iv(regs)}" if regs else "; not for other lines"))
            gl = [d for d in LINES[k] if d.get("gplan") and r["n"] in d["gplan"]["shots"]]
            if gl:
                return t + (f" | suggested for not-matched line(s) {', '.join(str(d['idx']) for d in gl)}: may be shown there"
                            + (f"; for other lines only {fmt_iv(regs)}" if regs else "; not for other lines"))
            return t + (f" | footage for THIS stretch: {fmt_iv(regs)} (the rest of the shot belongs to other stretches)" if regs
                        else " | ALL its footage belongs to other stretches: do NOT use it")
        line_txt = "\n".join(f"line {d['idx']}: {d['start_ms'] / 1000:.2f}-{d['end_ms'] / 1000:.2f} s  "
                             f"{('DIALOGUE said by ' + d['speaker']) if d['dialogue'] and d.get('speaker') else 'DIALOGUE' if d['dialogue'] else 'narration'}{' [scene: ' + str(d.get('scene')) + ', location: ' + d['loc'] + ']' if d.get('loc') else ''}{line_tag(d)}: {d['text']}" for d in LINES[k])
        rule8 = (f"\n11. The other stretches of the episode are cut AT THE SAME TIME as this one. For talking / action / object shots use "
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
            if not (ans or {}).get("edl"):                    # an empty answer saved by an older run is asked again
                c = content + ([{"type": "text", "text": extra}] if extra else [])
                ans = {}
                for attempt in range(3):
                    try:
                        ans = ai.extract_json(ask_claude(c), "edl") or {}
                        if ans.get("edl"): break
                    except Exception as ex:
                        print(f"  stretch {k} {T0:.1f}-{T1:.1f}: reply not readable ({str(ex)[:80]}), asking again", flush=True)
                if ans.get("edl"):                          # never cache a failed call (gateway down): it is asked again next run
                    with LOCK:
                        cache[key] = ans; json.dump(cache, open(cache_p, "w"), indent=1, ensure_ascii=False)
                else:
                    raise RuntimeError(f"stretch {k} {T0:.1f}-{T1:.1f}: no usable answer from Claude after 3 tries (gateway down?)")
            new = parse_edl(ans)
            if new: edl = sorted(new, key=lambda e: e["t0"])
            probs = check_par(edl, T0, T1, LINES[k], k, own, others, prev_last)
            rounds.append(dict(round=len(rounds), stage=tag, problems=probs, notes=ans.get("notes", "")))
            print(f"  stretch {k:2d} {T0:6.1f}-{T1:6.1f} [{tag}{rnd}]: {len(edl)} pieces, {len(probs)} problems"
                  + (": " + " | ".join(p[:110] for p in probs[:3]) if probs else ""), flush=True)
            if not probs: break
            extra = (extra0 + "\n\n" if extra0 else "") + ("Your previous EDL:\n" + json.dumps(ans.get("edl"), ensure_ascii=False) +
                     "\n\nIt has these problems, fix them all and answer the complete corrected JSON again:\n- " + "\n- ".join(probs) +
                     "\n\nIf a problem cannot be solved with this footage, keep the best possible cut, say why in notes and answer -- "
                     "do not keep searching.")
        return edl, rounds, time.time() - t

    with ThreadPoolExecutor(PAR) as ex:
        out = list(ex.map(lambda k: run(k, "a", 3), range(len(wins))))
    res = [o[0] for o in out]; rounds = [o[1] for o in out]; wsec = [o[2] for o in out]
    t_first = time.time() - t_start
    print(f"first pass: {len(wins)} stretches in {t_first:.0f} s", flush=True)

    fixes = []
    for g in range(2):
        gp, _ = global_check(res)
        for k in range(len(res)):                          # a stretch still failing its own check goes back too
            if rounds[k] and rounds[k][-1]["problems"]:
                gp[k] = rounds[k][-1]["problems"] + gp[k]
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
                         note="scene edit: " + e.get("why", "")[:200], **({"zoom": 1.08} if e.get("reuse") else {}),
                     **({"broll": True} if is_broll(e, piece_lines(e)) else {})))
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
                     note="scene edit: " + e.get("why", "")[:200], **({"zoom": 1.08} if e.get("reuse") else {}),
                     **({"broll": True} if is_broll(e, piece_lines(e)) else {})))
json.dump(dict(total_ms=round(A1 * 1000), segments=segs, bleed_report=[], groups=[]), open(OUTM, "w"), indent=1, ensure_ascii=False)
json.dump(dict(range=[A0, A1], windows=report), open(OUTM.with_suffix(".edl.json"), "w"), indent=1, ensure_ascii=False)
left = sum(len(w["rounds"][-1]["problems"]) for w in report)
print("saved", OUTM, len(segs), "pieces in", len(report), "windows;", "all checks OK" if not left else f"{left} problems left")
