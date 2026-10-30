"""Generate a clip with AI: frames, analysis, first prompt, chat with images, video job -> Project bin (gen.py)."""
import io, json, subprocess
from pathlib import Path
from conftest import wait_for
from test_m1 import make_show


def test_generate_flow(env, monkeypatch, tmp_path):
    from dubstudio import ai, gen, imports
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "settings", lambda: {"AI_MODEL": "claude-x", "AI_EMAIL": "e@x", "AI_BASE": "http://x", "AI_KEY": "k"})
    seen = []
    monkeypatch.setattr(ai, "chat", lambda content, model=None, max_tokens=0, **k: (
        'Here: {"people": [{"who": "man"}], "motion": "he turns his head"}', {}))

    monkeypatch.setattr(gen, "gateway_ok", lambda timeout=10: None)

    def fake_req(path, body=None, timeout=0):
        if body["model"].startswith("gemini"):                     # the clip analysis
            return {"choices": [{"message": {"content": 'Here: {"people": [{"who": "man"}], "motion": "he turns his head"}'}}]}
        seen.append(body)
        n = sum(1 for m in body["messages"] if m["role"] == "user")
        return {"choices": [{"message": {"content": f"Prompt {n}:\n```prompt\nA man turns his head slowly, take {n}.\n```"}}]}
    monkeypatch.setattr(ai, "_req", fake_req)
    monkeypatch.setattr(gen, "gateway_models", lambda: ["gemini-3.5-flash"])      # no Veo on this key: fal.ai only
    c = env["client"]; s = make_show(c, env["show"])
    e = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    clip = {"id": "c3", "label": "IT#3", "row": 3, "t0": 0, "t1": 2000, "s0": 500, "s1": 1300, "dub_text": "Ciao", "src_text": "Hello"}
    t = c.post(f"/api/episodes/{e['id']}/gen/threads", json={"clip": clip}).json()
    t = wait_for(lambda: (lambda x: x if x["status"] in ("ready", "error") else None)(c.get(f"/api/episodes/{e['id']}/gen/threads/{t['id']}").json()))
    assert t["status"] == "ready", t.get("error")
    assert len(t["frames"]["frames"]) == gen.N_FRAMES and "turns his head" in t["analysis"]
    assert t["prompt"] == "A man turns his head slowly, take 2." and [m["role"] for m in t["messages"]] == ["user", "assistant"]
    # the context Claude got: the clip facts + 3 frames as images, and the system prompt
    first = seen[0]["messages"]
    assert first[0]["role"] == "system" and "natural speed" in first[0]["content"]
    assert sum(1 for p in first[1]["content"] if p["type"] == "image_url") == 3 and "x0.40" in first[1]["content"][0]["text"]
    for f in t["frames"]["frames"] + [{"file": t["frames"]["start"]}]:
        assert c.get(f"/api/episodes/{e['id']}/gen/threads/{t['id']}/file/{f['file']}").status_code == 200
    assert c.get(f"/api/episodes/{e['id']}/gen/threads/{t['id']}/file/%2E%2E/%2E%2E/%2E%2E/config.json").status_code == 404
    # a message with an image: Claude sees the whole chat and the image; its new prompt is taken
    png = tmp_path / "ref.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:size=64x64", "-frames:v", "1", str(png)], check=True)
    with open(png, "rb") as f:
        t = c.post(f"/api/episodes/{e['id']}/gen/threads/{t['id']}/messages", data={"text": "use this colour"},
                   files=[("files", ("ref.png", f, "image/png"))]).json()
    assert t["prompt"] == "A man turns his head slowly, take 3." and t["messages"][-2]["images"]
    last = seen[-1]["messages"]
    assert len([m for m in last if m["role"] == "user"]) == 3 and any(p["type"] == "image_url" for p in last[-1]["content"])
    assert c.put(f"/api/episodes/{e['id']}/gen/threads/{t['id']}/prompt", json={"prompt": "my own words"}).json()["prompt"] == "my own words"
    # threads are kept per clip slot
    assert [x["id"] for x in c.get(f"/api/episodes/{e['id']}/gen/threads", params={"slot": "0-2000"}).json()] == [t["id"]]
    # generate: no key -> asks for it
    monkeypatch.setattr(gen, "fal_key", lambda: "")
    assert c.post(f"/api/episodes/{e['id']}/gen/threads/{t['id']}/generate", json={}).status_code == 409
    # with a key: submit -> completed -> download -> cut to the 2 s slot -> in the Project bin
    monkeypatch.setattr(gen, "fal_key", lambda: "fal-test")
    vid = tmp_path / "fal.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x160:rate=25:duration=5", str(vid)], check=True)
    calls = []

    def fake_fal(method, url, body=None, timeout=60):
        calls.append((method, url, body))
        if method == "POST":
            return {"request_id": "r1", "status_url": "http://fal/status", "response_url": "http://fal/result"}
        return {"status": "COMPLETED"} if url.endswith("status") else {"video": {"url": "http://fal/v.mp4"}}
    monkeypatch.setattr(gen, "_fal", fake_fal)
    real_open = gen.urllib.request.urlopen
    monkeypatch.setattr(gen.urllib.request, "urlopen", lambda u, timeout=0: open(vid, "rb") if u == "http://fal/v.mp4" else real_open(u, timeout=timeout))
    t = c.post(f"/api/episodes/{e['id']}/gen/threads/{t['id']}/generate",
               json={"model": "fal-ai/kling-video/v2.1/pro/image-to-video", "use_end": True}).json()
    job = wait_for(lambda: (lambda j: j if j["status"] in ("done", "failed") else None)(
        c.get(f"/api/episodes/{e['id']}/gen/threads/{t['id']}").json()["jobs"][-1]))
    assert job["status"] == "done", job["error"]
    body = calls[0][2]
    assert calls[0][1].endswith("kling-video/v2.1/pro/image-to-video") and body["duration"] == "5" and body["prompt"] == "my own words"
    assert body["image_url"].startswith("data:image/jpeg;base64,") and body["tail_image_url"].startswith("data:image/jpeg")
    it = imports.get(e["work_dir"], job["import_id"])
    assert it and abs(it["duration_ms"] - 2000) < 100            # cut to the slot
    assert any(x["id"] == job["import_id"] for x in c.get(f"/api/episodes/{e['id']}/bin").json()["imports"])


