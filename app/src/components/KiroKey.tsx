import { useEffect, useState } from "react";
import { runCommand } from "../lib/commands";
import { supabase } from "../lib/supabase";

type KeyStatus = { tail: string; next_tail: string; can_change: boolean };
type KeySaved = { ok: boolean; next_tail: string; plan?: string; credits_left?: number; credits_plan?: number };

/** Settings -> Credits: the Kiro API key Panda runs on. Panda checks a new key with Kiro, then saves it
 *  to its private Kaggle config, so it is used from the next Start. Only the last 4 characters are shown. */
export function KiroKeyBar({ online }: { online: boolean }) {
  const [st, setSt] = useState<KeyStatus | null>(null);
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!online) return;
    runCommand<KeyStatus>("kiro.key", { action: "status" }, 30000).then(setSt).catch(() => setSt(null));
  }, [online]);

  if (!online || !st) return null;

  const save = async () => {
    setBusy(true); setErr(""); setMsg("");
    try {
      const r = await runCommand<KeySaved>("kiro.key", { action: "set", key: val.trim() }, 240000);
      setVal("");
      setSt({ ...st, next_tail: r.next_tail });
      // Keep the Developer tab's copy in step, so a later dataset sync doesn't bring the old key back.
      const { data } = await supabase.rpc("list_secrets");
      if (Array.isArray(data) && data.some((x: { name?: string }) => x.name === "DEV__KIRO_API_KEY"))
        await supabase.rpc("set_secret", { p_name: "DEV__KIRO_API_KEY", p_value: val.trim(), p_note: "developer" });
      setMsg(`Saved${r.plan ? ` (${r.plan}` : ""}${r.credits_left != null ? `, ${Math.round(r.credits_left).toLocaleString()} credits left)` : r.plan ? ")" : ""}. `
        + "Panda uses it after the next Stop and Start.");
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };

  return (
    <section className="kiro-key">
      <div className="set-label">Kiro API key</div>
      <div className="muted small">
        In use: {st.tail || "none"}{st.next_tail ? ` · saved for the next Start: ${st.next_tail}` : ""}
      </div>
      {st.can_change ? (
        <form className="kiro-key-row" onSubmit={(e) => { e.preventDefault(); if (val.trim() && !busy) save(); }}>
          <input className="text-input" type="password" autoComplete="off" spellCheck={false}
            placeholder="Paste a new Kiro API key" value={val} onChange={(e) => setVal(e.target.value)} />
          <button className="btn" type="submit" disabled={busy || !val.trim()}>{busy ? "Checking…" : "Save"}</button>
        </form>
      ) : <div className="muted small">Only the account this Panda was set up for can change it.</div>}
      {msg && <div className="small">{msg}</div>}
      {err && <div className="error small">{err}</div>}
    </section>
  );
}
