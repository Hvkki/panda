// Shared helpers for the Panda server functions (Vercel, Node runtime). No database:
// the only state is Kaggle (the notebook, its private dataset, and an "endpoint" dataset).
//
// Vercel environment variables:
//   PANDA_PASSWORD     the password you type in the app (same value as app_password in worker.json)
//   KAGGLE_API_TOKEN   Kaggle API token (KGAT_...)
//   KAGGLE_KERNEL      e.g. yourname/panda-worker
//   KAGGLE_DATASET     e.g. yourname/panda-secret (the private dataset with worker.json)
//   PANDA_API_URL      optional: fixed https URL of a named Cloudflare tunnel
//   ENDPOINT_DATASET   optional: e.g. yourname/panda-endpoint, where quick tunnels publish their URL
const crypto = require("crypto");

const env = (k) => process.env[k] || "";

function authed(req) {
  const got = String(req.headers.authorization || "").replace(/^Bearer\s+/i, "");
  const want = env("PANDA_PASSWORD");
  if (!want || !got) return false;
  const a = crypto.createHash("sha256").update(got).digest();
  const b = crypto.createHash("sha256").update(want).digest();
  return crypto.timingSafeEqual(a, b);
}

function send(res, status, body) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify(body));
}

const timeout = (ms) => AbortSignal.timeout(ms);

async function kaggleRpc(service, method, body) {
  const r = await fetch(`https://api.kaggle.com/v1/${service}/${method}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${env("KAGGLE_API_TOKEN")}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: timeout(25000),
  });
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok && !data.error, status: r.status, data };
}

/** The worker's public URL, or "" when unknown. */
async function workerUrl() {
  if (env("PANDA_API_URL")) return env("PANDA_API_URL").replace(/\/+$/, "");
  const ds = env("ENDPOINT_DATASET");
  if (!ds) return "";
  const [owner, slug] = ds.split("/");
  const headers = { Authorization: `Bearer ${env("KAGGLE_API_TOKEN")}` };
  const tries = [
    () => fetch(`https://www.kaggle.com/api/v1/datasets/download/${owner}/${slug}/endpoint.json?t=${Date.now()}`,
      { headers, redirect: "follow", signal: timeout(15000) }),
    () => fetch("https://api.kaggle.com/v1/datasets.DatasetApiService/DownloadDatasetRaw", {
      method: "POST", headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ ownerSlug: owner, datasetSlug: slug, fileName: "endpoint.json" }),
      redirect: "follow", signal: timeout(15000) }),
  ];
  for (const t of tries) {
    try {
      const r = await t();
      if (!r.ok) continue;
      const j = JSON.parse(await r.text());
      if (j && typeof j.url === "string") return j.url.replace(/\/+$/, "");
    } catch { /* try the next route */ }
  }
  return "";
}

async function ping(url) {
  if (!url) return null;
  try {
    const r = await fetch(`${url}/health`, { signal: timeout(5000) });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

module.exports = { env, authed, send, kaggleRpc, workerUrl, ping, timeout };
