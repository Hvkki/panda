import { useCallback, useEffect, useState } from "react";
import { sessionAction, supabase } from "../lib/supabase";
import { gw, runCommand } from "../lib/commands";
import { TerminalTab } from "./Terminal";
import { IconAlert, IconClock, IconMoon, IconPlus, IconPower, IconTrash } from "./Icons";

type Tab = "skills" | "connectors" | "scheduled" | "terminal" | "keys";
const TABS: [Tab, string][] = [["skills", "Skills"], ["connectors", "Connectors"], ["scheduled", "Scheduled"],
  ["terminal", "Terminal"], ["keys", "API keys"]];

type AnyRec = Record<string, unknown>;
const str = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v));
const list = (d: unknown, ...keys: string[]): AnyRec[] => {
  if (Array.isArray(d)) return d as AnyRec[];
  for (const k of keys) {
    const v = (d as AnyRec | null)?.[k];
    if (Array.isArray(v)) return v as AnyRec[];
  }
  return [];
};

export function CustomizePage({ online, onAsk }: { online: boolean; onAsk: (text: string) => void }) {
  const [tab, setTab] = useState<Tab>(() => (new URLSearchParams(location.search).get("tab") as Tab) || "skills");
  return (
    <div className="page">
      <div className="page-head"><h1 className="serif">Customize</h1></div>
      <div className="tabs">{TABS.map(([id, label]) => (
        <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>
      ))}</div>
      {tab === "keys" ? <KeysTab online={online} /> : !online ? <OfflineNote /> :
        tab === "skills" ? <SkillsTab onAsk={onAsk} /> :
        tab === "connectors" ? <ConnectorsTab /> :
        tab === "terminal" ? <TerminalTab /> : <ScheduledTab onAsk={onAsk} />}
    </div>
  );
}

function OfflineNote() {
  const [busy, setBusy] = useState(false);
  return (
    <div className="empty-state">
      <div className="empty-art"><IconMoon size={30} /></div>
      <p>Panda is asleep. Start it to manage skills, connectors and scheduled tasks.</p>
      <button className="btn primary" disabled={busy} onClick={async () => { setBusy(true); try { await sessionAction("start"); } catch (e) { alert((e as Error).message); } setBusy(false); }}>
        <IconPower size={15} /> Start Panda</button>
    </div>
  );
}

