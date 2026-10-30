# Project context — everything decided so far

Written Oct 15, 2026 from the full working conversation between Sameer (owner) and Claude, so a new agent can
pick up with the same understanding. Read with `02_PLATFORM_DESIGN.md` (the app) and the skill (the pipeline).

## 1. What the business does

Sameer's team takes a short-drama / audio-story episode that already exists as a **source video** in one language
(US English, or German) and an **Italian dub** of the same story recorded as audio only. The dub is a re-written
adaptation, not a line-by-line translation: sentences are merged, split, dropped, re-ordered. The job is to build an
**Italian video**: play the Italian audio unchanged and cut the source footage under it so that every Italian line
shows the picture that matches its meaning (and, on some shows, a mouth moving when someone talks).

Each episode delivers three videos plus sheets:

- `<SHOW>_EP<N>_IT_final_clean.mp4` — the Italian video, no subtitles (source opening + dub episode + source ending).
- `<SHOW>_EP<N>_IT_final_it_subs.mp4` — with Italian subtitles burned in.
- `<SHOW>_EP<N>_IT_final_en_subs.mp4` — with English subtitles (machine translation of the Italian).
- the mapping sheet (`..._mapping.xlsx`: every Italian row → its source sentence), the dynamic-groups review sheet,
  the English SRT, and on lip shows the lip-sync review sheet.

## 2. The pipeline (it works today; the app wraps it)

`dubstitch.py` (one file, many sub-commands) plus helpers, documented in `skill/` (v18). Steps:

| Step | Command | What it does |
| --- | --- | --- |
| 0B | `mksrt.py` | no SRTs? Whisper transcribes the audio/video, the script cleans the text → SRTs (WBM needs this) |
| 1A | `words` | exact start/end of every sentence (or clause) from the audio, per side |
| 1B | `analyze` | automatic alignment (similarity + dynamic programming); marks rows it is unsure about |
| 2 | **read by meaning** | the only non-command step: someone reads the unsure rows and writes `spec.py` → `mkdec.py` → `decisions.json` |
| 3 | `sheet` | integrity check (no source used twice by accident) + mapping workbook |
| 4 | `manifest` | the row-level edit list (`manifest.json`) |
| 4B | `lipgpu.py` + `lipmesh.py` + `lipmerge.py` + `lipsync` | lip scan of the source video; which source sentences show a moving mouth; lip-sync rows (TOLR only) |
| 4A | `groups` | merges rows into continuous blocks of source footage (fewer cuts, steadier speed) → `manifest_groups.json` |
| 5-6 | `render`, `verify` | cut and time-stretch every piece, join; verify each piece shows its own footage (`qc_sheet.jpg`) |
| 7 | `bookends` | add the source opening and (optionally) ending → clean master |
| 7B | `transitions` | light leak / blur dissolve / zoom blur at clip switches (TOLR) |
| 8-9 | `subs`, `translate`, `subs` | burn Italian subs; translate to English (Google; fallback: export todo → translate → import); burn English subs |
| 10 | `check` | final package check, must print `FINAL CHECK PASSED` |

Step 2 is where judgement lives. In the conversation Claude did it by reading `merged.txt` (the n-1/n+1 method:
read the row and its neighbours on both sides, judge by meaning, never by score). The spec language:
`G(members, src, src_prefix, reason)` group, `A(row, prefix, src, src_prefix, reason)` assign, `C` confirm,
`GAP`, `U` unused note, `R` repeat. Real example: `examples/wbm_ep17/spec.py`. In the app the editor makes these
choices on the Check-matching screen (or AI when a key is set; see design).

## 3. The shows

| Show | Dub vs source | Notes |
| --- | --- | --- |
| **TDMB** | Italian vs US | source has burned-in English captions → "strip" subtitle style covers them; whole sentences; groups size 8 |
| **TOLR** | Italian vs German | talking faces → **lip scan** + lip-sync rows; groups size 7; transitions on; box subs; whole sentences; use the "NO SUB"/clean source file |
| **WBM** | Italian vs US | **no SRTs** (Step 0B makes them; Italian script `.docx` in `italy/`, no English script); **clause mode 3**; groups size 8 with `--max-drift 500`; box subs; **no source ending** (`--no-ending`); outputs in `outputs/ep<N>/` (note the s) |
| **HIH** | Italian vs US | no SRTs (Whisper both sides; Italian `.docx` script); the US video has English captions burned in (2 lines, sometimes 1 or 3) → **dynamic strip** subtitles (2 or 3 lines tall, following the caption under it; Italian cards fill it); outputs in `output/ep<N>/` |
| **MVS** | German vs US (earlier show) | episode numbers are shifted: **German N = US N+2** → the profile field `episode_offset` |

Full profile table: skill, "Show profile". App presets: `app/backend/dubstudio/presets.py`.

## 4. Decisions made, with the reason (do not reopen without the owner)

