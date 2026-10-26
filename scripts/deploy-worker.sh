#!/usr/bin/env bash
# Upload the worker, its system prompt and its skills to Supabase Storage (bucket kirocrew-state).
# Needs SUPABASE_URL and SUPABASE_SECRET_KEY: load panda-api-keys.env first.
# A worker that is already running keeps the code it started with: press Stop, then Start.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SUPABASE_URL:?load panda-api-keys.env first}"
: "${SUPABASE_SECRET_KEY:?load panda-api-keys.env first}"
BUCKET="$SUPABASE_URL/storage/v1/object/kirocrew-state"

up() {  # up <object path> <local file> <content type>
  curl -fsS -o /dev/null -w "$1 -> HTTP %{http_code}\n" -X POST "$BUCKET/$1" \
    -H "apikey: $SUPABASE_SECRET_KEY" -H "Authorization: Bearer $SUPABASE_SECRET_KEY" \
    -H "x-upsert: true" -H "Content-Type: $3" --data-binary @"$2"
}

python3 -m py_compile worker/worker.py
TAR="$(mktemp)"
tar -czf "$TAR" --exclude=__pycache__ -C worker/skills .
up code/worker.py worker/worker.py text/x-python
up config/prompt.md worker/prompt.md text/markdown
up code/skills.tar.gz "$TAR" application/gzip
rm -f "$TAR"
python3 scripts/mark-latest.py worker || true  # tell the next Developer agent which code is live
