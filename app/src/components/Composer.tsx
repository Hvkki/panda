import { useEffect, useRef, useState } from "react";
import { ModelSheet } from "./ModelSheet";
import { IconChevron, IconMic, IconPlus, IconSend, IconStop, IconX } from "./Icons";
import { getCommands, uploadFile, type SlashList, type UploadTarget } from "../lib/api";
import { COMMAND_INFO, LOCAL_COMMANDS, effortLabel, modelName } from "../lib/models";
import { getSettings } from "../lib/settings";

type Props = {
  /** When given, a model chip shows under the box (home and project pages; chats pick it in the top bar). */
  model?: string;
  effort?: string;
  onModel?: (model: string, effort: string) => void;
  /** `attached` are paths inside the worker's workspace (already uploaded). */
  onSend: (text: string, attached: string[]) => void;
  onStop?: () => void;
  /** Claude is working: sending queues the message, and the button stops the turn while the box is empty. */
  busy?: boolean;
  autoFocus?: boolean;
  /** Attaching needs the worker to be online. */
  canAttach: boolean;
  /** Where attachments go on the worker (a project's or the Developer folder). */
  target?: UploadTarget;
  placeholder?: string;
  /** Load Claude Code's slash commands from the worker (needs it online); `dev` picks the Developer folder. */
  commands?: { online: boolean; dev?: boolean };
};

// One fetch per folder per page load; the list barely changes during a run.
const cmdCache: Record<string, Promise<SlashList>> = {};
function useCommands(c?: { online: boolean; dev?: boolean }) {
  const [list, setList] = useState<SlashList | null>(null);
  const key = c?.dev ? "dev" : "chat";
  useEffect(() => {
    if (!c?.online) return;
    cmdCache[key] ??= getCommands(!!c.dev).catch((e) => { delete cmdCache[key]; throw e; });
    let live = true;
    cmdCache[key].then((d) => live && setList(d)).catch(() => live && setList({ commands: [], skills: [] }));
    return () => { live = false; };
  }, [c?.online, c?.dev, key]);
  return list;
}

/** Panda's own handling of /model, /effort, /usage and /clear. Returns true when handled. */
function runLocal(name: string, openModel: () => void) {
  if (!LOCAL_COMMANDS.has(name)) return false;
  if (name === "model" || name === "effort") openModel();
  else window.dispatchEvent(new Event(name === "usage" ? "panda:usage" : "panda:new-chat"));
  return true;
}

