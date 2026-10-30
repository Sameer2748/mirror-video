"""Editor v2: QC markers (red until resolved), subtitle editing that reaches the render, the 540-px preview copy,
locked clips kept through versions."""
import json
from pathlib import Path
from conftest import wait_for
from test_m1 import make_show
from test_editor import write_manifest
from dubstudio import timeline as tlm


def test_markers_are_red_until_resolved(tmp_path):
    w = tmp_path / "w"; write_manifest(w, [(0, 2000, 10000, 12000), (2000, 5000, 20000, 23000)], 5000)
    tl = tlm.build_v1(w)
    assert tlm.parse_marker("06:04 Viola smiling") == (364000, "Viola smiling")
    assert tlm.parse_marker("no time here") == (None, "no time here")
    mk = [dict(id=7, t_ms=2500, text="Viola smiling", resolved=0)]
    tl["approved"] = ["c1"]                                   # approving the clip does not close a QC note
    fl = tlm.flags(tl, tlm.context(w), {}, mk)
    assert fl["c1"][0]["kind"] == "qc" and fl["c1"][0]["marker"] == 7 and not fl["c1"][0].get("approved")
    assert tlm.open_counts(fl)["red"] == 1
    mk[0]["resolved"] = 1
    assert tlm.open_counts(tlm.flags(tl, tlm.context(w), {}, mk))["red"] == 0


def test_cue_checks():
    cues, probs = tlm.check_cues([{"t0": 1000, "t1": 2000, "text": " b "}, {"t0": 0, "t1": 900, "text": "a\n  "}])
    assert probs == [] and [c["text"] for c in cues] == ["a", "b"]
    assert tlm.srt_text(cues).startswith("1\n00:00:00,000 --> 00:00:00,900\na\n\n2\n00:00:01,000")
    _, probs = tlm.check_cues([{"t0": 0, "t1": 1500, "text": "a"}, {"t0": 1000, "t1": 2000, "text": "b"}])
    assert any("overlap" in p for p in probs)