### Lip scan: our local scan, not Gemini
- Tested Google **gemini-3.5-flash** (through the owner's AI gateway, video at 5 fps via `video_metadata`) on TOLR
  ep24, 174 German sentences, against our scan (`lipgpu` + `lipmesh` + `lipmerge`).
- "Speaking" mode agreed with our scan on 139/174 sentences; a "mouth movement" mode that asked exactly what our
  scan measures agreed on 142/174. 3-run voting with our scan as tie-breaker was also tried.
- Rendered both: **our scan's video looked better.** Why: our scan reacts to any lip-shape change of a visible
  face, which is what makes a moving mouth land on the voice; Gemini judges "is this person speaking", which
  misses short mouth moves and marks some narration over a face. Same clip list, different lip rows → different groups.
- Owner: "lets keep our method". Gemini was **not** added to the skill. Scripts kept for reference:
  `pipeline_tools/lipgemini.py`, `gemrange.py`, `gemmouth.py`; comparison: `examples/tolr_ep24/ep24_compare_gemini_vs_ourscan.xlsx`.

### Lip threshold stays 0.4 s
- A sentence counts as "lips moving" when the scan sees ≥ 0.4 s of mouth movement (`--min-len 0.4 --min-lip 0.4`).
- Tested without/halved (0.2 s / 0.2 s) on TOLR ep24 and WBM ep9: halving added 16 lip sentences on ep24, and when
  checked frame by frame only 4 of those were really someone talking. Owner: "lets keep 0.4".

### Clause mode (new in v18)
- Long dub sentences that hold several beats ("She turned, smiled, and walked away") were matched as one block,
  so the picture under the later half was wrong. Clause mode cuts a sentence after `, ; :` or before a dash
  **only if both parts keep ≥ N words** (N = 3). Short ones like "I am going, away" are not split.
- Set with `--clauses N` on `words` and `analyze`; saved in `W/sentence_mode.json`; env `DUBSTITCH_CLAUSES`.
- **WBM: on (3)** — it fixed the ep17 QC notes. **TOLR: off (0)** — ep24 test: 73% of screen time identical,
  25% shifted < 1 s, 2% other footage, 44 → 54 clip switches, 20 German sentences split into lip + no-lip parts,
  1 lip sentence lost. The lip scan decides per German sentence, so splitting changes which parts count as
  "lips moving"; not worth it. **TDMB: 0** (validated with whole sentences).
- Comparison: `examples/tolr_ep24/ep24_compare_sentences_vs_clauses.xlsx`.

### WBM ep17 QC round (why the design has flags and markers)
Codex (another agent) made WBM ep16–25 earlier. QC on ep17 came back with:
01:00–01:03 AV mismatch; 01:02 should show Viola smiling; 01:17–01:27 AV mismatch; 02:15–02:18 Viola speaking
unnecessarily; 06:04 AV mismatch; 06:59–07:05 visuals blurry; audio ends 07:21 but picture runs to 07:24.
Claude rebuilt ep17 in clause mode, re-read every row with the US frames, and fixed each spot (e.g. row 19 assigned
to the end of the US shot where Viola smiles, hand-set `us_start 60000`; row 46 narration given a shot with her
mouth still). The **ending** was not a black screen: `bookends` appended the source's own last seconds after the
Italian voice ended → WBM now uses `--no-ending`. `--max-drift 500` stops a group from letting a clause's picture
run more than 0.5 s from its voice (Codex's version drifted 3.7 s). Result: `WBM US-IT/outputs/ep17_v2/`;
all work files: `examples/wbm_ep17/`.

### Platform decisions (approved)
Local web app; editor decides flagged matches (AI when a key is set); files stay in show folders (paths +
fingerprints); Mac only; Premiere XML export in milestone 6; flag limits slow < 0.67×, fast > 1.5×, drift > 0.5 s;
one editor, no login. Details: `02_PLATFORM_DESIGN.md`.

## 5. Known quirks the app must handle

- **TOLR ep28:** source SRT timed to another cut (~15 s off) and a 14.5 s stretch of dub with no footage (warning).
- **TOLR ep44/45:** the dub SRT has an extra pasted copy of cues past the audio's end → drop cues past the end.
- **TDMB:** burned-in captions in the source → strip style; ep25 had clips bleeding across a camera cut.
- **MVS:** German N = US N+2.
- Source fps 29.97 vs 30, sometimes variable → work in ms, normalise at render.
- **Google translate 429** (rate limit): `translate --export-todo`, translate the todo file, `--engine file --import`;
  name the result `_english.srt`, not `_english_google.srt`.
- Big renders filled the disk and one transitions run ran out of memory (fixed: lazy per-transition frame cache).
  Check free space before proxies/renders.
- Commands accept `--max-seconds` and **resume**; exit code 3 means "run me again" (the runner does this).

## 6. AI usage

The owner has an AI gateway (OpenAI-compatible LiteLLM, budget $100, about $8.9 used by Oct 15, 2026). It serves
Claude (`claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-sonnet-4-6`) and `gemini-3.5-flash`. It was used for the
Gemini lip tests and is now used for **Step 2 matching with Claude Sonnet 5.5**, which agreed with the hand read
on 36–37 of 38 flagged rows (ep17) and 18–19 of 19 (ep24) for about $0.10 per episode. The owner asked for Claude
to be used here. The key is in `app/.env` (gitignored); never commit or copy it. Details: `05_AI_GATEWAY.md`.

## 7. How the owner likes to work

- Plain, short reports: what changed, what to look at, numbers that matter. He checks videos, not code.
- Do it, test it on a real episode, then show the result; ask only when a choice is really his.
- Keep a written, detailed list of what is built and what is next (`03_PROGRESS.md`).
- File names and output folders exactly as today; never overwrite his inputs.
- He compares versions by watching: give side-by-side sheets/clips when comparing approaches.
