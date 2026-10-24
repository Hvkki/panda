# Panda

Panda is a personal AI assistant you use from a phone or a browser. It is built on
[Kiro Crew](https://github.com/kirodotdev/KiroCrew), an open-source agent layer (memory,
scheduled tasks, skills, tools, multiple sessions) that runs on `kiro-cli`.

- **Web app** (installable PWA): https://panda-app-gamma.vercel.app
- **Backend:** Supabase project `ufvskhagspdjslomzwjh` (sign-in, Postgres with row-level
  security, Realtime, Storage, one Edge Function).
- **Worker:** a Kaggle notebook (`vladraf/kirocrew-worker`) that runs Kiro Crew. You press
  **Start** in the app. It runs until you press **Stop** or it reaches its 11-hour safety limit.
  It never starts itself again.

Earlier names: Embra.

> **If you are the next agent:** read this file, then [docs/status.md](docs/status.md) (what is
> verified and what is not), then [docs/runbook.md](docs/runbook.md).
> **The API keys are not in this repository.** The owner gives you a plain-text file named
> `panda-api-keys.env` (same format as `.env`). Ask for it before any step that calls an API.

## Repository layout

| Path | What it is |
|---|---|
| `app/` | React + TypeScript + Vite web app (PWA). Deployed to the Vercel project `panda-app`. |
| `worker/worker.py` | Runs inside the Kaggle notebook. Starts Kiro Crew, answers chats, streams replies, thinking, tool steps and approvals, mirrors notifications, publishes files, offloads big files to Google Drive, runs the browser, syncs the key vault, serves the app's command relay. |
| `worker/prompt.md` | Panda's system prompt. Stored in Supabase Storage (`config/prompt.md`), so you can change it without redeploying the worker. |
| `worker/skills/` | Skills the agent loads: `panda-docs` (PDF, DOCX, PPTX), `image-search`, `panda-browser` (real Chromium, live view, take-over), `panda-drive` (Google Drive). |
| `supabase/` | Schema and migrations (run in order), and the `session` Edge Function (Start, Stop, Status). |
| `tools/` | Live tests and probes, see [docs/runbook.md](docs/runbook.md). `tools/screenshots/` holds Playwright harnesses for UI checks. |
| `scripts/` | Deploy the app, deploy the worker, sync the Kaggle dataset after a key change, fetch Kiro Crew. |
| `vendor/KIROCREW.md` | Kiro Crew is an upstream dependency. It is pinned by commit and not copied here. |
| `docs/` | Status, runbook, architecture, lessons, a few screenshots. |
| `legacy/` | The first Kaggle smoke-test scripts, kept for reference. |

## Set up

1. Clone this repository.
2. `./scripts/fetch-kirocrew.sh` checks out Kiro Crew at the pinned commit into `vendor/KiroCrew`.
3. Put `panda-api-keys.env` (from the owner) in the workspace root, **outside this repository**, and load it:
   ```bash
   set -a; . ./panda-api-keys.env; set +a
   ```
4. Web app: `cd app && npm ci && npm run build`. Deploy: `python3 scripts/deploy-app.py`.
5. Worker: `./scripts/deploy-worker.sh`. A worker that is already running keeps the code it started
   with, so press **Stop** and then **Start** in the app.
6. Python tools: `python3 -m venv .venv && . .venv/bin/activate && pip install -r tools/requirements.txt`.

## Architecture in one picture

Phone or browser (Panda PWA) ↔ Supabase (rows, Realtime, Storage, `session` function) ↔ worker
on Kaggle ↔ Kiro Crew gateway (127.0.0.1:5476) ↔ kiro-cli ↔ Kiro models.
Details: [docs/architecture.md](docs/architecture.md).

## Hard rules

- **Keys are never committed**, never printed in logs or chat, and never stored in GitHub Actions
  secrets (anyone who can run workflows on a repo can read them).
- **This repository is public.** Keep keys and personal data out of it.
- **Sign-ups must stay closed.** At handoff they were still open (see
  [docs/status.md](docs/status.md)). Turn off Supabase → Authentication → Sign In / Providers →
  Email → "Allow new users to sign up". The database trigger `block_signups` refuses only a sixth
  account, so it does not replace that switch.
- **Kaggle runs start only when a person presses Start.** Do not add automatic restarts.
- **The worker runs unsandboxed on Kaggle**, because Kaggle blocks user namespaces. The owner
  accepted that. The app's approval flow is the control on tool use.
- **Do not claim a UI works from a still screenshot.** Motion, live streams, taps and push
  notifications need a device test. See [docs/status.md](docs/status.md).

## License

No license has been chosen yet (owner decision). Kiro Crew is Apache-2.0.
