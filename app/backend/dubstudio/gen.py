"""Generate a replacement clip with AI (owner, 2026-10-26).

A clip on the timeline that plays too slow (e.g. 6 s of slot filled with 2.4 s of source at x0.4) can be re-made as a
new video of the slot's length that shows the same thing at natural speed. For one clip (its *slot* on the dub
timeline) the editor gets a chat:

1. frames: 9 stills from the clip's footage (start … middle … end) in W/gen/<thread>/frames/ (+ full-size start
   and end frames for the video model);
2. analysis: Gemini watches the clip's footage (and compares its first and last frame) and writes down exactly what
   is in it (people, clothes, place, camera incl. push-in/pan, the motion from start to end, light) — nothing more;
3. prompt: Claude writes the video prompt from the analysis, the frames, the dub line and the clip's neighbours
   (prefilled; the editor can change it and chat with Claude about it, with images attached);
4. generate: the prompt + the clip's first (and last) frame go to a video model — Veo through the madeye gateway
   (the AI key, LiteLLM `/v1/videos`), or Kling / Veo on fal.ai (FAL_KEY) —; the result is brought back to the
   source's shape and to the slot's length, put in the Project bin, and the editor drops it on V2 over the slot.

Every chat (thread), message and generated video is kept per episode and slot, so the history of a clip stays.
Keys live in app/.env only (AI_KEY, FAL_KEY); never shown, printed or logged."""
import base64, json, os, re, shutil, subprocess, threading, time, urllib.error, urllib.request
from pathlib import Path

from . import ai, imports

ROOT = Path(__file__).resolve().parents[2]
N_FRAMES = 9
ANALYSIS_MODELS = ["gemini-3.1-pro-preview", "gemini-3.5-flash"]     # the first one the key has watches the clip

# The video models. provider "madeye" = the AI key's gateway (shown when the key has the model); "fal" = fal.ai
# (shown when a FAL_KEY is saved). durations: the lengths the model can make; end_frame: it can also END on a frame.
MODELS = [
    {"id": "veo-3.1-generate-001", "label": "Veo 3.1 · best (start + end frame)", "provider": "madeye", "family": "veo",
     "durations": [4, 6, 8], "end_frame": True, "refs": 3},
    {"id": "veo-3.1-fast-generate-001", "label": "Veo 3.1 Fast (start + end frame)", "provider": "madeye", "family": "veo",
     "durations": [4, 6, 8], "end_frame": True, "refs": 3},
    {"id": "veo-3.0-generate-001", "label": "Veo 3.0 (start frame)", "provider": "madeye", "family": "veo",
     "durations": [4, 6, 8], "end_frame": False},
    {"id": "fal-ai/kling-video/v2.1/pro/image-to-video", "label": "Kling 2.1 Pro · fal.ai (start + end frame)",
     "provider": "fal", "family": "kling", "durations": [5, 10], "end_frame": True},
    {"id": "fal-ai/kling-video/v2.1/standard/image-to-video", "label": "Kling 2.1 Standard · fal.ai (start frame)",
     "provider": "fal", "family": "kling", "durations": [5, 10], "end_frame": False},
    {"id": "fal-ai/veo3/fast/image-to-video", "label": "Veo 3 Fast · fal.ai (start frame, 8 s)", "provider": "fal",
     "family": "veo", "durations": [8], "end_frame": False},
]
RETIME_OK = (0.8, 1.25)        # a made video may be slowed / sped up this much to fit the slot (not visible)
# Veo's reference images ("ingredients", up to 3 people / places to keep): tested 2026-10-26 through the gateway --
# they cannot be combined with a first or last frame, and that mode only makes 8 s videos.
REF_SECONDS = 8
MIN_SHOT_MS = 250              # cuts closer than this to an edge or to each other are not shots of their own

