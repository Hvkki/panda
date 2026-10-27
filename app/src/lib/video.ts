import { runCommand } from "./commands";
import { FILES_BUCKET, signedUrl } from "./files";
import { supabase } from "./supabase";

export type VideoMode = "clip" | "frames" | "orbit";
export type VideoQuality = "draft" | "final";
export type VideoRatio = "9:16" | "16:9" | "1:1";
export type VideoStage = "queued" | "starting" | "loading" | "reading prompt" | "sampling" | "decoding" | "saving"
  | "done" | "failed" | "cancelled";

export type Estimate = { secs: number; usd: number; usd_high: number; warm: boolean;
  parts: { start: number; sampling: number; other: number } };

export type VideoJob = {
  id: string; group: string; created: number; source: "tab" | "chat"; chat_id?: string | null;
  title: string; idea: string; style: string; prompt: string; ratio: VideoRatio; seed: number;
  first?: string; last?: string; from?: string;
  mode: VideoMode; quality: VideoQuality; w: number; h: number; secs: number; frames: number; steps: number;
  est: Estimate; status: "queued" | "rendering" | "done" | "failed" | "cancelled";
  stage: VideoStage; step: number; pct: number; eta?: number; started?: number; finished?: number;
  error?: string; cost?: number; billed_secs?: number; video?: string; poster?: string; size?: number;
  cold?: boolean; timings?: { total?: number; container_up?: number; stages?: Record<string, number> };
};

export type JobsFile = { v: number; at: number; jobs: VideoJob[]; budget: number; spent: number; rate_usd_s: number;
  stats?: Stats; gpu?: { state?: string; t?: number; gpu?: string } };

export type VideoSpec = {
  mode: VideoMode; quality: VideoQuality; ratio: VideoRatio; secs: number; seeds: number; style: string;
  idea: string; prompt?: string; first?: string; last?: string; audio?: boolean; title?: string; from?: string;
  seed?: number;
};

export type EstimateResult = { w: number; h: number; frames: number; steps: number; seeds: number; est: Estimate;
  total_usd: number; total_high: number; total_secs: number; warm: boolean; budget: number; spent: number;
  committed: number; rate_usd_h: number };

export const videoPrompt = (s: Partial<VideoSpec>) =>
  runCommand<{ prompt: string; by: "writer" | "template" }>("video.prompt", s, 150000);
export const videoEstimate = (s: VideoSpec) => runCommand<EstimateResult>("video.estimate", s, 40000);
export const videoSubmit = (s: VideoSpec) =>
  runCommand<{ jobs: VideoJob[]; total_usd: number; total_high: number }>("video.submit", s, 90000);
export const videoCancel = (id: string) => runCommand<{ ok: boolean }>("video.cancel", { id }, 40000);
export const videoDelete = (id: string) => runCommand<{ ok: boolean }>("video.delete", { id }, 40000);

/** The gallery file the worker keeps in Storage. Readable even while Panda is off. */
let jobsUrl: { url: string; at: number } | null = null;
export async function loadJobs(owner: string): Promise<JobsFile | null> {
  const path = `${owner}/video/jobs.json`;
  if (!jobsUrl || Date.now() - jobsUrl.at > 4 * 60_000) {
    const { data, error } = await supabase.storage.from(FILES_BUCKET).createSignedUrl(path, 600);
    if (error || !data) return null; // no videos yet
    jobsUrl = { url: data.signedUrl, at: Date.now() };
  }
  const r = await fetch(`${jobsUrl.url}&t=${Date.now()}`, { cache: "no-store" });
  if (r.status === 400 || r.status === 404) { jobsUrl = null; return null; }
  if (!r.ok) throw new Error(`gallery: ${r.status}`);
  return (await r.json()) as JobsFile;
}

