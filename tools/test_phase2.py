"""End-to-end test of Panda phases 2-3 against the live relay + Kaggle worker.

Acts exactly like the app: signs in as a normal user with the publishable key,
presses Start through the `session` edge function, chats, uploads files, makes a
project, stores an API key, sends commands, then presses Stop.

Env: SB_URL, SB_PUBLISHABLE, TESTER_EMAIL, TESTER_PASSWORD.  Steps by argv.
"""
import json
import os
import sys
import time

import requests

URL = os.environ["SB_URL"].rstrip("/")
ANON = os.environ["SB_PUBLISHABLE"]
R = {}


def login():
    r = requests.post(f"{URL}/auth/v1/token?grant_type=password", headers={"apikey": ANON},
                      json={"email": os.environ["TESTER_EMAIL"], "password": os.environ["TESTER_PASSWORD"]})
    r.raise_for_status()
    d = r.json()
    return {"apikey": ANON, "Authorization": f"Bearer {d['access_token']}", "Content-Type": "application/json"}, d["user"]["id"]


H, ME = login()


def rest(method, path, params=None, body=None, prefer=""):
    r = requests.request(method, f"{URL}/rest/v1/{path}", headers=dict(H, **({"Prefer": prefer} if prefer else {})),
                         params=params, json=body)
    if r.status_code >= 400:
        raise SystemExit(f"{method} {path}: {r.status_code} {r.text[:300]}")
    return r.json() if r.text else None


def latest_session():
    rows = rest("GET", "worker_sessions", {"order": "started_at.desc", "limit": "1", "select": "*"})
    return rows[0] if rows else None


