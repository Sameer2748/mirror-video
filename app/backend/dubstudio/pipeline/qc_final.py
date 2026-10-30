"""Final QC of a finished episode (owner, 2026-10-30). Checks the final edit list and the finished videos and writes a
QC workbook with one PASS / WARN / FAIL line per case:

  1. B-roll repeats      -- one B-roll shot used at most 3 times (MAX_BROLL_USES)
  2. Short shots         -- every shot on screen (consecutive pieces of the same continuous footage = one shot) at least
                            2.0 s; a shorter B-roll = FAIL (rule), a shorter clip = WARN (may look jumpy)
  3. Story sync          -- Claude (text only) reads, shot by shot, what is heard and what is shown (who, where, what
                            happens, who talks) and lists every place where picture and story do not fit
  4. Black / blank       -- no black frames in the finished videos (ffmpeg blackdetect, 0.1 s or longer)
  5. Frame rate          -- every finished video at 25 fps
  6. Speaker             -- during a dialogue line nobody but its speaker talks on screen (face map)

Usage: qc_final.py --work W --library <dir with broll_library.json + face_map.json> --videos V1 [V2 ...] --out QC.xlsx
       [--en-srt EN.srt] [--range FROM TO] [--app <folder with dubstudio/>] [--no-ai]
"""
import argparse, json, os, re, subprocess, sys
p = argparse.ArgumentParser()
p.add_argument("--work", required=True); p.add_argument("--library", required=True); p.add_argument("--videos", nargs="+", default=[])
p.add_argument("--out", required=True); p.add_argument("--en-srt"); p.add_argument("--range", nargs=2, type=float)
p.add_argument("--app", default=""); p.add_argument("--no-ai", action="store_true"); p.add_argument("--fps", type=int, default=25)
p.add_argument("--min-shot", type=float, default=2.0); p.add_argument("--max-uses", type=int, default=3)
a = p.parse_args()
W, LD = a.work, a.library
ov = lambda a0, a1, b0, b1: max(0.0, min(a1, b1) - max(a0, b0))
fmt = lambda s: f"{int(s // 60)}:{s % 60:04.1f}"
LIB = json.load(open(os.path.join(LD, "broll_library.json"))); SH = {r["n"]: r for r in LIB["shots"]}
FM = json.load(open(os.path.join(LD, "face_map.json")))["shots"] if os.path.exists(os.path.join(LD, "face_map.json")) else {}
segs = [s for s in json.load(open(os.path.join(W, "manifest_groups.json")))["segments"] if not s.get("video")]
R0, R1 = (a.range or (0, 1e9))
segs = [s for s in segs if ov(s["it_start_ms"] / 1000, s["it_end_ms"] / 1000, R0, R1) > 0.04]
dub = [d for d in json.load(open(os.path.join(W, "sentences_dub.json")))["sentences"] if d.get("start_ms") is not None]
spk = {}
for c in (os.path.join(W, "scene", "speakers.json"), os.path.join(W, "speakers.json")):
    if os.path.exists(c): spk = json.load(open(c)).get("lines", {}); break
en = []
if a.en_srt and os.path.exists(a.en_srt):
    for b in re.split(r"\n\s*\n", open(a.en_srt, encoding="utf-8-sig").read().strip()):
        L = b.splitlines(); m = next((re.match(r"(\d+):(\d+):(\d+)[,.](\d+) --> (\d+):(\d+):(\d+)[,.](\d+)", x) for x in L if "-->" in x), None)
        if m:
            g = [int(x) for x in m.groups()]; t0 = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000; t1 = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
            en.append((t0, t1, " ".join(x for x in L if "-->" not in x and not x.strip().isdigit())))
shot_of = lambda s: next((r["n"] for r in LIB["shots"] if r["start"] <= (s["us_start_ms"] + s["us_end_ms"]) / 2000 < r["end"]), None)
for s in segs: s["_shot"] = shot_of(s)
# visible shots: consecutive pieces of the same shot whose footage continues = one shot on screen
vis = []
for s in segs:
    t0, t1, u0, u1 = s["it_start_ms"] / 1000, s["it_end_ms"] / 1000, s["us_start_ms"] / 1000, s["us_end_ms"] / 1000
    if vis and vis[-1]["shot"] == s["_shot"] and abs(u0 - vis[-1]["u1"]) < 0.15 and abs(t0 - vis[-1]["t1"]) < 0.05:
        vis[-1].update(t1=t1, u1=u1); vis[-1]["broll"] = vis[-1]["broll"] and bool(s.get("broll")); continue
    vis.append(dict(t0=t0, t1=t1, u0=u0, u1=u1, shot=s["_shot"], broll=bool(s.get("broll")), repeat=bool(s.get("zoom"))))
