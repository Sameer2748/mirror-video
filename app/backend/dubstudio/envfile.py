"""Settings that live in <app>/.env (gitignored, mode 600), read the same way by every module.

Order: environment variable first, then the file, then the default -- the same order as `config.py`.
The values are secrets (AI key, R2 keys, the admin password): never print, log or copy them. The file is read
at most once a second (its modification time is checked), so a module can call `get()` per request.
"""
import os, re, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # the app folder (holds .env)
_cache = {"path": None, "mtime": 0, "read": 0.0, "values": {}}
LINE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def path():
    return ROOT / ".env"


def file_values():
    """Every KEY=VALUE of app/.env ({} when there is no file)."""
    p = path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        _cache.update(path=str(p), mtime=0, read=time.time(), values={})
        return {}
    if _cache["path"] == str(p) and _cache["mtime"] == mtime and time.time() - _cache["read"] < 1.0:
        return _cache["values"]
    values = {}
    for line in p.read_text().splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = LINE.match(line)
        if m:
            values[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    _cache.update(path=str(p), mtime=mtime, read=time.time(), values=values)
    return values


def get(key, default=None):
    v = os.environ.get(key)
    if v in (None, ""):
        v = file_values().get(key)
    return default if v in (None, "") else v


def flag(key, default=False):
    v = get(key)
    return default if v is None else str(v).strip().lower() in ("1", "true", "yes", "on")


def number(key, default):
    try:
        return type(default)(get(key, default))
    except (TypeError, ValueError):
        return default


def write(values, keep_empty=False):
    """Save KEY=VALUE pairs in app/.env, keeping the rest of the file (used by the Settings page).
    An empty value removes the line unless `keep_empty`."""
    p = path()
    lines = p.read_text().splitlines() if p.exists() else []
    new = {k: ("" if v is None else str(v)).strip() for k, v in values.items()}
    out, done = [], set()
    for ln in lines:
        m = LINE.match(ln)
        k = m.group(1) if m and not ln.lstrip().startswith("#") else None
        if k in new:
            done.add(k)
            if new[k] or keep_empty:
                out.append(f"{k}={new[k]}")
        else:
            out.append(ln)
    out += [f"{k}={v}" for k, v in new.items() if k not in done and v]
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(out).rstrip("\n") + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    _cache["mtime"] = 0
    return True
