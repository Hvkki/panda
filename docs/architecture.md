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
| `messages` | user rows, assistant rows (streamed `content`, `thinking`, `meta.tools`), approval rows (role `tool`) | app (user, decisions), worker (assistant, approvals) |
| `worker_sessions` | one row per Kaggle run: status, heartbeat, stop request | `session` function, worker |
| `commands` | app → worker requests: `ping`, `gw`, `secrets.sync`, `browser.forget` | app (request), worker (result) |
| `notifications` | scheduled-task results, mirrored from Kiro Crew | worker |
| `user_settings` | per-user settings: thinking, approvals, defaults, browser topic, memory | app |
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

The app inserts `commands` rows. The worker runs them and writes `result`. The `gw` op calls only
gateway routes on the allow-list `GW_ALLOWED` in `worker/worker.py`. That list is a security boundary:
review it before adding routes that expose configuration.

## Files

Outputs up to 45 MB go to Storage. Larger outputs go to Google Drive, and the `files` row points to
`drive:<file id>`. The Drive copy stays in the owner's Drive when the card is deleted in the app.

## Browser

`panda-browser` runs a headless Chromium with a local HTTP API (the `pb` command). Frames go out as
JPEG broadcasts on a private Realtime channel whose name is stored in `user_settings.browserTopic`.
Taps from the phone come back on the same channel. Cookies are saved encrypted with Fernet to
`browser/state.enc` in the `kirocrew-state` bucket.

## Key vault

The owner stores keys through RPC functions (set, delete, list names). Values cannot be read back
through the app. Only the worker reads values, and it writes them to `~/.panda/secrets.env` for the
agent to source.
