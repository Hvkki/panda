import { useEffect, useMemo, useState } from "react";
import * as V from "../lib/video";
import { signedUrl } from "../lib/files";
import { PelicanBike, type PelicanStage } from "./PelicanBike";
import { IconCheck, IconImage, IconRetry, IconX } from "./Icons";

// ── Autopilot: one idea in. Panda directs, writes, films a draft, checks it, films the final. ──

const IDEAS = [
  "a pelican riding a bicycle along the sea",
  "a red panda chef flipping pancakes at sunrise",
  "aura edit of a samurai in the rain",
  "a paper boat racing down a rainy street",
  "a tiny astronaut watering a plant on the moon",
  "a perfume bottle spinning in golden light",
];

type Ref = { path: string; url: string; name: string };
const STYLE_NAME: Record<string, string> = { cinematic: "Cinematic", aura: "Aura edit", anime: "Anime", product: "Product",
  documentary: "Documentary", dreamy: "Dreamy" };

export function AutopilotDesk(p: {
  userId: string; online: boolean; usage: V.ModalUsage | null; stats: V.Stats; rate: number; warm: boolean;
  onStarted: (run: V.VideoRun) => void;
}) {
  const [idea, setIdea] = useState(() => localStorage.getItem("panda.video.auto.idea") || "");
  const [plan, setPlan] = useState<"full" | "quick">(() => (localStorage.getItem("panda.video.auto.plan") as "quick") || "full");
  const [pic, setPic] = useState<Ref | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ix, setIx] = useState(0);
  useEffect(() => { localStorage.setItem("panda.video.auto.idea", idea); }, [idea]);
  useEffect(() => { localStorage.setItem("panda.video.auto.plan", plan); }, [plan]);
  useEffect(() => {
    const id = window.setInterval(() => setIx((i) => (i + 1) % IDEAS.length), 3800);
    return () => window.clearInterval(id);
  }, []);

  // The director picks the length, so price a typical 6 s clip and the 8 s worst case.
  const price = (kind: "full" | "quick", secs: number, warm: boolean) => {
    const d = V.priceOf({ mode: "clip", quality: "draft", ratio: "9:16", secs, seeds: 1 }, p.stats, p.rate, warm);
    const f = V.priceOf({ mode: "clip", quality: "final", ratio: "9:16", secs, seeds: 1 }, p.stats, p.rate, true);
    return kind === "full" ? { usd: d.usd + f.usd, high: d.high + f.high, secs: d.secs + f.secs + 25 } : { usd: d.usd, high: d.high, secs: d.secs + 25 };
  };
  const full = price("full", 6, p.warm), quick = price("quick", 6, p.warm);
  const worst = price(plan, 8, p.warm).high;
  const left = p.usage ? p.usage.left : null;
  const short = left !== null && worst > left;

  async function go() {
    setErr("");
    const text = idea.trim();
    if (!text) { setErr("Tell Panda the idea first."); return; }
    if (!p.online) { setErr("Start Panda to make videos."); return; }
    if (pic && !consent) { setErr("Tick the box about the people in your picture."); return; }
    setBusy(true);
    try {
      const r = await V.videoAuto({ idea: text, plan, first: pic?.path });
      p.onStarted(r.run);
      setIdea(""); setPic(null); setConsent(false);
    } catch (e) {
      setErr(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  }

  async function pick(f?: File) {
    if (!f) return;
    setErr("");
    try {
      const path = await V.uploadRef(p.userId, f);
      setPic({ path, url: URL.createObjectURL(f), name: f.name });
    } catch (e) { setErr(String((e as Error).message || e)); }
  }

  return (
    <section className="ap-desk" aria-label="Autopilot">
      <div className="ap-finder">
        <i className="ap-corner tl" /><i className="ap-corner tr" /><i className="ap-corner bl" /><i className="ap-corner br" />
        <div className="ap-finder-top">
          <span className={`ap-rec ${idea.trim() ? "on" : ""}`}><i />REC</span>
          <span className="ap-tc">AUTOPILOT</span>
        </div>
        <label className="ap-idea">
          <span className="sr-only">Your idea</span>
          <textarea rows={3} maxLength={1500} value={idea} onChange={(e) => setIdea(e.target.value)} placeholder={IDEAS[ix]}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); go(); }
              if (e.key === "Tab" && !idea) { e.preventDefault(); setIdea(IDEAS[ix]); }
            }} />
        </label>
        <div className="ap-finder-foot">
          {pic ? (
            <span className="ap-pic">
              <img src={pic.url} alt="Start picture" />
              <span>Starts on this picture</span>
              <button className="icon-btn" onClick={() => setPic(null)} aria-label="Remove the picture"><IconX size={14} /></button>
            </span>
          ) : (
            <label className="ap-addpic">
              <IconImage size={15} /> Start from a picture
              <input type="file" accept="image/*" hidden onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
            </label>
          )}
          {!idea && <button className="ap-try" onClick={() => setIdea(IDEAS[ix])}>Use this idea</button>}
        </div>
      </div>
      {pic && (
        <label className="vs-consent"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          No real person is shown, or they agreed to be in this video.</label>
      )}

      <div className="ap-plans" role="radiogroup" aria-label="How far Panda goes">
        <PlanCard on={plan === "full"} onClick={() => setPlan("full")} title="Full film"
          steps={["Draft", "Check", "Final"]} usd={full.usd} secs={full.secs} />
        <PlanCard on={plan === "quick"} onClick={() => setPlan("quick")} title="Quick look"
          steps={["Draft", "Check"]} usd={quick.usd} secs={quick.secs} />
      </div>

      <button className="ap-go" onClick={go} disabled={busy || !p.online || !idea.trim()}>
        <Clapper busy={busy} />
        <span>{busy ? "Calling action…" : "Roll camera"}</span>
        <small>{short ? `needs up to ${V.money(worst)}, only ${V.money(left || 0)} left` : `up to ${V.money(worst)}`}</small>
      </button>
      {err && <div className="vs-err" role="alert"><IconX size={14} /> {err}</div>}

      <ol className="ap-decides" aria-label="What Panda decides">
        <li><b>Shape</b><span>9:16 · 16:9 · 1:1</span></li>
        <li><b>Length</b><span>3 to 10 s</span></li>
        <li><b>Look</b><span>style + light</span></li>
        <li><b>Script</b><span>shots, camera, sound</span></li>
        <li><b>Check</b><span>dark or frozen? retake</span></li>
      </ol>
      <p className="vs-fine">Panda directs on its own and stops if a film would go past your Modal credit. MiniMax H3 videos may not be used in the EU, US, UK or South Korea without MiniMax's permission.</p>
    </section>
  );
}

