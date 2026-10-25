import type { BrowserAction } from "../lib/browser";

/**
 * GPT-style pointer over the live screen: it glides to whatever Panda clicks or types into,
 * outlines the target, and ripples on a click. Each new step restarts a CSS fade, so the pointer
 * disappears a few seconds after Panda stops acting (no timers needed).
 */
export function AgentCursor({ action }: { action?: BrowserAction }) {
  if (!action || typeof action.x !== "number" || typeof action.y !== "number") return null;
  const x = action.x, y = action.y, w = action.w || 0, h = action.h || 0;
  const cx = (x + w / 2) * 100, cy = (y + h / 2) * 100;
  return (
    <div className="agent-layer" aria-hidden>
      {w > 0 && h > 0 && (
        <div key={`t${action.t}`} className="agent-target"
          style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` }} />
      )}
      {/* The outer element keeps its place between steps, so the pointer glides to the next one. */}
      <div className="agent-cursor" style={{ left: `${cx}%`, top: `${cy}%` }}>
        <div key={`c${action.t}`} className="agent-cursor-fade">
          {(action.k === "click" || action.k === "type") && <span className="agent-ripple" />}
          <svg width="22" height="22" viewBox="0 0 24 24" className="agent-arrow">
            <path d="M5 3.2 19.2 10l-6.3 2-3 6.6z" fill="#1b1a19" stroke="#fff" strokeWidth="1.6" strokeLinejoin="round" />
          </svg>
          <span className="agent-badge">Panda</span>
        </div>
      </div>
    </div>
  );
}

/** Where the user tapped (while they're in control), as a quick ripple. */
export function TapRipples({ taps }: { taps: { id: number; x: number; y: number }[] }) {
  return (
    <>
      {taps.map((t) => <span key={t.id} className="tap-ripple" style={{ left: `${t.x * 100}%`, top: `${t.y * 100}%` }} />)}
    </>
  );
}
