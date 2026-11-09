"""Milestone 6: renders (the render queue), the deliverables of an episode, and the Premiere XML export.

A render = one press of Render in the editor: it saves a timeline version and runs render -> verify -> bookends
(-> transitions) -> subs -> check in the episode's latest run. Its state is read from those jobs, so the queue page
and the episode always agree with the runner.

Premiere XML = Final Cut Pro 7 XML (xmeml v4), which Premiere Pro imports (File > Import): one sequence as long as
the dub, V1 = the clips (source video, in/out, speed as Premiere's Time Remap), V2 = the editor's overlay clips, A1 = the dub
audio, QC notes as sequence markers."""
import time
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from . import presets, timeline as tlm

RENDER_STEPS = ("render", "verify", "bookends", "transitions", "subs_dub", "subs_en", "check")
STEP_LABEL = {"render": "rendering clips", "verify": "checking sync", "bookends": "opening + ending",
              "transitions": "transitions", "subs_dub": "dub subtitle video", "subs_en": "English subtitle video",
              "check": "final check"}


# ---------------- renders ----------------
def start_render(db, eid, version, note=None):
    """A new render row; an older unfinished render of the same episode is replaced by it."""
    for r in db.q("SELECT id FROM renders WHERE episode_id=? AND status IN ('waiting','running')", (eid,)):
        db.update("renders", r["id"], status="replaced", finished=time.time())
    return db.insert("renders", episode_id=eid, version=version, status="waiting", created=time.time(),
                     check_result={"note": note} if note else None)


def render_state(db, runner, r):
    """{status, progress, step, eta, started} of one render from its episode's latest run (and saves the end once)."""
    if r["status"] not in ("waiting", "running"):
        # A rented-machine tail used to save passed=False because check.log was empty. Re-read the
        # render log once so a finished episode does not keep a red empty banner.
        cr = r.get("check_result") or {}
        if r["status"] == "done" and cr.get("passed") is not True:
            run = runner.latest_run(r["episode_id"])
            jobs = db.q("SELECT step, log_path FROM jobs WHERE episode_id=? AND run=?",
                        (r["episode_id"], run)) if run else []
            chk_res = runner.final_check_result(jobs)
            if chk_res.get("passed") is True:
                cr = dict(cr, passed=True, lines=chk_res["lines"][-12:])
                db.update("renders", r["id"], check_result=cr)
                r = dict(r, check_result=cr)
        return r
    run = runner.latest_run(r["episode_id"])
    jobs = [j for j in db.q("SELECT step, status, progress, eta, started, finished, log_path FROM jobs "
                            "WHERE episode_id=? AND run=? ORDER BY ord", (r["episode_id"], run)) if j["step"] in RENDER_STEPS]
    if not jobs:
        return r
    st = [j["status"] for j in jobs]
    if "failed" in st:
        status = "failed"
    elif all(s in ("done", "skipped") for s in st):
        status = "done"
    elif "cancelled" in st and not any(s in ("running", "waiting") for s in st):
        status = "cancelled"
    elif any(s == "running" for s in st) or any(s in ("done",) for s in st):
        status = "running"
    else:
        status = "waiting"
    cur = next((j for j in jobs if j["status"] == "running"), None) or next((j for j in jobs if j["status"] not in ("done", "skipped")), None)
    prog = sum(1 if j["status"] in ("done", "skipped") else (j["progress"] or 0) for j in jobs) / len(jobs)
    started = min((j["started"] for j in jobs if j["started"]), default=None)
    out = dict(r, status=status, progress=round(prog, 3), step=STEP_LABEL.get(cur["step"]) if cur and status != "done" else None,
               eta=cur["eta"] if cur and status == "running" else None, started=started)
    if status in ("done", "failed", "cancelled"):
        chk_res = runner.final_check_result(jobs)
        res = dict(r.get("check_result") or {}, passed=chk_res["passed"],
                   lines=chk_res["lines"][-12:],
                   seconds={j["step"]: round(j["finished"] - j["started"], 1) for j in jobs if j["started"] and j["finished"]})
        fin = max((j["finished"] for j in jobs if j["finished"]), default=time.time())
        db.update("renders", r["id"], status=status, check_result=res, finished=fin)
        out.update(check_result=res, finished=fin)
    elif status != r["status"]:
        db.update("renders", r["id"], status=status)
    return out


