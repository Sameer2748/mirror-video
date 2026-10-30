"""Step 2 review of unused source lines (the manual read's last check): an unused source line is given to the dub
row before/after it (its footage is extended), or noted as left out. Offline: the gateway call is a fake."""
import json


def _prep(tmp_path):
    p = {"dub": [{"text": "Ciao a tutti", "start_ms": 0}, {"text": "Lei si girò di scatto", "start_ms": 2000},
                 {"text": "Poi se ne andò", "start_ms": 5000}],
         "src": [{"text": "Hello everyone", "start_ms": 0}, {"text": "She turned around", "start_ms": 2000},
                 {"text": "Her eyes narrowed", "start_ms": 3500}, {"text": "A dog barked far away", "start_ms": 4500},
                 {"text": "Then she left", "start_ms": 5500}],
         "auto": {"0": {"src": 0, "kind": "DP match"}, "1": {"src": 1, "kind": "DP match"},
                  "2": {"src": 4, "kind": "DP match"}}}
    (tmp_path / "prep.json").write_text(json.dumps(p))
    return p


def test_unused_line_extends_the_row_before_and_the_rest_is_noted(tmp_path, monkeypatch):
    from dubstudio import ai
    from dubstudio.pipeline import ai_match, decisions as dec
    p = _prep(tmp_path)
    seen = []
    def chat(prompt, model=None, max_tokens=0, **k):
        seen.append(prompt)
        return ('ok {"unused":[{"src":2,"attach":"prev","reason":"same beat as her turning","confidence":0.8},'
                '{"src":3,"attach":"none","reason":"the dub leaves the dog out","confidence":0.7}]}', {})
    monkeypatch.setattr(ai, "chat", chat)
    usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    spec, rep = ai_match.review_unused(tmp_path, p, "# none\n", [], None, usage, lambda m: None)
    assert rep["unused"] == [2, 3] and rep["attached"][0][:2] == [2, 1] and rep["left"] == [3]
    assert "S2" in seen[0] and "UNUSED" in seen[0]
    dec.from_spec(tmp_path, spec)
    D = json.loads((tmp_path / "decisions.json").read_text())
    assert D["assign"]["1"]["src"] == [1, 2]                     # row 1 now shows S1-S2
    assert "3" in D["unused_notes"] and "2" not in D["unused_notes"]


def test_review_never_takes_another_rows_footage(tmp_path, monkeypatch):
    from dubstudio import ai
    from dubstudio.pipeline import ai_match, decisions as dec
    p = _prep(tmp_path)
    # row 1 is told to take S3 ("next" of S3 is row 2, "prev" is row 1): S2 is unused too and goes with it
    monkeypatch.setattr(ai, "chat", lambda *a, **k: ('{"unused":[{"src":2,"attach":"next"},{"src":3,"attach":"next"}]}', {}))
    spec, rep = ai_match.review_unused(tmp_path, p, "# none\n", [], None,
                                       {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}, lambda m: None)
    dec.from_spec(tmp_path, spec)
    D = json.loads((tmp_path / "decisions.json").read_text())
    assert D["assign"]["2"]["src"] == [2, 4]                     # row 2 extended back over S2-S3, nobody else's footage


def test_review_failure_keeps_the_decisions(tmp_path, monkeypatch):
    from dubstudio import ai
    from dubstudio.pipeline import ai_match
    p = _prep(tmp_path)
    monkeypatch.setattr(ai, "chat", lambda *a, **k: ("no json here", {}))
    spec, rep = ai_match.review_unused(tmp_path, p, "# none\n", [], None,
                                       {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}, lambda m: None)
    assert rep["attached"] == [] and rep["left"] == [2, 3]       # nothing attached, both only noted
