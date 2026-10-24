import { useCallback, useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { sessionAction, supabase, type Chat, type Notification } from "./lib/supabase";
import { SettingsProvider, useSettings } from "./lib/settings";
import { greetingKey, useT } from "./lib/i18n";
import { Login } from "./components/Login";
import { Sidebar } from "./components/Sidebar";
import { ChatView } from "./components/ChatView";
import { Composer } from "./components/Composer";
import { SessionChip, useWorker } from "./components/SessionChip";
import { NotificationsModal, SearchModal, SettingsModal } from "./components/Panels";
import { Logo } from "./components/Logo";
import { IconGlobe, IconPlus, IconSidebar } from "./components/Icons";
import { FilesPage } from "./components/Files";
import { BrowserPanel } from "./components/BrowserPanel";
import { BrowserContext, useBrowserLink } from "./lib/browser";
import { ProjectsPage } from "./components/Projects";
import { CustomizePage } from "./components/Customize";
import type { Attachment } from "./lib/files";

export type View = "chat" | "projects" | "files" | "customize";

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [recovery, setRecovery] = useState(false);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => { setSession(data.session); setReady(true); });
    const { data: sub } = supabase.auth.onAuthStateChange((event, s) => {
      setSession(s);
      // Invite and password-reset links land here: ask for a new password first.
      if (event === "PASSWORD_RECOVERY") setRecovery(true);
    });
    if (/type=(invite|recovery)/.test(window.location.hash)) setRecovery(true);
    return () => sub.subscription.unsubscribe();
  }, []);

  if (!ready) return <div className="splash"><Logo size={64} working /></div>;
  if (!session) return <Login />;
  if (recovery) return <Login recovery onDone={() => { setRecovery(false); history.replaceState(null, "", location.pathname); }} />;
  return (
    <SettingsProvider userId={session.user.id}>
      <Shell userId={session.user.id} email={session.user.email || ""} />
    </SettingsProvider>
  );
}

