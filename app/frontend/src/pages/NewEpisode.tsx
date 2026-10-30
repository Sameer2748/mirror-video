import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Episode, Show } from "../api";
import { ErrorBox } from "../ui";

export default function NewEpisode() {
  const { sid } = useParams(); const nav = useNavigate();
  const [show, setShow] = useState<Show | null>(null);
  const [num, setNum] = useState(""); const [out, setOut] = useState("");
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<Show>(`/api/shows/${sid}`).then(setShow).catch((e) => setErr(e.message)); }, [sid]);

  const defOut = show && num ? (show.profile.output_dir || "output/ep{N}").replace("{N}", num) : "";

  async function create() {
    setBusy(true); setErr(null);
    try {
      const e = await api.post<Episode>(`/api/shows/${sid}/episodes`, { number: Number(num), output_dir: out.trim() || undefined });
      nav(`/episodes/${e.id}/files`);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  if (!show) return <div className="page"><ErrorBox error={err} /></div>;
  return (
    <div className="page narrow">
      <div className="crumbs"><Link to="/">Shows</Link> / <Link to={`/shows/${sid}`}>{show.code}</Link> / New episode</div>
      <h1>New episode</h1>
      <p className="muted">Enter the episode number. Dub Studio looks in <code>{show.folder}</code> for its video, audio, SRTs and scripts
        {show.profile.episode_offset ? ` (source episode = ${num || "N"} + ${show.profile.episode_offset})` : ""}; you confirm them on the next screen.</p>
      <div className="form">
        <label>Episode number<input type="number" min={1} value={num} autoFocus onChange={(e) => setNum(e.target.value)} onKeyDown={(e) => e.key === "Enter" && num && create()} /></label>
        <label>Output folder (optional)
          <input value={out} onChange={(e) => setOut(e.target.value)} placeholder={defOut || "default from the show profile"} />
          <span className="hint">Leave empty for the show's usual folder{defOut ? ` (${defOut})` : ""}. Relative paths are inside the show folder.</span>
        </label>
      </div>
      <ErrorBox error={err} />
      <div className="actions"><button className="primary" disabled={!num || busy} onClick={create}>{busy ? "Looking for files…" : "Find the files"}</button></div>
    </div>
  );
}
