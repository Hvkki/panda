import { useSyncExternalStore } from "react";

/** Look-and-feel settings: kept in this browser. Claude-side settings live on the worker. */
export type Mode = "system" | "light" | "dark";
export type LightPalette = "paper" | "sand";
export type DarkPalette = "graphite" | "midnight" | "mocha" | "terminal";
export type Settings = {
  theme: Mode;
  lightPalette: LightPalette;
  darkPalette: DarkPalette;
  accent: string;
  uiFont: "inter" | "geist" | "plex" | "system" | "mono";
  font: "sans" | "serif" | "mono";        // replies
  codeFont: "jetbrains" | "geist-mono" | "plex-mono" | "system";
  textSize: "small" | "medium" | "large" | "xlarge";
  density: "compact" | "cozy" | "roomy";
  radius: "sharp" | "soft" | "round";
  width: "narrow" | "wide" | "full";
  layout: "transcript" | "bubbles";
  toolsOpen: boolean;
  showUsage: boolean;
  showThinking: boolean;
  enterToSend: boolean;
  notifyReplies: boolean;
  defaultModel: string;
  defaultEffort: string;
};
const DEFAULTS: Settings = {
  theme: "system", lightPalette: "paper", darkPalette: "graphite", accent: "#d97757",
  uiFont: "inter", font: "sans", codeFont: "jetbrains", textSize: "medium", density: "cozy", radius: "soft",
  width: "narrow", layout: "transcript", toolsOpen: false, showUsage: true, showThinking: true,
  enterToSend: true, notifyReplies: true, defaultModel: "default", defaultEffort: "medium",
};

export const PALETTES: { id: LightPalette | DarkPalette; name: string; dark: boolean; bg: string; panel: string; text: string }[] = [
  { id: "paper", name: "Paper", dark: false, bg: "#ffffff", panel: "#f5f5f3", text: "#1d1d1b" },
  { id: "sand", name: "Sand", dark: false, bg: "#faf7f2", panel: "#efe8dd", text: "#2a2622" },
  { id: "graphite", name: "Graphite", dark: true, bg: "#111214", panel: "#1d1e22", text: "#e8e8ea" },
  { id: "midnight", name: "Midnight", dark: true, bg: "#0b1020", panel: "#161d34", text: "#e4e8f5" },
  { id: "mocha", name: "Mocha", dark: true, bg: "#1c1a18", panel: "#2d2925", text: "#ece6de" },
  { id: "terminal", name: "Terminal", dark: true, bg: "#000000", panel: "#121212", text: "#d7dad7" },
];
export const ACCENTS = ["#d97757", "#e5484d", "#f5a524", "#a3c94a", "#30a46c", "#12a594", "#3e9bff", "#6e6ade", "#b264d9", "#e93d82"];

// Fonts are fetched only when picked.
const FONT_CSS: Record<string, string> = {
  inter: "Inter:wght@400;500;600",
  geist: "Geist:wght@400;500;600",
  plex: "IBM+Plex+Sans:wght@400;500;600",
  serif: "Source+Serif+4:opsz,wght@8..60,400;8..60,600",
  jetbrains: "JetBrains+Mono:wght@400;500;600",
  "geist-mono": "Geist+Mono:wght@400;500;600",
  "plex-mono": "IBM+Plex+Mono:wght@400;500;600",
};
const FONT_STACK: Record<string, string> = {
  inter: '"Inter", system-ui, sans-serif',
  geist: '"Geist", system-ui, sans-serif',
  plex: '"IBM Plex Sans", system-ui, sans-serif',
  system: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  jetbrains: '"JetBrains Mono", ui-monospace, Menlo, monospace',
  "geist-mono": '"Geist Mono", ui-monospace, Menlo, monospace',
  "plex-mono": '"IBM Plex Mono", ui-monospace, Menlo, monospace',
  "system-mono": 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
};

const KEY = "panda.settings.v1";
let current: Settings = (() => {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") };
  } catch { return DEFAULTS; }
})();
const subs = new Set<() => void>();

export function updateSettings(patch: Partial<Settings>) {
  current = { ...current, ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(current)); } catch { /* blocked */ }
  applySettings();
  subs.forEach((f) => f());
}
export const resetAppearance = () => updateSettings({
  theme: DEFAULTS.theme, lightPalette: DEFAULTS.lightPalette, darkPalette: DEFAULTS.darkPalette, accent: DEFAULTS.accent,
  uiFont: DEFAULTS.uiFont, font: DEFAULTS.font, codeFont: DEFAULTS.codeFont, textSize: DEFAULTS.textSize,
  density: DEFAULTS.density, radius: DEFAULTS.radius, width: DEFAULTS.width, layout: DEFAULTS.layout,
});
export const getSettings = () => current;
export function useSettings(): Settings {
  return useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => current);
}

/** Black or white, whichever reads better on the accent. */
function inkOn(hex: string) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return "#fff";
  const n = parseInt(m[1], 16);
  const lin = (c: number) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const L = 0.2126 * lin(n >> 16) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
  return L > 0.4 ? "#111" : "#fff";
}

const loaded = new Set<string>();
function loadFont(id: string) {
  const css = FONT_CSS[id];
  if (!css || loaded.has(id)) return;
  loaded.add(id);
  const l = document.createElement("link");
  l.rel = "stylesheet";
  l.href = `https://fonts.googleapis.com/css2?family=${css}&display=swap`;
  document.head.appendChild(l);
}

const dark = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
export function applySettings() {
  const root = document.documentElement;
  const d = root.dataset;
  const isDark = current.theme === "system" ? !!dark?.matches : current.theme === "dark";
  d.theme = isDark ? "dark" : "light";
  d.palette = isDark ? current.darkPalette : current.lightPalette;
  d.size = current.textSize;
  d.width = current.width;
  d.font = current.font;
  d.density = current.density;
  d.radius = current.radius;
  d.layout = current.layout;
  const accent = /^#[0-9a-f]{6}$/i.test(current.accent) ? current.accent : DEFAULTS.accent;
  root.style.setProperty("--accent", accent);
  root.style.setProperty("--on-accent", inkOn(accent));
  const mono = current.codeFont === "system" ? "system-mono" : current.codeFont;
  const ui = current.uiFont === "mono" || d.palette === "terminal" ? mono : current.uiFont;
  root.style.setProperty("--sans", FONT_STACK[ui]);
  root.style.setProperty("--mono", FONT_STACK[mono]);
  [ui, mono, current.font === "serif" ? "serif" : ""].forEach(loadFont);
  const meta = document.querySelector('meta[name="theme-color"]:not([media])') || (() => {
    const m = document.createElement("meta"); m.name = "theme-color"; document.head.appendChild(m); return m;
  })();
  meta.setAttribute("content", PALETTES.find((p) => p.id === d.palette)?.bg || "#111214");
}
dark?.addEventListener("change", applySettings);
