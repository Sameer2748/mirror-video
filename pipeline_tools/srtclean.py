"""
pipeline.py — core SRT cleaning logic (show-agnostic)

Implements the "show-srt-cleaner" process:
  script (source of truth for wording)
    -> raw ASR SRT (source of truth for timing/block structure)
    -> word-level alignment
    -> cleaned SRT (same block count / indices / timestamps as raw,
       text corrected against the script: names, punctuation, dialogue
       quotes, numbers, capitalization, stage-direction removal)

Any genuine content mismatch between audio and script (not just a
spelling/formatting difference) is left as the original audio and
reported, never silently guessed.
"""
from __future__ import annotations

import csv
import difflib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _ensure(pkg, import_name=None):
    try:
        __import__(import_name or pkg)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg,
                                "--break-system-packages", "-q"])

_ensure("python-docx", "docx")
_ensure("openpyxl")

import docx  # noqa: E402

# ── Episode discovery ────────────────────────────────────────────────────────

SCRIPT_DIR_NAMES = {"script", "scripts", "screenplay", "screenplays", "transcript", "transcripts"}
SRT_DIR_NAMES = {"srt", "raw srt", "raw", "subs", "subtitles"}
SCRIPT_EXTS = {".docx", ".txt", ".md"}
SHEET_NAME_HINTS = ("localis", "localiz", "character", "canvas", "glossary", "name", "casting")


def _ep_num(name: str) -> int | None:
    for pat in [
        r"\bEp\s*0*(\d{1,4})\b",
        r"\bEpisode\s*0*(\d{1,4})\b",
        r"\bEP0*(\d{1,4})\b",
        r"[_\-\s]0*(\d{1,4})[_\-\s.]",
        r"[_\-\s]0*(\d{1,4})$",
        # Bare filenames like "1.srt", "9.srt" (no "Episode"/"Ep" prefix, no
        # separator) — single- and double-digit numbers both need to match,
        # not just 2-4 digits, otherwise episodes 1-9 get silently dropped.
        r"^0*(\d{1,4})$",
    ]:
        m = re.search(pat, name, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def _pick_dir(root: Path, names: set[str]) -> Path | None:
    for p in root.iterdir():
        if p.is_dir() and p.name.strip().lower() in names:
            return p
    # one level deeper, in case the show folder wraps another folder
    for p in root.iterdir():
        if p.is_dir():
            for q in p.iterdir():
                if q.is_dir() and q.name.strip().lower() in names:
                    return q
    return None


def find_localization_sheet(root: Path) -> Path | None:
    candidates = []
    for p in root.rglob("*"):
        if "output" in p.parts or p.suffix.lower() not in {".xlsx", ".xls", ".csv"}:
            continue
        low = p.stem.lower()
        if any(h in low for h in SHEET_NAME_HINTS):
            candidates.append(p)
    return candidates[0] if candidates else None


def scan_folder(root: Path) -> dict:
    root = Path(root)
    script_dir = _pick_dir(root, SCRIPT_DIR_NAMES)
    srt_dir = _pick_dir(root, SRT_DIR_NAMES)

    scripts: dict[int, Path] = {}
    script_search_root = script_dir if script_dir else root
    for p in script_search_root.rglob("*"):
        if "output" in p.parts or p.suffix.lower() not in SCRIPT_EXTS:
            continue
        ep = _ep_num(p.stem)
        if ep is not None and ep not in scripts:
            scripts[ep] = p

    srts: dict[int, Path] = {}
    srt_search_root = srt_dir if srt_dir else root
    for p in srt_search_root.rglob("*.srt"):
        if "output" in p.parts or "clean" in p.stem.lower():
            continue
        ep = _ep_num(p.stem)
        if ep is not None and ep not in srts:
            srts[ep] = p

    all_eps = sorted(set(scripts) | set(srts))
    episodes = []
    for ep in all_eps:
        missing = []
        if ep not in scripts:
            missing.append("script")
        if ep not in srts:
            missing.append("srt")
        episodes.append({
            "ep": ep,
            "title": scripts.get(ep, srts.get(ep)).stem if (ep in scripts or ep in srts) else str(ep),
            "script": str(scripts.get(ep, "")),
            "srt": str(srts.get(ep, "")),
            "ready": not missing,
            "missing": missing,
        })

    sheet = find_localization_sheet(root)
    return {
        "episodes": episodes,
        "scripts": scripts,
        "srts": srts,
        "script_dir": str(script_dir) if script_dir else "",
        "srt_dir": str(srt_dir) if srt_dir else "",
        "localization_sheet": str(sheet) if sheet else "",
    }


# ── Localization / character-name sheet (optional, validation only) ─────────

DE_HEADER_HINT_RE = re.compile(r"char.*name.*|name.*char.*", re.IGNORECASE)


def load_localization_names(sheet_path: str, locale: str = "DE") -> list[str]:
    """Best-effort extraction of the target-language character-name column.
    Does not assume the visible first row is the header; scans the first
    several rows for one containing a recognisable '<locale> name' header."""
    if not sheet_path:
        return []
    path = Path(sheet_path)
    rows: list[list[str]] = []
    try:
        if path.suffix.lower() == ".csv":
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = [row for row in csv.reader(f)]
        else:
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True)
            ws = wb.active
            for r in ws.iter_rows(values_only=True):
                rows.append(["" if c is None else str(c) for c in r])
    except Exception:
        return []

    header_row_idx, name_col_idx = None, None
    locale_l = locale.lower()
    for i, row in enumerate(rows[:10]):
        for j, cell in enumerate(row):
            cl = (cell or "").strip().lower()
            if not cl:
                continue
            if locale_l in cl and ("name" in cl or "character" in cl or "localis" in cl or "localiz" in cl):
                header_row_idx, name_col_idx = i, j
                break
        if header_row_idx is not None:
            break
    if header_row_idx is None:
        return []

    names = []
    for row in rows[header_row_idx + 1:]:
        if name_col_idx < len(row):
            val = (row[name_col_idx] or "").strip()
            if val and "(?" not in val and not val.lower().startswith(("note", "comment")):
                names.append(val)
    return sorted(set(names))


# ── Tokenising helpers ───────────────────────────────────────────────────────

ALNUM_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]")
STAGE_TAG_RE_DEFAULT = [r"\[(VOA|SFX|MUSIC|FX|NOTE|DIRECTION|STAGE|AMBIENT)[^\]]*\]\s*(?:[SE]\b)?"]

