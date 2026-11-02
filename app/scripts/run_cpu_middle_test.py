#!/usr/bin/env python3
"""TGS ep34 middle steps on the CPU worker, each from the Mac's inputs, then compared with the Mac cut.

Scratch folder only. The Mac work folder is read, never written.
A step that already finished (`.done_<name>`) is skipped, so a re-run continues.

    python app/scripts/run_cpu_middle_test.py
"""
import json, shutil, statistics, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from dubstudio import cloud                                     # noqa: E402
from dubstudio.config import cfg                                # noqa: E402
from dubstudio.db import DB                                     # noqa: E402

MAC = ROOT / "library/work/TGS_ep34"
DEST = ROOT / "library/work/_cpu_middle_test"
VIDEO = "/Users/manish/Downloads/TGS US_DE/US/TGS EPISODE 34.mp4"
AUDIO = "/Users/manish/Downloads/TGS US_DE/DE/Episode 34  Ehekrach.mp3"
PY = cfg.pipeline_python
SCR = str(cfg.scripts)
MODELS = str(cfg.models_dir)


def copy_inputs():
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "out").mkdir(exist_ok=True)
    for name in ("TGS_EP34_EN_sentences.srt", "TGS_EP34_DE_sentences.srt",
                 "TGS_EP34_EN.srt", "TGS_EP34_DE.srt", "decisions.json", "spec.py"):
        src = MAC / name
        if src.exists() and not (DEST / name).exists():
            shutil.copy(src, DEST / name)
    nets = DEST / "lips_nets"
    if not nets.exists():
        shutil.copytree(MAC / "lips_nets_mac", nets)


def run(db, show, ep, name, cmd, tries=6):
    mark = DEST / f".done_{name}"
    if mark.exists():
        print(f"skip {name} (already done)", flush=True)
        return json.loads(mark.read_text())
    t0 = time.time()
    last = None
    for attempt in range(1, tries + 1):
        try:
            res = cloud.run_command(db, show, ep, name, cmd, cfg, log=lambda m: print(m, flush=True),
                                    timeout=7200, kind="cpu")
            info = {"wall_s": round(time.time() - t0, 1), "worker_s": res.get("seconds"), "attempts": attempt}
            mark.write_text(json.dumps(info))
            print(f"DONE {name} wall {info['wall_s']}s worker {info['worker_s']}s", flush=True)
            return info
        except RuntimeError as ex:
            last = ex
            if "exit 3" in str(ex) and attempt < tries:
                print(f"{name} asked to resume ({attempt})", flush=True)
                continue
            print(f"FAIL {name}: {ex}", flush=True)
            (DEST / f".fail_{name}").write_text(str(ex)[-2000:])
            return None
    print(f"FAIL {name}: {last}", flush=True)
    return None


def sentences(path):
    data = json.loads(Path(path).read_text())
    return data["sentences"]


def cmp_sentences(side):
    mac_p, got_p = MAC / f"sentences_{side}.json", DEST / f"sentences_{side}.json"
    if not got_p.exists():
        return f"{side}: missing"
    a, b = sentences(mac_p), sentences(got_p)
    n = min(len(a), len(b))
    same_text = sum(a[i]["text"] == b[i]["text"] for i in range(n))
    deltas = [abs(a[i]["start_ms"] - b[i]["start_ms"]) for i in range(n)]
    med = round(statistics.median(deltas), 1) if deltas else None
    within = sum(d <= 80 for d in deltas)
    return (f"{side}: count mac {len(a)} cloud {len(b)}, same text {same_text}/{n}, "
            f"start within 80 ms {within}/{n}, median start delta {med} ms")


def cmp_auto():
    if not (DEST / "prep.json").exists():
        return "match: missing"
    mac = json.loads((MAC / "prep.json").read_text())["auto"]
    got = json.loads((DEST / "prep.json").read_text())["auto"]
    keys = set(mac) | set(got)
    same = sum(mac.get(k) == got.get(k) for k in keys)
    return f"match: {same}/{len(keys)} dub rows pick the same source row"


def cmp_manifest():
    if not (DEST / "manifest.json").exists():
        return "edit list: missing"
    mac = json.loads((MAC / "manifest.json").read_text())
    got = json.loads((DEST / "manifest.json").read_text())
    return (f"edit list: segments mac {len(mac['segments'])} cloud {len(got['segments'])}, "
            f"total_ms mac {mac['total_ms']} cloud {got['total_ms']}")


