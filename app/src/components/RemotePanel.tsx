import { useCallback, useEffect, useState } from "react";
import { runCommand } from "../lib/commands";

type RemoteJob = { id: string; name: string; running: boolean; exit: number | null; started: number; agent: boolean; box?: string };
type RemoteBox = {
  id: string; alive: boolean; state: string; at?: number; since?: number; cpus?: number; free_mb?: number; load?: number;
  running: string[];
};
type RemoteStatus = RemoteBox & { jobs: RemoteJob[]; remote_id: string; boxes?: RemoteBox[]; max?: number };

const ago = (t?: number) => {
  if (!t) return "";
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return `${(s / 3600).toFixed(1)} h ago`;
};

/** Settings -> Remote Panda: the extra Kaggle notebooks (up to 5) agents send heavy commands and agent tasks to. */
export function RemotePanel({ online }: { online: boolean }) {
  const [st, setSt] = useState<RemoteStatus | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setSt(await runCommand<RemoteStatus>("remote.status", {}, 30000)); setErr(""); }
    catch (e) { setErr((e as Error).message); }
  }, []);

  useEffect(() => {
    if (!online) return;
    load();
    const id = window.setInterval(load, 15000);
    return () => window.clearInterval(id);
  }, [online, load]);

  const stop = async () => {
    setBusy(true);
    try { await runCommand("remote.stop", {}, 30000); await load(); } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };

  if (!online) return <p className="muted">Start Panda to see its remote notebook.</p>;
  const running = (st?.jobs || []).filter((j) => j.running);
  const recent = (st?.jobs || []).filter((j) => !j.running).slice(0, 8);
  // An older worker sends one notebook without `boxes`.
  const boxes: RemoteBox[] = st ? (st.boxes?.length ? st.boxes : [{ ...st, id: st.remote_id }]) : [];
  const anyAlive = boxes.some((b) => b.alive);

  return (
    <div className="remote-panel">
      <p className="muted">
        Up to {st?.max ?? 5} extra Kaggle notebooks, each with its own 4 CPUs and ~30 GB. Agents send heavy commands
        (<code>pjob start --remote</code>) and whole agent tasks (<code>--remote --agent</code>) there, two at a
        time per notebook. A new notebook starts by itself when the running ones are full, and each stops after
        20 minutes with nothing to do. Commands cost no credits;
        agent tasks use your Kiro credits like any helper.
      </p>
      {!st && <div className="set-row"><div className="set-label"><span className="remote-dot" aria-hidden /> Checking…</div></div>}
      {boxes.map((b, i) => (
        <div className="set-row" key={b.id}>
          <div>
            <div className="set-label">
              <span className={`remote-dot ${b.alive ? "on" : ""}`} aria-hidden /> Notebook {b.id.slice(1)}: {b.alive ? "running" : "not running"}
              {b.alive && b.running.length ? ` · ${b.running.length} task${b.running.length > 1 ? "s" : ""}` : ""}
            </div>
            {b.alive && <div className="muted small">
              up since {ago(b.since)} · {b.cpus ?? "?"} CPUs · {b.free_mb != null ? `${(b.free_mb / 1024).toFixed(1)} GB free` : ""}
              {b.load != null ? ` · load ${b.load}` : ""}</div>}
            {!b.alive && <div className="muted small">last seen: {b.at ? ago(b.at) : "never"}</div>}
          </div>
          {i === 0 && anyAlive && <div className="set-control">
            <button className="btn danger" onClick={stop} disabled={busy}>{busy ? "Stopping…" : "Stop all"}</button></div>}
        </div>
      ))}
      {err && <p className="error">{err}</p>}
      <h4>Running</h4>
      {running.length ? running.map((j) => (
        <div className="set-row" key={j.id}><div>
          <div className="set-label">{j.name}{j.agent ? " · agent" : ""}</div>
          <div className="muted small">{j.box ? `notebook ${j.box.slice(1)} · ` : ""}started {ago(j.started)}</div></div></div>
      )) : <p className="muted">Nothing running.</p>}
      {recent.length > 0 && <>
        <h4>Recent</h4>
        {recent.map((j) => (
          <div className="set-row" key={j.id}><div>
            <div className="set-label">{j.name}{j.agent ? " · agent" : ""}</div>
            <div className="muted small">{j.exit === 0 ? "done" : `ended with exit ${j.exit ?? "?"}`} · {ago(j.started)}</div>
          </div></div>
        ))}
      </>}
    </div>
  );
}
