"""Job runner: runs each episode's step graph in background threads, with resource slots, live progress,
cancel / retry, and resume after a restart (the pipeline tools continue where they stopped)."""
import json, os, re, shutil, signal, subprocess, sys, threading, time, traceback
from pathlib import Path
from .config import cfg
from .db import now
from .events import bus
from .pipeline import steps as pipeline_steps, decisions as dec
from . import scan, timeline

DONE = {"done", "skipped"}
ACTIVE = {"running"}
PROG_PATTERNS = [
    re.compile(r"(\d+)/(\d+) (?:chunks|segments|pieces|rows|sentences)\b"),
    re.compile(r"rendered (\d+)/(\d+)"),
    re.compile(r"chunk (\d+) done.*?of (\d+)"),
]
TIME_PAT = re.compile(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)(?:s\b| in\b)")   # "12.0-27.5s", lipgpu "done 45-60 in 23 s"
NOISE = re.compile(r"Warning:|^\s+torch\.")   # library warnings: kept in the log, not shown as the last line
PIECE_PAT = re.compile(r"piece\s+(\d+)\s*/\s*(\d+)", re.I)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


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

    # ---------- lifecycle ----------
    def start(self):
        for j in self.db.q("SELECT id, log_path FROM jobs WHERE status IN ('running','needs_input')"):
            self._stop_orphan(j["log_path"])
            self.db.update("jobs", j["id"], status="waiting", message="resumed after a restart")
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
            if isinstance(a["probe"], dict) and a["probe"].get("duration"):
                assets[f"_dur_{a['kind']}"] = a["probe"]["duration"]
        return show, ep, assets

    def create_run(self, ep_id):
        show, ep, assets = self.episode_context(ep_id)
        missing = [k for k in ("src_video", "dub_audio") if not assets.get(k)]
        if missing:
            raise ValueError(f"missing input files: {', '.join(missing)}")
        Path(ep["work_dir"]).mkdir(parents=True, exist_ok=True)
        Path(ep["output_dir"]).mkdir(parents=True, exist_ok=True)
        ctx, plan = pipeline_steps.plan(show, ep, assets)
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
        self.db.update("episodes", ep_id, status="processing", updated=now())
        bus.publish("run_created", ep_id, run=run, steps=len(plan))
        self.wake.set()
        return run

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
        if j and j["status"] in ("waiting", "needs_input"):
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
        self.db.update("episodes", j["episode_id"], status="processing", updated=now())
        self.wake.set()

    def poke(self):
        self.wake.set()

    # ---------- loop ----------
    def _loop(self):
        while not self.stop.is_set():
            try:
                self._tick()
            except Exception:
                traceback.print_exc()
            self.wake.wait(0.5); self.wake.clear()

    def _tick(self):
        jobs = self.db.q("SELECT * FROM jobs WHERE status IN ('waiting','running','needs_input') ORDER BY episode_id, run, ord")
        self._keep_awake(any(j["status"] == "running" for j in jobs))     # not for steps parked behind the review
        if not jobs:
            self._episode_status()
            return
        busy = {"cpu": 0, "gpu": 0}
        for j in jobs:
            if j["status"] == "running":
                busy[j["resource"]] = busy.get(j["resource"], 0) + 1
        slots = {"cpu": cfg.cpu_slots, "gpu": cfg.gpu_slots}
        for j in jobs:
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
            if any(d["status"] not in DONE for d in deps):
                continue
            res = j["resource"]
            if res in slots and busy.get(res, 0) >= slots[res]:
                continue
            if res in slots:
                busy[res] = busy.get(res, 0) + 1
            self.cancelled.discard(j["id"])
            self._set(j, status="running", started=now(), progress=0, message="starting")
            threading.Thread(target=self._run_job, args=(j["id"],), daemon=True).start()
        self._episode_status()

    def _episode_status(self):
        for e in self.db.q("SELECT id, status FROM episodes WHERE status IN ('processing','needs_input','review','failed','cancelled')"):
            run = self.latest_run(e["id"])
            js = self.db.q("SELECT step, status FROM jobs WHERE episode_id=? AND run=?", (e["id"], run))
            st = [x["status"] for x in js]
            if st and all(s in DONE for s in st):
                new = "done"
            elif "failed" in st:
                new = "failed"
            elif "needs_input" in st and "running" not in st:
                waiting = [x["step"] for x in js if x["status"] == "needs_input"]
                new = "review" if waiting == ["review"] else "needs_input"
            elif all(s in DONE | {"cancelled", "blocked"} for s in st):
                new = "cancelled"
            else:
                new = "processing"
            if new != e["status"]:
                self.db.update("episodes", e["id"], status=new, updated=now())
                bus.publish("episode_status", e["id"], status=new)

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
        """The AI gateway's spend so far ($), or 'NA' (no AI set up / not reachable): quick, never blocks a step long."""
        from . import ai
        if not ai.available():
            return "NA"
        try:
            i = ai._req("/key/info", timeout=8); i = i.get("info", i)
            return str(i.get("spend")) if i.get("spend") is not None else "NA"
        except Exception:
            return "NA"

    @staticmethod
    def ai_gate():
        """Before a step that needs the AI (library, face map, scene edit): the gateway must answer and budget must be
        left. Never skip the step: it stops here with a message for the owner (docs/06)."""
        from . import ai
        if not ai.available():
            raise RuntimeError("the AI gateway is not set up (Settings → AI): this step needs Claude")
        try:
            i = ai._req("/key/info", timeout=15); i = i.get("info", i)
        except Exception as ex:
            raise RuntimeError(f"the AI gateway is not reachable ({str(ex)[:80]}) — turn the VPN on, then Retry this step")
        spend, cap = i.get("spend"), i.get("max_budget")
        if spend is not None and cap is not None and float(spend) >= float(cap):
            raise RuntimeError(f"AI budget used up: ${float(spend):.2f} of ${float(cap):.2f} — ask to raise it, then Retry")

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
            if rc is not None and (rc < 0 or rc in (137, 143)) and job_id not in self.cancelled and (cur["attempt"] or 0) < 1:
                with open(j["log_path"], "a") as f:
                    f.write(f"\n(stopped by signal, exit {rc}: trying again once)\n")
                self._set(j, status="waiting", attempt=(cur["attempt"] or 0) + 1, progress=0,
                          message=f"stopped unexpectedly (exit {rc}); trying again")
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
    def _expand(cmd):
        """{py} = the pipeline python, {fpy} = the face map's python, {scripts} = the tools' scripts folder."""
        return [str(cfg.pipeline_python) if c == "{py}" else str(cfg.face_python) if c == "{fpy}"
                else c.replace("{scripts}", str(cfg.scripts)) for c in cmd]

    def _run_cmd(self, j, cmd=None):
        prm = j["params"]
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
                p = subprocess.Popen(cmd, cwd=str(cfg.scripts), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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
        self._set(j, status="done", progress=1, finished=now(), eta=0,
                  message=f"done in {time.time() - t0:.0f} s" + (f" ({rounds} rounds)" if rounds > 1 else ""))

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

    # ---------- internal steps ----------
    def _fn_ingest(self, j):
        ep_id = j["episode_id"]; problems = []
        rows = self.db.q("SELECT * FROM assets WHERE episode_id=? AND role='input'", (ep_id,))
        for k, a in enumerate(rows):
            self._set(j, progress=k / max(len(rows), 1), message=f"checking {Path(a['path']).name}")
            info, probs = scan.check_asset(a["kind"], a["path"])
            problems += probs
            if info:
                self.db.update("assets", a["id"], size=info.get("size"), fingerprint=info.get("fingerprint"),
                               mtime=info.get("mtime"), probe=info.get("probe"))
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
        def prog(x):
            if time.time() - last[0] > 0.5:
                last[0] = time.time(); self._set(j, progress=round(x, 4), message=f"making the preview copy {x:.0%}")
        with open(j["log_path"], "a") as f:
            p = timeline.make_proxy(work, a["path"], dur, prog, log=lambda m: f.write(m + "\n"))
            d = self.db.one("SELECT path FROM assets WHERE episode_id=? AND kind='dub_audio' AND role='input'", (j["episode_id"],))
            if d:                                          # the editor's exact copy of the dub audio
                timeline.dub_preview(work, d["path"], log=lambda m: f.write(m + "\n"))
        self._set(j, status="done", progress=1, finished=now(), message=f"{p.name} ready")

    def _fn_sharpness(self, j):
        work = j["params"]["ctx"]["W"]
        a = self.db.one("SELECT path FROM assets WHERE episode_id=? AND kind='src_video' AND role='input'", (j["episode_id"],))
        video = timeline.proxy_path(work) or a["path"]
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
                log.write("\n$ " + " ".join(cmd + extra) + "\n"); log.flush()
                p = subprocess.run(cmd + extra, cwd=str(cfg.scripts), capture_output=True, text=True)
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
            if prev.get("prep_mtime") == (w / "prep.json").stat().st_mtime:
                return                                             # already tried for this matching result
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
        self._auto_ai(j, work)
        while not self.stop.is_set():
            if j["id"] in self.cancelled:
                self._set(j, status="cancelled", finished=now(), message="cancelled"); return
            if self._stale(j):
                return
            miss = dec.missing(work)
            if miss == []:
                n = self._mkdec(j, work)
                self._set(j, status="done", progress=1, finished=now(),
                          message=f"every flagged row has a decision (mkdec.py: {n})")
                return
            msg = (f"{len(miss)} rows need a decision: import decisions or accept the automatic matches"
                   if miss else "waiting for the matching files")
            cur = self.db.one("SELECT status, message FROM jobs WHERE id=?", (j["id"],))
            if cur["status"] != "needs_input" or cur["message"] != msg:
                self._set(j, status="needs_input", message=msg, item=str(len(miss or [])))
            time.sleep(1.0)

    def _mkdec(self, j, work):
        """Step 2 ends as the skill says: W/spec.py -> `mkdec.py W W/spec.py` -> decisions.json (every index checked
        against its text by the skill's own tool). Decisions imported or accepted without a spec get one first."""
        w = Path(work); spec = w / "spec.py"
        if not spec.exists():
            p = json.loads((w / "prep.json").read_text()); D = json.loads((w / "decisions.json").read_text())
            spec.write_text(dec.to_spec(p, D, "# Step 2 decisions (Dub Studio: imported or accepted)"))
        r = subprocess.run([str(cfg.pipeline_python), str(cfg.scripts / "mkdec.py"), str(w), str(spec)],
                           capture_output=True, text=True, cwd=str(cfg.scripts), timeout=300)
        with open(j["log_path"], "a", encoding="utf-8") as log:
            log.write(f"$ mkdec.py {w} {spec}\n{r.stdout}{r.stderr}")
        if r.returncode:
            raise RuntimeError("mkdec.py refused the decisions: " + ((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1][:300])
        return (r.stdout.strip().splitlines() or [""])[-1]

    def _fn_merged(self, j):
        """`merged.py W 95 > W/merged.txt`: the reading view for Step 2 (the dub rows with their source lines)."""
        w = Path(j["params"]["ctx"]["W"])
        r = subprocess.run([str(cfg.pipeline_python), str(cfg.scripts / "merged.py"), str(w), "95"],
                           capture_output=True, text=True, cwd=str(cfg.scripts), timeout=300)
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
        if (w / "run_times.tsv").exists():           # the owner's time + Claude-cost table (run_report.py, as run_episode.sh)
            rt = out / f"{show['code'].lower()}_ep{ep['number']}_run_times.md"
            r = subprocess.run([str(cfg.pipeline_python), str(cfg.scripts / "run_report.py"), str(w), str(rt)],
                               capture_output=True, text=True, cwd=str(cfg.scripts), timeout=120)
            if r.returncode == 0 and rt.exists():
                L += ["", "## Time and Claude cost of every step", "", rt.read_text(encoding="utf-8").strip()]
        (out / "run_notes.md").write_text("\n".join(L) + "\n", encoding="utf-8")
        self._set(j, status="done", progress=1, finished=now(), message=f"{out / 'run_notes.md'}")

    # ---------- the scene edit (docs/06; the same as run_episode.sh) ----------
    def _fn_scene_edit(self, j):
        """Copy the inputs into W/scene/ (the automatic edit: manifest_groups.auto.json if kept, else manifest_groups.json),
        then sceneedit.py over the whole episode, 8 Claude calls at a time. A retry reuses Claude's cached answers."""
        ctx = j["params"]["ctx"]; W = Path(ctx["W"]); SW = Path(ctx["scene_dir"]); LIB = ctx["library_dir"]
        SW.mkdir(parents=True, exist_ok=True)
        for f in ("sentences_dub.json", "sentences_src.json", "lipsync.json"):
            shutil.copy(W / f, SW / f)
        auto = W / "manifest_groups.auto.json"
        src = auto if auto.exists() else W / "manifest_groups.json"
        shutil.copy(src, SW / "manifest_groups.json")
        tot = ctx.get("scene_until_s") or json.load(open(src))["total_ms"] / 1000     # a test window (e.g. 5 min) or all
        PL = cfg.scene_dir
        self._run_cmd(j, ["{py}", str(PL / "sceneedit.py"), str(SW), str(LIB), "0", str(tot), str(SW / "scene_manifest.json"),
                          "--parallel", str(ctx.get("scene_parallel") or 8)])

    @staticmethod
    def use_scene(W):
        """Keep the automatic edit as manifest_groups.auto.json (once) and write the scene edit as manifest_groups.json,
        its last piece stretched to the end of the dub (run_episode.sh's use_scene, the same Python). -> pieces"""
        W = str(W)
        if not os.path.exists(f"{W}/manifest_groups.auto.json"):
            shutil.copy(f"{W}/manifest_groups.json", f"{W}/manifest_groups.auto.json")
        a = json.load(open(f"{W}/manifest_groups.auto.json")); s = json.load(open(f"{W}/scene/scene_manifest.json"))
        s["total_ms"] = a["total_ms"]; L = s["segments"][-1]
        if L["it_end_ms"] < a["total_ms"] - 1:
            k = (a["total_ms"] - L["it_start_ms"]) / (L["it_end_ms"] - L["it_start_ms"])
            L["us_end_ms"] = L["us_start_ms"] + (L["us_end_ms"] - L["us_start_ms"]) * k; L["it_end_ms"] = a["total_ms"]
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
        ep = self.db.one("SELECT number, show_id FROM episodes WHERE id=?", (j["episode_id"],))
        code = self.db.one("SELECT code FROM shows WHERE id=?", (ep["show_id"],))["code"].upper()
        PL = cfg.scene_dir; out = ctx["name_scene_sheet"]
        en = ctx.get("en_srt_burn") or ctx["name_en_srt"]
        self._run_cmd(j, ["{py}", str(PL / "sceneedit_sheet.py"), str(Path(ctx["scene_dir"]) / "scene_manifest.edl.json"),
                          str(Path(ctx["library_dir"]) / "broll_library.json"), en, out, f"{code} ep{ep['number']}: scene edit"])
        cp = W / "config.json"; c = json.load(open(cp)) if cp.exists() else {}
        c["groups_review"] = out                          # `check` looks for the review sheet here
        json.dump(c, open(cp, "w"), indent=1)

    def _fn_review_gate(self, j):
        """Waits until the editor presses Render (timeline.request_render); the editor wrote manifest_groups.json."""
        work = j["params"]["ctx"]["W"]
        msg = "ready for review: open the editor, check the clips, then press Render"
        while not self.stop.is_set():
            if j["id"] in self.cancelled:
                self._set(j, status="cancelled", finished=now(), message="cancelled"); return
            if self._stale(j):
                return
            if timeline.take_render_request(work):
                self._set(j, status="done", progress=1, finished=now(), message="the editor pressed Render")
                return
            cur = self.db.one("SELECT status, message FROM jobs WHERE id=?", (j["id"],))
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
        ids = [by[s]["id"] for s in todo]
        self._refresh_params(ids, ep_id)
        for s in todo:
            j = by[s]; self.cancelled.discard(j["id"])
            self._set(j, status="waiting", attempt=(j["attempt"] or 0) + 1, progress=0, finished=None, message="")
        self.db.update("episodes", ep_id, status="processing", updated=now())
        bus.publish("episode_status", ep_id, status="processing")
        self.wake.set()
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