def cmp_groups():
    if not (DEST / "manifest_groups.json").exists():
        return "groups: missing"
    mac = json.loads((MAC / "manifest_groups.auto.json").read_text())
    got = json.loads((DEST / "manifest_groups.json").read_text())
    # The Mac file manifest_groups.auto.json is the groups step, before the scene edit replaced manifest_groups.json.
    return (f"groups: pieces mac {len(mac['segments'])} cloud {len(got['segments'])}, "
            f"total_ms mac {mac['total_ms']} cloud {got['total_ms']}")


def cmp_lipsync():
    if not (DEST / "lipsync.json").exists():
        return "lipsync: missing"
    mac = json.loads((MAC / "lipsync.json").read_text())["rows"]
    got = json.loads((DEST / "lipsync.json").read_text())["rows"]
    keys = set(mac) | set(got)
    same = sum(mac.get(k, {}).get("window") == got.get(k, {}).get("window") for k in keys)
    return f"lipsync: rows mac {len(mac)} cloud {len(got)}, same window {same}/{len(keys)}"


def cmp_mesh():
    mac_d, got_d = MAC / "lips_mesh", DEST / "lips_mesh"
    if not got_d.exists():
        return "lip mesh: missing"
    files = sorted(p.name for p in mac_d.glob("mesh_*.json"))
    got = {p.name for p in got_d.glob("mesh_*.json")}
    both = [n for n in files if n in got]
    deltas = []
    for name in both:
        a = json.loads((mac_d / name).read_text())["aperture"]
        b = json.loads((got_d / name).read_text())["aperture"]
        n = min(len(a), len(b))
        if n:
            deltas.append(sum(abs(a[i] - b[i]) for i in range(n)) / n)
    med = round(statistics.median(deltas), 4) if deltas else None
    return f"lip mesh: files mac {len(files)} cloud {len(got)} shared {len(both)}, median mean-abs aperture {med}"


def cmp_sharp():
    if not (DEST / "sharpness.json").exists():
        return "sharpness: missing"
    a = json.loads((MAC / "sharpness.json").read_text())["values"]
    b = json.loads((DEST / "sharpness.json").read_text())["values"]
    n = min(len(a), len(b))
    med = round(statistics.median(abs(a[i] - b[i]) for i in range(n)), 2) if n else None
    return f"sharpness: samples mac {len(a)} cloud {len(b)}, median abs delta {med}"


def cmp_proxy():
    got = DEST / "proxy_540.mp4"
    if not got.exists():
        return "preview: missing"
    def probe(path):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height,codec_type",
                            "-of", "json", str(path)], capture_output=True, text=True)
        return json.loads(r.stdout or "{}")
    a, b = probe(MAC / "proxy_540.mp4"), probe(got)
    return f"preview: mac {a.get('format', {}).get('duration')} cloud {b.get('format', {}).get('duration')} s"


def cmp_text(name):
    mac, got = MAC / name, DEST / name
    if not got.exists():
        return f"{name}: missing"
    at, bt = mac.read_text(encoding="utf-8"), got.read_text(encoding="utf-8")
    return f"{name}: mac {at.count(chr(10))} lines, cloud {bt.count(chr(10))} lines, same {at == bt}"


def report():
    lines = [cmp_proxy(), cmp_sharp(), cmp_sentences("src"), cmp_sentences("dub"), cmp_auto(),
             cmp_manifest(), cmp_lipsync(), cmp_groups(), cmp_mesh(), cmp_text("merged.txt")]
    text = "\n".join(lines)
    (DEST / "compare.txt").write_text(text + "\n")
    print("COMPARE\n" + text, flush=True)


