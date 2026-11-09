"""FastAPI app: REST API under /api, live events on /api/ws, the built UI at /."""
import asyncio, json, os, re, shutil, tempfile, threading, time
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Body, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from .config import cfg, ROOT
from .db import DB, now
from .events import bus
from .runner import Runner
from . import auth, envfile, gen, imports, presets, scan, deliver, names as nm, settings as stg, storage, timeline as tlm
from .pipeline import steps as pipeline_steps
from .pipeline import decisions as dec

app = FastAPI(title="Mirror Video", version="0.1.0")
state = {}


def db() -> DB:
    return state["db"]


def runner() -> Runner:
    return state["runner"]


@app.on_event("startup")
def _startup():
    cfg.library.mkdir(parents=True, exist_ok=True)
    state["db"] = DB(cfg.db_path)
    bus.db = state["db"]
    bus.connect_redis(envfile.get("REDIS_URL"))      # one machine: not needed; several: every replica hears it all
    state["runner"] = Runner(state["db"])
    state["runner"].start()
    gen_interrupted(state["db"])
    accounts_ready(state["db"])
    try:
        stg.daily_backup(state["db"].conn, cfg.library)
        if storage.configured() and storage.settings()["db_sync"]:
            threading.Thread(target=_push_db_quietly, daemon=True).start()
        storage.start_worker(state["db"])          # finishes any upload that was running when the app stopped
    except Exception as ex:
        print("backup:", ex)


