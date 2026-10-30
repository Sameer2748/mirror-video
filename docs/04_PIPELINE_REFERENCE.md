# Pipeline reference — commands and data files

The app drives these commands and reads/writes these files. Full manual: `skill/SKILL_dub-clip-video-stitcher_v18_full.md`
("Show profile" line 82, "Episode run sheet" line 108, then one section per step). Real files: `examples/`.

## Run sheet per show

Common: `T=adaptation-tool/scripts`, `M=adaptation-tool/data/models`, `W=<work folder>`.

| Variable | TDMB | TOLR | WBM |
| --- | --- | --- | --- |
| `DL / SL / LABELS` | it / en / IT,US | it / de / IT,DE | it / en / IT,US |
| `LIP` (Step 4B) | 0 | 1 | 0 |
| `GSIZE` | 8 | 7 | 8 |
| `DRIFT` (`--max-drift`, ms) | 4000 | 4000 | 500 |
| `CLAUSES` | 0 | 0 | 3 |
| `TRANS` (Step 7B) | 0 | 1 | 0 |
| `ENDING` | kept | kept | `--no-ending` |
| `STYLE` | strip | box | box  (HIH: `dynamic`, see below) |
| `render --subs` | keep | (clean source) | keep |
| SRTs | given | given | Step 0B `mksrt.py` |

