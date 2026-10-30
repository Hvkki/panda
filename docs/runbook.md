# Runbook

Load the keys first, in the shell you work from:

```bash
set -a; . ./panda-api-keys.env; set +a
```

## Start, stop, check

- **Start / Stop:** the chip in the app's top bar. Or call the `session` function with the owner's
  sign-in: `POST $SUPABASE_URL/functions/v1/session` with `{"action": "start" | "stop" | "status"}`.
- **Check the worker:** the newest `worker_sessions` row (`status`, `heartbeat_at`). Or insert a
  `commands` row with `op = "ping"` and read its `result`.
- **Read the worker log:** `kaggle kernels output vladraf/kirocrew-worker -p out -o`. Look for
  `running; waiting for messages` and `answered message N`.
- **A running worker keeps its code.** After any worker change: `./scripts/deploy-worker.sh`,
  then Stop, then Start (or wait: each hand-off, ~10 h 35 m into a run, starts the latest deployed code).

## Deploy

- **Web app:** `python3 scripts/deploy-app.py`. It builds `app/` and deploys to production through
  the Vercel REST API. The Vercel CLI rejected the account token, so the API is used.
- **Worker:** `./scripts/deploy-worker.sh`. It uploads `worker/worker.py`, `worker/prompt.md` and the
  skills to the Storage bucket `kirocrew-state`.
- **Database:** in the Supabase SQL editor, run `supabase/schema.sql`, then
  `supabase/migration_002_app.sql`, then `supabase/migration_003_panda.sql`. Safe to re-run.
- **`session` function:** Supabase dashboard → Edge Functions → `session`. Paste
  `supabase/functions/session/index.ts`. Set the function secrets `KAGGLE_API_TOKEN`,
  `KAGGLE_KERNEL` and `KAGGLE_DATASET` (values in the keys file). **Redeploy this function for the
  hand-off**: it adds the `handoff` action; without it the worker cannot start its successor and just stops
  at the time guard.
- **One Kaggle notebook (one CPU) per account:** `kirocrew-state/config/kernels.json` maps a user id
  to its notebook, e.g. `{"<user id>": "vladraf/panda-worker-tetiana"}`; unlisted accounts use
  `KAGGLE_KERNEL`. Create the notebook once (a private CPU script with internet and the
  `KAGGLE_DATASET` attached; any placeholder code, Start replaces it), add the line, upload the file.
  Each account's snapshots and browser logins live under `users/<user id>/` in the same bucket (the
  account in `worker.json` keeps the top-level paths and is the only one with Google Drive).
- **Auth settings:** Site URL `https://panda-app-gamma.vercel.app`. Redirect URL
  `https://panda-app-gamma.vercel.app/**`. "Allow new users to sign up" OFF.
- **Android app (`android/`):** a Trusted Web Activity: it opens the live web app full screen in
  Chrome, so app deploys reach it with no new APK. Build with `python3 scripts/build-apk.py
  --version-code N` (raise N for each new APK; needs JDK 17+, `$ANDROID_HOME`, Gradle as `$GRADLE`).
  The signing key lives in `kirocrew-state/config/android/`, never in git; losing it means the
  next APK cannot install over the old one. Its fingerprint is in
  `app/public/.well-known/assetlinks.json`; if that file is not live, the app shows a URL bar.

## Automatic restart (hand-off) and saved files

- **Watch one:** the worker log shows `hand-off: successor <id> requested`, then `ready; waiting for run`
  on the new run, `successor is ready; no new messages from here on` on the old one, and `files restored`.
  `worker_sessions.info` has `handoff` / `handoff_from`, `files` (what the last save wrote) and `files_restored`.
- **Turn it off:** `"auto_restart": false` in `worker.json` (then `python3 scripts/kaggle-dataset-sync.py`),
  or `PANDA_AUTO_RESTART=0`. Work-file saving has its own switch, `"save_files": false`.
- **Test cheaply:** a run younger than 30 min is refused by the function (restart-loop guard), so a hand-off
  cannot be tried with a tiny `max_runtime_secs` below ~1 h. Use `max_runtime_secs: 3300` and
  `handoff_lead_secs: 1500` (hand-off at 30 min).
- **If Kaggle cancels the old run when the new version is pushed** (not seen in the docs, untested here): set
  `"handoff_mode": "sequential"`. The pre-handoff snapshot already limits the loss to a few minutes.
- **Local checks:** `python3 tools/test_autorestart.py` (file selection, parts, timing, snapshot/restore of a
  temp HOME; no network).
- **Restore a bad files snapshot:** delete `latest-files*` from the `kirocrew-state` bucket (the full copy
  itself is named in `latest-files.tar.gz.parts.json`: a Kaggle dataset `panda-work-a`/`-b` or the Drive
  file `Panda/state/files.tar.gz`); the next run starts with empty work folders. To go back one full copy,
  point the manifest at the other Kaggle dataset's file (`kaggle datasets files <user>/panda-work-b`).

