// Generate a replacement clip with AI (backend: dubstudio/gen.py). Opens on the right of the editor for one clip:
// its frames, what the video model saw in it, a prefilled prompt, a chat with Claude (with images) and the videos
// made on fal.ai. Every chat and video is kept per clip slot, so the clip's history stays.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

export type GenClip = { id: string; label: string; row: number | null; t0: number; t1: number; s0: number; s1: number;
  dub_text: string; src_text: string; media?: string; prev_text?: string; next_text?: string; lip?: boolean; flags?: string[] };
type Msg = { id: number; role: "user" | "assistant" | "error"; text: string; images: string[]; created: number };
type Job = { id: number; model: string; prompt: string; status: string; error?: string; import_id?: string;
  result?: { file: string; seconds: number }; created: number; finished?: number; params?: any };
type Thread = { id: number; slot: string; title: string; status: string; error?: string; clip: GenClip; analysis?: string;
  frames?: { frames: { file: string; t_ms: number; shot?: number }[]; start: string; end: string;
    shots?: { s0: number; s1: number; start: string; end: string }[] }; prompt?: string; messages: Msg[]; jobs: Job[] };
type Mode = "frames" | "shots" | "refs";
type ThreadRow = { id: number; title: string; status: string; videos: number; created: number };
type Status = { chat: boolean; video: boolean; system_prompt: string; fal?: boolean;
  models: { id: string; label: string; durations: number[]; end_frame: boolean; refs?: number }[] };

const s2 = (ms: number) => (ms / 1000).toFixed(2);
const STEPS: [string, string][] = [["frames", "Taking frames from the clip"], ["analysing", "Watching the clip (Gemini)"],
  ["writing", "Writing the first prompt (Claude)"]];

