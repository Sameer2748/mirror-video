"""Short leftover = one B-roll or none. Long leftover = several ranked shots."""
from pathlib import Path

from dubstudio.pipeline.broll_policy import (
    LONG_BROLL_S, hole_broll_s, is_long_broll_hole, max_broll_shots,
    park_scene_edit_cache)


def test_two_second_hole_is_one_shot_or_none():
    dub, src = 4.0, 1.6  # 4 - 1.6/0.8 = 2.0 s of B-roll to reach 0.80×
    assert abs(hole_broll_s(dub, src) - 2.0) < 1e-6
    assert not is_long_broll_hole(dub, src)
    assert max_broll_shots(dub, src) == 1


def test_twenty_two_second_freeze_may_use_several_shots():
    dub, src = 22.2, 0.64  # IMTS FR-DE EP30 IT#107 class
    assert hole_broll_s(dub, src) >= LONG_BROLL_S
    assert is_long_broll_hole(dub, src)
    assert max_broll_shots(dub, src) == 3


def test_eight_second_gap_is_the_long_cutoff():
    assert not is_long_broll_hole(10.0, 2.0)  # 10 - 2.5 = 7.5
    assert is_long_broll_hole(10.0, 1.6)  # 10 - 2.0 = 8.0


def test_scene_redo_parks_slowfix_plan_not_only_par_cache(tmp_path):
    w = Path(tmp_path)
    (w / "scene").mkdir()
    (w / "scene_slowfix").mkdir()
    (w / "scene" / "scene_manifest.par_cache.json").write_text("{}", encoding="utf-8")
    (w / "scene_slowfix" / "broll_plan_editor.json").write_text('{"19": {}}', encoding="utf-8")
    (w / "scene_slowfix" / "decisions.json").write_text("[]", encoding="utf-8")
    parked = park_scene_edit_cache(w)
    assert not (w / "scene_slowfix" / "broll_plan_editor.json").exists()
    assert not (w / "scene_slowfix" / "decisions.json").exists()
    assert not (w / "scene" / "scene_manifest.par_cache.json").exists()
    assert len(parked) == 3
