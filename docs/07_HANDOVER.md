# 07 — Handover (2026-10-30)

Written at the end of a long Claude Code session so a **new chat (another account)** can pick up exactly where this one
stopped. Read this first, then `CLAUDE.md`, then the docs it lists. Nothing here replaces those docs: this file is
the map and the "where are we now"; the details live in 01–06 and `03_PROGRESS.md`.

---

## 1. What Dub Studio is, in one paragraph

A local web app on the owner's Mac (owner: **Sameer**, Mac user `manish`) that dubs TV episodes: it takes a foreign-language
dub audio (+ script) and the source-language video of the same episode, finds which source footage belongs under each
dub line, and renders a new cut of the source video timed to the dub, with burned subtitles. The heavy lifting is an
existing pipeline (`dubstitch.py` and friends in `/Users/manish/Desktop/france-team/adaptation-tool/scripts`); the app
runs every pipeline step as a job, uses Claude (through the owner's AI gateway) where judgement is needed, and gives a
Premiere-style editor to review and fix before rendering.

## 2. Where everything is

| What | Where |
| --- | --- |
| This project (docs, app, examples) | `/Users/manish/Downloads/dub-studioo` |
| Start the app | `app/scripts/start.sh` → http://127.0.0.1:8765 (restart: `POST /api/restart`, refused while a step runs) |
| Backend (FastAPI + SQLite) | `app/backend/dubstudio/` — `main.py` API, `runner.py` job runner, `pipeline/steps.py` step plan |
| Frontend (React + Vite) | `app/frontend/src/pages/` — Shows, NewShow, NewEpisode, Processing, Matching, Editor, GenPanel, Renders, Settings |
| Tests | `cd app/backend && .venv/bin/python -m pytest -q tests` → **123 passed, 4 skipped** (fake pipeline, seconds) |
| Real pipeline (run this one) | `/Users/manish/Desktop/france-team/adaptation-tool/scripts` (reference copies in `pipeline_tools/`) |
| Pipeline Python | `app/pipeline_env` (made by `app/scripts/setup_pipeline.sh`) |
| Face-map Python (separate!) | `adaptation-tool/mac_lipscan/venv_face` (made by `app/scripts/setup_face_env.sh`) |
| Scene-edit scripts (Step 4C) | `app/backend/dubstudio/pipeline/` — `library.py`, `sceneedit.py`, `scene_transitions.py`, `sceneedit_sheet.py`, `qc_final.py` |
| AI key + settings | `app/.env` (never print / log / copy / commit it) |
| Pipeline manual (skill) | `skill/SKILL_dub-clip-video-stitcher_v21_full.md` (v21 is current) |
| Accounts + cloud storage (2026-10-30) | `docs/08_ACCOUNTS_AND_STORAGE.md` — sign-in, own shows per account, R2 |
| Show folders (inputs: never touch) | `~/Downloads/WBM US-IT`, `TOLR DE_ITaly`, `TDMB US-IT`, `HIH US-IT`, `TGS US_DE` |
| Known-good examples | `examples/wbm_ep17`, `examples/tolr_ep24`, `examples/tgs_ep34_5min` (scene-edit done-test) |

## 3. The full step list (what runs for EVERY show, every episode)

Built by `pipeline/steps.py` `plan()`. Checked 2026-10-30 for TGS, TOLR, WBM, HIH, TDMB and an unknown new show code:
all get the same 34 steps (HIH 36).

```
ingest > mksrt_dub > mksrt_src > [HIH: captions] > proxy > sharpness > words_src > words_dub > analyze > merged >
decisions (Claude) > sheet > manifest > lip_gpu > lip_mesh > lip_merge > lipsync > groups >
library > facemap > scene_edit (Claude) > use_scene > review (editor gate) > [HIH: dynsrt] >
render > verify > bookends > trans_plan > transitions > subs_dub > translate (Claude) > subs_en >
scene_sheet > qc (Claude) > check > notes
```

- **Steps 0–4 (matching)**: Whisper SRTs for both sides, cleaned with the scripts; whole sentences; Claude decides the
  flagged rows, reads every row, gives unused source lines to neighbours; lip scan; groups (size 7, max drift 0.5 s).
- **Step 4C (scene edit, added 2026-10-30, mandatory for every show)**: shot library → face map (own Python, GPU) →
  Claude cuts each stretch as a scene (B-roll ONLY for slow lines, never a talking mouth over narration, speaker
  check, location guard) → `use_scene` makes it the edit → the editor reviews it (B-roll green, "repeat" tag on zoomed
  reuse, Claude's reason on hover, "Automatic edit" row, ↻ Redo scene edit in Versions).
- **After render**: bookends → transition plan (scene_transitions.py + the editor's own picks on top) → transitions
  → subtitles (dub + translated EN, `--max-lead 0`) → scene-edit sheet → **Final QC** (`<show>_ep<N>_qc.xlsx`, PASS / WARN
  / FAIL, never stops the run) → final check → notes (incl. `run_times.md`: seconds and $ per step).
- Every AI step first checks the gateway (`Runner.ai_gate`): VPN off → "turn the VPN on, then Retry"; budget used up →
  "ask to raise it". **The VPN must be on for a whole run** (`sceneedit.py` checks the gateway even when every
  answer is cached).
- Every output video is **25 fps** (from `dubstitch.py` `out_fps`).
- `fastfix` / `slowfix` no longer run (replaced by the scene edit).

## 4. Decisions that are FINAL (do not reopen)

From `CLAUDE.md` "Rules" and `01_PROJECT_CONTEXT.md` — short list:
- Our local lip scan (not Gemini), threshold 0.4 s; lip scan runs for every show.
- Same values for every show (`presets.py` constants): Whisper SRTs both sides, whole sentences (`--clauses 0`),
  groups 7 / drift 0.5 s, subtitle rows ≤ 7 words / 4 s, source ending stops before a closing fade to black.
- Subtitle style is the only per-show difference: TDMB strip, HIH dynamic strip, TGS strip, everyone else box.
- Step 4C (library, face map, scene edit, transition plan, scene sheet) + Final QC are mandatory for every show.
- B-roll only for slow lines; at most 3 uses per shot; shots used in turn; speaker shots rotate (sceneedit.py, 2026-10-30).
- Every step every episode; if something is down (VPN, Mac runner, app) stop and tell the owner — never skip silently.
- Never touch show-folder inputs; never delete in show folders (move to `_to_delete/`).
- Do not change pipeline logic (sceneedit.py, library.py, facemap.py, dubstitch.py, lip scan) without asking the owner.
- Face-map packages never go into `app/pipeline_env` or the lip-scan venv (they break mediapipe).
- **AI video generation is ON HOLD** (owner, 2026-10-30). The code stays (see §6) but is not a step.

## 5. What the owner asked for, in order (the main-logic questions), and what came of it

| Owner asked | Result |
| --- | --- |
| Wrap the pipeline in a local app with a Premiere-style editor | Milestones 1–6 done; 7 built (its test = Sameer runs an episode alone) |
| Claude does Step 2 matching automatically | Done (`ai_match.py`: decide flagged → review every row → unused lines) |
| One localisation sheet per show, always sent to Claude | Done (`names.py`) |
| Lip scan always on, same values for every show | Done, locked in `presets.py` |
| Transitions per cut, trims / gaps / V2 track, not-found = empty gap, project bin | Done (see 03 sections of 2026-10-16/20) |
| Voice ran ahead of picture / preview sync | Fixed; `playback_check.py` measures it |
| Fast-clip rule: nothing faster than 1.25x | Done (2026-10-22), check added 2026-10-26 |
| HIH dynamic strip subtitles | Done (2026-10-22/23) |
| Trimmed clips marked for the editor | Done (2026-10-26) |
| Generate a slow clip with AI (chat, frames, history, prompt) | Built (fal.ai + Veo through the gateway); **on hold** |
| Veo with reference images / camera cuts | Built + tested; limits: refs can't combine with first/last frame, refs = 8 s only |
| Lip-sync models (Sync Labs etc.) + pricing for a 12 s clip | Discussed only, nothing built |
| Slow clips → B-roll instead of slow motion | Grew into the scene edit (Step 4C), skill v20 → v21 |
| "TOLR ep24 rendered, check" / "next ep won't use 4 s drift?" | Checked; drift is 0.5 s for every new episode |
| Cleanup of test videos (leftover folders, rendered pieces, HIH ep55 output) | Done (moved, not deleted) |
| Storage map of Documents | Done in a separate session |
| Step 4C in the platform, mandatory, compare with TGS ep34 5-min "B" video | Done: done-test md5 identical; full 5 min SSIM 0.9994 vs B |
| Final QC step, 25 fps, new B-roll rules — "test once" | Done 2026-10-30 (see §7) |
| "Does the platform run all steps properly for any show?" | Yes — owner will now run one episode of another show end to end himself |

## 6. Other features in the app (built, available)

- **Generate panel** (`gen.py`, `GenPanel.tsx`) — on hold, see 03 "Generate a clip with AI" and the two sections after it.
  Keys: Claude/Gemini/Veo through the gateway key (alias `sameer-s-1091`, has `veo-3.1-fast-generate-001`,
  `veo-3.0-generate-001`); fal.ai key `FAL_KEY` in `app/.env`.
- **Outputs page** lists the render, subtitles, timeline XML (Premiere), scene-edit sheet, shot library sheet, QC sheet,
  run times.
- **Settings** page: paths, AI settings, health checks (pipeline, models, both Pythons, ffmpeg, font, Chrome, disk, AI,
  DB backup, caffeinate). All green on 2026-10-30. Disk was 27 GB free — low for many episodes.
- AI budget on 2026-10-30: **$106.74 of $200** spent (`GET /api/ai/status`).

## 7. State at handover (2026-10-30, evening)

- App running (launcher), restarted after the QC step was added; nothing in the queue.
- Last test: TGS ep34 first 5 min with the new B-roll rules: 114 pieces, 27 B-roll from 18 shots, no shot used more
  than 3 times (old: one shot 5×), 1 small problem left (a reaction shot shown twice in stretch 6), whole-episode check 0,
  cost $0.92. `examples/tgs_ep34_5min/expected/` still holds the OLD-rules answer (not updated).
- WBM ep9 (id 5) and ep18 (id 6) wait at "Ready for review".

## 7b. State at 2026-11-04 (bulk, live, TAB, cost)

Read `docs/03_PROGRESS.md` section **“Bulk upload, scene-edit cost, TAB FR→IT, and live hardening”** for the full log.
Short version for a new chat:

- **Live**: https://dubstudio-yg5s.onrender.com — Docker API on Render, Postgres, R2, RunPod CPU+GPU.
- **Local Mac** is a separate catalog (`hamburg/app/library` SQLite, `R2_PREFIX=dub-studio-mac`). Not the live DB.
- **Bulk** of a range of episodes works; loose file names (`TAB FR eo 14 .mp4`) match the episode number.
- **Scene edit** ~$2–4/ep (medium thinking, 2+2 rounds), not ~$18. Last clip is auto-trimmed to episode length.
- **API image** now carries `app/backend/pipeline_tools/dubstitch.py` + `merged.py` and `pipeline/lipmerge.py` so
  Render does not look for `/root/Desktop/france-team/adaptation-tool`. Groups, lipsync, merge, reading view and
  shot library run on the API box until the worker image is rebuilt.
- **TAB PGC IT** (IT dub, FR source, episodes 11–15) was the first real bulk of that kind. Failures that were
  turned into general rules: Whisper `IT`/`FR` → `it`/`fr`; 1-frame and last-piece lip merge; truncated/control-char
  shot-library JSON; lipsync sentence past EOF coverage; thread-safe library cache.
- **Do not** retry old TGS EP31/34 scene edit unless asked. **Do not** overwrite editor EP32/EP33 cuts with test
  outputs in `opt_sonnet` / `opt_medium`.
- **2026-11-09:** if scene edit leaves a hole and matching had a clip, use the matching clip (`use_scene` retry
  repairs it, no Claude). `cpu_slots` default 8. TAB EP11 editor is the pipeline cut, ready for a person.

## 8. Open items / waiting on the owner

1. **Owner runs one episode of another show end to end and renders it himself** (milestone 7 done-test). Fix whatever he
   trips over — this is the next job of the new chat.
2. Owner to decide: matched clips under 2 s — keep as a warning, or enforce?
3. Owner to decide: re-run the full ep34 5-min scene edit under the new rules (~$5), and then update
   `examples/tgs_ep34_5min/expected/` to the new answer?
4. Open one `*_timeline_v*.xml` in Premiere Pro to confirm speeds and in-points.
5. After every B-roll change: update the "B-roll for slow clips: experiment log" doc (Claude Docs) and the skill.
6. Uploads to the Drive folder: paused by the owner — don't ask.
7. Generation (Veo / fal.ai / lip-sync): on hold until the owner says.

## 9. How to work with this owner

- He doesn't read code: build, test, run on a real episode, then report in plain words (what works, what to click, what's next).
- Keep `docs/03_PROGRESS.md` current after every piece of work (dated section), and the test count in `CLAUDE.md`.
- Ask before anything that costs real money beyond a few dollars, and before changing pipeline logic.
- Use the in-app browser only if he asks; `.claude/launch.json` changes were rejected once — don't retry.