def test_lengths_params_and_avoid():
    from dubstudio import gen
    veo = "veo-3.1-fast-generate-001"
    assert gen.plan_length(veo, 4.21) == (4, "retime")          # 4 s made, x0.95 to fit: the end frame stays the end
    assert gen.plan_length(veo, 7.0) == (6, "retime") and gen.plan_length(veo, 2.0) == (4, "cut")
    assert gen.plan_length(veo, 12.0) == (8, "stretch")
    assert gen.plan_length("fal-ai/kling-video/v2.1/pro/image-to-video", 2.0) == (5, "cut")
    p = gen.fal_params("fal-ai/veo3/fast/image-to-video", "x", "data:a", "data:b", 8, "dogs")
    assert p["duration"] == "8s" and "tail_image_url" not in p and p["generate_audio"] is False and p["negative_prompt"].startswith("dogs, ")
    p = gen.fal_params("fal-ai/kling-video/v2.1/standard/image-to-video", "x", "data:a", "data:b", 10)
    assert p["duration"] == "10" and "tail_image_url" not in p
    assert gen.extract_prompt("hi\n```prompt\nA calm shot.\n```\nok") == "A calm shot."
    assert gen.split_avoid("A calm shot of a man.\nAvoid: dogs, text.") == ("A calm shot of a man.", "dogs, text")
    assert gen.split_avoid("No avoid line") == ("No avoid line", "")


