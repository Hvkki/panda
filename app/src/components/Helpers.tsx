import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { gw, runCommand } from "../lib/commands";
import { IconCheck, IconChevron, IconSpinner, IconUsers, IconX } from "./Icons";

/** One helper (Kiro Crew sub-agent) as GET /api/spawn lists it. */
type Helper = {
  id: string; task: string; done: boolean; parent: string; agent?: string; started?: number;
  result?: string; error?: string; stopped?: boolean; outcome?: string;
  turns?: number; last_tool?: string; elapsed?: number; awaiting_approval?: boolean;
  /** Set for remote agents (pjob --remote --agent): the notebook, the model and the commands they ran. */
  remote?: boolean; box?: string; model?: string; steps?: string[];
};

const ago = (s: number) => (s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h`);
const firstLine = (t: string) => (t || "").replace(/\s+/g, " ").trim();

/** The helpers this chat's Panda started, live while they run, with their answers when done. */
export function Helpers({ slot, online, busy }: { slot: string | null; online: boolean; busy: boolean }) {
  const [list, setList] = useState<Helper[]>([]);
  const [open, setOpen] = useState<string>("");
  const [shut, setShut] = useState(false);
  const running = list.some((h) => !h.done);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    if (!slot || !online) return;
    const parent = `dashboard:${slot}`;
    let timer = 0;
    const tick = async () => {
      if (document.hidden) return;  // a tab in the background never polls
      try {
        const [r, rem] = await Promise.all([
          gw<{ agents: Helper[] }>("GET", "/api/spawn").catch(() => null),
          runCommand<{ agents: Helper[] }>("remote.agents", { slot }, 30000).catch(() => null),
        ]);
        if (!alive.current || (!r && !rem)) return;
        setList((prev) => {
          const local = r ? (r.agents || []).filter((h) => h.parent === parent || h.parent?.endsWith(`:${slot}`))
            : prev.filter((h) => !h.remote);
          const remote = rem ? (rem.agents || []).map((h) => ({ ...h, remote: true })) : prev.filter((h) => h.remote);
          return [...local, ...remote].sort((a, b) => (a.started || 0) - (b.started || 0));
        });
      } catch { /* offline or relay hiccup: keep the last list */ }
    };
    tick();
    // Poll only while something can change, and slowly: every poll is a relay command row with the
    // full helper list (~16 KB), and polling every 5 s from open chats overloaded the database.
    if (running) timer = window.setInterval(tick, 20000);
    else if (busy) timer = window.setInterval(tick, 45000);
    return () => { alive.current = false; window.clearInterval(timer); };
  }, [slot, online, busy, running]);

  if (list.length === 0) return null;
  const live = list.filter((h) => !h.done).length;
  return (
    <section className={`helpers ${running ? "live" : ""}`} aria-label="Helpers">
      <button className="helpers-head" onClick={() => setShut((v) => !v)} aria-expanded={!shut}>
        <IconUsers size={15} />
        <b>{list.length === 1 ? "1 helper" : `${list.length} helpers`}</b>
        <span>{live ? `${live} working` : "all done"}</span>
        <i className={`helpers-chev ${shut ? "" : "up"}`} aria-hidden><IconChevron size={15} /></i>
      </button>
      {!shut && (
        <ul>
          {list.map((h) => {
            const state = !h.done ? (h.awaiting_approval ? "wait" : "run") : h.error || h.stopped ? "bad" : "ok";
            const meta = !h.done
              ? [h.awaiting_approval ? "waiting for approval" : h.last_tool ? `using ${h.last_tool}` : "thinking",
                 h.turns ? `${h.turns} steps` : "", h.elapsed ? ago(h.elapsed) : ""].filter(Boolean).join(" · ")
              : h.stopped ? "stopped" : h.error ? "failed" : "done";
            const isOpen = open === h.id;
            return (
              <li key={h.id} className={`helper ${state}`}>
                <button className="helper-row" onClick={() => setOpen(isOpen ? "" : h.id)} aria-expanded={isOpen}>
                  <i className="helper-dot" aria-hidden>
                    {state === "run" || state === "wait" ? <IconSpinner size={13} /> : state === "ok" ? <IconCheck size={12} /> : <IconX size={12} />}
                  </i>
                  <span className="helper-task">{firstLine(h.task)}</span>
                  <small>{h.remote && <em className="helper-remote">remote{h.box ? ` ${h.box}` : ""}</em>}
                    {h.agent ? `${h.agent.replace(/^kirocrew-/, "")} · ` : h.model ? `${h.model.replace(/^claude-/, "")} · ` : ""}{meta}</small>
                </button>
                {isOpen && (
                  <div className="helper-body">
                    <div className="helper-label">Task</div>
                    <p className="helper-text">{h.task}</p>
                    {!!h.steps?.length && <>
                      <div className="helper-label">Steps ({h.turns})</div>
                      <ol className="helper-steps">{h.steps.map((x, i) => <li key={i}><code>{x}</code></li>)}</ol>
                    </>}
                    {h.done && (h.result || h.error) && <>
                      <div className="helper-label">{h.error ? "Error" : "Answer"}</div>
                      <div className="prose small-prose"><ReactMarkdown remarkPlugins={[remarkGfm]}>{(h.error || h.result || "").slice(0, 6000)}</ReactMarkdown></div>
                    </>}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
