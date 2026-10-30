#!/usr/bin/env python3
"""lipgemini.py -- Step 4B first pass: Gemini watches the source video and decides which sentences are spoken on screen;
our own lip scan (lipgpu/lipmesh/lipmerge) is used only where Gemini is not sure, and for everything when Gemini is not
available (no key, gateway down, VPN off).

  python3 lipgemini.py ask   --work W --video SRC.mp4 [--env FILE] [--runs 3] [--piece 30] [--workers 25]
  #  -> W/gemini_labels.json  and  W/lips_local_ranges.json (the 15-s pieces our scan must still do; [] = none)
  #     run our scan on just those pieces (lipgpu.py / lipmesh.py --ranges W/lips_local_ranges.json, lipmerge.py
  #     --out W/lips_local), then:
  python3 lipgemini.py merge --work W [--local W/lips_local]
  #  -> W/lips_range/range_gemini.json (what `dubstitch.py lipsync` reads) + W/lip_decisions.json

How a sentence is decided (tested on TOLR ep24):
  * the video is cut into --piece-second pieces (480 px, with sound) and every piece is sent --runs times at once;
    each answer lists the time ranges where a person ON SCREEN is talking;
  * a run says "speaking" for a sentence when its ranges cover >= --min-lip s (0.4) of the sentence's Whisper time;
  * ALL runs agree  -> Gemini decides;  runs split, or a run failed -> our lip scan decides that sentence.
Exact clip times always come from Whisper (Step 1A), never from Gemini.

The key: give it in the environment or in an env file (never in the skill, the sheets, the notes or the output folder):
  AI_BASE=https://<OpenAI-compatible gateway>   AI_KEY=sk-...   AI_EMAIL=<email the gateway wants in metadata.user_email>
  AI_MODEL=gemini-3.5-flash  (default)
"""
import argparse, base64, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request, urllib.error
import concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = ("Watch this video clip (with sound). List every time range (seconds from the start of THIS clip, with one decimal) in which a "
          "person visible on screen is speaking - their lips/mouth move as they talk. Ignore narration while nobody on screen talks. "
          "Answer only JSON: {\"speaking\": [[start, end], ...]}.")
LOCAL_PIECE = 15.0          # our scan's standard piece grid (Step 4B.2): 0-15, 15-30, ...


def load_env(path):
    env = {}
    for p in [path, os.environ.get("LIPGEMINI_ENV")]:
        if p and os.path.exists(os.path.expanduser(p)):
            for line in open(os.path.expanduser(p), encoding="utf-8"):
                m = re.match(r"\s*(?:export\s+)?([A-Z_]+)\s*=\s*['\"]?(.*?)['\"]?\s*$", line)
                if m: env.setdefault(m.group(1), m.group(2))
            break
    for k in ("AI_BASE", "AI_KEY", "AI_EMAIL", "AI_MODEL"):
        if os.environ.get(k): env[k] = os.environ[k]
    env.setdefault("AI_MODEL", "gemini-3.5-flash")
    return env


def http(env, path, body=None, timeout=180):
    req = urllib.request.Request(env["AI_BASE"].rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + env["AI_KEY"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def spend(env):
    try:
        d = http(env, "/key/info", timeout=20); return float((d.get("info") or d).get("spend"))
    except Exception:
        return None


def ask_piece(env, f, tries=4):
    """One request; returns (ranges in seconds of the piece, usage) or (None, error text)."""
    b = base64.b64encode(open(f, "rb").read()).decode()
    body = dict(model=env["AI_MODEL"], max_tokens=4000, metadata={"user_email": env.get("AI_EMAIL", "")},
                messages=[{"role": "user", "content": [{"type": "text", "text": PROMPT},
                          {"type": "file", "file": {"file_data": "data:video/mp4;base64," + b, "format": "video/mp4"}}]}])
    err = ""
    for k in range(tries):
        try:
            d = http(env, "/v1/chat/completions", body)
            txt = d["choices"][0]["message"].get("content") or ""
            m = re.search(r"\{.*\}", txt, re.S)
            if not m: err = "no JSON in the answer"; continue
            rng = []
            for x in json.loads(m.group(0)).get("speaking", []):
                try: rng.append((float(x[0]), float(x[1])))
                except Exception: pass
            return rng, d.get("usage", {})
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code} {e.read().decode(errors='replace')[:200]}"
            if e.code not in (408, 429, 500, 502, 503, 504): break
        except Exception as e:
            err = str(e)[:200]
        time.sleep(5 * (k + 1))
    return None, err


def video_length(v):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", v],
                                capture_output=True, text=True).stdout)


