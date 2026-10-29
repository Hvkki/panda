#!/usr/bin/env python3
"""Write the worker's private values into the Kaggle dataset that the notebook reads.

The worker reads worker.json from the attached dataset (KAGGLE_DATASET). After you rotate a key,
load panda-api-keys.env and run:

    python3 scripts/kaggle-dataset-sync.py --dry-run   # lists which fields differ (names only)
    python3 scripts/kaggle-dataset-sync.py             # uploads a new dataset version

Then Stop and Start the worker. It reads the dataset when it starts.
Needs the `kaggle` CLI on PATH (pip install kaggle) and KAGGLE_API_TOKEN.
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DATASET = os.environ.get("KAGGLE_DATASET", "vladraf/kirocrew-test-secret")


def wanted(env):
    """The worker.json fields this script owns, built from the environment."""
    return {
        "supabase_url": env["SUPABASE_URL"],
        "supabase_publishable_key": env["SUPABASE_PUBLISHABLE_KEY"],
        "owner_id": env["PANDA_OWNER_USER_ID"],
        "worker_email": env["PANDA_WORKER_EMAIL"],
        "worker_password": env["PANDA_WORKER_PASSWORD"],
        "kiro_api_key": env["KIRO_API_KEY"],
        "vapid_private": env["VAPID_PRIVATE_KEY"],
        "vapid_sub": env["VAPID_SUBJECT"],
        "browser_key": env["BROWSER_FERNET_KEY"],
        # Kaggle API creds so the worker can use a Kaggle dataset as elastic backup storage
        # (blob_save_kaggle / blob_load). Same account that runs the notebooks.
        "kaggle_username": env["KAGGLE_USERNAME"],
        "kaggle_key": env["KAGGLE_API_TOKEN"],
        "drive": {
            "client_id": env["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": env["GOOGLE_OAUTH_CLIENT_SECRET"],
            "refresh_token": env["GOOGLE_DRIVE_REFRESH_TOKEN"],
            "folder_id": env["GOOGLE_DRIVE_FOLDER_ID"],
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="show differing fields, upload nothing")
    args = ap.parse_args()
    os.environ.setdefault("KAGGLE_CONFIG_DIR", os.path.join(tempfile.gettempdir(), "kaggle-config"))
    os.makedirs(os.environ["KAGGLE_CONFIG_DIR"], exist_ok=True)
    try:
        want = wanted(os.environ)
    except KeyError as missing:
        raise SystemExit(f"missing {missing}: load panda-api-keys.env first")

    tmp = Path(tempfile.mkdtemp(prefix="panda-dataset-"))
    try:
        subprocess.run(["kaggle", "datasets", "download", DATASET, "-p", str(tmp), "--unzip", "-q"], check=True)
        path = tmp / "worker.json"
        current = json.loads(path.read_text())
        changed = [k for k in want if want[k] != current.get(k)]
        print("fields that differ:", changed or "none")
        if args.dry_run:
            print("dry run: nothing uploaded")
            return
        if not changed:
            print("already up to date: nothing uploaded")
            return
        path.write_text(json.dumps({**current, **want}))
        (tmp / "dataset-metadata.json").write_text(json.dumps(
            {"title": DATASET.split("/")[1], "id": DATASET, "licenses": [{"name": "other"}]}))
        subprocess.run(["kaggle", "datasets", "version", "-p", str(tmp),
                        "-m", "sync from panda-api-keys.env", "--delete-old-versions"], check=True)
        print("uploaded a new dataset version; Stop and Start the worker to use it")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
