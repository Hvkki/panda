# Status (25 October 2026)

Each line says how it was checked. "Not verified" means nobody has seen it work on a device yet.
Several earlier claims were wrong. This page lists only what was checked.

## 25 October: credits, commands, Kiro Crew settings, browser, motion

Checked locally, **not yet on Kaggle or a phone** (the worker and app are not deployed yet):

- **Credit usage.** The worker copies Kiro Crew's per-turn `meta.turn_stats.credits` onto each reply
  (`meta.credits`) and keeps the Kiro account's month (`GET /api/sessions/usage`) plus the gateway's
  daily credits in `worker_sessions.info.usage`, so the app can show them offline. The app shows credits
  under each reply, a total per chat, a sidebar meter and Settings → Credits.
  Checked: the code path against Kiro Crew 0.11's source, the routes against a local gateway
  (`tools/test_worker_local.py`), the screens with mocked data. **Not checked with a paid turn**: the
  local gateway had no kiro-cli, so no real credit number has been seen yet. Whether
  `/api/sessions/usage` can read the account with `KIRO_API_KEY` sign-in is unknown; if it can't, the
  app says so and still shows what Panda's replies used.
- **Slash commands.** Typing `/` lists Panda's commands, every Kiro Crew command (live list from
  `GET /api/slash-commands`) and the prompt shortcuts. Kiro Crew commands go to the agent exactly as
  typed. `/btw` (and `/side`) ask on the side without interrupting; `/kb` searches the knowledge
  library. On Kiro Crew's KAS engine the kiro-cli-only commands are marked as such.
  Checked: `/goal`, `/prompts`, `/workflow`, `/kb`, side chat and the error path against a local gateway.
- **Kiro Crew settings.** Settings → Kiro Crew lists all 527 settings with their help, current and
  default values. The 34 that Crew lets a dashboard change (and Panda does not lock) are editable;
  changes are also saved in `user_settings.crewOverrides` and re-applied on every start.
  Checked against a local gateway: listing, saving, range errors, locked keys refused.
- **Browser.** GPT-style card in the reply (window chrome, live screen, a pointer that moves to each
  click, a caption of each step, the final screen kept after the session), a full panel that docks
  beside the chat on wide screens, a steps list with screens, take over / give back, and a keyboard
  bar that types into the page as you type. Checked with live Supabase Realtime: the engine's new
  `action` events, focus reporting, `pb select`, the end-of-turn frame (`tools/test_browser_local.py`),
  and the app's own supabase-js client driving the engine (`tools/test_browser_app.sh`: frames, take
  over, navigate, give back). Bug fixed: the inline card re-registered as a viewer on every frame,
  which made the engine take a screenshot per frame; closing the panel also stopped the card's stream.
- **Panda motion.** Idle blink, ear twitch and float; while working an orbit spins and the eyes read;
  a hop when a reply finishes; on the home screen the eyes look down while you type. Checked in
  headless Chromium with the Web Animations API (`tools/screenshots/mock_ui.py`), including reduced
  motion, where only the working orbit keeps turning. The old `!important` reduced-motion rule that
  stopped the ring is gone. **Not seen on a device.**
- **Security.** The worker now serves only the account that pressed Start (messages and commands),
  expires app commands that waited more than 3 minutes for an offline worker, and the relay allows
  writes only to skills, connectors, scheduled tasks, Kiro Crew settings (minus a locked list) and
  three dashboard settings.
- **"Reply and browser card disagree"** (below) was the screenshot script: `tools/screenshots/shoot3.py`
  inserts a fake AirPods reply. The worker now also takes the final text only from rows after the
  turn's own user row, so a turn without an answer can't show the previous one.

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
- **Kiro Crew's engine on Kaggle.** Kiro Crew 0.11 defaults to its KAS engine (`agent.acp_backend`).
  On KAS, kiro-cli commands such as `/usage`, `/tools`, `/context` answer "doesn't work with KAS";
  the app marks them. Which engine the Kaggle worker runs is not checked (a restored snapshot may
  pin the older kiro-cli default).
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
  Wikipedia page. Explained on 25 October: the screenshot script inserted that reply text.
- **Voice (phase 4)** and the **Android app** have not been started. Planned: a Capacitor wrapper
  around the web app.
- **License.** The repository is public and has no license yet.

## Owner-reported issues and where they stand

| Report | Status |
|---|---|
| Show credit usage | Built (25 Oct). Needs a live turn to see real numbers. |
| Every Kiro Crew `/` command | Built (25 Oct). Needs a device check. |
| Every Kiro Crew setting | Built (25 Oct): all listed, 34 editable. Needs a device check. |
| Browser does not work like GPT | Rebuilt (25 Oct): pointer, captions, steps, docked panel, keyboard bar. Needs a device check. |
| Thinking does not show | Stream verified in the database. Panel needs a device check. |
| Output arrives all at once | It was the old worker, 34 minutes old. A fresh worker streams step by step. Restart the worker. |
| Tools have no names | Labels added. Needs a device check. |
| Panda's circle does not spin | Rewritten (25 Oct); the orbit turns in headless Chromium, also with reduced motion. Not seen on a device. |
| Emojis in approvals | Removed. SVG icons instead. The prompt forbids emojis unless the user uses them first. |
| "hi" costs 69k tokens | Desktop-only tools removed. Not measured again. |
| Approve does nothing | Root cause fixed (lazy query builder). |
| Settings do not save | Fixed (upsert conflict key). |
| Titles should use GLM | Configured. Check it. |
| Old app keeps loading after deploys | Service worker rewritten. |
| Kiro Crew commands missing from the app | Built (25 Oct), see above. |
