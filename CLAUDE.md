# Dub Studio — start here (for Claude Code)

You are building **Dub Studio**: a local web app on the owner's Mac that wraps an existing, working video-dubbing
pipeline (`dubstitch.py` and friends) and adds a Premiere-style editor, so an editor can review and fix an episode
before it is rendered. The owner is **Sameer**. He is not reviewing code: build it, test it, run it on a real
episode, and report what works in plain words.

Read these, in this order, before writing code:

0. `docs/07_HANDOVER.md` — **start here in a new chat**: the map, the full step list, final decisions, the owner's
   questions so far and the open items (2026-10-30).
1. `docs/01_PROJECT_CONTEXT.md` — what the pipeline does, the shows, every decision made so far and why.
2. `docs/02_PLATFORM_DESIGN.md` — the approved design (architecture, data model, editor, flags, milestones).
3. `docs/03_PROGRESS.md` — what is built, what is next. **Update it after every piece of work.**
4. `docs/04_PIPELINE_REFERENCE.md` — the run sheet per show and every data file format, with real examples.
   `docs/05_AI_GATEWAY.md` — calling Claude through the owner's AI gateway (Step 2 matching), with test results.
   `docs/06_BROLL_SCENE_EDIT.md` — the steps after `groups` (shot library, face map, scene edit with B-roll for slow
   lines, transition plan, scene-edit sheet): order, exact commands, files, rules and the done-test (2026-10-30).
   `docs/PROMPT_scene_edit_integration.md` — the task to wire those steps into the app.
5. `skill/SKILL_dub-clip-video-stitcher_v21_full.md` — the full pipeline manual (read the sections you need,
   starting with "Show profile", "Episode run sheet" and Step 4C).

## Folder map

```
dub-studioo/
  CLAUDE.md                 this file
  docs/                     context, design, progress, pipeline reference, img/ (architecture + editor drawings)
  app/                      THE PROJECT YOU BUILD
    backend/dubstudio/      FastAPI app: config, db (SQLite), presets, scan, events, runner, main (API)
    backend/dubstudio/pipeline/  steps.py (plan of commands per show), decisions.py (Step 2 helpers)
    backend/tests/          pytest (72 tests + 1 live AI; they use a fake pipeline, no video needed)
    frontend/               React + TypeScript (Vite): the screens (built to frontend/dist)
    scripts/                start.sh (launcher), setup_pipeline.sh (the pipeline's Python env → app/pipeline_env)
  pipeline_tools/           REFERENCE COPIES of the pipeline scripts (read them; do not run the app against them)
  skill/                    the pipeline manual v18–v21 (.md; v21 is current) and the packaged skills (.zip)
  examples/wbm_ep17/        a finished WBM episode: every work file (SRTs, prep, rows, decisions, spec.py,
                            manifest, manifest_groups, sentences) + the mapping/groups sheets + QC frames
  examples/tolr_ep24/       a finished TOLR episode (lip-scan show): same, plus lipsync.json, lips_range/, and the
                            Gemini-vs-our-scan and sentences-vs-clauses comparison sheets
  examples/tgs_ep34_5min/   TGS ep34 first 5 minutes for the scene edit: work/ (inputs + Claude's cached answers, shot
                            library, face map) and expected/ (the scene edit it must reproduce, the face-map answer key
                            and its scorer)
```

## Paths on the Mac (user `manish`)

| What | Path |
| --- | --- |
| This project | `/Users/manish/Downloads/dub-studioo` |
| The real pipeline (run this one) | `/Users/manish/Desktop/france-team/adaptation-tool` — scripts in `scripts/`, Whisper models in `data/models/`, Python venv in `.venv/` |
| WBM show folder (Italian dub vs US, no SRTs) | `/Users/manish/Downloads/WBM US-IT` — outputs in `outputs/ep<N>/` |
| TOLR show folder (Italian dub vs German, lip scan) | `/Users/manish/Downloads/TOLR DE_ITaly` — outputs in `output/ep<N>/` |
| TDMB show folder (Italian dub vs US) | `/Users/manish/Downloads/TDMB US-IT` — outputs in `output/ep<N>/` |
| HIH show folder (Italian dub vs US, US captions burned in, no SRTs) | `/Users/manish/Downloads/HIH US-IT` — outputs in `output/ep<N>/` |
| Old first attempt (superseded, ignore) | `/Users/manish/Downloads/dub-studio` (no second "o") |

`app/backend/dubstudio/config.py` finds these by itself (env vars `DUBSTUDIO_TOOLS`, `DUBSTUDIO_LIBRARY`,
`DUBSTUDIO_PIPELINE_PY`, `DUBSTUDIO_MODELS`, `DUBSTUDIO_PORT`, `DUBSTUDIO_CPU_SLOTS`, `DUBSTUDIO_STEP_MAX_SECONDS`
override, then `app/config.json`, then defaults). Check the paths exist before relying on them.

## Rules

- **The pipeline is the source of truth.** Matching, lip scan, groups and render stay in `dubstitch.py`. The app
  calls it as a subprocess (`pipeline/steps.py`) and reads/writes its JSON files. Do not re-implement its logic.
  If the pipeline needs a change, change it in `adaptation-tool/scripts/` (back up the old file to
  `adaptation-tool/scripts/old_versions/` first), then copy it to `pipeline_tools/` and note it in progress.
