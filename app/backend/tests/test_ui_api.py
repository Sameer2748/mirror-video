"""API pieces the screens use (milestone 1.4): own output folder, rows for Check matching, final check, the UI."""
import time
from pathlib import Path
from conftest import wait_for, press_render
from test_m1 import make_show


def test_output_folder_rows_and_final_check(env):
    c = env["client"]; s = make_show(c, env["show"])
    (env["show"] / "outputs" / "ep17").mkdir(parents=True)
    (env["show"] / "outputs" / "ep17" / "old.mp4").write_text("x")
    e = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 17}).json()
    assert e["output_existing"] == ["old.mp4"]                       # the screen warns before replacing files
    e = c.patch(f"/api/episodes/{e['id']}", json={"output_dir": "outputs/ep17_studio"}).json()
    assert e["output_dir"] == str(env["show"] / "outputs" / "ep17_studio") and e["output_existing"] == []
    e2 = c.post(f"/api/shows/{s['id']}/episodes", json={"number": 18, "output_dir": "/tmp/x_ep18"}).json()
    assert e2["output_dir"] == "/tmp/x_ep18"
    assert c.get(f"/api/episodes/{e['id']}/rows").status_code == 409  # before the matching ran

    c.post(f"/api/episodes/{e['id']}/process")
    wait_for(lambda: c.get(f"/api/episodes/{e['id']}").json()["status"] == "needs_input")
    assert c.patch(f"/api/episodes/{e['id']}", json={"output_dir": "elsewhere"}).status_code == 409
    rows = c.get(f"/api/episodes/{e['id']}/rows").json()
    assert [r["text"] for r in rows["dub"]] == ["Ciao", "Come stai", "Bene"] and len(rows["src"]) == 2
    st = c.get(f"/api/episodes/{e['id']}/decisions").json()
    assert st["flagged"][0]["suggested_src"] == 1 and st["ai_last"] is None and st["decided"] is False
    # the spec the Check matching screen writes: confirm row 1, assign row 2 to S0
    spec = '# Step 2 decisions made in Dub Studio\nC(1,"automatic match accepted")\nA(2,"Bene",0,"Hello","editor")\n'
    r = c.post(f"/api/episodes/{e['id']}/decisions/import", files={"file": ("spec.py", spec.encode(), "text/plain")})
    assert r.status_code == 200, r.text
    press_render(c, e["id"])
    ep = wait_for(lambda: (lambda x: x if x["status"] == "done" else None)(c.get(f"/api/episodes/{e['id']}").json()))
    chk = next(j for j in ep["jobs"] if j["step"] == "check")
    assert chk["check_passed"] is True
    assert (Path(ep["output_dir"]) / "SHOW_EP17_IT_final_clean.mp4").exists()
    assert not (env["show"] / "outputs" / "ep17" / "SHOW_EP17_IT_final_clean.mp4").exists()


def test_spa_head_and_reveal_missing(env):
    c = env["client"]
    assert c.post("/api/reveal", json={"path": "/no/such/file"}).status_code == 404
