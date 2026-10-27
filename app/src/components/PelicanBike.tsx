import { useEffect, useRef, type CSSProperties } from "react";
import "./PelicanBike.css";

// Claude colours: graphite pencil on ivory paper, terracotta, kraft and manilla.
const INK = "#3D3929";
const WHITE = "#FFFDF8";
const CLAY = "#D97757";
const KRAFT = "#D4A27F";
const KRAFT_DEEP = "#B98560";
const MANILLA = "#EBDBBC";
const CLOUD = "#B1ADA1";

export type PelicanStage =
  | "queued"
  | "starting"
  | "loading"
  | "reading prompt"
  | "sampling"
  | "decoding"
  | "saving"
  | "done"
  | "failed";

type Vars = CSSProperties & Record<`--${string}`, number | string>;

const DEFAULT_LABEL: Record<PelicanStage, string> = {
  queued: "Waiting in the queue",
  starting: "Warming up the engine",
  loading: "Loading the model",
  "reading prompt": "Reading your prompt",
  sampling: "Sampling frames",
  decoding: "Decoding the video",
  saving: "Saving your video",
  done: "Your video is ready",
  failed: "Something went wrong",
};

// How fast the legs pedal per stage (revolutions per second). 0 = stopped.
function cadence(stage: PelicanStage): number {
  switch (stage) {
    case "sampling":
      return 1.5;
    case "decoding":
    case "saving":
      return 1.0;
    case "reading prompt":
      return 0.85;
    case "queued":
    case "starting":
    case "loading":
      return 0.55;
    case "done":
    case "failed":
      return 0;
  }
}

/** A shape drawn the pencil way: outline sketches itself in, then the colour fills it. */
function Sk({
  d,
  fill,
  w = 1.8,
  i = 0,
  stroke = INK,
}: {
  d: string;
  fill?: string;
  w?: number;
  i?: number;
  stroke?: string;
}) {
  const style: Vars = { "--i": i };
  return (
    <>
      {fill && <path className="pbk-fill" d={d} fill={fill} style={style} />}
      <path
        className="pbk-line"
        d={d}
        fill="none"
        stroke={stroke}
        strokeWidth={w}
        strokeLinecap="round"
        strokeLinejoin="round"
        pathLength={1}
        style={style}
      />
    </>
  );
}

// Geometry of the two wheels (in viewBox units).
const REAR = { cx: 150, cy: 150, r: 34 };
const FRONT = { cx: 268, cy: 150, r: 34 };
const CRANK = { cx: 209, cy: 150, r: 15 }; // chainring / pedal circle centre + radius
const HIP = { x: 196, y: 112 }; // where the legs leave the body
const THIGH = 24, SHIN = 24;

