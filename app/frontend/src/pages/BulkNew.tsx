import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Batch, Show } from "../api";
import { ErrorBox, fmtSize, Picker } from "../ui";
import { AUDIO_EXTS, folderLabel, folderUse, keepRange, matchFolder, SCRIPT_EXTS, stashBulkFiles, VIDEO_EXTS } from "./bulkFiles";

type LocalDirs = { src_video_dir: string; dub_audio_dir: string; dub_script_dir: string };
type PreviewRow = { number: number; src_video?: string | null; dub_audio?: string | null; dub_script?: string | null;
  missing: string[]; exists: boolean };
type LocalPreview = { episodes: PreviewRow[]; skipped?: Record<string, string[]> };

export default function BulkNew() {
  const { sid } = useParams(); const nav = useNavigate();
  const [show, setShow] = useState<Show | null>(null);
  const [start, setStart] = useState(""); const [end, setEnd] = useState("");
  const [videos, setVideos] = useState<File[]>([]);
  const [audios, setAudios] = useState<File[]>([]);
  const [scripts, setScripts] = useState<File[]>([]);
  const [dirs, setDirs] = useState<LocalDirs>({ src_video_dir: "", dub_audio_dir: "", dub_script_dir: "" });
  const [picking, setPicking] = useState<keyof LocalDirs | null>(null);
  const [localRows, setLocalRows] = useState<PreviewRow[] | null>(null);
  const [localSkipped, setLocalSkipped] = useState<Record<string, string[]>>({});
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);

  useEffect(() => { api.get<Show>(`/api/shows/${sid}`).then(setShow).catch((e) => setErr(e.message)); }, [sid]);

  const uploads = !!show?.profile.uploads;
  const a = Number(start), b = Number(end);
  const rangeOk = Number.isInteger(a) && Number.isInteger(b) && a >= 1 && b >= a && b - a < 100;
  const nums = rangeOk ? Array.from({ length: b - a + 1 }, (_, i) => a + i) : [];

  const videoMap = useMemo(() => keepRange(matchFolder(videos, VIDEO_EXTS, nums), nums), [videos, nums]);
  const audioMap = useMemo(() => keepRange(matchFolder(audios, AUDIO_EXTS, nums), nums), [audios, nums]);
  const scriptMap = useMemo(() => keepRange(matchFolder(scripts, SCRIPT_EXTS, nums), nums), [scripts, nums]);
  const videoUse = useMemo(() => folderUse(videos, VIDEO_EXTS, nums), [videos, nums]);
  const audioUse = useMemo(() => folderUse(audios, AUDIO_EXTS, nums), [audios, nums]);
  const scriptUse = useMemo(() => folderUse(scripts, SCRIPT_EXTS, nums), [scripts, nums]);
  const skippedN = uploads
    ? videoUse.skipped + audioUse.skipped + scriptUse.skipped
    : Object.values(localSkipped).reduce((n, xs) => n + xs.length, 0);

  const preview = nums.map((n) => {
    const src = uploads ? videoMap.get(n) : null;
    const dub = uploads ? audioMap.get(n) : null;
    const scr = uploads ? scriptMap.get(n) : null;
    const local = localRows?.find((r) => r.number === n);
    const missing = uploads
      ? [!src && "source video", !dub && "dub audio"].filter(Boolean) as string[]
      : (local?.missing || []).map((k) => k === "src_video" ? "source video" : "dub audio");
    return { n, src, dub, scr, local, missing, exists: !!local?.exists };
  });
  const readyN = preview.filter((r) => !r.missing.length && !r.exists).length;
  const canSubmit = rangeOk && readyN > 0 && (uploads ? videoMap.size > 0 && audioMap.size > 0
    : !!(dirs.src_video_dir && dirs.dub_audio_dir));

  useEffect(() => {
    if (uploads || !rangeOk || !show) { setLocalRows(null); setLocalSkipped({}); return; }
    if (!dirs.src_video_dir && !dirs.dub_audio_dir && !dirs.dub_script_dir) { setLocalRows(null); setLocalSkipped({}); return; }
    api.post<LocalPreview>(`/api/shows/${sid}/batches/preview`, { start: a, end: b, ...dirs })
      .then((r) => { setLocalRows(r.episodes); setLocalSkipped(r.skipped || {}); })
      .catch((e) => setErr(e.message));
  }, [uploads, rangeOk, a, b, dirs, show, sid]);

  async function submit() {
    if (!show) return;
    setBusy(true); setErr(null);
    try {
      const body: Record<string, unknown> = { start: a, end: b };
      if (!uploads) Object.assign(body, dirs);
      const batch = await api.post<Batch>(`/api/shows/${sid}/batches`, body);
      if (uploads) stashBulkFiles(batch.id, { video: videoMap, audio: audioMap, script: scriptMap });
      nav(`/shows/${sid}/bulk/${batch.id}`);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  if (!show) return <div className="page"><ErrorBox error={err} /></div>;
  return (
    <div className="page narrow">
      <div className="crumbs"><Link to="/">Shows</Link> / <Link to={`/shows/${sid}`}>{show.code}</Link> / Bulk generation</div>
      <h1>Bulk generation</h1>
      <p className="muted">Create many episodes at once. Each one joins the same processing queue as a single episode —
        workers pick them up on the cloud, one after another. You can still add a single episode the usual way.</p>

      <div className="name-rules">
        <b>How to name the files</b>
        <p>Each folder holds one file per episode. The name just has to contain that episode number — you do not have to rename to <code>11.mp4</code>:</p>
        <ul>
          <li>Source videos → <code>TAB FR eo 14 .mp4</code>, <code>TAB_FR_S01E11.mp4</code>, <code>11.mp4</code></li>
          <li>Dub audio → <code>TAB_Italy_Ep11.mp3</code>, <code>11.mp3</code>, <code>puntata 11.m4a</code></li>
          <li>Dub script → <code>TAB_IT_11.docx</code>, <code>11.docx</code>, <code>episode 11.txt</code></li>
        </ul>
        <p className="hint" style={{ margin: 0 }}>If you type 11–12, only files whose name is episode 11 or 12 are used.
          Extra episodes (<code>10</code>, <code>13</code>), trailers, and names with no number stay on disk.
          <code>S01E11</code> is episode 11, not 1. Years and 1080p are ignored.</p>
      </div>

      <div className="form">
        <div className="range-row">
          <label>First episode<input type="number" min={1} value={start} autoFocus
            onChange={(e) => setStart(e.target.value)} placeholder="1" /></label>
          <span className="muted">to</span>
          <label>Last episode<input type="number" min={1} value={end}
            onChange={(e) => setEnd(e.target.value)} placeholder="20" /></label>
        </div>
        <span className="hint">The range of episode numbers to create, for example 1 to 20, or 31 to 34.</span>

        {uploads ? (
          <>
            <FolderPick label="Source videos folder" hint="Only the source videos for this range are uploaded"
              files={videos} onFiles={setVideos} used={videoUse.used} skipped={videoUse.skipped} />
            <FolderPick label="Dub audio folder" hint="Only the dub audio for this range is uploaded"
              files={audios} onFiles={setAudios} used={audioUse.used} skipped={audioUse.skipped} />
            <FolderPick label="Dub script folder (optional)" hint="Only the scripts for this range are uploaded"
              files={scripts} onFiles={setScripts} used={scriptUse.used} skipped={scriptUse.skipped} />
          </>
        ) : (
          <>
            <LocalDir label="Source videos folder" hint="Any names that contain the episode number, e.g. TAB_FR_S01E11.mp4" value={dirs.src_video_dir}
              onPick={() => setPicking("src_video_dir")} />
            <LocalDir label="Dub audio folder" hint="Any names that contain the episode number, e.g. TAB_Italy_Ep11.mp3" value={dirs.dub_audio_dir}
              onPick={() => setPicking("dub_audio_dir")} />
            <LocalDir label="Dub script folder (optional)" hint="Any names that contain the episode number, e.g. TAB_IT_11.docx" value={dirs.dub_script_dir}
              onPick={() => setPicking("dub_script_dir")} />
          </>
        )}
      </div>

      {rangeOk && (
        <div className="bulk-preview">
          <div className="panel-row">
            <b>{preview.length} episode{preview.length === 1 ? "" : "s"}</b>
            <span className="muted small-text">{readyN} ready to create
              {preview.some((r) => r.missing.length) ? ` · ${preview.filter((r) => r.missing.length).length} missing a required file` : ""}
              {preview.some((r) => r.exists) ? ` · some numbers already exist on this show` : ""}
              {skippedN > 0 ? ` · skipping ${skippedN} other file${skippedN === 1 ? "" : "s"} (not uploaded)` : ""}</span>
          </div>
          <table className="table">
            <thead><tr><th>Episode</th><th>Source video</th><th>Dub audio</th><th>Dub script</th></tr></thead>
            <tbody>
              {preview.map((r) => (
                <tr key={r.n}>
                  <td><b>EP {r.n}</b>{r.exists && <div className="bad small-text">already exists</div>}</td>
                  <td><FileCell file={r.src} path={r.local?.src_video} missing={r.missing.includes("source video")} /></td>
                  <td><FileCell file={r.dub} path={r.local?.dub_audio} missing={r.missing.includes("dub audio")} /></td>
                  <td><FileCell file={r.scr} path={r.local?.dub_script} optional /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ErrorBox error={err} />
      <div className="actions">
        <button className="primary" disabled={!canSubmit || busy} onClick={submit}>
          {busy ? "Creating…" : uploads ? `Create ${readyN || ""} episode${readyN === 1 ? "" : "s"} and upload` : `Create ${readyN || ""} episode${readyN === 1 ? "" : "s"}`}
        </button>
        <Link to={`/shows/${sid}`} className="button ghost">Cancel</Link>
        <Link to={`/shows/${sid}/new-episode`} className="button ghost">Add one episode instead</Link>
      </div>
      {picking && <Picker mode="dir" start={dirs[picking] || show.folder || "~/Downloads"}
        onClose={() => setPicking(null)}
        onPick={(p) => { setDirs({ ...dirs, [picking]: p }); setPicking(null); }} />}
    </div>
  );
}

function FolderPick({ label, hint, files, onFiles, used, skipped }:
  { label: string; hint: string; files: File[]; onFiles: (f: File[]) => void; used: number; skipped: number }) {
  const extra = { webkitdirectory: "", directory: "" } as any;
  const summary = files.length
    ? `${folderLabel(files)} · using ${used} for this range`
      + (skipped ? ` · skipping ${skipped} other file${skipped === 1 ? "" : "s"}` : "")
    : "";
  return (
    <label>{label}
      <div className="row">
        <label className="button">{files.length ? "Change folder…" : "Choose folder…"}
          <input type="file" multiple style={{ display: "none" }} {...extra}
            onChange={(e) => onFiles(Array.from(e.target.files || []))} />
        </label>
        {summary && <span className="muted small-text">{summary}</span>}
      </div>
      <span className="hint">{hint}</span>
    </label>
  );
}

function LocalDir({ label, hint, value, onPick }: { label: string; hint: string; value: string; onPick: () => void }) {
  return (
    <label>{label}
      <div className="row">
        <input value={value} readOnly placeholder="/Users/…/videos" />
        <button type="button" onClick={onPick}>Browse…</button>
      </div>
      <span className="hint">{hint}</span>
    </label>
  );
}

function FileCell({ file, path, missing, optional }: { file?: File | null; path?: string | null; missing?: boolean; optional?: boolean }) {
  if (file) return <span title={file.name}>{file.name} <span className="muted">{fmtSize(file.size)}</span></span>;
  if (path) return <span className="good" title={path}>✓ {path.split("/").pop()}</span>;
  if (missing) return <span className="bad">missing</span>;
  return <span className="muted">{optional ? "none (fine)" : "—"}</span>;
}
