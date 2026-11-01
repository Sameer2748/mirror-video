// The render queue: every Render pressed in the editor, newest first, with its live progress (render → check).
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, QueueItem, QueueView, useLive } from "../api";
import { ErrorBox, fmtDur, fmtWhen } from "../ui";

export type RenderRow = {
  id: number; episode_id: number; version: number; status: string; created: number; finished?: number | null; started?: number | null;
  progress?: number; step?: string | null; eta?: number | null; number: number; code: string; show_id: number;
  check_result?: { passed?: boolean; note?: string; lines?: string[]; seconds?: Record<string, number> } | null;
};
const STATUS: Record<string, [string, string]> = {
  waiting: ["Waiting", "chip-grey"], running: ["Rendering", "chip-blue"], done: ["Done", "chip-green"],
  failed: ["Failed", "chip-red"], cancelled: ["Cancelled", "chip-grey"], replaced: ["Replaced by a newer render", "chip-grey"],
};

export function RenderStatus({ r }: { r: RenderRow }) {
  const [label, cls] = STATUS[r.status] || [r.status, "chip-grey"];
  const passed = r.check_result?.passed;
  return <span className={`chip ${r.status === "done" && passed === false ? "chip-red" : cls}`}>{r.status === "done" ? (passed ? "Done · check passed" : "Done · check FAILED") : label}</span>;
}

/** The line of episodes: what the machine is working on now, and who is next (docs/09 §4). */
function Queue() {
  const [q, setQ] = useState<QueueView | null>(null);
  const load = () => api.get<QueueView>("/api/queue").then(setQ).catch(() => {});
  useEffect(() => { load(); }, []);
  const [tick, setTick] = useState(0);
  useLive((e) => { if (e.kind === "job" || e.kind === "episode_status" || e.kind === "run_created") setTick((t) => t + 1); });
  useEffect(() => { if (!tick) return; const t = setTimeout(load, 1000); return () => clearTimeout(t); }, [tick]);
  if (!q || (!q.working.length && !q.waiting.length)) return null;
  const row = (e: QueueItem, place: string) => (
    <tr key={e.id}>
      <td style={{ width: 90 }}><span className={`chip ${e.working ? "chip-blue" : "chip-grey"}`}>{place}</span></td>
      <td><b>{e.code} · EP {e.number}</b></td>
      <td style={{ width: 300 }}>
        {e.working ? <>
          <div className="bar"><div className="bar-fill" style={{ width: `${Math.round(e.progress * 100)}%` }} /></div>
          <div className="muted small-text">{e.step_label || e.step || "starting"} · step {e.steps_done + 1} of {e.steps}</div>
        </> : <span className="muted small-text">waiting for the machine</span>}
      </td>
      <td className="right"><Link className="button small ghost" to={`/episodes/${e.id}`}>Open</Link></td>
    </tr>
  );
  return (
    <>
      <h3>Processing queue</h3>
      <div className="muted small-text" style={{ marginBottom: 8 }}>
        {q.max_active === 1 ? "One episode at a time" : `${q.max_active} episodes at a time`}; the next one starts by itself.
      </div>
      <table className="table"><tbody>
        {q.working.map((e) => row(e, "working"))}
        {q.waiting.map((e) => row(e, `#${e.position}`))}
      </tbody></table>
    </>
  );
}

export default function Renders() {
  const [rows, setRows] = useState<RenderRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<RenderRow[]>("/api/renders").then(setRows).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const [tick, setTick] = useState(0);
  useLive((e) => { if (e.kind === "job" || e.kind === "episode_status") setTick((t) => t + 1); });
  useEffect(() => { if (!tick) return; const t = setTimeout(load, 1000); return () => clearTimeout(t); }, [tick]);
  const active = (rows || []).filter((r) => r.status === "running" || r.status === "waiting");
  return (
    <div className="page">
      <div className="page-head"><div><h1>Render queue</h1>
        <div className="muted small-text">Every Render pressed in the editor. Renders run in the background, two heavy steps at a time; you can keep editing another episode.</div></div></div>
      <ErrorBox error={err} />
      <Queue />
      {rows && !rows.length && <div className="empty"><p>Nothing rendered yet.</p><p className="muted">Open an episode in the editor and press Render.</p></div>}
      {active.length > 0 && <h3>Now ({active.length})</h3>}
      {rows && !!rows.length && (
        <table className="table">
          <thead><tr><th>Episode</th><th>Version</th><th>Status</th><th style={{ width: 260 }}>Progress</th><th>Started</th><th>Took</th><th></th></tr></thead>
          <tbody>
            {rows.map((r) => {
              const took = r.finished && (r.started || r.created) ? r.finished - (r.started || r.created) : null;
              return (
                <tr key={r.id}>
                  <td><b>{r.code} · EP {r.number}</b>{r.check_result?.note && <div className="muted small-text">{r.check_result.note}</div>}</td>
                  <td>v{r.version}</td>
                  <td><RenderStatus r={r} /></td>
                  <td>{(r.status === "running" || r.status === "waiting") ? <>
                    <div className="bar"><div className="bar-fill" style={{ width: `${Math.round((r.progress || 0) * 100)}%` }} /></div>
                    <div className="muted small-text">{r.step || "waiting for a free slot"}{r.eta ? ` · about ${fmtDur(r.eta)} left on this step` : ""}</div>
                  </> : r.check_result?.seconds ? <span className="muted small-text">clips {fmtDur(r.check_result.seconds.render)}{r.check_result.seconds.subs_dub ? ` · subtitles ${fmtDur((r.check_result.seconds.subs_dub || 0) + (r.check_result.seconds.subs_en || 0))}` : ""}</span> : null}</td>
                  <td className="muted">{fmtWhen(r.started || r.created)}</td>
                  <td className="muted">{took ? fmtDur(took) : ""}</td>
                  <td className="right">
                    <Link className="button small" to={`/episodes/${r.episode_id}/outputs`}>Deliverables</Link>{" "}
                    <Link className="button small ghost" to={`/episodes/${r.episode_id}/editor`}>Editor</Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
