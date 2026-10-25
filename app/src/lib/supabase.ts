import { createClient } from "@supabase/supabase-js";
import { SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "./config";

export const supabase = createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
  realtime: { params: { eventsPerSecond: 20 } },
});

export type Chat = {
  id: string;
  owner: string;
  title: string;
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
    usage?: ContextUsage | null;
    secs?: number;
    kind?: string;
    error?: string;
    timed_out?: boolean;
    files?: string[];
    attachments?: import("./files").Attachment[];
    /** Sent while Panda was answering; the worker steers it into the running turn. */
    steer?: boolean;
    steered?: "steered" | "queued";
    /** Kiro credits this reply used (Kiro Crew's meteringUsage for the turn). */
    credits?: number;
    turn_stats?: TurnStats;
    /** The slash command this message is, or answers ("/compact"). */
    command?: string;
    browser?: BrowserShot;
    replayed?: boolean;
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
  meta: { title?: string; kind?: string };
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
