"""The editor's timeline: built from manifest_groups.json, flags, checks, versions, render and render again."""
import json
from pathlib import Path
from conftest import wait_for
from test_m1 import make_show
from dubstudio import timeline as tlm


def write_manifest(w, segs, total):
    w.mkdir(parents=True, exist_ok=True)
    (w / "manifest_groups.json").write_text(json.dumps(dict(total_ms=total, segments=[
        dict(kind="clip", it_idx=k, it_start_ms=a, it_end_ms=b, us_start_ms=c, us_end_ms=d, it_text=f"riga {k}",
             us_text=f"line {k}", stretch_factor=1.0, note="") for k, (a, b, c, d) in enumerate(segs)], groups=[])))


def test_flags_and_checks(tmp_path):
    w = tmp_path / "w"
    # 0: real speed; 1: x0.2 (very slow); 2: x1.8 (fast); 3: a flash; 4: shows clip 0's footage again
    write_manifest(w, [(0, 2000, 10000, 12000), (2000, 5000, 20000, 20600), (5000, 6000, 30000, 31800),
                       (6000, 6300, 40000, 40300), (6300, 8000, 10200, 11900)], 8000)
    tl = tlm.build_v1(w)
    assert [c["id"] for c in tl["clips"]] == ["c0", "c1", "c2", "c3", "c4"] and tlm.validate(tl) == []
    fl = tlm.flags(tl, tlm.context(w), {})
    kinds = {cid: sorted((f["kind"], f["sev"]) for f in v) for cid, v in fl.items()}
    assert ("slow", "red") in kinds["c1"] and ("fast", "yellow") in kinds["c2"] and ("short", "yellow") in kinds["c3"]
    assert ("repeat", "red") in kinds["c0"] and ("repeat", "red") in kinds["c4"]
    assert tlm.open_counts(fl) == {"red": 3, "yellow": 2}
    tl["approved"] = ["c1"]
    assert tlm.open_counts(tlm.flags(tl, tlm.context(w), {})) == {"red": 2, "yellow": 2}
    # holes, overlaps and footage outside the video are refused
    bad = json.loads(json.dumps(tl)); bad["clips"][1]["t1"] = 1900
    assert any("overlap" in p or "gap" in p for p in tlm.validate(bad))
    bad = json.loads(json.dumps(tl)); bad["clips"][2]["s1"] = 99_000
    assert any("outside" in p for p in tlm.validate(bad, src_duration_ms=50_000))
    hair = json.loads(json.dumps(tl)); hair["clips"][2]["s1"] = 50_020   # 20 ms past the end: one frame, not a miss
    assert not any("outside" in p for p in tlm.validate(hair, src_duration_ms=50_000))


def test_write_manifest_keeps_the_pipeline_copy(tmp_path):
    w = tmp_path / "w"; write_manifest(w, [(0, 1000, 0, 1000), (1000, 2000, 5000, 6000)], 2000)
    tl = tlm.build_v1(w); base = tl["base"]
    tl["clips"][1]["s0"], tl["clips"][1]["s1"] = 7000, 7500
    m = tlm.write_manifest(w, tl, 3)
    assert m["dub_studio"]["version"] == 3 and m["segments"][1]["us_start_ms"] == 7000
    assert m["segments"][1]["stretch_factor"] == 2.0                    # slot 1000 / footage 500
    assert (w / tlm.PIPELINE_COPY).exists()
    assert tlm.build_v1(w)["base"] == base                              # v1 still comes from the pipeline's list
    tlm.write_manifest(w, tl, 4)                                        # a second write keeps the first copy
    assert json.loads((w / tlm.PIPELINE_COPY).read_text()).get("dub_studio") is None


def test_review_gate_render_and_render_again(env):
    c = env["client"]; s = make_show(c, env["show"])
    e = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json(); eid = e["id"]
    c.post(f"/api/episodes/{eid}/process")
    wait_for(lambda: any(j["step"] == "decisions" and j["status"] == "needs_input" for j in c.get(f"/api/episodes/{eid}").json()["jobs"]))
    c.post(f"/api/episodes/{eid}/decisions/auto")
    ep = wait_for(lambda: (lambda x: x if x["status"] == "review" else None)(c.get(f"/api/episodes/{eid}").json()))
    assert next(j for j in ep["jobs"] if j["step"] == "render")["status"] == "waiting"
    d = c.get(f"/api/episodes/{eid}/timeline").json()
    tl = d["timeline"]
    assert len(tl["clips"]) == 3 and d["subs"]["en"][0]["text"] == "Hi" and d["state"]["review"] == "needs_input"
    # an edit: clip 1 shows other footage; autosave draft, then a version
    tl["clips"][1]["s0"], tl["clips"][1]["s1"] = 950, 1750
    assert c.put(f"/api/episodes/{eid}/timeline", json={**tl, "draft": True}).json()["draft"] is True
    assert c.get(f"/api/episodes/{eid}/timeline").json()["timeline"]["draft"] is True
    assert c.put(f"/api/episodes/{eid}/timeline", json=tl).json()["version"] == 1
    # a hole is refused
    bad = json.loads(json.dumps(tl)); bad["clips"][2]["t0"] = 2100
    assert c.post(f"/api/episodes/{eid}/timeline/render", json=bad).status_code == 400
    # render: the gate opens, the editor's list is what render reads
    r = c.post(f"/api/episodes/{eid}/timeline/render", json=tl).json()
    assert r["version"] == 2 and r["steps"] == ["review"] and r["render"]
    ep = wait_for(lambda: (lambda x: x if x["status"] == "done" else None)(c.get(f"/api/episodes/{eid}").json()))
    w = Path(ep["work_dir"])
    m = json.loads((w / "manifest_groups.json").read_text())
    assert m["dub_studio"]["version"] == 2 and m["segments"][1]["us_start_ms"] == 950
    render = next(j for j in ep["jobs"] if j["step"] == "render")
    assert any('"version": 2' in ln for ln in c.get(f"/api/jobs/{render['id']}/log").json()["lines"])
    # edit again after the render: only render and what follows run again
    tl2 = c.get(f"/api/episodes/{eid}/timeline").json()["timeline"]
    assert tl2["version"] == 2
    tl2["clips"][0]["s1"] = 800
    r = c.post(f"/api/episodes/{eid}/timeline/render", json=tl2).json()
    assert r["version"] == 3 and r["steps"][:2] == ["review", "render"] and "check" in r["steps"] and "analyze" not in r["steps"]
    ep = wait_for(lambda: (lambda x: x if x["status"] == "done" else None)(c.get(f"/api/episodes/{eid}").json()))
    assert json.loads((w / "manifest_groups.json").read_text())["dub_studio"]["version"] == 3
    assert [v["version"] for v in c.get(f"/api/episodes/{eid}/timeline").json()["versions"]] == [3, 2, 1]
    assert c.get(f"/api/episodes/{eid}/timeline?pipeline=1").json()["timeline"]["clips"][1]["s0"] == 900


