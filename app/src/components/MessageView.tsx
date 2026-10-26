import { memo, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import { supabase, type Message } from "../lib/supabase";
import { useSettings } from "../lib/settings";
import { useT } from "../lib/i18n";
import { modelName } from "../lib/models";
import { describeStep, summarize } from "../lib/steps";
import { fmtCredits } from "../lib/usage";
import { useSmoothText } from "../lib/smoothText";
import { Logo } from "./Logo";
import { MessageFiles, PandaImage, PandaLink } from "./Files";
import { fileKind, type Attachment } from "../lib/files";
import { useBrowser, usedBrowser } from "../lib/browser";
import { BrowserCard } from "./BrowserCard";
import { DrawingPanda } from "./DrawingPanda";
import { IconAlert, IconBrain, IconCheck, IconChevron, IconCoins, IconCopy, IconRetry, IconShield, IconSlash, IconSpinner } from "./Icons";

/** "/compact focus on the plan" → "/compact" when the message is a command. */
export const commandOf = (m: Pick<Message, "content" | "meta">) =>
  m.meta?.command || ((m.content || "").match(/^\s*(\/[a-z][\w-]*)/i)?.[1]?.toLowerCase() ?? "");

const COMMAND_NOTE: Record<string, string> = {
  "/clear": "Panda's memory of this chat was cleared. Messages above stay visible.",
  "/compact": "Earlier messages were summarised to free up context.",
};

/** A short hop of the panda when a reply has just finished (not for replies already on screen). */
function useJustFinished(live: boolean, ok: boolean) {
  const [hop, setHop] = useState(false);
  const was = useRef(live);
  useEffect(() => {
    if (was.current && !live && ok) {
      setHop(true);
      const id = window.setTimeout(() => setHop(false), 1600);
      was.current = live;
      return () => window.clearTimeout(id);
    }
    was.current = live;
  }, [live, ok]);
  return hop;
}

/** When `key` last changed (ms), plus a clock that ticks each second while `live`. */
function useChangedAt(key: string | number, live: boolean): [number, number] {
  const [at, setAt] = useState(() => Date.now());
  const [prev, setPrev] = useState(key);
  if (prev !== key) { setPrev(key); setAt(Date.now()); }
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [live]);
  return [at, Math.max(now, at)];
}

const secsLabel = (ms: number) => (ms >= 3000 ? ` · ${Math.round(ms / 1000)}s` : "");

const OPTIONS_RE = /\[OPTIONS:\s*([^\]]+)\]\s*$/;

/** react-markdown drops every URL scheme it does not know, so `panda-file:<id>` (an uploaded file,
 *  see Files.tsx) would reach the image and link components as "". Other URLs keep the default check. */
const keepPandaFiles = (url: string) => (/^panda-file:[\w-]+$/.test(url) ? url : defaultUrlTransform(url));

function splitOptions(text: string): [string, string[]] {
  const m = text.match(OPTIONS_RE);
  if (!m) return [text, []];
  return [text.slice(0, m.index).trimEnd(), m[1].split("|").map((s) => s.trim()).filter(Boolean).slice(0, 4)];
}

