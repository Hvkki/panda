#!/usr/bin/env python3
"""Panda browser engine: one real Chromium that Panda drives and the user can watch/take over.

  agent  --(pb.py, local HTTP :5480)-->  engine  --(CDP)-->  Chromium  (cookies persist)
  phone  <--(Supabase Realtime: JPEG frames, status)--  engine  <--(taps, keys, scroll)--  phone

Config (written by the worker): ~/.panda/browser.json
  {"supabase_url": ..., "anon": ..., "topic": "pbr-<secret>"}   topic empty => no live view
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from aiohttp import web

HOME = Path.home()
CFG_PATH = Path(os.environ.get("PANDA_BROWSER_CFG", HOME / ".panda/browser.json"))
STATE_PATH = Path(os.environ.get("PANDA_BROWSER_STATE", HOME / ".panda/browser/state.json"))
OUT_DIR = Path(os.environ.get("PANDA_OUTPUTS", HOME / "outputs"))
PORT = int(os.environ.get("PANDA_BROWSER_PORT", "5480"))
VIEW_W, VIEW_H = 1024, 720
VIEWER_TTL = 20          # seconds a viewer stays "present" after its last hello
MIN_FRAME_GAP = 0.12     # max ~8 fps
MAX_FRAME_B64 = 190_000  # Realtime free tier payload limit is ~250 KB


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def url_ok(url: str) -> tuple[bool, str]:
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False, "only http(s) addresses can be opened"
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return True, ""  # let the browser report the DNS failure itself
    for i in infos:
        ip = ipaddress.ip_address(i[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False, "private, local and internal addresses are blocked"
    return True, ""


def normalize(url: str) -> str:
    url = url.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        return url
    if " " in url or "." not in url:
        return "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(url)
    return "https://" + url


# ── Supabase Realtime (Phoenix protocol) ────────────────────────────────────
class Link:
    """Broadcast-only Realtime client with auto-reconnect."""

    def __init__(self, on_input):
        self.on_input = on_input
        self.ws = None
        self.ref = 0
        self.joined = False
        self.cfg: dict = {}
        self.task: asyncio.Task | None = None

    def configure(self, cfg: dict):
        changed = (cfg.get("topic"), cfg.get("supabase_url")) != (self.cfg.get("topic"), self.cfg.get("supabase_url"))
        self.cfg = cfg
        if changed:
            if self.task:
                self.task.cancel()
            self.joined = False
            if cfg.get("topic") and cfg.get("supabase_url"):
                self.task = asyncio.create_task(self._run())

    async def _run(self):
        import websockets
        delay = 1
        while True:
            try:
                host = self.cfg["supabase_url"].replace("https://", "").rstrip("/")
                url = f"wss://{host}/realtime/v1/websocket?apikey={self.cfg['anon']}&vsn=1.0.0"
                async with websockets.connect(url, max_size=4 * 2**20, ping_interval=None) as ws:
                    self.ws, self.ref, delay = ws, 0, 1
                    topic = "realtime:" + self.cfg["topic"]
                    await self._send({"topic": topic, "event": "phx_join", "ref": "1", "join_ref": "1", "payload": {
                        "config": {"broadcast": {"ack": False, "self": False}, "presence": {"key": ""},
                                   "postgres_changes": [], "private": False}}})
                    hb = asyncio.create_task(self._heartbeat())
                    try:
                        async for raw in ws:
                            msg = json.loads(raw)
                            ev = msg.get("event")
                            if ev == "phx_reply" and msg.get("ref") == "1":
                                self.joined = msg.get("payload", {}).get("status") == "ok"
                                log("realtime joined" if self.joined else f"realtime join failed: {msg.get('payload')}")
                            elif ev == "broadcast":
                                p = msg.get("payload", {})
                                if p.get("event") == "input":
                                    asyncio.create_task(self.on_input(p.get("payload") or {}))
                            elif ev in ("phx_error", "phx_close"):
                                break
                    finally:
                        hb.cancel()
                        self.joined = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log("realtime error:", type(exc).__name__, str(exc)[:120])
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15)

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(20)
            await self._send({"topic": "phoenix", "event": "heartbeat", "payload": {}, "ref": "hb"})

    async def _send(self, msg: dict):
        if self.ws:
            with contextlib.suppress(Exception):
                await self.ws.send(json.dumps(msg))

    async def broadcast(self, event: str, payload: dict):
        if not self.joined:
            return
        self.ref += 1
        await self._send({"topic": "realtime:" + self.cfg["topic"], "event": "broadcast", "ref": str(self.ref + 1),
                          "payload": {"type": "broadcast", "event": event, "payload": payload}})


# ── the browser ─────────────────────────────────────────────────────────────
class Engine:
    def __init__(self):
        self.pw = self.browser = self.ctx = self.page = self.cdp = None
        self.lock = asyncio.Lock()
        self.link = Link(self.on_input)
        self.control = "agent"            # agent | user | needs_user
        self.message = ""
        self.done_evt = asyncio.Event()
        self.last_hello = 0.0
        self.latest: str | None = None
        self.frame_evt = asyncio.Event()
        self.quality = 55
        self.busy = False
        self.cfg_mtime = 0.0
        self.last_status = ""
        self.ua_major = "140"
        self.closing = False
        self.view = (VIEW_W, VIEW_H)       # current page size in CSS px (phone viewers get a tall, narrow page)

    # lifecycle ----------------------------------------------------------------
    async def start(self):
        from playwright.async_api import async_playwright
        self.pw = await async_playwright().start()
        exe = os.environ.get("PANDA_CHROMIUM") or None
        args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-crash-reporter",
                "--disable-blink-features=AutomationControlled", "--lang=en-US"]
        try:
            self.browser = await self.pw.chromium.launch(headless=True, executable_path=exe, args=args)
        except Exception as exc:
            if "Executable doesn't exist" not in str(exc):
                raise
            log("chromium missing, installing…")
            subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], check=False)
            self.browser = await self.pw.chromium.launch(headless=True, executable_path=exe, args=args)
        self.ua_major = self.browser.version.split(".")[0]
        await self._new_context(STATE_PATH if STATE_PATH.exists() else None)
        asyncio.create_task(self._frame_sender())
        asyncio.create_task(self._housekeeping())
        self._load_cfg(force=True)
        log("engine ready, chromium", self.browser.version)

    async def _new_context(self, state):
        if self.ctx:
            with contextlib.suppress(Exception):
                await self.ctx.close()
        self.cdp = None
        ua = f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self.ua_major}.0.0.0 Safari/537.36"
        try:
            self.ctx = await self.browser.new_context(viewport={"width": VIEW_W, "height": VIEW_H}, user_agent=ua,
                                                      locale="en-US", accept_downloads=True,
                                                      storage_state=str(state) if state else None)
        except Exception as exc:  # a corrupt saved state must never lock the browser
            log("saved state unusable:", exc)
            self.ctx = await self.browser.new_context(viewport={"width": VIEW_W, "height": VIEW_H}, user_agent=ua,
                                                      locale="en-US", accept_downloads=True)
        self.ctx.on("page", lambda p: asyncio.create_task(self._adopt(p)))
        await self.ctx.route("**/*", self._route)
        page = await self.ctx.new_page()
        await self._adopt(page)

    async def _route(self, route):
        req = route.request
        if req.resource_type == "document" and req.is_navigation_request():
            ok, why = await asyncio.to_thread(url_ok, req.url)
            if not ok and not req.url.startswith("about:"):
                log("blocked navigation:", req.url[:80], why)
                return await route.abort("blockedbyclient")
        await route.continue_()

    async def _adopt(self, page):
        self.page = page
        with contextlib.suppress(Exception):
            await page.set_viewport_size({"width": self.view[0], "height": self.view[1]})
        page.on("dialog", lambda d: asyncio.create_task(d.accept() if d.type in ("alert", "beforeunload") else d.dismiss()))
        page.on("download", lambda d: asyncio.create_task(self._download(d)))
        page.on("close", lambda: asyncio.create_task(self._page_closed(page)))
        page.on("framenavigated", lambda f: asyncio.create_task(self.push_status()) if f == page.main_frame else None)
        await self._restart_screencast()
        await self.push_status()

    async def _page_closed(self, page):
        # Ignore closes caused by shutting down or swapping the whole context.
        if self.closing or page.context is not self.ctx or self.page is not page:
            return
        try:
            pages = [p for p in self.ctx.pages if not p.is_closed()]
            await self._adopt(pages[-1] if pages else await self.ctx.new_page())
        except Exception as exc:
            log("could not recover from closed tab:", str(exc)[:100])

    async def _download(self, d):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        dest = OUT_DIR / "downloads" / d.suggested_filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        await d.save_as(str(dest))
        log("downloaded", dest)

    def _load_cfg(self, force=False):
        try:
            m = CFG_PATH.stat().st_mtime
        except OSError:
            return
        if force or m != self.cfg_mtime:
            self.cfg_mtime = m
            with contextlib.suppress(Exception):
                self.link.configure(json.loads(CFG_PATH.read_text()))

    async def _housekeeping(self):
        n = 0
        while True:
            await asyncio.sleep(2)
            n += 1
            self._load_cfg()
            active = self.viewer_active()
            if active and not self.cdp:
                await self._restart_screencast()
            elif not active and self.cdp:
                await self._stop_screencast()
            if n % 22 == 0:  # ~every 45 s
                await self.save_state()
            if n % 5 == 0:  # ~every 10 s, viewer or not, so the app can show "Panda is browsing"
                await self.push_status(force=True)

    def viewer_active(self) -> bool:
        return time.time() - self.last_hello < VIEWER_TTL

    async def save_state(self) -> bool:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            await self.ctx.storage_state(path=str(tmp))
            os.chmod(tmp, 0o600)
            os.replace(tmp, STATE_PATH)
            return True
        except Exception as exc:
            log("state save failed:", exc)
            return False

    # live view -----------------------------------------------------------------
    async def _stop_screencast(self):
        if self.cdp:
            with contextlib.suppress(Exception):
                await self.cdp.send("Page.stopScreencast")
            with contextlib.suppress(Exception):
                await self.cdp.detach()
        self.cdp = None

    async def _restart_screencast(self):
        await self._stop_screencast()
        if not self.viewer_active() or not self.page:
            return
        try:
            self.cdp = await self.ctx.new_cdp_session(self.page)
            self.cdp.on("Page.screencastFrame", self._on_frame)
            await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": self.quality, "maxWidth": self.view[0],
                                                         "maxHeight": self.view[1], "everyNthFrame": 1})
        except Exception as exc:
            log("screencast failed:", exc)
            self.cdp = None

    def _on_frame(self, params):
        cdp = self.cdp
        if cdp:
            asyncio.create_task(self._ack(cdp, params["sessionId"]))
        self.latest = params["data"]
        self.frame_evt.set()

    async def _ack(self, cdp, sid):
        with contextlib.suppress(Exception):
            await cdp.send("Page.screencastFrameAck", {"sessionId": sid})

    async def _frame_sender(self):
        last = 0.0
        while True:
            await self.frame_evt.wait()
            self.frame_evt.clear()
            gap = MIN_FRAME_GAP - (time.time() - last)
            if gap > 0:
                await asyncio.sleep(gap)
            data, self.latest = self.latest, None
            if not data or not self.viewer_active():
                continue
            if len(data) > MAX_FRAME_B64 and self.quality > 25:  # page too detailed: compress harder
                self.quality -= 10
                asyncio.create_task(self._restart_screencast())
                continue
            last = time.time()
            await self.link.broadcast("frame", {"d": data, "w": self.view[0], "h": self.view[1]})

    async def snap_frame(self):
        """One immediate frame (used when a viewer opens, or the page is idle)."""
        with contextlib.suppress(Exception):
            png = await self.page.screenshot(type="jpeg", quality=self.quality, timeout=5000)
            self.latest = base64.b64encode(png).decode()
            self.frame_evt.set()

    async def push_status(self, force=False):
        if not self.page:
            return
        try:
            title = await asyncio.wait_for(self.page.title(), 2)
        except Exception:
            title = ""
        tabs = []
        for i, p in enumerate(self.ctx.pages):
            tabs.append({"i": i, "u": p.url[:200], "active": p is self.page})
        st = {"url": self.page.url, "title": title[:120], "tabs": tabs, "control": self.control,
              "message": self.message, "busy": self.busy, "viewer": self.viewer_active(), "w": self.view[0], "h": self.view[1]}
        key = json.dumps(st, sort_keys=True)
        if force or key != self.last_status:
            self.last_status = key
            await self.link.broadcast("status", st)

    async def fit(self, mode: str, vw: float, vh: float):
        """Phone viewers get a narrow, tall page that fills their screen; desktops get 1024x720."""
        if mode == "mobile" and vw > 50 and vh > 50:
            w = 480
            h = int(max(560, min(1000, w * vh / vw)))
        else:
            w, h = VIEW_W, VIEW_H
        if (w, h) == self.view:
            return
        self.view = (w, h)
        for pg in list(self.ctx.pages):
            with contextlib.suppress(Exception):
                await pg.set_viewport_size({"width": w, "height": h})
        await self._restart_screencast()

    # input from the phone -----------------------------------------------------
    async def on_input(self, p: dict):
        t = p.get("t")
        try:
            if t == "hello":
                self.last_hello = time.time()
                if p.get("mode"):
                    await self.fit(str(p["mode"]), float(p.get("vw") or 0), float(p.get("vh") or 0))
                if not self.cdp:
                    await self._restart_screencast()
                await self.push_status(force=True)
                await self.snap_frame()
                return
            if t == "takeover":
                self.control, self.message = "user", ""
                return await self.push_status(force=True)
            if t == "done":
                self.control, self.message = "agent", ""
                self.done_evt.set()
                return await self.push_status(force=True)
            if self.control == "agent":
                return  # view only until the user takes over
            page = self.page
            if t == "click":
                await page.mouse.click(float(p["x"]) * self.view[0], float(p["y"]) * self.view[1])
            elif t == "scroll":
                await page.mouse.move(float(p.get("x", 0.5)) * self.view[0], float(p.get("y", 0.5)) * self.view[1])
                await page.mouse.wheel(float(p.get("dx", 0)), float(p.get("dy", 0)))
            elif t == "key":
                await page.keyboard.press(str(p["key"])[:30])
            elif t == "text":
                await page.keyboard.insert_text(str(p["text"])[:2000])
            elif t == "nav":
                url = normalize(str(p["url"]))
                ok, why = url_ok(url)
                if ok:
                    asyncio.create_task(self._goto(url))
                else:
                    self.message = why
            elif t == "back":
                await page.go_back(timeout=8000)
            elif t == "forward":
                await page.go_forward(timeout=8000)
            elif t == "reload":
                await page.reload(timeout=15000)
            elif t == "tab":
                pages = self.ctx.pages
                i = int(p["i"])
                if 0 <= i < len(pages):
                    await pages[i].bring_to_front()
                    await self._adopt(pages[i])
            elif t == "newtab":
                await self._adopt(await self.ctx.new_page())
            elif t == "closetab":
                await self.page.close()
            await self.snap_frame()
        except Exception as exc:
            log("input failed:", t, str(exc)[:120])

    async def _goto(self, url):
        with contextlib.suppress(Exception):
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self.snap_frame()

    # agent operations --------------------------------------------------------
    async def snapshot(self, full=False) -> str:
        try:
            s = await asyncio.wait_for(self.page.aria_snapshot(mode="ai"), 12)
        except Exception as exc:
            s = f"(could not read the page: {str(exc)[:120]})"
        limit = 40000 if full else 12000
        if len(s) > limit:
            s = s[:limit] + "\n… (truncated — `pb snap --full`, or `pb text` to read the content)"
        return s

    async def summary(self, snap=True, full=False) -> dict:
        try:
            title = await asyncio.wait_for(self.page.title(), 3)
        except Exception:
            title = ""
        tabs = [f"{'*' if p is self.page else ' '}[{i}] {p.url[:90]}" for i, p in enumerate(self.ctx.pages)]
        out = {"ok": True, "url": self.page.url, "title": title, "tabs": tabs if len(tabs) > 1 else []}
        if snap:
            out["snapshot"] = await self.snapshot(full)
        return out

    async def settle(self, extra=0.35):
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        await asyncio.sleep(extra)
        await self.snap_frame()

    def _loc(self, ref: str):
        return self.page.locator(f"aria-ref={ref}")

    async def do(self, req: dict) -> dict:
        op = req.get("op")
        if op == "status":
            return {"ok": True, "control": self.control, "viewer": self.viewer_active(), "joined": self.link.joined,
                    **await self.summary(snap=False)}
        if self.control == "user":  # the user took over: wait for them to give it back
            self.message = "Panda is waiting for you to press Done"
            await self.push_status(force=True)
            self.done_evt.clear()
            try:
                await asyncio.wait_for(self.done_evt.wait(), 900)
            except asyncio.TimeoutError:
                return {"ok": False, "error": "the user is still controlling the browser; try again later"}
        async with self.lock:
            self.busy = True
            await self.push_status(force=True)
            try:
                return await self._do(op, req)
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                        **{k: v for k, v in (await self.summary(snap=False)).items() if k in ("url", "title")}}
            finally:
                self.busy = False
                await self.push_status(force=True)

    async def _do(self, op: str, r: dict) -> dict:
        page = self.page
        want_snap = not r.get("no_snap")
        if op == "open":
            url = normalize(r["url"])
            ok, why = url_ok(url)
            if not ok:
                return {"ok": False, "error": why}
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self.settle(0.8)
        elif op == "snap":
            pass
        elif op == "click":
            if r.get("ref"):
                await self._loc(r["ref"]).click(timeout=8000)
            else:
                await page.mouse.click(float(r["x"]), float(r["y"]))
            await self.settle()
        elif op == "type":
            loc = self._loc(r["ref"])
            text = r.get("text", "")
            try:
                await loc.fill(text, timeout=6000)
            except Exception:
                await loc.click(timeout=5000)
                await page.keyboard.type(text, delay=15)
            if r.get("submit"):
                await page.keyboard.press("Enter")
            await self.settle()
        elif op == "press":
            await page.keyboard.press(r["key"])
            await self.settle()
        elif op == "scroll":
            await page.mouse.move(self.view[0] / 2, self.view[1] / 2)
            await page.mouse.wheel(0, float(r.get("dy", 600)))
            await self.settle(0.3)
        elif op in ("back", "forward", "reload"):
            await {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[op](timeout=15000)
            await self.settle()
        elif op == "text":
            txt = await page.evaluate("document.body ? document.body.innerText : ''")
            limit = 40000 if r.get("full") else 9000
            out = await self.summary(snap=False)
            out["text"] = txt[:limit] + ("\n… (truncated; use --full)" if len(txt) > limit else "")
            return out
        elif op == "shot":
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", r.get("name") or f"screenshot-{int(time.time())}.png")
            if not name.lower().endswith((".png", ".jpg")):
                name += ".png"
            path = OUT_DIR / name
            await page.screenshot(path=str(path), full_page=bool(r.get("full")))
            return {**await self.summary(snap=False), "saved": str(path)}
        elif op == "tabs":
            return await self.summary(snap=False)
        elif op == "tab":
            pages = self.ctx.pages
            await self._adopt(pages[int(r["i"])])
            await self.settle(0.2)
        elif op == "newtab":
            await self._adopt(await self.ctx.new_page())
            if r.get("url"):
                url = normalize(r["url"])
                ok, why = url_ok(url)
                if not ok:
                    return {"ok": False, "error": why}
                await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await self.settle(0.6)
        elif op == "closetab":
            await page.close()
            await asyncio.sleep(0.2)
        elif op == "wait":
            if r.get("text"):
                await page.get_by_text(r["text"]).first.wait_for(timeout=float(r.get("secs", 15)) * 1000)
            else:
                await asyncio.sleep(min(float(r.get("secs", 2)), 30))
            await self.snap_frame()
        elif op == "handoff":
            self.control, self.message = "needs_user", (r.get("message") or "Panda needs your help")[:300]
            self.done_evt.clear()
            await self.push_status(force=True)
            self.busy = False
            try:
                await asyncio.wait_for(self.done_evt.wait(), float(r.get("timeout", 600)))
            except asyncio.TimeoutError:
                self.control, self.message = "agent", ""
                return {"ok": False, "error": "the user did not respond in time"}
            self.busy = True
            await self.save_state()
        elif op == "forget":
            STATE_PATH.unlink(missing_ok=True)
            await self._new_context(None)
            return {"ok": True, "note": "all browser logins and cookies were deleted"}
        elif op == "save":
            return {"ok": await self.save_state()}
        else:
            return {"ok": False, "error": f"unknown op {op}"}
        return await self.summary(snap=want_snap, full=bool(r.get("full")))


PID_PATH = HOME / ".panda/engine.pid"


async def main():
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()))
    eng = Engine()
    await eng.start()

    async def handle_do(request):
        return web.json_response(await eng.do(await request.json()))

    async def health(_):
        return web.json_response({"ok": True})

    app = web.Application(client_max_size=1 << 20)
    app.add_routes([web.post("/do", handle_do), web.get("/health", health)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    log("shutting down")
    PID_PATH.unlink(missing_ok=True)
    eng.closing = True
    await eng.save_state()
    with contextlib.suppress(Exception):
        await eng.browser.close()


if __name__ == "__main__":
    asyncio.run(main())
