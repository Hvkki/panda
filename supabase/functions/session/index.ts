// Embra "session" function: the Start / Stop / Status buttons.
// Start pushes the worker code (from private Storage) to Kaggle, which runs it once.
// It never starts a run by itself and refuses while one is already alive.
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
const MAX_RUN_SECS = 12 * 3600;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

  const jwt = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  const { data: who } = await admin.auth.getUser(jwt);
  const user = who?.user;
  if (!user) return json({ error: "not signed in" }, 401);
  const { data: isWorker } = await admin.from("relay_workers").select("user_id").eq("user_id", user.id);
  if (isWorker?.length) return json({ error: "worker accounts cannot use this" }, 403);

  const { action = "status" } = await req.json().catch(() => ({}));
  const { data: rows } = await admin.from("worker_sessions").select("*").eq("owner", user.id)
    .order("started_at", { ascending: false }).limit(1);
  const last = rows?.[0];
  const beat = last?.heartbeat_at ? (Date.now() - Date.parse(last.heartbeat_at)) / 1000 : Infinity;
  const age = last ? (Date.now() - Date.parse(last.started_at)) / 1000 : Infinity;
  const alive = !!last && (
    (["running", "stopping"].includes(last.status) && beat < ALIVE_SECS) ||
    (["requested", "starting"].includes(last.status) && age < 15 * 60)); // still booting

  if (action === "status") return json({ session: last ?? null, alive });

  if (action === "stop") {
    if (!last || !alive) return json({ session: last ?? null, alive: false });
    const { data } = await admin.from("worker_sessions").update({ stop_requested: true })
      .eq("id", last.id).select().single();
    return json({ session: data, alive: true });
  }

  if (action !== "start") return json({ error: "unknown action" }, 400);
  if (alive) return json({ session: last, alive: true, note: "already running" });

  const token = Deno.env.get("KAGGLE_API_TOKEN");
  const fallback = Deno.env.get("KAGGLE_KERNEL");
  const dataset = Deno.env.get("KAGGLE_DATASET");
  if (!token || !fallback || !dataset) return json({ error: "function secrets are not set" }, 500);

  // This account's own notebook (its own CPU), else the default one.
  let kernel = fallback;
  const map = await admin.storage.from("kirocrew-state").download("config/kernels.json");
  if (!map.error) {
    try {
      const k = (JSON.parse(await map.data.text()) as Record<string, string>)[user.id];
      if (typeof k === "string" && /^[\w-]+\/[\w-]+$/.test(k)) kernel = k;
    } catch { /* a broken map falls back to the default notebook */ }
  }
  const code = await admin.storage.from("kirocrew-state").download("code/worker.py");
  if (code.error) return json({ error: "worker code missing in storage" }, 500);

  const { data: sess, error: insErr } = await admin.from("worker_sessions")
    .insert({ owner: user.id, status: "requested", info: { by: "app" }, kaggle_kernel: `${kernel} v?` }).select().single();
  if (insErr) return json({ error: insErr.message }, 500);
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
    await admin.from("worker_sessions").update({ status: "error", info: { error: out.error || push.status } })
      .eq("id", sess.id);
    return json({ error: `Kaggle refused the start: ${out.error || push.status}` }, 502);
  }
  await admin.from("worker_sessions").update({ kaggle_kernel: `${kernel} v${out.versionNumber}` })
    .eq("id", sess.id);
  return json({ session: { ...sess, kaggle_kernel: `${kernel} v${out.versionNumber}` }, alive: true });
});
