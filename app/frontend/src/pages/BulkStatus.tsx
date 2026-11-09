import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Batch, BatchEpisode, Episode, useLive } from "../api";
import { Bar, Chip, ErrorBox, uploadEpisodeFile } from "../ui";
import Processing from "./Processing";
import { BulkFileMaps, keepRange, takeBulkFiles } from "./bulkFiles";

const BATCH_STATUS: Record<string, [string, string]> = {
  new: ["Waiting for files", "grey"],
  uploading: ["Uploading", "blue"],
  queued: ["Queued", "blue"],
  processing: ["Processing", "blue"],
  needs_input: ["Check matching", "yellow"],
  review: ["Ready for review", "violet"],
  done: ["Done", "green"],
  failed: ["Failed", "red"],
  cancelled: ["Cancelled", "grey"],
};

type Up = { number: number; kind: string; pct: number } | null;

export default function BulkStatus() {
  const { sid, bid } = useParams(); const nav = useNavigate();
  const [batch, setBatch] = useState<Batch | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [full, setFull] = useState<Episode | null>(null);
  const [up, setUp] = useState<Up>(null);
  const [upErr, setUpErr] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [renderNote, setRenderNote] = useState<string | null>(null);
  const started = useRef(false);

  const load = useCallback(() => {
    api.get<Batch>(`/api/batches/${bid}`).then(setBatch).catch((e) => setErr(e.message));
  }, [bid]);
  useEffect(load, [load]);

  const [tick, setTick] = useState(0);
  const lastLoad = useRef(0);
  useLive((e) => {
    if (!batch) return;
    if (!batch.episodes.some((x) => x.id === e.episode_id)) return;
    if (e.kind === "job" || e.kind === "episode_status" || e.kind === "run_created") setTick((t) => t + 1);
  });
  useEffect(() => {
    if (!tick) return;
    const wait = Math.max(0, 2000 - (Date.now() - lastLoad.current));
    const t = window.setTimeout(() => { lastLoad.current = Date.now(); load(); }, wait);
    return () => window.clearTimeout(t);
  }, [tick, load]);

  useEffect(() => {
    if (!open) { setFull(null); return; }
    api.get<Episode>(`/api/episodes/${open}`).then(setFull).catch((e) => setErr(e.message));
  }, [open, tick]);

  useEffect(() => {
    if (!batch || started.current) return;
    const files = takeBulkFiles(batch.id);
    if (!files) return;
    started.current = true;
    uploadThenQueue(batch, files, setUp, setUpErr, load);
  }, [batch, load]);

  async function act(fn: () => Promise<any>, label: string) {
    setBusy(label); setErr(null);
    try { await fn(); load(); } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  }

  if (!batch) return <div className="page"><ErrorBox error={err} /></div>;
  const show = batch.show || { code: "", name: "", id: batch.show_id, folder: "", preset: null, profile: {} };
  const eps = batch.episodes;
  const ready = eps.filter((e) => e.ready && ["new", "cancelled", "failed"].includes(e.status)).length;
  const working = eps.filter((e) => e.status === "processing" || e.status === "queued").length;
  const inReview = eps.filter((e) => e.status === "review").length;
  const uploading = !!up;

  function summarizeRender(results: { number: number; ok: boolean; error?: string }[]) {
    const ok = results.filter((r) => r.ok);
    const bad = results.filter((r) => !r.ok);
    if (!results.length) return "Nothing in review to render — processing episodes were left alone.";
    const bits: string[] = [];
    if (ok.length) bits.push(`Rendering EP ${ok.map((r) => r.number).join(", ")}`);
    if (bad.length) bits.push(`Skipped EP ${bad.map((r) => `${r.number} (${r.error})`).join("; EP ")}`);
    return bits.join(". ") + ".";
  }

  return (
    <div className="page">
      <div className="crumbs">
        <Link to="/">Shows</Link> / <Link to={`/shows/${sid}`}>{show.code}</Link> / Bulk EP {batch.start_num}–{batch.end_num}
      </div>
      <div className="page-head">
        <div>
          <h1>Bulk generation <span className="muted" style={{ fontWeight: 500 }}>EP {batch.start_num}–{batch.end_num}</span>
            <Chip status={uploading ? "uploading" : batch.status} map={BATCH_STATUS} /></h1>
          <div className="muted small-text">{eps.length} episodes · {(show && show.name) || ""}
            {working ? ` · ${working} in the queue` : ""}</div>
        </div>
        <div className="row">
          {!uploading && ready > 0 && (
            <button className="primary" disabled={!!busy} onClick={() => act(() => api.post(`/api/batches/${bid}/process`), "start")}>
              {busy === "start" ? "Queuing…" : `Start ${ready} ready episode${ready === 1 ? "" : "s"}`}
            </button>
          )}
          {!uploading && inReview > 0 && (
            <button className="primary" disabled={!!busy} onClick={() => {
              if (!confirm(`Render ${inReview} episode${inReview === 1 ? "" : "s"} waiting in review?\n\nEpisodes still processing are left alone. Only a black gap is skipped. Flags stay on the timeline so an editor can fix them later and render again.`)) return;
              setRenderNote(null);
              act(async () => {
                const r = await api.post<Batch>(`/api/batches/${bid}/render`);
                setRenderNote(summarizeRender(r.results || []));
              }, "render");
            }}>
              {busy === "render" ? "Starting…" : `Render ${inReview} in review`}
            </button>
          )}
          {working > 0 && (
            <button className="danger" disabled={!!busy}
              onClick={() => confirm("Stop every episode in this bulk run?") && act(() => api.post(`/api/batches/${bid}/cancel`), "cancel")}>
              Cancel all
            </button>
          )}
          <Link to={`/shows/${sid}/new-episode`} className="button ghost">+ Single episode</Link>
        </div>
      </div>

      {uploading && (
        <div className="callout">
          <div className="grow">
            <b>Uploading EP {up!.number} · {kindLabel(up!.kind)}</b>
            <span className="muted"> Files go to cloud storage, then each episode joins the worker queue.</span>
            <Bar value={up!.pct} />
          </div>
          <span className="muted">{Math.round(up!.pct * 100)}%</span>
        </div>
      )}
      {upErr && <div className="error">{upErr}</div>}
      {renderNote && <div className="ok-box">{renderNote}</div>}
      <ErrorBox error={err} />

      <table className="table">
        <thead><tr><th>Episode</th><th>Status</th><th>Step</th><th>Progress</th><th>Files</th><th></th></tr></thead>
        <tbody>
          {eps.map((e) => {
            const expanded = open === e.id;
            return (
              <EpisodeBlock key={e.id} e={e} expanded={expanded} uploading={uploading && up?.number === e.number}
                onToggle={() => setOpen(expanded ? null : e.id)}
                onOpen={() => nav(`/episodes/${e.id}`)}
                onRender={() => {
                  if (!confirm(`Render EP ${e.number} from its current timeline (no editor)?`)) return;
                  act(() => api.post(`/api/episodes/${e.id}/timeline/render`), "render");
                }}
                rendering={busy === "render"}
                full={expanded ? full : null}
                reload={() => { load(); if (e.id === open) api.get<Episode>(`/api/episodes/${e.id}`).then(setFull); }}
                goMatching={() => nav(`/episodes/${e.id}/matching`)} />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function EpisodeBlock({ e, expanded, uploading, onToggle, onOpen, onRender, rendering, full, reload, goMatching }:
  { e: BatchEpisode; expanded: boolean; uploading: boolean; onToggle: () => void; onOpen: () => void;
    onRender: () => void; rendering: boolean;
    full: Episode | null; reload: () => void; goMatching: () => void }) {
  const files = (e.have || []).length
    ? `${e.have!.includes("src_video") ? "video" : ""}${e.have!.includes("dub_audio") ? " · audio" : ""}${e.have!.includes("dub_script") ? " · script" : ""}`.replace(/^ · /, "")
    : "waiting for files";
  const step = uploading ? "uploading…" : e.status === "queued"
    ? `in the queue${e.queue_position && e.queue_position > 1 ? ` · ${e.queue_position - 1} ahead` : ""}`
    : (e.step_label || e.step || (e.ready ? "" : "needs files"));
  return (
    <>
      <tr className="clickable" onClick={onToggle}>
        <td><b>EP {e.number}</b></td>
        <td><Chip status={e.status} extra={e.status === "processing" && e.progress != null ? `${Math.round(e.progress * 100)}%` : undefined} /></td>
        <td className="muted small-text">{step}{e.steps ? ` · ${e.steps_done || 0}/${e.steps}` : ""}</td>
        <td style={{ width: 160 }}>{e.progress != null && <div className="bar"><div className="bar-fill" style={{ width: `${Math.round(e.progress * 100)}%` }} /></div>}</td>
        <td className="muted small-text">{e.ready ? <span className="good">{files}</span> : files}</td>
        <td className="right" onClick={(ev) => ev.stopPropagation()}>
          {e.status === "review" && (
            <button className="small primary" disabled={rendering} onClick={onRender}>Render</button>
          )}
          <button className="small ghost" onClick={onToggle}>{expanded ? "Hide steps" : "Steps"}</button>
          <button className="small" onClick={onOpen}>Open</button>
        </td>
      </tr>
      {expanded && (
        <tr className="bulk-steps">
          <td colSpan={6}>
            {full
              ? <Processing ep={full} reload={reload} goMatching={goMatching} />
              : <div className="muted pad">Loading steps…</div>}
          </td>
        </tr>
      )}
    </>
  );
}

function kindLabel(kind: string) {
  return ({ src_video: "source video", dub_audio: "dub audio", dub_script: "dub script" } as Record<string, string>)[kind] || kind;
}

async function uploadThenQueue(batch: Batch, files: BulkFileMaps,
  setUp: (u: Up) => void, setUpErr: (s: string | null) => void, load: () => void) {
  try {
    const nums = batch.episodes.map((e) => e.number);
    const only = { video: keepRange(files.video, nums), audio: keepRange(files.audio, nums),
      script: keepRange(files.script, nums) };
    for (const e of batch.episodes) {
      const jobs: { kind: string; file: File }[] = [];
      const v = only.video.get(e.number); if (v) jobs.push({ kind: "src_video", file: v });
      const a = only.audio.get(e.number); if (a) jobs.push({ kind: "dub_audio", file: a });
      const s = only.script.get(e.number); if (s) jobs.push({ kind: "dub_script", file: s });
      for (const j of jobs) {
        setUp({ number: e.number, kind: j.kind, pct: 0 });
        await uploadEpisodeFile(e.id, j.kind, j.file, (pct) => setUp({ number: e.number, kind: j.kind, pct }));
      }
    }
    setUp(null);
    await api.post(`/api/batches/${batch.id}/process`);
    load();
  } catch (err: any) {
    setUp(null);
    setUpErr(err.message || "upload failed");
  }
}
