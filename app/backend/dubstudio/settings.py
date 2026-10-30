"""Milestone 7: the Settings page (AI gateway, paths, performance), health checks, daily database backups.

The AI key lives only in app/.env (gitignored, mode 600). It is written here and never returned, printed or logged:
the API only says whether a key is set."""
import json, os, re, shutil, sqlite3, subprocess, sys, time
from datetime import date
from pathlib import Path

from . import ai, gen
from .config import ROOT, HOME

ENV = ROOT / ".env"
CONFIG = ROOT / "config.json"
AI_KEYS = ("AI_BASE", "AI_EMAIL", "AI_KEY", "AI_MODEL", "AI_MODEL_FAST", "FAL_KEY")   # FAL_KEY: fal.ai (video generation)
APP_KEYS = {"tools_dir": "path", "models_dir": "path", "pipeline_python": "file", "face_python": "file", "library": "path",
            "cpu_slots": "int", "step_max_seconds": "int", "font": "file", "port": "int"}


# ---------------- AI gateway (app/.env) ----------------
def ai_settings():
    s = ai.settings()
    return {"base": s.get("AI_BASE", ""), "email": s.get("AI_EMAIL", ""), "key_set": bool(s.get("AI_KEY")),
            "model": s.get("AI_MODEL"), "model_fast": s.get("AI_MODEL_FAST"), "configured": ai.available(),
            "fal_set": bool(gen.fal_key())}


def write_ai(values, remove_key=False):
    """values: {AI_BASE, AI_EMAIL, AI_KEY, AI_MODEL, AI_MODEL_FAST} (missing or empty AI_KEY = keep the key)."""
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    new = {k: v.strip() for k, v in values.items() if k in AI_KEYS and isinstance(v, str)}
    for k in ("AI_KEY", "FAL_KEY"):                          # an empty key field keeps the saved key
        if not new.get(k):
            new.pop(k, None)
    if new.get("AI_BASE") and not re.match(r"^https?://", new["AI_BASE"]):
        raise ValueError("the gateway address must start with https://")
    if new.get("AI_EMAIL") and "@" not in new["AI_EMAIL"]:
        raise ValueError("the email does not look right")
    if any(re.search(r"\s", v) for k, v in new.items() if k != "AI_EMAIL"):
        raise ValueError("no spaces allowed in the AI settings")
    out, done = [], set()
    for ln in lines:
        m = re.match(r"\s*(?:export\s+)?(AI_\w+|FAL_KEY)\s*=", ln)
        if m and m.group(1) == "AI_KEY" and remove_key is True or (m and remove_key == m.group(1)):
            continue
        if m and m.group(1) in new:
            out.append(f"{m.group(1)}={new[m.group(1)]}"); done.add(m.group(1))
        else:
            out.append(ln)
    out += [f"{k}={v}" for k, v in new.items() if k not in done and v]
    tmp = ENV.with_suffix(".tmp")
    tmp.write_text("\n".join(out).rstrip("\n") + "\n")
    os.chmod(tmp, 0o600); tmp.replace(ENV)
    return ai_settings()


def test_ai():
    """A real call to the gateway: budget, spend, models. Never includes the key."""
    if not ai.available():
        return {"ok": False, "error": "address, email and key are needed"}
    try:
        info = ai.key_info()
        try:
            models = ai.models()
        except Exception:
            models = info.get("models") or []
        return {"ok": True, "spend": info.get("spend"), "max_budget": info.get("max_budget"), "models": models}
    except Exception as ex:
        return {"ok": False, "error": re.sub(r"(sk-|Bearer )\S+", r"\1…", str(ex))[:300]}


# ---------------- app settings (app/config.json) ----------------
def app_settings(cfg):
    f = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    cur = {"tools_dir": str(cfg.tools_dir), "models_dir": str(cfg.models_dir), "pipeline_python": str(cfg.pipeline_python),
           "face_python": str(cfg.face_python),
           "library": str(cfg.library), "cpu_slots": cfg.cpu_slots, "step_max_seconds": int(cfg.step_max_seconds),
           "font": cfg.font, "port": cfg.port}
    return {"values": cur, "saved": {k: f[k] for k in APP_KEYS if k in f},
            "from_env": [k for k in APP_KEYS if os.environ.get("DUBSTUDIO_" + {"tools_dir": "TOOLS", "pipeline_python": "PIPELINE_PY", "face_python": "FACE_PY",
                                                                               "models_dir": "MODELS"}.get(k, k.upper()))]}


