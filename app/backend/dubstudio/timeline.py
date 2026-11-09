"""The editor's timeline: built from the pipeline's edit list (manifest_groups.json), edited in the Editor screen,
written back to manifest_groups.json for `render`.

A clip = one segment of the edit list: it fills the dub slot t0..t1 (ms on the dub timeline) with source footage
s0..s1 (ms in the source video), played at speed (s1 - s0) / (t1 - t0). The timeline is always exactly as long as
the dub and has no holes (design: "The editor"); `validate` enforces that before anything is saved or rendered.

Flags (design: "Review flags") are computed here from what the pipeline measured, so the screen and the tests use one
rule set. Versions are kept in the timeline_versions table; the pipeline's own edit list is kept beside ours as
manifest_groups.pipeline.json the first time the editor writes the file."""
import hashlib, json, re, shutil, subprocess, threading, time
from array import array
from pathlib import Path

PIPELINE_COPY = "manifest_groups.pipeline.json"
REVIEW_OK = "review_ok.json"
QUOTES = re.compile(r"[«»“”\"„]|^\s*[—–-]")


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def pipeline_manifest(work):
    """The pipeline's own edit list: manifest_groups.json, unless the editor wrote that file (then the saved copy)."""
    w = Path(work); cur = _load(w / "manifest_groups.json")
    if cur is None:
        return None
    if cur.get("dub_studio"):
        return _load(w / PIPELINE_COPY, cur)
    return cur


def base_id(manifest):
    segs = [(round(s["it_start_ms"]), round(s["it_end_ms"]), round(s["us_start_ms"]), round(s["us_end_ms"]))
            for s in manifest["segments"]]
    return hashlib.sha1(json.dumps(segs).encode()).hexdigest()[:12]


def build_v1(work):
    """Timeline version 1 = the pipeline's edit list, one clip per segment."""
    m = pipeline_manifest(work)
    if m is None:
        return None
    clips = []
    for k, s in enumerate(sorted(m["segments"], key=lambda s: s["it_start_ms"])):
        # the pipeline's own times, unrounded: render's clip cache is keyed on them (to 0.001 ms), so an unedited
        # clip renders from the cache exactly as before
        c = dict(id=f"c{k}", row=s.get("it_idx"), t0=round(s["it_start_ms"], 3), t1=round(s["it_end_ms"], 3),
                 s0=round(s["us_start_ms"], 3), s1=round(s["us_end_ms"], 3), dub_text=s.get("it_text") or "",
                 src_text=s.get("us_text") or "", note=s.get("note") or "")
        for k2 in ("broll", "zoom"):                  # scene edit: a B-roll piece, a repeat shown zoomed in
            if s.get(k2):
                c[k2] = s[k2]
        c["orig"] = dict(t0=c["t0"], t1=c["t1"], s0=c["s0"], s1=c["s1"])
        clips.append(c)
    if is_scene_edit(work):                           # the scene edit already placed every not-matched line (gap plan)
        holes = []
    else:
        clips, holes = _cut_holes(clips, _matching_notes(Path(work))["not_found"])
    return dict(base=base_id(m), total_ms=m["total_ms"], clips=clips, v2=[], approved=[], version=0, holes=holes)


def holes(work):
    """The rows no footage was found for: [{row, t0, t1, text}] (they start as gaps on V1)."""
    return [dict(row=g["row"], t0=float(g["t0"]), t1=float(g["t1"]), text=g.get("text", ""))
            for g in _matching_notes(Path(work))["not_found"] if g["t1"] - g["t0"] >= 34]


def is_scene_edit(work):
    """The edit list is the scene edit (Step 4C ran: use_scene kept the automatic one as manifest_groups.auto.json)."""
    return (Path(work) / "manifest_groups.auto.json").exists()


def auto_edit(work):
    """The automatic edit (before the scene edit), read-only, as clips for the editor's 'automatic edit' row."""
    m = _load(Path(work) / "manifest_groups.auto.json")
    if not m:
        return None
    return [dict(t0=round(s["it_start_ms"], 3), t1=round(s["it_end_ms"], 3), s0=round(s["us_start_ms"], 3),
                 s1=round(s["us_end_ms"], 3), row=s.get("it_idx"), note=s.get("note") or "")
            for s in sorted(m["segments"], key=lambda s: s["it_start_ms"])]


def _cut_holes(clips, not_found):
    """Rows no footage was found for (even after Claude's second look) stay EMPTY on V1: the clip the pipeline
    stretched over them is cut back, so the editor sees a gap there and puts a clip in it himself (Render waits
    until it is filled). -> (clips, holes [{row, t0, t1, text}])"""
    holes = []
    for g in not_found:
        a, b = float(g["t0"]), float(g["t1"])
        if b - a < 34:
            continue
        out = []
        for c in clips:
            if _overlap(c["t0"], c["t1"], a, b) <= 0:
                out.append(c); continue
            for k, (x0, x1) in enumerate(((c["t0"], a), (b, c["t1"]))):
                if x1 - x0 < 34:
                    continue
                piece = dict(c, id=c["id"] + ("" if k == 0 else "b"), t0=round(x0, 3), t1=round(x1, 3),
                             s0=round(_src_at(c, x0), 3), s1=round(_src_at(c, x1), 3))
                piece["orig"] = dict(t0=piece["t0"], t1=piece["t1"], s0=piece["s0"], s1=piece["s1"])
                out.append(piece)
        clips = out
        holes.append(dict(row=g["row"], t0=a, t1=b, text=g.get("text", "")))
    return clips, holes


