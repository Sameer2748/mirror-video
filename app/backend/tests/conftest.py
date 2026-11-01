import shutil
import json, os, subprocess, sys, textwrap, time
from pathlib import Path
import pytest

ADMIN = ("owner", "owner-password")          # the account every test signs in with (made at startup from the env)

FAKE_DUBSTITCH = r'''
import json, os, sys, time
from pathlib import Path
a = sys.argv[1:]; cmd = a[0]
def arg(k, d=None):
    return a[a.index(k) + 1] if k in a else d
W = Path(arg("--work", ".")); W.mkdir(parents=True, exist_ok=True)
print(f"fake {cmd}", flush=True)
if cmd == "words":
    (W / f"sentences_{arg('--side')}.json").write_text("{}"); print("3/3 chunks"); 
elif cmd == "analyze":
    prep = {"dub": [{"text": "Ciao", "start_ms": 0, "end_ms": 900}, {"text": "Come stai", "start_ms": 1000, "end_ms": 1900},
                    {"text": "Bene", "start_ms": 2900, "end_ms": 2900}],
            "src": [{"text": "Hello", "start_ms": 0, "end_ms": 900}, {"text": "How are you", "start_ms": 1000, "end_ms": 1900}],
            "auto": {"0": {"src": 0, "kind": "DP match"}, "1": {"src": 1, "kind": "Case A reuse"}}}
    (W / "prep.json").write_text(json.dumps(prep)); (W / "decisions.json").write_text(json.dumps({"groups": []}))
    print("2 rows must be read by meaning")
elif cmd == "groups":
    segs = [dict(kind="clip", it_idx=k, it_start_ms=k * 1000.0, it_end_ms=(k + 1) * 1000.0, us_start_ms=k * 900.0,
                 us_end_ms=k * 900.0 + 900.0, it_text=f"riga {k}", us_text=f"line {k}", stretch_factor=1.111, note="")
            for k in range(3)]
    (W / "manifest_groups.json").write_text(json.dumps(dict(total_ms=3000, segments=segs, bleed_report=[], groups=[])))
elif cmd == "render":
    m = json.loads((W / "manifest_groups.json").read_text()) if (W / "manifest_groups.json").exists() else {}
    print("rendering", json.dumps(m.get("dub_studio")), flush=True)
    for k in range(1, 5):
        print(f"rendered {k}/4 segments", flush=True); time.sleep(0.05)
elif cmd == "subs":
    flag = W / ("subs_round_" + Path(arg("--out")).name)
    if not flag.exists():
        flag.write_text("1"); print("chunk 1 done"); print("not finished -- run again (it resumes)"); sys.exit(3)
    Path(arg("--out")).write_text("video"); print("saved")
elif cmd == "translate":
    Path(arg("--out")).write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n")
elif cmd == "bookends":
    Path(arg("--out")).write_text("video")
elif cmd == "transitions":
    Path(arg("--out")).write_text("video"); print("transitions done")
elif cmd == "sheet":
    Path(arg("--out")).write_text("xlsx")
elif cmd == "lipsync":
    (W / "lipsync.json").write_text(json.dumps({"rows": {}, "intervals": []})); print("0 lip rows")
elif cmd == "dynsrt":
    Path(arg("--out")).write_text("1\n00:00:00,000 --> 00:00:01,000\nCiao a tutti\ncome state\n")
elif cmd == "captions":
    (W / "src_captions.json").write_text(json.dumps({"cards": [], "geometry": {}}))
elif cmd == "check":
    print("FINAL CHECK PASSED")
'''
FAKE_MKSRT = r'''
import sys
from pathlib import Path
a = sys.argv[1:]; out = Path(a[a.index("--out") + 1]); out.parent.mkdir(parents=True, exist_ok=True)
for k in range(0, 3):
    print(f"  heard 10 words in {k*1}-{k*1+1}s", flush=True)
out.write_text("1\n00:00:00,000 --> 00:00:01,000\nCiao\n")
Path(str(out).replace(".srt", "_sentences.srt")).write_text("1\n00:00:00,000 --> 00:00:01,000\nCiao\n")
print("done")
'''