def accounts_ready(d):
    """The first account (app/.env ADMIN_USERNAME / ADMIN_PASSWORD) and, on an older library, every show without an
    owner goes to that account -- so nothing the owner made before accounts existed disappears."""
    with d.setup_lock():                             # two processes must not both make the first account
        auth.seed(d)
        admin = d.one("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
        if admin and d.one("SELECT id FROM shows WHERE owner_id IS NULL LIMIT 1"):
            d.ex("UPDATE shows SET owner_id=? WHERE owner_id IS NULL", (admin["id"],))


def _push_db_quietly():
    try:
        storage.push_db(state["db"].conn, cfg.library)
    except Exception as ex:
        print("R2 database copy:", str(ex)[:200])


def gen_interrupted(d):
    """AI chats / videos that were being made when the app stopped cannot finish: say so (the panel offers Try again)."""
    d.ex("UPDATE gen_threads SET status='error', error='interrupted: the app restarted while preparing this chat. "
         "Press Try again.' WHERE status IN ('frames','analysing','writing')")
    d.ex("UPDATE gen_jobs SET status='failed', error='interrupted: the app restarted while the video was being made. "
         "Generate again.' WHERE status IN ('starting','queued','running')")


@app.on_event("shutdown")
def _shutdown():
    storage.stop_worker()
    if "runner" in state:
        state["runner"].shutdown()


def _get(table, id_):
    row = db().one(f"SELECT * FROM {table} WHERE id=?", (id_,))
    if not row:
        name = {"batches": "batch"}.get(table, table[:-1])
        raise HTTPException(404, f"{name} {id_} not found")
    return row


# ---------------- accounts: who is signed in, and what they may open ----------------
# Everyone sees their own shows and episodes; the admin (the owner) sees them all. The check is done once here,
# for every /api path that carries an id, so no endpoint can forget it.
PUBLIC_API = {"/api/info", "/api/auth/login", "/api/auth/logout", "/api/auth/me"}
ADMIN_API = (re.compile(r"^/api/users"), re.compile(r"^/api/settings/"), re.compile(r"^/api/restart$"),
             re.compile(r"^/api/storage/db"))
OWNED_API = re.compile(r"^/api/(shows|episodes|jobs|markers|uploads|batches)/(\d+)")
OWNER_SQL = {
    "shows": "SELECT owner_id FROM shows WHERE id=?",
    "episodes": "SELECT s.owner_id AS owner_id FROM episodes e JOIN shows s ON s.id=e.show_id WHERE e.id=?",
    "jobs": "SELECT s.owner_id AS owner_id FROM jobs j JOIN episodes e ON e.id=j.episode_id "
            "JOIN shows s ON s.id=e.show_id WHERE j.id=?",
    "markers": "SELECT s.owner_id AS owner_id FROM markers m JOIN episodes e ON e.id=m.episode_id "
               "JOIN shows s ON s.id=e.show_id WHERE m.id=?",
    "uploads": "SELECT s.owner_id AS owner_id FROM uploads u JOIN episodes e ON e.id=u.episode_id "
               "JOIN shows s ON s.id=e.show_id WHERE u.id=?",
    "batches": "SELECT s.owner_id AS owner_id FROM batches b JOIN shows s ON s.id=b.show_id WHERE b.id=?",
}


def local_user():
    """Who we are when sign-in is switched off (AUTH_REQUIRED=false): the owner's own account."""
    row = db().one("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1")
    return auth.public(row) if row else {"id": None, "username": "local", "name": "This Mac", "role": "admin", "active": 1}


def user_of(request):
    if not auth.required():
        return local_user()
    return auth.public(auth.user_of_token(db(), request.cookies.get(auth.COOKIE)))


def me(request) -> dict:
    """The signed-in account of this request (the guard put it there)."""
    u = getattr(request.state, "user", None)
    return u or user_of(request) or {}


def _not_allowed(user, path, method):
    """None = allowed, else the message for a 403."""
    if not auth.is_admin(user):
        for rx in ADMIN_API:
            if rx.match(path):
                return "only the owner's account can change this"
        m = OWNED_API.match(path)
        if m:
            kind, id_ = m.group(1), int(m.group(2))
            row = db().one(OWNER_SQL[kind], (id_,))
            if row is None:
                return None                                   # it does not exist: the endpoint answers 404
            if row.get("owner_id") != user.get("id"):
                return "this belongs to another account"
    return None


@app.middleware("http")
async def accounts_guard(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in PUBLIC_API or "db" not in state:
        return await call_next(request)
    user = user_of(request)
    if not user:
        return JSONResponse({"detail": "sign in to use Mirror Video"}, status_code=401)
    request.state.user = user
    msg = _not_allowed(user, path, request.method)
    if msg:
        return JSONResponse({"detail": msg}, status_code=403)
    return await call_next(request)


@app.post("/api/auth/login")
def login(request: Request, body: dict = Body(...)):
    """{username, password} -> the account, and the session cookie."""
    try:
        user, token = auth.sign_in(db(), body.get("username"), body.get("password"),
                                   agent=request.headers.get("user-agent"), ip=request.client.host if request.client else None)
    except ValueError as ex:
        raise HTTPException(401, str(ex))
    out = JSONResponse({"user": auth.public(user), "required": auth.required()})
    out.set_cookie(value=token, **auth.cookie_kwargs())
    return out


@app.post("/api/auth/logout")
def logout(request: Request):
    auth.sign_out(db(), request.cookies.get(auth.COOKIE))
    out = JSONResponse({"ok": True})
    out.delete_cookie(auth.COOKIE, path="/")
    return out


@app.get("/api/auth/me")
def auth_me(request: Request):
    """Who is signed in (user = null when nobody is). The UI shows the sign-in screen on null."""
    return {"user": user_of(request), "required": auth.required(), "ttl_seconds": auth.ttl()}


@app.post("/api/auth/password")
def change_password(request: Request, body: dict = Body(...)):
    """Change your own password: {current, password}."""
    user = me(request)
    row = db().one("SELECT * FROM users WHERE id=?", (user.get("id"),)) if user.get("id") else None
    if not row:
        raise HTTPException(404, "no account to change")
    if not auth.verify_password(body.get("current") or "", row["pw"]):
        raise HTTPException(403, "the current password is wrong")
    try:
        auth.set_password(db(), row["id"], body.get("password"))
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    user2, token = auth.sign_in(db(), row["username"], body.get("password"))
    out = JSONResponse({"user": auth.public(user2)})
    out.set_cookie(value=token, **auth.cookie_kwargs())        # stay signed in on this device
    return out


# ---------------- accounts: the owner manages them ----------------
@app.get("/api/users")
def list_users():
    rows = db().q("SELECT * FROM users ORDER BY id")
    out = []
    for r in rows:
        u = auth.public(r)
        u["shows"] = (db().one("SELECT COUNT(*) AS n FROM shows WHERE owner_id=?", (r["id"],)) or {}).get("n", 0)
        out.append(u)
    return out


@app.post("/api/users")
def add_user(body: dict = Body(...)):
    """{username, password, name, role: admin|editor}."""
    try:
        u = auth.create_user(db(), body.get("username"), body.get("password"), name=body.get("name"),
                             role=body.get("role") or "editor", email=body.get("email"))
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return auth.public(u)


@app.patch("/api/users/{uid}")
def patch_user(request: Request, uid: int, body: dict = Body(...)):
    """{name, email, role, active, password} -- the owner may switch an account off or give it a new password."""
    row = _get("users", uid)
    vals = {k: body[k] for k in ("name", "email") if k in body}
    if "role" in body:
        if body["role"] not in auth.ROLES:
            raise HTTPException(400, f"the role must be one of {', '.join(auth.ROLES)}")
        if row["role"] == "admin" and body["role"] != "admin" and _admins() <= 1:
            raise HTTPException(409, "there must be one owner account left")
        vals["role"] = body["role"]
    if "active" in body:
        if not body["active"] and row["role"] == "admin" and _admins() <= 1:
            raise HTTPException(409, "there must be one owner account left")
        if not body["active"]:
            db().ex("DELETE FROM sessions WHERE user_id=?", (uid,))
        vals["active"] = 1 if body["active"] else 0
    if vals:
        db().update("users", uid, updated=now(), **vals)
    if body.get("password"):
        try:
            auth.set_password(db(), uid, body["password"])
        except ValueError as ex:
            raise HTTPException(400, str(ex))
    return auth.public(_get("users", uid))


@app.delete("/api/users/{uid}")
def delete_user(request: Request, uid: int):
    """Remove an account. Its shows move to the account that does this, so no episode is lost."""
    row = _get("users", uid)
    if row["role"] == "admin" and _admins() <= 1:
        raise HTTPException(409, "there must be one owner account left")
    if uid == me(request).get("id"):
        raise HTTPException(409, "you cannot remove the account you are signed in with")
    db().ex("UPDATE shows SET owner_id=? WHERE owner_id=?", (me(request).get("id"), uid))
    db().ex("DELETE FROM users WHERE id=?", (uid,))
    return {"deleted": uid}


def _admins():
    return (db().one("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND active=1") or {}).get("n", 0)


# ---------------- app info ----------------
@app.get("/api/info")
def info():
    return {"version": "0.1.0", "config": cfg.as_dict(), "tools_found": (cfg.scripts / "dubstitch.py").exists()}


@app.get("/api/presets")
def get_presets():
    return {k: v for k, v in presets.PRESETS.items()}


# ---------------- shows ----------------
def _own_filter(request, column="owner_id"):
    user = me(request)
    if auth.is_admin(user):
        return "", []
    return f" AND {column}=?", [user.get("id")]


@app.get("/api/shows")
def list_shows(request: Request):
    where, args = _own_filter(request)
    shows = db().q(f"SELECT * FROM shows WHERE 1=1{where} ORDER BY name", args)
    for s in shows:
        c = db().q("SELECT status, COUNT(*) AS n FROM episodes WHERE show_id=? GROUP BY status", (s["id"],))
        s["episode_counts"] = {r["status"]: r["n"] for r in c}
    return shows


@app.post("/api/shows")
def create_show(request: Request, body: dict = Body(...)):
    name = (body.get("name") or "").strip(); code = re.sub(r"[^A-Za-z0-9]", "", body.get("code") or "").upper()
    folder = (body.get("folder") or "").strip()
    if not name or not code:
        raise HTTPException(400, "name and code are required")
    # Two kinds of show: one whose files already sit in a folder on this Mac, and one where the editor uploads them
    # in the browser (then the "folder" is the show's own staging folder in the library -- docs/09 §3).
    uploads = not folder
    if uploads:
        folder = str(cfg.library / "shows" / code)
        Path(folder).mkdir(parents=True, exist_ok=True)
    elif not Path(folder).expanduser().is_dir():
        raise HTTPException(400, f"folder not found: {folder}")
    # one show code on this Mac: the code decides the output file names, so two accounts must not write the same files
    old = db().one("SELECT id, owner_id FROM shows WHERE code=?", (code,))
    if old:
        mine = old.get("owner_id") == me(request).get("id")
        raise HTTPException(409, f"a show with code {code} exists" if mine else
                            f"a show with code {code} belongs to another account")
    prof = presets.preset(body.get("preset") or "NEW")
    prof.update(body.get("profile") or {})
    presets.normalize_profile_langs(prof)
    prof["uploads"] = uploads                                # the screens ask for uploads instead of folders
    if not prof.get("names_sheet") and not uploads:          # the localisation sheet, found in the show folder
        found = nm.find(Path(folder).expanduser())
        if found:
            prof["names_sheet"] = found[0]
    sid = db().insert("shows", name=name, code=code, folder=str(Path(folder).expanduser()), preset=body.get("preset"),
                      profile=prof, owner_id=me(request).get("id"), created=now(), updated=now())
    return _get("shows", sid)


@app.get("/api/shows/{sid}")
def get_show(sid: int):
    return _get("shows", sid)


@app.patch("/api/shows/{sid}")
def patch_show(sid: int, body: dict = Body(...)):
    s = _get("shows", sid)
    vals = {}
    for k in ("name", "folder"):
        if k in body:
            vals[k] = body[k]
    if "profile" in body:
        prof = s["profile"]; new = body["profile"]
        if "flags" in new:
            new = {**new, "flags": _flag_limits({**(prof.get("flags") or {}), **(new["flags"] or {})})}
        if "sub_styles" in new:
            cur = prof.get("sub_styles") or {}
            new = {**new, "sub_styles": {lang: _sub_style({**(cur.get(lang) or {}), **(v or {})})
                                         for lang, v in {**cur, **(new["sub_styles"] or {})}.items() if lang in ("dub", "en")}}
        prof.update(new)
        presets.normalize_profile_langs(prof)
        vals["profile"] = prof
    db().update("shows", sid, updated=now(), **vals)
    return _get("shows", sid)


FLAG_KEYS = ("slow", "slow_red", "fast", "fast_red", "drift_ms", "short_ms", "blur")


def _flag_limits(f):
    """The review flag limits of a show (design: "Review flags"), checked so the colours still make sense."""
    try:
        f = {k: float(f[k]) for k in FLAG_KEYS if k in f}
    except (TypeError, ValueError):
        raise HTTPException(400, "flag limits must be numbers")
    if any(v <= 0 for v in f.values()):
        raise HTTPException(400, "flag limits must be above 0")
    if f.get("slow_red", 0) >= f.get("slow", 1) or f.get("slow", 0.67) >= 1:
        raise HTTPException(400, "slow motion: red must be below yellow, and yellow below ×1")
    if f.get("fast", 1.5) <= 1 or f.get("fast_red", 9) <= f.get("fast", 1.5):
        raise HTTPException(400, "fast: yellow must be above ×1, and red above yellow")
    if f.get("blur", 0.25) >= 1:
        raise HTTPException(400, "blur: the share of the usual sharpness must be below 1 (e.g. 0.25)")
    return f


def _sub_style(st):
    """A subtitle style (one language): style box|strip, size 16-90 px (on a 1080 frame), bottom 0-500 px, alpha 0-1."""
    out = {}
    if st.get("style") not in (None, "box", "strip"):
        raise HTTPException(400, "subtitle style must be box or strip")
    if st.get("style"):
        out["style"] = st["style"]
    for k, lo, hi, cast in (("size", 16, 90, int), ("bottom", 0, 500, int), ("alpha", 0, 1, float)):
        v = st.get(k)
        if v in (None, ""):
            continue
        try:
            v = cast(float(v))
        except (TypeError, ValueError):
            raise HTTPException(400, f"subtitle {k} must be a number")
        if not lo <= v <= hi:
            raise HTTPException(400, f"subtitle {k} must be between {lo} and {hi}")
        out[k] = v
    return out


def show_names_sheet(show):
    """The show's localisation sheet: the one chosen on the show page, else the best one found in its folder."""
    return nm.for_show(show)


def names_sheet_for(eid):
    e = _get("episodes", eid); pth = show_names_sheet(_get("shows", e["show_id"]))
    if pth:
        return pth
    a = db().one("SELECT path FROM assets WHERE episode_id=? AND kind='names_csv' AND role='input'", (eid,))
    return a["path"] if a else None


@app.get("/api/shows/{sid}/names")
def get_names(sid: int):
    s = _get("shows", sid); pth = show_names_sheet(s)
    r = nm.read(pth) if pth else {"format": None, "names": [], "error": None}
    return {"path": pth, "chosen": bool((s.get("profile") or {}).get("names_sheet")), "format": r["format"],
            "count": len(r["names"]), "names": r["names"][:500], "error": r["error"],
            "candidates": nm.find(s["folder"]) if Path(s["folder"]).is_dir() else []}


@app.put("/api/shows/{sid}/names")
def put_names(sid: int, body: dict = Body(...)):
    """{"path": file} to use that sheet for the show, {"path": null} to let the app find it."""
    s = _get("shows", sid); prof = s["profile"]; pth = (body.get("path") or "").strip()
    if pth:
        p = Path(pth).expanduser()
        if not p.is_file() or p.suffix.lower() not in nm.SHEET_EXT:
            raise HTTPException(400, "choose a .csv or .xlsx file")
        r = nm.read(p)
        if not r["names"]:
            raise HTTPException(400, f"no names read from that sheet ({r['error'] or r['format']}): it needs the columns 'source name' and 'target name'")
        prof["names_sheet"] = str(p)
    else:
        prof.pop("names_sheet", None)
    db().update("shows", sid, profile=prof, updated=now())
    return get_names(sid)


@app.get("/api/shows/{sid}/names.csv")
def names_csv(sid: int):
    """The show's names in the house format (source name, target name), to download and keep in the show folder."""
    s = _get("shows", sid); pth = show_names_sheet(s)
    if not pth:
        raise HTTPException(404, "no localisation sheet found for this show")
    from fastapi.responses import Response
    return Response(nm.to_csv(nm.read(pth)["names"]), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{s["code"]}_localisation_names.csv"'})


@app.delete("/api/shows/{sid}")
def delete_show(sid: int):
    _get("shows", sid)
    db().ex("DELETE FROM shows WHERE id=?", (sid,))
    return {"deleted": sid}


# ---------------- episodes ----------------
def _episode_full(eid):
    e = _get("episodes", eid)
    e["assets"] = db().q("SELECT * FROM assets WHERE episode_id=? ORDER BY kind", (eid,))
    run = runner().latest_run(eid)
    e["run"] = run
    e["jobs"] = db().q("SELECT * FROM jobs WHERE episode_id=? AND run=? ORDER BY ord", (eid, run)) if run else []
    for j in e["jobs"]:
        j["label"] = (j.get("params") or {}).get("label", j["step"]); j.pop("params", None)
        if j["step"] == "check" and j["status"] in ("done", "failed"):
            res = runner().final_check_result(e["jobs"])
            j["check_passed"] = res["passed"]
            j["check_lines"] = res["lines"] or runner().log_tail(j["log_path"], 15)
    e["show"] = _get("shows", e["show_id"])
    out = Path(e["output_dir"] or "")
    e["output_existing"] = sorted(c.name for c in out.iterdir() if not c.name.startswith(".")) if e["output_dir"] and out.is_dir() else []
    if e["status"] == "queued":
        e["queue_position"] = runner().queue_position(eid)
    return e


NUMBERED_EXTS = {
    "src_video": (".mp4", ".mov", ".mkv", ".m4v"),
    "dub_audio": (".mp3", ".wav", ".m4a", ".aac"),
    "dub_script": (".docx", ".doc", ".txt"),
}
BATCH_MAX = 100


def _episode_range(body):
    """Accept either {start, end} or {range: '1-20'}."""
    raw = str(body.get("range") or "").strip()
    if raw:
        m = re.fullmatch(r"(\d+)\s*[-–—:to]+\s*(\d+)", raw, re.I)
        if not m:
            raise HTTPException(400, "range must look like 1-20")
        start, end = int(m.group(1)), int(m.group(2))
    else:
        try:
            start, end = int(body.get("start")), int(body.get("end"))
        except Exception:
            raise HTTPException(400, "start and end episode numbers are required (or a range like 1-20)")
    if start < 1 or end < start:
        raise HTTPException(400, "the range must go from a first episode to a later one (e.g. 1-20)")
    if end - start + 1 > BATCH_MAX:
        raise HTTPException(400, f"a bulk run can cover at most {BATCH_MAX} episodes")
    return start, end


# Resolutions / years that look like episode numbers inside a long file name.
_NOT_EPISODE = {360, 480, 720, 1080, 1440, 2160, 1920, 1280, 3840, 4096}
_SEASON_EP = re.compile(r"(?i)s\d{1,2}e0*([1-9]\d{0,2})(?!\d)")
_MARKED_EP = re.compile(r"(?i)(?:^|[^a-z0-9])(?:ep(?:isode)?|épisode|puntata|folge|eo|e|#)[\s._-]*0*([1-9]\d{0,2})(?!\d)")
_BARE_EP = re.compile(r"(?:^|\D)0*([1-9]\d{0,2})(?!\d)")


def _name_stem(name):
    """`TAB FR eo 14 .mp4` -> `tab fr eo 14` (spaces, underscores and dots collapse)."""
    base = Path(str(name)).name.strip()
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return re.sub(r"[\s._\-]+", " ", base).strip().lower()


def episode_from_name(name, allowed=None):
    """Episode number in a file name. `11.mp4` still works; so does `TAB FR eo 14 .mp4` or `S01E11`.
    `allowed` is the range the user typed — only those numbers count, so `S01E11` is 11, not 1."""
    stem = _name_stem(name)
    hits = []
    for m in _SEASON_EP.finditer(stem):
        hits.append((int(m.group(1)), "season_ep"))
    for m in _MARKED_EP.finditer(stem):
        hits.append((int(m.group(1)), "marked"))
    # S01E11: do not also treat the "01" as episode 1
    bare_src = _SEASON_EP.sub(" ", stem)
    for m in _BARE_EP.finditer(bare_src):
        n = int(m.group(1))
        if n in _NOT_EPISODE or 1900 <= n <= 2099:
            continue
        hits.append((n, "bare"))
    if allowed is not None:
        want = {int(x) for x in allowed}
        hits = [(n, k) for n, k in hits if n in want]
    if not hits:
        return None
    for kind in ("season_ep", "marked", "bare"):
        ns = {n for n, k in hits if k == kind}
        if len(ns) == 1:
            return ns.pop()
        if len(ns) > 1:
            return None
    ns = {n for n, _ in hits}
    return ns.pop() if len(ns) == 1 else None


def find_numbered(folder, n, exts):
    """A file for episode `n` in a folder. Exact `11.mp4` / `ep11.mp4` win; otherwise any name that
    clearly holds that number (`TAB_FR_S01E11.mp4`). First matching extension wins among ties."""
    if not folder:
        return None
    p = Path(str(folder)).expanduser()
    if not p.is_dir():
        return None
    n = int(n)
    ext_rank = {e.lower() if e.startswith(".") else f".{e.lower()}": i for i, e in enumerate(exts)}
    hits = []
    for c in p.iterdir():
        if not c.is_file() or c.suffix.lower() not in ext_rank:
            continue
        if episode_from_name(c.name, {n}) == n:
            stem = c.stem.lower()
            exact = stem in {str(n), f"{n:02d}", f"{n:03d}", f"ep{n}", f"ep{n:02d}"}
            hits.append((0 if exact else 1, ext_rank[c.suffix.lower()], c.name.lower(), str(c)))
    return min(hits)[-1] if hits else None


def unused_in_folder(folder, start, end, exts):
    """Media in the folder that is not for start..end. Extra 31.mp4 / trailer.mp4 stay on disk."""
    if not folder:
        return []
    p = Path(str(folder)).expanduser()
    if not p.is_dir():
        return []
    used = set()
    for n in range(start, end + 1):
        hit = find_numbered(folder, n, exts)
        if hit:
            used.add(Path(hit).name.lower())
    extra = [c.name for c in p.iterdir()
             if c.is_file() and c.suffix.lower() in exts and c.name.lower() not in used]
    return sorted(extra, key=str.lower)


def _new_episode(request, show, n, batch_id=None, output_dir=None):
    """Create one episode row and pick up any files already in the show folder. Same path as a single New episode."""
    sid = show["id"]
    if db().one("SELECT id FROM episodes WHERE show_id=? AND number=?", (sid, n)):
        raise HTTPException(409, f"episode {n} exists")
    out = Path(show["folder"]) / show["profile"].get("output_dir", "output/ep{N}").format(N=n)
    if (output_dir or "").strip():
        out = Path(output_dir.strip()).expanduser()
        if not out.is_absolute():
            out = Path(show["folder"]) / out
    work = cfg.work_dir(show["code"], n)
    if work.exists() and any(work.iterdir()):
        # a work folder left by a deleted episode: moved aside (never deleted), so every step runs again for real
        # instead of finding the old results (e.g. a lip scan "done" in 0 s)
        work.rename(work.with_name(f"{work.name}_old_{time.strftime('%Y%m%d_%H%M%S')}"))
    vals = dict(show_id=sid, number=n, status="new", work_dir=str(work), output_dir=str(out),
                created_by=me(request).get("id"), created=now(), updated=now())
    if batch_id:
        vals["batch_id"] = batch_id
    eid = db().insert("episodes", **vals)
    found = scan.scan_episode(show["folder"], n, show["profile"])
    for kind, paths in found.items():
        if paths:
            db().insert("assets", episode_id=eid, kind=kind, path=paths[0], role="input", created=now())
    return eid, found


def _set_input(eid, kind, path):
    db().ex("DELETE FROM assets WHERE episode_id=? AND kind=? AND role='input'", (eid, kind))
    if path:
        db().insert("assets", episode_id=eid, kind=kind, path=path, role="input", created=now())


def _decorate_episodes(eps):
    """Progress / current step / input files for many episodes: two queries, not two per row.

    The 35-episode bulk page was calling this once per episode (70 round-trips) and 502'ing when library
    jobs also sat on the API process."""
    if not eps:
        return eps
    ids = [e["id"] for e in eps]
    ph = ",".join("?" * len(ids))
    runs = {r["episode_id"]: r["r"] for r in db().q(
        f"SELECT episode_id, MAX(run) AS r FROM jobs WHERE episode_id IN ({ph}) GROUP BY episode_id", ids)}
    jobs_by_ep = {}
    if runs:
        for j in db().q(
                f"SELECT episode_id, run, step, status, progress, params FROM jobs "
                f"WHERE episode_id IN ({ph}) ORDER BY episode_id, ord", ids):
            if j["run"] != runs.get(j["episode_id"]):
                continue
            jobs_by_ep.setdefault(j["episode_id"], []).append(j)
    have_by_ep = {}
    for a in db().q(
            f"SELECT episode_id, kind FROM assets WHERE episode_id IN ({ph}) AND role='input'", ids):
        have_by_ep.setdefault(a["episode_id"], set()).add(a["kind"])
    rnr = None
    for e in eps:
        if e.get("status") == "queued":
            if rnr is None:
                rnr = runner()
            e["queue_position"] = rnr.queue_position(e["id"])
        run = runs.get(e["id"])
        e["run"] = run
        e["progress"] = None
        e["step"] = None
        e["step_label"] = None
        e["steps_done"] = 0
        e["steps"] = 0
        js = jobs_by_ep.get(e["id"]) or []
        if run:
            e["steps"] = len(js)
            e["progress"] = round(sum(1 if j["status"] in ("done", "skipped") else (j["progress"] or 0) for j in js)
                                  / max(len(js), 1), 3)
            e["steps_done"] = sum(1 for j in js if j["status"] in ("done", "skipped"))
            cur = next((j for j in js if j["status"] == "running"), None) \
                or next((j for j in js if j["status"] == "needs_input"), None) \
                or next((j for j in js if j["status"] == "failed"), None)
            if cur:
                e["step"] = cur["step"]
                e["step_label"] = (cur.get("params") or {}).get("label", cur["step"])
        have = have_by_ep.get(e["id"]) or set()
        e["have"] = sorted(have)
        e["ready"] = "src_video" in have and "dub_audio" in have
    return eps


def _decorate_episode(e):
    """Progress, current step and which input files are already there — used by the show table and bulk status."""
    return _decorate_episodes([e])[0]


def _batch_status(eps):
    if not eps:
        return "new"
    sts = [e["status"] for e in eps]
    if all(s == "new" for s in sts):
        return "new"
    if any(s in ("processing", "queued") for s in sts):
        return "processing"
    if any(s == "needs_input" for s in sts):
        return "needs_input"
    if any(s == "review" for s in sts):
        return "review"
    if all(s == "done" for s in sts):
        return "done"
    if any(s == "failed" for s in sts):
        return "failed"
    if all(s in ("cancelled", "new") for s in sts):
        return "cancelled"
    return "processing"


def _batch_full(bid):
    b = _get("batches", bid)
    b["show"] = _get("shows", b["show_id"])
    eps = db().q("SELECT * FROM episodes WHERE batch_id=? ORDER BY number", (bid,))
    b["episodes"] = _decorate_episodes(eps)
    b["status"] = _batch_status(b["episodes"])
    b["counts"] = {}
    for e in b["episodes"]:
        b["counts"][e["status"]] = b["counts"].get(e["status"], 0) + 1
    return b


@app.get("/api/shows/{sid}/episodes")
def list_episodes(sid: int):
    eps = db().q("SELECT * FROM episodes WHERE show_id=? ORDER BY number", (sid,))
    return _decorate_episodes(eps)


@app.post("/api/shows/{sid}/episodes")
def create_episode(request: Request, sid: int, body: dict = Body(...)):
    s = _get("shows", sid)
    try:
        n = int(body.get("number"))
    except Exception:
        raise HTTPException(400, "number is required")
    eid, found = _new_episode(request, s, n, output_dir=body.get("output_dir"))
    e = _episode_full(eid); e["candidates"] = found
    return e


# ---------------- bulk generation: many episodes in one go, same queue as a single episode ----------------
@app.post("/api/shows/{sid}/batches/preview")
def preview_batch(sid: int, body: dict = Body(...)):
    """Match numbered files in three folders against the range, without creating anything."""
    _get("shows", sid)
    start, end = _episode_range(body)
    dirs = {k: (body.get(k) or "").strip() or None for k in ("src_video_dir", "dub_audio_dir", "dub_script_dir")}
    existing = {r["number"] for r in db().q(
        "SELECT number FROM episodes WHERE show_id=? AND number BETWEEN ? AND ?", (sid, start, end))}
    rows = []
    for n in range(start, end + 1):
        files = {kind: find_numbered(dirs[f"{kind}_dir"], n, exts) for kind, exts in NUMBERED_EXTS.items()}
        missing = [k for k in ("src_video", "dub_audio") if not files[k]]
        rows.append({"number": n, **files, "missing": missing, "exists": n in existing})
    skipped = {kind: unused_in_folder(dirs[f"{kind}_dir"], start, end, exts)
               for kind, exts in NUMBERED_EXTS.items()}
    return {"start": start, "end": end, "episodes": rows,
            "ready": sum(1 for r in rows if not r["missing"] and not r["exists"]),
            "blocked": [r["number"] for r in rows if r["exists"]],
            "skipped": skipped}


@app.post("/api/shows/{sid}/batches")
def create_batch(request: Request, sid: int, body: dict = Body(...)):
    """Make every episode in the range. Optional folders (`1.mp4` / `1.mp3` / `1.docx`) are attached as inputs."""
    s = _get("shows", sid)
    start, end = _episode_range(body)
    existing = [r["number"] for r in db().q(
        "SELECT number FROM episodes WHERE show_id=? AND number BETWEEN ? AND ?", (sid, start, end))]
    if existing:
        raise HTTPException(409, f"episode{'s' if len(existing) > 1 else ''} already exist: "
                                 + ", ".join(str(n) for n in existing))
    dirs = {k: (body.get(f"{k}_dir") or "").strip() or None for k in NUMBERED_EXTS}
    bid = db().insert("batches", show_id=sid, start_num=start, end_num=end, status="new",
                      created_by=me(request).get("id"), created=now(), updated=now())
    for n in range(start, end + 1):
        eid, _ = _new_episode(request, s, n, batch_id=bid, output_dir=body.get("output_dir"))
        for kind, exts in NUMBERED_EXTS.items():
            path = find_numbered(dirs[kind], n, exts)
            if path:
                _set_input(eid, kind, path)
    return _batch_full(bid)


@app.get("/api/shows/{sid}/batches")
def list_batches(sid: int):
    _get("shows", sid)
    rows = db().q("SELECT * FROM batches WHERE show_id=? ORDER BY id DESC", (sid,))
    for b in rows:
        eps = db().q("SELECT id, number, status FROM episodes WHERE batch_id=? ORDER BY number", (b["id"],))
        b["episodes"] = eps
        b["status"] = _batch_status(eps)
        b["counts"] = {}
        for e in eps:
            b["counts"][e["status"]] = b["counts"].get(e["status"], 0) + 1
    return rows


@app.get("/api/batches/{bid}")
def get_batch(bid: int):
    return _batch_full(bid)


@app.post("/api/batches/{bid}/process")
def process_batch(bid: int):
    """Queue every episode in the batch that has its source video and dub audio. Same line as a single episode."""
    b = _batch_full(bid)
    results = []
    for e in b["episodes"]:
        if e["status"] in ("processing", "queued"):
            results.append({"id": e["id"], "number": e["number"], "ok": True, "skipped": "already in the queue"})
            continue
        if not e["ready"]:
            results.append({"id": e["id"], "number": e["number"], "ok": False,
                            "error": "missing " + ", ".join(k for k in ("src_video", "dub_audio") if k not in e["have"])})
            continue
        try:
            out = runner().submit(e["id"])
            results.append({"id": e["id"], "number": e["number"], "ok": True, **out})
        except ValueError as ex:
            results.append({"id": e["id"], "number": e["number"], "ok": False, "error": str(ex)})
    db().update("batches", bid, status="processing", updated=now())
    return {**_batch_full(bid), "results": results}


@app.post("/api/batches/{bid}/cancel")
def cancel_batch(bid: int):
    b = _batch_full(bid)
    for e in b["episodes"]:
        if e["status"] == "queued":
            db().update("episodes", e["id"], status="cancelled" if runner().latest_run(e["id"]) else "new",
                        queued=None, updated=now())
            bus.publish("episode_status", e["id"], status="left the queue")
        elif e["status"] == "processing":
            runner().cancel_run(e["id"])
            db().update("episodes", e["id"], status="cancelled", queued=None, updated=now())
            bus.publish("episode_status", e["id"], status="cancelled")
    runner().start_next()
    db().update("batches", bid, status="cancelled", updated=now())
    return _batch_full(bid)


def _render_review_list(eps, note):
    """Start render for every episode waiting in review. Processing / queued / new are left alone.
    A black gap skips that episode. Flags do not: first upload can go out, an editor can fix later."""
    results = []
    for e in eps:
        if e["status"] != "review":
            continue
        try:
            out = _render_as_is(e["id"], note=note)
            results.append({"id": e["id"], "number": e["number"], "ok": True, "version": out["version"]})
        except HTTPException as ex:
            detail = ex.detail
            results.append({"id": e["id"], "number": e["number"], "ok": False,
                            "error": detail if isinstance(detail, str) else json.dumps(detail)})
        except Exception as ex:
            results.append({"id": e["id"], "number": e["number"], "ok": False, "error": str(ex)})
    return results


@app.post("/api/batches/{bid}/render")
def render_batch_review(bid: int):
    """Render every episode in this bulk that is waiting in review. Does not touch processing ones."""
    b = _batch_full(bid)
    results = _render_review_list(b["episodes"], "rendered from the bulk list")
    return {**_batch_full(bid), "results": results}


@app.post("/api/shows/{sid}/render-review")
def render_show_review(sid: int):
    """Same as the bulk Render-all: only episodes of this show whose status is review."""
    _get("shows", sid)
    eps = db().q("SELECT * FROM episodes WHERE show_id=? AND status='review' ORDER BY number", (sid,))
    return {"results": _render_review_list(eps, "rendered from the show list")}


@app.get("/api/episodes/{eid}")
def get_episode(eid: int):
    return _episode_full(eid)


@app.patch("/api/episodes/{eid}")
def patch_episode(eid: int, body: dict = Body(...)):
    e = _get("episodes", eid); vals = {}
    if "output_dir" in body:
        run = runner().latest_run(eid)
        if run and db().one("SELECT id FROM jobs WHERE episode_id=? AND run=? AND status IN ('waiting','running','needs_input')",
                            (eid, run)):
            raise HTTPException(409, "the episode is being processed with the current folder; cancel it first")
        out = Path((body["output_dir"] or "").strip()).expanduser()
        if not str(out) or str(out) == ".":
            raise HTTPException(400, "output_dir is empty")
        if not out.is_absolute():
            out = Path(_get("shows", e["show_id"])["folder"]) / out
        vals["output_dir"] = str(out)
    if "note" in body:
        vals["note"] = body["note"]
    if vals:
        db().update("episodes", eid, updated=now(), **vals)
    return _episode_full(eid)


@app.get("/api/episodes/{eid}/rows")
def episode_rows(eid: int):
    """The timed sentences of both sides (prep.json), for the Check matching screen."""
    e = _get("episodes", eid); f = Path(e["work_dir"]) / "prep.json"
    if not f.exists():
        raise HTTPException(409, "the automatic matching has not run yet")
    p = json.loads(f.read_text())
    keep = lambda r: {k: r.get(k) for k in ("text", "start_ms", "end_ms")}
    return {"dub": [keep(r) for r in p["dub"]], "src": [keep(r) for r in p["src"]], "auto": p.get("auto", {}),
            "alts": dec.alternatives(p)}


@app.get("/api/episodes/{eid}/scan")
def rescan(eid: int):
    e = _get("episodes", eid); s = _get("shows", e["show_id"])
    return scan.scan_episode(s["folder"], e["number"], s["profile"])


@app.put("/api/episodes/{eid}/assets")
def set_assets(eid: int, body: dict = Body(...)):
    """{kind: path or null}"""
    _get("episodes", eid)
    for kind, path in body.items():
        db().ex("DELETE FROM assets WHERE episode_id=? AND kind=? AND role='input'", (eid, kind))
        if path:
            if not Path(path).exists():
                raise HTTPException(400, f"{kind}: file not found: {path}")
            db().insert("assets", episode_id=eid, kind=kind, path=path, role="input", created=now())
    return _episode_full(eid)


# ---------------- uploads: the browser sends the files, they live in R2 (docs/09) ----------------
UPLOAD_KINDS = ("src_video", "dub_audio", "dub_script", "src_script", "dub_srt", "src_srt", "names_csv")


def _save_upload(up: UploadFile, dest: Path, max_bytes=20 << 30):
    """Write the browser's file to disk a megabyte at a time (a source video is hundreds of MB)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with dest.open("wb") as f:
        while True:
            chunk = up.file.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                f.close(); dest.unlink(missing_ok=True)
                raise HTTPException(413, "that file is too big")
            f.write(chunk)
    return size


@app.post("/api/shows/{sid}/sheet")
def upload_sheet(sid: int, file: UploadFile = File(...)):
    """The show's localisation sheet (source name | target name), uploaded when the show is made."""
    show = _get("shows", sid)
    name = storage.safe_name(file.filename)
    if not name.lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "the localisation sheet must be a .csv or .xlsx file")
    local = Path(show["folder"]) / "localisation" / name
    size = _save_upload(file, local)
    key = None
    if storage.configured():
        key = storage.sheet_key(show, name)
        storage.put_file(local, key)
    prof = show["profile"]; prof["names_sheet"] = str(local)
    db().update("shows", sid, sheet_key=key, profile=prof, updated=now())
    sheet = nm.read(str(local))
    return {"name": name, "size": size, "path": str(local), "key": key, "names": len(sheet["names"]),
            "format": sheet["format"], "problem": sheet.get("error")}


@app.post("/api/episodes/{eid}/files/{kind}")
def upload_file(eid: int, kind: str, file: UploadFile = File(...)):
    """One of the episode's input files: it lands in the work folder and goes straight up to R2."""
    if kind not in UPLOAD_KINDS:
        raise HTTPException(400, f"unknown file kind {kind}")
    ep = _get("episodes", eid); show = _get("shows", ep["show_id"])
    if db().one("SELECT id FROM jobs WHERE episode_id=? AND status IN ('waiting','running')", (eid,)):
        raise HTTPException(409, "this episode is processing: cancel it before changing its files")
    name = storage.safe_name(file.filename)
    local = Path(storage.cache_path(ep, kind, name))
    size = _save_upload(file, local)
    key = None
    if storage.configured():
        key = storage.input_key(show, ep, kind, name)
        storage.put_big_file(local, key)                      # one PUT for small files, parts for big ones
    return _register_asset(eid, kind, name, local, key, size)


# ---- big files (GB): the browser sends them in parts, so a dropped line only costs one part ----
def _ep_show(eid):
    ep = _get("episodes", eid)
    return ep, _get("shows", ep["show_id"])


def _register_asset(eid, kind, name, local, key, size):
    info, problems = scan.check_asset(kind, str(local)) if Path(local).exists() else ({}, [])
    db().ex("DELETE FROM assets WHERE episode_id=? AND kind=? AND role='input'", (eid, kind))
    db().insert("assets", episode_id=eid, kind=kind, path=str(local), role="input", size=size, r2_key=key,
                origin="r2" if key else "local", uploaded=now(), fingerprint=(info or {}).get("fingerprint"),
                mtime=(info or {}).get("mtime"), probe=(info or {}).get("probe"), created=now())
    bus.publish("upload", eid, store=False, file_kind=kind, status="done", name=name)
    return {"kind": kind, "name": name, "size": size, "key": key, "in_cloud": bool(key),
            "info": (info or {}).get("probe"), "problems": problems}


@app.post("/api/episodes/{eid}/files/{kind}/start")
def upload_start(eid: int, kind: str, body: dict = Body(...)):
    """Begin a big upload: the browser then sends the file in parts (any size, a few GB is fine)."""
    if kind not in UPLOAD_KINDS:
        raise HTTPException(400, f"unknown file kind {kind}")
    ep, show = _ep_show(eid)
    if db().one("SELECT id FROM jobs WHERE episode_id=? AND status IN ('waiting','running')", (eid,)):
        raise HTTPException(409, "this episode is processing: cancel it before changing its files")
    name = storage.safe_name(body.get("filename") or kind)
    size = int(body.get("size") or 0)
    local = Path(storage.cache_path(ep, kind, name))
    local.parent.mkdir(parents=True, exist_ok=True)
    local.unlink(missing_ok=True)                                  # a new attempt starts from the beginning
    mode = storage.upload_mode() if storage.configured() else "local"
    if mode != "direct" and size and size < storage.MULTIPART_FROM:
        mode = "single"                                            # small file: one request through the API
    out = {"key": None, "upload_id": None, "name": name, "part_size": storage.PART_BIG,
           "parts": max(1, -(-size // storage.PART_BIG)), "mode": mode}
    if storage.configured() and mode != "single":
        out["key"] = storage.input_key(show, ep, kind, name)
        out["upload_id"] = storage.multipart_start(out["key"], storage.content_type(name))
    return out


@app.put("/api/episodes/{eid}/files/{kind}/part")
async def upload_part(request: Request, eid: int, kind: str, part: int, key: str = "", upload_id: str = "",
                      name: str = ""):
    """One part of a big upload: it is appended to the file on this machine and sent on to R2."""
    if kind not in UPLOAD_KINDS:
        raise HTTPException(400, f"unknown file kind {kind}")
    ep, _ = _ep_show(eid)
    data = await request.body()
    local = Path(storage.cache_path(ep, kind, storage.safe_name(name or key or kind)))
    local.parent.mkdir(parents=True, exist_ok=True)
    with local.open("ab" if part > 1 else "wb") as f:
        f.write(data)
    etag = None
    if key and upload_id:
        etag = storage.multipart_put(key, upload_id, part, data)
    bus.publish("upload", eid, store=False, file_kind=kind, status="sending", part=part, received=local.stat().st_size)
    return {"part": part, "etag": etag, "received": local.stat().st_size}


@app.post("/api/episodes/{eid}/files/{kind}/part-url")
def upload_part_url(eid: int, kind: str, body: dict = Body(...)):
    """Direct mode: a signed link for one part, so the browser sends the bytes to R2 itself (the API never carries
    a 3 GB video). The browser reads the part's ETag from the answer — the bucket's CORS rule exposes it."""
    if kind not in UPLOAD_KINDS:
        raise HTTPException(400, f"unknown file kind {kind}")
    _ep_show(eid)
    if not (body.get("key") and body.get("upload_id") and body.get("part")):
        raise HTTPException(400, "key, upload_id and part are needed")
    return {"url": storage.multipart_url(body["key"], body["upload_id"], int(body["part"]), seconds=3600)}


@app.post("/api/episodes/{eid}/files/{kind}/finish")
def upload_finish(eid: int, kind: str, body: dict = Body(...)):
    """All parts are in: close the upload in R2 and make the file this episode's input."""
    if kind not in UPLOAD_KINDS:
        raise HTTPException(400, f"unknown file kind {kind}")
    ep, _ = _ep_show(eid)
    name = storage.safe_name(body.get("name") or kind)
    local = Path(storage.cache_path(ep, kind, name))
    key, upload_id = body.get("key"), body.get("upload_id")
    if key and upload_id:
        etags = [(int(n), e) for n, e in (body.get("etags") or [])]
        if not etags:
            raise HTTPException(400, "no parts were sent")
        storage.multipart_finish(key, upload_id, etags)
        head = storage.head(key)
        if not head:
            raise HTTPException(502, "the file did not arrive in the cloud")
        size = head["size"]
    else:
        size = local.stat().st_size if local.exists() else 0
    return _register_asset(eid, kind, name, local, key, size)


@app.post("/api/episodes/{eid}/files/{kind}/abort")
def upload_abort(eid: int, kind: str, body: dict = Body(...)):
    if body.get("key") and body.get("upload_id") and storage.configured():
        storage.multipart_abort(body["key"], body["upload_id"])
    return {"aborted": True}


@app.post("/api/episodes/{eid}/upload-url")
def upload_url(eid: int, body: dict = Body(...)):
    """Deployed version: a signed link so the browser uploads straight to R2 (no bytes through the API).
    Needs a CORS rule on the bucket (docs/09 §3); `confirm` below registers the file afterwards."""
    if not storage.configured():
        raise HTTPException(409, "cloud storage is not set up")
    kind = body.get("kind")
    if kind not in UPLOAD_KINDS:
        raise HTTPException(400, f"unknown file kind {kind}")
    ep = _get("episodes", eid); show = _get("shows", ep["show_id"])
    name = storage.safe_name(body.get("filename") or kind)
    key = storage.input_key(show, ep, kind, name)
    return {"mode": storage.upload_mode(), "key": key, "name": name,
            "url": storage.presign(key, seconds=3600, method="PUT"),
            "headers": {"Content-Type": body.get("content_type") or storage.content_type(name)}}


@app.post("/api/episodes/{eid}/files/{kind}/confirm")
def upload_confirm(eid: int, kind: str, body: dict = Body(...)):
    """After a direct upload: check the object is really there, then make it the episode's file."""
    if kind not in UPLOAD_KINDS:
        raise HTTPException(400, f"unknown file kind {kind}")
    ep = _get("episodes", eid); _get("shows", ep["show_id"])
    key = body.get("key") or ""
    head = storage.head(key)
    if not head:
        raise HTTPException(409, "the file did not arrive in the cloud")
    name = Path(key).name
    db().ex("DELETE FROM assets WHERE episode_id=? AND kind=? AND role='input'", (eid, kind))
    db().insert("assets", episode_id=eid, kind=kind, path=storage.cache_path(ep, kind, name), role="input",
                size=head["size"], r2_key=key, origin="r2", uploaded=now(), created=now())
    return {"kind": kind, "name": name, "size": head["size"], "key": key, "in_cloud": True}


@app.delete("/api/episodes/{eid}/files/{kind}")
def delete_file(eid: int, kind: str):
    ep = _get("episodes", eid)
    for a in db().q("SELECT * FROM assets WHERE episode_id=? AND kind=? AND role='input'", (eid, kind)):
        if a.get("r2_key"):
            try:
                storage.delete(a["r2_key"])
            except Exception:
                pass
        if a.get("origin") == "r2" and Path(a["path"]).exists():
            Path(a["path"]).unlink(missing_ok=True)
        db().ex("DELETE FROM assets WHERE id=?", (a["id"],))
    return {"deleted": kind}


@app.post("/api/episodes/{eid}/check")
def check_files(eid: int):
    out = {}
    for a in db().q("SELECT * FROM assets WHERE episode_id=? AND role='input'", (eid,)):
        info, probs = scan.check_asset(a["kind"], a["path"])
        if info:
            db().update("assets", a["id"], size=info.get("size"), fingerprint=info.get("fingerprint"),
                        mtime=info.get("mtime"), probe=info.get("probe"))
        out[a["kind"]] = {"info": info.get("probe"), "problems": probs}
    return out


@app.delete("/api/episodes/{eid}")
def delete_episode(eid: int):
    runner().cancel_run(eid)
    db().ex("DELETE FROM episodes WHERE id=?", (eid,))
    return {"deleted": eid}


# ---------------- processing and the queue ----------------
@app.post("/api/episodes/{eid}/process")
def process(eid: int):
    """Put the episode in the line. It starts at once when the machine is free, else it waits its turn
    (one episode at a time by default -- MAX_ACTIVE_EPISODES)."""
    _get("episodes", eid)
    try:
        out = runner().submit(eid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**_episode_full(eid), **out}          # `queued` here means "waiting its turn", not the episode's column


@app.get("/api/queue")
def queue():
    """The line of episodes: what is being worked on now, and who is next."""
    return runner().queue_view(db())


@app.post("/api/episodes/{eid}/cancel")
def cancel_episode(eid: int):
    ep = _get("episodes", eid)
    if ep["status"] == "queued":                              # it had not started: it simply leaves the line
        db().update("episodes", eid, status="cancelled" if runner().latest_run(eid) else "new", queued=None,
                    updated=now())
        bus.publish("episode_status", eid, status="left the queue")
    else:
        runner().cancel_run(eid)
        db().update("episodes", eid, queued=None)
    runner().start_next()
    return _episode_full(eid)


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: int):
    runner().cancel(jid); return {"ok": True}


@app.post("/api/jobs/{jid}/retry")
def retry_job(jid: int):
    runner().retry(jid); return {"ok": True}


@app.get("/api/jobs/{jid}/log")
def job_log(jid: int, tail: int = 200):
    j = _get("jobs", jid)
    return {"lines": runner().log_tail(j["log_path"], tail)}


# ---------------- matching decisions (milestone 1: import or accept automatic) ----------------
@app.get("/api/episodes/{eid}/decisions")
def decisions_state(eid: int):
    e = _get("episodes", eid); w = Path(e["work_dir"])
    if not (w / "prep.json").exists():
        return {"ready": False, "missing": None}
    p, unmatched, case_a = dec.flagged_rows(w)
    miss = dec.missing(w)
    rows = [{"idx": i, "text": p["dub"][i]["text"], "kind": "unmatched" if i in unmatched else "Case A",
             "suggested": (p["src"][p["auto"][str(i)]["src"]]["text"] if str(i) in p["auto"] else None),
             "suggested_src": p["auto"].get(str(i), {}).get("src"), "score": p["auto"].get(str(i), {}).get("score"),
             "start_ms": p["dub"][i].get("start_ms"), "end_ms": p["dub"][i].get("end_ms")}
            for i in sorted(unmatched + case_a)]
    ai_file = w / "ai_proposals.json"
    ai_last = json.loads(ai_file.read_text()) if ai_file.exists() else None
    if ai_last and (w / "spec_ai.py").exists():
        ai_last["spec"] = (w / "spec_ai.py").read_text()
    auto = tlm._load(w / "ai_auto.json")
    if auto:
        auto.pop("spec", None)
    return {"ready": True, "flagged": rows, "missing": miss, "decided": (w / "spec.py").exists() or miss == [],
            "ai_last": ai_last, "ai_auto": auto}


@app.post("/api/episodes/{eid}/decisions/auto")
def decisions_auto(eid: int):
    e = _get("episodes", eid)
    try:
        r = dec.accept_auto(e["work_dir"])
    except FileNotFoundError:
        raise HTTPException(409, "the automatic matching has not run yet")
    runner().poke(); return r


@app.get("/api/ai/status")
def ai_status():
    from . import ai
    s = ai.settings()
    out = {"configured": ai.available(), "model": s["AI_MODEL"], "model_fast": s["AI_MODEL_FAST"],
           "provider": s["AI_PROVIDER"]}
    if out["configured"]:
        try:
            out.update(ai.key_info())
        except Exception as ex:
            out["error"] = str(ex)[:200]
    return out


@app.post("/api/episodes/{eid}/decisions/ai")
def decisions_ai(eid: int, body: dict = Body(default={})):
    """Step 2 by Claude through the AI gateway. Returns proposals (row, action, src, reason, confidence) and writes
    W/spec_ai.py + W/ai_proposals.json. With {"apply": true} the proposals are imported straight away."""
    from . import ai
    from .pipeline import ai_match
    e = _get("episodes", eid); w = Path(e["work_dir"])
    if not ai.available():
        raise HTTPException(409, "AI gateway not configured (app/.env: AI_BASE, AI_KEY, AI_EMAIL)")
    if not (w / "prep.json").exists():
        raise HTTPException(409, "the automatic matching has not run yet")
    log = lambda m: bus.publish("ai_log", episode_id=eid, store=False, line=m)
    s = _get("shows", e["show_id"])
    try:
        r = ai_match.propose(w, body.get("model"), log=log, names_csv=names_sheet_for(eid))
    except Exception as ex:
        raise HTTPException(502, f"AI matching failed: {ex}")
    if r["proposals"]:
        log("checking the proposals with the pipeline (shared footage → groups)…")
        r = ai_match.apply_group_rule(w, r, lambda spec: dec.check_spec(w, spec, cfg.pipeline_python, cfg.scripts,
                                                                         s["profile"]["labels"]), log=log)
    if body.get("apply"):
        try:
            r["applied"] = dec.from_spec(w, r["spec"])
        except Exception as ex:
            raise HTTPException(400, f"AI proposals did not pass the check: {ex}")
        runner().poke()
    return r


@app.post("/api/episodes/{eid}/decisions/rematch")
def decisions_rematch(eid: int):
    """Match again with the AI: the decisions are cleared and the decisions step runs again (Claude decides by
    itself), then sheet -> edit list -> groups -> review. Editor versions of the old edit list stay in the history."""
    from . import ai
    e = _get("episodes", eid); w = Path(e["work_dir"])
    if not ai.available():
        raise HTTPException(409, "AI gateway not configured (Settings)")
    if not (w / "prep.json").exists():
        raise HTTPException(409, "the automatic matching has not run yet")
    for f in ("ai_auto.json", "spec.py"):
        (w / f).unlink(missing_ok=True)
    (w / "decisions.json").write_text(json.dumps({"groups": [], "assign": {}, "confirm": {}, "repeat": {}, "gap": {},
                                                  "unused_notes": {}}))
    try:
        steps = runner().rerun_from(eid, "decisions")
    except ValueError as ex:
        raise HTTPException(409, str(ex))
    return {"steps": steps}


@app.post("/api/episodes/{eid}/decisions/check")
def decisions_check(eid: int, body: dict = Body(...)):
    """Run the pipeline's integrity check on a spec without saving it: {"spec": text}."""
    e = _get("episodes", eid); w = Path(e["work_dir"]); s = _get("shows", e["show_id"])
    if not (w / "prep.json").exists():
        raise HTTPException(409, "the automatic matching has not run yet")
    return dec.check_spec(w, body.get("spec") or "", cfg.pipeline_python, cfg.scripts, s["profile"]["labels"])


@app.post("/api/episodes/{eid}/decisions/join")
def decisions_join(eid: int, body: dict = Body(...)):
    """Neighbouring rows that share source footage become groups: {"spec": text} -> new spec + what was joined,
    checked again with the pipeline's integrity check."""
    e = _get("episodes", eid); w = Path(e["work_dir"]); s = _get("shows", e["show_id"])
    spec = body.get("spec") or ""
    r = dec.check_spec(w, spec, cfg.pipeline_python, cfg.scripts, s["profile"]["labels"])
    if not r["overlaps"]:
        return {"spec": spec, "joined": [], "left": [], "check": r}
    new, joined, left = dec.join_shared(w, spec, r["overlaps"])
    return {"spec": new, "joined": joined, "left": left,
            "check": dec.check_spec(w, new, cfg.pipeline_python, cfg.scripts, s["profile"]["labels"])}


@app.post("/api/episodes/{eid}/decisions/import")
async def decisions_import(eid: int, file: UploadFile = File(...)):
    e = _get("episodes", eid); text = (await file.read()).decode("utf-8")
    w = Path(e["work_dir"])
    try:
        if file.filename.endswith(".json"):
            json.loads(text); (w / "decisions.json").write_text(text); r = {"imported": "decisions.json"}
        else:
            r = dec.from_spec(w, text)
    except Exception as ex:
        raise HTTPException(400, f"could not import: {ex}")
    runner().poke(); return r


# ---------------- editor: the timeline before render ----------------
def _ep_ctx(eid):
    show, ep, assets = runner().episode_context(eid)
    # the editor is built out of the small work files (the edit list, rows, lip-sync, sentences, SRTs). On a machine
    # that never processed this episode -- a deployed box, or this Mac after `cleanup` -- they come down from R2
    # first, once. Without this the editor answers "the edit list is not made yet" for a finished episode.
    if not Path(ep["work_dir"], "manifest_groups.json").exists():
        storage.fetch_work_files(show, ep)
    ctx, _ = pipeline_steps.plan(show, ep, assets)
    return show, ep, assets, ctx


def _src_duration_ms(eid):
    a = db().one("SELECT probe FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'", (eid,))
    d = (a or {}).get("probe") or {}
    return round(d["duration"] * 1000) if isinstance(d, dict) and d.get("duration") else None


def _versions(eid):
    """The saved timeline versions, newest first (the `base` of each one comes out of its JSON: the same code works
    on SQLite and on Postgres)."""
    rows = db().q("SELECT version, made_by, note, created, body FROM timeline_versions WHERE episode_id=? "
                  "ORDER BY version DESC", (eid,))
    return [{**{k: v for k, v in r.items() if k != "body"}, "base": (r["body"] or {}).get("base")} for r in rows]


def _current_timeline(eid, work):
    v1 = tlm.build_v1(work)
    if v1 is None:
        raise HTTPException(409, "the edit list is not made yet: the editor opens after the Groups step")
    rows = db().q("SELECT * FROM timeline_versions WHERE episode_id=? ORDER BY version DESC", (eid,))
    row = next((r for r in rows if (r["body"] or {}).get("base") == v1["base"]), None)
    tl = row["body"] if row else v1
    tl["holes"] = v1.get("holes") or []
    if row:
        tl["version"] = row["version"]
    draft = tlm._load(Path(work) / "timeline_draft.json")
    if draft and draft.get("base") == v1["base"] and draft.get("saved", 0) > (row["created"] if row else 0):
        draft["draft"] = True; draft.setdefault("version", tl.get("version", 0)); tl = draft
    return tl


def _review_state(eid):
    run = runner().latest_run(eid)
    jobs = {j["step"]: j for j in db().q("SELECT step, status, progress FROM jobs WHERE episode_id=? AND run=?", (eid, run))} if run else {}
    return {k: jobs[k]["status"] for k in ("groups", "review", "render", "check") if k in jobs}


def _markers(eid):
    return db().q("SELECT id, t_ms, text, source, resolved, created FROM markers WHERE episode_id=? ORDER BY t_ms, id", (eid,))


def _check(eid, body, work):
    tl = {"clips": body["clips"], "v2": body.get("v2") or [], "tracks": body.get("tracks") or {},
          "approved": body.get("approved") or [], "total_ms": body["total_ms"], "holes": tlm.holes(work)}
    show = _get("shows", _get("episodes", eid)["show_id"])
    media_ms = {x["id"]: x["duration_ms"] for x in imports.items(work)}
    tlm.fit_episode(tl, _src_duration_ms(eid), media_ms)
    fl = tlm.flags(tl, tlm.context(work), (show["profile"] or {}).get("flags"), _markers(eid))
    return tl, fl, tlm.open_counts(fl), tlm.validate(tl, _src_duration_ms(eid), media_ms)


@app.get("/api/episodes/{eid}/timeline")
def get_timeline(eid: int, pipeline: bool = False):
    """The timeline to edit: the latest saved version (or the autosaved draft); pipeline=1 gives the pipeline's own."""
    show, ep, assets, ctx = _ep_ctx(eid); work = ep["work_dir"]
    tl = tlm.build_v1(work) if pipeline else _current_timeline(eid, work)
    if tl is None:
        raise HTTPException(409, "the edit list is not made yet: the editor opens after the Groups step")
    _, fl, counts, probs = _check(eid, tl, work)
    c = tlm.context(work)
    return {
        "timeline": tl, "flags": fl, "counts": counts, "problems": probs,
        "versions": [v for v in _versions(eid) if v["base"] == tl["base"]],
        "src_sentences": c["src_sentences"], "dub_starts": c["dub_starts"], "lip_rows": sorted(c["lip_rows"]),
        **_subs(eid, ctx), "markers": _markers(eid), "proxy": tlm.proxy_state(work),
        "labels": (show["profile"].get("labels") or "DUB,SRC").split(","),
        "sub_style": show["profile"].get("sub_style", "box"), "sub_styles": show["profile"].get("sub_styles") or {},
        "transitions": _transitions_info(show), "cuts": tlm.cuts(tl),
        "src_duration_ms": _src_duration_ms(eid), "state": _review_state(eid),
        "show": {"id": show["id"], "code": show["code"]}, "episode": {"id": ep["id"], "number": ep["number"], "status": ep["status"]},
    }


def _transitions_info(show):
    """The styles for the editor's menu, the show's most used first, and what a cut gets when nothing is chosen."""
    eps = db().q("SELECT id, work_dir FROM episodes WHERE show_id=?", (show["id"],))
    bodies = [r["body"] for e in eps for r in db().q("SELECT body FROM timeline_versions WHERE episode_id=? "
                                                     "ORDER BY version DESC LIMIT 1", (e["id"],))]
    cfgs = [tlm._load(Path(e["work_dir"]) / "config.json") for e in eps if e["work_dir"]]
    use = tlm.transition_use(bodies, cfgs)
    order = sorted(tlm.TRANSITIONS, key=lambda k: (-use[k], list(tlm.TRANSITIONS).index(k)))
    return {"default": "auto" if show["profile"].get("transitions") else "none",
            "styles": [{"id": k, "label": tlm.TRANSITIONS[k][0], "half_s": tlm.TRANSITIONS[k][1], "uses": use[k]} for k in order]}


@app.post("/api/episodes/{eid}/timeline/check")
def check_timeline(eid: int, body: dict = Body(...)):
    work = _get("episodes", eid)["work_dir"]
    _, fl, counts, probs = _check(eid, body, work)
    return {"flags": fl, "counts": counts, "problems": probs}


@app.put("/api/episodes/{eid}/timeline")
def save_timeline(eid: int, body: dict = Body(...)):
    """draft=true: autosave (one file, overwritten). Otherwise a new numbered version."""
    work = _get("episodes", eid)["work_dir"]
    v1 = tlm.build_v1(work)
    if v1 is None:
        raise HTTPException(409, "the edit list is not made yet")
    tl, fl, counts, probs = _check(eid, body, work)
    tl["base"] = v1["base"]
    if body.get("draft"):
        tl["saved"] = time.time(); tl["version"] = body.get("version", 0)
        (Path(work) / "timeline_draft.json").write_text(json.dumps(tl, ensure_ascii=False))
        return {"draft": True, "counts": counts, "problems": probs}
    if probs:
        raise HTTPException(400, "; ".join(probs[:5]))
    return {"version": _new_version(eid, tl, body.get("made_by") or "editor", body.get("note")), "counts": counts}


def _new_version(eid, tl, made_by, note=None):
    last = db().one("SELECT MAX(version) AS v FROM timeline_versions WHERE episode_id=?", (eid,))["v"] or 0
    body = {k: tl.get(k) for k in ("base", "total_ms", "clips", "approved")}
    body["v2"] = tl.get("v2") or []; body["tracks"] = tl.get("tracks") or {}
    db().insert("timeline_versions", episode_id=eid, version=last + 1, parent=tl.get("version"), made_by=made_by,
                note=note, body=body, created=now())
    db().update("episodes", eid, current_version=last + 1, updated=now())
    return last + 1


@app.get("/api/episodes/{eid}/timeline/auto")
def timeline_auto(eid: int):
    """The automatic edit (before the scene edit), read-only: the editor's 'Automatic edit' row."""
    clips = tlm.auto_edit(_get("episodes", eid)["work_dir"])
    if clips is None:
        raise HTTPException(404, "no automatic edit kept (the scene edit has not run for this episode)")
    return {"clips": clips}


@app.post("/api/episodes/{eid}/scene/redo")
def scene_redo(eid: int):
    """Redo the scene edit with fresh Claude answers: the cached answers move to W/scene/_old/<time>/ (nothing is
    deleted), then scene_edit and every step after it run again. A plain Retry reuses the cached answers ($0)."""
    w = Path(_get("episodes", eid)["work_dir"]); cache = w / "scene" / "scene_manifest.par_cache.json"
    if cache.exists():
        old = w / "scene" / "_old" / time.strftime("%Y%m%d_%H%M%S"); old.mkdir(parents=True, exist_ok=True)
        cache.rename(old / cache.name)
    try:
        steps = runner().rerun_from(eid, "scene_edit")
    except ValueError as ex:
        raise HTTPException(409, str(ex))
    return {"steps": steps}


@app.get("/api/episodes/{eid}/timeline/versions/{v}")
def get_version(eid: int, v: int):
    row = db().one("SELECT * FROM timeline_versions WHERE episode_id=? AND version=?", (eid, v))
    if not row:
        raise HTTPException(404, "no such version")
    tl = row["body"]; tl["version"] = v
    return tl


def _kick_render(eid, body, note=None, force=False):
    """Write this timeline as the edit list and run render → final check. Same path the editor's Render uses."""
    e = _get("episodes", eid); work = e["work_dir"]
    v1 = tlm.build_v1(work)
    if v1 is None:
        raise HTTPException(409, "the edit list is not made yet")
    tl, fl, counts, probs = _check(eid, body, work)
    if probs:
        raise HTTPException(400, "the timeline cannot be rendered: " + "; ".join(probs[:5]))
    if counts["red"] and not force:
        raise HTTPException(409, f"{counts['red']} red flags are open: fix or approve them first")
    st = _review_state(eid)
    if any(st.get(k) == "running" for k in ("render", "check")):
        raise HTTPException(409, "a render is running now")
    tl["base"] = v1["base"]
    v = _new_version(eid, tl, "render", note or "rendered")
    tlm.write_manifest(work, tl, v)
    (Path(work) / "timeline_draft.json").unlink(missing_ok=True)
    _, _, _, ctx = _ep_ctx(eid)
    _deliver_en_srt(work, ctx)
    show_ = _get("shows", e["show_id"])
    tlm.write_transition_plan(work, tl, "auto" if show_["profile"].get("transitions") else "none")
    runner().ensure_steps(eid)
    rid = deliver.start_render(db(), eid, v, note)
    try:
        _write_premiere(eid, tl, v)
    except Exception as ex:                                   # the XML is a convenience: never block a render on it
        print("premiere xml:", ex)
    tlm.request_render(work, v)
    runner().refresh_pending(eid)
    try:
        if "review" in st and st["review"] in ("needs_input", "waiting", "running"):
            steps = ["review"]; runner().poke()
        elif "review" in st:
            steps = runner().rerun_from(eid, "review")
        else:                                           # a run made before the editor existed
            Path(work, tlm.REVIEW_OK).unlink(missing_ok=True)
            steps = runner().rerun_from(eid, "render")
    except ValueError as ex:
        raise HTTPException(409, str(ex))
    return {"version": v, "steps": steps, "render": rid}


def _render_as_is(eid, note=None, force=False):
    """Render the current timeline without opening the editor. Only an episode waiting in review.
    Flags never block this path (urgent first upload); only a gap / broken timeline does.
    An editor can still open the cut later, fix flags, and render again."""
    e = _get("episodes", eid)
    if e["status"] != "review":
        raise HTTPException(409, "only an episode waiting in review can be rendered from the list")
    _ep_ctx(eid)
    tl = _current_timeline(eid, e["work_dir"])
    return _kick_render(eid, tl, note or "rendered from the list", True)


@app.post("/api/episodes/{eid}/timeline/render")
def render_timeline(eid: int, body: dict = Body(None)):
    """Save the editor's timeline as a version, write it as manifest_groups.json, and run render → final check.
    No clips in the body: use the current timeline as it is (the list's Render button)."""
    body = body or {}
    if "clips" not in body:
        return _render_as_is(eid, body.get("note"), bool(body.get("force")))
    return _kick_render(eid, body, body.get("note") or "rendered", bool(body.get("force")))


# ---------------- milestone 6: render queue, deliverables, Premiere XML ----------------
def _render_row(r):
    r = deliver.render_state(db(), runner(), r)
    e = db().one("SELECT e.number, e.status AS ep_status, s.code, s.id AS show_id FROM episodes e JOIN shows s ON s.id=e.show_id "
                 "WHERE e.id=?", (r["episode_id"],))
    return {**r, **(e or {})}


@app.get("/api/busy")
def busy():
    """Steps running now (any episode): the editor shows "processing is running, preview may lag" while the GPU or
    the processors are busy (a lip scan, Whisper, a render). Every account sees the same Mac is busy."""
    rows = db().q("SELECT j.step, j.resource, e.number, s.code FROM jobs j JOIN episodes e ON e.id=j.episode_id "
                  "JOIN shows s ON s.id=e.show_id WHERE j.status='running' AND j.resource IN ('cpu','gpu')")
    return {"running": [{"step": r["step"], "episode": f"{r['code']} ep{r['number']}", "resource": r["resource"]} for r in rows]}


@app.get("/api/renders")
def list_renders(request: Request, limit: int = 60):
    where, args = _own_filter(request, "s.owner_id")
    rows = db().q(f"SELECT r.* FROM renders r JOIN episodes e ON e.id=r.episode_id JOIN shows s ON s.id=e.show_id "
                  f"WHERE 1=1{where} ORDER BY r.id DESC LIMIT ?", args + [limit])
    return [_render_row(r) for r in rows]


def _rendered_version(work):
    m = tlm._load(Path(work) / "manifest_groups.json") or {}
    return (m.get("dub_studio") or {}).get("version")


@app.get("/api/episodes/{eid}/deliverables")
def deliverables(eid: int):
    show, ep, assets, ctx = _ep_ctx(eid); work = Path(ep["work_dir"])
    files = deliver.deliverables(show, ep, ctx)
    last = db().one("SELECT * FROM renders WHERE episode_id=? ORDER BY id DESC LIMIT 1", (eid,))
    latest_v = (db().one("SELECT MAX(version) AS v FROM timeline_versions WHERE episode_id=?", (eid,)) or {}).get("v")
    draft = tlm._load(work / "timeline_draft.json")
    rendered = _rendered_version(work)
    clean = next((f for f in files if f["kind"] == "clean" and f["exists"]), None)
    return {"files": files, "output_dir": ep["output_dir"], "render": _render_row(last) if last else None,
            "rendered_version": rendered, "latest_version": latest_v,
            "unrendered": bool((latest_v and rendered and latest_v > rendered) or
                               (draft and clean and draft.get("saved", 0) > clean["mtime"])),
            "final_check_image": (work / "final_check.jpg").exists(),
            "qc_open": (db().one("SELECT COUNT(*) AS n FROM markers WHERE episode_id=? AND resolved=0", (eid,)) or {}).get("n", 0)}


@app.get("/api/episodes/{eid}/deliverables.zip")
def deliverables_zip(eid: int):
    """Every finished file of this episode, in one folder, as a zip."""
    ep = _get("episodes", eid)
    show = _get("shows", ep["show_id"])
    files = deliver.deliverables(show, ep, None)
    folder = f"{show['code']}_ep{ep['number']}"
    fd, name = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        n = deliver.write_zip(files, name, folder)
    except Exception:
        os.unlink(name)
        raise
    if not n:
        os.unlink(name)
        raise HTTPException(409, "nothing to download yet")
    return FileResponse(name, media_type="application/zip", filename=f"{folder}.zip",
                        background=BackgroundTask(os.unlink, name))


@app.get("/api/episodes/{eid}/final-check.jpg")
def final_check_image(eid: int):
    e = _get("episodes", eid)
    show = _get("shows", e["show_id"])
    p = Path(e["work_dir"]) / "final_check.jpg"
    if not p.exists():
        p = storage.ensure_local(show, e, "final_check.jpg")
    if not p:
        raise HTTPException(404, "no final check image yet")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


def _write_premiere(eid, tl, version):
    show, ep, assets, ctx = _ep_ctx(eid)
    src = db().one("SELECT path, probe FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'", (eid,))
    dub = db().one("SELECT path, probe FROM assets WHERE episode_id=? AND kind='dub_audio' AND role='input'", (eid,))
    if not src or not dub:
        raise HTTPException(409, "the episode has no source video or dub audio")
    names = presets.names(show["code"], ep["number"], show["profile"])
    prefix = names["clean"].replace("_final_clean.mp4", "")
    xml = deliver.premiere_xml(tl, f"{prefix} v{version}" if version else prefix, src["path"], dub["path"], src["probe"],
                               (dub["probe"] or {}).get("duration") if isinstance(dub["probe"], dict) else None,
                               _markers(eid), (show["profile"].get("labels") or "IT,US").split(","),
                               {x["id"]: dict(x, path=imports.path_of(ep["work_dir"], x["id"])) for x in imports.items(ep["work_dir"])})
    out = Path(ep["output_dir"]) / f"{prefix}_timeline_v{version or 0}.xml"
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text(xml, encoding="utf-8")
    return out


@app.get("/api/episodes/{eid}/export/premiere")
def export_premiere(eid: int, version: int = 0):
    """The timeline (a saved version, or the current one) as Premiere-readable XML; also saved in the output folder."""
    work = _get("episodes", eid)["work_dir"]
    if version:
        row = db().one("SELECT body FROM timeline_versions WHERE episode_id=? AND version=?", (eid, version))
        if not row:
            raise HTTPException(404, "no such version")
        tl = row["body"]
    else:
        tl = _current_timeline(eid, work); version = tl.get("version") or 0
    p = _write_premiere(eid, tl, version)
    return FileResponse(p, media_type="application/xml", filename=p.name)


# ---------------- editor: subtitles (S1 dub, S2 English) ----------------
SUB_LANGS = ("dub", "en")


def _subs(eid, ctx):
    """Each track as the editor sees it: the edited cues if any, else the SRT the pipeline uses."""
    out, edited = {}, {}
    for lang in SUB_LANGS:
        row = db().one("SELECT cues FROM subtitle_tracks WHERE episode_id=? AND language=?", (eid, lang))
        edited[lang] = bool(row)
        out[lang] = row["cues"] if row else tlm.parse_srt(ctx.get("sub_srt_orig" if lang == "dub" else "name_en_srt") or "")
    return {"subs": out, "subs_edited": edited}


@app.put("/api/episodes/{eid}/subs/{lang}")
def save_subs(eid: int, lang: str, body: dict = Body(...)):
    """{"cues": [{t0, t1, text}]} -> saved for the episode and written as W/subs_edited_<lang>.srt, which the
    subtitle steps burn at the next render (the show folder's SRT is never changed)."""
    if lang not in SUB_LANGS:
        raise HTTPException(404, "unknown subtitle track")
    e = _get("episodes", eid); v1 = tlm.build_v1(e["work_dir"])
    cues, probs = tlm.check_cues(body.get("cues"), v1["total_ms"] if v1 else None)
    if probs:
        raise HTTPException(400, "; ".join(probs[:5]))
    if not cues:
        raise HTTPException(400, "a subtitle track needs at least one cue")
    db().ex("INSERT INTO subtitle_tracks (episode_id, language, kind, cues, updated) VALUES (?,?,?,?,?) "
            "ON CONFLICT(episode_id, language) DO UPDATE SET cues=excluded.cues, updated=excluded.updated",
            (eid, lang, "dub" if lang == "dub" else "translation", cues, now()))
    f = tlm.edited_srt(e["work_dir"], lang); f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(tlm.srt_text(cues), encoding="utf-8")
    return {"saved": len(cues), "file": str(f)}


@app.delete("/api/episodes/{eid}/subs/{lang}")
def revert_subs(eid: int, lang: str):
    """Back to the pipeline's SRT for this track."""
    if lang not in SUB_LANGS:
        raise HTTPException(404, "unknown subtitle track")
    e = _get("episodes", eid)
    db().ex("DELETE FROM subtitle_tracks WHERE episode_id=? AND language=?", (eid, lang))
    tlm.edited_srt(e["work_dir"], lang).unlink(missing_ok=True)
    _, _, _, ctx = _ep_ctx(eid)
    if lang == "en":
        _deliver_en_srt(e["work_dir"], ctx)
    return _subs(eid, ctx)


def _deliver_en_srt(work, ctx):
    """The delivered English SRT (output folder) follows the edited S2 track; the translation is kept in W once."""
    out = Path(ctx["name_en_srt"]); keep = Path(work) / "english_translation_original.srt"
    ed = tlm.edited_srt(work, "en")
    if ed.exists():
        if out.exists() and not keep.exists():
            shutil.copyfile(out, keep)
        out.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ed, out)
    elif keep.exists():
        shutil.copyfile(keep, out); keep.unlink()


@app.post("/api/episodes/{eid}/translate-line")
def translate_line(eid: int, body: dict = Body(...)):
    """Re-translate one subtitle line with the AI: {"text": dub line, "before": "", "after": ""} -> {"text": ...}."""
    from . import ai
    from .pipeline import translate_ai
    e = _get("episodes", eid); prof = _get("shows", e["show_id"])["profile"]
    if not ai.available():
        raise HTTPException(409, "AI gateway not configured (app/.env)")
    if not (body.get("text") or "").strip():
        raise HTTPException(400, "no text to translate")
    try:
        return {"text": translate_ai.one(body["text"], prof.get("dub_lang", "it"), prof.get("translate_to", "en"),
                                         before=body.get("before") or "", after=body.get("after") or "")}
    except Exception as ex:
        raise HTTPException(502, f"translation failed: {ex}")


# ---------------- editor: QC markers ----------------
@app.get("/api/episodes/{eid}/markers")
def list_markers(eid: int):
    _get("episodes", eid); return _markers(eid)


@app.post("/api/episodes/{eid}/markers")
def add_marker(eid: int, body: dict = Body(...)):
    """{"text": "06:04 Viola smiling"} (time read from the text) or {"text": ..., "t_ms": ...}."""
    _get("episodes", eid)
    t, text = tlm.parse_marker(body.get("text") or "")
    if t is None:
        t = body.get("t_ms")
    if t is None:
        raise HTTPException(400, "give a time: type it first (06:04 Viola smiling) or put the playhead there")
    if not text:
        raise HTTPException(400, "the note is empty")
    mid = db().insert("markers", episode_id=eid, t_ms=round(float(t)), text=text, source=body.get("source") or "QC",
                      resolved=0, created=now())
    return db().one("SELECT * FROM markers WHERE id=?", (mid,))


@app.patch("/api/markers/{mid}")
def patch_marker(mid: int, body: dict = Body(...)):
    _get("markers", mid); vals = {}
    if "text" in body:
        vals["text"] = (body["text"] or "").strip()
    if "t_ms" in body:
        vals["t_ms"] = round(float(body["t_ms"]))
    if "resolved" in body:
        vals["resolved"] = 1 if body["resolved"] else 0
    db().update("markers", mid, **vals)
    return db().one("SELECT * FROM markers WHERE id=?", (mid,))


@app.delete("/api/markers/{mid}")
def delete_marker(mid: int):
    _get("markers", mid); db().ex("DELETE FROM markers WHERE id=?", (mid,))
    return {"deleted": mid}


# ---------------- editor: the 540-px preview copy ----------------
def _src_video(eid):
    a = db().one("SELECT path, probe FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'", (eid,))
    if not a:
        raise HTTPException(404, "no source video")
    return a["path"], (a["probe"] or {}).get("duration") if isinstance(a["probe"], dict) else None


@app.get("/api/episodes/{eid}/proxy")
def proxy_status(eid: int):
    return tlm.proxy_state(_get("episodes", eid)["work_dir"])


@app.post("/api/episodes/{eid}/proxy")
def proxy_make(eid: int):
    """Make the preview copy now (episodes processed before the proxy step existed); returns at once."""
    e = _get("episodes", eid); path, dur = _src_video(eid)
    Path(e["work_dir"]).mkdir(parents=True, exist_ok=True)
    tlm.make_proxy_bg(e["work_dir"], path, dur)
    # older episodes: the blur flag's measure too (from the preview copy once it exists)
    if not (Path(e["work_dir"]) / tlm.SHARPNESS).exists() and tlm.proxy_path(e["work_dir"]):
        tlm.make_sharpness_bg(e["work_dir"], tlm.proxy_path(e["work_dir"]), cfg.pipeline_python)
    return tlm.proxy_state(e["work_dir"])


PLAY = {"Cache-Control": "private, max-age=3600"}


def _serve_media(show, ep, rel, local, media_type):
    """Hand the editor one media file. On this Mac it is on disk, and a copy goes to R2 so another machine can serve
    it; on a deployed box there is no disk copy, so the browser is sent straight to R2 (which does the range
    requests -- scrubbing works and the API passes no bytes)."""
    if local and Path(local).exists():
        storage.mirror(show, ep, rel)
        return FileResponse(local, media_type=media_type, headers=PLAY)
    url = storage.media_link(show, ep, rel)
    if url:
        return RedirectResponse(url, status_code=307, headers=PLAY)
    return None


@app.get("/api/episodes/{eid}/media/{kind}")
def media(eid: int, kind: str):
    e = _get("episodes", eid)
    show = _get("shows", e["show_id"])
    if kind == "src_proxy":
        r = _serve_media(show, e, tlm.PROXY, tlm.proxy_path(e["work_dir"]), "video/mp4")
        if not r:
            raise HTTPException(404, "the preview copy is not made yet")
        return r
    if kind == "dub_preview":                     # what the editor plays (exact AAC copy; see timeline.dub_preview)
        a = db().one("SELECT path FROM assets WHERE episode_id=? AND kind='dub_audio' AND role='input'", (eid,))
        p = None
        if a and Path(a["path"]).exists():
            try:
                p = tlm.dub_preview(e["work_dir"], a["path"])
            except RuntimeError as ex:
                raise HTTPException(500, str(ex))
        r = _serve_media(show, e, tlm.DUB_PREVIEW, p, "audio/mp4")
        if not r:
            raise HTTPException(404, "no dub_audio")
        return r
    if kind not in ("src_video", "dub_audio"):
        raise HTTPException(404, "unknown media")
    a = db().one("SELECT * FROM assets WHERE episode_id=? AND kind=? AND role='input'", (eid, kind))
    if not a:
        raise HTTPException(404, f"no {kind}")
    if Path(a["path"]).exists():
        return FileResponse(a["path"], headers=PLAY)
    key = _input_in_cloud(show, e, a, kind)
    if key:                                                 # uploaded episode, or the scratch is already cleaned
        return RedirectResponse(storage.presign(key, 10800), status_code=307, headers=PLAY)
    raise HTTPException(404, f"no {kind}")


def _input_in_cloud(show, ep, asset, kind):
    """The R2 key of an input file, or None.

    Three ways, in order of trust: the key on the asset row; the key its file name would have; and failing both, the
    one object sitting in that kind's folder. The last one matters because an episode whose files came from a show
    folder on the Mac has no `r2_key`, and the copy in R2 may have been put there under a different name."""
    if not storage.configured():
        return None
    if asset.get("r2_key") and storage.head(asset["r2_key"]):
        return asset["r2_key"]
    key = storage.input_key(show, ep, kind, Path(asset["path"]).name)
    if storage.head(key):
        return key
    try:
        found = storage.listing(f"shows/{storage.safe_name(show['code'])}/ep{ep['number']}/input/{kind}",
                                limit=5)["objects"]
    except Exception:
        return None
    return found[0]["key"] if len(found) == 1 else None      # only when there is no doubt which file it is


# ---------------- the project bin: imported clips + the episode's files ----------------
@app.get("/api/episodes/{eid}/bin")
def bin_items(eid: int):
    """What the editor's project bin shows: clips imported for this episode, and the episode's own files
    (source video, the SRTs, the translated SRT)."""
    show, ep, assets, ctx = _ep_ctx(eid); work = ep["work_dir"]
    files = []

    def add(kind, label, path, cloud_key=None):
        """A file is listed when it is here, or when R2 has it (a deployed box, or after `cleanup`)."""
        if not path:
            return
        p = Path(path)
        if p.exists():
            files.append(dict(kind=kind, label=label, name=p.name, path=str(p), size=p.stat().st_size))
            return
        if cloud_key:
            head = storage.head(cloud_key) if storage.configured() else None
            if head:
                files.append(dict(kind=kind, label=label, name=p.name, path=str(p), size=head["size"],
                                  in_cloud=True))

    def work_key(path):
        return storage.work_key(show, ep, Path(path).name) if path and storage.configured() else None

    S = (show["profile"].get("labels") or "IT,US").split(",")
    for kind, label in (("src_video", f"Source video ({S[1]})"), ("dub_audio", f"Dub audio ({S[0]})")):
        path = assets.get(kind)
        a = db().one("SELECT * FROM assets WHERE episode_id=? AND kind=? AND role='input'", (eid, kind))
        add(kind, label, path, _input_in_cloud(show, ep, a, kind) if a else None)
    add("dub_srt", f"{S[0]} subtitles (SRT)", ctx.get("sub_srt"), work_key(ctx.get("sub_srt")))
    add("src_srt", f"{S[1]} subtitles (SRT)", ctx.get("src_srt"), work_key(ctx.get("src_srt")))
    add("en_srt", "Translated subtitles (EN SRT)", ctx.get("en_srt_burn"), work_key(ctx.get("en_srt_burn")))
    return {"imports": imports.items(work), "files": files}


@app.get("/api/episodes/{eid}/bin/file/{kind}")
def bin_file(eid: int, kind: str):
    f = next((x for x in bin_items(eid)["files"] if x["kind"] == kind), None)
    if not f:
        raise HTTPException(404, "no such file")
    if Path(f["path"]).exists():
        return FileResponse(f["path"], filename=f["name"])
    show, ep, assets, ctx = _ep_ctx(eid)
    a = db().one("SELECT * FROM assets WHERE episode_id=? AND kind=? AND role='input'", (eid, kind))
    key = _input_in_cloud(show, ep, a, kind) if a else storage.work_key(show, ep, f["name"])
    if key and storage.head(key):
        return RedirectResponse(storage.presign(key, 3600, download_name=f["name"]), status_code=307)
    raise HTTPException(404, "no such file")


@app.post("/api/episodes/{eid}/imports")
async def import_clip(eid: int, file: UploadFile = File(...)):
    """Import a video clip into this episode's bin (a copy in the work folder; the original is not touched)."""
    work = _get("episodes", eid)["work_dir"]
    def write(dest):
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out, 8 << 20)
    try:
        return await asyncio.to_thread(imports.add, work, imports.safe_name(file.filename), write)
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.post("/api/episodes/{eid}/imports/path")
def import_clip_path(eid: int, body: dict = Body(...)):
    """Import a clip by its path on this Mac (faster for big files: no upload)."""
    try:
        return imports.add_path(_get("episodes", eid)["work_dir"], (body.get("path") or "").strip())
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.get("/api/episodes/{eid}/imports/{iid}/media")
def import_media(eid: int, iid: str):
    p = imports.preview_of(_get("episodes", eid)["work_dir"], iid)
    if not p or not Path(p).exists():
        raise HTTPException(404, "no such imported clip")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})