def test_veo_through_madeye_square_source(env, monkeypatch, tmp_path):
    """Veo on the AI key (LiteLLM /v1/videos): first + last frame padded to 16:9 in extra_body.instances, the
    'Avoid:' line as negative prompt, the result cropped back to the square source and fitted to the slot."""
    from dubstudio import ai, gen, imports
    from dubstudio.db import DB, now
    monkeypatch.setattr(gen, "gateway_models", lambda: ["veo-3.1-fast-generate-001", "gemini-3.5-flash"])
    work = tmp_path / "w"; d = gen.folder(work, 1) / "frames"; d.mkdir(parents=True)
    sq = tmp_path / "sq.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x320:rate=25:duration=3", str(sq)], check=True)
    fr = gen.make_frames(work, 1, sq, 500, 2000)
    assert fr["size"] == [320, 320] and len(fr["frames"]) == gen.N_FRAMES
    db = DB(tmp_path / "t.db")
    db.ex("INSERT INTO shows (id, name, code, folder, profile) VALUES (1, 's', 'S', '/x', '{}')")
    db.ex("INSERT INTO episodes (id, show_id, number) VALUES (1, 1, 1)")
    tid = db.insert("gen_threads", episode_id=1, slot="0-4210", clip={"label": "IT#4", "t0": 0, "t1": 4210, "s0": 500, "s1": 2000},
                    frames=fr, status="ready", created=now())
    jid = db.insert("gen_jobs", thread_id=tid, model="veo-3.1-fast-generate-001", prompt="A calm shot.\nAvoid: dogs",
                    params={"use_end": True}, status="starting", created=now())
    made = tmp_path / "veo.mp4"                                    # what Veo returns: 16:9, 4 s
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=24:duration=4", str(made)], check=True)
    calls = []

    def fake(method, path, body=None, raw=False, timeout=0):
        calls.append((method, path, body))
        if method == "POST":
            return {"id": "video_abc", "status": "processing"}
        if path.endswith("/content"):
            return made.read_bytes()
        return {"status": "completed"}
    monkeypatch.setattr(gen, "_madeye", fake)
    gen.run_job(db, jid, work, poll_s=0)
    j = db.one("SELECT * FROM gen_jobs WHERE id=?", (jid,))
    assert j["status"] == "done", j["error"]
    b = calls[0][2]
    assert calls[0][1] == "/v1/videos" and b["seconds"] == 4 and b["size"] == "1280x720" and b["prompt"] == "A calm shot."
    inst = b["extra_body"]["instances"]
    assert inst["image"]["bytesBase64Encoded"] and inst["lastFrame"]["bytesBase64Encoded"]
    assert b["extra_body"]["generateAudio"] is False and b["extra_body"]["negativePrompt"].startswith("dogs, ")
    assert j["result"]["fit"] == "retime"
    it = imports.get(work, j["import_id"])
    assert (it["width"], it["height"]) == (320, 320) and abs(it["duration_ms"] - 4210) < 80   # square again, slot length


def test_fal_key_saved_never_shown_and_removable(env, monkeypatch, tmp_path):
    from dubstudio import settings as stg, gen
    monkeypatch.setattr(stg, "ENV", tmp_path / ".env"); monkeypatch.setattr(gen, "ROOT", tmp_path)
    monkeypatch.delenv("FAL_KEY", raising=False)
    (tmp_path / ".env").write_text("AI_KEY=sk-KEEP\n")
    c = env["client"]
    r = c.put("/api/settings/ai", json={"fal_key": "fal-SECRET-1"}).json()
    assert r["fal_set"] is True and "fal-SECRET-1" not in str(r)
    txt = (tmp_path / ".env").read_text()
    assert "FAL_KEY=fal-SECRET-1" in txt and "AI_KEY=sk-KEEP" in txt
    assert c.get("/api/gen/status").json()["video"] is True
    c.put("/api/settings/ai", json={"fal_key": ""})                         # empty = keep
    assert "FAL_KEY=fal-SECRET-1" in (tmp_path / ".env").read_text()
    r = c.put("/api/settings/ai", json={"remove_fal_key": True}).json()
    txt = (tmp_path / ".env").read_text()
    assert r["fal_set"] is False and "FAL_KEY" not in txt and "AI_KEY=sk-KEEP" in txt



