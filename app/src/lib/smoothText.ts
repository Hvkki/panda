import { useEffect, useLayoutEffect, useRef, useState } from "react";

/**
 * Smooth streaming. The model writes much faster than people read, and the worker pushes the
 * reply in bursts, so the raw text arrives in big jumps. This shows it at a calm, steady typing
 * pace instead: the reveal keeps a small buffer behind the real text, its speed only changes
 * gradually (no sudden rushes), and it speeds up just enough to never fall far behind a long
 * reply. Once the reply is done the rest is typed out a little faster, never dumped at once.
 *
 * Only messages that are live while on screen animate; history renders at once.
 */
const LAG_SECS = 2.2;        // while streaming, aim to drain the backlog over about this long
const FINISH_LAG_SECS = 1.2; // once the reply is done, a bit quicker
const MIN_CPS = 28;          // never slower than this (characters per second)
const FINISH_MIN_CPS = 70;   // once done, do not crawl through the last words
const BASE_MAX_CPS = 150;    // calm reading pace while streaming
const FINISH_MAX_CPS = 380;  // top pace once the reply is done
const BEHIND_CHARS = 900;    // past this much backlog, allow faster so we never lag far behind
const ACCEL = 110;           // how fast the speed itself may change (cps per second)
const RENDER_MS = 33;        // re-render at most ~30 fps (markdown is not free)

export function useSmoothText(target: string, live: boolean): { text: string; typing: boolean } {
  // Fixed per message once true: it streamed while we watched.
  const [animate, setAnimate] = useState(live);
  if (live && !animate) setAnimate(true);
  const [shown, setShown] = useState(() => (live ? 0 : target.length));
  const pos = useRef(shown);
  const cps = useRef(0); // current speed; kept across new chunks so the pace never resets
  const tgt = useRef(target);
  const liveRef = useRef(live);
  useLayoutEffect(() => { tgt.current = target; liveRef.current = live; }, [target, live]);

  useEffect(() => {
    if (!animate) return;
    // The text was replaced rather than extended (e.g. the final re-read): never run past its end.
    if (pos.current > target.length) { pos.current = target.length; setShown(target.length); }
    let raf = 0;
    let last = performance.now();
    let lastRender = 0;
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const goal = tgt.current.length;
      const backlog = goal - pos.current;
      if (backlog <= 0) { raf = 0; return; }
      const streaming = liveRef.current;
      const behind = Math.max(0, backlog - BEHIND_CHARS) * 0.6;
      const max = (streaming ? BASE_MAX_CPS : FINISH_MAX_CPS) + behind;
      const floor = streaming ? MIN_CPS : FINISH_MIN_CPS;
      const want = Math.min(max, Math.max(floor, backlog / (streaming ? LAG_SECS : FINISH_LAG_SECS)));
      // Ease toward the wanted speed instead of jumping to it.
      const step = (streaming ? ACCEL : ACCEL * 3) * dt;
      cps.current = Math.max(floor, cps.current + Math.max(-step, Math.min(step, want - cps.current)));
      pos.current = Math.min(goal, pos.current + cps.current * dt);
      if (now - lastRender >= RENDER_MS || pos.current >= goal) {
        lastRender = now;
        setShown(Math.floor(pos.current));
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => { if (raf) cancelAnimationFrame(raf); };
  }, [target, live, animate]);

  if (!animate) return { text: target, typing: false };
  let n = Math.min(shown, target.length);
  // Do not cut a word or a markdown token in half: end the slice at the last space when close.
  if (n < target.length) {
    const sp = target.lastIndexOf(" ", n);
    if (sp > n - 12 && sp > 0) n = sp;
  }
  return { text: target.slice(0, n), typing: n < target.length || live };
}
