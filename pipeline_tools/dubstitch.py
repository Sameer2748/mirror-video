#!/usr/bin/env python3
"""
dubstitch.py -- match a dubbed episode (dub SRT + dub audio) against the original
(source SRT + source video) of the same episode, build the line-by-line mapping
sheet, and stitch a localized video from the source footage synced to the dub audio.

One file, no project imports. Needs python3, ffmpeg/ffprobe and
    pip install sentence-transformers numpy openpyxl pillow
pip install faster-whisper   (Step 1A `words`, and `listen`)

Run in order, always with the same --work folder:
  words     (Step 1A) exact sentence times from the AUDIO: Whisper hears every word, the SRT's words are lined up
            with it letter by letter; run once for --side src and once for --side dub; `analyze` then uses them
  analyze   check each SRT is timed to its media (auto-fixes a source SRT timed to another
            cut, e.g. a .wav with extra lead-in); parse + clean + embed + align + auto-match;
            print the reading report;
            write <work>/decisions.json template            (see SKILL.md)
  (manual)  read <work>/analysis.txt, fill <work>/decisions.json by MEANING
  sheet     apply decisions, run integrity checks, write the mapping .xlsx
  manifest  gap-free, bleed-checked edit list (<work>/manifest.json)
  groups    continuous US footage per block of 7-8 Italian rows (<work>/manifest_groups.json + review workbook)
  render    frame-exact render of the active edit list + mux dub audio, no subtitles (resumable)
  verify    sync-check every segment against the source + QC contact sheet
  bookends  add the source's unused opening and ending (1x, own sound) -> the CLEAN master video
  transitions  a transition at every clip switch of the clean master (TOLR: light leak / blur dissolve / zoom blur,
            random order, never twice in a row; length and sync unchanged)  [`glow` = the same with the glow only]
  captions  (HIH, dynamic strip) read the source's burned-in captions (tesseract OCR): lines per caption, where they sit
  dynsrt    (HIH, dynamic strip) dub cards that fill a strip 2 or 3 caption lines tall, each word at its own time
  subs      burn an SRT (white strip, Poppins Bold; --style box; --style dynamic for HIH) onto the clean master
  translate translate the dub SRT through Google, sentence by sentence, onto the same cards/times
  check     final check of the episode package (clean + subtitled videos, sheets, translated SRT)
  speech    (optional) which source sentences are dialogue in quotes vs narration
  lipsync   rows whose source sentence shows moving lips (lip scan of the whole video) play that sentence's whole clip
  lipmark   (tests) mark each word-timed sentence "speaking on screen" or not, from lipdetect.py --range results
  listen    (optional) transcribe a stretch of the dub audio, e.g. to confirm a repeat
"""
import argparse, difflib, hashlib, io, json, math, os, re, shutil, subprocess, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict

# ---------------------------------------------------------------------------------
# 1. SRT parsing, sentence resegmentation, overlap resolution (both sides)
# ---------------------------------------------------------------------------------
_TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})")
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_SCENE_MARK = re.compile(r"°+|\*+")   # "°°°" scene breaks and *italic* asterisks are markup, not text
# a sentence ends at . ! ? … (plus any closing quotes) when the next word does NOT start with a small letter: a capital, a
# digit, ANY quote mark („ « “ ‘ ‚ ‹ " '), a dash or a bracket starts a new sentence (TGS ep34: "zerschellen. “Du bist ..."
# was one row because “ was not known as an opening quote -- never again for any quote style)
_SENT_END = re.compile(r'(?<=[.!?…])["»«“”’›‹\']*\s+(?=\S)(?![a-zß\u00e0-\u00ff\u0101-\u017f])')

@dataclass
class Cue:
    index: int
    start_ms: int
    end_ms: int
    text: str

def _ms(h, m, s, x):
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(x)

def parse_srt(path):
    raw = open(path, encoding="utf-8-sig", errors="replace").read().replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = block.split("\n")
        ts = next((i for i, ln in enumerate(lines[:2]) if _TS.search(ln)), None)
        if ts is None:
            continue
        g = _TS.search(lines[ts]).groups()
        text = _TAG.sub("", " ".join(l.strip() for l in lines[ts + 1:] if l.strip())).strip()
        text = re.sub(r"\s+", " ", _SCENE_MARK.sub(" ", text)).strip()   # drop script markup: "°°°" scene breaks, *italic* asterisks
        if text:
            cues.append(Cue(len(cues), _ms(*g[:4]), _ms(*g[4:]), text))
    cues.sort(key=lambda c: c.start_ms)
    return cues

# Clause mode (WBM ep17 QC, Oct 2026): a long sentence with several actions ("..., Viola sorrise beffarda, ...") is matched
# clause by clause, so the picture changes where that part of the voice starts. `words --clauses N` / `analyze --clauses N`
# turn it on for the work folder (saved in <work>/sentence_mode.json, used by every later step); a sentence is cut after
# , ; : and before a dash, only where both parts keep at least N words. N = 0 (default) = whole sentences.
CLAUSE_MIN = int(os.environ.get("DUBSTITCH_CLAUSES", "0") or 0)
# --clause-long L (Dub Studio, owner Oct 2026): only sentences of at least L words are cut into clauses; shorter ones stay
# whole (every show has talking faces, and the lip scan judges whole sentences). 0 = cut any sentence (as before).
CLAUSE_LONG = int(os.environ.get("DUBSTITCH_CLAUSE_LONG", "0") or 0)
_CLAUSE_CUT = re.compile(r'[,;:]["»”’]?(?=\s)|(?<=\s)[–—](?=\s)')

def clause_bounds(full, bounds, n_min):
    out = []
    for a, b in zip(bounds, bounds[1:]):
        out.append(a); last = a
        if CLAUSE_LONG and len(full[a:b].split()) < CLAUSE_LONG:
            continue                                               # a short or normal sentence stays whole
        for m in _CLAUSE_CUT.finditer(full, a, b):
            cut = m.end() if m.group(0)[0] in ",;:" else m.start()
            if len(full[last:cut].split()) >= n_min and len(full[cut:b].split()) >= n_min:
                out.append(cut); last = cut
    out.append(bounds[-1])
    return sorted(set(out))

def resegment_to_sentences(cues):
    """One unit per sentence (raw subtitle cards are cut by reading speed, not by sentence)."""
    pieces, spans, pos = [], [], 0
    for c in cues:
        if pieces:
            pieces.append(" "); pos += 1
        start = pos; pieces.append(c.text); pos += len(c.text); spans.append((start, pos, c))
    full = "".join(pieces)
    # a closing quote stays with its own sentence («...!» | Next), not at the start of the next one
    bounds = sorted(set([0] + [m.start() + len(m.group(0).rstrip()) for m in _SENT_END.finditer(full)] + [len(full)]))
    if CLAUSE_MIN:
        bounds = clause_bounds(full, bounds, CLAUSE_MIN)
    out = []
    for a, b in zip(bounds, bounds[1:]):
        text = full[a:b].strip()
        ov = [c for (cs, ce, c) in spans if cs < b and ce > a]
        if text and ov:
            out.append(Cue(len(out), ov[0].start_ms, ov[-1].end_ms, text))
    return out

def resolve_overlaps(cues):
    """Cut each boundary at the midpoint of the original overlap. Can collapse a cue to 0ms (see 0 ms rule)."""
    n = len(cues)
    if not n:
        return []
    cut = [0] * (n + 1); cut[0] = cues[0].start_ms; cut[n] = cues[-1].end_ms
    for i in range(1, n):
        pe, cs = cues[i - 1].end_ms, cues[i].start_ms
        cut[i] = (pe + cs) // 2 if cs < pe else cs
    out, cur = [], cut[0]
    for i, c in enumerate(cues):
        s = max(cut[i], cur); e = max(min(cut[i + 1], c.end_ms), s)
        out.append(Cue(c.index, s, e, c.text)); cur = e
    return out

# 0 ms rule (Step 1): zero-duration cues -------------------------------------------------------------
def drop_single_word_zero_duration(cues):
    """Drop a cue only if it is BOTH 0ms long AND a single word ("Federico!", "Forse.")."""
    kept, dropped = [], []
    for c in cues:
        (dropped if (c.start_ms == c.end_ms and len(c.text.split()) <= 1) else kept).append(c)
    return kept, dropped

def retime_zero_duration(cues):
    """Every remaining 0ms cue is real dialogue: share the time of [real neighbour + 0ms run +
    real neighbour] among them in proportion to text length. Outer bounds never move."""
    cues = [Cue(c.index, c.start_ms, c.end_ms, c.text) for c in cues]
    n, i, retimed = len(cues), 0, []
    while i < n:
        if cues[i].end_ms > cues[i].start_ms:
            i += 1; continue
        j = i
        while j < n and cues[j].end_ms <= cues[j].start_ms:
            j += 1
        block = cues[(i - 1 if i > 0 else i):(j if j < n else j - 1) + 1]
        t0, t1 = block[0].start_ms, block[-1].end_ms
        w = [max(len(c.text), 1) for c in block]; tot, cur = sum(w), float(t0)
        for k, c in enumerate(block):
            c.start_ms = round(cur); cur += (t1 - t0) * w[k] / tot; c.end_ms = round(cur)
        block[-1].end_ms = t1
        retimed.extend(cues[i:j]); i = j
    bad = [c.text for c in cues if c.end_ms <= c.start_ms]
    if bad:
        raise SystemExit(f"re-timing could not give these cues any time (no real neighbour): {bad}")
    return cues, retimed

# ---------------------------------------------------------------------------------
# 1b. Subtitle timing check: is each SRT timed to the media it is paired with?
#     (Real case, TDMB ep84: the US SRT was timed to a US .wav that has 12.833 s more
#     lead-in than the US .mp4, so every line pointed 12.8 s too late in the video.)
# ---------------------------------------------------------------------------------
def audio_envelope(path, rate=8000, hop=80):
    """Mono 8 kHz samples + 10 ms loudness envelope of any audio/video file."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32); n = len(x) // hop
    if n == 0:
        raise SystemExit(f"no audio track could be read from {path}")
    return np.abs(x[:n * hop]).reshape(n, hop).mean(1), x

def best_lag(a, b, max_lag):
    """Lag L (samples) maximising corr(a[t], b[t+L]) -> (L, peak, best value >100 samples away from L)."""
    import numpy as np
    a = (a - a.mean()) / (a.std() + 1e-9); b = (b - b.mean()) / (b.std() + 1e-9)
    n = 1 << int(np.ceil(np.log2(len(a) + len(b) + 1)))
    c = np.fft.irfft(np.conj(np.fft.rfft(a, n)) * np.fft.rfft(b, n), n) / min(len(a), len(b))
    lags = np.arange(-max_lag, max_lag + 1); v = c[lags % n]; k = int(np.argmax(v))
    far = np.abs(lags - lags[k]) > 100
    return int(lags[k]), float(v[k]), float(v[far].max()) if far.any() else 0.0

def subtitle_lag_s(cues, env):
    """How many seconds LATE the subtitles are versus the speech in the media (about +-0.05 s)."""
    import numpy as np
    le = np.convolve(np.log(env + 1), np.ones(30) / 30, "same")
    lo, hi = np.percentile(le, 20), np.percentile(le, 90)
    speech = (le > lo + 0.5 * (hi - lo)).astype(np.float32)
    subs = np.zeros(len(env), np.float32)
    for c in cues:
        subs[max(c.start_ms // 10, 0):max(c.end_ms // 10, 0)] = 1
    lag, peak, other = best_lag(speech, subs, 6000)
    return lag / 100, peak, other

def reference_offset_s(video, ref_audio):
    """The SRT was timed to ref_audio. Returns how many seconds later everything happens in ref_audio than in
    the video (constant), or raises if the two are not the same edit."""
    import numpy as np
    ev, xv = audio_envelope(video); er, xr = audio_envelope(ref_audio)
    lag, peak, other = best_lag(ev, er, 9000)
    if peak < 0.4 or peak < 2 * other:
        raise SystemExit(f"{ref_audio} does not sound like the audio of {video} (match {peak:.2f}); "
                         "check the files, or pass --src-offset by hand")
    locs = []
    for frac in (0.1, 0.3, 0.5, 0.7, 0.9):
        t = int(len(xv) * frac); w = xv[t:t + 8000 * 5]; s = t + lag * 80
        if s < 0 or s + len(w) > len(xr) or w.std() < 50:
            continue
        l2, p2, _ = best_lag(w, xr[s:s + len(w)], 800)
        if p2 > 0.5:
            locs.append((lag * 80 + l2) / 8000)
    if len(locs) < 3:
        raise SystemExit("could not measure the subtitle audio offset reliably; pass --src-offset by hand")
    if max(locs) - min(locs) > 0.02:
        raise SystemExit(f"the SRT's audio and the video are different edits (offset changes along the episode: "
                         f"{[round(x, 3) for x in locs]}); this needs manual re-timing")
    return float(np.median(locs))

def shift_cues(cues, ms):
    if not ms:
        return cues
    out = [Cue(c.index, max(c.start_ms - ms, 0), c.end_ms - ms, c.text) for c in cues if c.end_ms - ms > 0]
    return out

def find_sibling_audio(srt):
    stem = os.path.splitext(srt)[0]
    for ext in (".wav", ".WAV", ".mp3", ".m4a", ".aac", ".flac"):
        if os.path.exists(stem + ext):
            return stem + ext
    return None

def check_timing(cfg, a, say):
    """Measure and fix the source SRT offset; then verify BOTH SRTs line up with their media's speech."""
    src_off_ms = 0; how = "none needed"
    ref = a.src_srt_audio or (None if a.src_offset is not None else find_sibling_audio(cfg["src_srt"]))
    if a.src_offset is not None:
        src_off_ms = int(round(a.src_offset * 1000)); how = "given with --src-offset"
    elif ref and os.path.abspath(ref) != os.path.abspath(cfg["src_video"]):
        src_off_ms = int(round(reference_offset_s(cfg["src_video"], ref) * 1000))
        how = f"measured against {os.path.basename(ref)} (the audio the SRT was timed to)"
    cfg["src_offset_ms"] = src_off_ms; cfg["src_offset_how"] = how
    say(f"SOURCE SRT OFFSET: {src_off_ms / 1000:+.3f}s ({how}); source subtitle times are moved by {-src_off_ms / 1000:+.3f}s")
    for side, srt, media, off in (("dub", cfg["dub_srt"], cfg["dub_audio"], 0), ("src", cfg["src_srt"], cfg["src_video"], src_off_ms)):
        env, _ = audio_envelope(media); cues = shift_cues(parse_srt(srt), off)
        lag, peak, other = subtitle_lag_s(cues, env)
        end_s, media_s = cues[-1].end_ms / 1000, len(env) / 100
        say(f"TIMING CHECK {side.upper()}: subtitles vs speech in {os.path.basename(media)}: {lag:+.2f}s "
            f"(match {peak:.2f} vs {other:.2f}); last subtitle ends {end_s:.1f}s, media is {media_s:.1f}s")
        clear = peak >= 1.5 * other
        if (abs(lag) > 1.0 and clear) or end_s > media_s + 1.0:
            flag = "--src-offset" if side == "src" else "a re-timed dub SRT"
            raise SystemExit(f"\n** {side.upper()} SRT IS NOT TIMED TO {media} **\n"
                             f"   its subtitles run {lag:+.2f}s against the speech (last subtitle {end_s:.1f}s, media {media_s:.1f}s).\n"
                             f"   Usually the SRT was timed to another cut (e.g. a .wav with extra lead-in). Put that audio next to\n"
                             f"   the SRT with the same name (or pass --src-srt-audio), or, after checking, use {flag} {lag:.3f}.")
        if not clear:
            say(f"   (timing check inconclusive on the {side} side -- spot-check a few lines by ear)")
    return src_off_ms

# ---------------------------------------------------------------------------------
# 1c. words: exact sentence times from the AUDIO, word by word (Step 1A)
#     SRT cards are cut by reading speed, so a sentence that shares a card with others has no time of its own
#     (TOLR ep24: "Schweinebraten." sat inside card 19.0-25.6 s; it is really spoken 20.98-22.06 s).
#     Whisper (faster-whisper, open source, CPU) gives a time for every word it hears; those words are lined
#     up with the SRT letter by letter, so every SRT word keeps its own spelling and gets the audio's time.
#     A sentence runs from the start of its first word to the end of its last word.
# ---------------------------------------------------------------------------------
def _letters(t):
    import unicodedata
    t = unicodedata.normalize("NFC", t.lower()).replace("\u00df", "ss")
    return [ch for ch in t if ch.isalnum()]

def _pcm16k(media, t0, dur):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t0, 0):.3f}", "-t", f"{dur:.3f}", "-i", media, "-vn", "-ac", "1",
                          "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32)

def trim_words_to_voice(words, x, t0, min_sil=0.10):
    """Whisper often lets a word start where the previous word ended, swallowing the pause in between
    ('Bring' 52.36-53.82 s while the voice starts at 53.66 s). Keep only the strongest voiced block of each
    word, measured on the loudness (10 ms steps) of the same audio."""
    import numpy as np
    n = len(x) // 160
    if n < 10: return words
    env = 20 * np.log10(np.sqrt((x[:n * 160].reshape(n, 160) ** 2).mean(1)) + 1)
    p10, p50 = np.percentile(env, 10), np.percentile(env, 50); thr = p10 + 0.4 * (p50 - p10)
    out = []
    for s, e, w, p in words:
        a, b = int((s - t0) * 100), max(int((e - t0) * 100), int((s - t0) * 100) + 1)
        v = env[max(a, 0):b] > thr
        if a < 0 or not v.any(): out.append((s, e, w, p)); continue
        blocks, k = [], 0
        while k < len(v):
            if not v[k]: k += 1; continue
            j = k
            while j < len(v):
                if v[j]: j += 1; continue
                g = j
                while g < len(v) and not v[g]: g += 1
                if g - j >= min_sil * 100 or g == len(v): break
                j = g
            blocks.append((k, j)); k = j
        k0, k1 = max(blocks, key=lambda q: float((env[a + q[0]:a + q[1]] - thr).clip(0).sum()))
        s2 = t0 + (a + k0) / 100 if k0 > 0 and not v[:k0].all() else s
        e2 = t0 + (a + k1) / 100 if k1 < len(v) and not v[k1:].all() else e
        out.append((round(s2, 3), round(e2, 3), w, p))
    return out

def transcribe_words(media, cache_dir, lang, models, t_from, t_to, chunk=60.0, pad=2.0, max_seconds=1e9, model_name="small"):
    """Every word Whisper hears in [t_from, t_to) of the media, with times. Resumable: one cached file per chunk."""
    lang = str(lang or "en").strip().lower().replace("_", "-").split("-")[0] or "en"
    lang = {"us": "en", "uk": "en", "jp": "ja", "deu": "de", "fra": "fr", "ita": "it"}.get(lang, lang)
    os.makedirs(cache_dir, exist_ok=True); model = None; stop = time.time() + max_seconds; words = []; todo = 0
    k = 0
    while t_from + k * chunk < t_to:
        cs = t_from + k * chunk; ce = min(cs + chunk, t_to); k += 1
        fp = os.path.join(cache_dir, f"{lang}_{model_name}_{cs:09.2f}_{ce:09.2f}.json")
        if not os.path.exists(fp):
            if time.time() > stop: todo += 1; continue
            if model is None:
                from faster_whisper import WhisperModel
                model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=os.path.join(models, "whisper"))
            w0 = max(cs - pad, 0); x = _pcm16k(media, w0, ce + pad - w0)
            segs, _ = model.transcribe(x / 32768.0, language=lang, word_timestamps=True, beam_size=5, condition_on_previous_text=False)
            ws = [(round(w0 + w.start, 3), round(w0 + w.end, 3), w.word.strip(), round(w.probability, 2)) for s in segs for w in s.words]
            ws = [w for w in trim_words_to_voice(ws, x, w0) if cs <= w[0] < ce and _letters(w[2])]
            json.dump(ws, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"  heard {len(ws)} words in {cs:.0f}-{ce:.0f}s", flush=True)
        words += [tuple(w) for w in json.load(open(fp, encoding="utf-8"))]
    return sorted(words), todo

def srt_words_from_audio(sent_texts, heard):
    """Line up the SRT's words with the words Whisper heard, letter by letter. Every SRT word keeps its own
    spelling and gets the time of its matching letters; a word Whisper missed gets a time between its
    neighbours. Returns (words, extra): extra = heard words that are not in the SRT."""
    A = []
    for wi, (s, e, w, *_r) in enumerate(heard):
        L = _letters(w)
        for k, c in enumerate(L): A.append((c, wi, k, len(L)))
    S, words = [], []
    for j, t in enumerate(sent_texts):
        for tok in t.split():
            L = _letters(tok)
            if not L: continue
            words.append(dict(sent=j, word=tok, n=len(L)))
            S += [(c, len(words) - 1) for c in L]
    sm = difflib.SequenceMatcher(None, [c for c, _ in S], [c for c, *_x in A], autojunk=False)
    m = {}
    for a, b, n in sm.get_matching_blocks():
        for k in range(n): m[a + k] = b + k
    def tl(ai, end):
        c, wi, k, n = A[ai]; s, e = heard[wi][0], heard[wi][1]
        return s + (e - s) * (k + (1 if end else 0)) / n
    used, pos = set(), 0
    for w in words:
        hit = [m[k] for k in range(pos, pos + w["n"]) if k in m]; pos += w["n"]
        if len(hit) >= max(1, 0.5 * w["n"]):
            his = sorted({A[x][1] for x in hit}); used.update(his)
            w.update(start=tl(hit[0], False), end=tl(hit[-1], True), heard_as=" ".join(heard[x][2] for x in his))
            w["status"] = "same" if _letters(w["word"]) == _letters(w["heard_as"]) else "written differently"
        else:
            w.update(start=None, end=None, heard_as="", status="not heard (time filled in)")
    i = 0
    while i < len(words):
        if words[i]["start"] is not None: i += 1; continue
        j = i
        while j < len(words) and words[j]["start"] is None: j += 1
        t0 = words[i - 1]["end"] if i else (words[j]["start"] if j < len(words) else 0.0)
        t1 = words[j]["start"] if j < len(words) else t0
        tot = sum(words[k]["n"] for k in range(i, j)); cur = t0
        for k in range(i, j):
            d = (t1 - t0) * words[k]["n"] / max(tot, 1); words[k]["start"], words[k]["end"] = cur, cur + d; cur += d
        i = j
    for w in words: w["start"], w["end"] = round(w["start"], 3), round(w["end"], 3); w.pop("n")
    return words, [heard[k] for k in range(len(heard)) if k not in used]

def cmd_words(a):
    """Sentences of one SRT with exact times from the media's audio -> <work>/sentences_<side>.json (+ .xlsx with --out).
    `analyze` uses these times instead of the SRT-card estimates when the file is there."""
    cfg = load(a.work, "config.json") if os.path.exists(os.path.join(a.work, "config.json")) else {}
    os.makedirs(a.work, exist_ok=True)
    srt = a.srt or cfg.get("src_srt" if a.side == "src" else "dub_srt")
    media = a.media or cfg.get("src_video" if a.side == "src" else "dub_audio")
    lang = a.lang or ("de" if a.side == "src" else "it")
    models = a.models or cfg.get("models") or os.path.expanduser("~/.cache/dubstitch_models")
    if not srt or not media: raise SystemExit("--srt and --media are needed (or run analyze first so config.json has them)")
    sents = resegment_to_sentences(parse_srt(srt))
    two = [(c.index, c.text[:80]) for c in sents if re.search(r'[.!?…]["»«“”’›‹\']*\s+(?![a-zß\u00e0-\u00ff\u0101-\u017f])\S', c.text)]
    print(f"sentence check: {len(sents)} rows; rows that still hold two sentences: {two or 'none'}")
    if a.first: sents = sents[:a.first]
    t_to = a.until or (sents[-1].end_ms / 1000 + 5.0)
    t_to = min(t_to, probe_duration_ms(media) / 1000)
    heard, todo = transcribe_words(media, asr_dir(a.work, media), lang, models, 0.0, t_to, max_seconds=a.max_seconds,
                                   model_name=a.model)
    if todo:
        print(f"{todo} audio chunks still to do -- run the same command again (it resumes)"); sys.exit(3)
    words, extra = srt_words_from_audio([c.text for c in sents], heard)
    # Whisper sometimes skips a short sentence inside a long chunk (TOLR ep24 #117 "Ihre Stimme gewann an Intensität."
    # came back as one word). Listen again to just that sentence's subtitle cards, with its text as a hint.
    redo = []
    for j, c in enumerate(sents):
        ww = [w for w in words if w["sent"] == j]
        if not ww: continue
        nh = sum(w["status"].startswith("not") for w in ww); dur = ww[-1]["end"] - ww[0]["start"]
        if nh >= max(1, 0.4 * len(ww)) or dur < 0.03 * len(_letters(c.text)):
            redo.append(j)
    if redo:
        from faster_whisper import WhisperModel
        model = WhisperModel(a.model, device="cpu", compute_type="int8", download_root=os.path.join(models, "whisper"))
        for j in redo:
            c = sents[j]; w0, w1 = max(c.start_ms / 1000 - 0.5, 0), c.end_ms / 1000 + 0.5
            fp = os.path.join(asr_dir(a.work, media), f"{lang}_{a.model}_redo_{w0:09.2f}_{w1:09.2f}.json")
            if not os.path.exists(fp):
                x = _pcm16k(media, w0, w1 - w0)
                segs, _ = model.transcribe(x / 32768.0, language=lang, word_timestamps=True, beam_size=5, initial_prompt=c.text,
                                           condition_on_previous_text=False)
                ws = [(round(w0 + w.start, 3), round(w0 + w.end, 3), w.word.strip(), round(w.probability, 2)) for sg in segs for w in sg.words]
                json.dump([w for w in trim_words_to_voice(ws, x, w0) if _letters(w[2])], open(fp, "w", encoding="utf-8"), ensure_ascii=False)
            new = [tuple(w) for w in json.load(open(fp, encoding="utf-8"))]
            new = [w for w in new if w[1] <= w1 - 0.25]           # drop a cut-off word at the window's end
            if new:                                             # replace only what the new pass really covers
                n0, n1 = new[0][0], new[-1][1]
                heard = sorted([w for w in heard if w[1] <= n0 or w[0] >= n1] + new)
        words, extra = srt_words_from_audio([c.text for c in sents], heard)
        print(f"listened again to sentences (#) {[j + 1 for j in redo]} (Whisper had skipped words there)")
    if words: extra = [e for e in extra if e[0] <= words[-1]["end"] + 0.5]   # (a test range stops after its last sentence)
    shares = quote_shares([c.text for c in sents]); rows = []
    for j, c in enumerate(sents):
        ww = [w for w in words if w["sent"] == j]
        st = [w["status"] for w in ww]
        rows.append(dict(idx=j, text=c.text, start_ms=round(ww[0]["start"] * 1000) if ww else c.start_ms,
                         end_ms=round(ww[-1]["end"] * 1000) if ww else c.end_ms, srt_start_ms=c.start_ms, srt_end_ms=c.end_ms,
                         words=len(ww), same=st.count("same"), differently=st.count("written differently"),
                         not_heard=sum(s.startswith("not") for s in st), quote_share=round(shares[j][0], 2),
                         text_type="speech" if shares[j][0] >= 0.6 else ("mixed" if shares[j][0] >= 0.15 else "narration")))
    for x, y in zip(rows, rows[1:]):                           # no overlap between neighbouring sentences
        if y["start_ms"] < x["end_ms"]: x["end_ms"] = y["start_ms"] = (x["end_ms"] + y["start_ms"]) // 2
    save(a.work, f"sentences_{a.side}.json", dict(srt=os.path.abspath(srt), media=os.path.abspath(media), lang=lang, model=a.model,
                                                  sentences=rows, words=words, heard_not_in_srt=extra, first=a.first))
    n = len(words); nd = sum(w["status"] == "written differently" for w in words); nn = sum(w["status"].startswith("not") for w in words)
    print(f"{len(rows)} sentences, {n} SRT words: {n - nd - nn} same as heard, {nd} written differently, {nn} not heard (time filled in); "
          f"{len(extra)} heard words not in the SRT -> {os.path.join(a.work, f'sentences_{a.side}.json')}")
    nh = [(w["sent"] + 1, w["word"]) for w in words if w["status"].startswith("not")]
    if nh: print(f"not heard (time filled in between neighbours), as (sentence #, word): {nh}")
    if a.out: sentences_workbook(a.out, rows, words, extra, a.side)

