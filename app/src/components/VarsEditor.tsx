import { useEffect, useState } from "react";
import { IconTrash } from "./Icons";

const NAME_RE = /^[A-Z][A-Z0-9_]{0,63}$/;

/** Names-only list of secret variables, with add and delete. Values are never shown again. */
export function VarsEditor({ load, save, empty }: {
  load: () => Promise<string[]>;
  save: (set: Record<string, string | null>) => Promise<{ names: string[] }>;
  empty: React.ReactNode;
}) {
  const [names, setNames] = useState<string[] | null>(null);
  const [err, setErr] = useState("");
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { load().then(setNames).catch((e) => setErr(e.message)); }, [load]);

  const apply = async (set: Record<string, string | null>) => {
    setBusy(true);
    setErr("");
    try {
      setNames((await save(set)).names);
      return true;
    } catch (e) {
      setErr((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const valid = NAME_RE.test(name);
  return (
    <>
      <div className="card-list">
        {names === null ? <p className="muted">Loading…</p> : names.length === 0 ? <p className="muted">{empty}</p> :
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
