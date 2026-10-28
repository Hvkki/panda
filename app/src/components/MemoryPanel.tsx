import { useCallback, useEffect, useState } from "react";
import { gw } from "../lib/commands";
import { IconAlert } from "./Icons";

/* Settings → Memory: what Panda (Kiro Crew) remembers, read-only.
   Four parts, all from Crew's own memory store through the worker's read relay:
   - About you: the preferences + projects documents Crew injects into every chat
   - Facts: semantic key/value memories
   - Memories: episodic summaries of past chats (newest first, paged)
   - Lessons: corrections and rules Panda learned */

type AnyRec = Record<string, unknown>;
type Part = "about" | "facts" | "memories" | "lessons";
const PARTS: [Part, string][] = [["about", "About you"], ["facts", "Facts"], ["memories", "Memories"], ["lessons", "Lessons"]];
const PAGE = 50;

const str = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v));
const when = (v: unknown) => {
  const d = new Date(str(v));
  return isNaN(d.getTime()) ? "" : d.toLocaleString();
};
// Crew stores a fact's value as JSON text; show strings plainly, anything else as compact JSON.
const factValue = (e: AnyRec) => {
  let v: unknown = e.value;
  if (v === undefined && typeof e.value_json === "string") {
    try { v = JSON.parse(e.value_json); } catch { v = e.value_json; }
  }
  return typeof v === "string" ? v : JSON.stringify(v);
};
const tagsOf = (e: AnyRec): string[] => {
  const t = e.tags;
  if (Array.isArray(t)) return t.map(str);
  if (typeof t === "string") { try { const p = JSON.parse(t); return Array.isArray(p) ? p.map(str) : []; } catch { return []; } }
  return [];
};

export function MemoryPanel({ online }: { online: boolean }) {
  const [part, setPart] = useState<Part>("about");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => { const t = setTimeout(() => setQuery(q.trim()), 350); return () => clearTimeout(t); }, [q]);

  if (!online) return <p className="muted small">Start Panda to see what it remembers. Memories live on Panda's machine.</p>;
  return (
    <div className="memory-panel">
      <div className="tabs">{PARTS.map(([id, label]) => (
        <button key={id} className={part === id ? "active" : ""} onClick={() => setPart(id)}>{label}</button>
      ))}</div>
      {(part === "facts" || part === "memories") && (
        <input className="text-input" type="search" placeholder="Search memories" aria-label="Search memories"
          value={q} onChange={(e) => setQ(e.target.value)} style={{ width: "100%", margin: "8px 0" }} />
      )}
      {part === "about" && <About />}
      {part === "facts" && <Facts q={query} />}
      {part === "memories" && <Memories q={query} />}
      {part === "lessons" && <Lessons />}
    </div>
  );
}

