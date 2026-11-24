import { useState } from "react";
import { AuthError, sessionAction, setPassword } from "../lib/api";
import { Logo } from "./Logo";

export function Login({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    try {
      await sessionAction("status", pw); // 401 means the password is wrong
      setPassword(pw);
      onDone();
    } catch (err) {
      setMsg(err instanceof AuthError ? "Wrong password." : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <Logo size={64} animated />
        <h1 className="serif">Panda</h1>
        <p className="muted">Enter your password</p>
        <input type="password" required autoComplete="current-password" placeholder="Password" value={pw}
          onChange={(e) => setPw(e.target.value)} />
        <button className="btn primary" disabled={busy}>{busy ? "…" : "Sign in"}</button>
        {msg && <p className="login-msg">{msg}</p>}
      </form>
    </div>
  );
}
