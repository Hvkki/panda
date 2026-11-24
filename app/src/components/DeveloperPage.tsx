import { useState, type ReactNode } from "react";
import { getDevEnv, setDevEnv } from "../lib/api";
import { IconCode } from "./Icons";
import { VarsEditor } from "./VarsEditor";
import { StartNote } from "./StartNote";

/**
 * Developer: one chat where Claude may change Panda itself (it clones the repo on the worker,
 * commits, pushes and deploys), plus variables only that chat can use (GITHUB_TOKEN, VERCEL_TOKEN…).
 */
export function DeveloperPage({ online, chat }: { online: boolean; chat: ReactNode }) {
  const [tab, setTab] = useState<"chat" | "env">("chat");
  return (
    <div className="dev-page">
      <div className="dev-head">
        <h1 className="serif"><IconCode size={22} /> Developer</h1>
        <div className="tabs">
          <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>Chat</button>
          <button className={tab === "env" ? "active" : ""} onClick={() => setTab("env")}>Env variables</button>
        </div>
      </div>
      {tab === "chat" ? (
        <div className="dev-chat">
          <div className="dev-note muted small">Claude can change Panda here and publish it with <code>VERCEL_TOKEN</code>. Changes are kept in your Kaggle dataset between runs. Add <code>GITHUB_TOKEN</code> too if you want them pushed to GitHub.</div>
          {chat}
        </div>
      ) : <div className="page dev-env"><EnvTab online={online} /></div>}
    </div>
  );
}

function EnvTab({ online }: { online: boolean }) {
  if (!online) return <StartNote what="see and change the developer variables" />;
  return (
    <>
      <p className="muted">Only the Developer chat sees these. They are saved in your private Kaggle dataset and kept between runs. Values are never shown again.</p>
      <VarsEditor load={getDevEnv} save={setDevEnv} empty={<>No variables yet. Add <code>VERCEL_TOKEN</code> so Claude can publish changes.</>} />
    </>
  );
}
