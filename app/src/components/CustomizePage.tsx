import { useCallback, useEffect, useState } from "react";
import { change, getSaved, mcpStatus, setEnv, type McpConfig, type Schedule, type WorkerSaved } from "../lib/api";
import { MODELS } from "../lib/models";
import { StartNote } from "./StartNote";
import { VarsEditor } from "./VarsEditor";
import { IconAlert, IconClock, IconEdit, IconGlobe, IconPlus, IconSparkle, IconTrash } from "./Icons";

type Tab = "skills" | "connectors" | "scheduled" | "keys" | "memory";
const TABS: [Tab, string][] = [["skills", "Skills"], ["connectors", "Connectors"], ["scheduled", "Scheduled"],
  ["keys", "API keys"], ["memory", "Memory"]];

/** Everything here is saved by the worker into your private Kaggle dataset, so it survives restarts. */
export function CustomizePage({ online, onAsk }: { online: boolean; onAsk: (text: string) => void }) {
  const [tab, setTab] = useState<Tab>("skills");
  const [saved, setSaved] = useState<WorkerSaved | null>(null);
  const [err, setErr] = useState("");
  const reload = useCallback(() => getSaved().then((s) => { setSaved(s); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { if (online) reload(); }, [online, reload]);
  // While a change is being written to Kaggle, check back so the badge settles.
  useEffect(() => {
    if (saved?.save !== "saving") return;
    const t = window.setTimeout(reload, 4000);
    return () => window.clearTimeout(t);
  }, [saved, reload]);

  return (
    <div className="page">
      <div className="page-head">
        <h1 className="serif">Customize</h1>
        {saved && <span className={`tag ${saved.save === "error" ? "warn" : ""}`}>
          {saved.save === "saving" ? "Saving to Kaggle…" : saved.save === "error" ? "Not saved" : "Saved"}</span>}
      </div>
      <div className="tabs">{TABS.map(([id, label]) => (
        <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>
      ))}</div>
      {!online ? <StartNote what="manage skills, connectors, scheduled tasks and memory" /> :
        err ? <p className="error-note"><IconAlert size={15} /> {err} <button className="link" onClick={reload}>Retry</button></p> :
        !saved ? <p className="muted">Loading…</p> :
        tab === "skills" ? <Skills saved={saved} reload={reload} onAsk={onAsk} /> :
        tab === "connectors" ? <Connectors saved={saved} reload={reload} /> :
        tab === "scheduled" ? <Scheduled saved={saved} reload={reload} /> :
        tab === "keys" ? <Keys /> : <Memory saved={saved} reload={reload} />}
    </div>
  );
}

type P = { saved: WorkerSaved; reload: () => void };

/* ── Skills ───────────────────────────────────────────────────────────────── */
const SKILL_TEMPLATE = (name: string) => `---
name: ${name}
description: What this skill does and when Claude should use it.
---

# ${name}

Steps Claude should follow…
`;

function Skills({ saved, reload, onAsk }: P & { onAsk: (t: string) => void }) {
  const [edit, setEdit] = useState<{ name: string; content: string; isNew: boolean } | null>(null);
  const [err, setErr] = useState("");
  const names = Object.keys(saved.skills).sort();
  const save = async (name: string, content: string | null) => {
    setErr("");
    try { await change("/skills", { name, content }); setEdit(null); reload(); } catch (e) { setErr((e as Error).message); }
  };
  if (edit) return (
    <div className="editor">
      {edit.isNew ? (
        <input className="text-input wide" placeholder="skill-name" value={edit.name} autoFocus
          onChange={(e) => setEdit({ ...edit, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} />
      ) : <h3 className="mono">{edit.name}</h3>}
      <textarea className="code-area" rows={16} value={edit.content} onChange={(e) => setEdit({ ...edit, content: e.target.value })} />
      <div className="row-gap">
        <button className="btn primary" disabled={!edit.name} onClick={() => save(edit.name, edit.content)}>Save</button>
        <button className="btn" onClick={() => setEdit(null)}>Cancel</button>
      </div>
      {err && <p className="login-msg">{err}</p>}
    </div>
  );
  return (
    <>
      <p className="muted">Skills are instructions Claude Code loads when a task matches their description (<code>~/.claude/skills</code>).</p>
      <div className="row-gap">
        <button className="btn primary" onClick={() => setEdit({ name: "", content: SKILL_TEMPLATE("my-skill"), isNew: true })}><IconPlus size={16} /> New skill</button>
        <button className="btn" onClick={() => {
          const what = prompt("What should the skill do?");
          if (what?.trim()) onAsk(`Create a Claude Code skill for this: ${what.trim()}\n\nShow me the complete SKILL.md (with name and description front matter) in one code block so I can save it in Customize → Skills.`);
        }}><IconSparkle size={16} /> Ask Claude to write one</button>
      </div>
      <div className="card-list" style={{ marginTop: 14 }}>
        {names.length === 0 && <p className="muted">No skills yet.</p>}
        {names.map((n) => {
          const desc = /description:\s*(.+)/.exec(saved.skills[n])?.[1] || "";
          return (
            <div key={n} className="row-card">
              <div className="grow"><div className="mono">{n}</div><div className="muted small">{desc}</div></div>
              <button className="icon-btn" aria-label="Edit" onClick={() => setEdit({ name: n, content: saved.skills[n], isNew: false })}><IconEdit size={16} /></button>
              <button className="icon-btn" aria-label="Delete" onClick={() => { if (confirm(`Delete skill ${n}?`)) save(n, null); }}><IconTrash size={16} /></button>
            </div>
          );
        })}
      </div>
      {err && <p className="login-msg">{err}</p>}
    </>
  );
}

/* ── Connectors (MCP servers) ─────────────────────────────────────────────── */
function Connectors({ saved, reload }: P) {
  const [form, setForm] = useState<{ name: string; type: McpConfig["type"]; url: string; header: string; token: string; command: string } | null>(null);
  const [err, setErr] = useState("");
  const [health, setHealth] = useState("");
  const names = Object.keys(saved.mcp).sort();

  const toggleBrowser = async () => {
    try { await change("/settings", { patch: { browser: !saved.settings.browser } }); reload(); } catch (e) { setErr((e as Error).message); }
  };
  const remove = async (name: string) => {
    try { await change("/mcp", { name, config: null }); reload(); } catch (e) { setErr((e as Error).message); }
  };
  const add = async () => {
    if (!form) return;
    let config: McpConfig;
    if (form.type === "stdio") {
      const parts = form.command.trim().split(/\s+/);
      config = { type: "stdio", command: parts[0], args: parts.slice(1) };
    } else {
      config = { type: form.type, url: form.url.trim() };
      if (form.token) config.headers = { [form.header || "Authorization"]: form.token };
    }
    try { await change("/mcp", { name: form.name, config }); setForm(null); reload(); } catch (e) { setErr((e as Error).message); }
  };

  return (
    <>
      <p className="muted">Connectors are MCP servers: they give Claude new tools (GitHub, Notion, databases, your own APIs…).</p>
      <div className="card-list">
        <div className="row-card">
          <IconGlobe size={18} />
          <div className="grow"><div>Browser</div><div className="muted small">A real Chromium Claude can drive (Playwright). You can watch and take over in the Browser panel.
            {saved.settings.browser && <> · {saved.browser === "ready" ? "ready" : saved.browser === "installing" ? "installing…" : saved.browser}</>}</div></div>
          <button className={`toggle ${saved.settings.browser ? "on" : ""}`} role="switch" aria-checked={saved.settings.browser} aria-label="Browser" onClick={toggleBrowser}><span /></button>
        </div>
        {names.map((n) => {
          const c = saved.mcp[n];
          return (
            <div key={n} className="row-card">
              <div className="grow"><div className="mono">{n}</div><div className="muted small">{c.type === "stdio" ? [c.command, ...(c.args || [])].join(" ") : c.url}</div></div>
              <button className="icon-btn" aria-label="Delete" onClick={() => { if (confirm(`Remove connector ${n}?`)) remove(n); }}><IconTrash size={16} /></button>
            </div>
          );
        })}
      </div>
      {form ? (
        <div className="editor" style={{ marginTop: 14 }}>
          <div className="env-form">
            <input className="text-input" placeholder="name (e.g. github)" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} />
            <select className="text-input" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value as McpConfig["type"] })}>
              <option value="http">Remote (HTTP)</option><option value="sse">Remote (SSE)</option><option value="stdio">Command</option>
            </select>
          </div>
          {form.type === "stdio" ? (
            <input className="text-input wide" placeholder="npx -y @modelcontextprotocol/server-memory" value={form.command}
              onChange={(e) => setForm({ ...form, command: e.target.value })} />
          ) : (
            <>
              <input className="text-input wide" placeholder="https://example.com/mcp" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
              <div className="env-form">
                <input className="text-input" placeholder="Header (Authorization)" value={form.header} onChange={(e) => setForm({ ...form, header: e.target.value })} />
                <input className="text-input" type="password" placeholder="Bearer token… (optional)" value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} />
              </div>
            </>
          )}
          <div className="row-gap" style={{ marginTop: 10 }}>
            <button className="btn primary" disabled={!form.name || (form.type === "stdio" ? !form.command.trim() : !form.url.trim())} onClick={add}>Add connector</button>
            <button className="btn" onClick={() => setForm(null)}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="row-gap" style={{ marginTop: 14 }}>
          <button className="btn primary" onClick={() => setForm({ name: "", type: "http", url: "", header: "Authorization", token: "", command: "" })}><IconPlus size={16} /> Add connector</button>
          <button className="btn" onClick={() => { setHealth("Checking…"); mcpStatus().then(setHealth).catch((e) => setHealth(e.message)); }}>Check connections</button>
        </div>
      )}
      {health && <pre className="health">{health}</pre>}
      {err && <p className="login-msg">{err}</p>}
    </>
  );
}

