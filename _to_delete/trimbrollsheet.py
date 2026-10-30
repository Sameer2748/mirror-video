"""Fast-trim + B-roll test sheet. Usage: trimbrollsheet.py <B work> <subtitle srt> <title> <video A name> <video B name> <out.xlsx>"""
import json, os, re, sys
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

W, SRT, TITLE, VA, VB, OUT = sys.argv[1:7]
cfg = json.load(open(os.path.join(W, "config.json")))
OP = cfg.get("opening_s", 0.0)
M = json.load(open(os.path.join(W, "manifest_groups.json")))
R = json.load(open(os.path.join(W, "slowfix.json")))
F = json.load(open(os.path.join(W, "fastfix.json")))


def parse_srt(p):
    out = []
    for b in re.split(r"\n\s*\n", open(p, encoding="utf-8-sig").read().strip()):
        L = b.strip().split("\n")
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", L[1] if len(L) > 1 else "")
        if not m: continue
        g = list(map(int, m.groups()))
        out.append(((g[0] * 3600 + g[1] * 60 + g[2]) * 1000 + g[3], (g[4] * 3600 + g[5] * 60 + g[6]) * 1000 + g[7], " ".join(L[2:])))
    return out


SUB = parse_srt(SRT)
def subtitle(t0, t1):
    return " ".join(t for a, b, t in SUB if min(b, t1) - max(a, t0) > 150)
def vt(ms):
    s = ms / 1000 + OP; return f"{int(s // 60):02d}:{s % 60:05.2f}"
def st(s):
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


ARIAL = "Arial"
hfont = Font(name=ARIAL, bold=True, color="FFFFFF"); hfill = PatternFill("solid", fgColor="2F4F6F")
body = Font(name=ARIAL, size=10)
thin = Side(style="thin", color="D0D0D0"); box = Border(left=thin, right=thin, top=thin, bottom=thin)
fills = {"reuse": PatternFill("solid", fgColor="FFF2CC"), "new": PatternFill("solid", fgColor="E2EFDA"),
         "run": PatternFill("solid", fgColor="DDEBF7"), "slow": PatternFill("solid", fgColor="F8CBAD"),
         "trim": PatternFill("solid", fgColor="EADCF8")}


def table(ws, headers, rows, widths, fill_of=None):
    for c, h in enumerate(headers, 1):
        x = ws.cell(1, c, h); x.font = hfont; x.fill = hfill; x.alignment = Alignment(wrap_text=True, vertical="center"); x.border = box
    for i, row in enumerate(rows, 2):
        f = fill_of(row) if fill_of else None
        for c, v in enumerate(row, 1):
            x = ws.cell(i, c, v); x.font = body; x.border = box; x.alignment = Alignment(wrap_text=True, vertical="top")
            if f: x.fill = f
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
    ws.freeze_panes = "A2"


# ---- fast trims ----
trims = []
for g in F.get("group_trims") or []:
    for t in g.get("trims") or []:
        trims.append([vt(g["it0"]), vt(g["it1"]), f"rows {g['rows'][0]}-{g['rows'][-1]}", "group (narration block)",
                      subtitle(g["it0"], g["it1"])[:300], g["speed_before"], g["speed_after"], round(t["ms"] / 1000, 2),
                      f"cut from row {t['row']} ({t.get('cls', '')})", "rule (group trim)", ""])
for c in F.get("clips") or []:
    trims.append([vt(c["it0"]), vt(c["it1"]), f"row {c['row']}", c["kind"], subtitle(c["it0"], c["it1"]),
                  c["speed_before"], c["speed_after"], c["cut_s"],
                  "; ".join(f"{st(a)}-{st(b)}" for a, b in c.get("cuts") or []), c["by"], c.get("reason", "")])
trims.sort(key=lambda r: r[0])

# ---- B-roll pieces (in video order) ----
clips = R["clips"]
def clip_of(seg):
    for c in clips:
        if c["row"] == seg["it_idx"] and c["it0"] - 1 <= seg["it_start_ms"] < c["it1"] + 1:
            return c
added = []
for s in sorted(M["segments"], key=lambda x: x["it_start_ms"]):
    c = clip_of(s)
    if not c: continue
    if s.get("broll"):
        kind = "reuse" if "reuse" in s["broll"] else "new"
        b = next((b for b in c["broll"] if abs(b["us0"] - s["us_start_ms"]) < 5), None)
        added.append((kind, [vt(s["it_start_ms"]), vt(s["it_end_ms"]), round((s["it_end_ms"] - s["it_start_ms"]) / 1000, 2),
            f"row {c['row']}", c["kind"], subtitle(c["it0"], c["it1"]), "B-roll",
            {"listener / reaction": "Listener / reaction shot", "same setup": "Same camera setup (same person, same framing)",
             "same scene": "Other shot of the same scene"}.get(s["broll"], "Reused footage of the same scene, zoomed in 8%"),
            f"{st(s['us_start_ms'] / 1000)}-{st(s['us_end_ms'] / 1000)}",
            "yes (also shown elsewhere in the episode)" if kind == "reuse" else "no (unused footage)",
            c["speed_before"], c["speed_after"], (b or {}).get("by") or c["by"], c["reason"]]))
    elif "slow-clip run-on" in (s.get("note") or ""):
        added.append(("run", [vt(s["it_start_ms"]), vt(s["it_end_ms"]), round((s["it_end_ms"] - s["it_start_ms"]) / 1000, 2),
            f"row {c['row']}", c["kind"], subtitle(c["it0"], c["it1"]), "More of the same shot",
            f"the clip runs {c['runon_s']:.1f} s longer into its own shot (no new cut)",
            f"{st(s['us_start_ms'] / 1000)}-{st(s['us_end_ms'] / 1000)}", "no (unused footage)",
            c["speed_before"], c["speed_after"], "rule", "the free seconds right next to the clip in the same shot"]))

