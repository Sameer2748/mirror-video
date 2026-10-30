import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Episode, Job, useLive } from "../api";
import { Bar, Chip, ErrorBox, fmtDur, JOB_STATUS } from "../ui";

const ACTIVE = ["running", "waiting", "needs_input"];

export default function Processing({ ep, reload, goMatching }: { ep: Episode; reload: () => void; goMatching: () => void }) {
  const [open, setOpenRaw] = useState<number | null>(null);
  const [picked, setPicked] = useState(false);
  const setOpen = (id: number | null) => { setPicked(true); setOpenRaw(id); };
  const [err, setErr] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(t); }, []);
  // follow the step that is running (or failed) until the editor opens one by hand
  const running = ep.jobs.find((j) => j.status === "running") || ep.jobs.find((j) => j.status === "failed");
  useEffect(() => { if (!picked && running) setOpenRaw(running.id); }, [running?.id, picked]);

  if (!ep.jobs.length) return <div className="empty"><p>This episode has not been processed yet.</p><p className="muted">Check the files on the Files tab, then press Start processing.</p></div>;

  const act = async (f: () => Promise<any>) => { setErr(null); try { await f(); reload(); } catch (e: any) { setErr(e.message); } };
  // after an edit + Render, count the time of the latest pass only (the steps that ran again)
  const again = ep.jobs.filter((j) => (j.attempt || 0) > 0 && j.started);
  const pass = again.length && again.some((j) => j.step === "render") ? again : ep.jobs;
  const started = Math.min(...pass.filter((j) => j.started).map((j) => j.started!));
  const ended = ep.jobs.every((j) => !ACTIVE.includes(j.status));
  const lastEnd = Math.max(...ep.jobs.map((j) => j.finished || 0));
  const doneN = ep.jobs.filter((j) => ["done", "skipped"].includes(j.status)).length;
  const prog = ep.jobs.reduce((a, j) => a + (["done", "skipped"].includes(j.status) ? 1 : j.progress || 0), 0) / ep.jobs.length;
  const needs = ep.jobs.find((j) => j.status === "needs_input");

  return (
    <div>
      <div className="panel">
        <div className="panel-row">
          <div className="grow">
            <b>{doneN} of {ep.jobs.length} steps done</b>
            {isFinite(started) && <span className="muted"> · {pass === ep.jobs ? "time used" : "last render"} {fmtDur((ended && lastEnd ? lastEnd : now) - started)}</span>}
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
            <span className="grow"><b>Ready for review.</b> <span className="muted">Nothing is rendered yet: open the editor, check the clips, then press Render.</span></span>
            <Link className="button primary" to={`/episodes/${ep.id}/editor`}>Open editor →</Link>
          </div>
        )}
      </div>
      <ErrorBox error={err} />
      <div className="steps">
        {ep.jobs.map((j) => (
          <StepRow key={j.id} j={j} now={now} open={open === j.id} toggle={() => setOpen(open === j.id ? null : j.id)}
            cancel={() => act(() => api.post(`/api/jobs/${j.id}/cancel`))} retry={() => act(() => api.post(`/api/jobs/${j.id}/retry`))} />
        ))}
      </div>
    </div>
  );
}

function StepRow({ j, now, open, toggle, cancel, retry }: { j: Job; now: number; open: boolean; toggle: () => void; cancel: () => void; retry: () => void }) {
  const took = j.started ? (j.finished || now) - j.started : null;
  const color = j.status === "failed" ? "red" : j.status === "done" ? "green" : j.status === "needs_input" ? "yellow" : "";
  return (
    <div className={`step ${j.status}`}>
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
  // while the step runs, refresh the tail at most every 1.5 s
  const [pending, setPending] = useState(false);
  useLive((e) => { if (e.job_id === job.id && !pending) { setPending(true); setTimeout(() => { setPending(false); setN((x) => x + 1); }, 1500); } });
  return (
    <pre className="log">{lines == null ? "loading…" : lines.length ? lines.join("\n") : "(no log lines yet)"}</pre>
  );
}
