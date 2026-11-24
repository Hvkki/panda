// Start / Stop / Status of the Kaggle worker. Never starts a run on its own.
const { env, authed, send, kaggleRpc, workerUrl, ping, timeout } = require("./_lib");

const MAX_RUN_SECS = 12 * 3600;

async function kernelRunning() {
  const [user, slug] = env("KAGGLE_KERNEL").split("/");
  const r = await kaggleRpc("kernels.KernelsApiService", "GetKernelSessionStatus", { userName: user, kernelSlug: slug });
  const s = String(r.data.status ?? "").toUpperCase();
  return r.ok && /RUNNING|QUEUED|NEW_SCRIPT/.test(s) ? s : "";
}

module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "authorization, content-type");
  if (req.method === "OPTIONS") return send(res, 204, {});
  if (!authed(req)) return send(res, 401, { error: "wrong password" });
  const body = typeof req.body === "object" && req.body ? req.body : {};
  const action = body.action || "status";

  const url = await workerUrl();
  const beat = await ping(url);
  if (action === "status") {
    if (beat) return send(res, 200, { state: "on", url, uptime: beat.uptime });
    const k = await kernelRunning();
    return send(res, 200, { state: k ? "starting" : "off" });
  }

  if (action === "stop") {
    if (!beat) return send(res, 200, { state: (await kernelRunning()) ? "starting" : "off",
      note: "The worker is not reachable yet. Try Stop again once it is online." });
    await fetch(`${url}/stop`, { method: "POST", headers: { Authorization: `Bearer ${env("PANDA_PASSWORD")}` },
      signal: timeout(10000) }).catch(() => {});
    return send(res, 200, { state: "off" });
  }

  if (action !== "start") return send(res, 400, { error: "unknown action" });
  if (beat) return send(res, 200, { state: "on", url, uptime: beat.uptime, note: "already running" });
  if (await kernelRunning()) return send(res, 200, { state: "starting", note: "already starting" });

  const kernel = env("KAGGLE_KERNEL");
  const dataset = env("KAGGLE_DATASET");
  if (!env("KAGGLE_API_TOKEN") || !kernel || !dataset) return send(res, 500, { error: "Vercel environment variables are not set" });
  const host = req.headers["x-forwarded-host"] || req.headers.host;
  const code = await fetch(`https://${host}/worker.py`, { signal: timeout(15000) }).then((r) => (r.ok ? r.text() : ""));
  if (!code) return send(res, 500, { error: "worker.py is missing from the deployment" });
  const sources = [dataset];
  const out = await kaggleRpc("kernels.KernelsApiService", "SaveKernel", {
    slug: kernel, newTitle: "panda worker", text: code, language: "python", kernelType: "script",
    isPrivate: true, enableGpu: false, enableTpu: false, enableInternet: true,
    datasetDataSources: sources, competitionDataSources: [], kernelDataSources: [],
    modelDataSources: [], categoryIds: [], sessionTimeoutSeconds: MAX_RUN_SECS,
  });
  if (!out.ok) return send(res, 502, { error: `Kaggle refused the start: ${out.data.error || out.status}` });
  return send(res, 200, { state: "starting" });
};
