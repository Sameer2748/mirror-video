#!/bin/bash
# The face map's own Python (insightface + onnxruntime + ultralytics). It must stay SEPARATE from the pipeline env
# (app/pipeline_env) and the lip-scan venv: these packages upgrade numpy and break mediapipe.
# Mac:     bash app/scripts/setup_face_env.sh            (needs uv: https://docs.astral.sh/uv/)
# Windows: the same with onnxruntime-directml instead of onnxruntime.
# The first face-map run downloads InsightFace buffalo_l (~290 MB, ~/.insightface/models) and yolo26l.pt (~50 MB).
set -e
DEST=${1:-/Users/manish/Desktop/france-team/adaptation-tool/mac_lipscan/venv_face}
uv venv --python 3.12 "$DEST"
uv pip install --python "$DEST/bin/python" -U insightface onnxruntime ultralytics opencv-python numpy
"$DEST/bin/python" -c "import insightface, onnxruntime, ultralytics; print('face env ready:', '$DEST')"
echo "Dub Studio uses it through DUBSTUDIO_FACE_PY or Settings (default: $DEST/bin/python)"
