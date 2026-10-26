import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { hostOf, isAlive, useBrowserSnapshot, useBrowserViewer, type BrowserLink } from "../lib/browser";
import { useT } from "../lib/i18n";
import { AgentCursor, TapRipples } from "./AgentCursor";
import {
  IconArrowLeft, IconArrowRight, IconBackspace, IconCursor, IconEnter, IconGlobe, IconHand, IconKeyboard, IconList,
  IconLock, IconPlus, IconRetry, IconSpinner, IconX,
} from "./Icons";

/** A touch screen with a text field focused: its on-screen keyboard is (about to be) up. */
const keyboardUp = () => {
  const a = document.activeElement;
  return "ontouchstart" in window && !!a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA");
};

const SPECIAL = ["Enter", "Backspace", "Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Delete", "Home", "End", "PageUp", "PageDown"];

/**
 * Panda's browser, full size. Panda drives and narrates each step (a pointer shows where it
 * clicks); you watch. "Take over" lets you tap, scroll and type yourself (logins, captchas);
 * "Give back to Panda" hands it back. On wide screens it docks beside the chat.
 */
export function BrowserPanel({ link, online, onClose, docked }: { link: BrowserLink; online: boolean; onClose: () => void; docked: boolean }) {
  const t = useT();
  const snap = useBrowserSnapshot(link);
  const { status, frame, dims, actions, thumbs } = snap;
  const [address, setAddress] = useState("");
  const [tabsOpen, setTabsOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [replay, setReplay] = useState<string | null>(null);
  const [taps, setTaps] = useState<{ id: number; x: number; y: number }[]>([]);
  const [size, setSize] = useState<{ vw: number; vh: number } | undefined>(undefined);
  const [typed, setTyped] = useState("");
  // The phone keyboard opens only when you ask for it (the ⌨ button), never by itself.
  const [kbdOpen, setKbdOpen] = useState(false);
  const [full, setFull] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {  // leaving system fullscreen (Back, Esc) also leaves Panda's fullscreen
    const off = () => { if (!document.fullscreenElement) setFull(false); };
    document.addEventListener("fullscreenchange", off);
    return () => document.removeEventListener("fullscreenchange", off);
  }, []);
  const toggleFull = () => {
    const on = !full;
    setFull(on);
    if (on) panel.current?.requestFullscreen?.().catch(() => {});  // iPhone has no API: the CSS still fills the screen
    else if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  };
  const typedRef = useRef("");
  const editing = useRef(false);
  const freeze = useRef(false);                // keyboard up: keep the page size, don't re-lay it out
  const view = useRef<HTMLDivElement>(null);   // the rendered page (exact size of the picture)
  const stage = useRef<HTMLDivElement>(null);  // the free area it is fitted into
  const kbd = useRef<HTMLInputElement>(null);
  const touch = useRef<{ x: number; y: number; t: number; moved: boolean } | null>(null);
  const wheel = useRef({ dx: 0, dy: 0, x: 0.5, y: 0.5, timer: 0 });

  // Tell the engine how big we are (phones get a tall page that fills the screen).
  useLayoutEffect(() => {
    const el = stage.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      const next = { vw: Math.round(r.width / 20) * 20, vh: Math.round(r.height / 20) * 20 };
      setSize((s) => {
        if (s && s.vw === next.vw && s.vh === next.vh) return s;
        // An on-screen keyboard only makes the free area shorter: the page keeps its shape.
        if (s && s.vw === next.vw && (freeze.current || keyboardUp())) return s;
        return next;
      });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useBrowserViewer(link, !!size, size);  // wait for the measured size: one hello, one page resize

  useEffect(() => { if (status && !editing.current) setAddress(status.url === "about:blank" ? "" : status.url); }, [status?.url]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { typedRef.current = ""; setTyped(""); }, [status?.focus?.label, status?.control]);
  useEffect(() => { if (status?.control === "agent") setKbdOpen(false); }, [status?.control]);
  useEffect(() => { freeze.current = kbdOpen; if (kbdOpen) kbd.current?.focus(); }, [kbdOpen]);
  // Keyboard up: the page shows at full width and scrolls so the field you type into stays visible.
  const focusY = status?.focus?.y;
  useEffect(() => {
    const el = stage.current, v = view.current;
    if (!kbdOpen || !el || !v) return;
    const id = window.setTimeout(() => {
      const y = (focusY ?? 0.3) * v.offsetHeight;
      el.scrollTo({ top: Math.max(0, y - el.clientHeight * 0.4), behavior: "smooth" });
    }, 250);  // after the keyboard finished opening
    return () => window.clearTimeout(id);
  }, [kbdOpen, focusY]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && !(e.target instanceof HTMLInputElement)) onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const send = link.send;
  const control = status?.control ?? "agent";
  const mine = control !== "agent";           // the user may interact
  const alive = isAlive(snap);
  const lastAction = actions[actions.length - 1];
  const rel = (clientX: number, clientY: number) => {
    const r = view.current!.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (clientY - r.top) / r.height)) };
  };
  const tap = (clientX: number, clientY: number) => {
    const p = rel(clientX, clientY);
    send({ t: "click", ...p });
    const id = Date.now() + Math.random();
    setTaps((list) => [...list.slice(-4), { id, ...p }]);
    window.setTimeout(() => setTaps((list) => list.filter((x) => x.id !== id)), 650);
  };
  const scrollBy = (x: number, y: number, dx: number, dy: number) => {   // coalesced, ~20 per second at most
    const w = wheel.current;
    w.dx += dx; w.dy += dy; w.x = x; w.y = y;
    if (w.timer) return;
    w.timer = window.setTimeout(() => {
      send({ t: "scroll", x: w.x, y: w.y, dx: Math.round(w.dx), dy: Math.round(w.dy) });
      w.dx = 0; w.dy = 0; w.timer = 0;
    }, 50);
  };

  const key = (e: React.KeyboardEvent) => {
    if (!mine || e.target instanceof HTMLInputElement) return;
    if (SPECIAL.includes(e.key)) { e.preventDefault(); send({ t: "key", key: e.key }); }
    else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) { e.preventDefault(); send({ t: "text", text: e.key }); }
    else if ((e.ctrlKey || e.metaKey) && e.key.length === 1) { e.preventDefault(); send({ t: "key", key: `Control+${e.key}` }); }
  };

  // Phone keyboard: everything typed in the bar goes straight into the focused field on the page.
  const onTyped = (v: string) => {
    const prev = typedRef.current;
    let i = 0;
    while (i < prev.length && i < v.length && prev[i] === v[i]) i++;
    for (let k = 0; k < prev.length - i; k++) send({ t: "key", key: "Backspace" });
    if (v.slice(i)) send({ t: "text", text: v.slice(i) });
    typedRef.current = v;
    setTyped(v);
  };

  const pill = !online ? "off" : control === "needs_user" ? "needs" : control === "user" ? "you" : status?.busy ? "busy" : "idle";
  const caption = control === "needs_user" ? (status?.message || "Panda needs your help.")
    : control === "user" ? (status?.message || "You're in control. Tap the page to click, swipe to scroll.")
    : status?.action || (status?.busy ? "Working…" : lastAction && Date.now() - lastAction.t < 20000 ? lastAction.c : "Panda's browser — watching live.");
  const focus = mine ? status?.focus : null;
  const secure = (status?.url || "").startsWith("https://");
  // Tabs work without Take over whenever Panda isn't using the browser.
  const tabsOk = online && (mine || !status?.busy);
  const tabs = status?.tabs?.length ? status.tabs : [{ i: 0, u: status?.url || "", t: status?.title || "", active: true }];
  const tabName = (tb: { u: string; t?: string }) => tb.t || hostOf(tb.u) || "New tab";
  const openTabs = () => { if (!tabsOpen) send({ t: "tabs" }); setTabsOpen(!tabsOpen); };
  const newTab = () => { send({ t: "newtab" }); setTabsOpen(false); };

  return (
    <div ref={panel} className={`browser-panel ${docked ? "docked" : ""} ${full ? "full" : ""} ctl-${control}`} role="dialog" aria-label="Panda's browser">
      <div className="bp-head">
        <button className="icon-btn" onClick={onClose} aria-label="Close"><IconX /></button>
        <div className="bp-titles">
          <div className="bp-title">{status?.title || hostOf(status?.url) || "Panda's browser"}</div>
          <div className="bp-sub muted">{hostOf(status?.url) || "Panda's browser"}</div>
        </div>
        <span className={`bp-pill ${pill}`}>{pill === "busy" && <i className="dot" />}
          {{ off: t("offline"), needs: t("needsYou"), you: "You", busy: t("live"), idle: "Idle" }[pill]}</span>
        <button className={`icon-btn ${logOpen ? "on" : ""}`} onClick={() => setLogOpen((v) => !v)} aria-label="Steps" title="Steps"><IconList /></button>
        <button className={`icon-btn ${full ? "on" : ""}`} onClick={toggleFull} aria-label={full ? "Exit full screen" : "Full screen"} title="Full screen">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            {full ? <path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" /> : <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />}
          </svg>
        </button>
      </div>

      <div className="bp-strip" role="tablist" aria-label="Tabs">
        {tabs.map((tb) => (
          <div key={tb.id ?? tb.i} className={`bp-strip-tab ${tb.active ? "active" : ""}`} role="tab" aria-selected={tb.active}>
            <button className="bp-strip-main" disabled={!tabsOk || tb.active} onClick={() => send({ t: "tab", i: tb.i })} title={tb.u}>
              <IconGlobe size={13} /><span>{tabName(tb)}</span></button>
            <button className="bp-strip-x" disabled={!tabsOk} onClick={() => send({ t: "closetab", i: tb.i })} aria-label={`Close ${tabName(tb)}`}><IconX size={13} /></button>
          </div>
        ))}
        <button className="icon-btn bp-strip-new" disabled={!tabsOk} onClick={newTab} aria-label="New tab" title="New tab"><IconPlus size={16} /></button>
      </div>

      <form className="bp-url" onSubmit={(e) => { e.preventDefault(); if (mine && address.trim()) send({ t: "nav", url: address.trim() }); editing.current = false; (document.activeElement as HTMLElement)?.blur(); }}>
        <button type="button" className="icon-btn" disabled={!mine} onClick={() => send({ t: "back" })} aria-label="Back"><IconArrowLeft size={18} /></button>
        <button type="button" className="icon-btn" disabled={!mine} onClick={() => send({ t: "forward" })} aria-label="Forward"><IconArrowRight size={18} /></button>
        <button type="button" className="icon-btn" disabled={!mine} onClick={() => send({ t: "reload" })} aria-label="Reload"><IconRetry size={17} /></button>
        <div className={`bp-address ${status?.loading ? "loading" : ""}`}>
          <span className="bp-lock">{secure ? <IconLock size={13} /> : <IconGlobe size={13} />}</span>
          <input value={address} disabled={!mine} placeholder={mine ? "Search or type an address" : "Take over to type an address"}
            inputMode="url" autoCapitalize="off" autoCorrect="off" spellCheck={false}
            onFocus={(e) => { editing.current = true; e.target.select(); }} onBlur={() => (editing.current = false)} onChange={(e) => setAddress(e.target.value)} />
          <span className="bp-progress" />
        </div>
        <button type="button" className={`icon-btn bp-tabs ${tabsOpen ? "on" : ""}`} onClick={openTabs} aria-label={`${tabs.length} tabs`} title="Tabs">
          <span className="bp-tabs-count">{tabs.length > 99 ? ":D" : tabs.length}</span></button>
      </form>

      <div className="bp-body">
        <div className={`bp-stage ${kbdOpen && mine ? "kb" : ""}`} ref={stage}>
          <div className={`bp-view ${mine ? "mine" : ""}`} ref={view} tabIndex={0} onKeyDown={key}
            style={{ ["--r" as string]: dims.w / dims.h }}
            onClick={(e) => { if (mine && !("ontouchstart" in window)) { tap(e.clientX, e.clientY); view.current?.focus(); } }}
            onWheel={(e) => { if (mine) { const p = rel(e.clientX, e.clientY); scrollBy(p.x, p.y, e.deltaX, e.deltaY); } }}
            onTouchStart={(e) => { const tt = e.touches[0]; touch.current = { x: tt.clientX, y: tt.clientY, t: Date.now(), moved: false }; }}
            onTouchMove={(e) => {
              const tt = e.touches[0], s = touch.current;
              if (!mine || !s) return;
              const dy = s.y - tt.clientY, dx = s.x - tt.clientX;
              if (s.moved || Math.abs(dy) + Math.abs(dx) > 8) {
                s.moved = true;
                const p = rel(tt.clientX, tt.clientY);
                scrollBy(p.x, p.y, dx * 1.6, dy * 1.6);
                s.x = tt.clientX; s.y = tt.clientY;
              }
            }}
            onTouchEnd={(e) => {
              const s = touch.current; touch.current = null;
              if (mine && s && !s.moved && Date.now() - s.t < 700) { const tt = e.changedTouches[0]; tap(tt.clientX, tt.clientY); e.preventDefault(); }
            }}>
            {frame ? <img src={frame} alt="Live browser view" draggable={false} /> :
              <div className="bp-empty">{!online ? "Panda is offline. Start it to use the browser." :
                alive ? <><IconSpinner size={18} /> Waiting for the first frame…</> : <><IconSpinner size={18} /> Connecting to Panda's browser…</>}</div>}
            {!mine && <AgentCursor action={lastAction} />}
            <TapRipples taps={taps} />
            {control === "agent" && status?.busy && frame && <div className="bp-working" aria-hidden />}
          </div>
        </div>
        {logOpen && (
          <aside className="bp-log">
            <div className="bp-log-head">Steps <span className="muted small">{actions.length}</span></div>
            {actions.length === 0 && <p className="muted small">Panda's steps in the browser show up here.</p>}
            {[...actions].reverse().map((a) => (
              <button key={a.t} className="bp-step" onClick={() => a.shot && setReplay(a.shot)} disabled={!a.shot}>
                {a.shot ? <img src={a.shot} alt="" /> : <span className="bp-step-blank"><IconCursor size={14} /></span>}
                <span className="bp-step-text"><span>{a.c}</span>
                  <span className="muted small">{new Date(a.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span></span>
              </button>
            ))}
          </aside>
        )}
        {tabsOpen && (
          <div className="bp-tabgrid" role="dialog" aria-label="Tabs">
            <div className="bp-tabgrid-head">
              <span>{tabs.length} {tabs.length === 1 ? "tab" : "tabs"}</span>
              {!tabsOk && <span className="muted small">{online ? "Wait until Panda is done, or take over" : "Start Panda to use tabs"}</span>}
              <button className="btn small" disabled={!tabsOk} onClick={newTab}><IconPlus size={15} /> New tab</button>
              <button className="icon-btn" onClick={() => setTabsOpen(false)} aria-label="Close tabs"><IconX size={18} /></button>
            </div>
            <div className="bp-tabgrid-list">
              {tabs.map((tb) => {
                const pic = tb.active ? frame : thumbs[String(tb.id)];
                return (
                  <div key={tb.id ?? tb.i} className={`bp-tabcard ${tb.active ? "active" : ""}`}>
                    <button className="bp-tabcard-main" disabled={!tabsOk && !tb.active}
                      onClick={() => { if (!tb.active) send({ t: "tab", i: tb.i }); setTabsOpen(false); }}>
                      <span className="bp-tabcard-top"><IconGlobe size={12} /><span>{tabName(tb)}</span></span>
                      <span className="bp-tabcard-pic">{pic ? <img src={pic} alt="" /> : <IconGlobe size={26} />}</span>
                      <span className="bp-tabcard-host muted">{hostOf(tb.u) || "New tab"}</span>
                    </button>
                    <button className="bp-tabcard-x" disabled={!tabsOk} onClick={() => send({ t: "closetab", i: tb.i })} aria-label={`Close ${tabName(tb)}`}><IconX size={14} /></button>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {replay && (
          <button className="bp-replay" onClick={() => setReplay(null)} aria-label="Close the step screen">
            <img src={replay} alt="Screen after this step" /><span className="chip">Back to live</span>
          </button>
        )}
      </div>

      <div className={`bp-caption ${control}`}>
        <span className="bp-caption-icon">{status?.busy && control === "agent" ? <IconSpinner size={15} /> : control === "agent" ? <IconCursor size={15} /> : <IconHand size={15} />}</span>
        <span className="bp-caption-text">{caption}</span>
        {mine && focus && !kbdOpen && (
          <button className="chip bp-type-hint" onClick={() => setKbdOpen(true)}>
            <IconKeyboard size={15} /> Type into {focus.label ? `“${focus.label.slice(0, 22)}”` : "the field"}</button>
        )}
      </div>

      {mine && kbdOpen && (
        <form className="bp-typebar" onSubmit={(e) => { e.preventDefault(); send({ t: "key", key: "Enter" }); typedRef.current = ""; setTyped(""); }}>
          <input ref={kbd} value={typed} type={focus?.secret ? "password" : "text"} onChange={(e) => onTyped(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Backspace" && !typedRef.current) { e.preventDefault(); send({ t: "key", key: "Backspace" }); } }}
            placeholder={focus ? `Typing into ${focus.label || "the selected field"}…` : "Tap a field on the page first"}
            autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false} enterKeyHint="go" />
          <button type="button" className="chip" onMouseDown={(e) => e.preventDefault()} onClick={() => send({ t: "key", key: "Tab" })}>Tab</button>
          <button type="button" className="chip icon-chip" onMouseDown={(e) => e.preventDefault()} onClick={() => send({ t: "key", key: "Backspace" })} aria-label="Delete"><IconBackspace size={16} /></button>
          <button type="submit" className="chip icon-chip" onMouseDown={(e) => e.preventDefault()} aria-label="Enter"><IconEnter size={16} /></button>
          <button type="button" className="chip icon-chip" onClick={() => { kbd.current?.blur(); setKbdOpen(false); }} aria-label="Hide the keyboard"><IconX size={16} /></button>
        </form>
      )}

      <div className="bp-actions">
        {control === "agent" ? (
          <>
            <span className="muted small bp-actions-note">{online ? "Panda drives. You can take over any time." : "Start Panda to use its browser."}</span>
            <button className="btn" disabled={!online} onClick={() => send({ t: "takeover" })}><IconHand size={16} /> {t("takeOver")}</button>
          </>
        ) : control === "needs_user" ? (
          <>
            <button className="btn primary grow" onClick={() => send({ t: "takeover" })}><IconHand size={16} /> {t("takeOver")}</button>
            <button className="btn" onClick={() => send({ t: "done" })}>I'm done</button>
          </>
        ) : (
          <>
            <button className={`btn bp-kbd-btn ${kbdOpen ? "on" : ""} ${focus && !kbdOpen ? "hint" : ""}`} aria-pressed={kbdOpen}
              onClick={() => setKbdOpen((v) => !v)} aria-label={kbdOpen ? "Hide the keyboard" : "Keyboard"} title="Keyboard">
              <IconKeyboard size={18} /></button>
            <button className="btn primary grow" onClick={() => send({ t: "done" })}>{t("giveBack")}</button>
          </>
        )}
      </div>
    </div>
  );
}
