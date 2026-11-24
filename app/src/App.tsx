import { useCallback, useEffect, useRef, useState } from "react";
import { AuthError, cancelChat, ensureWorker, getPassword, setPassword, streamChat, type StreamEvent } from "./lib/api";
import { uid, useChats, type Chat, type Message } from "./lib/store";
import { Login } from "./components/Login";
import { Sidebar } from "./components/Sidebar";
import { ChatView } from "./components/ChatView";
import { Composer } from "./components/Composer";
import { SessionChip, useWorker } from "./components/SessionChip";
import { FilesPage } from "./components/FilesPage";
import { Logo } from "./components/Logo";
import { IconPlus, IconSidebar } from "./components/Icons";

export default function App() {
  const [signedIn, setSignedIn] = useState(!!getPassword());
  useEffect(() => {
    const dark = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => { document.documentElement.dataset.theme = dark.matches ? "dark" : "light"; };
    apply();
    dark.addEventListener("change", apply);
    return () => dark.removeEventListener("change", apply);
  }, []);
  if (!signedIn) return <Login onDone={() => setSignedIn(true)} />;
  return <Shell onLogout={() => { setPassword(""); setSignedIn(false); }} />;
}

function Shell({ onLogout }: { onLogout: () => void }) {
  const { chats, add, remove, patchChat, patchMessage } = useChats();
  const worker = useWorker(onLogout);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [view, setView] = useState<"chat" | "files">("chat");
  const [sideOpen, setSideOpen] = useState(() => window.innerWidth > 900);
  const [draft, setDraft] = useState({ model: "default", effort: "medium" });
  const aborts = useRef(new Map<string, AbortController>());
  const active = chats.find((c) => c.id === activeId) || null;

  /** Run one turn: make sure the worker is up, stream Claude Code's reply into the assistant message. */
  const runTurn = useCallback(async (chat: Chat, text: string, history: Message[]) => {
    const reply: Message = { id: uid(), role: "assistant", content: "", status: "waiting", tools: [] };
    patchChat(chat.id, (c) => ({ ...c, messages: [...c.messages, reply] }));
    const upd = (fn: (m: Message) => Message) => patchMessage(chat.id, reply.id, fn);
    const ctl = new AbortController();
    aborts.current.set(chat.id, ctl);
    try {
      const url = await ensureWorker((s) => worker.setState(s), ctl.signal);
      worker.setState("on");
      upd((m) => ({ ...m, status: "streaming" }));
      let failed = false;
      const onEvent = (e: StreamEvent) => {
        if (e.t === "session") patchChat(chat.id, (c) => ({ ...c, session_id: e.id }));
        else if (e.t === "text") upd((m) => ({ ...m, content: m.content + e.d }));
        else if (e.t === "thinking") upd((m) => ({ ...m, thinking: (m.thinking || "") + e.d }));
        else if (e.t === "tool") upd((m) => {
          const tools = m.tools || [];
          return tools.some((t) => t.id === e.id)
            ? { ...m, tools: tools.map((t) => (t.id === e.id ? { ...t, label: e.label } : t)) }
            : { ...m, tools: [...tools, { id: e.id, label: e.label, done: false }] };
        });
        else if (e.t === "tool_done") upd((m) => ({ ...m, tools: (m.tools || []).map((t) => (t.id === e.id ? { ...t, done: true, error: e.error } : t)) }));
        else if (e.t === "done") {
          if (e.session_id) patchChat(chat.id, (c) => ({ ...c, session_id: e.session_id }));
          if (e.error) failed = true;
          upd((m) => ({ ...m, content: e.text || m.content, secs: e.secs, status: e.error ? "error" : "done", error: e.error ? e.text : undefined }));
        } else if (e.t === "error") { failed = true; upd((m) => ({ ...m, status: "error", error: e.message })); }
      };
      await streamChat(url, {
        chat_id: chat.id, message: text, model: chat.model, effort: chat.effort, session_id: chat.session_id,
        history: history.filter((m) => m.content).map((m) => ({ role: m.role, content: m.content })),
      }, onEvent, ctl.signal);
      if (!failed) upd((m) => (m.status === "streaming" ? { ...m, status: "done" } : m));
    } catch (e) {
      if (e instanceof AuthError) return onLogout();
      const stopped = ctl.signal.aborted;
      upd((m) => ({ ...m, status: stopped ? "done" : "error", error: stopped ? undefined : (e as Error).message }));
    } finally {
      aborts.current.delete(chat.id);
    }
  }, [patchChat, patchMessage, worker, onLogout]);

  const send = useCallback((chat: Chat, text: string, attached: string[]) => {
    const body = attached.length ? `${text}\n\n[Attached files in the working directory: ${attached.join(", ")}]`.trim() : text;
    const user: Message = { id: uid(), role: "user", content: body };
    const history = chat.messages;
    patchChat(chat.id, (c) => ({ ...c, messages: [...c.messages, user] }));
    runTurn(chat, body, history);
  }, [patchChat, runTurn]);

  const startChat = (text: string, attached: string[]) => {
    const title = text.replace(/\s+/g, " ").slice(0, 48) + (text.length > 48 ? "…" : "");
    const chat: Chat = { id: uid(), title: title || "New chat", model: draft.model, effort: draft.effort, pinned: false,
      created: Date.now(), updated: Date.now(), messages: [] };
    add(chat);
    setActiveId(chat.id);
    setView("chat");
    send(chat, text, attached);
  };

  const stop = (chat: Chat) => {
    aborts.current.get(chat.id)?.abort();
    cancelChat(chat.id);
  };

  const retry = (chat: Chat) => {
    const i = chat.messages.length - 1;
    const question = chat.messages[i - 1];
    if (i < 1 || question?.role !== "user") return;
    const rest = chat.messages.slice(0, i - 1);
    patchChat(chat.id, (c) => ({ ...c, messages: [...rest, question] }));
    runTurn(chat, question.content, rest);
  };

  const setModel = (model: string, effort: string) => {
    if (!active) return setDraft({ model, effort });
    patchChat(active.id, (c) => ({ ...c, model, effort }));
  };

  const closeSideOnMobile = () => { if (window.innerWidth <= 900) setSideOpen(false); };
  const online = worker.state === "on";

  return (
    <div className={`app ${sideOpen ? "side-open" : ""}`}>
      {sideOpen && <div className="side-scrim" onClick={() => setSideOpen(false)} />}
      <Sidebar chats={chats} activeId={view === "chat" ? activeId : null} view={view} onGo={(v) => { setView(v); closeSideOnMobile(); }}
        onNew={() => { setActiveId(null); setView("chat"); closeSideOnMobile(); }}
        onOpen={(id) => { setActiveId(id); setView("chat"); closeSideOnMobile(); }}
        onRename={(id, title) => patchChat(id, (c) => ({ ...c, title }))}
        onPin={(id) => patchChat(id, (c) => ({ ...c, pinned: !c.pinned }))}
        onDelete={(id) => { remove(id); if (id === activeId) setActiveId(null); }}
        onLogout={() => { if (confirm("Log out of Panda on this device?")) onLogout(); }} />
      <main className="main">
        <header className="topbar">
          <button className="icon-btn" onClick={() => setSideOpen((v) => !v)} aria-label="Sidebar"><IconSidebar /></button>
          <div className="topbar-title">{view === "chat" ? active?.title || "" : ""}</div>
          <SessionChip worker={worker} />
          {(active || view !== "chat") && (
            <button className="icon-btn" onClick={() => { setActiveId(null); setView("chat"); }} aria-label="New chat"><IconPlus /></button>
          )}
        </header>
        {view === "files" ? <FilesPage online={online} /> : active ? (
          <ChatView key={active.id} chat={active} onModel={setModel} canAttach={online}
            onSend={(t, a) => send(active, t, a)} onRetry={() => retry(active)} onStop={() => stop(active)} />
        ) : (
          <div className="home">
            <h1 className="greeting serif"><Logo size={40} animated /> What are we working on?</h1>
            <div className="home-composer">
              <Composer model={draft.model} effort={draft.effort} onModel={(model, effort) => setDraft({ model, effort })}
                onSend={startChat} autoFocus canAttach={false} />
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
