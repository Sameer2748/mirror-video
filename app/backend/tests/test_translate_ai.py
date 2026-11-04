"""How Claude's translation replies are read: the shapes that used to fail a batch of 40 (TGS ep32)."""
from dubstudio.pipeline import translate_ai as T


def test_parse_object_t_array():
    assert T.parse_batch_reply('{"t": ["Paul!", "Stay there!"]}', 2) == ["Paul!", "Stay there!"]


def test_parse_raw_json_array_and_fence():
    assert T.parse_batch_reply('```json\n["A", "B", "C"]\n```', 3) == ["A", "B", "C"]


def test_parse_numbered_list():
    text = "1. Paul!\n2. Stay there!\n3. Come on."
    assert T.parse_batch_reply(text, 3) == ["Paul!", "Stay there!", "Come on."]


def test_parse_single_string_t():
    assert T.parse_batch_reply('{"t": "Hi"}', 1) == ["Hi"]


def test_parse_rejects_wrong_count():
    assert T.parse_batch_reply('{"t": ["only one"]}', 2) is None
    assert T.parse_batch_reply("1. A\n2. B", 3) is None


def test_why_reports_count_mismatch():
    assert "2 strings, needed 40" in T._why('{"t": ["a", "b"]}', 40, None)


def test_batch_splits_when_count_never_matches(monkeypatch):
    """A 4-line batch that always comes back as 3 strings is split until each call matches."""
    calls = []

    def fake_chat(prompt, model=None, max_tokens=0, **k):
        items = [m.group(2) for ln in prompt.splitlines() if (m := T.NUM_LINE.match(ln))]
        calls.append(len(items))
        if len(items) >= 4:
            return '{"t": ["a", "b", "c"]}', {}          # the old failure: 3 for 4
        return '{"t": [' + ", ".join(f'"{i}"' for i in items) + "]}", {}

    monkeypatch.setattr(T.ai, "chat", fake_chat)
    notes = []
    out = T.translate(["w", "x", "y", "z"], batch=4, log=notes.append)
    assert out == ["w", "x", "y", "z"]
    assert any("splitting" in n for n in notes)
    assert min(calls) < 4


def test_fill_todo_resumes_already_filled(tmp_path, monkeypatch):
    todo = tmp_path / "todo.tsv"
    todo.write_text("Ciao.\t\nDopo.\t\n")
    out = tmp_path / "out.tsv"
    out.write_text("Ciao.\tHi.\n")
    monkeypatch.setattr(T, "translate", lambda texts, sl, tl, model=None, log=print: [f"EN:{t}" for t in texts])
    n = T.fill_todo(todo, out, sl="it", tl="en", log=lambda m: None)
    assert n == 2
    rows = dict(l.split("\t", 1) for l in out.read_text().splitlines())
    assert rows["Ciao."] == "Hi."
    assert rows["Dopo."] == "EN:Dopo."
