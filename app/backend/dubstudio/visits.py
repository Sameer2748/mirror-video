"""How the website groups pipeline jobs for live status. Stitch order is unchanged.

Each visit is one rented machine (or a gate). Inner step names match `pipeline/steps.py`."""

# id, label, steps that ride together, how they run
VISITS = (
    ("fetch", "Get the files", ("fetch",)),
    ("ingest", "Check files", ("ingest",)),
    ("lip", "Lip scan · 8 CPUs", ("lip_gpu", "lip_mesh", "lip_merge")),
    ("proxy", "Preview copy (540 px)", ("proxy",)),
    ("sharp", "Picture sharpness", ("sharpness",)),
    ("match", "Whisper → matching · one CPU",
     ("mksrt_dub", "mksrt_src", "words_dub", "words_src", "sentences_src", "analyze", "merged")),
    ("translate", "English translation", ("translate",)),
    ("decisions", "Check matching", ("decisions",)),
    ("cut", "Sheet → library · one CPU",
     ("sheet", "manifest", "lipsync", "groups", "library", "captions")),
    ("facemap", "Face map · GPU", ("facemap",)),
    ("scene", "Scene edit → fast trim · one CPU", ("scene_edit", "use_scene", "fast_trim")),
    ("review", "Ready for review", ("review",)),
    ("tail", "Render → check · one CPU",
     ("dynsrt", "render", "verify", "bookends", "trans_plan", "transitions",
      "subs_dub", "subs_en", "scene_sheet", "qc", "check")),
    ("publish", "Publish", ("publish",)),
    ("cleanup", "Cleanup", ("cleanup",)),
    ("notes", "Run notes", ("notes",)),
)

BY_STEP = {step: (vid, label) for vid, label, steps in VISITS for step in steps}

RANK = {"running": 0, "needs_input": 1, "failed": 2, "waiting": 3, "blocked": 4,
        "cancelled": 5, "done": 6, "skipped": 7}


def visit_of(step):
    return BY_STEP.get(step)


def label_for_step(step, inner_label=None):
    hit = BY_STEP.get(step)
    if not hit:
        return inner_label or step
    _, visit_label = hit
    if inner_label and inner_label not in visit_label:
        return f"{visit_label} · {inner_label}"
    return visit_label


def live_for_jobs(jobs):
    """Current visit + inner step for a run. jobs: rows with step, status, optional params/label/message."""
    if not jobs:
        return {"step": None, "label": None, "message": None, "visit": None}
    pick = None
    for want in ("running", "needs_input", "failed", "waiting"):
        pick = next((j for j in jobs if j.get("status") == want), None)
        if pick:
            break
    if not pick:
        return {"step": None, "label": None, "message": None, "visit": None}
    inner = (pick.get("params") or {}).get("label") or pick.get("label") or pick.get("step")
    msg = pick.get("message") or ""
    return {
        "step": pick.get("step"),
        "label": label_for_step(pick.get("step"), inner),
        "message": msg,
        "visit": (visit_of(pick.get("step")) or (None, None))[0],
    }