def sentences_workbook(path, rows, words, extra, side):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Sentences"
    H = Font(name="Arial", bold=True, color="FFFFFF"); HF = PatternFill("solid", start_color="1F4E78"); B = Font(name="Arial", size=10)
    hdr = ["#", "Spoken start (s)", "Spoken end (s)", "Seconds", "Sentence (SRT)", "Words: same / written differently / not heard",
           "SRT card start (s)", "SRT card end (s)", "Type from text (quotes)"]
    for c, h in enumerate(hdr, 1):
        x = ws.cell(1, c, h); x.font = H; x.fill = HF; x.alignment = Alignment(wrap_text=True)
    for i, r in enumerate(rows, 2):
        vals = [r["idx"] + 1, r["start_ms"] / 1000, r["end_ms"] / 1000, round((r["end_ms"] - r["start_ms"]) / 1000, 2), r["text"],
                f"{r['same']} / {r['differently']} / {r['not_heard']}", r["srt_start_ms"] / 1000, r["srt_end_ms"] / 1000, r["text_type"]]
        for c, v in enumerate(vals, 1):
            x = ws.cell(i, c, v); x.font = B; x.alignment = Alignment(wrap_text=True, vertical="top")
    for c, w in enumerate([5, 10, 10, 8, 62, 16, 10, 10, 14], 1): ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
    ws.freeze_panes = "A2"
    w2 = wb.create_sheet("Words")
    for c, h in enumerate(["Sentence #", "Word (SRT spelling)", "Start (s)", "End (s)", "Whisper heard", "Check"], 1):
        x = w2.cell(1, c, h); x.font = H; x.fill = HF
    yel, red = PatternFill("solid", start_color="FFEB9C"), PatternFill("solid", start_color="FFC7CE")
    for k, w in enumerate(words, 2):
        for c, v in enumerate([w["sent"] + 1, w["word"], w["start"], w["end"], w["heard_as"], w["status"]], 1):
            x = w2.cell(k, c, v); x.font = B
            if w["status"] == "written differently": x.fill = yel
            if w["status"].startswith("not"): x.fill = red
    for c, wd in enumerate([10, 24, 10, 10, 24, 26], 1): w2.column_dimensions[openpyxl.utils.get_column_letter(c)].width = wd
    w2.freeze_panes = "A2"
    w3 = wb.create_sheet("Heard, not in SRT")
    for c, h in enumerate(["Start (s)", "End (s)", "Word heard"], 1):
        x = w3.cell(1, c, h); x.font = H; x.fill = HF
    for k, e in enumerate(extra, 2):
        for c, v in enumerate(e[:3], 1): w3.cell(k, c, v).font = B
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True); wb.save(path); print(f"saved {path}")

# ---------------------------------------------------------------------------------
# 1d. lipmark: which sentences show moving lips on screen (needs lipdetect.py --range ... results)
# ---------------------------------------------------------------------------------
def cmd_lipmark(a):
    import numpy as np, openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    S = load(a.work, f"sentences_{a.side}.json"); rows = S["sentences"]
    iv, covered = merged_lip_intervals(a.work, a)
    out = []
    for r in rows:
        s, e = r["start_ms"] / 1000, r["end_ms"] / 1000
        if not covered[int(s * 25):max(int(e * 25), int(s * 25) + 1)].all():
            r.update(lips=None, video_type="not analysed"); continue
        hits = [(max(x, s), min(y, e)) for x, y in iv if min(y, e) - max(x, s) > 0]
        sec = round(sum(y - x for x, y in hits), 2)
        r.update(lips=[[round(x, 2), round(y, 2)] for x, y in hits], lip_s=sec,
                 video_type="speaking on screen" if sec >= a.min_lip else "no lip movement on screen")
    outside = [(round(x, 2), round(y, 2)) for x, y in iv
               if sum(max(0, min(y, r["end_ms"] / 1000) - max(x, r["start_ms"] / 1000)) for r in rows) < 0.2]
    S["lip_intervals"] = [[round(x, 2), round(y, 2)] for x, y in iv]; S["lips_outside_sentences"] = outside
    save(a.work, f"sentences_{a.side}.json", S)
    print(f"lip movement found: {[(round(x, 2), round(y, 2)) for x, y in iv]}")
    for r in rows:
        if r.get("video_type") != "not analysed":
            agree = (r["video_type"].startswith("speaking")) == (r["text_type"] != "narration")
            print(f"  #{r['idx'] + 1:3d} {r['start_ms'] / 1000:7.2f}-{r['end_ms'] / 1000:7.2f}s text {r['text_type']:9s} video {r['video_type']:26s}"
                  f"{'' if agree else ' <- differs'}  {r['text'][:45]!r}")
    if outside: print(f"lip movement in pauses between sentences: {outside}")
    if a.out:
        wb = openpyxl.load_workbook(a.out); ws = wb["Sentences"]
        H = Font(name="Arial", bold=True, color="FFFFFF"); HF = PatternFill("solid", start_color="7030A0")
        for k, h in enumerate(["Lip movement inside the sentence (s)", "Seconds of lip movement", "Type after video", "Same as text type?"]):
            x = ws.cell(1, 10 + k, h); x.font = H; x.fill = HF; x.alignment = Alignment(wrap_text=True)
        g, grey = PatternFill("solid", start_color="C6EFCE"), PatternFill("solid", start_color="F2F2F2")
        for i, r in enumerate(rows, 2):
            if r.get("video_type") == "not analysed": vals = ["", "", "not analysed", ""]
            else:
                same = "yes" if (r["video_type"].startswith("speaking")) == (r["text_type"] != "narration") else "NO"
                vals = [", ".join(f"{x:.2f}-{y:.2f}" for x, y in r["lips"]) or "-", r["lip_s"], r["video_type"], same]
            for k, v in enumerate(vals):
                x = ws.cell(i, 10 + k, v); x.font = Font(name="Arial", size=10, bold=(k == 3 and v == "NO"))
                x.alignment = Alignment(wrap_text=True, vertical="top")
                if r.get("video_type", "").startswith("speaking"): x.fill = g
                elif r.get("video_type") != "not analysed": x.fill = grey
        for k, w in enumerate([24, 10, 24, 10]): ws.column_dimensions[openpyxl.utils.get_column_letter(10 + k)].width = w
        if "Lip movements" in wb.sheetnames: del wb["Lip movements"]
        w4 = wb.create_sheet("Lip movements", 1)
        for c, h in enumerate(["#", "Start (s)", "End (s)", "Seconds", "Sentence(s)"], 1):
            x = w4.cell(1, c, h); x.font = H; x.fill = HF
        for k, (x0, y0) in enumerate(S["lip_intervals"], 2):
            sn = [str(r["idx"] + 1) for r in rows if min(y0, r["end_ms"] / 1000) - max(x0, r["start_ms"] / 1000) > 0]
            for c, v in enumerate([k - 1, x0, y0, round(y0 - x0, 2), ", ".join(sn) or "pause between sentences"], 1): w4.cell(k, c, v).font = Font(name="Arial", size=10)
        wb.save(a.out); print(f"updated {a.out}")

# ---------------------------------------------------------------------------------
# 2. Similarity + monotonic DP alignment + windowed Case A
# ---------------------------------------------------------------------------------
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

def similarity(dub_texts, src_texts, models_dir):
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(MODEL, cache_folder=models_dir)
    a = m.encode(dub_texts, normalize_embeddings=True, show_progress_bar=False)
    b = m.encode(src_texts, normalize_embeddings=True, show_progress_bar=False)
    return (a @ b.T).tolist()

def align(sim, match_threshold=0.5, gap_penalty=0.1, skip_penalty=0.1):
    n = len(sim); m = len(sim[0]) if n else 0
    D = [[0.0] * (m + 1) for _ in range(n + 1)]; BT = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1): D[i][0] = D[i - 1][0] - gap_penalty; BT[i][0] = "G"
    for j in range(1, m + 1): D[0][j] = D[0][j - 1] - skip_penalty; BT[0][j] = "S"
    for i in range(1, n + 1):
        row = sim[i - 1]
        for j in range(1, m + 1):
            best, mv = D[i - 1][j - 1] + (row[j - 1] - match_threshold), "M"
            g = D[i - 1][j] - gap_penalty
            s = D[i][j - 1] - skip_penalty
            if g > best: best, mv = g, "G"
            if s > best: best, mv = s, "S"
            D[i][j] = best; BT[i][j] = mv
    path, i, j = [], n, m
    while i > 0 or j > 0:
        mv = BT[i][j]
        if mv == "M": path.append(("match", i - 1, j - 1)); i, j = i - 1, j - 1
        elif mv == "G": path.append(("gap", i - 1, None)); i -= 1
        else: path.append(("skip", None, j - 1)); j -= 1
    return path[::-1]

def auto_match(sim):
    """DP matches, then Case A for DP gaps: best source line at/after the matched position of the
    nearest DP-matched dub line at or before i-2 (near 1-1 window), accepted if score >= 0.5."""
    path = align(sim)
    auto = {i: dict(src=j, score=float(sim[i][j]), kind="DP match") for k, i, j in path if k == "match"}
    for k, i, _ in path:
        if k != "gap":
            continue
        lo = 0
        for p in range(i - 2, -1, -1):
            if p in auto and auto[p]["kind"] == "DP match":
                lo = auto[p]["src"]; break
        best = max(range(lo, len(sim[i])), key=lambda j: sim[i][j])
        if sim[i][best] >= 0.5:
            auto[i] = dict(src=best, score=float(sim[i][best]), kind="Case A reuse")
    return auto

# ---------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------
def fmt(ms):
    ms = int(round(ms)); return f"{ms // 60000:02d}:{(ms % 60000) / 1000:06.3f}"

def asr_dir(work, media):
    """Whisper cache folder of ONE media file: W/asr/<file name>/. (One folder per language mixed up a UK dub and its
    English source video in the same work folder: the source got the dub's words. TOLR DE-UK ep9, 2026-10-27.)"""
    return os.path.join(work, "asr", re.sub(r"[^\w.-]", "_", os.path.splitext(os.path.basename(media))[0])[:80])

