import { useEscape } from "../lib/dismiss";
import { Backdrop } from "./Panels";
import { useCallback, useEffect, useState } from "react";
import { getTokens, getUsage, type PlanUsage, type PlanWindow, type TrackedCall } from "../lib/api";
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
    window: last?.window || 1000000,
    pct: last ? Math.min(100, (last.ctx / (last.window || 1000000)) * 100) : 0,
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

const shortModel = (m: string) => m.replace(/^claude-/, "").replace(/-(\d+)-(\d+)/, " $1.$2");
const pctDelta = (d: number) => (d > 0 ? `+${Math.round(d * 100)}%` : "");

/** Every model request since the 5-hour window started, from the worker's tracker, grouped by chat. */
function TokenTracker({ online, chats, since, reload }: { online: boolean; chats: Chat[]; since: number; reload: number }) {
  const [calls, setCalls] = useState<TrackedCall[] | null>(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (online) getTokens().then((c) => { setCalls(c); setErr(""); }).catch((e) => setErr((e as Error).message));
  }, [online, reload]);
  if (!online) return null;
  if (err || !calls) return <div className="us-card muted small">{err ? `Tracker: ${err}` : "Loading…"}</div>;
  const all = [...calls].sort((a, b) => a.at - b.at);
  // The plan-usage share each call moved (Anthropic reports it with every response, in whole percents).
  const delta = new Map<TrackedCall, number>();
  let prev: number | null = null;
  for (const c of all) {
    if (c.u5h != null) { if (prev != null && c.u5h > prev) delta.set(c, c.u5h - prev); prev = c.u5h; }
  }
  const win = all.filter((c) => c.at >= since);
  if (!win.length) return <div className="us-card muted small">No model requests recorded in this window yet. Requests are recorded from the worker's next start on.</div>;
  const tot = (xs: TrackedCall[]) => xs.reduce((a, c) => ({ n: a.n + 1, in: a.in + c.in, cw: a.cw + c.cw, cr: a.cr + c.cr, out: a.out + c.out,
    d: a.d + (delta.get(c) || 0) }), { n: 0, in: 0, cw: 0, cr: 0, out: 0, d: 0 });
  const t = tot(win);
  const title = (id: string) => (id ? chats.find((c) => c.id === id)?.title || (id.startsWith("sched-") ? "Scheduled task" : "Other chat") : "Outside a Panda chat");
  const groups = new Map<string, TrackedCall[]>();
  for (const c of win) groups.set(c.chat, [...(groups.get(c.chat) || []), c]);
  const byChat = [...groups.entries()].map(([id, xs]) => ({ id, ...tot(xs), models: [...new Set(xs.map((x) => shortModel(x.model)))].join(", ") }))
    .sort((a, b) => b.cr + b.cw * 10 + b.out * 20 - (a.cr + a.cw * 10 + a.out * 20));
  const fmtT = (s: number) => new Date(s * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" });
  return (
    <div className="us-card">
      <div className="us-row"><span className="us-title">All model requests</span><span className="muted">{t.n} this window</span></div>
      <div className="us-table">
        <div><span>New input</span><span>{t.in.toLocaleString()}</span></div>
        <div><span>Cache write</span><span>{t.cw.toLocaleString()}</span></div>
        <div><span>Cache read</span><span>{t.cr.toLocaleString()}</span></div>
        <div><span>Output (incl. thinking)</span><span>{t.out.toLocaleString()}</span></div>
      </div>
      <div className="us-table">
        {byChat.map((g) => (
          <div key={g.id || "-"}><span>{title(g.id)} <span className="muted">· {g.n} req · {g.models}</span></span>
            <span>{fmtTokens(g.in + g.cw + g.cr + g.out)}{g.d > 0 ? ` · ${pctDelta(g.d)}` : ""}</span></div>
        ))}
      </div>
      <button className="us-row as-btn us-more" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="muted small">Every request</span><span className={`chev ${open ? "up" : ""}`}><IconChevron size={16} /></span>
      </button>
      {open && (
        <div className="us-table us-calls">
          {[...win].reverse().slice(0, 200).map((c, i) => (
            <div key={i} title={`${c.msgs} messages, ${c.tools} tools, ${c.ms} ms, HTTP ${c.status}`}>
              <span>{fmtT(c.at)} {shortModel(c.model)} <span className="muted">{title(c.chat)}{c.tools && c.tools < 8 ? " (subagent?)" : ""}</span></span>
              <span>{fmtTokens(c.in + c.cw)}↑ {fmtTokens(c.cr)}⟳ {fmtTokens(c.out)}↓{c.u5h != null ? ` ${Math.round(c.u5h * 100)}%` : ""}{delta.get(c) ? ` (${pctDelta(delta.get(c)!)})` : ""}</span>
            </div>
          ))}
        </div>
      )}
      <div className="us-sub muted" style={{ marginTop: 8 }}>↑ new input + cache write · ⟳ cache read · ↓ output · % = 5-hour usage after the request. Recorded from the worker's network traffic, so nothing is left out.</div>
    </div>
  );
}

export function UsageSheet({ chat, chats, online, plan, onClose }: {
  chat: Chat | null; chats: Chat[]; online: boolean; plan: ReturnType<typeof usePlanUsage>; onClose: () => void;
}) {
  const u = chatUsage(chat);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    plan.load(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEscape(onClose);
  const w = plan.data?.windows || {};
  const weekly = Object.entries(w).filter(([k]) => k.startsWith("seven_day")) as [string, PlanWindow][];
  const extra = w.extra_usage;
  return (
    <Backdrop onClose={onClose}>
      <div className="modal usage" role="dialog" aria-modal="true" aria-label="Usage">
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
              <div className="us-label">Token tracker</div>
              <TokenTracker online={online} chats={chats} reload={plan.data?.at || 0}
                since={w.five_hour?.resets_at ? w.five_hour.resets_at - 5 * 3600 : Date.now() / 1000 - 5 * 3600} />
              {plan.data?.source === "chat" && <p className="muted small us-note">From your latest chat. Refresh for live numbers.</p>}
            </>
          )}
        </div>
      </div>
    </Backdrop>
  );
}
