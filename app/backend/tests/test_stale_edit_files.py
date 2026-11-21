"""A new groups run must replace leftover grouping / editor files, not keep the previous cut."""
import json
from pathlib import Path

from dubstudio.runner import drop_stale_edit_files, snapshot_latest_groups


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


def test_snapshot_skips_editor_save(tmp_path):
    W = Path(tmp_path)
    _write(W / "manifest_groups.json", {"dub_studio": {"version": 3}, "segments": [{"it_idx": 1}]})
    _write(W / "manifest_groups.grouping.json", {"segments": [{"it_idx": 18}]})
    assert snapshot_latest_groups(W) is False
    kept = json.loads((W / "manifest_groups.grouping.json").read_text())
    assert kept["segments"][0]["it_idx"] == 18
