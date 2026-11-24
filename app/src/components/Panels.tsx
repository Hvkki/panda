import { useEffect, useMemo, useState, type ReactNode } from "react";
import { getNotes, change, loginCode, loginStart, type Note } from "../lib/api";
import { updateSettings, useSettings, type Settings } from "../lib/settings";
import { EFFORTS, MODELS } from "../lib/models";
import type { Chat } from "../lib/store";
import { IconX } from "./Icons";

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className={`modal ${wide ? "wide" : ""}`} onClick={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><IconX /></button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

/* ── Search ───────────────────────────────────────────────────────────────── */
export function SearchModal({ chats, onOpen, onClose }: { chats: Chat[]; onOpen: (id: string) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const hits = useMemo(() => {
    const s = q.trim().toLowerCase();
    const list = [...chats].sort((a, b) => b.updated - a.updated);
    if (!s) return list.slice(0, 20).map((c) => ({ c, snip: "" }));
    return list.flatMap((c) => {
      if (c.title.toLowerCase().includes(s)) return [{ c, snip: "" }];
      const m = c.messages.find((x) => x.content.toLowerCase().includes(s));
      if (!m) return [];
      const i = m.content.toLowerCase().indexOf(s);
      return [{ c, snip: (i > 40 ? "…" : "") + m.content.slice(Math.max(0, i - 40), i + 80).replace(/\s+/g, " ") }];
    }).slice(0, 50);
  }, [q, chats]);
  return (
    <Modal title="Search chats" onClose={onClose}>
      <div className="search-box"><input autoFocus placeholder="Search titles and messages" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <div className="search-results">
        {hits.length === 0 && <p className="muted">No results</p>}
        {hits.map(({ c, snip }) => (
          <button key={c.id} className="search-hit" onClick={() => { onOpen(c.id); onClose(); }}>
            <div>{c.dev ? "Developer" : c.title}</div>
            <div className="muted small">{snip || new Date(c.updated).toLocaleString()}</div>
          </button>
        ))}
      </div>
    </Modal>
  );
}

/* ── Notifications ────────────────────────────────────────────────────────── */
const SEEN = "panda.notes.seen";
export const notesSeen = () => { try { return Number(localStorage.getItem(SEEN) || 0); } catch { return 0; } };

/** Polls the worker for scheduled-task results while it is online. */
export function useNotes(online: boolean) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [seen, setSeen] = useState(notesSeen);
  useEffect(() => {
    if (!online) return;
    const load = () => getNotes().then(setNotes).catch(() => {});
    load();
    const t = window.setInterval(load, 60000);
    return () => window.clearInterval(t);
  }, [online]);
  const markSeen = () => {
    const now = Math.floor(Date.now() / 1000);
    try { localStorage.setItem(SEEN, String(now)); } catch { /* blocked */ }
    setSeen(now);
  };
  return { notes, setNotes, unread: notes.filter((n) => n.at > seen).length, markSeen };
}

export function NotificationsModal({ notes, online, onClose, onOpenNote, onChanged }: {
  notes: Note[]; online: boolean; onClose: () => void; onOpenNote: (n: Note) => void; onChanged: (n: Note[]) => void;
}) {
  const clear = async (id?: string) => {
    const r = await change<{ notifications: Note[] }>("/notifications/clear", id ? { id } : {}).catch(() => null);
    if (r) onChanged(r.notifications);
  };
  return (
    <Modal title="Notifications" onClose={onClose}>
      {!online && <p className="muted">The worker is offline. Notifications from scheduled tasks show up here once it is running.</p>}
      {online && notes.length === 0 && <p className="muted">Nothing yet. Scheduled tasks post their results here.</p>}
      <div className="card-list">
        {notes.map((n) => (
          <div key={n.id} className={`note ${n.error ? "err" : ""}`}>
            <div className="note-head"><b>{n.title}</b><span className="muted small">{new Date(n.at * 1000).toLocaleString()}</span></div>
            <div className="note-text">{n.text.slice(0, 400)}{n.text.length > 400 ? "…" : ""}</div>
            <div className="row-gap">
              <button className="btn" onClick={() => { onOpenNote(n); onClose(); }}>Open as chat</button>
              <button className="btn ghost" onClick={() => clear(n.id)}>Dismiss</button>
            </div>
          </div>
        ))}
      </div>
      {notes.length > 0 && <button className="btn ghost" onClick={() => clear()}>Clear all</button>}
    </Modal>
  );
}

/* ── Settings ─────────────────────────────────────────────────────────────── */
type Tab = "general" | "chat" | "claude" | "data";
const TAB_LABEL: Record<Tab, string> = { general: "General", chat: "Chat", claude: "Claude account", data: "Data" };

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="set-row">
      <div><div>{label}</div>{hint && <div className="muted small">{hint}</div>}</div>
      <div className="set-control">{children}</div>
    </div>
  );
}
function Toggle({ k, label, hint }: { k: keyof Settings; label: string; hint?: string }) {
  const s = useSettings();
  const on = Boolean(s[k]);
  return (
    <Row label={label} hint={hint}>
      <button className={`toggle ${on ? "on" : ""}`} role="switch" aria-checked={on} aria-label={label}
        onClick={() => updateSettings({ [k]: !on } as Partial<Settings>)}><span /></button>
    </Row>
  );
}
function Pick({ k, label, hint, options }: { k: keyof Settings; label: string; hint?: string; options: [string, string][] }) {
  const s = useSettings();
  return (
    <Row label={label} hint={hint}>
      <select value={String(s[k])} onChange={(e) => updateSettings({ [k]: e.target.value } as Partial<Settings>)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </Row>
  );
}

export function SettingsModal({ online, chats, onImport, onClearAll, onLogout, onClose }: {
  online: boolean; chats: Chat[]; onImport: (c: Chat[]) => void; onClearAll: () => void; onLogout: () => void; onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("general");
  return (
    <Modal title="Settings" onClose={onClose} wide>
      <div className="set-layout">
        <div className="set-tabs">{(Object.keys(TAB_LABEL) as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{TAB_LABEL[t]}</button>
        ))}</div>
        <div className="set-panel">
          {tab === "general" && <>
            <Pick k="theme" label="Theme" options={[["system", "System"], ["light", "Light"], ["dark", "Dark"]]} />
            <Pick k="textSize" label="Text size" options={[["small", "Small"], ["medium", "Medium"], ["large", "Large"]]} />
            <Pick k="width" label="Chat width" options={[["narrow", "Narrow"], ["wide", "Wide"]]} />
            <Pick k="font" label="Reply font" options={[["serif", "Serif"], ["sans", "Sans"]]} />
          </>}
          {tab === "chat" && <>
            <Pick k="defaultModel" label="Default model" options={MODELS.map((m) => [m.id, m.name])} />
            <Pick k="defaultEffort" label="Default effort" options={EFFORTS.map((e) => [e.id, e.label])} />
            <Toggle k="showThinking" label="Show thinking" hint="Claude's reasoning, folded above the answer" />
            <Toggle k="enterToSend" label="Enter sends" hint="On a computer keyboard; Shift+Enter for a new line" />
            <Toggle k="notifyReplies" label="Notify when a reply is ready" hint="A browser notification when Panda is in the background" />
          </>}
          {tab === "claude" && <ClaudeAccount online={online} />}
          {tab === "data" && <DataTab chats={chats} onImport={onImport} onClearAll={onClearAll} onLogout={onLogout} />}
        </div>
      </div>
    </Modal>
  );
}

function ClaudeAccount({ online }: { online: boolean }) {
  const [url, setUrl] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  if (!online) return <p className="muted">Start the worker to connect a Claude account.</p>;
  return (
    <>
      <p className="muted">Panda uses your Claude subscription through a long-lived token (about a year). Reconnect here when it expires or to switch accounts.</p>
      {!url ? (
        <button className="btn primary" disabled={busy} onClick={async () => {
          setBusy(true); setMsg("");
          try { setUrl((await loginStart()).url); } catch (e) { setMsg((e as Error).message); }
          setBusy(false);
        }}>{busy ? "Preparing…" : "Connect Claude account"}</button>
      ) : (
        <div className="login-steps">
          <p>1. <a href={url} target="_blank" rel="noreferrer">Open the Claude login</a> and approve.</p>
          <p>2. Paste the code Claude shows you:</p>
          <input className="text-input wide" value={code} placeholder="code#state" onChange={(e) => setCode(e.target.value)} />
          <button className="btn primary" disabled={busy || !code.trim()} onClick={async () => {
            setBusy(true); setMsg("");
            try { await loginCode(code); setMsg("Connected. The new token is saved to your Kaggle dataset."); setUrl(""); setCode(""); }
            catch (e) { setMsg((e as Error).message); }
            setBusy(false);
          }}>{busy ? "Connecting…" : "Finish"}</button>
        </div>
      )}
      {msg && <p className="muted">{msg}</p>}
    </>
  );
}

function DataTab({ chats, onImport, onClearAll, onLogout }: { chats: Chat[]; onImport: (c: Chat[]) => void; onClearAll: () => void; onLogout: () => void }) {
  const [msg, setMsg] = useState("");
  const exportAll = () => {
    const blob = new Blob([JSON.stringify({ panda: 1, chats }, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `panda-chats-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
  };
  return (
    <>
      <p className="muted">Chats and projects are stored in this browser only. Export them to keep a copy or move them to another device.</p>
      <Row label="Export chats" hint={`${chats.length} chats`}><button className="btn" onClick={exportAll}>Download</button></Row>
      <Row label="Import chats" hint="From a Panda export file; existing chats are kept">
        <label className="btn">Choose file<input type="file" accept="application/json" hidden onChange={async (e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          try {
            const data = JSON.parse(await f.text());
            const list: Chat[] = Array.isArray(data) ? data : data.chats;
            if (!Array.isArray(list)) throw new Error("Not a Panda export");
            onImport(list);
            setMsg(`Imported ${list.length} chats.`);
          } catch (err) { setMsg((err as Error).message); }
        }} /></label>
      </Row>
      <Row label="Delete all chats" hint="Cannot be undone">
        <button className="btn danger" onClick={() => { if (confirm("Delete every chat in this browser?")) onClearAll(); }}>Delete</button>
      </Row>
      <Row label="Log out" hint="Forget the password on this device"><button className="btn" onClick={onLogout}>Log out</button></Row>
      {msg && <p className="muted">{msg}</p>}
    </>
  );
}
