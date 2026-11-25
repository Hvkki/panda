import { useCallback, useEffect, useRef, useState } from "react";
import { AuthError, TurnGone, cancelChat, ensureWorker, getPassword, resumeChat, sessionAction, setPassword, streamChat, type Note, type StreamEvent } from "./lib/api";
import { applySettings, getSettings } from "./lib/settings";
import { useDismiss } from "./lib/dismiss";
import { CustomizePage } from "./components/CustomizePage";
import { BrowserPanel } from "./components/BrowserPanel";
import { NotificationsModal, SearchModal, SettingsModal, useNotes } from "./components/Panels";
import { uid, useChats, useProjects, type Chat, type Message } from "./lib/store";
import { Login } from "./components/Login";
import { Sidebar, type View } from "./components/Sidebar";
import { ProjectsPage } from "./components/ProjectsPage";
import { DeveloperPage } from "./components/DeveloperPage";
import { ChatView } from "./components/ChatView";
import { Composer } from "./components/Composer";
import { SessionChip, useWorker } from "./components/SessionChip";
import { FilesPage } from "./components/FilesPage";
import { Logo } from "./components/Logo";
import { IconDots, IconGauge, IconGlobe, IconPlus, IconSidebar, IconSliders } from "./components/Icons";
import { UsageRing, UsageSheet, chatUsage, usePlanUsage } from "./components/UsageSheet";
import { ModelSheet } from "./components/ModelSheet";
import { StudioPage } from "./components/StudioPage";
import { effortLabel, modelName } from "./lib/models";

export default function App() {
  const [signedIn, setSignedIn] = useState(!!getPassword());
  useEffect(applySettings, []);
  if (!signedIn) return <Login onDone={() => setSignedIn(true)} />;
  return <Shell onLogout={() => { setPassword(""); setSignedIn(false); }} />;
}

const PLACE_KEY = "panda.place.v1";
const VIEWS: View[] = ["chat", "files", "projects", "developer", "studio", "customize"];
function lastPlace(): { view: View; activeId: string | null; projectId: string | null } {
  try {
    const p = JSON.parse(localStorage.getItem(PLACE_KEY) || "{}");
    return { view: VIEWS.includes(p.view) ? p.view : "chat", activeId: typeof p.activeId === "string" ? p.activeId : null,
      projectId: typeof p.projectId === "string" ? p.projectId : null };
  } catch { return { view: "chat", activeId: null, projectId: null }; }
}

