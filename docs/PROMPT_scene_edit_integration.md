# Prompt: add the B-roll scene edit + face map to Dub Studio (paste this to the coding agent)

You are working on **Dub Studio** (`/Users/manish/Downloads/dub-studioo`), the owner's local full-stack app (FastAPI +
React) that runs the dubbing pipeline step by step. The pipeline gained new steps that are tested and approved
outside the app: the **shot library**, the **face map** (local face recognition), the **scene edit** (Claude cuts the
picture with B-roll only for slow lines), **transitions from a plan** and the **scene-edit sheet**. Your job: make the app
run them, in the right order, with the same commands, so an episode made in the app is identical to one made by
`run_episode.sh`. The owner (Sameer) does not review code: build, test, run, and report in plain words.

## Read first (in this order)
1. `CLAUDE.md` (rules: the pipeline is the source of truth, never re-implement its logic, never print the AI key).
2. `docs/06_BROLL_SCENE_EDIT.md` — **the spec for this task**: step order, exact commands, environments, files, rules,
   and the done-test.
3. `pipeline_tools/run_episode.sh` — the reference runner. Every new step in the app must run exactly the command this
   script runs (same arguments, same order, same files). `scene_edit` and `use_scene` are bash/Python one-offs there:
   port them to small functions in `pipeline/steps.py` / `runner.py`, byte-for-byte the same behaviour.
4. `app/backend/dubstudio/pipeline/steps.py` (the plan), `runner.py` (how steps run), `config.py`, `presets.py`.
5. Skill `skill/SKILL_dub-clip-video-stitcher_v21_full.md`, Step 4C, when you need the why behind a rule.

## What to build
1. **Config:** `DUBSTUDIO_FACE_PY` (python of the face environment; default
   `/Users/manish/Desktop/france-team/adaptation-tool/mac_lipscan/venv_face/bin/python`), shown in Settings > health
   with a check that `import insightface, onnxruntime, ultralytics` works. Add `app/scripts/setup_face_env.sh` (Mac:
   `uv venv --python 3.12 venv_face` + `uv pip install -U insightface onnxruntime ultralytics opencv-python numpy`;
   Windows note: `onnxruntime-directml`). NEVER install these into `app/pipeline_env` or the lip-scan venv (it upgrades
   numpy and breaks mediapipe — this happened once).
2. **Steps (`steps.py`):** after `groups` add `library` -> `facemap` -> `scene_edit` -> `use_scene`, then the
   existing `review`; replace the `fastfix` step by the scene edit when the scene edit is on (it trims fast lines
   itself); after `bookends` always run `trans_plan` -> `transitions --plan W/transitions_plan.json`; after `subs_en`
   add `scene_sheet`; keep `check` and `notes` last. Labels for the UI: "Shot library (Claude labels every shot)",
   "Face map (who is on screen, who talks)", "Scene edit (Claude, B-roll for slow lines)", "Use the scene edit",
   "Transition plan", "Scene-edit sheet". Resources: `facemap` gpu, `library`/`scene_edit` need the AI.
   Make the scene edit a show-profile switch `scene_edit: true` (default true for TGS; the owner decides for other
   shows — ask him).
3. **AI gate:** before `library`, `facemap`, `scene_edit`: check the gateway (`ai.key_info()`); if unreachable or
   spend >= max_budget, stop the episode at that step with a clear message ("AI budget used up: $X of $Y — ask to raise
   it") — never skip the step. Log time + gateway spend before/after every step (like `run_episode.sh`) and put the
   table in the run notes.
4. **Editor:** the timeline opens `W/manifest_groups.json` (= the scene edit after `use_scene`). Show pieces with
   `broll: true` in a different colour ("B-roll") and `zoom: 1.08` as "repeat"; hovering a piece shows its `note`
   (Claude's reason). The automatic edit stays in `W/manifest_groups.auto.json` ("Show automatic edit" toggle,
   read-only). Edits the editor makes are saved to `manifest_groups.json` as today.
5. **Outputs page:** link the scene-edit sheet (`<show>_ep<N>_scene_edit.xlsx`), the shot library
   (`W/broll_library/broll_library.xlsx`) and the run-time table.
6. **Resume:** a rerun of `scene_edit` reuses `W/scene/scene_manifest.par_cache.json` (Claude's answers) — do not delete
   it on retry; a "Redo scene edit" button moves it to `W/scene/_old/` first (no deletes).

## Tests (all must pass before you report)
* pytest with the fake pipeline: the plan for a TGS profile contains the new steps in the order of
  `docs/06` section 1; `fastfix` absent when the scene edit is on; the AI gate stops on an unreachable gateway and on a
  used-up budget; `use_scene` keeps the automatic edit and writes the scene edit (port the test from the
  `run_episode.sh` Python).
* **Done-test (real, no AI cost):** run the app's `scene_edit` + `use_scene` on a temp copy of
  `examples/tgs_ep34_5min/work/` -> 0 Claude calls, 110 pieces, 30 B-roll, problems left 0, segments identical to
  `examples/tgs_ep34_5min/expected/scene_manifest.json` (md5 641460ec7c93d911bc714bb581e034ae).
* **Face-map test:** run the app's `facemap` step on ep34 (video `/Users/manish/Downloads/TGS US_DE/US/TGS EPISODE 34.mp4`,
  library + lip scan in `/Users/manish/Downloads/TGS US_DE/work_ep34/`), then
  `python examples/tgs_ep34_5min/expected/score_face_map.py <the new face_map.json>` ->
  people found >= 95%, on-screen speaker >= 35/37, no speaker on narration.
* **Full check:** make ep34's first 5 minutes from the app, every step, and compare with
  `TGS US_DE/output/ep34_test5min/` (B video): same pieces, picture check passes, subtitles self-check clean. Report the
  time and Claude cost of every step.

## Rules
* Do not change any pipeline logic (`sceneedit.py`, `library.py`, `facemap.py`, `dubstitch.py`, lip scan). If something
  in them must change, stop and ask the owner; the experiment log and the skill are updated with every logic change.
* No deletes in the show folders; moves to `_to_delete/` only. Never touch the show folders' inputs.
* Keep `docs/03_PROGRESS.md` current. Report to Sameer in plain words: what works, what he can click, what is next.
