"""UI check of the built app with Supabase fully mocked (no network to the real project).

Serves app/dist, fakes sign-in, REST, Storage, the session function and the Realtime websocket
(protocol 2.0.0, the one supabase-js uses), and plays Panda's browser engine on the private topic.
Takes screenshots and checks the panda animations with the Web Animations API.

    python tools/screenshots/mock_ui.py <out-dir> [fixtures-dir]

Fixtures (optional): crew_settings.json / crew_commands.json captured from a real gateway with
worker.crew_settings() / worker.crew_commands(); built-in samples are used otherwise.
"""
import asyncio
import contextlib
import base64
import functools
import http.server
import json
import os
import re
import sys
import threading
import time
import urllib.parse

from playwright.async_api import async_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIST = os.path.join(ROOT, "app", "dist")
# PANDA_APP_URL=https://panda-app-gamma.vercel.app checks the deployed build instead of app/dist
# (Supabase is still fully mocked, so no real account or data is touched).
APP = os.environ.get("PANDA_APP_URL", "http://127.0.0.1:4179").rstrip("/")
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/panda-shots"
FIX = sys.argv[2] if len(sys.argv) > 2 else ""
CHROME = os.environ.get("PANDA_CHROMIUM") or None
SB = "https://ufvskhagspdjslomzwjh.supabase.co"
UID = "11111111-2222-4333-8444-555555555555"
TOPIC = "pbr-mockui"
NOW = time.time()
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))  # noqa: E731
os.makedirs(OUT, exist_ok=True)
REPORT: dict = {}


def fixture(name, default):
    p = os.path.join(FIX, name) if FIX else ""
    return json.load(open(p)) if p and os.path.exists(p) else default


CREW_SETTINGS = fixture("crew_settings.json", {"items": [], "version": "0.11.0", "backend": "kiro"})
CREW_COMMANDS = fixture("crew_commands.json", {"commands": [{"name": "/compact", "description": "Compact conversation to free context"}], "backend": "kiro"})
CHAT_A, CHAT_B = "aaaaaaaa-0000-4000-8000-000000000001", "aaaaaaaa-0000-4000-8000-000000000002"
USAGE = {"account": {"credits_used": 1234.6, "credits_plan": 2000, "percentage": 61.7, "plan": "KIRO PRO+", "resets": "2026-11-01",
                     "bonus_credits": [{"name": "Welcome bonus", "used": 120, "total": 500, "days_left": 9}]},
         "daily": [{"date": time.strftime("%Y-%m-%d", time.gmtime(NOW - 86400 * i)), "credits": c} for i, c in enumerate([14.2, 30.1, 8.4, 0, 22.9])],
         "at": iso(NOW - 120)}
CHATS = [
    {"id": CHAT_A, "owner": UID, "title": "Red panda research", "model": "claude-sonnet-5.5", "effort": "medium", "pinned": False,
     "archived": False, "slot_id": "chat-1", "project_id": None, "created_at": iso(NOW - 3600), "updated_at": iso(NOW - 60)},
    {"id": CHAT_B, "owner": UID, "title": "Weekly plan", "model": "claude-opus-5.5", "effort": "high", "pinned": True,
     "archived": False, "slot_id": "chat-2", "project_id": None, "created_at": iso(NOW - 86400), "updated_at": iso(NOW - 7200)},
]


def msg(i, role, content, **kw):
    return {"id": i, "chat_id": CHAT_A, "owner": UID, "role": role, "content": content, "thinking": "", "status": kw.pop("status", "done"),
            "reply_to": kw.pop("reply_to", None), "decision": None, "cancel": False, "meta": kw.pop("meta", {}), "created_at": iso(NOW - 3000 + i * 60)}


