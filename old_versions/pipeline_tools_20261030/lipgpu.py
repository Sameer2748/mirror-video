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
