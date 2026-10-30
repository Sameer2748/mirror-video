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
