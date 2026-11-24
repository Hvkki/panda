import { useEffect, useState, type ReactNode } from "react";
import { getDevEnv, sessionAction, setDevEnv } from "../lib/api";
import { IconCode, IconPower, IconTrash } from "./Icons";

const NAME_RE = /^[A-Z][A-Z0-9_]{0,63}$/;

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
          <div className="dev-note muted small">Claude can change Panda here: it works on the GitHub repo, pushes, and redeploys. Needs <code>GITHUB_TOKEN</code> (and <code>VERCEL_TOKEN</code> to publish) in Env variables.</div>
          {chat}
        </div>
      ) : <div className="page dev-env"><EnvTab online={online} /></div>}
    </div>
  );
}

function EnvTab({ online }: { online: boolean }) {
  const [names, setNames] = useState<string[] | null>(null);
  const [err, setErr] = useState("");
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (online) getDevEnv().then(setNames).catch((e) => setErr(e.message));
  }, [online]);

  const apply = async (set: Record<string, string | null>) => {
    setBusy(true);
    setErr("");
    try {
      const r = await setDevEnv(set);
      setNames(r.names);
      if (!r.saved) setErr("Changed for this run, but saving to Kaggle failed. It will be gone after a restart.");
      return true;
    } catch (e) {
      setErr((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (!online) return (
    <div className="empty-state">
      <p>Start the worker to see and change the developer variables.</p>
      <button className="btn primary" disabled={starting} onClick={async () => {
        setStarting(true);
        try { await sessionAction("start"); } catch (e) { setErr((e as Error).message); }
      }}><IconPower size={15} /> {starting ? "Starting…" : "Start"}</button>
      {err && <p className="login-msg">{err}</p>}
    </div>
  );

  const valid = NAME_RE.test(name);
  return (
    <>
      <p className="muted">Only the Developer chat sees these. They are saved in your private Kaggle dataset and kept between runs. Values are never shown again.</p>
      <div className="card-list">
        {names === null ? <p className="muted">Loading…</p> : names.length === 0 ? <p className="muted">No variables yet. Add <code>GITHUB_TOKEN</code> so Claude can push to your repo.</p> :
          names.map((n) => (
            <div key={n} className="row-card">
              <code>{n}</code>
              <button className="icon-btn" disabled={busy} aria-label={`Delete ${n}`}
                onClick={() => { if (confirm(`Delete ${n}?`)) apply({ [n]: null }); }}><IconTrash size={16} /></button>
            </div>
          ))}
      </div>
      <form className="env-form" onSubmit={async (e) => {
        e.preventDefault();
        if (!valid || !value) return;
        if (await apply({ [name]: value })) { setName(""); setValue(""); }
      }}>
        <input className="text-input" placeholder="NAME" value={name} autoCapitalize="characters" autoComplete="off"
          onChange={(e) => setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))} />
        <input className="text-input" placeholder="value" type="password" value={value} autoComplete="new-password"
          onChange={(e) => setValue(e.target.value)} />
        <button className="btn primary" disabled={busy || !valid || !value}>{busy ? "Saving…" : "Add"}</button>
      </form>
      {err && <p className="login-msg">{err}</p>}
    </>
  );
}
