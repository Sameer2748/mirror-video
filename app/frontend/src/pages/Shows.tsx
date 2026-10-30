import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Show } from "../api";
import { ErrorBox } from "../ui";

export default function Shows() {
  const [shows, setShows] = useState<Show[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.get<Show[]>("/api/shows").then(setShows).catch((e) => setErr(e.message)); }, []);

  const all = (shows || []).reduce((a, s) => { Object.entries(s.episode_counts || {}).forEach(([k, v]) => (a[k] = (a[k] || 0) + v)); return a; }, {} as Record<string, number>);
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Shows</h1>
          <div className="page-sub">Every show you dub. Open one to process episodes, review them on the timeline and render.</div>
        </div>
        <Link to="/shows/new" className="button primary">+ New show</Link>
      </div>
      <ErrorBox error={err} />
      {shows && shows.length > 0 && (
        <div className="stats">
          <div className="stat"><b>{shows.length}</b><span>shows</span></div>
          <div className="stat"><b>{Object.values(all).reduce((a, b) => a + b, 0)}</b><span>episodes</span></div>
          <div className="stat"><b style={{ color: "var(--violet)" }}>{(all.review || 0) + (all.needs_input || 0)}</b><span>waiting for you</span></div>
          <div className="stat"><b style={{ color: "var(--green)" }}>{all.done || 0}</b><span>rendered</span></div>
        </div>
      )}
      {shows && !shows.length && (
        <div className="empty">
          <p style={{ fontSize: 16, fontWeight: 600 }}>No shows yet.</p>
          <p className="muted">Create one from a preset (TDMB, TOLR, WBM) and point it at its folder.</p>
          <Link to="/shows/new" className="button primary">Create the first show</Link>
        </div>
      )}
      <div className="cards">
        {shows?.map((s) => {
          const c = s.episode_counts || {};
          const total = Object.values(c).reduce((a, b) => a + b, 0);
          return (
            <Link key={s.id} to={`/shows/${s.id}`} className="card show-card">
              <span className="card-art" />
              <span className="card-code">{s.code}</span>
              <div className="card-title">{s.name}</div>
              <div className="muted small-text ellipsis" title={s.folder}>{s.folder}</div>
              <div className="card-stats">
                <span>{total} episode{total === 1 ? "" : "s"}</span>
                {c.review ? <span className="chip chip-violet">{c.review} to review</span> : null}
                {c.needs_input ? <span className="chip chip-yellow">{c.needs_input} to check</span> : null}
                {c.processing ? <span className="chip chip-blue">{c.processing} processing</span> : null}
                {c.failed ? <span className="chip chip-red">{c.failed} failed</span> : null}
                {c.done ? <span className="chip chip-green">{c.done} done</span> : null}
              </div>
            </Link>
          );
        })}
        {shows && shows.length > 0 && (
          <Link to="/shows/new" className="card card-new"><span className="plus">+</span><span>New show</span></Link>
        )}
      </div>
    </div>
  );
}
