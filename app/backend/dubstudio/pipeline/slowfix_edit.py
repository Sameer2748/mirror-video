"""Scene edit for slow clips only (same as the EP69-71 local run).

Input is the grouping cut. A group that already plays between 0.8x and 1.25x is left exactly as it is.
A group under 0.8x is split into in-range runs; leftover rows are the units this step fixes.

Usage: slowfix_edit.py <work> [<library dir>]
  work: episode work folder (manifest_groups.grouping.json or manifest_groups.json)
  library dir: default <work>/broll_library
Writes work/scene_slowfix/manifest_slowfix.json and scene_edit.xlsx.
"""
import base64, json, os, sys, time
from collections import defaultdict
from pathlib import Path

def _app_dir(script):
    script = Path(script).resolve()
    for folder in (script.parents[2], Path("/opt/app")):
        pkg = folder / "dubstudio"
        if (pkg / "ai.py").is_file() or (pkg / "__init__.py").is_file():
            return folder
    return script.parents[2]

sys.path.insert(0, str(_app_dir(__file__)))
from dubstudio import ai  # noqa: E402
from dubstudio.pipeline.keep_grouping import grouping_takes  # noqa: E402
from dubstudio.pipeline.broll_policy import (  # noqa: E402
    hole_broll_s, is_long_broll_hole, max_broll_shots)

_pt = Path(__file__).resolve().parents[2] / "pipeline_tools"
if _pt.is_dir():
    sys.path.insert(0, str(_pt))
try:
    import dubstitch as stitch  # noqa: E402
except Exception:
    stitch = None

W = Path(sys.argv[1]).resolve()
LIBD = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else (W / "broll_library")
OUT = W / "scene_slowfix"
OUT.mkdir(parents=True, exist_ok=True)
STRIPS = W / "naming_test" / "strips_png"
FRAMES = LIBD / "_frames"
if not FRAMES.is_dir():
    alt = W / "broll_library_flow4" / "_frames"
    if alt.is_dir():
        FRAMES = alt
MODEL = "claude-sonnet-5-5"
EDITOR = os.environ.get("SLOWFIX_EDITOR") or "claude-opus-5"

LO, HI, MIN_BROLL, REUSE_GAP, MAX_BROLL_USES, EDGE = 0.8, 1.25, 2.0, 8.0, 3, 0.08
MIN_PIECE, MAX_TRANSITIONS, MAX_BROLL_PIECES = 2.0, 3, 3  # MAX_BROLL_PIECES: long holes only (broll_policy)
NARR_MOUTH, TALK_GAP = 0.2, 1.0
SHARED = ("reaction", "neutral", "establishing")

gpath = W / "manifest_groups.grouping.json"
if not gpath.is_file():
    gpath = W / "manifest_groups.json"
if not (W / "manifest_groups.grouping.json").is_file() and (W / "manifest_groups.json").is_file():
    (W / "manifest_groups.grouping.json").write_bytes((W / "manifest_groups.json").read_bytes())
grp = json.load(open(gpath))
lib_path = LIBD / "broll_library.json"
if not lib_path.is_file():
    lib_path = W / "broll_library_flow4" / "broll_library.json"
lib_doc = json.load(open(lib_path))
LIB = lib_doc["shots"]
LIBN = {r["n"]: r for r in LIB}
SH = {r["n"]: r for r in LIB}
CAST = [c["name"] for c in (lib_doc.get("cast") or {}).get("cast") or []]
FACE = {}
for cand in (LIBD / "face_map.json", W / "broll_library" / "face_map.json",
             W / "naming_test" / "images_decisions_flow4.json"):
    if cand.is_file():
        raw = json.load(open(cand))
        FACE = raw.get("shots") or raw
        break
lip = json.load(open(W / "lipsync.json"))
LIP_IV = [tuple(x) for x in lip["intervals"]]
LIP_ROWS = lip["rows"]
DUB = {d["idx"]: d for d in json.load(open(W / "sentences_dub.json"))["sentences"]}
ov = lambda a0, a1, b0, b1: max(0.0, min(a1, b1) - max(a0, b0))


def people_of(n):
    if n is None:
        return []
    rec = FACE.get(str(n)) or FACE.get(n) or FACE.get(int(n) if str(n).isdigit() else n) or {}
    if isinstance(rec, dict) and rec.get("people") and not rec.get("persons"):
        return list(rec.get("people") or [])
    names = []
    for p in rec.get("persons") or rec.get("people") or []:
        if isinstance(p, str):
            if p and p not in ("unknown", "?"):
                names.append(p)
        else:
            nm = (p or {}).get("name")
            if nm and nm not in ("unknown", "?"):
                names.append(nm)
    if names:
        return names
    return list((LIBN.get(n) or {}).get("people") or [])


def cast_of(n):
    return sorted({p for p in people_of(n) if p in CAST})


def _cast_of(n):
    """Sheet dump: a piece with shot None used to KeyError SH[None] (WBT 128/137/146)."""
    if n is None:
        return []
    try:
        return cast_of(n)
    except Exception:
        return []


def _shot_kind(n):
    info = SH.get(n) if n is not None else None
    return (info or {}).get("type") or "?"


def mouth_in(a, b):
    return sum(ov(a, b, x, y) for x, y in LIP_IV)


def shots_over(a, b):
    return [r for r in LIB if ov(a, b, r["start"], r["end"]) > 0.05]


# ---------------- 1. units: slow rows left after the sub-split ----------------
by_row = defaultdict(list)
for s in grp["segments"]:
    by_row[s["it_idx"]].append(s)
for v in by_row.values():
    v.sort(key=lambda s: s["it_start_ms"])


def span(rows):
    segs = sorted((s for r in rows for s in by_row.get(r, [])), key=lambda s: s["it_start_ms"])
    if not segs:
        return None
    dub = (segs[-1]["it_end_ms"] - segs[0]["it_start_ms"]) / 1000
    src = sum(s["us_end_ms"] - s["us_start_ms"] for s in segs) / 1000
    ordered = all(segs[k]["us_start_ms"] + 80 >= segs[k - 1]["us_start_ms"] for k in range(1, len(segs)))
    return dict(dub=dub, src=src, speed=src / dub if dub else 0, ordered=ordered)


def _clip_play(s):
    """Playback = source / dub. Same 0.8–1.25 window as grouping. Use the clip, not group us0/us1."""
    return (s["us_end_ms"] - s["us_start_ms"]) / max(s["it_end_ms"] - s["it_start_ms"], 1)


def make_unit(rows, gi):
    segs = sorted((s for r in rows for s in by_row[r]), key=lambda s: s["it_start_ms"])
    u = dict(group=gi, rows=rows, segs=segs)
    u["t0"], u["t1"] = segs[0]["it_start_ms"] / 1000, segs[-1]["it_end_ms"] / 1000
    u["src"] = sum(s["us_end_ms"] - s["us_start_ms"] for s in segs) / 1000
    u["speed"] = u["src"] / (u["t1"] - u["t0"])
    g = grp["groups"][gi]
    u["group_speed"] = (g["us1"] - g["us0"]) / max(g["it1"] - g["it0"], 1)
    u["lip"] = any(str(r) in LIP_ROWS for r in rows)
    u["dialogue"] = any(DUB.get(r, {}).get("text_type") == "speech" for r in rows)
    parts = []
    for s in segs:
        a, b = s["us_start_ms"] / 1000, s["us_end_ms"] / 1000
        d0, d1 = s["it_start_ms"] / 1000, s["it_end_ms"] / 1000
        for r in shots_over(a, b):
            x, y = max(a, r["start"]), min(b, r["end"])
            f0 = d0 + (x - a) / (b - a) * (d1 - d0) if b > a else d0
            f1 = d0 + (y - a) / (b - a) * (d1 - d0) if b > a else d1
            parts.append(dict(shot=r["n"], us0=x, us1=y, it0=f0, it1=f1, row=s["it_idx"],
                              shown=cast_of(r["n"]), people=people_of(r["n"]), type=r["type"]))
    u["parts"] = parts
    tot = defaultdict(float)
    for p in parts:
        loc = SH[p["shot"]].get("location")
        if loc and loc != "mixed":
            tot[loc] += p["us1"] - p["us0"]
    u["loc"] = max(tot, key=tot.get) if tot else None
    u["scene"] = SH[parts[0]["shot"]]["scene"] if parts else None
    u["prev"] = DUB.get(rows[0] - 1, {}).get("text", "")
    u["text"] = " ".join(DUB.get(r, {}).get("text", "") for r in rows)
    u["src_text"] = " ".join(dict.fromkeys(s.get("us_text", "") for s in segs if s.get("us_text")))
    return u


