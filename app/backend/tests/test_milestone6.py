"""Milestone 6: renders in a queue, the deliverables of an episode, the Premiere (FCP7) XML export."""
import json, xml.etree.ElementTree as ET
from pathlib import Path
from conftest import wait_for
from test_m1 import make_show
from test_editor import write_manifest
from dubstudio import deliver, timeline as tlm


def test_premiere_xml(tmp_path):
    w = tmp_path / "w"
    write_manifest(w, [(0, 2000, 10000, 12000), (2000, 3000, 20000, 20500), (3000, 4500, 5000, 7400)], 4500)
    tl = tlm.build_v1(w); tl["clips"][1]["locked"] = True
    probe = {"duration": 60.0, "video": {"width": 1080, "height": 1080, "fps": 29.97}}
    xml = deliver.premiere_xml(tl, "SHOW EP1 v3", "/Videos/My Show/ep 1.mp4", "/Audio/ep1.mp3", probe, 4.5,
                               [{"t_ms": 2500, "text": "Viola smiling", "source": "QC", "resolved": 0}])
    root = ET.fromstring(xml.split("\n", 2)[2])                     # after the XML declaration and DOCTYPE
    seq = root.find("sequence")
    assert seq.findtext("rate/timebase") == "30" and seq.findtext("rate/ntsc") == "TRUE"
    assert seq.findtext("duration") == str(round(4.5 * 30000 / 1001))
    clips = seq.findall("media/video/track/clipitem")
    assert len(clips) == 3
    starts = [(int(c.findtext("start")), int(c.findtext("end"))) for c in clips]
    assert starts[0][0] == 0 and all(a[1] == b[0] for a, b in zip(starts, starts[1:]))       # no holes, no overlaps
    for c in clips:                                                                           # out - in = end - start
        assert int(c.findtext("out")) - int(c.findtext("in")) == int(c.findtext("end")) - int(c.findtext("start"))
    speeds = [c.find("filter/effect/parameter[parameterid='speed']/value") for c in clips]
    assert speeds[0] is None and abs(float(speeds[1].text) - 50) < 0.01 and abs(float(speeds[2].text) - 160) < 0.01
    assert clips[0].find("file/pathurl").text == "file://localhost/Videos/My%20Show/ep%201.mp4"
    assert clips[1].find("file").attrib == {"id": "file-src"} and clips[1].find("labels/label2").text == "Rose"
    assert int(clips[1].findtext("in")) == round(round(20 * 30000 / 1001) / 0.5)              # retimed in-point
    assert seq.find("marker/name").text == "QC Viola smiling" and seq.findtext("marker/in") == str(round(2.5 * 30000 / 1001))
    assert seq.find("media/audio/track/clipitem/file/pathurl").text == "file://localhost/Audio/ep1.mp3"


def test_render_queue_deliverables_and_export(env):
    c = env["client"]; s = make_show(c, env["show"])
    eid = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()["id"]
    c.post(f"/api/episodes/{eid}/process")
    wait_for(lambda: any(j["step"] == "decisions" and j["status"] == "needs_input" for j in c.get(f"/api/episodes/{eid}").json()["jobs"]))
    c.post(f"/api/episodes/{eid}/decisions/auto")
    wait_for(lambda: c.get(f"/api/episodes/{eid}").json()["status"] == "review")
    d = c.get(f"/api/episodes/{eid}/deliverables").json()
    assert d["render"] is None and [f["kind"] for f in d["files"]][:5] == ["clean", "dub_subs", "en_subs", "en_srt", "mapping"]
    tl = c.get(f"/api/episodes/{eid}/timeline").json()["timeline"]
    r = c.post(f"/api/episodes/{eid}/timeline/render", json={**tl, "note": "first cut"}).json()
    q = c.get("/api/renders").json()
    assert q[0]["id"] == r["render"] and q[0]["number"] == 17 and q[0]["code"] == "SHOW" and q[0]["status"] in ("waiting", "running", "done")
    done = wait_for(lambda: (lambda x: x if x["status"] == "done" else None)(c.get("/api/renders").json()[0]))
    assert done["check_result"]["passed"] is True and done["check_result"]["note"] == "first cut" and done["finished"]
    assert "render" in done["check_result"]["seconds"]
    d = c.get(f"/api/episodes/{eid}/deliverables").json()
    have = {f["kind"]: f for f in d["files"]}
    assert all(have[k]["exists"] for k in ("clean", "dub_subs", "en_subs", "en_srt", "mapping"))
    assert have["premiere"]["name"] == "SHOW_EP17_IT_timeline_v1.xml" and d["rendered_version"] == 1 and not d["unrendered"]
    # a second render replaces nothing finished; an unfinished one is marked replaced
    r2 = c.post(f"/api/episodes/{eid}/timeline/render", json=tl).json()
    wait_for(lambda: c.get("/api/renders").json()[0]["status"] == "done")
    assert [x["status"] for x in c.get("/api/renders").json()[:2]] == ["done", "done"]
    # export on demand: a saved version, as a download
    x = c.get(f"/api/episodes/{eid}/export/premiere?version=1")
    assert x.status_code == 200 and x.text.count("<clipitem") == len(tl["clips"]) + 1
    assert c.get(f"/api/episodes/{eid}/export/premiere?version=99").status_code == 404


def test_the_finished_files_pack_into_one_folder(tmp_path):
    a = tmp_path / "clean.mp4"; b = tmp_path / "notes.md"
    a.write_bytes(b"video"); b.write_text("notes")
    files = [
        {"kind": "clean", "name": "clean.mp4", "path": str(a), "exists": True},
        {"kind": "notes", "name": "notes.md", "path": str(b), "exists": True},
        {"kind": "missing", "name": "nope.mp4", "path": str(tmp_path / "nope.mp4"), "exists": False},
    ]
    dest = tmp_path / "out.zip"
    assert deliver.write_zip(files, dest, "TGS_ep34") == 2
    import zipfile
    with zipfile.ZipFile(dest) as z:
        assert sorted(z.namelist()) == ["TGS_ep34/clean.mp4", "TGS_ep34/notes.md"]
        assert z.read("TGS_ep34/clean.mp4") == b"video"


def test_replaced_render(tmp_path):
    from dubstudio.db import DB
    db = DB(tmp_path / "t.db")
    db.ex("INSERT INTO shows (name, code, folder, profile) VALUES ('a','A','/x','{}')")
    db.ex("INSERT INTO episodes (show_id, number) VALUES (1, 1)")
    a = deliver.start_render(db, 1, 1); b = deliver.start_render(db, 1, 2)
    assert db.one("SELECT status FROM renders WHERE id=?", (a,))["status"] == "replaced"
    assert db.one("SELECT status FROM renders WHERE id=?", (b,))["status"] == "waiting"
