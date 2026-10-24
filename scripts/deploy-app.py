#!/usr/bin/env python3
"""Build the web app and deploy it to Vercel through the REST API.

The Vercel CLI rejected the account token, so this talks to the API directly.
Needs VERCEL_TOKEN (load panda-api-keys.env first).

    python3 scripts/deploy-app.py              # build app/, then deploy to production
    python3 scripts/deploy-app.py --no-build   # deploy the existing app/dist
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


def main():
    token = os.environ.get("VERCEL_TOKEN")
    if not token:
        sys.exit("VERCEL_TOKEN is not set (load panda-api-keys.env first)")
    if "--no-build" not in sys.argv:
        subprocess.run(["npm", "run", "build"], cwd=APP, check=True)
    dist = APP / "dist"
    files = [{"file": p.relative_to(dist).as_posix(), "data": base64.b64encode(p.read_bytes()).decode(),
              "encoding": "base64"} for p in sorted(dist.rglob("*")) if p.is_file()]
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
