import { useState } from "react";
import type { Chat } from "../lib/store";
import { Wordmark } from "./Logo";
import { IconBell, IconCode, IconEdit, IconFolder, IconGear, IconGrid, IconPin, IconPlus, IconSearch, IconSliders, IconTrash } from "./Icons";

export type View = "chat" | "files" | "projects" | "developer" | "customize";

type Props = {
  chats: Chat[];
  activeId: string | null;
  view: View;
  onGo: (v: View) => void;
  onNew: () => void;
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onPin: (id: string) => void;
  onDelete: (id: string) => void;
  onSearch: () => void;
  onNotifications: () => void;
  unread: number;
  onSettings: () => void;
};

export function Sidebar(p: Props) {
  const [menu, setMenu] = useState<string | null>(null);
  const sorted = p.chats.filter((c) => !c.dev).sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.updated - a.updated);
  return (
    <aside className="sidebar">
      <div className="side-top"><Wordmark /></div>
      <nav className="side-nav">
        <button onClick={p.onNew}><IconPlus /> New chat</button>
        <button onClick={p.onSearch}><IconSearch /> Search</button>
        <button className={p.view === "projects" ? "active" : ""} onClick={() => p.onGo("projects")}><IconFolder /> Projects</button>
        <button className={p.view === "developer" ? "active" : ""} onClick={() => p.onGo("developer")}><IconCode /> Developer</button>
        <button className={p.view === "files" ? "active" : ""} onClick={() => p.onGo("files")}><IconGrid /> Files</button>
        <button className={p.view === "customize" ? "active" : ""} onClick={() => p.onGo("customize")}><IconSliders /> Customize</button>
        <button onClick={p.onNotifications}><IconBell /> Notifications {p.unread > 0 && <span className="badge">{p.unread}</span>}</button>
      </nav>
      <div className="side-list">
        <div className="side-label">Chats</div>
        {sorted.length === 0 && <div className="muted side-empty">No chats yet</div>}
        {sorted.map((c) => (
          <div key={c.id} className={`side-item ${c.id === p.activeId ? "active" : ""}`}>
            <button className="side-link" onClick={() => p.onOpen(c.id)}>{c.title || "New chat"}</button>
            <button className="side-more" onClick={() => setMenu(menu === c.id ? null : c.id)}>⋯</button>
            {menu === c.id && (
              <div className="side-menu" onMouseLeave={() => setMenu(null)}>
                <button onClick={() => { setMenu(null); const t = prompt("Rename", c.title); if (t?.trim()) p.onRename(c.id, t.trim()); }}><IconEdit size={15} /> Rename</button>
                <button onClick={() => { setMenu(null); p.onPin(c.id); }}><IconPin size={15} /> {c.pinned ? "Unpin" : "Pin"}</button>
                <button className="danger" onClick={() => { setMenu(null); if (confirm(`Delete "${c.title}"?`)) p.onDelete(c.id); }}><IconTrash size={15} /> Delete</button>
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="side-foot">
        <span className="avatar-circle">P</span>
        <span className="side-email">Claude Code on Kaggle</span>
        <button className="icon-btn" onClick={p.onSettings} title="Settings" aria-label="Settings"><IconGear /></button>
      </div>
    </aside>
  );
}
