"""Step 2 by AI: Claude reads the flagged rows by meaning and proposes decisions in the skill's spec format.

Flow: flagged rows (unmatched + Case A, from prep.json) -> clusters of nearby rows -> one Claude call per cluster
with the dub rows around them and the source window they could map to -> JSON answers -> validated spec lines
(G/A/C/GAP, the same format a person writes) -> W/spec_ai.py + W/ai_proposals.json.
Nothing is applied automatically: the editor reviews the proposals on Check matching, then they are imported
with decisions.from_spec (which re-checks every index against its text).

CLI:  python -m dubstudio.pipeline.ai_match <work_dir> [--model claude-sonnet-5-5] [--apply]
"""
import argparse, json, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .. import ai
from . import decisions as dec

RULES = """You are the Step-2 reader of a dubbing pipeline (skill manual "Step 2. The manual read"). An Italian (dub)
episode is an ADAPTATION of a source episode (English or German): lines are merged, split, shortened, re-ordered,
dropped or added, and characters have Italian names (see the name list). Each dub row must be shown with the source
footage whose MEANING matches it (who does what, the beat of the story). Judge by MEANING, never by score: true
matches score as low as 0.11, wrong ones up to 0.65.

How to read one row (the n-1/n+1 method):
1. Look at the neighbours (n-1, n+1) and the source sentence each is matched to.
2. Ask whether that sentence also carries this row's meaning (a clause, a paraphrase, the same beat):
   "Si domandò." = the "...she wondered." tail; "Muoviti." = the "...quickly" of "We must follow after Edgar
   quickly."; "Poteva incassare il colpo." = "...but she could handle it." -> then the rows are a GROUP on it.
3. If neither neighbour covers it, go to n+-2; a pair can grow into a 3- or 4-row group.
4. Check nearby UNUSED source lines ("Viola si fece avanti." = "Heather made her presence known.").
5. Short lines the adaptation added -- an interjection ("Merda."), an inner question ("E adesso?"), a thought
   ("Devo andarmene da qui.") or an order ("Smettila, le ordino Demetrio.") -- belong to the beat around them: group
   them with the neighbour whose footage shows that moment, or give them an unused source line of the same moment.
   BUT a line the adaptation added that tells an EVENT or detail no source line tells (e.g. "she grabbed a coffee cup
   and smashed it against the wall" where the source only has "God, you are a terrible liar.") is a GAP: never give
   it the footage of another sentence (owner, 2026-10-29). A gap is not black: the scene edit covers it with B-roll
   (a reaction / neutral shot of the same place), never with another line's footage.
6. A row often tells TWO source sentences (the adaptation merged them): give it the span [a,b], so its clip shows
   the whole moment and does not play in slow motion. The same for a nearby UNUSED line that belongs to its beat.
7. A spoken line (quotes, "disse", a question to someone) should show the character who says it (mind the name list).
8. Declare a genuine gap only after all of this finds nothing (that is rare).

Which decision to write (one per flagged row):
- "group": rows that are clauses of one source sentence (or a short added line with its neighbour's beat): give ALL
  member rows (consecutive, in dub order, neighbours included even if not flagged) and the source sentence or span.
- "assign": the row plays source sentence S (int) or consecutive lines [a,b] that tell the same beat (also overrides a
  wrong automatic match). A row that would play very slow: group it with a real neighbour instead -- only when that
  neighbour's sentence carries this row's meaning (an added event the source never tells stays a gap).
- "confirm": the suggested source (shown as -> [S]) is right.
- "gap": nothing fits, after steps 1-5. Say in the reason what you checked.
Prefer story order. A source sentence may serve several dub rows only through a group. Two neighbouring rows must
never show the same source footage as separate rows: make them one group.

Answer ONLY with JSON: {"decisions":[{"row":R,"action":"assign|group|confirm|gap","src":S or [a,b] or null,
"members":[rows] (group only),"reason":"short, by meaning","confidence":0.0-1.0}]} with one entry per flagged row."""


def _fmt_ms(ms):
    return f"{ms / 1000:6.1f}"


def _clusters(rows, gap=5):
    out = []
    for r in sorted(rows):
        if out and r - out[-1][-1] <= gap:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def load_names(path, texts, limit=150):
    """The show's localisation sheet (source name -> target name), all of it when it is short (else the names that
    occur in this episode). See dubstudio/names.py for the formats."""
    from .. import names as nm
    if not path:
        return []
    return nm.for_episode(nm.read(path)["names"], texts, limit)


def _names_txt(names):
    return ("Localisation sheet, character names (source video name -> dub name): "
            + "; ".join(f"{o} -> {l}" for o, l in names) + "\n\n") if names else ""


def _used(p, case_a, answered=None):
    """source index -> dub rows using it (automatic matches, plus answers already given)."""
    u = {}
    for k, v in p["auto"].items():
        if int(k) not in case_a:
            u.setdefault(v["src"], []).append(int(k))
    for r, d in (answered or {}).items():
        s_ = d.get("src")
        if d.get("action") in ("assign", "group") and s_ is not None:
            for j in (range(s_[0], s_[1] + 1) if isinstance(s_, list) and len(s_) == 2 else [s_]):
                if isinstance(j, int):
                    u.setdefault(j, []).append(r)
    return u


