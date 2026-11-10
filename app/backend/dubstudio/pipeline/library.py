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
def _app_dir(script):
    """Folder on sys.path for `import dubstudio`: parents[2] (Mac), else /opt/app (CPU worker)."""
    script = Path(script).resolve()
    for folder in (script.parents[2], Path("/opt/app")):
        pkg = folder / "dubstudio"
        if (pkg / "ai.py").is_file() or (pkg / "__init__.py").is_file():
            return folder
    return script.parents[2]
sys.path.insert(0, str(_app_dir(__file__)))          # app/backend, or /opt/app on the CPU worker
from dubstudio import ai  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def video_seconds(path):
    """Seconds of pictures. Container duration follows the longest stream (often audio), so the last
    shot would be cut to 615 s on a file whose pictures stop at 390 s, and frame grabs return empty PNGs."""
    fmt = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    stream = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration",
                             "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    try:
        a = float(fmt) if fmt else 0.0
    except ValueError:
        a = 0.0
    try:
        b = float(stream) if stream and stream not in ("N/A", "n/a") else 0.0
    except ValueError:
        b = 0.0
    if a > 0 and b > 0:
        return min(a, b)
    return a or b


def grab_png(video, t, size=256):
    """One PNG at t seconds. Fast seek first; if empty (past the pictures / a GOP hole), accurate seek."""
    def run(accurate):
        cmd = ["ffmpeg", "-v", "error"]
        if accurate:
            cmd += ["-i", video, "-ss", f"{t:.3f}"]
        else:
            cmd += ["-ss", f"{t:.3f}", "-i", video]
        cmd += ["-frames:v", "1", "-vf", f"scale={size}:{size}", "-f", "image2pipe", "-vcodec", "png", "-"]
        return subprocess.run(cmd, capture_output=True).stdout or b""
    png = run(False)
    if not png.startswith(PNG_MAGIC):
        png = run(True)
    return png


def image_from_png(png, size=256):
    """A 256×256 RGB image. Empty or garbage PNG (seek past EOF) becomes black, not a crash."""
    from PIL import Image
    if png.startswith(PNG_MAGIC):
        try:
            return Image.open(io.BytesIO(png)).convert("RGB")
        except Exception:
            pass
    return Image.new("RGB", (size, size), "black")


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
_U = dict(calls=0, prompt_tokens=0, completion_tokens=0); _L = threading.Lock(); _CACHE_LOCK = threading.Lock(); _chat = ai.chat
def _count_chat(*a, **k):
    text, u = _chat(*a, **k)
    with _L:
        _U["calls"] += 1; _U["prompt_tokens"] += int((u or {}).get("prompt_tokens") or 0); _U["completion_tokens"] += int((u or {}).get("completion_tokens") or 0)
    return text, u
ai.chat = _count_chat
atexit.register(lambda: print("Claude usage:", _U, flush=True))

cfg = json.load(open(W / "config.json")); video = cfg["src_video"]; OP = cfg.get("opening_s", 0)
dur = video_seconds(video)
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
            png = grab_png(video, t)
            if not png.startswith(PNG_MAGIC):
                print(f"shot {k} at {t:.2f}s: empty frame (past the pictures); using black", flush=True)
            ims.append(image_from_png(png))
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
    with _CACHE_LOCK:
        if key in cache:
            return cache[key]
    content = [{"type": "text", "text": f"Cast of the episode:\n{cast_txt}\n\n{RULES}\n\nThe shots (3 frames each, left to right):"}]
    for k in batch:
        x, y = shots[k]
        content += [{"type": "text", "text": f"shot {k}: {fmt(x)}-{fmt(y)} ({y - x:.1f} s). Narration around it: \"{text_at(x, y)[:400]}\""},
                    {"type": "image_url", "image_url": {"url": b64(strip(k))}}]
    r = None
    for attempt in range(3):
        try:
            text, _ = ai.chat(content, max_tokens=4000)
            r = ai.extract_json(text, "shots")
            if r and len(r.get("shots", [])) >= len(batch):
                break
        except Exception:
            r = None; time.sleep(3)
    with _CACHE_LOCK:
        cache[key] = r
        json.dump(dict(cache), open(cache_p, "w"), indent=1, ensure_ascii=False)
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
def _ask_locations(part):
    q = ("Here are the place descriptions of the shots of one drama episode. Group them into LOCATIONS: one location = one "
         "background that looks the same on screen (e.g. 'parking garage' and 'parking garage beside the car' are one "
         "location; a car interior, a different room, a street are different locations). A description that mixes several "
         "places (a flashback, a montage: 'bedroom, then inside a dark car, then a city street') belongs to the location "
         "\"mixed\". Answer only JSON: {\"locations\": {\"<place description exactly as given>\": \"<short location name>\"}}\n\n"
         + "\n".join(f"- {p}" for p in part))
    text, _ = ai.chat(q, max_tokens=8000)
    return ai.extract_json(text, "locations")["locations"]


if "locations" not in cache:
    loc = {}
    for i in range(0, len(places), 30):
        part = places[i:i + 30]
        try:
            loc.update(_ask_locations(part))
        except ValueError:
            text, _ = ai.chat(
                "Your last reply was cut off. Reply again with the FULL JSON only.\n\n"
                "Answer only JSON: {\"locations\": {\"<place description exactly as given>\": \"<short location name>\"}}\n\n"
                + "\n".join(f"- {p}" for p in part),
                max_tokens=12000)
            loc.update(ai.extract_json(text, "locations")["locations"])
    cache["locations"] = {"locations": loc}
    json.dump(cache, open(cache_p, "w"), indent=1, ensure_ascii=False)
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
def _plain(o):
    """Claude sometimes puts raw control characters in a label; those break json.load on the face-map worker."""
    if isinstance(o, str):
        return "".join(c if ord(c) >= 32 or c in "\n\t" else " " for c in o)
    if isinstance(o, list):
        return [_plain(x) for x in o]
    if isinstance(o, dict):
        return {(_plain(k) if isinstance(k, str) else k): _plain(v) for k, v in o.items()}
    return o


json.dump(_plain(dict(cast=cast, first_shots=first_shots, shots=rows)), open(OUT / "broll_library.json", "w"), indent=1, ensure_ascii=False)

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
