# Panda

Claude Code from your phone or browser. A Kaggle notebook runs the real `claude` CLI, logged in with
your Claude subscription, and the Panda web app (hosted on Vercel) talks to it through a tunnel.

There is no database. Kaggle is the only backend:

```
Phone / browser (Panda PWA, Vercel)
   │  /api/session  (Vercel function: Start, Stop, Status, finds the worker)
   ▼
Kaggle ── notebook "panda-worker" ── worker.py ── claude -p (stream-json) ── your Claude plan
   ▲            ▲
   │            └─ private dataset "panda-secret": worker.json (Claude token, app password)
   └─ Cloudflare tunnel: the app streams chat straight from the worker
```

- **Chats** are saved in your browser (localStorage). The worker keeps Claude's session files only
  while it runs; if a session is gone the app re-sends the conversation as context.
- **Files** Claude creates live in the worker's workspace. Download them from the Files page before
  you stop the worker.
- **Permissions:** Claude Code runs with `bypassPermissions` inside the disposable Kaggle VM, so it
  never waits for an approval. Anyone with your app password can run commands on that VM, so use a
  long password.
- No custom system prompt: Claude Code runs as shipped.

| Path | What it is |
|---|---|
| `app/` | React + TypeScript + Vite web app (PWA) |
| `app/api/` | Vercel functions: `session.js` (Start / Stop / Status), `_lib.js` |
| `worker/worker.py` | Runs on Kaggle: installs Claude Code and cloudflared, serves the chat API |
| `worker/kernel-metadata.json` | Notebook settings (the Start button pushes `worker.py` for you) |
| `scripts/setup-kaggle.py` | Saves your Claude token and password into private Kaggle datasets |
| `scripts/deploy-app.py` | Builds and deploys the app, functions and `worker.py` to Vercel |

## Set up

1. **Claude login (once).** On your own computer run `claude setup-token`. It opens a browser, you
   sign in to your Pro/Max account, and it prints a long-lived token (`sk-ant-oat01-…`). That token
   is how the subscription login survives Kaggle's wiped disks. Keep it secret.
2. **Pick a password** for the app (`PANDA_PASSWORD`).
3. **Tunnel**, pick one:
   - *Quick tunnel (no account):* nothing to do. The worker publishes its changing URL to the
     Kaggle dataset `<you>/panda-endpoint`, and the Vercel function reads it. Start takes a minute
     longer.
   - *Named Cloudflare tunnel (stable URL, recommended):* create a tunnel in Cloudflare Zero Trust,
     point a hostname at `http://localhost:8787`, and keep the tunnel token (`CLOUDFLARED_TOKEN`) and
     the hostname (`PANDA_API_URL`).
4. **Save the secrets in Kaggle:**
   ```bash
   pip install kaggle
   export KAGGLE_USERNAME=... KAGGLE_API_TOKEN=... CLAUDE_CODE_OAUTH_TOKEN=... PANDA_PASSWORD=...
   python3 scripts/setup-kaggle.py
   ```
5. **Deploy the app** (variable names are in `.env.example`):
   ```bash
   export VERCEL_TOKEN=... KAGGLE_KERNEL=<you>/panda-worker KAGGLE_DATASET=<you>/panda-secret
   export PANDA_PASSWORD=... KAGGLE_API_TOKEN=...   # plus PANDA_API_URL or ENDPOINT_DATASET
   python3 scripts/deploy-app.py --set-env
   ```
6. Open the app, enter the password, press **Start**. About two minutes later it says Online.
   Sending a message while offline starts the worker for you.

## Rotating the Claude token

Run `claude setup-token` again, export the new `CLAUDE_CODE_OAUTH_TOKEN`, and run
`python3 scripts/setup-kaggle.py`. Stop and Start the worker.

## Not verified

The worker's HTTP API and the Vercel function were exercised against stand-ins for `claude` and
`cloudflared`. These have not been run against the real services yet: the Claude Code install on
Kaggle, the Kaggle calls in `app/api` (`SaveKernel`, `GetKernelSessionStatus`, and the endpoint
dataset download), and a Vercel deployment. Check them on the first real Start.
