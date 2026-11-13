// The editor: after processing and before render, the episode is a timeline of clips locked to the dub audio.
// Each clip fills a dub slot (t0..t1) with source footage (s0..s1) at speed (s1-s0)/(t1-t0). Edits never move the
// dub: roll (move a cut), slip (slide the footage), speed, razor, replace from source, swap two clips, delete
// (the neighbour fills the slot), copy/paste footage, lock. Subtitles (S1 dub, S2 English) are edited here too and
// burned from the edited tracks at render; QC notes are markers that stay red flags until resolved. The monitors play
// the 540-px preview copy once it exists. Render writes the timeline as the pipeline's edit list and runs render → check.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import GenPanel, { GenClip } from "./GenPanel";
import { BrandMark } from "../App";

type Clip = { id: string; row: number | null; t0: number; t1: number; s0: number; s1: number; dub_text: string; src_text: string; note?: string; locked?: boolean; tin?: string; media?: string; broll?: boolean; zoom?: number; pan_x?: number; pan_y?: number; orig?: { t0: number; t1: number; s0: number; s1: number } };
// the project bin: clips imported for this episode + the episode's own files
type Imp = { id: string; name: string; duration_ms: number; width: number; height: number; size: number; preview: string };
type BinFile = { kind: string; label: string; name: string; size: number };
type Hole = { row: number; t0: number; t1: number; text: string };
type Flag = { kind: string; sev: "red" | "yellow" | "info"; msg: string; approved?: boolean; marker?: number };
type Marker = { id: number; t_ms: number; text: string; source: string; resolved: number };
type Lang = "dub" | "en";
type Subs = { dub: Cue[]; en: Cue[] };
type SubStyle = { style?: "box" | "strip"; size?: number; bottom?: number; alpha?: number };
const STYLE_DEFAULT = { box: { size: 38, bottom: 71, alpha: 0.3 }, strip: { size: 40, bottom: 0, alpha: 1 } };
function parseSrt(text: string): Cue[] {
  const out: Cue[] = [];
  for (const block of text.replace(/\r/g, "").split(/\n\s*\n/)) {
    const lines = block.split("\n").filter((l) => l.trim());
    const k = lines.findIndex((l) => l.includes("-->")); if (k < 0) continue;
    const m = lines[k].match(/(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)/); if (!m) continue;
    const g = m.slice(1).map(Number);
    const text2 = lines.slice(k + 1).join("\n").trim(); if (!text2) continue;
    out.push({ t0: ((g[0] * 60 + g[1]) * 60 + g[2]) * 1000 + g[3], t1: ((g[4] * 60 + g[5]) * 60 + g[6]) * 1000 + g[7], text: text2 });
  }
  return out.sort((a, b) => a.t0 - b.t0);
}
type TransInfo = { default: string; styles: { id: string; label: string; half_s: number; uses: number }[] };
const TLABEL: Record<string, string> = { auto: "Automatic", none: "Hard cut", light_leak: "Light leak", blur_dissolve: "Blur dissolve", zoom_blur: "Zoom blur", glow: "Glow" };
type Proxy = { ready: boolean; running?: boolean; progress: number; error?: string | null; rev?: number };
type Cue = { t0: number; t1: number; text: string };
type Sentence = { idx: number; text: string; start_ms: number; end_ms: number };
type Tracks = { v1?: { hidden?: boolean; locked?: boolean }; v2?: { hidden?: boolean; locked?: boolean }; src?: { hidden?: boolean } };
type SrcSeg = { s0: number; s1: number; text: string; idx: number | null };
type Timeline = { base: string; total_ms: number; clips: Clip[]; v2?: Clip[]; tracks?: Tracks; approved: string[]; version: number; draft?: boolean; holes?: Hole[] };
// what plays: V2 over V1 (else a gap = black); same rule as the backend's timeline.flatten
type Piece = { t0: number; t1: number; s0: number; s1: number; id: string; track: 1 | 2; gap?: false; media?: string } | { t0: number; t1: number; gap: true; id?: undefined; s0?: undefined; s1?: undefined; track?: undefined; media?: undefined };
function flatten(v1: Clip[], v2: Clip[], total: number): Piece[] {
  const at = (c: Clip, t: number) => c.s0 + ((t - c.t0) * (c.s1 - c.s0)) / Math.max(c.t1 - c.t0, 1e-6);
  const xs = Array.from(new Set([0, total, ...v1.flatMap((c) => [c.t0, c.t1]), ...v2.flatMap((c) => [c.t0, c.t1])].filter((x) => x >= 0 && x <= total))).sort((a, b) => a - b);
  const out: Piece[] = [];
  for (let k = 0; k + 1 < xs.length; k++) {
    const a = xs[k], b = xs[k + 1]; if (b - a < 1e-6) continue; const m = (a + b) / 2;
    const c = v2.find((x) => x.t0 <= m && m < x.t1); const d = c || v1.find((x) => x.t0 <= m && m < x.t1);
    const last = out[out.length - 1];
    if (!d) { if (last && last.gap) last.t1 = b; else out.push({ t0: a, t1: b, gap: true }); continue; }
    if (last && !last.gap && last.id === d.id) { last.t1 = b; last.s1 = at(d, b); continue; }
    out.push({ t0: a, t1: b, s0: at(d, a), s1: at(d, b), id: d.id, track: c ? 2 : 1, media: d.media });
  }
  return out;
}
type Version = { version: number; made_by: string; note: string | null; created: number };
type Data = {
  timeline: Timeline; flags: Record<string, Flag[]>; counts: { red: number; yellow: number }; problems: string[];
  versions: Version[]; src_sentences: Sentence[]; dub_starts: number[]; lip_rows: number[]; subs: Subs; subs_edited: Record<Lang, boolean>;
  markers: Marker[]; proxy: Proxy; labels: string[]; sub_style: string; sub_styles: Partial<Record<Lang, SubStyle>>;
  transitions: TransInfo;
  src_duration_ms: number | null; state: Record<string, string>; show: { id: number; code: string }; episode: { id: number; number: number; status: string };
};
type Snap = { clips: Clip[]; approved: string[]; subs: Subs; v2: Clip[] };
type Tool = "select" | "razor" | "slip";

const FRAME = 1000 / 29.97;
const MIN_SLOT = 120;
const fmt = (ms: number, dec = 1) => {
  const neg = ms < 0; ms = Math.abs(ms); const s = ms / 1000; const m = Math.floor(s / 60);
  return `${neg ? "-" : ""}${String(m).padStart(2, "0")}:${(s % 60).toFixed(dec).padStart(dec ? 3 + dec : 2, "0")}`;
};
const speedOf = (c: Clip) => (c.s1 - c.s0) / Math.max(c.t1 - c.t0, 1);
const label = (c: Clip) => (c.row != null ? `IT#${c.row}` : c.media ? `${(c.src_text || "imported").slice(0, 18)}` : `clip ${fmt(c.s0)}`);
const clamp = (x: number, a: number, b: number) => Math.min(Math.max(x, a), b);
const MOTION_Z_MAX = 4;
function applyMotion(c: Clip, zoom: number, pan_x: number, pan_y: number): Clip {
  const z = Math.round(clamp(zoom, 1, MOTION_Z_MAX) * 100) / 100;
  const out: Clip = { ...c };
  if (z <= 1.001) { delete out.zoom; delete out.pan_x; delete out.pan_y; return out; }
  out.zoom = z;
  out.pan_x = Math.round(clamp(pan_x, -1, 1) * 100) / 100;
  out.pan_y = Math.round(clamp(pan_y, -1, 1) * 100) / 100;
  return out;
}
function paintMotion(el: HTMLVideoElement | null, clip: Clip | undefined, extra = 1) {
  if (!el) return;
  const z = Math.max(1, Math.min(Number(clip?.zoom) || 1, 8));
  const scale = z * extra;
  if (scale <= 1.001) { el.style.transform = ""; el.style.transformOrigin = ""; return; }
  const hasPan = clip?.pan_x != null || clip?.pan_y != null;
  const px = clamp(Number(clip?.pan_x) || 0, -1, 1);
  const py = clamp(Number(clip?.pan_y) || 0, -1, 1);
  el.style.transformOrigin = hasPan ? `${50 + px * 50}% ${50 + py * 50}%` : "50% 100%";
  el.style.transform = `scale(${scale})`;
}
const uid = () => "e" + Math.random().toString(36).slice(2, 8);
const worst = (fl?: Flag[]) => {
  if (!fl) return "";
  const open = fl.filter((f) => !f.approved);
  if (open.some((f) => f.sev === "red")) return "red";
  if (open.some((f) => f.sev === "yellow")) return "yellow";
  if (fl.some((f) => f.approved)) return "ok";
  return "";
};
function findClip<T extends { t0: number; t1: number }>(clips: T[], t: number) {
  let lo = 0, hi = clips.length - 1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (t < clips[m].t0) hi = m - 1; else if (t >= clips[m].t1) lo = m + 1; else return m; }
  return clamp(lo, 0, clips.length - 1);
}
const srcAt = (c: Clip, t: number) => c.s0 + (t - c.t0) * speedOf(c);

// ---------------- pure edits ----------------
// a real trim: only this clip's edge moves (its speed stays); pulled in = a gap, pulled out = it overwrites its neighbours
function trimEdge(list: Clip[], i: number, side: "l" | "r", t: number, total: number, srcDur: number): Clip[] {
  const c = list[i]; const r = Math.max(speedOf(c), 1e-6); const out: (Clip | null)[] = list.slice();
  if (side === "r") {
    let t1 = clamp(t, c.t0 + MIN_SLOT, total); t1 = Math.min(t1, c.t0 + (srcDur - c.s0) / r);
    out[i] = { ...c, t1, s1: c.s0 + (t1 - c.t0) * r };
    for (let k = i + 1; k < list.length; k++) {
      const d = list[k]; if (d.t0 >= t1 - 0.5) break;
      out[k] = d.t1 - t1 < MIN_SLOT ? null : { ...d, t0: t1, s0: srcAt(d, t1) };
    }
  } else {
    let t0 = clamp(t, 0, c.t1 - MIN_SLOT); t0 = Math.max(t0, c.t1 - c.s1 / r);
    out[i] = { ...c, t0, s0: c.s1 - (c.t1 - t0) * r };
    for (let k = i - 1; k >= 0; k--) {
      const d = list[k]; if (d.t1 <= t0 + 0.5) break;
      out[k] = t0 - d.t0 < MIN_SLOT ? null : { ...d, t1: t0, s1: srcAt(d, t0) };
    }
  }
  return out.filter((x): x is Clip => !!x);
}
// a V2 clip slides along its track (same footage), never over another V2 clip
function moveClip(list: Clip[], i: number, t0: number, total: number): Clip[] {
  const c = list[i]; const len = c.t1 - c.t0;
  const lo = i > 0 ? list[i - 1].t1 : 0, hi = i < list.length - 1 ? list[i + 1].t0 : total;
  t0 = clamp(t0, lo, Math.max(lo, hi - len)); if (hi - lo < len) return list;
  const out = list.slice(); out[i] = { ...c, t0, t1: t0 + len }; return out;
}
function roll(clips: Clip[], i: number, t: number): Clip[] {
  const a = clips[i], b = clips[i + 1]; if (!a || !b) return clips;
  t = clamp(t, a.t0 + MIN_SLOT, b.t1 - MIN_SLOT);
  const ra = speedOf(a), rb = speedOf(b);
  const na = { ...a, t1: t, s1: a.s0 + (t - a.t0) * ra };
  const nb = { ...b, t0: t, s0: Math.max(0, b.s1 - (b.t1 - t) * rb) };
  const out = clips.slice(); out[i] = na; out[i + 1] = nb; return out;
}
function slip(c: Clip, d: number, srcDur: number): Clip {
  const len = c.s1 - c.s0; const s0 = clamp(c.s0 + d, 0, Math.max(0, srcDur - len));
  return { ...c, s0, s1: s0 + len };
}
function setSpeed(c: Clip, r: number, srcDur: number): Clip {
  r = clamp(r, 0.1, 6); const s1 = Math.min(c.s0 + (c.t1 - c.t0) * r, srcDur); return { ...c, s1 };
}
function razor(clips: Clip[], i: number, t: number): Clip[] {
  const c = clips[i]; if (!c || t - c.t0 < MIN_SLOT || c.t1 - t < MIN_SLOT) return clips;
  const s = srcAt(c, t);
  const a = { ...c, t1: t, s1: s }, b = { ...c, id: uid(), t0: t, s0: s, orig: undefined };
  const out = clips.slice(); out.splice(i, 1, a, b); return out;
}
function del(clips: Clip[], i: number): Clip[] {
  if (clips.length < 2) return clips;
  const c = clips[i]; const out = clips.slice();
  if (i > 0) { const p = out[i - 1]; const r = speedOf(p); out[i - 1] = { ...p, t1: c.t1, s1: p.s0 + (c.t1 - p.t0) * r }; }
  else { const n = out[1]; const r = speedOf(n); out[1] = { ...n, t0: c.t0, s0: Math.max(0, n.s1 - (n.t1 - c.t0) * r) }; }
  out.splice(i, 1); return out;
}
function swap(clips: Clip[], i: number, j: number): Clip[] {
  const a = clips[i], b = clips[j]; if (!a || !b || i === j) return clips;
  const out = clips.slice();
  out[i] = { ...a, s0: b.s0, s1: b.s1, src_text: b.src_text };
  out[j] = { ...b, s0: a.s0, s1: a.s1, src_text: a.src_text };
  return out;
}
function splitCue(cues: Cue[], k: number, t: number): Cue[] | null {
  const c = cues[k]; if (!c || t - c.t0 < 150 || c.t1 - t < 150) return null;
  let a = c.text, b = c.text;
  const nl = c.text.indexOf("\n");
  if (nl > 0) { a = c.text.slice(0, nl); b = c.text.slice(nl + 1); }
  else {
    const w = c.text.split(/\s+/);
    if (w.length > 1) { const n = clamp(Math.round((w.length * (t - c.t0)) / (c.t1 - c.t0)), 1, w.length - 1); a = w.slice(0, n).join(" "); b = w.slice(n).join(" "); }
  }
  const out = cues.slice(); out.splice(k, 1, { t0: c.t0, t1: Math.round(t), text: a }, { t0: Math.round(t), t1: c.t1, text: b }); return out;
}
function mergeCues(cues: Cue[], k: number): Cue[] | null {      // cue k with cue k+1
  const a = cues[k], b = cues[k + 1]; if (!a || !b) return null;
  const out = cues.slice(); out.splice(k, 2, { t0: a.t0, t1: b.t1, text: `${a.text} ${b.text}`.replace(/\s+\n|\n\s+/g, "\n") }); return out;
}
// the nearest snap point within `px` pixels, or null
function snapTo(t: number, pts: number[], pxms: number, px = 8): number | null {
  let best: number | null = null, bd = px / pxms;
  for (const x of pts) { const d = Math.abs(x - t); if (d < bd) { bd = d; best = x; } }
  return best;
}
function srcTextFor(s0: number, s1: number, sents: Sentence[]) {
  return sents.filter((x) => Math.min(x.end_ms, s1) - Math.max(x.start_ms, s0) > 250).map((x) => x.text).join(" ");
}

const STEPNAME: Record<string, string> = { lip_gpu: "Lip scan", lip_mesh: "Lip scan", mksrt_dub: "Whisper", mksrt_src: "Whisper",
  words_src: "Whisper", words_dub: "Whisper", analyze: "Matching", render: "Render", transitions: "Transitions", subs_dub: "Subtitles",
  subs_en: "Subtitles", proxy: "Preview copy", bookends: "Opening + ending", verify: "Sync check", check: "Final check" };
const modKey = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? "⌘" : "Ctrl+";
// "1:02.5", "62.5", "01:02:03.4" -> ms (null when empty or unreadable)
function parseTime(x: string): number | null {
  const t = x.trim(); if (!t) return null;
  const parts = t.split(":").map(Number); if (parts.some((n) => isNaN(n))) return null;
  return Math.round(parts.reduce((acc, n) => acc * 60 + n, 0) * 1000);
}
function TimeField({ value, onSet, placeholder, title }: { value: number | null; onSet: (v: number | null) => void; placeholder: string; title: string }) {
  const [text, setText] = useState(value != null ? fmt(value, 2) : "");
  useEffect(() => { setText(value != null ? fmt(value, 2) : ""); }, [value]);
  const done = (raw: string) => { const v = parseTime(raw); if (v == null && raw.trim()) { setText(value != null ? fmt(value, 2) : ""); return; } if (v !== value) onSet(v); };
  return <input className="time-field mono" value={text} placeholder={placeholder} title={title} onChange={(e) => setText(e.target.value)}
    onBlur={(e) => done(e.currentTarget.value)} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") { setText(value != null ? fmt(value, 2) : ""); (e.target as HTMLInputElement).blur(); } }} />;
}

// panels the editor can fold away, remembered in this browser (open by default)
const pref = (k: string) => { try { return localStorage.getItem(k) !== "0"; } catch { return true; } };
const savePref = (k: string, v: boolean) => { try { localStorage.setItem(k, v ? "1" : "0"); } catch { /* private mode */ } return v; };