# the scene-edit scripts (the real ones call Claude and the face models): enough to run the steps in order
FAKE_LIBRARY = r'''
import json, sys
from pathlib import Path
out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
(out / "broll_library.json").write_text(json.dumps({"cast": [], "shots": [{"n": 1, "start": 0, "end": 3, "broll": "yes"}]}))
(out / "broll_library.xlsx").write_text("xlsx"); print("1 shots labelled")
'''
FAKE_FACEMAP = r'''
import json, sys
from pathlib import Path
a = sys.argv[1:]; lib = Path(a[a.index("--library") + 1]).parent
(lib / "face_map.json").write_text(json.dumps({"shots": {}})); print("face map: 1 shots")
'''
FAKE_SCENEEDIT = r'''
import json, sys
from pathlib import Path
SW, LIB, A0, A1, OUT = sys.argv[1:6]
m = json.loads((Path(SW) / "manifest_groups.json").read_text()); segs = m["segments"]
segs[-1] = dict(segs[-1], broll=True, zoom=1.08, note="scene edit: B-roll for a slow line")
(Path(SW) / "scene_manifest.par_cache.json").write_text("{}")
Path(OUT).write_text(json.dumps(dict(m, segments=segs)))
Path(OUT.replace(".json", ".edl.json")).write_text(json.dumps({"stretches": [], "window": [float(A0), float(A1)]}))
print(f"{len(segs)} pieces, 1 B-roll, problems left 0")
'''
FAKE_TRANS = r'''
import json, sys
from pathlib import Path
W = Path(sys.argv[1]); m = json.loads((W / "manifest_groups.json").read_text())["segments"]
cuts = {str(round(b["it_start_ms"])): ("none" if b.get("broll") else "auto") for a, b in zip(m, m[1:])}
(W / "transitions_plan.json").write_text(json.dumps({"default": "none", "cuts": cuts})); print(len(cuts), "clip switches")
'''
FAKE_QC = r'''
import sys
from pathlib import Path
a = sys.argv[1:]; Path(a[a.index("--out") + 1]).write_text("xlsx"); print("QC: 6 checks, 6 PASS")
'''
FAKE_SHEET = r'''
import sys
from pathlib import Path
Path(sys.argv[4]).write_text("xlsx"); print("scene-edit sheet")
'''

FAKE_LIP = r'''
import json, sys
from pathlib import Path
a = sys.argv[1:]
out = Path(a[a.index("--out-dir") + 1] if "--out-dir" in a else a[a.index("--out") + 1]); out.mkdir(parents=True, exist_ok=True)
(out / "ranges.json").write_text("[]"); print("1/1 pieces")
'''


@pytest.fixture(autouse=True)
def _fresh_postgres():
    """When the suite runs on Postgres (DATABASE_URL set), every test starts with an empty database — the same
    isolation a new SQLite file gives."""
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith(("postgres://", "postgresql://")):
        import psycopg
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    yield


