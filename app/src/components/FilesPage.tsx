import { useEffect, useState } from "react";
import { downloadFile, listFiles, type WorkFile } from "../lib/api";

const size = (n: number) => (n > 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

/** Files Claude Code made in the worker's workspace this run. They are lost when the worker stops. */
export function FilesPage({ online }: { online: boolean }) {
  const [files, setFiles] = useState<WorkFile[] | null>(null);
  const [err, setErr] = useState("");
  const load = () => listFiles().then(setFiles).catch((e) => setErr(e.message));
  useEffect(() => { if (online) load(); }, [online]);
  return (
    <div className="page">
      <div className="page-head">
        <h1 className="serif">Files</h1>
        {online && <button className="btn" onClick={load}>Refresh</button>}
      </div>
      <p className="muted">Files live on the Kaggle worker and disappear when it stops. Download what you want to keep.</p>
      {!online ? <p className="muted">Start the worker to see its files.</p> : err ? <p className="login-msg">{err}</p> :
        files === null ? <p className="muted">Loading…</p> : files.length === 0 ? <p className="muted">No files yet.</p> : (
          <div className="card-list">
            {files.map((f) => (
              <div key={f.path} className="row-card">
                <div><div className="file-name">{f.path}</div>
                  <div className="muted">{size(f.size)} · {new Date(f.mtime * 1000).toLocaleString()}</div></div>
                <button className="btn" onClick={() => downloadFile(f.path).catch((e) => setErr(e.message))}>Download</button>
              </div>
            ))}
          </div>
        )}
    </div>
  );
}
