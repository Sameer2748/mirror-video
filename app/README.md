# Dub Studio

Local platform for the dub-clip pipeline: shows → episodes → processing → editor → render.
Context, design and progress: ../CLAUDE.md and ../docs/ (progress tracker: ../docs/03_PROGRESS.md).

## Run (Mac)
    ./scripts/setup_pipeline.sh # once: the pipeline's Python 3.12 environment in pipeline_env/ (~1.6 GB, Whisper,
                                # matching models' libraries, lip scan); the adaptation-tool .venv on the Mac is empty
    ./scripts/start.sh          # creates backend/.venv and builds the UI when needed, starts http://localhost:8765, opens it
    Dub Studio.command          # the same, by double-click in Finder

Develop the screens: `cd frontend && npm run dev` (port 5173, talks to the backend on 8765).

## Layout
    backend/dubstudio/          FastAPI app, SQLite, job runner, pipeline step adapters
    backend/tests/              pytest
    frontend/                   React + TypeScript (Vite); `npm run build` → frontend/dist (served by FastAPI)
    scripts/                    start.sh (launcher), setup_pipeline.sh (pipeline environment)
    pipeline_env/               the pipeline's Python (gitignored; config.py uses it before adaptation-tool/.venv)
    library/                    the app's database and per-episode work folders (created on first run)
    config.json                 paths: pipeline tools folder, pipeline python, library (optional; defaults are detected)
