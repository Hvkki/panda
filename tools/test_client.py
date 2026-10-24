"""Phone stand-in: drives the relay exactly as the Android app will.

Signs in as a normal (non-admin) user with the publishable key and only uses
what row-level security allows that user. Steps are chosen with argv:
  request              create a 'requested' worker session (the Start button)
  wait-running         wait until the worker reports running
  chat                 run the chat checks (reply, continuity, cron -> notification)
  stop                 set stop_requested (the Stop button) and wait for 'stopped'
Env: SB_URL, SB_PUBLISHABLE, TESTER_EMAIL, TESTER_PASSWORD.
"""
import json
import os
import sys
import time

import requests

URL = os.environ["SB_URL"].rstrip("/")
ANON = os.environ["SB_PUBLISHABLE"]


def login() -> dict:
    r = requests.post(f"{URL}/auth/v1/token?grant_type=password", headers={"apikey": ANON},
                      json={"email": os.environ["TESTER_EMAIL"], "password": os.environ["TESTER_PASSWORD"]})
    r.raise_for_status()
    d = r.json()
    return {"apikey": ANON, "Authorization": f"Bearer {d['access_token']}",
            "Content-Type": "application/json"}, d["user"]["id"]


H, ME = login()


def rest(method, path, params=None, body=None, prefer=""):
    h = dict(H, **({"Prefer": prefer} if prefer else {}))
    r = requests.request(method, f"{URL}/rest/v1/{path}", headers=h, params=params, json=body)
    if r.status_code >= 400:
        raise SystemExit(f"{method} {path}: {r.status_code} {r.text[:300]}")
    return r.json() if r.text else None


def latest_session():
    rows = rest("GET", "worker_sessions", {"order": "started_at.desc", "limit": "1", "select": "*"})
    return rows[0] if rows else None


def wait(pred, timeout, every=5, label=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(every)
    raise SystemExit(f"timeout waiting for {label}")


def ask(chat_id, text, timeout=300):
    m = rest("POST", "messages", body={"chat_id": chat_id, "owner": ME, "role": "user",
                                        "content": text, "status": "pending"},
             prefer="return=representation")[0]
    t0 = time.time()
    seen_partial = False

    def done():
        nonlocal seen_partial
        rows = rest("GET", "messages", {"reply_to": f"eq.{m['id']}", "select": "*"})
        if rows and rows[0]["status"] == "streaming" and rows[0]["content"]:
            seen_partial = True
        return rows[0] if rows and rows[0]["status"] in ("done", "error") else None

    r = wait(done, timeout, every=1, label="reply")
    return {"status": r["status"], "secs": round(time.time() - t0, 1), "streamed": seen_partial,
            "reply": r["content"][:600], "meta": r.get("meta")}


step = sys.argv[1]
if step == "request":
    s = rest("POST", "worker_sessions", body={"owner": ME, "status": "requested"},
             prefer="return=representation")[0]
    print(json.dumps({"session": s["id"], "status": s["status"]}))
elif step == "wait-running":
    s = wait(lambda: (lambda x: x if x and x["status"] in ("running", "error") else None)(latest_session()),
             1500, every=10, label="running")
    print(json.dumps({k: s[k] for k in ("status", "info", "heartbeat_at")}))
elif step == "chat":
    chat = rest("POST", "chats", body={"owner": ME, "title": "relay test"},
                prefer="return=representation")[0]
    out = {"chat": chat["id"]}
    out["q1"] = ask(chat["id"], "Write one catchy LinkedIn hook (max 20 words) for a tool that "
                                "summarizes research papers. Reply with only the hook.")
    out["q2"] = ask(chat["id"], "What did I ask you for in my previous message? One short sentence.")
    out["q3"] = ask(chat["id"], "Schedule a one-time job that fires in 2 minutes from now and sends me "
                                "a notification with exactly the text: CRON TEST OK. Confirm briefly.",
                    timeout=400)
    t0 = time.time()
    try:
        n = wait(lambda: [x for x in rest("GET", "notifications", {"select": "*", "order": "id.desc"})
                          if "CRON TEST OK" in (x["text"] or "")], 420, every=10, label="cron notification")
        out["cron_notification"] = {"after_secs": round(time.time() - t0), "text": n[0]["text"][:300]}
    except SystemExit as e:
        out["cron_notification"] = str(e)
        out["all_notifications"] = [x["text"][:200] for x in rest("GET", "notifications", {"select": "*"})]
    print(json.dumps(out, indent=2))
elif step == "continue":
    chat = rest("GET", "chats", {"order": "created_at.desc", "limit": "1", "select": "*"})[0]
    out = {"chat": chat["id"], "slot": chat["slot_id"]}
    out["q4"] = ask(chat["id"], "Quote the exact LinkedIn hook you wrote for me earlier in this chat.")
    out["q5"] = ask(chat["id"], "Schedule a one-time job that fires in 1 minute and sends me a "
                                "notification with exactly the text: PING 42. Confirm briefly.",
                    timeout=400)
    t0 = time.time()
    try:
        n = wait(lambda: [x for x in rest("GET", "notifications", {"select": "*", "order": "id.desc"})
                          if "PING 42" in (x["text"] or "")], 300, every=10, label="notification")
        out["cron_notification"] = {"after_secs": round(time.time() - t0), "text": n[0]["text"][:300],
                                    "meta": n[0]["meta"]}
    except SystemExit as e:
        out["cron_notification"] = str(e)
    for q in ("q4", "q5"):
        out[q].pop("meta", None)
    print(json.dumps(out, indent=2))
elif step == "stop":
    s = latest_session()
    rest("PATCH", "worker_sessions", {"id": f"eq.{s['id']}"}, {"stop_requested": True})
    s = wait(lambda: (lambda x: x if x["status"] in ("stopped", "error") else None)(latest_session()),
             600, every=10, label="stopped")
    print(json.dumps({k: s[k] for k in ("status", "last_snapshot", "info")}))
