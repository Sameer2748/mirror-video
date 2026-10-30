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
