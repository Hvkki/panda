import { useState } from "react";
import type { Worker } from "./SessionChip";
import type { UsageInfo } from "../lib/supabase";
import { usageNow } from "../lib/commands";
import { modelName } from "../lib/models";
import { useT } from "../lib/i18n";
import { fmtCredits, hasPlan, resetsLabel, useAccountUsage, useLedger } from "../lib/usage";
import { IconCoins, IconRetry, IconSpinner } from "./Icons";

const pctOf = (used: number, plan: number) => Math.max(0, Math.min(100, (used / plan) * 100));

/** Credits: the Kiro account's month (when Kiro Crew can read it) and what Panda's replies used. */
export function UsagePanel({ userId, worker, online }: { userId: string; worker: Worker; online: boolean }) {
  const t = useT();
  const [refreshKey, setRefreshKey] = useState(0);
  const [fresh, setFresh] = useState<UsageInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const usage = useAccountUsage(userId, worker.session, fresh);
  const ledger = useLedger(userId, refreshKey);
  const a = usage?.account;
  const plan = hasPlan(a) ? a : null;

  const refresh = async () => {
    setBusy(true);
    setErr("");
    try { setFresh(await usageNow(true)); } catch (e) { setErr((e as Error).message); }
    setBusy(false);
    setRefreshKey((k) => k + 1);
  };

  const gwMonth = (() => {
    const now = new Date();
    const prefix = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
    const rows = (usage?.daily || []).filter((d) => d.date.startsWith(prefix));
    return rows.length ? rows.reduce((s, d) => s + d.credits, 0) : null;
  })();
  const maxDay = Math.max(0.01, ...(ledger?.days || []).map((d) => d.credits));

  return (
    <div className="usage">
      <section className={`usage-hero ${plan ? "" : "muted-hero"}`}>
        <div className="usage-hero-top">
          <span className="usage-icon"><IconCoins size={18} /></span>
          <span className="usage-kicker">Kiro account · {t("thisMonth")}</span>
          {online && <button className="icon-btn" onClick={refresh} disabled={busy} title="Refresh" aria-label="Refresh">
            {busy ? <IconSpinner size={16} /> : <IconRetry size={16} />}</button>}
        </div>
        {plan ? (
          <>
            <div className="usage-big">{fmtCredits(plan.credits_used)}<span> / {fmtCredits(plan.credits_plan)} {t("creditsUnit")}</span></div>
            <div className={`meter ${pctOf(plan.credits_used, plan.credits_plan) > 85 ? "hot" : ""}`}>
              <span style={{ width: `${pctOf(plan.credits_used, plan.credits_plan)}%` }} /></div>
            <div className="usage-row muted small">
              <span>{Math.round(plan.percentage ?? pctOf(plan.credits_used, plan.credits_plan))}% used</span>
              {plan.plan && <span>{plan.plan}</span>}
              {plan.resets && <span>Resets {resetsLabel(plan.resets)}</span>}
            </div>
            {(plan.credits_overage || 0) > 0 && <p className="small warn-text">Over plan by {fmtCredits(plan.credits_overage)} credits{plan.overage_rate ? ` (billed at $${plan.overage_rate} each)` : ""}.</p>}
            {(plan.bonus_credits || []).map((b) => (
              <div key={b.name} className="usage-bonus small"><span>{b.name}</span><span>{fmtCredits(b.used)} / {fmtCredits(b.total)}{b.days_left != null ? ` · ${b.days_left} days left` : ""}</span></div>
            ))}
          </>
        ) : (
          <div className="usage-unavailable">
            <div className="usage-big muted">—</div>
            <p className="small">
              {!online ? "Start Panda to read your Kiro plan total — it's read from Kiro while Panda is online."
                : busy ? "Reading your Kiro plan total from Kiro…"
                : a?.reason ? <>Couldn't read your Kiro plan total: <span className="muted">{a.reason}</span></>
                : usage ? "Your Kiro plan total isn't in yet. Press Refresh — Panda reads it from Kiro, which can take a minute."
                : "Your Kiro plan total will appear here once Panda has read it from Kiro."}
            </p>
            {online && <button className="btn small-btn" onClick={refresh} disabled={busy}>{busy ? "Refreshing…" : "Refresh now"}</button>}
            <p className="muted tiny">This is the plan usage Kiro counts (the IDE too), not only Panda. The numbers below are what Panda's own replies used.</p>
          </div>
        )}
        {usage?.at && <div className="muted tiny">Updated {new Date(usage.at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}{a?.stale ? " · may be out of date" : ""}</div>}
        {err && <div className="error-note small">{err}</div>}
      </section>

      <section>
        <h3>Panda's replies</h3>
        <div className="stat-tiles">
          <div className="stat"><span className="stat-n">{fmtCredits(ledger?.today ?? 0)}</span><span className="muted small">Today</span></div>
          <div className="stat"><span className="stat-n">{fmtCredits(ledger?.week ?? 0)}</span><span className="muted small">7 days</span></div>
          <div className="stat"><span className="stat-n">{fmtCredits(ledger?.month ?? 0)}</span><span className="muted small">This month</span></div>
        </div>
        {ledger && (
          <div className="bars" role="img" aria-label="Credits per day, last 14 days">
            {ledger.days.map((d) => (
              <div key={d.date} className="bar" title={`${d.date}: ${fmtCredits(d.credits)} credits`}>
                <span style={{ height: `${Math.max(d.credits > 0 ? 6 : 2, (d.credits / maxDay) * 100)}%` }} className={d.credits > 0 ? "" : "zero"} />
              </div>
            ))}
          </div>
        )}
        {ledger && <div className="bars-axis muted tiny"><span>{ledger.days[0]?.date.slice(5)}</span><span>Today</span></div>}
        {!ledger && <p className="muted small">Adding up…</p>}
        {ledger && ledger.month > 0 && (
          <p className="muted small">{ledger.replies} replies this month, about {fmtCredits(ledger.month / Math.max(1, ledger.replies))} credits each.</p>
        )}
      </section>

      {ledger && ledger.byModel.length > 0 && (
        <section>
          <h3>By model · {t("thisMonth")}</h3>
          <div className="by-model">
            {ledger.byModel.slice(0, 6).map((m) => (
              <div key={m.model} className="by-model-row">
                <span className="by-model-name">{modelName(m.model)}</span>
                <span className="by-model-bar"><span style={{ width: `${(m.credits / Math.max(0.01, ledger.byModel[0].credits)) * 100}%` }} /></span>
                <span className="by-model-n">{fmtCredits(m.credits)}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {usage?.split ? (
        <section>
          <h3>Everything Kiro Crew ran · {t("thisMonth")}</h3>
          <div className="by-model">
            {([
              ["Chat replies", usage.split.replies],
              ["Background", usage.split.background],
            ] as const).map(([name, n]) => (
              <div key={name} className="by-model-row">
                <span className="by-model-name">{name}</span>
                <span className="by-model-bar"><span style={{ width: `${(n / Math.max(0.01, usage.split!.replies, usage.split!.background)) * 100}%` }} /></span>
                <span className="by-model-n">{fmtCredits(n)}</span>
              </div>
            ))}
          </div>
          <p className="muted small">Background: chat titles {fmtCredits(usage.split.titles)}, memory {fmtCredits(usage.split.memory)}, other {fmtCredits(usage.split.other)}.</p>
          {usage.split.since && <p className="muted tiny">Counted since {new Date(usage.split.since).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}. Kiro Crew starts counting again at each Start; "Panda's replies" above keeps the whole month.</p>}
        </section>
      ) : gwMonth != null && (
        <section>
          <h3>Everything Kiro Crew ran · {t("thisMonth")}</h3>
          <p><b>{fmtCredits(gwMonth)}</b> {t("creditsUnit")} <span className="muted small">— chat replies and background work together, since the last Start.</span></p>
        </section>
      )}
      <p className="muted tiny">Each reply shows the credits it used under the message. Numbers come from Kiro (kiro-cli's metering) through Kiro Crew.</p>
    </div>
  );
}

/** The small credits meter at the bottom of the sidebar. */
export function UsageMeter({ userId, worker, onOpen }: { userId: string; worker: Worker; onOpen: () => void }) {
  const t = useT();
  const online = worker.alive && worker.session?.status === "running";
  const usage = useAccountUsage(userId, worker.session);
  const a = usage?.account;
  const plan = hasPlan(a) ? a : null;
  // Only the Kiro plan gauge goes in the sidebar. When it isn't available, show a quiet state
  // (not a "0" that looks like the plan is empty) — the full picture is one tap away.
  const right = plan ? <b>{fmtCredits(plan.credits_used)}<span className="muted"> / {fmtCredits(plan.credits_plan)}</span></b>
    : <b className="muted usage-meter-hint">{!online ? "offline" : usage ? "see usage" : "…"}</b>;
  return (
    <button className="usage-meter" onClick={onOpen} title="Credit usage">
      <span className="usage-meter-top">
        <IconCoins size={14} />
        <span>{t("credits")}</span>
        <span className="grow" />
        {right}
      </span>
      {plan && <span className={`meter thin ${pctOf(plan.credits_used, plan.credits_plan) > 85 ? "hot" : ""}`}><span style={{ width: `${pctOf(plan.credits_used, plan.credits_plan)}%` }} /></span>}
    </button>
  );
}
