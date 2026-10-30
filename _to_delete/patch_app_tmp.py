"""Dub Studio: HIH preset + the dynamic-subtitle steps (captions -> dynsrt -> subs --style dynamic). Run from app/backend."""
import re
p = "dubstudio/presets.py"; s = open(p).read()
old = '''    "WBM": dict(BASE, name="WBM (Italian dub vs US)", labels="IT,US", src_lang="en", output_dir="outputs/ep{N}"),'''
new = old + '''
    # HIH: the US video has burned-in captions (2 lines, sometimes 1 or 3): "dynamic" = a white strip 2 caption lines tall,
    # 3 where a 3-line US caption is on screen, with Italian cards that fill it (owner, 2026-10-22). Other shows get it
    # only when the owner says so.
    "HIH": dict(BASE, name="HIH (Italian dub vs US)", labels="IT,US", src_lang="en", sub_style="dynamic"),'''
assert s.count(old) == 1 and '"HIH"' not in s; s = s.replace(old, new); open(p, "w").write(s)

p = "dubstudio/pipeline/steps.py"; s = open(p).read()
old = '''    add("proxy", fn="proxy", needs=["ingest"], label="Preview copy (540 px)", duration=dur_src)'''
new = '''    dynamic = {**{"style": prof.get("sub_style", "box")}, **((prof.get("sub_styles") or {}).get("dub") or {})}["style"] == "dynamic"
    dyn_srt = str(W / f"{Path(dub_sub).stem}_dynamic.srt")
    if dynamic:   # HIH: read the US captions while the rest runs (OCR, CPU)
        add("captions", [py, T("dubstitch.py"), "captions", "--work", str(W), "--video", assets["src_video"]], ["ingest"],
            resource="cpu", label="Read the source captions (lines per caption)", duration=dur_src)
''' + old
assert s.count(old) == 1; s = s.replace(old, new)
old = '''    add("review", fn="review_gate", needs=["fastfix", "translate"], resource="none", label="Review & edit (editor)")'''
new = '''    add("review", fn="review_gate", needs=["fastfix"] if dynamic else ["fastfix", "translate"], resource="none",
        label="Review & edit (editor)")
    if dynamic:   # the cards follow the FINAL edit list (after the editor), so they are made after review
        add("dynsrt", [py, T("dubstitch.py"), "dynsrt", "--work", str(W), "--words", str(dub_sub_orig)[:-4] + "_words.json",
                       "--out", dyn_srt] + (["--font", cfg.font] if cfg.font else []), ["review", "captions"],
            label="Dynamic subtitles (cards fill a 2- or 3-line strip)")
        dub_sub = dyn_srt'''
assert s.count(old) == 1; s = s.replace(old, new)
old = '''                     ctx["name_dub_subs"]] + font, [last], label=f"{D} subtitle video", duration=dur_dub)'''
new = '''                     ctx["name_dub_subs"]] + font, [last] + (["dynsrt"] if dynamic else []), label=f"{D} subtitle video",
        duration=dur_dub)'''
assert s.count(old) == 1; s = s.replace(old, new)
old = '''                      prof.get("translate_to", "en"), "--out", ctx["name_en_srt"]], ["analyze"], resource="cpu",'''
new = '''                      prof.get("translate_to", "en"), "--out", ctx["name_en_srt"]], ["dynsrt"] if dynamic else ["analyze"], resource="cpu",'''
assert s.count(old) == 1; s = s.replace(old, new)
if "from pathlib import Path" not in s and "import Path" not in s:
    s = "from pathlib import Path\n" + s
open(p, "w").write(s)
print("patched presets.py + steps.py")
