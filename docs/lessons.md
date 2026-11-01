# Lessons (things that cost hours)

1. **Supabase JS query builders are lazy.** `supabase.from("messages").update({…}).eq("id", id)`
   sends nothing unless you `await` it or call `.then()`. Two of the owner's bugs came from this:
   Approve did nothing, and settings never saved. Write `const { error } = await …` and handle `error`.

2. **A running worker keeps its code.** The Kaggle kernel runs what was pushed at Start. After
   `deploy-worker.sh`, press Stop and then Start. Some reported bugs came from a worker that was 34
   minutes old.

3. **Service workers cache aggressively.** A cache-first HTML shell kept old builds alive. Now HTML
   and `sw.js` are network-first, and only hashed `/assets/` files are cached.

4. **`kirocrew config set` parses values as JSON.** Pass objects as JSON:
   `kirocrew config set agent.role_models '{"background": "glm-5"}'`.

5. **Kiro Crew's chat stream** is Server-Sent Events with one JSON object per `data:` line. Types seen
   in tests: `chunk` (text), `tool`, `permission`, `context_usage`, `error`, `assistant` (full text),
   and `[DONE]` as the end marker. Thinking arrives on the websocket `/api/ws` as `chat_thinking`.
   Re-read the final answer from the slot history (`/api/chat/slots/{slot}?limit=10`), because streamed
   chunks can be partial.

6. **Kaggle sandbox.** User namespaces are blocked, so Kiro Crew needs `KIROCREW_ALLOW_UNSANDBOXED=1`
   and `agent.sandbox_allow_unsandboxed_exec=true`. Kaggle's `/root/.config` was read-only in the
   authoring sandbox. Set `KAGGLE_CONFIG_DIR` to a writable folder.

7. **Vercel.** The Vercel CLI refused the account token. The REST API works: `POST /v13/deployments`
   with base64 files. A `vck_` token is an AI Gateway key, not an account token. Deploys went to the
   personal scope; the team scope returned 403.

8. **Google.** A Drive service account has no storage quota, so use a user's OAuth grant with
   `drive.file`. Refresh tokens from an app in Testing mode expire after 7 days, so publish the app.
   Publishing needs a home page, a privacy URL and an authorized domain.

9. **Storage downloads.** For gzip objects use `iter_content`, not `raw`. `raw` returns the
   compressed bytes, which corrupted project files.

10. **Playwright in a sandbox.** Chromium's crash reporter fails here. Launch with
    `--no-sandbox --disable-crash-reporter --disable-gpu`.

11. **Live browser view.** Frames travel over Realtime broadcast as base64 JPEG, about 190 KB at most.
    The channel name is a random, private topic per user.

12. **Work files.** In the authoring sandbox, `/tmp` sometimes vanished between tool calls. Keep work
    files in the workspace, and make sure secrets never end up inside the repository folder.

13. **Screenshots show state, not motion.** A still frame cannot prove an animation or a live stream.
    Say "not verified" until someone has seen it on a device.

14. **Inline images went missing twice over.** Kiro Crew rewrites a reply's `![](/path)` to its own copy in
    `sessions/<chat>.attachments/` before the worker reads the final text, so the worker must publish
    from there. And react-markdown's default `urlTransform` turns unknown schemes such as
    `panda-file:` into `""`, so the app needs its own transform. `tools/screenshots/mock_drawing.py`
    checks both.

## Agent notes moved out of the always-on lessons (2026-11-01)

These were injected into every chat's prompt. They are kept here for the Developer chat.