function PlanCard(p: { on: boolean; onClick: () => void; title: string; steps: string[]; usd: number; secs: number }) {
  return (
    <button role="radio" aria-checked={p.on} className={`ap-plan ${p.on ? "on" : ""}`} onClick={p.onClick}>
      <span className="ap-plan-top"><b>{p.title}</b><i className="ap-radio" aria-hidden /></span>
      <span className="ap-plan-steps">{p.steps.map((s, i) => <span key={s}>{i > 0 && <em aria-hidden>›</em>}{s}</span>)}</span>
      <span className="ap-plan-price"><b>≈{V.money(p.usd)}</b><small>~{p.secs >= 120 ? `${Math.round(p.secs / 60)} min` : V.duration(p.secs)}</small></span>
    </button>
  );
}

function Clapper({ busy }: { busy: boolean }) {
  return (
    <svg className={`ap-clap ${busy ? "snap" : ""}`} viewBox="0 0 32 28" aria-hidden>
      <rect x="3" y="11" width="26" height="15" rx="2" className="c-body" />
      <g className="c-arm"><path d="M3 11 27 5l1 4.5L4 15z" className="c-top" /><path d="M9 9.5l3-4.3M16 8l3-4.3M23 6.3l3-4.3" className="c-stripe" /></g>
    </svg>
  );
}

