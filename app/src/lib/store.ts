import type { TurnUsage } from "./api";
import { useCallback, useEffect, useRef, useState } from "react";

export type Tool = { id: string; label: string; done: boolean; error?: boolean };
export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  tools?: Tool[];
  status?: "waiting" | "streaming" | "done" | "error";
  error?: string;
  secs?: number;
  usage?: TurnUsage;
  /** The worker's id for the run that produces this reply (to pick it up again after a reload). */
  turn?: string;
};
export type Chat = {
  id: string; title: string; model: string; effort: string; pinned: boolean;
  session_id?: string; created: number; updated: number; messages: Message[];
  /** Set when the chat belongs to a project. */
  project_id?: string;
  /** The single Developer chat. */
  dev?: boolean;
};

export type Project = { id: string; name: string; instructions: string; created: number; updated: number };

// Chats live in this browser only (no database). Clearing site data clears them.
// Each chat is stored under its own key, so a reply streaming into one chat re-saves only that chat.
// (Saving every chat after each step froze phones for 100+ ms at a time once there were a few MB of chats.)
const KEY_V1 = "panda.chats.v1";
const INDEX = "panda.chats.v2";
const CHAT = (id: string) => `panda.chat.${id}`;
const load = (): Chat[] => {
  try {
    const ids = localStorage.getItem(INDEX);
    if (ids === null) return JSON.parse(localStorage.getItem(KEY_V1) || "[]");
    return (JSON.parse(ids) as string[]).flatMap((id) => {
      try { const c = JSON.parse(localStorage.getItem(CHAT(id)) || "null"); return c ? [c as Chat] : []; } catch { return []; }
    });
  } catch { return []; }
};

/** Write chats whose object changed since the last save; drop removed ones. */
function save(chats: Chat[], saved: Map<string, Chat>) {
  let migrating = false;
  try {
    migrating = localStorage.getItem(INDEX) === null;
    // The old single key holds a full copy: free it first, or both copies may not fit in storage.
    if (migrating) localStorage.removeItem(KEY_V1);
    for (const c of chats) {
      if (saved.get(c.id) === c) continue;
      localStorage.setItem(CHAT(c.id), JSON.stringify(c));
      saved.set(c.id, c);
    }
    const ids = new Set(chats.map((c) => c.id));
    for (const id of [...saved.keys()]) if (!ids.has(id)) { localStorage.removeItem(CHAT(id)); saved.delete(id); }
    localStorage.setItem(INDEX, JSON.stringify(chats.map((c) => c.id)));
  } catch {
    // Storage full or blocked. When migrating, put the old format back so nothing is lost.
    if (migrating) {
      try {
        for (const id of saved.keys()) localStorage.removeItem(CHAT(id));
        saved.clear();
        localStorage.setItem(KEY_V1, JSON.stringify(chats));
      } catch { /* nothing more to do */ }
    }
  }
}

export const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()));

export function useChats() {
  const [chats, setChats] = useState<Chat[]>(load);
  // What is already in storage. Empty on first load when migrating from the single-key format.
  const saved = useRef<Map<string, Chat> | null>(null);
  if (saved.current === null) {
    let migrated = false;
    try { migrated = localStorage.getItem(INDEX) !== null; } catch { /* blocked */ }
    saved.current = migrated ? new Map(chats.map((c) => [c.id, c])) : new Map();
  }
  const latest = useRef(chats);
  latest.current = chats;
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (timer.current !== undefined) return; // a save is already scheduled; it reads the latest chats
    timer.current = window.setTimeout(() => { timer.current = undefined; save(latest.current, saved.current!); }, 1000);
  }, [chats]);
  useEffect(() => {
    const now = () => { window.clearTimeout(timer.current); timer.current = undefined; save(latest.current, saved.current!); };
    const hidden = () => { if (document.hidden) now(); };
    window.addEventListener("pagehide", now);
    document.addEventListener("visibilitychange", hidden);
    return () => { window.removeEventListener("pagehide", now); document.removeEventListener("visibilitychange", hidden); };
  }, []);

  const patchChat = useCallback((id: string, fn: (c: Chat) => Chat) =>
    setChats((prev) => prev.map((c) => (c.id === id ? { ...fn(c), updated: Date.now() } : c))), []);
  const patchMessage = useCallback((chatId: string, msgId: string, fn: (m: Message) => Message) =>
    patchChat(chatId, (c) => ({ ...c, messages: c.messages.map((m) => (m.id === msgId ? fn(m) : m)) })), [patchChat]);
  const add = useCallback((c: Chat) => setChats((prev) => [c, ...prev]), []);
  const remove = useCallback((id: string) => setChats((prev) => prev.filter((c) => c.id !== id)), []);
  const importChats = useCallback((list: Chat[]) => setChats((prev) => {
    const have = new Set(prev.map((c) => c.id));
    return [...prev, ...list.filter((c) => c && c.id && Array.isArray(c.messages) && !have.has(c.id))];
  }), []);
  const clearAll = useCallback(() => setChats([]), []);
  return { chats, add, remove, patchChat, patchMessage, importChats, clearAll };
}

// Projects live in this browser too.
const PKEY = "panda.projects.v1";
const loadProjects = (): Project[] => {
  try { return JSON.parse(localStorage.getItem(PKEY) || "[]"); } catch { return []; }
};

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>(loadProjects);
  useEffect(() => {
    try { localStorage.setItem(PKEY, JSON.stringify(projects)); } catch { /* storage full or blocked */ }
  }, [projects]);
  const addProject = useCallback((name: string) => {
    const p: Project = { id: uid(), name, instructions: "", created: Date.now(), updated: Date.now() };
    setProjects((prev) => [p, ...prev]);
    return p;
  }, []);
  const patchProject = useCallback((id: string, patch: Partial<Project>) =>
    setProjects((prev) => prev.map((p) => (p.id === id ? { ...p, ...patch, updated: Date.now() } : p))), []);
  const removeProject = useCallback((id: string) => setProjects((prev) => prev.filter((p) => p.id !== id)), []);
  return { projects, addProject, patchProject, removeProject };
}
