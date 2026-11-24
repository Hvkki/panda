import { useSyncExternalStore } from "react";

/** Look-and-feel settings: kept in this browser. Claude-side settings live on the worker. */
export type Settings = {
  theme: "system" | "light" | "dark";
  textSize: "small" | "medium" | "large";
  width: "narrow" | "wide";
  font: "serif" | "sans";
  showThinking: boolean;
  enterToSend: boolean;
  notifyReplies: boolean;
  defaultModel: string;
  defaultEffort: string;
};
const DEFAULTS: Settings = {
  theme: "system", textSize: "medium", width: "narrow", font: "serif", showThinking: true,
  enterToSend: true, notifyReplies: true, defaultModel: "default", defaultEffort: "medium",
};
const KEY = "panda.settings.v1";
let current: Settings = (() => {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch { return DEFAULTS; }
})();
const subs = new Set<() => void>();

export function updateSettings(patch: Partial<Settings>) {
  current = { ...current, ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(current)); } catch { /* blocked */ }
  applySettings();
  subs.forEach((f) => f());
}
export const getSettings = () => current;
export function useSettings(): Settings {
  return useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => current);
}

const dark = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
export function applySettings() {
  const d = document.documentElement.dataset;
  d.theme = current.theme === "system" ? (dark?.matches ? "dark" : "light") : current.theme;
  d.size = current.textSize;
  d.width = current.width;
  d.font = current.font;
}
dark?.addEventListener("change", applySettings);
