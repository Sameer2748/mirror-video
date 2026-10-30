#!/usr/bin/env python3
"""
mksrt.py -- Step 0B: make the SRTs of an episode from its audio/video (+ the script), when it has none.

  Dub side (audio + script):    python3 mksrt.py --media DUB.mp3 --lang it --script SCRIPT.docx --work W --out W/<SHOW>_EP<N>_IT.srt
  Source side (video + script): python3 mksrt.py --media SRC.mp4 --lang de --script SCRIPT.docx --work W --out W/<SHOW>_EP<N>_DE.srt
  (no script for a side: leave --script out; --names CHARACTERS.csv then fixes the spelling of names)

Writes two SRTs from the same cleaned, timed words:
  OUT                  subtitle rows for the burned-in subtitles (Steps 8-9): at most 6 words and 4 s per row
                       (--row-words, --row-seconds); a row also ends at a sentence end (3+ words) and at a 1-s pause.
  OUT_sentences.srt    one row per sentence: the SRT for the sentence matching (Steps 1A-4: words, analyze, ...).
  (+ OUT_raw.srt = as Whisper heard it, OUT_report.json = the cleaning report)

How:
1. Whisper (faster-whisper "small", CPU, 60-s pieces, resumable, cached in W/asr = the cache `dubstitch.py words` uses)
   hears every word with its time. Invented credit lines ("Sottotitoli ... a cura di") are removed. Where the script
   has 2+ words Whisper skipped and the audio has room for them, that stretch is heard again with the script as a hint.
2. Cleaning: the SRT-cleaning platform's method (srtclean.py = srt-cleaning-platform/pipeline.py, unchanged), run on a
   one-word-per-row SRT so every word keeps its own time: the script decides spelling, names, punctuation, quotes.
   Then small leftovers (1-3 words where voice and script still differ) take the script's words (--keep-heard: not).
3. The cleaned words are grouped into the two SRTs above.
`--engine mlx --model mlx-community/whisper-large-v3-turbo` uses the Apple GPU (better words without a script, less
exact times).
"""
import argparse, csv, difflib, json, os, re, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)