@app.delete("/api/episodes/{eid}/imports/{iid}")
def delete_import(eid: int, iid: str):
    work = _get("episodes", eid)["work_dir"]
    try:
        tl = _current_timeline(eid, work)
    except HTTPException:
        tl = {}
    if imports.used_in(tl, iid):
        raise HTTPException(409, "this clip is on the timeline: take it off first")
    if not imports.remove(work, iid):
        raise HTTPException(404, "no such imported clip")
    return {"ok": True}


# ---------------- generate a clip with AI (gen.py) ----------------
@app.get("/api/gen/status")
def gen_status():
    return gen.status()


def _gen_thread(eid, tid):
    t = db().one("SELECT * FROM gen_threads WHERE id=? AND episode_id=?", (tid, eid))
    if not t:
        raise HTTPException(404, "no such chat")
    t["_dir"] = str(gen.folder(_get("episodes", eid)["work_dir"], tid))
    return t


def _gen_full(eid, tid):
    t = _gen_thread(eid, tid); t.pop("_dir", None)
    t["messages"] = db().q("SELECT * FROM gen_messages WHERE thread_id=? ORDER BY id", (tid,))
    t["jobs"] = db().q("SELECT * FROM gen_jobs WHERE thread_id=? ORDER BY id", (tid,))
    return t


