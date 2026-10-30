---
name: "dub-clip-video-stitcher"
description: "Use when matching a foreign-language dub's SRT+audio against the source-language SRT+video of the same episode (any show: e.g. TDMB Italian vs US, TOLR Italian vs German, WBM Italian vs US without SRTs) to reuse source footage and stitch a localized cut synced to the dub audio, with lip-sync rows, transitions, subtitles and final checks. Self-contained: all tool code is included."
---

# Dub-to-source clip matching & video stitching

Given one episode's **dub** (subtitle `.srt` + audio `.wav`) and the **source** version of the
same episode (subtitle `.srt` + video `.mp4`), produce the complete episode package.

## What changed in v18 (Oct 2026, WBM ep17 QC)
- **Clause mode for long sentences** (`words --clauses 3`, `analyze --clauses 3`): a long sentence is cut at its
  commas / semicolons / colons / dashes into clauses (each at least 3 words) and **every clause is matched to its
  own source clip**, so the picture changes exactly where that part of the voice starts ("…, Viola sorrise
  beffarda, …" now shows Viola smiling on those words). Step 1A explains it, Step 2.6 how to read clauses.
- **`bookends --no-ending`**: the video ends with the dub audio (no source footage after the last line).
- **Tighter groups for clause shows:** `groups --max-drift 500` keeps each clause's picture within 0.5 s of its voice.
- **WBM** (Wolves of Blood Moon, Italian dub vs US video, no SRTs) is in the show profile.
- Translation blocked by Google: the agent may translate the exported lines itself (Step 9).
- `transitions` keeps the source frames of one transition at a time (clause mode on TOLR ep24 had 54 switches;
  holding a whole chunk's frames ran the 4 GB VM out of memory).
- Tested on TOLR ep24 (`output/ep24_clauses`): see the TOLR column of the show profile.

## Start here: this one file is the whole tool kit

**Everything needed is inside this file**: the instructions (Steps 0-14) and the complete source code of every
tool (the Appendix at the end). Share only this file. Any agent (Claude, Codex, another LLM) or a person can
rebuild the tools from it and run any show and any episode; nothing else has to be sent.

**1. Unpack the tools** (Python 3.9+). Save the following as `unpack.py` next to this file and run
`python3 unpack.py SKILL.md adaptation-tool`. It writes every file of the Appendix and checks each md5:

```python
# unpack.py -- rebuild every tool from the Appendix of SKILL.md (each file is checked against its md5)
import hashlib, os, re, sys
skill = sys.argv[1] if len(sys.argv) > 1 else "SKILL.md"
root = sys.argv[2] if len(sys.argv) > 2 else "adaptation-tool"
txt = open(skill, encoding="utf-8").read().replace("\r\n", "\n")
files = re.findall(r"<!-- FILE: (\S+) md5=([0-9a-f]{32}) -->\n~{6}[a-z]*\n(.*?)\n~{6}\n", txt, re.S)
bad = 0
for path, md5, body in files:
    data = (body + "\n").encode("utf-8"); out = os.path.join(root, path)
    os.makedirs(os.path.dirname(out), exist_ok=True); open(out, "wb").write(data)
    ok = hashlib.md5(data).hexdigest() == md5; bad += not ok
    if out.endswith(".command"): os.chmod(out, 0o755)
    print(("ok        " if ok else "MD5 WRONG ") + out)
print(f"{len(files)} files written, {bad} with a wrong md5" + (" -- copy the skill file again, unchanged" if bad else ""))
```

If you cannot run code, copy each Appendix block by hand into the file named above it (the text between the
`~~~~~~` lines, exactly).

**2. Folder layout after unpacking** (keep it; the tools find each other by it):

```
adaptation-tool/
  scripts/      dubstitch.py  pngsubs.py  lipdetect.py  lipfast.py  lipgpu.py  lipmesh.py  lipmerge.py  mkdec.py  merged.py
                mksrt.py  srtclean.py
  third_party/TalkNet-ASD/          (lip-sync shows only; Step 0 downloads it)
  data/models/                      (Whisper + MiniLM, downloaded automatically on first use)
  mac_lipscan/mac_runner.command    (only when the agent works in a Linux VM on a Mac, Step 4B.4)
```

| File | What it is for | Used in |
|---|---|---|
| `dubstitch.py` | every pipeline command (words, analyze, sheet, manifest, lipsync, groups, render, verify, bookends, transitions, subs, translate, check, ...) | all steps |
| `pngsubs.py` | box-style subtitles (transparent PNG stills turned into an overlay) | Steps 8-9 (`subs --style box`) |
| `lipdetect.py` | the reference lip detector (TalkNet-ASD + S3FD + MediaPipe Face Mesh); also the library the faster tools reuse | Step 4B |
| `lipfast.py` | same output as `lipdetect.py --range`, decodes each piece once; library for lipgpu/lipmesh | Step 4B |
| `lipgpu.py` | the heavy half of the lip scan (S3FD + TalkNet) on the GPU (Apple Metal / NVIDIA CUDA / CPU), with safety limits | Step 4B |
| `lipmesh.py` | the light half (Face Mesh lip opening) | Step 4B |
| `lipmerge.py` | joins the two halves into the `W/lips_range` files that `lipsync` reads | Step 4B |
| `mksrt.py` | makes an SRT from the audio/video when the episode has none (Whisper + script cleaning) | Step 0B |
| `srtclean.py` | the SRT-cleaning platform's code (word-by-word cleaning against the script), used by mksrt.py | Step 0B |
| `mkdec.py` | writes `W/decisions.json` from a short checked spec (`G/A/C/GAP/U/R`) | Step 2 |
| `merged.py` | one reading view of the dub rows with their source lines in order | Step 2 |
| `mac_runner.command` | runs job scripts natively on a Mac for an agent that sits in a Linux VM | Step 4B.4 |

**3. Fill the show profile** (next section) once per show, then run Steps 0-10 for each episode. The
**episode run sheet** below lists every command in order with the variables to set.

## Show profile: set once per show (this is what makes the skill work for any show)

Nothing in the tools is tied to one show. Everything that differs is a setting below. For a **new show**, fill
the right-hand column (ask the owner what you cannot see in the files) and keep it with the show's files.

| Setting | TDMB (Italian dub vs US) | TOLR (Italian dub vs German) | WBM (Italian dub vs US, no SRTs) | New show: how to decide |
|---|---|---|---|---|
| Dub / source language (`words --lang`) | `it` / `en` | `it` / `de` | `it` / `en` | the languages of the two SRTs |
| `--labels DUB,SRC` | `IT,US` | `IT,DE` | `IT,US` | short codes of the two languages |
| Episode pairing | same number | same number | same number | check after `analyze`: "DP matched" 70-90%; under 50% = wrong pairing (one show: German N = US N+2) |
| Burned-in captions in the source video | yes (`render --subs keep`, strip covers them) | no (use the "NO SUB"/"clean" file) | no (`render --subs keep`) | look at a frame; ask for a clean source if possible |
| Lip scan + lip-sync rows (Step 4B) | no | **yes** | no | yes when the source shows talking faces whose lips should match the dub voice |
| Groups (Step 4A) | `groups` (size 8, mode 2) | `groups --size 7` (mode 1, after 4B) | `groups --size 8 --max-drift 500` (mode 2) | 7 with lip scan, 8 without |
| Transitions at clip switches (Step 7B) | no | **yes** (light leak / blur dissolve / zoom blur) | no | the owner's choice; default yes for lip-sync shows |
| Subtitle style (Steps 8-9) | `strip` (white strip over the US captions) | `box` (30% black rounded box) | `box` | `box`, unless burned-in captions must be covered. The box layer is drawn for a 1080-px frame (TOLR is 1080×1080); on another size it is centred at the bottom, so for frames under 1080 px wide make a short `--preview 40` test first |
| Output folder | `TDMB US-IT/output/ep<N>/` | `TOLR DE_ITaly/output/ep<N>/` | `WBM US-IT/outputs/ep<N>/` | `<show folder>/output/ep<N>/` |
| File names | `TDMB_EP<N>_IT_final_clean.mp4`, `..._it_subs.mp4`, `..._en_subs.mp4`, `TDMB_EP<N>_IT_english_google.srt`, `ep<N>_it_us_mapping.xlsx`, `ep<N>_dynamic_groups_review.xlsx` | `TOLR_EP<N>_IT_...` (same pattern), `tolr_ep<N>_it_de_mapping.xlsx`, `tolr_ep<N>_dynamic_groups_review.xlsx`, `tolr_ep<N>_lipsync_review.xlsx` | `WBM_EP<N>_IT_final_clean.mp4`, `..._it_subs.mp4`, `..._en_subs.mp4`, `WBM_EP<N>_IT_english_google.srt`, `wbm_ep<N>_it_us_mapping.xlsx`, `wbm_ep<N>_dynamic_groups_review.xlsx` | `<SHOW>_EP<N>_<DUB>_...`, `<show>_ep<N>_<dub>_<src>_mapping.xlsx` |
| Translation for the 3rd video | Italian → English (Google) | Italian → English (Google) | Italian → English (Google; when blocked, Step 9) | dub language → English |
| Sentence mode (Steps 1A/1B `--clauses`) | `0` (whole sentences, as validated) | `0` (whole sentences; ep24 test with `3`: 73% of screen time identical, 25% shifted under 1 s, 2% other footage, 44 → 54 clip switches, 20 German sentences split into lip + no-lip parts, 1 lip sentence lost; kept at 0 unless the owner prefers the clause cut) | **`3`** (clauses, since ep17 v2) | `3` for shows without the lip scan; test before using it with Step 4B |
| Source ending after the last line (Step 7) | kept | kept | **none** (`bookends --no-ending`: QC flagged 3 s of picture after the voice) | ask the owner; default kept |
| SRTs | given | given | **none**: Step 0B (`mksrt.py`) for both sides; Italian script `.docx` in `italy/`, no English script | Step 0B when missing |

Input file names change from show to show and sometimes inside a show (see the owner's setups in Step 0);
always list the input folders and pick the four files of the episode by number: dub SRT, dub audio
(.wav/.mp3), source SRT, source video (+ the audio the source SRT was timed to, if there is one).

## Episode run sheet: every command, in order

Set the variables once per episode, then run the commands top to bottom (bash shown; on Windows use the same
commands in PowerShell with `$env:` variables or write the paths out). `T=adaptation-tool/scripts`.
`words`, `render`, `transitions`, `subs`, `lipgpu.py` and `lipmesh.py` take `--max-seconds` and resume: on a machine
that limits a command's run time, re-run the same command until it reports it is finished.

```bash
N=26; SHOW=TOLR; DL=it; SL=de; LABELS=IT,DE; STYLE=box          # from the show profile
LIP=1; GSIZE=7; TRANS=1                                          # TDMB: LIP=0; GSIZE=8; TRANS=0; STYLE=strip
CLAUSES=0; DRIFT=4000; ENDING=""                                # WBM: CLAUSES=3; DRIFT=500; ENDING=--no-ending (LIP=0, GSIZE=8, TRANS=0)
DUB_SRT="..."; DUB_AUDIO="..."; SRC_SRT="..."; SRC_VIDEO="..."     # the episode's four inputs
DUB_SCRIPT="..."; SRC_SCRIPT="..."; SUB_SRT="$DUB_SRT"            # scripts: only for Step 0B; SUB_SRT = SRT to burn in
W=work/ep$N; OUT="<show folder>/output/ep$N"; T=adaptation-tool/scripts; M=adaptation-tool/data/models
show=$(echo $SHOW | tr A-Z a-z); D=${LABELS%%,*}; dl=$(echo $D | tr A-Z a-z); sl=$(echo ${LABELS##*,} | tr A-Z a-z)
MAP="$OUT/${show}_ep${N}_${dl}_${sl}_mapping.xlsx"                # TDMB keeps its old name: MAP="$OUT/ep${N}_it_us_mapping.xlsx"
P="$OUT/${SHOW}_EP${N}_${D}"                                     # prefix of the videos and the English SRT
mkdir -p "$W" "$OUT"

# Step 0B (only when the SRTs are missing): make them from audio/video + script, then use
#   DUB_SRT / SRC_SRT = the *_sentences.srt files (matching)  and  SUB_SRT = the dub subtitle SRT (Steps 8-9)
# python3 $T/mksrt.py --media "$DUB_AUDIO" --lang $DL --script "$DUB_SCRIPT" --work $W --out $W/${SHOW}_EP${N}_${D}.srt
# python3 $T/mksrt.py --media "$SRC_VIDEO" --lang $SL --script "$SRC_SCRIPT" --work $W --out $W/${SHOW}_EP${N}_${SL}.srt
# DUB_SRT=$W/${SHOW}_EP${N}_${D}_sentences.srt; SRC_SRT=$W/${SHOW}_EP${N}_${SL}_sentences.srt; SUB_SRT=$W/${SHOW}_EP${N}_${D}.srt

# Step 1A + 1B: sentence times from the audio, then the automatic alignment
python3 $T/dubstitch.py words --work $W --side src --srt "$SRC_SRT" --media "$SRC_VIDEO" --lang $SL --models $M --clauses $CLAUSES
python3 $T/dubstitch.py words --work $W --side dub --srt "$DUB_SRT" --media "$DUB_AUDIO" --lang $DL --models $M --clauses $CLAUSES
python3 $T/dubstitch.py analyze --work $W --dub-srt "$DUB_SRT" --dub-audio "$DUB_AUDIO" --src-srt "$SRC_SRT" --src-video "$SRC_VIDEO" --models $M --clauses $CLAUSES
python3 $T/merged.py $W 95 > $W/merged.txt                         # reading view for Step 2

# Step 2: READ (by meaning) -> write W/spec.py -> decisions.json    (the only step that is not a command)
python3 $T/mkdec.py $W $W/spec.py

# Steps 3-4: sheet (integrity check) and edit list
python3 $T/dubstitch.py sheet --work $W --out "$MAP" --labels $LABELS    # the review workbooks are named after it
python3 $T/dubstitch.py manifest --work $W

# Step 4B (lip-sync shows only): lip scan, then lip-sync rows
if [ $LIP = 1 ]; then
  python3 $T/lipgpu.py  --video "$SRC_VIDEO" --out-dir $W/lips_nets           # GPU half; see 4B.2-4B.4 for devices and limits
  python3 $T/lipmesh.py --video "$SRC_VIDEO" --out-dir $W/lips_mesh           # Face Mesh half (Linux if possible; may run at the same time)
  python3 $T/lipmerge.py --nets $W/lips_nets --mesh $W/lips_mesh --out $W/lips_range
  python3 $T/dubstitch.py lipsync --work $W
fi

# Step 4A: groups (lip-sync show: size 7 = mode 1; other shows: size 8 = mode 2)
python3 $T/dubstitch.py groups --work $W --size $GSIZE --max-drift $DRIFT

# Steps 5-7: render, verify, clean master (with Step 7B transitions on shows that use them)
python3 $T/dubstitch.py render --work $W --subs keep               # --subs keep|crop from the show profile
python3 $T/dubstitch.py verify --work $W                           # then LOOK at W/qc_sheet.jpg
if [ $TRANS = 1 ]; then
  python3 $T/dubstitch.py bookends --work $W --out $W/clean_noglow.mp4
  python3 $T/dubstitch.py transitions --work $W --out "${P}_final_clean.mp4"
else
  python3 $T/dubstitch.py bookends --work $W --out "${P}_final_clean.mp4" $ENDING
fi

# Steps 8-10: subtitles (dub language), English translation + subtitles, final check
python3 $T/dubstitch.py subs --work $W --style $STYLE --srt "$SUB_SRT" --out "${P}_final_${dl}_subs.mp4"
python3 $T/dubstitch.py translate --work $W --srt "$SUB_SRT" --sl $DL --tl en --out "${P}_english_google.srt"
python3 $T/dubstitch.py subs --work $W --style $STYLE --srt "${P}_english_google.srt" --out "${P}_final_en_subs.mp4"
python3 $T/dubstitch.py check --work $W                            # must print FINAL CHECK PASSED
```

Keep a short `run_notes.md` in the output folder: inputs used, decisions worth knowing, the Step 11 numbers,
anything unusual (an offset, a black screen, a repeated passage) and the run times.

## Mandatory episode deliverables

Every episode run must produce and verify all of the following in that show's `output`
folder. A run is incomplete if any item is missing:

1. A **clean final video** (`TDMB_EP<N>_IT_final_clean.mp4`): the dynamic-group picture, the
   source opening/ending and the dub audio, with no burned subtitles.
2. A **target-language subtitle video** (`TDMB_EP<N>_IT_final_it_subs.mp4`): a copy of the clean video
   with the dub SRT (Italian) burned in.
3. A **translated subtitle video** (`TDMB_EP<N>_IT_final_en_subs.mp4`): a copy of the clean video with
   the Google Italian-to-English translation of the dub SRT burned in, in the same style.
4. The mapping workbook (`ep<N>_it_us_mapping.xlsx`), the dynamic-group review workbook
   (`ep<N>_dynamic_groups_review.xlsx`) and the translated SRT (`TDMB_EP<N>_IT_english_google.srt`).

Exactly these three videos per episode; no other subtitled versions are kept. **The subtitle style
depends on the show (Step 8):** TDMB (also called DMB) keeps the white full-width strip; **every other
show** (TOLR and any new show) uses the transparent-box style (`subs --style box`).

All three videos must contain the completed picture and the dub audio, have the same length,
and pass `check` (Step 10). The dynamic groups (Step 4A) and the Google translation
(Step 9) are mandatory for every episode; they are commands of the tool, not hand-made steps.
For the TOLR show (German source) the lip scan and lip-sync rows (Step 4B) and the transitions
(Step 7B) are mandatory too; the clean master then already contains the transitions.

What is in them:

1. **Mapping sheet (.xlsx):** Mapping (every dub line, its source line(s), timings, score, speed
   factor, notes; TOLR: a last column "Type after video"), Unused source lines (with the reason),
   Video edit list (row-level timings plus the opening/ending rows from Step 7), and for TOLR a
   Transitions tab (Step 7B).
2. **Dynamic-group review (.xlsx)**: one row per group and one row per Italian line.
3. **TOLR only: lip-sync review (.xlsx)** (Step 4B).
4. **Final videos (.mp4)**: the source footage played per group, fitted to the dub's timing, with
   no black screen; the real dub audio is the only track; the source's own opening and ending put
   back at normal speed with their own sound (Step 7); the clean master has no burned subtitles;
   the Italian and English versions burn their SRT in the show's subtitle style (Steps 8-9).

Everything runs through one Python tool, `dubstitch.py`, plus the box-subtitle helper `pngsubs.py` and, on
lip-sync shows, the lip-scan tools `lipgpu.py` / `lipmesh.py` / `lipmerge.py` (built on `lipdetect.py` and
`lipfast.py`). All their code is in the Appendix. Run all the steps, always in this order:

```
1A words (source + dub) -> 1B analyze -> 2 READ + write decisions.json -> 3 sheet -> 4 manifest (incl. gap fill)
  -> [TOLR: 4B lip scan + lipsync] -> 4A groups -> 5 render -> 6 verify -> 7 bookends (clean master)
  -> [TOLR: 7B transitions] -> 8 subs (Italian) -> 9 translate + subs (English) -> 10 check
     (Steps 8-9 style: TDMB/DMB = white strip `subs`; every other show = `subs --style box`)
```

All steps except Step 2 are commands. **Step 2 is reading that only a person or an LLM agent
can do: deciding matches by meaning.** It is mandatory. The tool refuses to build the sheet
until every flagged row has a decision.

**Validated end to end** on real episodes:
- TDMB (Italian dub vs US source): the tool reproduced the hand-checked reference outputs of
  ep81/ep82 exactly; ep81, 82, 84-87 and 101-106 were made with it (every row passed sync,
  subtitle self-check 0/0/0); a fresh agent given only this file produced ep81 without errors;
  ep82 ran the full flow (Steps 4A-10): 21 groups, 0 footage played twice, `check` passed.
- TOLR ep24 (Italian dub vs **German** source): Step 1A gave every German and Italian sentence its
  time from the audio (German: 1494 of 1564 SRT words heard exactly as written, 61 spelled
  differently, 9 filled in); "Schweinebraten." is spoken at 20.98-22.06 s, exactly when the lips
  move (the old card estimate said 19.66-20.90 s).
- TOLR ep24 final (reference for every TOLR episode, `output/ep24/final/`) ran this exact flow:
  Whisper sentences → analyser (76 of 174 German sentences speaking) → 179/179 matched →
  `groups --size 7` (18 exact-span groups, 12 lip groups, 4 single lip rows, 13 too-fast lip
  sentences trimmed to 1.5×) → 250/250 segments passed `verify` → 46 glow transitions →
  subtitle self-checks 0/0/0 → `check` passed. Its first 2 minutes are frame-identical to the
  test the owner approved.
- Transitions after the owner's review (24 previews on one ep24 switch): light leak, blur dissolve
  and zoom blur in random order, never the same twice in a row; the 3-minute ep24 test had 15 switches
  (6 light leak, 6 blur dissolve, 3 zoom blur), subtitle self-checks 0/0/0.
- Lip scan on the GPU (this version, Step 4B): ep24 0 of 174 and ep25 1 of 161 speaking sentences differ from the
  CPU reference; ep26 was run end to end with it (lipgpu on the Mac GPU + lipmesh/lipmerge on Linux, 230/230 rows,
  295/295 segments passed `verify`, 29 transitions, FINAL CHECK PASSED).
- TOLR ep41-45 ran this full flow (each `check` passed). Box-style subtitles (Step 8) were approved on
  ep42 and made the TOLR standard; ep43 was re-subtitled with `subs --style box` and passed `check`.

## Step 0. Before you start

**Install** (once): `pip install sentence-transformers numpy openpyxl pillow faster-whisper python-docx`, plus
ffmpeg and ffprobe. A CPU-only machine can first run
`pip install torch --index-url https://download.pytorch.org/whl/cpu`.
- Whisper "small" (~480 MB) downloads into `<models>/whisper` on the first `words` run; the
  embedding model (~470 MB) on the first `analyze`.
- **Lip tools (Step 4B) and transitions (Step 7B):** `pip install torch torchvision opencv-python scikit-learn
  python_speech_features scenedetect gdown psutil scipy "numpy<2" "mediapipe==0.10.14"` plus TalkNet-ASD:
  `git clone https://github.com/TaoRuijie/TalkNet-ASD adaptation-tool/third_party/TalkNet-ASD`, then inside it
  `gdown 1AbN9fCf9IexMxEKXLQY2KYBlb-IhSEea -O pretrain_TalkSet.model` and
  `gdown 1KafnHz7ccT-3IyddBsL5yi2xGtxAKypt -O model/faceDetector/s3fd/sfd_face.pth`
  (if Google Drive refuses gdown, open the two links in a browser and save the files under those names).
  - mediapipe 0.10.14 needs **Python 3.12 or older** (make a 3.12 venv, e.g. `uv venv --python 3.12`);
    mediapipe 1.x needs libEGL, which headless machines lack.
  - **Mac (Apple silicon):** the normal torch wheel has the Apple GPU (`torch.backends.mps.is_available()` → True).
  - **Windows/Linux with an NVIDIA GPU:** the CUDA build of torch (`--index-url https://download.pytorch.org/whl/cu121`).
  - **CPU only:** `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`.
  - ffmpeg (and ideally ffprobe) on the PATH; `imageio-ffmpeg` can provide ffmpeg when nothing else can be installed.
- **Font for Steps 8-9: Poppins Bold** (both styles). Found automatically if installed; otherwise download
  `https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf` and pass `--font`.
  The box style needs `pngsubs.py` next to `dubstitch.py`.

**The tools** are unpacked from the Appendix into `adaptation-tool/scripts/` (see "Start here"); Step 14 lists them.

**Confirm the two inputs are the same episode.** On one show (German "MVS") German episode N is US
episode N+2; on TDMB the numbers are the same. After `analyze`, the "DP matched X/Y" line is
normally 70-90%; under 50% means the wrong pairing: stop and ask.

**Check that each subtitle file is timed to the media it is paired with.** `analyze` does this
itself and stops if it is not. A source SRT is sometimes timed to a different cut (a `.wav` with
extra lead-in); nothing later catches this. TDMB ep84's US SRT was 12.833 s late; offsets on other
episodes were 0-4 s, so it is checked every episode. Keep the audio the SRT was timed to next to it
**with the same file name**, and `analyze` corrects the times automatically.

**Settle per show** (ask if you don't know): `--subs keep|crop` (burned-in captions), output names
and folder, `--labels DUB,SRC`, and the subtitle style (TDMB/DMB = strip, any other show = box).

**Owner's current setup (TDMB show, Italian dub vs US):**
- Inputs: Italian `TDMB US-IT/italy/Episode <N> - <title>.srt/.wav` (ep105 is `Episode 105- <title>`;
  ignore the `.docx` scripts); US video `TDMB US-IT/US/TDMB EPISODE <N>.mp4` (from ep101
  `TDMB_EPISODE_<N>.mp4`); US subtitles `TDMB US-IT/US/TDMB EPISODE <N>.srt` (older, timed to the mp4)
  or, ep83 onward, `TDMB US-IT/US/Ep <N> - <title>.srt` plus a same-name `.wav` (timed to that wav;
  `analyze` corrects it).
- Outputs in `TDMB US-IT/output/ep<N>/`: `ep<N>_it_us_mapping.xlsx`, `ep<N>_dynamic_groups_review.xlsx`,
  `TDMB_EP<N>_IT_english_google.srt`, `TDMB_EP<N>_IT_final_clean.mp4`, `_final_it_subs.mp4`,
  `_final_en_subs.mp4`. The render without subtitles stays in the work folder.
- Settings: `render --subs keep`, `--labels IT,US`, `groups` with the default `--size 8`. Subtitle
  style: **white full-width strip** over the English captions, Poppins Bold 40, black (`subs` default,
  `--style strip`). TDMB/DMB is the only show that uses the strip.
- Model cache `adaptation-tool/data/models`; one work folder per episode, outside the deliverables.

**Owner's second show (TOLR, Italian dub vs German source):**
- Inputs: `TOLR DE_ITaly/italy/SRT Clean/Episode <N> - <title>.srt`, `italy/Audio/Episode <N> -
  <title>.mp3`, `DE/srt/Copy of TOLR_DE - <N>.srt`, `DE/NO SUB TOLR DE EP <N>.mp4` (no burned
  captions, 1080×1080, 29.97 fps). German names differ (Ethan = Lorenzo, Emily = Emma).
- Outputs in `TOLR DE_ITaly/output/ep<N>/`, `--labels IT,DE`, file names `TOLR_EP<N>_IT_...` and
  `tolr_ep<N>_it_de_mapping.xlsx`. Words: `--lang de` for the source, `--lang it` for the dub.
- Subtitles: **box style** (`subs --style box`, Step 8), never the white strip.
- Tools: TalkNet-ASD in `adaptation-tool/third_party/TalkNet-ASD`; `pngsubs.py` and the lip tools (`lipgpu.py`,
  `lipmesh.py`, `lipmerge.py`, `lipfast.py`, `lipdetect.py`) next to `dubstitch.py` in `adaptation-tool/scripts/`;
  the Mac runner in `adaptation-tool/mac_lipscan/` (Step 4B.4).
- Flow (owner's rule since ep24 v3, every TOLR episode): Step 1A `words` on both sides → 1B-4 as usual →
  Step 4B (scan the whole German video; the video decides who speaks) → `groups --size 7` (Step 4A mode 1:
  exact-span groups of at most 7 rows, lip groups, too-fast lip sentences trimmed to 1.5×) → render → verify →
  `bookends --out W/clean_noglow.mp4` → `transitions --out OUT/TOLR_EP<N>_IT_final_clean.mp4` (Step 7B) →
  `subs --style box` (Italian) → `translate` + `subs --style box --srt ...english_google.srt` → check.
  The ep24 reference run is in `output/ep24/final/` with `run_notes.md`.
- Some TOLR source videos have the German subtitles burned in (ep41 `41 de.mp4`); the box style does not hide
  them, so ask the owner for the "clean"/"NO SUB" German video before running such an episode.

**Machines that cap each command's run time:** `words`, `render`, `transitions`, `subs`, `lipdetect.py`,
`lipgpu.py` and `lipmesh.py` take `--max-seconds` and resume; re-run the same command until it finishes. `--workers` sets parallel
jobs. `subs` caps itself at 2 workers on frames of about 1920×1080 or more (4 GB machines ran out
of memory with 3+).

## Step 0B. No SRT? Make both SRTs from the audio/video + script (`mksrt.py`), then continue with Step 1A

Use this when an episode comes without SRTs. Each side needs its **audio/video + script** (the owner always gives
the script, whatever the language: Italian, German, French, English...).

```bash
python3 mksrt.py --media DUB.mp3 --lang it --script "Episode N - Title.docx" --work W --out W/<SHOW>_EP<N>_IT.srt
python3 mksrt.py --media SRC.mp4 --lang de --script "<source script>.docx"  --work W --out W/<SHOW>_EP<N>_DE.srt
```

Each run writes, from the same cleaned and timed words:
- **`<...>.srt` — the subtitle SRT** (Steps 8-9: burned in and translated): at most **6 words and 4 s per row**
  (`--row-words`, `--row-seconds`). Each sentence gets the fewest rows that fit, with its words shared evenly
  (8 words → 4 + 4, not 6 + 2); a row ends at a sentence end and at a 1-s pause; a 1-2-word row joins a
  neighbour when that still fits. A row stays up 0.5 s after its last word (never past the next row, never > 4 s).
- **`<...>_sentences.srt` — the sentence SRT**: one row per sentence. This is the SRT for the sentence matching
  (Steps 1A-4: `words`, `analyze`, `sheet`, ...).
- `<...>_raw.srt` (as Whisper heard it) and `<...>_report.json` (the cleaning report).

What it does, in order:
1. **Whisper** (faster-whisper "small", CPU, 60-s pieces, resumable) hears every word with its time. The cache is
   `W/asr`, the same one `words` uses, so Step 1A does not run Whisper again.
2. **Invented credit lines are removed** (Whisper writes "Sottotitoli e revisione ... a cura di", "Untertitel im
   Auftrag des ZDF" in silence; TOLR ep24 had one at the end).
3. **Second listen:** where the script has 2+ words Whisper did not hear and the audio has room for them, that
   stretch is heard again with the script text as a hint (ep24: "«Emma Reali.» Scandì il suo nome." came back).
4. **Cleaning:** the SRT-cleaning platform's method, unchanged (`srtclean.py`, `--sensitivity aggressive`), run on a
   one-word-per-row SRT so every cleaned word keeps its own time: the script decides spelling, names, punctuation
   and quotes ("A noi." → "Annuì.", "Yako può" → "Jacopo", "Tomaso" → "Tommaso").
5. **Script wins on small leftovers** (`--keep-heard` turns it off): where the voice and the script still differ by
   1-3 words (a stray "a"/"la"/"che" heard, or "il", "testa", "«Ah," missed), the script's words are used. Every such
   change is printed (5-9 per episode) — look at them: they are the only places the voice and the script disagreed.
6. The cleaned words are grouped into the two SRTs above.
7. Then: Step 1A `words` and `analyze` with the **`_sentences.srt`** files; `subs` and `translate` with `--srt` set to
   the **subtitle SRT** (`<...>_IT.srt`).

No script for a side (should not happen): leave `--script` out and pass `--names CHARACTERS.csv` (fixes names that
sound like a character, e.g. Stirling → Sterling); the spelling is then only as good as Whisper (German 5.5-6.4%
of words wrong with "small", 1.9-3.0% with `--engine mlx --model mlx-community/whisper-large-v3-turbo` on a Mac GPU;
that engine's word times are less exact, so not for a subtitle SRT).

**Tested (TOLR ep24, ep25, ep26) against the official Italian SRTs, word by word (spelling, capitals, punctuation):**

| | ep24 | ep25 | ep26 |
|---|---|---|---|
| Whisper only: words different | 10.0% | 8.8% | 7.5% |
| **After cleaning + script wins: words different** | **1 of 1309** | **1 of 1384** | **0 of 1822** |
| Subtitle rows (words per row: 4-6 / 3 / 1-2) | 279 (244 / 30 / 5) | 293 (260 / 29 / 4) | 398 (339 / 48 / 11) |
| Row starts vs official cards, median (90% within) | 0.07 s (0.14 s) | 0.07 s (0.13 s) | 0.08 s (0.12 s) |
| Sentence matching with the made `_sentences.srt` (both sides): auto-match agrees with the final mapping | 93.0% (official SRTs 91.7%) | 86.6% (84.0%) | 89.7% (89.7%) |

The two words left: ep24 "«Ah ah!»" is in the script but not in the official SRT; ep25 the official SRT keeps a
spoken "E" that the script does not have. (Stray spaces before punctuation in the official SRTs, e.g.
"immaginato .", are not counted.) The German side of this test had no script (made with Whisper + names).

## Step 1A. words: exact sentence times from the audio (run before analyze, on both sides)

```bash
python3 dubstitch.py words --work W --side src --srt SRC.srt --media SRC.mp4 --lang de [--models DIR] [--max-seconds 150]
python3 dubstitch.py words --work W --side dub --srt DUB.srt --media DUB.wav --lang it [--models DIR] [--max-seconds 150]
```

**Why.** Subtitle cards are cut by reading speed, not by sentence, so a card often holds parts of
several sentences and a sentence has no time of its own. Anything that needs the real moment a line
is spoken (lip movement, exact clips) is then off by up to a few seconds.

**What it does:**
1. The SRT cards are re-cut into whole sentences (a closing quote stays with its sentence).
2. Whisper (faster-whisper "small", CPU) hears every word with a start and end time, in 60-s pieces
   (cached in `W/asr/`, resumable).
3. Each heard word is trimmed to its strongest voiced block (Whisper glued a 1.3 s pause onto "Bring").
4. **The SRT decides the words, the audio decides the times:** SRT words are lined up with the heard
   words letter by letter; every SRT word keeps its spelling and takes the matching letters' time.
   Statuses: *same*, *written differently*, *not heard* (time filled in between neighbours).
5. **Second listen:** a sentence that comes back mostly *not heard* or impossibly short is heard again
   alone, with its text as a hint.
6. A sentence = start of its first word → end of its last word; neighbours never overlap.

**Clause mode (`--clauses N`, show profile; WBM: 3).** Without it a matching unit is a whole sentence. In
clause mode a sentence is also cut **after `,` `;` `:` and before a dash**, but only where both parts keep at
least N words ("Sfortuna per lui," stays whole if the rest is short). Give the same `--clauses N` to both `words`
runs and to `analyze`; it is saved in `W/sentence_mode.json` and every later step uses it. Use the normal
sentence SRTs (Step 0B `*_sentences.srt` or the given SRTs): the tool does the cutting, and each clause gets its own
time from the audio here. Why: on WBM ep17 one Italian sentence held a line of dialogue, an action and a thought
("Che hai da guardare umana? Viola sorrise beffarda, voleva giocare a fare il duro?"); matched as one unit, the
smile appeared on the wrong words and QC flagged 4 AV mismatches. With clauses: 115 → 150 Italian units and
105 → 151 US units; each QC spot then showed the right picture (Step 12).

**Output:** `W/sentences_<side>.json` (`--out FILE.xlsx` also writes a workbook; `--first N` = a test
of the first N sentences, not used by `analyze`). Check the summary line; a long run of *not heard*
words means Whisper lost that stretch. `analyze` uses these files automatically (it prints `using
word-timed sentences`); re-run `words` whenever an SRT or media changes.

## Step 1B. analyze

```bash
python3 dubstitch.py analyze --work W \
  --dub-srt DUB.srt --dub-audio DUB.wav --src-srt SRC.srt --src-video SRC.mp4 [--models DIR] \
  [--src-srt-audio AUDIO | --src-offset SECONDS]
```

**0. Subtitle timing check:** measures the source SRT's offset against an audio file with the SRT's
name (or `--src-srt-audio`), stops if the audio is a different edit, then checks both SRTs against the
speech (normal: within ±0.1 s; it stops beyond 1 s or if the last subtitle ends after the media).
Don't copy the lag blindly: find the right audio, or pass `--src-offset` after checking by ear.

Then, on both sides: sentences (from Step 1A, or estimated from the cards with overlap resolution
and the 0 ms rule: a 0 ms cue is dropped only if it is a single word, others are re-timed); script
markup (`°°°`, `*italic*`, `<i>`) stripped; a multilingual MiniLM similarity matrix; a monotonic DP
alignment (threshold 0.5); and a **Case A fallback** for unmatched rows (best source line in a near
1-to-1 window, score ≥ 0.5) — **wrong about half the time, so every one must be read.**

It writes `W/analysis.txt` (all rows, the "DP matched X/Y" line, duplicate check, rows you must read
with neighbours, DP matches worth a second look, unused source lines, possible repeated passages,
source timeline), a `W/decisions.json` template and `W/prep.json`. **Read all of it.** Re-running
`analyze` means re-running every later step.

## Step 2. The manual read (mandatory; judge by MEANING, never by score)

### 2.1 What must be read
1. Every unmatched row and every Case A row (`sheet` refuses to run until each has a decision).
2. Every line in the duplicate check: keep the claim that reads right, re-decide the other row.
3. Every "DP match worth a second look" (ep81 rows 70/71 at 2.25× and 0.37× really covered one line
   plus half the next: a group over both lines).
4. Every possible repeated passage and every unused source line.
5. The neighbours of every row you read, and their matches.

### 2.2 How to read one row (the n-1/n+1 method)
1. Look at the neighbours (n-1, n+1) and the source sentence each is matched to.
2. Ask whether that sentence also carries this row's meaning (a clause, a paraphrase, the same beat):
   `"Si domandò."` = the "…she wondered." tail; `"Muoviti."` = the "…quickly" of "We must follow
   after Edgar quickly."; `"Poteva incassare il colpo."` = "…but she could handle it."
3. If neither neighbour covers it, go to n±2; a pair can grow into a 3- or 4-row group.
4. Check nearby unused source lines (`"Viola si fece avanti."` = "Heather made her presence known.").
5. Scores mislead both ways (true matches as low as 0.11, wrong ones up to 0.65). Only meaning counts.
6. Declare a genuine gap only after all of this finds nothing.

### 2.3 Which decision to write
1. **Row(s) = clauses of one source sentence → `groups`:** list all members in dub order, one source
   line or a span; the footage is split by dub duration. Prefer one group per sentence.
2. **One row = several consecutive source lines → `assign` with a span `[a, b]`.**
3. **A clear match elsewhere → `assign`** (also overrides a wrong DP/Case A match).
4. **A Case A match you agree with → `confirm`** with the reason (not if it is in the duplicate check).
5. **The dub repeats a passage → `repeat` (`{later_row: earlier_row}`)**: whole run; optionally confirm
   with `listen`; tell the person (TDMB ep82 IT#28-32 = IT#23-27, kept).
6. **Nothing fits → `gap`** with the reason.
7. **An unused source line → `unused_notes`.**
8. **A row that would play very slow → group it with a real neighbour** over both source lines.
Rules: a row in only one decision; copy the exact starting text into every `*_prefix` (checked by the
tool); edit `decisions.json` and re-run from `sheet` to change a decision.

### 2.4 `decisions.json` format

```json
{
 "groups": [
  {"members": [99, 100], "dub_prefixes": ["Dobbiamo seguire Federico", "Muoviti"],
   "src": 90, "src_prefix": "We must follow after Edgar quickly",
   "reason": "'We must follow after Edgar quickly.' -- 'Muoviti.' (move it) is the 'quickly'."},
  {"members": [70, 71], "dub_prefixes": ["Conosco il tuo carattere", "Ma non ti permetterò"],
   "src": [64, 65], "src_prefix": "I know how you are",
   "reason": "US#64 + US#65 together; IT#70 covers US#64 and the first half of US#65."}
 ],
 "assign": {
  "21": {"dub_prefix": "Viola si fece avanti", "src": 20, "src_prefix": "Heather made her presence known",
         "reason": "same beat; the line was unused and scored only 0.37 because the words differ"},
  "35": {"dub_prefix": "Non poteva evitarla", "src": [27, 28], "src_prefix": "He could not avoid",
         "reason": "exactly both lines"}
 },
 "confirm": {"57": "(illustrative) Case A line is the same statement, and no other row uses it"},
 "repeat": {"28": 23, "29": 24, "30": 25, "31": 26, "32": 27},
 "gap": {},
 "unused_notes": {"16": "Edgar's echo 'Woman?' -- the Italian has no line for it."}
}
```

### 2.5 Two helpers for the read: `merged.py` and `mkdec.py`

`python3 merged.py W 95 > W/merged.txt` prints the dub rows (`D<n>`) with their automatic source match, and the
source lines (`S<n>`) in order between them, cut to 95 characters: the n-1/n+1 read in one view.

Write the decisions as a short **spec** (`W/spec.py`); `python3 mkdec.py W W/spec.py` turns it into
`W/decisions.json`. Every index is checked against a word of its own text, so a wrong number stops at once:

```python
# W/spec.py -- (row, keyword) pairs; the keyword must appear in that row's text
G([(99, "Dobbiamo seguire"), (100, "Muoviti")], 90, "follow after Edgar", "'Muoviti' is the 'quickly'")  # group (src may be [a, b])
A(21, "Viola si fece", 20, "presence known", "same beat; scored 0.37 only because the words differ")    # assign (src may be [a, b])
C(57, "Case A line is the same statement and no other row uses it")                                    # confirm a Case A match
GAP(140, "Ehm", "no source line says this")                                                             # genuine gap
U(16, "Woman", "Edgar's echo; the dub has no line for it")                                              # note on an unused source line
R(28, 23)                                                                                               # dub row 28 repeats row 23
```
Keep `spec.py` in the work folder: re-running from `sheet` after a change is then one command.

### 2.6 Reading in clause mode (and fixing QC notes about the picture)
1. Many clauses of one source sentence: a **group** over that sentence (or span) as usual.
2. **A narration clause next to a spoken line must not show a talking mouth.** If the matched footage shows the
   speaker talking, assign the narration clause to a still shot of the same scene (WBM ep17: "piantandogli gli
   occhi in faccia" → US "She sassed back." = Viola staring, mouth still, even though it comes after the next
   line in the US order). Out-of-order assigns are allowed; `groups` breaks the block there.
3. **An action clause should land on its action** ("Viola sorrise beffarda" → the US shot where she smiles).
   Pull a frame grid of the candidate US seconds (`ffmpeg ... -vf fps=8/<dur>,tile=4x2`) and look before deciding.
4. **A line with no source footage** ("Ma a Demetrio non fregava nulla"): group it with its neighbour over a span
   that has a fitting non-talking picture (ep17: x-ray + Grant in pain), never onto someone's talking shot.
5. Rows that would play below ~0.5× or above ~1.8×: group them with a neighbour over both source lines.
6. Last resort, one clip: after `groups`, set a segment's `us_start_ms`/`us_end_ms` in `W/manifest_groups.json`
   inside its own source line (note why in its `note`), then re-run `render` → `verify` → `bookends` → `subs`
   (ep17 IT#19 starts at 60.0 s so the smile plays on "Viola sorrise"). `verify` must still pass.

## Step 3. sheet

```bash
python3 dubstitch.py sheet --work W --out OUT/epN_it_us_mapping.xlsx --labels IT,US
```

Applies the decisions, checks every prefix, runs the **integrity check** (no 0 ms window; no two rows
share footage except documented repeats) and writes the sheet. A failure is almost always a group
missing a member, a group on the wrong sentence, or a duplicate claim confirmed instead of re-decided.
Row colours: green = decided by hand, red = genuine gap.

## Step 4. manifest (the edit list)

```bash
python3 dubstitch.py manifest --work W            # gap fill on by default (--fill-gaps 1000)
```

Lays the clips on the dub timeline; absorbs every silence (≤1.5 s to the previous clip, longer split
50/50) so there is no black screen; bleed fix for clips slower than 1.6× (stays inside its own shot,
never into a neighbour's footage); gap fill (clips that follow each other in the source with ≤1 s
unused between them are joined, split at a scene cut if there is one); no first-clip rule (the source
opening comes back in Step 7); final checks; a Video edit list tab. It prints the slow (>1.6×) and fast
(>2×) clips; "NEEDS VISUAL REVIEW" means look at it on the QC sheet in Step 6.

## Step 4A. groups: continuous source footage for blocks of Italian lines (mandatory)

```bash
python3 dubstitch.py groups --work W                 # TDMB (no lip-sync rows): blocks of up to 8 rows
python3 dubstitch.py groups --work W --size 7        # TOLR (after Step 4B): blocks of up to 7 rows + lip groups
```

Each block of consecutive Italian lines plays **one continuous source span**, fitted to the block's
Italian time. After it, `render`, `verify`, `bookends` and `transitions` use the group edit list. Re-running
`manifest` switches back to the row-level list, so always run `groups` after `manifest` (and after
`lipsync`, when Step 4B is used).

**Places a group never spans:** a dub repeat; the Italian going back in the source order; a source jump
longer than `--max-gap` (2 s); source footage another row uses; a lip-sync row. Blocks are balanced
(9 rows → 5 + 4). A group whose picture would drift more than `--max-drift` (4 s) from the voice is
split. No footage ever plays twice and nothing skips backwards.

### Mode 1 — with lip-sync rows (TOLR, owner's rule since ep24 v3)

When `W/lipsync.json` exists (Step 4B):

1. **Grouped (no-lip) rows: the exact span.** A group plays **from its first row's source start
   to its last row's source end** (the Whisper times of Step 1A) as ONE clip, at one speed, fitted
   to the group's Italian time. Nothing is cut out of the middle and no other footage is borrowed.
   Example, TOLR ep24: IT#0-3 = DE#0-2 = 1.09-19.20 s of German over Italian 0.00-16.15 s (1.12× fast).
2. **Single lip-sync row.** Plays its own German sentence (exact Whisper time) over its own Italian
   voice; the silence next to it goes to the neighbouring group.
3. **Lip groups.** Lip-sync rows that follow each other in the source (a phone call, a dialogue),
   with at most `--lip-group-narr` 2 no-lip rows between them, play as **one continuous piece**:
   every lip sentence plays exactly over its own Italian voice (`--lip-anchor both`); the footage
   between two lip sentences (the German pause plus the no-lip rows' sentences) fills the Italian time
   between them at one speed — no jump, so no transition; two lip sentences back to back meet halfway
   in the pause; a link is only made when that in-between footage plays between 1/2× and 2×
   (`--lip-group-speed`). ep24: the phone call IT#16-32 (11 lip rows + 4 narration rows) is one piece.
4. **Too-fast lip sentences are trimmed** (`--lip-trim-above 1.6`, `--lip-max-speed 1.5`). ep24 IT#19
   "Questo non è da te." = 0.87 s of Italian for a 3.10 s German sentence would play 3.56×. A lip
   sentence faster than 1.6× is cut down to the part around its lip movement, sized to play at 1.5×
   (here 1.3 s, 87.50-88.81 s, where the lips start). The skipped part is a clip switch and gets a
   transition (Step 7B). The owner compared 1.0 s (1.15×) and 1.3 s (1.5×) and chose 1.3 s.
   `--lip-max-speed 0` turns trimming off.
5. **Speeds to check.** No-lip groups are never trimmed, so a group whose Italian is much shorter or
   longer than its German plays fast or slow (ep24: up to 1.57× fast; IT#177-178 at the very end
   2.9× slow); they are flagged yellow in the review.

### Mode 2 — without lip-sync rows (TDMB, WBM)

With clause mode (WBM) use `--max-drift 500`: a block is split when a clause's picture would run more than 0.5 s
from its voice (the default 4000 ms let Codex's earlier WBM ep17 drift 3.7 s, which QC saw as AV mismatches). A
block that is one decision group can still drift a little more (ep17: 1.77 s at most, in narration).

Blocks of about 8 rows. **Source longer than the Italian:** the extra is trimmed from the start and end
of the group's rows (fast rows and narration first; never less than `--min-cut` 0.3 s; keep at least
`--min-keep` 2 s of every trimmed row; 0.3 s or less is absorbed by a tiny speed-up; last-resort trims
are flagged; `--max-speedup` (default 1.0 = off; 1.1 lets a group play up to 10% faster first)). **Source shorter:** the
whole group is stretched evenly; beyond `--max-stretch` 1.25× it is flagged. ep82 (113 rows): 21
groups, 10.6 s of extra US removed in 3 groups, 0 footage played twice.

**Output (both modes).** `manifest_groups.json` and `<ep>_dynamic_groups_review.xlsx` next to the
mapping sheet (one row per group with rows, times, source span, removed time, speed, drift, why it
starts, flags; one row per Italian line). Look at the yellow rows in the video.

## Step 4B. lip scan + lip-sync rows: the video decides (lip-sync shows such as TOLR; run BEFORE Step 4A)

When the source picture shows a character's lips moving, the dub line matched to that sentence must
play that sentence's footage while the dub voice speaks. Owner's rule: **the video analyser decides,
not the text** — quote marks are not used.

### 4B.1 What is measured and how a row becomes a lip-sync row

- **Three measures per frame** (25 frames per second, frames scaled to 540×540):
  1. **TalkNet-ASD audio-visual score**: "is this face speaking the sound we hear";
  2. **TalkNet picture-only score**: "does this mouth move like speech";
  3. **MediaPipe Face Mesh**: how much the lips open and close.
  Faces are found by the **S3FD** face finder on every 2nd frame (confidence 0.9, image scale 0.5) and
  followed between camera cuts (tracks); TalkNet scores each track. A frame counts as **moving lips** when any
  of the three says so; gaps under 0.3 s are joined, stretches under 0.4 s dropped. AI-generated faces are not
  lip-synced to the voice, so the sound check alone finds little.
- **Speaking on screen.** A source sentence (its Whisper time from Step 1A) is speaking when it has at least
  `--min-lip` 0.4 s of moving lips inside it. `lipsync` stops if any sentence is not covered by the scan yet.
- **Lip-sync rows.** Every dub row matched to a speaking sentence becomes a lip-sync row: it plays **that
  sentence's whole clip** (its exact Whisper start to end), anchored so the clip starts when the dub voice
  starts; rows sharing one sentence each play their share. `groups` then builds single lip rows, lip groups
  and trims (Step 4A mode 1): a lip sentence that would play faster than 1.6× is cut down to the part around
  its lip movement, sized to play at 1.5×.
- **Outputs.** `<ep>_lipsync_review.xlsx` (every source sentence with its Whisper time, lip times, seconds of
  lips, type after video and the dub rows using it; every lip-sync row; every lip movement) and a "Type after
  video" column on the mapping sheet. `lipmark` (tests) marks a `words --first N --out` workbook the same way.
- **What to check.** Make a few frames of lip-sync rows: the right face should be talking.

### 4B.2 The scan: fast route (default) = GPU half + Face Mesh half, then merge

```bash
python3 lipgpu.py  --video SRC.mp4 --out-dir W/lips_nets      # S3FD + TalkNet on the fastest device (auto)
python3 lipmesh.py --video SRC.mp4 --out-dir W/lips_mesh      # Face Mesh; light; may run at the same time
python3 lipmerge.py --nets W/lips_nets --mesh W/lips_mesh --out W/lips_range
python3 dubstitch.py lipsync --work W
python3 dubstitch.py groups --work W --size 7                 # Step 4A, mode 1
```

- Both scanners cut the video into the same **15-s pieces** (`--piece`; or pass the same `--ranges` file;
  `lipgpu.py` writes `W/lips_nets/ranges.json`, which `lipmerge.py` uses). Both are **resumable** (pieces
  already written are skipped) and take `--max-seconds`.
- **Device (auto):** NVIDIA GPU (`cuda`) > Apple GPU (`mps`, any Apple-silicon Mac) > CPU. `--device` forces one.
- **Where to run `lipmesh.py`:** on **Linux** when you can (the Linux machine itself, a Linux VM, WSL on Windows,
  or Docker on a Mac). MediaPipe's macOS build measures lip opening slightly differently: on ep24, Face Mesh on
  the Mac flipped 2 of 174 sentences, Face Mesh on Linux 0. If no Linux is available, run it on the Mac and say
  so in the run notes.
- **Exactness (tested).** `lipgpu.py --device cpu` + `lipmesh.py` + `lipmerge.py` give **byte-identical** files
  to `lipdetect.py --range` (ep25, 0-30 s: scores, picture scores, mouth and aperture all equal). On the Apple GPU
  the arithmetic differs very slightly (S3FD boxes 0.1-3 px apart), which changed **0 of 174** speaking sentences
  on ep24 and **1 of 161** on ep25 (4 of 177 Italian rows) — the same size as the run-to-run noise of the
  original method (cutting the pieces at other places changed 3 of 161 sentences on ep25).
- **Piece edges matter a little.** A sentence within about a second of a piece edge can lose (or gain) its lip
  movement, because the face track is cut there. ep26 (v16 run, 15-s pieces) vs the earlier ep26 run (pieces cut
  elsewhere): 3 of 217 sentences differed (DE#115, 117, 123, all next to an edge; 4 lip-sync rows, 8 of 230
  Italian rows got other footage). The same three sentences scanned on the Mac GPU with the earlier run's pieces
  matched the earlier run exactly, so the difference comes from the edges, not from the GPU. Always use the
  default 15-s pieces so episodes are scanned the same way.

### 4B.3 Speed and the computer's safety (the scan must never make the computer hang)

Measured on full episodes (lip scan only):

| Computer | Route | Time per second of video | 12-16 min episode |
|---|---|---|---|
| 4-core Linux VM, CPU (reference `lipdetect.py`) | CPU | about 3 s | 37-50 min |
| Apple M4, 16 GB (Metal GPU), ep24 / ep25 / ep26 | GPU half on the Mac + `lipmesh.py` on Linux in parallel | 0.70 / 0.67 / 0.99 s | 8.6 / 8.4 / 15.5 min |
| (ep26 ran while the Linux VM on the same Mac did Face Mesh and Steps 1A-4: 9-11 s per 15-s piece at the start, 11-24 s later) | | | |
| NVIDIA GPU (Windows/Linux) | `lipgpu.py` (cuda) | not measured yet; expected faster than CPU | — |

`lipgpu.py` protects the computer by default (nothing to set):
- CPU threads = cores − 2 (at least 1), process priority lowered (`nice 10`; Windows "below normal");
- Apple GPU memory capped: `PYTORCH_MPS_HIGH_WATERMARK_RATIO` 0.5 (0.35 on Macs with 8 GB or less), low
  watermark = 0.8 × high (the low ratio must be below the high one, or torch refuses to start);
  NVIDIA: at most 60% of the card's memory;
- before every piece it waits while free RAM is below `--min-free-gb` (default the larger of 1.5 GB and 12% of
  the RAM), up to 15 min, and logs it;
- keeps the computer awake only while it runs (macOS `caffeinate -w`, Windows `SetThreadExecutionState`);
- logs free RAM, CPU and its own memory after every piece. ep25 on the M4: GPU about 84% busy, CPU about 1.8
  cores, its memory peaked at 3.1 GB, the Mac's free RAM never fell under 2.3 GB.

Settings for smaller or other computers:

| Computer | What to do |
|---|---|
| Mac M1/M2/M3 with **8 GB** | defaults (MPS cap 0.35 is automatic); close heavy apps; if the log shows RAM waits, add `--threads 4`. Expect roughly 1-2 s per second of video (not measured). |
| Mac with 16 GB or more | defaults. |
| **Windows, 16 GB, NVIDIA GPU** | install the CUDA build of torch (`pip install torch --index-url https://download.pytorch.org/whl/cu121`), defaults; run `lipmesh.py` in WSL if possible. Not measured yet. |
| Windows, 16 GB, no NVIDIA GPU | CPU route: `lipgpu.py --device cpu` (same result as `lipdetect.py`), expect 30-60 min per episode; leave `--threads` at the default so the computer stays usable. |
| Linux server / VM, CPU only | `lipgpu.py --device cpu` and `lipmesh.py` one after the other; on a per-command time cap use `--max-seconds 150` and re-run. |

### 4B.4 When the agent cannot run programs on the Mac itself (Claude Cowork: agent in a Linux VM on the Mac)

The agent's shell is a Linux VM without the Apple GPU. Use the **Mac runner** (`mac_lipscan/mac_runner.command`
from the Appendix): the owner double-clicks it once (a Terminal window stays open); it runs every `*.sh` job file
that appears in `mac_lipscan/jobs/`, natively on the Mac, writes `done/<job>.log`, and shows `runner_status.txt`.
Close the window to stop it.

One-time setup job (`jobs/01_setup.sh`; uses `uv` for a private Python 3.12, because mediapipe 0.10.14 needs ≤ 3.12):

```bash
cd "$(dirname "$0")/.."; export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"
[ -x venv312/bin/python ] || uv venv --python 3.12 venv312
source venv312/bin/activate
uv pip install torch torchvision "numpy<2" scipy opencv-python "mediapipe==0.10.14" python_speech_features imageio-ffmpeg psutil
ln -sf "$(python -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')" venv312/bin/ffmpeg
python -c "import torch;print('torch',torch.__version__,'mps',torch.backends.mps.is_available())"; echo SETUP OK
```

Episode job (`jobs/NN_ep<N>.sh`; write it under another name and rename it to `.sh` when complete, so the runner
never starts a half-written file). Paths are the Mac's own paths:

```bash
cd "$(dirname "$0")/.."; source venv312/bin/activate
python -u ../scripts/lipgpu.py --video "/Users/<owner>/.../SRC.mp4" --out-dir ep<N>_nets --talknet ../third_party/TalkNet-ASD \
  2>&1 | grep -E --line-buffered -v -i "warn|index_select|^  "
```

Meanwhile run `lipmesh.py` in the VM (it reads the same video through the shared folder), then `lipmerge.py
--nets <mac_lipscan>/ep<N>_nets --mesh W/lips_mesh --out W/lips_range` in the VM. The VM shares the Mac's CPU,
so the GPU half runs a little slower while `lipmesh.py` runs (ep26: 10 s per 15-s piece alone, 13-19 s during it).
Background processes in the Cowork VM are stopped when a command returns: run `lipmesh.py` in the foreground
with `--max-seconds 150` and repeat.

### 4B.5 Fallbacks and what not to change

- **Reference route** (slowest, any machine): `lipdetect.py --range` piece by piece —
  `for s in $(seq 0 15 <END>); do e=$((s+15)); [ -f W/lips_range/range_${s}_${e}.json ] || python3 lipdetect.py --work W --video SRC.mp4 --talknet .../TalkNet-ASD --range $s,$e; done`
  (each 15-s piece about 45 s on a 4-core laptop). `lipfast.py --work W --video SRC.mp4 --talknet DIR --range
  "0,15;15,30;..." --det s3fd` (quote the list) gives the same files and decodes each piece once.
- **Tested and rejected (do not switch to them):** YuNet face finder (5× faster, but ep24 1/174 + ep25 5/161
  sentences and 14/177 rows changed), S3FD on every 4th frame (ep25 3 sentences / 17 rows), SCRFD-500M (ep24 5
  sentences / 24 rows), a YuNet+S3FD hybrid (ep25 11 sentences), ONNX runtime or JIT (only ~10% faster), several
  parallel processes on 4 cores (no gain). Apple Neural Engine (Core ML) was not tested end to end; the GPU route is used.

## Step 5. render

```bash
python3 dubstitch.py render --work W --subs keep [--workers 4] [--max-seconds 150]
```

Renders the active edit list (`manifest_groups.json`) to `W/video_groups_nosubs.mp4`: every segment a
complete source window at one constant speed, frame-exact on one cumulative grid (no drift), the dub
audio as the only track. Resumable. Not a deliverable.

## Step 6. verify

```bash
python3 dubstitch.py verify --work W [--workers 4]
```

Duration must equal the dub audio (within about 2 frames); every segment's middle frame must match
the source frame the edit list names (±6 frames, correlation ≥ 0.90; fast motion blur can score just
under — check by eye); `W/qc_sheet.jpg` — **look at it**: each segment must show its own line's scene.

## Step 7. bookends: the clean master (source opening + dub episode + source ending)

```bash
python3 dubstitch.py bookends --work W --out OUT/TDMB_EP<N>_IT_final_clean.mp4     # TOLR: --out W/clean_noglow.mp4
```

`clean master = [source opening, 1x, own sound] + [render] + [source ending, 1x, own sound]`. Opening =
source 0 s up to the first matched clip or first source subtitle (and before any burned-in caption);
ending = after the last of both to the end. Source sound fades at the joins (`--fade 0.25`); pieces
under 0.5 s are skipped. It records `opening_s`, `ending_s` and the ending's source start; the total
length and the dub audio start are self-checked; the Video edit list gets yellow opening/ending rows.

`--no-ending` (WBM): no source footage after the last dub line, so the video ends with the voice. Without it,
WBM ep17 showed 2.5 s of the US tail (Demetrio walking off) with only the US background sound after the Italian
voice ended, and QC flagged "audio ends 07:21, visuals continue till 07:24". The opening is still added.

## Step 7B. transitions at every clip switch (TOLR; owner's choice since ep24 v3)

```bash
python3 dubstitch.py bookends --work W --out W/clean_noglow.mp4                       # Step 7 into the work folder
python3 dubstitch.py transitions --work W --out OUT/TOLR_EP<N>_IT_final_clean.mp4 [--max-seconds 150]
python3 dubstitch.py transitions --work W --out TEST.mp4 --until 180                   # a 3-minute test clip only
```

Wherever the stitched picture **jumps** in the source footage — between two groups, around a single
lip-sync row, at a trimmed lip sentence, and at the joins to the opening and ending — the switch gets
one of the owner's three transitions, chosen **at random but never the same one twice in a row**
(`--seed 24` makes the order repeatable):
- **light leak** (0.6 s): a soft mix of the two clips with a warm orange flare sweeping across;
- **blur dissolve** (0.6 s): both clips blur while they mix;
- **zoom blur** (0.5 s): a radial zoom punch over the switch.
The dissolving ones mix the outgoing clip, running on a little past its end in the source at its own
speed, with the incoming clip, starting a little before its start. The source's own camera cuts and
every place where the footage simply continues are left alone.
- **Length:** as above (`--length` scales them), never more than 45% of the clip next to it.
- **Sync:** no overlap is added; the length and the sync with the dub voice never change.
- `--styles` picks other kinds (`glow` is still available: `glow --out ...` = only the glow).
- **Resumable** in 60-s chunks; re-run until it prints `saved`.
- The result becomes the **clean master** for `subs` and `check`; every switch time and its transition go
  to a "Transitions" tab of the mapping sheet. Run `subs` only after `transitions` printed `saved`.
- `--until N` writes only the first N seconds as a test and leaves the clean master unchanged.
- Re-run `bookends` and then `transitions` after any change to the edit list.

## Step 8. subs: burn the Italian subtitles onto a copy of the clean master (last step before check)

The clean master is never changed: `subs` writes a new video. **Pick the style by show:**

| Show | Command | Look |
|---|---|---|
| TDMB / DMB | `subs` (= `--style strip`) | white full-width strip, black text (8A) |
| every other show (TOLR, new shows) | `subs --style box` | white text on a 30% black rounded box over the picture (8B) |

```bash
python3 dubstitch.py subs --work W --out OUT/TDMB_EP<N>_IT_final_it_subs.mp4 [--workers 4] [--max-seconds 60]          # TDMB
python3 dubstitch.py subs --work W --style box --out OUT/TOLR_EP<N>_IT_final_it_subs.mp4 [--max-seconds 140]          # other shows
```

Both styles take every card from the SRT, shift it by the opening length (`--offset` to change), drop script
markup (`*italic*` asterisks, `°°°`), register the video for `check`, and are resumable (60-s chunks).

### 8B. Box style (every show except TDMB) — the stills-to-video subtitle method, exactly

1. **One PNG per cue:** each SRT cue becomes a transparent 1080×1080 PNG (`pngsubs.py`; on a video of another size
   this layer is centred at the bottom): white
   **Poppins Bold** text, centred, on a black **rounded box at exactly 30% opacity** (RGBA `0,0,0,77`, the
   same for the whole episode), box bottom **71 px** above the frame bottom, text at most **86%** of the frame
   width; font **38 px**, shrinking 1 px at a time down to **30 px** only if needed; wraps to up to **3 lines**
   before shrinking. Box padding 22 px left/right, 14 px top/bottom, corner radius 14 px, line height 1.22×.
   Multi-line SRT cards are joined into one string and re-wrapped.
2. **Timeline:** an ffmpeg concat list of the PNGs at their exact SRT times with a **fully transparent blank
   PNG** in every gap (no holding of the previous line; the last file is repeated, as concat needs).
3. **Transparent overlay movie:** `-f concat ... -vf fps=<video fps>,format=argb -c:v qtrle subtitle_overlay.mov`.
4. **Overlay** `[0:v][1:v]overlay=0:0:format=auto` on the clean master, encoded in frame-exact chunks
   (libx264 crf 18, yuv420p), joined with stream copy, the clean master's own audio muxed back (AAC).
5. The picture outside the box is untouched; length and frame count equal the clean master. Work files:
   `W/pngsubs_<output name>/` (the PNGs; the large overlay movie and parts are deleted when done).
6. **Check:** the run prints the saved length (must equal the clean master); look at a few frames (one-line
   and two-line cues) and at `check`'s `final_check.jpg`.

`pngsubs.py --video V --srt S --out O --work D [--offset s] [--preview 40]` runs the same thing standalone
(`--preview 40` = a 40-s test clip).

### 8A. Strip style (TDMB / DMB only)
- **Strip:** white, full width, over the detected burned-in caption box + 12 px (TDMB ep84: rows
  855-1004 of 1080); with no caption box a band at 79-93% of the height; `--box` by hand.
- **Text:** every SRT card exactly as written at its exact times; Poppins Bold 40 px on a 1080-px
  frame (scaled with the height), black, centred; two balanced lines when too wide; smaller font only
  if needed (reported).
- **When the strip shows:** pauses under 1.5 s keep the previous line; lead-in from the first dub
  frame if the first card starts within 5 s; an empty strip wherever a source caption would show; no
  strip-off stretch under 0.5 s.
- **Self-check:** strip on every planned frame, nothing else changed, same duration, 0 visible source
  captions; `W/subs_qc.jpg` — look at it. With a per-command cap: `--max-seconds 60`, re-run until saved.

## Step 9. translate + subs: the English-subtitle video (mandatory)

```bash
python3 dubstitch.py translate --work W --out OUT/TDMB_EP<N>_IT_english_google.srt
python3 dubstitch.py subs --work W --srt OUT/TDMB_EP<N>_IT_english_google.srt --out OUT/TDMB_EP<N>_IT_final_en_subs.mp4   # TDMB
python3 dubstitch.py subs --work W --style box --srt OUT/TOLR_EP<N>_IT_english_google.srt --out OUT/TOLR_EP<N>_IT_final_en_subs.mp4   # other shows
```

The English video uses the same style as the Italian one.

Translates the **original dub SRT** (never the source text) with Google, whole sentences at a time
(free endpoint `client=gtx`; `--engine cloud --api-key KEY` for the Cloud API), puts each English
sentence back on the same cards and times, keeps character names as in the dub (Google turns Viola
into "Purple"), caches in `W/translations_it_en.json`. If Google refuses: `TRANSLATION BLOCKED`, the
episode is blocked (wait, `--engine cloud`, or `--export-todo`/`--import`). Record that the text was
sent to Google.

**Blocked and the episode is needed now (WBM ep17):** `translate --export-todo W/todo.tsv`, then the agent writes
the English in the 2nd column itself (whole sentences, character names exactly as in the dub, the line count
unchanged), then `translate --engine file --import W/todo_done.tsv --out OUT/<SHOW>_EP<N>_IT_english.srt`. Name it
`..._english.srt` (not `_google`) and tell the owner who translated it.

## Step 10. check: final verification of the episode package (mandatory)

```bash
python3 dubstitch.py check --work W
```

Clean master and two subtitled videos exist with video and audio and the same length; mapping sheet,
group review and translated SRT exist; `W/final_check.jpg` — look at it. It must end with
`FINAL CHECK PASSED`.

## Step 11. What to report to the person
- Rows, matched, gaps, decided by hand (with examples); unused source lines and why; dub repeats.
- Slowest and fastest clips/groups; verify (duration difference, sync pass count).
- Groups: how many, why any starts early, removed time (TDMB), flagged groups, largest drift.
- TOLR: speaking sentences, lip-sync rows, lip groups, single lip rows, trimmed lip sentences, number of
  transitions of each kind; the lip scan's route, device and time (Step 4B).
- Opening/ending lengths and final length; subtitle style used (strip or box) and per language the
  cards (strip: two-line cards, reduced fonts, lead-in, self-check 3 zeros); translation (engine,
  sentences, names kept); FINAL CHECK.
- Where the files are, and what you're unsure of. Never claim "all matched" without reading every
  flagged row.

## Step 12. Why each rule exists (lessons from real runs)
- **Model and DP.** MiniLM beat LaBSE, mpnet and a cross-encoder reranker; the DP is globally optimal.
- **Don't carry settings over between episodes**; re-run everything when an SRT changes.
- **The 0 ms rule was narrowed** (dropping every 0 ms cue deleted three real matches).
- **The duplicate check exists** because "120/120 matched" on ep82 hid 8 rows on other lines' footage.
- **Case A in the 0.50-0.60 band is wrong about half the time** ("Troppo calma." → "Scream more.").
- **No first-clip rule:** pulling the first clip back to frame 0 showed an unmatched scene.
- **Gap fill:** even 0.8 s skipped inside one shot reads as a jump cut.
- **Groups are a command with hard break rules:** the hand-made version played 17 s of footage twice.
- **Translate by sentence; protect names; clean master before subtitles.**
- **Timing check:** TDMB ep84's US SRT was 12.8 s late and every later step still "passed".
- **Sentence times from the audio (Step 1A):** card times put "Schweinebraten." 1.3 s early, so its lip
  movement went to the wrong sentence. **Words trimmed to the voice:** Whisper glued a 1.3 s pause onto
  "Bring". **Caption box must sit low:** white shirts on TOLR were detected as a box mid-frame.
- **Why lip groups (TOLR).** Playing every lip-sync row as its own clip made 88 clip switches in
  ep24, most of them inside one conversation (the phone call). Lip groups: 88 → 46 switches.
- **Why exact-span groups (TOLR).** The owner's rule: rows mapped to German rows a..b play German a's
  start → b's end as one clip. Trimming extra time made a jump at every trim (7 of the 20 switches
  in ep24's first 2 minutes).
- **Why too-fast lip sentences are trimmed.** IT#19 played 3.1 s of German in 0.87 s (3.56×); the owner
  chose keeping 1.3 s (1.5×) over 1.0 s (1.15×).
- **Why the transitions are made by the tool.** No editor presets exist in the owner's files; the tool
  draws its own. The owner first approved a glow, then compared 24 two-second previews on one ep24
  switch (`output/ep24/transitions/`) and chose light leak, blur dissolve and zoom blur, mixed at random
  and never the same twice in a row. They add no overlap, so sync is untouched.
- **A closing quote stays with its sentence** (it used to start the next one and broke the prefixes).
- **Why two subtitle styles.** TDMB's US video has burned-in English captions, so the white strip has to
  cover them. TOLR's German video is clean, and the owner chose the stills-to-video look (transparent PNGs,
  30% black box) after testing it on ep42; it is the default for every show except TDMB/DMB.
- **Dub SRTs can hold extra copies.** TOLR ep44/ep45 Italian SRTs had a second, uncleaned copy of an episode
  appended after the audio's end (cards timed past the audio length); use only the cards inside the audio
  (a trimmed copy in the work folder) and tell the owner.

- **Why the lip scan runs in two halves (Step 4B).** S3FD face finding is about 62% of the scan's time and TalkNet
  about 15%; both run well on a GPU (Apple M4: about 4× faster than a 4-core CPU). Face Mesh is light and stays on
  Linux because MediaPipe's macOS build measures the lip opening differently (ep24: 2 sentences flipped on the Mac,
  0 on Linux). Faster face finders were tried on ep24/ep25 and each changed more rows than the owner accepts.
- **Episode quirks seen so far — look for them every time:**
  - A subtitle file timed to a different cut: TOLR ep28's German SRT was timed to an older cut (an extra 15-s
    warning at the start and one sentence twice). Fixed with a corrected copy of the SRT in the work folder, each
    card moved by the offset measured against the Whisper words (15.2 s, then 22.05 s after the repeated sentence).
  - Dub material with no source footage: ep28's Italian audio starts with a 14.5-s content warning that the German
    video does not have; it was given a black screen (0-14.48 s) and the owner was told. Ask the owner first.
  - Two rows sharing a few frames make `sheet`'s integrity check fail (ep29: rows 112/113 shared 170 ms); decide
    them as one group over both source lines (ep29: IT#112 + IT#113 → DE#107-108).
  - Google translate answers `429` / `TRANSLATION BLOCKED` after many episodes in a row: `translate --export-todo
    todo.tsv`, translate those lines another way (later, from another network, or `--engine cloud --api-key`),
    then `translate --engine file --import todo.tsv`.
  - The source video with burned-in subtitles (TOLR ep41 `41 de.mp4`): ask for the clean file.
- **Why 0.4 s for the lip rule (`--min-len 0.4 --min-lip 0.4`), tested on TOLR ep24 (Oct 2026).** Halved (0.2/0.2) added
  16 speaking sentences, only 4 really talking (11 smiles, still faces, a cut to a building or crowd, an eye close-up);
  no threshold added 21 (5 real). "0.4-s stretches, any overlap" added 7 (3 real, 2 wrong). The owner kept 0.4 / 0.4.
- **WBM ep17 QC (Oct 2026) → clause mode, `--max-drift 500`, `--no-ending`.** The QC notes (the earlier Codex
  build): 01:00-01:03 and 01:17-01:27 AV mismatch, Viola "speaking unnecessarily" at 02:15, AV mismatch at 06:04,
  blurry 06:59-07:05, picture running 3 s after the voice. Causes: whole sentences holding several actions,
  groups drifting up to 3.7 s, narration clauses on talking shots, and the source tail. After the fixes every
  spot was checked frame by frame: right speaker, smile on "sorrise", Alice on "Alice, che aveva capito",
  still mouth on narration, x-ray/pain instead of Demetrio talking, video ends with the voice. The blur is in
  the US shot itself (Viola over his shoulder): it is now 1.5 s instead of 7 s.
- **Files on a shared folder can arrive late or stale** (a synced/bridged folder may keep an older copy for a
  while): write a new file under a new name and rename it, and check its md5 before using it.

## Step 13. Checklist
- [ ] Same episode confirmed (DP matched rate normal); subs keep/crop, labels, output names, subtitle style settled.
- [ ] `words` for both sides (with the show's `--clauses`): summary looked at, no long run of "not heard" words.
- [ ] `analyze` ("using word-timed sentences" for both sides), timing check passed, `analysis.txt` read.
- [ ] By meaning in `decisions.json`: every unmatched, Case A, duplicate, second-look, repeat and unused line.
- [ ] `sheet` passed the integrity check; every unused source line has a note.
- [ ] `manifest` run; slow and fast clips noted.
- [ ] (lip-sync shows) whole source video scanned (`lipgpu.py` + `lipmesh.py` + `lipmerge.py`, or the reference
      `lipdetect.py --range`), `lipsync` run, a few lip-sync rows looked at.
- [ ] `groups` (TOLR `--size 7`): 0 shared footage; lip groups and trimmed lip sentences noted; yellow groups looked at.
- [ ] `render` finished; `verify` passed, `qc_sheet.jpg` looked at.
- [ ] `bookends` (TOLR `--out W/clean_noglow.mp4`; WBM `--no-ending`): length check passed.
- [ ] (TOLR) `transitions --out ..._final_clean.mp4`: saved, same length, switches per kind noted, no kind twice in a row.
- [ ] `subs` Italian in the show's style (TDMB: strip, self-check 0/0/0, `subs_qc.jpg` looked at; other shows:
      `--style box`, length = clean master, frames looked at).
- [ ] `translate` (not blocked; names kept) then `subs --srt` English in the same style.
- [ ] Exactly three videos in the output folder: clean, `_it_subs`, `_en_subs`.
- [ ] `check`: FINAL CHECK PASSED, `final_check.jpg` looked at. Step 11 report given.

## Step 14. The tools (all in the Appendix; md5 of this version)

| File (in `adaptation-tool/`) | md5 | Purpose |
|---|---|---|
| `scripts/dubstitch.py` | f723e82639297eb1702f8e9f92e94cf3 | every command: words, analyze, sheet, manifest, lipsync, groups, render, verify, bookends, transitions (and `glow`), subs (`--style strip\|box`), translate, check, lipmark, speech, listen (`python3 dubstitch.py -h`) |
| `scripts/pngsubs.py` | 280b20ec2f3ff24198680d172897226e | box subtitle style (Step 8B); used by `subs --style box`, also runnable alone |
| `scripts/lipdetect.py` | fd3eed4aa869749be7053513997074bf | reference lip detector (TalkNet-ASD + S3FD + Face Mesh), `--range START,END`; library for the others |
| `scripts/lipfast.py` | 9a6a685740e3fe1d99d18cb642133374 | same output as `lipdetect.py --range`, decodes once; library for lipgpu/lipmesh |
| `scripts/lipgpu.py` | 7f819ac4cc799442bc959920ce819054 | Step 4B GPU half (S3FD + TalkNet; cuda/mps/cpu) with the safety limits |
| `scripts/lipmesh.py` | 09f625ba2b31251a71daec2ca578abdd | Step 4B Face Mesh half |
| `scripts/lipmerge.py` | 667b83918546a28f89b0c43cf796e637 | joins the halves into `W/lips_range` |
| `scripts/mksrt.py` | 5a3e56cd4fd99bcaa84241781f19e4a9 | Step 0B: SRT from audio/video (Whisper, second listen, cards, script cleaning, names) |
| `scripts/srtclean.py` | d4217626df812274a51fecdac2798dba | Step 0B: the SRT-cleaning platform's pipeline.py, unchanged |
| `scripts/mkdec.py` | 72fa5cde309f6bdb70c0529da328e79b | Step 2: spec → `decisions.json` (checked) |
| `scripts/merged.py` | 8d476f60331553013a75bf2275880ba8 | Step 2: reading view |
| `mac_lipscan/mac_runner.command` | 213c5039c1d5e7d568f1a0bcfa7029cb | Step 4B.4: runs job files natively on a Mac |

Third-party (not in this file, downloaded in Step 0): TalkNet-ASD with its two weight files, Whisper "small"
(faster-whisper) and the multilingual MiniLM sentence model (both downloaded automatically on first use).

When a tool is changed, give it a new md5 here and in its Appendix marker, and keep the old file next to it
(`<name>_v<k>.py`).

---

# Appendix: complete source code of every tool

Each block is one file, exactly. The line above each block names the file and its md5; `unpack.py`
(see "Start here") writes them all. Blocks are fenced with `~~~~~~` lines.

## Appendix: `scripts/dubstitch.py`

<!-- FILE: scripts/dubstitch.py md5=f723e82639297eb1702f8e9f92e94cf3 -->
~~~~~~python
#!/usr/bin/env python3
"""
dubstitch.py -- match a dubbed episode (dub SRT + dub audio) against the original
(source SRT + source video) of the same episode, build the line-by-line mapping
sheet, and stitch a localized video from the source footage synced to the dub audio.

One file, no project imports. Needs python3, ffmpeg/ffprobe and
    pip install sentence-transformers numpy openpyxl pillow
pip install faster-whisper   (Step 1A `words`, and `listen`)

Run in order, always with the same --work folder:
  words     (Step 1A) exact sentence times from the AUDIO: Whisper hears every word, the SRT's words are lined up
            with it letter by letter; run once for --side src and once for --side dub; `analyze` then uses them
  analyze   check each SRT is timed to its media (auto-fixes a source SRT timed to another
            cut, e.g. a .wav with extra lead-in); parse + clean + embed + align + auto-match;
            print the reading report;
            write <work>/decisions.json template            (see SKILL.md)
  (manual)  read <work>/analysis.txt, fill <work>/decisions.json by MEANING
  sheet     apply decisions, run integrity checks, write the mapping .xlsx
  manifest  gap-free, bleed-checked edit list (<work>/manifest.json)
  groups    continuous US footage per block of 7-8 Italian rows (<work>/manifest_groups.json + review workbook)
  render    frame-exact render of the active edit list + mux dub audio, no subtitles (resumable)
  verify    sync-check every segment against the source + QC contact sheet
  bookends  add the source's unused opening and ending (1x, own sound) -> the CLEAN master video
  transitions  a transition at every clip switch of the clean master (TOLR: light leak / blur dissolve / zoom blur,
            random order, never twice in a row; length and sync unchanged)  [`glow` = the same with the glow only]
  subs      burn an SRT (white strip, Poppins Bold) onto the clean master -> a subtitled final video
  translate translate the dub SRT through Google, sentence by sentence, onto the same cards/times
  check     final check of the episode package (clean + subtitled videos, sheets, translated SRT)
  speech    (optional) which source sentences are dialogue in quotes vs narration
  lipsync   rows whose source sentence shows moving lips (lip scan of the whole video) play that sentence's whole clip
  lipmark   (tests) mark each word-timed sentence "speaking on screen" or not, from lipdetect.py --range results
  listen    (optional) transcribe a stretch of the dub audio, e.g. to confirm a repeat
"""
import argparse, difflib, hashlib, io, json, math, os, re, shutil, subprocess, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict

# ---------------------------------------------------------------------------------
# 1. SRT parsing, sentence resegmentation, overlap resolution (both sides)
# ---------------------------------------------------------------------------------
_TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})")
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_SCENE_MARK = re.compile(r"°+|\*+")   # "°°°" scene breaks and *italic* asterisks are markup, not text
_SENT_END = re.compile(r'(?<=[.!?…])(?:["»“”])?\s+'
                       r'(?=[A-ZÀ-ÆÇ-ÏÒ-ÖØ-ÜÑ0-9"„«])')

@dataclass
class Cue:
    index: int
    start_ms: int
    end_ms: int
    text: str

def _ms(h, m, s, x):
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(x)

def parse_srt(path):
    raw = open(path, encoding="utf-8-sig", errors="replace").read().replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = block.split("\n")
        ts = next((i for i, ln in enumerate(lines[:2]) if _TS.search(ln)), None)
        if ts is None:
            continue
        g = _TS.search(lines[ts]).groups()
        text = _TAG.sub("", " ".join(l.strip() for l in lines[ts + 1:] if l.strip())).strip()
        text = re.sub(r"\s+", " ", _SCENE_MARK.sub(" ", text)).strip()   # drop script markup: "°°°" scene breaks, *italic* asterisks
        if text:
            cues.append(Cue(len(cues), _ms(*g[:4]), _ms(*g[4:]), text))
    cues.sort(key=lambda c: c.start_ms)
    return cues

# Clause mode (WBM ep17 QC, Oct 2026): a long sentence with several actions ("..., Viola sorrise beffarda, ...") is matched
# clause by clause, so the picture changes where that part of the voice starts. `words --clauses N` / `analyze --clauses N`
# turn it on for the work folder (saved in <work>/sentence_mode.json, used by every later step); a sentence is cut after
# , ; : and before a dash, only where both parts keep at least N words. N = 0 (default) = whole sentences.
CLAUSE_MIN = int(os.environ.get("DUBSTITCH_CLAUSES", "0") or 0)
_CLAUSE_CUT = re.compile(r'[,;:]["»”’]?(?=\s)|(?<=\s)[–—](?=\s)')

def clause_bounds(full, bounds, n_min):
    out = []
    for a, b in zip(bounds, bounds[1:]):
        out.append(a); last = a
        for m in _CLAUSE_CUT.finditer(full, a, b):
            cut = m.end() if m.group(0)[0] in ",;:" else m.start()
            if len(full[last:cut].split()) >= n_min and len(full[cut:b].split()) >= n_min:
                out.append(cut); last = cut
    out.append(bounds[-1])
    return sorted(set(out))

def resegment_to_sentences(cues):
    """One unit per sentence (raw subtitle cards are cut by reading speed, not by sentence)."""
    pieces, spans, pos = [], [], 0
    for c in cues:
        if pieces:
            pieces.append(" "); pos += 1
        start = pos; pieces.append(c.text); pos += len(c.text); spans.append((start, pos, c))
    full = "".join(pieces)
    # a closing quote stays with its own sentence («...!» | Next), not at the start of the next one
    bounds = sorted(set([0] + [m.start() + len(m.group(0).rstrip()) for m in _SENT_END.finditer(full)] + [len(full)]))
    if CLAUSE_MIN:
        bounds = clause_bounds(full, bounds, CLAUSE_MIN)
    out = []
    for a, b in zip(bounds, bounds[1:]):
        text = full[a:b].strip()
        ov = [c for (cs, ce, c) in spans if cs < b and ce > a]
        if text and ov:
            out.append(Cue(len(out), ov[0].start_ms, ov[-1].end_ms, text))
    return out

def resolve_overlaps(cues):
    """Cut each boundary at the midpoint of the original overlap. Can collapse a cue to 0ms (see 0 ms rule)."""
    n = len(cues)
    if not n:
        return []
    cut = [0] * (n + 1); cut[0] = cues[0].start_ms; cut[n] = cues[-1].end_ms
    for i in range(1, n):
        pe, cs = cues[i - 1].end_ms, cues[i].start_ms
        cut[i] = (pe + cs) // 2 if cs < pe else cs
    out, cur = [], cut[0]
    for i, c in enumerate(cues):
        s = max(cut[i], cur); e = max(min(cut[i + 1], c.end_ms), s)
        out.append(Cue(c.index, s, e, c.text)); cur = e
    return out

# 0 ms rule (Step 1): zero-duration cues -------------------------------------------------------------
def drop_single_word_zero_duration(cues):
    """Drop a cue only if it is BOTH 0ms long AND a single word ("Federico!", "Forse.")."""
    kept, dropped = [], []
    for c in cues:
        (dropped if (c.start_ms == c.end_ms and len(c.text.split()) <= 1) else kept).append(c)
    return kept, dropped

def retime_zero_duration(cues):
    """Every remaining 0ms cue is real dialogue: share the time of [real neighbour + 0ms run +
    real neighbour] among them in proportion to text length. Outer bounds never move."""
    cues = [Cue(c.index, c.start_ms, c.end_ms, c.text) for c in cues]
    n, i, retimed = len(cues), 0, []
    while i < n:
        if cues[i].end_ms > cues[i].start_ms:
            i += 1; continue
        j = i
        while j < n and cues[j].end_ms <= cues[j].start_ms:
            j += 1
        block = cues[(i - 1 if i > 0 else i):(j if j < n else j - 1) + 1]
        t0, t1 = block[0].start_ms, block[-1].end_ms
        w = [max(len(c.text), 1) for c in block]; tot, cur = sum(w), float(t0)
        for k, c in enumerate(block):
            c.start_ms = round(cur); cur += (t1 - t0) * w[k] / tot; c.end_ms = round(cur)
        block[-1].end_ms = t1
        retimed.extend(cues[i:j]); i = j
    bad = [c.text for c in cues if c.end_ms <= c.start_ms]
    if bad:
        raise SystemExit(f"re-timing could not give these cues any time (no real neighbour): {bad}")
    return cues, retimed

# ---------------------------------------------------------------------------------
# 1b. Subtitle timing check: is each SRT timed to the media it is paired with?
#     (Real case, TDMB ep84: the US SRT was timed to a US .wav that has 12.833 s more
#     lead-in than the US .mp4, so every line pointed 12.8 s too late in the video.)
# ---------------------------------------------------------------------------------
def audio_envelope(path, rate=8000, hop=80):
    """Mono 8 kHz samples + 10 ms loudness envelope of any audio/video file."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32); n = len(x) // hop
    if n == 0:
        raise SystemExit(f"no audio track could be read from {path}")
    return np.abs(x[:n * hop]).reshape(n, hop).mean(1), x

def best_lag(a, b, max_lag):
    """Lag L (samples) maximising corr(a[t], b[t+L]) -> (L, peak, best value >100 samples away from L)."""
    import numpy as np
    a = (a - a.mean()) / (a.std() + 1e-9); b = (b - b.mean()) / (b.std() + 1e-9)
    n = 1 << int(np.ceil(np.log2(len(a) + len(b) + 1)))
    c = np.fft.irfft(np.conj(np.fft.rfft(a, n)) * np.fft.rfft(b, n), n) / min(len(a), len(b))
    lags = np.arange(-max_lag, max_lag + 1); v = c[lags % n]; k = int(np.argmax(v))
    far = np.abs(lags - lags[k]) > 100
    return int(lags[k]), float(v[k]), float(v[far].max()) if far.any() else 0.0

def subtitle_lag_s(cues, env):
    """How many seconds LATE the subtitles are versus the speech in the media (about +-0.05 s)."""
    import numpy as np
    le = np.convolve(np.log(env + 1), np.ones(30) / 30, "same")
    lo, hi = np.percentile(le, 20), np.percentile(le, 90)
    speech = (le > lo + 0.5 * (hi - lo)).astype(np.float32)
    subs = np.zeros(len(env), np.float32)
    for c in cues:
        subs[max(c.start_ms // 10, 0):max(c.end_ms // 10, 0)] = 1
    lag, peak, other = best_lag(speech, subs, 6000)
    return lag / 100, peak, other

def reference_offset_s(video, ref_audio):
    """The SRT was timed to ref_audio. Returns how many seconds later everything happens in ref_audio than in
    the video (constant), or raises if the two are not the same edit."""
    import numpy as np
    ev, xv = audio_envelope(video); er, xr = audio_envelope(ref_audio)
    lag, peak, other = best_lag(ev, er, 9000)
    if peak < 0.4 or peak < 2 * other:
        raise SystemExit(f"{ref_audio} does not sound like the audio of {video} (match {peak:.2f}); "
                         "check the files, or pass --src-offset by hand")
    locs = []
    for frac in (0.1, 0.3, 0.5, 0.7, 0.9):
        t = int(len(xv) * frac); w = xv[t:t + 8000 * 5]; s = t + lag * 80
        if s < 0 or s + len(w) > len(xr) or w.std() < 50:
            continue
        l2, p2, _ = best_lag(w, xr[s:s + len(w)], 800)
        if p2 > 0.5:
            locs.append((lag * 80 + l2) / 8000)
    if len(locs) < 3:
        raise SystemExit("could not measure the subtitle audio offset reliably; pass --src-offset by hand")
    if max(locs) - min(locs) > 0.02:
        raise SystemExit(f"the SRT's audio and the video are different edits (offset changes along the episode: "
                         f"{[round(x, 3) for x in locs]}); this needs manual re-timing")
    return float(np.median(locs))

def shift_cues(cues, ms):
    if not ms:
        return cues
    out = [Cue(c.index, max(c.start_ms - ms, 0), c.end_ms - ms, c.text) for c in cues if c.end_ms - ms > 0]
    return out

def find_sibling_audio(srt):
    stem = os.path.splitext(srt)[0]
    for ext in (".wav", ".WAV", ".mp3", ".m4a", ".aac", ".flac"):
        if os.path.exists(stem + ext):
            return stem + ext
    return None

def check_timing(cfg, a, say):
    """Measure and fix the source SRT offset; then verify BOTH SRTs line up with their media's speech."""
    src_off_ms = 0; how = "none needed"
    ref = a.src_srt_audio or (None if a.src_offset is not None else find_sibling_audio(cfg["src_srt"]))
    if a.src_offset is not None:
        src_off_ms = int(round(a.src_offset * 1000)); how = "given with --src-offset"
    elif ref and os.path.abspath(ref) != os.path.abspath(cfg["src_video"]):
        src_off_ms = int(round(reference_offset_s(cfg["src_video"], ref) * 1000))
        how = f"measured against {os.path.basename(ref)} (the audio the SRT was timed to)"
    cfg["src_offset_ms"] = src_off_ms; cfg["src_offset_how"] = how
    say(f"SOURCE SRT OFFSET: {src_off_ms / 1000:+.3f}s ({how}); source subtitle times are moved by {-src_off_ms / 1000:+.3f}s")
    for side, srt, media, off in (("dub", cfg["dub_srt"], cfg["dub_audio"], 0), ("src", cfg["src_srt"], cfg["src_video"], src_off_ms)):
        env, _ = audio_envelope(media); cues = shift_cues(parse_srt(srt), off)
        lag, peak, other = subtitle_lag_s(cues, env)
        end_s, media_s = cues[-1].end_ms / 1000, len(env) / 100
        say(f"TIMING CHECK {side.upper()}: subtitles vs speech in {os.path.basename(media)}: {lag:+.2f}s "
            f"(match {peak:.2f} vs {other:.2f}); last subtitle ends {end_s:.1f}s, media is {media_s:.1f}s")
        clear = peak >= 1.5 * other
        if (abs(lag) > 1.0 and clear) or end_s > media_s + 1.0:
            flag = "--src-offset" if side == "src" else "a re-timed dub SRT"
            raise SystemExit(f"\n** {side.upper()} SRT IS NOT TIMED TO {media} **\n"
                             f"   its subtitles run {lag:+.2f}s against the speech (last subtitle {end_s:.1f}s, media {media_s:.1f}s).\n"
                             f"   Usually the SRT was timed to another cut (e.g. a .wav with extra lead-in). Put that audio next to\n"
                             f"   the SRT with the same name (or pass --src-srt-audio), or, after checking, use {flag} {lag:.3f}.")
        if not clear:
            say(f"   (timing check inconclusive on the {side} side -- spot-check a few lines by ear)")
    return src_off_ms

# ---------------------------------------------------------------------------------
# 1c. words: exact sentence times from the AUDIO, word by word (Step 1A)
#     SRT cards are cut by reading speed, so a sentence that shares a card with others has no time of its own
#     (TOLR ep24: "Schweinebraten." sat inside card 19.0-25.6 s; it is really spoken 20.98-22.06 s).
#     Whisper (faster-whisper, open source, CPU) gives a time for every word it hears; those words are lined
#     up with the SRT letter by letter, so every SRT word keeps its own spelling and gets the audio's time.
#     A sentence runs from the start of its first word to the end of its last word.
# ---------------------------------------------------------------------------------
def _letters(t):
    import unicodedata
    t = unicodedata.normalize("NFC", t.lower()).replace("\u00df", "ss")
    return [ch for ch in t if ch.isalnum()]

def _pcm16k(media, t0, dur):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t0, 0):.3f}", "-t", f"{dur:.3f}", "-i", media, "-vn", "-ac", "1",
                          "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32)

def trim_words_to_voice(words, x, t0, min_sil=0.10):
    """Whisper often lets a word start where the previous word ended, swallowing the pause in between
    ('Bring' 52.36-53.82 s while the voice starts at 53.66 s). Keep only the strongest voiced block of each
    word, measured on the loudness (10 ms steps) of the same audio."""
    import numpy as np
    n = len(x) // 160
    if n < 10: return words
    env = 20 * np.log10(np.sqrt((x[:n * 160].reshape(n, 160) ** 2).mean(1)) + 1)
    p10, p50 = np.percentile(env, 10), np.percentile(env, 50); thr = p10 + 0.4 * (p50 - p10)
    out = []
    for s, e, w, p in words:
        a, b = int((s - t0) * 100), max(int((e - t0) * 100), int((s - t0) * 100) + 1)
        v = env[max(a, 0):b] > thr
        if a < 0 or not v.any(): out.append((s, e, w, p)); continue
        blocks, k = [], 0
        while k < len(v):
            if not v[k]: k += 1; continue
            j = k
            while j < len(v):
                if v[j]: j += 1; continue
                g = j
                while g < len(v) and not v[g]: g += 1
                if g - j >= min_sil * 100 or g == len(v): break
                j = g
            blocks.append((k, j)); k = j
        k0, k1 = max(blocks, key=lambda q: float((env[a + q[0]:a + q[1]] - thr).clip(0).sum()))
        s2 = t0 + (a + k0) / 100 if k0 > 0 and not v[:k0].all() else s
        e2 = t0 + (a + k1) / 100 if k1 < len(v) and not v[k1:].all() else e
        out.append((round(s2, 3), round(e2, 3), w, p))
    return out

def transcribe_words(media, cache_dir, lang, models, t_from, t_to, chunk=60.0, pad=2.0, max_seconds=1e9, model_name="small"):
    """Every word Whisper hears in [t_from, t_to) of the media, with times. Resumable: one cached file per chunk."""
    os.makedirs(cache_dir, exist_ok=True); model = None; stop = time.time() + max_seconds; words = []; todo = 0
    k = 0
    while t_from + k * chunk < t_to:
        cs = t_from + k * chunk; ce = min(cs + chunk, t_to); k += 1
        fp = os.path.join(cache_dir, f"{lang}_{model_name}_{cs:09.2f}_{ce:09.2f}.json")
        if not os.path.exists(fp):
            if time.time() > stop: todo += 1; continue
            if model is None:
                from faster_whisper import WhisperModel
                model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=os.path.join(models, "whisper"))
            w0 = max(cs - pad, 0); x = _pcm16k(media, w0, ce + pad - w0)
            segs, _ = model.transcribe(x / 32768.0, language=lang, word_timestamps=True, beam_size=5, condition_on_previous_text=False)
            ws = [(round(w0 + w.start, 3), round(w0 + w.end, 3), w.word.strip(), round(w.probability, 2)) for s in segs for w in s.words]
            ws = [w for w in trim_words_to_voice(ws, x, w0) if cs <= w[0] < ce and _letters(w[2])]
            json.dump(ws, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"  heard {len(ws)} words in {cs:.0f}-{ce:.0f}s", flush=True)
        words += [tuple(w) for w in json.load(open(fp, encoding="utf-8"))]
    return sorted(words), todo

def srt_words_from_audio(sent_texts, heard):
    """Line up the SRT's words with the words Whisper heard, letter by letter. Every SRT word keeps its own
    spelling and gets the time of its matching letters; a word Whisper missed gets a time between its
    neighbours. Returns (words, extra): extra = heard words that are not in the SRT."""
    A = []
    for wi, (s, e, w, *_r) in enumerate(heard):
        L = _letters(w)
        for k, c in enumerate(L): A.append((c, wi, k, len(L)))
    S, words = [], []
    for j, t in enumerate(sent_texts):
        for tok in t.split():
            L = _letters(tok)
            if not L: continue
            words.append(dict(sent=j, word=tok, n=len(L)))
            S += [(c, len(words) - 1) for c in L]
    sm = difflib.SequenceMatcher(None, [c for c, _ in S], [c for c, *_x in A], autojunk=False)
    m = {}
    for a, b, n in sm.get_matching_blocks():
        for k in range(n): m[a + k] = b + k
    def tl(ai, end):
        c, wi, k, n = A[ai]; s, e = heard[wi][0], heard[wi][1]
        return s + (e - s) * (k + (1 if end else 0)) / n
    used, pos = set(), 0
    for w in words:
        hit = [m[k] for k in range(pos, pos + w["n"]) if k in m]; pos += w["n"]
        if len(hit) >= max(1, 0.5 * w["n"]):
            his = sorted({A[x][1] for x in hit}); used.update(his)
            w.update(start=tl(hit[0], False), end=tl(hit[-1], True), heard_as=" ".join(heard[x][2] for x in his))
            w["status"] = "same" if _letters(w["word"]) == _letters(w["heard_as"]) else "written differently"
        else:
            w.update(start=None, end=None, heard_as="", status="not heard (time filled in)")
    i = 0
    while i < len(words):
        if words[i]["start"] is not None: i += 1; continue
        j = i
        while j < len(words) and words[j]["start"] is None: j += 1
        t0 = words[i - 1]["end"] if i else (words[j]["start"] if j < len(words) else 0.0)
        t1 = words[j]["start"] if j < len(words) else t0
        tot = sum(words[k]["n"] for k in range(i, j)); cur = t0
        for k in range(i, j):
            d = (t1 - t0) * words[k]["n"] / max(tot, 1); words[k]["start"], words[k]["end"] = cur, cur + d; cur += d
        i = j
    for w in words: w["start"], w["end"] = round(w["start"], 3), round(w["end"], 3); w.pop("n")
    return words, [heard[k] for k in range(len(heard)) if k not in used]

def cmd_words(a):
    """Sentences of one SRT with exact times from the media's audio -> <work>/sentences_<side>.json (+ .xlsx with --out).
    `analyze` uses these times instead of the SRT-card estimates when the file is there."""
    cfg = load(a.work, "config.json") if os.path.exists(os.path.join(a.work, "config.json")) else {}
    os.makedirs(a.work, exist_ok=True)
    srt = a.srt or cfg.get("src_srt" if a.side == "src" else "dub_srt")
    media = a.media or cfg.get("src_video" if a.side == "src" else "dub_audio")
    lang = a.lang or ("de" if a.side == "src" else "it")
    models = a.models or cfg.get("models") or os.path.expanduser("~/.cache/dubstitch_models")
    if not srt or not media: raise SystemExit("--srt and --media are needed (or run analyze first so config.json has them)")
    sents = resegment_to_sentences(parse_srt(srt))
    if a.first: sents = sents[:a.first]
    t_to = a.until or (sents[-1].end_ms / 1000 + 5.0)
    t_to = min(t_to, probe_duration_ms(media) / 1000)
    heard, todo = transcribe_words(media, os.path.join(a.work, "asr"), lang, models, 0.0, t_to, max_seconds=a.max_seconds,
                                   model_name=a.model)
    if todo:
        print(f"{todo} audio chunks still to do -- run the same command again (it resumes)"); sys.exit(3)
    words, extra = srt_words_from_audio([c.text for c in sents], heard)
    # Whisper sometimes skips a short sentence inside a long chunk (TOLR ep24 #117 "Ihre Stimme gewann an Intensität."
    # came back as one word). Listen again to just that sentence's subtitle cards, with its text as a hint.
    redo = []
    for j, c in enumerate(sents):
        ww = [w for w in words if w["sent"] == j]
        if not ww: continue
        nh = sum(w["status"].startswith("not") for w in ww); dur = ww[-1]["end"] - ww[0]["start"]
        if nh >= max(1, 0.4 * len(ww)) or dur < 0.03 * len(_letters(c.text)):
            redo.append(j)
    if redo:
        from faster_whisper import WhisperModel
        model = WhisperModel(a.model, device="cpu", compute_type="int8", download_root=os.path.join(models, "whisper"))
        for j in redo:
            c = sents[j]; w0, w1 = max(c.start_ms / 1000 - 0.5, 0), c.end_ms / 1000 + 0.5
            fp = os.path.join(a.work, "asr", f"{lang}_{a.model}_redo_{w0:09.2f}_{w1:09.2f}.json")
            if not os.path.exists(fp):
                x = _pcm16k(media, w0, w1 - w0)
                segs, _ = model.transcribe(x / 32768.0, language=lang, word_timestamps=True, beam_size=5, initial_prompt=c.text,
                                           condition_on_previous_text=False)
                ws = [(round(w0 + w.start, 3), round(w0 + w.end, 3), w.word.strip(), round(w.probability, 2)) for sg in segs for w in sg.words]
                json.dump([w for w in trim_words_to_voice(ws, x, w0) if _letters(w[2])], open(fp, "w", encoding="utf-8"), ensure_ascii=False)
            new = [tuple(w) for w in json.load(open(fp, encoding="utf-8"))]
            new = [w for w in new if w[1] <= w1 - 0.25]           # drop a cut-off word at the window's end
            if new:                                             # replace only what the new pass really covers
                n0, n1 = new[0][0], new[-1][1]
                heard = sorted([w for w in heard if w[1] <= n0 or w[0] >= n1] + new)
        words, extra = srt_words_from_audio([c.text for c in sents], heard)
        print(f"listened again to sentences (#) {[j + 1 for j in redo]} (Whisper had skipped words there)")
    if words: extra = [e for e in extra if e[0] <= words[-1]["end"] + 0.5]   # (a test range stops after its last sentence)
    shares = quote_shares([c.text for c in sents]); rows = []
    for j, c in enumerate(sents):
        ww = [w for w in words if w["sent"] == j]
        st = [w["status"] for w in ww]
        rows.append(dict(idx=j, text=c.text, start_ms=round(ww[0]["start"] * 1000) if ww else c.start_ms,
                         end_ms=round(ww[-1]["end"] * 1000) if ww else c.end_ms, srt_start_ms=c.start_ms, srt_end_ms=c.end_ms,
                         words=len(ww), same=st.count("same"), differently=st.count("written differently"),
                         not_heard=sum(s.startswith("not") for s in st), quote_share=round(shares[j][0], 2),
                         text_type="speech" if shares[j][0] >= 0.6 else ("mixed" if shares[j][0] >= 0.15 else "narration")))
    for x, y in zip(rows, rows[1:]):                           # no overlap between neighbouring sentences
        if y["start_ms"] < x["end_ms"]: x["end_ms"] = y["start_ms"] = (x["end_ms"] + y["start_ms"]) // 2
    save(a.work, f"sentences_{a.side}.json", dict(srt=os.path.abspath(srt), media=os.path.abspath(media), lang=lang, model=a.model,
                                                  sentences=rows, words=words, heard_not_in_srt=extra, first=a.first))
    n = len(words); nd = sum(w["status"] == "written differently" for w in words); nn = sum(w["status"].startswith("not") for w in words)
    print(f"{len(rows)} sentences, {n} SRT words: {n - nd - nn} same as heard, {nd} written differently, {nn} not heard (time filled in); "
          f"{len(extra)} heard words not in the SRT -> {os.path.join(a.work, f'sentences_{a.side}.json')}")
    nh = [(w["sent"] + 1, w["word"]) for w in words if w["status"].startswith("not")]
    if nh: print(f"not heard (time filled in between neighbours), as (sentence #, word): {nh}")
    if a.out: sentences_workbook(a.out, rows, words, extra, a.side)

def sentences_workbook(path, rows, words, extra, side):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Sentences"
    H = Font(name="Arial", bold=True, color="FFFFFF"); HF = PatternFill("solid", start_color="1F4E78"); B = Font(name="Arial", size=10)
    hdr = ["#", "Spoken start (s)", "Spoken end (s)", "Seconds", "Sentence (SRT)", "Words: same / written differently / not heard",
           "SRT card start (s)", "SRT card end (s)", "Type from text (quotes)"]
    for c, h in enumerate(hdr, 1):
        x = ws.cell(1, c, h); x.font = H; x.fill = HF; x.alignment = Alignment(wrap_text=True)
    for i, r in enumerate(rows, 2):
        vals = [r["idx"] + 1, r["start_ms"] / 1000, r["end_ms"] / 1000, round((r["end_ms"] - r["start_ms"]) / 1000, 2), r["text"],
                f"{r['same']} / {r['differently']} / {r['not_heard']}", r["srt_start_ms"] / 1000, r["srt_end_ms"] / 1000, r["text_type"]]
        for c, v in enumerate(vals, 1):
            x = ws.cell(i, c, v); x.font = B; x.alignment = Alignment(wrap_text=True, vertical="top")
    for c, w in enumerate([5, 10, 10, 8, 62, 16, 10, 10, 14], 1): ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
    ws.freeze_panes = "A2"
    w2 = wb.create_sheet("Words")
    for c, h in enumerate(["Sentence #", "Word (SRT spelling)", "Start (s)", "End (s)", "Whisper heard", "Check"], 1):
        x = w2.cell(1, c, h); x.font = H; x.fill = HF
    yel, red = PatternFill("solid", start_color="FFEB9C"), PatternFill("solid", start_color="FFC7CE")
    for k, w in enumerate(words, 2):
        for c, v in enumerate([w["sent"] + 1, w["word"], w["start"], w["end"], w["heard_as"], w["status"]], 1):
            x = w2.cell(k, c, v); x.font = B
            if w["status"] == "written differently": x.fill = yel
            if w["status"].startswith("not"): x.fill = red
    for c, wd in enumerate([10, 24, 10, 10, 24, 26], 1): w2.column_dimensions[openpyxl.utils.get_column_letter(c)].width = wd
    w2.freeze_panes = "A2"
    w3 = wb.create_sheet("Heard, not in SRT")
    for c, h in enumerate(["Start (s)", "End (s)", "Word heard"], 1):
        x = w3.cell(1, c, h); x.font = H; x.fill = HF
    for k, e in enumerate(extra, 2):
        for c, v in enumerate(e[:3], 1): w3.cell(k, c, v).font = B
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True); wb.save(path); print(f"saved {path}")

# ---------------------------------------------------------------------------------
# 1d. lipmark: which sentences show moving lips on screen (needs lipdetect.py --range ... results)
# ---------------------------------------------------------------------------------
def cmd_lipmark(a):
    import numpy as np, openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    S = load(a.work, f"sentences_{a.side}.json"); rows = S["sentences"]
    iv, covered = merged_lip_intervals(a.work, a)
    out = []
    for r in rows:
        s, e = r["start_ms"] / 1000, r["end_ms"] / 1000
        if not covered[int(s * 25):max(int(e * 25), int(s * 25) + 1)].all():
            r.update(lips=None, video_type="not analysed"); continue
        hits = [(max(x, s), min(y, e)) for x, y in iv if min(y, e) - max(x, s) > 0]
        sec = round(sum(y - x for x, y in hits), 2)
        r.update(lips=[[round(x, 2), round(y, 2)] for x, y in hits], lip_s=sec,
                 video_type="speaking on screen" if sec >= a.min_lip else "no lip movement on screen")
    outside = [(round(x, 2), round(y, 2)) for x, y in iv
               if sum(max(0, min(y, r["end_ms"] / 1000) - max(x, r["start_ms"] / 1000)) for r in rows) < 0.2]
    S["lip_intervals"] = [[round(x, 2), round(y, 2)] for x, y in iv]; S["lips_outside_sentences"] = outside
    save(a.work, f"sentences_{a.side}.json", S)
    print(f"lip movement found: {[(round(x, 2), round(y, 2)) for x, y in iv]}")
    for r in rows:
        if r.get("video_type") != "not analysed":
            agree = (r["video_type"].startswith("speaking")) == (r["text_type"] != "narration")
            print(f"  #{r['idx'] + 1:3d} {r['start_ms'] / 1000:7.2f}-{r['end_ms'] / 1000:7.2f}s text {r['text_type']:9s} video {r['video_type']:26s}"
                  f"{'' if agree else ' <- differs'}  {r['text'][:45]!r}")
    if outside: print(f"lip movement in pauses between sentences: {outside}")
    if a.out:
        wb = openpyxl.load_workbook(a.out); ws = wb["Sentences"]
        H = Font(name="Arial", bold=True, color="FFFFFF"); HF = PatternFill("solid", start_color="7030A0")
        for k, h in enumerate(["Lip movement inside the sentence (s)", "Seconds of lip movement", "Type after video", "Same as text type?"]):
            x = ws.cell(1, 10 + k, h); x.font = H; x.fill = HF; x.alignment = Alignment(wrap_text=True)
        g, grey = PatternFill("solid", start_color="C6EFCE"), PatternFill("solid", start_color="F2F2F2")
        for i, r in enumerate(rows, 2):
            if r.get("video_type") == "not analysed": vals = ["", "", "not analysed", ""]
            else:
                same = "yes" if (r["video_type"].startswith("speaking")) == (r["text_type"] != "narration") else "NO"
                vals = [", ".join(f"{x:.2f}-{y:.2f}" for x, y in r["lips"]) or "-", r["lip_s"], r["video_type"], same]
            for k, v in enumerate(vals):
                x = ws.cell(i, 10 + k, v); x.font = Font(name="Arial", size=10, bold=(k == 3 and v == "NO"))
                x.alignment = Alignment(wrap_text=True, vertical="top")
                if r.get("video_type", "").startswith("speaking"): x.fill = g
                elif r.get("video_type") != "not analysed": x.fill = grey
        for k, w in enumerate([24, 10, 24, 10]): ws.column_dimensions[openpyxl.utils.get_column_letter(10 + k)].width = w
        if "Lip movements" in wb.sheetnames: del wb["Lip movements"]
        w4 = wb.create_sheet("Lip movements", 1)
        for c, h in enumerate(["#", "Start (s)", "End (s)", "Seconds", "Sentence(s)"], 1):
            x = w4.cell(1, c, h); x.font = H; x.fill = HF
        for k, (x0, y0) in enumerate(S["lip_intervals"], 2):
            sn = [str(r["idx"] + 1) for r in rows if min(y0, r["end_ms"] / 1000) - max(x0, r["start_ms"] / 1000) > 0]
            for c, v in enumerate([k - 1, x0, y0, round(y0 - x0, 2), ", ".join(sn) or "pause between sentences"], 1): w4.cell(k, c, v).font = Font(name="Arial", size=10)
        wb.save(a.out); print(f"updated {a.out}")

# ---------------------------------------------------------------------------------
# 2. Similarity + monotonic DP alignment + windowed Case A
# ---------------------------------------------------------------------------------
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

def similarity(dub_texts, src_texts, models_dir):
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(MODEL, cache_folder=models_dir)
    a = m.encode(dub_texts, normalize_embeddings=True, show_progress_bar=False)
    b = m.encode(src_texts, normalize_embeddings=True, show_progress_bar=False)
    return (a @ b.T).tolist()

def align(sim, match_threshold=0.5, gap_penalty=0.1, skip_penalty=0.1):
    n = len(sim); m = len(sim[0]) if n else 0
    D = [[0.0] * (m + 1) for _ in range(n + 1)]; BT = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1): D[i][0] = D[i - 1][0] - gap_penalty; BT[i][0] = "G"
    for j in range(1, m + 1): D[0][j] = D[0][j - 1] - skip_penalty; BT[0][j] = "S"
    for i in range(1, n + 1):
        row = sim[i - 1]
        for j in range(1, m + 1):
            best, mv = D[i - 1][j - 1] + (row[j - 1] - match_threshold), "M"
            g = D[i - 1][j] - gap_penalty
            s = D[i][j - 1] - skip_penalty
            if g > best: best, mv = g, "G"
            if s > best: best, mv = s, "S"
            D[i][j] = best; BT[i][j] = mv
    path, i, j = [], n, m
    while i > 0 or j > 0:
        mv = BT[i][j]
        if mv == "M": path.append(("match", i - 1, j - 1)); i, j = i - 1, j - 1
        elif mv == "G": path.append(("gap", i - 1, None)); i -= 1
        else: path.append(("skip", None, j - 1)); j -= 1
    return path[::-1]

def auto_match(sim):
    """DP matches, then Case A for DP gaps: best source line at/after the matched position of the
    nearest DP-matched dub line at or before i-2 (near 1-1 window), accepted if score >= 0.5."""
    path = align(sim)
    auto = {i: dict(src=j, score=float(sim[i][j]), kind="DP match") for k, i, j in path if k == "match"}
    for k, i, _ in path:
        if k != "gap":
            continue
        lo = 0
        for p in range(i - 2, -1, -1):
            if p in auto and auto[p]["kind"] == "DP match":
                lo = auto[p]["src"]; break
        best = max(range(lo, len(sim[i])), key=lambda j: sim[i][j])
        if sim[i][best] >= 0.5:
            auto[i] = dict(src=best, score=float(sim[i][best]), kind="Case A reuse")
    return auto

# ---------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------
def fmt(ms):
    ms = int(round(ms)); return f"{ms // 60000:02d}:{(ms % 60000) / 1000:06.3f}"

def probe_duration_ms(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return int(round(float(out) * 1000))

def probe_video(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    w, h, r = out.strip().split(",")[:3]; a, b = r.split("/")
    return int(w), int(h), round(int(a) / int(b))

def load(work, name):
    return json.load(open(os.path.join(work, name), encoding="utf-8"))

def save(work, name, obj):
    json.dump(obj, open(os.path.join(work, name), "w", encoding="utf-8"), indent=1, ensure_ascii=False)

def cfg_get(args):
    cfg = load(args.work, "config.json")
    for k, v in vars(args).items():
        if v is not None and k in cfg:
            cfg[k] = v
    return cfg

def active_manifest(work, cfg, name=None):
    """The edit list that render/verify/bookends use: manifest.json (one clip per row) or, after `groups`,
    manifest_groups.json (continuous 7-8-row groups). Returns (name, manifest dict)."""
    name = name or cfg.get("manifest", "manifest.json")
    if not os.path.exists(os.path.join(work, name)):
        raise SystemExit(f"{name} not found in {work} -- run `manifest` (and `groups`) first")
    return name, load(work, name)

def manifest_tag(name):
    """'manifest.json' -> '' ; 'manifest_groups.json' -> '_groups' (names the segment cache and default render)."""
    stem = os.path.splitext(os.path.basename(name))[0]
    return "" if stem == "manifest" else "_" + stem.replace("manifest_", "")

# ---------------------------------------------------------------------------------
# 3. analyze
# ---------------------------------------------------------------------------------
def cmd_analyze(a):
    os.makedirs(a.work, exist_ok=True)
    cfg = dict(dub_srt=a.dub_srt, dub_audio=a.dub_audio, src_srt=a.src_srt, src_video=a.src_video,
               models=a.models or os.path.expanduser("~/.cache/dubstitch_models"))
    for k in ("dub_srt", "dub_audio", "src_srt", "src_video"):
        if not cfg[k] or not os.path.exists(cfg[k]):
            raise SystemExit(f"--{k.replace('_', '-')} missing or not found: {cfg[k]}")
    save(a.work, "config.json", cfg)   # (re)starting: sheet/manifest/render/verify must be re-run after this
    for stale in ("rows.json", "manifest.json"):
        if os.path.exists(os.path.join(a.work, stale)): os.remove(os.path.join(a.work, stale))
    L = []; say = L.append
    src_off_ms = check_timing(cfg, a, say)
    save(a.work, "config.json", cfg)
    sides = {}
    for side, path in (("dub", cfg["dub_srt"]), ("src", cfg["src_srt"])):
        raw = shift_cues(parse_srt(path), src_off_ms if side == "src" else 0)
        res = resolve_overlaps(resegment_to_sentences(raw))
        kept, dropped = drop_single_word_zero_duration(res)
        final, retimed = retime_zero_duration(kept)
        wt = os.path.join(a.work, f"sentences_{side}.json")       # Step 1A: exact times from the audio, if made
        if os.path.exists(wt) and not a.no_word_times:
            WT = load(a.work, f"sentences_{side}.json")
            if os.path.abspath(WT["srt"]) == os.path.abspath(path) and not WT.get("first"):
                final = [Cue(i, r["start_ms"], r["end_ms"], r["text"]) for i, r in enumerate(WT["sentences"]) if r["end_ms"] > r["start_ms"]]
                dropped, retimed = [], []
                say(f"{side.upper()}: using word-timed sentences from {os.path.basename(wt)} ({len(final)} sentences, times from the audio)")
            else:
                say(f"{side.upper()}: {os.path.basename(wt)} is for another SRT or only a test range -- not used")
        sides[side] = final
        say(f"{side.upper()}: raw cues={len(raw)} (0ms in raw file={sum(c.start_ms == c.end_ms for c in raw)}) -> "
            f"sentences={len(res)} (0ms after overlap resolution={sum(c.start_ms == c.end_ms for c in res)}) -> "
            f"dropped single-word 0ms={len(dropped)} -> re-timed multi-word 0ms={len(retimed)} -> rows={len(final)}")
        for c in dropped: say(f"     DROPPED  {c.text!r}")
        for c in retimed: say(f"     RE-TIMED {c.text[:55]!r} -> {fmt(c.start_ms)}-{fmt(c.end_ms)}")
    dub, src = sides["dub"], sides["src"]
    sim = similarity([c.text for c in dub], [c.text for c in src], cfg["models"])
    auto = auto_match(sim)
    save(a.work, "prep.json", dict(dub=[asdict(c) for c in dub], src=[asdict(c) for c in src],
                                   sim=[[round(x, 5) for x in r] for r in sim],
                                   auto={str(k): v for k, v in auto.items()}))
    def srcline(j): return f"SRC#{j} [{fmt(src[j].start_ms)}] {src[j].text!r}"
    say(f"\n==== ALL {len(dub)} DUB ROWS (automatic result) ====")
    to_read = []
    for i, c in enumerate(dub):
        m = auto.get(i)
        say(f"DUB#{i:3d} [{fmt(c.start_ms)}-{fmt(c.end_ms)}] {c.text!r}")
        if m:
            say(f"        -> {srcline(m['src'])}  ({m['kind']} {m['score']:.3f})")
            if m["kind"] == "Case A reuse":
                to_read.append(i)
        else:
            top = sorted(range(len(src)), key=lambda j: -sim[i][j])[:3]
            say("        -> ** UNMATCHED **  top: " + " | ".join(f"SRC#{j} {sim[i][j]:.2f} {src[j].text[:40]!r}" for j in top))
            to_read.append(i)
    by = defaultdict(list)
    for i, m in auto.items(): by[m["src"]].append(i)
    n_dp = sum(m["kind"] == "DP match" for m in auto.values())
    say(f"\nDP matched {n_dp}/{len(dub)} dub rows ({100 * n_dp / len(dub):.0f}%)"
        + ("  ** VERY LOW -- check the two inputs are really the same episode **" if n_dp < 0.5 * len(dub) else ""))
    say("\n==== DUPLICATE CHECK: source lines claimed by more than one dub row ====")
    for j, owners in sorted(by.items()):
        if len(owners) > 1:
            say(srcline(j))
            for i in sorted(owners): say(f"     DUB#{i} {dub[i].text!r}  ({auto[i]['kind']} {auto[i]['score']:.3f})")
    say("\n==== ROWS YOU MUST READ (unmatched or Case A) with their neighbours ====")
    for i in to_read:
        say(f"-- DUB#{i} {dub[i].text!r}")
        for n in (i - 2, i - 1, i + 1, i + 2):
            if 0 <= n < len(dub):
                m = auto.get(n)
                say(f"     n{n - i:+d} DUB#{n} {dub[n].text[:60]!r} -> " + (srcline(m["src"]) if m else "(unmatched)"))
    say("\n==== DP MATCHES WORTH A SECOND LOOK (speed would be >1.6x slow or >2x fast, or a neighbouring source line is unused) ====")
    for i, m in sorted(auto.items()):
        if m["kind"] != "DP match":
            continue
        j = m["src"]; f = (dub[i].end_ms - dub[i].start_ms) / max(src[j].end_ms - src[j].start_ms, 1)
        free = [k for k in (j - 1, j + 1) if 0 <= k < len(src) and k not in by]
        if f > 1.6 or f < 0.5 or free:
            why = (f"speed {f:.2f}" if f > 1.6 or f < 0.5 else "") + (f" unused neighbour SRC#{free}" if free else "")
            say(f"   DUB#{i} {dub[i].text[:50]!r} -> SRC#{j} {src[j].text[:45]!r}  ({why.strip()})")
    say("\n==== source lines no dub row uses yet ====")
    for j in range(len(src)):
        if j not in by: say(srcline(j))
    say("\n==== possible repeated passages in the dub (>=4-word sentences, similarity >= 0.8) ====")
    norm = lambda t: re.sub(r"[^\w ]", "", t.lower()).strip()
    long_rows = [(i, c) for i, c in enumerate(dub) if len(c.text.split()) >= 4]
    for x in range(len(long_rows)):
        for y in range(x + 1, len(long_rows)):
            (i, ci), (k, ck) = long_rows[x], long_rows[y]
            na, nb = norm(ci.text), norm(ck.text)
            r = difflib.SequenceMatcher(None, na, nb).ratio()
            if r >= 0.8 or (len(na) > 25 and na in nb) or (len(nb) > 25 and nb in na):
                say(f"   DUB#{i} [{fmt(ci.start_ms)}] ~ DUB#{k} [{fmt(ck.start_ms)}]  ({r:.2f}) {ci.text[:60]!r}")
    say("\n==== SOURCE TIMELINE ====")
    for j, c in enumerate(src): say(f"SRC#{j:3d} [{fmt(c.start_ms)}-{fmt(c.end_ms)}] {c.text!r}")
    open(os.path.join(a.work, "analysis.txt"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    dpath = os.path.join(a.work, "decisions.json")
    if not os.path.exists(dpath):
        save(a.work, "decisions.json", dict(groups=[], assign={}, confirm={}, repeat={}, gap={}, unused_notes={}))
    print("\n".join(L[:40])); print(f"... full report: {os.path.join(a.work, 'analysis.txt')}")
    print(f"{len(to_read)} rows must be read by meaning (unmatched or Case A): {to_read}")

# ---------------------------------------------------------------------------------
# 4. sheet: apply decisions + integrity check + xlsx
# ---------------------------------------------------------------------------------
def build_rows(work, decisions_path):
    P = load(work, "prep.json")
    dub = [Cue(**c) for c in P["dub"]]; src = [Cue(**c) for c in P["src"]]
    sim = P["sim"]; auto = {int(k): v for k, v in P["auto"].items()}
    d = json.load(open(decisions_path, encoding="utf-8"))
    span = lambda v: (v, v) if isinstance(v, int) else (int(v[0]), int(v[1]))
    stext = lambda a, b: " ".join(src[j].text for j in range(a, b + 1))
    groups = d.get("groups", [])
    assign = {int(k): v for k, v in d.get("assign", {}).items()}
    confirm = {int(k): v for k, v in d.get("confirm", {}).items()}
    repeat = {int(k): int(v) for k, v in d.get("repeat", {}).items()}
    gap = {int(k): v for k, v in d.get("gap", {}).items()}
    errs = []
    for g in groups:   # guards against index drift: the text must be what was read
        a0 = span(g["src"])[0]
        if not src[a0].text.startswith(g["src_prefix"]): errs.append(f"group {g['members']}: SRC#{a0} is {src[a0].text!r}")
        for mbr, pre in zip(g["members"], g["dub_prefixes"]):
            if not dub[mbr].text.startswith(pre): errs.append(f"group member DUB#{mbr} is {dub[mbr].text!r}, not {pre!r}")
        if g["members"] != sorted(g["members"]): errs.append(f"group {g['members']} must be in dub order")
    for i, v in assign.items():
        if not src[span(v["src"])[0]].text.startswith(v["src_prefix"]): errs.append(f"assign DUB#{i}: src text mismatch")
        if not dub[i].text.startswith(v["dub_prefix"]): errs.append(f"assign DUB#{i}: dub text is {dub[i].text!r}")
    nrm = lambda t: re.sub(r"\s+", " ", re.sub(r"[^\w ]", "", t.lower())).strip()
    for i, s in repeat.items():
        if s >= i or nrm(dub[i].text) not in nrm(dub[s].text):
            errs.append(f"repeat DUB#{i} -> DUB#{s}: not an earlier row with the same text")
    group_of = {}
    for g in groups:
        mem = g["members"]; a0, b0 = span(g["src"])
        durs = [dub[m].end_ms - dub[m].start_ms for m in mem]
        tot = max(sum(durs), 1); cur = float(src[a0].start_ms); win = src[b0].end_ms - src[a0].start_ms
        for m, du in zip(mem, durs):
            s = cur; cur += win * du / tot
            group_of[m] = dict(members=mem, a=a0, b=b0, start=s, end=cur, reason=g["reason"])
        group_of[mem[-1]]["end"] = src[b0].end_ms
    rows = []
    for i, c in enumerate(dub):
        base = dict(idx=i, dub_start=c.start_ms, dub_end=c.end_ms, dub_text=c.text)
        if i in gap:
            best = max(range(len(src)), key=lambda j: sim[i][j])
            rows.append({**base, "src": None, "src_last": None, "src_start": None, "src_end": None, "src_text": None, "score": None,
                         "notes": f"NOT MATCHED -- genuine gap after the full n-1/n+1 check. Closest: SRC#{best} {src[best].text!r} "
                                  f"(score {sim[i][best]:.3f}) -- rejected: {gap[i]}", "flag": "red"})
        elif i in repeat:
            r0 = rows[repeat[i]]
            rows.append({**base, **{k: r0[k] for k in ("src", "src_last", "src_start", "src_end", "src_text")},
                         "score": round(sim[i][r0["src"]], 3) if r0["src"] is not None else None,
                         "notes": f"Dub repeat: the dub repeats DUB#{repeat[i]} here; the source has it once, so this row reuses that footage.",
                         "flag": "green"})
        elif i in group_of:
            g = group_of[i]
            rows.append({**base, "src": g["a"], "src_last": g["b"], "src_start": g["start"], "src_end": g["end"],
                         "src_text": stext(g["a"], g["b"]), "score": round(max(sim[i][j] for j in range(g["a"], g["b"] + 1)), 3),
                         "notes": f"Shared sentence ({len(g['members'])}-way) with DUB#{[m for m in g['members'] if m != i]}, "
                                  f"window split by dub duration. {g['reason']}", "flag": "green"})
        elif i in assign:
            a0, b0 = span(assign[i]["src"])
            rows.append({**base, "src": a0, "src_last": b0, "src_start": src[a0].start_ms, "src_end": src[b0].end_ms,
                         "src_text": stext(a0, b0), "score": round(max(sim[i][j] for j in range(a0, b0 + 1)), 3),
                         "notes": f"Manually verified match. {assign[i]['reason']}", "flag": "green"})
        elif i in auto:
            m = auto[i]; j = m["src"]
            if m["kind"] == "Case A reuse" and i not in confirm:
                errs.append(f"DUB#{i} is a Case A fallback ({m['score']:.3f} -> SRC#{j}): read it and put it in confirm/assign/groups/gap")
            note = m["kind"] + (f" (score {m['score']:.3f}) -- read and confirmed: {confirm[i]}" if i in confirm else "")
            rows.append({**base, "src": j, "src_last": j, "src_start": src[j].start_ms, "src_end": src[j].end_ms,
                         "src_text": src[j].text, "score": round(m["score"], 3), "notes": note, "flag": None})
        else:
            errs.append(f"DUB#{i} is unmatched and has no decision -- read its n-1/n+1 neighbours")
            rows.append({**base, "src": None, "src_last": None, "src_start": None, "src_end": None, "src_text": None,
                         "score": None, "notes": "", "flag": "red"})
    if errs:
        raise SystemExit("decisions incomplete or inconsistent:\n  " + "\n  ".join(errs))
    return rows, src, repeat, d

def mapping_checks(rows, repeat):
    """Integrity check: no 0ms window either side; no two rows share footage except documented dub repeats."""
    allowed = {frozenset((i, s)) for i, s in repeat.items()}
    zero = [r["idx"] for r in rows if r["dub_end"] <= r["dub_start"] or (r["src_start"] is not None and r["src_end"] <= r["src_start"])]
    m = [r for r in rows if r["src_start"] is not None]
    ov = []
    for x in range(len(m)):
        for y in range(x + 1, len(m)):
            o = min(m[x]["src_end"], m[y]["src_end"]) - max(m[x]["src_start"], m[y]["src_start"])
            if o > 1 and frozenset((m[x]["idx"], m[y]["idx"])) not in allowed:
                ov.append((m[x]["idx"], m[y]["idx"], round(o)))
    return zero, ov

def cmd_sheet(a):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    cfg = cfg_get(a)
    rows, src, repeat, d = build_rows(a.work, a.decisions or os.path.join(a.work, "decisions.json"))
    zero, ov = mapping_checks(rows, repeat)
    print(f"0ms rows: {zero}\nrow pairs sharing footage (outside documented repeats): {ov}")
    if zero or ov:
        raise SystemExit("integrity check failed -- fix decisions.json (usually a group missing a member, a group on the wrong sentence, or an unresolved duplicate claim)")
    used = set()
    for r in rows:
        if r["src"] is not None: used.update(range(r["src"], r["src_last"] + 1))
    unused = [j for j in range(len(src)) if j not in used]
    notes = {int(k): v for k, v in d.get("unused_notes", {}).items()}
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Mapping"
    hf = Font(name="Arial", size=11, bold=True, color="FFFFFF"); hfill = PatternFill("solid", start_color="1F4E78")
    bf = Font(name="Arial", size=10); wrap = Alignment(wrap_text=True, vertical="top")
    fills = {"green": PatternFill("solid", start_color="C6EFCE"), "yellow": PatternFill("solid", start_color="FFEB9C"),
             "red": PatternFill("solid", start_color="FFC7CE")}
    L = a.labels.split(",")
    hdr = ["#", f"{L[0]} Start", f"{L[0]} End", f"{L[0]} Text", f"{L[1]} Start", f"{L[1]} End", f"{L[1]} Text",
           "Match Score", "Stretch Factor", "Notes"]
    for ci, h in enumerate(hdr, 1):
        cell = ws.cell(1, ci, h); cell.font = hf; cell.fill = hfill
    for ri, r in enumerate(rows, 2):
        has = r["src_start"] is not None
        vals = [r["idx"], fmt(r["dub_start"]), fmt(r["dub_end"]), r["dub_text"],
                fmt(r["src_start"]) if has else "", fmt(r["src_end"]) if has else "", r["src_text"] or "",
                r["score"] if r["score"] is not None else "",
                round((r["dub_end"] - r["dub_start"]) / (r["src_end"] - r["src_start"]), 2) if has else "", r["notes"]]
        for ci, v in enumerate(vals, 1):
            cell = ws.cell(ri, ci, v); cell.font = bf; cell.alignment = wrap
            if r["flag"]: cell.fill = fills[r["flag"]]
    for ci, w in enumerate([6, 11, 11, 42, 11, 11, 42, 12, 13, 60], 1):
        ws.column_dimensions[chr(64 + ci)].width = w
    ws.freeze_panes = "A2"
    ws2 = wb.create_sheet(f"Unused {L[1]} lines")
    for ci, h in enumerate(["#", "Start", "End", f"{L[1]} Text", "Why unused"], 1):
        cell = ws2.cell(1, ci, h); cell.font = hf; cell.fill = hfill
    for ri, j in enumerate(unused, 2):
        for ci, v in enumerate([j, fmt(src[j].start_ms), fmt(src[j].end_ms), src[j].text, notes.get(j, "")], 1):
            cell = ws2.cell(ri, ci, v); cell.font = bf; cell.alignment = wrap
    for ci, w in enumerate([6, 11, 11, 60, 60], 1):
        ws2.column_dimensions[chr(64 + ci)].width = w
    out = a.out
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True); wb.save(out)
    save(a.work, "rows.json", dict(rows=rows, repeat={str(k): v for k, v in repeat.items()}))
    cfg["sheet_out"] = os.path.abspath(out); cfg["labels"] = a.labels; save(a.work, "config.json", cfg)
    n_gap = sum(r["flag"] == "red" for r in rows)
    print(f"saved {out}: rows={len(rows)} matched={len(rows) - n_gap} gaps={n_gap}")
    print(f"unused source lines: {[(j, src[j].text[:45]) for j in unused]}")
    missing = [j for j in unused if j not in notes]
    if missing: print(f"!! add an unused_notes entry explaining source lines {missing}")

# ---------------------------------------------------------------------------------
# 5. manifest: silence fill + absorb (zero black), bleed fix (fixed), gap fill; first clip starts at its own matched line
# ---------------------------------------------------------------------------------
SCENE_THRESHOLD = 0.15

def detect_cuts(video, start_ms, end_ms):
    if end_ms <= start_ms:
        return []
    r = subprocess.run(["ffmpeg", "-loglevel", "info", "-ss", f"{start_ms / 1000:.3f}", "-t", f"{(end_ms - start_ms) / 1000:.3f}",
                        "-i", video, "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"],
                       capture_output=True, text=True)
    cuts = [start_ms + float(x) * 1000 for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    # FIX (2026-09): showinfo can report frames past the window; only cuts inside it are valid.
    return sorted(c for c in cuts if start_ms <= c < end_ms)

def fix_stretch_bleed(clips, video, src_total_ms, max_stretch=1.6, review_cap=2.2):
    """Bleed fix: for clips slowed down more than max_stretch, trim an edge that crosses a scene cut
    (within min(1500ms, 35%) of that edge), then extend outward -- but only inside the SAME shot and
    never past the neighbouring clips' footage. Never keep a result worse than the original."""
    report = []
    for s in clips:
        dub_dur = s["it_end_ms"] - s["it_start_ms"]; src_dur = s["us_end_ms"] - s["us_start_ms"]
        factor = dub_dur / max(src_dur, 1)
        if factor <= max_stretch:
            continue
        others = [(c["us_start_ms"], c["us_end_ms"]) for c in clips if c is not s]
        prev_end = max([b for a_, b in others if b <= s["us_start_ms"]], default=0)
        next_start = min([a_ for a_, b in others if a_ >= s["us_end_ms"]], default=src_total_ms)
        cuts = detect_cuts(video, prev_end, next_start)
        o0, o1 = s["us_start_ms"], s["us_end_ms"]; margin = min(1500, 0.35 * (o1 - o0))
        n0, n1 = o0, o1
        ends = [c for c in cuts if o1 - margin <= c < o1]
        if ends: n1 = min(ends)
        starts = [c for c in cuts if o0 < c <= o0 + margin]
        if starts: n0 = max(starts)
        lo = max(max([c for c in cuts if c <= n0], default=prev_end), prev_end)
        hi = min(min([c for c in cuts if c >= n1], default=next_start), next_start)
        need = dub_dur / max_stretch - (n1 - n0)
        if need > 0:
            f = min(max(0, hi - n1), need); n1 += f; need -= f
            if need > 0:
                b = min(max(0, n0 - lo), need); n0 -= b
        nf_raw = dub_dur / max(n1 - n0, 1)
        moved = (n0, n1) != (o0, o1)
        n0, n1, nf = (o0, o1, factor) if nf_raw > factor else (n0, n1, nf_raw)
        s["us_start_ms"], s["us_end_ms"] = n0, n1
        review = nf > review_cap or (moved and nf_raw > factor)
        report.append(dict(idx=s["it_idx"], before=round(factor, 3), after=round(nf, 3), changed=(n0, n1) != (o0, o1),
                           needs_review=bool(review), text=s.get("it_text", "")[:60]))
    return report

def cmd_manifest(a):
    cfg = cfg_get(a)
    R = load(a.work, "rows.json"); rows = R["rows"]; repeat = {int(k): v for k, v in R["repeat"].items()}
    total_ms = probe_duration_ms(cfg["dub_audio"]); src_total = probe_duration_ms(cfg["src_video"])
    ev = []
    for r in rows:
        if r["src_start"] is None:
            continue  # genuine gaps become silence here and are absorbed below
        ev.append(dict(kind="clip", it_idx=r["idx"], it_start_ms=r["dub_start"], it_end_ms=r["dub_end"],
                       us_start_ms=r["src_start"], us_end_ms=r["src_end"], it_text=r["dub_text"], us_text=r["src_text"]))
    ev.sort(key=lambda e: e["it_start_ms"])
    full, cur = [], 0
    for e in ev:
        if e["it_start_ms"] < cur - 1: raise SystemExit(f"dub timeline overlap at DUB#{e['it_idx']}")
        if e["it_start_ms"] > cur: full.append(dict(kind="silence", it_start_ms=cur, it_end_ms=e["it_start_ms"]))
        full.append(e); cur = e["it_end_ms"]
    if cur < total_ms: full.append(dict(kind="silence", it_start_ms=cur, it_end_ms=total_ms))
    if cur > total_ms: raise SystemExit(f"dub SRT runs past the dub audio ({cur} > {total_ms} ms)")
    # Every silence is absorbed by the neighbouring clip(s) -> zero black screen.
    # (<=1.5s: whole sliver to the previous clip, else next; longer: split 50/50 between both.)
    for e in ev:
        e["orig"] = [e["it_start_ms"], e["it_end_ms"], e["us_start_ms"], e["us_end_ms"]]
    segs = []
    i = 0
    while i < len(full):
        s = full[i]
        if s["kind"] != "silence":
            segs.append(s); i += 1; continue
        gap = s["it_end_ms"] - s["it_start_ms"]
        prev = segs[-1] if segs and segs[-1]["kind"] == "clip" else None
        nxt = full[i + 1] if i + 1 < len(full) and full[i + 1]["kind"] == "clip" else None
        if prev and nxt and gap > 1500:
            half = gap // 2; prev["it_end_ms"] += half; nxt["it_start_ms"] -= gap - half
        elif prev:
            prev["it_end_ms"] = s["it_end_ms"]
        elif nxt:
            nxt["it_start_ms"] = s["it_start_ms"]
        else:
            raise SystemExit("no clip at all to absorb silence")
        i += 1
    for x, y in zip(segs, segs[1:]):
        assert x["it_end_ms"] == y["it_start_ms"], "timeline not contiguous"
    assert segs[0]["it_start_ms"] == 0 and segs[-1]["it_end_ms"] == total_ms
    rep = fix_stretch_bleed(segs, cfg["src_video"], src_total)
    # NO first-clip rule: the first clip starts at its own matched source line, like every other clip. It absorbs the
    # dub's leading silence by slowing down (bleed fix keeps it inside its own shot). Reaching back to source 0 pulled in
    # an unmatched extra scene (ep81/82), so it was removed (2026-10).
    # Gap fill: two clips that follow each other in the source with a small unused gap between them (<= --fill-gaps,
    # default 1 s) are joined, so the picture runs on continuously instead of jumping over a fraction of a second.
    # The gap is split at a scene cut inside it if there is one (each clip keeps its own shot), else at the midpoint.
    # Both clips play slightly faster. Skipped if any other clip uses that footage, or for documented dub repeats.
    filled = []
    if a.fill_gaps > 0:
        rep_rows = set(repeat) | set(repeat.values())
        for x, y in zip(segs, segs[1:]):
            g0, g1 = x["us_end_ms"], y["us_start_ms"]
            if not (0 < g1 - g0 <= a.fill_gaps) or x["it_idx"] in rep_rows or y["it_idx"] in rep_rows:
                continue
            if any(c is not x and c is not y and c["us_start_ms"] < g1 and c["us_end_ms"] > g0 for c in segs):
                continue
            cuts = detect_cuts(cfg["src_video"], g0, g1)
            m = cuts[0] if cuts else (g0 + g1) / 2
            fx = (x["it_end_ms"] - x["it_start_ms"]) / (x["us_end_ms"] - x["us_start_ms"])
            fy = (y["it_end_ms"] - y["it_start_ms"]) / (y["us_end_ms"] - y["us_start_ms"])
            x["us_end_ms"] = m; y["us_start_ms"] = m; x["gapfill"] = y["gapfill"] = True
            filled.append((x["it_idx"], y["it_idx"], round(g1 - g0), "cut" if cuts else "mid", round(fx, 3), round(fy, 3)))
    # final checks
    allowed = {frozenset((i, s)) for i, s in repeat.items()}
    ov = [(x["it_idx"], y["it_idx"]) for k, x in enumerate(segs) for y in segs[k + 1:]
          if min(x["us_end_ms"], y["us_end_ms"]) - max(x["us_start_ms"], y["us_start_ms"]) > 1
          and frozenset((x["it_idx"], y["it_idx"])) not in allowed]
    if ov: raise SystemExit(f"clips share footage after the bleed fix: {ov}")
    for s in segs:
        s["stretch_factor"] = round((s["it_end_ms"] - s["it_start_ms"]) / (s["us_end_ms"] - s["us_start_ms"]), 3)
    save(a.work, "manifest.json", dict(total_ms=total_ms, segments=segs, bleed_report=rep))
    cfg["manifest"] = "manifest.json"; save(a.work, "config.json", cfg)   # `groups` switches this to manifest_groups.json
    if cfg.get("sheet_out") and os.path.exists(cfg["sheet_out"]):
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = openpyxl.load_workbook(cfg["sheet_out"])
        if "Video edit list" in wb.sheetnames: del wb["Video edit list"]
        ws = wb.create_sheet("Video edit list")
        L = cfg.get("labels", "IT,US").split(",")
        hdr = ["#", f"{L[0]} Start", f"{L[0]} End", f"{L[1]} Start", f"{L[1]} End", "Speed factor", "Changed from Mapping tab by"]
        hf = Font(name="Arial", size=11, bold=True, color="FFFFFF"); hfill = PatternFill("solid", start_color="1F4E78")
        for ci, h in enumerate(hdr, 1):
            c_ = ws.cell(1, ci, h); c_.font = hf; c_.fill = hfill
        fixed = {r["idx"] for r in rep if r["changed"]}
        for ri, sg in enumerate(segs, 2):
            o = sg["orig"]; why = []
            if [sg["it_start_ms"], sg["it_end_ms"]] != o[:2]: why.append("absorbed neighbouring silence (no black screen)")
            if sg["it_idx"] in fixed: why.append("bleed fix (kept inside its own shot)")
            if sg.get("gapfill"): why.append("joined to the neighbouring clip (small source gap filled)")
            vals = [sg["it_idx"], fmt(sg["it_start_ms"]), fmt(sg["it_end_ms"]), fmt(sg["us_start_ms"]), fmt(sg["us_end_ms"]),
                    sg["stretch_factor"], "; ".join(why)]
            for ci, v in enumerate(vals, 1):
                c_ = ws.cell(ri, ci, v); c_.font = Font(name="Arial", size=10); c_.alignment = Alignment(wrap_text=True, vertical="top")
        for ci, w in enumerate([6, 11, 11, 11, 11, 12, 60], 1):
            ws.column_dimensions[chr(64 + ci)].width = w
        wb.save(cfg["sheet_out"]); print(f"added 'Video edit list' tab (final timings as played) to {cfg['sheet_out']}")
    print(f"manifest: {len(segs)} clips covering 0-{total_ms / 1000:.2f}s, 0 black, 0 shared footage")
    for r in rep:
        print(f"  bleed fix DUB#{r['idx']}: {r['before']}x -> {r['after']}x" + ("  NEEDS VISUAL REVIEW" if r["needs_review"] else ""))
    if filled:
        sf = {s["it_idx"]: s["stretch_factor"] for s in segs}
        print(f"gap fill: joined {len(filled)} pairs of clips across source gaps <= {a.fill_gaps / 1000:g}s "
              f"({sum(f[2] for f in filled) / 1000:.1f}s of extra footage used):")
        for xi, yi, g, how, fx, fy in filled:
            print(f"  DUB#{xi}|DUB#{yi}: gap {g}ms split at {'a scene cut' if how == 'cut' else 'the midpoint'}; "
                  f"stretch {fx}->{sf[xi]} and {fy}->{sf[yi]}")
    slow = [(s["it_idx"], s["stretch_factor"]) for s in segs if s["stretch_factor"] > 1.6]
    fast = [(s["it_idx"], s["stretch_factor"]) for s in segs if s["stretch_factor"] < 0.5]
    print(f"slow-motion clips (>1.6x slower): {slow}\nfast clips (>2x faster): {fast}")

# ---------------------------------------------------------------------------------
# 6. render: frame-exact on a cumulative grid (no drift), resumable
# ---------------------------------------------------------------------------------
def detect_subtitle_band(video, sample_every_s=2, white_thresh=200, width_frac=0.5):
    import numpy as np
    from PIL import Image
    w, h, _ = probe_video(video)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", f"fps=1/{sample_every_s},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w)
    vals = []
    for im in frames:
        rows = np.where((im[int(h * 0.4):] > white_thresh).sum(axis=1) > w * width_frac)[0]
        if len(rows): vals.append(int(h * 0.4) + int(rows[0]))
    if len(vals) < 10: return None
    arr = np.array(vals); close = arr[np.abs(arr - np.median(arr)) < 40]
    if len(close) < 5 or close.std() > 15: return None
    return max(int(close.min()) - 30, 0)

def cmd_render(a):
    cfg = cfg_get(a)
    mname, M = active_manifest(a.work, cfg, a.manifest); segs = M["segments"]; tag = manifest_tag(mname)
    a.out = a.out or os.path.join(a.work, f"video{tag}_nosubs.mp4")
    print(f"rendering {mname}: {len(segs)} segments")
    W, H, FPS = probe_video(cfg["src_video"])
    crop_h = None
    if a.subs == "crop":
        cache = os.path.join(a.work, "crop.json")
        crop_h = load(a.work, "crop.json")["crop_h"] if os.path.exists(cache) else detect_subtitle_band(cfg["src_video"])
        save(a.work, "crop.json", dict(crop_h=crop_h)); print(f"subtitle crop: {crop_h}")
    d = os.path.join(a.work, "segs" + tag); os.makedirs(d, exist_ok=True)
    t_end = time.time() + a.max_seconds
    def job(i):
        s = segs[i]; out = os.path.join(d, f"{i:04d}.mp4")
        f0 = round(s["it_start_ms"] * FPS / 1000); f1 = round(s["it_end_ms"] * FPS / 1000); nf = max(f1 - f0, 1)
        sig = f"{s['us_start_ms']:.3f}|{s['us_end_ms']:.3f}|{nf}|{crop_h}|{W}x{H}@{FPS}"
        if (os.path.exists(out + ".ok") and open(out + ".ok").read() == sig) or time.time() > t_end:
            return
        k = (nf / FPS) / max((s["us_end_ms"] - s["us_start_ms"]) / 1000, 0.04)
        frame = (f"crop={W}:{crop_h}:0:0,pad={W}:{H}:0:0:black" if crop_h else
                 f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2")
        vf = f"setpts={k:.6f}*PTS,{frame},fps={FPS},tpad=stop_mode=clone:stop_duration=2"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{s['us_start_ms'] / 1000:.3f}",
                            "-to", f"{s['us_end_ms'] / 1000:.3f}", "-i", cfg["src_video"], "-vf", vf, "-frames:v", str(nf),
                            "-r", str(FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-an", "-movflags", "+faststart", out], capture_output=True, text=True)
        if r.returncode: print(f"FAILED segment {i}: {r.stderr[-300:]}")
        else: open(out + ".ok", "w").write(sig)
    with ThreadPoolExecutor(a.workers) as ex: list(ex.map(job, range(len(segs))))
    def is_done(i):
        s = segs[i]; ok = os.path.join(d, f"{i:04d}.mp4.ok")
        nf = max(round(s["it_end_ms"] * FPS / 1000) - round(s["it_start_ms"] * FPS / 1000), 1)
        return os.path.exists(ok) and open(ok).read() == f"{s['us_start_ms']:.3f}|{s['us_end_ms']:.3f}|{nf}|{crop_h}|{W}x{H}@{FPS}"
    done = sum(is_done(i) for i in range(len(segs)))
    print(f"rendered {done}/{len(segs)} segments")
    if done < len(segs):
        print("not finished -- run the same render command again (it resumes)"); sys.exit(3)
    lst = os.path.join(a.work, f"concat{tag}.txt")
    open(lst, "w").write("".join(f"file '{os.path.abspath(os.path.join(d, f'{i:04d}.mp4'))}'\n" for i in range(len(segs))))
    silent = os.path.join(a.work, f"silent{tag}.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent], check=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", silent, "-i", cfg["dub_audio"], "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", a.out], check=True)
    cfg["video_out"] = os.path.abspath(a.out); cfg["video_manifest"] = mname; save(a.work, "config.json", cfg)
    print(f"saved {a.out}: video {probe_duration_ms(silent) / 1000:.3f}s, dub audio {probe_duration_ms(cfg['dub_audio']) / 1000:.3f}s")

# ---------------------------------------------------------------------------------
# 7. verify: every row shows its own footage; durations match; QC contact sheet
# ---------------------------------------------------------------------------------
def cmd_verify(a):
    import numpy as np
    from PIL import Image, ImageDraw
    cfg = load(a.work, "config.json"); final = cfg.get("video_out")
    if not final or not os.path.exists(final):
        raise SystemExit("no rendered video recorded in this work folder -- run `render` first")
    mname, M = active_manifest(a.work, cfg, cfg.get("video_manifest")); segs = M["segments"]
    print(f"checking {final} against {mname} ({len(segs)} segments)")
    _, _, FPS = probe_video(cfg["src_video"])
    vd, ad = probe_duration_ms(final), probe_duration_ms(cfg["dub_audio"])
    print(f"final video {vd / 1000:.3f}s vs dub audio {ad / 1000:.3f}s (diff {abs(vd - ad)}ms)")
    ok_dur = abs(vd - ad) <= 2 * 1000 / FPS + 50
    def grab(path, t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", path, "-frames:v", "1",
                              "-vf", "scale=64:64,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).astype(float) if len(raw) == 4096 else None
    def corr(x, y):
        return float(np.corrcoef(x, y)[0, 1]) if x is not None and y is not None and x.std() > 0 and y.std() > 0 else 1.0
    def check(s):
        f0 = round(s["it_start_ms"] * FPS / 1000); f1 = round(s["it_end_ms"] * FPS / 1000)
        t = (f0 + (f1 - f0) // 2 + 0.5) / FPS
        ts = (s["us_start_ms"] + (t - f0 / FPS) / ((f1 - f0) / FPS) * (s["us_end_ms"] - s["us_start_ms"])) / 1000
        x = grab(final, t); c = corr(x, grab(cfg["src_video"], ts))
        if c < 0.9:
            c = max(corr(x, grab(cfg["src_video"], ts + k / FPS)) for k in range(-6, 7))
        return s["it_idx"], round(t, 2), round(c, 3)
    with ThreadPoolExecutor(a.workers) as ex: res = list(ex.map(check, segs))
    bad = [r for r in res if r[2] < 0.9]
    print(f"sync check: {len(res)} rows, {len(res) - len(bad)} show their own footage (best corr >= 0.90); failing: {bad}")
    fixed = {r["idx"] for r in M.get("bleed_report", []) if r["changed"] or r["needs_review"]}
    pick = [segs[0]] + [s for s in segs[1:] if s["it_idx"] in fixed or s["stretch_factor"] > 1.6 or s["stretch_factor"] < 0.5][:15]
    sheet = Image.new("RGB", (4 * 220, len(pick) * 250), "white"); dr = ImageDraw.Draw(sheet)
    from PIL import ImageFont
    font = None
    for fp in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
               "/Library/Fonts/Arial.ttf", "C:/Windows/Fonts/arial.ttf"):
        if os.path.exists(fp): font = ImageFont.truetype(fp, 13); break
    for r_, s in enumerate(pick):
        t0, t1 = s["it_start_ms"] / 1000, s["it_end_ms"] / 1000
        dr.text((4, r_ * 250 + 2), f"#{s['it_idx']} {t0:.1f}-{t1:.1f}s factor {s['stretch_factor']}  {s.get('it_text', '')[:60]}", fill="black", font=font)
        for k, p in enumerate((0.08, 0.36, 0.64, 0.92)):
            png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0 + p * (t1 - t0):.3f}", "-i", final, "-frames:v", "1",
                                  "-vf", "scale=216:216", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
            if png: sheet.paste(Image.open(io.BytesIO(png)), (k * 220 + 2, r_ * 250 + 22))
    qc = os.path.join(a.work, "qc_sheet.jpg"); sheet.save(qc, quality=80)
    print(f"QC contact sheet (first clip + bleed-fixed + slow/fast clips): {qc} -- LOOK at it")
    if bad or not ok_dur: sys.exit(1)

# ---------------------------------------------------------------------------------
# 8. subs: burn the dub subtitles onto the rendered video -> the final deliverable
#    White full-width strip over the source's caption area + dub SRT text (Poppins Bold 40,
#    black, centred). Every card at its SRT times, first card from frame 0, text held across short
#    pauses, and an empty strip wherever the render would still show a source caption.
# ---------------------------------------------------------------------------------
FONT_CANDIDATES = ["/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf", "/usr/share/fonts/truetype/poppins/Poppins-Bold.ttf",
                   os.path.expanduser("~/Library/Fonts/Poppins-Bold.ttf"), "/Library/Fonts/Poppins-Bold.ttf",
                   os.path.expanduser("~/.local/share/fonts/Poppins-Bold.ttf"), os.path.expanduser("~/.fonts/Poppins-Bold.ttf"),
                   "C:/Windows/Fonts/Poppins-Bold.ttf"]

def find_font(path, size):
    from PIL import ImageFont
    cands = [path] if path else list(FONT_CANDIDATES)
    if not path and shutil.which("fc-match"):
        cands.append(subprocess.run(["fc-match", "-f", "%{file}", "Poppins:bold"], capture_output=True, text=True).stdout.strip())
    for p in cands:
        if p and os.path.exists(p):
            f = ImageFont.truetype(p, size)
            if path or f.getname() == ("Poppins", "Bold"):
                return p
    raise SystemExit("Poppins Bold not found. Download https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf "
                     "and pass --font /path/to/Poppins-Bold.ttf")

def detect_caption_box(video):
    """Median top/bottom row of the source's burned-in white caption box, or None if it has none."""
    import numpy as np
    W, H, _ = probe_video(video); h2 = H // 2
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", video, "-vf", f"fps=1/2,crop={W}:{H - h2}:0:{h2}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    n = W * (H - h2) * 3; tops, bots = [], []
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        m = (np.frombuffer(buf, np.uint8).reshape(H - h2, W, 3) > 225).all(2)
        rows = np.where(m.sum(1) > W * 0.11)[0]
        if len(rows) < 10:
            continue
        run = max(np.split(rows, np.where(np.diff(rows) > 3)[0] + 1), key=len)
        if len(run) >= 10:
            tops.append(h2 + int(run[0])); bots.append(h2 + int(run[-1]))
    p.wait()
    if len(tops) < 10:
        return None
    box = (int(np.median(tops)), int(np.median(bots)))
    # Burned-in captions sit in the bottom part of the frame. A "box" higher up is bright picture content (a white
    # shirt, a window; seen on TOLR ep24, which has no captions at all): treat it as no caption box.
    return box if box[0] >= 0.70 * H else None

def layout_text(text, font_path, size, maxw):
    """One line if it fits; else the most balanced 2-line split (never one word alone); if even that is
    too wide, the font is reduced 2 px at a time (reported)."""
    from PIL import ImageFont
    words = text.split()
    for s in range(size, 19, -2):
        f = ImageFont.truetype(font_path, s)
        if f.getlength(text) <= maxw or len(words) < 2:
            return f, [text], s
        k = min(range(1, len(words)), key=lambda k: max(f.getlength(" ".join(words[:k])), f.getlength(" ".join(words[k:]))))
        lines = [" ".join(words[:k]), " ".join(words[k:])]
        if max(f.getlength(l) for l in lines) <= maxw:
            return f, lines, s
    return f, lines, s

def caption_frames(video, box, W, FPS):
    """Per frame: does the (render's) picture show the source's burned-in caption box? (white rows in the box band)"""
    import numpy as np
    t, b = box; h = b - t + 1; sh = max(h // 2, 8)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", f"crop={W}:{h}:0:{t},scale=540:{sh},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, sh, 540)
    return ((fr > 235).sum(2) > 540 * 0.11).sum(1) >= 0.3 * sh

def cmd_subs(a):
    import numpy as np
    from PIL import Image, ImageDraw
    cfg = load(a.work, "config.json")
    clean = cfg.get("clean_final") if cfg.get("clean_final") and os.path.exists(cfg["clean_final"]) else None
    video = a.video or clean or cfg.get("video_out")      # default: the clean master from `bookends`
    if not video or not os.path.exists(video):
        raise SystemExit("no video to subtitle -- run `render`, `verify` and `bookends` first (or pass --video)")
    if os.path.abspath(a.out) in {os.path.abspath(p) for p in (video, cfg.get("video_out") or "", clean or "")}:
        raise SystemExit("--out must be a new file, not the clean master or the render")
    srt = a.srt or cfg["dub_srt"]
    W, H, FPS = probe_video(video); total_f = round(probe_duration_ms(video) * FPS / 1000)
    # On the clean master the dub episode starts after the source opening: shift every card by it, and keep the
    # opening and ending free of any strip (they have no dub audio and no subtitles).
    on_master = clean is not None and os.path.abspath(video) == os.path.abspath(clean)
    off = a.offset if a.offset is not None else (cfg.get("opening_s", 0) if on_master else 0)
    if a.style == "box":   # every show except TDMB: transparent full-frame subtitle PNGs (pngsubs.py), see Step 8
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import pngsubs
        wd = os.path.join(a.work, "pngsubs_" + re.sub(r"[^\w.-]", "_", os.path.splitext(os.path.basename(a.out))[0]))
        pngsubs.burn(video, srt, a.out, wd, off, a.chunk, min(a.max_seconds, 10 ** 6), a.font, keep_parts=a.keep_parts)
        cfg = load(a.work, "config.json")
        cfg.setdefault("subs_outputs", {})[os.path.abspath(a.out)] = dict(srt=os.path.abspath(srt), video=os.path.abspath(video),
                                                                          offset_s=off, style="box")
        save(a.work, "config.json", cfg); return
    win0 = round(cfg.get("opening_s", 0) * FPS) if on_master else 0
    win1 = total_f - (round(cfg.get("ending_s", 0) * FPS) if on_master else 0)
    base_size = a.size; a.size = round(base_size * H / 1080)   # --size is for a 1080-px frame; scale to this video
    if W * H > 1920 * 1080 * 0.9 and a.workers > 2:
        a.workers = 2                                           # 1440x1440 chunks were OOM-killed (4 GB machine) with 3+ workers
    font_path = find_font(a.font, a.size)
    cache = os.path.join(a.work, "caption_box.json")
    if os.path.exists(cache) and load(a.work, "caption_box.json").get("video") == cfg["src_video"]:
        cb = load(a.work, "caption_box.json")["box"]
    else:
        cb = detect_caption_box(cfg["src_video"]); save(a.work, "caption_box.json", dict(video=cfg["src_video"], box=cb))
    if a.box:
        y0, y1 = map(int, a.box.split(",")); how = "given with --box"
    else:
        y0, y1 = (cb[0] - 12, cb[1] + 12) if cb else (round(H * 0.79), round(H * 0.93))
        how = f"covers the source caption box (rows {cb[0]}-{cb[1]}) + 12 px" if cb else "no source caption box found: default band"
    need = 2 * int(a.size * 1.25) + 20
    if y1 - y0 < need:
        c = (y0 + y1) // 2; y0, y1 = c - need // 2, c + need - need // 2
    y0, y1 = max(y0, 0), min(y1, H - 1)
    print(f"strip: rows {y0}-{y1}, full width ({how}); font {font_path} size {a.size} px "
          f"(= {base_size} on a 1080-px frame, video is {W}x{H}); {a.workers} workers")
    cues = parse_srt(srt)   # the SRT as delivered (dub, or its translation): every card, in order
    print(f"subtitles: {os.path.basename(srt)} ({len(cues)} cards) on {os.path.basename(video)}, shifted {off:+.3f}s; "
          f"strip allowed {win0 / FPS:.2f}-{win1 / FPS:.2f}s")
    d = os.path.join(a.work, "subs_" + re.sub(r"[^\w.-]", "_", os.path.splitext(os.path.basename(a.out))[0]))
    os.makedirs(d, exist_ok=True)   # one folder per output, so an Italian and an English burn never share card images
    blank = os.path.join(d, "blank.png"); Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    strip_only = os.path.join(d, "strip.png"); im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle([0, y0, W - 1, y1], fill=(255, 255, 255, 255)); im.save(strip_only)
    cards, small = [], []
    for i, c in enumerate(cues):
        f, lines, s = layout_text(c.text, font_path, a.size, W - round(80 * H / 1080))
        if s != a.size: small.append((i, s, c.text))
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
        dr.rectangle([0, y0, W - 1, y1], fill=(255, 255, 255, 255))
        lh = int(s * 1.25); cy = (y0 + y1) / 2 - (len(lines) - 1) * lh / 2
        for k, l in enumerate(lines):
            dr.text((W / 2, cy + k * lh), l, font=f, fill=(0, 0, 0, 255), anchor="mm")
        p = os.path.join(d, f"c{i:04d}.png"); im.save(p)
        cards.append((c.start_ms / 1000 + off, c.end_ms / 1000 + off, p, len(lines)))
    # ---- frame-by-frame plan: state = card index (text), -1 = empty strip, -2 = no strip ----
    n = total_f; state = np.full(n, -2, int)
    span = [(min(max(math.ceil(s * FPS), win0), win1), min(max(math.ceil(e * FPS), win0), win1)) for s, e, _, _ in cards]
    for i, (fa, fb) in enumerate(span):
        state[fa:fb] = i                                   # 1. every card at its exact SRT times
    held = 0
    for i in range(len(span) - 1):                         # 2. continuity: hold the text across short pauses
        fb, na = span[i][1], span[i + 1][0]
        if 0 < na - fb < a.min_gap * FPS:
            state[fb:na] = i; held += 1
    lead = span[0][0] - win0 if span else 0; lead_used = tail_used = False
    if span and 0 < lead <= a.max_lead * FPS:              # 3. first card shown from the first dub frame
        state[win0:span[0][0]] = 0; lead_used = True
    tail = win1 - span[-1][1] if span else 0
    if span and 0 < tail <= a.max_lead * FPS:              # 4. last card held to the last dub frame
        state[span[-1][1]:win1] = len(cards) - 1; tail_used = True
    cap = None; dyn = 0
    if cb and not a.no_cover:                              # 5. anywhere the render still shows a source caption
        vst = os.stat(video); cpath = os.path.join(a.work, "caption_frames.npy")   # cached: the scan is slow and resumes re-run it
        ckey = f"{vst.st_size}|{int(vst.st_mtime)}|{cb}"
        if os.path.exists(cpath) and os.path.exists(cpath + ".key") and open(cpath + ".key").read() == ckey:
            cap = np.load(cpath)
        else:
            cap = caption_frames(video, cb, W, FPS); np.save(cpath, cap); open(cpath + ".key", "w").write(ckey)
        cap = cap[:n]                                      #    without our strip, add an EMPTY strip (dynamic)
        cap = np.pad(cap, (0, n - len(cap)))
        grow = np.convolve(cap.astype(int), np.ones(2 * 2 + 1, int), "same") > 0   # +-2 frames margin
        add = grow & (state == -2); dyn = int(add.sum()); state[add] = -1
    on = state != -2                                       # 6. no blinking: close off-gaps shorter than 0.5 s
    k = 0
    while k < n:
        if on[k]: k += 1; continue
        j = k
        while j < n and not on[j]: j += 1
        if k > 0 and j < n and (j - k) < 0.5 * FPS: state[k:j] = -1
        k = j
    png_of = lambda v: cards[v][2] if v >= 0 else (strip_only if v == -1 else blank)
    st = os.stat(video)
    base_sig = hashlib.md5(json.dumps([state.tolist(), [[c.start_ms, c.end_ms, c.text] for c in cues], font_path, a.size, y0, y1,
                                       st.st_size, int(st.st_mtime), FPS]).encode()).hexdigest()
    cf = round(a.chunk * FPS); chunks = [(k * cf, min((k + 1) * cf, n)) for k in range(math.ceil(n / cf))]
    t_stop = time.time() + a.max_seconds
    def job(k):
        f0, f1 = chunks[k]; out = os.path.join(d, f"part{k:03d}.mp4"); sig = f"{base_sig}|{f0}|{f1}"
        if (os.path.exists(out + ".ok") and open(out + ".ok").read() == sig) or time.time() > t_stop:
            return
        items, x = [], f0
        while x < f1:
            y = x
            while y < f1 and state[y] == state[x]: y += 1
            items.append((png_of(state[x]), (y - x) / FPS)); x = y
        lst = out + ".txt"
        with open(lst, "w") as fh:
            fh.write("ffconcat version 1.0\n")
            for p, du in items: fh.write(f"file '{os.path.abspath(p)}'\nduration {du:.6f}\n")
            fh.write(f"file '{os.path.abspath(items[-1][0])}'\n")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{f0 / FPS:.6f}", "-i", video, "-f", "concat", "-safe", "0", "-i", lst,
                            "-filter_complex", f"[1:v]fps={FPS}[o];[0:v][o]overlay=0:0:eof_action=repeat,format=yuv420p[v]",
                            "-map", "[v]", "-frames:v", str(f1 - f0), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "18", "-an", out], capture_output=True, text=True)
        if r.returncode: print(f"FAILED chunk {k} (exit {r.returncode}): {r.stderr[-300:]}")
        else: open(out + ".ok", "w").write(sig)
    with ThreadPoolExecutor(a.workers) as ex: list(ex.map(job, range(len(chunks))))
    done = sum(os.path.exists(os.path.join(d, f"part{k:03d}.mp4.ok")) and
               open(os.path.join(d, f"part{k:03d}.mp4.ok")).read() == f"{base_sig}|{chunks[k][0]}|{chunks[k][1]}" for k in range(len(chunks)))
    print(f"subtitled {done}/{len(chunks)} chunks")
    if done < len(chunks):
        print("not finished -- run the same subs command again (it resumes)"); sys.exit(3)
    lst = os.path.join(d, "parts.txt"); silent = os.path.join(d, "silent.mp4")
    open(lst, "w").write("".join(f"file '{os.path.abspath(os.path.join(d, f'part{k:03d}.mp4'))}'\n" for k in range(len(chunks))))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent], check=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", silent, "-i", video, "-map", "0:v", "-map", "1:a", "-c", "copy",
                    "-movflags", "+faststart", a.out], check=True)
    # ---- self-check on the saved file ----
    def strip(path):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"crop={W}:{y1 - y0 + 1}:0:{y0},scale=108:16,format=gray",
                              "-f", "rawvideo", "-"], capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(-1, 16, 108).astype(np.int16)
    A, B = strip(a.out), strip(video); m = min(len(A), len(B), n)
    shown = np.concatenate([A[:m, 2:14, :4], A[:m, 2:14, -4:]], axis=2).reshape(m, -1).min(1) >= 245
    exp = state[:m] != -2
    edge = np.zeros(m, bool); ch = np.where(np.diff(exp.astype(int)) != 0)[0]
    for x in ch: edge[max(x - 1, 0):x + 3] = True
    on_bad = np.where(exp & ~edge & ~shown)[0]
    off_bad = np.where(~exp & ~edge & (np.abs(A[:m] - B[:m]).mean((1, 2)) > 6))[0]
    eng = np.where(cap[:m] & ~shown)[0] if cap is not None else np.array([], int)
    vd, sd = probe_duration_ms(a.out), probe_duration_ms(video)
    two = sum(1 for c in cards if c[3] > 1)
    print(f"saved {a.out}: {vd / 1000:.3f}s (rendered video {sd / 1000:.3f}s); {len(cards)} subtitles, {two} on two lines")
    if small: print(f"font reduced to fit (too long even on two lines): {[(i, s) for i, s, _ in small]}")
    print(f"first subtitle: SRT start {cards[0][0]:.2f}s -> " + (f"shown from {win0 / FPS:.2f}s (lead-in)" if lead_used else "as timed")
          + f"; last subtitle " + ("held to the end" if tail_used else "as timed")
          + f"; {held} short pauses bridged by holding the previous line; {dyn} frames got an empty strip to hide a source caption")
    print(f"self-check: strip missing on {len(on_bad)} planned frames, picture changed on {len(off_bad)} other frames, "
          f"source caption visible on {len(eng)} frames" + (f" -- at {[round(x / FPS, 2) for x in list(on_bad[:3]) + list(off_bad[:3]) + list(eng[:3])]}s"
                                                          if len(on_bad) + len(off_bad) + len(eng) else ""))
    one = next((c for c in cards if c[3] == 1), cards[0]); twol = next((c for c in cards if c[3] > 1), cards[-1])
    shots = [win0 / FPS + 0.2, (one[0] + one[1]) / 2, (twol[0] + twol[1]) / 2] + ([(cards[small[0][0]][0] + cards[small[0][0]][1]) / 2] if small else [])
    qc = Image.new("RGB", (len(shots) * 540, 540), "white")
    for k, t in enumerate(shots):
        png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", a.out, "-frames:v", "1", "-vf", "scale=540:540",
                              "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        if png: qc.paste(Image.open(io.BytesIO(png)).convert("RGB"), (k * 540, 0))
    qp = os.path.join(a.work, "subs_qc.jpg"); qc.save(qp, quality=85)
    print(f"subtitle QC image (first frame, one-line, two-line{', reduced-font' if small else ''}): {qp} -- LOOK at it")
    if not a.keep_parts:
        for k in range(len(chunks)):
            for ext in ("", ".ok", ".txt"):
                q = os.path.join(d, f"part{k:03d}.mp4{ext}")
                if os.path.exists(q): os.remove(q)
        os.remove(silent)
    cfg = load(a.work, "config.json")
    cfg.setdefault("subs_outputs", {})[os.path.abspath(a.out)] = dict(srt=os.path.abspath(srt), video=os.path.abspath(video), offset_s=off)
    save(a.work, "config.json", cfg)
    if len(on_bad) or len(off_bad) or len(eng) or abs(vd - sd) > 1000 / FPS + 10: sys.exit(1)

# ---------------------------------------------------------------------------------
# 9. bookends: put back the source's unused opening and ending, at 1x with their own sound
#    (the build-up / theme music before the first matched line and after the last one).
#    The dub episode (audio + subtitles + matched clips) is unchanged; it just starts later.
# ---------------------------------------------------------------------------------
def scan_captions(video, box, W, FPS, t0, t1):
    """Times (s) in [t0, t1) where the video shows its burned-in caption box."""
    import numpy as np
    if t1 - t0 < 1 / FPS:
        return []
    t, b = box; h = b - t + 1; sh = max(h // 2, 8)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", video, "-vf",
                          f"crop={W}:{h}:0:{t},scale=540:{sh},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, sh, 540)
    on = ((fr > 235).sum(2) > 540 * 0.11).sum(1) >= 0.3 * sh
    return [t0 + i / FPS for i in np.where(on)[0]]

def cmd_bookends(a):
    import numpy as np
    cfg = load(a.work, "config.json"); main = a.input or cfg.get("video_out")
    if not main or not os.path.exists(main):
        raise SystemExit("no rendered video in this work folder -- run `render` and `verify` first")
    out = os.path.abspath(a.out)
    if out == os.path.abspath(main):
        raise SystemExit("--out must be a new file (the clean master), not the render itself")
    # The input is always the render WITHOUT opening/ending, so re-running simply rebuilds the clean master.
    for p in (cfg.get("subs_outputs") or {}):
        if os.path.abspath(main) == os.path.abspath(p):
            raise SystemExit(f"{main} is a subtitled output; bookends takes the clean render (video_out)")
    segs = active_manifest(a.work, cfg, cfg.get("video_manifest"))[1]["segments"]
    src = cfg["src_video"]; W, H, FPS = probe_video(src); src_ms = probe_duration_ms(src)
    cues = shift_cues(parse_srt(cfg["src_srt"]), cfg.get("src_offset_ms", 0))
    first_clip = min(s["us_start_ms"] for s in segs); last_clip = max(s["us_end_ms"] for s in segs)
    i_end = min([first_clip] + ([cues[0].start_ms] if cues else []))        # before ANY source dialogue
    o_start = max([last_clip] + ([cues[-1].end_ms] if cues else []))         # after ANY source dialogue
    cbp = os.path.join(a.work, "caption_box.json")
    box = load(a.work, "caption_box.json")["box"] if os.path.exists(cbp) else detect_caption_box(src)
    if box:                                                                 # and no burned-in caption on screen
        caps = scan_captions(src, box, W, FPS, 0, i_end / 1000)
        if caps: i_end = max(0, (caps[0] - 2 / FPS) * 1000)
        caps = scan_captions(src, box, W, FPS, o_start / 1000, src_ms / 1000)
        if caps: o_start = (caps[-1] + 3 / FPS) * 1000
    n_in = int(i_end * FPS / 1000); o_f = math.ceil(o_start * FPS / 1000); n_out = int(src_ms * FPS / 1000) - o_f
    if n_in < a.min_len * FPS: n_in = 0
    if n_out < a.min_len * FPS: n_out = 0
    if getattr(a, "no_ending", False):
        n_out = 0; print("--no-ending: the video ends with the dub audio (no source ending after it)")
    print(f"opening: source 0.00-{n_in / FPS:.2f}s  (first matched clip at {first_clip / 1000:.2f}s, first source subtitle at "
          f"{cues[0].start_ms / 1000 if cues else 0:.2f}s)")
    print(f"ending:  source {o_f / FPS:.2f}-{(o_f + n_out) / FPS:.2f}s  (last matched clip ends {last_clip / 1000:.2f}s, last source "
          f"subtitle ends {cues[-1].end_ms / 1000 if cues else 0:.2f}s, source is {src_ms / 1000:.2f}s)")
    if not n_in and not n_out:
        print("nothing to add: the clean master is the render itself"); shutil.copyfile(main, out)
        cfg.update(clean_final=out, opening_s=0, ending_s=0); save(a.work, "config.json", cfg); return
    ar = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels",
                         "-of", "csv=p=0", main], capture_output=True, text=True).stdout.strip().split(",")
    sr, ch = ar[0], ar[1]
    has_a = bool(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", src],
                                capture_output=True, text=True).stdout.strip())
    d = os.path.join(a.work, "bookends"); os.makedirs(d, exist_ok=True); fd = a.fade
    def cut(f0, nf, path, fade):
        dur = nf / FPS
        af = (f"afade=t=out:st={max(dur - fd, 0):.3f}:d={fd}" if fade == "out" else f"afade=t=in:st=0:d={fd}")
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{f0 / FPS:.6f}", "-i", src]
        if not has_a: cmd += ["-f", "lavfi", "-i", f"anullsrc=r={sr}:cl={'mono' if ch == '1' else 'stereo'}"]
        cmd += ["-map", "0:v:0", "-map", "0:a:0" if has_a else "1:a:0", "-vf", f"fps={FPS},format=yuv420p", "-frames:v", str(nf),
                "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-af", f"aresample={sr},{af}", "-ac", ch, "-c:a", "aac", "-b:a", "192k", "-t", f"{dur:.6f}", path]
        subprocess.run(cmd, check=True)
    parts = []
    if n_in: cut(0, n_in, os.path.join(d, "opening.mp4"), "out"); parts.append(os.path.join(d, "opening.mp4"))
    parts.append(main)
    if n_out: cut(o_f, n_out, os.path.join(d, "ending.mp4"), "in"); parts.append(os.path.join(d, "ending.mp4"))
    lst = os.path.join(d, "parts.txt"); open(lst, "w").write("".join(f"file '{os.path.abspath(p)}'\n" for p in parts))
    tmp = os.path.join(os.path.dirname(out), ".bookends_tmp.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-map", "0:v", "-map", "0:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp], check=True)
    main_ms = probe_duration_ms(main); new_ms = probe_duration_ms(tmp); want = (n_in + n_out) / FPS * 1000 + main_ms
    # check: the dub audio must start exactly where the opening ends
    def pcm(path, t0, t=15):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", str(t), "-i", path, "-vn", "-ac", "1", "-ar", "8000",
                              "-f", "s16le", "-"], capture_output=True).stdout
        return np.frombuffer(raw, np.int16).astype(float)
    x, y = pcm(tmp, n_in / FPS + 0.5), pcm(cfg["dub_audio"], 0.5); m = min(len(x), len(y))
    lag, peak, _ = best_lag(y[:m], x[:m], 800) if m > 8000 else (0, 0, 0)
    os.replace(tmp, out)
    cfg["opening_s"] = round(n_in / FPS, 3); cfg["ending_s"] = round(n_out / FPS, 3); cfg["clean_final"] = out
    cfg["ending_src_s"] = round(o_f / FPS, 3); cfg["clean_nofx"] = out; cfg.pop("glow", None)
    cfg.pop("bookended", None)
    save(a.work, "config.json", cfg)
    if cfg.get("sheet_out") and os.path.exists(cfg["sheet_out"]):
        import openpyxl
        from openpyxl.styles import Font, PatternFill
        wb = openpyxl.load_workbook(cfg["sheet_out"])
        if "Video edit list" in wb.sheetnames:
            ws = wb["Video edit list"]
            for rr in range(ws.max_row, 1, -1):          # re-running bookends replaces its own rows instead of adding more
                if ws.cell(rr, 1).value == "-" and str(ws.cell(rr, 7).value or "").startswith(("OPENING:", "ENDING:")):
                    ws.delete_rows(rr)
            ws.insert_rows(2)
            note = (f"OPENING: final video 00:00.000-{fmt(n_in / FPS * 1000)} = source 00:00.000-{fmt(n_in / FPS * 1000)} at 1x with the "
                    f"source's own sound (no dub, no subtitles). Every time below is {n_in / FPS:.3f}s later in the final video.") if n_in else \
                   "OPENING: none (the first matched line starts at the very beginning of the source)."
            ws.cell(2, 1, "-"); ws.cell(2, 7, note)
            r = ws.max_row + 1; ws.cell(r, 1, "-")
            ws.cell(r, 7, f"ENDING: source {fmt(o_f / FPS * 1000)}-{fmt((o_f + n_out) / FPS * 1000)} at 1x with the source's own sound, "
                          f"after the dub ends." if n_out else "ENDING: none.")
            for rr in (2, r):
                for cc in range(1, 8):
                    ws.cell(rr, cc).fill = PatternFill("solid", start_color="FFEB9C"); ws.cell(rr, cc).font = Font(name="Arial", size=10, bold=True)
            wb.save(cfg["sheet_out"])
    print(f"saved {out}: {new_ms / 1000:.3f}s = opening {n_in / FPS:.2f}s + dub episode {main_ms / 1000:.3f}s + ending {n_out / FPS:.2f}s "
          f"(expected {want / 1000:.3f}s, diff {abs(new_ms - want):.0f}ms)")
    print(f"check: dub audio starts {lag / 8000 * 1000:+.0f}ms from the end of the opening (match {peak:.2f})")
    if abs(new_ms - want) > 3 * 1000 / FPS + 50 or (m > 8000 and (abs(lag) > 400 or peak < 0.5)): sys.exit(1)

# ---------------------------------------------------------------------------------
# 9b. transitions: a transition at every clip switch of the clean master (owner's TOLR choice: light leak,
#     blur dissolve and zoom blur in random order, never the same one twice in a row; `glow` is also available).
#     Where the stitched picture jumps in the source (between groups, lip clips, trimmed lip sentences, and the joins
#     to the opening and ending) the switch gets a short effect. Dissolving effects mix the outgoing clip (running on a
#     little past its end in the source, at its own speed) with the incoming clip (starting a little earlier). No overlap
#     is added, so the length and the sync with the dub voice never change. Resumable in chunks.
# ---------------------------------------------------------------------------------
TRANSITION_HALF = dict(light_leak=0.30, blur_dissolve=0.30, zoom_blur=0.25, glow=0.25)   # seconds on each side of the switch

def _ss(x):
    x = min(max(x, 0.0), 1.0); return x * x * (3 - 2 * x)

def glow_frame(fr, w, peak=0.82):
    import numpy as np, cv2
    H, W = fr.shape[:2]
    f = fr.astype(np.float32)
    sm = cv2.resize(f, (max(W // 4, 1), max(H // 4, 1)), interpolation=cv2.INTER_AREA)
    bloom = cv2.resize(cv2.GaussianBlur(sm, (0, 0), 3 + 9 * w), (W, H), interpolation=cv2.INTER_LINEAR)
    out = 255 - (255 - f) * (255 - bloom * 0.9 * w) / 255             # screen-blend the bloom (glow)
    soft = cv2.GaussianBlur(out, (0, 0), 1 + 6 * w)                   # soften as it peaks (blur)
    out = out + (soft - out) * min(1.0, 1.4 * w)
    tint = np.array([255, 246, 238], np.float32)                      # RGB warm white
    out = out + (tint - out) * (peak * w ** 1.6)
    return np.clip(out, 0, 255).astype(np.uint8)

def transition_frame(style, a, b, x, w, p):
    """One frame of a transition. a/b = outgoing/incoming frame (float32 RGB), x = signed position in the transition
    (-1 at its start, 0 at the switch, +1 at its end), w = bump 0..1 (1 at the switch), p = progress 0..1."""
    import numpy as np, cv2
    H, W = a.shape[:2]
    if style == "light_leak":                                       # warm orange flare sweeping across a soft mix
        base = a * (1 - p) + b * p
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        lx = -0.3 * W + p * 1.6 * W
        leak = np.exp(-(((xx - lx) / (0.45 * W)) ** 2 + ((yy - 0.35 * H) / (0.9 * H)) ** 2))
        col = np.array([255, 140, 40], np.float32); m = (leak * w * 0.95)[..., None]
        out = base * (1 - m) + (255 - (255 - base) * (255 - col) / 255) * m
    elif style == "blur_dissolve":                                  # both clips blur while they mix
        out = a * (1 - p) + b * p
        if w > 0.01: out = cv2.GaussianBlur(out, (0, 0), 0.5 + 18 * w)
    elif style == "zoom_blur":                                      # radial zoom punch over a hard switch
        f = a if x < 0 else b
        if w < 0.01: out = f
        else:
            acc = np.zeros_like(f)
            for k in range(8):
                s = 1 + 0.25 * w * k / 7; M = np.float32([[s, 0, W / 2 - s * W / 2], [0, s, H / 2 - s * H / 2]])
                acc += cv2.warpAffine(f, M, (W, H), borderMode=cv2.BORDER_REFLECT)
            out = acc / 8
    elif style == "glow":
        return glow_frame(a if x < 0 else b, w)
    else:
        raise SystemExit(f"unknown transition {style}")
    return np.clip(out, 0, 255).astype(np.uint8)

def clip_switches(cfg, segs, min_jump):
    """Clip switches of the clean master: [(final time s, outgoing source end s, outgoing speed, incoming source start s,
    incoming speed)] -- speed = source seconds per final-video second. Our stitching only, not the source's own cuts."""
    op = cfg.get("opening_s", 0) or 0
    segs = sorted(segs, key=lambda s: s["it_start_ms"]); out = []
    sp = lambda s: (s["us_end_ms"] - s["us_start_ms"]) / max(s["it_end_ms"] - s["it_start_ms"], 1)
    if op > 0 and abs(segs[0]["us_start_ms"] / 1000 - op) * 1000 > min_jump:
        out.append((op, op, 1.0, segs[0]["us_start_ms"] / 1000, sp(segs[0])))
    for x, y in zip(segs, segs[1:]):
        if abs(y["us_start_ms"] - x["us_end_ms"]) > min_jump:
            out.append((op + x["it_end_ms"] / 1000, x["us_end_ms"] / 1000, sp(x), y["us_start_ms"] / 1000, sp(y)))
    es = cfg.get("ending_src_s")
    if (cfg.get("ending_s", 0) or 0) > 0 and es is not None and abs(es * 1000 - segs[-1]["us_end_ms"]) > min_jump:
        out.append((op + segs[-1]["it_end_ms"] / 1000, segs[-1]["us_end_ms"] / 1000, sp(segs[-1]), es, 1.0))
    return out

def cmd_transitions(a):
    import numpy as np, random
    cfg = load(a.work, "config.json")
    src = a.input or cfg.get("clean_nofx") or cfg.get("clean_final")
    if not src or not os.path.exists(src): raise SystemExit("no clean master -- run `bookends` first")
    out = os.path.abspath(a.out)
    if out == os.path.abspath(src): raise SystemExit("--out must be a new file (bookends' clean master is the input)")
    W, H, FPS = probe_video(src)
    n = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                            "stream=nb_read_packets", "-of", "csv=p=0", src], capture_output=True, text=True).stdout.strip())  # real frame count
    if a.until: n = min(n, round(a.until * FPS))
    segs = active_manifest(a.work, cfg, cfg.get("video_manifest"))[1]["segments"]
    sw = clip_switches(cfg, segs, a.min_jump)
    styles = [s.strip() for s in a.styles.split(",") if s.strip()]
    for s in styles:
        if s not in TRANSITION_HALF: raise SystemExit(f"unknown transition {s!r}; choose from {sorted(TRANSITION_HALF)}")
    rnd = random.Random(a.seed); plan, prev = [], None
    for k, (c, ua, sa, ub, sb) in enumerate(sw):                    # random order, never the same style twice in a row
        st = rnd.choice([s for s in styles if s != prev] or styles); prev = st
        hh = TRANSITION_HALF[st] * a.length
        pg = c - (sw[k - 1][0] if k else 0); ng = (sw[k + 1][0] if k + 1 < len(sw) else 1e9) - c
        plan.append(dict(t=c, style=st, hb=min(hh, 0.45 * pg), ha=min(hh, 0.45 * ng), ua=ua, sa=sa, ub=ub, sb=sb))
    src_video = cfg["src_video"]
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0",
                           src_video], capture_output=True, text=True).stdout.strip().split("/")
    sfps = int(rate[0]) / int(rate[1]) if len(rate) == 2 else float(rate[0])
    vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2"
    def grab(t0, t1):                                               # source frames [t0, t1) at the render's size
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t0, 0):.3f}", "-t", f"{t1 - max(t0, 0):.3f}", "-i", src_video, "-vf", vf,
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return max(t0, 0), np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)
    def at(fr, t0, u):
        return fr[1][min(max(int(round((u - fr[0]) * sfps)), 0), len(fr[1]) - 1)].astype(np.float32)
    d = os.path.join(a.work, "trans_" + re.sub(r"[^\w.-]", "_", os.path.splitext(os.path.basename(out))[0])); os.makedirs(d, exist_ok=True)
    st_ = os.stat(src)
    sig0 = hashlib.md5(json.dumps([plan, a.length, st_.st_size, int(st_.st_mtime), FPS, n]).encode()).hexdigest()
    cf = round(a.chunk * FPS); chunks = [(k * cf, min((k + 1) * cf, n)) for k in range(math.ceil(n / cf))]
    t_stop = time.time() + a.max_seconds
    def job(k):
        f0, f1 = chunks[k]; p = os.path.join(d, f"part{k:03d}.mp4"); sig = f"{sig0}|{f0}|{f1}"
        if (os.path.exists(p + ".ok") and open(p + ".ok").read() == sig) or time.time() > t_stop: return
        mine = [q for q in plan if q["t"] + q["ha"] > f0 / FPS and q["t"] - q["hb"] < f1 / FPS]
        cache = {}                                                  # source frames of ONE transition at a time (memory:
        def frames(q):                                              # clause-mode episodes have many switches per chunk)
            if id(q) not in cache:                                  # outgoing clip runs on, incoming clip starts earlier
                cache.clear()
                cache[id(q)] = (grab(q["ua"] - (q["hb"] + 0.1) * q["sa"], q["ua"] + (q["ha"] + 0.1) * q["sa"]),
                                grab(q["ub"] - (q["hb"] + 0.1) * q["sb"], q["ub"] + (q["ha"] + 0.1) * q["sb"]))
            return cache[id(q)]
        dec = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{f0 / FPS:.6f}", "-i", src, "-frames:v", str(f1 - f0), "-f", "rawvideo",
                                "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", p], stdin=subprocess.PIPE)
        m = 0
        while m < f1 - f0:
            buf = dec.stdout.read(W * H * 3)
            if len(buf) < W * H * 3: break
            t = (f0 + m + 0.5) / FPS
            q = next((q for q in mine if q["t"] - q["hb"] <= t < q["t"] + q["ha"]), None)
            if q and q["hb"] > 0.02 and q["ha"] > 0.02:
                dt = t - q["t"]; x = dt / q["hb"] if dt < 0 else dt / q["ha"]
                w = _ss(1 - abs(x)); p_ = _ss((x + 1) / 2)
                fa, fb = frames(q)
                A = at(fa, 0, q["ua"] + dt * q["sa"]); B = at(fb, 0, q["ub"] + dt * q["sb"])
                buf = transition_frame(q["style"], A, B, x, w, p_).tobytes()
            enc.stdin.write(buf); m += 1
        enc.stdin.close(); enc.wait(); dec.wait()
        if m == f1 - f0 and enc.returncode == 0: open(p + ".ok", "w").write(sig)
        else: print(f"FAILED chunk {k}: {m}/{f1 - f0} frames")
    with ThreadPoolExecutor(a.workers) as ex: list(ex.map(job, range(len(chunks))))
    done = sum(os.path.exists(os.path.join(d, f"part{k:03d}.mp4.ok")) and
               open(os.path.join(d, f"part{k:03d}.mp4.ok")).read() == f"{sig0}|{chunks[k][0]}|{chunks[k][1]}" for k in range(len(chunks)))
    cnt = defaultdict(int)
    for q in plan:
        if q["t"] < n / FPS: cnt[q["style"]] += 1
    print(f"transitions: {sum(cnt.values())} clip switches ({dict(cnt)}); {done}/{len(chunks)} chunks done")
    if done < len(chunks): print("not finished -- run the same command again (it resumes)"); sys.exit(3)
    lst = os.path.join(d, "parts.txt"); silent = os.path.join(d, "silent.mp4")
    open(lst, "w").write("".join(f"file '{os.path.abspath(os.path.join(d, f'part{k:03d}.mp4'))}'\n" for k in range(len(chunks))))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent], check=True)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", silent, "-i", src, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "192k", "-t", f"{n / FPS:.6f}", "-movflags", "+faststart", out], check=True)
    vd = probe_duration_ms(out)
    for k in range(len(chunks)):
        for ext in ("", ".ok"):
            q = os.path.join(d, f"part{k:03d}.mp4{ext}")
            if os.path.exists(q): os.remove(q)
    os.remove(silent)
    rows = [q for q in plan if q["t"] < n / FPS]
    if a.until:
        print(f"saved TEST {out}: first {vd / 1000:.3f}s only (the clean master is unchanged)"); return
    cfg = load(a.work, "config.json")
    cfg["clean_nofx"] = os.path.abspath(src); cfg["clean_final"] = out
    cfg["transitions"] = dict(styles=styles, seed=a.seed, length=a.length, plan=[dict(t=round(q["t"], 3), style=q["style"]) for q in rows], out=out)
    cfg.pop("glow", None); save(a.work, "config.json", cfg)
    if cfg.get("sheet_out") and os.path.exists(cfg["sheet_out"]):      # list every switch and its transition in the mapping sheet
        import openpyxl
        from openpyxl.styles import Font, PatternFill
        wb = openpyxl.load_workbook(cfg["sheet_out"])
        if "Transitions" in wb.sheetnames: del wb["Transitions"]
        ws = wb.create_sheet("Transitions")
        for ci, h in enumerate(["#", "Final video time (s)", "Timecode", "Transition", "Length (s)"], 1):
            c = ws.cell(1, ci, h); c.font = Font(name="Arial", bold=True, color="FFFFFF"); c.fill = PatternFill("solid", start_color="1F4E78")
        for k, q in enumerate(rows, 2):
            for ci, v in enumerate([k - 1, round(q["t"], 3), fmt(q["t"] * 1000), q["style"].replace("_", " "), round(q["hb"] + q["ha"], 2)], 1):
                ws.cell(k, ci, v).font = Font(name="Arial", size=10)
        wb.save(cfg["sheet_out"])
    print(f"saved {out}: {vd / 1000:.3f}s; {len(rows)} transitions ({dict(cnt)}), never the same twice in a row; "
          f"this is now the clean master that `subs` uses")

# ---------------------------------------------------------------------------------
# 10. groups: continuous US footage for blocks of 7-8 Italian rows (the mandatory dynamic-group flow)
#     Built on the finished row-level manifest (silences absorbed, bleed fix, gap fill). Each group plays ONE
#     continuous US span; extra US time is trimmed from the edges of fast narration rows, missing time is a gentle
#     whole-group stretch. Writes manifest_groups.json (render/verify/bookends then use it) + a review workbook.
# ---------------------------------------------------------------------------------
_SPEECH_W = set("i i'm i've i'll i'd me my mine myself we we're we've we'll us our ours you you're you've you'll you'd "
                "your yours yourself yourselves let's".split())
_NARR_W = set("he she his her him himself herself they them their theirs said asked thought wondered replied muttered "
              "whispered yelled shouted sighed told continued answered added called cried exclaimed snapped".split())

def speech_or_narration(text):
    """Rough class of a source line, recorded so a person can check every trim: 'direct speech' or 'narration / reaction'."""
    text = text or ""; words = re.findall(r"[a-z']+", text.lower())
    sp = sum(w in _SPEECH_W for w in words) + (1 if re.search(r'["“”«»]', text) else 0)
    na = sum(w in _NARR_W for w in words)
    if text.rstrip().endswith(("?", "!")) and not na: sp += 1
    return "direct speech" if sp > na else "narration / reaction"

def _group_plan(G, rows, a, repeat_rows, lip_rows=None):
    """One group: G = consecutive manifest segments. Returns dict with output segments, trims, drift, flags."""
    it0, it1 = G[0]["it_start_ms"], G[-1]["it_end_ms"]
    us0, us1 = G[0]["us_start_ms"], G[-1]["us_end_ms"]
    it_dur, us_dur = it1 - it0, us1 - us0
    info = dict(rows=[s["it_idx"] for s in G], it0=it0, it1=it1, us0=us0, us1=us1, trims=[], flags=[], note="")
    lip_rows = lip_rows or {}
    if len(G) == 1 and G[0]["it_idx"] in lip_rows:              # lip-sync row: its own clip, lips centred on the dub line
        p = lip_rows[G[0]["it_idx"]]
        info["note"] = p.get("note") or f"lip-sync row: own clip at x{p['stretch']}"
        info["lip"] = True; pieces = [(us0, us1)]
    elif len(G) == 1 and G[0]["it_idx"] in repeat_rows:          # dub repeat: replays the earlier row's footage as is
        info["note"] = "dub repeat: plays the earlier row's footage as in the row-level manifest"
        pieces = [(us0, us1)]
    else:
        keep_target = min(us_dur, it_dur * a.max_speedup)      # --max-speedup 1.0: trim ALL extra US time (approved flow)
        excess = us_dur - keep_target; removed = []
        if 0 < excess <= a.min_cut:
            info["note"] = f"only {excess / 1000:.2f}s extra: absorbed by a {us_dur / it_dur:.3f}x speed-up instead of a cut"
        elif excess > a.min_cut:
            cands = []
            for s in G:
                r = rows[s["it_idx"]]; ulen = s["us_end_ms"] - s["us_start_ms"]; cap = ulen - a.min_keep
                if cap <= 0 or s["it_idx"] in repeat_rows:
                    continue
                f = (r["dub_end"] - r["dub_start"]) / max(ulen, 1)     # < 1: the Italian line is shorter = plays fast
                cls = speech_or_narration(s.get("us_text") or r.get("src_text") or "")
                tier = (0 if f < 1 else 2) + (0 if cls.startswith("narration") else 1)
                cands.append(dict(seg=s, cap=cap, f=f, cls=cls, tier=tier))
            need = excess
            for tier in range(4):
                pool = sorted([c for c in cands if c["tier"] == tier], key=lambda c: -c["cap"])
                while pool and need > 1:
                    tot = sum(c["cap"] for c in pool)
                    al = [min(c["cap"], need * c["cap"] / tot) for c in pool]
                    if min(al) >= a.min_cut or len(pool) == 1:
                        break
                    pool = pool[:-1]                             # never cut slivers: drop the smallest candidate
                if not pool or need <= 1:
                    continue
                tot = sum(c["cap"] for c in pool)
                for c in pool:
                    e = min(c["cap"], need * c["cap"] / tot)
                    s = c["seg"]; h = e / 2
                    removed += [(s["us_start_ms"], s["us_start_ms"] + h), (s["us_end_ms"] - h, s["us_end_ms"])]
                    info["trims"].append(dict(row=s["it_idx"], ms=round(e), cls=c["cls"], fast=c["f"] < 1,
                                              text=s.get("us_text") or ""))
                    if tier >= 2:
                        info["flags"].append(f"IT#{s['it_idx']} trimmed although it is not a fast row -- check it")
                    elif tier == 1 and not any(x["tier"] == 0 for x in cands):
                        info["flags"].append(f"IT#{s['it_idx']} (direct speech) trimmed: no narration row could take it")
                need -= sum(min(c["cap"], need * c["cap"] / tot) for c in pool)
            if need > 1:
                info["flags"].append(f"{need / 1000:.2f}s could not be trimmed (no row long enough): absorbed by speed-up")
        removed.sort(); pieces, cur = [], us0
        for lo, hi in removed:
            if lo > cur: pieces.append((cur, lo))
            cur = max(cur, hi)
        if cur < us1: pieces.append((cur, us1))
        info["removed"] = [(round(lo), round(hi)) for lo, hi in removed]
    kept = sum(b - a_ for a_, b in pieces)
    stretch = it_dur / max(kept, 1)
    info.update(kept=kept, stretch=stretch, removed_ms=us_dur - kept)
    if stretch > a.max_stretch: info["flags"].append(f"whole group plays {stretch:.2f}x slower (limit {a.max_stretch})")
    if stretch < 1 / a.max_stretch: info["flags"].append(f"whole group plays {1 / stretch:.2f}x faster (limit {a.max_stretch})")
    # split kept pieces at row boundaries (labels + finer verify), never into slivers under 120 ms
    bounds = sorted({s["us_start_ms"] for s in G} | {s["us_end_ms"] for s in G})
    sub = []
    for p0, p1 in pieces:
        cuts = [b for b in bounds if p0 + 120 < b < p1 - 120]
        edges = [p0] + cuts + [p1]
        sub += list(zip(edges, edges[1:]))
    def owner(u0, u1):
        mid = (u0 + u1) / 2
        inside = [s for s in G if s["us_start_ms"] <= mid < s["us_end_ms"]]
        if inside: return inside[0], ""
        before = [s for s in G if s["us_end_ms"] <= mid]
        return (before[-1] if before else G[0]), " (footage between rows)"
    out, t = [], float(it0)
    for k, (u0, u1) in enumerate(sub):
        d_it = (u1 - u0) * it_dur / kept; t1 = it1 if k == len(sub) - 1 else t + d_it
        s, why = owner(u0, u1)
        out.append(dict(kind="clip", it_idx=s["it_idx"], it_start_ms=round(t, 3), it_end_ms=round(t1, 3),
                        us_start_ms=round(u0, 3), us_end_ms=round(u1, 3), it_text=s.get("it_text", ""),
                        us_text=s.get("us_text", ""), stretch_factor=round(stretch, 3), note=why.strip()))
        t = t1
    # picture/voice drift: where each row's (kept) footage plays vs. when its Italian line is spoken
    def out_time(u):
        acc = 0.0
        for p0, p1 in pieces:
            if u <= p0: break
            acc += min(u, p1) - p0
        return it0 + acc * it_dur / kept
    drift = {}
    for s in G:
        r = rows[s["it_idx"]]
        lo, hi = s["us_start_ms"], s["us_end_ms"]
        keep_parts = [(max(lo, p0), min(hi, p1)) for p0, p1 in pieces if min(hi, p1) > max(lo, p0)]
        if not keep_parts: continue
        u_mid = (keep_parts[0][0] + keep_parts[-1][1]) / 2
        drift[s["it_idx"]] = round(out_time(u_mid) - (r["dub_start"] + r["dub_end"]) / 2)
    info.update(segments=out, drift=drift, max_drift=max([abs(v) for v in drift.values()], default=0))
    return info

def cmd_groups(a):
    cfg = cfg_get(a)
    M = load(a.work, "manifest.json"); segs = M["segments"]; total_ms = M["total_ms"]
    R = load(a.work, "rows.json"); rows = {r["idx"]: r for r in R["rows"]}
    repeat_rows = {int(k) for k in R["repeat"]}
    lip = {}                                                  # lip-sync rows (`lipsync`): own clip, never inside a group
    lp = os.path.join(a.work, "lipsync.json")
    if os.path.exists(lp) and not a.no_lipsync:
        lip = {int(k): v for k, v in load(a.work, "lipsync.json")["rows"].items()}
        segs = [dict(s_, us_start_ms=lip[s_["it_idx"]]["window"][0], us_end_ms=lip[s_["it_idx"]]["window"][1])
                if s_["it_idx"] in lip else dict(s_) for s_ in segs]
        for k, s_ in enumerate(segs):                         # a lip row keeps its own spoken time; the silence next to it
            p_ = lip.get(s_["it_idx"])                         # goes to the neighbouring grouped row
            if p_ and "slot" in p_:
                s_["it_start_ms"], s_["it_end_ms"] = p_["slot"]
                if k: segs[k - 1]["it_end_ms"] = p_["slot"][0]
                if k + 1 < len(segs): segs[k + 1]["it_start_ms"] = p_["slot"][1]
        bad = [s_["it_idx"] for s_ in segs if s_["it_end_ms"] <= s_["it_start_ms"]]
        if bad: raise SystemExit(f"lip-sync slots leave rows {bad} without time -- re-run `lipsync` after `manifest`")
        # ---- lip groups: lip-sync rows that follow each other in the source (a phone call, a dialogue), with at most
        # --lip-group-narr narration rows between them, play as ONE continuous piece of source footage. Every lip row still
        # starts and ends its sentence with its own dub voice; the footage between two lip sentences (the source pause and
        # any narration rows' sentences) fills the dub time between them at one speed. No jump inside -> no transition.
        islip = lambda k: segs[k]["it_idx"] in lip
        def link(k, m):                                       # can lip seg k and the next lip seg m be one piece?
            mid = segs[k + 1:m]
            if len(mid) > a.lip_group_narr or any(s_["it_idx"] in repeat_rows for s_ in segs[k:m + 1]): return None
            rk, rm = rows[segs[k]["it_idx"]], rows[segs[m]["it_idx"]]
            u1, u0, d1, d0 = rk["src_end"], rm["src_start"], rk["dub_end"], rm["dub_start"]
            if u0 < u1 - 40: return None                      # the source goes back: not continuous
            prev = u1
            for s_ in mid:                                    # narration rows must sit in order between the two sentences
                r = rows[s_["it_idx"]]
                if r["src_start"] is None or r["src_start"] < prev - 40 or r["src_end"] > u0 + 40: return None
                prev = r["src_end"]
            S, D = u0 - u1, d0 - d1
            if D > 40 and S > 40 and 1 / a.lip_group_speed <= S / D <= a.lip_group_speed: return "between"
            if not mid and S <= 700: return "meet"           # back to back: the two sentences meet halfway in the pause
            return None
        def lip_window(i, it_len):
            # the row's source sentence, or -- if it would play faster than --lip-max-speed -- the part of it around the
            # lip movement that plays at exactly that speed
            r = rows[i]; u0, u1 = r["src_start"], r["src_end"]
            if not a.lip_max_speed or (u1 - u0) <= it_len * max(a.lip_trim_above, a.lip_max_speed): return (u0, u1), False
            Lk = it_len * a.lip_max_speed
            ls = [(max(x * 1000, u0), min(y * 1000, u1)) for x, y in lip[i].get("lips", []) if min(y * 1000, u1) > max(x * 1000, u0)]
            l0, l1 = (min(x for x, _ in ls), max(y for _, y in ls)) if ls else (u0, u1)
            w0 = (l0 + l1) / 2 - Lk / 2 if Lk >= l1 - l0 else l0
            w0 = min(max(w0, u0), u1 - Lk)
            return (w0, w0 + Lk), True
        chains = []
        if not a.no_lip_groups:
            lipk = [k for k in range(len(segs)) if islip(k)]; i = 0
            while i < len(lipk):
                ch, links = [lipk[i]], []
                while i + 1 < len(lipk):
                    how = link(lipk[i], lipk[i + 1])
                    if not how: break
                    links.append(how); ch.append(lipk[i + 1]); i += 1
                i += 1
                if len(ch) >= 2: chains.append((ch, links))
        inchain = {k for ch, _ in chains for k in range(ch[0], ch[-1] + 1)}
        single_trims = []
        for k, s_ in enumerate(segs):                         # single lip rows too (TRIM mode)
            if a.lip_max_speed > 0 and islip(k) and k not in inchain:
                (x0, x1), cut = lip_window(s_["it_idx"], s_["it_end_ms"] - s_["it_start_ms"])
                if cut:
                    s_["us_start_ms"], s_["us_end_ms"] = x0, x1; single_trims.append((s_["it_idx"], round(x1 - x0)))
        chain_groups = []
        for ch, links in chains:
            k0, k1 = ch[0], ch[-1]
            kn = [(segs[k0]["it_start_ms"], segs[k0]["us_start_ms"])]
            if a.lip_anchor == "start":
                # every lip sentence STARTS with its dub voice; its whole source sentence (and the pause / narration after
                # it) then runs until the next lip sentence starts with its voice -> the dub line's pause is used too
                kn += [(rows[segs[k]["it_idx"]]["dub_start"], rows[segs[k]["it_idx"]]["src_start"]) for k in ch[1:]]
            else:
                for q in range(len(ch) - 1):
                    rk, rm = rows[segs[ch[q]]["it_idx"]], rows[segs[ch[q + 1]]["it_idx"]]
                    if links[q] == "between": kn += [(rk["dub_end"], rk["src_end"]), (rm["dub_start"], rm["src_start"])]
                    else: kn.append(((rk["dub_end"] + rm["dub_start"]) / 2, (rk["src_end"] + rm["src_start"]) / 2))
            kn.append((segs[k1]["it_end_ms"], segs[k1]["us_end_ms"]))
            kn = [x for q_, x in enumerate(kn) if q_ == 0 or (x[0] > kn[q_ - 1][0] + 1 and x[1] > kn[q_ - 1][1] + 1)]
            kn[-1] = (segs[k1]["it_end_ms"], kn[-1][1])
            if a.lip_smooth_ms > 0 and len(kn) > 2:
                # Even out the speed: an inner knot may move up to --lip-smooth-ms (dub time) so that a piece playing much
                # faster or slower than its neighbours lends/borrows time from them (the lip sentence then starts that much
                # before/after its voice). Greedy, 20-ms steps, never makes the neighbour worse than the piece it helps.
                kn = [list(x) for x in kn]; t0s = [x[0] for x in kn]; lim = a.lip_smooth_ms
                sp = lambda j: (kn[j + 1][1] - kn[j][1]) / max(kn[j + 1][0] - kn[j][0], 1)
                bad = lambda v: abs(math.log(max(v, 1e-3)))
                for _ in range(4000):
                    j = max(range(len(kn) - 1), key=lambda j: bad(sp(j)))
                    if sp(j) <= a.lip_group_speed_ok and sp(j) >= 1 / a.lip_group_speed_ok: break
                    moved = False
                    fast = sp(j) > 1
                    for kk, dirn in ((j, -1), (j + 1, +1)):          # widen a fast piece outward / narrow a slow one inward
                        if not 0 < kk < len(kn) - 1: continue
                        step = 20 * (dirn if fast else -dirn)
                        nt = kn[kk][0] + step
                        if abs(nt - t0s[kk]) > lim or not (kn[kk - 1][0] + 200 < nt < kn[kk + 1][0] - 200): continue
                        old = kn[kk][0]; before = max(bad(sp(kk - 1)), bad(sp(kk)))
                        kn[kk][0] = nt
                        if max(bad(sp(kk - 1)), bad(sp(kk))) < before - 1e-6: moved = True; break
                        kn[kk][0] = old
                    if not moved: break
                kn = [tuple(x) for x in kn]
            members = list(range(k0, k1 + 1)); out_s = []; flags = []
            pieces = [(ta, tb, ua, ub) for (ta, ua), (tb, ub) in zip(kn, kn[1:])]
            if a.lip_max_speed > 0:
                # TRIM mode: every lip sentence plays exactly over its own dub voice; a sentence too long for its dub line
                # (faster than --lip-max-speed) is cut down to the part around its lip movement. The footage between two
                # lip sentences (pause + narration rows) fills the dub time between them; trimmed-off parts are skipped.
                L_ = []
                for q, k in enumerate(ch):                        # [dub start, dub end, src start, src end, sentence start, end, cut]
                    r = rows[segs[k]["it_idx"]]
                    d0 = segs[k0]["it_start_ms"] if q == 0 else r["dub_start"]
                    d1 = segs[k1]["it_end_ms"] if q == len(ch) - 1 else r["dub_end"]
                    L_.append([d0, d1, r["src_start"], r["src_end"], r["src_start"], r["src_end"], False])
                for q in range(len(ch) - 1):                      # back to back: the dub pause is shared halfway first
                    if links[q] != "between":
                        mt = (L_[q][1] + L_[q + 1][0]) / 2; L_[q][1] = L_[q + 1][0] = mt
                for q, k in enumerate(ch):                        # then each sentence vs its final dub length
                    (x0, x1), cut = lip_window(segs[k]["it_idx"], L_[q][1] - L_[q][0])
                    L_[q][2], L_[q][3], L_[q][6] = x0, x1, cut
                    if q == 0 and not cut: L_[q][2] = segs[k0]["us_start_ms"]
                    if q == len(ch) - 1 and not cut: L_[q][3] = segs[k1]["us_end_ms"]
                for q in range(len(ch) - 1):                      # back to back: the source pause is shared halfway too
                    if links[q] != "between":
                        mu = (L_[q][5] + L_[q + 1][4]) / 2
                        if not L_[q][6]: L_[q][3] = mu
                        if not L_[q + 1][6]: L_[q + 1][2] = mu
                pieces = []
                for q in range(len(ch)):
                    pieces.append(tuple(L_[q][:4]))
                    if q + 1 < len(ch) and links[q] == "between":
                        pieces.append((L_[q][1], L_[q + 1][0], L_[q][5], L_[q + 1][4]))
                pieces = [p_ for p_ in pieces if p_[1] - p_[0] > 1 and p_[3] - p_[2] > 1]
                trims_done = [(segs[k]["it_idx"], round(L_[q][3] - L_[q][2]), round(L_[q][5] - L_[q][4])) for q, k in enumerate(ch) if L_[q][6]]
                if trims_done: flags.append("trimmed lip sentences (row, ms kept, ms of sentence): " + str(trims_done))
            for ta, tb, ua, ub in pieces:
                mid = (ta + tb) / 2
                own = next((segs[j] for j in members if segs[j]["it_start_ms"] <= mid < segs[j]["it_end_ms"]), segs[k1])
                f = (tb - ta) / max(ub - ua, 1)
                out_s.append(dict(kind="clip", it_idx=own["it_idx"], it_start_ms=round(ta, 3), it_end_ms=round(tb, 3), us_start_ms=round(ua, 3),
                                  us_end_ms=round(ub, 3), it_text=own.get("it_text", ""), us_text=own.get("us_text", ""),
                                  stretch_factor=round(f, 3), note="lip group"))
                if f > 2.5 or f < 0.4: flags.append(f"IT#{own['it_idx']} piece plays x{f:.2f}")
            for j in members:                                 # members' footage = the pieces they own (for the free-footage checks)
                mine = [o for o in out_s if o["it_idx"] == segs[j]["it_idx"]]
                if mine: segs[j]["us_start_ms"], segs[j]["us_end_ms"] = min(o["us_start_ms"] for o in mine), max(o["us_end_ms"] for o in mine)
            nl = sum(1 for j in members if islip(j))
            it0, it1, us0, us1 = kn[0][0], kn[-1][0], kn[0][1], kn[-1][1]
            chain_groups.append(dict(rows=[segs[j]["it_idx"] for j in members], it0=it0, it1=it1, us0=us0, us1=us1, trims=[], flags=flags,
                                     note=(f"lip group: {nl} lip-sync rows + {len(members) - nl} narration rows as one continuous piece; "
                                           f"every lip sentence starts and ends with its dub voice"), removed=[], kept=us1 - us0,
                                     stretch=(it1 - it0) / max(us1 - us0, 1), removed_ms=0, segments=out_s, drift={}, max_drift=0,
                                     lip=True, lipgroup=True, why="lip group", first_seg=k0, break_before="lip group"))
        # A grouped row next to a lip-sync row now also carries the silence next to it. Instead of only slowing down, it
        # runs on into the FREE source footage between it and the lip clip (what comes right before/after in the source),
        # at most up to normal speed. Footage any other row uses is never taken.
        if a.exact_span:
            # Grouped (no-lip) rows play EXACTLY their own sentences' footage: a group = from its first row's source start to
            # its last row's source end (Whisper times), one continuous clip fitted to the dub time at one speed. No trimming,
            # no run-on into other footage (gap fill / bleed fix of the row-level manifest are not used for these rows).
            a.max_speedup = 1e9; a.no_lip_fill = True
            for k, s_ in enumerate(segs):
                r = rows[s_["it_idx"]]
                if k in inchain or islip(k) or s_["it_idx"] in repeat_rows or r["src_start"] is None: continue
                s_["us_start_ms"], s_["us_end_ms"] = r["src_start"], r["src_end"]
        def free(a0, a1, me):
            return a1 - a0 > 1 and not any(o is not me and min(o["us_end_ms"], a1) - max(o["us_start_ms"], a0) > 1 for o in segs)
        lipfill = []
        k = 0
        while k < len(segs) and not a.no_lip_fill:               # each run of grouped rows between lip-sync rows
            if islip(k) or k in inchain: k += 1; continue
            j = k
            while j < len(segs) and not (islip(j) or j in inchain): j += 1
            first, last = segs[k], segs[j - 1]
            run = segs[k:j]; k = j
            if any(s_["it_idx"] in repeat_rows for s_ in (first, last)): continue
            pl = segs.index(first) > 0; nl = j < len(segs)
            span = last["us_end_ms"] - first["us_start_ms"]          # a run plays as continuous footage (one or more groups)
            if span <= 0 or any(y["us_start_ms"] < x["us_end_ms"] - 40 for x, y in zip(run, run[1:])): continue
            need = (last["it_end_ms"] - first["it_start_ms"]) - span
            if not (pl or nl) or need <= 40: continue             # only where the run would play slower than normal
            p_, n_ = (segs[segs.index(first) - 1] if pl else None), (segs[j] if nl else None)
            rb = first["us_start_ms"] - p_["us_end_ms"] if p_ else 0
            ra = n_["us_start_ms"] - last["us_end_ms"] if n_ else 0
            rb = rb if rb > 0 and free(p_["us_end_ms"], first["us_start_ms"], first) else 0
            ra = ra if ra > 0 and free(last["us_end_ms"], n_["us_start_ms"], last) else 0
            ta = min(ra, need / 2); tb = min(rb, need - ta); ta = min(ra, need - tb)
            if ta + tb > 1:
                first["us_start_ms"] -= tb; last["us_end_ms"] += ta
                lipfill.append((first["it_idx"] if first is last else f"{first['it_idx']}-{last['it_idx']}", round(tb), round(ta)))
    if not lip: chains, inchain, chain_groups, lipfill, single_trims = [], set(), [], [], []
    n = len(segs); brk = {}
    for k in range(1, n):                                     # places a group may NEVER span
        x, y = segs[k - 1], segs[k]; g0, g1 = x["us_end_ms"], y["us_start_ms"]
        if x["it_idx"] in lip or y["it_idx"] in lip:
            brk[k] = "lip-sync row"
        elif x["it_idx"] in repeat_rows or y["it_idx"] in repeat_rows:
            brk[k] = "dub repeat"
        elif g1 < g0 - 40:
            brk[k] = "the Italian goes back in the US order"
        elif g1 - g0 > a.max_gap:
            brk[k] = f"US footage skips {(g1 - g0) / 1000:.1f}s here"
        elif any(j not in (k - 1, k) and s["it_idx"] not in repeat_rows and s["us_start_ms"] < g1 - 1 and s["us_end_ms"] > g0 + 1
                 for j, s in enumerate(segs)):
            brk[k] = "the US footage between these rows is used elsewhere"
    runs, cur = [], []
    for k in range(n):                                        # lip groups are already planned: never inside a block
        if k in inchain:
            if cur: runs.append(cur); cur = []
            continue
        if k in brk and cur: runs.append(cur); cur = []
        cur.append(k)
    if cur: runs.append(cur)
    queue = []
    for run in runs:                                          # balanced blocks of about --size rows
        m = len(run); kk = math.ceil(m / a.size); base, extra = divmod(m, kk); i = 0
        for g in range(kk):
            sz = base + (1 if g < extra else 0); queue.append((run[i:i + sz], None)); i += sz
    groups = []
    while queue:
        members, why = queue.pop(0)
        info = _group_plan([segs[j] for j in members], rows, a, repeat_rows, lip)
        if info["max_drift"] > a.max_drift and len(members) >= 4:
            h = len(members) // 2; tag = f"split: picture drifted {info['max_drift'] / 1000:.1f}s from the voice"
            queue[0:0] = [(members[:h], tag), (members[h:], tag)]
            continue
        info["why"] = why or ""; info["first_seg"] = members[0]
        groups.append(info)
    groups += chain_groups
    groups.sort(key=lambda g: g["it0"])
    for g in groups:
        k = g["first_seg"]
        if "break_before" not in g: g["break_before"] = brk.get(k, "start" if k == 0 else f"block of {a.size} rows")
    out = [s for g in groups for s in g["segments"]]
    for x, y in zip(out, out[1:]):
        assert abs(x["it_end_ms"] - y["it_start_ms"]) < 1, "group timeline not contiguous"
    assert abs(out[0]["it_start_ms"]) < 1 and abs(out[-1]["it_end_ms"] - total_ms) < 1
    gi = {id(s): i for i, g in enumerate(groups) for s in g["segments"]}
    clash = [(x["it_idx"], y["it_idx"]) for k, x in enumerate(out) for y in out[k + 1:]
             if gi[id(x)] != gi[id(y)] and x["it_idx"] not in repeat_rows and y["it_idx"] not in repeat_rows
             and min(x["us_end_ms"], y["us_end_ms"]) - max(x["us_start_ms"], y["us_start_ms"]) > 1]
    if clash: raise SystemExit(f"two groups would play the same US footage: {clash[:5]}")
    save(a.work, "manifest_groups.json", dict(total_ms=total_ms, segments=out, bleed_report=[],
         groups=[{k: v for k, v in g.items() if k != "segments"} for g in groups]))
    review = a.review or (re.sub(r"_[a-z]{2}_[a-z]{2}_mapping[^/]*\.xlsx$", "_dynamic_groups_review.xlsx", cfg["sheet_out"])
                          if cfg.get("sheet_out") and re.search(r"_[a-z]{2}_[a-z]{2}_mapping[^/]*\.xlsx$", cfg["sheet_out"]) else os.path.join(a.work, "dynamic_groups_review.xlsx"))
    _groups_workbook(review, groups, rows, cfg.get("labels", "IT,US").split(","))
    cfg.update(manifest="manifest_groups.json", groups_review=os.path.abspath(review)); save(a.work, "config.json", cfg)
    n_trim = sum(1 for g in groups if g["removed_ms"] > 1)
    print(f"groups: {len(groups)} groups over {n} rows ({len(out)} segments) covering 0-{total_ms / 1000:.2f}s, 0 shared footage")
    print(f"  {n_trim} groups trimmed ({sum(max(g['removed_ms'], 0) for g in groups) / 1000:.1f}s of extra US removed), "
          f"{sum(1 for g in groups if g['stretch'] > 1.001)} stretched; worst stretch {max(g['stretch'] for g in groups):.2f}x slower, "
          f"fastest {1 / min(g['stretch'] for g in groups):.2f}x; max picture/voice drift {max(g['max_drift'] for g in groups) / 1000:.2f}s")
    for i, g in enumerate(groups):
        print(f"  G{i:02d} IT#{g['rows'][0]}-{g['rows'][-1]} it {g['it0'] / 1000:.1f}-{g['it1'] / 1000:.1f}s  us {g['us0'] / 1000:.1f}-"
              f"{g['us1'] / 1000:.1f}s  removed {max(g['removed_ms'], 0) / 1000:.2f}s  x{g['stretch']:.3f}  drift {g['max_drift'] / 1000:.2f}s"
              + (f"  [{g['break_before']}]" if not g["break_before"].startswith("block") else "")
              + ("  ** " + "; ".join(g["flags"]) if g["flags"] else ""))
    if lip:
        print(f"  {sum(1 for g in groups if g.get('lip') and not g.get('lipgroup'))} single lip-sync rows play their own clip; "
              f"{len(chain_groups)} lip groups play {sum(len(g['rows']) for g in chain_groups)} rows as continuous pieces "
              f"({sum(1 for g in chain_groups for r in g['rows'] if r in lip)} lip-sync + "
              f"{sum(1 for g in chain_groups for r in g['rows'] if r not in lip)} narration rows)")
        tr_ = [f for g in chain_groups for f in g["flags"] if f.startswith("trimmed")]
        if single_trims or tr_: print(f"  lip sentences trimmed to their lip part (max x{a.lip_max_speed}): single rows {single_trims}; in lip groups {tr_}")
        if lipfill: print(f"  {len(lipfill)} grouped rows next to a lip-sync row ran on into free source footage instead of slowing down "
                          f"(row, ms before, ms after): {lipfill}")
    print(f"saved manifest_groups.json (render/verify/bookends now use it) and {review}")

def _groups_workbook(path, groups, rows, L):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Dynamic groups"
    hf = Font(name="Arial", size=11, bold=True, color="FFFFFF"); hfill = PatternFill("solid", start_color="1F4E78")
    bf = Font(name="Arial", size=10); wrap = Alignment(wrap_text=True, vertical="top")
    yel = PatternFill("solid", start_color="FFEB9C")
    hdr = ["Group", f"{L[0]} rows", f"{L[0]} start", f"{L[0]} end", f"{L[0]} duration (s)", f"{L[1]} continuous span",
           f"{L[1]} span (s)", "Extra removed (s)", f"Removed {L[1]} intervals", "Trimmed rows (class, s)",
           "Whole-group stretch (>1 slower)", "Max picture/voice drift (s)", "Group starts because", "Flags / notes"]
    for ci, h in enumerate(hdr, 1):
        c = ws.cell(1, ci, h); c.font = hf; c.fill = hfill; c.alignment = wrap
    for ri, g in enumerate(groups, 2):
        vals = [f"G{ri - 2:02d}", f"{g['rows'][0]}-{g['rows'][-1]}", fmt(g["it0"]), fmt(g["it1"]), round((g["it1"] - g["it0"]) / 1000, 3),
                f"{fmt(g['us0'])}-{fmt(g['us1'])}", round((g["us1"] - g["us0"]) / 1000, 3), round(max(g["removed_ms"], 0) / 1000, 3),
                " | ".join(f"{fmt(lo)}-{fmt(hi)}" for lo, hi in g.get("removed", [])),
                " | ".join(f"IT#{t['row']} {t['cls']} {t['ms'] / 1000:.2f}" for t in g["trims"]),
                round(g["stretch"], 3), round(g["max_drift"] / 1000, 2), g["why"] or g["break_before"],
                "; ".join(g["flags"] + ([g["note"]] if g["note"] else []))]
        for ci, v in enumerate(vals, 1):
            c = ws.cell(ri, ci, v); c.font = bf; c.alignment = wrap
            if g["flags"]: c.fill = yel
    for ci, w in enumerate([7, 9, 11, 11, 10, 22, 9, 9, 40, 40, 12, 12, 30, 50], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
    ws.freeze_panes = "A2"
    ws2 = wb.create_sheet("Rows")
    hdr2 = ["#", "Group", f"{L[0]} start", f"{L[0]} end", f"{L[0]} text", f"{L[1]} text", "Class", "Trimmed (s)",
            "Picture vs voice (s, + = picture late)"]
    for ci, h in enumerate(hdr2, 1):
        c = ws2.cell(1, ci, h); c.font = hf; c.fill = hfill; c.alignment = wrap
    ri = 2
    for gi, g in enumerate(groups):
        tr = {t["row"]: t for t in g["trims"]}
        for idx in g["rows"]:
            r = rows[idx]
            vals = [idx, f"G{gi:02d}", fmt(r["dub_start"]), fmt(r["dub_end"]), r["dub_text"], r.get("src_text") or "",
                    speech_or_narration(r.get("src_text") or ""), round(tr[idx]["ms"] / 1000, 2) if idx in tr else "",
                    round(g["drift"].get(idx, 0) / 1000, 2)]
            for ci, v in enumerate(vals, 1):
                c = ws2.cell(ri, ci, v); c.font = bf; c.alignment = wrap
            ri += 1
    for ci, w in enumerate([6, 7, 11, 11, 45, 45, 18, 10, 14], 1):
        ws2.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
    ws2.freeze_panes = "A2"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True); wb.save(path)

# ---------------------------------------------------------------------------------
# 11. translate: the dub SRT -> English SRT through Google, sentence by sentence, mapped back onto the
#     original cards (same count, same times). Cached, batched, names kept as in the dub.
# ---------------------------------------------------------------------------------
class TranslateBlocked(Exception):
    pass

def _http(url, params=None, json_body=None, timeout=30):
    import urllib.request, urllib.parse
    if params: url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    hdr = {"User-Agent": "Mozilla/5.0 (Macintosh) dubstitch"}; body = None
    if json_body is not None:
        body = json.dumps(json_body).encode(); hdr["Content-Type"] = "application/json; charset=utf-8"
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=hdr), timeout=timeout) as r:
        return r.read().decode("utf-8")

def _with_retry(fn, what):
    import urllib.error
    for attempt in range(4):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt == 3:
                raise TranslateBlocked(f"Google refused {what} (HTTP {e.code}). The free endpoint blocks an address after many "
                                       "requests; wait an hour, or use --engine cloud with an API key, or --export-todo/--import.")
            if e.code not in (403, 429, 500, 502, 503): raise
        except (OSError, ValueError):
            if attempt == 3: raise
        time.sleep(3 * 2 ** attempt)

def _gtx_batch(texts, sl, tl):
    """Free Google endpoint (client=gtx). Several sentences per request, one per line."""
    def one():
        raw = _http("https://translate.googleapis.com/translate_a/single",
                    params=dict(client="gtx", sl=sl, tl=tl, dt="t", q="\n".join(texts)))
        data = json.loads(raw)
        return "".join(seg[0] for seg in data[0] if seg and seg[0]).split("\n")
    out = [o.strip() for o in _with_retry(one, f"{len(texts)} sentences")]
    if len(out) != len(texts):                                # Google merged lines: fall back to one per request
        out = []
        for t in texts:
            out += _gtx_batch([t], sl, tl) if len(texts) > 1 else [""]
            time.sleep(0.3)
    return out

def _cloud_batch(texts, sl, tl, key):
    """Official Cloud Translation API v2 (needs an API key)."""
    raw = _with_retry(lambda: _http("https://translation.googleapis.com/language/translate/v2", params=dict(key=key),
                                    json_body=dict(q=texts, source=sl, target=tl, format="text")), f"{len(texts)} sentences")
    return [t["translatedText"] for t in json.loads(raw)["data"]["translations"]]

def translate_texts(texts, a, cache, cpath):
    todo = [t for t in dict.fromkeys(texts) if t not in cache]
    if not todo: return 0
    if a.engine == "file":
        raise SystemExit(f"{len(todo)} sentences have no translation in --import; e.g. {todo[:2]}")
    key = a.api_key or os.environ.get("GOOGLE_TRANSLATE_API_KEY")
    if a.engine == "cloud" and not key:
        raise SystemExit("--engine cloud needs --api-key or the GOOGLE_TRANSLATE_API_KEY environment variable")
    batches, cur = [], []
    for t in todo:                                             # keep each request URL comfortably short
        if cur and (len("\n".join(cur + [t]).encode()) > a.max_chars or len(cur) >= 100):
            batches.append(cur); cur = []
        cur.append(t)
    if cur: batches.append(cur)
    for b in batches:
        res = _cloud_batch(b, a.sl, a.tl, key) if a.engine == "cloud" else _gtx_batch(b, a.sl, a.tl)
        cache.update(zip(b, res)); json.dump(cache, open(cpath, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        time.sleep(a.pause)
    return len(batches)

def split_by_shares(text, shares):
    """Split a translated sentence into len(shares) consecutive parts, sized like the dub cards it spans,
    at word boundaries, preferring a boundary right after punctuation."""
    k = len(shares)
    if k == 1: return [text]
    words = text.split()
    if len(words) <= k: return words + [""] * (k - len(words))
    ends, pos = [], 0
    for w in words[:-1]:
        pos += len(w); ends.append(pos); pos += 1
    L = len(text); tot = sum(shares) or 1; cum = 0; cuts = []; prev = -1
    for j in range(k - 1):
        cum += shares[j]; target = cum / tot * L
        lo, hi = prev + 1, len(ends) + 1 - (k - 1 - j)          # leave at least one word for every later part
        best = min(range(lo, hi), key=lambda i: abs(ends[i] - target) / L - (0.06 if re.search(r"[,;:.!?»”\"]$", words[i]) else 0))
        cuts.append(best); prev = best
    parts, start = [], 0
    for c in cuts:
        parts.append(" ".join(words[start:c + 1])); start = c + 1
    parts.append(" ".join(words[start:]))
    return parts

def proper_names(sentences):
    """Character names in the dub: capitalised words that recur mid-sentence, never appear in lower case, and stand
    on their own at least once (or only ever follow such a name, as a surname: 'Emma Reali'). Words that only appear inside a
    longer capitalised name ('Gruppo Reali', 'Polo dell'Innovazione Lombardo') are left to the translation."""
    cnt, lower, alone, after, before = defaultdict(int), set(), set(), defaultdict(set), set()
    isname = lambda w: len(w) > 2 and w[0].isupper() and w[1:].islower()
    for s in sentences:
        for t in re.findall(r"[^\W\d_]+", s):
            if t.islower(): lower.add(t)
        words = [re.sub(r"^[^\W\d_]*'|[^\w]+$|^[^\w]+", "", w) for w in s.split()]
        for i, w in enumerate(words):
            if not isname(w): continue
            if i > 0: cnt[w] += 1
            prev = words[i - 1] if i else ""; nxt = words[i + 1] if i + 1 < len(words) else ""
            if not isname(nxt) and not (i and isname(prev)): alone.add(w)
            if i and isname(prev): after[w].add(prev)
            if isname(nxt): before.add(w)
    cand = {t for t, c in cnt.items() if c >= 2 and t.lower() not in lower}
    return sorted(t for t in cand if t in alone or (after[t] & alone & cand and t not in before))

def cmd_translate(a):
    cfg = load(a.work, "config.json")
    src = a.srt or cfg["dub_srt"]
    cues = parse_srt(src)                                      # same cleaning as the burned dub subtitles
    pieces, spans, pos = [], [], 0
    for c in cues:
        if pieces: pieces.append(" "); pos += 1
        spans.append((pos, pos + len(c.text))); pieces.append(c.text); pos += len(c.text)
    full = "".join(pieces)
    # a closing quote stays with its own sentence: «...Federico!» | Priscilla ...
    bounds = sorted(set([0] + [m.start() + len(m.group(0).rstrip()) for m in _SENT_END.finditer(full)] + [len(full)]))
    sents = []
    for s0, s1 in zip(bounds, bounds[1:]):
        t = full[s0:s1]; l = len(t) - len(t.lstrip()); t = t.strip()
        if t: sents.append((s0 + l, s0 + l + len(t), t))
    cpath = os.path.join(a.work, f"translations_{a.sl}_{a.tl}.json")
    cache = json.load(open(cpath, encoding="utf-8")) if os.path.exists(cpath) else {}
    if a.import_file:
        if a.import_file.endswith(".json"): cache.update(json.load(open(a.import_file, encoding="utf-8")))
        else:
            for ln in open(a.import_file, encoding="utf-8"):
                if "\t" in ln:
                    k, v = ln.rstrip("\n").split("\t", 1)
                    if v.strip(): cache[k] = v.strip()
        json.dump(cache, open(cpath, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    texts = [t for _, _, t in sents]
    names = proper_names(texts) if a.keep_names else []
    if a.export_todo:
        todo = [t for t in dict.fromkeys(texts + names) if t not in cache]
        open(a.export_todo, "w", encoding="utf-8").write("".join(f"{t}\t\n" for t in todo))
        print(f"wrote {len(todo)} untranslated lines to {a.export_todo} (fill the 2nd column, then --engine file --import it)"); return
    try:
        nreq = translate_texts(texts + names, a, cache, cpath)
    except TranslateBlocked as e:
        raise SystemExit(f"TRANSLATION BLOCKED: {e}\n(progress so far is cached in {cpath})")
    gloss = {}
    if a.glossary:
        for ln in open(a.glossary, encoding="utf-8"):
            if "\t" in ln:
                k, v = ln.rstrip("\n").split("\t", 1); gloss[k.strip()] = v.strip()
    name_map = {}
    for nm in names:                                           # e.g. Google turns Federico into Frederick: undo it
        en = cache.get(nm, nm).strip(" .")
        if en != nm and re.fullmatch(r"[A-Z][^\W\d_]+", en): name_map[en] = nm
    ctx_fixed = defaultdict(int)
    def fix(t, it_sent=""):
        for en, it in name_map.items():                       # only where the Italian sentence has that name
            if it in it_sent: t = re.sub(rf"\b{re.escape(en)}\b", it, t)
        for nm in names:                                       # in context Google may still anglicise a name
            if nm in it_sent and nm not in t:                  # (Federico -> Frederick): put the dub's name back
                cands = [w for w in set(re.findall(r"\b[A-Z][^\W\d_]{2,}\b", t)) if w not in it_sent
                         and difflib.SequenceMatcher(None, w.lower(), nm.lower()).ratio() >= 0.6]
                if len(cands) == 1:
                    t = re.sub(rf"\b{re.escape(cands[0])}\b", nm, t); ctx_fixed[f"{cands[0]}->{nm}"] += 1
        for k, v in gloss.items(): t = re.sub(rf"\b{re.escape(k)}\b", v, t)
        return t
    per_cue = [[] for _ in cues]
    for s0, s1, t in sents:
        hit = [(i, min(s1, c1) - max(s0, c0)) for i, (c0, c1) in enumerate(spans) if min(s1, c1) > max(s0, c0)]
        parts = split_by_shares(fix(cache[t], t), [max(n, 1) for _, n in hit])
        for (i, _), p in zip(hit, parts):
            if p: per_cue[i].append(p)
    lines, empty = [], 0
    for i, c in enumerate(cues):
        txt = " ".join(per_cue[i]).strip()
        if not txt:
            empty += 1; txt = " ".join(per_cue[i - 1]) if i else " ".join(per_cue[i + 1] if i + 1 < len(cues) else [])
        lines.append(f"{i + 1}\n{_srt_ts(c.start_ms)} --> {_srt_ts(c.end_ms)}\n{txt}\n")
    out = os.path.abspath(a.out); os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(lines))
    cfg = load(a.work, "config.json")
    cfg["translation"] = dict(out=out, source=os.path.abspath(src), engine=a.engine, sl=a.sl, tl=a.tl, cues=len(cues),
                              sentences=len(sents), names_kept=name_map, names_fixed_in_context=dict(ctx_fixed))
    save(a.work, "config.json", cfg)
    print(f"translated {len(sents)} sentences ({a.sl}->{a.tl}, engine {a.engine}, {nreq} requests, rest from cache) onto "
          f"{len(cues)} cards with the dub's exact times -> {out}")
    if name_map or ctx_fixed: print(f"names kept as in the dub: {name_map} {dict(ctx_fixed) if ctx_fixed else ''}")
    if empty: print(f"{empty} cards got no words of their own (very short card): they repeat the neighbouring text")
    if a.engine == "gtx": print("note: text was sent to Google's free translate endpoint (translate.googleapis.com, client=gtx)")

def _srt_ts(ms):
    ms = int(round(ms)); return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

# ---------------------------------------------------------------------------------
# 12. check: final verification of the whole episode package
# ---------------------------------------------------------------------------------
def cmd_check(a):
    from PIL import Image, ImageDraw
    cfg = load(a.work, "config.json"); bad = []
    clean = cfg.get("clean_final"); subs = cfg.get("subs_outputs") or {}
    vids = ([("clean", clean)] if clean else []) + [(os.path.basename(v["srt"]), p) for p, v in subs.items()]
    if not clean or not os.path.exists(clean): bad.append("no clean master (run `bookends --out ...`)")
    if len([p for p in subs if os.path.exists(p)]) < a.expect_subs: bad.append(f"only {len(subs)} subtitled videos, expected {a.expect_subs}")
    ref = probe_duration_ms(clean) if clean and os.path.exists(clean) else None
    _, _, FPS = probe_video(cfg["src_video"])
    for name, p in vids:
        if not os.path.exists(p): bad.append(f"missing {p}"); continue
        st = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", p],
                            capture_output=True, text=True).stdout.split()
        d = probe_duration_ms(p)
        ok = "video" in st and "audio" in st and (ref is None or abs(d - ref) <= 2 * 1000 / FPS + 60)
        print(f"{'OK ' if ok else 'BAD'} {os.path.basename(p)}: {d / 1000:.3f}s, streams {sorted(set(st))}")
        if not ok: bad.append(f"{os.path.basename(p)}: streams {st}, {d} ms vs clean {ref} ms")
    for label, p in (("mapping sheet", cfg.get("sheet_out")), ("dynamic-group review", cfg.get("groups_review")),
                     ("translated SRT", (cfg.get("translation") or {}).get("out"))):
        good = bool(p) and os.path.exists(p)
        print(f"{'OK ' if good else 'BAD'} {label}: {p}")
        if not good: bad.append(f"{label} missing")
    if vids and ref:
        cues = parse_srt(cfg["dub_srt"]); c = cues[min(2, len(cues) - 1)]
        t = cfg.get("opening_s", 0) + (c.start_ms + c.end_ms) / 2000
        sheet = Image.new("RGB", (len(vids) * 400, 430), "white"); dr = ImageDraw.Draw(sheet)
        for k, (name, p) in enumerate(vids):
            png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", p, "-frames:v", "1", "-vf", "scale=400:400",
                                  "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
            if png: sheet.paste(Image.open(io.BytesIO(png)).convert("RGB"), (k * 400, 30))
            dr.text((k * 400 + 6, 8), f"{name} @ {t:.1f}s", fill="black")
        qp = os.path.join(a.work, "final_check.jpg"); sheet.save(qp, quality=85)
        print(f"same moment in every video (look at it: clean = no strip, others = their subtitle): {qp}")
    print("FINAL CHECK " + ("PASSED" if not bad else "FAILED:\n  " + "\n  ".join(bad)))
    if bad: sys.exit(1)

# ---------------------------------------------------------------------------------
# 13. speech: which source lines are spoken on screen (direct speech in quotes) vs narration
# ---------------------------------------------------------------------------------
_Q_OPEN, _Q_TOGGLE = "„", "\"“”«»"     # „ always opens; " “ ” « » toggle

def quote_shares(texts):
    """Share of letters inside quotation marks for each text, reading them in order (a quote may span lines).
    A quote mark opens or closes by position (after a space = opens, after a word = closes), so a quote mark
    missing in the SRT cannot flip the rest of the episode. A closing mark with no open quote counts the line
    up to it as speech (the opening mark was missing)."""
    inside, out = False, []
    for t in texts:
        n_in = n_all = 0; first = last = None
        for k, ch in enumerate(t):
            if ch == _Q_OPEN or ch in _Q_TOGGLE:
                prev = t[k - 1] if k else " "; nxt = t[k + 1] if k + 1 < len(t) else " "
                if ch == _Q_OPEN or (prev.isspace() or prev in ":(-\u2013\u2014") and not nxt.isspace():
                    inside = True
                elif not prev.isspace():
                    if not inside and n_all:                     # closing mark, but no quote was open
                        n_in = n_all; first = 0; last = k - 1
                    inside = False
                else:
                    inside = not inside
                continue
            if ch.isalpha():
                n_all += 1
                if inside:
                    n_in += 1; first = k if first is None else first; last = k
        out.append((n_in / max(n_all, 1), first, last, len(t)))
    return out

def cmd_speech(a):
    """Classify every SOURCE sentence: 'speech' (a character says it -> lips may move on screen), 'mixed'
    (quote + narration in one sentence) or 'narration'. Writes <work>/speech.json for lipdetect.py."""
    P = load(a.work, "prep.json"); src = [Cue(**c) for c in P["src"]]
    rows = []
    for j, (c, (sh, f, l, n)) in enumerate(zip(src, quote_shares([c.text for c in src]))):
        cls = "speech" if sh >= a.speech_share else ("mixed" if sh >= a.mixed_share else "narration")
        d = c.end_ms - c.start_ms
        # time span of the quoted words, estimated from their position in the line (lipdetect searches the whole line anyway)
        q0 = c.start_ms + d * (f or 0) / max(n, 1); q1 = c.start_ms + d * ((l if l is not None else n - 1) + 1) / max(n, 1)
        rows.append(dict(idx=j, start_ms=c.start_ms, end_ms=c.end_ms, text=c.text, quote_share=round(sh, 2), cls=cls,
                         quote_ms=[round(q0), round(q1)] if cls != "narration" else None))
    dp = os.path.join(a.work, "decisions.json")                       # the person's reading wins: "speech": {"20": "mixed"}
    over = {int(k): v for k, v in (json.load(open(dp, encoding="utf-8")).get("speech", {}) if os.path.exists(dp) else {}).items()}
    for j, v in over.items():
        if v not in ("speech", "mixed", "narration"): raise SystemExit(f"decisions.json speech: SRC#{j} -> {v!r}?")
        rows[j]["cls"] = v; rows[j]["override"] = True
        if v != "narration" and not rows[j]["quote_ms"]: rows[j]["quote_ms"] = [rows[j]["start_ms"], rows[j]["end_ms"]]
    save(a.work, "speech.json", dict(rows=rows, src_video=load(a.work, "config.json")["src_video"]))
    n = defaultdict(int)
    for r in rows: n[r["cls"]] += 1
    tot = defaultdict(float)
    for r in rows: tot[r["cls"]] += (r["end_ms"] - r["start_ms"]) / 1000
    print(f"source lines: {n['speech']} speech ({tot['speech']:.0f}s), {n['mixed']} mixed ({tot['mixed']:.0f}s), "
          f"{n['narration']} narration ({tot['narration']:.0f}s) -> {os.path.join(a.work, 'speech.json')}")
    for r in rows:
        if r["cls"] != "narration":
            print(f"  SRC#{r['idx']:3d} {r['cls']:6s} {r['quote_share']:.2f}{' (set by hand)' if r.get('override') else ''} [{fmt(r['start_ms'])}-{fmt(r['end_ms'])}] {r['text'][:80]!r}")

# ---------------------------------------------------------------------------------
# 14. lipsync: dub rows whose source footage shows the character's lips moving keep their OWN clip
#     (out of the 7-8-row groups), timed so the lip movement plays while the dub line is spoken.
#     Needs <work>/speech.json (`speech`) and <work>/lips/*.json (lipdetect.py: TalkNet-ASD + Face Mesh).
# ---------------------------------------------------------------------------------
def lip_intervals(d, a):
    """Talking stretches (source seconds) inside one source line, from the per-frame measures of lipdetect.py:
    lips visibly moving (Face Mesh opening changes) OR TalkNet says a face speaks (in sync, or by picture alone)."""
    import numpy as np
    n = len(d.get("scores") or []); t0 = d["window"][0]
    if not n: return []
    on = np.zeros(n, bool)
    if a.use in ("mouth", "all") and d.get("mouth"): on |= np.array(d["mouth"][:n] + [0] * (n - len(d["mouth"][:n]))) >= a.mouth_thr
    if a.use in ("talknet", "all"):
        on |= np.array(d["scores"]) >= 0
        if d.get("visual"): on |= np.array(d["visual"][:n]) >= 0
    lo = max(int(round((d["start_s"] - t0) * 25)), 0); hi = min(int(round((d["end_s"] - t0) * 25)), n)
    ivs, k = [], lo
    while k < hi:
        if not on[k]: k += 1; continue
        j = k
        while j < hi and on[j]: j += 1
        if ivs and k - ivs[-1][1] <= a.merge * 25: ivs[-1][1] = j
        else: ivs.append([k, j])
        k = j
    return [(t0 + x / 25, t0 + y / 25) for x, y in ivs if y - x >= a.min_len * 25]

def merged_lip_intervals(work, a):
    """All lipdetect.py --range results in <work>/lips_range (any pieces of the source video) -> the talking stretches
    (seconds) over the whole video, and which 1/25-s frames were analysed at all."""
    import numpy as np
    rd = os.path.join(work, "lips_range")
    files = sorted(f for f in os.listdir(rd) if f.startswith("range_") and f.endswith(".json")) if os.path.isdir(rd) else []
    if not files: raise SystemExit("no lipdetect.py --range results in <work>/lips_range -- scan the source video first")
    T = max(json.load(open(os.path.join(rd, f)))["window"][1] for f in files); n = int(T * 25) + 2
    sc, vi, mo = np.full(n, -9.0), np.full(n, -9.0), np.zeros(n); covered = np.zeros(n, bool)
    for f in files:
        d = json.load(open(os.path.join(rd, f))); k0 = int(round(d["window"][0] * 25))
        for arr, key in ((sc, "scores"), (vi, "visual"), (mo, "mouth")):
            v = np.array(d[key][:n - k0]); arr[k0:k0 + len(v)] = np.maximum(arr[k0:k0 + len(v)], v)
        covered[k0:k0 + len(d["scores"])] = True
    d = dict(window=[0, n / 25], start_s=0, end_s=n / 25, scores=list(sc), visual=list(vi), mouth=list(mo))
    return lip_intervals(d, a), covered

def cmd_lipsync(a):
    """THE VIDEO DECIDES (not the quote marks): a source sentence is 'speaking on screen' when the lip scan of the whole
    source video (lipdetect.py --range) finds >= --min-lip seconds of moving lips inside its Whisper time. Every dub row
    matched to such a sentence becomes a lip-sync row: it plays that sentence's COMPLETE clip (its exact Whisper time),
    on its own, outside the groups. Its slot is exactly its own spoken Italian time, so the clip starts with the voice;
    the silence next to it goes to the neighbouring grouped row (between two lip rows, the clip runs on into the free
    source footage around its sentence at the same speed)."""
    cfg = cfg_get(a)
    P = load(a.work, "prep.json"); src = [Cue(**c) for c in P["src"]]
    R = load(a.work, "rows.json"); rows = {r["idx"]: r for r in R["rows"]}; repeat_rows = {int(k) for k in R["repeat"]}
    M = load(a.work, "manifest.json"); segs = M["segments"]; src_total = probe_duration_ms(cfg["src_video"])
    iv, covered = merged_lip_intervals(a.work, a)
    sent = []
    for j, c in enumerate(src):
        s, e = c.start_ms / 1000, c.end_ms / 1000
        if not covered[int(s * 25):max(int(e * 25), int(s * 25) + 1)].all():
            raise SystemExit(f"source sentence #{j} ({s:.1f}-{e:.1f}s) is not covered by the lip scan yet -- finish lipdetect.py --range")
        hits = [(max(x, s), min(y, e)) for x, y in iv if min(y, e) - max(x, s) > 0]
        sec = sum(y - x for x, y in hits)
        sent.append(dict(idx=j, start_ms=c.start_ms, end_ms=c.end_ms, text=c.text, lips=[[round(x, 2), round(y, 2)] for x, y in hits],
                         lip_s=round(sec, 2), speaking=sec >= a.min_lip))
    speaking = {d["idx"] for d in sent if d["speaking"]}
    lipset = {s_["it_idx"] for s_ in segs if rows[s_["it_idx"]]["src"] is not None and s_["it_idx"] not in repeat_rows
              and any(j in speaking for j in range(rows[s_["it_idx"]]["src"], rows[s_["it_idx"]]["src_last"] + 1))}
    # footage already taken: grouped rows keep their manifest windows; lip rows their sentence (then their planned window)
    taken = {s_["it_idx"]: ((rows[s_["it_idx"]]["src_start"], rows[s_["it_idx"]]["src_end"]) if s_["it_idx"] in lipset
                            else (s_["us_start_ms"], s_["us_end_ms"])) for s_ in segs if s_["it_idx"] not in repeat_rows}
    plan = {}
    for k, s_ in enumerate(segs):
        i = s_["it_idx"]
        if i not in lipset: continue
        r = rows[i]; d0, d1 = r["dub_start"], r["dub_end"]
        prev_lip = k > 0 and segs[k - 1]["it_idx"] in lipset; next_lip = k + 1 < len(segs) and segs[k + 1]["it_idx"] in lipset
        s0 = s_["it_start_ms"] if (k == 0 or prev_lip) else d0       # own spoken time; silence -> the grouped neighbour
        s1 = s_["it_end_ms"] if (k == len(segs) - 1 or next_lip) else d1
        others = [w for o, w in taken.items() if o != i]
        u0, u1 = r["src_start"], r["src_end"]
        lo = max([e for b, e in others if e <= u0 + 1], default=0); hi = min([b for b, e in others if b >= u1 - 1], default=src_total)
        lo, hi = min(lo, u0), max(hi, u1)
        f = (d1 - d0) / max(u1 - u0, 1)                              # Italian voice length / German sentence length
        w0 = max(u0 - (d0 - s0) / f, lo); w1 = min(u1 + (s1 - d1) / f, hi)
        stretch = (s1 - s0) / max(w1 - w0, 1)
        off = s0 + (u0 - w0) * stretch - d0                          # German sentence start vs Italian voice start
        taken[i] = (w0, w1)
        js = list(range(r["src"], r["src_last"] + 1))
        lips = [l for j in js for l in sent[j]["lips"]]
        plan[str(i)] = dict(it_idx=i, slot=[round(s0, 3), round(s1, 3)], speech=[d0, d1], sentence=[round(u0, 3), round(u1, 3)],
                            src_lines=js, lips=lips, lip_s=round(sum(y - x for x, y in lips), 2), window=[round(w0, 3), round(w1, 3)],
                            stretch=round(stretch, 3), sentence_offset_ms=round(off),
                            note=(f"lip-sync row: DE sentence(s) {js} {fmt(u0)}-{fmt(u1)} (Whisper time), lips {lips}, "
                                  f"played whole at x{stretch:.2f}"))
    save(a.work, "lipsync.json", dict(settings=dict(rule="video", use=a.use, mouth_thr=a.mouth_thr, min_lip=a.min_lip),
                                      sentences=sent, intervals=[[round(x, 2), round(y, 2)] for x, y in iv], rows=plan))
    # ---- review workbook ----
    out = a.review or (re.sub(r"_[a-z]{2}_[a-z]{2}_mapping[^/]*\.xlsx$", "_lipsync_review.xlsx", cfg["sheet_out"]) if cfg.get("sheet_out")
                       else os.path.join(a.work, "lipsync_review.xlsx"))
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    L = cfg.get("labels", "IT,US").split(",")
    hf = Font(name="Arial", size=11, bold=True, color="FFFFFF"); hfill = PatternFill("solid", start_color="1F4E78")
    bf = Font(name="Arial", size=10); wrap = Alignment(wrap_text=True, vertical="top"); green = PatternFill("solid", start_color="C6EFCE")
    by_src = defaultdict(list)
    for r in rows.values():
        if r["src"] is not None:
            for j in range(r["src"], r["src_last"] + 1): by_src[j].append(r["idx"])
    def sheet(ws, hdr, data, widths, hi_col=None):
        for ci, h in enumerate(hdr, 1):
            c = ws.cell(1, ci, h); c.font = hf; c.fill = hfill; c.alignment = Alignment(wrap_text=True)
        for ri, vals in enumerate(data, 2):
            for ci, v in enumerate(vals, 1):
                c = ws.cell(ri, ci, v); c.font = bf; c.alignment = wrap
                if hi_col is not None and vals[hi_col] == "speaking on screen": c.fill = green
        for ci, w in enumerate(widths, 1): ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
        ws.freeze_panes = "A2"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = f"{L[1]} sentences (video)"
    sheet(ws, ["#", "Spoken start (s)", "Spoken end (s)", f"{L[1]} sentence", "Lip movement inside it (s)", "Seconds of lips",
               "Type after video", f"{L[0]} rows using it"],
          [[d["idx"], d["start_ms"] / 1000, d["end_ms"] / 1000, d["text"], ", ".join(f"{x:.2f}-{y:.2f}" for x, y in d["lips"]) or "-",
            d["lip_s"], "speaking on screen" if d["speaking"] else "no lip movement", ", ".join(map(str, by_src.get(d["idx"], []))) or "unused"]
           for d in sent], [6, 10, 10, 60, 26, 9, 18, 12], hi_col=6)
    ws2 = wb.create_sheet("Lip-sync rows")
    sheet(ws2, ["#", f"{L[0]} spoken", f"{L[0]} text", f"{L[1]} sentence(s)", f"{L[1]} text", f"{L[1]} sentence time (Whisper)",
                "Lips moving inside (s)", f"{L[1]} clip played", "Speed (>1 slower)", f"{L[1]} start vs {L[0]} voice start (s)"],
          [[p["it_idx"], f"{fmt(p['speech'][0])}-{fmt(p['speech'][1])}", rows[p["it_idx"]]["dub_text"], ", ".join(f"#{j}" for j in p["src_lines"]),
            rows[p["it_idx"]]["src_text"], f"{fmt(p['sentence'][0])}-{fmt(p['sentence'][1])}", p["lip_s"],
            f"{fmt(p['window'][0])}-{fmt(p['window'][1])}", p["stretch"], round(p["sentence_offset_ms"] / 1000, 2)]
           for p in sorted(plan.values(), key=lambda p: p["it_idx"])], [6, 19, 40, 10, 40, 19, 9, 19, 9, 11])
    ws3 = wb.create_sheet("Lip movements")
    sheet(ws3, ["#", "Start (s)", "End (s)", "Seconds", f"{L[1]} sentence(s)"],
          [[k + 1, round(x, 2), round(y, 2), round(y - x, 2),
            ", ".join(str(d["idx"]) for d in sent if min(y, d["end_ms"] / 1000) - max(x, d["start_ms"] / 1000) > 0) or "pause between sentences"]
           for k, (x, y) in enumerate(iv)], [6, 10, 10, 9, 20])
    wb.save(out)
    cfg["lipsync_review"] = os.path.abspath(out); save(a.work, "config.json", cfg)
    if cfg.get("sheet_out") and os.path.exists(cfg["sheet_out"]):          # mark every row in the mapping sheet too
        wb2 = openpyxl.load_workbook(cfg["sheet_out"]); ws = wb2["Mapping"]
        c = ws.cell(1, 11, f"Type after video ({L[1]} lips)"); c.font = hf; c.fill = hfill; c.alignment = Alignment(wrap_text=True)
        for rr in range(2, ws.max_row + 1):
            i = ws.cell(rr, 1).value
            if not isinstance(i, int): continue
            p = plan.get(str(i))
            c = ws.cell(rr, 11, f"speaking on screen ({p['lip_s']}s of lips) -> own clip {fmt(p['window'][0])}-{fmt(p['window'][1])}"
                        if p else "no lip movement -> grouped"); c.font = bf; c.alignment = wrap
            if p: c.fill = green
        ws.column_dimensions["K"].width = 34; wb2.save(cfg["sheet_out"])
    st = sorted(p["stretch"] for p in plan.values())
    print(f"lip scan: {len(iv)} stretches of moving lips ({sum(y - x for x, y in iv):.0f}s); {len(speaking)} of {len(sent)} {L[1]} sentences are "
          f"speaking on screen (>= {a.min_lip}s of moving lips inside their Whisper time)")
    print(f"lip-sync rows: {len(plan)} of {len(segs)} {L[0]} rows play their {L[1]} sentence's whole clip, outside the groups"
          + (f"; speed x{st[0]}..x{st[-1]} (median x{st[len(st) // 2]})" if st else ""))
    for p in sorted(plan.values(), key=lambda p: p["it_idx"]):
        print(f"  {L[0]}#{p['it_idx']:3d} {fmt(p['slot'][0])}-{fmt(p['slot'][1])} <- {L[1]}#{p['src_lines']} sentence {p['sentence'][0] / 1000:.2f}-"
              f"{p['sentence'][1] / 1000:.2f}s, clip {p['window'][0] / 1000:.2f}-{p['window'][1] / 1000:.2f}s x{p['stretch']}"
              + (f"  start offset {p['sentence_offset_ms'] / 1000:+.2f}s" if abs(p['sentence_offset_ms']) >= 40 else "")
              + f"  {rows[p['it_idx']]['dub_text'][:45]!r}")
    unused_sp = [d["idx"] for d in sent if d["speaking"] and not by_src.get(d["idx"])]
    if unused_sp: print(f"speaking {L[1]} sentences no {L[0]} row uses: {unused_sp}")
    print(f"saved lipsync.json and {out}; run `groups` next")

def cmd_listen(a):
    from faster_whisper import WhisperModel
    cfg = load(a.work, "config.json")
    clip = os.path.join(a.work, "listen.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(a.start), "-to", str(a.end), "-i", cfg["dub_audio"],
                    "-ac", "1", "-ar", "16000", clip], check=True)
    segs, _ = WhisperModel("small", device="cpu", compute_type="int8").transcribe(clip, language=a.lang)
    for s in segs:
        print(f"[{fmt((a.start + s.start) * 1000)} - {fmt((a.start + s.end) * 1000)}] {s.text.strip()}")

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    x = sp.add_parser("analyze"); x.add_argument("--work", required=True)
    for k in ("--dub-srt", "--dub-audio", "--src-srt", "--src-video"): x.add_argument(k, required=True)
    x.add_argument("--models")
    x.add_argument("--src-srt-audio", help="audio the source SRT was timed to (default: a .wav/.mp3/... with the SRT's name, if present)")
    x.add_argument("--src-offset", type=float, help="seconds to move the source SRT EARLIER (skips measuring; use only after checking)")
    x.add_argument("--no-word-times", action="store_true", help="ignore sentences_<side>.json from `words` (use the SRT-card estimates)")
    x.add_argument("--clauses", type=int, help="split sentences into clauses of at least N words (0 = whole sentences); same N as `words`")
    x = sp.add_parser("words"); x.add_argument("--work", required=True); x.add_argument("--side", choices=["src", "dub"], default="src")
    x.add_argument("--srt"); x.add_argument("--media"); x.add_argument("--lang"); x.add_argument("--models")
    x.add_argument("--model", default="small", help="Whisper model (small is enough: the words only give times, the text comes from the SRT)")
    x.add_argument("--first", type=int, help="only the first N sentences (tests)"); x.add_argument("--until", type=float)
    x.add_argument("--max-seconds", type=float, default=1e9); x.add_argument("--out", help="also write a sentence/word workbook")
    x.add_argument("--clauses", type=int, help="split sentences into clauses of at least N words (0 = whole sentences); saved for this work folder")
    x = sp.add_parser("lipmark"); x.add_argument("--work", required=True); x.add_argument("--side", default="src")
    x.add_argument("--out", help="the workbook from `words --out` to add the video columns to")
    x.add_argument("--use", choices=["all", "mouth", "talknet"], default="all"); x.add_argument("--mouth-thr", type=float, default=0.007)
    x.add_argument("--merge", type=float, default=0.3); x.add_argument("--min-len", type=float, default=0.4)
    x.add_argument("--min-lip", type=float, default=0.4, help="a sentence counts as speaking on screen with this many seconds of moving lips")
    x = sp.add_parser("sheet"); x.add_argument("--work", required=True); x.add_argument("--out", required=True)
    x.add_argument("--decisions"); x.add_argument("--labels", default="IT,US", help="column labels: dub,source")
    x = sp.add_parser("manifest"); x.add_argument("--work", required=True)
    x.add_argument("--fill-gaps", type=int, default=1000, help="join neighbouring clips across source gaps up to this many ms (0 = off)")
    x = sp.add_parser("groups"); x.add_argument("--work", required=True)
    x.add_argument("--size", type=int, default=8, help="rows per group at most (balanced; TOLR uses 7)")
    x.add_argument("--max-gap", type=int, default=2000, help="never span a US jump longer than this (ms)")
    x.add_argument("--min-keep", type=int, default=2000, help="keep at least this much of every trimmed row (ms)")
    x.add_argument("--min-cut", type=int, default=300, help="no cut smaller than this (ms); smaller excess -> tiny speed-up")
    x.add_argument("--max-speedup", type=float, default=1.0, help="allowed speed-up before trimming (1.0 = trim all extra US time)")
    x.add_argument("--max-stretch", type=float, default=1.25, help="flag groups slower or faster than this")
    x.add_argument("--max-drift", type=int, default=4000, help="split a group whose picture drifts more than this from the voice (ms)")
    x.add_argument("--review", help="review workbook (default: next to the mapping sheet)")
    x.add_argument("--no-lipsync", action="store_true", help="ignore lipsync.json (put lip-sync rows back into groups)")
    x.add_argument("--no-exact-span", dest="exact_span", action="store_false",
                   help="old grouping: trim extra source time and run on into free footage instead of the exact sentence span")
    x.add_argument("--lip-anchor", choices=["start", "both"], default="both",
                   help="lip sentences tied to the dub voice at their start only (the sentence runs into the pause after it) or at start and end")
    x.add_argument("--lip-max-speed", type=float, default=1.5,
                   help="TRIM mode (with --lip-anchor both): a lip sentence that would play faster than this is cut down to its lip part")
    x.add_argument("--lip-trim-above", type=float, default=1.6, help="TRIM mode: only sentences faster than this are trimmed")
    x.add_argument("--lip-smooth-ms", type=float, default=600, help="how far (ms) a lip sentence may start before/after its voice to even out speed")
    x.add_argument("--lip-group-speed-ok", type=float, default=1.5, help="pieces inside a lip group are evened out until within 1/x..x speed")
    x.add_argument("--no-lip-groups", action="store_true", help="every lip-sync row plays alone (no continuous lip groups)")
    x.add_argument("--lip-group-narr", type=int, default=2, help="narration rows allowed between two lip-sync rows of one lip group")
    x.add_argument("--lip-group-speed", type=float, default=2.0, help="footage between two lip sentences may play between 1/x and x speed")
    x.add_argument("--no-lip-fill", action="store_true", help="grouped rows next to a lip-sync row only slow down (no run-on into free footage)")
    x = sp.add_parser("render"); x.add_argument("--work", required=True)
    x.add_argument("--manifest", help="edit list to render (default: the active one: manifest_groups.json after `groups`)")
    x.add_argument("--out", help="the rendered video WITHOUT subtitles (default: <work>/video_nosubs.mp4)")
    x.add_argument("--subs", choices=["keep", "crop"], default="keep"); x.add_argument("--workers", type=int, default=4)
    x.add_argument("--max-seconds", type=int, default=100000)
    x = sp.add_parser("verify"); x.add_argument("--work", required=True); x.add_argument("--workers", type=int, default=4)
    x = sp.add_parser("subs"); x.add_argument("--work", required=True); x.add_argument("--out", required=True, help="the subtitled final video")
    x.add_argument("--srt", help="SRT to burn (default: the dub SRT; e.g. the English one from `translate`)")
    x.add_argument("--video", help="video to burn onto (default: the clean master from `bookends`)")
    x.add_argument("--offset", type=float, help="seconds to shift the SRT (default: the opening length on the clean master)")
    x.add_argument("--font", help="Poppins-Bold.ttf (found automatically if installed)")
    x.add_argument("--size", type=int, default=40, help="text size in px on a 1080-px-tall frame (scaled to the video's height)")
    x.add_argument("--box", help="strip rows TOP,BOTTOM (default: over the source caption box)")
    x.add_argument("--min-gap", type=float, default=1.5, help="pauses shorter than this keep showing the previous line")
    x.add_argument("--max-lead", type=float, default=5.0, help="show the first line from frame 0 (and hold the last to the end) if within this")
    x.add_argument("--no-cover", action="store_true", help="do not add empty strips over source captions outside subtitle times")
    x.add_argument("--chunk", type=float, default=60); x.add_argument("--workers", type=int, default=4)
    x.add_argument("--max-seconds", type=int, default=100000); x.add_argument("--keep-parts", action="store_true")
    x.add_argument("--style", choices=["strip", "box"], default="strip",
                   help="strip = white full-width strip (TDMB); box = white text on a 30%% black rounded box, transparent PNG overlay (every other show)")
    x = sp.add_parser("bookends"); x.add_argument("--work", required=True)
    x.add_argument("--out", required=True, help="the clean master (opening + dub episode + ending, no subtitles)")
    x.add_argument("--in", dest="input", help="default: the render without subtitles (video_out)")
    x.add_argument("--min-len", type=float, default=0.5, help="skip an opening/ending shorter than this (s)")
    x.add_argument("--fade", type=float, default=0.25, help="fade the source sound out/in at the joins (s)")
    x.add_argument("--no-ending", action="store_true", help="no source ending after the last dub line: the video ends with the dub audio (WBM QC)")
    for cmd, dflt in (("transitions", "light_leak,blur_dissolve,zoom_blur"), ("glow", "glow")):
        x = sp.add_parser(cmd); x.add_argument("--work", required=True)
        x.add_argument("--out", required=True, help="the clean master WITH transitions (or a test clip with --until)")
        x.add_argument("--in", dest="input", help="default: the clean master from `bookends`")
        x.add_argument("--styles", default=dflt, help="comma list, used in random order, never twice in a row: "
                       "light_leak, blur_dissolve, zoom_blur, glow")
        x.add_argument("--seed", type=int, default=24, help="random order seed (same seed = same order)")
        x.add_argument("--length", type=float, default=1.0, help="scale every transition's length (1.0 = 0.5-0.6 s)")
        x.add_argument("--min-jump", type=float, default=50, help="a source jump bigger than this (ms) counts as a clip switch")
        x.add_argument("--until", type=float, help="TEST: only the first N seconds (the clean master is not changed)")
        x.add_argument("--chunk", type=float, default=60); x.add_argument("--workers", type=int, default=2)
        x.add_argument("--max-seconds", type=float, default=1e9)
    x = sp.add_parser("translate"); x.add_argument("--work", required=True); x.add_argument("--out", required=True)
    x.add_argument("--srt", help="default: the dub SRT"); x.add_argument("--sl", default="it"); x.add_argument("--tl", default="en")
    x.add_argument("--engine", choices=["gtx", "cloud", "file"], default="gtx",
                   help="gtx = free Google endpoint; cloud = Cloud Translation API (key); file = only --import")
    x.add_argument("--api-key"); x.add_argument("--import", dest="import_file", help="TSV (source<TAB>translation) or JSON")
    x.add_argument("--export-todo", help="write the untranslated sentences to this TSV and stop")
    x.add_argument("--glossary", help="TSV of English fixes applied after translation (wrong<TAB>right)")
    x.add_argument("--no-keep-names", dest="keep_names", action="store_false", help="let Google translate character names")
    x.add_argument("--max-chars", type=int, default=1500); x.add_argument("--pause", type=float, default=1.0)
    x = sp.add_parser("check"); x.add_argument("--work", required=True)
    x.add_argument("--expect-subs", type=int, default=2, help="subtitled videos required (Italian + English)")
    x = sp.add_parser("speech"); x.add_argument("--work", required=True)
    x.add_argument("--speech-share", type=float, default=0.6); x.add_argument("--mixed-share", type=float, default=0.15)
    x = sp.add_parser("lipsync"); x.add_argument("--work", required=True)
    x.add_argument("--use", choices=["all", "mouth", "talknet"], default="all", help="which lip measures count")
    x.add_argument("--mouth-thr", type=float, default=0.007, help="Face Mesh lip-opening change counted as moving lips")
    x.add_argument("--merge", type=float, default=0.3); x.add_argument("--min-len", type=float, default=0.4)
    x.add_argument("--min-lip", type=float, default=0.4, help="a source sentence is 'speaking on screen' with this many seconds of moving lips")
    x.add_argument("--review")
    x = sp.add_parser("listen"); x.add_argument("--work", required=True); x.add_argument("--start", type=float, required=True)
    x.add_argument("--end", type=float, required=True); x.add_argument("--lang", default="it")
    a = p.parse_args()
    global CLAUSE_MIN
    w = getattr(a, "work", None); sm = os.path.join(w, "sentence_mode.json") if w else None
    if w and getattr(a, "clauses", None) is not None:
        os.makedirs(w, exist_ok=True); json.dump({"clauses": a.clauses}, open(sm, "w"))
    if sm and os.path.exists(sm) and not os.environ.get("DUBSTITCH_CLAUSES"):
        CLAUSE_MIN = int(json.load(open(sm)).get("clauses", 0) or 0)
    if CLAUSE_MIN and a.cmd in ("words", "analyze"):
        print(f"sentence mode: clauses (long sentences cut at , ; : and dashes, each part >= {CLAUSE_MIN} words)")
    dict(analyze=cmd_analyze, sheet=cmd_sheet, manifest=cmd_manifest, render=cmd_render, verify=cmd_verify, subs=cmd_subs,
         listen=cmd_listen, bookends=cmd_bookends, groups=cmd_groups, translate=cmd_translate, check=cmd_check, speech=cmd_speech, lipsync=cmd_lipsync, words=cmd_words, lipmark=cmd_lipmark, glow=cmd_transitions, transitions=cmd_transitions)[a.cmd](a)

if __name__ == "__main__":
    main()
~~~~~~

## Appendix: `scripts/pngsubs.py`

<!-- FILE: scripts/pngsubs.py md5=280b20ec2f3ff24198680d172897226e -->
~~~~~~python
"""Burn subtitles onto an existing clean video the way the stills-to-video pipeline does:
SRT -> one transparent 1080x1080 PNG per cue (on a video of another size the layer is centred at the bottom) (white Poppins Bold, black rounded box at exactly 30% opacity,
bottom-centred, 71 px from the bottom, max 86% width, 38 px shrinking to 30 px, up to 3 lines) -> ffmpeg concat timeline
(blank PNG between cues) -> transparent qtrle .mov -> overlay on the clean video -> mux the clean video's audio.
Resumable: each step skips work already done; the overlay is encoded in chunks so every command stays short.

  python3 pngsubs.py --video CLEAN.mp4 --srt SUBS.srt --out OUT.mp4 --work DIR [--offset 0] [--step all|pngs|mov|overlay|mux] [--max-seconds 140]
"""
import argparse, hashlib, html, json, os, re, subprocess, sys, time
from PIL import Image, ImageDraw, ImageFont

FRAME = 1080; BOX_RGBA = (0, 0, 0, 77)          # round(255 * 0.30) = 77, fixed for the whole episode
BOTTOM = 71; MAXW = 0.86; START, MIN = 38, 30; MAX_LINES = 3
PAD_X, PAD_Y, RADIUS, LINE_GAP = 22, 14, 14, 1.22
FONTS = ["/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf", "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]

def ts(t):
    h, m, s = t.replace(",", ".").split(":"); return int(h) * 3600 + int(m) * 60 + float(s)

def parse_srt(path):
    txt = open(path, encoding="utf-8-sig").read().replace("\r", "")
    cues = []
    for b in re.split(r"\n\s*\n", txt.strip()):
        L = [l for l in b.split("\n") if l.strip()]
        k = next((i for i, l in enumerate(L) if "-->" in l), None)
        if k is None: continue
        a, e = [x.strip() for x in L[k].split("-->")]
        text = " ".join(L[k + 1:]); text = html.unescape(re.sub(r"<[^>]+>", "", text))
        text = re.sub(r"\s+", " ", re.sub(r"°+|\*", " ", text)).strip()   # drop script markup like the dub pipeline: °°° scene marks, *italic* asterisks
        if text: cues.append({"s": ts(a), "e": ts(e.split()[0]), "text": text})
    return cues

def wrap(text, font, maxw, draw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= maxw or not cur: cur = t
        else: lines.append(cur); cur = w
    if cur: lines.append(cur)
    return lines

def layout(text, font_path, draw):
    maxw = FRAME * MAXW - 2 * PAD_X
    for size in range(START, MIN - 1, -1):          # shrink gradually; keep to 3 lines before shrinking further
        f = ImageFont.truetype(font_path, size); lines = wrap(text, f, maxw, draw)
        if len(lines) <= MAX_LINES and all(draw.textlength(l, font=f) <= maxw for l in lines): return f, lines, size
    return f, lines, size

def render_png(text, path, font_path):
    im = Image.new("RGBA", (FRAME, FRAME), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    f, lines, size = layout(text, font_path, d)
    lh = round(size * LINE_GAP); asc, desc = f.getmetrics()
    tw = max(d.textlength(l, font=f) for l in lines); th = lh * (len(lines) - 1) + asc + desc
    x1 = (FRAME - tw) / 2 - PAD_X; x2 = (FRAME + tw) / 2 + PAD_X
    y2 = FRAME - BOTTOM; y1 = y2 - th - 2 * PAD_Y
    box = Image.new("RGBA", (FRAME, FRAME), (0, 0, 0, 0))
    ImageDraw.Draw(box).rounded_rectangle([x1, y1, x2, y2], radius=RADIUS, fill=BOX_RGBA)
    im = Image.alpha_composite(im, box); d = ImageDraw.Draw(im)
    for i, l in enumerate(lines):
        d.text((FRAME / 2, y1 + PAD_Y + i * lh), l, font=f, fill=(255, 255, 255, 255), anchor="ma")
    im.save(path); return size, len(lines)

def run(cmd): r = subprocess.run(cmd, capture_output=True, text=True); r.returncode and sys.exit(r.stderr[-800:]); return r

def probe(path):
    o = json.loads(run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate,nb_frames", "-show_entries",
                        "format=duration", "-of", "json", path]).stdout)
    v = next(s for s in o["streams"] if s["codec_type"] == "video"); a, b = v["r_frame_rate"].split("/")
    return float(o["format"]["duration"]), int(a) / int(b)

def burn(video, srt, out, work, offset=0.0, chunk=60, max_seconds=140, font=None, preview=None, keep_parts=False):
    T0 = time.time(); W = work; os.makedirs(W + "/png", exist_ok=True)
    font = font or next(p for p in FONTS if os.path.exists(p))
    dur, fps = probe(video); end = min(dur, preview) if preview else dur
    # 1. cue PNGs (+ a fully transparent blank)
    cues = parse_srt(srt); stats = []
    blank = W + "/png/blank.png"
    if not os.path.exists(blank): Image.new("RGBA", (FRAME, FRAME), (0, 0, 0, 0)).save(blank)
    for i, c in enumerate(cues):
        p = f"{W}/png/cue_{i:04d}_{hashlib.md5(c['text'].encode()).hexdigest()[:8]}.png"; c["png"] = p
        if not os.path.exists(p): stats.append(render_png(c["text"], p, font))
    # 2. concat timeline: blank between cues, each cue PNG for its exact SRT time (shifted by --offset), last file repeated
    lst, t = [], 0.0
    for c in cues:
        s, e = max(c["s"] + offset, t), min(c["e"] + offset, end)
        if e <= s: continue
        if s > t: lst.append((blank, s - t))
        lst.append((c["png"], e - s)); t = e
    if t < end: lst.append((blank, end - t))
    with open(W + "/subtitle_concat.txt", "w") as fh:
        for p, d in lst: fh.write(f"file '{p}'\nduration {d:.4f}\n")
        fh.write(f"file '{lst[-1][0]}'\n")
    print(f"{len(cues)} cues, font {os.path.basename(font)}; video {dur:.3f}s at {fps:g} fps")
    # 3. transparent subtitle movie (qtrle, alpha kept), same fps as the clean video
    mov = W + "/subtitle_overlay.mov"
    if not os.path.exists(mov + ".ok"):
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", W + "/subtitle_concat.txt",
             "-vf", f"fps={fps:g},format=argb", "-t", f"{end:.4f}", "-c:v", "qtrle", mov]); open(mov + ".ok", "w").write("1")
        print(f"saved overlay movie ({time.time() - T0:.0f}s)")
    # 4. overlay in chunks (frame-exact) onto the clean video
    nf = round(end * fps); per = round(chunk * fps); parts = []
    for k, f0 in enumerate(range(0, nf, per)):
        f1 = min(f0 + per, nf); part = f"{W}/part{k:03d}.mp4"; parts.append(part)
        if os.path.exists(part + ".ok"): continue
        if time.time() - T0 > max_seconds: print("not finished -- run again (it resumes)"); sys.exit(3)
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{f0 / fps:.6f}", "-i", video, "-ss", f"{f0 / fps:.6f}", "-i", mov,
             "-filter_complex", "[0:v][1:v]overlay=(W-w)/2:H-h:format=auto,format=yuv420p[v]", "-map", "[v]", "-frames:v", str(f1 - f0),
             "-r", f"{fps:g}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", part])
        open(part + ".ok", "w").write("1"); print(f"chunk {k} done ({time.time() - T0:.0f}s)")
    # 5. concat + the clean video's own audio
    with open(W + "/parts.txt", "w") as fh: fh.writelines(f"file '{p}'\n" for p in parts)
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", W + "/parts.txt", "-c", "copy", W + "/silent.mp4"])
    run(["ffmpeg", "-y", "-v", "error", "-i", W + "/silent.mp4", "-i", video, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", "-t", f"{end:.4f}", "-movflags", "+faststart", out])
    od, _ = probe(out)
    if not keep_parts:                                   # the parts and the overlay movie are large; the PNGs stay (small)
        for f in os.listdir(W):
            if f.startswith("part") or f in ("silent.mp4", "subtitle_overlay.mov", "subtitle_overlay.mov.ok"): os.remove(os.path.join(W, f))
    print(f"saved {out}: {od:.3f}s (video {end:.3f}s); {len(cues)} subtitles, transparent box style")
    return od

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True); ap.add_argument("--srt", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True); ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--chunk", type=float, default=60); ap.add_argument("--max-seconds", type=float, default=140)
    ap.add_argument("--font", default=None); ap.add_argument("--preview", type=float, default=None, help="only the first N seconds")
    ap.add_argument("--keep-parts", action="store_true")
    a = ap.parse_args()
    burn(a.video, a.srt, a.out, a.work, a.offset, a.chunk, a.max_seconds, a.font, a.preview, a.keep_parts)

if __name__ == "__main__": main()
~~~~~~

## Appendix: `scripts/lipdetect.py`

<!-- FILE: scripts/lipdetect.py md5=fd3eed4aa869749be7053513997074bf -->
~~~~~~python
#!/usr/bin/env python3
"""
lipdetect.py -- find WHERE a character's lips move on screen, for every spoken source line.

Uses TalkNet-ASD (open source, MIT; Tao et al. 2021) on CPU: S3FD face detection -> face tracks per shot ->
TalkNet audio-visual score per face per frame (> 0 = this face is speaking the sound we hear).
Runs only on the source lines that `dubstitch.py speech` marked 'speech' or 'mixed' (+ a small margin),
resumable line by line, so it fits machines that cap each command's run time.

  python3 lipdetect.py --work W --talknet /path/to/TalkNet-ASD [--max-seconds 150]

Writes W/lips/<SRC#>.json per line (+ a small .jpg of the talking face) and, when all are done, W/lips.json.
"""
import argparse, json, math, os, subprocess, sys, time
import numpy as np
np.int = int            # TalkNet's face detector still uses the removed numpy alias

FPS = 25                                   # TalkNet works on 25 fps video and 100 MFCC frames per second

def load_talknet(tn):
    sys.path.insert(0, tn)
    import torch
    torch.set_num_threads(max(os.cpu_count() or 1, 1))
    from model.talkNetModel import talkNetModel
    from loss import lossAV, lossV
    from model.faceDetector.s3fd.nets import S3FDNet
    model, head, vhead = talkNetModel(), lossAV(), lossV()
    st = torch.load(os.path.join(tn, "pretrain_TalkSet.model"), map_location="cpu")
    ms, hs, vs = model.state_dict(), head.state_dict(), vhead.state_dict()
    for k, v in st.items():
        k = k.replace("module.", "")
        if k.startswith("model.") and k[6:] in ms: ms[k[6:]].copy_(v)
        elif k.startswith("lossAV.") and k[7:] in hs: hs[k[7:]].copy_(v)
        elif k.startswith("lossV.") and k[6:] in vs: vs[k[6:]].copy_(v)
    model.eval(); head.eval(); vhead.eval(); head.vhead = vhead
    det = S3FDNet(device="cpu"); det.load_state_dict(torch.load(os.path.join(tn, "model/faceDetector/s3fd/sfd_face.pth"), map_location="cpu"))
    det.eval()
    return model, head, det

def detect_faces(det, img, conf=0.9, scale=0.5):
    """S3FD on one RGB frame (as in the TalkNet demo, run at a reduced scale). -> [[x0,y0,x1,y1,score]]"""
    import torch, cv2
    from model.faceDetector.s3fd.box_utils import nms_
    h, w = img.shape[:2]
    x = cv2.resize(img, (0, 0), fx=scale, fy=scale).astype("float32").transpose(2, 0, 1)
    x = x[[2, 1, 0], :, :] - np.array([104., 117., 123.], "float32")[:, None, None]
    with torch.no_grad():
        y = det(torch.from_numpy(x)[None])
    d = y.data; sc = torch.Tensor([w, h, w, h]); out = []
    for i in range(d.size(1)):
        j = 0
        while j < d.size(2) and d[0, i, j, 0] > conf:
            out.append(list((d[0, i, j, 1:] * sc).numpy()) + [float(d[0, i, j, 0])]); j += 1
    if not out: return []
    b = np.array(out); keep = nms_(b, 0.1)
    return b[keep].tolist()

def iou(a, b):
    xa, ya, xb, yb = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, xb - xa) * max(0, yb - ya)
    return inter / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter, 1e-6)

def shots(frames, thr=28.0):
    """Hard cuts: big change of a 32x32 grey thumbnail between consecutive frames."""
    import cv2
    th = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_RGB2GRAY), (32, 32)).astype(float) for f in frames]
    cuts = [0] + [k for k in range(1, len(th)) if np.abs(th[k] - th[k - 1]).mean() > thr] + [len(frames)]
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b > a]

def track_faces(dets, a, b, max_miss=10, min_len=10):
    """Link detections inside one shot into face tracks (IoU > 0.5), interpolate missing frames (TalkNet demo logic)."""
    pool = {f: list(dets[f]) for f in range(a, b)}; tracks = []
    while True:
        tr = []
        for f in range(a, b):
            for d in list(pool[f]):
                if not tr or (f - tr[-1][0] <= max_miss and iou(d, tr[-1][1]) > 0.5):
                    if tr and f == tr[-1][0]: continue
                    tr.append((f, d)); pool[f].remove(d); break
            if tr and f - tr[-1][0] > max_miss: break
        if not tr: break
        if len(tr) > min_len:
            fr = np.array([t[0] for t in tr]); bb = np.array([t[1][:4] for t in tr])
            fi = np.arange(fr[0], fr[-1] + 1)
            tracks.append(dict(frame=fi, bbox=np.stack([np.interp(fi, fr, bb[:, k]) for k in range(4)], 1)))
    return tracks

def crop_track(frames, tr, cs=0.40):
    """Face crops exactly like the TalkNet demo: square around the (median-smoothed) box, 224 px, grey, centre 112."""
    import cv2
    from scipy import signal
    s = np.array([max(b[3] - b[1], b[2] - b[0]) / 2 for b in tr["bbox"]]); y = (tr["bbox"][:, 1] + tr["bbox"][:, 3]) / 2
    x = (tr["bbox"][:, 0] + tr["bbox"][:, 2]) / 2
    k = min(13, len(s) - (1 - len(s) % 2))
    if k >= 3: s, x, y = signal.medfilt(s, k), signal.medfilt(x, k), signal.medfilt(y, k)
    out = []
    for i, f in enumerate(tr["frame"]):
        bs = s[i]; bsi = int(bs * (1 + 2 * cs))
        img = np.pad(frames[f], ((bsi, bsi), (bsi, bsi), (0, 0)), "constant", constant_values=110)
        my, mx = y[i] + bsi, x[i] + bsi
        face = img[int(my - bs):int(my + bs * (1 + 2 * cs)), int(mx - bs * (1 + cs)):int(mx + bs * (1 + cs))]
        g = cv2.resize(cv2.cvtColor(cv2.resize(face, (224, 224)), cv2.COLOR_RGB2GRAY), (224, 224))
        out.append(g[56:168, 56:168])
    return np.array(out), dict(s=s, x=x, y=y)

def talk_scores(model, head, faces, audio16k, durations=(1, 2, 4)):
    import torch, python_speech_features
    mf = python_speech_features.mfcc(audio16k, 16000, numcep=13, winlen=0.025, winstep=0.010)
    L = min((mf.shape[0] - mf.shape[0] % 4) / 100, faces.shape[0] / FPS)
    mf = mf[:int(round(L * 100))]; vf = faces[:int(round(L * FPS))]
    allsc, allv = [], []
    with torch.no_grad():
        for du in durations:
            sc, sv = [], []
            for i in range(int(math.ceil(L / du))):
                A = torch.FloatTensor(mf[i * du * 100:(i + 1) * du * 100]).unsqueeze(0)
                V = torch.FloatTensor(vf[i * du * FPS:(i + 1) * du * FPS]).unsqueeze(0)
                if A.shape[1] < 4 or V.shape[1] < 1: continue
                eA = model.forward_audio_frontend(A); eV = model.forward_visual_frontend(V)
                eA, eV = model.forward_cross_attention(eA, eV)
                sc.extend(head.forward(model.forward_audio_visual_backend(eA, eV), labels=None))
                ov = head.vhead.FC(model.forward_visual_backend(eV).squeeze(1))      # picture only: does the mouth move like speech?
                sv.extend((ov[:, 1] - ov[:, 0]).reshape(-1).numpy())
            allsc.append(sc[:len(vf)]); allv.append(sv[:len(vf)])
    n = min(len(s) for s in allsc + allv)
    return np.mean(np.array([s[:n] for s in allsc]), 0), np.mean(np.array([s[:n] for s in allv]), 0)

def mouth_series(frames, fps=FPS, win=5):
    """MediaPipe Face Mesh (Google, open source): how wide each face's lips are open in every frame, relative to the
    face height, and how much that opening changes around each frame (rolling std over +-win frames).
    Moving lips = the opening keeps changing. Works on any face, also when the picture is not lip-synced to the sound."""
    import mediapipe as mp
    fm = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=3, min_detection_confidence=0.5)
    n = len(frames); faces = []                         # faces: [{"pos": (x,y), "ap": {frame: aperture}}]
    for k, f in enumerate(frames):
        r = fm.process(np.ascontiguousarray(f))
        for lm in (r.multi_face_landmarks or []):
            P = lambda i: np.array([lm.landmark[i].x, lm.landmark[i].y])
            h = np.linalg.norm(P(10) - P(152)); 
            if h < 0.08: continue                           # tiny background face
            ap = np.linalg.norm(P(13) - P(14)) / h; pos = P(1)
            near = [q for q in faces if k - max(q["ap"]) <= 3 and np.linalg.norm(q["pos"] - pos) < 0.12]
            q = min(near, key=lambda q: np.linalg.norm(q["pos"] - pos)) if near else None
            if q is None or k in q["ap"]: q = dict(pos=pos, ap={}, h=h); faces.append(q)
            q["ap"][k] = ap; q["pos"] = pos
    fm.close()
    motion = np.zeros(n); aper = np.zeros(n)
    for q in faces:
        ks = sorted(q["ap"])
        if len(ks) < 2 * win: continue
        a = np.array([q["ap"][k] for k in ks])
        for i, k in enumerate(ks):
            seg = a[max(i - win, 0):i + win + 1]
            m = float(seg.std())
            if m > motion[k]: motion[k] = m; aper[k] = a[i]
    return motion, aper

def run_line(r, video, nets, out_dir, margin, thr, merge, min_len, size, every=2):
    import cv2
    model, head, det = nets
    t0 = max(r["start_ms"] / 1000 - margin, 0); t1 = r["end_ms"] / 1000 + margin; dur = t1 - t0
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", video, "-vf",
                          f"fps={FPS},scale={size}:{size}:force_original_aspect_ratio=decrease,pad={size}:{size}:(ow-iw)/2:(oh-ih)/2",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, size, size, 3)
    au = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
                         "-f", "s16le", "-"], capture_output=True).stdout
    audio = np.frombuffer(au, np.int16)
    n = len(frames); dets = [detect_faces(det, f) if k % every == 0 else [] for k, f in enumerate(frames)]   # tracks interpolate the rest
    best = np.full(n, -9.0); who = np.full(n, -1); vbest = np.full(n, -9.0); tracks_out = []
    for a, b in shots(frames):
        for tr in track_faces(dets, a, b, min_len=max(10 // every, 3)):
            faces, sm = crop_track(frames, tr)
            f0, f1 = int(tr["frame"][0]), int(tr["frame"][-1]) + 1
            sc, sv = talk_scores(model, head, faces, audio[int(f0 / FPS * 16000):int(f1 / FPS * 16000)])
            if not len(sc): continue
            smooth = lambda q: np.array([np.mean(q[max(i - 2, 0):min(i + 3, len(q))]) for i in range(len(q))])   # demo smoothing
            ss, vv = smooth(sc), smooth(sv)
            ti = len(tracks_out)
            tracks_out.append(dict(first_s=round(t0 + f0 / FPS, 3), last_s=round(t0 + (f0 + len(ss)) / FPS, 3),
                                   box=[round(float(v)) for v in np.median(tr["bbox"][:len(ss)], 0)], size=size,
                                   mean_score=round(float(ss.mean()), 2), mean_visual=round(float(vv.mean()), 2),
                                   talking_s=round(float((ss >= thr).sum()) / FPS, 2), mouth_s=round(float((vv >= 0).sum()) / FPS, 2)))
            for i in range(len(ss)):
                if ss[i] > best[f0 + i]: best[f0 + i] = ss[i]
                if vv[i] > vbest[f0 + i]: vbest[f0 + i] = vv[i]; who[f0 + i] = ti
    lo = int(round((r["start_ms"] / 1000 - t0) * FPS)); hi = min(int(round((r["end_ms"] / 1000 - t0) * FPS)), n)
    def spans(on):                                        # talking stretches inside the line itself (not the margin)
        ivs, k = [], lo
        while k < hi:
            if not on[k]: k += 1; continue
            j = k
            while j < hi and on[j]: j += 1
            if ivs and k - ivs[-1][1] <= merge * FPS: ivs[-1][1] = j
            else: ivs.append([k, j])
            k = j
        return [v for v in ivs if v[1] - v[0] >= min_len * FPS]
    def as_json(ivs, sc):
        return [dict(start_s=round(t0 + a / FPS, 3), end_s=round(t0 + b / FPS, 3),
                     face=int(np.bincount(who[a:b][who[a:b] >= 0]).argmax()) if (who[a:b] >= 0).any() else -1,
                     score=round(float(sc[a:b].mean()), 2)) for a, b in ivs]
    iv_av, iv_v = spans(best >= thr), spans(vbest >= 0)
    ivs = iv_v or iv_av
    res = dict(idx=r["idx"], cls=r["cls"], text=r["text"], start_s=r["start_ms"] / 1000, end_s=r["end_ms"] / 1000,
               window=[round(t0, 3), round(t1, 3)], frames=n, faces_frames=int(sum(1 for d in dets[lo:hi] if d)) * every,
               tracks=tracks_out, scores=[round(float(v), 2) for v in best], visual=[round(float(v), 2) for v in vbest],
               intervals=as_json(iv_av, best), intervals_visual=as_json(iv_v, vbest))
    if ivs:                                                           # small picture of the talking face, for the QC sheet
        a, b = max(ivs, key=lambda v: v[1] - v[0]); m = (a + b) // 2; img = frames[m].copy()
        t = tracks_out[who[m]] if who[m] >= 0 else None
        if t:
            x0, y0, x1, y1 = t["box"]; cv2.rectangle(img, (x0, y0), (x1, y1), (0, 255, 0), 4)
        cv2.imwrite(os.path.join(out_dir, f"{r['idx']:04d}.jpg"), cv2.cvtColor(cv2.resize(img, (270, 270)), cv2.COLOR_RGB2BGR))
    return res

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work", required=True); p.add_argument("--talknet", required=True, help="folder of TalkNet-ASD with its weights")
    p.add_argument("--margin", type=float, default=0.3, help="seconds analysed before/after each line")
    p.add_argument("--threshold", type=float, default=0.0, help="TalkNet score at/above which a face counts as talking")
    p.add_argument("--merge", type=float, default=0.3, help="join talking stretches separated by less than this (s)")
    p.add_argument("--min-len", type=float, default=0.3, help="ignore talking stretches shorter than this (s)")
    p.add_argument("--size", type=int, default=540, help="frame size used for detection (px)")
    p.add_argument("--det-every", type=int, default=2, help="detect faces on every Nth frame (tracking fills the rest)")
    p.add_argument("--max-seconds", type=float, default=1e9, help="stop starting new lines after this long (resume by re-running)")
    p.add_argument("--only", help="comma-separated SRC# to (re)do")
    p.add_argument("--video", help="the source video (default: from <work>/speech.json)")
    p.add_argument("--range", help="analyse any stretch of the source video instead of the spoken lines: START,END in seconds")
    p.add_argument("--mouth", action="store_true", help="add the Face Mesh lip-opening measure to lines already done")
    a = p.parse_args()
    sp = os.path.join(a.work, "speech.json")
    S = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
    if a.video: S["src_video"] = a.video
    if "src_video" not in S: raise SystemExit("pass --video (or run `dubstitch.py speech` first)")
    if a.range:                                                     # one free stretch: report every talking moment in it
        t0, t1 = (float(x) for x in a.range.split(","))
        r = dict(idx=-1, cls="range", text=f"{t0}-{t1}s", start_ms=int(t0 * 1000), end_ms=int(t1 * 1000))
        cwd = os.getcwd(); os.chdir(a.talknet); nets = load_talknet(a.talknet); os.chdir(cwd)
        out_dir = os.path.join(a.work, "lips_range"); os.makedirs(out_dir, exist_ok=True)
        res = run_line(r, S["src_video"], nets, out_dir, 0.0, a.threshold, a.merge, a.min_len, a.size, a.det_every)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{res['window'][0]:.3f}", "-t", f"{res['window'][1] - res['window'][0]:.3f}",
                              "-i", S["src_video"], "-vf", f"fps={FPS},scale={a.size}:{a.size}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True).stdout
        mo, ap = mouth_series(np.frombuffer(raw, np.uint8).reshape(-1, a.size, a.size, 3))
        res["mouth"] = [round(float(v), 4) for v in mo]; res["aperture"] = [round(float(v), 4) for v in ap]
        json.dump(res, open(os.path.join(out_dir, f"range_{t0:g}_{t1:g}.json"), "w"))
        print(f"faces found in {res['faces_frames']}/{res['frames']} frames; tracks: " +
              "; ".join(f"{t['first_s']:.2f}-{t['last_s']:.2f}s talknet {t['mean_score']:+.1f} picture {t['mean_visual']:+.1f}" for t in res["tracks"]))
        for k in range(0, len(mo), 5):
            t = res["window"][0] + k / FPS
            print(f"  {t:6.2f}s  lip-opening change {mo[k]:.4f}  opening {ap[k]:.3f}  talknet {res['scores'][k]:+.1f}  picture-only {res['visual'][k]:+.1f}")
        return
    todo = [r for r in S["rows"] if r["cls"] != "narration"]
    if a.only: todo = [r for r in S["rows"] if r["idx"] in {int(x) for x in a.only.split(",")}]
    out_dir = os.path.join(a.work, "lips"); os.makedirs(out_dir, exist_ok=True)
    sig = json.dumps([a.margin, a.threshold, a.merge, a.min_len, a.size, a.det_every])
    nets = None; t_stop = time.time() + a.max_seconds; done = 0
    if a.mouth:
        for r in todo:
            fp = os.path.join(out_dir, f"{r['idx']:04d}.json"); d = json.load(open(fp))
            if "mouth" in d: done += 1; continue
            if time.time() > t_stop: break
            t0, t1 = d["window"]; size = a.size
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", S["src_video"], "-vf",
                                  f"fps={FPS},scale={size}:{size}:force_original_aspect_ratio=decrease,pad={size}:{size}:(ow-iw)/2:(oh-ih)/2",
                                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
            fr = np.frombuffer(raw, np.uint8).reshape(-1, size, size, 3)
            mo, ap = mouth_series(fr)
            d["mouth"] = [round(float(v), 4) for v in mo]; d["aperture"] = [round(float(v), 4) for v in ap]
            json.dump(d, open(fp, "w"), ensure_ascii=False); done += 1
            print(f"SRC#{r['idx']:3d} mouth: max motion {mo.max():.3f}, frames > 0.01: {(mo > 0.01).sum()}/{len(mo)}", flush=True)
        print(f"done {done}/{len(todo)} lines"); sys.exit(0 if done == len(todo) else 3)
    for r in todo:
        fp = os.path.join(out_dir, f"{r['idx']:04d}.json")
        if not a.only and os.path.exists(fp) and json.load(open(fp)).get("sig") == sig + r["text"]:
            done += 1; continue
        if time.time() > t_stop: break
        if nets is None:
            cwd = os.getcwd(); os.chdir(a.talknet); nets = load_talknet(a.talknet); os.chdir(cwd)
        t = time.time(); res = run_line(r, S["src_video"], nets, out_dir, a.margin, a.threshold, a.merge, a.min_len, a.size, a.det_every)
        res["sig"] = sig + r["text"]; json.dump(res, open(fp, "w"), ensure_ascii=False); done += 1
        talk = sum(v["end_s"] - v["start_s"] for v in res["intervals"]); mo = sum(v["end_s"] - v["start_s"] for v in res["intervals_visual"])
        print(f"SRC#{r['idx']:3d} {r['cls']:6s} {res['frames']:4d} frames, {len(res['tracks'])} faces, mouth moving {mo:4.1f}s, in sync with voice {talk:4.1f}s of "
              f"{res['end_s'] - res['start_s']:.1f}s  ({time.time() - t:.0f}s)  {r['text'][:50]!r}", flush=True)
    print(f"done {done}/{len(todo)} lines")
    if done < len(todo):
        print("not finished -- run the same command again (it resumes)"); sys.exit(3)
    if a.only: return
    allr = [json.load(open(os.path.join(out_dir, f"{r['idx']:04d}.json"))) for r in todo]
    for r in allr: r.pop("scores", None); r.pop("visual", None); r.pop("sig", None)
    json.dump(dict(talknet=os.path.abspath(a.talknet), settings=json.loads(sig), lines=allr),
              open(os.path.join(a.work, "lips.json"), "w"), ensure_ascii=False, indent=1)
    n_l = sum(1 for r in allr if r["intervals"])
    print(f"lips.json: {n_l}/{len(allr)} spoken lines show a talking face on screen "
          f"({sum(v['end_s'] - v['start_s'] for r in allr for v in r['intervals']):.0f}s of lip movement)")

if __name__ == "__main__":
    main()
~~~~~~

## Appendix: `scripts/lipfast.py`

<!-- FILE: scripts/lipfast.py md5=9a6a685740e3fe1d99d18cb642133374 -->
~~~~~~python
#!/usr/bin/env python3
"""
lipfast.py -- faster drop-in for `lipdetect.py --range` (same measures, same output file format).

Same three measures as lipdetect.py: TalkNet-ASD audio-visual score, TalkNet picture-only score, MediaPipe Face Mesh
lip-opening change. What changes is only how the work is done:
  * the video is decoded ONCE (lipdetect decodes it twice: once for TalkNet, once for Face Mesh);
  * face detection options (--det):
      s3fd  : the same S3FD detector, run on batches of frames (--batch) instead of one frame at a time;
      yunet : OpenCV YuNet face detector (tiny, fast) instead of S3FD;
      mesh  : no separate detector -- face boxes come from the Face Mesh landmarks that are computed anyway;
  * --det-every N : detect on every Nth frame (tracking interpolates the rest, as lipdetect already does with 2).

  python3 lipfast.py --work W --video V --talknet TN --range START,END [--det s3fd|yunet|mesh] [--det-every 2]
         [--out-dir DIR]   # default W/lips_range
"""
import argparse, json, os, subprocess, sys, time
import numpy as np
np.int = int
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lipdetect as L

FPS = 25

def s3fd_batch(det, frames, idx, conf=0.9, scale=0.5, batch=8):
    import torch, cv2
    from model.faceDetector.s3fd.box_utils import nms_
    out = {}
    for b0 in range(0, len(idx), batch):
        ks = idx[b0:b0 + batch]
        h, w = frames[ks[0]].shape[:2]
        xs = []
        for k in ks:
            x = cv2.resize(frames[k], (0, 0), fx=scale, fy=scale).astype("float32").transpose(2, 0, 1)
            xs.append(x[[2, 1, 0], :, :] - np.array([104., 117., 123.], "float32")[:, None, None])
        with torch.no_grad():
            y = det(torch.from_numpy(np.stack(xs)))
        d = y.data; sc = torch.Tensor([w, h, w, h])
        for bi, k in enumerate(ks):
            res = []
            for i in range(d.size(1)):
                j = 0
                while j < d.size(2) and d[bi, i, j, 0] > conf:
                    res.append(list((d[bi, i, j, 1:] * sc).numpy()) + [float(d[bi, i, j, 0])]); j += 1
            if res:
                bb = np.array(res); keep = nms_(bb, 0.1); res = bb[keep].tolist()
            out[k] = res
    return out

_yunet = None
def yunet_dets(frames, idx, model_path, conf=0.7, scale=0.5):
    import cv2
    global _yunet
    h, w = frames[0].shape[:2]; sw, sh = int(w * scale), int(h * scale)
    if _yunet is None:
        _yunet = cv2.FaceDetectorYN.create(model_path, "", (sw, sh), conf, 0.3, 50)
    out = {}
    for k in idx:
        img = cv2.cvtColor(cv2.resize(frames[k], (sw, sh)), cv2.COLOR_RGB2BGR)
        _, f = _yunet.detect(img)
        res = []
        for r in (f if f is not None else []):
            x, y, bw, bh = r[:4] / scale
            res.append([x, y, x + bw, y + bh, float(r[-1])])
        out[k] = res
    return out

def mesh_pass(frames, win=5):
    """Face Mesh exactly as lipdetect.mouth_series, but also returns per-frame face boxes from the landmarks."""
    import mediapipe as mp
    fm = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=3, min_detection_confidence=0.5)
    n = len(frames); faces = []; boxes = {}
    H, W = frames[0].shape[:2]
    for k, f in enumerate(frames):
        r = fm.process(np.ascontiguousarray(f)); boxes[k] = []
        for lm in (r.multi_face_landmarks or []):
            P = lambda i: np.array([lm.landmark[i].x, lm.landmark[i].y])
            xs = np.array([p.x for p in lm.landmark]); ys = np.array([p.y for p in lm.landmark])
            boxes[k].append([xs.min() * W, ys.min() * H, xs.max() * W, ys.max() * H, 1.0])
            h = np.linalg.norm(P(10) - P(152))
            if h < 0.08: continue
            ap = np.linalg.norm(P(13) - P(14)) / h; pos = P(1)
            near = [q for q in faces if k - max(q["ap"]) <= 3 and np.linalg.norm(q["pos"] - pos) < 0.12]
            q = min(near, key=lambda q: np.linalg.norm(q["pos"] - pos)) if near else None
            if q is None or k in q["ap"]: q = dict(pos=pos, ap={}, h=h); faces.append(q)
            q["ap"][k] = ap; q["pos"] = pos
    fm.close()
    motion = np.zeros(n); aper = np.zeros(n)
    for q in faces:
        ks = sorted(q["ap"])
        if len(ks) < 2 * win: continue
        a = np.array([q["ap"][k] for k in ks])
        for i, k in enumerate(ks):
            m = float(a[max(i - win, 0):i + win + 1].std())
            if m > motion[k]: motion[k] = m; aper[k] = a[i]
    return motion, aper, boxes

def run_range(t0, t1, video, nets, det_kind, every, batch, size, yunet_path, yconf=0.85, key_every=6, thr=0.0, merge=0.3, min_len=0.3, timing=None):
    import cv2
    model, head, det = nets
    T = timing if timing is not None else {}
    t = time.time(); dur = t1 - t0
    raw = subprocess.run(["ffmpeg", "-v", "error", "-threads", "0", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", video, "-vf",
                          f"fps={FPS},scale={size}:{size}:force_original_aspect_ratio=decrease,pad={size}:{size}:(ow-iw)/2:(oh-ih)/2",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, size, size, 3)
    au = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
                         "-f", "s16le", "-"], capture_output=True).stdout
    audio = np.frombuffer(au, np.int16); n = len(frames)
    T["decode"] = T.get("decode", 0) + time.time() - t
    t = time.time(); mo, ap, mboxes = mesh_pass(frames); T["facemesh"] = T.get("facemesh", 0) + time.time() - t
    t = time.time(); idx = list(range(0, n, every))
    if det_kind == "s3fd": dd = s3fd_batch(det, frames, idx, batch=batch)
    elif det_kind == "yunet": dd = yunet_dets(frames, idx, yunet_path, conf=yconf)
    elif det_kind == "file":
        # S3FD boxes computed elsewhere (mac_fullscan.py --dets-only on the Mac GPU): dets_<t0>_<t1>.json
        D = json.load(open(os.path.join(yunet_path, f"dets_{t0:g}_{t1:g}.json")))
        if D["frames"] != n or D.get("every", 2) != every: raise SystemExit(f"dets file {t0:g}-{t1:g}: {D['frames']} frames / every {D.get('every')} vs {n} / {every}")
        dd = {int(k): v for k, v in D["dets"].items()}
    elif det_kind == "scrfd":
        import detlib; dd = detlib.scrfd_dets(frames, idx, yunet_path, conf=yconf, scale=0.5)
    elif det_kind == "hybrid":
        # S3FD (the original detector) on every key-th frame decides WHICH faces exist; YuNet (high recall at 0.5)
        # gives the boxes on the frames in between, kept only when S3FD saw that face on the key frame before or after.
        keys = list(range(0, n, key_every)); S = s3fd_batch(det, frames, keys, batch=1)
        rest = [k for k in idx if k not in S]; Y = yunet_dets(frames, rest, yunet_path, conf=0.5)
        dd = dict(S)
        for k in rest:
            kp = (k // key_every) * key_every; kn = kp + key_every
            ref = S.get(kp, []) + S.get(kn, [])
            dd[k] = [y for y in Y[k] if any(L.iou(y, r) > 0.3 for r in ref)]
        T["s3fd_calls"] = T.get("s3fd_calls", 0) + len(keys)
    else: dd = {k: mboxes[k] for k in idx}
    dets = [dd.get(k, []) if k % every == 0 else [] for k in range(n)]
    T["detect"] = T.get("detect", 0) + time.time() - t
    t = time.time(); tn = 0
    best = np.full(n, -9.0); who = np.full(n, -1); vbest = np.full(n, -9.0); tracks_out = []
    for a, b in L.shots(frames):
        for tr in L.track_faces(dets, a, b, min_len=max(10 // every, 3)):
            faces, sm = L.crop_track(frames, tr)
            f0, f1 = int(tr["frame"][0]), int(tr["frame"][-1]) + 1
            t2 = time.time(); sc, sv = L.talk_scores(model, head, faces, audio[int(f0 / FPS * 16000):int(f1 / FPS * 16000)]); tn += time.time() - t2
            if not len(sc): continue
            smooth = lambda q: np.array([np.mean(q[max(i - 2, 0):min(i + 3, len(q))]) for i in range(len(q))])
            ss, vv = smooth(sc), smooth(sv); ti = len(tracks_out)
            tracks_out.append(dict(first_s=round(t0 + f0 / FPS, 3), last_s=round(t0 + (f0 + len(ss)) / FPS, 3),
                                   box=[round(float(v)) for v in np.median(tr["bbox"][:len(ss)], 0)], size=size,
                                   mean_score=round(float(ss.mean()), 2), mean_visual=round(float(vv.mean()), 2),
                                   talking_s=round(float((ss >= thr).sum()) / FPS, 2), mouth_s=round(float((vv >= 0).sum()) / FPS, 2)))
            for i in range(len(ss)):
                if ss[i] > best[f0 + i]: best[f0 + i] = ss[i]
                if vv[i] > vbest[f0 + i]: vbest[f0 + i] = vv[i]; who[f0 + i] = ti
    T["talknet"] = T.get("talknet", 0) + tn; T["track"] = T.get("track", 0) + time.time() - t - tn
    res = dict(idx=-1, cls="range", text=f"{t0}-{t1}s", start_s=t0, end_s=t1, window=[round(t0, 3), round(t1, 3)], frames=n,
               faces_frames=int(sum(1 for d in dets if d)) * every, tracks=tracks_out,
               scores=[round(float(v), 2) for v in best], visual=[round(float(v), 2) for v in vbest],
               intervals=[], intervals_visual=[], method=dict(det=det_kind, every=every, batch=batch, size=size, model=os.path.basename(yunet_path) if det_kind in ('yunet', 'scrfd') else None, conf=yconf if det_kind in ('yunet', 'scrfd') else None, key_every=key_every if det_kind == 'hybrid' else None),
               mouth=[round(float(v), 4) for v in mo], aperture=[round(float(v), 4) for v in ap])
    return res

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work", required=True); p.add_argument("--talknet", required=True); p.add_argument("--video", required=True)
    p.add_argument("--range", required=True, help="START,END seconds; several pieces: START,END;START,END")
    p.add_argument("--det", choices=["s3fd", "yunet", "mesh", "hybrid", "scrfd", "file"], default="s3fd")
    p.add_argument("--key-every", type=int, default=6, help="hybrid: run S3FD on every Nth frame")
    p.add_argument("--det-every", type=int, default=2); p.add_argument("--batch", type=int, default=8)
    p.add_argument("--size", type=int, default=540); p.add_argument("--threads", type=int, default=0)
    p.add_argument("--yunet", default=None, help="model path (yunet/scrfd) or, for --det file, the folder with dets_<s>_<e>.json")
    p.add_argument("--yunet-conf", type=float, default=0.85, help="YuNet score threshold (0.85 matches S3FD at 0.9 best)")
    p.add_argument("--out-dir"); p.add_argument("--max-seconds", type=float, default=1e9, help="do not start a new piece after this long")
    a = p.parse_args()
    import torch
    out_dir = a.out_dir or os.path.join(a.work, "lips_range"); os.makedirs(out_dir, exist_ok=True)
    tl = time.time(); cwd = os.getcwd(); os.chdir(a.talknet); nets = L.load_talknet(a.talknet); os.chdir(cwd)
    if a.threads: torch.set_num_threads(a.threads)
    T = {"load": time.time() - tl}
    for piece in a.range.split(";"):
        if time.time() - tl > a.max_seconds: print("stopped (max-seconds); run again", flush=True); break
        t0, t1 = (float(x) for x in piece.split(","))
        ts = time.time()
        res = run_range(t0, t1, a.video, nets, a.det, a.det_every, a.batch, a.size, a.yunet, a.yunet_conf, a.key_every, timing=T)
        json.dump(res, open(os.path.join(out_dir, f"range_{t0:g}_{t1:g}.json"), "w"))
        print(f"done {t0:g}-{t1:g} ({time.time() - ts:.0f}s)", flush=True)
    print("timing", {k: round(v, 1) for k, v in T.items()})

if __name__ == "__main__":
    main()
~~~~~~

## Appendix: `scripts/lipgpu.py`

<!-- FILE: scripts/lipgpu.py md5=7f819ac4cc799442bc959920ce819054 -->
~~~~~~python
#!/usr/bin/env python3
"""
lipgpu.py -- the heavy half of the Step 4B lip scan (S3FD face finder + TalkNet-ASD) on the fastest device of THIS
computer, with limits so the computer stays usable. Same logic and output as `lipdetect.py --range` for these two
measures; the third measure (Face Mesh lip opening) is done by lipmesh.py and the halves are joined by lipmerge.py.

  python3 lipgpu.py --video SRC.mp4 --out-dir W/lips_nets [--ranges ranges.json | --piece 15]
                    [--talknet DIR] [--device auto|mps|cuda|cpu] [--threads N] [--min-free-gb G] [--max-seconds S]

Device (auto): NVIDIA GPU (cuda) > Apple GPU (mps, Apple-silicon Macs) > CPU.
Safety, on by default (the computer must never hang):
  * CPU threads = cores - 2 (at least 1);  process priority lowered (nice 10 / Windows "below normal");
  * Apple GPU memory capped (PYTORCH_MPS_HIGH_WATERMARK_RATIO 0.5, or 0.35 on Macs with 8 GB or less);
    NVIDIA GPU memory capped at 60% of the card;
  * before every piece: waits while free RAM is below --min-free-gb (default: 1.5 GB, or 12% of RAM if larger);
  * keeps the computer awake while running (macOS caffeinate / Windows SetThreadExecutionState), never longer;
  * logs RAM / CPU / its own memory after every piece; resumable (pieces already written are skipped).
Output: OUT_DIR/range_<s>_<e>.json (scores, visual, tracks; no mouth) + OUT_DIR/ranges.json + OUT_DIR/timing.json.
Tested: Apple M4 (Metal): ep24 0.70 s and ep25 0.67 s of work per second of video (CPU-only 4-core Linux: about 3 s).
"""
import argparse, json, math, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FPS = 25


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def total_ram_gb():
    try:
        import psutil; return psutil.virtual_memory().total / 2**30
    except Exception:
        try: return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
        except Exception: return 8.0


def free_ram_gb():
    """RAM that can still be used, in GB (psutil if installed; otherwise the operating system's own numbers)."""
    try:
        import psutil; return psutil.virtual_memory().available / 2**30
    except Exception:
        pass
    try:
        if sys.platform.startswith("linux"):
            for line in open("/proc/meminfo"):
                if line.startswith("MemAvailable:"): return int(line.split()[1]) / 2**20
        elif sys.platform == "darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
            page = int(out.split("page size of")[1].split()[0]); pages = 0
            for line in out.splitlines():
                if line.startswith(("Pages free", "Pages inactive", "Pages speculative")): pages += int(line.split(":")[1].strip().rstrip("."))
            return pages * page / 2**30
        elif os.name == "nt":
            import ctypes
            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tot", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("x", ctypes.c_ulonglong)]
            m = MS(); m.l = ctypes.sizeof(MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)); return m.avail / 2**30
    except Exception:
        pass
    return None


def video_length(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video], capture_output=True, text=True) \
        if shutil.which("ffprobe") else None
    if r is not None and r.stdout.strip(): return float(r.stdout.strip())
    err = subprocess.run(["ffmpeg", "-i", video], capture_output=True, text=True).stderr          # no ffprobe: read "Duration:"
    h, m, x = err.split("Duration:")[1].split(",")[0].strip().split(":"); return int(h) * 3600 + int(m) * 60 + float(x)


def make_ranges(video, piece):
    """Pieces of `piece` seconds from 0 to the end of the video (the same pieces as the lipdetect.py loop: s, s+piece)."""
    L = video_length(video); out = []; s = 0
    while s < L: out.append([s, s + piece]); s += piece
    return out


def be_gentle(threads):
    """Lower priority + keep awake; returns a cleanup function."""
    try:
        if os.name == "nt":
            import psutil; psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(10)
    except Exception:
        pass
    stop = lambda: None
    if sys.platform == "darwin":
        try:
            p = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())]); stop = p.terminate
        except Exception:
            pass
    elif os.name == "nt":
        try:
            import ctypes; ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
            stop = lambda: ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass
    return stop


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", required=True); p.add_argument("--out-dir", required=True)
    p.add_argument("--ranges", help="JSON [[start, end], ...] in seconds (default: --piece-second pieces over the whole video)")
    p.add_argument("--piece", type=float, default=15, help="piece length in seconds when --ranges is not given (default 15)")
    p.add_argument("--talknet", default=os.path.join(HERE, "..", "third_party", "TalkNet-ASD"), help="TalkNet-ASD folder with its two weight files")
    p.add_argument("--device", default="auto", choices=["auto", "mps", "cuda", "cpu"])
    p.add_argument("--threads", type=int, default=0, help="CPU threads (default: cores - 2)")
    p.add_argument("--min-free-gb", type=float, default=0, help="wait before a piece while free RAM is below this (default max(1.5, 12%% of RAM))")
    p.add_argument("--max-seconds", type=float, default=1e9, help="do not start a new piece after this long (run again to resume)")
    a = p.parse_args()

    ram = total_ram_gb()
    hi = 0.5 if ram > 8.5 else 0.35                                   # Apple GPU memory cap (must be set before torch loads)
    os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", str(hi))
    os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", str(round(float(os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"]) * 0.8, 2)))
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    import numpy as np
    np.int = int
    import torch, torch.nn.functional as F_, cv2
    import lipdetect as L, lipfast as LF

    if a.device == "auto":
        dev_name = "cuda" if torch.cuda.is_available() else ("mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu")
    else:
        dev_name = a.device
    dev = torch.device(dev_name)
    threads = a.threads or max(1, (os.cpu_count() or 4) - 2)
    min_free = a.min_free_gb or max(1.5, 0.12 * ram)
    stop_awake = be_gentle(threads)
    if dev_name == "cuda":
        try: torch.cuda.set_per_process_memory_fraction(0.6)
        except Exception: pass

    tn = os.path.abspath(a.talknet)
    if not os.path.exists(os.path.join(tn, "pretrain_TalkSet.model")): sys.exit(f"TalkNet-ASD weights not found in {tn} (Step 0)")
    cwd = os.getcwd(); os.chdir(tn); model, head, det = L.load_talknet(tn); os.chdir(cwd)
    torch.set_num_threads(threads)                                   # after load_talknet (it sets all cores)
    from model.faceDetector.s3fd.box_utils import nms_, PriorBox
    LF.mesh_pass = lambda frames, win=5: (np.zeros(len(frames)), np.zeros(len(frames)), {})   # Face Mesh: lipmesh.py
    log(f"device {dev_name}; torch {torch.__version__}; threads {threads}; RAM {ram:.1f} GB; wait if free RAM < {min_free:.1f} GB"
        + (f"; MPS cap {os.environ['PYTORCH_MPS_HIGH_WATERMARK_RATIO']}" if dev_name == "mps" else ""))

    class Head(torch.nn.Module):
        """S3FD forward up to the raw loc/conf maps (on the device); prior boxes + Detect stay on the CPU exactly as in nets.py."""
        def __init__(s, n): super().__init__(); s.n = n
        def forward(s, x):
            n = s.n; sources = []; loc = []; conf = []
            for k in range(16): x = n.vgg[k](x)
            sources.append(n.L2Norm3_3(x))
            for k in range(16, 23): x = n.vgg[k](x)
            sources.append(n.L2Norm4_3(x))
            for k in range(23, 30): x = n.vgg[k](x)
            sources.append(n.L2Norm5_3(x))
            for k in range(30, len(n.vgg)): x = n.vgg[k](x)
            sources.append(x)
            for k, v in enumerate(n.extras):
                x = F_.relu(v(x), inplace=True)
                if k % 2 == 1: sources.append(x)
            loc_x = n.loc[0](sources[0]); conf_x = n.conf[0](sources[0])
            max_conf, _ = torch.max(conf_x[:, 0:3, :, :], dim=1, keepdim=True); conf_x = torch.cat((max_conf, conf_x[:, 3:, :, :]), dim=1)
            loc.append(loc_x.permute(0, 2, 3, 1).contiguous()); conf.append(conf_x.permute(0, 2, 3, 1).contiguous())
            for i in range(1, len(sources)):
                conf.append(n.conf[i](sources[i]).permute(0, 2, 3, 1).contiguous()); loc.append(n.loc[i](sources[i]).permute(0, 2, 3, 1).contiguous())
            return tuple(loc) + tuple(conf)
    hd = Head(det).eval().to(dev); model.to(dev); head.to(dev); head.vhead.to(dev)

    def detect_one(img, conf=0.9, scale=0.5):
        h, w = img.shape[:2]
        x = cv2.resize(img, (0, 0), fx=scale, fy=scale).astype("float32").transpose(2, 0, 1)
        x = x[[2, 1, 0], :, :] - np.array([104., 117., 123.], "float32")[:, None, None]
        with torch.no_grad():
            outs = tuple(t.cpu() for t in hd(torch.from_numpy(x)[None].to(dev)))
        loc, cf = outs[:6], outs[6:]
        fm = [[l.size(1), l.size(2)] for l in loc]
        with torch.no_grad():
            locc = torch.cat([o.reshape(o.size(0), -1) for o in loc], 1); cfc = torch.cat([o.reshape(o.size(0), -1) for o in cf], 1)
            priors = PriorBox(torch.Size([x.shape[1], x.shape[2]]), fm).forward()
            y = det.detect.forward(locc.view(1, -1, 4), torch.softmax(cfc.view(1, -1, 2), -1), priors)
        d = y.data; sc = torch.Tensor([w, h, w, h]); out = []
        for i in range(d.size(1)):
            j = 0
            while j < d.size(2) and d[0, i, j, 0] > conf:
                out.append(list((d[0, i, j, 1:] * sc).numpy()) + [float(d[0, i, j, 0])]); j += 1
        if not out: return []
        b = np.array(out); keep = nms_(b, 0.1)
        return b[keep].tolist()

    def s3fd_dev(det_, frames, idx, conf=0.9, scale=0.5, batch=1):
        return {k: detect_one(frames[k], conf, scale) for k in idx}

    def talk_dev(model_, head_, faces, audio16k, durations=(1, 2, 4)):
        import python_speech_features
        mf = python_speech_features.mfcc(audio16k, 16000, numcep=13, winlen=0.025, winstep=0.010)
        Ln = min((mf.shape[0] - mf.shape[0] % 4) / 100, faces.shape[0] / FPS)
        mf = mf[:int(round(Ln * 100))]; vf = faces[:int(round(Ln * FPS))]
        allsc, allv = [], []
        with torch.no_grad():
            for du in durations:
                sc, sv = [], []
                for i in range(int(math.ceil(Ln / du))):
                    A = torch.FloatTensor(mf[i * du * 100:(i + 1) * du * 100]).unsqueeze(0).to(dev)
                    V = torch.FloatTensor(vf[i * du * FPS:(i + 1) * du * FPS]).unsqueeze(0).to(dev)
                    if A.shape[1] < 4 or V.shape[1] < 1: continue
                    eA = model.forward_audio_frontend(A); eV = model.forward_visual_frontend(V)
                    eA, eV = model.forward_cross_attention(eA, eV)
                    sc.extend(head.forward(model.forward_audio_visual_backend(eA, eV), labels=None))
                    ov = head.vhead.FC(model.forward_visual_backend(eV).squeeze(1))
                    sv.extend((ov[:, 1] - ov[:, 0]).reshape(-1).cpu().numpy())
                allsc.append(sc[:len(vf)]); allv.append(sv[:len(vf)])
        n = min(len(s) for s in allsc + allv)
        return np.mean(np.array([s[:n] for s in allsc]), 0), np.mean(np.array([s[:n] for s in allv]), 0)

    LF.s3fd_batch = s3fd_dev; L.talk_scores = talk_dev            # only the device changes; the logic is lipdetect's
    detect_one(np.zeros((540, 540, 3), np.uint8))                   # warm-up

    os.makedirs(a.out_dir, exist_ok=True)
    ranges = json.load(open(a.ranges)) if a.ranges else make_ranges(a.video, a.piece)
    json.dump(ranges, open(os.path.join(a.out_dir, "ranges.json"), "w"))
    T = {}; t_all = time.time(); total_video = sum(e - s for s, e in ranges); done_video = 0; left = 0
    try:
        for s, e in ranges:
            fp = os.path.join(a.out_dir, f"range_{s:g}_{e:g}.json")
            if os.path.exists(fp): continue
            if time.time() - t_all > a.max_seconds: left += 1; continue
            waited = 0
            while (free_ram_gb() or 99) < min_free and waited < 900:         # let the computer breathe (max 15 min)
                if waited == 0: log(f"free RAM {free_ram_gb():.1f} GB < {min_free:.1f} GB -- waiting")
                time.sleep(15); waited += 15
            t = time.time()
            res = LF.run_range(s, e, a.video, (model, head, det), "s3fd", 2, 1, 540, None, timing=T)
            res["method"] = dict(det="s3fd", every=2, device=dev_name, runner="lipgpu.py", mesh=False)
            res.pop("mouth", None); res.pop("aperture", None)
            json.dump(res, open(fp + ".part", "w")); os.replace(fp + ".part", fp)
            done_video += e - s
            if dev_name == "mps":
                try: torch.mps.empty_cache()
                except Exception: pass
            fr = free_ram_gb(); st = f"; free RAM {fr:.1f} GB" if fr is not None else ""
            try:
                import psutil; st += f", CPU {psutil.cpu_percent():.0f}%, this process {psutil.Process().memory_info().rss / 2**30:.1f} GB"
            except Exception:
                pass
            log(f"done {s:g}-{e:g} in {time.time() - t:.0f} s (so far {time.time() - t_all:.0f} s){st}")
    finally:
        stop_awake()
    wall = time.time() - t_all
    if left:
        log(f"{left} pieces left -- run the same command again (it resumes)"); sys.exit(3)
    log(f"ALL PIECES DONE: {done_video:.0f} s of video in {wall:.0f} s" + (f" = {wall / done_video:.2f} s per second of video" if done_video else ""))
    json.dump(dict(device=dev_name, threads=threads, video_s=total_video, scanned_now_s=done_video, wall_s=round(wall, 1),
                   phases={k: round(v, 1) for k, v in T.items()}), open(os.path.join(a.out_dir, "timing.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
~~~~~~

## Appendix: `scripts/lipmesh.py`

<!-- FILE: scripts/lipmesh.py md5=09f625ba2b31251a71daec2ca578abdd -->
~~~~~~python
#!/usr/bin/env python3
"""
lipmesh.py -- the third lip measure of `lipdetect.py --range` alone: MediaPipe Face Mesh lip opening/closing
(mouth, aperture), decoded exactly as lipdetect.py decodes it. Resumable. Light: about 0.15 s per second of video.
Used with lipgpu.py (S3FD + TalkNet on the GPU); lipmerge.py joins the two halves.
Run it on LINUX when you can (a Linux machine, VM, WSL or Docker): MediaPipe's macOS build measures lip opening
slightly differently (ep24 test: 2 of 174 sentences flipped when Face Mesh ran on the Mac; 0 when it ran on Linux).
It can run at the same time as lipgpu.py.

  python3 lipmesh.py --video SRC.mp4 --out-dir W/lips_mesh [--ranges W/lips_nets/ranges.json | --piece 15] [--max-seconds 150]
"""
import argparse, json, os, subprocess, sys, time
import numpy as np
np.int = int
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lipfast as LF


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", required=True); p.add_argument("--out-dir", required=True)
    p.add_argument("--ranges", help="the same ranges file lipgpu.py used (OUT/ranges.json); default: --piece pieces over the video")
    p.add_argument("--piece", type=float, default=15)
    p.add_argument("--max-seconds", type=float, default=1e9); p.add_argument("--size", type=int, default=540)
    a = p.parse_args(); os.makedirs(a.out_dir, exist_ok=True); t_start = time.time(); left = 0
    if a.ranges: ranges = json.load(open(a.ranges))
    else:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import lipgpu
        L = lipgpu.video_length(a.video); ranges = []; s = 0
        while s < L: ranges.append([s, s + a.piece]); s += a.piece
    try: os.nice(5)
    except Exception: pass
    for s, e in ranges:
        fp = os.path.join(a.out_dir, f"mesh_{s:g}_{e:g}.json")
        if os.path.exists(fp): continue
        if time.time() - t_start > a.max_seconds: left += 1; continue
        t = time.time(); size = a.size
        # same decode as lipdetect.py --range's mouth pass (fps 25, scaled to size x size)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", a.video, "-vf",
                              f"fps=25,scale={size}:{size}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        fr = np.frombuffer(raw, np.uint8).reshape(-1, size, size, 3)
        mo, ap, _ = LF.mesh_pass(fr)
        json.dump(dict(window=[s, e], frames=len(fr), mouth=[round(float(v), 4) for v in mo], aperture=[round(float(v), 4) for v in ap]),
                  open(fp + ".part", "w")); os.replace(fp + ".part", fp)
        print(f"mesh {s:g}-{e:g} done ({time.time() - t:.0f}s)", flush=True)
    print("ALL DONE" if not left else f"{left} pieces left -- run again (resumes)")
    sys.exit(0 if not left else 3)


if __name__ == "__main__":
    main()
~~~~~~

## Appendix: `scripts/lipmerge.py`

<!-- FILE: scripts/lipmerge.py md5=667b83918546a28f89b0c43cf796e637 -->
~~~~~~python
#!/usr/bin/env python3
"""
lipmerge.py -- join the two halves of the lip scan into the normal lips_range files that `dubstitch.py lipsync` reads:
  NETS_DIR/range_<s>_<e>.json  (S3FD + TalkNet, from lipgpu.py)
  MESH_DIR/mesh_<s>_<e>.json   (Face Mesh, from lipmesh.py)
-> OUT_DIR/range_<s>_<e>.json  (identical format to lipdetect.py --range)

  python3 lipmerge.py --nets W/lips_nets --mesh W/lips_mesh --out W/lips_range [--ranges W/lips_nets/ranges.json]
"""
import argparse, json, os, sys
p = argparse.ArgumentParser(); p.add_argument("--nets", required=True); p.add_argument("--mesh", required=True)
p.add_argument("--out", required=True); p.add_argument("--ranges", help="default: NETS_DIR/ranges.json")
a = p.parse_args(); os.makedirs(a.out, exist_ok=True); missing = []
ranges = json.load(open(a.ranges or os.path.join(a.nets, "ranges.json")))
for s, e in ranges:
    nf = os.path.join(a.nets, f"range_{s:g}_{e:g}.json"); mf = os.path.join(a.mesh, f"mesh_{s:g}_{e:g}.json")
    if not (os.path.exists(nf) and os.path.exists(mf)): missing.append(f"{s:g}-{e:g}"); continue
    d = json.load(open(nf)); m = json.load(open(mf))
    if m["frames"] != d["frames"]: sys.exit(f"frame count differs in {s:g}-{e:g}: nets {d['frames']} mesh {m['frames']}")
    d["mouth"], d["aperture"] = m["mouth"], m["aperture"]; d.setdefault("method", {})["mesh"] = "lipmesh.py"
    json.dump(d, open(os.path.join(a.out, f"range_{s:g}_{e:g}.json"), "w"))
if missing: sys.exit(f"not merged (a half is missing -- run lipgpu.py / lipmesh.py again): {missing}")
print(f"merged {len(ranges)} pieces into {a.out}")
~~~~~~

## Appendix: `scripts/mksrt.py`

<!-- FILE: scripts/mksrt.py md5=5a3e56cd4fd99bcaa84241781f19e4a9 -->
~~~~~~python
#!/usr/bin/env python3
"""
mksrt.py -- Step 0B: make the SRTs of an episode from its audio/video (+ the script), when it has none.

  Dub side (audio + script):    python3 mksrt.py --media DUB.mp3 --lang it --script SCRIPT.docx --work W --out W/<SHOW>_EP<N>_IT.srt
  Source side (video + script): python3 mksrt.py --media SRC.mp4 --lang de --script SCRIPT.docx --work W --out W/<SHOW>_EP<N>_DE.srt
  (no script for a side: leave --script out; --names CHARACTERS.csv then fixes the spelling of names)

Writes two SRTs from the same cleaned, timed words:
  OUT                  subtitle rows for the burned-in subtitles (Steps 8-9): at most 6 words and 4 s per row
                       (--row-words, --row-seconds); a row also ends at a sentence end (3+ words) and at a 1-s pause.
  OUT_sentences.srt    one row per sentence: the SRT for the sentence matching (Steps 1A-4: words, analyze, ...).
  (+ OUT_raw.srt = as Whisper heard it, OUT_report.json = the cleaning report)

How:
1. Whisper (faster-whisper "small", CPU, 60-s pieces, resumable, cached in W/asr = the cache `dubstitch.py words` uses)
   hears every word with its time. Invented credit lines ("Sottotitoli ... a cura di") are removed. Where the script
   has 2+ words Whisper skipped and the audio has room for them, that stretch is heard again with the script as a hint.
2. Cleaning: the SRT-cleaning platform's method (srtclean.py = srt-cleaning-platform/pipeline.py, unchanged), run on a
   one-word-per-row SRT so every word keeps its own time: the script decides spelling, names, punctuation, quotes.
   Then small leftovers (1-3 words where voice and script still differ) take the script's words (--keep-heard: not).
3. The cleaned words are grouped into the two SRTs above.
`--engine mlx --model mlx-community/whisper-large-v3-turbo` uses the Apple GPU (better words without a script, less
exact times).
"""
import argparse, csv, difflib, json, os, re, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)


def ts(t):
    t = max(t, 0.0); ms = int(round(t * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def media_length(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(r.stdout.strip())


def heard_words(a):
    """[(start, end, word)] for the whole media."""
    if a.engine == "mlx":
        tag = re.sub(r"[^A-Za-z0-9.-]", "_", a.model)
        cache = os.path.join(a.work, "asr", f"{a.lang}_mlx_{tag}.json")
        if not os.path.exists(cache):
            import mlx_whisper
            r = mlx_whisper.transcribe(a.media, path_or_hf_repo=a.model, language=a.lang, word_timestamps=True,
                                       condition_on_previous_text=False, verbose=False)
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            json.dump([[w["start"], w["end"], w["word"].strip()] for s in r["segments"] for w in s.get("words", [])], open(cache, "w"), ensure_ascii=False)
        return [tuple(w) for w in json.load(open(cache)) if w[2].strip()]
    import dubstitch as D
    models = a.models or os.path.join(HERE, "..", "data", "models")
    words, todo = D.transcribe_words(a.media, os.path.join(a.work, "asr"), a.lang, models, 0.0, media_length(a.media),
                                     max_seconds=a.max_seconds, model_name=a.model)
    if todo: print(f"{todo} audio pieces still to do -- run the same command again (it resumes)"); sys.exit(3)
    return [(w[0], w[1], w[2]) for w in words]


# Whisper's well-known invented lines (credits it learned from subtitled TV), heard in silence or music at the end.
# TOLR ep24 (Italian): "Sottotitoli e revisione QTSS a cura di" after the last line.
HALLUCINATIONS = [r"sottotitoli\b.{0,60}?(a cura di|amara\.org)(\s+\S+)?", r"sottotitolato da\b.{0,40}",
                  r"untertitel(ung)?\b.{0,60}?(zdf|amara\.org|ard|funk)\S*", r"subtitles by\b.{0,40}", r"amara\.org"]


def drop_hallucinations(words):
    text, pos = "", []
    for i, w in enumerate(words):
        if text: text += " "
        pos.append((len(text), i)); text += w[2]
    drop = set()
    for pat in HALLUCINATIONS:
        for m in re.finditer(pat, text, re.I):
            drop.update(i for p, i in pos if m.start() <= p < m.end())
    return [w for i, w in enumerate(words) if i not in drop], [words[i][2] for i in sorted(drop)]


def _n(w):
    return re.sub(r"[^\w]", "", w.lower())


def listen_again(a, words, script_words):
    """Whisper sometimes skips a sentence inside a long piece (TOLR ep24: '«Emma Reali.» Scandì il suo nome.' at
    200.4-204.6 s came back as nothing). Where the script has 2+ words that were not heard AND the audio has room for
    them, that stretch is heard again alone with the script text as a hint (like `dubstitch.py words` does)."""
    if a.engine != "faster": return words, []
    H = [_n(w[2]) for w in words]; S = [_n(w) for w in script_words]
    sm = difflib.SequenceMatcher(None, H, S, autojunk=False); jobs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag not in ("insert", "replace") or (j2 - j1) - (i2 - i1) < 2: continue
        lo, hi = max(i1 - 2, 0), min(i2 + 1, len(words) - 1)       # 2 heard words around it: a neighbour can be mis-heard too
        t0, t1 = words[lo][0], words[hi][1]
        room = sum(max(words[k + 1][0] - words[k][1], 0) for k in range(lo, hi))   # silence Whisper left in that stretch
        if room < 0.18 * ((j2 - j1) - (i2 - i1)): continue          # no time in the audio for the missing words: really not said
        jobs.append((max(t0 - 0.4, 0), t1 + 0.4, " ".join(script_words[max(j1 - 3, 0):j2 + 3]), t0, t1))
    if not jobs: return words, []
    import dubstitch as D
    from faster_whisper import WhisperModel
    models = a.models or os.path.join(HERE, "..", "data", "models")
    model = WhisperModel(a.model, device="cpu", compute_type="int8", download_root=os.path.join(models, "whisper")); done = []
    for w0, w1, hint, c0, c1 in jobs:
        fp = os.path.join(a.work, "asr", f"{a.lang}_{a.model}_mksrt_redo_{w0:09.2f}_{w1:09.2f}.json")
        if not os.path.exists(fp):
            x = D._pcm16k(a.media, w0, w1 - w0)
            segs, _ = model.transcribe(x / 32768.0, language=a.lang, word_timestamps=True, beam_size=5, initial_prompt=hint,
                                       condition_on_previous_text=False)
            ws = [(round(w0 + w.start, 3), round(w0 + w.end, 3), w.word.strip(), round(w.probability, 2)) for sg in segs for w in sg.words]
            json.dump([list(w) for w in D.trim_words_to_voice(ws, x, w0) if D._letters(w[2])], open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        # swap exactly the heard words c0..c1 (first to last word of the stretch) for the new ones in the same stretch
        new = [(w[0], w[1], w[2]) for w in json.load(open(fp, encoding="utf-8")) if c0 - 0.15 <= w[0] and w[1] <= c1 + 0.15]
        if not new: continue
        n0, n1 = new[0][0], new[-1][1]                              # replace only what the new pass really covers
        old = [w for w in words if n0 - 0.05 <= w[0] and w[1] <= n1 + 0.05]
        hint_set = set(_n(x) for x in hint.split())
        if sum(_n(w[2]) in hint_set for w in new) > sum(_n(w[2]) in hint_set for w in old):
            words = sorted([w for w in words if w not in old] + new); done.append(f"{w0:.1f}-{w1:.1f}s")
    return words, done


def parse_blocks(path):
    """SRT -> [[index, 'start --> end', text]]; safe for blocks whose text is empty (the cleaner can leave some)."""
    L = open(path, encoding="utf-8-sig").read().split("\n"); out = []; k = 0
    while k < len(L):
        if L[k].strip().isdigit() and k + 1 < len(L) and "-->" in L[k + 1]:
            idx, tsl = L[k].strip(), L[k + 1].strip(); k += 2; txt = []
            while k < len(L) and not (L[k].strip().isdigit() and k + 1 < len(L) and "-->" in L[k + 1]):
                if L[k].strip(): txt.append(L[k].strip())
                k += 1
            out.append([idx, tsl, " ".join(txt)])
        else:
            k += 1
    return out


def script_wins(srt_path, script_words, max_words=3):
    """After the cleaning: small leftovers where the heard words and the script still differ (Whisper heard a stray
    'a'/'la'/'che', or missed a short word such as 'il', 'testa', '«Ah,') are settled in favour of the SCRIPT, so every
    word is spelled as written. Runs of more than max_words words stay as the cleaning left them (and stay flagged)."""
    script_words = glue(" ".join(script_words).split())
    blocks = [[i, t, glue(x.split())] for i, t, x in parse_blocks(srt_path)]
    W = [(w, bi) for bi, b in enumerate(blocks) for w in b[2]]
    sm = difflib.SequenceMatcher(None, [_n(w) for w, _ in W], [_n(w) for w in script_words], autojunk=False)
    out, done = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or max(i2 - i1, j2 - j1) > max_words:
            out += W[i1:i2]; continue
        bi = W[i1][1] if i1 < len(W) else (W[-1][1] if W else 0)
        if i1 == i2 and i1 > 0: bi = W[i1 - 1][1]                  # missing words join the card of the word before them
        out += [(w, bi) for w in script_words[j1:j2]]
        done.append(f"'{' '.join(w for w, _ in W[i1:i2])}' -> '{' '.join(script_words[j1:j2])}'")
    for b in blocks: b[2] = []
    for w, bi in out: blocks[bi][2].append(w)
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(f"{b[0]}\n{b[1]}\n{' '.join(b[2])}\n" for b in blocks if b[2]))
    return done


def write_cards(C, path, hold=0.0):
    """C = list of cards, each a list of (start, end, word). A card shows until its last word ends (+ hold seconds,
    never past the next card's start)."""
    with open(path, "w", encoding="utf-8-sig") as f:
        for i, c in enumerate(C, 1):
            end = c[-1][1] + hold
            if hold: end = min(end, max(c[-1][1], c[0][0] + 4.0))  # the extra hold never makes a row longer than 4 s
            if i < len(C): end = min(end, C[i][0][0])          # never overlap the next card
            # Final subtitle policy: guillemets are not wanted in delivered SRTs.
            text = re.sub(r"[«»]", "", " ".join(x[2] for x in c))
            f.write(f"{i}\n{ts(c[0][0])} --> {ts(max(end, c[0][0] + 0.2))}\n{text}\n\n")


SENT_END = re.compile(r"[.!?\u2026]+[»”\"')\]]*$")


def make_subs(words, max_words=6, max_dur=4.0, pause=1.0):
    """Subtitle rows for the burned-in subtitles: at most max_words words and max_dur seconds of speech per row.
    Each sentence (also cut at a pause of `pause` s) is split into the fewest rows that fit, with the words shared
    evenly (8 words -> 4 + 4, not 6 + 2); a row of 1-2 words then joins a neighbouring row when both still fit."""
    segs, cur = [], []
    for w in words:
        if cur and w[0] - cur[-1][1] >= pause: segs.append(cur); cur = []
        cur.append(w)
        if SENT_END.search(w[2]): segs.append(cur); cur = []
    if cur: segs.append(cur)
    rows = []
    for sg in segs:
        k = max(1, -(-len(sg) // max_words))
        while True:
            parts = [sg[round(i * len(sg) / k):round((i + 1) * len(sg) / k)] for i in range(k)]
            if all(p[-1][1] - p[0][0] <= max_dur for p in parts if p) or k >= len(sg): break
            k += 1
        rows += [p for p in parts if p]
    fits = lambda r: len(r) <= max_words and r[-1][1] - r[0][0] <= max_dur
    out = []
    for r in rows:                                       # a 1-2 word row joins the row before it when that still fits
        if out and len(r) <= 2 and r[0][0] - out[-1][-1][1] < pause and fits(out[-1] + r):
            out[-1] = out[-1] + r
        elif out and len(out[-1]) <= 2 and r[0][0] - out[-1][-1][1] < pause and fits(out[-1] + r):
            out[-1] = out[-1] + r                         # ... or the row after it
        else:
            out.append(r)
    return out


def make_sentences(words):
    """One row per sentence (for the sentence matching, Step 1A/1B): a sentence ends at . ! ? ... (+ closing quotes)."""
    C, cur = [], []
    for w in words:
        cur.append(w)
        if SENT_END.search(w[2]): C.append(cur); cur = []
    if cur: C.append(cur)
    return C


def glue(tokens):
    """A token with no letters/digits belongs to a word: '.', '»', '...' to the one before it, '«', '„' to the next."""
    out, pre = [], ""
    for t in tokens:
        if not re.search(r"\w", t):
            if re.fullmatch(r"[«„“(\[]+", t): pre += t
            elif out: out[-1] += t
            else: pre += t
        else:
            out.append(pre + t); pre = ""
    if pre: out.append(pre)
    return out


def read_word_blocks(path):
    """A cleaned one-word-per-block SRT -> [(start, end, word)]; a block that now holds several words shares its time."""
    out = []
    f = lambda x: int(x[:2]) * 3600 + int(x[3:5]) * 60 + float(x[6:].replace(",", "."))
    for idx, tsl, text in parse_blocks(path):
        if not text.strip(): continue
        a_, c_ = [x.strip() for x in tsl.split("-->")]
        t0, t1 = f(a_), f(c_); toks = glue(text.split()); n = len(toks)
        out += [(round(t0 + (t1 - t0) * k / n, 3), round(t0 + (t1 - t0) * (k + 1) / n, 3), t) for k, t in enumerate(toks)]
    return out


def _key(s):
    s = s.lower().replace("ph", "f").replace("ie", "i").replace("y", "i").replace("ä", "a").replace("ö", "o").replace("ü", "u")
    return re.sub(r"(.)\1+", r"\1", s)


def load_names(path, cols):
    names = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            for c in cols:
                for n in re.split(r"[\s/,]+", (row.get(c) or "").strip()):
                    if len(n) >= 3 and n.isalpha(): names.add(n[0].upper() + n[1:].lower())
    return sorted(names)


def fix_names(words, names):
    """Replace a capitalised heard word by a character name (or its genitive -s) when they sound the same."""
    cands = {}
    for n in names:
        cands[n] = n; cands[n + "s"] = n + "s"
    keys = {k: _key(k) for k in cands}; out = []; fixed = []
    for s, e, w in words:
        m = re.match(r"^(\W*)(\w+)(\W*)$", w)
        if m and m.group(2)[:1].isupper() and m.group(2) not in cands and len(m.group(2)) >= 5:
            b = m.group(2); kb = _key(b); best, br = None, 0.0
            for c, kc in keys.items():
                if c[0].lower() != b[0].lower() or abs(len(c) - len(b)) > 1: continue
                r = difflib.SequenceMatcher(None, kb, kc).ratio()
                if r > br: best, br = c, r
            if best and br >= 0.85:      # tested on ep24-26: every fix right (0.8 also 'fixed' real words: Braun->Bruno)
                fixed.append((b, best)); w = m.group(1) + best + m.group(3)
        out.append((s, e, w))
    return out, fixed


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--media", required=True); p.add_argument("--lang", required=True); p.add_argument("--out", required=True)
    p.add_argument("--work", required=True, help="the episode's work folder (Whisper cache W/asr is shared with `words`)")
    p.add_argument("--script", help="the script (.docx/.txt) of this language: cleans the SRT word by word (srtclean.py)")
    p.add_argument("--sensitivity", default="aggressive", choices=["conservative", "balanced", "aggressive"],
                   help="how freely the cleaner replaces heard words by the script (aggressive: tested best on TOLR ep24-26)")
    p.add_argument("--keep-heard", action="store_true", help="keep small heard/script differences as heard (default: the script's words win)")
    p.add_argument("--names", help="character list CSV: fixes the spelling of names (when there is no script)")
    p.add_argument("--names-cols", default="First Name (Original),Last Name (Original)")
    p.add_argument("--engine", default="faster", choices=["faster", "mlx"]); p.add_argument("--model", default="small")
    p.add_argument("--models"); p.add_argument("--max-seconds", type=float, default=1e9)
    p.add_argument("--row-words", type=int, default=6, help="subtitle rows: at most this many words (default 6)")
    p.add_argument("--row-seconds", type=float, default=4.0, help="subtitle rows: at most this long (default 4 s)")
    a = p.parse_args(); t0 = time.time()
    words = heard_words(a)
    words, gone = drop_hallucinations(words)
    if gone: print(f"dropped Whisper's invented credit line: {' '.join(gone)}")
    if a.script:
        import srtclean as P
        words, redone = listen_again(a, words, P.extract_script_words(a.script, list(P.STAGE_TAG_RE_DEFAULT)))
        if redone: print(f"listened again (Whisper had skipped script words there): {', '.join(redone)}")
    if a.names:
        words, fixed = fix_names(words, load_names(a.names, a.names_cols.split(",")))
        print(f"names corrected from {os.path.basename(a.names)}: {len(fixed)} " + ", ".join(sorted({f'{x}->{y}' for x, y in fixed}))[:600])
    base = a.out[:-4] if a.out.lower().endswith(".srt") else a.out
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    final = words
    if a.script:
        import srtclean as P
        from pathlib import Path
        write_cards(make_subs(words, a.row_words, a.row_seconds), base + "_raw.srt", hold=0.5)     # as heard, for reference
        # clean ONE WORD PER BLOCK, so every cleaned word keeps its own time and can be regrouped freely afterwards
        tmp = Path(a.work) / "srtclean"; tmp.mkdir(parents=True, exist_ok=True); per_word = tmp / "heard_words.srt"
        write_cards([[w] for w in words], str(per_word))
        res = P.clean_episode(0, a.script, str(per_word), tmp, tmp, sensitivity=a.sensitivity)
        rep = json.load(open(res["report_path"])); os.replace(res["report_path"], base + "_report.json")
        if not a.keep_heard:
            sw = P.extract_script_words(a.script, list(P.STAGE_TAG_RE_DEFAULT))
            first = (P.docx.Document(a.script).paragraphs[0].text if a.script.lower().endswith(".docx")
                     else open(a.script, encoding="utf-8", errors="replace").readline())
            if re.match(r"\s*(episode|episodio|folge|ep)\.?\s*\d+", first or "", re.I):     # drop the title line
                sw = " ".join(sw).split()[len(first.split()):]
            done = script_wins(res["out_path"], sw)
            if done: print(f"script spelling used for {len(done)} small leftovers: " + "; ".join(done))
        final = read_word_blocks(res["out_path"])
        flagged = [c for c in rep["checks"] if c["type"] not in ("corrected", "stage-direction-not-spoken")
                   and not (c["type"] == "script-only-not-inserted" and re.match(r"\s*(episode|episodio|folge|ep)\.?\s*\d+", c["script"], re.I))]
        print(f"cleaned with {os.path.basename(a.script)}: {rep['num_corrected']} corrections, {len(flagged)} places where voice and script differ")
    subs = make_subs(final, a.row_words, a.row_seconds); write_cards(subs, a.out, hold=0.5)
    sents = make_sentences(final); write_cards(sents, base + "_sentences.srt")
    print(f"{len(final)} words -> subtitles {a.out} ({len(subs)} rows, at most {a.row_words} words / {a.row_seconds:g} s)"
          f" + sentences {base}_sentences.srt ({len(sents)} rows, for the sentence matching)")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
~~~~~~

## Appendix: `scripts/srtclean.py`

<!-- FILE: scripts/srtclean.py md5=d4217626df812274a51fecdac2798dba -->
~~~~~~python
"""
pipeline.py — core SRT cleaning logic (show-agnostic)

Implements the "show-srt-cleaner" process:
  script (source of truth for wording)
    -> raw ASR SRT (source of truth for timing/block structure)
    -> word-level alignment
    -> cleaned SRT (same block count / indices / timestamps as raw,
       text corrected against the script: names, punctuation, dialogue
       quotes, numbers, capitalization, stage-direction removal)

Any genuine content mismatch between audio and script (not just a
spelling/formatting difference) is left as the original audio and
reported, never silently guessed.
"""
from __future__ import annotations

import csv
import difflib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _ensure(pkg, import_name=None):
    try:
        __import__(import_name or pkg)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg,
                                "--break-system-packages", "-q"])

_ensure("python-docx", "docx")
_ensure("openpyxl")

import docx  # noqa: E402

# ── Episode discovery ────────────────────────────────────────────────────────

SCRIPT_DIR_NAMES = {"script", "scripts", "screenplay", "screenplays", "transcript", "transcripts"}
SRT_DIR_NAMES = {"srt", "raw srt", "raw", "subs", "subtitles"}
SCRIPT_EXTS = {".docx", ".txt", ".md"}
SHEET_NAME_HINTS = ("localis", "localiz", "character", "canvas", "glossary", "name", "casting")


def _ep_num(name: str) -> int | None:
    for pat in [
        r"\bEp\s*0*(\d{1,4})\b",
        r"\bEpisode\s*0*(\d{1,4})\b",
        r"\bEP0*(\d{1,4})\b",
        r"[_\-\s]0*(\d{1,4})[_\-\s.]",
        r"[_\-\s]0*(\d{1,4})$",
        # Bare filenames like "1.srt", "9.srt" (no "Episode"/"Ep" prefix, no
        # separator) — single- and double-digit numbers both need to match,
        # not just 2-4 digits, otherwise episodes 1-9 get silently dropped.
        r"^0*(\d{1,4})$",
    ]:
        m = re.search(pat, name, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def _pick_dir(root: Path, names: set[str]) -> Path | None:
    for p in root.iterdir():
        if p.is_dir() and p.name.strip().lower() in names:
            return p
    # one level deeper, in case the show folder wraps another folder
    for p in root.iterdir():
        if p.is_dir():
            for q in p.iterdir():
                if q.is_dir() and q.name.strip().lower() in names:
                    return q
    return None


def find_localization_sheet(root: Path) -> Path | None:
    candidates = []
    for p in root.rglob("*"):
        if "output" in p.parts or p.suffix.lower() not in {".xlsx", ".xls", ".csv"}:
            continue
        low = p.stem.lower()
        if any(h in low for h in SHEET_NAME_HINTS):
            candidates.append(p)
    return candidates[0] if candidates else None


def scan_folder(root: Path) -> dict:
    root = Path(root)
    script_dir = _pick_dir(root, SCRIPT_DIR_NAMES)
    srt_dir = _pick_dir(root, SRT_DIR_NAMES)

    scripts: dict[int, Path] = {}
    script_search_root = script_dir if script_dir else root
    for p in script_search_root.rglob("*"):
        if "output" in p.parts or p.suffix.lower() not in SCRIPT_EXTS:
            continue
        ep = _ep_num(p.stem)
        if ep is not None and ep not in scripts:
            scripts[ep] = p

    srts: dict[int, Path] = {}
    srt_search_root = srt_dir if srt_dir else root
    for p in srt_search_root.rglob("*.srt"):
        if "output" in p.parts or "clean" in p.stem.lower():
            continue
        ep = _ep_num(p.stem)
        if ep is not None and ep not in srts:
            srts[ep] = p

    all_eps = sorted(set(scripts) | set(srts))
    episodes = []
    for ep in all_eps:
        missing = []
        if ep not in scripts:
            missing.append("script")
        if ep not in srts:
            missing.append("srt")
        episodes.append({
            "ep": ep,
            "title": scripts.get(ep, srts.get(ep)).stem if (ep in scripts or ep in srts) else str(ep),
            "script": str(scripts.get(ep, "")),
            "srt": str(srts.get(ep, "")),
            "ready": not missing,
            "missing": missing,
        })

    sheet = find_localization_sheet(root)
    return {
        "episodes": episodes,
        "scripts": scripts,
        "srts": srts,
        "script_dir": str(script_dir) if script_dir else "",
        "srt_dir": str(srt_dir) if srt_dir else "",
        "localization_sheet": str(sheet) if sheet else "",
    }


# ── Localization / character-name sheet (optional, validation only) ─────────

DE_HEADER_HINT_RE = re.compile(r"char.*name.*|name.*char.*", re.IGNORECASE)


def load_localization_names(sheet_path: str, locale: str = "DE") -> list[str]:
    """Best-effort extraction of the target-language character-name column.
    Does not assume the visible first row is the header; scans the first
    several rows for one containing a recognisable '<locale> name' header."""
    if not sheet_path:
        return []
    path = Path(sheet_path)
    rows: list[list[str]] = []
    try:
        if path.suffix.lower() == ".csv":
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = [row for row in csv.reader(f)]
        else:
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True)
            ws = wb.active
            for r in ws.iter_rows(values_only=True):
                rows.append(["" if c is None else str(c) for c in r])
    except Exception:
        return []

    header_row_idx, name_col_idx = None, None
    locale_l = locale.lower()
    for i, row in enumerate(rows[:10]):
        for j, cell in enumerate(row):
            cl = (cell or "").strip().lower()
            if not cl:
                continue
            if locale_l in cl and ("name" in cl or "character" in cl or "localis" in cl or "localiz" in cl):
                header_row_idx, name_col_idx = i, j
                break
        if header_row_idx is not None:
            break
    if header_row_idx is None:
        return []

    names = []
    for row in rows[header_row_idx + 1:]:
        if name_col_idx < len(row):
            val = (row[name_col_idx] or "").strip()
            if val and "(?" not in val and not val.lower().startswith(("note", "comment")):
                names.append(val)
    return sorted(set(names))


# ── Tokenising helpers ───────────────────────────────────────────────────────

ALNUM_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]")
STAGE_TAG_RE_DEFAULT = [r"\[(VOA|SFX|MUSIC|FX|NOTE|DIRECTION|STAGE|AMBIENT)[^\]]*\]\s*(?:[SE]\b)?"]

# Beyond the known-keyword bracket tags above, real scripts also use
# keywords/typos/punctuation the whitelist can't anticipate — MUSIK, MUSC,
# SFC, END, MICROPHONE FILTER, VOICE-OVER, SCENE CUT, "[CROWD: CHEERS,
# WHISTLES]", "[ERIK'S INNER THOUGHT, ]"... An earlier version tried to
# whitelist the allowed punctuation character-by-character and kept missing
# commas, semicolons, em-dashes, parens-within-brackets, etc. The reliable
# signal isn't the punctuation at all, it's that every LETTER in the bracket
# is upper-case — a viewer-facing bracket (a game-stat panel like
# "[Nascosto]" or "[Razza: Umano]") is never written in all caps in
# practice. So: capture any bracket span, then decide by checking case in
# Python rather than restricting what characters the regex itself accepts.
BRACKET_CONTENT_RE = re.compile(r"\[([^\[\]]{1,160})\]\s*(?:[SE]\b)?")


def _is_allcaps_tag_content(content: str) -> bool:
    letters = [ch for ch in content if ch.isalpha()]
    return bool(letters) and all(ch.isupper() for ch in letters)


def _strip_allcaps_brackets(text: str) -> str:
    return BRACKET_CONTENT_RE.sub(lambda m: "" if _is_allcaps_tag_content(m.group(1)) else m.group(0), text)


def _remove_orphan_brackets(text: str) -> str:
    """Strip only UNPAIRED '[' / ']' characters — e.g. a typo'd extra "] O]"
    left behind in the source after its real tag was already stripped.
    A properly paired "[...]" that survived this far (its content wasn't
    all-caps, so it wasn't a stage tag) is viewer-facing — e.g. "[Nascosto]",
    "[Razza: Umano]" — and must be left completely untouched, brackets
    included. This only ever deletes a lone bracket character, never any
    surrounding text."""
    if "[" not in text and "]" not in text:
        return text
    stack = []
    orphan_idx = set()
    for i, ch in enumerate(text):
        if ch == "[":
            stack.append(i)
        elif ch == "]":
            if stack:
                stack.pop()
            else:
                orphan_idx.add(i)
    orphan_idx.update(stack)
    if not orphan_idx:
        return text
    return "".join(ch for i, ch in enumerate(text) if i not in orphan_idx)


def strip_bracket_stage_tags(text: str, stage_patterns: list[str]) -> str:
    """Remove production-only bracket tags. stage_patterns (the known-keyword
    whitelist, e.g. STAGE_TAG_RE_DEFAULT + any extras) is applied first for
    exact backward-compatible matching, then the shape-based all-caps
    catch-all above, then any now-orphaned '[' or ']' — a leftover from a
    malformed/typo'd tag in the source doc (e.g. "...] O]") — is stripped as
    bare punctuation, same as the "<<"/">>" markup handling, since a
    literal stray bracket character never belongs in viewer dialogue. A
    still-paired "[...]" (mixed-case, so not a stage tag) is left untouched.
    A no-op (returns text unchanged) when stage_patterns is empty, i.e. the
    "remove stage tags" toggle is off."""
    if not stage_patterns:
        return text
    for pat in stage_patterns:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    text = _strip_allcaps_brackets(text)
    text = _remove_orphan_brackets(text)
    return text

# Some scripts wrap production-only direction in parentheses instead of
# brackets — e.g. "(EMILYS INNER VOICE)", "(PHONE FILTER)", "(BENEATH THE
# FOLLOWING)", "(UNTIL NOTED)", a bare "(END)". These are performance/
# audio-cue notes for the voice actor, never viewer dialogue. A keyword
# whitelist here misses far more than it catches in practice — real shows
# use vocabulary (and typos) no fixed list anticipates — so this uses the
# same all-caps shape rule as brackets: every letter inside the parens is
# upper-case. Checked against real show data with zero legitimate
# mixed-case parenthetical dialogue found, so this fires only on
# production notes, never on spoken content.
PAREN_CONTENT_RE = re.compile(r"\(([^()]{1,160})\)")


def strip_paren_stage_tags(text: str) -> str:
    return PAREN_CONTENT_RE.sub(lambda m: "" if _is_allcaps_tag_content(m.group(1)) else m.group(0), text)


# Some scripts use "<<" / ">>" as script-only markup (e.g. emphasis/beat
# markers for the voice actor). They carry no meaning for a viewer and must
# never leak into the cleaned SRT — strip just the marker characters and
# keep whatever text they wrap. Always on, independent of the stage-tag
# toggle, since this is punctuation-level markup, not a removable tag/span.
ARROW_MARKUP_RE = re.compile(r"<<|>>")

# The script's own documented convention: a capital "R" + colon directly
# before a line (e.g. 'R: "Verdammt nochmal,"') tells the sound engineer
# to apply reverb to that line -- a production instruction, never part of
# the spoken line itself (see the writer's KEY NOTES: 'Letter R indicates
# REVERB to be applied to the voice'). Strip the bare marker, keep the
# dialogue that follows it. Gated by the stage-tag toggle like the
# bracket/paren tags above (not always-on like ARROW_MARKUP_RE), since
# it's the same kind of removable production annotation.
REVERB_MARKER_RE = re.compile(r"\bR:\s*")

# Word docs sometimes embed invisible formatting/section-break characters
# (BOM / zero-width no-break space U+FEFF, zero-width space U+200B, and
# zero-width joiners) inline in the paragraph text itself -- python-docx
# returns them as part of .text, re.split()/str.split() treat them as a
# "word" since they are not whitespace, and they then survive straight
# into the cleaned SRT as a stray invisible glyph between real words.
# Always stripped, independent of the stage-tag toggle, like
# ARROW_MARKUP_RE -- this is a document-encoding artifact, never
# intentional content.
INVISIBLE_CHARS_RE = re.compile(r"[\ufeff\u200b\u200c\u200d\u2060]")

# Scripts use a run of plain divider characters (---, ***, ===, a long
# run of dashes, etc.) as a visual scene-break/section separator on its
# own line in the Word doc -- never spoken dialogue (seen in practice:
# a "---" or longer dash run stuck onto the end of the preceding
# sentence once the script is flattened to one line per paragraph).
# Three or more of the same divider punctuation mark in a row is not a
# pattern that occurs in real speech in any language this tool has
# seen, unlike "..." (ellipsis) or "!!!"/"???" (emphasis), which are
# deliberately left alone. Always stripped, independent of the
# stage-tag toggle, like ARROW_MARKUP_RE and INVISIBLE_CHARS_RE.
DIVIDER_RUN_RE = re.compile(r"[-\u2013\u2014_=*]{3,}")

CARDINALS = {
    0: "null", 1: "eins", 2: "zwei", 3: "drei", 4: "vier", 5: "fünf", 6: "sechs",
    7: "sieben", 8: "acht", 9: "neun", 10: "zehn", 11: "elf", 12: "zwölf",
    13: "dreizehn", 14: "vierzehn", 15: "fünfzehn", 16: "sechzehn", 17: "siebzehn",
    18: "achtzehn", 19: "neunzehn", 20: "zwanzig", 21: "einundzwanzig",
    22: "zweiundzwanzig", 23: "dreiundzwanzig", 24: "vierundzwanzig",
    25: "fünfundzwanzig", 30: "dreißig", 31: "einunddreißig",
}
ORDINAL_IRREG = {1: "erst", 3: "dritt", 7: "siebt", 8: "acht"}


def ordinal_stem(n: int) -> str | None:
    if n in ORDINAL_IRREG:
        return ORDINAL_IRREG[n]
    if n in CARDINALS:
        return CARDINALS[n] + ("t" if n < 20 else "st")
    return None


def norm(tok: str) -> str:
    return re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ\-]", "", tok.lower())


def compact(tok: str) -> str:
    return re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ]", "", tok.lower())


def numeral_matches_word(a_raw: str, b_raw: str) -> bool:
    """True if one side is a bare digit token and the other is a spelled-out
    number word — treated as the same numeral written two ways, regardless
    of whether it's in the small CARDINALS lookup table."""
    for x, y in ((a_raw, b_raw), (b_raw, a_raw)):
        if re.fullmatch(r"\d{1,6}", x) and re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", y):
            n = int(x)
            card = CARDINALS.get(n)
            ostem = ordinal_stem(n)
            yl = y.lower()
            if card and yl.startswith(card[:max(3, len(card) - 2)]):
                return True
            if ostem and yl.startswith(ostem):
                return True
            return True
    return False


def merge_punct_only(words: list[str]) -> list[str]:
    """Merge any token that carries no letters/digits (e.g. a trailing
    '...!"' emitted as its own token because of a stray space in the
    source doc) into the previous real token."""
    out: list[str] = []
    for w in words:
        if out and not ALNUM_RE.search(w):
            out[-1] = out[-1] + " " + w
        else:
            out.append(w)
    return out


# ── Script / SRT extraction ──────────────────────────────────────────────────

def extract_script_words(path: str | Path, stage_patterns: list[str] | None = None) -> list[str]:
    """Read the script and tokenise it into words. Stage-direction markup
    (bracket tags + parenthetical performance notes) is stripped here, on
    the raw text, BEFORE tokenising/aligning — not just afterward on the
    rebuilt SRT text — because a tag that straddles two SRT blocks (its
    opening half lands in block N, its closing half in block N+1) can't be
    matched by a single per-block regex and would otherwise leak a
    half-tag fragment into the cleaned subtitle. Stripping it before the
    script is even split into words means it can never be assigned to a
    block in the first place. Pass stage_patterns=[] (via remove_stage_tags
    off) to skip this entirely and keep the script text as-is."""
    path = Path(path)
    if path.suffix.lower() == ".docx":
        d = docx.Document(str(path))
        paras = [p.text.strip() for p in d.paragraphs if p.text.strip()]
        full_text = "\n".join(paras)
    else:
        full_text = path.read_text(encoding="utf-8", errors="replace")
    full_text = INVISIBLE_CHARS_RE.sub("", full_text)
    full_text = DIVIDER_RUN_RE.sub("", full_text)
    if stage_patterns:
        full_text = strip_bracket_stage_tags(full_text, stage_patterns)
        full_text = strip_paren_stage_tags(full_text)
        full_text = REVERB_MARKER_RE.sub("", full_text)
    return merge_punct_only(full_text.split())


def parse_srt(path: str | Path) -> list[dict]:
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    blocks = raw.strip("\n").split("\n\n")
    parsed = []
    for b in blocks:
        lines = b.split("\n")
        if len(lines) < 2:
            continue
        idx = lines[0].strip()
        ts = lines[1].strip() if len(lines) > 1 else ""
        text_lines = lines[2:] if len(lines) > 2 else []
        parsed.append({"idx": idx, "ts": ts, "text": " ".join(text_lines).strip()})
    return parsed


def merge_punct_only_srt(words: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Mirror of merge_punct_only() above, but for the SRT side's
    (word, block_index) tuples. merge_punct_only() merges a punctuation-only
    token (e.g. a lone '«' or '?!' emitted as its own token because of a
    stray space in the script doc) into the previous real token -- but it
    was only ever applied to script_words, never to srt_words. That
    asymmetry is what causes duplicated punctuation in the cleaned output:
    if the SAME lone mark appears as a separate token in the raw SRT too
    (e.g. '...visage.' '«' as two words), the script side already absorbed
    it into 'visage. «' (one token), so difflib has nothing left on the
    script side to match the SRT's standalone '«' against -- it gets
    classified as audio-only-extra and is re-appended literally, producing
    'visage. « «' (or 'passe ?! ?', 'bien ? » »', etc.) in the final text.
    Merging it on the SRT side the same way keeps both sides' tokenisation
    symmetric so a trailing mark is counted once, not twice. Only merges
    within the same block (same bpos) -- never across a block boundary,
    since that would move a word into the wrong subtitle."""
    out: list[tuple[str, int]] = []
    for w, bpos in words:
        if out and out[-1][1] == bpos and not ALNUM_RE.search(w):
            out[-1] = (out[-1][0] + " " + w, bpos)
        else:
            out.append((w, bpos))
    return out


def build_srt_words(blocks: list[dict]) -> list[tuple[str, int]]:
    words = []
    for bi, b in enumerate(blocks):
        for w in b["text"].split():
            words.append((w, bi))
    return merge_punct_only_srt(words)


# ── Alignment ────────────────────────────────────────────────────────────────

@dataclass
class AlignOptions:
    short_max: int = 6
    short_ratio: float = 0.55
    same_len_ratio: float = 0.4


PRESETS = {
    "conservative": AlignOptions(short_max=4, short_ratio=0.7, same_len_ratio=0.55),
    "balanced": AlignOptions(short_max=6, short_ratio=0.55, same_len_ratio=0.4),
    "aggressive": AlignOptions(short_max=10, short_ratio=0.4, same_len_ratio=0.3),
}


def align(srt_words, script_words, opts: AlignOptions):
    srt_norm = [norm(w) for w, _ in srt_words]
    script_norm = [norm(w) for w in script_words]
    sm = difflib.SequenceMatcher(None, srt_norm, script_norm, autojunk=False)
    final: list[tuple[str, int]] = []
    checks: list[dict] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                _, bpos = srt_words[i1 + k]
                final.append((script_words[j1 + k], bpos))
        elif tag == "replace":
            srt_chunk = srt_words[i1:i2]
            script_chunk = script_words[j1:j2]
            block_positions = [bp for _, bp in srt_chunk]
            same_len = len(srt_chunk) == len(script_chunk)
            short = max(len(srt_chunk), len(script_chunk)) <= opts.short_max
            a_c = "".join(compact(w) for w, _ in srt_chunk)
            b_c = "".join(compact(w) for w in script_chunk)
            ratio = difflib.SequenceMatcher(None, a_c, b_c).ratio()
            numeral_ok = (
                len(srt_chunk) == 1 and len(script_chunk) == 1
                and numeral_matches_word(norm(srt_chunk[0][0]), norm(script_chunk[0]))
            )
            if script_chunk and ((short and ratio > opts.short_ratio) or numeral_ok
                                  or (same_len and ratio > opts.same_len_ratio)):
                for k, w in enumerate(script_chunk):
                    m = min(k, len(block_positions) - 1) if block_positions else 0
                    bpos = block_positions[m] if block_positions else (final[-1][1] if final else 0)
                    final.append((w, bpos))
                checks.append({"type": "corrected", "srt": " ".join(w for w, _ in srt_chunk),
                                "script": " ".join(script_chunk),
                                "blocks": sorted(set(b + 1 for b in block_positions))})
            else:
                for w, bpos in srt_chunk:
                    final.append((w, bpos))
                checks.append({"type": "CHECK-content-mismatch", "srt": " ".join(w for w, _ in srt_chunk),
                                "script": " ".join(script_chunk),
                                "blocks": sorted(set(b + 1 for b in block_positions))})
        elif tag == "delete":
            srt_chunk = srt_words[i1:i2]
            for w, bpos in srt_chunk:
                final.append((w, bpos))
            checks.append({"type": "audio-only-extra", "srt": " ".join(w for w, _ in srt_chunk), "script": "",
                            "blocks": sorted(set(b + 1 for b in [bp for _, bp in srt_chunk]))})
        elif tag == "insert":
            script_chunk = script_words[j1:j2]
            checks.append({"type": "script-only-not-inserted", "srt": "", "script": " ".join(script_chunk),
                            "blocks": []})
    return final, checks


def rebuild_blocks(blocks: list[dict], final_words, stage_patterns: list[str]) -> list[dict]:
    by_block: dict[int, list[str]] = {}
    for w, bpos in final_words:
        by_block.setdefault(bpos, []).append(w)
    new_blocks = []
    for bi, b in enumerate(blocks):
        text = " ".join(by_block.get(bi, []))
        text = strip_bracket_stage_tags(text, stage_patterns)
        if stage_patterns:
            text = strip_paren_stage_tags(text)
            text = REVERB_MARKER_RE.sub("", text)
        text = ARROW_MARKUP_RE.sub("", text)
        text = INVISIBLE_CHARS_RE.sub("", text)
        text = DIVIDER_RUN_RE.sub("", text)
        # A guillemet can end up doubled ("« «Word", "word» »") even after
        # merge_punct_only_srt: that fix only catches a mark that was its
        # OWN separate token on the SRT side. Here the second mark is fused
        # directly onto the next/previous real word with no space at all
        # (e.g. raw ASR text "«Olak,"), so it was never a standalone token
        # to merge in the first place -- it surfaces when that raw word is
        # kept as-is (a rejected/mismatched replace) right next to a script
        # word that already carries the same mark merged onto its end. A
        # real double-quote-within-a-quote would have actual words between
        # the two marks, not just whitespace, so collapsing an immediately
        # repeated identical mark is safe.
        text = re.sub(r"([«»])\s*\1", r"\1", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        new_blocks.append({"idx": b["idx"], "ts": b["ts"], "text": text})
    return new_blocks


# ── Optional: timestamp continuity repair ────────────────────────────────────

TS_RE = re.compile(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})")


def _ts_to_ms(ts: str) -> int:
    h, m, s_ms = ts.split(":")
    s, ms = s_ms.split(",")
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


def _ms_to_ts(ms: int) -> str:
    ms = max(ms, 0)
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fix_timestamp_continuity(blocks: list[dict]) -> tuple[list[dict], int]:
    fixed = 0
    prev_end_ms = None
    out = []
    for b in blocks:
        m = TS_RE.match(b["ts"])
        if not m:
            out.append(b)
            continue
        start_ms, end_ms = _ts_to_ms(m.group(1)), _ts_to_ms(m.group(2))
        new_ts = b["ts"]
        if prev_end_ms is not None and start_ms != prev_end_ms:
            new_ts = f"{_ms_to_ts(prev_end_ms)} --> {m.group(2)}"
            fixed += 1
        out.append({"idx": b["idx"], "ts": new_ts, "text": b["text"]})
        prev_end_ms = end_ms
    return out, fixed


# ── Quote-style normalisation (optional) ─────────────────────────────────────

QUOTE_CHARS = set("„“”\"«»")

QUOTE_STYLES = {
    "german": ("„", "“"),
    "english": ("“", "”"),
    "straight": ('"', '"'),
}


def force_quote_style(blocks: list[dict], style: str) -> list[dict]:
    if style not in QUOTE_STYLES:
        return blocks
    open_c, close_c = QUOTE_STYLES[style]
    full_text = "\n".join(b["text"] for b in blocks)
    out_chars = []
    n = 0
    for ch in full_text:
        if ch in QUOTE_CHARS:
            out_chars.append(open_c if n % 2 == 0 else close_c)
            n += 1
        else:
            out_chars.append(ch)
    new_full = "".join(out_chars).split("\n")
    return [{"idx": b["idx"], "ts": b["ts"], "text": t} for b, t in zip(blocks, new_full)]


# ── Validation ────────────────────────────────────────────────────────────────

def detect_quote_style(words: list[str]) -> str | None:
    """Guess which dialogue-quote convention a script actually uses. This
    matters because '“' (U+201C) is the CLOSING mark in German „…“ style but
    the OPENING mark in English “…” style — the same character means the
    opposite thing depending on convention, so counts can't be paired
    without knowing which one is active."""
    text = " ".join(words)
    if "„" in text:
        return "german"
    if "“" in text or "”" in text:
        return "english"
    if "«" in text or "»" in text:
        return "guillemet"
    if '"' in text:
        return "straight"
    return None


def validate(raw_blocks: list[dict], new_blocks: list[dict], quote_style: str | None) -> dict:
    issues = []
    if len(raw_blocks) != len(new_blocks):
        issues.append(f"block count changed: {len(raw_blocks)} -> {len(new_blocks)}")
    for rb, nb in zip(raw_blocks, new_blocks):
        if rb["idx"] != nb["idx"]:
            issues.append(f"index changed: {rb['idx']} -> {nb['idx']}")

    full = "\n".join(b["text"] for b in new_blocks)
    pairs = {
        "german": ("„", "“"),
        "english": ("“", "”"),
        "guillemet": ("«", "»"),
        "straight": ('"', '"'),
    }
    if quote_style in pairs:
        open_c, close_c = pairs[quote_style]
        o = full.count(open_c)
        c = full.count(close_c) if close_c != open_c else o  # straight: even-count check below
        if open_c == close_c:
            balanced = (o % 2 == 0)
            c = o
        else:
            balanced = (o == c)
    else:
        o = c = 0
        balanced = True  # no dialogue-quote convention detected in this show — nothing to balance
    return {"issues": issues, "quote_style_detected": quote_style,
            "quote_open": o, "quote_close": c, "quotes_balanced": balanced}


def write_srt(blocks: list[dict], path: Path) -> None:
    lines = []
    for b in blocks:
        lines += [b["idx"], b["ts"], b["text"], ""]
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def reclassify_stage_only_checks(checks: list[dict], stage_patterns: list[str]) -> list[dict]:
    """A 'script-only-not-inserted' check whose script text is ENTIRELY a
    production-only stage-direction tag (e.g. '[MUSIC: ...]', '[SFX: ...]')
    was correctly never spoken — that's expected, not a review item. Relabel
    those as informational so they stop inflating num_flagged. Anything with
    leftover real words (or a viewer-facing tag not in stage_patterns, e.g.
    a game-stat panel like '[NOME: ...]') keeps its original type."""
    if not stage_patterns:
        return checks
    out = []
    for c in checks:
        if c["type"] == "script-only-not-inserted":
            remainder = strip_bracket_stage_tags(c["script"], stage_patterns)
            remainder = strip_paren_stage_tags(remainder)
            if not ALNUM_RE.search(remainder):
                c = {**c, "type": "stage-direction-not-spoken"}
        out.append(c)
    return out


def find_quote_imbalance_suspects(checks: list[dict], quote_style: str | None) -> list[dict]:
    """When quotes end up unbalanced, it's almost always because one side of
    a quote pair fell inside a flagged (not auto-applied) chunk — the other
    side got emitted, this one didn't. Surface exactly which flagged item(s)
    carry an odd/unequal quote-mark count so a human doesn't have to hunt for
    the culprit line by line."""
    pairs = {"german": ("„", "“"), "english": ("“", "”"), "guillemet": ("«", "»"), "straight": ('"', '"')}
    if quote_style not in pairs:
        return []
    open_c, close_c = pairs[quote_style]
    suspects = []
    for c in checks:
        if c["type"] in ("corrected", "stage-direction-not-spoken"):
            continue
        text = f"{c.get('script', '')} {c.get('srt', '')}"
        o = text.count(open_c)
        cl = text.count(close_c)
        unbalanced = (o != cl) if open_c != close_c else (o % 2 == 1)
        if unbalanced:
            suspects.append(c)
    return suspects


# ── Top-level: clean one episode ─────────────────────────────────────────────

def clean_episode(ep: int, script_path: str, srt_path: str, out_dir: Path, report_dir: Path,
                   sensitivity: str = "balanced", fix_timestamps: bool = False,
                   remove_stage_tags: bool = True, extra_stage_patterns: list[str] | None = None,
                   quote_style: str = "script", canonical_names: list[str] | None = None,
                   log=lambda msg: None) -> dict:
    opts = PRESETS.get(sensitivity, PRESETS["balanced"])
    stage_patterns = list(STAGE_TAG_RE_DEFAULT) if remove_stage_tags else []
    if extra_stage_patterns:
        stage_patterns += extra_stage_patterns

    # Stage tags are stripped from the script text up front (see
    # extract_script_words) so a tag can never end up split across two SRT
    # blocks. rebuild_blocks() below still re-applies the same stripping to
    # the assembled block text as a defense-in-depth safety net.
    script_words = extract_script_words(script_path, stage_patterns)
    raw_blocks = parse_srt(srt_path)
    srt_words = build_srt_words(raw_blocks)

    log(f"aligning {len(srt_words)} audio words against {len(script_words)} script words")
    final_words, checks = align(srt_words, script_words, opts)

    new_blocks = rebuild_blocks(raw_blocks, final_words, stage_patterns)
    checks = reclassify_stage_only_checks(checks, stage_patterns)

    ts_fixed = 0
    if fix_timestamps:
        new_blocks, ts_fixed = fix_timestamp_continuity(new_blocks)

    if quote_style != "script":
        new_blocks = force_quote_style(new_blocks, quote_style)
        effective_quote_style = quote_style
    else:
        effective_quote_style = detect_quote_style(script_words)

    v = validate(raw_blocks, new_blocks, effective_quote_style)
    quote_suspects = find_quote_imbalance_suspects(checks, effective_quote_style) if not v["quotes_balanced"] else []

    name_flags = []
    if canonical_names:
        full_text_norm = norm(" ".join(b["text"] for b in new_blocks))
        for cname in canonical_names:
            cnorm = norm(cname)
            if cnorm and cnorm not in full_text_norm:
                name_flags.append(cname)

    out_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{Path(srt_path).stem}.srt"
    write_srt(new_blocks, out_path)

    corrected = [c for c in checks if c["type"] == "corrected"]
    informational = [c for c in checks if c["type"] == "stage-direction-not-spoken"]
    flagged = [c for c in checks if c["type"] not in ("corrected", "stage-direction-not-spoken")]
    report = {
        "episode": ep, "script": str(script_path), "srt": str(srt_path),
        "num_blocks": len(raw_blocks), "num_corrected": len(corrected),
        "num_flagged": len(flagged), "num_stage_directions_not_spoken": len(informational),
        "timestamp_fixes": ts_fixed, "validation": v, "quote_imbalance_suspects": quote_suspects,
        "canonical_names_missing": name_flags, "checks": checks,
    }
    report_path = report_dir / f"{Path(srt_path).stem}_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ep": ep, "out_path": str(out_path), "report_path": str(report_path),
        "num_blocks": len(raw_blocks), "num_corrected": len(corrected),
        "num_flagged": len(flagged), "quotes_balanced": v["quotes_balanced"],
        "quote_imbalance_blocks": sorted({b for s in quote_suspects for b in s["blocks"]}),
        "issues": v["issues"], "name_flags": name_flags,
    }
~~~~~~

## Appendix: `scripts/mkdec.py`

<!-- FILE: scripts/mkdec.py md5=72fa5cde309f6bdb70c0529da328e79b -->
~~~~~~python
# builds W/decisions.json from a compact spec; every index is checked against a keyword from its text
import json,sys
W=sys.argv[1]; spec=sys.argv[2]
p=json.load(open(W+'/prep.json')); dub=p['dub']; src=p['src']
D={"groups":[],"assign":{},"confirm":{},"repeat":{},"gap":{},"unused_notes":{}}
def chk(lst,i,kw,lab):
    t=lst[i]['text']
    assert kw.lower() in t.lower(), f"{lab}{i} '{t}' lacks '{kw}'"
    return t[:30].strip()
def s0(s): return s[0] if isinstance(s,list) else s
def G(members,s,skw,why):
    D["groups"].append({"members":[m for m,_ in members],"dub_prefixes":[chk(dub,m,k,'DUB#') for m,k in members],
      "src":s,"src_prefix":chk(src,s0(s),skw,'SRC#'),"reason":why})
def A(i,kw,s,skw,why): D["assign"][str(i)]={"dub_prefix":chk(dub,i,kw,'DUB#'),"src":s,"src_prefix":chk(src,s0(s),skw,'SRC#'),"reason":why}
def C(i,why): D["confirm"][str(i)]=why
def GAP(i,kw,why): chk(dub,i,kw,'DUB#'); D["gap"][str(i)]=why
def U(s,kw,why): chk(src,s,kw,'SRC#'); D["unused_notes"][str(s)]=why
def R(later,earlier): D["repeat"][str(later)]=earlier
exec(open(spec).read())
json.dump(D,open(W+'/decisions.json','w'),indent=1,ensure_ascii=False)
print("groups",len(D["groups"]),"assign",len(D["assign"]),"confirm",len(D["confirm"]),"gap",len(D["gap"]),"unused",len(D["unused_notes"]))
~~~~~~

## Appendix: `scripts/merged.py`

<!-- FILE: scripts/merged.py md5=8d476f60331553013a75bf2275880ba8 -->
~~~~~~python
import json,sys
W=sys.argv[1]; L=int(sys.argv[2]) if len(sys.argv)>2 else 100
p=json.load(open(W+'/prep.json')); au=p['auto']; dub=p['dub']; src=p['src']
def srcs(i):
    x=au.get(str(i))
    if not x or x.get('src') is None: return []
    s=x['src']; return s if isinstance(s,list) else [s]
out=[]; last=-1; si=0
for i,d in enumerate(dub):
    ss=srcs(i)
    if ss and (au.get(str(i)) or {}).get('kind','').startswith('DP'):
        while si<=max(ss) and si<len(src):
            s=src[si]; print(f"    S{si:3d} {s['start_ms']/1000:6.1f}-{s['end_ms']/1000:6.1f} {s['text'][:L]}"); si+=1
    x=au.get(str(i)) or {}
    tag=(x.get('kind','UNMATCHED') or '')[:6]
    print(f"D{i:3d} {d['start_ms']/1000:6.1f}-{d['end_ms']/1000:6.1f} {d['text'][:L]}  -> {ss} {tag} {x.get('score',0):.2f}" if ss else f"D{i:3d} {d['start_ms']/1000:6.1f}-{d['end_ms']/1000:6.1f} {d['text'][:L]}  -> ??")
while si<len(src):
    s=src[si]; print(f"    S{si:3d} {s['start_ms']/1000:6.1f} {s['text'][:L]}"); si+=1
~~~~~~

## Appendix: `mac_lipscan/mac_runner.command`

<!-- FILE: mac_lipscan/mac_runner.command md5=213c5039c1d5e7d568f1a0bcfa7029cb -->
~~~~~~bash
#!/bin/bash
# Mac runner: runs job scripts that appear in ./jobs (one at a time), natively on this Mac.
# Output of each job -> ./done/<job>.log ; heartbeat -> runner_status.txt. Close this window to stop it.
cd "$(dirname "$0")"
mkdir -p jobs done
echo "runner started $(date) on $(sysctl -n machdep.cpu.brand_string)"
while true; do
  echo "idle $(date +%H:%M:%S)" > runner_status.txt
  for j in jobs/*.sh; do
    [ -e "$j" ] || continue
    name=$(basename "$j" .sh); mv "$j" "done/$name.sh"
    echo "running $name since $(date +%H:%M:%S)" > runner_status.txt
    echo "$(date +%H:%M:%S) running job $name"
    caffeinate -i bash "done/$name.sh" > "done/$name.log" 2>&1; rc=$?
    echo "$(date +%H:%M:%S) job $name finished (exit $rc)"; echo "exit $rc" >> "done/$name.log"
  done
  sleep 5
done
~~~~~~