def ts(t):
    t = max(t, 0.0); ms = int(round(t * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def media_length(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(r.stdout.strip())


def heard_words(a):
    """[(start, end, word)] for the whole media."""
    if a.engine == "mlx":
        import dubstitch as D
        tag = re.sub(r"[^A-Za-z0-9.-]", "_", a.model)
        cache = os.path.join(D.asr_dir(a.work, a.media), f"{a.lang}_mlx_{tag}.json")
        if not os.path.exists(cache):
            import mlx_whisper
            r = mlx_whisper.transcribe(a.media, path_or_hf_repo=a.model, language=a.lang, word_timestamps=True,
                                       condition_on_previous_text=False, verbose=False)
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            json.dump([[w["start"], w["end"], w["word"].strip()] for s in r["segments"] for w in s.get("words", [])], open(cache, "w"), ensure_ascii=False)
        return [tuple(w) for w in json.load(open(cache)) if w[2].strip()]
    import dubstitch as D
    models = a.models or os.path.join(HERE, "..", "data", "models")
    words, todo = D.transcribe_words(a.media, D.asr_dir(a.work, a.media), a.lang, models, 0.0, media_length(a.media),
                                     max_seconds=a.max_seconds, model_name=a.model)
    if todo: print(f"{todo} audio pieces still to do -- run the same command again (it resumes)"); sys.exit(3)
    return [(w[0], w[1], w[2]) for w in words]


# Whisper's well-known invented lines (credits it learned from subtitled TV), heard in silence or music at the end.
# TOLR ep24 (Italian): "Sottotitoli e revisione QTSS a cura di" after the last line.
HALLUCINATIONS = [r"sottotitoli\b.{0,60}?(a cura di|amara\.org)(\s+\S+)?", r"sottotitolato da\b.{0,40}",
                  r"untertitel(ung)?\b.{0,60}?(zdf|amara\.org|ard|funk)\S*", r"subtitles by\b.{0,40}", r"amara\.org"]


def drop_hallucinations(words):
    text, pos = "", []
    for i, w in enumerate(words):
        if text: text += " "
        pos.append((len(text), i)); text += w[2]
    drop = set()
    for pat in HALLUCINATIONS:
        for m in re.finditer(pat, text, re.I):
            drop.update(i for p, i in pos if m.start() <= p < m.end())
    return [w for i, w in enumerate(words) if i not in drop], [words[i][2] for i in sorted(drop)]


def _n(w):
    return re.sub(r"[^\w]", "", w.lower())


def listen_again(a, words, script_words):
    """Whisper sometimes skips a sentence inside a long piece (TOLR ep24: '«Emma Reali.» Scandì il suo nome.' at
    200.4-204.6 s came back as nothing). Where the script has 2+ words that were not heard AND the audio has room for
    them, that stretch is heard again alone with the script text as a hint (like `dubstitch.py words` does)."""
    if a.engine != "faster": return words, []
    H = [_n(w[2]) for w in words]; S = [_n(w) for w in script_words]
    sm = difflib.SequenceMatcher(None, H, S, autojunk=False); jobs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag not in ("insert", "replace") or (j2 - j1) - (i2 - i1) < 2: continue
        lo, hi = max(i1 - 2, 0), min(i2 + 1, len(words) - 1)       # 2 heard words around it: a neighbour can be mis-heard too
        t0, t1 = words[lo][0], words[hi][1]
        room = sum(max(words[k + 1][0] - words[k][1], 0) for k in range(lo, hi))   # silence Whisper left in that stretch
        if room < 0.18 * ((j2 - j1) - (i2 - i1)): continue          # no time in the audio for the missing words: really not said
        jobs.append((max(t0 - 0.4, 0), t1 + 0.4, " ".join(script_words[max(j1 - 3, 0):j2 + 3]), t0, t1))
    if not jobs: return words, []
    import dubstitch as D
    from faster_whisper import WhisperModel
    models = a.models or os.path.join(HERE, "..", "data", "models")
    model = WhisperModel(a.model, device="cpu", compute_type="int8", download_root=os.path.join(models, "whisper")); done = []
    for w0, w1, hint, c0, c1 in jobs:
        fp = os.path.join(D.asr_dir(a.work, a.media), f"{a.lang}_{a.model}_mksrt_redo_{w0:09.2f}_{w1:09.2f}.json")
        if not os.path.exists(fp):
            x = D._pcm16k(a.media, w0, w1 - w0)
            segs, _ = model.transcribe(x / 32768.0, language=a.lang, word_timestamps=True, beam_size=5, initial_prompt=hint,
                                       condition_on_previous_text=False)
            ws = [(round(w0 + w.start, 3), round(w0 + w.end, 3), w.word.strip(), round(w.probability, 2)) for sg in segs for w in sg.words]
            json.dump([list(w) for w in D.trim_words_to_voice(ws, x, w0) if D._letters(w[2])], open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        # swap exactly the heard words c0..c1 (first to last word of the stretch) for the new ones in the same stretch
        new = [(w[0], w[1], w[2]) for w in json.load(open(fp, encoding="utf-8")) if c0 - 0.15 <= w[0] and w[1] <= c1 + 0.15]
        if not new: continue
        n0, n1 = new[0][0], new[-1][1]                              # replace only what the new pass really covers
        old = [w for w in words if n0 - 0.05 <= w[0] and w[1] <= n1 + 0.05]
        hint_set = set(_n(x) for x in hint.split())
        if sum(_n(w[2]) in hint_set for w in new) > sum(_n(w[2]) in hint_set for w in old):
            words = sorted([w for w in words if w not in old] + new); done.append(f"{w0:.1f}-{w1:.1f}s")
    return words, done


def parse_blocks(path):
    """SRT -> [[index, 'start --> end', text]]; safe for blocks whose text is empty (the cleaner can leave some)."""
    L = open(path, encoding="utf-8-sig").read().split("\n"); out = []; k = 0
    while k < len(L):
        if L[k].strip().isdigit() and k + 1 < len(L) and "-->" in L[k + 1]:
            idx, tsl = L[k].strip(), L[k + 1].strip(); k += 2; txt = []
            while k < len(L) and not (L[k].strip().isdigit() and k + 1 < len(L) and "-->" in L[k + 1]):
                if L[k].strip(): txt.append(L[k].strip())
                k += 1
            out.append([idx, tsl, " ".join(txt)])
        else:
            k += 1
    return out


def script_wins(srt_path, script_words, max_words=3):
    """After the cleaning: small leftovers where the heard words and the script still differ (Whisper heard a stray
    'a'/'la'/'che', or missed a short word such as 'il', 'testa', '«Ah,') are settled in favour of the SCRIPT, so every
    word is spelled as written. Runs of more than max_words words stay as the cleaning left them (and stay flagged)."""
    script_words = glue(" ".join(script_words).split())
    blocks = [[i, t, glue(x.split())] for i, t, x in parse_blocks(srt_path)]
    W = [(w, bi) for bi, b in enumerate(blocks) for w in b[2]]
    sm = difflib.SequenceMatcher(None, [_n(w) for w, _ in W], [_n(w) for w in script_words], autojunk=False)
    out, done = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or max(i2 - i1, j2 - j1) > max_words:
            out += W[i1:i2]; continue
        bi = W[i1][1] if i1 < len(W) else (W[-1][1] if W else 0)
        if i1 == i2 and i1 > 0: bi = W[i1 - 1][1]                  # missing words join the card of the word before them
        out += [(w, bi) for w in script_words[j1:j2]]
        done.append(f"'{' '.join(w for w, _ in W[i1:i2])}' -> '{' '.join(script_words[j1:j2])}'")
    for b in blocks: b[2] = []
    for w, bi in out: blocks[bi][2].append(w)
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(f"{b[0]}\n{b[1]}\n{' '.join(b[2])}\n" for b in blocks if b[2]))
    return done


def write_cards(C, path, hold=0.0):
    """C = list of cards, each a list of (start, end, word). A card shows until its last word ends (+ hold seconds,
    never past the next card's start)."""
    with open(path, "w", encoding="utf-8-sig") as f:
        for i, c in enumerate(C, 1):
            end = c[-1][1] + hold
            if hold: end = min(end, max(c[-1][1], c[0][0] + 4.0))  # the extra hold never makes a row longer than 4 s
            if i < len(C): end = min(end, C[i][0][0])          # never overlap the next card
            # Final subtitle policy: guillemets are not wanted in delivered SRTs.
            text = re.sub(r"[«»]", "", " ".join(x[2] for x in c))
            f.write(f"{i}\n{ts(c[0][0])} --> {ts(max(end, c[0][0] + 0.2))}\n{text}\n\n")


SENT_END = re.compile(r"[.!?\u2026]+[»”“’›\"')\]]*$")   # German closes quotes with “ (TGS ep34 "abkaufe?“ Auf der ...")


def make_subs(words, max_words=6, max_dur=4.0, pause=1.0):
    """Subtitle rows for the burned-in subtitles: at most max_words words and max_dur seconds of speech per row.
    Each sentence (also cut at a pause of `pause` s) is split into the fewest rows that fit, with the words shared
    evenly (8 words -> 4 + 4, not 6 + 2); a row of 1-2 words then joins a neighbouring row when both still fit."""
    segs, cur = [], []
    for w in words:
        if cur and w[0] - cur[-1][1] >= pause: segs.append(cur); cur = []
        cur.append(w)
        if SENT_END.search(w[2]): segs.append(cur); cur = []
    if cur: segs.append(cur)
    rows = []
    for sg in segs:
        k = max(1, -(-len(sg) // max_words))
        while True:
            parts = [sg[round(i * len(sg) / k):round((i + 1) * len(sg) / k)] for i in range(k)]
            if all(p[-1][1] - p[0][0] <= max_dur for p in parts if p) or k >= len(sg): break
            k += 1
        rows += [p for p in parts if p]
    fits = lambda r: len(r) <= max_words and r[-1][1] - r[0][0] <= max_dur
    out = []
    for r in rows:                                       # a 1-2 word row joins the row before it when that still fits
        if out and len(r) <= 2 and r[0][0] - out[-1][-1][1] < pause and fits(out[-1] + r):
            out[-1] = out[-1] + r
        elif out and len(out[-1]) <= 2 and r[0][0] - out[-1][-1][1] < pause and fits(out[-1] + r):
            out[-1] = out[-1] + r                         # ... or the row after it
        else:
            out.append(r)
    return out


def make_sentences(words):
    """One row per sentence (for the sentence matching, Step 1A/1B): a sentence ends at . ! ? ... (+ closing quotes)."""
    C, cur = [], []
    for w in words:
        cur.append(w)
        if SENT_END.search(w[2]): C.append(cur); cur = []
    if cur: C.append(cur)
    return C


def glue(tokens):
    """A token with no letters/digits belongs to a word: '.', '»', '...' to the one before it, '«', '„' to the next."""
    out, pre = [], ""
    for t in tokens:
        if not re.search(r"\w", t):
            if re.fullmatch(r"[«„“(\[]+", t): pre += t
            elif out: out[-1] += t
            else: pre += t
        else:
            out.append(pre + t); pre = ""
    if pre: out.append(pre)
    return out


def read_word_blocks(path):
    """A cleaned one-word-per-block SRT -> [(start, end, word)]; a block that now holds several words shares its time."""
    out = []
    f = lambda x: int(x[:2]) * 3600 + int(x[3:5]) * 60 + float(x[6:].replace(",", "."))
    for idx, tsl, text in parse_blocks(path):
        if not text.strip(): continue
        a_, c_ = [x.strip() for x in tsl.split("-->")]
        t0, t1 = f(a_), f(c_); toks = glue(text.split()); n = len(toks)
        out += [(round(t0 + (t1 - t0) * k / n, 3), round(t0 + (t1 - t0) * (k + 1) / n, 3), t) for k, t in enumerate(toks)]
    return out


def _key(s):
    s = s.lower().replace("ph", "f").replace("ie", "i").replace("y", "i").replace("ä", "a").replace("ö", "o").replace("ü", "u")
    return re.sub(r"(.)\1+", r"\1", s)


# words of titles in the localisation sheet ("The Moon Goddess", "il Re Alfa") that are not names: without this list
# "The" + genitive "s" turned the heard word "These" into "Thes" (WBM ep9)
NOT_NAMES = {"the", "and", "for", "von", "van", "der", "die", "das", "den", "des", "del", "della", "dei", "degli", "delle",
             "dal", "dalla", "una", "uno", "les", "the", "mrs", "miss", "king", "queen", "prince", "princess", "lord",
             "lady", "alpha", "student", "teacher", "girl", "boy", "man", "woman", "mother", "father", "il", "la", "lo"}


def load_names(path, cols):
    names = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            for c in cols:
                for n in re.split(r"[\s/,]+", (row.get(c) or "").strip()):
                    if len(n) >= 3 and n.isalpha() and n.lower() not in NOT_NAMES: names.add(n[0].upper() + n[1:].lower())
    return sorted(names)


def fix_names(words, names):
    """Replace a capitalised heard word by a character name (or its genitive -s) when they sound the same."""
    cands = {}
    for n in names:
        cands[n] = n; cands[n + "s"] = n + "s"
    keys = {k: _key(k) for k in cands}; out = []; fixed = []
    for s, e, w in words:
        m = re.match(r"^(\W*)(\w+)(\W*)$", w)
        if m and m.group(2)[:1].isupper() and m.group(2) not in cands and len(m.group(2)) >= 5:
            b = m.group(2); kb = _key(b); best, br = None, 0.0
            for c, kc in keys.items():
                if c[0].lower() != b[0].lower() or abs(len(c) - len(b)) > 1: continue
                r = difflib.SequenceMatcher(None, kb, kc).ratio()
                if r > br: best, br = c, r
            if best and br >= 0.85:      # tested on ep24-26: every fix right (0.8 also 'fixed' real words: Braun->Bruno)
                fixed.append((b, best)); w = m.group(1) + best + m.group(3)
        out.append((s, e, w))
    return out, fixed


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--media", required=True); p.add_argument("--lang", required=True); p.add_argument("--out", required=True)
    p.add_argument("--work", required=True, help="the episode's work folder (Whisper cache W/asr is shared with `words`)")
    p.add_argument("--script", help="the script (.docx/.txt) of this language: cleans the SRT word by word (srtclean.py)")
    p.add_argument("--sensitivity", default="aggressive", choices=["conservative", "balanced", "aggressive"],
                   help="how freely the cleaner replaces heard words by the script (aggressive: tested best on TOLR ep24-26)")
    p.add_argument("--keep-heard", action="store_true", help="keep small heard/script differences as heard (default: the script's words win)")
    p.add_argument("--names", help="character list CSV: fixes the spelling of names (when there is no script)")
    p.add_argument("--names-cols", default="First Name (Original),Last Name (Original)")
    p.add_argument("--engine", default="faster", choices=["faster", "mlx"]); p.add_argument("--model", default="small")
    p.add_argument("--models"); p.add_argument("--max-seconds", type=float, default=1e9)
    p.add_argument("--row-words", type=int, default=6, help="subtitle rows: at most this many words (default 6)")
    p.add_argument("--row-seconds", type=float, default=4.0, help="subtitle rows: at most this long (default 4 s)")
    a = p.parse_args(); t0 = time.time()
    words = heard_words(a)
    words, gone = drop_hallucinations(words)
    if gone: print(f"dropped Whisper's invented credit line: {' '.join(gone)}")
    if a.script:
        import srtclean as P
        words, redone = listen_again(a, words, P.extract_script_words(a.script, list(P.STAGE_TAG_RE_DEFAULT)))
        if redone: print(f"listened again (Whisper had skipped script words there): {', '.join(redone)}")
    if a.names:
        words, fixed = fix_names(words, load_names(a.names, a.names_cols.split(",")))
        print(f"names corrected from {os.path.basename(a.names)}: {len(fixed)} " + ", ".join(sorted({f'{x}->{y}' for x, y in fixed}))[:600])
    base = a.out[:-4] if a.out.lower().endswith(".srt") else a.out
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    final = words
    if a.script:
        import srtclean as P
        from pathlib import Path
        write_cards(make_subs(words, a.row_words, a.row_seconds), base + "_raw.srt", hold=0.5)     # as heard, for reference
        # clean ONE WORD PER BLOCK, so every cleaned word keeps its own time and can be regrouped freely afterwards
        tmp = Path(a.work) / "srtclean"; tmp.mkdir(parents=True, exist_ok=True); per_word = tmp / "heard_words.srt"
        write_cards([[w] for w in words], str(per_word))
        res = P.clean_episode(0, a.script, str(per_word), tmp, tmp, sensitivity=a.sensitivity)
        rep = json.load(open(res["report_path"])); os.replace(res["report_path"], base + "_report.json")
        if not a.keep_heard:
            sw = P.extract_script_words(a.script, list(P.STAGE_TAG_RE_DEFAULT))
            first = (P.docx.Document(a.script).paragraphs[0].text if a.script.lower().endswith(".docx")
                     else open(a.script, encoding="utf-8", errors="replace").readline())
            if re.match(r"\s*(episode|episodio|folge|ep)\.?\s*\d+", first or "", re.I):     # drop the title line
                sw = " ".join(sw).split()[len(first.split()):]
            done = script_wins(res["out_path"], sw)
            if done: print(f"script spelling used for {len(done)} small leftovers: " + "; ".join(done))
        final = read_word_blocks(res["out_path"])
        flagged = [c for c in rep["checks"] if c["type"] not in ("corrected", "stage-direction-not-spoken")
                   and not (c["type"] == "script-only-not-inserted" and re.match(r"\s*(episode|episodio|folge|ep)\.?\s*\d+", c["script"], re.I))]
        print(f"cleaned with {os.path.basename(a.script)}: {rep['num_corrected']} corrections, {len(flagged)} places where voice and script differ")
    subs = make_subs(final, a.row_words, a.row_seconds); write_cards(subs, a.out, hold=0.5)
    # every word with its own time (dynamic subtitles, `dubstitch.py dynsrt --words`)
    json.dump([list(w) for w in final], open(base + "_words.json", "w", encoding="utf-8"), ensure_ascii=False)
    sents = make_sentences(final); write_cards(sents, base + "_sentences.srt")
    print(f"{len(final)} words -> subtitles {a.out} ({len(subs)} rows, at most {a.row_words} words / {a.row_seconds:g} s)"
          f" + sentences {base}_sentences.srt ({len(sents)} rows, for the sentence matching)")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
