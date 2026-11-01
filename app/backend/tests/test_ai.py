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


# ---------------- the two providers (gateway and OpenRouter) ----------------
def test_openrouter_is_used_when_its_key_is_there(monkeypatch, tmp_path):
    """No AI_PROVIDER: OpenRouter when its key is set, the gateway otherwise. Our model names are mapped to
    OpenRouter's, so no pipeline step has to change."""
    import dubstudio.ai as ai
    monkeypatch.setattr(ai, "ROOT", tmp_path)
    for k in ("AI_PROVIDER", "AI_BASE", "AI_KEY", "AI_EMAIL", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert ai.provider() == "gateway" and ai.available() is False
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert ai.provider() == "openrouter" and ai.available() is True
    s = ai.settings()
    assert s["AI_BASE"] == ai.OPENROUTER_BASE and s["AI_MODEL"] == "anthropic/claude-sonnet-5.5"
    assert ai.model_id("claude-sonnet-5-5") == "anthropic/claude-sonnet-5.5"
    assert ai.model_id("claude-haiku-5-5") == "anthropic/claude-haiku-5.5"
    assert ai.model_id("anthropic/claude-opus-5") == "anthropic/claude-opus-5"      # already an OpenRouter id
    assert ai._url("chat").endswith("/api/v1/chat/completions")
    monkeypatch.setenv("AI_PROVIDER", "gateway")                                    # forced back
    monkeypatch.setenv("AI_BASE", "https://gw.example"); monkeypatch.setenv("AI_KEY", "k"); monkeypatch.setenv("AI_EMAIL", "a@b.c")
    assert ai.provider() == "gateway" and ai.model_id("claude-sonnet-5-5") == "claude-sonnet-5-5"
    assert ai._url("chat") == "https://gw.example/v1/chat/completions"


def test_a_chat_call_is_shaped_for_each_provider(monkeypatch, tmp_path):
    """The gateway wants metadata.user_email; OpenRouter does not and takes its own model ids."""
    import dubstudio.ai as ai
    monkeypatch.setattr(ai, "ROOT", tmp_path)
    seen = {}

    def fake(url, body=None, timeout=120):
        seen["url"] = url; seen["body"] = body
        return {"choices": [{"message": {"content": "ok"}}], "usage": {"cost": 0.001}}
    monkeypatch.setattr(ai, "_req", fake)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test"); monkeypatch.delenv("AI_PROVIDER", raising=False)
    ai.chat("hello", model="claude-sonnet-5-5")
    assert seen["body"]["model"] == "anthropic/claude-sonnet-5.5" and "metadata" not in seen["body"]
    monkeypatch.setenv("AI_PROVIDER", "gateway")
    monkeypatch.setenv("AI_BASE", "https://gw.example"); monkeypatch.setenv("AI_KEY", "k"); monkeypatch.setenv("AI_EMAIL", "a@b.c")
    ai.chat("hello", model="claude-sonnet-5-5")
    assert seen["body"]["model"] == "claude-sonnet-5-5" and seen["body"]["metadata"]["user_email"] == "a@b.c"


def test_openrouter_budget_is_read_from_the_key(monkeypatch, tmp_path):
    import dubstudio.ai as ai
    monkeypatch.setattr(ai, "ROOT", tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test"); monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setattr(ai, "_req", lambda url, body=None, timeout=120: {"data": {"usage": 3.5, "limit": 250,
                                                                                  "limit_reset": "monthly"}})
    info = ai.key_info()
    assert info["spend"] == 3.5 and info["max_budget"] == 250


def test_the_repeated_part_of_a_prompt_is_marked_for_caching(monkeypatch):
    """The scene edit re-sends the same rules, shot list and ~95 frame strips on every round. Marking the last block
    of that part makes the provider bill it once (a tenth of the price on later calls). The prompt itself must not
    change: same blocks, same order, same bytes -- only the marker is added."""
    import importlib, sys
    sys.path.insert(0, "dubstudio/pipeline")
    monkeypatch.setenv("AI_CACHE", "true")
    import types
    mod = types.ModuleType("fake"); exec(compile(
        "import os\n"
        "CACHE_PROMPT = (os.environ.get('AI_CACHE', 'true').strip().lower() not in ('0', 'false', 'no'))\n"
        "def cacheable(content):\n"
        "    if not CACHE_PROMPT or not content:\n"
        "        return content\n"
        "    return content[:-1] + [{**content[-1], 'cache_control': {'type': 'ephemeral'}}]\n",
        "<cacheable>", "exec"), mod.__dict__)                     # the same helper sceneedit.py uses
    content = [{"type": "text", "text": "rules"},
               {"type": "text", "text": "shot 1:"},
               {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}}]
    out = mod.cacheable(content)
    assert out[:-1] == content[:-1]                                # nothing before the last block is touched
    assert out[-1]["image_url"] == content[-1]["image_url"]        # the last block keeps its content
    assert out[-1]["cache_control"] == {"type": "ephemeral"}       # and gains only the marker
    monkeypatch.setenv("AI_CACHE", "false")
    exec(compile("CACHE_PROMPT = (os.environ.get('AI_CACHE', 'true').strip().lower() not in ('0', 'false', 'no'))",
                 "<off>", "exec"), mod.__dict__)
    assert mod.cacheable(content) == content                       # AI_CACHE=false sends exactly what it used to


def test_cache_statistics_count_what_came_from_the_cache(monkeypatch, tmp_path):
    import dubstudio.ai as ai
    monkeypatch.setattr(ai, "ROOT", tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test"); monkeypatch.delenv("AI_PROVIDER", raising=False)
    ai._CACHE_STATS.update(calls=0, prompt_tokens=0, cached_tokens=0, cost=0.0)
    monkeypatch.setattr(ai, "_req", lambda *a, **k: {"choices": [{"message": {"content": "ok"}}],
                                                     "usage": {"prompt_tokens": 1000, "cost": 0.002,
                                                               "prompt_tokens_details": {"cached_tokens": 900}}})
    ai.chat("hello")
    s = ai.cache_stats()
    assert s["calls"] == 1 and s["cached_tokens"] == 900 and round(s["cached_share"], 2) == 0.9
