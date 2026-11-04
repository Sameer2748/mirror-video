"""Translation by Claude (AI gateway), for when the pipeline's Google translation is blocked or fails.

The pipeline stays in charge of the subtitle file: `translate --export-todo` lists the sentences that have no
translation yet, Claude fills them here, and `translate --engine file --import` builds the English SRT from them
(same sentence split, character names kept, the dub's exact cue times).

Claude does not always return `{"t": […]}` for a batch of 40: it may send a raw JSON array, a numbered list, or
merge two subtitle fragments. Those replies used to fail the whole step (TGS ep32). The parser accepts the common
shapes; a batch whose count still does not match is split and asked again, down to one sentence.
"""
import json, re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .. import ai

LANG = {"it": "Italian", "en": "English", "de": "German", "fr": "French", "es": "Spanish", "pt": "Portuguese"}

PROMPT = """Translate these {sl} subtitle sentences of a dubbed drama episode into natural, spoken {tl} for
subtitles. Keep every character name exactly as written (do not anglicise names). Keep the meaning and the tone;
do not add or explain anything. Keep quotation marks « » and „ “ where the sentence has them.
There are exactly {n} numbered sentences. Reply with exactly {n} translations — one per input, same order.
Do not merge two inputs into one line, even if they are fragments of the same quote. A single name or word is
still one sentence: repeat it in {tl} (names stay as written).
Answer ONLY with JSON: {{"t": ["translation of 1", "translation of 2", ...]}}

{items}"""

NUM_LINE = re.compile(r"^\s*(?:\[)?(\d+)(?:\]|\.)\s+(.*\S)\s*$")


def _json_blobs(text):
    """Every JSON value that can be decoded from the reply (objects and arrays), first-wins order."""
    text = re.sub(r"```(?:json)?", "", text or "")
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "[{":
            try:
                v, end = json.JSONDecoder().raw_decode(text[i:])
                out.append(v)
                i += max(end, 1)
                continue
            except ValueError:
                pass
        i += 1
    return out


def _as_list(value, n):
    if isinstance(value, str):
        return [value] if n == 1 else None
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return value
    return None


def parse_batch_reply(text, n):
    """Pull n translation strings out of a model reply, or None if the reply is unusable.

    Accepts: {"t": [...]}, {"translations": [...]}, a raw JSON array, or a numbered list (1. …).
    """
    if not (text or "").strip() or n < 1:
        return None
    for blob in _json_blobs(text):
        if isinstance(blob, dict):
            for key in ("t", "translations", "lines"):
                got = _as_list(blob.get(key), n)
                if got is not None and len(got) == n:
                    return [x.strip() for x in got]
        got = _as_list(blob, n)
        if got is not None and len(got) == n:
            return [x.strip() for x in got]
    numbered = []
    for ln in text.splitlines():
        m = NUM_LINE.match(ln)
        if m:
            numbered.append((int(m.group(1)), m.group(2).strip().strip('"')))
    if len(numbered) == n and [i for i, _ in numbered] == list(range(1, n + 1)):
        return [t for _, t in numbered]
    plain = [ln.strip().strip('"') for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("```")]
    if len(plain) == n and not any(p.startswith("{") or p.startswith("[") for p in plain):
        return plain
    return None


def _why(text, n, parsed):
    if parsed is not None:
        return None
    blobs = _json_blobs(text)
    if not blobs:
        lines = [ln for ln in (text or "").splitlines() if ln.strip()]
        return f"no JSON (reply {len(text or '')} chars, {len(lines)} lines)"
    first = blobs[0]
    if isinstance(first, dict):
        got = first.get("t") or first.get("translations")
        if isinstance(got, list):
            return f"JSON had {len(got)} strings, needed {n}"
        return f"JSON object keys {list(first)[:6]}"
    if isinstance(first, list):
        return f"JSON array of {len(first)}, needed {n}"
    return "unreadable reply"


def _ask(b, sl, tl, model):
    items = "\n".join(f"{k + 1}. {t}" for k, t in enumerate(b))
    text, _ = ai.chat(PROMPT.format(sl=LANG.get(sl, sl), tl=LANG.get(tl, tl), n=len(b), items=items),
                      model=model, max_tokens=8000)
    return text


