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

- **Chats and projects** are saved in your browser (localStorage). Export or import them in
  Settings → Data. The worker keeps Claude's session files only while it runs; if a session is gone
  the app re-sends the conversation as context.
- **Everything you customize** (skills, connectors, scheduled tasks, API keys, memory, developer
  variables, the Claude token) is saved by the worker into the private dataset `panda-secret`
  (`worker.json`), so it survives Kaggle's wiped disks.
- **Files** Claude creates live in the worker's workspace. Download them from the Files page before
  you stop the worker.
- **Permissions:** Claude Code runs with `bypassPermissions` inside the disposable Kaggle VM, so it
  never waits for an approval. Anyone with your app password can run commands on that VM, so use a
  long password.

## Features

All built on Claude Code itself:

| Feature | How it works |
|---|---|
| Chat | `claude -p --output-format stream-json`, streamed to the app; model and effort per chat |
| Projects | Custom instructions (`--append-system-prompt`) and a folder per project on the worker |
| Developer | One chat that edits Panda's own repo, pushes and redeploys; its own env variables |
| Skills | Claude Code skills in `~/.claude/skills`, edited in Customize |
| Connectors | MCP servers (`claude mcp add-json -s user`), remote URL or command |
| Browser | Chromium + Playwright MCP, off by default (turn on in Customize); watch and take over in the Browser panel |
| Scheduled | Tasks the worker runs while online; results go to Notifications |
| API keys | Environment variables every chat can use (names shown to Claude, never values) |
| Memory | `~/.claude/CLAUDE.md`; Claude adds to it when you ask it to remember something |
| GitHub | Settings → GitHub: one token signs in `git` and `gh` for every chat; shows which repos it reaches and how to fix a token that hides private ones. Projects can pick a repo, cloned on first use |
| Appearance | Six themes (Paper, Sand, Graphite, Midnight, Mocha, Terminal), any accent colour, interface/reply/code fonts, density, corners, width, transcript or bubble messages |
| Search, Settings | Search all chats; defaults, export/import |
| Connect Claude | Settings → Claude account runs `claude setup-token` on the worker: open the link, paste the code |

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
6. Open the app, enter the password, press **Start**. About 30 seconds later it says Online.
   Sending a message while offline starts the worker for you.

## Plan usage

The worker keeps plan usage down: MCP tool definitions are loaded only when Claude needs them
(`ENABLE_TOOL_SEARCH`), non-essential background requests are off, the browser is off until you turn it
on, a kept Claude process that starts a turn on its own (a finished background command) is stopped,
and a conversation restored after a restart is capped at about 40k characters.

## Rotating the Claude token

In the app: Settings → Claude account → Connect, open the link, paste the code. Or run
`claude setup-token` yourself, export `CLAUDE_CODE_OAUTH_TOKEN`, and run
`python3 scripts/setup-kaggle.py`, then Stop and Start the worker.

## Verified on Kaggle (24 Nov 2026)

With the real subscription token, on the deployed site and a live worker: chat replies, project
instructions, a skill triggering, memory, a scheduled task posting to Notifications, connectors
setup, Claude driving the browser (Wikipedia, Google) and the live Browser panel on a phone-sized
screen, settings saved to Kaggle and restored after a restart, and the Connect Claude login link.
Every page was opened at phone size with no errors. Kaggle's API answers 404 to some default client
User-Agents, so `app/api` sends its own.

Not verified: the Developer chat pushing to GitHub (optional, needs your `GITHUB_TOKEN`; with only `VERCEL_TOKEN` it publishes to Vercel and keeps its changes in the Kaggle dataset), and scheduled tasks
firing on their own timer (only "Run now" was tested; the timing logic has a local test).
