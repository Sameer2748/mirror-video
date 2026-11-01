# 08 — Accounts (id + password) and cloud storage (Cloudflare R2)

Added 2026-10-30 in the `hamburg` workspace. Two things:

1. **Sign-in.** Everyone who uses Dub Studio has an id and a password. After signing in, each account sees **its own**
   shows and episodes; the owner's account sees every one.
2. **Cloud storage.** The finished videos and sheets of an episode, and a copy of the database, are kept in a
   Cloudflare R2 bucket (the S3 API) as well as on the Mac.

Nothing in the pipeline changed: the same 34 steps run the same way.

---

## 1. Accounts

| | |
| --- | --- |
| Sign-in screen | the whole app is behind it (`/api/info` is the only open endpoint, so the launcher can still check the app) |
| Where accounts live | the `users` table of the app's own database (`<library>/dubstudio.db`) |
| Passwords | never stored: only a PBKDF2-HMAC-SHA256 hash (240 000 rounds, own salt per account) |
| Sessions | a random token in an httpOnly cookie; the database keeps only its SHA-256. 7 days (`AUTH_SESSION_TTL_SECONDS`) |
| Wrong passwords | 10 wrong tries for one id = a five-minute wait |
| The first account | made at startup from `ADMIN_USERNAME` / `ADMIN_PASSWORD` in `app/.env`; without them: `admin` + a password printed once in the server log |
| Roles | **owner** (admin: sees everything, manages accounts and settings) and **editor** (its own shows) |
| Switch it off | `AUTH_REQUIRED=false` in `app/.env` → the app works as before (one editor, no sign-in, as the owner's account) |

**Screens**: the sign-in screen; **Accounts** in the sidebar (change your own password; the owner also adds accounts,
gives a new password, switches an account off or on, makes someone an owner, removes an account).

### What "its own" means
- A show belongs to the account that created it (`shows.owner_id`); its episodes, jobs, renders, markers, uploads and
  live progress follow the show.
- The check is in one place (`main.accounts_guard`), for every `/api/...` path that carries an id, so no endpoint can
  forget it. Someone else's id answers **403**.
- The owner's account (role `admin`) sees and opens everything.
- Only the owner's account may: manage accounts (`/api/users…`), change settings (`/api/settings/…`), restart the app,
  list the bucket, push or pull the database.
- Live progress (`/api/ws`) also needs a sign-in, and an account only hears about its own episodes.
- **Show codes stay unique on the Mac** (the code decides the output file names): if one account has `WBM`, another
  account adding `WBM` is told it belongs to another account. Sharing a show between two accounts is not built.
- An existing library (made before accounts) is upgraded at startup: every show gets the owner's account.

### The API
```
POST /api/auth/login     {username, password} -> the account + the session cookie
POST /api/auth/logout    GET /api/auth/me     POST /api/auth/password {current, password}
GET/POST /api/users      PATCH/DELETE /api/users/{id}          (owner's account only)
```

## 2. Cloud storage (R2)

R2 speaks the S3 API, so the app signs its own requests (AWS Signature V4 over `httpx`, `dubstudio/storage.py`) —
no extra package. Settings live in `app/.env` (or Settings → Cloud storage; the keys are never shown again):

```
R2_ACCOUNT_ID  R2_ACCESS_KEY_ID  R2_SECRET_ACCESS_KEY  R2_BUCKET  R2_ENDPOINT
R2_PREFIX=dub-studio-mac      a folder inside the bucket (the bucket is shared with another app)
R2_AUTO_UPLOAD=true           every finished episode goes up by itself
R2_DB_SYNC=true               the database is copied up at startup
```

What goes up, under the prefix:

| Key | What |
| --- | --- |
| `shows/<SHOW>/ep<N>/out/<file>` | the deliverables: the three videos, the English SRT, the mapping / scene-edit / QC sheets, the Premiere XML, the shot library |
| `db/dubstudio.db` | the newest copy of the database (SQLite's own online backup, safe while the app writes) |
| `db/history/dubstudio-<date>.db` | one copy per day |

- The upload runs in the background, one file at a time, with a progress bar on the episode's **Deliverables** tab
  ("In the cloud"): queued → sending → done, **Try again** on a failure, and **Download link** (a signed link that
  works for an hour without any key).
- A file that is already up there with the same size is not sent again.
- An episode that finishes (status `done`) queues its deliverables by itself (`R2_AUTO_UPLOAD`).
- Settings → Cloud storage: **Check the bucket**, **Copy the database to R2 now**, **Bring the copy down**
  (it lands in `<library>/restored/`, never over the live database). Health shows a "Cloud storage (R2)" line.

### R2 is storage, not a database
R2 keeps files. A live SQLite database needs file locking on the machine that writes it, so the database **stays on
the Mac** and the file is mirrored up to R2 (this is what "R2 as the database" means here: the data also lives off the
machine, and can be pulled back). If the owner ever wants a real remote database (two Macs writing at the same time),
that is Postgres / Cloudflare D1 and a bigger change — not done.

## 3. How to run it, and where everything lives

### Running it
```bash
cd <this workspace>/app
./scripts/start.sh                       # the normal way: port 8765, opens the browser
DUBSTUDIO_PORT=8766 ./scripts/start.sh   # next to the older copy, which uses 8765
```
The sign-in screen comes first. The first account is made from `ADMIN_USERNAME` / `ADMIN_PASSWORD` in `app/.env`
(**the password is only in that file — it is never written in the docs or in git**). Forgotten it? Stop the app, put a
new `ADMIN_PASSWORD` in `app/.env`, delete the `users` row of that account (or ask here) and start again.

The test instance of 2026-10-30 ran with:
```bash
DUBSTUDIO_LIBRARY=<workspace>/app/library \
DUBSTUDIO_PIPELINE_PY=/Users/manish/Downloads/dub-studioo/app/pipeline_env/bin/python \
.venv/bin/python -m uvicorn dubstudio.main:app --host 127.0.0.1 --port 8766
```
so it has **a copy of the live database** (4 shows, 7 episodes) while the older copy in `~/Downloads/dub-studioo`
keeps its own and stays untouched. Work and output folders are the real ones (their paths are inside the database),
so only one of the two apps should process an episode at a time.

### Where the data lives

| What | Where |
| --- | --- |
| Accounts, shows, episodes, jobs, timelines, uploads | the app's SQLite database, `<library>/dubstudio.db` (`DUBSTUDIO_LIBRARY`) |
| Daily database backups | `<library>/backups/dubstudio-<date>.db` (10 kept) + `db/` in R2 |
| Work files of an episode (proxies, Whisper, lip scan, scene edit) | `<library>/work/<SHOW>_ep<N>/` |
| Finished videos and sheets | the show's own output folder (`<show folder>/output/ep<N>/`), never the inputs |
| A second copy of the finished files | R2: `<R2_PREFIX>/shows/<SHOW>/ep<N>/out/` |
| Keys and passwords | `app/.env` only (mode 600, gitignored): AI gateway, fal.ai, R2, `ADMIN_*`, `AUTH_*` |
| What was built and why | `docs/03_PROGRESS.md` (dated entries), this file, `docs/07_HANDOVER.md` (the map) |

## 4. Tests

`cd app/backend && .venv/bin/python -m pytest -q tests` → **123 passed, 4 skipped** (the two skipped are the live
checks: `DUBSTUDIO_LIVE_AI=1` for the AI gateway, `DUBSTUDIO_LIVE_R2=1` for a real R2 round trip).

- `tests/test_auth.py` (15): signing in and out, the session running out, wrong-password slowdown, changing a
  password, the owner's accounts page, and that one account cannot see or open another's shows, episodes, jobs,
  markers, renders, uploads or live events.
- `tests/test_storage.py` (14 + 1 live): the signing maths against AWS's published example, put / get / head / list /
  delete and signed links against a small S3-shaped server on localhost, the episode upload queue (including retry
  after a failure), the database copy up and back, auto-upload on and off, and saving the keys from Settings.
- The whole old suite runs signed in as a test owner account (`tests/conftest.py`), so every existing endpoint is
  exercised with the guard switched on. The tests never read the real `app/.env`.

Checked by hand on 2026-10-30 against the real bucket (`dubstudioprod`, folder `dub-studio-mac/`): a show + episode
made through the API, two files uploaded, the download link played back, the database copied up and the test objects
deleted again.