# Slow runs: the rows of each group that are left over after the in-range sub-split (same rule as before).
# Lip groups still use the group span (one take). Leftovers use each clip's source/dub after
# overlap-trim — group us0/us1 can still look 1.25× while one row is an 80 ms sliver.
runs = []
for gi, g in enumerate(grp["groups"]):
    rows = [r for r in g["rows"] if r in by_row]
    segs_g = [s for r in rows for s in by_row[r]]
    sp = (g["us1"] - g["us0"]) / max(g["it1"] - g["it0"], 1)
    lip_take = bool(g.get("lipgroup") or g.get("lip"))
    if lip_take:
        if sp >= LO:
            continue
    elif segs_g and all(_clip_play(s) >= LO for s in segs_g) and sp >= LO:
        continue
    i, left = 0, []
    while i < len(rows):
        best = None
        for j in range(i, len(rows)):
            got = span(rows[i:j + 1])
            if not got or not got["ordered"]:
                break
            if LO <= got["speed"] <= HI:
                best = j
        if best is not None:
            if left:
                runs.append((gi, left))
                left = []
            i = best + 1
        else:
            left.append(rows[i])
            i += 1
    if left:
        runs.append((gi, left))

# One unit per SENTENCE (row) first. The subject is decided for each sentence; a neighbour joins it only when
# the who-call gives the same people, so a row about someone else never borrows another row's B-roll.
row_units = [make_unit([r], gi) for gi, rows in runs for r in rows]

# ---------------- 2. who should be on screen: one text call per sentence ----------------
if not row_units:
    json.dump(grp, open(OUT / "manifest_slowfix.json", "w"), indent=1, ensure_ascii=False)
    json.dump([], open(OUT / "decisions.json", "w"))
    print("no slow leftover units; grouping cut kept")
    raise SystemExit(0)
cache_p = OUT / "who_row_cache.json"
who_usage = dict(calls=0, prompt_tokens=0, completion_tokens=0, cost=0.0)
if cache_p.is_file():
    who = json.load(open(cache_p))
    cost = 0.0
else:
    lines = []
    for u in row_units:
        shown = "; ".join(f"shot {p['shot']}: {', '.join(p['people']) or 'nobody'}" for p in u["parts"])
        lines.append(
            f"row {u['rows'][0]} | dub {u['t0']:.1f}-{u['t1']:.1f} s | {'DIALOGUE (spoken aloud)' if u['dialogue'] else 'narration'}\n"
            f"  previous dub line: {u['prev'] or '(none)'}\n"
            f"  this dub line: {u['text']}\n"
            f"  source (English) line over the clip: {u['src_text'] or '(none)'}\n"
            f"  the clip shows now: {shown}")
    q = (
        "A dubbed drama episode (German). The dub may use other names for the same people "
        "(Francis = Frankie, Ida or Ayla = Isla, Keira = Kira, Becca = Rebecca Allen).\n"
        f"Cast (use exactly these names): {', '.join(CAST)}.\n\n"
        "For each dub line below (one per row) decide who the viewer should SEE on screen while this dub line plays.\n"
        "- Narration: the person the line is about, or who acts or reacts in it. Use the previous line when the "
        "line says he / she / they.\n"
        "- Dialogue: the speaker. Also give the listener when the line is said to someone.\n"
        "- A line about a place or an object with nobody in it: show = [].\n"
        "- Do not judge the clip. Only say who should be on screen.\n"
        "Answer only JSON: {\"units\": [{\"u\": <row number>, \"show\": [names], \"listener\": [names], \"why\": \"one short sentence\"}]}\n\n"
        + "\n\n".join(lines))
    t = time.time()
    text, usage = ai.chat(q, model=MODEL, max_tokens=8000, reasoning={"effort": "low"})
    who = {str(x["u"]): x for x in ai.extract_json(text, "units")["units"]}
    missing = [u["rows"][0] for u in row_units if str(u["rows"][0]) not in who]
    if missing:
        raise SystemExit(f"who call gave no answer for rows {missing}; not running the scene edit")
    json.dump(who, open(cache_p, "w"), indent=1, ensure_ascii=False)
    cost = float((usage or {}).get("cost") or 0)
    who_usage = dict(calls=1, prompt_tokens=int((usage or {}).get("prompt_tokens") or 0),
                     completion_tokens=int((usage or {}).get("completion_tokens") or 0), cost=cost)
    print(f"who call: {time.time() - t:.1f} s, in {who_usage['prompt_tokens']} out {who_usage['completion_tokens']}, "
          f"${cost:.4f}", flush=True)


def who_key(u):
    w = who.get(str(u["rows"][0])) or {}
    return (tuple(sorted(n for n in (w.get("show") or []) if n in CAST)),
            tuple(sorted(n for n in (w.get("listener") or []) if n in CAST)), u["dialogue"])


# Merge neighbouring sentences of one run only when they show the same people (and listen to the same people).
units = []
for u in row_units:
    p = units[-1] if units else None
    if p and p["group"] == u["group"] and p["rows"][-1] + 1 == u["rows"][0] and who_key(p) == who_key(u):
        units[-1] = make_unit(p["rows"] + u["rows"], u["group"])
    else:
        units.append(u)
for k, u in enumerate(units):
    u["k"] = k
UNTIL = float(os.environ.get("SLOWFIX_UNTIL") or 0)
if UNTIL:
    units = [u for u in units if u["t0"] < UNTIL]
    for k, u in enumerate(units):
        u["k"] = k
    print(f"test until {UNTIL:.0f}s: {len(units)} units", flush=True)
print(f"{len(units)} slow units ({len(row_units)} sentences), {sum(len(u['rows']) for u in units)} rows, "
      f"{sum(u['t1'] - u['t0'] for u in units):.1f} s of {grp['total_ms'] / 1000:.1f} s", flush=True)

# Visual editor (Opus): sees the grouping cut, pictures, and every rule. Code only executes the plan.
def _img(n, label):
    p = STRIPS / f"shot_{n:03d}.png"
    if not p.is_file():
        p = FRAMES / f"shot_{n:03d}.jpg"
    if not p.is_file():
        return [{"type": "text", "text": f"(no picture) {label}"}]
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return [{"type": "text", "text": label},
            {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, base64.b64encode(p.read_bytes()).decode())}}]


