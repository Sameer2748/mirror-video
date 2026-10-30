// Settings: the AI gateway (Claude for matching and translation), paths and performance, and a health check of
// everything Dub Studio needs on this Mac. The AI key is written to app/.env and never shown again.
import { useEffect, useState } from "react";
import { api } from "../api";
import { ErrorBox } from "../ui";

type S = {
  ai: { base: string; email: string; key_set: boolean; model: string; model_fast: string; configured: boolean; fal_set?: boolean };
  app: { values: Record<string, any>; saved: Record<string, any>; from_env: string[] };
  health: { name: string; ok: boolean; detail: string }[];
  supervised: boolean;
};
const APP_FIELDS: [string, string, string][] = [
  ["tools_dir", "Pipeline folder (adaptation-tool)", "the folder with scripts/dubstitch.py"],
  ["pipeline_python", "Pipeline Python", "app/pipeline_env/bin/python (made by scripts/setup_pipeline.sh)"],
  ["face_python", "Face map Python (separate: insightface, onnxruntime, ultralytics)", "adaptation-tool/mac_lipscan/venv_face/bin/python (scripts/setup_face_env.sh)"],
  ["models_dir", "Whisper models folder", "adaptation-tool/data/models"],
  ["library", "Library (database + work files)", "app/library"],
  ["font", "Subtitle font file", "Poppins-Bold.ttf"],
  ["cpu_slots", "Heavy steps at the same time", "2 (raise on a big Mac, lower if it gets hot)"],
  ["step_max_seconds", "Max seconds per step run (0 = no limit)", "0"],
  ["port", "Port", "8765"],
];

