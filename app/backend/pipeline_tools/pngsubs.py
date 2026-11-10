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
