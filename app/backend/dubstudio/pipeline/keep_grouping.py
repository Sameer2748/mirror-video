"""When scene edit keeps the grouping cut, write the grouping take — not one clip per library shot.

Leftover rows that share the same source span (overlap / rewind) stay one forward take.
EP71 IT#49–51: Tom's close-up played four times because each row kept overlapping in/out.
"""


def _overlap(a0, a1, b0, b1):
    return min(a1, b1) - max(a0, b0)


def shares_picture(prev_us0, prev_us1, us0, us1, join=0.05):
    """True when two clips are the same take: abutting, overlapping, or a short rewind into the previous span."""
    if abs(us0 - prev_us1) <= join:
        return True
    if _overlap(prev_us0, prev_us1, us0, us1) > join:
        return True
    return False


def grouping_takes(segs, shot=None):
    """One piece per grouping segment (contiguous or overlapping US stays one piece). Shot labels are not cuts."""
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
        if out and abs(out[-1]["it1"] - p["it0"]) <= 0.002 and shares_picture(
                out[-1]["us0"], out[-1]["us1"], p["us0"], p["us1"]):
            out[-1]["it1"] = p["it1"]
            out[-1]["us0"] = min(out[-1]["us0"], p["us0"])
            out[-1]["us1"] = max(out[-1]["us1"], p["us1"])
            continue
        out.append(p)
    return out


def fit_overlapping_ms(segs, min_overlap_ms=50):
    """Play overlapping leftover rows as one source span, split in dub order (no rewind).

    Returns True if any in/out changed. Lip-group callers should not use this.
    """
    if not segs or len(segs) < 2:
        return False
    ordered = sorted(segs, key=lambda s: s["it_start_ms"])
    join = False
    for x, y in zip(ordered, ordered[1:]):
        if _overlap(x["us_start_ms"], x["us_end_ms"], y["us_start_ms"], y["us_end_ms"]) > min_overlap_ms:
            join = True
            break
    if not join:
        return False
    us0 = min(s["us_start_ms"] for s in ordered)
    us1 = max(s["us_end_ms"] for s in ordered)
    it0, it1 = ordered[0]["it_start_ms"], ordered[-1]["it_end_ms"]
    span_it, span_us = it1 - it0, us1 - us0
    if span_it <= 1 or span_us <= 1:
        return False
    changed = False
    for s in ordered:
        ua = us0 + (s["it_start_ms"] - it0) / span_it * span_us
        ub = us0 + (s["it_end_ms"] - it0) / span_it * span_us
        if abs(s["us_start_ms"] - ua) > 1 or abs(s["us_end_ms"] - ub) > 1:
            changed = True
        s["us_start_ms"] = round(ua, 3)
        s["us_end_ms"] = round(ub, 3)
        s["stretch_factor"] = round((s["it_end_ms"] - s["it_start_ms"]) / max(s["us_end_ms"] - s["us_start_ms"], 1), 3)
    return changed
