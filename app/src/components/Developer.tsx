import { useEffect, useMemo, useState } from "react";
import { supabase, type Chat } from "../lib/supabase";
import { useSettings } from "../lib/settings";
import { runCommand } from "../lib/commands";
import type { Attachment } from "../lib/files";
import { ChatView } from "./ChatView";
import type { SendMeta } from "./Composer";
import { IconCode, IconPlus, IconSpinner, IconTrash } from "./Icons";

/** Vault names of developer variables carry this prefix; the worker strips it in ~/.panda/dev.env. */
export const DEV_PREFIX = "DEV__";
const NAME_RE = /^[A-Z][A-Z0-9_]{0,57}$/;

type Props = {
  userId: string;
  chats: Chat[];
  online: boolean;
  onSendMessage: (chat: Chat, text: string, attachments?: Attachment[], meta?: SendMeta) => Promise<void>;
  onChat: (chat: Chat) => void;
};

/**
 * Developer: a chat where Panda may change Panda itself (its UI, worker, skills), and the env
 * variables only that chat can use. Other chats never see these variables.
 */
export function DeveloperPage({ userId, chats, online, onSendMessage, onChat }: Props) {
  const [tab, setTab] = useState<"chat" | "env">("chat");
  return (
    <div className="dev-page">
      <div className="dev-head">
        <h1 className="serif"><IconCode size={22} /> Developer</h1>
        <div className="tabs">
          <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>Chat</button>
          <button className={tab === "env" ? "active" : ""} onClick={() => setTab("env")}>Env variables</button>
        </div>
      </div>
      {tab === "chat" ? <DevChat userId={userId} chats={chats} online={online} onSendMessage={onSendMessage} onChat={onChat} />
        : <div className="page dev-env"><EnvTab online={online} /></div>}
    </div>
  );
}