function Shell({ userId, email }: { userId: string; email: string }) {
  const { settings } = useSettings();
  const t = useT();
  const worker = useWorker(userId);
  const [chats, setChats] = useState<Chat[]>([]);
  const params = new URLSearchParams(location.search);
  const [activeId, setActiveId] = useState<string | null>(() => params.get("c"));
  const [view, setView] = useState<View>(() => (params.get("v") as View) || "chat");
  const [projectId, setProjectId] = useState<string | null>(() => params.get("p"));
  const [sideOpen, setSideOpen] = useState(() => window.innerWidth > 900);
  const [panel, setPanel] = useState<null | "settings" | "search" | "notifications">(
    () => (params.get("panel") === "notifications" ? "notifications" : null));
  const [notifs, setNotifs] = useState<Notification[]>([]);
  const browser = useBrowserLink(settings.browserTopic || undefined);
  const [browserOpen, setBrowserOpen] = useState(false);
  const browserLive = Date.now() - browser.lastSeen < 25000;
  const needsYou = browserLive && browser.status?.control === "needs_user";
  // Panda asks for help (login, captcha): open the live view automatically.
  useEffect(() => { if (needsYou) setBrowserOpen(true); }, [needsYou]);
  const [draft, setDraft] = useState({ model: settings.defaultModel, effort: settings.defaultEffort });

  useEffect(() => setDraft({ model: settings.defaultModel, effort: settings.defaultEffort }),
    [settings.defaultModel, settings.defaultEffort]);

  // Chats list + live updates.
  useEffect(() => {
    const load = () => supabase.from("chats").select("*").eq("archived", false)
      .order("pinned", { ascending: false }).order("updated_at", { ascending: false }).limit(200)
      .then(({ data }) => setChats((data as Chat[]) || []));
    load();
    const ch = supabase.channel("chats")
      .on("postgres_changes", { event: "*", schema: "public", table: "chats", filter: `owner=eq.${userId}` }, load)
      .subscribe();
    return () => { supabase.removeChannel(ch); };
  }, [userId]);

  // Notifications (scheduled task results) + browser notification when a new one lands.
  useEffect(() => {
    const load = () => supabase.from("notifications").select("*").order("id", { ascending: false }).limit(50)
      .then(({ data }) => setNotifs((data as Notification[]) || []));
    load();
    const ch = supabase.channel("notifs")
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "notifications", filter: `owner=eq.${userId}` },
        (p) => {
          load();
          const n = p.new as Notification;
          if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
            new Notification(n.meta?.title || "Panda", { body: n.text.slice(0, 180), icon: "logo.svg" });
          }
        })
      .subscribe();
    return () => { supabase.removeChannel(ch); };
  }, [userId]);

  useEffect(() => {
    const q = new URLSearchParams();
    if (view === "chat" && activeId) q.set("c", activeId);
    if (view !== "chat") q.set("v", view);
    if (view === "projects" && projectId) q.set("p", projectId);
    history.replaceState(null, "", q.toString() ? `?${q}` : location.pathname);
  }, [activeId, view, projectId]);

  const active = chats.find((c) => c.id === activeId) || null;

  const ensureWorker = useCallback(async () => {
    if (!worker.alive && settings.autoStart) {
      try { await sessionAction("start"); } catch { /* the chip shows errors */ }
    }
  }, [worker.alive, settings.autoStart]);

  const sendMessage = useCallback(async (chat: Chat, text: string, attachments: Attachment[] = []) => {
    await supabase.from("messages").insert({ chat_id: chat.id, owner: userId, role: "user", content: text,
      status: "pending", meta: attachments.length ? { attachments } : {} });
    const ids = attachments.map((a) => a.file_id).filter(Boolean) as string[];
    if (ids.length) supabase.from("files").update({ chat_id: chat.id }).in("id", ids).then(() => {});
    ensureWorker();
  }, [userId, ensureWorker]);

  const startChat = async (text: string, attachments: Attachment[] = [], inProject: string | null = null) => {
    const title = text.replace(/\s+/g, " ").slice(0, 48) + (text.length > 48 ? "…" : "");
    const { data, error } = await supabase.from("chats")
      .insert({ owner: userId, title, model: draft.model, effort: draft.effort, project_id: inProject }).select().single();
    if (error || !data) return alert(error?.message || "Could not create chat");
    setChats((prev) => [data as Chat, ...prev]);
    setActiveId(data.id);
    setView("chat");
    await sendMessage(data as Chat, text, attachments);
  };

  // "Ask Panda" from other pages: open a fresh chat with the composer pre-filled.
  const [prefill, setPrefill] = useState("");
  const askPanda = (text: string) => { setActiveId(null); setView("chat"); setPrefill(text); };

  const setModel = async (model: string, effort: string) => {
    if (!active) return setDraft({ model, effort });
    setChats((prev) => prev.map((c) => (c.id === active.id ? { ...c, model, effort } : c)));
    await supabase.from("chats").update({ model, effort }).eq("id", active.id);
  };

  const closeSideOnMobile = () => { if (window.innerWidth <= 900) setSideOpen(false); };
  const open = (id: string) => { setActiveId(id); setView("chat"); closeSideOnMobile(); };
  const go = (v: View) => { setView(v); if (v === "projects") setProjectId(null); closeSideOnMobile(); };
  const unread = notifs.filter((n) => !n.read).length;
  const name = settings.name || email.split("@")[0].replace(/[._\d]+/g, " ").trim().split(" ")[0] || "";

  return (
    <BrowserContext.Provider value={{ link: browser, openFull: () => setBrowserOpen(true), online: worker.alive && worker.session?.status === "running" }}>
    <div className={`app ${sideOpen ? "side-open" : ""}`}>
      {sideOpen && <div className="side-scrim" onClick={() => setSideOpen(false)} />}
      <Sidebar chats={chats} activeId={view === "chat" ? activeId : null} email={email} unread={unread} view={view} onGo={go}
        onNew={() => { setActiveId(null); setView("chat"); closeSideOnMobile(); }}
        onOpen={open} onSearch={() => setPanel("search")} onSettings={() => setPanel("settings")}
        onNotifications={() => setPanel("notifications")}
        onDeleted={(id) => { setChats((p) => p.filter((c) => c.id !== id)); if (id === activeId) setActiveId(null); }} />
      <main className="main">
        <header className="topbar">
          <button className="icon-btn" onClick={() => setSideOpen((v) => !v)} aria-label="Sidebar"><IconSidebar /></button>
          <div className="topbar-title">{view === "chat" ? active?.title || "" : ""}</div>
          {(browserLive || browserOpen) && (
            <button className={`icon-btn browser-btn ${browser.status?.busy ? "busy" : ""} ${needsYou ? "needs" : ""}`}
              onClick={() => setBrowserOpen(true)} aria-label="Open Panda's browser" title="Panda's browser"><IconGlobe /></button>
          )}
          <SessionChip worker={worker} />
          {(active || view !== "chat") && <button className="icon-btn" onClick={() => { setActiveId(null); setView("chat"); }} aria-label={t("newChat")}><IconPlus /></button>}
        </header>
        {view === "projects" ? (
          <ProjectsPage userId={userId} chats={chats} openId={projectId} onOpenProject={setProjectId} onOpenChat={open}
            onStartChat={startChat} draft={draft} onDraft={(model, effort) => setDraft({ model, effort })} />
        ) : view === "files" ? (
          <FilesPage onOpenChat={open} />
        ) : view === "customize" ? (
          <CustomizePage online={worker.alive && worker.session?.status === "running"} onAsk={askPanda} />
        ) : active ? (
          <ChatView key={active.id} chat={active} onModel={setModel} onSendMessage={sendMessage} />
        ) : (
          <div className="home">
            <h1 className="greeting serif"><Logo size={40} animated /> {t(greetingKey())}{name ? `, ${capitalize(name)}` : ""}</h1>
            <div className="home-composer">
              <Composer key={prefill} initialText={prefill} model={draft.model} effort={draft.effort} onModel={(model, effort) => setDraft({ model, effort })}
                onSend={(text, att) => { setPrefill(""); startChat(text, att); }} autoFocus />
            </div>
            <div className="starters">
              {["Write a LinkedIn post about my project", "Make a 6-slide deck about red pandas",
                "Find 4 photos of Tuscany for a blog post", "Every Monday at 9, send me my week's goals"].map((s) => (
                <button key={s} className="chip" onClick={() => startChat(s)}>{s}</button>
              ))}
            </div>
          </div>
        )}
      </main>
      {browserOpen && <BrowserPanel link={browser} online={worker.alive && worker.session?.status === "running"} onClose={() => setBrowserOpen(false)} />}
      {panel === "settings" && <SettingsModal email={email} userId={userId} onClose={() => setPanel(null)} />}
      {panel === "search" && <SearchModal chats={chats} onOpen={open} onClose={() => setPanel(null)} />}
      {panel === "notifications" && <NotificationsModal items={notifs} onClose={() => setPanel(null)} />}
    </div>
    </BrowserContext.Provider>
  );
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