export default function GenPanel({ eid, clip, onClose, onPutV2, onBinChanged }: {
  eid: string; clip: GenClip; onClose: () => void; onPutV2: (importId: string, clip: GenClip) => void; onBinChanged: () => void;
}) {
  const slot = `${Math.round(clip.t0)}-${Math.round(clip.t1)}`;
  const [st, setSt] = useState<Status | null>(null);
  const [threads, setThreads] = useState<ThreadRow[]>([]);
  const [t, setT] = useState<Thread | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [useEnd, setUseEnd] = useState(true);
  const [startImg, setStartImg] = useState<string | null>(null);     // an attached image to start from instead
  const [mode, setMode] = useState<Mode | null>(null);              // null = the default for this clip
  const [refs, setRefs] = useState<string[]>([]);                   // reference images (Veo 3.1): files in the chat folder
  const [show, setShow] = useState<{ analysis: boolean; system: boolean }>({ analysis: false, system: false });
  const [big, setBig] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const chatEnd = useRef<HTMLDivElement>(null);
  const doneJobs = useRef<Set<number>>(new Set());
  const base = `/api/episodes/${eid}/gen/threads`;
  const fileUrl = (th: Thread, f: string) => `${base}/${th.id}/file/${f}`;
  const slotS = (clip.t1 - clip.t0) / 1000, srcS = (clip.s1 - clip.s0) / 1000, sp = srcS / Math.max(slotS, 0.01);

  const loadList = useCallback(() => api.get<ThreadRow[]>(`${base}?slot=${slot}`).then((r) => { setThreads(r); return r; }), [base, slot]);
  const open = useCallback((id: number) => api.get<Thread>(`${base}/${id}`).then((x) => { setT(x); setPrompt(x.prompt || ""); return x; }).catch((e) => setErr(e.message)), [base]);
  useEffect(() => { api.get<Status>("/api/gen/status").then((s) => { setSt(s); setModel((m) => m || s.models[0]?.id || ""); }).catch(() => {}); }, []);
  useEffect(() => { setT(null); setErr(null); loadList().then((r) => { if (r[0]) open(r[0].id); }); }, [slot]); // eslint-disable-line

  // follow a chat while it prepares or a video is being made
  const busy = !!t && (["frames", "analysing", "writing"].includes(t.status) || t.jobs.some((j) => !["done", "failed"].includes(j.status)));
  useEffect(() => {
    if (!busy || !t) return;
    const iv = window.setInterval(() => api.get<Thread>(`${base}/${t.id}`).then((x) => {
      setT(x); if (x.status === "ready" && t.status !== "ready") setPrompt(x.prompt || "");
      for (const j of x.jobs) if (j.status === "done" && !doneJobs.current.has(j.id)) { doneJobs.current.add(j.id); onBinChanged(); loadList(); }
    }).catch(() => {}), 2000);
    return () => clearInterval(iv);
  }, [busy, t?.id, t?.status]); // eslint-disable-line
  useEffect(() => { t?.jobs.forEach((j) => j.status === "done" && doneJobs.current.add(j.id)); }, [t?.id]); // eslint-disable-line
  // scroll to the newest message only when one arrives (opening a chat shows its top: facts and frames)
  const seenMsgs = useRef<{ id: number; n: number }>({ id: 0, n: 0 });
  useEffect(() => {
    if (!t) return; const n = t.messages.length, prev = seenMsgs.current;
    if (prev.id === t.id && n > prev.n) chatEnd.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    seenMsgs.current = { id: t.id, n };
  }, [t?.id, t?.messages.length]); // eslint-disable-line

  const newChat = async () => {
    setErr(null);
    try { const x = await api.post<Thread>(base, { clip }); setT(x); setPrompt(""); loadList(); } catch (e: any) { setErr(e.message); }
  };
  const del = async () => {
    if (!t || !confirm(`Delete "${t.title}" and its messages? Videos already made stay in the Project bin.`)) return;
    await api.del(`${base}/${t.id}`); const r = await loadList(); if (r[0]) open(r[0].id); else setT(null);
  };
  const send = async () => {
    if (!t || (!text.trim() && !files.length)) return;
    setSending(true); setErr(null);
    try {
      const fd = new FormData(); fd.append("text", text); files.forEach((f) => fd.append("files", f));
      const r = await fetch(`${base}/${t.id}/messages`, { method: "POST", body: fd }); const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.statusText);
      setT(j); if (j.prompt) setPrompt(j.prompt); setText(""); setFiles([]);
    } catch (e: any) { setErr(e.message); } finally { setSending(false); }
  };
  const savePrompt = () => { if (t && prompt !== (t.prompt || "")) api.put<Thread>(`${base}/${t.id}/prompt`, { prompt }).then(setT).catch(() => {}); };
  const mi = st?.models.find((m) => m.id === model);
  const shots = t?.frames?.shots || [];
  const mainShot = shots.length ? shots.reduce((b, x, k) => (x.s1 - x.s0 > shots[b].s1 - shots[b].s0 ? k : b), 0) : 0;
  const keptS = shots.filter((_, k) => k !== mainShot).reduce((a, x) => a + (x.s1 - x.s0) / 1000, 0);
  // the way to make it: the editor's choice, else the default (several shots -> keep the short ones; else match the frames)
  const how: Mode = mode === "refs" && !mi?.refs ? (shots.length > 1 ? "shots" : "frames") : mode || (shots.length > 1 ? "shots" : "frames");
  const refList = refs.length ? refs : shots.slice(0, 3).map((x) => x.start).concat(shots.length ? [] : t?.frames ? [t.frames.start] : []);
  const toggleRef = (f: string) => setRefs((x) => { const cur = x.length ? x : refList; return cur.includes(f) ? cur.filter((y) => y !== f) : cur.length >= (mi?.refs || 3) ? cur : [...cur, f]; });
  const generate = async () => {
    if (!t) return; setErr(null);
    try { setT(await api.post<Thread>(`${base}/${t.id}/generate`, { model, prompt, use_end: useEnd, images: startImg && how === "frames" ? [startImg] : [],
      mode: how, refs: how === "refs" ? refList : [] })); }
    catch (e: any) { setErr(e.message); }
  };
  // same rules as the backend (gen.plan_length / run_job): how long the model makes it and how it is fitted
  const target = how === "shots" ? Math.max(slotS - keptS, 0) : slotS;
  const genSecs = how === "refs" ? 8 : mi ? mi.durations.reduce((a, d) => (Math.abs(d - target) < Math.abs(a - target) ? d : a), mi.durations[0]) : null;
  const fitNote = !genSecs ? "" : how === "refs" ? `makes 8 s from the reference images, ${slotS <= 8 ? "cut" : "slowed"} to ${slotS.toFixed(2)} s`
    : (() => { const f = target / genSecs; const pre = how === "shots" ? `shot ${mainShot + 1}: ` : "";
      const tail = how === "shots" ? ` + ${keptS.toFixed(2)} s kept as filmed` : "";
      return pre + (f >= 0.8 && f <= 1.25 ? `makes ${genSecs} s, fitted to ${target.toFixed(2)} s`
        : target < genSecs ? `makes ${mi!.durations.find((d) => d >= target)} s, cut to ${target.toFixed(2)} s` : `makes ${genSecs} s, slowed to ${target.toFixed(2)} s`) + tail; })();
  const userImages = t ? t.messages.flatMap((m) => (m.role === "user" ? m.images : [])) : [];

  return (
    <div className="gen">
      <div className="gen-head">
        <b>✦ Generate</b><span className="gen-clip">{clip.label}</span><span className="grow" />
        <button className="ghost small" onClick={onClose} title="Close (back to the inspector)">✕</button>
      </div>
      <div className="gen-body">
        <div className="gen-facts">
          <span><b>{s2(clip.t1 - clip.t0)} s</b> slot</span><span>footage {srcS.toFixed(2)} s</span>
          <span className={sp < 0.67 ? "bad" : ""}>plays ×{sp.toFixed(2)}</span>
          <span className="muted">→ new video {slotS.toFixed(2)} s at ×1.00</span>
          {shots.length > 1 && <span className="warn-chip" title="The footage has camera cuts">{shots.length} shots</span>}
        </div>
        {clip.dub_text && <div className="gen-line">“{clip.dub_text}”</div>}

        <div className="gen-threads">
          <select value={t?.id ?? ""} onChange={(e) => e.target.value && open(Number(e.target.value))} disabled={!threads.length}>
            {!threads.length && <option value="">no chats for this clip yet</option>}
            {threads.map((x) => <option key={x.id} value={x.id}>{x.title}{x.videos ? ` · ${x.videos} video${x.videos > 1 ? "s" : ""}` : ""}</option>)}
          </select>
          <button className="small" onClick={newChat} title="A new chat for this clip (the others stay)">＋ New chat</button>
          {t && <button className="ghost small danger" onClick={del} title="Delete this chat">🗑</button>}
        </div>

        {err && <div className="gen-err">{err}</div>}
        {st && !st.chat && <div className="gen-err">The AI gateway is not set up or not reachable (Settings → AI; the VPN must be on).</div>}

        {!t && (
          <div className="gen-intro">
            <p>Make a new video for this clip: the same moment, at natural speed, exactly {slotS.toFixed(2)} s long.</p>
            <ol><li>Frames are taken from the clip (start → end).</li><li>Gemini watches the clip and writes down what it sees.</li>
              <li>Claude writes the prompt. You can change it, chat and attach images.</li><li>Generate: the video lands in the Project bin; put it on V2 over the clip.</li></ol>
            <button className="primary" onClick={newChat} disabled={!st?.chat}>✦ Start</button>
          </div>
        )}

        {t && <>
          {["frames", "analysing", "writing"].includes(t.status) && (
            <div className="gen-steps">{STEPS.map(([k, l], i) => {
              const cur = STEPS.findIndex(([x]) => x === t.status);
              return <div key={k} className={i < cur ? "done" : i === cur ? "now" : ""}>{i < cur ? "✓" : i === cur ? <span className="spin" /> : "·"} {l}</div>;
            })}</div>
          )}
          {t.status === "error" && <div className="gen-err">Could not prepare this chat: {t.error}{" "}
            <button className="small" onClick={async () => { setErr(null); try { setT(await api.post<Thread>(`${base}/${t.id}/retry`)); } catch (e: any) { setErr(e.message); } }}>Try again</button></div>}

          {t.frames && (
            <div className="gen-sec">
              <div className="gen-lbl">Frames of the clip <span className="muted">· start → end · {how === "refs" ? "click to use as a reference (up to 3)" : "click to enlarge"}</span></div>
              <div className="gen-frames">
                {t.frames.frames.map((f, k) => {
                  const newShot = shots.length > 1 && k > 0 && f.shot !== t.frames!.frames[k - 1].shot;
                  const isRef = how === "refs" && refList.includes(f.file);
                  return <div key={f.file} className={`gen-frame ${newShot ? "cut" : ""} ${isRef ? "ref" : ""}`}>
                    <img src={fileUrl(t, f.file)} alt="" title={`${s2(f.t_ms)} s in the source${shots.length > 1 ? ` · shot ${(f.shot ?? 0) + 1}` : ""}${k === 0 ? " (start frame)" : k === t.frames!.frames.length - 1 ? " (end frame)" : ""}`}
                      className={k === 0 || k === t.frames!.frames.length - 1 ? "edge" : ""} onClick={() => (how === "refs" ? toggleRef(f.file) : setBig(fileUrl(t, f.file)))} />
                    {shots.length > 1 && <span className="gen-shot">{(f.shot ?? 0) + 1}</span>}
                    {isRef && <span className="gen-refmark">REF</span>}
                  </div>; })}
              </div>
              {shots.length > 1 && <div className="muted small-text">{shots.length} shots: {shots.map((x, k) => `${k + 1}) ${((x.s1 - x.s0) / 1000).toFixed(2)} s`).join(" · ")} — the orange line marks a camera cut</div>}
            </div>
          )}

          {t.analysis && (
            <div className="gen-sec">
              <button className="gen-toggle" onClick={() => setShow((x) => ({ ...x, analysis: !x.analysis }))}>{show.analysis ? "▾" : "▸"} What the video model saw</button>
              {show.analysis && <pre className="gen-pre">{t.analysis}</pre>}
            </div>
          )}
          <div className="gen-sec">
            <button className="gen-toggle" onClick={() => setShow((x) => ({ ...x, system: !x.system }))}>{show.system ? "▾" : "▸"} System prompt (the rules Claude follows)</button>
            {show.system && <pre className="gen-pre">{st?.system_prompt}</pre>}
          </div>

          <div className="gen-sec gen-chat">
            <div className="gen-lbl">Chat</div>
            {t.messages.map((m, i) => (
              <div key={m.id} className={`gen-msg ${m.role}`}>
                {m.role === "user" && i === 0 ? <span className="muted small-text">{m.text}</span> : <MsgText text={m.text} onUse={(p) => { setPrompt(p); api.put<Thread>(`${base}/${t.id}/prompt`, { prompt: p }).then(setT); }} />}
                {m.images?.length > 0 && <div className="gen-msg-imgs">{m.images.map((f) => <img key={f} src={fileUrl(t, f)} alt="" onClick={() => setBig(fileUrl(t, f))} />)}</div>}
              </div>))}
            {sending && <div className="gen-msg assistant"><span className="spin" /> Claude is thinking…</div>}
            <div ref={chatEnd} />
            <div className="gen-compose" onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => { e.preventDefault(); setFiles((x) => [...x, ...Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith("image/"))]); }}>
              {files.length > 0 && <div className="gen-attach">{files.map((f, k) => <span key={k} className="chip chip-grey">🖼 {f.name}<button className="ghost small" onClick={() => setFiles(files.filter((_, j) => j !== k))}>×</button></span>)}</div>}
              <textarea value={text} placeholder="Ask for a change (“less head movement”, “she should smile at the end”)… attach or drop images"
                disabled={t.status !== "ready" || sending} onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); }} />
              <div className="row" style={{ gap: 6 }}>
                <button className="ghost small" onClick={() => file.current?.click()} disabled={t.status !== "ready"} title="Attach images">📎 Image</button>
                <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden onChange={(e) => { if (e.target.files) setFiles((x) => [...x, ...Array.from(e.target.files!)]); e.target.value = ""; }} />
                <span className="grow" />
                <button className="small primary" onClick={send} disabled={t.status !== "ready" || sending || (!text.trim() && !files.length)} title="Send (⌘⏎)">Send</button>
              </div>
            </div>
          </div>

          <div className="gen-sec gen-prompt">
            <div className="gen-lbl">Video prompt <span className="muted">· yours to change</span></div>
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} onBlur={savePrompt} disabled={t.status !== "ready"} rows={7} />
            <div className="gen-opts">
              <label>Model <select value={model} onChange={(e) => setModel(e.target.value)}>
                {st?.models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</select></label>
              <div className="gen-modes">
                <div className="gen-lbl">How to make it</div>
                {shots.length > 1 && <label className="chk"><input type="radio" checked={how === "shots"} onChange={() => setMode("shots")} />
                  <span><b>Keep the short shots, generate shot {mainShot + 1}</b><br /><span className="muted small-text">the other shots play as filmed ({keptS.toFixed(2)} s); shot {mainShot + 1} is made from its own first and last frame — exact</span></span></label>}
                <label className="chk"><input type="radio" checked={how === "frames"} onChange={() => setMode("frames")} />
                  <span><b>Match the clip's first {mi?.end_frame ? "and last " : ""}frame</b><br /><span className="muted small-text">one continuous shot{shots.length > 1 ? " (the cuts are lost)" : ""}</span></span></label>
                <label className={`chk ${mi?.refs ? "" : "off"}`} title={mi?.refs ? "" : "Only Veo 3.1 takes reference images"}><input type="radio" disabled={!mi?.refs} checked={how === "refs"} onChange={() => setMode("refs")} />
                  <span><b>Reference images</b> <span className="muted small-text">(Veo 3.1)</span><br /><span className="muted small-text">up to 3 frames of the people / places{shots.length > 1 ? " — keeps all the shots and cuts" : ""}; makes 8 s, no exact first frame</span></span></label>
                {how === "refs" && <div className="gen-refs">{refList.map((f) => <img key={f} src={fileUrl(t, f)} alt="" title="Reference image (click to remove)" onClick={() => toggleRef(f)} />)}
                  {userImages.filter((f) => !refList.includes(f)).map((f) => <button key={f} className="ghost small" onClick={() => toggleRef(f)}>＋ attached image</button>)}
                  <span className="muted small-text">{refList.length}/{mi?.refs || 3} · click frames above to add</span></div>}
              </div>
              {how !== "refs" && mi?.end_frame && <label className="chk"><input type="checkbox" checked={useEnd} onChange={(e) => setUseEnd(e.target.checked)} /> end on the clip's last frame</label>}
              {how === "frames" && userImages.length > 0 && <label>Start from <select value={startImg || ""} onChange={(e) => setStartImg(e.target.value || null)}>
                <option value="">the clip's first frame</option>{userImages.map((f, k) => <option key={f} value={f}>attached image {k + 1}</option>)}</select></label>}
            </div>
            <div className="row" style={{ gap: 8, alignItems: "center" }}>
              <button className="primary" onClick={generate} disabled={!st?.video || t.status !== "ready" || !prompt.trim()}
                title={st?.video ? fitNote : "No video model: the AI key has no Veo model (VPN on?) and no fal.ai key is saved"}>✦ Generate video</button>
              <span className="muted small-text">{st?.video ? `${fitNote} · about 1–3 min` : "no video model available (VPN on?)"}</span>
            </div>
          </div>

          {t.jobs.length > 0 && (
            <div className="gen-sec">
              <div className="gen-lbl">Videos made in this chat</div>
              {[...t.jobs].reverse().map((j) => <JobCard key={j.id} j={j} eid={eid} clip={clip} onPutV2={onPutV2} />)}
            </div>
          )}
        </>}
      </div>
      {big && <div className="modal-back" onClick={() => setBig(null)}><img src={big} alt="" className="gen-big" /></div>}
    </div>
  );
}

