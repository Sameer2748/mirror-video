"""Find an episode's input files in a show folder, guess their language, probe and fingerprint them."""
import hashlib, json, os, re, subprocess, zipfile
from pathlib import Path
from .config import cfg

VIDEO = {".mp4", ".m4v", ".mov", ".mkv"}
AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
SKIP_DIRS = {"output", "outputs", "work", "_to_delete", "node_modules", ".git", "qc_check", "frames"}
STOP = {
    "it": "che non di la il è un una per con sono ma lo si le del della mi ti questo come".split(),
    "en": "the and you to of is that it in was he she for with his her not are this".split(),
    "de": "der die und nicht ist das ich sie er zu den mit es ein eine auch auf".split(),
    "fr": "le la les et est que pas je vous il elle des un une pour dans ne".split(),
}
LANG_WORDS = {"it": ["italy", "italian", "italiano", "ita", "it"], "en": ["us", "en", "english", "usa"],
              "de": ["de", "german", "deutsch", "ger"], "fr": ["fr", "french", "francais"]}


def num_re(n):
    return re.compile(rf"(?<!\d)0*{int(n)}(?!\d)")


def guess_lang_text(text):
    words = re.findall(r"[a-zàèéìòùäöüßç']+", text.lower())[:4000]
    if not words:
        return None
    scores = {k: sum(w in set(v) for w in words) for k, v in STOP.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 5 else None


def read_text(path, limit=200000):
    p = Path(path)
    try:
        if p.suffix.lower() == ".docx":
            with zipfile.ZipFile(p) as z:
                xml = z.read("word/document.xml").decode("utf8", "replace")
            return re.sub(r"<[^>]+>", " ", xml)[:limit]
        return p.read_text(encoding="utf-8-sig", errors="replace")[:limit]
    except Exception:
        return ""


def lang_from_path(path, show_folder):
    parts = [x.lower() for x in Path(os.path.relpath(path, show_folder)).parts[:-1]]
    for lang, ws in LANG_WORDS.items():
        for part in parts:
            toks = re.split(r"[^a-z]+", part)
            if any(w in toks for w in ws):
                return lang
    return None


def scan_episode(show_folder, number, profile):
    """Candidates per kind for one episode. Source files use number + episode_offset."""
    folder = Path(show_folder)
    dub_n, src_n = int(number), int(number) + int(profile.get("episode_offset", 0))
    from . import presets
    dl, sl = presets.asr_lang(profile["dub_lang"], "it"), presets.asr_lang(profile["src_lang"], "en")
    found = {k: [] for k in ("src_video", "dub_audio", "dub_srt", "src_srt", "dub_script", "src_script", "names_csv")}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        if len(Path(root).relative_to(folder).parts) > 4:
            continue
        for f in files:
            p = Path(root) / f
            ext = p.suffix.lower()
            name = p.stem
            if ext == ".csv" and "adaptation" in name.lower() or ext == ".csv" and "character" in name.lower():
                found["names_csv"].append(str(p)); continue
            plang = lang_from_path(p, folder)
            if ext in VIDEO and num_re(src_n).search(name):
                found["src_video"].append(str(p))
            elif ext in AUDIO and num_re(dub_n).search(name):
                found["dub_audio"].append(str(p))
            elif ext in (".srt", ".docx", ".txt") and (num_re(dub_n).search(name) or num_re(src_n).search(name)):
                lang = guess_lang_text(read_text(p)) or plang
                kind = "srt" if ext == ".srt" else "script"
                if lang == dl and num_re(dub_n).search(name):
                    found[f"dub_{kind}"].append(str(p))
                elif lang == sl and num_re(src_n).search(name):
                    found[f"src_{kind}"].append(str(p))
    for k in found:
        found[k].sort(key=lambda x: (len(x), x))
    return found


def probe(path):
    try:
        out = subprocess.run([cfg.ffprobe, "-v", "error", "-show_entries",
                              "format=duration:stream=codec_type,width,height,r_frame_rate,sample_rate,channels",
                              "-of", "json", str(path)], capture_output=True, text=True, timeout=60).stdout
        d = json.loads(out or "{}")
    except Exception as e:
        return {"error": str(e)}
    res = {"duration": float(d.get("format", {}).get("duration", 0) or 0), "video": None, "audio": None}
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and not res["video"]:
            num, den = (s.get("r_frame_rate") or "0/1").split("/")
            res["video"] = {"width": s.get("width"), "height": s.get("height"),
                            "fps": round(float(num) / float(den), 3) if float(den) else 0}
        if s.get("codec_type") == "audio" and not res["audio"]:
            res["audio"] = {"sample_rate": s.get("sample_rate"), "channels": s.get("channels")}
    return res


def fingerprint(path, chunk=4 << 20):
    """Fast identity check for big media: size + first and last 4 MB."""
    p = Path(path)
    st = p.stat()
    h = hashlib.sha256(str(st.st_size).encode())
    with open(p, "rb") as f:
        h.update(f.read(chunk))
        if st.st_size > chunk:
            f.seek(max(st.st_size - chunk, 0))
            h.update(f.read(chunk))
    return h.hexdigest()[:32], st.st_size, st.st_mtime


def srt_stats(path):
    txt = read_text(path)
    cues = re.findall(r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", txt)
    def ms(t):
        h, m, s = t.replace(",", ".").split(":")
        return int((int(h) * 3600 + int(m) * 60 + float(s)) * 1000)
    return {"cues": len(cues), "last_end_s": round(ms(cues[-1][1]) / 1000, 2) if cues else 0,
            "language": guess_lang_text(txt)}


def check_asset(kind, path):
    """Probe one input; returns (info, problems)."""
    p = Path(path)
    if not p.exists():
        return {}, [f"{kind}: file not found: {path}"]
    fp, size, mtime = fingerprint(p)
    info = {"fingerprint": fp, "size": size, "mtime": mtime}
    probs = []
    if kind in ("src_video", "dub_audio"):
        pr = probe(p); info["probe"] = pr
        if pr.get("error") or not pr.get("duration"):
            probs.append(f"{kind}: ffprobe could not read it")
        if kind == "src_video" and not pr.get("video"):
            probs.append("src_video: no video stream")
        if kind == "dub_audio" and not pr.get("audio"):
            probs.append("dub_audio: no audio stream")
    elif kind.endswith("_srt"):
        st = srt_stats(p); info["probe"] = st
        if not st["cues"]:
            probs.append(f"{kind}: no subtitle cues")
    elif kind.endswith("_script"):
        info["probe"] = {"chars": len(read_text(p)), "language": guess_lang_text(read_text(p))}
    return info, probs