MESSAGES = [
    msg(1, "user", "Open the red panda page on Wikipedia and tell me how many are left in the wild."),
    msg(2, "assistant", "I opened the **Red panda** article. The IUCN estimates **fewer than 10,000** mature red pandas remain in the wild, "
        "and the population is decreasing because of habitat loss.\n\n[OPTIONS: Make a one-pager | Find photos | Compare with giant pandas]",
        reply_to=1, meta={"model": "claude-sonnet-5.5", "secs": 21.4, "credits": 1.98,
                          "turn_stats": {"elapsed_ms": 21400, "credits": 1.98, "model": "claude-sonnet-5.5"},
                          "tools": ["Running: python3 ~/.kiro/crew/skills/panda-browser/pb.py open en.wikipedia.org/wiki/Red_panda",
                                    "Running: python3 ~/.kiro/crew/skills/panda-browser/pb.py text"],
                          "usage": {"pct": 23.4, "used_tokens": 46800, "window_tokens": 200000},
                          "browser": {"url": "https://en.wikipedia.org/wiki/Red_panda", "title": "Red panda - Wikipedia",
                                      "shot": f"{UID}/browser/{CHAT_A}/2.jpg", "w": 1024, "h": 720, "at": iso(NOW - 2800)}}),
    msg(3, "user", "/compact", meta={"command": "/compact"}),
    msg(4, "assistant", "✅ Conversation compacted: kept the red panda facts and the open questions.", reply_to=3,
        meta={"command": "/compact", "secs": 6.2}),
    msg(5, "user", "Now check the WWF page and find how I can adopt one."),
    msg(6, "assistant", "", reply_to=5, status="streaming",
        meta={"model": "claude-sonnet-5.5", "tools": ["Running: python3 ~/.kiro/crew/skills/panda-browser/pb.py open worldwildlife.org",
                                                      "Running: python3 ~/.kiro/crew/skills/panda-browser/pb.py click e41"]}),
]
DEV_CHAT = "aaaaaaaa-0000-4000-8000-0000000000de"
SETTINGS = {"theme": "light", "browserTopic": TOPIC, "developerChatId": DEV_CHAT, "name": "Alex", "showThinking": False, "approvals": "ask"}
SESSION = {"id": "sess-1", "owner": UID, "status": "running", "stop_requested": False, "started_at": iso(NOW - 1500),
           "heartbeat_at": iso(NOW - 5), "stopped_at": None, "info": {"usage": USAGE, "crew": "0.11.0"}}
COMMAND_RESULTS = {"crew.commands": CREW_COMMANDS, "crew.settings": CREW_SETTINGS, "usage": USAGE,
                   "side": {"answer": "Yes — adoption kits start at **$60** and include a plush and certificate."},
                   "crew.set": {"ok": True}}


def jwt():
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'HS256', 'typ': 'JWT'})}.{enc({'sub': UID, 'exp': int(NOW) + 86400, 'role': 'authenticated', 'aud': 'authenticated', 'email': 'you@example.com'})}.sig"


USER = {"id": UID, "aud": "authenticated", "role": "authenticated", "email": "you@example.com", "app_metadata": {}, "user_metadata": {},
        "created_at": iso(NOW - 864000)}
SESSION_TOKEN = {"access_token": jwt(), "token_type": "bearer", "expires_in": 86400, "expires_at": int(NOW) + 86400,
                 "refresh_token": "fake-refresh", "user": USER}


class State:
    theme = "light"

    def __init__(self):
        self.commands = {}
        self.next_cmd = 1
        self.sockets = []
        self.inputs = []
        self.control = "agent"
        self.frames = {}
        self.mobile = False


ST = State()


def pg_filter(qs: dict, key: str) -> str:
    v = qs.get(key, [""])[0]
    return v.split(".", 1)[1] if "." in v else v


