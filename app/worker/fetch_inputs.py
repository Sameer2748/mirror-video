#!/usr/bin/env python3
"""Put the worker image's build inputs next to the Dockerfile.

Two of them are too big for the repo -- and the repo is public, while the pipeline scripts, TalkNet and the models
are the owner's -- so they live in R2 and are fetched with the bucket's keys (`R2_*` in the environment, which in CI
come from repository secrets). The other two are made from the repo itself.

    python app/worker/fetch_inputs.py          # into app/worker/
"""
import os, shutil, subprocess, sys, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
sys.path.insert(0, str(BACKEND))
from dubstudio import storage                                   # noqa: E402

FROM_R2 = (("worker/pipeline_bundle.tar.gz", "bundle.tar.gz"),   # the pipeline scripts + TalkNet + mediapipe
           ("worker/pipeline_models.tar.gz", "models.tar.gz"))   # faster-whisper-small + MiniLM, the two in real use


def fetch(key, name):
    dest = HERE / name
    full = storage._key(key)
    head = storage.head(full)
    if not head:
        sys.exit(f"{key} is not in the bucket -- stage it first (app/worker/README.md)")
    if dest.exists() and dest.stat().st_size == head["size"]:
        print(f"  {name}: already here ({head['size']:,} bytes)")
        return
    url = storage.presign(full, 3600)
    print(f"  {name}: fetching {head['size']:,} bytes …", flush=True)
    with urllib.request.urlopen(url, timeout=1800) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 8 << 20)
    print(f"  {name}: {dest.stat().st_size:,} bytes")


def tar(dest, cwd, what):
    subprocess.run(["tar", "--exclude=__pycache__", "--exclude=*.pyc", "-czf", str(HERE / dest), "-C", str(cwd), what],
                   check=True)
    print(f"  {dest}: {(HERE / dest).stat().st_size:,} bytes")


if __name__ == "__main__":
    if not storage.configured():
        sys.exit("R2 is not set up: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET")
    for key, name in FROM_R2:
        fetch(key, name)
    tar("app.tar.gz", BACKEND, "dubstudio")                      # the app's own package (facemap/qc import it)
    tar("scene.tar.gz", BACKEND / "dubstudio", "pipeline")       # the scene-edit scripts
    shutil.copy(HERE.parent / "assets/fonts/Poppins-Bold.ttf", HERE / "Poppins-Bold.ttf")
    print("  Poppins-Bold.ttf: copied")
    print("ready to build:", HERE)
