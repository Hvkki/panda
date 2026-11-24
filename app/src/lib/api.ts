import { API_BASE } from "./config";

// ── password (the only credential the browser holds) ────────────────────────
const PW_KEY = "panda.password";
export const getPassword = () => { try { return localStorage.getItem(PW_KEY) || ""; } catch { return ""; } };
export const setPassword = (v: string) => {
  try { if (v) localStorage.setItem(PW_KEY, v); else localStorage.removeItem(PW_KEY); } catch { /* private mode */ }
};

export type WorkerState = { state: "on" | "starting" | "off"; url?: string; uptime?: number; note?: string };
let workerUrl = "";

/** Start / Stop / Status through the Vercel function (it holds the Kaggle token). */
export async function sessionAction(action: "start" | "stop" | "status", password = getPassword()): Promise<WorkerState> {
  const r = await fetch(`${API_BASE}/api/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${password}` },
    body: JSON.stringify({ action }),
  });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) throw new AuthError();
  if (!r.ok) throw new Error(data.error || `Server answered ${r.status}`);
  workerUrl = data.state === "on" ? data.url || "" : "";
  return data as WorkerState;
}

export class AuthError extends Error {
  constructor() { super("Wrong password"); }
}

/** Resolve once the worker answers; starts it first when it is off. */
export async function ensureWorker(onState: (s: WorkerState["state"]) => void, signal?: AbortSignal): Promise<string> {
  let s = await sessionAction("status");
  if (s.state === "off") s = await sessionAction("start");
  const deadline = Date.now() + 10 * 60 * 1000;
  while (s.state !== "on") {
    onState(s.state);
    if (signal?.aborted) throw new Error("Cancelled");
    if (Date.now() > deadline) throw new Error("The worker did not come online in 10 minutes. Check the Kaggle notebook log.");
    await new Promise((res) => setTimeout(res, 5000));
    s = await sessionAction("status");
  }
  return s.url!;
}

const wfetch = (path: string, init: RequestInit = {}) => {
  if (!workerUrl) throw new Error("The worker is offline");
  return fetch(`${workerUrl}${path}`, { ...init, headers: { ...(init.headers || {}), Authorization: `Bearer ${getPassword()}` } });
};

// ── chat stream ─────────────────────────────────────────────────────────────
export type StreamEvent =
  | { t: "session"; id: string }
  | { t: "text"; d: string }
  | { t: "thinking"; d: string }
  | { t: "tool"; id: string; label: string }
  | { t: "tool_done"; id: string; error: boolean }
  | { t: "done"; session_id?: string; text: string; error: boolean; secs: number }
  | { t: "error"; message: string };

export type ChatRequest = {
  chat_id: string; message: string; model: string; effort: string; session_id?: string;
  history: { role: string; content: string }[];
  /** Project instructions (added to Claude's system prompt). */
  system?: string;
  /** Project id: the chat runs in its own folder on the worker. */
  folder?: string;
  /** The Developer chat: may change Panda; sees the developer variables. */
  dev?: boolean;
};

