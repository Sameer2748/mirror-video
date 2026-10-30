"""Clips the editor imports for one episode (the editor's project bin, like Premiere's "Import").

An imported clip is copied into the episode's work folder (W/imports/<id><ext>; the file it came from is never
changed), probed, and given a 540-px preview copy for the monitors. On the timeline a clip from it carries
`media: <id>`; its s0/s1 are times in that file. At render the edit list names the file per segment ("video"),
which the pipeline's render and verify use instead of the source video."""
import json, re, shutil, subprocess, threading, time, uuid
from pathlib import Path

from .config import cfg
from . import scan

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mxf", ".mts")
_lock = threading.Lock()


def folder(work):
    return Path(work) / "imports"


def _reg(work):
    return folder(work) / "imports.json"


def items(work):
    try:
        return json.loads(_reg(work).read_text())
    except Exception:
        return []


def _save(work, its):
    folder(work).mkdir(parents=True, exist_ok=True)
    tmp = _reg(work).with_suffix(".tmp"); tmp.write_text(json.dumps(its, indent=1, ensure_ascii=False)); tmp.replace(_reg(work))


def get(work, iid):
    return next((x for x in items(work) if x["id"] == iid), None)


def path_of(work, iid):
    x = get(work, iid)
    return str(folder(work) / x["file"]) if x else None


def preview_of(work, iid):
    """The file the monitors play: the 540-px copy once made, else the imported file itself."""
    x = get(work, iid)
    if not x:
        return None
    p = folder(work) / f"{iid}_540.mp4"
    return str(p) if x.get("preview") == "ready" and p.exists() else str(folder(work) / x["file"])


def add(work, name, write):
    """write(dest_path) puts the file's bytes there. -> the new item (preview copy made in the background)."""
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_EXT:
        raise ValueError(f"not a video file ({ext or 'no extension'}): {', '.join(VIDEO_EXT)}")
    iid = "imp" + uuid.uuid4().hex[:8]
    folder(work).mkdir(parents=True, exist_ok=True)
    dest = folder(work) / f"{iid}{ext}"
    write(dest)
    pr = scan.probe(dest)
    if not pr.get("video") or not pr.get("duration"):
        dest.unlink(missing_ok=True)
        raise ValueError(f"{name}: no video stream found (ffprobe)")
    it = dict(id=iid, name=Path(name).name, file=dest.name, duration_ms=round(pr["duration"] * 1000),
              width=pr["video"]["width"], height=pr["video"]["height"], fps=pr["video"]["fps"],
              size=dest.stat().st_size, added=time.time(), preview="making")
    with _lock:
        its = items(work); its.append(it); _save(work, its)
    threading.Thread(target=_make_preview, args=(str(work), iid), daemon=True).start()
    return it


def add_path(work, src):
    p = Path(src).expanduser()
    if not p.is_file():
        raise ValueError(f"file not found: {p}")
    return add(work, p.name, lambda dest: shutil.copyfile(p, dest))


def _set(work, iid, **kv):
    with _lock:
        its = items(work)
        for x in its:
            if x["id"] == iid:
                x.update(kv)
        _save(work, its)


def _make_preview(work, iid):
    src = path_of(work, iid); out = folder(work) / f"{iid}_540.mp4"
    try:
        r = subprocess.run([cfg.ffmpeg, "-y", "-v", "error", "-i", src, "-vf", "scale=-2:540", "-c:v", "libx264", "-preset",
                            "veryfast", "-crf", "26", "-g", "15", "-an", "-movflags", "+faststart", str(out)],
                           capture_output=True, text=True, timeout=3600)
        _set(work, iid, preview="ready" if r.returncode == 0 else "failed")
    except Exception:
        _set(work, iid, preview="failed")


def remove(work, iid):
    with _lock:
        its = items(work); x = next((y for y in its if y["id"] == iid), None)
        if not x:
            return False
        for f in (folder(work) / x["file"], folder(work) / f"{iid}_540.mp4"):
            f.unlink(missing_ok=True)
        _save(work, [y for y in its if y["id"] != iid])
    return True


def used_in(tl, iid):
    return [c for c in list(tl.get("clips") or []) + list(tl.get("v2") or []) if c.get("media") == iid]


def safe_name(name):
    return re.sub(r"[^\w.\- ]", "_", Path(name or "clip").name)[:120] or "clip"
