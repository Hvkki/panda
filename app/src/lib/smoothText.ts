import { useEffect, useLayoutEffect, useRef, useState } from "react";

/**
 * Smooth streaming. The worker writes the reply about every half second, so the raw text arrives
 * in jumps. This reveals it at a steady pace instead: each frame shows a few more characters, and
 * the pace adapts to the backlog so the screen catches up in roughly one push interval and never
 * lags far behind. While the reply is live and nothing new has arrived yet, it keeps "typing" the
 * backlog it already has, so the reply flows instead of freezing and jumping.
 *
 * Only messages that are live while on screen animate; history renders at once.
 */
const CATCH_UP_SECS = 0.6;   // drain the backlog in about this long while streaming
const FINISH_SECS = 0.35;    // once the reply is done, finish the remainder faster
const MIN_CPS = 45;          // never slower than this (characters per second)
const RENDER_MS = 33;        // re-render at most ~30 fps (markdown is not free)

export function useSmoothText(target: string, live: boolean): { text: string; typing: boolean } {
  // Fixed per message once true: it streamed while we watched.
  const [animate, setAnimate] = useState(live);
  if (live && !animate) setAnimate(true);
  const [shown, setShown] = useState(() => (live ? 0 : target.length));
  const pos = useRef(shown);
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
      const cps = Math.max(MIN_CPS, backlog / (liveRef.current ? CATCH_UP_SECS : FINISH_SECS));
      pos.current = Math.min(goal, pos.current + cps * dt);
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
