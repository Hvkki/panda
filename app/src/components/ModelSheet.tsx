import { useCallback, useEffect, useRef, useState } from "react";
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

function ModelSheet({ model, effort, onChange, onClose: done }: Props & { onClose: () => void }) {
  const t = useT();
  // Closing slides the sheet down and fades the backdrop, then unmounts.
  const sheet = useRef<HTMLDivElement>(null);
  const [closing, setClosing] = useState(false);
  const onClose = useCallback(() => {
    if (closing) return;
    setClosing(true);
    const el = sheet.current;
    if (el) { el.style.transition = "transform .26s cubic-bezier(.2,.8,.2,1)"; el.style.transform = "translateY(100%)"; }
    window.setTimeout(done, 240);
  }, [closing, done]);
  // Swipe down to close: from the handle anywhere, or from the list when it is scrolled to the top.
  const drag = useRef<{ y: number; t: number; dy: number; on: boolean } | null>(null);
  const onTouchStart = (e: React.TouchEvent) => {
    const el = sheet.current;
    if (!el || closing) return;
    drag.current = { y: e.touches[0].clientY, t: Date.now(), dy: 0, on: el.scrollTop <= 0 };
    el.style.transition = "none";
  };
  const onTouchMove = (e: React.TouchEvent) => {
    const d = drag.current, el = sheet.current;
    if (!d || !el || !d.on) return;
    d.dy = Math.max(0, e.touches[0].clientY - d.y);
    if (d.dy > 0) el.style.transform = `translateY(${d.dy}px)`;
  };
  const onTouchEnd = () => {
    const d = drag.current, el = sheet.current;
    drag.current = null;
    if (!d || !el) return;
    const fast = d.dy / Math.max(1, Date.now() - d.t) > 0.5;
    if (d.dy > 90 || (fast && d.dy > 30)) return onClose();
    el.style.transition = "transform .22s cubic-bezier(.2,.8,.2,1)";
    el.style.transform = "";
  };
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
    <div className={`sheet-backdrop ${closing ? "closing" : ""}`} onClick={onClose}>
      <div ref={sheet} className="sheet" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Choose model"
        onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd} onTouchCancel={onTouchEnd}>
        <button type="button" className="sheet-handle-btn" onClick={onClose} aria-label="Close"><span className="sheet-handle" /></button>
        <div className="sheet-label">Model</div>
        {MAIN_MODELS.map((m) => row(m.id, m.name, m.blurb, m.credits))}
        <div className="sheet-sep" />
        <div className="sheet-label">{t("effort")}</div>
        <div className="effort-list">
          {EFFORTS.map((e) => (
            <button key={e.id} className={`effort-item ${effort === e.id ? "on" : ""}`} onClick={() => { onChange(model, e.id); onClose(); }}>
              <span className="effort-name">{e.label}</span>
              {"note" in e && <span className="effort-tag">{e.note}</span>}
              {"warn" in e && <span className="effort-tag warn"><IconAlert size={12} /> 8× or more usage</span>}
              {effort === e.id && <span className="accent effort-check"><IconCheck /></span>}
            </button>
          ))}
        </div>
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