def _group_brief(gi, g, us):
    D = (g["it1"] - g["it0"]) / 1000
    S = (g["us1"] - g["us0"]) / 1000
    lines = []
    for r in g["rows"]:
        d = DUB.get(r, {})
        segs = by_row.get(r, [])
        if not segs:
            continue
        src = sum(s["us_end_ms"] - s["us_start_ms"] for s in segs) / 1000
        dub = (segs[-1]["it_end_ms"] - segs[0]["it_start_ms"]) / 1000 if segs else 0
        us0 = segs[0]["us_start_ms"] / 1000 if segs else 0
        us1 = segs[-1]["us_end_ms"] / 1000 if segs else 0
        lip = str(r) in LIP_ROWS
        speech = ""
        if lip:
            sp = LIP_ROWS[str(r)].get("speech") or [0, 0]
            slot0, slot1 = segs[0]["it_start_ms"] / 1000, segs[-1]["it_end_ms"] / 1000
            sp0, sp1 = sp[0] / 1000, sp[1] / 1000
            speech = (f" speech {sp0:.2f}-{sp1:.2f}s | pause before {max(0, sp0 - slot0):.2f}s "
                      f"pause after {max(0, slot1 - sp1):.2f}s")
        shown = []
        for s in segs:
            for sh in shots_over(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000):
                shown.append(f"shot {sh['n']} {sh.get('type')} {', '.join(people_of(sh['n'])) or 'nobody'}")
        w = who.get(str(r)) or {}
        unit_here = next((u for u in us if r in u["rows"]), None)
        lines.append(
            f"  row {r} | dub {d.get('start_ms', 0)/1000:.2f}-{d.get('end_ms', 0)/1000:.2f}s "
            f"(slot {segs[0]['it_start_ms']/1000:.2f}-{segs[-1]['it_end_ms']/1000:.2f}s, {dub:.2f}s) "
            f"| matched US {us0:.2f}-{us1:.2f}s ({src:.2f}s, {src / dub if dub else 0:.2f}x) "
            f"| {'LIP-SYNC talking' if lip else 'narration'}{speech}\n"
            f"    dub: {d.get('text', '')}\n"
            f"    matched source: {segs[0].get('us_text') or d.get('src_text') or ''}\n"
            f"    clip shows: {'; '.join(dict.fromkeys(shown)) or 'n/a'}\n"
            f"    should show: {', '.join(w.get('show') or []) or 'n/a'} "
            f"(listener {', '.join(w.get('listener') or []) or '-'})"
            + ("" if unit_here else " | already in-range, grouping left this row alone"))
    shots = [r for r in LIB if ov(g["us0"] / 1000, g["us1"] / 1000, r["start"], r["end"]) > 0.15]
    loc = None
    tot = defaultdict(float)
    for r in shots:
        tot[r.get("location")] += r["end"] - r["start"]
    if tot:
        loc = max(tot, key=tot.get)
    brolls = [r for r in LIB if r.get("broll") == "yes" and r.get("type") in SHARED
              and r.get("location") == loc][:10]
    btxt = ", ".join(f"shot {r['n']} {r['type']} {r['start']:.1f}-{r['end']:.1f}s "
                     f"({', '.join(people_of(r['n'])) or 'place'})" for r in brolls) or "none"
    leftover = [u["rows"] for u in us]
    unused_txt = "UNUSED EDGES (not in grouping; matching stays unless you set unused): none (lip group)"
    if stitch and not (g.get("lipgroup") or g.get("lip")):
        Gtmp = []
        for h in grp["groups"]:
            segs_h = sorted((s for r in h["rows"] for s in by_row.get(r, [])),
                            key=lambda s: s["it_start_ms"])
            hh = dict(h)
            hh["segments"] = segs_h
            Gtmp.append(hh)
        gi_g = Gtmp[gi] if gi < len(Gtmp) else None
        if gi_g and stitch._span_of(gi_g):
            us0, us1 = stitch._span_of(gi_g)
            rb, ra = stitch._adjacent_unused(us0, us1, stitch.other_group_spans(Gtmp, gi_g))
            rb_s, ra_s = (rb / 1000 if rb < 1e11 else 0), (ra / 1000 if ra < 1e11 else 0)
            lip_g = bool(g.get("lipgroup") or g.get("lip"))
            unused_txt = (
                f"UNUSED EDGES (not in grouping; matching stays unless you opt in):\n"
                f"  before: {rb_s:.2f}s immediately left of US {us0/1000:.2f}s\n"
                f"  after: {ra_s:.2f}s immediately right of US {us1/1000:.2f}s\n"
                f"  lip group: {lip_g} — if true, unused MUST be none\n"
                f"  using unused must not cover another group's matched take"
            )
    return (
        f"GROUP {gi} dub {g['it0']/1000:.2f}-{g['it1']/1000:.2f}s ({D:.1f}s) | "
        f"source span {g['us0']/1000:.2f}-{g['us1']/1000:.2f}s ({S:.1f}s) | "
        f"if played as one clip: {S / D if D else 0:.2f}x (allowed 0.80-1.25x)\n"
        f"grouping note: {g.get('why') or g.get('note') or '-'}\n"
        f"scene-edit leftover units (slow after in-range sub-split): {leftover}\n"
        f"{unused_txt}\n"
        f"location: {loc}\n"
        f"same-place B-roll available: {btxt}\n"
        + "\n".join(lines)
    )


RANK_WHY = {
    0: "exact people who should be on screen",
    1: "more than one of those people, nobody else",
    2: "one-person reaction of someone who should be on screen",
    3: "one person from the line (not labelled reaction)",
    4: "those people plus someone extra",
}

MAIN_SHARE = 0.2
LOC_SHARE = defaultdict(lambda: defaultdict(float))
for _r in LIB:
    LOC_SHARE[_r["scene"]][_r.get("location")] += _r["end"] - _r["start"]
for _s, _c in LOC_SHARE.items():
    _t = sum(_c.values()) or 1
    for _l in _c:
        _c[_l] /= _t


def candidates(need, loc, scene, avoid, mid):
    """Same background as scene edit allows: a shot of the line's scene, or the same location elsewhere."""
    need = set(need)
    rows = []
    for r in LIB:
        if r.get("broll") != "yes" or r.get("type") not in SHARED or r.get("location") == "mixed":
            continue
        same_place = loc and r.get("location") == loc
        same_scene_main = (scene and r.get("scene") == scene and LOC_SHARE[scene].get(loc, 0) >= MAIN_SHARE
                           and LOC_SHARE[scene].get(r.get("location"), 0) >= MAIN_SHARE)
        if not (same_place or same_scene_main):
            continue
        if any(ov(r["start"], r["end"], a, b) > 0.05 for a, b in avoid):
            continue
        c = set(cast_of(r["n"]))
        if need:
            if c == need:
                rank = 0
            elif len(need) > 1 and c and c < need and len(c) > 1:
                rank = 1
            elif len(need) > 1 and len(c) == 1 and c < need and r["type"] == "reaction":
                rank = 2
            elif len(c) == 1 and c < need:
                rank = 3
            elif need < c:
                rank = 4
            else:
                continue
        else:
            if r["type"] != "establishing" and c:
                continue
            rank = 0
        rows.append((rank, 0 if r["scene"] == scene else 1, abs(r["start"] - mid), r))
    rows.sort(key=lambda x: x[:3])
    return [(x[0], x[3]) for x in rows]


EDITOR_RULES = """You are the finishing editor of a dubbed drama. A real viewer will watch this. Sync and
continuity matter more than hitting 0.80x.

You already have: German dub times, English source times, matching, unused source between matches (already
in the grouping cut when the scene is continuous), lip-scan (moving mouth), who is in each shot, who should
be on screen, pictures of the current shots, and a RANKED list of every allowed B-roll for this group
(people + place + duration). Rank 1 is the closest people+place match. You pick which shot to use.

Edit like a human:
1. Play the MATCHED source on the line it belongs to. If people are talking in that source, the German
   line must sit on that same talking footage. Never replace a talking mouth with B-roll from another
   time in the episode (that looks like a glitch).
2. A continuous lip-sync / conversation (several German rows on one source sentence or one take) stays
   ONE piece of picture. keep_whole=true. Do not cut B-roll into the middle of that chain.
3. Slightly slow matched picture (even 0.45–0.80x) is better than a jump cut. Do not B-roll just to
   chase 0.80x. If you do add B-roll, aim so the own clip reaches about 0.80x, not 1.0x.
4. B-roll is allowed only when: (a) there is no matched picture for that dub time, (b) a long pause with
   nobody to show, or (c) the clip shows the WRONG person and a same-place reaction of the right person
   exists. Put B-roll in a pause (before/after speech), never on the talking mouth. Each B-roll ≥2.0 s.
   Short leftover (~2 s of B-roll to reach 0.80x): at most ONE shot; if no single shot fits, keep_whole
   (two–three cuts there look jumpy). Long leftover/pause (needs 8 s or more to reach 0.80x, e.g. 20 s
   of dub on 0.6 s of source): you MAY use several ranked B-rolls of the right people and place (at most
   3 shots) to cover that pause. Do not refuse B-roll on a long hole just because one shot is too short.
5. Pick the B-roll from the RANKED list only (shot number must be on that list). Prefer a higher rank
   (exact people, same place). You MAY pick a lower rank if the picture is a better reaction for this
   line (a one-person reaction of someone in a multi-person line is allowed). Do not invent a shot.
6. Unused *middle* inside a lip group take is already in the grouping cut — keep it. Unused *edges*
   (listed below) are NOT in the grouping cut. Matching stays on the matched line until you opt in.
7. You may attach unused before/after ONLY when ALL of these hold: (a) this is a leftover narration
   group, not a lip-sync group, (b) the leftover is slower than 0.80x, (c) the unused is immediately
   adjacent and nobody else is matched to it, (d) using it will NOT put another line's talking mouth
   on this line or this line's mouth on another. If any of those fail, unused=none.
8. Lip-sync / conversation groups: unused=none always. Slightly slow matched picture is better than
   stealing unused that unsyncs the match.
9. At most 3 visual transitions in a sentence. Do not retime every sentence. Other rows keep grouping.
10. If a B-roll source is 1.3 s, it may play 2 s (steal 0.7 s from the previous own clip). Code does that.
11. If you are not sure, keep_whole and unused=none. A safe matched cut beats unused fill or B-roll.
12. A leftover row playing under 0.40x or under 0.5 s of source with a SHORT dub slot is a script hole
    (overlap-trim), not a creative slow-mo — do not B-roll a lip-sync line to fill 2 s. A LONG dub
    pause on a tiny source (20 s+ of unmatched/extra story) is different: cover it with ranked
    same-place B-roll as in (4), and keep the talking mouth on the matched take.

Answer only JSON for THIS group:
{"g": <id>, "keep_whole": true/false, "row": <row or null>, "broll_s": <seconds or 0>,
 "where": "after"|"before"|"none", "shot": <broll shot number or null>,
 "unused": "before"|"after"|"none", "unused_s": <seconds or 0>,
 "why": "one short sentence"}
"""

