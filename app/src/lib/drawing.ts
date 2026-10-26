/**
 * Seconds one Codex picture usually takes. Measured on Kaggle, 26 Oct 2026, five pictures in a row
 * with worker/skills/panda-image/make_image.py: 43, 56, 31, 47 and 40 s (average 43.4 s).
 */
export const IMAGE_SECS = 44;

/** Estimated progress: steady up to 95% at the usual time, then creeping towards 99%. */
export function drawPct(elapsed: number, expected = IMAGE_SECS): number {
  if (!(elapsed > 0)) return 0;
  const r = elapsed / expected;
  if (r < 1) return Math.floor(95 * r);
  return Math.min(99, Math.floor(95 + 4 * (1 - Math.exp(-(r - 1) * 1.5))));
}
