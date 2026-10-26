import { useEffect, useMemo, useState } from "react";
import { supabase } from "../lib/supabase";
import { deleteFile, driveUrl, fileKind, isDrive, prettySize, signedUrl, type FileRow } from "../lib/files";
import { IconDoc, IconFile, IconImage, IconPin, IconSearch, IconSlides, IconTable, IconTrash, IconX } from "./Icons";

/** Resolve a `panda-file:<id>` reference (used in replies) to a signed URL. */
export function usePandaFile(ref: string | undefined) {
  const [url, setUrl] = useState<string>("");
  useEffect(() => {
    if (!ref?.startsWith("panda-file:")) { setUrl(ref || ""); return; }
    let alive = true;
    supabase.from("files").select("path").eq("id", ref.slice(11)).maybeSingle().then(async ({ data }) => {
      if (data?.path && alive) setUrl(await signedUrl(data.path).catch(() => ""));
    });
    return () => { alive = false; };
  }, [ref]);
  return url;
}

/** A path on the worker's disk (named in a reply but never uploaded): the app cannot load it. */
const isLocalPath = (s?: string) => !!s && !/^(panda-file:|https?:|data:|blob:)/i.test(s);

export function PandaImage({ src, alt, live }: { src?: string; alt?: string; live?: boolean }) {
  const local = isLocalPath(src);
  const url = usePandaFile(local ? undefined : src);
  const [broken, setBroken] = useState(false);
  // While the reply streams, the path is still the worker's: the uploaded copy replaces it at the end.
  if (local) return live ? <span className="img-loading" /> : <MissingImage alt={alt} />;
  if (!src || broken) return <MissingImage alt={alt} />;
  if (!url) return <span className="img-loading" />;
  return <a href={url} target="_blank" rel="noreferrer"><img src={url} alt={alt || ""} loading="lazy" onError={() => setBroken(true)} /></a>;
}

function MissingImage({ alt }: { alt?: string }) {
  return <span className="img-missing"><IconImage size={16} />{alt ? `${alt} · ` : ""}picture not available</span>;
}

export function PandaLink(props: React.AnchorHTMLAttributes<HTMLAnchorElement>) {
  const url = usePandaFile(props.href);
  return <a {...props} href={url || "#"} target="_blank" rel="noreferrer" />;
}

/* ── preview overlay ─────────────────────────────────────────────────────── */
export function Preview({ file, onClose }: { file: FileRow; onClose: () => void }) {
  if (isDrive(file.path)) return <DrivePreview file={file} onClose={onClose} />;
  return <StoredPreview file={file} onClose={onClose} />;
}

function DrivePreview({ file, onClose }: { file: FileRow; onClose: () => void }) {
  useEffect(() => { window.open(driveUrl(file.path), "_blank", "noopener"); onClose(); }, [file.path, onClose]);
  return null;
}

