"""Step 2 inputs without the editor UI (milestone 1): import a decisions.json or a spec.py (the skill's mkdec
format), or accept the automatic matches (Case A rows confirmed, unmatched rows marked as gaps)."""
import json
from pathlib import Path


def flagged_rows(work):
    """Rows `sheet` refuses without a decision: unmatched + Case A."""
    p = json.loads((Path(work) / "prep.json").read_text())
    auto = {int(k): v for k, v in p["auto"].items()}
    unmatched = [i for i in range(len(p["dub"])) if i not in auto]
    case_a = [i for i, v in auto.items() if v.get("kind") == "Case A reuse"]
    return p, unmatched, sorted(case_a)


def accept_auto(work):
    p, unmatched, case_a = flagged_rows(work)
    d = {"groups": [], "assign": {}, "confirm": {str(i): "accepted automatically in Dub Studio (not read by meaning)"
                                                 for i in case_a},
         "repeat": {}, "gap": {str(i): "no automatic match (accepted automatically in Dub Studio)" for i in unmatched},
         "unused_notes": {}}
    (Path(work) / "decisions.json").write_text(json.dumps(d, indent=1, ensure_ascii=False))
    return {"confirmed": len(case_a), "gaps": len(unmatched)}


def from_spec(work, spec_text):
    """The skill's mkdec.py: spec lines G/A/C/GAP/U/R checked against the row texts."""
    p = json.loads((Path(work) / "prep.json").read_text()); dub, src = p["dub"], p["src"]
    D = {"groups": [], "assign": {}, "confirm": {}, "repeat": {}, "gap": {}, "unused_notes": {}}

    def chk(lst, i, kw, lab):
        t = lst[i]["text"]
        if kw.lower() not in t.lower():
            raise ValueError(f"{lab}{i} '{t}' lacks '{kw}'")
        return t[:30].strip()

    s0 = lambda s: s[0] if isinstance(s, list) else s
    def G(members, s, skw, why):
        D["groups"].append({"members": [m for m, _ in members], "dub_prefixes": [chk(dub, m, k, "DUB#") for m, k in members],
                            "src": s, "src_prefix": chk(src, s0(s), skw, "SRC#"), "reason": why})
    def A(i, kw, s, skw, why):
        D["assign"][str(i)] = {"dub_prefix": chk(dub, i, kw, "DUB#"), "src": s, "src_prefix": chk(src, s0(s), skw, "SRC#"),
                               "reason": why}
    def C(i, why): D["confirm"][str(i)] = why
    def GAP(i, kw, why): chk(dub, i, kw, "DUB#"); D["gap"][str(i)] = why
    def U(s, kw, why): chk(src, s, kw, "SRC#"); D["unused_notes"][str(s)] = why
    def R(later, earlier): D["repeat"][str(later)] = earlier
    exec(compile(spec_text, "spec.py", "exec"), {"G": G, "A": A, "C": C, "GAP": GAP, "U": U, "R": R, "__builtins__": {}})
    (Path(work) / "spec.py").write_text(spec_text)
    (Path(work) / "decisions.json").write_text(json.dumps(D, indent=1, ensure_ascii=False))
    return {k: len(v) for k, v in D.items()}


def missing(work):
    """Flagged rows still without a decision (empty = the gate opens)."""
    w = Path(work)
    if not (w / "decisions.json").exists() or not (w / "prep.json").exists():
        return None
    d = json.loads((w / "decisions.json").read_text())
    done = set(map(int, d.get("assign", {}))) | set(map(int, d.get("confirm", {}))) | set(map(int, d.get("gap", {}))) \
        | set(map(int, d.get("repeat", {}))) | {m for g in d.get("groups", []) for m in g["members"]}
    _, unmatched, case_a = flagged_rows(w)
    return sorted(i for i in unmatched + case_a if i not in done)


