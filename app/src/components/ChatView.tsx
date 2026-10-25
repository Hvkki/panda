import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { supabase, type Chat, type Message } from "../lib/supabase";
import { sideAsk } from "../lib/commands";
import { fmtCredits } from "../lib/usage";
import { useT } from "../lib/i18n";
import { MessageView, PendingReply, commandOf } from "./MessageView";
import { Composer, type SendMeta } from "./Composer";
import { IconCoins, IconSpinner, IconX } from "./Icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Attachment } from "../lib/files";

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
  const [sides, setSides] = useState<Side[]>([]);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    supabase.from("messages").select("*").eq("chat_id", chat.id).order("id").then(({ data }) => {
      if (!alive) return;
      setMessages((data as Message[]) || []);
      setLoading(false);
      stick.current = true;
    });
    const ch = supabase.channel(`chat-${chat.id}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "messages", filter: `chat_id=eq.${chat.id}` },
        (p) => {
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
      .subscribe();
    return () => { alive = false; supabase.removeChannel(ch); };
  }, [chat.id]);

  // Keep the view pinned to the bottom while streaming, unless the user scrolled up.
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages, sides]);

  const streaming = messages.find((m) => m.role === "assistant" && (m.status === "streaming" || m.status === "waiting"));
  const busy = !!streaming || messages.some((m) => m.role === "user" && (m.status === "processing"));

  const stop = useCallback(() => {
    if (streaming) supabase.from("messages").update({ cancel: true }).eq("id", streaming.id).then(() => {});
  }, [streaming]);

  const retry = useCallback((m: Message) => {
    const question = messages.find((x) => x.id === m.reply_to);
    if (question) onSendMessage(chat, question.content, question.meta.attachments);
  }, [messages, chat, onSendMessage]);

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
    <div className="chat">
      <div className="chat-scroll" ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}>
        <div className="chat-inner">
          {loading ? <div className="chat-loading"><span /><span /><span /></div> :
            messages.map((m) => (
              <MessageView key={m.id} m={m} isLast={m.id === lastAssistant?.id}
                command={m.reply_to ? commandFor.get(m.reply_to) : undefined}
                onRetry={m.role === "assistant" ? retry : undefined}
                onOption={(text) => onSendMessage(chat, text)} />
            ))}
          {!loading && waiting && <PendingReply queued={waiting.status === "pending"} model={chat.model} />}
          {!loading && totals.replies > 0 && (
            <div className="chat-total" title="Kiro credits used by Panda's replies in this chat">
              <IconCoins size={13} /> {fmtCredits(totals.credits)} {t("creditsUnit")} · {totals.replies} {totals.replies === 1 ? "reply" : "replies"}
            </div>
          )}
        </div>
      </div>
      <div className="chat-bottom">
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
          onStop={stop} busy={busy} autoFocus context={context} />
      </div>
    </div>
  );
}
