#!/usr/bin/env python3
"""Build the web app and deploy it to Vercel through the REST API.

The Vercel CLI rejected the account token, so this talks to the API directly.
Needs VERCEL_TOKEN. The deployment contains the built app, the server functions in app/api
(Start / Stop / Status) and worker/worker.py (served at /worker.py; Start pushes it to Kaggle).

    python3 scripts/deploy-app.py              # build app/, then deploy to production
    python3 scripts/deploy-app.py --no-build   # deploy the existing app/dist
    python3 scripts/deploy-app.py --set-env    # first set the project's environment variables from
                                               # the current environment (see app/api/_lib.js)
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
API = "https://api.vercel.com"


def call(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=180).read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"Vercel answered {e.code}: {e.read().decode()[:300]}")


ENV_NAMES = ["PANDA_PASSWORD", "KAGGLE_API_TOKEN", "KAGGLE_KERNEL", "KAGGLE_DATASET",
             "PANDA_API_URL", "ENDPOINT_DATASET"]


def set_env(token):
    for name in ENV_NAMES:
        if os.environ.get(name):
            call("POST", f"{API}/v10/projects/panda-app/env?upsert=true", token,
                 {"key": name, "value": os.environ[name], "type": "encrypted", "target": ["production"]})
            print("set", name)


def main():
    token = os.environ.get("VERCEL_TOKEN")
    if not token:
        sys.exit("VERCEL_TOKEN is not set (load panda-api-keys.env first)")
    if "--set-env" in sys.argv:
        set_env(token)
    if "--no-build" not in sys.argv:
        subprocess.run(["npm", "run", "build"], cwd=APP, check=True)
    dist = APP / "dist"
    files = [{"file": p.relative_to(dist).as_posix(), "data": base64.b64encode(p.read_bytes()).decode(),
              "encoding": "base64"} for p in sorted(dist.rglob("*")) if p.is_file()]
    for p in sorted(list((APP / "api").glob("*.js")) + [APP / "api" / "package.json"]):
        files.append({"file": f"api/{p.name}", "data": base64.b64encode(p.read_bytes()).decode(), "encoding": "base64"})
    files.append({"file": "worker.py", "data": base64.b64encode((ROOT / "worker" / "worker.py").read_bytes()).decode(),
                  "encoding": "base64"})
    files.append({"file": "vercel.json", "data": json.dumps({"cleanUrls": True, "headers": [
        {"source": "/sw.js", "headers": [{"key": "Cache-Control", "value": "no-cache"}]},
        {"source": "/(.*)", "headers": [{"key": "X-Content-Type-Options", "value": "nosniff"},
                                       {"key": "Referrer-Policy", "value": "strict-origin-when-cross-origin"}]}]})})
    body = {"name": "panda-app", "files": files, "target": "production",
            "projectSettings": {"framework": None, "buildCommand": None, "outputDirectory": None,
                                "installCommand": None}}
    dep = call("POST", f"{API}/v13/deployments?skipAutoDetectionConfirmation=1", token, body)
    for _ in range(80):
        s = call("GET", f"{API}/v13/deployments/{dep['id']}", token)
        if s.get("readyState") in ("READY", "ERROR", "CANCELED"):
            print(s["readyState"], *(s.get("alias") or [s.get("url")]))
            return
        time.sleep(3)
    sys.exit("timed out waiting for the deployment")


if __name__ == "__main__":
    main()
