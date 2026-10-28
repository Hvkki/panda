import { useEffect, useState } from "react";
import type { Message } from "../lib/supabase";
import { describeStep } from "../lib/steps";
import { IconClock, IconList, IconSpinner, IconX } from "./Icons";

/** The worker's live note on a long answer (Haiku names the step and guesses when it ends). */
export type TimelineNote = {
  now?: string; next?: string;
  at?: number;          // seconds into the answer when the note was written
  step?: number;        // index of the step the note is about
  step_end?: number | null;   // seconds into the answer when that step should end
  done_end?: number | null;   // seconds into the answer when the whole answer should end
  typ?: number | null;  // how long this kind of step usually takes (from past answers), seconds
  why?: string;         // Haiku: why the current step takes as long as it does
  run?: MachineView;    // what is running on Panda's machine right now (worker reads /proc every ~10 s)
};
export type MachineCmd = { cmd: string; secs?: number | null; kids?: string; job?: string; last?: string };
export type MachineView = { load?: number; cpus?: number; cmds?: MachineCmd[] };

/** The command itself, short: drops "cd dir &&" and env prefixes, first line only. */
function cmdText(raw: string, n = 90): string {
  let t = raw.replace(/^(running|ran|tool)\s*:\s*/i, "").split("\n")[0].trim();
  t = t.replace(/^(?:(?:cd\s+\S+|source\s+\S+|set\s+[-+]\w+)\s*(?:&&|;)\s*|[A-Z_][A-Z0-9_]*=\S*\s+)+/, "");
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
}

const KEY = "panda.timeline.open";

function dur(s: number): string {
  s = Math.max(0, Math.round(s));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}
function left(s: number): string {
  if (s < 45) return "under a minute";
  const m = Math.round(s / 60);
  return m < 60 ? `about ${m} min` : `about ${Math.floor(m / 60)} h ${m % 60} min`;
}
const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/**
 * Right-side timeline of what a running answer is doing: every step with its start and duration,
 * the current step with a plain-words note, and when that step (and the whole answer) may finish.
 */
