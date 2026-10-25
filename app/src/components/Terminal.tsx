import { useCallback, useEffect, useRef, useState } from "react";
import { fsList, fsRead, termRun, type FsEntry, type FsFile, type FsList, type TermResult } from "../lib/commands";
import { prettySize } from "../lib/files";
import { IconArrowLeft, IconChevron, IconDoc, IconFile, IconFolder, IconLock, IconRetry, IconSpinner, IconTerminal, IconX } from "./Icons";

/**
 * A read-only look at the worker's files and a real shell you can type into — the terminal and
 * file view Kiro Crew's dashboard has. Both go through the command relay, so they only work while
 * Panda is online, and only for the owner. Output is scrubbed of known secrets by the worker.
 */
export function TerminalTab() {
  const [pane, setPane] = useState<"files" | "shell">("shell");
  return (
    <div className="term-tab">
      <p className="muted term-intro">A direct line to Panda's machine on Kaggle: browse its files and run commands.
        It's your own worker — the same shell Panda uses. Secrets are hidden from the output.</p>
      <div className="seg">
        <button className={pane === "files" ? "on" : ""} onClick={() => setPane("files")}><IconFolder size={15} /> Files</button>
        <button className={pane === "shell" ? "on" : ""} onClick={() => setPane("shell")}><IconTerminal size={15} /> Terminal</button>
      </div>
      <div className="term-grid">
        <div className={`term-pane ${pane === "files" ? "" : "hide-sm"}`}><FileBrowser /></div>
        <div className={`term-pane ${pane === "shell" ? "" : "hide-sm"}`}><Console /></div>
      </div>
    </div>
  );
}