wb = openpyxl.Workbook()
ws = wb.active; ws.title = "Summary"
nb = sum(1 for k, _ in added if k in ("new", "reuse")); nr = sum(1 for k, _ in added if k == "reuse")
lines = [
    (TITLE, True), ("", False),
    (f"Video A = before: {VA}. The pipeline up to groups, NO fast-clip trimming and NO B-roll (slow clips play in slow motion, "
     f"fast clips play fast).", False),
    (f"Video B = after: {VB}. The same edit + the fast-clip rule (nothing faster than 1.25x: trimmed to about 1.2x) + the "
     f"slow-clip rule (under 0.8x: the clip plays at about normal speed and the rest of the line is covered with B-roll of the "
     f"same scene).", False),
    ("Both videos have the English subtitles and the same timing: every time in this sheet is a time in the videos.", False),
    ("", False),
    (f"Fast clips: {len(F.get('clips') or [])} clips trimmed + {len(F.get('group_trims') or [])} narration blocks trimmed, "
     f"{sum(c['cut_s'] for c in F.get('clips') or []) + sum(g['cut_s'] for g in F.get('group_trims') or []):.1f} s of footage cut; "
     f"after it no clip plays faster than 1.25x.", False),
    (f"Slow clips (under 0.8x): {len(clips)}, {R['slow_share_before']:.1%} of the episode before -> {R['slow_share_after']:.1%} after.", False),
    (f"B-roll pieces added: {nb} (unused footage: {nb - nr}; reused footage of the same scene, zoomed in 8%: {nr}); clips that "
     f"just run longer into their own shot: {sum(1 for k, _ in added if k == 'run')}.", False),
    (f"Slow clips still under 0.8x (no more footage of that scene): {sum(1 for c in clips if c['status'] != 'ok')}.", False),
    (f"New cuts from B-roll: {R.get('new_cuts', '')} (they get no transition; the other clip switches keep the TOLR transitions).", False),
    ("", False),
    ("Tabs: 'Fast trims' = every trim (purple); 'B-roll added' = every added piece (green = unused footage, yellow = reused "
     "and zoomed in, blue = the clip runs longer into its own shot); 'Every slow clip' = all slow clips, red = still slow.", False),
    ("Chosen by: Claude = Claude looked at the frames and chose; rule = chosen automatically.", False),
]
for i, (t, b) in enumerate(lines, 1):
    x = ws.cell(i, 1, t); x.font = Font(name=ARIAL, size=14 if i == 1 else 10, bold=b)
    x.alignment = Alignment(wrap_text=True, vertical="top")
ws.column_dimensions["A"].width = 125

table(wb.create_sheet("Fast trims"), ["Video time from", "to", "Row(s)", "Clip type", "Subtitle (English)", "Speed before",
      "Speed after", "Seconds cut", "Cut (source time)", "Chosen by", "Why"], trims,
      [10, 10, 10, 16, 45, 9, 9, 9, 22, 12, 60], fill_of=lambda r: fills["trim"])
table(wb.create_sheet("B-roll added"), ["Video time from", "to", "Length s", "Row", "Clip type", "Subtitle (English)",
      "What was added", "Shot used", "Source time", "Reused?", "Speed before", "Speed after (whole line)", "Chosen by", "Why"],
      [r for _, r in added], [10, 10, 8, 8, 11, 45, 14, 30, 14, 18, 9, 10, 10, 60],
      fill_of=lambda r: fills["reuse"] if "zoomed" in str(r[7]) else fills["run"] if r[6] != "B-roll" else fills["new"])
rows = [[vt(c["it0"]), vt(c["it1"]), f"row {c['row']}", c["kind"], subtitle(c["it0"], c["it1"]), c["speed_before"],
         c["speed_after"], c["runon_s"], len(c["broll"]), "ok" if c["status"] == "ok" else "still slow", c["by"], c["reason"]]
        for c in sorted(clips, key=lambda c: c["it0"])]
table(wb.create_sheet("Every slow clip"), ["Video time from", "to", "Row", "Clip type", "Subtitle (English)", "Speed before",
      "Speed after", "Run-on s", "B-roll pieces", "Result", "Chosen by", "Why"], rows,
      [10, 10, 8, 11, 45, 9, 9, 8, 8, 10, 10, 60], fill_of=lambda r: fills["slow"] if r[9] != "ok" else None)
wb.save(OUT)
print("saved", OUT, "trims", len(trims), "added", len(added), "broll", nb, "reuse", nr)