@pytest.fixture()
def env(tmp_path, monkeypatch):
    tools = tmp_path / "tools"; (tools / "scripts").mkdir(parents=True)
    (tools / "scripts" / "dubstitch.py").write_text(FAKE_DUBSTITCH)
    (tools / "scripts" / "mksrt.py").write_text(FAKE_MKSRT)
    for t in ("lipgpu.py", "lipmesh.py", "lipmerge.py"):
        (tools / "scripts" / t).write_text(FAKE_LIP)
    (tools / "scripts" / "facemap.py").write_text(FAKE_FACEMAP)
    (tools / "scripts" / "run_report.py").write_text("import sys; open(sys.argv[2], 'w').write('| # | Step |')\n")
    scene = tmp_path / "scene_scripts"; scene.mkdir()
    for name, body in (("library.py", FAKE_LIBRARY), ("sceneedit.py", FAKE_SCENEEDIT),
                       ("scene_transitions.py", FAKE_TRANS), ("sceneedit_sheet.py", FAKE_SHEET), ("qc_final.py", FAKE_QC)):
        (scene / name).write_text(body)
    monkeypatch.setenv("DUBSTUDIO_SCENE_DIR", str(scene))
    monkeypatch.setenv("DUBSTUDIO_FACE_PY", sys.executable)
    from dubstudio import runner as runner_mod                      # the tests' fake steps need no AI gateway
    monkeypatch.setattr(runner_mod.Runner, "ai_gate", staticmethod(lambda: None))
    for t in ("mkdec.py", "merged.py"):                    # the skill's own Step 2 helpers (pure Python, no video)
        shutil.copy(Path(__file__).parents[3] / "pipeline_tools" / t, tools / "scripts" / t)
    show = tmp_path / "SHOW US-IT"; (show / "US").mkdir(parents=True); (show / "italy").mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x160:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=duration=3", "-shortest", str(show / "US" / "SHOW Ep - 17.mp4")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=3",
                    str(show / "italy" / "Episode 17 - Prova.mp3")], check=True)
    (show / "italy" / "Episode 17 - Prova.txt").write_text(
        "Ciao, come stai? Non lo so. Questo è il copione di prova che non finisce mai, per la storia di una "
        "ragazza che si chiama Viola e che con la sua amica va a scuola. " * 3)
    monkeypatch.setenv("DUBSTUDIO_LIBRARY", str(tmp_path / "lib"))
    monkeypatch.setenv("DUBSTUDIO_TOOLS", str(tools))
    monkeypatch.setenv("DUBSTUDIO_PIPELINE_PY", sys.executable)
    monkeypatch.setenv("DUBSTUDIO_MODELS", str(tmp_path / "models"))
    # accounts: the tests sign in with their own owner account, and the real app/.env is never read (so no test can
    # reach the owner's AI gateway, his R2 bucket or his admin password)
    import dubstudio.envfile as envfile_mod
    monkeypatch.setattr(envfile_mod, "ROOT", tmp_path)
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("ADMIN_USERNAME", ADMIN[0])
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN[1])
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_ENDPOINT", "R2_PREFIX"):
        monkeypatch.delenv(k, raising=False)
    # the tests never see the owner's AI key (app/.env): no real gateway calls, no cost; tests that need the AI stub it
    if not os.environ.get("DUBSTUDIO_LIVE_AI"):
        import dubstudio.ai as ai_mod
        monkeypatch.setattr(ai_mod, "ROOT", tmp_path)
        import dubstudio.gen as gen_mod
        monkeypatch.setattr(gen_mod, "ROOT", tmp_path)                 # nor the fal.ai key
        for k in ("AI_BASE", "AI_KEY", "AI_EMAIL", "FAL_KEY"):
            monkeypatch.delenv(k, raising=False)
    import dubstudio.config as c
    c.reload()
    for m in ("dubstudio.main", "dubstudio.runner", "dubstudio.scan", "dubstudio.pipeline.steps"):
        mod = sys.modules.get(m)
        if mod is not None and hasattr(mod, "cfg"):
            mod.cfg = c.cfg
    from fastapi.testclient import TestClient
    import dubstudio.main as main
    with TestClient(main.app) as client:
        r = client.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
        assert r.status_code == 200, r.text                  # every test runs as the owner's account, signed in
        yield {"client": client, "show": show, "tmp": tmp_path, "admin": ADMIN}


def wait_for(fn, timeout=30):
    t = time.time()
    while time.time() - t < timeout:
        r = fn()
        if r:
            return r
        time.sleep(0.2)
    raise AssertionError("timed out")


def press_render(c, eid, timeout=30):
    """What the editor does at the end of review: open the timeline as it is and press Render."""
    wait_for(lambda: c.get(f"/api/episodes/{eid}").json()["status"] == "review", timeout)
    tl = c.get(f"/api/episodes/{eid}/timeline").json()["timeline"]
    r = c.post(f"/api/episodes/{eid}/timeline/render", json=tl)
    assert r.status_code == 200, r.text