def _context(p, cluster, case_a, nb=4, pad=4, answered=None):
    dub, src, auto, sim = p["dub"], p["src"], p["auto"], p.get("sim")
    used = _used(p, case_a, answered)
    lo, hi = max(0, cluster[0] - nb), min(len(dub) - 1, cluster[-1] + nb)
    srcs = [auto[str(i)]["src"] for i in range(lo, hi + 1) if str(i) in auto and i not in case_a]
    s_lo = max(0, (min(srcs) if srcs else 0) - pad); s_hi = min(len(src) - 1, (max(srcs) if srcs else len(src) - 1) + pad)
    extra = set()
    if sim:
        for r in cluster:
            extra |= set(sorted(range(len(src)), key=lambda j: -sim[r][j])[:4])
    lines = ["DUB rows (index, start s, text, -> current automatic source):"]
    for i in range(lo, hi + 1):
        a = auto.get(str(i)); flag = "  <== FLAGGED" if i in cluster else ""
        tag = f"-> [{a['src']}] {a.get('kind', '')} {a.get('score', 0):.2f}" if a else "-> (no match)"
        lines.append(f"D{i:>4} {_fmt_ms(dub[i].get('start_ms', 0))}  {dub[i]['text']}  {tag}{flag}")
    lines.append("\nSOURCE sentences in this part of the episode (used by which dub rows, or UNUSED):")
    for j in range(s_lo, s_hi + 1):
        tag = f"(used by D{', D'.join(map(str, sorted(set(used[j]))))})" if j in used else "(UNUSED)"
        lines.append(f"S{j:>4} {_fmt_ms(src[j].get('start_ms', 0))}  {src[j]['text']}  {tag}")
    far = sorted(j for j in extra if j < s_lo or j > s_hi)
    if far:
        lines.append("\nOther source sentences that score high for the flagged rows (far away; use only if the meaning is clearly this):")
        for j in far:
            lines.append(f"S{j:>4} {_fmt_ms(src[j].get('start_ms', 0))}  {src[j]['text']}")
    lines.append(f"\nFLAGGED rows to decide: {cluster}")
    return "\n".join(lines), (s_lo, s_hi)


def _kw(text, n=3):
    w = text.split()
    return " ".join(w[:n]) if w else text


def _valid_src(s, n):
    if isinstance(s, int):
        return 0 <= s < n
    return isinstance(s, list) and len(s) == 2 and all(isinstance(x, int) and 0 <= x < n for x in s) and s[0] <= s[1]


SECOND = """A first read answered "gap" (no footage) for dub row D{row}: "{reason}".
Read it again with the manual's checks, one by one, before accepting a gap:
1. Does the source sentence of D{prev} or D{next} carry this row's beat (a clause, a paraphrase, the same moment,
   a short reaction, thought or order inside that moment -- NOT an added event the source never tells)? -> "group" with that neighbour (members = the
   consecutive rows, the neighbour included; src = the neighbour's source sentence or a span over both).
2. The same with D{prev2} / D{next2}.
3. Is there an UNUSED source line between the neighbours' sources that shows this moment (same characters --
   mind the name list -- same action, story order)? -> "assign" it.
4. Only if all fail: "gap", saying what you checked.{note}
Answer ONLY with JSON {{"decisions":[{{"row":{row},"action":...,"src":...,"members":[...],"reason":"...","confidence":0.0-1.0}}]}}."""


def _ask(prompt, model, usage, log, label):
    text, u = ai.chat(prompt, model=model, max_tokens=3000)
    usage["calls"] += 1
    usage["prompt_tokens"] += u.get("prompt_tokens", 0); usage["completion_tokens"] += u.get("completion_tokens", 0)
    try:
        return ai.extract_json(text, "decisions").get("decisions", [])
    except Exception as ex:
        log(f"{label}: could not read reply ({ex})"); return []


def second_look(p, rows, props, case_a, names, model, usage, log, notes=None, workers=4):
    """Rows answered "gap" (or clashing) are read once more, alone, with the manual's checks spelled out.
    Returns {row: new answer} for the rows that now have footage."""
    def one(r):
        prev_ = props.get(r) or {}
        ctx, _ = _context(p, [r], case_a, nb=5, pad=6, answered={k: v for k, v in props.items() if k != r})
        q = SECOND.format(row=r, reason=(prev_.get("reason") or "no answer").replace('"', "'"), prev=r - 1, next=r + 1,
                          prev2=r - 2, next2=r + 2, note=("\n" + notes[r]) if notes and r in notes else "")
        ans = _ask(RULES + "\n\n" + _names_txt(names) + ctx + "\n\n" + q, model, usage, log, f"row {r} (second look)")
        return r, next((d for d in ans if d.get("row") == r or (isinstance(d.get("row"), str) and d["row"].strip() == str(r))), None)
    out = {}
    with ThreadPoolExecutor(workers) as ex:
        for r, d in ex.map(one, rows):
            if d and d.get("action") in ("assign", "group", "confirm") and (d.get("src") is not None or d.get("action") == "confirm"):
                out[r] = d
    return out