// ── one film in progress (or done): the pipeline, the director's notes and the live render ──

const STEPS: { id: V.RunStep; label: string }[] = [
  { id: "director", label: "Direct" }, { id: "script", label: "Script" }, { id: "draft", label: "Draft" },
  { id: "check", label: "Check" }, { id: "final", label: "Final" },
];
const ORDER: V.RunStep[] = ["director", "script", "draft", "check", "final", "done"];

export function RunCard(p: { run: V.VideoRun; jobs: Map<string, V.VideoJob>; online: boolean;
  onWatch: (id: string) => void; onAgain: (r: V.VideoRun) => void; onChange: () => void }) {
  const { run } = p;
  const [notes, setNotes] = useState(false);
  const [err, setErr] = useState("");
  const live = V.runActive(run);
  const steps = run.plan === "quick" ? STEPS.slice(0, 4) : STEPS;
  const reached = ORDER.indexOf(run.step === "failed" || run.step === "cancelled" ? lastReal(run) : run.step);
  const draft = run.phases?.draft ? p.jobs.get(run.phases.draft) : undefined;
  const final = run.phases?.final ? p.jobs.get(run.phases.final) : undefined;
  const filming = [final, draft].find((j) => j && V.active(j));
  const best = run.best ? p.jobs.get(run.best) : undefined;
  const c = run.choice;
  const cost = run.cost ?? run.jobs.reduce((s, id) => s + (p.jobs.get(id)?.cost || 0), 0);

  async function stop() {
    setErr("");
    try { await V.videoAutoCancel(run.id); p.onChange(); } catch (e) { setErr(String((e as Error).message || e)); }
  }
  async function dismiss() {
    try { await V.videoAutoDismiss(run.id); p.onChange(); } catch (e) { setErr(String((e as Error).message || e)); }
  }

  return (
    <article className={`ap-run ${run.status}`} aria-live={live ? "polite" : undefined}>
      <header className="ap-run-head">
        <div>
          <h3 className="serif">{run.title || "Untitled"}</h3>
          <p>“{run.idea.length > 110 ? run.idea.slice(0, 108) + "…" : run.idea}”</p>
        </div>
        {live ? <button className="vs-link" onClick={stop} disabled={!p.online}>Stop</button>
          : <button className="icon-btn" onClick={dismiss} disabled={!p.online} aria-label="Hide this film"><IconX size={15} /></button>}
      </header>

      <ol className="ap-steps">
        {steps.map((s) => {
          const i = ORDER.indexOf(s.id);
          const state = run.status === "done" || i < reached ? "done" : i === reached
            ? (run.status === "failed" ? "bad" : run.status === "cancelled" ? "off" : "now") : "wait";
          return (
            <li key={s.id} className={state} aria-current={state === "now" ? "step" : undefined}>
              <i>{state === "done" ? <IconCheck size={12} /> : state === "bad" ? <IconX size={12} /> : null}</i>
              <span>{s.label}</span>
            </li>
          );
        })}
      </ol>

      {c && (
        <div className="ap-notes">
          <div className="ap-chips">
            <span><i className={`ap-ratio r${c.ratio.replace(":", "x")}`} />{c.ratio}</span>
            <span>{c.secs} s</span>
            <span>{STYLE_NAME[c.style] || "Natural"}</span>
            <span>{c.audio ? "Sound + music" : "Silent"}</span>
            {run.by === "guess" && <span className="ap-guess" title="The director model didn't answer, so Panda used simple rules">simple rules</span>}
          </div>
          {run.why && <p className="ap-why">{run.why}</p>}
        </div>
      )}

      {live && !filming && run.status === "directing" && (
        <div className="ap-thinking"><i /><i /><i /> {run.log[run.log.length - 1]?.text || "Directing"}</div>
      )}
      {filming && (
        <div className="ap-live">
          <PelicanBike pct={filming.pct || 0} stage={stageOf(filming)} label={captionOf(filming)} />
          {final && draft?.status === "done" && <DraftPeek job={draft} onOpen={() => p.onWatch(draft.id)} />}
        </div>
      )}

      {run.status === "done" && best && (
        <button className="ap-result" style={{ aspectRatio: `${best.w} / ${best.h}` }} onClick={() => p.onWatch(best.id)}>
          <Still job={best} />
          <span className="vs-playbtn" aria-hidden><svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z" /></svg></span>
          <span className="ap-result-foot"><span>{best.quality === "final" ? "Final" : "Draft"} · {best.secs} s</span><span>{V.money(cost)} total</span></span>
        </button>
      )}
      {(run.status === "failed" || run.status === "cancelled") && (
        <div className="ap-failed">
          <span>{run.status === "failed" ? run.error || "It didn't work." : "Stopped."}{cost > 0 ? ` Used ${V.money(cost)}.` : ""}</span>
          <button className="btn" onClick={() => p.onAgain(run)} disabled={!p.online}><IconRetry size={14} /> Try again</button>
        </div>
      )}
      {err && <div className="vs-err" role="alert"><IconX size={14} /> {err}</div>}

      {run.log.length > 0 && (
        <>
          <button className="vs-link ap-log-toggle" onClick={() => setNotes((v) => !v)} aria-expanded={notes}>
            {notes ? "Hide Panda's notes" : `Panda's notes (${run.log.length})`}
          </button>
          {notes && (
            <ul className="ap-log">
              {run.log.map((l, i) => <li key={i}><time>{clock(l.t)}</time>{l.text}</li>)}
              {run.prompt && <li className="ap-log-script"><time>script</time><pre>{V.toScript(run.prompt).picture}</pre></li>}
            </ul>
          )}
        </>
      )}
    </article>
  );
}

