import { useEffect, useMemo, useState, type ReactNode } from "react";
import { supabase, type Chat, type Notification } from "../lib/supabase";
import { useSettings, type Settings } from "../lib/settings";
import { useT } from "../lib/i18n";
import { ALL_MODELS, EFFORTS } from "../lib/models";
import { IconX } from "./Icons";
import { disablePush, enablePush, pushState } from "../lib/push";

function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
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

/* ── Settings ─────────────────────────────────────────────────────────────── */

type Tab = "general" | "chat" | "notifications" | "memory" | "account";
const TAB_LABEL: Record<Tab, string> = { general: "General", chat: "Chat & tools", notifications: "Notifications", memory: "Memory", account: "Account" };

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="set-row">
      <div>
        <div>{label}</div>
        {hint && <div className="muted small">{hint}</div>}
      </div>
      <div className="set-control">{children}</div>
    </div>
  );
}

function Toggle({ k, hint, label }: { k: keyof Settings; label: string; hint?: string }) {
  const { settings, update } = useSettings();
  const on = Boolean(settings[k]);
  return (
    <Row label={label} hint={hint}>
      <button className={`toggle ${on ? "on" : ""}`} role="switch" aria-checked={on} aria-label={label}
        onClick={() => update({ [k]: !on } as Partial<Settings>)}><span /></button>
    </Row>
  );
}

