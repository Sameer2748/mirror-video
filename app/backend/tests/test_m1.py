import json
from pathlib import Path
from conftest import wait_for, press_render


def make_show(c, show, preset="WBM"):
    r = c.post("/api/shows", json={"name": "Test show", "code": "SHOW", "folder": str(show), "preset": preset})
    assert r.status_code == 200, r.text
    return r.json()


def test_presets_and_show_crud(env):
    c = env["client"]
    p = c.get("/api/presets").json()
    assert {"TDMB", "TOLR", "WBM", "NEW"} <= set(p)
    assert p["WBM"]["ending"] == "auto" and p["WBM"]["lip"] is True and p["TOLR"]["lip"] is True
    from dubstudio.presets import clause_mode
    assert {clause_mode(p[k]) for k in ("WBM", "TDMB", "TOLR", "NEW")} == {(0, 0)}        # whole sentences, every show
    same = lambda k: {(p[x]["group_size"], p[x]["max_drift_ms"], p[x]["ending"]) for x in ("WBM", "TDMB", "TOLR", "NEW")}
    assert same(0) == {(7, 500, "auto")} and p["TDMB"]["sub_style"] == "strip" and p["WBM"]["sub_style"] == "box"
    s = make_show(c, env["show"])
    assert s["profile"]["max_drift_ms"] == 500
    assert c.post("/api/shows", json={"name": "x", "code": "SHOW", "folder": str(env["show"])}).status_code == 409
    assert c.post("/api/shows", json={"name": "x", "code": "OTHER", "folder": "/nope"}).status_code == 400
    s2 = c.patch(f"/api/shows/{s['id']}", json={"profile": {"group_size": 6, "dub_lang": "IT", "src_lang": "FR"}}).json()
    assert s2["profile"]["group_size"] == 6 and s2["profile"]["clauses"] == 0   # stored; the run uses the fixed values
    assert s2["profile"]["dub_lang"] == "it" and s2["profile"]["src_lang"] == "fr"
    assert len(c.get("/api/shows").json()) == 1


def test_episode_scan_and_check(env):
    c = env["client"]; s = make_show(c, env["show"])
    e = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    kinds = {a["kind"]: a["path"] for a in e["assets"]}
    assert kinds["src_video"].endswith("SHOW Ep - 17.mp4")
    assert kinds["dub_audio"].endswith("Episode 17 - Prova.mp3")
    assert kinds["dub_script"].endswith("Episode 17 - Prova.txt")
    assert "src_srt" not in kinds
    chk = c.post(f"/api/episodes/{e['id']}/check").json()
    assert chk["src_video"]["problems"] == [] and abs(chk["src_video"]["info"]["duration"] - 3) < 0.2
    assert chk["src_video"]["info"]["video"]["fps"] == 25
    assert c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).status_code == 409