def main():
    for path in (VIDEO, AUDIO):
        if not Path(path).is_file():
            sys.exit(f"missing input {path}")
    copy_inputs()
    db = DB(cfg.library / "dubstudio.db")
    show = db.one("SELECT * FROM shows WHERE code=?", ("TGS",))
    ep = dict(db.one("SELECT * FROM episodes WHERE number=34 AND show_id=?", (show["id"],)))
    ep.update(work_dir=str(DEST), output_dir=str(DEST / "out"))
    prof = show["profile"] or {}
    sl, dl = prof.get("src_lang", "en"), prof.get("dub_lang", "de")
    labels = prof.get("labels") or "DE,US"
    print(f"CPU endpoint {cloud.endpoint('cpu')} langs {sl}/{dl}", flush=True)

    src_srt = str(DEST / "TGS_EP34_EN_sentences.srt")
    dub_srt = str(DEST / "TGS_EP34_DE_sentences.srt")
    proxy = str(DEST / "proxy_540.mp4")
    run(db, show, ep, "proxy", ["ffmpeg", "-v", "error", "-y", "-i", VIDEO, "-map", "0:v:0", "-map", "0:a:0?",
                                "-vf", "scale=-2:'min(540,ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
                                "-pix_fmt", "yuv420p", "-force_key_frames", "expr:gte(t,n_forced*0.5)",
                                "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart", proxy])
    run(db, show, ep, "dub_preview", ["ffmpeg", "-v", "error", "-y", "-i", AUDIO, "-map", "0:a:0", "-c:a", "aac",
                                      "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                                      str(DEST / "dub_preview.m4a")])
    if (DEST / "proxy_540.mp4").exists():
        tool = str(ROOT / "backend/dubstudio/sharpness_tool.py")
        run(db, show, ep, "sharpness", [PY, tool, "--video", proxy, "--out", str(DEST / "sharpness.json")])
    run(db, show, ep, "words_src", [PY, f"{SCR}/dubstitch.py", "words", "--work", str(DEST), "--side", "src",
                                   "--srt", src_srt, "--media", VIDEO, "--lang", sl, "--models", MODELS, "--clauses", "0"])
    run(db, show, ep, "words_dub", [PY, f"{SCR}/dubstitch.py", "words", "--work", str(DEST), "--side", "dub",
                                   "--srt", dub_srt, "--media", AUDIO, "--lang", dl, "--models", MODELS, "--clauses", "0"])
    if (DEST / "sentences_src.json").exists() and (DEST / "sentences_dub.json").exists():
        run(db, show, ep, "analyze", [PY, f"{SCR}/dubstitch.py", "analyze", "--work", str(DEST),
                                      "--dub-srt", dub_srt, "--dub-audio", AUDIO, "--src-srt", src_srt,
                                      "--src-video", VIDEO, "--models", MODELS, "--clauses", "0"])
    if (DEST / "prep.json").exists():
        run(db, show, ep, "merged", ["sh", "-c", "{PY} {SCRIPTS}/merged.py {WORK} 95 > {WORK}/merged.txt"])
        run(db, show, ep, "sheet", [PY, f"{SCR}/dubstitch.py", "sheet", "--work", str(DEST),
                                    "--out", str(DEST / "out/mapping.xlsx"), "--labels", labels])
    if (DEST / "rows.json").exists():
        run(db, show, ep, "manifest", [PY, f"{SCR}/dubstitch.py", "manifest", "--work", str(DEST)])
    if (DEST / "lips_nets").exists():
        run(db, show, ep, "lip_mesh", [PY, f"{SCR}/lipmesh.py", "--video", VIDEO, "--out-dir", str(DEST / "lips_mesh"),
                                       "--ranges", str(DEST / "lips_nets/ranges.json")])
    if (DEST / "lips_mesh").exists() and (DEST / "manifest.json").exists():
        run(db, show, ep, "lip_merge", [PY, f"{SCR}/lipmerge.py", "--nets", str(DEST / "lips_nets"),
                                        "--mesh", str(DEST / "lips_mesh"), "--out", str(DEST / "lips_range")])
        run(db, show, ep, "lipsync", [PY, f"{SCR}/dubstitch.py", "lipsync", "--work", str(DEST)])
        run(db, show, ep, "groups", [PY, f"{SCR}/dubstitch.py", "groups", "--work", str(DEST), "--size", "7",
                                     "--max-drift", "500", "--trim-fast", "1.25", "--trim-to", "1.2"])
    if (DEST / "spec.py").exists() and (DEST / "prep.json").exists():
        run(db, show, ep, "mkdec", [PY, f"{SCR}/mkdec.py", str(DEST), str(DEST / "spec.py")])
    run(db, show, ep, "translate", [PY, f"{SCR}/dubstitch.py", "translate", "--work", str(DEST),
                                    "--srt", str(DEST / "TGS_EP34_DE.srt"), "--sl", dl, "--tl", "en",
                                    "--out", str(DEST / "out/english.srt")])
    report()
    print("MIDDLE DONE", flush=True)


if __name__ == "__main__":
    main()
