// Account page: your own password, and (for the owner's account) who may sign in.
import { useEffect, useState } from "react";
import { api, auth, User } from "../api";
import { ErrorBox, fmtWhen } from "../ui";

export default function Accounts({ me, onMe }: { me: User | null; onMe: (u: User) => void }) {
  const [users, setUsers] = useState<User[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ text: string; err?: boolean } | null>(null);
  const [pw, setPw] = useState({ current: "", next: "", again: "" });
  const [add, setAdd] = useState({ username: "", name: "", password: "", role: "editor" });
  const [busy, setBusy] = useState("");
  const admin = me?.role === "admin";
  const load = () => { if (admin) api.get<User[]>("/api/users").then(setUsers).catch((e) => setErr(e.message)); };
  useEffect(() => { load(); }, [admin]);  // eslint-disable-line
  const say = (text: string, e = false) => setMsg({ text, err: e });

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    if (pw.next !== pw.again) return say("the two new passwords are not the same", true);
    setBusy("pw"); setMsg(null);
    try { onMe((await auth.password(pw.current, pw.next)).user); setPw({ current: "", next: "", again: "" }); say("Password changed. Other devices must sign in again."); }
    catch (ex: any) { say(ex.message, true); } finally { setBusy(""); }
  }
  async function addUser(e: React.FormEvent) {
    e.preventDefault();
    setBusy("add"); setMsg(null);
    try { await api.post("/api/users", add); setAdd({ username: "", name: "", password: "", role: "editor" }); say(`Account ${add.username} made.`); load(); }
    catch (ex: any) { say(ex.message, true); } finally { setBusy(""); }
  }
  async function patch(u: User, body: any, what: string) {
    setBusy(`u${u.id}`); setMsg(null);
    try { await api.patch(`/api/users/${u.id}`, body); say(what); load(); }
    catch (ex: any) { say(ex.message, true); } finally { setBusy(""); }
  }
  async function reset(u: User) {
    const p = prompt(`New password for ${u.username} (at least 6 characters):`);
    if (p) patch(u, { password: p }, `New password set for ${u.username}.`);
  }
  async function remove(u: User) {
    if (!confirm(`Remove the account ${u.username}? Its ${u.shows || 0} show(s) move to your account; no files are deleted.`)) return;
    setBusy(`u${u.id}`); setMsg(null);
    try { await api.del(`/api/users/${u.id}`); say(`Account ${u.username} removed.`); load(); }
    catch (ex: any) { say(ex.message, true); } finally { setBusy(""); }
  }

  return (
    <div className="page narrow">
      <h1>Account</h1>
      {msg && <div className={msg.err ? "error" : "ok-box"}>{msg.text}</div>}
      <ErrorBox error={err} />

      <h3>You</h3>
      <div className="panel">
        <div className="panel-row"><b>{me?.name || me?.username}</b>
          <span className="chip chip-grey">{me?.username}</span>
          <span className={`chip ${admin ? "chip-violet" : "chip-blue"}`}>{admin ? "owner" : "editor"}</span>
        </div>
        <form onSubmit={changePassword}>
          <div className="kv" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
            <label>Current password<input type="password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></label>
            <label>New password<input type="password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></label>
            <label>New password again<input type="password" value={pw.again} onChange={(e) => setPw({ ...pw, again: e.target.value })} /></label>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" type="submit" disabled={busy === "pw" || !pw.current || pw.next.length < 6}>Change my password</button>
            <span className="muted small-text">At least 6 characters. Changing it signs your other devices out.</span>
          </div>
        </form>
      </div>

      {admin && <>
        <h3>Who may sign in</h3>
        <div className="muted small-text" style={{ marginBottom: 10 }}>Each account sees only its own shows and episodes. Your own (owner) account sees them all.</div>
        <table className="table">
          <thead><tr><th>Id</th><th>Name</th><th>Role</th><th>Shows</th><th>Last signed in</th><th></th></tr></thead>
          <tbody>
            {(users || []).map((u) => (
              <tr key={u.id} className={u.active ? "" : "muted"}>
                <td><b>{u.username}</b>{!u.active && <span className="chip chip-grey" style={{ marginLeft: 6 }}>off</span>}</td>
                <td>{u.name}</td>
                <td>{u.role === "admin" ? "owner" : "editor"}</td>
                <td>{u.shows || 0}</td>
                <td className="small-text muted">{fmtWhen(u.last_login) || "never"}</td>
                <td className="right">
                  <button className="small ghost" disabled={busy === `u${u.id}`} onClick={() => reset(u)}>New password</button>
                  {u.id !== me?.id && <button className="small ghost" disabled={busy === `u${u.id}`}
                    onClick={() => patch(u, { active: !u.active }, u.active ? `${u.username} switched off.` : `${u.username} switched on.`)}>{u.active ? "Switch off" : "Switch on"}</button>}
                  {u.id !== me?.id && <button className="small ghost" disabled={busy === `u${u.id}`}
                    onClick={() => patch(u, { role: u.role === "admin" ? "editor" : "admin" }, `${u.username} is now ${u.role === "admin" ? "an editor" : "an owner"}.`)}>Make {u.role === "admin" ? "editor" : "owner"}</button>}
                  {u.id !== me?.id && <button className="small ghost danger" disabled={busy === `u${u.id}`} onClick={() => remove(u)}>Remove</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <h3>Add an account</h3>
        <div className="panel">
          <form onSubmit={addUser}>
            <div className="kv" style={{ gridTemplateColumns: "1fr 1fr 1fr auto" }}>
              <label>Id<input value={add.username} autoCapitalize="off" spellCheck={false} onChange={(e) => setAdd({ ...add, username: e.target.value })} placeholder="e.g. luca" /></label>
              <label>Name<input value={add.name} onChange={(e) => setAdd({ ...add, name: e.target.value })} placeholder="Luca Rossi" /></label>
              <label>Password<input type="password" value={add.password} onChange={(e) => setAdd({ ...add, password: e.target.value })} /></label>
              <label>Role<select value={add.role} onChange={(e) => setAdd({ ...add, role: e.target.value })}><option value="editor">editor</option><option value="admin">owner</option></select></label>
            </div>
            <div className="row" style={{ marginTop: 12 }}>
              <button className="primary" type="submit" disabled={busy === "add" || !add.username.trim() || add.password.length < 6}>Add the account</button>
              <span className="muted small-text">Tell the person their id and password; they can change the password here.</span>
            </div>
          </form>
        </div>
      </>}
    </div>
  );
}