def test_analysis_vpn_off_fallback_restart_and_retry(env, monkeypatch, tmp_path):
    """VPN off: a clear error in seconds (not a 25-min hang); Gemini failing: Claude reads the frames instead; a
    restart while preparing: the chat says so; Try again prepares the same chat."""
    import urllib.error
    from dubstudio import ai, gen
    from conftest import wait_for
    from test_m1 import make_show
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "settings", lambda: {"AI_MODEL": "claude-x", "AI_EMAIL": "e@x", "AI_BASE": "http://x", "AI_KEY": "k"})
    monkeypatch.setattr(gen, "gateway_models", lambda: ["gemini-3.5-flash"])
    c = env["client"]; s = make_show(c, env["show"])
    e = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    clip = {"id": "c1", "label": "IT#1", "row": 1, "t0": 0, "t1": 2000, "s0": 500, "s1": 1300, "dub_text": "Ciao", "src_text": "Hi"}
    # 1. the gateway does not answer
    def down(*a, **k):
        raise urllib.error.URLError("timed out")
    monkeypatch.setattr(gen.urllib.request, "urlopen", down)
    t = c.post(f"/api/episodes/{e['id']}/gen/threads", json={"clip": clip}).json()
    t = wait_for(lambda: (lambda x: x if x["status"] in ("ready", "error") else None)(c.get(f"/api/episodes/{e['id']}/gen/threads/{t['id']}").json()), timeout=15)
    assert t["status"] == "error" and "VPN" in t["error"] and t["frames"]          # the frames were made; then it said why
    # 2. back on, but Gemini cannot watch the clip: Claude reads the 9 frames; Try again uses the same chat
    monkeypatch.setattr(gen, "gateway_ok", lambda timeout=10: None)
    calls = []
    def req(path, body=None, timeout=0):
        calls.append(body["model"])
        if body["model"].startswith("gemini"):
            raise urllib.error.HTTPError("u", 500, "video not supported", {}, None)
        if any(p.get("type") == "image_url" for m in body["messages"] for p in (m["content"] if isinstance(m["content"], list) else [])) \
                and "frames of one clip" in str(body["messages"][-1]["content"]):
            return {"choices": [{"message": {"content": '{"people": [], "motion": "still", "camera": "static"}'}}]}
        return {"choices": [{"message": {"content": "```prompt\nA still shot.\nAvoid: text\n```"}}]}
    monkeypatch.setattr(ai, "_req", req)
    monkeypatch.setattr(gen.time, "sleep", lambda s: None)
    t = c.post(f"/api/episodes/{e['id']}/gen/threads/{t['id']}/retry").json()
    t = wait_for(lambda: (lambda x: x if x["status"] in ("ready", "error") else None)(c.get(f"/api/episodes/{e['id']}/gen/threads/{t['id']}").json()))
    assert t["status"] == "ready", t["error"]
    assert '"_by": "claude-x (read 9 frames)"' in t["analysis"] and calls[:2] == ["gemini-3.5-flash", "gemini-3.5-flash"]
    assert t["prompt"] == "A still shot.\nAvoid: text" and [m["role"] for m in t["messages"]] == ["user", "assistant"]
    # 3. a chat left half-done by a restart is marked, not stuck
    from dubstudio import main
    main.db().update("gen_threads", t["id"], status="analysing")
    main.gen_interrupted(main.db())
    assert c.get(f"/api/episodes/{e['id']}/gen/threads/{t['id']}").json()["status"] == "error"


