"""Step 2 by AI. Offline: the gateway call is replaced by a fake. Live check (costs a few cents):
DUBSTUDIO_LIVE_AI=1 pytest tests/test_ai.py -k live"""
import json, os
import pytest
from conftest import wait_for
from test_m1 import make_show


def test_extract_json_skips_prose():
    from dubstudio.ai import extract_json
    assert extract_json('Row 15 is [13]. {"decisions":[{"row":15}]}', "decisions") == {"decisions": [{"row": 15}]}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_ai_matching_endpoint_with_fake_gateway(env, monkeypatch):
    from dubstudio import ai
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "chat", lambda prompt, model=None, max_tokens=0, **k: (
        'Here you go. {"decisions":[{"row":1,"action":"assign","src":1,"reason":"same question","confidence":0.9},'
        '{"row":2,"action":"gap","src":null,"reason":"added line","confidence":0.8}]}', {"prompt_tokens": 10, "completion_tokens": 5}))
    c = env["client"]; s = make_show(c, env["show"])
    e = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    c.post(f"/api/episodes/{e['id']}/process")
    # with the AI set up, the decisions step does not wait: Claude decides, the unplaced row becomes "not found"
    wait_for(lambda: any(j["step"] == "decisions" and j["status"] == "done"
                         for j in c.get(f"/api/episodes/{e['id']}").json()["jobs"]))
    d = c.get(f"/api/episodes/{e['id']}/decisions").json()
    assert d["missing"] == [] and d["ai_auto"]["not_found"] == [2] and d["ai_auto"]["rows"] == 2
    assert d["ai_auto"]["low"] == []
    ep = wait_for(lambda: (lambda x: x if x["status"] == "review" else None)(c.get(f"/api/episodes/{e['id']}").json()))
    # the manual button still works (e.g. to ask again)
    r = c.post(f"/api/episodes/{e['id']}/decisions/ai", json={"apply": True})
    assert r.status_code == 200, r.text
    assert [p["action"] for p in r.json()["proposals"]] == ["assign", "gap"]


@pytest.mark.skipif(os.environ.get("DUBSTUDIO_LIVE_AI") != "1", reason="live gateway call; set DUBSTUDIO_LIVE_AI=1")
def test_live_gateway_claude():
    from dubstudio import ai
    assert ai.available(), "fill app/.env"
    text, usage = ai.chat('Reply with JSON {"ok": true} only.', model=ai.settings()["AI_MODEL_FAST"], max_tokens=20)
    assert ai.extract_json(text) == {"ok": True}
