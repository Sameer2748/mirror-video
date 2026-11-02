# The pipeline worker image

One image that can run **any** pipeline step on a rented machine (docs/09 §8). The app sends it the command it was
going to run anyway, with this machine's paths swapped for placeholders (`dubstudio/cloud.py`).

```
Dockerfile        two pythons: the handler on the system one, the pipeline in /opt/pipeline_env
handler.py        the two job kinds: `lip_scan` (split into pieces) and `command` (any step)
fetch_inputs.py   puts the build inputs next to the Dockerfile
```

## Building it

**In CI, normally** — `.github/workflows/worker-image.yml`, run by hand from the Actions tab. The image is ~6 GB and
needs ~15 GB of free disk to build; a laptop that runs out of space mid-export corrupts the Docker VM's filesystem,
which is exactly what happened on 2026-11-02.

By hand, on a machine with the space:

```bash
python3 app/worker/fetch_inputs.py          # needs the R2_* keys
docker buildx build --platform linux/amd64 -t sameer27488/dubstudio-worker:pipeline --push app/worker
```

## The build inputs

Two come from R2 and are **never committed** (this repo is public; they are the owner's pipeline):

| in R2 | what |
| --- | --- |
| `worker/pipeline_bundle.tar.gz` | `adaptation-tool/scripts`, `third_party/TalkNet-ASD`, `third_party/mediapipe` |
| `worker/pipeline_models.tar.gz` | `faster-whisper-small` and `paraphrase-multilingual-MiniLM-L12-v2` — the only two the pipeline really uses (LaBSE, mpnet and the cross-encoder appear only in the `sanity_*` scripts, so 3.3 GB is not shipped) |

To re-stage them from the Mac:

```bash
T=~/Desktop/france-team/adaptation-tool
tar -czf bundle.tar.gz -C $T scripts third_party/TalkNet-ASD third_party/mediapipe \
    --exclude="*.pyc" --exclude="__pycache__" --exclude="old_versions" --exclude="*.zip" --exclude="*.md"
tar -czf models.tar.gz -C $T/data/models whisper models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2
# then put them at worker/pipeline_bundle.tar.gz and worker/pipeline_models.tar.gz in the bucket
```

The other two (`app.tar.gz`, `scene.tar.gz`) and the font come from this repo, so `fetch_inputs.py` makes them.
