"""UI check: 50 attached photos show as 8 chips plus "+42".

    python tools/screenshots/mock_haikusteer.py <out-dir>
"""
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv = sys.argv[:2]
import mock_ui as M  # noqa: E402

OUT = M.OUT
REPORT: dict = {}
FILES = [{"path": f"u/{i}.jpg", "name": f"IMG_{2000+i}.jpg", "mime": "image/jpeg", "size": 900000} for i in range(50)]
M.MESSAGES[:] = [m for m in M.MESSAGES if m["id"] <= 4]
M.MESSAGES += [M.msg(5, "user", "Pick the best 5 for the deck.", meta={"attachments": FILES}),
               M.msg(6, "assistant", "Looking at all 50 photos now.", reply_to=5, meta={"model": "claude-sonnet-5.5", "secs": 4.0})]


async def main():
    M.serve()
    async with M.async_playwright() as p:
        b = await p.chromium.launch(executable_path=M.CHROME, args=["--no-sandbox", "--disable-gpu"])
        for name, vp in (("desktop", {"width": 1300, "height": 820}), ("phone", {"width": 390, "height": 844})):
            ctx = await b.new_context(viewport=vp, device_scale_factor=2, service_workers="block")
            await ctx.add_init_script(f"localStorage.setItem('sb-ufvskhagspdjslomzwjh-auth-token', {json.dumps(json.dumps(M.SESSION_TOKEN))});"
                                      f"localStorage.setItem('panda.settings', {json.dumps(json.dumps(M.SETTINGS))});")
            await ctx.route(f"{M.SB}/**", M.handle_route)
            page = await ctx.new_page()
            await page.route_web_socket(re.compile(r"wss://ufvskhagspdjslomzwjh\.supabase\.co/realtime/.*"), M.on_ws)
            page.on("pageerror", lambda e: REPORT.setdefault("page_errors", []).append(str(e)[:200]))
            await page.goto(f"{M.APP}/?c={M.CHAT_A}")
            await page.wait_for_selector("text=+42", timeout=20000)
            await asyncio.sleep(1)
            REPORT[f"{name}_chips_before"] = await page.locator(".msg.user .attach-chip").count()
            await page.screenshot(path=f"{OUT}/manyfiles-{name}.png")
            await page.click("text=+42")
            REPORT[f"{name}_chips_after"] = await page.locator(".msg.user .attach-chip").count()
            await page.screenshot(path=f"{OUT}/manyfiles-{name}-open.png")
            await ctx.close()
        await b.close()
    print(json.dumps(REPORT, indent=1))


asyncio.run(main())
