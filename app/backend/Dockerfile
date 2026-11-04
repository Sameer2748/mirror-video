# The Dub Studio API server, for Render (docs/09 §1).
#
# This image runs the API only: accounts, shows, episodes, uploads, the queue and the editor's saves. It does NOT
# run the pipeline -- the heavy steps go to the RunPod workers (`dubstudio/cloud.py`), so no Whisper, no models and
# no GPU here. ffmpeg is still installed because a few small reads (probe, waveform) use it.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir "psycopg[binary]" redis

COPY dubstudio ./dubstudio
COPY tools ./tools
COPY pipeline_tools ./pipeline_tools

# the built screens are served by the API at / (Render can also host them as a static site; this keeps one origin,
# which means no CORS and the session cookie just works)
COPY frontend_dist /app/frontend/dist

ENV PYTHONUNBUFFERED=1 DUBSTUDIO_LIBRARY=/data/library
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS http://127.0.0.1:${PORT:-8765}/api/info || exit 1
CMD ["sh", "-c", "python -m uvicorn dubstudio.main:app --host 0.0.0.0 --port ${PORT:-8765}"]
