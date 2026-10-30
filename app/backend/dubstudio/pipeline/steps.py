"""The pipeline as a graph of steps, built from a show profile + an episode's inputs (skill v18 run sheet).

Each step: name, resource ('cpu' | 'gpu' | 'none'), needs (step names), and either a command (run as a
subprocess with the pipeline python, cwd = the tools' scripts folder) or an internal function name."""
from pathlib import Path
from ..config import cfg
from .. import presets, names as nm


def srt_as_script(srt, out):
    """A given SRT's text as a plain script (one cue per line) for mksrt.py's cleaning."""
    import re
    if not Path(srt).is_file():
        return None
    txt = Path(srt).read_text(encoding="utf-8-sig", errors="replace").replace("\r", "")
    lines = []
    for block in re.split(r"\n\s*\n", txt):
        rows = [l for l in block.split("\n") if l.strip() and "-->" not in l and not l.strip().isdigit()]
        if rows:
            lines.append(" ".join(r.strip() for r in rows))
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out)


def plan(show, episode, assets):
    """-> (context, [step dicts]). assets: {kind: path}."""
    prof = show["profile"]; code = show["code"].upper(); N = episode["number"]
    W = Path(episode["work_dir"]); OUT = Path(episode["output_dir"])
    names = presets.names(code, N, prof)
    dl, sl = prof["dub_lang"], prof["src_lang"]
    D = prof.get("dub_label") or prof["labels"].split(",")[0]
    S_lab = prof["labels"].split(",")[1]
    M = str(cfg.models_dir)
    py = "{py}"; T = lambda s: f"{{scripts}}/{s}"
    ctx = {"W": str(W), "OUT": str(OUT), **{f"name_{k}": str(OUT / v) for k, v in names.items()}}
    steps = []

    def add(name, cmd=None, needs=(), resource="cpu", fn=None, label=None, duration=None, ai=False):
        # ai: the step needs the AI gateway -- the runner checks it (reachable, budget left) before the step starts
        steps.append({"step": name, "cmd": cmd, "needs": list(needs), "resource": resource, "fn": fn,
                      "label": label or name, "duration": duration, "ai": ai})

    dur_src = assets.get("_dur_src_video"); dur_dub = assets.get("_dur_dub_audio")
    add("ingest", fn="ingest", resource="none", label="Check files")
    # Step 0B for EVERY episode (owner, 2026-10-20): Whisper makes both sides' SRTs so every sentence has its exact
    # time, and each side is cleaned with its script so the words and sentence ends are right. Script per side: the
    # .docx/.txt script; else the given SRT's text (it has the right words and punctuation); else only the
    # localisation sheet's names. Two SRTs come out per side: one row per sentence (matching) and subtitle rows of at
    # most 7 words / 4 s (burned on the video).
    ncsv = nm.whisper_csv(show, W)
    SUB = ["--row-words", str(presets.SUB_WORDS), "--row-seconds", str(presets.SUB_SECONDS)]

    def step0b(side, media, lang, lab, script, given_srt, names_col, dur):
        base = W / f"{code}_EP{N}_{lab}.srt"
        cmd = [py, T("mksrt.py"), "--media", media, "--lang", lang, "--work", str(W), "--out", str(base),
               "--models", M] + SUB
        src_of_script = script or (srt_as_script(given_srt, W / f"script_{side}_from_srt.txt") if given_srt else None)
        if src_of_script:
            cmd += ["--script", src_of_script, "--sensitivity", "aggressive"]
        elif ncsv:
            cmd += ["--names", ncsv, "--names-cols", names_col]
        how = "script" if script else "its SRT as script" if given_srt else "names only"
        add(f"mksrt_{side}", cmd, ["ingest"], label=f"Make {lab} SRT (Whisper + {how})", duration=dur)
        return str(base).replace(".srt", "_sentences.srt"), str(base), f"mksrt_{side}"
    dub_match, dub_sub, need_d = step0b("dub", assets["dub_audio"], dl, D, assets.get("dub_script"),
                                        assets.get("dub_srt"), "target name", dur_dub)
    src_match, _src_sub, need_s = step0b("src", assets["src_video"], sl, sl.upper(), assets.get("src_script"),
                                         assets.get("src_srt"), "source name", dur_src)
    dynamic = {**{"style": prof.get("sub_style", "box")}, **((prof.get("sub_styles") or {}).get("dub") or {})}["style"] == "dynamic"
    dyn_srt = str(W / f"{Path(dub_sub).stem}_dynamic.srt")
    if dynamic:   # HIH: read the US captions while the rest runs (OCR, CPU)
        add("captions", [py, T("dubstitch.py"), "captions", "--work", str(W), "--video", assets["src_video"]], ["ingest"],
            resource="cpu", label="Read the source captions (lines per caption)", duration=dur_src)
    add("proxy", fn="proxy", needs=["ingest"], label="Preview copy (540 px)", duration=dur_src)
    add("sharpness", fn="sharpness", needs=["proxy"], label="Picture sharpness (blur flag)")
    # subtitles edited in the editor (W/subs_edited_*.srt) replace the SRT the subtitle videos are made from;
    # the show folder's own SRT stays as it is
    ed_dub, ed_en = W / "subs_edited_dub.srt", W / "subs_edited_en.srt"
    dub_sub_orig = dub_sub
    if ed_dub.exists():
        dub_sub = str(ed_dub)
    ctx.update(dub_srt=dub_match, sub_srt=dub_sub, sub_srt_orig=dub_sub_orig, src_srt=src_match,
               en_srt_burn=str(ed_en) if ed_en.exists() else ctx["name_en_srt"])
    cw, cl = presets.clause_mode(prof)                 # whole sentences, every show (owner, 2026-10-20)
    C = str(cw); CL = ["--clause-long", str(cl)] if cw and cl else []
    add("words_src", [py, T("dubstitch.py"), "words", "--work", str(W), "--side", "src", "--srt", src_match, "--media",
                      assets["src_video"], "--lang", sl, "--models", M, "--clauses", C] + CL, [need_s],
        label=f"{S_lab} sentence times", duration=dur_src)
    add("words_dub", [py, T("dubstitch.py"), "words", "--work", str(W), "--side", "dub", "--srt", dub_match, "--media",
                      assets["dub_audio"], "--lang", dl, "--models", M, "--clauses", C] + CL, [need_d],
        label=f"{D} sentence times", duration=dur_dub)
    add("analyze", [py, T("dubstitch.py"), "analyze", "--work", str(W), "--dub-srt", dub_match, "--dub-audio",
                    assets["dub_audio"], "--src-srt", src_match, "--src-video", assets["src_video"], "--models", M,
                    "--clauses", C] + CL, ["words_src", "words_dub"], label="Automatic matching")
    add("merged", fn="merged", needs=["analyze"], resource="none", label="Reading view (merged.txt)")
    add("decisions", fn="decisions_gate", needs=["merged"], resource="none", label="Check matching (decisions)")
    add("sheet", [py, T("dubstitch.py"), "sheet", "--work", str(W), "--out", ctx["name_mapping"], "--labels",
                  prof["labels"]], ["decisions"], label="Mapping sheet + integrity check")
    add("manifest", [py, T("dubstitch.py"), "manifest", "--work", str(W)], ["sheet"], label="Edit list")
    g_needs = ["manifest"]
    if True:                                           # the lip scan runs for every show (owner, 2026-10-16)
        nets, mesh = W / "lips_nets", W / "lips_mesh"
        add("lip_gpu", [py, T("lipgpu.py"), "--video", assets["src_video"], "--out-dir", str(nets)], ["ingest"],
            resource="gpu", label="Lip scan: faces + TalkNet (GPU)", duration=dur_src)
        add("lip_mesh", [py, T("lipmesh.py"), "--video", assets["src_video"], "--out-dir", str(mesh), "--ranges",
                         str(nets / "ranges.json")], ["lip_gpu"], label="Lip scan: lip opening", duration=dur_src)
        add("lip_merge", [py, T("lipmerge.py"), "--nets", str(nets), "--mesh", str(mesh), "--out", str(W / "lips_range")],
            ["lip_mesh"], label="Lip scan: merge")
        add("lipsync", [py, T("dubstitch.py"), "lipsync", "--work", str(W)], ["manifest", "lip_merge"],
            label="Lip-sync rows")
        g_needs = ["lipsync"]
    # Step 4A: "7 with lip scan, 8 without" (skill show profile); the lip scan runs for every show, so mode 1, size 7
    add("groups", [py, T("dubstitch.py"), "groups", "--work", str(W), "--size", str(presets.GROUP_SIZE),
                   "--max-drift", str(presets.MAX_DRIFT_MS), "--trim-fast", str(presets.FAST_THRESHOLD),
                   "--trim-to", str(presets.FAST_TARGET)], g_needs, label="Groups")
    # Step 4C, the scene edit (skill v21, docs/06; every show, owner 2026-10-30): the same commands, in the same order,
    # as adaptation-tool/scripts/run_episode.sh. It replaces fastfix (the scene edit trims fast lines itself).
    PL = cfg.scene_dir; LIB = W / "broll_library"
    add("library", [py, str(PL / "library.py"), str(W), str(LIB)], ["groups"], ai=True,
        label="Shot library (Claude labels every shot)")
    add("facemap", ["{fpy}", T("facemap.py"), "--video", assets["src_video"], "--library", str(LIB / "broll_library.json"),
                    "--lips", str(W / "lips_nets"), "--app", str(Path(__file__).resolve().parents[2])], ["library", "lip_gpu"], resource="gpu",
        ai=True, label="Face map (who is on screen, who talks)", duration=dur_src)
    add("scene_edit", fn="scene_edit", needs=["facemap"], resource="none", ai=True,
        label="Scene edit (Claude, B-roll for slow lines)")
    add("use_scene", fn="use_scene", needs=["scene_edit"], resource="none", label="Use the scene edit")
    ctx.update(scene_dir=str(W / "scene"), library_dir=str(LIB), scene_parallel=8)
    # the editor's stop: the episode opens as a timeline; render starts when the editor presses Render
    add("review", fn="review_gate", needs=["use_scene"] if dynamic else ["use_scene", "translate"], resource="none",
        label="Review & edit (editor)")
    if dynamic:   # the cards follow the FINAL edit list (after the editor), so they are made after review
        add("dynsrt", [py, T("dubstitch.py"), "dynsrt", "--work", str(W), "--words", str(dub_sub_orig)[:-4] + "_words.json",
                       "--out", dyn_srt] + (["--font", cfg.font] if cfg.font else []), ["review", "captions"],
            label="Dynamic subtitles (cards fill a 2- or 3-line strip)")
        dub_sub = dyn_srt
    add("render", [py, T("dubstitch.py"), "render", "--work", str(W), "--subs", prof.get("render_subs", "keep")],
        ["review"], label="Render clips", duration=dur_dub)
    add("verify", [py, T("dubstitch.py"), "verify", "--work", str(W)], ["render"], label="Verify sync")
    clean = ctx["name_clean"]
    # every show: the source ending is kept and stops before a closing fade to black; the opening is left out when the
    # lip scan shows a moving mouth in it (bookends reads W/lipsync.json). Then the transition plan of the scene edit:
    # a transition at every clip switch except the cut INTO a B-roll (scene_transitions.py), and the transitions.
    tplan = W / "transitions_plan.json"
    add("bookends", [py, T("dubstitch.py"), "bookends", "--work", str(W), "--out", str(W / "clean_noglow.mp4")],
        ["verify"], label="Opening + ending")
    add("trans_plan", [py, str(PL / "scene_transitions.py"), str(W), str(LIB / "broll_library.json")], ["bookends"],
        fn="trans_plan", label="Transition plan")
    add("transitions", [py, T("dubstitch.py"), "transitions", "--work", str(W), "--out", clean, "--plan", str(tplan)],
        ["trans_plan"], label="Transitions", duration=dur_dub)
    last = "transitions"
    font = ["--font", cfg.font] if cfg.font else []

    def style_args(lang):
        """The subtitle style of one language (show profile "sub_styles", set in the editor): box or strip, size,
        and for the box its distance from the bottom and its shade; unset = the pipeline's own defaults."""
        st = {**{"style": prof.get("sub_style", "box")}, **((prof.get("sub_styles") or {}).get(lang) or {})}
        out = ["--style", st["style"]]
        if st["style"] == "box":
            for k, flag in (("size", "--box-size"), ("bottom", "--box-bottom"), ("alpha", "--box-alpha")):
                if st.get(k) is not None:
                    out += [flag, str(st[k])]
        elif st.get("size") is not None:
            out += ["--size", str(st["size"])]
        return out
    add("subs_dub", [py, T("dubstitch.py"), "subs", "--work", str(W)] + style_args("dub") + ["--srt", dub_sub, "--out",
                     ctx["name_dub_subs"], "--max-lead", "0"] + font, [last] + (["dynsrt"] if dynamic else []), label=f"{D} subtitle video",
        duration=dur_dub)
    add("translate", [py, T("dubstitch.py"), "translate", "--work", str(W), "--srt", dub_sub, "--sl", dl, "--tl",
                      prof.get("translate_to", "en"), "--out", ctx["name_en_srt"]], ["dynsrt"] if dynamic else ["analyze"], resource="cpu",
        label="English translation", fn="translate")       # Google first; Claude when Google is blocked (runner)
    add("subs_en", [py, T("dubstitch.py"), "subs", "--work", str(W)] + style_args("en") + ["--srt", ctx["en_srt_burn"],
                    "--out", ctx["name_en_subs"], "--max-lead", "0"] + font, ["subs_dub", "translate"],
        label="English subtitle video", duration=dur_dub)
    ctx["name_scene_sheet"] = str(OUT / f"{code.lower()}_ep{N}_scene_edit.xlsx")
    add("scene_sheet", fn="scene_sheet", needs=["subs_en"], resource="none", label="Scene-edit sheet")
    # the final QC (owner, 2026-10-30): B-roll used <= 3 times, shots >= 2 s, story sync (Claude reads every shot), no
    # black frames, 25 fps, only the speaker talks -> a PASS / WARN / FAIL sheet; a FAIL is reported, it does not stop
    ctx["name_qc"] = str(OUT / f"{code.lower()}_ep{N}_qc.xlsx")
    add("qc", [py, str(PL / "qc_final.py"), "--work", str(W), "--library", str(LIB), "--videos", clean,
               ctx["name_dub_subs"], ctx["name_en_subs"], "--en-srt", ctx["en_srt_burn"], "--app",
               str(Path(__file__).resolve().parents[2]), "--out", ctx["name_qc"]], ["scene_sheet"], ai=True,
        label="Final QC (PASS / WARN / FAIL sheet)")
    add("check", [py, T("dubstitch.py"), "check", "--work", str(W)], ["qc"], label="Final check")
    add("notes", fn="run_notes", needs=["check"], resource="none", label="Run notes (run_notes.md)")
    return ctx, steps
