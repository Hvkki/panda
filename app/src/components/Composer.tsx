import { useEffect, useRef, useState } from "react";
import { ModelPicker } from "./ModelSheet";
import { IconMic, IconPlus, IconSend, IconStop, IconX } from "./Icons";
import { uploadFile, type UploadTarget } from "../lib/api";
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
};

// Browser speech recognition (Chrome / Android / Safari). Not in TS's DOM types.
type Rec = { lang: string; continuous: boolean; interimResults: boolean; start(): void; stop(): void;
  onresult: ((e: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onend: (() => void) | null; onerror: (() => void) | null };
const SpeechRec: (new () => Rec) | undefined =
  typeof window !== "undefined" ? ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition) : undefined;

export function Composer({ model, effort, onModel, onSend, onStop, busy, autoFocus, canAttach, target, placeholder }: Props) {
  const [text, setText] = useState("");
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
        <div className="composer-row">
          <input ref={input} type="file" multiple hidden onChange={(e) => addFiles(e.target.files)} />
          <button className="round-btn soft" disabled={!canAttach} onClick={() => input.current?.click()}
            title={canAttach ? "Attach files" : "Start the worker to attach files"} aria-label="Attach files"><IconPlus /></button>
          <textarea ref={area} rows={1} autoFocus={autoFocus} value={text}
            placeholder={busy ? "Add to queue" : placeholder || "Ask anything"}
            onChange={(e) => { setText(e.target.value); grow(); }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && window.innerWidth > 700 && getSettings().enterToSend) { e.preventDefault(); send(); }
            }} />
          {SpeechRec && (
            <button className={`round-btn soft ${listening ? "rec" : ""}`} onClick={toggleMic}
              aria-label={listening ? "Stop dictation" : "Dictate"} title={listening ? "Stop dictation" : "Dictate"}><IconMic /></button>
          )}
          {busy && !hasInput ? (
            <button className="round-btn soft" onClick={onStop} aria-label="Stop" title="Stop"><IconStop size={18} /></button>
          ) : (
            <button className="round-btn send" onClick={send} disabled={!hasInput || uploading}
              aria-label={busy ? "Queue" : "Send"} title={busy ? "Queue" : "Send"}><IconSend size={18} /></button>
          )}
        </div>
      </div>
      {model && effort && onModel && (
        <div className="composer-foot"><ModelPicker model={model} effort={effort} onChange={onModel} /></div>
      )}
    </div>
  );
}