async def handle_route(route):
    req = route.request
    u = urllib.parse.urlparse(req.url)
    qs = urllib.parse.parse_qs(u.query)
    path = u.path
    single = "vnd.pgrst.object" in (req.headers.get("accept") or "")
    ok = lambda body, status=200: route.fulfill(status=status, content_type="application/json", body=json.dumps(body))  # noqa: E731
    if path.startswith("/auth/v1/user"):
        return await ok(USER)
    if path.startswith("/auth/v1/token"):
        return await ok(SESSION_TOKEN)
    if path.startswith("/functions/v1/session"):
        return await ok({"session": SESSION, "alive": True})
    if path.startswith("/storage/v1/object/sign/") and req.method == "POST":
        return await ok({"signedURL": path.replace("/storage/v1", "") + "?token=mock"})
    if path.startswith("/storage/v1/object/sign/"):
        return await route.fulfill(status=200, content_type="image/jpeg", body=ST.frames.get("desktop_end", b""))
    if not path.startswith("/rest/v1/"):
        return await ok({})
    table = path.split("/rest/v1/")[1]
    if table == "rpc/list_secrets":
        return await ok([{"name": "OPENAI_API_KEY", "note": "", "updated_at": iso(NOW)},
                         {"name": "DEV__VERCEL_TOKEN", "note": "developer", "updated_at": iso(NOW)},
                         {"name": "DEV__SUPABASE_SECRET_KEY", "note": "developer", "updated_at": iso(NOW)}])
    if table == "chats":
        rows = CHATS + [{**CHATS[0], "id": DEV_CHAT, "title": "Developer", "pinned": False}]
        return await ok(rows if req.method == "GET" else ([] if not single else CHATS[0]))
    if table == "messages":
        if req.method != "GET":
            return await ok({} if single else [])
        if "meta->credits" in (qs.get("select", [""])[0]) or "credits:meta->credits" in u.query:
            rows = [{"created_at": iso(NOW - 3600 * h), "chat_id": CHAT_A if h % 2 else CHAT_B, "credits": c,
                     "model": "claude-sonnet-5.5" if h % 3 else "claude-opus-5.5"} for h, c in enumerate([1.98, 0.42, 2.7, 0.9, 5.1, 1.2, 3.3, 0.6])]
            return await ok(rows)
        cid = pg_filter(qs, "chat_id")
        return await ok([m for m in MESSAGES if m["chat_id"] == cid] if cid else [])
    if table == "worker_sessions":
        return await ok([SESSION])
    if table == "user_settings":
        st = {**SETTINGS, "theme": ST.theme}
        return await ok({"settings": st} if single else [{"settings": st}])
    if table == "commands":
        if req.method == "POST":
            body = json.loads(req.post_data or "{}")
            cid = ST.next_cmd
            ST.next_cmd += 1
            ST.commands[cid] = body
            row = {"id": cid, **body, "status": "pending"}
            return await ok(row if single else [row])
        cid = int(pg_filter(qs, "id") or 0)
        cmd = ST.commands.get(cid, {})
        op, cargs = cmd.get("op", ""), cmd.get("args", {}) or {}
        result = COMMAND_RESULTS.get(op, {"ok": True})
        if op == "crew.set":
            result = {"ok": True, "path": cargs.get("path"), "value": cargs.get("value")}
        elif op == "term":
            inp = str(cargs.get("input", ""))
            out = {"ls": "README.md  work/  outputs/  projects/", "pwd": "/root", "df -h": "Filesystem  Size  Used  Avail\n/dev/sda1   20G   7.2G   13G"}.get(inp, f"$ {inp}\n(ran on the worker)")
            result = {"output": out, "rc": 0, "cwd": "/root/outputs", "home": "/root"}
        elif op == "fs.list":
            p = cargs.get("path") or "/root"
            result = {"path": p, "rel": "~" if p in ("/root", None) else "~/" + p.split("/root/")[-1], "home": "/root",
                      "parent": None if p in ("/root", None) else "/root", "quick": ["outputs", "work", "projects"],
                      "entries": [{"name": "outputs", "dir": True, "size": 0, "mtime": NOW}, {"name": "work", "dir": True, "size": 0, "mtime": NOW},
                                  {"name": "README.md", "dir": False, "size": 2048, "mtime": NOW},
                                  {"name": "Red-Pandas.pptx", "dir": False, "size": 184320, "mtime": NOW},
                                  {"name": "secrets.env", "dir": False, "size": 96, "mtime": NOW, "sensitive": True}]}
        elif op == "fs.read":
            result = {"path": cargs.get("path"), "rel": "~/README.md", "name": "README.md", "size": 2048,
                      "text": "# Panda\n\nYour calm, creative assistant.\n"}
        row = {"status": "done", "result": result}
        return await ok(row if single else [row])
    return await ok([] if not single else {})


