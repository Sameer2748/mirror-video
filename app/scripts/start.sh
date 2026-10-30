#!/bin/bash
# Dub Studio launcher (Mac). First run: creates backend/.venv and builds the UI. Then starts the server on
# http://localhost:8765 (DUBSTUDIO_PORT overrides) and opens the browser. Ctrl-C stops it.
#   ./scripts/start.sh            start + open the browser
#   ./scripts/start.sh --no-open  start only
#   ./scripts/start.sh --rebuild  rebuild the UI even if it looks current
set -euo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
BE="$APP/backend"; FE="$APP/frontend"
PORT="${DUBSTUDIO_PORT:-$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('port',8765))" "$APP/config.json" 2>/dev/null || echo 8765)}"
OPEN=1; REBUILD=0
for a in "$@"; do
  case "$a" in --no-open) OPEN=0 ;; --rebuild) REBUILD=1 ;; *) echo "unknown option $a"; exit 2 ;; esac
done
URL="http://localhost:$PORT"
say() { echo "[dub studio] $*"; }

# already running? just open it
if curl -fsS "$URL/api/info" >/dev/null 2>&1; then
  say "already running at $URL"
  [ "$OPEN" = 1 ] && open "$URL"
  exit 0
fi
if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  say "port $PORT is used by another program; set DUBSTUDIO_PORT to another port"; exit 1
fi

# backend venv (re-install when requirements.txt changes)
if [ ! -x "$BE/.venv/bin/python" ]; then
  say "creating the Python environment (once)…"
  python3 -m venv "$BE/.venv"
fi
REQ_SUM="$(shasum "$BE/requirements.txt" | cut -d' ' -f1)"
if [ "$(cat "$BE/.venv/.req_sum" 2>/dev/null || true)" != "$REQ_SUM" ]; then
  say "installing Python packages…"
  "$BE/.venv/bin/pip" install -q --disable-pip-version-check -r "$BE/requirements.txt"
  echo "$REQ_SUM" > "$BE/.venv/.req_sum"
fi

# UI: build when missing or when a source file is newer than the build
if [ "$REBUILD" = 1 ] || [ ! -f "$FE/dist/index.html" ] || \
   [ -n "$(find "$FE/src" "$FE/index.html" "$FE/package.json" -newer "$FE/dist/index.html" 2>/dev/null | head -1)" ]; then
  if command -v npm >/dev/null 2>&1; then
    say "building the screens…"
    ( cd "$FE" && { [ -d node_modules ] || npm install --no-fund --no-audit --loglevel=error; } && npm run build --silent )
  elif [ -f "$FE/dist/index.html" ]; then
    say "npm not found; using the existing build"
  else
    say "npm not found and no build yet: install Node (brew install node) and run again"; exit 1
  fi
fi

# check the pipeline is where the config says
TOOLS="$("$BE/.venv/bin/python" -c "import sys;sys.path.insert(0,'$BE');from dubstudio.config import cfg;print(cfg.tools_dir)")"
[ -f "$TOOLS/scripts/dubstitch.py" ] || say "WARNING: pipeline not found at $TOOLS (set DUBSTUDIO_TOOLS)"
PPY="$("$BE/.venv/bin/python" -c "import sys;sys.path.insert(0,'$BE');from dubstudio.config import cfg;print(cfg.pipeline_python)")"
"$PPY" -c "import faster_whisper, sentence_transformers, openpyxl" 2>/dev/null || \
  say "WARNING: the pipeline's Python ($PPY) lacks its packages: run ./scripts/setup_pipeline.sh once"
command -v ffmpeg >/dev/null 2>&1 || say "WARNING: ffmpeg not found (brew install ffmpeg)"

if [ "$OPEN" = 1 ]; then
  ( for _ in $(seq 1 60); do curl -fsS "$URL/api/info" >/dev/null 2>&1 && { open "$URL"; break; }; sleep 0.5; done ) &
fi
say "starting at $URL (Ctrl-C to stop)"
cd "$BE"
# crash recovery: the launcher watches the server and starts it again if it stops by itself (steps resume where
# they were); exit 3 = "restart" from the Settings page. Ctrl-C stops everything.
export DUBSTUDIO_SUPERVISED=1
STOP=0; trap 'STOP=1; [ -n "${PID:-}" ] && kill -INT "$PID" 2>/dev/null' INT TERM
CRASHES=()
while true; do
  "$BE/.venv/bin/python" -m uvicorn dubstudio.main:app --host 127.0.0.1 --port "$PORT" --log-level warning --timeout-graceful-shutdown 3 &
  PID=$!; RC=0; wait "$PID" || RC=$?; PID=          # (|| : `set -e` must not end the launcher when the server dies)
  [ "$STOP" = 1 ] && { say "stopped"; exit 0; }
  if [ "$RC" = 3 ]; then say "restarting (asked from Settings)…"; continue; fi
  if [ "$RC" = 0 ]; then say "stopped"; exit 0; fi
  NOW=$(date +%s); CRASHES+=("$NOW"); RECENT=0
  for t in "${CRASHES[@]}"; do [ $((NOW - t)) -lt 600 ] && RECENT=$((RECENT + 1)); done
  if [ "$RECENT" -ge 5 ]; then say "the server stopped $RECENT times in 10 minutes (exit $RC); not restarting — see the messages above"; exit 1; fi
  say "the server stopped unexpectedly (exit $RC); starting it again in 3 s — unfinished steps continue where they were"
  sleep 3
done