def probe_duration_ms(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return int(round(float(out) * 1000))

def probe_video(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    w, h, r = out.strip().split(",")[:3]; a, b = r.split("/")
    return int(w), int(h), round(int(a) / int(b))

OUT_FPS = 25   # owner 2026-10-30: every output video is rendered at 25 fps, whatever the source's rate (TGS: 30)

def out_fps(cfg):
    """frame rate of every video this pipeline writes (config "out_fps" overrides; default OUT_FPS)"""
    return int(cfg.get("out_fps") or OUT_FPS)

def load(work, name):
    return json.load(open(os.path.join(work, name), encoding="utf-8"))

def save(work, name, obj):
    json.dump(obj, open(os.path.join(work, name), "w", encoding="utf-8"), indent=1, ensure_ascii=False)

def cfg_get(args):
    cfg = load(args.work, "config.json")
    for k, v in vars(args).items():
        if v is not None and k in cfg:
            cfg[k] = v
    return cfg

def active_manifest(work, cfg, name=None):
    """The edit list that render/verify/bookends use: manifest.json (one clip per row) or, after `groups`,
    manifest_groups.json (continuous 7-8-row groups). Returns (name, manifest dict)."""
    name = name or cfg.get("manifest", "manifest.json")
    if not os.path.exists(os.path.join(work, name)):
        raise SystemExit(f"{name} not found in {work} -- run `manifest` (and `groups`) first")
    return name, load(work, name)

def manifest_tag(name):
    """'manifest.json' -> '' ; 'manifest_groups.json' -> '_groups' (names the segment cache and default render)."""
    stem = os.path.splitext(os.path.basename(name))[0]
    return "" if stem == "manifest" else "_" + stem.replace("manifest_", "")

# ---------------------------------------------------------------------------------
# 3. analyze
# ---------------------------------------------------------------------------------
def cmd_analyze(a):
    os.makedirs(a.work, exist_ok=True)
    cfg = dict(dub_srt=a.dub_srt, dub_audio=a.dub_audio, src_srt=a.src_srt, src_video=a.src_video,
               models=a.models or os.path.expanduser("~/.cache/dubstitch_models"))
    for k in ("dub_srt", "dub_audio", "src_srt", "src_video"):
        if not cfg[k] or not os.path.exists(cfg[k]):
            raise SystemExit(f"--{k.replace('_', '-')} missing or not found: {cfg[k]}")
    save(a.work, "config.json", cfg)   # (re)starting: sheet/manifest/render/verify must be re-run after this
    for stale in ("rows.json", "manifest.json"):
        if os.path.exists(os.path.join(a.work, stale)): os.remove(os.path.join(a.work, stale))
    L = []; say = L.append
    src_off_ms = check_timing(cfg, a, say)
    save(a.work, "config.json", cfg)
    sides = {}
    for side, path in (("dub", cfg["dub_srt"]), ("src", cfg["src_srt"])):
        raw = shift_cues(parse_srt(path), src_off_ms if side == "src" else 0)
        res = resolve_overlaps(resegment_to_sentences(raw))
        kept, dropped = drop_single_word_zero_duration(res)
        final, retimed = retime_zero_duration(kept)
        wt = os.path.join(a.work, f"sentences_{side}.json")       # Step 1A: exact times from the audio, if made
        if os.path.exists(wt) and not a.no_word_times:
            WT = load(a.work, f"sentences_{side}.json")
            if os.path.abspath(WT["srt"]) == os.path.abspath(path) and not WT.get("first"):
                final = [Cue(i, r["start_ms"], r["end_ms"], r["text"]) for i, r in enumerate(WT["sentences"]) if r["end_ms"] > r["start_ms"]]
                dropped, retimed = [], []
                say(f"{side.upper()}: using word-timed sentences from {os.path.basename(wt)} ({len(final)} sentences, times from the audio)")
            else:
                say(f"{side.upper()}: {os.path.basename(wt)} is for another SRT or only a test range -- not used")
        sides[side] = final
        say(f"{side.upper()}: raw cues={len(raw)} (0ms in raw file={sum(c.start_ms == c.end_ms for c in raw)}) -> "
            f"sentences={len(res)} (0ms after overlap resolution={sum(c.start_ms == c.end_ms for c in res)}) -> "
            f"dropped single-word 0ms={len(dropped)} -> re-timed multi-word 0ms={len(retimed)} -> rows={len(final)}")
        for c in dropped: say(f"     DROPPED  {c.text!r}")
        for c in retimed: say(f"     RE-TIMED {c.text[:55]!r} -> {fmt(c.start_ms)}-{fmt(c.end_ms)}")
    dub, src = sides["dub"], sides["src"]
    sim = similarity([c.text for c in dub], [c.text for c in src], cfg["models"])
    auto = auto_match(sim)
    save(a.work, "prep.json", dict(dub=[asdict(c) for c in dub], src=[asdict(c) for c in src],
                                   sim=[[round(x, 5) for x in r] for r in sim],
                                   auto={str(k): v for k, v in auto.items()}))
    def srcline(j): return f"SRC#{j} [{fmt(src[j].start_ms)}] {src[j].text!r}"
    say(f"\n==== ALL {len(dub)} DUB ROWS (automatic result) ====")
    to_read = []
    for i, c in enumerate(dub):
        m = auto.get(i)
        say(f"DUB#{i:3d} [{fmt(c.start_ms)}-{fmt(c.end_ms)}] {c.text!r}")
        if m:
            say(f"        -> {srcline(m['src'])}  ({m['kind']} {m['score']:.3f})")
            if m["kind"] == "Case A reuse":
                to_read.append(i)
        else:
            top = sorted(range(len(src)), key=lambda j: -sim[i][j])[:3]
            say("        -> ** UNMATCHED **  top: " + " | ".join(f"SRC#{j} {sim[i][j]:.2f} {src[j].text[:40]!r}" for j in top))
            to_read.append(i)
    by = defaultdict(list)
    for i, m in auto.items(): by[m["src"]].append(i)
    n_dp = sum(m["kind"] == "DP match" for m in auto.values())
    say(f"\nDP matched {n_dp}/{len(dub)} dub rows ({100 * n_dp / len(dub):.0f}%)"
        + ("  ** VERY LOW -- check the two inputs are really the same episode **" if n_dp < 0.5 * len(dub) else ""))
    say("\n==== DUPLICATE CHECK: source lines claimed by more than one dub row ====")
    for j, owners in sorted(by.items()):
        if len(owners) > 1:
            say(srcline(j))
            for i in sorted(owners): say(f"     DUB#{i} {dub[i].text!r}  ({auto[i]['kind']} {auto[i]['score']:.3f})")
    say("\n==== ROWS YOU MUST READ (unmatched or Case A) with their neighbours ====")
    for i in to_read:
        say(f"-- DUB#{i} {dub[i].text!r}")
        for n in (i - 2, i - 1, i + 1, i + 2):
            if 0 <= n < len(dub):
                m = auto.get(n)
                say(f"     n{n - i:+d} DUB#{n} {dub[n].text[:60]!r} -> " + (srcline(m["src"]) if m else "(unmatched)"))
    say("\n==== DP MATCHES WORTH A SECOND LOOK (speed would be >1.6x slow or >2x fast, or a neighbouring source line is unused) ====")
    for i, m in sorted(auto.items()):
        if m["kind"] != "DP match":
            continue
        j = m["src"]; f = (dub[i].end_ms - dub[i].start_ms) / max(src[j].end_ms - src[j].start_ms, 1)
        free = [k for k in (j - 1, j + 1) if 0 <= k < len(src) and k not in by]
        if f > 1.6 or f < 0.5 or free:
            why = (f"speed {f:.2f}" if f > 1.6 or f < 0.5 else "") + (f" unused neighbour SRC#{free}" if free else "")
            say(f"   DUB#{i} {dub[i].text[:50]!r} -> SRC#{j} {src[j].text[:45]!r}  ({why.strip()})")
    say("\n==== source lines no dub row uses yet ====")
    for j in range(len(src)):
        if j not in by: say(srcline(j))
    say("\n==== possible repeated passages in the dub (>=4-word sentences, similarity >= 0.8) ====")
    norm = lambda t: re.sub(r"[^\w ]", "", t.lower()).strip()
    long_rows = [(i, c) for i, c in enumerate(dub) if len(c.text.split()) >= 4]
    for x in range(len(long_rows)):
        for y in range(x + 1, len(long_rows)):
            (i, ci), (k, ck) = long_rows[x], long_rows[y]
            na, nb = norm(ci.text), norm(ck.text)
            r = difflib.SequenceMatcher(None, na, nb).ratio()
            if r >= 0.8 or (len(na) > 25 and na in nb) or (len(nb) > 25 and nb in na):
                say(f"   DUB#{i} [{fmt(ci.start_ms)}] ~ DUB#{k} [{fmt(ck.start_ms)}]  ({r:.2f}) {ci.text[:60]!r}")
    say("\n==== SOURCE TIMELINE ====")
    for j, c in enumerate(src): say(f"SRC#{j:3d} [{fmt(c.start_ms)}-{fmt(c.end_ms)}] {c.text!r}")
    open(os.path.join(a.work, "analysis.txt"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    dpath = os.path.join(a.work, "decisions.json")
    if not os.path.exists(dpath):
        save(a.work, "decisions.json", dict(groups=[], assign={}, confirm={}, repeat={}, gap={}, unused_notes={}))
    print("\n".join(L[:40])); print(f"... full report: {os.path.join(a.work, 'analysis.txt')}")
    print(f"{len(to_read)} rows must be read by meaning (unmatched or Case A): {to_read}")

# ---------------------------------------------------------------------------------
# 4. sheet: apply decisions + integrity check + xlsx
# ---------------------------------------------------------------------------------
def build_rows(work, decisions_path):
    P = load(work, "prep.json")
    dub = [Cue(**c) for c in P["dub"]]; src = [Cue(**c) for c in P["src"]]
    sim = P["sim"]; auto = {int(k): v for k, v in P["auto"].items()}
    d = json.load(open(decisions_path, encoding="utf-8"))
    span = lambda v: (v, v) if isinstance(v, int) else (int(v[0]), int(v[1]))
    stext = lambda a, b: " ".join(src[j].text for j in range(a, b + 1))
    groups = d.get("groups", [])
    assign = {int(k): v for k, v in d.get("assign", {}).items()}
    confirm = {int(k): v for k, v in d.get("confirm", {}).items()}
    repeat = {int(k): int(v) for k, v in d.get("repeat", {}).items()}
    gap = {int(k): v for k, v in d.get("gap", {}).items()}
    errs = []
    for g in groups:   # guards against index drift: the text must be what was read
        a0 = span(g["src"])[0]
        if not src[a0].text.startswith(g["src_prefix"]): errs.append(f"group {g['members']}: SRC#{a0} is {src[a0].text!r}")
        for mbr, pre in zip(g["members"], g["dub_prefixes"]):
            if not dub[mbr].text.startswith(pre): errs.append(f"group member DUB#{mbr} is {dub[mbr].text!r}, not {pre!r}")
        if g["members"] != sorted(g["members"]): errs.append(f"group {g['members']} must be in dub order")
    for i, v in assign.items():
        if not src[span(v["src"])[0]].text.startswith(v["src_prefix"]): errs.append(f"assign DUB#{i}: src text mismatch")
        if not dub[i].text.startswith(v["dub_prefix"]): errs.append(f"assign DUB#{i}: dub text is {dub[i].text!r}")
    nrm = lambda t: re.sub(r"\s+", " ", re.sub(r"[^\w ]", "", t.lower())).strip()
    for i, s in repeat.items():
        if s >= i or nrm(dub[i].text) not in nrm(dub[s].text):
            errs.append(f"repeat DUB#{i} -> DUB#{s}: not an earlier row with the same text")
    group_of = {}
    for g in groups:
        mem = g["members"]; a0, b0 = span(g["src"])
        durs = [dub[m].end_ms - dub[m].start_ms for m in mem]
        tot = max(sum(durs), 1); cur = float(src[a0].start_ms); win = src[b0].end_ms - src[a0].start_ms
        for m, du in zip(mem, durs):
            s = cur; cur += win * du / tot
            group_of[m] = dict(members=mem, a=a0, b=b0, start=s, end=cur, reason=g["reason"])
        group_of[mem[-1]]["end"] = src[b0].end_ms
    rows = []
    for i, c in enumerate(dub):
        base = dict(idx=i, dub_start=c.start_ms, dub_end=c.end_ms, dub_text=c.text)
        if i in gap:
            best = max(range(len(src)), key=lambda j: sim[i][j])
            rows.append({**base, "src": None, "src_last": None, "src_start": None, "src_end": None, "src_text": None, "score": None,
                         "notes": f"NOT MATCHED -- genuine gap after the full n-1/n+1 check. Closest: SRC#{best} {src[best].text!r} "
                                  f"(score {sim[i][best]:.3f}) -- rejected: {gap[i]}", "flag": "red"})
        elif i in repeat:
            r0 = rows[repeat[i]]
            rows.append({**base, **{k: r0[k] for k in ("src", "src_last", "src_start", "src_end", "src_text")},
                         "score": round(sim[i][r0["src"]], 3) if r0["src"] is not None else None,
                         "notes": f"Dub repeat: the dub repeats DUB#{repeat[i]} here; the source has it once, so this row reuses that footage.",
                         "flag": "green"})
        elif i in group_of:
            g = group_of[i]
            rows.append({**base, "src": g["a"], "src_last": g["b"], "src_start": g["start"], "src_end": g["end"],
                         "src_text": stext(g["a"], g["b"]), "score": round(max(sim[i][j] for j in range(g["a"], g["b"] + 1)), 3),
                         "notes": f"Shared sentence ({len(g['members'])}-way) with DUB#{[m for m in g['members'] if m != i]}, "
                                  f"window split by dub duration. {g['reason']}", "flag": "green"})
        elif i in assign:
            a0, b0 = span(assign[i]["src"])
            rows.append({**base, "src": a0, "src_last": b0, "src_start": src[a0].start_ms, "src_end": src[b0].end_ms,
                         "src_text": stext(a0, b0), "score": round(max(sim[i][j] for j in range(a0, b0 + 1)), 3),
                         "notes": f"Manually verified match. {assign[i]['reason']}", "flag": "green"})
        elif i in auto:
            m = auto[i]; j = m["src"]
            if m["kind"] == "Case A reuse" and i not in confirm:
                errs.append(f"DUB#{i} is a Case A fallback ({m['score']:.3f} -> SRC#{j}): read it and put it in confirm/assign/groups/gap")
            note = m["kind"] + (f" (score {m['score']:.3f}) -- read and confirmed: {confirm[i]}" if i in confirm else "")
            rows.append({**base, "src": j, "src_last": j, "src_start": src[j].start_ms, "src_end": src[j].end_ms,
                         "src_text": src[j].text, "score": round(m["score"], 3), "notes": note, "flag": None})
        else:
            errs.append(f"DUB#{i} is unmatched and has no decision -- read its n-1/n+1 neighbours")
            rows.append({**base, "src": None, "src_last": None, "src_start": None, "src_end": None, "src_text": None,
                         "score": None, "notes": "", "flag": "red"})
    if errs:
        raise SystemExit("decisions incomplete or inconsistent:\n  " + "\n  ".join(errs))
    return rows, src, repeat, d

def mapping_checks(rows, repeat):
    """Integrity check: no 0ms window either side; no two rows share footage except documented dub repeats."""
    allowed = {frozenset((i, s)) for i, s in repeat.items()}
    zero = [r["idx"] for r in rows if r["dub_end"] <= r["dub_start"] or (r["src_start"] is not None and r["src_end"] <= r["src_start"])]
    m = [r for r in rows if r["src_start"] is not None]
    ov = []
    for x in range(len(m)):
        for y in range(x + 1, len(m)):
            o = min(m[x]["src_end"], m[y]["src_end"]) - max(m[x]["src_start"], m[y]["src_start"])
            if o > 1 and frozenset((m[x]["idx"], m[y]["idx"])) not in allowed:
                ov.append((m[x]["idx"], m[y]["idx"], round(o)))
    return zero, ov

def cmd_sheet(a):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    cfg = cfg_get(a)
    rows, src, repeat, d = build_rows(a.work, a.decisions or os.path.join(a.work, "decisions.json"))
    zero, ov = mapping_checks(rows, repeat)
    print(f"0ms rows: {zero}\nrow pairs sharing footage (outside documented repeats): {ov}")
    if zero or ov:
        raise SystemExit("integrity check failed -- fix decisions.json (usually a group missing a member, a group on the wrong sentence, or an unresolved duplicate claim)")
    used = set()
    for r in rows:
        if r["src"] is not None: used.update(range(r["src"], r["src_last"] + 1))
    unused = [j for j in range(len(src)) if j not in used]
    notes = {int(k): v for k, v in d.get("unused_notes", {}).items()}
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Mapping"
    hf = Font(name="Arial", size=11, bold=True, color="FFFFFF"); hfill = PatternFill("solid", start_color="1F4E78")
    bf = Font(name="Arial", size=10); wrap = Alignment(wrap_text=True, vertical="top")
    fills = {"green": PatternFill("solid", start_color="C6EFCE"), "yellow": PatternFill("solid", start_color="FFEB9C"),
             "red": PatternFill("solid", start_color="FFC7CE")}
    L = a.labels.split(",")
    hdr = ["#", f"{L[0]} Start", f"{L[0]} End", f"{L[0]} Text", f"{L[1]} Start", f"{L[1]} End", f"{L[1]} Text",
           "Match Score", "Stretch Factor", "Notes"]
    for ci, h in enumerate(hdr, 1):
        cell = ws.cell(1, ci, h); cell.font = hf; cell.fill = hfill
    for ri, r in enumerate(rows, 2):
        has = r["src_start"] is not None
        vals = [r["idx"], fmt(r["dub_start"]), fmt(r["dub_end"]), r["dub_text"],
                fmt(r["src_start"]) if has else "", fmt(r["src_end"]) if has else "", r["src_text"] or "",
                r["score"] if r["score"] is not None else "",
                round((r["dub_end"] - r["dub_start"]) / (r["src_end"] - r["src_start"]), 2) if has else "", r["notes"]]
        for ci, v in enumerate(vals, 1):
            cell = ws.cell(ri, ci, v); cell.font = bf; cell.alignment = wrap
            if r["flag"]: cell.fill = fills[r["flag"]]
    for ci, w in enumerate([6, 11, 11, 42, 11, 11, 42, 12, 13, 60], 1):
        ws.column_dimensions[chr(64 + ci)].width = w
    ws.freeze_panes = "A2"
    ws2 = wb.create_sheet(f"Unused {L[1]} lines")
    for ci, h in enumerate(["#", "Start", "End", f"{L[1]} Text", "Why unused"], 1):
        cell = ws2.cell(1, ci, h); cell.font = hf; cell.fill = hfill
    for ri, j in enumerate(unused, 2):
        for ci, v in enumerate([j, fmt(src[j].start_ms), fmt(src[j].end_ms), src[j].text, notes.get(j, "")], 1):
            cell = ws2.cell(ri, ci, v); cell.font = bf; cell.alignment = wrap
    for ci, w in enumerate([6, 11, 11, 60, 60], 1):
        ws2.column_dimensions[chr(64 + ci)].width = w
    # every row with no source sentence: the story was changed here (owner, 2026-10-29) -> its own tab, told to the owner
    nm = [r for r in rows if r["src_start"] is None]
    ws3 = wb.create_sheet(f"Not matched ({len(nm)})", 1)
    for ci, h in enumerate(["#", f"{L[0]} Start", f"{L[0]} End", f"{L[0]} Text", "Why (Step 2)"], 1):
        cell = ws3.cell(1, ci, h); cell.font = hf; cell.fill = hfill
    for ri, r in enumerate(nm, 2):
        for ci, v in enumerate([r["idx"], fmt(r["dub_start"]), fmt(r["dub_end"]), r["dub_text"], r["notes"]], 1):
            cell = ws3.cell(ri, ci, v); cell.font = bf; cell.alignment = wrap; cell.fill = fills["red"]
    for ci, w in enumerate([6, 11, 11, 70, 70], 1):
        ws3.column_dimensions[chr(64 + ci)].width = w
    out = a.out
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True); wb.save(out)
    save(a.work, "rows.json", dict(rows=rows, repeat={str(k): v for k, v in repeat.items()}))
    print(f"NOT MATCHED (no source sentence -- the story is changed here; covered with B-roll of the same place): "
          f"{[(r['idx'], r['dub_text'][:50]) for r in nm] or 'none'}")
    cfg["sheet_out"] = os.path.abspath(out); cfg["labels"] = a.labels; save(a.work, "config.json", cfg)
    n_gap = sum(r["flag"] == "red" for r in rows)
    print(f"saved {out}: rows={len(rows)} matched={len(rows) - n_gap} gaps={n_gap}")
    print(f"unused source lines: {[(j, src[j].text[:45]) for j in unused]}")
    missing = [j for j in unused if j not in notes]
    if missing: print(f"!! add an unused_notes entry explaining source lines {missing}")

# ---------------------------------------------------------------------------------
# 5. manifest: silence fill + absorb (zero black), bleed fix (fixed), gap fill; first clip starts at its own matched line
# ---------------------------------------------------------------------------------
SCENE_THRESHOLD = 0.15

def detect_cuts(video, start_ms, end_ms):
    if end_ms <= start_ms:
        return []
    r = subprocess.run(["ffmpeg", "-loglevel", "info", "-ss", f"{start_ms / 1000:.3f}", "-t", f"{(end_ms - start_ms) / 1000:.3f}",
                        "-i", video, "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"],
                       capture_output=True, text=True)
    cuts = [start_ms + float(x) * 1000 for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    # FIX (2026-09): showinfo can report frames past the window; only cuts inside it are valid.
    return sorted(c for c in cuts if start_ms <= c < end_ms)

def fix_stretch_bleed(clips, video, src_total_ms, max_stretch=1.6, review_cap=2.2):
    """Bleed fix: for clips slowed down more than max_stretch, trim an edge that crosses a scene cut
    (within min(1500ms, 35%) of that edge), then extend outward -- but only inside the SAME shot and
    never past the neighbouring clips' footage. Never keep a result worse than the original."""
    report = []
    for s in clips:
        dub_dur = s["it_end_ms"] - s["it_start_ms"]; src_dur = s["us_end_ms"] - s["us_start_ms"]
        factor = dub_dur / max(src_dur, 1)
        if factor <= max_stretch:
            continue
        others = [(c["us_start_ms"], c["us_end_ms"]) for c in clips if c is not s]
        prev_end = max([b for a_, b in others if b <= s["us_start_ms"]], default=0)
        next_start = min([a_ for a_, b in others if a_ >= s["us_end_ms"]], default=src_total_ms)
        cuts = detect_cuts(video, prev_end, next_start)
        o0, o1 = s["us_start_ms"], s["us_end_ms"]; margin = min(1500, 0.35 * (o1 - o0))
        n0, n1 = o0, o1
        ends = [c for c in cuts if o1 - margin <= c < o1]
        if ends: n1 = min(ends)
        starts = [c for c in cuts if o0 < c <= o0 + margin]
        if starts: n0 = max(starts)
        lo = max(max([c for c in cuts if c <= n0], default=prev_end), prev_end)
        hi = min(min([c for c in cuts if c >= n1], default=next_start), next_start)
        need = dub_dur / max_stretch - (n1 - n0)
        if need > 0:
            f = min(max(0, hi - n1), need); n1 += f; need -= f
            if need > 0:
                b = min(max(0, n0 - lo), need); n0 -= b
        nf_raw = dub_dur / max(n1 - n0, 1)
        moved = (n0, n1) != (o0, o1)
        n0, n1, nf = (o0, o1, factor) if nf_raw > factor else (n0, n1, nf_raw)
        s["us_start_ms"], s["us_end_ms"] = n0, n1
        review = nf > review_cap or (moved and nf_raw > factor)
        report.append(dict(idx=s["it_idx"], before=round(factor, 3), after=round(nf, 3), changed=(n0, n1) != (o0, o1),
                           needs_review=bool(review), text=s.get("it_text", "")[:60]))
    return report

def cmd_manifest(a):
    cfg = cfg_get(a)
    R = load(a.work, "rows.json"); rows = R["rows"]; repeat = {int(k): v for k, v in R["repeat"].items()}
    total_ms = probe_duration_ms(cfg["dub_audio"]); src_total = probe_duration_ms(cfg["src_video"])
    ev = []
    for r in rows:
        if r["src_start"] is None:
            continue  # genuine gaps become silence here and are absorbed below
        ev.append(dict(kind="clip", it_idx=r["idx"], it_start_ms=r["dub_start"], it_end_ms=r["dub_end"],
                       us_start_ms=r["src_start"], us_end_ms=r["src_end"], it_text=r["dub_text"], us_text=r["src_text"]))
    ev.sort(key=lambda e: e["it_start_ms"])
    full, cur = [], 0
    for e in ev:
        if e["it_start_ms"] < cur - 1: raise SystemExit(f"dub timeline overlap at DUB#{e['it_idx']}")
        if e["it_start_ms"] > cur: full.append(dict(kind="silence", it_start_ms=cur, it_end_ms=e["it_start_ms"]))
        full.append(e); cur = e["it_end_ms"]
    if cur < total_ms: full.append(dict(kind="silence", it_start_ms=cur, it_end_ms=total_ms))
    if cur > total_ms: raise SystemExit(f"dub SRT runs past the dub audio ({cur} > {total_ms} ms)")
    # Every silence is absorbed by the neighbouring clip(s) -> zero black screen.
    # (<=1.5s: whole sliver to the previous clip, else next; longer: split 50/50 between both.)
    for e in ev:
        e["orig"] = [e["it_start_ms"], e["it_end_ms"], e["us_start_ms"], e["us_end_ms"]]
    segs = []
    i = 0
    while i < len(full):
        s = full[i]
        if s["kind"] != "silence":
            segs.append(s); i += 1; continue
        gap = s["it_end_ms"] - s["it_start_ms"]
        prev = segs[-1] if segs and segs[-1]["kind"] == "clip" else None
        nxt = full[i + 1] if i + 1 < len(full) and full[i + 1]["kind"] == "clip" else None
        if prev and nxt and gap > 1500:
            half = gap // 2; prev["it_end_ms"] += half; nxt["it_start_ms"] -= gap - half
        elif prev:
            prev["it_end_ms"] = s["it_end_ms"]
        elif nxt:
            nxt["it_start_ms"] = s["it_start_ms"]
        else:
            raise SystemExit("no clip at all to absorb silence")
        i += 1
    for x, y in zip(segs, segs[1:]):
        assert x["it_end_ms"] == y["it_start_ms"], "timeline not contiguous"
    assert segs[0]["it_start_ms"] == 0 and segs[-1]["it_end_ms"] == total_ms
    rep = fix_stretch_bleed(segs, cfg["src_video"], src_total)
    # NO first-clip rule: the first clip starts at its own matched source line, like every other clip. It absorbs the
    # dub's leading silence by slowing down (bleed fix keeps it inside its own shot). Reaching back to source 0 pulled in
    # an unmatched extra scene (ep81/82), so it was removed (2026-10).
    # Gap fill: two clips that follow each other in the source with a small unused gap between them (<= --fill-gaps,
    # default 1 s) are joined, so the picture runs on continuously instead of jumping over a fraction of a second.
    # The gap is split at a scene cut inside it if there is one (each clip keeps its own shot), else at the midpoint.
    # Both clips play slightly faster. Skipped if any other clip uses that footage, or for documented dub repeats.
    filled = []
    if a.fill_gaps > 0:
        rep_rows = set(repeat) | set(repeat.values())
        for x, y in zip(segs, segs[1:]):
            g0, g1 = x["us_end_ms"], y["us_start_ms"]
            if not (0 < g1 - g0 <= a.fill_gaps) or x["it_idx"] in rep_rows or y["it_idx"] in rep_rows:
                continue
            if any(c is not x and c is not y and c["us_start_ms"] < g1 and c["us_end_ms"] > g0 for c in segs):
                continue
            cuts = detect_cuts(cfg["src_video"], g0, g1)
            m = cuts[0] if cuts else (g0 + g1) / 2
            fx = (x["it_end_ms"] - x["it_start_ms"]) / (x["us_end_ms"] - x["us_start_ms"])
            fy = (y["it_end_ms"] - y["it_start_ms"]) / (y["us_end_ms"] - y["us_start_ms"])
            x["us_end_ms"] = m; y["us_start_ms"] = m; x["gapfill"] = y["gapfill"] = True
            filled.append((x["it_idx"], y["it_idx"], round(g1 - g0), "cut" if cuts else "mid", round(fx, 3), round(fy, 3)))
    # final checks
    allowed = {frozenset((i, s)) for i, s in repeat.items()}
    ov = [(x["it_idx"], y["it_idx"]) for k, x in enumerate(segs) for y in segs[k + 1:]
          if min(x["us_end_ms"], y["us_end_ms"]) - max(x["us_start_ms"], y["us_start_ms"]) > 1
          and frozenset((x["it_idx"], y["it_idx"])) not in allowed]
    if ov: raise SystemExit(f"clips share footage after the bleed fix: {ov}")
    for s in segs:
        s["stretch_factor"] = round((s["it_end_ms"] - s["it_start_ms"]) / (s["us_end_ms"] - s["us_start_ms"]), 3)
    save(a.work, "manifest.json", dict(total_ms=total_ms, segments=segs, bleed_report=rep))
    cfg["manifest"] = "manifest.json"; save(a.work, "config.json", cfg)   # `groups` switches this to manifest_groups.json
    if cfg.get("sheet_out") and os.path.exists(cfg["sheet_out"]):
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = openpyxl.load_workbook(cfg["sheet_out"])
        if "Video edit list" in wb.sheetnames: del wb["Video edit list"]
        ws = wb.create_sheet("Video edit list")
        L = cfg.get("labels", "IT,US").split(",")
        hdr = ["#", f"{L[0]} Start", f"{L[0]} End", f"{L[1]} Start", f"{L[1]} End", "Speed factor", "Changed from Mapping tab by"]
        hf = Font(name="Arial", size=11, bold=True, color="FFFFFF"); hfill = PatternFill("solid", start_color="1F4E78")
        for ci, h in enumerate(hdr, 1):
            c_ = ws.cell(1, ci, h); c_.font = hf; c_.fill = hfill
        fixed = {r["idx"] for r in rep if r["changed"]}
        for ri, sg in enumerate(segs, 2):
            o = sg["orig"]; why = []
            if [sg["it_start_ms"], sg["it_end_ms"]] != o[:2]: why.append("absorbed neighbouring silence (no black screen)")
            if sg["it_idx"] in fixed: why.append("bleed fix (kept inside its own shot)")
            if sg.get("gapfill"): why.append("joined to the neighbouring clip (small source gap filled)")
            vals = [sg["it_idx"], fmt(sg["it_start_ms"]), fmt(sg["it_end_ms"]), fmt(sg["us_start_ms"]), fmt(sg["us_end_ms"]),
                    sg["stretch_factor"], "; ".join(why)]
            for ci, v in enumerate(vals, 1):
                c_ = ws.cell(ri, ci, v); c_.font = Font(name="Arial", size=10); c_.alignment = Alignment(wrap_text=True, vertical="top")
        for ci, w in enumerate([6, 11, 11, 11, 11, 12, 60], 1):
            ws.column_dimensions[chr(64 + ci)].width = w
        wb.save(cfg["sheet_out"]); print(f"added 'Video edit list' tab (final timings as played) to {cfg['sheet_out']}")
    print(f"manifest: {len(segs)} clips covering 0-{total_ms / 1000:.2f}s, 0 black, 0 shared footage")
    for r in rep:
        print(f"  bleed fix DUB#{r['idx']}: {r['before']}x -> {r['after']}x" + ("  NEEDS VISUAL REVIEW" if r["needs_review"] else ""))
    if filled:
        sf = {s["it_idx"]: s["stretch_factor"] for s in segs}
        print(f"gap fill: joined {len(filled)} pairs of clips across source gaps <= {a.fill_gaps / 1000:g}s "
              f"({sum(f[2] for f in filled) / 1000:.1f}s of extra footage used):")
        for xi, yi, g, how, fx, fy in filled:
            print(f"  DUB#{xi}|DUB#{yi}: gap {g}ms split at {'a scene cut' if how == 'cut' else 'the midpoint'}; "
                  f"stretch {fx}->{sf[xi]} and {fy}->{sf[yi]}")
    slow = [(s["it_idx"], s["stretch_factor"]) for s in segs if s["stretch_factor"] > 1.6]
    fast = [(s["it_idx"], s["stretch_factor"]) for s in segs if s["stretch_factor"] < 0.5]
    print(f"slow-motion clips (>1.6x slower): {slow}\nfast clips (>2x faster): {fast}")

# ---------------------------------------------------------------------------------
# 6. render: frame-exact on a cumulative grid (no drift), resumable
# ---------------------------------------------------------------------------------
def detect_subtitle_band(video, sample_every_s=2, white_thresh=200, width_frac=0.5):
    import numpy as np
    from PIL import Image
    w, h, _ = probe_video(video)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", f"fps=1/{sample_every_s},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w)
    vals = []
    for im in frames:
        rows = np.where((im[int(h * 0.4):] > white_thresh).sum(axis=1) > w * width_frac)[0]
        if len(rows): vals.append(int(h * 0.4) + int(rows[0]))
    if len(vals) < 10: return None
    arr = np.array(vals); close = arr[np.abs(arr - np.median(arr)) < 40]
    if len(close) < 5 or close.std() > 15: return None
    return max(int(close.min()) - 30, 0)

def seg_sig(s, nf, crop_h, W, H, FPS):
    """A rendered segment's cache key. A segment may name its own video ("video": an imported clip from the editor);
    the source video's segments keep the old key, so their cache stays valid."""
    sig = f"{s['us_start_ms']:.3f}|{s['us_end_ms']:.3f}|{nf}|{crop_h}|{W}x{H}@{FPS}"
    if s.get("zoom"): sig += f"|z{s['zoom']}"          # reused footage shown zoomed in (slow-clip B-roll)
    return sig + f"|{s['video']}" if s.get("video") else sig


def cmd_render(a):
    cfg = cfg_get(a)
    mname, M = active_manifest(a.work, cfg, a.manifest); segs = M["segments"]; tag = manifest_tag(mname)
    a.out = a.out or os.path.join(a.work, f"video{tag}_nosubs.mp4")
    print(f"rendering {mname}: {len(segs)} segments")
    W, H, _src_fps = probe_video(cfg["src_video"]); FPS = out_fps(cfg)
    crop_h = None
    if a.subs == "crop":
        cache = os.path.join(a.work, "crop.json")
        crop_h = load(a.work, "crop.json")["crop_h"] if os.path.exists(cache) else detect_subtitle_band(cfg["src_video"])
        save(a.work, "crop.json", dict(crop_h=crop_h)); print(f"subtitle crop: {crop_h}")
    d = os.path.join(a.work, "segs" + tag); os.makedirs(d, exist_ok=True)
    t_end = time.time() + a.max_seconds
    def job(i):
        s = segs[i]; out = os.path.join(d, f"{i:04d}.mp4")
        f0 = round(s["it_start_ms"] * FPS / 1000); f1 = round(s["it_end_ms"] * FPS / 1000); nf = max(f1 - f0, 1)
        sig = seg_sig(s, nf, crop_h, W, H, FPS)
        if (os.path.exists(out + ".ok") and open(out + ".ok").read() == sig) or time.time() > t_end:
            return
        k = (nf / FPS) / max((s["us_end_ms"] - s["us_start_ms"]) / 1000, 0.04)
        frame = (f"crop={W}:{crop_h}:0:0,pad={W}:{H}:0:0:black" if crop_h and not s.get("video") else
                 f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2")
        if s.get("zoom"):                               # a segment may ask for a centred zoom-in (reused B-roll)
            # anchored at the bottom: burned-in source captions stay where the subtitle strip covers them
            z = float(s["zoom"]); frame = f"crop=iw/{z:.4f}:ih/{z:.4f}:(iw-iw/{z:.4f})/2:ih-ih/{z:.4f},{frame}"
        vf = f"setpts={k:.6f}*PTS,{frame},fps={FPS},tpad=stop_mode=clone:stop_duration=2"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{s['us_start_ms'] / 1000:.3f}",
                            "-to", f"{s['us_end_ms'] / 1000:.3f}", "-i", s.get("video") or cfg["src_video"], "-vf", vf, "-frames:v", str(nf),
                            "-r", str(FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-an", "-movflags", "+faststart", out], capture_output=True, text=True)
        if r.returncode: print(f"FAILED segment {i}: {r.stderr[-300:]}")
        else: open(out + ".ok", "w").write(sig)
    with ThreadPoolExecutor(a.workers) as ex: list(ex.map(job, range(len(segs))))
    def is_done(i):
        s = segs[i]; ok = os.path.join(d, f"{i:04d}.mp4.ok")
        nf = max(round(s["it_end_ms"] * FPS / 1000) - round(s["it_start_ms"] * FPS / 1000), 1)
        return os.path.exists(ok) and open(ok).read() == seg_sig(s, nf, crop_h, W, H, FPS)
    done = sum(is_done(i) for i in range(len(segs)))
    print(f"rendered {done}/{len(segs)} segments")
    if done < len(segs):
        print("not finished -- run the same render command again (it resumes)"); sys.exit(3)
    lst = os.path.join(a.work, f"concat{tag}.txt")
    open(lst, "w").write("".join(f"file '{os.path.abspath(os.path.join(d, f'{i:04d}.mp4'))}'\n" for i in range(len(segs))))
    silent = os.path.join(a.work, f"silent{tag}.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent], check=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", silent, "-i", cfg["dub_audio"], "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", a.out], check=True)
    cfg["video_out"] = os.path.abspath(a.out); cfg["video_manifest"] = mname; save(a.work, "config.json", cfg)
    print(f"saved {a.out}: video {probe_duration_ms(silent) / 1000:.3f}s, dub audio {probe_duration_ms(cfg['dub_audio']) / 1000:.3f}s")

# ---------------------------------------------------------------------------------
# 7. verify: every row shows its own footage; durations match; QC contact sheet
# ---------------------------------------------------------------------------------
def cmd_verify(a):
    import numpy as np
    from PIL import Image, ImageDraw
    cfg = load(a.work, "config.json"); final = cfg.get("video_out")
    if not final or not os.path.exists(final):
        raise SystemExit("no rendered video recorded in this work folder -- run `render` first")
    mname, M = active_manifest(a.work, cfg, cfg.get("video_manifest")); segs = M["segments"]
    print(f"checking {final} against {mname} ({len(segs)} segments)")
    FPS = out_fps(cfg)
    vd, ad = probe_duration_ms(final), probe_duration_ms(cfg["dub_audio"])
    print(f"final video {vd / 1000:.3f}s vs dub audio {ad / 1000:.3f}s (diff {abs(vd - ad)}ms)")
    ok_dur = abs(vd - ad) <= 2 * 1000 / FPS + 50
    def grab(path, t, zoom=None):
        z_ = float(zoom or 1)                                     # a zoomed-in segment: compare zoomed (bottom-anchored)
        zf = f"crop=iw/{z_:.4f}:ih/{z_:.4f}:(iw-iw/{z_:.4f})/2:ih-ih/{z_:.4f}," if zoom else ""
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", path, "-frames:v", "1",
                              "-vf", zf + "scale=64:64,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).astype(float) if len(raw) == 4096 else None
    def corr(x, y):
        return float(np.corrcoef(x, y)[0, 1]) if x is not None and y is not None and x.std() > 0 and y.std() > 0 else 1.0
    def check(s):
        f0 = round(s["it_start_ms"] * FPS / 1000); f1 = max(round(s["it_end_ms"] * FPS / 1000), f0 + 1)   # a piece under one frame
        t = (f0 + (f1 - f0) // 2 + 0.5) / FPS
        ts = (s["us_start_ms"] + (t - f0 / FPS) / ((f1 - f0) / FPS) * (s["us_end_ms"] - s["us_start_ms"])) / 1000
        v = s.get("video") or cfg["src_video"]                  # an imported clip is checked against its own file
        z = s.get("zoom"); x = grab(final, t); c = corr(x, grab(v, ts, z))
        if c < 0.9:
            c = max(corr(x, grab(v, ts + k / FPS, z)) for k in range(-6, 7))
        return s["it_idx"], round(t, 2), round(c, 3)
    with ThreadPoolExecutor(a.workers) as ex: res = list(ex.map(check, segs))
    bad = [r for r in res if r[2] < 0.9]
    print(f"sync check: {len(res)} rows, {len(res) - len(bad)} show their own footage (best corr >= 0.90); failing: {bad}")
    fixed = {r["idx"] for r in M.get("bleed_report", []) if r["changed"] or r["needs_review"]}
    pick = [segs[0]] + [s for s in segs[1:] if s["it_idx"] in fixed or s["stretch_factor"] > 1.6 or s["stretch_factor"] < 0.5][:15]
    sheet = Image.new("RGB", (4 * 220, len(pick) * 250), "white"); dr = ImageDraw.Draw(sheet)
    from PIL import ImageFont
    font = None
    for fp in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
               "/Library/Fonts/Arial.ttf", "C:/Windows/Fonts/arial.ttf"):
        if os.path.exists(fp): font = ImageFont.truetype(fp, 13); break
    for r_, s in enumerate(pick):
        t0, t1 = s["it_start_ms"] / 1000, s["it_end_ms"] / 1000
        dr.text((4, r_ * 250 + 2), f"#{s['it_idx']} {t0:.1f}-{t1:.1f}s factor {s['stretch_factor']}  {s.get('it_text', '')[:60]}", fill="black", font=font)
        for k, p in enumerate((0.08, 0.36, 0.64, 0.92)):
            png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0 + p * (t1 - t0):.3f}", "-i", final, "-frames:v", "1",
                                  "-vf", "scale=216:216", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
            if png: sheet.paste(Image.open(io.BytesIO(png)), (k * 220 + 2, r_ * 250 + 22))
    qc = os.path.join(a.work, "qc_sheet.jpg"); sheet.save(qc, quality=80)
    print(f"QC contact sheet (first clip + bleed-fixed + slow/fast clips): {qc} -- LOOK at it")
    if bad or not ok_dur: sys.exit(1)

# ---------------------------------------------------------------------------------
# 8. subs: burn the dub subtitles onto the rendered video -> the final deliverable
#    White full-width strip over the source's caption area + dub SRT text (Poppins Bold 40,
#    black, centred). Every card at its SRT times, first card from frame 0, text held across short
#    pauses, and an empty strip wherever the render would still show a source caption.
# ---------------------------------------------------------------------------------
FONT_CANDIDATES = ["/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf", "/usr/share/fonts/truetype/poppins/Poppins-Bold.ttf",
                   os.path.expanduser("~/Library/Fonts/Poppins-Bold.ttf"), "/Library/Fonts/Poppins-Bold.ttf",
                   os.path.expanduser("~/.local/share/fonts/Poppins-Bold.ttf"), os.path.expanduser("~/.fonts/Poppins-Bold.ttf"),
                   "C:/Windows/Fonts/Poppins-Bold.ttf"]

def find_font(path, size):
    from PIL import ImageFont
    cands = [path] if path else list(FONT_CANDIDATES)
    if not path and shutil.which("fc-match"):
        cands.append(subprocess.run(["fc-match", "-f", "%{file}", "Poppins:bold"], capture_output=True, text=True).stdout.strip())
    for p in cands:
        if p and os.path.exists(p):
            f = ImageFont.truetype(p, size)
            if path or f.getname() == ("Poppins", "Bold"):
                return p
    raise SystemExit("Poppins Bold not found. Download https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf "
                     "and pass --font /path/to/Poppins-Bold.ttf")

def detect_caption_box(video):
    """Median top/bottom row of the source's burned-in white caption box, or None if it has none."""
    import numpy as np
    W, H, _ = probe_video(video); h2 = H // 2
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", video, "-vf", f"fps=1/2,crop={W}:{H - h2}:0:{h2}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    n = W * (H - h2) * 3; tops, bots = [], []
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        m = (np.frombuffer(buf, np.uint8).reshape(H - h2, W, 3) > 225).all(2)
        rows = np.where(m.sum(1) > W * 0.11)[0]
        if len(rows) < 10:
            continue
        run = max(np.split(rows, np.where(np.diff(rows) > 3)[0] + 1), key=len)
        if len(run) >= 10:
            tops.append(h2 + int(run[0])); bots.append(h2 + int(run[-1]))
    p.wait()
    if len(tops) < 10:
        return None
    box = (int(np.median(tops)), int(np.median(bots)))
    # Burned-in captions sit in the bottom part of the frame. A "box" higher up is bright picture content (a white
    # shirt, a window; seen on TOLR ep24, which has no captions at all): treat it as no caption box.
    return box if box[0] >= 0.70 * H else None

def layout_text(text, font_path, size, maxw):
    """One line if it fits; else the most balanced 2-line split (never one word alone); if even that is
    too wide, the font is reduced 2 px at a time (reported)."""
    from PIL import ImageFont
    words = text.split()
    for s in range(size, 19, -2):
        f = ImageFont.truetype(font_path, s)
        if f.getlength(text) <= maxw or len(words) < 2:
            return f, [text], s
        k = min(range(1, len(words)), key=lambda k: max(f.getlength(" ".join(words[:k])), f.getlength(" ".join(words[k:]))))
        lines = [" ".join(words[:k]), " ".join(words[k:])]
        if max(f.getlength(l) for l in lines) <= maxw:
            return f, lines, s
    return f, lines, s

def caption_frames(video, box, W, FPS):
    """Per frame: does the (render's) picture show the source's burned-in caption box? (white rows in the box band)"""
    import numpy as np
    t, b = box; h = b - t + 1; sh = max(h // 2, 8)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", f"crop={W}:{h}:0:{t},scale=540:{sh},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, sh, 540)
    return ((fr > 235).sum(2) > 540 * 0.11).sum(1) >= 0.3 * sh

def cmd_subs(a):
    import numpy as np
    from PIL import Image, ImageDraw
    cfg = load(a.work, "config.json")
    clean = cfg.get("clean_final") if cfg.get("clean_final") and os.path.exists(cfg["clean_final"]) else None
    video = a.video or clean or cfg.get("video_out")      # default: the clean master from `bookends`
    if not video or not os.path.exists(video):
        raise SystemExit("no video to subtitle -- run `render`, `verify` and `bookends` first (or pass --video)")
    if os.path.abspath(a.out) in {os.path.abspath(p) for p in (video, cfg.get("video_out") or "", clean or "")}:
        raise SystemExit("--out must be a new file, not the clean master or the render")
    srt = a.srt or cfg["dub_srt"]
    W, H, FPS = probe_video(video); total_f = round(probe_duration_ms(video) * FPS / 1000)
    # On the clean master the dub episode starts after the source opening: shift every card by it, and keep the
    # opening and ending free of any strip (they have no dub audio and no subtitles).
    on_master = clean is not None and os.path.abspath(video) == os.path.abspath(clean)
    off = a.offset if a.offset is not None else (cfg.get("opening_s", 0) if on_master else 0)
    if a.style == "box":   # every show except TDMB: transparent full-frame subtitle PNGs (pngsubs.py), see Step 8
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import pngsubs
        wd = os.path.join(a.work, "pngsubs_" + re.sub(r"[^\w.-]", "_", os.path.splitext(os.path.basename(a.out))[0]))
        pngsubs.burn(video, srt, a.out, wd, off, a.chunk, min(a.max_seconds, 10 ** 6), a.font, keep_parts=a.keep_parts,
                     size=a.box_size, bottom=a.box_bottom, box_alpha=a.box_alpha)
        cfg = load(a.work, "config.json")
        cfg.setdefault("subs_outputs", {})[os.path.abspath(a.out)] = dict(srt=os.path.abspath(srt), video=os.path.abspath(video),
                                                                          offset_s=off, style="box")
        save(a.work, "config.json", cfg); return
    if a.style == "dynamic":   # HIH: strip height follows the source caption under it (2 or 3 lines); see dynsubs.py
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import dynsubs
        if not os.path.exists(os.path.join(a.work, "dynsubs_plan.json")):
            raise SystemExit("subs --style dynamic needs `captions` and `dynsrt` first (W/dynsubs_plan.json)")
        if not a.srt and cfg.get("dynamic_srt"): srt = cfg["dynamic_srt"]
        cues = [(c.start_ms, c.end_ms, c.text) for c in parse_srt(srt)]
        print(f"subtitles: {os.path.basename(srt)} ({len(cues)} cards) on {os.path.basename(video)}, shifted {off:+.3f}s; dynamic strip")
        ok, rep = dynsubs.burn(a.work, video, cues, a.out, off, cfg, find_font(a.font, a.size), workers=min(a.workers, 2),
                               max_seconds=min(a.max_seconds, 10 ** 6), chunk=a.chunk, min_gap=a.min_gap)
        if rep is None:
            print("not finished -- run the same subs command again (it resumes)"); sys.exit(3)
        cfg = load(a.work, "config.json")
        cfg.setdefault("subs_outputs", {})[os.path.abspath(a.out)] = dict(srt=os.path.abspath(srt), video=os.path.abspath(video),
                                                                          offset_s=off, style="dynamic", report=rep)
        save(a.work, "config.json", cfg)
        if not ok: sys.exit(1)
        return
    win0 = round(cfg.get("opening_s", 0) * FPS) if on_master else 0
    win1 = total_f - (round(cfg.get("ending_s", 0) * FPS) if on_master else 0)
    base_size = a.size; a.size = round(base_size * H / 1080)   # --size is for a 1080-px frame; scale to this video
    if W * H > 1920 * 1080 * 0.9 and a.workers > 2:
        a.workers = 2                                           # 1440x1440 chunks were OOM-killed (4 GB machine) with 3+ workers
    font_path = find_font(a.font, a.size)
    cache = os.path.join(a.work, "caption_box.json")
    if os.path.exists(cache) and load(a.work, "caption_box.json").get("video") == cfg["src_video"]:
        cb = load(a.work, "caption_box.json")["box"]
    else:
        cb = detect_caption_box(cfg["src_video"]); save(a.work, "caption_box.json", dict(video=cfg["src_video"], box=cb))
    if a.box:
        y0, y1 = map(int, a.box.split(",")); how = "given with --box"
    else:
        y0, y1 = (cb[0] - 12, cb[1] + 12) if cb else (round(H * 0.79), round(H * 0.93))
        how = f"covers the source caption box (rows {cb[0]}-{cb[1]}) + 12 px" if cb else "no source caption box found: default band"
    need = 2 * int(a.size * 1.25) + 20
    if y1 - y0 < need:
        c = (y0 + y1) // 2; y0, y1 = c - need // 2, c + need - need // 2
    y0, y1 = max(y0, 0), min(y1, H - 1)
    print(f"strip: rows {y0}-{y1}, full width ({how}); font {font_path} size {a.size} px "
          f"(= {base_size} on a 1080-px frame, video is {W}x{H}); {a.workers} workers")
    cues = parse_srt(srt)   # the SRT as delivered (dub, or its translation): every card, in order
    print(f"subtitles: {os.path.basename(srt)} ({len(cues)} cards) on {os.path.basename(video)}, shifted {off:+.3f}s; "
          f"strip allowed {win0 / FPS:.2f}-{win1 / FPS:.2f}s")
    d = os.path.join(a.work, "subs_" + re.sub(r"[^\w.-]", "_", os.path.splitext(os.path.basename(a.out))[0]))
    os.makedirs(d, exist_ok=True)   # one folder per output, so an Italian and an English burn never share card images
    blank = os.path.join(d, "blank.png"); Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    strip_only = os.path.join(d, "strip.png"); im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle([0, y0, W - 1, y1], fill=(255, 255, 255, 255)); im.save(strip_only)
    cards, small = [], []
    for i, c in enumerate(cues):
        f, lines, s = layout_text(c.text, font_path, a.size, W - round(80 * H / 1080))
        if s != a.size: small.append((i, s, c.text))
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
        dr.rectangle([0, y0, W - 1, y1], fill=(255, 255, 255, 255))
        lh = int(s * 1.25); cy = (y0 + y1) / 2 - (len(lines) - 1) * lh / 2
        for k, l in enumerate(lines):
            dr.text((W / 2, cy + k * lh), l, font=f, fill=(0, 0, 0, 255), anchor="mm")
        p = os.path.join(d, f"c{i:04d}.png"); im.save(p)
        cards.append((c.start_ms / 1000 + off, c.end_ms / 1000 + off, p, len(lines)))
    # ---- frame-by-frame plan: state = card index (text), -1 = empty strip, -2 = no strip ----
    n = total_f; state = np.full(n, -2, int)
    span = [(min(max(math.ceil(s * FPS), win0), win1), min(max(math.ceil(e * FPS), win0), win1)) for s, e, _, _ in cards]
    for i, (fa, fb) in enumerate(span):
        state[fa:fb] = i                                   # 1. every card at its exact SRT times
    held = 0
    for i in range(len(span) - 1):                         # 2. continuity: hold the text across short pauses
        fb, na = span[i][1], span[i + 1][0]
        if 0 < na - fb < a.min_gap * FPS:
            state[fb:na] = i; held += 1
    lead = span[0][0] - win0 if span else 0; lead_used = tail_used = False
    if span and 0 < lead <= a.max_lead * FPS:              # 3. first card shown from the first dub frame
        state[win0:span[0][0]] = 0; lead_used = True
    tail = win1 - span[-1][1] if span else 0
    if span and 0 < tail <= a.max_lead * FPS:              # 4. last card held to the last dub frame
        state[span[-1][1]:win1] = len(cards) - 1; tail_used = True
    cap = None; dyn = 0
    if cb and not a.no_cover:                              # 5. anywhere the render still shows a source caption
        vst = os.stat(video); cpath = os.path.join(a.work, "caption_frames.npy")   # cached: the scan is slow and resumes re-run it
        ckey = f"{vst.st_size}|{int(vst.st_mtime)}|{cb}"
        if os.path.exists(cpath) and os.path.exists(cpath + ".key") and open(cpath + ".key").read() == ckey:
            cap = np.load(cpath)
        else:
            cap = caption_frames(video, cb, W, FPS); np.save(cpath, cap); open(cpath + ".key", "w").write(ckey)
        cap = cap[:n]                                      #    without our strip, add an EMPTY strip (dynamic)
        cap = np.pad(cap, (0, n - len(cap)))
        grow = np.convolve(cap.astype(int), np.ones(2 * 2 + 1, int), "same") > 0   # +-2 frames margin
        add = grow & (state == -2); dyn = int(add.sum()); state[add] = -1
    on = state != -2                                       # 6. no blinking: close off-gaps shorter than 0.5 s
    k = 0
    while k < n:
        if on[k]: k += 1; continue
        j = k
        while j < n and not on[j]: j += 1
        if k > 0 and j < n and (j - k) < 0.5 * FPS: state[k:j] = -1
        k = j
    png_of = lambda v: cards[v][2] if v >= 0 else (strip_only if v == -1 else blank)
    st = os.stat(video)
    base_sig = hashlib.md5(json.dumps([state.tolist(), [[c.start_ms, c.end_ms, c.text] for c in cues], font_path, a.size, y0, y1,
                                       st.st_size, int(st.st_mtime), FPS]).encode()).hexdigest()
    cf = round(a.chunk * FPS); chunks = [(k * cf, min((k + 1) * cf, n)) for k in range(math.ceil(n / cf))]
    t_stop = time.time() + a.max_seconds
    def job(k):
        f0, f1 = chunks[k]; out = os.path.join(d, f"part{k:03d}.mp4"); sig = f"{base_sig}|{f0}|{f1}"
        if (os.path.exists(out + ".ok") and open(out + ".ok").read() == sig) or time.time() > t_stop:
            return
        items, x = [], f0
        while x < f1:
            y = x
            while y < f1 and state[y] == state[x]: y += 1
            items.append((png_of(state[x]), (y - x) / FPS)); x = y
        lst = out + ".txt"
        with open(lst, "w") as fh:
            fh.write("ffconcat version 1.0\n")
            for p, du in items: fh.write(f"file '{os.path.abspath(p)}'\nduration {du:.6f}\n")
            fh.write(f"file '{os.path.abspath(items[-1][0])}'\n")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{f0 / FPS:.6f}", "-i", video, "-f", "concat", "-safe", "0", "-i", lst,
                            "-filter_complex", f"[1:v]fps={FPS}[o];[0:v][o]overlay=0:0:eof_action=repeat,format=yuv420p[v]",
                            "-map", "[v]", "-frames:v", str(f1 - f0), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "18", "-an", out], capture_output=True, text=True)
        if r.returncode: print(f"FAILED chunk {k} (exit {r.returncode}): {r.stderr[-300:]}")
        else: open(out + ".ok", "w").write(sig)
    with ThreadPoolExecutor(a.workers) as ex: list(ex.map(job, range(len(chunks))))
    done = sum(os.path.exists(os.path.join(d, f"part{k:03d}.mp4.ok")) and
               open(os.path.join(d, f"part{k:03d}.mp4.ok")).read() == f"{base_sig}|{chunks[k][0]}|{chunks[k][1]}" for k in range(len(chunks)))
    print(f"subtitled {done}/{len(chunks)} chunks")
    if done < len(chunks):
        print("not finished -- run the same subs command again (it resumes)"); sys.exit(3)
    lst = os.path.join(d, "parts.txt"); silent = os.path.join(d, "silent.mp4")
    open(lst, "w").write("".join(f"file '{os.path.abspath(os.path.join(d, f'part{k:03d}.mp4'))}'\n" for k in range(len(chunks))))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent], check=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", silent, "-i", video, "-map", "0:v", "-map", "1:a", "-c", "copy",
                    "-movflags", "+faststart", a.out], check=True)
    # ---- self-check on the saved file ----
    def strip(path):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"crop={W}:{y1 - y0 + 1}:0:{y0},scale=108:16,format=gray",
                              "-f", "rawvideo", "-"], capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(-1, 16, 108).astype(np.int16)
    A, B = strip(a.out), strip(video); m = min(len(A), len(B), n)
    shown = np.concatenate([A[:m, 2:14, :4], A[:m, 2:14, -4:]], axis=2).reshape(m, -1).min(1) >= 245
    exp = state[:m] != -2
    edge = np.zeros(m, bool); ch = np.where(np.diff(exp.astype(int)) != 0)[0]
    for x in ch: edge[max(x - 1, 0):x + 3] = True
    on_bad = np.where(exp & ~edge & ~shown)[0]
    off_bad = np.where(~exp & ~edge & (np.abs(A[:m] - B[:m]).mean((1, 2)) > 6))[0]
    eng = np.where(cap[:m] & ~shown)[0] if cap is not None else np.array([], int)
    vd, sd = probe_duration_ms(a.out), probe_duration_ms(video)
    two = sum(1 for c in cards if c[3] > 1)
    print(f"saved {a.out}: {vd / 1000:.3f}s (rendered video {sd / 1000:.3f}s); {len(cards)} subtitles, {two} on two lines")
    if small: print(f"font reduced to fit (too long even on two lines): {[(i, s) for i, s, _ in small]}")
    print(f"first subtitle: SRT start {cards[0][0]:.2f}s -> " + (f"shown from {win0 / FPS:.2f}s (lead-in)" if lead_used else "as timed")
          + f"; last subtitle " + ("held to the end" if tail_used else "as timed")
          + f"; {held} short pauses bridged by holding the previous line; {dyn} frames got an empty strip to hide a source caption")
    print(f"self-check: strip missing on {len(on_bad)} planned frames, picture changed on {len(off_bad)} other frames, "
          f"source caption visible on {len(eng)} frames" + (f" -- at {[round(x / FPS, 2) for x in list(on_bad[:3]) + list(off_bad[:3]) + list(eng[:3])]}s"
                                                          if len(on_bad) + len(off_bad) + len(eng) else ""))
    one = next((c for c in cards if c[3] == 1), cards[0]); twol = next((c for c in cards if c[3] > 1), cards[-1])
    shots = [win0 / FPS + 0.2, (one[0] + one[1]) / 2, (twol[0] + twol[1]) / 2] + ([(cards[small[0][0]][0] + cards[small[0][0]][1]) / 2] if small else [])
    qc = Image.new("RGB", (len(shots) * 540, 540), "white")
    for k, t in enumerate(shots):
        png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", a.out, "-frames:v", "1", "-vf", "scale=540:540",
                              "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        if png: qc.paste(Image.open(io.BytesIO(png)).convert("RGB"), (k * 540, 0))
    qp = os.path.join(a.work, "subs_qc.jpg"); qc.save(qp, quality=85)
    print(f"subtitle QC image (first frame, one-line, two-line{', reduced-font' if small else ''}): {qp} -- LOOK at it")
    if not a.keep_parts:
        for k in range(len(chunks)):
            for ext in ("", ".ok", ".txt"):
                q = os.path.join(d, f"part{k:03d}.mp4{ext}")
                if os.path.exists(q): os.remove(q)
        os.remove(silent)
    cfg = load(a.work, "config.json")
    cfg.setdefault("subs_outputs", {})[os.path.abspath(a.out)] = dict(srt=os.path.abspath(srt), video=os.path.abspath(video), offset_s=off)
    save(a.work, "config.json", cfg)
    if len(on_bad) or len(off_bad) or len(eng) or abs(vd - sd) > 1000 / FPS + 10: sys.exit(1)

