// Small shared pieces: status chips, bars, time formats, the file/folder picker.
import { useEffect, useState } from "react";
import { api } from "./api";

export const EP_STATUS: Record<string, [string, string]> = {
  new: ["Files", "grey"],
  queued: ["Queued", "blue"],
  processing: ["Processing", "blue"],
  needs_input: ["Check matching", "yellow"],
  review: ["Ready for review", "violet"],
  done: ["Done", "green"],
  failed: ["Failed", "red"],
  cancelled: ["Cancelled", "grey"],
};
export const JOB_STATUS: Record<string, [string, string]> = {
  waiting: ["Waiting", "grey"], running: ["Running", "blue"], needs_input: ["Needs you", "yellow"],
  done: ["Done", "green"], skipped: ["Skipped", "grey"], failed: ["Failed", "red"],
  cancelled: ["Cancelled", "grey"], blocked: ["Blocked", "grey"],
};

export function Chip({ status, map = EP_STATUS, extra }: { status: string; map?: Record<string, [string, string]>; extra?: string }) {
  const [label, color] = map[status] || [status, "grey"];
  return <span className={`chip chip-${color}`}>{label}{extra ? ` ${extra}` : ""}</span>;
}

export function Bar({ value, color }: { value: number; color?: string }) {
  return (
    <div className="bar"><div className={`bar-fill ${color || ""}`} style={{ width: `${Math.round(Math.min(Math.max(value, 0), 1) * 100)}%` }} /></div>
  );
}

export function fmtDur(s?: number | null) {
  if (s == null || !isFinite(s)) return "";
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min ${sec.toString().padStart(2, "0")} s`;
  return `${sec} s`;
}
export function fmtMs(ms?: number | null) {
  if (ms == null) return "";
  const s = ms / 1000, m = Math.floor(s / 60);
  return `${m.toString().padStart(2, "0")}:${(s % 60).toFixed(1).padStart(4, "0")}`;
}
export function fmtWhen(ts?: number | null) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
export function fmtSize(b?: number | null) {
  if (b == null) return "";
  if (b > 1 << 30) return `${(b / (1 << 30)).toFixed(1)} GB`;
  if (b > 1 << 20) return `${(b / (1 << 20)).toFixed(1)} MB`;
  return `${Math.round(b / 1024)} KB`;
}
export const base = (p: string) => p.split("/").pop() || p;

/** Send a file to the API with a progress bar (fetch cannot report upload progress, XHR can).
 *  The API streams it on into R2, so a 650 MB source video never sits in memory. */
export function uploadFile<T = any>(url: string, file: File, onProgress?: (fraction: number) => void,
                                    field = "file"): Promise<T> {
  return new Promise((resolve, reject) => {
    const form = new FormData(); form.append(field, file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      let data: any = null;
      try { data = xhr.responseText ? JSON.parse(xhr.responseText) : null; } catch { data = xhr.responseText; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as T);
      else reject(new Error((data && data.detail) || `${xhr.status} ${xhr.statusText}`));
    };
    xhr.onerror = () => reject(new Error("the upload stopped: check the connection"));
    xhr.send(form);
  });
}

/** Upload a file of any size to an episode: small files in one go, big ones in parts (a dropped line then only
 *  costs the part it was on, and the progress bar keeps moving). The API streams each part on to R2. */
type UploadStart = { key: string | null; upload_id: string | null; name: string; part_size: number; parts: number; mode: string };

export async function uploadEpisodeFile<T = any>(eid: number, kind: string, file: File,
                                                 onProgress?: (fraction: number) => void): Promise<T> {
  const start = await api.post<UploadStart>(`/api/episodes/${eid}/files/${kind}/start`,
    { filename: file.name, size: file.size });
  const direct = start.mode === "direct" && !!start.key;
  if (start.mode === "single")                            // small file through the API: one request, simplest
    return uploadFile<T>(`/api/episodes/${eid}/files/${kind}`, file, onProgress);
  const etags: [number, string][] = [];
  try {
    for (let n = 1, at = 0; at < file.size; n++, at += start.part_size) {
      const blob = file.slice(at, Math.min(at + start.part_size, file.size));
      let etag: string | null = null;
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          if (direct) {                                   // the bytes go from the browser straight to R2
            const { url } = await api.post<{ url: string }>(`/api/episodes/${eid}/files/${kind}/part-url`,
              { key: start.key, upload_id: start.upload_id, part: n });
            const r = await fetch(url, { method: "PUT", body: blob });
            if (!r.ok) throw new Error(`cloud storage refused part ${n} (${r.status})`);
            etag = (r.headers.get("ETag") || "").replace(/"/g, "");
          } else {                                        // through the API, which streams the part on to R2
            const q = new URLSearchParams({ part: String(n), name: start.name });
            if (start.key) { q.set("key", start.key); q.set("upload_id", start.upload_id!); }
            const r = await fetch(`/api/episodes/${eid}/files/${kind}/part?${q}`,
              { method: "PUT", body: blob, credentials: "same-origin" });
            if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status} ${r.statusText}`);
            etag = (await r.json()).etag;
          }
          break;
        } catch (e) { if (attempt === 2) throw e; await new Promise((s) => setTimeout(s, 1500 * (attempt + 1))); }
      }
      if (etag) etags.push([n, etag]);
      onProgress?.(Math.min((at + blob.size) / file.size, 1));
    }
    return await api.post<T>(`/api/episodes/${eid}/files/${kind}/finish`,
      { key: start.key, upload_id: start.upload_id, name: start.name, etags });
  } catch (e) {
    await api.post(`/api/episodes/${eid}/files/${kind}/abort`, { key: start.key, upload_id: start.upload_id }).catch(() => {});
    throw e;
  }
}