plan_p = OUT / "broll_plan_editor.json"
usage_p = OUT / "claude_usage.json"
slow_gs = []
seen_g = []
for u in units:
    if u["group"] not in seen_g:
        seen_g.append(u["group"])
        slow_gs.append(u["group"])
keep_plan = plan_p.is_file()
if keep_plan:
    broll_plan = {int(k): v for k, v in json.load(open(plan_p)).items()}
    print(f"editor plan: {len(broll_plan)} groups from cache ({EDITOR})", flush=True)
if keep_plan and usage_p.is_file():
    pass
else:
    measured, cost_ed = {}, 0.0
    tok_in, tok_out, n_ed = 0, 0, 0
    t0 = time.time()
    for gi in slow_gs:
        g = grp["groups"][gi]
        us_here = [u for u in units if u["group"] == gi]
        brief = _group_brief(gi, g, us_here)
        own_ns, br_ns = [], []
        for r in g["rows"]:
            for s in by_row.get(r, []):
                for sh in shots_over(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000):
                    if sh["n"] not in own_ns:
                        own_ns.append(sh["n"])
        loc = us_here[0]["loc"] if us_here else None
        scene = us_here[0]["scene"] if us_here else None
        need = set()
        for u in us_here:
            w = who.get(str(u["rows"][0])) or {}
            need |= {n for n in (w.get("show") or []) if n in CAST}
            if u["dialogue"]:
                need |= {n for n in (w.get("listener") or []) if n in CAST}
        avoid = [(g["us0"] / 1000, g["us1"] / 1000)]
        mid = (g["us0"] + g["us1"]) / 2000
        ranked = [(rank, r) for rank, r in candidates(sorted(need), loc, scene, avoid, mid)
                  if r["n"] not in own_ns]
        br_ns = [r["n"] for _, r in ranked]
        lines = []
        for i, (rank, r) in enumerate(ranked, 1):
            dur = r["end"] - r["start"]
            lines.append(
                f"  {i}. shot {r['n']} | rank {rank} ({RANK_WHY.get(rank, '')}) | {r.get('type')} | "
                f"{r.get('location')} | {dur:.1f}s source ({r['start']:.1f}-{r['end']:.1f}s) | "
                f"people: {', '.join(people_of(r['n'])) or 'place/nobody'}")
        ranked_txt = ("RANKED B-roll for this group (pick one shot number from this list, or keep_whole).\n"
                      f"Who should be on screen: {', '.join(sorted(need)) or 'place'}. Location: {loc}.\n"
                      + ("\n".join(lines) if lines else "  (none passed the people+place filter)"))
        content = [{"type": "text", "text": EDITOR_RULES + "\n\n" + brief + "\n\n" + ranked_txt
                    + "\n\nPictures of the CURRENT grouping shots:"}]
        for n in own_ns[:6]:
            r = LIBN.get(n) or {}
            content += _img(n, f"CURRENT shot {n} {r.get('type')} {r.get('location')} "
                            f"{', '.join(people_of(n)) or 'nobody'} {r.get('start', 0):.1f}-{r.get('end', 0):.1f}s")
        if ranked:
            content.append({"type": "text", "text": "Pictures of the top ranked B-roll (full list is in the text):"})
            for rank, r in ranked[:8]:
                content += _img(r["n"], f"B-ROLL shot {r['n']} rank {rank} {r.get('type')} {r.get('location')} "
                                f"{', '.join(people_of(r['n'])) or 'nobody'} "
                                f"{r['start']:.1f}-{r['end']:.1f}s ({r['end']-r['start']:.1f}s)")
        text, usage = ai.chat(content, model=EDITOR, max_tokens=800, reasoning={"effort": "low"})
        cost_ed += float((usage or {}).get("cost") or 0)
        pin, pout = int((usage or {}).get("prompt_tokens") or 0), int((usage or {}).get("completion_tokens") or 0)
        tok_in += pin; tok_out += pout; n_ed += 1
        print(f"  editor G{gi}: in {pin} out {pout} ${float((usage or {}).get('cost') or 0):.4f}", flush=True)
        try:
            p = ai.extract_json(text)
            if "g" not in p and isinstance(p.get("groups"), list) and p["groups"]:
                p = p["groups"][0]
        except Exception as ex:
            print(f"  editor G{gi}: JSON fail ({ex}); keep whole", flush=True)
            p = dict(g=gi, keep_whole=True, row=None, broll_s=0, where="none", shot=None,
                     unused="none", unused_s=0, why="editor JSON fail")
        p["g"] = gi
        measured[gi] = p
        print(f"  editor G{gi}: {len(ranked)} ranked brolls keep_whole={p.get('keep_whole')} row={p.get('row')} "
              f"broll={p.get('broll_s')} {p.get('where')} shot={p.get('shot')} "
              f"unused={p.get('unused')} {p.get('unused_s')} | {p.get('why')}", flush=True)
    if not keep_plan:
        broll_plan = measured
        json.dump({str(k): v for k, v in broll_plan.items()}, open(plan_p, "w"), indent=1, ensure_ascii=False)
    json.dump(dict(who=who_usage,
                   editor=dict(calls=n_ed, prompt_tokens=tok_in, completion_tokens=tok_out, cost=cost_ed),
                   model=EDITOR),
              open(usage_p, "w"), indent=1)
    print(f"editor plan: {time.time() - t0:.1f}s, ${cost_ed:.4f}, {len(measured)} groups, "
          f"in {tok_in} out {tok_out} calls {n_ed}, model {EDITOR}", flush=True)

if stitch:
    G = []
    for g in grp["groups"]:
        segs = sorted((s for r in g["rows"] for s in by_row.get(r, [])), key=lambda s: s["it_start_ms"])
        gg = dict(g)
        gg["segments"] = segs
        G.append(gg)
    plan_idx = {}
    for gi, p in (broll_plan or {}).items():
        idx = int(gi)
        plan_idx[idx] = p
        if 0 <= idx < len(G) and (G[idx].get("lipgroup") or G[idx].get("lip")):
            plan_idx[idx] = dict(p, unused="none", unused_s=0)
    unused_ok = stitch.apply_approved_unused(G, plan_idx, target=1.0 / LO)
    if unused_ok:
        print(f"opus unused-edge on leftovers: {unused_ok}", flush=True)
        for gi, gg in enumerate(G):
            if "us0" in gg:
                grp["groups"][gi]["us0"] = gg["us0"]
                grp["groups"][gi]["us1"] = gg["us1"]
        for u in units:
            segs = u["segs"]
            u["src"] = sum(s["us_end_ms"] - s["us_start_ms"] for s in segs) / 1000
            u["speed"] = u["src"] / max(u["t1"] - u["t0"], 1e-6)
            if u["speed"] >= LO:
                u["skip_group_broll"] = True
    else:
        print("opus unused-edge: none applied (matched takes kept)", flush=True)

