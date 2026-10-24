"""Force a reasoning turn at high effort and watch whether thinking streams in."""
import json, os, time
import requests
U = os.environ["SB_URL"].rstrip("/"); ANON = os.environ["SB_PUBLISHABLE"]
r = requests.post(f"{U}/auth/v1/token?grant_type=password", headers={"apikey": ANON},
                  json={"email": os.environ["TESTER_EMAIL"], "password": os.environ["TESTER_PASSWORD"]}); r.raise_for_status()
H = {"apikey": ANON, "Authorization": f"Bearer {r.json()['access_token']}", "Content-Type": "application/json"}; ME = r.json()["user"]["id"]
def rest(m, p, params=None, body=None, prefer=""):
    x = requests.request(m, f"{U}/rest/v1/{p}", headers=dict(H, **({"Prefer": prefer} if prefer else {})), params=params, json=body)
    x.raise_for_status(); return x.json() if x.text else None
c = rest("POST", "chats", body={"owner": ME, "title": "think", "model": "claude-sonnet-5.5", "effort": "high"}, prefer="return=representation")[0]
msg = rest("POST", "messages", prefer="return=representation", body={"chat_id": c["id"], "owner": ME, "role": "user", "status": "pending",
      "content": "Think step by step, showing your reasoning, then answer: a bat and ball cost 1.10 total, the bat costs 1.00 more than the ball. How much is the ball? Explain the trap."})[0]
print("sent", msg["id"]); t0 = time.time(); last = ""; reply = None
while time.time() - t0 < 180:
    rows = rest("GET", "messages", {"reply_to": f"eq.{msg['id']}", "role": "eq.assistant", "select": "*"})
    if rows:
        reply = rows[0]
        snap = f"status={reply['status']:<10} text={len(reply['content']):>4} think={len(reply['thinking']):>5}"
        if snap != last: print(f"  t={time.time()-t0:5.1f}s {snap}"); last = snap
        if reply["status"] in ("done", "error"): break
    time.sleep(1)
print("thinking (first 300):", (reply or {}).get("thinking", "")[:300] or "(NONE)")
print("answer (first 160):", (reply or {}).get("content", "")[:160])