def _clip_video(eid, work, clip):
    if clip.get("media"):
        p = imports.path_of(work, clip["media"])
        if p:
            return p
    a = db().one("SELECT path FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'", (eid,))
    if not a:
        raise HTTPException(409, "the episode has no source video")
    return a["path"]


def _gen_prepare(eid, tid):
    """frames -> Gemini analysis -> Claude's first prompt (in the background; the panel follows the status)."""
    e = _get("episodes", eid); work = e["work_dir"]
    try:
        t = db().one("SELECT * FROM gen_threads WHERE id=?", (tid,)); c = t["clip"]
        video = _clip_video(eid, work, c)
        db().update("gen_threads", tid, status="frames", updated=now())
        frames = gen.make_frames(work, tid, video, c["s0"], c["s1"])
        db().update("gen_threads", tid, frames=frames, status="analysing", updated=now())
        analysis = gen.analyse(work, tid, video, c["s0"], c["s1"], frames)
        db().update("gen_threads", tid, analysis=analysis, status="writing", updated=now())
        ask = gen.FIRST_ASK.format(slot=(c["t1"] - c["t0"]) / 1000)
        db().ex("DELETE FROM gen_messages WHERE thread_id=?", (tid,))       # a retry starts the chat again
        db().insert("gen_messages", thread_id=tid, role="user", text=ask, images=[], created=now())
        t = _gen_thread(eid, tid)
        msgs = db().q("SELECT * FROM gen_messages WHERE thread_id=? ORDER BY id", (tid,))
        text, prompt = gen.chat_reply(t, msgs)
        db().insert("gen_messages", thread_id=tid, role="assistant", text=text, images=[], created=now())
        db().update("gen_threads", tid, prompt=prompt or text.strip(), status="ready", error=None, updated=now())
    except Exception as ex:
        db().update("gen_threads", tid, status="error", error=str(ex)[:500], updated=now())