def test_full_run_with_gate_resume_and_outputs(env):
    c = env["client"]; s = make_show(c, env["show"])
    e = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    r = c.post(f"/api/episodes/{e['id']}/process").json()
    steps = [j["step"] for j in r["jobs"]]
    assert steps[:4] == ["fetch", "ingest", "mksrt_dub", "mksrt_src"] and "lip_gpu" in steps and "fastfix" not in steps
    assert steps.index("groups") < steps.index("library") < steps.index("facemap") < steps.index("scene_edit") \
        < steps.index("use_scene") < steps.index("review")                         # Step 4C, every show (owner, 2026-10-30)
    # the gate waits for decisions
    wait_for(lambda: any(j["step"] == "decisions" and j["status"] == "needs_input"
                         for j in c.get(f"/api/episodes/{e['id']}").json()["jobs"]))
    st = c.get(f"/api/episodes/{e['id']}/decisions").json()
    assert [x["idx"] for x in st["flagged"]] == [1, 2] and st["missing"] == [1, 2]
    assert c.post(f"/api/episodes/{e['id']}/decisions/auto").json() == {"confirmed": 1, "gaps": 1}
    # nothing renders until the editor presses Render
    wait_for(lambda: c.get(f"/api/episodes/{e['id']}").json()["status"] == "review")
    tl = c.get(f"/api/episodes/{e['id']}/timeline").json()["timeline"]
    assert c.post(f"/api/episodes/{e['id']}/timeline/render", json=tl).json()["steps"] == ["review"]
    ep = wait_for(lambda: (lambda x: x if x["status"] == "done" else None)(c.get(f"/api/episodes/{e['id']}").json()))
    assert all(j["status"] in ("done", "skipped") for j in ep["jobs"])   # publish/cleanup: skipped, no cloud in tests
    out = Path(ep["output_dir"])
    assert out == env["show"] / "outputs" / "ep17"
    for f in ("SHOW_EP17_IT_final_clean.mp4", "SHOW_EP17_IT_final_it_subs.mp4", "SHOW_EP17_IT_final_en_subs.mp4",
              "SHOW_EP17_IT_english_google.srt", "show_ep17_it_us_mapping.xlsx"):
        assert (out / f).exists(), f
    subs = next(j for j in ep["jobs"] if j["step"] == "subs_dub")
    assert "2 rounds" in subs["message"]                     # exit 3 -> resumed automatically
    render = next(j for j in ep["jobs"] if j["step"] == "render")
    log = c.get(f"/api/jobs/{render['id']}/log").json()["lines"]
    assert "rendered 4/4 segments" in log
    bookends_cmd = next(l for l in c.get(f"/api/jobs/{next(j for j in ep['jobs'] if j['step']=='bookends')['id']}/log").json()["lines"] if l.startswith("$"))
    assert "--no-ending" not in bookends_cmd and "--clauses" not in bookends_cmd   # ending: kept, stops before a fade
    words_cmd = next(l for l in c.get(f"/api/jobs/{next(j for j in ep['jobs'] if j['step']=='words_dub')['id']}/log").json()["lines"] if l.startswith("$"))
    assert "--clauses 0" in words_cmd and "--clause-long" not in words_cmd
    # every step of the skill's run sheet ran: merged.py, mkdec.py, groups size 7 after the lip scan, run_notes.md
    W = Path(ep["work_dir"]); log = lambda st: c.get(f"/api/jobs/{next(j for j in ep['jobs'] if j['step'] == st)['id']}/log").json()["lines"]
    assert (W / "merged.txt").read_text().count("\nD  ") == 3 and (W / "spec.py").exists()
    assert any("mkdec.py" in l for l in log("decisions")) and any(l.startswith("groups ") for l in log("decisions"))
    assert any("--size 7" in l for l in log("groups") if l.startswith("$"))
    notes = (out / "run_notes.md").read_text()
    assert "## Lip scan and groups" in notes and "lip_gpu: done" in notes
    assert c.get("/api/shows").json()[0]["episode_counts"] == {"done": 1}


def test_spec_import_and_cancel_retry(env):
    c = env["client"]; s = make_show(c, env["show"])
    e = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    c.post(f"/api/episodes/{e['id']}/process")
    wait_for(lambda: any(j["step"] == "decisions" and j["status"] == "needs_input"
                         for j in c.get(f"/api/episodes/{e['id']}").json()["jobs"]))
    bad = c.post(f"/api/episodes/{e['id']}/decisions/import",
                 files={"file": ("spec.py", b"A(1,'Wrong',1,'How','x')", "text/plain")})
    assert bad.status_code == 400 and "lacks" in bad.text
    ok = c.post(f"/api/episodes/{e['id']}/decisions/import",
                files={"file": ("spec.py", b"A(1,'Come',1,'How','same')\nGAP(2,'Bene','no line')", "text/plain")})
    assert ok.status_code == 200, ok.text
    wait_for(lambda: any(j["step"] == "decisions" and j["status"] == "done"
                         for j in c.get(f"/api/episodes/{e['id']}").json()["jobs"]))
    c.post(f"/api/episodes/{e['id']}/cancel")
    ep = wait_for(lambda: (lambda x: x if x["status"] in ("cancelled", "done") else None)(c.get(f"/api/episodes/{e['id']}").json()))
    if ep["status"] == "cancelled":
        stuck = next(j for j in ep["jobs"] if j["status"] == "cancelled")
        c.post(f"/api/jobs/{stuck['id']}/retry")
        press_render(c, e["id"])
        try:
            ep = wait_for(lambda: (lambda x: x if x["status"] == "done" else None)(c.get(f"/api/episodes/{e['id']}").json()), 15)
        except AssertionError:
            x = c.get(f"/api/episodes/{e['id']}").json()
            raise AssertionError("not done: " + str([(j["step"], j["status"], j.get("message")) for j in x["jobs"]]))
    assert ep["status"] == "done"


