import { useRef, useState } from "react";
import { ModelPicker } from "./ModelSheet";
import { IconPlus, IconSend, IconStop, IconX } from "./Icons";
import { uploadFile, type UploadTarget } from "../lib/api";
import { getSettings } from "../lib/settings";

type Props = {
  model: string;
  effort: string;
  onModel: (model: string, effort: string) => void;
  /** `attached` are paths inside the worker's workspace (already uploaded). */
  onSend: (text: string, attached: string[]) => void;
  onStop?: () => void;
  busy?: boolean;
  autoFocus?: boolean;
  /** Attaching needs the worker to be online. */
  canAttach: boolean;
  /** Where attachments go on the worker (a project's or the Developer folder). */
  target?: UploadTarget;
  placeholder?: string;
};

export function Composer({ model, effort, onModel, onSend, onStop, busy, autoFocus, canAttach, target, placeholder }: Props) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<{ name: string; path?: string; error?: string }[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const area = useRef<HTMLTextAreaElement>(null);

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

  const send = () => {
    const attached = files.flatMap((f) => (f.path ? [f.path] : []));
    if ((!text.trim() && !attached.length) || busy || files.some((f) => !f.path && !f.error)) return;
    onSend(text.trim(), attached);
    setText("");
    setFiles([]);
    if (area.current) area.current.style.height = "auto";
  };

  return (
    <div className="composer">
      {files.length > 0 && (
        <div className="attach-row">
          {files.map((f, i) => (
            <div key={i} className="attach-chip">
              <span className="attach-name">{f.name}{!f.path && !f.error ? " …" : f.error ? " (failed)" : ""}</span>
              <button className="tap" onClick={() => setFiles((p) => p.filter((_, j) => j !== i))} aria-label="Remove"><IconX size={14} /></button>
            </div>
          ))}
        </div>
      )}
      <textarea ref={area} rows={1} autoFocus={autoFocus} value={text} placeholder={placeholder || "Ask anything"}
        onChange={(e) => {
          setText(e.target.value);
          e.target.style.height = "auto";
          e.target.style.height = `${Math.min(e.target.scrollHeight, 240)}px`;
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && window.innerWidth > 700 && getSettings().enterToSend) { e.preventDefault(); send(); }
        }} />
      <div className="composer-bar">
        <input ref={input} type="file" multiple hidden onChange={(e) => addFiles(e.target.files)} />
        <button className="icon-btn" disabled={!canAttach} onClick={() => input.current?.click()}
          title={canAttach ? "Attach files" : "Start the worker to attach files"} aria-label="Attach files"><IconPlus /></button>
        <span style={{ flex: 1 }} />
        <ModelPicker model={model} effort={effort} onChange={onModel} />
        {busy ? (
          <button className="send-btn" onClick={onStop} aria-label="Stop"><IconStop size={18} /></button>
        ) : (
          <button className="send-btn" onClick={send} disabled={!text.trim() && !files.length} aria-label="Send"><IconSend size={18} /></button>
        )}
      </div>
    </div>
  );
}