// ---------------- the screen ----------------
export default function Editor() {
  const { eid } = useParams(); const nav = useNavigate();
  const [data, setData] = useState<Data | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [v2, setV2] = useState<Clip[]>([]);
  const [tracks, setTracks] = useState<Tracks>({});
  const [approved, setApproved] = useState<string[]>([]);
  const [flags, setFlags] = useState<Record<string, Flag[]>>({});
  const [counts, setCounts] = useState({ red: 0, yellow: 0 });
  const [problems, setProblems] = useState<string[]>([]);
  const [hideProblems, setHideProblems] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [tool, setTool] = useState<Tool>("select");
  const [zoom, setZoom] = useState(36);                 // px per second
  const [subMode, setSubMode] = useState<"dub" | "en" | "none">("dub");
  const [leftTab, setLeftTab] = useState<"flags" | "markers" | "mapping" | "source" | "versions">("flags");
  const [flagFilter, setFlagFilter] = useState<"open" | "red" | "all" | "approved">("open");
  const [playing, setPlaying] = useState(false);
  const [tShow, setTShow] = useState(0);                 // the playhead for text displays (~10 fps)
  const [markIn, setMarkIn] = useState<number | null>(null);
  const [markOut, setMarkOut] = useState<number | null>(null);
  const [srcT, setSrcT] = useState(0);
  const [version, setVersion] = useState(0);
  const [saveState, setSaveState] = useState<"saved" | "editing" | "draft" | "saving">("saved");
  const [toast, setToast] = useState<{ text: string; err?: boolean } | null>(null);
  const [showKeys, setShowKeys] = useState(false);
  const [tlH, setTlH] = useState(300);
  const [busy, setBusy] = useState(false);
  const [subs, setSubs] = useState<Subs>({ dub: [], en: [] });
  const [subsEdited, setSubsEdited] = useState<Record<Lang, boolean>>({ dub: false, en: false });
  const [selCue, setSelCue] = useState<{ lang: Lang; k: number } | null>(null);
  const [markers, setMarkers] = useState<Marker[]>([]);
  const [markerText, setMarkerText] = useState("");
  const [snapOn, setSnapOn] = useState(true);
  const [proxy, setProxy] = useState<Proxy>({ ready: false, progress: 0 });
  const [trackDlg, setTrackDlg] = useState<Lang | null>(null);
  const [monH, setMonH] = useState(400);
  const [srcAR, setSrcAR] = useState(16 / 9);            // final-video aspect: the program frame stays this size when you zoom
  const [clipboard, setClipboard] = useState<{ s0: number; s1: number; src_text: string; from: string; speed: number; media?: string; fromSource?: boolean; fromId?: string } | null>(null);
  const lastPane = useRef<"src" | "tl">("tl");             // where the editor last clicked: ⌘C copies from there
  const [bin, setBin] = useState<{ imports: Imp[]; files: BinFile[] }>({ imports: [], files: [] });
  const [srcMedia, setSrcMedia] = useState<string | null>(null);   // what the source monitor shows: null = the source video
  const [srcOpen, setSrcOpen] = useState(() => pref("ds.srcOpen"));
  const [leftOpen, setLeftOpen] = useState(() => pref("ds.leftOpen"));
  const [inspOpen, setInspOpen] = useState(() => pref("ds.inspOpen"));
  const [binOpen, setBinOpen] = useState(() => pref("ds.binOpen"));
  const [genClip, setGenClip] = useState<GenClip | null>(null);        // the clip the Generate panel is open for
  const binRef = useRef(bin); binRef.current = bin;
  const savedSubs = useRef<Subs>({ dub: [], en: [] });
  const monRO = useRef<ResizeObserver | null>(null);
  const markerInput = useRef<HTMLInputElement>(null);
  const hist = useRef<{ undo: Snap[]; redo: Snap[] }>({ undo: [], redo: [] });
  const tRef = useRef(0);
  const dirtyN = useRef(0);

  const say = (text: string, e = false) => { setToast({ text, err: e }); window.setTimeout(() => setToast(null), e ? 6000 : 2600); };
  const srcDur = data?.src_duration_ms || 1e9;
  const impOf = (id?: string | null) => (id ? binRef.current.imports.find((x) => x.id === id) : undefined);
  const durOf = (media?: string | null) => (media ? impOf(media)?.duration_ms || 0 : srcDur);   // footage length of a clip's file
  const impUrl = (id: string) => `/api/episodes/${eid}/imports/${id}/media`;
  // what is dragged onto the timeline: a source sentence (its number) or an imported clip ("imp:<id>")
  const footOf = (v: string | number): { s0: number; s1: number; text: string; why: string; media?: string } | null => {
    const sv = String(v);
    if (sv.startsWith("imp:")) { const x = impOf(sv.slice(4)); return x ? { s0: 0, s1: x.duration_ms, text: x.name, why: `imported ${x.name}`, media: x.id } : null; }
    const x = data?.src_sentences.find((q) => q.idx === Number(sv));
    return x ? { s0: x.start_ms, s1: x.end_ms, text: x.text, why: `${data!.labels[1]} sentence ${x.idx}` } : null;
  };
  const loadBin = useCallback(() => api.get<{ imports: Imp[]; files: BinFile[] }>(`/api/episodes/${eid}/bin`).then(setBin).catch(() => {}), [eid]);
  useEffect(() => { loadBin(); }, [loadBin]);
  useEffect(() => {                                          // preview copies of new imports: check until made
    if (!bin.imports.some((x) => x.preview === "making")) return;
    const t = window.setTimeout(loadBin, 2000); return () => clearTimeout(t);
  }, [bin, loadBin]);
  const toggleSrc = () => setSrcOpen((x) => savePref("ds.srcOpen", !x));
  const toggleLeft = () => setLeftOpen((x) => savePref("ds.leftOpen", !x));
  const toggleInsp = () => setInspOpen((x) => savePref("ds.inspOpen", !x));
  const toggleBin = () => setBinOpen((x) => savePref("ds.binOpen", !x));
  const total = data?.timeline.total_ms || 1;

  // ---- load ----
  const load = useCallback(async (v?: number) => {
    try {
      const d = await api.get<Data>(`/api/episodes/${eid}/timeline`);
      let tl = d.timeline;
      if (v) tl = await api.get<Timeline>(`/api/episodes/${eid}/timeline/versions/${v}`);
      setData(d); setClips(tl.clips); setV2(tl.v2 || []); setTracks(tl.tracks || {}); setApproved(tl.approved || []); setFlags(d.flags); setCounts(d.counts);
      setSubs(d.subs); savedSubs.current = d.subs; setSubsEdited(d.subs_edited); setMarkers(d.markers); setProxy(d.proxy);
      setProblems(d.problems); setHideProblems(false); setVersion(tl.version || 0); setSaveState(tl.draft ? "draft" : "saved");
      hist.current = { undo: [], redo: [] };
      if (v) dirtyN.current++;
    } catch (e: any) { setErr(e.message); }
  }, [eid]);
  useEffect(() => { load(); }, [load]);

  // ---- edits with undo ----
  const snap = (): Snap => ({ clips, approved, subs, v2 });
  const pushUndo = (s: Snap) => { hist.current.undo.push(s); hist.current.redo = []; if (hist.current.undo.length > 300) hist.current.undo.shift(); };
  const rowLocked = (track: 1 | 2) => {
    const l = track === 1 ? tracks.v1?.locked : tracks.v2?.locked;
    if (l) say(`V${track} is locked (🔒 on its row): unlock the row to change it`, true);
    return !!l;
  };
  const commit = useCallback((next: Clip[], nextApproved?: string[], nextV2?: Clip[]) => {
    if (next !== clips && rowLocked(1)) return;
    if (nextV2 && nextV2 !== v2 && rowLocked(2)) return;
    pushUndo({ clips, approved, subs, v2 });
    setClips(next); if (nextApproved) setApproved(nextApproved); if (nextV2) setV2(nextV2);
    dirtyN.current++; setSaveState("editing");
  }, [clips, approved, subs, v2, tracks]); // eslint-disable-line
  const setRow = (key: "v1" | "v2" | "src", what: "hidden" | "locked") => {
    const cur = (tracks as any)[key] || {}; const nx = { ...tracks, [key]: { ...cur, [what]: !cur[what] } };
    setTracks(nx); dirtyN.current++; setSaveState("editing");
    const name = key === "src" ? "The US row" : key.toUpperCase();
    say(what === "hidden" ? `${name} ${!cur.hidden ? "hidden" : "shown"}${key !== "src" ? (!cur.hidden ? " — it neither plays nor renders" : " — it plays and renders again") : ""}`
                          : `${name} ${!cur.locked ? "locked: nothing on it can change" : "unlocked"}`);
  };
  const commitV2 = (next: Clip[]) => commit(clips, undefined, next);
  const commitSubs = (lang: Lang, cues: Cue[]) => { pushUndo({ clips, approved, subs, v2 }); setSubs({ ...subs, [lang]: cues }); };
  const restore = (s: Snap) => { setClips(s.clips); setApproved(s.approved); setSubs(s.subs); setV2(s.v2 || []); dirtyN.current++; setSaveState("editing"); };
  const undo = () => { const s = hist.current.undo.pop(); if (!s) return; hist.current.redo.push(snap()); restore(s); };
  const redo = () => { const s = hist.current.redo.pop(); if (!s) return; hist.current.undo.push(snap()); restore(s); };

  // ---- subtitles: each changed track is saved (debounced) and burned from the edit at the next render ----
  useEffect(() => {
    const langs = (["dub", "en"] as Lang[]).filter((l) => subs[l] !== savedSubs.current[l]);
    if (!langs.length) return;
    const t = window.setTimeout(() => langs.forEach((l) => {
      const cues = subs[l];
      api.put(`/api/episodes/${eid}/subs/${l}`, { cues }).then(() => { savedSubs.current = { ...savedSubs.current, [l]: cues }; setSubsEdited((x) => ({ ...x, [l]: true })); })
        .catch((e) => say(`Subtitles not saved: ${e.message}`, true));
    }), 800);
    return () => clearTimeout(t);
  }, [subs]); // eslint-disable-line
  const revertSubs = async (lang: Lang) => {
    if (!confirm(`Throw away every change to the ${lang === "dub" ? "dub" : "English"} subtitles and go back to the pipeline's SRT?`)) return;
    const r = await api.del<{ subs: Subs; subs_edited: Record<Lang, boolean> }>(`/api/episodes/${eid}/subs/${lang}`);
    savedSubs.current = { ...savedSubs.current, [lang]: r.subs[lang] }; setSubs((x) => ({ ...x, [lang]: r.subs[lang] }));
    setSubsEdited(r.subs_edited); setSelCue(null); hist.current = { undo: [], redo: [] }; say("Subtitles back to the pipeline's SRT");
  };

  // subtitle style of a language: the show's setting over the pipeline's defaults
  const styleOf = (lang: Lang) => {
    const own = data?.sub_styles?.[lang] || {}; const style = (own.style || data?.sub_style || "box") as "box" | "strip";
    return { style, ...STYLE_DEFAULT[style], ...Object.fromEntries(Object.entries(own).filter(([, v]) => v != null)) } as Required<SubStyle>;
  };
  const saveStyle = async (lang: Lang, st: SubStyle) => {
    if (!data) return;
    try {
      const sh = await api.patch<any>(`/api/shows/${data.show.id}`, { profile: { sub_styles: { [lang]: st } } });
      setData({ ...data, sub_styles: sh.profile.sub_styles || {} }); say(`${lang === "dub" ? data.labels[0] : "EN"} subtitle style saved for ${data.show.code}: used from the next Render`);
    } catch (e: any) { say(e.message, true); }
  };
  const importSrt = async (lang: Lang, f: File) => {
    const cues = parseSrt(await f.text());
    if (!cues.length) { say("No subtitles found in that file", true); return; }
    commitSubs(lang, cues); setTrackDlg(null); say(`${cues.length} subtitles imported from ${f.name} (undo with ⌘Z)`);
  };
  const retranslate = async (k: number) => {
    const c = subs.en[k]; if (!c) return;
    const ov = subs.dub.filter((d) => Math.min(d.t1, c.t1) - Math.max(d.t0, c.t0) > 100);
    if (!ov.length) { say("No dub subtitle under this English one", true); return; }
    const i0 = subs.dub.indexOf(ov[0]), i1 = subs.dub.indexOf(ov[ov.length - 1]);
    try {
      const r = await api.post<{ text: string }>(`/api/episodes/${eid}/translate-line`, { text: ov.map((d) => d.text).join(" "),
        before: subs.dub[i0 - 1]?.text || "", after: subs.dub[i1 + 1]?.text || "" });
      setCue("en", k, { ...c, text: r.text }); say("Re-translated by the AI (undo with ⌘Z)");
    } catch (e: any) { say(e.message, true); }
  };

  // ---- QC markers: red flags until resolved ----
  const recheck = () => api.post<{ flags: Record<string, Flag[]>; counts: any; problems: string[] }>(`/api/episodes/${eid}/timeline/check`, { clips, v2, tracks, approved, total_ms: total })
    .then((r) => { setFlags(r.flags); setCounts(r.counts); setProblems(r.problems); }).catch(() => {});
  const reloadMarkers = () => api.get<Marker[]>(`/api/episodes/${eid}/markers`).then((m) => { setMarkers(m); recheck(); });
  const addMarker = async () => {
    const text = markerText.trim(); if (!text) return;
    try { const m = await api.post<Marker>(`/api/episodes/${eid}/markers`, { text, t_ms: Math.round(tRef.current) });
      setMarkerText(""); reloadMarkers(); say(`QC note at ${fmt(m.t_ms)}: ${m.text}`); }
    catch (e: any) { say(e.message, true); }
  };
  const patchMarker = (id: number, b: Partial<Marker>) => api.patch(`/api/markers/${id}`, b).then(reloadMarkers).catch((e) => say(e.message, true));
  const deleteMarker = (id: number) => api.del(`/api/markers/${id}`).then(reloadMarkers).catch((e) => say(e.message, true));
  const newMarkerAtPlayhead = () => { setLeftTab("markers"); setMarkerText(`${fmt(tRef.current)} `); window.setTimeout(() => markerInput.current?.focus(), 30); };

  // ---- the 540-px preview copy: made once, then the monitors and thumbnails use it ----
  useEffect(() => {
    if (!data || proxy.ready) return;
    if (!proxy.running) api.post<Proxy>(`/api/episodes/${eid}/proxy`).then(setProxy).catch(() => {});
    const iv = window.setInterval(() => api.get<Proxy>(`/api/episodes/${eid}/proxy`).then((p) => { setProxy(p); if (p.ready) clearInterval(iv); }).catch(() => {}), 2000);
    return () => clearInterval(iv);
  }, [data, proxy.ready]); // eslint-disable-line
  const videoUrl = `/api/episodes/${eid}/media/${proxy.ready ? "src_proxy" : "src_video"}`;

  // ---- flags (server rules) + autosave draft, debounced ----
  useEffect(() => {
    if (!data || !dirtyN.current) return;
    const body = { clips, v2, tracks, approved, total_ms: total };
    const t1 = window.setTimeout(() => {
      api.post<{ flags: Record<string, Flag[]>; counts: any; problems: string[] }>(`/api/episodes/${eid}/timeline/check`, body)
        .then((r) => { setFlags(r.flags); setCounts(r.counts); setProblems(r.problems); }).catch(() => {});
    }, 250);
    const t2 = window.setTimeout(() => {
      api.put(`/api/episodes/${eid}/timeline`, { ...body, draft: true, version }).then(() => setSaveState("draft")).catch(() => {});
    }, 1500);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [clips, approved, v2, tracks]); // eslint-disable-line

  const flat = useMemo(() => flatten(tracks.v1?.hidden ? [] : clips, tracks.v2?.hidden ? [] : v2, total), [clips, v2, total, tracks]);
  const byId = useMemo(() => new Map([...clips, ...v2].map((c) => [c.id, c] as [string, Clip])), [clips, v2]);
  // a cut = a visible piece that starts its clip where the picture jumps in the source (a transition can go there)
  const cutIds = useMemo(() => {
    const out = new Set<string>();
    flat.forEach((q, k) => { const pr = flat[k - 1]; if (k && !q.gap && pr && !pr.gap && Math.abs(q.s0! - pr.s1!) > 50 && Math.abs((byId.get(q.id!)?.t0 ?? -1) - q.t0) < 1) out.add(q.id!); });
    return out;
  }, [flat, byId]);
  // any clip after the first piece can take a transition from the inspector (Automatic still lets you pick a style)
  const transInto = useMemo(() => {
    const out = new Set<string>();
    let seen = false;
    for (const q of flat) {
      if (q.gap || !q.id) continue;
      if (seen) out.add(q.id);
      seen = true;
    }
    return out;
  }, [flat]);
  const selIdx = useMemo(() => clips.findIndex((c) => c.id === sel), [clips, sel]);
  const selV2 = useMemo(() => v2.findIndex((c) => c.id === sel), [v2, sel]);
  const selClip = selIdx >= 0 ? clips[selIdx] : selV2 >= 0 ? v2[selV2] : null;
  // locked clips refuse every edit until unlocked (⇧L)
  const locked = (...idx: number[]) => {
    const c = idx.map((i) => clips[i]).find((x) => x?.locked);
    if (c) say(`${label(c)} is locked: unlock it first (⇧L)`, true);
    return !!c;
  };
  const snapPts = useMemo(() => {
    const pts = clips.map((c) => c.t0).concat([total], data?.dub_starts || [], markers.map((m) => m.t_ms));
    return pts;
  }, [clips, data, markers, total]); // eslint-disable-line

  // ---- playback: dub audio = master clock, two video players swap at cuts ----
  const audio = useRef<HTMLAudioElement>(null);
  const vids = [useRef<HTMLVideoElement>(null), useRef<HTMLVideoElement>(null)];
  const impV = useRef<HTMLVideoElement>(null);         // a third player for imported clips (their own files)
  // how far the visible picture is behind the voice (s; + = behind) and whether its player is stuck loading
  const lag = useRef({ s: 0, stalled: false });
  const holding = useRef(false);                       // the voice is held until the picture catches up
  // how far ahead each player must run so the frame it SHOWS matches the voice: some browsers show a frame later
  // than the player reports (measured ~170 ms in a slow pane); learnt from the frames actually displayed
  const lead = useRef(new WeakMap<HTMLVideoElement, number>());
  const leadOf = (v: HTMLVideoElement) => lead.current.get(v) || 0;
  const [held, setHeld] = useState(false);
  // processing elsewhere (a lip scan, Whisper, a render) slows the preview: say so while it runs
  const [busyNow, setBusyNow] = useState<{ step: string; episode: string }[]>([]);
  useEffect(() => {
    const get = () => api.get<{ running: { step: string; episode: string }[] }>("/api/busy").then((r) => setBusyNow(r.running)).catch(() => {});
    get(); const iv = window.setInterval(get, 5000); return () => clearInterval(iv);
  }, []);
  const act = useRef(0);                 // which player is visible
  const vClip = useRef<string[]>(["", ""]);   // clip id each player is parked on
  const lastFix = useRef(0);
  const swapAt = useRef(0);              // last clip change: seeking here is normal, do not pause the voice
  const playheadEl = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const clipsRef = useRef(clips); clipsRef.current = clips;
  const flatRef = useRef<Piece[]>([]); flatRef.current = flat;
  const byIdRef = useRef(byId); byIdRef.current = byId;
  const transRef = useRef<TransInfo | null>(null); transRef.current = data?.transitions || null;
  const fxRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef(zoom); zoomRef.current = zoom;

  const placePlayhead = (t: number) => {
    if (playheadEl.current) playheadEl.current.style.transform = `translateX(${(t / 1000) * zoomRef.current}px)`;
  };
  const showFrame = useCallback((t: number, play: boolean) => {
    const ps = flatRef.current; if (!ps.length) return;
    const i = findClip(ps, t); const c = ps[i];
    let a = act.current; const A = vids[a].current, B = vids[1 - a].current; if (!A || !B) return;
    const nextReal = ps.slice(i + 1).find((q) => !q.gap && !q.media);
    const I = impV.current;
    if ((c.gap || !c.media) && I && !I.classList.contains("hidden")) { I.classList.add("hidden"); I.pause(); }
    if (!c.gap && c.media && I) {                     // an imported clip: played from its own file
      A.classList.add("hidden"); B.classList.add("hidden"); if (!A.paused) A.pause(); if (!B.paused) B.pause();
      if (fxRef.current) fxRef.current.style.opacity = "0";
      const url = `/api/episodes/${eid}/imports/${c.media}/media`;
      if (I.dataset.m !== c.media) { I.dataset.m = c.media; I.src = url; return; }   // onLoadedData shows the frame
      I.classList.remove("hidden");
      paintMotion(I, byIdRef.current.get(c.id!));
      const sp = (c.s1! - c.s0!) / Math.max(c.t1 - c.t0, 1); const want = (c.s0! + (t - c.t0) * sp) / 1000 + (play ? leadOf(I) : 0);
      const base = clamp(sp, 0.0625, 16); let rate = base;
      if (play) {
        if (I.paused) I.play().catch(() => {});
        const err = I.currentTime - want, now = performance.now();
        if (Math.abs(err) > 0.8 && now - lastFix.current > 500 && !I.seeking) { I.currentTime = want; lastFix.current = now; }
        else if (Math.abs(err) > 0.015) rate = clamp(base * (1 - clamp(err * 2, -0.25, 0.25)), 0.0625, 16);
      } else { if (!I.paused) I.pause(); if (Math.abs(I.currentTime - want) > 0.02) I.currentTime = want; }
      if (Math.abs(I.playbackRate - rate) > 0.001) I.playbackRate = rate;
      // HAVE_CURRENT_DATA (2) is enough to show a frame; R2 often sits here while playing
      lag.current = { s: want - I.currentTime, stalled: I.seeking || I.readyState < 2 };
      if (nextReal && !I.seeking) { const k = `${nextReal.id}@${Math.round(nextReal.t0)}`; if (vClip.current[a] !== k) { vClip.current[a] = k; A.currentTime = nextReal.s0! / 1000; } }
      return;
    }
    if (c.gap) {                                       // nothing on V1 or V2 here: black (it would render black)
      lag.current = { s: 0, stalled: false };
      paintMotion(A, undefined); paintMotion(B, undefined);
      A.classList.add("hidden"); B.classList.add("hidden"); if (!A.paused) A.pause(); if (!B.paused) B.pause();
      if (fxRef.current) fxRef.current.style.opacity = "0";
      if (nextReal) { const k = `${nextReal.id}@${Math.round(nextReal.t0)}`; if (vClip.current[a] !== k) { vClip.current[a] = k; A.currentTime = nextReal.s0! / 1000; } }
      return;
    }
    const key = `${c.id}@${Math.round(c.t0)}`; const sp = (c.s1! - c.s0!) / Math.max(c.t1 - c.t0, 1);
    const want = (c.s0! + (t - c.t0) * sp) / 1000 + (play ? leadOf(vids[act.current].current!) : 0);
    if (vClip.current[a] !== key) {
      if (vClip.current[1 - a] === key) { a = 1 - a; act.current = a; }    // the standby player is already here
      vClip.current[a] = key;
      swapAt.current = performance.now();
      const V = vids[a].current!; if (Math.abs(V.currentTime - want) > 0.05) V.currentTime = want;
    }
    const V = vids[a].current!, S = vids[1 - a].current!;
    V.classList.remove("hidden"); S.classList.add("hidden");
    // drift correction (design: "Preview without rendering"): a small speed nudge pulls the picture back within a
    // frame without a visible jump; only a big miss (a stall, a seek) jumps straight to the right frame
    const base = clamp(sp, 0.0625, 16);
    let rate = base;
    if (play) {
      if (V.paused) V.play().catch(() => {});
      const err = V.currentTime - want, now = performance.now();       // + = picture ahead of the voice
      // a hard seek every 250 ms keeps the player in "seeking" and pauses the voice on a cutty stretch (TAB EP22)
      if (Math.abs(err) > 0.8 && now - lastFix.current > 500 && !V.seeking) { V.currentTime = want; lastFix.current = now; }
      else if (Math.abs(err) > 0.015) rate = clamp(base * (1 - clamp(err * 2, -0.25, 0.25)), 0.0625, 16);
    }
    if (Math.abs(V.playbackRate - rate) > 0.001) V.playbackRate = rate;
    // HAVE_CURRENT_DATA (2) is enough to show a frame; R2 often sits here while playing
    lag.current = { s: want - V.currentTime, stalled: V.seeking || V.readyState < 2 };
    if (!play) {
      if (!V.paused) V.pause();
      if (Math.abs(V.currentTime - want) > 0.02) V.currentTime = want;
    }
    // rough preview of a transition at a cut (the real one is made at render)
    const ti = transRef.current; let fx: { st: string; x: number } | null = null;
    if (ti) for (const k of [i, i + 1]) {
      const q = ps[k], pr = ps[k - 1];
      if (!q || !pr || q.gap || pr.gap || Math.abs(q.s0! - pr.s1!) <= 50) continue;
      const cl = byIdRef.current.get(q.id!); if (!cl || Math.abs(cl.t0 - q.t0) >= 1) continue;
      const st = cl.tin || ti.default; if (st === "none") continue;
      const h = (ti.styles.find((x) => x.id === st)?.half_s || 0.3) * 1000; const d = t - q.t0;
      if (Math.abs(d) < h) fx = { st, x: d / h };
    }
    const w = fx ? (1 - Math.abs(fx.x)) ** 2 * (3 - 2 * (1 - Math.abs(fx.x))) : 0;
    const filt = !fx ? "" : fx.st === "zoom_blur" ? `blur(${2 * w}px)` : fx.st === "glow" ? `brightness(${1 + 0.6 * w}) blur(${3 * w}px)`
      : fx.st === "light_leak" ? `brightness(${1 + 0.25 * w})` : `blur(${7 * w}px)`;
    V.style.filter = filt;
    paintMotion(V, byIdRef.current.get(c.id!), fx && fx.st === "zoom_blur" ? 1 + 0.15 * w : 1);
    paintMotion(S, undefined);
    if (fxRef.current) {
      fxRef.current.style.opacity = fx && (fx.st === "light_leak" || fx.st === "glow" || fx.st === "auto") ? String(0.85 * w) : "0";
      fxRef.current.style.backgroundPosition = fx ? `${50 + 60 * fx.x}% 35%` : "";
      fxRef.current.dataset.label = fx ? TLABEL[fx.st] || fx.st : "";
    }
    // park the standby player on the next piece's first frame (not while this one is still seeking:
    // two R2 range requests at once make the visible cut wait)
    if (nextReal && !V.seeking) { const k = `${nextReal.id}@${Math.round(nextReal.t0)}`; if (vClip.current[1 - a] !== k) { vClip.current[1 - a] = k; S.pause(); S.currentTime = nextReal.s0! / 1000; } }
  }, []); // eslint-disable-line

  // learn each player's lead from the frames it really displays (requestVideoFrameCallback): the shown frame is
  // compared with the voice's time at the moment of display; the lead moves a third of the way each frame
  useEffect(() => {
    if (!data) return;
    const players = [vids[0].current, vids[1].current, impV.current].filter(Boolean) as HTMLVideoElement[];
    let alive = true;
    for (const v of players) {
      if (!("requestVideoFrameCallback" in v)) continue;
      let last = 0;
      const cb = (now: number, meta: any) => {
        if (!alive) return;
        const A = audio.current; const smooth = now - last < 120; last = now;   // only learn from smooth display
        if (A && !A.paused && smooth && !holding.current && !v.classList.contains("hidden")) {
          const at = A.currentTime * 1000 + (meta.expectedDisplayTime - now);
          const ps = flatRef.current; const q = ps[findClip(ps, at)];
          if (q && !q.gap && at - q.t0 > 150 && q.t1 - at > 150) {
            const sp = (q.s1! - q.s0!) / Math.max(q.t1 - q.t0, 1);
            const err = meta.mediaTime - (q.s0! + (at - q.t0) * sp) / 1000;      // + = shown frame ahead of the voice
            if (Math.abs(err) < 0.3) lead.current.set(v, clamp(leadOf(v) - err / 4, 0, 0.25));
          }
        }
        (v as any).requestVideoFrameCallback(cb);
      };
      (v as any).requestVideoFrameCallback(cb);
    }
    return () => { alive = false; };
  }, [data]); // eslint-disable-line

  const seek = useCallback((t: number) => {
    t = clamp(t, 0, total - 1); tRef.current = t; setTShow(t); placePlayhead(t);
    if (audio.current) audio.current.currentTime = t / 1000;
    showFrame(t, playing);
  }, [total, playing, showFrame]);

  // the play loop. The dub audio is the clock; picture + subtitles follow it. Screen redraws (requestAnimationFrame)
  // stop or slow down when the window is not in front or the Mac is busy (a lip scan, Whisper, a render), so a timer
  // drives the same step too. A cut jumps the source file (TAB EP22 has jumps of several minutes). On R2 that seek
  // often takes longer than a local file, so lag looks huge until it lands — that is not a hang; the voice keeps
  // going. Only a real empty buffer (no frame for several seconds) pauses it ("picture catching up").
  useEffect(() => {
    if (!playing) return;
    let raf = 0, lastUi = 0, lastStep = 0, holdSince = 0, stallSince = 0; let alive = true;
    const step = () => {
      const A = audio.current; if (!A || !alive) return;
      const now = performance.now(); if (now - lastStep < 12) return; lastStep = now;
      const t = A.currentTime * 1000; tRef.current = t; placePlayhead(t);
      if (holding.current) {
        showFrame(t, false);                           // park the picture on the voice's frame, paused
        const ok = !lag.current.stalled;
        if (ok || now - holdSince > 1500) {            // a frame is back (or give up holding)
          holding.current = false; setHeld(false); A.play().catch(() => {});
        }
      } else {
        showFrame(t, true);
        if (lag.current.stalled) swapAt.current = Math.max(swapAt.current, now - 200); // still seeking this cut
        const swapping = now - swapAt.current < 3000;
        stallSince = !swapping && lag.current.stalled ? stallSince || now : 0;
        // lag.s is the source-time miss: a 400 s jump reads as 400 s of "lag" until the seek finishes. Ignore it.
        if (!swapping && stallSince && now - stallSince > 5000 && !A.paused && !document.hidden) {
          holding.current = true; holdSince = now; stallSince = 0; setHeld(true); A.pause();
        }
      }
      if (now - lastUi > 100) {
        lastUi = now; setTShow(t);                     // subtitles + time follow at ~10 per second
        const sc = scroller.current;                   // keep the playhead in view
        if (sc) { const x = (t / 1000) * zoomRef.current; if (x > sc.scrollLeft + sc.clientWidth - 80 || x < sc.scrollLeft) sc.scrollLeft = x - 120; }
      }
      if ((A.ended || t >= total - 5) && !holding.current) {
        const end = Math.min(t, total - 1);
        tRef.current = end; setTShow(end); placePlayhead(end);
        if (!A.paused) A.pause();
        vids.forEach((v) => v.current?.pause());
        impV.current?.pause();
        showFrame(end, false);
        setPlaying(false);
      }
    };
    const frame = () => { step(); raf = requestAnimationFrame(frame); };
    raf = requestAnimationFrame(frame);
    const iv = window.setInterval(step, 40);           // keeps going when screen redraws stall
    const vis = () => { lastStep = 0; step(); };       // back in front: line everything up at once
    document.addEventListener("visibilitychange", vis);
    return () => { alive = false; cancelAnimationFrame(raf); clearInterval(iv); document.removeEventListener("visibilitychange", vis);
      if (holding.current) { holding.current = false; setHeld(false); } };
  }, [playing, showFrame, total]);

  const togglePlay = () => {
    const A = audio.current; if (!A) return;
    if (playing) { holding.current = false; setHeld(false); A.pause(); vids.forEach((v) => v.current?.pause()); impV.current?.pause(); setPlaying(false); showFrame(tRef.current, false); }
    else { A.currentTime = tRef.current / 1000; A.play().then(() => setPlaying(true)).catch((e) => say(`Cannot play: ${e.message}`, true)); }
  };
  useEffect(() => { if (data) setTimeout(() => showFrame(tRef.current, false), 300); }, [data]); // eslint-disable-line
  useEffect(() => {
    if (playing) return;
    vids.forEach((v) => v.current?.pause());
    impV.current?.pause();
    showFrame(tRef.current, false);
  }, [playing, clips, v2]); // eslint-disable-line

  // ---- source monitor ----
  const srcVid = useRef<HTMLVideoElement>(null);
  const seekSrc = (ms: number) => { const v = srcVid.current; if (v) { v.currentTime = Math.max(0, ms / 1000); setSrcT(ms); } };
  useEffect(() => { if (selClip) { setSrcMedia(selClip.media || null); seekSrc(selClip.s0); setMarkIn(selClip.s0); setMarkOut(selClip.s1); } }, [sel]); // eslint-disable-line
  const showImport = (x: Imp) => { setSrcMedia(x.id); setSrcT(0); seekSrc(0); setMarkIn(0); setMarkOut(x.duration_ms); if (!srcOpen) toggleSrc();
    say(`${x.name} on the source monitor (In/Out = the whole clip): drag it from the bin onto V1, a gap or V2, or mark In/Out and press Replace / ▲ V2`); };
  const importFiles = async (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      say(`Importing ${f.name}…`);
      try { const fd = new FormData(); fd.append("file", f);
        const r = await fetch(`/api/episodes/${eid}/imports`, { method: "POST", body: fd });
        const j = await r.json(); if (!r.ok) throw new Error(j.detail || r.statusText);
        say(`${f.name} imported (${fmt(j.duration_ms)}): drag it onto the timeline`); }
      catch (e: any) { say(`${f.name}: ${e.message}`, true); }
    }
    loadBin();
  };
  // a generated clip goes on V2 exactly over the clip's slot (V2 clips already there are replaced; V1 stays under)
  const putGenOnV2 = async (importId: string, c: GenClip) => {
    let it = impOf(importId);
    if (!it) { const b = await api.get<{ imports: Imp[]; files: BinFile[] }>(`/api/episodes/${eid}/bin`); setBin(b); binRef.current = b; it = b.imports.find((x) => x.id === importId); }
    if (!it) { say("The generated clip is not in the bin any more", true); return; }
    if (rowLocked(2)) return;
    const keep = v2.filter((x) => x.t1 <= c.t0 + 1 || x.t0 >= c.t1 - 1);
    if (v2.some((x) => x.locked && !keep.includes(x))) { say("A locked V2 clip is in the way: unlock it first", true); return; }
    const slot = c.t1 - c.t0; const s1 = Math.min(it.duration_ms, slot);
    const nc: Clip = { id: uid(), row: c.row, t0: c.t0, t1: c.t0 + s1, s0: 0, s1, dub_text: c.dub_text, src_text: it.name, media: it.id };
    commitV2([...keep, nc].sort((a, b) => a.t0 - b.t0)); setSel(nc.id);
    say(`${it.name} is on V2 over ${c.label} (${fmt(c.t0)}–${fmt(c.t0 + s1)}, ×1.00)${keep.length < v2.length ? "; the V2 clip that was there is replaced" : ""}`);
  };
  const removeImport = async (x: Imp) => {
    if ([...clips, ...v2].some((c) => c.media === x.id)) { say(`${x.name} is on the timeline: take it off first`, true); return; }
    if (!confirm(`Remove ${x.name} from this episode's bin? (The file you imported it from is not touched.)`)) return;
    try { await api.del(`/api/episodes/${eid}/imports/${x.id}`); if (srcMedia === x.id) setSrcMedia(null); loadBin(); } catch (e: any) { say(e.message, true); }
  };

  // ---- operations ----
  const update = (i: number, c: Clip) => {
    if (locked(i)) return;
    const prev = clips[i]; const out = clips.slice();
    if (data && !c.media && (Math.abs(prev.s0 - c.s0) > 1 || Math.abs(prev.s1 - c.s1) > 1)) c = { ...c, src_text: srcTextFor(c.s0, c.s1, data.src_sentences) || c.src_text };
    out[i] = c; commit(out);
  };
  // the selected clip may be on V1 or V2
  const updateV2 = (id: string, c: Clip) => {
    const k = v2.findIndex((x) => x.id === id); if (k < 0) return;
    if (v2[k].locked) { say(`${label(v2[k])} is locked: unlock it first (⇧L)`, true); return; }
    if (data && !c.media && (Math.abs(v2[k].s0 - c.s0) > 1 || Math.abs(v2[k].s1 - c.s1) > 1)) c = { ...c, src_text: srcTextFor(c.s0, c.s1, data.src_sentences) || c.src_text };
    const out = v2.slice(); out[k] = c; commitV2(out);
  };
  const updateSel = (c: Clip) => { if (selV2 >= 0) updateV2(c.id, c); else if (selIdx >= 0) update(selIdx, c); };
  const motionBase = useRef<Snap | null>(null);
  const setSelMotion = (c: Clip, done: boolean) => {
    const onV2 = selV2 >= 0; const i = onV2 ? selV2 : selIdx; if (i < 0) return;
    const list = onV2 ? v2 : clips; if (list[i]?.locked) return;
    if (!motionBase.current) motionBase.current = snap();
    const out = list.slice(); out[i] = c;
    if (onV2) setV2(out); else setClips(out);
    dirtyN.current++; setSaveState("editing");
    if (done && motionBase.current) { pushUndo(motionBase.current); motionBase.current = null; }
  };
  const doRazor = (t = tRef.current) => {
    const k = v2.findIndex((x) => x.t0 < t && t < x.t1);            // a V2 clip at the playhead is cut first (it is on top)
    if (k >= 0) { if (v2[k].locked) { locked(-1); say(`${label(v2[k])} is locked`, true); return; } const out = razor(v2, k, t); if (out === v2) say("Too close to an edge to split here"); else { commitV2(out); setSel(out[k + 1].id); } return; }
    const i = clips.findIndex((x) => x.t0 < t && t < x.t1); if (i < 0) { say("No clip at the playhead", true); return; }
    if (locked(i)) return; const out = razor(clips, i, t); if (out === clips) say("Too close to a cut to split here"); else { commit(out); setSel(out[i + 1].id); }
  };
  // Delete = lift (a real editor's delete): the clip goes, its place stays empty (a gap to fill). ⇧Delete = the clip
  // before takes its place (the old way). A V2 clip just goes: V1 shows again.
  const doDelete = (fill = false) => {
    if (selV2 >= 0) { if (v2[selV2].locked) { say(`${label(v2[selV2])} is locked`, true); return; } const c = v2[selV2]; commitV2(v2.filter((_, k) => k !== selV2)); setSel(null); say(`${label(c)} removed from V2; V1 shows again`); return; }
    if (selIdx < 0 || locked(selIdx)) return; const c = clips[selIdx];
    if (fill) { if (locked(selIdx > 0 ? selIdx - 1 : 1)) return; const out = del(clips, selIdx); commit(out); setSel(out[Math.max(0, selIdx - 1)]?.id || null); say(`${label(c)} removed; the clip before fills its place`); return; }
    commit(clips.filter((_, k) => k !== selIdx)); setSel(null);
    say(`${label(c)} removed: its place ${fmt(c.t0)}–${fmt(c.t1)} is a gap now (drop a sentence on it, or click it to fill)`);
  };
  // media: the file the footage is in (an imported clip's id; null = the source video); marks follow the source monitor
  const withMedia = (c: Clip, a: number, b: number, md: string | null): Clip => {
    const o: Clip = { ...c, s0: a, s1: b, src_text: md ? impOf(md)?.name || "imported clip" : srcTextFor(a, b, data!.src_sentences) || (c.media ? "" : c.src_text) };
    if (md) o.media = md; else delete o.media;
    delete o.broll; delete o.zoom; delete o.pan_x; delete o.pan_y;  // new footage: no longer the scene edit's B-roll / zoomed repeat
    return o;
  };
  const doReplace = (s0?: number, s1?: number, why = "marked source", i = selIdx, media?: string | null) => {
    const onV2 = i === selIdx && selIdx < 0 && selV2 >= 0;
    if (i < 0 && !onV2) { say("Select a clip first", true); return; }
    const a = s0 ?? markIn, b = s1 ?? markOut;
    const md = media !== undefined ? media : s0 == null ? srcMedia : null;
    if (a == null || b == null || b - a < 100) { say("Mark In (I) and Out (O) on the source monitor first", true); return; }
    if (onV2) { const c = v2[selV2]; updateV2(c.id, withMedia(c, a, b, md)); say(`${label(c)} (V2) now shows ${fmt(a)}–${fmt(b)} (${why})`); return; }
    const c = clips[i]; if (locked(i)) return;
    update(i, withMedia(c, a, b, md));
    say(`${label(c)} now shows ${fmt(a)}–${fmt(b)} (${why}), played at ×${((b - a) / (c.t1 - c.t0)).toFixed(2)}`);
  };
  // a source sentence dropped on a clip (mapping panel or timeline): that clip shows the sentence, fitted to its slot
  const dropSentence = (i: number, v: string | number) => {
    const f = footOf(v); if (!f || i < 0) return;
    const slot = clips[i].t1 - clips[i].t0;                // an imported clip plays at its own speed (from its start)
    setSel(clips[i].id); doReplace(f.s0, f.media ? Math.min(f.s1, f.s0 + slot) : f.s1, f.why, i, f.media ?? null);
  };
  const toggleApprove = (id = sel) => {
    if (!id) return;
    commit(clips, approved.includes(id) ? approved.filter((x) => x !== id) : [...approved, id]);
  };
  const setTransition = (id: string, st: string | null) => {
    const onV2 = v2.some((c) => c.id === id); const list = onV2 ? v2 : clips;
    const i = list.findIndex((c) => c.id === id); if (i < 0) return;
    const out = list.slice(); const c = { ...list[i] };
    if (st == null) delete c.tin; else c.tin = st;
    out[i] = c; if (onV2) commitV2(out); else commit(out);
    say(`${label(c)}: ${st == null ? `show default (${TLABEL[data!.transitions.default]})` : TLABEL[st] || st} at the cut ${fmt(c.t0)}`);
  };
  const setTransMode = async (auto: boolean) => {
    if (!data) return;
    try {
      await api.patch(`/api/shows/${data.show.id}`, { profile: { transitions: auto } });
      setData({ ...data, transitions: { ...data.transitions, default: auto ? "auto" : "none" } });
      const own = [...clips, ...v2].filter((c) => c.tin && cutIds.has(c.id)).length;
      say(auto ? `Automatic: every cut gets a transition at Render${own ? ` (your ${own} picks win)` : ""} — for all of ${data.show.code}`
               : `Manual: only the cuts you give a transition get one${own ? ` (${own} so far)` : " (none yet: click a cut's ✦ / │ handle)"} — for all of ${data.show.code}`);
    } catch (e: any) { say(e.message, true); }
  };
  const toggleLock = (id = sel) => {
    const onV2 = v2.some((c) => c.id === id); const list = onV2 ? v2 : clips;
    const i = list.findIndex((c) => c.id === id); if (i < 0) return;
    const out = list.slice(); out[i] = { ...list[i], locked: !list[i].locked }; if (onV2) commitV2(out); else commit(out);
    say(out[i].locked ? `${label(out[i])} locked` : `${label(out[i])} unlocked`);
  };
  const lockApproved = () => {
    const n = clips.filter((c) => approved.includes(c.id) && !c.locked).length;
    if (!n) { say("Every approved clip is locked already"); return; }
    commit(clips.map((c) => (approved.includes(c.id) ? { ...c, locked: true } : c))); say(`${n} approved clips locked`);
  };
  // copy: source In→Out (⌘C on the source) or a V1/V2 clip. ⌘V: a copied timeline clip goes on V2 at the playhead
  // (same as ▲ V2 for In/Out); a source copy still pastes onto the selected clip, a gap, or V2.
  const copySrc = () => {
    if (markIn == null || markOut == null || markOut - markIn < 100) { say("Set the clip's start and end first (type them, or I / O on the source)", true); return; }
    const name = srcMedia ? impOf(srcMedia)?.name || "imported clip" : S;
    const text = srcMedia ? name : srcTextFor(markIn, markOut, data!.src_sentences);
    setClipboard({ s0: markIn, s1: markOut, src_text: text, from: `${name} ${fmt(markIn)}–${fmt(markOut)}`, speed: 1, media: srcMedia || undefined, fromSource: true });
    try { navigator.clipboard?.writeText(`${name} ${fmt(markIn)}–${fmt(markOut)} (Mirror Video clip)`).catch(() => {}); } catch { /* no clipboard access */ }
    say(`Copied ${name} ${fmt(markIn)}–${fmt(markOut)} (${((markOut - markIn) / 1000).toFixed(2)} s): ${modKey}V pastes it onto the selected clip, into a gap, or on V2 at the playhead`);
  };
  const copyClip = () => {
    if (!selClip) return;
    setClipboard({ s0: selClip.s0, s1: selClip.s1, src_text: selClip.src_text, from: label(selClip), speed: speedOf(selClip), media: selClip.media, fromId: selClip.id });
    say(`Copied ${label(selClip)} (${fmt(selClip.s0)}–${fmt(selClip.s1)}): ${modKey}V pastes it on V2 at the playhead`);
  };
  const pasteClip = (keepSpeed = false) => {
    if (!clipboard) { say("Copy a clip first (⌘C)", true); return; }
    if (!selClip) { say("Select the clip to paste onto", true); return; }
    const c = selClip; let { s0, s1 } = clipboard;
    if (keepSpeed) s1 = Math.min(s0 + (c.t1 - c.t0) * clipboard.speed, durOf(clipboard.media));
    doReplace(s0, s1, `pasted from ${clipboard.from}`, selIdx, clipboard.media ?? null);
  };
  const pasteOnV2 = () => {
    if (!clipboard) { say("Copy a clip first (⌘C)", true); return; }
    if (tracks.v2?.locked) { rowLocked(2); return; }
    const slot = (clipboard.s1 - clipboard.s0) / Math.max(clipboard.speed, 0.05);
    addV2(tRef.current, clipboard.s0, clipboard.s1, clipboard.src_text, `pasted ${clipboard.from}`, undefined, slot, clipboard.media);
  };
  const resetClip = () => {
    if (!selClip?.orig) return; const o = selClip.orig;
    const slot = selClip.t1 - selClip.t0; const r = (o.s1 - o.s0) / (o.t1 - o.t0);
    const same = Math.abs(slot - (o.t1 - o.t0)) < 1;
    updateSel({ ...selClip, s0: o.s0, s1: same ? o.s1 : o.s0 + slot * r });
  };
  // ---- trims, gaps, V2 ----
  const lockedChange = (before: Clip[], after: Clip[]) => {            // a locked clip may not be trimmed or overwritten
    const a = new Map(after.map((c) => [c.id, c]));
    const hit = before.find((c) => c.locked && (!a.has(c.id) || ["t0", "t1", "s0", "s1"].some((k) => Math.abs((a.get(c.id) as any)[k] - (c as any)[k]) > 0.5)));
    if (hit) say(`${label(hit)} is locked: unlock it first (⇧L)`, true);
    return !!hit;
  };
  const onTrim = (track: 1 | 2, i: number, side: "l" | "r", t: number, done: boolean, base: Snap) => {
    if ((track === 1 ? tracks.v1?.locked : tracks.v2?.locked)) { if (done) rowLocked(track); return; }
    const list = track === 1 ? base.clips : base.v2; const out = trimEdge(list, i, side, t, total, durOf(list[i]?.media));
    if (lockedChange(list, out)) { restore(base); return; }
    if (track === 1) setClips(out); else setV2(out);
    if (done) { pushUndo(base); dirtyN.current++; setSaveState("editing"); }
  };
  const fillGap = (g: { t0: number; t1: number }, how: "before" | "after") => {
    const i = how === "before" ? clips.findIndex((c) => Math.abs(c.t1 - g.t0) < 1) : clips.findIndex((c) => Math.abs(c.t0 - g.t1) < 1);
    if (i < 0) { say(how === "before" ? "No clip right before this gap on V1" : "No clip right after this gap on V1", true); return; }
    if (locked(i)) return;
    const out = trimEdge(clips, i, how === "before" ? "r" : "l", how === "before" ? g.t1 : g.t0, total, durOf(clips[i].media));
    const c = out.find((x) => x.id === clips[i].id)!;
    if (how === "before" ? c.t1 < g.t1 - 1 : c.t0 > g.t0 + 1) say("Its footage runs out before the end of the gap: some black is left", true);
    commit(out); setSel(c.id); say(`${label(c)} extended over the gap (more of its own footage, same speed)`);
  };
  // footage into a gap: from s0 at `speed` (a dragged clip's, or the copied clip's), as long as the gap
  const footageToGap = (g: { t0: number; t1: number }, s0: number, speed: number, src_text: string, why: string, media?: string) => {
    let t1 = g.t1; const sp = Math.max(speed, 0.05); const end = durOf(media);
    if (s0 + (t1 - g.t0) * sp > end) t1 = g.t0 + (end - s0) / sp;                 // the footage ends first
    if (t1 - g.t0 < MIN_SLOT) { say("Not enough footage left after that point for this gap", true); return; }
    const c: Clip = { id: uid(), row: null, t0: g.t0, t1, s0, s1: s0 + (t1 - g.t0) * sp, dub_text: "", src_text, ...(media ? { media } : {}) };
    commit([...clips, c].sort((a, b) => a.t0 - b.t0)); setSel(c.id);
    say(`Gap ${fmt(g.t0)}–${fmt(g.t1)} filled with ${why} (×${sp.toFixed(2)})${t1 < g.t1 - 1 ? " — the footage ran out, a bit of gap is left" : ""}`);
  };
  const clipToGap = (c: Clip, g: { t0: number; t1: number }) => footageToGap(g, c.s0, speedOf(c), c.src_text, `a copy of ${label(c)}`, c.media);
  const pasteToGap = (g: { t0: number; t1: number }) => {
    if (!clipboard) { say("Copy a clip first (select it, ⌘C)", true); return; }
    footageToGap(g, clipboard.s0, clipboard.speed, clipboard.src_text, `the copied ${clipboard.from}`, clipboard.media);
  };
  const dropOnGap = (g: { t0: number; t1: number }, v: string | number) => {
    const f = footOf(v); if (!f) return;
    if (f.media) { footageToGap(g, 0, 1, f.text, f.why, f.media); return; }      // an imported clip: its own speed
    const c: Clip = { id: uid(), row: null, t0: g.t0, t1: g.t1, s0: f.s0, s1: f.s1, dub_text: "", src_text: f.text };
    commit([...clips, c].sort((a, b) => a.t0 - b.t0)); setSel(c.id);
    say(`New clip in the gap: ${f.why}, played at ×${speedOf(c).toFixed(2)}`);
  };
  // put footage on V2 at time t (from a sentence, the marked In/Out, or a copy of a V1 clip); it never covers another V2 clip
  const addV2 = (t: number, s0: number, s1: number, src_text: string, why: string, tin?: string, slot?: number, media?: string | null) => {
    t = clamp(t, 0, total - MIN_SLOT);
    if (v2.some((c) => c.t0 <= t && t < c.t1)) { say("There is a V2 clip here already: put it on an empty part of V2", true); return; }
    const next = v2.filter((c) => c.t0 > t).sort((a, b) => a.t0 - b.t0)[0];
    const want = slot ?? s1 - s0; const sp = (s1 - s0) / Math.max(want, 1);       // a copy keeps its length and speed
    const t1 = Math.min(t + want, next ? next.t0 : total, total);
    if (t1 - t < MIN_SLOT) { say("Not enough room on V2 here", true); return; }
    const c: Clip = { id: uid(), row: null, t0: t, t1, s0, s1: s0 + (t1 - t) * sp, dub_text: "", src_text, ...(tin ? { tin } : {}), ...(media ? { media } : {}) };
    commitV2([...v2, c].sort((a, b) => a.t0 - b.t0)); setSel(c.id);
    say(`On V2 at ${fmt(t)}: ${why}${t1 - t < want - 1 ? " (cut short to fit)" : ""}`);
  };
  const dropOnV2 = (t: number, v: string | number) => { const f = footOf(v); if (f) addV2(t, f.s0, f.s1, f.text, f.why, undefined, undefined, f.media); };
  const markToV2 = () => {
    if (markIn == null || markOut == null || markOut - markIn < 100) { say("Mark In (I) and Out (O) on the source monitor first", true); return; }
    addV2(tRef.current, markIn, markOut, srcMedia ? impOf(srcMedia)?.name || "imported clip" : srcTextFor(markIn, markOut, data!.src_sentences),
      `marked ${fmt(markIn)}–${fmt(markOut)}${srcMedia ? ` of ${impOf(srcMedia)?.name}` : ""}`, undefined, undefined, srcMedia);
  };
  const copyToV2 = (c: Clip, t = c.t0) => addV2(t, c.s0, c.s1, c.src_text, `a copy of ${label(c)} (same length and speed)`, undefined, c.t1 - c.t0, c.media);
  const onMoveV2 = (i: number, t0: number, done: boolean, base: Snap) => {
    if (tracks.v2?.locked) { if (done) rowLocked(2); return; }
    if (base.v2[i]?.locked) { say(`${label(base.v2[i])} is locked`, true); return; }
    const out = moveClip(base.v2, i, t0, total); setV2(out);
    if (done) { pushUndo(base); dirtyN.current++; setSaveState("editing"); }
  };

  // ---- the US row: the whole source video cut into its sentences (and the footage between them) ----
  const srcSegs = useMemo<SrcSeg[]>(() => {
    if (!data) return [];
    const ss = [...data.src_sentences].sort((a, b) => a.start_ms - b.start_ms); const out: SrcSeg[] = []; let t = 0;
    const end = data.src_duration_ms || (ss.length ? ss[ss.length - 1].end_ms : 0);
    for (const x of ss) {
      if (x.start_ms - t > 400) out.push({ s0: t, s1: x.start_ms, text: "", idx: null });   // footage with no words
      out.push({ s0: Math.max(t, x.start_ms), s1: x.end_ms, text: x.text, idx: x.idx }); t = Math.max(t, x.end_ms);
    }
    if (end - t > 400) out.push({ s0: t, s1: end, text: "", idx: null });
    return out;
  }, [data]);
  const srcUsedBy = useMemo(() => srcSegs.map((g) => [...clips, ...v2].filter((c) => Math.min(c.s1, g.s1) - Math.max(c.s0, g.s0) > 300).map(label)), [srcSegs, clips, v2]);
  const segName = (g: SrcSeg) => (g.idx != null ? `${data!.labels[1]} sentence ${g.idx}` : `${data!.labels[1]} footage ${fmt(g.s0)}–${fmt(g.s1)}`);
  const onSrcPick = (g: SrcSeg) => { setSrcMedia(null); seekSrc(g.s0); setMarkIn(g.s0); setMarkOut(g.s1); say(`${segName(g)} on the source monitor (In/Out marked): drag it onto V1, a gap or V2`); };
  const onSrcDrop = (g: SrcSeg, to: { kind: "v2"; t: number } | { kind: "gap"; g: { t0: number; t1: number } } | { kind: "clip"; i: number }) => {
    if (to.kind === "v2") { if (rowLocked(2)) return; addV2(to.t, g.s0, g.s1, g.text, segName(g)); return; }
    if (rowLocked(1)) return;
    if (to.kind === "gap") {
      const c: Clip = { id: uid(), row: null, t0: to.g.t0, t1: to.g.t1, s0: g.s0, s1: g.s1, dub_text: "", src_text: g.text };   // source footage
      commit([...clips, c].sort((a, b) => a.t0 - b.t0)); setSel(c.id); say(`Gap filled with ${segName(g)}, played at ×${speedOf(c).toFixed(2)}`); return;
    }
    setSel(clips[to.i].id); doReplace(g.s0, g.s1, segName(g), to.i, null);
  };

  // ---- subtitle cues (S1 dub, S2 English) ----
  const cueSel = selCue ? subs[selCue.lang][selCue.k] : null;
  const setCue = (lang: Lang, k: number, c: Cue) => { const out = subs[lang].slice(); out[k] = c; commitSubs(lang, out); };
  const cueSplit = () => {
    if (!selCue) return; const out = splitCue(subs[selCue.lang], selCue.k, tRef.current);
    if (!out) { say("Put the playhead inside the cue (not at its very edge) to split it", true); return; }
    commitSubs(selCue.lang, out); say("Cue split at the playhead");
  };
  const cueMerge = (dir: 1 | -1) => {
    if (!selCue) return; const k = dir > 0 ? selCue.k : selCue.k - 1;
    const out = mergeCues(subs[selCue.lang], k); if (!out) { say("No cue there to merge with", true); return; }
    commitSubs(selCue.lang, out); setSelCue({ lang: selCue.lang, k });
  };
  const cueDelete = () => {
    if (!selCue) return; const cues = subs[selCue.lang]; if (cues.length < 2) { say("A track needs at least one cue", true); return; }
    commitSubs(selCue.lang, cues.filter((_, k) => k !== selCue.k)); setSelCue(null);
  };
  const cueAdd = (lang: Lang) => {
    const cues = subs[lang]; const t = Math.round(tRef.current);
    if (cues.some((c) => t >= c.t0 && t < c.t1)) { say("There is a cue at the playhead already: move the playhead to an empty spot", true); return; }
    const next = cues.find((c) => c.t0 > t); const t1 = Math.min(t + 1500, next ? next.t0 : total);
    if (t1 - t < 200) { say("Not enough room before the next cue", true); return; }
    const out = [...cues, { t0: t, t1, text: "…" }].sort((a, b) => a.t0 - b.t0);
    commitSubs(lang, out); setSelCue({ lang, k: out.findIndex((c) => c.t0 === t) }); setSel(null);
  };
  const flagList = useMemo(() => {
    const out: { c: Clip; i: number; f: Flag }[] = [];
    clips.forEach((c, i) => (flags[c.id] || []).forEach((f) => out.push({ c, i, f })));
    return out;
  }, [clips, flags]);
  const shownFlags = flagList.filter(({ f }) =>
    flagFilter === "all" ? true : flagFilter === "red" ? f.sev === "red" && !f.approved : flagFilter === "approved" ? f.approved : f.sev !== "info" && !f.approved);
  const goFlag = (dir: 1 | -1) => {
    const open = flagList.filter(({ f }) => f.sev !== "info" && !f.approved);
    if (!open.length) { say("No open flags left"); return; }
    const t = tRef.current + (dir > 0 ? 50 : -50);
    const hit = dir > 0 ? open.find(({ c }) => c.t0 > t) || open[0] : [...open].reverse().find(({ c }) => c.t0 < t - 100) || open[open.length - 1];
    setSel(hit.c.id); seek(Math.max(0, hit.c.t0)); centerOn(hit.c.t0);
  };
  const centerOn = (t: number) => { const sc = scroller.current; if (sc) sc.scrollLeft = (t / 1000) * zoom - sc.clientWidth / 2; };

  async function saveVersion() {
    if (problems.length) { say(problems[0], true); return; }
    setSaveState("saving");
    try { const r = await api.put<{ version: number }>(`/api/episodes/${eid}/timeline`, { clips, v2, tracks, approved, total_ms: total, note: "saved in the editor" });
      setVersion(r.version); setSaveState("saved"); say(`Saved as version ${r.version}`);
      const d = await api.get<Data>(`/api/episodes/${eid}/timeline`); setData(d);
    } catch (e: any) { setSaveState("editing"); say(e.message, true); }
  }
  async function render() {
    if (problems.length) { say(problems[0], true); return; }
    let force = false;
    if (counts.red) {
      if (!confirm(`${counts.red} red flags are still open (very slow clips, repeated footage…).\n\nRender anyway?`)) return;
      force = true;
    }
    const nCuts = cutIds.size;
    const picked = [...clips, ...v2].filter((c) => cutIds.has(c.id) && c.tin && c.tin !== "none").length;
    const tnote = data!.transitions.default === "auto" ? `Transitions: automatic on the ${nCuts} cuts${picked ? ` (${picked} chosen by you)` : ""}.`
      : picked ? `Transitions: the ${picked} you chose; other cuts are hard cuts.` : "Transitions: none (manual mode, no cut has one).";
    if (!counts.red && !confirm(`Render the episode from this timeline?\n\n${tnote}\n\nThe pipeline renders the clips (only changed ones are redone), checks sync, adds the opening, ending${counts.yellow ? "" : ""}, transitions and both subtitle videos, then runs the final check.${counts.yellow ? `\n\n${counts.yellow} yellow flags are open (allowed).` : ""}`)) return;
    setBusy(true);
    try {
      const r = await api.post<{ version: number; steps: string[] }>(`/api/episodes/${eid}/timeline/render`, { clips, v2, tracks, approved, total_ms: total, force });
      say(`Rendering version ${r.version}…`); setTimeout(() => nav(`/episodes/${eid}/processing`), 700);
    } catch (e: any) { say(e.message, true); } finally { setBusy(false); }
  }

  // ---- keyboard ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const cmd = e.metaKey || e.ctrlKey; const k = e.key;
      const step = (ms: number) => { e.preventDefault(); if (playing) togglePlay(); seek(tRef.current + ms); };
      if (k === " ") { e.preventDefault(); togglePlay(); }
      else if (cmd && k.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
      else if (cmd && k.toLowerCase() === "s") { e.preventDefault(); saveVersion(); }
      else if (cmd && k.toLowerCase() === "c") {
        if (window.getSelection()?.toString()) return;                     // copying text on the page: leave it
        const srcOk = markIn != null && markOut != null && markOut - markIn >= 100;
        if (srcOk && (lastPane.current === "src" || !selClip)) { e.preventDefault(); copySrc(); }
        else if (selClip) { e.preventDefault(); copyClip(); }
      }
      else if (cmd && k.toLowerCase() === "v") {
        e.preventDefault();
        if (!clipboard) { say(`Nothing copied yet: set a clip on the source and press ${modKey}C, or select a clip and press ${modKey}C`, true); return; }
        const g = flat.find((q) => q.gap && q.t0 <= tRef.current && tRef.current < q.t1);
        const onSel = selClip && selClip.t0 <= tRef.current && tRef.current < selClip.t1;
        const ontoOther = selClip && clipboard.fromId && selClip.id !== clipboard.fromId && onSel;
        if (g && !onSel) pasteToGap(g);
        else if (clipboard.fromSource && selClip) pasteClip(e.shiftKey);  // In/Out copy onto the selected clip
        else if (ontoOther) pasteClip(e.shiftKey);                      // copied clip A's footage onto selected clip B
        else pasteOnV2();                                              // V1 copy → V2 at the playhead (same as ▲ V2)
      }
      else if (cmd) return;
      else if ((k === "l" || k === "L") && e.shiftKey) toggleLock();
      else if (k === "s" || k === "S") { setSnapOn((x) => !x); say(snapOn ? "Snapping off" : "Snapping on (cuts, dub sentence starts, markers, playhead)"); }
      else if (k === "m" || k === "M") { e.preventDefault(); newMarkerAtPlayhead(); }
      else if (k === "/" && selCue) cueSplit();
      else if (k === "ArrowLeft") step(e.shiftKey ? -1000 : -FRAME);
      else if (k === "ArrowRight") step(e.shiftKey ? 1000 : FRAME);
      else if (k === "j" || k === "J") step(-2000);
      else if (k === "l" || k === "L") step(2000);
      else if (k === "k" || k === "K") { if (playing) togglePlay(); }
      else if (k === "v" || k === "V") setTool("select");
      else if (k === "c" || k === "C") { if (e.shiftKey) doRazor(); else setTool("razor"); }
      else if (k === "y" || k === "Y") setTool("slip");
      else if (k === "Delete" || k === "Backspace") { e.preventDefault(); if (selCue) cueDelete(); else doDelete(e.shiftKey); }
      else if (k === ".") markToV2();
      else if (k === "n") goFlag(1);
      else if (k === "N") goFlag(-1);
      else if (k === "a" || k === "A") toggleApprove();
      else if (k === "i" || k === "I") setMarkIn(Math.round((srcVid.current?.currentTime || 0) * 1000));
      else if (k === "o" || k === "O") setMarkOut(Math.round((srcVid.current?.currentTime || 0) * 1000));
      else if (k === "f" || k === "F") doReplace();
      else if (k === "+" || k === "=") setZoom((z) => Math.min(z * 1.4, 600));
      else if (k === "-" || k === "_") setZoom((z) => Math.max(z / 1.4, 3));
      else if (k === "\\") fitZoom();
      else if (k === "ArrowDown" || k === "ArrowUp") { e.preventDefault(); const i = clamp((selIdx < 0 ? findClip(clips, tRef.current) : selIdx) + (k === "ArrowDown" ? 1 : -1), 0, clips.length - 1); setSel(clips[i].id); seek(clips[i].t0); const sc = scroller.current; if (sc) { const x = (clips[i].t0 / 1000) * zoom; if (x < sc.scrollLeft || x > sc.scrollLeft + sc.clientWidth - 60) centerOn(clips[i].t0); } }
      else if (k === "Home") seek(0);
      else if (k === "End") seek(total - 10);
      else if (k === "?") setShowKeys((x) => !x);
      else if (k === "Escape") { setSel(null); setSelCue(null); setShowKeys(false); setTrackDlg(null); document.dispatchEvent(new Event("close-tmenu")); }
    };
    // ⌘C copies from where the editor last clicked: the source monitor, or the timeline / a selected clip
    const onDown = (e: PointerEvent) => { lastPane.current = (e.target as HTMLElement)?.closest?.(".src-pane") ? "src" : "tl"; };
    window.addEventListener("keydown", onKey); window.addEventListener("pointerdown", onDown, true);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown, true); };
  });

  const scrollerW = useRef(1200);
  const fitZoom = () => { const sc = scroller.current; if (sc) setZoom(Math.max(2, (sc.clientWidth - 20) / (total / 1000))); };

  if (err) return <div className="page"><div className="error">{err}</div><Link to={`/episodes/${eid}`}>Back to the episode</Link></div>;
  if (!data) return <div className="ed" style={{ display: "grid", placeItems: "center" }}><span className="muted">Loading the timeline…</span></div>;

  const [D, S] = data.labels;
  const cueAt = (cues: Cue[], t: number) => cues.find((c) => t >= c.t0 && t < c.t1);
  const sub = subMode === "none" ? null : cueAt(subMode === "dub" ? subs.dub : subs.en, tShow);
  const openQc = markers.filter((m) => !m.resolved).length;
  // rows no footage was found for: empty gaps still to fill (and, in older versions, clips stretched over them)
  const openHoles = (data.timeline.holes || []).filter((h) => flat.some((q) => q.gap && Math.min(q.t1, h.t1) - Math.max(q.t0, h.t0) > 30));
  const notFound = new Set(flagList.filter(({ f }) => f.kind === "not_found" && f.sev === "red" && !f.approved).map(({ f }) => (f as any).row)).size + openHoles.length;
  // clips the software trimmed (fast-clip rule, over x1.25): yellow until the editor approves them (= checked)
  const trimmedOpen = flagList.filter(({ f }) => f.kind === "trimmed" && !f.approved);
  const curP = flat[findClip(flat, tShow)];
  const cur = curP && !curP.gap ? { ...byId.get(curP.id!)!, ...curP } as Clip : null;
  const rendering = ["running", "waiting"].includes(data.state.render || "") && data.state.review !== "needs_input";
  const lipSet = new Set(data.lip_rows);

  return (
    <div className="ed" style={{ ["--tl-h" as any]: `${tlH}px` }}>
      {/* ---------- top bar ---------- */}
      <div className="ed-top">
        <Link to={`/episodes/${eid}`} title="Back to the episode"><BrandMark /></Link>
        <div className="ed-title"><b>{data.show.code} · Ep {data.episode.number}</b><span className="wide-only">{fmt(total, 1)} · locked to the {D} dub</span><span className="built-by">Built by Sameer</span></div>
        <div className="tools">
          <button className={`tool ${tool === "select" ? "on" : ""}`} onClick={() => setTool("select")} title="Select, drag a clip onto another to swap footage, drag an edge to move a cut">Select <kbd>V</kbd></button>
          <button className={`tool ${tool === "razor" ? "on" : ""}`} onClick={() => setTool("razor")} title="Click a clip to cut it in two">Razor <kbd>C</kbd></button>
          <button className={`tool ${tool === "slip" ? "on" : ""}`} onClick={() => setTool("slip")} title="Drag a clip to slide which part of the source it shows">Slip <kbd>Y</kbd></button>
        </div>
        <div className="tools">
          <button className="tool" onClick={undo} disabled={!hist.current.undo.length} title="Undo (⌘Z)">↶</button>
          <button className="tool" onClick={redo} disabled={!hist.current.redo.length} title="Redo (⇧⌘Z)">↷</button>
        </div>
        <span className="sep" />
        <div className="counts">
          <span className="chip chip-red" title="must fix or approve before render">{counts.red} red</span>
          <span className="chip chip-yellow" title="worth a look">{counts.yellow} yellow</span>
          <span className="chip chip-green wide-only">{approved.length} approved</span>
          {openQc > 0 && <span className="chip chip-red" title="QC notes still open (red until resolved)">{openQc} QC</span>}
          {notFound > 0 && <span className="chip chip-red" style={{ cursor: "pointer" }} title="Dub lines no footage was found for (also after Claude's check): their place is an empty gap — put a clip there (drag a sentence, a clip or an imported clip). Click for the next one."
            onClick={() => { const h = openHoles.find((x) => x.t0 > tRef.current + 50) || openHoles[0]; if (h) { setSel(null); seek(h.t0); centerOn(h.t0); say(`Not found: ${D}#${h.row} "${h.text.slice(0, 60)}" — put a clip in this gap`); return; }
              const hit = flagList.find(({ f }) => f.kind === "not_found" && f.sev === "red" && !f.approved); if (hit) { setSel(hit.c.id); seek(hit.c.t0); centerOn(hit.c.t0); } }}>{notFound} not found</span>}
          {trimmedOpen.length > 0 && <span className="chip chip-yellow" style={{ cursor: "pointer" }} title="Clips the software trimmed because they played faster than x1.25 (cut down to about x1.2). Check each cut; approve the clip when it looks right. Click for the next one."
            onClick={() => { const hit = trimmedOpen.find(({ c }) => c.t0 > tRef.current + 50) || trimmedOpen[0]; if (hit) { setSel(hit.c.id); seek(hit.c.t0); centerOn(hit.c.t0); say(hit.f.msg); } }}>{trimmedOpen.length} trimmed</span>}
        </div>
        <button className="tool" onClick={lockApproved} title="Lock every approved clip so no later edit can move it">🔒<span className="wide-only"> Lock approved</span></button>
        <div className="grow" />
        <span className="save-state wide-only">{saveState === "saved" ? (version ? `v${version} saved` : "pipeline version") : saveState === "draft" ? `v${version} + changes (autosaved)` : saveState === "saving" ? "saving…" : "editing…"}</span>
        <button className="ghost small" onClick={() => setShowKeys(true)} title="Keyboard shortcuts (?)">⌨</button>
        <select className="trans-mode" value={data.transitions.default === "auto" ? "auto" : "manual"} onChange={(e) => setTransMode(e.target.value === "auto")}
          title="How cuts get transitions in this show (also on the show page)">
          <option value="auto">Transitions: auto</option>
          <option value="manual">Transitions: mine only</option>
        </select>
        <button onClick={saveVersion} title="Save a version (⌘S)">Save</button>
        <button className="primary" onClick={render} disabled={busy || rendering || problems.length > 0}
          title={problems[0] || (counts.red ? `${counts.red} red flags open` : "Render this timeline")}>
          {rendering ? "Rendering…" : "Render ▸"}
        </button>
      </div>

      {/* ---------- monitors + panels ---------- */}
      <div className={`ed-mid ${srcOpen ? "" : "src-closed"}`} style={{ gridTemplateColumns: [
        leftOpen ? (leftTab === "mapping" ? "minmax(300px, 34%)" : "minmax(190px, 17%)") : "30px",
        srcOpen ? "minmax(0, 1fr)" : "30px", srcOpen ? "minmax(0, 1.25fr)" : "minmax(0, 2.25fr)",
        genClip ? "minmax(360px, 30%)" : inspOpen ? "minmax(210px, 17%)" : "30px"].join(" ") }}>
        <div className={`pane ${leftOpen ? "" : "folded"}`}>
          {!leftOpen && <button className="src-open" onClick={toggleLeft} title="Show Flags / QC notes / Mapping">▸<span>Flags{counts.red ? ` · ${counts.red}` : ""}</span></button>}
          <div className="pane-head">
            <button className="ghost small fold" onClick={toggleLeft} title="Fold this panel away">◂</button>
            <div className="pane-tabs">
              <button className={`pane-tab ${leftTab === "flags" ? "on" : ""}`} onClick={() => setLeftTab("flags")}>Flags</button>
              <button className={`pane-tab ${leftTab === "markers" ? "on" : ""}`} onClick={() => setLeftTab("markers")}>QC notes{openQc ? ` ${openQc}` : ""}</button>
              <button className={`pane-tab ${leftTab === "mapping" ? "on" : ""}`} onClick={() => setLeftTab("mapping")} title="Each clip's dub line and its source sentence side by side; drag a sentence onto a row to change the clip">Mapping</button>
              <button className={`pane-tab ${leftTab === "source" ? "on" : ""}`} onClick={() => setLeftTab("source")}>{S} sentences</button>
              <button className={`pane-tab ${leftTab === "versions" ? "on" : ""}`} onClick={() => setLeftTab("versions")}>Versions</button>
            </div>
          </div>
          {leftTab === "flags" && <>
            <div className="filters">
              {(["open", "red", "approved", "all"] as const).map((f) => (
                <button key={f} className={flagFilter === f ? "on" : ""} onClick={() => setFlagFilter(f)}>{f === "open" ? `To check ${counts.red + counts.yellow}` : f === "red" ? `Red ${counts.red}` : f === "approved" ? "Approved" : "All notes"}</button>
              ))}
            </div>
            <div className="pane-body flag-list">
              {!shownFlags.length && <div className="muted pad" style={{ padding: 16 }}>{flagFilter === "open" ? "Nothing left to check. 🎉" : "None."}</div>}
              {shownFlags.map(({ c, f }, k) => (
                <div key={c.id + f.kind + k} className={`flag-item ${sel === c.id ? "sel" : ""}`} onClick={() => { const t = f.marker ? markers.find((m) => m.id === f.marker)?.t_ms ?? c.t0 : c.t0; setSel(c.id); setSelCue(null); seek(t); centerOn(t); }}>
                  <span className={`fdot ${f.approved ? "ok" : f.sev}`} />
                  <span><b>{label(c)}</b> <span className="fm">{f.msg}</span></span>
                  <span className="ft">{fmt(c.t0)}</span>
                </div>
              ))}
            </div>
          </>}
          {leftTab === "markers" && <>
            <form className="marker-add" onSubmit={(e) => { e.preventDefault(); addMarker(); }}>
              <input ref={markerInput} className="search" placeholder="06:04 Viola smiling  (no time = at the playhead)" value={markerText} onChange={(e) => setMarkerText(e.target.value)} />
              <button className="small primary" disabled={!markerText.trim()}>Add</button>
            </form>
            <div className="pane-body">
              {!markers.length && <div className="muted" style={{ padding: 14 }}>QC notes that come back after a review go here: type the time and the note (M at the playhead). Each one is a red flag on its clip until you tick it resolved.</div>}
              {markers.map((m) => (
                <div key={m.id} className={`marker-item ${m.resolved ? "done" : ""}`} onClick={() => { seek(m.t_ms); centerOn(m.t_ms); }}>
                  <input type="checkbox" checked={!!m.resolved} title="Resolved" onClick={(e) => e.stopPropagation()} onChange={(e) => patchMarker(m.id, { resolved: e.target.checked ? 1 : 0 })} />
                  <span className="ft">{fmt(m.t_ms)}</span>
                  <span className="mt">{m.text}</span>
                  <button className="ghost small danger" title="Delete the note" onClick={(e) => { e.stopPropagation(); if (confirm(`Delete the note "${m.text}"?`)) deleteMarker(m.id); }}>×</button>
                </div>
              ))}
            </div>
          </>}
          {leftTab === "mapping" && <MappingPanel clips={clips} sents={data.src_sentences} flags={flags} sel={sel} D={D} S={S} t={tShow}
            onPick={(i) => { setSel(clips[i].id); setSelCue(null); seek(clips[i].t0); centerOn(clips[i].t0); }}
            onDrop={dropSentence} onPreview={(x) => { setSrcMedia(null); seekSrc(x.start_ms); setMarkIn(x.start_ms); setMarkOut(x.end_ms); }} />}
          {leftTab === "source" && <SourceList sents={data.src_sentences} clips={clips} onPick={(s) => { setSrcMedia(null); seekSrc(s.start_ms); setMarkIn(s.start_ms); setMarkOut(s.end_ms); }}
            onUse={(s) => doReplace(s.start_ms, s.end_ms, `${S} sentence ${s.idx}`, selIdx, null)} canUse={!!selClip} />}
          {leftTab === "versions" && (
            <div className="pane-body">
              <div className="sent-item" onClick={() => confirm("Go back to the pipeline's own edit list? Your current changes can be undone with ⌘Z.") && (() => { const prev = snap(); api.get<Data>(`/api/episodes/${eid}/timeline?pipeline=1`).then((d) => { hist.current.undo.push(prev); setClips(d.timeline.clips); setV2([]); setApproved([]); dirtyN.current++; setSaveState("editing"); say("Pipeline edit list opened"); }); })()}>
                <span className="ft">v0</span> the pipeline's edit list (as processed)
              </div>
              {data.versions.map((v) => (
                <div key={v.version} className="sent-item" onClick={() => confirm(`Open version ${v.version}? Your current changes can be undone with ⌘Z.`) && (() => { const prev = snap(); api.get<Timeline>(`/api/episodes/${eid}/timeline/versions/${v.version}`).then((tl) => { hist.current.undo.push(prev); setClips(tl.clips); setV2(tl.v2 || []); setApproved(tl.approved || []); setVersion(v.version); dirtyN.current++; setSaveState("editing"); say(`Version ${v.version} opened`); }); })()}>
                  <span className="ft">v{v.version}</span> {v.made_by === "render" ? "rendered" : v.note || v.made_by} <span className="muted small-text">· {new Date(v.created * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                </div>
              ))}
              <div style={{ padding: "10px 12px" }}><a className="button small" href={`/api/episodes/${eid}/export/premiere${version ? `?version=${version}` : ""}`}
                title="Final Cut Pro 7 XML (Premiere: File → Import). Also saved in the output folder; every Render saves one too.">⇩ Export {version ? `v${version}` : "this timeline"} for Premiere</a></div>
              <div style={{ padding: "4px 12px 10px" }}><button className="small" title="Claude makes the scene edit again from scratch (its earlier answers are kept in scene/_old/). Steps after it run again; your saved versions stay."
                onClick={async () => { if (!confirm("Redo the scene edit with fresh Claude answers? It costs Claude time (about $5 for a 10-minute episode) and the steps after it run again. Your saved versions stay in this list.")) return;
                  try { await api.post(`/api/episodes/${eid}/scene/redo`); say("Scene edit is being made again: follow it on the episode page"); } catch (e: any) { say(e.message, true); } }}>↻ Redo scene edit</button></div>
              {!data.versions.length && <div className="muted" style={{ padding: 14 }}>No saved versions yet. Save version (⌘S) keeps a copy you can come back to; Render saves one too.</div>}
            </div>
          )}
        </div>

        {/* source monitor (collapsible) */}
        <div className={`pane src-pane ${srcOpen ? "" : "folded"}`}>
          {!srcOpen && <button className="src-open" onClick={toggleSrc} title="Show the source monitor">▸<span>Source</span></button>}
          <div className="pane-head"><button className="ghost small fold" onClick={toggleSrc} title="Collapse the source monitor (more room for the program)">◂</button>
            Source · {srcMedia ? <span style={{ textTransform: "none" }}>{impOf(srcMedia)?.name || "imported clip"}</span> : S}
            {srcMedia && <button className="ghost small" onClick={() => setSrcMedia(null)} title={`Back to the ${S} source video`}>↩ {S}</button>}<span className="grow" />
            <span className="mono" style={{ textTransform: "none", color: proxy.ready ? "var(--green)" : "var(--faint)" }} title={proxy.ready ? "The monitors play the 540-px preview copy (a keyframe every 0.5 s): seeking is instant" : "The monitors play the original until the preview copy is made"}>
              {proxy.ready ? "540p preview" : proxy.error ? "original (preview copy failed)" : `making 540p ${Math.round((proxy.progress || 0) * 100)}%`}</span>
            {selClip && <span className="mono" style={{ textTransform: "none", marginLeft: 8 }}>{label(selClip)}</span>}</div>
          <div className="monitor">
            <video ref={srcVid} src={srcMedia ? impUrl(srcMedia) : videoUrl} preload="metadata" playsInline controls onLoadedData={() => seekSrc(srcT)}
              onTimeUpdate={(e) => setSrcT(e.currentTarget.currentTime * 1000)} />
            {markIn != null && <span className="mon-badge">In {fmt(markIn)} · Out {markOut != null ? fmt(markOut) : "—"}</span>}
          </div>
          {/* the clip to take from the source: start and end (typed, or I / O at the current frame), then Copy (⌘C) */}
          <div className="clip-bar" onPointerDown={() => { lastPane.current = "src"; }}>
            <span className="cb-label">Clip</span>
            <TimeField value={markIn} placeholder="start" title="Start of the clip in the source (type 1:02.5, or press I at the current frame)"
              onSet={(v) => { setMarkIn(v); if (v != null) seekSrc(v); lastPane.current = "src"; }} />
            <span className="muted">→</span>
            <TimeField value={markOut} placeholder="end" title="End of the clip in the source (type 1:05, or press O at the current frame)"
              onSet={(v) => { setMarkOut(v); if (v != null) seekSrc(v); lastPane.current = "src"; }} />
            <span className="cb-len mono">{markIn != null && markOut != null && markOut > markIn ? `${((markOut - markIn) / 1000).toFixed(2)} s` : ""}</span>
            <span className="grow" />
            <button className="small" disabled={markIn == null || markOut == null || markOut - markIn < 100} onClick={copySrc}
              title={`Copy this clip (${modKey}C), then paste it with ${modKey}V: onto a selected clip, into a gap at the playhead, or on V2 at the playhead`}>⧉ Copy</button>
          </div>
          <div className="mon-bar">
            <span className="big">{fmt(srcT, 2)}</span><span className="grow" />
            <button className="small" onClick={() => setMarkIn(Math.round(srcT))} title="Mark In (I)">In</button>
            <button className="small" onClick={() => setMarkOut(Math.round(srcT))} title="Mark Out (O)">Out</button>
            <button className="small wide-only" disabled={!selClip} onClick={() => selClip && seekSrc(selClip.s0)} title="Go to the selected clip's footage">⤒ Clip</button>
            <button className="small" disabled={markIn == null || markOut == null} onClick={markToV2} title="Put In→Out on V2 at the playhead, at real speed, over V1 (.)">▲ V2</button>
            <button className="small primary" disabled={!selClip || markIn == null || markOut == null} onClick={() => doReplace()} title="Replace the selected clip with In→Out, fitted to its slot (F)">Replace<span className="wide-only"> ⏎</span></button>
          </div>
        </div>

        {/* program monitor */}
        <div className="pane">
          <div className="pane-head">Program · timeline
            {busyNow.length > 0 && <span className="busy-note" title={`Running now: ${busyNow.map((b) => `${STEPNAME[b.step] || b.step} (${b.episode})`).join(", ")}.\nThe Mac is busy, so the preview may lag; if the picture falls behind, the voice waits for it. The rendered video is not affected.`}>
              ⚙ {STEPNAME[busyNow[0].step] || busyNow[0].step}<span className="wide-only"> running · preview may lag</span></span>}
            <span className="grow" />
            <div className="pane-tabs">
              {(["dub", "en", "none"] as const).map((m) => <button key={m} className={`pane-tab ${subMode === m ? "on" : ""}`} onClick={() => setSubMode(m)}>{m === "dub" ? D : m === "en" ? "EN" : "No subs"}</button>)}
            </div>
          </div>
          <div className="monitor" onClick={togglePlay} ref={(el) => { if (el && !monRO.current) { monRO.current = new ResizeObserver(() => setMonH(el.clientHeight)); monRO.current.observe(el); } }}>
            <div className={`prog-frame ${cur?.zoom && cur.zoom > 1.001 ? "zoomed" : ""}`}
              style={{ ["--src-ar" as string]: String(srcAR) }}
              title="This box is the final video frame. Zoom crops the picture inside it; the black around it is not in the file.">
              <video ref={vids[0]} src={videoUrl} preload="auto" muted playsInline onLoadedData={(e) => {
                const v = e.currentTarget; if (v.videoWidth) setSrcAR(v.videoWidth / v.videoHeight);
                vClip.current[0] = ""; if (act.current === 0) showFrame(tRef.current, playing);
              }} />
              <video ref={vids[1]} className="hidden" src={videoUrl} preload="auto" muted playsInline onLoadedData={() => { vClip.current[1] = ""; if (act.current === 1) showFrame(tRef.current, playing); }} />
              <video ref={impV} className="hidden" preload="auto" muted playsInline onLoadedData={() => showFrame(tRef.current, !!audio.current && !audio.current.paused)} />
              <div className="mon-fx" ref={fxRef} />
              {sub && (() => {
                const lang: Lang = subMode === "en" ? "en" : "dub"; const st = styleOf(lang); const k = monH / 1080;
                return st.style === "strip"
                  ? <div className="mon-sub strip" style={{ bottom: `${8 * k}px`, fontSize: `${st.size * k}px` }}><span>{sub.text}</span></div>
                  : <div className="mon-sub" style={{ bottom: `${st.bottom * k}px` }}><span style={{ fontSize: `${st.size * k}px`, background: `rgba(0,0,0,${st.alpha})` }}>{sub.text}</span></div>;
              })()}
            </div>
            <audio ref={audio} src={`/api/episodes/${eid}/media/dub_preview`} preload="auto" />
            {cur && <span className="mon-badge">{label(cur)} ← {S} {fmt(srcAt(cur, tShow))}</span>}
            {cur && <span className="mon-badge right">×{speedOf(cur).toFixed(2)}</span>}
            {held && <span className="mon-hold">picture catching up — the voice waits…</span>}
          </div>
          <div className="mon-bar">
            <button className="icon-btn" onClick={() => seek(tRef.current - 5000)} title="Back 5 s">⏪</button>
            <button className="icon-btn play" onClick={togglePlay} title="Play / pause (Space)">{playing ? "❚❚" : "▶"}</button>
            <button className="icon-btn" onClick={() => seek(tRef.current + 5000)} title="Forward 5 s">⏩</button>
            <span className="big">{fmt(tShow, 2)}</span><span className="muted wide-only">/ {fmt(total, 1)}</span>
            <span className="grow" />
            <button className="small" onClick={() => goFlag(-1)} title="Previous flag (⇧N)">◂<span className="wide-only"> Flag</span></button>
            <button className="small" onClick={() => goFlag(1)} title="Next flag (N)"><span className="wide-only">Flag </span>▸</button>
          </div>
        </div>

        {/* inspector, or the Generate panel for one clip */}
        {genClip ? <div className="pane gen-pane">
          <GenPanel eid={eid!} clip={genClip} onClose={() => setGenClip(null)} onBinChanged={loadBin} onPutV2={putGenOnV2} />
        </div> :
        <div className={`pane ${inspOpen ? "" : "folded"}`}>
          {!inspOpen && <button className="src-open" onClick={toggleInsp} title="Show the inspector">◂<span>Inspector</span></button>}
          <div className="pane-head">Inspector<span className="grow" /><button className="ghost small fold" onClick={toggleInsp} title="Fold the inspector away">▸</button></div>
          <div className="pane-body">
            {cueSel && selCue && <CueEditor key={`${selCue.lang}${selCue.k}`} c={cueSel} k={selCue.k} n={subs[selCue.lang].length} lang={selCue.lang} D={D}
              prev={subs[selCue.lang][selCue.k - 1]} next={subs[selCue.lang][selCue.k + 1]} t={tShow} total={total} edited={subsEdited[selCue.lang]}
              onChange={(c) => setCue(selCue.lang, selCue.k, c)} onSplit={cueSplit} onMerge={cueMerge} onDelete={cueDelete}
              onPlay={() => { seek(cueSel.t0); if (!playing) togglePlay(); }} onRevert={() => revertSubs(selCue.lang)}
              onRetranslate={selCue.lang === "en" ? () => retranslate(selCue.k) : undefined} />}
            {!cueSel && !selClip && <div className="insp muted">Click a clip on the timeline, or a flag on the left.<br /><br />
              <b style={{ color: "var(--text)" }}>How editing works</b><br />
              The {D} voice never moves. You change which {S} footage plays under it:<br />
              • drag a clip's edge to trim it: in leaves a gap, out overwrites the next clip (⌥: move the cut)<br />• drag a clip onto another to swap their footage<br />• Slip (Y) slides the footage<br />
              • Razor (C) cuts a clip in two<br />• pick a sentence or mark In/Out on the source, then Replace (F)<br />• Delete leaves a gap (click it to fill, or drop a sentence on it)<br />              • V2 is over V1: drop a sentence, drag a clip up, or press . for the marked In→Out<br />
              • ⌘C a V1 clip, then ⌘V pastes it on V2 at the playhead (In/Out + ▲ V2 is the same from the source)<br />• ⇧L locks a clip; Lock approved locks them all<br />
              • click a subtitle on S1 / S2 to edit its words and times<br />• M adds a QC note at the playhead</div>}
            {!cueSel && selClip && !transInto.has(selClip.id) && (
              <div className="insp muted small-text" style={{ paddingBottom: 8 }}>First clip of the episode — no transition into it.</div>
            )}
            {!cueSel && selClip && transInto.has(selClip.id) && (
              <div className="insp" style={{ paddingBottom: 0 }}>
                <label className="trans-row">Transition into this clip
                  <TransSelect info={data.transitions} value={selClip.tin} onChange={(v) => setTransition(selClip.id, v)} />
                </label>
                <div className="muted small-text" style={{ padding: "0 0 8px", textTransform: "none", letterSpacing: 0, fontSize: 12 }}>
                  {selClip.tin
                    ? `${TLABEL[selClip.tin] || selClip.tin} on this clip (overrides ${TLABEL[data.transitions.default]}).`
                    : data.transitions.default === "auto"
                      ? "Automatic for this show: Render picks a style on picture jumps. Choose one here to use it on this clip."
                      : !cutIds.has(selClip.id)
                        ? "The picture continues from the clip before. A transition here still plays at this clip's start."
                        : "Manual: this clip is a hard cut until you pick a style."}
                </div>
              </div>
            )}
            {!cueSel && selClip && <Inspector c={selClip} clipboard={clipboard?.from || null} onLock={() => toggleLock()} onCopy={copyClip} onPaste={pasteClip} onPasteV2={pasteOnV2} fl={flags[selClip.id] || []} approved={approved.includes(selClip.id)} lip={selClip.row != null && lipSet.has(selClip.row)}
              D={D} S={S} srcDur={durOf(selClip.media)}
              onChange={(c) => updateSel(c)} onMotion={setSelMotion} onApprove={() => toggleApprove()} onReset={resetClip}
              track={selV2 >= 0 ? 2 : 1} onCopyToV2={() => selClip && copyToV2(selClip)} v2Locked={!!tracks.v2?.locked}
              onGenerate={() => {
                const ord = [...clips].sort((x, y) => x.t0 - y.t0); const k = ord.findIndex((x) => x.id === selClip.id);
                const prev = k > 0 ? ord[k - 1] : clips.filter((x) => x.t1 <= selClip.t0 + 1).pop(); const next = k >= 0 ? ord[k + 1] : clips.find((x) => x.t0 >= selClip.t1 - 1);
                setGenClip({ id: selClip.id, label: label(selClip), row: selClip.row, t0: selClip.t0, t1: selClip.t1, s0: selClip.s0, s1: selClip.s1,
                  dub_text: selClip.dub_text, src_text: selClip.src_text, media: selClip.media, prev_text: prev?.dub_text || "", next_text: next?.dub_text || "",
                  lip: selClip.row != null && lipSet.has(selClip.row), flags: (flags[selClip.id] || []).filter((f) => f.sev !== "info").map((f) => f.msg) });
                if (!inspOpen) toggleInsp(); }}
              onPlay={() => { seek(selClip.t0); if (!playing) togglePlay(); }} onDelete={() => doDelete(false)}
              onFitSentence={() => {
                const ov = (x: Sentence) => Math.min(x.end_ms, selClip.s1) - Math.max(x.start_ms, selClip.s0);
                const s = [...data.src_sentences].sort((a, b) => ov(b) - ov(a))[0];
                if (s && ov(s) > 0) doReplace(s.start_ms, s.end_ms, `${S} sentence ${s.idx}`); else say("No source sentence under this clip", true);
              }} />}
          </div>
        </div>}
      </div>

      {/* ---------- splitter + timeline ---------- */}
      <div className="ed-split" onPointerDown={(e) => {
        const y0 = e.clientY, h0 = tlH; (e.target as HTMLElement).setPointerCapture(e.pointerId);
        const mv = (ev: PointerEvent) => setTlH(clamp(h0 - (ev.clientY - y0), 200, window.innerHeight - 260));
        const up = () => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); };
        window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
      }} />
      <div className="ed-bottom" style={{ gridTemplateColumns: binOpen ? "minmax(220px, 26%) minmax(0, 1fr)" : "30px minmax(0, 1fr)" }}>
      {!binOpen ? <div className="pane folded bin-folded"><button className="src-open" onClick={toggleBin} title="Show the Project bin (imported clips, episode files)">▸<span>Project{bin.imports.length ? ` · ${bin.imports.length}` : ""}</span></button></div> :
      <BinPanel onFold={toggleBin} eid={eid!} bin={bin} clips={[...clips, ...v2]} srcMedia={srcMedia} onShow={showImport} onImport={importFiles} onRemove={removeImport}
        onImportPath={async (path) => { try { const x = await api.post<Imp>(`/api/episodes/${eid}/imports/path`, { path }); say(`${x.name} imported`); loadBin(); } catch (e: any) { say(e.message, true); } }}
        onShowSource={() => { setSrcMedia(null); if (!srcOpen) toggleSrc(); }} onTrack={(lang) => setTrackDlg(lang)} labels={data.labels} />}
      <TimelineView eid={eid!} thumbRev={proxy.rev || 0} holes={data.timeline.holes || []} clips={clips} flags={flags} approved={approved} total={total} zoom={zoom} setZoom={setZoom} fitZoom={fitZoom}
        tool={tool} sel={sel} setSel={(id) => { setSel(id); setSelCue(null); }} seek={seek} subs={subs} labels={data.labels} lipSet={lipSet}
        scroller={scroller} scrollerW={scrollerW} playheadEl={playheadEl} t={tShow} srcDur={srcDur}
        markers={markers} snapOn={snapOn} setSnapOn={setSnapOn} snapPts={snapPts} srcStarts={data.src_sentences.map((x) => x.start_ms)} tRef={tRef}
        selCue={selCue} onCuePick={(lang, k) => { setSelCue({ lang, k }); setSel(null); }} onCueAdd={cueAdd}
        onCueDrag={(lang, cues, done, base) => { if (done) pushUndo(base); setSubs((x) => ({ ...x, [lang]: cues })); }}
        isLocked={(...idx) => locked(...idx)} onDropSentence={dropSentence} onTrack={setTrackDlg}
        trans={data.transitions} onTransition={setTransition}
        v2={v2} flat={flat} cutIds={cutIds} onTrim={onTrim} onMoveV2={onMoveV2} onDropV2={dropOnV2}
        onCopyToV2={(c, t) => copyToV2(c, t)} onFillGap={fillGap} onDropGap={dropOnGap}
        onClipToGap={clipToGap} onPasteGap={pasteToGap} clipboard={clipboard?.from || null}
        tracks={tracks} onRow={setRow} srcSegs={srcSegs} srcUsedBy={srcUsedBy} onSrcPick={onSrcPick} onSrcDrop={onSrcDrop}
        onRoll={(i, t, done) => { if (tracks.v1?.locked) { if (done) rowLocked(1); return; } const out = roll(clips, i, t); if (done) commit(out); else setClips(out); }}
        onSlip={(track, i, d, done, base) => {
          if (track === 1 ? tracks.v1?.locked : tracks.v2?.locked) { if (done) rowLocked(track); return; }
          const list = track === 1 ? clips : v2; const out = list.slice(); out[i] = slip(base, d, durOf(base.media));
          const set = track === 1 ? setClips : setV2;
          if (done) { const before = list.map((c, k) => (k === i ? base : c)); pushUndo(track === 1 ? { clips: before, approved, subs, v2 } : { clips, approved, subs, v2: before }); set(out); dirtyN.current++; setSaveState("editing"); } else set(out); }}
        onRazor={(t) => doRazor(t)} onSwap={(i, j) => { if (locked(i, j)) return; commit(swap(clips, i, j)); say(`Swapped footage: ${label(clips[i])} ⇄ ${label(clips[j])}`); }}
        onSnapshot={snap} pushUndo={(s) => { pushUndo(s); dirtyN.current++; setSaveState("editing"); }} />
      </div>

      {problems.length > 0 && !hideProblems && (
        <div className="toast err toast-closable" style={{ bottom: tlH + 20 }} role="status">
          <span>⚠ {problems[0]}{problems.length > 1 ? ` · ${problems.length} issues` : ""}</span>
          <button type="button" className="toast-x" title="Dismiss" aria-label="Dismiss"
            onClick={() => setHideProblems(true)}>×</button>
        </div>
      )}
      {toast && <div className={`toast ${toast.err ? "err" : ""}`}>{toast.text}</div>}
      {showKeys && <KeysHelp onClose={() => setShowKeys(false)} />}
      {trackDlg && <TrackDialog lang={trackDlg} name={trackDlg === "dub" ? `S1 ${D}` : "S2 EN"} show={data.show.code} cur={styleOf(trackDlg)}
        edited={subsEdited[trackDlg]} n={subs[trackDlg].length} onClose={() => setTrackDlg(null)}
        onSave={(st) => { saveStyle(trackDlg, st); setTrackDlg(null); }} onImport={(f) => importSrt(trackDlg, f)}
        onRevert={() => { setTrackDlg(null); revertSubs(trackDlg); }} />}
    </div>
  );
}

