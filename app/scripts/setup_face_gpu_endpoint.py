#!/usr/bin/env python3
"""Create the face-map GPU endpoint on RunPod and save its id as RUNPOD_GPU_ENDPOINT in app/.env.

The image (`<user>/dubstudio-worker:face-gpu`, app/worker/Dockerfile.gpu) runs the handler as its only process, so
the template needs no start command. Prints ids only, never a key.

    python app/scripts/setup_face_gpu_endpoint.py
"""
import json, sys, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from dubstudio import envfile                                   # noqa: E402

NAME = "dubstudio-face-gpu"
IMAGE = f"{envfile.get('DOCKERHUB_USER', 'sameer27488')}/dubstudio-worker:face-gpu"
GPUS = ["NVIDIA GeForce RTX 4090", "NVIDIA RTX A6000", "NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation",
        "NVIDIA GeForce RTX 3090", "NVIDIA RTX A5000", "NVIDIA L4", "NVIDIA A40"]


def rest(path, body=None, method=None):
    req = urllib.request.Request(
        f"https://rest.runpod.io/v1{path}", data=json.dumps(body).encode() if body is not None else None,
        method=method or ("POST" if body is not None else "GET"),
        headers={"Authorization": f"Bearer {envfile.get('RUNPOD_API_KEY')}", "Content-Type": "application/json",
                 "User-Agent": "dubstudio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method or 'GET'} {path} -> {e.code}: {e.read().decode()[:500]}") from e


if __name__ == "__main__":
    if not envfile.get("RUNPOD_API_KEY"):
        sys.exit("RUNPOD_API_KEY is not set")
    eps = {e["name"]: e for e in rest("/endpoints")}
    if NAME in eps:
        eid = eps[NAME]["id"]
        print(f"endpoint already there: {eid}")
    else:
        tpl = {t["name"]: t for t in rest("/templates")}.get(NAME) or rest(
            "/templates", {"name": NAME, "imageName": IMAGE, "isServerless": True, "containerDiskInGb": 40})
        print(f"template {tpl['id']} ({IMAGE})")
        eid = rest("/endpoints", {"name": NAME, "templateId": tpl["id"], "gpuTypeIds": GPUS, "gpuCount": 1,
                                  "workersMin": 0, "workersMax": 2, "idleTimeout": 10,
                                  "scalerType": "QUEUE_DELAY", "scalerValue": 4})["id"]
        print(f"endpoint {eid}")
    envfile.write({"RUNPOD_GPU_ENDPOINT": eid})
    print("app/.env RUNPOD_GPU_ENDPOINT updated")
