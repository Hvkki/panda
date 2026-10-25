import { useEffect, useState } from "react";
import { EFFORTS, MAIN_MODELS, MORE_MODELS, effortLabel, modelName } from "../lib/models";
import { useT } from "../lib/i18n";
import { IconAlert, IconCheck, IconChevron } from "./Icons";
import { Logo, pandaTier } from "./Logo";

type Props = { model: string; effort: string; onChange: (model: string, effort: string) => void };

/** The model chip in the composer. `open`/`onOpenChange` let /model and /effort open the sheet. */
export function ModelPicker({ model, effort, onChange, open, onOpenChange }: Props & { open?: boolean; onOpenChange?: (v: boolean) => void }) {
  const [own, setOwn] = useState(false);
  const isOpen = open ?? own;
  const setOpen = (v: boolean) => { setOwn(v); onOpenChange?.(v); };
  return (
    <>
      <button type="button" className="chip model-chip" onClick={() => setOpen(true)}>
        {modelName(model)} <span className="muted model-effort">{effortLabel(effort)}</span> <IconChevron size={14} />
      </button>
      {isOpen && <ModelSheet model={model} effort={effort} onChange={onChange} onClose={() => setOpen(false)} />}
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
    <button key={id} className={`sheet-row ${model === id ? "selected" : ""}`} onClick={() => { onChange(id, effort); onClose(); }}>
      <div className="sheet-row-main">
        {pandaTier(id) && <Logo size={34} model={id} animated />}
        <div>
          <div className="sheet-title">{name} <span className="credit">{credits}×</span></div>
          {blurb && <div className="sheet-sub">{blurb}</div>}
        </div>
      </div>
      {model === id && <span className="accent"><IconCheck /></span>}
    </button>
  );

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Choose model">
        <div className="sheet-handle" />
        <div className="sheet-label">Model</div>
        {MAIN_MODELS.map((m) => row(m.id, m.name, m.blurb, m.credits))}
        <div className="sheet-sep" />
        <div className="sheet-label">{t("effort")}</div>
        <div className="effort-row">
          {EFFORTS.map((e) => (
            <button key={e.id} className={`effort-pill ${effort === e.id ? "on" : ""}`} onClick={() => { onChange(model, e.id); onClose(); }}
              title={"warn" in e ? e.warn : "note" in e ? e.note : undefined}>
              {e.label}
            </button>
          ))}
        </div>
        {effort === "max" && <p className="sheet-warn"><IconAlert size={13} /> Max effort uses much more credit.</p>}
        <div className="sheet-sep" />
        <button className="sheet-label as-btn" onClick={() => setShowMore((v) => !v)}>
          {t("moreModels")} <span className={`chev ${showMore ? "up" : ""}`}><IconChevron size={14} /></span>
        </button>
        {showMore && MORE_MODELS.map((m) => row(m.id, m.name, m.blurb, m.credits))}
        <p className="sheet-foot muted">Credits are per message relative to Kiro's base rate. Each reply shows what it really used.</p>
      </div>
    </div>
  );
}