function lastReal(run: V.VideoRun): V.RunStep {
  const real = [...run.log].reverse().find((l) => ORDER.includes(l.step));
  return real?.step || "director";
}

function DraftPeek({ job, onOpen }: { job: V.VideoJob; onOpen: () => void }) {
  return (
    <button className="ap-peek" onClick={onOpen} style={{ aspectRatio: `${job.w} / ${job.h}` }} aria-label="Watch the draft">
      <Still job={job} />
      <span>Draft</span>
    </button>
  );
}

function Still({ job }: { job: V.VideoJob }) {
  const [img, setImg] = useState("");
  useEffect(() => {
    let alive = true;
    if (job.poster) signedUrl(job.poster).then((u) => alive && setImg(u)).catch(() => {});
    return () => { alive = false; };
  }, [job.poster]);
  return img ? <img src={img} alt="" /> : <span className="vs-poster-blank" />;
}

const STAGES: PelicanStage[] = ["queued", "starting", "loading", "reading prompt", "sampling", "decoding", "saving", "done", "failed"];
function stageOf(j: V.VideoJob): PelicanStage {
  if (j.status === "queued" && (!j.stage || j.stage === "queued")) return "queued";
  return (STAGES.includes(j.stage as PelicanStage) ? j.stage : "sampling") as PelicanStage;
}
function captionOf(j: V.VideoJob): string {
  const what = j.quality === "final" ? "final" : "draft";
  const left = j.eta ? ` · about ${V.duration(j.eta)} left` : "";
  switch (j.stage) {
    case "starting": return "Waking the GPU" + left;
    case "loading": return "Loading the film stock" + left;
    case "reading prompt": return "Reading the script" + left;
    case "sampling": return `Filming the ${what}, pass ${Math.max(1, j.step)} of ${j.steps}` + left;
    case "decoding": return "Developing the frames" + left;
    case "saving": return "Splicing in the sound";
    default: return `The ${what} waits for the GPU` + left;
  }
}
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

// ── the Modal credit meter: what Modal itself billed this month, out of the $30 it gives ──