def _two_shot_video(path):
    """3 s: 1 s red (shot 1), then 2 s of a moving test pattern (shot 2): a hard cut at 1.0 s."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:size=320x320:rate=25:duration=1",
                    "-f", "lavfi", "-i", "testsrc=size=320x320:rate=25:duration=2",
                    "-filter_complex", "[0][1]concat=n=2:v=1:a=0", str(path)], check=True)


def test_shots_kept_and_main_shot_generated(monkeypatch, tmp_path):
    """A clip with a camera cut: the short shot plays as filmed, only the main shot is generated (from its own first
    and last frame) and fills the rest of the slot; reference images: no start frame, 8 s, then cut to the slot."""
    from dubstudio import gen, imports
    from dubstudio.db import DB, now
    v = tmp_path / "two.mp4"; _two_shot_video(v)
    shots = gen.detect_shots(v, 200, 2800)
    assert len(shots) == 2 and abs(shots[0][1] - 1000) < 60
    work = tmp_path / "w"
    fr = gen.make_frames(work, 1, v, 200, 2800)
    assert len(fr["shots"]) == 2 and fr["frames"][0]["shot"] == 0 and fr["frames"][-1]["shot"] == 1
    assert (gen.folder(work, 1) / fr["shots"][1]["start"]).exists()
    db = DB(tmp_path / "t.db")
    db.ex("INSERT INTO shows (id, name, code, folder, profile) VALUES (1, 's', 'S', '/x', '{}')")
    db.ex("INSERT INTO episodes (id, show_id, number) VALUES (1, 1, 1)")
    tid = db.insert("gen_threads", episode_id=1, slot="0-6000", clip={"label": "IT#9", "t0": 0, "t1": 6000, "s0": 200, "s1": 2800},
                    frames=fr, status="ready", created=now())
    made = tmp_path / "veo.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24:duration=8", str(made)], check=True)
    calls = []

    def fake(method, path, body=None, raw=False, timeout=0):
        calls.append(body)
        if method == "POST":
            return {"id": "video_x", "status": "processing"}
        return made.read_bytes() if path.endswith("/content") else {"status": "completed"}
    monkeypatch.setattr(gen, "_madeye", fake)
    # keep the short shots (default for a clip with cuts)
    jid = db.insert("gen_jobs", thread_id=tid, model="veo-3.1-fast-generate-001", prompt="The pattern moves.\nAvoid: red",
                    params={"use_end": True}, status="starting", created=now())
    gen.run_job(db, jid, work, poll_s=0, video=v)
    j = db.one("SELECT * FROM gen_jobs WHERE id=?", (jid,))
    assert j["status"] == "done", j["error"]
    r = j["result"]
    assert r["mode"] == "shots" and r["shot"] == 2 and abs(r["kept_s"] - 0.8) < 0.07 and abs(r["generated_s"] - 5.2) < 0.07
    assert calls[0]["seconds"] == 6 and "lastFrame" in calls[0]["extra_body"]["instances"]      # 5.2 s -> 6 s made, retimed
    it = imports.get(work, j["import_id"])
    assert abs(it["duration_ms"] - 6000) < 80 and (it["width"], it["height"]) == (320, 320)
    # reference images: no first / last frame, 8 s, cut to the 6 s slot
    calls.clear()
    jid = db.insert("gen_jobs", thread_id=tid, model="veo-3.1-fast-generate-001", prompt="Shot 1 red, hard cut to shot 2.",
                    params={"mode": "refs", "refs": [fr["shots"][0]["start"], fr["shots"][1]["start"]]}, status="starting", created=now())
    gen.run_job(db, jid, work, poll_s=0, video=v)
    j = db.one("SELECT * FROM gen_jobs WHERE id=?", (jid,))
    assert j["status"] == "done", j["error"]
    inst = calls[0]["extra_body"]["instances"]
    assert calls[0]["seconds"] == 8 and "image" not in inst and "lastFrame" not in inst and len(inst["referenceImages"]) == 2
    assert inst["referenceImages"][0]["referenceType"] == "asset" and j["result"]["fit"] == "cut"
    assert abs(imports.get(work, j["import_id"])["duration_ms"] - 6000) < 80
    # a model without reference images refuses
    jid = db.insert("gen_jobs", thread_id=tid, model="veo-3.0-generate-001", prompt="x", params={"mode": "refs"},
                    status="starting", created=now())
    gen.run_job(db, jid, work, poll_s=0, video=v)
    assert "cannot take reference images" in db.one("SELECT error FROM gen_jobs WHERE id=?", (jid,))["error"]