def sentences(work):
    P = json.load(open(os.path.join(work, "prep.json"), encoding="utf-8"))["src"]
    return [dict(idx=j, start=c["start_ms"] / 1000, end=c["end_ms"] / 1000, text=c["text"]) for j, c in enumerate(P)]


def cmd_ask(a):
    t0 = time.time(); S = sentences(a.work); L = video_length(a.video)
    if a.limit: L = min(L, a.limit)
    env = load_env(a.env); runs = [[] for _ in range(a.runs)]; failed = [set() for _ in range(a.runs)]
    starts = []; s = 0.0
    while s < L - 0.05: starts.append(s); s += a.piece
    usage = dict(prompt_tokens=0, completion_tokens=0); errors = []; s0 = None; why = ""
    if not (env.get("AI_BASE") and env.get("AI_KEY")):
        why = "no AI_BASE / AI_KEY given"
    else:
        try:
            http(env, "/v1/models", timeout=20)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403): why = f"gateway refused the key (HTTP {e.code})"
        except Exception as e:
            why = f"gateway not reachable ({str(e)[:80]}) -- VPN on?"
    if why:
        print(f"FALLBACK: {why} -> our lip scan decides every sentence")
        failed = [set(starts) for _ in range(a.runs)]
    else:
        s0 = spend(env); tmp = tempfile.mkdtemp(prefix="lipgemini_")
        def cut(s):
            f = os.path.join(tmp, f"p_{s:07.1f}.mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-threads", "2", "-ss", f"{s}", "-t", f"{min(a.piece, L - s)}", "-i", a.video,
                            "-vf", "scale=480:480", "-c:v", "libx264", "-crf", "30", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "48k", f],
                           check=True)
            return s, f
        pool = cf.ThreadPoolExecutor(a.workers); futs = {}
        with cf.ThreadPoolExecutor(8) as ex:                        # ask as soon as each piece is cut
            for s, f in ex.map(cut, starts):
                for r in range(a.runs): futs[pool.submit(ask_piece, env, f)] = (s, r)
        t_cut = time.time() - t0
        for fu in cf.as_completed(futs):
            s, r = futs[fu]; rng, u = fu.result()
            if rng is None: failed[r].add(s); errors.append(f"piece {s:.0f}s run {r + 1}: {u}"); continue
            runs[r] += [(s + x, s + y) for x, y in rng]
            for k in usage: usage[k] += (u or {}).get(k, 0)
        pool.shutdown(); shutil.rmtree(tmp, ignore_errors=True)
        print(f"{len(starts)} pieces x {a.runs} runs = {len(futs)} requests; cutting {t_cut:.0f}s, all answers {time.time() - t0:.0f}s; "
              f"failed {len(errors)}; tokens in {usage['prompt_tokens']} out {usage['completion_tokens']}")
        for e in errors[:5]: print("  " + e)
    lab = []; S = [x for x in S if x["start"] < L]
    for x in S:
        a_, b_ = x["start"], x["end"]
        pieces = {k * a.piece for k in range(int(a_ // a.piece), int(b_ // a.piece) + 1)}
        votes = []
        for r in range(a.runs):
            if pieces & failed[r]: votes.append(None); continue
            votes.append(sum(max(0.0, min(b_, q) - max(a_, p)) for p, q in runs[r]) >= a.min_lip)
        sure = None not in votes and len(set(votes)) == 1
        lab.append(dict(idx=x["idx"], start=a_, end=b_, votes=votes, decided_by="gemini" if sure else "local",
                        speaking=votes[0] if sure else None, text=x["text"]))
    need = sorted({k * LOCAL_PIECE for l in lab if l["decided_by"] == "local"
                   for k in range(int(l["start"] // LOCAL_PIECE), int(l["end"] // LOCAL_PIECE) + 1)})
    local = [[s, s + LOCAL_PIECE] for s in need]                      # the same pieces as a full lipgpu.py run (s, s+15)
    cost = None
    if s0 is not None:
        time.sleep(15); s1 = spend(env); cost = None if s1 is None else round(s1 - s0, 4)
    json.dump(dict(model=env.get("AI_MODEL"), runs=a.runs, piece=a.piece, min_lip=a.min_lip, fallback=why or None,
                   seconds=round(time.time() - t0, 1), cost_usd=cost, usage=usage, errors=errors, labels=lab, ranges=runs),
              open(os.path.join(a.work, "gemini_labels.json"), "w"), ensure_ascii=False, indent=0)
    json.dump(local, open(os.path.join(a.work, "lips_local_ranges.json"), "w"))
    g = [l for l in lab if l["decided_by"] == "gemini"]
    print(f"Gemini decided {len(g)}/{len(lab)} sentences ({sum(l['speaking'] for l in g)} speaking); "
          f"{len(lab) - len(g)} go to our lip scan -> {len(local)} pieces of {LOCAL_PIECE:.0f}s ({len(local) * LOCAL_PIECE:.0f}s of video) "
          f"in {os.path.join(a.work, 'lips_local_ranges.json')}")
    print(f"time {time.time() - t0:.0f}s" + (f", cost ${cost:.3f}" if cost is not None else ""))


def cmd_merge(a):
    import numpy as np
    sys.path.insert(0, HERE); import dubstitch as D
    G = json.load(open(os.path.join(a.work, "gemini_labels.json"), encoding="utf-8")); lab = G["labels"]
    opt = argparse.Namespace(use="all", mouth_thr=0.007, merge=0.3, min_len=0.4, min_lip=G.get("min_lip", 0.4))
    T = max(l["end"] for l in lab) + 2; n = int(T * 25) + 2
    sc, vi, mo = np.full(n, -9.0), np.full(n, -9.0), np.zeros(n)
    need = [l for l in lab if l["decided_by"] == "local"]
    if need:
        ld = a.local or os.path.join(a.work, "lips_local")
        files = sorted(f for f in os.listdir(ld) if f.startswith("range_") and f.endswith(".json")) if os.path.isdir(ld) else []
        if not files: raise SystemExit(f"{len(need)} sentences need our lip scan, but {ld} has no range_*.json -- run lipgpu/lipmesh/lipmerge first")
        Ls, Lv, Lm, cov = np.full(n, -9.0), np.full(n, -9.0), np.zeros(n), np.zeros(n, bool)
        for f in files:
            d = json.load(open(os.path.join(ld, f))); k0 = int(round(d["window"][0] * 25))
            for arr, key in ((Ls, "scores"), (Lv, "visual"), (Lm, "mouth")):
                v = np.array((d.get(key) or [])[:max(n - k0, 0)], float); arr[k0:k0 + len(v)] = np.maximum(arr[k0:k0 + len(v)], v)
            cov[k0:k0 + len(d["scores"][:max(n - k0, 0)])] = True
        local_iv = D.lip_intervals(dict(window=[0, n / 25], start_s=0, end_s=n / 25, scores=list(Ls), visual=list(Lv), mouth=list(Lm)), opt)
    for l in lab:
        k0, k1 = int(l["start"] * 25), max(int(l["end"] * 25), int(l["start"] * 25) + 1)
        if l["decided_by"] == "gemini":
            if l["speaking"]: sc[k0:k1 + 1] = 5.0
            continue
        if not cov[k0:k1].all(): raise SystemExit(f"sentence #{l['idx']} ({l['start']:.1f}-{l['end']:.1f}s) is not covered by {a.local or 'W/lips_local'}")
        sec = sum(max(0.0, min(y, l["end"]) - max(x, l["start"])) for x, y in local_iv)
        l["local_lip_s"] = round(sec, 2); l["speaking"] = sec >= opt.min_lip
        sc[k0:k1], vi[k0:k1], mo[k0:k1] = Ls[k0:k1], Lv[k0:k1], Lm[k0:k1]       # real lip times inside the sentence (lip trims)
        if not l["speaking"]: sc[k0:k1] = np.minimum(sc[k0:k1], -9); vi[k0:k1] = np.minimum(vi[k0:k1], -9); mo[k0:k1] = 0
    # self-check: lipsync must see exactly these decisions
    iv = D.lip_intervals(dict(window=[0, n / 25], start_s=0, end_s=n / 25, scores=list(sc), visual=list(vi), mouth=list(mo)), opt)
    bad = [l["idx"] for l in lab if (sum(max(0.0, min(y, l["end"]) - max(x, l["start"])) for x, y in iv) >= opt.min_lip) != bool(l["speaking"])]
    if bad: raise SystemExit(f"internal check failed for sentences {bad}")
    rd = os.path.join(a.work, "lips_range"); os.makedirs(rd, exist_ok=True)
    old = [f for f in os.listdir(rd) if f.startswith("range_") and f != "range_gemini.json"]
    if old:
        bk = os.path.join(a.work, "lips_range_full_scan"); os.makedirs(bk, exist_ok=True)
        for f in old: shutil.move(os.path.join(rd, f), os.path.join(bk, f))
        print(f"moved {len(old)} older range files to {bk} (lipsync must read only range_gemini.json)")
    json.dump(dict(idx=-1, cls="range", window=[0, n / 25], start_s=0, end_s=n / 25, frames=n, scores=[round(float(v), 3) for v in sc],
                   visual=[round(float(v), 3) for v in vi], mouth=[round(float(v), 4) for v in mo], aperture=[0.0] * n, tracks=[],
                   method=dict(first="Gemini %s, %d runs, all agree" % (G.get("model"), G.get("runs", 3)), else_="our lip scan")),
              open(os.path.join(rd, "range_gemini.json"), "w"))
    json.dump(lab, open(os.path.join(a.work, "lip_decisions.json"), "w"), ensure_ascii=False, indent=0)
    g = [l for l in lab if l["decided_by"] == "gemini"]; o = [l for l in lab if l["decided_by"] == "local"]
    print(f"lip decisions: {len(lab)} sentences, {sum(l['speaking'] for l in lab)} speaking on screen | Gemini (all runs agree) "
          f"{len(g)} ({sum(l['speaking'] for l in g)} speaking) | our lip scan {len(o)} ({sum(l['speaking'] for l in o)} speaking)")
    if o: print("  decided by our scan: " + ", ".join(f"#{l['idx']}{'=speaking' if l['speaking'] else ''}" for l in o))
    print(f"-> {os.path.join(rd, 'range_gemini.json')}; next: dubstitch.py lipsync --work {a.work}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter); sp = p.add_subparsers(dest="cmd", required=True)
    x = sp.add_parser("ask"); x.add_argument("--work", required=True); x.add_argument("--video", required=True)
    x.add_argument("--env", help="file with AI_BASE / AI_KEY / AI_EMAIL (default: environment, or $LIPGEMINI_ENV)")
    x.add_argument("--runs", type=int, default=3); x.add_argument("--piece", type=float, default=30.0)
    x.add_argument("--workers", type=int, default=25, help="requests at the same time (25 was faster than 75)")
    x.add_argument("--min-lip", type=float, default=0.4); x.add_argument("--limit", type=float, default=0, help="tests: only the first N seconds")
    x = sp.add_parser("merge"); x.add_argument("--work", required=True); x.add_argument("--local", help="our scan's range files (default W/lips_local)")
    a = p.parse_args(); {"ask": cmd_ask, "merge": cmd_merge}[a.cmd](a)


if __name__ == "__main__":
    main()
