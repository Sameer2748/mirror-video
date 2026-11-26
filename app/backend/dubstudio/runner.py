"""Job runner: runs each episode's step graph in background threads, with resource slots, live progress,
cancel / retry, and resume after a restart (the pipeline tools continue where they stopped)."""
import json, os, re, shutil, signal, subprocess, sys, threading, time, traceback
from pathlib import Path
from .config import cfg
from .db import now
from .events import bus
from .pipeline import steps as pipeline_steps, decisions as dec
from . import cloud, deliver, envfile, scan, storage, timeline

DONE = {"done", "skipped"}
ACTIVE = {"running"}
# Website-disk misses after R2-only fetch. Retry ourselves — do not ask for Process or a Retry click.
WORK_RETRY_STEPS = {"translate", "merged", "sharpness", "decisions", "facemap", "analyze", "lip_mesh",
                    "mksrt_src", "mksrt_dub", "proxy", "lip_gpu", "words_src", "words_dub", "sentences_src"}
WORK_RETRY_MARKERS = ("config.json", "prep.json", "_unpack_tars/", "matching files", "lips_nets",
                      "ranges.json", "previous step", "No space left", "claude_sentences.py")
PROG_PATTERNS = [
    re.compile(r"(\d+)/(\d+) (?:chunks|segments|pieces|rows|sentences)\b"),
    re.compile(r"rendered (\d+)/(\d+)"),
    re.compile(r"chunk (\d+) done.*?of (\d+)"),
]
TIME_PAT = re.compile(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)(?:s\b| in\b)")   # "12.0-27.5s", lipgpu "done 45-60 in 23 s"
NOISE = re.compile(r"Warning:|^\s+torch\.")   # library warnings: kept in the log, not shown as the last line
PIECE_PAT = re.compile(r"piece\s+(\d+)\s*/\s*(\d+)", re.I)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Leftover editor/scene files from a previous run. Groups must snapshot a new
# grouping.json; scene edit / the editor / render must not keep the old cut.
STALE_EDIT_FILES = (
    "manifest_groups.grouping.json",
    "manifest_groups.auto.json",
    "manifest_groups.pipeline.json",
    "manifest_groups.before_fast_trim.json",
    "timeline_draft.json",
    "review_ok.json",
)


def drop_stale_edit_files(work):
    """Forget the previous run's grouping snapshot, automatic edit, editor draft, and render gate."""
    W = Path(work)
    dropped = []
    for name in STALE_EDIT_FILES:
        p = W / name
        if p.exists():
            p.unlink()
            dropped.append(name)
    ss = W / "scene_slowfix"
    if ss.is_dir():
        for p in ss.iterdir():
            if p.is_file():
                p.unlink()
                dropped.append(f"scene_slowfix/{p.name}")
    return dropped


KEEP_WORK_ON_SCRATCH = {"input"}
RENDER_CACHE_DIRS = ("segs", "segs_groups", "segs_groups.auto", "bookends")
RENDER_CACHE_FILES = (
    "video_groups_nosubs.mp4", "silent_groups.mp4", "concat_groups.txt",
    "silent_groups.auto.mp4", "concat_groups.auto.txt", "clean_noglow.mp4",
)


def reset_work_for_new_run(work):
    """Process again from the first step: drop every leftover of the previous run except input files."""
    W = Path(work)
    if not W.is_dir():
        return []
    dropped = []
    for p in list(W.iterdir()):
        if p.name in KEEP_WORK_ON_SCRATCH:
            continue
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
        dropped.append(p.name)
    return dropped