export function ErrorBox({ error }: { error?: string | null }) {
  if (!error) return null;
  return <div className="error">{error}</div>;
}

type FsItem = { name: string; path: string; dir: boolean; size: number | null };

/** Modal browser over /api/fs. mode "dir" picks a folder, "file" picks a file. */
export function Picker({ start, mode, onPick, onClose, accept }:
  { start?: string; mode: "dir" | "file"; onPick: (p: string) => void; onClose: () => void; accept?: string[] }) {
  const [path, setPath] = useState(start || "~");
  const [data, setData] = useState<{ path: string; parent: string; items: FsItem[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setErr(null);
    api.get<any>(`/api/fs?path=${encodeURIComponent(path)}`).then(setData).catch((e) => {
      if (path !== "~") setPath("~"); else setErr(e.message);
    });
  }, [path]);
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <b>{mode === "dir" ? "Choose a folder" : "Choose a file"}</b>
          <button className="ghost" onClick={onClose}>Close</button>
        </div>
        <div className="picker-path">
          <button className="small" disabled={!data} onClick={() => data && setPath(data.parent)}>↑ Up</button>
          <code>{data?.path}</code>
        </div>
        <ErrorBox error={err} />
        <div className="picker-list">
          {data?.items.filter((i) => {
            if (i.dir) return true;
            if (mode !== "file") return false;
            if (!accept?.length) return true;
            const ext = (i.name.split(".").pop() || "").toLowerCase();
            return accept.includes(ext);
          }).map((i) => (
            <div key={i.path} className="picker-item" onDoubleClick={() => (i.dir ? setPath(i.path) : onPick(i.path))}
              onClick={() => (i.dir ? setPath(i.path) : onPick(i.path))}>
              <span>{i.dir ? "📁" : "📄"} {i.name}</span>
              <span className="muted">{i.dir ? "" : fmtSize(i.size)}</span>
            </div>
          ))}
          {data && !data.items.length && <div className="muted pad">Empty folder</div>}
        </div>
        {mode === "dir" && (
          <div className="modal-foot">
            <button className="primary" disabled={!data} onClick={() => data && onPick(data.path)}>Use this folder</button>
          </div>
        )}
      </div>
    </div>
  );
}
