// The sign-in screen: an id and a password. Everyone sees their own shows and episodes after signing in.
import { useEffect, useRef, useState } from "react";
import { auth, User } from "../api";
import { BrandMark } from "../App";

export default function Login({ onDone }: { onDone: (u: User) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => { first.current?.focus(); }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { onDone((await auth.login(username.trim(), password)).user); }
    catch (ex: any) { setErr(ex.message); setPassword(""); }
    finally { setBusy(false); }
  }

  return (
    <div className="signin">
      <form className="signin-card" onSubmit={submit}>
        <div className="signin-brand"><BrandMark /><span>Dub Studio<small>review · edit · render</small></span></div>
        <label>Your id<input ref={first} value={username} autoCapitalize="off" autoCorrect="off" spellCheck={false}
          onChange={(e) => setUsername(e.target.value)} placeholder="the id the owner gave you" /></label>
        <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        {err && <div className="error">{err}</div>}
        <button className="primary" type="submit" disabled={busy || !username.trim() || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        <div className="muted small-text">Your shows and episodes are yours: nobody else signed in here sees them.</div>
      </form>
    </div>
  );
}