/** Start / end pictures go to the owner's uploads folder, like chat attachments. */
export async function uploadRef(owner: string, file: File): Promise<string> {
  if (!file.type.startsWith("image/")) throw new Error("Pick a picture (JPG, PNG, WebP).");
  if (file.size > 20 * 2 ** 20) throw new Error("That picture is over 20 MB.");
  const name = file.name.replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 80) || "picture";
  const path = `${owner}/uploads/video-${Date.now()}-${name}`;
  const up = await supabase.storage.from(FILES_BUCKET).upload(path, file, { contentType: file.type });
  if (up.error) throw up.error;
  return path;
}

export const mediaUrl = (path: string) => signedUrl(path);

export const money = (usd: number) => `$${(Math.round(usd * 100) / 100).toFixed(2)}`;
export function duration(secs: number) {
  if (secs < 60) return `${Math.max(1, Math.round(secs))} s`;
  const m = Math.floor(secs / 60), s = Math.round(secs % 60);
  return s ? `${m} min ${s} s` : `${m} min`;
}
export const active = (j: VideoJob) => j.status === "queued" || j.status === "rendering";

// ── instant price: the same sums as worker/skills/panda-video/video.py (keep the two in step) ──
export type Stats = { a: number; b: number; stage: Record<string, number>; cold_up: number; n?: number };
export const DEFAULT_STATS: Stats = { a: 7.28, b: 1.2, stage: { "reading prompt": 6, decoding: 14, saving: 3, loading: 20 }, cold_up: 45 };
export const SIZES: Record<VideoQuality, Record<VideoRatio, [number, number]>> = {
  final: { "16:9": [1344, 768], "9:16": [768, 1344], "1:1": [1024, 1024] },
  draft: { "16:9": [896, 512], "9:16": [512, 896], "1:1": [640, 640] },
};
const STEPS: Record<VideoQuality, number> = { draft: 8, final: 20 };
export const framesFor = (secs: number) => 4 * Math.max(4, Math.round((secs * 24) / 4)) + 1;

export function planOf(s: Pick<VideoSpec, "mode" | "quality" | "ratio" | "secs">) {
  if (s.mode === "orbit") return { w: 768, h: 768, secs: 3, frames: 73, steps: 28, quality: "final" as VideoQuality };
  const [w, h] = SIZES[s.quality][s.ratio];
  const secs = Math.min(10, Math.max(2, s.secs));
  return { w, h, secs, frames: framesFor(secs), steps: STEPS[s.quality], quality: s.quality };
}

export function priceOf(s: Pick<VideoSpec, "mode" | "quality" | "ratio" | "secs" | "seeds">, stats: Stats = DEFAULT_STATS,
  rate = 0.001001, warm = false) {
  const p = planOf(s);
  const u = (p.w * p.h * p.frames) / 1e8;
  const st = { ...DEFAULT_STATS.stage, ...stats.stage };
  const one = (isWarm: boolean) => (isWarm ? 0 : stats.cold_up + st.loading) + st["reading prompt"]
    + p.steps * stats.a * u ** stats.b + st.decoding + st.saving;
  const takes = Math.max(1, Math.min(4, s.seeds || 1));
  const secs = one(warm) + one(true) * (takes - 1);
  return { ...p, secs, usd: secs * rate, high: secs * rate * 1.35 + 0.01 * takes };
}

/** Split a 3-field H3 prompt into its parts (and back), for the script editor. */
export type Script = { picture: string; sound: string; music: string };
export function toScript(prompt: string): Script {
  const grab = (key: string) => {
    const m = prompt.match(new RegExp(`${key}\\s*:\\s*([\\s\\S]*?)(?=\\n\\s*(integrated_multimodal_description|overall_soundscape|non_diegetic_music)\\s*:|$)`, "i"));
    return (m?.[1] || "").trim();
  };
  const picture = grab("integrated_multimodal_description");
  return { picture: picture || prompt.trim(), sound: grab("overall_soundscape"), music: grab("non_diegetic_music") };
}
export const fromScript = (s: Script) =>
  `integrated_multimodal_description: ${s.picture.trim()}\noverall_soundscape: ${s.sound.trim() || "the natural sound of the scene"}\nnon_diegetic_music: ${s.music.trim() || "none"}`;
