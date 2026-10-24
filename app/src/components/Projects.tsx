import { useEffect, useRef, useState } from "react";
import { supabase, type Chat } from "../lib/supabase";
import { deleteFile, uploadFile, type Attachment, type FileRow } from "../lib/files";
import { Composer } from "./Composer";
import { FileCard, Preview } from "./Files";
import { IconArrowLeft, IconFolder, IconPlus, IconTrash } from "./Icons";

export type Project = {
  id: string;
  owner: string;
  name: string;
  description: string;
  instructions: string;
  created_at: string;
  updated_at: string;
};

type Props = {
  userId: string;
  chats: Chat[];
  openId: string | null;
  onOpenProject: (id: string | null) => void;
  onOpenChat: (id: string) => void;
  onStartChat: (text: string, attachments: Attachment[], projectId: string) => void;
  draft: { model: string; effort: string };
  onDraft: (model: string, effort: string) => void;
};

export function ProjectsPage(p: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const load = () => supabase.from("projects").select("*").order("updated_at", { ascending: false })
    .then(({ data }) => setProjects((data as Project[]) || []));
  useEffect(() => { load(); }, []);

  const open = projects.find((x) => x.id === p.openId);
  if (open) return <ProjectDetail {...p} project={open} onChanged={load} />;

  const create = async () => {
    const name = prompt("Project name", "New project");
    if (!name?.trim()) return;
    const { data } = await supabase.from("projects").insert({ owner: p.userId, name: name.trim() }).select().single();
    await load();
    if (data) p.onOpenProject(data.id);
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1 className="serif">Projects</h1>
        <button className="btn primary" onClick={create}><IconPlus size={16} /> New project</button>
      </div>
      {projects.length === 0 ? (
        <div className="empty-state">
          <div className="empty-art"><IconFolder size={30} /></div>
          <p><b>Looking to start a project?</b></p>
          <p className="muted">Upload materials, set custom instructions, and keep related chats in one space.</p>
          <button className="btn" onClick={create}>New project</button>
        </div>
      ) : (
        <div className="project-grid">
          {projects.map((x) => {
            const n = p.chats.filter((c) => c.project_id === x.id).length;
            return (
              <button key={x.id} className="project-card" onClick={() => p.onOpenProject(x.id)}>
                <div className="project-name">{x.name}</div>
                <div className="muted small project-desc">{x.description || x.instructions || "No description"}</div>
                <div className="muted small">{n} chat{n === 1 ? "" : "s"} · updated {new Date(x.updated_at).toLocaleDateString()}</div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ProjectDetail({ project, userId, chats, onOpenProject, onOpenChat, onStartChat, onChanged, draft, onDraft }: Props & { project: Project; onChanged: () => void }) {
  const [form, setForm] = useState({ name: project.name, description: project.description, instructions: project.instructions });
  const [files, setFiles] = useState<FileRow[]>([]);
  const [saved, setSaved] = useState(true);
  const [preview, setPreview] = useState<FileRow | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const timer = useRef<number | undefined>(undefined);

  const loadFiles = () => supabase.from("files").select("*").eq("project_id", project.id).eq("kind", "project")
    .order("created_at").then(({ data }) => setFiles((data as FileRow[]) || []));
  useEffect(() => { loadFiles(); }, [project.id]);

  const edit = (patch: Partial<typeof form>) => {
    const next = { ...form, ...patch };
    setForm(next);
    setSaved(false);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      await supabase.from("projects").update(next).eq("id", project.id);
      setSaved(true);
      onChanged();
    }, 600);
  };

  const upload = async (list: FileList) => {
    setBusy(true);
    try {
      for (const f of Array.from(list)) await uploadFile(userId, f, { kind: "project", projectId: project.id });
    } catch (e) { alert((e as Error).message); }
    setBusy(false);
    loadFiles();
  };

  const projectChats = chats.filter((c) => c.project_id === project.id);
  return (
    <div className="page">
      <button className="link back" onClick={() => onOpenProject(null)}><IconArrowLeft size={15} /> All projects</button>
      <input className="project-title-input serif" value={form.name} onChange={(e) => edit({ name: e.target.value })} />
      <input className="text-input wide" placeholder="What is this project about?" value={form.description}
        onChange={(e) => edit({ description: e.target.value })} />

      <div className="project-layout">
        <div className="project-main">
          <Composer model={draft.model} effort={draft.effort} onModel={onDraft}
            onSend={(text, att) => onStartChat(text, att, project.id)} />
          <h3 className="section-title">Chats in this project</h3>
          {projectChats.length === 0 && <p className="muted small">Start a chat above — Panda will use this project's instructions and files.</p>}
          {projectChats.map((c) => (
            <button key={c.id} className="project-chat" onClick={() => onOpenChat(c.id)}>
              <span>{c.title}</span><span className="muted small">{new Date(c.updated_at).toLocaleDateString()}</span>
            </button>
          ))}
        </div>
        <aside className="project-side">
          <div className="side-card">
            <div className="side-card-head"><b>Instructions</b><span className="muted small">{saved ? "Saved" : "Saving…"}</span></div>
            <textarea rows={7} placeholder="e.g. Write in a friendly, expert tone. Our audience is small-business owners in Italy."
              value={form.instructions} onChange={(e) => edit({ instructions: e.target.value })} />
          </div>
          <div className="side-card">
            <div className="side-card-head"><b>Files</b>
              <button className="btn small-btn" disabled={busy} onClick={() => input.current?.click()}>{busy ? "Uploading…" : "+ Add"}</button>
            </div>
            <input ref={input} type="file" multiple hidden onChange={(e) => { if (e.target.files) upload(e.target.files); e.target.value = ""; }} />
            {files.length === 0 && <p className="muted small">Brand guides, briefs, data — Panda reads them in every chat here.</p>}
            <div className="project-files">
              {files.map((f) => (
                <div key={f.id} className="project-file">
                  <FileCard f={f} onOpen={setPreview} />
                  <button className="icon-btn" title="Remove" onClick={async () => { await deleteFile(f); loadFiles(); }}><IconTrash size={15} /></button>
                </div>
              ))}
            </div>
          </div>
          <button className="link danger-link" onClick={async () => {
            if (!confirm(`Delete project "${project.name}"? Its chats are kept.`)) return;
            for (const f of files) await deleteFile(f);
            await supabase.from("projects").delete().eq("id", project.id);
            onOpenProject(null);
            onChanged();
          }}>Delete project</button>
        </aside>
      </div>
      {preview && <Preview file={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
