import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Episode, reveal, Upload, useLive } from "../api";
import { base, Chip, ErrorBox, fmtDur, fmtSize, Picker, uploadEpisodeFile } from "../ui";
import Processing from "./Processing";
import Matching from "./Matching";
import { RenderRow } from "./Renders";

const TABS = [["files", "Files"], ["processing", "Processing"], ["matching", "Check matching"], ["outputs", "Deliverables"]] as const;

function defaultTab(status: string) {
  if (status === "new") return "files";
  if (status === "needs_input") return "matching";
  if (status === "review") return "processing";
  if (status === "done") return "outputs";
  return "processing";
}

export default function EpisodePage() {
  const { eid, tab } = useParams(); const nav = useNavigate();
  const [ep, setEp] = useState<Episode | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(() => api.get<Episode>(`/api/episodes/${eid}`).then(setEp).catch((e) => setErr(e.message)), [eid]);
  useEffect(() => { load(); }, [load]);
  // live: patch job fields straight from the event; reload the whole episode (debounced) when a status changes
  useLive((e) => {
    if (e.episode_id !== Number(eid)) return;
    if (e.kind === "job" && e.job_id) {
      setEp((cur) => cur && { ...cur, jobs: cur.jobs.map((j) => (j.id === e.job_id ? { ...j, ...e.data } : j)) });
      if (!e.data.status) return;
    }
    if (["job", "episode_status", "run_created"].includes(e.kind)) {
      clearTimeout(timer.current); timer.current = window.setTimeout(load, 400);
    }
  });

  if (!ep) return <div className="page"><ErrorBox error={err} /></div>;
  const cur = tab || defaultTab(ep.status);
  const go = (t: string) => nav(`/episodes/${eid}/${t}`);
  const canEdit = ep.jobs.some((j) => j.step === "groups" && j.status === "done");
  const prog = ep.jobs.length ? ep.jobs.reduce((a, j) => a + (["done", "skipped"].includes(j.status) ? 1 : j.progress || 0), 0) / ep.jobs.length : 0;

  return (
    <div className="page">
      <div className="crumbs"><Link to="/">Shows</Link> / <Link to={`/shows/${ep.show_id}`}>{ep.show.code}</Link> / EP {ep.number}</div>
      <div className="page-head">
        <h1>{ep.show.name} <span className="muted" style={{ fontWeight: 500 }}>Episode {ep.number}</span> <Chip status={ep.status} extra={ep.status === "processing" ? `${Math.round(prog * 100)}%` : undefined} /></h1>
      </div>
      {ep.status === "queued" && (
        <div className="callout">
          <div className="grow"><b>In the queue{ep.queue_position && ep.queue_position > 1 ? ` · ${ep.queue_position - 1} episode(s) ahead` : " · next"}.</b>{" "}
            <span className="muted">The machine is working on another episode. This one starts by itself, nothing else to do.</span></div>
          <Link to="/renders" className="button small">See the queue</Link>
        </div>
      )}
      {ep.status === "review" && (
        <div className="callout">
          <div className="grow"><b>Ready for review.</b> <span className="muted">Every clip is on the timeline. Check it, fix what needs fixing, then press Render in the editor.</span></div>
          <Link to={`/episodes/${eid}/editor`} className="button primary">Open editor →</Link>
        </div>
      )}
      <div className="tabs">
        {TABS.map(([k, label]) => (
          <button key={k} className={`tab ${cur === k ? "active" : ""}`} onClick={() => go(k)}>
            {label}{k === "matching" && ep.status === "needs_input" ? <span className="pip" /> : null}
          </button>
        ))}
        {canEdit && <button className="tab editor-tab" onClick={() => nav(`/episodes/${eid}/editor`)}>Editor ↗</button>}
      </div>
      {cur === "files" && (ep.show.profile.uploads
        ? <UploadFiles ep={ep} reload={load} onStarted={() => go("processing")} />
        : <Files ep={ep} reload={load} onStarted={() => go("processing")} />)}
      {cur === "processing" && <Processing ep={ep} reload={load} goMatching={() => go("matching")} />}
      {cur === "matching" && <Matching ep={ep} onImported={() => { load(); go("processing"); }} />}
      {cur === "outputs" && <Outputs ep={ep} />}
    </div>
  );
}

