import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { onRejoin, onResume, supabase, type Chat, type Message } from "../lib/supabase";
import { sideAsk } from "../lib/commands";
import { fmtCredits } from "../lib/usage";
import { useT } from "../lib/i18n";
import { MessageView, PendingReply, commandOf } from "./MessageView";
import { Composer, type SendMeta } from "./Composer";
import { Helpers } from "./Helpers";
import { Timeline, useTimelineOpen } from "./Timeline";
import { IconCoins, IconList, IconSpinner, IconX } from "./Icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Attachment } from "../lib/files";
import { useSettings } from "../lib/settings";

/** Every `messages` column except `thinking`. Keep in step with the table (supabase/schema.sql). */
const MESSAGE_COLS_NO_THINKING = "id,chat_id,owner,role,content,status,reply_to,meta,created_at,updated_at,cancel,decision";

/** A Postgres timestamp ("2026-10-29T22:16:10.169511+00:00", with or without zone) as ms, or 0. */
function tsOf(s?: string | null): number {
  const m = /^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d(?::?\d\d)?)?$/.exec(s || "");
  if (!m) return 0;
  let tz = m[4] || "Z";
  if (tz !== "Z") tz = tz.length === 3 ? `${tz}:00` : tz.length === 5 ? `${tz.slice(0, 3)}:${tz.slice(3)}` : tz;
  return Date.parse(`${m[1]}T${m[2]}${(m[3] || "").slice(0, 4)}${tz}`) || 0;
}

type Props = {
  chat: Chat;
  onModel: (model: string, effort: string) => void;
  onSendMessage: (chat: Chat, text: string, attachments?: Attachment[], meta?: SendMeta) => Promise<void>;
  /** App-wide commands (/new, /credits, /browser, /settings). */
  onCommand?: (name: string, args: string) => boolean | void;
  online: boolean;
};

type Side = { id: number; question: string; answer?: string; error?: string };