- In Panda's worker, a background flusher thread pushes accumulated text and thinking every ~0.4s even during quiet stretches (long tool calls, model thinking before first token) so the UI never looks ghosted.
- In Panda's worker, text before and after a tool call arrives with no separator ('check.Done.'); insert a paragraph break when the next chunk follows a tool step, and keep the streamed copy over Crew's joined history when it contains the same words.
- Panda steering: a message sent while a chat is answering is marked meta.steer=true; the worker's steer_pending posts it to Kiro Crew with steer=true so it joins the running turn (or Crew queues it); a failed steer falls back to a normal turn. Track busy chats in BUSY_SLOTS.
- The Vercel token can only deploy the Panda app, not push to GitHub. Pushing needs GITHUB_TOKEN.
- A Panda feature can look broken simply because the user's open tab still runs the old build; a new deploy doesn't reach it until reload. An update banner + reload-on-return was added to address this.
- Panda's mic originally used only the browser's SpeechRecognition with no error handler, so it failed silently on iPhone home-screen apps, Brave, etc. Fix: app records an audio clip, the worker transcribes it with faster-whisper (base for English ~2s/11s clip, small for Italian/Ukrainian ~5s) installed in its own folder; browser recognition stays as an offline fallback; clip deleted from the commands row after use.
- Kiro Crew dashboard has STT (Whisper) and voice_reply (Polly/OS/Piper). On this Linux host use Piper offline: install kirocrew[voice]+piper-tts, download en_US-lessac-medium (~60MB) to ~/.kiro/crew/voices/piper, set voice_reply.provider=piper and auto_speak via 'kirocrew config set'. Voice layer is free but each turn still costs Kiro credits. Changing the dashboard env requires a restart.
- The deployed Panda worker prepends a DEV_MARK line to every Developer-chat message so the agent always knows the [DEVELOPER MODE] instructions still apply; keep this when editing worker.py.
- Another session can overwrite Panda's deployed worker (Supabase code/worker.py) independently of the live app. Before deploying, back up the current worker and check it against git branches (e.g. origin/feat/browser-mobile-developer); merge the other branch in so steering/streaming fixes aren't lost.
- Panda's browser (worker/skills/panda-browser/engine.py) ran headless, which Google rejects at sign-in ('This browser or app may not be secure'). Fix: start a private Xvfb virtual display and launch the same Chromium windowed; start the Playwright driver AFTER DISPLAY is set so it inherits it. Falls back to headless if Xvfb is missing; PANDA_BROWSER_HEADLESS=1 forces headless. Confirmed working for Google sign-in.
- Panda's voice UI: tapping the mic turns the composer into one row (voice-bar) with X (cancel, restores the box), a Web Audio analyser level meter (VoiceWave canvas), a timer, a square to stop-and-edit, and a send arrow to stop-and-send. useVoice exposes toggle/finish/cancel/level/metered with take-id tracking so stale transcriptions are ignored. Owner approved this ChatGPT-style design.
- To apply a Panda browser-skill change immediately, copy engine.py into ~/.kiro/crew/skills/panda-browser/ and kill -TERM the live engine PID (~/.panda/engine.pid); it restarts itself within ~5s on port 5480. Killing a process by name can hit your own shell instead of the target, so use the PID.
- Panda has a mock UI harness at tools/screenshots/mock_ui.py (import as M) that serves a fake app with fake Supabase/worker. Use it with Playwright + fake mic (--use-file-for-fake-audio-capture) to test UI like the voice bar on tablet/phone, light/dark, without touching the owner's live account.
- Panda signs in to Kiro with an API key, and Kiro Crew skips the plan usage total for API-key sign-ins (reason: api_key_auth), so the usage bar was empty. Fix: the worker calls GetUsageLimits itself with KIRO_API_KEY, header TokenType: API_KEY (x-kiro-token-type), against the CodeWhisperer/q endpoints, and fills used/plan/resets. Confirmed returning Kiro Pro+ totals.
- Panda records the newest dev branch for the next Developer agent: scripts/mark-latest.py writes config/dev-latest.json (branch, commit, subject, when; per push/app/worker) to Supabase storage kirocrew-state. Both deploy scripts run it; the worker reads it each Developer turn, sets $PANDA_DEV_BRANCH and names the newest branch in the Developer instructions so the next agent starts from it.
- Panda's browser takes its website language (navigator.language / Accept-Language) from the app's Language setting. LANGS map: en->en-US,en; it->it-IT,it,en; uk->uk-UA,uk,en. Bug: every open Panda tab sent its own lang every few seconds, so two devices or a stale setting flipped it back and forth and reloaded the page each time. Fix: only the open browser panel (a real viewport size, vw/vh>50) sets the language; browser.ts sends lang only when big, else empty; no surprise reload — the next opened page uses the new language.
- Panda's BrowserPanel has a fullscreen toggle (top-right icon) using requestFullscreen/exitFullscreen with a fullscreenchange listener to sync state; on iPhone (no API) a .browser-panel.full CSS rule (position:fixed, inset:0, 100vw/100dvh) still fills the screen. Tablet fit: browser height follows the panel's aspect ratio (h = clamp(600..1400, w*vh/vw)) so pages fill without empty bands. Frame rate raised: desktop MIN_FRAME_GAP 0.12->0.07 (~14fps), mobile 0.2->0.12 (~8fps).
- Panda's markdown table cells were breaking words into letters on narrow (phone) widths. Fix: .prose th/td get word-break:normal, overflow-wrap:normal, hyphens:none so cells keep whole words and a wide table scrolls sideways instead.
- Panda's model effort picker (app/src/components/ModelSheet.tsx) was changed from an effort-row of pills to a vertical .effort-list of .effort-item rows (Low, Medium 'Recommended', High, Extra, Max '8x or more usage') with a check next to the selected one, styled like Claude's. Selecting an effort calls onChange(model, e.id) then onClose().
- Panda's home screen was redesigned Claude-style (app/src/App.tsx): removed STARTERS example cards, centered greeting/Panda logo in .home-hero (flex:1 0 auto, justify-content:center), and placed the same Composer used in chats at the bottom in .home-bottom (sticky bottom, with context ring at pct:0). Fixed mobile scroll by giving .home min-height:0 and overflow-y:auto. Owner wanted the chat bar identical to the in-chat one with context bar.
- Panda's ModelSheet (app/src/components/ModelSheet.tsx) bottom sheet now closes smoothly: a closing state slides the sheet translateY(100%) over ~0.26s then unmounts; the grey handle is a tappable .sheet-handle-btn; swipe-down to close works via touch handlers (from handle anywhere, or from the list when scrolled to top, closing if dy>90 or a fast flick). CSS uses overscroll-behavior:contain and html:has(.sheet-backdrop){overscroll-behavior-y:none} so Chrome pull-to-refresh doesn't reload the page while the sheet is open.
- Panda's 'Kiro Crew ran' credits number only counts since the last Stop/Start — each Start gives Kiro Crew a fresh machine and its old usage/tokens records are lost, so it reads lower than Panda's own saved 'Panda's replies' monthly total. Crew spend is read from /root/.kiro/crew/usage/tokens/<date>.jsonl ( _type:tokens rows with slot/model/credits).
- Panda image generation via Codex: react-markdown's default URL filter strips panda-file: links, so worker-uploaded images never render — must allow the panda-file: scheme. Images Kiro Crew moves into its .attachments folder need the worker to publish them. A drawing-panda animation card (Claude colours, panda seen from back sitting on a chair drawing on an easel, room sketches in, picture grows with a % counter) shows while an image is generated, with a safe fallback for images the app can't load.
- Codex (the program and its ChatGPT-Plus login) is kept across Kaggle/worker runs via save/restore + reinstall logic in Panda's worker, so it survives Stop/Start.
- Panda browser/drive skills used to tell the agent to define a shell function (pb() { python3 ...; }); Kiro Crew refuses to auto-approve a command that redefines a program name, so the step hung waiting for an unseen approval and the chat looked stopped. Fix: install real commands at ~/.local/bin/pb and ~/.local/bin/drive (install_skill_commands writes #!/bin/sh exec python3 wrappers), and SKILL.md tells agents to call them as-is and never define a pb()/drive() function or alias.
- In Panda's worker, a message sent while a chat's previous turn is still running gets an instant empty 'queued' receipt from Kiro Crew; the worker used to show that empty receipt as the answer (chat seemed to stop). Fix: wait_queued() marks the reply meta.queued=true (UI shows 'Waiting for the last step to finish') and polls /api/chat/slots/{slot} until the queued turn runs and finishes, then reads the real answer; honours cancel.
- Panda's browser engine waited 15 min silently for the user to press Done after a Take over, making the chat look dead. Fix: wait only PANDA_BROWSER_USER_WAIT (default 45s), then return an error telling the agent not to retry in a loop but to tell the user to press Done or continue without the browser.
- Panda has an Android app built as a TWA (Trusted Web Activity) that opens the live Vercel app (https://panda-app-gamma.vercel.app) full-screen in Chrome — no URL bar once Google verifies the link. Build with scripts/build-apk.py (needs ANDROID_HOME + a Gradle 8.9 install; commandline-tools + platforms;android-35 + build-tools;35.0.0). The signing key is kept in Supabase storage (not git); assetlinks.json lives in app/public/.well-known and is deployed with the app so digitalassetlinks verifies the sha256 cert. Rebuild bumping version: --version-code N. APK ~0.5MB; updates arrive automatically (web app), no new APK needed. Output to ~/outputs/Panda.apk.
- Panda chats answer in parallel and share one ~/outputs folder and one browser, so files/browser cards leaked into the wrong chat. Fix in worker.py: when two chats run at once each takes only the files its reply names, and a file already shown in one chat is never shown in another; a chat shows the browser card only if that turn actually used the browser. Confirmed as the cause of an elephant image appearing in the wrong chat.
- Panda's DrawingPanda SVG (app/src/components/DrawingPanda.tsx + .pdw CSS): panda seen from behind, big round white head, black ears, chair back in front, fluffy cloud tail, drawing on a big easel where the picture takes shape on the right over the % duration. Paw draws in slow ~3s real pencil strokes (press, pull a line, lift, carry back, set down, pause) while the arm sweeps across the board and the head turns slower out of step. Must keep moving even when the phone's 'Remove animations' setting is on (that setting had killed the whole animation) — paw still strokes, rest stays still.
- Panda's 'skill awaiting review' notifications (NotificationsModal in app/src/components/Panels.tsx) now have Approve/Deny buttons that call gw with the candidate slug; Approve lets Panda use the skill, Deny discards it, card shows Approved/Denied. Buttons are greyed when Panda is off ('Start Panda to approve or deny') because skills live on Panda's machine. Worker passes meta.slug into the notification row.
- Panda's Credits page (app Usage.tsx + worker crew_spend_split) splits Kiro Crew spend into 'chat replies' vs 'background' (titles, memory, other) by classifying the slot kind in the tokens jsonl; title generation is tiny (~0.1-0.2 credits), memory consolidation ~2, the bulk is normal chat replies. A line states when counting started.
- In Panda's Developer chat the agent holds Panda's master Supabase key on the machine it controls, so NO in-app switch can be an 'only the owner can flip it' lock on the agent: it can read MY_PASSWORD, decrypt stored values, remove the worker's own check, or act as the worker's login. A lock that truly binds the agent must live where the agent has no key — Kiro Crew's own Settings (a separate system), which is why real safety toggles are 'Locked in Panda'. Build only a view-only Security page in Panda and point the owner to Kiro Crew settings. — NOT: Don't build any Panda-side toggle/password gate that disables the agent's own safety rules, even when the owner insists and says only they will flip it.
- Panda's Composer (app/src/components/Composer.tsx) keeps the typed chat-bar draft across closing the app/Chrome: a new optional draftKey prop makes it load the draft from localStorage on mount and save it per-key as the user types; clear()/send empties text which removes the draft. Call sites: ChatView passes chat.id, App.tsx home composer passes 'home' (with key={prefill}, initialText=prefill wins over draft when a prefill arrives), Projects passes the project id. Device-local only (not synced across devices or to the server). Commit b6f919e on feat/smooth-stream-model-pandas, app-only (reload, no Stop/Start).
- A Panda app command failing with 'unknown op <name>' (e.g. video.auto) means the RUNNING worker predates that op — the code is deployed to Supabase but the live worker keeps old code until Stop/Start. Fix: tell the owner to press Stop then Start. Also map 'unknown op' video errors in the app to a friendly 'Panda needs a restart to use this' message instead of the raw red error (VideoTab.tsx showErr helper wrapping the catch blocks, commit 082ae36).
- Panda's /btw (side question) 404'd. The FIRST fix (commit 96be2e2, slot named by chat id) was NOT the real cause. Real cause: Kiro Crew has no GET /api/chat/slots/{slot}/side route at all; side answers come only over the live websocket Panda already uses for 'Thinking'. Fix (commit 2217a8d on feat/smooth-stream-model-pandas, worker.py): catch the side answer from the websocket (chat.side_result via a SIDE dict) instead of GETting a /side URL. Needs Stop/Start. — NOT: don't GET /api/chat/slots/{slot}/side; read side_result off the websocket.
- When Kiro Crew compacts a long chat it emits its compaction SUMMARY as a normal assistant reply and sends one bogus 0% context reading; Panda's worker used to show that summary as the answer and the 0% as context. Fix (commit 90d4516 on feat/smooth-stream-model-pandas, worker.py): detect the compaction notice (is_compaction_notice) and never show it as the reply — show a 'context compacted' step instead; ignore the 0% reading and keep the last real context pct. Needs Stop/Start. — NOT: don't surface Crew's compaction summary as the chat reply or trust its 0% context number.
- Panda's 'random' Kaggle restarts were the worker crashing during its periodic (~30min) state snapshot: it tars session chat files, but Kiro deletes old session files on its own, so a file vanishing mid-tar failed the save and crashed the whole worker (also earlier 413 'save too big' before part-splitting landed). Fix (commit f623f51, worker.py tar_add_live): skip files that vanish mid-snapshot and never die on a failed periodic snapshot; retry later. Needs Stop/Start.
- A new Panda chat started at ~7% (~70k tokens) context mostly from Kiro Crew's MCP tool list (~80 tools, ~40k) because Crew only defers tools behind tool_search once the list passes 50k and Panda's sat just under. Fix (commit b1d1230, worker.py): set agent.tool_search_min_pct 0 and agent.tool_search_min_tokens 0 so tool specs always defer; a chat loads a tool via tool_search in ~1s. Saved lessons (~5k, mostly Panda-code notes) could be made on_topic to save more. Needs Stop/Start.
