"""UI check of Settings -> Remote Panda, Supabase mocked.

    python tools/screenshots/mock_remote.py <out-dir>
"""
import asyncio
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mock_ui as M  # noqa: E402

OUT = M.OUT
NOW = time.time()
M.COMMAND_RESULTS["remote.status"] = {
    "alive": True, "state": "running", "at": NOW - 8, "since": NOW - 1500, "cpus": 4, "free_mb": 30200, "load": 2.1,
    "running": ["fullrender-1"], "remote_id": "r1", "jobs": [
        {"id": "fullrender-1-a", "name": "fullrender", "running": True, "exit": None, "started": NOW - 420, "agent": False},
        {"id": "research-1-b", "name": "research", "running": True, "exit": None, "started": NOW - 95, "agent": True},
        {"id": "stems-1-c", "name": "stems", "running": False, "exit": 0, "started": NOW - 3000, "agent": False},
        {"id": "broken-1-d", "name": "broken", "running": False, "exit": 7, "started": NOW - 5000, "agent": False}]}
REPORT = {}


async def main():
    srv = M.serve()
    async with M.async_playwright() as p:
        b = await p.chromium.launch(executable_path=M.CHROME, args=["--no-sandbox", "--disable-gpu"])
        for name, vp, theme in (("desktop", {"width": 1280, "height": 820}, "light"),
                                ("desktop-dark", {"width": 1280, "height": 820}, "dark"),
                                ("phone", {"width": 390, "height": 844}, "light")):
            ctx = await b.new_context(viewport=vp, device_scale_factor=2, service_workers="block",
                                      color_scheme=theme)
            s = dict(M.SETTINGS, theme=theme)
            await ctx.add_init_script(f"localStorage.setItem('sb-ufvskhagspdjslomzwjh-auth-token', {json.dumps(json.dumps(M.SESSION_TOKEN))});"
                                      f"localStorage.setItem('panda.settings', {json.dumps(json.dumps(s))});")
            await ctx.route(f"{M.SB}/**", M.handle_route)
            page = await ctx.new_page()
            await page.route_web_socket(re.compile(r"wss://ufvskhagspdjslomzwjh\.supabase\.co/realtime/.*"), M.on_ws)
            page.on("pageerror", lambda e: REPORT.setdefault("page_errors", []).append(str(e)[:200]))
            await page.goto(f"{M.APP}/")
            await asyncio.sleep(2)
            gear = page.locator("button[aria-label='Settings']").first
            if not await gear.is_visible():
                await page.locator("button[aria-label='Sidebar']").first.click()
                await asyncio.sleep(0.5)
            await gear.click()
            await page.get_by_role("tab", name="Remote Panda").click()
            await page.wait_for_selector(".remote-panel .remote-dot.on", timeout=10000)
            await asyncio.sleep(0.5)
            REPORT[f"{name}_rows"] = await page.locator(".remote-panel .set-row").count()
            REPORT[f"{name}_stop"] = await page.locator(".remote-panel .btn.danger").is_visible()
            REPORT[f"{name}_overflow"] = await page.evaluate("""() => [...document.querySelectorAll('.modal *, .modal')]
                .filter(e => e.scrollLeft > 0 || e.scrollWidth > e.clientWidth + 2)
                .slice(0, 6).map(e => `${e.tagName}.${e.className} sl=${e.scrollLeft} sw=${e.scrollWidth} cw=${e.clientWidth}`)""")
            if name == "phone":
                await page.get_by_role("tab", name="Credits").click()
                await asyncio.sleep(0.6)
                await page.screenshot(path=f"{OUT}/remote-phone-credits.png")
                await page.get_by_role("tab", name="Remote Panda").click()
                await asyncio.sleep(0.6)
            await page.screenshot(path=f"{OUT}/remote-{name}.png")
            await ctx.close()
        await b.close()
    srv.shutdown()
    print(json.dumps(REPORT))


asyncio.run(main())
