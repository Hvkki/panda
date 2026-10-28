import { createClient } from "@supabase/supabase-js";
import { SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "./config";

export const supabase = createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
  realtime: { params: { eventsPerSecond: 20 } },
});

/** Run `cb` whenever the app comes back: tab/app visible again, window focused, network back. A phone that
 *  sleeps (or a background tab) drops the live connection, and every change made meanwhile is never sent
 *  again -- the chat looked frozen until a reload. Callers re-read their data here. Returns a cleanup. */
export function onResume(cb: () => void): () => void {
  let last = 0;
  const fire = () => {
    if (document.visibilityState !== "visible") return;
    const now = Date.now();
    if (now - last < 1500) return; // visible + focus arrive together
    last = now;
    cb();
  };
  document.addEventListener("visibilitychange", fire);
  window.addEventListener("focus", fire);
  window.addEventListener("online", fire);
  return () => {
    document.removeEventListener("visibilitychange", fire);
    window.removeEventListener("focus", fire);
    window.removeEventListener("online", fire);
  };
}

/** For `.subscribe(...)`: re-read when the live channel (re)joins, so events missed while it was down are
 *  fetched. The first join is skipped -- the caller already loaded. */
export function onRejoin(load: () => void) {
  let joined = false;
  return (status: string) => {
    if (status !== "SUBSCRIBED") return;
    if (joined) load();
    joined = true;
  };
}

export type Chat = {
  id: string;
  owner: string;
  title: string;
  agent?: string;
  model: string;
  effort: string;
  pinned: boolean;
  archived: boolean;
  slot_id: string | null;
  project_id: string | null;
  created_at: string;
  updated_at: string;
};

/** Kiro Crew's per-turn numbers (`meta.turn_stats` on its reply row), copied by the worker. */
export type TurnStats = { elapsed_ms?: number; ttft_ms?: number; model?: string; credits?: number; retries?: number };

/** The browser's last screen of a turn, kept in Storage (panda-files) by the worker. */
export type BrowserShot = { url?: string; title?: string; shot?: string; w?: number; h?: number; at?: string };

export type ContextUsage = { pct?: number; used_tokens?: number; window_tokens?: number; reset?: boolean };

export type Message = {
  id: number;
  chat_id: string;
  owner: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  thinking: string;
  status: "pending" | "processing" | "streaming" | "done" | "error" | "waiting";
  reply_to: number | null;
  decision: "approved" | "rejected" | "trust" | null;
  cancel: boolean;
  meta: {
    model?: string;
    effort?: string;
    tools?: string[];
    queued?: boolean;       // Crew queued it behind a turn that was still running
    usage?: ContextUsage | null;
    secs?: number;
    kind?: string;
    error?: string;
    timed_out?: boolean;
    files?: string[];
    attachments?: import("./files").Attachment[];
    /** Sent while Panda was answering; the worker steers it into the running turn. */
    steer?: boolean;
    stopped?: boolean;
    steered?: "steered" | "queued";
    /** Kiro credits this reply used (Kiro Crew's meteringUsage for the turn). */
    credits?: number;
    turn_stats?: TurnStats;
    /** The slash command this message is, or answers ("/compact"). */
    command?: string;
    browser?: BrowserShot;
    replayed?: boolean;
    /** Live timeline: seconds into the answer when each step in `tools` started, the full step count,
     *  and the worker's note on the current step (see Timeline.tsx). */
    steps_at?: number[];
    n_steps?: number;
    /** Worker-made "carry on" after a restart cut the previous answer off. */
    auto_continue?: boolean;
    /** Job watcher's note to the agent (a --notify job ended or looks stuck); shown only as "Haiku steered". */
    haiku_steer?: boolean;
    interrupted?: boolean;
    tl?: import("../components/Timeline").TimelineNote | null;
  };
  created_at: string;
};

/** Kiro account usage as Kiro Crew reports it (GET /api/sessions/usage). */
export type AccountUsage = {
  credits_used?: number;
  credits_plan?: number;
  credits_overage?: number;
  percentage?: number;
  plan?: string;
  resets?: string;
  overage_rate?: number;
  cost_usd?: number;
  bonus_credits?: { name: string; used: number; total: number; days_left?: number }[];
  available?: boolean;
  reason?: string;
  stale?: boolean;
  source?: string;
};

export type UsageInfo = {
  account?: AccountUsage;
  /** Credits per day on the gateway (all activity, background work included). */
  daily?: { date: string; credits: number; messages?: number }[];
  /** This month's gateway credits split into chat replies and background work, counted since `since`. */
  split?: { replies: number; background: number; titles: number; memory: number; other: number; since: string | null };
  at?: string;
};

export type WorkerSession = {
  id: string;
  status: "requested" | "starting" | "running" | "stopping" | "stopped" | "error";
  stop_requested: boolean;
  started_at: string;
  heartbeat_at: string | null;
  stopped_at: string | null;
  info: { usage?: UsageInfo; crew?: string; [k: string]: unknown };
};

export type Notification = {
  id: number;
  text: string;
  source: string;
  read: boolean;
  meta: { title?: string; kind?: string; slug?: string; decision?: string; [k: string]: unknown };
  created_at: string;
};

/** Start / Stop / Status through the server function (it holds the Kaggle token). */
export async function sessionAction(action: "start" | "stop" | "status") {
  const { data, error } = await supabase.functions.invoke("session", { body: { action } });
  if (error) {
    let detail = error.message;
    try {
      const body = await (error as { context?: Response }).context?.json();
      if (body?.error) detail = body.error;
    } catch { /* keep generic message */ }
    throw new Error(detail);
  }
  return data as { session: WorkerSession | null; alive: boolean; note?: string };
}
