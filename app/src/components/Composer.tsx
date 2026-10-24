import { useEffect, useRef, useState } from "react";
import { useSettings } from "../lib/settings";
import { useT } from "../lib/i18n";
import { ModelPicker } from "./ModelSheet";
import { IconMic, IconPlus, IconSend, IconStop, IconX } from "./Icons";
import { supabase } from "../lib/supabase";
import { fileKind, uploadFile, type Attachment } from "../lib/files";

const SHORTCUTS = [
  { cmd: "/post", text: "Write a social post about " },
  { cmd: "/research", text: "Research and summarize, with sources: " },
  { cmd: "/pdf", text: "Create a clean PDF document about " },
  { cmd: "/plan", text: "Make a step-by-step plan for " },
  { cmd: "/remind", text: "Every weekday at 9:00, remind me to " },
  { cmd: "/explain", text: "Explain simply, with an example: " },
];

type Props = {
  model: string;
  effort: string;
  onModel: (model: string, effort: string) => void;
  onSend: (text: string, attachments: Attachment[]) => void;
  onStop?: () => void;
  busy?: boolean;
  autoFocus?: boolean;
  initialText?: string;
};

export function Composer({ model, effort, onModel, onSend, onStop, busy, autoFocus, initialText = "" }: Props) {
  const { settings } = useSettings();
  const t = useT();
  const [text, setText] = useState(initialText);
  const [listening, setListening] = useState(false);
  const [files, setFiles] = useState<(Attachment & { uploading?: boolean; error?: string })[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);

  const addFiles = async (list: FileList | File[]) => {
    const { data } = await supabase.auth.getSession();
    const owner = data.session?.user.id;
    if (!owner) return;
    for (const f of Array.from(list).slice(0, 10)) {
      const temp = { path: `temp-${f.name}-${Date.now()}`, name: f.name, mime: f.type, size: f.size, uploading: true };
      setFiles((prev) => [...prev, temp]);
      try {
        const row = await uploadFile(owner, f, { kind: "upload" });
        setFiles((prev) => prev.map((x) => x.path === temp.path ? { path: row.path, name: row.name, mime: row.mime, size: row.size, file_id: row.id } : x));
      } catch (e) {
        setFiles((prev) => prev.map((x) => x.path === temp.path ? { ...x, uploading: false, error: (e as Error).message } : x));
      }
    }
  };
  const ref = useRef<HTMLTextAreaElement>(null);
  const recog = useRef<{ stop: () => void } | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 240) + "px";
  }, [text]);

  useEffect(() => { if (autoFocus) ref.current?.focus(); }, [autoFocus]);

  const ready = files.filter((f) => !f.uploading && !f.error);
  const uploading = files.some((f) => f.uploading);
  const send = () => {
    const v = text.trim() || (ready.length ? "Please look at the attached file(s)." : "");
    if (!v || busy || uploading) return;
    onSend(v, ready.map(({ path, name, mime, size, file_id }) => ({ path, name, mime, size, file_id })));
    setText("");
    setFiles([]);
  };

  // Voice input (phase 1): the browser's speech recognition. Quality voice arrives in phase 4.
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

  const slash = text.startsWith("/") && !text.includes(" ")
    ? SHORTCUTS.filter((s) => s.cmd.startsWith(text.toLowerCase())) : [];

  return (
    <div className="composer"
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
      {slash.length > 0 && (
        <div className="slash-menu">
          {slash.map((s) => (
            <button key={s.cmd} onClick={() => { setText(s.text); ref.current?.focus(); }}>
              <b>{s.cmd}</b> <span className="muted">{s.text}</span>
            </button>
          ))}
        </div>
      )}
      <textarea
        ref={ref}
        rows={1}
        value={text}
        placeholder={t("placeholder")}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && settings.enterToSend && !("ontouchstart" in window)) {
            e.preventDefault();
            send();
          }
        }}
      />
      <div className="composer-bar">
        <input ref={fileInput} type="file" multiple hidden onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
        <button type="button" className="icon-btn" onClick={() => fileInput.current?.click()} title="Add files"><IconPlus /></button>
        <ModelPicker model={model} effort={effort} onChange={onModel} />
        <div className="grow" />
        {SR ? (
          <button type="button" className={`icon-btn ${listening ? "rec" : ""}`} onClick={toggleMic} title="Voice input">
            <IconMic />
          </button>
        ) : null}
        {busy && onStop ? (
          <button type="button" className="send-btn" onClick={onStop} title={t("stopGen")}><IconStop /></button>
        ) : (
          <button type="button" className="send-btn" onClick={send} disabled={(!text.trim() && !ready.length) || uploading} title="Send">
            <IconSend />
          </button>
        )}
      </div>
    </div>
  );
}
