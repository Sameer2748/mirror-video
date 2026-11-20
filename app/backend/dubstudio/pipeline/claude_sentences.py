"""Rebuild source sentences from word-level times with Claude Sonnet (EP69-71 local flow).

Usage: claude_sentences.py --work W [--out-srt PATH]
Reads W/sentences_src.json (from `dubstitch words --side src`) and overwrites it plus the SRT.
"""
import argparse, json, time
from pathlib import Path
import sys

def _app_dir(script):
    script = Path(script).resolve()
    for folder in (script.parents[2], Path("/opt/app")):
        pkg = folder / "dubstudio"
        if (pkg / "ai.py").is_file() or (pkg / "__init__.py").is_file():
            return folder
    return script.parents[2]

sys.path.insert(0, str(_app_dir(__file__)))
from dubstudio import ai  # noqa: E402

MODEL = "claude-sonnet-5-5"


def srt_t(ms):
    ms = int(ms)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def flat_words(doc):
    raw = doc.get("words") or []
    out = []
    for w in raw:
        if isinstance(w, (list, tuple)) and len(w) >= 3:
            out.append([float(w[0]), float(w[1]), str(w[2])])
        elif isinstance(w, dict):
            t0 = w.get("start") if w.get("start") is not None else w.get("t0")
            t1 = w.get("end") if w.get("end") is not None else w.get("t1")
            tx = w.get("word") or w.get("text") or ""
            if t0 is None or t1 is None:
                continue
            out.append([float(t0), float(t1), str(tx).strip()])
    out.sort(key=lambda x: (x[0], x[1]))
    return out


