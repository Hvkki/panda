import { useEffect, useState, type CSSProperties } from "react";
import { IMAGE_SECS, drawPct } from "../lib/drawing";

// Claude colours: graphite pencil on ivory paper, terracotta, kraft and manilla.
const INK = "#3D3929";
const FUR = "#2A2622";
const WHITE = "#FFFDF8";
const CLAY = "#D97757";
const KRAFT = "#D4A27F";
const KRAFT_DEEP = "#B98560";
const MANILLA = "#EBDBBC";
const CLOUD = "#B1ADA1";

type Vars = CSSProperties & Record<`--${string}`, number | string>;

/** A shape drawn the pencil way: the outline sketches itself in, then the colour fills it. */
function Sk({ d, fill, w = 1.8, i = 0, op }: { d: string; fill?: string; w?: number; i?: number; op?: number }) {
  const style: Vars = { "--i": i };
  return (
    <>
      {fill && <path className="pdw-fill" d={d} fill={fill} style={style} />}
      <path className="pdw-line" d={d} fill="none" stroke={INK} strokeWidth={w} strokeLinecap="round"
        strokeLinejoin="round" pathLength={1} style={style} opacity={op} />
    </>
  );
}

/** A line of the picture on the easel, drawn while the real picture is being made. */
function Stroke({ d, at, len, color = INK, w = 2 }: { d: string; at: number; len: number; color?: string; w?: number }) {
  const style: Vars = { "--s": at, "--d": len };
  return <path className="pdw-stroke" d={d} fill="none" stroke={color} strokeWidth={w} strokeLinecap="round"
    strokeLinejoin="round" pathLength={1} style={style} />;
}

function Wash({ d, at, len, color }: { d: string; at: number; len: number; color: string }) {
  const style: Vars = { "--s": at, "--d": len };
  return <path className="pdw-wash" d={d} fill={color} style={style} />;
}

/**
 * Panda seen from behind, on a chair, drawing on a big easel while Codex makes the picture.
 * The picture on the easel grows with the estimated progress (CSS animations timed by --T, the
 * usual seconds, and --t0, how far along it already was when this card appeared).
 */
