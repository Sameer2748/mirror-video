"""Fast-clip rule (owner, 2026-10-22): no clip may play faster than 1.25x. Every piece of the edit list
(manifest_groups.json) faster than THRESHOLD is cut down to about TARGET (1.2x) instead of being sped up.

  narration (no talking face): cut evenly from its two ends (blocks were already trimmed by `groups --trim-fast`,
            so what is left here are pieces inside lip groups and leftovers)
  lip-sync  (a talking sentence): Claude decides from data what to cut -- the clip's shots (camera cuts), the seconds of
            moving lips in each shot, one frame per shot, the German and Italian lines -- preferring reaction shots,
            other characters and the time before/after the lips move; the lips themselves only when the whole clip
            is lip movement. A rule decides when Claude is not available or its answer does not add up.
The result is checked: nothing stays above THRESHOLD. Writes manifest_groups.json (the old one is kept as
manifest_groups.before_fastfix.json) and fastfix.json (every fast clip: speed before/after, what was cut, why).

CLI: python fastfix.py <work> [--threshold 1.25] [--target 1.2] [--no-ai]
"""
import argparse, base64, json, os, re, subprocess, sys, tempfile, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))            # app/backend, for dubstudio.ai
THRESHOLD, TARGET = 1.25, 1.2
MIN_CUT, MIN_PIECE = 300, 400                         # ms: no cut shorter (skill 4A.1: 0.3 s), no kept piece shorter

PROMPT = """You trim one video clip of a dubbing edit. The clip shows a source (German) sentence while an Italian voice
speaks; it is too long for the Italian line, so it would play {speed:.2f}x fast. Cut exactly about {need:.2f} s of
source footage (between {lo:.2f} and {hi:.2f} s in total) so the rest plays at about {target}x.

What may be cut, best first:
1. shots where nobody in frame is talking: reaction shots, another character listening, an establishing or object shot;
2. the part of a talking shot before the lips start moving or after they stop;
3. pauses between two stretches of lip movement;
4. only if the clip is lip movement from start to end: lip movement itself, evenly from its start and its end.
Never cut the middle of the speaking character's lip movement when 1-3 can provide the time. The kept footage stays in
order. Each cut at least {min_cut:.2f} s; each kept piece at least {min_piece:.2f} s.

Italian line (the voice): {it_text}
German sentence (the clip): {de_text}
Clip: source {s0:.2f}-{s1:.2f} s ({dur:.2f} s), played in {slot:.2f} s of Italian.
Shots inside the clip (camera cuts), with the seconds of moving lips the lip scan found in each:
{shots}
Lip movement stretches (source s): {lips}
One frame per shot follows, in shot order.

Answer ONLY with JSON: {{"cuts": [[start_s, end_s], ...], "reason": "short: what is cut and why"}} in source seconds."""


def _load(p, d=None):
    try:
        return json.load(open(p))
    except Exception:
        return d


def scenes(work, video):
    """Camera cuts of the source video (s), cached in W/scenes.json."""
    cp = Path(work) / "scenes.json"; c = _load(cp)
    if c and c.get("video") == video and c.get("size") == os.path.getsize(video):
        return c["cuts"]
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", video, "-an", "-vf",
                          "fps=12.5,scale=192:-2,select='gt(scene,0.28)',showinfo", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    cuts = [round(float(x), 2) for x in re.findall(r"pts_time:([0-9.]+)", out)]
    json.dump({"video": video, "size": os.path.getsize(video), "cuts": cuts}, open(cp, "w"))
    return cuts


def frame_b64(video, t, w=320):
    with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, t):.3f}", "-i", video, "-frames:v", "1", "-vf",
                        f"scale={w}:-2", "-q:v", "5", f.name], check=True)
        return base64.b64encode(open(f.name, "rb").read()).decode()


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def complement(s0, s1, cuts):
    keep, cur = [], s0
    for a, b in sorted(cuts):
        if a > cur: keep.append([cur, a])
        cur = max(cur, b)
    if cur < s1: keep.append([cur, s1])
    return keep