Order (the app's `pipeline/steps.py` builds exactly this):

```
[mksrt.py dub/src if SRT missing]
words --side src  --clauses N      words --side dub --clauses N     (parallel)
analyze --clauses N  → merged.py → [Step 2: spec.py → mkdec.py → decisions.json]  (gate: needs_input)
sheet --out MAP --labels L         manifest
[lipgpu.py ∥ lipmesh.py → lipmerge.py → lipsync]   (TOLR; the scan can start right after ingest)
groups --size G --max-drift D
render --subs keep                 verify
bookends [--no-ending]  |  bookends --out W/clean_noglow.mp4 → transitions
subs (dub)   translate   subs (en)   check
```

Long commands take `--max-seconds` and resume; **exit code 3 = not finished, run again**.

## Work-folder files

All times in **ms**. Indexes are 0-based. "dub" = Italian, "src" = source (US/DE). Older code says `it_*`/`us_*`
for dub/src.

### `config.json` (written by `analyze`)
Input paths: `dub_srt`, `dub_audio`, `src_srt`, `src_video`, `models`, … (absolute paths of the run).

### `sentence_mode.json`
`{"clauses": 3}` — written by `--clauses`; read by every later step.

### `sentences_dub.json`, `sentences_src.json` (Step 1A `words`)
`{srt, media, lang, model, sentences: [{idx, text, start_ms, end_ms, srt_start, srt_end, …}]}`

### `prep.json` (Step 1B)
`{dub: [{index, start_ms, end_ms, text}], src: [...], sim: <similarity matrix>, auto: <automatic alignment>}`

### `rows.json` (Step 1B, updated by later steps)
`{rows: [{idx, dub_start, dub_end, dub_text, src, src_last, src_start, src_end, src_text, score, notes, flag}], repeat}`
`flag` after `sheet`: `null` = automatic match, `"green"` = decided in Step 2 (group/assign/confirm),
`"red"` = gap or unmatched. Before Step 2, the rows `analyze` marks as unsure are the ones to read
(`pipeline/decisions.py: flagged_rows`).

### `merged.txt`
Human reading view for Step 2 (`merged.py W 95`): each dub row with its neighbours and candidate source sentences.

### `spec.py` → `decisions.json` (Step 2)
`spec.py` is a list of calls:
```python
G([(0,"Erano a un passo"),(1,"L'allenamento"),(2,"voce stridula")], 0, "almost at the track", "reason")   # group rows → src
A(19, "Viola sorrise", 18, "Oh, so that's how", "reason")          # assign row → src (int or [first,last])
C(row, "why")   GAP(row, "kw", "why")   U(src, "kw", "why")   R(later_row, earlier_row)   # confirm, gap, unused source note, repeat
```
Each keyword must appear in that row's real text (`mkdec.py` asserts it), so a wrong index fails loudly. `mkdec.py W W/spec.py` writes
`decisions.json = {groups: [{members, dub_prefixes, src, src_prefix, reason}], assign: {row: {dub_prefix, src,
src_prefix, reason}}, confirm, repeat, gap, unused_notes: {src: note}}`.

### `manifest.json` (Step 4)
`{total_ms, segments: [...], bleed_report: [...]}` — one segment per dub row, same shape as below.

### `lipsync.json` (Step 4B, TOLR)
`{settings: {rule, use, mouth_thr: 0.007, min_lip: 0.4}, sentences: [{idx, start_ms, end_ms, text, lips: [[s,e],…],
lip_s, speaking}]}` — per **source** sentence. `lips_range/` holds the merged per-frame scan.

### `manifest_groups.json` (Step 4A) — the edit list render uses; the app's timeline is built from and back into it
```json
{
  "total_ms": 440307,
  "segments": [
    {"kind": "clip", "it_idx": 0, "it_start_ms": 0.0, "it_end_ms": 2677.2,
     "us_start_ms": 1474.1, "us_end_ms": 3174.1, "it_text": "...", "us_text": "...",
     "stretch_factor": 1.575, "note": ""}
  ],
  "bleed_report": [],
  "groups": [
    {"rows": [0,1,2], "it0": 0, "it1": 8400, "us0": 1474.1, "us1": 6808.0, "stretch": 1.57,
     "flags": ["whole group plays 1.57x slower (limit 1.25)"], "drift": {"0": 19, "1": 148, "2": 420},
     "max_drift": 420, "trims": [], "removed": [], "kept": 5333.9, "removed_ms": 0.0,
     "why": "split: picture drifted 1.9s from the voice", "first_seg": 0, "break_before": "start", "note": ""}
  ]
}
```
Segments tile the dub timeline with no holes (`it_start_ms`..`it_end_ms`); each plays source `us_start_ms`..`us_end_ms`
at `stretch_factor` = source length / slot length (>1 = slow motion). Groups carry the flags the editor needs
(speed, drift, trims). Hand fixes so far were made by editing this file (ep17 row 19: `us_start 60000`) — exactly
what the editor will do.

### Outputs and checks
`qc_sheet.jpg` (verify: one frame per piece next to its expected source frame), `final_check.jpg`, `times.txt`,
`analysis.txt`, mapping + groups (+ lipsync) workbooks, the three videos, `_english(_google).srt`.

## Examples

| Folder | What it shows |
| --- | --- |
| `examples/wbm_ep17/` | clause mode, no SRTs (Step 0B raw/sentences/clauses SRTs), full `spec.py` written by meaning with QC notes, manual translation `todo_done.tsv`, QC frames before/after |
| `examples/tolr_ep24/` | lip-scan show: `lipsync.json`, `lips_range/`, lip-sync review sheet, Gemini-vs-our-scan and sentences-vs-clauses comparisons, `spec_clause_mode_test.py` |

Input videos/audio are **not** copied (too large); they are in the show folders on the Mac (paths in `CLAUDE.md`).
Absolute paths inside `config.json`/`sentences_*.json` are from the Linux VM run (`/sessions/...`); on the Mac they
map to the show folders.

## HIH: the dynamic strip (2026-10-22)

HIH = the WBM run sheet (Whisper both sides, no SRTs) with three extra steps for the subtitles (skill v19, Step 8C):

```
captions --work W --video SRC.mp4                      # right after ingest, parallel: US captions read (OCR) -> W/src_captions.json
... words .. analyze .. Step 2 .. sheet .. manifest .. lip scan .. groups .. fastfix .. REVIEW (editor) ..
dynsrt --work W --words W/HIH_EP<N>_IT_words.json --out W/HIH_EP<N>_IT_dynamic.srt     # -> W/dynsubs_plan.json
render .. verify .. bookends
subs --style dynamic --srt W/HIH_EP<N>_IT_dynamic.srt --out ..._it_subs.mp4
translate --srt W/HIH_EP<N>_IT_dynamic.srt --out ..._english_google.srt
subs --style dynamic --srt ..._english_google.srt --out ..._en_subs.mp4        then check
```

`src_captions.json`: `{cards: [{start, end, lines, text, top, bottom}], geometry: {"2": {top, bottom}, "3": ...}, seconds}`
(source seconds, source pixels). `dynsubs_plan.json`: `{tick: 0.04, need: [2|3 per tick of the dub timeline], raw:
[caption lines under each tick], cards: [{start, end, L, lines}], geometry, src_size, font_size}`.