function DevChat({ userId, chats, online, onSendMessage, onChat }: Props) {
  const { settings, update, loaded } = useSettings();
  const id = settings.developerChatId;
  const known = useMemo(() => chats.find((c) => c.id === id) || null, [chats, id]);
  const [chat, setChat] = useState<Chat | null>(known);
  const [err, setErr] = useState("");
  useEffect(() => { if (known) setChat(known); }, [known]);

  // The Developer chat is created once and remembered in the user's settings (the worker reads it there).
  useEffect(() => {
    if (!loaded || known) return;
    let alive = true;
    (async () => {
      if (id) {
        const { data } = await supabase.from("chats").select("*").eq("id", id).maybeSingle();
        if (data && !data.archived) { if (alive) { setChat(data as Chat); onChat(data as Chat); } return; }
      }
      const { data, error } = await supabase.from("chats").insert({ owner: userId, title: "Developer",
        model: settings.defaultModel, effort: "high" }).select().single();
      if (!alive) return;
      if (error || !data) { setErr(error?.message || "Could not create the Developer chat"); return; }
      update({ developerChatId: data.id });
      setChat(data as Chat);
      onChat(data as Chat);
    })();
    return () => { alive = false; };
  }, [loaded, known, id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (err) return <div className="error-note pad">{err}</div>;
  if (!chat) return <div className="muted pad"><IconSpinner size={15} /> Opening the Developer chat…</div>;

  const setModel = async (model: string, effort: string) => {
    setChat({ ...chat, model, effort });
    await supabase.from("chats").update({ model, effort }).eq("id", chat.id);
  };
  return (
    <div className="dev-chat">
      <div className="dev-note muted small">Ask Panda to change its own UI or functions here — it can edit its code, build it and deploy it.
        Only this chat can use your developer env variables.</div>
      <ChatView key={chat.id} chat={chat} onModel={setModel} onSendMessage={onSendMessage} online={online}
        onCommand={(n) => (n === "/new" ? false : undefined)} />
    </div>
  );
}

type Row = { name: string; note: string; updated_at: string };

/** Parse .env text: NAME=value lines, `export`, quotes and comments allowed. */
function parseEnv(text: string): { name: string; value: string }[] {
  const out: { name: string; value: string }[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const m = line.match(/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!m) continue;
    let v = m[2].trim();
    if ((v.startsWith('"') && v.endsWith('"') && v.length > 1) || (v.startsWith("'") && v.endsWith("'") && v.length > 1)) v = v.slice(1, -1);
    else v = v.replace(/\s+#.*$/, "");
    if (v) out.push({ name: m[1].toUpperCase(), value: v });
  }
  return out;
}

function EnvTab({ online }: { online: boolean }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [form, setForm] = useState({ name: "", value: "", note: "" });
  const [bulk, setBulk] = useState("");
  const [bulkOpen, setBulkOpen] = useState(false);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => supabase.rpc("list_secrets").then(({ data }) =>
    setRows(((data || []) as Row[]).filter((r) => r.name.startsWith(DEV_PREFIX)).map((r) => ({ ...r, name: r.name.slice(DEV_PREFIX.length) }))));
  useEffect(() => { load(); }, []);
  const sync = () => (online ? runCommand("secrets.sync").catch(() => {}) : Promise.resolve());
  const saved = (n: number) => setMsg(`${n} variable${n === 1 ? "" : "s"} saved. ${online ? "Panda can use them in the Developer chat now." : "Panda loads them next time it starts."}`);

  const save = async (list: { name: string; value: string; note?: string }[]) => {
    setBusy(true);
    let n = 0;
    const bad: string[] = [];
    for (const v of list) {
      if (!NAME_RE.test(v.name)) { bad.push(v.name); continue; }
      const { error } = await supabase.rpc("set_secret", { p_name: DEV_PREFIX + v.name, p_value: v.value, p_note: v.note || "developer" });
      if (error) bad.push(`${v.name} (${error.message})`); else n++;
    }
    setBusy(false);
    await load();
    await sync();
    if (bad.length) setMsg(`Saved ${n}. Not saved: ${bad.join(", ")}`); else saved(n);
    return n;
  };

  const parsed = parseEnv(bulk);
  return (
    <>
      <p className="muted">Variables for the <b>Developer chat only</b> (deploy tokens, project keys…). They are written to
        <code> ~/.panda/dev.env</code> just while that chat answers, are hidden from replies and logs, and can be replaced or
        deleted but never shown again. Your normal API keys stay in Customize → API keys.</p>
      <div className="side-card form-card">
        <label>Name<input className="text-input" placeholder="VERCEL_TOKEN" value={form.name} autoCapitalize="characters"
          onChange={(e) => setForm({ ...form, name: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_") })} /></label>
        <label>Value<input className="text-input" type="password" autoComplete="off" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} /></label>
        <label>Note<input className="text-input" placeholder="optional" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></label>
        <div className="row-actions">
          <button className="btn primary" disabled={busy || !NAME_RE.test(form.name) || !form.value} onClick={async () => {
            if (await save([form])) setForm({ name: "", value: "", note: "" });
          }}><IconPlus size={15} /> Save variable</button>
          <button className="btn ghost" onClick={() => setBulkOpen((v) => !v)}>Import .env</button>
        </div>
      </div>
      {bulkOpen && (
        <div className="side-card dev-bulk">
          <textarea className="text-input" rows={7} spellCheck={false} autoComplete="off" value={bulk} onChange={(e) => setBulk(e.target.value)}
            placeholder={"Paste lines like\nVERCEL_TOKEN=...\nSUPABASE_SECRET_KEY=...\n# comments are skipped"} />
          <div className="row-actions">
            <span className="muted small grow">{parsed.length ? `${parsed.length} variable${parsed.length === 1 ? "" : "s"}: ${parsed.map((p) => p.name).join(", ")}` : "Nothing to import yet."}</span>
            <button className="btn primary" disabled={busy || !parsed.length} onClick={async () => {
              await save(parsed);
              setBulk("");
              setBulkOpen(false);
            }}>{busy ? "Saving…" : `Import ${parsed.length || ""}`}</button>
          </div>
        </div>
      )}
      {msg && <p className="muted small">{msg}</p>}
      <div className="card-list">
        {rows.length === 0 && <p className="muted small">No developer variables yet.</p>}
        {rows.map((r) => (
          <div key={r.name} className="row-card">
            <div className="grow"><b className="mono">{r.name}</b> <span className="muted small">•••••••• · {new Date(r.updated_at).toLocaleDateString()}</span>
              {r.note && r.note !== "developer" && <div className="muted small">{r.note}</div>}</div>
            <button className="icon-btn" title="Delete" aria-label={`Delete ${r.name}`} onClick={async () => {
              if (!confirm(`Delete ${r.name}?`)) return;
              await supabase.rpc("delete_secret", { p_name: DEV_PREFIX + r.name });
              load();
              sync();
            }}><IconTrash size={15} /></button>
          </div>
        ))}
      </div>
    </>
  );
}
