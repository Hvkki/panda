import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import * as V from "../lib/video";
import { signedUrl } from "../lib/files";
import { useSettings } from "../lib/settings";
import { PelicanBike, type PelicanStage } from "./PelicanBike";
import { AutopilotDesk, CreditMeter, CreditSheet, RunCard } from "./VideoAutopilot";
import { IconArrowRight, IconEdit, IconImage, IconRetry, IconSparkle, IconTrash, IconX } from "./Icons";
import "./VideoTab.css";

const MODES: { id: V.VideoMode; label: string; hint: string }[] = [
  { id: "clip", label: "Clip", hint: "From words, or a start picture" },
  { id: "frames", label: "Start → End", hint: "Two pictures, Panda films the way between" },
  { id: "orbit", label: "Orbit 360°", hint: "One photo, the camera circles it" },
];
const STYLES = [
  { id: "cinematic", label: "Cinematic", sw: "linear-gradient(135deg,#1f4e5a 10%,#e0894f 90%)" },
  { id: "aura", label: "Aura edit", sw: "radial-gradient(circle at 35% 40%,#ff6a3d,#3a0f0a 72%)" },
  { id: "anime", label: "Anime", sw: "linear-gradient(135deg,#ff9ac8,#7aa7ff)" },
  { id: "product", label: "Product", sw: "linear-gradient(135deg,#fbfbf9,#9aa0a6)" },
  { id: "documentary", label: "Documentary", sw: "linear-gradient(135deg,#6f6047,#cdb98f)" },
  { id: "dreamy", label: "Dreamy", sw: "linear-gradient(135deg,#ffd9e8,#c8e7ff 55%,#fff3c4)" },
];
const IDEAS = [
  "a pelican riding a bicycle along the sea",
  "aura edit of a samurai in the rain",
  "a paper boat racing down a rainy street",
  "a red panda chef flipping pancakes at sunrise",
  "a tiny astronaut watering a plant on the moon",
];
const RATIOS: V.VideoRatio[] = ["9:16", "16:9", "1:1"];
const STAGES: PelicanStage[] = ["queued", "starting", "loading", "reading prompt", "sampling", "decoding", "saving", "done", "failed"];

type Ref = { path: string; url: string; name: string };
type Draft = { mode: V.VideoMode; ratio: V.VideoRatio; secs: number; takes: number; style: string; idea: string;
  script: V.Script | null; by: string; audio: boolean };
const DRAFT_KEY = "panda.video.draft";
const readDraft = (): Partial<Draft> => { try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || "{}"); } catch { return {}; } };