def valid_cuts(s0, s1, cuts, need, lo, hi):
    try:
        cs = sorted([float(a), float(b)] for a, b in cuts)
    except Exception:
        return None
    cs = [[max(s0, a), min(s1, b)] for a, b in cs if b > a]
    for (a, b), (c, d) in zip(cs, cs[1:]):
        if c < b: return None
    if any(b - a < MIN_CUT / 1000 - 1e-3 for a, b in cs): return None
    keep = complement(s0, s1, cs)
    if not keep or any(b - a < MIN_PIECE / 1000 - 1e-3 for a, b in keep): return None
    tot = sum(b - a for a, b in cs)
    if not (lo - 0.05 <= tot <= hi + 0.05): return None
    return cs


def _merge(cuts):
    out = []
    for a, b in sorted(cuts):
        if out and a <= out[-1][1] + 1e-3: out[-1][1] = max(out[-1][1], b)
        else: out.append([a, b])
    return out


def ends_cuts(a, b, left):
    """`left` s from the two ends of a..b with no cut under MIN_CUT (skill 4A.1): evenly from both ends when each half
    is long enough, else one cut at the end; a remainder under MIN_CUT is rounded up to it (the clip ends a little
    slower than the target, never faster)."""
    m = MIN_CUT / 1000
    if left <= 1e-3:
        return []
    if left / 2 >= m - 1e-3:
        return [[a, a + left / 2], [b - left / 2, b]]
    return [[b - max(left, m), b]]


def rule_cuts(s0, s1, need, lips, shots):
    """No AI: non-lip time first (shots without lips, then the clip's ends outside the lips), then the lips evenly
    from both ends of what is kept. Always reaches `need`. -> cuts [[a, b]] in s."""
    cuts = []
    tot = lambda: sum(b - a for a, b in _merge(cuts))
    for a, b in sorted([sh for sh in shots if sum(overlap(sh[0], sh[1], x, y) for x, y in lips) < 0.1],
                       key=lambda x: -(x[1] - x[0])):            # whole shots with no lip movement
        left = need - tot()
        if left < MIN_CUT / 1000: break
        take = min(b - a, left)
        if take < MIN_CUT / 1000: continue                   # a shot too short to cut on its own
        if b >= s1 - 1e-3: cuts.append([b - take, b])
        elif a <= s0 + 1e-3: cuts.append([a, a + take])
        else: m = (a + b) / 2; cuts.append([m - take / 2, m + take / 2])
    inside = [(max(x, s0), min(y, s1)) for x, y in lips if min(y, s1) > max(x, s0)]
    if inside:                                               # before the first / after the last lip movement
        first, last = min(x for x, _ in inside), max(y for _, y in inside)
        for side in ("end", "start"):
            left = need - tot()
            if left < MIN_CUT / 1000: break
            keep = complement(s0, s1, _merge(cuts))
            if side == "end":
                a, b = max(last, keep[-1][0]), keep[-1][1]
                take = min(max(0, b - a), left)
                if take >= MIN_CUT / 1000: cuts.append([b - take, b])
            else:
                a, b = keep[0][0], min(first, keep[0][1])
                take = min(max(0, b - a), left)
                if take >= MIN_CUT / 1000: cuts.append([a, a + take])
    left = need - tot()
    if left > 0.02:                                          # the lips too: from both ends of what is kept
        keep = complement(s0, s1, _merge(cuts))
        cuts += ends_cuts(keep[0][0], keep[-1][1], left)
    return _merge(cuts)