def clear_output_dir(output_dir):
    """Process again: finished files of the previous run must not sit next to the new ones."""
    O = Path(output_dir) if output_dir else None
    if not O or not O.is_dir():
        return []
    dropped = []
    for p in list(O.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
        dropped.append(p.name)
    return dropped


def drop_previous_render(work, output_dir=None):
    """Rebuild picture + transitions from the current edit list; do not concat leftover segment files."""
    W = Path(work)
    dropped = []
    for name in RENDER_CACHE_DIRS:
        p = W / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            dropped.append(name)
    for name in RENDER_CACHE_FILES:
        p = W / name
        if p.exists():
            p.unlink()
            dropped.append(name)
    if output_dir:
        O = Path(output_dir)
        for p in O.glob("*_before.mp4"):
            p.unlink(missing_ok=True)
            dropped.append(p.name)
        for p in O.glob("*_timeline_v*.xml"):
            p.unlink(missing_ok=True)
            dropped.append(p.name)
    return dropped


def snapshot_latest_groups(work):
    """Point scene edit + the editor at this groups output (overwrite any leftover snapshot)."""
    if not work:
        return False
    W = Path(work)
    g = W / "manifest_groups.json"
    if not g.is_file():
        return False
    try:
        data = json.loads(g.read_text(encoding="utf-8"))
    except Exception:
        return False
    if data.get("dub_studio"):
        return False
    shutil.copy(g, W / "manifest_groups.grouping.json")
    return True


def is_key_limit_error(exc):
    """OpenRouter refused the key because its spend cap is used up.

    Not an empty Claude reply (those stay failed). The job can be asked once more after the cap is raised."""
    s = str(exc or "")
    return "Key limit exceeded" in s or "openrouter_key_limit" in s


class Runner:
    def __init__(self, db):
        self.db = db
        self.procs = {}             # job id -> Popen
        self.cancelled = set()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.wake = threading.Event()
        self.awake = None           # caffeinate while steps run (the Mac must not sleep mid-render)
        self._cleanup_busy = False
        self._cleanup_thread = None
        self._file_wait_at = {}
        self._retried_producer = set()

    # ---------- lifecycle ----------
    def start(self):
        for j in self.db.q("SELECT id, log_path FROM jobs WHERE status IN ('running','needs_input')"):
            self._stop_orphan(j["log_path"])
            self.db.update("jobs", j["id"], status="waiting", message="resumed after a restart")
        self._resume_link_imports()
        self._enforce_cap()
        self.thread = threading.Thread(target=self._loop, name="dubstudio-runner", daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set(); self.wake.set(); self._keep_awake(False)
        with self.lock:
            for jid, p in list(self.procs.items()):
                self._kill(p)

    def _keep_awake(self, on):
        """macOS: `caffeinate -i` while a step runs, so a long render survives the Mac's idle sleep; stopped when
        nothing runs (an episode waiting for review does not keep the Mac awake)."""
        if on and self.awake is None and sys.platform == "darwin" and shutil.which("caffeinate"):
            try:
                self.awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            except Exception:
                self.awake = None
        elif not on and self.awake is not None:
            try:
                self.awake.terminate()
            except Exception:
                pass
            self.awake = None

    @staticmethod
    def _stop_orphan(log_path):
        """After a crash the step's process may still run (it has its own process group): stop it before the step
        starts again, so two copies never write the same files. Only a pipeline command is ever touched."""
        pidf = Path(str(log_path) + ".pid")
        try:
            pid = int(pidf.read_text().strip())
        except Exception:
            return
        try:
            cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
            if cmd.strip() and str(cfg.scripts) in cmd:
                os.killpg(pid, signal.SIGTERM)
                for _ in range(50):
                    time.sleep(0.1)
                    if not subprocess.run(["ps", "-p", str(pid)], capture_output=True).stdout.count(b"\n") > 1:
                        break
                else:
                    os.killpg(pid, signal.SIGKILL)
        except Exception:
            pass
        pidf.unlink(missing_ok=True)

    # ---------- planning ----------
    def episode_context(self, ep_id):
        ep = self.db.one("SELECT * FROM episodes WHERE id=?", (ep_id,))
        show = self.db.one("SELECT * FROM shows WHERE id=?", (ep["show_id"],))
        assets = {a["kind"]: a["path"] for a in self.db.q("SELECT * FROM assets WHERE episode_id=? AND role='input'", (ep_id,))}
        for a in self.db.q("SELECT kind, probe FROM assets WHERE episode_id=? AND role='input'", (ep_id,)):
            if not isinstance(a["probe"], dict):
                continue
            if a["kind"] == "src_video":
                dur = scan.picture_duration(a["probe"])
            else:
                dur = a["probe"].get("duration")
            if dur:
                assets[f"_dur_{a['kind']}"] = dur
        return show, ep, assets

    def create_run(self, ep_id):
        show, ep, assets = self.episode_context(ep_id)
        missing = [k for k in ("src_video", "dub_audio") if not assets.get(k)]
        if missing:
            raise ValueError(f"missing input files: {', '.join(missing)}")
        Path(ep["work_dir"]).mkdir(parents=True, exist_ok=True)
        Path(ep["output_dir"]).mkdir(parents=True, exist_ok=True)
        reset_work_for_new_run(ep["work_dir"])
        clear_output_dir(ep["output_dir"])
        try:
            storage.drop_cloud_work(show, ep)
        except Exception:
            pass
        ctx, plan = pipeline_steps.plan(show, ep, assets)
        with self.lock:
            run = (self.db.one("SELECT MAX(run) AS r FROM jobs WHERE episode_id=?", (ep_id,))["r"] or 0) + 1
            rt = Path(ep["work_dir"]) / "run_times.tsv"         # a new run starts its own time + cost table
            if rt.exists():
                rt.rename(rt.with_name(f"run_times.before_run{run}.tsv"))
            for k, s in enumerate(plan):
                self.db.insert("jobs", episode_id=ep_id, run=run, step=s["step"], ord=k, status="waiting",
                               needs=s["needs"], resource=s["resource"],
                               params={"cmd": s["cmd"], "fn": s["fn"], "label": s["label"], "duration": s["duration"],
                                       "ai": s.get("ai", False), "ctx": ctx},
                               log_path=str(Path(ep["work_dir"]) / "logs" / f"run{run}_{k:02d}_{s['step']}.log"),
                               created=now())
            self.db.update("episodes", ep_id, status="processing", queued=None, resume=None, updated=now())
        bus.publish("run_created", ep_id, run=run, steps=len(plan))
        self.wake.set()
        return run

    def queue_in_order(self, ids, from_scratch=()):
        """Put these episodes in the line in this order. `from_scratch` drops the old run and starts
        Process again when the slot opens (WBT 101-200 failed/processing/cancelled)."""
        scratch = set(from_scratch or ())
        base = now()
        lined = set()
        for i, eid in enumerate(ids):
            ep = self.db.one("SELECT * FROM episodes WHERE id=?", (eid,))
            if not ep:
                continue
            lined.add(eid)
            if eid in scratch:
                if ep["status"] in ("processing", "queued", "failed", "cancelled"):
                    self.cancel_run(eid)
                self.db.update("episodes", eid, status="queued", queued=base + i * 0.01,
                               resume=None, updated=now())
                bus.publish("episode_status", eid, status="queued")
                continue
            if ep["status"] == "processing":
                continue
            resume = 1 if self.latest_run(eid) else None
            self.db.update("episodes", eid, status="queued", queued=base + i * 0.01,
                           resume=resume, updated=now())
            bus.publish("episode_status", eid, status="queued")
        extra = base + len(ids) + 1
        for row in self.db.q("SELECT id FROM episodes WHERE status='processing'"):
            if row["id"] in lined:
                continue
            self._park(row["id"], queued_at=extra)
            extra += 0.01
        for row in self.db.q("SELECT id, resume FROM episodes WHERE status='queued'"):
            if row["id"] in lined:
                continue
            self.db.update("episodes", row["id"], queued=extra, updated=now())
            extra += 0.01
        self.start_next()
        return {"lined": len(ids), "from_scratch": len(scratch)}

    # ---------- the queue: episodes take their turn (docs/09 §4) ----------
    @staticmethod
    def max_active():
        """How many episodes are worked on at the same time. 1 = one by one (the default).

        When DUBSTUDIO_FILL_CPUS is on, the cap is at least cpu_slots so queued episodes
        start instead of 20 boxes sitting idle (P8). Tests leave the flag off."""
        n = max(1, envfile.number("MAX_ACTIVE_EPISODES", 1))
        if not envfile.flag("DUBSTUDIO_FILL_CPUS", False):
            return n
        slots = int(getattr(cfg, "cpu_slots", 0) or 0)
        return max(n, slots) if slots else n

    def active_episodes(self):
        """Episodes using the machine now. An episode parked at a gate (Check matching, Ready for review) is not:
        it is waiting for a person, so the next episode in the line may start."""
        return [r["id"] for r in self.db.q("SELECT id FROM episodes WHERE status='processing'")]

    def submit(self, ep_id):
        """Press Start processing: begin now if the machine is free, otherwise wait in the line."""
        show, ep, assets = self.episode_context(ep_id)
        missing = [k for k in ("src_video", "dub_audio") if not assets.get(k)]
        if missing:
            raise ValueError(f"missing input files: {', '.join(missing)}")
        if not self._room_for(ep_id):
            # Cap full: if this episode already has a run in flight, wait and continue it.
            # resume=None here used to mean "Process again when a slot opens" and wiped
            # verify/render work (IMTS IT-DE 23/35). First Process (no open run) still
            # starts a new run when the slot opens.
            resume = 1 if self._run_still_open(ep_id) else None
            self.db.update("episodes", ep_id, status="queued", queued=now(), resume=resume, updated=now())
            bus.publish("episode_status", ep_id, status="queued")
            pos = self.queue_position(ep_id)
            return {"queued": True, "position": pos, "message": f"in the line: {pos} ahead of this one"
                    if pos else "next in the line"}
        run = self.create_run(ep_id)
        return {"queued": False, "run": run, "position": 0}

    def queue_position(self, ep_id):
        row = self.db.one("SELECT queued FROM episodes WHERE id=?", (ep_id,))
        if not row or not row.get("queued"):
            return 0
        n = self.db.one("SELECT COUNT(*) AS n FROM episodes WHERE status='queued' AND queued < ?", (row["queued"],))
        return (n or {}).get("n", 0) + 1

    def _room_for(self, ep_id):
        """True when this episode may be one of the max_active episodes being worked on."""
        active = [e for e in self.active_episodes() if e != ep_id]
        return len(active) < self.max_active()

    def _park(self, ep_id, queued_at=None):
        """The cap is full. Leave this episode's jobs as they are and wait for a free slot.

        resume=1 tells start_next to continue this run. A new run would wipe the episode."""
        row = self.db.one("SELECT queued FROM episodes WHERE id=?", (ep_id,))
        at = queued_at if queued_at is not None else ((row or {}).get("queued") or now())
        self.db.update("episodes", ep_id, status="queued", queued=at, resume=1, updated=now())
        bus.publish("episode_status", ep_id, status="queued")

    def _enforce_cap(self):
        """Only max_active episodes stay on Processing. The rest wait in the line, same run.

        An episode already on a machine keeps its slot. The free slots go to the lowest episode
        numbers. A restart puts every job back to waiting first, so this also parks the overflow
        before any of them is sent out again."""
        cap = self.max_active()
        rows = self.db.q("SELECT id, number FROM episodes WHERE status='processing' ORDER BY number, id")
        if len(rows) <= cap:
            return
        running = {r["episode_id"] for r in self.db.q(
            "SELECT DISTINCT episode_id FROM jobs WHERE status='running'")}
        keep_running = [r for r in rows if r["id"] in running]
        rest = [r for r in rows if r["id"] not in running]
        room = max(0, cap - len(keep_running))
        overflow = rest[room:]
        if not overflow:
            return
        # P4: do not park at queued=now (back of WBT/TGS). Sit in front of the existing line.
        ahead = self.db.one("SELECT MIN(queued) AS q FROM episodes WHERE status='queued' AND queued IS NOT NULL")
        front = (ahead["q"] - len(overflow) * 0.001) if ahead and ahead.get("q") is not None else now()
        for i, r in enumerate(overflow):
            self._park(r["id"], queued_at=front + i * 0.001)

    def _run_still_open(self, ep_id):
        """True when the latest run still has a step to do (waiting / running / needs a person).

        Those jobs must not be wiped by create_run just because the episode sat in the line."""
        run = self.latest_run(ep_id)
        if not run:
            return False
        return bool(self.db.one(
            "SELECT id FROM jobs WHERE episode_id=? AND run=? AND status IN ('waiting','running','needs_input')",
            (ep_id, run)))

    def start_next(self):
        """A slot came free: take the oldest episode out of the line (never more than max_active at once).

        An episode queued from Render or Retry already has its jobs (resume=1): set it to processing.
        Same if Process hit a full cap while that run was still open: continue, do not wipe.
        An episode with no open run (first Process, or from_scratch after cancel) starts a new run."""
        while len(self.active_episodes()) < self.max_active():
            nxt = self.db.one("SELECT * FROM episodes WHERE status='queued' ORDER BY queued, id LIMIT 1")
            if not nxt:
                return
            if nxt.get("resume") or self._run_still_open(nxt["id"]):
                self.db.update("episodes", nxt["id"], status="processing", queued=None, resume=None, updated=now())
                bus.publish("episode_status", nxt["id"], status="processing")
                self.wake.set()
                continue
            try:
                try:
                    storage.free_disk(Path(nxt["work_dir"]), log=lambda m: None, db=self.db,
                                      keep_episode_id=nxt["id"])
                except Exception:
                    pass
                self.create_run(nxt["id"])
                self.db.update("episodes", nxt["id"], queued=None, resume=None)
            except Exception as ex:
                self.db.update("episodes", nxt["id"], status="failed", queued=None, resume=None, note=str(ex)[:200])
                bus.publish("episode_status", nxt["id"], status="failed")

    def queue_view(self, db=None):
        d = db or self.db
        rows = d.q("SELECT e.id, e.number, e.status, e.queued, s.code, s.name, s.owner_id FROM episodes e "
                   "JOIN shows s ON s.id=e.show_id WHERE e.status IN ('queued','processing') ORDER BY e.queued, e.id")
        active = set(self.active_episodes())
        out = []
        for r in rows:
            run = self.latest_run(r["id"])
            jobs = d.q("SELECT step, status, progress, params, message FROM jobs WHERE episode_id=? AND run=? ORDER BY ord",
                       (r["id"], run)) if run else []
            from .visits import live_for_jobs
            live = live_for_jobs(jobs)
            from .visits import display_status
            done = sum(1 for j in jobs if j["status"] in ("done", "skipped"))
            out.append({**r, "working": r["id"] in active, "step": live["step"],
                        "step_label": live["label"], "step_message": live["message"],
                        "display_status": display_status(r["status"], jobs),
                        "steps_done": done, "steps": len(jobs),
                        "progress": round(done / len(jobs), 3) if jobs else 0})
        for i, e in enumerate([x for x in out if x["status"] == "queued"], start=1):
            e["position"] = i
        return {"max_active": self.max_active(), "working": [e for e in out if e["working"]],
                "waiting": [e for e in out if e["status"] == "queued"]}

    def latest_run(self, ep_id):
        r = self.db.one("SELECT MAX(run) AS r FROM jobs WHERE episode_id=?", (ep_id,))
        return r["r"] if r else None

    # ---------- control ----------
    def cancel(self, job_id):
        with self.lock:
            self.cancelled.add(job_id)
            p = self.procs.get(job_id)
            if p:
                self._kill(p)
        j = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if j and j["status"] in ("waiting", "needs_input", "running", "blocked"):
            self._set(j, status="cancelled", finished=now(), message="cancelled")

    def cancel_run(self, ep_id):
        run = self.latest_run(ep_id)
        for j in self.db.q("SELECT id FROM jobs WHERE episode_id=? AND run=? AND status NOT IN ('done','skipped')", (ep_id, run)):
            self.cancel(j["id"])

    def _refresh_params(self, job_ids, ep_id):
        """A retried step runs today's command (a config or plan fix since the run was made applies), same step name."""
        try:
            show, ep, assets = self.episode_context(ep_id)
            ctx, plan = pipeline_steps.plan(show, ep, assets)
        except Exception:
            return
        by_step = {s["step"]: s for s in plan}
        for jid in job_ids:
            j = self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))
            s = by_step.get(j["step"]) if j else None
            if s:
                self.db.update("jobs", jid, params={"cmd": s["cmd"], "fn": s["fn"], "label": s["label"],
                                                    "duration": s["duration"], "ai": s.get("ai", False), "ctx": ctx})

    def retry(self, job_id):
        j = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not j or j["status"] in ACTIVE:
            return
        self.cancelled.discard(job_id)
        down = self.db.q("SELECT * FROM jobs WHERE episode_id=? AND run=? AND status IN ('cancelled','blocked')",
                         (j["episode_id"], j["run"]))
        self._refresh_params([job_id] + [d["id"] for d in down], j["episode_id"])
        self._set(j, status="waiting", attempt=(j["attempt"] or 0) + 1, message="retry", progress=0, finished=None)
        # downstream cancelled/failed jobs wait again too
        for d in down:                      # a new attempt: an old waiting loop of that step stops (_stale)
            self._set(d, status="waiting", attempt=(d["attempt"] or 0) + 1, message="")
        if self._room_for(j["episode_id"]):
            self.db.update("episodes", j["episode_id"], status="processing", queued=None, resume=None, updated=now())
            self.wake.set()
        else:
            self._park(j["episode_id"])

    def poke(self):
        self.wake.set()

    @staticmethod
    def missing_work_retry(step, message, attempt=0):
        """True when the step died because config.json / prep.json / an unpack tar was not on this box."""
        if step not in WORK_RETRY_STEPS:
            return False
        cap = 5 if step == "decisions" else 2
        if (attempt or 0) >= cap:
            return False
        text = str(message or "")
        return any(m in text for m in WORK_RETRY_MARKERS)

    def _recover_missing_work(self):
        """Failed translate/merged/sharpness from a missing work file: fetch from R2 and run again."""
        rows = self.db.q(
            "SELECT * FROM jobs WHERE (status='failed' AND step IN "
            "('translate','merged','sharpness','mksrt_dub','sentences_src','analyze')) "
            "OR (status='needs_input' AND step='decisions')")
        for j in rows:
            if not self.missing_work_retry(j["step"], j.get("message"), j.get("attempt")):
                continue
            try:
                show, ep, _ = self.episode_context(j["episode_id"])
                storage.fetch_work_files(show, ep, force=True)
            except Exception:
                pass
            self.retry(j["id"])

    @staticmethod
    def _reached_late(jobs):
        """True when a run already got to review/render (the work a from-scratch Process again would throw away)."""
        late = {"review", "render", "verify", "bookends", "transitions", "subs_dub", "subs_en", "check"}
        return any(j["step"] in late and j["status"] in DONE | {"failed"} for j in jobs)

    def abandon_scratch_run(self, ep_id):
        """Drop a Process-again run that restarted from the top, and retry the failed step of the previous run.

        Latest run is latest by number; retrying a job on the old run while the new one exists marks it
        superseded. So the new jobs have to go."""
        latest = self.latest_run(ep_id)
        if not latest or latest < 2:
            raise ValueError("this episode has no previous run to go back to")
        latest_jobs = self.db.q("SELECT * FROM jobs WHERE episode_id=? AND run=? ORDER BY ord", (ep_id, latest))
        prev_jobs = self.db.q("SELECT * FROM jobs WHERE episode_id=? AND run=? ORDER BY ord", (ep_id, latest - 1))
        if not prev_jobs:
            raise ValueError("this episode has no previous run to go back to")
        if not self._reached_late(prev_jobs):
            raise ValueError("the previous run never reached review or render")
        if self._reached_late(latest_jobs):
            raise ValueError("the latest run already reached review or render; it is not a from-scratch restart")
        self.cancel_run(ep_id)
        self.db.ex("DELETE FROM jobs WHERE episode_id=? AND run=?", (ep_id, latest))
        failed = next((j for j in prev_jobs if j["status"] == "failed"), None)
        if failed:
            self.retry(failed["id"])
            return {"restored_run": latest - 1, "retried": failed["step"]}
        self._episode_status()
        self.wake.set()
        return {"restored_run": latest - 1, "retried": None}

    def retry_check_without_rerender(self, ep_id):
        """After translate has rewritten config.json, run check only. The videos from the failed tail stay."""
        run = self.latest_run(ep_id)
        if not run:
            raise ValueError("this episode has not been processed")
        jobs = {j["step"]: j for j in self.db.q(
            "SELECT * FROM jobs WHERE episode_id=? AND run=? ORDER BY ord", (ep_id, run))}
        tr, chk = jobs.get("translate"), jobs.get("check")
        if not tr or not chk:
            raise ValueError("this episode has no translate or check step")
        if tr["status"] != "done":
            raise ValueError("translate has not finished yet")
        if any((jobs.get(s) or {}).get("status") == "running" for s in cloud.CHAIN):
            raise ValueError("a render tail is still running")
        for step in ("render", "verify", "bookends", "transitions", "subs_dub", "subs_en", "scene_sheet", "qc"):
            sj = jobs.get(step)
            if sj and sj["status"] not in DONE:
                self._set(sj, status="done", progress=1, finished=now(),
                          message="kept the videos already made; only the translated SRT is checked again")
        self.retry(chk["id"])
        return {"retried": "check"}

    # ---------- link sheet: one download at a time, never a pipeline CPU ----------
    def _resume_link_imports(self):
        """A restart kills the download thread and leaves its row running, which then blocks every later row."""
        try:
            stuck = self.db.q("SELECT id FROM link_imports WHERE status='running'")
        except Exception:
            return
        for row in stuck:
            self.db.update("link_imports", row["id"], status="waiting", message="resumed after a restart")

    def _tick_link_imports(self):
        """Bring sheet links down on a private slot.

        The pipeline's cpu_slots are not touched. One download runs, the next waits, and only then does
        the episode join the normal processing line. A newer sheet row for the same episode replaces an
        older one that has not started."""
        try:
            if self.db.one("SELECT id FROM link_imports WHERE status='running'"):
                return
            waiting = self.db.q("SELECT * FROM link_imports WHERE status='waiting' ORDER BY id")
        except Exception:
            return
        if not waiting:
            return
        newest = {}
        for row in waiting:
            newest[row["episode_id"]] = row["id"]
        for row in waiting:
            if row["id"] != newest.get(row["episode_id"]):
                self.db.update("link_imports", row["id"], status="failed", finished=now(),
                               message="replaced by a newer sheet row")
        row = next(r for r in waiting if r["id"] == newest.get(r["episode_id"]))
        self.db.update("link_imports", row["id"], status="running", started=now(), message="bringing the files down")
        threading.Thread(target=self._run_link_import, args=(row["id"],), daemon=True).start()

    def _run_link_import(self, iid):
        from . import linksheet
        row = self.db.one("SELECT * FROM link_imports WHERE id=?", (iid,))
        if not row:
            return
        try:
            ep = self.db.one("SELECT * FROM episodes WHERE id=?", (row["episode_id"],))
            show = self.db.one("SELECT * FROM shows WHERE id=?", (row["show_id"],))
            for kind, url in (("src_video", row["src_url"]), ("dub_audio", row["audio_url"]),
                              ("dub_script", row["script_url"])):
                self.db.update("link_imports", iid, message=f"downloading the {kind.replace('_', ' ')}")
                path = linksheet.fetch(url, Path(ep["work_dir"]) / "input" / kind, kind)
                info, problems = scan.check_asset(kind, str(path))
                if problems:
                    raise RuntimeError("; ".join(problems))
                key = None
                if storage.configured():
                    key = storage.input_key(show, ep, kind, path.name)
                    storage.put_big_file(path, key)
                self.db.ex("DELETE FROM assets WHERE episode_id=? AND kind=? AND role='input'", (ep["id"], kind))
                self.db.insert("assets", episode_id=ep["id"], kind=kind, path=str(path), role="input",
                               size=path.stat().st_size, r2_key=key, origin="r2" if key else "local",
                               uploaded=now(), fingerprint=(info or {}).get("fingerprint"),
                               mtime=(info or {}).get("mtime"), probe=(info or {}).get("probe"), created=now())
                # Copier done: video/audio live in R2. Drop the local master so the website disk stays empty.
                if key and kind in storage.MEDIA_KINDS:
                    path.unlink(missing_ok=True)
            self.submit(ep["id"])
            self.db.update("link_imports", iid, status="done", finished=now(),
                           message="files are in; the episode joined the processing line")
        except Exception as ex:
            self.db.update("link_imports", iid, status="failed", finished=now(), message=str(ex)[:300])
        self.wake.set()

    # ---------- loop ----------
    def _loop(self):
        while not self.stop.is_set():
            try:
                self._tick()
            except Exception:
                traceback.print_exc()
            self.wake.wait(0.5); self.wake.clear()

    def _maybe_daily_cleanup(self):
        """Once a day, in a side thread so packing a job is not blocked: leftover tars, segs, and
        local copies of finished episodes that are already in R2. If the disk is already tight,
        do it now so the next queued episode can fetch."""
        if self._cleanup_busy:
            return
        tight = False
        try:
            tight = shutil.disk_usage(cfg.library).free < storage.TIGHT_FREE
        except OSError:
            pass
        if not tight and not storage.cleanup_due(cfg.library):
            return
        self._cleanup_busy = True

        def run():
            try:
                busy = [r["episode_id"] for r in self.db.q(
                    "SELECT DISTINCT episode_id FROM jobs WHERE status='running'")]
                # Under 5 GB, only folders of jobs that are actually running. Keeping every
                # processing/queued folder is how Render hit ENOSPC and the next steps lost their files.
                try:
                    room = shutil.disk_usage(cfg.library).free
                except OSError:
                    room = 0
                if room >= 5 * 10**9:
                    busy += [r["id"] for r in self.db.q("SELECT id FROM episodes WHERE status='processing'")]
                out = storage.maybe_daily_cleanup(self.db, cfg.library, keep_episode_ids=busy, force=tight)
                parked = storage.free_parked_media(self.db, keep_episode_ids=busy)
                freed = (out or {}).get("freed") or 0
                n = (out or {}).get("episodes") or 0
                if parked:
                    freed += parked.get("freed") or 0
                    n += parked.get("episodes") or 0
                if freed or n:
                    print(f"disk cleanup: {freed / 1e9:.2f} GB, {n} parked episodes")
            except Exception:
                traceback.print_exc()
            finally:
                self._cleanup_busy = False

        self._cleanup_thread = threading.Thread(target=run, daemon=True, name="disk-cleanup")
        self._cleanup_thread.start()

    def _tick(self):
        self._maybe_daily_cleanup()
        self._tick_link_imports()
        self._recover_missing_work()
        self._enforce_cap()
        # Queued episodes with no waiting jobs (from_scratch, or a cancelled leftover) never
        # entered this tick. Fill free slots so idle factory boxes are used.
        self.start_next()
        queued = {r["id"] for r in self.db.q("SELECT id FROM episodes WHERE status='queued'")}
        jobs = self.db.q("SELECT * FROM jobs WHERE status IN ('waiting','running','needs_input','blocked') ORDER BY episode_id, run, ord")
        self._keep_awake(any(j["status"] == "running" for j in jobs))     # not for steps parked behind the review
        if not jobs:
            self._episode_status()
            return
        busy = {"cpu": 0, "gpu": 0}
        packing = 0
        for j in jobs:
            if j["status"] != "running":
                continue
            msg = j.get("message") or ""
            if (msg == "starting" or "sending the files" in msg) and cloud.holds_cpu_machine(j["step"]):
                packing += 1
            # P8: a job already on RunPod must not fill cpu_slots. Those slots are for new sends
            # when the queue is waiting and boxes are idle.
            if j["resource"] == "cpu" and cloud.waiting_on_rented_cpu(msg):
                continue
            busy[j["resource"]] = busy.get(j["resource"], 0) + 1
        slots = {"cpu": cfg.cpu_slots, "gpu": cfg.gpu_slots}
        chain_busy = {o["episode_id"] for o in jobs if o["status"] == "running" and o["step"] in cloud.CHAIN}
        cloud_busy = {o["episode_id"] for o in jobs if o["status"] == "running" and cloud.holds_cpu_machine(o["step"])}
        cloud_load = sum(cloud.cpu_job_weight(o["step"]) for o in jobs
                         if o["status"] == "running" and cloud.holds_cpu_machine(o["step"]))
        # How many new CPU visits we pack on this website tick. 4 left ~17 RunPod boxes idle
        # while episodes sat on "retry". 12 fills a 30-worker pool without dumping 30 tars at once (P5).
        PACK_AT_ONCE = max(1, envfile.number("DUBSTUDIO_PACK_AT_ONCE", 12))
        # Face map is per episode: work tar + video + broll_library live under that episode's folder.
        # The GPU worker wipes /work /out /files before each job, so a reused machine cannot see the last ep.
        # gpu_slots (not show) is the cap — several IMTS face maps may run at once.
        for j in jobs:
            if j["status"] == "running" and (j.get("message") or "") == "starting" and j.get("started") \
                    and now() - j["started"] > 120:
                # The worker thread died before it logged anything (TGS ep31 reading view). Queue it again.
                self._set(j, status="waiting", progress=0, message="the worker thread died; trying again")
                continue
            if j["status"] in ("running", "needs_input"):
                continue
            latest = self.latest_run(j["episode_id"])
            if j["run"] != latest:
                self._set(j, status="cancelled", message="superseded by a newer run"); continue
            deps = self.db.q(f"SELECT step,status FROM jobs WHERE episode_id=? AND run=? AND step IN "
                             f"({','.join('?' * len(j['needs']))})", [j["episode_id"], j["run"], *j["needs"]]) if j["needs"] else []
            if any(d["status"] in ("failed", "cancelled", "blocked") for d in deps):
                if j["status"] != "blocked":
                    self._set(j, status="blocked", message="waiting for a failed step")
                continue
            if j["status"] == "blocked":
                # The step it was waiting on has finished. Start it; nobody has to press Retry.
                self._set(j, status="waiting", message="")
            if any(d["status"] not in DONE for d in deps):
                continue
            # Previous job "done" is not enough: the files it wrote must be on this box (or in R2).
            if not self._work_ready_to_start(j):
                continue
            # Queued episodes already have their jobs armed. Starting one here would ignore the cap.
            if j["episode_id"] in queued:
                continue
            # Captions only need the source video, so they would start during matching. On the cut visit
            # they ride with the sheet instead, one machine, after the decisions.
            if j["step"] == "captions" and cloud.stretch("cut"):
                continue
            if cloud.stretch("match") and j["step"] in cloud.SCRIPT_CHAIN:
                rows = self._jobs_by_step(j)
                first = next((s for s in cloud.SCRIPT_CHAIN
                              if (rows.get(s) or {}).get("status") not in DONE | {None}), None)
                if first and j["step"] != first:
                    continue
                if first == "translate" and self._translate_needs_dynsrt(rows.get("translate") or j):
                    # Dynamic SRT is made on the render tail. Do not start a match visit for it.
                    if (rows.get("render") or {}).get("status") not in DONE:
                        continue
            if cloud.stretch("scene") and j["step"] in cloud.SCENE_CHAIN and j["step"] != "scene_edit":
                continue
            if cloud.chain_wanted() and j["step"] in ("dynsrt", "scene_sheet", "qc", "check"):
                # First pass rides in render_tail. A Retry of QC/check after render is already
                # done must still rent a box — otherwise the episode sits "waiting" with idle CPUs.
                by = self._jobs_by_step(j)
                if (by.get("render") or {}).get("status") not in DONE:
                    continue
            # The render, the transitions and the subtitle burns are one visit. Starting a second
            # one while the first is still packing its files deletes the upload out from under it.
            if cloud.chain_wanted() and j["step"] in cloud.CHAIN:
                if j["episode_id"] in chain_busy:
                    continue
                chain_busy.add(j["episode_id"])
            # One rented CPU send per episode. Fill up to cpu_slots of real boxes (lip = 8 each).
            if cloud.holds_cpu_machine(j["step"]):
                if j["episode_id"] in cloud_busy:
                    continue
                weight = cloud.cpu_job_weight(j["step"])
                if cloud_load + weight > (slots.get("cpu") or 0):
                    continue
                if packing >= PACK_AT_ONCE:
                    continue
            res = j["resource"]
            if res in slots and busy.get(res, 0) >= slots[res]:
                continue
            if res in slots:
                busy[res] = busy.get(res, 0) + 1
            if cloud.holds_cpu_machine(j["step"]):
                cloud_busy.add(j["episode_id"])
                cloud_load += cloud.cpu_job_weight(j["step"])
                packing += 1
            self.cancelled.discard(j["id"])
            self._set(j, status="running", started=now(), progress=0, message="starting")
            threading.Thread(target=self._run_job, args=(j["id"],), daemon=True).start()
        self._episode_status()

    def _episode_status(self):
        for e in self.db.q("SELECT id, status FROM episodes WHERE status IN ('processing','needs_input','review','failed','cancelled')"):
            run = self.latest_run(e["id"])
            js = self.db.q("SELECT step, status FROM jobs WHERE episode_id=? AND run=?", (e["id"], run))
            st = [x["status"] for x in js]
            if not st:
                continue
            if st and all(s in DONE for s in st):
                new = "done"
            elif "running" in st:
                # A retried render is on a machine even if an old transitions row is still failed.
                new = "processing"
            elif "needs_input" in st:
                waiting = [x["step"] for x in js if x["status"] == "needs_input"]
                new = "review" if waiting == ["review"] else "needs_input"
            elif "failed" in st:
                new = "failed"
            elif "waiting" in st:
                new = "processing"
            elif all(s in DONE | {"cancelled", "blocked"} for s in st):
                new = "cancelled"
            else:
                new = "processing"
            if new != e["status"]:
                self.db.update("episodes", e["id"], status=new, updated=now())
                bus.publish("episode_status", e["id"], status=new)
                if new == "done":
                    self._cloud_upload(e["id"])
                if new in ("done", "review", "needs_input"):
                    try:
                        ep = self.db.one("SELECT * FROM episodes WHERE id=?", (e["id"],))
                        storage.clean_scratch(self.db, ep, keep_work=True, log=lambda m: None)
                    except Exception:
                        pass
                if new in ("done", "failed", "cancelled", "review", "needs_input"):
                    self.start_next()          # this episode has let the machine go: the next in the line starts

    def _cloud_upload(self, eid):
        """A finished episode's deliverables go up to R2 by themselves (switch off with R2_AUTO_UPLOAD=false)."""
        try:
            if not (storage.configured() and storage.settings()["auto_upload"]):
                return
            ep = self.db.one("SELECT * FROM episodes WHERE id=?", (eid,))
            show = self.db.one("SELECT * FROM shows WHERE id=?", (ep["show_id"],))
            files = deliver.deliverables(show, ep, None)
            storage.queue_uploads(self.db, show, ep, files, user_id=show.get("owner_id"))
        except Exception as ex:
            print("R2 upload:", str(ex)[:200])

    # ---------- one job ----------
    def _set(self, j, **vals):
        self.db.update("jobs", j["id"], **vals)
        bus.publish("job", j["episode_id"], j["id"], store=False, step=j["step"],
                    **{k: v for k, v in vals.items() if k in ("status", "progress", "message", "item", "eta")})

    # the steps that wait for a person are not timed (their time is the editor's, not the machine's)
    UNTIMED = {"review_gate"}
    # run_report.py's names for the app's steps (the owner's time + cost table, same as run_episode.sh)
    REPORT_NAME = {"mksrt_dub": "srt_dub", "mksrt_src": "srt_src", "lip_gpu": "lip_nets", "lip_mesh": "lip_mesh",
                   "lip_merge": "lip_merge", "decisions": "ai_match"}

    @staticmethod
    def _spend():
        """What Claude has cost so far ($), from whichever provider is in use, or 'NA' (not set up / not reachable).
        The difference before and after a step is that step's cost in the run-times table."""
        from . import ai
        if not ai.available():
            return "NA"
        try:
            spend = ai.key_info().get("spend")
            return str(spend) if spend is not None else "NA"
        except Exception:
            return "NA"

    @staticmethod
    def ai_gate():
        """Before a step that needs the AI (library, face map, scene edit): the gateway must answer and budget must be
        left. Never skip the step: it stops here with a message for the owner (docs/06)."""
        from . import ai
        if not ai.available():
            raise RuntimeError("Claude is not set up (Settings → AI): this step needs it")
        where = "OpenRouter" if ai.provider() == "openrouter" else "the AI gateway"
        try:
            i = ai.key_info()
        except Exception as ex:
            extra = " — turn the VPN on, then Retry this step" if ai.provider() != "openrouter" else " — check the internet, then Retry"
            raise RuntimeError(f"{where} is not reachable ({str(ex)[:80]}){extra}")
        spend, cap = i.get("spend"), i.get("max_budget")
        if spend is not None and cap is not None and float(cap) > 0 and float(spend) >= float(cap):
            raise RuntimeError(f"AI budget used up on {where}: ${float(spend):.2f} of ${float(cap):.2f} — "
                               f"raise it (or top up), then Retry")

    def _log_time(self, j, secs, s0, s1, rc):
        """W/run_times.tsv, the run_episode.sh format: name, seconds, spend before, spend after, exit code, clock time."""
        try:
            W = Path(j["params"]["ctx"]["W"]); W.mkdir(parents=True, exist_ok=True)
            with open(W / "run_times.tsv", "a", encoding="utf-8") as f:
                f.write(f"{self.REPORT_NAME.get(j['step'], j['step'])}\t{int(secs)}\t{s0}\t{s1}\t{rc}\t{time.strftime('%H:%M:%S')}\n")
        except Exception:
            pass

    def _run_job(self, job_id):
        j = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        prm = j["params"]
        Path(j["log_path"]).parent.mkdir(parents=True, exist_ok=True)
        timed = prm.get("fn") not in self.UNTIMED
        t_start = time.time(); s_start = self._spend() if timed else "NA"
        try:
            if prm.get("ai"):
                self.ai_gate()
            if prm.get("fn"):
                getattr(self, "_fn_" + prm["fn"])(j)
            else:
                self._run_cmd(j)
        except Exception as e:
            with open(j["log_path"], "a") as f:
                traceback.print_exc(file=f)
            # a step killed from outside (memory pressure, sleep, a crash: exit by signal) is retried once by itself
            m = re.search(r"exit code (-?\d+)", str(e)); rc = int(m.group(1)) if m else None
            cur = self.db.one("SELECT attempt FROM jobs WHERE id=?", (job_id,))
            once = job_id not in self.cancelled and (cur["attempt"] or 0) < 1
            if rc is not None and (rc < 0 or rc in (137, 143)) and once:
                with open(j["log_path"], "a") as f:
                    f.write(f"\n(stopped by signal, exit {rc}: trying again once)\n")
                self._set(j, status="waiting", attempt=(cur["attempt"] or 0) + 1, progress=0,
                          message=f"stopped unexpectedly (exit {rc}); trying again")
            elif once and (getattr(e, "errno", None) == 28 or "No space left" in str(e)):
                try:
                    show, ep, _ = self.episode_context(j["episode_id"])
                    storage.free_disk(ep["work_dir"], db=self.db, keep_episode_id=j["episode_id"])
                except Exception:
                    pass
                with open(j["log_path"], "a") as f:
                    f.write("\n(disk full: made room, trying again once)\n")
                self._set(j, status="waiting", attempt=(cur["attempt"] or 0) + 1, progress=0,
                          message="disk full; made room, trying again")
            elif once and is_key_limit_error(e):
                # OpenRouter 403 spend cap — not a Claude empty-reply. One job retry, one call, after the cap can move.
                with open(j["log_path"], "a") as f:
                    f.write("\n(OpenRouter key limit: trying again once)\n")
                self._set(j, status="waiting", attempt=(cur["attempt"] or 0) + 1, progress=0,
                          message="OpenRouter key limit; trying again once")
            elif once and self.missing_work_retry(j["step"], e, cur["attempt"]):
                try:
                    show, ep, _ = self.episode_context(j["episode_id"])
                    storage.fetch_work_files(show, ep, force=True)
                except Exception:
                    pass
                with open(j["log_path"], "a") as f:
                    f.write("\n(work file missing on this box: fetched from the cloud, trying again once)\n")
                self._set(j, status="waiting", attempt=(cur["attempt"] or 0) + 1, progress=0,
                          message="work file missing on this box; fetched, trying again")
            else:
                self._set(j, status="failed", finished=now(), message=f"{type(e).__name__}: {e}")
        finally:
            with self.lock:
                self.procs.pop(job_id, None)
            if timed:
                st = (self.db.one("SELECT status FROM jobs WHERE id=?", (job_id,)) or {}).get("status")
                if st in ("done", "failed"):
                    self._log_time(j, time.time() - t_start, s_start, self._spend(), 0 if st == "done" else 1)
            self.wake.set()

    @staticmethod
    def _local_cwd():
        """Where a local command starts. The tools' scripts folder on the Mac; the website does not have that
        folder, and Popen refuses to start if the folder is missing."""
        return str(cfg.scripts) if cfg.scripts.is_dir() else None

    @staticmethod
    def _expand(cmd):
        """{py} = the pipeline python, {fpy} = the face map's python, {scripts} = the tools' scripts folder.
        Whisper wants `it` / `fr`, so `--lang IT` stored from an old plan is rewritten here."""
        from . import presets
        out = [str(cfg.pipeline_python) if c == "{py}" else str(cfg.face_python) if c == "{fpy}"
               else c.replace("{scripts}", str(cfg.scripts)) for c in cmd]
        for i, a in enumerate(out[:-1]):
            if a in ("--lang", "--sl", "--tl"):
                out[i + 1] = presets.asr_lang(out[i + 1])
        return out

    @staticmethod
    def _app_pipeline_script(name):
        """A pipeline script that ships in the API image, not the Mac Desktop folder."""
        here = Path(__file__).resolve()
        for p in (here.parent / "pipeline" / name, here.parents[1] / "pipeline_tools" / name,
                  here.parents[3] / "pipeline_tools" / name if len(here.parents) > 3 else None):
            if p is not None and p.is_file():
                return p
        s = cfg.scripts / name
        return s if s.is_file() else None

    @staticmethod
    def _use_app_script(cmd, name, script):
        out = []
        for c in cmd or []:
            if str(c).endswith(name) or str(c).endswith("{scripts}/" + name):
                out.append(str(script))
            else:
                out.append(c)
        return out

    @staticmethod
    def _lipmerge_local_cmd(cmd):
        """Point lip_merge at this image's lipmerge.py and this process's Python (stdlib only)."""
        script = str(Path(__file__).resolve().parent / "pipeline" / "lipmerge.py")
        out = []
        for c in cmd or []:
            if c in ("{py}", "{PY}"):
                out.append(sys.executable)
            elif str(c).endswith("lipmerge.py"):
                out.append(script)
            else:
                out.append(c)
        return out

    def _run_cmd(self, j, cmd=None, force_local=False):
        prm = j["params"]
        if j["step"] in ("groups", "lipsync"):
            # Clash trim + last-piece lip coverage live in our dubstitch.py. Never the Mac Desktop path on Render.
            # Tests point DUBSTUDIO_TOOLS at a fake dubstitch.py — keep that, do not swap in the real script.
            script = self._app_pipeline_script("dubstitch.py")
            tools_script = cfg.scripts / "dubstitch.py" if cfg.scripts else None
            tools_s = str(tools_script or "")
            desktop = "france-team" in tools_s or "/Desktop/" in tools_s
            if script and (desktop or not (tools_script and tools_script.is_file())):
                force_local = True
                cmd = self._use_app_script(cmd or prm["cmd"], "dubstitch.py", script)
        if j["step"] == "lip_merge" and not cloud.enabled_for("lip_merge"):
            # No rented machine: run this box's lipmerge. The rented image still drops a 1-frame gap.
            force_local = True
            cmd = self._lipmerge_local_cmd(cmd or prm["cmd"])
        if not force_local and j["step"] == "lip_mesh" and cloud.enabled_for("lip_mesh"):
            return self._run_lips(j)
        # library is not force_local: W/_app carries this box's ai.py (truncated-JSON closer) with the cloud job.
        if not force_local and j["step"] in cloud.SCRIPT_CHAIN and cloud.stretch("match"):
            if j["step"] == "translate" and self._translate_needs_dynsrt(j):
                return self._run_cloud_tail(j)
            return self._run_match(j)
        if not force_local and j["step"] == "sheet" and cloud.stretch("cut"):
            return self._run_cut(j)
        if not force_local and cloud.chain_wanted() and j["step"] in cloud.CHAIN:
            return self._run_cloud_tail(j)
        # A step that already rode in a visit is not sent again on its own. A retry of one of those steps
        # (the visit is already done) still has to run on a rented machine: this server has no pipeline.
        if (not force_local and cloud.claimed_by_stretch(j["step"]) and j["step"] not in cloud.CHAIN
                and (j["step"] != "qc" or cloud.ai_key_allowed())):
            return self._run_cmd_cloud(j, cmd or prm["cmd"])
        if not force_local and cloud.enabled_for(j["step"]):  # CLOUD_STEPS: run it on a rented machine instead
            return self._run_cmd_cloud(j, cmd or prm["cmd"])
        cmd = self._expand(cmd or prm["cmd"])
        if cfg.step_max_seconds and any(x in cmd for x in ("render", "transitions", "subs", "words")) or \
                cfg.step_max_seconds and any(x.endswith(("mksrt.py", "lipgpu.py", "lipmesh.py")) for x in cmd):
            cmd += ["--max-seconds", str(cfg.step_max_seconds)]
        dur = prm.get("duration") or 0
        t0 = time.time(); rounds = 0
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        with open(j["log_path"], "a", encoding="utf-8") as log:
            log.write(f"\n$ {' '.join(cmd)}\n")
            while True:
                rounds += 1
                p = subprocess.Popen(cmd, cwd=self._local_cwd(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1, env=env, start_new_session=True)
                with self.lock:
                    self.procs[j["id"]] = p
                Path(j["log_path"] + ".pid").write_text(str(p.pid))
                last_push = 0
                for line in p.stdout:
                    line = ANSI.sub("", line.rstrip())
                    log.write(line + "\n"); log.flush()
                    if not line.strip():
                        continue
                    prog = self._parse_progress(line, dur)
                    if NOISE.search(line):
                        continue
                    if time.time() - last_push > 0.4 or prog is not None:
                        vals = {"message": line[-300:]}
                        if prog is not None:
                            vals["progress"] = round(min(max(prog, 0), 0.999), 4)
                            el = time.time() - t0
                            if prog > 0.02:
                                vals["eta"] = round(el / prog - el, 1)
                        self._set(j, **vals); last_push = time.time()
                rc = p.wait()
                Path(j["log_path"] + ".pid").unlink(missing_ok=True)
                if j["id"] in self.cancelled:
                    self._set(j, status="cancelled", finished=now(), message="cancelled"); return
                if rc == 3 and rounds < 500:          # "not finished -- run again (it resumes)"
                    log.write("(resuming: the tool asked to be run again)\n"); continue
                if rc != 0:
                    tail = self.log_tail(j["log_path"], 6)
                    raise RuntimeError(f"exit code {rc}: {tail[-1] if tail else ''}")
                break
        if j["step"] == "groups":
            snapshot_latest_groups((j.get("params") or {}).get("ctx", {}).get("W") or "")
        self._set(j, status="done", progress=1, finished=now(), eta=0,
                  message=f"done in {time.time() - t0:.0f} s" + (f" ({rounds} rounds)" if rounds > 1 else ""))

    def _worker_cmd(self, j, cmd):
        """Run this command on a rented machine when the step is in CLOUD_STEPS. The caller marks the job done.
        -> True when it ran there."""
        if not (cloud.enabled_for(j["step"]) or cloud.claimed_by_stretch(j["step"])):
            return False
        show, ep, _ = self.episode_context(j["episode_id"])
        with open(j["log_path"], "a", encoding="utf-8") as f:
            def log(msg):
                f.write(str(msg).rstrip() + "\n"); f.flush()
            self._set(j, message="sending the files to a rented machine")
            def prog(done, total, info):
                self._set(j, message=cloud.step_wait_message("this step", info))
            cloud.run_command(self.db, show, ep, j["step"], cmd, cfg, log=log, on_progress=prog,
                              timeout=cfg.step_max_seconds or 7200, kind=cloud.kind_for(j["step"]),
                              release_log=j["log_path"])
        return True

    def _run_cmd_cloud(self, j, cmd):
        """The same step, on a rented machine (dubstudio/cloud.py). The command is the one this machine would have
        run; only the paths differ. Its log is written to the job's log file, so the screens look the same."""
        t0 = time.time()
        show, ep, _ = self.episode_context(j["episode_id"])
        need = cloud.required_work_files(j["step"])
        if need and not self._wait_for_work_files(j, need):
            return
        with open(j["log_path"], "a", encoding="utf-8") as f:
            def log(msg):
                f.write(str(msg).rstrip() + "\n"); f.flush()

            def prog(done, total, info):
                self._set(j, message=cloud.step_wait_message("this step", info))

            self._set(j, message="sending the files to a rented machine")
            res = cloud.run_command(self.db, show, ep, j["step"], self._expand(cmd), cfg,
                                    log=log, on_progress=prog,
                                    timeout=cfg.step_max_seconds or 7200, kind=cloud.kind_for(j["step"]),
                                    release_log=j["log_path"])
        if res.get("rc") == 3:                             # "not finished -- run again (it resumes)"
            return self._run_cmd_cloud(j, cmd)
        if j["step"] == "groups":
            snapshot_latest_groups(ep["work_dir"])
        self._set(j, status="done", progress=1, finished=now(), eta=0,
                  message=f"done on a rented machine in {time.time() - t0:.0f} s")

    def _jobs_by_step(self, j):
        """One row per step. A leftover waiting duplicate must not hide a finished copy (WBT 101 run 8)."""
        rows = self.db.q("SELECT * FROM jobs WHERE episode_id=? AND run=? ORDER BY ord",
                         (j["episode_id"], j["run"]))
        out = {}
        for x in rows:
            prev = out.get(x["step"])
            if prev is None:
                out[x["step"]] = x
            elif prev["status"] in DONE:
                continue
            elif x["status"] in DONE:
                out[x["step"]] = x
        return out

    @staticmethod
    def _work_has(work, rel):
        p = Path(work) / rel
        if p.is_file():
            return True
        if p.is_dir():
            try:
                next(p.iterdir())
                return True
            except StopIteration:
                return False
        return False

    def _missing_work(self, work, rels):
        return [n for n in (rels or []) if not self._work_has(work, n)]

    def _work_dir_for(self, j):
        w = (j.get("params") or {}).get("ctx", {}).get("W")
        if w:
            return Path(w)
        ep = self.db.one("SELECT work_dir FROM episodes WHERE id=?", (j["episode_id"],))
        return Path((ep or {}).get("work_dir") or "")

    def _work_ready_to_start(self, j):
        """True when this step may start. If a previous step in this run is supposed to have written
        files we need, wait until those files are here (fetch from R2) instead of failing on the worker."""
        need = list(cloud.required_work_files(j["step"]))
        if j["step"] == "analyze":
            need = list(dict.fromkeys([*need, *cloud.required_work_files("match")]))
        by = {row["step"]: row["status"] for row in self.db.q(
            "SELECT step, status FROM jobs WHERE episode_id=? AND run=?", (j["episode_id"], j["run"]))}
        # Only wait for a file when the step that writes it exists in this run and is already done.
        need = [n for n in need if cloud.FILE_PRODUCER.get(n) in by and by[cloud.FILE_PRODUCER[n]] in DONE]
        if not need:
            return True
        work = self._work_dir_for(j)
        missing = self._missing_work(work, need)
        if not missing:
            return True
        now_ts = time.time()
        first = j["id"] not in self._file_wait_at
        if now_ts - self._file_wait_at.get(j["id"], 0) >= 5:
            self._file_wait_at[j["id"]] = now_ts
            try:
                show, ep, _ = self.episode_context(j["episode_id"])
                storage.fetch_work_files(show, ep, force=True)
            except Exception:
                pass
            missing = self._missing_work(work, need)
            if not missing:
                return True
            if not first:
                self._retry_missing_producers(j, missing, by)
        self._set(j, message="waiting for " + ", ".join(missing) + " from the previous step")
        return False

    def _retry_missing_producers(self, j, missing, by):
        """Previous step said done but its file is gone: run that step again. Do not start the later step empty."""
        for rel in missing:
            prod = cloud.FILE_PRODUCER.get(rel)
            if not prod or by.get(prod) not in DONE:
                continue
            row = self.db.one(
                "SELECT id, attempt FROM jobs WHERE episode_id=? AND run=? AND step=?",
                (j["episode_id"], j["run"], prod))
            if not row or (row.get("attempt") or 0) >= 3:
                continue
            stamp = (row["id"], row.get("attempt") or 0)
            if stamp in self._retried_producer:
                continue
            self._retried_producer.add(stamp)
            self.retry(row["id"])
            self._set(j, message=f"waiting for {rel}: running {prod} again")

    def _wait_for_work_files(self, j, rels, timeout=1800):
        """Hold this running job until these work files exist. -> False if cancelled."""
        work = self._work_dir_for(j)
        show, ep, _ = self.episode_context(j["episode_id"])
        t0 = time.time()
        while not self.stop.is_set():
            if j["id"] in self.cancelled:
                self._set(j, status="cancelled", finished=now(), message="cancelled")
                return False
            missing = self._missing_work(work, rels)
            if not missing:
                return True
            try:
                storage.fetch_work_files(show, ep, force=True)
            except Exception:
                pass
            missing = self._missing_work(work, rels)
            if not missing:
                return True
            if time.time() - t0 > timeout:
                raise RuntimeError("waiting for " + ", ".join(missing)
                                   + " from the previous step (not on this box or in R2 yet)")
            self._set(j, message="waiting for " + ", ".join(missing) + " from the previous step")
            time.sleep(2.0)
        return False

    def _wait_for_step(self, j, step):
        """Hold this job (no machine rented yet) until `step` has finished. -> False if this job was cancelled."""
        while not self.stop.is_set():
            if j["id"] in self.cancelled:
                self._set(j, status="cancelled", finished=now(), message="cancelled")
                return False
            other = self.db.one("SELECT status FROM jobs WHERE episode_id=? AND run=? AND step=?",
                                (j["episode_id"], j["run"], step))
            if not other or other["status"] in DONE:
                return True
            if other["status"] in ("failed", "cancelled", "blocked"):
                raise RuntimeError(f"{step} did not finish ({other['status']})")
            self._set(j, message=f"waiting for {step} before renting a machine")
            time.sleep(1.0)
        return False

    def _mark_visit(self, jobs, message):
        """Mark the visit's jobs done, leaves first, so a dependent cannot start in the gap."""
        for sj in reversed(jobs):
            self._set(sj, status="done", progress=1, finished=now(), eta=0, message=message)

    def _run_lips(self, j):
        """Lip opening and the merge, on the one CPU that already has the scan. The merge is a JSON join."""
        if not self._wait_for_work_files(j, cloud.required_work_files("lips")):
            return
        t0 = time.time()
        by = self._jobs_by_step(j)
        named = [("lip_mesh", self._expand((j["params"] or {})["cmd"]))]
        to_mark = [j]
        merge = by.get("lip_merge")
        if merge and merge["status"] not in DONE:
            cmd = (merge["params"] or {}).get("cmd")
            if cmd:
                named.append(("lip_merge", self._expand(cmd)))
                to_mark.append(merge)
        self._send_visit(j, "lips", named, to_mark, t0, "lip mesh on one rented machine")

    def _proxy_visit_cmds(self, j, assets):
        """Same ffmpeg the website used to send alone, so preview rides the matching CPU after Whisper."""
        work = Path(((j.get("params") or {}).get("ctx") or {}).get("W") or "")
        src = assets.get("src_video") or ""
        dub = assets.get("dub_audio")
        cmds = [("proxy", ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
                           "-vf", "scale=-2:'min(540,ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
                           "-pix_fmt", "yuv420p", "-force_key_frames", "expr:gte(t,n_forced*0.5)",
                           "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart",
                           str(work / timeline.PROXY)])]
        if dub:
            cmds.append(("proxy", ["ffmpeg", "-v", "error", "-y", "-i", str(dub), "-map", "0:a:0", "-c:a", "aac",
                                   "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                                   str(work / timeline.DUB_PREVIEW)]))
        return cmds

    def _sharpness_visit_cmd(self, j):
        work = Path(((j.get("params") or {}).get("ctx") or {}).get("W") or "")
        tool = cloud.stage_sharpness_tool(work)
        py = getattr(cfg, "pipeline_python", None) or "{py}"
        return ("sharpness", [py, str(tool), "--video", str(work / timeline.PROXY),
                              "--out", str(work / timeline.SHARPNESS)])

    def _translate_visit_cmd(self, j, cmd):
        """Google first, Claude for leftover sentences. 429 must not fail the matching visit."""
        work = Path(((j.get("params") or {}).get("ctx") or {}).get("W") or "")
        wrapper = cloud.stage_translate_fallback(work)
        py = getattr(cfg, "pipeline_python", None) or "{py}"
        return ("translate", [py, str(wrapper)] + list(cmd))

    @staticmethod
    def _translate_needs_dynsrt(j):
        """HIH / dynamic-strip shows: English cards are translated from *_dynamic.srt, which
        exists only after review. Packing that on the match visit FileNotFound IMTSITDE 39/40."""
        if not j:
            return False
        if "dynsrt" in (j.get("needs") or []):
            return True
        cmd = (j.get("params") or {}).get("cmd") or []
        return any(str(c).endswith("_dynamic.srt") for c in cmd)

    def _run_match(self, j):
        """Whisper through matching, then preview, sharpness, translation, on one CPU.
        The decision pause stays on the website."""
        t0 = time.time()
        show, ep, assets = self.episode_context(j["episode_id"])
        by = self._jobs_by_step(j)
        named, to_mark = [], []
        for step in cloud.SCRIPT_CHAIN:
            sj = by.get(step)
            if not sj or sj["status"] in DONE:
                continue
            if step == "merged":
                named.append(("merged", list(cloud.MERGED_CMD)))
                to_mark.append(sj)
                continue
            if step == "proxy":
                named.extend(self._proxy_visit_cmds(sj, assets))
                to_mark.append(sj)
                continue
            if step == "sharpness":
                named.append(self._sharpness_visit_cmd(sj))
                to_mark.append(sj)
                continue
            cmd = (sj["params"] or {}).get("cmd")
            if not cmd:
                continue
            expanded = self._expand(cmd)
            if step == "translate" and self._translate_needs_dynsrt(sj):
                continue
            if step == "translate":
                named.append(self._translate_visit_cmd(sj, expanded))
            else:
                named.append((step, expanded))
            to_mark.append(sj)
        if not named:
            return
        first = named[0][0]
        need = [] if first in ("mksrt_dub", "mksrt_src") else cloud.required_work_files(first)
        if need and not self._wait_for_work_files(j, need):
            return
        self._send_visit(j, "match", named, to_mark, t0, "script through matching on one rented machine")

    def _run_cut(self, j):
        """The mapping sheet through the shot library, on one CPU, after the decisions and the lip scan.

        mkdec is first: it checks the decisions and does not need a video. Captions, when this episode has
        them, ride along because they only read the source video. The face map is not here."""
        if not self._wait_for_step(j, "lip_merge"):
            return
        if not self._wait_for_work_files(j, cloud.required_work_files("cut")):
            return
        t0 = time.time()
        by = self._jobs_by_step(j)
        work = Path(j["params"]["ctx"]["W"])
        spec = self._write_spec(work)
        named = [("mkdec", [str(cfg.pipeline_python), str(cfg.scripts / "mkdec.py"), str(work), str(spec)])]
        to_mark = []
        for step in cloud.CUT_CHAIN:
            sj = by.get(step)
            if not sj or sj["status"] in DONE:
                continue
            cmd = (sj["params"] or {}).get("cmd")
            if not cmd:
                continue
            named.append((step, self._expand(cmd)))
            to_mark.append(sj)
        cap = by.get("captions")
        if cap and cap["status"] not in DONE | ACTIVE:
            cmd = (cap["params"] or {}).get("cmd")
            if cmd:
                named.append(("captions", self._expand(cmd)))
                to_mark.append(cap)
        if not any(sj["id"] == j["id"] for sj in to_mark):
            raise RuntimeError("the cut has no sheet command")
        self._send_visit(j, "cut", named, to_mark, t0, "the cut on one rented machine")

    def _send_visit(self, j, name, named, to_mark, t0, progress):
        show, ep, _ = self.episode_context(j["episode_id"])
        with open(j["log_path"], "a", encoding="utf-8") as f:
            def log(msg):
                f.write(str(msg).rstrip() + "\n"); f.flush()

            def prog(done, total, info):
                self._set(j, message=cloud.step_wait_message(progress, info))

            self._set(j, message=f"sending {name} to one rented machine")
            res = cloud.run_chain(self.db, show, ep, named, cfg, log=log, on_progress=prog,
                                  timeout=cfg.step_max_seconds or 7200, name=name, release_log=j["log_path"])
        if res.get("rc") == 3:
            if name == "match":
                return self._run_match(j)
            if name == "lips":
                return self._run_lips(j)
            if name == "scene":
                ctx = (j["params"] or {}).get("ctx") or {}
                return self._run_scene_visit(j, Path(ctx["W"]), ctx["library_dir"])
            if name in ("tail", "render_tail"):
                return self._run_cloud_tail(j)
            return self._run_cut(j)
        missing = self._missing_work(ep["work_dir"], cloud.produced_work_files(name))
        if missing:
            try:
                storage.fetch_work_files(show, ep, force=True)
            except Exception:
                pass
            missing = self._missing_work(ep["work_dir"], cloud.produced_work_files(name))
        if missing:
            raise RuntimeError(f"{name} finished without " + ", ".join(missing)
                               + " — not marking later steps done")
        msg = f"done on one rented machine in {time.time() - t0:.0f} s ({', '.join(n for n, _ in named)})"
        self._mark_visit(to_mark, msg)

    def _scene_sheet_argv(self, j, show, ep):
        """The scene-edit sheet command. Slowfix already wrote the xlsx; the old EDL sheet is only for sceneedit.py."""
        ctx = j["params"]["ctx"]
        out = ctx["name_scene_sheet"]
        W = Path(ctx.get("W") or ep.get("work_dir") or "")
        slow_xlsx = W / "scene_slowfix" / "scene_edit.xlsx"
        if slow_xlsx.is_file():
            cmd = ["{py}", "-c",
                   "import os, shutil, sys\n"
                   "src, dest = sys.argv[1], sys.argv[2]\n"
                   "os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)\n"
                   "shutil.copy(src, dest)\n"
                   "print('scene-edit sheet', dest)\n",
                   str(slow_xlsx), out]
            return cmd, out
        en = ctx.get("en_srt_burn") or ctx["name_en_srt"]
        cmd = ["{py}", str(cfg.scene_dir / "sceneedit_sheet.py"),
               str(Path(ctx["scene_dir"]) / "scene_manifest.edl.json"),
               str(Path(ctx["library_dir"]) / "broll_library.json"),
               en, out, f"{show['code'].upper()} ep{ep['number']}: scene edit"]
        return cmd, out

    @staticmethod
    def _groups_review_argv(out):
        """Point config.json at the sheet `check` looks for. Paths are filled in on the worker."""
        return ["{py}", "-c",
                "import json,sys\n"
                "p,g=sys.argv[1],sys.argv[2]\n"
                "c=json.load(open(p,encoding='utf-8'))\n"
                "c['groups_review']=g\n"
                "json.dump(c,open(p,'w'),indent=1)\n",
                "{WORK}/config.json", out]

    def _tail_closing(self, show, ep, by):
        """After the subtitle burns, on the same machine: the scene-edit sheet, then the final check.

        The check reads the videos and the sheet. `qc` asks Claude, so it stays off this visit unless
        CLOUD_AI is on. -> (commands, jobs to mark done)"""
        named, mark = [], []
        sj = by.get("scene_sheet")
        if sj and sj["status"] not in DONE:
            cmd, out = self._scene_sheet_argv(sj, show, ep)
            named.append(("scene_sheet", self._expand(cmd)))
            named.append(("groups_review", self._expand(self._groups_review_argv(out))))
            mark.append(sj)
        qj = by.get("qc")
        if cloud.ai_key_allowed() and qj and qj["status"] not in DONE and (qj["params"] or {}).get("cmd"):
            named.append(("qc", self._expand(qj["params"]["cmd"])))
            mark.append(qj)
        cj = by.get("check")
        if cj and cj["status"] not in DONE and (cj["params"] or {}).get("cmd"):
            if not cloud.ai_key_allowed() or not qj or qj["status"] in DONE or qj in mark:
                named.append(("point_translation", self._expand(self._point_translation_cmd())))
                named.append(("check", self._expand(cj["params"]["cmd"])))
                mark.append(cj)
        return named, mark

    @staticmethod
    def _point_translation_cmd():
        """Put `translation.out` back on config.json if a later unpack wiped it (TAB EP31)."""
        return ["{py}", "-c",
                "import glob, json, os, shutil\n"
                "p = '/work/config.json'\n"
                "c = json.load(open(p, encoding='utf-8'))\n"
                "cands = []\n"
                "cur = (c.get('translation') or {}).get('out')\n"
                "if cur: cands.append(cur)\n"
                "for d in ('/out', '/work'):\n"
                "    cands += glob.glob(os.path.join(d, '*english*.srt'))\n"
                "    cands.append(os.path.join(d, 'english_translation_original.srt'))\n"
                "for key in ('sheet_out', 'clean_final'):\n"
                "    q = c.get(key)\n"
                "    if q: cands += glob.glob(os.path.join(os.path.dirname(q), '*english*.srt'))\n"
                "src = next((x for x in cands if x and os.path.isfile(x)), '')\n"
                "if src:\n"
                "    want = (c.get('translation') or {}).get('out') or src\n"
                "    dest = os.path.abspath(want)\n"
                "    if os.path.abspath(src) != dest:\n"
                "        os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)\n"
                "        shutil.copy(src, dest)\n"
                "    c.setdefault('translation', {})['out'] = dest\n"
                "    json.dump(c, open(p, 'w'), indent=1)\n"
                "    print('translated SRT', c['translation']['out'])\n"
                "else:\n"
                "    print('translated SRT not next to the videos')\n"]

    @staticmethod
    def _clean_master_cmd():
        """Point the config at the clean video bookends just wrote.

        When the episode has no opening and no ending, bookends copies the render and records it as
        clean_final, and leaves clean_nofx alone. Transitions trusts clean_nofx first, even when that
        file is not on the machine."""
        return ["{py}", "-c",
                "import json, os, sys\n"
                "p, w = sys.argv[1], sys.argv[2]\n"
                "c = json.load(open(p, encoding='utf-8'))\n"
                "src_v = os.path.abspath(c.get('src_video') or '')\n"
                "def ok(x):\n"
                "    if not x or not os.path.isfile(x): return False\n"
                "    a = os.path.abspath(x)\n"
                "    if a.startswith('/files/'): return False\n"
                "    if src_v and a == src_v: return False\n"
                "    return True\n"
                "cands = [os.path.join(w, 'clean_noglow.mp4'), c.get('clean_final') or '',\n"
                "         c.get('video_out') or '', c.get('clean_nofx') or '']\n"
                "src = next((x for x in cands if ok(x)), '')\n"
                "if not src:\n"
                "    raise SystemExit('no clean master after bookends')\n"
                "want = os.path.abspath(os.path.join(w, 'clean_noglow.mp4'))\n"
                "if os.path.abspath(src) != want:\n"
                "    import shutil\n"
                "    shutil.copyfile(src, want)\n"
                "    src = want\n"
                "c['clean_nofx'] = src\n"
                "c['clean_final'] = src\n"
                "json.dump(c, open(p, 'w'), indent=1)\n"
                "print('clean master', c['clean_nofx'])\n",
                "{WORK}/config.json", "{WORK}"]

    @staticmethod
    def _tail_from(step, work):
        """Where a render-tail retry has to start.

        A failed visit does not send the videos back, so retrying Transitions alone arrives on an empty
        machine and bookends has not run there. Rebuild from the render, or from bookends if the render
        is already on disk."""
        work = Path(work)
        if step in ("check", "qc", "scene_sheet", "dynsrt"):
            return "render"
        if step in ("bookends", "trans_plan", "transitions", "subs_dub", "subs_en") and not (work / "video_groups_nosubs.mp4").is_file():
            return "render"
        if step in ("trans_plan", "transitions", "subs_dub", "subs_en") and not (work / "clean_noglow.mp4").is_file():
            return "bookends"
        return step

    def _tail_commands(self, j, by, work):
        """The commands for this visit, starting early enough that the clean master exists on that machine."""
        start = self._tail_from(j["step"], work)
        named, to_mark, started = [], [], False
        for step in cloud.CHAIN:
            sj = by.get(step)
            if not sj:
                continue
            if step == start or sj["id"] == j["id"]:
                started = True
            if not started:
                continue
            cmd = (sj["params"] or {}).get("cmd")
            if not cmd:
                continue
            if step == "transitions":
                named.append(("clean_master", self._clean_master_cmd()))
            # English burn needs the Google SRT on /out. Translate often wrote it; a retry after a
            # failed unpack does not, so find and copy it before subs_en opens the path.
            if step == "subs_en":
                named.append(("point_translation", self._expand(self._point_translation_cmd())))
            named.append((step, self._expand(cmd)))
            to_mark.append(sj)
        # The automatic cut (no B-roll, fast lines still fast) is rendered first, then the scene edit
        # overwrites video_out so verify still checks the finished picture.
        if any(n == "render" for n, _ in named) and (Path(work) / "manifest_groups.auto.json").is_file():
            extra = self._before_picture_cmds(by)
            if extra:
                at = next(i for i, (n, _) in enumerate(named) if n == "render")
                named[at:at] = extra
        return named, to_mark

    def _before_picture_cmds(self, by):
        """Render manifest_groups.auto.json beside the finished video, then drop its segment cache.

        That list is the cut after matching and groups, before the scene edit adds B-roll and trims
        fast lines. The segment pieces are only needed to build this one file."""
        render = ((by.get("render") or {}).get("params") or {}).get("cmd") or []
        trans = ((by.get("transitions") or {}).get("params") or {}).get("cmd") or []
        if "render" not in render or "--out" not in trans:
            return []
        clean = trans[trans.index("--out") + 1]
        out = clean.replace("_final_clean.mp4", "_before.mp4")
        if Path(out).is_file() and Path(out).stat().st_size > 64:
            return []
        py = render[0]
        picture = self._expand(list(render) + ["--manifest", "manifest_groups.auto.json", "--out", out])
        drop = self._expand([py, "-c",
                             "import json, os, shutil\n"
                             "shutil.rmtree('/work/segs_groups.auto', True)\n"
                             "for n in ('silent_groups.auto.mp4', 'concat_groups.auto.txt'):\n"
                             "    p = '/work/' + n\n"
                             "    if os.path.isfile(p):\n"
                             "        os.remove(p)\n"
                             "cfgp = '/work/config.json'\n"
                             "if os.path.isfile(cfgp):\n"
                             "    c = json.load(open(cfgp, encoding='utf-8'))\n"
                             "    c['manifest'] = 'manifest_groups.json'\n"
                             "    c.pop('video_out', None)\n"
                             "    c.pop('video_manifest', None)\n"
                             "    json.dump(c, open(cfgp, 'w'), indent=1)\n"])
        return [("before_render", picture), ("before_cleanup", drop)]

    def _run_cloud_tail(self, j):
        """render → verify → bookends → transitions → both subtitle burns, on ONE rented machine.

        Each of those reads the previous step's multi-GB video, so one step per machine cannot work
        (docs/09 §8). Dynamic cards, the transition plan, the scene-edit sheet, QC and the final check
        stay on that machine too so Render never looks for the three videos on its own disk."""
        t0 = time.time()
        show, ep, _ = self.episode_context(j["episode_id"])
        jobs = self.db.q("SELECT * FROM jobs WHERE episode_id=? AND run=? ORDER BY ord",
                         (j["episode_id"], j["run"]))
        by = {x["step"]: x for x in jobs}

        work = (j["params"] or {}).get("ctx", {}).get("W") or ep["work_dir"]
        if not Path(work, "config.json").exists() or not Path(work, "manifest_groups.json").exists():
            Path(j["log_path"]).parent.mkdir(parents=True, exist_ok=True)
            with open(j["log_path"], "a", encoding="utf-8") as f:
                storage.fetch_work_files(show, ep, log=lambda m: (f.write(str(m).rstrip() + "\n"), f.flush()),
                                         force=True)
        ctx = (j["params"] or {}).get("ctx") or {}
        for key in ("en_srt_burn", "name_en_srt"):
            path = ctx.get(key)
            if not path:
                continue
            got = storage.ensure_out_local(show, ep, Path(path).name, dest=path) if storage.configured() else None
            if got and Path(got).is_file():
                break
            if Path(path).is_file():
                break
            for cand in Path(work).glob("*english*.srt"):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(cand, path)
                break
        named, to_mark = self._tail_commands(j, by, work)
        dyn = by.get("dynsrt")
        if dyn and dyn["status"] not in DONE and (dyn["params"] or {}).get("cmd"):
            named.insert(0, ("dynsrt", self._expand(dyn["params"]["cmd"])))
            to_mark.insert(0, dyn)
        tr = by.get("translate")
        if tr and tr["status"] not in DONE and self._translate_needs_dynsrt(tr) and (tr["params"] or {}).get("cmd"):
            item = self._translate_visit_cmd(tr, self._expand(tr["params"]["cmd"]))
            at = next((i + 1 for i, (n, _) in enumerate(named) if n == "dynsrt"), 0)
            named.insert(at, item)
            to_mark.insert(min(at, len(to_mark)), tr)
        if any(n in ("subs_en", "render", "dynsrt") for n, _ in named):
            extra, extra_jobs = self._tail_closing(show, ep, by)
            named.extend(extra)
            to_mark.extend(extra_jobs)
        if not named:
            raise RuntimeError("the render tail has no commands to send")

        with open(j["log_path"], "a", encoding="utf-8") as f:
            def log(msg):
                f.write(str(msg).rstrip() + "\n"); f.flush()

            def prog(done, total, info):
                self._set(j, message=cloud.step_wait_message("render tail", info))

            self._set(j, message="sending the render tail to one rented machine")
            res = cloud.run_chain(self.db, show, ep, named, cfg, log=log, on_progress=prog,
                                  timeout=cfg.step_max_seconds or 7200, release_log=j["log_path"])
        if res.get("rc") == 3:
            return self._run_cloud_tail(j)
        msg = f"done on one rented machine in {time.time() - t0:.0f} s ({', '.join(n for n, _ in named)})"
        self._mark_visit(to_mark, msg)
        self._copy_chain_logs(j["log_path"], to_mark)

    def _copy_chain_logs(self, chain_log, jobs):
        """The visit writes one log. Copy each `=== step ===` section into that job's log so check/qc
        Deliverables can read FINAL CHECK PASSED from the check job after a rented-machine tail."""
        try:
            text = Path(chain_log).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return
        by_name, cur, buf = {}, "_head", []
        for ln in text.splitlines():
            m = re.match(r"^=== ([a-z_]+) ===\s*$", ln)
            if m:
                by_name[cur] = "\n".join(buf)
                cur, buf = m.group(1), [ln]
            else:
                buf.append(ln)
        by_name[cur] = "\n".join(buf)
        for sj in jobs or []:
            chunk = by_name.get(sj["step"])
            if not chunk:
                continue
            p = Path(sj["log_path"])
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    f.write(chunk.rstrip() + "\n")
            except Exception:
                continue

    @staticmethod
    def _parse_progress(line, dur):
        for pat in PROG_PATTERNS:
            m = pat.search(line)
            if m and int(m.group(2)):
                return int(m.group(1)) / int(m.group(2))
        m = PIECE_PAT.search(line)
        if m and int(m.group(2)):
            return int(m.group(1)) / int(m.group(2))
        if dur:
            m = TIME_PAT.search(line)
            if m:
                return float(m.group(2)) / float(dur)
        return None

    @staticmethod
    def log_tail(path, n=40):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read().splitlines()[-n:]
        except FileNotFoundError:
            return []

    @staticmethod
    def final_check_result(jobs):
        """Whether the package check passed, and the OK/BAD/FINAL lines.

        The render tail writes those lines into the *render* job log (`=== check ===`), not the check
        job's own file. Looking only at check.log then shows a red empty banner on a finished episode."""
        by = {j["step"]: j for j in jobs or []}
        tails = []
        for step, n in (("check", 80), ("render", 150)):
            j = by.get(step)
            if not j or not j.get("log_path"):
                continue
            tails.extend(Runner.log_tail(j["log_path"], n))
        lines = [ln for ln in tails if ln.startswith(("OK ", "BAD", "FINAL"))]
        if any("FINAL CHECK PASSED" in ln for ln in tails):
            return {"passed": True, "lines": lines[-20:]}
        if any("FINAL CHECK FAILED" in ln for ln in tails):
            return {"passed": False, "lines": lines[-20:]}
        return {"passed": None, "lines": lines[-20:]}

    # ---------- internal steps ----------
    # ---------- the three storage steps (docs/09 §3): get the files, put the results back, clean up ----------
    def _fn_fetch_inputs(self, j):
        """Small inputs can land here. Masters stay in R2 when a CPU factory is on (workers GET them).

        The website used to download every episode's video before lip/Whisper — that filled the disk."""
        show, ep, _ = self.episode_context(j["episode_id"])
        todo = storage.inputs_to_fetch(self.db, ep) if storage.configured() else []
        factory = cloud.available("cpu")
        skipped = []
        if factory:
            keep, skipped = [], []
            for a in todo:
                if a.get("kind") in storage.MEDIA_KINDS and a.get("r2_key"):
                    skipped.append(a)
                else:
                    keep.append(a)
            todo = keep
            missing = []
            for a in skipped:
                if not storage.head(a["r2_key"]):
                    missing.append(a.get("name") or Path(a.get("path") or "?").name)
            if missing:
                raise RuntimeError("not in the cloud: " + ", ".join(missing) + " — Retry this step, not Process again")
        if not todo:
            msg = "masters stay in R2; workers will get them" if skipped else "the files are here already"
            self._set(j, status="done", progress=1, finished=now(), message=msg)
            return
        total = sum(a["size"] or 0 for a in todo) or 1
        done_bytes = [0]; last = [0.0]

        def prog(kind, got, size):
            if time.time() - last[0] > 0.5:
                last[0] = time.time()
                self._set(j, progress=round(min((done_bytes[0] + got) / total, 1), 4),
                          message=f"getting {kind} ({(done_bytes[0] + got) / 1e6:.0f} of {total / 1e6:.0f} MB)")
        with open(j["log_path"], "a") as f:
            def log(m):
                f.write(m + "\n"); f.flush()
            for a in todo:
                storage.fetch_inputs(self.db, show, ep, kinds=[a["kind"]], log=log, on_progress=prog)
                done_bytes[0] += a["size"] or 0
        extra = f"; {len(skipped)} master(s) left in R2" if skipped else ""
        self._set(j, status="done", progress=1, finished=now(),
                  message=f"{len(todo)} file(s) brought down ({total / 1e6:.0f} MB){extra}")

    def _fn_publish(self, j):
        """The delivered files (and the small work files) go up to R2."""
        show, ep, _ = self.episode_context(j["episode_id"])
        if not storage.configured():
            self._set(j, status="skipped", progress=1, finished=now(), message="cloud storage is off")
            return
        with open(j["log_path"], "a") as f:
            out = storage.publish_outputs(self.db, show, ep, deliver.deliverables(show, ep, None),
                                          user_id=show.get("owner_id"), log=lambda m: f.write(m + "\n"))
        self._set(j, status="done", progress=1, finished=now(),
                  message=f"{out['uploads']} files to the cloud, {out['work_files']} work files kept")

    def _fn_cleanup(self, j):
        """Free the machine for the next episode: the files that came from R2 are deleted again."""
        show, ep, _ = self.episode_context(j["episode_id"])
        if not storage.configured():
            self._set(j, status="skipped", progress=1, finished=now(), message="cloud storage is off")
            return
        blocked = storage.blocking_failed_uploads(self.db, ep["id"])
        if blocked:
            for row in blocked:
                storage.retry_upload(self.db, row["id"])
            storage.start_worker(self.db)
            self._set(j, message=f"retrying {len(blocked)} latest failed upload(s) once")
        waiting = self.db.one("SELECT COUNT(*) AS n FROM uploads WHERE episode_id=? AND status IN ('waiting','sending')",
                              (ep["id"],))
        for _ in range(600):                      # the files must be up there before the local ones go
            if not (waiting or {}).get("n"):
                break
            time.sleep(1)
            waiting = self.db.one("SELECT COUNT(*) AS n FROM uploads WHERE episode_id=? AND status IN ('waiting','sending')",
                                  (ep["id"],))
            self._set(j, message=f"waiting for {waiting['n']} file(s) to finish going up")
        blocked = storage.blocking_failed_uploads(self.db, ep["id"])
        if blocked:
            names = ", ".join(sorted({(r.get("kind") or r.get("name") or "?") for r in blocked}))
            raise RuntimeError(
                f"{len(blocked)} latest upload(s) still failed ({names}): nothing was deleted. "
                "Retry those uploads, then Retry cleanup — do not Process again")
        missing = storage.masters_missing_from_cloud(show, ep)
        if missing:
            storage.queue_uploads(self.db, show, ep, deliver.deliverables(show, ep, None),
                                  user_id=show.get("owner_id"))
            raise RuntimeError(
                f"{len(missing)} master video(s) not in the cloud yet ({', '.join(missing)}): "
                "nothing was deleted. The upload is queued — Retry cleanup when it finishes")
        with open(j["log_path"], "a") as f:
            out = storage.clean_scratch(self.db, ep, keep_work=not envfile.flag("CLEAN_WORK", False),
                                        log=lambda m: f.write(m + "\n"))
        self._set(j, status="done", progress=1, finished=now(), message=f"{out['freed'] / 1e9:.2f} GB freed")

    def _fn_lip_scan_cloud(self, j):
        """The lip scan on rented machines: the episode is split into pieces that scan side by side and come back
        as the same `lips_nets` files the local step writes (proved identical to this Mac: docs/03, 2026-11-01)."""
        from . import cloud
        show, ep, assets = self.episode_context(j["episode_id"])
        dur = assets.get("_dur_src_video")
        if not dur:
            raise RuntimeError("the source video's length is not known yet (the ingest step measures it)")
        with open(j["log_path"], "a") as f:
            def log(m):
                f.write(m + "\n"); f.flush()

            def progress(done, total, info):
                self._set(j, progress=round(done / max(total, 1), 3),
                          message=cloud.step_wait_message(f"{done} of {total} pieces", info))
            out = cloud.lip_scan(self.db, show, ep, float(dur), log=log, on_progress=progress,
                                 release_log=j["log_path"])
        if not self._work_has(ep["work_dir"], "lips_nets/ranges.json"):
            try:
                storage.fetch_work_files(show, ep, force=True)
            except Exception:
                pass
        if not self._work_has(ep["work_dir"], "lips_nets/ranges.json"):
            raise RuntimeError("lip scan finished without lips_nets/ranges.json; not starting lip mesh or face map")
        self._set(j, status="done", progress=1, finished=now(),
                  message=f"{out['files']} pieces scanned on {out['pieces']} machines in {out['seconds']:.0f} s")

    def _fn_ingest(self, j):
        ep_id = j["episode_id"]; problems = []
        rows = self.db.q("SELECT * FROM assets WHERE episode_id=? AND role='input'", (ep_id,))
        for k, a in enumerate(rows):
            self._set(j, progress=k / max(len(rows), 1), message=f"checking {Path(a['path']).name}")
            info, probs = scan.check_asset(a["kind"], a["path"])
            if probs and a.get("r2_key") and storage.configured():
                head = storage.head(a["r2_key"])
                old = a.get("probe") if isinstance(a.get("probe"), dict) else {}
                if head and old.get("duration"):
                    info, probs = {"size": head.get("size") or a.get("size"), "probe": old,
                                   "fingerprint": a.get("fingerprint"), "mtime": a.get("mtime")}, []
                elif head:
                    url = storage.presign(a["r2_key"], 600)
                    info, probs = scan.check_asset(a["kind"], a["path"], url=url)
                    if info and not info.get("size"):
                        info["size"] = head.get("size")
                else:
                    probs = [f"{a['kind']}: not on this box and not in the cloud"]
            problems += probs
            if info:
                self.db.update("assets", a["id"], size=info.get("size") or a.get("size"),
                               fingerprint=info.get("fingerprint") or a.get("fingerprint"),
                               mtime=info.get("mtime") or a.get("mtime"), probe=info.get("probe") or a.get("probe"))
        with open(j["log_path"], "a") as f:
            f.write("\n".join(problems or ["all input files readable"]) + "\n")
        if problems:
            raise RuntimeError("; ".join(problems))
        self._set(j, status="done", progress=1, finished=now(), message=f"{len(rows)} files checked")

    def _fn_proxy(self, j):
        a = self.db.one("SELECT path, probe FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'",
                        (j["episode_id"],))
        dur = ((a or {}).get("probe") or {}).get("duration") if isinstance((a or {}).get("probe"), dict) else None
        work = j["params"]["ctx"]["W"]; last = [0.0]
        out = Path(work) / timeline.PROXY
        d = self.db.one("SELECT path FROM assets WHERE episode_id=? AND kind='dub_audio' AND role='input'", (j["episode_id"],))
        # enabled_for("proxy") is False when proxy rides the match visit, so the old
        # gate fell through to ffmpeg on Render. Masters are not on this disk (P1) →
        # "No such file" on /app/output/library/work/…/src_video/….mp4 (WBT 103).
        if cloud.enabled_for("proxy") or cloud.claimed_by_stretch("proxy"):
            # The website server has no ffmpeg. Same flags as timeline.make_proxy / dub_preview.
            if not out.exists():
                self._worker_cmd(j, ["ffmpeg", "-v", "error", "-y", "-i", a["path"], "-map", "0:v:0", "-map", "0:a:0?",
                                     "-vf", "scale=-2:'min(540,ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
                                     "-pix_fmt", "yuv420p", "-force_key_frames", "expr:gte(t,n_forced*0.5)",
                                     "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart", str(out)])
            dub_out = Path(work) / timeline.DUB_PREVIEW
            if d and not dub_out.exists():
                self._worker_cmd(j, ["ffmpeg", "-v", "error", "-y", "-i", d["path"], "-map", "0:a:0", "-c:a", "aac",
                                     "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(dub_out)])
            if d:
                # Masters stay in R2. Do not stat the local path — that is what failed WBT 101–104
                # after ffmpeg already ran on the rented machine.
                pth = Path(d["path"])
                if pth.is_file():
                    st = pth.stat()
                    timeline.record_dub_preview(work, d["path"], st.st_size, st.st_mtime)
                else:
                    row = self.db.one(
                        "SELECT size, mtime FROM assets WHERE episode_id=? AND kind='dub_audio' AND role='input'",
                        (j["episode_id"],)) or {}
                    timeline.record_dub_preview(work, d["path"], row.get("size"), row.get("mtime"))
            show, ep, _ = self.episode_context(j["episode_id"])
            def _in_cloud(rel):
                try:
                    return bool(storage.head(storage.work_key(show, ep, rel)))
                except Exception:
                    return False
            if not out.exists() and not _in_cloud(timeline.PROXY):
                raise RuntimeError("the preview copy did not come back from the rented machine")
            if d and not dub_out.exists() and not _in_cloud(timeline.DUB_PREVIEW):
                raise RuntimeError("the dub preview copy did not come back from the rented machine")
            p = out if out.exists() else Path(work) / timeline.PROXY
        else:
            def prog(x):
                if time.time() - last[0] > 0.5:
                    last[0] = time.time(); self._set(j, progress=round(x, 4), message=f"making the preview copy {x:.0%}")
            with open(j["log_path"], "a") as f:
                p = timeline.make_proxy(work, a["path"], dur, prog, log=lambda m: f.write(m + "\n"))
                if d:                                      # the editor's exact copy of the dub audio
                    timeline.dub_preview(work, d["path"], log=lambda m: f.write(m + "\n"))
        if storage.configured():                           # so an editor on another machine can play this episode
            show, ep, _ = self.episode_context(j["episode_id"])
            storage.mirror(show, ep, timeline.PROXY, background=False)
            storage.mirror(show, ep, timeline.DUB_PREVIEW, background=False)
        self._set(j, status="done", progress=1, finished=now(), message=f"{p.name} ready")

    def _fn_sharpness(self, j):
        work = j["params"]["ctx"]["W"]
        a = self.db.one("SELECT path FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'", (j["episode_id"],))
        video = timeline.proxy_path(work) or a["path"]
        out = Path(work) / timeline.SHARPNESS
        tool = Path(timeline.__file__).with_name("sharpness_tool.py")
        cmd = [str(cfg.pipeline_python), str(tool), "--video", str(video), "--out", str(out)]
        if not self._worker_cmd(j, cmd):
            with open(j["log_path"], "a") as f:
                timeline.make_sharpness(work, video, cfg.pipeline_python, log=lambda m: f.write(m + "\n"))
        self._set(j, status="done", progress=1, finished=now(), message="sharpness measured")

    def ensure_steps(self, ep_id):
        """The latest run gets the steps today's plan has and it lacks (e.g. transitions put on cuts in the editor), and
        a step today's plan no longer has is skipped; needs and order follow the plan."""
        run = self.latest_run(ep_id)
        if not run:
            return
        show, ep, assets = self.episode_context(ep_id)
        ctx, plan = pipeline_steps.plan(show, ep, assets)
        jobs = {j["step"]: j for j in self.db.q("SELECT * FROM jobs WHERE episode_id=? AND run=?", (ep_id, run))}
        ADDABLE = {"transitions", "notes"}   # an old run only gains transitions + run notes here (never e.g. a lip scan)
        plan = [st for st in plan if st["step"] in jobs or st["step"] in ADDABLE]
        names = [st["step"] for st in plan]
        for k, st in enumerate(plan):
            prm = {"cmd": st["cmd"], "fn": st["fn"], "label": st["label"], "duration": st["duration"], "ai": st.get("ai", False),
                   "ctx": ctx}
            needs = st["needs"]
            if st["step"] in jobs and not all(n in names for n in needs):
                needs = jobs[st["step"]]["needs"] or []          # e.g. groups of an old run without a lip scan
            if st["step"] in jobs:
                self.db.update("jobs", jobs[st["step"]]["id"], ord=k, needs=needs, params=prm)
            else:
                self.db.insert("jobs", episode_id=ep_id, run=run, step=st["step"], ord=k, status="waiting", needs=needs,
                               resource=st["resource"], params=prm, created=now(),
                               log_path=str(Path(ep["work_dir"]) / "logs" / f"run{run}_{k:02d}_{st['step']}.log"))
        for step, j in jobs.items():
            if step != "transitions":
                continue                     # only transitions come and go with the editor's choices
            if step not in names and j["status"] not in ("done", "skipped"):
                self.db.update("jobs", j["id"], status="skipped", message="not needed any more", needs=[])
            elif step not in names:
                self.db.update("jobs", j["id"], needs=[])

    def refresh_pending(self, ep_id):
        """Today's commands for every step of the latest run that has not run yet (e.g. edited subtitles)."""
        run = self.latest_run(ep_id)
        ids = [j["id"] for j in self.db.q("SELECT id FROM jobs WHERE episode_id=? AND run=? AND status IN "
                                          "('waiting','blocked','cancelled')", (ep_id, run))]
        self._refresh_params(ids, ep_id)

    def _fn_translate(self, j):
        """The pipeline's translate (Google). If it fails and the AI gateway is set up, Claude translates the
        sentences Google left (translate --export-todo -> translate_ai -> translate --engine file --import)."""
        show, ep, _ = self.episode_context(j["episode_id"])
        W = Path((j.get("params") or {}).get("ctx", {}).get("W") or ep["work_dir"])
        if not self._wait_for_work_files(j, ["config.json"]):
            return
        if not (W / "config.json").is_file():
            with open(j["log_path"], "a", encoding="utf-8") as log:
                storage.fetch_work_files(show, ep, log=lambda m: (log.write(str(m).rstrip() + "\n"), log.flush()),
                                         force=True)
        try:
            self._run_cmd(j); return
        except RuntimeError as ex:
            if j["id"] in self.cancelled:
                raise
            from . import ai
            if not ai.available():
                raise RuntimeError(f"{ex} -- Google translation failed and no AI key is set; import an English SRT "
                                   "on the S2 track in the editor, or retry later") from None
            first = str(ex)
        from .pipeline import translate_ai
        cmd = self._expand(j["params"]["cmd"])
        arg = lambda k: cmd[cmd.index(k) + 1] if k in cmd else None
        W = Path(arg("--work")); todo, filled = W / "translate_todo.tsv", W / "translate_ai.tsv"
        with open(j["log_path"], "a", encoding="utf-8") as log:
            say = lambda m: (log.write(m + "\n"), log.flush(), self._set(j, message=m[-300:]))
            say(f"Google translation failed ({first[-160:]}); translating with the AI instead")
            def run(extra):
                full = cmd + extra
                log.write("\n$ " + " ".join(full) + "\n"); log.flush()
                if not (W / "config.json").is_file():
                    storage.fetch_work_files(show, ep, log=say, force=True)
                # This image has dubstitch.py, so the old path ran Google's fallback on Render without
                # config.json (WBT 101–109). Keep the fallback on a rented machine when the file is gone.
                if (not (W / "config.json").is_file()) or not (cfg.scripts / "dubstitch.py").is_file():
                    if not self._worker_cmd(j, full):
                        raise RuntimeError("translate needs config.json and the pipeline scripts; Retry after they are in R2")
                    return
                p = subprocess.run(full, cwd=self._local_cwd(), capture_output=True, text=True)
                log.write(p.stdout + p.stderr); log.flush()
                if p.returncode != 0:
                    raise RuntimeError(f"translate {' '.join(extra)}: exit {p.returncode}: {(p.stdout + p.stderr).strip()[-200:]}")
            run(["--export-todo", str(todo)])
            n = translate_ai.fill_todo(todo, filled, arg("--sl") or "it", arg("--tl") or "en", log=say)
            run(["--engine", "file", "--import", str(filled)])
            say(f"translated {n} sentences with the AI (Google was not available)")
        self._set(j, status="done", progress=1, finished=now(), eta=0, message=f"done with the AI ({n} sentences; Google failed)")

    def _auto_ai(self, j, work):
        """Rows to decide and the AI is set up: Claude decides them now (once per matching result), so the episode
        goes on to the editor by itself. Any failure leaves the rows for the Check matching screen."""
        from . import ai
        from .pipeline import ai_match
        w = Path(work); miss = dec.missing(work)
        if not miss or not ai.available() or not (w / "prep.json").exists():
            return
        try:
            prev = json.loads((w / ai_match.AUTO).read_text())
            # A failed try writes the same file with an error. Only a finished one is skipped, so a deploy
            # that can now run the check does not leave the rows waiting.
            if (prev.get("prep_mtime") == (w / "prep.json").stat().st_mtime and not prev.get("error")
                    and prev.get("rows") is not None):
                return
        except Exception:
            pass
        ep = self.db.one("SELECT show_id FROM episodes WHERE id=?", (j["episode_id"],))
        labels = (self.db.one("SELECT profile FROM shows WHERE id=?", (ep["show_id"],))["profile"] or {}).get("labels") or "IT,US"
        self._set(j, status="running", message=f"Claude is matching {len(miss)} rows by meaning…")
        with open(j["log_path"], "a", encoding="utf-8") as log:
            say = lambda m: (log.write(m + "\n"), log.flush(), self._set(j, message=m[-300:]))
            try:
                from . import main as _main                      # the show's localisation sheet (one per show)
                r = ai_match.auto_match(w, lambda spec: dec.check_spec(w, spec, cfg.pipeline_python, cfg.scripts, labels), log=say,
                                        names_csv=_main.names_sheet_for(j["episode_id"]))
                say(f"AI matching done: {r['rows']} rows, {len(r['not_found'])} not found (red in the editor), "
                    f"{len(r['low'])} unsure (yellow)")
            except Exception as ex:
                (w / ai_match.AUTO).write_text(json.dumps({"at": time.time(), "prep_mtime": (w / "prep.json").stat().st_mtime,
                                                           "error": str(ex)[:500]}))
                say(f"AI matching did not work ({str(ex)[:200]}): the rows wait on Check matching")

    def _stale(self, j):
        """A waiting loop (decisions, review) belongs to one attempt of its step: after a cancel + retry, or once the
        step is finished, the old loop stops, so two loops never fight over one step."""
        cur = self.db.one("SELECT status, attempt FROM jobs WHERE id=?", (j["id"],))
        return not cur or (cur["attempt"] or 0) != (j["attempt"] or 0) or cur["status"] in ("done", "cancelled", "failed", "skipped")

    def _fn_decisions_gate(self, j):
        work = j["params"]["ctx"]["W"]
        show, ep, _ = self.episode_context(j["episode_id"])
        while not self.stop.is_set():
            if j["id"] in self.cancelled:
                self._set(j, status="cancelled", finished=now(), message="cancelled"); return
            if self._stale(j):
                return
            miss = dec.missing(work)
            if miss is None:
                # Not a person. prep.json / decisions.json are not on this box (R2-only factory).
                self._set(j, status="running",
                          message="fetching matching files from the cloud (not waiting for a person)")
                try:
                    storage.fetch_work_files(show, ep, force=True)
                except Exception:
                    pass
                time.sleep(1.0)
                continue
            if envfile.flag("DUBSTUDIO_AUTO_GATES", False):
                self._auto_ai(j, work)
                if miss:
                    try:
                        dec.accept_auto(work)
                    except Exception:
                        pass
                    miss = dec.missing(work)
            if miss == []:
                self._write_spec(work)
                if cloud.stretch("cut"):
                    # mkdec checks the decisions. It runs as the first command of the cut visit, on the
                    # rented machine: this server does not have the pipeline scripts.
                    self._set(j, status="done", progress=1, finished=now(),
                              message="every flagged row has a decision; the cut continues on one machine")
                    return
                n = self._mkdec(j, work)
                self._set(j, status="done", progress=1, finished=now(),
                          message=f"every flagged row has a decision (mkdec.py: {n})")
                return
            msg = f"{len(miss)} rows need a decision: import decisions or accept the automatic matches"
            cur = self.db.one("SELECT status, message FROM jobs WHERE id=?", (j["id"],))
            if cur["status"] != "needs_input" or cur["message"] != msg:
                self._set(j, status="needs_input", message=msg, item=str(len(miss or [])))
            time.sleep(1.0)

    def _write_spec(self, work):
        """Decisions imported or accepted without a spec get one first (pure Python, no video)."""
        w = Path(work); spec = w / "spec.py"
        if not spec.exists():
            p = json.loads((w / "prep.json").read_text()); D = json.loads((w / "decisions.json").read_text())
            spec.write_text(dec.to_spec(p, D, "# Step 2 decisions (Dub Studio: imported or accepted)"))
        return spec

    def _mkdec(self, j, work):
        """Step 2 ends as the skill says: W/spec.py -> `mkdec.py W W/spec.py` -> decisions.json (every index checked
        against its text by the skill's own tool)."""
        w = Path(work); spec = self._write_spec(work)
        mkdec = cfg.scripts / "mkdec.py"
        if self._worker_cmd(j, [str(cfg.pipeline_python), str(mkdec), str(w), str(spec)]):
            return "mkdec on a rented machine"
        if not mkdec.is_file():
            raise RuntimeError("mkdec.py is not on this machine")
        r = subprocess.run([str(cfg.pipeline_python), str(mkdec), str(w), str(spec)],
                           capture_output=True, text=True, cwd=self._local_cwd(), timeout=300)
        with open(j["log_path"], "a", encoding="utf-8") as log:
            log.write(f"$ mkdec.py {w} {spec}\n{r.stdout}{r.stderr}")
        if r.returncode:
            raise RuntimeError("mkdec.py refused the decisions: " + ((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1][:300])
        return (r.stdout.strip().splitlines() or [""])[-1]

    def _fn_merged(self, j):
        """`merged.py W 95 > W/merged.txt`: the reading view. Always on this Mac — it is one script over
        prep.json. It only rides to the cloud as part of the match visit (`_run_match`). Sending it alone
        used to hang (TGS ep31: status stayed 'starting' while the work folder was packed for a rented machine)."""
        w = Path(j["params"]["ctx"]["W"])
        if not self._wait_for_work_files(j, ["prep.json"]):
            return
        if not (w / "prep.json").is_file():
            raise RuntimeError("cannot build the reading view: prep.json is not on this box or in the cloud. "
                               "Retry matching after that file is in R2 — do not Process again")
        merged = self._app_pipeline_script("merged.py")
        if not merged:
            raise RuntimeError("merged.py is not on this machine")
        r = subprocess.run([sys.executable, str(merged), str(w), "95"],
                           capture_output=True, text=True, cwd=self._local_cwd(), timeout=300)
        Path(j["log_path"]).parent.mkdir(parents=True, exist_ok=True)
        with open(j["log_path"], "a", encoding="utf-8") as log:
            log.write(f"$ merged.py {w} 95 > {w / 'merged.txt'}\n{r.stderr}")
        if r.returncode:
            raise RuntimeError("merged.py failed: " + ((r.stderr or "").strip().splitlines() or ["?"])[-1][:300])
        (w / "merged.txt").write_text(r.stdout, encoding="utf-8")
        self._set(j, status="done", progress=1, finished=now(), message=f"merged.txt: {r.stdout.count(chr(10))} lines")

    def _fn_run_notes(self, j):
        """The skill's `run_notes.md` in the output folder: inputs, decisions, lip scan, groups, final check, run times."""
        ep_id = j["episode_id"]; ctx = j["params"]["ctx"]; w = Path(ctx["W"]); out = Path(ctx["OUT"])
        ep = self.db.one("SELECT * FROM episodes WHERE id=?", (ep_id,))
        show = self.db.one("SELECT * FROM shows WHERE id=?", (ep["show_id"],))
        jobs = self.db.q("SELECT * FROM jobs WHERE episode_id=? AND run=? ORDER BY ord", (ep_id, j["run"]))
        L = [f"# {show['code']} ep{ep['number']} — run notes (Dub Studio, run {j['run']})", "",
             f"Written {time.strftime('%Y-%m-%d %H:%M')}.", "", "## Inputs"]
        for a in self.db.q("SELECT kind, path FROM assets WHERE episode_id=? AND role='input' ORDER BY kind", (ep_id,)):
            L.append(f"- {a['kind']}: `{a['path']}`")
        if (w / "names.csv").exists():
            L.append(f"- names for Whisper (from the localisation sheet): `{w / 'names.csv'}`")
        L += ["", "## Step 2 decisions"]
        try:
            D = json.loads((w / "decisions.json").read_text())
            L.append("- " + ", ".join(f"{k} {len(v)}" for k, v in D.items()))
        except Exception:
            L.append("- decisions.json not found")
        try:
            A = json.loads((w / "ai_auto.json").read_text())
            if A.get("rows") is not None:
                L.append(f"- Claude decided {A.get('rows')} rows; not found: {A.get('not_found') or []}; unsure: {A.get('low') or []}")
        except Exception:
            pass
        L += ["", "## Lip scan and groups"]
        for step in ("lip_gpu", "lip_mesh", "lip_merge", "lipsync", "groups"):
            jb = next((x for x in jobs if x["step"] == step), None)
            if jb:
                tail = [ln for ln in self.log_tail(jb["log_path"], 6) if ln.strip()][-2:]
                L.append(f"- {step}: {jb['status']}" + (f" — {' / '.join(t.strip()[:160] for t in tail)}" if tail else ""))
        L += ["", "## Final check"]
        chk = next((x for x in jobs if x["step"] == "check"), None)
        lines = self.log_tail(chk["log_path"], 60) if chk else []
        L += [f"- {ln}" for ln in lines if ln.startswith(("OK ", "BAD", "FINAL"))] or ["- (no check output)"]
        L += ["", "## Run times", "| step | status | time |", "|---|---|---|"]
        for x in jobs:
            t = f"{x['finished'] - x['started']:.0f} s" if x["started"] and x["finished"] else ""
            L.append(f"| {x['step']} | {x['status']} | {t} |")
        out.mkdir(parents=True, exist_ok=True)
        report = cfg.scripts / "run_report.py"
        if (w / "run_times.tsv").exists() and report.is_file():
            rt = out / f"{show['code'].lower()}_ep{ep['number']}_run_times.md"
            r = subprocess.run([str(cfg.pipeline_python), str(report), str(w), str(rt)],
                               capture_output=True, text=True, cwd=self._local_cwd(), timeout=120)
            if r.returncode == 0 and rt.exists():
                L += ["", "## Time and Claude cost of every step", "", rt.read_text(encoding="utf-8").strip()]
        (out / "run_notes.md").write_text("\n".join(L) + "\n", encoding="utf-8")
        self._set(j, status="done", progress=1, finished=now(), message=f"{out / 'run_notes.md'}")

    # ---------- the scene edit (docs/06; the same as run_episode.sh) ----------
    def _fn_scene_edit(self, j):
        """EP69-71 local scene edit: keep the grouping cut, Opus only fixes slow leftover units with ranked B-roll."""
        ctx = j["params"]["ctx"]; W = Path(ctx["W"]); LIB = ctx["library_dir"]
        g = W / "manifest_groups.json"
        keep = W / "manifest_groups.grouping.json"
        if g.exists() and not keep.exists():
            shutil.copy(g, keep)
        auto = W / "manifest_groups.auto.json"
        src_auto = keep if keep.exists() else g
        if src_auto.exists() and not auto.exists():
            shutil.copy(src_auto, auto)
        from . import config as config_mod
        PL = config_mod.cfg.scene_dir
        cmd = ["{py}", str(PL / "slowfix_edit.py"), str(W), str(LIB)]
        until = ctx.get("scene_until_s")
        env_extra = {}
        if until:
            env_extra["SLOWFIX_UNTIL"] = str(until)
        from . import cloud
        if not until and cloud.stretch("scene") and cloud.ai_key_allowed():
            return self._run_scene_visit(j, W, LIB)
        if env_extra:
            old = os.environ.get("SLOWFIX_UNTIL")
            os.environ["SLOWFIX_UNTIL"] = env_extra["SLOWFIX_UNTIL"]
            try:
                self._run_cmd(j, cmd, force_local=True)
            finally:
                if old is None:
                    os.environ.pop("SLOWFIX_UNTIL", None)
                else:
                    os.environ["SLOWFIX_UNTIL"] = old
        else:
            self._run_cmd(j, cmd, force_local=True)

    def _run_scene_visit(self, j, work, library):
        """Scene edit, apply the cut, fast trim — one CPU after the face map. Scripts travel with the job."""
        from . import cloud
        if not self._wait_for_work_files(j, cloud.required_work_files("facemap")):
            return
        t0 = time.time()
        by = self._jobs_by_step(j)
        script = cloud.stage_scene(work)
        named, to_mark = [], []
        named.append(("scene_edit", [str(cfg.pipeline_python), str(script), str(work), str(library)]))
        to_mark.append(j)
        use = by.get("use_scene")
        if use and use["status"] not in DONE:
            app_root = Path(work) / "_app"
            named.append(("use_scene", [
                str(cfg.pipeline_python), "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "from dubstudio.pipeline.apply_scene import apply; "
                "n = apply(sys.argv[2]); "
                "print(f'the scene edit is the edit list: {n} pieces')",
                str(app_root), str(work)]))
            to_mark.append(use)
        ft = by.get("fast_trim")
        if ft and ft["status"] not in DONE and (ft["params"] or {}).get("cmd"):
            named.append(("fast_trim", self._expand(ft["params"]["cmd"])))
            to_mark.append(ft)
        self._send_visit(j, "scene", named, to_mark, t0, "scene edit through fast trim on one rented machine")

    @staticmethod
    def use_scene(W):
        """Keep the automatic edit as manifest_groups.auto.json (once) and write the scene edit as manifest_groups.json,
        its last piece stretched to the end of the dub. Prefers scene_slowfix (EP69-71) over sceneedit.py."""
        W = str(W)
        grouping = f"{W}/manifest_groups.grouping.json"
        auto = f"{W}/manifest_groups.auto.json"
        if not os.path.exists(auto):
            src = grouping if os.path.exists(grouping) else f"{W}/manifest_groups.json"
            shutil.copy(src, auto)
        slow = f"{W}/scene_slowfix/manifest_slowfix.json"
        scene = f"{W}/scene/scene_manifest.json"
        src_scene = slow if os.path.exists(slow) else scene
        a = json.load(open(f"{W}/manifest_groups.auto.json")); s = json.load(open(src_scene))
        # Claude may leave a hole or a placeholder with no footage. If matching already had a clip there, use it
        # (TAB EP11/12: 26–32 s black after a "placeholder" piece). Matching-not-found stays a hole.
        from . import timeline as tlm
        s["segments"] = tlm.fill_scene_from_matching(s.get("segments") or [], a.get("segments") or [],
                                                     a["total_ms"])
        s["total_ms"] = a["total_ms"]
        if not s["segments"]:
            s["segments"] = list(a.get("segments") or [])
        L = s["segments"][-1]
        span = L["it_end_ms"] - L["it_start_ms"]
        # Claude writes seconds to 2 decimals (prompt is {T1:.2f}), so the last piece is often 1–10 ms
        # short or long of the dub. Stretch a short tail; trim a long one. Same speed either way.
        if span > 0 and abs(L["it_end_ms"] - a["total_ms"]) > 1:
            k = (a["total_ms"] - L["it_start_ms"]) / span
            L["us_end_ms"] = L["us_start_ms"] + (L["us_end_ms"] - L["us_start_ms"]) * k
            L["it_end_ms"] = a["total_ms"]
        json.dump(s, open(f"{W}/manifest_groups.json", "w"), indent=1, ensure_ascii=False)
        return len(s["segments"])

    def _fn_use_scene(self, j):
        n = self.use_scene(j["params"]["ctx"]["W"])
        with open(j["log_path"], "a", encoding="utf-8") as f:
            f.write(f"{n} pieces\n")
        self._set(j, status="done", progress=1, finished=now(), message=f"the scene edit is the edit list: {n} pieces")

    def _fn_trans_plan(self, j):
        """scene_transitions.py (a transition at every clip switch except the cut INTO a B-roll), then the editor's own
        picks on cuts on top (none picked = exactly the pipeline's plan)."""
        self._run_cmd(j, j["params"]["cmd"])
        n = timeline.apply_editor_transitions(j["params"]["ctx"]["W"])
        if n:
            with open(j["log_path"], "a", encoding="utf-8") as f:
                f.write(f"the editor's own transitions on {n} cuts kept on top of the plan\n")
            self._set(j, message=f"plan made; the editor's own transitions on {n} cuts kept")

    def _fn_scene_sheet(self, j):
        """The scene-edit sheet in the output folder (and the review sheet `check` looks for)."""
        ctx = j["params"]["ctx"]; W = Path(ctx["W"])
        ep = self.db.one("SELECT * FROM episodes WHERE id=?", (j["episode_id"],))
        show = self.db.one("SELECT * FROM shows WHERE id=?", (ep["show_id"],))
        cmd, out = self._scene_sheet_argv(j, show, ep)
        slow_xlsx = W / "scene_slowfix" / "scene_edit.xlsx"
        if slow_xlsx.is_file():
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(slow_xlsx, out)
            Path(j["log_path"]).parent.mkdir(parents=True, exist_ok=True)
            with open(j["log_path"], "a", encoding="utf-8") as f:
                f.write(f"copied {slow_xlsx} -> {out}\n")
            self._set(j, status="done", progress=1, finished=now(), message=f"scene-edit sheet {out}")
        else:
            self._run_cmd(j, cmd)
        cp = W / "config.json"; c = json.load(open(cp)) if cp.exists() else {}
        c["groups_review"] = out                          # `check` looks for the review sheet here
        json.dump(c, open(cp, "w"), indent=1)

    def _fn_review_gate(self, j):
        """Waits until Render is pressed, or auto-renders a first draft when there is no black gap."""
        work = j["params"]["ctx"]["W"]
        msg = "ready for review: open the editor, check the clips, then press Render"
        auto_tried = False
        while not self.stop.is_set():
            if j["id"] in self.cancelled:
                self._set(j, status="cancelled", finished=now(), message="cancelled"); return
            if self._stale(j):
                return
            if timeline.take_render_request(work):
                self._set(j, status="done", progress=1, finished=now(), message="the editor pressed Render")
                return
            if not auto_tried:
                auto_tried = True
                if envfile.flag("DUBSTUDIO_AUTO_GATES", False):
                    try:
                        show, ep, _ = self.episode_context(j["episode_id"])
                        if not (Path(work) / "manifest_groups.json").is_file():
                            storage.fetch_work_files(show, ep, force=True)
                        v1 = timeline.build_v1(work)
                        black = (timeline.gaps(v1) if v1 else None) or (v1 or {}).get("holes") or []
                        if v1 and not black:
                            timeline.request_render(work, v1.get("version") or 1)
                            self._set(j, message="no black gap: first draft render started")
                            continue
                        if black:
                            self._set(j, status="needs_input",
                                      message="black gap in the cut: fill it in the editor, then press Render")
                    except Exception:
                        pass
            cur = self.db.one("SELECT status, message FROM jobs WHERE id=?", (j["id"],))
            if (cur.get("message") or "").startswith("black gap"):
                time.sleep(1.0)
                continue
            if cur["status"] != "needs_input" or cur["message"] != msg:
                self._set(j, status="needs_input", message=msg)
            time.sleep(1.0)

    def rerun_from(self, ep_id, step):
        """Run `step` and every step after it again (in the latest run), e.g. render after an edit."""
        run = self.latest_run(ep_id)
        jobs = self.db.q("SELECT * FROM jobs WHERE episode_id=? AND run=?", (ep_id, run))
        by = {j["step"]: j for j in jobs}
        if step not in by:
            raise ValueError(f"this run has no step {step}")
        todo = {step}; grew = True
        while grew:
            grew = False
            for j in jobs:
                if j["step"] not in todo and any(n in todo for n in (j["needs"] or [])):
                    todo.add(j["step"]); grew = True
        if any(by[s]["status"] in ACTIVE for s in todo):
            raise ValueError("these steps are running now")
        if todo & {"lipsync", "groups"}:
            _, ep, _ = self.episode_context(ep_id)
            drop_stale_edit_files(ep["work_dir"])
        ids = [by[s]["id"] for s in todo]
        self._refresh_params(ids, ep_id)
        for s in todo:
            j = by[s]; self.cancelled.discard(j["id"])
            self._set(j, status="waiting", attempt=(j["attempt"] or 0) + 1, progress=0, finished=None, message="")
        if self._room_for(ep_id):
            self.db.update("episodes", ep_id, status="processing", queued=None, resume=None, updated=now())
            bus.publish("episode_status", ep_id, status="processing")
            self.wake.set()
        else:
            self._park(ep_id)
        return sorted(todo, key=lambda s: by[s]["ord"])

    @staticmethod
    def _kill(p):
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except Exception:
            try:
                p.terminate()
            except Exception:
                pass