# ---------------- context the flags and the screen need ----------------
def context(work):
    w = Path(work)
    rows = (_load(w / "rows.json") or {}).get("rows", [])
    lip = _load(w / "lipsync.json") or {}
    m = pipeline_manifest(work) or {}
    drift = {}
    for g in m.get("groups", []):
        for r, d in (g.get("drift") or {}).items():
            drift[int(r)] = d
    srcs = (_load(w / "sentences_src.json") or {}).get("sentences", [])
    dubs = (_load(w / "sentences_dub.json") or {}).get("sentences", [])
    return dict(
        rows={r["idx"]: r for r in rows},
        lip_rows={int(k) for k in (lip.get("rows") or {})},
        lip_intervals=[(a * 1000, b * 1000) for a, b in lip.get("intervals", [])],
        has_lip=bool(lip),
        drift=drift,
        src_sentences=[dict(idx=s["idx"], text=s["text"], start_ms=s["start_ms"], end_ms=s["end_ms"]) for s in srcs],
        dub_starts=[s["start_ms"] for s in dubs if s.get("start_ms") is not None],
        sharp=_load(w / SHARPNESS),
        trimmed=_trimmed(w),
        **_matching_notes(w),
    )


def _trimmed(w):
    """Rows the software trimmed under the fast-clip rule (owner, 2026-10-26: "mark it as trimmed so the user can check"):
    {row: [what was cut]} from W/fastfix.json -- clip-level trims (fastfix) and group-level trims (`groups --trim-fast`)."""
    F = _load(w / "fastfix.json") or {}; out = {}
    for c in F.get("clips") or []:
        out.setdefault(c["row"], []).append(
            f"clip x{c['speed_before']:.2f} -> x{c['speed_after']:.2f}, {c['cut_s']:.2f} s cut "
            f"({'Claude chose the cut' if c.get('by') == 'Claude' else c.get('by', 'rule')})")
    for g in F.get("group_trims") or []:
        for t in g.get("trims") or []:
            out.setdefault(t["row"], []).append(
                f"group IT#{g['rows'][0]}-{g['rows'][-1]} x{g['speed_before']:.2f} -> x{g['speed_after']:.2f}, "
                f"{t['ms'] / 1000:.2f} s cut from this line")
    return out


def _matching_notes(w):
    """Rows with no source footage (gaps in decisions.json, whoever decided them) and rows the automatic AI matching
    was unsure about (only while its decisions are the ones in use)."""
    prep = _load(w / "prep.json") or {}; dub = prep.get("dub") or []
    d = _load(w / "decisions.json") or {}
    gaps = []
    for k in sorted(map(int, d.get("gap") or {})):
        if k < len(dub) and dub[k].get("start_ms") is not None:
            gaps.append(dict(row=k, t0=dub[k]["start_ms"], t1=dub[k]["end_ms"], text=dub[k].get("text", ""),
                             why=str(d["gap"][str(k)])[:160]))
    auto = _load(w / "ai_auto.json") or {}
    spec = (w / "spec.py").read_text() if (w / "spec.py").exists() else None
    low = {x["row"]: x for x in auto.get("low", [])} if auto.get("spec") and auto.get("spec") == spec else {}
    return dict(not_found=gaps, ai_low=low)


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


# ---------------- two video tracks ----------------
# V1 = the clips from the pipeline (holes allowed while editing), V2 = overlay clips the editor adds on top. What plays
# (and what is rendered) is the flattened picture: V2 where it has a clip, else V1, else a gap (black).
def all_clips(tl):
    return list(tl["clips"]) + list(tl.get("v2") or [])


def _src_at(c, t):
    return c["s0"] + (t - c["t0"]) * (c["s1"] - c["s0"]) / max(c["t1"] - c["t0"], 1e-6)


def flatten(tl):
    """-> [{t0, t1, s0, s1, id, track, row, ...}] covering 0..total_ms in order; gaps as {t0, t1, gap: True}.
    A hidden video row (tl["tracks"]["v1"|"v2"]["hidden"], the eye in the editor) is left out: it neither plays nor
    renders."""
    tr = tl.get("tracks") or {}
    v1 = [] if (tr.get("v1") or {}).get("hidden") else sorted(tl["clips"], key=lambda c: c["t0"])
    v2 = [] if (tr.get("v2") or {}).get("hidden") else sorted(tl.get("v2") or [], key=lambda c: c["t0"])
    total = tl["total_ms"]
    cuts_ = sorted({0.0, float(total)} | {float(x) for c in v1 + v2 for x in (c["t0"], c["t1"]) if 0 < x < total})
    out = []
    for a, b in zip(cuts_, cuts_[1:]):
        if b - a < 1e-6:
            continue
        mid = (a + b) / 2
        c = next((x for x in v2 if x["t0"] <= mid < x["t1"]), None) or next((x for x in v1 if x["t0"] <= mid < x["t1"]), None)
        if c is None:
            if out and out[-1].get("gap"):
                out[-1]["t1"] = b
            else:
                out.append(dict(t0=a, t1=b, gap=True))
            continue
        if out and not out[-1].get("gap") and out[-1]["id"] == c["id"]:
            out[-1]["t1"] = b; out[-1]["s1"] = _src_at(c, b)
            continue
        out.append(dict(t0=a, t1=b, s0=_src_at(c, a), s1=_src_at(c, b), id=c["id"], track=2 if c in v2 else 1,
                        row=c.get("row"), dub_text=c.get("dub_text", ""), src_text=c.get("src_text", ""),
                        note=c.get("note") or "", tin=c.get("tin") if abs(a - c["t0"]) < 1 else None,
                        media=c.get("media"), broll=c.get("broll"), zoom=c.get("zoom")))
    return out


