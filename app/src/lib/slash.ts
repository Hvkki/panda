import { useEffect, useState } from "react";
import { crewCommands, type CrewCommands } from "./commands";

/**
 * Everything the composer's "/" menu offers:
 *  - Panda commands, handled in the app (new chat, stop, model, side questions...),
 *  - every Kiro Crew chat command (GET /api/slash-commands), sent to the agent as typed,
 *  - Panda's prompt shortcuts, which just fill in a starting sentence.
 */
export type SlashGroup = "panda" | "crew" | "shortcut";
export type SlashCmd = {
  name: string;
  args?: string;          // usage hint, e.g. "<objective>"
  desc: string;
  group: SlashGroup;
  /** client: the app runs it · send: sent to Kiro Crew as a message · template: fills the composer */
  kind: "client" | "send" | "template";
  needsArgs?: boolean;    // picking it inserts "/name " so you can type the rest
  /** Needs Kiro Crew's kiro-cli engine; the KAS engine answers "doesn't work with KAS". */
  cli?: boolean;
  /** Can't run from Panda at all (the reason is shown). */
  off?: string;
  confirm?: string;
  template?: string;
  aliases?: string[];
};

const P = (name: string, desc: string, extra: Partial<SlashCmd> = {}): SlashCmd => ({ name, desc, group: "panda", kind: "client", ...extra });
const C = (name: string, desc: string, extra: Partial<SlashCmd> = {}): SlashCmd => ({ name, desc, group: "crew", kind: "send", ...extra });
const S = (name: string, template: string): SlashCmd => ({ name, desc: template.trim(), group: "shortcut", kind: "template", template });

export const PANDA_COMMANDS: SlashCmd[] = [
  P("/new", "Start a new chat"),
  P("/stop", "Stop the answer in progress"),
  P("/btw", "Ask a side question without interrupting Panda", { args: "<question>", needsArgs: true, aliases: ["/side"] }),
  P("/model", "Switch this chat's model (or open the picker)", { args: "[name]" }),
  P("/effort", "Set reasoning effort: low, medium, high, extra, max", { args: "[level]" }),
  P("/title", "Rename this chat", { args: "<name>", needsArgs: true }),
  P("/credits", "See your credit usage"),
  P("/browser", "Open Panda's live browser"),
  P("/settings", "Open settings"),
];

// Kiro Crew's own commands, with its English descriptions (Kiro Crew 0.11, chat_utils.py) as a
// fallback until the live list arrives. `cli`: forwarded to kiro-cli, so the KAS engine refuses it.
export const CREW_COMMANDS: SlashCmd[] = [
  C("/agent", "Switch or manage the active agent", { args: "[name]" }),
  C("/changelog", "Show the release changelog", { cli: true }),
  C("/clear", "Clear conversation history", { confirm: "Clear Panda's memory of this conversation? Your messages stay on screen." }),
  C("/code", "Open code intelligence tools", { cli: true }),
  C("/compact", "Compact conversation to free context", { args: "[what to keep]" }),
  C("/context", "Manage context files and token usage", { cli: true, args: "[sub-command]" }),
  C("/experiment", "Toggle experimental features", { cli: true }),
  C("/goal", "Set a standing goal the agent works toward across turns", { args: "<objective> | status | clear", needsArgs: true }),
  C("/help", "Show available commands", { cli: true }),
  C("/hooks", "View configured context hooks", { cli: true }),
  C("/issue", "Report an issue or bug", { cli: true }),
  C("/kb", "Search your knowledge library and answer with it", { args: "<question>", needsArgs: true }),
  C("/logdump", "Dump session logs to a file", { cli: true }),
  C("/mcp", "Show configured MCP servers", { cli: true }),
  C("/plain", "Where things stand — or add a topic", { args: "[topic]" }),
  C("/prompts", "List or invoke saved prompts & agent SOPs", { args: "[list | get <name>]" }),
  C("/tools", "Show available tools", { cli: true }),
  C("/usage", "Show billing and usage information", { cli: true }),
  C("/workflow", "List or run a saved workflow by name", { args: "[name] [input]" }),
  C("/onboarding", "Replay setup import and the welcome tour", { off: "Only in Kiro Crew's desktop dashboard" }),
];

export const SHORTCUTS: SlashCmd[] = [
  S("/post", "Write a social post about "),
  S("/research", "Research and summarize, with sources: "),
  S("/pdf", "Create a clean PDF document about "),
  S("/plan", "Make a step-by-step plan for "),
  S("/remind", "Every weekday at 9:00, remind me to "),
  S("/explain", "Explain simply, with an example: "),
];

// Names handled by the app even though Kiro Crew also has them.
const PANDA_OWNS = new Set(["/model", "/side", "/btw"]);
const CACHE_KEY = "panda.crew.commands";
let fetchedThisLoad = false;

/** Kiro Crew's live command list (cached, refreshed once per app load while Panda is online). */
export function useCrewCommands(online: boolean): CrewCommands | null {
  const [data, setData] = useState<CrewCommands | null>(() => {
    try { return JSON.parse(localStorage.getItem(CACHE_KEY) || "null"); } catch { return null; }
  });
  useEffect(() => {
    if (!online || fetchedThisLoad) return;
    fetchedThisLoad = true;
    crewCommands().then((d) => {
      if (!d?.commands?.length) return;
      localStorage.setItem(CACHE_KEY, JSON.stringify(d));
      setData(d);
    }).catch(() => { fetchedThisLoad = false; });
  }, [online]);
  return data;
}

/** Merge Panda's commands, the live Kiro Crew list and the shortcuts into one catalog. */
export function buildCatalog(live: CrewCommands | null): SlashCmd[] {
  const crew = CREW_COMMANDS.map((c) => ({ ...c }));
  for (const row of live?.commands || []) {
    const name = row.name.startsWith("/") ? row.name : `/${row.name}`;
    if (PANDA_OWNS.has(name)) continue;
    const known = crew.find((c) => c.name === name);
    if (known) { if (row.description) known.desc = row.description; }
    else crew.push(C(name, row.description || "Kiro Crew command"));
  }
  if (live?.backend === "kas") {
    for (const c of crew) if (c.cli && !c.off) c.off = "Needs Kiro Crew's kiro-cli engine (it runs on KAS now)";
  }
  crew.sort((a, b) => Number(!!a.off) - Number(!!b.off) || a.name.localeCompare(b.name));
  return [...PANDA_COMMANDS, ...crew, ...SHORTCUTS];
}

/** Commands matching what was typed after "/" (prefix first, then description words). */
export function matchCommands(catalog: SlashCmd[], typed: string): SlashCmd[] {
  const q = typed.toLowerCase().replace(/^\//, "");
  if (!q) return catalog;
  const names = (c: SlashCmd) => [c.name, ...(c.aliases || [])].map((n) => n.slice(1).toLowerCase());
  const prefix = catalog.filter((c) => names(c).some((n) => n.startsWith(q)));
  const words = catalog.filter((c) => !prefix.includes(c) && q.length > 2 && c.desc.toLowerCase().includes(q));
  return [...prefix, ...words];
}

/** "/name rest" → the catalog entry (by name or alias) and the arguments. */
export function parseCommand(catalog: SlashCmd[], text: string): { cmd: SlashCmd; args: string } | null {
  const m = text.trim().match(/^(\/[a-z][\w-]*)(?:\s+([\s\S]*))?$/i);
  if (!m) return null;
  const name = m[1].toLowerCase();
  const cmd = catalog.find((c) => c.name === name || c.aliases?.includes(name));
  return cmd ? { cmd, args: (m[2] || "").trim() } : null;
}