/* ── file browser ──────────────────────────────────────────────────────── */
function FileBrowser() {
  const [path, setPath] = useState<string | undefined>(undefined);
  const [data, setData] = useState<FsList | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<FsFile | null>(null);

  const load = useCallback((p?: string) => {
    setLoading(true);
    setErr("");
    fsList(p).then((d) => { setData(d); setPath(d.path); }).catch((e) => setErr((e as Error).message)).finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(path); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const openEntry = (e: FsEntry) => {
    const next = (data?.path || "") + "/" + e.name;
    if (e.dir) { load(next); return; }
    setOpen({ path: next, rel: next, name: e.name });
    fsRead(next).then(setOpen).catch((er) => setOpen({ path: next, rel: next, name: e.name, denied: (er as Error).message }));
  };

  const crumbs = (data?.rel || "~").split("/");
  return (
    <div className="fb">
      <div className="fb-bar">
        <button className="icon-btn" disabled={!data?.parent} onClick={() => load(data!.parent!)} aria-label="Up"><IconArrowLeft size={17} /></button>
        <div className="fb-crumbs">{crumbs.map((c, i) => <span key={i}>{i > 0 && <IconChevron size={12} />}{c}</span>)}</div>
        <button className="icon-btn" onClick={() => load(path)} disabled={loading} aria-label="Reload">{loading ? <IconSpinner size={16} /> : <IconRetry size={16} />}</button>
      </div>
      {data?.quick && data.quick.length > 0 && (
        <div className="fb-quick">{data.quick.map((q) => <button key={q} className="chip" onClick={() => load(data.home + "/" + q)}>{q}</button>)}</div>
      )}
      {err ? <div className="error-note small">{err}</div> : loading && !data ? <div className="muted pad"><IconSpinner size={16} /> Reading…</div> : (
        <div className="fb-list">
          {data?.entries.length === 0 && <div className="muted small pad">Empty folder.</div>}
          {data?.entries.map((e) => (
            <button key={e.name} className="fb-row" onClick={() => openEntry(e)}>
              <span className="fb-icon">{e.dir ? <IconFolder size={17} /> : e.sensitive ? <IconLock size={15} /> : <IconDoc size={16} />}</span>
              <span className="fb-name">{e.name}{e.dir ? "/" : ""}</span>
              <span className="muted small fb-size">{e.dir ? "" : prettySize(e.size)}</span>
            </button>
          ))}
        </div>
      )}
      {open && <FilePreview file={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function FilePreview({ file, onClose }: { file: FsFile; onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  const loading = file.text === undefined && !file.binary && !file.too_big && !file.denied;
  return (
    <div className="preview-backdrop" onClick={onClose}>
      <div className="preview file-view" onClick={(e) => e.stopPropagation()}>
        <div className="preview-head">
          <IconFile size={16} />
          <b className="preview-title mono">{file.name}</b>
          {file.size != null && <span className="muted small">{prettySize(file.size)}</span>}
          <button className="icon-btn" onClick={onClose} aria-label="Close"><IconX /></button>
        </div>
        <div className="preview-body file-body">
          {loading ? <div className="muted pad"><IconSpinner size={16} /> Reading…</div>
            : file.denied ? <div className="muted pad"><IconLock size={15} /> {file.denied}</div>
            : file.too_big ? <div className="muted pad">This file is too large to open here ({prettySize(file.size || 0)}).</div>
            : file.binary ? <div className="muted pad">Binary file — can't show it as text.</div>
            : <pre className="file-text"><code>{file.text}</code></pre>}
        </div>
      </div>
    </div>
  );
}

/* ── console ───────────────────────────────────────────────────────────── */
type Line = { cmd: string; cwd: string; out?: string; rc?: number | null; busy?: boolean; note?: string };
const shortCwd = (cwd: string, home?: string) => (home && cwd.startsWith(home) ? "~" + cwd.slice(home.length) : cwd) || "~";

function Console() {
  const [lines, setLines] = useState<Line[]>([]);
  const [input, setInput] = useState("");
  const [cwd, setCwd] = useState("~");
  const [home, setHome] = useState<string | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const hist = useRef<string[]>([]);
  const histAt = useRef(-1);
  const body = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (body.current) body.current.scrollTop = body.current.scrollHeight; }, [lines]);

  const run = async () => {
    const cmd = input.trim();
    if (!cmd || busy) return;
    if (cmd === "clear") { setLines([]); setInput(""); return; }
    hist.current = [cmd, ...hist.current.filter((c) => c !== cmd)].slice(0, 100);
    histAt.current = -1;
    setInput("");
    setBusy(true);
    const here = shortCwd(cwd, home);
    setLines((l) => [...l, { cmd, cwd: here, busy: true }]);
    let r: TermResult;
    try {
      r = await termRun(cmd);
    } catch (e) {
      r = { output: (e as Error).message, rc: null, cwd };
    }
    if (r.home) setHome(r.home);
    setCwd(r.cwd || cwd);
    setLines((l) => l.map((x, i) => i === l.length - 1 ? { ...x, busy: false, out: r.output, rc: r.rc,
      note: r.timeout ? "stopped — too long" : r.reset ? "terminal reset" : undefined } : x));
    setBusy(false);
    inputRef.current?.focus();
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") { e.preventDefault(); run(); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); histAt.current = Math.min(histAt.current + 1, hist.current.length - 1); if (hist.current[histAt.current] != null) setInput(hist.current[histAt.current]); }
    if (e.key === "ArrowDown") { e.preventDefault(); histAt.current = Math.max(histAt.current - 1, -1); setInput(histAt.current < 0 ? "" : hist.current[histAt.current] || ""); }
  };

  return (
    <div className="cons" onClick={() => inputRef.current?.focus()}>
      <div className="cons-body" ref={body}>
        <div className="cons-hello muted small">Panda's shell. Try <code>ls ~/outputs</code>, <code>df -h</code>, <code>cat README.md</code>. Type <code>clear</code> to clear.</div>
        {lines.map((l, i) => (
          <div key={i} className="cons-entry">
            <div className="cons-cmd"><span className="cons-prompt">{l.cwd} <b>$</b></span> <span>{l.cmd}</span></div>
            {l.busy ? <div className="cons-out muted"><IconSpinner size={13} /> running…</div>
              : <>{l.out ? <pre className="cons-out">{l.out}</pre> : null}
                {l.note && <div className={`cons-note ${l.note.startsWith("stopped") ? "warn-text" : "muted"}`}>{l.note}</div>}</>}
          </div>
        ))}
      </div>
      <div className="cons-input">
        <span className="cons-prompt">{cwd} <b>$</b></span>
        <input ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={onKey} autoFocus
          placeholder={busy ? "running…" : "type a command"} disabled={busy} spellCheck={false} autoCapitalize="off" autoCorrect="off"
          autoComplete="off" enterKeyHint="send" />
        {busy ? <IconSpinner size={16} /> : null}
      </div>
    </div>
  );
}
