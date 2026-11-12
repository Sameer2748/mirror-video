"""The web flow (docs/09): a show made in the browser, files uploaded to R2, the queue, and fetch / publish / cleanup.

The fake pipeline of conftest.py runs the real step graph in seconds, and the fake S3 server of test_storage.py
stands in for R2, so this covers the whole path without a video file or a real bucket.
"""
import io, json, time
from pathlib import Path

import pytest

from conftest import wait_for
from test_storage import r2, FakeS3, BUCKET            # noqa: F401 (the r2 fixture)


def cloud_show(c, code="CLD", name="Cloud show"):
    r = c.post("/api/shows", json={"name": name, "code": code, "preset": "WBM"})
    assert r.status_code == 200, r.text
    return r.json()


def upload(c, eid, kind, filename, data=b"x" * 4096):
    return c.post(f"/api/episodes/{eid}/files/{kind}", files={"file": (filename, io.BytesIO(data), "application/octet-stream")})


def real_media(env):
    """The tiny video and audio conftest made with ffmpeg: real files, so the checks in `ingest` are real too."""
    show = env["show"]
    return (show / "US" / "SHOW Ep - 17.mp4").read_bytes(), (show / "italy" / "Episode 17 - Prova.mp3").read_bytes()


def episode_with_files(c, show, number=17, env=None, video=None, audio=None):
    if video is None or audio is None:
        video, audio = real_media(env) if env else (b"v" * 8192, b"a" * 4096)
    ep = c.post(f"/api/shows/{show['id']}/episodes", json={"number": number}).json()
    assert upload(c, ep["id"], "src_video", "source.mp4", video).status_code == 200
    assert upload(c, ep["id"], "dub_audio", "dub.mp3", audio).status_code == 200
    return c.get(f"/api/episodes/{ep['id']}").json()


# ---------------- a show without a folder on the Mac ----------------
def test_a_show_can_be_made_without_a_mac_folder(r2):
    c = r2["client"]
    s = cloud_show(c)
    assert s["profile"]["uploads"] is True
    assert Path(s["folder"]).is_dir() and s["folder"].endswith("/shows/CLD")      # its own staging folder
    assert "CLD" in [x["code"] for x in c.get("/api/shows").json()]