- **Decided, do not reopen** (see 01 and 03 for why): our local lip scan, not Gemini; lip threshold 0.4 s
  (`--min-len 0.4 --min-lip 0.4`); **the lip scan runs for every show**. **Every show uses the same values**
  (owner, 2026-10-20; `presets.py` constants, not editable per show): Step 0B Whisper SRTs for BOTH sides on every
  episode, each side cleaned with its script (no script → the given SRT's text is the script; neither → the
  localisation sheet's names); **whole sentences** (`--clauses 0`); groups size 7, **max drift 0.5 s**; burned subtitle
  rows of at most **7 words / 4 s** (mksrt's subtitle SRT); the source ending is kept but **stops before a closing
  fade to black** (pipeline `source_fade_ms`, also clamps the last clips). Only the subtitle style differs: TDMB strip,
  **HIH dynamic strip** (2 or 3 caption lines tall, following the US caption under it; skill Step 8C), all others box.
  Another show gets the dynamic strip only when the owner says so.
  Step 2: Claude decides the flagged rows, then **reads every row** (`ai_match.review_rows`) and then gives unused
  source lines to their neighbour (`review_unused`).
- **Every step, every episode; ask when something is down** (owner, 2026-10-22): run every pipeline step (incl. the
  lip scan). If a step cannot run (Mac runner stopped, app not running, computer not reachable, AI gateway/VPN off),
  stop, tell the owner which step and why, ask him to start it (`adaptation-tool/mac_lipscan/mac_runner.command`,
  `app/scripts/start.sh`), then continue. Never skip or work around a step silently.
- **Never touch the show folders' inputs.** Outputs go to the show's output folder with today's file names
  (see `presets.py` `names()`); the app's own work files go in its library folder.
- **Localisation sheet:** one per show, two columns `source name` | `target name` (csv/xlsx), found in the show
  folder, always sent to Claude for matching (`names.py`).
- **AI = Claude through the owner's gateway.** The key and settings are in `app/.env` (gitignored). The
  client is `app/backend/dubstudio/ai.py`. How to call it, the models and the gotchas are in
  `docs/05_AI_GATEWAY.md`. Step 2 matching by Claude is built (`pipeline/ai_match.py`). Never print, log,
  hard-code or commit the key, and never copy `.env` anywhere else. Watch the budget with `GET /api/ai/status`.
- **Mac only**, one editor, no login, everything local. Apple GPU (MPS) for Whisper and the lip scan.
- **Face map runs in its own Python** (`venv_face`: insightface, onnxruntime, ultralytics; `DUBSTUDIO_FACE_PY`). Never
  install those packages into `app/pipeline_env` or the lip-scan venv: they upgrade numpy and break mediapipe.
- **Work in milestones** (design doc "Build plan"). Each ends with its "done when" test passing on WBM ep17 and
  TOLR ep24, where the right output is already known (`examples/`).
- **Report to the owner in plain words**: what now works, what he can click, what is next. No code dumps.
- Keep `docs/03_PROGRESS.md` current: tick items, add a dated line to the build log.

## Run the tests

```bash
cd app/backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
.venv/bin/python -m pytest -q tests                                  # 72 passed, 1 skipped (live AI)
DUBSTUDIO_LIVE_AI=1 .venv/bin/python -m pytest -q tests/test_ai.py -k live   # one real gateway call
```

The tests use a fake pipeline (`tests/conftest.py`) so they run in seconds without video files.

## What to do next

Milestones 1–6 are done with their done-tests; milestone 7 is built (its done-test is Sameer running an episode alone) (see `docs/03_PROGRESS.md`). Start the app with `app/scripts/start.sh` (the pipeline runs in
`app/pipeline_env`, made by `app/scripts/setup_pipeline.sh`; the tools' own `.venv` on the Mac is empty).
`app/scripts/playback_check.py <episode id>` plays an episode in headless Chrome and measures preview sync. Next:

1. Open one `*_timeline_v*.xml` in Premiere Pro once to confirm speeds and in-points (OpenTimelineIO reads it fine).
   Editor: trims leave gaps / overwrite, V2 overlay track (03 "Editor: real trims…"); next: the owner's editor wishes.
2. Sameer runs a full episode alone (milestone 7's done-test); fix what he trips over. Settings (sidebar) shows health.
3. WBM ep9 (id 5) and ep18 (id 6, matched automatically, 0 not found) wait at "Ready for review".
   Matching now runs by itself with Claude when the AI is set up (03 "Automatic matching"); next: the owner's
   editor wishes.

## After every test run (owner, 2026-10-29)
- (Paused by the owner, 2026-10-29: do not ask about uploads for now.) Drive folder for TGS tests, if it comes back:
  https://drive.google.com/drive/u/0/folders/1wP4yDayP8yJrxDC325aZczh3OV4Onj41 ("TGS broll testing"); videos need Google
  Drive for desktop on the Mac (not installed), the connector only takes small files.
- After every B-roll change, update the "B-roll for slow clips: experiment log" doc (Claude Docs) and the skill.
