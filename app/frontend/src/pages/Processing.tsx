import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Episode, Job, useLive } from "../api";
import { Bar, Chip, ErrorBox, fmtDur, JOB_STATUS } from "../ui";
import { groupJobs, liveLabel, VisitGroup } from "../visits";

const ACTIVE = ["running", "waiting", "needs_input"];

export default function Processing({ ep, reload, goMatching }: { ep: Episode; reload: () => void; goMatching: () => void }) {
  const [open, setOpenRaw] = useState<number | null>(null);
  const [openVisit, setOpenVisit] = useState<string | null>(null);
  const [picked, setPicked] = useState(false);
  const setOpen = (id: number | null) => { setPicked(true); setOpenRaw(id); };
  const [err, setErr] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(t); }, []);
  const running = ep.jobs.find((j) => j.status === "running") || ep.jobs.find((j) => j.status === "failed");
  useEffect(() => { if (!picked && running) setOpenRaw(running.id); }, [running?.id, picked]);

  if (!ep.jobs.length) return <div className="empty"><p>This episode has not been processed yet.</p><p className="muted">Check the files on the Files tab, then press Start processing.</p></div>;

  const act = async (f: () => Promise<any>) => { setErr(null); try { await f(); reload(); } catch (e: any) { setErr(e.message); } };
  const again = ep.jobs.filter((j) => (j.attempt || 0) > 0 && j.started);
  const pass = again.length && again.some((j) => j.step === "render") ? again : ep.jobs;
  const started = Math.min(...pass.filter((j) => j.started).map((j) => j.started!));
  const ended = ep.jobs.every((j) => !ACTIVE.includes(j.status));
  const lastEnd = Math.max(...ep.jobs.map((j) => j.finished || 0));
  const doneN = ep.jobs.filter((j) => ["done", "skipped"].includes(j.status)).length;
  const prog = ep.jobs.reduce((a, j) => a + (["done", "skipped"].includes(j.status) ? 1 : j.progress || 0), 0) / ep.jobs.length;
  const needs = ep.jobs.find((j) => j.status === "needs_input");
  const { groups, rest } = groupJobs(ep.jobs);
  const nowLine = liveLabel(ep.jobs);
  const visitsDone = groups.filter((g) => g.status === "done").length;

  return (
    <div>
      <div className="panel">
        <div className="panel-row">
          <div className="grow">
            <b>{visitsDone} of {groups.length} visits done</b>
            <span className="muted"> · {doneN} of {ep.jobs.length} steps</span>
            {isFinite(started) && <span className="muted"> · {pass === ep.jobs ? "time used" : "last render"} {fmtDur((ended && lastEnd ? lastEnd : now) - started)}</span>}
            {nowLine && !ended && <div className="small-text" style={{ marginTop: 6 }}><b>Now:</b> {nowLine}</div>}
            <Bar value={prog} color={ep.status === "failed" ? "red" : ep.status === "done" ? "green" : ""} />
          </div>
          {!ended && <button className="danger" onClick={() => confirm("Stop every step of this episode?") && act(() => api.post(`/api/episodes/${ep.id}/cancel`))}>Cancel all</button>}
          {ended && <button onClick={() => confirm("Start a new run of every step? Each tool resumes or redoes its own work.") && act(() => api.post(`/api/episodes/${ep.id}/process`))}>Process again</button>}
        </div>
        {needs && needs.step !== "review" && (
          <div className="warn row-between">
            <span><b>Waiting for you:</b> {needs.message}</span>
            <button className="primary" onClick={goMatching}>Check matching</button>
          </div>
        )}
        {needs && needs.step === "review" && (
          <div className="callout" style={{ margin: "10px 0 0" }}>
            <span className="grow"><b>Ready for review.</b> <span className="muted">Render as-is if there is no gap, or open the editor to check first.</span></span>
            <button className="primary" onClick={() => {
              if (!confirm(`Render EP ${ep.number} from its current timeline (no editor)?`)) return;
              act(() => api.post(`/api/episodes/${ep.id}/timeline/render`));
            }}>Render</button>
            <Link className="button" to={`/episodes/${ep.id}/editor`}>Open editor →</Link>
          </div>
        )}
      </div>
      <ErrorBox error={err} />
      <div className="steps">
        {groups.map((g) => (
          <VisitRow key={g.def.id} g={g} now={now}
            open={openVisit === g.def.id || (!picked && g.status === "running")}
            openJob={open}
            toggle={() => { setPicked(true); setOpenVisit(openVisit === g.def.id ? null : g.def.id); }}
            toggleJob={(id) => setOpen(open === id ? null : id)}
            cancel={(id) => act(() => api.post(`/api/jobs/${id}/cancel`))}
            retry={(id) => act(() => api.post(`/api/jobs/${id}/retry`))} />
        ))}
        {rest.map((j) => (
          <StepRow key={j.id} j={j} now={now} open={open === j.id} toggle={() => setOpen(open === j.id ? null : j.id)}
            cancel={() => act(() => api.post(`/api/jobs/${j.id}/cancel`))} retry={() => act(() => api.post(`/api/jobs/${j.id}/retry`))} />
        ))}
      </div>
    </div>
  );
}

