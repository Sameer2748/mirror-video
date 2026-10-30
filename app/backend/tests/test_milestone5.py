"""Milestone 5: the AI group rule, translation with an AI fallback, re-translate one line, subtitle styles."""
import json
from pathlib import Path
from conftest import wait_for
from test_m1 import make_show

BLOCKED_TRANSLATE = r'''
elif cmd == "translate":
    out = Path(arg("--out"))
    if "--export-todo" in a:
        Path(arg("--export-todo")).write_text("Ciao a tutti.\t\nCome stai?\t\n"); print("wrote 2 untranslated lines")
    elif arg("--engine") == "file":
        rows = dict(l.rstrip("\n").split("\t", 1) for l in open(arg("--import")) if "\t" in l)
        out.write_text("1\n00:00:00,000 --> 00:00:01,000\n" + rows["Ciao a tutti."] + "\n\n2\n00:00:01,000 --> 00:00:02,000\n" + rows["Come stai?"] + "\n")
        print("translated from file")
    else:
        print("TRANSLATION BLOCKED: HTTP 429"); sys.exit(1)
'''


def test_translation_falls_back_to_the_ai(env, monkeypatch):
    tools = env["tmp"] / "tools" / "scripts" / "dubstitch.py"
    src = tools.read_text()
    start = src.index('elif cmd == "translate":'); end = src.index('elif cmd == "bookends":')
    tools.write_text(src[:start] + BLOCKED_TRANSLATE.strip("\n") + "\n" + src[end:])
    from dubstudio import ai
    from dubstudio.pipeline import translate_ai
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(translate_ai, "translate", lambda texts, sl, tl, model=None, log=print: [f"EN:{t}" for t in texts])
    c = env["client"]; s = make_show(c, env["show"])
    eid = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()["id"]
    c.post(f"/api/episodes/{eid}/process")
    job = wait_for(lambda: next((j for j in c.get(f"/api/episodes/{eid}").json()["jobs"]
                                 if j["step"] == "translate" and j["status"] in ("done", "failed")), None))
    assert job["status"] == "done" and "AI" in job["message"], job
    ep = c.get(f"/api/episodes/{eid}").json()
    en = next(Path(ep["output_dir"]).glob("*english*.srt")).read_text()
    assert "EN:Ciao a tutti." in en and "EN:Come stai?" in en
    log = "\n".join(c.get(f"/api/jobs/{job['id']}/log").json()["lines"])
    assert "--export-todo" in log and "--engine file --import" in log


def test_translation_without_ai_fails_with_advice(env, monkeypatch):
    tools = env["tmp"] / "tools" / "scripts" / "dubstitch.py"
    src = tools.read_text()
    start = src.index('elif cmd == "translate":'); end = src.index('elif cmd == "bookends":')
    tools.write_text(src[:start] + BLOCKED_TRANSLATE.strip("\n") + "\n" + src[end:])
    from dubstudio import ai
    monkeypatch.setattr(ai, "available", lambda: False)
    c = env["client"]; s = make_show(c, env["show"])
    eid = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()["id"]
    c.post(f"/api/episodes/{eid}/process")
    job = wait_for(lambda: next((j for j in c.get(f"/api/episodes/{eid}").json()["jobs"]
                                 if j["step"] == "translate" and j["status"] in ("done", "failed")), None))
    assert job["status"] == "failed" and "import an English SRT" in job["message"]


def test_ai_group_rule_joins_neighbours(tmp_path, monkeypatch):
    from dubstudio.pipeline import ai_match, decisions as dec
    w = tmp_path / "w"; w.mkdir()
    prep = {"dub": [{"text": "Uno due"}, {"text": "Tre quattro"}, {"text": "Cinque sei"}],
            "src": [{"text": "One two three four"}, {"text": "Five six"}],
            "auto": {"0": {"src": 0, "kind": "DP match"}, "2": {"src": 1, "kind": "DP match"}}}
    (w / "prep.json").write_text(json.dumps(prep))
    spec = 'A(1,"Tre quattro",0,"One two three","AI: same beat")\n'
    (w / "spec_ai.py").write_text(spec)
    (w / "ai_proposals.json").write_text(json.dumps({"model": "m", "usage": {}, "proposals": [
        {"row": 1, "action": "assign", "src": 0, "reason": "same beat", "confidence": 0.8}]}))
    res = {"proposals": [{"row": 1, "action": "assign", "src": 0, "reason": "same beat", "confidence": 0.8}], "spec": spec}
    calls = []
    def check(s):                       # the pipeline would refuse rows 0 and 1 on the same footage, until grouped
        calls.append(s)
        return {"ok": "G(" in s, "overlaps": [] if "G(" in s else [[0, 1, 1500]], "problems": []}
    r = ai_match.apply_group_rule(w, res, check, log=lambda m: None)
    assert r["joined"] == [[0, 1]] and r["check"]["ok"]
    p = r["proposals"][0]
    assert p["action"] == "group" and p["members"] == [0, 1] and p["src"] == 0
    assert "G([(0," in (w / "spec_ai.py").read_text()
    assert json.loads((w / "ai_proposals.json").read_text())["joined"] == [[0, 1]]


