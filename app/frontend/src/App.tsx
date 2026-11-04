import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api, auth, onSignedOut, Show, useLive, User } from "./api";
import Shows from "./pages/Shows";
import NewShow from "./pages/NewShow";
import ShowPage from "./pages/Show";
import NewEpisode from "./pages/NewEpisode";
import BulkNew from "./pages/BulkNew";
import BulkStatus from "./pages/BulkStatus";
import EpisodePage from "./pages/Episode";
import Editor from "./pages/Editor";
import Renders from "./pages/Renders";
import Settings from "./pages/Settings";
import Login from "./pages/Login";
import Accounts from "./pages/Accounts";

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

function WhoAmI({ me, onOut }: { me: User | null; onOut: () => void }) {
  if (!me) return null;
  return (
    <div className="who">
      <Link to="/account" className="who-name" title={`Signed in as ${me.username}${me.role === "admin" ? " (owner)" : ""}`}>
        <span className="who-dot">{(me.name || me.username).slice(0, 1).toUpperCase()}</span>
        <span className="grow">{me.name || me.username}</span>
      </Link>
      <button className="small ghost" onClick={onOut}>Sign out</button>
    </div>
  );
}

function Sidebar({ me, onOut }: { me: User | null; onOut: () => void }) {
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
      <Link to="/" className="brand"><BrandMark /><span>Mirror Video<small>review · edit · render</small></span></Link>
      <NavLink to="/" end className="nav-item">◫ All shows</NavLink>
      <NavLink to="/shows/new" className="nav-item">＋ New show</NavLink>
      <NavLink to="/renders" className="nav-item">▤ Render queue{active ? <><span className="grow" /><span className="chip chip-blue" style={{ padding: "1px 7px" }}>{active}</span></> : null}</NavLink>
      <NavLink to="/account" className="nav-item">◉ {me?.role === "admin" ? "Accounts" : "My account"}</NavLink>
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
      <div className="side-foot"><AiBadge /><WhoAmI me={me} onOut={onOut} /><div className="built-by">Built by Sameer</div></div>
    </aside>
  );
}

export default function App() {
  const { pathname } = useLocation();
  const [me, setMe] = useState<User | null>(null);
  const [need, setNeed] = useState<boolean | null>(null);      // null = we have not asked the server yet
  useEffect(() => { window.scrollTo(0, 0); }, [pathname]);
  useEffect(() => {
    auth.me().then((m) => { setMe(m.user); setNeed(m.required && !m.user); })
      .catch(() => { setMe(null); setNeed(true); });
    onSignedOut(() => { setMe(null); setNeed(true); });
    return () => onSignedOut(null);
  }, []);
  async function signOut() {
    try { await auth.logout(); } catch { /* the cookie is gone either way */ }
    setMe(null); setNeed(true);
  }

  if (need === null) return <div className="signin"><div className="muted">…</div></div>;
  if (need) return <Login onDone={(u) => { setMe(u); setNeed(false); }} />;
  if (/^\/episodes\/\d+\/editor/.test(pathname)) {
    return <Routes><Route path="/episodes/:eid/editor" element={<Editor />} /></Routes>;
  }
  return (
    <div className="app">
      <Sidebar me={me} onOut={signOut} />
      <main className="main">
        <div className="main-body">
          <Routes>
            <Route path="/" element={<Shows />} />
            <Route path="/shows/new" element={<NewShow />} />
            <Route path="/renders" element={<Renders />} />
            <Route path="/account" element={<Accounts me={me} onMe={setMe} />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/shows/:sid" element={<ShowPage />} />
            <Route path="/shows/:sid/new-episode" element={<NewEpisode />} />
            <Route path="/shows/:sid/bulk/:bid" element={<BulkStatus />} />
            <Route path="/shows/:sid/bulk" element={<BulkNew />} />
            <Route path="/episodes/:eid" element={<EpisodePage />} />
            <Route path="/episodes/:eid/:tab" element={<EpisodePage />} />
            <Route path="*" element={<div className="page"><h1>Not found</h1><Link to="/">Back to shows</Link></div>} />
          </Routes>
        </div>
        <div className="built-by page-built">Built by Sameer</div>
      </main>
    </div>
  );
}