SYSTEM = """You are the video-generation assistant inside Dub Studio. Dub Studio rebuilds an episode for a new dubbed
voice: every line of the dub is covered by footage cut from the original episode. Sometimes the original footage for
a line is shorter than the dubbed line, so the editor's timeline plays it in slow motion (e.g. x0.40), which looks
wrong. Your job, for the ONE clip the editor selected: help make a NEW video, exactly as long as the slot, that shows
the SAME moment of the SAME shot at natural speed, so it can be laid over the slow clip and nobody notices.

What you are given (in the first message of every chat): the clip's facts (show, episode, where it is, slot length,
footage length, its speed, the source frame size), the dub line spoken over it and the lines before and after, the
source line, whether a mouth should move (a lip-sync row), the flags the editor's tool raised, an analysis of the
footage written by a video model, and frames of the footage (start, middle, end). Trust the frames over the
analysis when they disagree.

THE SHOT IS FIXED. Every prompt must keep, exactly as in the frames:
- the same people: face, age, hair, make-up, clothes, accessories, body position, where each one is in the frame;
- the same place and background, the same objects in the same places;
- the same camera: framing, angle, lens / depth of field, and the same camera movement (a slow push-in stays a slow
  push-in; a still camera stays still);
- the same light, colours and time of day; photorealistic, like the frames;
- it starts on the first frame and, when the end frame is used, arrives at the last frame.

SHOTS. Most clips are one continuous shot: never add a cut to them. Some clips contain camera cuts (the facts list the
shots). Then, by default, the tool keeps the short shots exactly as filmed and generates only the main shot from its
own first and last frame: write the prompt for that one shot only, as one continuous shot. Only when the editor uses
reference images (no start frame, one 8 s video) write the whole sequence: each shot in order, "hard cut to" between
them, with about how long each lasts, and the same people and places as in the frames.

NOTHING NEW. Never add or invent: people, faces, animals, objects, text, logos, subtitles, captions, cuts, scene or
angle changes, zooms or pans that are not in the footage, flashes, effects, music videos, slow motion, speed ramps,
morphing, face or clothes changes, extra limbs or fingers, glitches. If the editor asks for something that breaks the
shot (a new person, another place, a cut), say plainly that it would not match the episode and offer the closest
change that does.

MOTION. Only the movement that is in the footage, at natural speed, spread evenly over the whole duration: small and
calm (breathing, a blink, a slight head turn, a hand settling). If the footage shows nearly no movement, keep it
nearly still: do not invent action to fill time. If the clip is a lip-sync row or the analysis says someone talks,
the mouth moves naturally as if speaking, without exaggeration. Otherwise it is a SILENT moment: video models like to
make people talk, so say it plainly in the paragraph ("a silent moment, nobody speaks, lips stay closed and still")
and put "talking, lip movement, open mouth" in the Avoid line. No sound is used.

WRITING THE PROMPT (for image-to-video models such as Veo and Kling; the first frame is given to the model as an image):
- One paragraph, present tense, under 110 words: shot and camera first, then the people as they look, then what
  moves over the duration (beginning → end), then place and light. Concrete visual words; no story, no emotions that
  are not visible, no names of characters.
- Then a last line starting "Avoid:" with a short comma list of what must not appear (the tool sends it to the model
  as its negative prompt).
- Put the prompt (paragraph + Avoid line) in ONE fenced block marked ```prompt. Outside the block write at most two
  short lines (what you changed, or a tip). If the editor only asks a question, answer briefly without a block.
- Keep everything the editor already asked for in earlier messages unless they take it back."""


# ---------------- settings ----------------
def fal_key():
    if os.environ.get("FAL_KEY"):
        return os.environ["FAL_KEY"]
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?FAL_KEY\s*=\s*(.*)\s*$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return ""


_models_cache = {"at": 0.0, "ids": []}


def gateway_models():
    """The models the AI key can use (cached 10 min; [] when the gateway cannot be reached)."""
    if time.time() - _models_cache["at"] > 600 and ai.available():
        try:
            _models_cache.update(at=time.time(), ids=ai.models())
        except Exception:
            _models_cache.update(at=time.time() - 540, ids=_models_cache["ids"])   # retry in a minute
    return _models_cache["ids"]


def available_models():
    have = set(gateway_models()); fal = bool(fal_key())
    return [m for m in MODELS if (m["provider"] == "madeye" and m["id"] in have) or (m["provider"] == "fal" and fal)]


def analysis_model():
    have = gateway_models()
    return next((m for m in ANALYSIS_MODELS if m in have), ANALYSIS_MODELS[-1])


def status():
    ms = available_models()
    return {"chat": ai.available(), "video": bool(ms), "models": ms, "system_prompt": SYSTEM, "fal": bool(fal_key())}


# ---------------- files ----------------
def folder(work, tid):
    return Path(work) / "gen" / str(tid)


def _ff(args, timeout=300):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError("ffmpeg: " + r.stderr[-300:])


def probe_size(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate",
                        "-of", "json", str(video)], capture_output=True, text=True)
    s = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
    n, dd = (s.get("r_frame_rate") or "25/1").split("/")
    return int(s.get("width") or 0), int(s.get("height") or 0), round(float(n) / float(dd or 1), 3)


