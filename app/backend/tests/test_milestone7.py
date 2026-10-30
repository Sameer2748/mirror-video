"""Milestone 7: blur flag, settings (AI key never returned), health, backups, a step killed by a signal is retried."""
import json, sqlite3
from pathlib import Path
from conftest import wait_for, press_render
from test_m1 import make_show
from test_editor import write_manifest
from dubstudio import timeline as tlm


def test_blur_flag(tmp_path):
    w = tmp_path / "w"; write_manifest(w, [(0, 2000, 0, 2000), (2000, 4000, 10000, 12000)], 4000)
    # 2 samples a second: 0-5 s sharp (100), 10-12 s soft (15)
    vals = [100.0] * 10 + [100.0] * 10 + [15.0] * 6
    (w / tlm.SHARPNESS).write_text(json.dumps({"fps": 2, "values": vals}))
    fl = tlm.flags(tlm.build_v1(w), tlm.context(w), {})
    assert not any(f["kind"] == "blur" for f in fl["c0"])
    b = [f for f in fl["c1"] if f["kind"] == "blur"]
    assert b and b[0]["sev"] == "yellow" and "15%" in b[0]["msg"]
    assert not any(f["kind"] == "blur" for f in tlm.flags(tlm.build_v1(w), tlm.context(w), {"blur": 0.1})["c1"])


def test_settings_never_return_the_key(env, tmp_path, monkeypatch):
    from dubstudio import ai, settings as stg
    monkeypatch.setattr(stg, "ENV", tmp_path / ".env"); monkeypatch.setattr(stg, "CONFIG", tmp_path / "config.json")
    monkeypatch.setattr(ai, "ROOT", tmp_path)
    for k in ("AI_BASE", "AI_KEY", "AI_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / ".env").write_text("OTHER=1\nAI_BASE=https://old\n")
    c = env["client"]
    r = c.put("/api/settings/ai", json={"base": "https://gw.example", "email": "a@b.c", "key": "sk-SECRET123", "model": "m1"})
    assert r.status_code == 200 and "SECRET" not in r.text and r.json()["key_set"] is True
    txt = (tmp_path / ".env").read_text()
    assert "OTHER=1" in txt and "AI_KEY=sk-SECRET123" in txt and "https://old" not in txt
    assert oct((tmp_path / ".env").stat().st_mode)[-3:] == "600"
    g = c.get("/api/settings")
    assert "SECRET" not in g.text and g.json()["ai"]["key_set"] and g.json()["ai"]["base"] == "https://gw.example"
    c.put("/api/settings/ai", json={"base": "https://gw.example", "email": "a@b.c", "key": "", "model": "m2"})
    assert "AI_KEY=sk-SECRET123" in (tmp_path / ".env").read_text()                      # empty key = keep it
    assert c.put("/api/settings/ai", json={"remove_key": True}).json()["key_set"] is False
    assert c.put("/api/settings/ai", json={"base": "gw.example"}).status_code == 400
    assert {h["name"] for h in g.json()["health"]} >= {"Pipeline scripts", "ffmpeg", "Free disk space", "AI gateway"}
    # app settings: checked, saved in config.json
    assert c.put("/api/settings/app", json={"cpu_slots": 3}).json()["saved"]["cpu_slots"] == 3
    assert c.put("/api/settings/app", json={"cpu_slots": 30}).status_code == 400
    assert c.put("/api/settings/app", json={"tools_dir": "/no/such/folder"}).status_code == 400
    assert json.loads((tmp_path / "config.json").read_text()) == {"cpu_slots": 3}
    assert c.post("/api/restart").status_code == 409                                  # not under the launcher


def test_daily_backup(tmp_path):
    from dubstudio import settings as stg
    con = sqlite3.connect(str(tmp_path / "x.db")); con.execute("CREATE TABLE t (a)"); con.execute("INSERT INTO t VALUES (1)"); con.commit()
    p = stg.daily_backup(con, tmp_path)
    assert p.exists() and sqlite3.connect(str(p)).execute("SELECT a FROM t").fetchone() == (1,)
    assert stg.latest_backup(tmp_path) == p


KILLED_VERIFY = r'''
elif cmd == "verify":
    flag = W / "verify_killed_once"
    if not flag.exists():
        flag.write_text("1"); print("about to be killed", flush=True); os.kill(os.getpid(), 9)
    print("verified")
'''


def test_step_killed_by_a_signal_is_retried_once(env):
    tools = env["tmp"] / "tools" / "scripts" / "dubstitch.py"
    src = tools.read_text(); i = src.index('elif cmd == "subs":')
    tools.write_text(src[:i] + KILLED_VERIFY.strip("\n") + "\n" + src[i:])
    c = env["client"]; s = make_show(c, env["show"])
    eid = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()["id"]
    c.post(f"/api/episodes/{eid}/process")
    wait_for(lambda: any(j["step"] == "decisions" and j["status"] == "needs_input" for j in c.get(f"/api/episodes/{eid}").json()["jobs"]))
    c.post(f"/api/episodes/{eid}/decisions/auto")
    press_render(c, eid)
    ep = wait_for(lambda: (lambda x: x if x["status"] in ("done", "failed") else None)(c.get(f"/api/episodes/{eid}").json()))
    assert ep["status"] == "done"
    v = next(j for j in ep["jobs"] if j["step"] == "verify")
    assert v["attempt"] == 1 and v["status"] == "done"
    assert any("trying again once" in ln for ln in c.get(f"/api/jobs/{v['id']}/log").json()["lines"])
    sh = next(j for j in ep["jobs"] if j["step"] == "sharpness")                    # the blur measure ran too
    assert sh["status"] == "done" and (Path(ep["work_dir"]) / tlm.SHARPNESS).exists()


def test_old_work_folder_is_moved_aside_on_a_new_episode(env):
    """A work folder left by a deleted episode is moved aside (not deleted), so every step really runs again."""
    from dubstudio.config import cfg
    c = env["client"]; s = make_show(c, env["show"])
    old = cfg.work_dir(s["code"], 17); (old / "lips_range").mkdir(parents=True); (old / "prep.json").write_text("{}")
    e = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    assert Path(e["work_dir"]) == old and not (old / "prep.json").exists()
    kept = [p for p in old.parent.iterdir() if p.name.startswith(old.name + "_old_")]
    assert len(kept) == 1 and (kept[0] / "prep.json").exists()
