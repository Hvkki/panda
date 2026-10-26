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
