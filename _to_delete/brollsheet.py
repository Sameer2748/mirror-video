"""B-roll test sheet: where the slow-clip rule added footage (HIH ep55). Usage: brollsheet.py <B work> <out.xlsx>"""
import json, os, re, sys
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

W, OUT = sys.argv[1], sys.argv[2]
cfg = json.load(open(os.path.join(W, "config.json")))
OP = cfg.get("opening_s", 0.0)
M = json.load(open(os.path.join(W, "manifest_groups.json")))
R = json.load(open(os.path.join(W, "slowfix.json")))


def parse_srt(p):
    out = []
    for b in re.split(r"\n\s*\n", open(p, encoding="utf-8-sig").read().strip()):
        L = b.strip().split("\n")
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", L[1] if len(L) > 1 else "")
        if not m: continue
        g = list(map(int, m.groups()))
        out.append(((g[0] * 3600 + g[1] * 60 + g[2]) * 1000 + g[3], (g[4] * 3600 + g[5] * 60 + g[6]) * 1000 + g[7], " ".join(L[2:])))
    return out


EN = parse_srt(os.path.join(W, "HIH_EP55_EN.srt"))
def english(t0, t1):
    return " ".join(t for a, b, t in EN if min(b, t1) - max(a, t0) > 150)


def vt(ms):                                   # time in the delivered video (opening first)
    s = ms / 1000 + OP
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


def st(s):
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


ARIAL = "Arial"
hfont = Font(name=ARIAL, bold=True, color="FFFFFF"); hfill = PatternFill("solid", fgColor="2F4F6F")
body = Font(name=ARIAL, size=10); bold = Font(name=ARIAL, size=10, bold=True)
thin = Side(style="thin", color="D0D0D0"); box = Border(left=thin, right=thin, top=thin, bottom=thin)
fills = {"reuse": PatternFill("solid", fgColor="FFF2CC"), "new": PatternFill("solid", fgColor="E2EFDA"),
         "run": PatternFill("solid", fgColor="DDEBF7"), "slow": PatternFill("solid", fgColor="F8CBAD")}


def table(ws, r0, headers, rows, widths, fill_of=None):
    for c, h in enumerate(headers, 1):
        x = ws.cell(r0, c, h); x.font = hfont; x.fill = hfill; x.alignment = Alignment(wrap_text=True, vertical="center"); x.border = box
    for i, row in enumerate(rows, r0 + 1):
        f = fill_of(row) if fill_of else None
        for c, v in enumerate(row, 1):
            x = ws.cell(i, c, v); x.font = body; x.border = box
            x.alignment = Alignment(wrap_text=True, vertical="top")
            if f: x.fill = f
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
    ws.freeze_panes = ws.cell(r0 + 1, 1)


# ---- one row per piece of added footage, in video order ----
clips = {(c["row"], round(c["it0"])): c for c in R["clips"]}
def clip_of(seg):
    best = None
    for (row, it0), c in clips.items():
        if row == seg["it_idx"] and c["it0"] - 1 <= seg["it_start_ms"] < c["it1"] + 1:
            best = c
    return best

added = []
for s in sorted(M["segments"], key=lambda x: x["it_start_ms"]):
    c = clip_of(s)
    if not c: continue
    if s.get("broll"):
        kind = "reuse" if "reuse" in s["broll"] else "new"
        b = next((b for b in c["broll"] if abs(b["us0"] - s["us_start_ms"]) < 5), None)
        added.append(dict(fill=kind, row=[
            None, vt(s["it_start_ms"]), vt(s["it_end_ms"]), round((s["it_end_ms"] - s["it_start_ms"]) / 1000, 2), f"IT#{c['row']}",
            c["kind"], s.get("it_text", ""), english(c["it0"], c["it1"]), "B-roll",
            {"listener / reaction": "Listener / reaction shot", "same setup": "Same camera setup (same person, same framing)",
             "same scene": "Other shot of the same scene"}.get(s["broll"], "Reused footage of the same scene, zoomed in 8%"),
            f"{st(s['us_start_ms'] / 1000)}-{st(s['us_end_ms'] / 1000)}", "yes (shown elsewhere in the episode)" if kind == "reuse" else "no (unused footage)",
            c["speed_before"], c["speed_after"], (b or {}).get("by") or c["by"], c["reason"]]))
    elif "slow-clip run-on" in (s.get("note") or ""):
        added.append(dict(fill="run", row=[
            None, vt(s["it_start_ms"]), vt(s["it_end_ms"]), round((s["it_end_ms"] - s["it_start_ms"]) / 1000, 2), f"IT#{c['row']}",
            c["kind"], s.get("it_text", ""), english(c["it0"], c["it1"]), "More of the same shot",
            f"the clip runs {c['runon_s']:.1f} s longer into its own shot (no new cut)",
            f"{st(s['us_start_ms'] / 1000)}-{st(s['us_end_ms'] / 1000)}", "no (unused footage)",
            c["speed_before"], c["speed_after"], "rule", "the free seconds right next to the clip in the same shot"]))