export function Timeline({ m, onClose }: { m: Message; onClose: () => void }) {
  const [now, setNow] = useState(Date.now());
  const live = m.status === "streaming" || m.status === "waiting";
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [live]);

  const tools = m.meta.tools || [];
  const at = (m.meta.steps_at || []) as number[];
  const tl = (m.meta.tl || {}) as TimelineNote;
  const startMs = new Date(m.created_at).getTime();
  // Elapsed from the reply's start; never behind what the worker last reported.
  const el = live ? Math.max((now - startMs) / 1000, m.meta.secs || 0) : m.meta.secs || 0;
  // tools/steps_at are the last 30 of each; line them up from the end.
  const off = at.length - tools.length;
  const startOf = (i: number) => (i + off >= 0 && i + off < at.length ? at[i + off] : undefined);
  const cur = tools.length - 1;
  const curStart = startOf(cur);
  const nSteps = typeof m.meta.n_steps === "number" ? m.meta.n_steps : tools.length;
  const sameStep = tl.step !== undefined && tools.length > 0 && tl.step === nSteps - 1;

  let stepEta: { secs: number; over: boolean; guess: "ai" | "usual" } | null = null;
  if (live && curStart !== undefined) {
    if (sameStep && typeof tl.step_end === "number") stepEta = { secs: tl.step_end - el, over: el > tl.step_end, guess: "ai" };
    else if (typeof tl.typ === "number") stepEta = { secs: curStart + tl.typ - el, over: el > curStart + tl.typ, guess: "usual" };
  }
  const doneEta = live && typeof tl.done_end === "number" ? tl.done_end - el : null;

  return (
    <aside className="timeline" aria-label="What Panda is doing">
      <div className="timeline-head">
        <IconList size={16} /> <b>Timeline</b>
        <span className="muted small">{live ? `running ${dur(el)}` : `took ${dur(el)}`}</span>
        <button className="icon-btn" onClick={onClose} aria-label="Hide timeline"><IconX size={15} /></button>
      </div>

      {live && (
        <div className="timeline-now">
          <div className="tl-label">Now</div>
          <div className="tl-now-text">{tl.now && sameStep ? tl.now : tools.length ? describeStep(tools[cur]).label : "Thinking"}</div>
          {stepEta ? (
            <div className={`tl-eta ${stepEta.over ? "over" : ""}`}>
              <IconClock size={13} />
              {stepEta.over ? <>Taking longer than {stepEta.guess === "ai" ? "expected" : "usual"}</>
                : <>This step: {left(stepEta.secs)} left · ends ~{clock(now + stepEta.secs * 1000)}</>}
            </div>
          ) : curStart !== undefined && (
            <div className="tl-eta muted"><IconClock size={13} /> Guessing the end once the step has run a bit</div>
          )}
          {doneEta !== null && (
            <div className="tl-eta muted">
              {doneEta > 0 ? <>Whole answer: {left(doneEta)} · ~{clock(now + doneEta * 1000)}</> : <>Whole answer: should be close</>}
            </div>
          )}
          {tl.why && sameStep && <div className="tl-why small">Why: {tl.why}</div>}
          {tl.next && sameStep && <div className="tl-next muted small">Next: {tl.next}</div>}
        </div>
      )}

      {live && !!tl.run?.cmds?.length && (
        <div className="tl-machine">
          <div className="tl-label">
            Running on Panda's machine
            {typeof tl.run.load === "number" && tl.run.cpus ? (
              <span className={`tl-load ${tl.run.load > tl.run.cpus * 1.5 ? "busy" : ""}`}>
                {tl.run.load > tl.run.cpus * 1.5 ? "very busy" : tl.run.load > tl.run.cpus ? "busy" : "free"} · load {tl.run.load} / {tl.run.cpus} CPUs
              </span>
            ) : null}
          </div>
          {tl.run.cmds.map((c, i) => (
            <div key={i} className="tl-cmd-row" title={c.cmd}>
              <code className="tl-cmd">{c.job ? <b>{c.job}: </b> : null}{cmdText(c.cmd)}</code>
              <span className="muted small">
                {typeof c.secs === "number" ? dur(c.secs) : ""}{c.kids ? ` · ${c.kids}` : ""}
              </span>
              {c.last && <span className="tl-cmd-log small">{c.last}</span>}
            </div>
          ))}
        </div>
      )}

      <ol className="timeline-list">
        {tools.map((raw, i) => {
          const s = describeStep(raw);
          const st = startOf(i);
          const end = i < cur ? startOf(i + 1) : live ? el : m.meta.secs;
          const running = live && i === cur;
          return (
            <li key={i} className={`${running ? "running" : "done"} ${s.error ? "err" : ""}`} title={s.detail}>
              <span className="tl-dot">{running ? <IconSpinner size={12} /> : null}</span>
              <span className="tl-icon">{s.icon}</span>
              <span className="tl-step">{s.label}</span>
              {/^(Running a command|Working)$/.test(s.label) && <code className="tl-cmd tl-step-cmd">{cmdText(raw, 70)}</code>}
              <span className="tl-time muted small">
                {st !== undefined ? clock(startMs + st * 1000) : ""}
                {st !== undefined && end !== undefined ? ` · ${dur(end - st)}` : ""}
              </span>
            </li>
          );
        }).reverse()}
        {!tools.length && <li className="muted small">No steps yet.</li>}
      </ol>
    </aside>
  );
}

export function useTimelineOpen(): [boolean, (v: boolean) => void] {
  // Wide screens show it by default; on a phone it starts as a small pill above the chat bar.
  const [open, setOpen] = useState(() => {
    const v = localStorage.getItem(KEY);
    return v === null ? window.innerWidth > 900 : v === "1";
  });
  return [open, (v) => { setOpen(v); localStorage.setItem(KEY, v ? "1" : "0"); }];
}
