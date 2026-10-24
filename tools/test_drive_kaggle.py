"""Live Drive test on the Kaggle worker: start, auto-offload of a big output, drive command, project-file fix, stop."""
import json
import os
import sys
import time

import requests

URL = os.environ["SB_URL"].rstrip("/")
ANON = os.environ["SB_PUBLISHABLE"]
r = requests.post(f"{URL}/auth/v1/token?grant_type=password", headers={"apikey": ANON},
                  json={"email": os.environ["TESTER_EMAIL"], "password": os.environ["TESTER_PASSWORD"]})
r.raise_for_status()
H = {"apikey": ANON, "Authorization": f"Bearer {r.json()['access_token']}", "Content-Type": "application/json"}
ME = r.json()["user"]["id"]


def rest(method, path, params=None, body=None, prefer=""):
    x = requests.request(method, f"{URL}/rest/v1/{path}", headers=dict(H, **({"Prefer": prefer} if prefer else {})), params=params, json=body)
    if x.status_code >= 400:
        raise SystemExit(f"{method} {path}: {x.status_code} {x.text[:300]}")
    return x.json() if x.text else None


def wait(pred, timeout, every=3, label=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(every)
    raise SystemExit(f"timeout: {label}")


def latest():
    return (rest("GET", "worker_sessions", {"order": "started_at.desc", "limit": "1", "select": "*"}) or [None])[0]


def ask(chat_id, text, timeout=900):
    m = rest("POST", "messages", body={"chat_id": chat_id, "owner": ME, "role": "user", "content": text, "status": "pending"},
             prefer="return=representation")[0]
    t0 = time.time()

    def done():
        for c in rest("GET", "messages", {"chat_id": f"eq.{chat_id}", "role": "eq.tool", "status": "eq.pending", "select": "id,decision"}):
            if not c["decision"]:
                rest("PATCH", "messages", {"id": f"eq.{c['id']}"}, {"decision": "approved"})
        rows = rest("GET", "messages", {"reply_to": f"eq.{m['id']}", "role": "eq.assistant", "select": "*"})
        return rows[0] if rows and rows[0]["status"] in ("done", "error") else None

    a = wait(done, timeout, 2, "reply")
    return {"status": a["status"], "secs": round(time.time() - t0), "reply": a["content"], "id": a["id"]}


step = sys.argv[1]
if step == "start":
    x = requests.post(f"{URL}/functions/v1/session", headers=H, json={"action": "start"})
    print(x.status_code)
    s = wait(lambda: (lambda v: v if v and v["status"] in ("running", "error") else None)(latest()), 900, 10, "running")
    print(json.dumps({"status": s["status"], "info": s["info"]}))
elif step == "test":
    out = {}
    c = rest("POST", "chats", body={"owner": ME, "title": "t-drive"}, prefer="return=representation")[0]
    a = ask(c["id"], "Run in bash: head -c 62914560 /dev/urandom > ~/outputs/big-dataset.bin && ls -la ~/outputs/big-dataset.bin . "
                     "Then just tell me the file size. Do not upload it yourself.")
    files = rest("GET", "files", {"message_id": f"eq.{a['id']}", "select": "name,path,size"})
    out["auto_offload"] = {"status": a["status"], "secs": a["secs"], "files": files,
                           "on_drive": any(f["path"].startswith("drive:") for f in files)}
    a = ask(c["id"], "Use the panda-drive skill: create a small text file notes.txt containing 'drive works', "
                     "upload it with `drive put` into folder tests, then run `drive ls tests` and `drive usage`. Show me the output briefly.")
    out["drive_cmd"] = {"status": a["status"], "secs": a["secs"], "mentions_notes": "notes.txt" in a["reply"],
                        "has_link": "drive.google.com" in a["reply"], "reply": a["reply"][:400]}
    # project-file fix (was failing on compressed downloads)
    p = rest("POST", "projects", body={"owner": ME, "name": "Brand test 2", "instructions": ""}, prefer="return=representation")[0]
    path = f"{ME}/projects/{p['id']}/{int(time.time())}-brand.txt"
    requests.post(f"{URL}/storage/v1/object/panda-files/{path}", headers={**H, "Content-Type": "text/plain"},
                  data=b"Brand guide: our brand color is TEAL and our mascot is a heron.")
    rest("POST", "files", body={"owner": ME, "kind": "project", "name": "brand.txt", "path": path, "mime": "text/plain", "size": 60, "project_id": p["id"]})
    c2 = rest("POST", "chats", body={"owner": ME, "title": "t-proj2", "project_id": p["id"]}, prefer="return=representation")[0]
    a = ask(c2["id"], "According to the project files, what is our brand color and mascot? One line.")
    out["project_files_fixed"] = {"ok": "teal" in a["reply"].lower() and "heron" in a["reply"].lower(), "reply": a["reply"][:200]}
    print(json.dumps(out, indent=1))
elif step == "stop":
    s = latest()
    rest("PATCH", "worker_sessions", {"id": f"eq.{s['id']}"}, {"stop_requested": True})
    s = wait(lambda: (lambda v: v if v["status"] in ("stopped", "error") else None)(latest()), 600, 10, "stopped")
    print(json.dumps({"status": s["status"], "info": s["info"]}))