def test_subtitle_styles_per_language(env):
    c = env["client"]; s = make_show(c, env["show"], preset="TOLR")
    r = c.patch(f"/api/shows/{s['id']}", json={"profile": {"sub_styles": {"en": {"style": "box", "size": 46, "bottom": 140, "alpha": 0.5}}}})
    assert r.status_code == 200 and r.json()["profile"]["sub_styles"] == {"en": {"style": "box", "size": 46, "bottom": 140, "alpha": 0.5}}
    r = c.patch(f"/api/shows/{s['id']}", json={"profile": {"sub_styles": {"dub": {"style": "strip", "size": 44}}}})
    assert set(r.json()["profile"]["sub_styles"]) == {"dub", "en"}                     # the other language is kept
    for bad in ({"style": "fancy"}, {"size": 200}, {"alpha": 2}, {"bottom": "x"}):
        assert c.patch(f"/api/shows/{s['id']}", json={"profile": {"sub_styles": {"en": bad}}}).status_code == 400
    from dubstudio.pipeline.steps import plan
    show = c.get(f"/api/shows/{s['id']}").json()
    _, st = plan(show, {"number": 24, "work_dir": "/w", "output_dir": "/o"}, {"src_video": "/v.mp4", "dub_audio": "/a.mp3", "dub_srt": "/d.srt", "src_srt": "/s.srt"})
    cmd = {x["step"]: x["cmd"] for x in st}
    en, dub = " ".join(cmd["subs_en"]), " ".join(cmd["subs_dub"])
    assert "--style box --box-size 46 --box-bottom 140 --box-alpha 0.5" in en
    assert "--style strip --size 44" in dub and "--box-" not in dub
    # no styles set: the commands are exactly as before (pipeline defaults)
    _, st = plan({"code": "TOLR", "profile": __import__("dubstudio.presets", fromlist=["x"]).preset("TOLR")},
                 {"number": 24, "work_dir": "/w", "output_dir": "/o"}, {"src_video": "/v.mp4", "dub_audio": "/a.mp3", "dub_srt": "/d.srt", "src_srt": "/s.srt"})
    assert next(x["cmd"] for x in st if x["step"] == "subs_en")[5:7] == ["--style", "box"] and \
        not any(a.startswith("--box-") for x in st for a in (x["cmd"] or []))
    tl_style = c.get(f"/api/shows/{s['id']}").json()["profile"]["sub_styles"]["en"]["size"]
    assert tl_style == 46


def test_retranslate_one_line(env, monkeypatch):
    from dubstudio import ai
    from dubstudio.pipeline import translate_ai
    c = env["client"]; s = make_show(c, env["show"])
    eid = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()["id"]
    monkeypatch.setattr(ai, "available", lambda: False)
    assert c.post(f"/api/episodes/{eid}/translate-line", json={"text": "Ciao"}).status_code == 409
    monkeypatch.setattr(ai, "available", lambda: True)
    seen = {}
    monkeypatch.setattr(translate_ai, "one", lambda text, sl, tl, model=None, before="", after="": seen.update(sl=sl, tl=tl, before=before) or "Hi")
    assert c.post(f"/api/episodes/{eid}/translate-line", json={"text": "Ciao", "before": "Prima"}).json() == {"text": "Hi"}
    assert seen == {"sl": "it", "tl": "en", "before": "Prima"}
    assert c.post(f"/api/episodes/{eid}/translate-line", json={"text": " "}).status_code == 400


