#!/usr/bin/env python3
"""Put the face-map GPU image's build inputs next to Dockerfile.gpu.

The YOLO weights and InsightFace buffalo_l are not in this repo (too big). They are taken from this machine when
it has them (the Mac's own files, the bytes the local face map uses) and otherwise from R2, where they are staged
under `worker/face-gpu/` -- which is what CI does.

    python3 app/worker/fetch_face_inputs.py
"""
import shutil, subprocess, sys, tarfile, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
REPO = HERE.parent.parent
sys.path.insert(0, str(BACKEND))
from dubstudio import storage                                   # noqa: E402

YOLO = Path.home() / "Desktop/france-team/adaptation-tool/scripts/yolo26l.pt"
BUFFALO = Path.home() / ".insightface/models/buffalo_l"


def from_r2(key, dest):
    full = storage._key(key)
    head = storage.head(full)
    if not head:
        sys.exit(f"{key} is not here and not in the bucket -- stage it first")
    if dest.exists() and dest.stat().st_size == head["size"]:
        print(f"  {dest.name}: already here ({head['size']:,} bytes)")
        return
    print(f"  {dest.name}: fetching {head['size']:,} bytes from R2 …", flush=True)
    with urllib.request.urlopen(storage.presign(full, 3600), timeout=1800) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 8 << 20)


if __name__ == "__main__":
    if YOLO.exists():
        shutil.copy2(YOLO, HERE / "yolo26l.pt")
    else:
        from_r2("worker/face-gpu/yolo26l.pt", HERE / "yolo26l.pt")
    if BUFFALO.exists():
        with tarfile.open(HERE / "buffalo_l.tar.gz", "w:gz") as t:
            t.add(BUFFALO, arcname="buffalo_l")
    else:
        from_r2("worker/face-gpu/buffalo_l.tar.gz", HERE / "buffalo_l.tar.gz")
    shutil.copy2(REPO / "pipeline_tools/facemap.py", HERE / "facemap.py")
    subprocess.run(["tar", "--exclude=__pycache__", "--exclude=*.pyc", "-czf", str(HERE / "app.tar.gz"),
                    "-C", str(BACKEND), "dubstudio"], check=True)
    for n in ("yolo26l.pt", "buffalo_l.tar.gz", "facemap.py", "app.tar.gz"):
        print(f"  {n}: {(HERE / n).stat().st_size:,} bytes")
    print("ready to build face-gpu:", HERE)