// ---------------- inspector ----------------
function MotionBlock({ c, locked, onMotion }: { c: Clip; locked: boolean; onMotion: (c: Clip, done: boolean) => void }) {
  const z = c.zoom && c.zoom > 1 ? c.zoom : 1;
  const px = c.pan_x ?? 0, py = c.pan_y ?? 0;
  const zoomed = z > 1.001;
  const put = (nz: number, nx: number, ny: number, done: boolean) => onMotion(applyMotion(c, nz, nx, ny), done);
  const pad = useRef<HTMLDivElement>(null);
  const fromPtr = (e: React.PointerEvent, done: boolean) => {
    const r = pad.current?.getBoundingClientRect(); if (!r) return;
    put(Math.max(z, 1.2), clamp((e.clientX - r.left) / r.width, 0, 1) * 2 - 1,
      clamp((e.clientY - r.top) / r.height, 0, 1) * 2 - 1, done);
  };
  return (
    <fieldset className="motion" disabled={locked}>
      <div className="lbl muted small-text">Motion · zoom and position</div>
      <label className="motion-row">Zoom {Math.round(z * 100)}%
        <input type="range" min={1} max={MOTION_Z_MAX} step={0.05} value={z}
          onChange={(e) => put(parseFloat(e.target.value), px, py, false)}
          onPointerUp={() => put(z, px, py, true)} onPointerCancel={() => put(z, px, py, true)} />
      </label>
      <div className="motion-xy" style={{ opacity: zoomed ? 1 : 0.55 }}>
        <div className="pad" ref={pad} title="Drag: which part of the picture stays in frame (left / right / top / bottom)"
          onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture(e.pointerId); fromPtr(e, false); }}
          onPointerMove={(e) => { if (e.buttons) fromPtr(e, false); }}
          onPointerUp={(e) => fromPtr(e, true)}>
          <span className="pad-dot" style={{ left: `${50 + px * 50}%`, top: `${50 + py * 50}%` }} />
        </div>
        <div className="motion-sliders">
          <label>Position X · {px < -0.05 ? "left" : px > 0.05 ? "right" : "center"}
            <input type="range" min={-1} max={1} step={0.05} value={px} disabled={!zoomed}
              onChange={(e) => put(z, parseFloat(e.target.value), py, false)}
              onPointerUp={() => put(z, px, py, true)} onPointerCancel={() => put(z, px, py, true)} />
          </label>
          <label>Position Y · {py < -0.05 ? "top" : py > 0.05 ? "bottom" : "center"}
            <input type="range" min={-1} max={1} step={0.05} value={py} disabled={!zoomed}
              onChange={(e) => put(z, px, parseFloat(e.target.value), false)}
              onPointerUp={() => put(z, px, py, true)} onPointerCancel={() => put(z, px, py, true)} />
          </label>
          <div className="nudge">
            {([[0, 0, "Center"], [-1, 0, "Left"], [1, 0, "Right"], [0, -1, "Top"], [0, 1, "Bottom"]] as const).map(([x, y, l]) =>
              <button key={l} type="button" disabled={!zoomed} onClick={() => put(z, x, y, true)}>{l}</button>)}
          </div>
        </div>
      </div>
      {zoomed && <button className="small ghost" type="button" onClick={() => put(1, 0, 0, true)}>Reset zoom</button>}
      <div className="muted small-text" style={{ textTransform: "none", letterSpacing: 0 }}>The program monitor's box is the final video frame. Zoom crops inside that box (black around it is not in the file). Drag the square to pick left / right / top / bottom. Render burns it into the clip.</div>
    </fieldset>
  );
}

