// Check matching (Step 2): the rows the automatic matching could not decide. The editor takes the AI's proposal,
// the automatic one, a gap, or another source sentence per row; the choices become a spec.py (the skill's mkdec
// language) that the backend checks and turns into decisions.json.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, Decisions, Episode, Proposal, Rows, useLive } from "../api";
import { ErrorBox, fmtMs } from "../ui";

type Mode = "ai" | "auto" | "gap" | "src";
type Check = { ok: boolean; problems: string[]; overlaps: number[][]; counts: Record<string, number>; log: string[] };
type Choice = { mode: Mode; src?: number };

const kw = (t: string, n = 3) => { const w = (t || "").split(/\s+/).filter(Boolean); return w.length ? w.slice(0, n).join(" ") : t; };
const py = (x: any) => JSON.stringify(x);
const srcFirst = (s: any) => (Array.isArray(s) ? s[0] : s);
const validSrc = (s: any, n: number) => (typeof s === "number" ? s >= 0 && s < n
  : Array.isArray(s) && s.length === 2 && s.every((x) => Number.isInteger(x) && x >= 0 && x < n) && s[0] <= s[1]);
const confNum = (c: any) => (typeof c === "number" ? c : c === "high" ? 0.9 : c === "medium" ? 0.6 : c === "low" ? 0.3 : Number(c));
const lowConf = (c: any) => c != null && confNum(c) < 0.7;
const srcLabel = (s: any) => (s == null ? "" : Array.isArray(s) ? `S${s[0]}–S${s[1]}` : `S${s}`);

/** Same rules as the backend's ai_match (groups must be consecutive, a row is decided once). */
function buildSpec(flagged: Decisions["flagged"], choices: Record<number, Choice>, props: Record<number, Proposal>, rows: Rows, model: string) {
  const nd = rows.dub.length, ns = rows.src.length;
  const lines: string[] = []; const done = new Set<number>(); const coveredBy: Record<number, number> = {};
  const D = (i: number) => kw(rows.dub[i]?.text || ""); const S = (s: any) => kw(rows.src[srcFirst(s)]?.text || "");
  for (const f of flagged || []) {
    const r = f.idx; if (done.has(r)) continue;
    const p = props[r];
    const c0 = choices[r] || { mode: "auto" };
    const c: Choice = c0.mode === "ai" && !(p && !p.error) ? { mode: "auto" } : c0;
    if (c.mode === "ai" && p && !p.error) {
      const tag = `AI ${model} (${p.confidence}): ${(p.reason || "").replace(/"/g, "'")}`;
      const s = p.src;
      if (p.action === "gap" || ((p.action === "assign" || p.action === "group") && s == null)) lines.push(`GAP(${r},${py(D(r))},${py(tag)})`);
      else if (p.action === "confirm") lines.push(`C(${r},${py(tag)})`);
      else if (p.action === "group" && validSrc(s, ns)) {
        let mem = Array.from(new Set([...(p.members || []).filter((m) => Number.isInteger(m) && m >= 0 && m < nd && !done.has(m)), r])).sort((a, b) => a - b);
        if (mem.some((m, k) => m !== mem[0] + k)) mem = [r];
        if (mem.length === 1) lines.push(`A(${r},${py(D(r))},${py(s)},${py(S(s))},${py(tag)})`);
        else { lines.push(`G([${mem.map((m) => `(${m},${py(D(m))})`).join(",")}],${py(s)},${py(S(s))},${py(tag)})`); mem.forEach((m) => { done.add(m); if (m !== r) coveredBy[m] = r; }); }
      } else if (p.action === "assign" && validSrc(s, ns)) lines.push(`A(${r},${py(D(r))},${py(s)},${py(S(s))},${py(tag)})`);
      else lines.push(`# row ${r}: the AI answer was not usable; decide it by hand`);
    } else if (c.mode === "gap" || (c.mode === "auto" && f.kind === "unmatched")) {
      lines.push(`GAP(${r},${py(D(r))},${py("no footage (editor, Mirror Video)")})`);
    } else if (c.mode === "auto") {
      lines.push(`C(${r},${py("automatic match accepted by the editor (Mirror Video)")})`);
    } else if (c.mode === "src" && c.src != null) {
      lines.push(`A(${r},${py(D(r))},${c.src},${py(S(c.src))},${py("chosen by the editor (Mirror Video)")})`);
    }
    done.add(r);
  }
  return { spec: `# Step 2 decisions made in Mirror Video\n${lines.join("\n")}\n`, coveredBy };
}

