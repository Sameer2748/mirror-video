"""Build SKILL v19 from v18: dynamic strip (HIH), run rule, unified settings, current tool code in the Appendix."""
import hashlib, os, re, sys
SK = sys.argv[1]; TOOLS = sys.argv[2]; OUT = sys.argv[3]; SEC8C = open(sys.argv[4], encoding="utf-8").read().strip()
s = open(SK, encoding="utf-8").read()
def rep(old, new, n=1):
    global s
    assert s.count(old) == n, (s.count(old), old[:80]); s = s.replace(old, new)
md5 = lambda p: hashlib.md5(open(p, "rb").read()).hexdigest()

rep('Self-contained: all tool code is included."', 'Self-contained: all tool code is included. v19: dynamic subtitle strip for HIH."')
rep("## What changed in v18 (Oct 2026, WBM ep17 QC)", """## Run rule (owner, 2026-10-22): every step, every episode
- **Always run every step of the pipeline**, in order, for every episode: Step 0B (Whisper both sides), 1A/1B, Step 2
  (the read by meaning), sheet, manifest, the lip scan (Step 4B), groups + the fast-clip rule, render, verify,
  bookends, the subtitle videos in the show's style, translation, check. Never skip or replace a step on your own.
- **If a step cannot run, stop and ask the owner.** Say which step and why (e.g. the Mac runner is stopped, the Dub Studio
  app is not running, the computer is not reachable, the AI gateway/VPN is off). Ask him to start it (double-click
  `adaptation-tool/mac_lipscan/mac_runner.command`; the app: `dub-studioo/app/scripts/start.sh`), wait, then continue.
  Never deliver an episode that skipped a step without saying so first.

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
- **Fast-clip rule (2026-10-22):** no clip plays faster than 1.25x: `groups --trim-fast 1.25 --trim-to 1.2` trims
  fast narration blocks to about 1.2x; in Dub Studio, `fastfix.py` trims the remaining fast pieces (lip pieces: Claude
  picks the cuts from the shots).

## What changed in v18 (Oct 2026, WBM ep17 QC)""")
rep("                mksrt.py  srtclean.py\n", "                mksrt.py  srtclean.py  dynsubs.py\n")
rep("| `pngsubs.py` | box-style subtitles (transparent PNG stills turned into an overlay) | Steps 8-9 (`subs --style box`) |",
    "| `pngsubs.py` | box-style subtitles (transparent PNG stills turned into an overlay) | Steps 8-9 (`subs --style box`) |\n"
    "| `dynsubs.py` | dynamic strip (HIH): reads the source captions (OCR), makes the cards that fill a 2/3-line strip, burns them | Step 8C (`captions`, `dynsrt`, `subs --style dynamic`) |")
rep("Input file names change from show to show and sometimes inside a show", """**HIH** (Italian dub vs US, `HIH US-IT/`): like WBM (no SRTs: Step 0B both sides; Italian script
`italy/Script/Episode <N> - <title>.docx`, audio `italy/Audio/Episode <N> - <title>.wav`, US video `US/EP_<N>.mp4` or
`US/Ep <N> - <title>.mp4`), but the US video has English captions burned in → `render --subs keep` and the
**dynamic strip** (Step 8C) for both subtitle videos. Outputs `HIH US-IT/output/ep<N>/`, `HIH_EP<N>_IT_...`,
`hih_ep<N>_it_us_mapping.xlsx`. Needs `tesseract` on the machine (`brew install tesseract`).

Input file names change from show to show and sometimes inside a show""")
rep("## Step 9. translate + subs: the English-subtitle video (mandatory)", SEC8C + "\n\n## Step 9. translate + subs: the English-subtitle video (mandatory)")
rep("The English video uses the same style as the Italian one.",
    "The English video uses the same style as the Italian one. HIH (dynamic strip): translate the **dynamic** SRT "
    "(`--srt W/<SHOW>_EP<N>_IT_dynamic.srt`), so the English cards keep the Italian cards' times and strips (Step 8C).")
rep("- [ ] `translate` (not blocked; names kept) then `subs --srt` English in the same style.",
    "- [ ] (HIH) `captions` (3-line captions listed), `dynsrt` (words in = words out, 3-line zones noted), both videos\n"
    "      with `subs --style dynamic`: self-check 0/0, `subs_qc_<name>.jpg` looked at (no US caption above the strip).\n"
    "- [ ] `translate` (not blocked; names kept) then `subs --srt` English in the same style.")
rep("- [ ] Same episode confirmed", "- [ ] Every step run (run rule); anything that could not run was reported to the owner before going on.\n- [ ] Same episode confirmed")
# Step 14 table
old_d = re.search(r"\| `scripts/dubstitch.py` \| ([0-9a-f]{32}) \| [^\n]*\n", s).group(0)
rep(old_d, f"| `scripts/dubstitch.py` | {md5(TOOLS + '/scripts/dubstitch.py')} | every command: words, analyze, sheet, manifest, lipsync, "
           "groups, render, verify, bookends, transitions (and `glow`), subs (`--style strip\\|box\\|dynamic`), captions, dynsrt, "
           "translate, check, lipmark, speech, listen (`python3 dubstitch.py -h`) |\n")
old_m = re.search(r"\| `scripts/mksrt.py` \| ([0-9a-f]{32}) \| [^\n]*\n", s).group(0)
rep(old_m, old_m.replace(re.search(r"[0-9a-f]{32}", old_m).group(0), md5(TOOLS + "/scripts/mksrt.py"))[:-3]
    + "; also `<out>_words.json` |\n")
old_p = re.search(r"\| `scripts/pngsubs.py` \| [^\n]*\n", s).group(0)
rep(old_p, old_p + f"| `scripts/dynsubs.py` | {md5(TOOLS + '/scripts/dynsubs.py')} | dynamic strip (Step 8C): caption OCR, cards, burn; "
                   "used by `captions`, `dynsrt`, `subs --style dynamic` |\n")
# Appendix blocks
def block(path):
    body = open(TOOLS + "/" + path, encoding="utf-8").read()
    assert body.endswith("\n") and not body.endswith("\n\n"), path
    lang = "python" if path.endswith(".py") else "bash"
    return f"<!-- FILE: {path} md5={md5(TOOLS + '/' + path)} -->\n~~~~~~{lang}\n{body[:-1]}\n~~~~~~\n"
for path in ("scripts/dubstitch.py", "scripts/mksrt.py"):
    m = re.search(r"<!-- FILE: " + re.escape(path) + r" md5=[0-9a-f]{32} -->\n~{6}[a-z]*\n.*?\n~{6}\n", s, re.S)
    s = s[:m.start()] + block(path) + s[m.end():]
m = re.search(r"<!-- FILE: scripts/pngsubs.py md5=[0-9a-f]{32} -->\n~{6}[a-z]*\n.*?\n~{6}\n", s, re.S)
s = s[:m.end()] + "\n## Appendix: `scripts/dynsubs.py`\n\n" + block("scripts/dynsubs.py") + s[m.end():]
open(OUT, "w", encoding="utf-8").write(s)
# round trip: every appendix file == the tool on disk
files = re.findall(r"<!-- FILE: (\S+) md5=([0-9a-f]{32}) -->\n~{6}[a-z]*\n(.*?)\n~{6}\n", s, re.S)
bad = [p for p, h, b in files if hashlib.md5((b + "\n").encode()).hexdigest() != h]
print(len(files), "appendix files; md5 mismatches:", bad)
