"""Lip groups keep each line's own source in/out (IMTS EP71 lunch vs rice)."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve()
SRC = HERE.parents[1] / "pipeline_tools" / "dubstitch.py"


def load_stitch():
    spec = importlib.util.spec_from_file_location("dubstitch_own_lines", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seg(idx, it0, it1, us0, us1, text):
    return dict(it_idx=idx, it_start_ms=it0, it_end_ms=it1, us_start_ms=us0, us_end_ms=us1,
                it_text=text, us_text=text)


def _row(idx, src0, src1, dub0, dub1, text):
    return dict(idx=idx, src_start=src0, src_end=src1, dub_start=dub0, dub_end=dub1,
                src_text=text, dub_text=text)


def test_lunch_question_keeps_its_own_us_not_rice():
    st = load_stitch()
    lunch_us = (107400.0, 108920.0)
    rice_us = (110050.0, 114040.0)
    segs = [
        _seg(22, 128760, 132225, *lunch_us, "what did you eat for lunch today"),
        _seg(23, 132225, 136680, *rice_us, "I had rice chicken soup and celery"),
    ]
    rows = {
        22: _row(22, lunch_us[0], lunch_us[1], 128760, 132225, segs[0]["us_text"]),
        23: _row(23, rice_us[0], rice_us[1], 132225, 136680, segs[1]["us_text"]),
    }
    lip = {22: {}, 23: {}}

    def lip_window(i, it_len):
        r = rows[i]
        return (r["src_start"], r["src_end"]), False

    clips, _ = st.lip_group_own_line_clips([0, 1], segs, rows, lip, lip_window, 1.5)
    by = {c["it_idx"]: c for c in clips}
    lunch, rice = by[22], by[23]
    mid = (lunch_us[1] + rice_us[0]) / 2
    assert lunch["us_start_ms"] == lunch_us[0]
    assert abs(lunch["us_end_ms"] - mid) < 1
    assert abs(rice["us_start_ms"] - mid) < 1
    assert rice["us_end_ms"] == rice_us[1]
    # rice mouth (from 110.05s) must not play during the lunch-question dub
    assert lunch["us_end_ms"] <= rice_us[0] + 1
    assert lunch["us_end_ms"] < 111740
