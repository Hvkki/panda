#!/usr/bin/env python3
"""Record which branch and commit hold the newest Panda work, so the next Developer agent starts there.

Writes config/dev-latest.json in Supabase Storage (bucket kirocrew-state). The worker reads it at the
start of every Developer turn: it sets $PANDA_DEV_BRANCH and names the branch in the instructions.

    python3 scripts/mark-latest.py push     # after a git push
    python3 scripts/mark-latest.py app      # run by deploy-app.py after a production deploy
    python3 scripts/mark-latest.py worker   # run by deploy-worker.sh after an upload

Needs SUPABASE_URL and SUPABASE_SECRET_KEY. Fails softly: a missing record never blocks a deploy.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBJECT = "config/dev-latest.json"
PARTS = ("push", "app", "worker")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def call(method: str, url: str, key: str, body: bytes | None = None, extra: dict | None = None):
    headers = {"apikey": key, "Authorization": f"Bearer {key}", **(extra or {})}
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main() -> int:
    part = sys.argv[1] if len(sys.argv) > 1 else "push"
    if part not in PARTS:
        print(f"usage: mark-latest.py [{'|'.join(PARTS)}]", file=sys.stderr)
        return 2
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        print("mark-latest: SUPABASE_URL / SUPABASE_SECRET_KEY not set, not recorded", file=sys.stderr)
        return 0
    base = f"{url}/storage/v1/object"
    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        entry = {"branch": branch, "commit": git("rev-parse", "--short", "HEAD"),
                 "subject": git("log", "-1", "--format=%s")[:120],
                 "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "part": part}
        try:
            record = json.loads(call("GET", f"{base}/authenticated/kirocrew-state/{OBJECT}", key) or b"{}")
        except (urllib.error.HTTPError, ValueError):
            record = {}
        record = record if isinstance(record, dict) else {}
        record["latest"] = entry
        record[part] = entry
        call("POST", f"{base}/kirocrew-state/{OBJECT}", key, json.dumps(record, indent=1).encode(),
             {"x-upsert": "true", "Content-Type": "application/json"})
        print(f"mark-latest: {part} -> {entry['branch']} @ {entry['commit']}")
    except Exception as exc:  # never block a deploy over the record
        print(f"mark-latest: not recorded ({type(exc).__name__})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