def ws_broadcast(event, payload):
    frame = json.dumps([None, None, f"realtime:{TOPIC}", "broadcast", {"type": "broadcast", "event": event, "payload": payload}])
    for ws in list(ST.sockets):
        try:
            ws.send(frame)
        except Exception:
            pass


def decode_push(data: bytes):
    """supabase-js 'userBroadcastPush' binary frame -> (event, payload)."""
    try:
        kind, jl, rl, tl, el, ml, enc = data[:7]
        if kind != 3:
            return None
        off = 7 + jl + rl
        off += tl
        event = data[off:off + el].decode()
        off += el + ml
        payload = json.loads(data[off:].decode()) if enc == 1 else None
        return event, payload
    except Exception:
        return None


def on_ws(ws):
    ST.sockets.append(ws)

    def handle(message):
        if isinstance(message, (bytes, bytearray)):
            dec = decode_push(bytes(message))
            if dec and dec[0] == "input":
                ST.inputs.append(dec[1])
                on_input(dec[1])
            return
        try:
            join_ref, ref, topic, event, payload = json.loads(message)
        except Exception:
            return
        if event == "phx_join":
            pcs = ((payload or {}).get("config") or {}).get("postgres_changes") or []
            resp = {"postgres_changes": [{**b, "id": 1000 + i} for i, b in enumerate(pcs)]}
            ws.send(json.dumps([join_ref, ref, topic, "phx_reply", {"status": "ok", "response": resp}]))
        elif event in ("heartbeat", "access_token") or topic == "phoenix":
            ws.send(json.dumps([join_ref, ref, topic, "phx_reply", {"status": "ok", "response": {}}]))
        elif event == "broadcast" and isinstance(payload, dict) and payload.get("event") == "input":
            ST.inputs.append(payload.get("payload"))
            on_input(payload.get("payload") or {})
        elif event == "phx_leave":
            ws.send(json.dumps([join_ref, ref, topic, "phx_reply", {"status": "ok", "response": {}}]))

    ws.on_message(handle)


def status(control=None, **kw):
    st = {"url": "https://en.wikipedia.org/wiki/Red_panda", "title": "Red panda - Wikipedia",
          "tabs": [{"i": 0, "u": "https://en.wikipedia.org/wiki/Red_panda", "active": True}], "control": control or ST.control,
          "message": "", "busy": True, "loading": False, "viewer": True, "action": "Clicking “Conservation status” link",
          "focus": None, "w": 1024, "h": 720}
    st.update(kw)
    return st


def on_input(p):
    t = (p or {}).get("t")
    if t == "takeover":
        ST.control = "user"
        ws_broadcast("status", status("user", busy=False, action="", focus={"kind": "search", "label": "Search Wikipedia", "y": 0.06},
                                      **({"w": 400, "h": 720} if ST.mobile else {})))
    elif t == "done":
        ST.control = "agent"
        ws_broadcast("status", status("agent"))


def push_live(kind="desktop"):
    fr = ST.frames[kind]
    w, h = (1024, 720) if kind == "desktop" else (400, 720)
    ws_broadcast("status", status(w=w, h=h))
    ws_broadcast("frame", {"d": base64.b64encode(fr).decode(), "w": w, "h": h})
    ws_broadcast("action", {"k": "click", "c": "Clicking “Conservation status” link", "t": int(time.time() * 1000),
                            "x": 0.62, "y": 0.58, "w": 0.2, "h": 0.04})


def serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DIST)
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 4179), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def anims(page, sel):
    return await page.evaluate("""(sel) => {
      const root = document.querySelector(sel);
      if (!root) return {missing: sel};
      const names = document.getAnimations().filter(a => root.contains(a.effect?.target)).map(a => a.animationName);
      const ring = root.querySelector('.pd-ring');
      return {names: [...new Set(names)].sort(), ringOpacity: ring ? getComputedStyle(ring).opacity : null,
              ringTransform: ring ? getComputedStyle(ring).transform : null};
    }""", sel)


