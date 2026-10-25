# Architecture

```
Phone or browser (Panda PWA on Vercel)
        │  Supabase JS: sign-in, rows, Realtime, Storage, Edge Function
        ▼
Supabase project ufvskhagspdjslomzwjh
  Postgres with row-level security · Realtime · Storage buckets · Edge Function `session`
        ▲  REST, Storage, Realtime
        │
Kaggle notebook vladraf/kirocrew-worker (CPU, unsandboxed, stops itself at 11 h)
  worker.py
   ├─ HTTP ──────────► Kiro Crew gateway 127.0.0.1:5476 ──► kiro-cli acp ──► Kiro models
   ├─ websocket /api/ws ─► live thinking
   ├─ skills: panda-docs · image-search · panda-browser (Chromium on :5480) · panda-drive
   └─ Google Drive (drive.file scope)
```

## Tables

| Table | Holds | Written by |
|---|---|---|
| `chats` | conversations: model, effort, project, Kiro Crew slot | app (create), worker (slot) |
| `messages` | user rows, assistant rows (streamed `content`, `thinking`, `meta.tools`; at the end `meta.credits`, `meta.turn_stats`, `meta.command`, `meta.browser`), approval rows (role `tool`) | app (user, decisions), worker (assistant, approvals) |
| `worker_sessions` | one row per Kaggle run: status, heartbeat, stop request, `info.usage` (Kiro account credits) | `session` function, worker |
| `commands` | app → worker requests: `ping`, `gw`, `secrets.sync`, `browser.forget` | app (request), worker (result) |
| `notifications` | scheduled-task results, mirrored from Kiro Crew | worker |
| `user_settings` | per-user settings: thinking, approvals, defaults, browser topic, memory, `crewOverrides` (Kiro Crew settings changed in the app) | app |
| `projects`, `files` | projects and their files; outputs and uploads (`files.path` is a Storage path or `drive:<id>`) | app, worker |
| `secrets` | the API-key vault: the owner sets, deletes and lists names; values are only read by the worker | app (via RPC), worker |
| `push_subscriptions` | web-push endpoints | app (write), worker (read) |
| `relay_workers` | which accounts are worker accounts (row-level security) | admin |

Storage buckets (both private): `kirocrew-state` (worker code, prompt, skills, snapshots, encrypted
browser state) and `panda-files` (per-user folders for files).

## Message flow

1. The app inserts a `messages` row with role `user` and status `pending`.
2. The worker claims it (status `processing`) and maps the chat to a Kiro Crew slot. It sets the
   model, effort and approval mode for that slot.
3. The worker inserts an assistant row (status `streaming`). About every half second it writes the
   reply into `content`, the reasoning into `thinking`, and tool steps into `meta.tools`.
4. If a tool needs approval, the worker inserts a `messages` row with role `tool`, kind `approval`
   and status `pending`. The app writes `decision`, and the worker answers Kiro Crew.
5. At the end the worker re-reads the answer from Kiro Crew's history, publishes new files, and sets
   status `done`.

## Start and stop

- **Start:** the app calls `session`. It inserts a `worker_sessions` row and pushes
  `code/worker.py` from Storage into the Kaggle kernel.
- **Stop:** the app sets `stop_requested`. The worker finishes the turns in flight, uploads a
  snapshot to Storage (Crew state and Kiro sessions) and exits.
- The worker never restarts itself.

## Commands

The app inserts `commands` rows. The worker runs them (up to 4 at once) and writes `result`. It only
runs rows of the account that pressed Start, and expires rows that waited more than 3 minutes
(except `browser.forget` and `secrets.sync`, which are meant to wait for the next start).

| op | What it does |
|---|---|
| `gw` | Calls a gateway route. `relay_check` in `worker/worker.py` is the security boundary: broad reads, writes only to skills, MCP, crons, `PATCH /api/config/kirocrew` (minus `LOCKED_SETTINGS`) and three dashboard keys. Review it before widening. |
| `crew.commands` | Kiro Crew's slash commands and its engine (`agent.acp_backend`). |
| `crew.settings` | Every setting: schema, current value, how Panda may change it (`w`), or why it is locked (`k`). The editable list is read from the installed Kiro Crew (`_EDITABLE_CONFIG`). |
| `crew.set` | Changes one setting (`PATCH /api/config/kirocrew` or `PUT /api/dashboard/config`). The app also saves it in `user_settings.crewOverrides`; the worker re-applies those on start. |
| `usage` | Account usage now (optionally refreshed); also saved in `worker_sessions.info.usage`. |
| `side` | `/btw` side question: Crew's side chat on the chat's slot, answer returned to the app. |
| `secrets.sync`, `browser.forget`, `ping` | As before. |

## Slash commands and credits

- A message whose first word is a Kiro Crew command (`/compact`, `/goal`, …) is sent to Crew as typed,
  without the app's context, and its reply is marked `meta.command`. `/kb <question>` is expanded by the
  worker with the knowledge library's matches. Panda's own commands (`/new`, `/stop`, `/btw`, `/model`,
  `/effort`, `/title`, `/credits`, `/browser`, `/settings`) never reach Crew.
- Each reply gets `meta.credits` and `meta.turn_stats` from Crew's history row. The Kiro account's month
  (used, plan, reset) and the gateway's credits per day live in `worker_sessions.info.usage`, refreshed
  every 5 minutes and a minute after a reply.

## Files

Outputs up to 45 MB go to Storage. Larger outputs go to Google Drive, and the `files` row points to
`drive:<file id>`. The Drive copy stays in the owner's Drive when the card is deleted in the app.

## Browser

`panda-browser` runs a headless Chromium with a local HTTP API (the `pb` command). Frames go out as
JPEG broadcasts on a private Realtime channel whose name is stored in `user_settings.browserTopic`.
The engine also broadcasts `status` (URL, control, loading, the focused text field) and `action`
(each step's caption and the target's box, for the app's pointer). Taps, scrolls and typing from the
phone come back on the same channel; only while the user has control. Frames flow only while a viewer
says hello. At the end of a turn that used the browser, the worker saves the screen to
`panda-files/<owner>/browser/<chat>/<reply>.jpg` (`meta.browser`). Cookies are saved encrypted with
Fernet to `browser/state.enc` in the `kirocrew-state` bucket.

## Key vault

The owner stores keys through RPC functions (set, delete, list names). Values cannot be read back
through the app. Only the worker reads values, and it writes them to `~/.panda/secrets.env` for the
agent to source.
