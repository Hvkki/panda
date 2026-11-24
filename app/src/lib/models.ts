// Claude Code model aliases and effort levels (what `claude --model` and `--effort` accept).
export const MODELS = [
  { id: "default", name: "Default", blurb: "Whatever your Claude plan picks" },
  { id: "opus", name: "Opus", blurb: "Deepest thinking for complex work" },
  { id: "sonnet", name: "Sonnet", blurb: "Fast and smart for everyday tasks" },
  { id: "haiku", name: "Haiku", blurb: "Fastest for quick answers" },
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
