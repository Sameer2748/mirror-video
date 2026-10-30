#!/usr/bin/env python3
"""run_report.py <W> [out.md] -- the time (and AI cost) of every step of one episode, as a table for the owner (Step 11).

Reads W/run_times.tsv, one line per step run: name <TAB> seconds <TAB> gateway spend before <TAB> spend after <TAB>
exit code <TAB> clock time. run_episode.sh writes it; when steps are run by hand, append one line per step in the same
format (spend may be NA). A failed try is listed (red in the owner's eyes: time lost) and counted in the clock total.
The gateway books a call up to a minute late, so a late booking is added to the AI step before it."""
import sys

AI_STEPS = {"ai_match", "library", "scene_edit", "translate"}
NAMES = {"srt_dub": "Whisper subtitles, dub", "srt_src": "Whisper subtitles, source", "srt_de": "Whisper subtitles, dub",
         "srt_us": "Whisper subtitles, source", "lip_nets": "Lip scan, faces + talking (S3FD, TalkNet)",
         "lip_mesh": "Lip scan, mouth (Face Mesh)", "lip_merge": "Lip scan, join", "words_src": "Word times, source",
         "words_dub": "Word times, dub", "analyze": "Automatic alignment", "ai_match": "Step 2 by Claude (matching)",
         "sheet": "Mapping sheet + integrity check", "manifest": "Edit list", "lipsync": "Lip-sync rows",
         "groups": "Groups (automatic edit, fast-clip rule)", "library": "Shot library (Claude)",
         "scene_edit": "Scene edit (Claude cuts the picture)", "use_scene": "Scene edit -> edit list", "render": "Render",
         "verify": "Picture check", "bookends": "Opening / ending", "trans_plan": "Transition plan", "transitions": "Transitions",
         "captions": "Source captions (OCR)", "dynsrt": "Dynamic subtitle cards", "subs_dub": "Dub subtitle video",
         "subs_de": "Dub subtitle video", "translate": "English translation", "subs_en": "English subtitle video",
         "scene_sheet": "Scene-edit sheet", "check": "Final check"}


def num(x):
    try: return float(x)
    except Exception: return None


def main():
    W = sys.argv[1]; rows = []
    for line in open(f"{W}/run_times.tsv", encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 5: rows.append(dict(name=p[0], sec=int(float(p[1])), s0=num(p[2]), s1=num(p[3]), rc=p[4], at=p[5] if len(p) > 5 else ""))
    cost, last_ai = {}, None
    for i, r in enumerate(rows):
        d = (r["s1"] - r["s0"]) if r["s1"] is not None and r["s0"] is not None else 0.0
        if i and r["s0"] is not None and rows[i - 1]["s1"] is not None: d += r["s0"] - rows[i - 1]["s1"]   # booked between steps
        key = i if (r["name"] in AI_STEPS or last_ai is None) else last_ai
        cost[key] = cost.get(key, 0) + max(d, 0)
        if r["name"] in AI_STEPS: last_ai = i
    fm = lambda s: f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s"
    out = ["| # | Step | Time | Claude cost | Result |", "|---|---|---|---|---|"]
    ok_t = fail_t = 0
    for i, r in enumerate(rows):
        failed = r["rc"] != "0"; ok_t += 0 if failed else r["sec"]; fail_t += r["sec"] if failed else 0
        c = cost.get(i, 0)
        out.append(f"| {i + 1} | {NAMES.get(r['name'], r['name'])} | {fm(r['sec'])} | {'$%.2f' % c if c >= 0.005 else '-'} | "
                   + ("FAILED try (rerun below)" if failed else "ok") + " |")
    ok_c = sum(c for i, c in cost.items() if rows[i]["rc"] == "0"); fail_c = sum(c for i, c in cost.items() if rows[i]["rc"] != "0")
    out.append(f"| | **Total (steps that worked)** | **{fm(ok_t)}** | **${ok_c:.2f}** | |")
    if fail_t: out.append(f"| | Failed tries (lost) | {fm(fail_t)} | ${fail_c:.2f} | |")
    big = sorted((r for r in rows if r["rc"] == "0"), key=lambda r: -r["sec"])[:3]
    out.append("")
    out.append("Biggest steps: " + ", ".join(f"{NAMES.get(r['name'], r['name'])} {fm(r['sec'])}" for r in big)
               + f". Clock time from first to last step: about {fm(ok_t + fail_t)} of step time (plus any waiting between runs).")
    txt = "\n".join(out); print(txt)
    if len(sys.argv) > 2: open(sys.argv[2], "w", encoding="utf-8").write(txt + "\n")


if __name__ == "__main__":
    main()
