#!/bin/bash
# One-time: the Python environment the pipeline runs in (app/pipeline_env, Python 3.12 because mediapipe 0.10.14
# for the lip scan needs <= 3.12). The packages are the skill's "Step 0" list plus the lip tools. ~3 GB.
set -euo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"; export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
[ -x "$APP/pipeline_env/bin/python" ] || uv venv --python 3.12 "$APP/pipeline_env"
VIRTUAL_ENV="$APP/pipeline_env" uv pip install torch torchvision "numpy<2" scipy opencv-python "mediapipe==0.10.14" \
  python_speech_features imageio-ffmpeg psutil scikit-learn scenedetect sentence-transformers openpyxl pillow \
  faster-whisper python-docx
"$APP/pipeline_env/bin/python" -c "import torch;print('torch',torch.__version__,'Apple GPU',torch.backends.mps.is_available())"
echo "PIPELINE ENV OK"
