import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Chat, Message } from "../lib/store";
import { useSettings } from "../lib/settings";
import { Composer } from "./Composer";
import { fmtTokens } from "./UsageSheet";
import { IconAlert, IconCheck, IconChevron, IconBrain, IconCopy, IconRetry, IconSpinner, IconX } from "./Icons";
import { modelName } from "../lib/models";
import { dialogOpen } from "../lib/dismiss";

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

/** Split Markdown at blank lines outside code fences, so a streaming reply re-renders only its last block. */
function blocks(text: string): string[] {
  const out: string[] = [];
  let cur: string[] = [], fence = "";
  for (const line of text.split("\n")) {
    const f = line.match(/^\s*(`{3,}|~{3,})/);
    if (f) fence = !fence ? f[1] : line.trim().startsWith(fence) ? "" : fence;
    if (!fence && !f && !line.trim() && cur.length) { out.push(cur.join("\n")); cur = []; continue; }
    cur.push(line);
  }
  if (cur.length) out.push(cur.join("\n"));
  return out;
}

function StreamingMarkdown({ text }: { text: string }) {
  return <>{blocks(text).map((b, i) => <Markdown key={i} text={b} />)}</>;
}

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

type ToolT = NonNullable<Message["tools"]>[number];

/** "Searched code, ran 2 commands, edited 3 files" from the tool labels ("Bash: ls", "Read: /x"…). */
export function toolSummary(tools: ToolT[]) {
  const n: Record<string, number> = {};
  for (const t of tools) {
    const name = t.label.split(":")[0].trim();
    const k = name === "Bash" ? "cmd" : name === "Read" ? "read" : /^(Grep|Glob|LS)$/.test(name) ? "search"
      : /^(Edit|Write|MultiEdit|NotebookEdit)$/.test(name) ? "edit" : /^Web(Search|Fetch)$/.test(name) ? "web"
      : name.startsWith("mcp__browser") ? "browser" : /^(Task|Agent)$/.test(name) ? "agent"
      : /^(TodoWrite|ToolSearch)$/.test(name) ? "" : "tool";
    if (k) n[k] = (n[k] || 0) + 1;
  }
  const pl = (c: number, one: string, many: string) => `${c} ${c === 1 ? one : many}`;
  const parts = [
    n.search && "searched code",
    n.read && `read ${pl(n.read, "file", "files")}`,
    n.edit && `edited ${pl(n.edit, "file", "files")}`,
    n.cmd && `ran ${pl(n.cmd, "command", "commands")}`,
    n.web && "searched the web",
    n.browser && "used the browser",
    n.agent && `ran ${pl(n.agent, "agent", "agents")}`,
    n.tool && `used ${pl(n.tool, "tool", "tools")}`,
  ].filter(Boolean) as string[];
  const s = parts.join(", ") || `${pl(tools.length, "step", "steps")}`;
  return s[0].toUpperCase() + s.slice(1);
}

/** A run of tool calls between two paragraphs: one summary line that opens into the steps. */
/** "Bash: npm test" → ["Bash", "npm test"]; MCP names lose their prefix. */
function splitLabel(label: string): [string, string] {
  const i = label.indexOf(":");
  const name = (i < 0 ? label : label.slice(0, i)).trim().replace(/^mcp__([^_]+)__/, "$1 · ");
  return [name, i < 0 ? "" : label.slice(i + 1).trim()];
}

function Activity({ tools, all, live }: { tools: ToolT[]; all: ToolT[]; live: boolean }) {
  const { toolsOpen } = useSettings();
  const [open, setOpen] = useState(toolsOpen);
  if (!tools.length) return null;
  const kids = (id: string) => all.filter((t) => t.parent === id);
  const running = live ? [...tools].reverse().find((t) => !t.done) : undefined;
  const current = running && ([...kids(running.id)].reverse().find((t) => !t.done) || running);
  const failed = tools.filter((t) => t.error).length;
  const Step = ({ t, sub }: { t: ToolT; sub?: boolean }) => {
    const [name, detail] = splitLabel(t.label);
    const state = !t.done && live ? "run" : t.error ? "error" : "ok";
    return (
      <div className={`step ${state} ${sub ? "sub" : ""}`} title={t.label}>
        <span className="step-dot" aria-hidden />
        <span className="step-label"><b>{name}</b>{detail && <span className="step-detail">({detail})</span>}</span>
      </div>
    );
  };
  return (
    <div className={`activity ${open ? "open" : ""} ${running ? "live" : ""}`}>
      <button className="activity-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="act-icon">{running ? <IconSpinner size={14} /> : failed ? <IconAlert size={14} /> : <IconCheck size={14} />}</span>
        <span className="act-sum">{toolSummary(tools)}{failed ? <span className="act-err"> · {failed} failed</span> : null}</span>
        <span className="chev side"><IconChevron size={14} /></span>
      </button>
      {current && !open && <div className="act-now" title={current.label}><span className="act-elbow">⎿</span>{current.label}</div>}
      {open && (
        <div className="activity-list">
          {tools.map((t) => (
            <div key={t.id}>
              <Step t={t} />
              {kids(t.id).map((k) => <Step key={k.id} t={k} sub />)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

type Seg = { k: "text"; text: string } | { k: "tools"; tools: ToolT[] };
/** Turn a reply's parts into paragraphs and runs of tool calls, in order. */
function segments(m: Message): Seg[] {
  const tools = m.tools || [];
  if (!m.parts?.length) {  // replies saved before parts existed
    const top = tools.filter((t) => !t.parent);
    return [...(top.length ? [{ k: "tools", tools: top } as Seg] : []), ...(m.content ? [{ k: "text", text: m.content } as Seg] : [])];
  }
  const byId = new Map(tools.map((t) => [t.id, t]));
  const out: Seg[] = [];
  for (const p of m.parts) {
    const prev = out[out.length - 1];
    if (p.k === "text") { if (p.text.trim()) out.push({ k: "text", text: p.text }); continue; }
    const t = byId.get(p.id);
    if (!t) continue;
    if (prev?.k === "tools") prev.tools.push(t); else out.push({ k: "tools", tools: [t] });
  }
  return out;
}

const GLYPHS = ["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"];
/** The accent "Working…" line while Claude works. */
function LiveStatus({ m }: { m: Message }) {
  const tools = m.tools || [];
  const word = m.status === "waiting" ? "Starting the worker" : tools.some((t) => !t.done) ? "Working"
    : m.content ? "Writing" : m.thinking ? "Thinking" : "Pondering";
  const [{ tick, secs }, setT] = useState({ tick: 0, secs: 0 });
  const started = m.started;
  useEffect(() => {
    const t0 = started || Date.now();
    const id = window.setInterval(() => setT((v) => ({ tick: v.tick + 1, secs: Math.floor((Date.now() - t0) / 1000) })), 120);
    return () => window.clearInterval(id);
  }, [started]);
  return (
    <div className="live-status" role="status">
      <span className="live-glyph" aria-hidden>{GLYPHS[tick % GLYPHS.length]}</span>
      <span className="live-word">{word}…</span>
      <span className="live-secs">{secs >= 1 ? `${secs}s` : ""}{window.innerWidth > 700 ? " · esc to stop" : ""}</span>
    </div>
  );
}

const MessageView = memo(function MessageView({ m, model, onRetry }: { m: Message; model: string; onRetry?: () => void }) {
  const [copied, setCopied] = useState(false);
  const { showThinking, showUsage } = useSettings();
  if (m.role === "user") return <div className="msg user"><span className="gutter" aria-hidden>›</span><div className="bubble">{m.content}</div></div>;
  const live = m.status === "streaming" || m.status === "waiting";
  const u = m.usage;
  return (
    <div className="msg assistant">
      {m.thinking && showThinking && <Thinking text={m.thinking} live={live && !m.content} />}
      {segments(m).map((g, i, all) => {
        const lastSeg = i === all.length - 1;
        return g.k === "tools"
          ? <Activity key={i} tools={g.tools} all={m.tools || []} live={live} />
          : <div key={i} className="prose">{live && lastSeg ? <StreamingMarkdown text={g.text} /> : <Markdown text={g.text} />}</div>;
      })}
      {m.status === "error" && <div className="error-note"><IconAlert size={15} /> {m.error || "Something went wrong."}</div>}
      {live ? <LiveStatus m={m} /> : (
        <div className="msg-actions">
          <button className="tap" aria-label="Copy" title="Copy"
            onClick={() => { navigator.clipboard.writeText(m.content); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
            {copied ? <IconCheck size={17} /> : <IconCopy size={17} />}
          </button>
          {onRetry && <button className="tap" onClick={onRetry} title="Retry" aria-label="Retry"><IconRetry size={17} /></button>}
          <span className="muted meta-line">
            {modelName(model)}{m.secs ? ` · ${m.secs}s` : ""}
            {u && showUsage ? ` · ${fmtTokens(u.input + u.cache_write)} in · ${fmtTokens(u.cache_read)} cached · ${fmtTokens(u.output)} out` : ""}
          </span>
        </div>
      )}
    </div>
  );
});

type Props = {
  chat: Chat;
  onModel?: (model: string, effort: string) => void;
  onSend: (text: string, attached: string[]) => void;
  onRetry: () => void;
  onStop: () => void;
  canAttach: boolean;
};

export function ChatView({ chat, onSend, onRetry, onStop, canAttach, onModel }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [queue, setQueue] = useState<{ id: number; text: string; attached: string[] }[]>([]);
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [chat.messages]);
  const last = chat.messages[chat.messages.length - 1];
  const busy = last?.role === "assistant" && (last.status === "streaming" || last.status === "waiting");
  // Send the next queued message once the current turn ends (not after an error or a stop).
  useEffect(() => {
    if (busy || !queue.length) return;
    if (last?.status === "error") return;
    const [next, ...rest] = queue;
    setQueue(rest);
    stick.current = true;
    onSend(next.text, next.attached);
  }, [busy, queue, last, onSend]);
  useEffect(() => {
    if (!busy) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && !dialogOpen()) { setQueue([]); onStop(); } };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [busy, onStop]);
  const send = (text: string, attached: string[]) => {
    if (busy) setQueue((q) => [...q, { id: Date.now(), text, attached }]);
    else { stick.current = true; onSend(text, attached); }
  };
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
        {queue.length > 0 && (
          <div className="queue">
            {queue.map((q) => (
              <div key={q.id} className="queue-item">
                <span className="muted small">Queued</span>
                <span className="queue-text">{q.text || q.attached.join(", ")}</span>
                <button className="tap" onClick={() => setQueue((x) => x.filter((y) => y.id !== q.id))} aria-label="Remove from queue"><IconX size={14} /></button>
              </div>
            ))}
          </div>
        )}
        <Composer onSend={send} model={chat.model} effort={chat.effort} onModel={onModel}
          commands={{ online: canAttach, dev: chat.dev }} onStop={() => { setQueue([]); onStop(); }}
          busy={busy} autoFocus canAttach={canAttach} target={{ folder: chat.project_id, dev: chat.dev }}
          placeholder={chat.dev ? "Tell Claude what to change in Panda" : undefined} />
      </div>
    </div>
  );
}
