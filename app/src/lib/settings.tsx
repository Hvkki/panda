import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { supabase } from "./supabase";
import { newBrowserTopic } from "./browser";

export type Settings = {
  theme: "system" | "light" | "dark";
  font: "sans" | "serif";
  textSize: "small" | "medium" | "large";
  width: "narrow" | "wide";
  language: "en" | "it" | "uk";
  showThinking: boolean;
  approvals: "ask" | "trust";
  background_trust: boolean;
  memory: boolean;
  autoStart: boolean;
  notifyReplies: boolean;
  enterToSend: boolean;
  defaultModel: string;
  defaultEffort: string;
  name: string;
  browserTopic: string;
  /** Kiro Crew settings changed in Panda; the worker re-applies them on every start. */
  crewOverrides: Record<string, unknown>;
};

export const DEFAULTS: Settings = {
  theme: "system",
  font: "serif",
  textSize: "medium",
  width: "narrow",
  language: "en",
  showThinking: false,
  approvals: "ask",
  background_trust: true,
  memory: true,
  autoStart: true,
  notifyReplies: true,
  enterToSend: true,
  defaultModel: "claude-sonnet-5.5",
  defaultEffort: "medium",
  name: "",
  browserTopic: "",
  crewOverrides: {},
};

type Ctx = { settings: Settings; update: (patch: Partial<Settings>) => void; loaded: boolean };
const SettingsContext = createContext<Ctx>({ settings: DEFAULTS, update: () => {}, loaded: false });

export function SettingsProvider({ userId, children }: { userId: string; children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(() => {
    try {
      return { ...DEFAULTS, ...JSON.parse(localStorage.getItem("panda.settings") || "{}") };
    } catch {
      return DEFAULTS;
    }
  });
  const [loaded, setLoaded] = useState(false);
  const saveTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    supabase.from("user_settings").select("settings").eq("owner", userId).maybeSingle().then(({ data }) => {
      if (data?.settings) setSettings((s) => ({ ...s, ...data.settings }));
      setLoaded(true);
    });
  }, [userId]);

  // First run: create this user's private browser channel name.
  useEffect(() => {
    if (loaded && !settings.browserTopic) update({ browserTopic: newBrowserTopic() });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, settings.browserTopic]);

  const update = useCallback((patch: Partial<Settings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...patch };
      localStorage.setItem("panda.settings", JSON.stringify(next));
      window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => {
        supabase.from("user_settings")
          .upsert({ owner: userId, settings: next, updated_at: new Date().toISOString() }, { onConflict: "owner" })
          .then(({ error }) => { if (error) console.warn("settings not saved:", error.message); });
      }, 400);
      return next;
    });
  }, [userId]);

  // Apply look-and-feel settings to the document.
  useEffect(() => {
    const root = document.documentElement;
    const dark = settings.theme === "dark" ||
      (settings.theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    root.dataset.theme = dark ? "dark" : "light";
    root.dataset.font = settings.font;
    root.dataset.size = settings.textSize;
    root.dataset.width = settings.width;
    root.lang = settings.language;
  }, [settings.theme, settings.font, settings.textSize, settings.width, settings.language]);

  return <SettingsContext.Provider value={{ settings, update, loaded }}>{children}</SettingsContext.Provider>;
}

export const useSettings = () => useContext(SettingsContext);
