import { useEffect, useMemo, useState } from "react";
import { crewSet, crewSettings, type CrewSetting, type CrewSettings } from "../lib/commands";
import { useSettings } from "../lib/settings";
import { sessionAction } from "../lib/supabase";
import { ALL_MODELS } from "../lib/models";
import { IconChevron, IconLock, IconPower, IconRetry, IconSearch, IconSpinner } from "./Icons";

/**
 * Every Kiro Crew setting, read live from the worker's gateway (schema + current values).
 * The ones Kiro Crew lets a dashboard change, and Panda doesn't lock, are editable; changes are
 * also kept in Panda's settings so the worker re-applies them on its next start.
 */
const CACHE_KEY = "panda.crew.settings";

const ESSENTIALS: { title: string; paths: string[] }[] = [
  { title: "How Panda answers", paths: ["dashboard.verbosity", "dashboard.default_memory_mode", "dashboard.merge_queued_messages"] },
  { title: "About you", paths: ["dashboard.user_role", "dashboard.user_role_other", "dashboard.user_technical_level"] },
  { title: "Models for background work", paths: ["agent.role_models.background", "agent.role_efforts.background",
    "agent.role_models.subagent", "agent.role_efforts.subagent", "agent.fallback_model", "agent.refusal_fallback_model",
    "agent.model", "agent.reasoning_effort"] },
  { title: "Context & long answers", paths: ["session.autocompact_pct", "session.compact_wait_secs", "agent.tool_search",
    "agent.chat_turn_timeout_secs", "agent.soft_stop_budget_secs", "agent.completion_keep", "agent.completion_keep_chars"] },
  { title: "Learning", paths: ["session_summary.enabled", "dashboard.title_refresh_every_turns", "skills.auto_create_from_sessions",
    "knowledge.auto_add_documents", "knowledge.auto_ingest_artifacts"] },
];

const SECTION_NAMES: Record<string, string> = {
  agent: "Agent", agents: "Agents", session: "Sessions", session_summary: "Session summaries", memory: "Memory",
  memory_stores: "Memory stores", dashboard: "Dashboard", knowledge: "Knowledge library", skills: "Skills",
  mcp: "Tools (MCP)", mcp_gateway: "Tool gateway", stt: "Voice", telemetry: "Telemetry", decisions: "Decisions",
  computer_use: "Computer use", taskrunner: "Task runner", heartbeat: "Heartbeat", monitoring: "Monitoring",
  watchdog: "Watchdog", resource_limits: "Resource limits", cron_history: "Scheduled tasks", messaging: "Messaging",
  instances: "Remote Crew", tunnel: "Tunnel", hooks: "Hooks", workspaces: "Workspaces", apps: "Apps",
  registries: "Registries", session_environments: "Session environments", aws: "AWS", publish: "Publishing",
};
const CHANNELS = new Set(["slack", "slack_channels", "slack_dm_activation", "telegram", "discord", "webex", "weixin",
  "whatsapp", "feishu", "teams", "wecom", "imessage", "wakatime"]);
const MODEL_PATH = /(^agent\.(model|fallback_model|refusal_fallback_model)$|^agent\.role_models\.|^apps\.[^.]+\.model$|^knowledge\.extraction_model$)/;

const sectionOf = (p: string) => {
  const top = p.split(".")[0];
  return CHANNELS.has(top) ? "channels" : SECTION_NAMES[top] ? top : "other";
};
const sectionName = (s: string) => s === "channels" ? "Messaging channels" : s === "other" ? "Other" : SECTION_NAMES[s] || s;
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

function show(v: unknown, sensitive?: boolean): string {
  if (sensitive) return v ? "••••••••" : "not set";
  if (v === undefined || v === null || v === "") return "—";
  if (typeof v === "boolean") return v ? "On" : "Off";
  if (Array.isArray(v)) return v.length ? v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ").slice(0, 120) : "none";
  if (typeof v === "object") { const n = Object.keys(v as object).length; return n ? JSON.stringify(v).slice(0, 120) : "none"; }
  return String(v);
}

