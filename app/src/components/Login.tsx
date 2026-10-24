import { useState } from "react";
import { supabase } from "../lib/supabase";
import { Logo } from "./Logo";

export function Login({ recovery = false, onDone }: { recovery?: boolean; onDone?: () => void }) {
  const [mode, setMode] = useState<"login" | "forgot" | "set">(recovery ? "set" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    try {
      if (mode === "login") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      } else if (mode === "forgot") {
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: window.location.origin + window.location.pathname,
        });
        if (error) throw error;
        setMsg("Check your email for a link to set your password.");
      } else {
        if (password.length < 8) throw new Error("Use at least 8 characters.");
        const { error } = await supabase.auth.updateUser({ password });
        if (error) throw error;
        setMsg("Password saved.");
        onDone?.();
      }
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <Logo size={64} animated />
        <h1 className="serif">Panda</h1>
        <p className="muted">
          {mode === "set" ? "Choose your password" : mode === "forgot" ? "Reset your password" : "Welcome back"}
        </p>
        {mode !== "set" && (
          <input type="email" required autoComplete="email" placeholder="Email" value={email}
            onChange={(e) => setEmail(e.target.value)} />
        )}
        {mode !== "forgot" && (
          <input type="password" required autoComplete={mode === "set" ? "new-password" : "current-password"}
            placeholder={mode === "set" ? "New password" : "Password"} value={password}
            onChange={(e) => setPassword(e.target.value)} />
        )}
        <button className="btn primary" disabled={busy}>
          {busy ? "…" : mode === "login" ? "Sign in" : mode === "forgot" ? "Send link" : "Save password"}
        </button>
        {msg && <p className="login-msg">{msg}</p>}
        {mode === "login" && (
          <button type="button" className="link" onClick={() => setMode("forgot")}>Forgot password?</button>
        )}
        {mode === "forgot" && (
          <button type="button" className="link" onClick={() => setMode("login")}>Back to sign in</button>
        )}
      </form>
    </div>
  );
}
