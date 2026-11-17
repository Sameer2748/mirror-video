"""A show sheet of episode links: episode number, source video, dub audio, dub script.

The sheet is the extra way in. The folder scan and the browser upload stay as they are. One download
runs at a time, on its own slot, so it never takes a pipeline CPU.
"""
import csv, re
from pathlib import Path
from urllib.parse import unquote, urlparse
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


def _rows_of(path: Path):
    suf = path.suffix.lower()
    if suf == ".csv":
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return [list(r) for r in csv.reader(text.splitlines()) if any(_cell(c) for c in r)]
    if suf in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.worksheets[0]
            return [[c.value for c in row] for row in ws.iter_rows() if any(_cell(c.value) for c in row)]
        finally:
            wb.close()
    raise ValueError("the sheet must be a .csv or .xlsx file")


def _header_map(row):
    keys = []
    for c in row:
        keys.append(_HEADER.get(re.sub(r"\s+", " ", _cell(c).lower())))
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
                raise ValueError(f"row {i}: the {label} link must start with http:// or https://")
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