RULES = """You cut source-language word-level ASR into SENTENCES for a dubbing pipeline.

Why: each source sentence is matched to one dub line. The picture of that source time is what
the viewer sees. If one source "sentence" is 20 seconds of several questions and answers,
six dub lines share one mouth and lip-sync is wrong.

- Split on a real question, a new speaker, a new action, a new beat.
- Do NOT split titles/names: "Mrs." + "Horton", "Ms." + "Olsen", "Mr." + "Allen".
- Do NOT make tiny cards under ~1.2 s or under 4 words unless it is a short reply.
- Aim 2–8 seconds. Hard cap about 10 s unless it is truly one spoken utterance.
- Keep word order. Every word index used exactly once, in order, no gaps.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out-json")
    ap.add_argument("--out-srt")
    args = ap.parse_args()
    W = Path(args.work)
    OUT_JSON = Path(args.out_json) if args.out_json else W / "sentences_src.json"
    orig = ai._req
    ai._req = lambda url, body=None, timeout=120: orig(url, body=body, timeout=max(timeout, 600))
    old = json.loads(OUT_JSON.read_text()) if OUT_JSON.is_file() else {"sentences": []}
    words = flat_words(old)
    if len(words) < 8:
        print(f"sentences_src: {len(words)} words, skip Claude rebuild")
        return 0
    OUT_SRT = Path(args.out_srt) if args.out_srt else Path(old.get("srt") or (W / "src_sentences.srt"))
    CHUNK, got, cost = 380, [], 0.0
    for start in range(0, len(words), CHUNK):
        end = min(len(words), start + CHUNK)
        lines = [f"{i}\t{words[i][0]:.2f}-{words[i][1]:.2f}\t{words[i][2]}" for i in range(start, end)]
        prompt = (
            RULES
            + f"\nThis chunk is word indexes {start} through {end - 1} inclusive ({end - start} tokens). "
            f"Cover EVERY index in that range.\n\n"
            + "\n".join(lines)
            + '\n\nAnswer ONLY JSON:\n{"sentences":[{"w0":%d,"w1":%d,"text":"exact words joined with spaces"}]}\n'
            "w0 inclusive, w1 exclusive." % (start, end)
        )
        t0 = time.time()
        text, usage = ai.chat(prompt, model=MODEL, max_tokens=8000, reasoning={"effort": "medium"})
        c = float((usage or {}).get("cost") or 0)
        cost += c
        print(f"chunk {start}-{end} {time.time()-t0:.1f}s cost ${c:.4f}", flush=True)
        part = ai.extract_json(text, "sentences")["sentences"]
        got.extend(part)

    sents_raw = []
    for s in got:
        a, b = int(s["w0"]), int(s["w1"])
        if b <= a or a < 0 or b > len(words):
            continue
        sents_raw.append((a, b, s.get("text") or " ".join(w[2] for w in words[a:b])))
    sents_raw.sort()
    filled, cursor = [], 0
    for a, b, tx in sents_raw:
        if a > cursor:
            filled.append((cursor, a, " ".join(w[2] for w in words[cursor:a])))
        if a < cursor:
            a = cursor
            if b <= a:
                continue
            tx = " ".join(w[2] for w in words[a:b])
        filled.append((a, b, tx))
        cursor = b
    if cursor < len(words):
        filled.append((cursor, len(words), " ".join(w[2] for w in words[cursor:])))

    out_spans = []
    for a, b, tx in filled:
        dur = words[b - 1][1] - words[a][0]
        if dur <= 10.5 or b - a < 12:
            out_spans.append((a, b, tx))
            continue
        lines = [f"{i}\t{words[i][0]:.2f}-{words[i][1]:.2f}\t{words[i][2]}" for i in range(a, b)]
        prompt = (RULES + f"\nThis one card is TOO LONG ({dur:.1f}s), words {a}-{b-1}. Split it.\n\n"
                  + "\n".join(lines)
                  + '\n\nAnswer ONLY JSON: {"sentences":[{"w0":..,"w1":..,"text":"..."}]}')
        text, usage = ai.chat(prompt, model=MODEL, max_tokens=4000, reasoning={"effort": "medium"})
        cost += float((usage or {}).get("cost") or 0)
        try:
            part = ai.extract_json(text, "sentences")["sentences"]
        except Exception:
            part = []
        parts = []
        for s in part:
            x, y = int(s["w0"]), int(s["w1"])
            if y <= x or x < a or y > b:
                continue
            parts.append((x, y, s.get("text") or " ".join(w[2] for w in words[x:y])))
        parts.sort()
        if not parts:
            out_spans.append((a, b, tx))
            continue
        cur = a
        for x, y, t2 in parts:
            if x > cur:
                out_spans.append((cur, x, " ".join(w[2] for w in words[cur:x])))
            out_spans.append((x, y, t2))
            cur = y
        if cur < b:
            out_spans.append((cur, b, " ".join(w[2] for w in words[cur:b])))

    rows = []
    for i, (a, b, tx) in enumerate(out_spans):
        chunk = words[a:b]
        if not chunk:
            continue
        rows.append(dict(
            idx=i, text=tx.strip(),
            start_ms=round(chunk[0][0] * 1000), end_ms=round(chunk[-1][1] * 1000),
            srt_start_ms=round(chunk[0][0] * 1000), srt_end_ms=round(chunk[-1][1] * 1000),
            words=len(chunk), same=len(chunk), differently=0, not_heard=0,
            quote_share=0.0,
            text_type="speech" if any(x in tx for x in ('"', "“", "?", "!")) else "narration",
        ))
    for x, y in zip(rows, rows[1:]):
        if y["start_ms"] < x["end_ms"]:
            mid = (x["end_ms"] + y["start_ms"]) // 2
            x["end_ms"] = y["start_ms"] = mid
    bak = OUT_JSON.with_name(OUT_JSON.name.replace(".json", ".before_claude.json"))
    if OUT_JSON.is_file() and not bak.is_file():
        bak.write_bytes(OUT_JSON.read_bytes())
    OUT_SRT.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(old, srt=str(OUT_SRT.resolve()), sentences=rows, how="claude_sonnet_medium_from_words")
    json.dump(doc, open(OUT_JSON, "w"), indent=1, ensure_ascii=False)
    with open(OUT_SRT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(f"{r['idx']+1}\n{srt_t(r['start_ms'])} --> {srt_t(r['end_ms'])}\n{r['text']}\n\n")
    durs = [(r["end_ms"] - r["start_ms"]) / 1000 for r in rows] or [0]
    print(f"wrote {len(rows)} sentences (was {len(old.get('sentences') or [])}) "
          f"median {sorted(durs)[len(durs)//2]:.1f}s max {max(durs):.1f}s claude_cost ${cost:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