# Beyond the known-keyword bracket tags above, real scripts also use
# keywords/typos/punctuation the whitelist can't anticipate — MUSIK, MUSC,
# SFC, END, MICROPHONE FILTER, VOICE-OVER, SCENE CUT, "[CROWD: CHEERS,
# WHISTLES]", "[ERIK'S INNER THOUGHT, ]"... An earlier version tried to
# whitelist the allowed punctuation character-by-character and kept missing
# commas, semicolons, em-dashes, parens-within-brackets, etc. The reliable
# signal isn't the punctuation at all, it's that every LETTER in the bracket
# is upper-case — a viewer-facing bracket (a game-stat panel like
# "[Nascosto]" or "[Razza: Umano]") is never written in all caps in
# practice. So: capture any bracket span, then decide by checking case in
# Python rather than restricting what characters the regex itself accepts.
BRACKET_CONTENT_RE = re.compile(r"\[([^\[\]]{1,160})\]\s*(?:[SE]\b)?")


def _is_allcaps_tag_content(content: str) -> bool:
    letters = [ch for ch in content if ch.isalpha()]
    return bool(letters) and all(ch.isupper() for ch in letters)


def _strip_allcaps_brackets(text: str) -> str:
    return BRACKET_CONTENT_RE.sub(lambda m: "" if _is_allcaps_tag_content(m.group(1)) else m.group(0), text)


# A bracket whose letters are not all capitals can still be a stage note.
# "[ambience voice]" and "[Ambience: street]" are that case. The keyword
# regex spells AMBIENT, and a case-insensitive search does not treat that as
# the longer word "ambience", so those notes used to reach the comparison.
# The check is done on the text in Python. A viewer-facing bracket
# ("[Nascosto]", "[Razza: Umano]") does not start with one of these words
# and is left as written.
_STAGE_HEADS = (
    "microphone filter", "phone filter", "inner thought", "inner voice",
    "voice-over", "voice over", "voiceover", "ambience", "direction",
    "ambient", "reverb", "stage", "music", "crowd", "note", "sfx", "voa", "fx",
)


def _head_is_stage(content: str) -> bool:
    head = content.lstrip(" \t\"'").lower()
    for word in _STAGE_HEADS:
        if head.startswith(word) and (len(head) == len(word) or not head[len(word)].isalpha()):
            return True
    return False


def _strip_stage_head_brackets(text: str) -> str:
    return BRACKET_CONTENT_RE.sub(lambda m: "" if _head_is_stage(m.group(1)) else m.group(0), text)


def _remove_orphan_brackets(text: str) -> str:
    """Strip only UNPAIRED '[' / ']' characters — e.g. a typo'd extra "] O]"
    left behind in the source after its real tag was already stripped.
    A properly paired "[...]" that survived this far (its content wasn't
    all-caps, so it wasn't a stage tag) is viewer-facing — e.g. "[Nascosto]",
    "[Razza: Umano]" — and must be left completely untouched, brackets
    included. This only ever deletes a lone bracket character, never any
    surrounding text."""
    if "[" not in text and "]" not in text:
        return text
    stack = []
    orphan_idx = set()
    for i, ch in enumerate(text):
        if ch == "[":
            stack.append(i)
        elif ch == "]":
            if stack:
                stack.pop()
            else:
                orphan_idx.add(i)
    orphan_idx.update(stack)
    if not orphan_idx:
        return text
    return "".join(ch for i, ch in enumerate(text) if i not in orphan_idx)


# A whole "[...]" span on one line, with no nested or unclosed bracket inside it.
SQUARE_BRACKET_SPAN_RE = re.compile(r"\[[^\[\]\n]{1,160}\]")


def strip_bracket_stage_tags(text: str, stage_patterns: list[str]) -> str:
    """Remove production-only bracket tags. stage_patterns (the known-keyword
    whitelist, e.g. STAGE_TAG_RE_DEFAULT + any extras) is applied first for
    exact backward-compatible matching, then the shape-based all-caps
    catch-all above, then any now-orphaned '[' or ']' — a leftover from a
    malformed/typo'd tag in the source doc (e.g. "...] O]") — is stripped as
    bare punctuation, same as the "<<"/">>" markup handling, since a
    literal stray bracket character never belongs in viewer dialogue. A
    still-paired "[...]" is left untouched, unless its text starts with a
    stage-note word ("[ambience voice]", "[Ambience: street]"). A
    viewer-facing bracket stays. A no-op (returns text unchanged) when
    stage_patterns is empty, i.e. the "remove stage tags" toggle is off."""
    if not stage_patterns:
        return text
    for pat in stage_patterns:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    # Any square-bracket span is script-only (the director's notes, "[Ambience: rooms| bedroom]",
    # "[Nascosto]", "[Razza: Umano]"). The whole bracket and its text go, never just the brackets.
    text = SQUARE_BRACKET_SPAN_RE.sub("", text)
    text = _strip_stage_head_brackets(text)
    text = _strip_allcaps_brackets(text)
    text = _remove_orphan_brackets(text)
    return text

# Some scripts wrap production-only direction in parentheses instead of
# brackets — e.g. "(EMILYS INNER VOICE)", "(PHONE FILTER)", "(BENEATH THE
# FOLLOWING)", "(UNTIL NOTED)", a bare "(END)". These are performance/
# audio-cue notes for the voice actor, never viewer dialogue. A keyword
# whitelist here misses far more than it catches in practice — real shows
# use vocabulary (and typos) no fixed list anticipates — so this uses the
# same all-caps shape rule as brackets: every letter inside the parens is
# upper-case. Checked against real show data with zero legitimate
# mixed-case parenthetical dialogue found, so this fires only on
# production notes, never on spoken content.
PAREN_CONTENT_RE = re.compile(r"\(([^()]{1,160})\)")


def strip_paren_stage_tags(text: str) -> str:
    return PAREN_CONTENT_RE.sub(
        lambda m: "" if _is_allcaps_tag_content(m.group(1)) or _head_is_stage(m.group(1)) else m.group(0),
        text)


# Some scripts use "<<" / ">>" as script-only markup (e.g. emphasis/beat
# markers for the voice actor). They carry no meaning for a viewer and must
# never leak into the cleaned SRT — strip just the marker characters and
# keep whatever text they wrap. Always on, independent of the stage-tag
# toggle, since this is punctuation-level markup, not a removable tag/span.
ARROW_MARKUP_RE = re.compile(r"<<|>>")

