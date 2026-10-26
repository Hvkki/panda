import { createContext, useContext, useEffect, useMemo, useSyncExternalStore } from "react";
import type { RealtimeChannel } from "@supabase/supabase-js";
import { supabase } from "./supabase";

export type BrowserStatus = {
  url: string;
  title: string;
  tabs: { i: number; u: string; active: boolean }[];
  control: "agent" | "user" | "needs_user";
  message: string;
  busy: boolean;
  loading?: boolean;
  viewer: boolean;
  /** What Panda is doing right now ("Clicking “Search”"), empty when idle. */
  action?: string;
  /** The text field that has focus, so the app can offer the keyboard. */
  focus?: { kind: string; label: string; secret?: boolean; y?: number } | null;
  w?: number;
  h?: number;
};

/** One step Panda took in the browser. x/y/w/h: the target's box as fractions of the page. */
export type BrowserAction = {
  k: string; c: string; t: number; x?: number; y?: number; w?: number; h?: number;
  key?: string; url?: string; dy?: number;
  /** The screen right after the step (for the activity list). */
  shot?: string;
};

export type BrowserSnapshot = {
  status: BrowserStatus | null;
  frame: string | null;          // data URL of the latest JPEG
  dims: { w: number; h: number };// page size of that frame, in CSS px
  lastSeen: number;              // when the engine last spoke
  actions: BrowserAction[];      // newest last, at most 40
};

type Size = { vw: number; vh: number };

/**
 * Live link to Panda's browser engine over a Supabase Realtime broadcast channel.
 *
 * It is a small external store, so only the components that show the browser re-render when a
 * frame arrives (up to 8 a second), not the whole app. The channel name is a private random topic
 * kept in the user's settings. Frames flow only while at least one viewer is registered.
 */
export class BrowserLink {
  private snap: BrowserSnapshot = { status: null, frame: null, dims: { w: 1024, h: 720 }, lastSeen: 0, actions: [] };
  private listeners = new Set<() => void>();
  private chan: RealtimeChannel | null = null;
  private viewers = new Map<number, Size | undefined>();
  private nextId = 1;
  private timer = 0;
  private raf = 0;
  private pendingFrame: string | null = null;
  private pendingDims: { w: number; h: number } | null = null;
  private thumbFor: BrowserAction | null = null;
  readonly topic: string;

  constructor(topic: string) { this.topic = topic; }

  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  getSnapshot = () => this.snap;
  private set(patch: Partial<BrowserSnapshot>) { this.snap = { ...this.snap, ...patch }; this.listeners.forEach((l) => l()); }

  connect() {
    const ch = supabase.channel(this.topic, { config: { broadcast: { self: false, ack: false } } });
    ch.on("broadcast", { event: "frame" }, ({ payload }) => this.onFrame(payload));
    ch.on("broadcast", { event: "status" }, ({ payload }) => this.set({ status: payload as BrowserStatus, lastSeen: Date.now() }));
    ch.on("broadcast", { event: "action" }, ({ payload }) => this.onAction(payload as BrowserAction));
    ch.subscribe();
    this.chan = ch;
    this.timer = window.setInterval(() => { if (this.viewers.size) this.hello(); }, 8000);
  }

  disconnect() {
    window.clearInterval(this.timer);
    cancelAnimationFrame(this.raf);
    if (this.chan) supabase.removeChannel(this.chan);
    this.chan = null;
  }

  send = (payload: Record<string, unknown>) => {
    this.chan?.send({ type: "broadcast", event: "input", payload });
  };

  /**
   * Register a viewer (the inline card or the full panel). Returns a release function.
   * The largest registered size decides the page shape: phones get a tall, narrow page only
   * while the full panel is open; a card alone keeps the desktop layout (like a screen thumbnail).
   */
  acquire = (size?: Size) => {
    const id = this.nextId++;
    this.viewers.set(id, size);
    this.hello();
    return () => { this.viewers.delete(id); if (this.viewers.size) this.hello(); };
  };

