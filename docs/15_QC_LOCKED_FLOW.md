# 15 — Locked episode flow (QC’d IMTS EP69–71)

The flow we keep on live. Do not drop a step or a grouping rule without a new QC. Detail for grouping: `IMTS_EP69_one_lip/GROUPING_LOGIC.md`. Full local notes: `IMTS_EP69_one_lip/STEPS.md`.

Live code: `app/backend/pipeline_tools/dubstitch.py` + `app/backend/dubstudio/pipeline/steps.py`. Groups command: `--size 7 --max-drift 500` (no `--trim-fast`).

## Step order (after matching)

1. Mapping sheet → edit list (`manifest`)
2. Lip scan (0.4 s of moving lips on the **source sentence** = lip row)
3. Lip-sync rows
4. **Groups** (below)
5. Shot library → face map
6. Scene edit (Opus, **slow leftovers only**, 0.8–1.25× already left alone)
7. Use scene → **fast trim** (over 1.25×, from last; lip groups skip groups `--trim-fast`)
8. Review → render → verify → bookends → transitions (5 styles, seed 24) → subs

The editor timeline is **one block per picture take** (a lip group with unused middle is one clip; a scene-edit B-roll is a cut). Sentence text is still in the inspector. Razor still splits a take if you want a different cut.

## Groups — who is in a group (unchanged)

- Same type (lip or narration), at most 7 rows.
- One row of the other type may sit in the middle only when that type continues after it and the footage across it plays 0.5×–2×.
- Breaks: dub repeat, source goes backwards, source jump > 2 s, footage between the rows used elsewhere, picture/voice drift > 0.5 s (split).

## Groups — how a group is played (EP71 QC)

**This is the lunch/rice fix.** A lip group is one take:

- First matched **source start** → last matched **source end**
- over first **dub start** → last **dub end**
- Unused source **in the middle** stays in (do not cut 01:48.920–01:50.050 between lunch and rice)
- One speed. Do not even inner joins (`lip-smooth` off). Evening put rice picture on the lunch question.

Narration blocks: first source start → last source end the same way. Unused between members stays in.

## Unused edges on a single clip

If a **lone clip or leftover group** is still slower than 1.25× (not enough source for the dub), eat unused source immediately **before and/or after** it. Only the gap no other clip plays (40 ms guard). That lengthens source so stretch moves into 0.8–1.25×.

Do **not** add unused source to a clip that is already too fast. Do **not** eat unused edges on a lip group: that group already kept unused middle as one take; stretching the edges shifts lunch picture onto the diverted line.

## Latest run only

Groups always overwrites `manifest_groups.grouping.json` from this run’s groups output. Process-again and rerun-from groups drop leftover grouping / automatic-edit / editor draft / render-gate files so scene edit, the editor, and render never keep a previous run’s cut.

**Process again** clears the work folder except `input/` (no leftover lips, matching, grouping, or segment cache). **Render** (bulk first draft or after an editor change) drops `segs_groups` and the previous concat so picture and transitions are built from the current timeline.

## Scene edit — do not reopen

B-roll only for slow leftovers (and wrong-person). Lip-sync conversation already in range stays the grouping take. Fast leftovers: `fast_trim` from last after scene edit.

## Do not process unless asked

IMTS 69/70. IMTS leftover 197–258. Show IMTSITDE.
