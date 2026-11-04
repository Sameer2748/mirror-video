import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Profile, Show } from "../api";
import { ErrorBox, Picker, uploadFile } from "../ui";

// The profile fields the editor sets here; everything else keeps the preset's value. Sentences, groups, max drift,
// subtitle rows and the ending are the same for every show (owner, 2026-10-20) and are not set here.
const FIELDS: { key: string; label: string; type: "text" | "number" | "bool" | "select"; options?: string[]; hint?: string }[] = [
  { key: "dub_lang", label: "Dub language", type: "text", hint: "it or IT, fr or FR, de…" },
  { key: "src_lang", label: "Source language", type: "text", hint: "fr or FR, en, de…" },
  { key: "labels", label: "Labels (dub,source)", type: "text", hint: "used in file names, e.g. IT,US" },
  { key: "episode_offset", label: "Episode offset", type: "number", hint: "source episode = dub episode + this (MVS: 2)" },
  { key: "transitions", label: "Transitions", type: "select", options: ["auto", "manual"],
    hint: "automatic = a transition on every cut at Render (your own picks win); manual = only the cuts you give one in the editor" },
  { key: "sub_style", label: "Subtitle style", type: "select", options: ["box", "strip", "dynamic"], hint: "strip covers burned-in captions (TDMB); dynamic = strip 2 or 3 lines tall following the source caption, cards fill it (HIH)" },
  { key: "output_dir", label: "Output folder", type: "text", hint: "inside the show folder; {N} = episode number" },
];

export default function NewShow() {
  const nav = useNavigate();
  const [presets, setPresets] = useState<Record<string, Profile> | null>(null);
  const [preset, setPreset] = useState("");
  const [name, setName] = useState(""); const [code, setCode] = useState(""); const [folder, setFolder] = useState("");
  const [prof, setProf] = useState<Profile>({});
  const [picking, setPicking] = useState(false);
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const [where, setWhere] = useState<"upload" | "folder">("upload");     // files uploaded here, or read from a Mac folder
  const [sheet, setSheet] = useState<File | null>(null);
  const [sheetUp, setSheetUp] = useState(0);

  useEffect(() => { api.get<Record<string, Profile>>("/api/presets").then(setPresets).catch((e) => setErr(e.message)); }, []);

  function choose(k: string) {
    setPreset(k);
    const p = { ...presets![k] }; setProf(p);
    if (k !== "NEW") { setCode(k); if (!name) setName(k); }
  }

  async function save() {
    setBusy(true); setErr(null);
    try {
      const s = await api.post<Show>("/api/shows", { name, code, folder: where === "folder" ? folder : "", preset, profile: prof });
      if (sheet) await uploadFile(`/api/shows/${s.id}/sheet`, sheet, (f) => setSheetUp(f));
      nav(`/shows/${s.id}`);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="page narrow">
      <h1>New show</h1>
      <h3>1. Pick a preset</h3>
      <div className="cards">
        {presets && Object.entries(presets).map(([k, p]) => (
          <button key={k} className={`card preset ${preset === k ? "selected" : ""}`} onClick={() => choose(k)}>
            <div className="card-title">{k}</div>
            <div className="small-text">{p.name}</div>
            <div className="muted small-text">
              {p.sub_style} subs{p.transitions ? " · automatic transitions" : " · manual transitions"}
            </div>
          </button>
        ))}
      </div>
      {preset && (
        <>
          <h3>2. Name and files</h3>
          <div className="form">
            <label>Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="Wolves of Blood Moon" /></label>
            <label>Code<input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="WBM" />
              <span className="hint">used in output file names: {code || "CODE"}_EP17_…</span></label>
            <label>Where the episode files come from
              <select value={where} onChange={(e) => setWhere(e.target.value as any)}>
                <option value="upload">Uploaded here (kept in cloud storage)</option>
                <option value="folder">A folder on this Mac</option>
              </select>
              <span className="hint">{where === "upload"
                ? "For each episode you upload the source video, the dub audio and the script. They go to R2 and the workers take them from there."
                : "The app only reads the inputs in that folder; outputs go to the output folder below."}</span>
            </label>
            {where === "folder" && (
              <label>Show folder
                <div className="row">
                  <input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="/Users/…/WBM US-IT" />
                  <button onClick={() => setPicking(true)}>Browse…</button>
                </div>
              </label>
            )}
            <label>Localisation sheet{where === "folder" ? " (optional)" : ""}
              <div className="row">
                <input type="file" accept=".csv,.xlsx,.xls" onChange={(e) => setSheet(e.target.files?.[0] || null)} />
                {sheet && sheetUp > 0 && sheetUp < 1 && <span className="muted small-text">{Math.round(sheetUp * 100)}%</span>}
              </div>
              <span className="hint">Two columns: source name | target name. Sent to Claude with every episode's matching.
                {where === "folder" ? " Found in the show folder when you leave this empty." : ""}</span>
            </label>
          </div>
          <h3>3. Profile</h3>
          <div className="muted small-text" style={{ marginBottom: 10 }}>Always on for every show: the lip scan (our own scan of the source video). Matching keeps sentences whole; only a very long one (20 words or more) is split into clauses.</div>
          <div className="form grid2">
            {FIELDS.map((f) => (
              <label key={f.key} className={f.type === "bool" ? "check" : ""}>
                {f.type === "bool" ? (
                  <><input type="checkbox" checked={!!prof[f.key]} onChange={(e) => setProf({ ...prof, [f.key]: e.target.checked })} /> {f.label}</>
                ) : (
                  <>{f.label}
                    {f.key === "transitions" ? (
                      <select value={prof.transitions ? "auto" : "manual"} onChange={(e) => setProf({ ...prof, transitions: e.target.value === "auto" })}>
                        <option value="auto">Automatic at render (every cut)</option>
                        <option value="manual">Manual (the editor adds them)</option>
                      </select>
                    ) : f.type === "select" ? (
                      <select value={prof[f.key] ?? ""} onChange={(e) => setProf({ ...prof, [f.key]: e.target.value })}>
                        {f.options!.map((o) => <option key={o}>{o}</option>)}
                      </select>
                    ) : (
                      <input type={f.type} value={prof[f.key] ?? ""} onChange={(e) => setProf({ ...prof, [f.key]: f.type === "number" ? Number(e.target.value) : e.target.value })} />
                    )}
                  </>
                )}
                {f.hint && <span className="hint">{f.hint}</span>}
              </label>
            ))}
          </div>
          <ErrorBox error={err} />
          <div className="actions">
            <button className="primary" disabled={busy || !name || !code || (where === "folder" && !folder)} onClick={save}>
              {busy ? "Creating…" : "Create show"}
            </button>
          </div>
        </>
      )}
      {picking && <Picker mode="dir" start={folder || "~/Downloads"} onClose={() => setPicking(false)} onPick={(p) => { setFolder(p); setPicking(false); }} />}
    </div>
  );
}
