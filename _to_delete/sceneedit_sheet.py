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
      "Line type", "Dub line text", "English subtitle", "Repeat?", "Why Claude put it here"]
for c, h in enumerate(hd, 1):
    x = ws2.cell(1, c, h); x.font = hf; x.fill = hfill; x.border = bx; x.alignment = Alignment(wrap_text=True)
for i, e in enumerate(E, 2):
    r = SH.get(int(e["shot"]), {}); d = lines.get(e.get("line"))
    en = " ".join(t for a, b, t in EN if min(b, e["t1"]) - max(a, e["t0"]) > 0.15)
    v = [i - 1, fm(e["t0"] + OP), fm(e["t1"] + OP), e["shot"], f"{', '.join(r.get('people', []))} ({r.get('type', '')})", r.get("action", ""),
         fm(e["from"]), fm(e["to"]), round((e["to"] - e["from"]) / (e["t1"] - e["t0"]), 2),
         e.get("line") if e.get("line") is not None else "(between lines)", ("dialogue" if d and d["dialogue"] else "narration") if d else "",
         d["text"] if d else "", en, "yes" if e.get("reuse") else "", e.get("why", "")]
    for c, x in enumerate(v, 1):
        y = ws2.cell(i, c, x); y.font = bf; y.border = bx; y.alignment = Alignment(wrap_text=True, vertical="top")
        if d and d["dialogue"]: y.fill = PatternFill("solid", fgColor="E2EFDA")
        if e.get("reuse"): y.fill = PatternFill("solid", fgColor="FFF2CC")
for c, w in enumerate([5, 9, 9, 6, 18, 32, 9, 9, 7, 8, 10, 40, 40, 8, 70], 1):
    ws2.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
ws2.freeze_panes = "A2"
wb.save(OUT); print("saved", OUT, len(E), "pieces")
