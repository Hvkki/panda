import { useEffect, useState } from "react";
import { supabase, type AccountUsage, type UsageInfo, type WorkerSession } from "./supabase";

/** Credits Panda's own replies used, summed from the messages table (works offline too). */
export type Ledger = {
  today: number;
  week: number;
  month: number;
  replies: number;
  days: { date: string; credits: number }[];          // last 14 days, oldest first
  byModel: { model: string; credits: number; replies: number }[];
  byChat: { chatId: string; credits: number }[];
};

const dayKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export function useLedger(userId: string, refreshKey = 0): Ledger | null {
  const [ledger, setLedger] = useState<Ledger | null>(null);
  useEffect(() => {
    let alive = true;
    const now = new Date();
    const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
    const twoWeeks = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 13);
    const since = monthStart < twoWeeks ? monthStart : twoWeeks;
    supabase.from("messages").select("created_at, chat_id, credits:meta->credits, model:meta->>model")
      .eq("owner", userId).eq("role", "assistant").gte("created_at", since.toISOString())
      .not("meta->credits", "is", null).order("id", { ascending: false }).limit(5000)
      .then(({ data }) => {
        if (!alive) return;
        const rows = (data || []) as { created_at: string; chat_id: string; credits: number | string | null; model: string | null }[];
        const today = dayKey(now);
        const weekStart = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 6);
        const days = new Map<string, number>();
        for (let i = 13; i >= 0; i--) days.set(dayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - i)), 0);
        const models = new Map<string, { credits: number; replies: number }>();
        const chats = new Map<string, number>();
        const out = { today: 0, week: 0, month: 0, replies: 0 };
        for (const r of rows) {
          const c = Number(r.credits);
          if (!Number.isFinite(c)) continue;
          const at = new Date(r.created_at);
          const k = dayKey(at);
          if (days.has(k)) days.set(k, (days.get(k) || 0) + c);
          if (k === today) out.today += c;
          if (at >= weekStart) out.week += c;
          if (at >= monthStart) {
            out.month += c;
            out.replies += 1;
            const m = models.get(r.model || "?") || { credits: 0, replies: 0 };
            m.credits += c; m.replies += 1;
            models.set(r.model || "?", m);
            chats.set(r.chat_id, (chats.get(r.chat_id) || 0) + c);
          }
        }
        setLedger({
          ...out,
          days: [...days].map(([date, credits]) => ({ date, credits })),
          byModel: [...models].map(([model, v]) => ({ model, ...v })).sort((a, b) => b.credits - a.credits),
          byChat: [...chats].map(([chatId, credits]) => ({ chatId, credits })).sort((a, b) => b.credits - a.credits).slice(0, 5),
        });
      });
    return () => { alive = false; };
  }, [userId, refreshKey]);
  return ledger;
}

/**
 * The Kiro account's numbers (used of plan, reset date) from the newest worker session that has
 * them. The worker refreshes them every few minutes while it runs and keeps the last reading.
 */
export function useAccountUsage(userId: string, current: WorkerSession | null, override?: UsageInfo | null): UsageInfo | null {
  const live = current?.info?.usage;
  const [stored, setStored] = useState<UsageInfo | null>(null);
  useEffect(() => {
    if (live) return;
    let alive = true;
    supabase.from("worker_sessions").select("info").eq("owner", userId).not("info->usage", "is", null)
      .order("started_at", { ascending: false }).limit(1)
      .then(({ data }) => { if (alive) setStored(((data?.[0]?.info as WorkerSession["info"])?.usage) || null); });
    return () => { alive = false; };
  }, [userId, live]);
  return override || live || stored;
}

export const hasPlan = (a?: AccountUsage | null): a is AccountUsage & { credits_plan: number; credits_used: number } =>
  !!a && typeof a.credits_plan === "number" && a.credits_plan > 0 && typeof a.credits_used === "number";

/** 1234.5 → "1,235"; 12.34 → "12.3"; 0.372 → "0.37"; tiny → "<0.01". */
export function fmtCredits(n: number | undefined | null): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs > 0 && abs < 0.01) return "<0.01";
  const digits = abs >= 1000 ? 0 : abs >= 10 ? 1 : 2;
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export const fmtTokens = (n?: number) =>
  n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : String(n);

export function resetsLabel(resets?: string): string {
  if (!resets) return "";
  const d = new Date(resets + (resets.length === 10 ? "T00:00:00" : ""));
  if (Number.isNaN(d.getTime())) return resets;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