function useRemote<T>(fn: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const reload = useCallback(() => {
    setLoading(true);
    setErr("");
    fn().then(setData).catch((e) => setErr((e as Error).message)).finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(reload, [reload]);
  return { data, err, loading, reload };
}

const Loading = () => <div className="muted pad">Asking Panda…</div>;
const Err = ({ msg, retry }: { msg: string; retry: () => void }) => (
  <div className="error-note pad"><IconAlert size={15} /> {msg} <button className="link" onClick={retry}>Retry</button></div>
);

/* ── Skills ──────────────────────────────────────────────────────────────── */
function SkillsTab({ onAsk }: { onAsk: (t: string) => void }) {
  const { data, err, loading, reload } = useRemote(() => gw("GET", "/api/skills"));
  const [q, setQ] = useState("");
  const [found, setFound] = useState<AnyRec[] | null>(null);
  const [msg, setMsg] = useState("");
  const skills = list(data, "skills").filter((s) => !q || JSON.stringify(s).toLowerCase().includes(q.toLowerCase()));

  const discover = async () => {
    setMsg("Searching…");
    try {
      const r = await gw("GET", `/api/skills/-/discover?q=${encodeURIComponent(q || "writing")}`);
      setFound(list(r, "results", "skills", "items"));
      setMsg("");
    } catch (e) { setMsg((e as Error).message); }
  };

  return (
    <>
      <p className="muted">Skills are step-by-step know-how Panda loads only when needed — so they cost no credits until used.</p>
      <div className="toolbar">
        <input className="text-input grow" placeholder="Filter or search the skill directory" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn" onClick={discover}>Discover</button>
        <button className="btn primary" onClick={() => onAsk("Create a new skill for me. Ask me what task it should handle, then write it and save it as a skill.")}>
          <IconPlus size={15} /> Create</button>
      </div>
      {msg && <p className="muted small">{msg}</p>}
      {found && (
        <div className="card-list">
          <h3 className="section-title">From the directory</h3>
          {found.length === 0 && <p className="muted small">Nothing found.</p>}
          {found.slice(0, 20).map((s, i) => (
            <div key={i} className="row-card">
              <div className="grow"><b>{str(s.name || s.title || s.key)}</b><div className="muted small">{str(s.description).slice(0, 180)}</div></div>
              <button className="btn small-btn" onClick={async () => {
                try { await gw("POST", "/api/skills/-/discover/install", { key: s.key || s.name, name: s.name, source: s.source }); setMsg(`Installed ${str(s.name || s.key)}`); reload(); }
                catch (e) { setMsg((e as Error).message); }
              }}>Add</button>
            </div>
          ))}
        </div>
      )}
      {loading ? <Loading /> : err ? <Err msg={err} retry={reload} /> : (
        <div className="card-list">
          <h3 className="section-title">Installed ({skills.length})</h3>
          {skills.map((s, i) => {
            const name = str(s.name || s.key);
            const builtin = /builtin|managed|bundled/i.test(str(s.source || s.origin || s.scope));
            return (
              <div key={i} className="row-card">
                <div className="grow"><b>{name}</b> {builtin && <span className="tag">Built-in</span>}
                  <div className="muted small">{str(s.description).slice(0, 200)}</div></div>
                <button className="btn small-btn" onClick={() => onAsk(`Use the "${name}" skill. `)}>Try</button>
                {!builtin && <button className="icon-btn" title="Remove" onClick={async () => {
                  if (!confirm(`Remove skill ${name}?`)) return;
                  try { await gw("DELETE", `/api/skills/${encodeURIComponent(name)}`); reload(); } catch (e) { alert((e as Error).message); }
                }}><IconTrash size={15} /></button>}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

/* ── Connectors (MCP servers) ───────────────────────────────────────────── */
const PRESETS: { name: string; label: string; blurb: string; command: string; args: string[]; key?: string; envName?: string }[] = [
  { name: "github", label: "GitHub", blurb: "Repos, issues and pull requests", command: "npx", args: ["-y", "@modelcontextprotocol/server-github"], key: "GITHUB_TOKEN", envName: "GITHUB_PERSONAL_ACCESS_TOKEN" },
  { name: "notion", label: "Notion", blurb: "Read and write your Notion pages", command: "npx", args: ["-y", "@notionhq/notion-mcp-server"], key: "NOTION_TOKEN", envName: "NOTION_TOKEN" },
  { name: "brave-search", label: "Brave Search", blurb: "Fast web and image search", command: "npx", args: ["-y", "@modelcontextprotocol/server-brave-search"], key: "BRAVE_API_KEY", envName: "BRAVE_API_KEY" },
  { name: "fetch", label: "Web Fetch", blurb: "Read any web page as clean text", command: "uvx", args: ["mcp-server-fetch"] },
];

function ConnectorsTab() {
  const { data, err, loading, reload } = useRemote(() => gw("GET", "/api/mcp"));
  const [form, setForm] = useState<null | { name: string; command: string; args: string; envName: string; key: string }>(null);
  const [msg, setMsg] = useState("");
  const servers = list(data, "servers");

  const add = async (f: NonNullable<typeof form>) => {
    setMsg("Adding…");
    try {
      const body: AnyRec = { command: f.command, args: f.args.trim() ? f.args.trim().split(/\s+/) : [] };
      if (f.envName && f.key) body.env = { [f.envName]: `\${env:${f.key}}` };
      await gw("PUT", `/api/mcp/servers/${encodeURIComponent(f.name)}`, body);
      setMsg(`Added ${f.name}. It's available from the next new chat.`);
      setForm(null);
      reload();
    } catch (e) { setMsg((e as Error).message); }
  };

  return (
    <>
      <p className="muted">Connectors give Panda new tools (apps, data, search). Keys come from your <b>API keys</b> tab — they never appear in chat.</p>
      <div className="preset-grid">
        {PRESETS.map((p) => (
          <button key={p.name} className="preset" onClick={() => setForm({ name: p.name, command: p.command, args: p.args.join(" "), envName: p.envName || "", key: p.key || "" })}>
            <b>{p.label}</b><span className="muted small">{p.blurb}</span>{p.key && <span className="tag">needs {p.key}</span>}
          </button>
        ))}
        <button className="preset dashed" onClick={() => setForm({ name: "", command: "npx", args: "", envName: "", key: "" })}><b>+ Custom</b><span className="muted small">Any MCP server</span></button>
      </div>
      {form && (
        <div className="side-card form-card">
          <label>Name<input className="text-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value.replace(/[^a-z0-9-]/gi, "-").toLowerCase() })} /></label>
          <label>Command<input className="text-input" value={form.command} onChange={(e) => setForm({ ...form, command: e.target.value })} /></label>
          <label>Arguments<input className="text-input" value={form.args} onChange={(e) => setForm({ ...form, args: e.target.value })} /></label>
          <label>Env variable the server reads<input className="text-input" placeholder="optional" value={form.envName} onChange={(e) => setForm({ ...form, envName: e.target.value })} /></label>
          <label>…filled from your key<input className="text-input" placeholder="e.g. GITHUB_TOKEN" value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value.toUpperCase() })} /></label>
          <div className="row-actions">
            <button className="btn primary" disabled={!form.name || !form.command} onClick={() => add(form)}>Add connector</button>
            <button className="btn ghost" onClick={() => setForm(null)}>Cancel</button>
          </div>
        </div>
      )}
      {msg && <p className="muted small">{msg}</p>}
      {loading ? <Loading /> : err ? <Err msg={err} retry={reload} /> : (
        <div className="card-list">
          <h3 className="section-title">Connected ({servers.length})</h3>
          {servers.map((s, i) => {
            const name = str(s.name);
            const enabled = !(s.disabled === true || s.enabled === false);
            const managed = /kirocrew/i.test(name);
            return (
              <div key={i} className="row-card">
                <div className="grow"><b>{name}</b> {managed && <span className="tag">Core</span>}
                  <div className="muted small">{str(s.status || s.state || s.command || s.url).slice(0, 120)}</div></div>
                {!managed && <>
                  <button className={`toggle ${enabled ? "on" : ""}`} role="switch" aria-checked={enabled} onClick={async () => {
                    try { await gw("POST", "/api/mcp/toggle", { name, enabled: !enabled }); reload(); } catch (e) { alert((e as Error).message); }
                  }}><span /></button>
                  <button className="icon-btn" title="Remove" onClick={async () => {
                    if (!confirm(`Remove ${name}?`)) return;
                    try { await gw("DELETE", `/api/mcp/servers/${encodeURIComponent(name)}`); reload(); } catch (e) { alert((e as Error).message); }
                  }}><IconTrash size={15} /></button>
                </>}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

/* ── Scheduled tasks (crons) ────────────────────────────────────────────── */
function ScheduledTab({ onAsk }: { onAsk: (t: string) => void }) {
  const { data, err, loading, reload } = useRemote(() => gw("GET", "/api/crons"));
  const jobs = list(data, "jobs", "crons");
  const when = (j: AnyRec) => str(j.cron_expr || j.cron || (j.schedule as AnyRec)?.expr || (j.every ? `every ${j.every}s` : "") || j.at || j.schedule);
  return (
    <>
      <div className="toolbar">
        <p className="muted grow">Tasks Panda runs on a schedule while it's online. Results land in Notifications.</p>
        <button className="btn primary" onClick={() => onAsk("Schedule a recurring task for me: ")}><IconPlus size={15} /> New task</button>
      </div>
      {loading ? <Loading /> : err ? <Err msg={err} retry={reload} /> : jobs.length === 0 ? (
        <div className="empty-state"><div className="empty-art"><IconClock size={30} /></div><p>No scheduled tasks yet.</p>
          <p className="muted small">Try: “Every weekday at 9:00, send me 3 post ideas for LinkedIn”.</p></div>
      ) : (
        <div className="card-list">
          {jobs.map((j) => {
            const id = str(j.id || j.job_id);
            const enabled = j.enabled !== false && j.paused !== true;
            return (
              <div key={id} className="row-card">
                <div className="grow"><b>{str(j.name) || id}</b> <span className="tag">{when(j)}</span>
                  <div className="muted small">{str(j.message || j.prompt).slice(0, 200)}</div>
                  {j.next_run ? <div className="muted small">Next: {new Date(str(j.next_run)).toLocaleString()}</div> : null}</div>
                <button className="btn small-btn" onClick={async () => { try { await gw("POST", `/api/crons/${id}/run`); alert("Started — the result will arrive in Notifications."); } catch (e) { alert((e as Error).message); } }}>Run now</button>
                <button className={`toggle ${enabled ? "on" : ""}`} role="switch" aria-checked={enabled} onClick={async () => {
                  try { await gw("POST", `/api/crons/${id}/enable`, { enabled: !enabled }); reload(); } catch (e) { alert((e as Error).message); }
                }}><span /></button>
                <button className="icon-btn" title="Delete" onClick={async () => {
                  if (!confirm(`Delete "${str(j.name) || id}"?`)) return;
                  try { await gw("DELETE", `/api/crons/${id}`); reload(); } catch (e) { alert((e as Error).message); }
                }}><IconTrash size={15} /></button>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

/* ── API keys vault ─────────────────────────────────────────────────────── */
function KeysTab({ online }: { online: boolean }) {
  const [rows, setRows] = useState<{ name: string; note: string; updated_at: string }[]>([]);
  const [form, setForm] = useState({ name: "", value: "", note: "" });
  const [msg, setMsg] = useState("");
  const load = () => supabase.rpc("list_secrets").then(({ data }) => setRows(data || []));
  useEffect(() => { load(); }, []);
  const sync = () => online ? runCommand("secrets.sync").catch(() => {}) : Promise.resolve();

  return (
    <>
      <p className="muted">Store API keys once; Panda and your connectors can use them by name. You can replace or delete a key, but nobody — not even this app — can show it again.</p>
      <div className="side-card form-card">
        <label>Name<input className="text-input" placeholder="OPENAI_API_KEY" value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_") })} /></label>
        <label>Value<input className="text-input" type="password" autoComplete="off" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} /></label>
        <label>Note<input className="text-input" placeholder="optional — what it's for" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></label>
        <div className="row-actions">
          <button className="btn primary" disabled={!/^[A-Z][A-Z0-9_]{1,63}$/.test(form.name) || !form.value} onClick={async () => {
            const { error } = await supabase.rpc("set_secret", { p_name: form.name, p_value: form.value, p_note: form.note });
            if (error) return setMsg(error.message);
            setForm({ name: "", value: "", note: "" });
            setMsg(online ? "Saved. Panda can use it now." : "Saved. Panda will load it next time it starts.");
            load();
            sync();
          }}>Save key</button>
        </div>
      </div>
      {msg && <p className="muted small">{msg}</p>}
      <div className="card-list">
        {rows.map((r) => (
          <div key={r.name} className="row-card">
            <div className="grow"><b className="mono">{r.name}</b> <span className="muted small">•••••••• · {new Date(r.updated_at).toLocaleDateString()}</span>
              {r.note && <div className="muted small">{r.note}</div>}</div>
            <button className="icon-btn" title="Delete" onClick={async () => {
              if (!confirm(`Delete ${r.name}?`)) return;
              await supabase.rpc("delete_secret", { p_name: r.name });
              load();
              sync();
            }}><IconTrash size={15} /></button>
          </div>
        ))}
      </div>
    </>
  );
}