# ---------------------------------------------------------------------------------
# 9. bookends: put back the source's unused opening and ending, at 1x with their own sound
#    (the build-up / theme music before the first matched line and after the last one).
#    The dub episode (audio + subtitles + matched clips) is unchanged; it just starts later.
# ---------------------------------------------------------------------------------
def scan_captions(video, box, W, FPS, t0, t1):
    """Times (s) in [t0, t1) where the video shows its burned-in caption box."""
    import numpy as np
    if t1 - t0 < 1 / FPS:
        return []
    t, b = box; h = b - t + 1; sh = max(h // 2, 8)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", video, "-vf",
                          f"crop={W}:{h}:0:{t},scale=540:{sh},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, sh, 540)
    on = ((fr > 235).sum(2) > 540 * 0.11).sum(1) >= 0.3 * sh
    return [t0 + i / FPS for i in np.where(on)[0]]

def cmd_bookends(a):
    import numpy as np
    cfg = load(a.work, "config.json"); main = a.input or cfg.get("video_out")
    if not main or not os.path.exists(main):
        raise SystemExit("no rendered video in this work folder -- run `render` and `verify` first")
    out = os.path.abspath(a.out)
    if out == os.path.abspath(main):
        raise SystemExit("--out must be a new file (the clean master), not the render itself")
    # The input is always the render WITHOUT opening/ending, so re-running simply rebuilds the clean master.
    for p in (cfg.get("subs_outputs") or {}):
        if os.path.abspath(main) == os.path.abspath(p):
            raise SystemExit(f"{main} is a subtitled output; bookends takes the clean render (video_out)")
    segs = active_manifest(a.work, cfg, cfg.get("video_manifest"))[1]["segments"]
    segs = [s for s in segs if not s.get("video")] or segs     # imported clips say nothing about the source's opening/ending
    src = cfg["src_video"]; W, H, _src_fps = probe_video(src); FPS = out_fps(cfg); src_ms = probe_duration_ms(src)
    cues = shift_cues(parse_srt(cfg["src_srt"]), cfg.get("src_offset_ms", 0))
    first_clip = min(s["us_start_ms"] for s in segs); last_clip = max(s["us_end_ms"] for s in segs)
    i_end = min([first_clip] + ([cues[0].start_ms] if cues else []))        # before ANY source dialogue
    o_start = max([last_clip] + ([cues[-1].end_ms] if cues else []))         # after ANY source dialogue
    cbp = os.path.join(a.work, "caption_box.json")
    box = load(a.work, "caption_box.json")["box"] if os.path.exists(cbp) else detect_caption_box(src)
    if box:                                                                 # and no burned-in caption on screen
        caps = scan_captions(src, box, W, FPS, 0, i_end / 1000)
        if caps: i_end = max(0, (caps[0] - 2 / FPS) * 1000)
        caps = scan_captions(src, box, W, FPS, o_start / 1000, src_ms / 1000)
        if caps: o_start = (caps[-1] + 3 / FPS) * 1000
    n_in = int(i_end * FPS / 1000); o_f = math.ceil(o_start * FPS / 1000); n_out = int(src_ms * FPS / 1000) - o_f
    if n_in < a.min_len * FPS: n_in = 0
    if n_out < a.min_len * FPS: n_out = 0
    # owner 2026-10-29: never an opening that shows a person speaking (TGS ep34: 0.87 s of Anya with a moving mouth, just
    # before her source line, in front of the dub's first line, which Paul says). The source lip scan (lipsync.json) decides:
    # more than 0.2 s of moving mouth inside the opening = no opening; the video starts with the dub.
    if n_in and os.path.exists(os.path.join(a.work, "lipsync.json")):
        o1 = n_in / FPS
        m = sum(max(0.0, min(y, o1) - max(x, 0.0)) for x, y in (load(a.work, "lipsync.json").get("intervals") or []))
        if m > 0.2:
            print(f"opening left out: source 0.00-{o1:.2f}s shows a person speaking (moving mouth {m:.2f}s)"); n_in = 0
    fade = source_fade_ms(a.work, src)
    if fade is not None and n_out:
        keep = max(0, int((fade - 100) * FPS / 1000) - o_f)          # the ending never shows the fade or the black
        if keep < n_out:
            n_out = keep if keep >= a.min_len * FPS else 0
            print(f"ending stops before the source's fade to black at {fade / 1000:.2f}s"
                  + ("" if n_out else " (too little real picture left: no ending)"))
    if getattr(a, "no_ending", False):
        n_out = 0; print("--no-ending: the video ends with the dub audio (no source ending after it)")
    print(f"opening: source 0.00-{n_in / FPS:.2f}s  (first matched clip at {first_clip / 1000:.2f}s, first source subtitle at "
          f"{cues[0].start_ms / 1000 if cues else 0:.2f}s)")
    print(f"ending:  source {o_f / FPS:.2f}-{(o_f + n_out) / FPS:.2f}s  (last matched clip ends {last_clip / 1000:.2f}s, last source "
          f"subtitle ends {cues[-1].end_ms / 1000 if cues else 0:.2f}s, source is {src_ms / 1000:.2f}s)")
    if not n_in and not n_out:
        print("nothing to add: the clean master is the render itself"); shutil.copyfile(main, out)
        cfg.update(clean_final=out, opening_s=0, ending_s=0); save(a.work, "config.json", cfg); return
    ar = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels",
                         "-of", "csv=p=0", main], capture_output=True, text=True).stdout.strip().split(",")
    sr, ch = ar[0], ar[1]
    has_a = bool(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", src],
                                capture_output=True, text=True).stdout.strip())
    d = os.path.join(a.work, "bookends"); os.makedirs(d, exist_ok=True); fd = a.fade
    def cut(f0, nf, path, fade):
        dur = nf / FPS
        af = (f"afade=t=out:st={max(dur - fd, 0):.3f}:d={fd}" if fade == "out" else f"afade=t=in:st=0:d={fd}")
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{f0 / FPS:.6f}", "-i", src]
        if not has_a: cmd += ["-f", "lavfi", "-i", f"anullsrc=r={sr}:cl={'mono' if ch == '1' else 'stereo'}"]
        cmd += ["-map", "0:v:0", "-map", "0:a:0" if has_a else "1:a:0", "-vf", f"fps={FPS},format=yuv420p", "-frames:v", str(nf),
                "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-af", f"aresample={sr},{af}", "-ac", ch, "-c:a", "aac", "-b:a", "192k", "-t", f"{dur:.6f}", path]
        subprocess.run(cmd, check=True)
    parts = []
    if n_in: cut(0, n_in, os.path.join(d, "opening.mp4"), "out"); parts.append(os.path.join(d, "opening.mp4"))
    parts.append(main)
    if n_out: cut(o_f, n_out, os.path.join(d, "ending.mp4"), "in"); parts.append(os.path.join(d, "ending.mp4"))
    lst = os.path.join(d, "parts.txt"); open(lst, "w").write("".join(f"file '{os.path.abspath(p)}'\n" for p in parts))
    tmp = os.path.join(os.path.dirname(out), ".bookends_tmp.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-map", "0:v", "-map", "0:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp], check=True)
    main_ms = probe_duration_ms(main); new_ms = probe_duration_ms(tmp); want = (n_in + n_out) / FPS * 1000 + main_ms
    # check: the dub audio must start exactly where the opening ends
    def pcm(path, t0, t=15):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", str(t), "-i", path, "-vn", "-ac", "1", "-ar", "8000",
                              "-f", "s16le", "-"], capture_output=True).stdout
        return np.frombuffer(raw, np.int16).astype(float)
    x, y = pcm(tmp, n_in / FPS + 0.5), pcm(cfg["dub_audio"], 0.5); m = min(len(x), len(y))
    lag, peak, _ = best_lag(y[:m], x[:m], 800) if m > 8000 else (0, 0, 0)
    os.replace(tmp, out)
    cfg["opening_s"] = round(n_in / FPS, 3); cfg["ending_s"] = round(n_out / FPS, 3); cfg["clean_final"] = out
    cfg["ending_src_s"] = round(o_f / FPS, 3); cfg["clean_nofx"] = out; cfg.pop("glow", None)
    cfg.pop("bookended", None)
    save(a.work, "config.json", cfg)
    if cfg.get("sheet_out") and os.path.exists(cfg["sheet_out"]):
        import openpyxl
        from openpyxl.styles import Font, PatternFill
        wb = openpyxl.load_workbook(cfg["sheet_out"])
        if "Video edit list" in wb.sheetnames:
            ws = wb["Video edit list"]
            for rr in range(ws.max_row, 1, -1):          # re-running bookends replaces its own rows instead of adding more
                if ws.cell(rr, 1).value == "-" and str(ws.cell(rr, 7).value or "").startswith(("OPENING:", "ENDING:")):
                    ws.delete_rows(rr)
            ws.insert_rows(2)
            note = (f"OPENING: final video 00:00.000-{fmt(n_in / FPS * 1000)} = source 00:00.000-{fmt(n_in / FPS * 1000)} at 1x with the "
                    f"source's own sound (no dub, no subtitles). Every time below is {n_in / FPS:.3f}s later in the final video.") if n_in else \
                   "OPENING: none (the first matched line starts at the very beginning of the source)."
            ws.cell(2, 1, "-"); ws.cell(2, 7, note)
            r = ws.max_row + 1; ws.cell(r, 1, "-")
            ws.cell(r, 7, f"ENDING: source {fmt(o_f / FPS * 1000)}-{fmt((o_f + n_out) / FPS * 1000)} at 1x with the source's own sound, "
                          f"after the dub ends." if n_out else "ENDING: none.")
            for rr in (2, r):
                for cc in range(1, 8):
                    ws.cell(rr, cc).fill = PatternFill("solid", start_color="FFEB9C"); ws.cell(rr, cc).font = Font(name="Arial", size=10, bold=True)
            wb.save(cfg["sheet_out"])
    print(f"saved {out}: {new_ms / 1000:.3f}s = opening {n_in / FPS:.2f}s + dub episode {main_ms / 1000:.3f}s + ending {n_out / FPS:.2f}s "
          f"(expected {want / 1000:.3f}s, diff {abs(new_ms - want):.0f}ms)")
    print(f"check: dub audio starts {lag / 8000 * 1000:+.0f}ms from the end of the opening (match {peak:.2f})")
    if abs(new_ms - want) > 3 * 1000 / FPS + 50 or (m > 8000 and (abs(lag) > 400 or peak < 0.5)): sys.exit(1)

