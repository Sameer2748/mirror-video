"""Rebuild source sentences from word-level times with Claude Sonnet (EP69-71 local flow).

Usage: claude_sentences.py --work W [--out-json PATH] [--out-srt PATH]
Reads W/sentences_src.json (from `dubstitch words --side src`) and overwrites it plus the SRT.

Claude makes every sentence. No time cap. Punctuation first (. ? !), then unpunctuated
paragraphs are split where the sentences actually end.
"""
import argparse, json, re, time
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

# Titles that end with a dot are not sentence ends.
_ABBREV = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof|Sr|Jr|vs|etc|Inc|Ltd|St)\s*\.\s*$", re.I)
_END_PUNCT = re.compile(r'[.!?…]["»«“”’›‹\']*\s*$')


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


def needs_paragraph_split(tx, n_words):
    """True when this card may still be several sentences (Claude decides how to cut)."""
    t = (tx or "").strip()
    if n_words < 16:
        return False
    marks = [m.start() for m in re.finditer(r'[.!?…]', t)]
    real = []
    for i in marks:
        if _ABBREV.search(t[:i + 1]):
            continue
        real.append(i)
    if len(real) >= 2:
        return True
    if not real:
        return True
    return False


RULES = """You cut source-language word-level ASR into SENTENCES for a dubbing pipeline.

Every sentence you emit is used as one source card. Do not leave two real sentences in one card,
and do not cut a sentence in the middle.

How to split (in this order):

1) Punctuation first. End a sentence at . ? ! … (quotes after the mark stay on that sentence).
   Do NOT split titles/names: "Mrs." "Mr." "Ms." "Dr." plus the name that follows.

2) Then unpunctuated paragraphs. If a stretch has no . ? ! but is clearly several sentences
   (new speaker, a question then an answer, a new action), split where those sentences end.
   Use the words and the pauses between them. No clock. No "aim N seconds".

3) One spoken turn = one sentence. Nothing extra from the next turn, nothing missing from this one.

Keep word order. Every word index used exactly once, in order, no gaps.
"""


def claude_spans(words, start, end, extra, cost):
    lines = [f"{i}\t{words[i][0]:.2f}-{words[i][1]:.2f}\t{words[i][2]}" for i in range(start, end)]
    prompt = (
        RULES
        + extra
        + f"\nWord indexes {start} through {end - 1} inclusive. Cover EVERY index in that range.\n\n"
        + "\n".join(lines)
        + '\n\nAnswer ONLY JSON:\n{"sentences":[{"w0":%d,"w1":%d,"text":"exact words joined with spaces"}]}\n'
        "w0 inclusive, w1 exclusive." % (start, end)
    )
    t0 = time.time()
    text, usage = ai.chat(prompt, model=MODEL, max_tokens=8000, reasoning={"effort": "medium"})
    c = float((usage or {}).get("cost") or 0)
    pin, pout = int((usage or {}).get("prompt_tokens") or 0), int((usage or {}).get("completion_tokens") or 0)
    cost[0] += c
    cost[1] += pin
    cost[2] += pout
    cost[3] += 1
    print(f"claude {start}-{end} {time.time()-t0:.1f}s in {pin} out {pout} cost ${c:.4f}", flush=True)
    try:
        part = ai.extract_json(text, "sentences")["sentences"]
    except Exception:
        part = []
    out = []
    for s in part or []:
        a, b = int(s["w0"]), int(s["w1"])
        if b <= a or a < start or b > end:
            continue
        out.append((a, b, s.get("text") or " ".join(w[2] for w in words[a:b])))
    out.sort()
    return out


def fill_cover(spans, start, end, words):
    filled, cursor = [], start
    for a, b, tx in spans:
        if a > cursor:
            filled.append((cursor, a, " ".join(w[2] for w in words[cursor:a])))
        if a < cursor:
            a = cursor
            if b <= a:
                continue
            tx = " ".join(w[2] for w in words[a:b])
        filled.append((a, b, tx))
        cursor = b
    if cursor < end:
        filled.append((cursor, end, " ".join(w[2] for w in words[cursor:end])))
    return filled


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
    CHUNK, cost = 380, [0.0, 0, 0, 0]  # dollars, prompt tokens, completion tokens, calls
    got = []
    for start in range(0, len(words), CHUNK):
        end = min(len(words), start + CHUNK)
        got.extend(claude_spans(words, start, end, "\nThis is one chunk of the episode.\n", cost))
    filled = fill_cover(got, 0, len(words), words)

    out_spans = []
    for a, b, tx in filled:
        if not needs_paragraph_split(tx, b - a):
            out_spans.append((a, b, tx))
            continue
        print(f"unpunctuated paragraph words {a}-{b} ({b - a} tokens) → Claude", flush=True)
        parts = claude_spans(
            words, a, b,
            "\nThis card has no usable . ? ! (or several sentences stuck together). "
            "Split it into the real sentences inside it. Do not add or drop words.\n",
            cost,
        )
        if not parts:
            out_spans.append((a, b, tx))
            continue
        out_spans.extend(fill_cover(parts, a, b, words))

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
    doc = dict(old, srt=str(OUT_SRT.resolve()), sentences=rows, how="claude_sonnet_punct_then_paragraph")
    json.dump(doc, open(OUT_JSON, "w"), indent=1, ensure_ascii=False)
    with open(OUT_SRT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(f"{r['idx']+1}\n{srt_t(r['start_ms'])} --> {srt_t(r['end_ms'])}\n{r['text']}\n\n")
    durs = [(r["end_ms"] - r["start_ms"]) / 1000 for r in rows] or [0]
    print(f"wrote {len(rows)} sentences (was {len(old.get('sentences') or [])}) "
          f"median {sorted(durs)[len(durs)//2]:.1f}s max {max(durs):.1f}s "
          f"claude_cost ${cost[0]:.4f} in {cost[1]} out {cost[2]} calls {cost[3]}")
    usage_doc = dict(calls=cost[3], prompt_tokens=cost[1], completion_tokens=cost[2], cost=cost[0])
    json.dump(usage_doc, open(W / "sentences_src_usage.json", "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
