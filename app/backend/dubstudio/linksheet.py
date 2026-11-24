"""A show sheet of episode links: episode number, source video, dub audio, dub script.

The sheet is the extra way in. The folder scan and the browser upload stay as they are. One download
runs at a time, on its own slot, so it never takes a pipeline CPU.
"""
import csv, re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

EXTS = {
    "src_video": (".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi", ".mxf"),
    "dub_audio": (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"),
    "dub_script": (".docx", ".doc", ".txt"),
}
_HEADER = {
    "episode": "number", "ep": "number", "episode number": "number", "ep number": "number", "number": "number",
    "source video": "video", "source video link": "video", "video": "video", "video link": "video",
    "src video": "video", "source": "video",
    "audio": "audio", "audio link": "audio", "target audio": "audio", "target audio link": "audio",
    "dub audio": "audio", "dub audio link": "audio",
    "script": "script", "script link": "script", "dub script": "script", "target script": "script",
}


def _cell(v):
    if v is None:
        return ""
    return str(v).strip()


def _as_url(v):
    s = _cell(v)
    if not s.lower().startswith(("http://", "https://")):
        return ""
    # Sheets sometimes wraps the Drive file in google.com/url?q=
    if "google.com/url" in s.lower() and "q=" in s:
        q = (parse_qs(urlparse(s).query).get("q") or [""])[0]
        if q.lower().startswith(("http://", "https://")):
            return q
    return s


def _hyperlink_target(cell):
    """The URL sitting under a Drive/Sheets filename (the blue underlined text)."""
    hl = getattr(cell, "hyperlink", None)
    if not hl:
        return ""
    for attr in ("target", "location"):
        u = _as_url(getattr(hl, attr, None))
        if u:
            return u
    return _as_url(hl)


def _hyperlink_formula(val):
    s = _cell(val)
    if not s.lstrip().upper().startswith("=HYPERLINK"):
        return ""
    m = re.search(r'HYPERLINK\s*\(\s*"([^"]+)"', s, re.I) or re.search(r"HYPERLINK\s*\(\s*'([^']+)'", s, re.I)
    return _as_url(m.group(1)) if m else ""


def _resolved_cell(cell, extra=None):
    """Visible text if it is already a URL, otherwise the hyperlink under it."""
    if not hasattr(cell, "value"):
        return _cell(cell)
    return (_as_url(cell.value) or _hyperlink_target(cell) or _hyperlink_formula(cell.value)
            or (extra or {}).get(cell.coordinate) or _cell(cell.value))


def _sheet_hyperlinks(ws):
    """Drive links Google Sheets stores on the worksheet when the cell text is only the filename."""
    out = {}
    for hl in getattr(ws, "_hyperlinks", None) or []:
        ref = getattr(hl, "ref", None)
        tgt = _as_url(getattr(hl, "target", None))
        if ref and tgt and ":" not in str(ref):
            out[str(ref)] = tgt
    return out


def _rows_of(path: Path):
    suf = path.suffix.lower()
    if suf == ".csv":
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return [list(r) for r in csv.reader(text.splitlines()) if any(_cell(c) for c in r)]
    if suf in (".xlsx", ".xlsm"):
        import openpyxl
        # not read_only: that drops the hyperlink under "TGS EPISODE 05.mp4"
        wb = openpyxl.load_workbook(path, data_only=False)
        try:
            ws = wb.worksheets[0]
            extra = _sheet_hyperlinks(ws)
            rows = []
            for row in ws.iter_rows():
                vals = [_resolved_cell(c, extra) for c in row]
                if any(_cell(v) for v in vals):
                    rows.append(vals)
            return rows
        finally:
            wb.close()
    raise ValueError("the sheet must be a .csv or .xlsx file")


_WORDS_FOR = (
    # the order matters: "episode number" is a number, "source video" a video, "audio (mp3)" an audio
    ("video", ("video", "movie", "footage")),
    ("audio", ("audio", "sound", "voice")),
    ("script", ("script", "docx", "doc")),
    ("number", ("episode", "episodes", "ep", "number", "no")),
)


def _header_name(cell):
    """Which column a title belongs to, or None.

    Titles are read by their words, so 'Dubbed Audio', 'Audio File', 'Audio (mp3)', 'Script (docx)' and
    'source video url' all count. Words that only say what the cell links to ('url', 'link', 'file')
    and the dub/target/source prefixes are ignored, so 'source name' and 'target name' do not match."""
    name = re.sub(r"[^a-z0-9 ]+", " ", re.sub(r"\s+", " ", _cell(cell).lower())).strip()
    if not name:
        return None
    if name in _HEADER:
        return _HEADER[name]
    words = [w for w in name.split() if w not in {"url", "urls", "link", "links", "file", "files", "address", "path",
                                                   "the", "dub", "dubbed", "target", "src", "source"}]
    for key, keywords in _WORDS_FOR:
        if any(w in keywords for w in words):
            if key == "number" and any(w in ("video", "audio", "script") for w in words):
                continue
            return key
    return None


def _header_map(row):
    keys = [_header_name(c) for c in row]
    if {"number", "video", "audio", "script"} <= set(keys):
        return {name: keys.index(name) for name in ("number", "video", "audio", "script")}
    return None


def parse_sheet(path):
    """-> [{number, video, audio, script}]. The first row may be a header, or the four columns in that order."""
    rows = _rows_of(Path(path))
    if not rows:
        raise ValueError("the sheet is empty")
    header = _header_map(rows[0])
    data = rows[1:] if header else rows
    start = 2 if header else 1
    if not header:
        first = _cell(rows[0][0]) if rows[0] else ""
        if not re.fullmatch(r"\d+(\.0+)?", first):
            found = [_cell(c) for c in rows[0] if _cell(c)]
            raise ValueError(
                "the first row is read as column titles, but they do not name the four columns "
                f"(found: {', '.join(repr(x) for x in found) or 'nothing'}). "
                "Use: episode number, source video, audio, script (any title that says video, audio or script works), "
                "or leave the titles out and put the four columns in that order")
        header = {"number": 0, "video": 1, "audio": 2, "script": 3}
    out, seen = [], set()
    for i, row in enumerate(data, start=start):
        if len(row) <= max(header.values()):
            raise ValueError(f"row {i} needs four columns: episode, source video, audio, script")
        raw_n = _cell(row[header["number"]])
        try:
            number = int(float(raw_n))
        except ValueError:
            raise ValueError(f"row {i}: episode number must be a whole number, not {raw_n!r}")
        if number < 1:
            raise ValueError(f"row {i}: episode number must be 1 or more")
        if number in seen:
            raise ValueError(f"episode {number} is in the sheet twice")
        seen.add(number)
        item = {"number": number}
        for key, label in (("video", "source video"), ("audio", "audio"), ("script", "script")):
            url = _cell(row[header[key]])
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError(
                    f"row {i}: the {label} link must start with http:// or https:// (got {url!r}). "
                    "If the Drive file is a hyperlink under the filename, upload .xlsx not .csv")
            item[key] = url
        out.append(item)
    if not out:
        raise ValueError("the sheet has no episodes")
    return out


def direct_url(url):
    """A share page is not the file. Turn the common Drive, Docs and Dropbox links into a download."""
    doc = re.search(r"docs\.google\.com/document/d/([^/?]+)", url)
    if doc:
        return f"https://docs.google.com/document/d/{doc.group(1)}/export?format=docx"
    m = re.search(r"drive\.google\.com/file/d/([^/?]+)", url)
    if not m and "drive.google.com" in url:
        m = re.search(r"[?&]id=([^&]+)", url)
    if m:
        return ("https://drive.usercontent.google.com/download?id="
                + m.group(1) + "&export=download&confirm=t")
    if "dropbox.com" in url and "dl=1" not in url:
        return url + ("&" if "?" in url else "?") + "dl=1"
    return url


def _filename(url, headers, kind):
    cd = headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    name = unquote(m.group(1)).strip() if m else Path(unquote(urlparse(url).path)).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip("._") or kind
    if Path(name).suffix.lower() not in EXTS[kind]:
        ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
        guess = { "video/mp4": ".mp4", "audio/mpeg": ".mp3", "audio/wav": ".wav",
                  "text/plain": ".txt",
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx"}.get(ctype)
        if guess:
            name += guess
    if Path(name).suffix.lower() not in EXTS[kind]:
        raise ValueError(f"the {kind.replace('_', ' ')} link did not come back as a "
                         f"{' / '.join(EXTS[kind])} file (got {name})")
    return name


def fetch(url, dest_dir, kind):
    """Download one link into dest_dir. Returns the file path. A web page is refused."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    req = Request(direct_url(url), headers={"User-Agent": "dubstudio/1.0"})
    with urlopen(req, timeout=600) as r:
        name = _filename(r.geturl(), r.headers, kind)
        dest = dest_dir / name
        head = r.read(64)
        if head.lstrip()[:1] == b"<" or head.lower().startswith(b"<!doctype") or b"<html" in head.lower():
            raise ValueError(f"the {kind.replace('_', ' ')} link opened a web page, not the file")
        with dest.open("wb") as f:
            f.write(head)
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    return dest
