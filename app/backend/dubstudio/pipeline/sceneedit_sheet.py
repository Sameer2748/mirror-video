"""Scene-edit sheet: every piece Claude cut, with the reason. Usage: sceneedit_sheet.py <edl.json> <library json> <english srt> <out xlsx> <title> [opening s]"""
import json, re, sys
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

EDL, LIBJ, ENSRT, OUT, TITLE = sys.argv[1:6]
OP = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
R = json.load(open(EDL)); SH = {r["n"]: r for r in json.load(open(LIBJ))["shots"]}


def srt(p):
    out = []
    for b in re.split(r"\n\s*\n", open(p, encoding="utf-8-sig").read().strip()):
        L = b.split("\n")
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", L[1] if len(L) > 1 else "")
        if not m: continue
        g = list(map(int, m.groups()))
        out.append(((g[0] * 3600 + g[1] * 60 + g[2]) + g[3] / 1000, (g[4] * 3600 + g[5] * 60 + g[6]) + g[7] / 1000, " ".join(L[2:])))
    return out


EN = srt(ENSRT)
E = [e for w in R["windows"] for e in w["edl"]]
ov = lambda a0, a1, b0, b1: max(0.0, min(a1, b1) - max(a0, b0))
LN = sorted({d["idx"]: d for w in R["windows"] for d in w["lines"]}.values(), key=lambda d: d["start_ms"])


def plines(e):
    L = [d for d in LN if ov(e["t0"], e["t1"], d["start_ms"] / 1000, d["end_ms"] / 1000) > 0.1]
    if L: return L
    b = [d for d in LN if d["end_ms"] / 1000 <= e["t0"] + 0.05]; a = [d for d in LN if d["start_ms"] / 1000 >= e["t1"] - 0.05]
    return ([b[-1]] if b else []) + ([a[0]] if a else [])


def is_broll(e):
    """same test as sceneedit.is_broll: under half of the footage in the own footage (+-0.5 s) of its lines/neighbours"""
    L = plines(e)
    if not L or "_near" not in L[0]: return False
    own = sorted((a - 0.5, b + 0.5) for d in L for a, b in d["_near"]); m = []
    for a, b in own:
        if m and a <= m[-1][1]: m[-1][1] = max(m[-1][1], b)
        else: m.append([a, b])
    return sum(ov(a, b, e["from"], e["to"]) for a, b in m) < 0.5 * (e["to"] - e["from"])
lines = {d["idx"]: d for w in R["windows"] for d in w["lines"]}
sp = [(e["to"] - e["from"]) / (e["t1"] - e["t0"]) for e in E]
fm = lambda s: f"{int(s // 60):02d}:{s % 60:05.2f}"
F = "Arial"; hf = Font(name=F, bold=True, color="FFFFFF"); hfill = PatternFill("solid", fgColor="2F4F6F"); bf = Font(name=F, size=10)
th = Side(style="thin", color="D0D0D0"); bx = Border(left=th, right=th, top=th, bottom=th)
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Summary"
first_probs = sum(len(w["rounds"][0]["problems"]) for w in R["windows"])
left = sum(len(w["rounds"][-1]["problems"]) for w in R["windows"])
txt = [TITLE, "",
       f"Video times include the {OP:.2f} s source opening before the dub starts." if OP else "Video times = dub times.",
       f"{len(E)} pieces in {len(R['windows'])} stretches of about 30 s; every piece between {min(sp):.2f}x and {max(sp):.2f}x "
       f"(time-weighted {sum(e['to'] - e['from'] for e in E) / sum(e['t1'] - e['t0'] for e in E):.2f}x); "
       f"{sum(1 for e in E if e.get('reuse'))} reaction/neutral repeats (zoomed 8%).",
       f"Checks: {first_probs} problems in Claude's first cuts, {left} left after its corrections "
       "(tiling, speed 0.8-1.25x, piece >= 0.5 s, every dialogue line starts on a moving mouth within 0.3 s, action/talking footage once).",
       "", "Stretches:"]
for w in R["windows"]:
    txt.append(f"  {fm(w['window'][0] + OP)}-{fm(w['window'][1] + OP)}: {len(w['edl'])} pieces, check rounds {len(w['rounds'])} "
               f"(problems per round {[len(r['problems']) for r in w['rounds']]}). Claude's notes: {w['rounds'][-1]['notes'][:300]}")
for i, t in enumerate(txt, 1):
    c = ws.cell(i, 1, t); c.font = Font(name=F, size=13 if i == 1 else 10, bold=i == 1); c.alignment = Alignment(wrap_text=True)
