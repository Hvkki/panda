#!/usr/bin/env python3
"""Load saved tokens into a running Panda worker.

    python3 scripts/load-tokens.py panda-tokens.txt https://your-panda.vercel.app

Reads a KEY=value file with sections ("# ── Developer …", "# ── Chat …", as exported from the old
Supabase setup) and sends them to the worker:
  * Developer section → Developer → Env variables (only the Developer chat sees them)
  * Chat section      → API keys every chat can use
  * GITHUB_TOKEN      → Settings → GitHub (git and gh signed in for every chat). When the file has two
                        different GitHub tokens, the one that reaches the most private repos wins.
Asks for the app password (not echoed). Start the worker in the app first.
"""
import getpass
import json
import sys
import urllib.error
import urllib.request

SKIP_DEV = {"KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KERNEL", "KAGGLE_DATASET"}  # old Kiro setup, not Panda's


def parse(path):
    out, sec = {"dev": {}, "chat": {}}, None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("# ──"):
            sec = "dev" if "Developer" in line else "chat" if "Chat" in line else None
        elif sec and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[sec][k.strip()] = json.loads(v) if v.startswith('"') else v
    return out


def call(base, pw, path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST",
                                 headers={"Authorization": f"Bearer {pw}", "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=120).read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"{path}: HTTP {e.code} {e.read().decode()[:200]}")


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    tokens, app = parse(sys.argv[1]), sys.argv[2].rstrip("/")
    pw = getpass.getpass("Panda password: ")
    st = call(app, pw, "/api/session", {"action": "status"})
    if st.get("state") != "on":
        sys.exit("The worker is not online. Press Start in the app, wait until it says Online, then run this again.")
    w = st["url"].rstrip("/")

    gh = {v for v in (tokens["dev"].get("GITHUB_TOKEN"), tokens["chat"].get("GITHUB_TOKEN")) if v}
    dev = {k: v for k, v in tokens["dev"].items() if k not in SKIP_DEV}
    print("Developer variables:", len(call(w, pw, "/devenv", {"set": dev}).get("names", [])), "saved")
    print("Chat API keys:", len(call(w, pw, "/env", {"set": tokens["chat"]}).get("names", [])), "saved")

    best = None
    for t in gh:
        chk = call(w, pw, "/github", {"token": t})
        print(f"GitHub token …{t[-4:]}: {chk.get('login')} · {chk.get('repos')} repos, {chk.get('private')} private")
        if chk.get("connected") and (best is None or chk.get("private", 0) > best[1].get("private", 0)):
            best = (t, chk)
    if best:
        chk = call(w, pw, "/github", {"token": best[0]})
        print(f"Using …{best[0][-4:]} for GitHub.")
        for h in chk.get("hints") or []:
            print("  !", h)


if __name__ == "__main__":
    main()
