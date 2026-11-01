"""Accounts: signing in, sessions, and that one account never sees another's shows or episodes."""
import time

from conftest import ADMIN


def make_show(c, folder, code="SHOW", name="Show"):
    r = c.post("/api/shows", json={"name": name, "code": code, "folder": str(folder), "preset": "WBM"})
    assert r.status_code == 200, r.text
    return r.json()


def second_client(env, username="luca", password="luca-password", role="editor"):
    """A new account made by the owner, and a client signed in as that account."""
    c = env["client"]
    r = c.post("/api/users", json={"username": username, "password": password, "name": username.title(), "role": role})
    assert r.status_code == 200, r.text
    other = c.__class__(c.app, base_url=str(c.base_url))
    assert other.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return other, r.json()


# ---------------- signing in ----------------
def test_login_logout_and_me(env):
    c = env["client"]
    me = c.get("/api/auth/me").json()
    assert me["required"] is True and me["user"]["username"] == ADMIN[0] and me["user"]["role"] == "admin"
    assert "pw" not in me["user"]                                    # the hash never leaves the server
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").json()["user"] is None
    assert c.get("/api/shows").status_code == 401                    # signed out: the API is closed
    assert c.post("/api/auth/login", json={"username": ADMIN[0], "password": "wrong"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}).status_code == 200
    assert c.get("/api/shows").status_code == 200


def test_info_is_open_but_everything_else_is_not(env):
    c = env["client"]
    c.post("/api/auth/logout")
    assert c.get("/api/info").status_code == 200                     # the launcher checks this before signing in
    for url in ("/api/shows", "/api/settings", "/api/renders", "/api/users", "/api/storage/status"):
        assert c.get(url).status_code == 401, url


def test_wrong_password_is_slowed_down(env):
    c = env["client"]
    c.post("/api/auth/logout")
    for _ in range(10):
        assert c.post("/api/auth/login", json={"username": ADMIN[0], "password": "no"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 401 and "five minutes" in r.json()["detail"]
    import dubstudio.auth as auth
    auth._fails.clear()                                              # (the wait is five minutes; the test does not)
    assert c.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}).status_code == 200


def test_session_runs_out(env, monkeypatch):
    c = env["client"]
    import dubstudio.main as main
    main.db().ex("UPDATE sessions SET expires=?", (time.time() - 1,))
    assert c.get("/api/shows").status_code == 401
    assert main.db().one("SELECT id FROM sessions") is None          # the dead session is cleaned up


def test_change_own_password(env):
    c = env["client"]
    assert c.post("/api/auth/password", json={"current": "nope", "password": "another-one"}).status_code == 403
    assert c.post("/api/auth/password", json={"current": ADMIN[1], "password": "123"}).status_code == 400
    assert c.post("/api/auth/password", json={"current": ADMIN[1], "password": "a-longer-password"}).status_code == 200
    assert c.get("/api/shows").status_code == 200                    # this device stays signed in
    c.post("/api/auth/logout")
    assert c.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}).status_code == 401
    assert c.post("/api/auth/login", json={"username": ADMIN[0], "password": "a-longer-password"}).status_code == 200


# ---------------- the owner's accounts page ----------------
def test_owner_adds_an_account_and_only_the_owner_may(env):
    c = env["client"]
    other, user = second_client(env)
    assert user["role"] == "editor" and "pw" not in user
    assert other.get("/api/users").status_code == 403                # an editor may not manage accounts
    assert other.put("/api/settings/app", json={}).status_code == 403
    assert other.post("/api/restart").status_code == 403
    assert len(c.get("/api/users").json()) == 2
    assert c.post("/api/users", json={"username": "luca", "password": "x-another"}).status_code == 400   # id taken
    assert c.post("/api/users", json={"username": "no", "password": "x-another"}).status_code == 400     # id too short
    assert c.post("/api/users", json={"username": "fine_id", "password": "12345"}).status_code == 400    # password short


def test_switched_off_account_cannot_sign_in(env):
    c = env["client"]
    other, user = second_client(env)
    assert c.patch(f"/api/users/{user['id']}", json={"active": False}).status_code == 200
    assert other.get("/api/shows").status_code == 401               # its session is dropped at once
    r = other.post("/api/auth/login", json={"username": "luca", "password": "luca-password"})
    assert r.status_code == 401 and "switched off" in r.json()["detail"]


def test_the_last_owner_account_stays(env):
    c = env["client"]
    me = c.get("/api/auth/me").json()["user"]
    assert c.patch(f"/api/users/{me['id']}", json={"role": "editor"}).status_code == 409
    assert c.patch(f"/api/users/{me['id']}", json={"active": False}).status_code == 409
    assert c.delete(f"/api/users/{me['id']}").status_code == 409