def gaps(tl, min_ms=34):
    return [p for p in flatten(tl) if p.get("gap") and p["t1"] - p["t0"] >= min_ms]


def flags(tl, ctx, limits, markers=None):
    """{clip id: [{kind, sev: red|yellow|info, msg}]} for every clip. Open QC markers (design: "QC marker") are red
    flags on the clip under them; approving the clip does not close them, only resolving the marker does."""
    L = {**dict(slow=0.67, slow_red=0.5, fast=1.5, fast_red=2.0, drift_ms=500, short_ms=500, blur=0.25), **(limits or {})}
    clips = all_clips(tl); approved = set(tl.get("approved") or [])
    flat = flatten(tl)
    shown = {}                                            # visible ms per clip (a V1 clip under V2 may be hidden)
    for p_ in flat:
        if not p_.get("gap"):
            shown[p_["id"]] = shown.get(p_["id"], 0) + p_["t1"] - p_["t0"]
    v1_ids = {c["id"] for c in tl["clips"]}
    sh = ctx.get("sharp") or {}; sv = sh.get("values") or []; sfps = sh.get("fps") or 2
    smed = sorted(sv)[len(sv) // 2] if sv else 0
    out = {}
    for i, c in enumerate(clips):
        f = []; slot = c["t1"] - c["t0"]; src = c["s1"] - c["s0"]
        if shown.get(c["id"], 0) < 34:                 # fully under a V2 clip: nothing of it plays
            out[c["id"]] = [dict(kind="hidden", sev="info", msg="covered by a V2 clip: not seen")]
            continue
        edited = any(abs(c[k] - c["orig"][k]) > 1 for k in ("t0", "t1", "s0", "s1")) if c.get("orig") else True
        if src <= 0:
            f.append(dict(kind="gap", sev="red", msg="no footage in this slot"))
        else:
            # Scene edit keeps pieces in 0.8–1.25x. Flag yellow/red start at 0.67 / 1.5, so a clip that
            # already sits in that range is not flagged; leftovers (matching-fill, a hole Claude left)
            # still show for the later editor pass and do not block a list render.
            sp = src / max(slot, 1)
            if sp < L["slow_red"]:
                f.append(dict(kind="slow", sev="red", msg=f"plays ×{sp:.2f} (very slow motion)"))
            elif sp < L["slow"]:
                f.append(dict(kind="slow", sev="yellow", msg=f"plays ×{sp:.2f} (slow motion)"))
            elif sp > L["fast_red"]:
                f.append(dict(kind="fast", sev="red", msg=f"plays ×{sp:.2f} (very fast)"))
            elif sp > L["fast"]:
                f.append(dict(kind="fast", sev="yellow", msg=f"plays ×{sp:.2f} (fast)"))
        if slot < L["short_ms"]:
            f.append(dict(kind="short", sev="yellow", msg=f"only {slot / 1000:.2f} s on screen (a flash)"))
        if c.get("media"):                              # an imported clip: its own file, none of the source's checks
            f.append(dict(kind="imported", sev="info", msg=f"imported clip ({c.get('src_text') or c['media']})"))
            if c["id"] not in v1_ids:
                f.append(dict(kind="v2", sev="info", msg="on V2 (over V1)"))
            out[c["id"]] = f
            continue
        # same footage twice. A B-roll piece is *meant* to show a shot again (zoomed, at most 3 times, 8 s apart --
        # sceneedit.py's own rules, checked again by the Final QC), so that is a note, not something to fix.
        # A matching-clip fill (scene edit left a hole; we reused the matched neighbour) is the same: planned.
        matching = lambda x: str(x.get("note") or "").startswith("matching clip")
        for j, d in enumerate(clips):
            if j != i and not d.get("media") and src > 0 and shown.get(d["id"], 0) >= 34 and _overlap(c["s0"], c["s1"], d["s0"], d["s1"]) > 300:
                planned = c.get("broll") or d.get("broll") or matching(c) or matching(d)
                f.append(dict(kind="repeat", sev="yellow" if planned else "red",
                              msg=(f"B-roll: the same shot as {label(d)} at {fmt(d['t0'])}" if planned
                                   else f"same footage as {label(d)} at {fmt(d['t0'])}")))
                break
        if smed > 0 and src > 0 and L.get("blur"):
            x = sv[int(c["s0"] / 1000 * sfps):int(c["s1"] / 1000 * sfps) + 1] or sv[min(len(sv) - 1, int(c["s0"] / 1000 * sfps)):][:1]
            ratio = sum(x) / len(x) / smed if x else 1
            if ratio < L["blur"]:
                f.append(dict(kind="blur", sev="yellow",
                              msg=f"soft picture ({ratio:.0%} of this episode's usual sharpness): check it is not blurry"))
        for g in ctx.get("not_found") or []:          # a dub line with no matching footage: fill it by hand
            if _overlap(c["t0"], c["t1"], g["t0"], g["t1"]) > min(300, (g["t1"] - g["t0"]) / 2):
                if edited:
                    f.append(dict(kind="not_found", sev="info", msg=f"IT#{g['row']} had no matching footage; changed in the editor"))
                elif c.get("broll") or str(c.get("note") or "").startswith("matching clip"):
                    f.append(dict(kind="not_found", sev="info",
                                  msg=f"IT#{g['row']} had no footage of its own; the scene edit placed this shot here"))
                else:
                    f.append(dict(kind="not_found", sev="red", row=g["row"],
                                  msg=f"not found: no footage matches IT#{g['row']} \"{g['text'][:60]}\" "
                                      f"({fmt(g['t0'])}–{fmt(g['t1'])}); this clip is stretched over it — add a clip here, or approve to keep it"))
        lo = (ctx.get("ai_low") or {}).get(c.get("row"))
        if lo and not edited:
            f.append(dict(kind="ai_unsure", sev="yellow",
                          msg=f"the AI was unsure of this match (confidence {lo.get('confidence')}): {lo.get('reason', '')[:120]}"))
        row = ctx["rows"].get(c.get("row"))
        lip_row = c.get("row") in ctx["lip_rows"]
        if ctx["has_lip"] and not lip_row and not QUOTES.search(c.get("dub_text") or ""):
            lip_s = sum(_overlap(c["s0"], c["s1"], a, b) for a, b in ctx["lip_intervals"]) / 1000
            if lip_s >= 0.4:
                f.append(dict(kind="talking", sev="yellow", msg=f"a mouth moves for {lip_s:.1f} s on a narration line"))
        if not edited and c.get("row") in ctx["drift"] and ctx["drift"][c["row"]] > max(L["drift_ms"], 1000):
            f.append(dict(kind="drift", sev="yellow", msg=f"picture {ctx['drift'][c['row']] / 1000:.1f} s from its voice"))
        if not edited and row and (row.get("score") is not None and row["score"] < 0.5):
            f.append(dict(kind="low_score", sev="info", msg=f"weak match (score {row['score']:.2f})"))
        if c["id"] not in v1_ids:
            f.append(dict(kind="v2", sev="info", msg="on V2 (over V1)"))
        elif i and src > 0 and c["s0"] < clips[i - 1]["s0"] - 1500:
            f.append(dict(kind="order", sev="info", msg="jumps back in the source"))
        tr = (ctx.get("trimmed") or {}).get(c.get("row"))
        if tr and not edited:                           # cut by the fast-clip rule: the editor checks it (approve = checked)
            f.append(dict(kind="trimmed", sev="yellow", msg="trimmed by the software (fast clip over x1.25): "
                          + "; ".join(tr) + " -- check the cut"))
        if lip_row:
            f.append(dict(kind="lip", sev="info", msg="lip-sync row (a face speaks this line)"))
        if edited:
            f.append(dict(kind="edited", sev="info", msg="changed in the editor"))
        if c["id"] in approved:
            for x in f:
                if x["sev"] in ("red", "yellow"):
                    x["approved"] = True
        out[c["id"]] = f
    for m in markers or []:
        if m.get("resolved") or not clips:
            continue
        p_ = next((x for x in flat if not x.get("gap") and x["t0"] <= m["t_ms"] < x["t1"]), None) or \
            next((x for x in flat if not x.get("gap")), None)
        if p_ is None or p_["id"] not in out:
            continue
        out[p_["id"]].insert(0, dict(kind="qc", sev="red", marker=m["id"],
                                     msg=f"QC {fmt(m['t_ms'])}: {m.get('text') or 'note'}"))
    return out


def _find(clips, t):
    lo, hi = 0, len(clips) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if t >= clips[mid]["t1"]:
            lo = mid + 1
        else:
            hi = mid
    return lo


TIME_AT_START = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2}(?:[.,]\d+)?)\s*[-–—:]?\s*")


