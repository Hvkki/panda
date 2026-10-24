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
    usage?: { pct?: number; used_tokens?: number; window_tokens?: number } | null;
    secs?: number;
    kind?: string;
    error?: string;
    timed_out?: boolean;
    files?: string[];
    attachments?: import("./files").Attachment[];
  };
  created_at: string;
};

export type WorkerSession = {
  id: string;
  status: "requested" | "starting" | "running" | "stopping" | "stopped" | "error";
  stop_requested: boolean;
  started_at: string;
  heartbeat_at: string | null;
  stopped_at: string | null;
  info: Record<string, unknown>;
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