  private hello() {
    const sizes = [...this.viewers.values()].filter(Boolean) as Size[];
    const big = sizes.sort((a, b) => b.vw * b.vh - a.vw * a.vh)[0];
    const mobile = !!big && window.innerWidth < 900;
    this.send({ t: "hello", mode: mobile ? "mobile" : "desktop", vw: big?.vw || 0, vh: big?.vh || 0, lang: big ? document.documentElement.lang || "en" : "" });
  }

  private onFrame(payload: { d: string; w?: number; h?: number }) {
    this.pendingFrame = `data:image/jpeg;base64,${payload.d}`;
    if (payload.w && payload.h) this.pendingDims = { w: payload.w, h: payload.h };
    if (this.raf) return;
    this.raf = requestAnimationFrame(() => {   // never paint faster than the screen
      this.raf = 0;
      const patch: Partial<BrowserSnapshot> = { frame: this.pendingFrame, lastSeen: Date.now() };
      const d = this.pendingDims;
      if (d && (d.w !== this.snap.dims.w || d.h !== this.snap.dims.h)) patch.dims = d;
      const step = this.thumbFor;
      if (step && Date.now() - step.t > 450) {   // the screen right after the step
        this.thumbFor = null;
        patch.actions = this.snap.actions.map((a) => (a === step ? { ...a, shot: this.pendingFrame || undefined } : a));
      }
      this.set(patch);
    });
  }

  private onAction(a: BrowserAction) {
    const act = { ...a, t: Date.now() };
    this.thumbFor = act;
    this.set({ actions: [...this.snap.actions, act].slice(-40), lastSeen: Date.now() });
  }
}

/** One BrowserLink per topic for the signed-in user (stable across renders). */
export function useBrowserLink(topic: string | undefined): BrowserLink | null {
  const link = useMemo(() => (topic ? new BrowserLink(topic) : null), [topic]);
  useEffect(() => {
    if (!link) return;
    link.connect();
    return () => link.disconnect();
  }, [link]);
  return link;
}

const EMPTY: BrowserSnapshot = { status: null, frame: null, dims: { w: 1024, h: 720 }, lastSeen: 0, actions: [] };
const noop = () => () => {};

/** Re-render on every frame/status/action (for the views that show the browser). */
export function useBrowserSnapshot(link: BrowserLink | null): BrowserSnapshot {
  return useSyncExternalStore(link ? link.subscribe : noop, link ? link.getSnapshot : () => EMPTY);
}

/** Re-render only when the selected primitive changes (top bar badges etc.). */
export function useBrowserSelect<T extends string | number | boolean | null | undefined>(
  link: BrowserLink | null, pick: (s: BrowserSnapshot) => T): T {
  return useSyncExternalStore(link ? link.subscribe : noop, () => pick(link ? link.getSnapshot() : EMPTY));
}

/** Keep the live view streaming while the calling component is on screen. */
export function useBrowserViewer(link: BrowserLink | null, enabled: boolean, size?: Size) {
  const vw = size?.vw || 0, vh = size?.vh || 0;
  useEffect(() => {
    if (!link || !enabled) return;
    return link.acquire(vw && vh ? { vw, vh } : undefined);
  }, [link, enabled, vw, vh]);
}

export const isAlive = (s: BrowserSnapshot) => Date.now() - s.lastSeen < 25000;

/** Random unguessable channel name; created once per user and kept in their settings. */
export const newBrowserTopic = () =>
  "pbr-" + Array.from(crypto.getRandomValues(new Uint8Array(18)), (b) => b.toString(16).padStart(2, "0")).join("");

/** Shared across the chat so the inline cards and the full panel use one connection. */
export const BrowserContext = createContext<{ link: BrowserLink | null; openFull: () => void; online: boolean }>({
  link: null, openFull: () => {}, online: false,
});
export const useBrowser = () => useContext(BrowserContext);

/** True when a turn's tool steps include a Panda-browser action. */
export function usedBrowser(tools: string[] | undefined): boolean {
  return (tools || []).some((t) => /panda-browser|\bpb(?:\.py)?\s+(open|click|type|select|hover|scroll|snap|text|shot|handoff|press|tab|back|reload|wait)/i.test(t));
}

export const hostOf = (url?: string) => {
  try { return new URL(url || "").hostname.replace(/^www\./, ""); } catch { return ""; }
};