def parse_marker(text):
    """'06:04 Viola smiling' -> (364000, 'Viola smiling'); no time at the start -> (None, text)."""
    m = TIME_AT_START.match(text or "")
    if not m:
        return None, (text or "").strip()
    h, mi, se = int(m.group(1) or 0), int(m.group(2)), float(m.group(3).replace(",", "."))
    return round(((h * 60 + mi) * 60 + se) * 1000), text[m.end():].strip()


def open_counts(fl):
    red = sum(1 for f in fl.values() for x in f if x["sev"] == "red" and not x.get("approved"))
    yellow = sum(1 for f in fl.values() for x in f if x["sev"] == "yellow" and not x.get("approved"))
    return dict(red=red, yellow=yellow)


def fit_episode(tl, src_duration_ms=None, media_ms=None):
    """Trim any clip that starts before 0 or ends after the dub (and any source span past the
    video). Keeps the same speed. Scene edit often writes the last t1 1–10 ms past total_ms
    because Claude answers in hundredths of a second."""
    total = tl.get("total_ms")
    for c in all_clips(tl):
        t0, t1 = c["t0"], c["t1"]
        span = t1 - t0
        if span > 0 and total is not None:
            nt0, nt1 = max(t0, 0.0), min(t1, float(total))
            if nt1 > nt0 and (nt0 != t0 or nt1 != t1):
                c["s0"] = _src_at(c, nt0)
                c["s1"] = _src_at(c, nt1)
                c["t0"], c["t1"] = nt0, nt1
        dur = (media_ms or {}).get(c["media"], -1) if c.get("media") else src_duration_ms
        if dur and dur > 0:
            if c["s0"] < 0:
                c["s0"] = 0
            if c["s1"] > dur:
                c["s1"] = dur
    return tl


