#!/usr/bin/env python3
"""Put the API + screens on Render (docs/09 §1). Run it again any time: it changes only what is wrong.

    RENDER_API_KEY=rnd_... python3 app/scripts/deploy_render.py            # show what it would do
    RENDER_API_KEY=rnd_... python3 app/scripts/deploy_render.py --apply    # do it

What it does:
  1. finds the service (--service srv-xxx, or by name) and points it at our repo and branch, built from
     app/backend/Dockerfile;
  2. makes a Postgres database if there is none, and wires DATABASE_URL to it;
  3. sets the settings from render.yaml and the **secrets read out of app/.env** -- the keys travel straight from
     this Mac to Render and are never printed, logged or committed;
  4. triggers a deploy and waits for it to go live.

The key is read from the environment or app/.env (RENDER_API_KEY). Nothing is sent anywhere without --apply.
"""
import json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "app" / "backend"))
from dubstudio import envfile                                   # noqa: E402  (reads app/.env, env wins)

API = "https://api.render.com/v1"
REPO = "https://github.com/Sameer2748/mirror-video"
BRANCH = os.environ.get("DEPLOY_BRANCH", "handover-doc-review")
NAME = "dubstudio-api"
DB_NAME = "dubstudio-db"
# The owner's existing service (the old DUBSTUDIO app, which he no longer needs: 2026-11-02, "we dont need this app
# now can we replace its github"). It is already a Docker web service on 2 CPU / 8 GB, so only the repo, the build
# paths and the environment change. It lives in **singapore** and has a 20 GB disk mounted at /app/output -- the
# database has to be in the same region to use the internal connection string, and the library goes on that disk
# rather than destroying it to mount a new one somewhere else.
SERVICE = os.environ.get("DEPLOY_SERVICE", "srv-dar686jncjis73c9rr90")
REGION = "singapore"
LIBRARY = "/app/output/library"

# the plain settings (the same list as render.yaml, which is the written record of them)
SETTINGS = {
    "AUTH_REQUIRED": "true",
    "AUTH_COOKIE_SECURE": "true",                 # https on Render: the session cookie must be secure
    "AI_PROVIDER": "openrouter",                  # the owner's gateway is VPN-only, so a server cannot reach it
    "AI_CACHE": "true",
    "CLOUD_STEPS": "lip_gpu",                     # which steps go to the RunPod workers instead of this box
    "CLOUD_PIECES": "8",
    "UPLOAD_MODE": "direct",                      # the browser PUTs straight to R2
    "MAX_ACTIVE_EPISODES": "2",
    "PYTHONUNBUFFERED": "1",
    # One uvicorn process per container. Render picks WEB_CONCURRENCY from the CPU count (2 here), and a second
    # process means a second job runner in the same container, both polling for the same steps. When we really want
    # more than one, they need REDIS_URL for shared events and a Redis-claimed queue (docs/09 §4).
    "WEB_CONCURRENCY": "1",
    "DUBSTUDIO_LIBRARY": LIBRARY,
}
# copied from app/.env as they are; never printed
SECRETS = ["OPENROUTER_API_KEY", "R2_ACCOUNT_ID", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
           "R2_BUCKET", "R2_PREFIX", "RUNPOD_API_KEY", "RUNPOD_CPU_ENDPOINT", "ADMIN_USERNAME", "ADMIN_PASSWORD"]

APPLY = "--apply" in sys.argv
KEY = os.environ.get("RENDER_API_KEY") or envfile.get("RENDER_API_KEY")


def call(method, path, body=None, quiet=False):
    if not KEY:
        sys.exit("no RENDER_API_KEY (environment or app/.env)")
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {KEY}", "Accept": "application/json",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode()[:400]
        if quiet:
            return {"_error": ex.code, "_detail": detail}
        sys.exit(f"Render said {ex.code} to {method} {path}: {detail}")


def arg(flag):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None


def do(what, fn):
    """One change: described always, done only with --apply."""
    print(("  do   " if APPLY else "  would ") + what)
    return fn() if APPLY else None


