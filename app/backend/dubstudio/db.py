"""The database. SQLite by default (one file, <library>/dubstudio.db, WAL mode, JSON stored as TEXT); Postgres when
`DATABASE_URL` is set, which is what the deployed platform uses (docs/09 §1).

Both speak the same SQL in the rest of the app: queries are written with `?` placeholders and plain column types,
and this module translates for Postgres (`?` -> `%s`, `INTEGER PRIMARY KEY` -> `BIGSERIAL`, `REAL` -> `DOUBLE
PRECISION`, `INSERT … RETURNING id` instead of lastrowid). JSON columns stay TEXT on both, so a library can be
dumped from one and loaded into the other.
"""
import json, re, sqlite3, threading, time
from pathlib import Path

from . import envfile

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT, email TEXT, pw TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'editor', active INTEGER DEFAULT 1, created REAL, updated REAL, last_login REAL);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created REAL, expires REAL, seen REAL, agent TEXT, ip TEXT);
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
CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY, episode_id INTEGER REFERENCES episodes(id) ON DELETE CASCADE, kind TEXT, name TEXT,
  path TEXT, bucket TEXT, key TEXT, size INTEGER, sent INTEGER DEFAULT 0, status TEXT DEFAULT 'waiting',
  error TEXT, user_id INTEGER, created REAL, started REAL, finished REAL);
CREATE INDEX IF NOT EXISTS ix_gen_threads ON gen_threads(episode_id, slot);
CREATE INDEX IF NOT EXISTS ix_jobs_ep ON jobs(episode_id, run);
CREATE INDEX IF NOT EXISTS ix_events_ep ON events(episode_id, id);
CREATE INDEX IF NOT EXISTS ix_sessions_token ON sessions(token);
CREATE INDEX IF NOT EXISTS ix_uploads_ep ON uploads(episode_id, id);
"""

# columns added after the first release (an existing library is upgraded in place, see DB._migrate)
ADDED_COLUMNS = (("shows", "owner_id", "INTEGER REFERENCES users(id)"),
                 ("episodes", "created_by", "INTEGER REFERENCES users(id)"),
                 # files uploaded in the browser live in R2; `path` is where a worker caches them while it runs
                 ("assets", "r2_key", "TEXT"),
                 ("assets", "origin", "TEXT DEFAULT 'local'"),      # 'local' = a file on this Mac, 'r2' = uploaded
                 ("assets", "uploaded", "REAL"),
                 ("shows", "sheet_key", "TEXT"),                    # the localisation sheet in R2
                 ("episodes", "queued", "REAL"))                    # when it went in the line (one episode at a time)

JSON_COLS = {"profile", "probe", "needs", "params", "data", "body", "cues", "style", "outputs", "check_result", "clip", "frames",
             "images", "result"}


def database_url():
    """The Postgres URL when the platform runs on one; None = SQLite in the library folder."""
    url = envfile.get("DATABASE_URL", "")
    return url if url.startswith(("postgres://", "postgresql://")) else None


def _pg_schema(sql):
    """The same schema, in Postgres words."""
    sql = sql.replace("INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY")
    sql = sql.replace(" COLLATE NOCASE", "")           # case-insensitive lookups use LOWER() in the queries
    sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)
    sql = re.sub(r"\bINTEGER\b", "BIGINT", sql)
    return sql


class DB:
    def __init__(self, path):
        self.url = database_url()
        self.lock = threading.RLock()
        self.kind = "postgres" if self.url else "sqlite"
        if self.url:
            import psycopg
            from psycopg.rows import dict_row
            self.path = None
            self.conn = psycopg.connect(self.url, autocommit=True, row_factory=dict_row)
            with self.lock:
                self.conn.execute(_pg_schema(SCHEMA))
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
            self.conn.row_factory = sqlite3.Row
            with self.lock:
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA foreign_keys=ON")
                self.conn.executescript(SCHEMA)
                self.conn.commit()
        self._migrate()

    # ---------- talking to either database ----------
    def _sql(self, sql):
        return sql.replace("?", "%s") if self.url else sql

    def _run(self, sql, args=()):
        """One statement, on either database. Returns the cursor."""
        if self.url:
            return self.conn.execute(self._sql(sql), tuple(args))
        return self.conn.execute(sql, tuple(args))

    def _columns(self, table):
        if self.url:
            rows = self.conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s",
                                     (table,)).fetchall()
            return {r["column_name"] for r in rows}
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _migrate(self):
        """Add the columns a newer version needs to a database made by an older one."""
        with self.lock:
            for table, col, decl in ADDED_COLUMNS:
                if col not in self._columns(table):
                    self._run(f"ALTER TABLE {table} ADD COLUMN {col} {_pg_schema(decl) if self.url else decl}")
            if not self.url:
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
            return [self._out(r) for r in self._run(sql, args).fetchall()]

    def one(self, sql, args=()):
        with self.lock:
            return self._out(self._run(sql, args).fetchone())

    def ex(self, sql, args=()):
        with self.lock:
            cur = self._run(sql, [self._in(a) for a in args])
            if not self.url:
                self.conn.commit()
                return cur.lastrowid
            return None

    def insert(self, table, **vals):
        vals = {k: self._in(v) for k, v in vals.items()}
        cols = ",".join(vals)
        sql = f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' * len(vals))})"
        if not self.url:
            return self.ex(sql, list(vals.values()))
        with self.lock:
            row = self._run(sql + " RETURNING id", [self._in(v) for v in vals.values()]).fetchone()
            if "id" in vals:        # an explicit id does not move Postgres' counter: keep it past the biggest id
                self._run(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                          f"(SELECT COALESCE(MAX(id), 1) FROM {table}))")
            return row["id"] if row else None

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
