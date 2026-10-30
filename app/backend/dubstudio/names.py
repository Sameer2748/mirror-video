"""The show's localisation sheet: character names in the source video's language -> the dub's language.

The house format (owner's decision, 2026-10-16) is one sheet per show with two columns:

    source name | target name          e.g.  Asher | Demetrio,  Violet | Viola,  Emily Kaiser | Emma Reali

as .csv or .xlsx (first sheet), header in the first row; more columns are allowed and ignored. Older sheets are
still read: the WBM "Master Adaptation Sheet" (Original Mention / Localized Mention, Type) and the TOLR
"Localization Details" (Original Name / Localized Name and their first/last names). Claude gets the list with
every matching request."""
import csv, io, re
from pathlib import Path

NAME_HINT = re.compile(r"locali[sz]|names?\b|name.?sheet|adaptation|character", re.I)
SHEET_EXT = (".csv", ".xlsx")


def _rows(path):
    p = Path(path)
    if p.suffix.lower() == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        it = wb.worksheets[0].iter_rows(values_only=True)
        head = [str(c or "").strip() for c in next(it, [])]
        return [{head[k]: ("" if v is None else str(v)) for k, v in enumerate(r) if k < len(head)} for r in it]
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t") if text.strip() else csv.excel
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def _norm(k):
    return re.sub(r"[^a-z]", "", (k or "").lower())


def _nice(x):
    x = " ".join(str(x).split())
    return x.title() if x and x == x.lower() else x


def read(path):
    """-> {"format": "two-column" | "WBM master sheet" | "TOLR localization details" | "unknown",
           "names": [(source, target)], "error": str|None}"""
    try:
        rows = _rows(path)
    except Exception as ex:
        return {"format": "unreadable", "names": [], "error": str(ex)[:200]}
    if not rows:
        return {"format": "empty", "names": [], "error": "no rows"}
    cols = {_norm(k): k for k in rows[0] if k}
    pairs, fmt = [], "unknown"
    if "sourcename" in cols and "targetname" in cols:                                   # the house format
        fmt = "two-column"
        pairs = [(r.get(cols["sourcename"]), r.get(cols["targetname"])) for r in rows]
    elif "originalmention" in cols and "localizedmention" in cols:                      # WBM master sheet
        fmt = "WBM master sheet"; tk = cols.get("type")
        pairs = [(r.get(cols["originalmention"]), r.get(cols["localizedmention"])) for r in rows
                 if not tk or (r.get(tk) or "").strip().lower() in ("character", "")]
    elif "originalname" in cols and "localizedname" in cols:                            # TOLR localization details
        fmt = "TOLR localization details"; tk = cols.get("type")
        for r in rows:
            if tk and (r.get(tk) or "").strip().lower() not in ("character", ""):
                continue
            pairs.append((r.get(cols["originalname"]), r.get(cols["localizedname"])))
            fo, fl = cols.get("firstnameoriginal"), cols.get("firstnamelocalized")
            if fo and fl:
                pairs.append((r.get(fo), r.get(fl)))
    out, seen = [], set()
    for s, t in pairs:
        s, t = _nice(s or ""), _nice(t or "")
        if not s or not t or (s.lower(), t.lower()) in seen:
            continue
        seen.add((s.lower(), t.lower())); out.append((s, t))
    return {"format": fmt, "names": out,
            "error": None if fmt != "unknown" else "no 'source name' / 'target name' columns"}


def find(show_folder):
    """Localisation sheets in the show folder (top two levels), best first: the two-column format wins."""
    folder = Path(show_folder); cands = []
    for depth_glob in ("*", "*/*"):
        for p in folder.glob(depth_glob):
            if p.is_file() and p.suffix.lower() in SHEET_EXT and NAME_HINT.search(p.stem) and not p.name.startswith((".", "~$")):
                cands.append(p)
    scored = []
    for p in cands:
        r = read(p)
        if r["names"]:
            scored.append((r["format"] != "two-column", -len(r["names"]), str(p)))
    return [s[2] for s in sorted(scored)]


def for_episode(names, texts, limit=150):
    """What Claude gets: the whole sheet when it is short; else the names that occur in this episode's text."""
    if len(names) <= limit:
        return names
    blob = " ".join(texts).lower()
    hit = [(s, t) for s, t in names if re.search(r"\b" + re.escape(s.lower()) + r"\b", blob)
           or re.search(r"\b" + re.escape(t.lower()) + r"\b", blob)]
    return hit[:limit]


def to_csv(names):
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["source name", "target name"])
    w.writerows(names)
    return buf.getvalue()


def for_show(show):
    """The show's localisation sheet: the one chosen on the show page, else the best one found in its folder."""
    pth = (show.get("profile") or {}).get("names_sheet")
    if pth and Path(pth).exists():
        return pth
    folder = show.get("folder")
    found = find(folder) if folder and Path(folder).is_dir() else []
    return found[0] if found else None


def whisper_csv(show, work):
    """Step 0B without a script: mksrt.py --names fixes how Whisper spells the characters. Writes the show's sheet
    as W/names.csv (the two-column format) and returns its path, or None when the show has no sheet."""
    pth = for_show(show)
    names = read(pth)["names"] if pth else []
    if not names:
        return None
    out = Path(work) / "names.csv"; out.parent.mkdir(parents=True, exist_ok=True)
    text = to_csv(names)
    if not out.exists() or out.read_text(encoding="utf-8") != text:
        out.write_text(text, encoding="utf-8")
    return str(out)
