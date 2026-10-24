import { useEffect } from "react";
import { useBrowser } from "../lib/browser";
import { IconGlobe } from "./Icons";

/**
 * GPT-style live browser card shown inside the assistant message that used the browser.
 * It streams the live page; tap it (or "Take control") to open the full panel and drive it.
 */
export function BrowserInline({ live }: { live: boolean }) {
  const { link, openFull, online } = useBrowser();

  // While this card is on screen, keep the stream flowing.
  useEffect(() => {
    if (!link) return;
    link.setViewing(true);
    return () => link.setViewing(false);
  }, [link]);

  if (!link) return null;
  const status = link.status;
  const alive = Date.now() - link.lastSeen < 25000;
  const needsYou = status?.control === "needs_user";
  const youControl = status?.control === "user";
  const title = status?.title || (status?.url ? status.url.replace(/^https?:\/\//, "").split("/")[0] : "Browser");

  return (
    <div className={`bi ${needsYou ? "needs" : ""}`}>
      <button className="bi-head tap" onClick={openFull}>
        <span className="bi-ico"><IconGlobe size={16} /></span>
        <span className="bi-title">{title}</span>
        <span className={`bi-tag ${needsYou ? "needs" : youControl ? "you" : status?.busy ? "busy" : "idle"}`}>
          {needsYou ? "Needs you" : youControl ? "You" : status?.busy ? "Live" : online && alive ? "Live" : "Ended"}
        </span>
      </button>
      <button className="bi-stage" onClick={openFull} aria-label="Open the live browser">
        {link.frame ? <img src={link.frame} alt="Live browser view" draggable={false} /> :
          <div className="bi-empty">{online ? "Connecting to the live view…" : "Browser session ended"}</div>}
        {(live || needsYou) && link.frame && <span className="bi-pulse" />}
      </button>
      <div className="bi-foot">
        <span className="muted small">{needsYou ? (status?.message || "Panda needs your help") : "Live from Panda's browser"}</span>
        <button className="btn small-btn" onClick={openFull}>{needsYou || youControl ? "Take control" : "Open"}</button>
      </div>
    </div>
  );
}
