import { useEffect, useState } from "react";
import { EFFORTS, MAIN_MODELS, MORE_MODELS, effortLabel, modelName } from "../lib/models";
import { useT } from "../lib/i18n";
import { IconAlert, IconCheck, IconChevron } from "./Icons";

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

function ModelSheet({ model, effort, onChange, onClose }: Props & { onClose: () => void }) {
  const t = useT();
  const [showMore, setShowMore] = useState(MORE_MODELS.some((m) => m.id === model));
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const row = (id: string, name: string, blurb: string, credits: number) => (
    <button key={id} className="sheet-row" onClick={() => { onChange(id, effort); onClose(); }}>
      <div>
        <div className="sheet-title">{name} <span className="credit">{credits}×</span></div>
        {blurb && <div className="sheet-sub">{blurb}</div>}
      </div>
      {model === id && <span className="accent"><IconCheck /></span>}
    </button>
  );

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Choose model">
        <div className="sheet-handle" />
        {MAIN_MODELS.map((m) => row(m.id, m.name, m.blurb, m.credits))}
        <div className="sheet-sep" />
        <div className="sheet-label">{t("effort")}</div>
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
        <div className="sheet-sep" />
        <button className="sheet-label as-btn" onClick={() => setShowMore((v) => !v)}>
          {t("moreModels")} <IconChevron size={14} />
        </button>
        {showMore && MORE_MODELS.map((m) => row(m.id, m.name, m.blurb, m.credits))}
        <p className="sheet-foot muted">Credits are per message relative to Kiro's base rate.</p>
      </div>
    </div>
  );
}