for u in units:
    p = broll_plan.get(u["group"]) or {}
    u["claude_why"] = p.get("why") or ""
    long_hole = is_long_broll_hole(u["t1"] - u["t0"], u["src"])
    # Short leftover: keep_whole means no B-roll. Long pause: still try ranked multi B-roll
    # in the gap (talking mouth stays on the matched take).
    if p.get("keep_whole") and not long_hole:
        u["skip_group_broll"] = True
        continue
    host_row = p.get("row")
    b = float(p.get("broll_s") or 0)
    if host_row is None or b < MIN_BROLL - 1e-6:
        if long_hole:
            u.pop("skip_group_broll", None)
            continue
        u["skip_group_broll"] = True
        continue
    if host_row in u["rows"]:
        room = (u["t1"] - u["t0"]) - u["src"] / HI
        u["group_broll"] = min(max(b, MIN_BROLL), max(room, MIN_BROLL))
        u["broll_where"] = p.get("where") or "after"
        if p.get("shot") is not None:
            u["broll_shot"] = int(p["shot"])
        u.pop("skip_group_broll", None)
    else:
        if long_hole:
            u.pop("skip_group_broll", None)
        else:
            u["skip_group_broll"] = True

# ---------------- 3. B-roll picking ----------------
uses = []   # (us0, us1, it0, it1, shot, broll)
unit_rows = {r for u in units for r in u["rows"]}
for s in grp["segments"]:
    if s["it_idx"] in unit_rows:
        continue
    a, b = s["us_start_ms"] / 1000, s["us_end_ms"] / 1000
    for r in shots_over(a, b):
        uses.append((max(a, r["start"]), min(b, r["end"]), s["it_start_ms"] / 1000, s["it_end_ms"] / 1000, r["n"], False))


def free_ranges(r):
    out = [(r["start"] + EDGE, r["end"] - EDGE)]
    for x, y in LIP_IV:
        x, y = x - 0.1, y + 0.1
        out = [q for a, b in out for q in ((a, min(b, x)), (max(a, y), b)) if q[1] - q[0] > 0.3]
    return out


def allowed(x, y, p0, p1, n, narration=False):
    """Action footage is shown once. A talking shot may repeat, but not within 1 s of itself.
    Over narration a B-roll with more than 0.2 s of moving mouth is refused.
    Reaction, neutral and establishing repeat only 8 s apart, and one B-roll shot at most 3 times."""
    if narration and mouth_in(x, y) > NARR_MOUTH:
        return False
    if sum(1 for u in uses if u[4] == n and u[5]) >= MAX_BROLL_USES:
        return False
    new_kind = LIBN[n].get("type")
    for a, b, t0, t1, m, _ in uses:
        hit = ov(x, y, a, b)
        gap = max(p0 - t1, t0 - p1)
        kind = LIBN[m].get("type")
        if "talking" in (kind, new_kind):
            if (hit > 0.05 or m == n) and gap < TALK_GAP:
                return False
            continue
        if kind not in SHARED or new_kind not in SHARED:
            if hit > 0.05:
                return False
            continue
        if hit > 0.05 and gap < REUSE_GAP:
            return False
    return True


def fill(L, t_start, need, loc, scene, avoid, mid, prev_shot=None, narration=False, max_pieces=MAX_BROLL_PIECES,
         prefer_shot=None):
    """B-roll pieces that cover L seconds of dub from t_start. None if it cannot be done.
    max_pieces caps how many B-roll shots we cut in (each extra shot is another transition)."""
    cands = candidates(need, loc, scene, avoid, mid)
    if prefer_shot is not None:
        pref = [(rank, r) for rank, r in cands if r["n"] == prefer_shot]
        rest = [(rank, r) for rank, r in cands if r["n"] != prefer_shot]
        if not pref and prefer_shot in LIBN:
            r = LIBN[prefer_shot]
            if r.get("broll") == "yes" and r.get("type") in SHARED:
                pref = [(-1, r)]
        cands = pref + rest
    pieces, R, t, last = [], L, t_start, prev_shot
    tried = 0
    while R > 0.02 and tried < max_pieces:
        pick = None
        for rank, r in cands:
            same_place = [c for _, c in cands if c["n"] != r["n"] and c.get("location") == r.get("location")]
            if r["n"] == last and same_place:
                continue
            for a, b in free_ranges(r):
                rl = b - a
                p = min(R, rl / LO)
                if 0 < R - p < MIN_BROLL:
                    p = R - MIN_BROLL if R - MIN_BROLL >= MIN_BROLL else (R if rl / LO >= R else 0)
                if p < min(MIN_BROLL, R) - 1e-6:
                    continue
                src = min(rl, p)
                if src < p * LO:
                    src = p * LO
                if src > rl + 1e-6:
                    continue
                for off in (0.0, (rl - src) / 2, rl - src):
                    x, y = a + off, a + off + src
                    if allowed(x, y, t, t + p, r["n"], narration):
                        pick = dict(shot=r["n"], us0=x, us1=y, it0=t, it1=t + p, rank=rank)
                        break
                if pick:
                    break
            if pick:
                break
        if not pick:
            return None
        pieces.append(pick)
        uses.append((pick["us0"], pick["us1"], pick["it0"], pick["it1"], pick["shot"], True))
        R -= pick["it1"] - pick["it0"]
        t = pick["it1"]
        last = pick["shot"]
    if R > 0.02:
        for p in pieces:
            uses.remove((p["us0"], p["us1"], p["it0"], p["it1"], p["shot"], True))
        return None
    return pieces


def fill_one(L, t_start, need, loc, scene, avoid, mid, prev_shot=None, narration=False, prefer_shot=None):
    """One B-roll shot covering L seconds of dub, speed 0.8–1.25. None if no single shot can do it."""
    if L < MIN_BROLL - 1e-6:
        return None
    return fill(L, t_start, need, loc, scene, avoid, mid, prev_shot, narration, max_pieces=1,
                prefer_shot=prefer_shot)


def n_jumps(pieces):
    """A transition is a jump in source time (what the finished video actually cuts). Camera-shot
    labels on continuous US footage are not extra transitions."""
    n = 0
    for a, b in zip(pieces, pieces[1:]):
        if abs(b["us0"] - a["us1"]) > 0.05:
            n += 1
    return n


def steal_for_min_broll(pieces):
    """If a B-roll was given less than 2 s of dub, take that time from the neighbouring own clip
    (play the own clip a little faster, still showing all of its source) so the B-roll plays 2 s.
    Only when the own clip stays inside 0.8–1.25x and, if it was already ≥2 s, it stays ≥2 s.
    Only when the B-roll still has enough source to play those 2 s at ≥0.8x."""
    out = [dict(p) for p in pieces]
    for i, p in enumerate(out):
        broll = not p.get("own") and not p.get("kept_wrong") and not p.get("kept_grouping")
        dur = p["it1"] - p["it0"]
        if not broll or dur >= MIN_BROLL - 1e-6:
            continue
        need = MIN_BROLL - dur
        bsrc = p["us1"] - p["us0"]
        # Graph: a 1.3 s B-roll source may play as 2 s of dub so the cut is not a flash (about 0.65x).
        if bsrc / MIN_BROLL < 0.6:
            continue
        donors = [j for j in (i - 1, i + 1) if 0 <= j < len(out) and out[j].get("own")]
        for j in donors:
            d = out[j]
            dd = d["it1"] - d["it0"]
            dsrc = d["us1"] - d["us0"]
            new_own = dd - need
            # Example: own 1.0-3.0s (2 s) and a 1.3 s B-roll -> steal 0.7 s so B-roll plays 2.0-4.3s.
            # Own may drop under 2 s; it may run a little faster than 1.25x (cap 1.6x).
            if new_own < 0.05 or dsrc / new_own > 1.6 + 1e-6:
                continue
            if j < i:
                d["it1"] = d["it0"] + new_own
                p["it0"] = d["it1"]
                p["it1"] = p["it0"] + MIN_BROLL
            else:
                p["it1"] = p["it0"] + MIN_BROLL
                d["it0"] = p["it1"]
                d["it1"] = d["it0"] + new_own
            break
    return out


