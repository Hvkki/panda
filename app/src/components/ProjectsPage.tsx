import { useState } from "react";
import type { Chat, Project } from "../lib/store";
import { Composer } from "./Composer";
import { IconArrowLeft, IconFolder, IconPlus, IconTrash } from "./Icons";

type Props = {
  projects: Project[];
  chats: Chat[];
  openId: string | null;
  online: boolean;
  draft: { model: string; effort: string };
  onDraft: (model: string, effort: string) => void;
  onOpenProject: (id: string | null) => void;
  onOpenChat: (id: string) => void;
  onStartChat: (text: string, attached: string[], projectId: string) => void;
  onAdd: (name: string) => Project;
  onPatch: (id: string, patch: Partial<Project>) => void;
  onRemove: (id: string) => void;
};

export function ProjectsPage(p: Props) {
  const open = p.projects.find((x) => x.id === p.openId);
  if (open) return <ProjectDetail {...p} project={open} />;

  const create = () => {
    const name = prompt("Project name", "New project");
    if (name?.trim()) p.onOpenProject(p.onAdd(name.trim()).id);
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1 className="serif">Projects</h1>
        <button className="btn primary" onClick={create}><IconPlus size={16} /> New project</button>
      </div>
      {p.projects.length === 0 ? (
        <div className="empty-state">
          <div className="empty-art"><IconFolder size={30} /></div>
          <p><b>Looking to start a project?</b></p>
          <p className="muted">Give Claude custom instructions and keep related chats together. Each project gets its own folder on the worker.</p>
          <button className="btn" onClick={create}>New project</button>
        </div>
      ) : (
        <div className="project-grid">
          {p.projects.map((x) => {
            const n = p.chats.filter((c) => c.project_id === x.id).length;
            return (
              <button key={x.id} className="project-card" onClick={() => p.onOpenProject(x.id)}>
                <div className="project-name">{x.name}</div>
                <div className="muted small project-desc">{x.instructions || "No instructions yet"}</div>
                <div className="muted small">{n} chat{n === 1 ? "" : "s"} · updated {new Date(x.updated).toLocaleDateString()}</div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ProjectDetail(p: Props & { project: Project }) {
  const { project } = p;
  const [instructions, setInstructions] = useState(project.instructions);
  const chats = p.chats.filter((c) => c.project_id === project.id).sort((a, b) => b.updated - a.updated);
  const saveInstructions = () => { if (instructions !== project.instructions) p.onPatch(project.id, { instructions }); };

  return (
    <div className="page">
      <button className="btn ghost back-btn" onClick={() => p.onOpenProject(null)}><IconArrowLeft size={16} /> All projects</button>
      <input className="project-title-input serif" defaultValue={project.name} aria-label="Project name"
        onBlur={(e) => { const name = e.target.value.trim(); if (name && name !== project.name) p.onPatch(project.id, { name }); }} />
      <div className="project-layout">
        <div>
          <Composer model={p.draft.model} effort={p.draft.effort} onModel={p.onDraft} canAttach={p.online}
            target={{ folder: project.id }} placeholder={`New chat in ${project.name}`}
            onSend={(text, attached) => p.onStartChat(text, attached, project.id)} />
          <div className="project-chats">
            {chats.length === 0 ? <p className="muted pad">Chats in this project show up here.</p> : chats.map((c) => (
              <button key={c.id} className="project-chat" onClick={() => p.onOpenChat(c.id)}>
                <span>{c.title}</span>
                <span className="muted small">{new Date(c.updated).toLocaleDateString()}</span>
              </button>
            ))}
          </div>
        </div>
        <aside className="project-side">
          <div className="side-card">
            <h3>Instructions</h3>
            <p className="muted small">Claude follows these in every chat of this project.</p>
            <textarea className="instructions" rows={8} value={instructions} placeholder="e.g. Answer in Ukrainian. The code is in Python 3.12…"
              onChange={(e) => setInstructions(e.target.value)} onBlur={saveInstructions} />
            {instructions !== project.instructions && <button className="btn primary" onClick={saveInstructions}>Save</button>}
          </div>
          <div className="side-card">
            <h3>Files</h3>
            <p className="muted small">Files you attach in this project go to its folder on the worker. They are deleted when you press Stop, like all worker files.</p>
          </div>
          <button className="btn danger-text" onClick={() => {
            if (confirm(`Delete project "${project.name}"? Its chats stay in your chat list.`)) { p.onRemove(project.id); p.onOpenProject(null); }
          }}><IconTrash size={15} /> Delete project</button>
        </aside>
      </div>
    </div>
  );
}