function Shell({ onLogout }: { onLogout: () => void }) {
  const { chats, add, remove, patchChat, patchMessage, importChats, clearAll } = useChats();
  const { projects, addProject, patchProject, removeProject } = useProjects();
  const projectsRef = useRef(projects);
  useEffect(() => { projectsRef.current = projects; }, [projects]);
  // The open page and chat survive a reload (phones reload background tabs), so a reply that is still
  // streaming stays on screen instead of the app opening on a blank new chat.
  const [projectId, setProjectId] = useState<string | null>(() => lastPlace().projectId);
  const worker = useWorker(onLogout);
  const [activeId, setActiveId] = useState<string | null>(() => {
    const id = lastPlace().activeId;
    return id && chats.some((c) => c.id === id) ? id : null;
  });
  const [view, setView] = useState<View>(() => lastPlace().view);
  useEffect(() => {
    try { localStorage.setItem(PLACE_KEY, JSON.stringify({ view, activeId, projectId })); } catch { /* blocked */ }
  }, [view, activeId, projectId]);
  const [sideOpen, setSideOpen] = useState(() => window.innerWidth > 900);
  const [draft, setDraft] = useState(() => ({ model: getSettings().defaultModel, effort: getSettings().defaultEffort }));
  const [modal, setModal] = useState<"search" | "notes" | "settings" | "usage" | null>(null);
  const [browserOpen, setBrowserOpen] = useState(false);
  const aborts = useRef(new Map<string, AbortController>());
  const active = chats.find((c) => c.id === activeId) || null;

  /** Handle a reply's stream events: write them into the assistant message `replyId`. */
  const handlers = useCallback((chat: Chat, replyId: string) => {
    const upd = (fn: (m: Message) => Message) => patchMessage(chat.id, replyId, fn);
    const h = { failed: false, flush: () => {}, onEvent: (_e: StreamEvent) => {} };
    // Text arrives a few characters at a time. Re-rendering a long chat for every piece froze phones,
    // so pieces are collected and shown at most ~8 times a second.
    let bufText = "", bufThinking = "", timer: number | undefined, newPara = false;
    h.flush = () => {
      window.clearTimeout(timer); timer = undefined;
      if (!bufText && !bufThinking) return;
      const t = bufText, th = bufThinking, para = newPara; bufText = ""; bufThinking = ""; newPara = false;
      upd((m) => {
        if (!t) return { ...m, thinking: (m.thinking || "") + th };
        const parts = [...(m.parts || [])];
        const lastPart = parts[parts.length - 1];
        if (lastPart?.k === "text" && !para) parts[parts.length - 1] = { k: "text", text: lastPart.text + t };
        else parts.push({ k: "text", text: t });
        const sep = m.content && (para || lastPart?.k === "tool") ? "\n\n" : "";
        return { ...m, parts, content: m.content + sep + t, thinking: th ? (m.thinking || "") + th : m.thinking };
      });
    };
    h.onEvent = (e: StreamEvent) => {
      if (e.t === "text" || e.t === "thinking") {
        if (e.t === "text") bufText += e.d; else bufThinking += e.d;
        if (timer === undefined) timer = window.setTimeout(h.flush, 80);
        return;
      }
      h.flush();
      if (e.t === "break") { newPara = true; return; }
      if (e.t === "session") patchChat(chat.id, (c) => ({ ...c, session_id: e.id }));
      else if (e.t === "turn") upd((m) => (m.turn === e.id ? m : { ...m, turn: e.id }));
      else if (e.t === "tool") upd((m) => {
        const tools = m.tools || [];
        if (tools.some((t) => t.id === e.id)) return { ...m, tools: tools.map((t) => (t.id === e.id ? { ...t, label: e.label } : t)) };
        const parts = e.parent ? m.parts || [] : [...(m.parts || []), { k: "tool" as const, id: e.id }];
        return { ...m, parts, tools: [...tools, { id: e.id, label: e.label, done: false, parent: e.parent || undefined }] };
      });
      else if (e.t === "tool_done") upd((m) => ({ ...m, tools: (m.tools || []).map((t) => (t.id === e.id ? { ...t, done: true, error: e.error } : t)) }));
      else if (e.t === "limits") window.dispatchEvent(new Event("panda:limits"));
      else if (e.t === "done") {
        if (e.session_id) patchChat(chat.id, (c) => ({ ...c, session_id: e.session_id }));
        if (e.error) h.failed = true;
        // Keep the streamed text: the result holds only the last paragraph. Use it only if nothing streamed.
        upd((m) => ({ ...m, content: m.content || e.text, parts: m.content ? m.parts : e.text ? [{ k: "text", text: e.text }] : m.parts,
          secs: e.secs, usage: e.usage, status: e.error ? "error" : "done", error: e.error ? e.text : undefined,
          tools: (m.tools || []).map((t) => (t.done ? t : { ...t, done: true })) }));
      } else if (e.t === "error") { h.failed = true; upd((m) => ({ ...m, status: "error", error: e.message })); }
    };
    return h;
  }, [patchChat, patchMessage]);

  /** Run one turn: make sure the worker is up, stream Claude Code's reply into the assistant message. */
  const runTurn = useCallback(async (chat: Chat, text: string, history: Message[]) => {
    const reply: Message = { id: uid(), role: "assistant", content: "", status: "waiting", tools: [], started: Date.now() };
    patchChat(chat.id, (c) => ({ ...c, messages: [...c.messages, reply] }));
    const upd = (fn: (m: Message) => Message) => patchMessage(chat.id, reply.id, fn);
    const ctl = new AbortController();
    aborts.current.set(chat.id, ctl);
    try {
      const url = await ensureWorker((s) => worker.setState(s), ctl.signal);
      worker.setState("on");
      upd((m) => ({ ...m, status: "streaming" }));
      const h = handlers(chat, reply.id);
      await streamChat(url, {
        chat_id: chat.id, message: text, model: chat.model, effort: chat.effort, session_id: chat.session_id,
        history: history.filter((m) => m.content).map((m) => ({ role: m.role, content: m.content })),
        system: projectsRef.current.find((p) => p.id === chat.project_id)?.instructions || undefined,
        repo: projectsRef.current.find((p) => p.id === chat.project_id)?.repo || undefined,
        folder: chat.project_id, dev: chat.dev,
      }, h.onEvent, ctl.signal);
      h.flush();
      if (!h.failed) upd((m) => (m.status === "streaming" ? { ...m, status: "done" } : m));
      notifyReply(chat.dev ? "Developer" : chat.title, h.failed);
    } catch (e) {
      if (e instanceof AuthError) return onLogout();
      const stopped = ctl.signal.aborted;
      upd((m) => ({ ...m, status: stopped ? "done" : "error", error: stopped ? undefined : (e as Error).message }));
    } finally {
      if (aborts.current.get(chat.id) === ctl) aborts.current.delete(chat.id);
    }
  }, [patchChat, patchMessage, worker, onLogout, handlers]);

  // A reply still marked as running when the page loads was cut off by a reload (phones reload
  // background tabs to save memory). The worker kept running it: pick it up from the start.
  const resumed = useRef(false);
  useEffect(() => {
    if (resumed.current) return;
    resumed.current = true;
    for (const chat of chats) {
      const m = chat.messages[chat.messages.length - 1];
      if (m?.role !== "assistant" || (m.status !== "streaming" && m.status !== "waiting")) continue;
      const upd = (fn: (x: Message) => Message) => patchMessage(chat.id, m.id, fn);
      const ctl = new AbortController();
      aborts.current.set(chat.id, ctl);
      (async () => {
        try {
          if (!m.turn) throw new TurnGone();
          const s = await sessionAction("status");
          if (s.state !== "on" || !s.url) throw new Error("The worker stopped before this reply finished. Send the message again.");
          upd((x) => ({ ...x, content: "", thinking: undefined, tools: [], parts: [], status: "streaming", turn: m.turn }));
          const h = handlers(chat, m.id);
          await resumeChat(s.url, chat.id, m.turn, h.onEvent, ctl.signal);
          h.flush();
          if (!h.failed) upd((x) => (x.status === "streaming" ? { ...x, status: "done" } : x));
        } catch (e) {
          if (e instanceof AuthError) return;
          const stopped = ctl.signal.aborted;
          const msg = e instanceof TurnGone ? "This reply was interrupted. Send the message again." : (e as Error).message;
          upd((x) => ({ ...x, status: stopped ? "done" : "error", error: stopped ? undefined : msg }));
        } finally {
          if (aborts.current.get(chat.id) === ctl) aborts.current.delete(chat.id);
        }
      })();
    }
  }, [chats, handlers, patchMessage]);

  const send = useCallback((chat: Chat, text: string, attached: string[]) => {
    if (getSettings().notifyReplies && typeof Notification !== "undefined" && Notification.permission === "default") Notification.requestPermission().catch(() => {});
    const body = attached.length ? `${text}\n\n[Attached files in the working directory: ${attached.join(", ")}]`.trim() : text;
    const user: Message = { id: uid(), role: "user", content: body };
    const history = chat.messages;
    patchChat(chat.id, (c) => ({ ...c, messages: [...c.messages, user] }));
    runTurn(chat, body, history);
  }, [patchChat, runTurn]);

  const startChat = (text: string, attached: string[], project_id?: string) => {
    const title = text.replace(/\s+/g, " ").slice(0, 48) + (text.length > 48 ? "…" : "");
    const chat: Chat = { id: uid(), title: title || "New chat", model: draft.model, effort: draft.effort, pinned: false,
      created: Date.now(), updated: Date.now(), messages: [], project_id };
    add(chat);
    setActiveId(chat.id);
    setView("chat");
    send(chat, text, attached);
  };

  /** Start a chat about something (from Customize, a notification…). */
  const ask = (text: string) => startChat(text, []);
  const openNote = (n: Note) => {
    const chat: Chat = { id: uid(), title: n.title, model: draft.model, effort: draft.effort, pinned: false,
      created: n.at * 1000, updated: Date.now(), session_id: n.session_id || undefined,
      messages: [{ id: uid(), role: "user", content: n.prompt || n.title },
        { id: uid(), role: "assistant", content: n.text, status: n.error ? "error" : "done", error: n.error ? n.text : undefined }] };
    add(chat);
    setActiveId(chat.id);
    setView("chat");
  };

  /** Stop always ends the reply on screen, even if its stream was already lost (e.g. after a reload). */
  const stop = (chat: Chat) => {
    aborts.current.get(chat.id)?.abort();
    aborts.current.delete(chat.id);
    cancelChat(chat.id);
    patchChat(chat.id, (c) => ({ ...c, messages: c.messages.map((m) =>
      m.role === "assistant" && (m.status === "streaming" || m.status === "waiting") ? { ...m, status: "done" } : m) }));
  };

  const retry = (chat: Chat) => {
    const i = chat.messages.length - 1;
    const question = chat.messages[i - 1];
    if (i < 1 || question?.role !== "user") return;
    const rest = chat.messages.slice(0, i - 1);
    patchChat(chat.id, (c) => ({ ...c, messages: [...rest, question] }));
    runTurn(chat, question.content, rest);
  };

  // The Developer chat: one per browser, created the first time the tab is opened.
  const devChat = chats.find((c) => c.dev) || null;
  useEffect(() => {
    if (view === "developer" && !devChat) add({ id: uid(), title: "Developer", model: "default", effort: "high", pinned: false,
      created: Date.now(), updated: Date.now(), messages: [], dev: true });
  }, [view, devChat, add]);

  // What the top bar describes: the open chat (or the Developer chat on its page).
  const headChat = view === "chat" ? active : view === "developer" ? devChat : null;
  const headProject = headChat?.project_id ? projects.find((p) => p.id === headChat.project_id) : undefined;
  const headUsage = chatUsage(headChat);
  const title = view === "chat" ? active?.title || "" : view === "developer" ? "Developer"
    : view === "projects" ? "Projects" : view === "studio" ? "3D studio" : view === "files" ? "Files" : view === "customize" ? "Customize" : "";
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useDismiss(menuOpen, () => setMenuOpen(false), [menuRef]);
  // Slash commands Panda answers itself (/usage, /clear), sent by the composer.
  useEffect(() => {
    const usage = () => setModal("usage");
    const clear = () => {
      if (view === "developer" && devChat) patchChat(devChat.id, (c) => ({ ...c, messages: [], session_id: undefined }));
      else { setActiveId(null); setView("chat"); }
    };
    window.addEventListener("panda:usage", usage);
    window.addEventListener("panda:new-chat", clear);
    return () => { window.removeEventListener("panda:usage", usage); window.removeEventListener("panda:new-chat", clear); };
  }, [view, devChat, patchChat]);
  const [modelOpen, setModelOpen] = useState(false);

  const closeSideOnMobile = () => { if (window.innerWidth <= 900) setSideOpen(false); };
  const online = worker.state === "on";
  const notes = useNotes(online);
  const plan = usePlanUsage(online);

  return (
    <div className={`app ${sideOpen ? "side-open" : ""}`}>
      {sideOpen && <div className="side-scrim" onClick={() => setSideOpen(false)} />}
      <Sidebar chats={chats} activeId={view === "chat" ? activeId : null} view={view} onGo={(v) => { setView(v); if (v === "projects") setProjectId(null); closeSideOnMobile(); }}
        onNew={() => { setActiveId(null); setView("chat"); closeSideOnMobile(); }}
        onOpen={(id) => { setActiveId(id); setView("chat"); closeSideOnMobile(); }}
        onRename={(id, title) => patchChat(id, (c) => ({ ...c, title }))}
        onPin={(id) => patchChat(id, (c) => ({ ...c, pinned: !c.pinned }))}
        onDelete={(id) => { remove(id); if (id === activeId) setActiveId(null); }}
        onSearch={() => { setModal("search"); closeSideOnMobile(); }}
        onNotifications={() => { setModal("notes"); notes.markSeen(); closeSideOnMobile(); }} unread={notes.unread}
        onSettings={() => { setModal("settings"); closeSideOnMobile(); }} />
      <main className="main">
        <header className="topbar">
          <button className="icon-btn" onClick={() => setSideOpen((v) => !v)} aria-label="Sidebar"><IconSidebar /></button>
          <div className="topbar-center">
            <div className="topbar-title">{title}</div>
            {headChat && (
              <button className="topbar-sub" onClick={() => setModelOpen(true)}>
                {headProject ? headProject.name : headChat.dev ? "panda" : "Chat"} · {modelName(headChat.model)}
                {headChat.effort !== "medium" ? ` · ${effortLabel(headChat.effort)}` : ""}
              </button>
            )}
          </div>
          <button className="icon-btn" onClick={() => setModal("usage")} aria-label="Usage"
            title={headChat ? `Context ${Math.round(headUsage.pct)}% used` : "Usage"}>
            <UsageRing pct={headChat ? headUsage.pct : plan.data?.windows.five_hour?.pct ?? 0} />
          </button>
          <div className="menu-anchor" ref={menuRef}>
            <button className="icon-btn" onClick={() => setMenuOpen((v) => !v)} aria-label="More" aria-expanded={menuOpen}>
              <IconDots /><span className={`state-dot ${worker.state}`} />
            </button>
            {menuOpen && (
              <>
                <div className="top-menu" role="menu">
                  <div className="top-menu-session"><SessionChip worker={worker} /></div>
                  <button onClick={() => { setMenuOpen(false); setActiveId(null); setView("chat"); }}><IconPlus size={18} /> New chat</button>
                  {headChat && <button onClick={() => { setMenuOpen(false); setModelOpen(true); }}><IconSliders size={18} /> Model and effort</button>}
                  <button onClick={() => { setMenuOpen(false); setModal("usage"); }}><IconGauge size={18} /> Usage</button>
                  {online && <button onClick={() => { setMenuOpen(false); setBrowserOpen(true); }}><IconGlobe size={18} /> Browser</button>}
                </div>
              </>
            )}
          </div>
        </header>
        {view === "studio" ? <StudioPage online={online} /> : view === "files" ? <FilesPage online={online} /> : view === "customize" ? <CustomizePage online={online} onAsk={ask} /> : view === "projects" ? (
          <ProjectsPage projects={projects} chats={chats} openId={projectId} online={online} draft={draft}
            onDraft={(model, effort) => setDraft({ model, effort })} onOpenProject={setProjectId}
            onOpenChat={(id) => { setActiveId(id); setView("chat"); }}
            onStartChat={(text, attached, pid) => startChat(text, attached, pid)}
            onAdd={addProject} onPatch={patchProject} onRemove={removeProject} />
        ) : view === "developer" ? (
          <DeveloperPage online={online} chat={devChat && (
            <ChatView key={devChat.id} chat={devChat} canAttach={online}
              onModel={(model, effort) => patchChat(devChat.id, (c) => ({ ...c, model, effort }))}
              onSend={(t, a) => send(devChat, t, a)} onRetry={() => retry(devChat)} onStop={() => stop(devChat)} />
          )} />
        ) : active ? (
          <ChatView key={active.id} chat={active} canAttach={online}
            onModel={(model, effort) => patchChat(active.id, (c) => ({ ...c, model, effort }))}
            onSend={(t, a) => send(active, t, a)} onRetry={() => retry(active)} onStop={() => stop(active)} />
        ) : (
          <div className="home">
            <h1 className="greeting serif"><Logo size={40} animated /> What are we working on?</h1>
            <div className="home-composer">
              <Composer draftKey="home" model={draft.model} effort={draft.effort} onModel={(model, effort) => setDraft({ model, effort })}
                onSend={startChat} autoFocus canAttach={false} />
            </div>
          </div>
        )}
      </main>
      {modal === "usage" && <UsageSheet chat={headChat} online={online} plan={plan} onClose={() => setModal(null)} />}
      {modelOpen && headChat && <ModelSheet model={headChat.model} effort={headChat.effort} onClose={() => setModelOpen(false)}
        onChange={(model, effort) => patchChat(headChat.id, (c) => ({ ...c, model, effort }))} />}
      {browserOpen && online && <BrowserPanel onClose={() => setBrowserOpen(false)} />}
      {modal === "search" && <SearchModal chats={chats} onClose={() => setModal(null)}
        onOpen={(id) => { const c = chats.find((x) => x.id === id); setActiveId(id); setView(c?.dev ? "developer" : "chat"); }} />}
      {modal === "notes" && <NotificationsModal notes={notes.notes} online={online} onClose={() => setModal(null)}
        onOpenNote={openNote} onChanged={notes.setNotes} />}
      {modal === "settings" && <SettingsModal online={online} chats={chats} onImport={importChats} onClearAll={() => { clearAll(); setActiveId(null); }}
        onLogout={() => { if (confirm("Log out of Panda on this device?")) onLogout(); }} onClose={() => setModal(null)} />}
    </div>
  );
}

/** A system notification when a reply finishes while Panda is in the background. */
function notifyReply(title: string, failed: boolean) {
  if (!getSettings().notifyReplies || !document.hidden || typeof Notification === "undefined") return;
  if (Notification.permission !== "granted") return;
  const head = failed ? "Panda: something went wrong" : "Panda replied";
  const opts = { body: title, icon: "/icon-192.png" };
  if ("serviceWorker" in navigator) navigator.serviceWorker.getRegistration().then((r) => { if (r) return r.showNotification(head, opts); new Notification(head, opts); }).catch(() => {});
  else try { new Notification(head, opts); } catch { /* not supported */ }
}