export function PelicanBike(props: {
  pct: number;
  stage: PelicanStage;
  label?: string;
  compact?: boolean;
}) {
  const { stage, label, compact } = props;
  const pct = Math.max(0, Math.min(100, Math.round(props.pct)));
  const failed = stage === "failed";
  const done = stage === "done";
  const stopped = failed || done;

  const rearRef = useRef<SVGGElement>(null);
  const frontRef = useRef<SVGGElement>(null);
  const crankRef = useRef<SVGGElement>(null);
  const footLRef = useRef<SVGGElement>(null);
  const footRRef = useRef<SVGGElement>(null);
  const bodyRef = useRef<SVGGElement>(null);
  const sceneRef = useRef<SVGGElement>(null);
  const dustRef = useRef<SVGGElement>(null);
  const rafRef = useRef<number | null>(null);
  const legNearRef = useRef<SVGPathElement>(null);
  const legFarRef = useRef<SVGPathElement>(null);
  const footNearRef = useRef<SVGPathElement>(null);
  const footFarRef = useRef<SVGPathElement>(null);

  // Keep the live stage in a ref so the rAF loop reads it without restarting.
  const stageRef = useRef(stage);
  stageRef.current = stage;

  const reduced =
    typeof window !== "undefined" &&
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ── one rAF loop: wheels spin, crank + both feet pedal in sync, body bobs, scenery parallax ──
  useEffect(() => {
    let angle = 0; // pedal/wheel angle in radians
    let last = performance.now();
    let hop = 0;

    const tick = (t: number) => {
      const dt = Math.min(0.05, (t - last) / 1000);
      last = t;
      const st = stageRef.current;
      const rps = cadence(st);
      // reduced motion: calmer but still pedalling
      const speed = reduced ? Math.min(rps, 0.7) : rps;
      angle += speed * dt * Math.PI * 2;

      const deg = (angle * 180) / Math.PI;
      if (rearRef.current) rearRef.current.style.transform = `rotate(${deg}deg)`;
      if (frontRef.current) frontRef.current.style.transform = `rotate(${deg}deg)`;
      if (crankRef.current) crankRef.current.style.transform = `rotate(${deg}deg)`;

      // feet ride the crank circle: pedal point = crank centre + r*(cos,sin); 180deg apart.
      const fx = (a: number) => CRANK.cx + CRANK.r * Math.cos(a);
      const fy = (a: number) => CRANK.cy + CRANK.r * Math.sin(a);
      if (footLRef.current)
        footLRef.current.style.transform = `translate(${fx(angle)}px, ${fy(angle)}px)`;
      if (footRRef.current)
        footRRef.current.style.transform = `translate(${fx(angle + Math.PI)}px, ${fy(
          angle + Math.PI
        )}px)`;

      // body bobs per pedal stroke (twice per revolution)
      const bob = speed > 0 ? Math.sin(angle * 2) * 1.2 : 0;
      if (bodyRef.current) bodyRef.current.style.transform = `translateY(${bob.toFixed(2)}px)`;

      // legs: thigh + shin from the hip to each pedal, knee bent forward like a real rider
      const hip = { x: HIP.x, y: HIP.y + bob };
      const leg = (a: number, path: SVGPathElement | null, foot: SVGPathElement | null) => {
        const fx = CRANK.cx + CRANK.r * Math.cos(a), fy = CRANK.cy + CRANK.r * Math.sin(a) - 3;
        const dx = fx - hip.x, dy = fy - hip.y;
        const d = Math.min(Math.hypot(dx, dy), THIGH + SHIN - 0.5);
        const base = Math.atan2(dy, dx);
        const bend = Math.acos((THIGH * THIGH + d * d - SHIN * SHIN) / (2 * THIGH * d));
        const k1 = { x: hip.x + THIGH * Math.cos(base - bend), y: hip.y + THIGH * Math.sin(base - bend) };
        const k2 = { x: hip.x + THIGH * Math.cos(base + bend), y: hip.y + THIGH * Math.sin(base + bend) };
        const knee = k1.x > k2.x ? k1 : k2;
        path?.setAttribute("d", `M${hip.x.toFixed(1)} ${hip.y.toFixed(1)}L${knee.x.toFixed(1)} ${knee.y.toFixed(1)}L${fx.toFixed(1)} ${fy.toFixed(1)}`);
        // webbed foot: three toes fanned forward, flat on the pedal
        foot?.setAttribute("d", `M${(fx - 3).toFixed(1)} ${fy.toFixed(1)}l12 -1.5l-3 2.5l4 1l-4 1.2l2.5 2l-11.5 -2Z`);
      };
      leg(angle + Math.PI, legFarRef.current, footFarRef.current);
      leg(angle, legNearRef.current, footNearRef.current);

      if (!reduced) {
        // parallax scenery scrolls right-to-left while moving
        if (sceneRef.current) {
          const shift = (((angle * 9) % 320) + 320) % 320;
          sceneRef.current.style.transform = `translateX(${-shift.toFixed(2)}px)`;
        }
        // dust puffs behind rear wheel pulse with cadence
        if (dustRef.current) {
          const p = speed > 0 ? 0.35 + 0.35 * (1 + Math.sin(angle * 3)) : 0;
          dustRef.current.style.opacity = String(Math.min(0.85, p));
        }
      }

      // happy little hop on done
      if (bodyRef.current && st === "done") {
        hop += dt;
        const h = Math.max(0, Math.sin(hop * 6)) * -6 * Math.exp(-hop * 0.9);
        bodyRef.current.style.transform = `translateY(${h.toFixed(2)}px)`;
      }

      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [reduced]);

  const caption = label ?? DEFAULT_LABEL[stage];
  const barFill = failed ? CLOUD : CLAY;

  return (
    <div
      className={`pbk-card${compact ? " pbk-compact" : ""}${failed ? " pbk-failed" : ""}${
        done ? " pbk-done" : ""
      }`}
      role="status"
      data-stage={stage}
      aria-live="off"
      aria-label={`Making your video, ${pct}%`}
    >
      <svg className="pbk" viewBox="0 0 320 200" width="100%" aria-hidden>
        <defs>
          <clipPath id="pbk-sky">
            <rect x="0" y="0" width="320" height="200" />
          </clipPath>
        </defs>

        {/* warm glow background */}
        <rect x="0" y="0" width="320" height="200" rx="10" fill="#F6ECDF" />

        {/* ── scenery: sun, hills, kraft clouds, clapper-board signposts (parallax) ── */}
        <g clipPath="url(#pbk-sky)">
          <g ref={sceneRef} className="pbk-scene">
            {/* draw two copies offset by 320 so the scroll loops seamlessly */}
            {[0, 320, 640].map((ox) => (
              <g key={ox} transform={`translate(${ox} 0)`}>
                <Sk d="M44 40a13 13 0 1 0 26 0a13 13 0 1 0-26 0Z" fill="#F5C9AE" w={1.4} i={1} />
                <g stroke={CLAY} strokeWidth="1.8" strokeLinecap="round">
                  <path className="pbk-line" pathLength={1} d="M57 16v-9M57 73v-9M28 40h-9M95 40h-9M37 20l-6-6M83 20l6-6" style={{ "--i": 1 } as Vars} />
                </g>
                <Sk d="M120 34q10 0 16 7q6-7 16-7q8 0 12 6H112q0-6 8-6Z" fill={WHITE} w={1.3} i={2} />
                <Sk d="M210 48q9 0 14 6q6-6 14-6q7 0 10 5H202q0-5 8-5Z" fill={WHITE} w={1.3} i={2} />
                {/* clapper-board signpost */}
                <g transform="translate(250 78)">
                  <Sk d="M0 0h2v34H0Z" fill={KRAFT_DEEP} w={1.2} i={3} />
                  <Sk d="M-10 -14h24v12H-10Z" fill={MANILLA} w={1.3} i={3} />
                  <path className="pbk-line" d="M-10 -10h24M-7 -14l3 4M-2 -14l3 4M3 -14l3 4M8 -14l3 4" stroke={INK} strokeWidth="1" pathLength={1} style={{ "--i": 4 } as Vars} />
                </g>
              </g>
            ))}
          </g>
        </g>

        {/* rolling hills in front of scenery */}
        <Sk d="M0 150q50 -22 100 0t100 0t120 0V200H0Z" fill="#E7D7BA" w={1.4} i={3} />

        {/* ── road / film strip; ridden part fills CLAY by pct ── */}
        <g>
          <Sk d="M0 182q80 -10 160 -6t160 6V196H0Z" fill="#EDE3CE" w={1.4} i={4} />
          {/* film-strip perforations */}
          <g opacity={0.55}>
            {Array.from({ length: 11 }).map((_, k) => (
              <rect key={k} x={10 + k * 28} y={186} width="10" height="5" rx="1.2" fill={WHITE} stroke={INK} strokeWidth="0.5" />
            ))}
          </g>
          {/* ridden part fills in clay */}
          <rect x="0" y="183" width={(pct / 100) * 320} height="10" fill={CLAY} opacity={0.45} />
        </g>

        {/* ── dust puffs + speed lines behind rear wheel ── */}
        <g ref={dustRef} className="pbk-dust" opacity={0}>
          <circle cx="108" cy="156" r="6" fill={CLOUD} opacity={0.5} />
          <circle cx="98" cy="150" r="4.5" fill={CLOUD} opacity={0.4} />
          <circle cx="116" cy="150" r="4" fill={CLOUD} opacity={0.35} />
          <path d="M104 142h-22M108 148h-28M104 134h-16" stroke={CLOUD} strokeWidth="1.6" strokeLinecap="round" />
        </g>

        {/* ── bicycle ── */}
        <g className="pbk-bike">
          {/* rear wheel */}
          <g ref={rearRef} className="pbk-wheel" style={{ transformBox: "view-box", transformOrigin: `${REAR.cx}px ${REAR.cy}px` } as CSSProperties}>
            <circle cx={REAR.cx} cy={REAR.cy} r={REAR.r} fill="none" stroke={INK} strokeWidth="2.4" />
            <circle cx={REAR.cx} cy={REAR.cy} r={REAR.r - 4} fill="none" stroke={KRAFT_DEEP} strokeWidth="1.2" />
            {Array.from({ length: 8 }).map((_, k) => {
              const a = (k / 8) * Math.PI * 2;
              return (
                <line key={k} x1={REAR.cx} y1={REAR.cy} x2={REAR.cx + REAR.r * Math.cos(a)} y2={REAR.cy + REAR.r * Math.sin(a)} stroke={INK} strokeWidth="1.1" />
              );
            })}
            <circle cx={REAR.cx} cy={REAR.cy} r="3" fill={INK} />
          </g>
          {/* front wheel */}
          <g ref={frontRef} className="pbk-wheel" style={{ transformBox: "view-box", transformOrigin: `${FRONT.cx}px ${FRONT.cy}px` } as CSSProperties}>
            <circle cx={FRONT.cx} cy={FRONT.cy} r={FRONT.r} fill="none" stroke={INK} strokeWidth="2.4" />
            <circle cx={FRONT.cx} cy={FRONT.cy} r={FRONT.r - 4} fill="none" stroke={KRAFT_DEEP} strokeWidth="1.2" />
            {Array.from({ length: 8 }).map((_, k) => {
              const a = (k / 8) * Math.PI * 2;
              return (
                <line key={k} x1={FRONT.cx} y1={FRONT.cy} x2={FRONT.cx + FRONT.r * Math.cos(a)} y2={FRONT.cy + FRONT.r * Math.sin(a)} stroke={INK} strokeWidth="1.1" />
              );
            })}
            <circle cx={FRONT.cx} cy={FRONT.cy} r="3" fill={INK} />
          </g>

          {/* frame: rear hub -> crank -> seat -> head tube -> front hub */}
          <g stroke={INK} strokeWidth="2.6" strokeLinecap="round" fill="none">
            <path className="pbk-line" pathLength={1} d={`M${REAR.cx} ${REAR.cy}L${CRANK.cx} ${CRANK.cy}L188 112L${REAR.cx} ${REAR.cy}`} style={{ "--i": 5 } as Vars} />
            <path className="pbk-line" pathLength={1} d={`M${CRANK.cx} ${CRANK.cy}L250 108L${FRONT.cx} ${FRONT.cy}`} style={{ "--i": 6 } as Vars} />
            <path className="pbk-line" pathLength={1} d="M188 112L252 106" style={{ "--i": 6 } as Vars} />
            {/* seat post + seat */}
            <path className="pbk-line" pathLength={1} d="M188 112V96" style={{ "--i": 7 } as Vars} />
            {/* head tube down to fork */}
            <path className="pbk-line" pathLength={1} d={`M252 106L${FRONT.cx} ${FRONT.cy}`} style={{ "--i": 7 } as Vars} />
            {/* handlebar stem up */}
            <path className="pbk-line" pathLength={1} d="M252 106L256 92L246 90" style={{ "--i": 8 } as Vars} />
          </g>
          <Sk d="M178 93h20l-3 6h-14Z" fill={INK} w={1.2} i={8} />

          {/* crank + pedals (rotating group) */}
          <g ref={crankRef} className="pbk-crank" style={{ transformBox: "view-box", transformOrigin: `${CRANK.cx}px ${CRANK.cy}px` } as CSSProperties}>
            <circle cx={CRANK.cx} cy={CRANK.cy} r="8" fill="none" stroke={INK} strokeWidth="1.4" />
            <line x1={CRANK.cx} y1={CRANK.cy} x2={CRANK.cx + CRANK.r} y2={CRANK.cy} stroke={INK} strokeWidth="2.4" strokeLinecap="round" />
            <line x1={CRANK.cx} y1={CRANK.cy} x2={CRANK.cx - CRANK.r} y2={CRANK.cy} stroke={INK} strokeWidth="2.4" strokeLinecap="round" />
          </g>
          {/* pedals ride the crank circle, kept level via rAF translate (no rotation) */}
          <g ref={footLRef} className="pbk-pedal">
            <rect x="-6" y="-2" width="12" height="4" rx="1.5" fill={KRAFT_DEEP} stroke={INK} strokeWidth="0.8" />
          </g>
          <g ref={footRRef} className="pbk-pedal">
            <rect x="-6" y="-2" width="12" height="4" rx="1.5" fill={KRAFT_DEEP} stroke={INK} strokeWidth="0.8" />
          </g>
        </g>

        {/* ── legs: hip -> knee -> foot on the pedal, recomputed every frame (far leg paler) ── */}
        <g className="pbk-legs" fill="none" strokeLinecap="round" strokeLinejoin="round">
          <path ref={legFarRef} stroke={KRAFT_DEEP} strokeWidth="3" />
          <path ref={footFarRef} fill={KRAFT_DEEP} stroke={INK} strokeWidth="0.8" />
          <path ref={legNearRef} stroke={CLAY} strokeWidth="3.4" />
          <path ref={footNearRef} fill={CLAY} stroke={INK} strokeWidth="0.9" />
        </g>

        {/* ── pelican body (bobs). Legs are drawn above so the feet stay on the pedals ── */}
        <g ref={bodyRef} className="pbk-pelican" style={{ transformBox: "view-box", transformOrigin: "190px 110px" } as CSSProperties}>
          {/* tail */}
          <Sk d="M150 96q-16 0-24 10q14 2 24 -2Z" fill={WHITE} w={1.5} i={9} />
          {/* body / belly */}
          <Sk d="M150 76q40 -10 60 10q10 12 2 30q-10 8-40 6q-26 -2-30 -20q-4 -18 8 -26Z" fill={WHITE} w={2} i={9} />
          {/* folded wing with fluttering feathers */}
          <g className="pbk-wing" style={{ transformBox: "view-box", transformOrigin: "190px 96px" } as CSSProperties}>
            <Sk d="M170 92q24 -6 40 2q-6 16 -22 18q-16 2 -22 -8q-2 -8 4 -12Z" fill="#F3EFE6" w={1.6} i={10} />
            <path className="pbk-line pbk-feather" d="M178 98q10 -2 18 2M176 104q12 -2 22 3M180 110q10 0 18 4" stroke={CLOUD} strokeWidth="1.1" fill="none" pathLength={1} style={{ "--i": 11 } as Vars} />
          </g>
          {/* neck + head */}
          <Sk d="M196 78q14 -6 20 -22q4 -12 16 -12q10 0 10 10q0 8 -8 12q-6 4 -8 16q-2 10 -14 12Z" fill={WHITE} w={2} i={11} />
          {/* head top */}
          <Sk d="M224 44a12 12 0 1 0 24 0a12 12 0 1 0 -24 0Z" fill={WHITE} w={2} i={12} />
          {/* eye */}
          <circle className="pbk-fill" cx="240" cy="42" r="2.2" fill={INK} />
          {/* big clay pouch beak */}
          <g className="pbk-beak" style={{ transformBox: "view-box", transformOrigin: "248px 50px" } as CSSProperties}>
            <Sk d="M246 40l40 6q6 1 4 7l-2 6q-20 2-34 -4q-10 -5-8 -15Z" fill={KRAFT} w={1.8} i={13} />
            {/* pouch */}
            <Sk d="M252 52q16 10 34 6q-2 8-16 10q-16 2-22 -6q-2 -6 4 -10Z" fill={CLAY} w={1.6} i={14} />
            <path className="pbk-line" d="M248 46l40 5" stroke={INK} strokeWidth="1" fill="none" pathLength={1} style={{ "--i": 14 } as Vars} />
          </g>
        </g>

        {/* done: check mark */}
        {done && (
          <g className="pbk-check">
            <circle cx="286" cy="40" r="15" fill={WHITE} stroke={CLAY} strokeWidth="2" />
            <path d="M279 40l5 5l9 -11" fill="none" stroke={CLAY} strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" pathLength={1} className="pbk-line" style={{ "--i": 1 } as Vars} />
          </g>
        )}
      </svg>

      <div className="pbk-caption">
        <span>{caption}</span>
        <b>{pct}%</b>
      </div>
      <div className="pbk-bar" aria-hidden>
        <i style={{ width: `${pct}%`, background: stopped ? barFill : undefined }} />
      </div>
    </div>
  );
}
