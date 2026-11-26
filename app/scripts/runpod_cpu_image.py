#!/usr/bin/env python3
"""Point the CPU serverless endpoint at a Docker image and optionally park / restore workers.

The CPU template was pinned to an old digest, so Hub :pipeline pushes never reached RunPod.
GPU already tracks a floating tag. This is the CPU equivalent.

    python app/scripts/runpod_cpu_image.py --image sameer27488/dubstudio-worker:pipeline
    python app/scripts/runpod_cpu_image.py --stop-workers
    python app/scripts/runpod_cpu_image.py --workers-max 14
"""
import argparse, json, os, sys, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from dubstudio import envfile  # noqa: E402

GQL = "https://api.runpod.io/graphql"
IMAGE_DEFAULT = "sameer27488/dubstudio-worker:pipeline"


def gql(query, variables=None):
    key = envfile.get("RUNPOD_API_KEY")
    if not key:
        if os.environ.get("RUNPOD_SKIP_IF_UNSET"):
            print("no RUNPOD_API_KEY; skip RunPod retarget")
            sys.exit(0)
        sys.exit("RUNPOD_API_KEY is not set")
    body = {"query": query, "variables": variables or {}}
    req = urllib.request.Request(
        GQL, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "dubstudio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode()[:800]) from e
    if data.get("errors"):
        raise RuntimeError(data["errors"][0].get("message") or data["errors"])
    return data["data"]


def endpoint_id():
    return envfile.get("RUNPOD_CPU_ENDPOINT") or sys.exit("RUNPOD_CPU_ENDPOINT is not set")


def load_endpoint():
    q = """
    query($id: String!) {
      myself {
        endpoint(id: $id) {
          id name gpuIds workersMax workersMin idleTimeout scalerType scalerValue
          template {
            id name imageName containerDiskInGb volumeInGb isServerless dockerArgs
            isPublic startJupyter startSsh startScript ports readme category
          }
        }
      }
    }
    """
    ep = gql(q, {"id": endpoint_id()})["myself"]["endpoint"]
    if not ep or not ep.get("template"):
        sys.exit(f"no CPU endpoint {endpoint_id()}")
    return ep


def save_template(tpl, image):
    q = """
    mutation {
      saveTemplate(input: {
        id: %s
        name: %s
        imageName: %s
        containerDiskInGb: %s
        volumeInGb: %s
        isServerless: true
        dockerArgs: %s
        startSsh: %s
        env: []
      }) { id imageName }
    }
    """ % (
        json.dumps(tpl["id"]),
        json.dumps(tpl["name"]),
        json.dumps(image),
        int(tpl.get("containerDiskInGb") or 40),
        int(tpl.get("volumeInGb") or 0),
        json.dumps(tpl.get("dockerArgs") or ""),
        "true" if tpl.get("startSsh") else "false",
    )
    return gql(q)["saveTemplate"]


def save_workers(ep, workers_max, workers_min=0):
    gpu = ep.get("gpuIds") or ""
    q = """
    mutation {
      saveEndpoint(input: {
        id: %s
        name: %s
        templateId: %s
        gpuIds: %s
        workersMax: %s
        workersMin: %s
      }) { id workersMax workersMin }
    }
    """ % (
        json.dumps(ep["id"]),
        json.dumps(ep["name"]),
        json.dumps(ep["template"]["id"]),
        json.dumps(gpu),
        int(workers_max),
        int(workers_min),
    )
    return gql(q)["saveEndpoint"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="Docker image (repo:tag). Unpins any digest.")
    ap.add_argument("--stop-workers", action="store_true", help="workersMax=0 so current boxes die")
    ap.add_argument("--workers-max", type=int, help="restore / set workersMax")
    args = ap.parse_args()
    ep = load_endpoint()
    tpl = ep["template"]
    print(f"endpoint {ep['id']} workers {ep['workersMin']}-{ep['workersMax']}")
    print(f"template {tpl['id']} image {tpl['imageName']}")
    if args.stop_workers:
        out = save_workers(ep, 0, 0)
        print(f"workers parked {out}")
        ep = load_endpoint()
    if args.image:
        image = args.image.split("@", 1)[0]
        out = save_template(ep["template"], image)
        print(f"image now {out['imageName']}")
    if args.workers_max is not None:
        ep = load_endpoint()
        out = save_workers(ep, args.workers_max, 0)
        print(f"workers restored {out}")
    if not (args.stop_workers or args.image or args.workers_max is not None):
        sys.exit("pass --image, --stop-workers, and/or --workers-max")


if __name__ == "__main__":
    main()
