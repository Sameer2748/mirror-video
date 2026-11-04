"""Bulk generation: create a range of episodes, attach numbered files, queue them all."""
from pathlib import Path
from conftest import wait_for
from test_m1 import make_show
from test_storage import r2  # noqa: F401 — cloud-upload fixture
from test_uploads_queue import cloud_show, upload


def numbered_dirs(tmp, video_bytes, audio_bytes, numbers=(31, 34)):
    src = tmp / "src_videos"; dub = tmp / "dub_audio"; scripts = tmp / "dub_scripts"
    src.mkdir(parents=True); dub.mkdir(parents=True); scripts.mkdir(parents=True)
    for n in numbers:
        (src / f"{n}.mp4").write_bytes(video_bytes)
        (dub / f"{n}.mp3").write_bytes(audio_bytes)
        (scripts / f"{n}.docx").write_text(f"script {n}")
    return src, dub, scripts


def test_bulk_rejects_a_bad_range(env):
    c = env["client"]; s = make_show(c, env["show"])
    assert c.post(f"/api/shows/{s['id']}/batches", json={"start": 20, "end": 1}).status_code == 400
    assert c.post(f"/api/shows/{s['id']}/batches", json={"range": "nope"}).status_code == 400
    assert c.post(f"/api/shows/{s['id']}/batches", json={"range": "1-200"}).status_code == 400


