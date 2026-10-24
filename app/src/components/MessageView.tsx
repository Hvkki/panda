import { memo, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { supabase, type Message } from "../lib/supabase";
import { useSettings } from "../lib/settings";
import { useT } from "../lib/i18n";
import { modelName } from "../lib/models";
import { describeStep, summarize } from "../lib/steps";
import { Logo } from "./Logo";
import { MessageFiles, PandaImage, PandaLink } from "./Files";
import { fileKind, type Attachment } from "../lib/files";
import { useBrowser, usedBrowser } from "../lib/browser";
import { BrowserInline } from "./BrowserInline";
import { IconAlert, IconBrain, IconCheck, IconChevron, IconCopy, IconRetry, IconShield, IconSpinner } from "./Icons";

const OPTIONS_RE = /\[OPTIONS:\s*([^\]]+)\]\s*$/;

function splitOptions(text: string): [string, string[]] {
  const m = text.match(OPTIONS_RE);
  if (!m) return [text, []];
  return [text.slice(0, m.index).trimEnd(), m[1].split("|").map((s) => s.trim()).filter(Boolean).slice(0, 4)];
}

const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]}
      components={{
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        a: ({ node, ...p }) => <PandaLink {...p} />,
        img: ({ src, alt }) => <PandaImage src={typeof src === "string" ? src : undefined} alt={alt} />,
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
        <button className="tap" onClick={() => { navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
          aria-label="Copy code">
          {copied ? <IconCheck size={15} /> : <IconCopy size={15} />}
        </button>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  );
}

/* ── thinking (only shown when Settings → Show thinking is on) ────────────── */
function Thinking({ text, live }: { text: string; live: boolean }) {
  const t = useT();
  const [open, setOpen] = useState(live);
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => { if (live) setOpen(true); }, [live]);
  useEffect(() => { if (open && live && body.current) body.current.scrollTop = body.current.scrollHeight; }, [text, open, live]);
  return (
    <div className={`thinking ${open ? "open" : ""}`}>
      <button className="thinking-head tap" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <IconBrain size={15} /> {live ? <span className="shimmer">{t("thinking")}</span> : t("thought")}
        <span className="chev"><IconChevron size={14} /></span>
      </button>
      {open && <div className="thinking-body" ref={body}>{text}</div>}
    </div>
  );
}

/* ── activity: what Panda is doing, step by step ────────────────────────── */
function Activity({ tools, live, hasText }: { tools: string[]; live: boolean; hasText: boolean }) {
  const steps = useMemo(() => {
    const raw = tools.map(describeStep);
    // Kiro Crew emits a tool-call and its result as two lines; collapse consecutive same labels.
    return raw.filter((s, i) => i === 0 || s.label !== raw[i - 1].label);
  }, [tools]);
  const [open, setOpen] = useState(false);
  if (!steps.length) return null;
  const running = live && !hasText;
  if (running) {
    const shown = steps.slice(-4);
    return (
      <div className="activity live">
        {shown.map((s, i) => {
          const current = i === shown.length - 1;
          return (
            <div key={steps.length - shown.length + i} className={`step ${current ? "current" : "done"} ${s.error ? "error" : ""}`} title={s.detail}>
              <span className="step-icon">{current ? <IconSpinner size={15} /> : s.error ? <IconAlert size={15} /> : <IconCheck size={15} />}</span>
              <span className="step-glyph">{s.icon}</span>
              <span className="step-label">{s.label}</span>
            </div>
          );
        })}
      </div>
    );
  }
  return (
    <div className={`activity ${open ? "open" : ""}`}>
      <button className="activity-head tap" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="step-glyph">{steps[steps.length - 1].icon}</span>
        <span>{summarize(steps)}</span>
        <span className="chev"><IconChevron size={14} /></span>
      </button>
      {open && (
        <div className="activity-list">
          {steps.map((s, i) => (
            <div key={i} className={`step done ${s.error ? "error" : ""}`} title={s.detail}>
              <span className="step-glyph">{s.icon}</span>
              <span className="step-label">{s.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── approval card ──────────────────────────────────────────────────────── */
export function ApprovalCard({ m }: { m: Message }) {
  const t = useT();
  const [sent, setSent] = useState<string | null>(null);
  const pending = m.status === "pending" && !m.decision && !sent;
  const step = describeStep(m.content || "");
  const answer = async (decision: "approved" | "rejected" | "trust") => {
    setSent(decision);
    const { error } = await supabase.from("messages").update({ decision }).eq("id", m.id);
    if (error) { setSent(null); alert(error.message); }
  };
  const done = m.decision || sent;
  return (
    <div className={`approval ${pending ? "live" : ""}`}>
      <div className="approval-head">
        <span className="approval-icon"><IconShield size={18} /></span>
        <div>
          <div className="approval-title">{t("approvalNeeded")}</div>
          <div className="approval-tool"><span className="step-glyph">{step.icon}</span>{step.label}</div>
        </div>
      </div>
      {pending ? (
        <div className="approval-actions">
          <button className="btn primary" onClick={() => answer("approved")}>{t("approve")}</button>
          <button className="btn" onClick={() => answer("trust")}>{t("alwaysAllow")}</button>
          <button className="btn ghost" onClick={() => answer("rejected")}>{t("deny")}</button>
        </div>
      ) : (
        <div className={`approval-done ${done === "rejected" ? "no" : "yes"}`}>
          {done === "rejected" ? <IconAlert size={14} /> : <IconCheck size={14} />}
          {done === "rejected" ? (m.meta.timed_out ? "No answer — skipped" : "Denied") :
            done === "trust" ? "Always allowed in this chat" : "Approved"}
        </div>
      )}
    </div>
  );
}

/* ── placeholder while the worker picks the message up ─────────────────── */
export function PendingReply({ queued }: { queued: boolean }) {
  const t = useT();
  return (
    <div className="msg assistant">
      <div className="avatar"><Logo size={28} working /></div>
      <div className="msg-body">
        <div className="activity live">
          <div className="step current">
            <span className="step-icon"><IconSpinner size={15} /></span>
            <span className="step-label">{queued ? t("queued") : "Getting ready…"}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

type Props = { m: Message; onRetry?: (m: Message) => void; onOption?: (text: string) => void; isLast: boolean };

export const MessageView = memo(function MessageView({ m, onRetry, onOption, isLast }: Props) {
  const { settings } = useSettings();
  const t = useT();
  const [copied, setCopied] = useState(false);

  const { online } = useBrowser();
  if (m.role === "tool") return m.meta.kind === "approval" ? <ApprovalCard m={m} /> : null;

  if (m.role === "user") {
    return (
      <div className="msg user">
        {(m.meta.attachments || []).length > 0 && (
          <div className="attach-row right">
            {(m.meta.attachments || []).map((a: Attachment) => {
              const k = fileKind(a.name, a.mime);
              return <div key={a.path} className="attach-chip"><span className="file-badge" style={{ background: k.color }}>{k.label}</span><span className="attach-name">{a.name}</span></div>;
            })}
          </div>
        )}
        <div className="bubble">{m.content}</div>
      </div>
    );
  }

  const live = m.status === "streaming" || m.status === "waiting";
  const [body, options] = splitOptions(m.content || "");
  const tools = (m.meta.tools || []).filter(Boolean);
  const showThinking = settings.showThinking && !!m.thinking;
  return (
    <div className="msg assistant">
      <div className="avatar"><Logo size={28} animated working={live} /></div>
      <div className="msg-body">
        {showThinking && <Thinking text={m.thinking} live={live && !m.content} />}
        <Activity tools={tools} live={live} hasText={!!body} />
        {body ? <div className="prose"><Markdown text={body} /></div> :
          live && !tools.length && !showThinking ? (
            <div className="activity live"><div className="step current">
              <span className="step-icon"><IconSpinner size={15} /></span>
              <span className="step-label shimmer">{m.status === "waiting" ? "Waiting for your approval" : t("thinking")}</span>
            </div></div>
          ) : null}
        {isLast && online && usedBrowser(tools) && <BrowserInline live={live} />}
        {!live && <MessageFiles messageId={m.id} ids={m.meta.files} />}
        {m.status === "error" && (
          <div className="error-note"><IconAlert size={15} /> {m.meta.error || "Something went wrong."}</div>
        )}
        {!live && options.length > 0 && isLast && (
          <div className="options">
            {options.map((o) => <button key={o} className="chip" onClick={() => onOption?.(o)}>{o}</button>)}
          </div>
        )}
        {!live && (
          <div className="msg-actions">
            <button className="tap" onClick={() => { navigator.clipboard.writeText(body); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
              title={t("copy")} aria-label={t("copy")}>{copied ? <IconCheck size={16} /> : <IconCopy size={16} />}</button>
            {onRetry && <button className="tap" onClick={() => onRetry(m)} title={t("retry")} aria-label={t("retry")}><IconRetry size={16} /></button>}
            <span className="muted meta-line">
              {m.meta.model ? modelName(m.meta.model) : ""}{m.meta.secs ? ` · ${m.meta.secs}s` : ""}
            </span>
          </div>
        )}
      </div>
    </div>
  );
});