/* ── Scheduled tasks ──────────────────────────────────────────────────────── */
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const blank = (): Schedule => ({ name: "", prompt: "", kind: "daily", time: "09:00", minutes: 60, days: [0, 1, 2, 3, 4, 5, 6],
  model: "default", effort: "medium", enabled: true });
const describe = (s: Schedule) => s.kind === "interval" ? `Every ${s.minutes} min` :
  `${(s.days?.length ?? 7) === 7 ? "Every day" : (s.days || []).map((d) => DAYS[d]).join(", ")} at ${s.time}`;

function Scheduled({ saved, reload }: P) {
  const [edit, setEdit] = useState<Schedule | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const call = async (body: unknown, path = "/schedules") => {
    setErr("");
    try { await change(path, body); setEdit(null); reload(); return true; } catch (e) { setErr((e as Error).message); return false; }
  };
  if (edit) return (
    <div className="editor">
      <input className="text-input wide" placeholder="Name (e.g. Morning news)" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
      <textarea className="instructions" rows={5} placeholder="What should Claude do?" value={edit.prompt} onChange={(e) => setEdit({ ...edit, prompt: e.target.value })} />
      <div className="env-form">
        <select className="text-input" value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value as Schedule["kind"] })}>
          <option value="daily">At a time of day</option><option value="interval">Every N minutes</option>
        </select>
        {edit.kind === "daily" ? <input className="text-input" type="time" value={edit.time} onChange={(e) => setEdit({ ...edit, time: e.target.value })} /> :
          <input className="text-input" type="number" min={15} value={edit.minutes} onChange={(e) => setEdit({ ...edit, minutes: Number(e.target.value) })} />}
        <select className="text-input" value={edit.model} onChange={(e) => setEdit({ ...edit, model: e.target.value })}>
          {MODELS.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
      </div>
      {edit.kind === "daily" && (
        <div className="tabs" style={{ marginTop: 10 }}>{DAYS.map((d, i) => {
          const on = (edit.days || []).includes(i);
          return <button key={d} className={on ? "active" : ""} onClick={() => setEdit({ ...edit, days: on ? (edit.days || []).filter((x) => x !== i) : [...(edit.days || []), i].sort() })}>{d}</button>;
        })}</div>
      )}
      <div className="row-gap">
        <button className="btn primary" disabled={!edit.prompt.trim()} onClick={() => call({ schedule: { ...edit, name: edit.name || edit.prompt.slice(0, 40), tz: new Date().getTimezoneOffset() } })}>Save</button>
        <button className="btn" onClick={() => setEdit(null)}>Cancel</button>
      </div>
      {err && <p className="login-msg">{err}</p>}
    </div>
  );
  return (
    <>
      <p className="muted">Claude runs these on the worker and the results land in Notifications. They run while the worker is online. It renews itself every ~11 hours without a break, so they keep running until you press Stop.</p>
      <button className="btn primary" onClick={() => setEdit(blank())}><IconPlus size={16} /> New task</button>
      <div className="card-list" style={{ marginTop: 14 }}>
        {saved.schedules.length === 0 && <p className="muted">No scheduled tasks yet.</p>}
        {saved.schedules.map((s) => (
          <div key={s.id} className="row-card wrap">
            <IconClock size={18} />
            <div className="grow"><div>{s.name}</div><div className="muted small">{describe(s)}{s.last_run ? ` · last run ${new Date(s.last_run * 1000).toLocaleString()}` : ""}</div></div>
            <button className={`toggle ${s.enabled !== false ? "on" : ""}`} role="switch" aria-checked={s.enabled !== false} aria-label="Enabled"
              onClick={() => call({ schedule: { ...s, enabled: s.enabled === false } })}><span /></button>
            <button className="btn" onClick={async () => { if (await call({ id: s.id }, "/schedules/run")) setNote(`"${s.name}" is running. The result will appear in Notifications.`); }}>Run now</button>
            <button className="icon-btn" aria-label="Edit" onClick={() => setEdit({ ...s })}><IconEdit size={16} /></button>
            <button className="icon-btn" aria-label="Delete" onClick={() => { if (confirm(`Delete "${s.name}"?`)) call({ delete: s.id }); }}><IconTrash size={16} /></button>
          </div>
        ))}
      </div>
      {note && <p className="muted">{note}</p>}
      {err && <p className="login-msg">{err}</p>}
    </>
  );
}

