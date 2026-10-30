"""FastAPI app: REST API under /api, live events on /api/ws, the built UI at /."""
import asyncio, json, os, re, shutil, time
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .config import cfg, ROOT
from .db import DB, now
from .events import bus
from .runner import Runner
from . import gen, imports, presets, scan, deliver, names as nm, settings as stg, timeline as tlm
from .pipeline import steps as pipeline_steps
from .pipeline import decisions as dec

app = FastAPI(title="Dub Studio", version="0.1.0")
state = {}


def db() -> DB:
    return state["db"]


def runner() -> Runner:
    return state["runner"]


@app.on_event("startup")
def _startup():
    cfg.library.mkdir(parents=True, exist_ok=True)
    state["db"] = DB(cfg.db_path)
    bus.db = state["db"]
    state["runner"] = Runner(state["db"])
    state["runner"].start()
    gen_interrupted(state["db"])
    try:
        stg.daily_backup(state["db"].conn, cfg.library)
    except Exception as ex:
        print("backup:", ex)


def gen_interrupted(d):
    """AI chats / videos that were being made when the app stopped cannot finish: say so (the panel offers Try again)."""
    d.ex("UPDATE gen_threads SET status='error', error='interrupted: the app restarted while preparing this chat. "
         "Press Try again.' WHERE status IN ('frames','analysing','writing')")
    d.ex("UPDATE gen_jobs SET status='failed', error='interrupted: the app restarted while the video was being made. "
         "Generate again.' WHERE status IN ('starting','queued','running')")


@app.on_event("shutdown")
def _shutdown():
    if "runner" in state:
        state["runner"].shutdown()


def _get(table, id_):
    row = db().one(f"SELECT * FROM {table} WHERE id=?", (id_,))
    if not row:
        raise HTTPException(404, f"{table[:-1]} {id_} not found")
    return row


# ---------------- app info ----------------
@app.get("/api/info")
def info():
    return {"version": "0.1.0", "config": cfg.as_dict(), "tools_found": (cfg.scripts / "dubstitch.py").exists()}


@app.get("/api/presets")
def get_presets():
    return {k: v for k, v in presets.PRESETS.items()}


# ---------------- shows ----------------
@app.get("/api/shows")
def list_shows():
    shows = db().q("SELECT * FROM shows ORDER BY name")
    for s in shows:
        c = db().q("SELECT status, COUNT(*) AS n FROM episodes WHERE show_id=? GROUP BY status", (s["id"],))
        s["episode_counts"] = {r["status"]: r["n"] for r in c}
    return shows


@app.post("/api/shows")
def create_show(body: dict = Body(...)):
    name = (body.get("name") or "").strip(); code = re.sub(r"[^A-Za-z0-9]", "", body.get("code") or "").upper()
    folder = (body.get("folder") or "").strip()
    if not name or not code or not folder:
        raise HTTPException(400, "name, code and folder are required")
    if not Path(folder).expanduser().is_dir():
        raise HTTPException(400, f"folder not found: {folder}")
    if db().one("SELECT id FROM shows WHERE code=?", (code,)):
        raise HTTPException(409, f"a show with code {code} exists")
    prof = presets.preset(body.get("preset") or "NEW")
    prof.update(body.get("profile") or {})
    if not prof.get("names_sheet"):                          # the localisation sheet, found in the show folder
        found = nm.find(Path(folder).expanduser())
        if found:
            prof["names_sheet"] = found[0]
    sid = db().insert("shows", name=name, code=code, folder=str(Path(folder).expanduser()), preset=body.get("preset"),
                      profile=prof, created=now(), updated=now())
    return _get("shows", sid)


@app.get("/api/shows/{sid}")
def get_show(sid: int):
    return _get("shows", sid)


@app.patch("/api/shows/{sid}")
def patch_show(sid: int, body: dict = Body(...)):
    s = _get("shows", sid)
    vals = {}
    for k in ("name", "folder"):
        if k in body:
            vals[k] = body[k]
    if "profile" in body:
        prof = s["profile"]; new = body["profile"]
        if "flags" in new:
            new = {**new, "flags": _flag_limits({**(prof.get("flags") or {}), **(new["flags"] or {})})}
        if "sub_styles" in new:
            cur = prof.get("sub_styles") or {}
            new = {**new, "sub_styles": {lang: _sub_style({**(cur.get(lang) or {}), **(v or {})})
                                         for lang, v in {**cur, **(new["sub_styles"] or {})}.items() if lang in ("dub", "en")}}
        prof.update(new); vals["profile"] = prof
    db().update("shows", sid, updated=now(), **vals)
    return _get("shows", sid)


FLAG_KEYS = ("slow", "slow_red", "fast", "fast_red", "drift_ms", "short_ms", "blur")


def _flag_limits(f):
    """The review flag limits of a show (design: "Review flags"), checked so the colours still make sense."""
    try:
        f = {k: float(f[k]) for k in FLAG_KEYS if k in f}
    except (TypeError, ValueError):
        raise HTTPException(400, "flag limits must be numbers")
    if any(v <= 0 for v in f.values()):
        raise HTTPException(400, "flag limits must be above 0")
    if f.get("slow_red", 0) >= f.get("slow", 1) or f.get("slow", 0.67) >= 1:
        raise HTTPException(400, "slow motion: red must be below yellow, and yellow below ×1")
    if f.get("fast", 1.5) <= 1 or f.get("fast_red", 9) <= f.get("fast", 1.5):
        raise HTTPException(400, "fast: yellow must be above ×1, and red above yellow")
    if f.get("blur", 0.25) >= 1:
        raise HTTPException(400, "blur: the share of the usual sharpness must be below 1 (e.g. 0.25)")
    return f


def _sub_style(st):
    """A subtitle style (one language): style box|strip, size 16-90 px (on a 1080 frame), bottom 0-500 px, alpha 0-1."""
    out = {}
    if st.get("style") not in (None, "box", "strip"):
        raise HTTPException(400, "subtitle style must be box or strip")
    if st.get("style"):
        out["style"] = st["style"]
    for k, lo, hi, cast in (("size", 16, 90, int), ("bottom", 0, 500, int), ("alpha", 0, 1, float)):
        v = st.get(k)
        if v in (None, ""):
            continue
        try:
            v = cast(float(v))
        except (TypeError, ValueError):
            raise HTTPException(400, f"subtitle {k} must be a number")
        if not lo <= v <= hi:
            raise HTTPException(400, f"subtitle {k} must be between {lo} and {hi}")
        out[k] = v
    return out


def show_names_sheet(show):
    """The show's localisation sheet: the one chosen on the show page, else the best one found in its folder."""
    return nm.for_show(show)


def names_sheet_for(eid):
    e = _get("episodes", eid); pth = show_names_sheet(_get("shows", e["show_id"]))
    if pth:
        return pth
    a = db().one("SELECT path FROM assets WHERE episode_id=? AND kind='names_csv' AND role='input'", (eid,))
    return a["path"] if a else None


@app.get("/api/shows/{sid}/names")
def get_names(sid: int):
    s = _get("shows", sid); pth = show_names_sheet(s)
    r = nm.read(pth) if pth else {"format": None, "names": [], "error": None}
    return {"path": pth, "chosen": bool((s.get("profile") or {}).get("names_sheet")), "format": r["format"],
            "count": len(r["names"]), "names": r["names"][:500], "error": r["error"],
            "candidates": nm.find(s["folder"]) if Path(s["folder"]).is_dir() else []}


@app.put("/api/shows/{sid}/names")
def put_names(sid: int, body: dict = Body(...)):
    """{"path": file} to use that sheet for the show, {"path": null} to let the app find it."""
    s = _get("shows", sid); prof = s["profile"]; pth = (body.get("path") or "").strip()
    if pth:
        p = Path(pth).expanduser()
        if not p.is_file() or p.suffix.lower() not in nm.SHEET_EXT:
            raise HTTPException(400, "choose a .csv or .xlsx file")
        r = nm.read(p)
        if not r["names"]:
            raise HTTPException(400, f"no names read from that sheet ({r['error'] or r['format']}): it needs the columns 'source name' and 'target name'")
        prof["names_sheet"] = str(p)
    else:
        prof.pop("names_sheet", None)
    db().update("shows", sid, profile=prof, updated=now())
    return get_names(sid)


