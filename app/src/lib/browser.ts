import { useCallback, useEffect, useRef, useState } from "react";
import type { RealtimeChannel } from "@supabase/supabase-js";
import { supabase } from "./supabase";

export type BrowserStatus = {
  url: string;
  title: string;
  tabs: { i: number; u: string; active: boolean }[];
  control: "agent" | "user" | "needs_user";
  message: string;
  busy: boolean;
  viewer: boolean;
};

export type BrowserLink = {
  status: BrowserStatus | null;
  frame: string | null;          // data URL of the latest JPEG
  dims: { w: number; h: number };// page size of that frame, in CSS px
  lastSeen: number;              // when the engine last spoke
  send: (payload: Record<string, unknown>) => void;
  setViewing: (v: boolean, size?: { vw: number; vh: number }) => void;
};

/**
 * Live link to Panda's browser engine over a Supabase Realtime broadcast channel.
 * The channel name is a private random topic stored in the user's settings; only the
 * signed-in user (and their worker) know it. Frames flow only while `viewing` is true.
 */
export function useBrowserLink(topic: string | undefined): BrowserLink {
  const [status, setStatus] = useState<BrowserStatus | null>(null);
  const [frame, setFrame] = useState<string | null>(null);
  const [lastSeen, setLastSeen] = useState(0);
  const [dims, setDims] = useState({ w: 1024, h: 720 });
  const size = useRef({ vw: 0, vh: 0 });
  const chan = useRef<RealtimeChannel | null>(null);
  const viewing = useRef(false);
  const pending = useRef<string | null>(null);
  const raf = useRef(0);

  const send = useCallback((payload: Record<string, unknown>) => {
    chan.current?.send({ type: "broadcast", event: "input", payload });
  }, []);

  useEffect(() => {
    if (!topic) return;
    const ch = supabase.channel(topic, { config: { broadcast: { self: false, ack: false } } });
    ch.on("broadcast", { event: "frame" }, ({ payload }) => {
      pending.current = `data:image/jpeg;base64,${payload.d}`;
      if (payload.w && payload.h) setDims((d) => (d.w === payload.w && d.h === payload.h ? d : { w: payload.w, h: payload.h }));
      setLastSeen(Date.now());
      if (!raf.current) {
        raf.current = requestAnimationFrame(() => {   // never paint faster than the screen
          raf.current = 0;
          if (pending.current) setFrame(pending.current);
        });
      }
    });
    ch.on("broadcast", { event: "status" }, ({ payload }) => {
      setStatus(payload as BrowserStatus);
      setLastSeen(Date.now());
    });
    ch.subscribe();
    chan.current = ch;
    return () => {
      cancelAnimationFrame(raf.current);
      raf.current = 0;
      supabase.removeChannel(ch);
      chan.current = null;
    };
  }, [topic]);

  // While the panel is open, tell the engine a viewer is present (it streams only then) and how
  // big the viewer is: phones get a tall, narrow page that fills the screen.
  const hello = useCallback(() => {
    send({ t: "hello", mode: window.innerWidth < 900 ? "mobile" : "desktop", ...size.current });
  }, [send]);
  const setViewing = useCallback((v: boolean, sz?: { vw: number; vh: number }) => {
    viewing.current = v;
    if (sz) size.current = sz;
    if (v) hello();
  }, [hello]);
  useEffect(() => {
    const id = window.setInterval(() => { if (viewing.current) hello(); }, 8000);
    return () => window.clearInterval(id);
  }, [hello]);

  return { status, frame, dims, lastSeen, send, setViewing };
}

/** Random unguessable channel name; created once per user and kept in their settings. */
export const newBrowserTopic = () =>
  "pbr-" + Array.from(crypto.getRandomValues(new Uint8Array(18)), (b) => b.toString(16).padStart(2, "0")).join("");

import { createContext, useContext } from "react";

/** Shared across the chat so the inline live card and the full panel use one connection. */
export const BrowserContext = createContext<{ link: BrowserLink | null; openFull: () => void; online: boolean }>({
  link: null, openFull: () => {}, online: false,
});
export const useBrowser = () => useContext(BrowserContext);

/** True when a turn's tool steps include a Panda-browser action. */
export function usedBrowser(tools: string[] | undefined): boolean {
  return (tools || []).some((t) => /panda-browser|\bpb(?:\.py)?\s+(open|click|type|scroll|snap|text|shot|handoff|press|tab|back|reload|wait)/i.test(t)
    || /\bopen(ing)?\b.*\b(https?:|www\.|\.com|\.org|\.net|website|page)/i.test(t));
}
