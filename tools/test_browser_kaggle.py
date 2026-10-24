"""End-to-end browser test on the real Kaggle worker.

  start  - press Start through the edge function and wait for 'running'
  run    - Panda opens pages with `pb`; a phone stand-in watches the live stream, takes over, hands back
  check  - (after stop+start) confirm the login cookie survived the restart, and the stored blob is encrypted
  stop   - press Stop
Env: SB_URL, SB_PUBLISHABLE, TESTER_EMAIL, TESTER_PASSWORD, SB_SECRET (only for the blob check).
"""
import asyncio
import base64
import json
import os
import sys
import time

import requests
import websockets

URL = os.environ["SB_URL"].rstrip("/")
ANON = os.environ["SB_PUBLISHABLE"]


def login():
    r = requests.post(f"{URL}/auth/v1/token?grant_type=password", headers={"apikey": ANON},
                      json={"email": os.environ["TESTER_EMAIL"], "password": os.environ["TESTER_PASSWORD"]})
    r.raise_for_status()
    d = r.json()
    return {"apikey": ANON, "Authorization": f"Bearer {d['access_token']}", "Content-Type": "application/json"}, d["user"]["id"]


H, ME = login()


def rest(method, path, params=None, body=None, prefer=""):
    r = requests.request(method, f"{URL}/rest/v1/{path}", headers=dict(H, **({"Prefer": prefer} if prefer else {})), params=params, json=body)
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


def topic():
    rows = rest("GET", "user_settings", {"owner": f"eq.{ME}", "select": "settings"})
    s = (rows[0]["settings"] if rows else {}) or {}
    if not s.get("browserTopic"):
        s["browserTopic"] = "pbr-test-" + os.urandom(9).hex()
        requests.post(f"{URL}/rest/v1/user_settings", headers=dict(H, Prefer="resolution=merge-duplicates"),
                      json={"owner": ME, "settings": s})
    return s["browserTopic"]


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

    r = wait(done, timeout, every=2, label="reply")
    return {"status": r["status"], "secs": round(time.time() - t0), "reply": r["content"]}


class Phone:
    def __init__(self, tp):
        self.tp, self.frames, self.statuses, self.last = tp, 0, [], b""
        self.ws, self.joined = None, asyncio.Event()
        self.first_frame_at = None

    async def run(self):
        host = URL.replace("https://", "")
        async with websockets.connect(f"wss://{host}/realtime/v1/websocket?apikey={ANON}&vsn=1.0.0", max_size=4 * 2**20) as ws:
            self.ws = ws
            await ws.send(json.dumps({"topic": f"realtime:{self.tp}", "event": "phx_join", "ref": "1", "join_ref": "1", "payload": {
                "config": {"broadcast": {"ack": False, "self": False}, "presence": {"key": ""}, "postgres_changes": [], "private": False}}}))

            async def beat():
                while True:
                    await asyncio.sleep(20)
                    await ws.send(json.dumps({"topic": "phoenix", "event": "heartbeat", "payload": {}, "ref": "hb"}))
            hb = asyncio.create_task(beat())
            async for raw in ws:
                m = json.loads(raw)
                if m.get("event") == "phx_reply" and m.get("ref") == "1":
                    self.joined.set()
                if m.get("event") == "broadcast":
                    p = m["payload"]
                    if p["event"] == "frame":
                        self.frames += 1
                        self.first_frame_at = self.first_frame_at or time.time()
                        self.last = base64.b64decode(p["payload"]["d"])
                    elif p["event"] == "status":
                        self.statuses.append(p["payload"])
            hb.cancel()

    async def send(self, **payload):
        await self.ws.send(json.dumps({"topic": f"realtime:{self.tp}", "event": "broadcast", "ref": str(time.time()),
                                       "payload": {"type": "broadcast", "event": "input", "payload": payload}}))

    @property
    def status(self):
        return self.statuses[-1] if self.statuses else {}


step = sys.argv[1]

