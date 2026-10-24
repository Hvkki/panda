import asyncio, glob, re
from playwright.async_api import async_playwright
exe = glob.glob("/inner_container/opt/playwright/chromium-1232/chrome-linux64/chrome")[0]
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=exe, args=["--no-sandbox","--disable-crash-reporter","--disable-gpu"])
        page = await (await b.new_context(viewport={"width": 1024, "height": 720})).new_page()
        await page.set_content("<h1>Hello</h1><button onclick=\"document.title='clicked'\">Sign in</button><input placeholder='Email'>")
        for kw in ({"mode": "ai"}, {"_mode": "ai"}):
            try:
                s = await page.locator("body").aria_snapshot(**kw)
                print("locator.aria_snapshot", kw, "->\n", s); break
            except Exception as e:
                print("locator", kw, "failed:", str(e)[:120])
        try:
            s = await page.aria_snapshot(mode="ai"); print("page.aria_snapshot ->\n", s)
        except Exception as e:
            print("page.aria_snapshot failed:", str(e)[:150])
        m = re.search(r'button "Sign in" \[ref=(e\d+)\]', s or "")
        if m:
            await page.locator(f"aria-ref={m.group(1)}").click()
            print("title after click:", await page.title())
        await b.close()
asyncio.run(main())
