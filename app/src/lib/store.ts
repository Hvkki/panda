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
};
export type Chat = {
  id: string; title: string; model: string; effort: string; pinned: boolean;
  session_id?: string; created: number; updated: number; messages: Message[];
};

// Chats live in this browser only (no database). Clearing site data clears them.
const KEY = "panda.chats.v1";
const load = (): Chat[] => {
  try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { return []; }
};

export const uid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()));

export function useChats() {
  const [chats, setChats] = useState<Chat[]>(load);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      try { localStorage.setItem(KEY, JSON.stringify(chats)); } catch { /* storage full or blocked */ }
    }, 400);
  }, [chats]);

  const patchChat = useCallback((id: string, fn: (c: Chat) => Chat) =>
    setChats((prev) => prev.map((c) => (c.id === id ? { ...fn(c), updated: Date.now() } : c))), []);
  const patchMessage = useCallback((chatId: string, msgId: string, fn: (m: Message) => Message) =>
    patchChat(chatId, (c) => ({ ...c, messages: c.messages.map((m) => (m.id === msgId ? fn(m) : m)) })), [patchChat]);
  const add = useCallback((c: Chat) => setChats((prev) => [c, ...prev]), []);
  const remove = useCallback((id: string) => setChats((prev) => prev.filter((c) => c.id !== id)), []);
  return { chats, add, remove, patchChat, patchMessage };
}
