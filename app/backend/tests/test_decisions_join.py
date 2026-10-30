"""Step 2 helpers the Check matching screen uses: spec round-trip and joining neighbours that share footage."""
import json
from pathlib import Path
from dubstudio.pipeline import decisions as dec


def make_work(tmp_path):
    w = tmp_path / "w"; w.mkdir()
    dub = [{"text": t} for t in ["Uno due tre", "Quattro cinque sei", "Sette otto", "Nove dieci", "Undici", "Dodici", "Tredici"]]
    src = [{"text": t} for t in ["One two three", "Four five six", "Seven eight", "Nine ten", "Eleven twelve"]]
    auto = {"0": {"src": 0, "kind": "DP match"}, "2": {"src": 2, "kind": "DP match"}, "3": {"src": 3, "kind": "DP match"},
            "6": {"src": 2, "kind": "DP match"}}
    (w / "prep.json").write_text(json.dumps({"dub": dub, "src": src, "auto": auto}))
    return w


def test_to_spec_round_trip(tmp_path):
    w = make_work(tmp_path)
    spec = ('G([(0,"Uno"),(1,"Quattro")],[0,1],"One","one beat")\nA(4,"Undici",4,"Eleven","x")\n'
            'C(3,"ok")\nGAP(5,"Dodici","none")\n')
    dec.from_spec(w, spec); d1 = json.loads((w / "decisions.json").read_text())
    p = json.loads((w / "prep.json").read_text())
    dec.from_spec(w, dec.to_spec(p, d1)); d2 = json.loads((w / "decisions.json").read_text())
    assert d1 == d2


def test_join_neighbours_and_leave_far_pairs(tmp_path):
    w = make_work(tmp_path)
    # row 1 assigned to S2, which row 2 (automatic) already shows -> one beat in two lines -> group 1-2 on S2;
    # row 6 (automatic S2) is far from rows 1-2 -> left for the editor
    spec = 'A(1,"Quattro",2,"Seven","AI pick")\nA(4,"Undici",4,"Eleven","x")\nGAP(5,"Dodici","none")\n'
    new, joined, left = dec.join_shared(w, spec, [[1, 2, 900], [2, 6, 900], [1, 6, 900]])
    assert joined == [[1, 2]] and sorted(left) == [[1, 6], [2, 6]]
    dec.from_spec(w, new); d = json.loads((w / "decisions.json").read_text())
    assert d["groups"] == [{"members": [1, 2], "dub_prefixes": ["Quattro cinque sei", "Sette otto"], "src": 2,
                            "src_prefix": "Seven eight", "reason": d["groups"][0]["reason"]}]
    assert "1" not in d["assign"] and d["assign"]["4"]["src"] == 4 and "5" in d["gap"]
    assert "AI pick" in d["groups"][0]["reason"]


def test_join_grows_to_whole_groups(tmp_path):
    w = make_work(tmp_path)
    spec = 'G([(2,"Sette"),(3,"Nove")],[2,3],"Seven","beat")\nA(1,"Quattro",2,"Seven","x")\n'
    new, joined, _ = dec.join_shared(w, spec, [[1, 2, 500]])
    assert joined == [[1, 3]]                     # the existing group 2-3 is absorbed whole, never split
    dec.from_spec(w, new); d = json.loads((w / "decisions.json").read_text())
    assert [g["members"] for g in d["groups"]] == [[1, 2, 3]] and d["groups"][0]["src"] == [2, 3]


def test_check_spec_runs_the_pipeline_sheet(env):
    """With the fake pipeline the sheet passes; a spec that does not read is reported, not raised."""
    from dubstudio.config import cfg
    w = env["tmp"] / "chk"; w.mkdir()
    (w / "prep.json").write_text(json.dumps({"dub": [{"text": "Ciao"}, {"text": "Bene"}], "src": [{"text": "Hello"}],
                                             "auto": {"0": {"src": 0, "kind": "DP match"}}}))
    ok = dec.check_spec(w, 'GAP(1,"Bene","none")\n', cfg.pipeline_python, cfg.scripts, "IT,US")
    assert ok["ok"] and ok["problems"] == [] and ok["counts"]["gap"] == 1
    bad = dec.check_spec(w, 'A(1,"Nope",0,"Hello","x")\n', cfg.pipeline_python, cfg.scripts, "IT,US")
    assert not bad["ok"] and "lacks 'Nope'" in bad["problems"][0]
    assert not (w / "decisions.json").exists()    # the real work folder is never written by a check


def test_top5_alternatives_for_flagged_rows():
    from dubstudio.pipeline.decisions import alternatives
    prep = {"dub": [{}, {}, {}], "src": [{}] * 6,
            "sim": [[0.1, 0.9, 0.2, 0.3, 0.4, 0.5], [0.6, 0.1, 0.2, 0.3, 0.4, 0.5], [0.0, 0.1, 0.2, 0.3, 0.4, 0.8]],
            "auto": {"0": {"src": 1, "kind": "DP match"}, "1": {"src": 0, "kind": "Case A reuse"}}}
    a = alternatives(prep)
    assert set(a) == {"1", "2"}                         # row 0 was decided by the matcher; 1 is Case A; 2 unmatched
    assert [k for k, _ in a["2"]] == [5, 4, 3, 2, 1] and a["1"][0] == [0, 0.6]
