import { useState } from "react";
import { sessionAction } from "../lib/api";
import { IconMoon, IconPower } from "./Icons";

/** Shown where a page needs the worker: one tap starts it. */
export function StartNote({ what }: { what: string }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  return (
    <div className="empty-state">
      <div className="empty-art"><IconMoon size={30} /></div>
      <p>The worker is asleep. Start it to {what}.</p>
      <button className="btn primary" disabled={busy} onClick={async () => {
        setBusy(true);
        try { await sessionAction("start"); } catch (e) { setErr((e as Error).message); setBusy(false); }
      }}><IconPower size={15} /> {busy ? "Starting… (1–3 min)" : "Start"}</button>
      {err && <p className="login-msg">{err}</p>}
    </div>
  );
}