def test_unedited_v1_writes_the_pipeline_times_exactly(tmp_path):
    w = tmp_path / "w"; w.mkdir()
    segs = [dict(kind="clip", it_idx=0, it_start_ms=0.0, it_end_ms=1234.567, us_start_ms=5000.123, us_end_ms=6260.617,
                 it_text="a", us_text="b", stretch_factor=1.0, note=""),
            dict(kind="clip", it_idx=1, it_start_ms=1234.567, it_end_ms=2000.0, us_start_ms=7000.0, us_end_ms=7765.433,
                 it_text="c", us_text="d", stretch_factor=1.0, note="")]
    (w / "manifest_groups.json").write_text(json.dumps(dict(total_ms=2000.0, segments=segs, groups=[])))
    out = tlm.to_manifest(tlm.build_v1(w), w, 1)
    key = lambda s: (s["it_start_ms"], s["it_end_ms"], f"{s['us_start_ms']:.3f}|{s['us_end_ms']:.3f}")
    assert [key(s) for s in out["segments"]] == [key(s) for s in segs]


def test_software_trims_are_marked_for_the_editor(tmp_path):
    """Owner, 2026-10-26: a clip the software trimmed (fast-clip rule, over x1.25) is marked "trimmed" (yellow) so the
    editor checks it; approving it = checked; a clip the editor changed is no longer marked."""
    w = tmp_path / "w"
    write_manifest(w, [(0, 2000, 10000, 12000), (2000, 4000, 20000, 22400), (4000, 6000, 30000, 32000)], 6000)
    (w / "fastfix.json").write_text(json.dumps(dict(threshold=1.25, target=1.2, clips=[
        dict(row=1, kind="lip group piece", speed_before=1.6, speed_after=1.2, cut_s=0.8, by="Claude")],
        group_trims=[dict(rows=[2], it0=4000, it1=6000, us0=30000, us1=33100, speed_before=1.55, speed_after=1.0, cut_s=1.1,
                          trims=[dict(row=2, ms=1100, cls="narration", fast=True, text="line 2")])])))
    tl = tlm.build_v1(w)
    fl = tlm.flags(tl, tlm.context(w), {})
    tr = {cid: [f for f in v if f["kind"] == "trimmed"] for cid, v in fl.items()}
    assert not tr["c0"] and tr["c1"][0]["sev"] == "yellow" and tr["c2"][0]["sev"] == "yellow"
    assert "Claude chose the cut" in tr["c1"][0]["msg"] and "1.10 s cut from this line" in tr["c2"][0]["msg"]
    tl["approved"] = ["c1"]
    assert [f for f in tlm.flags(tl, tlm.context(w), {})["c1"] if f["kind"] == "trimmed"][0]["approved"]
    tl["clips"][2]["s0"] += 200                                  # the editor changed it: his cut now
    assert not [f for f in tlm.flags(tl, tlm.context(w), {})["c2"] if f["kind"] == "trimmed"]


def test_a_broll_repeat_is_a_note_not_a_blocker(tmp_path):
    """The scene edit shows a B-roll shot again on purpose (zoomed, at most 3 times). That must not be a red flag:
    red flags stop the render, and the editor would have to approve 50 of them on every episode."""
    from dubstudio import timeline as tlm
    W = tmp_path / "w"; W.mkdir()
    (W / "manifest_groups.json").write_text(json.dumps({"total_ms": 20000, "segments": []}))
    clip = lambda i, t0, s0, **kw: dict(id=f"c{i}", row=i, t0=t0, t1=t0 + 4000, s0=s0, s1=s0 + 4000, **kw)
    ctx = tlm.context(str(W))
    plain = {"clips": [clip(0, 0, 1000), clip(1, 4000, 1000)], "v2": [], "tracks": {}, "approved": [],
             "total_ms": 20000, "holes": []}
    red = tlm.open_counts(tlm.flags(plain, ctx, None, []))
    assert red["red"] >= 1                                   # two normal clips on the same footage: fix it

    scene = {**plain, "clips": [clip(0, 0, 1000), clip(1, 4000, 1000, broll=True, zoom=1.08)]}
    after = tlm.flags(scene, ctx, None, [])
    assert tlm.open_counts(after)["red"] == 0                 # the same thing, planned by the scene edit: a note
    msgs = [f["msg"] for fs in after.values() for f in fs if f["kind"] == "repeat"]
    assert msgs and all(f["sev"] == "yellow" for fs in after.values() for f in fs if f["kind"] == "repeat")
    assert "B-roll" in msgs[0]