export async function streamChat(url: string, req: ChatRequest, onEvent: (e: StreamEvent) => void, signal: AbortSignal) {
  workerUrl = url;
  const r = await wfetch("/chat", { method: "POST", body: JSON.stringify(req), signal, headers: { "Content-Type": "application/json" } });
  if (r.status === 401) throw new AuthError();
  if (!r.ok || !r.body) throw new Error(`Worker answered ${r.status}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = block.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try { onEvent(JSON.parse(line.slice(5))); } catch { /* ignore a broken frame */ }
    }
  }
}

export const cancelChat = (chat_id: string) =>
  wfetch("/cancel", { method: "POST", body: JSON.stringify({ chat_id }), headers: { "Content-Type": "application/json" } }).catch(() => {});

// ── files ───────────────────────────────────────────────────────────────────
export type WorkFile = { path: string; size: number; mtime: number };
export async function listFiles(): Promise<WorkFile[]> {
  const r = await wfetch("/files");
  if (!r.ok) throw new Error(`Worker answered ${r.status}`);
  return (await r.json()).files;
}
export async function downloadFile(path: string) {
  const r = await wfetch(`/file?path=${encodeURIComponent(path)}`);
  if (!r.ok) throw new Error(`Worker answered ${r.status}`);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = path.split("/").pop() || "file";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
export type UploadTarget = { folder?: string; dev?: boolean };
export async function uploadFile(f: File, target: UploadTarget = {}): Promise<string> {
  const headers: Record<string, string> = { "X-File-Name": f.name };
  if (target.folder) headers["X-Folder"] = target.folder;
  if (target.dev) headers["X-Dev"] = "1";
  const r = await wfetch("/upload", { method: "POST", body: f, headers });
  if (!r.ok) throw new Error(`Upload failed (${r.status})`);
  return (await r.json()).path;
}
// ── developer variables (stored in the private Kaggle dataset by the worker) ──
export const getDevEnv = () => wjson<{ names: string[] }>("/devenv").then((d) => d.names);
export const setDevEnv = (set: Record<string, string | null>) => wjson<{ names: string[] }>("/devenv", { set });

export const workerOnline = () => !!workerUrl;

// ── saved state on the worker (skills, connectors, keys, memory, schedules) ──
export type Schedule = {
  id?: string; name: string; prompt: string; kind: "daily" | "interval"; time?: string; minutes?: number;
  days?: number[]; tz?: number; model?: string; effort?: string; enabled?: boolean; last_run?: number;
};
export type McpConfig = { type: "http" | "sse" | "stdio"; url?: string; headers?: Record<string, string>;
  command?: string; args?: string[]; env?: Record<string, string> };
export type WorkerSaved = {
  skills: Record<string, string>; mcp: Record<string, McpConfig>; env: string[]; dev_env: string[]; memory: string;
  schedules: Schedule[]; settings: { browser: boolean; memory: boolean }; save: "saved" | "saving" | "error";
  browser: string; browser_error: string; uptime: number; max: number; claude: boolean;
};
export type Note = { id: string; at: number; kind: string; title: string; prompt?: string; text: string;
  session_id?: string; error?: boolean };

async function wjson<T>(path: string, body?: unknown): Promise<T> {
  const r = await wfetch(path, body === undefined ? {} : { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });
  const d = await r.json().catch(() => ({}));
  if (r.status === 401) throw new AuthError();
  if (!r.ok || d.error) throw new Error(d.error || `Worker answered ${r.status}`);
  return d as T;
}
export const getSaved = () => wjson<WorkerSaved>("/state");
export const change = <T = { ok: boolean }>(path: string, body: unknown) => wjson<T>(path, body);
export const getNotes = () => wjson<{ notifications: Note[] }>("/notifications").then((d) => d.notifications);
export const mcpStatus = () => wjson<{ text: string }>("/mcp/status").then((d) => d.text);
export const setEnv = (set: Record<string, string | null>) => wjson<{ names: string[] }>("/env", { set });
export const deleteFile = (path: string) => wjson("/delete", { path });
export const loginStart = () => wjson<{ url: string }>("/login/start", {});
export const loginCode = (code: string) => wjson<{ ok: boolean }>("/login/code", { code });

// ── live browser ─────────────────────────────────────────────────────────────
export type BrowserTab = { id: string; title: string; url: string };
export const browserStatus = () => wjson<{ state: string; error: string; tabs: BrowserTab[] }>("/browser/status");
export const browserAct = (a: Record<string, unknown>) => wjson("/browser/act", a);
export async function browserShot(tab: string): Promise<{ url: string; w: number; h: number }> {
  const r = await wfetch(`/browser/shot?tab=${encodeURIComponent(tab)}&q=60`);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `Worker answered ${r.status}`);
  const [w, h] = (r.headers.get("X-View") || "1280x800").split("x").map(Number);
  return { url: URL.createObjectURL(await r.blob()), w: w || 1280, h: h || 800 };
}
