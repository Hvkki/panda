// Logic test of the session function's watchdog() with a fake Supabase client (no network, no Kaggle).
//   node tools/test_watchdog.mjs        (needs typescript: transpiles supabase/functions/session/index.ts)
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const here = path.dirname(new URL(import.meta.url).pathname);
const require = createRequire(process.env.TS_FROM || path.join(process.env.HOME, "work/tscheck/x.js"));
const ts = require("typescript");
let src = fs.readFileSync(path.join(here, "../supabase/functions/session/index.ts"), "utf8");
src = src.replace(/^import .*$/m, "const createClient = () => null;").replace("Deno.serve(", "globalThis.__serve = (");
src += "\nglobalThis.__watchdog = watchdog;\n";
const js = ts.transpileModule(src, { compilerOptions: { target: "es2022", module: "esnext" } }).outputText;

const pushed = [];
globalThis.Deno = { env: { get: (k) => ({ KAGGLE_API_TOKEN: "t", KAGGLE_KERNEL: "u/k", KAGGLE_DATASET: "u/d" })[k] } };
globalThis.fetch = async () => { pushed.push(1); return { ok: true, status: 200, json: async () => ({ versionNumber: 9 }) }; };
await import("data:text/javascript," + encodeURIComponent(js));

const iso = (agoSecs) => new Date(Date.now() - agoSecs * 1000).toISOString();
function fakeAdmin(rows) {
  const notes = [], updates = [];
  const q = (table) => {
    const f = { filters: [], table };
    const api = {
      select() { return api; }, eq(k, v) { f.filters.push((r) => r[k] === v); return api; },
      gte(k, v) { f.filters.push((r) => r[k] >= v); return api; }, order() { return api; }, limit() { return api; },
      insert(obj) {
        if (table === "notifications") { notes.push(obj); return Promise.resolve({}); }
        const row = { id: `new${rows.length}`, started_at: iso(0), ...obj };
        rows.unshift(row);
        return { select: () => ({ single: async () => ({ data: row }) }) };
      },
      update(obj) { return { eq: async (k, v) => { updates.push(obj); Object.assign(rows.find((r) => r[k] === v), obj); return {}; } }; },
      then(res) { res({ data: rows.filter((r) => f.filters.every((fn) => fn(r))).sort((a, b) => b.started_at.localeCompare(a.started_at)) }); },
    };
    return api;
  };
  return { from: q, storage: { from: () => ({ download: async (p) => p === "code/worker.py"
    ? { data: { text: async () => 'INJECTED_SESSION_ID = ""' } } : { error: "none" } }) }, notes, updates, rows };
}

const cases = [
  ["dead: heartbeat stale 10 min", [{ id: "a", owner: "o1", status: "running", started_at: iso(7200), heartbeat_at: iso(600), info: {} }], true],
  ["alive", [{ id: "a", owner: "o1", status: "running", started_at: iso(7200), heartbeat_at: iso(20), info: {} }], false],
  ["person pressed Stop", [{ id: "a", owner: "o1", status: "stopped", stop_requested: true, started_at: iso(7200), stopped_at: iso(900), info: { stop_reason: "stop requested" } }], false],
  ["crashed (error) 10 min ago", [{ id: "a", owner: "o1", status: "error", started_at: iso(7200), stopped_at: iso(600), info: { error: "gateway process exited" } }], true],
  ["crashed 1 min ago: grace for the worker's own hand-off", [{ id: "a", owner: "o1", status: "error", started_at: iso(7200), stopped_at: iso(60), info: {} }], false],
  ["handed off but successor never came", [{ id: "a", owner: "o1", status: "stopped", started_at: iso(40000), stopped_at: iso(900), info: { stop_reason: "handoff" } }], true],
  ["3 watchdog starts in 2 h already: give up", [
    { id: "c", owner: "o1", status: "error", started_at: iso(400), stopped_at: iso(300), info: { by: "watchdog", error: "x" } },
    { id: "b", owner: "o1", status: "error", started_at: iso(1400), stopped_at: iso(1300), info: { by: "watchdog" } },
    { id: "a2", owner: "o1", status: "error", started_at: iso(2400), stopped_at: iso(2300), info: { by: "watchdog" } }], false],
];
let fail = 0;
for (const [name, rows, expect] of cases) {
  pushed.length = 0;
  const admin = fakeAdmin(rows.map((r) => ({ ...r })));
  const out = await globalThis.__watchdog(admin);
  const started = pushed.length > 0;
  const ok = started === expect;
  if (!ok) fail++;
  console.log(`${ok ? "ok" : "FAIL"}: ${name} -> ${started ? "started" : "left alone"}` +
    (admin.notes.length ? ` (note: ${admin.notes[0].meta.title})` : "") + (ok ? "" : " " + JSON.stringify(out)));
}
process.exit(fail ? 1 : 0);
