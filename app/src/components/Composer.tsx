import { useEffect, useMemo, useRef, useState } from "react";
import { useSettings } from "../lib/settings";
import { useT } from "../lib/i18n";
import { ModelPicker } from "./ModelSheet";
import { IconMic, IconPlus, IconSend, IconStop, IconX } from "./Icons";
import { supabase, type ContextUsage } from "../lib/supabase";
import { fileKind, uploadFile, type Attachment } from "../lib/files";
import { ALL_MODELS, EFFORTS } from "../lib/models";
import { buildCatalog, matchCommands, parseCommand, useCrewCommands, type SlashCmd } from "../lib/slash";
import { fmtTokens } from "../lib/usage";

export type SendMeta = { command?: string };

type Props = {
  model: string;
  effort: string;
  onModel: (model: string, effort: string) => void;
  onSend: (text: string, attachments: Attachment[], meta?: SendMeta) => void;
  /** Panda's own commands (/new, /stop, /btw…). Return false when this screen can't run it. */
  onCommand?: (name: string, args: string) => boolean | void;
  onStop?: () => void;
  busy?: boolean;
  autoFocus?: boolean;
  initialText?: string;
  /** The chat's context window use after the last reply (the ring next to the model). */
  context?: ContextUsage | null;
  onTyping?: (typing: boolean) => void;
  online?: boolean;
};

const GROUP_TITLE = { panda: "commandsPanda", crew: "commandsCrew", shortcut: "commandsShortcuts" } as const;