def _build(p, flagged, props, model):
    """Answers -> (spec text, proposal records) in the skill's spec language."""
    nd, ns = len(p["dub"]), len(p["src"])
    spec, done, out = [], set(), []
    for r in flagged:
        d = props.get(r)
        if not d:
            out.append({"row": r, "action": "none", "error": "no answer"}); continue
        act, s, why = d.get("action"), d.get("src"), (d.get("reason") or "").replace('"', "'")
        conf = d.get("confidence")
        tag = f"AI {model or ai.settings()['AI_MODEL']} ({conf}): {why}"
        rec = {"row": r, "action": act, "src": s, "members": d.get("members"), "reason": why, "confidence": conf}
        if d.get("second_look"):
            rec["second_look"] = True
        if r in done:
            rec["note"] = "covered by an earlier group"; out.append(rec); continue
        if act == "gap" or (act in ("assign", "group") and s is None):
            spec.append(f'GAP({r},{json.dumps(_kw(p["dub"][r]["text"]), ensure_ascii=False)},{json.dumps(tag, ensure_ascii=False)})')
        elif act == "confirm":
            spec.append(f'C({r},{json.dumps(tag, ensure_ascii=False)})')
        elif act == "group" and _valid_src(s, ns):
            mem = sorted({m for m in (d.get("members") or []) if isinstance(m, int) and 0 <= m < nd and m not in done} | {r})
            if mem != list(range(mem[0], mem[-1] + 1)):
                mem = [r]                       # members must be consecutive; fall back to a single assign
            if len(mem) == 1:
                spec.append(f'A({r},{json.dumps(_kw(p["dub"][r]["text"]), ensure_ascii=False)},{json.dumps(s)},'
                            f'{json.dumps(_kw(p["src"][s if isinstance(s, int) else s[0]]["text"]), ensure_ascii=False)},{json.dumps(tag, ensure_ascii=False)})')
            else:
                m_list = ",".join(f'({m},{json.dumps(_kw(p["dub"][m]["text"]), ensure_ascii=False)})' for m in mem)
                spec.append(f'G([{m_list}],{json.dumps(s)},{json.dumps(_kw(p["src"][s if isinstance(s, int) else s[0]]["text"]), ensure_ascii=False)},{json.dumps(tag, ensure_ascii=False)})')
                done |= set(mem); rec["members"] = mem
        elif act == "assign" and _valid_src(s, ns):
            spec.append(f'A({r},{json.dumps(_kw(p["dub"][r]["text"]), ensure_ascii=False)},{json.dumps(s)},'
                        f'{json.dumps(_kw(p["src"][s if isinstance(s, int) else s[0]]["text"]), ensure_ascii=False)},{json.dumps(tag, ensure_ascii=False)})')
        else:
            rec["error"] = "invalid answer"; out.append(rec); continue
        done.add(r); out.append(rec)
    return "# Step 2 proposals by AI (review before importing)\n" + "\n".join(spec) + "\n", out


def propose(work, model=None, workers=4, log=print, names_csv=None):
    w = Path(work)
    p, unmatched, case_a = dec.flagged_rows(w)
    flagged = sorted(unmatched + case_a)
    if not flagged:
        return {"proposals": [], "spec": "", "usage": {}}
    nd, ns = len(p["dub"]), len(p["src"])
    clusters = _clusters(flagged)
    usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    names = load_names(names_csv, [x["text"] for x in p["dub"] + p["src"]]) if names_csv else []
    if names:
        log(f"name list: {len(names)} characters ({', '.join(f'{o}->{l}' for o, l in names[:6])}…)")

    def run(cl):
        ctx, _ = _context(p, cl, set(case_a))
        text, u = ai.chat(RULES + "\n\n" + _names_txt(names) + ctx, model=model, max_tokens=3000)
        try:
            ans = ai.extract_json(text, "decisions").get("decisions", [])
        except Exception as ex:
            ans = []; log(f"cluster {cl}: could not read reply ({ex})")
        return cl, ans, u

    props = {}

    def collect(results):
        for cl, ans, u in results:
            usage["calls"] += 1
            usage["prompt_tokens"] += u.get("prompt_tokens", 0); usage["completion_tokens"] += u.get("completion_tokens", 0)
            for d in ans:
                cover = [d.get("row")] + list(d.get("members") or [])   # a group answer also decides its members
                for r in cover:
                    r = int(r) if isinstance(r, str) and r.strip().isdigit() else r
                    if isinstance(r, int) and r in cl and r not in props:
                        props[r] = d
            log(f"rows {cl}: {len([r for r in cl if r in props])}/{len(cl)} answered")

    with ThreadPoolExecutor(workers) as ex:
        collect(list(ex.map(run, clusters)))
        again = [r for r in flagged if r not in props]          # a row the reply skipped: ask once more, alone
        if again:
            log(f"asking again for {again}")
            collect(list(ex.map(run, [[r] for r in again])))

    # second look: every row answered "gap" (or not answered) is read again alone, with the manual's checks
    gaps = [r for r in flagged if (props.get(r) or {}).get("action") in (None, "gap") or (props.get(r) or {}).get("src") is None
            and (props.get(r) or {}).get("action") != "confirm"]
    if gaps:
        log(f"second look at {len(gaps)} rows answered 'no footage': {gaps}")
        found = second_look(p, gaps, props, set(case_a), names, model, usage, log, workers=workers)
        for r, d in found.items():
            props[r] = dict(d, second_look=True)
        log(f"second look found footage for {len(found)} of {len(gaps)}: {sorted(found)}")

    spec_text, out = _build(p, flagged, props, model)
    (w / "spec_ai.py").write_text(spec_text)
    (w / "ai_proposals.json").write_text(json.dumps({"model": model or ai.settings()["AI_MODEL"], "usage": usage,
                                                     "proposals": out, "names": len(names)}, indent=1, ensure_ascii=False))
    return {"proposals": out, "spec": spec_text, "usage": usage, "_props": props, "_names": names, "_case_a": case_a}


