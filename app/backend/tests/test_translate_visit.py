"""Matching-CPU translate: Google 429 must fall through to Claude, not fail the visit."""
import json, sys
from pathlib import Path

from dubstudio.pipeline import translate_visit


FAKE = r'''
import sys
from pathlib import Path
a = sys.argv[1:]
def arg(k):
    return a[a.index(k)+1] if k in a else None
out = Path(arg("--out"))
if "--export-todo" in a:
    Path(arg("--export-todo")).write_text("Hallo Welt.\t\nGuten Morgen.\t\n")
    print("wrote todo")
elif "--engine" in a and arg("--engine") == "file":
    rows = dict(l.rstrip("\n").split("\t", 1) for l in open(arg("--import")) if "\t" in l)
    out.write_text("1\n00:00:00,000 --> 00:00:01,000\n" + rows["Hallo Welt."] + "\n\n"
                   "2\n00:00:01,000 --> 00:00:02,000\n" + rows["Guten Morgen."] + "\n")
    print("imported")
else:
    print("TRANSLATION BLOCKED: HTTP 429")
    sys.exit(1)
'''


def test_google_429_is_filled_by_claude(tmp_path, monkeypatch):
    fake = tmp_path / "dubstitch.py"
    fake.write_text(FAKE)
    work = tmp_path / "work"; work.mkdir()
    out = tmp_path / "en.srt"
    from dubstudio.pipeline import translate_ai
    monkeypatch.setattr(translate_ai, "fill_todo",
                        lambda todo, filled, sl, tl, log=print: (
                            Path(filled).write_text("Hallo Welt.\tHello world.\nGuten Morgen.\tGood morning.\n"), 2))
    cmd = [sys.executable, str(fake), "translate", "--work", str(work), "--sl", "de", "--tl", "en", "--out", str(out)]
    rc = translate_visit.main(cmd)
    assert rc == 0
    text = out.read_text()
    assert "Hello world." in text and "Good morning." in text


def test_matching_visit_wraps_translate_with_claude_fallback(tmp_path, monkeypatch):
    from dubstudio.runner import Runner
    from dubstudio import cloud
    work = tmp_path / "work"; work.mkdir()
    (work / "prep.json").write_text("{}")
    log = tmp_path / "job.log"
    ctx = {"W": str(work)}
    tr = {"id": 3, "episode_id": 1, "run": 1, "step": "translate", "status": "waiting",
          "log_path": str(log), "params": {"cmd": ["{py}", "dubstitch.py", "translate", "--work", str(work)],
                                           "ctx": ctx}}
    sent = {}
    monkeypatch.setattr(cloud, "run_chain", lambda *a, **k: sent.update(steps=[n for n, _ in a[3]]) or {"ok": True, "rc": 0})
    runner = Runner(None)
    runner._jobs_by_step = lambda j: {"translate": tr}
    runner.episode_context = lambda eid: ({"code": "TGS"}, {"id": 1, "number": 34, "work_dir": str(work)}, {})
    runner._set = lambda j, **vals: None
    runner._expand = lambda cmd: cmd
    runner._wait_for_work_files = lambda *a, **k: True
    runner._run_match(tr)
    assert sent["steps"] == ["translate"]
    # The wrapper is what the rented CPU runs, not bare dubstitch translate.
    wrapper = work / "_app" / "dubstudio" / "pipeline" / "translate_visit.py"
    assert wrapper.is_file()