lines_at = lambda t0, t1: [d for d in dub if ov(d["start_ms"] / 1000, d["end_ms"] / 1000, t0, t1) > 0.1]
res = {}
# 1. B-roll repeats
uses = {}
for v in vis:
    if v["broll"] and v["shot"] is not None: uses.setdefault(v["shot"], []).append(v)
c1 = [dict(shot=n, uses=len(v), at=", ".join(fmt(x["t0"]) for x in v), what=SH[n]["action"]) for n, v in sorted(uses.items(), key=lambda x: -len(x[1]))]
res[1] = ("B-roll repeats", f"one B-roll shot at most {a.max_uses} times", "FAIL" if any(x["uses"] > a.max_uses for x in c1) else "PASS",
          sum(1 for x in c1 if x["uses"] > a.max_uses), c1)
# 2. short shots
c2 = []
for v in vis:
    d = v["t1"] - v["t0"]
    if d < a.min_shot - 0.01 and v["t1"] < R1 - 0.05:
        c2.append(dict(at=fmt(v["t0"]), seconds=round(d, 2), kind="B-roll" if v["broll"] else "clip", shot=v["shot"],
                       what=SH.get(v["shot"], {}).get("action", ""), level="FAIL" if v["broll"] else "WARN",
                       lines=" | ".join(x["text"][:50] for x in lines_at(v["t0"], v["t1"]))))
nb = sum(1 for x in c2 if x["level"] == "FAIL")
res[2] = ("Short shots", f"every shot on screen at least {a.min_shot} s", "FAIL" if nb else ("WARN" if c2 else "PASS"), len(c2), c2)
# 6. speaker (face map)
c6 = []
for v in vis:
    fm = FM.get(str(v["shot"])) if v["shot"] is not None else None
    if not fm: continue
    sp = (v["u1"] - v["u0"]) / max(v["t1"] - v["t0"], 1e-6)
    for d in lines_at(v["t0"], v["t1"]):
        who = spk.get(str(d["idx"]))
        if not who or who in ("narration", "other", "unclear"): continue
        a0, a1 = max(v["t0"], d["start_ms"] / 1000), min(v["t1"], d["end_ms"] / 1000)
        s0, s1 = v["u0"] + (a0 - v["t0"]) * sp, v["u0"] + (a1 - v["t0"]) * sp
        talk = sorted({x["name"] for x in fm["talking"] if x["name"] not in ("unknown face",) and ov(x["from_s"], x["to_s"], s0, s1) > 0.2})
        if talk and who not in talk:
            c6.append(dict(at=fmt(a0), line=d["text"][:70], speaker=who, on_screen_talking=", ".join(talk), shot=v["shot"]))