def test_the_localisation_sheet_is_uploaded_with_the_show(r2):
    c = r2["client"]
    s = cloud_show(c)
    sheet = b"source name,target name\nViola,Viola\nTom,Tommaso\n"
    r = c.post(f"/api/shows/{s['id']}/sheet", files={"file": ("names.csv", io.BytesIO(sheet), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["names"] == 2
    assert r2["objects"]["shows/CLD/localisation/names.csv"] == sheet
    show = c.get(f"/api/shows/{s['id']}").json()
    assert show["sheet_key"] == "shows/CLD/localisation/names.csv"
    assert Path(show["profile"]["names_sheet"]).exists()
    assert c.post(f"/api/shows/{s['id']}/sheet", files={"file": ("x.mp4", io.BytesIO(b"1"), "video/mp4")}).status_code == 400


# ---------------- the episode's files ----------------
def test_episode_files_are_uploaded_and_land_in_r2(r2):
    c = r2["client"]
    s = cloud_show(c)
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    r = upload(c, ep["id"], "src_video", "My Source 17.mp4", b"v" * 10000)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["in_cloud"] and body["key"] == "shows/CLD/ep17/input/src_video/My_Source_17.mp4"
    assert r2["objects"][body["key"]] == b"v" * 10000
    a = c.get(f"/api/episodes/{ep['id']}").json()["assets"][0]
    assert a["kind"] == "src_video" and a["origin"] == "r2" and a["size"] == 10000
    assert Path(a["path"]).exists() and "input/src_video" in a["path"]       # cached where the pipeline will read it
    # uploading again replaces it
    assert upload(c, ep["id"], "dub_srt", "x.srt", b"1\n").status_code == 200
    assert len(c.get(f"/api/episodes/{ep['id']}").json()["assets"]) == 2
    assert upload(c, ep["id"], "nonsense", "x.bin").status_code == 400


def test_upload_rejects_the_wrong_file_type_for_the_slot(r2):
    """An mp4 in the audio slot, a wav in the video slot, a video as the script."""
    c = r2["client"]
    s = cloud_show(c)
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    eid = ep["id"]
    assert upload(c, eid, "dub_audio", "11.mp4", b"v").status_code == 400
    assert upload(c, eid, "src_video", "11.mp3", b"a").status_code == 400
    assert upload(c, eid, "dub_script", "11.mp4", b"v").status_code == 400
    assert upload(c, eid, "dub_script", "11.docx", b"d").status_code == 200
    start = c.post(f"/api/episodes/{eid}/files/dub_audio/start", json={"filename": "ep.mp4", "size": 10})
    assert start.status_code == 400
    assert upload(c, eid, "dub_audio", "11.wav", b"a").status_code == 200


def test_a_file_can_be_removed_again(r2):
    c = r2["client"]
    s = cloud_show(c)
    ep = episode_with_files(c, s, env=r2)
    key = [a for a in ep["assets"] if a["kind"] == "src_video"][0]["r2_key"]
    assert c.delete(f"/api/episodes/{ep['id']}/files/src_video").status_code == 200
    assert key not in r2["objects"]
    assert [a["kind"] for a in c.get(f"/api/episodes/{ep['id']}").json()["assets"]] == ["dub_audio"]


def test_direct_upload_url_and_confirm(r2):
    """The deployed version: the browser PUTs straight to R2, then the API checks the object and registers it."""
    c = r2["client"]
    s = cloud_show(c)
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    u = c.post(f"/api/episodes/{ep['id']}/upload-url", json={"kind": "src_video", "filename": "big.mp4"}).json()
    assert "X-Amz-Signature=" in u["url"] and u["key"].endswith("/input/src_video/big.mp4")
    import httpx
    assert httpx.put(u["url"], content=b"v" * 2048, headers=u["headers"]).status_code == 200
    r = c.post(f"/api/episodes/{ep['id']}/files/src_video/confirm", json={"key": u["key"]})
    assert r.status_code == 200 and r.json()["size"] == 2048
    assert c.post(f"/api/episodes/{ep['id']}/files/src_video/confirm", json={"key": "shows/CLD/ep17/input/src_video/none.mp4"}).status_code == 409


def test_files_cannot_change_while_the_episode_is_processing(r2):
    c = r2["client"]
    s = cloud_show(c)
    ep = episode_with_files(c, s, env=r2)
    c.post(f"/api/episodes/{ep['id']}/process")
    r = upload(c, ep["id"], "src_video", "other.mp4")
    assert r.status_code == 409 and "cancel it" in r.json()["detail"]


# ---------------- fetch, publish, cleanup ----------------
def test_the_run_starts_by_fetching_and_ends_by_publishing(r2):
    c = r2["client"]
    s = cloud_show(c)
    ep = episode_with_files(c, s, env=r2)
    steps = [j["step"] for j in c.post(f"/api/episodes/{ep['id']}/process").json()["jobs"]]
    assert steps[0] == "fetch" and steps[-2:] == ["publish", "cleanup"]


def test_fetch_brings_a_deleted_file_back_from_r2(r2):
    """What a worker does: the machine has nothing, the files come down from R2 before the first step."""
    c = r2["client"]
    s = cloud_show(c)
    ep = episode_with_files(c, s, env=r2)
    video = [a for a in ep["assets"] if a["kind"] == "src_video"][0]
    Path(video["path"]).unlink()                                   # as if the worker were a fresh machine
    assert not Path(video["path"]).exists()
    c.post(f"/api/episodes/{ep['id']}/process")
    wait_for(lambda: next((j for j in c.get(f"/api/episodes/{ep['id']}").json()["jobs"]
                           if j["step"] == "fetch" and j["status"] == "done"), None), 30)
    assert Path(video["path"]).read_bytes() == real_media(r2)[0]
    assert "brought down" in next(j for j in c.get(f"/api/episodes/{ep['id']}").json()["jobs"] if j["step"] == "fetch")["message"]


def test_publish_and_cleanup_at_the_end_of_a_run(r2):
    from conftest import press_render
    c = r2["client"]
    s = cloud_show(c)
    ep = episode_with_files(c, s, env=r2)
    c.post(f"/api/episodes/{ep['id']}/process")
    wait_for(lambda: any(j["step"] == "decisions" and j["status"] == "needs_input"
                         for j in c.get(f"/api/episodes/{ep['id']}").json()["jobs"]), 40)
    c.post(f"/api/episodes/{ep['id']}/decisions/auto")
    c.post(f"/api/episodes/{ep['id']}/decisions/import", json={"spec": "C(0)\nC(1)\n"})
    press_render(c, ep["id"], timeout=60)
    wait_for(lambda: c.get(f"/api/episodes/{ep['id']}").json()["status"] == "done", 90)
    jobs = {j["step"]: j for j in c.get(f"/api/episodes/{ep['id']}").json()["jobs"]}
    assert jobs["publish"]["status"] == "done" and jobs["cleanup"]["status"] == "done"
    assert any(k.startswith("shows/CLD/ep17/out/") for k in r2["objects"]), sorted(r2["objects"])
    assert any(k.startswith("shows/CLD/ep17/work/") for k in r2["objects"])
    for a in c.get(f"/api/episodes/{ep['id']}").json()["assets"]:
        if a.get("r2_key"):
            assert not Path(a["path"]).exists()                    # the machine is free for the next episode
    # and the file comes back when the episode is processed again
    assert c.get(f"/api/episodes/{ep['id']}/uploads").json()["uploads"]


# ---------------- the queue ----------------
def test_episodes_wait_their_turn_and_start_by_themselves(r2):
    c = r2["client"]
    s = cloud_show(c)
    first = episode_with_files(c, s, number=17, env=r2)
    second = episode_with_files(c, s, number=18, env=r2)
    r1 = c.post(f"/api/episodes/{first['id']}/process").json()
    assert r1["queued"] is False
    r2_ = c.post(f"/api/episodes/{second['id']}/process").json()
    assert r2_["queued"] is True and r2_["status"] == "queued" and r2_["queue_position"] == 1
    q = c.get("/api/queue").json()
    assert q["max_active"] == 1 and [e["number"] for e in q["waiting"]] == [18]
    assert q["working"] and q["working"][0]["number"] == 17
    # the first episode stops at Check matching (a person is needed): the second starts by itself
    wait_for(lambda: c.get(f"/api/episodes/{second['id']}").json()["status"] == "processing", 60)
    assert c.get(f"/api/episodes/{first['id']}").json()["status"] == "needs_input"
    assert c.get("/api/queue").json()["waiting"] == []


def test_cancelling_a_queued_episode_lets_the_next_one_in(r2):
    c = r2["client"]
    s = cloud_show(c)
    a = episode_with_files(c, s, number=17, env=r2)
    b = episode_with_files(c, s, number=18, env=r2)
    c.post(f"/api/episodes/{a['id']}/process")
    c.post(f"/api/episodes/{b['id']}/process")
    assert c.get(f"/api/episodes/{b['id']}").json()["status"] == "queued"
    c.post(f"/api/episodes/{b['id']}/cancel")
    assert c.get("/api/queue").json()["waiting"] == []


def test_the_queue_is_per_account(r2):
    from test_auth import second_client
    c = r2["client"]
    s = cloud_show(c)
    ep = episode_with_files(c, s, env=r2)
    other, _ = second_client(r2)
    assert other.post(f"/api/episodes/{ep['id']}/process").status_code == 403
    assert other.get("/api/queue").status_code == 200              # everyone may see the machine is busy


# ---------------- big files (GB): the browser sends them in parts ----------------
def test_a_big_file_is_uploaded_in_parts_through_the_api(r2, monkeypatch):
    """What the screen does with a multi-GB video: start -> part, part, … -> finish. Each part is small enough to
    retry on its own, and the file lands both on the machine and in R2."""
    from dubstudio import storage
    monkeypatch.setattr(storage, "PART_BIG", 4096)
    monkeypatch.setattr(storage, "MULTIPART_FROM", 8192)            # "big" for this test
    c = r2["client"]
    s = cloud_show(c)
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    data = bytes(range(256)) * 60                                   # 15 360 bytes -> 4 parts
    start = c.post(f"/api/episodes/{ep['id']}/files/src_video/start",
                   json={"filename": "huge video.mp4", "size": len(data)}).json()
    assert start["parts"] == 4 and start["upload_id"] and start["name"] == "huge_video.mp4"
    etags = []
    for n, at in enumerate(range(0, len(data), start["part_size"]), start=1):
        r = c.put(f"/api/episodes/{ep['id']}/files/src_video/part",
                  params={"part": n, "key": start["key"], "upload_id": start["upload_id"], "name": start["name"]},
                  content=data[at:at + start["part_size"]])
        assert r.status_code == 200, r.text
        etags.append([n, r.json()["etag"]])
    out = c.post(f"/api/episodes/{ep['id']}/files/src_video/finish",
                 json={"key": start["key"], "upload_id": start["upload_id"], "name": start["name"], "etags": etags}).json()
    assert out["size"] == len(data) and out["in_cloud"]
    assert r2["objects"][start["key"]] == data
    a = [x for x in c.get(f"/api/episodes/{ep['id']}").json()["assets"] if x["kind"] == "src_video"][0]
    assert Path(a["path"]).read_bytes() == data                     # also on the machine, ready for the pipeline


def test_a_big_upload_can_be_given_up(r2, monkeypatch):
    from dubstudio import storage
    monkeypatch.setattr(storage, "PART_BIG", 4096)
    monkeypatch.setattr(storage, "MULTIPART_FROM", 8192)
    c = r2["client"]
    s = cloud_show(c)
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    start = c.post(f"/api/episodes/{ep['id']}/files/dub_audio/start", json={"filename": "a.mp3", "size": 9000}).json()
    c.put(f"/api/episodes/{ep['id']}/files/dub_audio/part",
          params={"part": 1, "key": start["key"], "upload_id": start["upload_id"], "name": start["name"]}, content=b"x" * 4096)
    assert c.post(f"/api/episodes/{ep['id']}/files/dub_audio/abort",
                  json={"key": start["key"], "upload_id": start["upload_id"]}).status_code == 200
    assert start["key"] not in r2["objects"]                        # nothing half-finished is left behind


def test_direct_mode_signs_a_part_link_for_the_browser(r2, monkeypatch):
    """UPLOAD_MODE=direct: the API only signs links, the browser sends the bytes to R2 itself (needs the bucket's
    CORS rule, which exposes the part's ETag)."""
    monkeypatch.setenv("UPLOAD_MODE", "direct")
    c = r2["client"]
    s = cloud_show(c)
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    start = c.post(f"/api/episodes/{ep['id']}/files/src_video/start", json={"filename": "v.mp4", "size": 2048}).json()
    assert start["mode"] == "direct" and start["upload_id"]
    u = c.post(f"/api/episodes/{ep['id']}/files/src_video/part-url",
               json={"key": start["key"], "upload_id": start["upload_id"], "part": 1}).json()
    import httpx
    r = httpx.put(u["url"], content=b"v" * 2048)                     # this is what the browser does
    assert r.status_code == 200
    etag = (r.headers.get("etag") or "").strip('"')
    out = c.post(f"/api/episodes/{ep['id']}/files/src_video/finish",
                 json={"key": start["key"], "upload_id": start["upload_id"], "name": start["name"],
                       "etags": [[1, etag]]}).json()
    assert out["size"] == 2048 and r2["objects"][start["key"]] == b"v" * 2048
    a = [x for x in c.get(f"/api/episodes/{ep['id']}").json()["assets"] if x["kind"] == "src_video"][0]
    assert a["r2_key"] == start["key"]          # not on this machine yet: the `fetch` step brings it down


def test_small_files_still_go_in_one_request(r2):
    c = r2["client"]
    s = cloud_show(c)
    ep = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    start = c.post(f"/api/episodes/{ep['id']}/files/dub_audio/start", json={"filename": "a.mp3", "size": 5000}).json()
    assert start["mode"] == "single" and start["upload_id"] is None   # no multipart started for a small file


# ---------------- steps on rented machines (docs/09) ----------------
def test_the_lip_scan_can_run_on_the_cloud_in_pieces(r2, monkeypatch, tmp_path):
    """CLOUD_STEPS=lip_gpu sends the lip scan to the serverless endpoint: the episode is split into pieces, each
    piece comes back as a tar of range files, and they land in <work>/lips_nets exactly as the local step writes
    them. No key goes to the worker -- only signed links."""
    import tarfile, json as js
    from dubstudio import cloud, storage
    c = r2["client"]
    monkeypatch.setenv("RUNPOD_API_KEY", "rp-test"); monkeypatch.setenv("RUNPOD_CPU_ENDPOINT", "ep-test")
    monkeypatch.setenv("CLOUD_STEPS", "lip_gpu"); monkeypatch.setenv("CLOUD_PIECES", "4")
    assert cloud.enabled_for("lip_gpu") and cloud.pieces() == 4

    sent = []
    def fake_call(path, body=None, timeout=60, kind="cpu"):
        if path == "/run":
            sent.append(body["input"])
            # the "worker": make the range file this piece would produce and PUT it to the signed link
            d = tmp_path / f"w{len(sent)}"; (d / "lips_nets").mkdir(parents=True)
            for s, e in body["input"]["args"]["ranges"]:       # one job can carry several of the 15 s pieces
                (d / "lips_nets" / f"range_{int(s)}_{int(e)}.json").write_text(js.dumps({"tracks": [], "piece": [s, e]}))
            tar = d / "out.tar.gz"
            with tarfile.open(tar, "w:gz") as t:
                t.add(d / "lips_nets", arcname="lips_nets")
            import httpx
            httpx.put(body["input"]["outputs"]["result"], content=tar.read_bytes())
            return {"id": f"job{len(sent)}"}
        if path.startswith("/status/"):
            return {"status": "COMPLETED", "executionTime": 1000, "output": {"ok": True}}
        return {"workers": {"running": 2}}
    monkeypatch.setattr(cloud, "_call", fake_call)

    s = cloud_show(c)
    ep = episode_with_files(c, s, env=r2)                   # a show + episode with its files
    import dubstudio.main as main
    show_row = main.db().one("SELECT * FROM shows WHERE id=?", (s["id"],))
    ep_db = main.db().one("SELECT * FROM episodes WHERE id=?", (ep["id"],))
    out = cloud.lip_scan(main.db(), show_row, ep_db, duration_s=60.0, log=lambda m: None)

    # 60 s of video at the pipeline's own 15 s piece size = 4 range files, shared across the machines
    assert out["files"] == 4
    nets = Path(ep_db["work_dir"]) / "lips_nets"
    assert sorted(p.name for p in nets.glob("range_*.json")) == ["range_0_15.json", "range_15_30.json",
                                                                 "range_30_45.json", "range_45_60.json"]
    # lipgpu writes this next to the pieces and the two steps after the scan read it, so the cloud must too
    assert js.loads((nets / "ranges.json").read_text()) == [[0, 15], [15, 30], [30, 45], [45, 60]]
    # every job carried signed links only -- never a key
    for job in sent:
        assert job["step"] == "lip_scan" and "X-Amz-Signature=" in job["inputs"]["video"]
        assert "X-Amz-Signature=" in job["outputs"]["result"]
        assert "secret" not in js.dumps(job).lower()
    # and the source video was put in R2 so a worker can read it
    assert any("input/src_video" in k for k in r2["objects"])


def test_without_the_setting_the_lip_scan_stays_on_this_machine(env, monkeypatch):
    from dubstudio import cloud
    from dubstudio.pipeline.steps import plan
    from dubstudio import presets
    monkeypatch.delenv("CLOUD_STEPS", raising=False)
    assert cloud.enabled_for("lip_gpu") is False
    show = {"code": "TGS", "profile": presets.preset("TGS")}
    ep = {"number": 34, "work_dir": "/w", "output_dir": "/o"}
    _, st = plan(show, ep, {"src_video": "/v.mp4", "dub_audio": "/a.mp3"})
    lip = next(s for s in st if s["step"] == "lip_gpu")
    assert lip["cmd"] and lip["resource"] == "gpu" and lip["fn"] is None     # the local command, as before
