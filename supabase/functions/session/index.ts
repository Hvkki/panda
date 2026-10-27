// Embra "session" function: the Start / Stop / Status buttons, and the worker's hand-off.
// Start pushes the worker code (from private Storage) to Kaggle, which runs it once.
// A person's Start refuses while a run is already alive. The only other way a run starts is "handoff":
// a RUNNING worker (signed in as the worker account) asks for the next run of its own account shortly
// before its 11 h guard. The worker has no Kaggle credential; the token stays in this function's secrets.
//
// Secrets to set in Supabase (Edge Functions -> Secrets):
//   KAGGLE_API_TOKEN   your Kaggle API token (KGAT_...)
//   KAGGLE_KERNEL      e.g. vladraf/kirocrew-worker   (the default notebook)
//   KAGGLE_DATASET     e.g. vladraf/kirocrew-test-secret
// One notebook per account: Storage kirocrew-state/config/kernels.json maps a user id to its own
// Kaggle notebook ({"<user id>": "vladraf/panda-worker-name"}); accounts not listed use KAGGLE_KERNEL.
// Each notebook is its own CPU machine, so accounts never share a worker.
// SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are provided automatically.
import { createClient } from "npm:@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });

const ALIVE_SECS = 90; // a heartbeat newer than this means the worker is alive
const BOOT_SECS = 15 * 60; // a run that was just requested has this long to boot
const MAX_RUN_SECS = 12 * 3600;
const MIN_HANDOFF_AGE_SECS = 30 * 60; // a run younger than this cannot hand off (stops restart loops)
const MAX_SUCCESSORS = 3; // per run: a successor that failed may be replaced, but only so often

// deno-lint-ignore no-explicit-any
type Row = Record<string, any>;
// deno-lint-ignore no-explicit-any
type Admin = any;

function isAlive(r: Row | undefined | null): boolean {
  if (!r) return false;
  const beat = r.heartbeat_at ? (Date.now() - Date.parse(r.heartbeat_at)) / 1000 : Infinity;
  const age = (Date.now() - Date.parse(r.started_at)) / 1000;
  return (["running", "stopping"].includes(r.status) && beat < ALIVE_SECS) ||
    // booting, or a successor that is installing and waiting (it heartbeats while it waits)
    (["requested", "starting"].includes(r.status) && (age < BOOT_SECS || beat < ALIVE_SECS));
}

/** Push the worker code to this account's notebook and record the run. Used by Start and by hand-off. */
async function launch(admin: Admin, userId: string, info: Row): Promise<
  { ok: true; session: Row } | { ok: false; status: number; error: string }
