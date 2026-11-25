// Start / Stop / Status of the Kaggle worker, and the relay: shortly before a run hits Kaggle's 12 h
// limit, the worker asks for a fresh run here and hands everything over to it (see worker.py, "relay").
// The two runs overlap, so they use two notebooks: KAGGLE_KERNEL and KAGGLE_KERNEL + "-2".
const { env, authed, send, kaggleRpc, workerUrl, ping, timeout } = require("./_lib");

const MAX_RUN_SECS = 12 * 3600;

const kernels = () => { const k = env("KAGGLE_KERNEL"); return k ? [k, `${k}-2`] : []; };

async function kernelRunning(kernel) {
  const [user, slug] = kernel.split("/");
  const r = await kaggleRpc("kernels.KernelsApiService", "GetKernelSessionStatus", { userName: user, kernelSlug: slug });
  const s = String(r.data.status ?? "").toUpperCase();
  return r.ok && /RUNNING|QUEUED|NEW_SCRIPT/.test(s);
}

/** The notebooks that are running (or starting) now. */
async function running() {
  const ks = kernels();
  const on = await Promise.all(ks.map(kernelRunning));
  return ks.filter((_, i) => on[i]);
}

/** Start worker.py on `kernel`. `prev` is the run to take over from (relay). */
async function startKernel(req, kernel, prev = "") {
  const host = req.headers["x-forwarded-host"] || req.headers.host;
  let code = await fetch(`https://${host}/worker.py`, { signal: timeout(15000) }).then((r) => (r.ok ? r.text() : ""));
  if (!code) return { ok: false, error: "worker.py is missing from the deployment" };
  code = code.replace(/^PANDA_APP = ""/m, `PANDA_APP = ${JSON.stringify(`https://${host}`)}`)
    .replace(/^PANDA_PREV = ""/m, `PANDA_PREV = ${JSON.stringify(prev)}`);
  const second = kernel !== kernels()[0];
  const out = await kaggleRpc("kernels.KernelsApiService", "SaveKernel", {
    slug: kernel, newTitle: second ? kernel.split("/")[1].replace(/-/g, " ") : "panda worker", text: code,
    language: "python", kernelType: "script", isPrivate: true, enableGpu: false, enableTpu: false, enableInternet: true,
    datasetDataSources: [env("KAGGLE_DATASET")], competitionDataSources: [], kernelDataSources: [],
    modelDataSources: [], categoryIds: [], sessionTimeoutSeconds: MAX_RUN_SECS,
  });
  return out.ok ? { ok: true } : { ok: false, error: `Kaggle refused the start: ${out.data.error || out.status}` };
}

module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "authorization, content-type");
  if (req.method === "OPTIONS") return send(res, 204, {});
  if (!authed(req)) return send(res, 401, { error: "wrong password" });
  const body = typeof req.body === "object" && req.body ? req.body : {};
  const action = body.action || "status";
  if (!env("KAGGLE_API_TOKEN") || !kernels().length || !env("KAGGLE_DATASET"))
    return send(res, 500, { error: "Vercel environment variables are not set" });

  let url = await workerUrl();
  let beat = await ping(url);
  // A run that handed over points to its successor until the published URL catches up.
  if (beat?.moved) { url = beat.moved; beat = await ping(url); }

  if (action === "status") {
    if (beat) return send(res, 200, { state: "on", url, uptime: beat.uptime });
    return send(res, 200, { state: (await running()).length ? "starting" : "off" });
  }

  if (action === "stop") {
    if (!beat) return send(res, 200, { state: (await running()).length ? "starting" : "off",
      note: "The worker is not reachable yet. Try Stop again once it is online." });
    await fetch(`${url}/stop`, { method: "POST", headers: { Authorization: `Bearer ${env("PANDA_PASSWORD")}` },
      signal: timeout(10000) }).catch(() => {});
    return send(res, 200, { state: "off" });
  }

  if (action === "relay") {
    // Called by the worker itself. Start the other notebook and point it at the caller.
    const from = String(body.from || "");
    if (!/^https:\/\/[A-Za-z0-9.-]+$/.test(from)) return send(res, 400, { error: "bad from" });
    const busy = await running();
    const free = kernels().find((k) => !busy.includes(k));
    if (busy.length > 1 || !free) return send(res, 200, { state: "starting", note: "the next run is already starting" });
    const out = await startKernel(req, free, from);
    return out.ok ? send(res, 200, { state: "starting", kernel: free }) : send(res, 502, { error: out.error });
  }

  if (action !== "start") return send(res, 400, { error: "unknown action" });
  if (beat) return send(res, 200, { state: "on", url, uptime: beat.uptime, note: "already running" });
  if ((await running()).length) return send(res, 200, { state: "starting", note: "already starting" });
  const out = await startKernel(req, kernels()[0]);
  return out.ok ? send(res, 200, { state: "starting" }) : send(res, 502, { error: out.error });
};
