"""Bulk generation: create a range of episodes, attach numbered files, queue them all."""
from pathlib import Path
from conftest import wait_for
from test_m1 import make_show
from test_storage import r2  # noqa: F401 — cloud-upload fixture
from test_uploads_queue import cloud_show, upload
from dubstudio.main import episode_from_name


def test_episode_from_name_reads_common_titles():
    """The user should not have to rename TAB_FR_S01E11.mp4 to 11.mp4."""
    assert episode_from_name("TAB FR eo 14 .mp4") == 14
    assert episode_from_name("TAB FR eo 14 .mp4", {14, 15}) == 14
    assert episode_from_name("TAB FR ep 14.mp4") == 14
    assert episode_from_name("11.mp4") == 11
    assert episode_from_name("011.mp3") == 11
    assert episode_from_name("ep12.docx") == 12
    assert episode_from_name("TAB_FR_S01E11.mp4") == 11
    assert episode_from_name("TAB_Italy_Ep11.mp3") == 11
    assert episode_from_name("Italy episode 12 - title.mp3") == 12
    assert episode_from_name("puntata 11.docx") == 11
    assert episode_from_name("Show 1080p ep11.mp4") == 11
    assert episode_from_name("trailer.mp4") is None
    assert episode_from_name("2024_recap.mp4") is None
    assert episode_from_name("TAB_FR_S01E11.mp4", {11, 12}) == 11
    assert episode_from_name("TAB_FR_S01E11.mp4", {1, 2}) is None
    assert episode_from_name("Show 11 and 12.mp4", {11, 12}) is None
    assert episode_from_name("TGS_EPISODE_31.mp4", {32, 33}) is None
    assert episode_from_name("TGS_EPISODE_32.mp4", {32, 33}) == 32


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
    got = c.get(f"/api/batches/{b['id']}").json()
    assert got["id"] == b["id"]
    assert all("have" in e and "ready" in e and "steps" in e for e in got["episodes"])


def test_bulk_does_not_overwrite_an_existing_episode(env):
    c = env["client"]; s = make_show(c, env["show"])
    assert c.post(f"/api/shows/{s['id']}/episodes", json={"number": 32}).status_code == 200
    r = c.post(f"/api/shows/{s['id']}/batches", json={"start": 31, "end": 34})
    assert r.status_code == 409 and "32" in r.json()["detail"]
    assert [e["number"] for e in c.get(f"/api/shows/{s['id']}/episodes").json()] == [32]


def test_deleting_a_row_frees_the_number_for_a_new_bulk(env):
    """Empty or leftover rows block the same range; Delete removes them so the bulk can be created again."""
    c = env["client"]; s = make_show(c, env["show"])
    b = c.post(f"/api/shows/{s['id']}/batches", json={"range": "31-34"}).json()
    ids = {e["number"]: e["id"] for e in b["episodes"]}
    assert c.delete(f"/api/episodes/{ids[32]}").json() == {"deleted": ids[32]}
    assert [e["number"] for e in c.get(f"/api/shows/{s['id']}/episodes").json()] == [31, 33, 34]
    blocked = c.post(f"/api/shows/{s['id']}/batches", json={"range": "31-34"})
    assert blocked.status_code == 409 and "31" in blocked.json()["detail"]
    for n in (31, 33, 34):
        assert c.delete(f"/api/episodes/{ids[n]}").status_code == 200
    b2 = c.post(f"/api/shows/{s['id']}/batches", json={"range": "31-34"}).json()
    assert [e["number"] for e in b2["episodes"]] == [31, 32, 33, 34]
    assert b2["id"] != b["id"]


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


