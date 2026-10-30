"""Two video tracks and real trims: V2 plays over V1, gaps are black and block the render, the render gets what is seen."""
import json
from pathlib import Path
from test_editor import write_manifest
from dubstudio import timeline as tlm


def tl3(tmp_path):
    w = tmp_path / "w"; write_manifest(w, [(0, 2000, 10000, 12000), (2000, 4000, 20000, 22000), (4000, 6000, 30000, 32000)], 6000)
    return w, tlm.build_v1(w)


def test_v2_covers_v1_and_flattens(tmp_path):
    w, tl = tl3(tmp_path)
    assert tl["v2"] == [] and [p["id"] for p in tlm.flatten(tl)] == ["c0", "c1", "c2"]
    tl["v2"] = [dict(id="v2a", row=None, t0=1500, t1=2500, s0=50000, s1=51000, dub_text="", src_text="x")]
    f = tlm.flatten(tl)
    assert [(p["id"], p["t0"], p["t1"]) for p in f] == [("c0", 0, 1500), ("v2a", 1500, 2500), ("c1", 2500, 4000), ("c2", 4000, 6000)]
    c1 = f[2]; assert c1["s0"] == 20500 and c1["s1"] == 22000                     # V1 resumes at the right frame
    assert tlm.validate(tl) == []
    m = tlm.to_manifest(tl, w, 1)
    assert [(s["it_start_ms"], s["us_start_ms"]) for s in m["segments"]] == [(0, 10000), (1500, 50000), (2500, 20500), (4000, 30000)]
    fl = tlm.flags(tl, tlm.context(w), {})
    assert any(x["kind"] == "v2" for x in fl["v2a"])
    tl["v2"][0].update(t0=2000, t1=4000)                                          # c1 fully covered
    assert [x["kind"] for x in tlm.flags(tl, tlm.context(w), {})["c1"]] == ["hidden"]
    tl["v2"].append(dict(id="v2b", t0=3000, t1=3500, s0=60000, s1=60500))
    assert any("V2" in p and "overlap" in p for p in tlm.validate(tl))


def test_gaps_are_black_and_refused(tmp_path):
    w, tl = tl3(tmp_path)
    tl["clips"][1].update(t1=3000, s1=21000)                                      # trimmed in: a 1-s gap
    g = tlm.gaps(tl); assert [(x["t0"], x["t1"]) for x in g] == [(3000, 4000)]
    assert any("gap (black screen) at 00:03.0" in p for p in tlm.validate(tl))
    tl["v2"] = [dict(id="v2a", t0=2900, t1=4100, s0=70000, s1=71200)]              # a V2 clip fills it
    assert tlm.gaps(tl) == [] and tlm.validate(tl) == []
    tl["v2"] = []; tl["clips"][0].update(t0=300, s0=10300)                        # a gap at the very start
    assert tlm.gaps(tl)[0]["t0"] == 0


def test_cuts_and_transitions_follow_what_is_seen(tmp_path):
    w, tl = tl3(tmp_path)
    tl["v2"] = [dict(id="v2a", t0=1000, t1=1800, s0=50000, s1=50800, tin="glow")]
    assert tlm.cuts(tl) == ["v2a", "c1", "c2"]
    tlm.write_transition_plan(w, tl, "none")
    assert json.loads((w / tlm.TPLAN).read_text())["cuts"] == {"1000": "glow"}


def test_premiere_has_two_tracks(tmp_path):
    import xml.etree.ElementTree as ET
    from dubstudio import deliver
    w, tl = tl3(tmp_path)
    tl["clips"][1].update(t1=3000, s1=21000)                                      # V1 keeps its gap in the XML
    tl["v2"] = [dict(id="v2a", t0=2900, t1=4100, s0=70000, s1=71200)]
    xml = deliver.premiere_xml(tl, "T", "/v.mp4", "/a.mp3", {"duration": 120, "video": {"fps": 25, "width": 1080, "height": 1080}})
    seq = ET.fromstring(xml.split("\n", 2)[2]).find("sequence")
    tracks = seq.findall("media/video/track")
    assert len(tracks) == 2 and len(tracks[0].findall("clipitem")) == 3 and len(tracks[1].findall("clipitem")) == 1
    v1 = [(int(c.findtext("start")), int(c.findtext("end"))) for c in tracks[0].findall("clipitem")]
    assert v1[1] == (50, 75) and v1[2][0] == 100                                  # the gap 3.0–4.0 s (25 fps)
    assert tracks[1].find("clipitem/file").attrib == {"id": "file-src"}


def test_hidden_rows(tmp_path):
    w, tl = tl3(tmp_path)
    tl["v2"] = [dict(id="v2a", t0=1500, t1=2500, s0=50000, s1=51000)]
    tl["tracks"] = {"v2": {"hidden": True}}                                       # V2's eye off: V1 plays and renders
    assert [p["id"] for p in tlm.flatten(tl)] == ["c0", "c1", "c2"] and tlm.validate(tl) == []
    assert [s["us_start_ms"] for s in tlm.to_manifest(tl, w, 1)["segments"]] == [10000, 20000, 30000]
    tl["tracks"] = {"v1": {"hidden": True}}
    assert any("V1 is hidden" in p for p in tlm.validate(tl))


