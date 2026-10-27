#!/usr/bin/env python3
"""Panda browser engine: one real Chromium that Panda drives and the user can watch/take over.

  agent  --(pb.py, local HTTP :5480)-->  engine  --(CDP)-->  Chromium  (cookies persist)
  phone  <--(Supabase Realtime: JPEG frames, status, actions)--  engine  <--(taps, keys, scroll)--  phone

Realtime events the engine broadcasts on the private topic:
  frame   {d: base64 JPEG, w, h}                       live screen (only while someone watches)
  status  {url, title, tabs, control, message, busy, loading, action, focus, w, h, ...}
  action  {k: kind, c: caption, t: ms, x?, y?, w?, h?}  what Panda is doing, with the target's box
                                                       (0..1 of the page) so the app can move a cursor

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
import shutil
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
MIN_FRAME_GAP = 0.07     # max ~14 fps
MAX_FRAME_B64 = 190_000  # Realtime free tier payload limit is ~250 KB
BEST_QUALITY = 60
CURSOR_LEAD = 0.18       # while someone watches, show where Panda will click before it clicks
# Phones: a real mobile page (mobile layout, touch, mobile user agent) rendered at more than 1 device
# pixel per CSS pixel, so text stays sharp when the picture is scaled up to the phone's screen.
MOBILE_W = 400
MOBILE_DPR = 1.5
MOBILE_FRAME_GAP = 0.12  # sharp frames are bigger: at most ~8 a second
HOME_URL = "https://www.google.com/"
# The app's language -> what the browser tells websites (navigator.language, Accept-Language).
LANGS = {"en": "en-US,en", "it": "it-IT,it,en", "uk": "uk-UA,uk,en"}
SEARCH_URL = "https://www.google.com/search?q="
TLDS = ("com", "it", "org", "net", "io", "dev", "app", "co", "uk", "de", "fr", "es", "eu", "ua", "ru", "info", "me", "ai")

# The focused element, so the app can offer the keyboard when the user tapped a text field.
FOCUS_JS = """() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  const nonText = ['button','submit','checkbox','radio','range','color','file','image','reset','hidden'];
  const editable = el.isContentEditable || tag === 'textarea' || (tag === 'input' && !nonText.includes(type));
  if (!editable) return null;
  const label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || '';
  const r = el.getBoundingClientRect();
  return {kind: el.isContentEditable ? 'content' : tag === 'textarea' ? 'textarea' : (type || 'text'),
          label: String(label).slice(0, 60), secret: type === 'password',
          y: Math.round(Math.min(1, Math.max(0, (r.top + r.height / 2) / (innerHeight || 1))) * 1000) / 1000};
}"""

ROLE_WORDS = {"link": "link", "button": "button", "textbox": "text field", "searchbox": "search box",
              "combobox": "menu", "checkbox": "checkbox", "radio": "option", "tab": "tab", "menuitem": "menu item",
              "option": "option", "img": "image", "heading": "heading", "switch": "switch", "listbox": "list"}
# Outline lines look like `- link "Learn more" [ref=e13] [cursor=pointer]:`; after navigations the
# refs carry a frame prefix (`f1e2`).
_REF_LINE = re.compile(r'^\s*-\s+([A-Za-z]+)(?:\s+"((?:[^"\\]|\\.)*)")?[^\n]*?\[ref=([A-Za-z0-9]+)\]', re.M)


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
    """An address, or Google search words. `google com` (a phone keyboard ate the dot) is google.com."""
    url = url.strip()
    if not url:
        return HOME_URL
    if re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        return url
    m = re.match(r"^(?:www\s*\.?\s*)?([a-z0-9-]+)\s*[ ,]\s*(" + "|".join(TLDS) + r")(/\S*)?$", url, re.I)
    if m:
        return f"https://{m.group(1)}.{m.group(2).lower()}{m.group(3) or ''}"
    if " " in url or "." not in url:
        return SEARCH_URL + urllib.parse.quote_plus(url)
    return "https://" + url


def host_of(url: str) -> str:
    h = urllib.parse.urlparse(url).hostname or url
    return h[4:] if h.startswith("www.") else h


def quoted(s: str, n: int = 48) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return "“" + (s if len(s) <= n else s[: n - 1] + "…") + "”"


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
        self.last_hello_snap = 0.0
        self.latest: str | None = None
        self.frame_evt = asyncio.Event()
        self.quality = 55
        self.busy = False
        self.loading = False
        self.action = ""                  # caption of what Panda is doing ("Clicking “Search”")
        self.action_ts = 0.0
        self.last_agent_ts = 0.0          # when Panda last used the browser (the worker keeps a final frame)
        self.focus: dict | None = None    # the text field the user is typing into, if any
        self.refs: dict[str, tuple[str, str]] = {}  # ref -> (role, name) from the last page outline
        self.cfg_mtime = 0.0
        self.last_status = ""
        self.ua_major = "140"
        self.closing = False
        self.view = (VIEW_W, VIEW_H)       # current page size in CSS px (phone viewers get a tall, narrow page)
        self.lang = "en"
        self.mobile = False                # a phone is watching: real mobile page, touch taps
        self.dpr = 1.0                     # device pixels per CSS pixel of the live picture
        self.emu = None                    # CDP session of the current page for emulation and sharp captures
        self.xvfb: subprocess.Popen | None = None  # the virtual screen Chromium draws on
        self.pending_fit: tuple | None = None  # a panel resize that came while Panda was busy
        self.tab_ids: dict = {}            # page -> stable tab id (indexes shift when a tab closes)
        self.owner_pages: dict = {}        # agent (its kiro-cli process) -> its own tab, so chats don't share one
        self.wired: set = set()            # pages whose events are hooked up already
        self.next_tab = 1
        self.thumbs: dict[int, str] = {}   # tab id -> small JPEG of how it looked, for the tab overview

    # lifecycle ----------------------------------------------------------------
    def _virtual_screen(self) -> bool:
        """Start a private virtual screen (Xvfb), so Chromium runs with a window instead of headless.

        Google's sign-in refuses headless browsers ("This browser or app may not be secure"); the
        same Chromium with a screen is accepted. Falls back to headless when Xvfb is missing.
        """
        if os.environ.get("PANDA_BROWSER_HEADLESS") == "1":
            return False
        if os.environ.get("DISPLAY"):
            return True
        xvfb = shutil.which("Xvfb")
        if not xvfb:
            log("Xvfb missing, running headless")
            return False
        for n in range(99, 120):
            if Path(f"/tmp/.X11-unix/X{n}").exists() or Path(f"/tmp/.X{n}-lock").exists():
                continue
            self.xvfb = subprocess.Popen([xvfb, f":{n}", "-screen", "0", "1600x1800x24", "-nolisten", "tcp", "-ac"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(50):
                if Path(f"/tmp/.X11-unix/X{n}").exists():
                    os.environ["DISPLAY"] = f":{n}"
                    return True
                if self.xvfb.poll() is not None:
                    break
                time.sleep(0.1)
            with contextlib.suppress(Exception):
                self.xvfb.kill()
            self.xvfb = None
        log("virtual screen did not start, running headless")
        return False

    async def start(self):
        from playwright.async_api import async_playwright
        headless = not self._virtual_screen()  # first: Playwright's driver takes DISPLAY when it starts
        self.pw = await async_playwright().start()
        exe = os.environ.get("PANDA_CHROMIUM") or None
        args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-crash-reporter",
                "--disable-blink-features=AutomationControlled", "--lang=en-US", "--window-size=1600,1800",
                "--no-first-run", "--no-default-browser-check"]
        try:
            self.browser = await self.pw.chromium.launch(headless=headless, executable_path=exe, args=args)
        except Exception as exc:
            if "Executable doesn't exist" not in str(exc):
                raise
            log("chromium missing, installing…")
            subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], check=False)
            self.browser = await self.pw.chromium.launch(headless=headless, executable_path=exe, args=args)
        self.ua_major = self.browser.version.split(".")[0]
        await self._new_context(STATE_PATH if STATE_PATH.exists() else None)
        asyncio.create_task(self._frame_sender())
        asyncio.create_task(self._housekeeping())
        self._load_cfg(force=True)
        log("engine ready, chromium", self.browser.version, "headless" if headless else f"with screen {os.environ.get('DISPLAY')}")

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

    def _tid(self, page) -> int:
        if page not in self.tab_ids:
            self.tab_ids[page] = self.next_tab
            self.next_tab += 1
        return self.tab_ids[page]

    async def _thumb(self):
        """A small picture of the current tab, kept for the tab overview."""
        if not self.page or not self.emu or self.page.is_closed():
            return
        with contextlib.suppress(Exception):
            m = (await asyncio.wait_for(self.emu.send("Page.getLayoutMetrics"), 2))["cssVisualViewport"]
            w = m["clientWidth"]
            r = await asyncio.wait_for(self.emu.send("Page.captureScreenshot", {
                "format": "jpeg", "quality": 45, "optimizeForSpeed": True,
                "clip": {"x": m["pageX"], "y": m["pageY"], "width": w, "height": min(m["clientHeight"], w * 1.4),
                         "scale": min(1.0, 260 / max(1, w))}}), 3)
            self.thumbs[self._tid(self.page)] = r["data"]

    async def send_thumbs(self):
        await self._thumb()
        live = {self._tid(p) for p in self.ctx.pages}
        self.thumbs = {k: v for k, v in self.thumbs.items() if k in live}
        await self.link.broadcast("thumbs", {"thumbs": self.thumbs})

    async def _adopt(self, page):
        if self.page and self.page is not page and not self.page.is_closed():
            await self._thumb()  # remember how the tab we leave looked
        self.page = page
        self.focus = None
        await self._emulate()
        if page in self.wired:  # switching back to a known tab: its events are hooked up already
            await self._restart_screencast()
            return await self.push_status()
        self.wired.add(page)
        page.on("dialog", lambda d: asyncio.create_task(d.accept() if d.type in ("alert", "beforeunload") else d.dismiss()))
        page.on("download", lambda d: asyncio.create_task(self._download(d)))
        page.on("close", lambda: asyncio.create_task(self._page_closed(page)))
        page.on("framenavigated", lambda f: asyncio.create_task(self._navigated(page)) if f == page.main_frame else None)
        page.on("request", lambda r: self._nav_request(page, r, True))
        page.on("requestfailed", lambda r: self._nav_request(page, r, False))
        page.on("domcontentloaded", lambda _p: self._loading(page, False))
        page.on("load", lambda _p: self._loading(page, False))
        await self._restart_screencast()
        await self.push_status()

    def _nav_request(self, page, req, on: bool):
        try:
            if req.is_navigation_request() and req.frame == page.main_frame:
                self._loading(page, on)
        except Exception:  # service-worker requests have no frame
            pass

    def _loading(self, page, on: bool):
        if page is self.page and self.loading != on:
            self.loading = on
            asyncio.create_task(self.push_status())

    async def _navigated(self, page):
        if page is self.page:
            self.focus = None
            await self.push_status()

    async def _page_closed(self, page):
        # Ignore closes caused by shutting down or swapping the whole context.
        if self.closing or page.context is not self.ctx:
            return
        self.thumbs.pop(self.tab_ids.pop(page, 0), None)
        self.wired.discard(page)
        for o in [o for o, p in self.owner_pages.items() if p is page]:
            del self.owner_pages[o]
        if self.page is not page:  # a background tab closed: only the list changes
            return await self.push_status(force=True)
        try:
            pages = [p for p in self.ctx.pages if not p.is_closed()]
            await self._adopt(pages[-1] if pages else await self.ctx.new_page())
        except Exception as exc:
            log("could not recover from closed tab:", str(exc)[:100])

    async def _owner_page(self, owner: str):
        """Give each agent its own tab: the first time it browses it takes a free tab, or opens one."""
        p = self.owner_pages.get(owner)
        if p is None or p.is_closed():
            taken = [x for o, x in self.owner_pages.items() if o != owner and not x.is_closed()]
            cur = self.page
            p = cur if cur and not cur.is_closed() and cur not in taken else await self.ctx.new_page()
            self.owner_pages[owner] = p
        if p is not self.page:
            await self._adopt(p)  # the viewer follows the agent that is acting

    async def _drop_gone_owners(self):
        """An agent whose process ended (chat closed, helper done) gives its tab back; the last tab stays."""
        for o, p in list(self.owner_pages.items()):
            pid = o.split(":", 1)[1] if o.startswith("pid:") else ""
            alive = True
            if pid.isdigit():
                try:
                    os.kill(int(pid), 0)
                except OSError:
                    alive = False
            if alive:
                continue
            del self.owner_pages[o]
            if not p.is_closed() and len([x for x in self.ctx.pages if not x.is_closed()]) > 1:
                with contextlib.suppress(Exception):
                    await p.close()

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
                await self._drop_gone_owners()
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
        small = 0
        while True:
            await self.frame_evt.wait()
            self.frame_evt.clear()
            sharp = self.dpr > 1
            gap = (MOBILE_FRAME_GAP if sharp else MIN_FRAME_GAP) - (time.time() - last)
            if gap > 0:
                await asyncio.sleep(gap)
            data, self.latest = self.latest, None
            if not data or not self.viewer_active():
                continue
            if sharp:
                # The screencast only says "the page changed"; the picture sent is a device-pixel capture.
                data = await self._capture(self.quality) or data
                if len(data) > MAX_FRAME_B64 and self.quality <= 35 and self.dpr > 1:
                    self.dpr = max(1.0, round(self.dpr - 0.25, 2))  # still too big: fewer pixels
                    self.quality = 50
                    await self._emulate()
                    continue
            if len(data) > MAX_FRAME_B64 and self.quality > 25:  # page too detailed: compress harder
                self.quality -= 10
                small = 0
                asyncio.create_task(self._restart_screencast())
                continue
            # Simple pages again: win back sharpness, slowly.
            small = small + 1 if len(data) < MAX_FRAME_B64 * 0.45 else 0
            if small > 40 and self.quality < BEST_QUALITY:
                self.quality = min(BEST_QUALITY, self.quality + 5)
                small = 0
                asyncio.create_task(self._restart_screencast())
            last = time.time()
            await self.link.broadcast("frame", {"d": data, "w": self.view[0], "h": self.view[1]})

    async def snap_frame(self):
        """One immediate frame (used when a viewer opens, or the page is idle)."""
        if not self.viewer_active():
            return
        data = await self._capture(self.quality)
        if data:
            self.latest = data
            self.frame_evt.set()

    async def push_status(self, force=False):
        if not self.page:
            return
        try:
            title = await asyncio.wait_for(self.page.title(), 2)
        except Exception:
            title = ""
        pages = list(self.ctx.pages)

        async def name(p):
            if p is self.page:
                return title
            try:
                return await asyncio.wait_for(p.title(), 0.4)
            except Exception:
                return ""
        names = await asyncio.gather(*(name(p) for p in pages)) if len(pages) > 1 else [title]
        tabs = [{"i": i, "id": self._tid(p), "u": p.url[:200], "t": (names[i] or "")[:80], "active": p is self.page}
                for i, p in enumerate(pages)]
        recent = time.time() - self.action_ts < 6
        st = {"url": self.page.url, "title": title[:120], "tabs": tabs, "control": self.control,
              "message": self.message, "busy": self.busy, "loading": self.loading, "viewer": self.viewer_active(),
              "action": self.action if (self.busy or recent) else "", "focus": self.focus,
              "w": self.view[0], "h": self.view[1]}
        key = json.dumps(st, sort_keys=True)
        if force or key != self.last_status:
            self.last_status = key
            await self.link.broadcast("status", st)

    async def fit(self, mode: str, vw: float, vh: float):
        """Shape the page like the viewer's panel. Always the DESKTOP site, also for phones: switching
        to the mobile site reloaded the page and broke Panda's clicks mid-task. While Panda is using
        the browser the page keeps its size; the new shape is applied once Panda is done."""
        if self.busy:
            self.pending_fit = (mode, vw, vh)
            return
        self.pending_fit = None
        if mode == "mobile":  # a phone only watches: the page stays exactly as it is
            return
        mobile = False
        if mobile:
            w = MOBILE_W
            h = int(max(560, min(1100, w * vh / vw)))
        else:
            # Tablets and big screens: keep the desktop width, match the panel's shape so the page fills it.
            w = VIEW_W
            h = int(max(600, min(1400, w * vh / vw))) if vw > 50 and vh > 50 else VIEW_H
        if (w, h) == self.view and mobile == self.mobile:
            return
        was_mobile = self.mobile
        self.view, self.mobile = (w, h), mobile
        if mobile != was_mobile:
            self.dpr = MOBILE_DPR if mobile else 1.0
        await self._emulate()
        if mobile != was_mobile:  # the site serves a different layout to phones: load it again
            with contextlib.suppress(Exception):
                if self.page.url.startswith("http"):
                    await self.page.reload(wait_until="domcontentloaded", timeout=15000)
        await self._restart_screencast()

    def _ua(self) -> str:
        if self.mobile:
            return (f"Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
                    f"Chrome/{self.ua_major}.0.0.0 Mobile Safari/537.36")
        return f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self.ua_major}.0.0.0 Safari/537.36"

    async def _emulate(self):
        """Apply the page shape to the current page: size, pixel density, mobile/touch, user agent.

        Playwright's set_viewport_size keeps its own idea of the size in step; the CDP override after
        it adds what Playwright can't change per page (density, mobile, touch, user agent).
        """
        page = self.page
        if not page:
            return
        w, h = self.view
        with contextlib.suppress(Exception):
            await page.set_viewport_size({"width": w, "height": h})
        try:
            if not self.emu or getattr(self.emu, "_panda_page", None) is not page:
                if self.emu:
                    with contextlib.suppress(Exception):
                        await self.emu.detach()
                self.emu = await self.ctx.new_cdp_session(page)
                self.emu._panda_page = page
            # Density stays 1 here (Playwright's own session also sets it); sharpness comes from the
            # capture scale in _capture, which re-rasterizes the visible area at self.dpr.
            await self.emu.send("Emulation.setDeviceMetricsOverride", {
                "width": w, "height": h, "deviceScaleFactor": 1, "mobile": self.mobile,
                "screenWidth": w, "screenHeight": h})
            await self.emu.send("Emulation.setTouchEmulationEnabled", {"enabled": self.mobile, "maxTouchPoints": 5})
            brands = [{"brand": "Chromium", "version": self.ua_major}, {"brand": "Google Chrome", "version": self.ua_major},
                      {"brand": "Not.A/Brand", "version": "99"}]
            await self.emu.send("Emulation.setUserAgentOverride", {
                "userAgent": self._ua(), "acceptLanguage": LANGS.get(self.lang, "en-US,en"), "platform": "Linux armv8l" if self.mobile else "Linux x86_64",
                "userAgentMetadata": {"brands": brands, "fullVersion": self.browser.version, "platform": "Android" if self.mobile else "Linux",
                                      "platformVersion": "14.0.0" if self.mobile else "", "architecture": "" if self.mobile else "x86",
                                      "model": "Pixel 8" if self.mobile else "", "mobile": self.mobile}})
        except Exception as exc:
            log("emulation failed:", str(exc)[:120])
            self.emu = None

    async def _capture(self, quality: int) -> str | None:
        """A sharp picture of the visible page in device pixels (base64 JPEG)."""
        if self.emu:
            with contextlib.suppress(Exception):
                req = {"format": "jpeg", "quality": quality, "optimizeForSpeed": True}
                if self.dpr > 1:  # the visible area (in page coordinates) re-rendered at dpr
                    m = (await asyncio.wait_for(self.emu.send("Page.getLayoutMetrics"), 3))["cssVisualViewport"]
                    req["clip"] = {"x": m["pageX"], "y": m["pageY"], "width": m["clientWidth"],
                                   "height": m["clientHeight"], "scale": self.dpr}
                r = await asyncio.wait_for(self.emu.send("Page.captureScreenshot", req), 5)
                return r.get("data")
        with contextlib.suppress(Exception):
            return base64.b64encode(await self.page.screenshot(type="jpeg", quality=quality, timeout=5000)).decode()
        return None

    async def tap(self, x: float, y: float):
        """A finger tap on phones (sites listen for touch), a mouse click on desktops."""
        if self.mobile and self.emu:
            try:
                await self.emu.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
                await asyncio.sleep(0.05)
                await self.emu.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                return
            except Exception as exc:
                log("touch failed, clicking instead:", str(exc)[:80])
        await self.page.mouse.click(x, y)

    # what Panda is doing, for the live view ----------------------------------
    async def announce(self, kind: str, caption: str, box: dict | None = None, **extra):
        self.action, self.action_ts = caption[:160], time.time()
        payload = {"k": kind, "c": self.action, "t": int(self.action_ts * 1000), **extra}
        if box:
            payload.update(box)
        await self.link.broadcast("action", payload)
        await self.push_status(force=True)
        if box and self.viewer_active():
            await asyncio.sleep(CURSOR_LEAD)

    async def _box(self, loc) -> dict | None:
        """The element's box as fractions of the visible page (scrolled into view first)."""
        try:
            await loc.scroll_into_view_if_needed(timeout=3000)
            b = await loc.bounding_box(timeout=2000)
        except Exception:
            return None
        if not b or b["width"] <= 0 or b["height"] <= 0:
            return None
        w, h = self.view
        r4 = lambda v: round(min(1.0, max(0.0, v)), 4)  # noqa: E731
        return {"x": r4(b["x"] / w), "y": r4(b["y"] / h), "w": r4(b["width"] / w), "h": r4(b["height"] / h)}

    def _target(self, ref: str) -> str:
        role, name = self.refs.get(ref, ("", ""))
        word = ROLE_WORDS.get(role, "")
        if name:
            return f"{quoted(name)}{' ' + word if word in ('link', 'button', 'tab', 'checkbox', 'switch') else ''}"
        return f"the {word}" if word else "the page"

    def _remember_refs(self, outline: str):
        refs = {}
        for role, name, ref in _REF_LINE.findall(outline or ""):
            refs[ref] = (role, name.replace('\\"', '"'))
        if refs:
            self.refs = refs

    # input from the phone -----------------------------------------------------
    async def on_input(self, p: dict):
        t = p.get("t")
        try:
            if t == "hello":
                first = not self.viewer_active()
                self.last_hello = time.time()
                # Only the open browser panel (a real size) sets the language, so a second device or a
                # chat card can't flip it back and forth. No reload: the next page you open uses it.
                lang = str(p.get("lang") or "")[:2]
                sized = float(p.get("vw") or 0) > 50 and float(p.get("vh") or 0) > 50
                if sized and lang in LANGS and lang != self.lang:
                    self.lang = lang
                    log("browser language ->", lang)
                    with contextlib.suppress(Exception):
                        await self.ctx.set_extra_http_headers({"Accept-Language": LANGS[lang]})
                    await self._emulate()
                # Only a viewer with a real size (the full panel) changes the page's shape; a chat card
                # alone keeps it, so closing the panel on a phone doesn't reload the site as desktop.
                if p.get("mode") and float(p.get("vw") or 0) > 50 and float(p.get("vh") or 0) > 50:
                    await self.fit(str(p["mode"]), float(p["vw"]), float(p["vh"]))
                if not self.cdp:
                    await self._restart_screencast()
                # A hello repeats every few seconds; only a new viewer needs a fresh frame right away.
                if first and self.page.url == "about:blank" and not self.busy:
                    asyncio.create_task(self._goto(HOME_URL))  # an empty browser opens on Google
                if first or time.time() - self.last_hello_snap > 6:
                    self.last_hello_snap = time.time()
                    await self.push_status(force=True)
                    await self.snap_frame()
                return
            if t == "takeover":
                self.control, self.message = "user", ""
                return await self.push_status(force=True)
            if t == "done":
                self.control, self.message, self.focus = "agent", "", None
                self.done_evt.set()
                return await self.push_status(force=True)
            if t == "tabs":  # the tab overview opened: send fresh pictures of the tabs
                return await self.send_thumbs()
            # Tabs can be opened, switched and closed whenever Panda isn't using the browser.
            tab_op = t in ("tab", "newtab", "closetab")
            if self.control == "agent" and not (tab_op and not self.busy):
                return  # otherwise view only until the user takes over
            page = self.page
            if t in ("click", "dblclick"):
                x, y = float(p["x"]) * self.view[0], float(p["y"]) * self.view[1]
                if t == "click":
                    await self.tap(x, y)
                else:
                    await page.mouse.dblclick(x, y)
                await asyncio.sleep(0.15)
                await self._report_focus()
            elif t == "scroll":
                await page.mouse.move(float(p.get("x", 0.5)) * self.view[0], float(p.get("y", 0.5)) * self.view[1])
                await page.mouse.wheel(float(p.get("dx", 0)), float(p.get("dy", 0)))
            elif t == "key":
                await page.keyboard.press(str(p["key"])[:30])
                if p["key"] in ("Tab", "Shift+Tab", "Enter", "Escape"):
                    await asyncio.sleep(0.1)
                    await self._report_focus()
            elif t == "text":
                await page.keyboard.insert_text(str(p["text"])[:2000])
            elif t == "nav":
                url = normalize(str(p["url"]))
                ok, why = url_ok(url)
                if ok:
                    asyncio.create_task(self._goto(url))
                else:
                    self.message = why
                    await self.push_status(force=True)
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
                url = normalize(str(p.get("url") or ""))
                ok, _why = url_ok(url)
                await self._adopt(await self.ctx.new_page())
                asyncio.create_task(self._goto(url if ok else HOME_URL))
            elif t == "closetab":
                pages = self.ctx.pages
                i = int(p.get("i", pages.index(self.page) if self.page in pages else -1))
                if 0 <= i < len(pages):
                    await pages[i].close()
                await asyncio.sleep(0.15)
                await self.push_status(force=True)
            await self.snap_frame()
        except Exception as exc:
            log("input failed:", t, str(exc)[:120])

    async def _report_focus(self):
        try:
            f = await asyncio.wait_for(self.page.evaluate(FOCUS_JS), 2)
        except Exception:
            f = None
        self.focus = f if isinstance(f, dict) else None
        await self.push_status(force=True)

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
        self._remember_refs(s)
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

    async def settle(self, extra=0.15):
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("domcontentloaded", timeout=4000)
        await asyncio.sleep(extra)
        await self.snap_frame()

    def _loc(self, ref: str):
        return self.page.locator(f"aria-ref={ref}")

    async def do(self, req: dict) -> dict:
        op = req.get("op")
        if op == "status":
            return {"ok": True, "control": self.control, "viewer": self.viewer_active(), "joined": self.link.joined,
                    "last_agent_ts": self.last_agent_ts, **await self.summary(snap=False)}
        if op == "frame":  # read-only: the worker keeps the final screen of a turn with the reply
            q = max(30, min(90, int(req.get("quality") or 70)))
            try:
                jpg = await self.page.screenshot(type="jpeg", quality=q, timeout=8000)
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            return {"ok": True, "jpeg": base64.b64encode(jpg).decode(), "w": self.view[0], "h": self.view[1],
                    **{k: v for k, v in (await self.summary(snap=False)).items() if k in ("url", "title")}}
        if self.control == "user":  # the user took over: wait for them to give it back
            # Wait briefly, not 15 minutes: a silent long wait looked like Panda had stopped answering.
            # After that the agent hears why and tells the user to press Done.
            self.message = "Panda wants the browser back. Press Done when you're finished."
            await self.push_status(force=True)
            self.done_evt.clear()
            try:
                await asyncio.wait_for(self.done_evt.wait(), float(os.environ.get("PANDA_BROWSER_USER_WAIT", 45)))
            except asyncio.TimeoutError:
                return {"ok": False, "error": "The user has control of the browser (they tapped Take over) and has not "
                        "pressed Done. Do not retry in a loop: tell the user to press Done in the browser panel, "
                        "then stop or continue without the browser."}
        async with self.lock:
            self.busy = True
            self.last_agent_ts = time.time()
            await self.push_status(force=True)
            owner = str(req.get("owner") or "")[:40]
            try:
                if owner and op not in ("forget", "save"):
                    await self._owner_page(owner)
                out = await self._do(op, req)
                # A tab the agent switched to, opened, or that its click popped up is now its tab
                # (unless another agent owns it).
                if owner and self.page and not self.page.is_closed() and \
                        all(p is not self.page for o, p in self.owner_pages.items() if o != owner):
                    self.owner_pages[owner] = self.page
                return out
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                        **{k: v for k, v in (await self.summary(snap=False)).items() if k in ("url", "title")}}
            finally:
                self.busy = False
                self.last_agent_ts = time.time()
                if self.pending_fit:
                    with contextlib.suppress(Exception):
                        await self.fit(*self.pending_fit)
                await self.push_status(force=True)

    async def _do(self, op: str, r: dict) -> dict:
        page = self.page
        want_snap = not r.get("no_snap")
        if op == "open":
            url = normalize(r["url"])
            ok, why = url_ok(url)
            if not ok:
                return {"ok": False, "error": why}
            await self.announce("nav", f"Opening {host_of(url)}", url=url[:300])
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self.settle(0.4)
        elif op == "snap":
            await self.announce("look", "Looking at the page")
        elif op == "click":
            if r.get("ref"):
                loc = self._loc(r["ref"])
                await self.announce("click", f"Clicking {self._target(r['ref'])}", await self._box(loc))
                await loc.click(timeout=8000)
            else:
                x, y = float(r["x"]), float(r["y"])
                box = {"x": round(x / self.view[0], 4), "y": round(y / self.view[1], 4), "w": 0, "h": 0}
                await self.announce("click", "Clicking on the page", box)
                await page.mouse.click(x, y)
            await self.settle()
        elif op == "type":
            loc = self._loc(r["ref"])
            text = r.get("text", "")
            secret = False
            with contextlib.suppress(Exception):
                secret = (await loc.get_attribute("type", timeout=1500) or "").lower() == "password"
            what = "a password" if secret else quoted(text, 40)
            await self.announce("type", f"Typing {what} into {self._target(r['ref'])}", await self._box(loc))
            try:
                await loc.fill(text, timeout=6000)
            except Exception:
                await loc.click(timeout=5000)
                await page.keyboard.type(text, delay=15)
            if r.get("submit"):
                await self.announce("key", "Pressing Enter", key="Enter")
                await page.keyboard.press("Enter")
            await self.settle()
        elif op == "select":
            loc = self._loc(r["ref"])
            values = r.get("values") or [r.get("value", "")]
            await self.announce("click", f"Choosing {quoted(', '.join(map(str, values)), 40)} in {self._target(r['ref'])}",
                                await self._box(loc))
            await loc.select_option([str(v) for v in values], timeout=8000)
            await self.settle()
        elif op == "hover":
            loc = self._loc(r["ref"])
            await self.announce("hover", f"Pointing at {self._target(r['ref'])}", await self._box(loc))
            await loc.hover(timeout=8000)
            await self.settle(0.15)
        elif op == "press":
            await self.announce("key", f"Pressing {r['key']}", key=str(r["key"])[:30])
            await page.keyboard.press(r["key"])
            await self.settle()
        elif op == "scroll":
            dy = float(r.get("dy", 600))
            await self.announce("scroll", "Scrolling down" if dy >= 0 else "Scrolling up", dy=dy)
            await page.mouse.move(self.view[0] / 2, self.view[1] / 2)
            await page.mouse.wheel(0, dy)
            await self.settle(0.15)
        elif op in ("back", "forward", "reload"):
            await self.announce("nav", {"back": "Going back", "forward": "Going forward", "reload": "Reloading the page"}[op])
            await {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[op](timeout=15000)
            await self.settle()
        elif op == "text":
            await self.announce("read", "Reading the page")
            txt = await page.evaluate("document.body ? document.body.innerText : ''")
            limit = 40000 if r.get("full") else 9000
            out = await self.summary(snap=False)
            out["text"] = txt[:limit] + ("\n… (truncated; use --full)" if len(txt) > limit else "")
            return out
        elif op == "shot":
            await self.announce("shot", "Taking a screenshot")
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", r.get("name") or f"screenshot-{int(time.time())}.png")
            if not name.lower().endswith((".png", ".jpg")):
                name += ".png"
            path = OUT_DIR / name
            await page.screenshot(path=str(path), full_page=bool(r.get("full")))
            if r.get("full"):
                await self._emulate()  # a full-page shot resets the page's emulation
            return {**await self.summary(snap=False), "saved": str(path)}
        elif op == "tabs":
            return await self.summary(snap=False)
        elif op == "tab":
            await self.announce("nav", "Switching tabs")
            pages = self.ctx.pages
            await self._adopt(pages[int(r["i"])])
            await self.settle(0.2)
        elif op == "newtab":
            await self.announce("nav", "Opening a new tab")
            await self._adopt(await self.ctx.new_page())
            url = normalize(r.get("url") or "")  # no address: Google
            ok, why = url_ok(url)
            if not ok:
                return {"ok": False, "error": why}
            await self.announce("nav", f"Opening {host_of(url)}", url=url[:300])
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self.settle(0.3)
        elif op == "closetab":
            await self.announce("nav", "Closing the tab")
            await page.close()
            await asyncio.sleep(0.2)
        elif op == "wait":
            if r.get("text"):
                await self.announce("wait", f"Waiting for {quoted(r['text'], 40)}")
                await page.get_by_text(r["text"]).first.wait_for(timeout=float(r.get("secs", 15)) * 1000)
            else:
                await self.announce("wait", "Waiting for the page")
                await asyncio.sleep(min(float(r.get("secs", 2)), 30))
            await self.snap_frame()
        elif op == "handoff":
            self.control, self.message = "needs_user", (r.get("message") or "Panda needs your help")[:300]
            await self.announce("handoff", self.message)
            self.done_evt.clear()
            self.busy = False
            await self.push_status(force=True)
            try:
                await asyncio.wait_for(self.done_evt.wait(), float(r.get("timeout", 600)))
            except asyncio.TimeoutError:
                self.control, self.message = "agent", ""
                return {"ok": False, "error": "the user did not respond in time"}
            self.busy = True
            await self.save_state()
        elif op == "forget":
            STATE_PATH.unlink(missing_ok=True)
            self.owner_pages.clear()
            self.wired.clear()
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
    if eng.xvfb:
        eng.xvfb.terminate()


if __name__ == "__main__":
    asyncio.run(main())