def test_uppercase_langs_become_whisper_codes():
    """A new show often stores IT / FR (labels). Whisper only accepts it / fr."""
    from dubstudio.pipeline.steps import plan
    from dubstudio import presets
    assert presets.asr_lang("IT") == "it" and presets.asr_lang("FR") == "fr" and presets.asr_lang("US") == "en"
    show = {"code": "TAB", "profile": {**presets.preset("NEW"), "dub_lang": "IT", "src_lang": "FR", "labels": "IT,FR"}}
    ep = {"number": 13, "work_dir": "/w", "output_dir": "/o"}
    _, st = plan(show, ep, {"src_video": "/v.mp4", "dub_audio": "/a.mp3"})
    dub = next(s for s in st if s["step"] == "mksrt_dub")["cmd"]
    src = next(s for s in st if s["step"] == "mksrt_src")["cmd"]
    assert dub[dub.index("--lang") + 1] == "it"
    assert src[src.index("--lang") + 1] == "fr"
    words_d = next(s for s in st if s["step"] == "words_dub")["cmd"]
    words_s = next(s for s in st if s["step"] == "words_src")["cmd"]
    assert words_d[words_d.index("--lang") + 1] == "it"
    assert words_s[words_s.index("--lang") + 1] == "fr"


def test_tolr_plan_has_lip_and_transitions(env):
    from dubstudio.pipeline.steps import plan
    from dubstudio import presets
    show = {"code": "TOLR", "profile": presets.preset("TOLR")}
    ep = {"number": 24, "work_dir": "/w", "output_dir": "/o"}
    ctx, st = plan(show, ep, {"src_video": "/v.mp4", "dub_audio": "/a.mp3", "dub_srt": "/d.srt", "src_srt": "/s.srt"})
    names = [s["step"] for s in st]
    assert "mksrt_dub" in names and "mksrt_src" in names and "lip_gpu" in names and "transitions" in names   # Whisper both sides, always
    assert next(s for s in st if s["step"] == "groups")["needs"] == ["lipsync"]
    assert "--clauses 0" in " ".join(next(s for s in st if s["step"] == "words_dub")["cmd"])
    assert "--max-drift 500" in " ".join(next(s for s in st if s["step"] == "groups")["cmd"])
    assert "--row-words 7 --row-seconds 4" in " ".join(next(s for s in st if s["step"] == "mksrt_dub")["cmd"])
    bo = next(s for s in st if s["step"] == "bookends")["cmd"]
    assert "/w/clean_noglow.mp4" in bo and "--no-ending" not in bo
    assert ctx["name_clean"] == "/o/TOLR_EP24_IT_final_clean.mp4"
    assert ctx["name_mapping"] == "/o/tolr_ep24_it_de_mapping.xlsx"


def test_given_srt_becomes_the_whisper_script(tmp_path):
    """Owner, 2026-10-20: Whisper makes both SRTs (exact times); a side without a .docx script is cleaned with its
    given SRT's text (right words and sentence ends)."""
    from dubstudio.pipeline.steps import plan
    from dubstudio import presets
    srt = tmp_path / "de.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nTalentiert, ja –\naber impulsiv.\n\n2\n00:00:04,000 --> 00:00:05,000\nWas?\n",
                   encoding="utf-8")
    show = {"code": "TOLR", "profile": presets.preset("TOLR")}
    ep = {"number": 24, "work_dir": str(tmp_path / "w"), "output_dir": str(tmp_path / "o")}
    ctx, st = plan(show, ep, {"src_video": "/v.mp4", "dub_audio": "/a.mp3", "dub_script": "/s.docx", "src_srt": str(srt)})
    src = " ".join(next(s for s in st if s["step"] == "mksrt_src")["cmd"])
    dub = " ".join(next(s for s in st if s["step"] == "mksrt_dub")["cmd"])
    assert "--script /s.docx" in dub
    txt = tmp_path / "w" / "script_src_from_srt.txt"
    assert f"--script {txt}" in src and txt.read_text(encoding="utf-8") == "Talentiert, ja – aber impulsiv.\nWas?\n"
    assert ctx["src_srt"].endswith("_DE_sentences.srt") and ctx["sub_srt"].endswith("TOLR_EP24_IT.srt")