def find_service():
    sid = arg("--service") or SERVICE
    if sid:
        s = call("GET", f"/services/{sid}")
        return s.get("service", s)
    for row in call("GET", "/services?limit=100"):
        s = row.get("service", row)
        if s.get("name") == NAME:
            return s
    return None


def find_database():
    for row in call("GET", "/postgres?limit=100"):
        d = row.get("postgres", row)
        if d.get("name") == DB_NAME:
            return d
    return None


def main():
    print(f"repo {REPO}  branch {BRANCH}")
    missing = [k for k in SECRETS if not envfile.get(k)]
    if missing:
        print(f"! not in app/.env, so not sent: {', '.join(missing)}")

    svc = find_service()
    if svc:
        print(f"service: {svc['name']} ({svc['id']}), repo {svc.get('repo')}, branch {svc.get('branch')}, "
              f"runtime {svc.get('serviceDetails', {}).get('env') or svc.get('type')}")
    else:
        print(f"service: none called {NAME} -- one will be made")

    db = find_database()
    if db:
        print(f"database: {db['name']} ({db['id']}), {db.get('status')}")
    else:
        db = do(f"make the Postgres database {DB_NAME} in {REGION}",
                lambda: call("POST", "/postgres", {"name": DB_NAME, "plan": "basic_256mb", "region": REGION,
                                                   "version": "16", "ownerId": svc["ownerId"]}))
        if APPLY:
            db = db.get("postgres", db)

    body = {"repo": REPO, "branch": BRANCH, "autoDeploy": "yes",
            "serviceDetails": {"env": "docker", "healthCheckPath": "/api/info",
                               "envSpecificDetails": {"dockerfilePath": "./app/backend/Dockerfile",
                                                      "dockerContext": "./app/backend"}}}
    if svc:
        do(f"point {svc['id']} at our repo, branch {BRANCH}, built from app/backend/Dockerfile",
           lambda: call("PATCH", f"/services/{svc['id']}", body))
    else:
        svc = do("make the web service", lambda: call("POST", "/services", {
            "type": "web_service", "name": NAME, "region": REGION, "plan": "standard", **body}))
        svc = (svc or {}).get("service", svc) if APPLY else {"id": "<new>"}

    # the environment: settings from this file, secrets from app/.env, DATABASE_URL from the database
    vars_ = dict(SETTINGS)
    for k in SECRETS:
        v = envfile.get(k)
        if v:
            vars_[k] = v
    if db and db.get("id"):
        conn = call("GET", f"/postgres/{db['id']}/connection-info", quiet=True) if APPLY else {}
        if conn.get("internalConnectionString"):
            vars_["DATABASE_URL"] = conn["internalConnectionString"]
        else:
            print("  ! could not read the database's connection string; set DATABASE_URL in the dashboard")
    do(f"set {len(vars_)} environment variables ({len([k for k in vars_ if k in SECRETS])} of them secret, "
       f"values not shown)",
       lambda: call("PUT", f"/services/{svc['id']}/env-vars",
                    [{"key": k, "value": v} for k, v in vars_.items()]))

    dep = do("deploy", lambda: call("POST", f"/services/{svc['id']}/deploys", {"clearCache": "do_not_clear"}))
    if not APPLY:
        print("\nnothing was sent. Run it again with --apply to do the above.")
        return
    did = (dep or {}).get("id")
    print(f"  deploy {did} started; waiting for it to go live (a Docker build takes a few minutes)")
    for _ in range(120):
        time.sleep(15)
        st = call("GET", f"/services/{svc['id']}/deploys/{did}", quiet=True)
        status = (st.get("deploy", st) or {}).get("status")
        print(f"    {status}", flush=True)
        if status in ("live", "build_failed", "update_failed", "canceled", "pre_deploy_failed"):
            break
    fresh = call("GET", f"/services/{svc['id']}")
    url = (fresh.get("service", fresh) or {}).get("serviceDetails", {}).get("url")
    print(f"\nservice URL: {url}")
    print("Still to do by hand: add that URL to the R2 bucket's CORS AllowedOrigins (docs/09 §3).")


if __name__ == "__main__":
    main()