def label(c):
    return f"IT#{c.get('row')}" if c.get("row") is not None else f"clip {fmt(c['s0'])}"


def fmt(ms):
    s = ms / 1000
    return f"{int(s // 60):02d}:{s % 60:04.1f}"


# ---------------- transitions per cut ----------------
# the pipeline's styles (dubstitch.TRANSITION_HALF: seconds on each side of the cut at length 1.0)
TRANSITIONS = {"light_leak": ("Light leak", 0.30), "blur_dissolve": ("Blur dissolve", 0.30),
               "zoom_blur": ("Zoom blur", 0.25), "glow": ("Glow", 0.25)}
TPLAN = "transitions_plan.json"


def cuts(tl, min_jump=50):
    """Clip ids that start at a clip switch (the source jumps), i.e. where the pipeline can put a transition."""
    fl = [p for p in flatten(tl) if not p.get("gap")]
    by_id = {c["id"]: c for c in all_clips(tl)}
    return [b["id"] for a, b in zip(fl, fl[1:])
            if abs(b["s0"] - a["s1"]) > min_jump and b["id"] in by_id and abs(b["t0"] - by_id[b["id"]]["t0"]) < 1]


def ok_times(tl):
    """Dub times (ms) of the timeline's clip switches."""
    ids = set(cuts(tl)); return [c["t0"] for c in all_clips(tl) if c["id"] in ids]


TPLAN_EDITOR = "transitions_editor.json"


def apply_editor_transitions(work):
    """After scene_transitions.py wrote W/transitions_plan.json: the transitions the editor picked on cuts win.
    -> how many picks were applied (0 = the plan is exactly the pipeline's)."""
    w = Path(work); e = _load(w / TPLAN_EDITOR) or {}; picks = e.get("cuts") or {}
    if not picks:
        return 0
    plan = _load(w / TPLAN) or {"default": "none", "cuts": {}}
    plan.setdefault("cuts", {}).update(picks)
    (w / TPLAN).write_text(json.dumps(plan, indent=1))
    return len(picks)


def _editor_transition_picks(tl):
    """Styles the editor set on clips. Any clip after the start of the episode can have one — not only source jumps —
    so a pick in the inspector is kept even when the footage continues from the clip before."""
    out = {}
    for c in all_clips(tl):
        tin = c.get("tin")
        if not tin or (tin not in TRANSITIONS and tin not in ("auto", "none")):
            continue
        if float(c.get("t0") or 0) <= 1:
            continue
        out[str(round(c["t0"]))] = tin
    return out


def write_transition_plan(work, tl, default):
    """The editor's choices for the pipeline's `transitions --plan`: {"default", "cuts": {dub ms of the cut: style}}.
    No choices on a show without transitions = no file (and no transitions step)."""
    f = Path(work) / TPLAN
    chosen = _editor_transition_picks(tl)
    if is_scene_edit(work):
        # a scene-edited episode: the `trans_plan` step makes the plan (scene_transitions.py, as run_episode.sh) and then
        # puts the editor's OWN picks on top -- they are kept here for it
        (Path(work) / TPLAN_EDITOR).write_text(json.dumps({"cuts": chosen}, indent=1))
        return None
    if default != "none":                               # jump cuts made by the fast-clip trim never get a transition
        fc = Path(work) / "fastfix_cuts.json"
        if fc.exists():
            try:
                for ms in json.loads(fc.read_text()).get("new_cuts_ms", []):
                    near = [c for c in ok_times(tl) if abs(c - ms) <= 60]
                    for c in near:
                        chosen.setdefault(str(round(c)), "none")
            except Exception:
                pass
    if not chosen or (default == "none" and all(v == "none" for v in chosen.values())):
        f.unlink(missing_ok=True); return None
    f.write_text(json.dumps({"default": default, "cuts": chosen}, indent=1)); return f


def transition_use(bodies, configs):
    """How often each style is used in this show: the editor's choices + the pipeline's own plan of past renders."""
    n = {k: 0 for k in TRANSITIONS}
    for b in bodies:
        for c in (b or {}).get("clips", []) + ((b or {}).get("v2") or []):
            if c.get("tin") in n:
                n[c["tin"]] += 1
    for cfg in configs:
        for q in ((cfg or {}).get("transitions") or {}).get("plan", []):
            if q.get("style") in n:
                n[q["style"]] += 1
    return n


# ---------------- scene edit: fall back to matching when Claude leaves a hole ----------------
_MIN_FILL_MS = 50       # smaller than this is rounding, not a real hole
_MIN_FOOTAGE_MS = 34    # one frame at 25 fps — a piece with no source is a placeholder


def _slice_matching(seg, t0, t1):
    """One matching/automatic clip cropped to dub range [t0, t1]. None if nothing usable is left."""
    a0, a1 = float(seg.get("it_start_ms") or 0), float(seg.get("it_end_ms") or 0)
    o0, o1 = max(a0, t0), min(a1, t1)
    span = a1 - a0
    if span <= 0 or o1 - o0 < _MIN_FILL_MS:
        return None
    u0, u1 = float(seg.get("us_start_ms") or 0), float(seg.get("us_end_ms") or 0)
    nu0 = u0 + (u1 - u0) * (o0 - a0) / span
    nu1 = u0 + (u1 - u0) * (o1 - a0) / span
    if nu1 - nu0 < _MIN_FOOTAGE_MS:
        return None
    out = dict(seg)
    out["it_start_ms"] = round(o0, 3)
    out["it_end_ms"] = round(o1, 3)
    out["us_start_ms"] = round(nu0, 3)
    out["us_end_ms"] = round(nu1, 3)
    out["stretch_factor"] = round((o1 - o0) / max(nu1 - nu0, 1), 3)
    out["note"] = "matching clip (scene edit left a hole)"
    out.pop("broll", None)
    out.pop("zoom", None)
    return out