@app.get("/api/episodes/{eid}/gen/threads")
def gen_threads(eid: int, slot: str = ""):
    """The chats of one clip (its slot "t0-t1" on the dub timeline), newest first; without slot: all of the episode."""
    rows = db().q("SELECT id, slot, clip, title, status, created, updated FROM gen_threads WHERE episode_id=? "
                  + ("AND slot=? " if slot else "") + "ORDER BY id DESC", (eid, slot) if slot else (eid,))
    for r in rows:
        r["videos"] = db().one("SELECT COUNT(*) AS n FROM gen_jobs WHERE thread_id=? AND status='done'", (r["id"],))["n"]
    return rows


@app.post("/api/episodes/{eid}/gen/threads")
def gen_new_thread(eid: int, body: dict = Body(...)):
    """{clip: {id, label, row, t0, t1, s0, s1, dub_text, src_text, media}} -> a new chat for that clip; its frames,
    analysis and first prompt are made in the background."""
    c = body.get("clip") or {}
    try:
        c = {k: c.get(k) for k in ("id", "label", "row", "dub_text", "src_text", "media", "prev_text", "next_text", "lip")} | \
            {"flags": [str(f)[:200] for f in (c.get("flags") or [])][:12]} | {k: float(c[k]) for k in ("t0", "t1", "s0", "s1")}
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "clip needs t0, t1, s0, s1")
    e = _get("episodes", eid); sh = _get("shows", e["show_id"])                   # context for the AI
    c.update(show=sh["code"], episode=e["number"], labels=(sh["profile"] or {}).get("labels"))
    if c["s1"] - c["s0"] < 40 or c["t1"] - c["t0"] < 100:
        raise HTTPException(400, "the clip is too short")
    slot = f"{round(c['t0'])}-{round(c['t1'])}"
    n = db().one("SELECT COUNT(*) AS n FROM gen_threads WHERE episode_id=? AND slot=?", (eid, slot))["n"]
    tid = db().insert("gen_threads", episode_id=eid, slot=slot, clip=c, title=f"{c.get('label') or 'clip'} · chat {n + 1}",
                      status="frames", created=now(), updated=now())
    import threading
    threading.Thread(target=_gen_prepare, args=(eid, tid), daemon=True).start()
    return _gen_full(eid, tid)


