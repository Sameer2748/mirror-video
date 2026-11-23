# How each step handles a line

This is the cut that rendered `IMTS_EP69_per_line_qc/EP69_full.mp4`. It is the code as it runs now: each lip line keeps its own matched picture, and Claude is the only step that may add unused footage.

Speed in this doc is **source seconds ÷ dub seconds**.

| Speed | What the viewer sees | What the pipeline does |
|---|---|---|
| under 0.80× | slow motion, not enough picture | scene edit may add B-roll or unused |
| 0.80×–1.25× | in range | left alone |
| over 1.25× | picture runs too fast, too much source | fast trim cuts source from the end |

A number printed as `stretch_factor` on a clip is the other way up: dub ÷ source. 0.76 there means the picture is playing fast (about 1.31×).

---

## Order

1. Sentences (Whisper words, then Sonnet rebuilds the source sentences)
2. Matching
3. Mapping sheet
4. Edit list (one clip per matched row)
5. Lip scan, then lip-sync rows
6. Groups
7. Shot library, then face map
8. Scene edit (Opus, slow leftovers only)
9. Fast trim
10. Review, render, verify, bookends, transitions, subtitles

Steps 1–6 do not add B-roll. Steps 7–8 may. Step 9 only shortens picture that is too fast. The EP69 file you have stops at render of the scene-edit timeline. It has no bookends, no transitions, and no burned subtitles.

---

## 1. Sentences

Whisper times every word on the dub audio and on the source video. Sonnet then rebuilds the **source** sentences from those words, using punctuation. The dub sentences stay the cleaned SRT sentences.

What it handles: a sentence boundary, a quote, a name. What it does not handle: which English line belongs to which German line. That is matching.

---

## 2. Matching

One German row is paired with the English source time that means the same thing. Two German rows may share one English sentence (EP69 D0 and D1 both sit on “When she heard… There’s no need.”). A row with no English counterpart stays unmatched.

What it handles: meaning. What it does not handle: how that picture is played. A green row can still look wrong later if a group slides the next mouth onto this line. Matching itself is not changed by groups or by scene edit.

Output: `rows.json`, the mapping sheet.

---

## 3. Edit list

One clip per matched row. Dub in/out is that row’s dub time. Source in/out is that row’s matched source time. Nothing is grouped yet. Gaps in the source stay gaps.

Output: `manifest.json`.

---

## 4. Lip scan and lip-sync rows

The lip scan measures moving mouths on the source video. A source sentence is a **lip row** when the moving lips inside that sentence add up to **0.4 seconds or more**. The 0.4 s is a fixed length, not a share of the sentence. A long sentence with 0.3 s of lips stays narration. A short sentence with 0.4 s of lips is a lip row.

A lip row then keeps its own spoken dub slot. Silence beside that slot is given to the neighbour.

What it handles: “is someone talking on screen in this sentence?” What it does not handle: who the person is. That is the face map.

Output: `lipsync.json`.

---

## 5. Groups

Groups decide two different things: **who sits in the same block**, and **which source each line plays**.

Size is 7. Picture may drift at most 0.5 s from the voice inside a block (`--max-drift 500`). A block that drifts more is split in half.

### Who is in a block

Rows are walked in dub order.

- Same type in a row (all lip, or all narration), up to 7, while the source keeps moving forward.
- A block breaks when the dub line is a repeat, the source goes backwards (more than 40 ms), or the source jumps more than 2 seconds.
- **One row of the other type may sit in the middle** only when the original type continues on the other side, the block still fits in 7, and the footage between the two lip sentences would play between 0.5× and 2×. Example: lip, one narration line, lip. That narration is inside the lip block. Two narrations in a row do not get this pass.
- Footage between two lip sentences that some other row already plays is not bridged. The block breaks.

A lip block is marked `lip group`. A narration block is not.

### How a lip block is played

Each line plays **its own matched source** over **its own dub slot**. Speeds are not evened across the block.

If the next line’s source starts after this line’s source ends, the pause between them is split halfway. The take stays continuous, and the next mouth stays on the next line. EP69 opening:

| Line | Dub | Source | Why |
|---|---|---|---|
| D1 “There’s no need.” | 10.95–13.38 s | 4.30–7.49 s | its own match |
| D2 “Yes, there is.” | 13.38–19.35 s | 7.49–11.34 s | its own match, starts where D1 ended |

What this refuses: playing the whole block at one speed from the first source start to the last source end. That is what put D2’s picture on D1.

If one lip line would play faster than 1.5×, that line alone is cut down to the part of the sentence around the moving mouth, at 1.5×. Neighbours are not moved.

### How a narration block is played

Each narration row is set back to its own matched source in/out. A narration block then plays from the first row’s source start to the last row’s source end, one speed, over that block’s dub. Unused source **between** those rows stays in the take. Unused **before** the first row and **after** the last row is not added here.

### What groups do not do

- Groups do not add B-roll.
- Groups do not attach unused footage before or after a clip. That used to stretch a matched mouth onto the wrong sentence. Claude decides unused later, and only on a slow leftover.
- Groups do not trim a clip that is faster than 1.25×. Fast trim does that after scene edit.
- A leftover (narration) must not sit on another group’s picture. If it overlaps a lip take, it is cut off that take.

Output: `manifest_groups.grouping.json` and the groups review sheet.