def fill_scene_from_matching(scene_segs, auto_segs, total_ms, min_ms=_MIN_FILL_MS):
    """If the scene edit left a hole (or a placeholder with no source footage) and matching already had a clip
    there, use that matching clip. Never leave black screen when matching had picture.

    Matching-not-found stays a hole (the editor banner is then correct). Tiny leftover at the end (Claude writing
    hundredths of a second) is left for `use_scene` to stretch/trim."""
    kept = []
    for s in scene_segs or []:
        if float(s.get("it_end_ms") or 0) - float(s.get("it_start_ms") or 0) < min_ms:
            continue
        if float(s.get("us_end_ms") or 0) - float(s.get("us_start_ms") or 0) < _MIN_FOOTAGE_MS:
            continue
        kept.append(dict(s))
    kept.sort(key=lambda s: s["it_start_ms"])
    auto = sorted([a for a in (auto_segs or []) if not a.get("video")],
                  key=lambda s: s.get("it_start_ms") or 0)
    out = []
    t = 0.0
    total = float(total_ms or 0)

    def splice(t0, t1):
        for a in auto:
            piece = _slice_matching(a, t0, t1)
            if piece:
                out.append(piece)

    for s in kept:
        t0, t1 = float(s["it_start_ms"]), float(s["it_end_ms"])
        if t0 > t + min_ms:
            splice(t, t0)
        if t1 <= t:
            continue
        if t0 < t:
            span = t1 - t0
            if span <= 0:
                continue
            u0, u1 = float(s.get("us_start_ms") or 0), float(s.get("us_end_ms") or 0)
            s = dict(s)
            s["us_start_ms"] = u0 + (u1 - u0) * (t - t0) / span
            s["it_start_ms"] = t
            t0 = t
        out.append(s)
        t = max(t, t1)
    if total - t > min_ms:
        splice(t, total)
    return out


# ---------------- checks and writing ----------------
def validate(tl, src_duration_ms=None, media_ms=None):
    """Problems that would break the render or the sync: [] when the timeline is good. V1 and V2 each must not
    overlap themselves; what plays must have no gap (black) anywhere from 0 to the end of the dub."""
    probs = []
    if not tl["clips"] and not tl.get("v2"):
        return ["the timeline is empty"]
    for name, cl in (("V1", sorted(tl["clips"], key=lambda c: c["t0"])), ("V2", sorted(tl.get("v2") or [], key=lambda c: c["t0"]))):
        for a, b in zip(cl, cl[1:]):
            if a["t1"] - b["t0"] > 1:
                probs.append(f"{name}: {label(a)} and {label(b)} overlap at {fmt(b['t0'])}")
    for c in all_clips(tl):
        if c["t1"] - c["t0"] < 34:
            probs.append(f"{label(c)} at {fmt(c['t0'])} is shorter than one frame")
        if c["s1"] - c["s0"] < 34:
            probs.append(f"{label(c)} at {fmt(c['t0'])} has no footage")
        dur = (media_ms or {}).get(c["media"], -1) if c.get("media") else src_duration_ms
        if dur == -1:
            probs.append(f"{label(c)} at {fmt(c['t0'])}: its imported clip is missing")
        elif c["s0"] < 0 or (dur and c["s1"] > dur + 40):
            # 40 ms = one frame at 25 fps. Scene-edit rounding can sit 1–2 ms past ffprobe's duration
            # (TGS ep32 last fade: 779.113 s vs 779.112 s) and that is not a real overshoot.
            probs.append(f"{label(c)} at {fmt(c['t0'])} points outside the source video")
        if c["t0"] < -1 or c["t1"] > tl["total_ms"] + 1:
            probs.append(f"{label(c)} runs outside the episode")
    if ((tl.get("tracks") or {}).get("v1") or {}).get("hidden"):
        probs.append("V1 is hidden (the eye on V1): show it again before rendering")
    else:
        holes = tl.get("holes") or []
        for g in gaps(tl):
            h = next((h for h in holes if _overlap(h["t0"], h["t1"], g["t0"], g["t1"]) > 0), None)
            why = f" (IT#{h['row']}: no footage was found for it)" if h else ""
            probs.append(f"gap (black screen) at {fmt(g['t0'])}–{fmt(g['t1'])}{why}: fill it with a clip")
    return probs


