"""Groups used to exit when two blocks shared a sliver of source footage (TGS ep33 156/157)."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve()
CANDS = [
    HERE.parents[3] / "pipeline_tools" / "dubstitch.py",
    Path.home() / "Desktop/france-team/adaptation-tool/scripts/dubstitch.py",
]


def load_stitch():
    src = next(p for p in CANDS if p.is_file())
    spec = importlib.util.spec_from_file_location("dubstitch_groups", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seg(idx, it0, it1, us0, us1):
    return dict(kind="clip", it_idx=idx, it_start_ms=it0, it_end_ms=it1,
                us_start_ms=us0, us_end_ms=us1, stretch_factor=1.0, note="")


def test_lip_window_spill_is_trimmed_not_raised():
    """157's lip window ran 113 ms into 156. 157 is lip-sync and keeps the overlap."""
    st = load_stitch()
    a = _seg(156, 621280, 623940, 514220, 515780)
    b = _seg(157, 624690, 627240, 511130, 514333.301)
    out = [a, b]
    gi = {id(a): 0, id(b): 1}
    assert st.group_footage_clash(out, gi, set())
    n = st.trim_shared_footage(out, gi, set(), lip={157: {}})
    assert n == 1
    assert not st.group_footage_clash(out, gi, set())
    assert a["us_start_ms"] >= b["us_end_ms"] - 0.001
    assert b["us_start_ms"] == 511130 and b["us_end_ms"] == 514333.301
    assert "156" in (a.get("note") or "") or "157" in (a.get("note") or "")