def own_scaled(parts, t_start, speed):
    out, t = [], t_start
    for p in parts:
        d = (p["us1"] - p["us0"]) / speed
        out.append(dict(shot=p["shot"], us0=p["us0"], us1=p["us1"], it0=t, it1=t + d, row=p["row"], own=True))
        t += d
    return out


def mouth_error(u, own, t_start_own):
    rows = [r for r in u["rows"] if str(r) in LIP_ROWS]
    if not rows:
        return None
    voice = LIP_ROWS[str(rows[0])]["speech"][0] / 1000
    for p in own:
        for x, y in LIP_IV:
            if p["us0"] - 0.05 <= x < p["us1"]:
                sp = (p["us1"] - p["us0"]) / (p["it1"] - p["it0"])
                return abs(p["it0"] + (x - p["us0"]) / sp - voice)
    return None


def solve(u):
    D, S = u["t1"] - u["t0"], u["src"]
    w = who.get(str(u["rows"][0])) or {}
    need = sorted({n for n in (w.get("show") or []) if n in CAST})
    listener = sorted({n for n in (w.get("listener") or []) if n in CAST})
    # A multi-person line looks for that group, never for one listener from it.
    broll_need = need if len(need) > 1 else (listener if (u["dialogue"] and listener) else need)
    avoid = [(p["us0"], p["us1"]) for p in u["parts"]]
    mid = sum(p["us0"] + p["us1"] for p in u["parts"]) / (2 * max(1, len(u["parts"])))
    right_people = set(need) | (set(listener) if u["dialogue"] else set())
    wrong = [p for p in u["parts"] if right_people and not right_people & set(p["shown"])]
    e_orig = mouth_error(u, u["parts"], u["t0"])
    res = dict(need=need, listener=listener, why=w.get("why", ""), case=2 if wrong else 1, pieces=[], problems=[],
               choice="")
    if u.get("skip_group_broll"):
        shot = u["parts"][0]["shot"] if u["parts"] else None
        res.update(pieces=grouping_takes(u["segs"], shot),
                   choice="kept grouping: " + (u.get("claude_why") or "Claude: no B-roll here"))
        return res
    if not wrong:
        # Own clip target: just reach 0.80x with as little B-roll as possible. A lip leftover is never
        # sped above 0.80x (do not make the lip middle of a slow group faster than the rest of the group).
        target = LO
        need_b = D - S / target
        if u.get("group_broll"):
            need_b = u["group_broll"]
        b_hi = D - S / HI
        # Graph rule: if the B-roll that would get us to 0.80x is under 2 s, still play 2 s of B-roll and
        # run the own clip a little faster (complete source, shorter dub time), as long as that ≤ 1.25x.
        if 0 < need_b < MIN_BROLL and D - MIN_BROLL > 0.05 and S / (D - MIN_BROLL) <= HI + 1e-6:
            need_b = MIN_BROLL
        limit = max(0.3, e_orig or 0) + 0.05

        def place(b, max_pieces):
            if b < MIN_BROLL - 1e-6:
                return None
            own_d = D - b
            if own_d < 0.05 or S / own_d > HI + 1e-6:
                return None
            # One B-roll may leave the own clip a bit under 0.80x when no single shot is long enough.
            # It must still be faster than the grouping cut. Two-or-more B-rolls must reach 0.80x.
            if max_pieces == 1:
                if S / own_d + 1e-6 < u["speed"]:
                    return None
                # One short B-roll on a 20 s line that stays at 0.28x is a jump for almost no gain.
                if S / own_d < LO - 0.12:
                    return None
            elif S / own_d < LO - 0.05:
                return None
            own_after = own_scaled(u["parts"], u["t0"], S / own_d)
            e_after = mouth_error(u, own_after, u["t0"])
            own_before = own_scaled(u["parts"], u["t0"] + b, S / own_d)
            e_before = mouth_error(u, own_before, u["t0"] + b)
            broll_first = e_after is not None and e_before is not None and e_before < e_after
            if u.get("broll_where") == "after":
                broll_first = False
            elif u.get("broll_where") == "before":
                broll_first = True
            e = e_before if broll_first else e_after
            if u["lip"] and e is not None and e > limit:
                return None
            t_b = u["t0"] if broll_first else u["t0"] + own_d
            got = fill(b, t_b, broll_need, u["loc"], u["scene"], avoid, mid, narration=not u["dialogue"],
                       max_pieces=max_pieces, prefer_shot=u.get("broll_shot"))
            if not got:
                return None
            own = own_before if broll_first else own_after
            seq = (got + own) if broll_first else (own + got)
            seq = steal_for_min_broll(seq)
            if n_jumps(seq) > MAX_TRANSITIONS:
                for p in got:
                    uses.remove((p["us0"], p["us1"], p["it0"], p["it1"], p["shot"], True))
                return None
            return seq, own_d, b, broll_first, e

        placed = None
        long = is_long_broll_hole(D, S)
        n_shots = max_broll_shots(D, S)
        # 1) One B-roll: first the shortest that reaches 0.80x, then a shorter one (still one cut) if no
        # shot is that long, then a slightly longer one. Never two B-roll shots on a short hole.
        top = b_hi if b_hi >= MIN_BROLL else (D - 0.05)
        btry, tried = [], set()
        x = max(need_b, MIN_BROLL)
        while x >= MIN_BROLL:
            btry.append(x)
            x -= 0.5
        x = max(need_b, MIN_BROLL) + 0.5
        while x <= top + 1e-6:
            btry.append(x)
            x += 0.5
        btry.append(MIN_BROLL)
        for b in btry:
            b = round(b, 2)
            if b in tried or b < MIN_BROLL:
                continue
            tried.add(b)
            placed = place(b, 1)
            if placed is not None:
                break
        # 2) Whole sentence as one B-roll when the own footage is only reaction/neutral narration.
        if placed is None:
            own_types = {p["type"] for p in u["parts"]}
            if not u["dialogue"] and own_types <= {"reaction", "neutral"} and mouth_in(
                    min(p["us0"] for p in u["parts"]), max(p["us1"] for p in u["parts"])) < 0.05:
                got = fill_one(D, u["t0"], broll_need, u["loc"], u["scene"], avoid, mid, narration=not u["dialogue"],
                               prefer_shot=u.get("broll_shot"))
                if got and n_jumps(got) <= MAX_TRANSITIONS:
                    res.update(pieces=got, choice="one B-roll for the whole sentence")
                    return res
        # 3) Short hole (~2 s): do not cut 2–3 B-rolls. Long hole: several ranked shots to reach 0.80x.
        if placed is None and long and n_shots > 1 and hole_broll_s(D, S) >= MIN_BROLL - 1e-6:
            fill_b = max(need_b, hole_broll_s(D, S), MIN_BROLL)
            if b_hi >= MIN_BROLL:
                fill_b = min(fill_b, max(b_hi, MIN_BROLL))
            for n_pcs in range(2, n_shots + 1):
                placed = place(fill_b, n_pcs)
                if placed is not None:
                    break
        if placed is not None:
            seq, own_d, b, broll_first, e = placed
            res.update(pieces=seq,
                       choice=f"clip at {S / own_d:.2f}x + {b:.1f} s B-roll {'before' if broll_first else 'after'}")
            if e is not None and e > 0.3:
                res["problems"].append(f"mouth starts {e:.2f} s from the voice (grouping cut: {e_orig:.2f} s)")
            if n_jumps(seq) > 0:
                res["problems"].append(f"{n_jumps(seq)} transition(s) inside this sentence (cap {MAX_TRANSITIONS})")
            return res
        if need_b > b_hi + 1e-6:
            shot = u["parts"][0]["shot"] if u["parts"] else None
            res.update(pieces=grouping_takes(u["segs"], shot), choice="kept: no room for a 2 s B-roll at 1.25x")
            res["problems"].append(f"needs {D - S / LO:.1f} s of B-roll, below the 2 s minimum, and the clip cannot run faster than 1.25x")
            return res
        shot = u["parts"][0]["shot"] if u["parts"] else None
        res.update(pieces=grouping_takes(u["segs"], shot), choice="kept: no B-roll of these people at this place")
        res["problems"].append(f"no B-roll of {', '.join(broll_need) or 'the place'} at {u['loc']} fits {max(need_b, MIN_BROLL):.1f}-{max(b_hi, 0):.1f} s in one shot")
        return res

    right = [p for p in u["parts"] if p not in wrong]
    if right:
        tot = defaultdict(float)
        for p in right:
            tot[LIBN[p["shot"]].get("location")] += p["us1"] - p["us0"]
        u["loc"] = max(tot, key=tot.get)
        u["scene"] = LIBN[max(right, key=lambda p: p["us1"] - p["us0"])["shot"]]["scene"]
    Sr = sum(p["us1"] - p["us0"] for p in right)
    Dr = sum(p["it1"] - p["it0"] for p in right)
    if right:
        sp = max(Sr / Dr, LO) if Dr > 0 else LO
        own_d = Sr / min(sp, HI)
    else:
        own_d = 0.0
    total_b = D - own_d
    seq, t = [], u["t0"]
    slots, run = [], []
    for p in u["parts"]:
        if p in wrong:
            run.append(p)
        else:
            if run:
                slots.append(("b", run)); run = []
            slots.append(("o", p))
    if run:
        slots.append(("b", run))
    wrong_d = sum(p["it1"] - p["it0"] for p in wrong)
    b_slots = [s for s in slots if s[0] == "b"]
    lengths = {}
    for kind, v in b_slots:
        lengths[id(v)] = (sum(p["it1"] - p["it0"] for p in v) / wrong_d) * total_b if wrong_d else 0
    for kind, v in slots:
        if kind == "o":
            d = (v["us1"] - v["us0"]) / (Sr / own_d) if own_d else 0
            seq.append(dict(shot=v["shot"], us0=v["us0"], us1=v["us1"], it0=t, it1=t + d, row=v["row"], own=True))
            t += d
        else:
            L = lengths[id(v)]
            got = fill(L, t, broll_need, u["loc"], u["scene"], avoid, mid, seq[-1]["shot"] if seq else None,
                       narration=not u["dialogue"], max_pieces=1)
            if got is None:
                got = fill(L, t, broll_need, u["loc"], u["scene"], avoid, mid, seq[-1]["shot"] if seq else None,
                           narration=not u["dialogue"], max_pieces=2)
            if got:
                seq += got
            else:
                src = sum(p["us1"] - p["us0"] for p in v)
                tt = t
                for p in v:
                    d = (p["us1"] - p["us0"]) / src * L if src else 0
                    seq.append(dict(shot=p["shot"], us0=p["us0"], us1=p["us1"], it0=tt, it1=tt + d, row=p["row"], own=True,
                                    kept_wrong=True))
                    tt += d
                res["problems"].append(f"shows {', '.join(v[0]['shown']) or 'someone else'} but should show "
                                       f"{', '.join(broll_need)}; no B-roll of them at {u['loc']} fits {L:.1f} s, so the clip is kept")
            t += L
    seq = steal_for_min_broll(seq)
    res.update(pieces=seq, choice=f"replace {len(wrong)} wrong part(s) with B-roll of {', '.join(broll_need) or 'the place'}")
    if n_jumps(seq) > MAX_TRANSITIONS:
        res["problems"].append(f"{n_jumps(seq)} transition(s) inside this sentence (cap {MAX_TRANSITIONS})")
    return res


