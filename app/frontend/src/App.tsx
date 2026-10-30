import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api, Show, useLive } from "./api";
import Shows from "./pages/Shows";
import NewShow from "./pages/NewShow";
import ShowPage from "./pages/Show";
import NewEpisode from "./pages/NewEpisode";
import EpisodePage from "./pages/Episode";
import Editor from "./pages/Editor";
import Renders from "./pages/Renders";
import Settings from "./pages/Settings";

type AiStatus = { configured: boolean; model?: string; spend?: number; max_budget?: number; error?: string };

function AiBadge() {
  const [s, setS] = useState<AiStatus | null>(null);
  useEffect(() => { api.get<AiStatus>("/api/ai/status").then(setS).catch(() => setS(null)); }, []);
  if (!s) return null;
  if (!s.configured) return <span className="chip chip-grey" title="No AI gateway key in app/.env">AI off</span>;
  const money = s.spend != null ? ` · $${s.spend.toFixed(2)}${s.max_budget ? ` / $${s.max_budget}` : ""}` : "";
  return <span className={`chip ${s.error ? "chip-red" : "chip-green"}`} title={s.error || `AI gateway: ${s.model}`}>Claude{money}</span>;
}

export function BrandMark() {
  return (
    <span className="brand-mark">
      <svg viewBox="0 0 16 16"><path d="M5 3.2l8 4.8-8 4.8z" fill="#fff" /></svg>
    </span>
  );
}

function Sidebar() {
  const [shows, setShows] = useState<Show[]>([]);
  const [active, setActive] = useState(0);
  const load = () => {
    api.get<Show[]>("/api/shows").then(setShows).catch(() => {});
    api.get<{ status: string }[]>("/api/renders?limit=20").then((r) => setActive(r.filter((x) => x.status === "running" || x.status === "waiting").length)).catch(() => {});
  };
  useEffect(() => { load(); }, []);
  useLive((e) => { if (e.kind === "episode_status") load(); });
  const { pathname } = useLocation();
  useEffect(() => { load(); }, [pathname]);
  return (
    <aside className="side">
      <Link to="/" className="brand"><BrandMark /><span>Dub Studio<small>review · edit · render</small></span></Link>
      <NavLink to="/" end className="nav-item">◫ All shows</NavLink>
      <NavLink to="/shows/new" className="nav-item">＋ New show</NavLink>
      <NavLink to="/renders" className="nav-item">▤ Render queue{active ? <><span className="grow" /><span className="chip chip-blue" style={{ padding: "1px 7px" }}>{active}</span></> : null}</NavLink>
      <NavLink to="/settings" className="nav-item">⚙ Settings</NavLink>
      {shows.length > 0 && <div className="side-label">Shows</div>}
      {shows.map((s) => {
        const c = s.episode_counts || {};
        const wait = (c.review || 0) + (c.needs_input || 0);
        return (
          <NavLink key={s.id} to={`/shows/${s.id}`} className="nav-item">
            <span className="dot" /><span className="code">{s.code}</span>
            <span className="grow" />
            {wait ? <span className="chip chip-violet" style={{ padding: "1px 7px" }}>{wait}</span> : null}
          </NavLink>
        );
      })}
      <div className="side-foot"><AiBadge /></div>
    </aside>
  );
}

export default function App() {
  const { pathname } = useLocation();
  useEffect(() => { window.scrollTo(0, 0); }, [pathname]);
  if (/^\/episodes\/\d+\/editor/.test(pathname)) {
    return <Routes><Route path="/episodes/:eid/editor" element={<Editor />} /></Routes>;
  }
  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <Routes>
          <Route path="/" element={<Shows />} />
          <Route path="/shows/new" element={<NewShow />} />
          <Route path="/renders" element={<Renders />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/shows/:sid" element={<ShowPage />} />
          <Route path="/shows/:sid/new-episode" element={<NewEpisode />} />
          <Route path="/episodes/:eid" element={<EpisodePage />} />
          <Route path="/episodes/:eid/:tab" element={<EpisodePage />} />
          <Route path="*" element={<div className="page"><h1>Not found</h1><Link to="/">Back to shows</Link></div>} />
        </Routes>
      </main>
    </div>
  );
}