def detect_shots(video, s0, s1):
    """The camera cuts inside s0..s1 (ms) -> shots [[a, b], ...] in ms (one shot when there is no cut)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{s0 / 1000:.3f}", "-t", f"{(s1 - s0) / 1000:.3f}", "-i", str(video),
                        "-an", "-vf", "fps=25,scale=192:-2,select='gt(scene,0.28)',showinfo", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=300).stderr
    cuts = []
    for x in re.findall(r"pts_time:([0-9.]+)", r):
        t = s0 + float(x) * 1000
        if t - s0 >= MIN_SHOT_MS and s1 - t >= MIN_SHOT_MS and (not cuts or t - cuts[-1] >= MIN_SHOT_MS):
            cuts.append(round(t, 1))
    b = [s0] + cuts + [s1]
    return [[b[k], b[k + 1]] for k in range(len(b) - 1)]


def main_shot(shots):
    """The shot that is generated when the others are kept as filmed: the longest."""
    ln = lambda x: (x["s1"] - x["s0"]) if isinstance(x, dict) else (x[1] - x[0])
    return max(range(len(shots)), key=lambda k: ln(shots[k])) if shots else 0


def make_frames(work, tid, video, s0, s1):
    """9 stills from s0..s1 (small, for the AI and the panel) + the first and last frame at full size (for the video
    model) + the clip's shots (camera cuts) with each shot's first and last frame.
    -> {"frames": [{"file", "t_ms", "shot"}], "start", "end", "size": [w, h], "fps", "shots": [{"s0", "s1", "start", "end"}]}"""
    d = folder(work, tid) / "frames"; d.mkdir(parents=True, exist_ok=True)
    out = []
    last = max(s0, s1 - 40)                                   # the last frame inside the clip
    for k in range(N_FRAMES):
        t = s0 + (last - s0) * k / (N_FRAMES - 1)
        f = d / f"f{k:02d}.jpg"
        _ff(["-ss", f"{t / 1000:.3f}", "-i", str(video), "-frames:v", "1", "-vf", "scale='min(768,iw)':-2", "-q:v", "3", str(f)])
        out.append({"file": f"frames/{f.name}", "t_ms": round(t)})
    for name, t in (("start", s0), ("end", last)):
        _ff(["-ss", f"{t / 1000:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(d / f"{name}.jpg")])
    w, h, fps = probe_size(video)
    shots = []
    for k, (a, b2) in enumerate(detect_shots(video, s0, s1)):
        e = max(a, b2 - 40)
        for name, t in (("start", a), ("end", e)):
            _ff(["-ss", f"{t / 1000:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(d / f"shot{k}_{name}.jpg")])
        shots.append({"s0": round(a, 1), "s1": round(b2, 1), "start": f"frames/shot{k}_start.jpg", "end": f"frames/shot{k}_end.jpg"})
    for f in out:
        f["shot"] = next((k for k, x in enumerate(shots) if x["s0"] - 1 <= f["t_ms"] < x["s1"] + 1), len(shots) - 1)
    return {"frames": out, "start": "frames/start.jpg", "end": "frames/end.jpg", "size": [w, h], "fps": fps, "shots": shots}


def _data_uri(path, mime=None, max_px=None):
    p = Path(path)
    if max_px:                                                 # shrink big uploads before sending them to the AI
        small = p.with_name(p.stem + f"_{max_px}.jpg")
        if not small.exists():
            _ff(["-i", str(p), "-vf", f"scale='min({max_px},iw)':-2", "-q:v", "3", str(small)])
        p = small; mime = "image/jpeg"
    mime = mime or ("image/png" if p.suffix.lower() == ".png" else "image/jpeg")
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def _b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


# ---------------- analysis + first prompt ----------------
ANALYSE = """Watch this clip from an episode (it is {src_s:.2f} s of original footage). Write down EXACTLY what is
visible, nothing invented. Answer as JSON with these keys:
"people": [{{"who": short label, "looks": face/hair/age, "clothes": ..., "position": where in the frame}}],
"setting": the place and background, "camera": framing, angle, lens feel and any camera movement,
"light": light and colour, "motion": what moves from start to end, in order (start / middle / end),
"speaking": who is talking (mouth moving) or nobody, "avoid": things a generator might wrongly add here.
Camera movement is easy to miss: the two images after the video are the FIRST and the LAST frame. Compare them:
if the people or objects get bigger/smaller or shift in the frame, the camera pushes in / pulls out / pans / tilts —
say which, which way and how much (e.g. "slow push-in, the woman grows from about 1/3 to 1/2 of the frame height").
Only JSON."""


GATEWAY_DOWN = ("The AI gateway (madeye) is not reachable from this Mac — turn the VPN on, then press Try again.")


def gateway_ok(timeout=10):
    """A quick check before the slow calls: a dropped VPN must say so in seconds, not hang for minutes."""
    s = ai.settings()
    if not ai.available():
        raise RuntimeError("The AI gateway is not set up (Settings → AI).")
    r = urllib.request.Request(s["AI_BASE"].rstrip("/") + "/v1/models", headers={"Authorization": "Bearer " + s["AI_KEY"]})
    try:
        urllib.request.urlopen(r, timeout=timeout).read()
    except urllib.error.HTTPError:
        pass                                         # it answered: reachable
    except Exception:
        raise RuntimeError(GATEWAY_DOWN) from None


def ask(content, model=None, max_tokens=4000, timeout=150, tries=2, system=None):
    """One gateway call with a short wait and few retries (ai.chat waits up to 5 min x 5 tries). -> text"""
    s = ai.settings(); model = model or s["AI_MODEL"]
    msgs = ([{"role": "system", "content": system}] if system else []) + (
        content if isinstance(content, list) and content and isinstance(content[0], dict) and "role" in content[0]
        else [{"role": "user", "content": content}])
    body = dict(model=model, max_tokens=max_tokens, metadata={"user_email": s.get("AI_EMAIL", "")}, messages=msgs)
    last = None
    for a in range(tries):
        try:
            d = ai._req("/v1/chat/completions", body, timeout=timeout)
            return d["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as e:
            last = RuntimeError(f"{model}: AI gateway HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
            if e.code not in (429, 500, 502, 503, 504, 529):
                break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = RuntimeError(f"{model}: no answer from the AI gateway ({str(e)[:80]}); is the VPN on?")
        time.sleep(3 * (a + 1))
    raise last


ANALYSE_FRAMES = """These are {n} frames of one clip from an episode, in order from its first to its last frame
({src_s:.2f} s of original footage). Write down EXACTLY what is visible, nothing invented. Compare the first and the
last frame for camera movement (push-in / pull-out / pan / tilt: which way, how much). Answer as JSON with the keys
"people" [{{"who", "looks", "clothes", "position"}}], "setting", "camera", "light", "motion" (start / middle / end),
"speaking" (whose mouth moves between the frames, or nobody), "avoid". Only JSON."""


def _as_json(text, by):
    try:
        v = ai.extract_json(text)
        if isinstance(v, dict):
            v = {"_by": by, **v}
        return json.dumps(v, ensure_ascii=False, indent=1)
    except ValueError:
        return f"(by {by})\n" + text.strip()


def analyse(work, tid, video, s0, s1, frames, log=None):
    """Gemini watches the clip (video + first/last frame); if that fails, Claude reads the clip's 9 frames instead.
    The gateway is checked first: no VPN -> a clear error in seconds."""
    gateway_ok()
    d = folder(work, tid); clip = d / "clip.mp4"
    _ff(["-ss", f"{s0 / 1000:.3f}", "-i", str(video), "-t", f"{max(s1 - s0, 200) / 1000:.3f}", "-an",
         "-vf", "scale='min(640,iw)':-2", "-c:v", "libx264", "-crf", "26", "-preset", "veryfast", str(clip)])
    fr = frames.get("frames") or []
    model = analysis_model()
    content = [{"type": "text", "text": ANALYSE.format(src_s=(s1 - s0) / 1000)},
               {"type": "file", "file": {"file_data": _data_uri(clip, "video/mp4")}}]
    for f in (fr[0], fr[-1]) if len(fr) >= 2 else ():                    # first + last frame: camera movement
        content.append({"type": "image_url", "image_url": {"url": _data_uri(d / f["file"])}})
    try:
        return _as_json(ask(content, model=model, max_tokens=4000, timeout=150), f"{model} (watched the clip)")
    except RuntimeError as ex:
        if GATEWAY_DOWN in str(ex) or "VPN" in str(ex):
            gateway_ok()                             # still reachable? if not, say so
        if log:
            log(f"{model} could not watch the clip ({ex}); Claude reads its frames instead")
    parts = [{"type": "text", "text": ANALYSE_FRAMES.format(n=len(fr), src_s=(s1 - s0) / 1000)}]
    parts += [{"type": "image_url", "image_url": {"url": _data_uri(d / f["file"])}} for f in fr]
    m2 = ai.settings()["AI_MODEL"]
    return _as_json(ask(parts, model=m2, max_tokens=3000, timeout=150), f"{m2} (read {len(fr)} frames)")


def _context(thread):
    """Everything about the clip for Claude (the first user turn of every chat), with its start/middle/end frames."""
    c = thread["clip"]; slot = (c["t1"] - c["t0"]) / 1000; src = (c["s1"] - c["s0"]) / 1000
    fr = (thread.get("frames") or {}); frs = fr.get("frames") or []
    pick = [frs[0], frs[len(frs) // 2], frs[-1]] if len(frs) >= 3 else frs
    w, h = (fr.get("size") or [0, 0])[:2]
    ms = available_models()
    lines = [
        f"Show {c.get('show') or '?'}, episode {c.get('episode') or '?'} ({c.get('labels') or 'dub vs source'}).",
        f"Clip {c.get('label') or '?'} on the timeline at {c['t0'] / 1000:.2f}-{c['t1'] / 1000:.2f} s: slot {slot:.2f} s.",
        f"Its footage: {src:.2f} s of the source ({c['s0'] / 1000:.2f}-{c['s1'] / 1000:.2f} s"
        + (", an imported clip" if c.get("media") else "") + f"), so today it plays at x{src / max(slot, 0.01):.2f}.",
        f"The new video: {slot:.2f} s, natural speed, frame {w}x{h}" + (f" ({'square' if w == h else 'landscape' if w > h else 'portrait'})" if w else "") + ".",
        f"Dub line spoken over it: {c.get('dub_text') or '-'}",
        f"Line before: {c.get('prev_text') or '-'}  |  line after: {c.get('next_text') or '-'}",
        f"Source line (original language): {c.get('src_text') or '-'}",
        "Lip-sync row: " + ("yes — a mouth on screen speaks this line" if c.get("lip") else "no"),
        "Flags on this clip: " + ("; ".join(c.get("flags") or []) or "none"),
        "Video models available: " + (", ".join(f"{m['label']} ({'/'.join(map(str, m['durations']))} s)" for m in ms) or "none yet")
        + ". The tool fits the made video to the slot (nearest length, gently retimed).",
    ]
    shots = fr.get("shots") or []
    if len(shots) > 1:
        mk = main_shot(shots); keep_s = sum(x["s1"] - x["s0"] for k, x in enumerate(shots) if k != mk) / 1000
        lines += [f"The footage has {len(shots)} SHOTS (camera cuts): " + "; ".join(
                      f"shot {k + 1} {(x['s0'] - c['s0']) / 1000:.2f}-{(x['s1'] - c['s0']) / 1000:.2f} s ({(x['s1'] - x['s0']) / 1000:.2f} s)"
                      for k, x in enumerate(shots)) + ".",
                  f"Default way to make it ('keep the short shots'): the other shots play as filmed ({keep_s:.2f} s) and only "
                  f"SHOT {mk + 1} is generated, from its own first and last frame, {max(slot - keep_s, 0):.2f} s long. So by "
                  f"default write the prompt for SHOT {mk + 1} ONLY (one continuous shot, no cut).",
                  "If the editor chooses 'reference images' (Veo 3.1: up to 3 images of the people / places, 8 s, no start "
                  "frame), write ONE prompt for the whole sequence: each shot in order with 'hard cut to' between them and "
                  "about how long each lasts."]
    else:
        lines += ["The footage is one continuous shot (no camera cut)."]
    lines += [
        "", "Analysis of the footage (by a video model):", thread.get("analysis") or "(not ready)", "",
        "Frames below: start, middle and end of the footage.",
    ]
    out = [{"type": "text", "text": "\n".join(lines)}]
    for f in pick:
        out.append({"type": "image_url", "image_url": {"url": _data_uri(Path(thread["_dir"]) / f["file"])}})
    if len(shots) > 1:                               # the generated shot's own first and last frame
        x = shots[main_shot(shots)]
        out.append({"type": "text", "text": f"First and last frame of SHOT {main_shot(shots) + 1} (the one that is generated):"})
        for f in (x["start"], x["end"]):
            out.append({"type": "image_url", "image_url": {"url": _data_uri(Path(thread["_dir"]) / f, max_px=768)}})
    return out


def _history(thread, messages):
    """OpenAI-style messages: context first, then the chat as it went (user images included, shrunk)."""
    msgs = [{"role": "user", "content": _context(thread)},
            {"role": "assistant", "content": "Understood. I have the clip, its frames, its context and the analysis."}]
    for m in messages:
        if m["role"] not in ("user", "assistant"):
            continue
        if m["role"] == "user" and m.get("images"):
            parts = [{"type": "text", "text": m.get("text") or "(see the images)"}]
            for f in m["images"]:
                parts.append({"type": "image_url", "image_url": {"url": _data_uri(Path(thread["_dir"]) / f, max_px=1024)}})
            msgs.append({"role": "user", "content": parts})
        else:
            msgs.append({"role": m["role"], "content": m.get("text") or ""})
    return msgs


def chat_reply(thread, messages, model=None):
    """Claude's answer to the chat so far -> (text, prompt or None)."""
    gateway_ok()
    text = ask(_history(thread, messages), model=model, max_tokens=2000, timeout=180, tries=2, system=SYSTEM)
    return text, extract_prompt(text)


