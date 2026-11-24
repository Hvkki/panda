import { useEffect, useRef, useState } from "react";
import { browserAct, browserShot, browserStatus, type BrowserTab } from "../lib/api";
import { IconArrowLeft, IconChevron, IconGlobe, IconPlus, IconRetry, IconX } from "./Icons";

/**
 * Live view of the worker's Chromium (the one Claude drives through Playwright MCP).
 * Tap to click, type into the bar to send keys. Frames are fetched only while this is open.
 */
export function BrowserPanel({ onClose }: { onClose: () => void }) {
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [tab, setTab] = useState("");
  const [state, setState] = useState("…");
  const [err, setErr] = useState("");
  const [frame, setFrame] = useState<{ url: string; w: number; h: number } | null>(null);
  const [addr, setAddr] = useState("");
  const [text, setText] = useState("");
  const editingAddr = useRef(false);
  const [phone, setPhone] = useState(() => {
    try { const v = localStorage.getItem("panda.browser.phone"); if (v) return v === "1"; } catch { /* blocked */ }
    return window.innerWidth < 700;
  });
  const sized = useRef("");
  const tabRef = useRef("");
  useEffect(() => { tabRef.current = tab; }, [tab]);

  // Tabs and state, every 3 s.
  useEffect(() => {
    let alive = true;
    const load = () => browserStatus().then((s) => {
      if (!alive) return;
      setState(s.state);
      setErr(s.error);
      setTabs(s.tabs);
      const cur = s.tabs.find((t) => t.id === tabRef.current) || s.tabs[0];
      if (cur && cur.id !== tabRef.current) setTab(cur.id);
      if (cur && !editingAddr.current) setAddr(cur.url === "about:blank" ? "" : cur.url);
    }).catch((e) => alive && setErr(e.message));
    load();
    const t = window.setInterval(load, 3000);
    return () => { alive = false; window.clearInterval(t); };
  }, []);

  // Frames, back to back, while visible.
  useEffect(() => {
    if (state !== "ready" || !tab) return;
    let alive = true;
    let timer = 0;
    const next = async () => {
      if (!alive) return;
      if (!document.hidden) {
        try {
          const f = await browserShot(tab);
          if (!alive) { URL.revokeObjectURL(f.url); return; }
          setFrame((old) => { if (old) URL.revokeObjectURL(old.url); return f; });
        } catch { /* a frame can fail while a page loads */ }
      }
      timer = window.setTimeout(next, 700);
    };
    next();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [state, tab]);

  const act = (a: Record<string, unknown>) => browserAct({ tab, ...a }).catch((e) => setErr(e.message));

  // Phone view: a narrow window, so sites show their mobile layout.
  useEffect(() => {
    if (state !== "ready" || !tab) return;
    const want = phone ? "phone" : "desktop";
    if (sized.current === want) return;
    sized.current = want;
    browserAct({ tab, type: "size", ...(phone ? { w: 412, h: 860 } : { w: 1280, h: 800 }) }).catch((e) => setErr(e.message));
  }, [state, tab, phone]);
  const togglePhone = () => {
    setPhone((v) => { try { localStorage.setItem("panda.browser.phone", v ? "0" : "1"); } catch { /* blocked */ } return !v; });
  };

  return (
    <div className="bp">
      <div className="bp-bar">
        <button className="icon-btn" onClick={onClose} aria-label="Close browser"><IconX /></button>
        <button className="icon-btn" onClick={() => act({ type: "back" })} aria-label="Back"><IconArrowLeft size={18} /></button>
        <button className="icon-btn" onClick={() => act({ type: "reload" })} aria-label="Reload"><IconRetry size={18} /></button>
        <form className="bp-url" onSubmit={(e) => { e.preventDefault(); editingAddr.current = false; act({ type: "nav", url: addr }); (document.activeElement as HTMLElement)?.blur(); }}>
          <input value={addr} placeholder="Search or type a URL" inputMode="url" autoCapitalize="off"
            onFocus={() => { editingAddr.current = true; }} onBlur={() => { editingAddr.current = false; }}
            onChange={(e) => setAddr(e.target.value)} />
        </form>
        <button className="btn small-btn" onClick={togglePhone} title="Switch between phone and desktop pages">{phone ? "Phone" : "Desktop"}</button>
        <button className="icon-btn" onClick={() => act({ type: "new" })} aria-label="New tab"><IconPlus size={18} /></button>
      </div>
      {tabs.length > 1 && (
        <div className="bp-tabs">{tabs.map((t) => (
          <button key={t.id} className={t.id === tab ? "active" : ""} onClick={() => setTab(t.id)}>
            <span>{t.title || t.url || "New tab"}</span>
            <span className="bp-x" role="button" aria-label="Close tab" onClick={(e) => { e.stopPropagation(); browserAct({ type: "close", tab: t.id }); }}>×</span>
          </button>
        ))}</div>
      )}
      <div className="bp-stage" onWheel={(e) => act({ type: "scroll", dy: e.deltaY })}>
        {state !== "ready" ? (
          <div className="bp-empty"><IconGlobe size={28} />
            <p>{state === "installing" ? "Setting up the browser on the worker (a minute or two after Start)…" :
              state === "off" ? "The browser is turned off. Turn it on in Customize → Connectors." :
              state === "error" ? `The browser failed to start. ${err}` : "Connecting…"}</p></div>
        ) : frame ? (
          <img src={frame.url} alt="Browser" draggable={false} onClick={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            act({ type: "click", x: ((e.clientX - r.left) / r.width) * frame.w, y: ((e.clientY - r.top) / r.height) * frame.h });
          }} />
        ) : <div className="bp-empty"><p>Loading…</p></div>}
      </div>
      <div className="bp-keys">
        <button className="btn" onClick={() => act({ type: "scroll", dy: -500 })} aria-label="Scroll up"><span className="flip"><IconChevron size={16} /></span></button>
        <button className="btn" onClick={() => act({ type: "scroll", dy: 500 })} aria-label="Scroll down"><IconChevron size={16} /></button>
        <form className="bp-type" onSubmit={(e) => { e.preventDefault(); if (text) { act({ type: "type", text }); setText(""); } }}>
          <input value={text} placeholder="Type into the page" autoCapitalize="off" onChange={(e) => setText(e.target.value)} />
        </form>
        <button className="btn" onClick={() => act({ type: "key", key: "Backspace" })}>⌫</button>
        <button className="btn" onClick={() => act({ type: "key", key: "Tab" })}>Tab</button>
        <button className="btn primary" onClick={() => act({ type: "key", key: "Enter" })}>Enter</button>
      </div>
    </div>
  );
}