def test_hih_plan_has_dynamic_subtitles(env):
    """Owner, 2026-10-22: HIH (only) gets the dynamic strip: US captions read, Italian cards made after the editor's
    review from the final edit, translated from those cards, both videos burned with --style dynamic."""
    from dubstudio.pipeline.steps import plan
    from dubstudio import presets
    show = {"code": "HIH", "profile": presets.preset("HIH")}
    ep = {"number": 55, "work_dir": "/w", "output_dir": "/o"}
    ctx, st = plan(show, ep, {"src_video": "/v.mp4", "dub_audio": "/a.wav", "dub_script": "/s.docx"})
    by = {s["step"]: s for s in st}
    assert by["captions"]["needs"] == ["ingest"] and "/v.mp4" in by["captions"]["cmd"]
    assert by["review"]["needs"] == ["use_scene"]
    assert by["dynsrt"]["needs"] == ["review", "captions"]
    dy = " ".join(by["dynsrt"]["cmd"])
    assert "--words /w/HIH_EP55_IT_words.json" in dy and "--out /w/HIH_EP55_IT_dynamic.srt" in dy
    from dubstudio.config import cfg
    assert cfg.font and Path(cfg.font).name == "Poppins-Bold.ttf" and Path(cfg.font).is_file()
    assert "--font" in dy and cfg.font in dy
    assert by["translate"]["needs"] == ["dynsrt"] and "--srt /w/HIH_EP55_IT_dynamic.srt" in " ".join(by["translate"]["cmd"])
    for k in ("subs_dub", "subs_en"):
        assert "--style dynamic" in " ".join(by[k]["cmd"])
    assert "--srt /w/HIH_EP55_IT_dynamic.srt" in " ".join(by["subs_dub"]["cmd"]) and "dynsrt" in by["subs_dub"]["needs"]
    for name in ("TDMB", "TOLR", "WBM", "NEW"):          # nobody else gets it unless the owner says so
        p = plan({"code": name, "profile": presets.preset(name)}, {"number": 1, "work_dir": "/w", "output_dir": "/o"},
                 {"src_video": "/v.mp4", "dub_audio": "/a.wav", "dub_script": "/s.docx"})[1]
        assert not {"captions", "dynsrt"} & {s["step"] for s in p}


def test_scene_edit_steps_in_the_order_of_docs06():
    """docs/06 section 1: groups -> library -> facemap -> scene_edit -> use_scene -> review -> render -> verify ->
    bookends -> trans_plan -> transitions -> subs -> scene_sheet -> check; fastfix not with the scene edit; the AI steps
    are marked for the gate; the face map runs in its own Python."""
    from dubstudio.pipeline.steps import plan
    from dubstudio import presets
    show = {"code": "TGS", "profile": presets.preset("TGS")}
    ep = {"number": 34, "work_dir": "/w", "output_dir": "/o"}
    ctx, st = plan(show, ep, {"src_video": "/v.mp4", "dub_audio": "/a.mp3", "dub_script": "/s.docx"})
    names = [s["step"] for s in st]; by = {s["step"]: s for s in st}
    order = ["groups", "library", "facemap", "scene_edit", "use_scene", "review", "render", "verify", "bookends",
             "trans_plan", "transitions", "subs_dub", "subs_en", "scene_sheet", "qc", "check", "notes",
             "publish", "cleanup"]
    assert [n for n in names if n in order] == order and "fastfix" not in names
    assert {s["step"] for s in st if s.get("ai")} == {"library", "facemap", "scene_edit", "qc"}
    assert by["facemap"]["cmd"][0] == "{fpy}" and by["facemap"]["resource"] == "gpu"
    assert by["facemap"]["needs"] == ["library", "lip_gpu"] and "--lips" in by["facemap"]["cmd"]
    assert by["transitions"]["cmd"][-2:] == ["--plan", "/w/transitions_plan.json"]
    assert "--max-lead" in by["subs_en"]["cmd"] and by["check"]["needs"] == ["qc"] and by["qc"]["needs"] == ["scene_sheet"]
    q = by["qc"]["cmd"]
    assert q[q.index("--videos") + 1:q.index("--videos") + 4] == ["/o/TGS_EP34_DE_final_clean.mp4", "/o/TGS_EP34_DE_final_de_subs.mp4",
                                                                "/o/TGS_EP34_DE_final_en_subs.mp4"] and q[-1] == "/o/tgs_ep34_qc.xlsx"
    assert by["subs_dub"]["cmd"][by["subs_dub"]["cmd"].index("--style") + 1] == "strip"      # TGS: the white strip
    assert ctx["name_scene_sheet"] == "/o/tgs_ep34_scene_edit.xlsx"


