# 15 — Locked episode flow (QC’d IMTS EP69–71)

**Do not change this logic unless Sameer says to change it.** Bugs in the editor, deliverables list, or leftover-file cleanup may still be fixed. Matching, grouping membership, how a group is played, unused-edge fill, scene-edit scope, stretch 0.8–1.25×, and the step order are frozen.

Live: `handover-doc-review` → https://dubstudio-yg5s.onrender.com (`srv-dar686jncjis73c9rr90`). Code: `app/backend/pipeline_tools/dubstitch.py`, `app/backend/dubstudio/pipeline/steps.py`, `app/backend/dubstudio/timeline.py`, `app/backend/dubstudio/runner.py`. Groups: `--size 7 --max-drift 500` (no `--trim-fast`).

Older grouping notes: `IMTS_EP69_one_lip/GROUPING_LOGIC.md`. TAB-week platform issues: `docs/13_WEEK_ISSUES_AND_FEATURES.md` (that week did not rewrite stitch logic).

---

## Step order (after matching)

1. Mapping sheet → edit list (`manifest`)
2. Lip scan (0.4 s of moving lips on the **source sentence** = lip row)
3. Lip-sync rows
4. **Groups** (membership + playback below)
5. Shot library → face map
6. Scene edit (Opus, **slow leftovers only**, 0.8–1.25× already left alone)
7. Use scene → **fast trim** (over 1.25×, from last; lip groups skip groups `--trim-fast`)
8. Review → render → verify → bookends → transitions (5 styles, seed 24) → subs

Matching (locked with this flow): Claude punctuation-first source cards; ignore SRT file paths and always use this episode’s sentence JSON so cloud matching cannot re-glue cards.

Speed window for picture: **0.8×–1.25×**. Stretch_factor = dub duration / source duration.

---

## Groups — who is in a group (unchanged)

- Same type (lip or narration), at most **7** rows.
- One row of the other type may sit in the middle only when that type continues after it and the footage across it plays **0.5×–2×**.
- Breaks: dub repeat, source goes backwards, source jump > 2 s, footage between the rows used elsewhere, picture/voice drift > 0.5 s (split).

## Groups — how a group is played (EP71 QC)

**Each lip line plays its own matched source** over its own dub slot (EP69). A pause between two lines is split halfway so the take stays continuous. Do not even the whole group to one speed — that puts the next mouth on this line. Unused before/after is not added here; scene-edit Claude decides that on leftovers.

Narration blocks: first source start → last source end the same way. Unused between members stays in.

If several matched rows share one picture span, they still play that span (not empty black).

## Unused edges (Opus on leftovers only)

Grouping does **not** attach unused before/after. Matched takes stay as matching+lip-group play left them. Unused *middle* inside a lip group take still stays (lunch/rice).

Scene-edit **Opus** may attach one adjacent unused piece on a **slow leftover** only when all of these hold: leftover (not lip), slower than 0.80×, unused immediately before/after with nobody else matched (40 ms guard), and it will not put another line's mouth on this line. Code refuses unused on lip groups even if Opus asks. If unsure, unused=none.

## Scene edit — do not reopen

B-roll only for slow leftovers (and wrong-person). Lip-sync conversation already in range stays the grouping take. Fast leftovers: `fast_trim` from last after scene edit.

**Keep grouping is one take.** Shot-library labels are for who is on screen, not extra cuts. If Opus (or the fallback) keeps grouping, write the grouping segment(s) — do not emit one clip per library shot. EP71: one leftover unit over shots 123–134 became 39 flashes and extra transitions; Opus had said keep as one slowed piece.

**Leftover rows that share source stay one forward take.** If IT#49–51 all sit on the same Tom close-up (overlapping in/out), play first source start → last source end over those dub lines, then split in time order. Do not play, jump back, play again. `fast_trim` from last of that take, not each overlapping row. QC case 8 fails leftover rewind. True source jumps (no overlap) stay cuts.

---

## Editor

- Timeline is **one block per picture take** (a lip group with unused middle is one clip; a scene-edit B-roll is a cut). Sentence text stays in the inspector. Razor still splits a take.
- Monitor must stay inside the clip’s source in/out. The HTML player used to run ~1 s past out-point (hard-seek only if lag > 0.8 s). Final ffmpeg was already correct. Live screens are rebuilt `frontend_dist` (`index-_ydW0bIB.js`): clamp to last in-clip frame; thumbs do not round past out.
- A leftover **< 40 ms** hole between takes is one frame of float, not a black gap. Absorb it into the previous clip. Do not block first-draft / bulk render on it.

## Latest files only

- Groups always overwrites `manifest_groups.grouping.json` from **this run’s** groups output.
- **Process again** (`create_run`): wipe work except `input/`; clear the output folder; delete this episode’s previous **work** files on R2 so old grouping/matching JSON cannot be fetched back.
- **Rerun from groups**: drop leftover grouping / automatic-edit / editor draft / render-gate files. Keep lips/ASR (that is a partial rerun, not Process again).
- **Render** (bulk first draft or after an editor change): drop `segs` / `segs_groups` / concat / `video_groups_nosubs.mp4` / `_before.mp4` / old `*_timeline_v*.xml`. Picture and transitions rebuild from the **current** timeline. Same-named masters are overwritten.
- **Deliverables**: one row per kind (latest XML version, one of each video/sheet). Do not list previous render + new render together.

