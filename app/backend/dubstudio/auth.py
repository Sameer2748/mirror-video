"""Accounts: an id and a password per person, sessions in a cookie, and who owns which show.

Everyone who signs in sees **their own** shows and episodes; the admin (the owner) sees every one. A show belongs
to the account that created it (`shows.owner_id`); an episode follows its show.

Settings (app/.env, environment variables win -- see `envfile.py`):
  AUTH_REQUIRED=true                 sign-in needed for every /api call (false = the old single-user mode)
  AUTH_SESSION_TTL_SECONDS=604800    how long a sign-in lasts (7 days)
  ADMIN_USERNAME / ADMIN_PASSWORD    the first account, made at startup when the database has no accounts

Passwords are never stored: only `pbkdf2$<rounds>$<salt>$<hash>` (PBKDF2-HMAC-SHA256). Session cookies hold a
random token; the database keeps only its SHA-256, so the file cannot be used to sign in.
"""
import hashlib, hmac, os, re, secrets, time

from . import envfile

COOKIE = "dubstudio_session"
ROUNDS = 240_000
TTL_DEFAULT = 604800                      # 7 days
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
MIN_PASSWORD = 6
ROLES = ("admin", "editor")
_fails = {}                               # username -> [times] (slows down password guessing)


# ---------------- settings ----------------
def required():
    return envfile.flag("AUTH_REQUIRED", True)


def ttl():
    v = envfile.number("AUTH_SESSION_TTL_SECONDS", TTL_DEFAULT)
    return v if 60 <= v <= 60 * 60 * 24 * 365 else TTL_DEFAULT


# ---------------- passwords ----------------
def hash_password(password, rounds=ROUNDS, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2${rounds}${salt.hex()}${h.hex()}"


def verify_password(password, stored):
    try:
        kind, rounds, salt, want = (stored or "").split("$")
        if kind != "pbkdf2":
            return False
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(h.hex(), want)


def check_username(username):
    u = (username or "").strip()
    if not USERNAME_RE.match(u):
        raise ValueError("the id must be 3 to 32 letters, digits, dot, dash or underscore")
    return u


def check_password(password):
    p = password or ""
    if len(p) < MIN_PASSWORD:
        raise ValueError(f"the password must be at least {MIN_PASSWORD} characters")
    return p


# ---------------- accounts ----------------
def public(user):
    """A user row without the password hash (what the API returns)."""
    if not user:
        return None
    return {k: v for k, v in user.items() if k != "pw"}


def by_name(db, username):
    return db.one("SELECT * FROM users WHERE LOWER(username)=LOWER(?)", ((username or "").strip(),))


def count(db):
    return (db.one("SELECT COUNT(*) AS n FROM users") or {}).get("n", 0)


def create_user(db, username, password, name=None, role="editor", email=None):
    username = check_username(username); check_password(password)
    if role not in ROLES:
        raise ValueError(f"the role must be one of {', '.join(ROLES)}")
    if by_name(db, username):
        raise ValueError(f"the id {username} is taken")
    uid = db.insert("users", username=username, name=(name or username).strip(), email=(email or "").strip() or None,
                    pw=hash_password(password), role=role, active=1, created=time.time(), updated=time.time())
    return db.one("SELECT * FROM users WHERE id=?", (uid,))


def set_password(db, uid, password):
    check_password(password)
    db.update("users", uid, pw=hash_password(password), updated=time.time())
    db.ex("DELETE FROM sessions WHERE user_id=?", (uid,))          # a new password signs the old devices out


def seed(db, log=print):
    """Make the first account when the database has none: ADMIN_USERNAME / ADMIN_PASSWORD from app/.env, else
    `admin` with a password printed once in the server log (local app: the owner sees his own terminal)."""
    if count(db):
        return None
    user = envfile.get("ADMIN_USERNAME", "admin")
    pw = envfile.get("ADMIN_PASSWORD")
    shown = None
    if not pw:
        pw = shown = secrets.token_urlsafe(9)
    try:
        u = create_user(db, user, pw, name=user, role="admin")
    except ValueError as ex:
        log(f"accounts: could not make the first account ({ex}); set ADMIN_USERNAME / ADMIN_PASSWORD in app/.env")
        return None
    log(f"accounts: made the owner's account \"{u['username']}\"" +
        (f" with the password {shown} (change it in the app: Account -> password)" if shown else " (password from app/.env)"))
    return u


# ---------------- sessions ----------------
def _token_hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def sign_in(db, username, password, agent=None, ip=None):
    """-> (user, token). Raises ValueError with a message the screen can show."""
    key = (username or "").strip().lower()
    recent = [t for t in _fails.get(key, []) if time.time() - t < 300]
    _fails[key] = recent
    if len(recent) >= 10:
        raise ValueError("too many tries: wait five minutes")
    user = by_name(db, username)
    if not user or not verify_password(password or "", user["pw"]):
        _fails[key] = recent + [time.time()]
        raise ValueError("wrong id or password")
    if not user.get("active", 1):
        raise ValueError("this account is switched off: ask the owner")
    _fails.pop(key, None)
    token = secrets.token_urlsafe(32)
    db.insert("sessions", token=_token_hash(token), user_id=user["id"], created=time.time(),
              expires=time.time() + ttl(), seen=time.time(), agent=(agent or "")[:200], ip=(ip or "")[:60])
    db.update("users", user["id"], last_login=time.time())
    db.ex("DELETE FROM sessions WHERE expires < ?", (time.time(),))
    return db.one("SELECT * FROM users WHERE id=?", (user["id"],)), token


def sign_out(db, token):
    if token:
        db.ex("DELETE FROM sessions WHERE token=?", (_token_hash(token),))


def user_of_token(db, token):
    """The signed-in user of a cookie token, or None (expired, unknown or switched off)."""
    if not token:
        return None
    row = db.one("SELECT s.id AS sid, s.expires, u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
                 (_token_hash(token),))
    if not row:
        return None
    if (row.get("expires") or 0) < time.time():
        db.ex("DELETE FROM sessions WHERE id=?", (row["sid"],))
        return None
    if not row.get("active", 1):
        return None
    if time.time() - (row.get("seen") or 0) > 300:                 # touch at most every 5 minutes
        db.update("sessions", row["sid"], seen=time.time())
    user = {k: v for k, v in row.items() if k not in ("sid", "expires", "seen", "agent", "ip")}
    return user


def is_admin(user):
    return bool(user and user.get("role") == "admin")


def cookie_kwargs():
    return dict(key=COOKIE, httponly=True, samesite="lax", max_age=ttl(), path="/",
                secure=bool(envfile.flag("AUTH_COOKIE_SECURE", False)))