for n, a in enumerate(added, 1):
    a["row"][0] = n

wb = openpyxl.Workbook()
ws = wb.active; ws.title = "Summary"
C = R["clips"]
nb = sum(1 for a in added if a["row"][8] == "B-roll"); nr = sum(1 for a in added if a["fill"] == "reuse")
lines = [
    ("HIH ep55 — B-roll test (slow-clip rule)", True),
    ("", False),
    ("What changed: every clip that played slower than 0.8x (slow motion) now keeps its own picture at about normal "
     "speed, and the rest of the Italian line is covered with other footage of the same scene (a B-roll).", False),
    ("Video A = before (HIH_EP55_A_before_broll_en_subs.mp4). Video B = after (HIH_EP55_B_with_broll_en_subs.mp4). "
     "Both have the English subtitles and the same timing: the times in this sheet are times in the videos.", False),
    ("", False),
    (f"Slow clips (under 0.8x): {len(C)}, {R['slow_share_before']:.1%} of the episode before -> {R['slow_share_after']:.1%} after", False),
    (f"B-roll pieces added: {nb} (unused footage: {nb - nr}; reused footage, zoomed in 8%: {nr})", False),
    (f"Clips only made longer into their own shot (no new cut): {sum(1 for a in added if a['fill'] == 'run')}", False),
    (f"Clips still a little under 0.8x (no free footage of the same scene): {sum(1 for c in C if c['status'] != 'ok')}", False),
    (f"New cuts in the episode: {R.get('new_cuts', '')}", False),
    ("", False),
    ("Colours on the 'B-roll added' tab: green = unused footage, yellow = reused footage (zoomed in 8%), "
     "blue = the clip simply runs longer into its own shot.", False),
    ("Who chose: Claude = Claude looked at the frames and picked the shot; rule = picked automatically (best match of "
     "look and time, or Claude's choice had no footage left).", False),
]
for i, (t, b) in enumerate(lines, 1):
    x = ws.cell(i, 1, t); x.font = Font(name=ARIAL, size=14 if i == 1 else 10, bold=b or i == 1)
    x.alignment = Alignment(wrap_text=True, vertical="top")
ws.column_dimensions["A"].width = 120

ws2 = wb.create_sheet("B-roll added")
table(ws2, 1, ["#", "Video time from", "to", "Length s", "Italian row", "Clip type", "Italian line", "English (subtitle)",
               "What was added", "Shot used", "Source time (US video)", "Reused?", "Speed before", "Speed after (whole line)",
               "Chosen by", "Why"],
      [a["row"] for a in added], [5, 10, 10, 8, 9, 11, 40, 40, 14, 30, 14, 18, 9, 10, 10, 60],
      fill_of=lambda r: fills["reuse"] if "zoomed" in str(r[9]) else fills["run"] if r[8] != "B-roll" else fills["new"])

ws3 = wb.create_sheet("Every slow clip")
rows = []
for c in sorted(C, key=lambda c: c["it0"]):
    rows.append([f"IT#{c['row']}", vt(c["it0"]), vt(c["it1"]), c["kind"], c.get("it_text", ""), english(c["it0"], c["it1"]),
                 c["speed_before"], c["speed_after"], c["runon_s"], len(c["broll"]),
                 "ok" if c["status"] == "ok" else "still a little slow", c["by"], c["reason"]])
table(ws3, 1, ["Italian row", "Video time from", "to", "Clip type", "Italian line", "English", "Speed before", "Speed after",
               "Run-on s", "B-roll pieces", "Result", "Chosen by", "Why"], rows,
      [9, 10, 10, 11, 40, 40, 9, 9, 8, 8, 14, 10, 60],
      fill_of=lambda r: fills["slow"] if r[10] != "ok" else None)
wb.save(OUT)
print("saved", OUT, "pieces", len(added), "B-roll", nb, "reuse", nr)