def extract_prompt(text):
    m = re.search(r"```prompt\s*\n(.*?)```", text or "", re.S)
    return m.group(1).strip() if m else None


def split_avoid(prompt):
    """'… paragraph …\\nAvoid: a, b, c' -> (paragraph, 'a, b, c'): the Avoid line goes to the model's negative prompt."""
    m = re.search(r"(?im)^\s*avoid\s*:\s*(.+)$", prompt or "")
    if not m:
        return (prompt or "").strip(), ""
    return (prompt[:m.start()] + prompt[m.end():]).strip(), m.group(1).strip().rstrip(".")


FIRST_ASK = ("Write the video prompt for this clip now: the new {slot:.2f} s video, natural speed, exactly this "
             "moment and nothing more.")
BASE_AVOID = ("new people, extra characters, text, subtitles, captions, logo, watermark, scene cut, camera change, "
              "zoom jump, slow motion, speed ramp, morphing, face change, distorted hands, glitches, flicker")


# ---------------- length + shape ----------------
def model_info(model_id):
    return next((m for m in MODELS if m["id"] == model_id), {"id": model_id, "provider": "fal", "family": "other",
                                                              "durations": [5], "end_frame": False})


def plan_length(model_id, slot_s):
    """-> (seconds to make, how to fit it to the slot): the nearest length; 'retime' when within RETIME_OK (the end
    frame stays the end), else 'cut' (made longer than the slot: its start is kept) or 'stretch' (too short)."""
    ds = model_info(model_id)["durations"]
    gen_s = min(ds, key=lambda d: abs(d - slot_s))
    f = slot_s / gen_s
    if RETIME_OK[0] <= f <= RETIME_OK[1]:
        return gen_s, "retime"
    longer = [d for d in ds if d >= slot_s]
    return (min(longer), "cut") if longer else (max(ds), "stretch")


