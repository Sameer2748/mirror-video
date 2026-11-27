"""A show sheet of episode links: episode number, source video, dub audio, dub script.

The sheet is the extra way in. The folder scan and the browser upload stay as they are. One download
runs at a time, on its own slot, so it never takes a pipeline CPU.
"""
import csv, re, time
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

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


def _drive_id(url):
    m = re.search(r"docs\.google\.com/document/d/([^/?]+)", url)
    if m:
        return None
    m = re.search(r"drive\.google\.com/file/d/([^/?]+)", url)
    if not m and "drive.google.com" in url:
        m = re.search(r"[?&]id=([^&]+)", url)
    return m.group(1) if m else None


def direct_url(url):
    """A share page is not the file. Turn the common Drive, Docs and Dropbox links into a download."""
    doc = re.search(r"docs\.google\.com/document/d/([^/?]+)", url)
    if doc:
        return f"https://docs.google.com/document/d/{doc.group(1)}/export?format=docx"
    fid = _drive_id(url)
    if fid:
        return ("https://drive.usercontent.google.com/download?id="
                + fid + "&export=download&confirm=t")
    if "dropbox.com" in url and "dl=1" not in url:
        return url + ("&" if "?" in url else "?") + "dl=1"
    return url


def download_urls(url):
    """Drive often 404s one host after a burst of videos; try the other download URL too."""
    out, fid = [], _drive_id(url)
    if fid:
        out.append("https://drive.usercontent.google.com/download?id=" + fid + "&export=download&confirm=t")
        out.append("https://drive.google.com/uc?export=download&id=" + fid + "&confirm=t")
    d = direct_url(url)
    if d not in out:
        out.append(d)
    if url not in out:
        out.append(url)
    return out


class _DriveForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}
        self._in = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == "download-form":
            self._in = True
            self.action = a.get("action")
        if self._in and tag == "input" and a.get("name"):
            self.fields[a["name"]] = a.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form":
            self._in = False


def _virus_scan_url(html):
    p = _DriveForm()
    p.feed(html)
    if p.action and p.fields.get("id"):
        return p.action + ("&" if "?" in p.action else "?") + urlencode(p.fields)
    return _confirm_from_html(html)


def _confirm_from_html(html):
    """Virus-scan / quota pages that are not the classic download-form still carry id+confirm."""
    fid = (re.search(r'name="id"\s+value="([^"]+)"', html)
           or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", html))
    if not fid:
        return None
    confirm = (re.search(r'name="confirm"\s+value="([^"]+)"', html)
               or re.search(r"confirm=([0-9A-Za-z_-]+)", html))
    uuid = re.search(r'name="uuid"\s+value="([^"]+)"', html)
    q = {"id": fid.group(1), "export": "download", "confirm": confirm.group(1) if confirm else "t"}
    if uuid:
        q["uuid"] = uuid.group(1)
    return "https://drive.usercontent.google.com/download?" + urlencode(q)


JUNK_NAMES = {"view", "edit", "open", "uc", "download", "u", "file", "preview"}


def _filename(url, headers, kind):
    cd = headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    name = unquote(m.group(1)).strip() if m else Path(unquote(urlparse(url).path)).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip("._") or kind
    if Path(name).stem.lower() in JUNK_NAMES:
        name = kind
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


def _looks_like_html(head, headers):
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype in ("text/html", "application/xhtml+xml"):
        return True
    h = head.lstrip().lower()
    return h.startswith(b"<!doctype") or h.startswith(b"<html") or h[:1] == b"<" or b"<html" in h[:200]


def _save_response(r, dest_dir, kind):
    head = r.read(256)
    if _looks_like_html(head, r.headers):
        rest = (head + r.read()).decode("utf-8", "replace")
        nxt = _virus_scan_url(rest) or _confirm_from_html(rest)
        if nxt:
            raise _VirusScan(nxt)
        raise ValueError(f"the {kind.replace('_', ' ')} link opened a web page, not the file")
    name = _filename(r.geturl(), r.headers, kind)
    dest = dest_dir / name
    with dest.open("wb") as f:
        f.write(head)
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return dest


class _VirusScan(Exception):
    def __init__(self, url):
        super().__init__("virus scan")
        self.url = url


def _page_should_retry(msg):
    s = str(msg).lower()
    return "web page" in s or "got view" in s or "virus scan" in s


def fetch(url, dest_dir, kind):
    """Download one link into dest_dir. Returns the file path. A web page is refused.

    Drive often answers 404 / an HTML quota or virus-scan page after a burst of videos.
    Follow the confirm URL, try the other download host, and wait, instead of failing
    the episode on the first HTML page."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    last = None
    urls = download_urls(url)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    for attempt in range(8):
        for cand in list(urls):
            try:
                req = Request(cand, headers={"User-Agent": UA})
                with opener.open(req, timeout=600) as r:
                    return _save_response(r, dest_dir, kind)
            except _VirusScan as ex:
                urls = [ex.url] + [u for u in urls if u != ex.url]
                last = ex
            except HTTPError as ex:
                last = ex
                if ex.code not in (403, 404, 429, 500, 502, 503):
                    raise
            except ValueError as ex:
                last = ex
                if not _page_should_retry(ex):
                    raise
        time.sleep(min(45, 2 ** attempt))
    if last:
        raise last
    raise ValueError(f"the {kind.replace('_', ' ')} link could not be downloaded")