function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const reload = useCallback(() => {
    setLoading(true); setErr("");
    fn().then(setData).catch((e) => setErr((e as Error).message)).finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(reload, [reload]);
  return { data, err, loading, reload };
}

const Loading = () => <div className="muted pad">Asking Panda…</div>;
const Err = ({ msg, retry }: { msg: string; retry: () => void }) => (
  <div className="error-note pad"><IconAlert size={15} /> {msg} <button className="link" onClick={retry}>Retry</button></div>
);
const Empty = ({ text }: { text: string }) => <p className="muted small pad">{text}</p>;

function About() {
  const { data, err, loading, reload } = useLoad(async () => {
    const [p, j] = await Promise.all([
      gw<AnyRec>("GET", "/api/memory/preferences").catch(() => ({} as AnyRec)),
      gw<AnyRec>("GET", "/api/memory/projects").catch(() => ({} as AnyRec)),
    ]);
    return { prefs: str(p.content).trim(), projects: str(j.content).trim() };
  }, []);
  if (loading) return <Loading />;
  if (err) return <Err msg={err} retry={reload} />;
  if (!data?.prefs && !data?.projects) return <Empty text="Panda hasn't written anything about you yet." />;
  return (
    <>
      {data.prefs && <><h4>Your preferences</h4><pre className="memory-doc">{data.prefs}</pre></>}
      {data.projects && <><h4>Your projects</h4><pre className="memory-doc">{data.projects}</pre></>}
    </>
  );
}

function Facts({ q }: { q: string }) {
  const { data, err, loading, reload } = useLoad(
    () => gw<AnyRec>("GET", `/api/memory/semantic?limit=500${q ? `&q=${encodeURIComponent(q)}` : ""}`), [q]);
  if (loading) return <Loading />;
  if (err) return <Err msg={err} retry={reload} />;
  const rows = ((data?.entries as AnyRec[]) || [])
    .slice().sort((a, b) => str(b.updated_at).localeCompare(str(a.updated_at)));
  if (!rows.length) return <Empty text={q ? "No facts match." : "No saved facts yet."} />;
  return (
    <div className="card-list">
      {rows.map((e) => (
        <div key={str(e.key)} className="row-card">
          <div className="grow">
            <b className="memory-key">{str(e.key)}</b>
            <div className="memory-text">{factValue(e)}</div>
            <div className="muted small">{when(e.updated_at)}{e.source ? ` · ${str(e.source)}` : ""}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function Memories({ q }: { q: string }) {
  const [rows, setRows] = useState<AnyRec[]>([]);
  const [more, setMore] = useState(false);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const load = useCallback(async (offset: number) => {
    setLoading(true); setErr("");
    try {
      const d = await gw<AnyRec>("GET", `/api/memory/episodic?limit=${PAGE}&offset=${offset}${q ? `&q=${encodeURIComponent(q)}` : ""}`);
      const got = (d.entries as AnyRec[]) || [];
      setRows((r) => (offset ? [...r, ...got] : got));
      setMore(got.length === PAGE);
    } catch (e) { setErr((e as Error).message); }
    setLoading(false);
  }, [q]);
  useEffect(() => { load(0); }, [load]);
  if (err && !rows.length) return <Err msg={err} retry={() => load(0)} />;
  if (loading && !rows.length) return <Loading />;
  if (!rows.length) return <Empty text={q ? "No memories match." : "No memories yet. Panda writes them after chats."} />;
  const sorted = rows.slice().sort((a, b) => str(b.created_at).localeCompare(str(a.created_at)));
  return (
    <>
      <div className="card-list">
        {sorted.map((e) => (
          <div key={str(e.id)} className="row-card">
            <div className="grow">
              <div className="memory-text">{str(e.text)}</div>
              <div className="muted small">{when(e.created_at)}
                {tagsOf(e).slice(0, 4).map((t) => <span key={t} className="tag" style={{ marginLeft: 6 }}>{t}</span>)}</div>
            </div>
          </div>
        ))}
      </div>
      {more && <button className="btn" disabled={loading} onClick={() => load(rows.length)} style={{ marginTop: 8 }}>
        {loading ? "Loading…" : "Show more"}</button>}
    </>
  );
}

function Lessons() {
  const { data, err, loading, reload } = useLoad(() => gw<AnyRec>("GET", "/api/lessons?limit=200"), []);
  if (loading) return <Loading />;
  if (err) return <Err msg={err} retry={reload} />;
  const rows = ((data?.lessons as AnyRec[]) || []).slice().reverse(); // newest first
  if (!rows.length) return <Empty text="No lessons yet." />;
  const total = Number(data?.total) || rows.length;
  return (
    <>
      <p className="muted small">Rules Panda learned from your corrections. {total > rows.length ? `Showing the newest ${rows.length} of ${total}.` : `${total} in total.`}</p>
      <div className="card-list">
        {rows.map((e, i) => (
          <div key={i} className="row-card">
            <div className="grow">
              <div className="memory-text">{str(e.rule)}</div>
              {e.negative ? <div className="muted small">Not: {str(e.negative)}</div> : null}
              <div className="muted small">{when(e.ts)}{e.category ? ` · ${str(e.category)}` : ""}</div>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
