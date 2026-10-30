"""Translation by Claude (AI gateway), for when the pipeline's Google translation is blocked or fails.

The pipeline stays in charge of the subtitle file: `translate --export-todo` lists the sentences that have no
translation yet, Claude fills them here, and `translate --engine file --import` builds the English SRT from them
(same sentence split, character names kept, the dub's exact cue times)."""
import json
from concurrent.futures import ThreadPoolExecutor
from .. import ai

LANG = {"it": "Italian", "en": "English", "de": "German", "fr": "French", "es": "Spanish", "pt": "Portuguese"}

PROMPT = """Translate these {sl} subtitle sentences of a dubbed drama episode into natural, spoken {tl} for
subtitles. Keep every character name exactly as written (do not anglicise names). Keep the meaning and the tone;
do not add or explain anything. Keep quotation marks « » where the sentence has them.
Answer ONLY with JSON: {{"t": ["translation of 1", "translation of 2", ...]}} -- one string per numbered sentence, same order.

{items}"""


def translate(texts, sl="it", tl="en", model=None, batch=40, log=print):
    """[text] -> [translation] (same order). Several calls in parallel; each batch is checked for its count."""
    batches = [texts[k:k + batch] for k in range(0, len(texts), batch)]

    def run(b):
        items = "\n".join(f"{k + 1}. {t}" for k, t in enumerate(b))
        for attempt in range(3):
            text, _ = ai.chat(PROMPT.format(sl=LANG.get(sl, sl), tl=LANG.get(tl, tl), items=items),
                              model=model, max_tokens=8000)
            try:
                out = ai.extract_json(text, "t")["t"]
                if len(out) == len(b) and all(isinstance(x, str) and x.strip() for x in out):
                    return [x.strip() for x in out]
            except Exception:
                pass
            log(f"batch of {len(b)}: the reply did not match, asking again")
        raise RuntimeError(f"the AI did not translate a batch of {len(b)} sentences")

    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(run, batches))
    return [t for b in res for t in b]


def fill_todo(todo_tsv, out_tsv, sl="it", tl="en", model=None, log=print):
    """The pipeline's TSV of untranslated sentences (text<TAB>) -> the same TSV filled (text<TAB>translation)."""
    texts = [ln.rstrip("\n").split("\t", 1)[0] for ln in open(todo_tsv, encoding="utf-8") if ln.strip()]
    if not texts:
        open(out_tsv, "w", encoding="utf-8").write(""); return 0
    log(f"translating {len(texts)} sentences with {model or ai.settings()['AI_MODEL']}")
    tr = translate(texts, sl, tl, model, log=log)
    with open(out_tsv, "w", encoding="utf-8") as f:
        for a, b in zip(texts, tr):
            f.write(f"{a}\t{' '.join(b.split())}\n")
    return len(texts)


def one(text, sl="it", tl="en", model=None, before="", after=""):
    """One subtitle line (the editor's re-translate), with its neighbours as context."""
    ctx = (f"\nContext only (do not translate): before: {before!r}; after: {after!r}" if before or after else "")
    text_out, _ = ai.chat(f"Translate this {LANG.get(sl, sl)} subtitle line of a drama into natural spoken "
                          f"{LANG.get(tl, tl)}. Keep character names as written. Answer ONLY with JSON "
                          f'{{"t": "..."}}.{ctx}\n\nLine: {text}', model=model or ai.settings()["AI_MODEL_FAST"],
                          max_tokens=600)
    return ai.extract_json(text_out, "t")["t"].strip()
