// Claude Code models (what `claude --model` accepts) and effort levels (`--effort`).
// Aliases always follow the newest model of that family; full ids pin one version.
export const MODELS = [
  { id: "default", name: "Default", blurb: "Whatever your Claude plan picks" },
  { id: "opus", name: "Opus 5.5", blurb: "Deepest thinking for complex work · 1M context" },
  { id: "sonnet", name: "Sonnet 5.5", blurb: "Fast and smart for everyday tasks · 1M context" },
  { id: "haiku", name: "Haiku 5.5", blurb: "Fastest and lightest on your plan · 1M context" },
  { id: "claude-fable-5-2", name: "Fable 5.2", blurb: "Most capable, uses your plan fastest (if your plan has it)" },
  { id: "opusplan", name: "Opus plan", blurb: "Opus while planning, Sonnet while doing" },
];
export const modelName = (id: string) => MODELS.find((m) => m.id === id)?.name ?? id;

export const EFFORTS = [
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium", note: "Recommended" },
  { id: "high", label: "High" },
  { id: "xhigh", label: "Extra" },
  { id: "max", label: "Max", warn: "Uses much more of your plan" },
] as const;
export const effortLabel = (id: string) => EFFORTS.find((e) => e.id === id)?.label ?? id;

/** What Claude Code's built-in slash commands do (skills and custom commands have no blurb). */
export const COMMAND_INFO: Record<string, string> = {
  compact: "Summarize the conversation to free up context",
  context: "Show what is using the context window",
  clear: "Start a new chat",
  model: "Choose the model",
  effort: "Choose the effort level",
  usage: "Show token and plan usage",
  init: "Write a CLAUDE.md that describes this folder",
  "security-review": "Review pending changes for security issues",
  "code-review": "Review the current changes for bugs",
  simplify: "Clean up the changed code",
  review: "Review a pull request",
  agents: "List the available subagents",
  mcp: "Show MCP servers",
  config: "Show Claude Code settings",
  "output-style": "Change the reply style",
  recap: "Recap this session",
  insights: "Insights about your sessions",
  goal: "Set a goal for this session",
  rename: "Rename the session",
  advisor: "Configure the advisor",
  autocompact: "Turn auto-compaction on or off",
  fast: "Toggle fast mode",
  import: "Import a conversation",
  heapdump: "Write a memory heap dump",
  "auto-mode-setup": "Set up auto mode",
  "list-agents": "List running agents",
  "team-onboarding": "Team onboarding guide",
  loop: "Run a prompt repeatedly",
  batch: "Run a task over many items",
  debug: "Debug a problem step by step",
  verify: "Verify the work so far",
  "reload-skills": "Reload skills",
};
/** Commands Panda answers itself instead of sending them to Claude. */
export const LOCAL_COMMANDS = new Set(["model", "effort", "usage", "clear"]);
