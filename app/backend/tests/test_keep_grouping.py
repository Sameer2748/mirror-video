from dubstudio.pipeline.keep_grouping import grouping_takes


def test_keep_grouping_is_one_take_not_one_clip_per_library_shot():
    """EP71: 12 library shots under one grouping span must not become 12 (or 39) timeline clips."""
    segs = [
        dict(it_idx=114, it_start_ms=586930, it_end_ms=610390, us_start_ms=459760, us_end_ms=475500),
        dict(it_idx=114, it_start_ms=610390, it_end_ms=611680, us_start_ms=475500, us_end_ms=476110),
    ]
    got = grouping_takes(segs, shot=123)
    assert len(got) == 1
    assert got[0]["kept_grouping"] is True
    assert got[0]["us0"] == 459.76 and abs(got[0]["us1"] - 476.11) < 1e-6
    assert got[0]["it0"] == 586.93 and abs(got[0]["it1"] - 611.68) < 1e-6


def test_keep_grouping_stays_two_takes_when_source_jumps():
    segs = [
        dict(it_idx=49, it_start_ms=0, it_end_ms=1000, us_start_ms=0, us_end_ms=800),
        dict(it_idx=49, it_start_ms=1000, it_end_ms=2000, us_start_ms=5000, us_end_ms=5800),
    ]
    got = grouping_takes(segs)
    assert len(got) == 2
    assert got[0]["us1"] == 0.8 and got[1]["us0"] == 5.0
