import { useEffect, useRef, useState } from "react";
import type { BrowserLink } from "../lib/browser";
import { IconChevron, IconRetry, IconX } from "./Icons";

/**
 * Live view of Panda's browser. Panda drives; you watch. Tap "Take over" to click, type
 * and scroll yourself (logins, captchas), then "Done" to hand it back.
 */
export function BrowserPanel({ link, online, onClose }: { link: BrowserLink; online: boolean; onClose: () => void }) {
  const { status, frame, dims, lastSeen, send, setViewing } = link;
  const [address, setAddress] = useState("");
  const [typed, setTyped] = useState("");
  const [tabsOpen, setTabsOpen] = useState(false);
  const editing = useRef(false);
  const view = useRef<HTMLDivElement>(null);   // the rendered page (exact size of the picture)
  const stage = useRef<HTMLDivElement>(null);  // the free area it is fitted into
  const touch = useRef<{ x: number; y: number; moved: boolean } | null>(null);

  useEffect(() => {
    const r = stage.current?.getBoundingClientRect();
    setViewing(true, { vw: r?.width || window.innerWidth, vh: r?.height || window.innerHeight * 0.6 });
    return () => setViewing(false);
  }, [setViewing]);
  useEffect(() => { if (status && !editing.current) setAddress(status.url === "about:blank" ? "" : status.url); }, [status?.url]); // eslint-disable-line react-hooks/exhaustive-deps

  const control = status?.control ?? "agent";
  const mine = control !== "agent";           // user may interact
  const alive = Date.now() - lastSeen < 25000;
  const rel = (clientX: number, clientY: number) => {
    const r = view.current!.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (clientY - r.top) / r.height)) };
  };

  const key = (e: React.KeyboardEvent) => {
    if (!mine || e.target instanceof HTMLInputElement) return;
    const special = ["Enter", "Backspace", "Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Delete", "Home", "End", "PageUp", "PageDown"];
    if (special.includes(e.key)) { e.preventDefault(); send({ t: "key", key: e.key }); }
    else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) { e.preventDefault(); send({ t: "text", text: e.key }); }
    else if ((e.ctrlKey || e.metaKey) && e.key.length === 1) { e.preventDefault(); send({ t: "key", key: `Control+${e.key}` }); }
  };

  const badge = control === "needs_user" ? "needs" : status?.busy ? "busy" : control === "user" ? "you" : "idle";
  const banner =
    control === "needs_user" ? (status?.message || "Panda needs your help.") :
    control === "user" ? "You're in control. Press Done to hand it back to Panda." :
    status?.busy ? "Panda is browsing…" : "Panda's browser — watching live.";

  return (
    <div className="browser-panel" role="dialog" aria-label="Panda's browser">
      <div className="bp-head">
        <button className="icon-btn" onClick={onClose} aria-label="Close"><IconX /></button>
        <div className="bp-title">{status?.title || "Browser"}</div>
        <span className={`bp-badge ${badge}`}>{{ needs: "Needs you", busy: "Working", you: "You", idle: "Idle" }[badge]}</span>
      </div>

      <div className={`bp-banner ${control}`}>
        <span>{banner}</span>
        {control === "agent" && online && <button className="btn small-btn" onClick={() => send({ t: "takeover" })}>Take over</button>}
        {control !== "agent" && <button className="btn primary small-btn" onClick={() => send({ t: "done" })}>Done</button>}
      </div>

      <form className="bp-url" onSubmit={(e) => { e.preventDefault(); if (mine && address.trim()) send({ t: "nav", url: address.trim() }); editing.current = false; }}>
        <button type="button" className="icon-btn" disabled={!mine} onClick={() => send({ t: "back" })} aria-label="Back"><span style={{ transform: "rotate(90deg)", display: "grid" }}><IconChevron size={18} /></span></button>
        <button type="button" className="icon-btn" disabled={!mine} onClick={() => send({ t: "reload" })} aria-label="Reload"><IconRetry size={17} /></button>
        <input value={address} disabled={!mine} placeholder={mine ? "Search or type an address" : "Take over to navigate"}
          onFocus={() => (editing.current = true)} onBlur={() => (editing.current = false)} onChange={(e) => setAddress(e.target.value)} />
        <button type="button" className="chip bp-tabs" onClick={() => setTabsOpen((v) => !v)}>{status?.tabs.length || 1}</button>
      </form>
      {tabsOpen && (
        <div className="bp-tabmenu">
          {(status?.tabs || []).map((t) => (
            <button key={t.i} className={t.active ? "active" : ""} disabled={!mine} onClick={() => { send({ t: "tab", i: t.i }); setTabsOpen(false); }}>
              {t.u.replace(/^https?:\/\//, "").slice(0, 60) || "New tab"}
            </button>
          ))}
          <button disabled={!mine} onClick={() => { send({ t: "newtab" }); setTabsOpen(false); }}>+ New tab</button>
        </div>
      )}

      <div className="bp-stage" ref={stage}>
        <div className={`bp-view ${mine ? "mine" : ""}`} ref={view} tabIndex={0} onKeyDown={key}
          style={{ ["--r" as string]: dims.w / dims.h }}
          onClick={(e) => { if (mine) { send({ t: "click", ...rel(e.clientX, e.clientY) }); view.current?.focus(); } }}
          onWheel={(e) => { if (mine) send({ t: "scroll", ...rel(e.clientX, e.clientY), dx: e.deltaX, dy: e.deltaY }); }}
          onTouchStart={(e) => { const t = e.touches[0]; touch.current = { x: t.clientX, y: t.clientY, moved: false }; }}
          onTouchMove={(e) => {
            const t = e.touches[0], s = touch.current;
            if (!mine || !s) return;
            const dy = s.y - t.clientY, dx = s.x - t.clientX;
            if (Math.abs(dy) + Math.abs(dx) > 6) {
              s.moved = true;
              send({ t: "scroll", ...rel(t.clientX, t.clientY), dx: dx * 1.6, dy: dy * 1.6 });
              s.x = t.clientX; s.y = t.clientY;
            }
          }}
          onTouchEnd={(e) => {
            const s = touch.current; touch.current = null;
            if (mine && s && !s.moved) { const t = e.changedTouches[0]; send({ t: "click", ...rel(t.clientX, t.clientY) }); e.preventDefault(); }
          }}>
          {frame ? <img src={frame} alt="Live browser view" draggable={false} /> :
            <div className="bp-empty">{online ? (alive ? "Waiting for the first frame…" : "Connecting to Panda's browser…") :
              "Panda is offline. Start it to use the browser."}</div>}
          {control === "agent" && frame && <div className="bp-glow" />}
        </div>
      </div>

      {mine && (
        <form className="bp-type" onSubmit={(e) => { e.preventDefault(); if (typed) send({ t: "text", text: typed }); send({ t: "key", key: "Enter" }); setTyped(""); }}>
          <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Type here, then Enter — goes into the selected field"
            autoComplete="off" autoCapitalize="off" spellCheck={false} />
          <button type="button" className="chip" onClick={() => { if (typed) { send({ t: "text", text: typed }); setTyped(""); } }}>Insert</button>
          <button type="button" className="chip" onClick={() => send({ t: "key", key: "Tab" })}>Tab</button>
          <button type="button" className="chip" onClick={() => send({ t: "key", key: "Backspace" })}>Del</button>
        </form>
      )}
    </div>
  );
}
