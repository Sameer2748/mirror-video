"""Lip groups play first matched source start → last source end as one take."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve()
SRC = HERE.parents[1] / "pipeline_tools" / "dubstitch.py"


def load_stitch():
    spec = importlib.util.spec_from_file_location("dubstitch_span", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seg(idx, it0, it1, us0, us1, text):
    return dict(it_idx=idx, it_start_ms=it0, it_end_ms=it1, us_start_ms=us0, us_end_ms=us1,
                it_text=text, us_text=text)


def _row(idx, src0, src1, dub0, dub1, text):
    return dict(idx=idx, src_start=src0, src_end=src1, dub_start=dub0, dub_end=dub1,
                src_text=text, dub_text=text)


def test_lunch_rice_group_keeps_unused_middle_as_one_clip():
    st = load_stitch()
    lunch_us = (107400.0, 108920.0)
    rice_us = (110050.0, 114040.0)
    unused_after_lunch = (108920.0, 110050.0)
    segs = [
        _seg(22, 128760, 132225, *lunch_us, "what did you eat for lunch today"),
        _seg(23, 132225, 136680, *rice_us, "I had rice chicken soup and celery"),
    ]
    rows = {
        22: _row(22, lunch_us[0], lunch_us[1], 128760, 132225, segs[0]["us_text"]),
        23: _row(23, rice_us[0], rice_us[1], 132225, 136680, segs[1]["us_text"]),
    }
    clips, _ = st.lip_group_span_clip([0, 1], segs, rows)
    assert len(clips) == 2
    assert clips[0]["us_start_ms"] == lunch_us[0]
    assert clips[-1]["us_end_ms"] == rice_us[1]
    assert clips[0]["it_start_ms"] == 128760
    assert clips[-1]["it_end_ms"] == 136680
    # unused 108.920-110.050 sits inside the continuous take
    assert clips[0]["us_start_ms"] < unused_after_lunch[0] < unused_after_lunch[1] < clips[-1]["us_end_ms"]
    assert abs(clips[0]["us_end_ms"] - clips[1]["us_start_ms"]) < 1
    assert abs(clips[0]["stretch_factor"] - clips[1]["stretch_factor"]) < 0.01
    # rice 3.99s no longer plays alone in 1.75s (0.44x)
    assert clips[0]["stretch_factor"] > 0.8
    assert clips[1]["stretch_factor"] > 0.8


def _g(idx, it0, it1, us0, us1):
    return dict(rows=[idx], it0=it0, it1=it1, us0=us0, us1=us1, stretch=(it1 - it0) / (us1 - us0),
                kept=us1 - us0, segments=[dict(kind="clip", it_idx=idx, it_start_ms=it0, it_end_ms=it1,
                                               us_start_ms=us0, us_end_ms=us1, it_text="", us_text="",
                                               stretch_factor=(it1 - it0) / (us1 - us0), note="")])


def test_slow_single_eats_unused_after():
    st = load_stitch()
    groups = [
        _g(10, 0, 2000, 1000, 1800),      # 2.5x slow; unused 1800-2500 before neighbour
        _g(11, 2000, 3000, 2500, 3500),   # 1.0x
    ]
    filled = st.expand_groups_into_unused(groups, target=1.25)
    assert filled
    assert groups[0]["us1"] > 1800
    assert groups[0]["us1"] <= 2460      # 40ms guard before neighbour
    assert groups[1]["us0"] == 2500
    assert groups[0]["stretch"] < 2.5
    assert groups[0]["stretch"] <= 1.25 + 1e-6 or groups[0]["us1"] >= 2460 - 1


def test_fast_clip_does_not_eat_unused():
    st = load_stitch()
    groups = [
        _g(1, 0, 1000, 0, 3000),          # 0.33x fast
        _g(2, 1000, 2000, 4000, 5000),
    ]
    filled = st.expand_groups_into_unused(groups, target=1.25)
    assert filled == []
    assert groups[0]["us0"] == 0 and groups[0]["us1"] == 3000
