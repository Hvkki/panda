# Architecture

```
Phone or browser (Panda PWA on Vercel)
        │  Supabase JS: sign-in, rows, Realtime, Storage, Edge Function
        ▼
Supabase project ufvskhagspdjslomzwjh
  Postgres with row-level security · Realtime · Storage buckets · Edge Function `session`
        ▲  REST, Storage, Realtime
        │
Kaggle notebook vladraf/kirocrew-worker (CPU, unsandboxed, hands over to a fresh run at ~10 h 35 m)
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
- **Stop:** the app sets `stop_requested` on every live run of the account. The worker finishes the
  turns in flight, uploads a snapshot to Storage and exits. A Stop never restarts.
- **Time limit:** see the next section.

## Hand-off at the time limit

Kaggle ends a run at 12 h; the worker's guard is `MAX_RUNTIME` (11 h). The worker has no Kaggle
credential (the token lives only in the `session` function's secrets), so it asks that function.

1. At `MAX_RUNTIME - 25 min` (`handoff_lead_secs`, 5-40 min) the worker takes a full snapshot (insurance),
   then calls `session` with `{"action":"handoff","session_id":<its row>}` as the worker account. The
   function checks the run is live, not asked to stop and at least 30 min old, returns the existing
   successor if there is one (idempotent), and starts the next run of the same account exactly like Start
   (new row, `info.handoff_from = <old id>`, pushes the latest `code/worker.py`, so a hand-off also deploys).
2. The successor boots and installs, sets `info.handoff_ready`, then **waits** (heartbeating) until the old
   row is `stopped`/`error` or silent for 3 min. It restores only after that.
3. The old run keeps serving until it sees `handoff_ready`, then leaves the loop: no new messages or
   commands are claimed, turns in flight finish, browser and Codex login are saved, final snapshot, Crew
   is stopped, row set to `stopped` (`info.stop_reason = "handoff"`). At `MAX_RUNTIME` it stops anyway.
4. Messages sent in the gap stay `pending` and are answered by the successor (commands wait up to 3 min).

The app shows the serving run (a running one first, else the newest), and "Restarting" while a successor
or a draining old run is the newest. Stop sets `stop_requested` on both, so a waiting successor never takes
over after a Stop. If the function cannot be reached the worker retries (5 times, 2 min apart), then at the
time guard snapshots, tries once more after the snapshot, and otherwise just stops (app: offline).

Settings (`worker.json` in the Kaggle dataset, env wins): `auto_restart` (default true; env
`PANDA_AUTO_RESTART=0` turns it off), `handoff_lead_secs`, `handoff_mode` (`"overlap"` default, or
`"sequential"`: drain, snapshot, request, exit, for the case that Kaggle cancels a running version when a
new one is pushed).

## What a snapshot holds

Every snapshot (every 30 min, on Stop, on hand-off) writes to `kirocrew-state` (per account under
`users/<id>/`): `latest-crew.tar.gz`, `latest-kiro.tar.gz` and `latest-files.tar.gz`. The last is the
owner's files: `~/dev ~/projects` (without `node_modules dist build .venv venv .next __pycache__ .cache`),
`~/work` (without `~/work/tmp`; its `node_modules` are kept) `~/outputs ~/uploads`, and `~/.panda` without
secrets and machine state (`dev.env secrets.env drive.json browser.json browser/ stt-lib/ *.log *.pid`).
Files over 100 MB are skipped and logged (4 GB when the Kaggle store is set up). It is restored before
Kiro Crew starts; unchanged file sets are not uploaded again.

A Free Supabase project refuses objects over 50 MB, so any of the three files bigger than 40 MB
(`state_part_mb`) is stored as `name.<gen>.pNNN` parts plus a `name.parts.json` manifest (sha256), written
last. The work files are a **full copy plus every change since it**, never trimmed. Both go first to the
account's own **Modal volume** `panda-store` (1 TiB free; keys `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` from that
account's vault, `modal_store_keys` names another pair; files under `/<account>/<blob>/<unique>.pbak`); the
manifest switches only after Modal lists the file at its full size, then the previous file is removed.
Without Modal keys: a full copy under 200 MB (`state_storage_max_mb`) stays in Storage; a bigger one goes to
a private Kaggle dataset (`panda-work-a` / `panda-work-b`, a `-<id>` suffix for other accounts; needs
`kaggle_username`/`kaggle_key` in `worker.json`), Google Drive (`Panda/state`, primary account) only as the
last fallback. The two datasets take turns, and the manifest switches only after Kaggle lists the new
file, so a failed push keeps the last good copy. A copy in a weaker store moves to the best one at the
next save. Between full copies `latest-files-delta.tar.gz` holds every file changed since the full copy
(`latest-files-index.json.gz` lists what it holds). A new full copy is made when the changes pass 300 MB
(`files_delta_max_mb`), a changed file is over 100 MB, or the copy is 8 h old (`files_base_every_secs`);
Stop saves only the changes. The storage GC (every 6 h) also removes snapshot parts no manifest names
(after a day) and the pre-chunk `latest-kiro.tar.gz` once the chunk index has superseded it for a day.
Work files are not encrypted (the bucket, volume and datasets are private); browser cookies and the Codex
login still are, separately.

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

Inline images: when Crew saves a turn it copies every image the reply shows with `![](…)` into
`~/.kiro/crew/sessions/<chat>.attachments/<hash>-<name>` and rewrites the path, so the final text names
that copy. The worker publishes those copies too (images only) and rewrites them to `panda-file:<id>`,
which the app resolves to a signed URL (react-markdown's `urlTransform` must let `panda-file:` through).
A path the app cannot load shows "picture not available", never a link.

## Pictures (Codex)

`panda-image` runs `codex exec` with the owner's ChatGPT plan (pinned `@openai/codex` 0.164.0) and copies
the PNG out of `~/.codex/generated_images`. Kaggle starts on a clean disk, so the worker saves
`~/.codex/auth.json` (and `config.toml`) encrypted with the browser key to `codex/login.enc` in
`kirocrew-state` (per account, like the browser logins) whenever it changes and at stop, and at start
puts it back and reinstalls Codex. While the newest step is `make_image.py`, the app shows a drawing
panda with an estimated % (`IMAGE_SECS` in `app/src/lib/drawing.ts`, measured at 44 s).

## Browser

`panda-browser` runs a headless Chromium with a local HTTP API (the `pb` command). Frames go out as
JPEG broadcasts on a private Realtime channel whose name is stored in `user_settings.browserTopic`.
The engine also broadcasts `status` (URL, control, loading, the focused text field) and `action`
(each step's caption and the target's box, for the app's pointer). Taps, scrolls and typing from the
phone come back on the same channel; only while the user has control. Frames flow only while a viewer
says hello. At the end of a turn that used the browser, the worker saves the screen to
`panda-files/<owner>/browser/<chat>/<reply>.jpg` (`meta.browser`). Cookies are saved encrypted with
Fernet to `browser/state.enc` in the `kirocrew-state` bucket.

A viewer's hello carries `mode` and its size. For `mobile` the engine emulates a phone over CDP
(400 CSS px wide, mobile user agent, touch) and sends `Page.captureScreenshot` frames clipped to the
visual viewport at scale 1.5; the screencast is then only the "page changed" signal.

## Key vault

The owner stores keys through RPC functions (set, delete, list names). Values cannot be read back
through the app. Only the worker reads values, and it writes them to `~/.panda/secrets.env` for the
agent to source.

Names starting with `DEV__` belong to the Developer tab. They are left out of `secrets.env` and
Crew's `.env`; while a turn of the Developer chat (`user_settings.developerChatId`) runs, the worker
writes them without the prefix to `~/.panda/dev.env` and deletes the file afterwards.