/* ── API keys (all chats) ─────────────────────────────────────────────────── */
function Keys() {
  const load = useCallback(() => getSaved().then((s) => s.env), []);
  return (
    <>
      <p className="muted">Environment variables every chat can use, e.g. <code>OPENAI_API_KEY</code> for a script Claude writes. Claude is told their names, never their values. Developer-only variables live in Developer → Env variables.</p>
      <VarsEditor load={load} save={setEnv} empty="No API keys yet." />
    </>
  );
}

/* ── Memory ───────────────────────────────────────────────────────────────── */
function Memory({ saved, reload }: P) {
  const [text, setText] = useState(saved.memory);
  const [err, setErr] = useState("");
  const dirty = text !== saved.memory;
  const toggle = async () => {
    try { await change("/settings", { patch: { memory: !saved.settings.memory } }); reload(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <>
      <div className="set-row">
        <div><div>Let Claude remember things</div><div className="muted small">Claude adds lasting facts and preferences you share. The memory is loaded in every chat.</div></div>
        <button className={`toggle ${saved.settings.memory ? "on" : ""}`} role="switch" aria-checked={saved.settings.memory} aria-label="Memory" onClick={toggle}><span /></button>
      </div>
      <p className="muted">This is Claude Code's <code>~/.claude/CLAUDE.md</code>. Edit it freely.</p>
      <textarea className="code-area" rows={14} value={text} placeholder="e.g. My name is Vlad. I live in Italy. I prefer short answers." onChange={(e) => setText(e.target.value)} />
      <div className="row-gap">
        <button className="btn primary" disabled={!dirty} onClick={async () => {
          try { await change("/memory", { text }); reload(); } catch (e) { setErr((e as Error).message); }
        }}>Save memory</button>
        {dirty && <button className="btn" onClick={() => setText(saved.memory)}>Undo</button>}
      </div>
      {err && <p className="login-msg">{err}</p>}
    </>
  );
}
