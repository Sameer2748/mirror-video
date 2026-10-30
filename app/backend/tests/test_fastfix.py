"""Fast-clip rule: no piece faster than 1.25x; narration trimmed from both ends, lip clips by the rule when no AI."""
import json, subprocess, sys
from pathlib import Path


def _work(tmp_path, segs, lip_rows=None, intervals=None):
    w = tmp_path / "w"; w.mkdir()
    (w / "manifest_groups.json").write_text(json.dumps({"total_ms": segs[-1]["it_end_ms"], "segments": segs, "groups": []}))
    (w / "config.json").write_text(json.dumps({"src_video": str(tmp_path / "v.mp4")}))
    (w / "lipsync.json").write_text(json.dumps({"rows": lip_rows or {}, "intervals": intervals or []}))
    (w / "prep.json").write_text(json.dumps({"dub": [{"text": "a"}, {"text": "b"}], "src": [{"text": "x"}, {"text": "y"}]}))
    (w / "scenes.json").write_text(json.dumps({"video": str(tmp_path / "v.mp4"), "size": 0, "cuts": [12.0]}))
    (tmp_path / "v.mp4").write_bytes(b"")
    return w


def seg(i, t0, t1, u0, u1, note=""):
    return dict(kind="clip", it_idx=i, it_start_ms=t0, it_end_ms=t1, us_start_ms=u0, us_end_ms=u1, it_text="", us_text="",
                stretch_factor=round((t1 - t0) / (u1 - u0), 3), note=note)


def test_every_fast_piece_ends_at_or_under_threshold(tmp_path):
    from dubstudio.pipeline import fastfix
    segs = [seg(0, 0, 4000, 0, 6000),                                   # narration x1.5
            seg(1, 4000, 8000, 10000, 16000, "lip group"),               # lip x1.5: shot 10-12 s has no lips
            seg(1, 8000, 10000, 16000, 18000)]                           # x1.0, untouched
    w = _work(tmp_path, segs, {"1": {"sentence": [10000, 16000], "src_lines": [1]}}, [[12.2, 15.8]])
    class A: work = str(w); threshold = 1.25; target = 1.2; no_ai = True; max_seconds = 1e9
    assert fastfix.main(A) == 0
    out = json.loads((w / "manifest_groups.json").read_text())["segments"]
    assert all((s["us_end_ms"] - s["us_start_ms"]) / (s["it_end_ms"] - s["it_start_ms"]) <= 1.26 for s in out)
    rep = json.loads((w / "fastfix.json").read_text())
    lip = next(c for c in rep["clips"] if c["row"] == 1)
    assert lip["kind"] == "lip group piece" and all(b <= 12.2 for a, b in lip["cuts"])   # cut from the no-lip shot only
    assert abs(out[-1]["it_end_ms"] - 10000) < 1 and (w / "fastfix_cuts.json").exists()
    assert (w / "manifest_groups.before_fastfix.json").exists()


def test_rule_cuts_reaches_need_even_when_all_lips():
    from dubstudio.pipeline.fastfix import rule_cuts
    cuts = rule_cuts(10.0, 16.0, 1.0, [(10.0, 16.0)], [(10.0, 16.0)])
    assert abs(sum(b - a for a, b in cuts) - 1.0) < 0.01 and cuts[0][0] == 10.0 and cuts[-1][1] == 16.0


def test_no_cut_under_the_minimum():
    """Skill 4A.1: no cut smaller than 0.3 s. A small trim is one cut at the end (rounded up to 0.3 s), a bigger one
    is split evenly between both ends; the lip rule's leftovers and short shots follow the same minimum."""
    from dubstudio.pipeline import fastfix as ff
    m = ff.MIN_CUT / 1000
    assert m == 0.3
    assert ff.ends_cuts(10.0, 14.0, 1.0) == [[10.0, 10.5], [13.5, 14.0]]            # 2 x 0.5 s
    assert ff.ends_cuts(10.0, 14.0, 0.5) == [[13.5, 14.0]]                           # halves would be 0.25 s
    assert ff.ends_cuts(10.0, 14.0, 0.12) == [[13.7, 14.0]]                          # rounded up to 0.3 s
    for need in (0.15, 0.4, 0.55, 0.9, 1.7):
        cs = ff.rule_cuts(0.0, 5.0, need, lips=[(1.0, 4.0)], shots=[(0.0, 0.2), (0.2, 5.0)])
        assert cs and all(b - a >= m - 1e-6 for a, b in cs), (need, cs)
        assert sum(b - a for a, b in cs) >= need - 1e-6
