"""UI check of the browser's tabs (tab row on tablets, tab grid on phones), Supabase mocked.

    python tools/screenshots/mock_tabs.py <out-dir>
"""
import asyncio
import base64
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv = sys.argv[:2]
import mock_ui as M  # noqa: E402

OUT = M.OUT
REPORT: dict = {}
TABS = [{"i": 0, "id": 1, "u": "https://en.wikipedia.org/wiki/Red_panda", "t": "Red panda - Wikipedia", "active": True},
        {"i": 1, "id": 2, "u": "https://www.google.com/search?q=red+panda", "t": "red panda - Google Search", "active": False},
        {"i": 2, "id": 3, "u": "https://example.com/", "t": "Example Domain", "active": False}]


def push(busy=False):
    fr = base64.b64encode(M.ST.frames["desktop"]).decode()
    M.ws_broadcast("status", M.status("agent", busy=busy, action="", tabs=TABS))
    M.ws_broadcast("frame", {"d": fr, "w": 1024, "h": 720})


def on_input(p):
    M.ST.inputs.append(p)
    if (p or {}).get("t") == "tabs":
        M.ws_broadcast("thumbs", {"thumbs": {"2": base64.b64encode(M.ST.frames["thumb"]).decode()}})


async def main():
    srv = M.serve()
    M.on_input = on_input
    async with M.async_playwright() as p:
        b = await p.chromium.launch(executable_path=M.CHROME, args=["--no-sandbox", "--disable-gpu"])
        shot = await (await b.new_context(viewport={"width": 1024, "height": 720})).new_page()
        await shot.set_content("<body style='font:40px sans-serif;padding:40px;background:#f4efe6'><h1>Red panda</h1>"
                               "<p>The red panda is a small mammal native to the eastern Himalayas.</p></body>")
        M.ST.frames["desktop"] = await shot.screenshot(type="jpeg", quality=60)
        await shot.set_content("<body style='font:40px sans-serif;padding:40px'><b style='color:#4285f4'>Google</b>"
                               "<p>red panda</p><p style='color:#1a0dab'>Red panda - Wikipedia</p></body>")
        M.ST.frames["thumb"] = await shot.screenshot(type="jpeg", quality=45, scale="css")
        for name, vp in (("phone", {"width": 390, "height": 844}), ("tablet", {"width": 820, "height": 1180})):
            ctx = await b.new_context(viewport=vp, device_scale_factor=2, service_workers="block", has_touch=True)
            await ctx.add_init_script(f"localStorage.setItem('sb-ufvskhagspdjslomzwjh-auth-token', {json.dumps(json.dumps(M.SESSION_TOKEN))});"
                                      f"localStorage.setItem('panda.settings', {json.dumps(json.dumps(M.SETTINGS))});")
            await ctx.route(f"{M.SB}/**", M.handle_route)
            page = await ctx.new_page()
            await page.route_web_socket(re.compile(r"wss://ufvskhagspdjslomzwjh\.supabase\.co/realtime/.*"), M.on_ws)
            page.on("pageerror", lambda e: REPORT.setdefault("page_errors", []).append(str(e)[:200]))
            await page.goto(f"{M.APP}/?c={M.CHAT_A}")
            await page.wait_for_selector(".msg.assistant", timeout=20000)
            await asyncio.sleep(1)
            for _ in range(3):
                push()
                await asyncio.sleep(0.4)
            await page.wait_for_selector(".bcard.is-live img", timeout=10000)
            await page.locator(".bcard.is-live .bcard-stage").click()
            await page.wait_for_selector(".browser-panel", timeout=5000)
            push()
            await asyncio.sleep(0.8)
            REPORT[f"{name}_strip_visible"] = await page.locator(".bp-strip").is_visible()
            REPORT[f"{name}_counter"] = await page.locator(".bp-tabs-count").inner_text()
            await page.screenshot(path=f"{OUT}/tabs-{name}-panel.png")
            await page.locator(".bp-tabs").click()
            await asyncio.sleep(0.6)
            REPORT[f"{name}_cards"] = await page.locator(".bp-tabcard").count()
            REPORT[f"{name}_thumb_imgs"] = await page.locator(".bp-tabcard-pic img").count()
            await page.screenshot(path=f"{OUT}/tabs-{name}-grid.png")
            M.ST.inputs.clear()
            await page.locator(".bp-tabcard").nth(1).locator(".bp-tabcard-main").click()
            await page.locator(".bp-tabs").click()
            await page.locator(".bp-tabgrid-head .btn").click()
            await page.locator(".bp-tabs").click()
            await page.locator(".bp-tabcard-x").nth(2).click()
            REPORT[f"{name}_sent"] = [{k: v for k, v in i.items() if k in ("t", "i")} for i in M.ST.inputs if i.get("t") != "hello"]
            push(busy=True)
            await asyncio.sleep(0.5)
            REPORT[f"{name}_busy_x_disabled"] = await page.locator(".bp-tabcard-x").first.is_disabled()
            await ctx.close()
        await b.close()
    srv.shutdown()
    print(json.dumps(REPORT, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