export function Composer({ model, effort, onModel, onSend, onCommand, onStop, busy, autoFocus, initialText = "",
  context, onTyping, online = false }: Props) {
  const { settings } = useSettings();
  const t = useT();
  const [text, setText] = useState(initialText);
  const [listening, setListening] = useState(false);
  const [files, setFiles] = useState<(Attachment & { uploading?: boolean; error?: string })[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState("");
  const [notice, setNotice] = useState("");
  const [ctxOpen, setCtxOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const ref = useRef<HTMLTextAreaElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const recog = useRef<{ stop: () => void } | null>(null);
  const live = useCrewCommands(online);
  const catalog = useMemo(() => buildCatalog(live), [live]);

  const addFiles = async (list: FileList | File[]) => {
    // Copy the files first: the picker's FileList is emptied as soon as its input is reset, and a
    // drop or paste list dies with its event, both before the session read below resolves.
    const picked = Array.from(list).slice(0, 10);
    if (!picked.length) return;
    const { data } = await supabase.auth.getSession();
    const owner = data.session?.user.id;
    if (!owner) { setNotice("Sign in again to attach files."); return; }
    for (const f of picked) {
      const temp = { path: `temp-${f.name}-${Date.now()}`, name: f.name, mime: f.type, size: f.size, uploading: true };
      setFiles((prev) => [...prev, temp]);
      try {
        const row = await uploadFile(owner, f, { kind: "upload" });
        setFiles((prev) => prev.map((x) => x.path === temp.path ? { path: row.path, name: row.name, mime: row.mime, size: row.size, file_id: row.id } : x));
      } catch (e) {
        const why = (e as Error).message || "upload failed";
        setFiles((prev) => prev.map((x) => x.path === temp.path ? { ...x, uploading: false, error: why } : x));
        setNotice(`${f.name} was not attached: ${why}`);
      }
    }
  };

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 240) + "px";
  }, [text]);
  useEffect(() => { if (autoFocus) ref.current?.focus(); }, [autoFocus]);
  useEffect(() => { onTyping?.(text.length > 0); }, [text.length > 0]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!notice) return;
    const id = window.setTimeout(() => setNotice(""), 3200);
    return () => window.clearTimeout(id);
  }, [notice]);

  // "/" menu: open while the first word is being typed.
  const word = /^\/[\w-]*$/.test(text) ? text : "";
  const matches = word && word !== dismissed ? matchCommands(catalog, word) : [];
  const parsed = !word && text.startsWith("/") ? parseCommand(catalog, text) : null;
  const shown = Math.min(active, Math.max(0, matches.length - 1));
  useEffect(() => {
    menuRef.current?.querySelector(`[data-i="${shown}"]`)?.scrollIntoView({ block: "nearest" });
  }, [shown]);

  const ready = files.filter((f) => !f.uploading && !f.error);
  const uploading = files.some((f) => f.uploading);
  const clear = () => { setText(""); setFiles([]); };

  const runClient = (cmd: SlashCmd, args: string) => {
    if (cmd.name === "/model") {
      if (!args) { setPickerOpen(true); clear(); return; }
      const q = args.toLowerCase().replace(/\s+/g, "");
      const m = ALL_MODELS.find((x) => x.id === q || x.name.toLowerCase().replace(/\s+/g, "") === q)
        || ALL_MODELS.find((x) => x.id.includes(q) || x.name.toLowerCase().replace(/\s+/g, "").includes(q));
      if (!m) { setNotice(`No model called “${args}”. Type /model to pick one.`); return; }
      onModel(m.id, effort);
      setNotice(`Model: ${m.name}`);
      clear();
      return;
    }
    if (cmd.name === "/effort") {
      if (!args) { setPickerOpen(true); clear(); return; }
      const q = args.toLowerCase() === "xhigh" ? "extra" : args.toLowerCase();
      const e = EFFORTS.find((x) => x.id === q || x.label.toLowerCase() === q);
      if (!e) { setNotice("Effort can be low, medium, high, extra or max."); return; }
      onModel(model, e.id);
      setNotice(`Effort: ${e.label}`);
      clear();
      return;
    }
    if (cmd.needsArgs && !args) { setText(cmd.name + " "); ref.current?.focus(); return; }
    const handled = onCommand?.(cmd.name, args);
    if (handled === false) { setNotice(cmd.name === "/stop" ? "Nothing is running." : "Open a chat first."); return; }
    clear();
  };

  const sendCrew = (cmd: SlashCmd, args: string) => {
    if (cmd.off) { setNotice(cmd.off); return; }
    if (cmd.needsArgs && !args) { setText(cmd.name + " "); ref.current?.focus(); setNotice(`${cmd.name} ${cmd.args || ""}`.trim()); return; }
    if (busy) { setNotice("Wait for the answer to finish, or use /btw to ask on the side."); return; }
    if (cmd.confirm && !confirm(cmd.confirm)) return;
    onSend(`${cmd.name}${args ? " " + args : ""}`, [], { command: cmd.name });
    clear();
  };

  const pick = (cmd: SlashCmd) => {
    if (cmd.off) { setNotice(cmd.off); return; }
    if (cmd.kind === "template") { setText(cmd.template || ""); ref.current?.focus(); return; }
    if (cmd.kind === "client") return runClient(cmd, "");
    if (cmd.needsArgs || (cmd.args && cmd.args.startsWith("<"))) { setText(cmd.name + " "); ref.current?.focus(); return; }
    sendCrew(cmd, "");
  };

  const send = () => {
    const raw = text.trim();
    const p = raw.startsWith("/") && !ready.length ? parseCommand(catalog, raw) : null;
    if (p) {
      if (p.cmd.kind === "client") return runClient(p.cmd, p.args);
      if (p.cmd.kind === "send") return sendCrew(p.cmd, p.args);
      if (p.cmd.kind === "template" && p.args) {
        onSend((p.cmd.template || "") + p.args, [], undefined);
        clear();
        return;
      }
    }
    const v = raw || (ready.length ? "Please look at the attached file(s)." : "");
    if (busy && v) { setNotice("Panda is still answering. Wait a moment, or ask on the side with /btw."); return; }
    if (!v || uploading) return;
    onSend(v, ready.map(({ path, name, mime, size, file_id }) => ({ path, name, mime, size, file_id })));
    clear();
  };

  // Voice input (phase 1): the browser's speech recognition.
  const SR = (window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown })
    .SpeechRecognition || (window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition;
  const toggleMic = () => {
    if (listening) { recog.current?.stop(); return; }
    if (!SR) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const r: any = new (SR as any)();
    r.lang = { en: "en-US", it: "it-IT", uk: "uk-UA" }[settings.language];
    r.interimResults = true;
    r.continuous = false;
    const base = text ? text.trimEnd() + " " : "";
    r.onresult = (e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => {
      let said = "";
      for (let i = 0; i < e.results.length; i++) said += e.results[i][0].transcript;
      setText(base + said);
    };
    r.onend = () => setListening(false);
    r.start();
    recog.current = r;
    setListening(true);
  };

  // kiro-cli often reports only the percentage (reset: no token counts); the meter still shows it.
  const pct = context && typeof context.pct === "number" && Number.isFinite(context.pct) ? Math.max(0, Math.min(100, context.pct)) : null;
  const tokens = context && !context.reset && context.used_tokens && context.window_tokens;

  return (
    <div className={`composer ${matches.length ? "menu-open" : ""}`}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => { e.preventDefault(); if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); }}
      onPaste={(e) => { if (e.clipboardData.files.length) { e.preventDefault(); addFiles(e.clipboardData.files); } }}>
      {files.length > 0 && (
        <div className="attach-row">
          {files.map((f) => {
            const k = fileKind(f.name, f.mime);
            return (
              <div key={f.path} className={`attach-chip ${f.error ? "bad" : ""}`} title={f.error || f.name}>
                <span className="file-badge" style={{ background: k.color }}>{f.uploading ? "…" : k.label}</span>
                <span className="attach-name">{f.name}</span>
                <button onClick={() => setFiles((p) => p.filter((x) => x.path !== f.path))} aria-label="Remove"><IconX size={13} /></button>
              </div>
            );
          })}
        </div>
      )}
      {matches.length > 0 && (
        <div className="slash-menu" ref={menuRef} role="listbox" aria-label="Commands">
          {matches.map((c, i) => {
            const head = i === 0 || matches[i - 1].group !== c.group;
            return (
              <div key={c.group + c.name}>
                {head && <div className="slash-group">{t(GROUP_TITLE[c.group])}{c.group === "crew" && live?.backend
                  ? <span className="slash-engine">{live.backend === "kas" ? "KAS engine" : "kiro-cli"}</span> : null}</div>}
                <button data-i={i} role="option" aria-selected={i === shown} aria-disabled={!!c.off}
                  className={`slash-item ${i === shown ? "active" : ""} ${c.off ? "off" : ""}`}
                  onMouseEnter={() => setActive(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(c)}>
                  <span className="slash-name">{c.name}</span>
                  {c.args && <span className="slash-args">{c.args}</span>}
                  <span className="slash-desc">{c.off || c.desc}</span>
                </button>
              </div>
            );
          })}
        </div>
      )}
      {parsed && !matches.length && (parsed.cmd.args || parsed.cmd.off) && (
        <div className="slash-hint"><b>{parsed.cmd.name}</b> {parsed.cmd.args} <span className="muted">— {parsed.cmd.off || parsed.cmd.desc}</span></div>
      )}
      <textarea
        ref={ref}
        rows={1}
        value={text}
        placeholder={t("placeholder")}
        onChange={(e) => { setText(e.target.value); setActive(0); if (dismissed && e.target.value !== dismissed) setDismissed(""); }}
        onKeyDown={(e) => {
          if (matches.length) {
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((shown + 1) % matches.length); return; }
            if (e.key === "ArrowUp") { e.preventDefault(); setActive((shown - 1 + matches.length) % matches.length); return; }
            if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); pick(matches[shown]); return; }
            if (e.key === "Escape") { e.preventDefault(); setDismissed(word); return; }
          }
          if (e.key === "Enter" && !e.shiftKey && settings.enterToSend && !("ontouchstart" in window)) {
            e.preventDefault();
            send();
          }
        }}
      />
      <div className="composer-bar">
        <input ref={fileInput} type="file" multiple hidden onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
        <button type="button" className="icon-btn" onClick={() => fileInput.current?.click()} title="Add files" aria-label="Add files"><IconPlus /></button>
        <ModelPicker model={model} effort={effort} onChange={onModel} open={pickerOpen} onOpenChange={setPickerOpen} />
        {pct != null && (
          <div className="ctx">
            <button type="button" className={`ctx-ring ${pct >= 80 ? "hot" : pct >= 60 ? "warm" : ""}`} onClick={() => setCtxOpen((v) => !v)}
              title={`${t("context")} ${Math.round(pct)}%`} aria-label={`${t("context")} ${Math.round(pct)}%`}>
              <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden>
                <circle cx="10" cy="10" r="7.5" fill="none" stroke="currentColor" strokeOpacity=".18" strokeWidth="2.4" />
                <circle cx="10" cy="10" r="7.5" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"
                  strokeDasharray={`${(pct / 100) * 47.1} 47.1`} transform="rotate(-90 10 10)" />
              </svg>
              <span className="ctx-pct">{Math.round(pct)}%</span>
            </button>
            {ctxOpen && (
              <div className="ctx-pop" onMouseLeave={() => setCtxOpen(false)}>
                <div className="ctx-title">{t("context")} · {Math.round(pct)}%</div>
                <div className="muted small">{tokens ? <>{fmtTokens(context?.used_tokens)} of {fmtTokens(context?.window_tokens)} tokens used by this chat. </>
                  : <>About {Math.round(pct)}% of the model's context window is used by this chat. </>}
                  Kiro Crew compacts it automatically when it gets full.</div>
                <button className="btn small-btn" disabled={busy} onClick={() => { setCtxOpen(false); sendCrew(catalog.find((c) => c.name === "/compact")!, ""); }}>
                  Compact now</button>
              </div>
            )}
          </div>
        )}
        <div className="grow" />
        {SR ? (
          <button type="button" className={`icon-btn ${listening ? "rec" : ""}`} onClick={toggleMic} title="Voice input" aria-label="Voice input">
            <IconMic />
          </button>
        ) : null}
        {busy && onStop && !text.trim() ? (
          <button type="button" className="send-btn stop" onClick={onStop} title={t("stopGen")} aria-label={t("stopGen")}><IconStop /></button>
        ) : (
          <button type="button" className="send-btn" onClick={send} disabled={(!text.trim() && !ready.length) || uploading} title="Send" aria-label="Send">
            <IconSend />
          </button>
        )}
      </div>
      {notice && <div className="composer-notice" role="status">{notice}</div>}
    </div>
  );
}