async def main():
    srv = serve()
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox", "--disable-crash-reporter", "--disable-gpu"])
        # Real page frames for the fake engine.
        shot = await (await b.new_context(viewport={"width": 1024, "height": 720})).new_page()
        await shot.goto("https://en.wikipedia.org/wiki/Red_panda", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(1.5)
        ST.frames["desktop"] = await shot.screenshot(type="jpeg", quality=60)
        await shot.mouse.wheel(0, 900)
        await asyncio.sleep(0.6)
        ST.frames["desktop_end"] = await shot.screenshot(type="jpeg", quality=70)
        mob = await (await b.new_context(viewport={"width": 400, "height": 720}, device_scale_factor=1.5, is_mobile=True, has_touch=True,
                                         user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36")).new_page()
        await mob.goto("https://en.wikipedia.org/wiki/Red_panda", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(1.5)
        ST.frames["mobile"] = await mob.screenshot(type="jpeg", quality=60)

        async def new_page(w, h, theme="light", reduced=False):
            ST.theme = theme
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, service_workers="block",
                                      reduced_motion="reduce" if reduced else "no-preference", has_touch=w < 700, is_mobile=w < 700)
            await ctx.add_init_script(f"localStorage.setItem('sb-ufvskhagspdjslomzwjh-auth-token', {json.dumps(json.dumps(SESSION_TOKEN))});"
                                      f"localStorage.setItem('panda.settings', {json.dumps(json.dumps({**SETTINGS, 'theme': theme}))});")
            await ctx.route(f"{SB}/**", handle_route)
            page = await ctx.new_page()
            await page.route_web_socket(re.compile(r"wss://ufvskhagspdjslomzwjh\.supabase\.co/realtime/.*"), on_ws)
            page.on("pageerror", lambda e: REPORT.setdefault("page_errors", []).append(str(e)[:200]))
            return page

        # 1. Home, phone: animated panda + starters; then the "/" menu.
        page = await new_page(390, 844)
        await page.goto(f"{APP}/")
        await page.wait_for_selector(".greeting", timeout=20000)
        await asyncio.sleep(1.2)
        REPORT["home_panda"] = await anims(page, ".home-mark")
        await page.screenshot(path=f"{OUT}/1-home-phone.png")
        await page.fill(".composer textarea", "/")
        await asyncio.sleep(0.5)
        REPORT["slash_items"] = await page.locator(".slash-item").count()
        REPORT["slash_groups"] = await page.locator(".slash-group").all_inner_texts()
        await page.screenshot(path=f"{OUT}/2-slash-menu-phone.png")
        await page.fill(".composer textarea", "/co")
        await asyncio.sleep(0.3)
        REPORT["slash_co"] = await page.locator(".slash-name").all_inner_texts()
        await page.close()

        # 2. Chat, phone: command card, credits, live browser card with the agent pointer.
        page = await new_page(390, 844)
        await page.goto(f"{APP}/?c={CHAT_A}")
        await page.wait_for_selector(".msg.assistant", timeout=20000)
        await asyncio.sleep(1.0)
        for _ in range(3):
            push_live("desktop")
            await asyncio.sleep(0.4)
        await page.wait_for_selector(".bcard.is-live img", timeout=10000)
        await asyncio.sleep(0.8)
        REPORT["working_panda"] = await anims(page, "svg.panda-working")
        t1 = REPORT["working_panda"]["ringTransform"] if REPORT["working_panda"] else None
        await asyncio.sleep(0.35)
        t2 = (await anims(page, "svg.panda-working") or {}).get("ringTransform")
        REPORT["ring_turns"] = bool(t1 and t2 and t1 != t2)
        REPORT["credit_chips"] = await page.locator(".credit-chip").all_inner_texts()
        REPORT["session_chip_phone"] = await page.locator(".session-chip").inner_text()
        REPORT["context_meter"] = await page.locator(".ctx-ring").all_inner_texts()
        REPORT["chat_total"] = await page.locator(".chat-total").inner_text() if await page.locator(".chat-total").count() else None
        REPORT["command_cards"] = await page.locator(".command-out").count()
        REPORT["cards"] = {"live": await page.locator(".bcard.is-live").count(), "ended": await page.locator(".bcard.is-ended").count()}
        REPORT["cursor_visible"] = await page.locator(".bcard.is-live .agent-cursor").count()
        await page.screenshot(path=f"{OUT}/3-chat-phone.png")
        await page.locator(".bcard.is-live").scroll_into_view_if_needed()
        await page.screenshot(path=f"{OUT}/4-live-card-phone.png")
        # 3. Full browser panel, phone (mobile frames), then take over.
        await page.locator(".bcard.is-live .bcard-stage").click()
        await page.wait_for_selector(".browser-panel", timeout=5000)
        for _ in range(2):
            push_live("mobile")
            await asyncio.sleep(0.4)
        await asyncio.sleep(0.6)
        hello = [i for i in ST.inputs if (i or {}).get("t") == "hello"]
        REPORT["hello_from_panel"] = hello[-1] if hello else None
        await page.screenshot(path=f"{OUT}/5-browser-panel-phone.png")
        ST.mobile = True
        await page.get_by_role("button", name=re.compile("Take over")).click()
        await asyncio.sleep(0.8)
        REPORT["takeover_sent"] = any((i or {}).get("t") == "takeover" for i in ST.inputs)
        REPORT["typebar_before_keyboard_button"] = await page.locator(".bp-typebar").count()
        REPORT["type_hint"] = await page.locator(".bp-type-hint").all_inner_texts()
        await page.screenshot(path=f"{OUT}/6a-browser-takeover-phone.png")
        await page.locator(".bp-kbd-btn").click()
        await asyncio.sleep(0.4)
        REPORT["typebar"] = await page.locator(".bp-typebar").count()
        REPORT["stage_keyboard_mode"] = await page.locator(".bp-stage.kb").count()
        await page.locator(".bp-typebar input").fill("red panda adoption")
        await asyncio.sleep(0.3)
        REPORT["typed_forwarded"] = [i.get("text") for i in ST.inputs if (i or {}).get("t") == "text"]
        await page.screenshot(path=f"{OUT}/6-browser-takeover-phone.png")
        ST.mobile = False
        await page.close()

        # 3b. Developer tab, phone: the Developer chat and its env variables.
        page = await new_page(390, 844)
        await page.goto(f"{APP}/?v=developer")
        await page.wait_for_selector(".dev-page", timeout=20000)
        await asyncio.sleep(1.0)
        REPORT["dev_chat"] = await page.locator(".dev-chat .composer").count()
        REPORT["dev_chat_in_sidebar_list"] = await page.locator(".side-link", has_text="Developer").count()
        await page.screenshot(path=f"{OUT}/8a-developer-chat-phone.png")
        await page.get_by_role("button", name="Env variables").click()
        await asyncio.sleep(0.6)
        await page.get_by_role("button", name="Import .env").click()
        await page.locator(".dev-bulk textarea").fill("# deploy\nVERCEL_TOKEN=abc\nexport SUPABASE_URL=\"https://x.supabase.co\"\n")
        await asyncio.sleep(0.3)
        REPORT["dev_env_rows"] = await page.locator(".dev-env .row-card b").all_inner_texts()
        REPORT["dev_import_preview"] = await page.locator(".dev-bulk .muted").inner_text()
        await page.screenshot(path=f"{OUT}/8b-developer-env-phone.png")
        await page.close()

        # 4. Desktop: docked browser next to the chat; settings tabs.
        page = await new_page(1440, 900)
        await page.goto(f"{APP}/?c={CHAT_A}")
        await page.wait_for_selector(".msg.assistant", timeout=20000)
        for _ in range(2):
            push_live("desktop")
            await asyncio.sleep(0.4)
        await page.wait_for_selector(".bcard.is-live img", timeout=10000)
        await page.locator(".bcard.is-live .bcard-expand").click()
        await asyncio.sleep(0.4)
        push_live("desktop")
        await asyncio.sleep(0.8)
        REPORT["docked"] = await page.locator(".browser-panel.docked").count()
        await page.screenshot(path=f"{OUT}/7-desktop-docked-browser.png")
        await page.locator(".browser-panel .bp-head .icon-btn").first.click()
        await page.locator(".sidebar .side-foot .icon-btn").click()
        await page.get_by_role("tab", name="Kiro Crew").click()
        with contextlib.suppress(Exception):  # no rows without crew_settings.json fixtures
            await page.wait_for_selector(".crew-row", timeout=10000)
        await asyncio.sleep(0.4)
        REPORT["crew_rows"] = await page.locator(".crew-row").count()
        await page.screenshot(path=f"{OUT}/8-settings-kiro-crew.png")
        await page.locator(".section-head").first.click()
        await asyncio.sleep(0.3)
        REPORT["crew_rows_after_open"] = await page.locator(".crew-row").count()
        await page.fill(".crew-settings .search-box input", "compact")
        await asyncio.sleep(0.3)
        REPORT["crew_search_compact"] = await page.locator(".crew-row-path").all_inner_texts()
        await page.screenshot(path=f"{OUT}/9-settings-search.png")
        await page.get_by_role("tab", name="Credits").click()
        await page.wait_for_selector(".usage-hero", timeout=5000)
        await asyncio.sleep(0.6)
        REPORT["usage_big"] = await page.locator(".usage-big").inner_text()
        await page.screenshot(path=f"{OUT}/10-settings-credits.png")
        await page.close()

        # 4a. Terminal tab (file browser + console) on desktop.
        page = await new_page(1280, 860)
        await page.goto(f"{APP}/?v=customize")
        await page.wait_for_selector(".tabs", timeout=20000)
        await page.get_by_role("button", name="Terminal", exact=True).click()
        await page.wait_for_selector(".cons", timeout=10000)
        await page.wait_for_selector(".fb-row", timeout=10000)
        REPORT["fb_rows"] = await page.locator(".fb-row").count()
        REPORT["fb_has_sensitive_lock"] = await page.locator(".fb-row .fb-name").filter(has_text="secrets.env").count()
        await page.locator(".cons-input input").fill("df -h")
        await page.locator(".cons-input input").press("Enter")
        await page.wait_for_selector(".cons-out", timeout=10000)
        await asyncio.sleep(0.4)
        REPORT["console_ran"] = await page.locator(".cons-out").count()
        await page.screenshot(path=f"{OUT}/15-terminal-desktop.png")
        await page.locator(".fb-row").filter(has_text="README.md").click()
        await page.wait_for_selector(".file-text", timeout=10000)
        await asyncio.sleep(0.3)
        await page.screenshot(path=f"{OUT}/16-file-preview.png")
        await page.close()
        # 4a-mobile. Terminal tab on a phone (segmented Files | Terminal).
        page = await new_page(390, 844)
        await page.goto(f"{APP}/?v=customize")
        await page.wait_for_selector(".tabs", timeout=20000)
        await page.get_by_role("button", name="Terminal", exact=True).click()
        await page.wait_for_selector(".seg", timeout=10000)
        await asyncio.sleep(0.4)
        REPORT["mobile_seg"] = await page.locator(".seg button").count()
        await page.screenshot(path=f"{OUT}/17-terminal-phone.png")
        await page.get_by_role("button", name="Files").click()
        await asyncio.sleep(0.4)
        await page.screenshot(path=f"{OUT}/18-files-phone.png")
        await page.close()
        # 4a-settings-mobile. Settings on a phone: General, Kiro Crew, Credits.
        page = await new_page(390, 844)
        await page.goto(f"{APP}/?c={CHAT_A}")
        await page.wait_for_selector(".msg.assistant", timeout=20000)
        # open the sidebar (hidden on mobile), then settings from its footer
        await page.locator(".topbar .icon-btn").first.click()
        await page.wait_for_selector(".sidebar .side-foot", timeout=10000)
        await page.locator(".sidebar .side-foot .icon-btn").click()
        await page.wait_for_selector(".settings", timeout=10000)
        await asyncio.sleep(0.4)
        await page.screenshot(path=f"{OUT}/19-settings-general-phone.png")
        await page.get_by_role("tab", name="Kiro Crew").dispatch_event("click")
        with contextlib.suppress(Exception):  # no rows without crew_settings.json fixtures
            await page.wait_for_selector(".crew-row", timeout=10000)
        await asyncio.sleep(0.4)
        REPORT["crew_overflow"] = await page.evaluate("() => { const b=document.querySelector('.modal-body'); return b ? b.scrollWidth - b.clientWidth : -1; }")
        REPORT["diag"] = await page.evaluate("""() => {
          const iw = window.innerWidth;
          const r = (e) => e.getBoundingClientRect();
          const m = document.querySelector('.modal'), b = document.querySelector('.modal-body');
          const over = [...document.querySelectorAll('.modal *')].filter(e => r(e).right > iw + 1)
            .map(e => ({ c: (e.className||'').toString().slice(0,40), r: Math.round(r(e).right), w: Math.round(r(e).width) }));
          return { iw, docW: document.documentElement.scrollWidth, modalW: Math.round(r(m).width),
                   modalRight: Math.round(r(m).right), bodyW: Math.round(r(b).width), over: over.slice(0, 8) };
        }""")
        await page.screenshot(path=f"{OUT}/20-settings-crew-phone.png")
        await page.get_by_role("tab", name="Credits").dispatch_event("click")
        await page.wait_for_selector(".usage-hero", timeout=10000)
        await asyncio.sleep(0.4)
        REPORT["mobile_credits_plan"] = await page.locator(".usage-big").first.inner_text()
        REPORT["settings_overflow"] = await page.evaluate("() => { const b=document.querySelector('.modal-body'); return b ? b.scrollWidth - b.clientWidth : -1; }")
        await page.screenshot(path=f"{OUT}/21-settings-credits-phone.png")
        await page.close()

        # 4b. Other pages keep working with the new styles.
        page = await new_page(1280, 820)
        for name, q in (("projects", "v=projects"), ("files", "v=files"), ("customize", "v=customize&tab=keys")):
            await page.goto(f"{APP}/?{q}")
            await page.wait_for_selector(".page-head", timeout=20000)
            await asyncio.sleep(0.6)
            await page.screenshot(path=f"{OUT}/13-page-{name}.png")
        await page.close()
        ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, service_workers="block")
        await ctx.route(f"{SB}/**", handle_route)
        login = await ctx.new_page()
        await login.goto(f"{APP}/")
        await login.wait_for_selector(".login-card", timeout=20000)
        await asyncio.sleep(0.5)
        await login.screenshot(path=f"{OUT}/14-login.png")
        await ctx.close()

        # 5. Dark home + reduced motion (the working ring must still turn).
        page = await new_page(390, 844, theme="dark", reduced=True)
        await page.goto(f"{APP}/?c={CHAT_A}")
        await page.wait_for_selector(".msg.assistant", timeout=20000)
        await asyncio.sleep(0.8)
        a1 = await anims(page, "svg.panda-working")
        await asyncio.sleep(0.35)
        a2 = await anims(page, "svg.panda-working")
        REPORT["reduced_motion"] = {"names": (a1 or {}).get("names"), "ring_turns": bool(a1 and a2 and a1["ringTransform"] != a2["ringTransform"])}
        await page.screenshot(path=f"{OUT}/11-chat-dark-phone.png")
        await page.goto(f"{APP}/")
        await page.wait_for_selector(".greeting", timeout=20000)
        await page.fill(".composer textarea", "/")
        await asyncio.sleep(0.5)
        await page.screenshot(path=f"{OUT}/12-home-dark-slash.png")
        await page.close()
        await b.close()
    srv.shutdown()
    print(json.dumps(REPORT, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # later steps can need fixtures; still show what was checked
        REPORT["failed_at"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
        print(json.dumps(REPORT, indent=1, ensure_ascii=False))
        raise SystemExit(1)