export function ChatView({ chat, onModel, onSendMessage, onCommand, online }: Props) {
  const t = useT();
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const { settings } = useSettings();
  const withThinking = !!settings.showThinking; // thinking is ~70-85% of a long chat's bytes; load it only when shown
  const [sides, setSides] = useState<Side[]>([]);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const reload = useRef<(() => void) | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const cols = withThinking ? "*" : MESSAGE_COLS_NO_THINKING;
    let seen = 0; // newest updated_at we hold (server time, ms): catch-up reads start a little before it
    const note = (rows: Message[]) => { for (const r of rows) seen = Math.max(seen, tsOf(r.updated_at)); };
    // A re-read can land just after a newer live update of a reply still being written: keep the longer text.
    const pick = (p: Message | undefined, r: Message) => p && r.status === "streaming" && p.status === "streaming" &&
      (p.content || "").length > (r.content || "").length ? p : r;
    // Full read: the first load, and when there is nothing to catch up from.
    const load = (first = false) => supabase.from("messages").select(cols).eq("chat_id", chat.id).order("id")
      .then(({ data, error }) => {
        if (!alive || error || !data) { if (alive && first) setLoading(false); return; }
        const rows = data as unknown as Message[];
        note(rows);
        setMessages((prev) => first ? rows : rows.map((r) => pick(prev.find((m) => m.id === r.id), r)));
        setLoading(false);
        if (first) stick.current = true;
      });
    // Catch-up read whenever the live link may have missed changes: only the rows changed since the
    // newest one we hold, plus the id list so a deleted row goes too. Re-reading the whole chat every
    // 6 s (2 MB for a long chat, mostly thinking) was most of the database's traffic on 29 Oct.
    const sync = () => {
      if (!seen) { load(); return; }
      const since = new Date(seen - 5000).toISOString();
      Promise.all([
        supabase.from("messages").select("id").eq("chat_id", chat.id),
        supabase.from("messages").select(cols).eq("chat_id", chat.id).gte("updated_at", since).order("id"),
      ]).then(([ids, changed]) => {
        if (!alive || ids.error || changed.error || !ids.data || !changed.data) return;
        const kept = new Set((ids.data as { id: number }[]).map((r) => r.id));
        const rows = changed.data as unknown as Message[];
        note(rows);
        setMessages((prev) => {
          const byId = new Map(prev.filter((m) => kept.has(m.id)).map((m) => [m.id, m] as const));
          for (const r of rows) byId.set(r.id, pick(byId.get(r.id), r));
          return [...byId.values()].sort((a, b) => a.id - b.id);
        });
      });
    };
    load(true);
    const ch = supabase.channel(`chat-${chat.id}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "messages", filter: `chat_id=eq.${chat.id}` },
        (p) => {
          if (p.eventType !== "DELETE") note([p.new as Message]);
          setMessages((prev) => {
            if (p.eventType === "DELETE") return prev.filter((m) => m.id !== (p.old as Message).id);
            const row = p.new as Message;
            const i = prev.findIndex((m) => m.id === row.id);
            if (i === -1) return [...prev, row].sort((a, b) => a.id - b.id);
            const next = prev.slice();
            next[i] = row;
            return next;
          });
        })
      .subscribe(onRejoin(() => sync()));
    const off = onResume(() => sync());
    reload.current = () => sync();
    return () => { alive = false; supabase.removeChannel(ch); off(); reload.current = null; };
  }, [chat.id, withThinking]);

  // Keep the view pinned to the bottom while streaming, unless the user scrolled up.
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages, sides]);

  const streaming = messages.find((m) => m.role === "assistant" && (m.status === "streaming" || m.status === "waiting"));
  const [tlOpen, setTlOpen] = useTimelineOpen();
  // The timeline follows the running answer; with nothing running it can show the last one on request.
  const tlMsg = streaming && (streaming.meta.tools?.length || 0) > 0 ? streaming : null;
  const busy = !!streaming || messages.some((m) => m.role === "user" && (m.status === "processing"));

  // Safety net while Panda answers: a live link can drop silently with no error at all, so re-read every
  // few seconds until the answer is done. Nothing runs when the chat is idle.
  const inFlight = busy || messages.some((m) => m.role === "user" && m.status === "pending");
  useEffect(() => {
    if (!inFlight) return;
    const id = window.setInterval(() => { if (!document.hidden) reload.current?.(); }, 6000);
    return () => window.clearInterval(id);
  }, [inFlight]);

  const stop = useCallback(() => {
    if (streaming) { supabase.from("messages").update({ cancel: true }).eq("id", streaming.id).then(() => {}); return; }
    // Nothing is answering yet (the message still waits in line): take it out of the line.
    const queued = [...messages].reverse().find((m) => m.role === "user" && m.status === "pending");
    if (queued) supabase.from("messages").update({ status: "done", meta: { ...queued.meta, stopped: true } })
      .eq("id", queued.id).eq("status", "pending").then(() => {});
  }, [streaming, messages]);

  const retry = useCallback((m: Message) => {
    if (busy) return; // one turn at a time in a chat: a retry now would start a second, parallel one
    const question = messages.find((x) => x.id === m.reply_to);
    if (question) onSendMessage(chat, question.content, question.meta.attachments);
  }, [messages, chat, onSendMessage, busy]);

  const askSide = useCallback((question: string) => {
    const id = Date.now();
    setSides((s) => [...s, { id, question }]);
    stick.current = true;
    sideAsk(chat.id, question)
      .then((r) => setSides((s) => s.map((x) => x.id === id ? { ...x, answer: r.answer || "", error: r.error && !r.answer ? r.note || "No answer." : undefined } : x)))
      .catch((e) => setSides((s) => s.map((x) => x.id === id ? { ...x, error: (e as Error).message } : x)));
  }, [chat.id]);

  // Commands that need this chat; the rest go to the app.
  const command = useCallback((name: string, args: string) => {
    if (name === "/stop") { if (!streaming) return false; stop(); return true; }
    if (name === "/btw" || name === "/side") {
      if (!online) { alert("Panda is offline. Start it to ask a side question."); return true; }
      askSide(args);
      return true;
    }
    if (name === "/title") {
      supabase.from("chats").update({ title: args.slice(0, 80) }).eq("id", chat.id).then(() => {});
      return true;
    }
    return onCommand?.(name, args);
  }, [streaming, stop, online, askSide, chat.id, onCommand]);

  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  // A sent message that no reply has picked up yet: show Panda getting ready (or queued).
  const waiting = [...messages].reverse().find((m) => m.role === "user" && !m.meta?.steer && (m.status === "pending" || m.status === "processing")
    && !messages.some((r) => r.role === "assistant" && r.reply_to === m.id));
  const commandFor = useMemo(() => {
    const map = new Map<number, string>();
    for (const m of messages) if (m.role === "user" && m.meta?.command) map.set(m.id, commandOf(m));
    return map;
  }, [messages]);
  const totals = useMemo(() => {
    let credits = 0, replies = 0;
    for (const m of messages) {
      const c = m.meta?.credits ?? m.meta?.turn_stats?.credits;
      if (m.role === "assistant" && typeof c === "number") { credits += c; replies += 1; }
    }
    return { credits, replies };
  }, [messages]);
  // Always show the meter in a chat (0% until the first reading arrives after a reply).
  const context = [...messages].reverse().find((m) => m.role === "assistant" && m.meta?.usage && typeof m.meta.usage.pct === "number")?.meta.usage
    ?? { pct: 0, reset: true };

  return (
    <div className={`chat-wrap ${tlMsg && tlOpen ? "with-timeline" : ""}`}>
    <div className="chat">
      <div className="chat-scroll" ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}>
        <div className="chat-inner">
          {chat.agent === "incognito" && <div className="incognito-banner">{t("incognitoNote")}</div>}
          {loading ? <div className="chat-loading"><span /><span /><span /></div> :
            messages.map((m) => (
              <MessageView key={m.id} m={m} isLast={m.id === lastAssistant?.id}
                command={m.reply_to ? commandFor.get(m.reply_to) : undefined}
                onRetry={m.role === "assistant" ? retry : undefined}
                onOption={(text) => onSendMessage(chat, text)} />
            ))}
          {!loading && waiting && !streaming && <PendingReply queued={waiting.status === "pending"} model={chat.model} online={online} />}
          {!loading && <Helpers slot={chat.slot_id} online={online} busy={busy} />}
          {!loading && totals.replies > 0 && (
            <div className="chat-total" title="Kiro credits used by Panda's replies in this chat">
              <IconCoins size={13} /> {fmtCredits(totals.credits)} {t("creditsUnit")} · {totals.replies} {totals.replies === 1 ? "reply" : "replies"}
            </div>
          )}
        </div>
      </div>
      <div className="chat-bottom">
        {tlMsg && !tlOpen && (
          <button className="timeline-pill" onClick={() => setTlOpen(true)}>
            <IconList size={14} /> {tlMsg.meta.tl?.now ? `Now: ${tlMsg.meta.tl.now}` : "Show what Panda is doing"}
          </button>
        )}
        {sides.length > 0 && (
          <div className="side-stack">
            {sides.map((s) => (
              <div key={s.id} className="side-answer">
                <div className="side-q"><span className="tag">{t("sideQuestion")}</span> {s.question}
                  <button className="icon-btn" onClick={() => setSides((x) => x.filter((y) => y.id !== s.id))} aria-label="Dismiss"><IconX size={15} /></button></div>
                {s.answer ? <div className="prose small-prose"><ReactMarkdown remarkPlugins={[remarkGfm]}>{s.answer}</ReactMarkdown></div>
                  : s.error ? <div className="error-note">{s.error}</div>
                  : <div className="muted small side-wait"><IconSpinner size={14} /> Asking on the side — the main answer keeps going…</div>}
              </div>
            ))}
          </div>
        )}
        <Composer model={chat.model} effort={chat.effort} onModel={onModel} online={online}
          onSend={(text, att, meta) => onSendMessage(chat, text, att, meta)} onCommand={command}
          onStop={stop} busy={busy} autoFocus context={context} draftKey={chat.id} />
      </div>
    </div>
    {tlMsg && tlOpen && <Timeline m={tlMsg} onClose={() => setTlOpen(false)} />}
    </div>
  );
}
