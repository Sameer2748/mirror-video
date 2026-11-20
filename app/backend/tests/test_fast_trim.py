"""Fast trim after the slow-only scene edit: two cases, cut from last, cap 1.25x."""
import json
from pathlib import Path

from dubstudio.pipeline.fast_trim import run, spd


def _seg(idx, it0, it1, us0, us1):
    return dict(kind="clip", it_idx=idx, it_start_ms=it0, it_end_ms=it1,
                us_start_ms=us0, us_end_ms=us1, stretch_factor=round((it1 - it0) / max(us1 - us0, 1), 3), note="")


def test_narration_group_trims_fastest_inner_clip(tmp_path):
    W = tmp_path
    segs = [
        _seg(0, 0, 4000, 0, 8000),      # 2.0x
        _seg(1, 4000, 8000, 8000, 9000),  # 0.25x
    ]
    json.dump({"total_ms": 8000, "segments": segs, "groups": []}, open(W / "manifest_groups.json", "w"))
    json.dump({"groups": [{"rows": [0, 1], "it0": 0, "it1": 8000, "us0": 0, "us1": 9000}]},
              open(W / "manifest_groups.grouping.json", "w"))
    json.dump({"rows": {}, "intervals": []}, open(W / "lipsync.json", "w"))
    assert run(W) == 0
    out = json.loads((W / "manifest_groups.json").read_text())["segments"]
    assert spd(out[0]) <= 1.26
    assert out[0]["us_end_ms"] < 8000
    assert "fast-trim" in (out[0].get("note") or "")


def test_lip_group_trims_narration_not_the_lip_clip(tmp_path):
    W = tmp_path
    segs = [
        _seg(0, 0, 3000, 0, 6000),       # narration 2.0x
        _seg(1, 3000, 6000, 6000, 7500), # lip 0.5x
    ]
    json.dump({"total_ms": 6000, "segments": segs}, open(W / "manifest_groups.json", "w"))
    json.dump({"groups": [{"rows": [0, 1], "it0": 0, "it1": 6000, "us0": 0, "us1": 7500, "lip": True}]},
              open(W / "manifest_groups.grouping.json", "w"))
    json.dump({"rows": {"1": {"speech": [3000, 6000]}}, "intervals": [[6.0, 7.5]]},
              open(W / "lipsync.json", "w"))
    assert run(W) == 0
    out = json.loads((W / "manifest_groups.json").read_text())["segments"]
    assert out[1]["us_end_ms"] == 7500
    assert out[0]["us_end_ms"] < 6000
