"""Phone view of the Panda browser engine, against live Supabase Realtime (no database writes).

Checks: a phone viewer gets a real mobile page (mobile layout + user agent) with sharp device-pixel
frames; typed addresses and searches go to Google ("google com" is google.com); a tap on a search
field reports focus (with its position, so the app can keep it visible above the keyboard).

    TEST_HOME=/tmp/pbm SB_URL=https://<project>.supabase.co SB_PUBLISHABLE=sb_publishable_... \
    PANDA_CHROMIUM=/path/to/chrome python tools/test_browser_mobile.py
"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import time
from struct import unpack

import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SK = os.path.join(ROOT, "worker/skills/panda-browser")
sys.path.insert(0, SK)
import engine as E  # noqa: E402  (pure helpers only)

HOME = os.environ["TEST_HOME"]
TOPIC = "pbr-test-" + os.urandom(8).hex()
URL, ANON = os.environ["SB_URL"], os.environ["SB_PUBLISHABLE"]
os.makedirs(f"{HOME}/.panda", exist_ok=True)
json.dump({"supabase_url": URL, "anon": ANON, "topic": TOPIC}, open(f"{HOME}/.panda/browser.json", "w"))
env = dict(os.environ, HOME=HOME, PANDA_BROWSER_PORT="5482", PANDA_OUTPUTS=f"{HOME}/outputs")
R: dict = {}


def jpeg_size(d: bytes):
    i = 2
    while i < len(d):
        m, n = d[i + 1], unpack(">H", d[i + 2:i + 4])[0]
        if m in (0xC0, 0xC2):
            h, w = unpack(">HH", d[i + 5:i + 9])
            return w, h
        i += 2 + n


def pb(*args):
    r = subprocess.run([sys.executable, f"{SK}/pb.py", *args], env=env, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


class Phone:
    def __init__(self):
        self.frame, self.frames, self.status, self.ws = b"", 0, {}, None
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
                        self.frame = base64.b64decode(p["payload"]["d"])
                    elif p["event"] == "status":
                        self.status = p["payload"]

    async def send(self, **payload):
        await self.ws.send(json.dumps({"topic": f"realtime:{TOPIC}", "event": "broadcast", "ref": str(time.time()),
                                       "payload": {"type": "broadcast", "event": "input", "payload": payload}}))


async def main():
    R["normalize"] = {s: E.normalize(s) for s in ("google com", "vinted it", "vinted", "red panda facts", "x.com", "")}
    eng = subprocess.Popen([sys.executable, f"{SK}/engine.py"], env=env, stdout=open(f"{HOME}/engine.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            await asyncio.sleep(0.5)
            if await asyncio.to_thread(lambda: pb("tabs")[0] == 0):
                break
        phone = Phone()
        asyncio.create_task(phone.run())
        await asyncio.wait_for(phone.joined.wait(), 20)
        await asyncio.sleep(2)
        # A phone opens the panel: 390x700 free area.
        await phone.send(t="hello", mode="mobile", vw=390, vh=700)
        await asyncio.sleep(5)
        R["empty_browser_opens"] = phone.status.get("url", "")
        await phone.send(t="takeover")
        await asyncio.sleep(0.5)
        await phone.send(t="nav", url="en.wikipedia.org/wiki/Red_panda")
        await asyncio.sleep(6)
        await phone.send(t="hello", mode="mobile", vw=390, vh=700)
        await asyncio.sleep(2)
        R["mobile_page"] = {"status_wh": [phone.status.get("w"), phone.status.get("h")], "url": phone.status.get("url"),
                            "frame_px": jpeg_size(phone.frame), "frame_kb": round(len(phone.frame) / 1024), "frames": phone.frames}
        open(f"{HOME}/mobile.jpg", "wb").write(phone.frame)
        await phone.send(t="nav", url="google com")
        await asyncio.sleep(6)
        R["typed_google_com"] = phone.status.get("url", "")
        await phone.send(t="nav", url="vinted")
        await asyncio.sleep(6)
        R["search_goes_to"] = phone.status.get("url", "")[:60]
        open(f"{HOME}/google.jpg", "wb").write(phone.frame)
        # Tap Google's search box (top of the results page) and expect focus with a position.
        await phone.send(t="nav", url="https://www.google.com/")
        await asyncio.sleep(5)
        open(f"{HOME}/google-home.jpg", "wb").write(phone.frame)
        await phone.send(t="key", key="Tab")
        for _ in range(6):
            if phone.status.get("focus"):
                break
            await phone.send(t="key", key="Tab")
            await asyncio.sleep(1)
        R["focus"] = phone.status.get("focus")
        # Back to a desktop viewer: the page goes back to 1024x720.
        await phone.send(t="done")
        await phone.send(t="hello", mode="desktop", vw=1200, vh=800)
        await asyncio.sleep(4)
        R["desktop_again"] = {"status_wh": [phone.status.get("w"), phone.status.get("h")], "frame_px": jpeg_size(phone.frame)}
    finally:
        eng.terminate()
        try:
            eng.wait(10)
        except Exception:
            eng.kill()
    print(json.dumps(R, indent=1))


asyncio.run(main())
