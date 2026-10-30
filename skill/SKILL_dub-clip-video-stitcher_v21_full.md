---
name: "dub-clip-video-stitcher"
description: "Use when matching a foreign-language dub's SRT+audio against the source-language SRT+video of the same episode (any show: e.g. TDMB Italian vs US, TOLR Italian vs German, WBM Italian vs US without SRTs) to reuse source footage and stitch a localized cut synced to the dub audio, with lip-sync rows, transitions, subtitles and final checks. Self-contained: all tool code is included. v19: dynamic subtitle strip for HIH. v20: scene edit by Claude (parallel, same-location B-roll, never a talking mouth over narration), Windows lip scan on ONNX Runtime, one-command episode runner. v21: B-roll ONLY for slow lines (hard rule)."
---

# Dub-to-source clip matching & video stitching

Given one episode's **dub** (subtitle `.srt` + audio `.wav`) and the **source** version of the
same episode (subtitle `.srt` + video `.mp4`), produce the complete episode package.

## Run rule (owner, 2026-10-22): every step, every episode
- **Always run every step of the pipeline**, in order, for every episode: Step 0B (Whisper both sides), 1A/1B, Step 2
  (the read by meaning), sheet, manifest, the lip scan (Step 4B), groups + the fast-clip rule, render, verify,
  bookends, the subtitle videos in the show's style, translation, check. Never skip or replace a step on your own.
- **If a step cannot run, stop and ask the owner.** Say which step and why (e.g. the Mac runner is stopped, the Dub Studio
  app is not running, the computer is not reachable, the AI gateway/VPN is off). Ask him to start it (double-click
  `adaptation-tool/mac_lipscan/mac_runner.command`; the app: `dub-studioo/app/scripts/start.sh`), wait, then continue.
  Never deliver an episode that skipped a step without saying so first.
- **AI steps need the gateway (v20).** Before Step 2 (automatic), 4C and the translation fallback, the gateway must
  answer (`run_episode.sh` checks it). If it does not (VPN off, `.env` missing), stop and ask the owner; never let the
  calls time out for minutes.
- **Time every step and report it after every episode (owner, 2026-10-28; mandatory for every agent: Claude, Codex, a
  person).** Every step's run time (and the AI cost of the Claude steps) goes into `W/run_times.tsv` — `run_episode.sh`
  does it by itself; when you run steps by hand, write one line per step yourself (Step 11.1). After the episode, give
  the owner the table from `scripts/run_report.py W` (also saved as `OUT/<show>_ep<N>_run_times.md`), failed tries
  included. An episode report without the time table is incomplete.

## What changed in v21 (2026-10-28, owner)
- **Hard rule: B-roll only for slow lines (Step 4C).** Until v20 Claude could cut to reaction shots anywhere (TGS ep32:
  12% of the B-roll time on normal-speed lines, 5% on fast lines). Now every line shows its own footage as matched; only
  a line whose own footage plays below 0.8x gets B-roll, and only for the seconds its footage cannot fill at 0.8x; fast
  lines are trimmed. The B-roll itself must be good: reaction / neutral / establishing, no moving mouth under narration,
  the line's place. Checked by code (checked against the delivered ep32 cut: 19 B-roll pieces on lines that were not
  slow, 43 slow lines with more B-roll than needed, 20 B-roll pieces of action/talking footage).
- `run_episode.sh`: `AIP` (folder of `dubstudio/`), `STOP_AFTER` (stop after a step, for tests); works with the Mac's
  old bash 3.2 (empty optional arguments no longer stop it).
- `groups` (dubstitch.py): a lip group no longer runs through source footage that another row plays (TGS ep34 rows 14-15
  tell the spy beat earlier than the source: the lip group over rows 17-22 ran through it and `groups` stopped with "two
  groups would play the same US footage"); ep32's automatic edit is unchanged by this. `verify` accepts a piece shorter
  than one frame.
- Scene edit, parallel: a stretch may continue the shot the stretch before ends on (that footage counts as its own), so
  the jump-cut check and the footage sharing can no longer contradict each other (TGS ep34 5-min test: 2 stretches were
  stuck on it).
- **Sentence splitting with “ quotes (2026-10-29, TGS ep34 row 31):** a German script that opens a quote with “ (instead
  of „) was not split: „…zerschellen. “Du bist so ein erbärmlicher Lügner!”“ became ONE row and got the "terrible liar"
  clip for 14 s. `dubstitch.py` now starts a sentence at any opening quote („ « “ ‘ ‚ ‹) and `mksrt.py` ends one at a
  German closing “. ep32 is unchanged by this (215 sentences either way).
- **An added event is a gap, never another line's footage (Step 2 automatic):** a dub line that tells an event no source
  line tells (she smashes a cup) is "not found"; it is not grouped with its neighbour just to get footage. In the scene
  edit a gap line has NO own footage and is covered with B-roll only (reaction / neutral shot of the same place, no
  moving mouth) — not black, and not the neighbour's clip stretched over it.
- `run_episode.sh`: `STOP_AFTER` also stops when that step was already done (it ran on into the full scene edit).
- **Sentence splitting can no longer merge two sentences (2026-10-29):** after . ! ? … (and any closing quotes) a new
  sentence starts whenever the next word does not begin with a small letter (a capital, a digit, any quote mark, a dash,
  a bracket). `words` prints a sentence check ("rows that still hold two sentences: none"). TGS ep31 had 4 such merged
  rows ("…auf Viktor. “Immerhin wissen wir eines…”"); ep32 and ep34 now split correctly.
- **NOT MATCHED is a result, not a failure (owner, 2026-10-29):** every dub row whose meaning no source sentence carries
  (an event, interjection, thought or order the adaptation added) is "not found": the story is changed there. Step 2
  never forces a match and never lends a neighbour's footage. The mapping sheet lists them on its own tab
  "Not matched (N)" (red), `sheet` prints them, and the report to the owner names them (Step 11). In the scene edit they
  get B-roll of the same place (a dialogue gap line has no speaker footage, so no mouth check). TGS ep34: 11 rows.

- **B-roll at least 1.3 s; no transition into a B-roll (owner, 2026-10-29).** Short cutaways (a few hundred ms to 1 s)
  looked jumpy. Every B-roll piece now lasts at least 1.3 s; a slow line that needs less takes either one 1.3-s B-roll
  (its own clip then plays faster, up to 1.25x) or none (its own clip slower, down to 0.65x). `scene_transitions.py` now
  puts a transition at every clip switch except the cut INTO a B-roll (clip -> B-roll straight, B-roll -> next clip with
  a transition; `--scenes-only` = the older story-scene plan). ep34 first 5 min: B-roll 47 pieces -> 35 (all >= 1.3 s),
  transitions 104 -> 56 + 34 straight cuts into B-roll.
- **A not-matched line may show what it tells (owner, 2026-10-29):** on a NOT FOUND (gap) line the B-roll may also be the
  action / object shot that shows exactly the event the line tells ("Der Spion im Auto" -> the spy in his car). Never a
  talking mouth, never another line's matched footage (action footage is still shown once).
- **The mouth on screen is the speaker's (owner, 2026-10-29, TGS ep34 0:01).** Paul says "Anya, bitte! Das ist nicht
  das, wonach es aussieht!" but Step 2 matched it to Ava's source line "If it's not what it looks like, how do you explain
  this?" (same words, other speaker), so Ava talked over Paul's voice, though Paul's talking footage of the same office
  exists. `sceneedit.py` now asks Claude once (text only, cached in `W/speakers.json`) who says every dub dialogue line
  and who talks in every source shot with a moving mouth. A dialogue line whose own footage shows someone else talking
  is a WRONG-SPEAKER line: that footage is dropped from its own footage, and it is shown with a SPEAKER shot (its speaker
  talking at the same location, the mouth starting with the voice; talking footage used elsewhere may repeat, 30 s
  apart, zoomed) or a face that does not talk. Every piece is checked: a moving mouth during a dialogue line must be its
  speaker's. A dialogue line Claude names "narration" (a thought) gets no speaker. TGS ep34: 8 wrong-speaker lines (e.g. 0:01, 3:47
  Anya's "Aber ich glaube dir kein beschissenes Wort!" on Paul talking, 7:58 Charlotte's "W-was?" on Paul on the phone);
  first 5 minutes rerun: pieces with the wrong person talking 3 -> 0, scene edit $4.95.
- **Story order inside a group (owner, 2026-10-29, TGS ep34 0:05-0:28).** Step 2 joined rows 3-6 onto source S1-S4 and the
  automatic edit spreads that footage evenly, so the phone thrust ("Ava shoved the photo in his face", S1) played on row 3
  ("over the past weeks she had belittled him") instead of row 5 ("she waved the phone wildly in front of his face"); the
  scene edit kept it there because of the own-footage rule. Now the rows of a group share the group's own footage and
  Claude places it in story order; speed and B-roll allowance are counted per group; stretches never cut through a group.
  ep34 first minute: phone thrust at 0:17 on row 5; rows 3-4 show Paul uneasy / staring and Anya glaring, standing with
  the phone. Scene edit $1.23 for the minute.
- **Gap plan: not-matched lines get the right person, talking or not (owner, 2026-10-29).** The old rule "a not-matched
  line is B-roll only, never a talking mouth" put Paul's SILENT face over his own "Anya, bitte!" at 0:00. Now one text-only
  Claude call per episode reads the story and the B-roll book (every shot of the line's place: who, type, who talks,
  what happens) and plans every not-matched line: `talking` / `reaction` / `action` / `neutral`, who, suggested shots
  (`W/gap_plan.json`). ep34's 11 lines: "Anya, bitte!" -> Paul talking; "Oh mein Gott." -> Charlotte talking on the phone;
  "Der Spion im Auto" -> the car flashback; the coffee cup (no cup shot exists) -> Anya glaring; "Und das werde ich
  niemals zulassen, blitzte es ihm durch den Kopf" -> Paul thinking. Code checks the planned person is on screen and a
  talking line starts on that person's mouth. ep34 first minute: Paul talks from "Anya, bitte!" on; 0 problems left; $0.70.
- **No opening with a person speaking (owner, 2026-10-29).** `bookends` put the source's 0.87 s before its first subtitle in
  front of TGS ep34: Anya with a moving mouth, just before her line, while the dub's first line is Paul's. Now `bookends`
  reads the work folder's lip scan (`lipsync.json`): more than 0.2 s of moving mouth inside the opening = no opening (it
  prints "opening left out: ... shows a person speaking"); the video starts with the dub. Openings without a talking
  face (titles, music, establishing shots) are kept as before.
- **B-roll at least 2 s, lengthened by code (owner, 2026-10-29).** MIN_BROLL 1.3 -> 2.0 s. A B-roll Claude plans shorter is
  lengthened to 2 s by `fix_short_broll` (applied to every Claude answer before the check): the missing time comes from the
  clip just before it (else the clip after it); that clip keeps its footage and plays faster, and above 1.25x shows a
  shorter part (its start kept). Owner's example: clip 1-3 s + B-roll 3-4.3 s -> clip 1-2.3 s + B-roll 2.3-4.3 s. On the
  previous ep34 5-min cut it would have lengthened 14 B-rolls; 2 had no clip next to them and go back to Claude.
- **Face map step (owner, 2026-10-29): who is on screen, where, face or back, who talks -- from the picture.** New step
  `facemap` after `library` (`adaptation-tool/scripts/facemap.py` -> `<library>/face_map.json`). It runs in its own Python
  environment (FACEPY, the Mac's `mac_lipscan/venv_face`; never install these into the lip-scan venv -- they upgrade numpy
  and break mediapipe):
  `uv venv --python 3.12 venv_face && uv pip install --python venv_face/bin/python -U insightface onnxruntime ultralytics opencv-python numpy`
  (Windows: the same with `onnxruntime-directml` instead of `onnxruntime`). Models download on first use: InsightFace
  `buffalo_l` (~290 MB, ~/.insightface) and `yolo26l.pt` (~50 MB).
  1. every library shot at 5 frames/s: InsightFace (face box, head pose, identity) + YOLO26-L (bodies every 2nd frame;
     phones: a face inside a phone is a photo);
  2. faces -> identity groups (cosine >= 0.40); a group is named by VOTE of the library's one-person shots (no Claude);
     only groups without a clear vote go to Claude with reference faces (one call);
  3. a body without a face = someone seen from behind: named by elimination (the library's people of the shot minus the
     faces found), else Claude from a body crop with the others greyed out;
  4. who talks: `lipfast.py` now keeps every face track's own TalkNet scores and boxes (the lip-scan intervals are
     unchanged: ep34 rescan compared with `compare_lips.py`: all 42 pieces match); a track takes the name of the faces it
     covers; talking = score > 0 for >= 0.3 s.
  `sceneedit.py` uses it when present: who talks in a shot (and when) comes from the face map (the text-only speaker
  guess is the fallback); every shot in Claude's prompt says "ON SCREEN: Ava (centre, face), Aiden (left, back);
  talking: Ava 80.0-81.8". Test (ep34, 40 shots, hand-checked key): on-screen speaker 36/37, no speaker given to
  narration (0/30); Claude's text guess was 34/37 with 3 given to narration. Time: ~8-12 min per episode on the Mac.
- **Keep the best correction round (2026-10-30).** A correction round can make a stretch worse (ep34: stretch 34-54 s
  ended on the phone-thrust action again, over narration, in its last round). `sceneedit.py` now keeps the round with
  the fewest problems (a tie goes to the later one; in a whole-episode fix the cut before the fix counts too).
  ep34 first 5 min with every rule (face map, speakers, groups, gap plan, 2-s B-roll, opening rule): 110 pieces, 30
  B-roll (68 s, all >= 2.0 s), 0 B-roll on lines that are not slow, 0 problems left, 0 pieces with the wrong person
  talking, picture check 268/268, subtitles clean. Time: face map 456 s (once per episode), scene edit 486 s, renders +
  transitions + subtitles ~12 min. Claude: $4.81 for the 5 minutes (face map naming $0.003).
- **App integration (2026-10-30):** the steps after `groups` for Dub Studio are specified in
  `dub-studioo/docs/06_BROLL_SCENE_EDIT.md` (order, exact commands = `run_episode.sh`, files, rules) with the done-test
  fixture `dub-studioo/examples/tgs_ep34_5min/` (the scene edit must come out identical, md5 641460ec…, 0 Claude calls).
- **B-roll used in turn, at most 3 times; final QC; 25 fps (owner, 2026-10-30).**
  - One B-roll shot is used at most 3 times in the episode (`MAX_BROLL_USES`, checked per stretch and for the whole
    episode). When a place needs B-roll again and again, its B-roll shots are used IN TURN (A, B, A, B ...), never the same
    shot twice in a row when another one of that place exists. The 30-s gap for SPEAKER shots is gone: speaker shots are
    used in turn too, never right after themselves (`SPK_GAP` 1 s), and count against the 3 uses. Reaction repeats still
    need 8 s between them.
  - Every output video is rendered at **25 fps** (`dubstitch.py` `out_fps`, config `out_fps` overrides; was the source's
    rate, TGS 30): render, verify, bookends and check use it; transitions and subtitles follow the clean master.
  - **Final QC step** `qc` (`dubstudio/pipeline/qc_final.py`, after `scene_sheet`) writes `<show>_ep<N>_qc.xlsx`, PASS /
    WARN / FAIL per case: 1 B-roll repeats (<= 3 per shot), 2 short shots (every shot on screen >= 2 s: a shorter B-roll
    FAIL, a shorter clip WARN), 3 story sync (Claude, text only, reads every shot: heard vs shown), 4 black / blank frames
    (ffmpeg blackdetect >= 0.1 s), 5 frame rate 25, 6 speaker (face map: nobody but the speaker talks on screen).
## What changed in v20 (2026-10-27/28, TGS ep31/ep32)
- **Step 4C, the scene edit (new, every show):** after `groups`, Claude cuts the picture of the whole episode like a film
  editor, from the story, stretch by stretch, with the source shots (3 frames each) and our lip scan. It replaces the
  row-by-row picture: a slow line gets B-roll (another shot of the same place) instead of slow motion, a dialogue line
  starts on the speaker's moving mouth, an action is shown when the voice tells it. Speeds stay 0.80-1.25x by
  construction (the fast-clip rule of 4A.1 still runs inside `groups`). Tools: `ai/dubstudio/pipeline/library.py`
  (shot library) and `sceneedit.py` (the edit), both in the Appendix. Owner-approved on TGS ep31 (30 s, 2 min, full)
  and ep32.
- **B-roll rules (owner, all checked by code, Step 4C.6):** only reaction / neutral / establishing shots are B-roll;
  action and talking footage is shown once; a repeated reaction is at least 8 s from the last time and zoomed 8%;
  **never a moving mouth while narration plays** (more than 0.2 s = a problem); a quote over several sentences is
  dialogue in all of them; **location guard:** a piece shows only the place of its line: a shot of the line's scene, or
  a reaction/neutral shot of the SAME location elsewhere in the episode, never another background; a shot that mixes
  places (flashback, montage) is never B-roll.
- **Parallel:** all stretches are cut at the same time (8 Claude calls at once): footage is shared out before the calls,
  the joins come from the automatic edit, then one whole-episode check sends only the stretches with a problem back.
  ep32 (13:20): scene edit 13.7 min instead of about 45 min one by one.
- **Transitions on a scene-edited episode:** only where the STORY changes scene (`scene_transitions.py`); every other
  cut is a straight cut.
- **Windows lip scan (Step 4B.6):** on Windows without an NVIDIA GPU, `lipgpu.py` runs S3FD and TalkNet on ONNX Runtime
  (DirectML on any DirectX 12 GPU, else OpenVINO, else CPU). Always use it there (owner, 2026-10-28; tested on a Windows
  PC). Same weights, FP32, same output (`compare_lips.py`). Mac stays on PyTorch + MPS, NVIDIA on PyTorch + CUDA.
- **One command per episode:** `scripts/run_episode.sh` runs every step in order, resumable, and logs the time and the
  AI cost of each step (`W/run_times.tsv`). Step 2 runs automatically with Claude (`ai_match.py`) in this script; the
  manual read of Step 2 stays the reference when the owner asks for it.
- **AI gateway:** Steps 2 (automatic), 4C and the translation fallback call Claude through the company's
  OpenAI-compatible gateway (`ai/dubstudio/ai.py`; settings in `adaptation-tool/.env`, never in a shared file). The
  gateway is internal: the VPN must be on. Every AI step checks it first and stops with a clear message when it cannot
  be reached (ask the owner to connect the VPN; never wait on timeouts).

## What changed in v19 (Oct 2026)
- **Dynamic strip (HIH only, Step 8C):** the white strip is 2 caption lines tall and grows to 3 only where the US
  footage on screen has a 3-line burned-in caption; the Italian cards fill it (2 or 3 lines), each word at its own
  time. New commands `captions` and `dynsrt`, new style `subs --style dynamic`, new tool `dynsubs.py`; `mksrt.py`
  also writes `<out>_words.json`. Other shows keep their style unless the owner says otherwise.
- **Run rule** above: every step, every episode; ask the owner when a machine is not available.
- **Same settings for every show (owner, 2026-10-20; where the show profile below differs, these win):** Whisper
  makes both SRTs (Step 0B; the script is the `.docx`, else the given SRT's text); whole sentences (`--clauses 0`);
  the lip scan runs for every show; `groups --size 7 --max-drift 500`; burned-in subtitle rows at most 7 words / 4 s
  (`mksrt.py --row-words 7 --row-seconds 4`); the source ending is kept but stops before a closing fade to black.
  Only the subtitle style differs: TDMB strip, HIH dynamic, every other show box.
- **Fast-clip rule (2026-10-22, Step 4A.1):** nothing plays faster than 1.25x. First per group (`groups --trim-fast 1.25
  --trim-to 1.2`: a group above 1.25x is cut down to 1.2x, the fast narration clips inside it first), then per clip
  (`fastfix.py`: every clip still above 1.25x is cut to 1.2x; lip clips where Claude picks the cuts). Hard check: no
  clip above 1.25x.

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
                mksrt.py  srtclean.py  dynsubs.py  export_lip_onnx.py  compare_lips.py  run_episode.sh  run_report.py
  ai/dubstudio/ ai.py  names.py  pipeline/{library, sceneedit, scene_transitions, sceneedit_sheet, ai_match, decisions,
                translate_ai}.py          (Claude steps: Step 2 automatic, Step 4C, translation fallback)
  .env          AI_BASE=<gateway URL>  AI_KEY=<your key>  AI_EMAIL=<your work email>  AI_MODEL=claude-sonnet-5-5
                (never share it, never put it in this file; the gateway is internal: VPN on)
  third_party/TalkNet-ASD/          (lip scan; Step 0 downloads it)
  data/models/                      (Whisper + MiniLM, downloaded automatically on first use)
  data/models_onnx/                 (Windows without NVIDIA: s3fd_head.onnx + talknet.onnx from export_lip_onnx.py)
  mac_lipscan/mac_runner.command    (only when the agent works in a Linux VM on a Mac, Step 4B.4)
```

| File | What it is for | Used in |
|---|---|---|
| `dubstitch.py` | every pipeline command (words, analyze, sheet, manifest, lipsync, groups, render, verify, bookends, transitions, subs, translate, check, ...) | all steps |
| `pngsubs.py` | box-style subtitles (transparent PNG stills turned into an overlay) | Steps 8-9 (`subs --style box`) |
| `dynsubs.py` | dynamic strip (HIH): reads the source captions (OCR), makes the cards that fill a 2/3-line strip, burns them | Step 8C (`captions`, `dynsrt`, `subs --style dynamic`) |
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
| `export_lip_onnx.py`, `compare_lips.py` | the lip networks on ONNX Runtime for Windows without NVIDIA, and the proof the result is the same | Step 4B.6 |
| `run_episode.sh` | one episode, every step in order, resumable, time + AI cost per step | all steps |
| `run_report.py` | the time + Claude cost table of every step, for the owner after every episode | Step 11.1 |
| `ai/dubstudio/pipeline/library.py` | shot library: camera cuts, frames, Claude labels (type, B-roll yes/no), scenes, locations | Step 4C.1 |
| `ai/dubstudio/pipeline/sceneedit.py` | the scene edit: Claude cuts the picture stretch by stretch (parallel), every piece checked | Step 4C |
| `ai/dubstudio/pipeline/scene_transitions.py`, `sceneedit_sheet.py` | transitions plan (story scene changes), review sheet | Step 4C.7-4C.8 |
| `ai/dubstudio/pipeline/ai_match.py`, `decisions.py`, `ai/dubstudio/names.py` | Step 2 done by Claude (automatic route) | Step 2 |
| `ai/dubstudio/ai.py`, `pipeline/translate_ai.py` | AI gateway client; Claude translation of lines Google left | Steps 2, 4C, 9 |

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

**HIH** (Italian dub vs US, `HIH US-IT/`): like WBM (no SRTs: Step 0B both sides; Italian script
`italy/Script/Episode <N> - <title>.docx`, audio `italy/Audio/Episode <N> - <title>.wav`, US video `US/EP_<N>.mp4` or
`US/Ep <N> - <title>.mp4`), but the US video has English captions burned in → `render --subs keep` and the
**dynamic strip** (Step 8C) for both subtitle videos. Outputs `HIH US-IT/output/ep<N>/`, `HIH_EP<N>_IT_...`,
`hih_ep<N>_it_us_mapping.xlsx`. Needs `tesseract` on the machine (`brew install tesseract`).

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

# Step 4C (v20, every show): shot library + scene edit by Claude, then the scene manifest is the edit list (Step 4C)

# Steps 5-7: render, verify, clean master (with Step 7B transitions on shows that use them)
python3 $T/dubstitch.py render --work $W --subs keep               # --subs keep|crop from the show profile
python3 $T/dubstitch.py verify --work $W                           # then LOOK at W/qc_sheet.jpg
if [ $TRANS = 1 ]; then
  python3 $T/dubstitch.py bookends --work $W --out $W/clean_noglow.mp4
  python3 ai/dubstudio/pipeline/scene_transitions.py $W $W/broll_library/broll_library.json    # v20 (Step 4C.8)
  python3 $T/dubstitch.py transitions --work $W --out "${P}_final_clean.mp4" --plan $W/transitions_plan.json
else
  python3 $T/dubstitch.py bookends --work $W --out "${P}_final_clean.mp4" $ENDING
fi

# Steps 8-10: subtitles (dub language), English translation + subtitles, final check
python3 $T/dubstitch.py subs --work $W --style $STYLE --srt "$SUB_SRT" --out "${P}_final_${dl}_subs.mp4"
python3 $T/dubstitch.py translate --work $W --srt "$SUB_SRT" --sl $DL --tl en --out "${P}_english_google.srt"
python3 $T/dubstitch.py subs --work $W --style $STYLE --srt "${P}_english_google.srt" --out "${P}_final_en_subs.mp4"
python3 $T/dubstitch.py check --work $W                            # must print FINAL CHECK PASSED
```

**v20: the whole run in one command** (every step below in order, plus Step 4C, resumable, time and AI cost per step):
```bash
SHOW=TGS N=32 LABELS=DE,US DL=de SL=en STYLE=box \
DUB_AUDIO=".../DE/Episode 32 - ....mp3" DUB_SCRIPT=".../DE/Episode_ 32 - ... cleaned.docx" SRC_VIDEO=".../US/TGS EPISODE 32.mp4" \
W=".../work_ep32" OUT=".../output/ep32" bash adaptation-tool/scripts/run_episode.sh
# optional: PY=<python of the pipeline env>  FONT=<Poppins-Bold.ttf if not installed>  PAR=8  LIP_ENGINE=auto
#           FACEPY=<python of the face environment, venv_face>  (needed for the face map step)
```
Order after the automatic edit: `groups` -> `library` (Claude labels every shot) -> `facemap` (who is on screen / who
talks; FACEPY) -> `scene_edit` (Step 4C) -> `use_scene` -> `render` -> `verify` -> `bookends` (no opening that shows a
person speaking) -> `trans_plan` -> `transitions` -> subtitles -> `scene_sheet` -> `check`. Step 4C (scene edit) comes
after `groups` and before `render`; the commands are in Step 4C. Transitions on a
scene-edited episode use `--plan W/transitions_plan.json` from `scene_transitions.py`.

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

### 4A.1 Fast-clip rule (owner, 2026-10-22): nothing plays faster than 1.25x

**Speed** = source length / Italian slot length. Above 1 the source plays fast, below 1 it plays slow (slow motion).
Owner's table: 0.8-1.25x a plain speed change is fine; above **1.25x** the clip is **trimmed** (source time cut) until
it plays at about **1.2x**; below 0.75x needs B-roll or a hand fix (editor flags). Trimming happens at two levels:

1. **Group level, in `groups --trim-fast 1.25 --trim-to 1.2`.** For every group (block of up to 7 Italian rows playing
   one continuous piece of source; lip-sync rows and lip groups are not trimmed here) the tool checks the whole
   group's speed. A group above **1.25x** is marked "needs trim": its extra source time is cut down to 1.2x. The tool
   then picks **which clips inside the group** give that time, in this order:
   1. clips that are themselves fast (their Italian line is shorter than their source clip) **and narration**,
   2. fast clips with speech, 3. slow narration clips, 4. slow speech clips.
   Inside a tier the cut is shared in proportion to how much each clip can give; every clip keeps at least its
   minimum length, no cut is smaller than 0.3 s (a smaller excess is absorbed by a tiny speed-up instead), and dub
   repeats are never cut. The review sheet records each trimmed group (speed before/after, seconds cut, where).
2. **Clip level, after `groups` (Dub Studio `fastfix.py --threshold 1.25 --target 1.2`).** Every single clip (segment)
   that still plays above **1.25x** (mostly lip-sync clips and lip-group pieces, which step 1 does not touch) is
   trimmed to about 1.2x:
   - **narration** (no talking face): cut evenly from its start and end;
   - **lip clips:** Claude gets the clip's shots (scene cuts), a frame per shot, the seconds of moving lips per shot
     and both lines, and picks the cuts (non-lip shots first, keep the talking mouth); the answer is checked (total
     cut, each cut ≥ 0.3 s, every kept piece long enough); if it does not add up, a fixed rule cuts instead (non-lip
     shots first, then before/after the lips, then the lips from both ends).
   The clip is split into its kept pieces at one speed. **Hard check:** after this no clip is above 1.25x, else the
   step fails. No transition is ever put on a trim (`fastfix_cuts.json`). Results: `W/fastfix.json`, the "Fast clips"
   tab of the groups review sheet (speed before and after per clip).

3. **Every trim is stored and marked "trimmed" for the editor (owner, 2026-10-26).** The software's cuts are never
   silent: `W/fastfix.json` stores each one (row, speed before → after, seconds cut, where, who chose the cut: the group
   rule, the narration rule, Claude or the lip rule, and Claude's reason); the groups review sheet lists them on its
   "Fast clips" tab; in the Dub Studio editor every trimmed clip carries a **yellow "trimmed" flag** ("trimmed by the
   software (fast clip over x1.25): clip x1.61 -> x1.20, 0.50 s cut (Claude chose the cut) -- check the cut") and the
   top bar shows "N trimmed" (click = next one). The editor plays it, and **approves** the clip when the cut is fine
   (= checked) or fixes it; once the editor changes the clip, the mark goes (the cut is then his). Report the number
   of trimmed rows to the owner (Step 11).

So a group is first checked as a whole, then the clips that are still too fast are fixed one by one, and every cut is
marked for the editor to check. Example, TOLR
ep24: 2 narration groups trimmed (4.7 s), then 56 clips trimmed (37 lip clips, 33 cut where Claude chose); fastest
clip 1.64x → 1.22x; time above 1.5x 9.3% → 0%.

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
| Windows PC, CPU only, PyTorch (Lunar Curse ep18) | CPU | about 5 s | 1 h 26 min (S3FD alone 46 min) → use 4B.6 |
| Apple M4, TGS ep32 (`lipgpu.py` + `lipmesh.py` on the Mac, one after the other) | GPU | 1.0 s + 0.3 s | 13.8 min |

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
| Windows, no NVIDIA GPU | **ONNX Runtime + DirectML / OpenVINO (Step 4B.6)** — automatic once `export_lip_onnx.py` has run; the plain CPU route took 1 h 26 min on Lunar Curse ep18. |
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
  sentences / 24 rows), a YuNet+S3FD hybrid (ep25 11 sentences), ONNX runtime or JIT on the same device (only ~10% faster; but on Windows without NVIDIA, ONNX Runtime on the GPU is the route: 4B.6), several
  parallel processes on 4 cores (no gain). Apple Neural Engine (Core ML) was not tested end to end; the GPU route is used.

### 4B.6 Windows: always run the lip networks on ONNX Runtime (owner, 2026-10-28)

On Windows, PyTorch can use only an NVIDIA GPU (CUDA). Most Windows PCs have Intel or AMD graphics, so the lip scan ran
on the CPU: Lunar Curse ep18 took 1 h 26 min for `lipgpu.py` (S3FD alone about 46 min). The fix, **always used on
Windows without an NVIDIA GPU** (tested on a Windows PC, 2026-10-28): S3FD and TalkNet run on **ONNX Runtime**, which
uses any DirectX 12 GPU (**DirectML**: Intel, AMD, NVIDIA) or an Intel GPU/CPU (**OpenVINO**). Everything else of the
scan (frames, every 2nd frame, half size, threshold 0.9, boxes, NMS, tracks, Face Mesh, merge, lipsync) is unchanged.
All of it is free (Microsoft / Intel), no account or key.

| Computer | What `lipgpu.py` uses (auto) |
|---|---|
| Mac (Apple silicon) | PyTorch on the Apple GPU (MPS) — unchanged |
| Windows or Linux with an NVIDIA GPU | PyTorch on CUDA — install the CUDA build of torch (pytorch.org), no ONNX |
| **Windows without NVIDIA** | **ONNX Runtime: DirectML > OpenVINO > CPU** (`--engine auto`) |
| Linux CPU only | PyTorch on the CPU (ONNX on a plain CPU is not faster: 3.5 vs 2.9 s per second of video on a 4-core VM) |

**Once per Windows PC** (PowerShell; a separate environment, because `onnxruntime`, `onnxruntime-directml` and
`onnxruntime-openvino` cannot be installed together):
```powershell
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion   # which graphics chip; nvidia-smi answers only on NVIDIA
py -3.12 -m venv C:\lipscan\venv
C:\lipscan\venv\Scripts\pip install <the pipeline packages> onnx onnxscript onnxruntime-directml   # Intel CPU/iGPU: onnxruntime-openvino
C:\lipscan\venv\Scripts\python -c "import onnxruntime as o; print(o.get_available_providers())"     # must list DmlExecutionProvider
cd adaptation-tool\scripts
C:\lipscan\venv\Scripts\python export_lip_onnx.py        # writes data\models_onnx\s3fd_head.onnx + talknet.onnx and checks them
```
`export_lip_onnx.py` exports the same trained weights in full precision (FP32; TalkNet with the newer exporter,
`dynamo=True`, because the old one freezes the frame count in the attention layers) and checks both files against
PyTorch at the sizes the scan uses; it must print `largest difference ... -> OK` (measured: 5e-6). The `.onnx` files
can be copied to other Windows PCs.

**Every episode:** nothing changes — `lipgpu.py` picks the engine itself and logs it:
`ONNX engine dml: S3FD on DmlExecutionProvider, TalkNet on DmlExecutionProvider (full precision FP32)`. If the log
shows `CPUExecutionProvider`, the GPU is not used (update the graphics driver; DirectML needs Windows 10 1903+).
Force a choice with `--engine onnx --provider dml|openvino-gpu|openvino-cpu|cpu`, or `--engine torch`.
`timing.json` records the engine and provider.

**Proof that the result is the same** (`compare_lips.py <lips_nets A> <lips_nets B>`; run it once per PC on an
episode already scanned elsewhere, e.g. the first 3 pieces):
- ONNX vs PyTorch on the same machine (TGS ep32, 0-30 s): tracks, boxes, scores and intervals identical.
- ONNX (CPU) vs the Mac's MPS scan: same tracks and the same talking intervals; boxes within 1 px; per-frame scores
  differ by up to 0.35 — exactly as much as plain PyTorch on a CPU differs from MPS, so this is normal rounding.
  `compare_lips.py` passes when tracks, boxes (≤ 2 px) and every interval edge (≤ one frame) match.
- Never use FP16 / INT8 / "performance" precision, YuNet, or fewer frames to make it faster (4B.5).

## Step 4C. Scene edit: Claude cuts the picture from the story (B-roll; every show since v20)

> **Hard rule (owner, 2026-10-28, v21): B-roll ONLY for slow lines.** Every dub line shows its **own footage** (what
> the automatic match plays during it; a neighbouring line's own footage is allowed for lip timing and story order),
> used as matched, with no exceptions. A line whose own footage would play **below 0.8x** is **slow**: it shows its own
> footage first (down to 0.8x) and gets B-roll **only for the seconds its own footage cannot fill**
> (`budget = line length − own footage / 0.8`). A **fast** line (above 1.25x) is **trimmed** (a shorter part of its own
> footage), never B-roll. A **normal** line (0.8-1.25x) never gets B-roll. B-roll = a reaction / neutral / establishing
> shot of the line's place without a moving mouth. All of it is checked by code; Claude is told each line's own
> footage, speed and B-roll seconds.

**Why (owner, TGS ep31):** the row-by-row edit fills each dub line on its own. When the source is shorter than the dub,
lines play in slow motion; and a lip clip can land at the wrong moment ("Paul… warte!": her mouth played over the silence
before the voice, Paul's "Ja?" clip played during the narration before his line). A person edits a scene as a whole:
first Paul walking to the door, then the girl calling "wait" exactly when the voice says it. Step 4C does that for the
whole episode, for any show. It runs after `groups` (the automatic edit is the map: which source footage belongs to which
line) and before `render`.

```bash
AIP=adaptation-tool/ai; PL=$AIP/dubstudio/pipeline; SW=W/scene; LIB=W/broll_library
python3 $PL/library.py W $LIB                                    # 4C.1 shot library (Claude labels every shot)
mkdir -p $SW; cp W/sentences_dub.json W/sentences_src.json W/lipsync.json $SW/
cp W/manifest_groups.json W/manifest_groups.auto.json; cp W/manifest_groups.auto.json $SW/manifest_groups.json
python3 $PL/sceneedit.py $SW $LIB 0 <dub length s> $SW/scene_manifest.json --parallel 8      # 4C.2-4C.6
#   -> $SW/scene_manifest.json (the edit list) + .edl.json (every piece with Claude's reason, every check round)
#   then W/manifest_groups.json = the scene manifest (run_episode.sh step use_scene), render, verify, bookends,
python3 $PL/scene_transitions.py W $LIB/broll_library.json         # 4C.8 transitions only at story scene changes
python3 dubstitch.py transitions --work W --out <P>_final_clean.mp4 --plan W/transitions_plan.json
python3 $PL/sceneedit_sheet.py $SW/scene_manifest.edl.json $LIB/broll_library.json <english srt> OUT/<show>_ep<N>_scene_edit.xlsx "<title>"
```
`scripts/run_episode.sh` does all of this in order (steps `library`, `scene_edit`, `use_scene`, `trans_plan`,
`scene_sheet`). The scene-edit sheet is the review sheet (`config.json` `groups_review`) that `check` looks for.

### 4C.1 The shot library (`library.py`): what every source shot is
1. **Camera cuts:** ffmpeg scene score > **0.28** on a 12.5 fps, 192-px copy (`W/scenes.json`); a piece shorter than
   **0.4 s** joins the shot before (TGS ep32: 125 shots).
2. **Frames:** 3 per shot at 20 / 50 / 80% of it, 256×256 each, side by side (`_frames/shot_###.jpg`, 768×256).
3. **Claude, cast (1 call):** 14 sample shots + the whole source narration → characters (name, look) and places.
4. **Claude, labels (6 shots per call, 6 calls at a time):** per shot: people, place, **type**
   (`reaction` a face showing a feeling / `neutral` someone just sitting, standing, looking / `talking` the mouth clearly
   speaks / `action` a story action or detail / `object` / `establishing` a wide view), action, `broll` and why.
   **B-roll = yes only for reaction, neutral and establishing shots** (owner: a story action like picking up the pastry
   is shown in its own place, never as B-roll).
5. **Claude, scenes (1 text call):** the ordered shot list → story scenes (one place, one continuous moment).
6. **Claude, locations (1 text call, v20):** every place description → a location = one background that looks the same
   (the parking garage at the start and later = one location; a car interior, another room, a street = other locations).
   A description that mixes places (flashback, montage) = `mixed`, and a mixed shot is never B-roll.
7. Our lip scan adds the seconds of moving mouth per shot. Output: `broll_library.json` (every shot: start, end, people,
   place, location, scene, type, action, broll, lips, text) + `broll_library.xlsx`. All Claude answers are cached
   (`_claude_cache.json`): a re-run only asks what is new.

### 4C.2 Where each dub line plays (`line_places.json`, v20)
Claude reads the story once (text only): the source scenes in order (their locations and source narration) and every
dub line → for each line the **scene** and the **location** it plays at. The automatic match alone is not enough for
this: where it stretched one shot over many lines it gives the wrong place (TGS ep32 lines 99-106 are still in the gym
but got a parking-garage shot). Cached in `W/scene/line_places.json` (about $0.05 per episode).

### 4C.3 Stretches and the parallel run
- **Stretches:** the dub is cut first where the story changes scene (a scene part under 8 s stays with its neighbour),
  then about every **30 s** inside a scene, always in a pause between lines (0.6-1.4 × 30 s). TGS ep32: 29 stretches.
- **Shots offered per stretch:** every shot of the scenes the automatic edit uses there and of the scenes of its lines
  (4C.2), later shots only if B-roll (start ≤ last used source time + 40 s, else broll = yes), no `mixed` shot unless the
  automatic edit put it there, plus up to 12 reaction/neutral/establishing shots of the **same locations** from other
  scenes (nearest first). Each shot goes as text (times, people, type, action, mouth-moving times, the source narration
  over it, where the automatic edit used it, its location and scene) and as its 3-frame image.
- **Footage shared out before the calls (ownership):** for talking / action / object shots, the seconds the automatic
  edit gave to a stretch belong to that stretch; free seconds of a shot go to the nearest owner. Reaction, neutral and
  establishing shots are shared by all stretches. Each stretch is told what it may use ("footage for THIS stretch: a-b";
  "ALL its footage belongs to other stretches: do NOT use it").
- **Joins:** each call gets the automatic edit's pieces just before and just after its stretch (a rough guide), so no
  call waits for another.
- **All stretches at once** (`--parallel 8` = 8 Claude calls at a time). Each answer is checked (4C.6); a stretch with
  problems gets its answer and the problem list back, up to 3 rounds.
- **Whole-episode check** after all answers: action/talking footage shown twice, a reaction repeated less than 8 s
  apart across a join, a jump cut at a join (same shot, non-continuous), plus any stretch still failing its own check.
  Only those stretches go back to Claude, in parallel, now with the real neighbouring pieces and the footage the
  other stretches show (up to 2 such rounds, 3 rounds each). Repeats of shared shots are marked by the code (zoom 8%).
- Without `--parallel` the stretches run one by one, each seeing the real pieces before it (the v19 way; about 3× slower).

### 4C.4 What Claude is told (the rules of every call)
```text
You are the film editor of a dubbed episode. The dub's voice track is fixed. Cut the PICTURE for dub time {T0:.2f}-{T1:.2f} s
using only the source shots below (frames attached). Think about the story like an editor: what must the viewer see at each
moment so picture and voice tell the same story?

Characters: {'; '.join(f"{c['name']}: {c['look']}" for c in cast)}
(The dub may use other names for the same people; match them by the story.)

Rules:
1. The pieces are back to back and cover {T0:.2f}-{T1:.2f} s exactly (first t0 = {T0:.2f}, each t0 = the previous t1, last t1 = {T1:.2f}).
2. A piece plays one shot from "from" to "to" (source seconds, inside that shot). speed = (to - from) / (t1 - t0) must be
   between {SPEED_LO} and {SPEED_HI} (1.0 is best). A piece lasts at least {MIN_PIECE} s.
3. DIALOGUE line (in quotes): show the SPEAKER with a moving mouth, timed so the mouth starts moving when the voice starts:
   the source time where the mouth starts moving must land on the line's start (within {LIP_TOL} s). You may cut to the
   listener after the first words of a long line.
4. NARRATION line: show what it tells, and keep the person it is about on screen ("Paul looked down at himself" shows Paul).
   A B-roll piece over narration NEVER shows a moving mouth (see "mouth moving at" of every shot, reaction shots too):
   use only seconds where nobody's mouth moves. The line's own footage stays as matched.
   An action (walking to the door, turning round, picking something up) is shown when the voice tells it, not earlier, once.
5. Before a dialogue line, show what leads to it (e.g. the person being called walking away, then the caller says "wait").
6. Never show an event before the voice has told it. Action and talking footage is shown ONCE in the whole episode (see the
   footage already used below). Only reaction / neutral shots (a face listening, looking) may be shown again, at least
   {REUSE_GAP:.0f} s after the last time, with "reuse": true. Every B-roll shot is used AT MOST {MAX_BROLL_USES} times in the episode;
   when a place needs B-roll again and again, use that place's B-roll shots IN TURN (A, B, A, B ...), never the same shot
   twice in a row when another one of that place is there (it looks repeated).
7. Prefer longer pieces and few cuts; follow the story order of the footage where you can; continue smoothly from the
   pieces just before this stretch.
8. LOCATION: every dub line is marked with the location (background) where it plays. A B-roll piece shows ONLY that place: a
   shot of the line's scene (not a flashback shot), or a reaction / neutral shot of the SAME location from elsewhere in the
   episode. Never a shot with another background (another room, another scene's place, a flashback).
9. B-ROLL ONLY FOR SLOW LINES (hard rule): every line is marked with its OWN footage (what the automatic match plays during
   it) and its speed. Show each line with its own footage (or its neighbours' own footage, e.g. for lip timing or the
   story order). Lines marked GROUP share their own footage: place it in STORY ORDER inside the group -- an action
   (e.g. shoving the phone in his face) goes on the line that TELLS it, never on an earlier line of the group; the other
   lines of the group show its reaction / neutral footage. Only a SLOW line (own footage below 0.8x) may add B-roll, and only for the seconds given; use its own
   footage first (down to 0.8x). A FAST line (above 1.25x) shows a shorter part of its own footage (trim) -- never B-roll.
   A normal line (0.8-1.25x) never gets B-roll. B-roll = a reaction / neutral / establishing shot, never action or talking;
   only on a NOT FOUND (gap) line may it also be the action / object shot that shows exactly what that line tells.
   Every B-roll piece lasts at least {MIN_BROLL:.0f} s (shorter cutaways look jumpy). A B-roll you plan shorter is lengthened
   to {MIN_BROLL:.0f} s by the software, taking the time from the clip before it (that clip then plays faster). A slow line
   that needs less than {MIN_BROLL:.0f} s of B-roll takes either one {MIN_BROLL:.0f}-s B-roll or none (its own footage slower, down to 0.65x).
10. SPEAKER: a moving mouth during a dialogue line must be the mouth of the person who SAYS it (every dialogue line says
   who; every talking shot says who talks). Never show another person talking over it. A WRONG-SPEAKER line (its matched
   footage shows the wrong person talking) is shown with its speaker talking -- a SPEAKER shot of the same location, the
   mouth starting with the voice; it may repeat footage shown elsewhere in the episode -- or a face that does not talk.

Answer ONLY JSON: {{"edl": [{{"t0": s, "t1": s, "shot": n, "from": s, "to": s, "line": dub line number or null, "reuse": false,
"why": "what the viewer sees and why here (at most 15 words)"}}], "notes": "anything that could not be done (at most 40 words)"}}
Keep the answer short: no text outside the JSON.
```

With `--parallel` each call also gets rule 9: *"The other stretches of the episode are cut AT THE SAME TIME as this one.
For talking / action / object shots use ONLY the footage marked 'for THIS stretch' (the rest is shown by other
stretches). SHARED shots (reaction / neutral / establishing) may be used by any stretch; the software marks repeats."*
A correction round adds: *"If a problem cannot be solved with this footage, keep the best possible cut, say why in
notes and answer — do not keep searching."* (without it Claude sometimes searched until its answer was cut off).

### 4C.5 Every case and what happens

| Case | What the edit does | Checked by code |
|---|---|---|
| Dialogue line (in quotes, or inside a quote opened by an earlier sentence) | the speaker on screen with a moving mouth that starts when the voice starts; may cut to the listener after the first words | mouth start within 0.3 s of the line start (lip scan) |
| Narration line | what the line tells (its own footage), the person it is about kept on screen | — |
| B-roll over narration | **never a moving mouth** (also not from a "reaction" shot whose mouth moves); the line's own footage is used as matched | > 0.2 s of moving mouth under narration in a B-roll piece = problem |
| B-roll shot type | reaction / neutral / establishing only, no talking mouth (> 0.3 s); on a NOT FOUND line also the action / object shot of exactly what it tells | otherwise = problem |
| **Slow line** (own footage below 0.8x) | own footage first, down to 0.8x; B-roll (reaction / neutral / establishing of the same place) only for the rest | B-roll seconds ≤ budget + 0.75 s |
| **Normal line** (0.8-1.25x) | its own footage only (or a neighbour's, for lip timing / story order) | a B-roll piece on a line that is not slow = problem |
| **Fast line** (above 1.25x) | trimmed: a shorter part of its own footage; never B-roll | speed ≤ 1.25x; no B-roll |
| Line with no source line (gap, e.g. an added event) | NO own footage (the automatic edit's stretched neighbour does not count), never another line's footage. A **gap plan** (one text-only Claude call per episode, `W/gap_plan.json`, story + the B-roll book of the line's place) says per line WHO is on screen and HOW: `talking` (said out loud by a character on screen -> that character's lip clip, a SPEAKER shot, e.g. ep34 Paul's "Anya, bitte!"), `reaction` (whose face), `action` (the shot of exactly the event, e.g. the spy in the car) or `neutral`, with suggested shots | the planned person on screen for at least half the line; a `talking` gap line starts on that person's moving mouth (lip check) and no one else talks over it |
| **Wrong speaker** (dialogue line whose own footage shows ANOTHER person talking, e.g. TGS ep34 0:01: Paul's "Anya, bitte! Das ist nicht das, wonach es aussieht!" was matched to Ava's "If it's not what it looks like…") | that footage is not its own any more: the line shows its speaker talking (a SPEAKER shot of the same location, mouth starting with the voice; talking footage shown elsewhere may be repeated for it, at least 30 s away, zoomed 8%) or a face that does not talk | any piece showing a moving mouth of someone other than the line's speaker = problem |
| **Group of lines** (Step 2 joined rows that tell the same source lines, e.g. ep34 rows 3-6 = S1-S4) | the rows SHARE the group's own footage and Claude places it in story order: an action on the row that tells it ("she waved the phone in his face" gets the phone thrust, not the row before it); the other rows show the group's reactions; speed and B-roll allowance count for the whole group; a stretch never cuts through a group | B-roll seconds ≤ group allowance + 0.75 s |
| Before a dialogue line | what leads to it (the person called walking away, then "wait!") | — |
| Footage already shown | action / talking footage only once in the episode; footage with > 0.3 s of moving mouth counts as talking | overlap > 0.2 s with an earlier piece = problem |
| Reaction / neutral repeat | allowed, at least 8 s from the last time, zoomed 8% (`zoom 1.08`); one B-roll shot at most 3 times in the episode; a place's B-roll shots are used IN TURN (A, B, A, B), never the same shot twice in a row | repeat < 8 s apart, a 4th use, or the same B-roll shot twice in a row while another of that place exists = problem |
| B-roll from elsewhere | only from the line's scene, or a reaction/neutral/establishing shot of the SAME location in another scene | B-roll piece location ≠ line location and not the line's scene = problem |
| Flashback / montage shot (`mixed`) | never as B-roll; only where the automatic edit put it for that line (±1 line) | as above |
| Very short pieces | at least 0.5 s on screen; a B-roll piece at least 2 s (owner, was 1.3 s). A shorter B-roll is LENGTHENED to 2 s by code (`fix_short_broll`): the missing time comes from the clip before it (else after it), which keeps its footage and plays faster; above 1.25x it shows a shorter part (start kept). Example: clip 1-3 s + B-roll 3-4.3 s -> clip 1-2.3 s + B-roll 2.3-4.3 s | < 0.5 s, or B-roll < 2 s left after the fix = problem |
| Slow line that needs < 2 s of B-roll | one 2-s B-roll (own clip faster, up to 1.25x) or no B-roll (own clip slower, down to 0.65x) | own clip of a slow line may go to 0.65x |
| Join between two stretches | continue smoothly; never restart the same shot at another moment | jump cut at a join = problem |
| No good footage left | best possible cut, reason in the notes; a leftover problem is listed in the sheet | final counts |
| Gateway down (VPN off) | `sceneedit.py` stops at once ("connect the VPN and run again") | key/info check at start |
| Empty / cut-off reply | asked again (3 tries); a failed reply is never cached; after 3 failures the step stops | — |

### 4C.6 The check (main logic, exactly as in `sceneedit.py`)
Thresholds (do not change them without the owner):

| Setting | Value | Meaning |
|---|---|---|
| `SPEED_LO`, `SPEED_HI` | 0.8, 1.25 | speed = source seconds / dub seconds of a piece (1.0 best) |
| `MIN_PIECE` | 0.5 s | shortest piece on screen |
| `LIP_TOL` | 0.3 s | a dialogue line's mouth starts within this of the voice |
| `REUSE_GAP` | 8 s | shortest time between two showings of the same reaction footage |
| `SLOW` | 0.8 | a line whose own footage would play below this is slow (may get B-roll) |
| B-roll budget | line length − own footage / 0.8 (+ 0.75 s tolerance) | the most B-roll a slow line may show |
| `OWN_FRAC`, `OWN_PAD` | 0.5, 0.5 s | a piece is B-roll when less than half of its footage lies in the own footage (± 0.5 s) of its lines and their neighbours |
| `MIN_BROLL`, `SLOW_FLOOR` | 2.0 s (was 1.3), 0.65 | shortest B-roll piece; slowest own clip of a slow line that takes no / less B-roll |
| `SPK_GAP` | 30 s | a SPEAKER shot (wrong-speaker line) may repeat talking footage shown elsewhere only this far away |
| speakers | `W/speakers.json` | one text-only Claude call: who says every dub dialogue line (source names; "narration" for a thought) and who talks in every shot with a moving mouth |
| `NARR_MOUTH` | 0.2 s | most moving mouth allowed in a B-roll piece under a narration line |
| talking footage | > 0.3 s of moving mouth in the piece | shown once, like action footage |
| repeat zoom | 1.08 | a repeated reaction is zoomed in 8% (bottom-anchored, so the caption strip still covers the source caption) |
| stretch | about 30 s, cut at pauses; at story scene changes first | one Claude call each |
| `--parallel` | 8 | calls at a time |
| rounds | 3 per stretch, then up to 2 whole-episode rounds of 3 | correction rounds |
| reply | `max_tokens` 32000; "why" ≤ 15 words, notes ≤ 40 words | keeps answers short (cost) |

Dialogue across sentences (a quote over several sentences is dialogue in all of them):
```python
# dialogue = a line with a quote mark OR inside a quote that an earlier sentence opened (owner 2026-10-28: "Sei doch nicht
# so paranoid! | Das Fitnessstudio gehoert ... | Niemand wird uns ... | Komm schon ...“" is ONE quote over 4 sentences).
# German opens with „ and closes with “; other languages open with “ and close with ”; «»; a straight " toggles.
_de = any("„" in d["text"] for d in dub)
_OPEN, _CLOSE = ("„«", "“”»") if _de else ("“«", "”»")
_inside, _run = False, 0
for d in dub:
    has = bool(QUOTE.search(d["text"]))
    d["dialogue"] = has or _inside
    _run = 0 if has else _run + 1
    if _inside and _run > 4: _inside = False; d["dialogue"] = has      # a quote never closed: stop after 4 sentences
    for ch in d["text"]:
        if ch in _OPEN: _inside = True
        elif ch in _CLOSE: _inside = False
        elif ch == '"': _inside = not _inside
# LOCATION GUARD (owner 2026-10-28): a piece shows only the background of its line -- a shot of the same scene, or a
# reaction/neutral shot of the SAME location elsewhere in the episode; never another background. The library gives every
# shot a location (place descriptions grouped by Claude; "mixed" = flashback/montage); a line's location = the location of
# the footage the automatic match gives it.
HAS_LOC = any("location" in r for r in LIB["shots"])


def line_auto(d):
    return [(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000) for s in auto
            if not s.get("video") and ov(d["start_ms"], d["end_ms"], s["it_start_ms"], s["it_end_ms"]) > 0]


def line_loc(d):
    tot = {}
    for a, b in d["_auto"]:
        for r in LIB["shots"]:
            o = ov(a, b, r["start"], r["end"])
            if o > 0 and r.get("location") and r["location"] != "mixed": tot[r["location"]] = tot.get(r["location"], 0) + o
    return max(tot, key=tot.get) if tot else None


def line_places():
    """Claude reads the story once (text only): for every dub line the source scene it plays in and the location there.
    The automatic match is not enough for this: where it stretched one shot over several lines (TGS ep32 lines 99-106,
    still in the gym, got a parking-garage shot) it would give the wrong place. Cached in W/line_places.json."""
    cp = W / "line_places.json"; key = f"{len(dub)}|{len(LIB['shots'])}|{LIB['shots'][-1].get('scene')}|{dub[-1]['text'][:30]}"
    if cp.exists():
        c = json.load(open(cp))
        if c.get("key") == key: return c["lines"]
    scenes = []
    for r in LIB["shots"]:
        if not scenes or scenes[-1]["name"] != r["scene"]: scenes.append(dict(name=r["scene"], start=r["start"], end=r["end"], locs=[]))
        scenes[-1]["end"] = r["end"]
        if r.get("location") and r["location"] not in scenes[-1]["locs"]: scenes[-1]["locs"].append(r["location"])
    stxt = "\n".join(f"scene {i}: {sc['name']} | locations: {', '.join(sc['locs'])} | source narration: \""
                     + " ".join(x["text"] for x in src if sc["start"] <= x["start_ms"] / 1000 < sc["end"])[:900] + "\"" for i, sc in enumerate(scenes))
    q = ("A dubbed episode tells the same story as the source video, in the same order (lines may be reworded, added or cut). "
         "For EVERY dub line, say in which source scene it plays and at which of that scene's locations (the background on "
         "screen at that moment of the story). A line told as a memory / flashback gets the location \"mixed\". Answer only "
         "JSON: {\"lines\": [[dub line number, scene number, \"location\"], ...]} with one entry per dub line, in order.\n\n"
         "SOURCE SCENES, in order:\n" + stxt + "\n\nDUB LINES:\n" + "\n".join(f"{d['idx']}: {d['text']}" for d in dub))
    for attempt in range(3):
        try:
            text, _ = ai.chat(q, model=MODEL or None, max_tokens=16000)
            res = ai.extract_json(text, "lines")["lines"]
            if len(res) >= 0.9 * len(dub): break
        except Exception as ex:
            print("line places: reply not readable, asking again", str(ex)[:80], flush=True); res = []
    out = {}
    for row in res:
        try:
            a, b, c = int(row[0]), int(row[1]), str(row[2])
        except Exception:
            continue
        if not 0 <= b < len(scenes): continue
        sc = scenes[b]
        if c not in sc["locs"] and c != "mixed": c = sc["locs"][0] if sc["locs"] else c
        out[str(a)] = dict(scene=sc["name"], loc=c)
    json.dump(dict(key=key, lines=out), open(cp, "w"), indent=1, ensure_ascii=False)
    return out


PLACES = line_places() if HAS_LOC else {}
for d in dub:
    d["_auto"] = line_auto(d); pl = PLACES.get(str(d["idx"]))
    d["_near"] = [iv for x in dub if abs(x["idx"] - d["idx"]) <= 1 for iv in line_auto(x)]
    d["scene"], d["loc"] = (pl["scene"], pl["loc"]) if pl else (None, line_loc(d) if HAS_LOC else None)
```
Own footage, slow lines and the B-roll budget (the hard rule):
```python
# B-ROLL ONLY FOR SLOW LINES (owner, 2026-10-28, hard rule): every line shows its OWN footage (what the automatic match
# plays during it; the neighbouring lines' footage is allowed too, for lip timing and story order). Only a line whose own
# footage would play slower than 0.8x (SLOW) may add B-roll, and only for the time its own footage cannot fill at 0.8x.
# A fast line (above 1.25x) is TRIMMED: a shorter part of its own footage, never B-roll. Normal lines: own footage only.
# The own footage is used as matched, with no exceptions (owner, 2026-10-28: the talking mouths over narration came from
# B-roll shots). The B-roll quality rules (reaction / neutral / establishing shot only, no moving mouth under narration,
# the line's place, repeats 8 s apart) apply to the B-roll pieces.
SLOW, OWN_FRAC, OWN_PAD = 0.8, 0.5, 0.5
# owner, 2026-10-29: a B-roll of a few hundred ms or 1 s looks jumpy. Every B-roll piece lasts at least MIN_BROLL; a slow
# line that needs less either takes one B-roll of MIN_BROLL (its own footage then plays faster, up to 1.25x) or no B-roll
# and its own footage a little slower, down to SLOW_FLOOR.
# owner, 2026-10-29 (later): at least 2 s (was 1.3). A B-roll that comes out shorter is LENGTHENED to 2 s by code
# (fix_short_broll): the missing time (2 s - its length) is taken from the clip just before it, which keeps its footage
# and plays faster (example: clip 1-3 s + B-roll 3-4.3 s -> clip 1-2.3 s + B-roll 2.3-4.3 s). Faster than SPEED_HI that
# clip shows a shorter part of its footage (its start is kept: a mouth or an action begins there), like a fast line.
MIN_BROLL, SLOW_FLOOR = 2.0, 0.65


def merge_iv(iv):
    out = []
    for a, b in sorted(tuple(x) for x in iv):
        if out and a <= out[-1][1]: out[-1][1] = max(out[-1][1], b)
        else: out.append([a, b])
    return out


def own_line(d):
    """(source intervals, seconds) the automatic match plays during this line = its own footage"""
    iv, sec = [], 0.0
    for s in auto:
        if s.get("video"): continue
        o0, o1 = max(d["start_ms"], s["it_start_ms"]), min(d["end_ms"], s["it_end_ms"])
        if o1 - o0 <= 0: continue
        k = (s["us_end_ms"] - s["us_start_ms"]) / max(s["it_end_ms"] - s["it_start_ms"], 1)
        a = (s["us_start_ms"] + (o0 - s["it_start_ms"]) * k) / 1000; b = (s["us_start_ms"] + (o1 - s["it_start_ms"]) * k) / 1000
        iv.append((a, b)); sec += b - a
    return merge_iv(iv), sec


def load_gaps():
    """dub rows Step 2 left without a source line (decisions.json "gap"): they have NO own footage -- the automatic edit
    only stretched a neighbour over them -- so they are covered with B-roll only (owner, 2026-10-29: an added event like
    "she smashed a coffee cup against the wall" must never show another line's footage, e.g. Ava saying "liar")."""
    for p_ in (W, W.parent, W.parent.parent):
        f = p_ / "decisions.json"
        if f.exists():
            try: return {int(k) for k in json.load(open(f)).get("gap", {})}
            except Exception: return set()
    return set()


GAPS = load_gaps()
for d in dub:
    d["_own"], _os = own_line(d); _dur = max((d["end_ms"] - d["start_ms"]) / 1000, 1e-6)
    d["gap"] = d["idx"] in GAPS
    if d["gap"]: d["_own"], _os, d["_near"] = [], 0.0, []
    d["own_speed"] = round(_os / _dur, 2); d["slow"] = d["own_speed"] < SLOW
    d["broll_budget"] = round(max(0.0, _dur - _os / SLOW), 2) if d["slow"] else 0.0

# SPEAKER (owner, 2026-10-29): ep34 0:01 -- Paul says "Anya, bitte! Das ist nicht das, wonach es aussieht!" but the matched
# footage showed Anya talking (the source line "If it's not what it looks like..." is hers), although Paul's talking
# footage of the same office exists. A moving mouth during a dialogue line must be its SPEAKER's mouth. Claude names once
# (text only, cached in W/speakers.json) who says every dub dialogue line and who talks in every source shot with a moving
# mouth. A dialogue line whose own footage shows someone else talking is a WRONG-SPEAKER line ("swap"): that footage is no
# longer its own; it is shown with a talking shot of its speaker at the same location (a SPEAKER shot -- talking footage
# shown elsewhere in the episode may be repeated for it, at least SPK_GAP s away, zoomed) or a face that does not talk.
# owner 2026-10-30: no 30-s gap for SPEAKER shots -- a place's speaker shots are used IN TURN (A, B, A, B), never the same
# footage right after itself (SPK_GAP), and like every B-roll shot at most MAX_BROLL_USES times in the episode.
SPK_GAP = 1.0
NAMES = [c["name"] for c in cast]


def speakers():
    cp = W / "speakers.json"
    talk = [r for r in LIB["shots"] if r["type"] == "talking" or mouth_in(r["start"], r["end"]) > 0.3]
    key = f"v2|{len(dub)}|{len(talk)}|{len(LIB['shots'])}|{dub[-1]['text'][:30]}"
    if cp.exists():
        c = json.load(open(cp))
        if c.get("key") == key: return c

    def said(r):
        return " ".join(x["text"] for x in src if ov(x["start_ms"] / 1000, x["end_ms"] / 1000, r["start"], r["end"]) > 0.1)[:220]
    q = ("A dubbed episode (another language; the dub may use other names for the same people) tells the story of a source "
         f"video. Characters (source names): {', '.join(NAMES)}.\n"
         "1. For EVERY dub line marked DIALOGUE say who SAYS it out loud: a source name from the list, \"other\" for someone not "
         "in the list, \"narration\" if it is really the narrator (or a thought, not spoken), \"unclear\" if the story does not tell.\n"
         "2. For EVERY source shot below say whose mouth TALKS in it (a list of source names, [] if you cannot tell), from the "
         "shot's description and the words heard over it.\n"
         "Answer only JSON: {\"lines\": [[dub line number, \"name\"], ...], \"shots\": [[shot number, [\"name\", ...]], ...]}\n\n"
         "DUB LINES:\n" + "\n".join(f"{d['idx']}: {'DIALOGUE' if d['dialogue'] else 'narration'}: {d['text']}" for d in dub) +
         "\n\nSOURCE SHOTS:\n" + "\n".join(f"shot {r['n']}: {', '.join(r['people']) or 'nobody'} | {r['type']} | {r['action']} | "
                                          f"words heard: \"{said(r)}\"" for r in talk))
    res = {}
    for attempt in range(3):
        try:
            text, _ = ai.chat(q, model=MODEL or None, max_tokens=16000)
            res = ai.extract_json(text, "lines") or {}
            if res.get("lines"): break
        except Exception as ex:
            print("speakers: reply not readable, asking again", str(ex)[:80], flush=True)
    low = {n.lower(): n for n in NAMES}
    lines, shots = {}, {}
    for row in res.get("lines") or []:
        try: lines[str(int(row[0]))] = low.get(str(row[1]).strip().lower(), str(row[1]))
        except Exception: pass
    for row in res.get("shots") or []:
        try:
            v = row[1] if isinstance(row[1], list) else [row[1]]
            shots[str(int(row[0]))] = [low[str(x).strip().lower()] for x in v if str(x).strip().lower() in low]
        except Exception: pass
    out = dict(key=key, lines=lines, shots=shots)
    if lines: json.dump(out, open(cp, "w"), indent=1, ensure_ascii=False)
    return out


def minus_iv(iv, cut):
    out = []
    for a, b in iv:
        segs = [(a, b)]
        for c0, c1 in cut:
            segs = [q for x, y in segs for q in ((x, min(y, c0)), (max(x, c1), y)) if q[1] - q[0] > 0.05]
        out += segs
    return out


SPK = speakers() if NAMES else dict(lines={}, shots={})
SHOT_SPK = {int(k): v for k, v in SPK.get("shots", {}).items()}
# FACE MAP (owner, 2026-10-29; facemap.py -> <library>/face_map.json): who is on screen, where, face / back, and WHO
# TALKS WHEN from the picture (face recognition + the lip scan's per-face talking scores). When it exists it decides who
# talks in a shot; the text-only guess above is the fallback.
FMP = LIBD / "face_map.json"; FACE = json.load(open(FMP))["shots"] if FMP.exists() else {}
def talkers_at(n, a=-1e9, b=1e9):
    """names talking in shot n during source a-b (face map), else the text guess for the whole shot"""
    fm = FACE.get(str(int(n)))
    if fm is None: return SHOT_SPK.get(int(n), [])
    return sorted({x["name"] for x in fm["talking"] if x["name"] in NAMES and ov(x["from_s"], x["to_s"], a, b) > 0.1})
talkers = lambda n: talkers_at(n)
def on_screen(n):
    fm = FACE.get(str(int(n)))
    if not fm: return ""
    pp = ", ".join(f"{x['name']} ({x['pos']}, {x['view']})" for x in fm["persons"])
    tk = ", ".join(f"{x['name']} {x['from_s']:.1f}-{x['to_s']:.1f}" for x in fm["talking"] if x["name"] != "unknown face")
    return f" | ON SCREEN (face map): {pp or 'nobody'}" + (f"; talking: {tk}" if tk else "; nobody talks")
WRONG = []
for d in dub:
    sp_ = SPK["lines"].get(str(d["idx"])) if d["dialogue"] else None
    d["speaker"] = sp_ if sp_ in NAMES else None
    d["swap"], d["_wrong"] = False, []
    if not d["speaker"] or d["gap"]: continue
    for a, b in d["_own"]:
        for r in LIB["shots"]:
            o0, o1 = max(a, r["start"]), min(b, r["end"])
            tk = talkers_at(r["n"], o0, o1) if FACE else talkers(r["n"])
            if o1 - o0 > 0.05 and mouth_in(o0, o1) > min(0.3, 0.5 * (o1 - o0)) and tk and d["speaker"] not in tk:
                WRONG.append((o0, o1)); d["swap"] = True; d["_wrong"].append((r["n"], round(o0, 2), round(o1, 2), tk))
for d in dub:
    d["_near"] = minus_iv(d["_near"], WRONG)
    if d["swap"]:
        d["_own"] = merge_iv(minus_iv(d["_own"], WRONG)); _os = sum(b - a for a, b in d["_own"])
        _dur = max((d["end_ms"] - d["start_ms"]) / 1000, 1e-6)
        d["own_speed"] = round(_os / _dur, 2); d["slow"] = d["own_speed"] < SLOW
        d["broll_budget"] = round(max(0.0, _dur - _os / SLOW), 2) if d["slow"] else 0.0
print(f"speakers: {sum(1 for d in dub if d['speaker'])} dialogue lines with a speaker; wrong-speaker lines: "
      + (", ".join(f"{d['idx']} ({d['speaker']}; footage shows {'/'.join(d['_wrong'][0][3])})" for d in dub if d["swap"]) or "none"), flush=True)


# GAP PLAN (owner, 2026-10-29): a NOT FOUND line is not "B-roll only, never a mouth". Claude reads the story and the
# B-roll book (every shot of the line's place: who, type, who talks, what happens) and decides per line WHO must be on
# screen and HOW: "talking" (the line is said out loud by a character on screen -> that character's lip clip, e.g. ep34
# 0:00 Paul's "Anya, bitte!"), "reaction" (a face: whose), "action" (the shot of exactly the event the line tells) or
# "neutral" (the place / a person standing). Cached in W/gap_plan.json; the scene edit follows it and code checks that
# the planned person is on screen and that a talking line starts on that person's moving mouth.
def gap_plan():
    gl = [d for d in dub if d["gap"]]
    if not gl: return {}
    cp = W / "gap_plan.json"; key = f"v1|{len(dub)}|{len(LIB['shots'])}|" + ",".join(str(d["idx"]) for d in gl)
    if cp.exists():
        c = json.load(open(cp))
        if c.get("key") == key: return c["lines"]
    locs = {d.get("loc") for d in gl}; scs = {d.get("scene") for d in gl}
    shots = [r for r in LIB["shots"] if r.get("location") in locs or r["scene"] in scs]

    def shot_line(r):
        m = mouth_in(r["start"], r["end"])
        return (f"shot {r['n']}: {r['start']:.1f}-{r['end']:.1f} s | {', '.join(r['people']) or 'nobody'} | {r['type']}"
                f"{' (talking: ' + ', '.join(talkers(r['n'])) + ')' if talkers(r['n']) else ''} | mouth moving {m:.1f} s | {r['action']} | "
                f"place: {r.get('location')} | scene: {r['scene']}")

    def ctx(d):
        i = dub.index(d)
        return " / ".join(f"[{x['idx']}] {x['text']}" for x in dub[max(0, i - 3):i + 4])
    q = (f"A dubbed episode retells the story of a source video. Characters (source names; the dub may call them differently): "
         f"{'; '.join(c['name'] + ': ' + c['look'][:120] for c in cast)}.\n"
         "The dub lines below have NO matching source line (the adaptation added them), so the picture must be chosen from the "
         "story. For EACH line decide like a film editor what the viewer must see while it is heard:\n"
         "- show \"talking\": the line is spoken out loud by a character who is in the scene -> that character's lip clip "
         "(a shot where THAT character talks, at the same place). Never another character talking.\n"
         "- show \"reaction\": a face listening / reacting (say whose: the person the line is about, or the listener).\n"
         "- show \"action\": the shot of exactly the event the line tells (an object, a movement, e.g. 'the spy in the car').\n"
         "- show \"neutral\": the place or a person standing, when nothing more exact fits.\n"
         "\"who\" = the source name of the person on screen (\"\" for none). \"shots\" = the best shots from the list, best "
         "first (at most 4, only shots at the line's place).\n"
         "Answer only JSON: {\"lines\": [{\"line\": n, \"show\": \"talking|reaction|action|neutral\", \"who\": \"name\", "
         "\"shots\": [n, ...], \"why\": \"at most 15 words\"}]}\n\nLINES WITHOUT A SOURCE LINE:\n" +
         "\n".join(f"line {d['idx']} ({'DIALOGUE' + (' said by ' + d['speaker'] if d.get('speaker') else '') if d['dialogue'] else 'narration'}; "
                   f"place: {d.get('loc')}; scene: {d.get('scene')}): \"{d['text']}\"\n   story around it: {ctx(d)}" for d in gl) +
         "\n\nSHOT LIST (the B-roll book for these places):\n" + "\n".join(shot_line(r) for r in shots))
    res = []
    for attempt in range(3):
        try:
            text, _ = ai.chat(q, model=MODEL or None, max_tokens=8000)
            res = (ai.extract_json(text, "lines") or {}).get("lines") or []
            if res: break
        except Exception as ex:
            print("gap plan: reply not readable, asking again", str(ex)[:80], flush=True)
    low = {n.lower(): n for n in NAMES}; out = {}
    for x in res:
        try:
            k = str(int(x["line"]))
            out[k] = dict(show=str(x.get("show", "neutral")).lower(), who=low.get(str(x.get("who", "")).strip().lower(), ""),
                          shots=[int(n) for n in (x.get("shots") or []) if int(n) in SH][:4], why=str(x.get("why", ""))[:120])
        except Exception:
            pass
    if out: json.dump(dict(key=key, lines=out), open(cp, "w"), indent=1, ensure_ascii=False)
    return out


GPLAN = gap_plan()
for d in dub:
    d["spk_ok"] = d["swap"]
    gp_ = GPLAN.get(str(d["idx"])) if d["gap"] else None
    if not gp_: continue
    d["gplan"] = gp_
    if gp_["show"] == "talking" and gp_["who"] and d["dialogue"]:
        d["speaker"] = gp_["who"]; d["spk_ok"] = True          # its speaker's lip clip (a SPEAKER shot)
print("gap plan: " + ("; ".join(f"{k}: {v['show']} {v['who']} {v['shots']}" for k, v in GPLAN.items()) or "no not-matched lines"), flush=True)


# STORY ORDER INSIDE A GROUP (owner, 2026-10-29, TGS ep34 0:05-0:28): Step 2 joins dub rows that tell the same source
# lines into one group (rows 3-6 = source S1-S4) and the automatic edit spreads that footage EVENLY over the rows, so
# "Ava shoved the photo in his face" (S1, the phone thrust) landed on row 3 ("over the past weeks she had belittled him")
# instead of row 5 ("she waved the phone wildly in front of his face") / row 6 ("but he recognized it at once"). In the
# scene edit the rows of a group SHARE the group's own footage: Claude places it in story order (each action on the row
# that tells it); speed and B-roll allowance are counted for the whole group; a stretch never cuts through a group.
def load_groups():
    for p_ in (W, W.parent, W.parent.parent):
        f = p_ / "decisions.json"
        if f.exists():
            try: return [[int(x) for x in g.get("members") or []] for g in json.load(open(f)).get("groups") or []]
            except Exception: return []
    return []


BYIDX = {d["idx"]: d for d in dub}
for _mem in load_groups():
    _ms = [BYIDX[i] for i in _mem if i in BYIDX and not BYIDX[i]["gap"]]
    if len(_ms) < 2: continue
    _pool = merge_iv([iv for d in _ms for iv in d["_own"]])
    _os = sum(b - a for a, b in _pool); _dur = sum((d["end_ms"] - d["start_ms"]) / 1000 for d in _ms)
    _sp = round(_os / max(_dur, 1e-6), 2); _slow = _sp < SLOW
    _bud = round(max(0.0, _dur - _os / SLOW), 2) if _slow else 0.0
    for d in _ms:
        d["_grp"], d["_gown"] = [x["idx"] for x in _ms], _pool
        d["_near"] = merge_iv(d["_near"] + _pool)
        d["own_speed"], d["slow"], d["broll_budget"] = _sp, _slow, _bud
same_grp = lambda a, b: bool(a.get("_grp")) and b["idx"] in a["_grp"]
print(f"groups: {sum(1 for d in dub if d.get('_grp') and d['_grp'][0] == d['idx'])} groups of lines share their footage", flush=True)


def spk_piece(e, r=None, L=None):
    """a talking shot of the speaker of a wrong-speaker line, played while that line is heard (a SPEAKER shot)"""
    r = r or SH.get(int(e["shot"]))
    if not r: return False
    L = piece_lines(e) if L is None else L
    sw = [d for d in L if d.get("spk_ok")]
    return bool(sw) and all(d["speaker"] in talkers(r["n"]) for d in sw)


def wrong_mouth(e, r, lines):
    """[(line, talkers)] where this piece shows someone else talking while a dialogue line is said"""
    out = []
    if not talkers(r["n"]): return out
    sp = (e["to"] - e["from"]) / max(e["t1"] - e["t0"], 1e-6)
    for d in lines:
        if not d.get("speaker"): continue
        a, b = max(e["t0"], d["start_ms"] / 1000), min(e["t1"], d["end_ms"] / 1000)
        if b - a <= 0.1: continue
        sa, sb = e["from"] + (a - e["t0"]) * sp, e["from"] + (b - e["t0"]) * sp
        tk = talkers_at(r["n"], sa, sb) if FACE else talkers(r["n"])
        if tk and mouth_in(sa, sb) > min(0.3, 0.5 * (sb - sa)) and d["speaker"] not in tk: out.append((d, tk))
    return out


def line_before(t):
    return next((d for d in reversed(dub) if d["end_ms"] / 1000 <= t + 0.05), None)


def line_after(t):
    return next((d for d in dub if d["start_ms"] / 1000 >= t - 0.05), None)


def piece_lines(e):
    L = [d for d in dub if ov(e["t0"], e["t1"], d["start_ms"] / 1000, d["end_ms"] / 1000) > 0.1]
    return L or [d for d in (line_before(e["t0"]), line_after(e["t1"])) if d]          # a pause: the lines around it


def is_broll(e, L):
    """the piece shows footage that is not the own footage of these lines or their neighbours"""
    own = merge_iv([(a - OWN_PAD, b + OWN_PAD) for d in L for a, b in d["_near"]])
    return sum(ov(a, b, e["from"], e["to"]) for a, b in own) < OWN_FRAC * (e["to"] - e["from"])


def fix_short_broll(edl):
    """lengthen every B-roll piece shorter than MIN_BROLL to MIN_BROLL, taking the time from the clip before it (else the
    clip after it). Returns (new edl, notes)."""
    edl = sorted([dict(e) for e in edl], key=lambda e: e["t0"]); notes = []
    for i, e in enumerate(edl):
        d = e["t1"] - e["t0"]; r = SH.get(int(e["shot"]))
        if not r or d <= 0 or d >= MIN_BROLL - 0.01 or not is_broll(e, piece_lines(e)): continue
        if r["end"] - r["start"] < MIN_BROLL * SPEED_LO - 0.01: continue        # the shot is too short even at 0.8x
        need = MIN_BROLL - d
        for j in (i - 1, i + 1):
            if not 0 <= j < len(edl): continue
            n = edl[j]
            if is_broll(n, piece_lines(n)) or (n["t1"] - n["t0"]) - need < MIN_PIECE: continue
            sp = min(max((e["to"] - e["from"]) / d, SPEED_LO), SPEED_HI)
            foot = min(MIN_BROLL * sp, r["end"] - r["start"]); a = e["from"]; b = a + foot
            if b > r["end"]: b = r["end"]; a = max(r["start"], b - foot)
            if j == i - 1: n["t1"] = round(n["t1"] - need, 3); e["t0"] = n["t1"]
            else: n["t0"] = round(n["t0"] + need, 3); e["t1"] = n["t0"]
            e["from"], e["to"] = round(a, 3), round(b, 3)
            nd = n["t1"] - n["t0"]
            if (n["to"] - n["from"]) / nd > SPEED_HI + 0.001: n["to"] = round(n["from"] + SPEED_HI * nd, 3)   # too fast: trim its end
            notes.append(f"B-roll at {e['t0']:.2f} s lengthened {d:.2f} -> {MIN_BROLL:.1f} s; clip {'before' if j < i else 'after'} it now "
                         f"{nd:.2f} s at {(n['to'] - n['from']) / nd:.2f}x")
            break
    return edl, notes
```
Mouth under narration, talking footage, and the per-piece checks:
```python
def mouth_in(a, b):
    """seconds of moving mouth (lip scan) in source a-b"""
    return sum(ov(x, y, a, b) for x, y in lip_iv)
def eff_type(e, r):
    """footage with a moving mouth is talking footage (shown once), whatever the library calls the shot"""
    return "talking" if mouth_in(e["from"], e["to"]) > 0.3 else r["type"]
def narration_mouth(e, lines):
    """[(line, src a, src b, mouth s)] where this piece shows a moving mouth while a narration line is heard"""
    sp = (e["to"] - e["from"]) / max(e["t1"] - e["t0"], 1e-6); out = []
    for d in lines:
        if d["dialogue"]: continue
        a, b = max(e["t0"], d["start_ms"] / 1000), min(e["t1"], d["end_ms"] / 1000)
        if b - a <= 0.05: continue
        sa, sb = e["from"] + (a - e["t0"]) * sp, e["from"] + (b - e["t0"]) * sp
        m = mouth_in(sa, sb)
        if m > NARR_MOUTH: out.append((d, sa, sb, m))
    return out
def check(edl, T0, T1, lines, before):
    probs = []
    if not edl: return ["empty EDL"]
    if abs(edl[0]["t0"] - T0) > 0.02: probs.append(f"first t0 must be {T0}")
    if abs(edl[-1]["t1"] - T1) > 0.02: probs.append(f"last t1 must be {T1}")
    seen = [(e["from"], e["to"], e["t0"], e["t1"], f"earlier piece at dub {e['t0']:.1f} s", SH.get(int(e["shot"]), {}).get("type"), spk_piece(e)) for e in before]
    for k, e in enumerate(edl):
        r = SH.get(int(e["shot"]))
        d = e["t1"] - e["t0"]; u = e["to"] - e["from"]
        if k and abs(e["t0"] - edl[k - 1]["t1"]) > 0.02: probs.append(f"piece {k}: t0 {e['t0']} != previous t1 {edl[k - 1]['t1']}")
        if not r: probs.append(f"piece {k}: shot {e['shot']} is not in the list"); continue
        if e["from"] < r["start"] - 0.05 or e["to"] > r["end"] + 0.05 or u <= 0:
            probs.append(f"piece {k}: from/to {e['from']}-{e['to']} not inside shot {r['n']} ({r['start']:.2f}-{r['end']:.2f})")
        L = piece_lines(e); BR = bool(L) and is_broll(e, L)
        lo = SLOW_FLOOR if (not BR and L and all(x["slow"] and not x.get("gap") for x in L)) else SPEED_LO
        if d < MIN_PIECE - 0.01: probs.append(f"piece {k}: {d:.2f} s is shorter than {MIN_PIECE} s")
        elif u > 0 and not (lo - 0.01 <= u / d <= SPEED_HI + 0.01):
            probs.append(f"piece {k}: speed {u / d:.2f} outside {lo}-{SPEED_HI} (footage {u:.2f} s on {d:.2f} s)")
        if BR and d < MIN_BROLL - 0.01:
            probs.append(f"piece {k}: B-roll of only {d:.2f} s looks jumpy; a B-roll lasts at least {MIN_BROLL} s -- make it longer (the "
                         f"line's own footage may play faster, up to {SPEED_HI}x) or leave it out and let the own footage play slower (down to {SLOW_FLOOR}x)")
        SPKP = BR and spk_piece(e, r, L)
        for a, b, t0_, t1_, name, typ, sk in reversed(seen):
            if ov(a, b, e["from"], e["to"]) > 0.2:
                if SPKP or sk:
                    if abs(e["t0"] - t1_) < SPK_GAP:
                        probs.append(f"piece {k}: repeats talking footage {e['from']:.2f}-{e['to']:.2f} only {abs(e['t0'] - t1_):.1f} s after "
                                     f"{name}; a SPEAKER shot may repeat footage, but never right after itself")
                elif eff_type(e, r) not in ("reaction", "neutral", "establishing"):
                    probs.append(f"piece {k}: shot {r['n']} is a {eff_type(e, r)} shot{' (a mouth moves in this footage)' if eff_type(e, r) != r['type'] else ''} and its footage {e['from']:.2f}-{e['to']:.2f} is already "
                                 f"shown ({name}); action/talking footage is shown once: use other footage or let pieces run slower (down to {SPEED_LO})")
                elif not e.get("reuse"):
                    probs.append(f"piece {k}: footage already shown ({name}); mark reuse or use other footage")
                elif e["t0"] - t1_ < REUSE_GAP:
                    probs.append(f"piece {k}: repeats footage {e['t0'] - t1_:.1f} s after it was shown ({name}); a repeat needs {REUSE_GAP:.0f} s in between")
                break
        seen.append((e["from"], e["to"], e["t0"], e["t1"], f"piece {k}", r["type"], SPKP))
        gap_only = bool(L) and all(x.get("gap") for x in L)        # owner 2026-10-29: a NOT FOUND line may show the shot of
        if BR and not SPKP and (eff_type(e, r) == "talking" or                    # exactly what it tells (an action / object, e.g. the spy)
                   (eff_type(e, r) not in ("reaction", "neutral", "establishing") and not gap_only)):
            probs.append(f"piece {k}: B-roll must be a reaction / neutral / establishing shot without a moving mouth; shot {r['n']} "
                         f"source {e['from']:.2f}-{e['to']:.2f} is {eff_type(e, r)} footage")
        for d in (lines if BR else []):
            if not d.get("loc") or ov(e["t0"], e["t1"], d["start_ms"] / 1000, d["end_ms"] / 1000) < 0.1: continue
            if r.get("location") == d["loc"] or (r["scene"] == d.get("scene") and (r.get("location") != "mixed" or d["loc"] == "mixed")): continue
            if r["scene"] == d.get("scene") and any(ov(a, b, e["from"], e["to"]) > 0.2 for a, b in d["_near"]): continue   # a shot with a
            # flashback cut inside (place "gym (with a flashback ...)") is fine where the automatic match put that footage
            probs.append(f"piece {k}: shot {r['n']} is at location \"{r.get('location')}\" but line {d['idx']} plays at \"{d['loc']}\"; "
                         f"show only the line's location (same scene, or a reaction/neutral shot of the same location) -- never another background")
            break
        if BR and not any(d["broll_budget"] > 0 for d in L):
            probs.append(f"piece {k}: shot {r['n']} source {e['from']:.2f}-{e['to']:.2f} is B-roll (not the own footage of line(s) "
                         f"{', '.join(str(d['idx']) for d in L)}), but {'that line is' if len(L) == 1 else 'those lines are'} not slow "
                         f"({', '.join(str(d['own_speed']) + 'x' for d in L)}): B-roll is ONLY for slow lines (below 0.8x); show the line's own "
                         f"footage ({'; '.join(fmt_iv0(d['_own']) for d in L)}), a shorter part of it if it is too fast")
        for d, who in wrong_mouth(e, r, lines):
            probs.append(f"piece {k}: shot {r['n']} shows {'/'.join(who)} talking while dialogue line {d['idx']} is said by {d['speaker']}; "
                         f"a moving mouth must be the speaker's: show {d['speaker']} talking (a SPEAKER shot of this place) or a face that does not talk")
        for d, sa, sb, m in (narration_mouth(e, lines) if BR else []):
            probs.append(f"piece {k}: shot {r['n']} shows a moving mouth ({m:.1f} s in source {sa:.2f}-{sb:.2f}; mouth moving at "
                         f"{fmt_iv0([(max(x, e['from']), min(y, e['to'])) for x, y in lip_iv if ov(x, y, e['from'], e['to']) > 0.05])}) while "
                         f"narration line {d['idx']} is heard; never a talking mouth over narration: use footage where nobody's mouth moves")
    for d in lines:                                 # a not-matched line shows the person its story plan names
        gp_ = d.get("gplan")
        if not gp_ or not gp_["who"] or not (T0 <= (d["start_ms"] + d["end_ms"]) / 2000 < T1): continue
        a0, a1 = d["start_ms"] / 1000, d["end_ms"] / 1000
        on = sum(ov(e["t0"], e["t1"], a0, a1) for e in edl if gp_["who"] in SH.get(int(e["shot"]), {}).get("people", []))
        if on < 0.5 * (a1 - a0):
            probs.append(f"not-matched line {d['idx']} \"{d['text'][:40]}\": its story plan shows {gp_['who']} ({gp_['show']}, suggested "
                         f"shots {gp_['shots']}), but {gp_['who']} is on screen only {on:.1f} of {a1 - a0:.1f} s")
    brs = [(k, e) for k, e in enumerate(edl) if SH.get(int(e["shot"])) and is_broll(e, piece_lines(e))]
    cnt = {}
    for k, e in brs:                                # B-roll: at most MAX_BROLL_USES per shot, used in turn
        n = int(e["shot"]); cnt[n] = cnt.get(n, 0) + 1
        if cnt[n] == MAX_BROLL_USES + 1:
            probs.append(f"piece {k}: B-roll shot {n} is used {cnt[n]} times in this stretch; a B-roll shot is used at most {MAX_BROLL_USES} "
                         f"times in the episode -- use another B-roll shot of this place")
    for (k0, e0), (k, e) in zip(brs, brs[1:]):
        if int(e["shot"]) != int(e0["shot"]): continue
        r = SH[int(e["shot"])]
        alt = [x["n"] for x in LIB["shots"] if x["n"] != r["n"] and x.get("location") == r.get("location") and x["broll"] == "yes"
               and x["type"] in SHARED and x.get("location") != "mixed"]
        if alt:
            probs.append(f"piece {k}: B-roll shot {r['n']} again right after piece {k0} used it; use this place's B-roll shots in turn "
                         f"(e.g. {', '.join(str(a) for a in alt[:4])}) so it does not look repeated")
    done_g = set()
    for d in lines:                                 # a slow line (or group): own footage first, B-roll only for the rest
        if d["broll_budget"] <= 0 or not (T0 <= (d["start_ms"] + d["end_ms"]) / 2000 < T1): continue
        grp = tuple(d.get("_grp") or [d["idx"]])
        if grp in done_g: continue
        done_g.add(grp); ms = [x for x in lines if x["idx"] in grp] or [d]
        used = sum(ov(e["t0"], e["t1"], x["start_ms"] / 1000, x["end_ms"] / 1000) for x in ms for e in edl if is_broll(e, [x]))
        if used > max(d["broll_budget"], MIN_BROLL) + 0.75:
            who = f"lines {grp[0]}-{grp[-1]} (one group)" if len(grp) > 1 else f"line {d['idx']}"
            probs.append(f"{who} show {used:.1f} s of B-roll but need at most {d['broll_budget']:.1f} s (own footage "
                         f"{fmt_iv0(d.get('_gown') or d['_own'])} plays at {d['own_speed']}x): show more of the own footage, down to 0.8x, before B-roll")
    for d in lines:                                 # dialogue: a moving mouth must start within LIP_TOL of the voice
        if not d["dialogue"] or (d.get("gap") and not d.get("spk_ok")): continue  # a gap line has no source footage of its speaker: B-roll only
        ls = d["start_ms"] / 1000
        if not (T0 <= ls < T1): continue
        e = next((e for e in edl if e["t0"] - 0.01 <= ls < e["t1"]), None)
        if not e: continue
        sp = (e["to"] - e["from"]) / max(e["t1"] - e["t0"], 1e-6)
        src_at = e["from"] + (ls - e["t0"]) * sp
        lips = [(a, b) for a, b in lip_iv if ov(a, b, e["from"], e["to"]) > 0.05]
        starts = [max(a, e["from"]) for a, b in lips]
        good = any(abs(st - src_at) / sp <= LIP_TOL for st in starts) or any(a < src_at - 0.05 < b for a, b in lips)
        if not good:
            probs.append(f"dialogue line {d['idx']} \"{d['text'][:40]}\" starts at {ls:.2f} s but the piece on screen then (shot "
                         f"{e['shot']}, source {src_at:.2f} s) has no mouth starting to move there (mouth moving: "
                         f"{', '.join(f'{a:.2f}-{b:.2f}' for a, b in lips) or 'never'})")
    return probs
```
The location of a dub line and of a piece (4C.2):
```python
def line_auto(d):
    return [(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000) for s in auto
            if not s.get("video") and ov(d["start_ms"], d["end_ms"], s["it_start_ms"], s["it_end_ms"]) > 0]


def line_loc(d):
    tot = {}
    for a, b in d["_auto"]:
        for r in LIB["shots"]:
            o = ov(a, b, r["start"], r["end"])
            if o > 0 and r.get("location") and r["location"] != "mixed": tot[r["location"]] = tot.get(r["location"], 0) + o
    return max(tot, key=tot.get) if tot else None
```

### 4C.7 Reading the result
- `scene_manifest.edl.json`: per stretch its lines, the shots offered, the pieces (dub t0-t1, shot, source from-to, line,
  reuse, why) and every check round; `stats`: wall time, first pass, Claude calls and tokens, whole-episode fixes and
  anything left. `sceneedit.py` prints `problems left in stretches: N, whole-episode: M` — report both (Step 11).
- The scene-edit sheet (`<show>_ep<N>_scene_edit.xlsx`): one row per piece with video time, shot, who / type @ location,
  what happens, source time, speed, the dub line (dialogue/narration @ location), the English subtitle, repeat and
  Claude's reason; green = dialogue, yellow = repeat.

### 4C.8 Transitions on a scene-edited episode
`scene_transitions.py W W/broll_library/broll_library.json` writes `W/transitions_plan.json` (owner, 2026-10-29): a
transition (`auto` = the owner's three kinds in random order) at **every clip switch except the cut INTO a B-roll piece**
(the manifest marks B-roll pieces `"broll": true`): clip → B-roll is a straight cut, B-roll → next clip gets the
transition. ep34 first 5 min: 56 transitions + 34 straight cuts into B-roll. `--scenes-only` gives the older plan (a
transition only at the first clip switch after the story moves to another scene). Then
`transitions --plan W/transitions_plan.json`.

### 4C.9 Time and cost (reference, TGS ep32, 13:20, Apple M4, claude-sonnet-5-5 at $2 / $10 per million tokens in / out)

| Step | Time | Claude cost |
|---|---|---|
| Whisper subtitles (dub + source) | 7.4 min | — |
| Lip scan (Mac GPU) | 13.8 min | — |
| words, analyze, sheet, manifest, lipsync, groups | 1.7 min | — |
| Step 2 automatic (`ai_match.py`) | 1.6 min | $0.60 |
| Shot library | 1.8 min | $0.30 |
| Scene edit, parallel 8 (29 stretches) | 13.7 min (5 min for a 5-min test) | $5.60 (about $2 per 5 min with the short answers of v20) |
| Render, verify, bookends, transitions | 6 min | — |
| Subtitle videos (2) + translation | 10.8 min | — |
| Whole episode | about 57 min | about $6.50 |

### 4C.10 What not to change
- The thresholds above; **B-roll only for slow lines**; the B-roll types (reaction / neutral / establishing only); the
  narration rule; the location guard; the check rounds. A change here changes every episode — only with the owner.
- Never let Claude's answer pass without the check; never cache an empty answer; never deliver with problems left
  without listing them (Step 11).
- Keep the automatic edit (`manifest_groups.auto.json`): it is the map for 4C and the fallback.

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

**Scene-edited episodes (Step 4C, v20):** do not put a transition at every clip switch. Use the plan from
`scene_transitions.py` (`--plan W/transitions_plan.json`): a transition only where the story moves to another scene,
straight cuts everywhere else (4C.8).

## Step 8. subs: burn the Italian subtitles onto a copy of the clean master (last step before check)

**Font (v20):** Poppins Bold must be installed or passed with `--font <path>/Poppins-Bold.ttf` (the Mac runner had none: `subs` stopped with "Poppins Bold not found"). Use `--max-lead 0` on a scene-edited episode.

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

## Step 9. translate + subs: the English-subtitle video (mandatory)

```bash
python3 dubstitch.py translate --work W --out OUT/TDMB_EP<N>_IT_english_google.srt
python3 dubstitch.py subs --work W --srt OUT/TDMB_EP<N>_IT_english_google.srt --out OUT/TDMB_EP<N>_IT_final_en_subs.mp4   # TDMB
python3 dubstitch.py subs --work W --style box --srt OUT/TOLR_EP<N>_IT_english_google.srt --out OUT/TOLR_EP<N>_IT_final_en_subs.mp4   # other shows
```

The English video uses the same style as the Italian one. HIH (dynamic strip): translate the **dynamic** SRT (`--srt W/<SHOW>_EP<N>_IT_dynamic.srt`), so the English cards keep the Italian cards' times and strips (Step 8C).

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
- (v20) Scene edit: stretches, pieces, speed range, reaction repeats, problems left (per stretch and whole episode),
  mouth over narration (must be 0), pieces at another place (must be 0), transitions; the lip-scan engine
  (MPS / CUDA / ONNX DirectML ...).
- **(v20, mandatory) the time table of 11.1**, every step with its time and Claude cost, totals, failed tries.
- **(v21, mandatory) Not matched rows:** the count and every row (number, time, text) from the mapping sheet's
  "Not matched" tab — the places where the dub's story is changed.
- Where the files are, and what you're unsure of. Never claim "all matched" without reading every
  flagged row.

### 11.1 Time and cost of every step (mandatory after every episode, owner 2026-10-28)
Whoever runs the episode (Claude, Codex, another agent or a person) reports how long every step took, so episodes and
machines can be compared and slow steps found.

- **With `run_episode.sh`:** nothing to do — it writes `W/run_times.tsv` and at the end prints and saves the table
  (`OUT/<show>_ep<N>_run_times.md`).
- **Steps run by hand or by another runner:** time each step and append one line per step (tab-separated) to
  `W/run_times.tsv`: `name  seconds  spend_before  spend_after  exit_code  clock_time`. The spend is the AI gateway's
  `GET /key/info` → `spend` (`python -c "from dubstudio import ai; print(ai.key_info()['spend'])"` in `adaptation-tool/ai`),
  or `NA` when the step uses no AI. Use the step names of `run_episode.sh` (`srt_dub, srt_src, lip_nets, lip_mesh,
  lip_merge, words_src, words_dub, analyze, ai_match, sheet, manifest, lipsync, groups, library, scene_edit, use_scene,
  render, verify, bookends, trans_plan, transitions, subs_dub, translate, subs_en, scene_sheet, check`). A failed try
  gets its own line with its exit code, then the rerun another line. Example (bash):
  ```bash
  t0=$(date +%s); python3 dubstitch.py render --work $W --subs keep; rc=$?
  printf "render\t%s\tNA\tNA\t%s\t%s\n" $(( $(date +%s) - t0 )) $rc $(date +%H:%M:%S) >> $W/run_times.tsv
  ```
- **Then:** `python3 scripts/run_report.py W OUT/<show>_ep<N>_run_times.md` and paste the table into the report.

The table (TGS ep32, Apple M4, v20 pipeline; the reference to compare with):

| # | Step | Time | Claude cost | Result |
|---|---|---|---|---|
| 1 | Whisper subtitles, dub | 5 min 01 s | - | ok |
| 2 | Whisper subtitles, source | 2 min 22 s | - | ok |
| 3 | Lip scan, faces + talking (S3FD, TalkNet) | 10 min 42 s | - | ok |
| 4 | Lip scan, mouth (Face Mesh) | 3 min 05 s | - | ok |
| 5 | Lip scan, join | 0 s | - | ok |
| 6 | Word times, source | 6 s | - | ok |
| 7 | Word times, dub | 16 s | - | ok |
| 8 | Automatic alignment | 21 s | - | ok |
| 9 | Step 2 by Claude (matching) | 1 min 33 s | $0.56 | FAILED try (rerun below) |
| 10 | Step 2 by Claude (matching) | 1 min 37 s | $0.63 | ok |
| 11 | Mapping sheet + integrity check | 0 s | - | ok |
| 12 | Edit list | 1 min 00 s | - | ok |
| 13 | Lip-sync rows | 1 s | - | ok |
| 14 | Groups (automatic edit, fast-clip rule) | 3 s | - | ok |
| 15 | Shot library (Claude) | 1 min 49 s | $0.30 | ok |
| 16 | Scene edit (Claude cuts the picture) | 13 min 40 s | $5.62 | ok |
| 17 | Scene edit -> edit list | 0 s | - | ok |
| 18 | Render | 2 min 09 s | - | ok |
| 19 | Picture check | 29 s | - | ok |
| 20 | Opening / ending | 23 s | - | ok |
| 21 | Transition plan | 0 s | - | ok |
| 22 | Transitions | 2 min 57 s | - | ok |
| 23 | Dub subtitle video | 1 s | - | FAILED try (rerun below) |
| 24 | Dub subtitle video | 5 min 42 s | - | ok |
| 25 | English translation | 22 s | - | ok |
| 26 | English subtitle video | 4 min 46 s | - | ok |
| 27 | Scene-edit sheet | 1 s | - | ok |
| 28 | Final check | 2 s | - | ok |
| | **Total (steps that worked)** | **56 min 54 s** | **$6.55** | |
| | Failed tries (lost) | 1 min 34 s | $0.56 | |

Biggest steps: Scene edit (Claude cuts the picture) 13 min 40 s, Lip scan, faces + talking (S3FD, TalkNet) 10 min 42 s, Dub subtitle video 5 min 42 s. Clock time from first to last step: about 58 min 28 s of step time (plus any waiting between runs).

Also say on which computer it ran (Mac model / Windows + graphics chip), the lip-scan engine, and anything that made
it slow (a failed try, the VPN, waiting for the owner).

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

- **Scene edit, not row by row (v20).** Filling each line on its own put lip clips at the wrong moment and left 64% of
  TGS ep31 below 0.8x; the scene edit has every piece at 0.80-1.25x and the story order right (owner checks on ep31/32).
- **A "reaction" can talk.** The library labels shots from 3 still frames; shot 7 of TGS ep32 (Aiden "looking down")
  had his mouth moving for 3.2 s and was used 5 times over narration in the first minute. The lip scan decides:
  footage with a moving mouth is talking footage, and never plays under narration (31 places → 0 on ep32).
- **Quotes run over sentences.** „Sei doch nicht so paranoid! … Das Fitnessstudio gehört … Niemand wird uns … Komm
  schon …“ is one quote; marking only the sentences with a quote mark made 9 of Charlotte's lines narration.
- **Same place only (location guard).** A stretch can span two scenes; without a per-piece check ep32 showed the
  elevator during garage narration and Aiden in his gym shirt before the gym (3 in the first 5 min → 0).
- **The automatic match is not a map of places.** Where it stretched one shot over many lines (ep32 lines 99-106) the
  place came out wrong; Claude reads where each line plays instead (`line_places.json`).
- **Parallel needs the footage shared out first.** Neighbouring stretches see the same shots (ep31: 45 of 153 shots used
  in several stretches); without ownership two stretches take the same action shot.
- **VPN off = calls hang.** Every AI step checks the gateway first and stops at once.
- **Ask for short answers.** Long "why" texts made Claude's output 600k tokens per episode and sometimes cut answers off;
  ≤ 15 words per reason cut the output to about a fifth.
- **B-roll only where the footage is too short (v21).** Letting the editor cut to reactions anywhere replaced good
  matched footage and brought talking "reaction" shots over narration; the owner's rule: every line keeps its own
  footage, B-roll only fills what a slow line cannot fill at 0.8x.
- **Never another line's footage on an added event (2026-10-29).** TGS ep34: "she grabbed a coffee cup and smashed it
  against the wall" had no source line; it was glued to "God, you are a terrible liar" and showed Ava saying it for
  14 s (also a sentence-splitting bug with “ quotes). Now it is a gap and gets B-roll of the same room.

## Step 13. Checklist
- [ ] Every step run (run rule); anything that could not run was reported to the owner before going on.
- [ ] Same episode confirmed (DP matched rate normal); subs keep/crop, labels, output names, subtitle style settled.
- [ ] `words` for both sides (with the show's `--clauses`): summary looked at, no long run of "not heard" words.
- [ ] `analyze` ("using word-timed sentences" for both sides), timing check passed, `analysis.txt` read.
- [ ] By meaning in `decisions.json`: every unmatched, Case A, duplicate, second-look, repeat and unused line.
- [ ] `sheet` passed the integrity check; every unused source line has a note.
- [ ] `manifest` run; slow and fast clips noted.
- [ ] (lip-sync shows) whole source video scanned (`lipgpu.py` + `lipmesh.py` + `lipmerge.py`, or the reference
      `lipdetect.py --range`), `lipsync` run, a few lip-sync rows looked at.
- [ ] `groups` (TOLR `--size 7`): 0 shared footage; lip groups and trimmed lip sentences noted; yellow groups looked at.
- [ ] Fast-clip rule (4A.1): groups above 1.25x trimmed, then `fastfix`: no clip above 1.25x ("Fast clips" tab looked at);
      every trimmed row marked "trimmed" for the editor and the count reported.
- [ ] `render` finished; `verify` passed, `qc_sheet.jpg` looked at.
- [ ] `bookends` (TOLR `--out W/clean_noglow.mp4`; WBM `--no-ending`): length check passed.
- [ ] (TOLR) `transitions --out ..._final_clean.mp4`: saved, same length, switches per kind noted, no kind twice in a row.
- [ ] `subs` Italian in the show's style (TDMB: strip, self-check 0/0/0, `subs_qc.jpg` looked at; other shows:
      `--style box`, length = clean master, frames looked at).
- [ ] (HIH) `captions` (3-line captions listed), `dynsrt` (words in = words out, 3-line zones noted), both videos
      with `subs --style dynamic`: self-check 0/0, `subs_qc_<name>.jpg` looked at (no US caption above the strip).
- [ ] `translate` (not blocked; names kept) then `subs --srt` English in the same style.
- [ ] Exactly three videos in the output folder: clean, `_it_subs`, `_en_subs`.
- [ ] (v20) `library.py`: shots, scenes and locations printed; `sceneedit.py --parallel 8`: problems left 0 (or listed),
      whole-episode 0; mouth over narration 0; pieces at another place 0; scene-edit sheet looked at.
- [ ] (v20) Transitions from `scene_transitions.py` (story scene changes only).
- [ ] (Windows) lip scan on ONNX DirectML/OpenVINO (log line `ONNX engine ...`), `compare_lips.py` passed once on this PC.
- [ ] **Time table** (11.1): every step's time + Claude cost from `run_report.py`, failed tries included, in the report.
- [ ] `check`: FINAL CHECK PASSED, `final_check.jpg` looked at. Step 11 report given.

## Step 14. The tools (all in the Appendix; md5 of this version)

| File (in `adaptation-tool/`) | md5 | Purpose |
|---|---|---|
| `scripts/dubstitch.py` | e71dc5a7d3ae1faf5f9058676ac1f08a | every command: words, analyze, sheet, manifest, lipsync, groups (fast-clip rule), render, verify, bookends, transitions (`--plan`), subs (`--style strip\|box\|dynamic`), captions, dynsrt, translate, check (`python3 dubstitch.py -h`) |
| `scripts/pngsubs.py` | 27d95158ccf38bbf4b7b2dc7112f537f | box subtitle style (Step 8B) |
| `scripts/dynsubs.py` | 4521d58a4daf18880bc3c101877bf00d | dynamic strip (Step 8C) |
| `scripts/lipdetect.py` | fd3eed4aa869749be7053513997074bf | reference lip detector; library for the others |
| `scripts/lipfast.py` | 0c8355ddebc839c44fd8de98c3c0f8ff | same output as `lipdetect.py --range`, decodes once |
| `scripts/lipgpu.py` | 1301d58e7cd4da8870901f8e783daa1b | Step 4B heavy half (S3FD + TalkNet): PyTorch on MPS/CUDA/CPU, or ONNX Runtime (DirectML/OpenVINO) on Windows without NVIDIA (4B.6) |
| `scripts/lipmesh.py` | 09f625ba2b31251a71daec2ca578abdd | Step 4B Face Mesh half |
| `scripts/lipmerge.py` | 667b83918546a28f89b0c43cf796e637 | joins the halves into `W/lips_range` |
| `scripts/export_lip_onnx.py` | a7e2d13177539ff4aed4d5f5f94f1eec | Step 4B.6: S3FD + TalkNet → ONNX once, checked against PyTorch |
| `scripts/compare_lips.py` | 5b89cc31e23a778b2c18a50991b8e4f4 | Step 4B.6: proof that two lip scans give the same result |
| `scripts/mksrt.py` | 758820543686e47a08eb1eb7c2977f19 | Step 0B: SRT from audio/video (Whisper + script cleaning); also `<out>_words.json` |
| `scripts/srtclean.py` | d4217626df812274a51fecdac2798dba | Step 0B: the SRT-cleaning platform's pipeline |
| `scripts/mkdec.py` | 72fa5cde309f6bdb70c0529da328e79b | Step 2 (manual read): spec → `decisions.json` |
| `scripts/merged.py` | 8d476f60331553013a75bf2275880ba8 | Step 2 (manual read): reading view |
| `scripts/run_episode.sh` | 90bccc10206aaf185b747150f7515ef3 | v20: every step of one episode in order, resumable, time + AI cost per step |
| `scripts/run_report.py` | ad44c5f2c3a590ea11c7bc26cf9cee5d | v20: the time + Claude cost table of every step for the owner (Step 11.1, mandatory) |
| `mac_lipscan/mac_runner.command` | 213c5039c1d5e7d568f1a0bcfa7029cb | Step 4B.4: runs job files natively on a Mac |
| `ai/dubstudio/__init__.py` | b7fbde62df27abaaa97a90d4bac8a0a8 | package marker |
| `ai/dubstudio/ai.py` | b31fba91b7cac1dfabf6c9933df84b3b | AI gateway client (OpenAI-compatible; settings from `adaptation-tool/.env`) |
| `ai/dubstudio/names.py` | 7f803b15393507c30021c850c69fd1da | character names (used by `ai_match.py`) |
| `ai/dubstudio/pipeline/__init__.py` | 2b9053c8947c85c4817febdbbb4a580c | package marker |
| `ai/dubstudio/pipeline/library.py` | d8b0bfcc447774947566fa2023185689 | Step 4C.1: shot library (cuts, frames, Claude labels, scenes, locations) |
| `ai/dubstudio/pipeline/sceneedit.py` | d1754fa1f5ad2696223900a66cb64f53 | Step 4C: the scene edit (rules, checks, parallel, location guard) |
| `ai/dubstudio/pipeline/scene_transitions.py` | 167d469d0242a8b08ddae1b7cf5f5168 | Step 4C.8: transitions only at story scene changes |
| `ai/dubstudio/pipeline/sceneedit_sheet.py` | 024588d0458fed2b5b367dba29a60482 | Step 4C.7: the scene-edit review sheet |
| `ai/dubstudio/pipeline/ai_match.py` | 0097423fb75381ecc21fa3a85426b767 | Step 2 automatic: Claude decides the flagged rows |
| `ai/dubstudio/pipeline/decisions.py` | 98abe5051aa27c1150386ea34b2a10a0 | Step 2 automatic: spec ↔ decisions, integrity check |
| `ai/dubstudio/pipeline/translate_ai.py` | 4b64d4cbe2fbba61753944f5c926ba69 | Step 9: Claude translates what Google left |

Python packages: see Step 0; v20 adds `onnx onnxscript onnxruntime-directml` (or `onnxruntime-openvino`) on Windows without NVIDIA (4B.6). Third-party (not in this file, downloaded in Step 0): TalkNet-ASD with its two weight files, Whisper "small"
(faster-whisper) and the multilingual MiniLM sentence model (both downloaded automatically on first use).

When a tool is changed, give it a new md5 here and in its Appendix marker, and keep the old file next to it
(`<name>_v<k>.py`).

---

# Appendix: complete source code of every tool

Each block is one file, exactly. The line above each block names the file and its md5; `unpack.py`
(see "Start here") writes them all. Blocks are fenced with `~~~~~~` lines.

## Appendix: `scripts/dubstitch.py`

<!-- FILE: scripts/dubstitch.py md5=e71dc5a7d3ae1faf5f9058676ac1f08a -->
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
  captions  (HIH, dynamic strip) read the source's burned-in captions (tesseract OCR): lines per caption, where they sit
  dynsrt    (HIH, dynamic strip) dub cards that fill a strip 2 or 3 caption lines tall, each word at its own time
  subs      burn an SRT (white strip, Poppins Bold; --style box; --style dynamic for HIH) onto the clean master
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
# a sentence ends at . ! ? … (plus any closing quotes) when the next word does NOT start with a small letter: a capital, a
# digit, ANY quote mark („ « “ ‘ ‚ ‹ " '), a dash or a bracket starts a new sentence (TGS ep34: "zerschellen. “Du bist ..."
# was one row because “ was not known as an opening quote -- never again for any quote style)
_SENT_END = re.compile(r'(?<=[.!?…])["»«“”’›‹\']*\s+(?=\S)(?![a-zß\u00e0-\u00ff\u0101-\u017f])')

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
# --clause-long L (Dub Studio, owner Oct 2026): only sentences of at least L words are cut into clauses; shorter ones stay
# whole (every show has talking faces, and the lip scan judges whole sentences). 0 = cut any sentence (as before).
CLAUSE_LONG = int(os.environ.get("DUBSTITCH_CLAUSE_LONG", "0") or 0)
_CLAUSE_CUT = re.compile(r'[,;:]["»”’]?(?=\s)|(?<=\s)[–—](?=\s)')

def clause_bounds(full, bounds, n_min):
    out = []
    for a, b in zip(bounds, bounds[1:]):
        out.append(a); last = a
        if CLAUSE_LONG and len(full[a:b].split()) < CLAUSE_LONG:
            continue                                               # a short or normal sentence stays whole
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
    two = [(c.index, c.text[:80]) for c in sents if re.search(r'[.!?…]["»«“”’›‹\']*\s+(?![a-zß\u00e0-\u00ff\u0101-\u017f])\S', c.text)]
    print(f"sentence check: {len(sents)} rows; rows that still hold two sentences: {two or 'none'}")
    if a.first: sents = sents[:a.first]
    t_to = a.until or (sents[-1].end_ms / 1000 + 5.0)
    t_to = min(t_to, probe_duration_ms(media) / 1000)
    heard, todo = transcribe_words(media, asr_dir(a.work, media), lang, models, 0.0, t_to, max_seconds=a.max_seconds,
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
            fp = os.path.join(asr_dir(a.work, media), f"{lang}_{a.model}_redo_{w0:09.2f}_{w1:09.2f}.json")
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

def asr_dir(work, media):
    """Whisper cache folder of ONE media file: W/asr/<file name>/. (One folder per language mixed up a UK dub and its
    English source video in the same work folder: the source got the dub's words. TOLR DE-UK ep9, 2026-10-27.)"""
    return os.path.join(work, "asr", re.sub(r"[^\w.-]", "_", os.path.splitext(os.path.basename(media))[0])[:80])

def probe_duration_ms(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return int(round(float(out) * 1000))

def probe_video(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    w, h, r = out.strip().split(",")[:3]; a, b = r.split("/")
    return int(w), int(h), round(int(a) / int(b))

OUT_FPS = 25   # owner 2026-10-30: every output video is rendered at 25 fps, whatever the source's rate (TGS: 30)

def out_fps(cfg):
    """frame rate of every video this pipeline writes (config "out_fps" overrides; default OUT_FPS)"""
    return int(cfg.get("out_fps") or OUT_FPS)

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
    # every row with no source sentence: the story was changed here (owner, 2026-10-29) -> its own tab, told to the owner
    nm = [r for r in rows if r["src_start"] is None]
    ws3 = wb.create_sheet(f"Not matched ({len(nm)})", 1)
    for ci, h in enumerate(["#", f"{L[0]} Start", f"{L[0]} End", f"{L[0]} Text", "Why (Step 2)"], 1):
        cell = ws3.cell(1, ci, h); cell.font = hf; cell.fill = hfill
    for ri, r in enumerate(nm, 2):
        for ci, v in enumerate([r["idx"], fmt(r["dub_start"]), fmt(r["dub_end"]), r["dub_text"], r["notes"]], 1):
            cell = ws3.cell(ri, ci, v); cell.font = bf; cell.alignment = wrap; cell.fill = fills["red"]
    for ci, w in enumerate([6, 11, 11, 70, 70], 1):
        ws3.column_dimensions[chr(64 + ci)].width = w
    out = a.out
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True); wb.save(out)
    save(a.work, "rows.json", dict(rows=rows, repeat={str(k): v for k, v in repeat.items()}))
    print(f"NOT MATCHED (no source sentence -- the story is changed here; covered with B-roll of the same place): "
          f"{[(r['idx'], r['dub_text'][:50]) for r in nm] or 'none'}")
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

def seg_sig(s, nf, crop_h, W, H, FPS):
    """A rendered segment's cache key. A segment may name its own video ("video": an imported clip from the editor);
    the source video's segments keep the old key, so their cache stays valid."""
    sig = f"{s['us_start_ms']:.3f}|{s['us_end_ms']:.3f}|{nf}|{crop_h}|{W}x{H}@{FPS}"
    if s.get("zoom"): sig += f"|z{s['zoom']}"          # reused footage shown zoomed in (slow-clip B-roll)
    return sig + f"|{s['video']}" if s.get("video") else sig


def cmd_render(a):
    cfg = cfg_get(a)
    mname, M = active_manifest(a.work, cfg, a.manifest); segs = M["segments"]; tag = manifest_tag(mname)
    a.out = a.out or os.path.join(a.work, f"video{tag}_nosubs.mp4")
    print(f"rendering {mname}: {len(segs)} segments")
    W, H, _src_fps = probe_video(cfg["src_video"]); FPS = out_fps(cfg)
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
        sig = seg_sig(s, nf, crop_h, W, H, FPS)
        if (os.path.exists(out + ".ok") and open(out + ".ok").read() == sig) or time.time() > t_end:
            return
        k = (nf / FPS) / max((s["us_end_ms"] - s["us_start_ms"]) / 1000, 0.04)
        frame = (f"crop={W}:{crop_h}:0:0,pad={W}:{H}:0:0:black" if crop_h and not s.get("video") else
                 f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2")
        if s.get("zoom"):                               # a segment may ask for a centred zoom-in (reused B-roll)
            # anchored at the bottom: burned-in source captions stay where the subtitle strip covers them
            z = float(s["zoom"]); frame = f"crop=iw/{z:.4f}:ih/{z:.4f}:(iw-iw/{z:.4f})/2:ih-ih/{z:.4f},{frame}"
        vf = f"setpts={k:.6f}*PTS,{frame},fps={FPS},tpad=stop_mode=clone:stop_duration=2"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{s['us_start_ms'] / 1000:.3f}",
                            "-to", f"{s['us_end_ms'] / 1000:.3f}", "-i", s.get("video") or cfg["src_video"], "-vf", vf, "-frames:v", str(nf),
                            "-r", str(FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-an", "-movflags", "+faststart", out], capture_output=True, text=True)
        if r.returncode: print(f"FAILED segment {i}: {r.stderr[-300:]}")
        else: open(out + ".ok", "w").write(sig)
    with ThreadPoolExecutor(a.workers) as ex: list(ex.map(job, range(len(segs))))
    def is_done(i):
        s = segs[i]; ok = os.path.join(d, f"{i:04d}.mp4.ok")
        nf = max(round(s["it_end_ms"] * FPS / 1000) - round(s["it_start_ms"] * FPS / 1000), 1)
        return os.path.exists(ok) and open(ok).read() == seg_sig(s, nf, crop_h, W, H, FPS)
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
    FPS = out_fps(cfg)
    vd, ad = probe_duration_ms(final), probe_duration_ms(cfg["dub_audio"])
    print(f"final video {vd / 1000:.3f}s vs dub audio {ad / 1000:.3f}s (diff {abs(vd - ad)}ms)")
    ok_dur = abs(vd - ad) <= 2 * 1000 / FPS + 50
    def grab(path, t, zoom=None):
        z_ = float(zoom or 1)                                     # a zoomed-in segment: compare zoomed (bottom-anchored)
        zf = f"crop=iw/{z_:.4f}:ih/{z_:.4f}:(iw-iw/{z_:.4f})/2:ih-ih/{z_:.4f}," if zoom else ""
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", path, "-frames:v", "1",
                              "-vf", zf + "scale=64:64,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).astype(float) if len(raw) == 4096 else None
    def corr(x, y):
        return float(np.corrcoef(x, y)[0, 1]) if x is not None and y is not None and x.std() > 0 and y.std() > 0 else 1.0
    def check(s):
        f0 = round(s["it_start_ms"] * FPS / 1000); f1 = max(round(s["it_end_ms"] * FPS / 1000), f0 + 1)   # a piece under one frame
        t = (f0 + (f1 - f0) // 2 + 0.5) / FPS
        ts = (s["us_start_ms"] + (t - f0 / FPS) / ((f1 - f0) / FPS) * (s["us_end_ms"] - s["us_start_ms"])) / 1000
        v = s.get("video") or cfg["src_video"]                  # an imported clip is checked against its own file
        z = s.get("zoom"); x = grab(final, t); c = corr(x, grab(v, ts, z))
        if c < 0.9:
            c = max(corr(x, grab(v, ts + k / FPS, z)) for k in range(-6, 7))
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
        pngsubs.burn(video, srt, a.out, wd, off, a.chunk, min(a.max_seconds, 10 ** 6), a.font, keep_parts=a.keep_parts,
                     size=a.box_size, bottom=a.box_bottom, box_alpha=a.box_alpha)
        cfg = load(a.work, "config.json")
        cfg.setdefault("subs_outputs", {})[os.path.abspath(a.out)] = dict(srt=os.path.abspath(srt), video=os.path.abspath(video),
                                                                          offset_s=off, style="box")
        save(a.work, "config.json", cfg); return
    if a.style == "dynamic":   # HIH: strip height follows the source caption under it (2 or 3 lines); see dynsubs.py
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import dynsubs
        if not os.path.exists(os.path.join(a.work, "dynsubs_plan.json")):
            raise SystemExit("subs --style dynamic needs `captions` and `dynsrt` first (W/dynsubs_plan.json)")
        if not a.srt and cfg.get("dynamic_srt"): srt = cfg["dynamic_srt"]
        cues = [(c.start_ms, c.end_ms, c.text) for c in parse_srt(srt)]
        print(f"subtitles: {os.path.basename(srt)} ({len(cues)} cards) on {os.path.basename(video)}, shifted {off:+.3f}s; dynamic strip")
        ok, rep = dynsubs.burn(a.work, video, cues, a.out, off, cfg, find_font(a.font, a.size), workers=min(a.workers, 2),
                               max_seconds=min(a.max_seconds, 10 ** 6), chunk=a.chunk, min_gap=a.min_gap)
        if rep is None:
            print("not finished -- run the same subs command again (it resumes)"); sys.exit(3)
        cfg = load(a.work, "config.json")
        cfg.setdefault("subs_outputs", {})[os.path.abspath(a.out)] = dict(srt=os.path.abspath(srt), video=os.path.abspath(video),
                                                                          offset_s=off, style="dynamic", report=rep)
        save(a.work, "config.json", cfg)
        if not ok: sys.exit(1)
        return
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
    segs = [s for s in segs if not s.get("video")] or segs     # imported clips say nothing about the source's opening/ending
    src = cfg["src_video"]; W, H, _src_fps = probe_video(src); FPS = out_fps(cfg); src_ms = probe_duration_ms(src)
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
    # owner 2026-10-29: never an opening that shows a person speaking (TGS ep34: 0.87 s of Anya with a moving mouth, just
    # before her source line, in front of the dub's first line, which Paul says). The source lip scan (lipsync.json) decides:
    # more than 0.2 s of moving mouth inside the opening = no opening; the video starts with the dub.
    if n_in and os.path.exists(os.path.join(a.work, "lipsync.json")):
        o1 = n_in / FPS
        m = sum(max(0.0, min(y, o1) - max(x, 0.0)) for x, y in (load(a.work, "lipsync.json").get("intervals") or []))
        if m > 0.2:
            print(f"opening left out: source 0.00-{o1:.2f}s shows a person speaking (moving mouth {m:.2f}s)"); n_in = 0
    fade = source_fade_ms(a.work, src)
    if fade is not None and n_out:
        keep = max(0, int((fade - 100) * FPS / 1000) - o_f)          # the ending never shows the fade or the black
        if keep < n_out:
            n_out = keep if keep >= a.min_len * FPS else 0
            print(f"ending stops before the source's fade to black at {fade / 1000:.2f}s"
                  + ("" if n_out else " (too little real picture left: no ending)"))
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
    # optional per-cut choices (Dub Studio's editor): {"default": "auto"|"none"|style, "cuts": {"<dub ms of the cut>": style}}
    # "auto" = the random rotation below (what happens without a plan), "none" = a hard cut at that switch
    pl = json.load(open(a.plan)) if a.plan else None
    op_ = cfg.get("opening_s", 0) or 0
    def chosen(c):
        if not pl: return "auto"
        ms = (c - op_) * 1000; cuts = pl.get("cuts") or {}
        k = min(cuts, key=lambda x: abs(float(x) - ms), default=None)
        return cuts[k] if k is not None and abs(float(k) - ms) <= 60 else pl.get("default", "auto")
    for s_ in ([v for v in ((pl or {}).get("cuts") or {}).values()] + [(pl or {}).get("default", "auto")]):
        if s_ not in TRANSITION_HALF and s_ not in ("auto", "none"): raise SystemExit(f"unknown transition {s_!r} in --plan")
    rnd = random.Random(a.seed); plan, prev = [], None
    for k, (c, ua, sa, ub, sb) in enumerate(sw):                    # random order, never the same style twice in a row
        ch = chosen(c)
        if ch == "none": continue
        st = rnd.choice([s for s in styles if s != prev] or styles) if ch == "auto" else ch; prev = st
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
        tf = getattr(a, "trim_fast", 0) or 0
        if tf and us_dur > it_dur * tf:                        # --trim-fast X: a block faster than X loses its extra
            keep_target = min(us_dur, it_dur * getattr(a, "trim_to", 1.0))   # source time down to --trim-to (narration first)
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

def source_fade_ms(work, video, tail_s=40.0):
    """Where the source video's closing fade to black starts (ms), or None when it does not end in black.
    Cached in W/source_fade.json. Used so no clip and no ending ever shows the fade or the black after it
    (WBM ep28: the US video fades from 498.2 s and is black 499.0-501.5 s)."""
    import numpy as np
    cp = os.path.join(work, "source_fade.json")
    if os.path.exists(cp):
        c = json.load(open(cp))
        if c.get("video") == video and c.get("size") == os.path.getsize(video):
            return c.get("fade_ms")
    dur = probe_duration_ms(video) / 1000; t0 = max(0.0, dur - tail_s); fps = 10
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-i", video, "-an", "-vf",
                          f"fps={fps},scale=160:-2,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
                          "-f", "null", "-"], capture_output=True, text=True).stdout
    y = np.array([float(v) for v in re.findall(r"YAVG=([0-9.]+)", out)])
    fade = None
    if len(y) > fps * 3 and y[-1] <= 20:
        k = len(y) - 1
        while k > 0 and y[k - 1] <= 20: k -= 1                 # start of the black tail
        black = k
        ref = float(np.median(y[max(0, black - 5 * fps):max(1, black - 2 * fps)])) if black > 2 * fps else 100.0
        while k > 0 and y[k - 1] < 0.92 * ref: k -= 1             # back over the fade
        fade = round((t0 + k / fps) * 1000)
        print(f"source ends in black: fade from {fade / 1000:.2f}s, black from {(t0 + black / fps):.2f}s "
              f"(video {dur:.2f}s): no clip or ending will show it")
    json.dump({"video": video, "size": os.path.getsize(video), "fade_ms": fade}, open(cp, "w"))
    return fade


def clamp_to_fade(segs, fade, margin=100):
    """Clips that reach into the closing fade end before it; a clip wholly inside it moves back before it.
    Speeds are recomputed. -> number of clips changed."""
    if fade is None: return 0
    lim = fade - margin; n = 0
    for s_ in segs:
        if s_.get("video") or s_["us_end_ms"] <= lim: continue
        L = s_["us_end_ms"] - s_["us_start_ms"]
        if s_["us_start_ms"] < lim - 300:
            s_["us_end_ms"] = lim
        else:
            s_["us_end_ms"] = lim; s_["us_start_ms"] = max(0, lim - L)
        s_["stretch_factor"] = round((s_["it_end_ms"] - s_["it_start_ms"]) / max(1, s_["us_end_ms"] - s_["us_start_ms"]), 3)
        s_["note"] = ((s_.get("note") or "") + "; ends before the source's fade to black").lstrip("; ")
        n += 1
    return n


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
            # the source between the two sentences must not be footage another row plays (TGS ep34: rows 14-15 tell the
            # spy beat earlier than the source; a lip group over rows 17-22 ran through that footage -> groups clash)
            if any(j < k or j > m for j, s_ in enumerate(segs)
                   if s_.get("us_start_ms") is not None and min(u0, s_["us_end_ms"]) - max(u1, s_["us_start_ms"]) > 40): return None
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
    nf = clamp_to_fade(out, source_fade_ms(a.work, cfg["src_video"]))
    if nf: print(f"{nf} clip(s) ended before the source's closing fade to black")
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
def cmd_captions(a):
    """Dynamic subtitles (HIH), step 1: read the source video's burned-in captions (tesseract OCR, 4 frames/s, resumable)
    -> W/src_captions.json: every caption card with its time, number of lines and position."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import dynsubs
    cfg = load(a.work, "config.json") if os.path.exists(os.path.join(a.work, "config.json")) else {}
    video = a.video or cfg.get("src_video")
    if not video: raise SystemExit("captions: pass --video (no config.json yet)")
    if not dynsubs.read_captions(a.work, video, fps=a.fps, workers=a.workers, max_seconds=a.max_seconds):
        print("not finished -- run the same captions command again (it resumes)"); sys.exit(3)

def cmd_dynsrt(a):
    """Dynamic subtitles (HIH), step 2: the dub words grouped into cards that fill the strip (2 lines; 3 where the footage
    on screen carries a 3-line source caption), each word at its own time -> the dynamic SRT + W/dynsubs_plan.json."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import dynsubs
    cfg = load(a.work, "config.json")
    for need in ("manifest_groups.json", "src_captions.json"):
        if not os.path.exists(os.path.join(a.work, need)): raise SystemExit(f"dynsrt needs W/{need} (run groups and captions first)")
    W, H, _ = probe_video(cfg["src_video"])
    words = [tuple(w) for w in json.load(open(a.words, encoding="utf-8"))]
    dynsubs.make_plan(a.work, words, a.out, find_font(a.font, a.size), (W, H), size=a.size)
    cfg = load(a.work, "config.json"); cfg["dynamic_srt"] = os.path.abspath(a.out); save(a.work, "config.json", cfg)

def cmd_check(a):
    from PIL import Image, ImageDraw
    cfg = load(a.work, "config.json"); bad = []
    clean = cfg.get("clean_final"); subs = cfg.get("subs_outputs") or {}
    vids = ([("clean", clean)] if clean else []) + [(os.path.basename(v["srt"]), p) for p, v in subs.items()]
    if not clean or not os.path.exists(clean): bad.append("no clean master (run `bookends --out ...`)")
    if len([p for p in subs if os.path.exists(p)]) < a.expect_subs: bad.append(f"only {len(subs)} subtitled videos, expected {a.expect_subs}")
    ref = probe_duration_ms(clean) if clean and os.path.exists(clean) else None
    FPS = out_fps(cfg)
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
    x.add_argument("--clause-long", type=int, help="only sentences of at least L words are split (0 = any); same L as `words`")
    x = sp.add_parser("words"); x.add_argument("--work", required=True); x.add_argument("--side", choices=["src", "dub"], default="src")
    x.add_argument("--srt"); x.add_argument("--media"); x.add_argument("--lang"); x.add_argument("--models")
    x.add_argument("--model", default="small", help="Whisper model (small is enough: the words only give times, the text comes from the SRT)")
    x.add_argument("--first", type=int, help="only the first N sentences (tests)"); x.add_argument("--until", type=float)
    x.add_argument("--max-seconds", type=float, default=1e9); x.add_argument("--out", help="also write a sentence/word workbook")
    x.add_argument("--clauses", type=int, help="split sentences into clauses of at least N words (0 = whole sentences); saved for this work folder")
    x.add_argument("--clause-long", type=int, help="only sentences of at least L words are split (0 = any); saved for this work folder")
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
    x.add_argument("--trim-fast", type=float, default=0,
                   help="a block (not lip-sync) that would play faster than this has its extra source time cut, narration "
                        "lines first, instead of speeding up (0 = off; works with the lip scan's exact-span grouping too)")
    x.add_argument("--trim-to", type=float, default=1.0, help="with --trim-fast: speed a trimmed block is cut down to")
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
    x.add_argument("--box-size", type=int, help="box style: text size in px on the 1080 frame (default 38)")
    x.add_argument("--box-bottom", type=int, help="box style: box distance from the bottom in px (default 71)")
    x.add_argument("--box-alpha", type=float, help="box style: box opacity 0-1 (default 0.30)")
    x.add_argument("--min-gap", type=float, default=1.5, help="pauses shorter than this keep showing the previous line")
    x.add_argument("--max-lead", type=float, default=5.0, help="show the first line from frame 0 (and hold the last to the end) if within this")
    x.add_argument("--no-cover", action="store_true", help="do not add empty strips over source captions outside subtitle times")
    x.add_argument("--chunk", type=float, default=60); x.add_argument("--workers", type=int, default=4)
    x.add_argument("--max-seconds", type=int, default=100000); x.add_argument("--keep-parts", action="store_true")
    x.add_argument("--style", choices=["strip", "box", "dynamic"], default="strip",
                   help="strip = white full-width strip (TDMB); box = white text on a 30%% black rounded box, transparent PNG overlay (every other show); "
                        "dynamic = white strip 2 or 3 caption lines tall, following the source caption under it (HIH; needs captions + dynsrt)")
    x = sp.add_parser("captions", help="read the source's burned-in captions (OCR): W/src_captions.json (dynamic subtitles)")
    x.add_argument("--work", required=True); x.add_argument("--video", help="default: the source video of the run")
    x.add_argument("--fps", type=int, default=4); x.add_argument("--workers", type=int, default=4)
    x.add_argument("--max-seconds", type=int, default=100000)
    x = sp.add_parser("dynsrt", help="dub subtitle cards that fill a strip as tall as the source caption under them")
    x.add_argument("--work", required=True); x.add_argument("--words", required=True, help="the dub's words (mksrt *_words.json)")
    x.add_argument("--out", required=True, help="the dynamic dub SRT"); x.add_argument("--font"); x.add_argument("--size", type=int, default=40)
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
        x.add_argument("--plan", help="JSON of per-cut choices from the editor (default/cuts by dub ms; auto|none|style)")
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
    global CLAUSE_MIN, CLAUSE_LONG
    w = getattr(a, "work", None); sm = os.path.join(w, "sentence_mode.json") if w else None
    if w and getattr(a, "clauses", None) is not None:
        os.makedirs(w, exist_ok=True)
        mode = {"clauses": a.clauses}
        if getattr(a, "clause_long", None):
            mode["long"] = a.clause_long
        json.dump(mode, open(sm, "w"))
    if sm and os.path.exists(sm) and not os.environ.get("DUBSTITCH_CLAUSES"):
        sm_ = json.load(open(sm)); CLAUSE_MIN = int(sm_.get("clauses", 0) or 0)
        if not os.environ.get("DUBSTITCH_CLAUSE_LONG"):
            CLAUSE_LONG = int(sm_.get("long", 0) or 0)
    if CLAUSE_MIN and a.cmd in ("words", "analyze"):
        print(f"sentence mode: clauses (long sentences cut at , ; : and dashes, each part >= {CLAUSE_MIN} words"
              + (f"; only sentences of >= {CLAUSE_LONG} words" if CLAUSE_LONG else "") + ")")
    dict(analyze=cmd_analyze, sheet=cmd_sheet, manifest=cmd_manifest, render=cmd_render, verify=cmd_verify, subs=cmd_subs,
         captions=cmd_captions, dynsrt=cmd_dynsrt, listen=cmd_listen, bookends=cmd_bookends, groups=cmd_groups, translate=cmd_translate, check=cmd_check, speech=cmd_speech, lipsync=cmd_lipsync, words=cmd_words, lipmark=cmd_lipmark, glow=cmd_transitions, transitions=cmd_transitions)[a.cmd](a)

if __name__ == "__main__":
    main()
~~~~~~

## Appendix: `scripts/pngsubs.py`

<!-- FILE: scripts/pngsubs.py md5=27d95158ccf38bbf4b7b2dc7112f537f -->
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

def set_style(size=None, bottom=None, box_alpha=None):
    """Optional house-style changes (Dub Studio's subtitle style per language): text size in px on the 1080 frame
    (shrinks to 79% of it for long cues, as 38 -> 30), distance of the box from the bottom (px), box opacity 0-1.
    Returns a short key for the cue PNG names ('' = the default style, whose PNG names are unchanged)."""
    global START, MIN, BOTTOM, BOX_RGBA
    key = []
    if size and int(size) != START:
        START, MIN = int(size), max(12, round(int(size) * 30 / 38)); key.append(f"s{START}")
    if bottom is not None and int(bottom) != BOTTOM:
        BOTTOM = int(bottom); key.append(f"b{BOTTOM}")
    if box_alpha is not None and round(float(box_alpha) * 255) != BOX_RGBA[3]:
        BOX_RGBA = (0, 0, 0, round(float(box_alpha) * 255)); key.append(f"a{BOX_RGBA[3]}")
    return "_".join(key)

def burn(video, srt, out, work, offset=0.0, chunk=60, max_seconds=140, font=None, preview=None, keep_parts=False,
         size=None, bottom=None, box_alpha=None):
    T0 = time.time(); W = work; os.makedirs(W + "/png", exist_ok=True)
    style = set_style(size, bottom, box_alpha)
    font = font or next(p for p in FONTS if os.path.exists(p))
    dur, fps = probe(video); end = min(dur, preview) if preview else dur
    # 1. cue PNGs (+ a fully transparent blank)
    cues = parse_srt(srt); stats = []
    blank = W + "/png/blank.png"
    if not os.path.exists(blank): Image.new("RGBA", (FRAME, FRAME), (0, 0, 0, 0)).save(blank)
    for i, c in enumerate(cues):
        p = f"{W}/png/cue_{i:04d}_{hashlib.md5((c['text'] + style).encode()).hexdigest()[:8]}.png"; c["png"] = p
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

## Appendix: `scripts/dynsubs.py`

<!-- FILE: scripts/dynsubs.py md5=4521d58a4daf18880bc3c101877bf00d -->
~~~~~~python
"""Dynamic subtitles: the dub subtitle strip follows the source's burned-in caption under it (HIH, owner 2026-10-22).

The source video (HIH: US) has its own captions burned in, usually 2 lines, sometimes 1 or 3. The white strip that
hides them is 2 caption lines tall; where the footage on screen carries a 3-line caption, the strip grows to 3 lines.
The dub subtitles are grouped to FILL that strip: 2 lines of text in a 2-line strip, 3 in a 3-line strip; words that
do not fit become a second card. Every word keeps its own spoken time.

  read_captions  OCR (tesseract) of the source captions -> W/src_captions.json  (dubstitch.py captions)
  make_plan      dub words + edit list + source captions -> dynamic SRT + W/dynsubs_plan.json  (dubstitch.py dynsrt)
  burn           any SRT (dub, or its translation) with the per-frame strip height  (dubstitch.py subs --style dynamic)
"""
import json, math, os, re, subprocess, sys, time, hashlib, io, difflib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

TICK = 0.04                  # the plan is kept per 40 ms of dub time
OCR_W = 720                  # frames are read at this width
GROW = 0.3                   # a 3-line zone grows this much on each side (the OCR samples every 0.25 s)
PAUSE = 1.5                  # a card never spans a silence this long
MAXDUR = {2: 5.0, 3: 7.0}    # longest card (speech time) per strip size
HOLD = 0.5                   # a card stays this long after its last word (never into the next card)
PAD = 18                     # strip margin around the source caption (px on a 1080 frame)
WEAK = set(("il lo la i gli le un uno una di a da in con su per tra fra e o ma che del della dei degli delle al alla ai "
            "agli alle dal dalla nel nella sul sulla non mi ti si ci vi se come quando the a an of to in on at and or but "
            "for with his her my your their is was").split())
_PUNCT_END = re.compile(r"[.!?…]['\"»”)]*$")
_PUNCT_ANY = re.compile(r"[.!?…,;:]['\"»”)]*$")


def _probe(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate",
                          "-of", "csv=p=0", video], capture_output=True, text=True).stdout
    w, h, r = out.strip().split(",")[:3]; a, b = r.split("/")
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                             capture_output=True, text=True).stdout)
    return int(w), int(h), round(int(a) / int(b)), d


# ------------------------------------------------------------------------------------------------------------------
# 1. read the source captions
# ------------------------------------------------------------------------------------------------------------------
def _box(m, kh, kw):
    """sum of a 0/1 image over a kh x kw window centred on each pixel (numpy only)"""
    import numpy as np
    p = np.pad(m.astype(np.int32), ((kh // 2 + 1, kh - kh // 2 - 1), (kw // 2 + 1, kw - kw // 2 - 1)))
    c = p.cumsum(0).cumsum(1)
    return c[kh:, kw:] - c[:-kh, kw:] - c[kh:, :-kw] + c[:-kh, :-kw]


def _mask(c):
    """Keep only wide, tall white text areas (the caption): close 9x41, then open 15x60."""
    import numpy as np
    white = c > 220
    closed = _box(_box(white, 9, 41) > 0, 9, 41) == 9 * 41                 # dilate, then erode
    opened = _box(_box(closed, 15, 60) == 15 * 60, 15, 60) > 0              # erode, then dilate
    return opened


def _tesseract(img, tmp):
    from PIL import Image
    Image.fromarray(img).save(tmp)
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    r = subprocess.run(["tesseract", tmp, "stdout", "--psm", "6", "tsv"], capture_output=True, text=True, env=env)
    out = []
    for line in r.stdout.splitlines()[1:]:
        f = line.split("\t")
        if len(f) == 12 and f[0] == "5":
            out.append(dict(block=f[2], par=f[3], line=f[4], left=int(f[6]), top=int(f[7]), height=int(f[9]),
                            conf=float(f[10]), text=f[11]))
    return out


def _ocr_frame(g, y0, k, tmp):
    """Lines of burned-in caption text in one gray frame (bottom part only): [(top, bottom, text)] in source pixels."""
    import numpy as np
    c = g[y0:]
    box = _mask(c)
    if box.sum() < 200: return [], None
    out = np.full_like(c, 255); out[box] = c[box]
    lines = {}
    for w in _tesseract(out, tmp):
        t = w["text"].strip()
        if t and w["conf"] > 55 and any(ch.isalnum() for ch in t):
            lines.setdefault((w["block"], w["par"], w["line"]), []).append((w["left"], t, w["top"] + y0, w["top"] + w["height"] + y0))
    L = []
    for ws in lines.values():
        ws.sort(); txt = " ".join(x for _, x, _, _ in ws)
        if len(txt) >= 2:
            L.append((min(x[2] for x in ws), max(x[3] for x in ws), txt))
    L.sort()
    # a caption is one stack of evenly spaced lines at the bottom: drop specks of picture texture read as text
    # (HIH ep55 423 s "~y" at row 495, 460 s "Toe" 82 px above the caption)
    hh = g.shape[0]; pitch = 57 * hh / 1080
    if L and L[-1][0] < 0.70 * hh: return [], out
    keep = L[-1:]
    for ln in reversed(L[:-1]):
        gap = keep[0][0] - ln[0]
        if 0.7 * pitch <= gap <= 1.3 * pitch and (ln[1] - ln[0]) <= 1.3 * pitch: keep.insert(0, ln)
        else: break
    return [(round(a * k), round(b * k), t) for a, b, t in keep], out


def _ocr_piece(job):
    video, t0, dur, fps, out, W, H = job
    import numpy as np, tempfile
    w = OCR_W; h = round(H * w / W / 2) * 2; k = W / w; y0 = int(h * 0.45)
    tmp = os.path.join(tempfile.gettempdir(), f"capocr_{os.getpid()}_{int(t0)}.png")
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", video, "-vf",
                          f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    rows, n, last, last_img, ocrs = [], 0, [], None, 0
    while True:
        b = p.stdout.read(w * h)
        if len(b) < w * h: break
        g = np.frombuffer(b, np.uint8).reshape(h, w)
        c = g[y0:]; box = _mask(c)
        if box.sum() < 200:
            L, img = [], None
        else:
            img = np.full_like(c, 255); img[box] = c[box]
            if last_img is not None and np.abs(img.astype(np.int16) - last_img).mean() < 1.5:
                L = last                                   # the same caption as the frame before: no new OCR
            else:
                L, img = _ocr_frame(g, y0, k, tmp); ocrs += 1
        rows.append([round(t0 + n / fps, 3), L]); n += 1
        last, last_img = L, (img.astype(np.int16) if img is not None else None)
    p.wait()
    if os.path.exists(tmp): os.remove(tmp)
    json.dump(rows, open(out + ".tmp", "w")); os.replace(out + ".tmp", out)
    return out


def _cards(rows, step):
    """Frames -> caption cards (same text on consecutive frames); line count = the most common count of the card."""
    key = lambda L: " ".join(t for _, _, t in L).lower()
    cards, cur = [], None
    for t, L in rows:
        k = key(L)
        if cur and k and difflib.SequenceMatcher(None, k, cur["k"]).ratio() > 0.75:
            cur["end"] = t + step; cur["f"].append(L); continue
        if cur and not k and t - cur["end"] < step * 1.2:
            continue                                          # one empty frame inside a card
        if cur: cards.append(cur); cur = None
        if k: cur = {"start": t, "end": t + step, "k": k, "f": [L]}
    if cur: cards.append(cur)
    out = []
    for c in cards:
        if c["end"] - c["start"] < 0.5: continue
        n = Counter(len(L) for L in c["f"]).most_common(1)[0][0]
        fr = [L for L in c["f"] if len(L) == n]
        best = max(fr, key=lambda L: len(key(L)))
        med = lambda xs: sorted(xs)[len(xs) // 2]
        out.append(dict(start=round(c["start"], 3), end=round(c["end"], 3), lines=n, text=[t for _, _, t in best],
                        top=med([L[0][0] for L in fr]), bottom=med([L[-1][1] for L in fr])))
    return out


def read_captions(work, video, fps=4, workers=4, max_seconds=1e9, piece=60.0):
    """OCR (tesseract) of the whole source video in 60-s pieces (cached in W/captions/, resumable). True when finished."""
    import shutil
    for d_ in ("/opt/homebrew/bin", "/usr/local/bin"):                  # the Mac app may start with a short PATH
        if os.path.isdir(d_) and d_ not in os.environ.get("PATH", ""): os.environ["PATH"] += os.pathsep + d_
    if not shutil.which("tesseract"):
        raise SystemExit("captions needs the tesseract OCR program (Mac: `brew install tesseract`)")
    W, H, _, dur = _probe(video)
    d = os.path.join(work, "captions"); os.makedirs(d, exist_ok=True)
    jobs = []
    for i in range(math.ceil(dur / piece)):
        t0 = i * piece; out = os.path.join(d, f"piece_{i:04d}_{fps}.json")
        if not os.path.exists(out): jobs.append((video, t0, min(piece, dur - t0), fps, out, W, H))
    t_stop = time.time() + max_seconds
    piece_s = None                                    # a piece is only started if it can finish within --max-seconds
    with ThreadPoolExecutor(workers) as ex:      # the work is in ffmpeg/tesseract processes and numpy
        while jobs:
            batch = jobs[:workers]; jobs = jobs[workers:]
            t0 = time.time()
            list(ex.map(_ocr_piece, batch))
            piece_s = time.time() - t0
            if jobs and time.time() + piece_s > t_stop: break
    left = sum(not os.path.exists(os.path.join(d, f"piece_{i:04d}_{fps}.json")) for i in range(math.ceil(dur / piece)))
    print(f"caption reading: {math.ceil(dur / piece) - left}/{math.ceil(dur / piece)} pieces of {piece:.0f}s done")
    if left: return False
    rows = []
    for i in range(math.ceil(dur / piece)):
        rows += json.load(open(os.path.join(d, f"piece_{i:04d}_{fps}.json")))
    rows.sort(key=lambda r: r[0])
    cards = _cards(rows, 1 / fps)
    geo = {}
    for n in (1, 2, 3):
        cs = [c for c in cards if c["lines"] == n]
        if cs:
            geo[str(n)] = dict(top=sorted(c["top"] for c in cs)[len(cs) // 2], bottom=sorted(c["bottom"] for c in cs)[len(cs) // 2],
                               cards=len(cs))
    tops2 = [c for c in cards if c["lines"] >= 2]
    secs = Counter()
    for c in cards: secs[c["lines"]] += c["end"] - c["start"]
    json.dump(dict(video=video, size=[W, H], fps=fps, cards=cards, geometry=geo,
                   seconds={str(k): round(v, 1) for k, v in sorted(secs.items())}),
              open(os.path.join(work, "src_captions.json"), "w"), indent=0, ensure_ascii=False)
    print(f"source captions: {len(cards)} cards; seconds on screen by line count: "
          + ", ".join(f"{k} line{'s' if k > 1 else ''} {v:.1f}s" for k, v in sorted(secs.items()))
          + f"; geometry (px, {W}x{H}): " + ", ".join(f"{k} lines rows {g['top']}-{g['bottom']}" for k, g in sorted(geo.items())))
    for c in cards:
        if c["lines"] >= 3: print(f"  {c['lines']}-line caption {c['start']:.2f}-{c['end']:.2f}s: {' / '.join(c['text'])}")
    return True


# ------------------------------------------------------------------------------------------------------------------
# 2. plan: which caption is under each dub moment, and the dub cards that fill the strip
# ------------------------------------------------------------------------------------------------------------------
class Layout:
    def __init__(self, font_path, size, maxw):
        from PIL import ImageFont
        self.font_path, self.size, self.maxw = font_path, size, maxw
        self.f = ImageFont.truetype(font_path, size); self._c = {}

    def width(self, s):
        if s not in self._c: self._c[s] = self.f.getlength(s)
        return self._c[s]

    def lb(self, line):
        w = line.split()[-1]
        if _PUNCT_ANY.search(w): return 0
        return self.size * 6.5 if (w.lower().strip("'’") in WEAK or w.endswith("'") or w.endswith("’")) else self.size * 3

    def wrap_n(self, ws, m):
        """Exactly m lines, balanced, preferring line ends after punctuation; None if it does not fit."""
        if len(ws) < m: return None
        best = None
        def rec(i, left, acc):
            nonlocal best
            if left == 1:
                line = " ".join(ws[i:])
                if self.width(line) <= self.maxw:
                    cand = acc + [line]; wd = [self.width(x) for x in cand]
                    sc = max(wd) - 0.3 * min(wd) + sum(self.lb(x) for x in cand[:-1])
                    if best is None or sc < best[0]: best = (sc, cand)
                return
            for e in range(i + 1, len(ws) - left + 2):
                line = " ".join(ws[i:e])
                if self.width(line) > self.maxw: break
                rec(e, left - 1, acc + [line])
        rec(0, m, [])
        return best[1] if best else None

    def fewest(self, ws, L):
        for m in range(1, L + 1):
            r = self.wrap_n(ws, m)
            if r: return m, r
        return None, None

    def show(self, ws, L):
        """Lines shown in an L-line strip: at least the fewest that fit; with enough words the strip is filled
        (at least 2 words per line), so a 2-line strip never carries one short line."""
        m, r = self.fewest(ws, L)
        if m is None: return None
        want = max(m, min(L, len(ws) // 2))
        return self.wrap_n(ws, want) or r


def _brk(w):
    if _PUNCT_END.search(w): return 0
    if _PUNCT_ANY.search(w): return 2
    return 12 if (w.lower() in WEAK or w.endswith("'") or w.endswith("’")) else 7


def _fmt(x):
    ms = int(round(x * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def caption_lines_at(cards):
    starts = [c["start"] for c in cards]
    import bisect
    def f(u):
        if u is None: return 0
        i = bisect.bisect_right(starts, u) - 1
        return cards[i]["lines"] if i >= 0 and cards[i]["start"] <= u < cards[i]["end"] else 0
    return f


def make_plan(work, words, out_srt, font_path, src_size, size=40):
    """words = [(start_s, end_s, word)] of the dub (mksrt's *_words.json)."""
    G = json.load(open(os.path.join(work, "manifest_groups.json"))); segs = G["segments"]; total = G["total_ms"] / 1000
    C = json.load(open(os.path.join(work, "src_captions.json")))
    lines_at = caption_lines_at(C["cards"])
    W, H = src_size
    lay = Layout(font_path, round(size * H / 1080), W - round(80 * H / 1080))
    seg_i = [0]
    def us_time(t):
        ms = t * 1000
        for s in segs:
            if s["it_start_ms"] <= ms < s["it_end_ms"]:
                r = (ms - s["it_start_ms"]) / max(s["it_end_ms"] - s["it_start_ms"], 1)
                return (s["us_start_ms"] + r * (s["us_end_ms"] - s["us_start_ms"])) / 1000
        return None
    n = int(total / TICK) + 1
    raw = [lines_at(us_time(k * TICK)) for k in range(n)]
    need = [3 if x >= 3 else 2 for x in raw]
    g = round(GROW / TICK)
    three = [k for k in range(n) if need[k] == 3]
    for k in three:
        for j in range(max(0, k - g), min(n, k + g + 1)): need[j] = 3
    k = 0                                                     # a 2-line gap under 1 s between 3-line zones -> 3
    while k < n:
        j = k
        while j < n and need[j] == need[k]: j += 1
        if need[k] == 2 and k > 0 and j < n and (j - k) * TICK < 1.0: need[k:j] = [3] * (j - k)
        k = j
    def need_max(t0, t1):
        a = max(0, int(t0 / TICK)); b = min(n, max(a + 1, int(t1 / TICK) + 1))
        return max(need[a:b]) if a < n else 2
    words = [(a, b, re.sub(r"[«»°*]", "", w).strip()) for a, b, w in words]
    words = [w for w in words if w[2]]
    chunks, cur = [], []
    for w in words:
        if cur and w[0] - cur[-1][1] >= PAUSE: chunks.append(cur); cur = []
        cur.append(w)
    if cur: chunks.append(cur)
    cards = []
    for ch in chunks:
        m = len(ch); best = [1e18] * (m + 1); prev = [0] * (m + 1); info = [None] * (m + 1); best[0] = 0
        for j in range(1, m + 1):
            for i in range(j - 1, -1, -1):
                d = ch[j - 1][1] - ch[i][0]; L = need_max(ch[i][0], ch[j - 1][1])
                if d > MAXDUR[L] and j - i > 1: break
                ws = [w[2] for w in ch[i:j]]
                ly = lay.show(ws, L)
                if ly is None:
                    if j - i > 1: break
                    ly = [ws[0]]
                c = best[i] + 10 + (0 if j == m else _brk(ch[j - 1][2])) + (5 if d < 1.0 else 0) + (L - len(ly)) * 4
                if c < best[j]: best[j], prev[j], info[j] = c, i, (L, ly)
        cuts = []; j = m
        while j > 0: cuts.append((prev[j], j, info[j])); j = prev[j]
        for i, j, (L, ly) in reversed(cuts):
            cards.append(dict(start=round(ch[i][0], 3), end=round(ch[j - 1][1], 3), L=L, lines=ly))
    for i, c in enumerate(cards):                             # hold, never into the next card or a taller strip
        e = c["end"] + HOLD
        if i + 1 < len(cards): e = min(e, cards[i + 1]["start"])
        while e > c["end"] and need_max(c["start"], e) > c["L"]: e -= TICK
        c["end"] = round(max(e, c["start"] + 0.2), 3)
    with open(out_srt, "w", encoding="utf-8") as fh:
        for i, c in enumerate(cards, 1):
            fh.write(f"{i}\n{_fmt(c['start'])} --> {_fmt(c['end'])}\n" + "\n".join(c["lines"]) + "\n\n")
    plan = dict(tick=TICK, need=need, raw=raw, cards=cards, src_size=[W, H], geometry=C["geometry"], font_size=size,
                srt=os.path.abspath(out_srt), settings=dict(grow=GROW, pause=PAUSE, maxdur=MAXDUR, hold=HOLD))
    json.dump(plan, open(os.path.join(work, "dynsubs_plan.json"), "w"))
    zones, k = [], 0
    while k < n:
        j = k
        while j < n and need[j] == need[k]: j += 1
        if need[k] == 3: zones.append((k * TICK, j * TICK))
        k = j
    nw_in = len(words); nw_out = sum(len(" ".join(c["lines"]).split()) for c in cards)
    sec = Counter(raw)
    print(f"source caption under the dub video: " + ", ".join(f"{k} lines {v * TICK:.1f}s" for k, v in sorted(sec.items())))
    print(f"dynamic subtitles {out_srt}: {len(cards)} cards (" + ", ".join(f"{v} on {k} lines" for k, v in
          sorted(Counter(len(c['lines']) for c in cards).items())) + f"); 3-line strip zones: {len(zones)} = "
          f"{sum(b - a for a, b in zones):.1f}s " + " ".join(f"[{a:.1f}-{b:.1f}s]" for a, b in zones))
    durs = sorted(c["end"] - c["start"] for c in cards)
    print(f"card length: median {durs[len(durs) // 2]:.1f}s, longest {durs[-1]:.1f}s; words {nw_in} in, {nw_out} on cards")
    if nw_in != nw_out: raise SystemExit(f"words lost: {nw_in} in, {nw_out} out")
    return plan


# ------------------------------------------------------------------------------------------------------------------
# 3. burn
# ------------------------------------------------------------------------------------------------------------------
def strip_rows(plan, L, Wv, Hv):
    """Strip rows (top, bottom) on the video for an L-line source caption, from the measured caption geometry."""
    Ws, Hs = plan["src_size"]; k = Hv / Hs; geo = plan["geometry"]; pad = round(PAD * Hv / 1080)
    bot = max(g["bottom"] for g in geo.values()) if geo else round(Hs * 0.96)
    if str(L) in geo: top = geo[str(L)]["top"]
    elif "2" in geo:
        pitch = (geo["2"]["bottom"] - geo["2"]["top"]) / 2
        top = geo["2"]["top"] - pitch * (L - 2)
    else: top = Hs * (0.85 - 0.05 * L)
    t, b = round(top * k) - pad, min(Hv - 1, round(bot * k) + pad)
    need = L * round(plan["font_size"] * Hv / 1080 * 1.25) + round(20 * Hv / 1080)
    if b - t < need: t = b - need
    return max(0, t), b


def burn(work, video, srt_cues, out, offset, cfg, font_path, workers=2, max_seconds=1e9, chunk=60.0, min_gap=1.5):
    """srt_cues = [(start_ms, end_ms, text)] of the SRT to burn. Returns (ok, report)."""
    import numpy as np
    from PIL import Image, ImageDraw
    plan = json.load(open(os.path.join(work, "dynsubs_plan.json")))
    Wv, Hv, FPS, dur = _probe(video); n = round(dur * FPS)
    need, raw, tick = plan["need"], plan["raw"], plan["tick"]
    lines_at = caption_lines_at(json.load(open(os.path.join(work, "src_captions.json")))["cards"])
    size = round(plan["font_size"] * Hv / 1080); lh = round(size * 1.25)
    dub_s = len(need) * tick; ending_src = cfg.get("ending_src_s") or 0
    def at(k):                      # (strip lines needed, source caption lines) at video frame k
        t = k / FPS - offset
        if 0 <= t < dub_s:
            j = min(int(t / tick), len(need) - 1); return need[j], raw[j]
        u = k / FPS if t < 0 else ending_src + (t - dub_s)        # opening / ending: the source plays as is
        c = lines_at(u); return (3 if c >= 3 else 2), c
    rows = {L: strip_rows(plan, L, Wv, Hv) for L in (2, 3)}
    # cards: the SRT's own cards; a card's strip = the tallest needed under it, and its text fills that strip
    lays = {}
    def layout(text, L):
        ws = text.split()
        for s in range(size, max(size - 12, 20) - 1, -2):
            if s not in lays: lays[s] = Layout(font_path, s, Wv - round(80 * Hv / 1080))
            ly = lays[s].show(ws, L)
            if ly: return s, ly
        return s, lays[s].wrap_n(ws, L) or [text]
    cards = []
    for a_ms, b_ms, text in srt_cues:
        k0, k1 = math.ceil((a_ms / 1000 + offset) * FPS), math.ceil((b_ms / 1000 + offset) * FPS)
        L = max([at(k)[0] for k in range(k0, max(k0 + 1, k1))] + [2])
        s, ly = layout(text, L)
        if len(ly) > L and L < 3: L = 3; s, ly = layout(text, 3)
        cards.append(dict(k0=k0, k1=k1, L=L, size=s, lines=ly))
    card = np.full(n, -1); lines = np.zeros(n, int)
    for i, c in enumerate(cards): card[max(c["k0"], 0):min(c["k1"], n)] = i
    for i in range(len(cards) - 1):                            # pauses under min_gap keep the previous card
        a_, b_ = cards[i]["k1"], cards[i + 1]["k0"]
        if 0 < b_ - a_ < min_gap * FPS: card[a_:b_] = i
    for k in range(n):
        nd, rw = at(k)
        if card[k] >= 0: lines[k] = max(nd, cards[card[k]]["L"])
        elif rw > 0: lines[k] = nd                             # an empty strip over a source caption
    on = lines > 0; k = 0
    while k < n:                                               # no strip blinking off for under 0.5 s
        if on[k]: k += 1; continue
        j = k
        while j < n and not on[j]: j += 1
        if k > 0 and j < n and j - k < 0.5 * FPS: lines[k:j] = max(lines[k - 1], lines[j])
        k = j
    state = [(int(card[k]) if lines[k] else -1, int(lines[k])) for k in range(n)]
    name = re.sub(r"[^\w.-]", "_", os.path.splitext(os.path.basename(out))[0])
    d = os.path.join(work, "dynsubs_" + name); os.makedirs(d, exist_ok=True)
    from PIL import ImageFont
    fonts = {}
    pngs = {}
    def png(st):
        if st in pngs: return pngs[st]
        ci, L = st; p = os.path.join(d, f"s_{ci}_{L}.png")
        im = Image.new("RGBA", (Wv, Hv), (0, 0, 0, 0))
        if L:
            dr = ImageDraw.Draw(im); y0, y1 = rows[min(L, 3)]; dr.rectangle([0, y0, Wv - 1, y1], fill=(255, 255, 255, 255))
            if ci >= 0:
                c = cards[ci]; f = fonts.setdefault(c["size"], ImageFont.truetype(font_path, c["size"]))
                lh_ = round(c["size"] * 1.25); cy = (y0 + y1) / 2 - (len(c["lines"]) - 1) * lh_ / 2
                for i, l in enumerate(c["lines"]): dr.text((Wv / 2, cy + i * lh_), l, font=f, fill=(0, 0, 0, 255), anchor="mm")
        im.save(p); pngs[st] = p; return p
    for st in set(state): png(st)
    cf = round(chunk * FPS); chunks = [(i * cf, min((i + 1) * cf, n)) for i in range(math.ceil(n / cf))]
    st_v = os.stat(video)
    sig = hashlib.md5(json.dumps([state, [c["lines"] for c in cards], rows, st_v.st_size, int(st_v.st_mtime)]).encode()).hexdigest()
    t_stop = time.time() + max_seconds
    def job(i):
        f0, f1 = chunks[i]; o = os.path.join(d, f"part{i:03d}.mp4"); s = f"{sig}|{f0}|{f1}"
        if (os.path.exists(o + ".ok") and open(o + ".ok").read() == s) or time.time() > t_stop: return
        items, x = [], f0
        while x < f1:
            y = x
            while y < f1 and state[y] == state[x]: y += 1
            items.append((png(state[x]), (y - x) / FPS)); x = y
        with open(o + ".txt", "w") as fh:
            fh.write("ffconcat version 1.0\n")
            for p, du in items: fh.write(f"file '{os.path.abspath(p)}'\nduration {du:.6f}\n")
            fh.write(f"file '{os.path.abspath(items[-1][0])}'\n")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{f0 / FPS:.6f}", "-i", video, "-f", "concat", "-safe", "0",
                            "-i", o + ".txt", "-filter_complex", f"[1:v]fps={FPS}[o];[0:v][o]overlay=0:0:eof_action=repeat,format=yuv420p[v]",
                            "-map", "[v]", "-frames:v", str(f1 - f0), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "18", "-an", o], capture_output=True, text=True)
        if r.returncode: print(f"FAILED chunk {i}: {r.stderr[-300:]}")
        else: open(o + ".ok", "w").write(s)
    with ThreadPoolExecutor(workers) as ex: list(ex.map(job, range(len(chunks))))
    ok = sum(os.path.exists(os.path.join(d, f"part{i:03d}.mp4.ok")) and
             open(os.path.join(d, f"part{i:03d}.mp4.ok")).read() == f"{sig}|{c[0]}|{c[1]}" for i, c in enumerate(chunks))
    print(f"subtitled {ok}/{len(chunks)} chunks")
    if ok < len(chunks): return False, None
    lst = os.path.join(d, "parts.txt"); silent = os.path.join(d, "silent.mp4")
    open(lst, "w").write("".join(f"file '{os.path.abspath(os.path.join(d, f'part{i:03d}.mp4'))}'\n" for i in range(len(chunks))))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent], check=True)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", silent, "-i", video, "-map", "0:v", "-map", "1:a", "-c", "copy",
                    "-movflags", "+faststart", out], check=True)
    # ---- self-check on the saved file: strip edges white where planned; the 3-line rows only where planned ----
    def band(path, y0, y1):
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"crop={Wv}:{y1 - y0}:0:{y0},scale=108:4,format=gray",
                            "-f", "rawvideo", "-"], capture_output=True).stdout
        return np.frombuffer(r, np.uint8).reshape(-1, 4, 108).astype(np.int16)
    t2, b2 = rows[2]; t3 = rows[3][0]
    A2 = band(out, t2 + 2, t2 + 10); m = min(len(A2), n)
    edge2 = np.concatenate([A2[:m, :, :4], A2[:m, :, -4:]], axis=2).reshape(m, -1).min(1) >= 240
    plan2 = np.array([s[1] >= 2 for s in state[:m]])
    A3 = band(out, t3 + 2, t3 + 10)
    edge3 = np.concatenate([A3[:m, :, :4], A3[:m, :, -4:]], axis=2).reshape(m, -1).min(1) >= 240
    plan3 = np.array([s[1] >= 3 for s in state[:m]])
    trans = np.zeros(m, bool)
    for x in np.where(np.diff([s[1] for s in state[:m]]) != 0)[0]: trans[max(x - 1, 0):x + 3] = True
    miss = int((plan2 & ~edge2 & ~trans).sum()); miss3 = int((plan3 & ~edge3 & ~trans).sum())
    vd, sd = _probe(out)[3], dur
    rep = dict(cards=len(cards), on_lines=dict(Counter(len(c["lines"]) for c in cards)), three_strip_cards=sum(c["L"] == 3 for c in cards),
               frames_2=int(sum(s[1] == 2 for s in state)), frames_3=int(sum(s[1] == 3 for s in state)),
               strip_missing=miss, strip3_missing=miss3, length_diff_ms=round((vd - sd) * 1000),
               font_reduced=sum(c["size"] < size for c in cards))
    print(f"saved {out}: {vd:.3f}s (clean {sd:.3f}s); {len(cards)} cards (" + ", ".join(f"{v} on {k} lines" for k, v in
          sorted(rep['on_lines'].items())) + f"), {rep['three_strip_cards']} in a 3-line strip, {rep['font_reduced']} with a smaller font; "
          f"strip rows 2-line {rows[2][0]}-{rows[2][1]}, 3-line {rows[3][0]}-{rows[3][1]}")
    print(f"self-check: 2-line strip missing on {miss} planned frames, 3-line strip missing on {miss3}")
    # QC image: a 3-line strip card (if any), a 2-line one, the first card
    picks = [c for c in cards if c["L"] == 3][:2] + [c for c in cards if c["L"] == 2][1:3]
    qc = Image.new("RGB", (max(1, len(picks)) * 540, 540), "white")
    for i, c in enumerate(picks):
        t = (c["k0"] + c["k1"]) / 2 / FPS
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", out, "-frames:v", "1", "-vf", "scale=540:540",
                            "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        if p: qc.paste(Image.open(io.BytesIO(p)).convert("RGB"), (i * 540, 0))
    qp = os.path.join(work, f"subs_qc_{name}.jpg"); qc.save(qp, quality=85)
    print(f"subtitle QC image (3-line strip cards first, then 2-line): {qp} -- LOOK at it")
    for i in range(len(chunks)):
        for ext in ("", ".ok", ".txt"):
            q = os.path.join(d, f"part{i:03d}.mp4{ext}")
            if os.path.exists(q): os.remove(q)
    os.remove(silent)
    good = miss == 0 and miss3 == 0 and abs(vd - sd) * 1000 <= 1000 / FPS + 10
    return good, rep
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

<!-- FILE: scripts/lipfast.py md5=0c8355ddebc839c44fd8de98c3c0f8ff -->
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
                                   talking_s=round(float((ss >= thr).sum()) / FPS, 2), mouth_s=round(float((vv >= 0).sum()) / FPS, 2),
                                   # per face, frame by frame (owner 2026-10-29, face map): WHICH face talks, not only that one does
                                   f0=round(t0 + f0 / FPS, 3), score=[round(float(v), 2) for v in ss], visual=[round(float(v), 2) for v in vv],
                                   boxes=[[int(round(float(v))) for v in b] for b in tr["bbox"][:len(ss)]]))
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

<!-- FILE: scripts/lipgpu.py md5=1301d58e7cd4da8870901f8e783daa1b -->
~~~~~~python
#!/usr/bin/env python3
"""
lipgpu.py -- the heavy half of the Step 4B lip scan (S3FD face finder + TalkNet-ASD) on the fastest device of THIS
computer, with limits so the computer stays usable. Same logic and output as `lipdetect.py --range` for these two
measures; the third measure (Face Mesh lip opening) is done by lipmesh.py and the halves are joined by lipmerge.py.

  python3 lipgpu.py --video SRC.mp4 --out-dir W/lips_nets [--ranges ranges.json | --piece 15]
                    [--talknet DIR] [--device auto|mps|cuda|cpu] [--threads N] [--min-free-gb G] [--max-seconds S]
                    [--engine auto|torch|onnx] [--provider auto|dml|openvino-gpu|openvino-cpu|cpu] [--onnx-dir DIR]

Device (auto): NVIDIA GPU (cuda) > Apple GPU (mps, Apple-silicon Macs) > CPU.
Engine (auto): on WINDOWS without an NVIDIA GPU the two networks run on ONNX Runtime (DirectML on any DirectX 12 GPU, else
OpenVINO, else CPU) -- same weights, same settings, FP32; everything else (frames, boxes, NMS, tracks) is unchanged.
Needs the two .onnx files from export_lip_onnx.py (once). Mac (MPS) and NVIDIA (CUDA) keep PyTorch.
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


def build_head_class():
    """S3FD forward pass up to the raw loc/conf maps (the network part); used by main() and by export_lip_onnx.py."""
    import torch, torch.nn.functional as F_

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
    return Head


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", required=True); p.add_argument("--out-dir", required=True)
    p.add_argument("--ranges", help="JSON [[start, end], ...] in seconds (default: --piece-second pieces over the whole video)")
    p.add_argument("--piece", type=float, default=15, help="piece length in seconds when --ranges is not given (default 15)")
    p.add_argument("--talknet", default=os.path.join(HERE, "..", "third_party", "TalkNet-ASD"), help="TalkNet-ASD folder with its two weight files")
    p.add_argument("--device", default="auto", choices=["auto", "mps", "cuda", "cpu"])
    p.add_argument("--engine", default="auto", choices=["auto", "torch", "onnx"],
                   help="auto = ONNX Runtime on Windows without an NVIDIA GPU, else PyTorch (Mac: MPS, NVIDIA: CUDA)")
    p.add_argument("--provider", default="auto", choices=["auto", "dml", "openvino-gpu", "openvino-cpu", "cpu"],
                   help="ONNX Runtime device: dml = DirectML (any DirectX 12 GPU: Intel, AMD, NVIDIA), openvino = Intel")
    p.add_argument("--onnx-dir", default=os.path.join(HERE, "..", "data", "models_onnx"), help="s3fd_head.onnx + talknet.onnx (export_lip_onnx.py)")
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

    Head = build_head_class()
    eng = a.engine
    if eng == "auto":
        eng = "onnx" if (os.name == "nt" and dev_name == "cpu") else "torch"
        if eng == "onnx":
            try:
                import onnxruntime  # noqa: F401
            except ImportError:
                log("Windows without an NVIDIA GPU: install onnxruntime-directml (Step 4B.6) for the fast scan; using PyTorch on the CPU now")
                eng = "torch"
    S3 = TN = None
    if eng == "onnx":
        import onnxruntime as ort
        avail = ort.get_available_providers(); prov_name = a.provider
        if prov_name == "auto":
            prov_name = "dml" if "DmlExecutionProvider" in avail else ("openvino-gpu" if "OpenVINOExecutionProvider" in avail else "cpu")
        so = ort.SessionOptions(); so.intra_op_num_threads = threads
        if prov_name == "dml":
            so.enable_mem_pattern = False; so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL   # required by DirectML
            prov = [("DmlExecutionProvider", {"device_id": 0}), "CPUExecutionProvider"]
        elif prov_name.startswith("openvino"):
            prov = [("OpenVINOExecutionProvider", {"device_type": "GPU" if prov_name == "openvino-gpu" else "CPU", "precision": "FP32"}),
                    "CPUExecutionProvider"]
        else:
            prov = ["CPUExecutionProvider"]
        od = os.path.abspath(a.onnx_dir)
        if not os.path.exists(os.path.join(od, "s3fd_head.onnx")):
            sys.exit(f"ONNX models not found in {od}: run `python export_lip_onnx.py` once (Step 4B.6)")
        S3 = ort.InferenceSession(os.path.join(od, "s3fd_head.onnx"), so, providers=prov)
        TN = ort.InferenceSession(os.path.join(od, "talknet.onnx"), so, providers=prov)
        dev_name, dev = "cpu", torch.device("cpu")                    # PyTorch only for setup, boxes and NMS
        log(f"ONNX engine {prov_name}: S3FD on {S3.get_providers()[0]}, TalkNet on {TN.get_providers()[0]} (full precision FP32)")
    else:
        prov_name = None
    hd = Head(det).eval().to(dev); model.to(dev); head.to(dev); head.vhead.to(dev)

    def detect_one(img, conf=0.9, scale=0.5):
        h, w = img.shape[:2]
        x = cv2.resize(img, (0, 0), fx=scale, fy=scale).astype("float32").transpose(2, 0, 1)
        x = x[[2, 1, 0], :, :] - np.array([104., 117., 123.], "float32")[:, None, None]
        if S3 is not None:
            outs = tuple(torch.from_numpy(o) for o in S3.run(None, {"img": np.ascontiguousarray(x[None])}))
        else:
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
                    if TN is not None:
                        a_np = np.asarray(mf[i * du * 100:(i + 1) * du * 100], np.float32)[None]
                        v_np = np.asarray(vf[i * du * FPS:(i + 1) * du * FPS], np.float32)[None]
                        if a_np.shape[1] < 4 or v_np.shape[1] < 1: continue
                        av, vis = TN.run(None, {"A": a_np, "V": v_np})
                        sc.extend(av.tolist()); sv.extend(vis.tolist())
                        continue
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
            res["method"] = dict(det="s3fd", every=2, device=dev_name, runner="lipgpu.py", mesh=False, engine=eng, provider=prov_name)
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
    json.dump(dict(device=dev_name, engine=eng, provider=prov_name, threads=threads, video_s=total_video, scanned_now_s=done_video, wall_s=round(wall, 1),
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

## Appendix: `scripts/export_lip_onnx.py`

<!-- FILE: scripts/export_lip_onnx.py md5=a7e2d13177539ff4aed4d5f5f94f1eec -->
~~~~~~python
#!/usr/bin/env python3
"""export_lip_onnx.py -- export the two lip-scan networks to ONNX, once (Step 4B.6, for Windows without an NVIDIA GPU).

  python export_lip_onnx.py [--talknet DIR] [--out DIR]          (default out: ../data/models_onnx)

Writes s3fd_head.onnx (the S3FD forward pass of lipgpu.py, up to the raw loc/conf maps; boxes and NMS stay in Python) and
talknet.onnx (TalkNet: audio MFCC + face crops -> audio-visual score and visual score per frame, exactly what lipgpu.py
collects). Same trained weights, FP32. Then checks both files against PyTorch at several input sizes and prints the
largest difference (must be < 1e-3); lipgpu.py --engine onnx uses them (any computer; auto on Windows without NVIDIA).
Needs: torch 2.5+ (CPU build is fine), onnx, onnxscript, onnxruntime (any flavour)."""
import argparse, os, sys
import numpy as np
np.int = int
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--talknet", default=os.path.join(HERE, "..", "third_party", "TalkNet-ASD"))
    p.add_argument("--out", default=os.path.join(HERE, "..", "data", "models_onnx"))
    a = p.parse_args()
    import torch
    import lipdetect as L
    from lipgpu import build_head_class
    os.makedirs(a.out, exist_ok=True)
    tn = os.path.abspath(a.talknet); cwd = os.getcwd(); os.chdir(tn); model, head, det = L.load_talknet(tn); os.chdir(cwd)
    torch.set_num_threads(max(1, (os.cpu_count() or 4) - 2))

    # S3FD: image (1, 3, H, W) = BGR minus the mean, as lipgpu.detect_one builds it -> 6 loc + 6 conf maps
    hd = build_head_class()(det).eval()
    outs = [f"loc{i}" for i in range(6)] + [f"conf{i}" for i in range(6)]
    dyn = {"img": {2: "h", 3: "w"}}; dyn.update({n: {1: f"{n}_h", 2: f"{n}_w"} for n in outs})
    p_s3 = os.path.join(a.out, "s3fd_head.onnx")
    torch.onnx.export(hd, torch.randn(1, 3, 270, 480), p_s3, input_names=["img"], output_names=outs, dynamic_axes=dyn,
                      opset_version=17, do_constant_folding=True, dynamo=False)

    # TalkNet: audio MFCC (1, Ta, 13) + face crops (1, Tv, 112, 112), Ta = 4 Tv -> AV score, visual score per frame
    class TalkNetONNX(torch.nn.Module):
        def __init__(s, m, h): super().__init__(); s.m, s.h = m, h
        def forward(s, A, V):
            eA = s.m.forward_audio_frontend(A); eV = s.m.forward_visual_frontend(V)
            eA, eV = s.m.forward_cross_attention(eA, eV)
            # the backends already return (frames, 256) / (frames, 128): lossAV's x.squeeze(1) is a no-op there and is left
            # out (in ONNX it becomes a conditional node that breaks shape inference)
            av = s.h.FC(s.m.forward_audio_visual_backend(eA, eV))               # = lossAV.forward(labels=None) before numpy
            ov = s.h.vhead.FC(s.m.forward_visual_backend(eV))
            return av[:, 1], ov[:, 1] - ov[:, 0]
    tw = TalkNetONNX(model, head).eval()
    p_tn = os.path.join(a.out, "talknet.onnx")
    # the newer exporter (dynamo) is needed here: the old one freezes the frame count inside the attention layers
    from torch.export import Dim
    tv = Dim("tv", min=2, max=1000)
    torch.onnx.export(tw, (torch.randn(1, 200, 13), torch.rand(1, 50, 112, 112) * 255), p_tn, input_names=["A", "V"],
                      output_names=["av", "vis"], dynamic_shapes={"A": {1: 4 * tv}, "V": {1: tv}}, dynamo=True, opset_version=18)

    # check against PyTorch at the sizes the scan uses (chunks of 1, 2, 4 s + a short last chunk; two frame shapes)
    import onnxruntime as ort
    s3 = ort.InferenceSession(p_s3, providers=["CPUExecutionProvider"]); tk = ort.InferenceSession(p_tn, providers=["CPUExecutionProvider"])
    worst = 0.0
    for h, w in ((270, 480), (480, 270), (270, 270)):
        x = torch.randn(1, 3, h, w) * 50
        with torch.no_grad(): ref = [t.numpy() for t in hd(x)]
        got = s3.run(None, {"img": x.numpy()})
        d = max(float(np.abs(r - g).max()) for r, g in zip(ref, got)); worst = max(worst, d); print(f"S3FD {h}x{w}: max diff {d:.2e}")
    for tv in (25, 50, 100, 13, 1):
        A = torch.randn(1, 4 * tv, 13); V = torch.rand(1, tv, 112, 112) * 255
        with torch.no_grad(): ref = [t.numpy() for t in tw(A, V)]
        got = tk.run(None, {"A": A.numpy(), "V": V.numpy()})
        d = max(float(np.abs(r - g).max()) for r, g in zip(ref, got)); worst = max(worst, d); print(f"TalkNet {tv} frames: max diff {d:.2e}")
    print(f"saved {p_s3} and {p_tn}; largest difference {worst:.2e} -> " + ("OK" if worst < 1e-3 else "TOO LARGE: do not use ONNX"))
    sys.exit(0 if worst < 1e-3 else 1)


if __name__ == "__main__":
    main()
~~~~~~

## Appendix: `scripts/compare_lips.py`

<!-- FILE: scripts/compare_lips.py md5=5b89cc31e23a778b2c18a50991b8e4f4 -->
~~~~~~python
#!/usr/bin/env python3
"""compare_lips.py <lips_nets A> <lips_nets B> -- proof that two lip scans give the same result (Step 4B.6):
every piece in B (range_<s>_<e>.json) against the same piece in A: tracks, face boxes, per-frame talking/visual scores and
the talking intervals. OK = same tracks, boxes within 2 px, every talking-interval edge within one frame (0.04 s) and the
scores within 0.5. Score differences of 0.1-0.35 are normal between an Apple GPU (MPS) scan and any CPU scan (plain PyTorch
too, TGS ep32) and change nothing; ONNX vs PyTorch on the same device differ by about 1e-5."""
import json, os, sys
import numpy as np


def main():
    A, B = sys.argv[1], sys.argv[2]; bad = n = 0
    for f in sorted(x for x in os.listdir(B) if x.startswith("range_") and x.endswith(".json")):
        if not os.path.exists(os.path.join(A, f)): continue
        a, b = json.load(open(os.path.join(A, f))), json.load(open(os.path.join(B, f))); n += 1
        ds = np.abs(np.array(a["scores"], float) - np.array(b["scores"], float)) if a["scores"] else np.zeros(1)
        dv = np.abs(np.array(a["visual"], float) - np.array(b["visual"], float)) if a["visual"] else np.zeros(1)
        same_tracks = len(a["tracks"]) == len(b["tracks"]) and a["faces_frames"] == b["faces_frames"]
        box = max((max(abs(p - q) for p, q in zip(ta["box"], tb["box"])) for ta, tb in zip(a["tracks"], b["tracks"])), default=0)
        edges = [abs(p - q) for ia, ib in zip(a["intervals"], b["intervals"]) for p, q in zip(ia, ib)]
        same_iv = len(a["intervals"]) == len(b["intervals"]) and max(edges, default=0) <= 0.04
        ok = same_tracks and box <= 2 and ds.max() < 0.5 and dv.max() < 0.5 and same_iv
        bad += not ok
        print(f"{f}: tracks {'same' if same_tracks else 'DIFFERENT'}, box {box:.1f} px, score diff {ds.max():.4f}, visual diff "
              f"{dv.max():.4f}, intervals {'same' if same_iv else 'DIFFERENT'} -> {'OK' if ok else 'CHECK'}")
    print(f"{n} pieces compared: " + ("ALL PIECES MATCH" if not bad else f"{bad} pieces to look at"))
    sys.exit(1 if bad or not n else 0)


if __name__ == "__main__":
    main()
~~~~~~

## Appendix: `scripts/mksrt.py`

<!-- FILE: scripts/mksrt.py md5=758820543686e47a08eb1eb7c2977f19 -->
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
        import dubstitch as D
        tag = re.sub(r"[^A-Za-z0-9.-]", "_", a.model)
        cache = os.path.join(D.asr_dir(a.work, a.media), f"{a.lang}_mlx_{tag}.json")
        if not os.path.exists(cache):
            import mlx_whisper
            r = mlx_whisper.transcribe(a.media, path_or_hf_repo=a.model, language=a.lang, word_timestamps=True,
                                       condition_on_previous_text=False, verbose=False)
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            json.dump([[w["start"], w["end"], w["word"].strip()] for s in r["segments"] for w in s.get("words", [])], open(cache, "w"), ensure_ascii=False)
        return [tuple(w) for w in json.load(open(cache)) if w[2].strip()]
    import dubstitch as D
    models = a.models or os.path.join(HERE, "..", "data", "models")
    words, todo = D.transcribe_words(a.media, D.asr_dir(a.work, a.media), a.lang, models, 0.0, media_length(a.media),
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
        fp = os.path.join(D.asr_dir(a.work, a.media), f"{a.lang}_{a.model}_mksrt_redo_{w0:09.2f}_{w1:09.2f}.json")
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


SENT_END = re.compile(r"[.!?\u2026]+[»”“’›\"')\]]*$")   # German closes quotes with “ (TGS ep34 "abkaufe?“ Auf der ...")


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


# words of titles in the localisation sheet ("The Moon Goddess", "il Re Alfa") that are not names: without this list
# "The" + genitive "s" turned the heard word "These" into "Thes" (WBM ep9)
NOT_NAMES = {"the", "and", "for", "von", "van", "der", "die", "das", "den", "des", "del", "della", "dei", "degli", "delle",
             "dal", "dalla", "una", "uno", "les", "the", "mrs", "miss", "king", "queen", "prince", "princess", "lord",
             "lady", "alpha", "student", "teacher", "girl", "boy", "man", "woman", "mother", "father", "il", "la", "lo"}


def load_names(path, cols):
    names = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            for c in cols:
                for n in re.split(r"[\s/,]+", (row.get(c) or "").strip()):
                    if len(n) >= 3 and n.isalpha() and n.lower() not in NOT_NAMES: names.add(n[0].upper() + n[1:].lower())
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
    # every word with its own time (dynamic subtitles, `dubstitch.py dynsrt --words`)
    json.dump([list(w) for w in final], open(base + "_words.json", "w", encoding="utf-8"), ensure_ascii=False)
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

## Appendix: `scripts/run_episode.sh`

<!-- FILE: scripts/run_episode.sh md5=90bccc10206aaf185b747150f7515ef3 -->
~~~~~~bash
#!/bin/bash
# run_episode.sh -- one episode, EVERY step, in order (skill v20 run rule), each step timed with the AI gateway spend before
# and after it (W/run_times.tsv; at the end the table OUT/<show>_ep<N>_run_times.md for the owner). Resumable: a step with a W/.done_<name> marker is skipped; it stops at the first failure.
# To redo from a step: move away W/.done_<step> and the markers after it. Works on macOS, Linux, Windows (Git Bash / WSL).
#
#   SHOW=TGS N=32 LABELS=DE,US DL=de SL=en STYLE=strip \
#   DUB_AUDIO=".../Episode 32.mp3" DUB_SCRIPT=".../Episode 32 cleaned.docx" SRC_VIDEO=".../TGS EPISODE 32.mp4" \
#   W=".../work_ep32" OUT=".../output/ep32" bash run_episode.sh
#
# Optional: PY (python with the pipeline packages), ROOT (the adaptation-tool folder), FONT (Poppins-Bold.ttf when it is
# not installed), SRC_SCRIPT (source script), PAR (Claude calls at a time, default 8), LIP_ENGINE (auto|torch|onnx),
# AIP (folder holding dubstudio/, default ROOT/ai), FACEPY (python of the face environment, default PY), STOP_AFTER (stop after this step, e.g. library for a test).
set -u
ROOT=${ROOT:-$(cd "$(dirname "$0")/.." && pwd)}; S="$ROOT/scripts"; AIP=${AIP:-"$ROOT/ai"}; PL="$AIP/dubstudio/pipeline"; M="$ROOT/data/models"
PY=${PY:-python3}; FACEPY=${FACEPY:-$PY}; PAR=${PAR:-8}; STYLE=${STYLE:-box}; LIP_ENGINE=${LIP_ENGINE:-auto}; FONT=${FONT:-}
D=${LABELS%%,*}; P="$OUT/${SHOW}_EP${N}_${D}"; show=$(echo "$SHOW" | tr A-Z a-z); dl=$(echo "$D" | tr A-Z a-z); sl2=$(echo "${LABELS##*,}" | tr A-Z a-z)
SW="$W/scene"; LIB="$W/broll_library"; LOG="$W/run_times.tsv"; export PYTHONUNBUFFERED=1
FONTARG=(); [ -n "$FONT" ] && FONTARG=(--font "$FONT")
mkdir -p "$W" "$OUT" "$SW"
spend() { (cd "$AIP" && "$PY" -c "from dubstudio import ai; print(ai.key_info()['spend'])" 2>/dev/null) || echo NA; }
step() {
  local name=$1; shift
  if [ -e "$W/.done_$name" ]; then
    echo "== $name: done before, skipped"
    [ "$name" = "${STOP_AFTER:-}" ] && { echo "stopped after $name (STOP_AFTER)"; exit 0; }
    return 0
  fi
  echo "== $name  $(date +%H:%M:%S)"; local s0; s0=$(spend); local t0; t0=$(date +%s)
  "$@"; local rc=$?
  local t1; t1=$(date +%s); local s1; s1=$(spend)
  printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$name" "$((t1-t0))" "$s0" "$s1" "$rc" "$(date +%H:%M:%S)" >> "$LOG"
  echo "== $name finished in $((t1-t0)) s (exit $rc), gateway spend $s0 -> $s1"
  [ $rc = 0 ] && touch "$W/.done_$name" || { echo "STOPPED at $name -- fix it (or ask the owner), then run again"; exit $rc; }
  [ "$name" = "${STOP_AFTER:-}" ] && { echo "stopped after $name (STOP_AFTER)"; exit 0; }
}
need_ai() { (cd "$AIP" && "$PY" -c "from dubstudio import ai; ai.key_info()" >/dev/null 2>&1) || { echo "AI gateway not reachable (VPN? $ROOT/.env?) -- STOP and ask the owner"; exit 2; }; }
cd "$S"
SCRIPTARG=(); [ -n "${DUB_SCRIPT:-}" ] && SCRIPTARG=(--script "$DUB_SCRIPT")
SSCRIPTARG=(); [ -n "${SRC_SCRIPT:-}" ] && SSCRIPTARG=(--script "$SRC_SCRIPT")
step srt_dub     "$PY" mksrt.py --media "$DUB_AUDIO" --lang "$DL" ${SCRIPTARG[@]+"${SCRIPTARG[@]}"} --work "$W" --models "$M" --out "$W/${SHOW}_EP${N}_${D}.srt" --row-words 7 --row-seconds 4
step srt_src     "$PY" mksrt.py --media "$SRC_VIDEO" --lang "$SL" ${SSCRIPTARG[@]+"${SSCRIPTARG[@]}"} --work "$W" --models "$M" --out "$W/${SHOW}_EP${N}_${LABELS##*,}.srt" --row-words 7 --row-seconds 4
step lip_nets    "$PY" lipgpu.py --video "$SRC_VIDEO" --out-dir "$W/lips_nets" --engine "$LIP_ENGINE"
step lip_mesh    "$PY" lipmesh.py --video "$SRC_VIDEO" --out-dir "$W/lips_mesh" --ranges "$W/lips_nets/ranges.json"
step lip_merge   "$PY" lipmerge.py --nets "$W/lips_nets" --mesh "$W/lips_mesh" --out "$W/lips_range"
DUB_SRT="$W/${SHOW}_EP${N}_${D}_sentences.srt"; SRC_SRT="$W/${SHOW}_EP${N}_${LABELS##*,}_sentences.srt"
step words_src   "$PY" dubstitch.py words --work "$W" --side src --srt "$SRC_SRT" --media "$SRC_VIDEO" --lang "$SL" --models "$M" --clauses 0
step words_dub   "$PY" dubstitch.py words --work "$W" --side dub --srt "$DUB_SRT" --media "$DUB_AUDIO" --lang "$DL" --models "$M" --clauses 0
step analyze     "$PY" dubstitch.py analyze --work "$W" --dub-srt "$DUB_SRT" --dub-audio "$DUB_AUDIO" --src-srt "$SRC_SRT" --src-video "$SRC_VIDEO" --models "$M" --clauses 0
need_ai
step ai_match    bash -c "cd '$AIP' && '$PY' -c \"
import json
from dubstudio.pipeline import ai_match, decisions as dec
W = r'''$W'''
check = lambda spec: dec.check_spec(W, spec, r'''$PY''', r'''$S''', '$LABELS')
r = ai_match.auto_match(W, check, names_csv=None)
open(W + '/spec.py', 'w').write(r['spec'])
print(json.dumps({k: r[k] for k in ('rows', 'not_found', 'joined', 'clashes', 'counts', 'usage')}, default=str))\""
step sheet       "$PY" dubstitch.py sheet --work "$W" --out "$OUT/${show}_ep${N}_${dl}_${sl2}_mapping.xlsx" --labels "$LABELS"
step manifest    "$PY" dubstitch.py manifest --work "$W"
step lipsync     "$PY" dubstitch.py lipsync --work "$W"
step groups      "$PY" dubstitch.py groups --work "$W" --size 7 --max-drift 4000
step library     "$PY" "$PL/library.py" "$W" "$LIB"
# face map (2026-10-29): who is on screen / where / face or back / who talks when -- InsightFace + YOLO + the lip scan's
# per-face scores; FACEPY = the face environment (venv_face; on Windows the same packages with onnxruntime-directml)
step facemap     "$FACEPY" facemap.py --video "$SRC_VIDEO" --library "$LIB/broll_library.json" --lips "$W/lips_nets" --app "$AIP"
need_ai
TOT=$("$PY" -c "import json;print(json.load(open(r'''$W/manifest_groups.json'''))['total_ms']/1000)")
[ -e "$W/manifest_groups.auto.json" ] && TOT=$("$PY" -c "import json;print(json.load(open(r'''$W/manifest_groups.auto.json'''))['total_ms']/1000)")
step scene_edit  bash -c "cp '$W/sentences_dub.json' '$W/sentences_src.json' '$W/lipsync.json' '$SW/' && \
  if [ -e '$W/manifest_groups.auto.json' ]; then cp '$W/manifest_groups.auto.json' '$SW/manifest_groups.json'; else cp '$W/manifest_groups.json' '$SW/'; fi && \
  '$PY' '$PL/sceneedit.py' '$SW' '$LIB' 0 $TOT '$SW/scene_manifest.json' --parallel $PAR"
step use_scene   "$PY" - "$W" <<'PYEOF'
import json, os, shutil, sys
W = sys.argv[1]
if not os.path.exists(f"{W}/manifest_groups.auto.json"): shutil.copy(f"{W}/manifest_groups.json", f"{W}/manifest_groups.auto.json")
a = json.load(open(f"{W}/manifest_groups.auto.json")); s = json.load(open(f"{W}/scene/scene_manifest.json"))
s["total_ms"] = a["total_ms"]; L = s["segments"][-1]
if L["it_end_ms"] < a["total_ms"] - 1:
    k = (a["total_ms"] - L["it_start_ms"]) / (L["it_end_ms"] - L["it_start_ms"])
    L["us_end_ms"] = L["us_start_ms"] + (L["us_end_ms"] - L["us_start_ms"]) * k; L["it_end_ms"] = a["total_ms"]
json.dump(s, open(f"{W}/manifest_groups.json", "w"), indent=1, ensure_ascii=False); print(len(s["segments"]), "pieces")
PYEOF
step render      "$PY" dubstitch.py render --work "$W" --subs keep
step verify      "$PY" dubstitch.py verify --work "$W"
step bookends    "$PY" dubstitch.py bookends --work "$W" --out "$W/clean_noglow.mp4"
step trans_plan  "$PY" "$PL/scene_transitions.py" "$W" "$LIB/broll_library.json"
step transitions "$PY" dubstitch.py transitions --work "$W" --out "${P}_final_clean.mp4" --plan "$W/transitions_plan.json"
SUB_SRT="$W/${SHOW}_EP${N}_${D}.srt"
if [ "$STYLE" = dynamic ]; then
  step captions  "$PY" dubstitch.py captions --work "$W" --video "$SRC_VIDEO"
  step dynsrt    "$PY" dubstitch.py dynsrt --work "$W" --words "$W/${SHOW}_EP${N}_${D}_words.json" --out "$W/${SHOW}_EP${N}_${D}_dynamic.srt"
  SUB_SRT="$W/${SHOW}_EP${N}_${D}_dynamic.srt"
fi
step subs_dub    "$PY" dubstitch.py subs --work "$W" --style "$STYLE" --srt "$SUB_SRT" --out "${P}_final_${dl}_subs.mp4" --max-lead 0 ${FONTARG[@]+"${FONTARG[@]}"}
step translate   bash -c "'$PY' dubstitch.py translate --work '$W' --srt '$SUB_SRT' --sl $DL --tl en --out '${P}_english_google.srt' && \
  '$PY' dubstitch.py translate --work '$W' --srt '$SUB_SRT' --sl $DL --tl en --out '${P}_english_google.srt' --export-todo '$W/en_todo.tsv' && \
  if [ -s '$W/en_todo.tsv' ]; then cd '$AIP' && '$PY' -c \"
import csv
from dubstudio.pipeline import translate_ai
rows = [r for r in csv.reader(open(r'''$W/en_todo.tsv''', encoding='utf-8'), delimiter='\t') if r]
out = translate_ai.translate([r[0] for r in rows], sl='$DL', tl='en')
w = csv.writer(open(r'''$W/en_todo_done.tsv''', 'w', encoding='utf-8', newline=''), delimiter='\t'); [w.writerow([r[0], t]) for r, t in zip(rows, out)]
print('Claude translated', len(rows), 'lines Google left')\" && cd '$S' && \
  '$PY' dubstitch.py translate --work '$W' --srt '$SUB_SRT' --sl $DL --tl en --out '${P}_english_google.srt' --engine file --import '$W/en_todo_done.tsv'; fi"
step subs_en     "$PY" dubstitch.py subs --work "$W" --style "$STYLE" --srt "${P}_english_google.srt" --out "${P}_final_en_subs.mp4" --max-lead 0 ${FONTARG[@]+"${FONTARG[@]}"}
step scene_sheet bash -c "'$PY' '$PL/sceneedit_sheet.py' '$SW/scene_manifest.edl.json' '$LIB/broll_library.json' '${P}_english_google.srt' '$OUT/${show}_ep${N}_scene_edit.xlsx' '$SHOW ep$N: scene edit' && \
  '$PY' -c \"import json;p=r'''$W/config.json''';c=json.load(open(p));c['groups_review']=r'''$OUT/${show}_ep${N}_scene_edit.xlsx''';json.dump(c,open(p,'w'),indent=1)\""
# final QC (owner, 2026-10-30): B-roll repeats <= 3, shots >= 2 s, story sync (Claude reads every shot), no black frames,
# 25 fps, speaker -> OUT/<show>_ep<N>_qc.xlsx
step qc          "$PY" "$PL/qc_final.py" --work "$W" --library "$LIB" --videos "${P}_final_clean.mp4" "${P}_final_${dl}_subs.mp4" "${P}_final_en_subs.mp4" --en-srt "${P}_english_google.srt" --app "$AIP" --out "$OUT/${show}_ep${N}_qc.xlsx"
step check       "$PY" dubstitch.py check --work "$W"
echo "ALL STEPS DONE"
"$PY" "$S/run_report.py" "$W" "$OUT/${show}_ep${N}_run_times.md"     # the time + cost table for the owner (Step 11, mandatory)
~~~~~~

## Appendix: `scripts/run_report.py`

<!-- FILE: scripts/run_report.py md5=ad44c5f2c3a590ea11c7bc26cf9cee5d -->
~~~~~~python
#!/usr/bin/env python3
"""run_report.py <W> [out.md] -- the time (and AI cost) of every step of one episode, as a table for the owner (Step 11).

Reads W/run_times.tsv, one line per step run: name <TAB> seconds <TAB> gateway spend before <TAB> spend after <TAB>
exit code <TAB> clock time. run_episode.sh writes it; when steps are run by hand, append one line per step in the same
format (spend may be NA). A failed try is listed (red in the owner's eyes: time lost) and counted in the clock total.
The gateway books a call up to a minute late, so a late booking is added to the AI step before it."""
import sys

AI_STEPS = {"ai_match", "library", "scene_edit", "translate"}
NAMES = {"srt_dub": "Whisper subtitles, dub", "srt_src": "Whisper subtitles, source", "srt_de": "Whisper subtitles, dub",
         "srt_us": "Whisper subtitles, source", "lip_nets": "Lip scan, faces + talking (S3FD, TalkNet)",
         "lip_mesh": "Lip scan, mouth (Face Mesh)", "lip_merge": "Lip scan, join", "words_src": "Word times, source",
         "words_dub": "Word times, dub", "analyze": "Automatic alignment", "ai_match": "Step 2 by Claude (matching)",
         "sheet": "Mapping sheet + integrity check", "manifest": "Edit list", "lipsync": "Lip-sync rows",
         "groups": "Groups (automatic edit, fast-clip rule)", "library": "Shot library (Claude)",
         "scene_edit": "Scene edit (Claude cuts the picture)", "use_scene": "Scene edit -> edit list", "render": "Render",
         "verify": "Picture check", "bookends": "Opening / ending", "trans_plan": "Transition plan", "transitions": "Transitions",
         "captions": "Source captions (OCR)", "dynsrt": "Dynamic subtitle cards", "subs_dub": "Dub subtitle video",
         "subs_de": "Dub subtitle video", "translate": "English translation", "subs_en": "English subtitle video",
         "scene_sheet": "Scene-edit sheet", "check": "Final check"}


def num(x):
    try: return float(x)
    except Exception: return None


def main():
    W = sys.argv[1]; rows = []
    for line in open(f"{W}/run_times.tsv", encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 5: rows.append(dict(name=p[0], sec=int(float(p[1])), s0=num(p[2]), s1=num(p[3]), rc=p[4], at=p[5] if len(p) > 5 else ""))
    cost, last_ai = {}, None
    for i, r in enumerate(rows):
        d = (r["s1"] - r["s0"]) if r["s1"] is not None and r["s0"] is not None else 0.0
        if i and r["s0"] is not None and rows[i - 1]["s1"] is not None: d += r["s0"] - rows[i - 1]["s1"]   # booked between steps
        key = i if (r["name"] in AI_STEPS or last_ai is None) else last_ai
        cost[key] = cost.get(key, 0) + max(d, 0)
        if r["name"] in AI_STEPS: last_ai = i
    fm = lambda s: f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s"
    out = ["| # | Step | Time | Claude cost | Result |", "|---|---|---|---|---|"]
    ok_t = fail_t = 0
    for i, r in enumerate(rows):
        failed = r["rc"] != "0"; ok_t += 0 if failed else r["sec"]; fail_t += r["sec"] if failed else 0
        c = cost.get(i, 0)
        out.append(f"| {i + 1} | {NAMES.get(r['name'], r['name'])} | {fm(r['sec'])} | {'$%.2f' % c if c >= 0.005 else '-'} | "
                   + ("FAILED try (rerun below)" if failed else "ok") + " |")
    ok_c = sum(c for i, c in cost.items() if rows[i]["rc"] == "0"); fail_c = sum(c for i, c in cost.items() if rows[i]["rc"] != "0")
    out.append(f"| | **Total (steps that worked)** | **{fm(ok_t)}** | **${ok_c:.2f}** | |")
    if fail_t: out.append(f"| | Failed tries (lost) | {fm(fail_t)} | ${fail_c:.2f} | |")
    big = sorted((r for r in rows if r["rc"] == "0"), key=lambda r: -r["sec"])[:3]
    out.append("")
    out.append("Biggest steps: " + ", ".join(f"{NAMES.get(r['name'], r['name'])} {fm(r['sec'])}" for r in big)
               + f". Clock time from first to last step: about {fm(ok_t + fail_t)} of step time (plus any waiting between runs).")
    txt = "\n".join(out); print(txt)
    if len(sys.argv) > 2: open(sys.argv[2], "w", encoding="utf-8").write(txt + "\n")


if __name__ == "__main__":
    main()
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

## Appendix: `ai/dubstudio/__init__.py`

<!-- FILE: ai/dubstudio/__init__.py md5=b7fbde62df27abaaa97a90d4bac8a0a8 -->
~~~~~~python
"""Dub Studio: local platform around the dub-clip pipeline."""
__version__ = "0.1.0"
~~~~~~

## Appendix: `ai/dubstudio/ai.py`

<!-- FILE: ai/dubstudio/ai.py md5=b31fba91b7cac1dfabf6c9933df84b3b -->
~~~~~~python
"""AI gateway client (OpenAI-compatible chat completions, Pocket FM "madeye" gateway).

Settings come from environment variables, else from <app>/.env (never committed):
  AI_BASE   gateway URL, e.g. https://madeye.internal.pocketfm.org
  AI_KEY    the owner's key (Bearer token)
  AI_EMAIL  the owner's work email; the gateway wants it in metadata.user_email on every call
  AI_MODEL  default model (claude-sonnet-5-5); AI_MODEL_FAST (claude-haiku-5-5)
Models on this key (GET /v1/models): claude-sonnet-5-5, claude-haiku-5-5, claude-sonnet-4-6, gemini-3.5-flash.
Budget/spend: GET /key/info -> info.spend, info.max_budget.
Claude models are served through LiteLLM -> Vertex; do NOT send `temperature` to them (HTTP 400).
"""
import json, os, re, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # the app folder (holds .env)


def settings():
    s = {}
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?(AI_\w+)\s*=\s*(.*)\s*$", line)
            if m:
                s[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    for k in ("AI_BASE", "AI_KEY", "AI_EMAIL", "AI_MODEL", "AI_MODEL_FAST"):
        if os.environ.get(k):
            s[k] = os.environ[k]
    s.setdefault("AI_MODEL", "claude-sonnet-5-5"); s.setdefault("AI_MODEL_FAST", "claude-haiku-5-5")
    return s


def available():
    s = settings()
    return bool(s.get("AI_BASE") and s.get("AI_KEY") and s.get("AI_EMAIL"))


def _req(path, body=None, timeout=120):
    s = settings()
    if not available():
        raise RuntimeError("AI gateway not configured: set AI_BASE, AI_KEY, AI_EMAIL in app/.env")
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(s["AI_BASE"].rstrip("/") + path, data=data,
                               headers={"Authorization": "Bearer " + s["AI_KEY"], "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def chat(prompt, model=None, max_tokens=4000, system=None, retries=4, temperature=None):
    """One call. `prompt` is a string or an OpenAI-style content list. Returns (text, usage)."""
    s = settings(); model = model or s["AI_MODEL"]
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = dict(model=model, messages=msgs, max_tokens=max_tokens, metadata={"user_email": s.get("AI_EMAIL", "")})
    if temperature is not None:          # the Claude 5.5 models refuse `temperature` (deprecated); leave it out
        body["temperature"] = temperature
    for a in range(retries + 1):
        try:
            d = _req("/v1/chat/completions", body, timeout=300)
            ch = d["choices"][0]; txt = ch["message"].get("content") or ""
            if not txt.strip():                  # say why a reply is empty (e.g. finish_reason=length: max_tokens used up)
                import sys
                print(f"AI gateway: empty reply, finish_reason={ch.get('finish_reason')}, usage={d.get('usage')}", file=sys.stderr, flush=True)
            return txt, d.get("usage", {})
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504, 529) and a < retries:
                time.sleep(2 * 2 ** a); continue
            raise RuntimeError(f"AI gateway HTTP {e.code}: {e.read()[:300]!r}") from None
        except (urllib.error.URLError, TimeoutError):
            if a < retries:
                time.sleep(2 * 2 ** a); continue
            raise


def extract_json(text, key=None):
    """Claude sometimes writes prose (which may contain things like "[13]") before the JSON. Return the first JSON
    object found; with `key`, the first object that has that key."""
    text = re.sub(r"```(?:json)?", "", text)
    first = None
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                v = json.JSONDecoder().raw_decode(text[i:])[0]
            except ValueError:
                continue
            if key is None and isinstance(v, (dict, list)) and ch == "{":
                return v
            if key is not None and isinstance(v, dict) and key in v:
                return v
            first = first if first is not None else v
    if key is None and first is not None:
        return first
    raise ValueError("no JSON in model reply: " + text[:200])


def key_info():
    i = _req("/key/info", timeout=30); i = i.get("info", i)
    return {"spend": i.get("spend"), "max_budget": i.get("max_budget"), "models": i.get("models")}


def models():
    return [m["id"] for m in _req("/v1/models", timeout=30).get("data", [])]
~~~~~~

## Appendix: `ai/dubstudio/names.py`

<!-- FILE: ai/dubstudio/names.py md5=7f803b15393507c30021c850c69fd1da -->
~~~~~~python
"""The show's localisation sheet: character names in the source video's language -> the dub's language.

The house format (owner's decision, 2026-10-16) is one sheet per show with two columns:

    source name | target name          e.g.  Asher | Demetrio,  Violet | Viola,  Emily Kaiser | Emma Reali

as .csv or .xlsx (first sheet), header in the first row; more columns are allowed and ignored. Older sheets are
still read: the WBM "Master Adaptation Sheet" (Original Mention / Localized Mention, Type) and the TOLR
"Localization Details" (Original Name / Localized Name and their first/last names). Claude gets the list with
every matching request."""
import csv, io, re
from pathlib import Path

NAME_HINT = re.compile(r"locali[sz]|names?\b|name.?sheet|adaptation|character", re.I)
SHEET_EXT = (".csv", ".xlsx")


def _rows(path):
    p = Path(path)
    if p.suffix.lower() == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        it = wb.worksheets[0].iter_rows(values_only=True)
        head = [str(c or "").strip() for c in next(it, [])]
        return [{head[k]: ("" if v is None else str(v)) for k, v in enumerate(r) if k < len(head)} for r in it]
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t") if text.strip() else csv.excel
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def _norm(k):
    return re.sub(r"[^a-z]", "", (k or "").lower())


def _nice(x):
    x = " ".join(str(x).split())
    return x.title() if x and x == x.lower() else x


def read(path):
    """-> {"format": "two-column" | "WBM master sheet" | "TOLR localization details" | "unknown",
           "names": [(source, target)], "error": str|None}"""
    try:
        rows = _rows(path)
    except Exception as ex:
        return {"format": "unreadable", "names": [], "error": str(ex)[:200]}
    if not rows:
        return {"format": "empty", "names": [], "error": "no rows"}
    cols = {_norm(k): k for k in rows[0] if k}
    pairs, fmt = [], "unknown"
    if "sourcename" in cols and "targetname" in cols:                                   # the house format
        fmt = "two-column"
        pairs = [(r.get(cols["sourcename"]), r.get(cols["targetname"])) for r in rows]
    elif "originalmention" in cols and "localizedmention" in cols:                      # WBM master sheet
        fmt = "WBM master sheet"; tk = cols.get("type")
        pairs = [(r.get(cols["originalmention"]), r.get(cols["localizedmention"])) for r in rows
                 if not tk or (r.get(tk) or "").strip().lower() in ("character", "")]
    elif "originalname" in cols and "localizedname" in cols:                            # TOLR localization details
        fmt = "TOLR localization details"; tk = cols.get("type")
        for r in rows:
            if tk and (r.get(tk) or "").strip().lower() not in ("character", ""):
                continue
            pairs.append((r.get(cols["originalname"]), r.get(cols["localizedname"])))
            fo, fl = cols.get("firstnameoriginal"), cols.get("firstnamelocalized")
            if fo and fl:
                pairs.append((r.get(fo), r.get(fl)))
    out, seen = [], set()
    for s, t in pairs:
        s, t = _nice(s or ""), _nice(t or "")
        if not s or not t or (s.lower(), t.lower()) in seen:
            continue
        seen.add((s.lower(), t.lower())); out.append((s, t))
    return {"format": fmt, "names": out,
            "error": None if fmt != "unknown" else "no 'source name' / 'target name' columns"}


def find(show_folder):
    """Localisation sheets in the show folder (top two levels), best first: the two-column format wins."""
    folder = Path(show_folder); cands = []
    for depth_glob in ("*", "*/*"):
        for p in folder.glob(depth_glob):
            if p.is_file() and p.suffix.lower() in SHEET_EXT and NAME_HINT.search(p.stem) and not p.name.startswith((".", "~$")):
                cands.append(p)
    scored = []
    for p in cands:
        r = read(p)
        if r["names"]:
            scored.append((r["format"] != "two-column", -len(r["names"]), str(p)))
    return [s[2] for s in sorted(scored)]


def for_episode(names, texts, limit=150):
    """What Claude gets: the whole sheet when it is short; else the names that occur in this episode's text."""
    if len(names) <= limit:
        return names
    blob = " ".join(texts).lower()
    hit = [(s, t) for s, t in names if re.search(r"\b" + re.escape(s.lower()) + r"\b", blob)
           or re.search(r"\b" + re.escape(t.lower()) + r"\b", blob)]
    return hit[:limit]


def to_csv(names):
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["source name", "target name"])
    w.writerows(names)
    return buf.getvalue()


def for_show(show):
    """The show's localisation sheet: the one chosen on the show page, else the best one found in its folder."""
    pth = (show.get("profile") or {}).get("names_sheet")
    if pth and Path(pth).exists():
        return pth
    folder = show.get("folder")
    found = find(folder) if folder and Path(folder).is_dir() else []
    return found[0] if found else None


def whisper_csv(show, work):
    """Step 0B without a script: mksrt.py --names fixes how Whisper spells the characters. Writes the show's sheet
    as W/names.csv (the two-column format) and returns its path, or None when the show has no sheet."""
    pth = for_show(show)
    names = read(pth)["names"] if pth else []
    if not names:
        return None
    out = Path(work) / "names.csv"; out.parent.mkdir(parents=True, exist_ok=True)
    text = to_csv(names)
    if not out.exists() or out.read_text(encoding="utf-8") != text:
        out.write_text(text, encoding="utf-8")
    return str(out)
~~~~~~

## Appendix: `ai/dubstudio/pipeline/__init__.py`

<!-- FILE: ai/dubstudio/pipeline/__init__.py md5=2b9053c8947c85c4817febdbbb4a580c -->
~~~~~~python
# Dub Studio pipeline steps used by the skill: shot library, scene edit, transitions plan, AI matching, AI translation.
~~~~~~

## Appendix: `ai/dubstudio/pipeline/library.py`

<!-- FILE: ai/dubstudio/pipeline/library.py md5=d8b0bfcc447774947566fa2023185689 -->
~~~~~~python
"""Per-character B-roll library of a source video (owner, 2026-10-27).

Every shot of the source is looked at by Claude (3 frames + the narration around it): who is in it, where, what kind of shot
(reaction / neutral / talking / story action / object / establishing) and whether it may be used as B-roll. The owner's rule:
only reactions and neutral moments are B-roll; a shot that shows a specific story action or detail (e.g. picking up the
pastry) is NOT, because that moment is shown in its own place in the story.

Usage: library.py <work dir with sentences_src.json, lipsync.json, manifest_groups.json, config.json> <out folder>
                  [first seconds: per-character folders for the first N s, default 0 = none]
Camera cuts come from W/scenes.json (made here with ffmpeg if missing).
"""
import base64, io, json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

W, OUT = Path(sys.argv[1]), Path(sys.argv[2]); FIRST = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))          # app/backend
from dubstudio import ai  # noqa: E402


def camera_cuts(work, video):
    """Camera cuts of the source video (s), cached in W/scenes.json (same as fastfix.scenes): ffmpeg scene score > 0.28
    on a 12.5 fps, 192-px-wide copy."""
    cp = Path(work) / "scenes.json"
    try:
        c = json.load(open(cp))
        if c.get("video") == video and c.get("size") == os.path.getsize(video): return c["cuts"]
    except Exception:
        pass
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", video, "-an", "-vf", "fps=12.5,scale=192:-2,select='gt(scene,0.28)',showinfo",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    cuts = [round(float(x), 2) for x in re.findall(r"pts_time:([0-9.]+)", out)]
    json.dump({"video": video, "size": os.path.getsize(video), "cuts": cuts}, open(cp, "w"))
    return cuts


import atexit, threading  # noqa: E402
_U = dict(calls=0, prompt_tokens=0, completion_tokens=0); _L = threading.Lock(); _chat = ai.chat
def _count_chat(*a, **k):
    text, u = _chat(*a, **k)
    with _L:
        _U["calls"] += 1; _U["prompt_tokens"] += int((u or {}).get("prompt_tokens") or 0); _U["completion_tokens"] += int((u or {}).get("completion_tokens") or 0)
    return text, u
ai.chat = _count_chat
atexit.register(lambda: print("Claude usage:", _U, flush=True))

cfg = json.load(open(W / "config.json")); video = cfg["src_video"]; OP = cfg.get("opening_s", 0)
dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                           capture_output=True, text=True).stdout)
cuts = [c for c in camera_cuts(W, video) if 0 < c < dur]
b = [0.0] + cuts + [dur]
shots = []
for x, y in zip(b, b[1:]):
    if shots and y - x < 0.4: shots[-1][1] = y; continue          # a sliver joins the shot before
    shots.append([x, y])
src = json.load(open(W / "sentences_src.json"))["sentences"]
lip_iv = [tuple(v) for v in (json.load(open(W / "lipsync.json")).get("intervals") or [])]
M = json.load(open(W / "manifest_groups.json"))["segments"]
ov = lambda a0, a1, b0, b1: max(0.0, min(a1, b1) - max(a0, b0))
OUT.mkdir(parents=True, exist_ok=True); FR = OUT / "_frames"; FR.mkdir(exist_ok=True)
fmt = lambda s: f"{int(s // 60):02d}:{s % 60:05.2f}"


def strip(k):
    p = FR / f"shot_{k:03d}.jpg"
    if not p.exists():
        x, y = shots[k]; ims = []
        from PIL import Image
        for f in (0.2, 0.5, 0.8):
            t = x + (y - x) * f
            png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-vf", "scale=256:256",
                                  "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
            ims.append(Image.open(io.BytesIO(png)).convert("RGB"))
        s = Image.new("RGB", (768, 256)); [s.paste(im, (i * 256, 0)) for i, im in enumerate(ims)]
        s.save(p, quality=80)
    return p


def b64(p):
    return "data:image/jpeg;base64," + base64.b64encode(open(p, "rb").read()).decode()


def text_at(x, y, pad=1.0):
    return " ".join(s["text"] for s in src if ov(x - pad, y + pad, s["start_ms"] / 1000, s["end_ms"] / 1000) > 0)


with ThreadPoolExecutor(6) as ex: list(ex.map(strip, range(len(shots))))
cache_p = OUT / "_claude_cache.json"; cache = json.load(open(cache_p)) if cache_p.exists() else {}

# ---- 1. the cast: names from the narration, looks from the frames ----
if "cast" not in cache:
    sample = list(range(0, len(shots), max(1, len(shots) // 14)))[:14]
    content = [{"type": "text", "text":
        "These are frames from one episode of a drama (each image = 3 frames of one shot) and its whole narration. List the "
        "characters who appear ON SCREEN: name (from the narration) and how to recognise them (hair, clothes, age), and the "
        "places (scenes) of the episode in order. Answer only JSON: {\"cast\": [{\"name\": ..., \"look\": ...}], "
        "\"places\": [..]}\n\nNarration:\n" + " ".join(s["text"] for s in src)}]
    for k in sample:
        content += [{"type": "text", "text": f"shot at {fmt(shots[k][0])}:"}, {"type": "image_url", "image_url": {"url": b64(strip(k))}}]
    text, _ = ai.chat(content, max_tokens=4000)
    cache["cast"] = ai.extract_json(text, "cast"); json.dump(cache, open(cache_p, "w"), indent=1)
cast = cache["cast"]
cast_txt = "\n".join(f"- {c['name']}: {c['look']}" for c in cast["cast"])
print("cast:", [c["name"] for c in cast["cast"]], "places:", cast.get("places"))

RULES = """For EACH shot answer:
- people: the names (from the cast) of everyone visible; "" if nobody; a person not in the cast = a short description.
- place: where it is (kitchen, car, office, ...).
- type: one of "reaction" (a face showing a feeling: looking, listening, smiling, worried), "neutral" (a person just sitting /
  standing / looking with no special action), "talking" (the mouth clearly speaks), "action" (a specific story action or
  detail: picking up / eating / handing over an object, opening a door, cooking, a gesture that is a story moment),
  "object" (an object or detail without a face), "establishing" (a wide view of the place / exterior).
- action: what happens, in a few words.
- broll: "yes" only for reaction, neutral and establishing shots that could be cut in at another moment of the same scene
  without showing a story event twice or too early; "no" for talking, action and story objects (the owner's rule: a story
  action like picking up the pastry is shown in its own place, never as B-roll).
- why: one short sentence.
Answer only JSON: {"shots": [{"n": <shot number>, "people": [...], "place": "...", "type": "...", "action": "...",
"broll": "yes"|"no", "why": "..."}]}"""


def label(batch):
    key = ",".join(map(str, batch))
    if key in cache: return cache[key]
    content = [{"type": "text", "text": f"Cast of the episode:\n{cast_txt}\n\n{RULES}\n\nThe shots (3 frames each, left to right):"}]
    for k in batch:
        x, y = shots[k]
        content += [{"type": "text", "text": f"shot {k}: {fmt(x)}-{fmt(y)} ({y - x:.1f} s). Narration around it: \"{text_at(x, y)[:400]}\""},
                    {"type": "image_url", "image_url": {"url": b64(strip(k))}}]
    for attempt in range(3):
        try:
            text, _ = ai.chat(content, max_tokens=4000)
            r = ai.extract_json(text, "shots")
            if r and len(r.get("shots", [])) >= len(batch): break
        except Exception as e:
            r = None; time.sleep(3)
    cache[key] = r; json.dump(cache, open(cache_p, "w"), indent=1, ensure_ascii=False)
    return r


batches = [list(range(i, min(i + 6, len(shots)))) for i in range(0, len(shots), 6)]
with ThreadPoolExecutor(6) as ex: res = list(ex.map(label, batches))
info = {}
for r in res:
    for s in (r or {}).get("shots", []):
        info[int(s["n"])] = s

# ---- 2. the table: every shot ----
def used_at(x, y):
    """where the episode (before B-roll) shows this footage: video times"""
    out = []
    for s in M:
        o = ov(x, y, s["us_start_ms"] / 1000, s["us_end_ms"] / 1000)
        if o > 0.2:
            out.append(fmt(s["it_start_ms"] / 1000 + OP))
    return out
first_src = [(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000) for s in M if s["it_start_ms"] < FIRST * 1000]
first_shots = sorted({k for k, (x, y) in enumerate(shots) for a, c in first_src if ov(x, y, a, c) > 0.1})
# scenes: Claude splits the ordered shot list into story scenes (one place, one continuous moment); the place names it
# gave per shot vary ("kitchen", "kitchen / dining table"), so "same scene" is decided here, not by the words
if "scenes" not in cache:
    lst = "\n".join(f"{k}: {fmt(x)} {info.get(k, {}).get('place', '')} | {', '.join(info.get(k, {}).get('people') or [])} | "
                    f"{info.get(k, {}).get('action', '')}" for k, (x, y) in enumerate(shots))
    q = ("Here is every shot of a drama episode in order (number: time place | people | action). Split it into story SCENES: a "
         "scene is one place and one continuous moment (e.g. the breakfast in the kitchen/dining room is one scene even if the "
         "camera moves between the counter and the table). Answer only JSON: {\"scenes\": [{\"from\": first shot number, "
         "\"to\": last shot number, \"name\": short name}]}\n\n" + lst)
    text, _ = ai.chat(q, max_tokens=4000)
    cache["scenes"] = ai.extract_json(text, "scenes"); json.dump(cache, open(cache_p, "w"), indent=1, ensure_ascii=False)
scene_of = {}
for sc in cache["scenes"]["scenes"]:
    for k in range(int(sc["from"]), int(sc["to"]) + 1): scene_of[k] = sc["name"]
first_scenes = {scene_of.get(k) for k in first_shots}
print("scenes:", [(sc["from"], sc["to"], sc["name"]) for sc in cache["scenes"]["scenes"]])
# locations (owner 2026-10-28: a B-roll only from the same background): Claude groups the shots' place descriptions into
# locations = one background (the parking garage at the start and later = one location); a description that mixes places
# (flashback, montage) is "mixed" and never a B-roll
places = sorted({info.get(k, {}).get("place", "") for k in range(len(shots))} - {""})
if "locations" not in cache:
    q = ("Here are the place descriptions of the shots of one drama episode. Group them into LOCATIONS: one location = one "
         "background that looks the same on screen (e.g. 'parking garage' and 'parking garage beside the car' are one "
         "location; a car interior, a different room, a street are different locations). A description that mixes several "
         "places (a flashback, a montage: 'bedroom, then inside a dark car, then a city street') belongs to the location "
         "\"mixed\". Answer only JSON: {\"locations\": {\"<place description exactly as given>\": \"<short location name>\"}}\n\n"
         + "\n".join(f"- {p}" for p in places))
    text, _ = ai.chat(q, max_tokens=4000)
    cache["locations"] = ai.extract_json(text, "locations"); json.dump(cache, open(cache_p, "w"), indent=1, ensure_ascii=False)
loc_of_place = cache["locations"]["locations"]
print("locations:", sorted(set(loc_of_place.values())))
rows = []
for k, (x, y) in enumerate(shots):
    s = info.get(k, {}); lips = sum(ov(x, y, a, c) for a, c in lip_iv)
    rows.append(dict(n=k, start=x, end=y, length=round(y - x, 2), people=s.get("people") or [], place=s.get("place", ""),
                     type=s.get("type", "?"), action=s.get("action", ""), broll=s.get("broll", "?"), why=s.get("why", ""),
                     lips=round(lips, 2), scene=scene_of.get(k, ""), location=loc_of_place.get(s.get("place", ""), s.get("place", "") or "?"), same_scene=scene_of.get(k) in first_scenes, used=used_at(x, y),
                     in_first=k in first_shots, text=text_at(x, y, 0)[:300]))
for r in rows:
    if r["location"] == "mixed": r["broll"] = "no"
json.dump(dict(cast=cast, first_shots=first_shots, shots=rows), open(OUT / "broll_library.json", "w"), indent=1, ensure_ascii=False)

# ---- 3. the characters of the first FIRST seconds: their folders ----
chars = []
for k in first_shots:
    for p in info.get(k, {}).get("people") or []:
        if p and p not in chars: chars.append(p)
print("first", FIRST, "s of the episode: shots", first_shots, "characters", chars)


def clip(r, folder):
    name = f"shot{r['n']:03d}_{fmt(r['start']).replace(':', 'm').replace('.', 's')}_{r['type']}.mp4"
    p = folder / name
    if not p.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{r['start']:.3f}", "-to", f"{r['end']:.3f}", "-i", video, "-an",
                        "-vf", "scale=540:540", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", str(p)], check=True)
    return name


def sheet_img(rs, path, title):
    from PIL import Image, ImageDraw
    cols = 4; w = 256; h = 256 + 44
    im = Image.new("RGB", (cols * w, ((len(rs) + cols - 1) // cols) * h + 30), "white"); d = ImageDraw.Draw(im)
    d.text((6, 8), title, fill="black")
    for i, r in enumerate(rs):
        st_ = Image.open(FR / f"shot_{r['n']:03d}.jpg").crop((256, 0, 512, 256))
        x0, y0 = (i % cols) * w, 30 + (i // cols) * h
        im.paste(st_, (x0, y0))
        col = (0, 130, 0) if r["broll"] == "yes" else (200, 0, 0)
        d.rectangle([x0, y0 + 256, x0 + w - 1, y0 + h - 1], fill=(245, 245, 245))
        d.text((x0 + 4, y0 + 259), f"#{r['n']} {fmt(r['start'])} {r['length']}s {r['type']}", fill="black")
        d.text((x0 + 4, y0 + 273), ("B-ROLL OK" if r["broll"] == "yes" else "NOT B-roll") + (" same scene" if r["same_scene"] else ""), fill=col)
    im.save(path, quality=82)


for ch in chars:
    folder = OUT / re.sub(r"[^\w -]", "", ch).strip()
    (folder / "broll_ok").mkdir(parents=True, exist_ok=True); (folder / "not_broll").mkdir(exist_ok=True)
    mine = [r for r in rows if ch in r["people"]]
    for r in mine:
        r.setdefault("clip", {})[ch] = ("broll_ok/" if r["broll"] == "yes" else "not_broll/") + clip(r, folder / ("broll_ok" if r["broll"] == "yes" else "not_broll"))
    sheet_img(mine, folder / f"{ch}_all_shots.jpg", f"{ch}: every shot (green = B-roll OK, red = not B-roll)")
    print(ch, len(mine), "shots,", sum(r["broll"] == "yes" for r in mine), "B-roll OK,",
          sum(r["broll"] == "yes" and r["same_scene"] for r in mine), "of them in the same scene")

# ---- 4. the workbook ----
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
A = "Arial"; hf = Font(name=A, bold=True, color="FFFFFF"); hfill = PatternFill("solid", fgColor="2F4F6F"); bf = Font(name=A, size=10)
thin = Side(style="thin", color="D0D0D0"); bx = Border(left=thin, right=thin, top=thin, bottom=thin)
ok_f = PatternFill("solid", fgColor="E2EFDA"); no_f = PatternFill("solid", fgColor="F8CBAD"); first_f = PatternFill("solid", fgColor="DDEBF7")
def table(ws, head, data, widths, fill=None):
    for c, h in enumerate(head, 1):
        x = ws.cell(1, c, h); x.font = hf; x.fill = hfill; x.border = bx; x.alignment = Alignment(wrap_text=True, vertical="center")
    for i, row in enumerate(data, 2):
        f = fill(row) if fill else None
        for c, v in enumerate(row, 1):
            x = ws.cell(i, c, v); x.font = bf; x.border = bx; x.alignment = Alignment(wrap_text=True, vertical="top")
            if f: x.fill = f
    for c, w in enumerate(widths, 1): ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
    ws.freeze_panes = "A2"
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "How it works"
lines = [f"B-roll library of the source video {os.path.basename(video)} ({fmt(dur)})", "",
         f"1. The source video is cut into its {len(shots)} shots (camera cuts).",
         "2. Claude looks at every shot (3 frames) with the narration around it and says who is in it, where, what kind of shot it "
         "is and whether it may be a B-roll. Rule: only reaction, neutral and establishing shots are B-roll; talking shots, story "
         "actions (e.g. picking up the pastry) and story objects are not, because they belong to their own moment of the story.",
         "3. Mouth moving (s) comes from our lip scan. Claude splits the shots into story scenes; 'Same scene' = the scene of the "
         "first part of the episode (only those are B-roll candidates for it).",
         "4. 'Used in the episode at' = where the episode already shows that footage (video times, before any B-roll).",
         f"5. Characters on screen in the first {FIRST:.0f} s: {', '.join(chars)}. Each has a tab here and a folder with every shot "
         "of them as a small clip: broll_ok/ and not_broll/, plus <name>_all_shots.jpg (one frame per shot, green = OK).",
         "", "Mark any shot that is wrong (column 'Your note'); the next step stitches the 30 s with these B-rolls."]
for i, t in enumerate(lines, 1):
    x = ws.cell(i, 1, t); x.font = Font(name=A, size=13 if i == 1 else 10, bold=i == 1); x.alignment = Alignment(wrap_text=True)
ws.column_dimensions["A"].width = 130
head = ["Shot", "Source from", "to", "Length s", "People", "Scene", "Same scene", "Shot type", "What happens", "B-roll?",
        "Why", "Mouth moving s", "Used in the episode at", "Clip file", "Narration", "Your note"]
wd = [6, 10, 10, 8, 18, 12, 8, 11, 32, 8, 40, 9, 18, 34, 45, 20]
def rowv(r, ch=None):
    return [r["n"], fmt(r["start"]), fmt(r["end"]), r["length"], ", ".join(r["people"]), f"{r['scene']} ({r['place']})", "yes" if r["same_scene"] else "",
            r["type"], r["action"], r["broll"], r["why"], r["lips"], ", ".join(r["used"][:4]), (r.get("clip") or {}).get(ch, "") if ch else "",
            r["text"], ""]
if FIRST: table(wb.create_sheet(f"First {FIRST:.0f} s"), head, [rowv(r, (r["people"] or [None])[0]) for r in rows if r["in_first"]], wd,
      fill=lambda v: first_f)
for ch in chars:
    mine = [r for r in rows if ch in r["people"]]
    mine.sort(key=lambda r: (r["broll"] != "yes", not r["same_scene"], r["start"]))
    table(wb.create_sheet(ch[:30]), head, [rowv(r, ch) for r in mine], wd, fill=lambda v: ok_f if v[9] == "yes" else no_f)
table(wb.create_sheet("All shots"), head, [rowv(r, (r["people"] or [None])[0]) for r in rows], wd,
      fill=lambda v: ok_f if v[9] == "yes" else no_f)
wb.save(OUT / "broll_library.xlsx")
print("saved", OUT / "broll_library.xlsx", len(rows), "shots,", len(cache["scenes"]["scenes"]), "scenes")
~~~~~~

## Appendix: `ai/dubstudio/pipeline/sceneedit.py`

<!-- FILE: ai/dubstudio/pipeline/sceneedit.py md5=d1754fa1f5ad2696223900a66cb64f53 -->
~~~~~~python
"""Scene editor (owner, 2026-10-27): Claude cuts the picture of the dub like a film editor, from the story.

Why: the row-by-row matching + groups + B-roll fill each line on its own. That put "Paul... warte!" (her mouth) over the
silence before the voice, and Paul's "Ja?" clip during the narration before it. A person edits the scene as a whole:
first Paul walking to the door, THEN the girl calling "wait" exactly when the voice says it; a narration about Paul shows
Paul; an action is shown when it is told.

The dub range is cut into windows of about 30 s (at a pause between lines). For each window, in order, Claude gets:
  - every dub line with its exact voice times, marked dialogue (quotes) or narration;
  - every source shot of the scene(s) the automatic match uses there (from the B-roll library): frames, who, shot type,
    what happens, when a mouth moves, the source narration over it, where the automatic match used it;
  - the footage already used by the earlier windows, and the last pieces before this window (continuity);
and returns an edit decision list (EDL): back-to-back pieces of shots. The script checks it (tiling, footage inside its
shot, speed 0.8-1.25x, piece >= 0.5 s, every dialogue line starts on a moving mouth within 0.3 s, action/talking footage
only once in the episode, reaction/neutral repeats 8 s apart) and sends the problems back to Claude (up to 4 rounds).

Usage: sceneedit.py <work> <library dir> <from s> <to s> <out manifest json> [--window 30] [--model M] [--parallel N]
  --parallel N: all stretches at the same time, N Claude calls at once (see PARALLEL MODE below); without it: one by one.
Writes the manifest (segments for render) and <out>.edl.json (every window: EDL with reasons, check rounds).
"""
import base64, json, os, re, sys
from pathlib import Path

W, LIBD, A0, A1, OUTM = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), Path(sys.argv[5])
opt = lambda k, d: type(d)(sys.argv[sys.argv.index(k) + 1]) if k in sys.argv else d
WIN = opt("--window", 30.0); MODEL = opt("--model", "")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))          # app/backend
from dubstudio import ai  # noqa: E402

SPEED_LO, SPEED_HI, MIN_PIECE, LIP_TOL, REUSE_GAP = 0.8, 1.25, 0.5, 0.3, 8.0
MAX_BROLL_USES = 3        # owner 2026-10-30: one B-roll shot at most 3 times in the episode (QC case 1)
LIB = json.load(open(LIBD / "broll_library.json")); SH = {r["n"]: r for r in LIB["shots"]}
cast = LIB.get("cast", {}).get("cast", [])
dub = [d for d in json.load(open(W / "sentences_dub.json"))["sentences"] if d.get("start_ms") is not None]
src = json.load(open(W / "sentences_src.json"))["sentences"]
lip_iv = [tuple(v) for v in (json.load(open(W / "lipsync.json")).get("intervals") or [])]
auto = json.load(open(W / "manifest_groups.json"))["segments"]
ov = lambda a0, a1, b0, b1: max(0.0, min(a1, b1) - max(a0, b0))
QUOTE = re.compile(r"[„“\"«»]")
# dialogue = a line with a quote mark OR inside a quote that an earlier sentence opened (owner 2026-10-28: "Sei doch nicht
# so paranoid! | Das Fitnessstudio gehoert ... | Niemand wird uns ... | Komm schon ...“" is ONE quote over 4 sentences).
# German opens with „ and closes with “; other languages open with “ and close with ”; «»; a straight " toggles.
_de = any("„" in d["text"] for d in dub)
_OPEN, _CLOSE = ("„«", "“”»") if _de else ("“«", "”»")
_inside, _run = False, 0
for d in dub:
    has = bool(QUOTE.search(d["text"]))
    d["dialogue"] = has or _inside
    _run = 0 if has else _run + 1
    if _inside and _run > 4: _inside = False; d["dialogue"] = has      # a quote never closed: stop after 4 sentences
    for ch in d["text"]:
        if ch in _OPEN: _inside = True
        elif ch in _CLOSE: _inside = False
        elif ch == '"': _inside = not _inside
# LOCATION GUARD (owner 2026-10-28): a piece shows only the background of its line -- a shot of the same scene, or a
# reaction/neutral shot of the SAME location elsewhere in the episode; never another background. The library gives every
# shot a location (place descriptions grouped by Claude; "mixed" = flashback/montage); a line's location = the location of
# the footage the automatic match gives it.
HAS_LOC = any("location" in r for r in LIB["shots"])


def line_auto(d):
    return [(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000) for s in auto
            if not s.get("video") and ov(d["start_ms"], d["end_ms"], s["it_start_ms"], s["it_end_ms"]) > 0]


def line_loc(d):
    tot = {}
    for a, b in d["_auto"]:
        for r in LIB["shots"]:
            o = ov(a, b, r["start"], r["end"])
            if o > 0 and r.get("location") and r["location"] != "mixed": tot[r["location"]] = tot.get(r["location"], 0) + o
    return max(tot, key=tot.get) if tot else None


def line_places():
    """Claude reads the story once (text only): for every dub line the source scene it plays in and the location there.
    The automatic match is not enough for this: where it stretched one shot over several lines (TGS ep32 lines 99-106,
    still in the gym, got a parking-garage shot) it would give the wrong place. Cached in W/line_places.json."""
    cp = W / "line_places.json"; key = f"{len(dub)}|{len(LIB['shots'])}|{LIB['shots'][-1].get('scene')}|{dub[-1]['text'][:30]}"
    if cp.exists():
        c = json.load(open(cp))
        if c.get("key") == key: return c["lines"]
    scenes = []
    for r in LIB["shots"]:
        if not scenes or scenes[-1]["name"] != r["scene"]: scenes.append(dict(name=r["scene"], start=r["start"], end=r["end"], locs=[]))
        scenes[-1]["end"] = r["end"]
        if r.get("location") and r["location"] not in scenes[-1]["locs"]: scenes[-1]["locs"].append(r["location"])
    stxt = "\n".join(f"scene {i}: {sc['name']} | locations: {', '.join(sc['locs'])} | source narration: \""
                     + " ".join(x["text"] for x in src if sc["start"] <= x["start_ms"] / 1000 < sc["end"])[:900] + "\"" for i, sc in enumerate(scenes))
    q = ("A dubbed episode tells the same story as the source video, in the same order (lines may be reworded, added or cut). "
         "For EVERY dub line, say in which source scene it plays and at which of that scene's locations (the background on "
         "screen at that moment of the story). A line told as a memory / flashback gets the location \"mixed\". Answer only "
         "JSON: {\"lines\": [[dub line number, scene number, \"location\"], ...]} with one entry per dub line, in order.\n\n"
         "SOURCE SCENES, in order:\n" + stxt + "\n\nDUB LINES:\n" + "\n".join(f"{d['idx']}: {d['text']}" for d in dub))
    for attempt in range(3):
        try:
            text, _ = ai.chat(q, model=MODEL or None, max_tokens=16000)
            res = ai.extract_json(text, "lines")["lines"]
            if len(res) >= 0.9 * len(dub): break
        except Exception as ex:
            print("line places: reply not readable, asking again", str(ex)[:80], flush=True); res = []
    out = {}
    for row in res:
        try:
            a, b, c = int(row[0]), int(row[1]), str(row[2])
        except Exception:
            continue
        if not 0 <= b < len(scenes): continue
        sc = scenes[b]
        if c not in sc["locs"] and c != "mixed": c = sc["locs"][0] if sc["locs"] else c
        out[str(a)] = dict(scene=sc["name"], loc=c)
    json.dump(dict(key=key, lines=out), open(cp, "w"), indent=1, ensure_ascii=False)
    return out


PLACES = line_places() if HAS_LOC else {}
for d in dub:
    d["_auto"] = line_auto(d); pl = PLACES.get(str(d["idx"]))
    d["_near"] = [iv for x in dub if abs(x["idx"] - d["idx"]) <= 1 for iv in line_auto(x)]
    d["scene"], d["loc"] = (pl["scene"], pl["loc"]) if pl else (None, line_loc(d) if HAS_LOC else None)
NARR_MOUTH = 0.2          # owner 2026-10-28: NEVER a moving mouth while narration plays (more than 0.2 s of it = a problem)


def mouth_in(a, b):
    """seconds of moving mouth (lip scan) in source a-b"""
    return sum(ov(x, y, a, b) for x, y in lip_iv)


def eff_type(e, r):
    """footage with a moving mouth is talking footage (shown once), whatever the library calls the shot"""
    return "talking" if mouth_in(e["from"], e["to"]) > 0.3 else r["type"]


def narration_mouth(e, lines):
    """[(line, src a, src b, mouth s)] where this piece shows a moving mouth while a narration line is heard"""
    sp = (e["to"] - e["from"]) / max(e["t1"] - e["t0"], 1e-6); out = []
    for d in lines:
        if d["dialogue"]: continue
        a, b = max(e["t0"], d["start_ms"] / 1000), min(e["t1"], d["end_ms"] / 1000)
        if b - a <= 0.05: continue
        sa, sb = e["from"] + (a - e["t0"]) * sp, e["from"] + (b - e["t0"]) * sp
        m = mouth_in(sa, sb)
        if m > NARR_MOUTH: out.append((d, sa, sb, m))
    return out


def windows(a0, a1):
    """cut points in the pauses between lines, about every WIN s"""
    gaps = [(dub[i]["end_ms"] / 1000 + dub[i + 1]["start_ms"] / 1000) / 2 for i in range(len(dub) - 1)
            if dub[i]["end_ms"] / 1000 > a0 and dub[i + 1]["start_ms"] / 1000 < a1 and not same_grp(dub[i], dub[i + 1])]
    cuts, t = [a0], a0
    while a1 - t > WIN * 1.4:
        cand = [g for g in gaps if t + WIN * 0.6 <= g <= t + WIN * 1.4]
        if not cand: break
        t = min(cand, key=lambda g: abs(g - (t + WIN))); cuts.append(round(t, 2))
    return list(zip(cuts, cuts[1:] + [a1]))


def shot_txt(r, auto_w):
    lips = [(max(a, r["start"]), min(b, r["end"])) for a, b in lip_iv if ov(a, b, r["start"], r["end"]) > 0.05]
    said = " ".join(s["text"] for s in src if ov(s["start_ms"] / 1000, s["end_ms"] / 1000, r["start"], r["end"]) > 0.1)
    auto_at = [f"{s['it_start_ms'] / 1000:.1f}-{s['it_end_ms'] / 1000:.1f}" for s in auto_w
               if ov(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000, r["start"], r["end"]) > 0.1]
    return (f"shot {r['n']}: source {r['start']:.2f}-{r['end']:.2f} s ({r['length']} s) | {', '.join(r['people']) or 'nobody'} | "
            f"{r['type']}{' (talking: ' + ', '.join(talkers(r['n'])) + ')' if talkers(r['n']) else ''} | {r['action']} | mouth moving at: {', '.join(f'{a:.2f}-{b:.2f}' for a, b in lips) or 'never'} | "
            f"source narration over it: \"{said[:160]}\"" + (f" | the automatic match used it at dub {', '.join(auto_at)}" if auto_at else "")
            + (f" | LOCATION: {r.get('location')} (scene: {r['scene']})" if HAS_LOC else "") + on_screen(r["n"]))


def prompt(T0, T1):
    return f"""You are the film editor of a dubbed episode. The dub's voice track is fixed. Cut the PICTURE for dub time {T0:.2f}-{T1:.2f} s
using only the source shots below (frames attached). Think about the story like an editor: what must the viewer see at each
moment so picture and voice tell the same story?

Characters: {'; '.join(f"{c['name']}: {c['look']}" for c in cast)}
(The dub may use other names for the same people; match them by the story.)

Rules:
1. The pieces are back to back and cover {T0:.2f}-{T1:.2f} s exactly (first t0 = {T0:.2f}, each t0 = the previous t1, last t1 = {T1:.2f}).
2. A piece plays one shot from "from" to "to" (source seconds, inside that shot). speed = (to - from) / (t1 - t0) must be
   between {SPEED_LO} and {SPEED_HI} (1.0 is best). A piece lasts at least {MIN_PIECE} s.
3. DIALOGUE line (in quotes): show the SPEAKER with a moving mouth, timed so the mouth starts moving when the voice starts:
   the source time where the mouth starts moving must land on the line's start (within {LIP_TOL} s). You may cut to the
   listener after the first words of a long line.
4. NARRATION line: show what it tells, and keep the person it is about on screen ("Paul looked down at himself" shows Paul).
   A B-roll piece over narration NEVER shows a moving mouth (see "mouth moving at" of every shot, reaction shots too):
   use only seconds where nobody's mouth moves. The line's own footage stays as matched.
   An action (walking to the door, turning round, picking something up) is shown when the voice tells it, not earlier, once.
5. Before a dialogue line, show what leads to it (e.g. the person being called walking away, then the caller says "wait").
6. Never show an event before the voice has told it. Action and talking footage is shown ONCE in the whole episode (see the
   footage already used below). Only reaction / neutral shots (a face listening, looking) may be shown again, at least
   {REUSE_GAP:.0f} s after the last time, with "reuse": true. Every B-roll shot is used AT MOST {MAX_BROLL_USES} times in the episode;
   when a place needs B-roll again and again, use that place's B-roll shots IN TURN (A, B, A, B ...), never the same shot
   twice in a row when another one of that place is there (it looks repeated).
7. Prefer longer pieces and few cuts; follow the story order of the footage where you can; continue smoothly from the
   pieces just before this stretch.
8. LOCATION: every dub line is marked with the location (background) where it plays. A B-roll piece shows ONLY that place: a
   shot of the line's scene (not a flashback shot), or a reaction / neutral shot of the SAME location from elsewhere in the
   episode. Never a shot with another background (another room, another scene's place, a flashback).
9. B-ROLL ONLY FOR SLOW LINES (hard rule): every line is marked with its OWN footage (what the automatic match plays during
   it) and its speed. Show each line with its own footage (or its neighbours' own footage, e.g. for lip timing or the
   story order). Lines marked GROUP share their own footage: place it in STORY ORDER inside the group -- an action
   (e.g. shoving the phone in his face) goes on the line that TELLS it, never on an earlier line of the group; the other
   lines of the group show its reaction / neutral footage. Only a SLOW line (own footage below 0.8x) may add B-roll, and only for the seconds given; use its own
   footage first (down to 0.8x). A FAST line (above 1.25x) shows a shorter part of its own footage (trim) -- never B-roll.
   A normal line (0.8-1.25x) never gets B-roll. B-roll = a reaction / neutral / establishing shot, never action or talking;
   only on a NOT FOUND (gap) line may it also be the action / object shot that shows exactly what that line tells.
   Every B-roll piece lasts at least {MIN_BROLL:.0f} s (shorter cutaways look jumpy). A B-roll you plan shorter is lengthened
   to {MIN_BROLL:.0f} s by the software, taking the time from the clip before it (that clip then plays faster). A slow line
   that needs less than {MIN_BROLL:.0f} s of B-roll takes either one {MIN_BROLL:.0f}-s B-roll or none (its own footage slower, down to 0.65x).
10. SPEAKER: a moving mouth during a dialogue line must be the mouth of the person who SAYS it (every dialogue line says
   who; every talking shot says who talks). Never show another person talking over it. A WRONG-SPEAKER line (its matched
   footage shows the wrong person talking) is shown with its speaker talking -- a SPEAKER shot of the same location, the
   mouth starting with the voice; it may repeat footage shown elsewhere in the episode -- or a face that does not talk.

Answer ONLY JSON: {{"edl": [{{"t0": s, "t1": s, "shot": n, "from": s, "to": s, "line": dub line number or null, "reuse": false,
"why": "what the viewer sees and why here (at most 15 words)"}}], "notes": "anything that could not be done (at most 40 words)"}}
Keep the answer short: no text outside the JSON."""


def b64(n):
    return "data:image/jpeg;base64," + base64.b64encode(open(LIBD / "_frames" / f"shot_{n:03d}.jpg", "rb").read()).decode()


# B-ROLL ONLY FOR SLOW LINES (owner, 2026-10-28, hard rule): every line shows its OWN footage (what the automatic match
# plays during it; the neighbouring lines' footage is allowed too, for lip timing and story order). Only a line whose own
# footage would play slower than 0.8x (SLOW) may add B-roll, and only for the time its own footage cannot fill at 0.8x.
# A fast line (above 1.25x) is TRIMMED: a shorter part of its own footage, never B-roll. Normal lines: own footage only.
# The own footage is used as matched, with no exceptions (owner, 2026-10-28: the talking mouths over narration came from
# B-roll shots). The B-roll quality rules (reaction / neutral / establishing shot only, no moving mouth under narration,
# the line's place, repeats 8 s apart) apply to the B-roll pieces.
SLOW, OWN_FRAC, OWN_PAD = 0.8, 0.5, 0.5
# owner, 2026-10-29: a B-roll of a few hundred ms or 1 s looks jumpy. Every B-roll piece lasts at least MIN_BROLL; a slow
# line that needs less either takes one B-roll of MIN_BROLL (its own footage then plays faster, up to 1.25x) or no B-roll
# and its own footage a little slower, down to SLOW_FLOOR.
# owner, 2026-10-29 (later): at least 2 s (was 1.3). A B-roll that comes out shorter is LENGTHENED to 2 s by code
# (fix_short_broll): the missing time (2 s - its length) is taken from the clip just before it, which keeps its footage
# and plays faster (example: clip 1-3 s + B-roll 3-4.3 s -> clip 1-2.3 s + B-roll 2.3-4.3 s). Faster than SPEED_HI that
# clip shows a shorter part of its footage (its start is kept: a mouth or an action begins there), like a fast line.
MIN_BROLL, SLOW_FLOOR = 2.0, 0.65


def merge_iv(iv):
    out = []
    for a, b in sorted(tuple(x) for x in iv):
        if out and a <= out[-1][1]: out[-1][1] = max(out[-1][1], b)
        else: out.append([a, b])
    return out


def own_line(d):
    """(source intervals, seconds) the automatic match plays during this line = its own footage"""
    iv, sec = [], 0.0
    for s in auto:
        if s.get("video"): continue
        o0, o1 = max(d["start_ms"], s["it_start_ms"]), min(d["end_ms"], s["it_end_ms"])
        if o1 - o0 <= 0: continue
        k = (s["us_end_ms"] - s["us_start_ms"]) / max(s["it_end_ms"] - s["it_start_ms"], 1)
        a = (s["us_start_ms"] + (o0 - s["it_start_ms"]) * k) / 1000; b = (s["us_start_ms"] + (o1 - s["it_start_ms"]) * k) / 1000
        iv.append((a, b)); sec += b - a
    return merge_iv(iv), sec


def load_gaps():
    """dub rows Step 2 left without a source line (decisions.json "gap"): they have NO own footage -- the automatic edit
    only stretched a neighbour over them -- so they are covered with B-roll only (owner, 2026-10-29: an added event like
    "she smashed a coffee cup against the wall" must never show another line's footage, e.g. Ava saying "liar")."""
    for p_ in (W, W.parent, W.parent.parent):
        f = p_ / "decisions.json"
        if f.exists():
            try: return {int(k) for k in json.load(open(f)).get("gap", {})}
            except Exception: return set()
    return set()


GAPS = load_gaps()
for d in dub:
    d["_own"], _os = own_line(d); _dur = max((d["end_ms"] - d["start_ms"]) / 1000, 1e-6)
    d["gap"] = d["idx"] in GAPS
    if d["gap"]: d["_own"], _os, d["_near"] = [], 0.0, []
    d["own_speed"] = round(_os / _dur, 2); d["slow"] = d["own_speed"] < SLOW
    d["broll_budget"] = round(max(0.0, _dur - _os / SLOW), 2) if d["slow"] else 0.0

# SPEAKER (owner, 2026-10-29): ep34 0:01 -- Paul says "Anya, bitte! Das ist nicht das, wonach es aussieht!" but the matched
# footage showed Anya talking (the source line "If it's not what it looks like..." is hers), although Paul's talking
# footage of the same office exists. A moving mouth during a dialogue line must be its SPEAKER's mouth. Claude names once
# (text only, cached in W/speakers.json) who says every dub dialogue line and who talks in every source shot with a moving
# mouth. A dialogue line whose own footage shows someone else talking is a WRONG-SPEAKER line ("swap"): that footage is no
# longer its own; it is shown with a talking shot of its speaker at the same location (a SPEAKER shot -- talking footage
# shown elsewhere in the episode may be repeated for it, at least SPK_GAP s away, zoomed) or a face that does not talk.
# owner 2026-10-30: no 30-s gap for SPEAKER shots -- a place's speaker shots are used IN TURN (A, B, A, B), never the same
# footage right after itself (SPK_GAP), and like every B-roll shot at most MAX_BROLL_USES times in the episode.
SPK_GAP = 1.0
NAMES = [c["name"] for c in cast]


def speakers():
    cp = W / "speakers.json"
    talk = [r for r in LIB["shots"] if r["type"] == "talking" or mouth_in(r["start"], r["end"]) > 0.3]
    key = f"v2|{len(dub)}|{len(talk)}|{len(LIB['shots'])}|{dub[-1]['text'][:30]}"
    if cp.exists():
        c = json.load(open(cp))
        if c.get("key") == key: return c

    def said(r):
        return " ".join(x["text"] for x in src if ov(x["start_ms"] / 1000, x["end_ms"] / 1000, r["start"], r["end"]) > 0.1)[:220]
    q = ("A dubbed episode (another language; the dub may use other names for the same people) tells the story of a source "
         f"video. Characters (source names): {', '.join(NAMES)}.\n"
         "1. For EVERY dub line marked DIALOGUE say who SAYS it out loud: a source name from the list, \"other\" for someone not "
         "in the list, \"narration\" if it is really the narrator (or a thought, not spoken), \"unclear\" if the story does not tell.\n"
         "2. For EVERY source shot below say whose mouth TALKS in it (a list of source names, [] if you cannot tell), from the "
         "shot's description and the words heard over it.\n"
         "Answer only JSON: {\"lines\": [[dub line number, \"name\"], ...], \"shots\": [[shot number, [\"name\", ...]], ...]}\n\n"
         "DUB LINES:\n" + "\n".join(f"{d['idx']}: {'DIALOGUE' if d['dialogue'] else 'narration'}: {d['text']}" for d in dub) +
         "\n\nSOURCE SHOTS:\n" + "\n".join(f"shot {r['n']}: {', '.join(r['people']) or 'nobody'} | {r['type']} | {r['action']} | "
                                          f"words heard: \"{said(r)}\"" for r in talk))
    res = {}
    for attempt in range(3):
        try:
            text, _ = ai.chat(q, model=MODEL or None, max_tokens=16000)
            res = ai.extract_json(text, "lines") or {}
            if res.get("lines"): break
        except Exception as ex:
            print("speakers: reply not readable, asking again", str(ex)[:80], flush=True)
    low = {n.lower(): n for n in NAMES}
    lines, shots = {}, {}
    for row in res.get("lines") or []:
        try: lines[str(int(row[0]))] = low.get(str(row[1]).strip().lower(), str(row[1]))
        except Exception: pass
    for row in res.get("shots") or []:
        try:
            v = row[1] if isinstance(row[1], list) else [row[1]]
            shots[str(int(row[0]))] = [low[str(x).strip().lower()] for x in v if str(x).strip().lower() in low]
        except Exception: pass
    out = dict(key=key, lines=lines, shots=shots)
    if lines: json.dump(out, open(cp, "w"), indent=1, ensure_ascii=False)
    return out


def minus_iv(iv, cut):
    out = []
    for a, b in iv:
        segs = [(a, b)]
        for c0, c1 in cut:
            segs = [q for x, y in segs for q in ((x, min(y, c0)), (max(x, c1), y)) if q[1] - q[0] > 0.05]
        out += segs
    return out


SPK = speakers() if NAMES else dict(lines={}, shots={})
SHOT_SPK = {int(k): v for k, v in SPK.get("shots", {}).items()}
# FACE MAP (owner, 2026-10-29; facemap.py -> <library>/face_map.json): who is on screen, where, face / back, and WHO
# TALKS WHEN from the picture (face recognition + the lip scan's per-face talking scores). When it exists it decides who
# talks in a shot; the text-only guess above is the fallback.
FMP = LIBD / "face_map.json"; FACE = json.load(open(FMP))["shots"] if FMP.exists() else {}
def talkers_at(n, a=-1e9, b=1e9):
    """names talking in shot n during source a-b (face map), else the text guess for the whole shot"""
    fm = FACE.get(str(int(n)))
    if fm is None: return SHOT_SPK.get(int(n), [])
    return sorted({x["name"] for x in fm["talking"] if x["name"] in NAMES and ov(x["from_s"], x["to_s"], a, b) > 0.1})
talkers = lambda n: talkers_at(n)
def on_screen(n):
    fm = FACE.get(str(int(n)))
    if not fm: return ""
    pp = ", ".join(f"{x['name']} ({x['pos']}, {x['view']})" for x in fm["persons"])
    tk = ", ".join(f"{x['name']} {x['from_s']:.1f}-{x['to_s']:.1f}" for x in fm["talking"] if x["name"] != "unknown face")
    return f" | ON SCREEN (face map): {pp or 'nobody'}" + (f"; talking: {tk}" if tk else "; nobody talks")
WRONG = []
for d in dub:
    sp_ = SPK["lines"].get(str(d["idx"])) if d["dialogue"] else None
    d["speaker"] = sp_ if sp_ in NAMES else None
    d["swap"], d["_wrong"] = False, []
    if not d["speaker"] or d["gap"]: continue
    for a, b in d["_own"]:
        for r in LIB["shots"]:
            o0, o1 = max(a, r["start"]), min(b, r["end"])
            tk = talkers_at(r["n"], o0, o1) if FACE else talkers(r["n"])
            if o1 - o0 > 0.05 and mouth_in(o0, o1) > min(0.3, 0.5 * (o1 - o0)) and tk and d["speaker"] not in tk:
                WRONG.append((o0, o1)); d["swap"] = True; d["_wrong"].append((r["n"], round(o0, 2), round(o1, 2), tk))
for d in dub:
    d["_near"] = minus_iv(d["_near"], WRONG)
    if d["swap"]:
        d["_own"] = merge_iv(minus_iv(d["_own"], WRONG)); _os = sum(b - a for a, b in d["_own"])
        _dur = max((d["end_ms"] - d["start_ms"]) / 1000, 1e-6)
        d["own_speed"] = round(_os / _dur, 2); d["slow"] = d["own_speed"] < SLOW
        d["broll_budget"] = round(max(0.0, _dur - _os / SLOW), 2) if d["slow"] else 0.0
print(f"speakers: {sum(1 for d in dub if d['speaker'])} dialogue lines with a speaker; wrong-speaker lines: "
      + (", ".join(f"{d['idx']} ({d['speaker']}; footage shows {'/'.join(d['_wrong'][0][3])})" for d in dub if d["swap"]) or "none"), flush=True)


# GAP PLAN (owner, 2026-10-29): a NOT FOUND line is not "B-roll only, never a mouth". Claude reads the story and the
# B-roll book (every shot of the line's place: who, type, who talks, what happens) and decides per line WHO must be on
# screen and HOW: "talking" (the line is said out loud by a character on screen -> that character's lip clip, e.g. ep34
# 0:00 Paul's "Anya, bitte!"), "reaction" (a face: whose), "action" (the shot of exactly the event the line tells) or
# "neutral" (the place / a person standing). Cached in W/gap_plan.json; the scene edit follows it and code checks that
# the planned person is on screen and that a talking line starts on that person's moving mouth.
def gap_plan():
    gl = [d for d in dub if d["gap"]]
    if not gl: return {}
    cp = W / "gap_plan.json"; key = f"v1|{len(dub)}|{len(LIB['shots'])}|" + ",".join(str(d["idx"]) for d in gl)
    if cp.exists():
        c = json.load(open(cp))
        if c.get("key") == key: return c["lines"]
    locs = {d.get("loc") for d in gl}; scs = {d.get("scene") for d in gl}
    shots = [r for r in LIB["shots"] if r.get("location") in locs or r["scene"] in scs]

    def shot_line(r):
        m = mouth_in(r["start"], r["end"])
        return (f"shot {r['n']}: {r['start']:.1f}-{r['end']:.1f} s | {', '.join(r['people']) or 'nobody'} | {r['type']}"
                f"{' (talking: ' + ', '.join(talkers(r['n'])) + ')' if talkers(r['n']) else ''} | mouth moving {m:.1f} s | {r['action']} | "
                f"place: {r.get('location')} | scene: {r['scene']}")

    def ctx(d):
        i = dub.index(d)
        return " / ".join(f"[{x['idx']}] {x['text']}" for x in dub[max(0, i - 3):i + 4])
    q = (f"A dubbed episode retells the story of a source video. Characters (source names; the dub may call them differently): "
         f"{'; '.join(c['name'] + ': ' + c['look'][:120] for c in cast)}.\n"
         "The dub lines below have NO matching source line (the adaptation added them), so the picture must be chosen from the "
         "story. For EACH line decide like a film editor what the viewer must see while it is heard:\n"
         "- show \"talking\": the line is spoken out loud by a character who is in the scene -> that character's lip clip "
         "(a shot where THAT character talks, at the same place). Never another character talking.\n"
         "- show \"reaction\": a face listening / reacting (say whose: the person the line is about, or the listener).\n"
         "- show \"action\": the shot of exactly the event the line tells (an object, a movement, e.g. 'the spy in the car').\n"
         "- show \"neutral\": the place or a person standing, when nothing more exact fits.\n"
         "\"who\" = the source name of the person on screen (\"\" for none). \"shots\" = the best shots from the list, best "
         "first (at most 4, only shots at the line's place).\n"
         "Answer only JSON: {\"lines\": [{\"line\": n, \"show\": \"talking|reaction|action|neutral\", \"who\": \"name\", "
         "\"shots\": [n, ...], \"why\": \"at most 15 words\"}]}\n\nLINES WITHOUT A SOURCE LINE:\n" +
         "\n".join(f"line {d['idx']} ({'DIALOGUE' + (' said by ' + d['speaker'] if d.get('speaker') else '') if d['dialogue'] else 'narration'}; "
                   f"place: {d.get('loc')}; scene: {d.get('scene')}): \"{d['text']}\"\n   story around it: {ctx(d)}" for d in gl) +
         "\n\nSHOT LIST (the B-roll book for these places):\n" + "\n".join(shot_line(r) for r in shots))
    res = []
    for attempt in range(3):
        try:
            text, _ = ai.chat(q, model=MODEL or None, max_tokens=8000)
            res = (ai.extract_json(text, "lines") or {}).get("lines") or []
            if res: break
        except Exception as ex:
            print("gap plan: reply not readable, asking again", str(ex)[:80], flush=True)
    low = {n.lower(): n for n in NAMES}; out = {}
    for x in res:
        try:
            k = str(int(x["line"]))
            out[k] = dict(show=str(x.get("show", "neutral")).lower(), who=low.get(str(x.get("who", "")).strip().lower(), ""),
                          shots=[int(n) for n in (x.get("shots") or []) if int(n) in SH][:4], why=str(x.get("why", ""))[:120])
        except Exception:
            pass
    if out: json.dump(dict(key=key, lines=out), open(cp, "w"), indent=1, ensure_ascii=False)
    return out


GPLAN = gap_plan()
for d in dub:
    d["spk_ok"] = d["swap"]
    gp_ = GPLAN.get(str(d["idx"])) if d["gap"] else None
    if not gp_: continue
    d["gplan"] = gp_
    if gp_["show"] == "talking" and gp_["who"] and d["dialogue"]:
        d["speaker"] = gp_["who"]; d["spk_ok"] = True          # its speaker's lip clip (a SPEAKER shot)
print("gap plan: " + ("; ".join(f"{k}: {v['show']} {v['who']} {v['shots']}" for k, v in GPLAN.items()) or "no not-matched lines"), flush=True)


# STORY ORDER INSIDE A GROUP (owner, 2026-10-29, TGS ep34 0:05-0:28): Step 2 joins dub rows that tell the same source
# lines into one group (rows 3-6 = source S1-S4) and the automatic edit spreads that footage EVENLY over the rows, so
# "Ava shoved the photo in his face" (S1, the phone thrust) landed on row 3 ("over the past weeks she had belittled him")
# instead of row 5 ("she waved the phone wildly in front of his face") / row 6 ("but he recognized it at once"). In the
# scene edit the rows of a group SHARE the group's own footage: Claude places it in story order (each action on the row
# that tells it); speed and B-roll allowance are counted for the whole group; a stretch never cuts through a group.
def load_groups():
    for p_ in (W, W.parent, W.parent.parent):
        f = p_ / "decisions.json"
        if f.exists():
            try: return [[int(x) for x in g.get("members") or []] for g in json.load(open(f)).get("groups") or []]
            except Exception: return []
    return []


BYIDX = {d["idx"]: d for d in dub}
for _mem in load_groups():
    _ms = [BYIDX[i] for i in _mem if i in BYIDX and not BYIDX[i]["gap"]]
    if len(_ms) < 2: continue
    _pool = merge_iv([iv for d in _ms for iv in d["_own"]])
    _os = sum(b - a for a, b in _pool); _dur = sum((d["end_ms"] - d["start_ms"]) / 1000 for d in _ms)
    _sp = round(_os / max(_dur, 1e-6), 2); _slow = _sp < SLOW
    _bud = round(max(0.0, _dur - _os / SLOW), 2) if _slow else 0.0
    for d in _ms:
        d["_grp"], d["_gown"] = [x["idx"] for x in _ms], _pool
        d["_near"] = merge_iv(d["_near"] + _pool)
        d["own_speed"], d["slow"], d["broll_budget"] = _sp, _slow, _bud
same_grp = lambda a, b: bool(a.get("_grp")) and b["idx"] in a["_grp"]
print(f"groups: {sum(1 for d in dub if d.get('_grp') and d['_grp'][0] == d['idx'])} groups of lines share their footage", flush=True)


def spk_piece(e, r=None, L=None):
    """a talking shot of the speaker of a wrong-speaker line, played while that line is heard (a SPEAKER shot)"""
    r = r or SH.get(int(e["shot"]))
    if not r: return False
    L = piece_lines(e) if L is None else L
    sw = [d for d in L if d.get("spk_ok")]
    return bool(sw) and all(d["speaker"] in talkers(r["n"]) for d in sw)


def wrong_mouth(e, r, lines):
    """[(line, talkers)] where this piece shows someone else talking while a dialogue line is said"""
    out = []
    if not talkers(r["n"]): return out
    sp = (e["to"] - e["from"]) / max(e["t1"] - e["t0"], 1e-6)
    for d in lines:
        if not d.get("speaker"): continue
        a, b = max(e["t0"], d["start_ms"] / 1000), min(e["t1"], d["end_ms"] / 1000)
        if b - a <= 0.1: continue
        sa, sb = e["from"] + (a - e["t0"]) * sp, e["from"] + (b - e["t0"]) * sp
        tk = talkers_at(r["n"], sa, sb) if FACE else talkers(r["n"])
        if tk and mouth_in(sa, sb) > min(0.3, 0.5 * (sb - sa)) and d["speaker"] not in tk: out.append((d, tk))
    return out


def line_before(t):
    return next((d for d in reversed(dub) if d["end_ms"] / 1000 <= t + 0.05), None)


def line_after(t):
    return next((d for d in dub if d["start_ms"] / 1000 >= t - 0.05), None)


def piece_lines(e):
    L = [d for d in dub if ov(e["t0"], e["t1"], d["start_ms"] / 1000, d["end_ms"] / 1000) > 0.1]
    return L or [d for d in (line_before(e["t0"]), line_after(e["t1"])) if d]          # a pause: the lines around it


def is_broll(e, L):
    """the piece shows footage that is not the own footage of these lines or their neighbours"""
    own = merge_iv([(a - OWN_PAD, b + OWN_PAD) for d in L for a, b in d["_near"]])
    return sum(ov(a, b, e["from"], e["to"]) for a, b in own) < OWN_FRAC * (e["to"] - e["from"])


def fix_short_broll(edl):
    """lengthen every B-roll piece shorter than MIN_BROLL to MIN_BROLL, taking the time from the clip before it (else the
    clip after it). Returns (new edl, notes)."""
    edl = sorted([dict(e) for e in edl], key=lambda e: e["t0"]); notes = []
    for i, e in enumerate(edl):
        d = e["t1"] - e["t0"]; r = SH.get(int(e["shot"]))
        if not r or d <= 0 or d >= MIN_BROLL - 0.01 or not is_broll(e, piece_lines(e)): continue
        if r["end"] - r["start"] < MIN_BROLL * SPEED_LO - 0.01: continue        # the shot is too short even at 0.8x
        need = MIN_BROLL - d
        for j in (i - 1, i + 1):
            if not 0 <= j < len(edl): continue
            n = edl[j]
            if is_broll(n, piece_lines(n)) or (n["t1"] - n["t0"]) - need < MIN_PIECE: continue
            sp = min(max((e["to"] - e["from"]) / d, SPEED_LO), SPEED_HI)
            foot = min(MIN_BROLL * sp, r["end"] - r["start"]); a = e["from"]; b = a + foot
            if b > r["end"]: b = r["end"]; a = max(r["start"], b - foot)
            if j == i - 1: n["t1"] = round(n["t1"] - need, 3); e["t0"] = n["t1"]
            else: n["t0"] = round(n["t0"] + need, 3); e["t1"] = n["t0"]
            e["from"], e["to"] = round(a, 3), round(b, 3)
            nd = n["t1"] - n["t0"]
            if (n["to"] - n["from"]) / nd > SPEED_HI + 0.001: n["to"] = round(n["from"] + SPEED_HI * nd, 3)   # too fast: trim its end
            notes.append(f"B-roll at {e['t0']:.2f} s lengthened {d:.2f} -> {MIN_BROLL:.1f} s; clip {'before' if j < i else 'after'} it now "
                         f"{nd:.2f} s at {(n['to'] - n['from']) / nd:.2f}x")
            break
    return edl, notes


def line_tag(d):
    if d.get("swap"):
        w = d["_wrong"][0]; own = fmt_iv0(d["_own"])
        return (f" [WRONG-SPEAKER: said by {d['speaker']}, but its matched footage (shot {w[0]}, {w[1]:.2f}-{w[2]:.2f}) shows "
                f"{'/'.join(w[3])} talking -- never show that here. Show {d['speaker']} talking: a SPEAKER shot of {d['speaker']} at "
                f"this location (talking: {d['speaker']}), the mouth starting with the voice; or a face that does not talk"
                + (f"; its other own footage {own} may stay" if d["_own"] else "") + "]")
    if d.get("gap") and d.get("gplan"):
        gp_ = d["gplan"]; dur = (d["end_ms"] - d["start_ms"]) / 1000; sh = ", ".join(str(n) for n in gp_["shots"]) or "-"
        if d.get("spk_ok"):
            how = (f"show {gp_['who']} TALKING: a SPEAKER shot of {gp_['who']} at this place (suggested shots {sh}), the mouth starting "
                   f"with the voice; it may repeat talking footage shown elsewhere (in turn with that place's other speaker shots, never right after itself)")
        elif gp_["show"] == "action":
            how = f"show exactly the event it tells (suggested shots {sh}), no talking mouth"
        else:
            how = f"show {gp_['who'] or 'the place'} ({gp_['show']}, suggested shots {sh}), no talking mouth"
        return (f" [NOT FOUND (no source line), story plan: {how} -- {gp_['why'].rstrip('.')}. Whole line {dur:.1f} s; never another line's "
                f"matched footage]")
    if d.get("gap"):
        return (f" [NO source line (gap): B-roll only, for the whole line ({(d['end_ms'] - d['start_ms']) / 1000:.1f} s) -- a reaction / "
                f"neutral shot of this place, or the action / object shot that shows exactly what this line tells (e.g. 'the spy in "
                f"the car' -> the spy shot); never a talking mouth, never another line's matched footage]")
    own = fmt_iv0([(a, b) for a, b in (d.get("_gown") or d["_own"])])
    g = d.get("_grp")
    if g:
        pre = (f"GROUP of lines {g[0]}-{g[-1]}: they SHARE their own footage {own} -- place it in STORY ORDER inside the group "
               f"(each action / event on the line that tells it: compare the source narration over each shot with the lines), "
               f"not spread evenly; the group's other lines show its reaction / neutral footage. Group ")
        if d["slow"]:
            return (f" [{pre}speed {d['own_speed']}x SLOW: own footage first (down to 0.8x) + B-roll for at most "
                    f"{d['broll_budget']:.1f} s over the WHOLE group, each B-roll piece at least {MIN_BROLL} s]")
        return f" [{pre}speed {d['own_speed']}x: ONLY the group's own footage{' (trim it: too fast)' if d['own_speed'] > 1.25 else ''}]"
    if d["slow"] and d["broll_budget"] < MIN_BROLL:
        return (f" [own footage {own} = {d['own_speed']}x SLOW, needs only {d['broll_budget']:.1f} s: EITHER one B-roll of at least "
                f"{MIN_BROLL} s (own footage then faster, up to {SPEED_HI}x) OR no B-roll and own footage slower, down to {SLOW_FLOOR}x]")
    if d["slow"]:
        return (f" [own footage {own} = {d['own_speed']}x SLOW: show it (down to 0.8x) + B-roll for at most {d['broll_budget']:.1f} s, "
                f"each B-roll piece at least {MIN_BROLL} s]")
    return f" [own footage {own} = {d['own_speed']}x: ONLY its own footage{' (trim it: too fast)' if d['own_speed'] > 1.25 else ''}]"


fmt_iv0 = lambda iv: ", ".join(f"{a:.2f}-{b:.2f}" for a, b in iv) or "never"


def check(edl, T0, T1, lines, before):
    probs = []
    if not edl: return ["empty EDL"]
    if abs(edl[0]["t0"] - T0) > 0.02: probs.append(f"first t0 must be {T0}")
    if abs(edl[-1]["t1"] - T1) > 0.02: probs.append(f"last t1 must be {T1}")
    seen = [(e["from"], e["to"], e["t0"], e["t1"], f"earlier piece at dub {e['t0']:.1f} s", SH.get(int(e["shot"]), {}).get("type"), spk_piece(e)) for e in before]
    for k, e in enumerate(edl):
        r = SH.get(int(e["shot"]))
        d = e["t1"] - e["t0"]; u = e["to"] - e["from"]
        if k and abs(e["t0"] - edl[k - 1]["t1"]) > 0.02: probs.append(f"piece {k}: t0 {e['t0']} != previous t1 {edl[k - 1]['t1']}")
        if not r: probs.append(f"piece {k}: shot {e['shot']} is not in the list"); continue
        if e["from"] < r["start"] - 0.05 or e["to"] > r["end"] + 0.05 or u <= 0:
            probs.append(f"piece {k}: from/to {e['from']}-{e['to']} not inside shot {r['n']} ({r['start']:.2f}-{r['end']:.2f})")
        L = piece_lines(e); BR = bool(L) and is_broll(e, L)
        lo = SLOW_FLOOR if (not BR and L and all(x["slow"] and not x.get("gap") for x in L)) else SPEED_LO
        if d < MIN_PIECE - 0.01: probs.append(f"piece {k}: {d:.2f} s is shorter than {MIN_PIECE} s")
        elif u > 0 and not (lo - 0.01 <= u / d <= SPEED_HI + 0.01):
            probs.append(f"piece {k}: speed {u / d:.2f} outside {lo}-{SPEED_HI} (footage {u:.2f} s on {d:.2f} s)")
        if BR and d < MIN_BROLL - 0.01:
            probs.append(f"piece {k}: B-roll of only {d:.2f} s looks jumpy; a B-roll lasts at least {MIN_BROLL} s -- make it longer (the "
                         f"line's own footage may play faster, up to {SPEED_HI}x) or leave it out and let the own footage play slower (down to {SLOW_FLOOR}x)")
        SPKP = BR and spk_piece(e, r, L)
        for a, b, t0_, t1_, name, typ, sk in reversed(seen):
            if ov(a, b, e["from"], e["to"]) > 0.2:
                if SPKP or sk:
                    if abs(e["t0"] - t1_) < SPK_GAP:
                        probs.append(f"piece {k}: repeats talking footage {e['from']:.2f}-{e['to']:.2f} only {abs(e['t0'] - t1_):.1f} s after "
                                     f"{name}; a SPEAKER shot may repeat footage, but never right after itself")
                elif eff_type(e, r) not in ("reaction", "neutral", "establishing"):
                    probs.append(f"piece {k}: shot {r['n']} is a {eff_type(e, r)} shot{' (a mouth moves in this footage)' if eff_type(e, r) != r['type'] else ''} and its footage {e['from']:.2f}-{e['to']:.2f} is already "
                                 f"shown ({name}); action/talking footage is shown once: use other footage or let pieces run slower (down to {SPEED_LO})")
                elif not e.get("reuse"):
                    probs.append(f"piece {k}: footage already shown ({name}); mark reuse or use other footage")
                elif e["t0"] - t1_ < REUSE_GAP:
                    probs.append(f"piece {k}: repeats footage {e['t0'] - t1_:.1f} s after it was shown ({name}); a repeat needs {REUSE_GAP:.0f} s in between")
                break
        seen.append((e["from"], e["to"], e["t0"], e["t1"], f"piece {k}", r["type"], SPKP))
        gap_only = bool(L) and all(x.get("gap") for x in L)        # owner 2026-10-29: a NOT FOUND line may show the shot of
        if BR and not SPKP and (eff_type(e, r) == "talking" or                    # exactly what it tells (an action / object, e.g. the spy)
                   (eff_type(e, r) not in ("reaction", "neutral", "establishing") and not gap_only)):
            probs.append(f"piece {k}: B-roll must be a reaction / neutral / establishing shot without a moving mouth; shot {r['n']} "
                         f"source {e['from']:.2f}-{e['to']:.2f} is {eff_type(e, r)} footage")
        for d in (lines if BR else []):
            if not d.get("loc") or ov(e["t0"], e["t1"], d["start_ms"] / 1000, d["end_ms"] / 1000) < 0.1: continue
            if r.get("location") == d["loc"] or (r["scene"] == d.get("scene") and (r.get("location") != "mixed" or d["loc"] == "mixed")): continue
            if r["scene"] == d.get("scene") and any(ov(a, b, e["from"], e["to"]) > 0.2 for a, b in d["_near"]): continue   # a shot with a
            # flashback cut inside (place "gym (with a flashback ...)") is fine where the automatic match put that footage
            probs.append(f"piece {k}: shot {r['n']} is at location \"{r.get('location')}\" but line {d['idx']} plays at \"{d['loc']}\"; "
                         f"show only the line's location (same scene, or a reaction/neutral shot of the same location) -- never another background")
            break
        if BR and not any(d["broll_budget"] > 0 for d in L):
            probs.append(f"piece {k}: shot {r['n']} source {e['from']:.2f}-{e['to']:.2f} is B-roll (not the own footage of line(s) "
                         f"{', '.join(str(d['idx']) for d in L)}), but {'that line is' if len(L) == 1 else 'those lines are'} not slow "
                         f"({', '.join(str(d['own_speed']) + 'x' for d in L)}): B-roll is ONLY for slow lines (below 0.8x); show the line's own "
                         f"footage ({'; '.join(fmt_iv0(d['_own']) for d in L)}), a shorter part of it if it is too fast")
        for d, who in wrong_mouth(e, r, lines):
            probs.append(f"piece {k}: shot {r['n']} shows {'/'.join(who)} talking while dialogue line {d['idx']} is said by {d['speaker']}; "
                         f"a moving mouth must be the speaker's: show {d['speaker']} talking (a SPEAKER shot of this place) or a face that does not talk")
        for d, sa, sb, m in (narration_mouth(e, lines) if BR else []):
            probs.append(f"piece {k}: shot {r['n']} shows a moving mouth ({m:.1f} s in source {sa:.2f}-{sb:.2f}; mouth moving at "
                         f"{fmt_iv0([(max(x, e['from']), min(y, e['to'])) for x, y in lip_iv if ov(x, y, e['from'], e['to']) > 0.05])}) while "
                         f"narration line {d['idx']} is heard; never a talking mouth over narration: use footage where nobody's mouth moves")
    for d in lines:                                 # a not-matched line shows the person its story plan names
        gp_ = d.get("gplan")
        if not gp_ or not gp_["who"] or not (T0 <= (d["start_ms"] + d["end_ms"]) / 2000 < T1): continue
        a0, a1 = d["start_ms"] / 1000, d["end_ms"] / 1000
        on = sum(ov(e["t0"], e["t1"], a0, a1) for e in edl if gp_["who"] in SH.get(int(e["shot"]), {}).get("people", []))
        if on < 0.5 * (a1 - a0):
            probs.append(f"not-matched line {d['idx']} \"{d['text'][:40]}\": its story plan shows {gp_['who']} ({gp_['show']}, suggested "
                         f"shots {gp_['shots']}), but {gp_['who']} is on screen only {on:.1f} of {a1 - a0:.1f} s")
    brs = [(k, e) for k, e in enumerate(edl) if SH.get(int(e["shot"])) and is_broll(e, piece_lines(e))]
    cnt = {}
    for k, e in brs:                                # B-roll: at most MAX_BROLL_USES per shot, used in turn
        n = int(e["shot"]); cnt[n] = cnt.get(n, 0) + 1
        if cnt[n] == MAX_BROLL_USES + 1:
            probs.append(f"piece {k}: B-roll shot {n} is used {cnt[n]} times in this stretch; a B-roll shot is used at most {MAX_BROLL_USES} "
                         f"times in the episode -- use another B-roll shot of this place")
    for (k0, e0), (k, e) in zip(brs, brs[1:]):
        if int(e["shot"]) != int(e0["shot"]): continue
        r = SH[int(e["shot"])]
        alt = [x["n"] for x in LIB["shots"] if x["n"] != r["n"] and x.get("location") == r.get("location") and x["broll"] == "yes"
               and x["type"] in SHARED and x.get("location") != "mixed"]
        if alt:
            probs.append(f"piece {k}: B-roll shot {r['n']} again right after piece {k0} used it; use this place's B-roll shots in turn "
                         f"(e.g. {', '.join(str(a) for a in alt[:4])}) so it does not look repeated")
    done_g = set()
    for d in lines:                                 # a slow line (or group): own footage first, B-roll only for the rest
        if d["broll_budget"] <= 0 or not (T0 <= (d["start_ms"] + d["end_ms"]) / 2000 < T1): continue
        grp = tuple(d.get("_grp") or [d["idx"]])
        if grp in done_g: continue
        done_g.add(grp); ms = [x for x in lines if x["idx"] in grp] or [d]
        used = sum(ov(e["t0"], e["t1"], x["start_ms"] / 1000, x["end_ms"] / 1000) for x in ms for e in edl if is_broll(e, [x]))
        if used > max(d["broll_budget"], MIN_BROLL) + 0.75:
            who = f"lines {grp[0]}-{grp[-1]} (one group)" if len(grp) > 1 else f"line {d['idx']}"
            probs.append(f"{who} show {used:.1f} s of B-roll but need at most {d['broll_budget']:.1f} s (own footage "
                         f"{fmt_iv0(d.get('_gown') or d['_own'])} plays at {d['own_speed']}x): show more of the own footage, down to 0.8x, before B-roll")
    for d in lines:                                 # dialogue: a moving mouth must start within LIP_TOL of the voice
        if not d["dialogue"] or (d.get("gap") and not d.get("spk_ok")): continue  # a gap line has no source footage of its speaker: B-roll only
        ls = d["start_ms"] / 1000
        if not (T0 <= ls < T1): continue
        e = next((e for e in edl if e["t0"] - 0.01 <= ls < e["t1"]), None)
        if not e: continue
        sp = (e["to"] - e["from"]) / max(e["t1"] - e["t0"], 1e-6)
        src_at = e["from"] + (ls - e["t0"]) * sp
        lips = [(a, b) for a, b in lip_iv if ov(a, b, e["from"], e["to"]) > 0.05]
        starts = [max(a, e["from"]) for a, b in lips]
        good = any(abs(st - src_at) / sp <= LIP_TOL for st in starts) or any(a < src_at - 0.05 < b for a, b in lips)
        if not good:
            probs.append(f"dialogue line {d['idx']} \"{d['text'][:40]}\" starts at {ls:.2f} s but the piece on screen then (shot "
                         f"{e['shot']}, source {src_at:.2f} s) has no mouth starting to move there (mouth moving: "
                         f"{', '.join(f'{a:.2f}-{b:.2f}' for a, b in lips) or 'never'})")
    return probs


def cut_window(T0, T1, before, cache, wkey):
    lines = [d for d in dub if d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1]
    auto_w = [s for s in auto if ov(T0 * 1000, T1 * 1000, s["it_start_ms"], s["it_end_ms"]) > 0]
    used_src = [(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000) for s in auto_w if not s.get("video")]
    hi = max(b for a, b in used_src)
    scenes = {SH[n]["scene"] for n in SH if any(ov(SH[n]["start"], SH[n]["end"], a, b) > 0.05 for a, b in used_src)}
    scenes |= {d["scene"] for d in dub if d.get("scene") and d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1}
    shots = [r for r in LIB["shots"] if r["scene"] in scenes and (r["start"] <= hi + 40 or r["broll"] == "yes")
             and (r.get("location") != "mixed" or any(ov(r["start"], r["end"], a, b) > 0.05 for a, b in used_src))]
    if HAS_LOC:
        locs = {d["loc"] for d in dub if d.get("loc") and d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1}
        mid = sum(a + b for a, b in used_src) / (2 * len(used_src))
        more = sorted((r for r in LIB["shots"] if r["scene"] not in scenes and r.get("location") in locs and r["broll"] == "yes"
                       and r["type"] in ("reaction", "neutral", "establishing")), key=lambda r: abs(r["start"] - mid))[:12]
        shots = sorted(shots + more, key=lambda r: r["n"])
    prev = before[-3:]
    used_txt = "\n".join(f"- shot {e['shot']} source {e['from']:.2f}-{e['to']:.2f} shown at dub {e['t0']:.1f}-{e['t1']:.1f} s"
                         for e in before if int(e["shot"]) in {r["n"] for r in shots}) or "- none"
    line_txt = "\n".join(f"line {d['idx']}: {d['start_ms'] / 1000:.2f}-{d['end_ms'] / 1000:.2f} s  "
                         f"{('DIALOGUE said by ' + d['speaker']) if d['dialogue'] and d.get('speaker') else 'DIALOGUE' if d['dialogue'] else 'narration'}{' [scene: ' + str(d.get('scene')) + ', location: ' + d['loc'] + ']' if d.get('loc') else ''}{line_tag(d)}: {d['text']}" for d in lines)
    head = (prompt(T0, T1) + "\n\nDUB LINES (voice times):\n" + line_txt +
            "\n\nTHE LAST PIECES BEFORE THIS STRETCH (continue from them):\n" +
            ("\n".join(f"- {e['t0']:.1f}-{e['t1']:.1f} s shot {e['shot']} source {e['from']:.2f}-{e['to']:.2f}: {e.get('why', '')[:120]}"
                       for e in prev) or "- this is the start") +
            "\n\nFOOTAGE OF THESE SHOTS ALREADY SHOWN EARLIER IN THE EPISODE:\n" + used_txt +
            "\n\nSOURCE SHOTS:\n" + "\n".join(shot_txt(r, auto_w) for r in shots) + "\n\nFrames of each shot (3 frames, left to right):")
    content = [{"type": "text", "text": head}]
    for r in shots:
        content += [{"type": "text", "text": f"shot {r['n']}:"}, {"type": "image_url", "image_url": {"url": b64(r["n"])}}]
    extra, rounds, edl = "", [], []
    for rnd in range(4):
        key = f"{wkey}|round{rnd}"
        if key in cache:
            ans = cache[key]
        else:
            c = content + ([{"type": "text", "text": extra}] if extra else [])
            ans = {}
            for attempt in range(3):                       # a cut-off or broken reply is asked again
                try:
                    text, _ = ai.chat(c, model=MODEL or None, max_tokens=24000)
                    ans = ai.extract_json(text, "edl") or {}
                    if ans.get("edl"): break
                except Exception as ex:
                    print(f"  window {T0:.1f}-{T1:.1f}: reply not readable ({str(ex)[:80]}), asking again", flush=True)
            cache[key] = ans; json.dump(cache, open(OUTM.with_suffix(".cache.json"), "w"), indent=1, ensure_ascii=False)
        try:
            edl = [dict(e, t0=float(e["t0"]), t1=float(e["t1"]), **{"from": float(e["from"]), "to": float(e["to"])}) for e in ans.get("edl") or []]
        except Exception as ex:
            edl = []
        probs = check(edl, T0, T1, lines, before)
        rounds.append(dict(round=rnd, problems=probs, notes=ans.get("notes", "")))
        print(f"  window {T0:.1f}-{T1:.1f}: round {rnd}: {len(edl)} pieces, {len(probs)} problems" + (": " + " | ".join(probs[:4]) if probs else ""), flush=True)
        if not probs: break
        extra = ("Your previous EDL:\n" + json.dumps(ans.get("edl"), ensure_ascii=False) + "\n\nIt has these problems, fix them all "
                 "and answer the complete corrected JSON again:\n- " + "\n- ".join(probs))
    return edl, rounds, lines, [r["n"] for r in shots]


# =====================================================================================================================
# PARALLEL MODE (--parallel N, owner 2026-10-27): every stretch is cut at the same time, without waiting for the one before.
#   1. stretches are cut at scene changes first (different scenes share no shots), then about every WIN s inside a scene;
#   2. inside a scene the footage is shared out BEFORE the calls: the action/talking/object seconds the automatic match
#      gave to a stretch belong to it (free seconds of a shot go to the nearest owner); reaction/neutral/establishing shots
#      are shared by all stretches;
#   3. the context at the joins comes from the automatic edit (its pieces just before/after), not from Claude's real cut;
#   4. after all replies the software joins them and checks the whole episode (action footage twice, reaction repeats
#      closer than 8 s, jump cuts at the joins); only the stretches with a problem go back to Claude, also in parallel,
#      now with the real neighbouring pieces. Repeats of shared shots are marked (zoom 8%) by the code, not by Claude.
# =====================================================================================================================
import threading, time
from concurrent.futures import ThreadPoolExecutor

PAR = int(opt("--parallel", 0))
SHARED = ("reaction", "neutral", "establishing")
LOCK = threading.Lock()
USAGE = dict(calls=0, prompt_tokens=0, completion_tokens=0, call_seconds=0.0)
fmt_iv = lambda iv: ", ".join(f"{a:.2f}-{b:.2f}" for a, b in iv)


def ask_claude(c):
    t = time.time()
    text, u = ai.chat(c, model=MODEL or None, max_tokens=32000)
    with LOCK:
        USAGE["calls"] += 1; USAGE["call_seconds"] += time.time() - t
        USAGE["prompt_tokens"] += int((u or {}).get("prompt_tokens") or 0); USAGE["completion_tokens"] += int((u or {}).get("completion_tokens") or 0)
    return text


def scene_at(t):
    return next((r["scene"] for r in LIB["shots"] if r["start"] <= t < r["end"]), None)


def line_scene(d):
    tot = {}
    for s in auto:
        o = ov(d["start_ms"], d["end_ms"], s["it_start_ms"], s["it_end_ms"])
        if o > 0 and not s.get("video"):
            sc = scene_at((s["us_start_ms"] + s["us_end_ms"]) / 2000); tot[sc] = tot.get(sc, 0) + o
    return max(tot, key=tot.get) if tot else None


def windows_par(a0, a1):
    L = [d for d in dub if d["end_ms"] / 1000 > a0 and d["start_ms"] / 1000 < a1]
    sc = [line_scene(d) for d in L]
    br = [a0]
    for i in range(len(L) - 1):
        if sc[i] and sc[i + 1] and sc[i] != sc[i + 1] and not same_grp(L[i], L[i + 1]):
            g = round((L[i]["end_ms"] + L[i + 1]["start_ms"]) / 2000, 2)
            if g - br[-1] >= 8 and a1 - g >= 8: br.append(g)          # a scene bit under 8 s stays with its neighbour
    br.append(a1)
    out = []
    for s0, s1 in zip(br, br[1:]): out += windows(s0, s1)
    return out


def cands_for(T0, T1):
    auto_w = [s for s in auto if ov(T0 * 1000, T1 * 1000, s["it_start_ms"], s["it_end_ms"]) > 0]
    used_src = [(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000) for s in auto_w if not s.get("video")]
    if not used_src:
        near = min((s for s in auto if not s.get("video")), key=lambda s: abs(s["it_start_ms"] / 1000 - T0))
        used_src = [(near["us_start_ms"] / 1000, near["us_end_ms"] / 1000)]
    hi = max(b for a, b in used_src)
    scenes = {SH[n]["scene"] for n in SH if any(ov(SH[n]["start"], SH[n]["end"], a, b) > 0.05 for a, b in used_src)}
    scenes |= {d["scene"] for d in dub if d.get("scene") and d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1}
    shots = [r for r in LIB["shots"] if r["scene"] in scenes and (r["start"] <= hi + 40 or r["broll"] == "yes")
             and (r.get("location") != "mixed" or any(ov(r["start"], r["end"], a, b) > 0.05 for a, b in used_src))]
    if HAS_LOC:
        locs = {d["loc"] for d in dub if d.get("loc") and d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1}
        mid = sum(a + b for a, b in used_src) / (2 * len(used_src))
        more = sorted((r for r in LIB["shots"] if r["scene"] not in scenes and r.get("location") in locs and r["broll"] == "yes"
                       and r["type"] in ("reaction", "neutral", "establishing")), key=lambda r: abs(r["start"] - mid))[:12]
        shots = sorted(shots + more, key=lambda r: r["n"])
    sw = [d for d in dub if d.get("spk_ok") and d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1]
    if sw:                                           # SPEAKER shots: the speaker talking at the line's location, unused footage first
        want = {d["speaker"] for d in sw}; locs = {d["loc"] for d in sw if d.get("loc")}; scs = {d["scene"] for d in sw if d.get("scene")}
        have = {r["n"] for r in shots}; mid = (T0 + T1) / 2
        def unused(r):
            return (r["end"] - r["start"]) - sum(ov(s["us_start_ms"] / 1000, s["us_end_ms"] / 1000, r["start"], r["end"]) for s in auto if not s.get("video"))
        sp_ = sorted((r for r in LIB["shots"] if set(talkers(r["n"])) & want and r.get("location") != "mixed"
                      and (r.get("location") in locs or r["scene"] in scs) and r["n"] not in have),
                     key=lambda r: (-min(unused(r), 2.0), abs(r["start"] - mid)))[:8]
        shots = sorted(shots + sp_, key=lambda r: r["n"])
    have = {r["n"] for r in shots}
    for d in dub:
        if d.get("gplan") and d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1:
            for n in d["gplan"]["shots"]:
                if n not in have: shots.append(SH[n]); have.add(n)
    shots = sorted(shots, key=lambda r: r["n"])
    return auto_w, used_src, shots


def ownership(wins, cand, used):
    """(shot, stretch) -> source intervals of a talking/action/object shot that stretch may use"""
    def w_of(s):
        m = (s["it_start_ms"] + s["it_end_ms"]) / 2000
        return next((k for k, (a, b) in enumerate(wins) if a <= m < b), None)
    marks = {}
    for s in auto:
        k = w_of(s)
        if s.get("video") or k is None: continue
        a, b = s["us_start_ms"] / 1000, s["us_end_ms"] / 1000
        for r in LIB["shots"]:
            if ov(a, b, r["start"], r["end"]) > 0.05:
                marks.setdefault(r["n"], []).append((max(a, r["start"]), min(b, r["end"]), k))
    own = {}
    for r in LIB["shots"]:
        if r["type"] in SHARED: continue
        n = r["n"]; ws = [k for k in range(len(wins)) if n in cand[k]]
        m = sorted(marks.get(n, []), key=lambda x: (x[0] + x[1]) / 2)
        if not m:
            if ws:
                c = (r["start"] + r["end"]) / 2
                k = min(ws, key=lambda k: min((abs(c - (a + b) / 2) for a, b in used[k]), default=1e9))
                own[(n, k)] = [(r["start"], r["end"])]
            continue
        bnd = []
        for i in range(len(m) - 1):
            x = (m[i][1] + m[i + 1][0]) / 2
            bnd.append(max(x, bnd[-1]) if bnd else x)
        edges = [r["start"]] + bnd + [r["end"]]
        regs = []
        for i, (_, _, k) in enumerate(m):
            lo, hi = edges[i], edges[i + 1]
            if regs and regs[-1][2] == k: regs[-1][1] = hi
            else: regs.append([lo, hi, k])
        for lo, hi, k in regs:
            if hi - lo > 0.05: own.setdefault((n, k), []).append((round(lo, 2), round(hi, 2)))
    return own


def auto_reuse(pieces):
    """pieces in dub order: a piece whose footage was already shown is a repeat (zoomed 8% at render)"""
    for e in pieces: e["reuse"] = False
    for j, e in enumerate(pieces):
        for f in pieces[:j]:
            if ov(f["from"], f["to"], e["from"], e["to"]) > 0.2:
                if spk_piece(f) and not spk_piece(e): f["reuse"] = True     # the SPEAKER piece is the repeat
                else: e["reuse"] = True


def parse_edl(ans):
    try:
        return [dict(e, t0=float(e["t0"]), t1=float(e["t1"]), **{"from": float(e["from"]), "to": float(e["to"])}) for e in ans.get("edl") or []]
    except Exception:
        return []


def check_par(edl, T0, T1, lines, k, own, others=(), prev_last=None):
    edl = sorted(edl, key=lambda e: e["t0"]); auto_reuse(edl)
    probs = check(edl, T0, T1, lines, [])                       # tiling, inside shot, speed, length, repeats inside, mouths
    for i, e in enumerate(edl):
        r = SH.get(int(e["shot"]))
        if not r or r["type"] in SHARED or spk_piece(e, r): continue
        regs = own.get((r["n"], k), [])
        if i == 0 and prev_last and int(prev_last["shot"]) == r["n"] and abs(e["from"] - prev_last["to"]) <= 0.3:
            regs = regs + [(prev_last["to"] - 0.3, e["to"])]   # continuing the shot the stretch before ends on (no jump cut)
        if (e["to"] - e["from"]) - sum(ov(a, b, e["from"], e["to"]) for a, b in regs) > 0.2:
            probs.append(f"piece {i}: shot {r['n']} is a {r['type']} shot and footage {e['from']:.2f}-{e['to']:.2f} is outside what this "
                         f"stretch may use ({fmt_iv(regs) if regs else 'nothing: all of it belongs to other stretches'}); use that part, "
                         f"a reaction/neutral shot, or let pieces run slower (down to {SPEED_LO})")
    for i, e in enumerate(edl):
        r = SH.get(int(e["shot"]))
        if not r: continue
        hits = [f for f in others if ov(f["from"], f["to"], e["from"], e["to"]) > 0.2]
        if not hits: continue
        f = min(hits, key=lambda f: abs(f["t0"] - e["t0"]))
        if spk_piece(e, r) or spk_piece(f):
            gap = e["t0"] - f["t1"] if f["t1"] <= e["t0"] else f["t0"] - e["t1"]
            if gap < SPK_GAP:
                probs.append(f"piece {i}: talking footage {e['from']:.2f}-{e['to']:.2f} (shot {r['n']}) is shown by another stretch at dub "
                             f"{f['t0']:.1f}-{f['t1']:.1f} s ({max(gap, 0):.1f} s away); a SPEAKER shot may repeat footage, but never right after itself")
        elif eff_type(e, r) not in SHARED:
            probs.append(f"piece {i}: footage {e['from']:.2f}-{e['to']:.2f} of {r['type']} shot {r['n']} is already shown at dub "
                         f"{f['t0']:.1f}-{f['t1']:.1f} s by another stretch; action/talking footage is shown once")
        else:
            gap = e["t0"] - f["t1"] if f["t1"] <= e["t0"] else f["t0"] - e["t1"]
            if gap < REUSE_GAP:
                probs.append(f"piece {i}: repeats footage {e['from']:.2f}-{e['to']:.2f} that another stretch shows at dub "
                             f"{f['t0']:.1f}-{f['t1']:.1f} s ({max(gap, 0):.1f} s away); a repeat needs {REUSE_GAP:.0f} s in between")
    if prev_last and edl and int(edl[0]["shot"]) == int(prev_last["shot"]) and abs(edl[0]["from"] - prev_last["to"]) > 0.3:
        probs.append(f"piece 0: the stretch before ends on shot {prev_last['shot']} at source {prev_last['to']:.2f}; starting the same "
                     f"shot at {edl[0]['from']:.2f} is a jump cut: continue from {prev_last['to']:.2f} or start with another shot")
    return probs


def global_check(res):
    allp = sorted([dict(e, _w=k) for k, edl in enumerate(res) for e in edl], key=lambda e: e["t0"])
    auto_reuse(allp)
    probs = {k: [] for k in range(len(res))}
    for j, e in enumerate(allp):
        r = SH.get(int(e["shot"]), {})
        earlier = [f for f in allp[:j] if f["_w"] != e["_w"] and ov(f["from"], f["to"], e["from"], e["to"]) > 0.2]
        if not earlier: continue
        f = earlier[-1]
        if spk_piece(e) or spk_piece(f):
            if e["t0"] - f["t1"] < SPK_GAP:
                probs[e["_w"]].append(f"the piece at dub {e['t0']:.1f} s repeats talking footage {e['from']:.2f}-{e['to']:.2f} (shot {e['shot']}) shown "
                                      f"at dub {f['t0']:.1f} s; a SPEAKER shot may repeat footage, but never right after itself")
        elif (eff_type(e, r) if r else None) not in SHARED:
            probs[e["_w"]].append(f"the piece at dub {e['t0']:.1f} s shows {eff_type(e, r) if r else '?'} footage {e['from']:.2f}-{e['to']:.2f} (shot {e['shot']}) "
                                  f"that is already shown at dub {f['t0']:.1f} s; action/talking footage is shown once")
        elif e["t0"] - f["t1"] < REUSE_GAP:
            probs[e["_w"]].append(f"the piece at dub {e['t0']:.1f} s repeats footage {e['from']:.2f}-{e['to']:.2f} (shot {e['shot']}) shown at dub "
                                  f"{f['t0']:.1f}-{f['t1']:.1f} s, only {e['t0'] - f['t1']:.1f} s before; a repeat needs {REUSE_GAP:.0f} s in between")
    uses = {}
    for e in allp:                                  # B-roll shot used more than MAX_BROLL_USES times in the episode
        if SH.get(int(e["shot"])) and is_broll(e, piece_lines(e)):
            uses.setdefault(int(e["shot"]), []).append(e)
    for n, es in uses.items():
        for e in es[MAX_BROLL_USES:]:
            at = ", ".join("%.1f" % x["t0"] for x in es if x is not e)
            probs[e["_w"]].append(f"the B-roll at dub {e['t0']:.1f} s is shot {n}'s use number {es.index(e) + 1} in the episode (also at dub "
                                  f"{at} s); at most {MAX_BROLL_USES} -- use another B-roll shot of this place, in turn")
    for k in range(1, len(res)):
        if res[k] and res[k - 1]:
            a, b = res[k - 1][-1], res[k][0]
            if int(a["shot"]) == int(b["shot"]) and abs(b["from"] - a["to"]) > 0.3:
                probs[k].append(f"the stretch before ends on shot {a['shot']} at source {a['to']:.2f}; your first piece starts the same shot at "
                                f"{b['from']:.2f}: a jump cut. Continue from {a['to']:.2f} or start with another shot")
    return probs, allp


def run_par(A0, A1):
    try:                                             # fail fast when the AI gateway cannot be reached (VPN off)
        ai.key_info()
    except Exception as ex:
        sys.exit(f"AI gateway not reachable ({str(ex)[:120]}): connect the VPN and run again")
    t_start = time.time()
    wins = windows_par(A0, A1)
    info = [cands_for(T0, T1) for T0, T1 in wins]
    cand = [{r["n"] for r in x[2]} for x in info]; used = [x[1] for x in info]
    own = ownership(wins, cand, used)
    cache_p = OUTM.with_suffix(".par_cache.json"); cache = json.load(open(cache_p)) if cache_p.exists() else {}
    LINES = [[d for d in dub if d["end_ms"] / 1000 > T0 and d["start_ms"] / 1000 < T1] for T0, T1 in wins]
    print(f"parallel: {len(wins)} stretches ({len({line_scene(d) for d in dub})} scenes), {PAR} calls at a time", flush=True)

    def base_content(k):
        T0, T1 = wins[k]; auto_w, _, shots = info[k]
        def auto_pc(s):
            return f"- {s['it_start_ms'] / 1000:.1f}-{s['it_end_ms'] / 1000:.1f} s source {s['us_start_ms'] / 1000:.2f}-{s['us_end_ms'] / 1000:.2f} (shot {next((r['n'] for r in LIB['shots'] if r['start'] <= (s['us_start_ms'] + s['us_end_ms']) / 2000 < r['end']), '?')})"
        before = [s for s in auto if s["it_end_ms"] <= T0 * 1000 + 1 and not s.get("video")][-3:]
        after = [s for s in auto if s["it_start_ms"] >= T1 * 1000 - 1 and not s.get("video")][:2]
        def stxt(r):
            t = shot_txt(r, auto_w)
            if r["type"] in SHARED:
                return t + " | SHARED shot: may be used (repeats are fine 8 s apart)" + (
                    "; its mouth moves at the times above: never those seconds over narration" if mouth_in(r["start"], r["end"]) > 0.3 else "")
            regs = own.get((r["n"], k))
            sw = [d for d in LINES[k] if d.get("spk_ok") and d["speaker"] in talkers(r["n"])]
            if sw:
                return t + (f" | SPEAKER shot for wrong-speaker line(s) {', '.join(str(d['idx']) for d in sw)}: may be shown there even where "
                            f"its footage is shown elsewhere (in turn with the other speaker shots, never right after itself)"
                            + (f"; for other lines only {fmt_iv(regs)}" if regs else "; not for other lines"))
            gl = [d for d in LINES[k] if d.get("gplan") and r["n"] in d["gplan"]["shots"]]
            if gl:
                return t + (f" | suggested for not-matched line(s) {', '.join(str(d['idx']) for d in gl)}: may be shown there"
                            + (f"; for other lines only {fmt_iv(regs)}" if regs else "; not for other lines"))
            return t + (f" | footage for THIS stretch: {fmt_iv(regs)} (the rest of the shot belongs to other stretches)" if regs
                        else " | ALL its footage belongs to other stretches: do NOT use it")
        line_txt = "\n".join(f"line {d['idx']}: {d['start_ms'] / 1000:.2f}-{d['end_ms'] / 1000:.2f} s  "
                             f"{('DIALOGUE said by ' + d['speaker']) if d['dialogue'] and d.get('speaker') else 'DIALOGUE' if d['dialogue'] else 'narration'}{' [scene: ' + str(d.get('scene')) + ', location: ' + d['loc'] + ']' if d.get('loc') else ''}{line_tag(d)}: {d['text']}" for d in LINES[k])
        rule8 = (f"\n11. The other stretches of the episode are cut AT THE SAME TIME as this one. For talking / action / object shots use "
                 f"ONLY the footage marked 'for THIS stretch' (the rest is shown by other stretches). SHARED shots (reaction / "
                 f"neutral / establishing) may be used by any stretch; the software marks repeats.")
        head = (prompt(T0, T1) + rule8 + "\n\nDUB LINES (voice times):\n" + line_txt +
                "\n\nTHE AUTOMATIC EDIT'S PIECES JUST BEFORE THIS STRETCH (rough guide for a smooth join; the real cut there is made at the same time):\n" +
                ("\n".join(auto_pc(s) for s in before) or "- this is the start of the episode") +
                "\nAND JUST AFTER IT:\n" + ("\n".join(auto_pc(s) for s in after) or "- this is the end") +
                "\n\nSOURCE SHOTS:\n" + "\n".join(stxt(r) for r in shots) + "\n\nFrames of each shot (3 frames, left to right):")
        content = [{"type": "text", "text": head}]
        for r in shots:
            content += [{"type": "text", "text": f"shot {r['n']}:"}, {"type": "image_url", "image_url": {"url": b64(r["n"])}}]
        return content

    def run(k, tag, nr, extra0="", others=(), prev_last=None, prev_edl=None):
        T0, T1 = wins[k]; t = time.time()
        content = base_content(k); extra = extra0; rounds = []; edl = prev_edl or []
        # keep the BEST round (fewest problems; a tie goes to the later one): a correction can make a cut worse
        # (ep34 2026-10-30: stretch 34-54 s ended on the phone-thrust action again, over narration, in its last round)
        best = None
        if prev_edl:
            bp = check_par(prev_edl, T0, T1, LINES[k], k, own, others, prev_last); best = (len(bp), prev_edl, "the cut before this fix", bp)
        for rnd in range(nr):
            key = f"par|{T0:.2f}-{T1:.2f}|{tag}{rnd}"
            with LOCK: ans = cache.get(key)
            if not (ans or {}).get("edl"):                    # an empty answer saved by an older run is asked again
                c = content + ([{"type": "text", "text": extra}] if extra else [])
                ans = {}
                for attempt in range(3):
                    try:
                        ans = ai.extract_json(ask_claude(c), "edl") or {}
                        if ans.get("edl"): break
                    except Exception as ex:
                        print(f"  stretch {k} {T0:.1f}-{T1:.1f}: reply not readable ({str(ex)[:80]}), asking again", flush=True)
                if ans.get("edl"):                          # never cache a failed call (gateway down): it is asked again next run
                    with LOCK:
                        cache[key] = ans; json.dump(cache, open(cache_p, "w"), indent=1, ensure_ascii=False)
                else:
                    raise RuntimeError(f"stretch {k} {T0:.1f}-{T1:.1f}: no usable answer from Claude after 3 tries (gateway down?)")
            new = parse_edl(ans)
            if new: edl, _fx = fix_short_broll(new)
            probs = check_par(edl, T0, T1, LINES[k], k, own, others, prev_last)
            rounds.append(dict(round=len(rounds), stage=tag, problems=probs, notes=ans.get("notes", "")))
            if best is None or len(probs) <= best[0]: best = (len(probs), edl, f"{tag}{rnd}", probs)
            print(f"  stretch {k:2d} {T0:6.1f}-{T1:6.1f} [{tag}{rnd}]: {len(edl)} pieces, {len(probs)} problems"
                  + (": " + " | ".join(p[:110] for p in probs[:3]) if probs else ""), flush=True)
            if not probs: break
            extra = (extra0 + "\n\n" if extra0 else "") + ("Your previous EDL:\n" + json.dumps(ans.get("edl"), ensure_ascii=False) +
                     "\n\nIt has these problems, fix them all and answer the complete corrected JSON again:\n- " + "\n- ".join(probs) +
                     "\n\nIf a problem cannot be solved with this footage, keep the best possible cut, say why in notes and answer -- "
                     "do not keep searching.")
        if best is not None and best[1] is not edl:
            print(f"  stretch {k:2d} {T0:6.1f}-{T1:6.1f}: keeping {best[2]} ({best[0]} problems) instead of the last round", flush=True)
            rounds.append(dict(round=len(rounds), stage=tag, problems=best[3], kept=best[2], notes=""))
            edl = best[1]
        return edl, rounds, time.time() - t

    with ThreadPoolExecutor(PAR) as ex:
        out = list(ex.map(lambda k: run(k, "a", 3), range(len(wins))))
    res = [o[0] for o in out]; rounds = [o[1] for o in out]; wsec = [o[2] for o in out]
    t_first = time.time() - t_start
    print(f"first pass: {len(wins)} stretches in {t_first:.0f} s", flush=True)

    fixes = []
    for g in range(2):
        gp, _ = global_check(res)
        for k in range(len(res)):                          # a stretch still failing its own check goes back too
            if rounds[k] and rounds[k][-1]["problems"]:
                gp[k] = rounds[k][-1]["problems"] + gp[k]
        bad = [k for k in gp if gp[k]]
        print(f"whole-episode check {g}: {sum(len(v) for v in gp.values())} problems in stretches {bad}", flush=True)
        for k in bad:
            for p in gp[k]: print(f"    stretch {k}: {p[:160]}", flush=True)
        fixes.append({str(k): gp[k] for k in bad})
        if not bad: break

        def fix(k):
            T0, T1 = wins[k]
            others = [e for kk in range(len(res)) if kk != k for e in res[kk]]
            shots_k = cand[k]
            pl = res[k - 1][-1] if k and res[k - 1] else None
            nf = res[k + 1][0] if k + 1 < len(res) and res[k + 1] else None
            shown = "\n".join(f"- shot {e['shot']} source {e['from']:.2f}-{e['to']:.2f} at dub {e['t0']:.1f}-{e['t1']:.1f} s"
                              for e in sorted(others, key=lambda e: e["t0"]) if int(e["shot"]) in shots_k) or "- none"
            extra0 = ("NOW THE OTHER STRETCHES ARE CUT.\nThe real piece just before this stretch: " +
                      (f"shot {pl['shot']} source {pl['from']:.2f}-{pl['to']:.2f} at dub {pl['t0']:.1f}-{pl['t1']:.1f} s ({pl.get('why', '')[:100]})" if pl else "none") +
                      "\nThe real piece just after it: " +
                      (f"shot {nf['shot']} source {nf['from']:.2f}-{nf['to']:.2f} at dub {nf['t0']:.1f}-{nf['t1']:.1f} s" if nf else "none") +
                      "\nFootage of these shots shown by the other stretches:\n" + shown +
                      "\n\nYour EDL for this stretch:\n" + json.dumps([{x: e[x] for x in ("t0", "t1", "shot", "from", "to", "line", "why") if x in e} for e in res[k]], ensure_ascii=False) +
                      "\n\nThe whole-episode check found these problems; fix them all (keep what is fine) and answer the complete corrected JSON:\n- " + "\n- ".join(gp[k]))
            return run(k, f"g{g}_", 3, extra0, others, pl, res[k])

        with ThreadPoolExecutor(PAR) as ex:
            fo = list(ex.map(fix, bad))
        for k, (edl, rr, sec) in zip(bad, fo):
            res[k] = edl; rounds[k] += rr; wsec[k] += sec
    gp, allp = global_check(res)
    left_global = sum(len(v) for v in gp.values())
    wall = time.time() - t_start
    report = [dict(window=list(wins[k]), scene=line_scene(LINES[k][0]) if LINES[k] else None, lines=LINES[k], shots=sorted(cand[k]),
                   edl=[e for e in allp if e["_w"] == k], rounds=rounds[k], seconds=round(wsec[k], 1)) for k in range(len(wins))]
    stats = dict(mode=f"parallel {PAR}", wall_seconds=round(wall), first_pass_seconds=round(t_first), usage=USAGE,
                 global_fixes=fixes, global_problems_left={str(k): v for k, v in gp.items() if v})
    return [dict(e) for e in allp], report, stats


if PAR:
    allE, report, stats = run_par(A0, A1)
    segs = []
    for e in allE:
        d = next((d for d in dub if d["start_ms"] / 1000 <= (e["t0"] + e["t1"]) / 2 <= d["end_ms"] / 1000), None)
        segs.append(dict(kind="clip", it_idx=e.get("line") if e.get("line") is not None else (d["idx"] if d else -1),
                         it_start_ms=round(e["t0"] * 1000, 3), it_end_ms=round(e["t1"] * 1000, 3),
                         us_start_ms=round(e["from"] * 1000, 3), us_end_ms=round(e["to"] * 1000, 3),
                         it_text=d["text"] if d else "", us_text="", stretch_factor=round((e["t1"] - e["t0"]) / max(e["to"] - e["from"], 1e-6), 3),
                         note="scene edit: " + e.get("why", "")[:200], **({"zoom": 1.08} if e.get("reuse") else {}),
                     **({"broll": True} if is_broll(e, piece_lines(e)) else {})))
    json.dump(dict(total_ms=round(A1 * 1000), segments=segs, bleed_report=[], groups=[]), open(OUTM, "w"), indent=1, ensure_ascii=False)
    json.dump(dict(range=[A0, A1], windows=report, stats=stats), open(OUTM.with_suffix(".edl.json"), "w"), indent=1, ensure_ascii=False)
    left = sum(len(w["rounds"][-1]["problems"]) for w in report)
    print(f"saved {OUTM} {len(segs)} pieces in {len(report)} stretches; problems left in stretches: {left}, whole-episode: "
          f"{sum(len(v) for v in stats['global_problems_left'].values())}; wall {stats['wall_seconds']} s; Claude calls {USAGE['calls']}, "
          f"tokens in {USAGE['prompt_tokens']} out {USAGE['completion_tokens']}", flush=True)
    sys.exit(0)


cache_p = OUTM.with_suffix(".cache.json"); cache = json.load(open(cache_p)) if cache_p.exists() else {}
allE, report = [], []
for T0, T1 in windows(A0, A1):
    edl, rounds, lines, shots = cut_window(T0, T1, allE, cache, f"{T0:.2f}-{T1:.2f}")
    allE += edl
    report.append(dict(window=[T0, T1], lines=lines, shots=shots, edl=edl, rounds=rounds))

segs = []
for e in allE:
    d = next((d for d in dub if d["start_ms"] / 1000 <= (e["t0"] + e["t1"]) / 2 <= d["end_ms"] / 1000), None)
    segs.append(dict(kind="clip", it_idx=e.get("line") if e.get("line") is not None else (d["idx"] if d else -1),
                     it_start_ms=round(e["t0"] * 1000, 3), it_end_ms=round(e["t1"] * 1000, 3),
                     us_start_ms=round(e["from"] * 1000, 3), us_end_ms=round(e["to"] * 1000, 3),
                     it_text=d["text"] if d else "", us_text="", stretch_factor=round((e["t1"] - e["t0"]) / max(e["to"] - e["from"], 1e-6), 3),
                     note="scene edit: " + e.get("why", "")[:200], **({"zoom": 1.08} if e.get("reuse") else {}),
                     **({"broll": True} if is_broll(e, piece_lines(e)) else {})))
json.dump(dict(total_ms=round(A1 * 1000), segments=segs, bleed_report=[], groups=[]), open(OUTM, "w"), indent=1, ensure_ascii=False)
json.dump(dict(range=[A0, A1], windows=report), open(OUTM.with_suffix(".edl.json"), "w"), indent=1, ensure_ascii=False)
left = sum(len(w["rounds"][-1]["problems"]) for w in report)
print("saved", OUTM, len(segs), "pieces in", len(report), "windows;", "all checks OK" if not left else f"{left} problems left")
~~~~~~

## Appendix: `ai/dubstudio/pipeline/scene_transitions.py`

<!-- FILE: ai/dubstudio/pipeline/scene_transitions.py md5=167d469d0242a8b08ddae1b7cf5f5168 -->
~~~~~~python
"""transitions plan for a scene-edited episode (owner, 2026-10-29): a transition at EVERY clip switch, except the cut INTO a
B-roll piece (clip -> B-roll is a straight cut; B-roll -> next clip gets the transition), so a short B-roll does not get two
transitions around it. `--scenes-only`: the older plan, a transition only where the STORY moves to another scene.
Story scene at a moment = the scene of the dub line there (W/scene/line_places.json from sceneedit.py, Claude's reading of
the story). Without that file: the library scene of the footage (older runs). A reaction shot of the same location taken
from another scene (location guard) is NOT a scene change.
Usage: scene_transitions.py <work> <broll_library.json> [--scenes-only]"""
import json, os, sys
W, LIBJ = sys.argv[1], sys.argv[2]
SH = json.load(open(LIBJ))["shots"]
M = json.load(open(f"{W}/manifest_groups.json"))["segments"]
LP = os.path.join(W, "scene", "line_places.json")
places = json.load(open(LP))["lines"] if os.path.exists(LP) else None
dub = [d for d in json.load(open(f"{W}/sentences_dub.json"))["sentences"] if d.get("start_ms") is not None] if places else []


def story_scene(ms):
    """scene of the line playing at dub ms (or the last line before it)"""
    best = None
    for d in dub:
        if d["start_ms"] <= ms: best = d
        else: break
    return (places.get(str(best["idx"])) or {}).get("scene") if best else None


def scene_at(t):
    return next((r["scene"] for r in SH if r["start"] <= t < r["end"]), None)


if "--scenes-only" not in sys.argv:
    cuts, n_into = {}, 0
    for a, b in zip(M, M[1:]):
        if abs(b["us_start_ms"] - a["us_end_ms"]) <= 50: continue          # the footage continues: not a switch
        into = bool(b.get("broll"))
        cuts[str(round(b["it_start_ms"]))] = "none" if into else "auto"; n_into += into
    json.dump({"default": "none", "cuts": cuts}, open(f"{W}/transitions_plan.json", "w"), indent=1)
    print(f"{len(cuts)} clip switches: {len(cuts) - n_into} transitions, {n_into} straight cuts into a B-roll")
    sys.exit(0)
cuts, n_scene = {}, 0
cur = (places.get(str(dub[0]["idx"])) or {}).get("scene") if places and dub else None   # story scene at the last switch
for a, b in zip(M, M[1:]):
    if abs(b["us_start_ms"] - a["us_end_ms"]) <= 50: continue          # not a switch
    if places:                                                         # the first switch after the story changed scene
        sa, sb = cur, story_scene(b["it_start_ms"] + 1)
        if sb: cur = sb
    else:
        sa = scene_at((a["us_start_ms"] + a["us_end_ms"]) / 2000); sb = scene_at((b["us_start_ms"] + b["us_end_ms"]) / 2000)
    cuts[str(round(b["it_start_ms"]))] = "auto" if sa != sb else "none"
    n_scene += sa != sb
json.dump({"default": "none", "cuts": cuts}, open(f"{W}/transitions_plan.json", "w"), indent=1)
print(f"{len(cuts)} clip switches: {n_scene} scene changes get a transition, {len(cuts) - n_scene} straight cuts"
      + (" (story scenes from line_places.json)" if places else " (library scenes of the footage)"))
~~~~~~

## Appendix: `ai/dubstudio/pipeline/sceneedit_sheet.py`

<!-- FILE: ai/dubstudio/pipeline/sceneedit_sheet.py md5=024588d0458fed2b5b367dba29a60482 -->
~~~~~~python
"""Scene-edit sheet: every piece Claude cut, with the reason. Usage: sceneedit_sheet.py <edl.json> <library json> <english srt> <out xlsx> <title> [opening s]"""
import json, re, sys
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

EDL, LIBJ, ENSRT, OUT, TITLE = sys.argv[1:6]
OP = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
R = json.load(open(EDL)); SH = {r["n"]: r for r in json.load(open(LIBJ))["shots"]}


def srt(p):
    out = []
    for b in re.split(r"\n\s*\n", open(p, encoding="utf-8-sig").read().strip()):
        L = b.split("\n")
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", L[1] if len(L) > 1 else "")
        if not m: continue
        g = list(map(int, m.groups()))
        out.append(((g[0] * 3600 + g[1] * 60 + g[2]) + g[3] / 1000, (g[4] * 3600 + g[5] * 60 + g[6]) + g[7] / 1000, " ".join(L[2:])))
    return out


EN = srt(ENSRT)
E = [e for w in R["windows"] for e in w["edl"]]
ov = lambda a0, a1, b0, b1: max(0.0, min(a1, b1) - max(a0, b0))
LN = sorted({d["idx"]: d for w in R["windows"] for d in w["lines"]}.values(), key=lambda d: d["start_ms"])


def plines(e):
    L = [d for d in LN if ov(e["t0"], e["t1"], d["start_ms"] / 1000, d["end_ms"] / 1000) > 0.1]
    if L: return L
    b = [d for d in LN if d["end_ms"] / 1000 <= e["t0"] + 0.05]; a = [d for d in LN if d["start_ms"] / 1000 >= e["t1"] - 0.05]
    return ([b[-1]] if b else []) + ([a[0]] if a else [])


def is_broll(e):
    """same test as sceneedit.is_broll: under half of the footage in the own footage (+-0.5 s) of its lines/neighbours"""
    L = plines(e)
    if not L or "_near" not in L[0]: return False
    own = sorted((a - 0.5, b + 0.5) for d in L for a, b in d["_near"]); m = []
    for a, b in own:
        if m and a <= m[-1][1]: m[-1][1] = max(m[-1][1], b)
        else: m.append([a, b])
    return sum(ov(a, b, e["from"], e["to"]) for a, b in m) < 0.5 * (e["to"] - e["from"])
lines = {d["idx"]: d for w in R["windows"] for d in w["lines"]}
sp = [(e["to"] - e["from"]) / (e["t1"] - e["t0"]) for e in E]
fm = lambda s: f"{int(s // 60):02d}:{s % 60:05.2f}"
F = "Arial"; hf = Font(name=F, bold=True, color="FFFFFF"); hfill = PatternFill("solid", fgColor="2F4F6F"); bf = Font(name=F, size=10)
th = Side(style="thin", color="D0D0D0"); bx = Border(left=th, right=th, top=th, bottom=th)
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Summary"
first_probs = sum(len(w["rounds"][0]["problems"]) for w in R["windows"])
left = sum(len(w["rounds"][-1]["problems"]) for w in R["windows"])
txt = [TITLE, "",
       f"Video times include the {OP:.2f} s source opening before the dub starts." if OP else "Video times = dub times.",
       f"{len(E)} pieces in {len(R['windows'])} stretches of about 30 s; every piece between {min(sp):.2f}x and {max(sp):.2f}x "
       f"(time-weighted {sum(e['to'] - e['from'] for e in E) / sum(e['t1'] - e['t0'] for e in E):.2f}x); "
       f"{sum(1 for e in E if e.get('reuse'))} reaction/neutral repeats (zoomed 8%).",
       f"Checks: {first_probs} problems in Claude's first cuts, {left} left after its corrections "
       "(tiling, speed 0.8-1.25x, piece >= 0.5 s, every dialogue line starts on a moving mouth within 0.3 s, action/talking footage once).",
       "", "Stretches:"]
for w in R["windows"]:
    txt.append(f"  {fm(w['window'][0] + OP)}-{fm(w['window'][1] + OP)}: {len(w['edl'])} pieces, check rounds {len(w['rounds'])} "
               f"(problems per round {[len(r['problems']) for r in w['rounds']]}). Claude's notes: {w['rounds'][-1]['notes'][:300]}")
for i, t in enumerate(txt, 1):
    c = ws.cell(i, 1, t); c.font = Font(name=F, size=13 if i == 1 else 10, bold=i == 1); c.alignment = Alignment(wrap_text=True)
ws.column_dimensions["A"].width = 150
ws2 = wb.create_sheet("Scene edit")
hd = ["#", "Video from", "to", "Shot", "Who / shot type", "What happens in the shot", "Source from", "to", "Speed", "Dub line",
      "Line type", "Dub line text", "English subtitle", "Repeat?", "B-roll?", "Why Claude put it here"]
for c, h in enumerate(hd, 1):
    x = ws2.cell(1, c, h); x.font = hf; x.fill = hfill; x.border = bx; x.alignment = Alignment(wrap_text=True)
for i, e in enumerate(E, 2):
    r = SH.get(int(e["shot"]), {}); d = lines.get(e.get("line"))
    en = " ".join(t for a, b, t in EN if min(b, e["t1"]) - max(a, e["t0"]) > 0.15)
    v = [i - 1, fm(e["t0"] + OP), fm(e["t1"] + OP), e["shot"], f"{', '.join(r.get('people', []))} ({r.get('type', '')})" + (f" @ {r['location']}" if r.get('location') else ""), r.get("action", ""),
         fm(e["from"]), fm(e["to"]), round((e["to"] - e["from"]) / (e["t1"] - e["t0"]), 2),
         e.get("line") if e.get("line") is not None else "(between lines)", (("dialogue" if d["dialogue"] else "narration") + (f" @ {d['loc']}" if d.get("loc") else "")) if d else "",
         d["text"] if d else "", en, "yes" if e.get("reuse") else "", "B-ROLL" if is_broll(e) else "", e.get("why", "")]
    for c, x in enumerate(v, 1):
        y = ws2.cell(i, c, x); y.font = bf; y.border = bx; y.alignment = Alignment(wrap_text=True, vertical="top")
        if d and d["dialogue"]: y.fill = PatternFill("solid", fgColor="E2EFDA")
        if e.get("reuse"): y.fill = PatternFill("solid", fgColor="FFF2CC")
        if is_broll(e): y.fill = PatternFill("solid", fgColor="DDEBF7")
for c, w in enumerate([5, 9, 9, 6, 18, 32, 9, 9, 7, 8, 10, 40, 40, 8, 8, 70], 1):
    ws2.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
ws2.freeze_panes = "A2"
if LN and "own_speed" in LN[0]:                    # v21: every line, its own footage speed and the B-roll it got
    ws3 = wb.create_sheet("Lines (B-roll rule)")
    hd3 = ["Line", "Video from", "to", "Dub line", "English subtitle", "Own footage (source s)", "Own speed", "Class",
           "B-roll allowed s", "B-roll used s", "Pieces (shot: source)"]
    for c, h in enumerate(hd3, 1):
        x = ws3.cell(1, c, h); x.font = hf; x.fill = hfill; x.border = bx; x.alignment = Alignment(wrap_text=True)
    for i, d in enumerate(LN, 2):
        a0, a1 = d["start_ms"] / 1000, d["end_ms"] / 1000
        P = [e for e in E if ov(e["t0"], e["t1"], a0, a1) > 0.05]
        used = sum(ov(e["t0"], e["t1"], a0, a1) for e in P if is_broll(e))
        cls = "SLOW" if d.get("slow") else ("fast (trim)" if d["own_speed"] > 1.25 else "normal")
        en = " ".join(t for a, b, t in EN if min(b, a1) - max(a, a0) > 0.15)
        v = [d["idx"], fm(a0 + OP), fm(a1 + OP), d["text"], en, ", ".join(f"{a:.2f}-{b:.2f}" for a, b in d.get("_own", [])) or "none",
             d["own_speed"], cls, d.get("broll_budget", 0), round(used, 2),
             "; ".join(f"{e['shot']}: {e['from']:.2f}-{e['to']:.2f}" + (" B-ROLL" if is_broll(e) else "") for e in P)]
        bad = (used > d.get("broll_budget", 0) + 0.75) or (used > 0.05 and not d.get("slow"))
        for c, x in enumerate(v, 1):
            y = ws3.cell(i, c, x); y.font = bf; y.border = bx; y.alignment = Alignment(wrap_text=True, vertical="top")
            if d.get("slow"): y.fill = PatternFill("solid", fgColor="FCE4D6")
            if bad: y.fill = PatternFill("solid", fgColor="F8CBAD")
    for c, w in enumerate([6, 9, 9, 40, 40, 22, 8, 10, 9, 9, 50], 1):
        ws3.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
    ws3.freeze_panes = "A2"
wb.save(OUT); print("saved", OUT, len(E), "pieces")
~~~~~~

## Appendix: `ai/dubstudio/pipeline/ai_match.py`

<!-- FILE: ai/dubstudio/pipeline/ai_match.py md5=0097423fb75381ecc21fa3a85426b767 -->
~~~~~~python
"""Step 2 by AI: Claude reads the flagged rows by meaning and proposes decisions in the skill's spec format.

Flow: flagged rows (unmatched + Case A, from prep.json) -> clusters of nearby rows -> one Claude call per cluster
with the dub rows around them and the source window they could map to -> JSON answers -> validated spec lines
(G/A/C/GAP, the same format a person writes) -> W/spec_ai.py + W/ai_proposals.json.
Nothing is applied automatically: the editor reviews the proposals on Check matching, then they are imported
with decisions.from_spec (which re-checks every index against its text).

CLI:  python -m dubstudio.pipeline.ai_match <work_dir> [--model claude-sonnet-5-5] [--apply]
"""
import argparse, json, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .. import ai
from . import decisions as dec

RULES = """You are the Step-2 reader of a dubbing pipeline (skill manual "Step 2. The manual read"). An Italian (dub)
episode is an ADAPTATION of a source episode (English or German): lines are merged, split, shortened, re-ordered,
dropped or added, and characters have Italian names (see the name list). Each dub row must be shown with the source
footage whose MEANING matches it (who does what, the beat of the story). Judge by MEANING, never by score: true
matches score as low as 0.11, wrong ones up to 0.65.

How to read one row (the n-1/n+1 method):
1. Look at the neighbours (n-1, n+1) and the source sentence each is matched to.
2. Ask whether that sentence also carries this row's meaning (a clause, a paraphrase, the same beat):
   "Si domandò." = the "...she wondered." tail; "Muoviti." = the "...quickly" of "We must follow after Edgar
   quickly."; "Poteva incassare il colpo." = "...but she could handle it." -> then the rows are a GROUP on it.
3. If neither neighbour covers it, go to n+-2; a pair can grow into a 3- or 4-row group.
4. Check nearby UNUSED source lines ("Viola si fece avanti." = "Heather made her presence known.").
5. A line the adaptation ADDED -- an event or detail ("she grabbed a coffee cup and smashed it against the wall"), an
   interjection ("Merda."), an inner question, a thought or an order that NO source sentence says -- is NOT FOUND
   ("gap"): the story is changed there (owner, 2026-10-29). Never give it a neighbour's footage and never group it
   with a neighbour just so it has footage. (Not black: the scene edit covers a gap with B-roll of the same place.)
   A row IS matched when a source sentence carries its meaning: the same line reworded, a clause of it (then group),
   or an unused source line of the same moment.
6. A row often tells TWO source sentences (the adaptation merged them): give it the span [a,b], so its clip shows
   the whole moment and does not play in slow motion. The same for a nearby UNUSED line that belongs to its beat.
7. A spoken line (quotes, "disse", a question to someone) should show the character who says it (mind the name list).
8. After these checks, a row whose meaning no source sentence carries is NOT FOUND ("gap"). Do not force a match: say
   in the reason which neighbours / unused lines you checked and that the story is changed here.

Which decision to write (one per flagged row):
- "group": rows that are clauses of one source sentence (or a short added line with its neighbour's beat): give ALL
  member rows (consecutive, in dub order, neighbours included even if not flagged) and the source sentence or span.
- "assign": the row plays source sentence S (int) or consecutive lines [a,b] that tell the same beat (also overrides a
  wrong automatic match). A row that would play very slow: group it with a real neighbour instead -- only when that
  neighbour's sentence carries this row's meaning (an added event the source never tells stays a gap).
- "confirm": the suggested source (shown as -> [S]) is right.
- "gap" (NOT FOUND): no source sentence carries the row's meaning (an added line, the story is changed here). Say in
  the reason what you checked.
Prefer story order. A source sentence may serve several dub rows only through a group. Two neighbouring rows must
never show the same source footage as separate rows: make them one group.

Answer ONLY with JSON: {"decisions":[{"row":R,"action":"assign|group|confirm|gap","src":S or [a,b] or null,
"members":[rows] (group only),"reason":"short, by meaning","confidence":0.0-1.0}]} with one entry per flagged row."""


def _fmt_ms(ms):
    return f"{ms / 1000:6.1f}"


def _clusters(rows, gap=5):
    out = []
    for r in sorted(rows):
        if out and r - out[-1][-1] <= gap:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def load_names(path, texts, limit=150):
    """The show's localisation sheet (source name -> target name), all of it when it is short (else the names that
    occur in this episode). See dubstudio/names.py for the formats."""
    from .. import names as nm
    if not path:
        return []
    return nm.for_episode(nm.read(path)["names"], texts, limit)


def _names_txt(names):
    return ("Localisation sheet, character names (source video name -> dub name): "
            + "; ".join(f"{o} -> {l}" for o, l in names) + "\n\n") if names else ""


def _used(p, case_a, answered=None):
    """source index -> dub rows using it (automatic matches, plus answers already given)."""
    u = {}
    for k, v in p["auto"].items():
        if int(k) not in case_a:
            u.setdefault(v["src"], []).append(int(k))
    for r, d in (answered or {}).items():
        s_ = d.get("src")
        if d.get("action") in ("assign", "group") and s_ is not None:
            for j in (range(s_[0], s_[1] + 1) if isinstance(s_, list) and len(s_) == 2 else [s_]):
                if isinstance(j, int):
                    u.setdefault(j, []).append(r)
    return u


def _context(p, cluster, case_a, nb=4, pad=4, answered=None):
    dub, src, auto, sim = p["dub"], p["src"], p["auto"], p.get("sim")
    used = _used(p, case_a, answered)
    lo, hi = max(0, cluster[0] - nb), min(len(dub) - 1, cluster[-1] + nb)
    srcs = [auto[str(i)]["src"] for i in range(lo, hi + 1) if str(i) in auto and i not in case_a]
    s_lo = max(0, (min(srcs) if srcs else 0) - pad); s_hi = min(len(src) - 1, (max(srcs) if srcs else len(src) - 1) + pad)
    extra = set()
    if sim:
        for r in cluster:
            extra |= set(sorted(range(len(src)), key=lambda j: -sim[r][j])[:4])
    lines = ["DUB rows (index, start s, text, -> current automatic source):"]
    for i in range(lo, hi + 1):
        a = auto.get(str(i)); flag = "  <== FLAGGED" if i in cluster else ""
        tag = f"-> [{a['src']}] {a.get('kind', '')} {a.get('score', 0):.2f}" if a else "-> (no match)"
        lines.append(f"D{i:>4} {_fmt_ms(dub[i].get('start_ms', 0))}  {dub[i]['text']}  {tag}{flag}")
    lines.append("\nSOURCE sentences in this part of the episode (used by which dub rows, or UNUSED):")
    for j in range(s_lo, s_hi + 1):
        tag = f"(used by D{', D'.join(map(str, sorted(set(used[j]))))})" if j in used else "(UNUSED)"
        lines.append(f"S{j:>4} {_fmt_ms(src[j].get('start_ms', 0))}  {src[j]['text']}  {tag}")
    far = sorted(j for j in extra if j < s_lo or j > s_hi)
    if far:
        lines.append("\nOther source sentences that score high for the flagged rows (far away; use only if the meaning is clearly this):")
        for j in far:
            lines.append(f"S{j:>4} {_fmt_ms(src[j].get('start_ms', 0))}  {src[j]['text']}")
    lines.append(f"\nFLAGGED rows to decide: {cluster}")
    return "\n".join(lines), (s_lo, s_hi)


def _kw(text, n=3):
    w = text.split()
    return " ".join(w[:n]) if w else text


def _valid_src(s, n):
    if isinstance(s, int):
        return 0 <= s < n
    return isinstance(s, list) and len(s) == 2 and all(isinstance(x, int) and 0 <= x < n for x in s) and s[0] <= s[1]


SECOND = """A first read answered "gap" (no footage) for dub row D{row}: "{reason}".
Read it again with the manual's checks, one by one, before accepting a gap:
1. Does the source sentence of D{prev} or D{next} say what this row says (a clause or a paraphrase of it -- NOT a
   line the adaptation added: event, interjection, thought, order)? -> "group" with that neighbour (members = the
   consecutive rows, the neighbour included; src = the neighbour's source sentence or a span over both).
2. The same with D{prev2} / D{next2}.
3. Is there an UNUSED source line between the neighbours' sources that shows this moment (same characters --
   mind the name list -- same action, story order)? -> "assign" it.
4. If no source sentence carries this row's meaning (a line the adaptation added: event, interjection, thought,
   order): "gap" (NOT FOUND, the story is changed here) -- never a neighbour's footage. Say what you checked.{note}
Answer ONLY with JSON {{"decisions":[{{"row":{row},"action":...,"src":...,"members":[...],"reason":"...","confidence":0.0-1.0}}]}}."""


def _ask(prompt, model, usage, log, label):
    text, u = ai.chat(prompt, model=model, max_tokens=3000)
    usage["calls"] += 1
    usage["prompt_tokens"] += u.get("prompt_tokens", 0); usage["completion_tokens"] += u.get("completion_tokens", 0)
    try:
        return ai.extract_json(text, "decisions").get("decisions", [])
    except Exception as ex:
        log(f"{label}: could not read reply ({ex})"); return []


def second_look(p, rows, props, case_a, names, model, usage, log, notes=None, workers=4):
    """Rows answered "gap" (or clashing) are read once more, alone, with the manual's checks spelled out.
    Returns {row: new answer} for the rows that now have footage."""
    def one(r):
        prev_ = props.get(r) or {}
        ctx, _ = _context(p, [r], case_a, nb=5, pad=6, answered={k: v for k, v in props.items() if k != r})
        q = SECOND.format(row=r, reason=(prev_.get("reason") or "no answer").replace('"', "'"), prev=r - 1, next=r + 1,
                          prev2=r - 2, next2=r + 2, note=("\n" + notes[r]) if notes and r in notes else "")
        ans = _ask(RULES + "\n\n" + _names_txt(names) + ctx + "\n\n" + q, model, usage, log, f"row {r} (second look)")
        return r, next((d for d in ans if d.get("row") == r or (isinstance(d.get("row"), str) and d["row"].strip() == str(r))), None)
    out = {}
    with ThreadPoolExecutor(workers) as ex:
        for r, d in ex.map(one, rows):
            if d and d.get("action") in ("assign", "group", "confirm") and (d.get("src") is not None or d.get("action") == "confirm"):
                out[r] = d
    return out


def _build(p, flagged, props, model):
    """Answers -> (spec text, proposal records) in the skill's spec language."""
    nd, ns = len(p["dub"]), len(p["src"])
    spec, done, out = [], set(), []
    for r in flagged:
        d = props.get(r)
        if not d:
            out.append({"row": r, "action": "none", "error": "no answer"}); continue
        act, s, why = d.get("action"), d.get("src"), (d.get("reason") or "").replace('"', "'")
        conf = d.get("confidence")
        tag = f"AI {model or ai.settings()['AI_MODEL']} ({conf}): {why}"
        rec = {"row": r, "action": act, "src": s, "members": d.get("members"), "reason": why, "confidence": conf}
        if d.get("second_look"):
            rec["second_look"] = True
        if r in done:
            rec["note"] = "covered by an earlier group"; out.append(rec); continue
        if act == "gap" or (act in ("assign", "group") and s is None):
            spec.append(f'GAP({r},{json.dumps(_kw(p["dub"][r]["text"]), ensure_ascii=False)},{json.dumps(tag, ensure_ascii=False)})')
        elif act == "confirm":
            spec.append(f'C({r},{json.dumps(tag, ensure_ascii=False)})')
        elif act == "group" and _valid_src(s, ns):
            mem = sorted({m for m in (d.get("members") or []) if isinstance(m, int) and 0 <= m < nd and m not in done} | {r})
            if mem != list(range(mem[0], mem[-1] + 1)):
                mem = [r]                       # members must be consecutive; fall back to a single assign
            if len(mem) == 1:
                spec.append(f'A({r},{json.dumps(_kw(p["dub"][r]["text"]), ensure_ascii=False)},{json.dumps(s)},'
                            f'{json.dumps(_kw(p["src"][s if isinstance(s, int) else s[0]]["text"]), ensure_ascii=False)},{json.dumps(tag, ensure_ascii=False)})')
            else:
                m_list = ",".join(f'({m},{json.dumps(_kw(p["dub"][m]["text"]), ensure_ascii=False)})' for m in mem)
                spec.append(f'G([{m_list}],{json.dumps(s)},{json.dumps(_kw(p["src"][s if isinstance(s, int) else s[0]]["text"]), ensure_ascii=False)},{json.dumps(tag, ensure_ascii=False)})')
                done |= set(mem); rec["members"] = mem
        elif act == "assign" and _valid_src(s, ns):
            spec.append(f'A({r},{json.dumps(_kw(p["dub"][r]["text"]), ensure_ascii=False)},{json.dumps(s)},'
                        f'{json.dumps(_kw(p["src"][s if isinstance(s, int) else s[0]]["text"]), ensure_ascii=False)},{json.dumps(tag, ensure_ascii=False)})')
        else:
            rec["error"] = "invalid answer"; out.append(rec); continue
        done.add(r); out.append(rec)
    return "# Step 2 proposals by AI (review before importing)\n" + "\n".join(spec) + "\n", out


def propose(work, model=None, workers=4, log=print, names_csv=None):
    w = Path(work)
    p, unmatched, case_a = dec.flagged_rows(w)
    flagged = sorted(unmatched + case_a)
    if not flagged:
        return {"proposals": [], "spec": "", "usage": {}}
    nd, ns = len(p["dub"]), len(p["src"])
    clusters = _clusters(flagged)
    usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    names = load_names(names_csv, [x["text"] for x in p["dub"] + p["src"]]) if names_csv else []
    if names:
        log(f"name list: {len(names)} characters ({', '.join(f'{o}->{l}' for o, l in names[:6])}…)")

    def run(cl):
        ctx, _ = _context(p, cl, set(case_a))
        text, u = ai.chat(RULES + "\n\n" + _names_txt(names) + ctx, model=model, max_tokens=3000)
        try:
            ans = ai.extract_json(text, "decisions").get("decisions", [])
        except Exception as ex:
            ans = []; log(f"cluster {cl}: could not read reply ({ex})")
        return cl, ans, u

    props = {}

    def collect(results):
        for cl, ans, u in results:
            usage["calls"] += 1
            usage["prompt_tokens"] += u.get("prompt_tokens", 0); usage["completion_tokens"] += u.get("completion_tokens", 0)
            for d in ans:
                cover = [d.get("row")] + list(d.get("members") or [])   # a group answer also decides its members
                for r in cover:
                    r = int(r) if isinstance(r, str) and r.strip().isdigit() else r
                    if isinstance(r, int) and r in cl and r not in props:
                        props[r] = d
            log(f"rows {cl}: {len([r for r in cl if r in props])}/{len(cl)} answered")

    with ThreadPoolExecutor(workers) as ex:
        collect(list(ex.map(run, clusters)))
        again = [r for r in flagged if r not in props]          # a row the reply skipped: ask once more, alone
        if again:
            log(f"asking again for {again}")
            collect(list(ex.map(run, [[r] for r in again])))

    # second look: every row answered "gap" (or not answered) is read again alone, with the manual's checks
    gaps = [r for r in flagged if (props.get(r) or {}).get("action") in (None, "gap") or (props.get(r) or {}).get("src") is None
            and (props.get(r) or {}).get("action") != "confirm"]
    if gaps:
        log(f"second look at {len(gaps)} rows answered 'no footage': {gaps}")
        found = second_look(p, gaps, props, set(case_a), names, model, usage, log, workers=workers)
        for r, d in found.items():
            props[r] = dict(d, second_look=True)
        log(f"second look found footage for {len(found)} of {len(gaps)}: {sorted(found)}")

    spec_text, out = _build(p, flagged, props, model)
    (w / "spec_ai.py").write_text(spec_text)
    (w / "ai_proposals.json").write_text(json.dumps({"model": model or ai.settings()["AI_MODEL"], "usage": usage,
                                                     "proposals": out, "names": len(names)}, indent=1, ensure_ascii=False))
    return {"proposals": out, "spec": spec_text, "usage": usage, "_props": props, "_names": names, "_case_a": case_a}


def apply_group_rule(work, result, check, log=print):
    """After the AI answered: run the pipeline's integrity check on its spec; neighbouring rows that still share
    footage are joined into groups (decisions.join_shared), and the proposals are rewritten to match, so the editor
    gets proposals that pass. `check(spec) -> {"overlaps": [[a, b, ms], ...], ...}`. Adds result["joined"]."""
    w = Path(work)
    r = check(result["spec"])
    result["joined"], result["left"] = [], []
    if not r.get("overlaps"):
        result["check"] = r; return result
    new_spec, joined, left = dec.join_shared(w, result["spec"], r["overlaps"])
    result["joined"], result["left"] = joined, left
    log(f"group rule: joined rows {', '.join(f'{a}-{b}' for a, b in joined)}" + (f"; left {left}" if left else ""))
    # the joined groups as proposals: every flagged row inside a joined run becomes a member of that group
    p = json.loads((w / "prep.json").read_text())
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "prep.json").write_text(json.dumps(p))
        dec.from_spec(tmp, new_spec)
        groups = json.loads((Path(tmp) / "decisions.json").read_text())["groups"]
    by_first = {g["members"][0]: g for g in groups}
    for a, b in joined:
        g = by_first.get(a)
        if not g:
            continue
        for rec in result["proposals"]:
            if a <= rec["row"] <= b:
                rec.update(action="group", src=g["src"], members=list(range(a, b + 1)), error=None,
                           reason=f"rows {a}-{b} share source footage, so they are one group. " + (rec.get("reason") or ""))
    result["spec"] = "# Step 2 proposals by AI (review before importing); neighbours on one footage joined\n" + \
        "\n".join(l for l in new_spec.splitlines() if not l.startswith("#")) + "\n"
    result["check"] = check(result["spec"])
    (w / "spec_ai.py").write_text(result["spec"])
    meta = json.loads((w / "ai_proposals.json").read_text())
    meta.update(proposals=result["proposals"], joined=joined, left=left)
    (w / "ai_proposals.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    return result


REVIEW = """You are now doing the FULL read of the manual (Step 2): every dub row is checked, not only the flagged
ones. The automatic matcher pairs sentences by similarity and is often right, but it makes these mistakes, which a
person reading the episode fixes:
- a dub row that tells the beat of TWO source sentences gets only one of them (the other stays unused or goes to a
  neighbour), so its clip shows half the moment and plays in slow motion -> "assign" the span [a,b];
- two or three dub rows that are clauses of ONE source sentence are spread over neighbouring sentences ->
  "group" them on that sentence (or span);
- a row sent to the wrong moment (another character, another action, out of story order) -> "assign" the right line.
Look at each row in "Rows to check" with its neighbours (n-1/n+1, then n+-2). Judge by MEANING: who does what,
who speaks (mind the name list; a spoken line should show the character who says it), the story order. The duration
column helps: "x2.1 slow" means the clip would play 2.1 times slower than real time, a sign that the row needs more
footage (a span, or a group with its neighbour) -- but only footage that tells the same beat: a row that tells an event
no source line tells (an added action) stays a gap, never inside another row's group. Change a row ONLY when you are sure the current source is wrong or
incomplete; leave correct rows alone. Never give a row footage another row shows unless you group the two.

Answer ONLY with JSON: {"decisions":[{"row":R,"action":"assign|group","src":S or [a,b],"members":[rows] (group only,
consecutive, in dub order),"reason":"short, by meaning","confidence":0.0-1.0}]} listing ONLY the rows you change
(an empty list when every row is right)."""


def _decided(p, D, case_a):
    """-> {row: ("group", gid) | ("assign", src) | ("auto", src) | ("gap", None)}, {gid: group}"""
    out, groups = {}, {}
    for k, v in p["auto"].items():
        if int(k) not in case_a:
            out[int(k)] = ("auto", v["src"])
    for k in D.get("confirm", {}):
        if k in p["auto"]:
            out[int(k)] = ("auto", p["auto"][k]["src"])
    for k, a in D.get("assign", {}).items():
        out[int(k)] = ("assign", a["src"])
    for gi, g in enumerate(D.get("groups", [])):
        groups[gi] = dict(g)
        for m in g["members"]:
            out[m] = ("group", gi)
    for k in D.get("gap", {}):
        out[int(k)] = ("gap", None)
    return out, groups


def review_rows(work, p, spec, names, model, usage, log, case_a=(), window=12, workers=4):
    """Claude reads EVERY dub row (windows of ~12 rows with their neighbours) and corrects the automatic matches a
    person would correct: half beats, clauses spread over neighbours, wrong moments. -> (new spec, report)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "prep.json").write_text(json.dumps(p)); dec.from_spec(tmp, spec)
        D = json.loads((Path(tmp) / "decisions.json").read_text())
    dub, src = p["dub"], p["src"]; nd, ns = len(dub), len(src)
    cur = _current(p, D, set(case_a))

    def block(lo, hi):
        a0, a1 = max(0, lo - 3), min(nd - 1, hi + 3)
        lines = ["DUB rows (index, start s, length s, text, -> source lines shown now, speed):"]
        for i in range(a0, a1 + 1):
            c = cur.get(i); d_ms = max(1, dub[i].get("end_ms", 0) - dub[i].get("start_ms", 0))
            if c:
                s_ms = max(1, src[c[1]].get("end_ms", 0) - src[c[0]].get("start_ms", 0)); f = d_ms / s_ms
                spd = f"x{f:.1f} slow" if f > 1.3 else (f"x{1 / f:.1f} fast" if f < 0.7 else "ok")
                tag = f"-> S{c[0]}" + (f"-S{c[1]}" if c[1] != c[0] else "") + f" ({spd})"
            else:
                tag = "-> (no footage)"
            mark = "" if lo <= i <= hi else "   (context)"
            lines.append(f"D{i:>4} {_fmt_ms(dub[i].get('start_ms', 0))} {d_ms / 1000:4.1f}s  {dub[i]['text']}  {tag}{mark}")
        ss = [cur[i][0] for i in range(a0, a1 + 1) if i in cur] + [cur[i][1] for i in range(a0, a1 + 1) if i in cur]
        s_lo, s_hi = (max(0, min(ss) - 3), min(ns - 1, max(ss) + 3)) if ss else (0, min(ns - 1, 20))
        lines.append("\nSOURCE sentences (shown under which dub rows, or UNUSED):")
        for j in range(s_lo, s_hi + 1):
            users = [f"D{r}" for r, (x, y) in cur.items() if x <= j <= y]
            lines.append(f"S{j:>4} {_fmt_ms(src[j].get('start_ms', 0))}  {src[j]['text']}  "
                         + (f"(shown under {', '.join(users)})" if users else "<== UNUSED"))
        lines.append(f"\nRows to check: D{lo} to D{hi}")
        return "\n".join(lines)

    wins = [(lo, min(nd - 1, lo + window - 1)) for lo in range(0, nd, window)]
    def run(w_):
        return w_, _ask(RULES + "\n\n" + REVIEW + "\n\n" + _names_txt(names) + block(*w_), model, usage, log,
                        f"review D{w_[0]}-D{w_[1]}")
    changes = {}
    with ThreadPoolExecutor(workers) as ex:
        for (lo, hi), ans in ex.map(run, wins):
            for d in ans or []:
                try:
                    r = int(d.get("row"))
                except (TypeError, ValueError):
                    continue
                if lo <= r <= hi and d.get("action") in ("assign", "group") and _valid_src(d.get("src"), ns):
                    changes[r] = d
    dec_of, groups = _decided(p, D, set(case_a))
    applied = []
    for r in sorted(changes):
        d = changes[r]
        if d["action"] == "group":
            mem = sorted({m for m in (d.get("members") or []) if isinstance(m, int) and 0 <= m < nd} | {r})
            if len(mem) < 2 or mem != list(range(mem[0], mem[-1] + 1)) or any(m in applied for m in mem):
                continue
        else:
            mem = [r]
            if r in applied:
                continue
        for m in mem:                                     # take the rows out of what they were in
            k = dec_of.get(m)
            if k and k[0] == "group":
                g = groups.get(k[1])
                if g:
                    g["members"] = [x for x in g["members"] if x != m]
        gid = max(groups, default=-1) + 1
        if len(mem) > 1:
            groups[gid] = {"members": mem, "src": d["src"], "reason": "AI full read: " + (d.get("reason") or "")}
            for m in mem: dec_of[m] = ("group", gid)
        else:
            dec_of[r] = ("assign", d["src"])
            D.setdefault("assign", {})[str(r)] = {"src": d["src"], "reason": "AI full read: " + (d.get("reason") or "")}
        for m in mem:
            D.get("confirm", {}).pop(str(m), None); D.get("gap", {}).pop(str(m), None)
            if len(mem) > 1: D.get("assign", {}).pop(str(m), None)
        applied += mem
    newg = []
    for gi, g in groups.items():                          # groups that lost members: keep the rest
        mm = sorted(g["members"])
        if len(mm) >= 2 and mm == list(range(mm[0], mm[-1] + 1)):
            newg.append(dict(g, members=mm))
        else:
            for m in mm:
                D.setdefault("assign", {})[str(m)] = {"src": g["src"], "reason": g.get("reason", "")}
    D["groups"] = newg
    rep = {"windows": len(wins), "changed": sorted(applied),
           "why": {r: changes[r].get("reason") for r in sorted(changes) if r in applied}}
    log(f"full read of all {nd} rows: {len(applied)} rows changed {sorted(applied)[:30]}")
    return dec.to_spec(p, D, "# Step 2 decisions by AI (Dub Studio, automatic) + full read"), rep


UNUSED = """Some SOURCE sentences are not shown under any dub row yet (marked UNUSED below). In the manual read every
source line is accounted for: an unused line usually belongs to the beat of the dub row just before or just after it
(the adaptation merged it: "Fabio si sistemò a sedere, palesemente irritato." also tells "His head turned" and
"Jason bristled"), and then that row's footage is extended over it, so the row shows the whole beat and plays at a
natural speed. Only when the dub really leaves that moment out is the line left unused.

For EACH unused source line below decide:
- "prev": its meaning is part of the dub row shown just BEFORE it (that row's footage is extended to include it);
- "next": its meaning is part of the dub row shown just AFTER it;
- "none": the dub does not tell this moment (say what it is, e.g. "a line the Italian leaves out").
Judge by MEANING (who does what), mind the name list. Prefer "prev"/"next" when the beat is there even if shortened.

Answer ONLY with JSON: {"unused":[{"src":S,"attach":"prev|next|none","reason":"short","confidence":0.0-1.0}]}"""


def _current(p, D, case_a):
    """dub row -> (first, last) source line it shows now (decisions over the automatic match); None = no footage."""
    cur = {}
    for k, v in p["auto"].items():
        if int(k) not in case_a:
            cur[int(k)] = (v["src"], v["src"])
    for k, why in D.get("confirm", {}).items():
        if k in p["auto"]:
            cur[int(k)] = (p["auto"][k]["src"],) * 2
    rng = lambda s: (s, s) if isinstance(s, int) else (s[0], s[1])
    for k, a in D.get("assign", {}).items():
        cur[int(k)] = rng(a["src"])
    for g in D.get("groups", []):
        for m in g["members"]:
            cur[m] = rng(g["src"])
    for k in D.get("gap", {}):
        cur.pop(int(k), None)
    return cur


def review_unused(work, p, spec, names, model, usage, log, case_a=(), per_call=8, workers=4):
    """The manual read's last check, done by Claude: every source line no dub row shows yet is either given to the
    dub row just before/after it (that row's footage is extended over it, as a person reading the episode does) or
    noted as left out. Works on the whole episode, not only the flagged rows. -> (new spec, report)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "prep.json").write_text(json.dumps(p)); dec.from_spec(tmp, spec)
        D = json.loads((Path(tmp) / "decisions.json").read_text())
    dub, src = p["dub"], p["src"]
    cur = _current(p, D, set(case_a))
    used = {j for a, b in cur.values() for j in range(a, b + 1)}
    first, last = (min(used), max(used)) if used else (0, -1)
    unused = [j for j in range(first, last + 1) if j not in used and str(j) not in D.get("unused_notes", {})]
    report = {"unused": unused, "attached": [], "left": []}
    if not unused:
        return spec, report
    def nb(j):
        prev = max((r for r, (a, b) in cur.items() if b < j), key=lambda r: (cur[r][1], r), default=None)
        nxt = min((r for r, (a, b) in cur.items() if a > j), key=lambda r: (cur[r][0], -r), default=None)
        return prev, nxt
    items = [(j,) + nb(j) for j in unused]
    def block(chunk):
        rows = sorted({r for _, a, b in chunk for r in (a, b) if r is not None})
        lo, hi = max(0, rows[0] - 2), min(len(dub) - 1, rows[-1] + 2)
        lines = ["DUB rows (index, start s, text, -> source lines it shows now):"]
        for i in range(lo, hi + 1):
            c = cur.get(i)
            lines.append(f"D{i:>4} {_fmt_ms(dub[i].get('start_ms', 0))}  {dub[i]['text']}  -> "
                         + (f"S{c[0]}" + (f"-S{c[1]}" if c[1] != c[0] else "") if c else "(no footage)"))
        s_lo = min(min(cur[r][0] for r in rows if r in cur), min(j for j, _, _ in chunk))
        s_hi = max(max(cur[r][1] for r in rows if r in cur), max(j for j, _, _ in chunk))
        lines.append("\nSOURCE sentences (UNUSED = shown under no dub row):")
        for j in range(max(0, s_lo - 1), min(len(src) - 1, s_hi + 1) + 1):
            users = [f"D{r}" for r, (a, b) in cur.items() if a <= j <= b]
            lines.append(f"S{j:>4} {_fmt_ms(src[j].get('start_ms', 0))}  {src[j]['text']}  "
                         + (f"(shown under {', '.join(users)})" if users else "<== UNUSED"))
        lines.append("\nUnused lines to decide: " + "; ".join(
            f"S{j} (row before: {'D' + str(a) if a is not None else 'none'}, row after: {'D' + str(b) if b is not None else 'none'})"
            for j, a, b in chunk))
        return "\n".join(lines)
    chunks, curc = [], []
    for it in items:                                    # neighbouring unused lines go in one request
        if curc and (len(curc) >= per_call or it[0] - curc[-1][0] > 12):
            chunks.append(curc); curc = []
        curc.append(it)
    if curc:
        chunks.append(curc)
    def run(chunk):
        return chunk, _ask_key(UNUSED + "\n\n" + _names_txt(names) + block(chunk), model, usage, log,
                               f"unused S{chunk[0][0]}-S{chunk[-1][0]}")
    answers = {}
    with ThreadPoolExecutor(workers) as ex:
        for chunk, ans in ex.map(run, chunks):
            for d in ans or []:
                try:
                    answers[int(d.get("src"))] = d
                except (TypeError, ValueError):
                    pass
    ext = {}                                             # dub row -> new (first, last)
    for j, a, b in items:
        d = answers.get(j) or {}
        how = (d.get("attach") or "none").lower()
        r = a if how == "prev" else b if how == "next" else None
        if r is None or r not in cur:
            report["left"].append(j)
            D.setdefault("unused_notes", {})[str(j)] = "AI: " + (d.get("reason") or "not told in the dub")
            continue
        f0, f1 = ext.get(r, cur[r])
        ext[r] = (min(f0, j), max(f1, j))
        report["attached"].append([j, r, d.get("reason") or ""])
    in_group = {m: g for g in D.get("groups", []) for m in g["members"]}
    for r, (f0, f1) in ext.items():
        if any(j in used for j in range(f0, f1 + 1) if not (cur[r][0] <= j <= cur[r][1])):
            continue                                     # would take another row's footage: leave it
        new = f0 if f0 == f1 else [f0, f1]
        why = "AI review: also shows " + ", ".join(f"S{j}" for j, rr, _ in report["attached"] if rr == r)
        if r in in_group:
            g = in_group[r]; g0 = g["src"] if isinstance(g["src"], list) else [g["src"], g["src"]]
            f0, f1 = min(f0, g0[0]), max(f1, g0[1]); new = f0 if f0 == f1 else [f0, f1]
            g["src"] = new; g["reason"] = (g.get("reason") or "") + "; " + why
        else:
            D.setdefault("confirm", {}).pop(str(r), None)
            old = D.get("assign", {}).get(str(r), {}).get("reason")
            D.setdefault("assign", {})[str(r)] = {"src": new, "reason": ((old + "; ") if old else "") + why}
    log(f"review of unused source lines: {len(unused)} unused, {len(report['attached'])} given to a neighbour row, "
        f"{len(report['left'])} noted as left out")
    return dec.to_spec(p, D, "# Step 2 decisions by AI (Dub Studio, automatic) + review of unused source lines"), report


def _ask_key(prompt, model, usage, log, label, key="unused"):
    text, u = ai.chat(prompt, model=model, max_tokens=3000)
    usage["calls"] += 1
    usage["prompt_tokens"] += u.get("prompt_tokens", 0); usage["completion_tokens"] += u.get("completion_tokens", 0)
    try:
        return ai.extract_json(text, key).get(key, [])
    except Exception as ex:
        log(f"{label}: could not read reply ({ex})"); return []


AUTO = "ai_auto.json"
LOW_CONFIDENCE = 0.7


def _conf(c):
    try:
        return float(c)
    except (TypeError, ValueError):
        return {"high": 0.9, "medium": 0.6, "low": 0.3}.get(str(c).lower(), 1.0)


def _drop_clashes(w, p, spec, overlaps, conf):
    """For each pair of rows on the same footage: the row the AI (or the matcher) is less sure of loses its footage
    and becomes a gap. Rows inside a group are kept. -> (new spec, [dropped rows])."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "prep.json").write_text(json.dumps(p))
        dec.from_spec(tmp, spec)
        D = json.loads((Path(tmp) / "decisions.json").read_text())
    group_of = {m for g in D["groups"] for m in g["members"]}
    dropped = []
    for a, b, *_ in overlaps:
        if a in dropped or b in dropped:
            continue
        cands = [x for x in (a, b) if x not in group_of]
        if not cands:
            # both rows sit in groups (2026-10-27, TGS ep32 rows 93/99/100): the less sure row that is the FIRST or LAST
            # member of its group leaves the group and becomes a gap; a group left with one row becomes that row's assign
            edge = []
            for x in (a, b):
                g = next(g for g in D["groups"] if x in g["members"])
                if x in (g["members"][0], g["members"][-1]):
                    edge.append((x, g))
            if not edge:
                continue
            x, g = min(edge, key=lambda e: (conf.get(e[0], 0.95), -e[0])); other = b if x == a else a
            g["members"] = [m for m in g["members"] if m != x]
            if len(g["members"]) == 1:
                D["groups"].remove(g)
                D["assign"][str(g["members"][0])] = {"src": g["src"], "reason": g.get("reason", "")}
            D["gap"][str(x)] = f"not found: its footage is also used by row {other} (AI matching); add a clip"
            dropped.append(x); group_of = {m for g in D["groups"] for m in g["members"]}
            continue
        x = min(cands, key=lambda r: (conf.get(r, 0.95), -r))          # least sure; on a tie the later row
        other = b if x == a else a
        D["assign"].pop(str(x), None); D["confirm"].pop(str(x), None)
        D["gap"][str(x)] = f"not found: its footage is also used by row {other} (AI matching); add a clip"
        dropped.append(x)
    return dec.to_spec(p, D, "# Step 2 decisions by AI (Dub Studio, automatic)"), dropped


def _fill_gaps(w, p, spec):
    """Flagged rows the spec leaves open become gaps ("not found")."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "prep.json").write_text(json.dumps(p))
        dec.from_spec(tmp, spec)
        left = dec.missing(tmp) or []
    extra = [f'GAP({i},{json.dumps(_kw(p["dub"][i]["text"]), ensure_ascii=False)},"not found: the AI could not place this row")'
             for i in left]
    return spec.rstrip("\n") + ("\n" + "\n".join(extra) if extra else "") + "\n"


def auto_match(work, check, model=None, log=print, names_csv=None, review_unused_lines=True,
               review_all_rows=True):
    """The decisions step without a person: Claude decides every flagged row (with the show's names, and a second
    look at every "no footage" answer), neighbours on one footage are joined into groups, a row whose footage clashes
    with a row further away is read once more, and only what still has no place becomes a gap ("not found": red in
    the editor, to fill by hand). Imported once the pipeline's integrity check passes. Writes W/ai_auto.json."""
    w = Path(work)
    prep_mtime = (w / "prep.json").stat().st_mtime
    r = propose(w, model, log=log, names_csv=names_csv)
    r = apply_group_rule(w, r, check, log=log)
    p = json.loads((w / "prep.json").read_text())
    props, names, case_a = r.get("_props") or {}, r.get("_names") or [], set(r.get("_case_a") or [])
    flagged = sorted(props) if props else []
    usage = r.setdefault("usage", {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
    spec = _fill_gaps(w, p, r["spec"])
    review = None
    if review_all_rows:
        try:
            spec, review_full = review_rows(w, p, spec, names, model, usage, log, case_a=case_a)
        except Exception as ex:                          # an extra: never lose the decisions over it
            review_full = {"error": str(ex)}; log(f"full read skipped: {ex}")
    else:
        review_full = None
    if review_unused_lines:
        try:
            spec, review = review_unused(w, p, spec, names, model, usage, log, case_a=case_a)
        except Exception as ex:                          # the review is an extra: never lose the decisions over it
            log(f"review of unused source lines skipped: {ex}")
    chk = check(spec)
    conf = {x["row"]: _conf(x.get("confidence")) for x in r["proposals"]}
    clashes, reasked = [], set()
    for _ in range(5):
        if not chk.get("overlaps"):
            break
        spec2, j2, _left = dec.join_shared(w, spec, chk["overlaps"])
        if j2:
            spec, chk = spec2, check(spec2); r.setdefault("joined", []).extend(j2)
            if not chk.get("overlaps"):
                break
        # a row on footage another row (too far away to group) already shows: read it once more before giving up
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "prep.json").write_text(json.dumps(p)); dec.from_spec(tmp, spec)
            D = json.loads((Path(tmp) / "decisions.json").read_text())
        in_group = {m for g in D["groups"] for m in g["members"]}
        ask, notes = [], {}
        for a, b, *_ in chk["overlaps"]:
            cands = [x for x in (a, b) if x not in in_group and x in props and x not in reasked]
            if cands:
                x = min(cands, key=lambda q: (conf.get(q, 0.95), -q)); other = b if x == a else a
                ask.append(x); notes[x] = (f"Its footage is already shown by D{other} (too far away to be one group): "
                                           f"choose OTHER footage for D{x} -- a group with its own neighbour, or an "
                                           f"unused line -- or answer gap.")
        if ask:
            reasked |= set(ask)
            log(f"rows {ask} share footage with rows further away: second look")
            found = second_look(p, ask, props, case_a, names, model, usage, log, notes=notes)
            if found:
                for x, d in found.items():
                    props[x] = dict(d, second_look=True)
                spec, r["proposals"] = _build(p, flagged, props, model)
                spec = _fill_gaps(w, p, spec); chk = check(spec)
                continue
        spec, dropped = _drop_clashes(w, p, spec, chk["overlaps"], conf)
        if not dropped:
            break
        clashes += dropped; log(f"same footage, too far apart for a group: row(s) {dropped} set to not found")
        chk = check(spec)
    if not chk.get("ok", True) or chk.get("problems"):
        (w / "spec_ai_failed.py").write_text(spec)
        raise ValueError("the AI's decisions do not pass the pipeline's check: " + "; ".join((chk.get("problems") or ["?"])[:3]))
    counts = dec.from_spec(w, spec)
    D = json.loads((w / "decisions.json").read_text())
    by_row = {x["row"]: x for x in r["proposals"]}
    not_found = sorted(int(k) for k in D["gap"])
    low = [dict(row=x["row"], confidence=x.get("confidence"), reason=x.get("reason") or "")
           for x in r["proposals"] if x.get("action") not in ("gap", "none") and not x.get("error")
           and _conf(x.get("confidence")) < LOW_CONFIDENCE and x["row"] not in not_found]
    res = dict(at=time.time(), prep_mtime=prep_mtime, model=model or ai.settings()["AI_MODEL"], rows=len(by_row),
               not_found=not_found, low=low, joined=r.get("joined", []), clashes=clashes, counts=counts,
               second_look=sorted(x["row"] for x in r["proposals"] if x.get("second_look")), names=len(names),
               usage=usage, spec=spec, review=review, review_full=review_full)
    (w / AUTO).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    log(f"decided {len(by_row)} rows: {len(not_found)} not found, {len(low)} unsure")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work"); ap.add_argument("--model"); ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--apply", action="store_true", help="also write decisions.json via from_spec")
    a = ap.parse_args()
    r = propose(a.work, a.model, a.workers)
    print(json.dumps(r["usage"]), f"{len(r['proposals'])} rows -> {Path(a.work) / 'spec_ai.py'}")
    if a.apply:
        print(dec.from_spec(a.work, r["spec"]))


if __name__ == "__main__":
    sys.exit(main())
~~~~~~

## Appendix: `ai/dubstudio/pipeline/decisions.py`

<!-- FILE: ai/dubstudio/pipeline/decisions.py md5=98abe5051aa27c1150386ea34b2a10a0 -->
~~~~~~python
"""Step 2 inputs without the editor UI (milestone 1): import a decisions.json or a spec.py (the skill's mkdec
format), or accept the automatic matches (Case A rows confirmed, unmatched rows marked as gaps)."""
import json
from pathlib import Path


def flagged_rows(work):
    """Rows `sheet` refuses without a decision: unmatched + Case A."""
    p = json.loads((Path(work) / "prep.json").read_text())
    auto = {int(k): v for k, v in p["auto"].items()}
    unmatched = [i for i in range(len(p["dub"])) if i not in auto]
    case_a = [i for i, v in auto.items() if v.get("kind") == "Case A reuse"]
    return p, unmatched, sorted(case_a)


def accept_auto(work):
    p, unmatched, case_a = flagged_rows(work)
    d = {"groups": [], "assign": {}, "confirm": {str(i): "accepted automatically in Dub Studio (not read by meaning)"
                                                 for i in case_a},
         "repeat": {}, "gap": {str(i): "no automatic match (accepted automatically in Dub Studio)" for i in unmatched},
         "unused_notes": {}}
    (Path(work) / "decisions.json").write_text(json.dumps(d, indent=1, ensure_ascii=False))
    return {"confirmed": len(case_a), "gaps": len(unmatched)}


def from_spec(work, spec_text):
    """The skill's mkdec.py: spec lines G/A/C/GAP/U/R checked against the row texts."""
    p = json.loads((Path(work) / "prep.json").read_text()); dub, src = p["dub"], p["src"]
    D = {"groups": [], "assign": {}, "confirm": {}, "repeat": {}, "gap": {}, "unused_notes": {}}

    def chk(lst, i, kw, lab):
        t = lst[i]["text"]
        if kw.lower() not in t.lower():
            raise ValueError(f"{lab}{i} '{t}' lacks '{kw}'")
        return t[:30].strip()

    s0 = lambda s: s[0] if isinstance(s, list) else s
    def G(members, s, skw, why):
        D["groups"].append({"members": [m for m, _ in members], "dub_prefixes": [chk(dub, m, k, "DUB#") for m, k in members],
                            "src": s, "src_prefix": chk(src, s0(s), skw, "SRC#"), "reason": why})
    def A(i, kw, s, skw, why):
        D["assign"][str(i)] = {"dub_prefix": chk(dub, i, kw, "DUB#"), "src": s, "src_prefix": chk(src, s0(s), skw, "SRC#"),
                               "reason": why}
    def C(i, why): D["confirm"][str(i)] = why
    def GAP(i, kw, why): chk(dub, i, kw, "DUB#"); D["gap"][str(i)] = why
    def U(s, kw, why): chk(src, s, kw, "SRC#"); D["unused_notes"][str(s)] = why
    def R(later, earlier): D["repeat"][str(later)] = earlier
    exec(compile(spec_text, "spec.py", "exec"), {"G": G, "A": A, "C": C, "GAP": GAP, "U": U, "R": R, "__builtins__": {}})
    (Path(work) / "spec.py").write_text(spec_text)
    (Path(work) / "decisions.json").write_text(json.dumps(D, indent=1, ensure_ascii=False))
    return {k: len(v) for k, v in D.items()}


def missing(work):
    """Flagged rows still without a decision (empty = the gate opens)."""
    w = Path(work)
    if not (w / "decisions.json").exists() or not (w / "prep.json").exists():
        return None
    d = json.loads((w / "decisions.json").read_text())
    done = set(map(int, d.get("assign", {}))) | set(map(int, d.get("confirm", {}))) | set(map(int, d.get("gap", {}))) \
        | set(map(int, d.get("repeat", {}))) | {m for g in d.get("groups", []) for m in g["members"]}
    _, unmatched, case_a = flagged_rows(w)
    return sorted(i for i in unmatched + case_a if i not in done)


def check_spec(work, spec_text, python, scripts, labels, timeout=180):
    """Dry run: the spec's decisions go through the pipeline's own `sheet` integrity check in a scratch copy of the
    work folder (top-level .json/.srt files only; nothing in the real work folder changes).
    -> {"ok": bool, "problems": [str], "overlaps": [[row, row, ms]], "counts": {...}, "log": [str]}"""
    import ast, re, shutil, subprocess, tempfile
    w = Path(work)
    with tempfile.TemporaryDirectory(prefix="dubstudio_check_") as tmp:
        t = Path(tmp)
        for f in w.iterdir():
            if f.is_file() and f.suffix in (".json", ".srt"):
                shutil.copy2(f, t / f.name)
        for f in ("decisions.json", "spec.py"):
            (t / f).unlink(missing_ok=True)
        try:
            counts = from_spec(t, spec_text)
        except Exception as ex:
            return {"ok": False, "problems": [f"the decisions file does not read: {ex}"], "overlaps": [], "counts": {}, "log": []}
        p = subprocess.run([str(python), str(Path(scripts) / "dubstitch.py"), "sheet", "--work", str(t), "--out",
                            str(t / "check.xlsx"), "--labels", labels], capture_output=True, text=True, timeout=timeout,
                           cwd=str(scripts))
        out = (p.stdout + "\n" + p.stderr).splitlines()
    overlaps, problems = [], []
    for ln in out:
        m = re.search(r"row pairs sharing footage.*?:\s*(\[.*\])", ln)
        if m:
            try:
                overlaps = [list(x) for x in ast.literal_eval(m.group(1))]
            except Exception:
                pass
        if re.match(r"\s+DUB#\d+", ln) or "integrity check failed" in ln or "0ms rows: [" in ln and ln.strip() != "0ms rows: []":
            problems.append(ln.strip())
    if p.returncode != 0 and not problems and not overlaps:
        problems.append((out[-1] if out else f"sheet exit code {p.returncode}").strip())
    for a, b, ms in overlaps:
        problems.append(f"rows {a} and {b} show the same source footage ({ms} ms overlap)")
    return {"ok": p.returncode == 0, "problems": problems, "overlaps": overlaps, "counts": counts, "log": out[-30:]}


def _kw(text, n=3):
    w = text.split()
    return " ".join(w[:n]) if w else text


def to_spec(p, D, header="# Step 2 decisions (Dub Studio)"):
    """decisions dict -> spec.py text in the skill's G/A/C/GAP/R/U language (keywords = the first words)."""
    dub, src = p["dub"], p["src"]; q = lambda x: json.dumps(x, ensure_ascii=False)
    s0 = lambda s: s[0] if isinstance(s, list) else s
    items = []
    for g in D.get("groups", []):
        m = ",".join(f"({i},{q(_kw(dub[i]['text']))})" for i in g["members"])
        items.append((g["members"][0], f"G([{m}],{q(g['src'])},{q(_kw(src[s0(g['src'])]['text']))},{q(g.get('reason', ''))})"))
    for i, a in D.get("assign", {}).items():
        i = int(i)
        items.append((i, f"A({i},{q(_kw(dub[i]['text']))},{q(a['src'])},{q(_kw(src[s0(a['src'])]['text']))},{q(a.get('reason', ''))})"))
    for i, why in D.get("confirm", {}).items():
        items.append((int(i), f"C({int(i)},{q(why)})"))
    for i, why in D.get("gap", {}).items():
        items.append((int(i), f"GAP({int(i)},{q(_kw(dub[int(i)]['text']))},{q(why)})"))
    for i, e in D.get("repeat", {}).items():
        items.append((int(i), f"R({int(i)},{int(e)})"))
    for s, why in D.get("unused_notes", {}).items():
        items.append((10 ** 6 + int(s), f"U({int(s)},{q(_kw(src[int(s)]['text']))},{q(why)})"))
    return header + "\n" + "\n".join(t for _, t in sorted(items)) + "\n"


def join_shared(work, spec_text, overlaps, max_span=3):
    """Neighbouring rows that claim the same source footage become one group (the hand-read rule: one source
    sentence told by several dub rows = a group, its window split by dub duration). Pairs further apart than
    max_span rows are left alone. -> (new spec text, [[first, last], ...] joined, [pairs left])"""
    p = json.loads((Path(work) / "prep.json").read_text())
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "prep.json").write_text(json.dumps(p))
        from_spec(tmp, spec_text)
        D = json.loads((Path(tmp) / "decisions.json").read_text())
    auto = p["auto"]
    span = lambda s: (s[0], s[1]) if isinstance(s, list) else (s, s)
    group_of = {m: k for k, g in enumerate(D["groups"]) for m in g["members"]}

    def row_span(i):
        if i in group_of:
            return span(D["groups"][group_of[i]]["src"])
        if str(i) in D["assign"]:
            return span(D["assign"][str(i)]["src"])
        if str(i) in D["gap"] or str(i) in D["repeat"]:
            return None
        return span(auto[str(i)]["src"]) if str(i) in auto else None

    runs, left = [], []
    for a, b, _ in sorted(overlaps):
        a, b = min(a, b), max(a, b)
        if b - a > max_span:
            left.append([a, b]); continue
        runs.append([a, b])
    # merge touching runs, then grow each run to cover whole existing groups, until nothing changes
    while True:
        runs.sort(); out = []
        for r in runs:
            if out and r[0] <= out[-1][1]:
                out[-1][1] = max(out[-1][1], r[1])
            else:
                out.append(list(r))
        grew = False
        for r in out:
            for i in range(r[0], r[1] + 1):
                if i in group_of:
                    mem = D["groups"][group_of[i]]["members"]
                    if mem[0] < r[0] or mem[-1] > r[1]:
                        r[0], r[1] = min(r[0], mem[0]), max(r[1], mem[-1]); grew = True
        runs = out
        if not grew:
            break
    keep_groups = [g for g in D["groups"] if not any(r[0] <= g["members"][0] <= r[1] for r in runs)]
    new_groups = []
    for lo, hi in runs:
        spans = [s for s in (row_span(i) for i in range(lo, hi + 1)) if s]
        if not spans:
            continue
        a0, b0 = min(s[0] for s in spans), max(s[1] for s in spans)
        reasons = []
        for i in range(lo, hi + 1):
            for d in (D["assign"].pop(str(i), None), D["confirm"].pop(str(i), None), D["gap"].pop(str(i), None)):
                if isinstance(d, dict) and d.get("reason"):
                    reasons.append(d["reason"])
                elif isinstance(d, str):
                    reasons.append(d)
        for g in D["groups"]:
            if lo <= g["members"][0] <= hi and g.get("reason"):
                reasons.append(g["reason"])
        new_groups.append({"members": list(range(lo, hi + 1)), "src": a0 if a0 == b0 else [a0, b0],
                           "reason": f"joined in Dub Studio: rows {lo}-{hi} share source footage. " + " | ".join(reasons)[:400]})
    D["groups"] = sorted(keep_groups + new_groups, key=lambda g: g["members"][0])
    return to_spec(p, D), [[g["members"][0], g["members"][-1]] for g in new_groups], left


def alternatives(prep, n=5, rows=None):
    """{row: [[src, score], ...]}: the n source sentences closest in meaning to each flagged dub row (or `rows`),
    from the similarity table the automatic matching already computed (prep["sim"], dub x src)."""
    sim = prep.get("sim") or []
    if rows is None:
        auto = prep.get("auto", {})
        rows = [i for i in range(len(prep.get("dub", []))) if str(i) not in auto or "Case A" in auto[str(i)].get("kind", "")]
    out = {}
    for i in rows:
        if i < len(sim):
            best = sorted(range(len(sim[i])), key=lambda k: -sim[i][k])[:n]
            out[str(i)] = [[k, round(float(sim[i][k]), 3)] for k in best]
    return out
~~~~~~

## Appendix: `ai/dubstudio/pipeline/translate_ai.py`

<!-- FILE: ai/dubstudio/pipeline/translate_ai.py md5=4b64d4cbe2fbba61753944f5c926ba69 -->
~~~~~~python
"""Translation by Claude (AI gateway), for when the pipeline's Google translation is blocked or fails.

The pipeline stays in charge of the subtitle file: `translate --export-todo` lists the sentences that have no
translation yet, Claude fills them here, and `translate --engine file --import` builds the English SRT from them
(same sentence split, character names kept, the dub's exact cue times)."""
import json
from concurrent.futures import ThreadPoolExecutor
from .. import ai

LANG = {"it": "Italian", "en": "English", "de": "German", "fr": "French", "es": "Spanish", "pt": "Portuguese"}

PROMPT = """Translate these {sl} subtitle sentences of a dubbed drama episode into natural, spoken {tl} for
subtitles. Keep every character name exactly as written (do not anglicise names). Keep the meaning and the tone;
do not add or explain anything. Keep quotation marks « » where the sentence has them.
Answer ONLY with JSON: {{"t": ["translation of 1", "translation of 2", ...]}} -- one string per numbered sentence, same order.

{items}"""


def translate(texts, sl="it", tl="en", model=None, batch=40, log=print):
    """[text] -> [translation] (same order). Several calls in parallel; each batch is checked for its count."""
    batches = [texts[k:k + batch] for k in range(0, len(texts), batch)]

    def run(b):
        items = "\n".join(f"{k + 1}. {t}" for k, t in enumerate(b))
        for attempt in range(3):
            text, _ = ai.chat(PROMPT.format(sl=LANG.get(sl, sl), tl=LANG.get(tl, tl), items=items),
                              model=model, max_tokens=8000)
            try:
                out = ai.extract_json(text, "t")["t"]
                if len(out) == len(b) and all(isinstance(x, str) and x.strip() for x in out):
                    return [x.strip() for x in out]
            except Exception:
                pass
            log(f"batch of {len(b)}: the reply did not match, asking again")
        raise RuntimeError(f"the AI did not translate a batch of {len(b)} sentences")

    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(run, batches))
    return [t for b in res for t in b]


def fill_todo(todo_tsv, out_tsv, sl="it", tl="en", model=None, log=print):
    """The pipeline's TSV of untranslated sentences (text<TAB>) -> the same TSV filled (text<TAB>translation)."""
    texts = [ln.rstrip("\n").split("\t", 1)[0] for ln in open(todo_tsv, encoding="utf-8") if ln.strip()]
    if not texts:
        open(out_tsv, "w", encoding="utf-8").write(""); return 0
    log(f"translating {len(texts)} sentences with {model or ai.settings()['AI_MODEL']}")
    tr = translate(texts, sl, tl, model, log=log)
    with open(out_tsv, "w", encoding="utf-8") as f:
        for a, b in zip(texts, tr):
            f.write(f"{a}\t{' '.join(b.split())}\n")
    return len(texts)


def one(text, sl="it", tl="en", model=None, before="", after=""):
    """One subtitle line (the editor's re-translate), with its neighbours as context."""
    ctx = (f"\nContext only (do not translate): before: {before!r}; after: {after!r}" if before or after else "")
    text_out, _ = ai.chat(f"Translate this {LANG.get(sl, sl)} subtitle line of a drama into natural spoken "
                          f"{LANG.get(tl, tl)}. Keep character names as written. Answer ONLY with JSON "
                          f'{{"t": "..."}}.{ctx}\n\nLine: {text}', model=model or ai.settings()["AI_MODEL_FAST"],
                          max_tokens=600)
    return ai.extract_json(text_out, "t")["t"].strip()
~~~~~~