def test_ai_gate_stops_on_no_gateway_and_on_a_used_up_budget(monkeypatch):
    """On the gateway the message says "turn the VPN on"; on OpenRouter it says to check the internet. Either way
    a used-up budget stops the step with the numbers in it, and a step never runs without Claude."""
    import urllib.error, pytest
    from dubstudio import ai
    from dubstudio.runner import Runner
    monkeypatch.setenv("AI_PROVIDER", "gateway")
    monkeypatch.setattr(ai, "available", lambda: True)
    def down(*a, **k):
        raise urllib.error.URLError("timed out")
    monkeypatch.setattr(ai, "_req", down)
    with pytest.raises(RuntimeError, match="VPN"):
        Runner.ai_gate()
    monkeypatch.setattr(ai, "_req", lambda *a, **k: {"info": {"spend": 100.4, "max_budget": 100.0}})
    with pytest.raises(RuntimeError, match=r"AI budget used up on the AI gateway: \$100.40 of \$100.00"):
        Runner.ai_gate()
    monkeypatch.setattr(ai, "_req", lambda *a, **k: {"info": {"spend": 14.0, "max_budget": 100.0}})
    Runner.ai_gate()                                                     # budget left: goes on
    monkeypatch.setenv("AI_PROVIDER", "openrouter"); monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(ai, "_req", down)
    with pytest.raises(RuntimeError, match="OpenRouter is not reachable"):
        Runner.ai_gate()
    monkeypatch.setattr(ai, "_req", lambda *a, **k: {"data": {"usage": 250.0, "limit": 250.0}})
    with pytest.raises(RuntimeError, match=r"AI budget used up on OpenRouter"):
        Runner.ai_gate()


def test_use_scene_keeps_the_automatic_edit_and_stretches_the_last_piece(tmp_path):
    """run_episode.sh's use_scene, ported: the automatic edit kept once as manifest_groups.auto.json, the scene edit
    written as manifest_groups.json with its last piece stretched to the end of the dub."""
    import json
    from dubstudio.runner import Runner
    W = tmp_path; (W / "scene").mkdir()
    auto = {"total_ms": 10000, "segments": [{"it_start_ms": 0, "it_end_ms": 10000, "us_start_ms": 0, "us_end_ms": 10000}]}
    (W / "manifest_groups.json").write_text(json.dumps(auto))
    # 10 ms short of the dub: rounding, not a hole — stretch the last piece (a 1 s hole would be filled from matching).
    scene = {"total_ms": 9990, "segments": [{"it_start_ms": 0, "it_end_ms": 5000, "us_start_ms": 100, "us_end_ms": 5100},
                                            {"it_start_ms": 5000, "it_end_ms": 9990, "us_start_ms": 200, "us_end_ms": 2200, "broll": True}]}
    (W / "scene" / "scene_manifest.json").write_text(json.dumps(scene))
    assert Runner.use_scene(W) == 2
    m = json.loads((W / "manifest_groups.json").read_text())
    assert json.loads((W / "manifest_groups.auto.json").read_text()) == auto
    L = m["segments"][-1]
    want_us = 200 + 2000 * 5000 / 4990
    assert m["total_ms"] == 10000 and L["it_end_ms"] == 10000 and abs(L["us_end_ms"] - want_us) < 1e-6 and L["broll"] is True
    Runner.use_scene(W)                                                  # again: the automatic edit is not overwritten
    assert json.loads((W / "manifest_groups.auto.json").read_text()) == auto


def test_use_scene_trims_a_last_piece_past_the_dub(tmp_path):
    """Claude writes hundredths of a second, so the last piece can sit a few ms past the dub. Trim it."""
    import json
    from dubstudio.runner import Runner
    W = tmp_path; (W / "scene").mkdir()
    auto = {"total_ms": 10000, "segments": [{"it_start_ms": 0, "it_end_ms": 10000, "us_start_ms": 0, "us_end_ms": 10000}]}
    (W / "manifest_groups.json").write_text(json.dumps(auto))
    (W / "scene" / "scene_manifest.json").write_text(json.dumps({
        "total_ms": 10003, "segments": [
            {"it_start_ms": 0, "it_end_ms": 8000, "us_start_ms": 100, "us_end_ms": 8100},
            {"it_start_ms": 8000, "it_end_ms": 10003, "us_start_ms": 200, "us_end_ms": 2203, "broll": True}]}))
    assert Runner.use_scene(W) == 2
    L = json.loads((W / "manifest_groups.json").read_text())["segments"][-1]
    assert L["it_end_ms"] == 10000 and abs(L["us_end_ms"] - 2200) < 1e-6


