# Status (24 October 2026)

Each line says how it was checked. "Not verified" means nobody has seen it work on a device yet.
Several earlier claims were wrong. This page lists only what was checked.

## Verified on the live system

- The web app deploys to Vercel. `/`, `/logo.svg`, `/manifest.webmanifest` and `/sw.js` return 200.
- Sign-in with email and password works for the owner.
- Start and Stop from the app's `session` function. A worker is running 85 to 110 s after Start.
  Stop saves a snapshot and the worker exits.
- Streaming is incremental. On a fresh worker, tool steps appeared at 11, 14, 16, 19 and 22 s,
  and the reply finished at 24 s (`tools/probe_stream.py`).
- Thinking streams into the database: 0, then 207, then 385 characters in about 2 s on a
  reasoning task (`tools/probe_think.py`).
- PDF, Word and PowerPoint creation produce valid files (PDF in 36 s, PPTX in 17 s).
- Image search returns licensed images with credits.
- The agent reads attachments and project files. Project files were broken until the Storage
  download was fixed for gzip.
- The API-key vault works. The agent uses a stored key without showing it.
- Google Drive: a 60 MB output was moved to Drive automatically (22 s). The `drive` command
  upload, list, usage, link, download and trash all worked.
- Browser: the agent opened pages, the phone stand-in took over and handed control back, and
  cookies survived a restart. The cookie file in Storage is encrypted.
- The inline live-browser card in a chat renders a live Wikipedia page. Apple's pages render blank
  in headless Chromium (they block bots). Take-over is the fallback.
- A scheduled job fired on time and its result arrived as a notification (earlier test).
- Settings save to the database. The upsert now uses `onConflict`, and the API returns 200 where it
  used to return 409.
- The round Panda mark renders (`docs/screenshots/panda-mark-v3.png`).
- Kiro Crew's `kiro-cli` 2.33.1 and Crew 0.9.0 install on Kaggle with the official installers.

## Fixed in code, not yet verified on a device

- **Approve / Always allow / Deny.** The root cause: Supabase query builders are lazy. An update
  without `await` is never sent. The code now awaits and shows an error if the update fails.
- **Readable tool steps.** Labels such as "Creating PDF · …", "Searching images · …" and
  "Running a command" replace raw shell lines. Consecutive duplicates are collapsed. The mapping
  was tested on the exact raw lines from the owner's screenshots.
- **Thinking panel.** Shown in the chat only when Settings → Chat & tools → Show thinking is on.
- **Inline live browser card**, shown inside the assistant message that used the browser.
- **Haiku 5.5** is in the main model list next to Opus 5.5 and Sonnet 5.5.
- **Chat titles on GLM.** The worker sets `agent.role_models.background = glm-5`. Confirm it with
  `kirocrew config get agent.role_models` on the worker, or watch a new chat's title.
- **Smaller agent.** Desktop-only tool servers (computer use, dashboard guide, crew mate) are removed
  from the agent definition. The "hi" context was 69k tokens before. It is not measured again yet.
- **Service worker.** HTML and `sw.js` are network-first. Only hashed `/assets/` files are cached.
  A new version reloads once.
- **Panda's ring and bob animation.** The CSS was corrected. It is **not verified visually**. The
  earlier claim that the ring spins was wrong: the old rules targeted parts the new mark does not
  have, and reduced-motion settings turned motion off.

## Not verified or not done

- **Phone push notifications.** They need the installed PWA on a real device.
- **Kiro Crew's full command surface in the app.** The relay now allows more gateway routes
  (agents, workspaces, knowledge, artifacts, spawn, memory, mcp-gateway, config, status), but
  there is no UI for most of them.
- **Customize create, edit and delete.** Only the read paths were tested.
- **Google Drive refresh token.** It was issued while the Google app was in Testing mode.
  Testing-mode tokens expire after 7 days. If Drive stops working, run the sign-in again
  (see the runbook).
- **Sign-ups are still open. Close them first.** At handoff the auth setting `disable_signup` was
  `false` and the project had 3 accounts. The database trigger `block_signups`
  (`supabase/migration_003_panda.sql`) refuses only a sixth account, so two more accounts could
  still be created by anyone. Turn off "Allow new users to sign up" in the Supabase dashboard
  (see the runbook, Auth settings).
- **Reply and browser card disagree.** The screenshots `docs/screenshots/v4-chat-phone.png` and
  `v4-chat-tablet.png` show the reply saying Apple's AirPods page is live, while the card shows the
  Wikipedia page. The reply text does not match the browser state. Not investigated yet.
- **Voice (phase 4)** and the **Android app** have not been started. Planned: a Capacitor wrapper
  around the web app.
- **License.** The repository is public and has no license yet.

## Owner-reported issues and where they stand

| Report | Status |
|---|---|
| Browser does not work like GPT | Inline card added and checked with a live page. Needs a device check. |
| Thinking does not show | Stream verified in the database. Panel needs a device check. |
| Output arrives all at once | It was the old worker, 34 minutes old. A fresh worker streams step by step. Restart the worker. |
| Tools have no names | Labels added. Needs a device check. |
| Panda's circle does not spin | Fixed in CSS, not seen. The earlier claim was wrong. |
| Emojis in approvals | Removed. SVG icons instead. The prompt forbids emojis unless the user uses them first. |
| "hi" costs 69k tokens | Desktop-only tools removed. Not measured again. |
| Approve does nothing | Root cause fixed (lazy query builder). |
| Settings do not save | Fixed (upsert conflict key). |
| Titles should use GLM | Configured. Check it. |
| Old app keeps loading after deploys | Service worker rewritten. |
| Kiro Crew commands missing from the app | Not done beyond the relay allow-list. |