export function DrawingPanda({ startedAt, expected = IMAGE_SECS }: { startedAt: number; expected?: number }) {
  const [t0] = useState(() => Math.max(0, (Date.now() - startedAt) / 1000));
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, []);
  const pct = drawPct((now - startedAt) / 1000, expected);
  const timing: Vars = { "--T": expected, "--t0": t0 };
  return (
    <div className="pdw-card" role="status" aria-live="off" aria-label={`Panda is drawing your picture, ${pct}%`}>
      <svg className="pdw" viewBox="0 0 360 236" style={timing} aria-hidden>
        {/* room: warm glow, floor, rug */}
        <circle cx="244" cy="92" r="112" fill="#F4ECE0" />
        <path d="M0 210H360V236H0Z" fill="#F1E9DC" />
        <Sk d="M6 210.5C90 208.8 270 211.8 354 209.8" w={1.3} op={0.7} />
        <Sk d="M38 214C38 204 222 204 222 214C222 224 38 224 38 214Z" fill={MANILLA} w={1.3} i={1} />
        <path d="M60 214c10 2 24 3 40 3M150 218c14 0 28-1 40-3" stroke={KRAFT} strokeWidth="1.2" fill="none" strokeLinecap="round" className="pdw-fill" />

        {/* easel: back leg, front legs, brace, the big board, clamp, tray with pencils */}
        <Sk d="M248 20H254L259 206H253Z" fill="#C99872" w={1.3} i={2} />
        <Sk d="M182 14H190L171 212H163Z" fill={KRAFT} w={1.5} i={2} />
        <Sk d="M314 14H322L342 212H334Z" fill={KRAFT} w={1.5} i={2} />
        <Sk d="M168 182H337V188H168Z" fill={KRAFT} w={1.2} i={3} />
        <Sk d="M150 24H330V146H150Z" fill="#FFFEFA" w={2} i={3} />
        <path className="pdw-line" d="M148.5 26.5H331.8M151.5 144.6H328.4" stroke={INK} strokeWidth="0.9" opacity="0.35" fill="none" pathLength={1} style={{ "--i": 4 } as Vars} />
        <Sk d="M231 13H257V27H231Z" fill={KRAFT} w={1.3} i={4} />
        <Sk d="M144 146H336V156H144Z" fill={KRAFT} w={1.5} i={4} />
        <Sk d="M292 140H314L318 143L314 146H292Z" fill={CLAY} w={1.1} i={5} />
        <Sk d="M268 140.5H281V146H268Z" fill="#F2B8A2" w={1.1} i={5} />

        {/* the picture taking shape: mountains, ground, a sun like Claude's spark, birds, shading, colour */}
        <Wash at={0.8} len={0.08} color={MANILLA} d="M176 122L204 86L220 104L246 66L274 108L290 94L318 124L322 130C262 122 210 118 168 128Z" />
        <Wash at={0.88} len={0.07} color="#F5C9AE" d="M226 46a12 12 0 1 0 24 0a12 12 0 1 0-24 0Z" />
        <Stroke at={0.02} len={0.2} d="M176 122L204 86L220 104L246 66L274 108L290 94L318 124" />
        <Stroke at={0.22} len={0.1} w={1.7} d="M168 128C210 118 262 122 322 130" />
        <Stroke at={0.34} len={0.1} color={CLAY} w={2.2} d="M226 46a12 12 0 1 0 24 0a12 12 0 1 0-24 0" />
        <Stroke at={0.45} len={0.12} color={CLAY} w={2.2}
          d="M254 46h6M249.3 57.3l4.3 4.3M238 62v6M226.7 57.3l-4.3 4.3M222 46h-6M226.7 34.7l-4.3-4.3M238 30v-6M249.3 34.7l4.3-4.3" />
        <Stroke at={0.58} len={0.08} w={1.6} d="M270 40q5-5 10 0q5-5 10 0M292 56q4-4 8 0q4-4 8 0" />
        <Stroke at={0.67} len={0.12} w={1.2} color="#6B6355" d="M252 80l-6 10M258 88l-6 10M264 96l-5 9M210 98l-4 7M296 104l-5 8M304 110l-4 7" />

        {/* chair seat and far legs, under the panda */}
        <Sk d="M78 194H186V201H78Z" fill={KRAFT_DEEP} w={1.5} i={5} />
        <Sk d="M100 201H105V211H100ZM159 201H164V211H159Z" fill={KRAFT_DEEP} w={1.1} i={5} />

        {/* panda from behind: white back, black shoulder band, resting left arm, little tail */}
        <g className="pdw-body">
          <Sk d="M86 198C78 160 88 124 132 118C176 124 186 160 178 198Z" fill={WHITE} i={6} />
          <path className="pdw-fill" d="M112 162q4 3 8 2M146 170q4 2 8 0M124 150q3 2 6 1" stroke={CLOUD} strokeWidth="1.2" fill="none" strokeLinecap="round" />
          <Sk d="M88 152C90 128 108 118 132 118C156 118 174 128 176 152C164 141 148 135 132 135C116 135 100 141 88 152Z" fill={FUR} i={7} />
          <Sk d="M126 190c-3-1-3-5 0-6c0-3 4-4 6-2c2-2 6-1 6 2c3 1 3 5 0 6c-1 3-5 3-6 1c-1 2-5 2-6-1Z" fill={WHITE} w={1.2} i={8} />
          <Sk d="M92 132C78 142 74 166 80 182C84 190 96 188 98 178C100 162 100 146 98 136Z" fill={FUR} i={8} />
        </g>

        {/* chair back between us and the panda */}
        <Sk d="M88 146H96V212H88ZM168 146H176V212H168Z" fill={KRAFT} w={1.5} i={9} />
        <Sk d="M82 140Q132 134 182 140L182 151Q132 145 82 151Z" fill={KRAFT} w={1.5} i={9} />
        <Sk d="M96 170H168V176H96Z" fill={KRAFT} w={1.3} i={10} />

        {/* head from behind: ears, round white back of the head, a tuft of fur */}
        <g className="pdw-head">
          <g className="pdw-ear pdw-ear-l"><Sk d="M96.5 66a11.5 11.5 0 1 0 23 0a11.5 11.5 0 1 0-23 0Z" fill={FUR} i={10} /></g>
          <g className="pdw-ear pdw-ear-r"><Sk d="M144.5 64a11.5 11.5 0 1 0 23 0a11.5 11.5 0 1 0-23 0Z" fill={FUR} i={10} /></g>
          <Sk d="M100 92C100 72 116 63 132 63C150 63 164 73 164 92C164 110 150 121 132 121C114 121 100 110 100 92Z" fill={WHITE} w={2} i={11} />
          <path className="pdw-fill" d="M128 67q4-5 9-1M118 76q3-3 7-2M142 74q3-2 7 0M110 98q2 4 6 6" stroke={CLOUD} strokeWidth="1.2" fill="none" strokeLinecap="round" />
        </g>

        {/* drawing arm: sweeps across the board over the picture's time, the paw scribbles */}
        <g className="pdw-arm">
          <g className="pdw-hand">
            <path className="pdw-fill" d="M166 140Q192 128 212 104" stroke={FUR} strokeWidth="18" strokeLinecap="round" fill="none" />
            <g transform="translate(219 96) rotate(-45)" className="pdw-fill">
              <path d="M-4 -2.7H0V2.7H-4Z" fill="#F2B8A2" stroke={INK} strokeWidth="1" />
              <path d="M0 -2.7H16V2.7H0Z" fill={CLAY} stroke={INK} strokeWidth="1" />
              <path d="M16 -2.7L22.5 0L16 2.7Z" fill={MANILLA} stroke={INK} strokeWidth="1" strokeLinejoin="round" />
              <path d="M20.6 -0.8L23 0L20.6 0.8Z" fill={INK} />
            </g>
            <circle className="pdw-fill" cx="214" cy="101" r="9.5" fill={FUR} />
            <path className="pdw-line" d="M205 104q4 6 12 4" stroke="#5A534C" strokeWidth="1.1" fill="none" pathLength={1} style={{ "--i": 12 } as Vars} />
          </g>
        </g>
      </svg>
      <div className="pdw-caption">
        <span>Panda is drawing your picture</span>
        <b>{pct}%</b>
      </div>
      <div className="pdw-bar" aria-hidden><i style={{ width: `${pct}%` }} /></div>
    </div>
  );
}