@app.post("/api/episodes/{eid}/gen/threads/{tid}/retry")
def gen_retry(eid: int, tid: int):
    """Prepare a chat that failed (VPN off, interrupted) again: frames -> analysis -> first prompt."""
    t = _gen_thread(eid, tid)
    if t["status"] not in ("error", "ready"):
        raise HTTPException(409, "this chat is being prepared right now")
    if t["status"] == "ready" and db().one("SELECT COUNT(*) AS n FROM gen_messages WHERE thread_id=? AND role='user'", (tid,))["n"] > 1:
        raise HTTPException(409, "this chat already has your messages: start a new chat instead")
    db().update("gen_threads", tid, status="frames", error=None, updated=now())
    import threading
    threading.Thread(target=_gen_prepare, args=(eid, tid), daemon=True).start()
    return _gen_full(eid, tid)


@app.get("/api/episodes/{eid}/gen/threads/{tid}")
def gen_thread(eid: int, tid: int):
    return _gen_full(eid, tid)


@app.delete("/api/episodes/{eid}/gen/threads/{tid}")
def gen_delete_thread(eid: int, tid: int):
    t = _gen_thread(eid, tid)
    db().ex("DELETE FROM gen_threads WHERE id=?", (tid,))
    shutil.rmtree(t["_dir"], ignore_errors=True)       # its frames and uploads; generated clips stay in the bin
    return {"ok": True}


