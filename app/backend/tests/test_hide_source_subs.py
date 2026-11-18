"""A show ticked for burned-in source subtitles zooms every episode at Render.
An editor can turn the same zoom on for one episode of a show that was not ticked.
"""
import json
from pathlib import Path

from dubstudio.pipeline.steps import plan, render_subs_mode
from dubstudio import presets


ASSETS = {"src_video": "/v.mp4", "dub_audio": "/a.wav", "dub_script": "/s.txt"}


def _subs(show, ep):
    cmd = next(s["cmd"] for s in plan(show, ep, ASSETS)[1] if s["step"] == "render")
    return cmd[cmd.index("--subs") + 1]


def test_the_show_tick_zooms_every_episode_and_a_plain_show_does_not(tmp_path):
    ep = {"number": 3, "work_dir": str(tmp_path), "output_dir": str(tmp_path)}
    plain = {"code": "ZZ", "profile": presets.preset("NEW")}
    ticked = {"code": "ZZ", "profile": {**presets.preset("NEW"), "hide_source_subs": True}}
    assert _subs(plain, ep) == "keep"
    assert _subs(ticked, ep) == "zoom"
    assert render_subs_mode(plain["profile"], tmp_path) == "keep"
    assert render_subs_mode({"render_subs": "crop"}, tmp_path) == "crop"


def test_one_episode_can_opt_in_without_the_show_tick(tmp_path):
    (tmp_path / "hide_source_subs.json").write_text(json.dumps({"on": True}), encoding="utf-8")
    ep = {"number": 3, "work_dir": str(tmp_path), "output_dir": str(tmp_path)}
    show = {"code": "ZZ", "profile": presets.preset("NEW")}
    assert _subs(show, ep) == "zoom"
    (tmp_path / "hide_source_subs.json").write_text(json.dumps({"on": False}), encoding="utf-8")
    assert _subs(show, ep) == "keep"


def test_creating_a_show_saves_the_tick_and_the_editor_can_set_one_episode(env):
    c = env["client"]
    made = c.post("/api/shows", json={
        "name": "Burned subs", "code": "SHOW", "folder": str(env["show"]), "preset": "NEW",
        "profile": {"hide_source_subs": True, "dub_lang": "it", "src_lang": "en"},
    })
    assert made.status_code == 200, made.text
    show = made.json()
    assert show["profile"]["hide_source_subs"] is True
    show = c.patch(f"/api/shows/{show['id']}", json={"profile": {"hide_source_subs": False}}).json()
    assert show["profile"]["hide_source_subs"] is False
    ep = c.post(f"/api/shows/{show['id']}/episodes", json={"number": 17}).json()
    on = c.post(f"/api/episodes/{ep['id']}/source-sub-zoom", json={"on": True})
    assert on.status_code == 200 and on.json() == {"on": True, "show": False}
    assert json.loads(Path(ep["work_dir"], "hide_source_subs.json").read_text())["on"] is True
    assert _subs(show, ep) == "zoom"
    off = c.post(f"/api/episodes/{ep['id']}/source-sub-zoom", json={"on": False})
    assert off.json()["on"] is False
    assert not Path(ep["work_dir"], "hide_source_subs.json").exists()
    assert _subs(show, ep) == "keep"
    # the show tick wins for every episode, including one the editor tried to turn off
    show = c.patch(f"/api/shows/{show['id']}", json={"profile": {"hide_source_subs": True}}).json()
    locked = c.post(f"/api/episodes/{ep['id']}/source-sub-zoom", json={"on": False})
    assert locked.json()["on"] is True and locked.json()["show"] is True
    assert _subs(show, ep) == "zoom"
