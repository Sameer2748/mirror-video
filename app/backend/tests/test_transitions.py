"""Transitions per cut: the editor's choices -> transitions_plan.json -> the pipeline's `transitions --plan`."""
import json
from pathlib import Path
from conftest import wait_for
from test_m1 import make_show
from test_editor import write_manifest
from dubstudio import timeline as tlm


def test_plan_file_and_cuts(tmp_path):
    w = tmp_path / "w"
    # c1 jumps in the source (a cut), c2 continues c1's footage (no cut)
    write_manifest(w, [(0, 1000, 0, 1000), (1000, 2000, 5000, 6000), (2000, 3000, 6000, 7000)], 3000)
    tl = tlm.build_v1(w)
    assert tlm.cuts(tl) == ["c1"]
    assert tlm.write_transition_plan(w, tl, "none") is None and not (w / tlm.TPLAN).exists()
    tl["clips"][1]["tin"] = "light_leak"; tl["clips"][2]["tin"] = "glow"            # c2 is no cut: ignored
    tlm.write_transition_plan(w, tl, "none")
    assert json.loads((w / tlm.TPLAN).read_text()) == {"default": "none", "cuts": {"1000": "light_leak"}}
    tl["clips"][1]["tin"] = "none"
    assert tlm.write_transition_plan(w, tl, "none") is None and not (w / tlm.TPLAN).exists()   # back to no step
    tlm.write_transition_plan(w, tl, "auto")                                          # TOLR: a hard cut is a choice
    assert json.loads((w / tlm.TPLAN).read_text())["cuts"] == {"1000": "none"}
    use = tlm.transition_use([{"clips": [{"tin": "glow"}, {"tin": "glow"}, {"tin": "auto"}]}],
                             [{"transitions": {"plan": [{"style": "zoom_blur"}]}}])
    assert use == {"light_leak": 0, "blur_dissolve": 0, "zoom_blur": 1, "glow": 2}


def test_scene_plan_then_the_editors_own_picks(env):
    """Every episode (scene edit): trans_plan makes the plan (a transition at every switch except INTO a B-roll), then
    the transitions the editor picked on cuts win; without picks the plan is exactly the pipeline's."""
    c = env["client"]; s = make_show(c, env["show"])
    eid = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()["id"]
    c.post(f"/api/episodes/{eid}/process")
    wait_for(lambda: any(j["step"] == "decisions" and j["status"] == "needs_input" for j in c.get(f"/api/episodes/{eid}").json()["jobs"]))
    c.post(f"/api/episodes/{eid}/decisions/auto")
    wait_for(lambda: c.get(f"/api/episodes/{eid}").json()["status"] == "review")
    tl = c.get(f"/api/episodes/{eid}/timeline").json()["timeline"]
    assert tl["clips"][-1].get("broll") is True and tl["clips"][-1].get("zoom") == 1.08    # the scene edit's marks reach the editor
    tl["clips"][1]["s0"], tl["clips"][1]["s1"] = 2000, 2900   # the editor gives clip 2 other footage: a cut ...
    tl["clips"][1]["tin"] = "glow"                             # ... with a transition of his own
    assert c.post(f"/api/episodes/{eid}/timeline/render", json={**tl, "force": True}).status_code == 200
    ep = wait_for(lambda: (lambda x: x if x["status"] == "done" else None)(c.get(f"/api/episodes/{eid}").json()))
    jobs = {j["step"]: j for j in ep["jobs"]}
    assert jobs["trans_plan"]["status"] == "done" and "1 cuts kept" in jobs["trans_plan"]["message"]
    lines = c.get(f"/api/jobs/{jobs['transitions']['id']}/log").json()["lines"]
    assert any("--plan" in l and "transitions_plan.json" in l for l in lines)
    w = Path(ep["work_dir"]); plan_ = json.loads((w / tlm.TPLAN).read_text())["cuts"]
    assert plan_["1000"] == "glow"                             # the editor's pick won
    m = json.loads((w / "manifest_groups.json").read_text())["segments"]
    assert m[-1].get("broll") is True and m[-1].get("zoom") == 1.08    # still B-roll + zoomed in what render reads
    assert plan_[str(round(m[-1]["it_start_ms"]))] == "none"   # straight cut into the B-roll


def test_old_run_never_gains_a_lip_scan(env):
    """An episode processed before the lip scan was always on keeps its steps when Render adds transitions."""
    c = env["client"]; s = make_show(c, env["show"])
    eid = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()["id"]
    c.post(f"/api/episodes/{eid}/process")
    wait_for(lambda: any(j["step"] == "decisions" and j["status"] == "needs_input" for j in c.get(f"/api/episodes/{eid}").json()["jobs"]))
    import dubstudio.main as main
    db = main.db()
    db.ex("DELETE FROM jobs WHERE episode_id=? AND step IN ('lip_gpu','lip_mesh','lip_merge','lipsync')", (eid,))   # an old run
    db.ex("UPDATE jobs SET needs=? WHERE episode_id=? AND step='groups'", ('["manifest"]', eid))
    main.runner().ensure_steps(eid)
    jobs = {j["step"]: j for j in db.q("SELECT step, needs FROM jobs WHERE episode_id=?", (eid,))}
    assert not any(k.startswith("lip") for k in jobs) and "lipsync" not in jobs["groups"]["needs"]
    assert jobs["groups"]["needs"] == ["manifest"]