## Rotate keys

The keys were pasted into a chat transcript while Panda was built, so rotate them.

1. Rotate each one at its provider:
   - Supabase: secret and publishable keys (API settings). Reset the worker user's password.
   - Kiro portal: API key.
   - Kaggle: API token.
   - Vercel: account token (and revoke the unused `vck_` key).
   - Google Cloud: OAuth client secret. Then sign in to Drive again (below).
   - VAPID: generate a new key pair. Users must turn notifications on again.
   - Browser key: a new key makes the saved browser logins unreadable. Ask Panda to run
     `pb forget` or use Settings → Forget.
2. Update `panda-api-keys.env`.
3. `python3 scripts/kaggle-dataset-sync.py --dry-run` shows which fields differ (names only).
   Run it without `--dry-run` to upload the new values to the Kaggle dataset.
4. Stop, then Start the worker. It reads the dataset when it starts.

## Google Drive sign-in (when the refresh token stops working)

Testing-mode refresh tokens expire after 7 days. The published app avoids this for new grants.
To sign in again, use an OAuth link with scope `https://www.googleapis.com/auth/drive.file`,
PKCE, and redirect `http://127.0.0.1:8765`. Copy the full address you land on, exchange the
code for a refresh token, put it in `panda-api-keys.env` as `GOOGLE_DRIVE_REFRESH_TOKEN`, and
run the dataset sync.

## Live tests

These use Kaggle time and Kiro credits, and they create chats. Each one should end with a stop.

Before a test run, give the tester account a new random password through the admin API (the tests
sign in with it):

```bash
PW="T-$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')"
curl -s -o /dev/null -X PUT "$SUPABASE_URL/auth/v1/admin/users/<tester-user-id>" \
  -H "apikey: $SUPABASE_SECRET_KEY" -H "Authorization: Bearer $SUPABASE_SECRET_KEY" \
  -H "Content-Type: application/json" -d "{\"password\":\"$PW\"}"
export TESTER_PASSWORD="$PW" TESTER_EMAIL=tester@kirocrew.local
export SB_URL="$SUPABASE_URL" SB_PUBLISHABLE="$SUPABASE_PUBLISHABLE_KEY"
```

Use the tester user id that appears in `tools/*.py`. Lock the account again afterwards.

| Script | What it proves |
|---|---|
| `tools/test_phase2.py start \| test \| test2 \| stop` | Files, attachments, projects, vault, commands |
| `tools/test_drive_kaggle.py start \| test \| stop` | Big-file offload to Drive and the `drive` command |
| `tools/probe_stream.py` | Step-by-step timing of one reply |
| `tools/probe_think.py` | The thinking stream |
| `tools/test_browser_kaggle.py start \| run \| check \| stop` | Browser on Kaggle, take-over, cookies after restart |
| `tools/test_browser_local.py` | Browser engine on your machine, against live Realtime: frames, actions, focus, take-over, cookies |
| `tools/test_browser_app.sh` | The app's own supabase-js client drives the engine (protocol 2.0 ↔ 1.0) |
| `tools/test_client.py` | Phone stand-in: request, wait, chat, stop |

## Local tests (no Kaggle, no credits)

- **Worker ↔ Kiro Crew:** install Kiro Crew from `vendor/KiroCrew` into a Python 3.12 venv, run
  `kirocrew setup --agent-only` and `kirocrew gateway --no-open --no-tunnel` with a throwaway `HOME`,
  then `python tools/test_worker_local.py`. It checks the relay rules, slash commands, every setting,
  saving one, usage, side chat and command turns with Supabase faked.
- **UI:** `cd app && npm run build`, then `python tools/screenshots/mock_ui.py <out-dir> [fixtures]`.
  Supabase (auth, REST, Storage, Realtime) is mocked; it saves screenshots and checks the panda
  animations. Both browser tests need `PANDA_CHROMIUM` where Playwright's own download is missing.
- **Pictures UI:** `python tools/screenshots/mock_drawing.py <out-dir> [picture.png]` (same mocks):
  the drawing panda and its % in real time, light and dark, an uploaded inline picture, and an old
  reply that names a worker path.

## Local setup

- **Python:** `pip install -r tools/requirements.txt`.
- **Playwright Chromium:** `python -m playwright install chromium`. The worker installs it on Kaggle.
  In the authoring sandbox the binary was `/inner_container/opt/playwright/chromium-1232/chrome-linux64/chrome`,
  launched with `--no-sandbox --disable-crash-reporter --disable-gpu`.
- **Kiro CLI** (only to run Kiro Crew locally): `curl -fsSL https://cli.kiro.dev/install | bash`.
- **Node 22** for `app/`.