def test_removing_an_account_moves_its_shows_to_the_owner(env):
    c = env["client"]
    other, user = second_client(env)
    s = make_show(other, env["show"], code="LUCA")
    assert c.delete(f"/api/users/{user['id']}").status_code == 200
    assert c.get(f"/api/shows/{s['id']}").json()["owner_id"] == c.get("/api/auth/me").json()["user"]["id"]


# ---------------- one account, its own shows ----------------
def test_each_account_sees_only_its_own_shows(env):
    c = env["client"]
    mine = make_show(c, env["show"], code="MINE")
    other, _ = second_client(env)
    theirs = make_show(other, env["show"], code="THEIRS", name="Theirs")

    assert [s["code"] for s in other.get("/api/shows").json()] == ["THEIRS"]
    assert sorted(s["code"] for s in c.get("/api/shows").json()) == ["MINE", "THEIRS"]      # the owner sees both

    assert other.get(f"/api/shows/{mine['id']}").status_code == 403
    assert other.patch(f"/api/shows/{mine['id']}", json={"name": "hijacked"}).status_code == 403
    assert other.delete(f"/api/shows/{mine['id']}").status_code == 403
    assert other.get(f"/api/shows/{mine['id']}/episodes").status_code == 403
    assert c.get(f"/api/shows/{theirs['id']}").status_code == 200                           # the owner may open it


def test_episodes_jobs_and_markers_follow_their_show(env):
    c = env["client"]
    mine = make_show(c, env["show"], code="MINE")
    ep = c.post(f"/api/shows/{mine['id']}/episodes", json={"number": 17}).json()
    other, _ = second_client(env)
    for url in (f"/api/episodes/{ep['id']}", f"/api/episodes/{ep['id']}/rows", f"/api/episodes/{ep['id']}/deliverables",
                f"/api/episodes/{ep['id']}/timeline", f"/api/episodes/{ep['id']}/markers",
                f"/api/episodes/{ep['id']}/uploads", f"/api/episodes/{ep['id']}/proxy"):
        assert other.get(url).status_code == 403, url
    assert other.post(f"/api/episodes/{ep['id']}/process").status_code == 403
    assert other.delete(f"/api/episodes/{ep['id']}").status_code == 403
    m = c.post(f"/api/episodes/{ep['id']}/markers", json={"t_ms": 1000, "text": "QC"}).json()
    assert other.patch(f"/api/markers/{m['id']}", json={"resolved": 1}).status_code == 403
    assert other.delete(f"/api/markers/{m['id']}").status_code == 403


def test_two_accounts_cannot_hold_the_same_show_code(env):
    c = env["client"]
    make_show(c, env["show"], code="WBM")
    other, _ = second_client(env)
    r = other.post("/api/shows", json={"name": "Also WBM", "code": "WBM", "folder": str(env["show"]), "preset": "WBM"})
    assert r.status_code == 409 and "another account" in r.json()["detail"]


def test_render_queue_is_per_account(env):
    c = env["client"]
    mine = make_show(c, env["show"], code="MINE")
    ep = c.post(f"/api/shows/{mine['id']}/episodes", json={"number": 17}).json()
    import dubstudio.main as main
    main.db().insert("renders", episode_id=ep["id"], version=1, status="done", created=time.time())
    other, _ = second_client(env)
    assert other.get("/api/renders").json() == []
    assert len(c.get("/api/renders").json()) == 1


def test_live_events_only_reach_the_owner_of_the_episode(env):
    c = env["client"]
    mine = make_show(c, env["show"], code="MINE")
    ep = c.post(f"/api/shows/{mine['id']}/episodes", json={"number": 17}).json()
    other, _ = second_client(env)
    import dubstudio.main as main
    from dubstudio.events import bus
    with other.websocket_connect("/api/ws") as ws:
        bus.publish("episode_status", ep["id"], status="processing")
        bus.publish("hello", None, store=False, text="for everyone")
        assert ws.receive_json()["kind"] == "hello"              # the other account's episode is skipped
    main.db().ex("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username='luca')")
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with other.websocket_connect("/api/ws") as ws:
            ws.receive_json()


def test_sign_in_can_be_switched_off_for_a_single_user_mac(env, monkeypatch):
    """AUTH_REQUIRED=false: the app works as before (one editor, no login), as the owner's own account."""
    c = env["client"]
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    c.post("/api/auth/logout")
    assert c.get("/api/shows").status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["required"] is False and me["user"]["role"] == "admin"
