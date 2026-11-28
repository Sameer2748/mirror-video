"""A truncated who-call must not abort scene edit (IMTS DE-US 174)."""
from dubstudio.pipeline.who_rows import (
    complete_who, fallback_who, missing_units, parse_who_reply)


CAST = {"Inès", "Charles", "Professeur Menier"}


def _unit(row, people):
    return {"rows": [row], "parts": [{"shot": 1, "people": people}]}


def test_parse_who_keeps_closed_rows_from_truncated_json():
    text = '{"units": [{"u": 1, "show": ["Charles"], "listener": [], "why": "ok"}, {"u": 2, "sh'
    got = parse_who_reply(text, lambda t, key=None: {"units": [
        {"u": 1, "show": ["Charles"], "listener": [], "why": "ok"}]})
    assert "1" in got and "2" not in got


def test_missing_rows_are_retried_then_filled_from_the_clip():
    units = [_unit(69, ["Charles"]), _unit(70, ["Inès"]), _unit(71, ["Professeur Menier"])]
    calls = []

    def ask(chunk):
        calls.append([u["rows"][0] for u in chunk])
        if len(calls) == 1:
            return '{"units": [{"u": 69, "show": ["Charles"], "listener": [], "why": "a"}]}', {"cost": 0.1}
        return "{}", {"cost": 0.05}

    def extract(text, key=None):
        if "69" in (text or ""):
            return {"units": [{"u": 69, "show": ["Charles"], "listener": [], "why": "a"}]}
        return {"units": []}

    who, usage = complete_who(units, {}, ask, extract, CAST, batch=10)
    assert set(who) == {"69", "70", "71"}
    assert who["69"]["show"] == ["Charles"]
    assert who["70"]["show"] == ["Inès"]
    assert who["71"]["why"].startswith("clip people")
    assert missing_units(units, who) == []
    assert usage["calls"] == 2


def test_fallback_uses_only_cast_names_on_the_clip():
    u = _unit(5, ["Charles", "extra", "Inès"])
    got = fallback_who(u, CAST)
    assert got["show"] == ["Charles", "Inès"]
    assert got["u"] == 5