def test_bulk_matches_long_file_names_in_the_range(env):
    """TAB FR / Italy titles that already contain 11 and 12, without renaming to 11.mp4."""
    c = env["client"]; s = make_show(c, env["show"])
    video = (env["show"] / "US" / "SHOW Ep - 17.mp4").read_bytes()
    audio = (env["show"] / "italy" / "Episode 17 - Prova.mp3").read_bytes()
    src = env["tmp"] / "tab-src"; dub = env["tmp"] / "tab-dub"; scripts = env["tmp"] / "tab-doc"
    src.mkdir(); dub.mkdir(); scripts.mkdir()
    (src / "TAB_FR_S01E11.mp4").write_bytes(video)
    (src / "TAB_FR_S01E12.mp4").write_bytes(video)
    (src / "TAB_FR_S01E10.mp4").write_bytes(video)
    (dub / "TAB_Italy_Ep11.mp3").write_bytes(audio)
    (dub / "TAB Italy episode 12.mp3").write_bytes(audio)
    (scripts / "TAB_IT_11.docx").write_text("s11")
    (scripts / "puntata 12.docx").write_text("s12")
    prev = c.post(f"/api/shows/{s['id']}/batches/preview", json={
        "start": 11, "end": 12, "src_video_dir": str(src), "dub_audio_dir": str(dub),
        "dub_script_dir": str(scripts)}).json()
    by = {r["number"]: r for r in prev["episodes"]}
    assert by[11]["src_video"].endswith("TAB_FR_S01E11.mp4") and not by[11]["missing"]
    assert by[12]["dub_audio"].endswith("TAB Italy episode 12.mp3")
    assert prev["ready"] == 2
    assert prev["skipped"]["src_video"] == ["TAB_FR_S01E10.mp4"]

    b = c.post(f"/api/shows/{s['id']}/batches", json={
        "start": 11, "end": 12, "src_video_dir": str(src), "dub_audio_dir": str(dub),
        "dub_script_dir": str(scripts)}).json()
    names = {}
    for e in b["episodes"]:
        names[e["number"]] = {a["kind"]: Path(a["path"]).name
                              for a in c.get(f"/api/episodes/{e['id']}").json()["assets"] if a.get("role") == "input"}
    assert names[11]["src_video"] == "TAB_FR_S01E11.mp4"
    assert names[11]["dub_audio"] == "TAB_Italy_Ep11.mp3"
    assert names[12]["dub_script"] == "puntata 12.docx"


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


def test_bulk_render_leaves_processing_episodes_alone(env):
    """The bulk Render button only starts episodes waiting in review, never ones still processing."""
    c = env["client"]; s = make_show(c, env["show"])
    b = c.post(f"/api/shows/{s['id']}/batches", json={"range": "17-17"}).json()
    eid = b["episodes"][0]["id"]
    c.post(f"/api/batches/{b['id']}/process")
    wait_for(lambda: c.get(f"/api/episodes/{eid}").json()["status"] == "processing")
    r = c.post(f"/api/batches/{b['id']}/render")
    assert r.status_code == 200, r.text
    assert r.json()["results"] == []
    assert c.get(f"/api/episodes/{eid}").json()["status"] == "processing"
    r2 = c.post(f"/api/shows/{s['id']}/render-review")
    assert r2.status_code == 200 and r2.json()["results"] == []


def test_bulk_render_skips_a_review_episode_with_a_gap(env):
    """A review episode with a black hole is skipped; processing ones are not in the result list at all."""
    from pathlib import Path
    from test_editor import write_manifest
    import dubstudio.main as main
    c = env["client"]; s = make_show(c, env["show"])
    b = c.post(f"/api/shows/{s['id']}/batches", json={"range": "17-17"}).json()
    eid = b["episodes"][0]["id"]
    ep = c.get(f"/api/episodes/{eid}").json()
    work = Path(ep["work_dir"]); work.mkdir(parents=True, exist_ok=True)
    # clips cover 0–2 s of a 3 s episode → a 1 s black gap at the end
    write_manifest(work, [(0, 1000, 0, 1000), (1000, 2000, 1000, 2000)], 3000)
    main.state["db"].update("episodes", eid, status="review")
    skipped = c.post(f"/api/batches/{b['id']}/render").json()["results"]
    assert len(skipped) == 1 and skipped[0]["ok"] is False
    assert "gap" in skipped[0]["error"]
    assert c.get(f"/api/episodes/{eid}").json()["status"] == "review"


def test_bulk_render_starts_a_flagged_episode_when_there_is_no_gap(env):
    """Flags (slow, repeat) do not skip a list render. A later editor pass can still fix them."""
    from pathlib import Path
    from test_editor import write_manifest
    import dubstudio.main as main
    c = env["client"]; s = make_show(c, env["show"])
    b = c.post(f"/api/shows/{s['id']}/batches", json={"range": "17-17"}).json()
    eid = b["episodes"][0]["id"]
    ep = c.get(f"/api/episodes/{eid}").json()
    work = Path(ep["work_dir"]); work.mkdir(parents=True, exist_ok=True)
    write_manifest(work, [(0, 1000, 0, 1000), (1000, 2000, 1000, 1100), (2000, 3000, 2000, 3000)], 3000)
    db = main.state["db"]
    db.update("episodes", eid, status="review")
    db.insert("jobs", episode_id=eid, run=1, step="review", ord=1, status="needs_input", needs=[], resource="none")
    db.insert("jobs", episode_id=eid, run=1, step="render", ord=2, status="waiting", needs=["review"], resource="cpu")
    started = c.post(f"/api/batches/{b['id']}/render").json()["results"]
    assert len(started) == 1 and started[0]["ok"] is True