// ---------------- Files ----------------
const KINDS: { kind: string; label: (d: string, s: string) => string; need?: boolean; hint: string }[] = [
  { kind: "src_video", label: (_, s) => `${s} source video`, need: true, hint: "the original episode video" },
  { kind: "dub_audio", label: (d) => `${d} dub audio`, need: true, hint: "the dubbed audio track" },
  { kind: "dub_srt", label: (d) => `${d} SRT`, hint: "optional: made by Whisper from the audio when missing" },
  { kind: "src_srt", label: (_, s) => `${s} SRT`, hint: "optional: made by Whisper from the video when missing" },
  { kind: "dub_script", label: (d) => `${d} script`, hint: "optional: cleans the Whisper text (.docx / .txt)" },
  { kind: "src_script", label: (_, s) => `${s} script`, hint: "optional" },
  { kind: "names_csv", label: () => "Names list", hint: "optional: character / adaptation sheet (.csv)" },
];

function describe(kind: string, info: any) {
  if (!info) return "";
  if (info.error) return info.error;
  if (kind === "src_video" || kind === "dub_audio") {
    const v = info.video ? ` · ${info.video.width}×${info.video.height} · ${info.video.fps} fps` : "";
    return `${fmtDur(info.duration)}${v}`;
  }
  if (kind.endsWith("_srt")) return `${info.cues} cues · ends ${fmtDur(info.last_end_s)}${info.language ? ` · looks ${info.language}` : ""}`;
  if (kind.endsWith("_script")) return `${info.chars} characters${info.language ? ` · looks ${info.language}` : ""}`;
  return "";
}