def pad_frame(src, dst, w, h):
    """A frame for Veo (16:9 or 9:16 only): the frame whole in the middle, a blurred copy of it filling the sides."""
    W, H = (1280, 720) if w >= h else (720, 1280)
    _ff(["-i", str(src), "-filter_complex",
         f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:5[bg];"
         f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2", "-q:v", "2", str(dst)])
    return W, H


def finish(raw, out, slot_s, gen_s, how, size, padded, fps=None):
    """The made video -> the source's shape (padding cropped off), the source size (at most 1080 high), the slot's
    length (retimed / cut / stretched), no sound."""
    w, h = size; vf = []
    if padded and w and h:
        vf.append(f"crop='min(iw,ih*{w}/{h})':'min(ih,iw*{h}/{w})'")        # the middle, in the source's shape
    if w and h:
        tw, th = (w, h) if h <= 1080 else (round(w * 1080 / h / 2) * 2, 1080)
        vf.append(f"scale={tw}:{th}")
    if how in ("retime", "stretch"):
        vf.append(f"setpts={slot_s / gen_s:.6f}*PTS")
    if fps:
        vf.append(f"fps={fps},setsar=1")
    _ff(["-i", str(raw), "-vf", ",".join(vf) or "null", "-t", f"{slot_s:.3f}", "-an", "-c:v", "libx264", "-crf", "18",
         "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])


# ---------------- providers ----------------
def _fal(method, url, body=None, timeout=60):
    key = fal_key()
    if not key:
        raise RuntimeError("no fal.ai key: add it in Settings → Video generation")
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Authorization": "Key " + key, "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(r, timeout=timeout))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"fal.ai HTTP {e.code}: {e.read()[:400].decode(errors='replace')}") from None


def _madeye(method, path, body=None, raw=False, timeout=120):
    """The madeye gateway's video API (LiteLLM /v1/videos); it wants the user's email with every call."""
    s = ai.settings()
    if not ai.available():
        raise RuntimeError("AI gateway not configured (Settings → AI)")
    data = json.dumps(dict(body, metadata={"user_email": s["AI_EMAIL"]})).encode() if body is not None else None
    r = urllib.request.Request(s["AI_BASE"].rstrip("/") + path, data=data, method=method,
                               headers={"Authorization": "Bearer " + s["AI_KEY"], "Content-Type": "application/json",
                                        "user_email": s["AI_EMAIL"]})
    try:
        resp = urllib.request.urlopen(r, timeout=timeout); d = resp.read()
        return d if raw else json.loads(d)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"madeye video HTTP {e.code}: {e.read()[:400].decode(errors='replace')}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"AI gateway not reachable ({e}); is the VPN on?") from None


def fal_params(model_id, prompt, start_uri, end_uri, gen_s, negative=""):
    """fal.ai request body for one model family."""
    m = model_info(model_id); neg = ", ".join(x for x in (negative, BASE_AVOID) if x)
    if m["family"] == "kling":
        p = {"prompt": prompt, "image_url": start_uri, "duration": str(gen_s), "negative_prompt": neg, "cfg_scale": 0.5}
        if m.get("end_frame") and end_uri:
            p["tail_image_url"] = end_uri
    elif m["family"] == "veo":
        p = {"prompt": prompt, "image_url": start_uri, "duration": f"{gen_s}s", "generate_audio": False,
             "resolution": "720p", "negative_prompt": neg}
    else:
        p = {"prompt": prompt, "image_url": start_uri, "duration": gen_s}
        if end_uri:
            p["end_image_url"] = end_uri
    return p


def madeye_body(model_id, prompt, start_file, end_file, gen_s, size_wh, negative=""):
    """The /v1/videos request for Veo on Vertex: the frames go in extra_body.instances (LiteLLM passes them on as the
    Veo instance: image = first frame, lastFrame = last frame)."""
    W, H = size_wh
    inst = {"image": {"bytesBase64Encoded": _b64(start_file), "mimeType": "image/jpeg"}}
    if end_file and model_info(model_id).get("end_frame"):
        inst["lastFrame"] = {"bytesBase64Encoded": _b64(end_file), "mimeType": "image/jpeg"}
    return {"model": model_id, "prompt": prompt, "seconds": gen_s, "size": f"{W}x{H}",
            "extra_body": {"instances": inst, "generateAudio": False,
                           "negativePrompt": ", ".join(x for x in (negative, BASE_AVOID) if x)}}


def _generate(model_id, prompt, negative, start, end, gen_s, size, d, tag, on_sub=None, refs=None, poll_s=6, max_wait_s=1800):
    """One video from a model -> the raw file. Veo gets frames padded to 16:9 / 9:16 (cropped back in finish()).
    refs: reference images (Veo 3.1 only; no start / end frame then, 8 s)."""
    m = model_info(model_id); padded = m["family"] == "veo"
    WH = (1280, 720) if not size[0] or size[0] >= size[1] else (720, 1280)
    def pad(f, name):
        if not padded or not f:
            return f
        pf = d / "frames" / f"{name}_pad_{tag}.jpg"; pad_frame(f, pf, *size); return pf
    raw = d / f"out_{tag}_raw.mp4"; t0 = time.time()
    if m["provider"] == "madeye":
        if refs:
            inst = {"referenceImages": [{"image": {"bytesBase64Encoded": _b64(pad(f, f"ref{k}")), "mimeType": "image/jpeg"},
                                         "referenceType": "asset"} for k, f in enumerate(refs)]}
            body = {"model": model_id, "prompt": prompt, "seconds": REF_SECONDS, "size": f"{WH[0]}x{WH[1]}",
                    "extra_body": {"instances": inst, "generateAudio": False,
                                   "negativePrompt": ", ".join(x for x in (negative, BASE_AVOID) if x)}}
        else:
            body = madeye_body(model_id, prompt, pad(start, "start"), pad(end, "end"), gen_s, WH, negative)
        sub = _madeye("POST", "/v1/videos", body)
        if on_sub:
            on_sub(str(sub.get("id"))[-60:])
        while True:
            if time.time() - t0 > max_wait_s:
                raise RuntimeError("the video model did not finish in 30 minutes")
            time.sleep(poll_s)
            st = _madeye("GET", f"/v1/videos/{sub['id']}")
            if st.get("status") == "completed":
                break
            if st.get("status") == "failed":
                raise RuntimeError(f"the video model failed: {json.dumps(st.get('error'))[:400]}")
        raw.write_bytes(_madeye("GET", f"/v1/videos/{sub['id']}/content", raw=True, timeout=300))
        return raw, "madeye", padded
    if refs:
        raise RuntimeError("reference images work with Veo 3.1 through the AI key only")
    body = fal_params(model_id, prompt, _data_uri(start, max_px=1536), _data_uri(end) if end else None, gen_s, negative)
    sub = _fal("POST", f"https://queue.fal.run/{model_id}", body)
    if on_sub:
        on_sub(sub.get("request_id"))
    while True:
        if time.time() - t0 > max_wait_s:
            raise RuntimeError("fal.ai did not finish in 30 minutes")
        st = _fal("GET", sub["status_url"])
        if st.get("status") == "COMPLETED":
            break
        if st.get("status") in ("FAILED", "ERROR", "CANCELLED"):
            raise RuntimeError(f"fal.ai: {st.get('status')} {str(st)[:300]}")
        time.sleep(poll_s)
    res = _fal("GET", sub["response_url"])
    url = (res.get("video") or {}).get("url") or (res.get("videos") or [{}])[0].get("url")
    if not url:
        raise RuntimeError("fal.ai answered without a video: " + json.dumps(res)[:300])
    with urllib.request.urlopen(url, timeout=300) as r, open(raw, "wb") as f:
        shutil.copyfileobj(r, f)
    return raw, url, padded


def plan_mode(th, prm, model_id):
    """'frames' (one shot: first + last frame), 'shots' (cuts in the clip: the short shots kept as filmed, the main
    shot generated), 'refs' (reference images, Veo 3.1). The editor's choice wins when it is possible."""
    shots = (th.get("frames") or {}).get("shots") or []
    want = prm.get("mode")
    if want == "refs" or prm.get("refs"):
        return "refs"
    if want == "frames":
        return "frames"
    return "shots" if len(shots) > 1 else "frames"


def _source_piece(video, a, b, out, size, fps):
    """A shot kept as filmed: a..b ms of the source at its own speed, at the source's size and frame rate."""
    w, h = size
    _ff(["-ss", f"{a / 1000:.3f}", "-i", str(video), "-t", f"{(b - a) / 1000:.3f}", "-an",
         "-vf", f"scale={w}:{h},fps={fps},setsar=1", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", str(out)])


def run_job(db, job_id, work, on_done=None, poll_s=6, max_wait_s=1800, video=None):
    """Make one video: (frames | shots | refs) -> the model -> wait -> download -> shape + length -> Project bin."""
    j = db.one("SELECT * FROM gen_jobs WHERE id=?", (job_id,))
    th = db.one("SELECT * FROM gen_threads WHERE id=?", (j["thread_id"],))
    d = folder(work, th["id"]); prm = j["params"] or {}
    try:
        c = th["clip"]; fr = th["frames"]; slot_s = (c["t1"] - c["t0"]) / 1000
        size = (fr.get("size") or [0, 0])[:2]; fps = fr.get("fps") or 25
        prompt, negative = split_avoid(j["prompt"])
        mode = plan_mode(th, prm, j["model"])
        sub_id = lambda rid: db.update("gen_jobs", job_id, request_id=rid, status="running")
        label = re.sub(r"[^\w#-]+", "_", c.get("label") or "clip")
        out = d / f"gen_{label}_t{th['id']}_v{job_id}.mp4"
        info = {"mode": mode}
        if mode == "refs":
            if not model_info(j["model"]).get("refs"):
                raise RuntimeError(f"{j['model']} cannot take reference images (Veo 3.1 can)")
            refs = [d / f for f in (prm.get("refs") or [])][:3] or \
                   [d / x["start"] for x in (fr.get("shots") or [])][:3] or [d / fr["start"]]
            gen_s = REF_SECONDS; how = "cut" if slot_s <= gen_s else "stretch"
            db.update("gen_jobs", job_id, status="queued", params=dict(prm, mode=mode, seconds=gen_s, fit=how))
            raw, src, padded = _generate(j["model"], prompt, negative, None, None, gen_s, size, d, job_id, sub_id,
                                         refs=refs, poll_s=poll_s, max_wait_s=max_wait_s)
            finish(raw, out, slot_s, gen_s, how, size, padded, fps)
            info.update(refs=len(refs))
        elif mode == "shots":
            shots = fr["shots"]; mk = main_shot(shots)
            keep_ms = sum(x["s1"] - x["s0"] for k, x in enumerate(shots) if k != mk)
            target_s = slot_s - keep_ms / 1000
            if target_s < 0.5:
                raise RuntimeError("the other shots already fill this slot: nothing to generate (use 'reference images' "
                                   "or 'match the frames' instead)")
            x = shots[mk]
            gen_s, how = plan_length(j["model"], target_s)
            db.update("gen_jobs", job_id, status="queued", params=dict(prm, mode=mode, seconds=gen_s, fit=how, shot=mk + 1))
            raw, src, padded = _generate(j["model"], prompt, negative, d / x["start"],
                                         d / x["end"] if prm.get("use_end", True) else None, gen_s, size, d, job_id, sub_id,
                                         poll_s=poll_s, max_wait_s=max_wait_s)
            pieces = []
            for k, sh in enumerate(shots):
                pf = d / f"piece_{job_id}_{k}.mp4"
                if k == mk:
                    finish(raw, pf, target_s, gen_s, how, size, padded, fps)
                else:
                    _source_piece(video or c.get("_video"), sh["s0"], sh["s1"], pf, size, fps)
                pieces.append(pf)
            ins = sum((["-i", str(p_)] for p_ in pieces), [])
            _ff(ins + ["-filter_complex", "".join(f"[{k}:v]" for k in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=0[v]",
                       "-map", "[v]", "-t", f"{slot_s:.3f}", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                       "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])
            info.update(shot=mk + 1, shots=len(shots), kept_s=round(keep_ms / 1000, 2), generated_s=round(target_s, 2))
        else:
            start = d / fr["start"]; end = d / fr["end"] if prm.get("use_end", True) else None
            for f in prm.get("images") or []:          # an image the editor attached replaces the start frame
                start = d / f; break
            gen_s, how = plan_length(j["model"], slot_s)
            db.update("gen_jobs", job_id, status="queued", params=dict(prm, mode=mode, seconds=gen_s, fit=how))
            raw, src, padded = _generate(j["model"], prompt, negative, start, end, gen_s, size, d, job_id, sub_id,
                                         poll_s=poll_s, max_wait_s=max_wait_s)
            finish(raw, out, slot_s, gen_s, how, size, padded, fps)
        it = imports.add_path(work, out)
        db.update("gen_jobs", job_id, status="done", result=dict(info, file=out.name, **{"from": src[:200]}, seconds=gen_s, fit=how),
                  import_id=it["id"], finished=time.time())
    except Exception as ex:
        db.update("gen_jobs", job_id, status="failed", error=str(ex)[:600], finished=time.time())
    if on_done:
        on_done(job_id)


def start_job(db, job_id, work, on_done=None, video=None):
    threading.Thread(target=run_job, args=(db, job_id, work, on_done), kwargs={"video": video}, daemon=True).start()