def test_bulk_creates_every_episode_in_the_range(env):
    c = env["client"]; s = make_show(c, env["show"])
    r = c.post(f"/api/shows/{s['id']}/batches", json={"range": "31-34"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["start_num"] == 31 and b["end_num"] == 34
    assert [e["number"] for e in b["episodes"]] == [31, 32, 33, 34]
    assert all(e["status"] == "new" and e["batch_id"] == b["id"] for e in b["episodes"])
    listed = c.get(f"/api/shows/{s['id']}/episodes").json()
    assert [e["number"] for e in listed] == [31, 32, 33, 34]
    assert c.get(f"/api/shows/{s['id']}/batches").json()[0]["id"] == b["id"]
    assert c.get(f"/api/batches/{b['id']}").json()["id"] == b["id"]


def test_bulk_does_not_overwrite_an_existing_episode(env):
    c = env["client"]; s = make_show(c, env["show"])
    assert c.post(f"/api/shows/{s['id']}/episodes", json={"number": 32}).status_code == 200
    r = c.post(f"/api/shows/{s['id']}/batches", json={"start": 31, "end": 34})
    assert r.status_code == 409 and "32" in r.json()["detail"]
    assert [e["number"] for e in c.get(f"/api/shows/{s['id']}/episodes").json()] == [32]


def test_single_episode_still_works_next_to_bulk(env):
    c = env["client"]; s = make_show(c, env["show"])
    c.post(f"/api/shows/{s['id']}/batches", json={"start": 31, "end": 32})
    e = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    assert e["number"] == 17 and not e.get("batch_id")
    assert e["candidates"]["src_video"]


def test_bulk_ignores_files_outside_the_range(env):
    """32–33 uses only those two files even when the folders also have 31, 34, and junk names."""
    c = env["client"]; s = make_show(c, env["show"])
    video = (env["show"] / "US" / "SHOW Ep - 17.mp4").read_bytes()
    audio = (env["show"] / "italy" / "Episode 17 - Prova.mp3").read_bytes()
    src, dub, scripts = numbered_dirs(env["tmp"] / "bulk-range", video, audio, (31, 32, 33, 34))
    (src / "trailer.mp4").write_bytes(video)
    (src / "TGS_EPISODE_31.mp4").write_bytes(video)
    (dub / "theme.mp3").write_bytes(audio)
    prev = c.post(f"/api/shows/{s['id']}/batches/preview", json={
        "start": 32, "end": 33, "src_video_dir": str(src), "dub_audio_dir": str(dub),
        "dub_script_dir": str(scripts)}).json()
    assert [r["number"] for r in prev["episodes"]] == [32, 33]
    assert prev["ready"] == 2
    assert set(prev["skipped"]["src_video"]) == {"31.mp4", "34.mp4", "trailer.mp4", "TGS_EPISODE_31.mp4"}
    assert set(prev["skipped"]["dub_audio"]) == {"31.mp3", "34.mp3", "theme.mp3"}
    assert set(prev["skipped"]["dub_script"]) == {"31.docx", "34.docx"}

    b = c.post(f"/api/shows/{s['id']}/batches", json={
        "start": 32, "end": 33, "src_video_dir": str(src), "dub_audio_dir": str(dub),
        "dub_script_dir": str(scripts)}).json()
    assert [e["number"] for e in b["episodes"]] == [32, 33]
    have = {e["number"]: set(e["have"]) for e in b["episodes"]}
    assert have[32] >= {"src_video", "dub_audio", "dub_script"}
    assert have[33] >= {"src_video", "dub_audio", "dub_script"}
    listed = c.get(f"/api/shows/{s['id']}/episodes").json()
    assert [e["number"] for e in listed] == [32, 33]
    for e in b["episodes"]:
        names = {a["kind"]: Path(a["path"]).name
                 for a in c.get(f"/api/episodes/{e['id']}").json()["assets"] if a.get("role") == "input"}
        assert names["src_video"] == f"{e['number']}.mp4"
        assert names["dub_audio"] == f"{e['number']}.mp3"
        assert names["dub_script"] == f"{e['number']}.docx"


def test_preview_and_create_attach_numbered_files(env):
    c = env["client"]; s = make_show(c, env["show"])
    video = (env["show"] / "US" / "SHOW Ep - 17.mp4").read_bytes()
    audio = (env["show"] / "italy" / "Episode 17 - Prova.mp3").read_bytes()
    src, dub, scripts = numbered_dirs(env["tmp"] / "bulk", video, audio, (31, 34))
    prev = c.post(f"/api/shows/{s['id']}/batches/preview", json={
        "start": 31, "end": 34, "src_video_dir": str(src), "dub_audio_dir": str(dub),
        "dub_script_dir": str(scripts)}).json()
    by_n = {r["number"]: r for r in prev["episodes"]}
    assert by_n[31]["src_video"].endswith("31.mp4") and not by_n[31]["missing"]
    assert by_n[32]["missing"] == ["src_video", "dub_audio"]
    assert prev["ready"] == 2

    b = c.post(f"/api/shows/{s['id']}/batches", json={
        "start": 31, "end": 34, "src_video_dir": str(src), "dub_audio_dir": str(dub),
        "dub_script_dir": str(scripts)}).json()
    have = {e["number"]: set(e["have"]) for e in b["episodes"]}
    assert have[31] >= {"src_video", "dub_audio", "dub_script"}
    assert have[34] >= {"src_video", "dub_audio"}
    assert "src_video" not in have[32]


def test_bulk_process_queues_ready_episodes(env):
    c = env["client"]; s = make_show(c, env["show"])
    video = (env["show"] / "US" / "SHOW Ep - 17.mp4").read_bytes()
    audio = (env["show"] / "italy" / "Episode 17 - Prova.mp3").read_bytes()
    src, dub, scripts = numbered_dirs(env["tmp"] / "bulkq", video, audio, (31, 34))
    b = c.post(f"/api/shows/{s['id']}/batches", json={
        "start": 31, "end": 34, "src_video_dir": str(src), "dub_audio_dir": str(dub),
        "dub_script_dir": str(scripts)}).json()
    r = c.post(f"/api/batches/{b['id']}/process")
    assert r.status_code == 200, r.text
    results = {x["number"]: x for x in r.json()["results"]}
    assert results[31]["ok"] and results[34]["ok"]
    assert results[32]["ok"] is False and "missing" in results[32]["error"]
    statuses = {e["number"]: e["status"] for e in r.json()["episodes"]}
    assert statuses[31] in ("processing", "queued")
    assert statuses[34] in ("processing", "queued")
    assert statuses[32] == "new"
    # one episode at a time: the other waits in the line
    assert {"processing", "queued"} <= set(statuses[k] for k in (31, 34)) or statuses[31] == statuses[34] == "processing"


def test_bulk_upload_then_process_on_a_cloud_show(r2):
    c = r2["client"]
    s = cloud_show(c, code="BLK", name="Bulk cloud")
    b = c.post(f"/api/shows/{s['id']}/batches", json={"start": 31, "end": 32}).json()
    assert len(b["episodes"]) == 2
    video, audio = (r2["show"] / "US" / "SHOW Ep - 17.mp4").read_bytes(), (r2["show"] / "italy" / "Episode 17 - Prova.mp3").read_bytes()
    for e in b["episodes"]:
        assert upload(c, e["id"], "src_video", f"{e['number']}.mp4", video).status_code == 200
        assert upload(c, e["id"], "dub_audio", f"{e['number']}.mp3", audio).status_code == 200
    r = c.post(f"/api/batches/{b['id']}/process")
    assert r.status_code == 200, r.text
    assert all(x["ok"] for x in r.json()["results"])
    got = c.get(f"/api/batches/{b['id']}").json()
    assert {e["status"] for e in got["episodes"]} <= {"processing", "queued"}
    first = next(e for e in got["episodes"] if e["status"] == "processing")
    wait_for(lambda: any(j["status"] in ("running", "done")
                         for j in c.get(f"/api/episodes/{first['id']}").json()["jobs"]), 20)
    jobs = [j["step"] for j in c.get(f"/api/episodes/{first['id']}").json()["jobs"]]
    assert jobs[0] == "fetch" and "lip_gpu" in jobs


def test_bulk_cancel_leaves_the_queue(env):
    c = env["client"]; s = make_show(c, env["show"])
    video = (env["show"] / "US" / "SHOW Ep - 17.mp4").read_bytes()
    audio = (env["show"] / "italy" / "Episode 17 - Prova.mp3").read_bytes()
    src, dub, scripts = numbered_dirs(env["tmp"] / "bulkc", video, audio, (31, 34))
    b = c.post(f"/api/shows/{s['id']}/batches", json={
        "start": 31, "end": 34, "src_video_dir": str(src), "dub_audio_dir": str(dub)}).json()
    c.post(f"/api/batches/{b['id']}/process")
    r = c.post(f"/api/batches/{b['id']}/cancel")
    assert r.status_code == 200, r.text
    wait_for(lambda: all(e["status"] in ("new", "cancelled")
                         for e in c.get(f"/api/batches/{b['id']}").json()["episodes"]
                         if e["number"] in (31, 34)))