@app.get("/api/shows/{sid}/names.csv")
def names_csv(sid: int):
    """The show's names in the house format (source name, target name), to download and keep in the show folder."""
    s = _get("shows", sid); pth = show_names_sheet(s)
    if not pth:
        raise HTTPException(404, "no localisation sheet found for this show")
    from fastapi.responses import Response
    return Response(nm.to_csv(nm.read(pth)["names"]), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{s["code"]}_localisation_names.csv"'})


@app.delete("/api/shows/{sid}")
def delete_show(sid: int):
    _get("shows", sid)
    db().ex("DELETE FROM shows WHERE id=?", (sid,))
    return {"deleted": sid}


# ---------------- episodes ----------------
def _episode_full(eid):
    e = _get("episodes", eid)
    e["assets"] = db().q("SELECT * FROM assets WHERE episode_id=? ORDER BY kind", (eid,))
    run = runner().latest_run(eid)
    e["run"] = run
    e["jobs"] = db().q("SELECT * FROM jobs WHERE episode_id=? AND run=? ORDER BY ord", (eid, run)) if run else []
    for j in e["jobs"]:
        j["label"] = (j.get("params") or {}).get("label", j["step"]); j.pop("params", None)
        if j["step"] == "check" and j["status"] in ("done", "failed"):
            tail = runner().log_tail(j["log_path"], 60)
            j["check_passed"] = any("FINAL CHECK PASSED" in ln for ln in tail)
            j["check_lines"] = tail[-15:]
    e["show"] = _get("shows", e["show_id"])
    out = Path(e["output_dir"] or "")
    e["output_existing"] = sorted(c.name for c in out.iterdir() if not c.name.startswith(".")) if e["output_dir"] and out.is_dir() else []
    return e


@app.get("/api/shows/{sid}/episodes")
def list_episodes(sid: int):
    eps = db().q("SELECT * FROM episodes WHERE show_id=? ORDER BY number", (sid,))
    for e in eps:
        run = runner().latest_run(e["id"])
        if run:
            js = db().q("SELECT status, progress FROM jobs WHERE episode_id=? AND run=?", (e["id"], run))
            e["progress"] = round(sum(1 if j["status"] in ("done", "skipped") else (j["progress"] or 0) for j in js) / max(len(js), 1), 3)
    return eps


@app.post("/api/shows/{sid}/episodes")
def create_episode(sid: int, body: dict = Body(...)):
    s = _get("shows", sid)
    try:
        n = int(body.get("number"))
    except Exception:
        raise HTTPException(400, "number is required")
    if db().one("SELECT id FROM episodes WHERE show_id=? AND number=?", (sid, n)):
        raise HTTPException(409, f"episode {n} exists")
    out = Path(s["folder"]) / s["profile"].get("output_dir", "output/ep{N}").format(N=n)
    if (body.get("output_dir") or "").strip():
        out = Path(body["output_dir"].strip()).expanduser()
        if not out.is_absolute():
            out = Path(s["folder"]) / out
    work = cfg.work_dir(s["code"], n)
    if work.exists() and any(work.iterdir()):
        # a work folder left by a deleted episode: moved aside (never deleted), so every step runs again for real
        # instead of finding the old results (e.g. a lip scan "done" in 0 s)
        work.rename(work.with_name(f"{work.name}_old_{time.strftime('%Y%m%d_%H%M%S')}"))
    eid = db().insert("episodes", show_id=sid, number=n, status="new", work_dir=str(work),
                      output_dir=str(out), created=now(), updated=now())
    found = scan.scan_episode(s["folder"], n, s["profile"])
    for kind, paths in found.items():
        if paths:
            db().insert("assets", episode_id=eid, kind=kind, path=paths[0], role="input", created=now())
    e = _episode_full(eid); e["candidates"] = found
    return e


@app.get("/api/episodes/{eid}")
def get_episode(eid: int):
    return _episode_full(eid)


@app.patch("/api/episodes/{eid}")
def patch_episode(eid: int, body: dict = Body(...)):
    e = _get("episodes", eid); vals = {}
    if "output_dir" in body:
        run = runner().latest_run(eid)
        if run and db().one("SELECT id FROM jobs WHERE episode_id=? AND run=? AND status IN ('waiting','running','needs_input')",
                            (eid, run)):
            raise HTTPException(409, "the episode is being processed with the current folder; cancel it first")
        out = Path((body["output_dir"] or "").strip()).expanduser()
        if not str(out) or str(out) == ".":
            raise HTTPException(400, "output_dir is empty")
        if not out.is_absolute():
            out = Path(_get("shows", e["show_id"])["folder"]) / out
        vals["output_dir"] = str(out)
    if "note" in body:
        vals["note"] = body["note"]
    if vals:
        db().update("episodes", eid, updated=now(), **vals)
    return _episode_full(eid)


@app.get("/api/episodes/{eid}/rows")
def episode_rows(eid: int):
    """The timed sentences of both sides (prep.json), for the Check matching screen."""
    e = _get("episodes", eid); f = Path(e["work_dir"]) / "prep.json"
    if not f.exists():
        raise HTTPException(409, "the automatic matching has not run yet")
    p = json.loads(f.read_text())
    keep = lambda r: {k: r.get(k) for k in ("text", "start_ms", "end_ms")}
    return {"dub": [keep(r) for r in p["dub"]], "src": [keep(r) for r in p["src"]], "auto": p.get("auto", {}),
            "alts": dec.alternatives(p)}


@app.get("/api/episodes/{eid}/scan")
def rescan(eid: int):
    e = _get("episodes", eid); s = _get("shows", e["show_id"])
    return scan.scan_episode(s["folder"], e["number"], s["profile"])


@app.put("/api/episodes/{eid}/assets")
def set_assets(eid: int, body: dict = Body(...)):
    """{kind: path or null}"""
    _get("episodes", eid)
    for kind, path in body.items():
        db().ex("DELETE FROM assets WHERE episode_id=? AND kind=? AND role='input'", (eid, kind))
        if path:
            if not Path(path).exists():
                raise HTTPException(400, f"{kind}: file not found: {path}")
            db().insert("assets", episode_id=eid, kind=kind, path=path, role="input", created=now())
    return _episode_full(eid)


@app.post("/api/episodes/{eid}/check")
def check_files(eid: int):
    out = {}
    for a in db().q("SELECT * FROM assets WHERE episode_id=? AND role='input'", (eid,)):
        info, probs = scan.check_asset(a["kind"], a["path"])
        if info:
            db().update("assets", a["id"], size=info.get("size"), fingerprint=info.get("fingerprint"),
                        mtime=info.get("mtime"), probe=info.get("probe"))
        out[a["kind"]] = {"info": info.get("probe"), "problems": probs}
    return out


@app.delete("/api/episodes/{eid}")
def delete_episode(eid: int):
    runner().cancel_run(eid)
    db().ex("DELETE FROM episodes WHERE id=?", (eid,))
    return {"deleted": eid}


# ---------------- processing ----------------
@app.post("/api/episodes/{eid}/process")
def process(eid: int):
    _get("episodes", eid)
    try:
        run = runner().create_run(eid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"run": run, **_episode_full(eid)}


@app.post("/api/episodes/{eid}/cancel")
def cancel_episode(eid: int):
    runner().cancel_run(eid)
    return _episode_full(eid)


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: int):
    runner().cancel(jid); return {"ok": True}


@app.post("/api/jobs/{jid}/retry")
def retry_job(jid: int):
    runner().retry(jid); return {"ok": True}


