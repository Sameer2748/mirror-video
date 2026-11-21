"""Groups used to exit when two blocks shared a sliver of source footage (TGS ep33 156/157)."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve()
CANDS = [
    HERE.parents[1] / "pipeline_tools" / "dubstitch.py",  # live image
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


def test_leftover_not_padded_inside_lip_group_span():
    """EP71 IT#16: leftover vs lip slices 18/19 must not become an 80 ms sliver inside Horton."""
    st = load_stitch()
    leftover = _seg(16, 100860, 106040, 90000, 94700)
    lip18 = _seg(18, 109240, 109740, 93630, 94010)
    lip19 = _seg(19, 109740, 114560, 94010, 97920)
    lip24 = _seg(24, 138000, 142480, 114040, 118740)
    out = [leftover, lip18, lip19, lip24]
    gi = {id(leftover): 0, id(lip18): 1, id(lip19): 1, id(lip24): 1}
    lip = {18: {}, 19: {}, 24: {}}
    n = st.trim_shared_footage(out, gi, set(), lip=lip)
    assert n >= 1
    assert leftover["us_end_ms"] <= 93630 + 0.001
    assert leftover["us_start_ms"] == 90000
    assert leftover["us_end_ms"] - leftover["us_start_ms"] > 3000
    assert not st.group_footage_clash(out, gi, set(), lip)


def test_fully_inside_leftover_parks_before_lip_span():
    st = load_stitch()
    leftover = _seg(16, 100860, 106040, 94634, 94714)
    lip18 = _seg(18, 109240, 109740, 93630, 94010)
    lip19 = _seg(19, 109740, 114560, 94010, 97920)
    out = [leftover, lip18, lip19]
    gi = {id(leftover): 0, id(lip18): 1, id(lip19): 1}
    lip = {18: {}, 19: {}}
    st.trim_shared_footage(out, gi, set(), lip=lip)
    assert leftover["us_end_ms"] <= 93630 + 0.001
    assert leftover["us_start_ms"] < 93630
    assert leftover["us_end_ms"] - leftover["us_start_ms"] < 80
    assert not st.group_footage_clash(out, gi, set(), lip)


def test_both_leftover_rows_trimmed_off_lip_span():
    """Same leftover group: IT#16 is already clear, IT#17 still hangs into Horton — trim 17 too."""
    st = load_stitch()
    a = _seg(16, 100860, 106040, 86540, 90640)
    b = _seg(17, 106040, 109240, 90640, 94700)
    lip18 = _seg(18, 109240, 109740, 93630, 94010)
    lip19 = _seg(19, 109740, 114560, 94010, 97920)
    out = [a, b, lip18, lip19]
    gi = {id(a): 0, id(b): 0, id(lip18): 1, id(lip19): 1}
    n = st.trim_shared_footage(out, gi, set(), lip={18: {}, 19: {}})
    assert n >= 1
    assert a["us_start_ms"] == 86540 and a["us_end_ms"] == 90640
    assert b["us_end_ms"] <= 93630 + 0.001
    assert b["us_start_ms"] == 90640
    assert not st.group_footage_clash(out, gi, set(), lip={18: {}, 19: {}})


def test_leftover_beside_lip_group_is_not_eaten():
    """Kira-look leftover 86.5–93.2 must stay when Horton lip group is 93.63–118.74."""
    st = load_stitch()
    a = _seg(16, 100860, 106040, 86540, 90640)
    b = _seg(17, 106040, 109240, 90640, 93240)
    lip18 = _seg(18, 109240, 109740, 93630, 94010)
    lip19 = _seg(19, 109740, 114560, 94010, 97920)
    out = [a, b, lip18, lip19]
    gi = {id(a): 0, id(b): 0, id(lip18): 1, id(lip19): 1}
    n = st.trim_shared_footage(out, gi, set(), lip={18: {}, 19: {}})
    assert n == 0
    assert a["us_start_ms"] == 86540 and a["us_end_ms"] == 90640
    assert b["us_start_ms"] == 90640 and b["us_end_ms"] == 93240


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


def test_unused_edge_does_not_grow_through_overlapping_lip():
    """Prev rule: leftover unused-edge never steals lip footage, even if spans already overlap."""
    st = load_stitch()
    leftover = _seg(27, 151390, 161770, 131420, 132640)
    lip = _seg(25, 142500, 151390, 118780, 132000)
    groups = [
        dict(rows=[25], lipgroup=True, segments=[lip]),
        dict(rows=[27, 28], lipgroup=False, segments=[leftover]),
    ]
    before0, before1 = leftover["us_start_ms"], leftover["us_end_ms"]
    st.expand_groups_into_unused(groups, target=1.25)
    assert leftover["us_start_ms"] == before0
    assert leftover["us_end_ms"] == before1


def test_unused_edge_still_fills_the_gap_before_the_next_lip_group():
    st = load_stitch()
    leftover = _seg(16, 100860, 106040, 86540, 90640)
    lip = _seg(18, 109240, 142480, 93630, 118740)
    groups = [
        dict(rows=[16, 17], lipgroup=False, segments=[leftover]),
        dict(rows=[18], lipgroup=True, segments=[lip]),
    ]
    st.expand_groups_into_unused(groups, target=1.25)
    assert leftover["us_end_ms"] <= 93630 - 40 + 1
    assert leftover["us_end_ms"] >= 90640
    assert leftover["us_end_ms"] < 92000


def test_trim_does_not_carve_lip_when_leftover_dub_is_earlier():
    """IT#61: leftover 60 overlapped Tom sigh; lip group keeps its window."""
    st = load_stitch()
    leftover = _seg(60, 337740, 346930, 283780, 298480)
    lip = _seg(61, 346930, 348780, 283700, 295000)
    out = [leftover, lip]
    gi = {id(leftover): 0, id(lip): 1}
    st.trim_shared_footage(out, gi, set(), lip={61: {}})
    assert lip["us_start_ms"] == 283700 and lip["us_end_ms"] == 295000
    assert leftover["us_end_ms"] <= 283700 + 0.001 or leftover["us_start_ms"] >= 295000 - 0.001


def test_clamp_span_stops_at_next_lip_group():
    st = load_stitch()
    us0, us1 = st.clamp_span_to_others(459760, 505560, [(482700, 510300)])
    assert us0 == 459760
    assert us1 <= 482700


def test_enforce_leftover_off_others_is_the_owner():
    """After unused-edge / leftover-span / trim, leftover must not sit on the lip take."""
    st = load_stitch()
    leftover = _seg(66, 300000, 310000, 250000, 270000)
    lip = _seg(67, 310000, 320000, 260000, 280000)
    groups = [
        dict(rows=[66], lipgroup=False, segments=[leftover], it0=300000, it1=310000),
        dict(rows=[67], lipgroup=True, lip=True, segments=[lip]),
    ]
    n = st.enforce_leftover_off_others(groups)
    assert n == 1
    assert leftover["us_end_ms"] <= 260000 + 0.001
    assert lip["us_start_ms"] == 260000 and lip["us_end_ms"] == 280000