def to_manifest(tl, work, version):
    """The editor's timeline in the pipeline's edit-list format (manifest_groups.json)."""
    base = pipeline_manifest(work) or {}
    segs = []
    flat = flatten(tl)
    for k, c in enumerate(flat):                       # what plays: V2 over V1 (a hair-thin gap goes to the clip before)
        if c.get("gap"):
            continue
        if k + 1 < len(flat) and flat[k + 1].get("gap") and flat[k + 1]["t1"] - flat[k + 1]["t0"] < 34:
            g = flat[k + 1]; c = dict(c, t1=g["t1"], s1=c["s1"] + (g["t1"] - c["t1"]) * (c["s1"] - c["s0"]) / max(c["t1"] - c["t0"], 1))
        slot = c["t1"] - c["t0"]; src = c["s1"] - c["s0"]
        segs.append(dict(kind="clip", it_idx=c.get("row"), it_start_ms=round(c["t0"], 3), it_end_ms=round(c["t1"], 3),
                         us_start_ms=round(c["s0"], 3), us_end_ms=round(c["s1"], 3), it_text=c.get("dub_text", ""),
                         us_text=c.get("src_text", ""), stretch_factor=round(slot / max(src, 1), 3),
                         note=c.get("note") or ""))
        for k2 in ("broll", "zoom"):                     # the scene edit's marks: transitions plan + the render's zoom
            if c.get(k2):
                segs[-1][k2] = c[k2]
        if c.get("media"):                              # an imported clip: the pipeline renders it from its own file
            from . import imports
            segs[-1]["video"] = imports.path_of(work, c["media"])
    return dict(total_ms=tl["total_ms"], segments=segs, bleed_report=[], groups=base.get("groups", []),
                dub_studio=dict(version=version, written=time.time()))


def write_manifest(work, tl, version):
    w = Path(work)
    cur = _load(w / "manifest_groups.json")
    if cur is not None and not cur.get("dub_studio"):
        shutil.copyfile(w / "manifest_groups.json", w / PIPELINE_COPY)   # keep the pipeline's own list once
    m = to_manifest(tl, work, version)
    tmp = w / "manifest_groups.json.part"
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(w / "manifest_groups.json")
    return m


def request_render(work, version):
    (Path(work) / REVIEW_OK).write_text(json.dumps(dict(version=version, consumed=False, at=time.time())))


def take_render_request(work):
    """The review gate: True once (and marks it used) when the editor asked for a render."""
    p = Path(work) / REVIEW_OK; r = _load(p)
    if r and not r.get("consumed"):
        r["consumed"] = True; p.write_text(json.dumps(r)); return True
    return False


# ---------------- media helpers ----------------
def parse_srt(path):
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return []
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = [x for x in block.strip().split("\n") if x.strip()]
        for k, ln in enumerate(lines):
            m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", ln)
            if m:
                g = [int(x) for x in m.groups()]
                t0 = ((g[0] * 60 + g[1]) * 60 + g[2]) * 1000 + g[3]; t1 = ((g[4] * 60 + g[5]) * 60 + g[6]) * 1000 + g[7]
                out.append(dict(t0=t0, t1=t1, text="\n".join(lines[k + 1:])))
                break
    return out


def _ts(ms):
    ms = max(0, int(round(ms))); h, ms = divmod(ms, 3_600_000); m, ms = divmod(ms, 60_000); sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def srt_text(cues):
    return "".join(f"{k}\n{_ts(c['t0'])} --> {_ts(c['t1'])}\n{c['text'].strip()}\n\n" for k, c in enumerate(cues, 1))


def check_cues(cues, total_ms=None):
    """Clean a subtitle track from the editor: sorted, every cue at least 0.1 s, words in it, no overlaps.
    -> (cues, problems)."""
    out, probs = [], []
    for c in sorted(cues or [], key=lambda c: float(c["t0"])):
        t0, t1, text = round(float(c["t0"])), round(float(c["t1"])), "\n".join(
            ln.strip() for ln in str(c.get("text") or "").strip().splitlines() if ln.strip())
        if not text:
            probs.append(f"the cue at {fmt(t0)} has no words"); continue
        if t1 - t0 < 100:
            probs.append(f"the cue at {fmt(t0)} is shorter than 0.1 s")
        if t0 < 0 or (total_ms and t0 >= total_ms):
            probs.append(f"the cue at {fmt(t0)} is outside the episode")
        if out and t0 < out[-1]["t1"] - 1:
            probs.append(f"the cues at {fmt(out[-1]['t0'])} and {fmt(t0)} overlap")
        out.append(dict(t0=t0, t1=t1, text=text))
    return out, probs


def edited_srt(work, lang):
    """The editor's subtitle file for `lang` ('dub' or 'en') in the work folder (the show's own SRT is never changed)."""
    return Path(work) / f"subs_edited_{lang}.srt"


SHARPNESS = "sharpness.json"
_sharp_lock = threading.Lock()


def make_sharpness(work, video, python, log=None):
    """W/sharpness.json from the preview copy (or the original): the blur flag's measure. Returns its path."""
    out = Path(work) / SHARPNESS
    with _sharp_lock:
        if out.exists():
            return out
        cmd = [str(python), str(Path(__file__).with_name("sharpness_tool.py")), "--video", str(video), "--out", str(out)]
        if log:
            log("$ " + " ".join(cmd))
        p = subprocess.run(cmd, capture_output=True, text=True)
        if log:
            log((p.stdout + p.stderr).strip()[-500:])
        if p.returncode != 0 or not out.exists():
            raise RuntimeError(f"sharpness: exit {p.returncode}: {(p.stdout + p.stderr).strip()[-200:]}")
        return out


def make_sharpness_bg(work, video, python):
    if (Path(work) / SHARPNESS).exists():
        return
    threading.Thread(target=lambda: _quiet(make_sharpness, work, video, python), daemon=True).start()


