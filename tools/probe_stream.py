"""Watch a reply row update in real time to prove whether streaming is incremental.

Sends one message that forces several tool calls, then polls the assistant reply
every second and prints how content / thinking / tool-steps grow over wall-clock time.
"""
import json, os, sys, time
import requests

U = os.environ["SB_URL"].rstrip("/"); ANON = os.environ["SB_PUBLISHABLE"]
r = requests.post(f"{U}/auth/v1/token?grant_type=password", headers={"apikey": ANON},
                  json={"email": os.environ["TESTER_EMAIL"], "password": os.environ["TESTER_PASSWORD"]}); r.raise_for_status()
H = {"apikey": ANON, "Authorization": f"Bearer {r.json()['access_token']}", "Content-Type": "application/json"}
ME = r.json()["user"]["id"]
def rest(m, p, params=None, body=None, prefer=""):
    x = requests.request(m, f"{U}/rest/v1/{p}", headers=dict(H, **({"Prefer": prefer} if prefer else {})), params=params, json=body)
    x.raise_for_status(); return x.json() if x.text else None

enable_think = rest("POST", "user_settings", body={"owner": ME, "settings": {"showThinking": True}},
                    prefer="resolution=merge-duplicates,return=representation")
c = rest("POST", "chats", body={"owner": ME, "title": "probe"}, prefer="return=representation")[0]
msg = rest("POST", "messages", prefer="return=representation", body={"chat_id": c["id"], "owner": ME, "role": "user", "status": "pending",
      "content": "Do these as THREE separate bash commands, one at a time: 1) ls ~  2) cat /etc/hostname  3) date -u . "
                 "Then write one short sentence about red pandas. Think briefly first."})[0]
print(f"sent msg {msg['id']} at t=0")
t0 = time.time(); last = ""; reply = None
while time.time() - t0 < 180:
    # auto-approve any tool cards (settings is Trust, but just in case)
    for tc in rest("GET", "messages", {"chat_id": f"eq.{c['id']}", "role": "eq.tool", "status": "eq.pending", "select": "id,decision"}):
        if not tc["decision"]: rest("PATCH", "messages", {"id": f"eq.{tc['id']}"}, {"decision": "approved"})
    rows = rest("GET", "messages", {"reply_to": f"eq.{msg['id']}", "role": "eq.assistant", "select": "*", "order": "id.asc"})
    if rows:
        reply = rows[0]; meta = reply["meta"]
        snap = f"status={reply['status']:<10} text={len(reply['content']):>4} think={len(reply['thinking']):>4} steps={len(meta.get('tools') or [])} :: {(meta.get('tools') or [])[-1][:38] if meta.get('tools') else ''}"
        if snap != last:
            print(f"  t={time.time()-t0:5.1f}s  {snap}"); last = snap
        if reply["status"] in ("done", "error"): break
    time.sleep(1)
print("FINAL reply:", (reply or {}).get("content", "")[:200])
print("FINAL steps:", json.dumps((reply or {}).get("meta", {}).get("tools", []), indent=0)[:400])
print("thinking sample:", (reply or {}).get("thinking", "")[:160] or "(none)")
