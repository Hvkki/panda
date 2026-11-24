import { useCallback, useEffect, useState } from "react";
import { getUsage, type PlanUsage, type PlanWindow } from "../lib/api";
import type { Chat } from "../lib/store";
import { IconChevron, IconRetry, IconX } from "./Icons";

/** 111100 -> "111.1k", 1000000 -> "1M" */
export const fmtTokens = (n: number) =>
  n >= 1e6 ? `${+(n / 1e6).toFixed(n % 1e6 ? 1 : 0)}M` : n >= 1000 ? `${+(n / 1000).toFixed(1)}k` : String(n);

/** Context fill and token totals of a chat, from the usage the worker sends after each turn. */
export function chatUsage(chat: Chat | null) {
  const turns = (chat?.messages || []).filter((m) => m.role === "assistant" && m.usage);
  const last = turns[turns.length - 1]?.usage;
  const sum = (k: "input" | "output" | "cache_read" | "cache_write" | "cost") => turns.reduce((a, m) => a + (m.usage![k] || 0), 0);
  return {
    turns: turns.length,
    ctx: last?.ctx || 0,
    window: last?.window || 200000,
    pct: last ? Math.min(100, (last.ctx / (last.window || 200000)) * 100) : 0,
    input: sum("input"), output: sum("output"), cacheRead: sum("cache_read"), cacheWrite: sum("cache_write"), cost: sum("cost"),
  };
}

/** Plan usage from the worker, refreshed every minute and whenever a chat reports new limits. */
export function usePlanUsage(online: boolean) {
  const [data, setData] = useState<PlanUsage | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const load = useCallback((fresh = false) => {
    if (!online) return;
    setLoading(true);
    getUsage(fresh).then((d) => { setData(d); setErr(""); }).catch((e) => setErr((e as Error).message)).finally(() => setLoading(false));
  }, [online]);
  useEffect(() => {
    load();
    const id = window.setInterval(() => load(), 120000);
    const onLimits = () => load();
    window.addEventListener("panda:limits", onLimits);
    return () => { window.clearInterval(id); window.removeEventListener("panda:limits", onLimits); };
  }, [load]);
  return { data, err, loading, load };
}

/** Small progress ring for the top bar. */
export function UsageRing({ pct, size = 22 }: { pct: number; size?: number }) {
  const r = 9, c = 2 * Math.PI * r;
  const tone = pct >= 90 ? "var(--danger)" : pct >= 70 ? "var(--accent)" : "var(--blue)";
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <circle cx="12" cy="12" r={r} fill="none" stroke="var(--line)" strokeWidth="2.6" />
      <circle cx="12" cy="12" r={r} fill="none" stroke={tone} strokeWidth="2.6" strokeLinecap="round"
        strokeDasharray={`${(Math.max(pct, 2) / 100) * c} ${c}`} transform="rotate(-90 12 12)" />
    </svg>
  );
}

const fmtReset = (t: number | null) => {
  if (!t) return "";
  const d = new Date(t * 1000);
  const soon = d.getTime() - Date.now() < 24 * 3600 * 1000;
  return `Resets ${d.toLocaleString(undefined, soon ? { hour: "numeric", minute: "2-digit" } : { weekday: "short", hour: "numeric", minute: "2-digit" })}`;
};

const WEEKLY: Record<string, string> = {
  seven_day: "All models", seven_day_opus: "Opus", seven_day_sonnet: "Sonnet", seven_day_oauth_apps: "Apps",
};

function Meter({ title, pct, sub, right, children }: { title: string; pct: number; sub?: string; right?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="us-card">
      <div className="us-row">
        <span className="us-title">{title}</span>
        <span className="muted">{right ?? `${Math.round(pct)}% used`}</span>
      </div>
      <div className="us-bar"><span style={{ width: `${Math.min(100, Math.max(pct, pct > 0 ? 1.5 : 0))}%` }} className={pct >= 90 ? "hot" : ""} /></div>
      {sub && <div className="us-sub muted">{sub}</div>}
      {children}
    </div>
  );
}

export function UsageSheet({ chat, online, plan, onClose }: {
  chat: Chat | null; online: boolean; plan: ReturnType<typeof usePlanUsage>; onClose: () => void;
}) {
  const u = chatUsage(chat);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    plan.load(true);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const w = plan.data?.windows || {};
  const weekly = Object.entries(w).filter(([k]) => k.startsWith("seven_day")) as [string, PlanWindow][];
  const extra = w.extra_usage;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal usage" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Usage">
        <div className="us-head">
          <button className="icon-btn" onClick={onClose} aria-label="Close"><IconX /></button>
          <h2>Usage</h2>
          <button className="icon-btn" onClick={() => plan.load(true)} aria-label="Refresh" disabled={!online || plan.loading}>
            <span className={plan.loading ? "spin" : ""} style={{ display: "inline-grid" }}><IconRetry /></span>
          </button>
        </div>
        <div className="modal-body">
          <div className="us-label">Context</div>
          <div className="us-card">
            <button className="us-row as-btn" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
              <span className="us-title">Context window</span>
              <span className="muted us-right">{Math.round(u.pct)}% used <span className={`chev ${open ? "up" : ""}`}><IconChevron size={16} /></span></span>
            </button>
            <div className="us-bar"><span style={{ width: `${Math.max(u.pct, u.ctx ? 1.5 : 0)}%` }} className={u.pct >= 90 ? "hot" : ""} /></div>
            <div className="us-sub muted">{chat ? `${fmtTokens(u.ctx)} of ${fmtTokens(u.window)} tokens` : "Open a chat to see its context"}</div>
            {open && (
              <div className="us-table">
                <div><span>Turns</span><span>{u.turns}</span></div>
                <div><span>Input tokens</span><span>{u.input.toLocaleString()}</span></div>
                <div><span>Output tokens</span><span>{u.output.toLocaleString()}</span></div>
                <div><span>Cache read</span><span>{u.cacheRead.toLocaleString()}</span></div>
                <div><span>Cache write</span><span>{u.cacheWrite.toLocaleString()}</span></div>
                <div><span>Total this chat</span><span>{(u.input + u.output + u.cacheRead + u.cacheWrite).toLocaleString()}</span></div>
                {u.cost > 0 && <div><span>API-price equivalent</span><span>${u.cost.toFixed(2)}</span></div>}
              </div>
            )}
          </div>

          {!online ? (
            <p className="muted small us-note">Start the worker to see your plan usage.</p>
          ) : !plan.data && !plan.err ? (
            <p className="muted small us-note">Loading plan usage…</p>
          ) : (
            <>
              <div className="us-label">Plan usage</div>
              {w.five_hour ? <Meter title="Current session" pct={w.five_hour.pct} sub={fmtReset(w.five_hour.resets_at)} />
                : <div className="us-card muted small">{plan.err || plan.data?.error || "No data yet. Send a message and refresh."}</div>}
              {weekly.length > 0 && <div className="us-label">Weekly limits</div>}
              {weekly.map(([k, x]) => <Meter key={k} title={WEEKLY[k] || k} pct={x.pct} sub={fmtReset(x.resets_at)} />)}
              {extra && (
                <>
                  <div className="us-label">Extra usage</div>
                  <Meter title="This month" pct={extra.pct} />
                </>
              )}
              {plan.data?.source === "chat" && <p className="muted small us-note">From your latest chat. Refresh for live numbers.</p>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
