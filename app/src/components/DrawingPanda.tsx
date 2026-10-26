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
      <svg className="pdw" viewBox="52 30 256 184" style={timing} aria-hidden>
        {/* room: warm round glow behind the easel, rug under the chair */}
        <circle cx="181" cy="117" r="91" fill="#F6ECDF" />
        <Sk d="M84 200C84 192 276 192 276 200C276 208 84 208 84 200Z" fill="#EAD9BF" w={1.3} i={1} />

        {/* easel: hidden back leg, right leg, posts, the big board, clamp, shelf with an eraser and a box */}
        <Sk d="M137 147L122 201H128L143 147Z" fill={KRAFT_DEEP} w={1.3} i={2} />
        <Sk d="M240 64H247L264 200H257Z" fill={KRAFT} w={1.5} i={2} />
        <Sk d="M144 42H151V56H144ZM226 42H233V56H226Z" fill={KRAFT} w={1.4} i={2} />
        <Sk d="M130 53H244Q247 53 247 56V145Q247 148 244 148H130Q127 148 127 145V56Q127 53 130 53Z" fill="#FFFEFA" w={2} i={3} />
        <Sk d="M180 46.5H197Q198.5 46.5 198.5 48V56.5Q198.5 58 197 58H180Q178.5 58 178.5 56.5V48Q178.5 46.5 180 46.5Z" fill={KRAFT_DEEP} w={1.3} i={4} />
        <Sk d="M186 147.5H256V155H186Z" fill={KRAFT} w={1.5} i={4} />
        <Sk d="M204 155H210V194H204ZM194 176H257V181.5H194Z" fill={KRAFT} w={1.3} i={5} />
        <Sk d="M206.5 142H215.5V147.5H206.5Z" fill={WHITE} w={1.1} i={5} />
        <Sk d="M221 141H240Q241.5 141 241.5 142.5V147.5H219.5V142.5Q219.5 141 221 141Z" fill={CLAY} w={1.1} i={5} />

        {/* the picture taking shape on the board: mountains, ground, a sun like Claude's spark, birds, shading */}
        <g transform="translate(189 62) scale(0.36) translate(-166 -24)">
          <Wash at={0.8} len={0.08} color={MANILLA} d="M176 122L204 86L220 104L246 66L274 108L290 94L318 124L322 130C262 122 210 118 168 128Z" />
          <Wash at={0.88} len={0.07} color="#F5C9AE" d="M226 46a12 12 0 1 0 24 0a12 12 0 1 0-24 0Z" />
          <Stroke at={0.02} len={0.2} w={3.4} d="M176 122L204 86L220 104L246 66L274 108L290 94L318 124" />
          <Stroke at={0.22} len={0.1} w={3} d="M168 128C210 118 262 122 322 130" />
          <Stroke at={0.34} len={0.1} color={CLAY} w={2.8} d="M226 46a12 12 0 1 0 24 0a12 12 0 1 0-24 0" />
          <Stroke at={0.45} len={0.12} color={CLAY} w={2.8}
            d="M254 46h6M249.3 57.3l4.3 4.3M238 62v6M226.7 57.3l-4.3 4.3M222 46h-6M226.7 34.7l-4.3-4.3M238 30v-6M249.3 34.7l4.3-4.3" />
          <Stroke at={0.58} len={0.08} w={2} d="M270 40q5-5 10 0q5-5 10 0M292 56q4-4 8 0q4-4 8 0" />
          <Stroke at={0.67} len={0.12} w={1.6} color="#6B6355" d="M252 80l-6 10M258 88l-6 10M264 96l-5 9M210 98l-4 7M296 104l-5 8M304 110l-4 7" />
        </g>

        {/* panda from behind: black arms and legs around a white back, round head, little cloud of a tail */}
        <g className="pdw-body">
          <Sk d="M117 126C106 142 105 172 111 196H195C200 172 199 142 189 126C172 117 134 117 117 126Z" fill={FUR} i={6} />
          <Sk d="M130 128C126 148 126 168 130 184H177C181 168 181 148 177 128C162 122 145 122 130 128Z" fill={WHITE} i={7} />
          <path className="pdw-fill" d="M140 150q4 3 8 2M160 160q4 2 8 0M146 168q3 2 6 1" stroke={CLOUD} strokeWidth="1.1" fill="none" strokeLinecap="round" />
          <Sk d="M119 126C134 137 172 137 188 126C176 118 131 118 119 126Z" fill={FUR} w={1.4} i={7} />
          <Sk d="M145 173c-4-1-4-7 0-8c0-4 5-6 8-3c3-3 8-1 8 3c4 1 4 7 0 8c-2 3-7 3-8 1c-1 2-6 2-8-1Z" fill={WHITE} w={1.3} i={8} />
          <Sk d="M114 186C110 196 120 201 130 197C131 192 128 187 122 186ZM178 186C176 194 184 200 194 197C196 192 192 187 186 186Z" fill={FUR} w={1.3} i={8} />
        </g>

        {/* drawing arm: reaches up to the board, sweeps across it over the picture's time, the paw scribbles */}
        <g className="pdw-arm">
          <g className="pdw-hand">
            <path className="pdw-fill" d="M183 132Q197 128 204 114" stroke={FUR} strokeWidth="15" strokeLinecap="round" fill="none" />
            <path className="pdw-line" d="M190.5 125.5Q198 120 200 112" stroke="#5A534C" strokeWidth="1" fill="none" pathLength={1} style={{ "--i": 12 } as Vars} />
            <g transform="translate(205 112) rotate(-62)" className="pdw-fill">
              <path d="M-4 -2.4H0V2.4H-4Z" fill="#F2B8A2" stroke={INK} strokeWidth="0.9" />
              <path d="M0 -2.4H13V2.4H0Z" fill={CLAY} stroke={INK} strokeWidth="0.9" />
              <path d="M13 -2.4L19 0L13 2.4Z" fill={MANILLA} stroke={INK} strokeWidth="0.9" strokeLinejoin="round" />
              <path d="M17.4 -0.7L19.5 0L17.4 0.7Z" fill={INK} />
            </g>
            <circle className="pdw-fill" cx="204" cy="113" r="7.5" fill={FUR} />
          </g>
        </g>

        {/* chair back, in front of the panda: curved top rail, two posts, lower rail */}
        <Sk d="M117 138Q153 131 190 138V148Q153 141 117 148Z" fill={KRAFT} w={1.6} i={9} />
        <Sk d="M121 146H129V203H121ZM178 146H186V203H178Z" fill={KRAFT} w={1.5} i={9} />
        <Sk d="M129 180H178V186H129Z" fill={KRAFT} w={1.3} i={10} />
        <circle className="pdw-fill" cx="125" cy="142.6" r="1.3" fill={KRAFT_DEEP} />
        <circle className="pdw-fill" cx="182" cy="142.6" r="1.3" fill={KRAFT_DEEP} />

        {/* head from behind: black ears, big round white head, a tuft of fur on top */}
        <g className="pdw-head">
          <g className="pdw-ear pdw-ear-l"><Sk d="M115 74a12.5 12.5 0 1 0 25 0a12.5 12.5 0 1 0-25 0Z" fill={FUR} i={10} /></g>
          <g className="pdw-ear pdw-ear-r"><Sk d="M167 73a12.5 12.5 0 1 0 25 0a12.5 12.5 0 1 0-25 0Z" fill={FUR} i={10} /></g>
          <Sk d="M120 97C120 77 135 64 153 64C172 64 187 77 187 97C187 116 172 129 153 129C135 129 120 116 120 97Z" fill={WHITE} w={2} i={11} />
          <Sk d="M147 66q2-6 6-4q1-4 5-1q4-1 3 4" fill={WHITE} w={1.3} i={12} />
          <path className="pdw-fill" d="M130 84q3-4 7-3M168 82q4-1 7 2M127 110q3 5 8 7M176 108q-2 5-6 7" stroke={CLOUD} strokeWidth="1.1" fill="none" strokeLinecap="round" />
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
