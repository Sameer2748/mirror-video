from dubstudio.visits import label_for_step, live_for_jobs


def test_matching_visit_label_uses_the_packed_name():
    jobs = [
        {"step": "mksrt_dub", "status": "running", "params": {"label": "Make IT SRT (Whisper + script)"},
         "message": "script through matching on one rented machine"},
        {"step": "analyze", "status": "waiting", "params": {"label": "Matching"}},
    ]
    live = live_for_jobs(jobs)
    assert live["visit"] == "match"
    assert live["label"].startswith("Whisper → matching")
    assert "Make IT SRT" in live["label"]
    assert "rented machine" in live["message"]


def test_cut_does_not_look_like_captions():
    assert "Sheet → library" in label_for_step("captions", "Read the source captions")