def check_spec(work, spec_text, python, scripts, labels, timeout=180):
    """Dry run: the spec's decisions go through the pipeline's own `sheet` integrity check in a scratch copy of the
    work folder (top-level .json/.srt files only; nothing in the real work folder changes).
    -> {"ok": bool, "problems": [str], "overlaps": [[row, row, ms]], "counts": {...}, "log": [str]}"""
    import ast, re, shutil, subprocess, tempfile
    w = Path(work)
    with tempfile.TemporaryDirectory(prefix="dubstudio_check_") as tmp:
        t = Path(tmp)
        for f in w.iterdir():
            if f.is_file() and f.suffix in (".json", ".srt"):
                shutil.copy2(f, t / f.name)
        for f in ("decisions.json", "spec.py"):
            (t / f).unlink(missing_ok=True)
        try:
            counts = from_spec(t, spec_text)
        except Exception as ex:
            return {"ok": False, "problems": [f"the decisions file does not read: {ex}"], "overlaps": [], "counts": {}, "log": []}
        p = subprocess.run([str(python), str(Path(scripts) / "dubstitch.py"), "sheet", "--work", str(t), "--out",
                            str(t / "check.xlsx"), "--labels", labels], capture_output=True, text=True, timeout=timeout,
                           cwd=str(scripts))
        out = (p.stdout + "\n" + p.stderr).splitlines()
    overlaps, problems = [], []
    for ln in out:
        m = re.search(r"row pairs sharing footage.*?:\s*(\[.*\])", ln)
        if m:
            try:
                overlaps = [list(x) for x in ast.literal_eval(m.group(1))]
            except Exception:
                pass
        if re.match(r"\s+DUB#\d+", ln) or "integrity check failed" in ln or "0ms rows: [" in ln and ln.strip() != "0ms rows: []":
            problems.append(ln.strip())
    if p.returncode != 0 and not problems and not overlaps:
        problems.append((out[-1] if out else f"sheet exit code {p.returncode}").strip())
    for a, b, ms in overlaps:
        problems.append(f"rows {a} and {b} show the same source footage ({ms} ms overlap)")
    return {"ok": p.returncode == 0, "problems": problems, "overlaps": overlaps, "counts": counts, "log": out[-30:]}


def _kw(text, n=3):
    w = text.split()
    return " ".join(w[:n]) if w else text


def to_spec(p, D, header="# Step 2 decisions (Dub Studio)"):
    """decisions dict -> spec.py text in the skill's G/A/C/GAP/R/U language (keywords = the first words)."""
    dub, src = p["dub"], p["src"]; q = lambda x: json.dumps(x, ensure_ascii=False)
    s0 = lambda s: s[0] if isinstance(s, list) else s
    items = []
    for g in D.get("groups", []):
        m = ",".join(f"({i},{q(_kw(dub[i]['text']))})" for i in g["members"])
        items.append((g["members"][0], f"G([{m}],{q(g['src'])},{q(_kw(src[s0(g['src'])]['text']))},{q(g.get('reason', ''))})"))
    for i, a in D.get("assign", {}).items():
        i = int(i)
        items.append((i, f"A({i},{q(_kw(dub[i]['text']))},{q(a['src'])},{q(_kw(src[s0(a['src'])]['text']))},{q(a.get('reason', ''))})"))
    for i, why in D.get("confirm", {}).items():
        items.append((int(i), f"C({int(i)},{q(why)})"))
    for i, why in D.get("gap", {}).items():
        items.append((int(i), f"GAP({int(i)},{q(_kw(dub[int(i)]['text']))},{q(why)})"))
    for i, e in D.get("repeat", {}).items():
        items.append((int(i), f"R({int(i)},{int(e)})"))
    for s, why in D.get("unused_notes", {}).items():
        items.append((10 ** 6 + int(s), f"U({int(s)},{q(_kw(src[int(s)]['text']))},{q(why)})"))
    return header + "\n" + "\n".join(t for _, t in sorted(items)) + "\n"


