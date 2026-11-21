"""Trim groups/clips that play faster than 1.25x. Deterministic, no Claude.

Same two cases as the EP69-71 local run (after the slow-only scene edit):

  Case 1 — narration group: fastest inner clip first, cut from the last of that clip.
  Case 2 — lip group: trim narration first; if none, last part of the group.

Usage: fast_trim.py <work>
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from dubstudio.pipeline.keep_grouping import fit_overlapping_ms

THRESHOLD = 1.25
MIN_KEEP_MS = 400
MIN_CUT_MS = 80


def load(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default


def spd(s):
    d = s["it_end_ms"] - s["it_start_ms"]
    u = s["us_end_ms"] - s["us_start_ms"]
    return (u / d) if d > 1 else 0.0


def gspd(segs):
    d = sum(s["it_end_ms"] - s["it_start_ms"] for s in segs)
    u = sum(s["us_end_ms"] - s["us_start_ms"] for s in segs)
    return (u / d) if d > 1 else 0.0


def trim_last(s, keep_us, why):
    u0, u1 = s["us_start_ms"], s["us_end_ms"]
    dur = u1 - u0
    min_keep = min(MIN_KEEP_MS, max(80.0, dur - MIN_CUT_MS))
    keep = max(min_keep, min(dur, keep_us))
    take = dur - keep
    if take < 20:
        return 0.0
    s["us_end_ms"] = u0 + keep
    it = s["it_end_ms"] - s["it_start_ms"]
    s["stretch_factor"] = round(it / keep, 3) if keep else s.get("stretch_factor")
    note = (s.get("note") or "").strip()
    extra = f"fast-trim {take / 1000:.2f}s from last ({why})"
    s["note"] = (note + "; " + extra).lstrip("; ")
    return take


def bring_clip_to_cap(s, why):
    it = s["it_end_ms"] - s["it_start_ms"]
    if it <= 1 or spd(s) <= THRESHOLD + 0.005:
        return 0.0
    return trim_last(s, THRESHOLD * it, why)


def pick_fastest(cands):
    cands = [s for s in cands if (s["us_end_ms"] - s["us_start_ms"]) > MIN_KEEP_MS + MIN_CUT_MS]
    if not cands:
        return None
    return max(cands, key=spd)


def run(W: Path):
    W = Path(W)
    grp_path = W / "manifest_groups.grouping.json"
    if not grp_path.exists():
        grp_path = W / "manifest_groups.auto.json"
    man_path = W / "manifest_groups.json"
    if not man_path.exists():
        man_path = W / "scene_slowfix" / "manifest_slowfix.json"
    M = json.loads(man_path.read_text())
    G = json.loads(grp_path.read_text()) if grp_path.exists() else {"groups": []}
    lip_rows = {int(k) for k in (load(W / "lipsync.json", {}) or {}).get("rows") or {}}
    segs = M["segments"]

    def segs_for(g):
        rows = set(g["rows"])
        t0, t1 = g["it0"], g["it1"]
        out = []
        for s in segs:
            if s.get("video"):
                continue
            if s["it_idx"] in rows or (t0 - 1 <= s["it_start_ms"] < t1):
                out.append(s)
        return out

    report = {"threshold": THRESHOLD, "clips": [], "groups": []}
    new_cuts = []

    def record(s, take, case, gi, before):
        if take <= 0:
            return
        new_cuts.append(s["it_end_ms"])
        report["clips"].append(dict(
            row=s["it_idx"], group=gi, case=case,
            speed_before=round(before, 3), speed_after=round(spd(s), 3),
            cut_s=round(take / 1000, 3), why=s.get("note", ""),
        ))

    for gi, g in enumerate(G.get("groups") or []):
        gs = segs_for(g)
        if not gs:
            continue
        lip_in = [s for s in gs if s["it_idx"] in lip_rows]
        note = (g.get("note") or "") + " " + " ".join(g.get("flags") or [])
        is_lip_group = bool(g.get("lip") or g.get("lipgroup") or lip_in or "lip" in note.lower())
        if not is_lip_group:
            fit_overlapping_ms(gs)
        before_g = gspd(gs)
        if before_g <= THRESHOLD + 0.005:
            continue
        case = "lip" if is_lip_group else "narration"
        actions = []
        if case == "narration":
            blocked = set()
            while gspd(gs) > THRESHOLD + 0.005:
                over = [s for s in gs if id(s) not in blocked and spd(s) > THRESHOLD + 0.005]
                s = pick_fastest(over)
                if s is None:
                    break
                b = spd(s)
                take = bring_clip_to_cap(s, "case1 narration: fastest inner clip")
                if take <= 0:
                    blocked.add(id(s))
                    continue
                record(s, take, "1-narration-fastest", gi, b)
                actions.append(f"IT#{s['it_idx']} -{take / 1000:.2f}s from last")
        else:
            guard = 0
            while gspd(gs) > THRESHOLD + 0.005 and guard < 40:
                guard += 1
                need_g = sum(x["us_end_ms"] - x["us_start_ms"] for x in gs) - THRESHOLD * sum(
                    x["it_end_ms"] - x["it_start_ms"] for x in gs)
                narr_ok = [s for s in gs if s["it_idx"] not in lip_rows
                           and (s["us_end_ms"] - s["us_start_ms"]) > MIN_KEEP_MS + MIN_CUT_MS]
                if narr_ok:
                    s = pick_fastest(narr_ok)
                    b = spd(s)
                    it = s["it_end_ms"] - s["it_start_ms"]
                    keep_cap = THRESHOLD * it if spd(s) > THRESHOLD + 0.005 else (
                        s["us_end_ms"] - s["us_start_ms"] - need_g)
                    keep_target = max(MIN_KEEP_MS, min(s["us_end_ms"] - s["us_start_ms"] - MIN_CUT_MS, keep_cap))
                    take = trim_last(s, keep_target, "case2 lip group: narration, not the lip part")
                    if take <= 0:
                        break
                    record(s, take, "2-lip-trim-narration", gi, b)
                    actions.append(f"IT#{s['it_idx']} narration -{take / 1000:.2f}s from last")
                    continue
                last = gs[-1]
                b = spd(last)
                keep = max(MIN_KEEP_MS, (last["us_end_ms"] - last["us_start_ms"]) - need_g)
                take = trim_last(last, keep, "case2 lip group: no narration, last part")
                if take <= 0:
                    break
                record(last, take, "2-lip-trim-last", gi, b)
                actions.append(f"IT#{last['it_idx']} last -{take / 1000:.2f}s")
        report["groups"].append(dict(
            gi=gi, rows=g["rows"], case=case, speed_before=round(before_g, 3),
            speed_after=round(gspd(gs), 3), actions=actions,
        ))

    for s in segs:
        if s.get("video") or spd(s) <= THRESHOLD + 0.005:
            continue
        b = spd(s)
        kind = "2-leftover-lip" if s["it_idx"] in lip_rows else "1-leftover-narration"
        take = bring_clip_to_cap(s, "leftover clip over 1.25x, from last")
        record(s, take, kind, None, b)

    bak = man_path.with_suffix(man_path.suffix + ".before_fast_trim.json")
    if man_path.name.endswith(".json"):
        bak = man_path.parent / (man_path.stem + ".before_fast_trim.json")
    if not bak.exists():
        shutil.copy(man_path, bak)
    json.dump(M, open(man_path, "w"), indent=1, ensure_ascii=False)
    if man_path.name != "manifest_groups.json":
        json.dump(M, open(W / "manifest_groups.json", "w"), indent=1, ensure_ascii=False)
    slow = W / "scene_slowfix" / "manifest_slowfix.json"
    if slow.parent.is_dir():
        json.dump(M, open(slow, "w"), indent=1, ensure_ascii=False)
    json.dump(report, open(W / "fastfix.json", "w"), indent=1, ensure_ascii=False)
    json.dump({"new_cuts_ms": [round(x) for x in new_cuts]}, open(W / "fastfix_cuts.json", "w"))

    still = [
        (s["it_idx"], round(spd(s), 2))
        for s in segs
        if not s.get("video") and (s["it_end_ms"] > s["it_start_ms"]) and spd(s) > THRESHOLD + 0.01
    ]
    print(
        f"fast-trim: {len(report['groups'])} groups, {len(report['clips'])} clips, "
        f"{sum(c['cut_s'] for c in report['clips']):.1f}s source cut; "
        f"still over {THRESHOLD}x: {still or 'none'}"
    )
    for g in report["groups"]:
        print(f"  G{g['gi']:02d} {g['case']:9s} {g['speed_before']:.2f}x -> {g['speed_after']:.2f}x  "
              f"{'; '.join(g['actions']) or '(no cut)'}")
    return 0


if __name__ == "__main__":
    sys.exit(run(Path(sys.argv[1])))
