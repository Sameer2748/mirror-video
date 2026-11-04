"""lipmerge: a 1-frame ffmpeg drift on 29.97 video is trimmed; a real gap still fails."""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pipeline_tools"))
from lipmerge import merge_pair  # noqa: E402


def test_one_frame_drift_is_trimmed():
    d = {"frames": 375, "window": [15, 30], "scores": list(range(375)), "visual": list(range(375)), "method": {}}
    m = {"frames": 376, "mouth": [0.1] * 376, "aperture": [0.2] * 376}
    out, note = merge_pair(d, m, "15-30")
    assert out["frames"] == 375
    assert len(out["mouth"]) == 375 == len(out["aperture"]) == len(out["scores"])
    assert "trimmed" in note


def test_matching_counts_pass_through():
    d = {"frames": 375, "scores": [1] * 375, "method": {}}
    m = {"frames": 375, "mouth": [0.3] * 375, "aperture": [0.4] * 375}
    out, note = merge_pair(d, m, "0-15")
    assert note is None
    assert out["mouth"] == m["mouth"]
    assert out["method"]["mesh"] == "lipmesh.py"


def test_big_gap_still_fails():
    d = {"frames": 90, "window": [675, 690]}
    m = {"frames": 376, "mouth": [0] * 376, "aperture": [0] * 376}
    try:
        merge_pair(d, m, "675-690")
    except ValueError as ex:
        assert "nets 90" in str(ex) and "mesh 376" in str(ex)
    else:
        raise AssertionError("expected a ValueError")