@app.put("/api/episodes/{eid}/gen/threads/{tid}/prompt")
def gen_set_prompt(eid: int, tid: int, body: dict = Body(...)):
    _gen_thread(eid, tid)
    db().update("gen_threads", tid, prompt=(body.get("prompt") or "").strip(), updated=now())
    return _gen_full(eid, tid)


@app.post("/api/episodes/{eid}/gen/threads/{tid}/messages")
async def gen_message(eid: int, tid: int, text: str = Form(""), files: list[UploadFile] = File(default=[])):
    """The editor's message (+ images) -> Claude's answer with the whole chat as context. A ```prompt block in the
    answer becomes the clip's prompt."""
    t = _gen_thread(eid, tid); d = Path(t["_dir"]) / "uploads"; d.mkdir(parents=True, exist_ok=True)
    imgs = []
    for f in files or []:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            raise HTTPException(400, f"{f.filename}: only jpg / png / webp images")
        name = f"u{int(time.time() * 1000)}_{len(imgs)}{ext}"
        (d / name).write_bytes(await f.read()); imgs.append(f"uploads/{name}")
    if not (text or "").strip() and not imgs:
        raise HTTPException(400, "write a message or attach an image")
    db().insert("gen_messages", thread_id=tid, role="user", text=(text or "").strip(), images=imgs, created=now())
    msgs = db().q("SELECT * FROM gen_messages WHERE thread_id=? ORDER BY id", (tid,))
    try:
        reply, prompt = await asyncio.to_thread(gen.chat_reply, t, msgs)
    except Exception as ex:
        db().insert("gen_messages", thread_id=tid, role="error", text=str(ex)[:400], images=[], created=now())
        return _gen_full(eid, tid)
    db().insert("gen_messages", thread_id=tid, role="assistant", text=reply, images=[], created=now())
    if prompt:
        db().update("gen_threads", tid, prompt=prompt, updated=now())
    return _gen_full(eid, tid)