# ---------------------------------------------------------------------------------
# 9b. transitions: a transition at every clip switch of the clean master (owner's TOLR choice: light leak,
#     blur dissolve and zoom blur in random order, never the same one twice in a row; `glow` is also available).
#     Where the stitched picture jumps in the source (between groups, lip clips, trimmed lip sentences, and the joins
#     to the opening and ending) the switch gets a short effect. Dissolving effects mix the outgoing clip (running on a
#     little past its end in the source, at its own speed) with the incoming clip (starting a little earlier). No overlap
#     is added, so the length and the sync with the dub voice never change. Resumable in chunks.
# ---------------------------------------------------------------------------------
TRANSITION_HALF = dict(light_leak=0.30, blur_dissolve=0.30, zoom_blur=0.25, glow=0.25)   # seconds on each side of the switch

def _ss(x):
    x = min(max(x, 0.0), 1.0); return x * x * (3 - 2 * x)

def glow_frame(fr, w, peak=0.82):
    import numpy as np, cv2
    H, W = fr.shape[:2]
    f = fr.astype(np.float32)
    sm = cv2.resize(f, (max(W // 4, 1), max(H // 4, 1)), interpolation=cv2.INTER_AREA)
    bloom = cv2.resize(cv2.GaussianBlur(sm, (0, 0), 3 + 9 * w), (W, H), interpolation=cv2.INTER_LINEAR)
    out = 255 - (255 - f) * (255 - bloom * 0.9 * w) / 255             # screen-blend the bloom (glow)
    soft = cv2.GaussianBlur(out, (0, 0), 1 + 6 * w)                   # soften as it peaks (blur)
    out = out + (soft - out) * min(1.0, 1.4 * w)
    tint = np.array([255, 246, 238], np.float32)                      # RGB warm white
    out = out + (tint - out) * (peak * w ** 1.6)
    return np.clip(out, 0, 255).astype(np.uint8)

def transition_frame(style, a, b, x, w, p):
    """One frame of a transition. a/b = outgoing/incoming frame (float32 RGB), x = signed position in the transition
    (-1 at its start, 0 at the switch, +1 at its end), w = bump 0..1 (1 at the switch), p = progress 0..1."""
    import numpy as np, cv2
    H, W = a.shape[:2]
    if style == "light_leak":                                       # warm orange flare sweeping across a soft mix
        base = a * (1 - p) + b * p
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        lx = -0.3 * W + p * 1.6 * W
        leak = np.exp(-(((xx - lx) / (0.45 * W)) ** 2 + ((yy - 0.35 * H) / (0.9 * H)) ** 2))
        col = np.array([255, 140, 40], np.float32); m = (leak * w * 0.95)[..., None]
        out = base * (1 - m) + (255 - (255 - base) * (255 - col) / 255) * m
    elif style == "blur_dissolve":                                  # both clips blur while they mix
        out = a * (1 - p) + b * p
        if w > 0.01: out = cv2.GaussianBlur(out, (0, 0), 0.5 + 18 * w)
    elif style == "zoom_blur":                                      # radial zoom punch over a hard switch
        f = a if x < 0 else b
        if w < 0.01: out = f
        else:
            acc = np.zeros_like(f)
            for k in range(8):
                s = 1 + 0.25 * w * k / 7; M = np.float32([[s, 0, W / 2 - s * W / 2], [0, s, H / 2 - s * H / 2]])
                acc += cv2.warpAffine(f, M, (W, H), borderMode=cv2.BORDER_REFLECT)
            out = acc / 8
    elif style == "glow":
        return glow_frame(a if x < 0 else b, w)
    else:
        raise SystemExit(f"unknown transition {style}")
    return np.clip(out, 0, 255).astype(np.uint8)

def clip_switches(cfg, segs, min_jump):
    """Clip switches of the clean master: [(final time s, outgoing source end s, outgoing speed, incoming source start s,
    incoming speed)] -- speed = source seconds per final-video second. Our stitching only, not the source's own cuts."""
    op = cfg.get("opening_s", 0) or 0
    segs = sorted(segs, key=lambda s: s["it_start_ms"]); out = []
    sp = lambda s: (s["us_end_ms"] - s["us_start_ms"]) / max(s["it_end_ms"] - s["it_start_ms"], 1)
    if op > 0 and abs(segs[0]["us_start_ms"] / 1000 - op) * 1000 > min_jump:
        out.append((op, op, 1.0, segs[0]["us_start_ms"] / 1000, sp(segs[0])))
    for x, y in zip(segs, segs[1:]):
        if abs(y["us_start_ms"] - x["us_end_ms"]) > min_jump:
            out.append((op + x["it_end_ms"] / 1000, x["us_end_ms"] / 1000, sp(x), y["us_start_ms"] / 1000, sp(y)))
    es = cfg.get("ending_src_s")
    if (cfg.get("ending_s", 0) or 0) > 0 and es is not None and abs(es * 1000 - segs[-1]["us_end_ms"]) > min_jump:
        out.append((op + segs[-1]["it_end_ms"] / 1000, segs[-1]["us_end_ms"] / 1000, sp(segs[-1]), es, 1.0))
    return out

def cmd_transitions(a):
    import numpy as np, random
    cfg = load(a.work, "config.json")
    src = a.input or cfg.get("clean_nofx") or cfg.get("clean_final")
    if not src or not os.path.exists(src): raise SystemExit("no clean master -- run `bookends` first")
    out = os.path.abspath(a.out)
    if out == os.path.abspath(src): raise SystemExit("--out must be a new file (bookends' clean master is the input)")
    W, H, FPS = probe_video(src)
    n = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                            "stream=nb_read_packets", "-of", "csv=p=0", src], capture_output=True, text=True).stdout.strip())  # real frame count
    if a.until: n = min(n, round(a.until * FPS))
    segs = active_manifest(a.work, cfg, cfg.get("video_manifest"))[1]["segments"]
    sw = clip_switches(cfg, segs, a.min_jump)
    styles = [s.strip() for s in a.styles.split(",") if s.strip()]
    for s in styles:
        if s not in TRANSITION_HALF: raise SystemExit(f"unknown transition {s!r}; choose from {sorted(TRANSITION_HALF)}")
    # optional per-cut choices (Dub Studio's editor): {"default": "auto"|"none"|style, "cuts": {"<dub ms of the cut>": style}}
    # "auto" = the random rotation below (what happens without a plan), "none" = a hard cut at that switch
    pl = json.load(open(a.plan)) if a.plan else None
    op_ = cfg.get("opening_s", 0) or 0
    def chosen(c):
        if not pl: return "auto"
        ms = (c - op_) * 1000; cuts = pl.get("cuts") or {}
        k = min(cuts, key=lambda x: abs(float(x) - ms), default=None)
        return cuts[k] if k is not None and abs(float(k) - ms) <= 60 else pl.get("default", "auto")
    for s_ in ([v for v in ((pl or {}).get("cuts") or {}).values()] + [(pl or {}).get("default", "auto")]):
        if s_ not in TRANSITION_HALF and s_ not in ("auto", "none"): raise SystemExit(f"unknown transition {s_!r} in --plan")
    rnd = random.Random(a.seed); plan, prev = [], None
    for k, (c, ua, sa, ub, sb) in enumerate(sw):                    # random order, never the same style twice in a row
        ch = chosen(c)
        if ch == "none": continue
        st = rnd.choice([s for s in styles if s != prev] or styles) if ch == "auto" else ch; prev = st
        hh = TRANSITION_HALF[st] * a.length
        pg = c - (sw[k - 1][0] if k else 0); ng = (sw[k + 1][0] if k + 1 < len(sw) else 1e9) - c
        plan.append(dict(t=c, style=st, hb=min(hh, 0.45 * pg), ha=min(hh, 0.45 * ng), ua=ua, sa=sa, ub=ub, sb=sb))
    src_video = cfg["src_video"]
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0",
                           src_video], capture_output=True, text=True).stdout.strip().split("/")
    sfps = int(rate[0]) / int(rate[1]) if len(rate) == 2 else float(rate[0])
    vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2"
    def grab(t0, t1):                                               # source frames [t0, t1) at the render's size
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t0, 0):.3f}", "-t", f"{t1 - max(t0, 0):.3f}", "-i", src_video, "-vf", vf,
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return max(t0, 0), np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)
    def at(fr, t0, u):
        return fr[1][min(max(int(round((u - fr[0]) * sfps)), 0), len(fr[1]) - 1)].astype(np.float32)
    d = os.path.join(a.work, "trans_" + re.sub(r"[^\w.-]", "_", os.path.splitext(os.path.basename(out))[0])); os.makedirs(d, exist_ok=True)
    st_ = os.stat(src)
    sig0 = hashlib.md5(json.dumps([plan, a.length, st_.st_size, int(st_.st_mtime), FPS, n]).encode()).hexdigest()
    cf = round(a.chunk * FPS); chunks = [(k * cf, min((k + 1) * cf, n)) for k in range(math.ceil(n / cf))]
    t_stop = time.time() + a.max_seconds
    def job(k):
        f0, f1 = chunks[k]; p = os.path.join(d, f"part{k:03d}.mp4"); sig = f"{sig0}|{f0}|{f1}"
        if (os.path.exists(p + ".ok") and open(p + ".ok").read() == sig) or time.time() > t_stop: return
        mine = [q for q in plan if q["t"] + q["ha"] > f0 / FPS and q["t"] - q["hb"] < f1 / FPS]
        cache = {}                                                  # source frames of ONE transition at a time (memory:
        def frames(q):                                              # clause-mode episodes have many switches per chunk)
            if id(q) not in cache:                                  # outgoing clip runs on, incoming clip starts earlier
                cache.clear()
                cache[id(q)] = (grab(q["ua"] - (q["hb"] + 0.1) * q["sa"], q["ua"] + (q["ha"] + 0.1) * q["sa"]),
                                grab(q["ub"] - (q["hb"] + 0.1) * q["sb"], q["ub"] + (q["ha"] + 0.1) * q["sb"]))
            return cache[id(q)]
        dec = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{f0 / FPS:.6f}", "-i", src, "-frames:v", str(f1 - f0), "-f", "rawvideo",
                                "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", p], stdin=subprocess.PIPE)
        m = 0
        while m < f1 - f0:
            buf = dec.stdout.read(W * H * 3)
            if len(buf) < W * H * 3: break
            t = (f0 + m + 0.5) / FPS
            q = next((q for q in mine if q["t"] - q["hb"] <= t < q["t"] + q["ha"]), None)
            if q and q["hb"] > 0.02 and q["ha"] > 0.02:
                dt = t - q["t"]; x = dt / q["hb"] if dt < 0 else dt / q["ha"]
                w = _ss(1 - abs(x)); p_ = _ss((x + 1) / 2)
                fa, fb = frames(q)
                A = at(fa, 0, q["ua"] + dt * q["sa"]); B = at(fb, 0, q["ub"] + dt * q["sb"])
                buf = transition_frame(q["style"], A, B, x, w, p_).tobytes()
            enc.stdin.write(buf); m += 1
        enc.stdin.close(); enc.wait(); dec.wait()
        if m == f1 - f0 and enc.returncode == 0: open(p + ".ok", "w").write(sig)
        else: print(f"FAILED chunk {k}: {m}/{f1 - f0} frames")
    with ThreadPoolExecutor(a.workers) as ex: list(ex.map(job, range(len(chunks))))
    done = sum(os.path.exists(os.path.join(d, f"part{k:03d}.mp4.ok")) and
               open(os.path.join(d, f"part{k:03d}.mp4.ok")).read() == f"{sig0}|{chunks[k][0]}|{chunks[k][1]}" for k in range(len(chunks)))
    cnt = defaultdict(int)
    for q in plan:
        if q["t"] < n / FPS: cnt[q["style"]] += 1
    print(f"transitions: {sum(cnt.values())} clip switches ({dict(cnt)}); {done}/{len(chunks)} chunks done")
    if done < len(chunks): print("not finished -- run the same command again (it resumes)"); sys.exit(3)
    lst = os.path.join(d, "parts.txt"); silent = os.path.join(d, "silent.mp4")
    open(lst, "w").write("".join(f"file '{os.path.abspath(os.path.join(d, f'part{k:03d}.mp4'))}'\n" for k in range(len(chunks))))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent], check=True)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", silent, "-i", src, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "192k", "-t", f"{n / FPS:.6f}", "-movflags", "+faststart", out], check=True)
    vd = probe_duration_ms(out)
    for k in range(len(chunks)):
        for ext in ("", ".ok"):
            q = os.path.join(d, f"part{k:03d}.mp4{ext}")
            if os.path.exists(q): os.remove(q)
    os.remove(silent)
    rows = [q for q in plan if q["t"] < n / FPS]
    if a.until:
        print(f"saved TEST {out}: first {vd / 1000:.3f}s only (the clean master is unchanged)"); return
    cfg = load(a.work, "config.json")
    cfg["clean_nofx"] = os.path.abspath(src); cfg["clean_final"] = out
    cfg["transitions"] = dict(styles=styles, seed=a.seed, length=a.length, plan=[dict(t=round(q["t"], 3), style=q["style"]) for q in rows], out=out)
    cfg.pop("glow", None); save(a.work, "config.json", cfg)
    if cfg.get("sheet_out") and os.path.exists(cfg["sheet_out"]):      # list every switch and its transition in the mapping sheet
        import openpyxl
        from openpyxl.styles import Font, PatternFill
        wb = openpyxl.load_workbook(cfg["sheet_out"])
        if "Transitions" in wb.sheetnames: del wb["Transitions"]
        ws = wb.create_sheet("Transitions")
        for ci, h in enumerate(["#", "Final video time (s)", "Timecode", "Transition", "Length (s)"], 1):
            c = ws.cell(1, ci, h); c.font = Font(name="Arial", bold=True, color="FFFFFF"); c.fill = PatternFill("solid", start_color="1F4E78")
        for k, q in enumerate(rows, 2):
            for ci, v in enumerate([k - 1, round(q["t"], 3), fmt(q["t"] * 1000), q["style"].replace("_", " "), round(q["hb"] + q["ha"], 2)], 1):
                ws.cell(k, ci, v).font = Font(name="Arial", size=10)
        wb.save(cfg["sheet_out"])
    print(f"saved {out}: {vd / 1000:.3f}s; {len(rows)} transitions ({dict(cnt)}), never the same twice in a row; "
          f"this is now the clean master that `subs` uses")

# ---------------------------------------------------------------------------------
# 10. groups: continuous US footage for blocks of 7-8 Italian rows (the mandatory dynamic-group flow)
#     Built on the finished row-level manifest (silences absorbed, bleed fix, gap fill). Each group plays ONE
#     continuous US span; extra US time is trimmed from the edges of fast narration rows, missing time is a gentle
#     whole-group stretch. Writes manifest_groups.json (render/verify/bookends then use it) + a review workbook.
# ---------------------------------------------------------------------------------
_SPEECH_W = set("i i'm i've i'll i'd me my mine myself we we're we've we'll us our ours you you're you've you'll you'd "
                "your yours yourself yourselves let's".split())
_NARR_W = set("he she his her him himself herself they them their theirs said asked thought wondered replied muttered "
              "whispered yelled shouted sighed told continued answered added called cried exclaimed snapped".split())

def speech_or_narration(text):
    """Rough class of a source line, recorded so a person can check every trim: 'direct speech' or 'narration / reaction'."""
    text = text or ""; words = re.findall(r"[a-z']+", text.lower())
    sp = sum(w in _SPEECH_W for w in words) + (1 if re.search(r'["“”«»]', text) else 0)
    na = sum(w in _NARR_W for w in words)
    if text.rstrip().endswith(("?", "!")) and not na: sp += 1
    return "direct speech" if sp > na else "narration / reaction"

def _group_plan(G, rows, a, repeat_rows, lip_rows=None):
    """One group: G = consecutive manifest segments. Returns dict with output segments, trims, drift, flags."""
    it0, it1 = G[0]["it_start_ms"], G[-1]["it_end_ms"]
    us0, us1 = G[0]["us_start_ms"], G[-1]["us_end_ms"]
    it_dur, us_dur = it1 - it0, us1 - us0
    info = dict(rows=[s["it_idx"] for s in G], it0=it0, it1=it1, us0=us0, us1=us1, trims=[], flags=[], note="")
    lip_rows = lip_rows or {}
    if len(G) == 1 and G[0]["it_idx"] in lip_rows:              # lip-sync row: its own clip, lips centred on the dub line
        p = lip_rows[G[0]["it_idx"]]
        info["note"] = p.get("note") or f"lip-sync row: own clip at x{p['stretch']}"
        info["lip"] = True; pieces = [(us0, us1)]
    elif len(G) == 1 and G[0]["it_idx"] in repeat_rows:          # dub repeat: replays the earlier row's footage as is
        info["note"] = "dub repeat: plays the earlier row's footage as in the row-level manifest"
        pieces = [(us0, us1)]
    else:
        keep_target = min(us_dur, it_dur * a.max_speedup)      # --max-speedup 1.0: trim ALL extra US time (approved flow)
        tf = getattr(a, "trim_fast", 0) or 0
        if tf and us_dur > it_dur * tf:                        # --trim-fast X: a block faster than X loses its extra
            keep_target = min(us_dur, it_dur * getattr(a, "trim_to", 1.0))   # source time down to --trim-to (narration first)
        excess = us_dur - keep_target; removed = []
        if 0 < excess <= a.min_cut:
            info["note"] = f"only {excess / 1000:.2f}s extra: absorbed by a {us_dur / it_dur:.3f}x speed-up instead of a cut"
        elif excess > a.min_cut:
            cands = []
            for s in G:
                r = rows[s["it_idx"]]; ulen = s["us_end_ms"] - s["us_start_ms"]; cap = ulen - a.min_keep
                if cap <= 0 or s["it_idx"] in repeat_rows:
                    continue
                f = (r["dub_end"] - r["dub_start"]) / max(ulen, 1)     # < 1: the Italian line is shorter = plays fast
                cls = speech_or_narration(s.get("us_text") or r.get("src_text") or "")
                tier = (0 if f < 1 else 2) + (0 if cls.startswith("narration") else 1)
                cands.append(dict(seg=s, cap=cap, f=f, cls=cls, tier=tier))
            need = excess
            for tier in range(4):
                pool = sorted([c for c in cands if c["tier"] == tier], key=lambda c: -c["cap"])
                while pool and need > 1:
                    tot = sum(c["cap"] for c in pool)
                    al = [min(c["cap"], need * c["cap"] / tot) for c in pool]
                    if min(al) >= a.min_cut or len(pool) == 1:
                        break
                    pool = pool[:-1]                             # never cut slivers: drop the smallest candidate
                if not pool or need <= 1:
                    continue
                tot = sum(c["cap"] for c in pool)
                for c in pool:
                    e = min(c["cap"], need * c["cap"] / tot)
                    s = c["seg"]; h = e / 2
                    removed += [(s["us_start_ms"], s["us_start_ms"] + h), (s["us_end_ms"] - h, s["us_end_ms"])]
                    info["trims"].append(dict(row=s["it_idx"], ms=round(e), cls=c["cls"], fast=c["f"] < 1,
                                              text=s.get("us_text") or ""))
                    if tier >= 2:
                        info["flags"].append(f"IT#{s['it_idx']} trimmed although it is not a fast row -- check it")
                    elif tier == 1 and not any(x["tier"] == 0 for x in cands):
                        info["flags"].append(f"IT#{s['it_idx']} (direct speech) trimmed: no narration row could take it")
                need -= sum(min(c["cap"], need * c["cap"] / tot) for c in pool)
            if need > 1:
                info["flags"].append(f"{need / 1000:.2f}s could not be trimmed (no row long enough): absorbed by speed-up")
        removed.sort(); pieces, cur = [], us0
        for lo, hi in removed:
            if lo > cur: pieces.append((cur, lo))
            cur = max(cur, hi)
        if cur < us1: pieces.append((cur, us1))
        info["removed"] = [(round(lo), round(hi)) for lo, hi in removed]
    kept = sum(b - a_ for a_, b in pieces)
    stretch = it_dur / max(kept, 1)
    info.update(kept=kept, stretch=stretch, removed_ms=us_dur - kept)
    if stretch > a.max_stretch: info["flags"].append(f"whole group plays {stretch:.2f}x slower (limit {a.max_stretch})")
    if stretch < 1 / a.max_stretch: info["flags"].append(f"whole group plays {1 / stretch:.2f}x faster (limit {a.max_stretch})")
    # split kept pieces at row boundaries (labels + finer verify), never into slivers under 120 ms
    bounds = sorted({s["us_start_ms"] for s in G} | {s["us_end_ms"] for s in G})
    sub = []
    for p0, p1 in pieces:
        cuts = [b for b in bounds if p0 + 120 < b < p1 - 120]
        edges = [p0] + cuts + [p1]
        sub += list(zip(edges, edges[1:]))
    def owner(u0, u1):
        mid = (u0 + u1) / 2
        inside = [s for s in G if s["us_start_ms"] <= mid < s["us_end_ms"]]
        if inside: return inside[0], ""
        before = [s for s in G if s["us_end_ms"] <= mid]
        return (before[-1] if before else G[0]), " (footage between rows)"
    out, t = [], float(it0)
    for k, (u0, u1) in enumerate(sub):
        d_it = (u1 - u0) * it_dur / kept; t1 = it1 if k == len(sub) - 1 else t + d_it
        s, why = owner(u0, u1)
        out.append(dict(kind="clip", it_idx=s["it_idx"], it_start_ms=round(t, 3), it_end_ms=round(t1, 3),
                        us_start_ms=round(u0, 3), us_end_ms=round(u1, 3), it_text=s.get("it_text", ""),
                        us_text=s.get("us_text", ""), stretch_factor=round(stretch, 3), note=why.strip()))
        t = t1
    # picture/voice drift: where each row's (kept) footage plays vs. when its Italian line is spoken
    def out_time(u):
        acc = 0.0
        for p0, p1 in pieces:
            if u <= p0: break
            acc += min(u, p1) - p0
        return it0 + acc * it_dur / kept
    drift = {}
    for s in G:
        r = rows[s["it_idx"]]
        lo, hi = s["us_start_ms"], s["us_end_ms"]
        keep_parts = [(max(lo, p0), min(hi, p1)) for p0, p1 in pieces if min(hi, p1) > max(lo, p0)]
        if not keep_parts: continue
        u_mid = (keep_parts[0][0] + keep_parts[-1][1]) / 2
        drift[s["it_idx"]] = round(out_time(u_mid) - (r["dub_start"] + r["dub_end"]) / 2)
    info.update(segments=out, drift=drift, max_drift=max([abs(v) for v in drift.values()], default=0))
    return info