def test_use_scene_fills_a_hole_from_matching(tmp_path):
    """Scene edit left a hole (and a placeholder with no footage). Matching already had a clip → use it, not black."""
    import json
    from dubstudio.runner import Runner
    W = tmp_path; (W / "scene").mkdir()
    auto = {"total_ms": 10000, "segments": [
        {"it_start_ms": 0, "it_end_ms": 4000, "us_start_ms": 0, "us_end_ms": 4000, "it_idx": 1},
        {"it_start_ms": 4000, "it_end_ms": 7000, "us_start_ms": 8000, "us_end_ms": 11000, "it_idx": 2},
        {"it_start_ms": 7000, "it_end_ms": 10000, "us_start_ms": 12000, "us_end_ms": 15000, "it_idx": 3}]}
    (W / "manifest_groups.json").write_text(json.dumps(auto))
    (W / "scene" / "scene_manifest.json").write_text(json.dumps({
        "total_ms": 10000, "segments": [
            {"it_start_ms": 0, "it_end_ms": 4000, "us_start_ms": 0, "us_end_ms": 4000, "note": "scene edit: ok"},
            {"it_start_ms": 4000, "it_end_ms": 4300, "us_start_ms": 8000, "us_end_ms": 8000, "note": "scene edit: placeholder"},
            {"it_start_ms": 7000, "it_end_ms": 10000, "us_start_ms": 12000, "us_end_ms": 15000, "note": "scene edit: ok"}]}))
    n = Runner.use_scene(W)
    segs = json.loads((W / "manifest_groups.json").read_text())["segments"]
    assert n >= 3
    # no black from 4.0–7.0 s: matching clip (rows 2) fills it
    t = 0
    for s in segs:
        assert s["it_start_ms"] <= t + 1
        assert s["us_end_ms"] - s["us_start_ms"] >= 34
        t = s["it_end_ms"]
    assert abs(t - 10000) < 2
    mid = [s for s in segs if s["it_start_ms"] >= 3999 and s["it_end_ms"] <= 7001]
    assert any("matching clip" in str(s.get("note") or "") for s in mid)
    assert json.loads((W / "manifest_groups.auto.json").read_text()) == auto


def test_final_check_reads_the_render_tail_log(tmp_path):
    """Check rode the render visit: FINAL CHECK PASSED is in the render log, not check.log."""
    from dubstudio.runner import Runner
    chk, rnd = tmp_path / "check.log", tmp_path / "render.log"
    rnd.write_text("=== check ===\nOK  a.mp4: 10s, streams ['audio', 'video']\nFINAL CHECK PASSED\nCHAIN OK\n")
    jobs = [{"step": "check", "log_path": str(chk)}, {"step": "render", "log_path": str(rnd)}]
    r = Runner.final_check_result(jobs)
    assert r["passed"] is True
    assert any("FINAL CHECK PASSED" in x for x in r["lines"])
    assert Runner.final_check_result([{"step": "check", "log_path": str(chk)}])["passed"] is None


def test_fill_scene_leaves_a_matching_not_found_hole(tmp_path):
    """If matching also had no clip there, do not invent footage — the editor gap banner is then correct."""
    from dubstudio.timeline import fill_scene_from_matching
    scene = [{"it_start_ms": 0, "it_end_ms": 4000, "us_start_ms": 0, "us_end_ms": 4000},
             {"it_start_ms": 7000, "it_end_ms": 10000, "us_start_ms": 1000, "us_end_ms": 4000}]
    auto = [{"it_start_ms": 0, "it_end_ms": 4000, "us_start_ms": 0, "us_end_ms": 4000},
            {"it_start_ms": 7000, "it_end_ms": 10000, "us_start_ms": 1000, "us_end_ms": 4000}]
    out = fill_scene_from_matching(scene, auto, 10000)
    covered = sum(s["it_end_ms"] - s["it_start_ms"] for s in out)
    assert covered <= 7001  # 4s + 3s; the 3s matching-not-found hole stays empty