def _quiet(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass


PROXY = "proxy_540.mp4"
_proxy_jobs = {}
_proxy_lock = threading.Lock()


DUB_PREVIEW = "dub_preview.m4a"
_dub_lock = threading.Lock()


def dub_preview(work, audio, log=None):
    """The editor plays the dub from this exact AAC copy (48 kHz), not the show's file: browsers play some MP3s
    (TOLR ep24's 48 kHz MP3 without a header) faster than the time they report, so the voice ran ahead of picture
    and subtitles by 2 s after 20 s of play (measured 2026-10-20; the AAC copy stays within 20 ms). ffmpeg (render)
    reads the MP3 right, so only the preview needs it. Remade when the audio file changes. -> path"""
    w = Path(work); out = w / DUB_PREVIEW; key = w / "dub_preview.json"
    st = Path(audio).stat(); want = {"src": str(audio), "size": st.st_size, "mtime": st.st_mtime}
    with _dub_lock:
        if out.exists() and _load(key) == want:
            return out
        tmp = w / (DUB_PREVIEW + ".part.m4a"); w.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(audio), "-map", "0:a:0", "-c:a", "aac", "-b:a", "192k",
                            "-ar", "48000", "-movflags", "+faststart", str(tmp)], capture_output=True, text=True)
        if r.returncode:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("dub preview copy failed: " + r.stderr[-300:])
        tmp.replace(out); key.write_text(json.dumps(want))
        if log:
            log(f"dub preview copy: {out.name}")
        return out


def proxy_path(work):
    p = Path(work) / PROXY
    return p if p.exists() else None


def proxy_state(work):
    if proxy_path(work):
        return dict(ready=True, progress=1.0)
    j = _proxy_jobs.get(str(Path(work)))
    return dict(ready=False, running=bool(j and j.get("running")), progress=(j or {}).get("progress", 0.0),
                error=(j or {}).get("error"))


def make_proxy(work, video, duration_s=None, on_progress=None, log=None):
    """The 540-px preview copy (design: "Processing services" → proxy): H.264, a keyframe every 0.5 s so the editor
    seeks instantly, small AAC sound. Written as W/proxy_540.mp4; returns its path. Safe to call twice at once."""
    w = Path(work); out = w / PROXY; key = str(w)
    with _proxy_lock:
        st = _proxy_jobs.setdefault(key, dict(running=False, progress=0.0, lock=threading.Lock()))
    with st["lock"]:
        if out.exists():
            return out
        st.update(running=True, progress=0.0, error=None)
        tmp = w / (PROXY + ".part.mp4")
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-map", "0:v:0", "-map", "0:a:0?",
               "-vf", "scale=-2:'min(540,ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
               "-pix_fmt", "yuv420p", "-force_key_frames", "expr:gte(t,n_forced*0.5)", "-c:a", "aac", "-b:a", "96k",
               "-ac", "2", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(tmp)]
        if log:
            log("$ " + " ".join(cmd))
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for line in p.stdout:
                if line.startswith("out_time_us=") and duration_s:
                    try:
                        st["progress"] = min(0.999, int(line.split("=")[1]) / 1e6 / duration_s)
                    except ValueError:
                        continue
                    if on_progress:
                        on_progress(st["progress"])
            err = p.stderr.read(); rc = p.wait()
            if rc != 0 or not tmp.exists():
                raise RuntimeError(f"ffmpeg exit {rc}: {err.strip()[-300:]}")
            tmp.replace(out); st["progress"] = 1.0
            return out
        except Exception as ex:
            st["error"] = str(ex); tmp.unlink(missing_ok=True); raise
        finally:
            st["running"] = False


def make_proxy_bg(work, video, duration_s=None):
    """Start the proxy in a background thread (episodes processed before the proxy step existed)."""
    if proxy_path(work) or (_proxy_jobs.get(str(Path(work))) or {}).get("running"):
        return
    def go():
        try:
            make_proxy(work, video, duration_s)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


_wave_lock = threading.Lock()


def waveform(work, audio, per_s=50):
    """Peaks of the dub audio, 0-100, `per_s` values per second (cached in the work folder)."""
    cache = Path(work) / f"waveform_{per_s}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    with _wave_lock:
        if cache.exists():
            return json.loads(cache.read_text())
        rate = 4000
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", audio, "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                             capture_output=True).stdout
        a = array("h"); a.frombytes(raw[: len(raw) // 2 * 2])
        step = rate // per_s; peaks = []
        for k in range(0, len(a), step):
            chunk = a[k:k + step]
            peaks.append(max((abs(x) for x in chunk), default=0))
        top = max(sorted(peaks)[int(len(peaks) * 0.995)] if peaks else 1, 1)
        res = dict(per_s=per_s, peaks=[min(100, round(100 * p / top)) for p in peaks])
        cache.write_text(json.dumps(res))
        return res


_thumb_sem = threading.Semaphore(4)


def thumb(work, video, ms, height=120):
    """One small frame of the source video at `ms` (rounded to 0.1 s), cached as a jpg."""
    ms = int(round(ms / 100.0) * 100)
    d = Path(work) / "thumbs"; d.mkdir(exist_ok=True)
    p = d / f"{ms:08d}_{height}.jpg"
    if not p.exists():
        with _thumb_sem:
            if not p.exists():
                tmp = p.with_suffix(".part.jpg")
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{ms / 1000:.3f}", "-i", video, "-frames:v", "1",
                                "-vf", f"scale=-2:{height}", "-q:v", "5", str(tmp)], capture_output=True)
                if tmp.exists():
                    tmp.replace(p)
    return p if p.exists() else None