def source_fade_ms(work, video, tail_s=40.0):
    """Where the source video's closing fade to black starts (ms), or None when it does not end in black.
    Cached in W/source_fade.json. Used so no clip and no ending ever shows the fade or the black after it
    (WBM ep28: the US video fades from 498.2 s and is black 499.0-501.5 s)."""
    import numpy as np
    cp = os.path.join(work, "source_fade.json")
    if os.path.exists(cp):
        c = json.load(open(cp))
        if c.get("video") == video and c.get("size") == os.path.getsize(video):
            return c.get("fade_ms")
    dur = probe_duration_ms(video) / 1000; t0 = max(0.0, dur - tail_s); fps = 10
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-i", video, "-an", "-vf",
                          f"fps={fps},scale=160:-2,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
                          "-f", "null", "-"], capture_output=True, text=True).stdout
    y = np.array([float(v) for v in re.findall(r"YAVG=([0-9.]+)", out)])
    fade = None
    if len(y) > fps * 3 and y[-1] <= 20:
        k = len(y) - 1
        while k > 0 and y[k - 1] <= 20: k -= 1                 # start of the black tail
        black = k
        ref = float(np.median(y[max(0, black - 5 * fps):max(1, black - 2 * fps)])) if black > 2 * fps else 100.0
        while k > 0 and y[k - 1] < 0.92 * ref: k -= 1             # back over the fade
        fade = round((t0 + k / fps) * 1000)
        print(f"source ends in black: fade from {fade / 1000:.2f}s, black from {(t0 + black / fps):.2f}s "
              f"(video {dur:.2f}s): no clip or ending will show it")
    json.dump({"video": video, "size": os.path.getsize(video), "fade_ms": fade}, open(cp, "w"))
    return fade


def clamp_to_fade(segs, fade, margin=100):
    """Clips that reach into the closing fade end before it; a clip wholly inside it moves back before it.
    Speeds are recomputed. -> number of clips changed."""
    if fade is None: return 0
    lim = fade - margin; n = 0
    for s_ in segs:
        if s_.get("video") or s_["us_end_ms"] <= lim: continue
        L = s_["us_end_ms"] - s_["us_start_ms"]
        if s_["us_start_ms"] < lim - 300:
            s_["us_end_ms"] = lim
        else:
            s_["us_end_ms"] = lim; s_["us_start_ms"] = max(0, lim - L)
        s_["stretch_factor"] = round((s_["it_end_ms"] - s_["it_start_ms"]) / max(1, s_["us_end_ms"] - s_["us_start_ms"]), 3)
        s_["note"] = ((s_.get("note") or "") + "; ends before the source's fade to black").lstrip("; ")
        n += 1
    return n


def group_footage_clash(out, gi, repeat_rows):
    """Segments in different groups that share more than 1 ms of source time."""
    hits = []
    for k, x in enumerate(out):
        for y in out[k + 1:]:
            if gi[id(x)] == gi[id(y)]:
                continue
            if x["it_idx"] in repeat_rows or y["it_idx"] in repeat_rows:
                continue
            if min(x["us_end_ms"], y["us_end_ms"]) - max(x["us_start_ms"], y["us_start_ms"]) > 1:
                hits.append((x, y))
    return hits


def trim_shared_footage(out, gi, repeat_rows, lip=None):
    """Two groups sharing source time (a lip window spilled into the next row, or the dub
    plays source lines out of order) used to kill the episode. TGS ep33 IT#156/157 overlapped
    113 ms after lipsync padded 157 into 156. Keep the lip-sync row (or the earlier dub line)
    and trim the other. Never fail the run for this."""
    lip = lip or {}
    n = 0
    while n < 50:
        clash = group_footage_clash(out, gi, repeat_rows)
        if not clash:
            return n
        x, y = clash[0]
        keep, cut = (x, y) if (x["it_idx"] in lip) != (y["it_idx"] in lip) and x["it_idx"] in lip \
            else (y, x) if (x["it_idx"] in lip) != (y["it_idx"] in lip) \
            else (x, y) if x["it_start_ms"] <= y["it_start_ms"] else (y, x)
        shared = min(keep["us_end_ms"], cut["us_end_ms"]) - max(keep["us_start_ms"], cut["us_start_ms"])
        if cut["us_start_ms"] < keep["us_end_ms"] <= cut["us_end_ms"]:
            cut["us_start_ms"] = keep["us_end_ms"]
        elif cut["us_end_ms"] > keep["us_start_ms"] >= cut["us_start_ms"]:
            cut["us_end_ms"] = keep["us_start_ms"]
        else:
            left = keep["us_start_ms"] - cut["us_start_ms"]
            right = cut["us_end_ms"] - keep["us_end_ms"]
            if right >= left:
                cut["us_start_ms"] = keep["us_end_ms"]
            else:
                cut["us_end_ms"] = keep["us_start_ms"]
        if cut["us_end_ms"] - cut["us_start_ms"] < 80:
            if cut["us_start_ms"] >= keep["us_end_ms"]:
                cut["us_end_ms"] = cut["us_start_ms"] + 80
            elif cut["us_end_ms"] <= keep["us_start_ms"]:
                cut["us_start_ms"] = cut["us_end_ms"] - 80
            else:
                cut["us_start_ms"] = keep["us_end_ms"]
                cut["us_end_ms"] = keep["us_end_ms"] + 80
        cut["stretch_factor"] = round((cut["it_end_ms"] - cut["it_start_ms"]) / max(cut["us_end_ms"] - cut["us_start_ms"], 1), 3)
        cut["note"] = ((cut.get("note") or "") + f"; trimmed {shared / 1000:.2f}s that IT#{keep['it_idx']} already plays").lstrip("; ")
        n += 1
    return n


def cmd_groups(a):
    cfg = cfg_get(a)
    M = load(a.work, "manifest.json"); segs = M["segments"]; total_ms = M["total_ms"]
    R = load(a.work, "rows.json"); rows = {r["idx"]: r for r in R["rows"]}
    repeat_rows = {int(k) for k in R["repeat"]}
    lip = {}                                                  # lip-sync rows (`lipsync`): own clip, never inside a group
    lp = os.path.join(a.work, "lipsync.json")
    if os.path.exists(lp) and not a.no_lipsync:
        lip = {int(k): v for k, v in load(a.work, "lipsync.json")["rows"].items()}
        segs = [dict(s_, us_start_ms=lip[s_["it_idx"]]["window"][0], us_end_ms=lip[s_["it_idx"]]["window"][1])
                if s_["it_idx"] in lip else dict(s_) for s_ in segs]
        for k, s_ in enumerate(segs):                         # a lip row keeps its own spoken time; the silence next to it
            p_ = lip.get(s_["it_idx"])                         # goes to the neighbouring grouped row
            if p_ and "slot" in p_:
                s_["it_start_ms"], s_["it_end_ms"] = p_["slot"]
                if k: segs[k - 1]["it_end_ms"] = p_["slot"][0]
                if k + 1 < len(segs): segs[k + 1]["it_start_ms"] = p_["slot"][1]
        bad = [s_["it_idx"] for s_ in segs if s_["it_end_ms"] <= s_["it_start_ms"]]
        if bad: raise SystemExit(f"lip-sync slots leave rows {bad} without time -- re-run `lipsync` after `manifest`")
        # ---- lip groups: lip-sync rows that follow each other in the source (a phone call, a dialogue), with at most
        # --lip-group-narr narration rows between them, play as ONE continuous piece of source footage. Every lip row still
        # starts and ends its sentence with its own dub voice; the footage between two lip sentences (the source pause and
        # any narration rows' sentences) fills the dub time between them at one speed. No jump inside -> no transition.
        islip = lambda k: segs[k]["it_idx"] in lip
        def link(k, m):                                       # can lip seg k and the next lip seg m be one piece?
            mid = segs[k + 1:m]
            if len(mid) > a.lip_group_narr or any(s_["it_idx"] in repeat_rows for s_ in segs[k:m + 1]): return None
            rk, rm = rows[segs[k]["it_idx"]], rows[segs[m]["it_idx"]]
            u1, u0, d1, d0 = rk["src_end"], rm["src_start"], rk["dub_end"], rm["dub_start"]
            if u0 < u1 - 40: return None                      # the source goes back: not continuous
            prev = u1
            for s_ in mid:                                    # narration rows must sit in order between the two sentences
                r = rows[s_["it_idx"]]
                if r["src_start"] is None or r["src_start"] < prev - 40 or r["src_end"] > u0 + 40: return None
                prev = r["src_end"]
            S, D = u0 - u1, d0 - d1
            # the source between the two sentences must not be footage another row plays (TGS ep34: rows 14-15 tell the
            # spy beat earlier than the source; a lip group over rows 17-22 ran through that footage -> groups clash)
            if any(j < k or j > m for j, s_ in enumerate(segs)
                   if s_.get("us_start_ms") is not None and min(u0, s_["us_end_ms"]) - max(u1, s_["us_start_ms"]) > 40): return None
            if D > 40 and S > 40 and 1 / a.lip_group_speed <= S / D <= a.lip_group_speed: return "between"
            if not mid and S <= 700: return "meet"           # back to back: the two sentences meet halfway in the pause
            return None
        def lip_window(i, it_len):
            # the row's source sentence, or -- if it would play faster than --lip-max-speed -- the part of it around the
            # lip movement that plays at exactly that speed
            r = rows[i]; u0, u1 = r["src_start"], r["src_end"]
            if not a.lip_max_speed or (u1 - u0) <= it_len * max(a.lip_trim_above, a.lip_max_speed): return (u0, u1), False
            Lk = it_len * a.lip_max_speed
            ls = [(max(x * 1000, u0), min(y * 1000, u1)) for x, y in lip[i].get("lips", []) if min(y * 1000, u1) > max(x * 1000, u0)]
            l0, l1 = (min(x for x, _ in ls), max(y for _, y in ls)) if ls else (u0, u1)
            w0 = (l0 + l1) / 2 - Lk / 2 if Lk >= l1 - l0 else l0
            w0 = min(max(w0, u0), u1 - Lk)
            return (w0, w0 + Lk), True
        chains = []
        if not a.no_lip_groups:
            lipk = [k for k in range(len(segs)) if islip(k)]; i = 0
            while i < len(lipk):
                ch, links = [lipk[i]], []
                while i + 1 < len(lipk):
                    how = link(lipk[i], lipk[i + 1])
                    if not how: break
                    links.append(how); ch.append(lipk[i + 1]); i += 1
                i += 1
                if len(ch) >= 2: chains.append((ch, links))
        inchain = {k for ch, _ in chains for k in range(ch[0], ch[-1] + 1)}
        single_trims = []
        for k, s_ in enumerate(segs):                         # single lip rows too (TRIM mode)
            if a.lip_max_speed > 0 and islip(k) and k not in inchain:
                (x0, x1), cut = lip_window(s_["it_idx"], s_["it_end_ms"] - s_["it_start_ms"])
                if cut:
                    s_["us_start_ms"], s_["us_end_ms"] = x0, x1; single_trims.append((s_["it_idx"], round(x1 - x0)))
        chain_groups = []
        for ch, links in chains:
            k0, k1 = ch[0], ch[-1]
            kn = [(segs[k0]["it_start_ms"], segs[k0]["us_start_ms"])]
            if a.lip_anchor == "start":
                # every lip sentence STARTS with its dub voice; its whole source sentence (and the pause / narration after
                # it) then runs until the next lip sentence starts with its voice -> the dub line's pause is used too
                kn += [(rows[segs[k]["it_idx"]]["dub_start"], rows[segs[k]["it_idx"]]["src_start"]) for k in ch[1:]]
            else:
                for q in range(len(ch) - 1):
                    rk, rm = rows[segs[ch[q]]["it_idx"]], rows[segs[ch[q + 1]]["it_idx"]]
                    if links[q] == "between": kn += [(rk["dub_end"], rk["src_end"]), (rm["dub_start"], rm["src_start"])]
                    else: kn.append(((rk["dub_end"] + rm["dub_start"]) / 2, (rk["src_end"] + rm["src_start"]) / 2))
            kn.append((segs[k1]["it_end_ms"], segs[k1]["us_end_ms"]))
            kn = [x for q_, x in enumerate(kn) if q_ == 0 or (x[0] > kn[q_ - 1][0] + 1 and x[1] > kn[q_ - 1][1] + 1)]
            kn[-1] = (segs[k1]["it_end_ms"], kn[-1][1])
            if a.lip_smooth_ms > 0 and len(kn) > 2:
                # Even out the speed: an inner knot may move up to --lip-smooth-ms (dub time) so that a piece playing much
                # faster or slower than its neighbours lends/borrows time from them (the lip sentence then starts that much
                # before/after its voice). Greedy, 20-ms steps, never makes the neighbour worse than the piece it helps.
                kn = [list(x) for x in kn]; t0s = [x[0] for x in kn]; lim = a.lip_smooth_ms
                sp = lambda j: (kn[j + 1][1] - kn[j][1]) / max(kn[j + 1][0] - kn[j][0], 1)
                bad = lambda v: abs(math.log(max(v, 1e-3)))
                for _ in range(4000):
                    j = max(range(len(kn) - 1), key=lambda j: bad(sp(j)))
                    if sp(j) <= a.lip_group_speed_ok and sp(j) >= 1 / a.lip_group_speed_ok: break
                    moved = False
                    fast = sp(j) > 1
                    for kk, dirn in ((j, -1), (j + 1, +1)):          # widen a fast piece outward / narrow a slow one inward
                        if not 0 < kk < len(kn) - 1: continue
                        step = 20 * (dirn if fast else -dirn)
                        nt = kn[kk][0] + step
                        if abs(nt - t0s[kk]) > lim or not (kn[kk - 1][0] + 200 < nt < kn[kk + 1][0] - 200): continue
                        old = kn[kk][0]; before = max(bad(sp(kk - 1)), bad(sp(kk)))
                        kn[kk][0] = nt
                        if max(bad(sp(kk - 1)), bad(sp(kk))) < before - 1e-6: moved = True; break
                        kn[kk][0] = old
                    if not moved: break
                kn = [tuple(x) for x in kn]
            members = list(range(k0, k1 + 1)); out_s = []; flags = []
            pieces = [(ta, tb, ua, ub) for (ta, ua), (tb, ub) in zip(kn, kn[1:])]
            if a.lip_max_speed > 0:
                # TRIM mode: every lip sentence plays exactly over its own dub voice; a sentence too long for its dub line
                # (faster than --lip-max-speed) is cut down to the part around its lip movement. The footage between two
                # lip sentences (pause + narration rows) fills the dub time between them; trimmed-off parts are skipped.
                L_ = []
                for q, k in enumerate(ch):                        # [dub start, dub end, src start, src end, sentence start, end, cut]
                    r = rows[segs[k]["it_idx"]]
                    d0 = segs[k0]["it_start_ms"] if q == 0 else r["dub_start"]
                    d1 = segs[k1]["it_end_ms"] if q == len(ch) - 1 else r["dub_end"]
                    L_.append([d0, d1, r["src_start"], r["src_end"], r["src_start"], r["src_end"], False])
                for q in range(len(ch) - 1):                      # back to back: the dub pause is shared halfway first
                    if links[q] != "between":
                        mt = (L_[q][1] + L_[q + 1][0]) / 2; L_[q][1] = L_[q + 1][0] = mt
                for q, k in enumerate(ch):                        # then each sentence vs its final dub length
                    (x0, x1), cut = lip_window(segs[k]["it_idx"], L_[q][1] - L_[q][0])
                    L_[q][2], L_[q][3], L_[q][6] = x0, x1, cut
                    if q == 0 and not cut: L_[q][2] = segs[k0]["us_start_ms"]
                    if q == len(ch) - 1 and not cut: L_[q][3] = segs[k1]["us_end_ms"]
                for q in range(len(ch) - 1):                      # back to back: the source pause is shared halfway too
                    if links[q] != "between":
                        mu = (L_[q][5] + L_[q + 1][4]) / 2
                        if not L_[q][6]: L_[q][3] = mu
                        if not L_[q + 1][6]: L_[q + 1][2] = mu
                pieces = []
                for q in range(len(ch)):
                    pieces.append(tuple(L_[q][:4]))
                    if q + 1 < len(ch) and links[q] == "between":
                        pieces.append((L_[q][1], L_[q + 1][0], L_[q][5], L_[q + 1][4]))
                pieces = [p_ for p_ in pieces if p_[1] - p_[0] > 1 and p_[3] - p_[2] > 1]
                trims_done = [(segs[k]["it_idx"], round(L_[q][3] - L_[q][2]), round(L_[q][5] - L_[q][4])) for q, k in enumerate(ch) if L_[q][6]]
                if trims_done: flags.append("trimmed lip sentences (row, ms kept, ms of sentence): " + str(trims_done))
            for ta, tb, ua, ub in pieces:
                mid = (ta + tb) / 2
                own = next((segs[j] for j in members if segs[j]["it_start_ms"] <= mid < segs[j]["it_end_ms"]), segs[k1])
                f = (tb - ta) / max(ub - ua, 1)
                out_s.append(dict(kind="clip", it_idx=own["it_idx"], it_start_ms=round(ta, 3), it_end_ms=round(tb, 3), us_start_ms=round(ua, 3),
                                  us_end_ms=round(ub, 3), it_text=own.get("it_text", ""), us_text=own.get("us_text", ""),
                                  stretch_factor=round(f, 3), note="lip group"))
                if f > 2.5 or f < 0.4: flags.append(f"IT#{own['it_idx']} piece plays x{f:.2f}")
            for j in members:                                 # members' footage = the pieces they own (for the free-footage checks)
                mine = [o for o in out_s if o["it_idx"] == segs[j]["it_idx"]]
                if mine: segs[j]["us_start_ms"], segs[j]["us_end_ms"] = min(o["us_start_ms"] for o in mine), max(o["us_end_ms"] for o in mine)
            nl = sum(1 for j in members if islip(j))
            it0, it1, us0, us1 = kn[0][0], kn[-1][0], kn[0][1], kn[-1][1]
            chain_groups.append(dict(rows=[segs[j]["it_idx"] for j in members], it0=it0, it1=it1, us0=us0, us1=us1, trims=[], flags=flags,
                                     note=(f"lip group: {nl} lip-sync rows + {len(members) - nl} narration rows as one continuous piece; "
                                           f"every lip sentence starts and ends with its dub voice"), removed=[], kept=us1 - us0,
                                     stretch=(it1 - it0) / max(us1 - us0, 1), removed_ms=0, segments=out_s, drift={}, max_drift=0,
                                     lip=True, lipgroup=True, why="lip group", first_seg=k0, break_before="lip group"))
        # A grouped row next to a lip-sync row now also carries the silence next to it. Instead of only slowing down, it
        # runs on into the FREE source footage between it and the lip clip (what comes right before/after in the source),
        # at most up to normal speed. Footage any other row uses is never taken.
        if a.exact_span:
            # Grouped (no-lip) rows play EXACTLY their own sentences' footage: a group = from its first row's source start to
            # its last row's source end (Whisper times), one continuous clip fitted to the dub time at one speed. No trimming,
            # no run-on into other footage (gap fill / bleed fix of the row-level manifest are not used for these rows).
            a.max_speedup = 1e9; a.no_lip_fill = True
            for k, s_ in enumerate(segs):
                r = rows[s_["it_idx"]]
                if k in inchain or islip(k) or s_["it_idx"] in repeat_rows or r["src_start"] is None: continue
                s_["us_start_ms"], s_["us_end_ms"] = r["src_start"], r["src_end"]
        def free(a0, a1, me):
            return a1 - a0 > 1 and not any(o is not me and min(o["us_end_ms"], a1) - max(o["us_start_ms"], a0) > 1 for o in segs)
        lipfill = []
        k = 0
        while k < len(segs) and not a.no_lip_fill:               # each run of grouped rows between lip-sync rows
            if islip(k) or k in inchain: k += 1; continue
            j = k
            while j < len(segs) and not (islip(j) or j in inchain): j += 1
            first, last = segs[k], segs[j - 1]
            run = segs[k:j]; k = j
            if any(s_["it_idx"] in repeat_rows for s_ in (first, last)): continue
            pl = segs.index(first) > 0; nl = j < len(segs)
            span = last["us_end_ms"] - first["us_start_ms"]          # a run plays as continuous footage (one or more groups)
            if span <= 0 or any(y["us_start_ms"] < x["us_end_ms"] - 40 for x, y in zip(run, run[1:])): continue
            need = (last["it_end_ms"] - first["it_start_ms"]) - span
            if not (pl or nl) or need <= 40: continue             # only where the run would play slower than normal
            p_, n_ = (segs[segs.index(first) - 1] if pl else None), (segs[j] if nl else None)
            rb = first["us_start_ms"] - p_["us_end_ms"] if p_ else 0
            ra = n_["us_start_ms"] - last["us_end_ms"] if n_ else 0
            rb = rb if rb > 0 and free(p_["us_end_ms"], first["us_start_ms"], first) else 0
            ra = ra if ra > 0 and free(last["us_end_ms"], n_["us_start_ms"], last) else 0
            ta = min(ra, need / 2); tb = min(rb, need - ta); ta = min(ra, need - tb)
            if ta + tb > 1:
                first["us_start_ms"] -= tb; last["us_end_ms"] += ta
                lipfill.append((first["it_idx"] if first is last else f"{first['it_idx']}-{last['it_idx']}", round(tb), round(ta)))
    if not lip: chains, inchain, chain_groups, lipfill, single_trims = [], set(), [], [], []
    n = len(segs); brk = {}
    for k in range(1, n):                                     # places a group may NEVER span
        x, y = segs[k - 1], segs[k]; g0, g1 = x["us_end_ms"], y["us_start_ms"]
        if x["it_idx"] in lip or y["it_idx"] in lip:
            brk[k] = "lip-sync row"
        elif x["it_idx"] in repeat_rows or y["it_idx"] in repeat_rows:
            brk[k] = "dub repeat"
        elif g1 < g0 - 40:
            brk[k] = "the Italian goes back in the US order"
        elif g1 - g0 > a.max_gap:
            brk[k] = f"US footage skips {(g1 - g0) / 1000:.1f}s here"
        elif any(j not in (k - 1, k) and s["it_idx"] not in repeat_rows and s["us_start_ms"] < g1 - 1 and s["us_end_ms"] > g0 + 1
                 for j, s in enumerate(segs)):
            brk[k] = "the US footage between these rows is used elsewhere"
    runs, cur = [], []
    for k in range(n):                                        # lip groups are already planned: never inside a block
        if k in inchain:
            if cur: runs.append(cur); cur = []
            continue
        if k in brk and cur: runs.append(cur); cur = []
        cur.append(k)
    if cur: runs.append(cur)
    queue = []
    for run in runs:                                          # balanced blocks of about --size rows
        m = len(run); kk = math.ceil(m / a.size); base, extra = divmod(m, kk); i = 0
        for g in range(kk):
            sz = base + (1 if g < extra else 0); queue.append((run[i:i + sz], None)); i += sz
    groups = []
    while queue:
        members, why = queue.pop(0)
        info = _group_plan([segs[j] for j in members], rows, a, repeat_rows, lip)
        if info["max_drift"] > a.max_drift and len(members) >= 4:
            h = len(members) // 2; tag = f"split: picture drifted {info['max_drift'] / 1000:.1f}s from the voice"
            queue[0:0] = [(members[:h], tag), (members[h:], tag)]
            continue
        info["why"] = why or ""; info["first_seg"] = members[0]
        groups.append(info)
    groups += chain_groups
    groups.sort(key=lambda g: g["it0"])
    for g in groups:
        k = g["first_seg"]
        if "break_before" not in g: g["break_before"] = brk.get(k, "start" if k == 0 else f"block of {a.size} rows")
    out = [s for g in groups for s in g["segments"]]
    nf = clamp_to_fade(out, source_fade_ms(a.work, cfg["src_video"]))
    if nf: print(f"{nf} clip(s) ended before the source's closing fade to black")
    for x, y in zip(out, out[1:]):
        assert abs(x["it_end_ms"] - y["it_start_ms"]) < 1, "group timeline not contiguous"
    assert abs(out[0]["it_start_ms"]) < 1 and abs(out[-1]["it_end_ms"] - total_ms) < 1
    gi = {id(s): i for i, g in enumerate(groups) for s in g["segments"]}
    n_trim = trim_shared_footage(out, gi, repeat_rows, lip)
    if n_trim:
        print(f"groups: trimmed {n_trim} shared-footage overlap(s) (lip window or out-of-order lines); not failing")
    leftover = [(x["it_idx"], y["it_idx"]) for x, y in group_footage_clash(out, gi, repeat_rows)]
    if leftover:
        print(f"groups: leftover overlap after trim {leftover[:5]} (kept the first owner)")
    save(a.work, "manifest_groups.json", dict(total_ms=total_ms, segments=out, bleed_report=[],
         groups=[{k: v for k, v in g.items() if k != "segments"} for g in groups]))
    review = a.review or (re.sub(r"_[a-z]{2}_[a-z]{2}_mapping[^/]*\.xlsx$", "_dynamic_groups_review.xlsx", cfg["sheet_out"])
                          if cfg.get("sheet_out") and re.search(r"_[a-z]{2}_[a-z]{2}_mapping[^/]*\.xlsx$", cfg["sheet_out"]) else os.path.join(a.work, "dynamic_groups_review.xlsx"))
    _groups_workbook(review, groups, rows, cfg.get("labels", "IT,US").split(","))
    cfg.update(manifest="manifest_groups.json", groups_review=os.path.abspath(review)); save(a.work, "config.json", cfg)
    n_trim = sum(1 for g in groups if g["removed_ms"] > 1)
    print(f"groups: {len(groups)} groups over {n} rows ({len(out)} segments) covering 0-{total_ms / 1000:.2f}s, 0 shared footage")
    print(f"  {n_trim} groups trimmed ({sum(max(g['removed_ms'], 0) for g in groups) / 1000:.1f}s of extra US removed), "
          f"{sum(1 for g in groups if g['stretch'] > 1.001)} stretched; worst stretch {max(g['stretch'] for g in groups):.2f}x slower, "
          f"fastest {1 / min(g['stretch'] for g in groups):.2f}x; max picture/voice drift {max(g['max_drift'] for g in groups) / 1000:.2f}s")
    for i, g in enumerate(groups):
        print(f"  G{i:02d} IT#{g['rows'][0]}-{g['rows'][-1]} it {g['it0'] / 1000:.1f}-{g['it1'] / 1000:.1f}s  us {g['us0'] / 1000:.1f}-"
              f"{g['us1'] / 1000:.1f}s  removed {max(g['removed_ms'], 0) / 1000:.2f}s  x{g['stretch']:.3f}  drift {g['max_drift'] / 1000:.2f}s"
              + (f"  [{g['break_before']}]" if not g["break_before"].startswith("block") else "")
              + ("  ** " + "; ".join(g["flags"]) if g["flags"] else ""))
    if lip:
        print(f"  {sum(1 for g in groups if g.get('lip') and not g.get('lipgroup'))} single lip-sync rows play their own clip; "
              f"{len(chain_groups)} lip groups play {sum(len(g['rows']) for g in chain_groups)} rows as continuous pieces "
              f"({sum(1 for g in chain_groups for r in g['rows'] if r in lip)} lip-sync + "
              f"{sum(1 for g in chain_groups for r in g['rows'] if r not in lip)} narration rows)")
        tr_ = [f for g in chain_groups for f in g["flags"] if f.startswith("trimmed")]
        if single_trims or tr_: print(f"  lip sentences trimmed to their lip part (max x{a.lip_max_speed}): single rows {single_trims}; in lip groups {tr_}")
        if lipfill: print(f"  {len(lipfill)} grouped rows next to a lip-sync row ran on into free source footage instead of slowing down "
                          f"(row, ms before, ms after): {lipfill}")
    print(f"saved manifest_groups.json (render/verify/bookends now use it) and {review}")

