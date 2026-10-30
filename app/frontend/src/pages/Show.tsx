import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Episode, Show, useLive } from "../api";
import { Chip, ErrorBox, fmtWhen, Picker } from "../ui";

export default function ShowPage() {
  const { sid } = useParams(); const nav = useNavigate();
  const [show, setShow] = useState<Show | null>(null);
  const [eps, setEps] = useState<Episode[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<Show>(`/api/shows/${sid}`).then(setShow).catch((e) => setErr(e.message));
    api.get<Episode[]>(`/api/shows/${sid}/episodes`).then(setEps).catch((e) => setErr(e.message));
  }, [sid]);
  useEffect(load, [load]);
  // progress changes arrive as events; reload the table at most every 2 s
  const [tick, setTick] = useState(0);
  useLive((e) => { if (e.kind === "job" || e.kind === "episode_status" || e.kind === "run_created") setTick((t) => t + 1); });
  useEffect(() => { if (!tick) return; const t = setTimeout(load, 2000); return () => clearTimeout(t); }, [tick, load]);

  async function remove() {
    if (!show || !confirm(`Remove the show ${show.code} from Dub Studio? Files on disk are not touched.`)) return;
    await api.del(`/api/shows/${sid}`); nav("/");
  }

  if (!show) return <div className="page"><ErrorBox error={err} /></div>;
  const p = show.profile;
  return (
    <div className="page">
      <div className="crumbs"><Link to="/">Shows</Link> / {show.code}</div>
      <div className="page-head">
        <div>
          <h1>{show.name}{show.name !== show.code && <span className="card-code" style={{ margin: 0 }}>{show.code}</span>}</h1>
          <div className="muted small-text"><code>{show.folder}</code></div>
          <div className="muted small-text">
            {p.dub_lang} dub vs {p.src_lang} source · Whisper SRTs + script · whole sentences · lip scan · groups 7 · max drift 0.5 s
            {p.transitions ? " · automatic transitions" : " · manual transitions"} · {p.sub_style} subs (7 words / 4 s) · ending stops before a fade
            {p.episode_offset ? ` · source episode = dub + ${p.episode_offset}` : ""}
          </div>
        </div>
        <div className="row">
          <Link to={`/shows/${sid}/new-episode`} className="button primary">+ New episode</Link>
        </div>
      </div>
      <ErrorBox error={err} />
      {eps && !eps.length && <div className="empty"><p>No episodes yet.</p><Link to={`/shows/${sid}/new-episode`} className="button primary">Add the first episode</Link></div>}
      {eps && !!eps.length && (
        <table className="table">
          <thead><tr><th>Episode</th><th>Status</th><th>Progress</th><th>Last change</th><th>Output folder</th><th></th></tr></thead>
          <tbody>
            {eps.map((e) => (
              <tr key={e.id} className="clickable" onClick={() => nav(`/episodes/${e.id}`)}>
                <td><b>EP {e.number}</b></td>
                <td><Chip status={e.status} extra={e.status === "processing" && e.progress != null ? `${Math.round(e.progress * 100)}%` : undefined} /></td>
                <td style={{ width: 160 }}>{e.progress != null && <div className="bar"><div className="bar-fill" style={{ width: `${Math.round(e.progress * 100)}%` }} /></div>}</td>
                <td className="muted">{fmtWhen(e.updated)}</td>
                <td className="muted small-text">{e.output_dir.replace(show.folder + "/", "")}</td>
                <td className="right" onClick={(ev) => ev.stopPropagation()}>
                  {["review", "done"].includes(e.status) && <Link to={`/episodes/${e.id}/editor`} className={`button small ${e.status === "review" ? "primary" : ""}`}>Open editor</Link>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <TransitionMode show={show} onSaved={setShow} />
      <NamesSheet show={show} />
      <FlagLimits show={show} onSaved={setShow} />
      <div className="footer-actions"><button className="ghost danger" onClick={remove}>Remove show</button></div>
    </div>
  );
}

// The limits behind the editor's yellow and red clips, per show (design: "Review flags").
const LIMITS: { key: string; label: string; hint: string; unit?: string }[] = [
  { key: "slow", label: "Slow motion: yellow below ×", hint: "default 0.67" },
  { key: "slow_red", label: "Slow motion: red below ×", hint: "default 0.5" },
  { key: "fast", label: "Fast: yellow above ×", hint: "default 1.5" },
  { key: "fast_red", label: "Fast: red above ×", hint: "default 2" },
  { key: "short_ms", label: "Flash: yellow under (ms)", hint: "default 500" },
  { key: "drift_ms", label: "Drift: yellow over (ms)", hint: "default 500 (shown from 1 s)" },
  { key: "blur", label: "Soft picture: yellow under (share of usual)", hint: "default 0.25" },
];

function FlagLimits({ show, onSaved }: { show: Show; onSaved: (s: Show) => void }) {
  const cur = (show.profile.flags || {}) as Record<string, number>;
  const [v, setV] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ text: string; err?: boolean } | null>(null);
  useEffect(() => { setV(Object.fromEntries(LIMITS.map((l) => [l.key, cur[l.key] != null ? String(cur[l.key]) : ""]))); }, [show.id]); // eslint-disable-line
  const changed = LIMITS.some((l) => v[l.key] !== (cur[l.key] != null ? String(cur[l.key]) : ""));
  async function save() {
    try {
      const flags = Object.fromEntries(LIMITS.filter((l) => v[l.key] !== "").map((l) => [l.key, Number(v[l.key])]));
      const s = await api.patch<Show>(`/api/shows/${show.id}`, { profile: { flags } });
      onSaved(s); setMsg({ text: "Saved. The editor uses these limits the next time a timeline is opened." });
    } catch (e: any) { setMsg({ text: e.message, err: true }); }
  }
  return (
    <div className="card" style={{ marginTop: 22 }}>
      <h3 style={{ marginTop: 0 }}>Review flag limits</h3>
      <div className="muted small-text" style={{ marginBottom: 12 }}>When a clip turns yellow or red in the editor. Red clips must be fixed or approved before Render.</div>
      <div className="form-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
        {LIMITS.map((l) => (
          <label key={l.key} className="field">
            <span className="small-text">{l.label}</span>
            <input value={v[l.key] ?? ""} placeholder={l.hint} inputMode="decimal" onChange={(e) => setV({ ...v, [l.key]: e.target.value })} />
          </label>
        ))}
      </div>
      <div className="row" style={{ marginTop: 12, gap: 10, alignItems: "center" }}>
        <button className="primary" disabled={!changed} onClick={save}>Save limits</button>
        {msg && <span className={msg.err ? "bad" : "good"} style={{ fontSize: 13 }}>{msg.text}</span>}
      </div>
    </div>
  );
}

// The show's localisation sheet: character names source video -> dub, sent to Claude with every matching request.
type Names = { path: string | null; chosen: boolean; format: string | null; count: number; names: [string, string][]; error: string | null; candidates: string[] };

function NamesSheet({ show }: { show: Show }) {
  const [n, setN] = useState<Names | null>(null);
  const [picking, setPicking] = useState(false);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<Names>(`/api/shows/${show.id}/names`).then(setN).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [show.id]); // eslint-disable-line
  const choose = (path: string | null) => api.put<Names>(`/api/shows/${show.id}/names`, { path }).then((x) => { setN(x); setErr(null); }).catch((e) => setErr(e.message));
  const [src, tgt] = (show.profile.labels || "SRC,DUB").split(",").reverse();
  return (
    <div className="card" style={{ marginTop: 22 }}>
      <h3 style={{ marginTop: 0 }}>Localisation sheet</h3>
      <div className="muted small-text" style={{ marginBottom: 10 }}>
        Character names in the source video ({src}) and in the dub ({tgt}), two columns: <code>source name</code> | <code>target name</code> (.csv or .xlsx).
        Claude gets the whole list with every matching request. The app finds it in the show folder by itself.
      </div>
      <ErrorBox error={err} />
      {n && !n.path && <div className="warn">No localisation sheet found in the show folder. Put one there (a file with "localisation" or "names" in its name), or choose it below.</div>}
      {n && n.path && <div className="panel-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <span><b>{n.count} names</b> · <span className="muted">{n.format === "two-column" ? "two-column format" : `old format (${n.format}) — read fine; download it as two columns to switch`}</span></span>
        <code className="small-text" style={{ wordBreak: "break-all" }}>{n.path}{n.chosen ? "" : " (found automatically)"}</code>
      </div>}
      <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
        <button className="small" onClick={() => setPicking(true)}>Choose a sheet…</button>
        {n?.chosen && <button className="small ghost" onClick={() => choose(null)}>Find it automatically</button>}
        {n?.path && <a className="button small ghost" href={`/api/shows/${show.id}/names.csv`}>Download as 2-column sheet</a>}
        {n?.count ? <button className="small ghost" onClick={() => setOpen((x) => !x)}>{open ? "Hide the names" : "Show the names"}</button> : null}
      </div>
      {open && n && <table className="table" style={{ marginTop: 10 }}>
        <thead><tr><th>source name ({src})</th><th>target name ({tgt})</th></tr></thead>
        <tbody>{n.names.map(([a, b], k) => <tr key={k}><td>{a}</td><td>{b}</td></tr>)}</tbody>
      </table>}
      {picking && <Picker start={show.folder} mode="file" onPick={(p) => { setPicking(false); choose(p); }} onClose={() => setPicking(false)} />}
    </div>
  );
}

// How cuts get transitions in this show: automatically at Render, or only where the editor adds one.
export function TransitionMode({ show, onSaved }: { show: Show; onSaved: (s: Show) => void }) {
  const [err, setErr] = useState<string | null>(null);
  const auto = !!show.profile.transitions;
  const set = (v: boolean) => api.patch<Show>(`/api/shows/${show.id}`, { profile: { transitions: v } }).then(onSaved).catch((e) => setErr(e.message));
  return (
    <div className="card" style={{ marginTop: 22 }}>
      <h3 style={{ marginTop: 0 }}>Transitions</h3>
      <ErrorBox error={err} />
      <label className="radio-row"><input type="radio" checked={auto} onChange={() => set(true)} />
        <span><b>Automatic at render</b><br /><span className="muted small-text">Edit as you like; when you press Render every cut gets a transition (light leak, blur dissolve, zoom blur in turn). A cut where you picked one yourself keeps your pick.</span></span></label>
      <label className="radio-row"><input type="radio" checked={!auto} onChange={() => set(false)} />
        <span><b>Manual — the editor adds them</b><br /><span className="muted small-text">Only the cuts you give a transition in the editor (click the cut, choose from the list) get one; all other cuts are hard cuts.</span></span></label>
    </div>
  );
}
