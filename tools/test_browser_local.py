"""Local integration test of the Panda browser engine against live Supabase Realtime.

Runs the engine as a subprocess, drives it with `pb`, and plays the phone over a second
Realtime connection: receives frames/status, takes over, taps, types, gives back control.
"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import time

import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SK = os.path.join(ROOT, "worker/skills/panda-browser")
HOME = os.environ["TEST_HOME"]
TOPIC = "pbr-test-" + os.urandom(8).hex()
URL = os.environ["SB_URL"]
ANON = os.environ["SB_PUBLISHABLE"]
CHROME = os.environ.get("PANDA_CHROMIUM", "")

os.makedirs(f"{HOME}/.panda", exist_ok=True)
json.dump({"supabase_url": URL, "anon": ANON, "topic": TOPIC}, open(f"{HOME}/.panda/browser.json", "w"))
env = dict(os.environ, HOME=HOME, PANDA_CHROMIUM=CHROME, PANDA_BROWSER_PORT="5481", PANDA_OUTPUTS=f"{HOME}/outputs")
results = {}


def pb(*args, timeout=120):
    r = subprocess.run([sys.executable, f"{SK}/pb.py", *args], env=env, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)


class Phone:
    def __init__(self):
        self.frames = 0
        self.last_frame = b""
        self.status = {}
        self.ws = None
        self.joined = asyncio.Event()

    async def run(self):
        host = URL.replace("https://", "")
        async with websockets.connect(f"wss://{host}/realtime/v1/websocket?apikey={ANON}&vsn=1.0.0", max_size=4 * 2**20) as ws:
            self.ws = ws
            await ws.send(json.dumps({"topic": f"realtime:{TOPIC}", "event": "phx_join", "ref": "1", "join_ref": "1", "payload": {
                "config": {"broadcast": {"ack": False, "self": False}, "presence": {"key": ""}, "postgres_changes": [], "private": False}}}))
            async for raw in ws:
                m = json.loads(raw)
                if m.get("event") == "phx_reply" and m.get("ref") == "1":
                    self.joined.set()
                if m.get("event") == "broadcast":
                    p = m["payload"]
                    if p["event"] == "frame":
                        self.frames += 1
                        self.last_frame = base64.b64decode(p["payload"]["d"])
                    elif p["event"] == "status":
                        self.status = p["payload"]

    async def send(self, **payload):
        await self.ws.send(json.dumps({"topic": f"realtime:{TOPIC}", "event": "broadcast", "ref": str(time.time()),
                                       "payload": {"type": "broadcast", "event": "input", "payload": payload}}))


async def main():
    eng = subprocess.Popen([sys.executable, f"{SK}/engine.py"], env=env, stdout=open(f"{HOME}/engine.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            await asyncio.sleep(0.5)
            if await asyncio.to_thread(lambda: pb("tabs")[0] == 0):
                break
        phone = Phone()
        asyncio.create_task(phone.run())
        await asyncio.wait_for(phone.joined.wait(), 20)
        await asyncio.sleep(2)  # engine joins its own side
        # 1. agent opens a page
        t0 = time.time()
        rc, out = await asyncio.to_thread(pb, "open", "example.com")
        results["open"] = {"rc": rc, "secs": round(time.time() - t0, 1), "has_link_ref": "[ref=" in out, "head": out[:160]}
        # 2. phone connects -> must receive frames + status
        await phone.send(t="hello")
        await asyncio.sleep(3)
        results["live_view"] = {"frames": phone.frames, "jpeg": phone.last_frame[:3] == b"\xff\xd8\xff", "kb": round(len(phone.last_frame) / 1024),
                                "status_url": phone.status.get("url"), "control": phone.status.get("control")}
        open(f"{HOME}/frame.jpg", "wb").write(phone.last_frame)
        # 3. view-only: taps are ignored while the agent controls
        before = phone.status.get("url")
        await phone.send(t="nav", url="https://www.wikipedia.org")
        await asyncio.sleep(2)
        results["view_only_blocks_input"] = (phone.status.get("url") == before)
        # 4. agent click via ref
        rc, out = await asyncio.to_thread(pb, "open", "https://example.com")
        import re
        m = re.search(r'link "[^"]*" \[ref=(e\d+)\]', out)
        if m:
            rc, out2 = await asyncio.to_thread(pb, "click", m.group(1))
            results["agent_click"] = {"rc": rc, "url_after": next((l for l in out2.splitlines() if l.startswith("URL:")), "")}
        # 5. handoff: agent asks, phone sees needs_user, takes over, navigates, presses done
        hand = asyncio.create_task(asyncio.to_thread(pb, "handoff", "Please log in, then press Done", timeout=60))
        await asyncio.sleep(3)
        results["handoff_status"] = {"control": phone.status.get("control"), "message": phone.status.get("message")}
        await phone.send(t="takeover")
        await asyncio.sleep(1)
        await phone.send(t="nav", url="https://www.wikipedia.org")
        await asyncio.sleep(4)
        results["user_navigated"] = phone.status.get("url", "")
        await phone.send(t="click", x=0.5, y=0.5)
        await phone.send(t="text", text="hello")
        await phone.send(t="scroll", dy=300)
        await phone.send(t="done")
        rc, out = await asyncio.wait_for(hand, 30)
        results["handoff_returned"] = {"rc": rc, "tail": out.strip().splitlines()[-1][:100] if out.strip() else ""}
        # 6. text + screenshot + persistence of cookies across an engine restart
        rc, out = await asyncio.to_thread(pb, "text")
        results["text"] = {"rc": rc, "chars": len(out)}
        rc, out = await asyncio.to_thread(pb, "shot", "test.png")
        results["shot"] = {"rc": rc, "file": os.path.exists(f"{HOME}/outputs/test.png")}
        rc, out = await asyncio.to_thread(pb, "open", "https://httpbin.org/cookies/set?panda=cookie42")
        await asyncio.to_thread(pb, "tabs")
        results["cookie_set"] = rc
        # 7. SSRF guard
        rc, out = await asyncio.to_thread(pb, "open", "http://169.254.169.254/latest/meta-data/")
        results["ssrf_blocked"] = rc != 0 and "blocked" in out
        rc, out = await asyncio.to_thread(pb, "open", "http://localhost:5476")
        results["localhost_blocked"] = rc != 0 and "blocked" in out
    finally:
        # graceful stop saves the state file
        eng.terminate()
        try:
            eng.wait(10)
        except Exception:
            eng.kill()
    results["state_saved"] = os.path.exists(f"{HOME}/.panda/browser/state.json")
    if results["state_saved"]:
        st = json.load(open(f"{HOME}/.panda/browser/state.json"))
        results["cookie_names"] = sorted({c["name"] for c in st.get("cookies", [])})
    # restart: cookie should survive
    eng2 = subprocess.Popen([sys.executable, f"{SK}/engine.py"], env=env, stdout=open(f"{HOME}/engine2.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            await asyncio.sleep(0.5)
            if await asyncio.to_thread(lambda: pb("tabs")[0] == 0):
                break
        rc, out = await asyncio.to_thread(pb, "open", "https://httpbin.org/cookies")
        results["cookie_survived_restart"] = "cookie42" in out
        rc, out = await asyncio.to_thread(pb, "forget")
        results["forget"] = out.strip()[:80]
        rc, out = await asyncio.to_thread(pb, "open", "https://httpbin.org/cookies")
        results["cookie_gone_after_forget"] = "cookie42" not in out
    finally:
        eng2.terminate()
        try:
            eng2.wait(10)
        except Exception:
            eng2.kill()
    print(json.dumps(results, indent=1))


asyncio.run(main())