const Markdown = memo(function Markdown({ text, live = false }: { text: string; live?: boolean }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={keepPandaFiles}
      components={{
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        a: ({ node, ...p }) => <PandaLink {...p} />,
        img: ({ src, alt }) => <PandaImage src={typeof src === "string" ? src : undefined} alt={alt} live={live} />,
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
export function PendingReply({ queued, model }: { queued: boolean; model?: string }) {
  const t = useT();
  return (
    <div className="msg assistant">
      <div className="avatar"><Logo size={28} working model={model} /></div>
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

type Props = { m: Message; onRetry?: (m: Message) => void; onOption?: (text: string) => void; isLast: boolean; command?: string };

export const MessageView = memo(function MessageView({ m, onRetry, onOption, isLast, command }: Props) {
  const { settings } = useSettings();
  const t = useT();
  const [copied, setCopied] = useState(false);
  const live = m.status === "streaming" || m.status === "waiting";
  const hop = useJustFinished(live, m.status === "done");
  // Smooth the half-second jumps of the stream into a steady flow (history renders at once).
  const smooth = useSmoothText(m.role === "assistant" ? m.content || "" : "", live);
  // Quiet stretches (a long tool call, thinking before the first word) still show signs of life.
  const toolCount = (m.meta?.tools || []).length;
  const [textAt, now] = useChangedAt((m.content || "").length, live);
  const [toolAt] = useChangedAt(toolCount, live);
  const [startAt] = useChangedAt(m.id, live);
  // A picture is being made: the newest step is the image tool. The key changes per image step
  // (steps with the same label count once, like the activity list), so each one starts at 0%.
  const toolList: string[] | undefined = m.meta?.tools;
  const drawKey = useMemo(() => {
    if (m.status !== "streaming") return "";
    const labels = (toolList || []).filter(Boolean).map((x) => describeStep(x));
    const last = labels[labels.length - 1];
    if (!last?.image) return "";
    return `img-${labels.filter((s, i) => i === 0 || s.label !== labels[i - 1].label).length}`;
  }, [m.status, toolList]);
  const [drawAt] = useChangedAt(drawKey, live);

  const { online } = useBrowser();
  if (m.role === "tool") return m.meta.kind === "approval" ? <ApprovalCard m={m} /> : null;

  if (m.role === "user") {
    const cmd = commandOf(m);
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
        {cmd && m.meta.command ? (
          <div className="bubble command"><IconSlash size={15} /><span>{m.content}</span></div>
        ) : <div className="bubble">{m.content}</div>}
        {m.meta.steer && (
          <div className="steer-tag muted">{m.status === "done"
            ? (m.meta.steered === "queued" ? "Panda answers this next" : "Steered into the answer")
            : <><IconSpinner size={11} /> Steering…</>}</div>
        )}
      </div>
    );
  }

  const cmd = m.meta.command || command || "";
  const [fullBody, options] = splitOptions(m.content || "");
  // While typing, hide a half-arrived "[OPTIONS: …" tail; the chips appear when the reply is done.
  const body = smooth.typing ? splitOptions(smooth.text)[0].replace(/\[OPTIONS:[^\]]*$/, "").trimEnd() : fullBody;
  const proseCls = `prose${smooth.typing ? " streaming" : ""}`;
  const tools = (m.meta.tools || []).filter(Boolean);
  const showThinking = settings.showThinking && !!m.thinking;
  const credits = typeof m.meta.credits === "number" ? m.meta.credits : m.meta.turn_stats?.credits;
  const caughtUp = smooth.text.length >= (m.content || "").length - 16;
  const quietFor = now - Math.max(textAt, toolAt);
  const lastStep = tools.length ? describeStep(tools[tools.length - 1]) : null;
  const showStatus = live && !!body && caughtUp && quietFor >= 1500 && m.status !== "waiting";
  const statusLabel = lastStep && toolAt > textAt ? lastStep.label : t("thinking");
  const showBrowser = !!m.meta.browser || (isLast && online && usedBrowser(tools));
  const drawing = !!drawKey && toolAt >= textAt;
  return (
    <div className={`msg assistant ${cmd ? "is-command" : ""}`}>
      <div className="avatar"><Logo size={28} animated working={live} happy={hop} model={m.meta.model} /></div>
      <div className="msg-body">
        {showThinking && <Thinking text={m.thinking} live={live && !m.content} />}
        <Activity tools={tools} live={live} hasText={!!body} />
        {cmd && (body || live) ? (
          <div className="command-out">
            <div className="command-head"><IconSlash size={14} /> {cmd}{live && <IconSpinner size={13} />}</div>
            {body && <div className={proseCls}><Markdown text={body} live={live} /></div>}
          </div>
        ) : body ? <div className={proseCls}><Markdown text={body} live={live} /></div> :
          live && !tools.length && !showThinking ? (
            <div className="activity live"><div className="step current">
              <span className="step-icon"><IconSpinner size={15} /></span>
              <span className="step-label shimmer">{m.status === "waiting" ? "Waiting for your approval" : t("thinking")}{secsLabel(now - startAt)}</span>
            </div></div>
          ) : null}
        {drawing && <DrawingPanda key={drawKey} startedAt={drawAt} />}
        {showStatus && !drawing && (
          <div className="live-status" role="status">
            <IconSpinner size={13} />
            {lastStep && toolAt > textAt && <span className="step-glyph">{lastStep.icon}</span>}
            <span className="shimmer">{statusLabel}{secsLabel(quietFor)}</span>
          </div>
        )}
        {showBrowser && <BrowserCard latest={isLast} shot={m.meta.browser} />}
        {!live && <MessageFiles messageId={m.id} ids={m.meta.files} />}
        {m.status === "error" && (
          <div className="error-note"><IconAlert size={15} /> {m.meta.error || "Something went wrong."}</div>
        )}
        {!live && m.status === "done" && COMMAND_NOTE[cmd] && <div className="command-divider"><span>{COMMAND_NOTE[cmd]}</span></div>}
        {!live && options.length > 0 && isLast && (
          <div className="options">
            {options.map((o) => <button key={o} className="chip" onClick={() => onOption?.(o)}>{o}</button>)}
          </div>
        )}
        {!live && (
          <div className="msg-actions">
            <button className="tap" onClick={() => { navigator.clipboard.writeText(body); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
              title={t("copy")} aria-label={t("copy")}>{copied ? <IconCheck size={16} /> : <IconCopy size={16} />}</button>
            {onRetry && !cmd && <button className="tap" onClick={() => onRetry(m)} title={t("retry")} aria-label={t("retry")}><IconRetry size={16} /></button>}
            <span className="muted meta-line">
              {m.meta.model && !cmd ? modelName(m.meta.model) : ""}{m.meta.secs ? `${m.meta.model && !cmd ? " · " : ""}${m.meta.secs}s` : ""}
            </span>
            {typeof credits === "number" && credits > 0 && (
              <span className="credit-chip" title={`This reply used ${credits} Kiro credits`}>
                <IconCoins size={12} /> {fmtCredits(credits)}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
});