def write_sheet(W, cfg, r, P):
    """Tab "Fast clips" in the groups review workbook: every fast clip, speed before/after, what was cut and why."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    out = cfg.get("groups_review")
    if not out or not os.path.exists(out):
        return
    op = cfg.get("opening_s", 0) or 0
    t = lambda ms: f"{int((ms / 1000 + op) // 60):02d}:{(ms / 1000 + op) % 60:05.2f}"
    rows = []
    for g in r["group_trims"]:
        rows.append(dict(time=t(g["it0"]), rows=", ".join(f"IT#{x}" for x in g["rows"]),
                         line=" / ".join(P["dub"][x]["text"][:40] for x in g["rows"][:3]), kind="narration block (no talking face)",
                         src=f"{g['us0'] / 1000:.2f}-{g['us1'] / 1000:.2f}", before=g["speed_before"], cut=g["cut_s"],
                         what="; ".join(f"IT#{x['row']}: {x['ms'] / 1000:.2f}s from its start and end" for x in g["trims"]),
                         after=g["speed_after"], by="pipeline (groups --trim-fast)", why="narration: extra footage cut instead of speeding up"))
    for c in r["clips"]:
        rows.append(dict(time=t(c["it0"]), rows=f"IT#{c['row']}", line=c["it_text"], kind=c["kind"],
                         src=f"{c['us0'] / 1000:.2f}-{c['us1'] / 1000:.2f}", before=c["speed_before"], cut=c["cut_s"],
                         what="; ".join(f"{a:.2f}-{b:.2f}s" for a, b in c["cuts"]), after=c["speed_after"], by=c["by"], why=c["reason"]))
    rows.sort(key=lambda x: x["time"])
    wb = openpyxl.load_workbook(out)
    if "Fast clips" in wb.sheetnames: del wb["Fast clips"]
    ws = wb.create_sheet("Fast clips", 0); F = lambda **k: Font(name="Arial", **k)
    ws["A1"] = f"Fast-clip rule: no clip plays faster than {r['threshold']}x; every faster clip is trimmed to about {r['target']}x"
    ws["A1"].font = F(bold=True, size=12)
    mx = max([x["after"] for x in rows], default=0)
    ws["A2"] = (f"{len(rows)} fast clips trimmed: {len(r['group_trims'])} narration blocks, "
                f"{sum(1 for x in rows if 'lip' in x['kind'] and 'narration' not in x['kind'])} lip-sync clips "
                f"({sum(1 for x in rows if x['by'] == 'Claude')} decided by Claude from the shots, lip data and frames), the rest "
                f"narration pieces. Fastest clip after trimming: {mx:.2f}x. {sum(x['cut'] for x in rows):.1f} s of source footage cut. "
                f"Video time = time in the final videos.")
    ws["A2"].font = F(size=10); ws["A2"].alignment = Alignment(wrap_text=True); ws.merge_cells("A2:L2"); ws.row_dimensions[2].height = 32
    H = ["#", "Video time", "Dub row(s)", "Dub line", "Type", "Source footage before (s)", "Speed before (x)", "Cut (s)",
         "What was cut (source s)", "Speed after (x)", "Decided by", "Why"]
    for k, h in enumerate(H, 1):
        c = ws.cell(4, k, h); c.font = F(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", start_color="1F4E78")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    red, amb, grn, lip = (PatternFill("solid", start_color=x) for x in ("F8CBAD", "FFE699", "C6EFCE", "E4DFEC"))
    for i, x in enumerate(rows, 5):
        vals = [i - 4, x["time"], x["rows"], x["line"], x["kind"], x["src"], x["before"], x["cut"], x["what"], x["after"], x["by"], x["why"]]
        for k, v in enumerate(vals, 1):
            c = ws.cell(i, k, v); c.font = F(size=10); c.alignment = Alignment(wrap_text=True, vertical="top")
            if k in (7, 8, 10): c.number_format = "0.00"
        ws.cell(i, 7).fill = red if x["before"] >= 1.5 else amb
        ws.cell(i, 10).fill = grn if x["after"] <= r["threshold"] else red
        if "lip" in x["kind"] and "narration" not in x["kind"]: ws.cell(i, 5).fill = lip
    for k, w in enumerate([5, 10, 12, 40, 22, 14, 10, 8, 26, 10, 20, 60], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(k)].width = w
    ws.freeze_panes = "A5"; ws.auto_filter.ref = f"A4:L{4 + len(rows)}"
    wb.save(out)


def main(a):
    W = Path(a.work)
    M = _load(W / "manifest_groups.json"); cfg = _load(W / "config.json", {}) or {}
    if not M or not M.get("segments") or not cfg.get("src_video") or not (W / "prep.json").exists():
        print("fast-clip rule: no edit list yet, nothing to do"); return 0
    video = cfg["src_video"]
    L = _load(W / "lipsync.json", {}) or {}
    lip_rows = {int(k): v for k, v in (L.get("rows") or {}).items()}
    lip_iv = [tuple(x) for x in (L.get("intervals") or [])]
    P = json.load(open(W / "prep.json")); src_sent = P["src"]
    cuts_all = scenes(W, video)
    T, R = a.threshold, a.target
    use_ai = not a.no_ai
    if use_ai:
        try:
            from dubstudio import ai
            use_ai = ai.available()
        except Exception:
            use_ai = False
    report = {"threshold": T, "target": R, "ai": use_ai, "clips": [], "group_trims": []}
    ai_down = []                                      # lip clips Claude could not be asked about (gateway / VPN down)
    # narration blocks trimmed by `groups --trim-fast` (recorded for the sheet)
    for g in M.get("groups", []):
        if g.get("removed_ms", 0) > 1 and not g.get("lip"):
            it = g["it1"] - g["it0"]
            report["group_trims"].append(dict(rows=g["rows"], it0=g["it0"], it1=g["it1"], us0=g["us0"], us1=g["us1"],
                                              speed_before=round((g["us1"] - g["us0"]) / it, 3),
                                              speed_after=round(g["kept"] / it, 3), cut_s=round(g["removed_ms"] / 1000, 2),
                                              trims=g.get("trims", [])))
    segs = M["segments"]; jobs = []
    for k, s in enumerate(segs):
        if s.get("video"): continue
        d = s["it_end_ms"] - s["it_start_ms"]; u = s["us_end_ms"] - s["us_start_ms"]
        if d <= 0 or u / d <= T + 0.005: continue
        i = s["it_idx"]; lp = lip_rows.get(i)
        s0, s1 = s["us_start_ms"] / 1000, s["us_end_ms"] / 1000
        is_lip = bool(lp) and overlap(s0, s1, lp["sentence"][0] / 1000, lp["sentence"][1] / 1000) > 0.3 * (s1 - s0)
        need = (u - R * d) / 1000
        kind = ("lip group piece" if is_lip and (s.get("note") or "").startswith("lip group") else
                "lip-sync line" if is_lip else
                "narration (lip group)" if (s.get("note") or "").startswith("lip group") else "narration")
        jobs.append(dict(k=k, row=i, kind=kind, lip=is_lip, s0=s0, s1=s1, slot=d / 1000, speed=u / d, need=need))
    # Claude decides the lip clips: the gateway must answer (owner's rule: never work around a step silently)
    if use_ai and any(j["lip"] for j in jobs):
        from dubstudio import gen
        try:
            gen.gateway_ok()
        except RuntimeError as ex:
            print(f"fast-clip rule: {sum(j['lip'] for j in jobs)} lip clips need Claude to choose their cuts, but {ex}")
            return 2

    def decide(j):
        s0, s1, need = j["s0"], j["s1"], j["need"]
        lo, hi = max(need - 0.15, (j["s1"] - j["s0"]) - j["slot"] * T), need + 0.25
        if not j["lip"]:
            cs = ends_cuts(s0, s1, need)
            return dict(j, cuts=cs, by="rule: narration, evenly from both ends" if len(cs) == 2 else "rule: narration, from its end",
                        reason="no talking face: trimmed from its start and end" if len(cs) == 2 else
                        f"no talking face: one cut at its end (two halves would be under {MIN_CUT / 1000:.1f} s)")
        lips = [(max(x, s0), min(y, s1)) for x, y in lip_iv if min(y, s1) > max(x, s0)]
        bounds = [s0] + [c for c in cuts_all if s0 + 0.2 < c < s1 - 0.2] + [s1]
        shots = list(zip(bounds, bounds[1:]))
        if use_ai:
            try:
                from dubstudio import ai
                lp = lip_rows.get(j["row"]) or {}
                de = " ".join(src_sent[x]["text"] for x in (lp.get("src_lines") or []) if 0 <= x < len(src_sent))
                it_txt = next((x.get("it_text") for x in segs if x["it_idx"] == j["row"]), "")
                shot_txt = "\n".join(f"  shot {n + 1}: {a:.2f}-{b:.2f} s ({b - a:.2f} s), lips moving {sum(overlap(a, b, x, y) for x, y in lips):.2f} s"
                                     for n, (a, b) in enumerate(shots))
                q = PROMPT.format(speed=j["speed"], need=need, lo=lo, hi=hi, target=R, min_cut=MIN_CUT / 1000,
                                  min_piece=MIN_PIECE / 1000, it_text=it_txt, de_text=de or "(see frames)", s0=s0, s1=s1,
                                  dur=s1 - s0, slot=j["slot"], shots=shot_txt,
                                  lips=", ".join(f"{x:.2f}-{y:.2f}" for x, y in lips) or "none found")
                content = [{"type": "text", "text": q}]
                for n, (a_, b_) in enumerate(shots[:8]):
                    content.append({"type": "text", "text": f"shot {n + 1} ({a_:.2f}-{b_:.2f} s):"})
                    content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + frame_b64(video, (a_ + b_) / 2)}})
                from dubstudio import gen
                text = gen.ask(content, max_tokens=6000, timeout=180, tries=2)
                ans = ai.extract_json(text, "cuts")
                cs = valid_cuts(s0, s1, ans.get("cuts") or [], need, lo, hi)
                if cs:
                    return dict(j, cuts=cs, by="Claude", reason=ans.get("reason") or "", shots=len(shots))
                rc = rule_cuts(s0, s1, need, lips, shots)
                return dict(j, cuts=rc, by="rule (Claude's answer did not add up)", reason=f"Claude: {ans.get('reason', '')}",
                            shots=len(shots))
            except Exception as ex:
                if "VPN" in str(ex) or "no answer from the AI gateway" in str(ex):
                    return dict(j, ai_down=str(ex)[:200])     # not decided: the step fails, a retry asks Claude again
                return dict(j, cuts=rule_cuts(s0, s1, need, lips, shots), by="rule (AI error)", reason=str(ex)[:160],
                            shots=len(shots))
        return dict(j, cuts=rule_cuts(s0, s1, need, lips, shots), by="rule (no AI)",
                    reason="non-lip shots first, then before/after the lips, then the lips from both ends", shots=len(shots))

    # Claude's answers are cached (W/fastfix_cache.json), so a run cut short by --max-seconds resumes (exit 3)
    import time
    cache_p = W / "fastfix_cache.json"; cache = _load(cache_p, {}) or {}
    t_end = time.time() + a.max_seconds; lock = threading.Lock()
    key = lambda j: f"{j['row']}|{j['s0']:.3f}|{j['s1']:.3f}|{j['slot']:.3f}|{R}|{use_ai}"

    def run(j):
        kj = key(j)
        if kj in cache: return dict(j, **cache[kj])
        if time.time() > t_end: return None
        r = decide(j)
        if r.get("ai_down"):
            with lock:
                ai_down.append(r)
            return r
        with lock:
            cache[kj] = {x: r[x] for x in ("cuts", "by", "reason", "shots") if x in r}
            json.dump(cache, open(cache_p, "w"), indent=1, ensure_ascii=False)
        return r
    with ThreadPoolExecutor(4) as ex:
        done = list(ex.map(run, jobs))
    if ai_down:
        print(f"fast-clip rule: Claude could not be reached for {len(ai_down)} lip clips ({ai_down[0]['ai_down']}). "
              "Turn the VPN on, then retry this step: the clips already decided are kept.")
        return 2
    if any(d is None for d in done):
        print(f"{sum(d is None for d in done)} of {len(done)} fast clips still to decide -- run again (it resumes)")
        return 3
    # apply: each fast segment becomes its kept pieces, played one after another in its slot at one speed
    by_k = {j["k"]: j for j in done}; out = []
    for k, s in enumerate(segs):
        j = by_k.get(k)
        if not j:
            out.append(s); continue
        keep = [p for p in complement(j["s0"], j["s1"], j["cuts"]) if p[1] - p[0] > 0.02]
        kept = sum(b - a for a, b in keep); t = s["it_start_ms"]; dur = s["it_end_ms"] - s["it_start_ms"]
        for n, (a_, b_) in enumerate(keep):
            t1 = s["it_end_ms"] if n == len(keep) - 1 else t + dur * (b_ - a_) / kept
            out.append(dict(s, it_start_ms=round(t, 3), it_end_ms=round(t1, 3), us_start_ms=round(a_ * 1000, 3),
                            us_end_ms=round(b_ * 1000, 3), stretch_factor=round((t1 - t) / ((b_ - a_) * 1000), 3),
                            note=((s.get("note") or "") + f"; fast-clip trim ({j['by']})").lstrip("; ")))
            t = t1
        report["clips"].append(dict(row=j["row"], kind=j["kind"], it0=s["it_start_ms"], it1=s["it_end_ms"],
                                    us0=j["s0"] * 1000, us1=j["s1"] * 1000, speed_before=round(j["speed"], 3),
                                    speed_after=round(kept * 1000 / dur, 3), cut_s=round(j["s1"] - j["s0"] - kept, 2),
                                    cuts=[[round(a_, 2), round(b_, 2)] for a_, b_ in j["cuts"]], by=j["by"],
                                    reason=j.get("reason", ""), shots=j.get("shots"), it_text=s.get("it_text", ""),
                                    us_text=s.get("us_text", "")))
    # the hard rule, checked
    bad = [(s["it_idx"], round((s["us_end_ms"] - s["us_start_ms"]) / (s["it_end_ms"] - s["it_start_ms"]), 2)) for s in out
           if not s.get("video") and s["it_end_ms"] > s["it_start_ms"]
           and (s["us_end_ms"] - s["us_start_ms"]) / (s["it_end_ms"] - s["it_start_ms"]) > T + 0.01]
    for x, y in zip(out, out[1:]):
        assert abs(x["it_end_ms"] - y["it_start_ms"]) < 1, "timeline not contiguous"
    if not (W / "manifest_groups.before_fastfix.json").exists():
        json.dump(M, open(W / "manifest_groups.before_fastfix.json", "w"), indent=1, ensure_ascii=False)
    M2 = dict(M, segments=out)
    json.dump(M2, open(W / "manifest_groups.json", "w"), indent=1, ensure_ascii=False)
    report["still_fast"] = bad
    report["cuts_under_min"] = [(c["row"], round(b_ - a_, 3)) for c in report["clips"] for a_, b_ in c.get("cuts", [])
                                if b_ - a_ < MIN_CUT / 1000 - 2e-3]
    json.dump(report, open(W / "fastfix.json", "w"), indent=1, ensure_ascii=False)

    def switches(m):
        S = sorted(m["segments"], key=lambda x: x["it_start_ms"])
        return [y["it_start_ms"] for x, y in zip(S, S[1:]) if abs(y["us_start_ms"] - x["us_end_ms"]) > 50]
    old_c = switches(M)
    new_c = [c for c in switches(M2) if min((abs(c - o) for o in old_c), default=1e9) > 60]
    json.dump({"new_cuts_ms": [round(c) for c in new_c]}, open(W / "fastfix_cuts.json", "w"))   # never a transition here
    try:
        write_sheet(W, cfg, report, P)
    except Exception as ex:
        print(f"fast-clip sheet not written: {ex}")
    n_ai = sum(1 for c in report["clips"] if c["by"] == "Claude")
    print(f"fast-clip rule (>{T}x -> {R}x): {len(report['group_trims'])} narration blocks trimmed by groups, "
          f"{len(report['clips'])} pieces trimmed here ({sum(1 for c in report['clips'] if 'lip' in c['kind'] and 'narration' not in c['kind'])} "
          f"lip, {n_ai} decided by Claude), {sum(c['cut_s'] for c in report['clips']):.1f} s cut; still over {T}x: {bad or 'none'}; "
          f"cuts under {MIN_CUT / 1000:.1f} s: {len(report['cuts_under_min']) or 'none'}")
    return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("work"); ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--target", type=float, default=TARGET); ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=1e9, help="stop asking Claude after this long; exit 3 = run again")
    sys.exit(main(ap.parse_args()))
