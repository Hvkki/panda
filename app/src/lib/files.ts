import { supabase } from "./supabase";

export const FILES_BUCKET = "panda-files";

export type FileRow = {
  id: string;
  owner: string;
  chat_id: string | null;
  message_id: number | null;
  project_id: string | null;
  kind: "output" | "upload" | "project";
  name: string;
  path: string;
  mime: string;
  size: number;
  pinned: boolean;
  created_at: string;
};

export type Attachment = { path: string; name: string; mime: string; size: number; file_id?: string };

const urlCache = new Map<string, { url: string; at: number }>();

/** Short-lived download link for a private file (cached for 50 minutes). */
export const isDrive = (path: string) => path.startsWith("drive:");
export const driveUrl = (path: string) => `https://drive.google.com/file/d/${path.slice(6)}/view`;

export async function signedUrl(path: string, download?: string): Promise<string> {
  if (isDrive(path)) return driveUrl(path); // big files live in the user's Google Drive
  const key = path + (download ?? "");
  const hit = urlCache.get(key);
  if (hit && Date.now() - hit.at < 50 * 60_000) return hit.url;
  const { data, error } = await supabase.storage.from(FILES_BUCKET)
    .createSignedUrl(path, 3600, download ? { download } : undefined);
  if (error || !data) throw error || new Error("no url");
  urlCache.set(key, { url: data.signedUrl, at: Date.now() });
  return data.signedUrl;
}

const safe = (n: string) => n.replace(/[^A-Za-z0-9._ -]+/g, "_").slice(0, 120) || "file";

/** Upload one file; returns the stored row. kind=upload (chat attachment) or project. */
export async function uploadFile(owner: string, file: File, opts: { kind: "upload" | "project"; projectId?: string; chatId?: string }) {
  if (file.size > 50 * 2 ** 20) throw new Error(`${file.name} is over 50 MB`);
  const folder = opts.kind === "project" ? `projects/${opts.projectId}` : "uploads";
  const path = `${owner}/${folder}/${Date.now()}-${safe(file.name)}`;
  const up = await supabase.storage.from(FILES_BUCKET).upload(path, file, { contentType: file.type || undefined });
  if (up.error) throw up.error;
  const { data, error } = await supabase.from("files").insert({
    owner, kind: opts.kind, name: file.name, path, mime: file.type || "application/octet-stream",
    size: file.size, project_id: opts.projectId ?? null, chat_id: opts.chatId ?? null,
  }).select().single();
  if (error) throw error;
  return data as FileRow;
}

export async function deleteFile(f: FileRow) {
  // Drive copies stay in the user's Drive (they can delete them there); only the card is removed.
  if (!isDrive(f.path)) await supabase.storage.from(FILES_BUCKET).remove([f.path]);
  await supabase.from("files").delete().eq("id", f.id);
}

export function fileKind(name: string, mime = ""): { label: string; color: string; group: "doc" | "slides" | "image" | "data" | "other" } {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (mime.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext))
    return { label: ext.toUpperCase() || "IMG", color: "#8B5CF6", group: "image" };
  if (ext === "pdf") return { label: "PDF", color: "#E5484D", group: "doc" };
  if (["doc", "docx", "odt", "rtf", "md", "txt"].includes(ext)) return { label: ext.toUpperCase(), color: "#2563EB", group: "doc" };
  if (["ppt", "pptx", "key", "odp"].includes(ext)) return { label: ext.toUpperCase(), color: "#E8643A", group: "slides" };
  if (["csv", "xls", "xlsx", "json", "tsv"].includes(ext)) return { label: ext.toUpperCase(), color: "#2F855A", group: "data" };
  return { label: (ext || "FILE").toUpperCase().slice(0, 4), color: "#8A8178", group: "other" };
}

export const prettySize = (n: number) =>
  n < 1024 ? `${n} B` : n < 2 ** 20 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 2 ** 20).toFixed(1)} MB`;