function Inspector({ c, fl, approved, lip, D, S, srcDur, clipboard, onChange, onMotion, onApprove, onReset, onPlay, onDelete, onFitSentence, onLock, onCopy, onPaste, onPasteV2, track, onCopyToV2, v2Locked, onGenerate }: {
  c: Clip; fl: Flag[]; approved: boolean; lip: boolean; D: string; S: string; srcDur: number; clipboard: string | null;
  onChange: (c: Clip) => void; onMotion: (c: Clip, done: boolean) => void; onApprove: () => void; onReset: () => void; onPlay: () => void; onDelete: () => void; onFitSentence: () => void;
  onLock: () => void; onCopy: () => void; onPaste: (keepSpeed: boolean) => void; onPasteV2: () => void; track: 1 | 2; onCopyToV2: () => void; v2Locked: boolean; onGenerate: () => void;
}) {
  const lk = !!c.locked;
  const sp = speedOf(c);
  const cls = sp < 0.5 || sp > 2 ? "red" : sp < 0.67 || sp > 1.5 ? "yellow" : "ok";
  const [sIn, setSIn] = useState(""); const [sOut, setSOut] = useState(""); const [spd, setSpd] = useState("");
  useEffect(() => { setSIn((c.s0 / 1000).toFixed(2)); setSOut((c.s1 / 1000).toFixed(2)); setSpd(sp.toFixed(2)); }, [c.s0, c.s1, c.t0, c.t1]); // eslint-disable-line
  const edited = c.orig && ["t0", "t1", "s0", "s1"].some((k) => Math.abs((c as any)[k] - (c.orig as any)[k]) > 1);
  return (
    <div className="insp">
      <div className="insp-title">{track === 2 && <span className="chip chip-violet" title="This clip is on V2, over V1">V2</span>}{label(c)} <span className="arrow">←</span> {S} {fmt(c.s0)} {lip && <span className="chip chip-violet" title="lip-sync row">lips</span>}
        {lk && <span className="chip chip-grey" title="Locked: no edit can change it">🔒 locked</span>}</div>
      <div className="insp-block"><div className="lbl">{D} line · {fmt(c.t0)}–{fmt(c.t1)} · {((c.t1 - c.t0) / 1000).toFixed(2)} s</div>{c.dub_text || <span className="muted">—</span>}</div>
      <div className="insp-block"><div className="lbl">{S} footage · {((c.s1 - c.s0) / 1000).toFixed(2)} s</div>{c.src_text || <span className="muted">—</span>}</div>
      <div className="row" style={{ alignItems: "baseline", gap: 10 }}>
        <span className={`speed ${cls}`}>×{sp.toFixed(2)}</span>
        <span className="muted small-text">{sp < 0.97 ? "slow motion" : sp > 1.03 ? "faster than real" : "real speed"}</span>
      </div>
      <button className={`gen-btn ${sp < 0.67 ? "hot" : ""}`} onClick={onGenerate}
        title="Make a new video of this moment at natural speed with AI (frames + analysis + prompt + chat), then put it on V2">
        ✦ Generate this clip with AI{sp < 0.67 ? ` (plays ×${sp.toFixed(2)})` : ""}</button>
      <fieldset className="kv" disabled={lk}>
        <label>Source in (s)<input value={sIn} onChange={(e) => setSIn(e.target.value)} onBlur={() => { const v = parseFloat(sIn) * 1000; if (isFinite(v) && v < c.s1 - 50) onChange({ ...c, s0: Math.max(0, v) }); }} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} /></label>
        <label>Source out (s)<input value={sOut} onChange={(e) => setSOut(e.target.value)} onBlur={() => { const v = parseFloat(sOut) * 1000; if (isFinite(v) && v > c.s0 + 50) onChange({ ...c, s1: Math.min(v, srcDur) }); }} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} /></label>
        <label>Speed ×<input value={spd} onChange={(e) => setSpd(e.target.value)} onBlur={() => { const v = parseFloat(spd); if (isFinite(v) && v > 0) onChange(setSpeed(c, v, srcDur)); }} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} /></label>
        <label>Real speed<button className="small" onClick={() => onChange(setSpeed(c, 1, srcDur))}>×1.00</button></label>
      </fieldset>
      <fieldset disabled={lk}>
        <div className="lbl muted small-text" style={{ marginBottom: 5 }}>Slip the footage</div>
        <div className="nudge">
          {[[-1000, "−1s"], [-250, "−¼s"], [-FRAME, "−1f"], [FRAME, "+1f"], [250, "+¼s"], [1000, "+1s"]].map(([d, l]) =>
            <button key={l as string} onClick={() => onChange(slip(c, d as number, srcDur))}>{l}</button>)}
        </div>
      </fieldset>
      <MotionBlock c={c} locked={lk} onMotion={onMotion} />
      {fl.length > 0 && <div className="insp-flags">
        {fl.map((f, k) => <div key={k} className="insp-flag"><span className={`fdot ${f.approved ? "ok" : f.sev}`} style={{ width: 8, height: 8, borderRadius: 4, display: "inline-block" }} />{f.msg}</div>)}
      </div>}
      <div className="insp-actions">
        <button className={`btn-approve ${approved ? "on" : ""}`} onClick={onApprove} title="Approve (A): its flags turn green">{approved ? "✓ Approved" : "Approve"}</button>
        <button onClick={onPlay}>▶ Play</button>
        <button onClick={onLock} title="Lock / unlock (⇧L): a locked clip cannot be changed by any edit">{lk ? "🔓 Unlock" : "🔒 Lock"}</button>
        <button onClick={onFitSentence} disabled={lk} title="Show exactly the matched source sentence">Fit sentence</button>
        {edited && <button className="ghost" disabled={lk} onClick={onReset}>Reset clip</button>}
        <button className="ghost danger" disabled={lk} onClick={onDelete} title={track === 2 ? "Remove from V2 (V1 shows again)" : "Delete leaves a gap to fill (⇧Delete: the clip before takes its place)"}>Delete</button>
      </div>
      <div className="insp-actions">
        <button className="small" onClick={onCopy} title="Copy this clip (⌘C). ⌘V then pastes it on V2 at the playhead">Copy footage</button>
        <button className="small" disabled={!clipboard || lk} onClick={() => onPaste(false)} title="Paste the copied footage onto this clip, fitted to its slot">Paste onto clip{clipboard ? ` ${clipboard}` : ""}</button>
        <button className="small ghost" disabled={!clipboard || lk} onClick={() => onPaste(true)} title="Paste from the copied in-point at the copied clip's speed (⇧⌘V onto this clip)">… keep speed</button>
        {track === 1 && <button className="small ghost" onClick={onCopyToV2} title="Put a copy of this footage on V2 above it (then change the V2 copy; V1 stays as it was)">⧉ Copy to V2</button>}
        <button className="small" disabled={!clipboard || v2Locked} onClick={onPasteV2} title="Paste the copied clip on V2 at the playhead, same length and speed (⌘V)">▲ Paste on V2</button>
      </div>
    </div>
  );
}