# The script's own documented convention: a capital "R" + colon directly
# before a line (e.g. 'R: "Verdammt nochmal,"') tells the sound engineer
# to apply reverb to that line -- a production instruction, never part of
# the spoken line itself (see the writer's KEY NOTES: 'Letter R indicates
# REVERB to be applied to the voice'). Strip the bare marker, keep the
# dialogue that follows it. Gated by the stage-tag toggle like the
# bracket/paren tags above (not always-on like ARROW_MARKUP_RE), since
# it's the same kind of removable production annotation.
REVERB_MARKER_RE = re.compile(r"\bR:\s*")

# Word docs sometimes embed invisible formatting/section-break characters
# (BOM / zero-width no-break space U+FEFF, zero-width space U+200B, and
# zero-width joiners) inline in the paragraph text itself -- python-docx
# returns them as part of .text, re.split()/str.split() treat them as a
# "word" since they are not whitespace, and they then survive straight
# into the cleaned SRT as a stray invisible glyph between real words.
# Always stripped, independent of the stage-tag toggle, like
# ARROW_MARKUP_RE -- this is a document-encoding artifact, never
# intentional content.
INVISIBLE_CHARS_RE = re.compile(r"[\ufeff\u200b\u200c\u200d\u2060]")

# Scripts use a run of plain divider characters (---, ***, ===, a long
# run of dashes, etc.) as a visual scene-break/section separator on its
# own line in the Word doc -- never spoken dialogue (seen in practice:
# a "---" or longer dash run stuck onto the end of the preceding
# sentence once the script is flattened to one line per paragraph).
# Three or more of the same divider punctuation mark in a row is not a
# pattern that occurs in real speech in any language this tool has
# seen, unlike "..." (ellipsis) or "!!!"/"???" (emphasis), which are
# deliberately left alone. Always stripped, independent of the
# stage-tag toggle, like ARROW_MARKUP_RE and INVISIBLE_CHARS_RE.
DIVIDER_RUN_RE = re.compile(r"[-\u2013\u2014_=*]{3,}")

# A Word header left at the top of the script ("Word Count: 1369"). It is
# never spoken. It is three tokens, so the small-leftover pass would paste
# it onto the first subtitle (IMTS episode 69). Removed before the script is
# split, the same way a divider line is removed, so the comparison never
# sees it. Always on: it is a document header, not a stage-tag toggle.
WORD_COUNT_RE = re.compile(r"(?i)\bword\s*count\s*:\s*\d[\d,.]*")

CARDINALS = {
    0: "null", 1: "eins", 2: "zwei", 3: "drei", 4: "vier", 5: "fünf", 6: "sechs",
    7: "sieben", 8: "acht", 9: "neun", 10: "zehn", 11: "elf", 12: "zwölf",
    13: "dreizehn", 14: "vierzehn", 15: "fünfzehn", 16: "sechzehn", 17: "siebzehn",
    18: "achtzehn", 19: "neunzehn", 20: "zwanzig", 21: "einundzwanzig",
    22: "zweiundzwanzig", 23: "dreiundzwanzig", 24: "vierundzwanzig",
    25: "fünfundzwanzig", 30: "dreißig", 31: "einunddreißig",
}
ORDINAL_IRREG = {1: "erst", 3: "dritt", 7: "siebt", 8: "acht"}


def ordinal_stem(n: int) -> str | None:
    if n in ORDINAL_IRREG:
        return ORDINAL_IRREG[n]
    if n in CARDINALS:
        return CARDINALS[n] + ("t" if n < 20 else "st")
    return None


def norm(tok: str) -> str:
    return re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ\-]", "", tok.lower())


def compact(tok: str) -> str:
    return re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ]", "", tok.lower())


def numeral_matches_word(a_raw: str, b_raw: str) -> bool:
    """True if one side is a bare digit token and the other is a spelled-out
    number word — treated as the same numeral written two ways, regardless
    of whether it's in the small CARDINALS lookup table."""
    for x, y in ((a_raw, b_raw), (b_raw, a_raw)):
        if re.fullmatch(r"\d{1,6}", x) and re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", y):
            n = int(x)
            card = CARDINALS.get(n)
            ostem = ordinal_stem(n)
            yl = y.lower()
            if card and yl.startswith(card[:max(3, len(card) - 2)]):
                return True
            if ostem and yl.startswith(ostem):
                return True
            return True
    return False


def merge_punct_only(words: list[str]) -> list[str]:
    """Merge any token that carries no letters/digits (e.g. a trailing
    '...!"' emitted as its own token because of a stray space in the
    source doc) into the previous real token."""
    out: list[str] = []
    for w in words:
        if out and not ALNUM_RE.search(w):
            out[-1] = out[-1] + " " + w
        else:
            out.append(w)
    return out


# ── Script / SRT extraction ──────────────────────────────────────────────────

def extract_script_words(path: str | Path, stage_patterns: list[str] | None = None) -> list[str]:
    """Read the script and tokenise it into words. Stage-direction markup
    (bracket tags + parenthetical performance notes) is stripped here, on
    the raw text, BEFORE tokenising/aligning — not just afterward on the
    rebuilt SRT text — because a tag that straddles two SRT blocks (its
    opening half lands in block N, its closing half in block N+1) can't be
    matched by a single per-block regex and would otherwise leak a
    half-tag fragment into the cleaned subtitle. Stripping it before the
    script is even split into words means it can never be assigned to a
    block in the first place. Pass stage_patterns=[] (via remove_stage_tags
    off) to skip this entirely and keep the script text as-is."""
    path = Path(path)
    if path.suffix.lower() == ".docx":
        d = docx.Document(str(path))
        paras = [p.text.strip() for p in d.paragraphs if p.text.strip()]
        full_text = "\n".join(paras)
    else:
        full_text = path.read_text(encoding="utf-8", errors="replace")
    full_text = INVISIBLE_CHARS_RE.sub("", full_text)
    full_text = DIVIDER_RUN_RE.sub("", full_text)
    full_text = WORD_COUNT_RE.sub("", full_text)
    if stage_patterns:
        full_text = strip_bracket_stage_tags(full_text, stage_patterns)
        full_text = strip_paren_stage_tags(full_text)
        full_text = REVERB_MARKER_RE.sub("", full_text)
    return merge_punct_only(full_text.split())


