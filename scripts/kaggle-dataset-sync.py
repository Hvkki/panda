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
KIRO_USAGE_HOSTS = ("https://codewhisperer.us-east-1.amazonaws.com", "https://q.eu-central-1.amazonaws.com")


def kiro_credits(key: str):
    """{plan, used, limit, overage} for a Kiro API key (Kiro's GetUsageLimits), or None if unreadable.

    The key itself is never printed. On 29 October a sync wrote a Kiro key whose month was used up
    (10 000 of 10 000) into worker.json: remote helpers then failed with "The monthly usage limit has
    been reached", and the next Start would have put every chat on it too.
    """
    import urllib.request
    headers = {"Authorization": f"Bearer {key}", "TokenType": "API_KEY",
               "Content-Type": "application/x-amz-json-1.0",
               "X-Amz-Target": "AmazonCodeWhispererService.GetUsageLimits",
               "User-Agent": "AmazonQ-For-CLI/1.24.0 ua/2.0 os/linux lang/rust Panda"}
    body = json.dumps({"origin": "AI_EDITOR", "resourceType": "AGENTIC_REQUEST"}).encode()
    for host in KIRO_USAGE_HOSTS:
        try:
            with urllib.request.urlopen(urllib.request.Request(host, data=body, headers=headers, method="POST"),
                                        timeout=20) as r:
                data = json.loads(r.read())
        except Exception:
            continue
        rows = [b for b in data.get("usageBreakdownList") or [] if isinstance(b, dict)]
        b = next((x for x in rows if x.get("resourceType") == "CREDIT"), rows[0] if len(rows) == 1 else None)
        if not b:
            return None
        return {"plan": str((data.get("subscriptionInfo") or {}).get("subscriptionTitle") or ""),
                "used": float(b.get("currentUsageWithPrecision") or b.get("currentUsage") or 0),
                "limit": float(b.get("usageLimitWithPrecision") or b.get("usageLimit") or 0),
                "overage": (data.get("overageConfiguration") or {}).get("overageStatus") == "ENABLED"}
    return None


def check_kiro_key(key: str, force: bool) -> None:
    """Refuse (unless --force) to write a Kiro key with no credits left and no overage."""
    c = kiro_credits(key)
    if c is None:
        print("kiro_api_key: could not read its credits (network?); not checked")
        return
    left = c["limit"] - c["used"]
    print(f"kiro_api_key: {c['plan'].title() or 'plan ?'}, {left:,.0f} of {c['limit']:,.0f} credits left"
          + (", overage on" if c["overage"] else ""))
    if c["limit"] and left <= 0 and not c["overage"] and not force:
        raise SystemExit("refusing: this KIRO_API_KEY has no credits left this month and overage is off, so every "
                         "chat and helper would fail with 'monthly usage limit reached'. Put the key that has credits "
                         "in the Developer tab's KIRO_API_KEY, or pass --force.")


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
    ap.add_argument("--force", action="store_true", help="write a Kiro key even when it has no credits left")
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
        check_kiro_key(want["kiro_api_key"], args.force)
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