function StoredPreview({ file, onClose }: { file: FileRow; onClose: () => void }) {
  const [url, setUrl] = useState("");
  const k = fileKind(file.name, file.mime);
  useEffect(() => { signedUrl(file.path).then(setUrl).catch(() => {}); }, [file.path]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  const office = ["DOCX", "DOC", "PPTX", "PPT", "XLSX", "XLS"].includes(k.label);
  return (
    <div className="preview-backdrop" onClick={onClose}>
      <div className="preview" onClick={(e) => e.stopPropagation()}>
        <div className="preview-head">
          <span className="file-badge" style={{ background: k.color }}>{k.label}</span>
          <b className="preview-title">{file.name}</b>
          <button className="btn primary small-btn" onClick={() => download(file)}>Download</button>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><IconX /></button>
        </div>
        <div className="preview-body">
          {!url ? <div className="muted pad">Loading…</div> :
            k.group === "image" ? <img src={url} alt={file.name} /> :
            k.label === "PDF" ? <iframe src={url} title={file.name} /> :
            office ? <iframe title={file.name} src={`https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(url)}`} /> :
            <iframe src={url} title={file.name} />}
        </div>
        {office && <p className="muted small preview-note">Preview by Microsoft Office Online (the link expires in 1 hour).</p>}
      </div>
    </div>
  );
}

async function download(f: FileRow) {
  if (isDrive(f.path)) { window.open(driveUrl(f.path), "_blank", "noopener"); return; }
  const url = await signedUrl(f.path, f.name);
  const a = document.createElement("a");
  a.href = url;
  a.download = f.name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/* ── card ────────────────────────────────────────────────────────────────── */
export function FileCard({ f, onOpen, big }: { f: FileRow; onOpen: (f: FileRow) => void; big?: boolean }) {
  const k = fileKind(f.name, f.mime);
  const [thumb, setThumb] = useState("");
  useEffect(() => { if (k.group === "image" && !isDrive(f.path)) signedUrl(f.path).then(setThumb).catch(() => {}); }, [f.path, k.group]);
  return (
    <button className={`file-card ${big ? "big" : ""}`} onClick={() => onOpen(f)}>
      <div className="file-thumb" style={{ background: `color-mix(in srgb, ${k.color} 14%, var(--panel))` }}>
        {thumb ? <img src={thumb} alt="" /> : <span className="file-icon" style={{ color: k.color }}>{k.group === "slides" ? <IconSlides size={26} /> : k.group === "data" ? <IconTable size={26} /> : k.group === "image" ? <IconImage size={26} /> : k.group === "doc" ? <IconDoc size={26} /> : <IconFile size={26} />}</span>}
      </div>
      <div className="file-meta">
        <div className="file-name">{f.name}</div>
        <div className="muted small"><span className="file-badge" style={{ background: k.color }}>{k.label}</span> {prettySize(f.size)}
          {isDrive(f.path) && <span className="drive-tag"> · Google Drive</span>}</div>
      </div>
    </button>
  );
}

/** Files produced by one assistant message, shown as cards under it. */
export function MessageFiles({ messageId, ids }: { messageId: number; ids?: string[] }) {
  const [files, setFiles] = useState<FileRow[]>([]);
  const [open, setOpen] = useState<FileRow | null>(null);
  useEffect(() => {
    if (!ids?.length) return;
    supabase.from("files").select("*").eq("message_id", messageId).order("created_at").then(({ data }) => setFiles((data as FileRow[]) || []));
  }, [messageId, ids?.length]);
  if (!files.length) return null;
  return (
    <>
      <div className="file-cards">{files.map((f) => <FileCard key={f.id} f={f} onOpen={setOpen} />)}</div>
      {open && <Preview file={open} onClose={() => setOpen(null)} />}
    </>
  );
}

/* ── Files page (all artifacts) ─────────────────────────────────────────── */
const FILTERS = [["all", "All"], ["doc", "Docs"], ["slides", "Slides"], ["image", "Images"], ["data", "Data"], ["drive", "Drive"], ["upload", "Uploaded"]] as const;

export function FilesPage({ onOpenChat }: { onOpenChat: (id: string) => void }) {
  const [files, setFiles] = useState<FileRow[]>([]);
  const [filter, setFilter] = useState<(typeof FILTERS)[number][0]>("all");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<FileRow | null>(null);
  const load = () => supabase.from("files").select("*").neq("kind", "project").order("pinned", { ascending: false })
    .order("created_at", { ascending: false }).limit(300).then(({ data }) => setFiles((data as FileRow[]) || []));
  useEffect(() => { load(); }, []);

  const shown = useMemo(() => files.filter((f) => {
    if (q && !f.name.toLowerCase().includes(q.toLowerCase())) return false;
    if (filter === "all") return true;
    if (filter === "upload") return f.kind === "upload";
    if (filter === "drive") return isDrive(f.path);
    return f.kind === "output" && fileKind(f.name, f.mime).group === filter;
  }), [files, filter, q]);

  return (
    <div className="page">
      <div className="page-head">
        <h1 className="serif">Files</h1>
        <div className="search-box"><IconSearch size={16} /><input value={q} placeholder="Search files" onChange={(e) => setQ(e.target.value)} /></div>
      </div>
      <div className="tabs">{FILTERS.map(([id, label]) => (
        <button key={id} className={filter === id ? "active" : ""} onClick={() => setFilter(id)}>{label}</button>
      ))}</div>
      {shown.length === 0 && <div className="empty-state">
        <div className="empty-art"><IconDoc size={30} /></div>
        <p>Documents, slides and images Panda makes for you show up here.</p>
        <p className="muted small">Try: “Create a PDF one-pager about my product” or “Make a 6-slide deck about red pandas”.</p>
      </div>}
      <div className="file-grid">
        {shown.map((f) => (
          <div key={f.id} className="file-grid-item">
            <FileCard f={f} onOpen={setOpen} big />
            <div className="file-actions">
              {f.chat_id && <button className="link" onClick={() => onOpenChat(f.chat_id!)}>Open chat</button>}
              <button className="icon-btn" title={f.pinned ? "Unpin" : "Pin"}
                onClick={async () => { await supabase.from("files").update({ pinned: !f.pinned }).eq("id", f.id); load(); }}>
                <IconPin size={15} /></button>
              <button className="icon-btn" title="Delete" onClick={async () => {
                if (!confirm(`Delete ${f.name}?`)) return;
                await deleteFile(f);
                load();
              }}><IconTrash size={15} /></button>
            </div>
          </div>
        ))}
      </div>
      {open && <Preview file={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
