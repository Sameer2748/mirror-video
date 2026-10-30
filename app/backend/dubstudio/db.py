"""SQLite storage. One file (<library>/dubstudio.db), WAL mode, JSON stored as TEXT."""
import json, sqlite3, threading, time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS shows (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT NOT NULL UNIQUE, folder TEXT NOT NULL,
  preset TEXT, profile TEXT NOT NULL, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY, show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE, number INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'new', work_dir TEXT, output_dir TEXT, current_version INTEGER, note TEXT,
  created REAL, updated REAL, UNIQUE(show_id, number));
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE, kind TEXT NOT NULL,
  path TEXT NOT NULL, size INTEGER, fingerprint TEXT, mtime REAL, probe TEXT, language TEXT, role TEXT DEFAULT 'input',
  created REAL, UNIQUE(episode_id, kind, role));
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE, run INTEGER NOT NULL,
  step TEXT NOT NULL, ord INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'waiting', progress REAL DEFAULT 0,
  item TEXT, message TEXT, needs TEXT, resource TEXT DEFAULT 'cpu', params TEXT, log_path TEXT, attempt INTEGER DEFAULT 0,
  started REAL, finished REAL, eta REAL, created REAL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts REAL, episode_id INTEGER, job_id INTEGER, kind TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS sentences (
  id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE, side TEXT, idx INTEGER,
  text TEXT, start_ms INTEGER, end_ms INTEGER, lip_s REAL, speaking INTEGER);
CREATE TABLE IF NOT EXISTS mapping (
  id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE, dub_idx INTEGER,
  src_first INTEGER, src_last INTEGER, kind TEXT, reason TEXT, score REAL, decided_by TEXT);
CREATE TABLE IF NOT EXISTS timeline_versions (
  id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE, version INTEGER NOT NULL,
  parent INTEGER, made_by TEXT, note TEXT, body TEXT NOT NULL, created REAL, UNIQUE(episode_id, version));
CREATE TABLE IF NOT EXISTS flags (
  id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE, version INTEGER,
  clip_id TEXT, kind TEXT, severity TEXT, message TEXT, status TEXT DEFAULT 'open', created REAL);
CREATE TABLE IF NOT EXISTS markers (
  id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE, t_ms INTEGER,
  text TEXT, source TEXT, resolved INTEGER DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS subtitle_tracks (
  id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE, language TEXT,
  kind TEXT, cues TEXT, style TEXT, updated REAL, UNIQUE(episode_id, language));
CREATE TABLE IF NOT EXISTS renders (
  id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE, version INTEGER,
  status TEXT, outputs TEXT, check_result TEXT, created REAL, finished REAL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS gen_threads (
  id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE, slot TEXT NOT NULL,
  clip TEXT, title TEXT, status TEXT, analysis TEXT, frames TEXT, prompt TEXT, error TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS gen_messages (
  id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL REFERENCES gen_threads(id) ON DELETE CASCADE, role TEXT NOT NULL,
  text TEXT, images TEXT, created REAL);
CREATE TABLE IF NOT EXISTS gen_jobs (
  id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL REFERENCES gen_threads(id) ON DELETE CASCADE, model TEXT,
  prompt TEXT, params TEXT, status TEXT, request_id TEXT, result TEXT, import_id TEXT, error TEXT, created REAL,
  finished REAL);
CREATE INDEX IF NOT EXISTS ix_gen_threads ON gen_threads(episode_id, slot);
CREATE INDEX IF NOT EXISTS ix_jobs_ep ON jobs(episode_id, run);
CREATE INDEX IF NOT EXISTS ix_events_ep ON events(episode_id, id);
"""

JSON_COLS = {"profile", "probe", "needs", "params", "data", "body", "cues", "style", "outputs", "check_result", "clip", "frames",
             "images", "result"}


class DB:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    @staticmethod
    def _out(row):
        if row is None:
            return None
        d = dict(row)
        for k in JSON_COLS & d.keys():
            if isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    pass
        return d

    @staticmethod
    def _in(v):
        return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v

    def q(self, sql, args=()):
        with self.lock:
            return [self._out(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        with self.lock:
            return self._out(self.conn.execute(sql, args).fetchone())

    def ex(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, [self._in(a) for a in args])
            self.conn.commit()
            return cur.lastrowid

    def insert(self, table, **vals):
        vals = {k: self._in(v) for k, v in vals.items()}
        cols = ",".join(vals)
        return self.ex(f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' * len(vals))})", list(vals.values()))

    def update(self, table, id_, **vals):
        if not vals:
            return
        sets = ",".join(f"{k}=?" for k in vals)
        self.ex(f"UPDATE {table} SET {sets} WHERE id=?", [self._in(v) for v in vals.values()] + [id_])

    def close(self):
        with self.lock:
            self.conn.close()


def now():
    return time.time()