export function CreditMeter({ usage, cap, onOpen }: { usage: V.ModalUsage | null; cap: number; onOpen: () => void }) {
  const total = Math.max(cap, 0.01);
  const seg = useMemo(() => {
    if (!usage) return null;
    const pct = (v: number) => `${Math.max(0, Math.min(100, (v / total) * 100))}%`;
    return { other: pct(usage.other), video: pct(usage.panda + usage.pending), booked: pct(usage.booked) };
  }, [usage, total]);
  const now = usage ? usage.now + usage.booked : 0;
  const level = now / total > 0.9 ? "red" : now / total > 0.7 ? "amber" : "";
  return (
    <button className={`ap-meter ${level}`} onClick={onOpen} title="Modal credit this month">
      <span className="ap-meter-text">
        <span>Modal</span>
        {usage ? <><b>{V.money(usage.now)}</b> of {V.money(cap)}</> : <em>reading the bill…</em>}
      </span>
      <span className="ap-bar" aria-hidden>
        {seg && <><i className="o" style={{ width: seg.other }} /><i className="v" style={{ width: seg.video }} /><i className="b" style={{ width: seg.booked }} /></>}
      </span>
    </button>
  );
}

export function CreditSheet(p: { usage: V.ModalUsage | null; cap: number; online: boolean; onCap: (v: number) => void;
  onClose: () => void; onRefreshed: (u: V.ModalUsage) => void }) {
  const u = p.usage;
  const [cap, setCap] = useState(String(p.cap));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function refresh() {
    setBusy(true); setErr("");
    try { p.onRefreshed(await V.videoUsage()); } catch (e) { setErr(String((e as Error).message || e)); } finally { setBusy(false); }
  }
  const ago = u ? Math.max(0, Math.round((Date.now() / 1000 - u.at) / 60)) : 0;
  const resets = new Date(Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth() + 1, 1))
    .toLocaleDateString([], { day: "numeric", month: "short" });
  return (
    <div className="ap-sheet" role="dialog" aria-label="Modal credit">
      <div className="ap-sheet-head">
        <h3 className="serif">Modal credit</h3>
        <button className="icon-btn" onClick={p.onClose} aria-label="Close"><IconX size={15} /></button>
      </div>
      {u ? (
        <>
          <div className="ap-big"><b>{V.money(Math.max(0, p.cap - u.now - u.booked))}</b><span>left of {V.money(p.cap)} · resets {resets}</span></div>
          <table className="ap-table">
            <tbody>
              <tr><td><i className="sw v" />Videos</td><td>{V.money(u.panda)}</td></tr>
              {u.pending > 0 && <tr><td><i className="sw v" />Videos Modal hasn't counted yet</td><td>{V.money(u.pending)}</td></tr>}
              <tr><td><i className="sw o" />Other Modal apps</td><td>{V.money(u.other)}</td></tr>
              {u.booked > 0 && <tr><td><i className="sw b" />Booked for videos filming now</td><td>{V.money(u.booked)}</td></tr>}
              <tr className="sum"><td>Used this month</td><td>{V.money(u.now)}</td></tr>
            </tbody>
          </table>
          <p className="ap-src">{u.source === "modal" ? `From Modal's bill, ${ago ? `${ago} min ago` : "just now"}. Modal counts by the hour.` : "Modal's bill couldn't be read, so this is Panda's own count."}</p>
        </>
      ) : <p className="ap-src">Panda reads Modal's bill when it starts.</p>}
      <form className="ap-cap" onSubmit={(e) => { e.preventDefault(); p.onCap(Math.max(0, Number(cap) || 0)); }}>
        <label>Stop new videos at $<input inputMode="decimal" value={cap} onChange={(e) => setCap(e.target.value)} /></label>
        <button className="btn primary" type="submit">Save</button>
        <button className="btn" type="button" onClick={refresh} disabled={busy || !p.online}>{busy ? "Reading…" : "Read the bill now"}</button>
      </form>
      <small className="ap-src">Modal's free plan gives $30 a month. The stop line counts all your Modal apps, not just videos.</small>
      {err && <div className="vs-err" role="alert"><IconX size={14} /> {err}</div>}
    </div>
  );
}
