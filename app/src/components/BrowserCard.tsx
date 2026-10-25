import { useEffect, useState } from "react";
import { hostOf, isAlive, useBrowser, useBrowserSnapshot, useBrowserViewer } from "../lib/browser";
import type { BrowserShot } from "../lib/supabase";
import { signedUrl } from "../lib/files";
import { useT } from "../lib/i18n";
import { AgentCursor } from "./AgentCursor";
import { IconCursor, IconExpand, IconGlobe, IconHand, IconLock, IconSpinner } from "./Icons";

/**
 * The browser inside a reply, like ChatGPT's agent: a small browser window that streams the live
 * screen while Panda works, with a pointer showing each click and a caption saying what it does.
 * After the session it keeps the final screen the worker saved with the reply.
 */
export function BrowserCard({ latest, shot }: { latest: boolean; shot?: BrowserShot }) {
  const t = useT();
  const { link, openFull, online } = useBrowser();
  const wantLive = latest && online && !!link;
  useBrowserViewer(link, wantLive);
  const snap = useBrowserSnapshot(wantLive ? link : null);
  const live = wantLive && isAlive(snap) && !!snap.frame;
  const [shotUrl, setShotUrl] = useState("");
  useEffect(() => {
    if (live || !shot?.shot) return;
    let alive = true;
    signedUrl(shot.shot).then((u) => { if (alive) setShotUrl(u); }).catch(() => {});
    return () => { alive = false; };
  }, [live, shot?.shot]);

  const status = live ? snap.status : null;
  if (!live && !shot?.shot && !(wantLive && isAlive(snap))) return null;

  const needsYou = status?.control === "needs_user";
  const mine = status?.control === "user";
  const url = (live ? status?.url : shot?.url) || "";
  const title = (live ? status?.title : shot?.title) || hostOf(url) || "Browser";
  const dims = live ? snap.dims : { w: shot?.w || 1024, h: shot?.h || 720 };
  const ratio = Math.max(0.5, dims.w / Math.max(1, dims.h));
  const lastAction = snap.actions[snap.actions.length - 1];
  const caption = needsYou ? status?.message || "Panda needs your help"
    : mine ? t("youControl")
    : status?.action || (status?.busy ? "Working…" : live ? (lastAction && Date.now() - lastAction.t < 15000 ? lastAction.c : t("watching")) : "");
  const img = live ? snap.frame : shotUrl;

  return (
    <div className={`bcard ${needsYou ? "needs" : ""} ${mine ? "mine" : ""} ${live ? "is-live" : "is-ended"}`}>
      <div className="bcard-bar">
        <span className="bcard-dots" aria-hidden><i /><i /><i /></span>
        <button className="bcard-url" onClick={live ? openFull : undefined} title={url}>
          {url.startsWith("https://") ? <IconLock size={12} /> : <IconGlobe size={12} />}
          <span>{hostOf(url) || title}</span>
        </button>
        <span className={`bcard-pill ${needsYou ? "needs" : mine ? "you" : live ? "live" : "ended"}`}>
          {live && !needsYou && !mine && <i className="dot" />}
          {needsYou ? t("needsYou") : mine ? "You" : live ? t("live") : t("ended")}
        </span>
        {live && <button className="icon-btn bcard-expand" onClick={openFull} aria-label="Open the live browser"><IconExpand size={16} /></button>}
      </div>
      <button className="bcard-stage" onClick={live ? openFull : () => img && window.open(img, "_blank", "noopener")}
        aria-label={live ? "Open the live browser" : "Open the last screen"}>
        <div className="bcard-frame" style={{ aspectRatio: String(ratio), maxWidth: ratio < 1 ? 320 : undefined }}>
          {img ? <img src={img} alt={title} draggable={false} /> : <div className="bcard-empty"><IconSpinner size={18} /> Connecting to the live view…</div>}
          {live && !mine && <AgentCursor action={lastAction} />}
          {live && status?.busy && <span className="bcard-scan" aria-hidden />}
        </div>
      </button>
      <div className="bcard-foot">
        <span className="bcard-caption">
          {live && status?.busy ? <IconSpinner size={14} /> : <IconCursor size={14} />}
          <span>{caption || title}</span>
        </span>
        {live && (needsYou || mine ? (
          <button className="btn primary small-btn" onClick={openFull}><IconHand size={15} /> {needsYou ? t("takeOver") : "Open"}</button>
        ) : (
          <button className="btn small-btn" onClick={openFull}>Open</button>
        ))}
        {!live && shot?.at && <span className="muted small">{new Date(shot.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
      </div>
    </div>
  );
}