function Pick({ k, label, hint, options }: { k: keyof Settings; label: string; hint?: string; options: [string, string][] }) {
  const { settings, update } = useSettings();
  return (
    <Row label={label} hint={hint}>
      <select value={String(settings[k])} onChange={(e) => update({ [k]: e.target.value } as Partial<Settings>)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </Row>
  );
}

function ForgetRow({ userId }: { userId: string }) {
  const [msg, setMsg] = useState("");
  return (
    <>
      <Row label="Forget browser logins" hint="Deletes every site you (or Panda) signed in to in the browser — on the running worker and in your encrypted backup.">
        <button className="btn danger" onClick={async () => {
          if (!confirm("Delete all saved browser logins and cookies?")) return;
          const { error } = await supabase.from("commands").insert({ owner: userId, op: "browser.forget" });
          setMsg(error ? error.message : "Done — it will be wiped now if Panda is online, otherwise the next time it starts.");
        }}>Forget</button>
      </Row>
      {msg && <p className="muted small">{msg}</p>}
    </>
  );
}

function PushRow({ userId }: { userId: string }) {
  const [state, setState] = useState<string>("…");
  const [err, setErr] = useState("");
  useEffect(() => { pushState().then(setState); }, []);
  const on = state === "on";
  return (
    <>
      <Row label="Phone notifications" hint={state === "unsupported" ? "Not supported here. On iPhone, add Panda to your Home Screen first."
        : state === "blocked" ? "Blocked — allow notifications for this site in your browser settings." : "Get notified even when Panda is closed."}>
        <button className={`toggle ${on ? "on" : ""}`} role="switch" aria-checked={on} disabled={state === "unsupported" || state === "blocked"}
          onClick={async () => {
            setErr("");
            try { if (on) await disablePush(); else await enablePush(userId); } catch (e) { setErr((e as Error).message); }
            setState(await pushState());
          }}><span /></button>
      </Row>
      {err && <p className="error-note small">{err}</p>}
    </>
  );
}

export function SettingsModal({ email, userId, onClose }: { email: string; userId: string; onClose: () => void }) {
  const { settings, update } = useSettings();
  const t = useT();
  const [tab, setTab] = useState<Tab>("general");
  const [pwMsg, setPwMsg] = useState("");

  return (
    <Modal title={t("settings")} onClose={onClose} wide>
      <div className="settings">
        <div className="set-tabs">
          {(Object.keys(TAB_LABEL) as Tab[]).map((x) => (
            <button key={x} className={tab === x ? "active" : ""} onClick={() => setTab(x)}>{TAB_LABEL[x]}</button>
          ))}
        </div>
        <div className="set-panel">
          {tab === "general" && <>
            <h3>Appearance</h3>
            <Pick k="theme" label="Theme" options={[["system", "System"], ["light", "Light"], ["dark", "Dark"]]} />
            <Pick k="font" label="Response font" options={[["serif", "Serif"], ["sans", "Sans"]]} />
            <Pick k="textSize" label="Text size" options={[["small", "Small"], ["medium", "Medium"], ["large", "Large"]]} />
            <Pick k="width" label="Chat width" options={[["narrow", "Narrow"], ["wide", "Wide"]]} />
            <h3>Language</h3>
            <Pick k="language" label="Interface language" hint="Panda answers in the language you write in."
              options={[["en", "English"], ["it", "Italiano"], ["uk", "Українська"]]} />
            <h3>You</h3>
            <Row label="What should Panda call you?">
              <input className="text-input" value={settings.name} placeholder="Name"
                onChange={(e) => update({ name: e.target.value })} />
            </Row>
          </>}

          {tab === "chat" && <>
            <h3>Thinking</h3>
            <Toggle k="showThinking" label="Show thinking"
              hint="Show the model's thought process above answers. Hidden unless you turn this on." />
            <h3>Defaults for new chats</h3>
            <Pick k="defaultModel" label="Model" options={ALL_MODELS.map((m) => [m.id, `${m.name} (${m.credits}×)`])} />
            <Pick k="defaultEffort" label="Effort" options={EFFORTS.map((e) => [e.id, e.label])} />
            <h3>Tools & approvals</h3>
            <Pick k="approvals" label="When Panda wants to use a tool"
              hint="Ask shows an Approve card. Trust lets it work without asking (fastest)."
              options={[["ask", "Ask me"], ["trust", "Trust (don't ask)"]]} />
            <Toggle k="background_trust" label="Allow scheduled tasks to use tools"
              hint="Scheduled jobs run without you; they can't wait for a tap." />
            <h3>Browser</h3>
            <ForgetRow userId={userId} />
            <h3>Worker</h3>
            <Toggle k="autoStart" label="Start automatically when I send"
              hint="If Panda is offline, sending a message presses Start for you." />
            <Toggle k="enterToSend" label="Enter sends (desktop)" hint="Shift+Enter for a new line." />
          </>}

          {tab === "notifications" && <>
            <h3>Notifications</h3>
            <PushRow userId={userId} />
            <Toggle k="notifyReplies" label="Response completions" hint="Notify me when a long answer finishes." />
            <p className="muted small">Scheduled-task results always notify you when phone notifications are on.</p>
          </>}

          {tab === "memory" && <>
            <h3>Memory</h3>
            <Toggle k="memory" label="Generate memory from chats"
              hint="Panda learns your preferences and projects over time. Applies from the next Start." />
            <p className="muted small">Existing memories stay available either way. Scheduled tasks, lessons and
              skills are stored in your private backup between sessions.</p>
          </>}

          {tab === "account" && <>
            <h3>Account</h3>
            <Row label="Email"><span className="muted">{email}</span></Row>
            <Row label="Password" hint="We email you a link to set a new one.">
              <button className="btn" onClick={async () => {
                const { error } = await supabase.auth.resetPasswordForEmail(email, {
                  redirectTo: window.location.origin + window.location.pathname,
                });
                setPwMsg(error ? error.message : "Email sent.");
              }}>Change</button>
            </Row>
            {pwMsg && <p className="muted small">{pwMsg}</p>}
            <Row label="Sign out of this device">
              <button className="btn danger" onClick={() => supabase.auth.signOut()}>{t("signOut")}</button>
            </Row>
          </>}
        </div>
      </div>
    </Modal>
  );
}

/* ── Search ───────────────────────────────────────────────────────────────── */

type Hit = { chat_id: string; title: string; snippet: string };

export function SearchModal({ chats, onOpen, onClose }: { chats: Chat[]; onOpen: (id: string) => void; onClose: () => void }) {
  const t = useT();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const titleHits = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? chats.filter((c) => c.title.toLowerCase().includes(s)).slice(0, 8) : chats.slice(0, 8);
  }, [q, chats]);

  useEffect(() => {
    const s = q.trim();
    if (s.length < 2) { setHits([]); return; }
    const timer = window.setTimeout(async () => {
      const { data } = await supabase.from("messages").select("chat_id, content")
        .ilike("content", `%${s.replace(/[%_]/g, "")}%`).in("role", ["user", "assistant"])
        .order("id", { ascending: false }).limit(30);
      const seen = new Set<string>();
      const out: Hit[] = [];
      for (const r of (data || []) as { chat_id: string; content: string }[]) {
        if (seen.has(r.chat_id)) continue;
        seen.add(r.chat_id);
        const i = r.content.toLowerCase().indexOf(s.toLowerCase());
        const snippet = r.content.slice(Math.max(0, i - 40), i + 80).replace(/\s+/g, " ");
        out.push({ chat_id: r.chat_id, title: chats.find((c) => c.id === r.chat_id)?.title || "Chat", snippet });
      }
      setHits(out);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [q, chats]);

  return (
    <Modal title={t("search")} onClose={onClose}>
      <input className="search-input" autoFocus value={q} placeholder={t("search") + "…"} onChange={(e) => setQ(e.target.value)} />
      <div className="search-results">
        {titleHits.map((c) => (
          <button key={c.id} className="search-row" onClick={() => { onOpen(c.id); onClose(); }}>{c.title}</button>
        ))}
        {hits.filter((h) => !titleHits.some((c) => c.id === h.chat_id)).map((h) => (
          <button key={h.chat_id} className="search-row" onClick={() => { onOpen(h.chat_id); onClose(); }}>
            <div>{h.title}</div>
            <div className="muted small">…{h.snippet}…</div>
          </button>
        ))}
        {q && !titleHits.length && !hits.length && <div className="muted pad">{t("noResults")}</div>}
      </div>
    </Modal>
  );
}

/* ── Notifications (scheduled task results) ──────────────────────────────── */

export function NotificationsModal({ items, onClose }: { items: Notification[]; onClose: () => void }) {
  const t = useT();
  useEffect(() => {
    const unread = items.filter((n) => !n.read).map((n) => n.id);
    if (unread.length) supabase.from("notifications").update({ read: true }).in("id", unread).then(() => {});
  }, [items]);
  return (
    <Modal title={t("notifications")} onClose={onClose}>
      {items.length === 0 && <div className="muted pad">Results of scheduled tasks will appear here.</div>}
      <div className="notif-list">
        {items.map((n) => (
          <div key={n.id} className={`notif ${n.read ? "" : "unread"}`}>
            <div className="notif-title">{n.meta.title || n.source}</div>
            <div className="notif-text">{n.text}</div>
            <div className="muted small">{new Date(n.created_at).toLocaleString()}</div>
          </div>
        ))}
      </div>
    </Modal>
  );
}
