// Thin client for the FastAPI backend (/api) and its live event socket (/api/ws).
import { useEffect, useRef } from "react";

export type Profile = Record<string, any>;
export type Show = {
  id: number; name: string; code: string; folder: string; preset: string | null; profile: Profile;
  episode_counts?: Record<string, number>;
};
export type Asset = { id: number; kind: string; path: string; size?: number; probe?: any; role: string };
export type Job = {
  id: number; step: string; label: string; ord: number; status: string; progress: number; item?: string;
  message?: string; resource?: string; attempt?: number; started?: number; finished?: number; eta?: number;
  log_path?: string; check_passed?: boolean; check_lines?: string[];
};
export type Episode = {
  id: number; show_id: number; number: number; status: string; work_dir: string; output_dir: string;
  updated?: number; progress?: number; assets: Asset[]; jobs: Job[]; run?: number; show: Show;
  output_existing: string[]; candidates?: Record<string, string[]>;
};
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

async function call<T>(method: string, url: string, body?: any): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as any)["Content-Type"] = "application/json";
  }
  const r = await fetch(url, init);
  const text = await r.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : `${r.status} ${r.statusText}`);
  }
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
