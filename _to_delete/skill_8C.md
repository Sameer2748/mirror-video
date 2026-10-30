### 8C. Dynamic strip (HIH only, owner 2026-10-22) — the strip follows the US caption under it

**Why.** HIH's US video has English captions burned in: usually 2 lines, sometimes 1, and in a few places 3
(ep55: 2 lines 662 s, 1 line 65 s, 3 lines 14 s). A fixed 2-line strip lets a 3-line caption show above it; a strip
tall enough for 3 lines everywhere, with one line of Italian in it, looks bad. So the strip is 2 caption lines tall
and grows to 3 only where the footage on screen carries a 3-line caption, and the Italian cards **fill** the strip:
2 lines of text in a 2-line strip, 3 in a 3-line strip. Every word keeps its own spoken time.

**Only HIH uses it** (`sub_style: "dynamic"`). Another show gets it only when the owner says so.

```bash
# (Step 0B) mksrt writes every dub word with its time: W/<SHOW>_EP<N>_IT_words.json
python3 $T/dubstitch.py captions --work $W --video "$SRC_VIDEO"        # OCR (tesseract), resumable, any time after ingest
# ... Steps 1-7 as usual (groups, fastfix, editor review, render, verify, bookends) ...
python3 $T/dubstitch.py dynsrt --work $W --words $W/${SHOW}_EP${N}_IT_words.json --out $W/${SHOW}_EP${N}_IT_dynamic.srt
python3 $T/dubstitch.py subs --work $W --style dynamic --srt $W/${SHOW}_EP${N}_IT_dynamic.srt --out $OUT/${SHOW}_EP${N}_IT_final_it_subs.mp4
python3 $T/dubstitch.py translate --work $W --srt $W/${SHOW}_EP${N}_IT_dynamic.srt --out $OUT/${SHOW}_EP${N}_IT_english_google.srt
python3 $T/dubstitch.py subs --work $W --style dynamic --srt $OUT/${SHOW}_EP${N}_IT_english_google.srt --out $OUT/${SHOW}_EP${N}_IT_final_en_subs.mp4
```

1. **`captions`** reads the US captions with tesseract OCR, 4 frames a second, in 60-s pieces (`W/captions/`,
   resumable, `--max-seconds`). Frames with the same text form one caption card: start, end, number of lines, text,
   top/bottom rows. A frame is OCR'd only when its caption area changed. Only one stack of evenly spaced lines at
   the bottom counts (specks of picture texture read as text are dropped). → `W/src_captions.json` (+ the median
   rows per line count = where the strip must go). Needs the `tesseract` program (Mac: `brew install tesseract`);
   numpy + Pillow only, no OpenCV. ep55 (760 s): under 1 minute with 4 workers.
2. **`dynsrt`** maps every 40 ms of the Italian timeline through the FINAL edit list (`manifest_groups.json`, after
   fastfix and the editor) to the US moment on screen, and looks up that moment's caption lines. Strip lines needed =
   3 under a 3-line caption (zone grown 0.3 s each side; a 2-line gap under 1 s between two 3-line zones becomes 3),
   else 2. Then the Italian words (from `*_words.json`) are grouped, chunk by chunk (a silence of 1.5 s+ always
   ends a card), into the fewest cards that fit: a card's strip = the tallest strip needed under it; its text must
   fit that many lines at Poppins Bold 40 (width 1000 px on 1080); at most 5 s of speech in a 2-line strip, 7 s in a
   3-line strip; cards end after a sentence end where they can, never after an article or preposition; the text is
   wrapped in balanced lines that end after punctuation where possible, and it fills the strip (at least
   2 words per line, so a 2-line strip never carries one short line). Each card is held 0.5 s after its last word,
   never into the next card and never into a taller strip. The run stops if a word is lost (words in = words out).
   → the dynamic SRT (cards with their line breaks) + `W/dynsubs_plan.json`.
3. **`subs --style dynamic`** burns any SRT with the plan: per frame the strip is `max(lines needed there, the
   card's strip)`; an empty strip wherever a US caption shows with no card; pauses under 1.5 s keep the previous
   card; no strip-off under 0.5 s. Strip rows come from the measured caption rows ± 18 px (ep55: 2-line 912-1056,
   3-line 855-1056 on 1080). The English SRT keeps the Italian cards' times, so it gets the same strips; its text
   is re-wrapped into the card's lines (font down to 28 px only if needed, reported).
   **Self-check:** the 2-line strip present on every planned frame, the 3-line rows on every 3-line frame, same
   length as the clean master; `W/subs_qc_<name>.jpg` shows 3-line-strip cards first — look at it.

**Order in the app:** `captions` runs right after ingest (parallel). For a dynamic show the English translation is
made from the dynamic SRT, so `dynsrt` → `translate` come **after** the editor's review (the cards follow the
final edit), then both subtitle videos, then `check`.

**Tested** on HIH ep55: 3 three-line zones (Italian 247-251 s, 252.7-258.1 s, 734.4-741 s); 166 cards (161 on 2
lines, 5 on 3); 1732/1732 words; no US caption visible above the strip in the QC frames.