ws.column_dimensions["A"].width = 150
ws2 = wb.create_sheet("Scene edit")
hd = ["#", "Video from", "to", "Shot", "Who / shot type", "What happens in the shot", "Source from", "to", "Speed", "Dub line",
      "Line type", "Dub line text", "English subtitle", "Repeat?", "B-roll?", "Why Claude put it here"]
for c, h in enumerate(hd, 1):
    x = ws2.cell(1, c, h); x.font = hf; x.fill = hfill; x.border = bx; x.alignment = Alignment(wrap_text=True)
for i, e in enumerate(E, 2):
    r = SH.get(int(e["shot"]), {}); d = lines.get(e.get("line"))
    en = " ".join(t for a, b, t in EN if min(b, e["t1"]) - max(a, e["t0"]) > 0.15)
    v = [i - 1, fm(e["t0"] + OP), fm(e["t1"] + OP), e["shot"], f"{', '.join(r.get('people', []))} ({r.get('type', '')})" + (f" @ {r['location']}" if r.get('location') else ""), r.get("action", ""),
         fm(e["from"]), fm(e["to"]), round((e["to"] - e["from"]) / (e["t1"] - e["t0"]), 2),
         e.get("line") if e.get("line") is not None else "(between lines)", (("dialogue" if d["dialogue"] else "narration") + (f" @ {d['loc']}" if d.get("loc") else "")) if d else "",
         d["text"] if d else "", en, "yes" if e.get("reuse") else "", "B-ROLL" if is_broll(e) else "", e.get("why", "")]
    for c, x in enumerate(v, 1):
        y = ws2.cell(i, c, x); y.font = bf; y.border = bx; y.alignment = Alignment(wrap_text=True, vertical="top")
        if d and d["dialogue"]: y.fill = PatternFill("solid", fgColor="E2EFDA")
        if e.get("reuse"): y.fill = PatternFill("solid", fgColor="FFF2CC")
        if is_broll(e): y.fill = PatternFill("solid", fgColor="DDEBF7")
for c, w in enumerate([5, 9, 9, 6, 18, 32, 9, 9, 7, 8, 10, 40, 40, 8, 8, 70], 1):
    ws2.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
ws2.freeze_panes = "A2"
if LN and "own_speed" in LN[0]:                    # v21: every line, its own footage speed and the B-roll it got
    ws3 = wb.create_sheet("Lines (B-roll rule)")
    hd3 = ["Line", "Video from", "to", "Dub line", "English subtitle", "Own footage (source s)", "Own speed", "Class",
           "B-roll allowed s", "B-roll used s", "Pieces (shot: source)"]
    for c, h in enumerate(hd3, 1):
        x = ws3.cell(1, c, h); x.font = hf; x.fill = hfill; x.border = bx; x.alignment = Alignment(wrap_text=True)
    for i, d in enumerate(LN, 2):
        a0, a1 = d["start_ms"] / 1000, d["end_ms"] / 1000
        P = [e for e in E if ov(e["t0"], e["t1"], a0, a1) > 0.05]
        used = sum(ov(e["t0"], e["t1"], a0, a1) for e in P if is_broll(e))
        cls = "SLOW" if d.get("slow") else ("fast (trim)" if d["own_speed"] > 1.25 else "normal")
        en = " ".join(t for a, b, t in EN if min(b, a1) - max(a, a0) > 0.15)
        v = [d["idx"], fm(a0 + OP), fm(a1 + OP), d["text"], en, ", ".join(f"{a:.2f}-{b:.2f}" for a, b in d.get("_own", [])) or "none",
             d["own_speed"], cls, d.get("broll_budget", 0), round(used, 2),
             "; ".join(f"{e['shot']}: {e['from']:.2f}-{e['to']:.2f}" + (" B-ROLL" if is_broll(e) else "") for e in P)]
        bad = (used > d.get("broll_budget", 0) + 0.75) or (used > 0.05 and not d.get("slow"))
        for c, x in enumerate(v, 1):
            y = ws3.cell(i, c, x); y.font = bf; y.border = bx; y.alignment = Alignment(wrap_text=True, vertical="top")
            if d.get("slow"): y.fill = PatternFill("solid", fgColor="FCE4D6")
            if bad: y.fill = PatternFill("solid", fgColor="F8CBAD")
    for c, w in enumerate([6, 9, 9, 40, 40, 22, 8, 10, 9, 9, 50], 1):
        ws3.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
    ws3.freeze_panes = "A2"
wb.save(OUT); print("saved", OUT, len(E), "pieces")
