import { useCallback, useEffect, useMemo, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { onRejoin, onResume, sessionAction, supabase, type Chat, type Notification } from "./lib/supabase";
import { SettingsProvider, useSettings } from "./lib/settings";
import { greetingKey, useT } from "./lib/i18n";
import { Login } from "./components/Login";
import { Sidebar } from "./components/Sidebar";
import { ChatView } from "./components/ChatView";
import { Composer, type SendMeta } from "./components/Composer";
import { SessionChip, useWorker } from "./components/SessionChip";
import { NotificationsModal, SearchModal, SettingsModal, type SettingsTab } from "./components/Panels";
import { Logo } from "./components/Logo";
import { IconGlobe, IconIncognito, IconPlus, IconSidebar } from "./components/Icons";
import { FilesPage } from "./components/Files";
import { VideoPage } from "./components/VideoTab";
import { BrowserPanel } from "./components/BrowserPanel";
import { BrowserContext, isAlive, useBrowserLink, useBrowserSelect } from "./lib/browser";
import { ProjectsPage } from "./components/Projects";
import { CustomizePage } from "./components/Customize";
import { DeveloperPage } from "./components/Developer";
import type { Attachment } from "./lib/files";

export type View = "chat" | "projects" | "files" | "video" | "customize" | "developer";

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

  if (!ready) return <div className="splash"><Logo size={72} working /></div>;
  if (!session) return <Login />;
  if (recovery) return <Login recovery onDone={() => { setRecovery(false); history.replaceState(null, "", location.pathname); }} />;
  return (
    <SettingsProvider userId={session.user.id}>
      <Shell userId={session.user.id} email={session.user.email || ""} />
    </SettingsProvider>
  );
}