export default function Settings() {
  const [s, setS] = useState<S | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ai, setAi] = useState({ base: "", email: "", key: "", model: "", model_fast: "" });
  const [appV, setAppV] = useState<Record<string, string>>({});
  const [test, setTest] = useState<any>(null);
  const [msg, setMsg] = useState<{ text: string; err?: boolean } | null>(null);
  const [busy, setBusy] = useState("");
  const load = () => api.get<S>("/api/settings").then((x) => {
    setS(x); setAi({ base: x.ai.base, email: x.ai.email, key: "", model: x.ai.model, model_fast: x.ai.model_fast });
    setAppV(Object.fromEntries(Object.entries(x.app.values).map(([k, v]) => [k, v == null ? "" : String(v)])));
  }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const say = (text: string, e = false) => setMsg({ text, err: e });

  async function saveAi(removeKey = false) {
    setBusy("ai"); setMsg(null);
    try { await api.put("/api/settings/ai", { ...ai, remove_key: removeKey }); say(removeKey ? "Key removed: the AI is off." : "AI settings saved."); setTest(null); await load(); }
    catch (e: any) { say(e.message, true); } finally { setBusy(""); }
  }
  const [fal, setFal] = useState("");
  async function saveFal(remove = false) {
    setBusy("fal"); setMsg(null);
    try { await api.put("/api/settings/ai", remove ? { remove_fal_key: true } : { fal_key: fal }); setFal(""); say(remove ? "fal.ai key removed: video generation is off." : "fal.ai key saved."); await load(); }
    catch (e: any) { say(e.message, true); } finally { setBusy(""); }
  }
  async function runTest() {
    setBusy("test"); setTest(null);
    try { setTest(await api.post("/api/settings/ai/test")); } catch (e: any) { setTest({ ok: false, error: e.message }); } finally { setBusy(""); }
  }
  async function saveApp() {
    setBusy("app"); setMsg(null);
    const changed = Object.fromEntries(Object.entries(appV).filter(([k, v]) => String(s!.app.values[k] ?? "") !== v));
    try { await api.put("/api/settings/app", changed); say(`Saved (${Object.keys(changed).join(", ") || "nothing changed"}). These are used after a restart.`); await load(); }
    catch (e: any) { say(e.message, true); } finally { setBusy(""); }
  }
  async function restart() {
    if (!confirm("Restart Dub Studio now? Nothing is lost; the page reconnects in a few seconds.")) return;
    try { await api.post("/api/restart"); say("Restarting…"); setTimeout(() => location.reload(), 4000); } catch (e: any) { say(e.message, true); }
  }

  if (!s) return <div className="page"><ErrorBox error={err} /></div>;
  const bad = s.health.filter((h) => !h.ok);
  return (
    <div className="page narrow">
      <h1>Settings</h1>
      {msg && <div className={msg.err ? "error" : "ok-box"}>{msg.text}</div>}

      <h3>Health</h3>
      <div className={bad.length ? "warn" : "ok-box"}>{bad.length ? `${bad.length} thing${bad.length > 1 ? "s" : ""} to look at` : "Everything Dub Studio needs is in place."}</div>
      <table className="table"><tbody>
        {s.health.map((h) => <tr key={h.name}><td style={{ width: 24 }}>{h.ok ? <span className="good">✓</span> : <span className="bad">✗</span>}</td><td><b>{h.name}</b></td><td className="muted small-text">{h.detail}</td></tr>)}
      </tbody></table>

      <h3>AI (Claude through the gateway)</h3>
      <div className="muted small-text" style={{ marginBottom: 10 }}>Used for Check matching (Match with AI), translation when Google is blocked, and Re-translate in the editor. Without it everything still works by hand.</div>
      <div className="panel">
        <div className="kv" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <label>Gateway address<input value={ai.base} placeholder="https://…" onChange={(e) => setAi({ ...ai, base: e.target.value })} /></label>
          <label>Your work email<input value={ai.email} onChange={(e) => setAi({ ...ai, email: e.target.value })} /></label>
          <label>Key<input type="password" autoComplete="off" value={ai.key} placeholder={s.ai.key_set ? "a key is saved — type a new one to replace it" : "paste the key"} onChange={(e) => setAi({ ...ai, key: e.target.value })} /></label>
          <label>Model<input value={ai.model} onChange={(e) => setAi({ ...ai, model: e.target.value })} /></label>
          <label>Fast model (re-translate)<input value={ai.model_fast} onChange={(e) => setAi({ ...ai, model_fast: e.target.value })} /></label>
        </div>
        <div className="row" style={{ gap: 8, marginTop: 12, alignItems: "center" }}>
          <button className="primary" disabled={!!busy} onClick={() => saveAi()}>Save</button>
          <button disabled={!!busy || !s.ai.configured} onClick={runTest}>{busy === "test" ? "Testing…" : "Test the connection"}</button>
          {s.ai.key_set && <button className="ghost danger" disabled={!!busy} onClick={() => confirm("Remove the saved key? The AI features turn off.") && saveAi(true)}>Remove the key</button>}
          <span className="muted small-text">{s.ai.key_set ? "Key saved in app/.env (never shown)." : "No key saved."}</span>
        </div>
        {test && (test.ok
          ? <div className="ok-box small-text">✓ Connected. Spent ${Number(test.spend || 0).toFixed(2)}{test.max_budget ? ` of $${test.max_budget}` : ""}. Models: {(test.models || []).join(", ")}</div>
          : <div className="error small-text">Not connected: {test.error}</div>)}
      </div>

      <h3>Video generation (fal.ai)</h3>
      <div className="muted small-text" style={{ marginBottom: 10 }}>Used by ✦ Generate in the editor: a new video of a slow clip at natural speed (Kling / Veo on fal.ai). Claude and Gemini for the prompt use the AI key above.</div>
      <div className="panel">
        <div className="kv" style={{ gridTemplateColumns: "1fr" }}>
          <label>fal.ai key<input type="password" autoComplete="off" value={fal} placeholder={s.ai.fal_set ? "a key is saved — type a new one to replace it" : "paste the fal.ai key"} onChange={(e) => setFal(e.target.value)} /></label>
        </div>
        <div className="row" style={{ gap: 8, marginTop: 12, alignItems: "center" }}>
          <button className="primary" disabled={!!busy || !fal.trim()} onClick={() => saveFal()}>Save</button>
          {s.ai.fal_set && <button className="ghost danger" disabled={!!busy} onClick={() => confirm("Remove the fal.ai key? Generating videos turns off.") && saveFal(true)}>Remove the key</button>}
          <span className="muted small-text">{s.ai.fal_set ? "Key saved in app/.env (never shown)." : "No key saved."}</span>
        </div>
      </div>

      <h3>Paths and performance</h3>
      <div className="muted small-text" style={{ marginBottom: 10 }}>Saved in app/config.json. Used after a restart{s.supervised ? "" : " (stop Dub Studio and start it again)"}.{s.app.from_env.length ? ` Set by the environment (wins over this page): ${s.app.from_env.join(", ")}.` : ""}</div>
      <div className="panel">
        <div className="kv" style={{ gridTemplateColumns: "1fr" }}>
          {APP_FIELDS.map(([k, label, hint]) => <label key={k}>{label}<input value={appV[k] ?? ""} placeholder={hint} onChange={(e) => setAppV({ ...appV, [k]: e.target.value })} /></label>)}
        </div>
        <div className="row" style={{ gap: 8, marginTop: 12 }}>
          <button className="primary" disabled={!!busy} onClick={saveApp}>Save</button>
          {s.supervised && <button onClick={restart}>Restart Dub Studio</button>}
        </div>
      </div>
    </div>
  );
}