export default function Matching({ ep, onImported }: { ep: Episode; onImported: () => void }) {
  const [d, setD] = useState<Decisions | null>(null);
  const [rows, setRows] = useState<Rows | null>(null);
  const [choices, setChoices] = useState<Record<number, Choice>>({});
  const [aiOn, setAiOn] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(""); const [err, setErr] = useState<string | null>(null);
  const [aiLog, setAiLog] = useState<string[]>([]);
  const [onlyLow, setOnlyLow] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [override, setOverride] = useState<string | null>(null);   // the decisions file after a join or a hand edit
  const [chkRaw, setChk] = useState<(Check & { spec: string }) | null>(null);
  const [joinNote, setJoinNote] = useState<string | null>(null);

  const load = () => api.get<Decisions>(`/api/episodes/${ep.id}/decisions`).then((x) => {
    setD(x);
    if (x.ready) api.get<Rows>(`/api/episodes/${ep.id}/rows`).then(setRows).catch((e) => setErr(e.message));
  }).catch((e) => setErr(e.message));
  useEffect(() => { load(); api.get<any>("/api/ai/status").then((s) => setAiOn(!!s.configured)).catch(() => setAiOn(false)); }, [ep.id, ep.status]); // eslint-disable-line
  useLive((e) => { if (e.kind === "ai_log" && e.episode_id === ep.id) setAiLog((l) => [...l.slice(-30), e.data.line]); });

  const props = useMemo(() => {
    const m: Record<number, Proposal> = {};
    (d?.ai_last?.proposals || []).forEach((p) => (m[p.row] = p));
    return m;
  }, [d?.ai_last]);
  // default choice: the AI's when there is one, else the automatic one
  useEffect(() => {
    if (!d?.flagged) return;
    const c: Record<number, Choice> = {};
    d.flagged.forEach((f) => (c[f.idx] = { mode: props[f.idx] && !props[f.idx].error ? "ai" : "auto" }));
    setChoices(c);
  }, [d?.flagged, props]);

  const model = d?.ai_last?.model || "claude";
  const built = useMemo(() => (d?.flagged && rows ? buildSpec(d.flagged, choices, props, rows, model) : null), [d, rows, choices, props, model]);

  async function runAi() {
    setBusy("ai"); setErr(null); setAiLog([]);
    try { await api.post(`/api/episodes/${ep.id}/decisions/ai`, {}); await load(); } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  }
  const spec = override ?? built?.spec ?? "";
  const chk = chkRaw && chkRaw.spec === spec ? chkRaw : null;     // a check result only counts for the text it checked
  async function check(text = spec) {
    const r = await api.post<Check>(`/api/episodes/${ep.id}/decisions/check`, { spec: text }); setChk({ ...r, spec: text }); return r;
  }
  async function runCheck() {
    setBusy("check"); setErr(null);
    try { await check(); } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  }
  async function join() {
    setBusy("join"); setErr(null);
    try {
      const r = await api.post<{ spec: string; joined: number[][]; left: number[][]; check: Check }>(`/api/episodes/${ep.id}/decisions/join`, { spec });
      setOverride(r.spec); setChk({ ...r.check, spec: r.spec });
      setJoinNote(r.joined.length ? `Joined into groups: ${r.joined.map(([a, b]) => `rows ${a}–${b}`).join(", ")}.` +
        (r.left.length ? ` Not joined (too far apart, decide by meaning): ${r.left.map(([a, b]) => `${a} & ${b}`).join(", ")}.` : "") : "Nothing to join.");
    } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  }
  async function save() {
    setBusy("import"); setErr(null);
    try {
      const r = await check();
      if (!r.ok) { setErr("Not saved: the pipeline's integrity check refuses these decisions (see the list above the Save button)."); return; }
      const fd = new FormData(); fd.append("file", new Blob([spec], { type: "text/plain" }), "spec.py");
      await api.post(`/api/episodes/${ep.id}/decisions/import`, fd); onImported();
    } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  }
  async function importSpec(text: string, name = "spec.py") {
    setBusy("import"); setErr(null);
    try {
      const fd = new FormData(); fd.append("file", new Blob([text], { type: "text/plain" }), name);
      await api.post(`/api/episodes/${ep.id}/decisions/import`, fd); onImported();
    } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  }
  async function acceptAuto() {
    if (!confirm("Accept every automatic match as it is? Rows with no match become gaps (black or stretched neighbours). Nobody will have read them by meaning.")) return;
    setBusy("auto"); setErr(null);
    try { await api.post(`/api/episodes/${ep.id}/decisions/auto`); onImported(); } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  }
  async function uploadFile(f: File) { await importSpec(await f.text(), f.name); }

  if (!d) return <ErrorBox error={err} />;
  if (!d.ready) return <div className="empty"><p>The automatic matching has not run yet.</p><p className="muted">This screen fills in when the "Automatic matching" step is done.</p></div>;
  const flagged = d.flagged || [];
  const unmatched = flagged.filter((f) => f.kind === "unmatched").length;
  const waiting = ep.status === "needs_input";
  const hasAi = Object.keys(props).length > 0;
  const lowN = flagged.filter((f) => props[f.idx] && (lowConf(props[f.idx].confidence) || props[f.idx].error)).length;
  const shown = onlyLow ? flagged.filter((f) => props[f.idx] && (lowConf(props[f.idx].confidence) || props[f.idx].error)) : flagged;
  const cost = d.ai_last?.usage ? `${d.ai_last.usage.calls} calls, ${Math.round((d.ai_last.usage.prompt_tokens + d.ai_last.usage.completion_tokens) / 1000)}k tokens` : "";

  const auto = d.ai_auto;
  return (
    <div>
      {auto && !auto.error && (
        <div className="ok-box" style={{ fontWeight: 400 }}>
          <b>Claude matched these {auto.rows} rows by itself</b> ({new Date(auto.at * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}{auto.joined?.length ? `; ${auto.joined.length} groups joined` : ""}).{" "}
          {auto.not_found?.length ? <><b>{auto.not_found.length} not found</b> (rows {auto.not_found.join(", ")}): no footage matches them — they are <b>red in the editor</b>, where you add a clip. </> : "Every row found footage. "}
          {auto.low?.length ? <>{auto.low.length} the AI was unsure of are yellow in the editor. </> : null}
          You can still change any row below and save again.
        </div>
      )}
      {auto?.error && <div className="warn">The automatic AI matching did not work ({auto.error.slice(0, 200)}). Decide the rows below, or press "Ask the AI again".</div>}
      <div className="panel">
        <div className="panel-row">
          <div className="grow">
            <b>{flagged.length} rows</b> the automatic matching could not decide on its own
            <span className="muted"> ({unmatched} with no match, {flagged.length - unmatched} "Case A" reuse picks)</span>
            {!waiting && d.missing && d.missing.length === 0 && <div className="good small-text">✓ Every row has a decision. Saving again replaces the decisions and continues.</div>}
          </div>
        </div>
        <div className="actions">
          {aiOn && <button disabled={!!busy} title="Clear the decisions and let Claude match every row again by itself (names, second look); the episode then goes back to Ready for review"
            onClick={async () => { if (!confirm("Match this episode again with the AI? The current decisions are replaced; the edit list is rebuilt and the episode returns to Ready for review. Saved editor versions stay in the history.")) return;
              setBusy("rematch"); setErr(null); try { await api.post(`/api/episodes/${ep.id}/decisions/rematch`); onImported(); } catch (e: any) { setErr(e.message); } finally { setBusy(""); } }}>
            {busy === "rematch" ? "Starting…" : "Match again with AI (automatic)"}</button>}
          {aiOn && <button className="primary" disabled={!!busy} onClick={runAi}>{busy === "ai" ? "Claude is reading the rows…" : hasAi ? "Ask the AI again" : "Match with AI"}</button>}
          {aiOn === null && <span className="muted">checking the AI gateway…</span>}
          {aiOn === false && <span className="muted">AI is off (no gateway key in app/.env) — decide the rows below by hand.</span>}
          <button disabled={!!busy} onClick={acceptAuto}>Accept all automatic</button>
          <button disabled={!!busy} onClick={() => fileRef.current?.click()}>Import spec.py / decisions.json…</button>
          <input ref={fileRef} type="file" accept=".py,.json,.txt" hidden onChange={(e) => e.target.files?.[0] && uploadFile(e.target.files[0])} />
        </div>
        {busy === "ai" && <pre className="log">{aiLog.length ? aiLog.join("\n") : "sending the flagged rows with their neighbours…"}</pre>}
        {hasAi && <div className="small-text muted">AI proposals from {model}{cost ? ` (${cost})` : ""}. {lowN ? <b className="bad">{lowN} with low confidence — read those first.</b> : "All with good confidence."}
          {d.ai_last?.joined?.length ? <> Neighbouring rows on the same footage were joined into {d.ai_last.joined.length} groups (rows {d.ai_last.joined.map(([a, b]) => `${a}–${b}`).join(", ")}).</> : null}</div>}
      </div>
      <ErrorBox error={err} />

      {hasAi && lowN > 0 && <label className="check small-text"><input type="checkbox" checked={onlyLow} onChange={(e) => setOnlyLow(e.target.checked)} /> Show only the {lowN} low-confidence rows</label>}
      <div className="match-list">
        {shown.map((f) => (
          <MatchRow key={f.idx} f={f} rows={rows} p={props[f.idx]} edited={override != null} c={choices[f.idx] || { mode: "auto" }}
            coveredBy={built?.coveredBy[f.idx]} set={(c) => setChoices({ ...choices, [f.idx]: c })} />
        ))}
      </div>

      {built && (
        <div className="panel">
          <details open={override != null}>
            <summary className="small-text">{override != null ? "The decisions file (edited — the row choices above no longer change it)" : "Show / edit the decisions file (spec.py) that will be saved"}</summary>
            <textarea className="spec-edit" spellCheck={false} value={spec} onChange={(e) => setOverride(e.target.value)} />
            <div className="small-text muted">One line per decision: G([(row,"first words"),…], source, "source words", "reason") groups rows on one source sentence or range; A(row, …) assigns one row; C(row, reason) confirms the automatic match; GAP(row, "words", reason) leaves no footage.</div>
            {override != null && <button className="small ghost" onClick={() => { setOverride(null); setJoinNote(null); }}>Discard the edits (back to the row choices)</button>}
          </details>
          {joinNote && <div className="ok-box small-text">{joinNote}</div>}
          {chk && chk.ok && <div className="ok-box">✓ The pipeline's integrity check passes: {chk.counts.groups} groups, {chk.counts.assign} single picks, {chk.counts.confirm} confirmed, {chk.counts.gap} gaps.</div>}
          {chk && !chk.ok && (
            <div className="error">
              <b>The pipeline's integrity check refuses these decisions:</b>
              <ul>{chk.problems.filter((x) => !x.startsWith("integrity check failed")).map((x, k) => <li key={k}>{x}</li>)}</ul>
              {chk.overlaps.some(([a, b]) => Math.abs(b - a) <= 3) && <div>Rows next to each other on the same footage are one beat told in several lines: join them into a group.</div>}
              {chk.overlaps.some(([a, b]) => Math.abs(b - a) > 3) && <div>Rows far apart on the same footage: one of them is on the wrong sentence. Read both by meaning and change one (the automatic match of a row that was not flagged can be wrong too; fix it in the decisions file).</div>}
            </div>
          )}
          <div className="actions">
            <button disabled={!!busy || !spec} onClick={runCheck}>{busy === "check" ? "Checking…" : "Check decisions"}</button>
            {chk && chk.overlaps.some(([a, b]) => Math.abs(b - a) <= 3) && <button disabled={!!busy} onClick={join}>{busy === "join" ? "Joining…" : "Join neighbouring rows into groups"}</button>}
            <button className="primary" disabled={!!busy || spec.includes("# row ")} onClick={save}>
              {busy === "import" ? "Checking and saving…" : waiting ? "Save decisions and continue processing" : "Save decisions"}
            </button>
            {spec.includes("# row ") && <span className="bad small-text">Some AI answers were not usable; choose those rows by hand.</span>}
          </div>
        </div>
      )}
    </div>
  );
}

function MatchRow({ f, rows, p, c, coveredBy, set, edited }: { f: NonNullable<Decisions["flagged"]>[number]; rows: Rows | null; p?: Proposal; c: Choice; coveredBy?: number; set: (c: Choice) => void; edited?: boolean }) {
  const dub = rows?.dub || []; const src = rows?.src || [];
  const low = p && (lowConf(p.confidence) || p.error);
  const srcText = (s: any) => (s == null ? "" : Array.isArray(s) ? src.slice(s[0], s[1] + 1).map((x) => x.text).join(" ") : src[s]?.text || "");
  // where the "another source sentence" list starts: the suggestion, else the AI's, else the previous row's match
  let prevAuto: number | undefined;
  for (let k = f.idx - 1; k >= 0 && prevAuto == null; k--) prevAuto = rows?.auto?.[String(k)]?.src;
  const near = f.suggested_src ?? (p && srcFirst(p.src)) ?? prevAuto ?? 0;
  return (
    <div className={`match ${low ? "low" : ""} ${coveredBy != null ? "covered" : ""}`}>
      <div className="match-dub">
        <div className="muted small-text">IT#{f.idx} · {fmtMs(f.start_ms)} · {f.kind}</div>
        {f.idx > 0 && dub[f.idx - 1] && <div className="ctx">{dub[f.idx - 1].text}</div>}
        <div className="dub-text">{f.text}</div>
        {dub[f.idx + 1] && <div className="ctx">{dub[f.idx + 1].text}</div>}
      </div>
      <div className="match-opt">
        <div className="muted small-text">Automatic</div>
        {f.suggested ? <div><b>S{f.suggested_src}</b> {f.suggested}{f.score != null && <span className="muted"> ({f.score.toFixed(2)})</span>}</div> : <div className="muted">no match</div>}
      </div>
      <div className="match-opt">
        <div className="muted small-text">AI {p?.confidence != null && <span className={low ? "bad" : "good"}>· confidence {p.confidence}</span>}</div>
        {!p && <div className="muted">—</div>}
        {p?.error && <div className="bad">{p.error}</div>}
        {p && !p.error && (
          <div>
            <b>{p.action}{p.src != null ? ` ${srcLabel(p.src)}` : ""}</b>{p.members && p.members.length > 1 ? <span className="muted"> (rows {p.members.join(", ")})</span> : null} {srcText(p.src)}
            {p.reason && <div className="muted small-text">{p.reason}</div>}
          </div>
        )}
      </div>
      <div className="match-choice">
        {coveredBy != null ? <div className="muted small-text">decided with the group of row {coveredBy}</div> : (
          <>
            <select value={c.mode} disabled={edited} title={edited ? "the decisions file was edited; discard the edits to use the row choices" : ""} onChange={(e) => set({ mode: e.target.value as Mode, src: e.target.value === "src" ? (c.src ?? near) : undefined })}>
              {p && !p.error && <option value="ai">AI: {p.action} {srcLabel(p.src)}</option>}
              <option value="auto">{f.kind === "unmatched" ? "Automatic: no match → gap" : `Automatic: S${f.suggested_src}`}</option>
              <option value="gap">Gap (no footage)</option>
              <option value="src">Another source sentence…</option>
            </select>
            {!edited && rows?.alts?.[String(f.idx)] && (
              <div className="alts" title="The 5 source sentences closest in meaning (from the automatic matching)">
                <span className="muted small-text">Closest:</span>
                {rows.alts[String(f.idx)].map(([k, sc]) => (
                  <button key={k} className={`alt ${c.mode === "src" && c.src === k ? "on" : ""}`} onClick={() => set({ mode: "src", src: k })} title={src[k]?.text}>
                    <b>S{k}</b> {kw(src[k]?.text || "", 6)}<span className="muted"> {sc.toFixed(2)}</span>
                  </button>
                ))}
              </div>
            )}
            {c.mode === "src" && rows && (
              <select value={c.src ?? near} onChange={(e) => set({ mode: "src", src: Number(e.target.value) })}>
                {src.map((s, k) => <option key={k} value={k}>S{k} {fmtMs(s.start_ms)} {s.text.slice(0, 80)}</option>)}
              </select>
            )}
          </>
        )}
      </div>
    </div>
  );
}