// an assistant message: text, with ```prompt blocks shown as cards that can be used as the video prompt
function MsgText({ text, onUse }: { text: string; onUse: (p: string) => void }) {
  const parts = text.split(/```prompt\s*\n([\s\S]*?)```/);
  return <>{parts.map((p, k) => k % 2 === 1
    ? <div key={k} className="gen-pcard"><div className="gen-pcard-text">{p.trim()}</div><button className="small" onClick={() => onUse(p.trim())}>Use this prompt</button></div>
    : p.trim() && <div key={k} className="gen-text">{p.trim()}</div>)}</>;
}

function JobCard({ j, eid, clip, onPutV2 }: { j: Job; eid: string; clip: GenClip; onPutV2: (importId: string, clip: GenClip) => void }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { if (["done", "failed"].includes(j.status)) return; const iv = window.setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(iv); }, [j.status]);
  const secs = Math.round(((j.finished ? j.finished * 1000 : now) - j.created * 1000) / 1000);
  return (
    <div className={`gen-job ${j.status}`}>
      <div className="gen-job-head"><b>#{j.id}</b><span className="muted">{(j.model.includes("/") ? j.model.split("/").slice(1, 3).join(" ") : j.model.replace("-generate-001", ""))}
        {j.params?.mode === "shots" ? ` · shot ${j.params.shot ?? ""} made, others kept` : j.params?.mode === "refs" ? " · reference images" : ""}</span><span className="grow" />
        <span className={`chip ${j.status === "done" ? "chip-green" : j.status === "failed" ? "chip-red" : "chip-yellow"}`}>
          {j.status === "done" ? "ready" : j.status === "failed" ? "failed" : <><span className="spin" /> {j.status}</>}</span>
        <span className="muted small-text">{secs} s</span></div>
      {j.status === "done" && j.import_id && <>
        <video src={`/api/episodes/${eid}/imports/${j.import_id}/media`} controls loop muted playsInline />
        <div className="row" style={{ gap: 6 }}>
          <button className="small primary" onClick={() => onPutV2(j.import_id!, clip)} title="Put it on V2 over this clip (V1 stays underneath)">▲ Put on V2 over {clip.label}</button>
          <span className="muted small-text">also in the Project bin</span>
        </div>
      </>}
      {j.status === "failed" && <div className="gen-err">{j.error}</div>}
      <details><summary className="muted small-text">prompt used</summary><div className="small-text">{j.prompt}</div></details>
    </div>
  );
}