def join_shared(work, spec_text, overlaps, max_span=3):
    """Neighbouring rows that claim the same source footage become one group (the hand-read rule: one source
    sentence told by several dub rows = a group, its window split by dub duration). Pairs further apart than
    max_span rows are left alone. -> (new spec text, [[first, last], ...] joined, [pairs left])"""
    p = json.loads((Path(work) / "prep.json").read_text())
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "prep.json").write_text(json.dumps(p))
        from_spec(tmp, spec_text)
        D = json.loads((Path(tmp) / "decisions.json").read_text())
    auto = p["auto"]
    span = lambda s: (s[0], s[1]) if isinstance(s, list) else (s, s)
    group_of = {m: k for k, g in enumerate(D["groups"]) for m in g["members"]}

    def row_span(i):
        if i in group_of:
            return span(D["groups"][group_of[i]]["src"])
        if str(i) in D["assign"]:
            return span(D["assign"][str(i)]["src"])
        if str(i) in D["gap"] or str(i) in D["repeat"]:
            return None
        return span(auto[str(i)]["src"]) if str(i) in auto else None

    runs, left = [], []
    for a, b, _ in sorted(overlaps):
        a, b = min(a, b), max(a, b)
        if b - a > max_span:
            left.append([a, b]); continue
        runs.append([a, b])
    # merge touching runs, then grow each run to cover whole existing groups, until nothing changes
    while True:
        runs.sort(); out = []
        for r in runs:
            if out and r[0] <= out[-1][1]:
                out[-1][1] = max(out[-1][1], r[1])
            else:
                out.append(list(r))
        grew = False
        for r in out:
            for i in range(r[0], r[1] + 1):
                if i in group_of:
                    mem = D["groups"][group_of[i]]["members"]
                    if mem[0] < r[0] or mem[-1] > r[1]:
                        r[0], r[1] = min(r[0], mem[0]), max(r[1], mem[-1]); grew = True
        runs = out
        if not grew:
            break
    keep_groups = [g for g in D["groups"] if not any(r[0] <= g["members"][0] <= r[1] for r in runs)]
    new_groups = []
    for lo, hi in runs:
        spans = [s for s in (row_span(i) for i in range(lo, hi + 1)) if s]
        if not spans:
            continue
        a0, b0 = min(s[0] for s in spans), max(s[1] for s in spans)
        reasons = []
        for i in range(lo, hi + 1):
            for d in (D["assign"].pop(str(i), None), D["confirm"].pop(str(i), None), D["gap"].pop(str(i), None)):
                if isinstance(d, dict) and d.get("reason"):
                    reasons.append(d["reason"])
                elif isinstance(d, str):
                    reasons.append(d)
        for g in D["groups"]:
            if lo <= g["members"][0] <= hi and g.get("reason"):
                reasons.append(g["reason"])
        new_groups.append({"members": list(range(lo, hi + 1)), "src": a0 if a0 == b0 else [a0, b0],
                           "reason": f"joined in Dub Studio: rows {lo}-{hi} share source footage. " + " | ".join(reasons)[:400]})
    D["groups"] = sorted(keep_groups + new_groups, key=lambda g: g["members"][0])
    return to_spec(p, D), [[g["members"][0], g["members"][-1]] for g in new_groups], left


def alternatives(prep, n=5, rows=None):
    """{row: [[src, score], ...]}: the n source sentences closest in meaning to each flagged dub row (or `rows`),
    from the similarity table the automatic matching already computed (prep["sim"], dub x src)."""
    sim = prep.get("sim") or []
    if rows is None:
        auto = prep.get("auto", {})
        rows = [i for i in range(len(prep.get("dub", []))) if str(i) not in auto or "Case A" in auto[str(i)].get("kind", "")]
    out = {}
    for i in rows:
        if i < len(sim):
            best = sorted(range(len(sim[i])), key=lambda k: -sim[i][k])[:n]
            out[str(i)] = [[k, round(float(sim[i][k]), 3)] for k in best]
    return out
