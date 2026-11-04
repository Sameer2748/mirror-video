"""lipmerge: 1-frame drift and last-piece EOF gaps are trimmed; an empty half still fails."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dubstudio" / "pipeline"))
from lipmerge import merge_pair  # noqa: E402
from dubstudio.runner import Runner


def test_one_frame_drift_is_trimmed():
    d = {"frames": 375, "window": [15, 30], "scores": list(range(375)), "visual": list(range(375)), "method": {}}
    m = {"frames": 376, "mouth": [0.1] * 376, "aperture": [0.2] * 376}
    out, note = merge_pair(d, m, "15-30")
    assert out["frames"] == 375
    assert len(out["mouth"]) == 375 == len(out["aperture"]) == len(out["scores"])
    assert "trimmed" in note


def test_nets_one_frame_longer_is_trimmed():
    """TAB / TGS: piece 135-150 often comes back nets 376, mesh 375."""
    d = {"frames": 376, "scores": list(range(376)), "visual": list(range(376)), "method": {}}
    m = {"frames": 375, "mouth": [0.1] * 375, "aperture": [0.2] * 375}
    out, note = merge_pair(d, m, "135-150")
    assert out["frames"] == 375
    assert len(out["mouth"]) == 375 == len(out["scores"])
    assert "trimmed" in note


def test_matching_counts_pass_through():
    d = {"frames": 375, "scores": [1] * 375, "method": {}}
    m = {"frames": 375, "mouth": [0.3] * 375, "aperture": [0.4] * 375}
    out, note = merge_pair(d, m, "0-15")
    assert note is None
    assert out["mouth"] == m["mouth"]
    assert out["method"]["mesh"] == "lipmesh.py"


def test_last_piece_eof_gap_is_trimmed():
    """TAB ep12 600-615: one half stops at the video end (258), the other wrote a full 15 s window (376)."""
    d = {"frames": 376, "scores": [1] * 376, "visual": [1] * 376, "method": {}}
    m = {"frames": 258, "mouth": [0.1] * 258, "aperture": [0.2] * 258}
    out, note = merge_pair(d, m, "600-615")
    assert out["frames"] == 258
    assert len(out["mouth"]) == 258 == len(out["scores"])
    assert "trimmed" in note


def test_empty_half_still_fails():
    try:
        merge_pair({"frames": 0, "scores": []}, {"frames": 376, "mouth": [0] * 376, "aperture": [0] * 376}, "0-15")
    except ValueError as ex:
        assert "empty" in str(ex)
    else:
        raise AssertionError("expected a ValueError")


def test_lip_merge_uses_the_app_script_not_the_rented_one():
    cmd = ["{py}", "{scripts}/lipmerge.py", "--nets", "/w/lips_nets", "--mesh", "/w/lips_mesh", "--out", "/w/lips_range"]
    got = Runner._lipmerge_local_cmd(cmd)
    assert got[0] == sys.executable
    assert got[1].endswith("dubstudio/pipeline/lipmerge.py")
    assert Path(got[1]).is_file()


def test_groups_and_merged_use_shipped_scripts_not_the_mac_desktop():
    dub = Runner._app_pipeline_script("dubstitch.py")
    merged = Runner._app_pipeline_script("merged.py")
    assert dub and merged
    assert "france-team" not in str(dub) and "Desktop" not in str(merged)
    cmd = ["{py}", "{scripts}/dubstitch.py", "groups", "--work", "/w"]
    got = Runner._use_app_script(cmd, "dubstitch.py", dub)
    assert got[1] == str(dub)