# ---------------- deliverables ----------------
def deliverables(show, ep, ctx):
    """The files an episode delivers, in order, with what is on disk now."""
    prof = show["profile"]; names = presets.names(show["code"], ep["number"], prof)
    out = Path(ep["output_dir"]); D = prof.get("dub_label") or prof["labels"].split(",")[0]
    want = [("before", "Picture before B-roll and the fast trim", names["clean"].replace("_final_clean.mp4", "_before.mp4")),
            ("clean", f"{D} video, no subtitles", names["clean"]),
            ("dub_subs", f"{D} video with {D} subtitles", names["dub_subs"]),
            ("en_subs", f"{D} video with English subtitles", names["en_subs"]),
            ("en_srt", "English subtitles (SRT)", names["en_srt"]),
            ("mapping", "Mapping sheet", names["mapping"])]
    files = []
    for kind, label, name in want:
        p = out / name
        files.append(dict(kind=kind, label=label, name=name, path=str(p), exists=p.exists(),
                          size=p.stat().st_size if p.exists() else None, mtime=p.stat().st_mtime if p.exists() else None))
    for kind, label, pat in (("qc", "Final QC sheet (PASS / WARN / FAIL per check)", "*_qc.xlsx"),
                             ("scene_sheet", "Scene-edit sheet (every stretch, B-roll, Claude's reasons)", "*_scene_edit.xlsx"),
                             ("run_times", "Time and Claude cost of every step", "*_run_times.md"),
                             ("groups", "Groups review sheet", "*dynamic_groups_review*.xlsx"),
                             ("lipsync", "Lip-sync review sheet", "*lipsync_review*.xlsx"),
                             ("premiere", "Premiere timeline (XML)", "*_timeline_v*.xml")):
        hits = sorted(out.glob(pat), key=lambda x: x.stat().st_mtime) if out.is_dir() else []
        if hits:
            p = hits[-1]
            files.append(dict(kind=kind, label=label, name=p.name, path=str(p), exists=True, size=p.stat().st_size,
                              mtime=p.stat().st_mtime))
    lib = Path(ep["work_dir"]) / "broll_library" / "broll_library.xlsx"     # the shot library lives in the work folder
    if lib.exists():
        files.append(dict(kind="shot_library", label="Shot library (every shot, Claude's labels, face map)", name=lib.name,
                          path=str(lib), exists=True, size=lib.stat().st_size, mtime=lib.stat().st_mtime))
    return files


def write_zip(files, dest, folder):
    """The finished files that are on disk, stored (not recompressed) in one folder inside a zip. -> how many."""
    import zipfile
    n = 0
    used = set()
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as z:
        for f in files:
            path = f.get("path")
            if not f.get("exists") or not path or not Path(path).is_file():
                continue
            name = f["name"]
            arc = f"{folder}/{name}"
            stem, suf = Path(name).stem, Path(name).suffix
            i = 2
            while arc in used:
                arc = f"{folder}/{stem}_{i}{suf}"
                i += 1
            used.add(arc)
            z.write(path, arcname=arc)
            n += 1
    return n


# ---------------- Premiere (FCP7 XML) ----------------
def _rate(fps):
    tb = round(fps); ntsc = abs(fps - tb) > 0.01
    return tb, ntsc, (tb * 1000 / 1001 if ntsc else float(tb))


def _url(path):
    return "file://localhost" + quote(str(Path(path).resolve()))


