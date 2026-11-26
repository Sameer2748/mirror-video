from dubstudio.visits import display_status, job_on_cpu, label_for_step, live_for_jobs


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


def test_outer_chip_is_waiting_when_the_box_is_not_ours_yet():
    jobs = [{"step": "render", "status": "running", "message": "render tail: waiting for a free machine"}]
    assert job_on_cpu(jobs[0]) is False
    assert display_status("processing", jobs) == "waiting"


def test_outer_chip_is_failed_when_render_died_and_transitions_are_only_waiting():
    jobs = [
        {"step": "render", "status": "failed", "message": "HTTP 404"},
        {"step": "transitions", "status": "waiting"},
    ]
    assert display_status("processing", jobs) == "failed"


def test_outer_chip_is_processing_when_a_cpu_is_packing():
    jobs = [{"step": "render", "status": "running", "message": "sending the files to a rented machine"}]
    assert display_status("processing", jobs) == "processing"
