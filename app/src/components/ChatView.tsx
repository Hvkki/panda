import { useCallback, useEffect, useRef, useState } from "react";
import { supabase, type Chat, type Message } from "../lib/supabase";
import { MessageView, PendingReply } from "./MessageView";
import { Composer } from "./Composer";
import type { Attachment } from "../lib/files";

type Props = {
  chat: Chat;
  onModel: (model: string, effort: string) => void;
  onSendMessage: (chat: Chat, text: string, attachments?: Attachment[]) => Promise<void>;
  pendingFirst?: boolean;
};

export function ChatView({ chat, onModel, onSendMessage }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
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
  }, [messages]);

  const streaming = messages.find((m) => m.role === "assistant" && (m.status === "streaming" || m.status === "waiting"));
  const busy = !!streaming || messages.some((m) => m.role === "user" && (m.status === "processing"));

  const stop = useCallback(() => {
    if (streaming) supabase.from("messages").update({ cancel: true }).eq("id", streaming.id).then(() => {});
  }, [streaming]);

  const retry = useCallback((m: Message) => {
    const question = messages.find((x) => x.id === m.reply_to);
    if (question) onSendMessage(chat, question.content, question.meta.attachments);
  }, [messages, chat, onSendMessage]);

  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  // A sent message that no reply has picked up yet: show Panda getting ready (or queued).
  const waiting = [...messages].reverse().find((m) => m.role === "user" && (m.status === "pending" || m.status === "processing")
    && !messages.some((r) => r.role === "assistant" && r.reply_to === m.id));

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
                onRetry={m.role === "assistant" ? retry : undefined}
                onOption={(text) => onSendMessage(chat, text)} />
            ))}
          {!loading && waiting && <PendingReply queued={waiting.status === "pending"} />}
        </div>
      </div>
      <div className="chat-bottom">
        <Composer model={chat.model} effort={chat.effort} onModel={onModel}
          onSend={(text, att) => onSendMessage(chat, text, att)} onStop={stop} busy={busy} autoFocus />
      </div>
    </div>
  );
}
