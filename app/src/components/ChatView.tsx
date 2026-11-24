import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Chat, Message } from "../lib/store";
import { useSettings } from "../lib/settings";
import { Composer } from "./Composer";
import { Logo } from "./Logo";
import { IconAlert, IconCheck, IconChevron, IconBrain, IconCopy, IconRetry, IconSpinner } from "./Icons";
import { modelName } from "../lib/models";

const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]}
      components={{
        a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
        code: ({ className, children, ...rest }) => {
          const inline = !className && !String(children).includes("\n");
          if (inline) return <code className="inline-code" {...rest}>{children}</code>;
          return <CodeBlock lang={(className || "").replace("language-", "")} code={String(children).replace(/\n$/, "")} />;
        },
        table: (p) => <div className="table-wrap"><table {...p} /></div>,
      }}>
      {text}
    </ReactMarkdown>
  );
});

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="codeblock">
      <div className="codeblock-head">
        <span>{lang || "text"}</span>
        <button className="tap" aria-label="Copy code"
          onClick={() => { navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
          {copied ? <IconCheck size={15} /> : <IconCopy size={15} />}
        </button>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  );
}

function Thinking({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`thinking ${open ? "open" : ""}`}>
      <button className="thinking-head tap" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <IconBrain size={15} /> {live ? <span className="shimmer">Thinking</span> : "Thought process"}
        <span className="chev"><IconChevron size={14} /></span>
      </button>
      {open && <div className="thinking-body">{text}</div>}
    </div>
  );
}

function Activity({ m, live }: { m: Message; live: boolean }) {
  const tools = m.tools || [];
  const [open, setOpen] = useState(false);
  if (!tools.length) return null;
  const row = (t: Message["tools"] extends (infer T)[] | undefined ? T : never, current: boolean) => (
    <div key={t.id} className={`step ${current ? "current" : "done"} ${t.error ? "error" : ""}`} title={t.label}>
      <span className="step-icon">{!t.done && current ? <IconSpinner size={15} /> : t.error ? <IconAlert size={15} /> : <IconCheck size={15} />}</span>
      <span className="step-label">{t.label}</span>
    </div>
  );
  if (live && !m.content) {
    return <div className="activity live">{tools.slice(-4).map((t, i, a) => row(t, i === a.length - 1))}</div>;
  }
  return (
    <div className={`activity ${open ? "open" : ""}`}>
      <button className="activity-head tap" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span>{tools.length} step{tools.length === 1 ? "" : "s"}</span>
        <span className="chev"><IconChevron size={14} /></span>
      </button>
      {open && <div className="activity-list">{tools.map((t) => row(t, false))}</div>}
    </div>
  );
}

const MessageView = memo(function MessageView({ m, model, onRetry }: { m: Message; model: string; onRetry?: () => void }) {
  const [copied, setCopied] = useState(false);
  const { showThinking } = useSettings();
  if (m.role === "user") return <div className="msg user"><div className="bubble">{m.content}</div></div>;
  const live = m.status === "streaming" || m.status === "waiting";
  return (
    <div className="msg assistant">
      <div className="avatar"><Logo size={28} animated working={live} /></div>
      <div className="msg-body">
        {m.thinking && showThinking && <Thinking text={m.thinking} live={live && !m.content} />}
        <Activity m={m} live={live} />
        {m.content ? <div className="prose"><Markdown text={m.content} /></div> :
          live && !(m.tools || []).length && !m.thinking ? (
            <div className="activity live"><div className="step current">
              <span className="step-icon"><IconSpinner size={15} /></span>
              <span className="step-label shimmer">{m.status === "waiting" ? "Starting the worker. This takes about 30 seconds." : "Thinking"}</span>
            </div></div>
          ) : null}
        {m.status === "error" && <div className="error-note"><IconAlert size={15} /> {m.error || "Something went wrong."}</div>}
        {!live && (
          <div className="msg-actions">
            <button className="tap" aria-label="Copy" title="Copy"
              onClick={() => { navigator.clipboard.writeText(m.content); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
              {copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
            </button>
            {onRetry && <button className="tap" onClick={onRetry} title="Retry" aria-label="Retry"><IconRetry size={16} /></button>}
            <span className="muted meta-line">{modelName(model)}{m.secs ? ` · ${m.secs}s` : ""}</span>
          </div>
        )}
      </div>
    </div>
  );
});

type Props = {
  chat: Chat;
  onModel: (model: string, effort: string) => void;
  onSend: (text: string, attached: string[]) => void;
  onRetry: () => void;
  onStop: () => void;
  canAttach: boolean;
};

export function ChatView({ chat, onModel, onSend, onRetry, onStop, canAttach }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [chat.messages]);
  const last = chat.messages[chat.messages.length - 1];
  const busy = last?.role === "assistant" && (last.status === "streaming" || last.status === "waiting");
  return (
    <div className="chat">
      <div className="chat-scroll" ref={scroller}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
        <div className="chat-inner">
          {chat.messages.map((m) => (
            <MessageView key={m.id} m={m} model={chat.model} onRetry={m === last && m.role === "assistant" ? onRetry : undefined} />
          ))}
        </div>
      </div>
      <div className="chat-bottom">
        <Composer model={chat.model} effort={chat.effort} onModel={onModel} onSend={onSend} onStop={onStop}
          busy={busy} autoFocus canAttach={canAttach} target={{ folder: chat.project_id, dev: chat.dev }}
          placeholder={chat.dev ? "Tell Claude what to change in Panda" : undefined} />
      </div>
    </div>
  );
}
