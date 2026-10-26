"""UI check of the drawing panda (a picture being made) and of inline pictures, Supabase mocked.

    python tools/screenshots/mock_drawing.py <out-dir> [picture.png]

Reuses the fakes in mock_ui.py (no network to the real project). The chat holds: a finished reply
with an uploaded picture (panda-file:), an old reply that still names a path on the worker (must show
"picture not available", not a broken image linking to a 404), and a reply whose newest step is
make_image.py (the drawing panda). Screenshots of the card are taken in real time while its % grows.
"""
import asyncio
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
PNG = open(sys.argv[2], "rb").read() if len(sys.argv) > 2 else b""
sys.argv = sys.argv[:2]  # mock_ui reads argv[1] as its out dir and argv[2] as a fixtures dir
import mock_ui as M  # noqa: E402

OUT = M.OUT
STEP = 'python3 ~/.kiro/crew/skills/panda-image/make_image.py "A cute giant panda cooking pancakes" ~/outputs/panda-cooking.png'
M.MESSAGES[:] = [
    M.msg(1, "user", "Draw a panda surfing at sunset"),
    M.msg(2, "assistant", "Here it is:\n\n![Panda surfing](panda-file:f1)", reply_to=1,
          meta={"model": "claude-opus-5.5", "secs": 52.3, "credits": 2.1,
                "tools": ["Reading SKILL.md:1", STEP.replace("cooking pancakes", "surfing")]}),
    M.msg(3, "user", "Show the test picture"),
    M.msg(4, "assistant", "Here is the first test picture:\n\n![Red panda on bamboo at sunset]"
          "(/root/.kiro/crew/sessions/x.attachments/1d0c70a6f5d065f4-test-panda.png)", reply_to=3,
          meta={"model": "claude-opus-5.5", "secs": 105.4}),
    M.msg(5, "user", "Now draw a panda cooking pancakes"),
    M.msg(6, "assistant", "", reply_to=5, status="streaming", meta={"model": "claude-opus-5.5", "tools": ["Reading SKILL.md:1", STEP]}),
]
REPORT = {}


async def route(r):
    u = urllib.parse.urlparse(r.request.url)
    if u.path.startswith("/rest/v1/files"):
        single = "vnd.pgrst.object" in (r.request.headers.get("accept") or "")
        row = {"path": f"{M.UID}/chats/x/panda-surfing.png"}
        return await r.fulfill(status=200, content_type="application/json", body=json.dumps(row if single else [row]))
    if u.path.startswith("/storage/v1/object/sign/") and r.request.method == "GET" and PNG:
        return await r.fulfill(status=200, content_type="image/png", body=PNG)
    return await M.handle_route(r)


async def main():
    srv = M.serve()
    async with M.async_playwright() as p:
        b = await p.chromium.launch(executable_path=M.CHROME, args=["--no-sandbox", "--disable-crash-reporter", "--disable-gpu"])

        async def new_page(theme):
            M.ST.theme = theme
            ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, service_workers="block",
                                      has_touch=True, is_mobile=True)
            await ctx.add_init_script(f"localStorage.setItem('sb-ufvskhagspdjslomzwjh-auth-token', {json.dumps(json.dumps(M.SESSION_TOKEN))});"
                                      f"localStorage.setItem('panda.settings', {json.dumps(json.dumps({**M.SETTINGS, 'theme': theme}))});")
            await ctx.route(f"{M.SB}/**", route)
            page = await ctx.new_page()
            await page.route_web_socket(re.compile(r"wss://ufvskhagspdjslomzwjh\.supabase\.co/realtime/.*"), M.on_ws)
            page.on("pageerror", lambda e: REPORT.setdefault("page_errors", []).append(str(e)[:200]))
            return page

        page = await new_page("light")
        await page.goto(f"{M.APP}/?c={M.CHAT_A}")
        await page.wait_for_selector(".pdw-card", timeout=20000)
        t0 = time.time()
        card = page.locator(".pdw-card")
        for at in (2, 12, 24, 36, 48):
            await asyncio.sleep(max(0, at - (time.time() - t0)))
            await card.screenshot(path=f"{OUT}/draw-{at:02d}s.png")
            REPORT[f"caption_{at}s"] = await page.locator(".pdw-caption").inner_text()
        REPORT["card_animations"] = await page.evaluate("""() => {
          const root = document.querySelector('.pdw-card');
          return [...new Set(document.getAnimations().filter(a => root.contains(a.effect?.target)).map(a => a.animationName))].sort();
        }""")
        REPORT["activity"] = await page.locator(".msg.assistant").last.locator(".step-label").all_inner_texts()
        await page.screenshot(path=f"{OUT}/chat-drawing-phone.png")
        # The finished replies above: the uploaded picture shows; the worker path does not link anywhere.
        await page.locator(".msg.assistant").first.scroll_into_view_if_needed()
        await asyncio.sleep(1.0)
        REPORT["uploaded_img_src"] = await page.locator(".msg.assistant").first.locator(".prose img").get_attribute("src")
        REPORT["missing_note"] = await page.locator(".img-missing").all_inner_texts()
        REPORT["links_to_worker_paths"] = await page.locator("a[href^='/root/']").count()
        REPORT["summary"] = await page.locator(".msg.assistant").first.locator(".activity-head").inner_text()
        await page.screenshot(path=f"{OUT}/chat-pictures-phone.png")
        await page.close()

        page = await new_page("dark")
        await page.goto(f"{M.APP}/?c={M.CHAT_A}")
        await page.wait_for_selector(".pdw-card", timeout=20000)
        await asyncio.sleep(3)
        await page.locator(".pdw-card").screenshot(path=f"{OUT}/draw-dark.png")
        await page.close()
        await b.close()
    srv.shutdown()
    print(json.dumps(REPORT, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