def test_not_found_and_unsure_flags(tmp_path):
    from dubstudio import timeline as tlm
    from test_editor import write_manifest
    w = tmp_path / "w"; write_manifest(w, [(0, 2000, 0, 2000), (2000, 6000, 5000, 7000)], 6000)
    (w / "prep.json").write_text(json.dumps({"dub": [{"text": "Uno", "start_ms": 0, "end_ms": 2000},
                                                    {"text": "Due", "start_ms": 2000, "end_ms": 3500},
                                                    {"text": "Tre", "start_ms": 3600, "end_ms": 6000}], "src": [], "auto": {}}))
    (w / "decisions.json").write_text(json.dumps({"gap": {"2": "not found: the AI could not place this row"}, "groups": []}))
    spec = 'GAP(2,"Tre","x")\n'; (w / "spec.py").write_text(spec)
    (w / "ai_auto.json").write_text(json.dumps({"spec": spec, "not_found": [2], "low": [{"row": 0, "confidence": 0.4, "reason": "maybe"}]}))
    tl = tlm.build_v1(w)
    assert [(c["id"], c["t1"]) for c in tl["clips"]] == [("c0", 2000), ("c1", 3600)]   # row 2 starts as an empty gap
    assert tl["holes"][0]["row"] == 2
    # a version saved before (the pipeline's clip still stretched over the row) keeps its red flag
    d = json.loads((w / "decisions.json").read_text()); (w / "decisions.json").write_text(json.dumps({"groups": []}))
    tl = tlm.build_v1(w); (w / "decisions.json").write_text(json.dumps(d))
    fl = tlm.flags(tl, tlm.context(w), {})
    nf = [f for f in fl["c1"] if f["kind"] == "not_found"]
    assert nf and nf[0]["sev"] == "red" and "IT#2" in nf[0]["msg"] and "add a clip" in nf[0]["msg"]
    assert any(f["kind"] == "ai_unsure" and f["sev"] == "yellow" for f in fl["c0"])
    tl["clips"][1]["s0"] += 500                                   # the editor put other footage there
    assert [f["sev"] for f in tlm.flags(tl, tlm.context(w), {})["c1"] if f["kind"] == "not_found"] == ["info"]
    (w / "spec.py").write_text("C(0,'by hand')\n")                # decisions made by hand later: no AI-unsure flags
    assert not any(f["kind"] == "ai_unsure" for f in tlm.flags(tlm.build_v1(w), tlm.context(w), {})["c0"])


def test_names_and_second_look(tmp_path, monkeypatch):
    from dubstudio import ai
    from dubstudio.pipeline import ai_match
    csv = tmp_path / "names.csv"
    csv.write_text("Type,ID,Canonical Name,Original Mention,Localized Mention\nCharacter,c1,asher,Asher,Demetrio\n"
                   "Character,c2,violet,Violet,Viola\nCharacter,c3,bob,Bob,Roberto\nEntity,e1,school,the academy,l'accademia\n")
    names = ai_match.load_names(csv, ["Smettila, le ordinò Demetrio.", "After Asher smacked her butt", "Violet froze"])
    assert names == [("Asher", "Demetrio"), ("Violet", "Viola"), ("Bob", "Roberto")]   # the whole (short) sheet
    w = tmp_path / "w"; w.mkdir()
    prep = {"dub": [{"text": "Le diede una sculacciata.", "start_ms": 0}, {"text": "Smettila, le ordinò Demetrio.", "start_ms": 1000},
                    {"text": "Viola si bloccò.", "start_ms": 2000}],
            "src": [{"text": "After Asher smacked her butt,", "start_ms": 0}, {"text": "she froze, fuming inside.", "start_ms": 1500}],
            "auto": {"0": {"src": 0, "kind": "DP match"}, "2": {"src": 1, "kind": "DP match"}}}
    (w / "prep.json").write_text(json.dumps(prep)); (w / "decisions.json").write_text("{}")
    prompts = []
    def chat(prompt, model=None, max_tokens=0, **k):
        prompts.append(prompt)
        if "first read answered" in prompt:                                     # the second look groups it
            return '{"decisions":[{"row":1,"action":"group","src":0,"members":[0,1],"reason":"the order is part of the smack beat","confidence":0.7}]}', {}
        return '{"decisions":[{"row":1,"action":"gap","src":null,"reason":"added line","confidence":0.5}]}', {}
    monkeypatch.setattr(ai, "chat", chat); monkeypatch.setattr(ai, "settings", lambda: {"AI_MODEL": "m", "AI_MODEL_FAST": "f"})
    r = ai_match.propose(w, names_csv=csv, log=lambda m: None)
    assert "Asher -> Demetrio" in prompts[0] and "used by D2" in prompts[0]
    assert "used by D0" in prompts[0]
    rec = r["proposals"][0]
    assert rec["action"] == "group" and rec["members"] == [0, 1] and rec.get("second_look") is True
    assert "G([(0," in r["spec"]