def premiere_xml(tl, name, src_video, dub_audio, src_probe, dub_seconds=None, markers=(), labels=("IT", "US"), media=None):
    """The timeline as FCP7 XML. Frames are counted at the source video's frame rate."""
    fps = (src_probe or {}).get("video", {}).get("fps") or 29.97
    w = (src_probe or {}).get("video", {}).get("width") or 1920; h = (src_probe or {}).get("video", {}).get("height") or 1080
    tb, ntsc, real = _rate(fps)
    F = lambda ms: int(round(ms / 1000 * real))
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"
    total = F(tl["total_ms"]); src_len = F((src_probe or {}).get("duration", 0) * 1000) or total * 4
    dub_len = F((dub_seconds or tl["total_ms"] / 1000) * 1000)
    vfile = (f'<file id="file-src"><name>{escape(Path(src_video).name)}</name><pathurl>{_url(src_video)}</pathurl>{rate}'
             f"<duration>{src_len}</duration><timecode>{rate}<string>00:00:00:00</string><frame>0</frame>"
             f"<displayformat>NDF</displayformat></timecode><media><video><samplecharacteristics>{rate}<width>{w}</width>"
             f"<height>{h}</height></samplecharacteristics></video><audio><channelcount>2</channelcount></audio></media></file>")
    afile = (f'<file id="file-dub"><name>{escape(Path(dub_audio).name)}</name><pathurl>{_url(dub_audio)}</pathurl>{rate}'
             f"<duration>{dub_len}</duration><media><audio><channelcount>2</channelcount></audio></media></file>")
    first_file = [True]; seen_media = set(); media = media or {}

    def media_file(iid):
        """An imported clip's own file element (full the first time, then a reference)."""
        m = media.get(iid) or {}
        if iid in seen_media:
            return f'<file id="file-{iid}"/>'
        seen_media.add(iid)
        return (f'<file id="file-{iid}"><name>{escape(m.get("name") or iid)}</name><pathurl>{_url(m.get("path") or iid)}</pathurl>'
                f"{rate}<duration>{F(m.get('duration_ms') or 0)}</duration><media><video><samplecharacteristics>{rate}"
                f"<width>{m.get('width') or w}</width><height>{m.get('height') or h}</height></samplecharacteristics></video></media></file>")

    def track(clips, tag):
        items = []
        for k, c in enumerate(sorted(clips, key=lambda c: c["t0"])):
            start, end = F(c["t0"]), F(c["t1"])     # shared boundaries round to the same frame: no slivers
            if end <= start:
                continue
            speed = (c["s1"] - c["s0"]) / max(c["t1"] - c["t0"], 1)
            # FCP7/Premiere: with Time Remap, in/out are counted on the retimed clip (out - in = end - start)
            cin = int(round(F(c["s0"]) / speed)) if speed > 0 else F(c["s0"])
            cout = cin + (end - start)
            label = f"{labels[0]}#{c['row']}" if c.get("row") is not None else c["id"]
            remap = ""
            if abs(speed - 1) > 0.0005:
                remap = ("<filter><effect><name>Time Remap</name><effectid>timeremap</effectid><effectcategory>motion</effectcategory>"
                         "<effecttype>motion</effecttype><mediatype>video</mediatype>"
                         '<parameter authoringApp="PremierePro"><parameterid>variablespeed</parameterid><name>variablespeed</name>'
                         "<valuemin>0</valuemin><valuemax>1</valuemax><value>0</value></parameter>"
                         '<parameter authoringApp="PremierePro"><parameterid>speed</parameterid><name>speed</name>'
                         f"<valuemin>-100000</valuemin><valuemax>100000</valuemax><value>{speed * 100:.4f}</value></parameter>"
                         '<parameter authoringApp="PremierePro"><parameterid>reverse</parameterid><name>reverse</name><value>FALSE</value></parameter>'
                         '<parameter authoringApp="PremierePro"><parameterid>frameblending</parameterid><name>frameblending</name><value>FALSE</value></parameter>'
                         "</effect></filter>")
            note = f"{c.get('dub_text', '')} | {labels[1]} {tlm.fmt(c['s0'])}: {c.get('src_text', '')}"
            own = c.get("media")
            mlen = F((media.get(own) or {}).get("duration_ms") or 0) if own else src_len
            dur = int(round(mlen / speed)) if speed > 0 else mlen            # the retimed media's length
            items.append(f'<clipitem id="{tag}-{k + 1}"><name>{escape(label)}</name><enabled>TRUE</enabled>'
                         f"<duration>{dur}</duration>{rate}<start>{start}</start><end>{end}</end><in>{cin}</in><out>{cout}</out>"
                         + (media_file(own) if own else vfile if first_file[0] else '<file id="file-src"/>') + remap
                         + f"<comments><mastercomment1>{escape(note[:400])}</mastercomment1></comments>"
                         + ("<labels><label2>Rose</label2></labels>" if c.get("locked") else "") + "</clipitem>")
            if not own:
                first_file[0] = False
        return "<track>" + "".join(items) + "</track>"

    vtracks = track(tl["clips"], "clip") + (track(tl["v2"], "v2clip") if tl.get("v2") else "")
    audio = (f'<clipitem id="dub-1"><name>{escape(Path(dub_audio).name)}</name><enabled>TRUE</enabled><duration>{dub_len}</duration>'
             f"{rate}<start>0</start><end>{total}</end><in>0</in><out>{total}</out>{afile}"
             "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>")
    marks = "".join(f"<marker><name>{escape(('QC ' if m.get('source', 'QC') == 'QC' else '') + (m.get('text') or ''))[:200]}</name>"
                    f"<comment>{'resolved' if m.get('resolved') else 'open'}</comment><in>{F(m['t_ms'])}</in><out>-1</out></marker>"
                    for m in markers)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
            f"<sequence id=\"seq-1\"><name>{escape(name)}</name><duration>{total}</duration>{rate}"
            f"<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>{marks}"
            f"<media><video><format><samplecharacteristics>{rate}<width>{w}</width><height>{h}</height>"
            f"<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>"
            f"{vtracks}</video>"
            f"<audio><numOutputChannels>2</numOutputChannels><track>{audio}</track></audio></media></sequence></xmeml>\n")