def _translate_batch(b, sl, tl, model, log, tries=3):
    """One batch. After `tries` unusable replies, split and ask the halves (one sentence is the floor)."""
    if not b:
        return []
    last = ""
    for attempt in range(tries):
        text = _ask(b, sl, tl, model)
        last = text
        got = parse_batch_reply(text, len(b))
        if got and all(x.strip() for x in got):
            return got
        why = _why(text, len(b), got)
        log(f"batch of {len(b)}: the reply did not match ({why}), asking again")
    if len(b) == 1:
        raise RuntimeError(f"the AI did not translate a batch of 1 sentences: {_why(last, 1, None)}")
    mid = max(1, len(b) // 2)
    log(f"batch of {len(b)} still unmatched; splitting into {mid} + {len(b) - mid}")
    return (_translate_batch(b[:mid], sl, tl, model, log, tries)
            + _translate_batch(b[mid:], sl, tl, model, log, tries))


def translate(texts, sl="it", tl="en", model=None, batch=16, log=print):
    """[text] -> [translation] (same order). Several calls in parallel; a bad batch is split, not dropped."""
    from .. import presets
    sl, tl = presets.asr_lang(sl, "it"), presets.asr_lang(tl, "en")
    batches = [texts[k:k + batch] for k in range(0, len(texts), batch)]

    def run(b):
        return _translate_batch(b, sl, tl, model, log)

    workers = 2 if len(batches) > 1 else 1
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(run, batches))
    return [t for part in res for t in part]


def _read_todo(todo_tsv):
    texts = []
    for ln in open(todo_tsv, encoding="utf-8"):
        if not ln.strip():
            continue
        texts.append(ln.rstrip("\n").split("\t", 1)[0])
    return texts


def _read_filled(path):
    """source -> translation, last write wins. Used so a retry does not pay for lines already done."""
    out = {}
    p = Path(path)
    if not p.is_file():
        return out
    for ln in p.read_text(encoding="utf-8").splitlines():
        if "\t" not in ln:
            continue
        a, b = ln.split("\t", 1)
        if a and b.strip():
            out[a] = b.strip()
    return out


def fill_todo(todo_tsv, out_tsv, sl="it", tl="en", model=None, log=print):
    """The pipeline's TSV of untranslated sentences (text<TAB>) -> the same TSV filled (text<TAB>translation)."""
    texts = _read_todo(todo_tsv)
    if not texts:
        open(out_tsv, "w", encoding="utf-8").write("")
        return 0
    have = _read_filled(out_tsv)
    need_i = [i for i, t in enumerate(texts) if t not in have]
    log(f"translating {len(need_i)} of {len(texts)} sentences with {model or ai.settings()['AI_MODEL']}"
        + (f" ({len(texts) - len(need_i)} already filled)" if len(need_i) != len(texts) else ""))
    if need_i:
        fresh = translate([texts[i] for i in need_i], sl, tl, model, log=log)
        for i, tr in zip(need_i, fresh):
            have[texts[i]] = " ".join(tr.split())
    with open(out_tsv, "w", encoding="utf-8") as f:
        for a in texts:
            f.write(f"{a}\t{have.get(a, '')}\n")
    missing = [a for a in texts if not have.get(a)]
    if missing:
        raise RuntimeError(f"the AI left {len(missing)} sentences untranslated")
    return len(texts)


def one(text, sl="it", tl="en", model=None, before="", after=""):
    """One subtitle line (the editor's re-translate), with its neighbours as context."""
    from .. import presets
    sl, tl = presets.asr_lang(sl, "it"), presets.asr_lang(tl, "en")
    ctx = (f"\nContext only (do not translate): before: {before!r}; after: {after!r}" if before or after else "")
    text_out, _ = ai.chat(f"Translate this {LANG.get(sl, sl)} subtitle line of a drama into natural spoken "
                          f"{LANG.get(tl, tl)}. Keep character names as written. Answer ONLY with JSON "
                          f'{{"t": "..."}}.{ctx}\n\nLine: {text}', model=model or ai.settings()["AI_MODEL_FAST"],
                          max_tokens=600)
    parsed = parse_batch_reply(text_out, 1)
    if parsed:
        return parsed[0]
    return ai.extract_json(text_out, "t")["t"].strip()