def piece_speed(p):
    return (p["us1"] - p["us0"]) / max(p["it1"] - p["it0"], 1e-6)


def person_share(parts, right):
    if not right:
        return None
    tot = hit = 0.0
    for p in parts:
        d = p["it1"] - p["it0"]
        if d <= 0:
            continue
        tot += d
        shown = set(p.get("shown") or cast_of(p["shot"]))
        if shown & right:
            hit += d
    return hit / tot if tot else None


def keep_if_worse(u, r):
    """If the new cut is slower, or shows the right person less, keep the grouping cut."""
    if not r["pieces"] or not u["parts"]:
        return
    right = set(r["need"]) | (set(r["listener"]) if u["dialogue"] else set())
    g_slow, n_slow = min(piece_speed(p) for p in u["parts"]), min(piece_speed(p) for p in r["pieces"])
    g_share, n_share = person_share(u["parts"], right), person_share(r["pieces"], right)
    reasons = []
    if n_slow < g_slow - 0.02:
        reasons.append(f"slower {g_slow:.2f}x to {n_slow:.2f}x")
    if g_share is not None and n_share is not None and n_share < g_share - 0.02:
        reasons.append(f"right person {g_share:.0%} to {n_share:.0%}")
    if not reasons:
        return
    shot = u["parts"][0]["shot"] if u["parts"] else None
    r["pieces"] = grouping_takes(u["segs"], shot)
    why = "; ".join(reasons)
    r["choice"] = f"kept the grouping cut: {why}"
    r["problems"].append(f"new cut was worse than the grouping cut ({why}), so the grouping cut stays")


results = []
for u in units:
    r = solve(u)
    if r.get("pieces"):
        r["pieces"] = steal_for_min_broll(r["pieces"])
    keep_if_worse(u, r)
    out = [(p["us1"] - p["us0"]) / max(p["it1"] - p["it0"], 1e-6) for p in r["pieces"]]
    out = [sp for sp in out if not (LO - 0.005 <= sp <= HI + 0.005)]
    if out:
        r["problems"].append(f"{len(out)} piece(s) still outside 0.8-1.25x (slowest {min(out):.2f}x)")
    results.append(r)

# ---------------- 4. the cut ----------------
new_segs = []
done = set()
for s in grp["segments"]:
    u = next((u for u in units if s["it_idx"] in u["rows"]), None)
    if u is None:
        new_segs.append(dict(s, note=s.get("note") or ""))
        continue
    if u["k"] in done:
        continue
    done.add(u["k"])
    r = results[u["k"]]
    # Every piece is cut at the dub boundaries of the unit's sentences (same picture, the footage is split in the same
    # proportion), and each part plays for the sentence that contains its middle. No sentence is left without a piece.
    bounds = sorted({z[k] / 1000 for x in u["rows"] for z in by_row[x] for k in ("it_start_ms", "it_end_ms")})

    def row_of(mid):
        return next((x for x in u["rows"] for z in by_row[x] if z["it_start_ms"] / 1000 <= mid < z["it_end_ms"] / 1000),
                    u["rows"][0])
    parts_ = []
    for p in r["pieces"]:
        span_ = max(p["it1"] - p["it0"], 1e-9)
        cuts = [p["it0"]] + [t for t in bounds if p["it0"] + 1e-3 < t < p["it1"] - 1e-3] + [p["it1"]]
        for a0, a1 in zip(cuts, cuts[1:]):
            f0 = p["us0"] + (a0 - p["it0"]) / span_ * (p["us1"] - p["us0"])
            f1 = p["us0"] + (a1 - p["it0"]) / span_ * (p["us1"] - p["us0"])
            parts_.append(dict(p, it0=a0, it1=a1, us0=f0, us1=f1))
    for p in parts_:
        row = row_of((p["it0"] + p["it1"]) / 2)
        dub_ms = (p["it1"] - p["it0"]) * 1000
        src_ms = (p["us1"] - p["us0"]) * 1000
        tag = "own clip" if p.get("own") else f"B-roll shot {p['shot']} ({', '.join(_cast_of(p.get('shot'))) or 'place'}, {_shot_kind(p.get('shot'))})"
        if p.get("kept_wrong"):
            tag = "own clip, kept (no B-roll found)"
        if p.get("kept_grouping"):
            tag = "grouping cut kept (new cut was worse)"
        new_segs.append(dict(kind="clip", it_idx=row, it_start_ms=round(p["it0"] * 1000, 1), it_end_ms=round(p["it1"] * 1000, 1),
                             us_start_ms=round(p["us0"] * 1000, 1), us_end_ms=round(p["us1"] * 1000, 1),
                             it_text=DUB.get(row, {}).get("text", ""), us_text="" if not p.get("own") else "",
                             stretch_factor=round(dub_ms / max(src_ms, 1), 3), note=f"slowfix case {r['case']}: {tag}"))