// ---------------- subtitle cue ----------------
function CueEditor({ c, k, n, lang, D, prev, next, t, total, edited, onChange, onSplit, onMerge, onDelete, onPlay, onRevert, onRetranslate }: {
  c: Cue; k: number; n: number; lang: Lang; D: string; prev?: Cue; next?: Cue; t: number; total: number; edited: boolean;
  onChange: (c: Cue) => void; onSplit: () => void; onMerge: (dir: 1 | -1) => void; onDelete: () => void; onPlay: () => void; onRevert: () => void;
  onRetranslate?: () => void;
}) {
  const [busyTr, setBusyTr] = useState(false);
  const [text, setText] = useState(c.text); const [a, setA] = useState(""); const [b, setB] = useState("");
  useEffect(() => { setText(c.text); }, [c.text]);
  useEffect(() => { setA(fmt(c.t0, 2)); setB(fmt(c.t1, 2)); }, [c.t0, c.t1]);
  const lo = prev ? prev.t1 : 0, hi = next ? next.t0 : total;
  const parse = (x: string) => { const m = x.trim().match(/^(?:(\d+):)?(\d+(?:[.,]\d+)?)$/); return m ? Math.round(((Number(m[1] || 0) * 60) + Number(m[2].replace(",", "."))) * 1000) : NaN; };
  const setTimes = (t0: number, t1: number) => {
    t0 = clamp(Math.round(t0), lo, hi - 100); t1 = clamp(Math.round(t1), t0 + 100, hi);
    if (t0 !== c.t0 || t1 !== c.t1) onChange({ ...c, t0, t1 });
  };
  const saveText = () => { const v = text.split("\n").map((x) => x.trim()).filter(Boolean).join("\n"); if (v && v !== c.text) onChange({ ...c, text: v }); else setText(c.text); };
  const lines = c.text.split("\n").length, words = c.text.split(/\s+/).filter(Boolean).length, dur = (c.t1 - c.t0) / 1000;
  const inside = t > c.t0 + 150 && t < c.t1 - 150;
  return (
    <div className="insp">
      <div className="insp-title">{lang === "dub" ? `S1 ${D}` : "S2 EN"} subtitle <span className="muted small-text" style={{ fontWeight: 400 }}>{k + 1} of {n}</span>
        {edited && <span className="chip chip-violet" title="This track is edited: the subtitle video is made from the edit">edited</span>}</div>
      <textarea className="cue-text" value={text} rows={3} onChange={(e) => setText(e.target.value)} onBlur={saveText}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) (e.target as HTMLTextAreaElement).blur(); if (e.key === "Escape") { setText(c.text); (e.target as HTMLTextAreaElement).blur(); } }} />
      <div className={`muted small-text ${lines > 2 || dur < 0.7 ? "warn-text" : ""}`}>{lines} line{lines > 1 ? "s" : ""} · {words} words · {dur.toFixed(2)} s{lines > 2 ? " · more than 2 lines" : ""}{dur < 0.7 ? " · very short" : ""}</div>
      <div className="kv">
        <label>Start<input value={a} onChange={(e) => setA(e.target.value)} onBlur={() => { const v = parse(a); if (isFinite(v)) setTimes(v, c.t1); else setA(fmt(c.t0, 2)); }} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} /></label>
        <label>End<input value={b} onChange={(e) => setB(e.target.value)} onBlur={() => { const v = parse(b); if (isFinite(v)) setTimes(c.t0, v); else setB(fmt(c.t1, 2)); }} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} /></label>
        <label>Start = playhead<button className="small" onClick={() => setTimes(t, c.t1)}>⇤ {fmt(t)}</button></label>
        <label>End = playhead<button className="small" onClick={() => setTimes(c.t0, t)}>{fmt(t)} ⇥</button></label>
      </div>
      <div className="insp-actions">
        <button onClick={onPlay}>▶ Play</button>
        <button onClick={onSplit} disabled={!inside} title="Split the cue in two at the playhead (/)">Split at playhead</button>
        <button onClick={() => onMerge(-1)} disabled={!prev} title="Join with the cue before">⇠ Merge</button>
        <button onClick={() => onMerge(1)} disabled={!next} title="Join with the cue after">Merge ⇢</button>
        <button className="ghost danger" onClick={onDelete} title="Delete the cue (Delete)">Delete</button>
        {onRetranslate && <button disabled={busyTr} onClick={async () => { setBusyTr(true); try { await onRetranslate(); } finally { setBusyTr(false); } }}
          title={`Translate the ${D} line(s) under this cue again with the AI`}>{busyTr ? "Translating…" : `↻ Re-translate from ${D}`}</button>}
      </div>
      <div className="muted small-text">Drag a cue on the timeline to move it, its edges to change its times. Changes save by themselves and are burned into the subtitle video at the next Render; the show folder's SRT is never changed.</div>
      {edited && <div><button className="small ghost" onClick={onRevert}>Back to the pipeline's SRT</button></div>}
    </div>
  );
}

