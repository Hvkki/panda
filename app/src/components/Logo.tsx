import { useId } from "react";

/**
 * Panda mark: a round, bold, high-contrast panda face inside a circle (inspired by classic
 * conservation-style pandas — flat black shapes on white). Idle: blinks. `working`: ears
 * wiggle, eyes look around, and a ring spins around the badge while Panda thinks/acts.
 */
export function Logo({ size = 28, working = false, animated = false }: { size?: number; working?: boolean; animated?: boolean }) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const clip = `pd-clip-${uid}`;
  const ring = `pd-ring-${uid}`;
  const cls = ["panda", animated || working ? "panda-alive" : "", working ? "panda-working" : ""].join(" ").trim();
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden className={cls}>
      <defs>
        <clipPath id={clip}><circle cx="32" cy="32" r="29" /></clipPath>
        <linearGradient id={ring} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#F0703F" />
          <stop offset="1" stopColor="#F0703F" stopOpacity="0" />
        </linearGradient>
      </defs>
      {/* badge */}
      <circle cx="32" cy="32" r="30.5" fill="#FFFFFF" stroke="#1B1A19" strokeWidth="3" />
      <g clipPath={`url(#${clip})`}>
        <g className="pd-head">
          {/* ears */}
          <circle className="pd-ear pd-ear-l" cx="15.5" cy="17" r="8.6" fill="#1B1A19" />
          <circle className="pd-ear pd-ear-r" cx="48.5" cy="17" r="8.6" fill="#1B1A19" />
          {/* face */}
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
          {/* nose + mouth */}
          <path d="M28.6 41.6c0-1.3 1.5-2 3.4-2s3.4.7 3.4 2c0 1.4-2 2.9-3.4 2.9s-3.4-1.5-3.4-2.9z" fill="#1B1A19" />
          <path d="M32 44.4v2.1M32 46.5c-1.1 1.3-2.6 1.6-3.8.9M32 46.5c1.1 1.3 2.6 1.6 3.8.9" fill="none" stroke="#1B1A19"
            strokeWidth="1.6" strokeLinecap="round" />
        </g>
      </g>
      {/* thinking ring */}
      <circle className="pd-ring" cx="32" cy="32" r="30.5" fill="none" stroke={`url(#${ring})`} strokeWidth="3.4"
        strokeLinecap="round" strokeDasharray="48 144" />
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