export function CrewSettingsPanel({ online }: { online: boolean }) {
  const { settings, update } = useSettings();
  const [data, setData] = useState<CrewSettings | null>(() => {
    try { return JSON.parse(localStorage.getItem(CACHE_KEY) || "null"); } catch { return null; }
  });
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [rowErr, setRowErr] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string>("");
  const [starting, setStarting] = useState(false);

  const load = () => {
    setLoading(true);
    setErr("");
    crewSettings().then((d) => setData(d)).catch((e) => setErr((e as Error).message)).finally(() => setLoading(false));
  };
  useEffect(() => { if (online) load(); }, [online]); // eslint-disable-line react-hooks/exhaustive-deps
  // Keep the cached copy in step with every change, saves included: a reload used to show the value
  // from before the last save (e.g. verbosity back to answer_only) until the fresh list arrived.
  useEffect(() => {
    if (!data) return;
    try { localStorage.setItem(CACHE_KEY, JSON.stringify(data)); } catch { /* too big for this browser: fine */ }
  }, [data]);

  const byPath = useMemo(() => new Map((data?.items || []).map((i) => [i.p, i])), [data]);
  const groups = useMemo(() => {
    const m = new Map<string, CrewSetting[]>();
    for (const it of data?.items || []) {
      const s = sectionOf(it.p);
      if (!m.has(s)) m.set(s, []);
      m.get(s)!.push(it);
    }
    const order = ["agent", "session", "session_summary", "memory", "knowledge", "skills", "dashboard"];
    return [...m].sort(([a], [b]) => {
      const ia = order.indexOf(a), ib = order.indexOf(b);
      if (a === "channels" || b === "channels") return a === "channels" ? 1 : -1;
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
    });
  }, [data]);
  const query = q.trim().toLowerCase();
  const found = query ? (data?.items || []).filter((i) => (i.l + " " + i.p + " " + (i.h || "")).toLowerCase().includes(query)).slice(0, 80) : [];

  const save = async (it: CrewSetting, value: unknown) => {
    if (!online) { setRowErr((r) => ({ ...r, [it.p]: "Start Panda to change Kiro Crew settings." })); return; }
    const before = it.v;
    setRowErr((r) => ({ ...r, [it.p]: "" }));
    setSaving(it.p);
    const patchValue = (v: unknown) => setData((d) => d && { ...d, items: d.items.map((x) => x.p === it.p ? { ...x, v } : x) });
    patchValue(value);
    try {
      const r = await crewSet(it.p, value);
      patchValue(r.value);
      const next = { ...(settings.crewOverrides || {}) };
      if (same(r.value, it.d)) delete next[it.p]; else next[it.p] = r.value;
      update({ crewOverrides: next });
      if (r.clamp?.stored != null) setRowErr((x) => ({ ...x, [it.p]: `Saved as ${r.clamp!.stored} (allowed range).` }));
    } catch (e) {
      patchValue(before);
      setRowErr((x) => ({ ...x, [it.p]: (e as Error).message }));
    } finally {
      setSaving("");
    }
  };

  const editable = (data?.items || []).filter((i) => i.w).length;
  const row = (it: CrewSetting | undefined) => it && (
    <SettingRow key={it.p} it={it} onSave={(v) => save(it, v)} saving={saving === it.p} error={rowErr[it.p]} online={online} />
  );

  return (
    <div className="crew-settings">
      <div className="crew-head">
        <div>
          <div className="crew-title">Kiro Crew</div>
          <div className="muted small">
            {data ? <>{data.version ? `Version ${data.version} · ` : ""}{data.backend ? `engine ${data.backend === "kas" ? "KAS" : data.backend} · ` : ""}
              {data.items.length} settings, {editable} you can change here</> : "Every setting of the agent that runs Panda."}
          </div>
        </div>
        {online && <button className="icon-btn" onClick={load} disabled={loading} title="Reload" aria-label="Reload">
          {loading ? <IconSpinner size={16} /> : <IconRetry size={16} />}</button>}
      </div>

      {!online && (
        <div className="offline-card">
          <IconPower size={16} />
          <span>{data ? "Panda is offline — showing the last settings it reported. Start it to make changes." : "Start Panda to see Kiro Crew's settings."}</span>
          <button className="btn small-btn primary" disabled={starting} onClick={async () => {
            setStarting(true);
            try { await sessionAction("start"); } catch (e) { alert((e as Error).message); }
            setStarting(false);
          }}>Start</button>
        </div>
      )}
      {err && <div className="error-note small">{err}</div>}
      {loading && !data && <div className="muted pad"><IconSpinner size={16} /> Reading Kiro Crew's settings…</div>}

      {data && (
        <>
          <label className="search-box wide"><IconSearch size={16} />
            <input value={q} placeholder={`Search ${data.items.length} settings`} onChange={(e) => setQ(e.target.value)} /></label>
          {query ? (
            <div className="set-list">
              {found.length === 0 && <p className="muted small">No setting matches “{q}”.</p>}
              {found.map(row)}
            </div>
          ) : (
            <>
              {ESSENTIALS.map((g) => {
                const items = g.paths.map((p) => byPath.get(p)).filter(Boolean);
                return items.length ? (
                  <div key={g.title} className="set-group">
                    <h3>{g.title}</h3>
                    <div className="set-list">{items.map(row)}</div>
                  </div>
                ) : null;
              })}
              <h3>All settings</h3>
              <div className="sections">
                {groups.map(([s, items]) => {
                  const isOpen = open.has(s);
                  const n = items.filter((i) => i.w).length;
                  return (
                    <div key={s} className={`section ${isOpen ? "open" : ""}`}>
                      <button className="section-head" onClick={() => setOpen((o) => { const x = new Set(o); if (x.has(s)) x.delete(s); else x.add(s); return x; })}>
                        <span>{sectionName(s)}</span>
                        <span className="muted small">{items.length}{n ? ` · ${n} editable` : ""}</span>
                        <span className="chev"><IconChevron size={14} /></span>
                      </button>
                      {isOpen && <div className="set-list">{items.map(row)}</div>}
                    </div>
                  );
                })}
              </div>
              <p className="muted tiny">Settings marked “restart” apply after Panda restarts (Stop, then Start). Locked ones change
                a security boundary or the worker's own setup, or only matter in Kiro Crew's desktop dashboard.</p>
            </>
          )}
        </>
      )}
    </div>
  );
}

function SettingRow({ it, onSave, saving, error, online }: { it: CrewSetting; onSave: (v: unknown) => void; saving: boolean; error?: string; online: boolean }) {
  const [more, setMore] = useState(false);
  const [draft, setDraft] = useState<string>(() => (it.v == null ? "" : String(it.v)));
  useEffect(() => { setDraft(it.v == null ? "" : String(it.v)); }, [it.v]);
  const canEdit = !!it.w && online;
  const values = (it.x?.values ?? it.e ?? null) as (string | number | boolean | null)[] | null;
  const changed = it.d !== undefined && !same(it.v, it.d);
  const type = it.x?.type || it.t;

  let control: React.ReactNode;
  if (!it.w) {
    control = <span className="set-value" title={it.k || "Kiro Crew lets you change this only in its config file"}>
      <IconLock size={12} /> {show(it.v, !!it.s)}</span>;
  } else if (type === "bool" || it.t === "boolean") {
    const on = it.v === true;
    control = <button className={`toggle ${on ? "on" : ""}`} role="switch" aria-checked={on} aria-label={it.l} disabled={!canEdit || saving}
      onClick={() => onSave(!on)}><span /></button>;
  } else if (MODEL_PATH.test(it.p)) {
    const cur = String(it.v ?? "");
    const opts = ["", "auto", ...ALL_MODELS.map((m) => m.id).filter((id) => id !== "auto")];
    if (cur && !opts.includes(cur)) opts.push(cur);
    control = <select value={cur} disabled={!canEdit || saving} onChange={(e) => onSave(e.target.value)}>
      {opts.map((o) => <option key={o} value={o}>{o === "" ? "Same as chat (default)" : o === "auto" ? "Auto" : (ALL_MODELS.find((m) => m.id === o)?.name || o)}</option>)}
    </select>;
  } else if (values && values.length) {
    control = <select value={String(it.v ?? "")} disabled={!canEdit || saving}
      onChange={(e) => { const raw = e.target.value; const match = values.find((v) => String(v ?? "") === raw); onSave(match ?? raw); }}>
      {values.map((v) => <option key={String(v)} value={String(v ?? "")}>{v === "" || v == null ? "Default" : String(v)}</option>)}
    </select>;
  } else if (type === "int" || type === "float" || it.t === "integer" || it.t === "number") {
    const commit = () => {
      if (draft.trim() === String(it.v ?? "")) return;
      const n = Number(draft);
      if (!Number.isFinite(n)) { setDraft(String(it.v ?? "")); return; }
      onSave(type === "int" || it.t === "integer" ? Math.round(n) : n);
    };
    control = <input className="text-input num" type="number" inputMode="decimal" value={draft} disabled={!canEdit || saving}
      min={it.x?.min} max={it.x?.max} onChange={(e) => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />;
  } else if (it.t === "string" || type === "str") {
    const commit = () => { if (draft !== String(it.v ?? "")) onSave(draft); };
    control = <input className="text-input" value={draft} maxLength={it.x?.max_len} disabled={!canEdit || saving}
      onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />;
  } else {
    control = <span className="set-value">{show(it.v)}</span>;
  }

  return (
    <div className={`crew-row ${it.w ? "" : "ro"}`}>
      <div className="crew-row-main">
        <div className="crew-row-label">
          <span>{it.l}</span>
          {it.r && <span className="tag">restart</span>}
          {changed && it.w && <span className="tag accent-tag">changed</span>}
          {saving && <IconSpinner size={13} />}
        </div>
        <div className="crew-row-path mono">{it.p}</div>
        {it.h && <button className={`crew-row-help ${more ? "open" : ""}`} onClick={() => setMore((v) => !v)}>{it.h}</button>}
        {it.k && <div className="crew-row-lock small"><IconLock size={11} /> Locked in Panda: {it.k}.</div>}
        {error && <div className={`small ${error.startsWith("Saved") ? "muted" : "error-text"}`}>{error}</div>}
        {changed && it.w && canEdit && <button className="link small" onClick={() => onSave(it.d)}>Reset to default ({show(it.d)})</button>}
      </div>
      <div className="crew-row-control">{control}</div>
    </div>
  );
}