res[6] = ("Speaker", "during a dialogue line nobody but its speaker talks on screen", "FAIL" if c6 else "PASS", len(c6), c6)
# 4. black frames, 5. frame rate
c4, c5 = [], []
for vp in a.videos:
    if not os.path.exists(vp): c4.append(dict(video=os.path.basename(vp), at="-", seconds="missing")); continue
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate,avg_frame_rate", "-of", "csv=p=0", vp],
                       capture_output=True, text=True).stdout.strip().split(",")
    from fractions import Fraction
    fr = float(Fraction(r[0])) if r and r[0] and "/" in r[0] else 0.0
    c5.append(dict(video=os.path.basename(vp), fps=round(fr, 3), ok="yes" if abs(fr - a.fps) < 0.01 else "NO"))
    out = subprocess.run(["ffmpeg", "-v", "info", "-i", vp, "-vf", "blackdetect=d=0.1:pic_th=0.95:pix_th=0.10", "-an", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    for m in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", out):
        c4.append(dict(video=os.path.basename(vp), at=fmt(float(m.group(1))), seconds=round(float(m.group(3)), 2)))
res[4] = ("Black / blank screens", "no black frames (0.1 s or longer) in the finished videos", "FAIL" if c4 else "PASS", len(c4), c4)
res[5] = ("Frame rate", f"every finished video at {a.fps} fps", "FAIL" if any(x["ok"] == "NO" for x in c5) or not c5 else "PASS",
          sum(1 for x in c5 if x["ok"] == "NO"), c5)
# 3. story sync (Claude, text only)
c3, usage = [], None
if not a.no_ai and a.app:
    sys.path.insert(0, a.app)
    from dubstudio import ai
    def en_at(t0, t1): return " ".join(x[2] for x in en if ov(x[0], x[1], t0, t1) > 0.1)
    rows = []
    for i, v in enumerate(vis):
        r = SH.get(v["shot"], {}); fm = FM.get(str(v["shot"]), {})
        heard = " | ".join(f"{'DIALOGUE said by ' + spk.get(str(d['idx']), '?') if spk.get(str(d['idx'])) not in (None, 'narration') else 'narration'}: {d['text']}" for d in lines_at(v["t0"], v["t1"]))
        on = "; ".join(f"{x['name']} ({x['pos']}, {x['view']})" for x in fm.get("persons", [])) or ", ".join(r.get("people") or []) or "nobody"
        tk = ", ".join(sorted({x["name"] for x in fm.get("talking", []) if ov(x["from_s"], x["to_s"], v["u0"], v["u1"]) > 0.2})) or "nobody"
        rows.append(f"{i}: {fmt(v['t0'])}-{fmt(v['t1'])} {'B-ROLL' if v['broll'] else 'clip'} | HEARD: {heard or '(pause)'} | ENGLISH: {en_at(v['t0'], v['t1'])[:160]}"
                    f" | SHOWN: shot {v['shot']}: {r.get('action', '')}; place {r.get('location', '')}; on screen {on}; talking {tk}")
    q0 = ("You are checking a finished dubbed video, shot by shot. For every shot you get what is HEARD (the dub lines, "
          "dialogue with its speaker or narration, plus the English subtitle) and what is SHOWN (the source shot: what "
          "happens, the place, who is on screen, who talks). List ONLY real story-sync errors a viewer would notice: a person "
          "talking on screen while someone else's line or narration is heard; the wrong person shown for what the line is "
          "about; an event or object shown before the voice tells it or that the story does not tell; a different place than "
          "the scene. A listener's reaction during dialogue, or a silent face during narration about that person, is fine. "
          "Answer only JSON: {\"issues\": [{\"shot\": n, \"severity\": \"high|medium\", \"problem\": \"at most 20 words\"}]}\n\nSHOTS:\n")
    for k in range(0, len(rows), 45):
        try:
            text, usage = ai.chat(q0 + "\n".join(rows[k:k + 45]), max_tokens=4000)
            for x in (ai.extract_json(text, "issues") or {}).get("issues") or []:
                i = int(x.get("shot", -1))
                if 0 <= i < len(vis):
                    v = vis[i]
                    c3.append(dict(at=fmt(v["t0"]), shot=v["shot"], kind="B-roll" if v["broll"] else "clip", severity=x.get("severity", ""),
                                   problem=x.get("problem", ""), heard=" | ".join(d["text"][:60] for d in lines_at(v["t0"], v["t1"]))))
        except Exception as ex:
            c3.append(dict(at="-", shot="-", kind="-", severity="error", problem=f"story check failed: {str(ex)[:120]}", heard=""))
    res[3] = ("Story sync", "what is shown fits what is heard (Claude reads every shot)",
              "FAIL" if any(x["severity"] == "high" for x in c3) else ("WARN" if c3 else "PASS"), len(c3), c3)
else:
    res[3] = ("Story sync", "what is shown fits what is heard (Claude reads every shot)", "NOT RUN", 0, [])
# workbook
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
wb = Workbook(); ws = wb.active; ws.title = "Summary"
F = Font(name="Arial", size=10); HB = Font(name="Arial", size=10, bold=True, color="FFFFFF"); HF = PatternFill("solid", fgColor="1F4E78")
col = {"PASS": "C6EFCE", "WARN": "FFEB9C", "FAIL": "FFC7CE", "NOT RUN": "D9D9D9"}
for i, h in enumerate(["Case", "Check", "Rule", "Result", "Findings"], 1):
    c = ws.cell(1, i, h); c.font = HB; c.fill = HF
for row, k in enumerate(sorted(res), 2):
    name, rule, st, n, _ = res[k]
    for i, v in enumerate([k, name, rule, st, n], 1):
        c = ws.cell(row, i, v); c.font = F
    ws.cell(row, 4).fill = PatternFill("solid", fgColor=col.get(st, "FFFFFF"))
ws.cell(len(res) + 3, 1, f"{len(vis)} shots on screen, {sum(1 for v in vis if v['broll'])} of them B-roll; range {fmt(R0)}-{fmt(min(R1, vis[-1]['t1'] if vis else 0))}").font = F
for w_, c in zip((6, 22, 60, 10, 10), "ABCDE"): ws.column_dimensions[c].width = w_
for k in sorted(res):
    name, rule, st, n, rows_ = res[k]
    t = wb.create_sheet(re.sub(r"[\\/*?:\[\]]", "-", f"{k} {name}")[:31])
    if not rows_: t.cell(1, 1, "nothing found").font = F; continue
    keys = list(rows_[0].keys())
    for i, h in enumerate(keys, 1):
        c = t.cell(1, i, h); c.font = HB; c.fill = HF; t.column_dimensions[c.column_letter].width = 14 if h not in ("what", "problem", "lines", "line", "heard") else 50
    for r_, x in enumerate(rows_, 2):
        for i, h in enumerate(keys, 1):
            c = t.cell(r_, i, x[h]); c.font = F; c.alignment = Alignment(wrap_text=True, vertical="top")
wb.save(a.out)
for k in sorted(res):
    print(f"QC {k} {res[k][0]}: {res[k][2]} ({res[k][3]} findings)")
print("saved", a.out, "| shots on screen:", len(vis), "| story check usage:", usage)
