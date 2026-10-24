import { supabase } from "./supabase";

/**
 * Ask the running worker to do something in Kiro Crew (skills, connectors,
 * scheduled tasks). Resolves with the worker's result, or rejects on error/timeout.
 */
export async function runCommand<T = unknown>(op: string, args: Record<string, unknown> = {}, timeoutMs = 45000): Promise<T> {
  const { data: { user } } = await supabase.auth.getUser();
  const { data, error } = await supabase.from("commands").insert({ owner: user?.id, op, args }).select().single();
  if (error || !data) throw error || new Error("could not queue command");
  const id = data.id as number;
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    await new Promise((r) => setTimeout(r, 900));
    const { data: row } = await supabase.from("commands").select("status,result").eq("id", id).single();
    if (row?.status === "done") return row.result as T;
    if (row?.status === "error") throw new Error((row.result as { error?: string })?.error || "failed");
  }
  throw new Error("Panda didn't answer in time — is it online?");
}

/** Shortcut for gateway calls the worker allows (skills, mcp, crons...). */
export const gw = <T = unknown>(method: string, path: string, body?: unknown) =>
  runCommand<T>("gw", { method, path, body });
