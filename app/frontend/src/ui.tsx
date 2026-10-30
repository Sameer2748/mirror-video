// Small shared pieces: status chips, bars, time formats, the file/folder picker.
import { useEffect, useState } from "react";
import { api } from "./api";

export const EP_STATUS: Record<string, [string, string]> = {
  new: ["Uploaded", "grey"],
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

export function ErrorBox({ error }: { error?: string | null }) {
  if (!error) return null;
  return <div className="error">{error}</div>;
}

type FsItem = { name: string; path: string; dir: boolean; size: number | null };

/** Modal browser over /api/fs. mode "dir" picks a folder, "file" picks a file. */
export function Picker({ start, mode, onPick, onClose }: { start?: string; mode: "dir" | "file"; onPick: (p: string) => void; onClose: () => void }) {
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
          {data?.items.filter((i) => i.dir || mode === "file").map((i) => (
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