> {
  const token = Deno.env.get("KAGGLE_API_TOKEN");
  const fallback = Deno.env.get("KAGGLE_KERNEL");
  const dataset = Deno.env.get("KAGGLE_DATASET");
  if (!token || !fallback || !dataset) return { ok: false, status: 500, error: "function secrets are not set" };

  // This account's own notebook (its own CPU), else the default one.
  let kernel = fallback;
  const map = await admin.storage.from("kirocrew-state").download("config/kernels.json");
  if (!map.error) {
    try {
      const k = (JSON.parse(await map.data.text()) as Record<string, string>)[userId];
      if (typeof k === "string" && /^[\w-]+\/[\w-]+$/.test(k)) kernel = k;
    } catch { /* a broken map falls back to the default notebook */ }
  }
  const code = await admin.storage.from("kirocrew-state").download("code/worker.py");
  if (code.error) return { ok: false, status: 500, error: "worker code missing in storage" };

  const { data: sess, error: insErr } = await admin.from("worker_sessions")
    .insert({ owner: userId, status: "requested", info, kaggle_kernel: `${kernel} v?` }).select().single();
  if (insErr) return { ok: false, status: 500, error: insErr.message };
  // The run serves exactly this Start press (and so this account).
  const text = (await code.data.text()).replace(/^INJECTED_SESSION_ID = ""$/m, `INJECTED_SESSION_ID = "${sess.id}"`);
  const title = kernel.split("/")[1].replace(/-/g, " ");

  const push = await fetch("https://api.kaggle.com/v1/kernels.KernelsApiService/SaveKernel", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      slug: kernel, newTitle: title, text, language: "python",
      kernelType: "script", isPrivate: true, enableGpu: false, enableTpu: false, enableInternet: true,
      datasetDataSources: [dataset], competitionDataSources: [], kernelDataSources: [],
      modelDataSources: [], categoryIds: [], sessionTimeoutSeconds: MAX_RUN_SECS,
    }),
  });
  const out = await push.json().catch(() => ({}));
  if (!push.ok || out.error) {
    await admin.from("worker_sessions").update({ status: "error", info: { ...info, error: out.error || push.status } })
      .eq("id", sess.id);
    return { ok: false, status: 502, error: `Kaggle refused the start: ${out.error || push.status}` };
  }
  await admin.from("worker_sessions").update({ kaggle_kernel: `${kernel} v${out.versionNumber}` }).eq("id", sess.id);
  return { ok: true, session: { ...sess, kaggle_kernel: `${kernel} v${out.versionNumber}` } };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

  const jwt = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  const { data: who } = await admin.auth.getUser(jwt);
  const user = who?.user;
  if (!user) return json({ error: "not signed in" }, 401);
  const { data: isWorker } = await admin.from("relay_workers").select("user_id").eq("user_id", user.id);
  const body = await req.json().catch(() => ({}));
  const { action = "status" } = body;

  // The worker account may do exactly one thing here: hand its own run over to a successor.
  if (isWorker?.length) {
    if (action !== "handoff") return json({ error: "worker accounts cannot use this" }, 403);
    const sid = String(body.session_id || "");
    const { data: cur } = await admin.from("worker_sessions").select("*").eq("id", sid).maybeSingle();
    if (!cur) return json({ error: "no such run" }, 404);
    if (!isAlive(cur) || !["running", "stopping"].includes(cur.status) || cur.stop_requested) {
      return json({ error: "only a live run that was not asked to stop can hand off" }, 409);
    }
    if ((Date.now() - Date.parse(cur.started_at)) / 1000 < MIN_HANDOFF_AGE_SECS) {
      return json({ error: "this run is too young to hand off" }, 429);
    }
    // Idempotent: a successor that is still alive is returned, never a second one.
    const { data: succ } = await admin.from("worker_sessions").select("*").eq("owner", cur.owner)
      .eq("info->>handoff_from", sid).order("started_at", { ascending: false });
    const live = (succ ?? []).find(isAlive);
    if (live) return json({ session: live, alive: true, note: "already handed off" });
    if ((succ ?? []).length >= MAX_SUCCESSORS) return json({ error: "too many failed hand-offs" }, 429);
    // Someone (the person pressing Start) may already have started a newer run for this account.
    const { data: newer } = await admin.from("worker_sessions").select("*").eq("owner", cur.owner)
      .gt("started_at", cur.started_at).order("started_at", { ascending: false }).limit(3);
    const other = (newer ?? []).find(isAlive);
    if (other) return json({ session: other, alive: true, note: "a newer run is already alive" });
    const made = await launch(admin, cur.owner, { by: "handoff", handoff_from: sid });
    if (!made.ok) return json({ error: made.error }, made.status);
    return json({ session: made.session, alive: true });
  }

  if (action === "handoff") return json({ error: "only the worker can hand off" }, 403);

  // The newest rows: during a hand-off the old run and its successor are both alive. Report the one that
  // is serving (a running one first), else the newest.
  const { data: recent } = await admin.from("worker_sessions").select("*").eq("owner", user.id)
    .order("started_at", { ascending: false }).limit(3);
  const rows: Row[] = recent ?? [];
  const liveRows = rows.filter(isAlive);
  const last = liveRows.find((r) => r.status === "running") ?? liveRows[0] ?? rows[0];
  const alive = isAlive(last);

  if (action === "status") return json({ session: last ?? null, alive });

  if (action === "stop") {
    if (!last || !alive) return json({ session: last ?? null, alive: false });
    // Stop every live run: a successor that is still waiting must not take over after a Stop.
    await admin.from("worker_sessions").update({ stop_requested: true }).in("id", liveRows.map((r) => r.id));
    const { data } = await admin.from("worker_sessions").select("*").eq("id", last.id).single();
    return json({ session: data, alive: true });
  }

  if (action !== "start") return json({ error: "unknown action" }, 400);
  if (alive) return json({ session: last, alive: true, note: "already running" });

  const made = await launch(admin, user.id, { by: "app" });
  if (!made.ok) return json({ error: made.error }, made.status);
  return json({ session: made.session, alive: true });
});