export function VideoPage({ userId, online }: { userId: string; online: boolean }) {
  const { settings, update } = useSettings();
  const saved = useMemo(readDraft, []);
  const [file, setFile] = useState<V.JobsFile | null>(null);
  const [mode, setMode] = useState<V.VideoMode>(saved.mode || "clip");
  const [ratio, setRatio] = useState<V.VideoRatio>(saved.ratio || "9:16");
  const [secs, setSecs] = useState(saved.secs || 5);
  const [takes, setTakes] = useState(saved.takes || 1);
  const [style, setStyle] = useState(saved.style ?? "cinematic");
  const [idea, setIdea] = useState(saved.idea || "");
  const [script, setScript] = useState<V.Script | null>(saved.script || null);
  const [by, setBy] = useState(saved.by || "");
  const [audio, setAudio] = useState(saved.audio ?? true);
  const [first, setFirst] = useState<Ref | null>(null);
  const [last, setLast] = useState<Ref | null>(null);
  const [consent, setConsent] = useState(false);
  const [writing, setWriting] = useState(false);
  const [busy, setBusy] = useState<"" | V.VideoQuality>("");
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<string>("");
  const [sheet, setSheet] = useState(false);
  const [pilot, setPilot] = useState<"auto" | "manual">(() => (localStorage.getItem("panda.video.pilot") as "manual") || "auto");
  useEffect(() => { localStorage.setItem("panda.video.pilot", pilot); }, [pilot]);
  const [ideaIx, setIdeaIx] = useState(0);
  const [, setTick] = useState(0);
  const fileRef = useRef<V.JobsFile | null>(null);
  const hotUntil = useRef(0);
  const queueRef = useRef<HTMLDivElement>(null);
  fileRef.current = file;

  useEffect(() => {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ mode, ratio, secs, takes, style, idea, script, by, audio }));
  }, [mode, ratio, secs, takes, style, idea, script, by, audio]);

  // Gallery + progress: fast while something renders, slow otherwise, paused in a hidden tab.
  const [kick, setKick] = useState(0);
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const tick = async () => {
      try {
        const f = await V.loadJobs(userId);
        if (alive && f) setFile(f);
      } catch { /* keep the last copy */ }
      if (!alive) return;
      const hot = (fileRef.current?.jobs || []).some(V.active) || (fileRef.current?.runs || []).some(V.runActive)
        || Date.now() < hotUntil.current;
      timer = window.setTimeout(tick, document.hidden ? 30000 : hot ? 2000 : 20000);
    };
    tick();
    const wake = () => { if (!document.hidden) { window.clearTimeout(timer); tick(); } };
    document.addEventListener("visibilitychange", wake);
    return () => { alive = false; window.clearTimeout(timer); document.removeEventListener("visibilitychange", wake); };
  }, [userId, kick]);

  // Rotating example in the idea box, and a clock for the GPU status.
  useEffect(() => {
    const id = window.setInterval(() => { setIdeaIx((i) => (i + 1) % IDEAS.length); setTick((t) => t + 1); }, 4000);
    return () => window.clearInterval(id);
  }, []);

  const jobs = useMemo(() => [...(file?.jobs || [])].sort((a, b) => b.created - a.created), [file]);
  // The clip being filmed first, then the line in the order it was sent.
  const byId = useMemo(() => new Map(jobs.map((j) => [j.id, j])), [jobs]);
  const runs = useMemo(() => [...(file?.runs || [])].filter((r) => !r.hidden)
    .sort((a, b) => Number(V.runActive(b)) - Number(V.runActive(a)) || b.created - a.created).slice(0, 4), [file]);
  // Autopilot films show their own render inside their card; "On set" keeps the rest.
  const running = jobs.filter((j) => V.active(j) && !(j.run && runs.some((r) => r.id === j.run))).sort((a, b) =>
    (a.status === "rendering" ? 0 : 1) - (b.status === "rendering" ? 0 : 1) || a.created - b.created);
  const finished = jobs.filter((j) => !V.active(j));
  const month = new Date().toISOString().slice(0, 7);
  const spent = jobs.filter((j) => new Date(j.created * 1000).toISOString().slice(0, 7) === month)
    .reduce((s, j) => s + (j.cost || 0), 0);
  const budget = settings.modalCap ?? 30;
  const [usageNow, setUsageNow] = useState<V.ModalUsage | null>(null);
  const fileUsage = file?.modal || null;
  const usage = usageNow && (!fileUsage || usageNow.at >= fileUsage.at) ? usageNow : fileUsage;
  const gpuWarm = !!file?.gpu?.t && Date.now() / 1000 - (file.gpu.t || 0) < 25 && ["ready", "idle", "busy"].includes(file.gpu.state || "");
  const stats = file?.stats || V.DEFAULT_STATS;
  const rate = file?.rate_usd_s || 0.001001;
  const pDraft = V.priceOf({ mode, quality: "draft", ratio, secs, seeds: takes }, stats, rate, gpuWarm);
  const pFinal = V.priceOf({ mode, quality: "final", ratio, secs, seeds: 1 }, stats, rate, gpuWarm);
  const needs = mode === "frames" ? !(first && last) : mode === "orbit" ? !first : false;
  const pics = !!(first || last);

  const spec = (quality: V.VideoQuality, prompt: string): V.VideoSpec => ({
    mode, quality, ratio, secs, seeds: quality === "final" ? 1 : takes, style, idea: idea.trim(), prompt, audio,
    first: first?.path, last: last?.path, title: idea.trim().slice(0, 70),
  });

  async function write() {
    setErr("");
    if (!idea.trim()) { setErr("Type an idea first."); return; }
    setWriting(true);
    try {
      const r = await V.videoPrompt({ idea, mode, secs, ratio, style, first: first?.path, last: last?.path });
      setScript(V.toScript(r.prompt));
      setBy(r.by);
    } catch (e) {
      setErr(String((e as Error).message || e));
    } finally {
      setWriting(false);
    }
  }

  async function make(quality: V.VideoQuality) {
    setErr("");
    if (!online) { setErr("Start Panda to make videos."); return; }
    if (needs) { setErr(mode === "orbit" ? "Add the photo to orbit." : "Add a start and an end picture."); return; }
    if (pics && !consent) { setErr("Tick the box about the people in your pictures."); return; }
    setBusy(quality);
    try {
      let prompt = script ? V.fromScript(script) : "";
      if (!prompt) {
        if (!idea.trim()) { setErr("Type an idea first."); return; }
        const r = await V.videoPrompt({ idea, mode, secs, ratio, style, first: first?.path, last: last?.path });
        prompt = r.prompt;
        setScript(V.toScript(prompt));
        setBy(r.by);
      }
      const r = await V.videoSubmit(spec(quality, prompt));
      setFile((f) => ({ ...(f || { v: 1, at: Date.now() / 1000, budget, spent, rate_usd_s: rate }), jobs: [...(f?.jobs || []), ...r.jobs] }));
      hotUntil.current = Date.now() + 30000;
      setKick((k) => k + 1);
      window.setTimeout(() => queueRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 120);
    } catch (e) {
      setErr(String((e as Error).message || e));
    } finally {
      setBusy("");
    }
  }

  async function again(j: V.VideoJob, quality: V.VideoQuality, sameSeed: boolean) {
    setErr("");
    if (!online) { setErr("Start Panda to make videos."); return; }
    try {
      const r = await V.videoSubmit({ mode: j.mode, quality, ratio: j.ratio, secs: j.secs, seeds: 1, style: j.style,
        idea: j.idea, prompt: j.prompt, first: j.first || undefined, last: j.last || undefined, title: j.title,
        from: j.id, ...(sameSeed ? { seed: j.seed } : {}) });
      setFile((f) => (f ? { ...f, jobs: [...f.jobs, ...r.jobs] } : f));
      hotUntil.current = Date.now() + 30000;
      setOpen("");
      setKick((k) => k + 1);
      window.setTimeout(() => queueRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 120);
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  }

  async function restart(old: V.VideoRun) {
    setErr("");
    try {
      const res = await V.videoAuto({ idea: old.idea, plan: old.plan, first: old.first || undefined });
      setFile((f) => (f ? { ...f, runs: [...(f.runs || []), res.run] } : f));
      hotUntil.current = Date.now() + 30000;
      setKick((k) => k + 1);
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  }

  function reuse(j: V.VideoJob) {
    setMode(j.mode); setRatio(j.ratio); setSecs(j.secs); setStyle(j.style); setIdea(j.idea);
    setScript(V.toScript(j.prompt)); setBy("yours"); setOpen("");
    window.scrollTo({ top: 0, behavior: "smooth" });
    document.querySelector(".vs-page")?.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function remove(j: V.VideoJob) {
    if (!confirm(`Delete “${j.title || "this video"}”?`)) return;
    setFile((f) => (f ? { ...f, jobs: f.jobs.filter((x) => x.id !== j.id) } : f));
    setOpen("");
    try { await V.videoDelete(j.id); } catch (e) { setErr(String((e as Error).message || e)); setKick((k) => k + 1); }
  }

  async function stop(j: V.VideoJob) {
    try { await V.videoCancel(j.id); setKick((k) => k + 1); } catch (e) { setErr(String((e as Error).message || e)); }
  }

  async function pick(which: "first" | "last", f: File | undefined) {
    if (!f) return;
    setErr("");
    try {
      const path = await V.uploadRef(userId, f);
      const ref = { path, url: URL.createObjectURL(f), name: f.name };
      if (which === "first") setFirst(ref); else setLast(ref);
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  }

  const openJob = jobs.find((j) => j.id === open) || null;

  return (
    <div className="page vs-page">
      <header className="vs-head">
        <div className="vs-title">
          <Reel spinning={running.length > 0} />
          <div>
            <h1 className="serif">Video studio</h1>
            <p>Tell Panda an idea. It directs, films, checks the take and makes the final.</p>
          </div>
        </div>
        <div className="vs-head-side">
          <span className={`vs-gpu ${gpuWarm ? "warm" : ""}`} title={file?.gpu?.gpu || "RTX PRO 6000 on your Modal"}>
            <i />{gpuWarm ? "GPU warm · starts at once" : "GPU asleep · wakes in ~30 s"}
          </span>
          <CreditMeter usage={usage} cap={budget} onOpen={() => setSheet((v) => !v)} />
        </div>
      </header>
      {sheet && (
        <CreditSheet usage={usage} cap={budget} online={online} onClose={() => setSheet(false)}
          onCap={(v) => { update({ modalCap: v }); setSheet(false); }} onRefreshed={(u) => setUsageNow(u)} />
      )}
      <div className="ap-switch" role="tablist" aria-label="How to make a video">
        <button role="tab" aria-selected={pilot === "auto"} className={pilot === "auto" ? "on" : ""} onClick={() => setPilot("auto")}>
          <b>Autopilot</b><small>idea in, film out</small>
        </button>
        <button role="tab" aria-selected={pilot === "manual"} className={pilot === "manual" ? "on" : ""} onClick={() => setPilot("manual")}>
          <b>Director's chair</b><small>you pick every setting</small>
        </button>
        <i className="ap-switch-thumb" aria-hidden />
      </div>
      {!online && <div className="vs-offline">Panda is off. Your videos are all here; press Start to make new ones.</div>}

      <div className="vs-grid">
        {pilot === "auto" ? (
          <AutopilotDesk userId={userId} online={online} usage={usage} stats={stats} rate={rate} warm={gpuWarm}
            onStarted={(run) => {
              setFile((f) => (f ? { ...f, runs: [...(f.runs || []), run] } : { v: 2, at: Date.now() / 1000, jobs: [], runs: [run], budget, spent, rate_usd_s: rate }));
              hotUntil.current = Date.now() + 30000;
              setKick((k) => k + 1);
              window.setTimeout(() => queueRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 120);
            }} />
        ) : (
        <section className="vs-slate" aria-label="New video">
          <div className="vs-slate-top" aria-hidden><span /><span /><span /><span /><span /><span /><span /></div>
          <div className="vs-modes" role="radiogroup" aria-label="Type of video">
            {MODES.map((m) => (
              <button key={m.id} role="radio" aria-checked={mode === m.id} className={`vs-mode ${mode === m.id ? "on" : ""}`} onClick={() => setMode(m.id)}>
                <ModeArt mode={m.id} />
                <b>{m.label}</b>
                <small>{m.hint}</small>
              </button>
            ))}
          </div>

          <label className="vs-idea">
            <span className="vs-label">Your idea</span>
            <textarea rows={3} value={idea} maxLength={1500} onChange={(e) => setIdea(e.target.value)}
              placeholder={mode === "orbit" ? "what is in the photo, e.g. a vintage camera on a wooden table" : IDEAS[ideaIx]}
              onKeyDown={(e) => { if (e.key === "Tab" && !idea && mode !== "orbit") { e.preventDefault(); setIdea(IDEAS[ideaIx]); } }} />
            {!idea && mode !== "orbit" && <button type="button" className="vs-try" onClick={() => setIdea(IDEAS[ideaIx])}>Use this example</button>}
          </label>

          {mode !== "clip" || first ? null : (
            <button type="button" className="vs-addpic" onClick={() => document.getElementById("vs-first")?.click()}>
              <IconImage size={16} /> Start from a picture (optional)
            </button>
          )}
          {(mode !== "clip" || first) && (
            <div className="vs-pics">
              <PicSlot label={mode === "orbit" ? "Photo" : "Start"} pic={first} id="vs-first" onPick={(f) => pick("first", f)} onClear={() => setFirst(null)} />
              {mode === "frames" && <><span className="vs-pics-arrow" aria-hidden><IconArrowRight size={18} /></span>
                <PicSlot label="End" pic={last} id="vs-last" onPick={(f) => pick("last", f)} onClear={() => setLast(null)} /></>}
            </div>
          )}
          <input id="vs-first" type="file" accept="image/*" hidden onChange={(e) => { pick("first", e.target.files?.[0]); e.target.value = ""; }} />
          {pics && (
            <label className="vs-consent"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              No real person is shown, or they agreed to be in this video.</label>
          )}

          <div className="vs-row">
            <div className="vs-field">
              <span className="vs-label">Style</span>
              <div className="vs-styles">
                {STYLES.map((s) => (
                  <button key={s.id} className={`vs-style ${style === s.id ? "on" : ""}`} onClick={() => setStyle(style === s.id ? "" : s.id)}>
                    <i style={{ background: s.sw }} />{s.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="vs-row vs-row-3">
            <div className="vs-field">
              <span className="vs-label">Shape</span>
              <div className="vs-ratios" role="radiogroup" aria-label="Shape">
                {RATIOS.map((r) => (
                  <button key={r} role="radio" aria-checked={(mode === "orbit" ? "1:1" : ratio) === r} disabled={mode === "orbit"}
                    className={`vs-ratio ${(mode === "orbit" ? "1:1" : ratio) === r ? "on" : ""}`} onClick={() => setRatio(r)}>
                    <i className={`r${r.replace(":", "x")}`} /><span>{r}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="vs-field vs-len">
              <span className="vs-label">Length <b>{mode === "orbit" ? 3 : secs} s</b></span>
              <input type="range" min={2} max={10} step={1} value={mode === "orbit" ? 3 : secs} disabled={mode === "orbit"}
                onChange={(e) => setSecs(Number(e.target.value))} aria-label="Length in seconds" />
              <div className="vs-ticks" aria-hidden>{Array.from({ length: 9 }, (_, i) => <i key={i} className={(mode === "orbit" ? 3 : secs) >= i + 2 ? "on" : ""} />)}</div>
            </div>
            <div className="vs-field">
              <span className="vs-label">Takes</span>
              <div className="vs-takes">
                {[1, 2, 3].map((n) => (
                  <button key={n} className={`vs-take ${takes === n ? "on" : ""}`} onClick={() => setTakes(n)} aria-label={`${n} takes`}>{n}</button>
                ))}
              </div>
            </div>
          </div>
          <label className="vs-sound"><input type="checkbox" checked={audio} onChange={(e) => setAudio(e.target.checked)} /> Sound and music</label>

          <div className={`vs-script ${script ? "has" : ""}`}>
            <div className="vs-script-head">
              <span className="vs-label">Script</span>
              {script && <span className={`vs-by ${by}`}>{by === "writer" ? "written by Panda" : by === "yours" ? "from your video" : by === "template" ? "simple version" : "edited"}</span>}
              <span className="vs-flex" />
              {script && <button className="vs-link" onClick={() => { setScript(null); setBy(""); }}>Clear</button>}
              <button className="btn vs-write" onClick={write} disabled={writing || !online || !idea.trim()}>
                <IconSparkle size={15} /> {writing ? "Writing…" : script ? "Rewrite" : "Write the script"}
              </button>
            </div>
            {writing && <div className="vs-writing"><i /><i /><i /> Panda is writing shots, camera moves and sound</div>}
            {script && !writing && (
              <div className="vs-script-body">
                <ScriptField label="Picture" hint="shots, action, camera" value={script.picture} rows={5}
                  onChange={(v) => { setScript({ ...script, picture: v }); setBy("edited"); }} />
                <ScriptField label="Sound" hint="what you hear in the scene" value={script.sound} rows={2}
                  onChange={(v) => { setScript({ ...script, sound: v }); setBy("edited"); }} />
                <ScriptField label="Music" hint="or none" value={script.music} rows={1}
                  onChange={(v) => { setScript({ ...script, music: v }); setBy("edited"); }} />
              </div>
            )}
            {!script && !writing && <p className="vs-script-empty">Panda turns your idea into shots, camera moves and sound. You can edit every word before filming. Or skip it: “Make” writes it for you.</p>}
          </div>

          <div className="vs-ticket">
            <TicketHalf title={mode === "orbit" ? "Orbit" : `Draft${takes > 1 ? ` × ${takes}` : ""}`} sub={`${pDraft.w}×${pDraft.h} · ${pDraft.steps} steps`}
              usd={(mode === "orbit" ? pFinal : pDraft).usd} secs={(mode === "orbit" ? pFinal : pDraft).secs}
              action={busy === "draft" ? "Starting…" : mode === "orbit" ? "Make orbit" : "Make draft"} primary
              disabled={!!busy || !online} onClick={() => make(mode === "orbit" ? "final" : "draft")} />
            {mode !== "orbit" && <>
              <div className="vs-perf" aria-hidden />
              <TicketHalf title="Final" sub={`${pFinal.w}×${pFinal.h} · ${pFinal.steps} steps`} usd={pFinal.usd} secs={pFinal.secs}
                action={busy === "final" ? "Starting…" : "Make final"} disabled={!!busy || !online} onClick={() => make("final")} />
            </>}
          </div>
          {err && <div className="vs-err" role="alert"><IconX size={14} /> {err}</div>}
          <p className="vs-fine">Prices are estimates for your Modal account (RTX PRO 6000, ${(rate * 3600).toFixed(2)}/h) and learn from every video. MiniMax H3 videos may not be used in the EU, US, UK or South Korea without MiniMax's permission.</p>
        </section>
        )}

        <section className="vs-side">
          <div ref={queueRef} />
          {runs.length > 0 && (
            <div className="ap-runs">
              <h2>Autopilot</h2>
              {runs.map((r) => (
                <RunCard key={r.id} run={r} jobs={byId} online={online} onWatch={(id) => setOpen(id)}
                  onChange={() => { hotUntil.current = Date.now() + 20000; setKick((k) => k + 1); }}
                  onAgain={(old) => restart(old)} />
              ))}
            </div>
          )}
          {running.length > 0 && (
            <div className="vs-queue">
              <h2>On set</h2>
              {running.map((j, i) => (
                <div key={j.id} className={`vs-q ${i === 0 ? "now" : ""}`}>
                  <PelicanBike pct={j.pct || 0} stage={stageOf(j)} label={captionOf(j, i)} compact={i > 0} />
                  <div className="vs-q-meta">
                    <span>{j.title || "Video"} · {j.quality} · {j.secs} s · ≈{V.money(j.est?.usd || 0)}</span>
                    <button className="vs-link" onClick={() => stop(j)} disabled={!online}>Stop</button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="vs-gallery-head">
            <h2>Your videos</h2>
            <span>{finished.filter((j) => j.status === "done").length} clips</span>
          </div>
          {finished.length === 0 && running.length === 0 && runs.length === 0 ? (
            <EmptyReel />
          ) : (
            <div className="vs-gallery">
              {finished.map((j) => <Card key={j.id} job={j} onOpen={() => setOpen(j.id)} onRetry={() => again(j, j.quality, true)} onDelete={() => remove(j)} />)}
            </div>
          )}
        </section>
      </div>

      {openJob && (
        <Screening job={openJob} online={online} onClose={() => setOpen("")} onFinal={() => again(openJob, "final", true)}
          onRetake={() => again(openJob, openJob.quality, false)} onEdit={() => reuse(openJob)} onDelete={() => remove(openJob)} />
      )}
    </div>
  );
}

function stageOf(j: V.VideoJob): PelicanStage {
  if (j.status === "queued" && (!j.stage || j.stage === "queued")) return "queued";
  return (STAGES.includes(j.stage as PelicanStage) ? j.stage : "sampling") as PelicanStage;
}

function captionOf(j: V.VideoJob, place: number): string {
  const left = j.eta ? ` · about ${V.duration(j.eta)} left` : "";
  if (j.status === "queued" && place > 0) return `In line, number ${place + 1}`;
  switch (j.stage) {
    case "starting": return "Waking the GPU" + left;
    case "loading": return "Loading the film stock" + left;
    case "reading prompt": return "Reading the script" + left;
    case "sampling": return `Filming, pass ${Math.max(1, j.step)} of ${j.steps}` + left;
    case "decoding": return "Developing the frames" + left;
    case "saving": return "Splicing in the sound";
    default: return "Waiting for the GPU" + left;
  }
}

function TicketHalf(p: { title: string; sub: string; usd: number; secs: number; action: string; primary?: boolean; disabled?: boolean; onClick: () => void }) {
  return (
    <div className={`vs-half ${p.primary ? "primary" : ""}`}>
      <div className="vs-half-top"><b>{p.title}</b><span>{p.sub}</span></div>
      <div className="vs-price"><b>≈{V.money(p.usd)}</b><span>~{V.duration(p.secs)}</span></div>
      <button className={`btn ${p.primary ? "primary" : ""}`} disabled={p.disabled} onClick={p.onClick}>{p.action}</button>
    </div>
  );
}

function ScriptField({ label, hint, value, rows, onChange }: { label: string; hint: string; value: string; rows: number; onChange: (v: string) => void }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { // grow with the text
    const el = ref.current;
    if (el) { el.style.height = "auto"; el.style.height = `${el.scrollHeight + 2}px`; }
  }, [value]);
  return (
    <label className="vs-sf">
      <span><b>{label}</b> <small>{hint}</small></span>
      <textarea ref={ref} rows={rows} value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} />
    </label>
  );
}

function PicSlot({ label, pic, id, onPick, onClear }: { label: string; pic: Ref | null; id: string; onPick: (f?: File) => void; onClear: () => void }) {
  return (
    <div className={`vs-pic ${pic ? "has" : ""}`}>
      {pic ? (
        <>
          <img src={pic.url} alt={label} />
          <button className="vs-pic-x" onClick={onClear} aria-label={`Remove ${label} picture`}><IconX size={14} /></button>
        </>
      ) : (
        <label htmlFor={id === "vs-first" ? "vs-first" : `${id}-in`}>
          <IconImage size={20} /><span>{label}</span>
        </label>
      )}
      {id !== "vs-first" && <input id={`${id}-in`} type="file" accept="image/*" hidden onChange={(e) => { onPick(e.target.files?.[0]); e.target.value = ""; }} />}
      <em>{label}</em>
    </div>
  );
}

function Poster({ job, hover }: { job: V.VideoJob; hover: boolean }) {
  const [img, setImg] = useState("");
  const [vid, setVid] = useState("");
  useEffect(() => {
    let alive = true;
    if (job.poster) signedUrl(job.poster).then((u) => alive && setImg(u)).catch(() => {});
    return () => { alive = false; };
  }, [job.poster]);
  useEffect(() => {
    let alive = true;
    if (hover && job.video && !vid) signedUrl(job.video).then((u) => alive && setVid(u)).catch(() => {});
    return () => { alive = false; };
  }, [hover, job.video, vid]);
  return (
    <>
      {img ? <img src={img} alt="" loading="lazy" /> : <span className="vs-poster-blank" />}
      {hover && vid && <video src={vid} muted autoPlay loop playsInline />}
    </>
  );
}

function Card({ job, onOpen, onRetry, onDelete }: { job: V.VideoJob; onOpen: () => void; onRetry: () => void; onDelete: () => void }) {
  const [hover, setHover] = useState(false);
  if (job.status !== "done") {
    return (
      <div className="vs-card failed" style={{ aspectRatio: `${job.w} / ${job.h}` }}>
        <div className="vs-failed">
          <b>{job.status === "cancelled" ? "Stopped" : "Didn't work"}</b>
          <span>{job.error || "Something went wrong."}</span>
          <div>
            <button className="btn" onClick={onRetry}><IconRetry size={14} /> Try again</button>
            <button className="icon-btn" onClick={onDelete} aria-label="Delete"><IconTrash size={15} /></button>
          </div>
        </div>
        <span className="vs-title-chip">{job.title || "Video"}</span>
      </div>
    );
  }
  return (
    <button className="vs-card" style={{ aspectRatio: `${job.w} / ${job.h}` }} onClick={onOpen}
      onPointerEnter={(e) => e.pointerType === "mouse" && setHover(true)} onPointerLeave={() => setHover(false)}>
      <Poster job={job} hover={hover} />
      <span className={`vs-badge ${job.quality}`}>{job.mode === "orbit" ? "Orbit" : job.quality === "final" ? "Final" : "Draft"}</span>
      <span className="vs-playbtn" aria-hidden><svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z" /></svg></span>
      <span className="vs-card-foot"><span>{job.title || "Video"}</span><span>{job.secs} s · {V.money(job.cost || 0)}</span></span>
    </button>
  );
}

function Screening(p: { job: V.VideoJob; online: boolean; onClose: () => void; onFinal: () => void; onRetake: () => void; onEdit: () => void; onDelete: () => void }) {
  const { job } = p;
  const [url, setUrl] = useState("");
  const [dl, setDl] = useState("");
  const [showScript, setShowScript] = useState(false);
  useEffect(() => {
    if (!job.video) return;
    signedUrl(job.video).then(setUrl).catch(() => {});
    signedUrl(job.video, `${(job.title || "video").replace(/[^\w -]+/g, "").slice(0, 40) || "video"}.mp4`).then(setDl).catch(() => {});
  }, [job.video, job.title]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && p.onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [p]);
  const sc = V.toScript(job.prompt);
  const made = job.billed_secs ? `made in ${V.duration(job.billed_secs)}` : "";
  return (
    <div className="vs-screen" role="dialog" aria-modal aria-label={job.title || "Video"} onClick={p.onClose}>
      <div className="vs-screen-box" onClick={(e) => e.stopPropagation()}>
        <button className="vs-screen-x icon-btn" onClick={p.onClose} aria-label="Close"><IconX /></button>
        <div className={`vs-screen-stage r${job.w > job.h ? "wide" : job.w < job.h ? "tall" : "sq"}`}>
          {url ? <video src={url} controls autoPlay loop playsInline /> : <div className="vs-poster-blank" />}
        </div>
        <div className="vs-screen-info">
          <h3 className="serif">{job.title || "Video"}</h3>
          <div className="vs-chips">
            <span className={`vs-badge static ${job.quality}`}>{job.mode === "orbit" ? "Orbit" : job.quality === "final" ? "Final" : "Draft"}</span>
            <span>{job.w}×{job.h}</span><span>{job.secs} s</span><span>{V.money(job.cost || 0)}{job.est ? ` (guess ${V.money(job.est.usd)})` : ""}</span>
            {made && <span>{made}{job.cold ? " · GPU woke up" : ""}</span>}<span>seed {job.seed}</span>
          </div>
          <div className="vs-screen-actions">
            {job.quality === "draft" && job.mode !== "orbit" && (
              <Action primary onClick={p.onFinal} disabled={!p.online} icon={<IconSparkle size={15} />}>Make the final of this take</Action>
            )}
            <Action onClick={p.onRetake} disabled={!p.online} icon={<IconRetry size={15} />}>New take</Action>
            <Action onClick={p.onEdit} icon={<IconEdit size={15} />}>Edit the script</Action>
            {dl && <a className="btn" href={dl} download>Download</a>}
            <button className="icon-btn" onClick={p.onDelete} aria-label="Delete"><IconTrash size={16} /></button>
          </div>
          <button className="vs-link" onClick={() => setShowScript((v) => !v)}>{showScript ? "Hide the script" : "Show the script"}</button>
          {showScript && (
            <dl className="vs-script-read">
              <dt>Picture</dt><dd>{sc.picture}</dd>
              <dt>Sound</dt><dd>{sc.sound}</dd>
              <dt>Music</dt><dd>{sc.music}</dd>
            </dl>
          )}
        </div>
      </div>
    </div>
  );
}

function Action({ children, onClick, disabled, primary, icon }: { children: ReactNode; onClick: () => void; disabled?: boolean; primary?: boolean; icon?: ReactNode }) {
  return <button className={`btn ${primary ? "primary" : ""}`} onClick={onClick} disabled={disabled}>{icon}{children}</button>;
}

/** Little moving pictures on the three mode tiles. */
function ModeArt({ mode }: { mode: V.VideoMode }) {
  if (mode === "clip") {
    return (
      <svg className="vs-art clip" viewBox="0 0 64 44" aria-hidden>
        <rect x="8" y="16" width="48" height="24" rx="3" className="a-body" />
        <g className="a-arm"><path d="M8 16l46-9 2 8-46 9z" className="a-top" /><path d="M16 14l6-7M28 12l6-7M40 10l6-7" className="a-stripe" /></g>
        <circle cx="32" cy="28" r="5" className="a-dot" />
      </svg>
    );
  }
  if (mode === "frames") {
    return (
      <svg className="vs-art frames" viewBox="0 0 64 44" aria-hidden>
        <rect x="4" y="10" width="22" height="24" rx="3" className="a-body" />
        <rect x="38" y="10" width="22" height="24" rx="3" className="a-body" />
        <path d="M28 22h8" className="a-stripe" />
        <circle cx="15" cy="22" r="5" className="a-dot a-move" />
      </svg>
    );
  }
  return (
    <svg className="vs-art orbit" viewBox="0 0 64 44" aria-hidden>
      <ellipse cx="32" cy="26" rx="24" ry="9" className="a-ring" />
      <rect x="25" y="13" width="14" height="16" rx="3" className="a-body" />
      <circle cx="0" cy="0" r="3.5" className="a-dot a-orbit" />
    </svg>
  );
}

function Reel({ spinning }: { spinning: boolean }) {
  return (
    <svg className={`vs-reel ${spinning ? "spin" : ""}`} viewBox="0 0 48 48" aria-hidden>
      <circle cx="24" cy="24" r="21" className="r-outer" />
      {[0, 1, 2, 3, 4].map((i) => {
        const a = (i / 5) * Math.PI * 2 - Math.PI / 2;
        return <circle key={i} cx={24 + 11 * Math.cos(a)} cy={24 + 11 * Math.sin(a)} r="4.6" className="r-hole" />;
      })}
      <circle cx="24" cy="24" r="3" className="r-hub" />
    </svg>
  );
}

function EmptyReel() {
  return (
    <div className="vs-empty">
      <PelicanBike pct={0} stage="queued" label="Your first video will ride in here" />
      <p>Try “a pelican riding a bicycle along the sea”: a draft costs about ten cents.</p>
    </div>
  );
}