def wait(pred, timeout, every=3, label=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(every)
    raise SystemExit(f"timeout waiting for {label}")


def ask(chat_id, text, meta=None, timeout=420):
    """Send a message like the app; answer approval cards the way the Approve button would."""
    m = rest("POST", "messages", body={"chat_id": chat_id, "owner": ME, "role": "user", "content": text,
                                       "status": "pending", "meta": meta or {}}, prefer="return=representation")[0]
    t0, approvals = time.time(), 0

    def done():
        nonlocal approvals
        for c in rest("GET", "messages", {"chat_id": f"eq.{chat_id}", "role": "eq.tool", "status": "eq.pending",
                                          "select": "id,decision"}):
            if not c["decision"]:
                rest("PATCH", "messages", {"id": f"eq.{c['id']}"}, {"decision": "approved"})
                approvals += 1
        rows = rest("GET", "messages", {"reply_to": f"eq.{m['id']}", "role": "eq.assistant", "select": "*"})
        return rows[0] if rows and rows[0]["status"] in ("done", "error") else None

    r = wait(done, timeout, every=2, label=f"reply to {text[:30]!r}")
    return {"status": r["status"], "secs": round(time.time() - t0), "approvals": approvals,
            "reply": r["content"], "files": r["meta"].get("files", []), "msg_id": r["id"]}


def new_chat(title, **extra):
    return rest("POST", "chats", body={"owner": ME, "title": title, **extra}, prefer="return=representation")[0]


def file_rows(msg_id):
    return rest("GET", "files", {"message_id": f"eq.{msg_id}", "select": "*"})


def fetch_file(path):
    r = requests.post(f"{URL}/storage/v1/object/sign/panda-files/{requests.utils.quote(path)}", headers=H, json={"expiresIn": 120})
    r.raise_for_status()
    data = requests.get(f"{URL}/storage/v1{r.json()['signedURL']}")
    return data.content


def command(op, args=None, timeout=60):
    c = rest("POST", "commands", body={"owner": ME, "op": op, "args": args or {}}, prefer="return=representation")[0]
    def done():
        row = rest("GET", "commands", {"id": f"eq.{c['id']}", "select": "status,result"})[0]
        return row if row["status"] in ("done", "error") else None
    return wait(done, timeout, every=1, label=f"command {op}")


step = sys.argv[1]

if step == "start":
    r = requests.post(f"{URL}/functions/v1/session", headers=H, json={"action": "start"})
    print(r.status_code, r.text[:300])
    if r.status_code != 200:
        raise SystemExit("start failed")
    s = wait(lambda: (lambda x: x if x and x["status"] in ("running", "error") else None)(latest_session()), 900, 10, "running")
    print(json.dumps({k: s[k] for k in ("status", "info", "kaggle_kernel")}))

elif step == "test":
    out = {}
    # 1. PDF
    c = new_chat("t-pdf")
    a = ask(c["id"], "Create a one-page PDF about red pandas (3 short sections) with the panda-docs skill. "
                     "Save it as Red-Pandas.pdf in ~/outputs. Be quick.")
    rows = file_rows(a["msg_id"])
    pdf = next((f for f in rows if f["name"].lower().endswith(".pdf")), None)
    out["pdf"] = {"turn": {k: a[k] for k in ("status", "secs", "approvals")}, "files": [f["name"] for f in rows],
                  "valid_pdf": bool(pdf and fetch_file(pdf["path"])[:5] == b"%PDF-"), "size": pdf and pdf["size"],
                  "reply_tail": a["reply"][-200:]}
    # 2. PPTX
    a = ask(c["id"], "Now make a 4-slide PowerPoint deck about the same topic with panda-docs, saved as Red-Pandas.pptx.")
    rows = file_rows(a["msg_id"])
    ppt = next((f for f in rows if f["name"].lower().endswith(".pptx")), None)
    out["pptx"] = {"turn": {k: a[k] for k in ("status", "secs", "approvals")}, "files": [f["name"] for f in rows],
                   "valid_zip": bool(ppt and fetch_file(ppt["path"])[:2] == b"PK"), "reply_tail": a["reply"][-160:]}
    # 3. image search
    c2 = new_chat("t-images")
    a = ask(c2["id"], "Find 3 photos of Tuscany with the image-search skill and show them in your answer.")
    out["images"] = {"turn": {k: a[k] for k in ("status", "secs", "approvals")},
                     "image_markdown": a["reply"].count("!["), "reply_head": a["reply"][:300]}
    print(json.dumps(out, indent=1))

elif step == "test2":
    out = {}
    # 4. attachment
    path = f"{ME}/uploads/{int(time.time())}-secret.txt"
    up = requests.post(f"{URL}/storage/v1/object/panda-files/{path}", headers={**H, "Content-Type": "text/plain"},
                       data=b"The secret word is MANGOSTEEN-42.")
    out["upload_status"] = up.status_code
    c = new_chat("t-attach")
    a = ask(c["id"], "What is the secret word in my attached file? Answer with just the word.",
            meta={"attachments": [{"path": path, "name": "secret.txt", "mime": "text/plain", "size": 33}]})
    out["attachment"] = {"turn": {k: a[k] for k in ("status", "secs", "approvals")}, "found": "MANGOSTEEN-42" in a["reply"], "reply": a["reply"][:200]}
    # 5. project
    p = rest("POST", "projects", body={"owner": ME, "name": "Brand test", "description": "test",
                                      "instructions": "Always finish every reply with the exact token PANDA-OK."},
             prefer="return=representation")[0]
    ppath = f"{ME}/projects/{p['id']}/{int(time.time())}-brand.txt"
    requests.post(f"{URL}/storage/v1/object/panda-files/{ppath}", headers={**H, "Content-Type": "text/plain"},
                  data=b"Brand guide: our brand color is TEAL and our mascot is a heron.")
    rest("POST", "files", body={"owner": ME, "kind": "project", "name": "brand.txt", "path": ppath, "mime": "text/plain",
                                "size": 60, "project_id": p["id"]})
    c = new_chat("t-project", project_id=p["id"])
    a = ask(c["id"], "According to the project files, what is our brand color and mascot?")
    out["project"] = {"turn": {k: a[k] for k in ("status", "secs", "approvals")},
                      "knows_files": "teal" in a["reply"].lower() and "heron" in a["reply"].lower(),
                      "follows_instructions": "PANDA-OK" in a["reply"], "reply": a["reply"][:300]}
    # 6. api key vault -> agent
    requests.post(f"{URL}/rest/v1/rpc/set_secret", headers=H, json={"p_name": "TEST_KEY", "p_value": "hunter2-xyz", "p_note": "test"})
    r = command("secrets.sync")
    out["vault_sync"] = r["result"]
    c = new_chat("t-vault")
    a = ask(c["id"], "Using bash, run: source ~/.panda/secrets.env && echo -n ${#TEST_KEY}. Tell me only the number. Never show the key.")
    out["vault"] = {"turn": {k: a[k] for k in ("status", "secs", "approvals")}, "reply": a["reply"][:200],
                    "key_leaked": "hunter2-xyz" in a["reply"]}
    requests.post(f"{URL}/rest/v1/rpc/delete_secret", headers=H, json={"p_name": "TEST_KEY"})
    command("secrets.sync")
    # 7. Customize commands
    cmds = {}
    for name, path in (("skills", "/api/skills"), ("mcp", "/api/mcp"), ("crons", "/api/crons")):
        try:
            r = command("gw", {"method": "GET", "path": path})
            res = r["result"]
            items = res if isinstance(res, list) else next((v for v in res.values() if isinstance(v, list)), []) if isinstance(res, dict) else []
            cmds[name] = {"status": r["status"], "count": len(items), "keys": sorted(items[0].keys())[:12] if items and isinstance(items[0], dict) else None,
                          "has_panda_docs": "panda-docs" in json.dumps(res)}
        except SystemExit as e:
            cmds[name] = str(e)
    out["commands"] = cmds
    print(json.dumps(out, indent=1))

elif step == "stop":
    s = latest_session()
    rest("PATCH", "worker_sessions", {"id": f"eq.{s['id']}"}, {"stop_requested": True})
    s = wait(lambda: (lambda x: x if x["status"] in ("stopped", "error") else None)(latest_session()), 600, 10, "stopped")
    print(json.dumps({k: s[k] for k in ("status", "last_snapshot", "info")}))