@app.post("/api/episodes/{eid}/gen/threads/{tid}/generate")
def gen_generate(eid: int, tid: int, body: dict = Body(...)):
    """{model, prompt?, use_end, images: [uploaded image to start from]} -> a video job on fal.ai."""
    t = _gen_thread(eid, tid)
    ms = [m["id"] for m in gen.available_models()]
    if not ms:
        raise HTTPException(409, "no video model: the AI key has no Veo model (VPN on?) and no fal.ai key is saved")
    if not t.get("frames"):
        raise HTTPException(409, "the clip's frames are not ready yet")
    prompt = (body.get("prompt") or t.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "the prompt is empty")
    model = (body.get("model") or ms[0]).strip()
    if model not in ms:
        raise HTTPException(400, f"{model} is not available (available: {', '.join(ms)})")
    mode = body.get("mode") or None
    if mode not in (None, "frames", "shots", "refs"):
        raise HTTPException(400, "mode is frames, shots or refs")
    refs = [r for r in (body.get("refs") or []) if isinstance(r, str) and ".." not in r and r.startswith(("frames/", "uploads/"))][:3]
    if (mode == "refs" or refs) and not gen.model_info(model).get("refs"):
        raise HTTPException(400, f"{model} cannot take reference images: choose Veo 3.1")
    jid = db().insert("gen_jobs", thread_id=tid, model=model, prompt=prompt, status="starting", created=now(),
                      params={"use_start": True, "use_end": bool(body.get("use_end", True)), "images": body.get("images") or [],
                              "mode": mode, "refs": refs})
    db().update("gen_threads", tid, prompt=prompt, updated=now())
    e = _get("episodes", eid)
    gen.start_job(db(), jid, e["work_dir"], video=_clip_video(eid, e["work_dir"], t["clip"]))
    return _gen_full(eid, tid)


@app.get("/api/episodes/{eid}/gen/threads/{tid}/file/{path:path}")
def gen_file(eid: int, tid: int, path: str):
    base = Path(_gen_thread(eid, tid)["_dir"]).resolve(); p = (base / path).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404, "no such file")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})


def _frames_from(eid):
    """The file thumbnails are cut from: the preview copy if there is one (here or in R2), else the source video."""
    e = _get("episodes", eid)
    show = _get("shows", e["show_id"])
    p = tlm.proxy_path(e["work_dir"]) or storage.ensure_local(show, e, tlm.PROXY)
    if p:
        return e, p
    a = db().one("SELECT path FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'", (eid,))
    return e, (a["path"] if a and Path(a["path"]).exists() else None)


@app.get("/api/episodes/{eid}/thumb")
def thumb(eid: int, ms: float, h: int = 120):
    e, src = _frames_from(eid)
    if not src:
        raise HTTPException(404, "no source video")
    p = tlm.thumb(e["work_dir"], str(src), ms, max(60, min(h, 360)))
    if not p:
        raise HTTPException(404, "no frame")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/episodes/{eid}/waveform")
def wave(eid: int):
    e = _get("episodes", eid)
    show = _get("shows", e["show_id"])
    cache = f"waveform_{50}.json"
    a = db().one("SELECT path FROM assets WHERE episode_id=? AND kind='dub_audio' AND role='input'", (eid,))
    if a and Path(a["path"]).exists():
        res = tlm.waveform(e["work_dir"], a["path"])
        storage.mirror(show, e, cache)              # tiny; saves the deployed box re-reading the audio
        return res
    got = storage.ensure_local(show, e, cache)      # measured once, then it travels with the episode
    if got:
        return json.loads(got.read_text())
    p = storage.ensure_local(show, e, tlm.DUB_PREVIEW)
    if not p:
        raise HTTPException(404, "no dub audio")
    res = tlm.waveform(e["work_dir"], str(p))
    storage.mirror(show, e, cache)
    return res


# ---------------- settings (milestone 7) ----------------
@app.get("/api/settings")
def get_settings(request: Request):
    return {"ai": stg.ai_settings(), "app": stg.app_settings(cfg), "health": stg.health(cfg, cfg.db_path),
            "supervised": os.environ.get("DUBSTUDIO_SUPERVISED") == "1",
            "storage": storage.public_settings(),
            "accounts": {"required": auth.required(), "ttl_seconds": auth.ttl(), "me": me(request),
                         "users": (db().one("SELECT COUNT(*) AS n FROM users") or {}).get("n", 0)}}


@app.put("/api/settings/ai")
def put_ai(body: dict = Body(...)):
    """{base, email, key, model, model_fast, remove_key}: an empty key keeps the one saved. Saved in app/.env."""
    try:
        return stg.write_ai({"AI_BASE": body.get("base"), "AI_EMAIL": body.get("email"), "AI_KEY": body.get("key"),
                             "AI_MODEL": body.get("model"), "AI_MODEL_FAST": body.get("model_fast"),
                             "FAL_KEY": body.get("fal_key")},
                            remove_key="FAL_KEY" if body.get("remove_fal_key") else bool(body.get("remove_key")))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.post("/api/settings/ai/test")
def test_ai():
    return stg.test_ai()


@app.put("/api/settings/storage")
def put_storage(body: dict = Body(...)):
    """{account_id, bucket, endpoint, key_id, secret, prefix, auto_upload, db_sync}: saved in app/.env.
    Empty key fields keep the keys that are already saved; the keys are never sent back."""
    from . import envfile
    vals = {"R2_ACCOUNT_ID": body.get("account_id"), "R2_BUCKET": body.get("bucket"),
            "R2_ENDPOINT": body.get("endpoint"), "R2_PREFIX": body.get("prefix")}
    for field, key in (("key_id", "R2_ACCESS_KEY_ID"), ("secret", "R2_SECRET_ACCESS_KEY")):
        if (body.get(field) or "").strip():
            vals[key] = body[field].strip()
    for field, key in (("auto_upload", "R2_AUTO_UPLOAD"), ("db_sync", "R2_DB_SYNC")):
        if field in body:
            vals[key] = "true" if body[field] else "false"
    vals = {k: v for k, v in vals.items() if v is not None}
    if any(re.search(r"\s", str(v)) for v in vals.values()):
        raise HTTPException(400, "no spaces allowed in the storage settings")
    envfile.write(vals, keep_empty=True)
    return storage.status()


@app.put("/api/settings/app")
def put_app(body: dict = Body(...)):
    """Paths and performance, saved in app/config.json; used after a restart."""
    try:
        saved = stg.write_app(body)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"saved": saved, "restart_needed": True}


@app.post("/api/restart")
def restart():
    """Restart the server (only when the launcher watches it: it starts it again at once). Refused while a step runs."""
    if os.environ.get("DUBSTUDIO_SUPERVISED") != "1":
        raise HTTPException(409, "Dub Studio was not started with its launcher; stop it and start it again")
    if db().one("SELECT id FROM jobs WHERE status='running'"):
        raise HTTPException(409, "a step is running: wait for it, or cancel it first")
    import threading
    threading.Timer(0.5, lambda: os._exit(3)).start()
    return {"restarting": True}


# ---------------- cloud storage (Cloudflare R2) ----------------
@app.get("/api/storage/status")
def storage_status():
    """Is R2 set up, does the bucket answer, and when was the database copied up."""
    out = storage.status()
    try:
        out["database"] = storage.db_status()
    except Exception as ex:
        out["database"] = {"configured": True, "error": str(ex)[:200]}
    return out


@app.get("/api/episodes/{eid}/uploads")
def list_uploads(eid: int):
    """What of this episode is in the cloud, and what is on its way up."""
    ep = _get("episodes", eid); show = _get("shows", ep["show_id"])
    return {"configured": storage.configured(), "auto": storage.settings()["auto_upload"],
            "prefix": storage.episode_prefix(show, ep) if storage.configured() else None,
            "uploads": storage.uploads_of(db(), eid)}


@app.post("/api/episodes/{eid}/uploads")
def start_uploads(request: Request, eid: int, body: dict = Body(None)):
    """Send this episode's finished files to R2 (all of them, or {"kinds": ["clean", "qc"]})."""
    if not storage.configured():
        raise HTTPException(409, "R2 storage is not set up (Settings -> Cloud storage)")
    ep = _get("episodes", eid); show = _get("shows", ep["show_id"])
    files = deliver.deliverables(show, ep, None)             # what is on disk now (no pipeline context needed)
    kinds = (body or {}).get("kinds")
    if kinds:
        files = [f for f in files if f["kind"] in kinds]
    rows = storage.queue_uploads(db(), show, ep, files, user_id=me(request).get("id"))
    if not rows:
        raise HTTPException(409, "there is nothing to send up yet: render the episode first")
    return {"queued": rows}


@app.post("/api/uploads/{uid}/retry")
def retry_upload(uid: int):
    _get("uploads", uid)
    storage.retry_upload(db(), uid)
    storage.start_worker(db())
    return _get("uploads", uid)


@app.get("/api/uploads/{uid}/link")
def upload_link(uid: int, seconds: int = 3600):
    """A download link straight from R2 (it works for a while without any key)."""
    row = _get("uploads", uid)
    if row["status"] != "done":
        raise HTTPException(409, "this file is not in the cloud yet")
    return {"url": storage.presign(row["key"], seconds=seconds, download_name=row["name"]),
            "expires": now() + seconds, "name": row["name"]}


@app.delete("/api/uploads/{uid}")
def delete_upload(uid: int, remove_file: bool = False):
    """Forget an upload; `remove_file=true` also deletes the object in R2."""
    row = _get("uploads", uid)
    if remove_file and row["status"] == "done":
        try:
            storage.delete(row["key"])
        except Exception as ex:
            raise HTTPException(502, str(ex)[:200])
    db().ex("DELETE FROM uploads WHERE id=?", (uid,))
    return {"deleted": uid}


@app.get("/api/storage/objects")
def storage_objects(request: Request, prefix: str = "", limit: int = 200):
    if not auth.is_admin(me(request)):
        raise HTTPException(403, "only the owner's account can list the bucket")
    try:
        return storage.listing(prefix, limit)
    except Exception as ex:
        raise HTTPException(502, str(ex)[:200])


@app.post("/api/storage/db/push")
def storage_db_push():
    """Copy the database up to R2 now (it also happens at startup, after the daily backup)."""
    try:
        return storage.push_db(db().conn, cfg.library)
    except Exception as ex:
        raise HTTPException(502, str(ex)[:300])


@app.post("/api/storage/db/pull")
def storage_db_pull():
    """Bring the copy in R2 down to <library>/restored/ (never over the live database)."""
    try:
        return storage.pull_db(cfg.library)
    except Exception as ex:
        raise HTTPException(502, str(ex)[:300])


# ---------------- files (pick inputs) ----------------
@app.get("/api/fs")
def list_dir(path: str = "~"):
    p = Path(path).expanduser()
    if not p.is_dir():
        raise HTTPException(404, "not a folder")
    items = []
    for c in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if c.name.startswith("."):
            continue
        items.append({"name": c.name, "path": str(c), "dir": c.is_dir(), "size": c.stat().st_size if c.is_file() else None})
    return {"path": str(p), "parent": str(p.parent), "items": items[:2000]}


@app.post("/api/reveal")
def reveal(body: dict = Body(...)):
    """Show a file or folder in Finder (Mac)."""
    p = Path(body.get("path") or "").expanduser()
    if not p.exists():
        raise HTTPException(404, "not found")
    import subprocess, sys
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)] if p.is_file() else ["open", str(p)])
    return {"ok": True}


# ---------------- live events ----------------
def _episode_owner(eid):
    row = db().one("SELECT s.owner_id AS owner_id FROM episodes e JOIN shows s ON s.id=e.show_id WHERE e.id=?", (eid,))
    return row.get("owner_id") if row else None


@app.websocket("/api/ws")
async def ws(sock: WebSocket):
    """Live progress. Sign-in is needed here too, and an account only hears about its own episodes."""
    user = None
    if auth.required():
        user = auth.public(auth.user_of_token(db(), sock.cookies.get(auth.COOKIE)))
        if not user:
            await sock.close(code=1008)                       # policy violation: not signed in
            return
    else:
        user = local_user()
    await sock.accept()
    loop = asyncio.get_running_loop(); q = bus.subscribe(loop)
    admin = auth.is_admin(user)
    try:
        while True:
            ev = await q.get()
            eid = ev.get("episode_id")
            if not admin and eid and _episode_owner(eid) != user.get("id"):
                continue
            await sock.send_text(json.dumps(ev, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(loop, q)


# ---------------- the UI ----------------
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.api_route("/{full:path}", methods=["GET", "HEAD"])
    def spa(full: str):
        f = DIST / full
        if full and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})   # a rebuilt UI shows at once
