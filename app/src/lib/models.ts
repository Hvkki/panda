export type ModelInfo = { id: string; name: string; blurb: string; credits: number };

// Two main models, like the reference picker; everything else lives under "More models".
export const MAIN_MODELS: ModelInfo[] = [
  { id: "claude-opus-5.5", name: "Opus 5.5", blurb: "Deepest thinking for complex work", credits: 2.0 },
  { id: "claude-sonnet-5.5", name: "Sonnet 5.5", blurb: "Fast and smart for everyday tasks", credits: 1.3 },
  { id: "claude-haiku-5.5", name: "Haiku 5.5", blurb: "Fastest for quick answers", credits: 0.06 },
];

export const MORE_MODELS: ModelInfo[] = [
  { id: "claude-opus-5", name: "Opus 5", blurb: "", credits: 2.2 },
  { id: "claude-sonnet-5", name: "Sonnet 5", blurb: "", credits: 1.3 },
  { id: "claude-opus-4.8", name: "Opus 4.8", blurb: "", credits: 2.2 },
  { id: "claude-opus-4.7", name: "Opus 4.7", blurb: "", credits: 2.2 },
  { id: "claude-opus-4.6", name: "Opus 4.6", blurb: "", credits: 2.2 },
  { id: "claude-sonnet-4.6", name: "Sonnet 4.6", blurb: "", credits: 1.3 },
  { id: "claude-haiku-4.5", name: "Haiku 4.5", blurb: "", credits: 0.4 },
  { id: "gpt-5.6-sol", name: "GPT 5.6 Sol", blurb: "", credits: 4.4 },
  { id: "gpt-5.6-terra", name: "GPT 5.6 Terra", blurb: "", credits: 2.2 },
  { id: "gpt-5.6-luna", name: "GPT 5.6 Luna", blurb: "", credits: 0.6 },
  { id: "deepseek-3.2", name: "DeepSeek 3.2", blurb: "", credits: 0.25 },
  { id: "glm-5", name: "GLM-5", blurb: "", credits: 0.5 },
  { id: "minimax-m2.5", name: "MiniMax M2.5", blurb: "", credits: 0.25 },
  { id: "auto", name: "Auto", blurb: "Kiro picks per task", credits: 1.0 },
];

export const ALL_MODELS = [...MAIN_MODELS, ...MORE_MODELS];
export const modelName = (id: string) => ALL_MODELS.find((m) => m.id === id)?.name ?? id;

export const EFFORTS = [
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium", note: "Recommended" },
  { id: "high", label: "High" },
  { id: "extra", label: "Extra" },
  { id: "max", label: "Max", warn: "Uses much more credit" },
] as const;
export const effortLabel = (id: string) => EFFORTS.find((e) => e.id === id)?.label ?? id;
