# Dub Studio — progress tracker

Update this file after every piece of work: tick the item, add a line to the build log at the bottom.
Milestones and their "done when" tests come from `02_PLATFORM_DESIGN.md` → "Build plan".

## Milestone 1 — Server, database, jobs

*Done when: WBM ep17 processes from the UI and produces the same files as today's command-line run.*

- [x] **1.1 Project skeleton** — `app/` layout, `requirements.txt`, README.
- [x] **1.2 Backend core** (tested)
  - `config.py`: paths from env → `app/config.json` → defaults (tools `~/Desktop/france-team/adaptation-tool`,
    pipeline python `tools/.venv/bin/python` on Mac, port 8765).
  - `db.py`: SQLite (WAL); tables shows, episodes, assets, jobs, events, sentences, mapping, timeline_versions,
    flags, markers, subtitle_tracks, renders, settings; JSON stored as TEXT; helpers `q/one/ex/insert/update`.
  - `presets.py`: TDMB, TOLR, WBM, NEW profiles + `names()` for output file names.
  - `scan.py`: finds an episode's files in a show folder by number (+ `episode_offset`), guesses language
    (stopwords / folder name), `ffprobe`, fingerprint (size + sha256 of first/last 4 MB), SRT stats, file checks.
  - `pipeline/steps.py`: `plan(show, episode, assets)` → the ordered step list per show profile (mksrt only when an
    SRT is missing; `--clauses`; lip steps for TOLR; `--size/--max-drift`; `--no-ending`; transitions).
  - `pipeline/decisions.py`: flagged rows, accept-auto, import `spec.py` (embedded mkdec), missing rows.
- [x] **1.3 Job runner + API** (tested)
  - `runner.py`: background thread, resource slots (cpu 2, gpu 1), statuses waiting/running/needs_input/done/
    skipped/failed/cancelled/blocked, auto re-run on exit 3, progress + ETA parsed from tool output, cancel kills the
    process group, retry resets downstream jobs, restart-safe.
  - `events.py` + `WS /api/ws`: live job events.
  - `main.py`: shows CRUD, episodes (create = scan), assets, check, process, cancel, job cancel/retry/log,
    decisions (get / auto / import), `/api/fs` folder browser, serves `frontend/dist`.
  - Tests: `backend/tests/test_m1.py` — 5 passing (presets + show CRUD; scan + check; full run with the decisions
    gate, resume and outputs; spec import + cancel/retry; TOLR plan has lip + transitions).
- [x] **1.3b Step 2 by Claude** (tested): `ai.py` gateway client (key in `app/.env`), `pipeline/ai_match.py`,
  `GET /api/ai/status`, `POST /api/episodes/{id}/decisions/ai`, `tests/test_ai.py` (offline + optional live),
  `tools/eval_ai_match.py`. On ep17: 36–37/38 agree with the hand read; on ep24: 18–19/19; ~$0.10 per episode.
- [x] **1.4 Frontend** (2026-10-15) — Vite + React 18 + TS in `app/frontend` (`npm run build` → `dist/`, served by
  FastAPI; `npm run dev` on 5173 proxies to 8765). Screens:
  - Shows (cards with counts), New show (preset cards → profile form, folder browser), Show (episodes table, live %).
  - New episode (number + optional own output folder) → Files tab: scanned files per kind with the other candidates,
    Browse…, Check files (duration / fps / cues / language, video-vs-audio length warning), warning when the output
    folder already holds files, Start processing.
  - Processing tab: live step list over the WebSocket (bar, time used / left, last log line, GPU tag, try N), the
    running or failed step opens its log tail by itself, Cancel / Retry per step, Cancel all, Process again.
  - Check matching tab: flagged rows with n-1/n+1 context, automatic suggestion + score, AI proposal with confidence
    (low < 0.7 highlighted, filter "only low"), per-row choice (AI / automatic / gap / another source sentence),
    Match with AI, Accept all automatic, Import spec.py / decisions.json, the decisions file shown and **editable**,
    **Check decisions** (dry run of the pipeline's own `sheet` integrity check in a scratch copy), **Join neighbouring
    rows into groups**, Save = check first, then import (the gate opens and processing continues).
  - Outputs tab: files in the output folder, Show in Finder, FINAL CHECK PASSED / the check's own lines.
  - Header: AI on/off with spend of budget.
- [x] **1.5 Launcher + real run** (2026-10-15)
  - `app/scripts/start.sh` (+ `app/Dub Studio.command` for a double-click): backend venv (reinstalls when
    requirements change), builds the UI when a source file is newer, warns if the pipeline / its packages / ffmpeg are
    missing, starts uvicorn on 8765, opens the browser; "already running" just opens it.
  - **The pipeline's `adaptation-tool/.venv` on the Mac is empty** (no pip, no packages: the earlier runs were in the
    Linux sandbox). Added `app/scripts/setup_pipeline.sh` → `app/pipeline_env` (Python 3.12 via uv; torch 2.14 with the
    Apple GPU, faster-whisper, sentence-transformers, openpyxl, python-docx, opencv, mediapipe 0.10.14, …; 1.6 GB).
    `config.py` uses it before the tools' `.venv`. The pipeline folder was not changed.
  - Subtitle font: `pngsubs.py` only looks in Linux font folders → `app/assets/fonts/Poppins-Bold.ttf` (Google
    Fonts, OFL; identical to the copy in `WBT UK-DE`), passed as `--font` to both `subs` steps (`cfg.font`,
    env `DUBSTUDIO_FONT`).
  - **WBM ep17 from the UI, real pipeline, Whisper on the Mac** → `WBM US-IT/outputs/ep17_studio/` (ep17 and ep17_v2
    untouched): all 6 deliverables, **FINAL CHECK PASSED**, 441.8 s (1.467 s opening + 440.3 s dub, no ending).
    Times: Whisper IT 97 s + US 77 s (parallel), matching 16 s, Claude Step 2 ~1 min (11 calls, 25k tokens, ~$0.08),
    edit list + groups 21 s, render 46 s, verify 14 s, bookends 18 s, subtitles 52 + 54 s.
    Italian rows identical to ep17_v2 (150/150); US Whisper on the Mac heard some lines differently (153 vs 151
    sentences, "That is how you thank your savior" not heard at all), so the hand spec.py does not apply to it.
    Decisions: Claude's 38 + Join (15 neighbour groups) + editor fixes (rows 33-35, 116-122, 148-149).
    Against ep17_v2: 73% of screen time the same US moment (< 1 s), 23% within 1-3 s, 4% other footage.
    Notable: **02:15 row 46 narration shows Viola talking again** (the QC note; row 46 was not flagged, so nobody was
    asked) and **row 143 plays ×3** (rows 144-145 are gaps because of the unheard line) — both are milestone-2 flags.
  - **Strict test = milestone-1 "done when"**: show "WBM reference check" (code WBMREF) with ep17_v2's own
    `_clauses.srt` files + `examples/wbm_ep17/spec.py`, output `app/library/checks/wbm_ep17_ref/`: **100% of the
    timeline shows the same US moment as ep17_v2** (every 0.1 s within 1 s), 37 vs 38 clip switches, 26 vs 27 slow
    segments, FINAL CHECK PASSED. Only known difference: ep17_v2's hand edit of row 19 (`us_start 60000`, the smile)
    — that is editor work (milestone 4).
- [x] Found + fixed while doing it: Claude's Step 2 answers passed on meaning but broke the pipeline's integrity rule
  in 23 places (a flagged row put on the sentence its neighbour already shows; the hand read makes those groups).
  `decisions.check_spec` (dry-run `sheet`), `join_shared` (neighbours → groups; far pairs left to the editor),
  `to_spec`; endpoints `POST /decisions/check`, `/decisions/join`. Retry now rebuilds the step's command from the
  current plan (a fix since the run was made applies). Output folder can't change while a run uses it. Index page not
  cached. Tests: 13 passed + 1 live skipped (`test_ui_api.py`, `test_decisions_join.py` new).
- [x] **TOLR ep24 from the UI** (2026-10-15) → `TOLR DE_ITaly/output/ep24_studio/` (the old `ep24_*` folders
  untouched): all 7 deliverables (3 videos, mapping, groups review, **lip-sync review**, English SRT), **FINAL CHECK
  PASSED**, 617.7 s (reference 617.5 s). Source video = `DE/TOLR_EP_24.mp4`, the only German file in the folder (no
  separate "NO SUB" file exists; the reference run used the same one). The German SRT is not in the folder any more,
  so the app made it with Whisper (4.8 min). Wall time **30.6 min** from Start to FINAL CHECK, ~2.6 min of it waiting
  on the editor at Check matching.
  - **Lip scan on the Mac GPU works with no changes**: lipgpu (S3FD + TalkNet on MPS) 17.7 min for the 12.4-min video
    (50 pieces of 15 s, ~20 s each, sharing the CPU with Whisper), lipmesh 1.6 min, lipmerge + lipsync < 1 s. TalkNet
    and its weights are found from `adaptation-tool/third_party/TalkNet-ASD` by the script's own default; mediapipe
    0.10.14 Face Mesh works. Nothing in `steps.py` or the config had to change.
  - Other times: Whisper IT words 4.3 min, matching 64 s, edit list 44 s, render 125 s, verify 30 s, bookends 40 s,
    transitions 176 s, subtitles 150 + 151 s, translate 8 s.
  - Step 2: 21 flagged rows; Claude 9 calls, 21k tokens (~$0.06), 4 low-confidence. By meaning Claude agrees with the
    hand read on 19-20 of 21. Row 123 "Il suo tono si fece più fermo" (0.4) is wrong: its German sentence "Ihre Stimme
    gewann an Intensität." was not heard by the Mac's Whisper. Check decisions refused 10 neighbour pairs → **Join**
    (8 groups) → integrity check passes (15 groups, 4 single picks, 0 gaps) → Save.
  - Against the hand-made reference (`examples/tolr_ep24`): 92.0% of screen time the same German moment (< 1 s),
    5.5% within 1-3 s, 2.5% other footage (rows 57, 66, 74, 137, 161). **Row 136 plays ×5.1 slow motion at 07:59**
    (the reference does not) — a milestone-2 flag; rows 177-178 play ×2.9 in both.
