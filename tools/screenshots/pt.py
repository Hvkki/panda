import asyncio, glob, os
from playwright.async_api import async_playwright
exe = glob.glob("/inner_container/opt/playwright/chromium-1232/chrome-linux64/chrome")[0]
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=exe, args=["--no-sandbox","--disable-crash-reporter","--disable-gpu","--crash-dumps-dir=/projects/sandbox/.pwhome/crash"])
        ctx = await b.new_context(viewport={"width": 1024, "height": 720})
        page = await ctx.new_page()
        await page.set_content("<h1>Hello</h1><button>Sign in</button><input placeholder='Email'><a href='https://example.com'>Link</a>")
        try:
            print("AI SNAPSHOT:\n", await page._impl_obj._snapshot_for_ai())
        except Exception as e:
            print("snapshot_for_ai failed:", type(e).__name__, str(e)[:200])
        print("ARIA:\n", await page.locator("body").aria_snapshot())
        await b.close()
asyncio.run(main())