@app.get("/api/jobs/{jid}/log")
def job_log(jid: int, tail: int = 200):
    j = _get("jobs", jid)
    return {"lines": runner().log_tail(j["log_path"], tail)}


# ---------------- matching decisions (milestone 1: import or accept automatic) ----------------
@app.get("/api/episodes/{eid}/decisions")
def decisions_state(eid: int):
    e = _get("episodes", eid); w = Path(e["work_dir"])
    if not (w / "prep.json").exists():
        return {"ready": False, "missing": None}
    p, unmatched, case_a = dec.flagged_rows(w)
    miss = dec.missing(w)
    rows = [{"idx": i, "text": p["dub"][i]["text"], "kind": "unmatched" if i in unmatched else "Case A",
             "suggested": (p["src"][p["auto"][str(i)]["src"]]["text"] if str(i) in p["auto"] else None),
             "suggested_src": p["auto"].get(str(i), {}).get("src"), "score": p["auto"].get(str(i), {}).get("score"),
             "start_ms": p["dub"][i].get("start_ms"), "end_ms": p["dub"][i].get("end_ms")}
            for i in sorted(unmatched + case_a)]
    ai_file = w / "ai_proposals.json"
    ai_last = json.loads(ai_file.read_text()) if ai_file.exists() else None
    if ai_last and (w / "spec_ai.py").exists():
        ai_last["spec"] = (w / "spec_ai.py").read_text()
    auto = tlm._load(w / "ai_auto.json")
    if auto:
        auto.pop("spec", None)
    return {"ready": True, "flagged": rows, "missing": miss, "decided": (w / "spec.py").exists() or miss == [],
            "ai_last": ai_last, "ai_auto": auto}


@app.post("/api/episodes/{eid}/decisions/auto")
def decisions_auto(eid: int):
    e = _get("episodes", eid)
    try:
        r = dec.accept_auto(e["work_dir"])
    except FileNotFoundError:
        raise HTTPException(409, "the automatic matching has not run yet")
    runner().poke(); return r


@app.get("/api/ai/status")
def ai_status():
    from . import ai
    s = ai.settings(); out = {"configured": ai.available(), "model": s["AI_MODEL"], "model_fast": s["AI_MODEL_FAST"]}
    if out["configured"]:
        try:
            out.update(ai.key_info())
        except Exception as ex:
            out["error"] = str(ex)[:200]
    return out


@app.post("/api/episodes/{eid}/decisions/ai")
def decisions_ai(eid: int, body: dict = Body(default={})):
    """Step 2 by Claude through the AI gateway. Returns proposals (row, action, src, reason, confidence) and writes
    W/spec_ai.py + W/ai_proposals.json. With {"apply": true} the proposals are imported straight away."""
    from . import ai
    from .pipeline import ai_match
    e = _get("episodes", eid); w = Path(e["work_dir"])
    if not ai.available():
        raise HTTPException(409, "AI gateway not configured (app/.env: AI_BASE, AI_KEY, AI_EMAIL)")
    if not (w / "prep.json").exists():
        raise HTTPException(409, "the automatic matching has not run yet")
    log = lambda m: bus.publish("ai_log", episode_id=eid, store=False, line=m)
    s = _get("shows", e["show_id"])
    try:
        r = ai_match.propose(w, body.get("model"), log=log, names_csv=names_sheet_for(eid))
    except Exception as ex:
        raise HTTPException(502, f"AI matching failed: {ex}")
    if r["proposals"]:
        log("checking the proposals with the pipeline (shared footage → groups)…")
        r = ai_match.apply_group_rule(w, r, lambda spec: dec.check_spec(w, spec, cfg.pipeline_python, cfg.scripts,
                                                                         s["profile"]["labels"]), log=log)
    if body.get("apply"):
        try:
            r["applied"] = dec.from_spec(w, r["spec"])
        except Exception as ex:
            raise HTTPException(400, f"AI proposals did not pass the check: {ex}")
        runner().poke()
    return r


@app.post("/api/episodes/{eid}/decisions/rematch")
def decisions_rematch(eid: int):
    """Match again with the AI: the decisions are cleared and the decisions step runs again (Claude decides by
    itself), then sheet -> edit list -> groups -> review. Editor versions of the old edit list stay in the history."""
    from . import ai
    e = _get("episodes", eid); w = Path(e["work_dir"])
    if not ai.available():
        raise HTTPException(409, "AI gateway not configured (Settings)")
    if not (w / "prep.json").exists():
        raise HTTPException(409, "the automatic matching has not run yet")
    for f in ("ai_auto.json", "spec.py"):
        (w / f).unlink(missing_ok=True)
    (w / "decisions.json").write_text(json.dumps({"groups": [], "assign": {}, "confirm": {}, "repeat": {}, "gap": {},
                                                  "unused_notes": {}}))
    try:
        steps = runner().rerun_from(eid, "decisions")
    except ValueError as ex:
        raise HTTPException(409, str(ex))
    return {"steps": steps}


@app.post("/api/episodes/{eid}/decisions/check")
def decisions_check(eid: int, body: dict = Body(...)):
    """Run the pipeline's integrity check on a spec without saving it: {"spec": text}."""
    e = _get("episodes", eid); w = Path(e["work_dir"]); s = _get("shows", e["show_id"])
    if not (w / "prep.json").exists():
        raise HTTPException(409, "the automatic matching has not run yet")
    return dec.check_spec(w, body.get("spec") or "", cfg.pipeline_python, cfg.scripts, s["profile"]["labels"])


@app.post("/api/episodes/{eid}/decisions/join")
def decisions_join(eid: int, body: dict = Body(...)):
    """Neighbouring rows that share source footage become groups: {"spec": text} -> new spec + what was joined,
    checked again with the pipeline's integrity check."""
    e = _get("episodes", eid); w = Path(e["work_dir"]); s = _get("shows", e["show_id"])
    spec = body.get("spec") or ""
    r = dec.check_spec(w, spec, cfg.pipeline_python, cfg.scripts, s["profile"]["labels"])
    if not r["overlaps"]:
        return {"spec": spec, "joined": [], "left": [], "check": r}
    new, joined, left = dec.join_shared(w, spec, r["overlaps"])
    return {"spec": new, "joined": joined, "left": left,
            "check": dec.check_spec(w, new, cfg.pipeline_python, cfg.scripts, s["profile"]["labels"])}


@app.post("/api/episodes/{eid}/decisions/import")
async def decisions_import(eid: int, file: UploadFile = File(...)):
    e = _get("episodes", eid); text = (await file.read()).decode("utf-8")
    w = Path(e["work_dir"])
    try:
        if file.filename.endswith(".json"):
            json.loads(text); (w / "decisions.json").write_text(text); r = {"imported": "decisions.json"}
        else:
            r = dec.from_spec(w, text)
    except Exception as ex:
        raise HTTPException(400, f"could not import: {ex}")
    runner().poke(); return r


# ---------------- editor: the timeline before render ----------------
def _ep_ctx(eid):
    show, ep, assets = runner().episode_context(eid)
    ctx, _ = pipeline_steps.plan(show, ep, assets)
    return show, ep, assets, ctx


def _src_duration_ms(eid):
    a = db().one("SELECT probe FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'", (eid,))
    d = (a or {}).get("probe") or {}
    return round(d["duration"] * 1000) if isinstance(d, dict) and d.get("duration") else None


def _versions(eid):
    return db().q("SELECT version, made_by, note, created, json_extract(body, '$.base') AS base FROM timeline_versions "
                  "WHERE episode_id=? ORDER BY version DESC", (eid,))