// ---------------- mapping: dub rows and their source, side by side ----------------
function MappingPanel({ clips, sents, flags, sel, D, S, t, onPick, onDrop, onPreview }: {
  clips: Clip[]; sents: Sentence[]; flags: Record<string, Flag[]>; sel: string | null; D: string; S: string; t: number;
  onPick: (i: number) => void; onDrop: (i: number, idx: number) => void; onPreview: (s: Sentence) => void;
}) {
  const [q, setQ] = useState(""); const [over, setOver] = useState<number | null>(null);
  const rowsEl = useRef<HTMLDivElement>(null); const srcEl = useRef<HTMLDivElement>(null);
  const selI = clips.findIndex((c) => c.id === sel);
  const cur = selI >= 0 ? clips[selI] : clips[findClip(clips, t)];
  const usedBy = useMemo(() => {
    const m = new Map<number, string[]>();
    sents.forEach((x) => clips.forEach((c) => { if (Math.min(c.s1, x.end_ms) - Math.max(c.s0, x.start_ms) > 300) m.set(x.idx, [...(m.get(x.idx) || []), label(c)]); }));
    return m;
  }, [sents, clips]);
  // keep the selected row and the source near its footage in view
  useEffect(() => { rowsEl.current?.querySelector(".map-row.sel")?.scrollIntoView({ block: "nearest" }); }, [sel]);
  useEffect(() => {
    if (q || !cur) return;
    const k = sents.findIndex((x) => x.end_ms > cur.s0);
    (srcEl.current?.children[Math.max(0, k - 2)] as HTMLElement | undefined)?.scrollIntoView({ block: "start" });
  }, [cur?.id, q]); // eslint-disable-line
  const list = sents.filter((x) => !q || x.text.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="mapping">
      <div className="map-col">
        <div className="map-head">{D} line → {S} footage <span className="muted">· drop a sentence on a row</span></div>
        <div className="pane-body" ref={rowsEl}>
          {clips.map((c, i) => {
            const w = worst(flags[c.id]);
            return (
              <div key={c.id} className={`map-row ${c.id === sel ? "sel" : ""} ${over === i ? "drop" : ""} ${c.locked ? "locked" : ""}`} onClick={() => onPick(i)}
                onDragOver={(e) => { if (e.dataTransfer.types.includes("text/x-src-sentence")) { e.preventDefault(); setOver(i); } }}
                onDragLeave={() => setOver((o) => (o === i ? null : o))}
                onDrop={(e) => { e.preventDefault(); setOver(null); const v = e.dataTransfer.getData("text/x-src-sentence"); if (v) onDrop(i, Number(v)); }}>
                <span className={`fdot ${w || "none"}`} />
                <div className="map-dub"><span className="ft">{label(c)} {fmt(c.t0)}</span>{c.dub_text}</div>
                <div className="map-src"><span className="ft">{fmt(c.s0)} ×{speedOf(c).toFixed(2)}</span>{c.src_text || <span className="muted">—</span>}</div>
              </div>
            );
          })}
        </div>
      </div>
      <div className="map-col src">
        <input className="search" placeholder={`Search the ${S} text…`} value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="pane-body" ref={srcEl}>
          {list.map((x) => {
            const by = usedBy.get(x.idx);
            return (
              <div key={x.idx} className={`sent-item drag ${by ? "used" : ""}`} draggable title="Drag onto a row (or a clip on the timeline) · click: preview on the source monitor"
                onDragStart={(e) => { e.dataTransfer.setData("text/x-src-sentence", String(x.idx)); e.dataTransfer.effectAllowed = "copy"; }}
                onClick={() => onPreview(x)}>
                <span className="ft">S{x.idx} {fmt(x.start_ms)}</span>{x.text}{by ? <span className="tag used-by">{by.slice(0, 2).join(", ")}{by.length > 2 ? "…" : ""}</span> : <span className="tag">unused</span>}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ---------------- source sentences ----------------
function SourceList({ sents, clips, onPick, onUse, canUse }: { sents: Sentence[]; clips: Clip[]; onPick: (s: Sentence) => void; onUse: (s: Sentence) => void; canUse: boolean }) {
  const [q, setQ] = useState("");
  const used = useMemo(() => {
    const u = new Set<number>();
    sents.forEach((s) => { if (clips.some((c) => Math.min(c.s1, s.end_ms) - Math.max(c.s0, s.start_ms) > 300)) u.add(s.idx); });
    return u;
  }, [sents, clips]);
  const list = sents.filter((s) => !q || s.text.toLowerCase().includes(q.toLowerCase()));
  return <>
    <input className="search" placeholder="Search the source text…" value={q} onChange={(e) => setQ(e.target.value)} />
    <div className="pane-body">
      {list.map((s) => (
        <div key={s.idx} className={`sent-item ${used.has(s.idx) ? "used" : ""}`} onClick={() => onPick(s)} onDoubleClick={() => canUse && onUse(s)}
          draggable onDragStart={(e) => { e.dataTransfer.setData("text/x-src-sentence", String(s.idx)); e.dataTransfer.effectAllowed = "copy"; }}
          title={canUse ? "Click: preview on the source monitor · double-click: put it on the selected clip" : "Click: preview on the source monitor"}>
          <span className="ft">{fmt(s.start_ms)}</span>{s.text}{!used.has(s.idx) && <span className="tag">unused</span>}
        </div>
      ))}
    </div>
  </>;
}

// ---------------- timeline ----------------
const H = { ruler: 24, marks: 16, flags: 14, v2: 46, v1: 64, auto: 30, src: 44, a1: 46, s: 26 };

function TimelineView(p: {
  eid: string; thumbRev: number; clips: Clip[]; flags: Record<string, Flag[]>; approved: string[]; total: number; zoom: number; setZoom: (z: number) => void; fitZoom: () => void;
  tool: Tool; sel: string | null; setSel: (id: string | null) => void; seek: (t: number) => void; subs: { dub: Cue[]; en: Cue[] }; labels: string[]; lipSet: Set<number>;
  scroller: React.RefObject<HTMLDivElement>; scrollerW: React.MutableRefObject<number>; playheadEl: React.RefObject<HTMLDivElement>; t: number; srcDur: number;
  onRoll: (i: number, t: number, done: boolean) => void; onSlip: (track: 1 | 2, i: number, d: number, done: boolean, base: Clip) => void; onRazor: (t: number) => void;
  onSwap: (i: number, j: number) => void; onSnapshot: () => Snap; pushUndo: (s: Snap) => void;
  markers: Marker[]; snapOn: boolean; setSnapOn: (f: (x: boolean) => boolean) => void; snapPts: number[]; srcStarts: number[]; tRef: React.MutableRefObject<number>;
  selCue: { lang: Lang; k: number } | null; onCuePick: (lang: Lang, k: number) => void; onCueAdd: (lang: Lang) => void;
  onCueDrag: (lang: Lang, cues: Cue[], done: boolean, base: Snap) => void; isLocked: (...idx: number[]) => boolean;
  onDropSentence: (i: number, v: string | number) => void; onTrack: (lang: Lang) => void; holes: Hole[];
  trans: TransInfo; onTransition: (id: string, st: string | null) => void;
  v2: Clip[]; flat: Piece[]; cutIds: Set<string>;
  onTrim: (track: 1 | 2, i: number, side: "l" | "r", t: number, done: boolean, base: Snap) => void;
  onMoveV2: (i: number, t0: number, done: boolean, base: Snap) => void; onDropV2: (t: number, v: string | number) => void;
  onCopyToV2: (c: Clip, t: number) => void; onFillGap: (g: { t0: number; t1: number }, how: "before" | "after") => void;
  onDropGap: (g: { t0: number; t1: number }, v: string | number) => void;
  onClipToGap: (c: Clip, g: { t0: number; t1: number }) => void; onPasteGap: (g: { t0: number; t1: number }) => void; clipboard: string | null;
  tracks: Tracks; onRow: (key: "v1" | "v2" | "src", what: "hidden" | "locked") => void; srcSegs: SrcSeg[]; srcUsedBy: string[][];
  onSrcPick: (g: SrcSeg) => void;
  onSrcDrop: (g: SrcSeg, to: { kind: "v2"; t: number } | { kind: "gap"; g: { t0: number; t1: number } } | { kind: "clip"; i: number }) => void;
}) {
  const { clips, zoom, total } = p;
  const [gmenu, setGmenu] = useState<{ t0: number; t1: number } | null>(null);
  const [v2over, setV2over] = useState<number | null>(null);         // drop position on V2 while dragging
  const [gapOver, setGapOver] = useState<number | null>(null);       // t0 of the gap a clip is dragged over
  const gapAtMs = (t: number) => p.flat.find((q) => q.gap && q.t0 <= t && t < q.t1);
  const v2Lane = useRef<HTMLDivElement>(null); const v1Lane = useRef<HTMLDivElement>(null);
  const srcH = p.tracks.src?.hidden ? 18 : H.src;
  // the automatic edit (before the scene edit), read-only, under V1 when switched on
  const [autoOn, setAutoOn] = useState(false); const [autoClips, setAutoClips] = useState<Clip[] | null>(null);
  const autoH = autoOn && autoClips ? H.auto : 0;
  const toggleAuto = () => { if (autoOn) { setAutoOn(false); return; }
    if (autoClips) { setAutoOn(true); return; }
    api.get<{ clips: Clip[] }>(`/api/episodes/${p.eid}/timeline/auto`).then((r) => { setAutoClips(r.clips); setAutoOn(true); })
      .catch(() => alert("This episode has no automatic edit kept: the scene edit has not run for it.")); };
  const [srcDrag, setSrcDrag] = useState<number | null>(null);       // the US piece being dragged
  const pxms = zoom / 1000;
  const srcEnd = p.srcSegs.length ? p.srcSegs[p.srcSegs.length - 1].s1 : total;
  const W = Math.ceil(Math.max(total, srcEnd) * pxms) + 200;
  const [view, setView] = useState({ left: 0, width: 1400 });
  const [drop, setDrop] = useState<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [snapLine, setSnapLine] = useState<number | null>(null);
  const [tmenu, setTmenu] = useState<string | null>(null);          // clip id whose incoming cut's menu is open
  useEffect(() => { const f = () => { setTmenu(null); setGmenu(null); }; document.addEventListener("close-tmenu", f); document.addEventListener("pointerdown", f); return () => { document.removeEventListener("close-tmenu", f); document.removeEventListener("pointerdown", f); }; }, []);
  // snap t (ms on the timeline) to cuts, dub sentence starts, markers and the playhead, leaving out `skip`
  // (drags pass the points as they were when the drag started, so a moving cut never snaps to itself)
  const snapT = (t: number, skip: number[] = [], from: number[] = p.snapPts) => {
    if (!p.snapOn) { setSnapLine(null); return t; }
    const pts = from.filter((x) => !skip.some((y) => Math.abs(x - y) < 1)).concat(skip.some((y) => Math.abs(y - p.tRef.current) < 1) ? [] : [p.tRef.current]);
    const s = snapTo(t, pts, pxms); setSnapLine(s); return s ?? t;
  };
  const [wave, setWave] = useState<{ per_s: number; peaks: number[] } | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => { api.get<any>(`/api/episodes/${p.eid}/waveform`).then(setWave).catch(() => {}); }, [p.eid]);
  useEffect(() => {
    const sc = p.scroller.current; if (!sc) return;
    const on = () => { setView({ left: sc.scrollLeft, width: sc.clientWidth }); p.scrollerW.current = sc.clientWidth; };
    on(); sc.addEventListener("scroll", on, { passive: true }); window.addEventListener("resize", on);
    return () => { sc.removeEventListener("scroll", on); window.removeEventListener("resize", on); };
  }, []); // eslint-disable-line
  // keep the playhead where it is on screen when zooming
  const prevZoom = useRef(zoom);
  useEffect(() => {
    const sc = p.scroller.current; if (!sc || prevZoom.current === zoom) return;
    const x = (p.t / 1000) * prevZoom.current - sc.scrollLeft; sc.scrollLeft = (p.t / 1000) * zoom - x; prevZoom.current = zoom;
    if (p.playheadEl.current) p.playheadEl.current.style.transform = `translateX(${(p.t / 1000) * zoom}px)`;
  }, [zoom]); // eslint-disable-line
  // waveform of the visible stretch
  useEffect(() => {
    const cv = canvas.current; if (!cv || !wave) return;
    const dpr = window.devicePixelRatio || 1; const w = view.width + 400, h = H.a1 - 8;
    cv.width = w * dpr; cv.height = h * dpr; cv.style.width = `${w}px`; cv.style.height = `${h}px`; cv.style.left = `${Math.max(0, view.left - 200)}px`;
    const g = cv.getContext("2d")!; g.scale(dpr, dpr); g.clearRect(0, 0, w, h);
    const grad = g.createLinearGradient(0, 0, 0, h); grad.addColorStop(0, "#5eead4"); grad.addColorStop(1, "#2bb39a"); g.fillStyle = grad;
    const x0 = Math.max(0, view.left - 200);
    for (let x = 0; x < w; x += 2) {
      const ms0 = ((x0 + x) / pxms), ms1 = ((x0 + x + 2) / pxms);
      const k0 = Math.floor((ms0 / 1000) * wave.per_s), k1 = Math.max(k0 + 1, Math.floor((ms1 / 1000) * wave.per_s));
      let m = 0; for (let k = k0; k < k1 && k < wave.peaks.length; k++) m = Math.max(m, wave.peaks[k]);
      const bh = Math.max(1, (m / 100) * (h - 2)); g.fillRect(x, (h - bh) / 2, 1.4, bh);
    }
  }, [wave, view, zoom]); // eslint-disable-line

  const vis = (t0: number, t1: number) => t1 * pxms >= view.left - 300 && t0 * pxms <= view.left + view.width + 300;
  const msAt = (clientX: number) => { const sc = p.scroller.current!; const r = sc.getBoundingClientRect(); return (clientX - r.left + sc.scrollLeft) / pxms; };

  // ruler ticks
  const steps = [1, 2, 5, 10, 15, 30, 60, 120]; const step = steps.find((s) => s * zoom >= 90) || 300;
  const ticks: number[] = []; for (let s = Math.floor(view.left / zoom / step) * step; s * zoom < view.left + view.width + 100; s += step) if (s >= 0) ticks.push(s);

  const R = H.ruler + H.marks;
  const V = R + H.flags;
  const VA = V + H.v2 + H.v1 + autoH;                 // after V1 (and the automatic-edit row when it is on)
  const top = { marks: H.ruler, flags: R, v2: V, v1: V + H.v2, auto: V + H.v2 + H.v1, src: VA, a1: VA + srcH,
                s1: VA + srcH + H.a1, s2: VA + srcH + H.a1 + H.s };
  const inV1 = (clientY: number) => { const r = v1Lane.current?.getBoundingClientRect(); return !!r && clientY >= r.top && clientY <= r.bottom; };
  // a US piece: click = to the source monitor; drag onto a V1 clip (replace), a gap (fill) or V2 (over V1)
  function srcDown(e: React.PointerEvent, k: number) {
    if (e.button !== 0) return; e.stopPropagation();
    const g = p.srcSegs[k]; const x0 = e.clientX, y0 = e.clientY; let moved = false;
    const mv = (ev: PointerEvent) => {
      if (!moved && Math.abs(ev.clientX - x0) < 6 && Math.abs(ev.clientY - y0) < 6) return;
      moved = true; setSrcDrag(k);
      const t = msAt(ev.clientX);
      if (inV2(ev.clientY)) { setDrop(null); setGapOver(null); setV2over(t); return; }
      setV2over(null);
      if (inV1(ev.clientY)) { const gp = gapAtMs(t); if (gp) { setGapOver(gp.t0); setDrop(null); } else { setGapOver(null); const j = findClip(clips, t); setDrop(clips[j]?.id || null); } return; }
      setDrop(null); setGapOver(null);
    };
    const up = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up);
      setSrcDrag(null); setDrop(null); setGapOver(null); setV2over(null);
      if (!moved) { p.onSrcPick(g); return; }
      const t = msAt(ev.clientX);
      if (inV2(ev.clientY)) { p.onSrcDrop(g, { kind: "v2", t: snapT(t) }); setSnapLine(null); return; }
      if (inV1(ev.clientY)) {
        const gp = gapAtMs(t); if (gp) { p.onSrcDrop(g, { kind: "gap", g: { t0: gp.t0, t1: gp.t1 } }); return; }
        const j = clips.findIndex((c) => c.t0 <= t && t < c.t1); if (j >= 0) p.onSrcDrop(g, { kind: "clip", i: j });
      }
    };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }
  const rowBtns = (key: "v1" | "v2" | "src") => { const st = (p.tracks as any)[key] || {}; return (
    <span className="row-btns">
      <button className={`rb ${st.hidden ? "off" : ""}`} title={st.hidden ? "Hidden — click to show" + (key === "src" ? "" : " (it plays and renders again)") : "Hide" + (key === "src" ? " (fold the row away)" : " — it then neither plays nor renders")} onClick={() => p.onRow(key, "hidden")}>{st.hidden ? "◌" : "👁"}</button>
      {key !== "src" && <button className={`rb ${st.locked ? "on" : ""}`} title={st.locked ? "Locked — click to unlock" : "Lock the row: nothing on it can change"} onClick={() => p.onRow(key, "locked")}>{st.locked ? "🔒" : "🔓"}</button>}
    </span>); };
  const inV2 = (clientY: number) => { const r = v2Lane.current?.getBoundingClientRect(); return !!r && clientY >= r.top && clientY <= r.bottom; };

  function clipDown(e: React.PointerEvent, i: number, track: 1 | 2 = 1) {
    if (e.button !== 0) return;
    const list = track === 1 ? clips : p.v2; const c = list[i]; e.stopPropagation();
    if (p.tool === "razor") { p.onRazor(snapT(msAt(e.clientX))); setSnapLine(null); return; }
    p.setSel(c.id);
    const x0 = e.clientX; let moved = false;
    if (p.tool === "slip") {
      if (c.locked) { p.isLocked(track === 1 ? i : -1); return; }
      const base = c;
      // slip snaps the footage's first frame to a source sentence start
      const dAt = (ev: PointerEvent) => {
        const d = -(ev.clientX - x0) / pxms * speedOf(base);
        if (!p.snapOn) return d;
        const s = snapTo(base.s0 + d, p.srcStarts, pxms / Math.max(speedOf(base), 0.05));
        setSnapLine(s == null ? null : base.t0); return s == null ? d : s - base.s0;
      };
      const mv = (ev: PointerEvent) => { moved = true; p.onSlip(track, i, dAt(ev), false, base); };
      const up = (ev: PointerEvent) => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); setSnapLine(null); if (moved) p.onSlip(track, i, dAt(ev), true, base); };
      window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up); return;
    }
    const grab = msAt(e.clientX) - c.t0;                            // where in the clip it was picked up
    if (track === 2) {                                              // V2: drag = move along V2
      const base = p.onSnapshot(); const pts = p.snapPts;
      const at = (ev: PointerEvent) => snapT(msAt(ev.clientX) - grab, [c.t0, c.t1], pts);
      const mv = (ev: PointerEvent) => { if (!moved && Math.abs(ev.clientX - x0) < 4) return; moved = true; p.onMoveV2(i, at(ev), false, base); };
      const up = (ev: PointerEvent) => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); setSnapLine(null); if (moved) p.onMoveV2(i, at(ev), true, base); else p.seek(msAt(ev.clientX)); };
      window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up); return;
    }
    // V1, select tool: drag onto another clip = swap footage; drag up onto V2 = a copy of its footage there
    const mv = (ev: PointerEvent) => {
      if (!moved && Math.abs(ev.clientX - x0) < 6 && Math.abs(ev.clientY - e.clientY) < 6) return;
      moved = true; setDragId(c.id);
      if (inV2(ev.clientY)) { setDrop(null); setGapOver(null); setV2over(msAt(ev.clientX) - grab); return; }
      setV2over(null);
      const g = gapAtMs(msAt(ev.clientX));
      if (g) { setDrop(null); setGapOver(g.t0); return; }
      setGapOver(null);
      const j = findClip(clips, msAt(ev.clientX)); setDrop(j !== i ? clips[j].id : null);
    };
    const up = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up);
      setDragId(null); setDrop(null); setV2over(null); setGapOver(null);
      if (moved && inV2(ev.clientY)) { p.onCopyToV2(c, snapT(msAt(ev.clientX) - grab)); setSnapLine(null); return; }
      const g = moved ? gapAtMs(msAt(ev.clientX)) : undefined;
      if (g) { p.onClipToGap(c, { t0: g.t0, t1: g.t1 }); return; }                // a clip dropped on a gap fills it
      if (moved) { const j = findClip(clips, msAt(ev.clientX)); if (j !== i) p.onSwap(i, j); }
      else p.seek(msAt(ev.clientX));
    };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }
  // a real trim of one edge (⌥/Alt held on V1 = the old roll: the cut moves, both clips change)
  function trimDown(e: React.PointerEvent, track: 1 | 2, i: number, side: "l" | "r") {
    e.stopPropagation(); if (e.button !== 0) return;
    const list = track === 1 ? clips : p.v2; const c = list[i];
    if (e.altKey && track === 1) {
      const k = side === "r" ? i : i - 1;
      if (k >= 0 && k < clips.length - 1 && Math.abs(clips[k].t1 - clips[k + 1].t0) < 1) { edgeDown(e, k); return; }
    }
    const base = p.onSnapshot(); const edge = side === "r" ? c.t1 : c.t0; const pts = p.snapPts; let moved = false;
    const at = (ev: PointerEvent) => snapT(msAt(ev.clientX), [edge], pts);
    const mv = (ev: PointerEvent) => { moved = true; p.onTrim(track, i, side, at(ev), false, base); };
    const up = (ev: PointerEvent) => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); setSnapLine(null); if (moved) p.onTrim(track, i, side, at(ev), true, base); };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }
  function edgeDown(e: React.PointerEvent, i: number) {     // boundary between clip i and i+1
    e.stopPropagation(); if (i < 0 || i >= clips.length - 1) return;
    if (p.isLocked(i, i + 1)) return;
    const snap = p.onSnapshot(); let moved = false; const cut = clips[i].t1; const pts = p.snapPts;
    const mv = (ev: PointerEvent) => { moved = true; p.onRoll(i, snapT(msAt(ev.clientX), [cut], pts), false); };
    const up = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up);
      if (moved) { p.pushUndo(snap); p.onRoll(i, snapT(msAt(ev.clientX), [cut], pts), false); }
      setSnapLine(null);
    };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }

  // a subtitle cue: drag the body to move it, an edge to change one time; it never crosses its neighbours
  function cueDown(e: React.PointerEvent, lang: Lang, k: number) {
    if (e.button !== 0) return; e.stopPropagation();
    p.onCuePick(lang, k);
    const cues = p.subs[lang]; const c = cues[k]; const base = p.onSnapshot();
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const edgePx = Math.min(7, r.width / 4);
    const mode = e.clientX - r.left < edgePx ? "l" : r.right - e.clientX < edgePx ? "r" : "move";
    const lo = k > 0 ? cues[k - 1].t1 : 0, hi = k < cues.length - 1 ? cues[k + 1].t0 : total;
    const x0 = e.clientX; let moved = false;
    const at = (ev: PointerEvent): Cue => {
      const d = (ev.clientX - x0) / pxms;
      if (mode === "l") return { ...c, t0: Math.round(clamp(snapT(c.t0 + d, [c.t0]), lo, c.t1 - 100)) };
      if (mode === "r") return { ...c, t1: Math.round(clamp(snapT(c.t1 + d, [c.t1]), c.t0 + 100, hi)) };
      const len = c.t1 - c.t0; let t0 = snapT(c.t0 + d, [c.t0]); t0 = clamp(t0, lo, hi - len);
      return { ...c, t0: Math.round(t0), t1: Math.round(t0 + len) };
    };
    const put = (ev: PointerEvent, done: boolean) => { const out = cues.slice(); out[k] = at(ev); p.onCueDrag(lang, out, done, base); };
    const mv = (ev: PointerEvent) => { if (!moved && Math.abs(ev.clientX - x0) < 3) return; moved = true; put(ev, false); };
    const up = (ev: PointerEvent) => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); setSnapLine(null); if (moved) put(ev, true); else p.seek(msAt(ev.clientX)); };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }

  function clipBlock(c: Clip, i: number, track: 1 | 2) {
    const x = c.t0 * pxms, w = Math.max(2, (c.t1 - c.t0) * pxms - 1);
    const fl = p.flags[c.id]; const sev = worst(fl); const sp = speedOf(c);
    return (
      <div key={c.id} className={`clip ${c.broll ? "broll" : ""} ${track === 2 ? "on-v2" : ""} ${sev} ${c.locked ? "locked" : ""} ${p.sel === c.id ? "sel" : ""} ${drop === c.id ? "drop" : ""} ${dragId === c.id ? "dragging" : ""}`}
        style={{ left: x, width: w }} onPointerDown={(e) => clipDown(e, i, track)}
        onDragOver={(e) => { if (e.dataTransfer.types.includes("text/x-src-sentence")) { e.preventDefault(); setDrop(c.id); } }}
        onDragLeave={() => setDrop(null)}
        onDrop={(e) => { const v = e.dataTransfer.getData("text/x-src-sentence"); setDrop(null); if (v) { e.preventDefault(); e.stopPropagation(); if (track === 1) p.onDropSentence(i, v); else { p.setSel(c.id); p.onDropV2(c.t0, v); } } }}
        title={`${track === 2 ? "V2 · " : ""}${c.broll ? "B-roll · " : ""}${c.zoom ? "repeat (zoomed in) · " : ""}${label(c)}  ${fmt(c.t0)}–${fmt(c.t1)}\n${c.dub_text}\n← ${fmt(c.s0)} ${c.src_text}\n×${sp.toFixed(2)}${c.note ? "\nWhy: " + c.note.replace(/^scene edit: /, "") : ""}${fl?.length ? "\n" + fl.map((f) => "• " + f.msg).join("\n") : ""}\nDrag an edge to trim it (⌥ = move the cut)`}>
        <div className="thumbs">{thumbsFor(c, w, track === 2 ? H.v2 : H.v1)}</div>
        {w > 34 && <div className="cl">{c.locked ? "🔒 " : ""}{c.broll ? <span className="broll-tag">B-roll</span> : null}{label(c)}</div>}
        {w > 70 && c.zoom ? <span className="zoom-tag" title={`A repeat of footage used before, shown zoomed in ×${c.zoom}`}>repeat</span> : null}
        {w > 46 && <span className="cs" style={{ color: sp < 0.67 || sp > 1.5 ? "var(--yellow)" : undefined }}>×{sp.toFixed(2)}</span>}
        {w > 80 && c.row != null && p.lipSet.has(c.row) && <span className="lipi">👄</span>}
        {p.tool === "select" && <span className="edge l" onPointerDown={(e) => trimDown(e, track, i, "l")} />}
        {(p.cutIds.has(c.id) || c.tin) && (() => {
          const picked = c.tin;
          const st = picked || p.trans.default;
          const inheritAuto = !picked && p.trans.default === "auto";
          const hard = picked === "none" || (!picked && p.trans.default !== "auto");
          return (
          <span className={`cut-fx ${picked && picked !== "none" ? "on" : ""} ${picked ? "own" : ""} ${inheritAuto ? "def" : ""}`}
            title={`Transition at this cut: ${TLABEL[st] || st}${picked ? "" : " (show default)"} — click to change`}
            onPointerDown={(e) => { e.stopPropagation(); setTmenu(tmenu === c.id ? null : c.id); }}>{hard ? "│" : "✦"}</span>); })()}
        {p.tool === "select" && <span className="edge r" onPointerDown={(e) => trimDown(e, track, i, "r")} />}
      </div>
    );
  }

  const thumbsFor = (c: Clip, w: number, laneH = H.v1) => {
    if (c.media) return null;                                       // an imported clip: no frames from the source video
    const n = w < 16 ? 1 : clamp(Math.ceil(w / Math.max(laneH * 1.2, 40)), 1, 8);
    const span = Math.max((c.s1 || 0) - (c.s0 || 0), 1);
    return Array.from({ length: n }, (_, k) => {
      const ms = Math.round(((c.s0 || 0) + ((k + 0.5) / n) * span) / 500) * 500;
      return <img key={`${p.thumbRev}-${k}`} src={`/api/episodes/${p.eid}/thumb?ms=${ms}&h=80&r=${p.thumbRev}`} loading="lazy" alt="" draggable={false}
        onError={(e) => { const el = e.currentTarget; el.onerror = null; el.remove(); }} />;
    });
  };

  return (
    <div className="tl" style={{ cursor: p.tool === "razor" ? "crosshair" : undefined }}>
      <div className="tl-bar">
        <span>Timeline</span>
        <span className="muted">· {clips.length} clips</span>
        <span className="grow" />
        <span className="muted">{p.tool === "razor" ? "Razor: click a clip to cut it" : p.tool === "slip" ? "Slip: drag a clip to slide its footage" : "Drag an edge to trim (⌥ moves the cut) · drag a clip onto another to swap, or up onto V2"}</span>
        <button className={`small ${autoOn ? "snap-on" : "ghost"}`} onClick={toggleAuto} title="Show the automatic edit (before the scene edit) under V1, read-only">Automatic edit</button>
        <button className={`small ${p.snapOn ? "snap-on" : "ghost"}`} onClick={() => p.setSnapOn((x) => !x)} title="Snapping (S): edges, cues and the playhead stick to cuts, dub sentence starts, markers and the playhead; slip sticks to source sentence starts">🧲 Snap</button>
        <span className="sep" />
        <button className="small ghost" onClick={() => p.setZoom(Math.max(zoom / 1.4, 2))}>−</button>
        <input type="range" min={0} max={100} value={Math.round((Math.log(zoom / 2) / Math.log(300)) * 100)} onChange={(e) => p.setZoom(2 * Math.pow(300, Number(e.target.value) / 100))} />
        <button className="small ghost" onClick={() => p.setZoom(Math.min(zoom * 1.4, 600))}>+</button>
        <button className="small" onClick={p.fitZoom} title="Whole episode (\\)">Fit</button>
      </div>
      <div className="tl-body">
        <div className="tl-heads" style={{ height: top.s2 + H.s + 18 }}>
          <div className="tl-head" style={{ height: H.ruler }} />
          <div className="tl-head" style={{ height: H.marks }} title="QC notes (M adds one at the playhead)">QC</div>
          <div className="tl-head" style={{ height: H.flags }} />
          <div className="tl-head" style={{ height: H.v2 }} title="V2: your own clips over V1 — drop a sentence here, drag a V1 clip up, or put the marked In/Out here (.)">V2{rowBtns("v2")}</div>
          <div className="tl-head" style={{ height: H.v1 }}>V1{rowBtns("v1")}</div>
          {autoH > 0 && <div className="tl-head" style={{ height: autoH }} title="The automatic edit before the scene edit (read-only)">AUTO</div>}
          <div className="tl-head" style={{ height: srcH }} title={`The whole ${p.labels[1]} video in its own time, cut into its sentences: click a piece to see it, drag it onto V1, a gap or V2`}>{p.labels[1]}{rowBtns("src")}</div>
          <div className="tl-head" style={{ height: H.a1 }}>A1 {p.labels[0]}</div>
          <div className="tl-head" style={{ height: H.s }}>S1 {p.labels[0]}<button className="cue-add" title="Track: style, import an SRT" onClick={() => p.onTrack("dub")}>⚙</button><button className="cue-add" style={{ marginLeft: 3 }} title="Add a cue at the playhead" onClick={() => p.onCueAdd("dub")}>+</button></div>
          <div className="tl-head" style={{ height: H.s }}>S2 EN<button className="cue-add" title="Track: style, import an SRT" onClick={() => p.onTrack("en")}>⚙</button><button className="cue-add" style={{ marginLeft: 3 }} title="Add a cue at the playhead" onClick={() => p.onCueAdd("en")}>+</button></div>
        </div>
        <div className="tl-scroll" ref={p.scroller} style={{ height: top.s2 + H.s + 18 }} onWheel={(e) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); p.setZoom(clamp(zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15), 2, 600)); } }}>
          <div className="tl-inner" style={{ width: W }} onPointerDown={(e) => { if (e.button === 0 && p.tool !== "razor") { p.seek(msAt(e.clientX)); } }}>
            <div className="tl-ruler" onPointerDown={(e) => { e.stopPropagation(); const sk = [p.tRef.current]; p.seek(snapT(msAt(e.clientX), sk)); const mv = (ev: PointerEvent) => p.seek(snapT(msAt(ev.clientX), sk)); const up = () => { setSnapLine(null); window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); }; window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up); }}>
              {ticks.map((s) => <div key={s} className="tick" style={{ left: s * zoom }}>{fmt(s * 1000, 0)}</div>)}
            </div>
            <div className="lane lane-marks" style={{ top: top.marks, height: H.marks }}>
              {p.markers.map((m) => vis(m.t_ms, m.t_ms) && <span key={m.id} className={`qc-pin ${m.resolved ? "done" : ""}`} style={{ left: m.t_ms * pxms }}
                title={`${fmt(m.t_ms)} ${m.text}${m.resolved ? " (resolved)" : ""}`} onPointerDown={(e) => { e.stopPropagation(); p.seek(m.t_ms); }}>{zoom > 14 ? m.text : ""}</span>)}
            </div>
            <div className="lane lane-flags" style={{ top: top.flags, height: H.flags }}>
              {[...clips, ...p.v2].map((c) => { const w = worst(p.flags[c.id]); return (w === "red" || w === "yellow") && vis(c.t0, c.t1) ? <span key={c.id} className={`fmark ${w}`} style={{ left: ((c.t0 + c.t1) / 2) * pxms }} title={(p.flags[c.id] || []).map((f) => f.msg).join("\n")} onPointerDown={(e) => { e.stopPropagation(); p.setSel(c.id); p.seek(c.t0); }} /> : null; })}
            </div>
            <div className={`lane lane-v2 ${p.tracks.v2?.hidden ? "row-hidden" : ""} ${p.tracks.v2?.locked ? "row-locked" : ""}`} ref={v2Lane} style={{ top: top.v2, height: H.v2 }}
              onDragOver={(e) => { if (e.dataTransfer.types.includes("text/x-src-sentence")) { e.preventDefault(); setV2over(msAt(e.clientX)); } }}
              onDragLeave={() => setV2over(null)}
              onDrop={(e) => { const v = e.dataTransfer.getData("text/x-src-sentence"); setV2over(null); if (v) { e.preventDefault(); p.onDropV2(snapT(msAt(e.clientX)), v); setSnapLine(null); } }}>
              {!p.v2.length && v2over == null && <span className="v2-hint" style={{ left: view.left + 12 }}>V2 — drop a sentence here, drag a V1 clip up, or press . to put the marked In→Out at the playhead</span>}
              {p.v2.map((c, i) => vis(c.t0, c.t1) && clipBlock(c, i, 2))}
              {v2over != null && <span className="v2-ghost" style={{ left: v2over * pxms }} />}
            </div>
            <div className={`lane ${p.tracks.v1?.hidden ? "row-hidden" : ""} ${p.tracks.v1?.locked ? "row-locked" : ""}`} ref={v1Lane} style={{ top: top.v1, height: H.v1 }}>
              {clips.map((c, i) => vis(c.t0, c.t1) && clipBlock(c, i, 1))}
              {p.flat.map((g, k) => { if (!g.gap || !vis(g.t0, g.t1)) return null;
                const h = p.holes.find((x) => Math.min(x.t1, g.t1) - Math.max(x.t0, g.t0) > 0); return (
                <div key={"gap" + k} className={`gap-block ${h ? "not-found" : ""} ${gapOver === g.t0 ? "drop" : ""}`} style={{ left: g.t0 * pxms, width: Math.max(3, (g.t1 - g.t0) * pxms) }}
                  title={`${h ? `NOT FOUND: no footage matched ${p.labels[0]}#${h.row} "${h.text}" (also after Claude's check) — find a clip for it.\n` : ""}Gap ${fmt(g.t0)}–${fmt(g.t1)}: black screen. Drag a clip, a sentence or an imported clip here, paste a copied clip (⌘V), or click to fill it.`}
                  onPointerDown={(e) => { e.stopPropagation(); setGmenu(gmenu && gmenu.t0 === g.t0 ? null : { t0: g.t0, t1: g.t1 }); }}
                  onDragOver={(e) => { if (e.dataTransfer.types.includes("text/x-src-sentence")) e.preventDefault(); }}
                  onDrop={(e) => { const v = e.dataTransfer.getData("text/x-src-sentence"); if (v) { e.preventDefault(); p.onDropGap({ t0: g.t0, t1: g.t1 }, v); } }}>
                  {(g.t1 - g.t0) * pxms > 40 && <span>{h ? `not found · ${p.labels[0]}#${h.row}` : "gap"}</span>}
                </div>); })}
            </div>
            {autoH > 0 && <div className="lane lane-auto" style={{ top: top.auto, height: autoH }}>
              {autoClips!.map((c, k) => vis(c.t0, c.t1) && <div key={k} className="auto-piece" style={{ left: c.t0 * pxms, width: Math.max(2, (c.t1 - c.t0) * pxms - 1) }}
                title={`Automatic edit (before the scene edit)\n${fmt(c.t0)}–${fmt(c.t1)} ← ${fmt(c.s0)}–${fmt(c.s1)} ×${((c.s1 - c.s0) / Math.max(c.t1 - c.t0, 1)).toFixed(2)}${c.note ? "\n" + c.note : ""}`}
                onPointerDown={(e) => { e.stopPropagation(); p.seek(c.t0); }}>{(c.t1 - c.t0) * pxms > 40 ? (c.row != null ? `#${c.row}` : "") : ""}</div>)}
            </div>}
            <div className="lane lane-src" style={{ top: top.src, height: srcH }}>
              {p.tracks.src?.hidden ? <span className="v2-hint" style={{ left: view.left + 12, top: 2 }}>{p.labels[1]} row hidden — 👁 on its head shows it</span>
                : p.srcSegs.map((g, k) => {
                  if (!vis(g.s0, g.s1)) return null;
                  const x = g.s0 * pxms, w = Math.max(2, (g.s1 - g.s0) * pxms - 1); const used = p.srcUsedBy[k] || [];
                  return (
                    <div key={k} className={`src-piece ${g.idx == null ? "nowords" : ""} ${used.length ? "used" : ""} ${srcDrag === k ? "dragging" : ""}`} style={{ left: x, width: w }}
                      onPointerDown={(e) => srcDown(e, k)}
                      title={`${g.idx != null ? `${p.labels[1]} sentence ${g.idx}` : "footage without words"}  ${fmt(g.s0)}–${fmt(g.s1)}\n${g.text}\n${used.length ? "used by " + used.slice(0, 4).join(", ") : "unused"}\nClick: see it on the source monitor · drag onto a V1 clip, a gap or V2`}>
                      <div className="thumbs">{thumbsFor({ s0: g.s0, s1: g.s1 } as Clip, w, H.src)}</div>
                      {w > 30 && <div className="cl">{g.idx != null ? `S${g.idx}` : "·"}{w > 90 && g.text ? " " + g.text : ""}</div>}
                      {w > 50 && <span className="cs">{used.length ? used[0] + (used.length > 1 ? ` +${used.length - 1}` : "") : "unused"}</span>}
                    </div>);
                })}
            </div>
            <div className="lane" style={{ top: top.a1 + 4, height: H.a1 - 8 }}><canvas ref={canvas} className="wave" /></div>
            <div className="lane" style={{ top: top.s1, height: H.s }}>
              {p.subs.dub.map((c, k) => vis(c.t0, c.t1) && <div key={k} className={`sub it ${p.selCue?.lang === "dub" && p.selCue.k === k ? "sel" : ""}`} style={{ left: c.t0 * pxms, width: Math.max(2, (c.t1 - c.t0) * pxms - 1) }} title={c.text} onPointerDown={(e) => cueDown(e, "dub", k)}>{c.text}</div>)}
            </div>
            <div className="lane" style={{ top: top.s2, height: H.s }}>
              {p.subs.en.map((c, k) => vis(c.t0, c.t1) && <div key={k} className={`sub en ${p.selCue?.lang === "en" && p.selCue.k === k ? "sel" : ""}`} style={{ left: c.t0 * pxms, width: Math.max(2, (c.t1 - c.t0) * pxms - 1) }} title={c.text} onPointerDown={(e) => cueDown(e, "en", k)}>{c.text}</div>)}
            </div>
            {snapLine != null && <div className="snap-line" style={{ left: snapLine * pxms }} />}
            {gmenu && (
              <div className="trans-menu" style={{ left: Math.max(4, gmenu.t0 * pxms), top: top.v1 + H.v1 - 2 }} onPointerDown={(e) => e.stopPropagation()}>
                <div className="tm-head">Gap {fmt(gmenu.t0)}–{fmt(gmenu.t1)} (black)</div>
                <button onClick={() => { p.onFillGap(gmenu, "before"); setGmenu(null); }}>Extend the clip before <span className="tm-note">more of its footage</span></button>
                <button onClick={() => { p.onFillGap(gmenu, "after"); setGmenu(null); }}>Extend the clip after <span className="tm-note">earlier footage</span></button>
                <button disabled={!p.clipboard} onClick={() => { p.onPasteGap(gmenu); setGmenu(null); }}>Paste the copied clip here <span className="tm-note">{p.clipboard ? p.clipboard : "copy one first (⌘C)"}</span></button>
                <div className="tm-note" style={{ padding: "6px 8px" }}>or drag a clip, or a sentence from Mapping / {p.labels[1]} sentences, onto the gap</div>
              </div>
            )}
            {tmenu && (() => { const onV2 = p.v2.some((x) => x.id === tmenu); const c = (onV2 ? p.v2 : clips).find((x) => x.id === tmenu); if (!c) return null; return (
              <div className="trans-menu" style={{ left: Math.max(4, c.t0 * pxms - 90), top: onV2 ? top.v2 + H.v2 - 2 : top.v1 + H.v1 - 2 }} onPointerDown={(e) => e.stopPropagation()}>
                <div className="tm-head">Transition at {fmt(c.t0)}</div>
                {p.trans.styles.map((x, k) => (
                  <button key={x.id} className={c.tin === x.id ? "on" : ""} onClick={() => { p.onTransition(c.id, x.id); setTmenu(null); }}>
                    {x.label}{k === 0 && x.uses ? <span className="tm-note">most used</span> : x.uses ? <span className="tm-note">{x.uses}×</span> : null}</button>))}
                <button className={c.tin === "auto" ? "on" : ""} onClick={() => { p.onTransition(c.id, "auto"); setTmenu(null); }}>Automatic <span className="tm-note">rotation</span></button>
                <button className={c.tin === "none" ? "on" : ""} onClick={() => { p.onTransition(c.id, "none"); setTmenu(null); }}>None (hard cut)</button>
                <button className={!c.tin ? "on" : ""} onClick={() => { p.onTransition(c.id, null); setTmenu(null); }}>Show default <span className="tm-note">{TLABEL[p.trans.default]}</span></button>
              </div>); })()}
            <div className="playhead" ref={p.playheadEl} style={{ transform: `translateX(${(p.t / 1000) * zoom}px)` }} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------- the project bin (like Premiere's Project panel) ----------------
function BinPanel({ eid, bin, clips, srcMedia, labels, onShow, onImport, onImportPath, onRemove, onShowSource, onTrack, onFold }: {
  eid: string; bin: { imports: Imp[]; files: BinFile[] }; clips: Clip[]; srcMedia: string | null; labels: string[];
  onShow: (x: Imp) => void; onImport: (f: FileList | File[]) => void; onImportPath: (path: string) => void; onRemove: (x: Imp) => void;
  onShowSource: () => void; onTrack: (lang: Lang) => void; onFold: () => void;
}) {
  const file = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const mb = (n: number) => (n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`);
  const icon: Record<string, string> = { src_video: "🎞", dub_audio: "🔊", dub_srt: "💬", src_srt: "💬", en_srt: "🌐" };
  return (
    <div className={`bin ${over ? "drop" : ""}`}
      onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setOver(true); } }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { if (e.dataTransfer.files.length) { e.preventDefault(); setOver(false); onImport(e.dataTransfer.files); } }}>
      <div className="bin-head">
        <button className="ghost small fold" onClick={onFold} title="Fold the bin away (the timeline gets the whole width)">◂</button>
        <b>Project</b><span className="grow" />
        <button className="small primary" onClick={() => file.current?.click()} title="Import video clips into this episode (or drop files from Finder here)">＋ Import</button>
        <button className="small ghost" title="Import a file by its path on this Mac (no upload: faster for big files)"
          onClick={() => { const pth = prompt("Path of the video file on this Mac (e.g. /Users/manish/Movies/clip.mov):"); if (pth?.trim()) onImportPath(pth.trim()); }}>Path…</button>
        <input ref={file} type="file" accept="video/*,.mov,.mp4,.m4v,.mkv,.mxf" multiple hidden onChange={(e) => { if (e.target.files?.length) onImport(e.target.files); e.target.value = ""; }} />
      </div>
      <div className="bin-body">
        <div className="bin-sec">Imported clips <span className="muted">{bin.imports.length || ""}</span></div>
        {!bin.imports.length && <div className="bin-empty">Import a clip (＋ Import, or drop video files here), then drag it onto V1, a gap or V2.</div>}
        {bin.imports.map((x) => {
          const n = clips.filter((c) => c.media === x.id).length;
          return (
            <div key={x.id} className={`bin-item ${srcMedia === x.id ? "on" : ""}`} draggable
              onDragStart={(e) => { e.dataTransfer.setData("text/x-src-sentence", `imp:${x.id}`); e.dataTransfer.effectAllowed = "copy"; }}
              onClick={() => onShow(x)} title={`${x.name}\n${fmt(x.duration_ms)} · ${x.width}×${x.height} · ${mb(x.size)}\nClick: see it on the source monitor · drag onto V1, a gap or V2`}>
              <span className="bi-icon">🎬</span>
              <span className="bi-name">{x.name}</span>
              <span className="bi-meta">{fmt(x.duration_ms)}{x.preview === "making" ? " · preparing…" : ""}{n ? ` · used ${n}×` : ""}</span>
              <button className="ghost small danger" title="Remove from the bin" onClick={(e) => { e.stopPropagation(); onRemove(x); }}>×</button>
            </div>);
        })}
        <div className="bin-sec">Episode files</div>
        {bin.files.map((f) => (
          <div key={f.kind} className={`bin-item ${f.kind === "src_video" && !srcMedia ? "on" : ""}`}
            onClick={() => (f.kind === "src_video" ? onShowSource() : f.kind === "dub_srt" ? onTrack("dub") : f.kind === "en_srt" ? onTrack("en") : undefined)}
            title={`${f.name}\n${f.kind === "src_video" ? "Click: show it on the source monitor (its sentences are the " + labels[1] + " row)" : f.kind === "dub_srt" ? "Click: the S1 track (style, import an SRT)" : f.kind === "en_srt" ? "Click: the S2 track (style, import an SRT)" : ""}`}>
            <span className="bi-icon">{icon[f.kind] || "📄"}</span>
            <span className="bi-name">{f.label}<span className="bi-file">{f.name}</span></span>
            <span className="bi-meta">{mb(f.size)}</span>
            {f.kind.endsWith("srt") && <a className="button ghost small" href={`/api/episodes/${eid}/bin/file/${f.kind}`} onClick={(e) => e.stopPropagation()} title="Download">⇩</a>}
          </div>))}
      </div>
    </div>
  );
}

function TransSelect({ info, value, onChange }: { info: TransInfo; value?: string; onChange: (v: string | null) => void }) {
  return (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">{`Show default (${TLABEL[info.default]})`}</option>
      {info.styles.map((x, k) => <option key={x.id} value={x.id}>{x.label}{k === 0 && x.uses ? " (most used)" : ""}</option>)}
      <option value="auto">Automatic (rotation)</option>
      <option value="none">None (hard cut)</option>
    </select>
  );
}

function TrackDialog({ lang, name, show, cur, edited, n, onClose, onSave, onImport, onRevert }: {
  lang: Lang; name: string; show: string; cur: Required<SubStyle>; edited: boolean; n: number;
  onClose: () => void; onSave: (s: SubStyle) => void; onImport: (f: File) => void; onRevert: () => void;
}) {
  const [st, setSt] = useState({ style: cur.style, size: String(cur.size), bottom: String(cur.bottom), alpha: String(Math.round(cur.alpha * 100)) });
  const file = useRef<HTMLInputElement>(null);
  const box = st.style === "box";
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 460 }}>
        <div className="modal-head"><b>Subtitle track {name}</b><button className="ghost" onClick={onClose}>Close</button></div>
        <div style={{ padding: "4px 18px 18px", display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="muted small-text">Style of the {lang === "dub" ? "dub" : "English"} subtitle video for every episode of {show}, used from the next Render. The program monitor shows it.</div>
          <div className="kv">
            <label>Style<select value={st.style} onChange={(e) => { const v = e.target.value as "box" | "strip"; setSt({ ...st, style: v, size: String(STYLE_DEFAULT[v].size) }); }}>
              <option value="box">Box (white text on a soft box)</option><option value="strip">Strip (covers burned-in captions)</option></select></label>
            <label>Text size (px on 1080)<input value={st.size} onChange={(e) => setSt({ ...st, size: e.target.value })} placeholder={String(STYLE_DEFAULT[st.style].size)} /></label>
            {box && <label>From the bottom (px)<input value={st.bottom} onChange={(e) => setSt({ ...st, bottom: e.target.value })} placeholder="71" /></label>}
            {box && <label>Box shade (%)<input value={st.alpha} onChange={(e) => setSt({ ...st, alpha: e.target.value })} placeholder="30" /></label>}
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="primary" onClick={() => onSave(box ? { style: "box", size: Number(st.size), bottom: Number(st.bottom), alpha: Number(st.alpha) / 100 } : { style: "strip", size: Number(st.size) })}>Save style</button>
            <button className="ghost" onClick={() => onSave(box ? { style: "box", ...STYLE_DEFAULT.box } : { style: "strip", size: STYLE_DEFAULT.strip.size })}>House default</button>
          </div>
          <hr style={{ border: 0, borderTop: "1px solid var(--line)", margin: 0 }} />
          <div className="muted small-text">{n} subtitles{edited ? " · edited in Mirror Video" : " · as the pipeline made them"}. Importing an SRT replaces this track (for example a human translation, or when Google translation was blocked); undo with ⌘Z.</div>
          <div className="row" style={{ gap: 8 }}>
            <button onClick={() => file.current?.click()}>Import an SRT…</button>
            <input ref={file} type="file" accept=".srt,.txt" hidden onChange={(e) => e.target.files?.[0] && onImport(e.target.files[0])} />
            {edited && <button className="ghost" onClick={onRevert}>Back to the pipeline's SRT</button>}
          </div>
        </div>
      </div>
    </div>
  );
}

function KeysHelp({ onClose }: { onClose: () => void }) {
  const keys: [string, string][] = [
    ["Space", "play / pause"], ["J / L", "back / forward 2 s"], ["← →", "one frame"], ["⇧← ⇧→", "one second"], ["↑ ↓", "previous / next clip"],
    ["N / ⇧N", "next / previous flag"], ["A", "approve the clip"], ["V", "select tool"], ["C", "razor tool"], ["⇧C", "cut at the playhead"], ["drag an edge", "trim that clip (in = gap, out = overwrite)"], ["⌥ + drag an edge", "move the cut (both clips change)"], [".", "marked In→Out onto V2 at the playhead"], ["⇧Delete", "delete and let the clip before fill"],
    ["Y", "slip tool"], ["I / O", "mark In / Out on the source"], ["F", "replace the clip with In→Out"], ["Delete", "remove the clip (leaves a gap; on V2: V1 shows again)"],
    ["⌘C", "copy: the source clip (start → end) after a click on the source monitor, else the selected clip"], ["⌘V", "paste a copied V1 clip on V2 at the playhead; a source copy still goes onto the selected clip, a gap, or V2"], ["⇧⌘V", "paste keeping the copied speed onto another clip"], ["⇧L", "lock / unlock the clip"],
    ["S", "snapping on / off"], ["M", "QC note at the playhead"], ["/", "split the selected subtitle at the playhead"],
    ["⌘Z / ⇧⌘Z", "undo / redo"], ["⌘S", "save a version"], ["+ / −", "zoom"], ["\\", "fit the whole episode"], ["⌘ + scroll", "zoom at the mouse"], ["Esc", "deselect"],
  ];
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head"><b>Keyboard</b><button className="ghost" onClick={onClose}>Close</button></div>
        <div className="keys">{keys.map(([k, v]) => <div key={k}><span>{v}</span><kbd>{k}</kbd></div>)}</div>
      </div>
    </div>
  );
}