def test_not_found_rows_start_as_empty_gaps(tmp_path):
    """A row no footage was found for (a gap decision, also after Claude's second look) is a blank gap on V1, not
    the neighbour's clip stretched over it; Render waits until the editor fills it."""
    import json
    from test_editor import write_manifest
    w = tmp_path / "w"
    # the pipeline stretched clip 0 over row 1 (2000-3500) because row 1 had no footage
    write_manifest(w, [(0, 3500, 10000, 13500), (3500, 6000, 20000, 22500)], 6000)
    (w / "prep.json").write_text(json.dumps({"dub": [{"text": "uno", "start_ms": 0, "end_ms": 1900},
                                                     {"text": "due", "start_ms": 2000, "end_ms": 3400},
                                                     {"text": "tre", "start_ms": 3500, "end_ms": 5900}], "src": [], "auto": {}}))
    (w / "decisions.json").write_text(json.dumps({"gap": {"1": "not found by Claude"}}))
    tl = tlm.build_v1(w)
    assert [(c["id"], c["t0"], c["t1"]) for c in tl["clips"]] == [("c0", 0, 2000), ("c0b", 3400, 3500), ("c1", 3500, 6000)]
    assert tl["clips"][1]["s0"] == 13400                       # same footage, same speed: only the hole is gone
    assert tl["holes"] == [dict(row=1, t0=2000.0, t1=3400.0, text="due")]
    probs = tlm.validate(tl)
    assert len(probs) == 1 and "IT#1" in probs[0] and "gap" in probs[0]


def test_imported_clip_bin_timeline_render_and_premiere(env, tmp_path):
    """The editor imports a clip for the episode, puts it in a gap: the edit list names its file for the render,
    the source-video checks leave it alone, and the Premiere XML points at it."""
    import subprocess, time
    import xml.etree.ElementTree as ET
    from dubstudio import deliver, imports
    from conftest import wait_for
    from test_m1 import make_show
    clip = tmp_path / "broll.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=25:duration=4", str(clip)], check=True)
    c = env["client"]; s = make_show(c, env["show"])
    e = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    with open(clip, "rb") as f:
        it = c.post(f"/api/episodes/{e['id']}/imports", files={"file": ("broll.mov", f, "video/quicktime")}).json()
    assert it["name"] == "broll.mov" and abs(it["duration_ms"] - 4000) < 100 and clip.exists()
    assert c.post(f"/api/episodes/{e['id']}/imports/path", json={"path": str(tmp_path / "nope.mov")}).status_code == 400
    b = c.get(f"/api/episodes/{e['id']}/bin").json()
    assert [x["id"] for x in b["imports"]] == [it["id"]] and any(x["kind"] == "src_video" for x in b["files"])
    wait_for(lambda: imports.get(e["work_dir"], it["id"])["preview"] != "making")
    assert c.get(f"/api/episodes/{e['id']}/imports/{it['id']}/media").status_code == 200
    # on the timeline (in a gap)
    w, tl = tl3(tmp_path)
    (w / "imports").mkdir(); (w / "imports" / "imports.json").write_text(json.dumps([dict(it, file=it["file"])]))
    (w / "imports" / it["file"]).write_bytes(b"x")
    tl["clips"][1].update(t1=3000, s1=21000)
    tl["v2"] = [dict(id="v2a", t0=3000, t1=4000, s0=500, s1=1500, media=it["id"], src_text="broll.mov", row=None)]
    assert tlm.validate(tl, 3_600_000, {it["id"]: 4000}) == []
    tl["v2"][0]["s1"] = 4500
    assert any("outside" in p for p in tlm.validate(tl, 3_600_000, {it["id"]: 4000}))
    tl["v2"][0]["s1"] = 1500
    fl = tlm.flags(tl, tlm.context(w), {})["v2a"]
    assert [x["kind"] for x in fl] == ["imported", "v2"]
    m = tlm.to_manifest(tl, w, 1)
    assert m["segments"][2]["video"].endswith(it["file"]) and "video" not in m["segments"][1]
    xml = deliver.premiere_xml(tl, "T", "/v.mp4", "/a.mp3", {"duration": 120, "video": {"fps": 25, "width": 1080, "height": 1080}},
                               media={it["id"]: dict(it, path="/x/broll.mov")})
    assert "broll.mov" in xml and ET.fromstring(xml.split("\n", 2)[2]) is not None
    # it can be removed while it is not on the episode's timeline
    assert c.delete(f"/api/episodes/{e['id']}/imports/{it['id']}").json() == {"ok": True}
    assert c.get(f"/api/episodes/{e['id']}/bin").json()["imports"] == []


def test_dub_preview_is_an_exact_aac_copy(env):
    """The editor plays the dub from an AAC copy (browsers play some MP3s faster than they report): same length,
    made once, remade when the audio file changes."""
    import subprocess, time
    from test_m1 import make_show
    c = env["client"]; s = make_show(c, env["show"])
    e = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    r = c.get(f"/api/episodes/{e['id']}/media/dub_preview")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mp4"
    out = Path(e["work_dir"]) / tlm.DUB_PREVIEW
    dur = lambda p: float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                                         capture_output=True, text=True).stdout)
    src = next(a["path"] for a in e["assets"] if a["kind"] == "dub_audio")
    assert abs(dur(out) - dur(src)) < 0.05
    m1 = out.stat().st_mtime; c.get(f"/api/episodes/{e['id']}/media/dub_preview")
    assert out.stat().st_mtime == m1                                       # made once
    time.sleep(0.05); Path(src).touch()
    c.get(f"/api/episodes/{e['id']}/media/dub_preview")
    assert out.stat().st_mtime > m1                                        # the audio changed: made again