def _current_timeline(eid, work):
    v1 = tlm.build_v1(work)
    if v1 is None:
        raise HTTPException(409, "the edit list is not made yet: the editor opens after the Groups step")
    row = db().one("SELECT * FROM timeline_versions WHERE episode_id=? AND json_extract(body, '$.base')=? "
                   "ORDER BY version DESC LIMIT 1", (eid, v1["base"]))
    tl = row["body"] if row else v1
    tl["holes"] = v1.get("holes") or []
    if row:
        tl["version"] = row["version"]
    draft = tlm._load(Path(work) / "timeline_draft.json")
    if draft and draft.get("base") == v1["base"] and draft.get("saved", 0) > (row["created"] if row else 0):
        draft["draft"] = True; draft.setdefault("version", tl.get("version", 0)); tl = draft
    return tl


def _review_state(eid):
    run = runner().latest_run(eid)
    jobs = {j["step"]: j for j in db().q("SELECT step, status, progress FROM jobs WHERE episode_id=? AND run=?", (eid, run))} if run else {}
    return {k: jobs[k]["status"] for k in ("groups", "review", "render", "check") if k in jobs}


def _markers(eid):
    return db().q("SELECT id, t_ms, text, source, resolved, created FROM markers WHERE episode_id=? ORDER BY t_ms, id", (eid,))


def _check(eid, body, work):
    tl = {"clips": body["clips"], "v2": body.get("v2") or [], "tracks": body.get("tracks") or {},
          "approved": body.get("approved") or [], "total_ms": body["total_ms"], "holes": tlm.holes(work)}
    show = _get("shows", _get("episodes", eid)["show_id"])
    fl = tlm.flags(tl, tlm.context(work), (show["profile"] or {}).get("flags"), _markers(eid))
    media_ms = {x["id"]: x["duration_ms"] for x in imports.items(work)}
    return tl, fl, tlm.open_counts(fl), tlm.validate(tl, _src_duration_ms(eid), media_ms)


@app.get("/api/episodes/{eid}/timeline")
def get_timeline(eid: int, pipeline: bool = False):
    """The timeline to edit: the latest saved version (or the autosaved draft); pipeline=1 gives the pipeline's own."""
    show, ep, assets, ctx = _ep_ctx(eid); work = ep["work_dir"]
    tl = tlm.build_v1(work) if pipeline else _current_timeline(eid, work)
    if tl is None:
        raise HTTPException(409, "the edit list is not made yet: the editor opens after the Groups step")
    _, fl, counts, probs = _check(eid, tl, work)
    c = tlm.context(work)
    return {
        "timeline": tl, "flags": fl, "counts": counts, "problems": probs,
        "versions": [v for v in _versions(eid) if v["base"] == tl["base"]],
        "src_sentences": c["src_sentences"], "dub_starts": c["dub_starts"], "lip_rows": sorted(c["lip_rows"]),
        **_subs(eid, ctx), "markers": _markers(eid), "proxy": tlm.proxy_state(work),
        "labels": (show["profile"].get("labels") or "DUB,SRC").split(","),
        "sub_style": show["profile"].get("sub_style", "box"), "sub_styles": show["profile"].get("sub_styles") or {},
        "transitions": _transitions_info(show), "cuts": tlm.cuts(tl),
        "src_duration_ms": _src_duration_ms(eid), "state": _review_state(eid),
        "show": {"id": show["id"], "code": show["code"]}, "episode": {"id": ep["id"], "number": ep["number"], "status": ep["status"]},
    }


def _transitions_info(show):
    """The styles for the editor's menu, the show's most used first, and what a cut gets when nothing is chosen."""
    eps = db().q("SELECT id, work_dir FROM episodes WHERE show_id=?", (show["id"],))
    bodies = [r["body"] for e in eps for r in db().q("SELECT body FROM timeline_versions WHERE episode_id=? "
                                                     "ORDER BY version DESC LIMIT 1", (e["id"],))]
    cfgs = [tlm._load(Path(e["work_dir"]) / "config.json") for e in eps if e["work_dir"]]
    use = tlm.transition_use(bodies, cfgs)
    order = sorted(tlm.TRANSITIONS, key=lambda k: (-use[k], list(tlm.TRANSITIONS).index(k)))
    return {"default": "auto" if show["profile"].get("transitions") else "none",
            "styles": [{"id": k, "label": tlm.TRANSITIONS[k][0], "half_s": tlm.TRANSITIONS[k][1], "uses": use[k]} for k in order]}


@app.post("/api/episodes/{eid}/timeline/check")
def check_timeline(eid: int, body: dict = Body(...)):
    work = _get("episodes", eid)["work_dir"]
    _, fl, counts, probs = _check(eid, body, work)
    return {"flags": fl, "counts": counts, "problems": probs}


@app.put("/api/episodes/{eid}/timeline")
def save_timeline(eid: int, body: dict = Body(...)):
    """draft=true: autosave (one file, overwritten). Otherwise a new numbered version."""
    work = _get("episodes", eid)["work_dir"]
    v1 = tlm.build_v1(work)
    if v1 is None:
        raise HTTPException(409, "the edit list is not made yet")
    tl, fl, counts, probs = _check(eid, body, work)
    tl["base"] = v1["base"]
    if body.get("draft"):
        tl["saved"] = time.time(); tl["version"] = body.get("version", 0)
        (Path(work) / "timeline_draft.json").write_text(json.dumps(tl, ensure_ascii=False))
        return {"draft": True, "counts": counts, "problems": probs}
    if probs:
        raise HTTPException(400, "; ".join(probs[:5]))
    return {"version": _new_version(eid, tl, body.get("made_by") or "editor", body.get("note")), "counts": counts}


def _new_version(eid, tl, made_by, note=None):
    last = db().one("SELECT MAX(version) AS v FROM timeline_versions WHERE episode_id=?", (eid,))["v"] or 0
    body = {k: tl.get(k) for k in ("base", "total_ms", "clips", "approved")}
    body["v2"] = tl.get("v2") or []; body["tracks"] = tl.get("tracks") or {}
    db().insert("timeline_versions", episode_id=eid, version=last + 1, parent=tl.get("version"), made_by=made_by,
                note=note, body=body, created=now())
    db().update("episodes", eid, current_version=last + 1, updated=now())
    return last + 1


@app.get("/api/episodes/{eid}/timeline/auto")
def timeline_auto(eid: int):
    """The automatic edit (before the scene edit), read-only: the editor's 'Automatic edit' row."""
    clips = tlm.auto_edit(_get("episodes", eid)["work_dir"])
    if clips is None:
        raise HTTPException(404, "no automatic edit kept (the scene edit has not run for this episode)")
    return {"clips": clips}


@app.post("/api/episodes/{eid}/scene/redo")
def scene_redo(eid: int):
    """Redo the scene edit with fresh Claude answers: the cached answers move to W/scene/_old/<time>/ (nothing is
    deleted), then scene_edit and every step after it run again. A plain Retry reuses the cached answers ($0)."""
    w = Path(_get("episodes", eid)["work_dir"]); cache = w / "scene" / "scene_manifest.par_cache.json"
    if cache.exists():
        old = w / "scene" / "_old" / time.strftime("%Y%m%d_%H%M%S"); old.mkdir(parents=True, exist_ok=True)
        cache.rename(old / cache.name)
    try:
        steps = runner().rerun_from(eid, "scene_edit")
    except ValueError as ex:
        raise HTTPException(409, str(ex))
    return {"steps": steps}


@app.get("/api/episodes/{eid}/timeline/versions/{v}")
def get_version(eid: int, v: int):
    row = db().one("SELECT * FROM timeline_versions WHERE episode_id=? AND version=?", (eid, v))
    if not row:
        raise HTTPException(404, "no such version")
    tl = row["body"]; tl["version"] = v
    return tl


