"""The CPU/GPU handler PUTs videos itself and keeps the return tar small."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

HANDLER = Path(__file__).resolve().parents[2] / "worker" / "handler.py"


def load_handler():
    spec = importlib.util.spec_from_file_location("worker_handler", HANDLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_split_changed_keeps_json_out_of_the_video_list(tmp_path):
    h = load_handler()
    root = tmp_path / "work"
    root.mkdir()
    (root / "prep.json").write_text("{}")
    (root / "proxy_540.mp4").write_bytes(b"v" * 64)
    before = {}
    small, large = h.split_changed(str(root), before)
    assert "prep.json" in small
    assert "proxy_540.mp4" in large
    assert h.is_large_output("rows.json", str(root / "prep.json")) is False


def test_command_puts_videos_to_loose_urls(tmp_path, monkeypatch):
    h = load_handler()
    work, out, files = tmp_path / "work", tmp_path / "out", tmp_path / "files"
    work.mkdir(); out.mkdir(); files.mkdir()
    monkeypatch.setattr(h, "WORK", str(work))
    monkeypatch.setattr(h, "OUT", str(out))
    monkeypatch.setattr(h, "FILES", str(files))
    monkeypatch.setattr(h, "wipe_dirs", lambda dirs: [Path(d).mkdir(parents=True, exist_ok=True) for d in dirs])
    monkeypatch.setattr(h, "untar_from", lambda url, dest: None)
    monkeypatch.setattr(h, "fetch", lambda url, path, tries=4: {"bytes": 1, "seconds": 0})
    puts = []

    def fake_put(url, path, ctype="application/octet-stream", tries=4):
        puts.append((url, Path(path).name, Path(path).stat().st_size))
        return 200

    monkeypatch.setattr(h, "put", fake_put)

    def run_logged(cmd, cwd=None, env=None):
        Path(h.WORK, "prep.json").write_text("{}")
        Path(h.WORK, "proxy_540.mp4").write_bytes(b"vid" * 20)
        Path(h.OUT, "final_clean.mp4").write_bytes(b"out" * 20)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(h, "run_logged", run_logged)
    job = {
        "step": "command",
        "name": "proxy",
        "cmd": ["echo"],
        "outputs": {
            "work": "https://r2/work.tar",
            "out": "https://r2/out.tar",
            "loose": {
                "work": {"proxy_540.mp4": "https://r2/put-proxy"},
                "out": {"final_clean.mp4": "https://r2/put-final"},
            },
        },
    }
    res = h.command(job)
    assert res["ok"]
    urls = [u for u, _, _ in puts]
    assert "https://r2/put-proxy" in urls
    assert "https://r2/put-final" in urls
    names = [n for _, n, _ in puts]
    assert "proxy_540.mp4" in names and "final_clean.mp4" in names
    loose = json.dumps(res["sent"]["loose"])
    assert "proxy_540.mp4" in loose and "final_clean.mp4" in loose
    work_tar = res["sent"].get("work") or {}
    assert "proxy_540.mp4" not in (work_tar.get("names") or [])


def test_command_gets_a_missing_input_named_in_the_command(tmp_path, monkeypatch):
    """Nov 9 CPU image never GETs a previous-step file. This handler must, or matching/render 404."""
    h = load_handler()
    work, out, files = tmp_path / "work", tmp_path / "out", tmp_path / "files"
    work.mkdir(); out.mkdir(); files.mkdir()
    monkeypatch.setattr(h, "WORK", str(work))
    monkeypatch.setattr(h, "OUT", str(out))
    monkeypatch.setattr(h, "FILES", str(files))
    monkeypatch.setattr(h, "wipe_dirs", lambda dirs: [Path(d).mkdir(parents=True, exist_ok=True) for d in dirs])
    monkeypatch.setattr(h, "untar_from", lambda url, dest: None)
    fetched = []

    def fake_fetch(url, path, tries=4):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"src")
        fetched.append((url, path))
        return {"bytes": 3, "seconds": 0}

    monkeypatch.setattr(h, "fetch", fake_fetch)
    monkeypatch.setattr(h, "put", lambda *a, **k: 200)
    monkeypatch.setattr(h, "run_logged", lambda cmd, cwd=None, env=None: SimpleNamespace(returncode=0, stdout="ok", stderr=""))
    dest = str(work / "input" / "ep.mp4")
    job = {
        "step": "command",
        "cmd": ["echo", dest],
        "files": {"{F0}": "https://r2/src.mp4"},
        "names": {"{F0}": "ep.mp4"},
        "place": [{"name": "ep.mp4", "url": "https://r2/src.mp4", "dests": [dest]}],
        "outputs": {"work": "https://r2/work.tar", "out": "https://r2/out.tar"},
    }
    res = h.command(job)
    assert res["ok"]
    assert fetched, "the handler never GETs the signed input"
    assert Path(dest).is_file() and Path(dest).stat().st_size > 0


def test_command_skips_a_404_for_a_file_this_visit_will_write(tmp_path, monkeypatch):
    """clipstry EP4 signed video_out that was not in R2. The handler must not die before render runs."""
    import urllib.error
    h = load_handler()
    work, out, files = tmp_path / "work", tmp_path / "out", tmp_path / "files"
    work.mkdir(); out.mkdir(); files.mkdir()
    monkeypatch.setattr(h, "WORK", str(work))
    monkeypatch.setattr(h, "OUT", str(out))
    monkeypatch.setattr(h, "FILES", str(files))
    monkeypatch.setattr(h, "wipe_dirs", lambda dirs: [Path(d).mkdir(parents=True, exist_ok=True) for d in dirs])
    monkeypatch.setattr(h, "untar_from", lambda url, dest: None)

    def fake_fetch(url, path, tries=4):
        if "ghost" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"src")
        return {"bytes": 3, "seconds": 0}

    monkeypatch.setattr(h, "fetch", fake_fetch)
    monkeypatch.setattr(h, "put", lambda *a, **k: 200)
    monkeypatch.setattr(h, "run_logged", lambda cmd, cwd=None, env=None: SimpleNamespace(returncode=0, stdout="ok", stderr=""))
    dest = str(work / "input" / "ep.mp4")
    ghost = str(work / "video_groups.mp4")
    job = {
        "step": "command",
        "cmd": ["echo", dest],
        "files": {"{F0}": "https://r2/src.mp4", "{F1}": "https://r2/ghost.mp4"},
        "names": {"{F0}": "ep.mp4", "{F1}": "video_groups.mp4"},
        "place": [
            {"name": "ep.mp4", "url": "https://r2/src.mp4", "dests": [dest]},
            {"name": "video_groups.mp4", "url": "https://r2/ghost.mp4", "dests": [ghost]},
        ],
        "outputs": {"work": "https://r2/work.tar", "out": "https://r2/out.tar"},
    }
    res = h.command(job)
    assert res["ok"]
    assert Path(dest).is_file()
