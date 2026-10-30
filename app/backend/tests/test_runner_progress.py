from dubstudio.runner import Runner, NOISE


def test_progress_from_tool_lines():
    p = Runner._parse_progress
    assert abs(p("22:58:50 done 45-60 in 23 s (so far 71 s); free RAM 3.2 GB", 741) - 60 / 741) < 1e-9   # lipgpu
    assert abs(p("chunk 12.0-27.5s", 100) - 0.275) < 1e-9
    assert p("rendered 3/12", None) == 0.25
    assert p("nothing here", 100) is None


def test_library_warnings_are_not_the_last_line():
    assert NOISE.search("box_utils.py:103: UserWarning: An output with one or more elements was resized")
    assert NOISE.search("  torch.index_select(x1, 0, idx, out=xx1)")
    assert not NOISE.search("22:58:50 done 45-60 in 23 s")