@app.post("/api/episodes/{eid}/timeline/render")
def render_timeline(eid: int, body: dict = Body(...)):
    """Save the editor's timeline as a version, write it as manifest_groups.json, and run render → final check."""
    e = _get("episodes", eid); work = e["work_dir"]
    v1 = tlm.build_v1(work)
    if v1 is None:
        raise HTTPException(409, "the edit list is not made yet")
    tl, fl, counts, probs = _check(eid, body, work)
    if probs:
        raise HTTPException(400, "the timeline cannot be rendered: " + "; ".join(probs[:5]))
    if counts["red"] and not body.get("force"):
        raise HTTPException(409, f"{counts['red']} red flags are open: fix or approve them first")
    st = _review_state(eid)
    if any(st.get(k) == "running" for k in ("render", "check")):
        raise HTTPException(409, "a render is running now")
    tl["base"] = v1["base"]
    v = _new_version(eid, tl, "render", body.get("note") or "rendered")
    tlm.write_manifest(work, tl, v)
    (Path(work) / "timeline_draft.json").unlink(missing_ok=True)
    _, _, _, ctx = _ep_ctx(eid)
    _deliver_en_srt(work, ctx)
    show_ = _get("shows", e["show_id"])
    tlm.write_transition_plan(work, tl, "auto" if show_["profile"].get("transitions") else "none")
    runner().ensure_steps(eid)
    rid = deliver.start_render(db(), eid, v, body.get("note"))
    try:
        _write_premiere(eid, tl, v)
    except Exception as ex:                                   # the XML is a convenience: never block a render on it
        print("premiere xml:", ex)
    tlm.request_render(work, v)
    runner().refresh_pending(eid)
    try:
        if "review" in st and st["review"] in ("needs_input", "waiting", "running"):
            steps = ["review"]; runner().poke()
        elif "review" in st:
            steps = runner().rerun_from(eid, "review")
        else:                                           # a run made before the editor existed
            Path(work, tlm.REVIEW_OK).unlink(missing_ok=True)
            steps = runner().rerun_from(eid, "render")
    except ValueError as ex:
        raise HTTPException(409, str(ex))
    return {"version": v, "steps": steps, "render": rid}


# ---------------- milestone 6: render queue, deliverables, Premiere XML ----------------
def _render_row(r):
    r = deliver.render_state(db(), runner(), r)
    e = db().one("SELECT e.number, e.status AS ep_status, s.code, s.id AS show_id FROM episodes e JOIN shows s ON s.id=e.show_id "
                 "WHERE e.id=?", (r["episode_id"],))
    return {**r, **(e or {})}


@app.get("/api/busy")
def busy():
    """Steps running now (any episode): the editor shows "processing is running, preview may lag" while the GPU or
    the processors are busy (a lip scan, Whisper, a render)."""
    rows = db().q("SELECT j.step, j.resource, e.number, s.code FROM jobs j JOIN episodes e ON e.id=j.episode_id "
                  "JOIN shows s ON s.id=e.show_id WHERE j.status='running' AND j.resource IN ('cpu','gpu')")
    return {"running": [{"step": r["step"], "episode": f"{r['code']} ep{r['number']}", "resource": r["resource"]} for r in rows]}


@app.get("/api/renders")
def list_renders(limit: int = 60):
    rows = db().q("SELECT * FROM renders ORDER BY id DESC LIMIT ?", (limit,))
    return [_render_row(r) for r in rows]


def _rendered_version(work):
    m = tlm._load(Path(work) / "manifest_groups.json") or {}
    return (m.get("dub_studio") or {}).get("version")


@app.get("/api/episodes/{eid}/deliverables")
def deliverables(eid: int):
    show, ep, assets, ctx = _ep_ctx(eid); work = Path(ep["work_dir"])
    files = deliver.deliverables(show, ep, ctx)
    last = db().one("SELECT * FROM renders WHERE episode_id=? ORDER BY id DESC LIMIT 1", (eid,))
    latest_v = (db().one("SELECT MAX(version) AS v FROM timeline_versions WHERE episode_id=?", (eid,)) or {}).get("v")
    draft = tlm._load(work / "timeline_draft.json")
    rendered = _rendered_version(work)
    clean = next((f for f in files if f["kind"] == "clean" and f["exists"]), None)
    return {"files": files, "output_dir": ep["output_dir"], "render": _render_row(last) if last else None,
            "rendered_version": rendered, "latest_version": latest_v,
            "unrendered": bool((latest_v and rendered and latest_v > rendered) or
                               (draft and clean and draft.get("saved", 0) > clean["mtime"])),
            "final_check_image": (work / "final_check.jpg").exists(),
            "qc_open": (db().one("SELECT COUNT(*) AS n FROM markers WHERE episode_id=? AND resolved=0", (eid,)) or {}).get("n", 0)}


@app.get("/api/episodes/{eid}/final-check.jpg")
def final_check_image(eid: int):
    p = Path(_get("episodes", eid)["work_dir"]) / "final_check.jpg"
    if not p.exists():
        raise HTTPException(404, "no final check image yet")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


def _write_premiere(eid, tl, version):
    show, ep, assets, ctx = _ep_ctx(eid)
    src = db().one("SELECT path, probe FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'", (eid,))
    dub = db().one("SELECT path, probe FROM assets WHERE episode_id=? AND kind='dub_audio' AND role='input'", (eid,))
    if not src or not dub:
        raise HTTPException(409, "the episode has no source video or dub audio")
    names = presets.names(show["code"], ep["number"], show["profile"])
    prefix = names["clean"].replace("_final_clean.mp4", "")
    xml = deliver.premiere_xml(tl, f"{prefix} v{version}" if version else prefix, src["path"], dub["path"], src["probe"],
                               (dub["probe"] or {}).get("duration") if isinstance(dub["probe"], dict) else None,
                               _markers(eid), (show["profile"].get("labels") or "IT,US").split(","),
                               {x["id"]: dict(x, path=imports.path_of(ep["work_dir"], x["id"])) for x in imports.items(ep["work_dir"])})
    out = Path(ep["output_dir"]) / f"{prefix}_timeline_v{version or 0}.xml"
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text(xml, encoding="utf-8")
    return out


@app.get("/api/episodes/{eid}/export/premiere")
def export_premiere(eid: int, version: int = 0):
    """The timeline (a saved version, or the current one) as Premiere-readable XML; also saved in the output folder."""
    work = _get("episodes", eid)["work_dir"]
    if version:
        row = db().one("SELECT body FROM timeline_versions WHERE episode_id=? AND version=?", (eid, version))
        if not row:
            raise HTTPException(404, "no such version")
        tl = row["body"]
    else:
        tl = _current_timeline(eid, work); version = tl.get("version") or 0
    p = _write_premiere(eid, tl, version)
    return FileResponse(p, media_type="application/xml", filename=p.name)


# ---------------- editor: subtitles (S1 dub, S2 English) ----------------
SUB_LANGS = ("dub", "en")


def _subs(eid, ctx):
    """Each track as the editor sees it: the edited cues if any, else the SRT the pipeline uses."""
    out, edited = {}, {}
    for lang in SUB_LANGS:
        row = db().one("SELECT cues FROM subtitle_tracks WHERE episode_id=? AND language=?", (eid, lang))
        edited[lang] = bool(row)
        out[lang] = row["cues"] if row else tlm.parse_srt(ctx.get("sub_srt_orig" if lang == "dub" else "name_en_srt") or "")
    return {"subs": out, "subs_edited": edited}


@app.put("/api/episodes/{eid}/subs/{lang}")
def save_subs(eid: int, lang: str, body: dict = Body(...)):
    """{"cues": [{t0, t1, text}]} -> saved for the episode and written as W/subs_edited_<lang>.srt, which the
    subtitle steps burn at the next render (the show folder's SRT is never changed)."""
    if lang not in SUB_LANGS:
        raise HTTPException(404, "unknown subtitle track")
    e = _get("episodes", eid); v1 = tlm.build_v1(e["work_dir"])
    cues, probs = tlm.check_cues(body.get("cues"), v1["total_ms"] if v1 else None)
    if probs:
        raise HTTPException(400, "; ".join(probs[:5]))
    if not cues:
        raise HTTPException(400, "a subtitle track needs at least one cue")
    db().ex("INSERT INTO subtitle_tracks (episode_id, language, kind, cues, updated) VALUES (?,?,?,?,?) "
            "ON CONFLICT(episode_id, language) DO UPDATE SET cues=excluded.cues, updated=excluded.updated",
            (eid, lang, "dub" if lang == "dub" else "translation", cues, now()))
    f = tlm.edited_srt(e["work_dir"], lang); f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(tlm.srt_text(cues), encoding="utf-8")
    return {"saved": len(cues), "file": str(f)}