def unspoken_script_chunk(words: list[str]) -> bool:
    """True when this script leftover is only a document header or a stage note.

    The comparison must not paste it onto a subtitle. A real short difference
    ("Kira" heard, "Keira" written) still has letters left after the strip,
    and is not this."""
    text = " ".join(words).strip()
    if not text:
        return False
    text = WORD_COUNT_RE.sub(" ", text)
    text = strip_bracket_stage_tags(text, list(STAGE_TAG_RE_DEFAULT))
    text = strip_paren_stage_tags(text)
    return not ALNUM_RE.search(text)


def parse_srt(path: str | Path) -> list[dict]:
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    blocks = raw.strip("\n").split("\n\n")
    parsed = []
    for b in blocks:
        lines = b.split("\n")
        if len(lines) < 2:
            continue
        idx = lines[0].strip()
        ts = lines[1].strip() if len(lines) > 1 else ""
        text_lines = lines[2:] if len(lines) > 2 else []
        parsed.append({"idx": idx, "ts": ts, "text": " ".join(text_lines).strip()})
    return parsed


def merge_punct_only_srt(words: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Mirror of merge_punct_only() above, but for the SRT side's
    (word, block_index) tuples. merge_punct_only() merges a punctuation-only
    token (e.g. a lone '«' or '?!' emitted as its own token because of a
    stray space in the script doc) into the previous real token -- but it
    was only ever applied to script_words, never to srt_words. That
    asymmetry is what causes duplicated punctuation in the cleaned output:
    if the SAME lone mark appears as a separate token in the raw SRT too
    (e.g. '...visage.' '«' as two words), the script side already absorbed
    it into 'visage. «' (one token), so difflib has nothing left on the
    script side to match the SRT's standalone '«' against -- it gets
    classified as audio-only-extra and is re-appended literally, producing
    'visage. « «' (or 'passe ?! ?', 'bien ? » »', etc.) in the final text.
    Merging it on the SRT side the same way keeps both sides' tokenisation
    symmetric so a trailing mark is counted once, not twice. Only merges
    within the same block (same bpos) -- never across a block boundary,
    since that would move a word into the wrong subtitle."""
    out: list[tuple[str, int]] = []
    for w, bpos in words:
        if out and out[-1][1] == bpos and not ALNUM_RE.search(w):
            out[-1] = (out[-1][0] + " " + w, bpos)
        else:
            out.append((w, bpos))
    return out


def build_srt_words(blocks: list[dict]) -> list[tuple[str, int]]:
    words = []
    for bi, b in enumerate(blocks):
        for w in b["text"].split():
            words.append((w, bi))
    return merge_punct_only_srt(words)


# ── Alignment ────────────────────────────────────────────────────────────────

@dataclass
class AlignOptions:
    short_max: int = 6
    short_ratio: float = 0.55
    same_len_ratio: float = 0.4


PRESETS = {
    "conservative": AlignOptions(short_max=4, short_ratio=0.7, same_len_ratio=0.55),
    "balanced": AlignOptions(short_max=6, short_ratio=0.55, same_len_ratio=0.4),
    "aggressive": AlignOptions(short_max=10, short_ratio=0.4, same_len_ratio=0.3),
}


# ── Script typo repair ───────────────────────────────────────────────────────

# "scampo.Viola", "pensarci,qualcosa", "posto?!»continuò": the script is
# missing the space after punctuation. Only between two real words (2+
# letters each side), so numbers (3.5), initials (M.Andrieux) and S.p.A.
# are left alone.
MISSING_SPACE_RE = re.compile(r"(?<=[^\W\d_]{2})([.,;:!?…»]+)(?=[^\W\d_]{2}|[^\W\d_]['’][^\W\d_]|«)")


def fix_script_spacing(words: list[str]) -> list[str]:
    """Split script tokens that are missing a space after punctuation, so
    each word lines up with its own spoken word (and its own subtitle)."""
    out: list[str] = []
    for w in words:
        # split ONLY where a space is missing; a token's existing spaces
        # (French "deux ?", "aspettando .»") must stay inside the token
        out.extend(part for part in MISSING_SPACE_RE.sub("\\1\x00", w).split("\x00") if part)
    return out


# Words that are legitimately written joined to the word before/after them,
# so a "joined word" built from these is NOT treated as a script typo.
JOIN_PREFIXES = {"tra", "fra", "ri", "re", "pre", "pro", "anti", "auto", "contro", "sopra", "sotto",
                 "super", "extra", "multi", "semi", "inter", "intra", "sovra", "stra", "dis", "mal",
                 "bene", "post", "neo", "ultra", "mega", "micro", "mini", "maxi", "vice", "co",
                 "en", "em", "sur", "sous", "dé", "des", "entre", "outre", "para", "porte"}
JOIN_SUFFIXES = {"gli", "lo", "la", "le", "li", "ne", "ci", "si", "mi", "ti", "vi", "glielo",
                 "gliela", "glieli", "gliele", "gliene", "mente",
                 # verb endings: "calamitava" is one word, not "calamita va"
                 "va", "vano", "vamo", "vate", "vo", "no", "ono", "rono", "re", "ra", "ro", "rà", "rò"}
SPELL_LANGS = ("it", "fr", "es", "pt")  # not German: German writes compounds as one word
_SPELL_CACHE: dict = {}


def _spellchecker(lang: str):
    if lang not in _SPELL_CACHE:
        try:
            from spellchecker import SpellChecker
            _SPELL_CACHE[lang] = SpellChecker(language=lang, distance=1)
        except Exception:
            _SPELL_CACHE[lang] = None
    return _SPELL_CACHE[lang]


def detect_script_dictionary(script_words: list[str]):
    """Pick the dictionary (it/fr/es/pt) that recognises most of the script's
    words; None if none of them clearly fits (e.g. a German script)."""
    sample = [compact(w) for w in script_words if len(compact(w)) > 3][:600]
    if not sample:
        return None
    best, best_ratio = None, 0.0
    for lang in SPELL_LANGS:
        sc = _spellchecker(lang)
        if sc is None:
            continue
        ratio = sum(1 for w in sample if w in sc) / len(sample)
        if ratio > best_ratio:
            best, best_ratio = sc, ratio
    return best if best_ratio >= 0.6 else None


def split_glued_script_word(script_word: str, srt_pair: list[str], sc, script_counts) -> list[str] | None:
    """If the script glued two words together by mistake ("trattatacome")
    and the audio clearly said those two words ("trattata come"), return the
    two words -- keeping the script's own capitals and punctuation.
    Returns None whenever the joined form could be a real word."""
    if sc is None or len(srt_pair) != 2:
        return None
    if any(ch in script_word for ch in "'’-"):   # elision (un'ape) / hyphenated (petit-déjeuner): correct as written
        return None
    core = re.sub(r"^[^0-9A-Za-zÀ-ÖØ-öø-ÿ]+", "", script_word)
    if core[:1].isupper():                        # names (Lemaire, Lunaris) are kept as the script writes them
        return None
    a, b, w = compact(srt_pair[0]), compact(srt_pair[1]), compact(script_word)
    if a + b != w or len(a) < 2 or len(b) < 2 or not a.isalpha() or not b.isalpha():
        return None
    if w in sc or script_counts.get(w, 0) >= 2:      # a real / deliberately used word
        return None
    if a not in sc or b not in sc or a in JOIN_PREFIXES or b in JOIN_SUFFIXES:
        return None
    # split the script's own spelling after len(a) letters
    seen = 0
    for idx, ch in enumerate(script_word):
        if re.match(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]", ch):
            seen += 1
            if seen == len(a):
                return [script_word[:idx + 1], script_word[idx + 1:]]
    return None


_VOCAB_CACHE: dict = {}


def show_vocabulary(script_path) -> dict[str, int]:
    """How often each word appears across ALL scripts in this script's
    folder. A joined word the writers use more than once in the show
    (e.g. "cortocircuito", a character's name) is deliberate, not a typo."""
    folder = Path(script_path).parent
    key = str(folder)
    if key not in _VOCAB_CACHE:
        counts: dict[str, int] = {}
        for f in folder.iterdir():
            if f.suffix.lower() in SCRIPT_EXTS and not f.name.startswith("~$"):
                try:
                    for w in fix_script_spacing(extract_script_words(f, [])):
                        counts[compact(w)] = counts.get(compact(w), 0) + 1
                except Exception:
                    continue
        _VOCAB_CACHE[key] = counts
    return _VOCAB_CACHE[key]


GUILLEMET_RE = re.compile(r"\s*[«»]\s*")


def strip_srt_guillemets(word: str) -> str:
    """Remove « and » from a word that is being kept from the raw SRT.

    Guillemets are only wanted when the SCRIPT has them. Words that come
    from the script (matched or corrected) keep their marks exactly as the
    script writes them; any word kept as-is from the raw SRT (audio-only
    extras, rejected/mismatched replacements) has its « » removed here, so
    stray ASR guillemets never reach the cleaned SRT."""
    # "elle »." must become "elle." -- not "elle ." -- so a mark that sits
    # right before a full stop or comma is removed together with its space.
    word = re.sub(r"\s*[«»]\s*(?=[.,])", "", word)
    return GUILLEMET_RE.sub(" ", word).strip()


def _keep_srt_word(final: list, w: str, bpos: int) -> None:
    w = strip_srt_guillemets(w)
    if w:
        final.append((w, bpos))


def _quote_events(words: list[str]) -> list[tuple[int, str]]:
    """(word index, mark) for every « / » inside a list of script words."""
    return [(j, ch) for j, w in enumerate(words) for ch in w if ch in "«»"]


def _unmatched_quotes(words: list[str]) -> tuple[list[int], list[int]]:
    """Quote marks in a run of script words that are NOT closed/opened within
    that same run. A complete «...» pair inside the run cancels out."""
    opens: list[int] = []
    closes: list[int] = []
    for j, ch in _quote_events(words):
        if ch == "«":
            opens.append(j)
        elif opens:
            opens.pop()
        else:
            closes.append(j)
    return opens, closes


def _map_script_quotes(script_chunk: list[str], start: int, kept_n: int) -> tuple[list[int], list[int]]:
    """Work out where the « / » of a rejected script chunk belong among the
    kept_n spoken words that replace it (final[start : start + kept_n]).

    Each mark is placed proportionally. A « that trails its word (the script
    fused it on: "hurler. «") opens the NEXT word, which may be the first
    word after this chunk; a » that leads its word closes the PREVIOUS one.
    A complete «...» pair spanning several script words that lands on one
    single spoken word means that quote wasn't really spoken -- both marks
    are dropped together so the result stays balanced."""
    n = len(script_chunk)
    events = []  # (script index, mark, target index in final)
    for j, w in enumerate(script_chunk):
        for c, ch in enumerate(w):
            if ch not in "«»":
                continue
            before, after = ALNUM_RE.search(w[:c]), ALNUM_RE.search(w[c + 1:])
            if ch == "«":
                k = (j * kept_n) // n if after else ((j + 1) * kept_n) // n
                events.append((j, ch, start + min(k, kept_n)))
            else:
                k = (-(-(j + 1) * kept_n // n) - 1) if before else (-(-j * kept_n // n) - 1)
                events.append((j, ch, start + max(k, -1)))
    # "» ... «" can never sit on one word: when a close and a LATER open
    # land on the same spoken word, the close belongs to the word before.
    for e, (j, ch, t) in enumerate(events):
        if ch == "»" and any(c2 == "«" and t2 == t for (_, c2, t2) in events[e + 1:]):
            events[e] = (j, ch, t - 1)
    drop: set[int] = set()
    stack: list[int] = []
    for e, (j, ch, t) in enumerate(events):
        if ch == "«":
            stack.append(e)
        elif stack:
            o = stack.pop()
            if events[o][2] == t and events[o][0] != j:
                drop.update((o, e))
    opens = [t for e, (j, ch, t) in enumerate(events) if ch == "«" and e not in drop]
    closes = [t for e, (j, ch, t) in enumerate(events) if ch == "»" and e not in drop]
    return opens, closes


def _drop_misplaced_moved_quotes(final: list, added_open: set[int], added_close: set[int]) -> None:
    """Safety net for marks MOVED from the script (never the script's own).
    A moved « is kept only if the next mark after it is a »; a moved » only
    if the mark before it is a «. Anything else would create a doubled «« or
    a » with nothing open -- which happens where the raw SRT and the script
    diverge so much that there is no right place for the mark."""
    while True:
        marks = []  # (word index, mark, moved?)
        for i, (w, _) in enumerate(final):
            chars = [ch for ch in w if ch in "«»"]
            for k, ch in enumerate(chars):
                moved = (ch == "«" and k == 0 and i in added_open) or \
                        (ch == "»" and k == len(chars) - 1 and i in added_close)
                marks.append((i, ch, moved))
        bad = None
        for m, (i, ch, moved) in enumerate(marks):
            if not moved:
                continue
            if ch == "«" and (m + 1 >= len(marks) or marks[m + 1][1] != "»"):
                bad = (i, ch)
                break
            if ch == "»" and (m == 0 or marks[m - 1][1] != "«"):
                bad = (i, ch)
                break
        if bad is None:
            return
        i, ch = bad
        w, b = final[i]
        if ch == "«":
            final[i] = (w[1:].lstrip(), b)
            added_open.discard(i)
        else:
            final[i] = (w[:-1].rstrip(), b)
            added_close.discard(i)


def align(srt_words, script_words, opts: AlignOptions, show_vocab: dict | None = None):
    # Guillemets come ONLY from the script. When a script word that carries
    # a « or » cannot be used (the audio says something different, or the
    # script words were never spoken), its mark is moved onto the spoken
    # word in the same place, so the script's quoting survives without ever
    # keeping the raw SRT's own marks. French puts a space inside the marks
    # (« Bonjour »), Italian doesn't («Ciao») -- follow whatever the script does.
    _joined = " ".join(script_words)
    spaced = len(re.findall(r"«\s", _joined)) > len(re.findall(r"«[^\s]", _joined))
    fix_open: list[int] = []
    fix_close: list[int] = []
    spell = detect_script_dictionary(script_words)
    script_counts: dict[str, int] = dict(show_vocab) if show_vocab else {}
    if not show_vocab:
        for w in script_words:
            script_counts[compact(w)] = script_counts.get(compact(w), 0) + 1
    srt_norm = [norm(w) for w, _ in srt_words]
    script_norm = [norm(w) for w in script_words]
    sm = difflib.SequenceMatcher(None, srt_norm, script_norm, autojunk=False)
    final: list[tuple[str, int]] = []
    checks: list[dict] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                _, bpos = srt_words[i1 + k]
                final.append((script_words[j1 + k], bpos))
        elif tag == "replace":
            srt_chunk = srt_words[i1:i2]
            script_chunk = script_words[j1:j2]
            block_positions = [bp for _, bp in srt_chunk]
            same_len = len(srt_chunk) == len(script_chunk)
            short = max(len(srt_chunk), len(script_chunk)) <= opts.short_max
            a_c = "".join(compact(w) for w, _ in srt_chunk)
            b_c = "".join(compact(w) for w in script_chunk)
            ratio = difflib.SequenceMatcher(None, a_c, b_c).ratio()
            numeral_ok = (
                len(srt_chunk) == 1 and len(script_chunk) == 1
                and numeral_matches_word(norm(srt_chunk[0][0]), norm(script_chunk[0]))
            )
            if script_chunk and ((short and ratio > opts.short_ratio) or numeral_ok
                                  or (same_len and len(srt_chunk) <= opts.short_max
                                      and ratio > opts.same_len_ratio)):
                if len(script_chunk) == 1 and len(srt_chunk) == 2:
                    parts = split_glued_script_word(script_chunk[0], [w for w, _ in srt_chunk],
                                                    spell, script_counts)
                    if parts:
                        for w, (_, bpos) in zip(parts, srt_chunk):
                            final.append((w, bpos))
                        checks.append({"type": "script-typo-split", "srt": " ".join(w for w, _ in srt_chunk),
                                       "script": script_chunk[0], "blocks": sorted(set(b + 1 for b in block_positions))})
                        continue
                for k, w in enumerate(script_chunk):
                    m = min(k, len(block_positions) - 1) if block_positions else 0
                    bpos = block_positions[m] if block_positions else (final[-1][1] if final else 0)
                    final.append((w, bpos))
                checks.append({"type": "corrected", "srt": " ".join(w for w, _ in srt_chunk),
                                "script": " ".join(script_chunk),
                                "blocks": sorted(set(b + 1 for b in block_positions))})
            else:
                start = len(final)
                for w, bpos in srt_chunk:
                    _keep_srt_word(final, w, bpos)
                opens, closes = _map_script_quotes(script_chunk, start, len(final) - start)
                fix_open.extend(opens)
                fix_close.extend(closes)
                checks.append({"type": "CHECK-content-mismatch", "srt": " ".join(w for w, _ in srt_chunk),
                                "script": " ".join(script_chunk),
                                "blocks": sorted(set(b + 1 for b in block_positions))})
        elif tag == "delete":
            srt_chunk = srt_words[i1:i2]
            for w, bpos in srt_chunk:
                _keep_srt_word(final, w, bpos)
            checks.append({"type": "audio-only-extra", "srt": " ".join(w for w, _ in srt_chunk), "script": "",
                            "blocks": sorted(set(b + 1 for b in [bp for _, bp in srt_chunk]))})
        elif tag == "insert":
            script_chunk = script_words[j1:j2]
            opens, closes = _unmatched_quotes(script_chunk)
            fix_close.extend([len(final) - 1] * len(closes))
            fix_open.extend([len(final)] * len(opens))
            checks.append({"type": "script-only-not-inserted", "srt": "", "script": " ".join(script_chunk),
                            "blocks": []})

    # Skip a moved mark when the same mark is already right there -- either
    # on the word itself or at the touching end of its neighbour (the script
    # often fuses a lone « onto the previous word: "Rémi. «").
    added_open: set[int] = set()
    added_close: set[int] = set()
    for i in sorted(set(fix_close)):
        if not 0 <= i < len(final):
            continue
        w, b = final[i]
        if w.rstrip(".,;:!?…").endswith("»") or (i + 1 < len(final) and final[i + 1][0].lstrip().startswith("»")):
            continue
        if w.rstrip().endswith("«"):
            # the quote this « opened was never spoken: drop the orphan «
            final[i] = (w.rstrip()[:-1].rstrip(), b)
            continue
        final[i] = (w + (" »" if spaced else "»"), b)
        added_close.add(i)
    for i in sorted(set(fix_open)):
        if not 0 <= i < len(final):
            continue
        w, b = final[i]
        if w.startswith("«") or (i > 0 and final[i - 1][0].rstrip().endswith("«")):
            continue
        final[i] = (("« " if spaced else "«") + w, b)
        added_open.add(i)
    _drop_misplaced_moved_quotes(final, added_open, added_close)
    return final, checks


def rebuild_blocks(blocks: list[dict], final_words, stage_patterns: list[str]) -> list[dict]:
    by_block: dict[int, list[str]] = {}
    for w, bpos in final_words:
        by_block.setdefault(bpos, []).append(w)
    new_blocks = []
    for bi, b in enumerate(blocks):
        text = " ".join(by_block.get(bi, []))
        text = strip_bracket_stage_tags(text, stage_patterns)
        if stage_patterns:
            text = strip_paren_stage_tags(text)
            text = REVERB_MARKER_RE.sub("", text)
        text = ARROW_MARKUP_RE.sub("", text)
        text = INVISIBLE_CHARS_RE.sub("", text)
        text = DIVIDER_RUN_RE.sub("", text)
        # A guillemet can end up doubled ("« «Word", "word» »") even after
        # merge_punct_only_srt: that fix only catches a mark that was its
        # OWN separate token on the SRT side. Here the second mark is fused
        # directly onto the next/previous real word with no space at all
        # (e.g. raw ASR text "«Olak,"), so it was never a standalone token
        # to merge in the first place -- it surfaces when that raw word is
        # kept as-is (a rejected/mismatched replace) right next to a script
        # word that already carries the same mark merged onto its end. A
        # real double-quote-within-a-quote would have actual words between
        # the two marks, not just whitespace, so collapsing an immediately
        # repeated identical mark is safe.
        text = re.sub(r"([«»])\s*\1", r"\1", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        new_blocks.append({"idx": b["idx"], "ts": b["ts"], "text": text})
    return new_blocks



def extend_over_merged_subtitles(raw_blocks: list[dict], new_blocks: list[dict]) -> list[dict]:
    """A subtitle can end up empty because the cleaner merged its words into
    the subtitle before it (heard "luna." | "Aries." -> script "Lunaris").
    The empty subtitle is dropped when writing, so stretch the previous
    subtitle's end time over it -- the merged word stays on screen while it
    is being spoken and no gap opens in the timeline. Subtitles that only
    ever held quote marks or tags are just dropped, as before."""
    out = [dict(b) for b in new_blocks]
    last = None  # index of the last subtitle that still has text
    for i, (rb, nb) in enumerate(zip(raw_blocks, out)):
        if nb["text"].strip():
            last = i
            continue
        had_words = bool(ALNUM_RE.search(strip_bracket_stage_tags(rb["text"], STAGE_TAG_RE_DEFAULT)))
        if had_words and last is not None:
            m_prev, m_this = TS_RE.match(out[last]["ts"]), TS_RE.match(nb["ts"])
            if m_prev and m_this:
                out[last]["ts"] = f"{m_prev.group(1)} --> {m_this.group(2)}"
    return out


# ── Optional: timestamp continuity repair ────────────────────────────────────

TS_RE = re.compile(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})")


def _ts_to_ms(ts: str) -> int:
    h, m, s_ms = ts.split(":")
    s, ms = s_ms.split(",")
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


def _ms_to_ts(ms: int) -> str:
    ms = max(ms, 0)
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fix_timestamp_continuity(blocks: list[dict]) -> tuple[list[dict], int]:
    fixed = 0
    prev_end_ms = None
    out = []
    for b in blocks:
        m = TS_RE.match(b["ts"])
        if not m:
            out.append(b)
            continue
        start_ms, end_ms = _ts_to_ms(m.group(1)), _ts_to_ms(m.group(2))
        new_ts = b["ts"]
        if prev_end_ms is not None and start_ms != prev_end_ms:
            new_ts = f"{_ms_to_ts(prev_end_ms)} --> {m.group(2)}"
            fixed += 1
        out.append({"idx": b["idx"], "ts": new_ts, "text": b["text"]})
        prev_end_ms = end_ms
    return out, fixed


# ── Quote-style normalisation (optional) ─────────────────────────────────────

QUOTE_CHARS = set("„“”\"«»")

QUOTE_STYLES = {
    "german": ("„", "“"),
    "english": ("“", "”"),
    "straight": ('"', '"'),
}


def force_quote_style(blocks: list[dict], style: str) -> list[dict]:
    if style not in QUOTE_STYLES:
        return blocks
    open_c, close_c = QUOTE_STYLES[style]
    full_text = "\n".join(b["text"] for b in blocks)
    out_chars = []
    n = 0
    for ch in full_text:
        if ch in QUOTE_CHARS:
            out_chars.append(open_c if n % 2 == 0 else close_c)
            n += 1
        else:
            out_chars.append(ch)
    new_full = "".join(out_chars).split("\n")
    return [{"idx": b["idx"], "ts": b["ts"], "text": t} for b, t in zip(blocks, new_full)]


# ── Validation ────────────────────────────────────────────────────────────────

def detect_quote_style(words: list[str]) -> str | None:
    """Guess which dialogue-quote convention a script actually uses. This
    matters because '“' (U+201C) is the CLOSING mark in German „…“ style but
    the OPENING mark in English “…” style — the same character means the
    opposite thing depending on convention, so counts can't be paired
    without knowing which one is active."""
    text = " ".join(words)
    if "„" in text:
        return "german"
    if "“" in text or "”" in text:
        return "english"
    if "«" in text or "»" in text:
        return "guillemet"
    if '"' in text:
        return "straight"
    return None


def validate(raw_blocks: list[dict], new_blocks: list[dict], quote_style: str | None) -> dict:
    issues = []
    if len(raw_blocks) != len(new_blocks):
        issues.append(f"block count changed: {len(raw_blocks)} -> {len(new_blocks)}")
    for rb, nb in zip(raw_blocks, new_blocks):
        if rb["idx"] != nb["idx"]:
            issues.append(f"index changed: {rb['idx']} -> {nb['idx']}")

    full = "\n".join(b["text"] for b in new_blocks)
    pairs = {
        "german": ("„", "“"),
        "english": ("“", "”"),
        "guillemet": ("«", "»"),
        "straight": ('"', '"'),
    }
    if quote_style in pairs:
        open_c, close_c = pairs[quote_style]
        o = full.count(open_c)
        c = full.count(close_c) if close_c != open_c else o  # straight: even-count check below
        if open_c == close_c:
            balanced = (o % 2 == 0)
            c = o
        else:
            balanced = (o == c)
    else:
        o = c = 0
        balanced = True  # no dialogue-quote convention detected in this show — nothing to balance
    return {"issues": issues, "quote_style_detected": quote_style,
            "quote_open": o, "quote_close": c, "quotes_balanced": balanced}


def write_srt(blocks: list[dict], path: Path) -> int:
    """Write blocks as an SRT file. A subtitle whose text ended up empty
    (e.g. it only ever held a stray '»' or a stage tag) is left out, because
    an empty text line is a blank line -- which ends the SRT block early and
    makes players misread every subtitle after it. When anything is left
    out, the remaining subtitles are renumbered 1, 2, 3... Returns how many
    empty subtitles were dropped."""
    kept = [b for b in blocks if b["text"].strip()]
    dropped = len(blocks) - len(kept)
    lines = []
    for n, b in enumerate(kept, 1):
        lines += [str(n) if dropped else b["idx"], b["ts"], b["text"], ""]
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return dropped


def reclassify_stage_only_checks(checks: list[dict], stage_patterns: list[str]) -> list[dict]:
    """A 'script-only-not-inserted' check whose script text is ENTIRELY a
    production-only stage-direction tag (e.g. '[MUSIC: ...]', '[SFX: ...]')
    was correctly never spoken — that's expected, not a review item. Relabel
    those as informational so they stop inflating num_flagged. Anything with
    leftover real words (or a viewer-facing tag not in stage_patterns, e.g.
    a game-stat panel like '[NOME: ...]') keeps its original type."""
    if not stage_patterns:
        return checks
    out = []
    for c in checks:
        if c["type"] == "script-only-not-inserted":
            remainder = strip_bracket_stage_tags(c["script"], stage_patterns)
            remainder = strip_paren_stage_tags(remainder)
            if not ALNUM_RE.search(remainder):
                c = {**c, "type": "stage-direction-not-spoken"}
        out.append(c)
    return out


def find_quote_imbalance_suspects(checks: list[dict], quote_style: str | None) -> list[dict]:
    """When quotes end up unbalanced, it's almost always because one side of
    a quote pair fell inside a flagged (not auto-applied) chunk — the other
    side got emitted, this one didn't. Surface exactly which flagged item(s)
    carry an odd/unequal quote-mark count so a human doesn't have to hunt for
    the culprit line by line."""
    pairs = {"german": ("„", "“"), "english": ("“", "”"), "guillemet": ("«", "»"), "straight": ('"', '"')}
    if quote_style not in pairs:
        return []
    open_c, close_c = pairs[quote_style]
    suspects = []
    for c in checks:
        if c["type"] in ("corrected", "stage-direction-not-spoken"):
            continue
        text = f"{c.get('script', '')} {c.get('srt', '')}"
        o = text.count(open_c)
        cl = text.count(close_c)
        unbalanced = (o != cl) if open_c != close_c else (o % 2 == 1)
        if unbalanced:
            suspects.append(c)
    return suspects


# ── Top-level: clean one episode ─────────────────────────────────────────────

def clean_episode(ep: int, script_path: str, srt_path: str, out_dir: Path, report_dir: Path,
                   sensitivity: str = "balanced", fix_timestamps: bool = False,
                   remove_stage_tags: bool = True, extra_stage_patterns: list[str] | None = None,
                   quote_style: str = "script", canonical_names: list[str] | None = None,
                   log=lambda msg: None) -> dict:
    opts = PRESETS.get(sensitivity, PRESETS["balanced"])
    stage_patterns = list(STAGE_TAG_RE_DEFAULT) if remove_stage_tags else []
    if extra_stage_patterns:
        stage_patterns += extra_stage_patterns

    # Stage tags are stripped from the script text up front (see
    # extract_script_words) so a tag can never end up split across two SRT
    # blocks. rebuild_blocks() below still re-applies the same stripping to
    # the assembled block text as a defense-in-depth safety net.
    script_words = fix_script_spacing(extract_script_words(script_path, stage_patterns))
    raw_blocks = parse_srt(srt_path)
    if not raw_blocks:
        raise ValueError("the SRT file has no subtitles in it (it is empty, or not a real .srt file)")
    srt_words = build_srt_words(raw_blocks)

    log(f"aligning {len(srt_words)} audio words against {len(script_words)} script words")
    try:
        vocab = show_vocabulary(script_path)
    except Exception:
        vocab = None
    final_words, checks = align(srt_words, script_words, opts, vocab)

    new_blocks = rebuild_blocks(raw_blocks, final_words, stage_patterns)
    checks = reclassify_stage_only_checks(checks, stage_patterns)
    new_blocks = extend_over_merged_subtitles(raw_blocks, new_blocks)

    ts_fixed = 0
    if fix_timestamps:
        new_blocks, ts_fixed = fix_timestamp_continuity(new_blocks)

    if quote_style != "script":
        new_blocks = force_quote_style(new_blocks, quote_style)
        effective_quote_style = quote_style
    else:
        effective_quote_style = detect_quote_style(script_words)

    v = validate(raw_blocks, new_blocks, effective_quote_style)
    quote_suspects = find_quote_imbalance_suspects(checks, effective_quote_style) if not v["quotes_balanced"] else []

    name_flags = []
    if canonical_names:
        full_text_norm = norm(" ".join(b["text"] for b in new_blocks))
        for cname in canonical_names:
            cnorm = norm(cname)
            if cnorm and cnorm not in full_text_norm:
                name_flags.append(cname)

    out_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{Path(srt_path).stem}.srt"
    empty_dropped = write_srt(new_blocks, out_path)
    if empty_dropped:
        log(f"{empty_dropped} empty subtitle(s) left out (they held only quote marks/tags)")

    corrected = [c for c in checks if c["type"] in ("corrected", "script-typo-split")]
    informational = [c for c in checks if c["type"] == "stage-direction-not-spoken"]
    flagged = [c for c in checks if c["type"] not in ("corrected", "script-typo-split", "stage-direction-not-spoken")]
    report = {
        "episode": ep, "script": str(script_path), "srt": str(srt_path),
        "num_blocks": len(raw_blocks), "num_corrected": len(corrected),
        "num_flagged": len(flagged), "num_stage_directions_not_spoken": len(informational),
        "timestamp_fixes": ts_fixed, "empty_subtitles_dropped": empty_dropped, "validation": v, "quote_imbalance_suspects": quote_suspects,
        "canonical_names_missing": name_flags, "checks": checks,
    }
    report_path = report_dir / f"{Path(srt_path).stem}_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ep": ep, "out_path": str(out_path), "report_path": str(report_path),
        "num_blocks": len(raw_blocks), "num_corrected": len(corrected),
        "num_flagged": len(flagged), "quotes_balanced": v["quotes_balanced"],
        "quote_imbalance_blocks": sorted({b for s in quote_suspects for b in s["blocks"]}),
        "issues": v["issues"], "name_flags": name_flags,
    }
