"""When scene edit keeps the grouping cut, write the grouping take — not one clip per library shot."""


def grouping_takes(segs, shot=None):
    """One piece per grouping segment (contiguous US stays one piece). Shot labels are not cuts."""
    out = []
    for s in segs or []:
        p = dict(
            shot=shot,
            us0=s["us_start_ms"] / 1000,
            us1=s["us_end_ms"] / 1000,
            it0=s["it_start_ms"] / 1000,
            it1=s["it_end_ms"] / 1000,
            row=s["it_idx"],
            own=True,
            kept_grouping=True,
        )
        if out and abs(out[-1]["it1"] - p["it0"]) <= 0.002 and abs(out[-1]["us1"] - p["us0"]) <= 0.05:
            out[-1]["it1"] = p["it1"]
            out[-1]["us1"] = p["us1"]
            continue
        out.append(p)
    return out
