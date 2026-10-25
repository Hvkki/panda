import { useEffect, useRef } from "react";

/**
 * The live voice line while you talk: a row of small dots that grow into bars with your voice and
 * slide left as time goes on. `level` is read about 20 times a second; `frozen` stops the line
 * (while the clip is turned into text). Drawn on a canvas in the element's text color.
 */
export function VoiceWave({ level, frozen, metered }: { level: () => number; frozen: boolean; metered: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const hist = useRef<number[]>([]);
  const fn = useRef({ level, frozen, metered });
  useEffect(() => { fn.current = { level, frozen, metered }; });

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const g = el.getContext("2d");
    if (!g) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const PITCH = 5, BAR = 2.5, STEP_MS = 55;
    let raf = 0, lastStep = 0, smooth = 0, w = 0, h = 0;

    const fit = () => {
      const dpr = Math.min(3, window.devicePixelRatio || 1);
      w = el.clientWidth; h = el.clientHeight;
      el.width = Math.round(w * dpr); el.height = Math.round(h * dpr);
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);

    const draw = (t: number) => {
      raf = requestAnimationFrame(draw);
      const { level: lv, frozen: fz, metered: m } = fn.current;
      const n = Math.max(1, Math.floor(w / PITCH));
      if (!fz && t - lastStep >= STEP_MS) {
        lastStep = t;
        // With no live mic (browser recognition) a soft made-up line shows it is listening.
        const raw = m ? lv() : 0.08 + 0.07 * Math.sin(t / 260) + Math.random() * 0.05;
        smooth = smooth * 0.35 + raw * 0.65;
        hist.current.push(smooth);
        if (hist.current.length > n) hist.current.splice(0, hist.current.length - n);
      }
      g.clearRect(0, 0, w, h);
      const color = getComputedStyle(el).color;
      const data = hist.current;
      const off = n - data.length;  // empty slots on the left stay as faint dots
      for (let i = 0; i < n; i++) {
        const v = i >= off ? data[i - off] : -1;
        const bh = v < 0 ? BAR : Math.max(BAR, Math.min(h, v * h * 1.05));
        const x = w - (n - i) * PITCH + (PITCH - BAR) / 2;
        g.globalAlpha = v < 0 ? 0.28 : 0.95;
        g.fillStyle = color;
        g.beginPath();
        g.roundRect(x, (h - bh) / 2, BAR, bh, BAR / 2);
        g.fill();
      }
      if (reduce && fz) cancelAnimationFrame(raf);
    };
    raf = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);

  return <canvas ref={canvas} className={`voice-wave ${frozen ? "frozen" : ""}`} aria-hidden />;
}