def write_app(values):
    f = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    for k, v in values.items():
        if k not in APP_KEYS:
            continue
        kind = APP_KEYS[k]
        if v in (None, ""):
            f.pop(k, None); continue
        if kind == "int":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a whole number")
            if k == "cpu_slots" and not 1 <= v <= 8:
                raise ValueError("CPU slots must be 1 to 8")
            if k == "port" and not 1024 <= v <= 65535:
                raise ValueError("the port must be 1024 to 65535")
        else:
            p = Path(str(v)).expanduser()
            if kind == "path" and k != "library" and not p.is_dir():
                raise ValueError(f"{k}: folder not found: {p}")
            if kind == "file" and not p.is_file():
                raise ValueError(f"{k}: file not found: {p}")
            v = str(p)
        f[k] = v
    CONFIG.write_text(json.dumps(f, indent=2) + "\n")
    return f


# ---------------- health ----------------
_health_cache = {}


def health(cfg, db_path):
    """[{name, ok, detail}]: what Dub Studio needs on this Mac. The slow checks are cached for 5 minutes."""
    out = []
    add = lambda name, ok, detail: out.append({"name": name, "ok": bool(ok), "detail": detail})
    add("Pipeline scripts", (cfg.scripts / "dubstitch.py").exists(), str(cfg.scripts))
    add("Whisper models", cfg.models_dir.is_dir(), str(cfg.models_dir))
    key = ("py", str(cfg.pipeline_python))
    if key not in _health_cache or time.time() - _health_cache[key][0] > 300:
        try:
            p = subprocess.run([str(cfg.pipeline_python), "-c", "import faster_whisper, sentence_transformers, openpyxl, numpy, cv2"],
                               capture_output=True, text=True, timeout=120)
            _health_cache[key] = (time.time(), p.returncode == 0, (p.stderr.strip().splitlines() or [""])[-1][:160])
        except Exception as ex:
            _health_cache[key] = (time.time(), False, str(ex)[:160])
    _, ok, err = _health_cache[key]
    add("Pipeline Python + packages", ok, str(cfg.pipeline_python) if ok else f"{cfg.pipeline_python}: {err} (run scripts/setup_pipeline.sh)")
    key = ("face", str(cfg.face_python))                 # the face map's own environment
    if key not in _health_cache or time.time() - _health_cache[key][0] > 300:
        try:
            p = subprocess.run([str(cfg.face_python), "-c", "import insightface, onnxruntime, ultralytics"],
                               capture_output=True, text=True, timeout=180)
            _health_cache[key] = (time.time(), p.returncode == 0, (p.stderr.strip().splitlines() or [""])[-1][:160])
        except Exception as ex:
            _health_cache[key] = (time.time(), False, str(ex)[:160])
    _, ok, err = _health_cache[key]
    add("Face map Python (insightface, onnxruntime, ultralytics)", ok,
        str(cfg.face_python) if ok else f"{cfg.face_python}: {err} (run scripts/setup_face_env.sh)")
    for tool in ("ffmpeg", "ffprobe"):
        add(tool, shutil.which(tool), shutil.which(tool) or "not found (brew install ffmpeg)")
    add("Subtitle font (Poppins Bold)", cfg.font and Path(cfg.font).exists(), cfg.font or "not found: the pipeline falls back to another font")
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    add("Google Chrome (playback check)", chrome.exists(), str(chrome) if chrome.exists() else "optional: only for scripts/playback_check.py")
    du = shutil.disk_usage(cfg.library if cfg.library.exists() else HOME)
    add("Free disk space", du.free > 20e9, f"{du.free / 1e9:.0f} GB free on the library's disk" + ("" if du.free > 20e9 else " — an episode needs ~10 GB"))
    add("AI gateway", ai.available(), "configured" if ai.available() else "off: matching by hand (Settings → AI)")
    b = latest_backup(cfg.library)
    add("Database backup", b is not None, f"{b.name}" if b else "none yet")
    add("Kept awake while working", sys.platform != "darwin" or shutil.which("caffeinate"), "caffeinate" if shutil.which("caffeinate") else "not available")
    return out


# ---------------- backups ----------------
def backup_dir(library):
    return Path(library) / "backups"


def latest_backup(library):
    d = backup_dir(library)
    files = sorted(d.glob("dubstudio-*.db")) if d.is_dir() else []
    return files[-1] if files else None


def daily_backup(conn, library, keep=10):
    """One copy of the database per day (SQLite's online backup: safe while the app writes); keeps the newest 10."""
    d = backup_dir(library); d.mkdir(parents=True, exist_ok=True)
    target = d / f"dubstudio-{date.today().isoformat()}.db"
    if not target.exists():
        dst = sqlite3.connect(str(target))
        with dst:
            conn.backup(dst)
        dst.close()
    for old in sorted(d.glob("dubstudio-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return target