def apply_group_rule(work, result, check, log=print):
    """After the AI answered: run the pipeline's integrity check on its spec; neighbouring rows that still share
    footage are joined into groups (decisions.join_shared), and the proposals are rewritten to match, so the editor
    gets proposals that pass. `check(spec) -> {"overlaps": [[a, b, ms], ...], ...}`. Adds result["joined"]."""
    w = Path(work)
    r = check(result["spec"])
    result["joined"], result["left"] = [], []
    if not r.get("overlaps"):
        result["check"] = r; return result
    new_spec, joined, left = dec.join_shared(w, result["spec"], r["overlaps"])
    result["joined"], result["left"] = joined, left
    log(f"group rule: joined rows {', '.join(f'{a}-{b}' for a, b in joined)}" + (f"; left {left}" if left else ""))
    # the joined groups as proposals: every flagged row inside a joined run becomes a member of that group
    p = json.loads((w / "prep.json").read_text())
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "prep.json").write_text(json.dumps(p))
        dec.from_spec(tmp, new_spec)
        groups = json.loads((Path(tmp) / "decisions.json").read_text())["groups"]
    by_first = {g["members"][0]: g for g in groups}
    for a, b in joined:
        g = by_first.get(a)
        if not g:
            continue
        for rec in result["proposals"]:
            if a <= rec["row"] <= b:
                rec.update(action="group", src=g["src"], members=list(range(a, b + 1)), error=None,
                           reason=f"rows {a}-{b} share source footage, so they are one group. " + (rec.get("reason") or ""))
    result["spec"] = "# Step 2 proposals by AI (review before importing); neighbours on one footage joined\n" + \
        "\n".join(l for l in new_spec.splitlines() if not l.startswith("#")) + "\n"
    result["check"] = check(result["spec"])
    (w / "spec_ai.py").write_text(result["spec"])
    meta = json.loads((w / "ai_proposals.json").read_text())
    meta.update(proposals=result["proposals"], joined=joined, left=left)
    (w / "ai_proposals.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    return result


REVIEW = """You are now doing the FULL read of the manual (Step 2): every dub row is checked, not only the flagged
ones. The automatic matcher pairs sentences by similarity and is often right, but it makes these mistakes, which a
person reading the episode fixes:
- a dub row that tells the beat of TWO source sentences gets only one of them (the other stays unused or goes to a
  neighbour), so its clip shows half the moment and plays in slow motion -> "assign" the span [a,b];
- two or three dub rows that are clauses of ONE source sentence are spread over neighbouring sentences ->
  "group" them on that sentence (or span);
- a row sent to the wrong moment (another character, another action, out of story order) -> "assign" the right line.
Look at each row in "Rows to check" with its neighbours (n-1/n+1, then n+-2). Judge by MEANING: who does what,
who speaks (mind the name list; a spoken line should show the character who says it), the story order. The duration
column helps: "x2.1 slow" means the clip would play 2.1 times slower than real time, a sign that the row needs more
footage (a span, or a group with its neighbour) -- but only footage that tells the same beat: a row that tells an event
no source line tells (an added action) stays a gap, never inside another row's group. Change a row ONLY when you are sure the current source is wrong or
incomplete; leave correct rows alone. Never give a row footage another row shows unless you group the two.

Answer ONLY with JSON: {"decisions":[{"row":R,"action":"assign|group","src":S or [a,b],"members":[rows] (group only,
consecutive, in dub order),"reason":"short, by meaning","confidence":0.0-1.0}]} listing ONLY the rows you change
(an empty list when every row is right)."""


def _decided(p, D, case_a):
    """-> {row: ("group", gid) | ("assign", src) | ("auto", src) | ("gap", None)}, {gid: group}"""
    out, groups = {}, {}
    for k, v in p["auto"].items():
        if int(k) not in case_a:
            out[int(k)] = ("auto", v["src"])
    for k in D.get("confirm", {}):
        if k in p["auto"]:
            out[int(k)] = ("auto", p["auto"][k]["src"])
    for k, a in D.get("assign", {}).items():
        out[int(k)] = ("assign", a["src"])
    for gi, g in enumerate(D.get("groups", [])):
        groups[gi] = dict(g)
        for m in g["members"]:
            out[m] = ("group", gi)
    for k in D.get("gap", {}):
        out[int(k)] = ("gap", None)
    return out, groups


def review_rows(work, p, spec, names, model, usage, log, case_a=(), window=12, workers=4):
    """Claude reads EVERY dub row (windows of ~12 rows with their neighbours) and corrects the automatic matches a
    person would correct: half beats, clauses spread over neighbours, wrong moments. -> (new spec, report)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "prep.json").write_text(json.dumps(p)); dec.from_spec(tmp, spec)
        D = json.loads((Path(tmp) / "decisions.json").read_text())
    dub, src = p["dub"], p["src"]; nd, ns = len(dub), len(src)
    cur = _current(p, D, set(case_a))

    def block(lo, hi):
        a0, a1 = max(0, lo - 3), min(nd - 1, hi + 3)
        lines = ["DUB rows (index, start s, length s, text, -> source lines shown now, speed):"]
        for i in range(a0, a1 + 1):
            c = cur.get(i); d_ms = max(1, dub[i].get("end_ms", 0) - dub[i].get("start_ms", 0))
            if c:
                s_ms = max(1, src[c[1]].get("end_ms", 0) - src[c[0]].get("start_ms", 0)); f = d_ms / s_ms
                spd = f"x{f:.1f} slow" if f > 1.3 else (f"x{1 / f:.1f} fast" if f < 0.7 else "ok")
                tag = f"-> S{c[0]}" + (f"-S{c[1]}" if c[1] != c[0] else "") + f" ({spd})"
            else:
                tag = "-> (no footage)"
            mark = "" if lo <= i <= hi else "   (context)"
            lines.append(f"D{i:>4} {_fmt_ms(dub[i].get('start_ms', 0))} {d_ms / 1000:4.1f}s  {dub[i]['text']}  {tag}{mark}")
        ss = [cur[i][0] for i in range(a0, a1 + 1) if i in cur] + [cur[i][1] for i in range(a0, a1 + 1) if i in cur]
        s_lo, s_hi = (max(0, min(ss) - 3), min(ns - 1, max(ss) + 3)) if ss else (0, min(ns - 1, 20))
        lines.append("\nSOURCE sentences (shown under which dub rows, or UNUSED):")
        for j in range(s_lo, s_hi + 1):
            users = [f"D{r}" for r, (x, y) in cur.items() if x <= j <= y]
            lines.append(f"S{j:>4} {_fmt_ms(src[j].get('start_ms', 0))}  {src[j]['text']}  "
                         + (f"(shown under {', '.join(users)})" if users else "<== UNUSED"))
        lines.append(f"\nRows to check: D{lo} to D{hi}")
        return "\n".join(lines)

    wins = [(lo, min(nd - 1, lo + window - 1)) for lo in range(0, nd, window)]
    def run(w_):
        return w_, _ask(RULES + "\n\n" + REVIEW + "\n\n" + _names_txt(names) + block(*w_), model, usage, log,
                        f"review D{w_[0]}-D{w_[1]}")
    changes = {}
    with ThreadPoolExecutor(workers) as ex:
        for (lo, hi), ans in ex.map(run, wins):
            for d in ans or []:
                try:
                    r = int(d.get("row"))
                except (TypeError, ValueError):
                    continue
                if lo <= r <= hi and d.get("action") in ("assign", "group") and _valid_src(d.get("src"), ns):
                    changes[r] = d
    dec_of, groups = _decided(p, D, set(case_a))
    applied = []
    for r in sorted(changes):
        d = changes[r]
        if d["action"] == "group":
            mem = sorted({m for m in (d.get("members") or []) if isinstance(m, int) and 0 <= m < nd} | {r})
            if len(mem) < 2 or mem != list(range(mem[0], mem[-1] + 1)) or any(m in applied for m in mem):
                continue
        else:
            mem = [r]
            if r in applied:
                continue
        for m in mem:                                     # take the rows out of what they were in
            k = dec_of.get(m)
            if k and k[0] == "group":
                g = groups.get(k[1])
                if g:
                    g["members"] = [x for x in g["members"] if x != m]
        gid = max(groups, default=-1) + 1
        if len(mem) > 1:
            groups[gid] = {"members": mem, "src": d["src"], "reason": "AI full read: " + (d.get("reason") or "")}
            for m in mem: dec_of[m] = ("group", gid)
        else:
            dec_of[r] = ("assign", d["src"])
            D.setdefault("assign", {})[str(r)] = {"src": d["src"], "reason": "AI full read: " + (d.get("reason") or "")}
        for m in mem:
            D.get("confirm", {}).pop(str(m), None); D.get("gap", {}).pop(str(m), None)
            if len(mem) > 1: D.get("assign", {}).pop(str(m), None)
        applied += mem
    newg = []
    for gi, g in groups.items():                          # groups that lost members: keep the rest
        mm = sorted(g["members"])
        if len(mm) >= 2 and mm == list(range(mm[0], mm[-1] + 1)):
            newg.append(dict(g, members=mm))
        else:
            for m in mm:
                D.setdefault("assign", {})[str(m)] = {"src": g["src"], "reason": g.get("reason", "")}
    D["groups"] = newg
    rep = {"windows": len(wins), "changed": sorted(applied),
           "why": {r: changes[r].get("reason") for r in sorted(changes) if r in applied}}
    log(f"full read of all {nd} rows: {len(applied)} rows changed {sorted(applied)[:30]}")
    return dec.to_spec(p, D, "# Step 2 decisions by AI (Dub Studio, automatic) + full read"), rep


UNUSED = """Some SOURCE sentences are not shown under any dub row yet (marked UNUSED below). In the manual read every
source line is accounted for: an unused line usually belongs to the beat of the dub row just before or just after it
(the adaptation merged it: "Fabio si sistemò a sedere, palesemente irritato." also tells "His head turned" and
"Jason bristled"), and then that row's footage is extended over it, so the row shows the whole beat and plays at a
natural speed. Only when the dub really leaves that moment out is the line left unused.

For EACH unused source line below decide:
- "prev": its meaning is part of the dub row shown just BEFORE it (that row's footage is extended to include it);
- "next": its meaning is part of the dub row shown just AFTER it;
- "none": the dub does not tell this moment (say what it is, e.g. "a line the Italian leaves out").
Judge by MEANING (who does what), mind the name list. Prefer "prev"/"next" when the beat is there even if shortened.

Answer ONLY with JSON: {"unused":[{"src":S,"attach":"prev|next|none","reason":"short","confidence":0.0-1.0}]}"""


def _current(p, D, case_a):
    """dub row -> (first, last) source line it shows now (decisions over the automatic match); None = no footage."""
    cur = {}
    for k, v in p["auto"].items():
        if int(k) not in case_a:
            cur[int(k)] = (v["src"], v["src"])
    for k, why in D.get("confirm", {}).items():
        if k in p["auto"]:
            cur[int(k)] = (p["auto"][k]["src"],) * 2
    rng = lambda s: (s, s) if isinstance(s, int) else (s[0], s[1])
    for k, a in D.get("assign", {}).items():
        cur[int(k)] = rng(a["src"])
    for g in D.get("groups", []):
        for m in g["members"]:
            cur[m] = rng(g["src"])
    for k in D.get("gap", {}):
        cur.pop(int(k), None)
    return cur


def review_unused(work, p, spec, names, model, usage, log, case_a=(), per_call=8, workers=4):
    """The manual read's last check, done by Claude: every source line no dub row shows yet is either given to the
    dub row just before/after it (that row's footage is extended over it, as a person reading the episode does) or
    noted as left out. Works on the whole episode, not only the flagged rows. -> (new spec, report)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "prep.json").write_text(json.dumps(p)); dec.from_spec(tmp, spec)
        D = json.loads((Path(tmp) / "decisions.json").read_text())
    dub, src = p["dub"], p["src"]
    cur = _current(p, D, set(case_a))
    used = {j for a, b in cur.values() for j in range(a, b + 1)}
    first, last = (min(used), max(used)) if used else (0, -1)
    unused = [j for j in range(first, last + 1) if j not in used and str(j) not in D.get("unused_notes", {})]
    report = {"unused": unused, "attached": [], "left": []}
    if not unused:
        return spec, report
    def nb(j):
        prev = max((r for r, (a, b) in cur.items() if b < j), key=lambda r: (cur[r][1], r), default=None)
        nxt = min((r for r, (a, b) in cur.items() if a > j), key=lambda r: (cur[r][0], -r), default=None)
        return prev, nxt
    items = [(j,) + nb(j) for j in unused]
    def block(chunk):
        rows = sorted({r for _, a, b in chunk for r in (a, b) if r is not None})
        lo, hi = max(0, rows[0] - 2), min(len(dub) - 1, rows[-1] + 2)
        lines = ["DUB rows (index, start s, text, -> source lines it shows now):"]
        for i in range(lo, hi + 1):
            c = cur.get(i)
            lines.append(f"D{i:>4} {_fmt_ms(dub[i].get('start_ms', 0))}  {dub[i]['text']}  -> "
                         + (f"S{c[0]}" + (f"-S{c[1]}" if c[1] != c[0] else "") if c else "(no footage)"))
        s_lo = min(min(cur[r][0] for r in rows if r in cur), min(j for j, _, _ in chunk))
        s_hi = max(max(cur[r][1] for r in rows if r in cur), max(j for j, _, _ in chunk))
        lines.append("\nSOURCE sentences (UNUSED = shown under no dub row):")
        for j in range(max(0, s_lo - 1), min(len(src) - 1, s_hi + 1) + 1):
            users = [f"D{r}" for r, (a, b) in cur.items() if a <= j <= b]
            lines.append(f"S{j:>4} {_fmt_ms(src[j].get('start_ms', 0))}  {src[j]['text']}  "
                         + (f"(shown under {', '.join(users)})" if users else "<== UNUSED"))
        lines.append("\nUnused lines to decide: " + "; ".join(
            f"S{j} (row before: {'D' + str(a) if a is not None else 'none'}, row after: {'D' + str(b) if b is not None else 'none'})"
            for j, a, b in chunk))
        return "\n".join(lines)
    chunks, curc = [], []
    for it in items:                                    # neighbouring unused lines go in one request
        if curc and (len(curc) >= per_call or it[0] - curc[-1][0] > 12):
            chunks.append(curc); curc = []
        curc.append(it)
    if curc:
        chunks.append(curc)
    def run(chunk):
        return chunk, _ask_key(UNUSED + "\n\n" + _names_txt(names) + block(chunk), model, usage, log,
                               f"unused S{chunk[0][0]}-S{chunk[-1][0]}")
    answers = {}
    with ThreadPoolExecutor(workers) as ex:
        for chunk, ans in ex.map(run, chunks):
            for d in ans or []:
                try:
                    answers[int(d.get("src"))] = d
                except (TypeError, ValueError):
                    pass
    ext = {}                                             # dub row -> new (first, last)
    for j, a, b in items:
        d = answers.get(j) or {}
        how = (d.get("attach") or "none").lower()
        r = a if how == "prev" else b if how == "next" else None
        if r is None or r not in cur:
            report["left"].append(j)
            D.setdefault("unused_notes", {})[str(j)] = "AI: " + (d.get("reason") or "not told in the dub")
            continue
        f0, f1 = ext.get(r, cur[r])
        ext[r] = (min(f0, j), max(f1, j))
        report["attached"].append([j, r, d.get("reason") or ""])
    in_group = {m: g for g in D.get("groups", []) for m in g["members"]}
    for r, (f0, f1) in ext.items():
        if any(j in used for j in range(f0, f1 + 1) if not (cur[r][0] <= j <= cur[r][1])):
            continue                                     # would take another row's footage: leave it
        new = f0 if f0 == f1 else [f0, f1]
        why = "AI review: also shows " + ", ".join(f"S{j}" for j, rr, _ in report["attached"] if rr == r)
        if r in in_group:
            g = in_group[r]; g0 = g["src"] if isinstance(g["src"], list) else [g["src"], g["src"]]
            f0, f1 = min(f0, g0[0]), max(f1, g0[1]); new = f0 if f0 == f1 else [f0, f1]
            g["src"] = new; g["reason"] = (g.get("reason") or "") + "; " + why
        else:
            D.setdefault("confirm", {}).pop(str(r), None)
            old = D.get("assign", {}).get(str(r), {}).get("reason")
            D.setdefault("assign", {})[str(r)] = {"src": new, "reason": ((old + "; ") if old else "") + why}
    log(f"review of unused source lines: {len(unused)} unused, {len(report['attached'])} given to a neighbour row, "
        f"{len(report['left'])} noted as left out")
    return dec.to_spec(p, D, "# Step 2 decisions by AI (Dub Studio, automatic) + review of unused source lines"), report


def _ask_key(prompt, model, usage, log, label, key="unused"):
    text, u = ai.chat(prompt, model=model, max_tokens=3000)
    usage["calls"] += 1
    usage["prompt_tokens"] += u.get("prompt_tokens", 0); usage["completion_tokens"] += u.get("completion_tokens", 0)
    try:
        return ai.extract_json(text, key).get(key, [])
    except Exception as ex:
        log(f"{label}: could not read reply ({ex})"); return []


AUTO = "ai_auto.json"
LOW_CONFIDENCE = 0.7


def _conf(c):
    try:
        return float(c)
    except (TypeError, ValueError):
        return {"high": 0.9, "medium": 0.6, "low": 0.3}.get(str(c).lower(), 1.0)


def _drop_clashes(w, p, spec, overlaps, conf):
    """For each pair of rows on the same footage: the row the AI (or the matcher) is less sure of loses its footage
    and becomes a gap. Rows inside a group are kept. -> (new spec, [dropped rows])."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "prep.json").write_text(json.dumps(p))
        dec.from_spec(tmp, spec)
        D = json.loads((Path(tmp) / "decisions.json").read_text())
    group_of = {m for g in D["groups"] for m in g["members"]}
    dropped = []
    for a, b, *_ in overlaps:
        if a in dropped or b in dropped:
            continue
        cands = [x for x in (a, b) if x not in group_of]
        if not cands:
            # both rows sit in groups (2026-10-27, TGS ep32 rows 93/99/100): the less sure row that is the FIRST or LAST
            # member of its group leaves the group and becomes a gap; a group left with one row becomes that row's assign
            edge = []
            for x in (a, b):
                g = next(g for g in D["groups"] if x in g["members"])
                if x in (g["members"][0], g["members"][-1]):
                    edge.append((x, g))
            if not edge:
                continue
            x, g = min(edge, key=lambda e: (conf.get(e[0], 0.95), -e[0])); other = b if x == a else a
            g["members"] = [m for m in g["members"] if m != x]
            if len(g["members"]) == 1:
                D["groups"].remove(g)
                D["assign"][str(g["members"][0])] = {"src": g["src"], "reason": g.get("reason", "")}
            D["gap"][str(x)] = f"not found: its footage is also used by row {other} (AI matching); add a clip"
            dropped.append(x); group_of = {m for g in D["groups"] for m in g["members"]}
            continue
        x = min(cands, key=lambda r: (conf.get(r, 0.95), -r))          # least sure; on a tie the later row
        other = b if x == a else a
        D["assign"].pop(str(x), None); D["confirm"].pop(str(x), None)
        D["gap"][str(x)] = f"not found: its footage is also used by row {other} (AI matching); add a clip"
        dropped.append(x)
    return dec.to_spec(p, D, "# Step 2 decisions by AI (Dub Studio, automatic)"), dropped


def _fill_gaps(w, p, spec):
    """Flagged rows the spec leaves open become gaps ("not found")."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "prep.json").write_text(json.dumps(p))
        dec.from_spec(tmp, spec)
        left = dec.missing(tmp) or []
    extra = [f'GAP({i},{json.dumps(_kw(p["dub"][i]["text"]), ensure_ascii=False)},"not found: the AI could not place this row")'
             for i in left]
    return spec.rstrip("\n") + ("\n" + "\n".join(extra) if extra else "") + "\n"


def auto_match(work, check, model=None, log=print, names_csv=None, review_unused_lines=True,
               review_all_rows=True):
    """The decisions step without a person: Claude decides every flagged row (with the show's names, and a second
    look at every "no footage" answer), neighbours on one footage are joined into groups, a row whose footage clashes
    with a row further away is read once more, and only what still has no place becomes a gap ("not found": red in
    the editor, to fill by hand). Imported once the pipeline's integrity check passes. Writes W/ai_auto.json."""
    w = Path(work)
    prep_mtime = (w / "prep.json").stat().st_mtime
    r = propose(w, model, log=log, names_csv=names_csv)
    r = apply_group_rule(w, r, check, log=log)
    p = json.loads((w / "prep.json").read_text())
    props, names, case_a = r.get("_props") or {}, r.get("_names") or [], set(r.get("_case_a") or [])
    flagged = sorted(props) if props else []
    usage = r.setdefault("usage", {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
    spec = _fill_gaps(w, p, r["spec"])
    review = None
    if review_all_rows:
        try:
            spec, review_full = review_rows(w, p, spec, names, model, usage, log, case_a=case_a)
        except Exception as ex:                          # an extra: never lose the decisions over it
            review_full = {"error": str(ex)}; log(f"full read skipped: {ex}")
    else:
        review_full = None
    if review_unused_lines:
        try:
            spec, review = review_unused(w, p, spec, names, model, usage, log, case_a=case_a)
        except Exception as ex:                          # the review is an extra: never lose the decisions over it
            log(f"review of unused source lines skipped: {ex}")
    chk = check(spec)
    conf = {x["row"]: _conf(x.get("confidence")) for x in r["proposals"]}
    clashes, reasked = [], set()
    for _ in range(5):
        if not chk.get("overlaps"):
            break
        spec2, j2, _left = dec.join_shared(w, spec, chk["overlaps"])
        if j2:
            spec, chk = spec2, check(spec2); r.setdefault("joined", []).extend(j2)
            if not chk.get("overlaps"):
                break
        # a row on footage another row (too far away to group) already shows: read it once more before giving up
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "prep.json").write_text(json.dumps(p)); dec.from_spec(tmp, spec)
            D = json.loads((Path(tmp) / "decisions.json").read_text())
        in_group = {m for g in D["groups"] for m in g["members"]}
        ask, notes = [], {}
        for a, b, *_ in chk["overlaps"]:
            cands = [x for x in (a, b) if x not in in_group and x in props and x not in reasked]
            if cands:
                x = min(cands, key=lambda q: (conf.get(q, 0.95), -q)); other = b if x == a else a
                ask.append(x); notes[x] = (f"Its footage is already shown by D{other} (too far away to be one group): "
                                           f"choose OTHER footage for D{x} -- a group with its own neighbour, or an "
                                           f"unused line -- or answer gap.")
        if ask:
            reasked |= set(ask)
            log(f"rows {ask} share footage with rows further away: second look")
            found = second_look(p, ask, props, case_a, names, model, usage, log, notes=notes)
            if found:
                for x, d in found.items():
                    props[x] = dict(d, second_look=True)
                spec, r["proposals"] = _build(p, flagged, props, model)
                spec = _fill_gaps(w, p, spec); chk = check(spec)
                continue
        spec, dropped = _drop_clashes(w, p, spec, chk["overlaps"], conf)
        if not dropped:
            break
        clashes += dropped; log(f"same footage, too far apart for a group: row(s) {dropped} set to not found")
        chk = check(spec)
    if not chk.get("ok", True) or chk.get("problems"):
        (w / "spec_ai_failed.py").write_text(spec)
        raise ValueError("the AI's decisions do not pass the pipeline's check: " + "; ".join((chk.get("problems") or ["?"])[:3]))
    counts = dec.from_spec(w, spec)
    D = json.loads((w / "decisions.json").read_text())
    by_row = {x["row"]: x for x in r["proposals"]}
    not_found = sorted(int(k) for k in D["gap"])
    low = [dict(row=x["row"], confidence=x.get("confidence"), reason=x.get("reason") or "")
           for x in r["proposals"] if x.get("action") not in ("gap", "none") and not x.get("error")
           and _conf(x.get("confidence")) < LOW_CONFIDENCE and x["row"] not in not_found]
    res = dict(at=time.time(), prep_mtime=prep_mtime, model=model or ai.settings()["AI_MODEL"], rows=len(by_row),
               not_found=not_found, low=low, joined=r.get("joined", []), clashes=clashes, counts=counts,
               second_look=sorted(x["row"] for x in r["proposals"] if x.get("second_look")), names=len(names),
               usage=usage, spec=spec, review=review, review_full=review_full)
    (w / AUTO).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    log(f"decided {len(by_row)} rows: {len(not_found)} not found, {len(low)} unsure")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work"); ap.add_argument("--model"); ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--apply", action="store_true", help="also write decisions.json via from_spec")
    a = ap.parse_args()
    r = propose(a.work, a.model, a.workers)
    print(json.dumps(r["usage"]), f"{len(r['proposals'])} rows -> {Path(a.work) / 'spec_ai.py'}")
    if a.apply:
        print(dec.from_spec(a.work, r["spec"]))


if __name__ == "__main__":
    sys.exit(main())
