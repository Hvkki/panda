import { useEffect, useState } from "react";
import { sessionAction, supabase, type WorkerSession } from "../lib/supabase";
import { useT } from "../lib/i18n";
import { IconPower } from "./Icons";

export type Worker = { session: WorkerSession | null; alive: boolean };

/** Live state of the Kaggle worker, from the worker_sessions table. */
export function useWorker(userId: string) {
  const [w, setW] = useState<Worker>({ session: null, alive: false });
  useEffect(() => {
    const isAlive = (s: WorkerSession): boolean => {
      const beat = s.heartbeat_at ? (Date.now() - Date.parse(s.heartbeat_at)) / 1000 : Infinity;
      const age = (Date.now() - Date.parse(s.started_at)) / 1000;
      // A successor that is installing and waiting for the old run to finish heartbeats; it may be older than 15 min.
      return (["running", "stopping"].includes(s.status) && beat < 90) ||
        (["requested", "starting"].includes(s.status) && (age < 900 || beat < 90));
    };
    // During a hand-off the old run and its successor are both alive: show the one that is serving.
    const compute = (rows: WorkerSession[]): Worker => {
      if (!rows.length) return { session: null, alive: false };
      const live = rows.filter(isAlive);
      const s = live.find((r) => r.status === "running") ?? live[0] ?? rows[0];
      return { session: s, alive: isAlive(s) };
    };
    const load = () => supabase.from("worker_sessions").select("*").eq("owner", userId)
      .order("started_at", { ascending: false }).limit(3)
      .then(({ data }) => setW(compute((data as WorkerSession[]) ?? [])));
    load();
    const ch = supabase.channel("worker")
      .on("postgres_changes", { event: "*", schema: "public", table: "worker_sessions", filter: `owner=eq.${userId}` },
        () => load())
      .subscribe();
    const timer = window.setInterval(load, 30000); // heartbeats age out even without events
    return () => { supabase.removeChannel(ch); window.clearInterval(timer); };
  }, [userId]);
  return w;
}

export function SessionChip({ worker }: { worker: Worker }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const s = worker.session;
  // A run that restarts itself before Kaggle's time limit is "restarting", never "offline".
  const restarting = worker.alive && !s?.stop_requested &&
    ((typeof s?.info?.handoff_from === "string" && s.status !== "running") ||
      (s?.status === "stopping" && !!s.info?.handoff));
  const state = !worker.alive ? "off" : restarting ? "starting" : s?.stop_requested || s?.status === "stopping" ? "stopping"
    : s?.status === "running" ? "on" : "starting";

  const [, tick] = useState(0);
  useEffect(() => {
    if (state !== "on" && state !== "starting") return;
    const id = window.setInterval(() => tick((n) => n + 1), 15000);
    return () => window.clearInterval(id);
  }, [state]);
  const mins = s ? Math.floor((Date.now() - Date.parse(s.started_at)) / 60000) : 0;
  const up = mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;

  const click = async () => {
    setBusy(true);
    setErr("");
    try {
      await sessionAction(state === "off" ? "start" : "stop");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const label = state === "on" ? <><span className="session-word">{t("running")} · </span><span className="session-up">{up}</span></> : state === "starting" ? t(restarting ? "restarting" : "starting")
    : state === "stopping" ? t("stopping") : t("offline");
  return (
    <div className="session">
      <button className={`session-chip ${state}`} onClick={click} disabled={busy || state === "stopping"}
        title={state === "off" ? t("start") : state === "on" ? `${t("running")} · ${up} — ${t("stop")}` : t("stop")}>
        <span className="dot" /> <span className="session-label">{busy ? "…" : label}</span>
        {(state === "off" || state === "on") && <span className="session-action"><IconPower size={14} />
          <span className={state === "on" ? "session-action-text" : ""}>{state === "off" ? t("start") : t("stop")}</span></span>}
      </button>
      {err && <div className="session-err" onClick={() => setErr("")}>{err}</div>}
    </div>
  );
}
