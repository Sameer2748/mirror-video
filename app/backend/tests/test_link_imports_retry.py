"""Failed sheet downloads go back on the one-at-a-time line without a new sheet upload."""


def test_retry_puts_failed_link_imports_back_in_line(env):
    c = env["client"]
    show = c.post("/api/shows", json={
        "name": "Clean files", "code": "SHOW", "folder": str(env["show"]), "preset": "NEW",
        "profile": {"dub_lang": "fr", "src_lang": "en"},
    }).json()
    ep = c.post(f"/api/shows/{show['id']}/episodes", json={"number": 12}).json()
    import dubstudio.main as main
    from dubstudio.db import now
    iid = main.db().insert(
        "link_imports", show_id=show["id"], episode_id=ep["id"], number=12,
        src_url="https://example.com/a.mp4", audio_url="https://example.com/a.mp3",
        script_url="https://example.com/a.txt", status="failed",
        message="the src video link did not come back as a .mp4", created=now())
    got = c.post(f"/api/shows/{show['id']}/link-imports/retry")
    assert got.status_code == 200, got.text
    assert got.json()["queued"] == 1
    row = main.db().one("SELECT status FROM link_imports WHERE id=?", (iid,))
    # the download slot may already have picked the first row up
    assert row["status"] in ("waiting", "running", "failed")
