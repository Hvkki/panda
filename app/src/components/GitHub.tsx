import { useEffect, useMemo, useState } from "react";
import { getGitHub, getRepos, recheckGitHub, setGitHub, type GitHubCheck, type GitHubRepo } from "../lib/api";
import { IconAlert, IconCheck, IconLock, IconRetry, IconSearch, IconX } from "./Icons";

// A classic token with these scopes reaches every repo you can, private and organization ones included.
const NEW_TOKEN = "https://github.com/settings/tokens/new?scopes=repo,workflow,read:org&description=Panda%20on%20Kaggle";

/** Settings → GitHub: connect one token, see what it can reach and what to fix. */
export function GitHubSettings({ online }: { online: boolean }) {
  const [check, setCheck] = useState<GitHubCheck | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { if (online) getGitHub().then(setCheck).catch((e) => setErr(e.message)); }, [online]);
  if (!online) return <p className="muted">Start the worker to connect GitHub.</p>;

  const run = async (fn: () => Promise<GitHubCheck>) => {
    setBusy(true); setErr("");
    try { setCheck(await fn()); setToken(""); } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };

  return (
    <div className="gh">
      {check?.connected ? (
        <div className="gh-card">
          {check.avatar && <img src={check.avatar} alt="" className="gh-avatar" />}
          <div className="gh-who">
            <div><b>{check.login}</b> <span className="tag">{check.kind === "fine-grained" ? "Fine-grained token" : check.kind === "classic" ? "Classic token" : "Token"}</span></div>
            <div className="muted small">
              {check.repos} repos · {check.private} private · can push to {check.push}
              {check.scopes?.length ? <> · scopes: {check.scopes.join(", ")}</> : null}
            </div>
          </div>
          <button className="icon-btn" title="Check again" aria-label="Check again" disabled={busy} onClick={() => run(recheckGitHub)}>
            <span className={busy ? "spin" : ""} style={{ display: "inline-grid" }}><IconRetry size={18} /></span>
          </button>
        </div>
      ) : (
        <p className="muted">Connect GitHub once and every chat can clone, commit, push and open pull requests, private repos included. Claude never asks for a key again.</p>
      )}
      {check?.hints?.map((h, i) => <div key={i} className="gh-hint"><IconAlert size={15} /><span>{h}</span></div>)}
      {check?.connected && !check.hints?.length && <div className="gh-ok"><IconCheck size={15} /> git and gh on the worker are signed in.</div>}

      <form className="gh-form" onSubmit={(e) => { e.preventDefault(); if (token.trim()) run(() => setGitHub(token.trim())); }}>
        <input className="text-input" type="password" autoComplete="off" placeholder={check?.connected ? "Replace with a new token" : "ghp_… or github_pat_…"}
          value={token} onChange={(e) => setToken(e.target.value)} />
        <button className="btn primary" disabled={busy || !token.trim()}>{busy ? "Checking…" : check?.connected ? "Replace" : "Connect"}</button>
      </form>
      <p className="muted small">
        Easiest: <a href={NEW_TOKEN} target="_blank" rel="noreferrer">create a classic token</a> with the scopes
        <code>repo</code>, <code>workflow</code> and <code>read:org</code> already ticked. Fine-grained tokens work too, but only see
        the repos you pick for them. The token is saved in your private Kaggle dataset.
      </p>
      {check?.connected && (
        <button className="btn danger-text" disabled={busy} onClick={() => { if (confirm("Disconnect GitHub from every chat?")) run(() => setGitHub("")); }}>
          Disconnect
        </button>
      )}
      {err && <p className="login-msg">{err}</p>}
    </div>
  );
}

/** Pick one of the repos the token can reach. */
export function RepoPicker({ value, onPick, online }: { value?: string; onPick: (repo: string | undefined) => void; online: boolean }) {
  const [repos, setRepos] = useState<GitHubRepo[] | null>(null);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const load = (fresh = false) => { setErr(""); getRepos(fresh).then(setRepos).catch((e) => setErr(e.message)); };
  useEffect(() => { if (open && online && repos === null) load(); }, [open, online]); // eslint-disable-line react-hooks/exhaustive-deps
  const shown = useMemo(() => (repos || []).filter((r) => r.full_name.toLowerCase().includes(q.toLowerCase())).slice(0, 60), [repos, q]);

  if (!open) return (
    <div className="repo-current">
      {value ? <><code>{value}</code><button className="icon-btn" aria-label="Remove repo" onClick={() => onPick(undefined)}><IconX size={15} /></button></>
        : <span className="muted small">No repo</span>}
      <button className="btn" disabled={!online} onClick={() => setOpen(true)} title={online ? "" : "Start the worker first"}>{value ? "Change" : "Choose repo"}</button>
    </div>
  );
  return (
    <div className="repo-picker">
      <div className="repo-search">
        <IconSearch size={15} />
        <input autoFocus placeholder="Search your repos" value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }} />
        <button className="icon-btn" aria-label="Reload" onClick={() => load(true)}><IconRetry size={15} /></button>
        <button className="icon-btn" aria-label="Close" onClick={() => setOpen(false)}><IconX size={15} /></button>
      </div>
      {err ? <p className="login-msg small">{err}</p> : repos === null ? <p className="muted small pad">Loading…</p> : (
        <div className="repo-list">
          {shown.length === 0 && <p className="muted small pad">No repos match. Private ones missing? Check Settings → GitHub.</p>}
          {shown.map((r) => (
            <button key={r.full_name} className={`repo-row ${r.full_name === value ? "active" : ""}`} onClick={() => { onPick(r.full_name); setOpen(false); }}>
              <span className="repo-name">{r.private && <IconLock size={13} />} {r.full_name}</span>
              <span className="muted small">{r.push ? "" : "read-only · "}{r.description}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