@app.delete("/api/episodes/{eid}/subs/{lang}")
def revert_subs(eid: int, lang: str):
    """Back to the pipeline's SRT for this track."""
    if lang not in SUB_LANGS:
        raise HTTPException(404, "unknown subtitle track")
    e = _get("episodes", eid)
    db().ex("DELETE FROM subtitle_tracks WHERE episode_id=? AND language=?", (eid, lang))
    tlm.edited_srt(e["work_dir"], lang).unlink(missing_ok=True)
    _, _, _, ctx = _ep_ctx(eid)
    if lang == "en":
        _deliver_en_srt(e["work_dir"], ctx)
    return _subs(eid, ctx)


def _deliver_en_srt(work, ctx):
    """The delivered English SRT (output folder) follows the edited S2 track; the translation is kept in W once."""
    out = Path(ctx["name_en_srt"]); keep = Path(work) / "english_translation_original.srt"
    ed = tlm.edited_srt(work, "en")
    if ed.exists():
        if out.exists() and not keep.exists():
            shutil.copyfile(out, keep)
        out.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ed, out)
    elif keep.exists():
        shutil.copyfile(keep, out); keep.unlink()


@app.post("/api/episodes/{eid}/translate-line")
def translate_line(eid: int, body: dict = Body(...)):
    """Re-translate one subtitle line with the AI: {"text": dub line, "before": "", "after": ""} -> {"text": ...}."""
    from . import ai
    from .pipeline import translate_ai
    e = _get("episodes", eid); prof = _get("shows", e["show_id"])["profile"]
    if not ai.available():
        raise HTTPException(409, "AI gateway not configured (app/.env)")
    if not (body.get("text") or "").strip():
        raise HTTPException(400, "no text to translate")
    try:
        return {"text": translate_ai.one(body["text"], prof.get("dub_lang", "it"), prof.get("translate_to", "en"),
                                         before=body.get("before") or "", after=body.get("after") or "")}
    except Exception as ex:
        raise HTTPException(502, f"translation failed: {ex}")


# ---------------- editor: QC markers ----------------
@app.get("/api/episodes/{eid}/markers")
def list_markers(eid: int):
    _get("episodes", eid); return _markers(eid)


@app.post("/api/episodes/{eid}/markers")
def add_marker(eid: int, body: dict = Body(...)):
    """{"text": "06:04 Viola smiling"} (time read from the text) or {"text": ..., "t_ms": ...}."""
    _get("episodes", eid)
    t, text = tlm.parse_marker(body.get("text") or "")
    if t is None:
        t = body.get("t_ms")
    if t is None:
        raise HTTPException(400, "give a time: type it first (06:04 Viola smiling) or put the playhead there")
    if not text:
        raise HTTPException(400, "the note is empty")
    mid = db().insert("markers", episode_id=eid, t_ms=round(float(t)), text=text, source=body.get("source") or "QC",
                      resolved=0, created=now())
    return db().one("SELECT * FROM markers WHERE id=?", (mid,))


@app.patch("/api/markers/{mid}")
def patch_marker(mid: int, body: dict = Body(...)):
    _get("markers", mid); vals = {}
    if "text" in body:
        vals["text"] = (body["text"] or "").strip()
    if "t_ms" in body:
        vals["t_ms"] = round(float(body["t_ms"]))
    if "resolved" in body:
        vals["resolved"] = 1 if body["resolved"] else 0
    db().update("markers", mid, **vals)
    return db().one("SELECT * FROM markers WHERE id=?", (mid,))


@app.delete("/api/markers/{mid}")
def delete_marker(mid: int):
    _get("markers", mid); db().ex("DELETE FROM markers WHERE id=?", (mid,))
    return {"deleted": mid}


# ---------------- editor: the 540-px preview copy ----------------
def _src_video(eid):
    a = db().one("SELECT path, probe FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'", (eid,))
    if not a:
        raise HTTPException(404, "no source video")
    return a["path"], (a["probe"] or {}).get("duration") if isinstance(a["probe"], dict) else None


@app.get("/api/episodes/{eid}/proxy")
def proxy_status(eid: int):
    return tlm.proxy_state(_get("episodes", eid)["work_dir"])


@app.post("/api/episodes/{eid}/proxy")
def proxy_make(eid: int):
    """Make the preview copy now (episodes processed before the proxy step existed); returns at once."""
    e = _get("episodes", eid); path, dur = _src_video(eid)
    Path(e["work_dir"]).mkdir(parents=True, exist_ok=True)
    tlm.make_proxy_bg(e["work_dir"], path, dur)
    # older episodes: the blur flag's measure too (from the preview copy once it exists)
    if not (Path(e["work_dir"]) / tlm.SHARPNESS).exists() and tlm.proxy_path(e["work_dir"]):
        tlm.make_sharpness_bg(e["work_dir"], tlm.proxy_path(e["work_dir"]), cfg.pipeline_python)
    return tlm.proxy_state(e["work_dir"])


@app.get("/api/episodes/{eid}/media/{kind}")
def media(eid: int, kind: str):
    if kind == "src_proxy":
        p = tlm.proxy_path(_get("episodes", eid)["work_dir"])
        if not p:
            raise HTTPException(404, "the preview copy is not made yet")
        return FileResponse(p, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})
    if kind == "dub_preview":                     # what the editor plays (exact AAC copy; see timeline.dub_preview)
        e = _get("episodes", eid)
        a = db().one("SELECT path FROM assets WHERE episode_id=? AND kind='dub_audio' AND role='input'", (eid,))
        if not a or not Path(a["path"]).exists():
            raise HTTPException(404, "no dub_audio")
        try:
            p = tlm.dub_preview(e["work_dir"], a["path"])
        except RuntimeError as ex:
            raise HTTPException(500, str(ex))
        return FileResponse(p, media_type="audio/mp4", headers={"Cache-Control": "private, max-age=3600"})
    if kind not in ("src_video", "dub_audio"):
        raise HTTPException(404, "unknown media")
    a = db().one("SELECT path FROM assets WHERE episode_id=? AND kind=? AND role='input'", (eid, kind))
    if not a or not Path(a["path"]).exists():
        raise HTTPException(404, f"no {kind}")
    return FileResponse(a["path"], headers={"Cache-Control": "private, max-age=3600"})


# ---------------- the project bin: imported clips + the episode's files ----------------
@app.get("/api/episodes/{eid}/bin")
def bin_items(eid: int):
    """What the editor's project bin shows: clips imported for this episode, and the episode's own files
    (source video, the SRTs, the translated SRT)."""
    show, ep, assets, ctx = _ep_ctx(eid); work = ep["work_dir"]
    files = []
    def add(kind, label, path, kind2=None):
        if path and Path(path).exists():
            files.append(dict(kind=kind, label=label, name=Path(path).name, path=str(path), size=Path(path).stat().st_size))
    S = (show["profile"].get("labels") or "IT,US").split(",")
    add("src_video", f"Source video ({S[1]})", assets.get("src_video"))
    add("dub_audio", f"Dub audio ({S[0]})", assets.get("dub_audio"))
    add("dub_srt", f"{S[0]} subtitles (SRT)", ctx.get("sub_srt"))
    add("src_srt", f"{S[1]} subtitles (SRT)", ctx.get("src_srt"))
    add("en_srt", "Translated subtitles (EN SRT)", ctx.get("en_srt_burn"))
    return {"imports": imports.items(work), "files": files}