if step == "start":
    tp = topic()
    r = requests.post(f"{URL}/functions/v1/session", headers=H, json={"action": "start"})
    print(r.status_code, r.text[:120])
    s = wait(lambda: (lambda x: x if x and x["status"] in ("running", "error") else None)(latest_session()), 900, 10, "running")
    print(json.dumps({"status": s["status"], "startup": s["info"]}))

elif step == "run":
    tp = topic()

    async def main():
        out = {}
        phone = Phone(tp)
        asyncio.create_task(phone.run())
        await asyncio.wait_for(phone.joined.wait(), 20)

        async def hello_loop():
            while True:
                await phone.send(t="hello")
                await asyncio.sleep(8)
        asyncio.create_task(hello_loop())

        chat = rest("POST", "chats", body={"owner": ME, "title": "t-browser"}, prefer="return=representation")[0]
        t0 = time.time()
        a = await asyncio.to_thread(ask, chat["id"],
            "Use the panda-browser skill (the `pb` command). Open https://example.com, then open "
            "https://httpbin.org/cookies/set?panda=kaggle42 . Finally tell me the title of example.com and the cookie name you set. Be quick.")
        out["agent_browses"] = {"status": a["status"], "secs": round(time.time() - t0), "reply": a["reply"][:300]}
        out["live_stream"] = {"frames": phone.frames, "jpeg": phone.last[:3] == b"\xff\xd8\xff", "kb": round(len(phone.last) / 1024),
                              "first_frame_after_secs": round(phone.first_frame_at - t0) if phone.first_frame_at else None,
                              "last_url": phone.status.get("url")}
        open("/projects/sandbox/.pwhome/kaggle_frame.jpg", "wb").write(phone.last)

        # handoff: Panda asks, the phone sees it, takes over, finishes
        task = asyncio.create_task(asyncio.to_thread(ask, chat["id"],
            "Open https://httpbin.org/forms/post with pb, then run `pb handoff \"Please press Done\"` and wait for me. "
            "After it returns, tell me the page title and say HANDOFF-OK."))
        seen_needs = False
        for _ in range(240):
            await asyncio.sleep(2)
            if phone.status.get("control") == "needs_user":
                seen_needs = True
                break
        out["handoff"] = {"phone_saw_needs_user": seen_needs, "message": phone.status.get("message")}
        if seen_needs:
            await phone.send(t="takeover")
            await asyncio.sleep(1)
            await phone.send(t="click", x=0.5, y=0.2)
            await phone.send(t="text", text="Vlad")
            await phone.send(t="scroll", dy=300)
            await asyncio.sleep(2)
            out["handoff"]["control_after_takeover"] = phone.status.get("control")
            await phone.send(t="done")
        a = await asyncio.wait_for(task, 400)
        out["handoff"]["agent_continued"] = "HANDOFF-OK" in a["reply"]
        out["handoff"]["reply"] = a["reply"][:200]
        print(json.dumps(out, indent=1))

    asyncio.run(main())

elif step == "check":
    tp = topic()
    chat = rest("POST", "chats", body={"owner": ME, "title": "t-browser2"}, prefer="return=representation")[0]
    a = ask(chat["id"], "Use pb to open https://httpbin.org/cookies and tell me exactly what cookies it shows. Be quick.")
    print(json.dumps({"cookie_survived_restart": "kaggle42" in a["reply"], "reply": a["reply"][:250]}, indent=1))
    sk = os.environ.get("SB_SECRET")
    if sk:
        r = requests.get(f"{URL}/storage/v1/object/kirocrew-state/browser/state.enc", headers={"apikey": sk, "Authorization": f"Bearer {sk}"})
        blob = r.content
        print(json.dumps({"blob_status": r.status_code, "blob_bytes": len(blob),
                          "plaintext_visible": b"cookies" in blob or b"httpbin" in blob or b"kaggle42" in blob,
                          "looks_fernet": blob[:4] == b"gAAA"}))

elif step == "stop":
    s = latest_session()
    rest("PATCH", "worker_sessions", {"id": f"eq.{s['id']}"}, {"stop_requested": True})
    s = wait(lambda: (lambda x: x if x["status"] in ("stopped", "error") else None)(latest_session()), 600, 10, "stopped")
    print(json.dumps({k: s[k] for k in ("status", "info")}))
