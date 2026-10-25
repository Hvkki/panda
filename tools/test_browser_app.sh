#!/usr/bin/env bash
# Engine <-> app protocol check: starts the browser engine on a random private Realtime topic and
# drives it with the app's own supabase-js client (tools/test_browser_app_protocol.mjs).
# Uses only the publishable key. Needs Python deps (playwright, aiohttp, websockets) and app/node_modules.
#   SB_URL=... SB_PUBLISHABLE=... PANDA_CHROMIUM=... tools/test_browser_app.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SB_URL:?}" "${SB_PUBLISHABLE:?}"
HOME_DIR="$(mktemp -d)"
TOPIC="pbr-apptest-$(python3 -c 'import secrets;print(secrets.token_hex(8))')"
mkdir -p "$HOME_DIR/.panda"
printf '{"supabase_url": "%s", "anon": "%s", "topic": "%s"}' "$SB_URL" "$SB_PUBLISHABLE" "$TOPIC" > "$HOME_DIR/.panda/browser.json"
export HOME="$HOME_DIR" PANDA_BROWSER_PORT=5484 PANDA_OUTPUTS="$HOME_DIR/outputs"
python3 worker/skills/panda-browser/engine.py > "$HOME_DIR/engine.log" 2>&1 &
ENG=$!
trap 'kill $ENG 2>/dev/null; wait $ENG 2>/dev/null; rm -rf "$HOME_DIR"' EXIT
for _ in $(seq 1 60); do curl -s http://127.0.0.1:5484/health >/dev/null && break; sleep 0.5; done
python3 worker/skills/panda-browser/pb.py open https://example.com --no-snap >/dev/null
sleep 2
(cd app && node ../tools/test_browser_app_protocol.mjs "$SB_URL" "$SB_PUBLISHABLE" "$TOPIC")
grep -E "realtime joined|input failed" "$HOME_DIR/engine.log" | head -5
