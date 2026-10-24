import { useState } from "react";
import { supabase, type Chat } from "../lib/supabase";
import { useT } from "../lib/i18n";
import { Wordmark } from "./Logo";
import { IconBell, IconEdit, IconFolder, IconGear, IconGrid, IconPin, IconPlus, IconSearch, IconSliders, IconTrash } from "./Icons";
import type { View } from "../App";

type Props = {
  chats: Chat[];
  activeId: string | null;
  email: string;
  unread: number;
  onNew: () => void;
  onOpen: (id: string) => void;
  onSearch: () => void;
  onSettings: () => void;
  onNotifications: () => void;
  onDeleted: (id: string) => void;
  view: View;
  onGo: (v: View) => void;
};

export function Sidebar(p: Props) {
  const t = useT();
  const [menu, setMenu] = useState<string | null>(null);
  const pinned = p.chats.filter((c) => c.pinned);
  const rest = p.chats.filter((c) => !c.pinned);

  const item = (c: Chat) => (
    <div key={c.id} className={`side-item ${c.id === p.activeId ? "active" : ""}`}>
      <button className="side-link" onClick={() => p.onOpen(c.id)}>{c.title || t("newChat")}</button>
      <button className="side-more" onClick={() => setMenu(menu === c.id ? null : c.id)}>⋯</button>
      {menu === c.id && (
        <div className="side-menu" onMouseLeave={() => setMenu(null)}>
          <button onClick={async () => {
            setMenu(null);
            const title = prompt(t("rename"), c.title);
            if (title?.trim()) await supabase.from("chats").update({ title: title.trim() }).eq("id", c.id);
          }}><IconEdit size={15} /> {t("rename")}</button>
          <button onClick={async () => {
            setMenu(null);
            await supabase.from("chats").update({ pinned: !c.pinned }).eq("id", c.id);
          }}><IconPin size={15} /> {c.pinned ? t("unpin") : t("pin")}</button>
          <button className="danger" onClick={async () => {
            setMenu(null);
            if (!confirm(`${t("delete")} "${c.title}"?`)) return;
            await supabase.from("chats").delete().eq("id", c.id);
            p.onDeleted(c.id);
          }}><IconTrash size={15} /> {t("delete")}</button>
        </div>
      )}
    </div>
  );

  return (
    <aside className="sidebar">
      <div className="side-top"><Wordmark /></div>
      <nav className="side-nav">
        <button onClick={p.onNew}><IconPlus /> {t("newChat")}</button>
        <button onClick={p.onSearch}><IconSearch /> {t("search")}</button>
        <button className={p.view === "projects" ? "active" : ""} onClick={() => p.onGo("projects")}><IconFolder /> Projects</button>
        <button className={p.view === "files" ? "active" : ""} onClick={() => p.onGo("files")}><IconGrid /> Files</button>
        <button className={p.view === "customize" ? "active" : ""} onClick={() => p.onGo("customize")}><IconSliders /> Customize</button>
        <button onClick={p.onNotifications}>
          <IconBell /> {t("notifications")} {p.unread > 0 && <span className="badge">{p.unread}</span>}
        </button>
      </nav>
      <div className="side-list">
        {pinned.length > 0 && <div className="side-label">{t("pinned")}</div>}
        {pinned.map(item)}
        <div className="side-label">{t("chats")}</div>
        {rest.length === 0 && pinned.length === 0 && <div className="muted side-empty">{t("empty")}</div>}
        {rest.map(item)}
      </div>
      <div className="side-foot">
        <span className="avatar-circle">{(p.email[0] || "?").toUpperCase()}</span>
        <span className="side-email">{p.email}</span>
        <button className="icon-btn" onClick={p.onSettings} title={t("settings")}><IconGear /></button>
      </div>
    </aside>
  );
}