def _groups_workbook(path, groups, rows, L):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Dynamic groups"
    hf = Font(name="Arial", size=11, bold=True, color="FFFFFF"); hfill = PatternFill("solid", start_color="1F4E78")
    bf = Font(name="Arial", size=10); wrap = Alignment(wrap_text=True, vertical="top")
    yel = PatternFill("solid", start_color="FFEB9C")
    hdr = ["Group", f"{L[0]} rows", f"{L[0]} start", f"{L[0]} end", f"{L[0]} duration (s)", f"{L[1]} continuous span",
           f"{L[1]} span (s)", "Extra removed (s)", f"Removed {L[1]} intervals", "Trimmed rows (class, s)",
           "Whole-group stretch (>1 slower)", "Max picture/voice drift (s)", "Group starts because", "Flags / notes"]
    for ci, h in enumerate(hdr, 1):
        c = ws.cell(1, ci, h); c.font = hf; c.fill = hfill; c.alignment = wrap
    for ri, g in enumerate(groups, 2):
        vals = [f"G{ri - 2:02d}", f"{g['rows'][0]}-{g['rows'][-1]}", fmt(g["it0"]), fmt(g["it1"]), round((g["it1"] - g["it0"]) / 1000, 3),
                f"{fmt(g['us0'])}-{fmt(g['us1'])}", round((g["us1"] - g["us0"]) / 1000, 3), round(max(g["removed_ms"], 0) / 1000, 3),
                " | ".join(f"{fmt(lo)}-{fmt(hi)}" for lo, hi in g.get("removed", [])),
                " | ".join(f"IT#{t['row']} {t['cls']} {t['ms'] / 1000:.2f}" for t in g["trims"]),
                round(g["stretch"], 3), round(g["max_drift"] / 1000, 2), g["why"] or g["break_before"],
                "; ".join(g["flags"] + ([g["note"]] if g["note"] else []))]
        for ci, v in enumerate(vals, 1):
            c = ws.cell(ri, ci, v); c.font = bf; c.alignment = wrap
            if g["flags"]: c.fill = yel
    for ci, w in enumerate([7, 9, 11, 11, 10, 22, 9, 9, 40, 40, 12, 12, 30, 50], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
    ws.freeze_panes = "A2"
    ws2 = wb.create_sheet("Rows")
    hdr2 = ["#", "Group", f"{L[0]} start", f"{L[0]} end", f"{L[0]} text", f"{L[1]} text", "Class", "Trimmed (s)",
            "Picture vs voice (s, + = picture late)"]
    for ci, h in enumerate(hdr2, 1):
        c = ws2.cell(1, ci, h); c.font = hf; c.fill = hfill; c.alignment = wrap
    ri = 2
    for gi, g in enumerate(groups):
        tr = {t["row"]: t for t in g["trims"]}
        for idx in g["rows"]:
            r = rows[idx]
            vals = [idx, f"G{gi:02d}", fmt(r["dub_start"]), fmt(r["dub_end"]), r["dub_text"], r.get("src_text") or "",
                    speech_or_narration(r.get("src_text") or ""), round(tr[idx]["ms"] / 1000, 2) if idx in tr else "",
                    round(g["drift"].get(idx, 0) / 1000, 2)]
            for ci, v in enumerate(vals, 1):
                c = ws2.cell(ri, ci, v); c.font = bf; c.alignment = wrap
            ri += 1
    for ci, w in enumerate([6, 7, 11, 11, 45, 45, 18, 10, 14], 1):
        ws2.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
    ws2.freeze_panes = "A2"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True); wb.save(path)

# ---------------------------------------------------------------------------------
# 11. translate: the dub SRT -> English SRT through Google, sentence by sentence, mapped back onto the
#     original cards (same count, same times). Cached, batched, names kept as in the dub.
# ---------------------------------------------------------------------------------
class TranslateBlocked(Exception):
    pass

def _http(url, params=None, json_body=None, timeout=30):
    import urllib.request, urllib.parse
    if params: url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    hdr = {"User-Agent": "Mozilla/5.0 (Macintosh) dubstitch"}; body = None
    if json_body is not None:
        body = json.dumps(json_body).encode(); hdr["Content-Type"] = "application/json; charset=utf-8"
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=hdr), timeout=timeout) as r:
        return r.read().decode("utf-8")

def _with_retry(fn, what):
    import urllib.error
    for attempt in range(4):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt == 3:
                raise TranslateBlocked(f"Google refused {what} (HTTP {e.code}). The free endpoint blocks an address after many "
                                       "requests; wait an hour, or use --engine cloud with an API key, or --export-todo/--import.")
            if e.code not in (403, 429, 500, 502, 503): raise
        except (OSError, ValueError):
            if attempt == 3: raise
        time.sleep(3 * 2 ** attempt)

def _gtx_batch(texts, sl, tl):
    """Free Google endpoint (client=gtx). Several sentences per request, one per line."""
    def one():
        raw = _http("https://translate.googleapis.com/translate_a/single",
                    params=dict(client="gtx", sl=sl, tl=tl, dt="t", q="\n".join(texts)))
        data = json.loads(raw)
        return "".join(seg[0] for seg in data[0] if seg and seg[0]).split("\n")
    out = [o.strip() for o in _with_retry(one, f"{len(texts)} sentences")]
    if len(out) != len(texts):                                # Google merged lines: fall back to one per request
        out = []
        for t in texts:
            out += _gtx_batch([t], sl, tl) if len(texts) > 1 else [""]
            time.sleep(0.3)
    return out

def _cloud_batch(texts, sl, tl, key):
    """Official Cloud Translation API v2 (needs an API key)."""
    raw = _with_retry(lambda: _http("https://translation.googleapis.com/language/translate/v2", params=dict(key=key),
                                    json_body=dict(q=texts, source=sl, target=tl, format="text")), f"{len(texts)} sentences")
    return [t["translatedText"] for t in json.loads(raw)["data"]["translations"]]

