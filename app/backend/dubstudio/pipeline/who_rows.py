"""Who should be on screen for each leftover dub row.

Claude often truncates the one-shot JSON (IMTS DE-US 174: dozens of rows unanswered).
Retry the missing rows in small batches, then fill any leftover from the people already
on the clip so scene edit can still run.
"""

WHO_BATCH = 20


def parse_who_reply(text, extract_json):
    """{row_str: unit} from a model reply. Truncated JSON still yields the rows that closed."""
    try:
        data = extract_json(text or "", "units")
    except Exception:
        return {}
    units = data.get("units") if isinstance(data, dict) else data
    out = {}
    for x in units or []:
        if isinstance(x, dict) and x.get("u") is not None:
            out[str(x["u"])] = x
    return out


def clip_people(unit, cast):
    names, seen = [], set()
    for p in unit.get("parts") or []:
        for n in p.get("people") or []:
            if n in cast and n not in seen:
                names.append(n)
                seen.add(n)
    return names


def fallback_who(unit, cast):
    row = (unit.get("rows") or [None])[0]
    return {"u": row, "show": clip_people(unit, cast), "listener": [],
            "why": "clip people; who-call missed this row"}


def missing_units(row_units, who):
    return [u for u in row_units if str((u.get("rows") or [None])[0]) not in who]


def complete_who(row_units, who, ask, extract_json, cast, batch=WHO_BATCH):
    """Fill `who` for every leftover row. `ask(units) -> (text, usage)`.

    One truncated reply used to abort the whole scene edit. Missing rows are asked
    again in batches; a row that still has no answer uses the people on the clip."""
    usage_acc = dict(calls=0, prompt_tokens=0, completion_tokens=0, cost=0.0)

    def add_usage(usage):
        usage = usage or {}
        usage_acc["calls"] += 1
        usage_acc["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        usage_acc["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        usage_acc["cost"] += float(usage.get("cost") or 0)

    pending = missing_units(row_units, who)
    while pending:
        chunk = pending[: max(1, int(batch))]
        text, usage = ask(chunk)
        add_usage(usage)
        who.update(parse_who_reply(text, extract_json))
        still = missing_units(chunk, who)
        if still == chunk:
            for u in still:
                who[str(u["rows"][0])] = fallback_who(u, cast)
        pending = missing_units(row_units, who)
    return who, usage_acc
