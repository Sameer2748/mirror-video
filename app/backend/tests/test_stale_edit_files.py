"""A new groups run must replace leftover grouping / editor files, not keep the previous cut."""
import json
from pathlib import Path

from dubstudio.runner import clear_output_dir, drop_stale_edit_files, drop_previous_render, reset_work_for_new_run, snapshot_latest_groups
from dubstudio.storage import latest_uploads


def _write(p, body):
    Path(p).write_text(json.dumps(body), encoding="utf-8")


def test_drop_stale_edit_files_removes_previous_cut(tmp_path):
    W = Path(tmp_path)
    (W / "scene_slowfix").mkdir()
    for name in ("manifest_groups.grouping.json", "manifest_groups.auto.json",
                 "manifest_groups.pipeline.json", "timeline_draft.json", "review_ok.json"):
        (W / name).write_text("{}")
    (W / "scene_slowfix" / "manifest_slowfix.json").write_text("{}")
    (W / "manifest_groups.json").write_text(json.dumps({"segments": []}))
    dropped = drop_stale_edit_files(W)
    assert "manifest_groups.grouping.json" in dropped
    assert "timeline_draft.json" in dropped
    assert "manifest_groups.auto.json" in dropped
    assert not (W / "manifest_groups.grouping.json").exists()
    assert (W / "manifest_groups.json").exists()
    assert not (W / "scene_slowfix" / "manifest_slowfix.json").exists()


def test_snapshot_overwrites_old_grouping(tmp_path):
    W = Path(tmp_path)
    _write(W / "manifest_groups.grouping.json", {"segments": [{"it_idx": 99, "note": "old evened group"}]})
    _write(W / "manifest_groups.json", {"segments": [{"it_idx": 18, "note": "new span"}]})
    assert snapshot_latest_groups(W) is True
    kept = json.loads((W / "manifest_groups.grouping.json").read_text())
    assert kept["segments"][0]["it_idx"] == 18
    assert "span" in kept["segments"][0]["note"]


def test_process_again_keeps_only_input(tmp_path):
    W = Path(tmp_path)
    (W / "input").mkdir()
    (W / "input" / "src.mp4").write_text("vid")
    (W / "segs_groups").mkdir()
    (W / "segs_groups" / "part.mp4").write_text("old")
    (W / "lipsync.json").write_text("{}")
    (W / "manifest_groups.json").write_text("{}")
    dropped = reset_work_for_new_run(W)
    assert "lipsync.json" in dropped and "segs_groups" in dropped
    assert (W / "input" / "src.mp4").exists()
    assert not (W / "lipsync.json").exists()
    assert not (W / "segs_groups").exists()


def test_render_drops_previous_segment_cache(tmp_path):
    W = Path(tmp_path); O = tmp_path / "out"; O.mkdir()
    (W / "segs_groups").mkdir()
    (W / "video_groups_nosubs.mp4").write_text("old picture")
    (O / "SHOW_EP1_before.mp4").write_text("old before")
    (O / "SHOW_EP1_timeline_v2.xml").write_text("<xml/>")
    dropped = drop_previous_render(W, O)
    assert "segs_groups" in dropped and "video_groups_nosubs.mp4" in dropped
    assert "SHOW_EP1_before.mp4" in dropped
    assert "SHOW_EP1_timeline_v2.xml" in dropped
    assert not (W / "video_groups_nosubs.mp4").exists()
    assert not (O / "SHOW_EP1_timeline_v2.xml").exists()


def test_process_again_clears_previous_output_files(tmp_path):
    O = tmp_path / "out"; O.mkdir()
    (O / "SHOW_EP1_final_en_subs.mp4").write_text("old")
    (O / "SHOW_EP1_timeline_v2.xml").write_text("<xml/>")
    dropped = clear_output_dir(O)
    assert "SHOW_EP1_final_en_subs.mp4" in dropped
    assert list(O.iterdir()) == []


def test_deliverables_list_is_latest_kind_only():
    rows = [
        {"id": 9, "kind": "premiere", "name": "v3.xml"},
        {"id": 8, "kind": "en_subs", "name": "en.mp4"},
        {"id": 4, "kind": "premiere", "name": "v2.xml"},
        {"id": 3, "kind": "en_subs", "name": "en.mp4"},
        {"id": 2, "kind": "scene_sheet", "name": "scene.xlsx"},
    ]
    got = latest_uploads(rows)
    assert [r["name"] for r in got] == ["v3.xml", "en.mp4", "scene.xlsx"]


def test_snapshot_skips_editor_save(tmp_path):
    W = Path(tmp_path)
    _write(W / "manifest_groups.json", {"dub_studio": {"version": 3}, "segments": [{"it_idx": 1}]})
    _write(W / "manifest_groups.grouping.json", {"segments": [{"it_idx": 18}]})
    assert snapshot_latest_groups(W) is False
    kept = json.loads((W / "manifest_groups.grouping.json").read_text())
    assert kept["segments"][0]["it_idx"] == 18