/** Upload mode (a show whose files are not on this Mac): one row per file, straight into cloud storage. */
function UploadRow({ ep, kind, label, hint, need, have, size, reload }:
  { ep: Episode; kind: string; label: string; hint: string; need?: boolean; have?: string; size?: number | null; reload: () => void }) {
  const [pct, setPct] = useState(-1);
  const [err, setErr] = useState<string | null>(null);
  async function pick(f: File | null) {
    if (!f) return;
    setErr(null); setPct(0);
    try { await uploadEpisodeFile(ep.id, kind, f, setPct); reload(); }
    catch (e: any) { setErr(e.message); } finally { setPct(-1); }
  }
  async function clear() {
    try { await api.del(`/api/episodes/${ep.id}/files/${kind}`); reload(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <tr>
      <td><b>{label}</b>{need && <span className="req"> *</span>}<div className="hint">{hint}</div></td>
      <td>
        {have ? <span title={have}>{base(have)} <span className="muted">{fmtSize(size)}</span></span>
          : <span className="muted">{need ? "not uploaded yet" : "none (fine)"}</span>}
        {pct >= 0 && <><div className="bar" style={{ marginTop: 6 }}><div className="bar-fill" style={{ width: `${Math.round(pct * 100)}%` }} /></div>
          <div className="muted small-text">{Math.round(pct * 100)}% uploaded</div></>}
        {err && <div className="error small-text">{err}</div>}
      </td>
      <td className="small-text muted">{have ? <span className="good">✓ in cloud storage</span> : ""}</td>
      <td className="right">
        <label className="button small">{have ? "Replace…" : "Upload…"}
          <input type="file" style={{ display: "none" }} disabled={pct >= 0} onChange={(e) => pick(e.target.files?.[0] || null)} />
        </label>
        {have && <button className="small ghost" disabled={pct >= 0} onClick={clear}>Remove</button>}
      </td>
    </tr>
  );
}

function UploadFiles({ ep, reload, onStarted }: { ep: Episode; reload: () => void; onStarted: () => void }) {
  const [busy, setBusy] = useState(""); const [err, setErr] = useState<string | null>(null);
  const [d, s] = (ep.show.profile.labels || "DUB,SRC").split(",");
  const have: Record<string, { path: string; size: number | null }> = {};
  ep.assets.filter((a) => a.role === "input").forEach((a) => (have[a.kind] = { path: a.path, size: a.size ?? null }));
  const missing = KINDS.filter((k) => k.need && !have[k.kind]);
  async function start() {
    setBusy("start"); setErr(null);
    try { await api.post(`/api/episodes/${ep.id}/process`); reload(); onStarted(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  }
  return (
    <div>
      <div className="panel">
        <div className="muted small-text">The files you upload go to cloud storage (R2) under
          <code> shows/{ep.show.code}/ep{ep.number}/input/</code>. When you press Start processing the episode joins the
          queue; a worker brings the files down, runs every step, puts the results back and frees the machine.</div>
      </div>
      <table className="table files">
        <thead><tr><th style={{ width: 200 }}>File</th><th>Uploaded</th><th style={{ width: 160 }}></th><th style={{ width: 180 }}></th></tr></thead>
        <tbody>
          {KINDS.map((k) => (
            <UploadRow key={k.kind} ep={ep} kind={k.kind} label={k.label(d, s)} hint={k.hint} need={k.need}
              have={have[k.kind]?.path} size={have[k.kind]?.size} reload={reload} />
          ))}
        </tbody>
      </table>
      <ErrorBox error={err} />
      <div className="actions">
        <button className="primary" disabled={!!busy || missing.length > 0} onClick={start}>
          {busy === "start" ? "Starting…" : ep.run ? "Process again" : "Start processing"}
        </button>
        {missing.length > 0 && <span className="muted">Still needed: {missing.map((k) => k.label(d, s)).join(", ")}</span>}
      </div>
    </div>
  );
}

function Files({ ep, reload, onStarted }: { ep: Episode; reload: () => void; onStarted: () => void }) {
  const [cands, setCands] = useState<Record<string, string[]>>(ep.candidates || {});
  const [checks, setChecks] = useState<Record<string, { info: any; problems: string[] }> | null>(null);
  const [picking, setPicking] = useState<string | null>(null);
  const [editOut, setEditOut] = useState(false); const [out, setOut] = useState(ep.output_dir);
  const [busy, setBusy] = useState(""); const [err, setErr] = useState<string | null>(null);
  const [d, s] = (ep.show.profile.labels || "DUB,SRC").split(",");
  const locked = ep.jobs.some((j) => ["waiting", "running", "needs_input"].includes(j.status));
  const have: Record<string, string> = {};
  ep.assets.filter((a) => a.role === "input").forEach((a) => (have[a.kind] = a.path));

  useEffect(() => { api.get<Record<string, string[]>>(`/api/episodes/${ep.id}/scan`).then(setCands).catch(() => {}); }, [ep.id]);

  async function setAsset(kind: string, path: string | null) {
    setErr(null); setChecks(null);
    try { await api.put(`/api/episodes/${ep.id}/assets`, { [kind]: path }); reload(); } catch (e: any) { setErr(e.message); }
  }
  async function check() {
    setBusy("check"); setErr(null);
    try { setChecks(await api.post(`/api/episodes/${ep.id}/check`)); } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  }
  async function saveOut() {
    setErr(null);
    try { await api.patch(`/api/episodes/${ep.id}`, { output_dir: out }); setEditOut(false); reload(); } catch (e: any) { setErr(e.message); }
  }
  async function start() {
    if (ep.output_existing.length && !confirm(`The output folder already holds ${ep.output_existing.length} files. Files with the same names will be replaced. Continue?`)) return;
    setBusy("start"); setErr(null);
    try { await api.post(`/api/episodes/${ep.id}/process`); reload(); onStarted(); } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  }

  const problems = checks ? Object.values(checks).flatMap((c) => c.problems) : [];
  const missingNeeded = KINDS.filter((k) => k.need && !have[k.kind]);
  const durs = checks ? [checks.src_video?.info?.duration, checks.dub_audio?.info?.duration] : [];

  return (
    <div>
      <div className="panel">
        <div className="panel-row">
          <div><b>Output folder</b>
            {!editOut && <div className="small-text"><code>{ep.output_dir}</code> <button className="small ghost" disabled={locked} onClick={() => setEditOut(true)}>Change</button></div>}
            {editOut && <div className="row"><input value={out} onChange={(e) => setOut(e.target.value)} style={{ minWidth: 480 }} /><button className="small primary" onClick={saveOut}>Save</button><button className="small ghost" onClick={() => setEditOut(false)}>Cancel</button></div>}
          </div>
        </div>
        {ep.output_existing.length > 0 && (
          <div className="warn">This folder already holds {ep.output_existing.length} files ({ep.output_existing.slice(0, 4).join(", ")}{ep.output_existing.length > 4 ? ", …" : ""}).
            Processing writes the episode's videos and sheets here and replaces files with the same names. To keep them, choose a new folder (for example <code>{ep.output_dir}_studio</code>).</div>
        )}
        <div className="muted small-text">Work files go to <code>{ep.work_dir}</code>. Input files are only read, never changed.</div>
      </div>

      <table className="table files">
        <thead><tr><th style={{ width: 180 }}>File</th><th>Chosen</th><th style={{ width: 260 }}>Check</th><th style={{ width: 150 }}></th></tr></thead>
        <tbody>
          {KINDS.map((k) => {
            const p = have[k.kind]; const options = cands[k.kind] || [];
            const c = checks?.[k.kind];
            return (
              <tr key={k.kind}>
                <td><b>{k.label(d, s)}</b>{k.need && <span className="req"> *</span>}<div className="hint">{k.hint}</div></td>
                <td>
                  {options.length > 1 || (options.length === 1 && options[0] !== p) ? (
                    <select value={p || ""} disabled={locked} onChange={(e) => setAsset(k.kind, e.target.value || null)}>
                      <option value="">— none —</option>
                      {p && !options.includes(p) && <option value={p}>{base(p)}</option>}
                      {options.map((o) => <option key={o} value={o}>{o.replace(ep.show.folder + "/", "")}</option>)}
                    </select>
                  ) : p ? <span title={p}>{p.replace(ep.show.folder + "/", "")}</span> : <span className="muted">{k.need ? "not found — choose one" : "none (fine)"}</span>}
                </td>
                <td className="small-text">
                  {c && c.problems.length > 0 && <span className="bad">{c.problems.join("; ")}</span>}
                  {c && !c.problems.length && <span className="good">✓ {describe(k.kind, c.info)}</span>}
                </td>
                <td className="right">
                  <button className="small" disabled={locked} onClick={() => setPicking(k.kind)}>Browse…</button>
                  {p && !k.need && <button className="small ghost" disabled={locked} onClick={() => setAsset(k.kind, null)}>Clear</button>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {checks && durs[0] && durs[1] && Math.abs(durs[0] - durs[1]) > 60 && (
        <div className="warn">The video ({fmtDur(durs[0])}) and the dub audio ({fmtDur(durs[1])}) differ by more than a minute — check they are the same episode.</div>
      )}
      {checks && !problems.length && <div className="ok-box">All files checked: nothing wrong found.</div>}
      <ErrorBox error={err} />
      <div className="actions">
        <button onClick={check} disabled={!!busy}>{busy === "check" ? "Checking…" : "Check files"}</button>
        <button className="primary" disabled={!!busy || missingNeeded.length > 0 || locked || problems.length > 0} onClick={start}>
          {busy === "start" ? "Starting…" : ep.run ? "Process again" : "Start processing"}
        </button>
        {missingNeeded.length > 0 && <span className="muted">Needed: {missingNeeded.map((k) => k.label(d, s)).join(", ")}</span>}
        {locked && <span className="muted">Processing — files are locked.</span>}
      </div>
      {picking && <Picker mode="file" start={have[picking] ? have[picking].split("/").slice(0, -1).join("/") : ep.show.folder}
        onClose={() => setPicking(null)} onPick={(p) => { const k = picking; setPicking(null); setAsset(k, p); }} />}
    </div>
  );
}

// ---------------- Deliverables ----------------
type Deliv = {
  files: { kind: string; label: string; name: string; path: string; exists: boolean; size: number | null; mtime: number | null }[];
  output_dir: string; render: RenderRow | null; rendered_version: number | null; latest_version: number | null;
  unrendered: boolean; final_check_image: boolean; qc_open: number;
};

function Outputs({ ep }: { ep: Episode }) {
  const [d, setD] = useState<Deliv | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<Deliv>(`/api/episodes/${ep.id}/deliverables`).then(setD).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [ep.id, ep.status]); // eslint-disable-line
  const open = (p: string) => reveal(p).catch((e) => setErr(e.message));
  if (!d) return <ErrorBox error={err} />;
  const r = d.render; const check = ep.jobs.find((j) => j.step === "check");
  const passed = r?.check_result?.passed ?? check?.check_passed;
  const main = d.files.filter((f) => ["clean", "dub_subs", "en_subs", "en_srt", "mapping"].includes(f.kind));
  const extra = d.files.filter((f) => !main.includes(f));
  const busy = r && (r.status === "running" || r.status === "waiting");
  return (
    <div>
      {busy && <div className="panel"><b>Rendering v{r!.version}</b> <span className="muted">· {r!.step || "waiting for a free slot"}</span>
        <div className="bar" style={{ marginTop: 8 }}><div className="bar-fill" style={{ width: `${Math.round((r!.progress || 0) * 100)}%` }} /></div></div>}
      {!busy && passed === true && <div className="ok-box">✓ FINAL CHECK PASSED{d.rendered_version ? ` · rendered from timeline version ${d.rendered_version}` : ""}{r?.finished ? ` · ${new Date(r.finished * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}` : ""}</div>}
      {!busy && passed === false && <div className="error">The final check did not pass:<pre className="log">{(r?.check_result?.lines || check?.check_lines || []).join("\n")}</pre></div>}
      {d.unrendered && <div className="warn">The editor has changes that are not in these files yet (timeline version {d.latest_version}{d.rendered_version ? `, rendered ${d.rendered_version}` : ""}). <Link to={`/episodes/${ep.id}/editor`}>Open the editor</Link> and press Render.</div>}
      {d.qc_open > 0 && <div className="warn">{d.qc_open} QC note{d.qc_open > 1 ? "s are" : " is"} still open in the editor.</div>}
      <div className="panel-row">
        <code>{d.output_dir}</code>
        <button className="small" onClick={() => open(d.output_dir)}>Open in Finder</button>
        <a className="button small ghost" href={`/api/episodes/${ep.id}/export/premiere`} title="The current timeline as Final Cut Pro 7 XML: in Premiere, File → Import">Export for Premiere</a>
      </div>
      <ErrorBox error={err} />
      <table className="table">
        <tbody>
          {main.map((f) => (
            <tr key={f.kind} className={f.exists ? "" : "muted"}>
              <td style={{ width: 30 }}>{f.exists ? (f.name.endsWith(".mp4") ? "🎬" : "📄") : "·"}</td>
              <td><b>{f.label}</b><div className="small-text muted">{f.name}</div></td>
              <td className="muted">{f.exists ? fmtSize(f.size) : "not made yet"}</td>
              <td className="muted small-text">{f.mtime ? new Date(f.mtime * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}</td>
              <td className="right">{f.exists && <button className="small ghost" onClick={() => open(f.path)}>Show in Finder</button>}</td>
            </tr>
          ))}
          {extra.map((f) => (
            <tr key={f.kind}>
              <td>📎</td><td>{f.label}<div className="small-text muted">{f.name}</div></td><td className="muted">{fmtSize(f.size)}</td>
              <td className="muted small-text">{f.mtime ? new Date(f.mtime * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}</td>
              <td className="right"><button className="small ghost" onClick={() => open(f.path)}>Show in Finder</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <Cloud ep={ep} />
      {d.final_check_image && <div className="panel">
        <div className="small-text muted" style={{ marginBottom: 8 }}>The final check: the same moment in every video (clean = no subtitles; the others show theirs).</div>
        <img src={`/api/episodes/${ep.id}/final-check.jpg?v=${r?.finished || 0}`} alt="final check" style={{ maxWidth: "100%", borderRadius: 10 }} />
      </div>}
    </div>
  );
}

// ---------------- the same files in the cloud (R2) ----------------
function Cloud({ ep }: { ep: Episode }) {
  const [c, setC] = useState<{ configured: boolean; auto: boolean; prefix: string | null; uploads: Upload[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const load = () => api.get<any>(`/api/episodes/${ep.id}/uploads`).then(setC).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [ep.id]);   // eslint-disable-line
  useLive((e) => { if (e.kind === "upload" && e.episode_id === ep.id) load(); });

  async function send() {
    setBusy("send"); setErr(null);
    try { await api.post(`/api/episodes/${ep.id}/uploads`); load(); } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  }
  async function link(u: Upload) {
    try { const r = await api.get<{ url: string }>(`/api/uploads/${u.id}/link`); window.open(r.url, "_blank"); }
    catch (e: any) { setErr(e.message); }
  }
  if (!c || !c.configured) return null;
  const running = c.uploads.find((u) => u.status === "sending");
  return (
    <div className="panel">
      <div className="panel-row">
        <b>In the cloud</b>
        <span className="muted small-text">R2 · <code>{c.prefix}</code>{c.auto ? " · sent up by itself when the episode is done" : ""}</span>
        <span className="grow" />
        <button className="small" disabled={!!busy} onClick={send}>{busy === "send" ? "Queueing…" : "Send the finished files up"}</button>
      </div>
      <ErrorBox error={err} />
      {running && <div className="bar" style={{ marginBottom: 8 }}><div className="bar-fill" style={{ width: `${Math.round(((running.sent || 0) / Math.max(running.size || 1, 1)) * 100)}%` }} /></div>}
      {!c.uploads.length && <div className="muted small-text">Nothing up there yet.</div>}
      {c.uploads.length > 0 && <table className="table"><tbody>
        {c.uploads.map((u) => (
          <tr key={u.id}>
            <td style={{ width: 24 }}>{u.status === "done" ? <span className="good">✓</span> : u.status === "failed" ? <span className="bad">✗</span> : "⟳"}</td>
            <td>{u.name}<div className="small-text muted">{u.status === "sending" ? `${fmtSize(u.sent)} of ${fmtSize(u.size)}` : u.status === "failed" ? u.error : u.key}</div></td>
            <td className="muted">{fmtSize(u.size)}</td>
            <td className="right">
              {u.status === "done" && <button className="small ghost" onClick={() => link(u)}>Download link</button>}
              {u.status === "failed" && <button className="small ghost" onClick={() => api.post(`/api/uploads/${u.id}/retry`).then(load).catch((e) => setErr(e.message))}>Try again</button>}
            </td>
          </tr>
        ))}
      </tbody></table>}
    </div>
  );
}
