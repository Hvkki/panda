import { useCallback, useEffect, useState } from "react";
import { AuthError, sessionAction, type WorkerState } from "../lib/api";
import { IconPower } from "./Icons";

/** Live state of the Kaggle worker. `onAuthError` fires when the saved password stops working. */
export function useWorker(onAuthError: () => void) {
  const [state, setState] = useState<WorkerState["state"]>("off");
  const [uptime, setUptime] = useState(0);
  const refresh = useCallback(async (action: "status" | "start" | "stop" = "status") => {
    try {
      const s = await sessionAction(action);
      setState(s.state);
      setUptime(s.uptime ?? 0);
      return s;
    } catch (e) {
      if (e instanceof AuthError) onAuthError();
      throw e;
    }
  }, [onAuthError]);
  useEffect(() => {
    refresh().catch(() => {});
    const id = window.setInterval(() => refresh().catch(() => {}), state === "starting" ? 5000 : 30000);
    return () => window.clearInterval(id);
  }, [refresh, state]);
  return { state, uptime, refresh, setState };
}

export function SessionChip({ worker }: { worker: ReturnType<typeof useWorker> }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const { state } = worker;
  const mins = Math.floor(worker.uptime / 60);
  const up = mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;

  const click = async () => {
    setBusy(true);
    setErr("");
    try {
      const s = await worker.refresh(state === "off" ? "start" : "stop");
      if (s.note) setErr(s.note);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const label = state === "on" ? `Online · ${up}` : state === "starting" ? "Starting…" : "Offline";
  return (
    <div className="session">
      <button className={`session-chip ${state}`} onClick={click} disabled={busy || state === "starting"}
        title={state === "off" ? "Start the worker" : "Stop the worker"}>
        <span className="dot" /> {busy ? "…" : label}
        {state !== "starting" && <span className="session-action"><IconPower size={14} />{state === "off" ? "Start" : "Stop"}</span>}
      </button>
      {err && <div className="session-err" onClick={() => setErr("")}>{err}</div>}
    </div>
  );
}
