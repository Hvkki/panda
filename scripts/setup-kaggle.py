#!/usr/bin/env python3
"""One-time (and key-rotation) setup: put the worker's private config into Kaggle.

Creates or updates two private Kaggle datasets:
  <user>/panda-secret    worker.json: your Claude token, the app password, optional tunnel settings
  <user>/panda-endpoint  where a quick tunnel publishes its URL (skipped when CLOUDFLARED_TOKEN is set)

Claude subscription login (do this once, on your own computer, where you can open a browser):
    claude setup-token          # prints a long-lived token (sk-ant-oat01-...); uses your Pro/Max plan
Then:
    export CLAUDE_CODE_OAUTH_TOKEN=...   PANDA_PASSWORD=...   KAGGLE_API_TOKEN=...   KAGGLE_USERNAME=...
    # optional: CLOUDFLARED_TOKEN=... (named tunnel, fixed URL)
    python3 scripts/setup-kaggle.py

Needs the `kaggle` CLI (pip install kaggle). Nothing secret is printed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def need(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"{name} is not set")
    return v


def kaggle(*args, cwd=None):
    r = subprocess.run(["kaggle", *args], capture_output=True, text=True, cwd=cwd)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def push(user, slug, files, message):
    d = Path(tempfile.mkdtemp(prefix="panda-ds-"))
    try:
        for name, text in files.items():
            (d / name).write_text(text)
        (d / "dataset-metadata.json").write_text(json.dumps(
            {"title": slug, "id": f"{user}/{slug}", "licenses": [{"name": "other"}], "isPrivate": True}))
        ok, out = kaggle("datasets", "version", "-p", str(d), "-m", message)
        if not ok:  # first run: the dataset does not exist yet
            ok, out = kaggle("datasets", "create", "-p", str(d))
        print(f"{user}/{slug}:", "ok" if ok else f"FAILED ({out[-300:]})")
        if not ok:
            sys.exit(1)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    user = need("KAGGLE_USERNAME")
    need("KAGGLE_API_TOKEN")
    cfg = {
        "claude_oauth_token": need("CLAUDE_CODE_OAUTH_TOKEN"),
        "app_password": need("PANDA_PASSWORD"),
        "kaggle_api_token": os.environ["KAGGLE_API_TOKEN"],
        "kaggle_username": user,
    }
    if os.environ.get("CLOUDFLARED_TOKEN"):
        cfg["cloudflared_token"] = os.environ["CLOUDFLARED_TOKEN"]
    else:
        cfg["endpoint_dataset"] = f"{user}/panda-endpoint"
        push(user, "panda-endpoint", {"endpoint.json": json.dumps({"url": ""})}, "init")
    push(user, "panda-secret", {"worker.json": json.dumps(cfg)}, "update worker config")
    print("Done. Press Start in the app (or run the notebook) to use the new values.")


if __name__ == "__main__":
    main()
