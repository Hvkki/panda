import type { ReactNode } from "react";
import {
  IconAlert, IconBook, IconClock, IconCloud, IconDoc, IconEye, IconGlobe, IconImage, IconPen, IconSearch,
  IconSlides, IconSparkle, IconTable, IconTerminal, IconTool, IconUsers,
} from "../components/Icons";

export type Step = { icon: ReactNode; label: string; detail: string; error?: boolean };

const base = (p: string) => p.split(/[\\/]/).pop() || p;
const short = (s: string, n = 70) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

/**
 * Turn a raw tool line from the agent ("Running: python3 …/make_doc.py notes.md ~/outputs/Report.pdf",
 * "Reading SKILL.md:1", "@kirocrew-cron/cron_add") into a friendly step with an icon.
 */
export function describeStep(raw: string): Step {
  const t = raw.replace(/^(running|ran|tool)\s*:\s*/i, "").trim();
  const low = t.toLowerCase();
  const detail = short(t, 140);
  const s = (icon: ReactNode, label: string, extra?: Partial<Step>): Step => ({ icon, label, detail, ...extra });

  if (low.startsWith("error:")) return s(<IconAlert size={15} />, short(t.slice(6).trim() || "Something failed"), { error: true });
  if (low.includes("stopped by you")) return s(<IconAlert size={15} />, "Stopped");

  // Panda skills
  const doc = t.match(/make_doc\.py\s+\S+\s+(\S+\.(pdf|docx|pptx))/i);
  if (doc) {
    const ext = doc[2].toLowerCase();
    return s(ext === "pptx" ? <IconSlides size={15} /> : <IconDoc size={15} />,
      `Creating ${ext === "pdf" ? "PDF" : ext === "docx" ? "Word document" : "slides"} · ${base(doc[1])}`);
  }
  if (low.includes("make_doc.py")) return s(<IconDoc size={15} />, "Creating document");
  if (low.includes("find_images.py")) {
    const q = t.match(/find_images\.py\s+["']([^"']+)["']/);
    return s(<IconImage size={15} />, q ? `Searching images · ${short(q[1], 40)}` : "Searching images");
  }
  const pb = t.match(/pb(?:\.py)?\s+(open|click|type|press|scroll|text|shot|snap|tabs?|newtab|handoff|back|reload|wait)\b\s*(.*)/i);
  if (pb || low.includes("panda-browser")) {
    const verb = (pb?.[1] || "").toLowerCase();
    const arg = (pb?.[2] || "").replace(/^["']|["']$/g, "");
    const what: Record<string, string> = {
      open: `Opening ${short(arg.replace(/^https?:\/\//, ""), 40)}`, click: "Clicking on the page", type: "Typing on the page",
      press: "Pressing a key", scroll: "Scrolling", text: "Reading the page", snap: "Looking at the page",
      shot: "Taking a screenshot", handoff: "Waiting for you in the browser", back: "Going back", reload: "Reloading",
      wait: "Waiting for the page", tabs: "Checking tabs", tab: "Switching tab", newtab: "Opening a new tab",
    };
    return s(<IconGlobe size={15} />, what[verb] || "Using the browser");
  }
  const drive = t.match(/drive\.py\s+(put|get|ls|find|rm|link|usage)\s*(\S*)/i);
  if (drive) {
    const verb = drive[1].toLowerCase();
    const label = { put: `Saving to Google Drive · ${base(drive[2])}`, get: `Getting from Google Drive · ${base(drive[2])}`,
      ls: "Listing Google Drive", find: "Searching Google Drive", rm: "Removing from Google Drive",
      link: "Getting a Drive link", usage: "Checking Drive space" }[verb] || "Using Google Drive";
    return s(<IconCloud size={15} />, label);
  }

  // Kiro Crew / kiro-cli tools
  if (/cron_(add|update)/.test(low)) return s(<IconClock size={15} />, "Scheduling a task");
  if (/cron_(list|remove|pause|resume|trigger)/.test(low)) return s(<IconClock size={15} />, "Managing scheduled tasks");
  if (/spawn_(run|continue)/.test(low)) return s(<IconUsers size={15} />, "Starting a helper");
  if (/spawn_/.test(low)) return s(<IconUsers size={15} />, "Checking helpers");
  if (/memory_recall|search_chat_history|get_chat_session/.test(low)) return s(<IconSparkle size={15} />, "Remembering");
  if (/learn_add|memory_(save|store|add)/.test(low)) return s(<IconSparkle size={15} />, "Saving to memory");
  if (/skill\.md|skill_search|skill_read/.test(low)) return s(<IconBook size={15} />, "Reading a skill");
  if (/tool_search/.test(low)) return s(<IconTool size={15} />, "Loading a tool");
  if (/web_search|searching the web|search(ing)?:/.test(low)) {
    const q = t.match(/(?:web_search|search(?:ing)?(?: the web)?)\s*[:\-]?\s*["']?([^"']+)/i);
    return s(<IconSearch size={15} />, q && q[1].length > 2 ? `Searching the web · ${short(q[1], 40)}` : "Searching the web");
  }
  if (/web_fetch|fetching|https?:\/\//.test(low)) {
    const u = t.match(/https?:\/\/([^\s/]+)/);
    return s(<IconGlobe size={15} />, u ? `Reading ${u[1]}` : "Reading a web page");
  }
  if (/^(reading|fs_read|read)\b/.test(low)) return s(<IconEye size={15} />, `Reading ${short(base(t.replace(/^\S+\s+/, "").replace(/:\d+.*$/, "")), 40)}`);
  if (/^(writing|creating|fs_write|write|editing)\b/.test(low)) return s(<IconPen size={15} />, `Writing ${short(base(t.replace(/^\S+\s+/, "")), 40)}`);
  if (/\.(csv|xlsx)\b/.test(low) || /pandas|matplotlib/.test(low)) return s(<IconTable size={15} />, "Working with data");
  if (/^(grep|glob|find)\b/.test(low)) return s(<IconSearch size={15} />, "Looking through files");
  if (/execute_bash|^(\$|python3?|bash|sh|pip|npm|npx|curl|ls|cat|mkdir|cd|cp|mv|zip|unzip|tar)\b/.test(low))
    return s(<IconTerminal size={15} />, "Running a command");
  if (t.startsWith("@")) return s(<IconTool size={15} />, short(t.replace(/^@[^/]+\//, "").replace(/_/g, " "), 50));
  return s(<IconTool size={15} />, short(t, 60) || "Working");
}

/** Human summary for a finished turn: "Used 4 tools · Created PDF". */
export function summarize(steps: Step[]): string {
  const real = steps.filter((x) => !x.error);
  if (!real.length) return "Done";
  const made = real.find((x) => x.label.startsWith("Creating"));
  return `${real.length} step${real.length === 1 ? "" : "s"}${made ? ` · ${made.label.split(" · ")[0].replace("Creating", "Created")}` : ""}`;
}
