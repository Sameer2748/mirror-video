#!/usr/bin/env python3
"""TGS ep34 face map on the GPU endpoint, in a scratch work folder, with the exact command the pipeline builds
(`pipeline/steps.py`). Compare the result with the Mac's: pipeline_tools/compare_face_map.py.

    python app/scripts/run_gpu_facemap_test.py
"""
import json, shutil, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from dubstudio import cloud                                     # noqa: E402
from dubstudio.config import cfg                                # noqa: E402
from dubstudio.db import DB                                     # noqa: E402

MAC = ROOT / "library/work/TGS_ep34"
DEST = ROOT / "library/work/_gpu_facemap_test"
VIDEO = "/Users/manish/Downloads/TGS US_DE/US/TGS EPISODE 34.mp4"

shutil.rmtree(DEST, ignore_errors=True)
(DEST / "broll_library").mkdir(parents=True)
(DEST / "out").mkdir()
shutil.copy(MAC / "broll_library/broll_library.json", DEST / "broll_library/broll_library.json")
shutil.copytree(MAC / "lips_nets", DEST / "lips_nets")

db = DB(cfg.library / "dubstudio.db")
show = db.one("SELECT * FROM shows WHERE code=?", ("TGS",))
ep = dict(db.one("SELECT * FROM episodes WHERE number=34 AND show_id=?", (show["id"],)))
ep.update(work_dir=str(DEST), output_dir=str(DEST / "out"))

cmd = [cfg.face_python, f"{cfg.scripts}/facemap.py", "--video", VIDEO,
       "--library", str(DEST / "broll_library/broll_library.json"), "--lips", str(DEST / "lips_nets"),
       "--app", str(ROOT / "backend")]
print("GPU endpoint", cloud.endpoint("gpu"), flush=True)
t0 = time.time()
res = cloud.run_command(db, show, ep, "facemap", cmd, cfg, log=lambda m: print(m, flush=True),
                        timeout=3600, kind="gpu")
wall = time.time() - t0
fm = DEST / "broll_library/face_map.json"
(DEST / "job.json").write_text(json.dumps({"wall_s": round(wall, 1), "worker_s": res.get("seconds")}, indent=1))
print(f"WALL {wall:.0f} s, on the worker {res.get('seconds')} s; face_map.json {fm.exists()}")