def translate_texts(texts, a, cache, cpath):
    todo = [t for t in dict.fromkeys(texts) if t not in cache]
    if not todo: return 0
    if a.engine == "file":
        raise SystemExit(f"{len(todo)} sentences have no translation in --import; e.g. {todo[:2]}")
    key = a.api_key or os.environ.get("GOOGLE_TRANSLATE_API_KEY")
    if a.engine == "cloud" and not key:
        raise SystemExit("--engine cloud needs --api-key or the GOOGLE_TRANSLATE_API_KEY environment variable")
    batches, cur = [], []
    for t in todo:                                             # keep each request URL comfortably short
        if cur and (len("\n".join(cur + [t]).encode()) > a.max_chars or len(cur) >= 100):
            batches.append(cur); cur = []
        cur.append(t)
    if cur: batches.append(cur)
    for b in batches:
        res = _cloud_batch(b, a.sl, a.tl, key) if a.engine == "cloud" else _gtx_batch(b, a.sl, a.tl)
        cache.update(zip(b, res)); json.dump(cache, open(cpath, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        time.sleep(a.pause)
    return len(batches)

def split_by_shares(text, shares):
    """Split a translated sentence into len(shares) consecutive parts, sized like the dub cards it spans,
    at word boundaries, preferring a boundary right after punctuation."""
    k = len(shares)
    if k == 1: return [text]
    words = text.split()
    if len(words) <= k: return words + [""] * (k - len(words))
    ends, pos = [], 0
    for w in words[:-1]:
        pos += len(w); ends.append(pos); pos += 1
    L = len(text); tot = sum(shares) or 1; cum = 0; cuts = []; prev = -1
    for j in range(k - 1):
        cum += shares[j]; target = cum / tot * L
        lo, hi = prev + 1, len(ends) + 1 - (k - 1 - j)          # leave at least one word for every later part
        best = min(range(lo, hi), key=lambda i: abs(ends[i] - target) / L - (0.06 if re.search(r"[,;:.!?»”\"]$", words[i]) else 0))
        cuts.append(best); prev = best
    parts, start = [], 0
    for c in cuts:
        parts.append(" ".join(words[start:c + 1])); start = c + 1
    parts.append(" ".join(words[start:]))
    return parts

def proper_names(sentences):
    """Character names in the dub: capitalised words that recur mid-sentence, never appear in lower case, and stand
    on their own at least once (or only ever follow such a name, as a surname: 'Emma Reali'). Words that only appear inside a
    longer capitalised name ('Gruppo Reali', 'Polo dell'Innovazione Lombardo') are left to the translation."""
    cnt, lower, alone, after, before = defaultdict(int), set(), set(), defaultdict(set), set()
    isname = lambda w: len(w) > 2 and w[0].isupper() and w[1:].islower()
    for s in sentences:
        for t in re.findall(r"[^\W\d_]+", s):
            if t.islower(): lower.add(t)
        words = [re.sub(r"^[^\W\d_]*'|[^\w]+$|^[^\w]+", "", w) for w in s.split()]
        for i, w in enumerate(words):
            if not isname(w): continue
            if i > 0: cnt[w] += 1
            prev = words[i - 1] if i else ""; nxt = words[i + 1] if i + 1 < len(words) else ""
            if not isname(nxt) and not (i and isname(prev)): alone.add(w)
            if i and isname(prev): after[w].add(prev)
            if isname(nxt): before.add(w)
    cand = {t for t, c in cnt.items() if c >= 2 and t.lower() not in lower}
    return sorted(t for t in cand if t in alone or (after[t] & alone & cand and t not in before))

def cmd_translate(a):
    cfg = load(a.work, "config.json")
    src = a.srt or cfg["dub_srt"]
    cues = parse_srt(src)                                      # same cleaning as the burned dub subtitles
    pieces, spans, pos = [], [], 0
    for c in cues:
        if pieces: pieces.append(" "); pos += 1
        spans.append((pos, pos + len(c.text))); pieces.append(c.text); pos += len(c.text)
    full = "".join(pieces)
    # a closing quote stays with its own sentence: «...Federico!» | Priscilla ...
    bounds = sorted(set([0] + [m.start() + len(m.group(0).rstrip()) for m in _SENT_END.finditer(full)] + [len(full)]))
    sents = []
    for s0, s1 in zip(bounds, bounds[1:]):
        t = full[s0:s1]; l = len(t) - len(t.lstrip()); t = t.strip()
        if t: sents.append((s0 + l, s0 + l + len(t), t))
    cpath = os.path.join(a.work, f"translations_{a.sl}_{a.tl}.json")
    cache = json.load(open(cpath, encoding="utf-8")) if os.path.exists(cpath) else {}
    if a.import_file:
        if a.import_file.endswith(".json"): cache.update(json.load(open(a.import_file, encoding="utf-8")))
        else:
            for ln in open(a.import_file, encoding="utf-8"):
                if "\t" in ln:
                    k, v = ln.rstrip("\n").split("\t", 1)
                    if v.strip(): cache[k] = v.strip()
        json.dump(cache, open(cpath, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    texts = [t for _, _, t in sents]
    names = proper_names(texts) if a.keep_names else []
    if a.export_todo:
        todo = [t for t in dict.fromkeys(texts + names) if t not in cache]
        open(a.export_todo, "w", encoding="utf-8").write("".join(f"{t}\t\n" for t in todo))
        print(f"wrote {len(todo)} untranslated lines to {a.export_todo} (fill the 2nd column, then --engine file --import it)"); return
    try:
        nreq = translate_texts(texts + names, a, cache, cpath)
    except TranslateBlocked as e:
        raise SystemExit(f"TRANSLATION BLOCKED: {e}\n(progress so far is cached in {cpath})")
    gloss = {}
    if a.glossary:
        for ln in open(a.glossary, encoding="utf-8"):
            if "\t" in ln:
                k, v = ln.rstrip("\n").split("\t", 1); gloss[k.strip()] = v.strip()
    name_map = {}
    for nm in names:                                           # e.g. Google turns Federico into Frederick: undo it
        en = cache.get(nm, nm).strip(" .")
        if en != nm and re.fullmatch(r"[A-Z][^\W\d_]+", en): name_map[en] = nm
    ctx_fixed = defaultdict(int)
    def fix(t, it_sent=""):
        for en, it in name_map.items():                       # only where the Italian sentence has that name
            if it in it_sent: t = re.sub(rf"\b{re.escape(en)}\b", it, t)
        for nm in names:                                       # in context Google may still anglicise a name
            if nm in it_sent and nm not in t:                  # (Federico -> Frederick): put the dub's name back
                cands = [w for w in set(re.findall(r"\b[A-Z][^\W\d_]{2,}\b", t)) if w not in it_sent
                         and difflib.SequenceMatcher(None, w.lower(), nm.lower()).ratio() >= 0.6]
                if len(cands) == 1:
                    t = re.sub(rf"\b{re.escape(cands[0])}\b", nm, t); ctx_fixed[f"{cands[0]}->{nm}"] += 1
        for k, v in gloss.items(): t = re.sub(rf"\b{re.escape(k)}\b", v, t)
        return t
    per_cue = [[] for _ in cues]
    for s0, s1, t in sents:
        hit = [(i, min(s1, c1) - max(s0, c0)) for i, (c0, c1) in enumerate(spans) if min(s1, c1) > max(s0, c0)]
        parts = split_by_shares(fix(cache[t], t), [max(n, 1) for _, n in hit])
        for (i, _), p in zip(hit, parts):
            if p: per_cue[i].append(p)
    lines, empty = [], 0
    for i, c in enumerate(cues):
        txt = " ".join(per_cue[i]).strip()
        if not txt:
            empty += 1; txt = " ".join(per_cue[i - 1]) if i else " ".join(per_cue[i + 1] if i + 1 < len(cues) else [])
        lines.append(f"{i + 1}\n{_srt_ts(c.start_ms)} --> {_srt_ts(c.end_ms)}\n{txt}\n")
    out = os.path.abspath(a.out); os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(lines))
    cfg = load(a.work, "config.json")
    cfg["translation"] = dict(out=out, source=os.path.abspath(src), engine=a.engine, sl=a.sl, tl=a.tl, cues=len(cues),
                              sentences=len(sents), names_kept=name_map, names_fixed_in_context=dict(ctx_fixed))
    save(a.work, "config.json", cfg)
    print(f"translated {len(sents)} sentences ({a.sl}->{a.tl}, engine {a.engine}, {nreq} requests, rest from cache) onto "
          f"{len(cues)} cards with the dub's exact times -> {out}")
    if name_map or ctx_fixed: print(f"names kept as in the dub: {name_map} {dict(ctx_fixed) if ctx_fixed else ''}")
    if empty: print(f"{empty} cards got no words of their own (very short card): they repeat the neighbouring text")
    if a.engine == "gtx": print("note: text was sent to Google's free translate endpoint (translate.googleapis.com, client=gtx)")

def _srt_ts(ms):
    ms = int(round(ms)); return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

# ---------------------------------------------------------------------------------
# 12. check: final verification of the whole episode package
# ---------------------------------------------------------------------------------
def cmd_captions(a):
    """Dynamic subtitles (HIH), step 1: read the source video's burned-in captions (tesseract OCR, 4 frames/s, resumable)
    -> W/src_captions.json: every caption card with its time, number of lines and position."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import dynsubs
    cfg = load(a.work, "config.json") if os.path.exists(os.path.join(a.work, "config.json")) else {}
    video = a.video or cfg.get("src_video")
    if not video: raise SystemExit("captions: pass --video (no config.json yet)")
    if not dynsubs.read_captions(a.work, video, fps=a.fps, workers=a.workers, max_seconds=a.max_seconds):
        print("not finished -- run the same captions command again (it resumes)"); sys.exit(3)

def cmd_dynsrt(a):
    """Dynamic subtitles (HIH), step 2: the dub words grouped into cards that fill the strip (2 lines; 3 where the footage
    on screen carries a 3-line source caption), each word at its own time -> the dynamic SRT + W/dynsubs_plan.json."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import dynsubs
    cfg = load(a.work, "config.json")
    for need in ("manifest_groups.json", "src_captions.json"):
        if not os.path.exists(os.path.join(a.work, need)): raise SystemExit(f"dynsrt needs W/{need} (run groups and captions first)")
    W, H, _ = probe_video(cfg["src_video"])
    words = [tuple(w) for w in json.load(open(a.words, encoding="utf-8"))]
    dynsubs.make_plan(a.work, words, a.out, find_font(a.font, a.size), (W, H), size=a.size)
    cfg = load(a.work, "config.json"); cfg["dynamic_srt"] = os.path.abspath(a.out); save(a.work, "config.json", cfg)

def cmd_check(a):
    from PIL import Image, ImageDraw
    cfg = load(a.work, "config.json"); bad = []
    clean = cfg.get("clean_final"); subs = cfg.get("subs_outputs") or {}
    vids = ([("clean", clean)] if clean else []) + [(os.path.basename(v["srt"]), p) for p, v in subs.items()]
    if not clean or not os.path.exists(clean): bad.append("no clean master (run `bookends --out ...`)")
    if len([p for p in subs if os.path.exists(p)]) < a.expect_subs: bad.append(f"only {len(subs)} subtitled videos, expected {a.expect_subs}")
    ref = probe_duration_ms(clean) if clean and os.path.exists(clean) else None
    FPS = out_fps(cfg)
    for name, p in vids:
        if not os.path.exists(p): bad.append(f"missing {p}"); continue
        st = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", p],
                            capture_output=True, text=True).stdout.split()
        d = probe_duration_ms(p)
        ok = "video" in st and "audio" in st and (ref is None or abs(d - ref) <= 2 * 1000 / FPS + 60)
        print(f"{'OK ' if ok else 'BAD'} {os.path.basename(p)}: {d / 1000:.3f}s, streams {sorted(set(st))}")
        if not ok: bad.append(f"{os.path.basename(p)}: streams {st}, {d} ms vs clean {ref} ms")
    for label, p in (("mapping sheet", cfg.get("sheet_out")), ("dynamic-group review", cfg.get("groups_review")),
                     ("translated SRT", (cfg.get("translation") or {}).get("out"))):
        good = bool(p) and os.path.exists(p)
        print(f"{'OK ' if good else 'BAD'} {label}: {p}")
        if not good: bad.append(f"{label} missing")
    if vids and ref:
        cues = parse_srt(cfg["dub_srt"]); c = cues[min(2, len(cues) - 1)]
        t = cfg.get("opening_s", 0) + (c.start_ms + c.end_ms) / 2000
        sheet = Image.new("RGB", (len(vids) * 400, 430), "white"); dr = ImageDraw.Draw(sheet)
        for k, (name, p) in enumerate(vids):
            png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", p, "-frames:v", "1", "-vf", "scale=400:400",
                                  "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
            if png: sheet.paste(Image.open(io.BytesIO(png)).convert("RGB"), (k * 400, 30))
            dr.text((k * 400 + 6, 8), f"{name} @ {t:.1f}s", fill="black")
        qp = os.path.join(a.work, "final_check.jpg"); sheet.save(qp, quality=85)
        print(f"same moment in every video (look at it: clean = no strip, others = their subtitle): {qp}")
    print("FINAL CHECK " + ("PASSED" if not bad else "FAILED:\n  " + "\n  ".join(bad)))
    if bad: sys.exit(1)

# ---------------------------------------------------------------------------------
# 13. speech: which source lines are spoken on screen (direct speech in quotes) vs narration
# ---------------------------------------------------------------------------------
_Q_OPEN, _Q_TOGGLE = "„", "\"“”«»"     # „ always opens; " “ ” « » toggle

def quote_shares(texts):
    """Share of letters inside quotation marks for each text, reading them in order (a quote may span lines).
    A quote mark opens or closes by position (after a space = opens, after a word = closes), so a quote mark
    missing in the SRT cannot flip the rest of the episode. A closing mark with no open quote counts the line
    up to it as speech (the opening mark was missing)."""
    inside, out = False, []
    for t in texts:
        n_in = n_all = 0; first = last = None
        for k, ch in enumerate(t):
            if ch == _Q_OPEN or ch in _Q_TOGGLE:
                prev = t[k - 1] if k else " "; nxt = t[k + 1] if k + 1 < len(t) else " "
                if ch == _Q_OPEN or (prev.isspace() or prev in ":(-\u2013\u2014") and not nxt.isspace():
                    inside = True
                elif not prev.isspace():
                    if not inside and n_all:                     # closing mark, but no quote was open
                        n_in = n_all; first = 0; last = k - 1
                    inside = False
                else:
                    inside = not inside
                continue
            if ch.isalpha():
                n_all += 1
                if inside:
                    n_in += 1; first = k if first is None else first; last = k
        out.append((n_in / max(n_all, 1), first, last, len(t)))
    return out

def cmd_speech(a):
    """Classify every SOURCE sentence: 'speech' (a character says it -> lips may move on screen), 'mixed'
    (quote + narration in one sentence) or 'narration'. Writes <work>/speech.json for lipdetect.py."""
    P = load(a.work, "prep.json"); src = [Cue(**c) for c in P["src"]]
    rows = []
    for j, (c, (sh, f, l, n)) in enumerate(zip(src, quote_shares([c.text for c in src]))):
        cls = "speech" if sh >= a.speech_share else ("mixed" if sh >= a.mixed_share else "narration")
        d = c.end_ms - c.start_ms
        # time span of the quoted words, estimated from their position in the line (lipdetect searches the whole line anyway)
        q0 = c.start_ms + d * (f or 0) / max(n, 1); q1 = c.start_ms + d * ((l if l is not None else n - 1) + 1) / max(n, 1)
        rows.append(dict(idx=j, start_ms=c.start_ms, end_ms=c.end_ms, text=c.text, quote_share=round(sh, 2), cls=cls,
                         quote_ms=[round(q0), round(q1)] if cls != "narration" else None))
    dp = os.path.join(a.work, "decisions.json")                       # the person's reading wins: "speech": {"20": "mixed"}
    over = {int(k): v for k, v in (json.load(open(dp, encoding="utf-8")).get("speech", {}) if os.path.exists(dp) else {}).items()}
    for j, v in over.items():
        if v not in ("speech", "mixed", "narration"): raise SystemExit(f"decisions.json speech: SRC#{j} -> {v!r}?")
        rows[j]["cls"] = v; rows[j]["override"] = True
        if v != "narration" and not rows[j]["quote_ms"]: rows[j]["quote_ms"] = [rows[j]["start_ms"], rows[j]["end_ms"]]
    save(a.work, "speech.json", dict(rows=rows, src_video=load(a.work, "config.json")["src_video"]))
    n = defaultdict(int)
    for r in rows: n[r["cls"]] += 1
    tot = defaultdict(float)
    for r in rows: tot[r["cls"]] += (r["end_ms"] - r["start_ms"]) / 1000
    print(f"source lines: {n['speech']} speech ({tot['speech']:.0f}s), {n['mixed']} mixed ({tot['mixed']:.0f}s), "
          f"{n['narration']} narration ({tot['narration']:.0f}s) -> {os.path.join(a.work, 'speech.json')}")
    for r in rows:
        if r["cls"] != "narration":
            print(f"  SRC#{r['idx']:3d} {r['cls']:6s} {r['quote_share']:.2f}{' (set by hand)' if r.get('override') else ''} [{fmt(r['start_ms'])}-{fmt(r['end_ms'])}] {r['text'][:80]!r}")

# ---------------------------------------------------------------------------------
# 14. lipsync: dub rows whose source footage shows the character's lips moving keep their OWN clip
#     (out of the 7-8-row groups), timed so the lip movement plays while the dub line is spoken.
#     Needs <work>/speech.json (`speech`) and <work>/lips/*.json (lipdetect.py: TalkNet-ASD + Face Mesh).
# ---------------------------------------------------------------------------------
def lip_intervals(d, a):
    """Talking stretches (source seconds) inside one source line, from the per-frame measures of lipdetect.py:
    lips visibly moving (Face Mesh opening changes) OR TalkNet says a face speaks (in sync, or by picture alone)."""
    import numpy as np
    n = len(d.get("scores") or []); t0 = d["window"][0]
    if not n: return []
    on = np.zeros(n, bool)
    if a.use in ("mouth", "all") and d.get("mouth"): on |= np.array(d["mouth"][:n] + [0] * (n - len(d["mouth"][:n]))) >= a.mouth_thr
    if a.use in ("talknet", "all"):
        on |= np.array(d["scores"]) >= 0
        if d.get("visual"): on |= np.array(d["visual"][:n]) >= 0
    lo = max(int(round((d["start_s"] - t0) * 25)), 0); hi = min(int(round((d["end_s"] - t0) * 25)), n)
    ivs, k = [], lo
    while k < hi:
        if not on[k]: k += 1; continue
        j = k
        while j < hi and on[j]: j += 1
        if ivs and k - ivs[-1][1] <= a.merge * 25: ivs[-1][1] = j
        else: ivs.append([k, j])
        k = j
    return [(t0 + x / 25, t0 + y / 25) for x, y in ivs if y - x >= a.min_len * 25]

def merged_lip_intervals(work, a):
    """All lipdetect.py --range results in <work>/lips_range (any pieces of the source video) -> the talking stretches
    (seconds) over the whole video, and which 1/25-s frames were analysed at all."""
    import numpy as np
    rd = os.path.join(work, "lips_range")
    files = sorted(f for f in os.listdir(rd) if f.startswith("range_") and f.endswith(".json")) if os.path.isdir(rd) else []
    if not files: raise SystemExit("no lipdetect.py --range results in <work>/lips_range -- scan the source video first")
    T = max(json.load(open(os.path.join(rd, f)))["window"][1] for f in files); n = int(T * 25) + 2
    sc, vi, mo = np.full(n, -9.0), np.full(n, -9.0), np.zeros(n); covered = np.zeros(n, bool)
    for f in files:
        d = json.load(open(os.path.join(rd, f))); k0 = int(round(d["window"][0] * 25))
        for arr, key in ((sc, "scores"), (vi, "visual"), (mo, "mouth")):
            v = np.array(d[key][:n - k0]); arr[k0:k0 + len(v)] = np.maximum(arr[k0:k0 + len(v)], v)
        covered[k0:k0 + len(d["scores"])] = True
    d = dict(window=[0, n / 25], start_s=0, end_s=n / 25, scores=list(sc), visual=list(vi), mouth=list(mo))
    return lip_intervals(d, a), covered

def cmd_lipsync(a):
    """THE VIDEO DECIDES (not the quote marks): a source sentence is 'speaking on screen' when the lip scan of the whole
    source video (lipdetect.py --range) finds >= --min-lip seconds of moving lips inside its Whisper time. Every dub row
    matched to such a sentence becomes a lip-sync row: it plays that sentence's COMPLETE clip (its exact Whisper time),
    on its own, outside the groups. Its slot is exactly its own spoken Italian time, so the clip starts with the voice;
    the silence next to it goes to the neighbouring grouped row (between two lip rows, the clip runs on into the free
    source footage around its sentence at the same speed)."""
    cfg = cfg_get(a)
    P = load(a.work, "prep.json"); src = [Cue(**c) for c in P["src"]]
    R = load(a.work, "rows.json"); rows = {r["idx"]: r for r in R["rows"]}; repeat_rows = {int(k) for k in R["repeat"]}
    M = load(a.work, "manifest.json"); segs = M["segments"]; src_total = probe_duration_ms(cfg["src_video"])
    iv, covered = merged_lip_intervals(a.work, a)
    sent = []
    for j, c in enumerate(src):
        s, e = c.start_ms / 1000, c.end_ms / 1000
        if not covered[int(s * 25):max(int(e * 25), int(s * 25) + 1)].all():
            raise SystemExit(f"source sentence #{j} ({s:.1f}-{e:.1f}s) is not covered by the lip scan yet -- finish lipdetect.py --range")
        hits = [(max(x, s), min(y, e)) for x, y in iv if min(y, e) - max(x, s) > 0]
        sec = sum(y - x for x, y in hits)
        sent.append(dict(idx=j, start_ms=c.start_ms, end_ms=c.end_ms, text=c.text, lips=[[round(x, 2), round(y, 2)] for x, y in hits],
                         lip_s=round(sec, 2), speaking=sec >= a.min_lip))
    speaking = {d["idx"] for d in sent if d["speaking"]}
    lipset = {s_["it_idx"] for s_ in segs if rows[s_["it_idx"]]["src"] is not None and s_["it_idx"] not in repeat_rows
              and any(j in speaking for j in range(rows[s_["it_idx"]]["src"], rows[s_["it_idx"]]["src_last"] + 1))}
    # footage already taken: grouped rows keep their manifest windows; lip rows their sentence (then their planned window)
    taken = {s_["it_idx"]: ((rows[s_["it_idx"]]["src_start"], rows[s_["it_idx"]]["src_end"]) if s_["it_idx"] in lipset
                            else (s_["us_start_ms"], s_["us_end_ms"])) for s_ in segs if s_["it_idx"] not in repeat_rows}
    plan = {}
    for k, s_ in enumerate(segs):
        i = s_["it_idx"]
        if i not in lipset: continue
        r = rows[i]; d0, d1 = r["dub_start"], r["dub_end"]
        prev_lip = k > 0 and segs[k - 1]["it_idx"] in lipset; next_lip = k + 1 < len(segs) and segs[k + 1]["it_idx"] in lipset
        s0 = s_["it_start_ms"] if (k == 0 or prev_lip) else d0       # own spoken time; silence -> the grouped neighbour
        s1 = s_["it_end_ms"] if (k == len(segs) - 1 or next_lip) else d1
        others = [w for o, w in taken.items() if o != i]
        u0, u1 = r["src_start"], r["src_end"]
        lo = max([e for b, e in others if e <= u0 + 1], default=0); hi = min([b for b, e in others if b >= u1 - 1], default=src_total)
        lo, hi = min(lo, u0), max(hi, u1)
        f = (d1 - d0) / max(u1 - u0, 1)                              # Italian voice length / German sentence length
        w0 = max(u0 - (d0 - s0) / f, lo); w1 = min(u1 + (s1 - d1) / f, hi)
        stretch = (s1 - s0) / max(w1 - w0, 1)
        off = s0 + (u0 - w0) * stretch - d0                          # German sentence start vs Italian voice start
        taken[i] = (w0, w1)
        js = list(range(r["src"], r["src_last"] + 1))
        lips = [l for j in js for l in sent[j]["lips"]]
        plan[str(i)] = dict(it_idx=i, slot=[round(s0, 3), round(s1, 3)], speech=[d0, d1], sentence=[round(u0, 3), round(u1, 3)],
                            src_lines=js, lips=lips, lip_s=round(sum(y - x for x, y in lips), 2), window=[round(w0, 3), round(w1, 3)],
                            stretch=round(stretch, 3), sentence_offset_ms=round(off),
                            note=(f"lip-sync row: DE sentence(s) {js} {fmt(u0)}-{fmt(u1)} (Whisper time), lips {lips}, "
                                  f"played whole at x{stretch:.2f}"))
    save(a.work, "lipsync.json", dict(settings=dict(rule="video", use=a.use, mouth_thr=a.mouth_thr, min_lip=a.min_lip),
                                      sentences=sent, intervals=[[round(x, 2), round(y, 2)] for x, y in iv], rows=plan))
    # ---- review workbook ----
    out = a.review or (re.sub(r"_[a-z]{2}_[a-z]{2}_mapping[^/]*\.xlsx$", "_lipsync_review.xlsx", cfg["sheet_out"]) if cfg.get("sheet_out")
                       else os.path.join(a.work, "lipsync_review.xlsx"))
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    L = cfg.get("labels", "IT,US").split(",")
    hf = Font(name="Arial", size=11, bold=True, color="FFFFFF"); hfill = PatternFill("solid", start_color="1F4E78")
    bf = Font(name="Arial", size=10); wrap = Alignment(wrap_text=True, vertical="top"); green = PatternFill("solid", start_color="C6EFCE")
    by_src = defaultdict(list)
    for r in rows.values():
        if r["src"] is not None:
            for j in range(r["src"], r["src_last"] + 1): by_src[j].append(r["idx"])
    def sheet(ws, hdr, data, widths, hi_col=None):
        for ci, h in enumerate(hdr, 1):
            c = ws.cell(1, ci, h); c.font = hf; c.fill = hfill; c.alignment = Alignment(wrap_text=True)
        for ri, vals in enumerate(data, 2):
            for ci, v in enumerate(vals, 1):
                c = ws.cell(ri, ci, v); c.font = bf; c.alignment = wrap
                if hi_col is not None and vals[hi_col] == "speaking on screen": c.fill = green
        for ci, w in enumerate(widths, 1): ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
        ws.freeze_panes = "A2"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = f"{L[1]} sentences (video)"
    sheet(ws, ["#", "Spoken start (s)", "Spoken end (s)", f"{L[1]} sentence", "Lip movement inside it (s)", "Seconds of lips",
               "Type after video", f"{L[0]} rows using it"],
          [[d["idx"], d["start_ms"] / 1000, d["end_ms"] / 1000, d["text"], ", ".join(f"{x:.2f}-{y:.2f}" for x, y in d["lips"]) or "-",
            d["lip_s"], "speaking on screen" if d["speaking"] else "no lip movement", ", ".join(map(str, by_src.get(d["idx"], []))) or "unused"]
           for d in sent], [6, 10, 10, 60, 26, 9, 18, 12], hi_col=6)
    ws2 = wb.create_sheet("Lip-sync rows")
    sheet(ws2, ["#", f"{L[0]} spoken", f"{L[0]} text", f"{L[1]} sentence(s)", f"{L[1]} text", f"{L[1]} sentence time (Whisper)",
                "Lips moving inside (s)", f"{L[1]} clip played", "Speed (>1 slower)", f"{L[1]} start vs {L[0]} voice start (s)"],
          [[p["it_idx"], f"{fmt(p['speech'][0])}-{fmt(p['speech'][1])}", rows[p["it_idx"]]["dub_text"], ", ".join(f"#{j}" for j in p["src_lines"]),
            rows[p["it_idx"]]["src_text"], f"{fmt(p['sentence'][0])}-{fmt(p['sentence'][1])}", p["lip_s"],
            f"{fmt(p['window'][0])}-{fmt(p['window'][1])}", p["stretch"], round(p["sentence_offset_ms"] / 1000, 2)]
           for p in sorted(plan.values(), key=lambda p: p["it_idx"])], [6, 19, 40, 10, 40, 19, 9, 19, 9, 11])
    ws3 = wb.create_sheet("Lip movements")
    sheet(ws3, ["#", "Start (s)", "End (s)", "Seconds", f"{L[1]} sentence(s)"],
          [[k + 1, round(x, 2), round(y, 2), round(y - x, 2),
            ", ".join(str(d["idx"]) for d in sent if min(y, d["end_ms"] / 1000) - max(x, d["start_ms"] / 1000) > 0) or "pause between sentences"]
           for k, (x, y) in enumerate(iv)], [6, 10, 10, 9, 20])
    wb.save(out)
    cfg["lipsync_review"] = os.path.abspath(out); save(a.work, "config.json", cfg)
    if cfg.get("sheet_out") and os.path.exists(cfg["sheet_out"]):          # mark every row in the mapping sheet too
        wb2 = openpyxl.load_workbook(cfg["sheet_out"]); ws = wb2["Mapping"]
        c = ws.cell(1, 11, f"Type after video ({L[1]} lips)"); c.font = hf; c.fill = hfill; c.alignment = Alignment(wrap_text=True)
        for rr in range(2, ws.max_row + 1):
            i = ws.cell(rr, 1).value
            if not isinstance(i, int): continue
            p = plan.get(str(i))
            c = ws.cell(rr, 11, f"speaking on screen ({p['lip_s']}s of lips) -> own clip {fmt(p['window'][0])}-{fmt(p['window'][1])}"
                        if p else "no lip movement -> grouped"); c.font = bf; c.alignment = wrap
            if p: c.fill = green
        ws.column_dimensions["K"].width = 34; wb2.save(cfg["sheet_out"])
    st = sorted(p["stretch"] for p in plan.values())
    print(f"lip scan: {len(iv)} stretches of moving lips ({sum(y - x for x, y in iv):.0f}s); {len(speaking)} of {len(sent)} {L[1]} sentences are "
          f"speaking on screen (>= {a.min_lip}s of moving lips inside their Whisper time)")
    print(f"lip-sync rows: {len(plan)} of {len(segs)} {L[0]} rows play their {L[1]} sentence's whole clip, outside the groups"
          + (f"; speed x{st[0]}..x{st[-1]} (median x{st[len(st) // 2]})" if st else ""))
    for p in sorted(plan.values(), key=lambda p: p["it_idx"]):
        print(f"  {L[0]}#{p['it_idx']:3d} {fmt(p['slot'][0])}-{fmt(p['slot'][1])} <- {L[1]}#{p['src_lines']} sentence {p['sentence'][0] / 1000:.2f}-"
              f"{p['sentence'][1] / 1000:.2f}s, clip {p['window'][0] / 1000:.2f}-{p['window'][1] / 1000:.2f}s x{p['stretch']}"
              + (f"  start offset {p['sentence_offset_ms'] / 1000:+.2f}s" if abs(p['sentence_offset_ms']) >= 40 else "")
              + f"  {rows[p['it_idx']]['dub_text'][:45]!r}")
    unused_sp = [d["idx"] for d in sent if d["speaking"] and not by_src.get(d["idx"])]
    if unused_sp: print(f"speaking {L[1]} sentences no {L[0]} row uses: {unused_sp}")
    print(f"saved lipsync.json and {out}; run `groups` next")

def cmd_listen(a):
    from faster_whisper import WhisperModel
    cfg = load(a.work, "config.json")
    clip = os.path.join(a.work, "listen.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(a.start), "-to", str(a.end), "-i", cfg["dub_audio"],
                    "-ac", "1", "-ar", "16000", clip], check=True)
    segs, _ = WhisperModel("small", device="cpu", compute_type="int8").transcribe(clip, language=a.lang)
    for s in segs:
        print(f"[{fmt((a.start + s.start) * 1000)} - {fmt((a.start + s.end) * 1000)}] {s.text.strip()}")

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    x = sp.add_parser("analyze"); x.add_argument("--work", required=True)
    for k in ("--dub-srt", "--dub-audio", "--src-srt", "--src-video"): x.add_argument(k, required=True)
    x.add_argument("--models")
    x.add_argument("--src-srt-audio", help="audio the source SRT was timed to (default: a .wav/.mp3/... with the SRT's name, if present)")
    x.add_argument("--src-offset", type=float, help="seconds to move the source SRT EARLIER (skips measuring; use only after checking)")
    x.add_argument("--no-word-times", action="store_true", help="ignore sentences_<side>.json from `words` (use the SRT-card estimates)")
    x.add_argument("--clauses", type=int, help="split sentences into clauses of at least N words (0 = whole sentences); same N as `words`")
    x.add_argument("--clause-long", type=int, help="only sentences of at least L words are split (0 = any); same L as `words`")
    x = sp.add_parser("words"); x.add_argument("--work", required=True); x.add_argument("--side", choices=["src", "dub"], default="src")
    x.add_argument("--srt"); x.add_argument("--media"); x.add_argument("--lang"); x.add_argument("--models")
    x.add_argument("--model", default="small", help="Whisper model (small is enough: the words only give times, the text comes from the SRT)")
    x.add_argument("--first", type=int, help="only the first N sentences (tests)"); x.add_argument("--until", type=float)
    x.add_argument("--max-seconds", type=float, default=1e9); x.add_argument("--out", help="also write a sentence/word workbook")
    x.add_argument("--clauses", type=int, help="split sentences into clauses of at least N words (0 = whole sentences); saved for this work folder")
    x.add_argument("--clause-long", type=int, help="only sentences of at least L words are split (0 = any); saved for this work folder")
    x = sp.add_parser("lipmark"); x.add_argument("--work", required=True); x.add_argument("--side", default="src")
    x.add_argument("--out", help="the workbook from `words --out` to add the video columns to")
    x.add_argument("--use", choices=["all", "mouth", "talknet"], default="all"); x.add_argument("--mouth-thr", type=float, default=0.007)
    x.add_argument("--merge", type=float, default=0.3); x.add_argument("--min-len", type=float, default=0.4)
    x.add_argument("--min-lip", type=float, default=0.4, help="a sentence counts as speaking on screen with this many seconds of moving lips")
    x = sp.add_parser("sheet"); x.add_argument("--work", required=True); x.add_argument("--out", required=True)
    x.add_argument("--decisions"); x.add_argument("--labels", default="IT,US", help="column labels: dub,source")
    x = sp.add_parser("manifest"); x.add_argument("--work", required=True)
    x.add_argument("--fill-gaps", type=int, default=1000, help="join neighbouring clips across source gaps up to this many ms (0 = off)")
    x = sp.add_parser("groups"); x.add_argument("--work", required=True)
    x.add_argument("--size", type=int, default=8, help="rows per group at most (balanced; TOLR uses 7)")
    x.add_argument("--max-gap", type=int, default=2000, help="never span a US jump longer than this (ms)")
    x.add_argument("--min-keep", type=int, default=2000, help="keep at least this much of every trimmed row (ms)")
    x.add_argument("--min-cut", type=int, default=300, help="no cut smaller than this (ms); smaller excess -> tiny speed-up")
    x.add_argument("--max-speedup", type=float, default=1.0, help="allowed speed-up before trimming (1.0 = trim all extra US time)")
    x.add_argument("--max-stretch", type=float, default=1.25, help="flag groups slower or faster than this")
    x.add_argument("--trim-fast", type=float, default=0,
                   help="a block (not lip-sync) that would play faster than this has its extra source time cut, narration "
                        "lines first, instead of speeding up (0 = off; works with the lip scan's exact-span grouping too)")
    x.add_argument("--trim-to", type=float, default=1.0, help="with --trim-fast: speed a trimmed block is cut down to")
    x.add_argument("--max-drift", type=int, default=4000, help="split a group whose picture drifts more than this from the voice (ms)")
    x.add_argument("--review", help="review workbook (default: next to the mapping sheet)")
    x.add_argument("--no-lipsync", action="store_true", help="ignore lipsync.json (put lip-sync rows back into groups)")
    x.add_argument("--no-exact-span", dest="exact_span", action="store_false",
                   help="old grouping: trim extra source time and run on into free footage instead of the exact sentence span")
    x.add_argument("--lip-anchor", choices=["start", "both"], default="both",
                   help="lip sentences tied to the dub voice at their start only (the sentence runs into the pause after it) or at start and end")
    x.add_argument("--lip-max-speed", type=float, default=1.5,
                   help="TRIM mode (with --lip-anchor both): a lip sentence that would play faster than this is cut down to its lip part")
    x.add_argument("--lip-trim-above", type=float, default=1.6, help="TRIM mode: only sentences faster than this are trimmed")
    x.add_argument("--lip-smooth-ms", type=float, default=600, help="how far (ms) a lip sentence may start before/after its voice to even out speed")
    x.add_argument("--lip-group-speed-ok", type=float, default=1.5, help="pieces inside a lip group are evened out until within 1/x..x speed")
    x.add_argument("--no-lip-groups", action="store_true", help="every lip-sync row plays alone (no continuous lip groups)")
    x.add_argument("--lip-group-narr", type=int, default=2, help="narration rows allowed between two lip-sync rows of one lip group")
    x.add_argument("--lip-group-speed", type=float, default=2.0, help="footage between two lip sentences may play between 1/x and x speed")
    x.add_argument("--no-lip-fill", action="store_true", help="grouped rows next to a lip-sync row only slow down (no run-on into free footage)")
    x = sp.add_parser("render"); x.add_argument("--work", required=True)
    x.add_argument("--manifest", help="edit list to render (default: the active one: manifest_groups.json after `groups`)")
    x.add_argument("--out", help="the rendered video WITHOUT subtitles (default: <work>/video_nosubs.mp4)")
    x.add_argument("--subs", choices=["keep", "crop"], default="keep"); x.add_argument("--workers", type=int, default=4)
    x.add_argument("--max-seconds", type=int, default=100000)
    x = sp.add_parser("verify"); x.add_argument("--work", required=True); x.add_argument("--workers", type=int, default=4)
    x = sp.add_parser("subs"); x.add_argument("--work", required=True); x.add_argument("--out", required=True, help="the subtitled final video")
    x.add_argument("--srt", help="SRT to burn (default: the dub SRT; e.g. the English one from `translate`)")
    x.add_argument("--video", help="video to burn onto (default: the clean master from `bookends`)")
    x.add_argument("--offset", type=float, help="seconds to shift the SRT (default: the opening length on the clean master)")
    x.add_argument("--font", help="Poppins-Bold.ttf (found automatically if installed)")
    x.add_argument("--size", type=int, default=40, help="text size in px on a 1080-px-tall frame (scaled to the video's height)")
    x.add_argument("--box", help="strip rows TOP,BOTTOM (default: over the source caption box)")
    x.add_argument("--box-size", type=int, help="box style: text size in px on the 1080 frame (default 38)")
    x.add_argument("--box-bottom", type=int, help="box style: box distance from the bottom in px (default 71)")
    x.add_argument("--box-alpha", type=float, help="box style: box opacity 0-1 (default 0.30)")
    x.add_argument("--min-gap", type=float, default=1.5, help="pauses shorter than this keep showing the previous line")
    x.add_argument("--max-lead", type=float, default=5.0, help="show the first line from frame 0 (and hold the last to the end) if within this")
    x.add_argument("--no-cover", action="store_true", help="do not add empty strips over source captions outside subtitle times")
    x.add_argument("--chunk", type=float, default=60); x.add_argument("--workers", type=int, default=4)
    x.add_argument("--max-seconds", type=int, default=100000); x.add_argument("--keep-parts", action="store_true")
    x.add_argument("--style", choices=["strip", "box", "dynamic"], default="strip",
                   help="strip = white full-width strip (TDMB); box = white text on a 30%% black rounded box, transparent PNG overlay (every other show); "
                        "dynamic = white strip 2 or 3 caption lines tall, following the source caption under it (HIH; needs captions + dynsrt)")
    x = sp.add_parser("captions", help="read the source's burned-in captions (OCR): W/src_captions.json (dynamic subtitles)")
    x.add_argument("--work", required=True); x.add_argument("--video", help="default: the source video of the run")
    x.add_argument("--fps", type=int, default=4); x.add_argument("--workers", type=int, default=4)
    x.add_argument("--max-seconds", type=int, default=100000)
    x = sp.add_parser("dynsrt", help="dub subtitle cards that fill a strip as tall as the source caption under them")
    x.add_argument("--work", required=True); x.add_argument("--words", required=True, help="the dub's words (mksrt *_words.json)")
    x.add_argument("--out", required=True, help="the dynamic dub SRT"); x.add_argument("--font"); x.add_argument("--size", type=int, default=40)
    x = sp.add_parser("bookends"); x.add_argument("--work", required=True)
    x.add_argument("--out", required=True, help="the clean master (opening + dub episode + ending, no subtitles)")
    x.add_argument("--in", dest="input", help="default: the render without subtitles (video_out)")
    x.add_argument("--min-len", type=float, default=0.5, help="skip an opening/ending shorter than this (s)")
    x.add_argument("--fade", type=float, default=0.25, help="fade the source sound out/in at the joins (s)")
    x.add_argument("--no-ending", action="store_true", help="no source ending after the last dub line: the video ends with the dub audio (WBM QC)")
    for cmd, dflt in (("transitions", "light_leak,blur_dissolve,zoom_blur"), ("glow", "glow")):
        x = sp.add_parser(cmd); x.add_argument("--work", required=True)
        x.add_argument("--out", required=True, help="the clean master WITH transitions (or a test clip with --until)")
        x.add_argument("--in", dest="input", help="default: the clean master from `bookends`")
        x.add_argument("--styles", default=dflt, help="comma list, used in random order, never twice in a row: "
                       "light_leak, blur_dissolve, zoom_blur, glow")
        x.add_argument("--seed", type=int, default=24, help="random order seed (same seed = same order)")
        x.add_argument("--length", type=float, default=1.0, help="scale every transition's length (1.0 = 0.5-0.6 s)")
        x.add_argument("--min-jump", type=float, default=50, help="a source jump bigger than this (ms) counts as a clip switch")
        x.add_argument("--until", type=float, help="TEST: only the first N seconds (the clean master is not changed)")
        x.add_argument("--chunk", type=float, default=60); x.add_argument("--workers", type=int, default=2)
        x.add_argument("--max-seconds", type=float, default=1e9)
        x.add_argument("--plan", help="JSON of per-cut choices from the editor (default/cuts by dub ms; auto|none|style)")
    x = sp.add_parser("translate"); x.add_argument("--work", required=True); x.add_argument("--out", required=True)
    x.add_argument("--srt", help="default: the dub SRT"); x.add_argument("--sl", default="it"); x.add_argument("--tl", default="en")
    x.add_argument("--engine", choices=["gtx", "cloud", "file"], default="gtx",
                   help="gtx = free Google endpoint; cloud = Cloud Translation API (key); file = only --import")
    x.add_argument("--api-key"); x.add_argument("--import", dest="import_file", help="TSV (source<TAB>translation) or JSON")
    x.add_argument("--export-todo", help="write the untranslated sentences to this TSV and stop")
    x.add_argument("--glossary", help="TSV of English fixes applied after translation (wrong<TAB>right)")
    x.add_argument("--no-keep-names", dest="keep_names", action="store_false", help="let Google translate character names")
    x.add_argument("--max-chars", type=int, default=1500); x.add_argument("--pause", type=float, default=1.0)
    x = sp.add_parser("check"); x.add_argument("--work", required=True)
    x.add_argument("--expect-subs", type=int, default=2, help="subtitled videos required (Italian + English)")
    x = sp.add_parser("speech"); x.add_argument("--work", required=True)
    x.add_argument("--speech-share", type=float, default=0.6); x.add_argument("--mixed-share", type=float, default=0.15)
    x = sp.add_parser("lipsync"); x.add_argument("--work", required=True)
    x.add_argument("--use", choices=["all", "mouth", "talknet"], default="all", help="which lip measures count")
    x.add_argument("--mouth-thr", type=float, default=0.007, help="Face Mesh lip-opening change counted as moving lips")
    x.add_argument("--merge", type=float, default=0.3); x.add_argument("--min-len", type=float, default=0.4)
    x.add_argument("--min-lip", type=float, default=0.4, help="a source sentence is 'speaking on screen' with this many seconds of moving lips")
    x.add_argument("--review")
    x = sp.add_parser("listen"); x.add_argument("--work", required=True); x.add_argument("--start", type=float, required=True)
    x.add_argument("--end", type=float, required=True); x.add_argument("--lang", default="it")
    a = p.parse_args()
    global CLAUSE_MIN, CLAUSE_LONG
    w = getattr(a, "work", None); sm = os.path.join(w, "sentence_mode.json") if w else None
    if w and getattr(a, "clauses", None) is not None:
        os.makedirs(w, exist_ok=True)
        mode = {"clauses": a.clauses}
        if getattr(a, "clause_long", None):
            mode["long"] = a.clause_long
        json.dump(mode, open(sm, "w"))
    if sm and os.path.exists(sm) and not os.environ.get("DUBSTITCH_CLAUSES"):
        sm_ = json.load(open(sm)); CLAUSE_MIN = int(sm_.get("clauses", 0) or 0)
        if not os.environ.get("DUBSTITCH_CLAUSE_LONG"):
            CLAUSE_LONG = int(sm_.get("long", 0) or 0)
    if CLAUSE_MIN and a.cmd in ("words", "analyze"):
        print(f"sentence mode: clauses (long sentences cut at , ; : and dashes, each part >= {CLAUSE_MIN} words"
              + (f"; only sentences of >= {CLAUSE_LONG} words" if CLAUSE_LONG else "") + ")")
    dict(analyze=cmd_analyze, sheet=cmd_sheet, manifest=cmd_manifest, render=cmd_render, verify=cmd_verify, subs=cmd_subs,
         captions=cmd_captions, dynsrt=cmd_dynsrt, listen=cmd_listen, bookends=cmd_bookends, groups=cmd_groups, translate=cmd_translate, check=cmd_check, speech=cmd_speech, lipsync=cmd_lipsync, words=cmd_words, lipmark=cmd_lipmark, glow=cmd_transitions, transitions=cmd_transitions)[a.cmd](a)

if __name__ == "__main__":
    main()