@app.get("/api/episodes/{eid}/bin/file/{kind}")
def bin_file(eid: int, kind: str):
    f = next((x for x in bin_items(eid)["files"] if x["kind"] == kind), None)
    if not f:
        raise HTTPException(404, "no such file")
    return FileResponse(f["path"], filename=f["name"])


@app.post("/api/episodes/{eid}/imports")
async def import_clip(eid: int, file: UploadFile = File(...)):
    """Import a video clip into this episode's bin (a copy in the work folder; the original is not touched)."""
    work = _get("episodes", eid)["work_dir"]
    def write(dest):
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out, 8 << 20)
    try:
        return await asyncio.to_thread(imports.add, work, imports.safe_name(file.filename), write)
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.post("/api/episodes/{eid}/imports/path")
def import_clip_path(eid: int, body: dict = Body(...)):
    """Import a clip by its path on this Mac (faster for big files: no upload)."""
    try:
        return imports.add_path(_get("episodes", eid)["work_dir"], (body.get("path") or "").strip())
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.get("/api/episodes/{eid}/imports/{iid}/media")
def import_media(eid: int, iid: str):
    p = imports.preview_of(_get("episodes", eid)["work_dir"], iid)
    if not p or not Path(p).exists():
        raise HTTPException(404, "no such imported clip")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})


@app.delete("/api/episodes/{eid}/imports/{iid}")
def delete_import(eid: int, iid: str):
    work = _get("episodes", eid)["work_dir"]
    try:
        tl = _current_timeline(eid, work)
    except HTTPException:
        tl = {}
    if imports.used_in(tl, iid):
        raise HTTPException(409, "this clip is on the timeline: take it off first")
    if not imports.remove(work, iid):
        raise HTTPException(404, "no such imported clip")
    return {"ok": True}


# ---------------- generate a clip with AI (gen.py) ----------------
@app.get("/api/gen/status")
def gen_status():
    return gen.status()


def _gen_thread(eid, tid):
    t = db().one("SELECT * FROM gen_threads WHERE id=? AND episode_id=?", (tid, eid))
    if not t:
        raise HTTPException(404, "no such chat")
    t["_dir"] = str(gen.folder(_get("episodes", eid)["work_dir"], tid))
    return t


def _gen_full(eid, tid):
    t = _gen_thread(eid, tid); t.pop("_dir", None)
    t["messages"] = db().q("SELECT * FROM gen_messages WHERE thread_id=? ORDER BY id", (tid,))
    t["jobs"] = db().q("SELECT * FROM gen_jobs WHERE thread_id=? ORDER BY id", (tid,))
    return t


def _clip_video(eid, work, clip):
    if clip.get("media"):
        p = imports.path_of(work, clip["media"])
        if p:
            return p
    a = db().one("SELECT path FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'", (eid,))
    if not a:
        raise HTTPException(409, "the episode has no source video")
    return a["path"]


def _gen_prepare(eid, tid):
    """frames -> Gemini analysis -> Claude's first prompt (in the background; the panel follows the status)."""
    e = _get("episodes", eid); work = e["work_dir"]
    try:
        t = db().one("SELECT * FROM gen_threads WHERE id=?", (tid,)); c = t["clip"]
        video = _clip_video(eid, work, c)
        db().update("gen_threads", tid, status="frames", updated=now())
        frames = gen.make_frames(work, tid, video, c["s0"], c["s1"])
        db().update("gen_threads", tid, frames=frames, status="analysing", updated=now())
        analysis = gen.analyse(work, tid, video, c["s0"], c["s1"], frames)
        db().update("gen_threads", tid, analysis=analysis, status="writing", updated=now())
        ask = gen.FIRST_ASK.format(slot=(c["t1"] - c["t0"]) / 1000)
        db().ex("DELETE FROM gen_messages WHERE thread_id=?", (tid,))       # a retry starts the chat again
        db().insert("gen_messages", thread_id=tid, role="user", text=ask, images=[], created=now())
        t = _gen_thread(eid, tid)
        msgs = db().q("SELECT * FROM gen_messages WHERE thread_id=? ORDER BY id", (tid,))
        text, prompt = gen.chat_reply(t, msgs)
        db().insert("gen_messages", thread_id=tid, role="assistant", text=text, images=[], created=now())
        db().update("gen_threads", tid, prompt=prompt or text.strip(), status="ready", error=None, updated=now())
    except Exception as ex:
        db().update("gen_threads", tid, status="error", error=str(ex)[:500], updated=now())


@app.get("/api/episodes/{eid}/gen/threads")
def gen_threads(eid: int, slot: str = ""):
    """The chats of one clip (its slot "t0-t1" on the dub timeline), newest first; without slot: all of the episode."""
    rows = db().q("SELECT id, slot, clip, title, status, created, updated FROM gen_threads WHERE episode_id=? "
                  + ("AND slot=? " if slot else "") + "ORDER BY id DESC", (eid, slot) if slot else (eid,))
    for r in rows:
        r["videos"] = db().one("SELECT COUNT(*) AS n FROM gen_jobs WHERE thread_id=? AND status='done'", (r["id"],))["n"]
    return rows


@app.post("/api/episodes/{eid}/gen/threads")
def gen_new_thread(eid: int, body: dict = Body(...)):
    """{clip: {id, label, row, t0, t1, s0, s1, dub_text, src_text, media}} -> a new chat for that clip; its frames,
    analysis and first prompt are made in the background."""
    c = body.get("clip") or {}
    try:
        c = {k: c.get(k) for k in ("id", "label", "row", "dub_text", "src_text", "media", "prev_text", "next_text", "lip")} | \
            {"flags": [str(f)[:200] for f in (c.get("flags") or [])][:12]} | {k: float(c[k]) for k in ("t0", "t1", "s0", "s1")}
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "clip needs t0, t1, s0, s1")
    e = _get("episodes", eid); sh = _get("shows", e["show_id"])                   # context for the AI
    c.update(show=sh["code"], episode=e["number"], labels=(sh["profile"] or {}).get("labels"))
    if c["s1"] - c["s0"] < 40 or c["t1"] - c["t0"] < 100:
        raise HTTPException(400, "the clip is too short")
    slot = f"{round(c['t0'])}-{round(c['t1'])}"
    n = db().one("SELECT COUNT(*) AS n FROM gen_threads WHERE episode_id=? AND slot=?", (eid, slot))["n"]
    tid = db().insert("gen_threads", episode_id=eid, slot=slot, clip=c, title=f"{c.get('label') or 'clip'} · chat {n + 1}",
                      status="frames", created=now(), updated=now())
    import threading
    threading.Thread(target=_gen_prepare, args=(eid, tid), daemon=True).start()
    return _gen_full(eid, tid)


@app.post("/api/episodes/{eid}/gen/threads/{tid}/retry")
def gen_retry(eid: int, tid: int):
    """Prepare a chat that failed (VPN off, interrupted) again: frames -> analysis -> first prompt."""
    t = _gen_thread(eid, tid)
    if t["status"] not in ("error", "ready"):
        raise HTTPException(409, "this chat is being prepared right now")
    if t["status"] == "ready" and db().one("SELECT COUNT(*) AS n FROM gen_messages WHERE thread_id=? AND role='user'", (tid,))["n"] > 1:
        raise HTTPException(409, "this chat already has your messages: start a new chat instead")
    db().update("gen_threads", tid, status="frames", error=None, updated=now())
    import threading
    threading.Thread(target=_gen_prepare, args=(eid, tid), daemon=True).start()
    return _gen_full(eid, tid)


@app.get("/api/episodes/{eid}/gen/threads/{tid}")
def gen_thread(eid: int, tid: int):
    return _gen_full(eid, tid)


@app.delete("/api/episodes/{eid}/gen/threads/{tid}")
def gen_delete_thread(eid: int, tid: int):
    t = _gen_thread(eid, tid)
    db().ex("DELETE FROM gen_threads WHERE id=?", (tid,))
    shutil.rmtree(t["_dir"], ignore_errors=True)       # its frames and uploads; generated clips stay in the bin
    return {"ok": True}