---

## Do not process unless asked

IMTS 69/70. IMTS leftover 197–258. Show **IMTSITDE** (including future queue).

---

## Issues we hit and what is locked as the fix

| What we saw | Cause | Locked fix |
| --- | --- | --- |
| EP71: lunch / rice / Tom picture early even though texts matched 1:1 | Lip-group **evening** (each line did not keep its own US in/out; later the evened 11-row Horton–Tom group) | Do not even. Play **first US start → last US end** over the group’s dub, unused **middle** kept |
| Local span video was right; first cloud review still showed lunch on “diverted the conversation” | Leftover `grouping.json` from an older evened run. Scene edit copied it if present and ignored the new groups file | Snapshot `manifest_groups.grouping.json` after groups; drop stale edit files on Process again and rerun-from groups |
| Same lunch-on-diverted after unused-edge fill | Unused-**edge** remapped the whole lip take into the next line | Unused-edge is **leftovers only** (and only if slow); lip groups keep unused **middle**, never unused before/after; leftover never the next group's match |
| EP69 opening: matched D0/D1 off in the cut | One speed across the lip group, plus unused-edge on that group | Each lip line keeps its own source; Claude decides unused on leftovers only |
| Cloud matching re-glued Claude source cards | SRT path / remapped work paths | Ignore SRT paths; always this episode’s sentence JSON; Claude cards before regex split |
| Sheet fail `SRC#36` missing prefix | mkdec assert on a glued source line | Same: keep Claude source sentences on cloud |
| Editor looked like a cut every sentence | One timeline clip per lip-group slice | `merge_render_takes`: one clip per picture take; B-roll / speed / source jump stay cuts |
| Keep-grouping leftover became 28–39 clips of 0.2 s (EP71 rows 114–116, Tom’s eyes) | Scene edit sliced grouping US on every **library shot**, then wrote each slice; Opus had said keep one piece | Keep grouping writes grouping segments only, not `shots_over` parts |
| Editor flash of ~1 s at clip end; trim did not remove it; final render OK | HTML5 player ran past `s1`; seek tolerance 0.8 s; thumbs rounded to 500 ms past out | Clamp monitor + thumbs to in/out; pause on last in-clip frame. **Rebuild `frontend_dist`** or live keeps the old JS |
| First-draft / bulk render 400 “gap 10:11.1–10:11.1” | 34.3 ms float hole; `gaps` used 34 ms | Treat < 40 ms as hairline; absorb into previous clip |
| Re-render / Process again still looked like an old cut | `scene_edit` reused leftover grouping; render skipped existing `segs_groups` / `_before.mp4`; Process again did not wipe work | Latest-run snapshot; drop render cache on every kick render; Process again wipes work except `input/` plus output dir plus R2 **work** json |
| Deliverables showed v2+v3 XML and two copies of the same videos/sheets | Every R2 upload row kept; Premiere XML is versioned | List **latest per kind** only; drop old `timeline_v*.xml` on a new render |
| Live editor fix missing after a backend-only commit | Docker copies prebuilt `app/backend/frontend_dist` | Run `app/backend/build_frontend.sh` and commit the new JS when Editor.tsx changes |
| EP71 IT#16 ×0.02 (Kira look) | `trim_shared_footage` clashed leftover vs **each lip-group slice**, padded 80 ms into the next slice, looped 50×; scene-edit/QC used **group** 1.25× | Clash leftover vs the **lip-group span**; trim **every** leftover row in that group (not only IT#16); never pad 80 ms inside that span; scene-edit leftover units + QC case 7 use **clip** source/dub; Opus only on those leftovers |
| EP71 ~04:42 Tom four times (IT#49–51) | Leftover rows kept overlapping US of one close-up; unused-edge + `fast_trim` from last on **each** row rewound the same face; editor pink marks = source jump | Overlapping leftover US = **one forward span** (groups + `grouping_takes` + `fast_trim`); QC case 8 fails leftover rewind |
| Leftover vs lip (IT#61 80ms, 27–28 ×0.12, leftover growing into next lip) | Unused-edge / leftover-span / trim each wrote US; overlapping lip was treated as free; leftover one-span took min–max into the next lip take | One owner: leftover source **must not occupy another group's take** (`enforce_leftover_off_others` after unused-edge and leftover-span). Lip group is never the cut. Slow leftover next to a lip group goes to scene-edit, not into the lip picture |

EP71 check after the locked grouping + unused-middle: diverted US 102.14–108.38, lunch 108.38–110.99, stretch 1.324× (same as the good local QC). First-draft rebuild (timeline v3): 156/156 segments encoded, final check passed, 703.640 s.