// Browser speech recognition (Chrome / Android / Safari). Not in TS's DOM types.
type Rec = { lang: string; continuous: boolean; interimResults: boolean; start(): void; stop(): void;
  onresult: ((e: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onend: (() => void) | null; onerror: (() => void) | null };
const SpeechRec: (new () => Rec) | undefined =
  typeof window !== "undefined" ? ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition) : undefined;

export function Composer({ model, effort, onModel, onSend, onStop, busy, autoFocus, canAttach, target, placeholder, commands }: Props) {
  const [text, setText] = useState("");
  const [modelOpen, setModelOpen] = useState(false);
  const slash = useCommands(commands);
  const canModel = !!(model && effort && onModel);
  const openModel = () => canModel && setModelOpen(true);
  // The "/" menu: shown while the box holds just "/word" (no space yet).
  const [hidden, setHidden] = useState("");  // the text for which Escape closed the "/" menu
  const typed = hidden === text ? undefined : /^\/([\w:.-]*)$/.exec(text)?.[1];
  const menu = typed === undefined || !commands ? [] : [
    ...[...LOCAL_COMMANDS].filter((n) => canModel || (n !== "model" && n !== "effort")),
    ...(slash?.commands || []).filter((n) => !LOCAL_COMMANDS.has(n)),
  ].filter((n) => n.toLowerCase().includes(typed.toLowerCase()))
    .sort((a, b) => Number(!b.startsWith(typed)) - Number(!a.startsWith(typed)));
  const pick = (name: string) => {
    if (runLocal(name, openModel)) { setText(""); return; }
    setText(`/${name} `);
    requestAnimationFrame(() => area.current?.focus());
  };
  const [files, setFiles] = useState<{ name: string; path?: string; error?: string }[]>([]);
  const [listening, setListening] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const rec = useRef<Rec | null>(null);
  useEffect(() => () => rec.current?.stop(), []);

  const grow = () => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  };

  const addFiles = async (list: FileList | null) => {
    for (const f of Array.from(list || []).slice(0, 10)) {
      setFiles((p) => [...p, { name: f.name }]);
      try {
        const path = await uploadFile(f, target);
        setFiles((p) => p.map((x) => (x.name === f.name && !x.path ? { ...x, path } : x)));
      } catch (e) {
        setFiles((p) => p.map((x) => (x.name === f.name && !x.path ? { ...x, error: (e as Error).message } : x)));
      }
    }
    if (input.current) input.current.value = "";
  };

  const uploading = files.some((f) => !f.path && !f.error);
  const hasInput = !!text.trim() || files.some((f) => f.path);
  const send = () => {
    const attached = files.flatMap((f) => (f.path ? [f.path] : []));
    if ((!text.trim() && !attached.length) || uploading) return;
    const local = /^\/([\w-]+)\s*$/.exec(text.trim())?.[1];
    if (local && !attached.length && runLocal(local, openModel)) { setText(""); return; }
    rec.current?.stop();
    onSend(text.trim(), attached);
    setText("");
    setFiles([]);
    if (area.current) area.current.style.height = "auto";
  };

  const toggleMic = () => {
    if (!SpeechRec) return;
    if (listening) { rec.current?.stop(); return; }
    const r = new SpeechRec();
    r.lang = navigator.language || "en-US";
    r.continuous = true;
    r.interimResults = false;
    const base = text ? text.replace(/\s*$/, " ") : "";
    let said = "";
    r.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) said += e.results[i][0].transcript;
      setText(base + said.trim());
      requestAnimationFrame(grow);
    };
    r.onend = r.onerror = () => { setListening(false); rec.current = null; };
    rec.current = r;
    try { r.start(); setListening(true); } catch { setListening(false); }
  };

  return (
    <div className="composer-wrap">
      {menu.length > 0 && (
        <div className="slash-menu" role="listbox" aria-label="Commands">
          {menu.map((n) => (
            <button key={n} className="slash-row" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(n)}>
              <span className="slash-name">/{n}</span>
              <span className="slash-desc muted">{COMMAND_INFO[n] || (slash?.skills.includes(n) ? "Skill" : "Command")}</span>
            </button>
          ))}
        </div>
      )}
      {commands && typed !== undefined && !slash && <div className="slash-menu muted small slash-wait">{commands.online ? "Loading commands…" : "Start the worker to see Claude Code's commands"}</div>}
      <div className="composer">
        {files.length > 0 && (
          <div className="attach-row">
            {files.map((f, i) => (
              <div key={i} className={`attach-chip ${f.error ? "bad" : ""}`}>
                <span className="attach-name">{f.name}{!f.path && !f.error ? " …" : f.error ? " (failed)" : ""}</span>
                <button className="tap" onClick={() => setFiles((p) => p.filter((_, j) => j !== i))} aria-label="Remove"><IconX size={14} /></button>
              </div>
            ))}
          </div>
        )}
        <textarea ref={area} rows={1} autoFocus={autoFocus} value={text}
          placeholder={busy ? "Queue a follow-up…" : placeholder || (commands ? "Message Claude Code  ·  / for commands" : "Message Claude Code")}
          onChange={(e) => { setText(e.target.value); grow(); }}
          onKeyDown={(e) => {
            if (e.key === "Escape" && (menu.length || typed !== undefined)) { e.preventDefault(); e.stopPropagation(); setHidden(text); return; }
            if ((e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) && menu.length && typed !== menu[0]) { e.preventDefault(); pick(menu[0]); return; }
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && window.innerWidth > 700 && getSettings().enterToSend) { e.preventDefault(); send(); }
          }} />
        <div className="composer-row">
          <input ref={input} type="file" multiple hidden onChange={(e) => addFiles(e.target.files)} />
          <button className="c-btn" disabled={!canAttach} onClick={() => input.current?.click()}
            title={canAttach ? "Attach files" : "Start the worker to attach files"} aria-label="Attach files"><IconPlus size={18} /></button>
          {canModel && (
            <button type="button" className="c-chip" onClick={openModel} title="Model and effort">
              {modelName(model!)} <span className="c-dim">{effortLabel(effort!)}</span> <IconChevron size={13} />
            </button>
          )}
          {commands && <button type="button" className="c-chip c-dim hide-narrow" onClick={() => { setText("/"); area.current?.focus(); }}>/ commands</button>}
          <span className="grow" />
          {SpeechRec && (
            <button className={`c-btn ${listening ? "rec" : ""}`} onClick={toggleMic}
              aria-label={listening ? "Stop dictation" : "Dictate"} title={listening ? "Stop dictation" : "Dictate"}><IconMic size={18} /></button>
          )}
          {busy && <button className="c-btn stop" onClick={onStop} aria-label="Stop" title="Stop (Esc)"><IconStop size={16} /></button>}
          {(!busy || hasInput) && (
            <button className="c-send" onClick={send} disabled={!hasInput || uploading}
              aria-label={busy ? "Queue" : "Send"} title={busy ? "Queue" : "Send"}><IconSend size={17} /></button>
          )}
        </div>
      </div>
      {modelOpen && canModel && <ModelSheet model={model!} effort={effort!} onChange={onModel!} onClose={() => setModelOpen(false)} />}
    </div>
  );
}
