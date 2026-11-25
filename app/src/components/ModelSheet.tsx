import { useState } from "react";
import { useEscape } from "../lib/dismiss";
import { Backdrop } from "./Panels";
import { EFFORTS, MODELS, effortLabel, modelName } from "../lib/models";
import { IconAlert, IconCheck, IconChevron, IconX } from "./Icons";

type Props = { model: string; effort: string; onChange: (model: string, effort: string) => void };

export function ModelPicker({ model, effort, onChange }: Props) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="chip model-chip" onClick={() => setOpen(true)}>
        {modelName(model)} <span className="muted">{effortLabel(effort)}</span> <IconChevron size={14} />
      </button>
      {open && <ModelSheet model={model} effort={effort} onChange={onChange} onClose={() => setOpen(false)} />}
    </>
  );
}

export function ModelSheet({ model, effort, onChange, onClose }: Props & { onClose: () => void }) {
  useEscape(onClose);
  return (
    <Backdrop className="sheet-backdrop" onClose={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label="Choose model">
        <div className="sheet-head">
          <span className="sheet-label">Model</span>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><IconX size={18} /></button>
        </div>
        {MODELS.map((m) => (
          <button key={m.id} className="sheet-row" onClick={() => { onChange(m.id, effort); onClose(); }}>
            <div>
              <div className="sheet-title">{m.name}</div>
              <div className="sheet-sub">{m.blurb}</div>
            </div>
            {model === m.id && <span className="accent"><IconCheck /></span>}
          </button>
        ))}
        <div className="sheet-sep" />
        <div className="sheet-label">Effort</div>
        {EFFORTS.map((e) => (
          <button key={e.id} className="sheet-row" onClick={() => { onChange(model, e.id); onClose(); }}>
            <div className="sheet-title">
              {e.label}
              {"note" in e && e.note && <span className="tag">{e.note}</span>}
              {"warn" in e && e.warn && <span className="tag warn"><IconAlert size={12} /> {e.warn}</span>}
            </div>
            {effort === e.id && <span className="accent"><IconCheck /></span>}
          </button>
        ))}
      </div>
    </Backdrop>
  );
}
