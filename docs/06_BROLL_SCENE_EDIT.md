# 06 — B-roll, scene edit and face map (the steps after `groups`)

Owner-approved logic as of 2026-10-30 (tested on TGS ep34, first 5 minutes). The skill
`skill/SKILL_dub-clip-video-stitcher_v21_full.md` (Step 4C) has the full manual. This file is what the app needs.
The reference runner that does every step in order is `pipeline_tools/run_episode.sh` (the live copy is
`adaptation-tool/scripts/run_episode.sh`). **The app must run the same commands in the same order.**

## 1. Step order (after the automatic edit)

| # | Step | Command (cwd = adaptation-tool/scripts, `{py}` = pipeline python, `{fpy}` = face python) | Needs | Resource |
|---|---|---|---|---|
| 1 | `lip_gpu` (exists) | `{py} lipgpu.py --video SRC --out-dir W/lips_nets` — **now also saves every face track's own TalkNet scores + boxes** (`lipfast.py`, 2026-10-29); the talking intervals are unchanged | ingest | gpu |
| 2 | `groups` (exists) | unchanged (the app's presets: size 7, max drift, `--trim-fast/--trim-to`) | lipsync | cpu |
| 3 | `library` (NEW) | `{py} {backend}/dubstudio/pipeline/library.py W W/broll_library` | groups | cpu + AI |
| 4 | `facemap` (NEW) | `{fpy} facemap.py --video SRC --library W/broll_library/broll_library.json --lips W/lips_nets --app {backend}` | library, lip_gpu | gpu (+ AI, cents) |
| 5 | `scene_edit` (NEW) | copy `W/sentences_dub.json W/sentences_src.json W/lipsync.json` and `W/manifest_groups.auto.json` (if it exists, else `W/manifest_groups.json`) into `W/scene/` as `manifest_groups.json`, then `{py} {backend}/dubstudio/pipeline/sceneedit.py W/scene W/broll_library 0 <total_s> W/scene/scene_manifest.json --parallel 8` | facemap | AI (8 calls at a time) |
| 6 | `use_scene` (NEW) | keep the automatic edit as `W/manifest_groups.auto.json` (once), write the scene edit as `W/manifest_groups.json` (stretch the last piece to `total_ms`) — the exact Python is in `run_episode.sh` | scene_edit | none |
| 7 | `review` (exists) | the editor opens `W/manifest_groups.json` = the scene edit | use_scene | — |
| 8 | `render`, `verify` (exist) | unchanged | review | cpu |
| 9 | `bookends` (exists) | `{py} dubstitch.py bookends --work W --out W/clean_noglow.mp4` — **leaves the opening out when the source lip scan shows > 0.2 s of a moving mouth in it** (needs `W/lipsync.json`) | verify | cpu |
| 10 | `trans_plan` (NEW) | `{py} {backend}/dubstudio/pipeline/scene_transitions.py W W/broll_library/broll_library.json` -> `W/transitions_plan.json` (a transition at every clip switch except the cut INTO a B-roll) | bookends | none |
| 11 | `transitions` (exists) | `{py} dubstitch.py transitions --work W --out <final clean> --plan W/transitions_plan.json` | trans_plan | cpu |
| 12 | `subs_dub`, `translate`, `subs_en` (exist) | unchanged (`--max-lead 0`) | transitions | cpu |
| 13 | `scene_sheet` (NEW) | `{py} {backend}/dubstudio/pipeline/sceneedit_sheet.py W/scene/scene_manifest.edl.json W/broll_library/broll_library.json <en srt> OUT/<show>_ep<N>_scene_edit.xlsx "<SHOW> ep<N>: scene edit"` | subs_en | none |
| 14 | `qc` (NEW) | `{py} {backend}/dubstudio/pipeline/qc_final.py --work W --library W/broll_library --videos <final clean> <dub subs> <en subs> --en-srt <en srt> --app {backend} --out OUT/<show>_ep<N>_qc.xlsx` | scene_sheet | cpu + AI (cents) |
| 15 | `check` (exists) | unchanged | qc | cpu |

* `fastfix` does **not** run when the scene edit runs: the scene edit trims fast lines itself (above 1.25x a shorter part
  of the line's own footage). Keep `fastfix` only for an episode run without the scene edit.
* The AI gateway must be reachable before `library`, `facemap`, `scene_edit` (stop and tell the owner if not: VPN,
  budget — `GET /key/info` spend vs max_budget).
* Time + spend of every step is reported to the owner after every episode (`run_report.py` table).

## 2. Environments

* Pipeline python (`app/pipeline_env`): unchanged.
* **Face python (NEW, never mix into the lip-scan / pipeline env — it upgrades numpy and breaks mediapipe):**
  `uv venv --python 3.12 venv_face && uv pip install --python venv_face/bin/python -U insightface onnxruntime ultralytics opencv-python numpy`
  (Windows: `onnxruntime-directml` instead of `onnxruntime`). On the Mac it is
  `/Users/manish/Desktop/france-team/adaptation-tool/mac_lipscan/venv_face`. First run downloads InsightFace
  `buffalo_l` (~290 MB, `~/.insightface/models`) and `yolo26l.pt` (~50 MB, cwd). Config: `DUBSTUDIO_FACE_PY`.

## 3. Files (all in the work folder W)

| File | Made by | What |
|---|---|---|
| `lips_nets/range_*.json` | lip_gpu | per 15 s: `tracks[]` each with `f0` (s), `score[]`, `visual[]`, `boxes[]` per frame (25 fps, 540-px frame) + the old summary fields |
| `broll_library/broll_library.json` | library | `cast` (name, look, places) + `shots[]`: n, start, end, length, people, place, type (talking/reaction/neutral/action/object/establishing), action, broll yes/no, why, lips (s of moving mouth), scene, location ("mixed" = flashback, never B-roll), used, text |
| `broll_library/_frames/shot_NNN.jpg` | library | the 3 frames per shot Claude sees |
| `broll_library/face_map.json` | facemap | `shots{n: {persons[{name, pos left/centre/right, view face/profile/back, from_s, to_s, frames, how}], talking[{name, from_s, to_s}]}}`, `groups`, timing |
| `scene/line_places.json` | scene_edit (cached) | every dub line -> story scene + location |
| `scene/speakers.json` | scene_edit (cached) | who SAYS every dub dialogue line (source names; "narration" for a thought) |
| `scene/gap_plan.json` | scene_edit (cached) | every not-matched line: show talking/reaction/action/neutral, who, suggested shots |
| `scene/scene_manifest.par_cache.json` | scene_edit | Claude's answers (a rerun with the same inputs costs $0) |
| `scene/scene_manifest.json` | scene_edit | the edit list (same format as manifest_groups; pieces carry `broll: true`, `zoom: 1.08` for a repeat) |
| `scene/scene_manifest.edl.json` | scene_edit | every stretch: lines, shots, the EDL with Claude's reasons, every check round, stats (calls, tokens) |
| `transitions_plan.json` | trans_plan | where transitions go |

## 4. The rules (code-checked in `sceneedit.py`; do not change without the owner)

* Own footage first: every line plays what the automatic match gives it. **B-roll only for slow lines** (own footage
  below 0.8x), only for the seconds own footage cannot fill at 0.8x. Fast lines (> 1.25x) are trimmed, normal lines none.
* B-roll = reaction / neutral / establishing of the line's location; never a moving mouth over narration; repeats 8 s
  apart, zoomed 8%; every B-roll **at least 2.0 s** — a shorter one is lengthened by code, the time taken from the clip
  before it (that clip plays faster, above 1.25x it shows a shorter part).
* **Speaker:** a moving mouth during a dialogue line must be its speaker's (face map decides who talks). A line whose
  own footage shows someone else talking gets a SPEAKER shot (its speaker talking, same place, 30 s from other uses).
* **Groups:** lines that Step 2 grouped share their footage; Claude places it in story order (an action on the line
  that tells it); speed and B-roll allowance per group; a stretch never cuts through a group.
* **Not-matched lines:** the gap plan decides who is on screen and how (a spoken line gets its speaker's lip clip;
  narration a reaction / the exact event); never another line's footage. Code checks the planned person is on screen.
* Location guard, dialogue starts on a moving mouth (0.3 s), stretches of ~30 s cut at scene changes, 8 in parallel,
  whole-episode check, **the best correction round is kept**.

## 5. Done-test (output must be the same)

`examples/tgs_ep34_5min/` holds the ep34 first-5-minute inputs + Claude's cached answers + the expected result.
Copy `work/` to a temp folder and run step 5 on it:
`{py} sceneedit.py <tmp>/scene <tmp>/broll_library 0 297.68 <tmp>/scene/scene_manifest.json --parallel 8`
-> 0 Claude calls, 110 pieces, 30 B-roll, "problems left 0", and `segments` **identical** to
`expected/scene_manifest.json` (md5 641460ec7c93d911bc714bb581e034ae; checked 2026-10-30). The face map is checked with
`expected/score_face_map.py` against `expected/face_answer_key_40_shots.json` (expect: people 98%, on-screen speaker
36/37, no speaker on narration). The full ep34 5-min videos (A/B) are in `TGS US_DE/output/ep34_test5min/`.

Reference numbers (ep34, 5 min): face map 7.6 min for the whole episode ($0.003); scene edit 8 min, $4.81 (38 calls);
renders + transitions + subtitles about 12 min.

## 6. Rules added 2026-10-30

* One B-roll shot at most **3 times** in the episode; a place's B-roll shots are used **in turn** (A, B, A, B), never the
  same shot twice in a row when another of that place exists. Speaker shots: no 30-s gap any more, used in turn, never
  right after themselves. Code-checked per stretch and for the whole episode.
* Every output video at **25 fps** (`dubstitch.py out_fps`, config `out_fps` overrides).
* **Final QC** (`qc_final.py` -> `<show>_ep<N>_qc.xlsx`, PASS/WARN/FAIL): 1 B-roll repeats <= 3, 2 shots on screen >= 2 s
  (B-roll FAIL, clip WARN), 3 story sync (Claude, text only), 4 no black frames >= 0.1 s, 5 25 fps, 6 speaker (face map).
  ep34 5-min run: about 47 s, $0.05.
