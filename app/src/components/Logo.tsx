import { useId } from "react";

type Props = {
  size?: number;
  /** Idle life: blinks, ears twitch, floats gently. */
  animated?: boolean;
  /** Thinking or acting: an orbit spins around the badge, the eyes read left and right. */
  working?: boolean;
  /** A short hop and rosy cheeks, e.g. when an answer has just finished. */
  happy?: boolean;
  /** Eyes look down (at the composer while you type). */
  look?: "down" | null;
};

/**
 * Panda mark: a round, bold, high-contrast panda face inside a circle. Every moving part has its
 * own class so CSS can animate it (see "Panda motion" in styles.css). The working orbit is a
 * status indicator, so it keeps turning when the device asks for reduced motion; the rest stops.
 */
export function Logo({ size = 28, working = false, animated = false, happy = false, look = null }: Props) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const clip = `pd-clip-${uid}`;
  const ring = `pd-ring-${uid}`;
  const cls = ["panda", animated || working || happy ? "panda-alive" : "", working ? "panda-working" : "",
    happy ? "panda-happy" : "", look === "down" ? "panda-look-down" : ""].filter(Boolean).join(" ");
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden className={cls}>
      <defs>
        <clipPath id={clip}><circle cx="32" cy="32" r="29" /></clipPath>
        <linearGradient id={ring} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: "var(--accent, #E2603A)" }} />
          <stop offset="1" style={{ stopColor: "var(--accent-2, #F6A04D)", stopOpacity: 0.1 }} />
        </linearGradient>
      </defs>
      <circle className="pd-halo" cx="32" cy="32" r="31" />
      <g className="pd-float">
        {/* badge */}
        <circle cx="32" cy="32" r="30.5" fill="#FFFFFF" stroke="#1B1A19" strokeWidth="3" />
        <g clipPath={`url(#${clip})`}>
          <g className="pd-head">
            <circle className="pd-ear pd-ear-l" cx="15.5" cy="17" r="8.6" fill="#1B1A19" />
            <circle className="pd-ear pd-ear-r" cx="48.5" cy="17" r="8.6" fill="#1B1A19" />
            <path d="M32 14c12.5 0 21 8.6 21 20.2C53 46.3 43.8 55 32 55S11 46.3 11 34.2C11 22.6 19.5 14 32 14z" fill="#FFFFFF" />
            {/* eye patches: teardrops tilted outward */}
            <path d="M23.2 25.5c4.4-.6 7.3 3.2 6.6 8.3-.7 5-4.2 9.3-8.4 9.6-3.7.3-5.5-2.9-4.9-6.9.8-5.2 2.8-10.4 6.7-11z" fill="#1B1A19" />
            <path d="M40.8 25.5c-4.4-.6-7.3 3.2-6.6 8.3.7 5 4.2 9.3 8.4 9.6 3.7.3 5.5-2.9 4.9-6.9-.8-5.2-2.8-10.4-6.7-11z" fill="#1B1A19" />
            <g className="pd-eyes">
              <g className="pd-pupils">
                <circle cx="25" cy="33.2" r="2.3" fill="#FFFFFF" />
                <circle cx="39" cy="33.2" r="2.3" fill="#FFFFFF" />
              </g>
            </g>
            <circle className="pd-cheek" cx="17.6" cy="44" r="3.2" />
            <circle className="pd-cheek" cx="46.4" cy="44" r="3.2" />
            {/* nose + mouth */}
            <path d="M28.6 41.6c0-1.3 1.5-2 3.4-2s3.4.7 3.4 2c0 1.4-2 2.9-3.4 2.9s-3.4-1.5-3.4-2.9z" fill="#1B1A19" />
            <path className="pd-mouth" d="M32 44.4v2.1M32 46.5c-1.1 1.3-2.6 1.6-3.8.9M32 46.5c1.1 1.3 2.6 1.6 3.8.9" fill="none"
              stroke="#1B1A19" strokeWidth="1.6" strokeLinecap="round" />
          </g>
        </g>
      </g>
      {/* working orbit: a bright arc and a fainter one turning the other way */}
      <circle className="pd-ring" cx="32" cy="32" r="30.5" fill="none" stroke={`url(#${ring})`} strokeWidth="3.6"
        strokeLinecap="round" strokeDasharray="52 140" />
      <circle className="pd-ring2" cx="32" cy="32" r="30.5" fill="none" style={{ stroke: "var(--accent, #E2603A)" }} strokeWidth="3.6"
        strokeLinecap="round" strokeDasharray="10 182" strokeOpacity="0.55" />
    </svg>
  );
}

export function Wordmark({ size = 22 }: { size?: number }) {
  return (
    <span className="wordmark" style={{ fontSize: size }}>
      <Logo size={size * 1.3} animated /> Panda
    </span>
  );
}