function VisitRow({ g, now, open, openJob, toggle, toggleJob, cancel, retry }: {
  g: VisitGroup; now: number; open: boolean; openJob: number | null;
  toggle: () => void; toggleJob: (id: number) => void;
  cancel: (id: number) => void; retry: (id: number) => void;
}) {
  const live = g.live;
  const color = g.status === "failed" ? "red" : g.status === "done" ? "green" : g.status === "needs_input" ? "yellow" : "";
  const bar = g.jobs.length ? g.done / g.jobs.length + (live && live.status === "running" ? (live.progress || 0) / g.jobs.length : 0) : 0;
  const msg = live && ["running", "failed", "needs_input"].includes(live.status)
    ? (g.jobs.length > 1 ? `${live.label}: ${live.message || live.status}` : (live.message || ""))
    : "";
  return (
    <div className={`step visit ${g.status}`}>
      <div className="step-head" onClick={toggle}>
        <span className="caret">{open ? "▾" : "▸"}</span>
        <span className="step-label">
          {g.def.title}
          {g.def.machine ? <span className="tag">{g.def.machine}</span> : null}
          {g.def.steps.includes("facemap") ? <span className="tag">GPU</span> : null}
        </span>
        <span className="step-bar">{["running", "done", "failed"].includes(g.status) && <Bar value={g.status === "done" ? 1 : bar} color={color} />}</span>
        <span className="step-time muted">{g.done}/{g.jobs.length}</span>
        <Chip status={g.status} map={JOB_STATUS} />
        <span className="step-btns" onClick={(e) => e.stopPropagation()}>
          {g.status === "running" && live && <button className="small ghost" onClick={() => cancel(live.id)}>Cancel</button>}
          {g.status === "failed" && live && <button className="small" onClick={() => retry(live.id)}>Retry</button>}
        </span>
      </div>
      {msg && <div className={`step-msg ${g.status === "failed" ? "bad" : "muted"}`}>{msg}</div>}
      {open && (
        <div className="visit-inner">
          {g.jobs.map((j) => (
            <StepRow key={j.id} j={j} now={now} open={openJob === j.id} toggle={() => toggleJob(j.id)}
              cancel={() => cancel(j.id)} retry={() => retry(j.id)} nested />
          ))}
        </div>
      )}
    </div>
  );
}

function StepRow({ j, now, open, toggle, cancel, retry, nested }: {
  j: Job; now: number; open: boolean; toggle: () => void; cancel: () => void; retry: () => void; nested?: boolean;
}) {
  const took = j.started ? (j.finished || now) - j.started : null;
  const color = j.status === "failed" ? "red" : j.status === "done" ? "green" : j.status === "needs_input" ? "yellow" : "";
  return (
    <div className={`step ${j.status}${nested ? " nested" : ""}`}>
      <div className="step-head" onClick={toggle}>
        <span className="caret">{open ? "▾" : "▸"}</span>
        <span className="step-label">{j.label}{j.resource === "gpu" ? <span className="tag">GPU</span> : null}{(j.attempt || 0) > 0 ? <span className="tag">try {(j.attempt || 0) + 1}</span> : null}</span>
        <span className="step-bar">{["running", "done", "failed"].includes(j.status) && <Bar value={j.status === "done" ? 1 : j.progress || 0} color={color} />}</span>
        <span className="step-time muted">
          {j.status === "running" && j.eta ? `${fmtDur(j.eta)} left` : took != null && j.status !== "waiting" ? fmtDur(took) : ""}
        </span>
        <Chip status={j.status} map={JOB_STATUS} />
        <span className="step-btns" onClick={(e) => e.stopPropagation()}>
          {["running", "waiting", "needs_input"].includes(j.status) && <button className="small ghost" onClick={cancel}>Cancel</button>}
          {["failed", "cancelled", "blocked"].includes(j.status) && <button className="small" onClick={retry}>Retry</button>}
        </span>
      </div>
      {j.message && <div className={`step-msg ${j.status === "failed" ? "bad" : "muted"}`}>{j.message}</div>}
      {open && <LogTail job={j} />}
    </div>
  );
}

function LogTail({ job }: { job: Job }) {
  const [lines, setLines] = useState<string[] | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    api.get<{ lines: string[] }>(`/api/jobs/${job.id}/log?tail=40`).then((r) => setLines(r.lines)).catch(() => setLines([]));
  }, [job.id, n, job.status]);
  const [pending, setPending] = useState(false);
  useLive((e) => { if (e.job_id === job.id && !pending) { setPending(true); setTimeout(() => { setPending(false); setN((x) => x + 1); }, 1500); } });
  return (
    <pre className="log">{lines == null ? "loading…" : lines.length ? lines.join("\n") : "(no log lines yet)"}</pre>
  );
}