new_segs.sort(key=lambda s: s["it_start_ms"])
# Rounding inside a unit can leave a few ms between two pieces. Close gaps up to 200 ms by extending the earlier piece's
# dub end (its source stays the same, so its speed moves by well under 1%); bigger gaps are reported below.
closed = 0
for a_, b_ in zip(new_segs, new_segs[1:]):
    d_ = b_["it_start_ms"] - a_["it_end_ms"]
    if 0 < d_ <= 200:
        a_["it_end_ms"] = b_["it_start_ms"]
        a_["stretch_factor"] = round((a_["it_end_ms"] - a_["it_start_ms"]) / max(a_["us_end_ms"] - a_["us_start_ms"], 1), 3)
        closed += 1
print(f"closed {closed} rounding gap(s) of up to 200 ms between pieces", flush=True)
gaps = [(a["it_end_ms"], b["it_start_ms"]) for a, b in zip(new_segs, new_segs[1:]) if abs(b["it_start_ms"] - a["it_end_ms"]) > 2]
man = dict(total_ms=grp["total_ms"], segments=new_segs)
json.dump(man, open(OUT / "manifest_slowfix.json", "w"), indent=1, ensure_ascii=False)

save = []
for u, r in zip(units, results):
    save.append(dict(unit=u["k"], rows=u["rows"], dub=[round(u["t0"], 2), round(u["t1"], 2)], speed=round(u["speed"], 2),
                     lip=u["lip"], dialogue=u["dialogue"], location=u["loc"], text=u["text"], prev=u["prev"],
                     shows=[dict(shot=p["shot"], people=p["people"]) for p in u["parts"]], need=r["need"],
                     listener=r["listener"], why=r["why"], case=r["case"], choice=r["choice"], problems=r["problems"],
                     pieces=[dict(p, speed=round((p["us1"] - p["us0"]) / max(p["it1"] - p["it0"], 1e-6), 2)) for p in r["pieces"]]))
json.dump(save, open(OUT / "decisions.json", "w"), indent=1, ensure_ascii=False)

# ---------------- 5. sheet ----------------
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

A = "Arial"
hf = Font(name=A, bold=True, color="FFFFFF", size=11)
hfill = PatternFill("solid", fgColor="1F4E78")
bf = Font(name=A, size=11)
bold = Font(name=A, bold=True, size=11)
thin = Border(*(Side(style="thin", color="D9D9D9"),) * 4)
wrap = Alignment(wrap_text=True, vertical="center")
ok = PatternFill("solid", fgColor="E2EFDA")
bad = PatternFill("solid", fgColor="FCE4D6")
two = PatternFill("solid", fgColor="DDEBF7")


def head(ws, headers, widths):
    for c, h in enumerate(headers, 1):
        cell = ws.cell(1, c, h)
        cell.font = hf; cell.fill = hfill; cell.alignment = wrap
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "A2"
    ws.sheet_view.showGridLines = False


wb = Workbook()
ws = wb.active
ws.title = "Slow clips"
head(ws, ["Unit", "Rows", "Dub time", "Speed before", "Line type", "Previous dub line", "This dub line", "Clip shows now",
          "Should be on screen", "Case", "What was done", "New pieces (shot, people, speed)", "Problems",
          "Clip picture", "First B-roll picture"],
     [7, 12, 14, 10, 12, 38, 44, 34, 30, 8, 34, 56, 40, 50, 50])
for i, s in enumerate(save):
    row = i + 2
    pieces = "\n".join(
        f"{'own' if p.get('own') else 'B-roll'} shot {p['shot']} ({', '.join(_cast_of(p.get('shot'))) or 'nobody'}, {_shot_kind(p.get('shot'))}) "
        f"dub {p['it0']:.1f}-{p['it1']:.1f}s at {p['speed']}x"
        + (" KEPT WRONG" if p.get("kept_wrong") else "")
        + (" GROUPING KEPT" if p.get("kept_grouping") else "")
        for p in s["pieces"])
    shows = "; ".join(f"shot {x['shot']}: {', '.join(x['people']) or 'nobody'}" for x in s["shows"])
    need = ", ".join(s["need"]) or "nobody (place)"
    if s["listener"]:
        need += f" (listener: {', '.join(s['listener'])})"
    need += f"\n{s['why']}"
    vals = [s["unit"], ", ".join(map(str, s["rows"])), f"{s['dub'][0]:.1f}-{s['dub'][1]:.1f}s", s["speed"],
            ("lip " if s["lip"] else "") + ("dialogue" if s["dialogue"] else "narration"), s["prev"], s["text"], shows, need,
            s["case"], s["choice"], pieces, "\n".join(s["problems"]) or "none", None, None]
    fill = bad if s["problems"] else (two if s["case"] == 2 else ok)
    for c, v in enumerate(vals, 1):
        cell = ws.cell(row, c, v)
        cell.font = bold if c in (1, 10) else bf
        cell.alignment = wrap; cell.border = thin
        if c not in (14, 15):
            cell.fill = fill
    own_shot = s["shows"][0]["shot"] if s["shows"] else None
    br = next((p["shot"] for p in s["pieces"] if not p.get("own")), None)
    for col, n in (("N", own_shot), ("O", br)):
        if n is not None and (STRIPS / f"shot_{n:03d}.png").is_file():
            im = XLImage(str(STRIPS / f"shot_{n:03d}.png")); im.width = 330; im.height = 110
            ws.add_image(im, f"{col}{row}")
    ws.row_dimensions[row].height = max(100, 18 * (pieces.count("\n") + 2))

ws2 = wb.create_sheet("Every new piece")
head(ws2, ["Unit", "Dub from", "Dub to", "Kind", "Shot", "People", "Shot type", "Location", "Source from", "Source to", "Speed", "Picture"],
     [7, 10, 10, 18, 8, 30, 14, 26, 11, 11, 8, 50])
row = 2
for s in save:
    for p in s["pieces"]:
        kind = "own clip" if p.get("own") else "B-roll"
        if p.get("kept_wrong"):
            kind = "own clip (kept, wrong)"
        if p.get("kept_grouping"):
            kind = "grouping cut kept"
        # Unused-edge fill may leave shot=None (editor plan allows null). Do not KeyError SH[None].
        n = p.get("shot")
        r = SH.get(n) if n is not None else None
        r = r or {"type": "?", "location": None}
        vals = [s["unit"], round(p["it0"], 2), round(p["it1"], 2), kind, n, ", ".join(people_of(n)) or "nobody",
                r.get("type") or "?", r.get("location"), round(p["us0"], 2), round(p["us1"], 2), p["speed"], None]
        for c, v in enumerate(vals, 1):
            cell = ws2.cell(row, c, v); cell.font = bf; cell.alignment = wrap; cell.border = thin
            if c != 12:
                cell.fill = ok if kind == "B-roll" else (bad if "wrong" in kind else two)
        if n is not None and (STRIPS / f"shot_{n:03d}.png").is_file():
            im = XLImage(str(STRIPS / f"shot_{p['shot']:03d}.png")); im.width = 330; im.height = 110
            ws2.add_image(im, f"L{row}")
        ws2.row_dimensions[row].height = 90
        row += 1

xlsx = OUT / "scene_edit.xlsx"
wb.save(xlsx)

c1 = sum(1 for s in save if s["case"] == 1)
c2 = sum(1 for s in save if s["case"] == 2)
fixed = sum(1 for s in save if not s["problems"])
nb = sum(1 for s in save for p in s["pieces"] if not p.get("own"))
print(f"case 1 (right person, slow): {c1}, case 2 (wrong person): {c2}")
print(f"units fixed with no problem: {fixed} of {len(save)}; B-roll pieces placed: {nb}")
print(f"timeline gaps: {len(gaps)}; who call cost ${cost:.4f}")
for s in save:
    print(f"  unit {s['unit']:2} rows {s['rows']} {s['dub'][0]:.1f}-{s['dub'][1]:.1f}s {s['speed']}x case {s['case']} "
          f"need {s['need']} | {s['choice']}" + (f" | {'; '.join(s['problems'])}" if s["problems"] else ""))
print("saved", OUT / "manifest_slowfix.json")
print("saved", xlsx)