---

## 6. Shot library and face map

The shot library cuts the source on camera changes and labels each shot: who is in it, the place, the type (talking, reaction, neutral, establishing, action), and whether it may be used as B-roll later. B-roll “yes” is only reaction, neutral, or establishing. Talking, action, and objects are “no”.

The face map ties a face across shots and names who is on screen and who is talking.

What they handle: pictures for the scene edit. What they do not handle: the cut. A label is not a new edit. If scene edit keeps grouping, it writes the grouping clip, not one clip per library shot.

---

## 7. Scene edit

Input is the grouping cut. Opus (Claude) sees the slow leftovers, the pictures, and a ranked B-roll list. Code only does what that answer allows.

### What is left alone

A lip block whose picture already plays at 0.80× or faster is not opened. A narration block is left alone when every clip in it, and the block as a whole, is at 0.80× or faster.

In-range rows inside a slow block are also left alone. The step walks the block and keeps the longest run of neighbours whose own source plays between 0.80× and 1.25× and whose source does not jump backwards. Those rows are not “units”.

### The sub-split (the slow units)

Whatever is left after that walk is a slow unit. A unit starts as **one sentence**. Two neighbouring slow sentences in the same block are merged only when the who-call says they should show the same people. A sentence about someone else does not borrow the neighbour’s B-roll.

On EP69 this was 24 units, 29 sentences.

### Who should be on screen

One text call per slow sentence. It returns who should be shown, and who is only listening. The code compares that with who the face map says the current picture shows.

- **Case 1.** The picture already shows the right person (or the listener, on dialogue). The only problem is speed.
- **Case 2.** The picture shows someone who is not in that set.

### Unused footage (Claude, leftovers only)

Grouping did not add this. For each slow block Opus may answer `unused: before`, `after`, or `none`, and how many seconds.

Code applies it only when all of these are true:

- the block is a leftover, not a lip block (a lip answer is forced to `none`)
- the leftover is slower than 0.80×
- the unused picture sits immediately before or after that leftover
- nobody else is matched to it (40 ms guard)
- it does not cover another group’s take
- one side only, and only enough to reach 1.25×

If Opus is not sure, the answer is `none`. On this EP69 run Opus added unused on three leftovers (the opening narration, rows 53–54, rows 85–86) and refused it on every lip block.

### B-roll

Opus may also ask for one B-roll shot, on one row, before or after the speech, at least 2 seconds. Code will place it only when:

- the shot is on the ranked list: B-roll yes, reaction / neutral / establishing, same place (or the main place of the same scene)
- it is not the picture already playing
- it does not land on a talking mouth
- a narration B-roll has no more than 0.2 s of moving mouth
- at most 3 visual jumps in that sentence
- the own clip does not go faster than 1.25×

If no such shot exists, or Opus said keep the grouping cut, the grouping clip stays. A kept grouping clip is one piece, not one piece per camera shot.

B-roll is for three situations: no matched picture, a long pause with nobody to show, or the wrong person when a same-place reaction of the right person exists. A slightly slow matched mouth is preferred to a jump cut.

### What scene edit does not do

- It does not rematch lines.
- It does not change a lip block that is already at 0.80× or faster.
- It does not add unused onto a lip line.
- It does not trim a clip that is too fast. That is the next step.

Output: `scene_slowfix/manifest_slowfix.json` and `scene_edit.xlsx`.

---

## 8. Fast trim

Only clips whose picture is **faster than 1.25×** (too much source for the dub). Source is cut from the **end** of the clip. The start, which is where the mouth usually is, stays.

Two cases while a whole block is still over 1.25×:

- **Narration block.** The fastest clip inside it is shortened first, from its end, until the block is in range.
- **Lip block.** A narration row inside the block is shortened first. The lip line is not the first thing cut. If there is no narration left to cut, the last clip of the block is shortened from its end.

After the blocks, any single clip still over 1.25× is brought down the same way, from the end. A lip line and a narration line both get this pass. EP69 D1 was 4.30–7.49 s at about 1.31×; fast trim cut 0.15 s off the end, so it plays 4.30–7.33 s.

Nothing under 1.25× is touched. A slow line is not sped up here.

---

## 9. Render, and what comes after on a full live run

Render plays the timeline: each clip is the source in/out fitted to the dub in/out, then the dub audio is muxed. That is the file you have.

On a full live run, after you press Render, three more steps run:

- **Verify** checks each segment against the source.
- **Bookends** put the source’s unused opening and ending back at 1× with their own sound, and stop before a fade to black. The opening is left out when the lip scan sees a moving mouth in it. This range is not B-roll.
- **Transitions** are one of five styles (fade to black, fade to white, light leak, blur dissolve, zoom blur), seed 24, at clip switches. Then subtitles are burned.

---

## What a wrong picture usually means

| What you see | Which step | What it is not |
|---|---|---|
| The English line is the wrong sentence | Matching | not groups |
| The right sentence, but the next mouth is on this line | Groups played one speed across the block | not matching |
| The right mouth, then extra picture from before or after the match | Unused footage | Claude, and only on a slow leftover |
| The right mouth, playing slow | Scene edit kept it because no safe B-roll | not a matching error |
| The end of a line is missing and the next line starts clean | Fast trim cut source from the end | the mouth at the start was kept |