@app.put("/api/episodes/{eid}/gen/threads/{tid}/prompt")
def gen_set_prompt(eid: int, tid: int, body: dict = Body(...)):
    _gen_thread(eid, tid)
    db().update("gen_threads", tid, prompt=(body.get("prompt") or "").strip(), updated=now())
    return _gen_full(eid, tid)


@app.post("/api/episodes/{eid}/gen/threads/{tid}/messages")
async def gen_message(eid: int, tid: int, text: str = Form(""), files: list[UploadFile] = File(default=[])):
    """The editor's message (+ images) -> Claude's answer with the whole chat as context. A ```prompt block in the
    answer becomes the clip's prompt."""
    t = _gen_thread(eid, tid); d = Path(t["_dir"]) / "uploads"; d.mkdir(parents=True, exist_ok=True)
    imgs = []
    for f in files or []:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            raise HTTPException(400, f"{f.filename}: only jpg / png / webp images")
        name = f"u{int(time.time() * 1000)}_{len(imgs)}{ext}"
        (d / name).write_bytes(await f.read()); imgs.append(f"uploads/{name}")
    if not (text or "").strip() and not imgs:
        raise HTTPException(400, "write a message or attach an image")
    db().insert("gen_messages", thread_id=tid, role="user", text=(text or "").strip(), images=imgs, created=now())
    msgs = db().q("SELECT * FROM gen_messages WHERE thread_id=? ORDER BY id", (tid,))
    try:
        reply, prompt = await asyncio.to_thread(gen.chat_reply, t, msgs)
    except Exception as ex:
        db().insert("gen_messages", thread_id=tid, role="error", text=str(ex)[:400], images=[], created=now())
        return _gen_full(eid, tid)
    db().insert("gen_messages", thread_id=tid, role="assistant", text=reply, images=[], created=now())
    if prompt:
        db().update("gen_threads", tid, prompt=prompt, updated=now())
    return _gen_full(eid, tid)


@app.post("/api/episodes/{eid}/gen/threads/{tid}/generate")
def gen_generate(eid: int, tid: int, body: dict = Body(...)):
    """{model, prompt?, use_end, images: [uploaded image to start from]} -> a video job on fal.ai."""
    t = _gen_thread(eid, tid)
    ms = [m["id"] for m in gen.available_models()]
    if not ms:
        raise HTTPException(409, "no video model: the AI key has no Veo model (VPN on?) and no fal.ai key is saved")
    if not t.get("frames"):
        raise HTTPException(409, "the clip's frames are not ready yet")
    prompt = (body.get("prompt") or t.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "the prompt is empty")
    model = (body.get("model") or ms[0]).strip()
    if model not in ms:
        raise HTTPException(400, f"{model} is not available (available: {', '.join(ms)})")
    mode = body.get("mode") or None
    if mode not in (None, "frames", "shots", "refs"):
        raise HTTPException(400, "mode is frames, shots or refs")
    refs = [r for r in (body.get("refs") or []) if isinstance(r, str) and ".." not in r and r.startswith(("frames/", "uploads/"))][:3]
    if (mode == "refs" or refs) and not gen.model_info(model).get("refs"):
        raise HTTPException(400, f"{model} cannot take reference images: choose Veo 3.1")
    jid = db().insert("gen_jobs", thread_id=tid, model=model, prompt=prompt, status="starting", created=now(),
                      params={"use_start": True, "use_end": bool(body.get("use_end", True)), "images": body.get("images") or [],
                              "mode": mode, "refs": refs})
    db().update("gen_threads", tid, prompt=prompt, updated=now())
    e = _get("episodes", eid)
    gen.start_job(db(), jid, e["work_dir"], video=_clip_video(eid, e["work_dir"], t["clip"]))
    return _gen_full(eid, tid)


@app.get("/api/episodes/{eid}/gen/threads/{tid}/file/{path:path}")
def gen_file(eid: int, tid: int, path: str):
    base = Path(_gen_thread(eid, tid)["_dir"]).resolve(); p = (base / path).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404, "no such file")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/episodes/{eid}/thumb")
def thumb(eid: int, ms: float, h: int = 120):
    e = _get("episodes", eid)
    a = db().one("SELECT path FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'", (eid,))
    if not a:
        raise HTTPException(404, "no source video")
    p = tlm.thumb(e["work_dir"], str(tlm.proxy_path(e["work_dir"]) or a["path"]), ms, max(60, min(h, 360)))
    if not p:
        raise HTTPException(404, "no frame")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/episodes/{eid}/waveform")
def wave(eid: int):
    e = _get("episodes", eid)
    a = db().one("SELECT path FROM assets WHERE episode_id=? AND kind='dub_audio' AND role='input'", (eid,))
    if not a:
        raise HTTPException(404, "no dub audio")
    return tlm.waveform(e["work_dir"], a["path"])


# ---------------- settings (milestone 7) ----------------
@app.get("/api/settings")
def get_settings():
    return {"ai": stg.ai_settings(), "app": stg.app_settings(cfg), "health": stg.health(cfg, cfg.db_path),
            "supervised": os.environ.get("DUBSTUDIO_SUPERVISED") == "1"}


@app.put("/api/settings/ai")
def put_ai(body: dict = Body(...)):
    """{base, email, key, model, model_fast, remove_key}: an empty key keeps the one saved. Saved in app/.env."""
    try:
        return stg.write_ai({"AI_BASE": body.get("base"), "AI_EMAIL": body.get("email"), "AI_KEY": body.get("key"),
                             "AI_MODEL": body.get("model"), "AI_MODEL_FAST": body.get("model_fast"),
                             "FAL_KEY": body.get("fal_key")},
                            remove_key="FAL_KEY" if body.get("remove_fal_key") else bool(body.get("remove_key")))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.post("/api/settings/ai/test")
def test_ai():
    return stg.test_ai()


@app.put("/api/settings/app")
def put_app(body: dict = Body(...)):
    """Paths and performance, saved in app/config.json; used after a restart."""
    try:
        saved = stg.write_app(body)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"saved": saved, "restart_needed": True}


@app.post("/api/restart")
def restart():
    """Restart the server (only when the launcher watches it: it starts it again at once). Refused while a step runs."""
    if os.environ.get("DUBSTUDIO_SUPERVISED") != "1":
        raise HTTPException(409, "Dub Studio was not started with its launcher; stop it and start it again")
    if db().one("SELECT id FROM jobs WHERE status='running'"):
        raise HTTPException(409, "a step is running: wait for it, or cancel it first")
    import threading
    threading.Timer(0.5, lambda: os._exit(3)).start()
    return {"restarting": True}


# ---------------- files (pick inputs) ----------------
@app.get("/api/fs")
def list_dir(path: str = "~"):
    p = Path(path).expanduser()
    if not p.is_dir():
        raise HTTPException(404, "not a folder")
    items = []
    for c in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if c.name.startswith("."):
            continue
        items.append({"name": c.name, "path": str(c), "dir": c.is_dir(), "size": c.stat().st_size if c.is_file() else None})
    return {"path": str(p), "parent": str(p.parent), "items": items[:2000]}


@app.post("/api/reveal")
def reveal(body: dict = Body(...)):
    """Show a file or folder in Finder (Mac)."""
    p = Path(body.get("path") or "").expanduser()
    if not p.exists():
        raise HTTPException(404, "not found")
    import subprocess, sys
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)] if p.is_file() else ["open", str(p)])
    return {"ok": True}


# ---------------- live events ----------------
@app.websocket("/api/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    loop = asyncio.get_running_loop(); q = bus.subscribe(loop)
    try:
        while True:
            ev = await q.get()
            await sock.send_text(json.dumps(ev, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(loop, q)


# ---------------- the UI ----------------
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.api_route("/{full:path}", methods=["GET", "HEAD"])
    def spa(full: str):
        f = DIST / full
        if full and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})   # a rebuilt UI shows at once