- [x] **Strict test for TOLR**: show "TOLR reference check" (code TOLRREF), output `app/library/checks/tolr_ep24_ref/`.
  Inputs: the show's Italian SRT (as the reference) + the reference's German SRT rebuilt the way the reference made it
  (`src_from_sentences.srt`: one cue per sentence of `examples/tolr_ep24/sentences_src.json` →
  `app/library/checks/tolr_ep24_ref_inputs/TOLR_EP24_DE_from_sentences.srt`; the original German SRT is gone) +
  the reference `decisions.json` (there is no `spec.py` for ep24; it passed the sheet check as is). Lip pieces from the
  main run were reused (same video, same tool; lipmesh ran again itself). **FINAL CHECK PASSED**, 617.7 s.
  - Sentences: 174 DE / 179 IT, every text identical to the reference; times identical except 4 German sentences
    (#116 "Ihre Stimme gewann an Intensität." starts 3.1 s earlier on the Mac).
  - **Timeline** (`app/backend/tools/compare_timelines.py`)**: 95.1% the same German moment (< 1 s), 4.7% within 1-3 s, 0.2% more**; 249 vs 248 segments, 35 vs 32
    groups, 43 vs 42 clip switches, 19 vs 18 slow segments. All differences are in 4 places: rows 123-126 (sentence
    #116's Whisper time), rows 92-93 and 136-138 (the two lip rows that differ, below).
  - **Lip sync: 85 lip rows on the Mac vs 85 in the reference (83 the same**; Mac adds 93, 131, the reference has 137,
    146); 74 vs 74 talking German sentences (72 the same); lip time 171.5 s vs 172.2 s, 163.5 s of it the same (95%).
    The main run (Whisper German SRT): 81 lip rows, 75/178 talking sentences.
  - Why not 100% as for WBM: Whisper on the Mac times one German sentence differently from the Linux run, and the
    Mac lip scan differs on two rows. Both are the pipeline's own results on this machine, not app logic.
- [x] Found + fixed: the step list showed 0% for the whole lip scan and the last line was a PyTorch warning repeated
  thousands of times. `runner.py` now reads lipgpu's "done 45-60 in 23 s" as progress and keeps library warnings in
  the log only (`tests/test_runner_progress.py`). Takes effect at the next app start (the running server was not
  restarted).

Known gaps in what exists:
- Step 2: the per-row picker cannot make a group by hand (edit the decisions file instead); top-5 alternatives and
  the 2-second preview are milestone 5. Claude is not yet told about the "neighbours = group" rule; Join fixes it
  after the fact (better: ask for groups in the prompt, or join inside `ai_match`).
- Rows that are **not flagged** are never shown, though their automatic match can be wrong (ep17 row 116 → S120,
  row 46 talking face): the milestone-2 flags are the answer.
- `sentences`, `mapping`, `flags` tables exist but nothing fills them yet (milestone 2).
- When the dub's own sentence is not heard by Whisper on the source side (TOLR ep24 #116), Claude is asked about a
  row whose German sentence does not exist: it should be allowed to say "gap / ask the editor" (it gave 0.4).
- `lip_mesh` waits for all of `lip_gpu`; it could start right after `ranges.json` is written (saves ~1.5 min).

## Editor v1 + new look (2026-10-16) — owner's request: "show all clips on a timeline before render, let the editor check, fix and then render"

Done (covers most of milestones 2-4; what is left is listed under each milestone below):
- **Review stop before render.** New step `review` (`steps.py`: groups + translate → **review** → render). The
  episode stops as **"Ready for review"** (new status `review`); nothing is rendered until the editor presses
  Render. Runner: `_fn_review_gate`, `rerun_from(step)` (render again after an edit runs only render → final check).
- **Timeline** (`backend/dubstudio/timeline.py`): version 1 = one clip per segment of the pipeline's
  `manifest_groups.json`; checks (no holes, no overlaps, as long as the dub, footage inside the video); versions in
  `timeline_versions` + an autosaved draft (`timeline_draft.json`); Render writes the editor's list as
  `manifest_groups.json` (`dub_studio.version` inside) and keeps the pipeline's own as
  `manifest_groups.pipeline.json`. The pipeline is unchanged: render/verify/bookends/transitions read that file.
- **Flags** (one rule set, in Python): slow (< ×0.67 yellow, < ×0.5 red), fast (> ×1.5 / > ×2), flash (< 0.5 s),
  **same footage twice** (red), **talking face on a narration line** (lip scan sees ≥ 0.4 s of moving mouth on a row
  without quotes), drift > 1 s (pipeline's groups), notes (weak match, lip row, jumps back, edited). Approve turns a
  clip's flags green; Render is blocked by open red flags (or asks to render anyway). TOLR ep24: 3 red / 65 yellow
  (row 136 ×0.20, rows 177-178 ×0.34); WBM ep17: 12 red (incl. row 143 ×0.33) / 52 yellow.
- **API**: `GET/PUT /api/episodes/{id}/timeline` (`?pipeline=1` = the pipeline's own), `POST .../timeline/check`,
  `POST .../timeline/render`, `GET .../timeline/versions/{v}`, media for the preview (`/media/src_video`,
  `/media/dub_audio` with byte ranges), `/thumb?ms=` (cached jpgs), `/waveform` (cached peaks).
- **Editor screen** (`frontend/src/pages/Editor.tsx`, route `/episodes/{id}/editor`, full screen): flags list
  (to check / red / approved / all, N = next flag), DE sentences (search, used/unused, double-click = put on the clip),
  versions; **source monitor** (mark In/Out, Replace); **program monitor** playing the timeline live (dub audio = clock,
  two video players swapping at cuts, speed per clip, IT / EN / no subtitles in Poppins); **inspector** (lines, speed,
  source in/out, speed field, slip ±frame/¼s/1s, Approve, Fit sentence, Reset clip, Delete); **timeline** (ruler,
  flag diamonds, V1 clips with thumbnails + speed + lip icon, A1 waveform, S1 IT and S2 EN cues, playhead, zoom,
  ⌘-scroll zoom). Tools: Select (drag an edge = roll the cut; drag a clip onto another = swap their footage),
  Razor, Slip; Delete gives the slot to the clip before; undo/redo; ⌘S version; keyboard map (?).
- **New look**: dark "midnight studio" theme (violet → cyan), sidebar with the shows, stat tiles, new cards,
  segmented tabs, "Ready for review → Open editor" callouts on the episode, processing and show pages.
- **Real test, TOLR ep24 (`output/ep24_studio`)**: in the editor, IT#136 got the whole German sentence
  (572.92-577.18 s "Talentiert, ja – aber impulsiv", ×0.20 → ×1.40, as in the hand-made reference); IT#177-178
  (×0.34, same in the reference) approved; 0 red → Render. Only that clip re-rendered (render 15 s), whole pass
  4.8 min, **FINAL CHECK PASSED**; the final video shows the chosen source frame at 08:00.
- Tests: `tests/test_editor.py` (flags, checks, pipeline copy, review gate → render → edit → render again); the full
  runs press Render (`conftest.press_render`). **18 passed**, 1 skipped (live AI).

Not yet (after v2 below): transitions per cut, V2 overlay clips, Premiere XML, per-show flag limits screen,
re-translate one cue, subtitle style per language. Episodes processed before the review step existed still open in
the editor (Render runs render → check again).

## Editor v2 (2026-10-16) — subtitles, QC notes, preview copy, lock, copy/paste, snapping

- **Subtitle editing (S1 dub, S2 English).** Click a cue on S1/S2: the inspector edits its words and start/end
  (typed, or "= playhead"), Split at playhead (/), Merge with the cue before/after, Delete; drag a cue to move it,
  drag its edges to retime it (never over its neighbours); "+" on the track head adds a cue at the playhead. Undo
  covers cues too. Each changed track autosaves to `subtitle_tracks` and is written as `W/subs_edited_dub.srt` /
  `W/subs_edited_en.srt` (`PUT/DELETE /api/episodes/{id}/subs/{dub|en}`; checks: words, ≥ 0.1 s, no overlaps).
  `steps.plan` points `subs_dub` and `translate` at the edited dub SRT and `subs_en` at the edited English SRT when
  they exist; Render refreshes the commands of steps not run yet (`Runner.refresh_pending`). The delivered English SRT
  in the output folder follows S2 (the Google translation is kept as `W/english_translation_original.srt` and put
  back on revert). The show folder's input SRT is never touched. "Back to the pipeline's SRT" reverts a track.
  Real-pipeline check: `pngsubs.burn` burned 9 s of TOLR ep24 from the editor's SRT (164 cues, Poppins box style).
- **QC notes (markers).** Tab "QC notes": type "06:04 Viola smiling" (time read from the text; without a time it
  goes at the playhead; M pre-fills the playhead time). Pins on a new QC lane above the flags; each open note is a
  **red flag on the clip under it** that Approve does not close — only ticking it resolved does, so Render waits for
  it (`/api/episodes/{id}/markers`, `PATCH/DELETE /api/markers/{id}`; `timeline.flags(..., markers)`).
- **540-px preview copy.** New step `proxy` after ingest (`timeline.make_proxy`: H.264, scale to 540 px high, a
  keyframe every 0.5 s, AAC 96k, faststart → `W/proxy_540.mp4`). Older episodes make it the first time the editor
  opens (`POST /api/episodes/{id}/proxy`, progress in the source monitor's header). Both monitors play
  `/media/src_proxy` once it exists; thumbnails are cut from it. TOLR ep24: 1 GB original → proxy in ~25 s.
- **Lock.** 🔒 Lock / ⇧L on a clip, "Lock approved" in the top bar. A locked clip is hatched, its fields are
  disabled, and roll, slip, razor, swap, replace, paste, speed and delete (also of a neighbour that would grow into
  it) are refused with a message. `locked` is kept on the clip in every saved version.
- **Copy / paste footage.** ⌘C copies the selected clip's footage; ⌘V puts it on the selected clip fitted to its
  slot; ⇧⌘V keeps the copied speed from the copied in-point (also buttons in the inspector). Same-footage flag applies.
- **Snapping** (🧲 Snap, S): rolling a cut, dragging cues, razor clicks and ruler scrubbing stick to cuts, dub
  sentence starts (`sentences_dub.json`), QC notes and the playhead within 8 px (a cyan guide line shows it); Slip
  sticks the footage's first frame to source sentence starts. Snap points are frozen at drag start.
- Tested in the app on TOLR ep24 (episode 3): QC note → IT#102 red and "1 QC"; cue split saved to the work folder
  (165 cues vs 164 in the show's SRT) and undone; IT#102 locked → Delete refused; IT#101's footage pasted on IT#103
  and undone; the cut before IT#101 snapped exactly to the dub sentence start at 05:59.38. Test edits were removed
  afterwards (ep24 opens at its saved version, 0 red). Full render with edited subtitles: see the next item.
- **Real render with edited subtitles, TOLR ep24 (version 2):** two typo fixes on cue 97 (06:01.6–06:06.6): IT
  "di Milano .»" → "di Milano.»", EN `of Milan." … “Even` → `of Milan.» … «Even`. Render ran render → verify →
  bookends → transitions → both subtitle videos (subs_dub / subs_en burned from `W/subs_edited_dub.srt` /
  `W/subs_edited_en.srt`, per their logs) → **FINAL CHECK PASSED** (about 5 min). Frames at 06:04 of `…_it_subs.mp4`
  and `…_en_subs.mp4` show the corrected words; the delivered `TOLR_EP24_IT_english_google.srt` has the EN fix (the
  Google one is kept as `W/english_translation_original.srt`); the show's input SRT is byte-identical (sha1 checked).
- Tests: `tests/test_editor_v2.py` (markers red until resolved, cue checks, subtitles → render commands + delivered
  SRT + inputs untouched + revert, proxy made and served, lock kept in versions). **21 passed**, 1 skipped (live AI).

## Milestone 2 — Timeline from the pipeline + flags  ✅ (2026-10-16; the flags DB table stays unused: flags are computed live)
Timeline version 1 from `manifest_groups.json`; all flags (design: "Review flags"); render from a timeline version
back to `manifest_groups.json`. *Done when an unedited v1 renders clip-for-clip the same as ep17 / ep24 today.*
- **Done-test passed.** The edit list the editor writes for an unedited v1 equals the pipeline's own, segment for
  segment (WBM ep17 156/156, TOLR ep24 239/239, 0 differences). WBM ep17 unedited v1 rendered through the editor:
  verify + FINAL CHECK PASSED, and the clean video is **identical to the pipeline's** at every sampled second
  (442/442 frames, 64-px grey fingerprints). ep24 was not re-rendered unedited (it would overwrite its delivered,
  edited version 2). The first try showed 15 differing seconds: `build_v1` rounded times to 0.1 ms, and render's clip
  cache is keyed on them to 0.001 ms, so those clips were re-encoded. Fixed (times kept to 0.001 ms; test added).
- **Per-show flag limits.** Show page → "Review flag limits": slow yellow/red, fast yellow/red, flash, drift; saved
  in the show profile (`PATCH /api/shows/{id}`, checked: red beyond yellow, slow below ×1, fast above ×1).

## Milestone 3 — Editor, view and review  ✅ (2026-10-16; subtitle styles per language moved to milestone 5)
Proxies (540 px, keyframe every 0.5 s), thumbnails, waveforms; monitors; canvas timeline; dual-player live
preview with drift correction; subtitles overlay; Flags panel; approve/lock; autosave + versions.
- **Done-test passed.** WBM ep17 played start to end in the editor (`app/scripts/playback_check.py 1`, headless
  Chrome, 540-px copy): picture vs timeline every 200 ms, 1970 samples — median 14 ms, 95% under 49 ms, **85% within
  one frame, 100% within 100 ms**; one 1-s blip at IT#124 (×0.47) up to 195 ms. Before the fix the picture trailed by
  a steady ~55 ms (4% within a frame): drift correction now nudges the player's speed (±25%) instead of waiting for
  a 120-ms miss and jumping; it still jumps on a miss over 250 ms.
- **QC spots of the ep17 report, found in the editor:** 01:00–01:03 and 01:02 smile → IT#19 (fixed, milestone 4);
  01:17–01:27 → IT#28-29 red (×0.49); 06:04 → IT#124 red (×0.47); 06:59–07:05 "blurry" → IT#142-143 red (×0.33,
  the blur comes from slow motion); 07:21 ending → no ending added (video = dub + 1.47 s opening). Not flagged:
  02:15–02:18 "Viola speaking" (IT#46): WBM has no lip scan, so the talking-face flag cannot fire there — that one
  needs a QC note (marker), which is what they are for.

## Milestone 4 — Editing  ✅ (2026-10-16)
Select, razor, trim, roll, slip, speed, replace from source, delete with auto-fill, copy/paste, undo/redo, snapping.
*Done when the ep17 smile fix (slip on IT#19) takes under a minute and survives render.*
- **Done-test passed.** In the editor on the app's own WBM ep17: IT#19 ("Viola sorrise beffarda") showed US
  57.3–58.9 s (side shot, no smile). ↓ to IT#19, ⌘C, source out 61.28, source in 59.66 (her smile ends the shot at
  61.3 s), ↓ to IT#20, ⌘V (IT#19's old footage, so nothing repeats): **45 s** including one mis-click. Render as
  version 3: only the changed clips re-rendered (8 s), FINAL CHECK PASSED; the it_subs video at 01:02 ends on her
  smile, as in the hand-made reference (`examples/wbm_ep17/qc_frames/new_62_smile.jpg`). ↑/↓ now also scroll the
  timeline to the selected clip.

## Milestone 5 — Matching and subtitles  ✅ (2026-10-16)
- Done: subtitle editor (editor v2); **top-5 alternatives** on Check matching: under each undecided row, the 5
  source sentences closest in meaning (from the matcher's own similarity table, `prep.json` "sim";
  `decisions.alternatives`, in `GET /rows` as `alts`), one click picks one.
- **Done-test passed: WBM ep9 from upload to editor without the command line** (episode id 5, output
  `outputs/ep9`). New episode 9 → the scan found the US video, IT audio, IT script (.docx) and names sheet → Check
  files (all good) → Start processing → Whisper made both SRTs (IT 4.2 min with the script, US 2.8 min), preview
  copy, sentence times, matching: 44 rows to decide → Check matching: top-5 shown, "Match with AI" (Sonnet, 20 calls,
  39k tokens; 16 low-confidence) → the integrity check refused 16 shared-footage pairs → "Join neighbouring rows
  into groups" (13 groups) → passes → Save → edit list, groups, translation → **Ready for review**: 309 clips over
  12:54.9, 17 red / 66 yellow, no timeline problems; opens in the editor. About 12 min end to end. Not rendered:
  the 17 red flags (mostly ×0.42–0.50 slow motion) and the 16 low-confidence AI rows are the editor's to review.
- **AI group rule.** The prompt now states it (neighbouring rows on the same footage = one group, unflagged
  neighbours included), and after Claude answers the app runs the pipeline's integrity check itself and joins any
  pair still sharing footage (`ai_match.apply_group_rule` → `decisions.join_shared`), rewriting the proposals as
  groups. On ep9's real answers: 16 refused pairs → 13 groups, check passes with no clicks. Check matching says
  which rows were joined.
- **Mapping panel** (editor, left tab "Mapping", widens the panel): every clip's dub line and its source footage side
  by side, flag dots, and the source sentences with "used by IT#…" / "unused"; drag a sentence onto a row (or onto a
  clip on the timeline; also from the "US sentences" tab) = that clip shows the sentence, fitted to its slot
  (respects locks, undo). Checked on ep9 (drop on IT#4 and on IT#2's timeline clip, undone).
- **Translation fallback.** The `translate` step runs the pipeline's Google translation first; if it fails and the AI
  is set up, the runner calls `translate --export-todo`, Claude translates the missing sentences
  (`pipeline/translate_ai.py`, batches of 40, count-checked), and `translate --engine file --import` builds the SRT —
  so the pipeline still does the sentence split, names and exact cue times. Without an AI key the step fails with
  advice. Real run on ep9's Italian SRT: 234 sentences in 11 s, $0.008, 355 cues. **Pasted file:** ⚙ on S1/S2 →
  "Import an SRT…" replaces that track (undo-able). **Re-translate one cue:** "↻ Re-translate from IT" on an English
  cue sends the Italian line(s) under it with their neighbours (`POST /api/episodes/{id}/translate-line`, fast model).
- **Subtitle styles per language.** ⚙ on S1/S2: box or strip, text size, and for the box its height from the bottom
  and shade; saved per show (`profile.sub_styles`, checked ranges) and passed to the `subs` steps; the program monitor
  shows it. Needed a small **pipeline change** (backups in `adaptation-tool/scripts/old_versions/`
  `dubstitch_v18_before_sub_styles.py`, `pngsubs_v2_before_styles.py`; copied to `pipeline_tools/`): `subs` takes
  `--box-size/--box-bottom/--box-alpha`, `pngsubs.burn(size, bottom, box_alpha)`. Defaults are unchanged and so are
  the default cue PNG names (cache stays valid; checked: `cue_0000_239fcc09` identical). Real burn of ep24 with
  46 px / 140 px / 50% vs the default: correct. Font choice is not offered (Poppins Bold is the house font).
- Tests: `tests/test_milestone5.py` (AI fallback with Google blocked, no-AI advice, group rule, styles → commands,
  re-translate) + top-5. **30 passed**, 1 skipped.
Check-matching screen with top-5 alternatives, mapping panel drag-to-reassign, subtitle editor + styles,
translation with fallback. *Done when WBM ep9 goes from upload to editor without the command line.*

## Milestone 6 — Render queue and delivery  ✅ (2026-10-16)
Per-clip render cache, the 3 videos, check, deliverables page, Premiere XML export, QC markers.
- **Done-test passed:** a 10-clip edit (WBM ep17: rows 5, 34, 47, 62, 76, 90, 104, 119, 132, 149 slipped 0.2 s)
  re-rendered its clips in **10.3 s** (target < 30 s; the pipeline's per-clip cache keeps the other 146); whole pass
  2.6 min with both subtitle videos (56 + 58 s), FINAL CHECK PASSED; the video differs from the unedited render only
  in those clips. Afterwards ep17 was rendered back to the smile-fix timeline (v3 → saved as v5): only seconds 62–66
  (IT#19–20) differ from the unedited render; the test XML v4 was removed from the output folder.
- **Render queue** (sidebar "Render queue", `/renders`; `GET /api/renders`): every Render press is a row in
  `renders` (`deliver.start_render`; an unfinished older render of the episode becomes "replaced"); status, step,
  progress and ETA come live from the run's render → check jobs (`deliver.render_state`), and at the end the check
  result and seconds per step are stored. Two heavy steps run at a time (runner slots), so renders of several episodes
  queue by themselves.
- **Deliverables** (episode tab "Deliverables", `GET /api/episodes/{id}/deliverables`): the 3 videos, English SRT and
  mapping sheet (expected names, size, time, Show in Finder), the groups/lip-sync sheets and the Premiere XML, the
  check result with the rendered timeline version, the final-check contact image, a warning when the editor has
  newer changes than the files, and open QC notes.
- **Premiere XML** (`deliver.premiere_xml`, FCP7 xmeml v4; `GET /api/episodes/{id}/export/premiere[?version=N]`,
  "Export for Premiere" on Deliverables and in the editor's Versions tab; every Render also writes
  `<SHOW>_EP<N>_<D>_timeline_v<N>.xml` to the output folder): one sequence as long as the dub at the source's frame
  rate (29.97 → 30 NTSC), V1 = every clip (source file, in/out, speed as Premiere's Time Remap, the dub line + source
  text as a comment, locked clips labelled Rose), A1 = the dub audio, QC notes as sequence markers. Checked with
  OpenTimelineIO's FCP7 reader on ep17: 156/156 clips + the audio, sequence 440.31 s, every clip within half a frame
  of the editor. **Not yet opened in Premiere itself** (not installed here): the speed convention (in/out counted on
  the retimed clip, as FCP7 does) needs one real import to confirm.
- Tests: `tests/test_milestone6.py` (XML structure, frames, speeds, markers; render queue → done → deliverables;
  export by version; replaced renders). **33 passed**, 1 skipped.

## Milestone 7 — Polish and launcher  ◐ (2026-10-16: all built and tested; the done-test is Sameer's own run)
Keyboard map, blur flag, one-click start, crash recovery, settings page (incl. AI key).
*Done when Sameer runs a full episode alone* — not something I can test; everything below is in place for it.
- **Blur flag** (yellow, "soft picture … check it is not blurry"): new step `sharpness` after the preview copy
  (`backend/dubstudio/sharpness_tool.py`, run with the pipeline's Python: Laplacian variance of the 540-px copy, 2
  frames/s, 1.6 s for ep17; older episodes measure it when the editor opens). A clip is flagged when its footage is
  under 25% of the episode's median sharpness (show setting "Soft picture", Show page). ep17: 8 clips (soft close-ups,
  motion blur). The QC report's 06:59 "blurry" spot is not soft in the source (83%): it came from ×0.33 slow motion,
  which the slow flag already marks red.
- **Settings page** (sidebar ⚙ Settings, `GET /api/settings`): AI gateway address, email, key (password field; saved
  only in `app/.env`, mode 600, never returned: the API says only whether a key is set), models, "Test the connection"
  (real call: spend/budget/models), remove the key; paths and performance (pipeline folder, pipeline Python, models,
  library, font, heavy steps at once, max seconds per step, port) saved in `app/config.json` with checks, used after
  a restart; **Restart Dub Studio** (when started by the launcher); a **Health** list (pipeline scripts, models,
  pipeline packages, ffmpeg/ffprobe, font, Chrome, free disk, AI, backup, keep-awake). All green on this Mac.
- **Crash recovery:**
  - The launcher (`scripts/start.sh`, also behind `Dub Studio.command`) watches the server: it starts it again 3 s
    after a crash (gives up after 5 crashes in 10 min), at once when Settings asks for a restart (exit 3), and stops
    on Ctrl-C; graceful shutdown is capped at 3 s (an open live-updates connection kept an old server alive).
  - Each running step records its process id; after a crash the leftover step process is stopped (only if it is a
    pipeline command) before the step starts again — never two copies writing the same files.
  - A step killed by a signal (memory pressure, sleep) is retried once by itself.
  - The Mac is kept awake (`caffeinate -i`) while a step runs, not while an episode waits for review.
  - The database is copied once a day to `library/backups/` (SQLite online backup, newest 10 kept).
  - **Real test (WBM ep17):** the server was killed with `kill -9` while the English subtitle video was being made;
    the launcher had it back in 4 s, the leftover step process was stopped, the step started again and the render
    finished: FINAL CHECK PASSED, the clean video identical to before (442/442 s). (Renders v6 and v7 of ep17 are
    these tests: same timeline as v5.) The first attempt found a launcher bug (`set -e` ended the launcher when the
    server died) — fixed.
- Tests: `tests/test_milestone7.py` (blur flag, settings never return the key + validation, backup, a step killed by
  a signal is retried, the sharpness step runs). **37 passed**, 1 skipped.

## Build log

- 2026-10-15 — Design approved (Claude Doc "Dub Studio — local platform design", exported to `docs/02`).
- 2026-10-15 — M1.1–1.3 written by Claude in Cowork; runner fixes (episode reaches done when no jobs are left;
  status includes cancelled; retry puts the episode back to processing; a restarted job is removed from the
  cancelled set). 5/5 tests pass. Handoff folder assembled for Claude Code.
- 2026-10-15 — Owner asked for Claude through the AI gateway for Step 2. Built `ai.py` + `ai_match.py` + endpoints.
  Found: Claude 5.5 refuses `temperature`; prose before the JSON can hold `[13]` (parser fixed); group answers
  must also cover their members; skipped rows are asked again alone. 7 passed + 1 live check passed.
- 2026-10-15 — Claude Code: M1.4 screens + M1.5 launcher built; pipeline Python env made (`app/pipeline_env`); font;
  check/join for Step 2; WBM ep17 processed from the UI (FINAL CHECK PASSED) and the strict reference check matches
  ep17_v2 on 100% of the timeline. Milestone 1 done. 13 tests pass.
- 2026-10-15 — Claude Code: TOLR ep24 processed from the UI with the lip scan on the Mac GPU (FINAL CHECK PASSED,
  30.6 min); strict reference check TOLRREF: 95.1% of the timeline the same German moment, 85 vs 85 lip rows (83 the
  same). Lip-scan progress shown. Milestone 1 complete on both shows. 15 tests pass.
- 2026-10-16 — Owner asked for an editing step before render and a better-looking UI. Built the review stop, the
  timeline API with flags/versions, the Editor screen and a new theme. TOLR ep24: row 136 fixed in the editor, rendered
  again (only the changed clip), FINAL CHECK PASSED. 18 tests pass.
- 2026-10-16 — Editor v2: subtitle editing on S1/S2 burned at render (show SRTs untouched), QC notes as red flags
  until resolved, the 540-px preview copy (new `proxy` step) for monitors and thumbnails, lock + Lock approved,
  copy/paste footage, snapping. Checked in the app on TOLR ep24. 21 tests pass.
- 2026-10-16 — TOLR ep24 rendered as version 2 with two subtitle fixes made in the editor (IT + EN, cue 97): both
  subtitle videos and the delivered English SRT carry them, input SRT untouched, FINAL CHECK PASSED.
- 2026-10-16 — Milestones 2, 3, 4 closed with their done-tests (ep17 unedited render identical to the pipeline's;
  ep17 played in sync start to end; smile fix in 45 s, survives render). Fixed: 0.1-ms rounding in v1 (needless
  re-renders), drift correction by speed nudges. Added per-show flag limits, `scripts/playback_check.py`, top-5
  alternatives on Check matching. WBM ep9 started from the screens (milestone 5 done-test). 25 tests pass.
- 2026-10-16 — WBM ep9 went from a new episode to Ready for review entirely from the screens (AI matching + Join),
  ~12 min: milestone 5's done-test passes; mapping panel, subtitle styles and translation fallback are left.
- 2026-10-16 — Milestone 5 finished: AI group rule (auto-join after the check), mapping panel with drag to reassign,
  translation fallback to Claude through the pipeline's own import + SRT import + re-translate one cue, subtitle
  styles per language (small pipeline change, defaults unchanged). 30 tests pass.
- 2026-10-16 — Milestone 6: render queue page, Deliverables tab, Premiere (FCP7) XML export written with every
  render; done-test: 10-clip edit re-rendered its clips in 10.3 s, check passed. 33 tests pass.
- 2026-10-16 — Milestone 7 built: blur flag (sharpness step), Settings page (AI key in .env only, paths, health,
  restart), crash recovery (launcher restarts the server, leftover step processes stopped, retry once on a signal,
  keep-awake, daily DB backup); real kill -9 mid-render on ep17 recovered by itself, check passed. 37 tests pass.
- 2026-10-20 — Skill audit: merged.py + mkdec.py steps, mksrt --names, groups size 7, run_notes.md, fresh work
  folder per new episode; TOLR ep24 reprocessed for real. Editor: not-found rows start as empty gaps, Project bin
  with imported clips (pipeline renders a segment from its own file), scrollable timeline, collapsible source
  monitor. 54 tests pass.

## Automatic matching (2026-10-16, owner's request)
"If it stops at Check matching, let Claude decide automatically; what it still cannot find is shown as not found and
the editor adds a clip there; then open the editor."
- **The decisions step no longer waits when the AI is set up.** `Runner._auto_ai` → `ai_match.auto_match`: Claude
  decides every flagged row, neighbours on one footage are joined into groups, rows it could not place become gaps,
  and rows still on the same footage but too far apart for a group: the less sure one becomes a gap
  (`_drop_clashes`). Imported once the pipeline's integrity check passes → sheet, edit list, groups → **Ready for
  review** without a click. Once per matching result (`W/ai_auto.json`). If the AI is off or fails, the rows wait on
  Check matching as before (the screen says why).
- **Not found = red in the editor:** every gap row (`decisions.json` "gap") puts a red flag on the clip that covers
  its time ("not found: no footage matches IT#n … add a clip here, or approve to keep it"; the pipeline stretches
  the neighbour's shot over it). It turns into a note once that clip's footage is changed, or green when approved.
  The top bar shows "N not found" (click = next one). Rows the AI was unsure of (confidence < 0.7) are yellow while
  its decisions are in use. Check matching shows a summary of what Claude did and still allows changes.
- **Real run, WBM ep18** (episode id 6, output `outputs/ep18_studio`; `outputs/ep18` untouched): 42 rows → Claude
  decided all, 8 groups joined, 5 not found (IT#5, 31, 199, 210, 211), 10 unsure → Ready for review by itself: 277
  clips, 22 red / 71 yellow. The first try stopped on rows 49 and 54 sharing footage 5 rows apart; that is what the
  clash rule fixes.
- The tests never see the owner's AI key any more (`conftest`): one test run before that called the real gateway
  (about $0.015). 38 passed, 1 skipped.
- 2026-10-16 — matching runs automatically with Claude; not-found rows are red in the editor; ep18 went to review alone.

### Matching fix: names + the manual's reading rules (2026-10-16, owner: "the not-found rows do have matches")
The owner checked ep18's 5 "not found" rows by hand and they all had footage. Causes: Claude had no name list
(Demetrio = Asher, Viola = Violet: "Smettila, le ordinò Demetrio" = "Stop it, Asher ordered"), and the prompt only
summarised the manual's Step 2, so it gave up on short lines the adaptation added. The pipeline is unchanged; only
what Claude is told and shown:
- The prompt is now the manual's Step 2 (n-1/n+1 method, n±2, nearby unused lines, gap only after all of it, its
  examples), plus a rule for added short lines (interjections, inner questions, thoughts, orders belong to the beat
  around them).
- The show's names sheet (asset `names_csv`) gives the characters that occur in the episode (`ai_match.load_names`;
  ep18: 14 names).
- Every source sentence in the prompt says which dub rows use it, or "UNUSED".
- **Second look:** every row still answered "gap" is read again alone with the checks spelled out; a row clashing
  on footage with a row further away is read again ("its footage is already shown by D…") before it may become a gap.
- **"Match again with AI (automatic)"** on Check matching (`POST /api/episodes/{id}/decisions/rematch`): clears the
  decisions and runs the decisions step again → Ready for review.
- **ep18 again:** 42 rows, **0 not found** (was 5), 16 unsure (yellow), $0.13. IT#5 → group 5–7 on S2 "After Asher
  smacked her butt"; IT#31 → group 30–32 on S30–31; IT#199 → unused S221 "her heart pounding"; IT#210–211 → group
  209–211 on S232 "Her body went rigid"; IT#54 (the old clash) → group 53–54 on S56 "But it was a mirage" — the same
  footage a hand read finds.
- Also fixed (found by an intermittent test): after cancel + retry, an old waiting loop of the review/decisions step
  could put a finished step back to "waiting"; each loop now belongs to one attempt (`Runner._stale`). 39 tests pass.

## Localisation sheet, one per show (2026-10-16, owner's decision)
"One sheet per show, two columns: source name (the video's language, e.g. German) and target name (the dub's
language, e.g. Italian); scanned by the app and always sent to Claude with the sentences."
- **Format:** `.csv` or `.xlsx`, header row `source name` | `target name` (any case; more columns ignored; `;` or tab
  separated CSV fine). Reader: `backend/dubstudio/names.py`. Old sheets still read: WBM "Master Adaptation Sheet"
  (Original/Localized Mention, characters only: 131 names) and TOLR "Localization Details" (Original/Localized
  Name + first names: 122 names, e.g. Emily Kaiser → Emma Reali, Emily → Emma). TDMB has none yet.
- **Found by the app:** files in the show folder (2 levels) whose name has localisation/localization/names/
  adaptation/character; a two-column sheet wins. Saved on the show when it is created (`profile.names_sheet`);
  existing shows find theirs automatically. Before this, TOLR's sheet was never picked up (the episode scan only
  looked for "adaptation"/"character" in the name).
- **Show page → "Localisation sheet":** which file, its format, how many names, show the names, choose another sheet,
  "Download as 2-column sheet" (`GET /api/shows/{id}/names.csv`: converts an old sheet to the house format; a
  download — the app never writes into the show folder). API: `GET/PUT /api/shows/{id}/names`.
- **Sent to Claude:** every matching request (automatic and the buttons) gets the whole list (when over 150 names,
  the ones in the episode), headed "Localisation sheet, character names (source video name → dub name)".
- `openpyxl` added to the app's requirements (for .xlsx). Tests: `tests/test_names.py`. **42 passed**, 1 skipped.

## Transitions per cut (2026-10-16, owner's request)
"Let the editor add a transition himself at a cut, from a list with our most used ones on top."
- Before: only TOLR had transitions, and the pipeline put one on every clip switch, style chosen at random
  (light leak, blur dissolve, zoom blur; never the same twice in a row).
- **Pipeline change** (backup `old_versions/dubstitch_v18_before_transition_plan.py`, copied to `pipeline_tools/`):
  `transitions --plan FILE` = per-cut choices {"default": auto|none|style, "cuts": {dub ms of the cut: style}};
  "auto" = the old random rotation, "none" = a hard cut. Without `--plan` nothing changes (same random order).
  Real check on TOLR ep24 (30-s test mode, the master untouched): cut 1 set to none → hard cut, cut 2 set to light
  leak → light leak (frames checked).
- **App:** a clip whose source jumps from the clip before (> 50 ms, the pipeline's "clip switch") has a cut handle on
  the timeline (│ = hard cut, ✦ = transition; outlined = chosen in the editor). Click → menu: the show's styles,
  **most used first** (count of the editor's choices in the show + the pipeline's own past plans), "Automatic"
  (rotation), "None (hard cut)", "Show default" (TOLR: automatic; others: hard cut). Also in the inspector
  ("Transition into this clip"). Undo works. The program monitor previews it roughly (blur / zoom / glow / orange
  light-leak sweep); the real one is made at render.
- At Render the choices are written to `W/transitions_plan.json`; a show without transitions gets the transitions
  step only when a cut has one (`steps.plan`; `Runner.ensure_steps` adds the step to the existing run, or skips it
  when no longer needed); bookends then writes `clean_noglow.mp4` and transitions makes the clean video.
- Checked in the editor on WBM ep18 (94 cuts; light leak at 00:35.0 previewed; undone, draft removed).
- Tests: `tests/test_transitions.py`. **44 passed**, 1 skipped.
- The owner deleted the TOLR and TOLRREF shows on purpose (to test re-adding a show); files on disk are intact
  (work folders `library/work/TOLR_ep24`, `TOLRREF_ep24`; outputs `output/ep24_studio`), and today's backup
  `library/backups/dubstudio-2026-10-16.db` still has the old records.
- **Show setting "Transitions": Automatic at render / Manual** (owner, 2026-10-16). Automatic = edit freely; at Render
  every cut gets a transition (the rotation), a cut the editor picked keeps the pick. Manual = only the cuts the
  editor gives one; the rest are hard cuts. It is the profile's `transitions` (true = automatic; TOLR preset true,
  others false). Chosen on New show (a two-option list), on the Show page ("Transitions" card), and in the editor's
  top bar next to Render (applies to the whole show); the Render confirmation says what will happen. 45 tests pass.

## Lip scan always on; clause mode fixed (2026-10-16, owner)
- "Clause mode: hard-code 3, no field; lip scan always on (our own scan worker)". Asked about the earlier TOLR ep24
  test (clauses 3 split 20 German sentences into lip/no-lip parts, lost 1 lip sentence, 44 → 54 cuts); the owner chose:
  **3 for every show, 0 (whole sentences) for talking-face shows** (`presets.clause_mode`, hidden profile flag
  `talking_faces`, set by the TOLR preset). **TDMB changes from 0 to 3** under this rule.
- **The lip scan runs for every show** (`steps.plan`; profile `lip` no longer used). WBM/TDMB episodes now get lip
  rows and the editor's "talking face on a narration line" flag (the ep17 QC note "Viola speaking unnecessarily" was
  unflaggable without it); about 10–20 min more GPU per episode.
- Both fields are gone from New show; the show summary says "lip scan · clauses" or "talking faces: whole sentences".
- Episodes processed before keep their steps: `Runner.ensure_steps` only ever adds the transitions step to an old run
  (never a lip scan) and keeps a step's old dependencies when the new ones are not in that run. Tests: fake lip tools
  in `conftest`; `test_old_run_never_gains_a_lip_scan`. **46 passed**, 1 skipped.
- Not yet run for real: a WBM or TDMB episode processed with the lip scan on (first real run will show the timing and
  how many lip rows a US-source show gets).

### Clause mode: only too-long sentences (2026-10-16, owner: "every show is a talking show; clauses only for too-big sentences")
- **Pipeline change** (backup `old_versions/dubstitch_v18_before_clause_long.py`, copied to `pipeline_tools/`):
  `words/analyze --clause-long L` — only sentences of at least L words are cut into clauses; saved in
  `sentence_mode.json` ("long"); without it nothing changes.
- **L = 20 words, measured** (the owner asked not to guess 14): 1432 whole sentences of WBM ep9/17/18 (IT+US) and
  TOLR ep24 (IT+DE): median 7 words / 2.3 s, 90% ≤ 15 words / 5.4 s, longest 43 words / 16.7 s. Splitting helped in
  the one ep17 QC case caused by length (20 words, 7.6 s: "Alice, che aveva capito…, sgranò gli occhi e scosse la
  testa…"); it hurt on TOLR ep24 (21 German lip sentences of 6–22 words cut into lip/no-lip parts, one lip line lost
  at 18 words). At ≥ 20 words: ~3% of sentences split, the QC case still fixed, 2 of the 21 still cut (not the lost
  one); at 14 words: 15% split, 13 of 21 cut incl. the lost one.
- Every show: `--clauses 3 --clause-long 20` (`presets.CLAUSE_LONG`, one constant); the talking-face exception is gone.
  Real check on WBM ep18 Italian: 197 sentences → the 9 of 20+ words become 24 clauses, 188 stay whole (old clause mode:
  258 pieces).

## Editor: real trims, gaps and a second video track V2 (2026-10-16, owner)
"Clips behave stitched: stretch one and the next moves; let us trim like a real editor, and add another track to drag
clips into."
- Why it happened: the edge drag was a **roll** (the cut moves, both clips change), from the v1 rule "no holes ever".
- **Trim now:** dragging an edge changes only that clip (its speed stays, more/less of its own footage): pulled in =
  a **gap**, pulled out = it **overwrites** the neighbour (its head/tail is cut off, or it goes if fully covered).
  ⌥ (Option) + drag = the old roll. Locked clips refuse trims and overwrites. Delete now **lifts** (leaves a gap);
  ⇧Delete = the clip before fills the place (old way).
- **Gaps** (black) show as red hatched blocks on V1: click → "Extend the clip before / after" (its own footage, same
  speed), or drop a sentence on the gap = a new clip there. The program monitor shows black in a gap; Render is
  blocked while a gap is left ("gap (black screen) at …").
- **V2** (above V1): drop a sentence on it (at real speed), drag a V1 clip up onto it (a copy, same length and speed),
  "▲ V2" / `.` puts the marked In→Out at the playhead, ⧉ Copy to V2 in the inspector. V2 clips move (drag), trim,
  slip, razor, replace, paste, lock, transitions, delete (V1 shows again). V2 never overlaps itself.
- **What plays = what renders:** `timeline.flatten` (and the same function in the editor): V2 where it has a clip, else
  V1, else a gap. Playback, flags (a V1 clip fully under V2 is "covered"), QC notes, transitions/cuts and the render's
  `manifest_groups.json` all use it; the pipeline still gets one edit list. Versions/drafts store `v2`.
- **Premiere XML:** two real video tracks (V1 with its gaps, V2).
- Checked in the editor on WBM ep18 (trim in → gap 00:04.7–00:06.3, black in the monitor, filled; trim out → IT#1's head
  overwritten, its end unchanged; sentence dropped on V2 plays over V1; V1 clip dragged onto V2; V2 clip moved, deleted,
  undone), then put back as it was (no draft, empty V2). Tests: `tests/test_tracks.py`. **50 passed**, 1 skipped.
- **Filling a gap with a clip** (owner: "I copied a clip and dropped it in the gap; it only offers extend"): a V1 clip
  dragged onto a gap fills it with a copy of its footage (its own speed, from its first frame; the gap lights up while
  dragging); ⌘V with the playhead in a gap pastes the copied clip there; the gap's menu has "Paste the copied clip here".
  Checked on WBM ep9 (delete IT#4 → gap 00:12.3–00:16.9 → drag IT#2 onto it → filled; undo; ⌘C IT#6, ⌘V in the gap →
  filled at ×0.50), then ep9 put back as it was.

## The US row and the eye / lock of each video row (2026-10-16, owner)
"Another video line that is always the US video, cut by its sentences, to pick any clip from; and hide / lock of the
video rows."
- **US row** (under V1): the whole source video in its own time (the timeline is as wide as the longer of the two),
  cut into its sentences plus the footage without words between them (> 0.4 s), with thumbnails and "used by IT#… /
  unused". Click = to the source monitor with In/Out marked; drag onto a V1 clip = replace its footage (fitted), onto a
  gap = fill it (fitted), onto V2 = over V1 at real speed. Never plays in the program monitor, never rendered.
- **Eye / lock on V1, V2 and US** (row heads): hidden V1/V2 neither play nor render (`tracks` in the timeline JSON,
  saved with drafts and versions; `timeline.flatten` leaves a hidden row out; a hidden V1 blocks Render: "V1 is
  hidden"); hiding US folds the row away. Locked rows refuse every change (commit, trims, slips, moves, rolls).
- Checked on WBM ep9: 38 pieces in view, click → monitor In/Out; piece → V2, → IT#1 (replaced), → a gap (filled);
  V2 hidden → V1 plays there; V1 hidden → Render blocked; V1 locked → trim refused. ep9 put back as it was. 51 passed.

## Every skill step, every show (2026-10-19/20, owner: "no step missed; the lip scan ran in 0 s")
Why TOLR ep24 looked wrong: the owner deleted and re-added it, and the new episode reused the old work folder
(`library/work/TOLR_ep24`), so Whisper, the lip scan, groups etc. found their old results and "finished" in 0–9 s
(lip files dated Oct 15). The tools themselves are exactly the skill's (md5 checked). Fixed and added:
- **A new episode never reuses an old work folder**: an existing one is moved aside to `<name>_old_<date_time>`
  (never deleted), so every step really runs (`main.create_episode`; test `test_old_work_folder_is_moved_aside…`).
- **`merged.py`** (run sheet: `merged.py W 95 > W/merged.txt`, the Step 2 reading view) is a step after `analyze`;
  **`mkdec.py`** (Step 2: `W/spec.py` → `decisions.json`, every index checked against its text) now writes the final
  `decisions.json` when the decisions step closes (decisions imported/accepted without a spec get a spec first).
  Both copied from the skill's Appendix into `adaptation-tool/scripts/` and `pipeline_tools/` (md5 as the skill).
- **Step 0B names**: when a side has no script, `mksrt.py` gets `--names W/names.csv` (the show's localisation
  sheet, source names for the source side, target names for the dub side) as the skill says.
- **Groups size 7** ("7 with lip scan, 8 without"; the lip scan runs for every show) → mode 1 with `lipsync.json`.
- **`run_notes.md`** in the output folder after the final check (inputs, Step 2 counts, Claude's not-found/unsure rows,
  lip scan + groups lines, final check lines, every step's run time).
- TOLR ep24 reprocessed from a fresh folder (run 2): Whisper 718 s (IT) / 797 s (DE), lip scan ~30 min for the
  13-minute video, merged.txt 364 lines, Claude matching with the 122-name sheet.

## Editor: not-found = empty gap, project bin + imported clips, layout (2026-10-20, owner's sketch)
- **Not found → blank**: rows no footage was found for (gap decisions, also after Claude's second look) are cut out
  of V1 in the pipeline's version (`timeline._cut_holes`, `timeline.holes`): a red striped "not found · IT#n" gap,
  the "n not found" chip jumps to the next one, Render says "gap … (IT#n: no footage was found for it)" until it is
  filled. Saved versions from before keep their red "stretched over" flag.
- **Import clips** (Premiere-like Project panel, bottom left): ＋ Import (or drop files from Finder, or Path… for a
  file on the Mac) copies the clip into `W/imports/` (+ a 540-px preview copy); `GET /bin` also lists the episode's
  files (source video, dub audio, dub / source / translated SRT; SRTs download, click opens the S1/S2 track).
  Click an imported clip → the source monitor shows it (mark In/Out, Replace, ▲ V2); drag it onto a V1 clip, a gap
  (its own speed) or V2. A timeline clip from it has `media: <id>`; the program monitor plays it with a third player;
  flags skip the source-only checks ("imported clip" note); Premiere XML points at its file.
- **Pipeline (additive)**: a segment may carry `"video"`: `render` cuts it from that file and `verify` checks it
  against that file; `bookends` ignores such segments. Old cache keys unchanged. Backup:
  `old_versions/dubstitch_v18_before_imported_clips.py`; copied to `pipeline_tools/`. Tested with the real
  pipeline on a 3-segment episode: render 3/3, verify 3/3 own footage.
- **Layout**: the bottom is the Project bin (~30%) + the timeline (to the right edge); the timeline's rows scroll
  top to bottom (S2 always reachable); the source monitor collapses to a thin "Source" strip (◂ / ▸, remembered).
- Checked in a preview copy on WBM ep9 (import → source monitor → V2 at ×1.00 → program plays it in sync and hands
  back to the source video after 6 s; pipeline version shows "not found · IT#278 / IT#280"); ep9 put back as it
  was. Tests: 54 passed, 1 skipped.
- 2026-10-20 — TOLR ep24 run 2 finished up to review once the VPN was on: Claude decided the 17 rows (14 groups, 2
  assigned, 0 not found; mkdec.py wrote decisions.json), lip-sync 83 rows, groups size 7 mode 1. The TOLR show had
  max drift 500 ms (set on the New Show form); set to the skill's 4000 ms (owner) → 39 groups instead of 47, 11 lip
  groups, 6 single lip rows, largest drift 2.2 s. Editor: 236 clips, 4 red / 67 yellow, nothing blocking Render.
  Note: the AI gateway is an internal host: it needs the VPN (without it matching waits on Check matching).

## Editor tidy-up + own colour theme (2026-10-20, owner: "less clutter, fit the window at 100%, not the blue AI look")
- **Theme "cutting room"** (`styles.css` tokens, whole app): warm graphite surfaces, tally-light orange accent
  (`--accent #ff7a2f`), mint for sound (waveform, source pieces), flat (no glows, no background gradients). Flag
  colours stay distinct from the accent: red `#ff5d6c`, lemon `#f2d24b`, green `#4ade80`. The playhead is orange.
  Old stylesheet kept in the session scratchpad only; to tune colours, change the `:root` tokens.
- **Fits the window**: the editor's fixed 1180-px minimum is gone (900 px now); top bar compact (Undo/Redo icons,
  "Save", short transition menu, shortcut letters and secondary labels hidden under 1400–1500 px); monitor bars
  shrink; checked at 1000×620: nothing overflows, Render always visible.
- **Fold panels away** (◂ / ▸, remembered in the browser): Flags (strip shows the red count), Source monitor,
  Inspector, Project bin (the timeline then takes the full width). All folded at 1000 px: program 880 px wide,
  timeline 970 px.

## TOLR ep24 vs the hand-made reference: why the app differed, and the fix (2026-10-20, Claude in Cowork)
Owner: "the editor's ep24 is not like the one you made". Measured with the same dub timeline (share of the 614 s
showing the same German moment within 1 s as `examples/tolr_ep24/manifest_groups.json`):
| Run | same moment | 1-3 s off | other footage |
|---|---|---|---|
| App run 2 (Whisper SRTs both sides, AI on 17 flagged rows, clauses 3/long 20) | 86.0% | 10.8% | 3.1% |
| Real SRTs + AI on flagged rows only, clauses 3/long 20 | 87.6% | 9.8% | 2.7% |
| Real SRTs + AI on flagged rows only, whole sentences | 94.7% | 3.7% | 1.6% |
| Real SRTs + AI + **review of unused source lines** (new), clauses 3/long 20 | 91.8% | 7.5% | 0.7% |
| Real SRTs + AI + **review of unused source lines** (new), whole sentences | **97.9%** | 1.6% | 0.4% |
| Real SRTs + the hand decisions, whole sentences (ceiling) | 97.8% | 2.2% | 0.0% |
Causes: (1) no German SRT in the show folder → Whisper German without a script splits/merges sentences
("Talentiert, ja – aber impulsiv." → "Talentiert?" + "Ja, aber impulsiv." → IT#135 played 5.1x slower); the Italian
`SRT Clean` file is found by the scan but was not in episode 7's file list. (2) Claude only read the 17 flagged rows;
the hand read also gave unused German lines to the row that tells them (19 rows differed). (3) the 20-word clause
rule splits 13 German sentences on ep24 (174 → 187) and costs ~6 points on this lip-scan show.
Done:
- `ai_match.review_unused` (called by `auto_match`, `review_unused_lines=True`): every source line no dub row shows is
  given to the row just before/after it (that row's footage extended; never another row's footage) or noted in
  `unused_notes`; failures never lose the decisions. ~5 extra calls per episode. `tests/test_review_unused.py`.
  57 passed, 1 skipped. Takes effect after the app is restarted.
- `TOLR DE_ITaly/DE/TOLR_DE - 24.srt` added: the German SRT the reference used (one cue per sentence of the original
  German SRT, which is no longer on the Mac). The scan now finds both SRTs for ep24.
Open (owner's decision): whole sentences for TOLR (talking faces) instead of the 20-word clause rule.
- 2026-10-20 — Claude (Cowork): ep24 comparison, review of unused source lines, German SRT for ep24 put back.
- 2026-10-20 — Real run through the app on the Mac (server restarted with the review): show "TOLR tool test (Claude)"
  (code TOLRT, episode 8, output `output/ep24_tooltest`, lip pieces reused from TOLR_ep24): both SRTs picked up by
  the scan, Claude decided 15 flagged rows + review gave 17 of 19 unused German lines to a neighbour row → Ready for
  review in 2 min. Timeline vs the hand-made ep24: **94.5% same moment** (app run 2: 86.0%), 4.3% 1-3 s, 1.2% other;
  48 vs 42 cuts; 6 vs 5 slow clips. Left: the 20-word clause rule (whole sentences gave 97.9% in the same test).

## One profile for every show + Claude reads every row (2026-10-20, owner; Claude in Cowork)
Owner: "all shows the same values, only TDMB's subtitle step differs; Whisper makes both sides' sentence SRTs (exact
durations) and each side is checked with its script; separate subtitle SRT of 7 words / 4 s; Claude checks all rows,
not only flags; better prompt". Answers to the open questions: whole sentences; max drift 0.5 s; ending kept but stops
before a fade/black; subtitle rows 7 words / 4 s.
- `presets.py`: constants CLAUSE_WORDS 0, GROUP_SIZE 7, MAX_DRIFT_MS 500, SUB_WORDS 7, SUB_SECONDS 4, ending "auto";
  every preset the same except TDMB `sub_style: strip`. New Show no longer shows group size / max drift / ending.
- `steps.py`: `mksrt_dub` + `mksrt_src` on every episode (`--row-words 7 --row-seconds 4`); script per side = the docx/txt
  script, else the given SRT's text (`srt_as_script` → `W/script_<side>_from_srt.txt`), else `--names`; matching uses
  the `_sentences.srt`, the burned subtitles use mksrt's subtitle SRT; words/analyze `--clauses 0`; groups
  `--size 7 --max-drift 500`; bookends never `--no-ending`.
- Pipeline (`dubstitch.py`, backup `old_versions/dubstitch_v18_before_fade_guard.py`, copied to `pipeline_tools/`):
  `source_fade_ms` finds a closing fade to black (cached `W/source_fade.json`; WBM ep28: 498.27 s, TOLR ep24: 740.10 s);
  `groups` ends any clip before it (WBM ep28's last lip clip ran to 501.47 s = black; now 498.17 s), `bookends` stops
  the ending before it.
- `ai_match.review_rows` (REVIEW prompt): Claude reads all rows in windows of 12 with neighbours, each row's length and
  its speed (x slow/fast), every source line's users or UNUSED; returns only rows to change (spans for merged beats,
  groups for clauses, wrong moments). RULES got two points (spans for two-sentence rows; a spoken line shows its
  speaker). Then `review_unused`. ~27 calls / 130k tokens per episode (~$0.6).
- Tests: 58 passed, 1 skipped (`test_given_srt_becomes_the_whisper_script`, updated presets/plan tests).
- **Real run in the app on the Mac (show TOLRT, episode 8, lip pieces reused): Ready for review in 2.5 min.**
  Whisper + script: IT 175 sentences / 254 subtitle rows; DE (script = the German SRT text) 173 sentences. Claude: 20
  flagged rows, full read changed 53 rows, 2 unused lines attached. **vs the hand-made ep24: 95.2% same moment, 4.8%
  1-3 s, 0.0% other footage**; with max drift 4 s (what the hand-made one used) 96.5% / 3.5% / 0%, 41 vs 42 cuts.
- 2026-10-20 — Claude (Cowork): one profile for all shows, Whisper both sides + script, full read, fade guard; ep24 95.2%.

## Source clip copy/paste + preview sync under load (2026-10-20, owner)
- Checked first: the other chat's per-show logic (`presets.py` constants: whole sentences, groups 7, max drift 0.5 s,
  7 words / 4 s subtitle rows, both-side Whisper SRTs) is what `steps.py` uses; not changed. 58 tests pass.
- **Copy a clip from the source monitor**: a "Clip" row under the source video: type the start and end (1:02.5,
  62.5 or I / O at the current frame), its length shows, ⧉ Copy (⌘C after a click on the source monitor; Ctrl+C on
  Windows keys). ⌘V pastes it where the editor is: onto the selected clip (fitted to its slot; ⇧⌘V keeps real speed),
  into a gap at the playhead, or on V2 at the playhead at real speed. Works for imported clips too. A short label goes
  to the Mac clipboard. Checked on WBM ep9 (id 9) with real typing and keys; ep9 put back as it was.
- **Preview sync when the Mac is busy** (the other chat's finding: picture/subtitles froze while the voice ran on):
  the play loop now also runs on a 40 ms timer (screen redraws stop when the window is not in front or a lip scan
  keeps the GPU busy), lines up again at once when the window comes back, and if the picture falls more than 0.5 s
  behind (or its player is stuck loading for 0.5 s) the voice waits for it ("picture catching up — the voice
  waits…"). The program monitor's head shows "⚙ Lip scan running · preview may lag" while a step runs
  (`GET /api/busy`). `scripts/playback_check.py` gained `--cpu-throttle N`, `--slow-raf MS`, a subtitle check and the
  held time. TOLR ep24, 60 s each: normal 89% within 1 frame / subtitles 98%; redraws 1 per second 87% / 97%;
  Chrome 6x slower 89% / 98%; max error under 120 ms in all three, the voice never needed holding. Not yet measured
  with a real lip scan running at the same time.

## Voice ran ahead of picture + subtitles in the editor (2026-10-20, owner: "audio plays some seconds before")
- **Cause, measured**: the browser plays TOLR ep24's dub MP3 (48 kHz CBR, no Xing/LAME header) ~9% faster than the
  time it reports, so the voice ran ahead of the reported time by 60 ms → 0.7 s → 2.06 s within 20 s of play from
  the start. Picture and subtitles follow the reported time, so they stayed together and the voice ran away; starting
  from a clip reset the gap. Measured by recording what the audio player outputs (Web Audio analyser) and aligning it
  with the ffmpeg waveform of the same file. Render is not affected (ffmpeg reads the MP3 right).
- **Fix**: the editor plays an exact AAC copy of the dub audio (`timeline.dub_preview` → `W/dub_preview.m4a`, 48 kHz,
  made by the proxy step or on first open in ~10 s, remade if the audio file changes; `GET /media/dub_preview`).
  Measured: voice within 20 ms of the reported time for 40 s from the start (was +2 s at 20 s).
- **Picture by the frame really shown**: the program monitor learns each player's display delay from the frames it
  actually displays (`requestVideoFrameCallback`) and runs that much ahead (0–250 ms, only from smooth display).
  `playback_check.py` now also reports the frames shown. TOLR ep24 in Chrome, 45 s from the start: frames shown
  median 11 ms, 87% within one frame, 100% within 100 ms; subtitles right 98%; Chrome 6x slower: 9 ms / 88% / 100%;
  IT#4 (0:16, "Arrosto di vitello" over "Schweine braten") within 41–58 ms. 59 tests pass.
- 2026-10-20 — Pipeline: `groups --trim-fast X [--trim-to 1.0]` (backup `old_versions/dubstitch_v18_before_trim_fast.py`):
  a non-lip block faster than X has its extra source time cut (narration lines first, the old trim rules) instead of
  speeding up; off by default, also works in the lip scan's exact-span mode (where trimming was never used). TOLR ep24
  test (`output/ep24_trimtest`, X = 1.5): 1 block trimmed (5.4 s), fastest narration block 1.54x → 1.28x; fast >1.5x
  on the timeline 11.0% → 9.3%; the rest (57 s) is inside lip groups. Not yet in the app's steps.

## Fast-clip rule: nothing plays faster than 1.25x (2026-10-22, owner; Claude in Cowork)
Owner's table: 0.8-1.25x = speed change is fine; 1.25-2x = smart trimming; below 0.75x = B-roll / manual. Fast is a hard
rule now: every clip faster than 1.25x is trimmed to about 1.2x.
- Narration blocks: `groups --trim-fast 1.25 --trim-to 1.2` (the pipeline's trim, narration lines first).
- Everything still faster (pieces of lip groups, lip-sync lines, leftovers): `pipeline/fastfix.py` (step "fastfix" after
  groups). Narration piece → cut evenly from both ends. Lip clip → Claude (vision) gets the clip's shots (camera cuts,
  `W/scenes.json`), seconds of moving lips per shot, the lip stretches, one frame per shot, the German and Italian lines,
  and decides what to cut (reaction/other-character shots, time before/after the lips, pauses between lip stretches; the
  lips only when the clip is all lip movement). Answer checked (total, piece sizes); else the same priorities as a rule.
  Hard check: nothing left over 1.25x. Writes `manifest_groups.json` (old one kept as `.before_fastfix.json`),
  `fastfix.json`, `fastfix_cache.json`, `fastfix_cuts.json` (the new jump cuts: `timeline.write_transition_plan` gives them
  "none", so no transition lands on a trim) and a "Fast clips" tab in the groups review workbook.
- Gateway note: the Claude 5.5 models now return thinking blocks; calls need max_tokens ≥ 1500 (fastfix uses 6000).
- **TOLR ep24 (`output/ep24_fastfix`, all deliverables, FINAL CHECK PASSED):** 2 narration blocks trimmed (4.7 s), 56
  pieces trimmed by fastfix (37 lip clips, 33 decided by Claude, 4 by the rule), 34.3 s cut; fastest clip 1.22x (was 1.64x);
  timeline at 0.8-1.25x 66% → 89%, faster than 1.5x 9.3% → 0%; clip switches 53 → 107 (54 jump cuts from trimming, no
  transitions on them); same German moment as the hand-made ep24 94.3%; verify 247/247 own footage.
  English subtitles: Google + 9 hand fixes. Tests: 61 passed, 1 skipped.

## Dynamic strip subtitles for HIH (2026-10-22/23, owner; Claude in Cowork)

Owner: HIH's US video has burned-in captions of 2-3 lines; a 3-line caption shows above our strip, and a big strip with
one line of Italian looks bad. "Make our SRT dynamic according to the US SRT"; only for HIH for now; "always run every
step of our pipeline and ask the user if it's not working (tell him to run the runner)"; put the step in the skill.

- **Pipeline** (`adaptation-tool/scripts`, copied to `pipeline_tools/`; backups `old_versions/dubstitch_v18_before_dynamic_subs.py`,
  `mksrt_before_words_json.py`): new `dynsubs.py`; `dubstitch.py captions` (tesseract OCR of the US captions, 4 frames/s,
  60-s pieces, only changed frames are read, one evenly spaced stack of lines at the bottom counts), `dubstitch.py dynsrt`
  (dub words → cards that fill a 2- or 3-line strip, from the final edit list), `subs --style dynamic` (per-frame strip
  height + self-check + `subs_qc_<name>.jpg`); `mksrt.py` also writes `<out>_words.json`. No OpenCV/pytesseract needed;
  the Mac has tesseract 5.5 (Homebrew).
- **App:** preset **HIH** (`sub_style: "dynamic"`), New show offers "dynamic"; `steps.py`: `captions` after ingest;
  for a dynamic show `review` needs only fastfix, then `dynsrt` (needs review + captions) → `translate` (from the
  dynamic SRT) → both subtitle videos with `--style dynamic`. Other shows unchanged (tested).
- **Tests:** `test_hih_plan_has_dynamic_subtitles`; fake pipeline knows `captions`/`dynsrt`. 62 passed, 1 skipped.
- **Skill v19** (`skill/SKILL_dub-clip-video-stitcher_v19_full.md` + zip): run rule, current settings, Step 8C, HIH
  setup, checklist, tools table and Appendix with the current `dubstitch.py`, `mksrt.py`, `dynsubs.py` (unpack round
  trip: 13 files, 0 md5 errors).
- **HIH ep55 (VM test):** US captions 2 lines 668 s, 1 line 60 s, 3 lines 13.5 s (3 captions); under the Italian video
  3 three-line zones (247.0-250.9, 252.7-258.1, 734.4-741.0 s); 166 cards (161 on 2 lines, 5 on 3), 1732/1732 words;
  strip rows 912-1056 (2 lines) / 855-1056 (3 lines); self-check 0/0; no US caption visible in the QC frames.

## Generate a clip with AI (2026-10-26, owner's sketch)
For a clip that plays too slow: make a NEW video of its slot's length showing the same moment at natural speed.
- **Editor**: select a clip → Inspector **✦ Generate this clip with AI** (highlighted when it plays under ×0.67) → the
  Generate panel opens in the right column (✕ back to the inspector). It shows the slot / footage / speed, the dub line,
  the clip's **9 frames** (start → end, click to enlarge), **What the video model saw** (Gemini's analysis), the
  **system prompt**, a **chat with Claude** (messages + attached / dropped images, ⌘⏎ to send; proposed prompts appear
  as cards with "Use this prompt"), the editable **video prompt**, the model (Kling 2.1 Pro start+end frame, Kling 2.1
  Standard, Veo 3 Fast, on fal.ai), "end on the clip's last frame", "start from an attached image", **✦ Generate
  video**, and the **videos made in this chat** (player, status, time, "▲ Put on V2 over IT#n", prompt used).
- **History**: every chat is kept per clip slot (`gen_threads`, `gen_messages`, `gen_jobs` tables; files in
  `W/gen/<chat>/`), several chats per clip (＋ New chat, 🗑). Each Claude call gets the system prompt, the clip facts,
  the start/middle/end frames, the analysis and the whole chat incl. the editor's images.
- **Backend** (`gen.py`, routes `/api/gen/status`, `/api/episodes/{id}/gen/threads…`): frames by ffmpeg; analysis by
  `gemini-3.5-flash` through the madeye gateway (the clip as video + first/last frame, JSON: people, setting, camera
  incl. push-in/pan, light, motion start→end, speaking, avoid); first prompt + chat by Claude (`AI_MODEL`); video by
  fal.ai's queue API (`FAL_KEY` in app/.env, Settings → Video generation; never shown) with the start frame (and end
  frame for Kling Pro), the smallest duration ≥ the slot, then cut to the slot, no sound, added to the Project bin.
  Put on V2 replaces V2 clips in the slot (refuses a locked one); V1 stays underneath.
- Tested for real on TOLR ep24 IT#178 (4.21 s slot, ×0.34): frames + analysis + prompt in 18 s; the first analysis
  missed a slow push-in → Gemini now also gets the first and last frame to compare (caught it after); a chat message
  with an attached frame answered in 9 s with a new prompt. Put on V2 checked with a stand-in video (fal key not
  given yet); ep24 put back as it was. 65 tests pass (`test_gen.py`: whole flow with a fake gateway and fake fal.ai,
  per-model parameters, key saved / never shown / removed).
- **Waiting for**: the owner's fal.ai key (Settings → Video generation) to run a real generation; the fal model ids
  may need updating to the newest versions then.

### Veo through the madeye key + stricter system prompt (2026-10-26)
- The AI key (alias `sameer-s-1091`) now has `veo-3.1-fast-generate-001` and `veo-3.0-generate-001` (the owner added
  them; `veo-3.1-generate-001` and `gemini-3.1-pro-preview` are not on it yet: the app uses them as soon as they are).
  The model menu shows only what the key has (+ fal.ai models when a FAL_KEY is saved).
- Gateway video API = LiteLLM `/v1/videos` (POST → id, GET `/v1/videos/{id}` → processing/completed/failed, GET
  `/v1/videos/{id}/content` → MP4); email in `metadata.user_email` + `user_email` header. The frames only reach Veo inside
  `extra_body.instances` (`image` = first frame, `lastFrame` = last frame; a top-level `instances` is silently dropped →
  text-only video with other people: tested). Also `generateAudio: false`, `negativePrompt`.
- Veo makes 16:9 / 9:16 and 4/6/8 s: the frames are padded to 16:9 with a blurred copy, the result is cropped back to
  the source's shape and size; the length is the nearest one, retimed to the slot when within ×0.8–1.25 (the last frame
  stays the last frame), else cut / stretched (`gen.plan_length`, `gen.finish`). The prompt's "Avoid:" line becomes the
  negative prompt (+ a fixed base list).
- System prompt rewritten: the task, what the AI is given, THE SHOT IS FIXED (people, place, camera incl. movement,
  light; start/end frames), NOTHING NEW (list), MOTION (only what is there, natural speed, silent moments stay silent:
  "nobody speaks, lips closed" + Avoid "talking, lip movement, open mouth"), how to write the prompt (one paragraph
  < 110 words + Avoid line, ```prompt block, keep earlier requests). The first message now also gives show, episode,
  lines before/after, lip-sync row, the clip's flags, frame size and the available models.
- Real test, TOLR ep24 IT#178 (4.21 s slot, ×0.34, slow push-in, nobody speaks): chat ready in 28 s; Veo 3.1 Fast video
  in 78 s → 1080×1080, 4.208 s, starts on the first frame and ends on the last, same people and push-in, but the woman
  opened her mouth mid-way; asked in the chat → new prompt → second video (96 s): mouths closed throughout. Both videos
  are in ep24's chat for IT#178 and its Project bin (not put on V2). 66 tests pass.

## Trimmed clips are marked for the editor (2026-10-26, owner: "mark it as trimmed by the software so the user checks it")

- `timeline.py`: `context()` reads `W/fastfix.json` (`_trimmed`: clip-level trims from fastfix + group-level trims from
  `groups --trim-fast`); `flags()` gives each trimmed, unedited clip a **yellow "trimmed"** flag with what was cut and who
  chose it. Approving the clip = checked; an edited clip loses the mark. Editor top bar: "N trimmed" chip (click = next).
- Skill v19 Step 4A.1 point 3 + checklist. Test `test_software_trims_are_marked_for_the_editor`. 67 passed, 1 skipped.
- HIH ep55 now shows 43 trimmed clips (25 trims; a trimmed clip can be split into pieces).


## Fast-clip rule check + AI calls that fail fast (2026-10-26, owner)
- **Fast-clip rule (skill v19 4A.1)**: already built by the other chat and wired for every episode: `groups --trim-fast
  1.25 --trim-to 1.2`, then the `fastfix` step (pipeline/fastfix.py), trims marked yellow "trimmed" in the editor.
  Fixed: the smallest cut is now 0.3 s as the skill says (was 0.25 s). Ran on a copy of TOLR ep24's work files (the
  episode itself untouched; its review sheet redirected to the scratch folder): groups trimmed 4 groups (7.3 s);
  56 pieces still above 1.25x (fastest 1.83x, 37 lip clips) for `fastfix` — that part waits for the VPN (below).
- **"Gemini not working"**: the madeye gateway stopped answering (VPN off). Chat 3 (HIH ep55 IT#70) failed after a
  timeout; chat 4 hung on "analysing" (each call waited up to 5 min x 5 tries; a restart left it stuck forever).
  Now: `gen.gateway_ok()` checks the gateway in 10 s before the slow calls → "not reachable, turn the VPN on, then Try
  again"; calls use `gen.ask` (150-180 s, 2 tries); if Gemini cannot watch the clip, Claude reads the 9 frames instead
  (the analysis says which: `"_by"`); a restart marks half-made chats / videos as interrupted (`gen_interrupted`);
  **Try again** prepares the same chat (`POST …/gen/threads/{id}/retry`).
- **fastfix** no longer falls back silently when Claude cannot be reached: gateway checked first (VPN off → the step
  fails in ~30 s: "37 lip clips need Claude … turn the VPN on"), a connection drop mid-way is not saved as a decision and
  fails the step (retry asks Claude again; decided clips are kept). The designed fallback (Claude's cuts do not add up →
  rule) stays. 68 tests pass.
- **Waiting for the VPN** to finish: fastfix with Claude on the TOLR ep24 copy (hard check: no clip above 1.25x), and
  Try again on HIH ep55 IT#70 (Gemini analysis).
- **VPN back, both tests finished (2026-10-26)**: HIH ep55 IT#70 "Try again" → ready in 24 s, analysed by
  gemini-3.5-flash watching the clip (man in a suit, dining room, speaking → the prompt keeps the mouth moving).
  fastfix with Claude on the TOLR ep24 copy: 55 pieces trimmed (37 lip; Claude chose 32, 5 by the rule because its
  answer did not add up), 33 s cut, fastest IT#9 x1.83 → x1.20, time above 1.25x 22.2% → 0%, above 1.5x 3.7% → 0%;
  hard check passed (max x1.253, within the 0.01 margin). The first run showed 34 cuts under 0.3 s from the two rules
  (narration halves, lip leftovers): fixed with `fastfix.ends_cuts` (halves only when each is ≥ 0.3 s, else one cut at
  the end, rounded up to 0.3 s; shots under 0.3 s are not cut alone); re-run: 0 cuts under 0.3 s; the report now lists
  `cuts_under_min`. 69 tests pass. The real TOLR ep24 (id 10) still has its old edit list (made before the rule);
  re-process it or a new episode to get the trims.

## Generate: clips with camera cuts + reference images (2026-10-26, owner: "it is not generating the complete clip")
- Cause: TOLR ep24 IT#30's footage is car → office → car (two cuts); the rules said "one continuous shot, no cuts", so
  the prompt left out the office shot and the video showed only the car (frame comparison checked).
- **Shots**: `gen.detect_shots` (ffmpeg scene cuts inside the clip, ≥ 0.25 s apart) when a chat starts; frames carry their
  shot; each shot's first + last frame is saved. Claude is told the shots and the default way to make the clip.
- **Three ways to make it** (panel "How to make it"): *Keep the short shots, generate shot N* (default when there are
  cuts: the longest shot is generated from its own first + last frame to fill slot − the other shots, the others play as
  filmed, joined in order at the source's size and frame rate) · *Match the clip's first (and last) frame* (one
  continuous shot) · *Reference images* (Veo 3.1 only: up to 3 frames / attached images, keeps all shots and cuts, 8 s,
  cut to the slot).
- Veo's reference images, tested through the gateway: accepted (`extra_body.instances.referenceImages`, `referenceType:
  "asset"`), made car → office with a hard cut in 36 s; **cannot be combined with a first / last frame** ("Image and
  reference images cannot be both set") and **only 8 s** ("supported durations are [8] for feature reference_to_video").
- Real test, TOLR ep24 IT#149 (3.9 s slot, ×0.51, over-the-shoulder shot + 0.5 s profile shot): chat ready in 22 s
  (shots found exactly; Claude wrote the prompt for shot 1 only); video in 56 s: generated shot 1 (3.4 s, start/end on
  its frames) + the real profile shot, 1080×1080, 3.904 s, 29.97 fps. 70 tests pass (`test_shots_kept_and_main_shot_generated`).

## Slow clips: B-roll instead of slow motion (2026-10-26, owner; TEST, not in the app's step list yet)

Owner: clips under 0.8x should get B-roll from the source (narration: reaction / same background; lip clips: keep the
speaker, continue over the listener or the same setup, reuse a continuing scene's footage if needed).
- `app/backend/dubstudio/pipeline/slowfix.py` (runs after fastfix): shot map of the source (scene cuts + a look
  fingerprint per shot + lip seconds per shot); per slow clip: 1. run-on in the same shot, 2. unused same-scene shots
  (within 45 s, look similarity >= 0.55; narration: no moving mouth; lip clips: speaker first, then listener / same
  setup), 3. reuse of same-scene footage shown >= 30 s away (8 s for the same camera setup), never the same part, zoomed
  in 8% bottom-anchored. Claude picks among the candidates from frames (cached, resumable). Target ~0.9x, never above
  1.15x; no piece under 0.5 s; the same B-roll shot not twice within 10 s. Writes `W/slowfix.json`, `slowfix_cuts.json`,
  "Slow clips" tab, `manifest_groups.before_slowfix.json`.
- `dubstitch.py`: a segment may carry `"zoom"` (render crops bottom-anchored; verify compares zoomed). Backup
  `old_versions/dubstitch_v19_before_zoom.py`.
- Test HIH ep55 (VM copy): 101 slow clips (30.6% of the episode) → 3.2% under 0.8x; 10 run-on only, 70 with B-roll
  (22 listener/reaction, 6 same setup, 3 same scene, 43 reused zoomed), 22 still a little under 0.8x; 134 new cuts;
  verify 364/364. The US video is ~93% used by the episode, so unused footage is scarce → most fills are reuse.
- Test TOLR ep24: 26 slow clips (10.8%) → 2.0%; 16 with B-roll (7 listener, 1 same setup, 8 reused), 28 new cuts;
  verify 263/263. Comparison reels: `HIH US-IT/output/ep55/slowfix_test/`, `TOLR DE_ITaly/output/ep24_slowfix_test/`.

## TOLR DE-UK ep9: fast trims + B-roll test (2026-10-27, owner; outputs in testing-broll-TOLR-DE-UK/output/ep9)

- Inputs: UK dub `UK/audio/Ep 9 - Sweeten the Devil.mp3` (704 s); source `DE/TOLR_EP9_clean_download.mp4` (547 s) — its audio
  is ENGLISH (tagged eng; "Susan caught Ethan staring"), so it was matched as English (labels UK,EN); the German docx does not fit it.
- **Bug fixed:** the Whisper cache was one folder per language (`W/asr/en_small_*.json`), so with a UK dub and an English source
  in one work folder the source got the dub's words. Now `W/asr/<media file name>/` (`dubstitch.asr_dir`, used by `words`
  and `mksrt.py`). Backups `old_versions/dubstitch_v19_before_asr_per_media.py`, `mksrt_before_asr_per_media.py`.
- Run: Step 0B both sides, words, analyze (51 rows to read), Claude full read (157 rows), sheet 157/157, manifest, lip scan on
  the Mac, lipsync; A = groups only; B = groups --trim-fast 1.25 --trim-to 1.2 + fastfix (18 clips + 1 block, 9.7 s cut) +
  slowfix; transitions (B: none on trim/B-roll cuts, 205 cuts); box subtitles from the UK SRT.
- slowfix changes: SIM_MIN 0.5, REUSE_GAP 20 s / 6 s same setup, several free parts of one shot, the source opening/ending
  never used, no piece under 0.5 s on screen (a 0.09 s lip sliver flashed). Result: under 0.8x 66.8% → 18.1%; 130 B-roll
  pieces (94 reused + zoomed), 33 clips still slow, 198 new cuts. The dub is 29% longer than the source video.

## Scene editor: Claude cuts a whole scene from the story (2026-10-27, owner; TEST on TGS ep31 first 30 s)

Owner: row-by-row B-roll put "Paul... warte!" (her mouth) over the silence before the voice and Paul's "Ja?" clip during the
narration before it; "Claude should think about the story: the person walking away first, then the other says wait, lip
clips at the right time; narration about a person keeps that person on screen; a solution for any show and episode".
- `app/backend/dubstudio/pipeline/sceneedit.py <work> <library> <from> <to> <out manifest>`: Claude gets every dub line with
  its voice times (dialogue = quotes / narration), every shot of the scene from the B-roll library (frames, people, type,
  action, mouth-moving times, US narration over it, where the auto match used it) and returns an EDL (pieces of shots
  tiling the window). Checks: tiling, inside the shot, speed 0.8-1.25x, piece >= 0.5 s, dialogue lines start on a moving
  mouth within 0.3 s, footage once (only reaction/neutral may repeat, 8 s apart, zoomed 8%). Problems go back to Claude
  (up to 4 rounds). Writes the manifest + `.edl.json` (reasons, rounds).
- slowfix.py also got: lip lines start with the voice (lead-in shot in the silence before), Claude's B-roll choice final.
- TGS ep31 0-30.5 s: 12 pieces, all 0.8-1.0x, round 1: 2 problems (an action and a talking shot repeated) -> round 2: 0.
  Video C + sheet in `TGS US_DE/output/ep31_test30s/`. Next (not built): whole-episode windows by scene, app step.
- Also found: Claude's Step-2 full read put row 45 before row 44's footage (story backwards, groups refused); fixed by hand
  in spec.py; a story-order check for AI answers is still to add.


## Scene editor in parallel + TGS ep32 full run with time and cost per step (2026-10-27, owner)
- `sceneedit.py --parallel N`: stretches cut at scene changes first, then about every 30 s; inside a scene the action/talking
  footage is shared out before the calls (from the automatic match), reaction/neutral/establishing shots are shared; the join
  context comes from the automatic edit; after all replies one whole-episode check (action twice, reaction repeat < 8 s, jump
  cut at a join) sends only the stretches with a problem back, also in parallel. Repeats are marked (zoom) by code.
- New app pipeline files: `library.py` (shot library, any episode), `scene_transitions.py`, `sceneedit_sheet.py`.
  `ai_match.py`: when two GROUPS claim the same footage, the less sure edge member leaves its group (was: raise).
- TGS ep32 (800 s), every step on the Mac, one script `TGS US_DE/work_ep32/run_ep32.sh` (resumable, logs run_times.tsv):
  scene edit 820 s (was about 45 min one by one on ep31), 29 stretches, 59 Claude calls, 13 stretches fixed in one parallel
  round, 0 problems left; 241 pieces 0.80-1.25x; verify 241/241; FINAL CHECK PASSED. Whole run 57 min of steps,
  AI cost $7.1 (scene edit $5.6, matching $0.6 + $0.56 failed try, library $0.3). Sheet: output/ep32/tgs_ep32_run_times_and_cost.xlsx.
- Mac needs `--font .../dub-studioo/app/assets/fonts/Poppins-Bold.ttf` for `subs` (Poppins not installed there).

## B-roll rules: never a talking mouth over narration + location guard (2026-10-28, owner; TGS ep32)
- Narration: a piece over a narration line may show at most 0.2 s of a moving mouth (lip scan); footage with a moving mouth is
  talking footage (shown once) whatever the library calls the shot; a quote over several sentences is dialogue in all of them.
  ep32 rerun: 31 places -> 0.
- Location guard: `library.py` groups the shots' place descriptions into locations (Claude, text only; "mixed" = flashback /
  montage, never B-roll). `sceneedit.py` asks Claude once per episode which scene + location every dub line plays in
  (`scene/line_places.json`; the automatic match alone is wrong where it stretched one shot over many lines) and allows a
  piece only from the line's scene, or a reaction/neutral shot of the SAME location from another scene; checked per piece.
- `scene_transitions.py`: a transition at the first clip switch after the STORY scene changes (line_places), so a same-location
  B-roll from another scene is no transition. Reply size: "why" <= 15 words (output tokens 600k -> about 110k per 5 min).
- Test: TGS ep32 first 5 min (output/ep32_test5min_location): wrong place 3 -> 0, mouth over narration 0, scene edit 297 s.

## Skill v20 (2026-10-28, owner)
- `skill/SKILL_dub-clip-video-stitcher_v20_full.md` + `dub-clip-video-stitcher_v20_skill.zip` (built by
  `~/build/sk20/mkskill20.py` from v19): Step 4C scene edit (library, line places, parallel, every case, thresholds, the
  check code), B-roll rules (narration mouth, location guard, mixed shots), transitions by story scene, Step 4B.6 Windows
  lip scan on ONNX Runtime (DirectML / OpenVINO; `export_lip_onnx.py`, `compare_lips.py`, `lipgpu.py --engine auto`),
  `run_episode.sh` (every step, time + AI cost), the AI files under `ai/dubstudio/` (key only in `adaptation-tool/.env`).
  27 Appendix files, unpack round trip OK (md5 + compile + imports).
- `lipgpu.py` ONNX: ONNX vs PyTorch identical (TGS ep32 0-30 s); Mac MPS run unchanged after the change (regression OK).

## Hard rule: B-roll only for slow lines (2026-10-28, owner) -> skill v21
- `sceneedit.py`: every line's own footage = what the automatic match plays during it (used as matched, no exceptions);
  a line below 0.8x is slow and may show B-roll only for `length - own/0.8` s; fast lines are trimmed; normal lines never
  get B-roll. B-roll must be reaction/neutral/establishing, no moving mouth under narration, the line's place. Claude gets
  each line's own footage, speed and B-roll seconds. Checked against the delivered ep32 cut: 19 B-roll pieces on lines
  not slow, 43 slow lines with too much B-roll, 20 B-roll pieces of action/talking footage.
- Skill v21 (`skill/SKILL_dub-clip-video-stitcher_v21_full.md` + zip), built by `~/build/sk20/mkskill20.py`.

## 2026-10-29 (evening) — speaker check in the scene edit
- Owner found ep34 0:01: Paul's "Anya, bitte! Das ist nicht das, wonach es aussieht!" showed Ava talking (Step 2 matched it
  to Ava's source line with the same words). `sceneedit.py`: `speakers()` (one text-only Claude call, `W/speakers.json`)
  names who says every dialogue line / who talks in every mouth-moving shot; wrong-speaker lines (`d["swap"]`) drop that
  footage and use SPEAKER shots (speaker talking, same location; may repeat talking footage `SPK_GAP` = 30 s apart, zoomed);
  `wrong_mouth()` check on every piece; prompt rule 10 SPEAKER (parallel rule now 11).
- ep34 first 5 min rerun (also first run of the gap exact-event rule): wrong-person-talking pieces 3 -> 0; 119 pieces,
  38 B-roll (79 s, all >= 1.3 s), 0 B-roll on non-slow lines, verify 277/277; 1 problem left (line 1 mouth starts 0.2 s
  late). Scene edit 519 s, ~$4.95. Previous outputs in work_ep34/_to_delete/test5min_v5_minbroll/.
- Story order inside Step 2 groups (owner, ep34 0:05-0:28): group rows share the pooled own footage (`load_groups`,
  `_grp`/`_gown`), group-level speed / B-roll allowance, windows never split a group, prompt rule 9 + line tag.
  ep34 first minute rerun: phone thrust 16.9 s on row 5 (was 7.6 s on row 3). $1.23.
- Gap plan (owner): `gap_plan()` -> W/gap_plan.json (show talking/reaction/action/neutral, who, shots); talking gap dialogue
  lines become SPEAKER lines (`spk_ok`, lip check on); check "planned person on screen >= half the line". ep34 first minute:
  0:00-4.02 Paul talking (shot 29) over lines 0-2; 0 problems; $0.70. The source opening (0-0.87 s, Ava) from `bookends` is
  still in front -- separate fix, not approved yet.
- `bookends` (dubstitch.py): opening left out when the source lip scan shows > 0.2 s of moving mouth in it (ep34: Anya
  0.08-0.87 s). ep34 first minute rerun (no Claude cost): A and B both start on the dub; B starts on Paul talking.

## 2026-10-29 (night) — face-map test: who is where / face or back / who talks (ep34, 40 shots)
- Answer key: work_ep34/facetest/gt.json (40 shots incl. all 18 two-person shots; 67 heard lines). Scorer: facetest/score.py.
- Local: facetracks.py (our S3FD + TalkNet, scores kept PER face track) + facemap.py (InsightFace 2.1 buffalo_l identity +
  head pose, OpenCV YuNet+SFace, YOLO26-L persons; venv_face, separate from the lip scan) + facejoin.py (cluster faces,
  Claude names big groups, then small groups against reference faces, then people seen from behind on masked body crops).
- Results: today people 95% / speaker on screen 34/37 + 3 false; local InsightFace people 98% (1 wrong), face/back 56/58,
  position 33/37, speaker 35/37 + 0 false; Gemini 3.5 Flash (video+sound via gateway) people 97% (5 wrong), position 29/36,
  speaker 28/37 (gives Ava's lines to Aiden). Lip scan venv verified unchanged after the install mix-up (compare_lips: match).
- Face map built in: `facemap.py` (scripts) + step `facemap` in run_episode.sh (FACEPY); `lipfast.py` keeps per-face TalkNet
  scores/boxes (ep34 rescan: intervals identical, compare_lips all 42 match; old lips_nets in _to_delete); sceneedit uses
  face_map.json (talkers_at, ON SCREEN in shot text). ep34 full face map: 309 s; vs answer key: speaker 36/37, 0/30 false,
  people 95% (3 groups 'unknown' because the gateway budget is used up -- Claude names them when it is back).

## 2026-10-30 — ep34 first 5 min, every rule, gateway budget raised to $200
- facemap with Claude naming (3 groups + backs, $0.003): vs answer key people 98%, speaker 36/37, 0/30 false.
- run 146 (scene edit 486 s, $4.81): last round of stretch 34-54 s put the phone thrust over narration -> keep-best-round
  fix in run(); run 147 (cached, $0): 110 pieces, 30 B-roll all >= 2 s, 0 problems, 0 wrong-person talking, verify 268/268.
  Old outputs: work_ep34/_to_delete/test5min_run146_lastround.
- 2026-10-30: pipeline_tools/ synced with adaptation-tool/scripts (dubstitch, lipfast, lipgpu, mksrt + new facemap.py,
  run_episode.sh, run_report.py, export_lip_onnx.py, compare_lips.py; old copies in old_versions/pipeline_tools_20261030).
  New docs/06_BROLL_SCENE_EDIT.md (the steps after groups) + docs/PROMPT_scene_edit_integration.md (wire them into the
  app) + examples/tgs_ep34_5min (fixture: the scene edit reproduces identically with 0 Claude calls, checked on the Mac;
  the face-map scorer runs from the fixture). Not yet in the app's steps.py: that is the prompt's task.

## Step 4C in the app: shot library, face map, scene edit, transition plan, scene sheet (2026-10-30, owner: "mandatory for every show")
Spec: docs/06_BROLL_SCENE_EDIT.md + docs/PROMPT_scene_edit_integration.md; skill v21. The app runs the same commands, in the
same order, as `adaptation-tool/scripts/run_episode.sh`. Generation (Veo) is on hold (owner).
- **Steps** (`steps.py`), every show (`presets.SCENE_EDIT = True`): after `groups` → `library` (Claude labels every shot) →
  `facemap` (`{fpy}` = the face Python, GPU) → `scene_edit` (copies sentences/lipsync + the automatic edit into W/scene,
  `sceneedit.py … 0 <total s> … --parallel 8`) → `use_scene` (automatic edit kept once as manifest_groups.auto.json, scene
  edit written as manifest_groups.json, last piece stretched to the end: run_episode.sh's Python, ported) → `review` →
  render → verify → `bookends` (→ W/clean_noglow.mp4) → `trans_plan` (scene_transitions.py, then the editor's own picks on
  top) → `transitions --plan W/transitions_plan.json` → subs (`--max-lead 0`) → `scene_sheet` (OUT/<show>_ep<N>_scene_edit.xlsx,
  set as the review sheet) → check → notes. `fastfix` no longer runs (the scene edit trims fast lines itself).
- **AI gate** (`Runner.ai_gate`) before library / facemap / scene_edit: gateway not reachable → "turn the VPN on, then
  Retry"; spend ≥ budget → "AI budget used up: $X of $Y — ask to raise it". Never skipped.
- **Time + spend of every step**: W/run_times.tsv (run_episode.sh format; spend from /key/info before/after), the run notes
  get run_report.py's table and OUT/<show>_ep<N>_run_times.md. A new run starts a new table.
- **Config / health**: `DUBSTUDIO_FACE_PY` / Settings "Face map Python" (default adaptation-tool/mac_lipscan/venv_face),
  health check `import insightface, onnxruntime, ultralytics`; `app/scripts/setup_face_env.sh`. `DUBSTUDIO_SCENE_DIR`
  (the scene scripts; tests point it at fakes). New show preset **TGS** (DE dub vs US, strip subtitles).
- **Editor**: B-roll pieces in green with a "B-roll" tag, zoomed repeats tagged "repeat", hover = Claude's reason; the
  `broll` / `zoom` marks go through load → save → render (tested: 110 pieces round trip, 0 differences); no "not found"
  holes in a scene edit (the gap plan placed those lines); "Automatic edit" button shows the automatic edit as a read-only
  row under V1 (`GET /timeline/auto`); Versions tab: "↻ Redo scene edit" (`POST /scene/redo`: Claude's cached answers
  move to W/scene/_old/<time>/, scene_edit + the steps after run again; a plain Retry reuses the cache, $0). Replacing a
  B-roll piece's footage drops its B-roll / zoom marks.
- **Outputs page**: scene-edit sheet, time + cost table, shot library (W/broll_library/broll_library.xlsx).
- **Tests**: 72 passed. New: plan order = docs/06 §1 (fastfix absent, AI steps marked, face Python), AI gate (no gateway,
  used-up budget), use_scene port, the scene marks through editor + render + transitions plan with the editor's pick on top.
- **Done-test** (real, $0): the app's scene_edit + use_scene on a copy of examples/tgs_ep34_5min/work → scene_manifest.json
  md5 641460ec7c93d911bc714bb581e034ae = expected; 110 pieces, 30 B-roll, problems left 0, Claude calls 0.
- **Face-map test** (the app's facemap command on the whole ep34, copy of the library): score_face_map.py → people 98%,
  on-screen speaker 36/37, narration with a wrong speaker 0/30 (892 s with a render running alongside; ref 456 s).
- **Full check vs video B** (ep34 first 5 min): the app's steps from scene_edit to check on a copy of work_ep34 (test pack's
  library, face map and Claude cache; B's English SRT reused, so translate was not run): render 300 s, verify 110/110,
  bookends 48 s, trans_plan 59 switches, transitions 449 s, subs 228 + 206 s (self-check clean), scene sheet, final check
  passed. EN-subs video vs B over 297.68 s: SSIM mean 0.9994, 99.3% of frames ≥ 0.98; only 295.2–297.7 s differ (B's
  5-minute window ends there: B switches back to the automatic edit with a transition, the app's scene edit runs on).
- Old episodes keep their old steps; process an episode again to get Step 4C.
- 2026-10-30: owner's QC rules: B-roll max 3 uses per shot + used in turn (sceneedit MAX_BROLL_USES, rotation check, SPK_GAP
  30 -> 1 s); 25 fps output (dubstitch out_fps; ep34 5-min A/B re-rendered: verify 257/257 and 268/268, fps 25); new
  qc_final.py + step `qc` (run_episode.sh). QC on the existing ep34 5-min B (made before the new B-roll rules): repeats FAIL
  (shot 9 x5, shot 54 x4), 23 clips under 2 s (WARN, no B-roll under 2 s), story sync 8 findings, black PASS, 25 fps PASS,
  speaker PASS; $0.05. The scene edit with the new rules is not rerun yet (~$5).

## Final QC step, 25 fps, new B-roll rules (2026-10-30, owner: "do these and test once")
- [x] **Final QC** step (`qc`, after `scene_sheet`, before `check`; AI step, so the gateway check runs first): `qc_final.py`
      writes `<show>_ep<N>_qc.xlsx` (PASS / WARN / FAIL per check) to the output folder; listed on the Outputs page.
      It never stops the run on a FAIL; the editor reads the sheet. Tests: plan order + arguments (72 passed, 1 skipped).
- [x] **25 fps** for every output video comes from `dubstitch.py` itself (`out_fps`); no app change.
- [x] **B-roll rules** in `sceneedit.py` (at most 3 uses per shot, shots in turn, speaker shots rotate). Done-test re-run on a
      copy of `examples/tgs_ep34_5min/work`: the new rules made Claude redo 8 stretches (about $0.92, cached answers
      for the rest): 114 pieces, 27 B-roll from 18 shots, **no shot over 3 uses** (old: shot 9 ×5, shot 54 ×4); 1 problem
      left in stretch 6 (a reaction shot shown twice), whole-episode check 0. `expected/` is unchanged (old rules).
      Note: `sceneedit.py` now checks the gateway even when every answer is cached, so the VPN must be on.
- [x] Plan checked for TGS, TOLR, WBM, HIH, TDMB and a new show code: every one gets all 34 steps (HIH 36: captions,
      dynsrt), incl. lip scan, shot library, face map, scene edit, QC. App restarted; all health checks green (27 GB free).
- Owner to decide: matched clips under 2 s — warning only or enforced; re-run the ep34 5-min scene edit in full.

## Handover to a new chat (2026-10-30, owner: switching account)
- [x] `docs/07_HANDOVER.md`: map, full step list per show, final decisions, the owner's questions and their results,
      state at handover, open items. `docs/PROMPT_new_chat.md`: the prompt to paste into the new chat. CLAUDE.md points at both.

## Accounts (id + password) and cloud storage on R2 (2026-10-30, owner; workspace `hamburg`)
Built in the Conductor workspace `hamburg` (the copy in ~/Downloads/dub-studioo is untouched, so the old state can
still be looked at). Full write-up: `docs/08_ACCOUNTS_AND_STORAGE.md`.
- **Sign-in** (`auth.py`, `envfile.py`): id + password, PBKDF2 hashes, session cookie (7 days), 10 wrong tries = a
  five-minute wait. First account from `ADMIN_USERNAME` / `ADMIN_PASSWORD` in app/.env (else `admin` + a password
  printed once). `AUTH_REQUIRED=false` gives back the old single-user mode.
- **Own shows**: `shows.owner_id` (+ `episodes.created_by`); one guard in `main.accounts_guard` checks every
  `/api/...` path with an id (shows, episodes, jobs, markers, uploads) → 403 for someone else's; the lists (shows,
  render queue) and the live socket are filtered the same way; the owner's account sees everything. An older library
  is upgraded at startup: every show goes to the owner. Show codes stay unique on the Mac (they decide the file names).
- **Screens**: sign-in screen; "Accounts" in the sidebar (own password; the owner adds accounts, gives new passwords,
  switches accounts off, makes someone an owner, removes an account); who is signed in + Sign out in the sidebar foot.
- **Cloud storage** (`storage.py`): Cloudflare R2 through the S3 API, signed in our own code (SigV4 over httpx, no new
  package). Deliverables go to `shows/<SHOW>/ep<N>/`, the database to `db/dubstudio.db` (+ `db/history/<date>`), all
  under `R2_PREFIX=dub-studio-mac` because the bucket `dubstudioprod` is shared with the owner's other dubbing app.
  Background uploader with progress, retry and signed download links on the Deliverables tab ("In the cloud"); a
  finished episode is sent up by itself; Settings → Cloud storage (status, keys, Copy the database to R2 now, Bring
  the copy down) and a health line.
- **Tests: 101 passed, 2 skipped** (live AI, live R2). New `test_auth.py` (15) and `test_storage.py` (14 + live);
  the whole old suite now runs signed in. `playback_check.py` signs in by itself (`--user` / `--password`).
- Checked against the real bucket: show + episode through the API, two files up, download link, database copy up and
  back, then the test objects deleted.
- 2026-10-30 (evening): started for the owner to try, on **http://127.0.0.1:8766** (the older copy keeps 8765): a copy
  of the live database (4 shows, 7 episodes), the real pipeline Python, sign-in on, R2 connected (the database copy
  went up: 1.0 MB). How to run it and where every file lives: `docs/08_ACCOUNTS_AND_STORAGE.md` §3.

## Web flow: uploads to R2, the queue, fetch / publish / cleanup (2026-10-31, owner; workspace `hamburg`)
The owner's ask with the deployment requirements doc: no more folder picking on the Mac — a show is made in the
browser with its localisation sheet, an episode by uploading the source video, dub audio and script; the files live
in R2; Start processing puts the episode in a queue; the workers take the files, run our steps and clean up.
Plan and target architecture: `docs/09_WEB_PLATFORM.md`.
- **Uploads**: `POST /api/shows/{id}/sheet` and `POST /api/episodes/{id}/files/{kind}` stream the browser's file to
  disk and on to R2 (`shows/<CODE>/localisation/…`, `shows/<CODE>/ep<N>/input/<kind>/…`); `upload-url` + `confirm`
  do the same with a signed link for the deployed version (needs a CORS rule on the bucket — the S3 keys cannot set
  it, the dashboard can). Assets keep `r2_key` + `origin` + the local cache path the pipeline will read.
- **Shows without a Mac folder**: no folder given → `profile.uploads = true` and a staging folder in the library;
  New show asks "where do the files come from", New episode and the Files tab show upload rows with progress bars.
- **Three new steps** (`steps.py`, `runner.py`, `storage.py`): `fetch` (inputs down from R2 before `ingest`),
  `publish` (deliverables → `…/ep<N>/out/`, small work files → `…/ep<N>/work/`), `cleanup` (the fetched files are
  deleted once everything is up; `CLEAN_WORK=true` also drops proxies and rendered pieces). The pipeline scripts are
  untouched: they still get plain file paths.
- **Queue**: `submit()` starts the episode or puts it in the line (`MAX_ACTIVE_EPISODES`, default 1, one by one);
  `GET /api/queue` + the Render-queue page show what is working (step, progress) and who is waiting with its place;
  an episode waiting for a person (Check matching, review) lets the next one start; cancelling a queued episode takes
  it out of the line.
- **Tests: 113 passed, 2 skipped.** New `test_uploads_queue.py` (12): cloud show, sheet upload, file upload/replace/
  remove, direct upload + confirm, files locked while processing, the run starting with `fetch` and ending with
  `publish`/`cleanup`, a deleted file coming back from R2, the full fake-pipeline run publishing to R2 and cleaning
  the machine, and the queue (waiting, starting by itself, cancelling, per account).
- Checked against the real bucket as the new `sameer` editor account: cloud show + sheet + video + audio uploaded
  (`dub-studio-mac/shows/DEMO/…`), the Files tab showed them as "in cloud storage"; the demo show and its objects
  were deleted again.

## Any-size uploads, Postgres and Redis (2026-10-31, owner; workspace `hamburg`)
- **Files of any size**: files over 96 MB go up in 64 MB parts (S3 multipart: start → part → finish, with abort and
  3 tries per part), from the browser too (`uploadEpisodeFile` slices the file, the API streams each part on to R2 and
  appends it to the machine's copy). A dropped line now costs one part, not the whole video. New endpoints:
  `files/{kind}/start | part | finish | abort`; `storage.put_big_file` is used for the deliverables as well.
- **Postgres**: `DATABASE_URL` switches the whole app to Postgres (`db.py` translates the same SQL: `?` → `%s`,
  `INTEGER PRIMARY KEY` → `BIGSERIAL`, `REAL` → `DOUBLE PRECISION`, `INSERT … RETURNING id`, `information_schema`
  instead of `PRAGMA`). Removed the two SQLite-only bits in the API (`json_extract` on timeline versions,
  `COLLATE NOCASE` on the account lookup). SQLite stays the default for the Mac.
- **Redis**: `REDIS_URL` sends every live event through a Redis channel, so several API replicas / workers share one
  stream; without it the events stay in the process (one machine). The queue still claims jobs in the database.
- `app/docker-compose.yml` runs both locally (Postgres 5433, Redis 6380; `docker-compose up -d`).
- **Tests: 117 passed on SQLite, 119 on Postgres + Redis** (the two extra are the Postgres and Redis checks).
  New: multipart upload against the fake bucket, the chunked upload API, give-up/abort, Redis fan-out between two
  buses, and the app answering on Postgres. One run on Postgres had a flaky failure that two later runs did not
  reproduce; worth watching.
- 2026-10-31: the owner added the bucket's CORS rule, so **direct uploads are on** (`UPLOAD_MODE=direct`): the API
  signs a link per part and the browser sends the bytes to R2 itself. Checked from a real headless Chrome — a 120 MB
  file in 2 parts, 10.5 s, nothing through the API; `fetch` brought it back to the machine in 4.1 s. Small files
  (< 96 MB) still go in one request through the API. 119 tests pass.

## Claude through OpenRouter (2026-10-31, owner: "can we use OpenRouter for our Claude usage")
- `ai.py` now speaks to **either provider**, chosen by `AI_PROVIDER` (or by itself: OpenRouter when its key is there):
  - **openrouter** — `OPENROUTER_API_KEY`, base `https://openrouter.ai/api/v1`, our model names mapped to theirs
    (`claude-sonnet-5-5` → `anthropic/claude-sonnet-5.5`, `claude-haiku-5-5` → `anthropic/claude-haiku-5.5`),
    budget from `GET /key` (+ `/credits` when the key has no limit of its own).
  - **gateway** — the Pocket FM gateway as before (VPN only).
  No pipeline step changed: they all call `ai.chat()` with our own names.
- The owner's project key is in app/.env: **$250 a month**, nothing spent yet. Checked live: a text call, an
  **image** call (the shot library, face map and scene edit send frames) and a Step-2-style matching call — all
  correct, 1.4 s, $0.0004 each. Total spent on these checks: under one cent.
- Why it matters: OpenRouter is on the public internet, so a **deployed worker reaches Claude without the VPN** —
  that was the open deployment blocker. Prices on this account: Sonnet 5.5 $2/M in, $10/M out (the `:batch` variant
  is half that, a later cost lever); Haiku 5.5 $0.10/$0.50.
- The AI gate, the badge and the Settings health line now say which provider is in use and show its budget.
- Tests: 122 passed, 4 skipped. New: provider choice, model mapping, the call shape per provider, the budget read,
  and the gate's message for each ("turn the VPN on" vs "check the internet").

## TGS ep34, first 5 minutes, re-made on OpenRouter and compared with the Cowork run (2026-10-31, owner)
Same window (0–297.68 s), same inputs (ep34 sentences, lip scan, groups, shot library + face map), **every cached
Claude answer deleted** so speakers, line places, gap plan and all ten stretches were answered fresh — through
OpenRouter (`anthropic/claude-sonnet-5.5`), not the gateway.

| | OpenRouter (today) | Cowork / gateway (before) |
| --- | --- | --- |
| Scene edit | 107 pieces, 10 stretches, 441 s, 36 calls, **$4.99** | 110 pieces, 486 s, $4.81 (first full run) |
| Problems left | **0** (stretches and whole-episode) | 0 in `expected/`; 1 left in the 2026-10-30 new-rules re-run |
| B-roll | 31 pieces, 80 s, from **19** shots, most-used **3×** | 30 pieces, 68 s, from 16 shots, most-used **5×** |
| Render | 265/265 rows show their own footage | 268/268 (A/B re-render) |
| Transitions | 115 clip switches (86 transitions, 29 cuts into B-roll) | 59 switches in the app's earlier check |
| Subtitles | self-check clean (0 bad frames) | clean |
| **Final QC** | repeats **PASS**, short shots WARN (20), story sync FAIL (6), black PASS, 25 fps PASS, speaker PASS | repeats **FAIL** (shot 9 ×5, shot 54 ×4), 23 short, story sync 8, rest PASS |
| Same source footage on screen | 39.6% of the window (a different but equally valid cut; the rules changed since `expected/` and the scene edit is not deterministic) | — |
| Whole test | **$5.24** of Claude (scene edit $4.99, speakers/places/gap plan $0.16, QC $0.007, matching checks $0.07) | ~$4.9 |

Output for watching, next to the owner's own videos in `TGS US_DE/output/ep34_test5min/`:
`TGS_EP34_first5min_OPENROUTER_scene_edit_en_subs.mp4` (+ `..._openrouter_qc.xlsx`, `..._openrouter_scene_manifest.json`).
Conclusion: OpenRouter gives the same quality at the same price and speed, and the new B-roll rules show in the QC
(repeats PASS instead of FAIL). Nothing in the pipeline changed — only the provider.

## TGS ep34 run from step 0 through the app, on OpenRouter (2026-10-31, owner: "run every step yourself")
Show + episode made in the app (folder show), **all 37 steps** run by the app's own queue — nothing reused from the
owner's earlier run. Scene edit limited to the first 5 minutes (new `scene_until_s` / `DUBSTUDIO_SCENE_SECONDS`
setting) so the test matches his own 5-minute reference and stays in budget.
- **79 minutes wall, $6.30 of Claude** (matching $1.28 incl. the earlier checks, shot library, scene edit, QC).
  Slowest: lip scan 1033 s, Whisper 682 + 587 s, scene edit 455 s, face map 407 s.
- Five Claude steps all ran on OpenRouter: `decisions` (23 groups, 7 assigns, 10 gaps — every flagged row decided),
  `library` (92 s), `scene_edit` (105 pieces, 26 B-roll, 24 zoomed), `translate`, `qc`.
- Deliverables in `output/ep34_openrouter_full/`: the three videos (230/205/204 MB), English SRT, mapping, groups,
  lip-sync, scene-edit and QC sheets, Premiere XML, run notes and times. `publish` sent 12 files + 54 work files to
  R2 (`dub-studio-mac/shows/TGS/ep34/out|work`), `cleanup` freed nothing (a folder show keeps its own files).
- **Final QC**: B-roll repeats PASS, short shots WARN (18), story sync FAIL (6), black PASS, 25 fps PASS, speaker
  PASS — the same shape as the owner's own 5-min QC, with repeats PASS where his older cut FAILED.
- **Found and fixed**: the render gate counted the scene edit's own B-roll as red flags (55 of them: 39 "repeat",
  16 "not found"), so every episode would need 50+ approvals by hand. `timeline.py`: a repeat on a B-roll piece is
  now a yellow note ("B-roll: the same shot as …"), and a line the gap plan covered on purpose is an info note.
  Red flags on this episode: **55 -> 6** (the 6 are real). Test added (`test_editor.py`).

## Scene edit on Haiku 5.5 instead of Sonnet: tested, not usable (2026-10-31, owner)
Same 5 minutes of ep34, same shot library / face map / groups from the full run, every Claude answer deleted,
`sceneedit.py --model anthropic/claude-haiku-5.5`.
- **It failed**: after 862 s and $0.53 it stopped with "stretch 0: no usable answer after 3 tries". Four of the ten
  stretches (0, 3, 4, 6 — the image-heaviest) answered **empty** over and over ("no JSON in model reply"); a small
  test prompt to the same model answers fine, so it is the 40 k-token, 35–95-image prompts it cannot handle.
- **Where it did answer, it broke the rules**: first-round problems 11, 8, 6, 5 (Sonnet: 0–4, converging to 0),
  including B-roll of 0.58–1.36 s (the rule is ≥ 2 s), B-roll on lines that are **not slow** (the hard rule of v21),
  footage outside the stretch's window, and invented timings ("147.92-147.92 not inside shot 42").
- Sonnet on the same inputs: 107 pieces, 0 problems left, 441 s, $4.99.
- **Conclusion**: the scene edit stays on `claude-sonnet-5.5`. The cheap-model route would need the prompt split
  (fewer shots per call) plus the mechanical fixes in code; that is the cascade idea, not a drop-in swap.
- The cost work to do instead (no quality risk): prompt caching of the identical shot/rules prefix (~$5/episode),
  fixing mechanical problems in code instead of another Claude round (~$2–3/episode), smaller frame strips (~$1).
- Also fixed: the run-times sheet showed $0.00 per step because the cost reader still called the old gateway's
  endpoint; it now asks whichever provider is in use, so the next run shows real dollars per step.

## Prompt caching for the scene edit (2026-10-31, owner: "test caching first")
`sceneedit.py` now marks the end of the block that never changes between rounds (rules + shot list + the ~95 frame
strips) with `cache_control: ephemeral`; `AI_CACHE=false` switches it off. **Nothing about the prompt changes** —
same blocks, same order, same bytes — only the billing: the first call writes the cache, later ones read it at a
tenth of the price. `ai.py` counts cached tokens per call (`ai.cache_stats()`), and the run-times sheet now reads
the spend from whichever provider is in use (it printed $0.00 before).
- Proof on two calls sharing a 7.3 k-token image block: first $0.0184 (0 cached), second **$0.0009 with 7 303 of
  7 334 tokens cached** — 20× cheaper on the repeat.
- Same 5 minutes of ep34, fresh, caching on: **110 pieces, 0 problems left, 216 s, $2.29** (uncached baseline:
  107 pieces, 0 problems, 441 s, $4.99).
- Honest split of that drop: this run needed **19 calls instead of 36** (run-to-run variance in how many stretches
  need a re-cut), which alone would have cost $3.01 at list price; **caching took it to $2.29 — it paid for 24% of
  the run, about half the input bill**. On the earlier 36-call run the same mechanism would have saved a similar
  share. Expect **$10.50 -> $6-7 per episode** from caching alone, more when the re-cut rounds are fixed in code.
- Quality unchanged: 0 problems left in both runs, no shot used more than 3 times.

## First cloud step: the lip scan of ep34 on a RunPod L4 (2026-10-31, owner)
One pipeline step run end to end on rented hardware, to prove the shape of the deployed platform.
- **How**: an L4 pod (24 GB, $0.59/h) started from the stock `runpod/pytorch` image with a start command that pulls a
  job script from R2. The pod got **four signed links only** (code+weights bundle in, source video in, result out,
  report out) — no R2, RunPod or AI key ever left the Mac. It installed the few extra packages, fetched the 142 MB
  lip-scan bundle (lipgpu/lipdetect/lipfast + TalkNet-ASD + its weights) and the 651 MB episode, ran
  `lipgpu.py --device cuda`, tarred `lips_nets/` back to R2 and wrote a report. Then terminated from here.
- **Result**: exit 0, 44 files, **$0.20 for the whole test**.
- **Same output as the Mac**: `compare_lips.py` on the Mac's `lips_nets` vs the cloud's — **42 pieces, tracks same,
  talking intervals same in every one**; 2 pieces flagged only on score/visual drift (0.18 / 0.77), which is what
  docs/01 predicted for a different device. The edit would come out identical.
- **GPU barely used**: **peak 1,008 MiB of 23,034 MiB (4%)**, mean 802 MiB over 433 samples taken during the scan.
  Scan 926 s vs the Mac's 1,033 s — only 10% faster, and the pod's telemetry showed CPU 98% / GPU 3%: this step is
  **CPU-bound**, not GPU-bound. So: many jobs fit on one GPU by memory, but they would fight over CPU; the lever is
  vCPUs per worker, and an L4 is more GPU than this step needs. Next cheap measurement: sample GPU *utilisation*
  during a run to see whether a CPU-only box could do it.
- Staged in R2 for any future worker: `worker/lipscan_bundle.tar.gz`, `worker/job_lipscan.sh`, and ep34's source
  video under `shows/TGS/ep34/input/`.

## What the cloud tests actually proved about GPUs (2026-11-01, owner; $0.90 of RunPod)
Four L4 runs on ep34. Three findings that change the deployment plan:
1. **The lip scan gives the same answer in the cloud as on the Mac.** `compare_lips.py`, Mac vs L4, full episode:
   42 pieces, **tracks same and talking intervals same in every one**; 2 pieces differ only in score (0.18) and
   visual drift (0.77), exactly as docs/01 predicted for another device. The cut is identical.
2. **The lip scan does not use the GPU**: mean GPU utilisation **1%** (peak 27%), peak VRAM 1,008 MiB of 23,034.
   It is CPU-bound, and the container's real budget is **13.6 cores** (`cgroup cpu.max 1360000 100000`; `nproc`
   misleadingly reports the host's 128).
3. **More processes on one machine do not help** — they hurt. Same 30 s pieces, one L4:
   | pieces at once | wall | video | s per second of video |
   |---|---|---|---|
   | 1 (full episode, sequential) | 918 s | 630 s | **1.46** |
   | 4 | 392 s | 120 s | 3.27 |
   | 8 | 800 s | 240 s | 3.33 |
   `lipgpu` sets threads = cores − 2, so N processes spawn ~11N threads on 13.6 cores and thrash. Level 16 was not
   run (the answer was clear; it would have cost another $0.26). **One job per machine is optimal; parallelism must
   be across machines** (the pipeline already supports `--ranges`, and `lipmerge` joins the pieces — 8 workers would
   do an episode's lip scan in ~2-3 min for the same money, since billing is per second).
   An earlier run of the same 4-piece test reported 36 s; it could not be reproduced and is **not trusted**.
4. **Whisper does not use the GPU either** — and never has, on the Mac or in the cloud. `mksrt.py --engine` defaults
   to `faster`, and every `WhisperModel(...)` in `dubstitch.py` / `mksrt.py` is hard-coded
   `device="cpu", compute_type="int8"` (dubstitch.py lines 331, 418, 2796). The MLX (Apple GPU) path only runs with
   `--engine mlx`, which the app never passes. That is why Whisper took 682 s + 587 s on ep34.
   **Opportunity (needs the owner's go-ahead, it is a pipeline change):** faster-whisper on CUDA float16 is typically
   5-10× quicker than CPU int8 — roughly 20 min -> 2-3 min per episode. The transcription would have to be checked
   against the Mac's output first.
5. **Only the face map really wants a GPU** (`facemap.py`: InsightFace `ctx_id=0`, YOLO `device=cuda`). Not yet
   measured in the cloud — that is the remaining test before the worker shapes are fixed.

## Serverless worker on RunPod: it works, autoscales, and matches the Mac (2026-11-01, owner)
The queue-based worker the owner asked for, proved end to end. **Total RunPod spend for every test today: $1.55.**
- **Image** `sameer27488/dubstudio-worker:cpu` (911 MB), built here for linux/amd64 and pushed to Docker Hub.
  It carries `lipgpu/lipdetect/lipfast`, TalkNet-ASD **and its weights**, torch/torchvision/torchaudio (CPU),
  ffmpeg and the RunPod SDK — **nothing is downloaded at job time**, which is what broke five earlier bare-pod
  attempts (InsightFace weights, Ultralytics, apt, …). Build file: `/tmp/worker/Dockerfile`, handler: `handler.py`.
- **Endpoint** `dubstudio-cpu` (`9k3nkvxj8zarxj`): min 0 / max 4 workers, scales when a job waits > 4 s.
  Created through GraphQL (template) + the REST API (endpoint; REST needs a browser User-Agent or Cloudflare
  answers 403).
- **A job is one step of one episode**: `{"step":"lip_scan","inputs":{"video":<signed GET>},"args":{"range":[0,15]},
  "outputs":{"result":<signed PUT>}}`. The worker fetches, runs the **unchanged** pipeline script, PUTs the result
  back, returns timings, and wipes its scratch. **No credential ever reaches the worker** — only expiring links.
- **Autoscale measured**: 8 pieces submitted at once against max 4 workers → 0 → 4 workers, **all 8 done in 208 s**,
  0 failures, 420 s of worker time. Idle workers afterwards are **not billed** (`currentSpendPerHr: $0`).
- **Output is right**: the 8 cloud pieces vs the Mac's own `lips_nets` for 0-120 s —
  **`8 pieces compared: ALL PIECES MATCH`**, box 0.0 px, several pieces 0.0000 score and visual difference.
  Better than the GPU pod run, which had 2 pieces flagged on score drift.
- Speed per worker: 2-3.5 s per second of video (the Mac does 1.64), so the win is **parallelism across machines**,
  not per-machine speed: a full 11-minute episode as 8 pieces would clear in roughly 5 minutes instead of 17.
- First attempt failed with `No module named torchvision` (TalkNet's S3FD needs it) — the API returned the whole
  traceback, fixed in one line and rebuilt. That is the difference the image makes: failures are legible.

## The whole episode's lip scan in the cloud: right answer, but slower than the Mac (2026-11-02, owner)
The first full-episode run (ep34, 627 s) **failed**, and both reasons were mine:
- the endpoint's execution limit was **30 min** and two of the eight shares hit it;
- worse, `cloud.wait()` **raised on the first failure**, so the six shares that had finished were thrown away.

Both fixed. The limit is now 2 h, and a failure is reported rather than raised: `cloud.lip_scan` sends the failed
shares again (3 attempts) and keeps the finished ones, so one bad machine costs one share, not the episode.

**The re-run worked: 42 of 42 range files, 0 retries.** And the answer is the Mac's answer — the pipeline's own
`compare_lips.py`, Mac vs cloud, full episode: **`ALL PIECES MATCH`**, box 0.0 px, worst score difference 0.02 and
worst visual difference 0.07 (the threshold is 0.5; most pieces are 0.0000). That is a better match than the GPU pod
run, which had 2 pieces drifting on score.

**One real bug found by comparing, not by reading.** My splitter clamped the last piece to the end of the video —
`[615, 627]` — so the file came back as `range_615_627.json`. `lipgpu.make_ranges` does **not** clamp: it steps
`while s < L`, so a 627 s episode's last piece is `[615, 630]` and the file is `range_615_630.json`. A clamped last
piece writes a name the rest of the pipeline does not look for. `cloud.make_ranges` is now `lipgpu.make_ranges`
written out, with the reason in the docstring.

**Correction to yesterday's estimate.** I wrote that an 8-piece episode "would clear in roughly 5 minutes instead of
17". It did not: **1,736 s of wall time (29 min) against the Mac's 1,033 s (17 min)**, for 1,085 s of billed worker
time. The arithmetic I did (8 jobs x ~136 s of execution / 4 workers = ~5 min) ignored everything that is not
execution: each of the 8 jobs cold-starts a 911 MB image and pulls the 651 MB source video before it scans anything,
and the endpoint's max of 4 workers means two rounds of that. The per-piece *compute* is as fast as measured before;
the overhead is per job, so **cutting an episode into more, smaller pieces makes it worse, not better**. For the
record: a single 15 s piece submitted on its own takes minutes, nearly all of it fetch and start-up.

What that means for the plan: the cloud lip scan is **right**, **safe to retry** and **cheap** (about $0.5 of worker
time for a full episode; the balance is $196.46 and idle workers are not billed), but it is **not a speed-up for one
episode**. It is a way to run episodes **side by side** without tying up the Mac, and the thing to fix if speed
matters is the per-job overhead: keep the source video on the worker between pieces (one job per episode, not per
piece) or stream only the needed seconds instead of the whole file.

## The editor's media comes from the cloud now (2026-11-02)
A deployed API box has none of the big files, so the editor had nothing to play: the 540-px preview copy, the dub
preview (the exact AAC copy — browsers run ahead on some MP3s), the waveform and the thumbnails are all made by
ffmpeg from the originals. Now:
- each one is copied to R2 when it is made (and on first serve, so older episodes are covered the first time someone
  opens them);
- **playback** is a 307 to a signed R2 link when the file is not on this machine — R2 answers the range requests, so
  scrubbing works and the API carries no video bytes;
- the two things that need a real file (thumbnails, the waveform) pull the small preview copy down once;
- nothing anywhere is still a plain 404.
This is also what makes `cleanup` safe: after the big local files are deleted the episode still plays. Three new
tests cover it (mirror → serve from disk → take the disk copy away → served from R2), **130 passed, 4 skipped**.

## Ready to deploy on Render, waiting for one key (2026-11-02)
- `app/backend/Dockerfile` — the API image (python:3.11-slim + ffmpeg, the built screens inside it, no pipeline, no
  models: the heavy steps go to the RunPod workers). `app/backend/build_frontend.sh` builds the screens into it.
- `render.yaml` — the blueprint (web service + Postgres 16, 50 GB disk, health check `/api/info`); the secrets are
  deliberately **not** in it.
- `app/scripts/deploy_render.py` — one command does the lot: points the service at our repo and branch, makes the
  Postgres database, copies the settings and the **keys straight out of `app/.env`** (never printed, never
  committed), deploys and waits for it to go live. Nothing is sent without `--apply`.
- **Proved the image before deploying it**: run against a clean Postgres it seeded the owner's account from
  `ADMIN_USERNAME`/`ADMIN_PASSWORD`, signed in, refused a wrong password (401) and an API call with no session
  (401), served the screens, and made an upload-only show (`profile.uploads = true`, no Mac folder).
  Worth knowing: `auth.seed()` only ever makes the **first** account, so `ADMIN_USERNAME` must be set before the
  deployed database gets its first user — it will not change an existing password.
- **Still needed from the owner**: a **Render API key**, and the Render URL added to the R2 bucket's CORS
  `AllowedOrigins`. Also to decide: the repo is private, so Render's GitHub app needs access to
  `Sameer2748/mirror-video` (or we deploy the image from Docker Hub instead).
- **What the deployed box can and cannot do**: accounts, shows, uploads, the queue, the editor and review — yes.
  **Processing an episode — not yet**: only the lip scan has a worker image, so every other step would look for
  Whisper, the models and the pipeline scripts, which are not in the API image. Processing stays on the Mac until
  the other worker images exist.

### The cloud scan's output drives the next step too (same day)
Matching files is not enough — the point is that the pipeline can carry on from them. So:
- `lipmerge.py` was run **on the cloud scan's `lips_nets`** (with the Mac's `lips_mesh`): 42 merged files.
- Against the Mac's own `lips_range`, on everything the pipeline decides from — talking intervals, visual
  intervals, frame counts, face boxes, track times, mouth and aperture — **41 of 42 files are identical**, and the
  42nd differs by **one pixel** on one of three face boxes (the pipeline's own tolerance is 2 px) with the talking
  intervals identical. The rest of each file differs only in 2nd-decimal float noise and `method.device`, which
  honestly records `cpu` instead of `mps`.
- **A second real bug, found the same way**: the cloud path never wrote `lips_nets/ranges.json`. `lipgpu` writes it,
  and both following steps read it (`lipmesh --ranges`, and `lipmerge` defaults to `NETS_DIR/ranges.json`) — each
  worker only knows its own share, so the whole list has to be written by the app. Without it the scan reported
  success and the next step had nothing to work from. `cloud.lip_scan` now writes it, and also checks the pieces it
  got are the pieces it asked for (the right *count* is not the right *pieces*).
- `tests/test_cloud.py` (5 new tests) pins all of it: lipgpu's piece list including the unclamped last piece, the
  full `ranges.json`, a failed share being re-sent while the finished ones are kept, and a share that keeps failing
  being reported rather than hidden. **135 passed, 4 skipped.**
