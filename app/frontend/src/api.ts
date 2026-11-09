// Thin client for the FastAPI backend (/api) and its live event socket (/api/ws).
import { useEffect, useRef } from "react";

export type Profile = Record<string, any>;
export type Show = {
  id: number; name: string; code: string; folder: string; preset: string | null; profile: Profile;
  episode_counts?: Record<string, number>;
};
export type Asset = {
  id: number; kind: string; path: string; size?: number; probe?: any; role: string;
  r2_key?: string | null; origin?: string;          // 'r2' = uploaded in the browser, 'local' = a file on this Mac
};
export type Job = {
  id: number; step: string; label: string; ord: number; status: string; progress: number; item?: string;
  message?: string; resource?: string; attempt?: number; started?: number; finished?: number; eta?: number;
  log_path?: string; check_passed?: boolean; check_lines?: string[];
};
export type Episode = {
  id: number; show_id: number; number: number; status: string; work_dir: string; output_dir: string;
  updated?: number; progress?: number; assets: Asset[]; jobs: Job[]; run?: number; show: Show;
  output_existing: string[]; candidates?: Record<string, string[]>; queue_position?: number;
  batch_id?: number | null; have?: string[]; ready?: boolean;
  step?: string | null; step_label?: string | null; steps_done?: number; steps?: number;
};
export type BatchEpisode = {
  id: number; show_id: number; number: number; status: string; progress?: number | null;
  queue_position?: number; have?: string[]; ready?: boolean; step?: string | null; step_label?: string | null;
  steps_done?: number; steps?: number; run?: number | null; batch_id?: number | null; updated?: number;
};
export type Batch = {
  id: number; show_id: number; start_num: number; end_num: number; status: string;
  created?: number; updated?: number; show?: Show; episodes: BatchEpisode[];
  counts?: Record<string, number>;
  results?: { id: number; number: number; ok: boolean; error?: string; skipped?: string; version?: number }[];
};
export type QueueItem = {
  id: number; number: number; status: string; code: string; name: string; working: boolean;
  step?: string | null; step_label?: string | null; steps_done: number; steps: number; progress: number; position?: number;
};
export type QueueView = { max_active: number; working: QueueItem[]; waiting: QueueItem[] };
export type FlaggedRow = {
  idx: number; text: string; kind: string; suggested: string | null; suggested_src: number | null;
  score: number | null; start_ms?: number; end_ms?: number;
};
export type Proposal = {
  row: number; action: string; src?: number | number[] | null; members?: number[] | null; reason?: string;
  confidence?: number | string | null; note?: string; error?: string;
};
export type Decisions = {
  ready: boolean; flagged?: FlaggedRow[]; missing?: number[] | null; decided?: boolean;
  ai_last?: { model: string; usage: any; proposals: Proposal[]; spec?: string; joined?: number[][]; left?: number[][] } | null;
  ai_auto?: { at: number; model?: string; rows?: number; not_found?: number[]; low?: { row: number; confidence: any; reason: string }[];
    joined?: number[][]; error?: string } | null;
};
export type Rows = {
  dub: { text: string; start_ms?: number; end_ms?: number }[]; src: { text: string; start_ms?: number; end_ms?: number }[];
  auto?: Record<string, { src: number; score?: number; kind?: string }>;
  alts?: Record<string, [number, number][]>;   // flagged row -> the 5 closest source sentences [index, score]
};

export type User = {
  id: number | null; username: string; name?: string; email?: string | null; role: string; active?: number;
  created?: number; last_login?: number; shows?: number;
};
export type Upload = {
  id: number; episode_id: number; kind: string; name: string; path: string; key: string; size: number; sent: number;
  status: string; error?: string | null; created?: number; finished?: number;
};

// The session cookie can run out while a page is open: the app then shows the sign-in screen again instead of
// filling the screen with errors.
let signedOut: (() => void) | null = null;
export function onSignedOut(fn: (() => void) | null) { signedOut = fn; }

async function call<T>(method: string, url: string, body?: any): Promise<T> {
  const init: RequestInit = { method, headers: {}, credentials: "same-origin" };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as any)["Content-Type"] = "application/json";
  }
  const r = await fetch(url, init);
  const text = await r.text();
  const ct = (r.headers.get("content-type") || "").toLowerCase();
  let data: any = null;
  if (text && (ct.includes("json") || text.startsWith("{") || text.startsWith("["))) {
    try { data = JSON.parse(text); } catch { data = text; }
  } else if (text) {
    data = text;
  }
  if (r.status === 401 && !url.startsWith("/api/auth/")) signedOut?.();
  if (!r.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : `${r.status} ${r.statusText}`);
  }
  // An unknown /api path on an old server falls through to the SPA and returns HTML 200.
  // Treating that as JSON used to crash the show page (string.map is not a function).
  if (typeof data === "string") throw new Error(`${r.status} unexpected response`);
  return data as T;
}

export const api = {
  get: <T,>(u: string) => call<T>("GET", u),
  post: <T,>(u: string, b?: any) => call<T>("POST", u, b ?? {}),
  put: <T,>(u: string, b: any) => call<T>("PUT", u, b),
  patch: <T,>(u: string, b: any) => call<T>("PATCH", u, b),
  del: <T,>(u: string) => call<T>("DELETE", u),
};

export type LiveEvent = { kind: string; episode_id: number | null; job_id: number | null; data: any; ts: number };

// One shared socket for the whole page; reconnects by itself.
type Listener = (e: LiveEvent) => void;
const listeners = new Set<Listener>();
let sock: WebSocket | null = null;
function connect() {
  if (sock && sock.readyState <= 1) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  sock = new WebSocket(`${proto}://${location.host}/api/ws`);
  sock.onmessage = (m) => {
    try { const ev = JSON.parse(m.data); listeners.forEach((l) => l(ev)); } catch { /* ignore */ }
  };
  sock.onclose = () => { sock = null; if (listeners.size) setTimeout(connect, 1500); };
}

export function useLive(fn: Listener) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const l: Listener = (e) => ref.current(e);
    listeners.add(l); connect();
    return () => { listeners.delete(l); };
  }, []);
}

export const reveal = (path: string) => api.post("/api/reveal", { path });

// ---------------- accounts ----------------
export type Me = { user: User | null; required: boolean; ttl_seconds?: number };
export const auth = {
  me: () => api.get<Me>("/api/auth/me"),
  login: (username: string, password: string) => api.post<{ user: User }>("/api/auth/login", { username, password }),
  logout: () => api.post("/api/auth/logout"),
  password: (current: string, password: string) => api.post<{ user: User }>("/api/auth/password", { current, password }),
};