def test_subs_markers_proxy_and_lock_through_render(env):
    c = env["client"]; s = make_show(c, env["show"])
    eid = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()["id"]
    c.post(f"/api/episodes/{eid}/process")
    wait_for(lambda: any(j["step"] == "decisions" and j["status"] == "needs_input" for j in c.get(f"/api/episodes/{eid}").json()["jobs"]))
    c.post(f"/api/episodes/{eid}/decisions/auto")
    ep = wait_for(lambda: (lambda x: x if x["status"] == "review" else None)(c.get(f"/api/episodes/{eid}").json()))
    w = Path(ep["work_dir"]); out = Path(ep["output_dir"])

    # the preview copy was made by the run, served, and used for thumbnails
    assert next(j for j in ep["jobs"] if j["step"] == "proxy")["status"] == "done"
    assert (w / tlm.PROXY).exists() and c.get(f"/api/episodes/{eid}/proxy").json()["ready"] is True
    assert c.get(f"/api/episodes/{eid}/media/src_proxy").status_code == 200

    d = c.get(f"/api/episodes/{eid}/timeline").json()
    assert d["proxy"]["ready"] and d["subs_edited"] == {"dub": False, "en": False} and d["markers"] == []
    tl = d["timeline"]

    # a QC note typed with its time is a red flag that blocks render until resolved
    m = c.post(f"/api/episodes/{eid}/markers", json={"text": "00:01.5 Viola smiling"}).json()
    assert m["t_ms"] == 1500 and m["text"] == "Viola smiling"
    assert c.post(f"/api/episodes/{eid}/markers", json={"text": "no time"}).status_code == 400
    assert c.post(f"/api/episodes/{eid}/markers", json={"text": "at the playhead", "t_ms": 2500}).json()["t_ms"] == 2500
    chk = c.post(f"/api/episodes/{eid}/timeline/check", json=tl).json()
    assert chk["counts"]["red"] == 2 and chk["flags"]["c1"][0]["kind"] == "qc"
    assert c.post(f"/api/episodes/{eid}/timeline/render", json=tl).status_code == 409
    for mk in c.get(f"/api/episodes/{eid}/markers").json():
        assert c.patch(f"/api/markers/{mk['id']}", json={"resolved": True}).json()["resolved"] == 1
    assert c.post(f"/api/episodes/{eid}/timeline/check", json=tl).json()["counts"]["red"] == 0

    # subtitles: S1 split in two, S2 reworded; bad tracks refused
    dub = d["subs"]["dub"]; en = d["subs"]["en"]
    assert dub and en
    assert c.put(f"/api/episodes/{eid}/subs/dub", json={"cues": [{"t0": 0, "t1": 900, "text": "x"}, {"t0": 800, "t1": 1500, "text": "y"}]}).status_code == 400
    new_dub = [{"t0": 0, "t1": 500, "text": "Ciao"}, {"t0": 500, "t1": 1000, "text": "a tutti"}]
    assert c.put(f"/api/episodes/{eid}/subs/dub", json={"cues": new_dub}).json()["saved"] == 2
    assert c.put(f"/api/episodes/{eid}/subs/en", json={"cues": [{"t0": 0, "t1": 1000, "text": "Hello everyone"}]}).status_code == 200
    d2 = c.get(f"/api/episodes/{eid}/timeline").json()
    assert d2["subs_edited"] == {"dub": True, "en": True} and [x["text"] for x in d2["subs"]["dub"]] == ["Ciao", "a tutti"]
    assert "a tutti" in (w / "subs_edited_dub.srt").read_text()
    inputs = lambda: sorted(p.read_text() for p in Path(env["show"]).rglob("*.srt") if out not in p.parents)
    show_srts = inputs()

    # lock: kept on the clip through a saved version
    tl["clips"][0]["locked"] = True; tl["approved"] = ["c0"]
    v = c.put(f"/api/episodes/{eid}/timeline", json=tl).json()["version"]
    assert c.get(f"/api/episodes/{eid}/timeline/versions/{v}").json()["clips"][0]["locked"] is True

    # render: the subtitle videos are made from the edited tracks; the delivered English SRT follows S2
    assert c.post(f"/api/episodes/{eid}/timeline/render", json=tl).status_code == 200
    ep = wait_for(lambda: (lambda x: x if x["status"] == "done" else None)(c.get(f"/api/episodes/{eid}").json()))
    cmd = lambda step: next(l for l in c.get(f"/api/jobs/{next(j for j in ep['jobs'] if j['step'] == step)['id']}/log").json()["lines"] if l.startswith("$"))
    assert str(w / "subs_edited_dub.srt") in cmd("subs_dub") and str(w / "subs_edited_en.srt") in cmd("subs_en")
    en_out = next(out.glob("*english*.srt"))
    assert "Hello everyone" in en_out.read_text() and (w / "english_translation_original.srt").exists()
    assert inputs() == show_srts                                     # the show folder's inputs are untouched

    # back to the pipeline's subtitles
    r = c.delete(f"/api/episodes/{eid}/subs/en").json()
    assert r["subs_edited"]["en"] is False and r["subs"]["en"][0]["text"] == "Hi"
    assert not (w / "subs_edited_en.srt").exists()


def test_show_flag_limits(env):
    c = env["client"]; s = make_show(c, env["show"])
    r = c.patch(f"/api/shows/{s['id']}", json={"profile": {"flags": {"slow": 0.8, "slow_red": "0.6"}}})
    assert r.status_code == 200 and r.json()["profile"]["flags"]["slow"] == 0.8 and r.json()["profile"]["flags"]["slow_red"] == 0.6
    assert r.json()["profile"]["flags"]["fast_red"] == 2.0                       # the others are kept
    assert c.patch(f"/api/shows/{s['id']}", json={"profile": {"flags": {"slow_red": 0.9}}}).status_code == 400
    assert c.patch(f"/api/shows/{s['id']}", json={"profile": {"flags": {"fast": "x"}}}).status_code == 400
    assert c.get(f"/api/shows/{s['id']}").json()["profile"]["flags"]["slow"] == 0.8


def test_limits_change_the_colours(tmp_path):
    w = tmp_path / "w"; write_manifest(w, [(0, 1000, 0, 750)], 1000)       # plays ×0.75
    tl = tlm.build_v1(w)
    assert tlm.open_counts(tlm.flags(tl, tlm.context(w), {})) == {"red": 0, "yellow": 0}
    assert tlm.open_counts(tlm.flags(tl, tlm.context(w), {"slow": 0.8, "slow_red": 0.6})) == {"red": 0, "yellow": 1}
