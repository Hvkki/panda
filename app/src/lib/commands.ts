import { supabase, type UsageInfo } from "./supabase";

/**
 * Ask the running worker to do something in Kiro Crew (skills, connectors, settings, usage...).
 * Resolves with the worker's result, or rejects on error/timeout.
 */
export async function runCommand<T = unknown>(op: string, args: Record<string, unknown> = {}, timeoutMs = 45000): Promise<T> {
  const { data: { user } } = await supabase.auth.getUser();
  const { data, error } = await supabase.from("commands").insert({ owner: user?.id, op, args }).select().single();
  if (error || !data) throw error || new Error("could not queue command");
  const id = data.id as number;
  const t0 = Date.now();
  let wait = 700;
  while (Date.now() - t0 < timeoutMs) {
    await new Promise((r) => setTimeout(r, wait));
    wait = Math.min(3000, wait + 300);  // each check is a database read; back off
    const { data: row } = await supabase.from("commands").select("status,result").eq("id", id).single();
    if (row?.status === "done") return row.result as T;
    if (row?.status === "error") throw new Error((row.result as { error?: string })?.error || "failed");
  }
  throw new Error("Panda didn't answer in time — is it online?");
}

/** Shortcut for gateway calls the worker allows (skills, mcp, crons...). */
export const gw = <T = unknown>(method: string, path: string, body?: unknown) =>
  runCommand<T>("gw", { method, path, body });

export type CrewCommands = { commands: { name: string; description: string }[]; backend: string; version?: string };
/** Kiro Crew's slash commands (GET /api/slash-commands) and which engine it runs on. */
export const crewCommands = () => runCommand<CrewCommands>("crew.commands", {}, 30000);

/** One Kiro Crew setting, as the worker sends it (short keys keep the payload small). */
export type CrewSetting = {
  p: string;               // dotted path, e.g. "session.autocompact_pct"
  t: string;               // JSON type: string | integer | number | boolean | array | object
  l: string;               // label
  h?: string;              // help
  e?: (string | number | boolean | null)[]; // enum values from the schema
  d?: unknown;             // default
  v?: unknown;             // current value
  r?: 1;                   // needs a gateway restart
  s?: 1;                   // sensitive (value masked)
  w?: "patch" | "dash";    // how Panda may change it (absent: read-only)
  x?: { type?: string; min?: number; max?: number; max_len?: number; values?: (string | number | boolean)[] };
  k?: string;              // why Panda keeps it locked
};
export type CrewSettings = { items: CrewSetting[]; version?: string; backend?: string; spec?: string; at?: string };
export const crewSettings = () => runCommand<CrewSettings>("crew.settings", {}, 90000);
export const crewSet = (path: string, value: unknown) =>
  runCommand<{ ok: boolean; path: string; value: unknown; clamp?: { stored?: number } }>("crew.set", { path, value }, 45000);

/** Fresh account usage from Kiro Crew (and saved on the worker session for offline viewing). */
export const usageNow = (refresh = false) => runCommand<UsageInfo>("usage", { refresh }, refresh ? 120000 : 45000);

/** Side chat (/btw): a quick question that doesn't interrupt the running answer. */
export const sideAsk = (chatId: string, question: string) =>
  runCommand<{ answer: string; error?: boolean; note?: string }>("side", { chat_id: chatId, question }, 200000);

/** Terminal: run one command in the worker's persistent shell. */
export type TermResult = { output: string; rc: number | null; cwd: string; home?: string; timeout?: boolean; reset?: boolean };
export const termRun = (input: string, timeout = 60) =>
  runCommand<TermResult>("term", { input, timeout }, (timeout + 25) * 1000);

/** Disk browser: list a folder / read a file, under the worker's home only. */
export type FsEntry = { name: string; dir: boolean; size: number; mtime: number; sensitive?: boolean };
export type FsList = { path: string; rel: string; home: string; parent: string | null; quick: string[]; entries: FsEntry[] };
export const fsList = (path?: string) => runCommand<FsList>("fs.list", { path }, 30000);
export type FsFile = { path: string; rel: string; name: string; size?: number; text?: string; binary?: boolean; too_big?: boolean; denied?: string };
export const fsRead = (path: string) => runCommand<FsFile>("fs.read", { path }, 30000);