function useWide(px: number) {
  const [wide, setWide] = useState(() => window.innerWidth >= px);
  useEffect(() => {
    const on = () => setWide(window.innerWidth >= px);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, [px]);
  return wide;
}

function Shell({ userId, email }: { userId: string; email: string }) {
  const { settings } = useSettings();
  const t = useT();
  const worker = useWorker(userId);
  const online = worker.alive && worker.session?.status === "running";
  const [chats, setChats] = useState<Chat[]>([]);
  const params = new URLSearchParams(location.search);
  const [activeId, setActiveId] = useState<string | null>(() => params.get("c"));
  const [view, setView] = useState<View>(() => (params.get("v") as View) || "chat");
  const [projectId, setProjectId] = useState<string | null>(() => params.get("p"));
  const [sideOpen, setSideOpen] = useState(() => window.innerWidth > 900);
  const [panel, setPanel] = useState<null | "settings" | "search" | "notifications">(
    () => (params.get("panel") === "notifications" ? "notifications" : null));
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("general");
  const [notifs, setNotifs] = useState<Notification[]>([]);
  const [typing, setTyping] = useState(false);
  const browser = useBrowserLink(settings.browserTopic || undefined);
  const [browserOpen, setBrowserOpen] = useState(false);
  const browserLive = useBrowserSelect(browser, isAlive);
  const browserBusy = useBrowserSelect(browser, (s) => !!s.status?.busy);
  const needsYou = useBrowserSelect(browser, (s) => isAlive(s) && s.status?.control === "needs_user");
  const docked = useWide(1100);
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
      .subscribe(onRejoin(load));
    const off = onResume(load);
    return () => { supabase.removeChannel(ch); off(); };
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

  // The Developer chat lives in the Developer tab only, not in the chat list.
  const devId = settings.developerChatId;
  // Incognito chats never show in the list; the open one stays reachable while you're in it.
  const listed = useMemo(() => chats.filter((c) => c.id !== devId && (c.agent !== "incognito" || c.id === activeId)),
    [chats, devId, activeId]);
  const [incognito, setIncognito] = useState(false);
  // Leaving an incognito chat deletes it with its messages; one left behind by a closed app goes on the next load.
  useEffect(() => {
    const gone = chats.filter((c) => c.agent === "incognito" && c.id !== activeId);
    if (!gone.length) return;
    supabase.from("chats").delete().in("id", gone.map((c) => c.id)).then(() => {});
    setChats((p) => p.filter((c) => !gone.includes(c)));
  }, [activeId, chats]);
  const active = listed.find((c) => c.id === activeId) || null;

  const ensureWorker = useCallback(async () => {
    if (!worker.alive && settings.autoStart) {
      try { await sessionAction("start"); } catch { /* the chip shows errors */ }
    }
  }, [worker.alive, settings.autoStart]);

  const sendMessage = useCallback(async (chat: Chat, text: string, attachments: Attachment[] = [], meta?: SendMeta) => {
    const m: Record<string, unknown> = {};
    if (attachments.length) m.attachments = attachments;
    if (meta?.command) m.command = meta.command;
    if (meta?.steer) m.steer = true;
    const { error } = await supabase.from("messages").insert({ chat_id: chat.id, owner: userId, role: "user", content: text,
      status: "pending", meta: m });
    if (error) { alert(error.message); return; }
    const ids = attachments.map((a) => a.file_id).filter(Boolean) as string[];
    if (ids.length) supabase.from("files").update({ chat_id: chat.id }).in("id", ids).then(() => {});
    ensureWorker();
  }, [userId, ensureWorker]);

  const startChat = async (text: string, attachments: Attachment[] = [], inProject: string | null = null, meta?: SendMeta) => {
    const title = text.replace(/\s+/g, " ").slice(0, 48) + (text.length > 48 ? "…" : "");
    const { data, error } = await supabase.from("chats")
      .insert({ owner: userId, title, model: draft.model, effort: draft.effort, project_id: inProject,
        ...(incognito && !inProject ? { agent: "incognito" } : {}) }).select().single();
    if (error || !data) return alert(error?.message || "Could not create chat");
    setChats((prev) => [data as Chat, ...prev]);
    setIncognito(false);
    setActiveId(data.id);
    setView("chat");
    await sendMessage(data as Chat, text, attachments, meta);
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
  const openSettings = (tab: SettingsTab = "general") => { setSettingsTab(tab); setPanel("settings"); closeSideOnMobile(); };
  const unread = notifs.filter((n) => !n.read).length;
  const name = settings.name || email.split("@")[0].replace(/[._\d]+/g, " ").trim().split(" ")[0] || "";

  // Panda's app-wide "/" commands. Chat-only ones (/stop, /btw, /title) are handled in ChatView.
  const appCommand = (cmd: string): boolean => {
    if (cmd === "/new") { setActiveId(null); setView("chat"); return true; }
    if (cmd === "/credits") { openSettings("usage"); return true; }
    if (cmd === "/settings") { openSettings("general"); return true; }
    if (cmd === "/browser") { setBrowserOpen(true); return true; }
    return false;
  };

  const browserCtx = useMemo(() => ({ link: browser, openFull: () => setBrowserOpen(true), online }), [browser, online]);
  const panelOpen = browserOpen && !!browser;

  return (
    <BrowserContext.Provider value={browserCtx}>
    <div className={`app ${sideOpen ? "side-open" : ""} ${panelOpen && docked ? "with-dock" : ""}`}>
      {sideOpen && <div className="side-scrim" onClick={() => setSideOpen(false)} />}
      <Sidebar chats={listed} activeId={view === "chat" ? activeId : null} email={email} userId={userId} worker={worker}
        unread={unread} view={view} onGo={go}
        onNew={() => { setActiveId(null); setView("chat"); closeSideOnMobile(); }}
        onOpen={open} onSearch={() => setPanel("search")} onSettings={() => openSettings("general")}
        onUsage={() => openSettings("usage")}
        onNotifications={() => setPanel("notifications")}
        onDeleted={(id) => { setChats((p) => p.filter((c) => c.id !== id)); if (id === activeId) setActiveId(null); }} />
      <main className="main">
        <header className="topbar">
          <button className="icon-btn" onClick={() => setSideOpen((v) => !v)} aria-label="Sidebar"><IconSidebar /></button>
          <div className="topbar-title">{view === "chat" ? active?.title || "" : view === "developer" ? "Developer" : view === "video" ? "Video" : ""}</div>
          {(browserLive || browserOpen) && browser && (
            <button className={`icon-btn browser-btn ${browserBusy ? "busy" : ""} ${needsYou ? "needs" : ""}`}
              onClick={() => setBrowserOpen((v) => !v)} aria-label="Panda's browser" title="Panda's browser"><IconGlobe /></button>
          )}
          <SessionChip worker={worker} />
          {(active || view !== "chat") && <button className="icon-btn" onClick={() => { setActiveId(null); setView("chat"); }} aria-label={t("newChat")}><IconPlus /></button>}
        </header>
        {view === "projects" ? (
          <ProjectsPage userId={userId} chats={chats} openId={projectId} onOpenProject={setProjectId} onOpenChat={open}
            onStartChat={startChat} draft={draft} onDraft={(model, effort) => setDraft({ model, effort })} online={online} />
        ) : view === "files" ? (
          <FilesPage onOpenChat={open} />
        ) : view === "video" ? (
          <VideoPage userId={userId} online={online} />
        ) : view === "customize" ? (
          <CustomizePage online={online} onAsk={askPanda} />
        ) : view === "developer" ? (
          <DeveloperPage userId={userId} chats={chats} online={online} onSendMessage={sendMessage}
            onChat={(c) => setChats((prev) => (prev.some((x) => x.id === c.id) ? prev : [c, ...prev]))} />
        ) : active ? (
          <ChatView key={active.id} chat={active} onModel={setModel} onSendMessage={sendMessage} online={online}
            onCommand={(n) => appCommand(n)} />
        ) : (
          <div className={`home ${typing ? "typing" : ""} ${incognito ? "incognito" : ""}`}>
            <button className={`icon-btn home-incognito ${incognito ? "on" : ""}`} onClick={() => setIncognito((v) => !v)}
              aria-pressed={incognito} aria-label={t("incognito")} title={t("incognito")}><IconIncognito /></button>
            <div className="home-hero">
              <div className="home-mark"><Logo size={64} animated look={typing ? "down" : null} /></div>
              <h1 className="greeting serif">{incognito ? t("incognito") : <>{t(greetingKey())}{name ? `, ${capitalize(name)}` : ""}</>}</h1>
              {incognito && <p className="muted incognito-note">{t("incognitoNote")}</p>}
            </div>
            <div className="chat-bottom home-bottom">
              <Composer key={prefill} initialText={prefill} model={draft.model} effort={draft.effort} online={online}
                onModel={(model, effort) => setDraft({ model, effort })} onTyping={setTyping}
                onCommand={(n) => appCommand(n)} context={{ pct: 0, reset: true }} draftKey="home"
                onSend={(text, att, meta) => { setPrefill(""); startChat(text, att, null, meta); }} autoFocus />
            </div>
          </div>
        )}
      </main>
      {panelOpen && <BrowserPanel link={browser!} online={online} docked={docked} onClose={() => setBrowserOpen(false)} />}
      {panel === "settings" && <SettingsModal email={email} userId={userId} worker={worker} online={online}
        initialTab={settingsTab} onClose={() => setPanel(null)} />}
      {panel === "search" && <SearchModal chats={listed} onOpen={open} onClose={() => setPanel(null)} />}
      {panel === "notifications" && <NotificationsModal items={notifs} online={online} onClose={() => setPanel(null)} />}
    </div>
    </BrowserContext.Provider>
  );
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
