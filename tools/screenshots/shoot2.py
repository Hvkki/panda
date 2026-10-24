import asyncio, os, re, pathlib, requests
from playwright.async_api import async_playwright

URL = "https://ufvskhagspdjslomzwjh.supabase.co"
SECRET = os.environ["SB_SECRET"]
H = {"apikey": SECRET, "Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"}
TID = "3793df2a-2133-49e9-835e-1d9aac1c43f6"
PW = "Shoot-" + os.urandom(6).hex()
CHROME = "/inner_container/opt/playwright/chromium-1232/chrome-linux64/chrome"
OUT = "/projects/sandbox/.kiro/artifacts/screenshots/"
CHAT = open("/projects/sandbox/.pwhome/demo_chat").read().strip()

# single-file bundle of the build
d = pathlib.Path("/projects/sandbox/embra/dist")
html = (d / "index.html").read_text()
for m in re.findall(r'href="\./(assets/[^"]+\.css)"', html):
    html = re.sub(r'<link rel="stylesheet"[^>]*' + re.escape(m) + r'"[^>]*>', "<style>" + (d / m).read_text() + "</style>", html)
for m in re.findall(r'src="\./(assets/[^"]+\.js)"', html):
    js = (d / m).read_text().replace("</script", "<\\/script")
    html = re.sub(r'<script type="module"[^>]*' + re.escape(m) + r'"[^>]*></script>', lambda _: '<script type="module">' + js + "</script>", html)
pathlib.Path("/projects/sandbox/.kiro/artifacts/embra-preview.html").write_text(html)

requests.put(f"{URL}/auth/v1/admin/users/{TID}", headers=H, json={"password": PW})


async def main():
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox", "--disable-crash-reporter", "--disable-gpu"])
            for name, w, h in (("tablet", 768, 1024), ("phone", 400, 860)):
                ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
                page = await ctx.new_page()
                await page.goto(f"file:///projects/sandbox/.kiro/artifacts/embra-preview.html?c={CHAT}")
                await page.wait_for_selector("input[type=email]", timeout=20000)
                await page.fill("input[type=email]", "tester@kirocrew.local")
                await page.fill("input[type=password]", PW)
                await page.click("button.btn.primary")
                await page.wait_for_selector(".approval", timeout=30000)
                await asyncio.sleep(1.2)
                await page.screenshot(path=OUT + f"polish-{name}.png")
                if name == "tablet":
                    await page.click(".activity-head")
                    await asyncio.sleep(0.4)
                    await page.screenshot(path=OUT + "polish-tablet-steps.png")
                await ctx.close()
            await b.close()
        print("ok")
    finally:
        requests.put(f"{URL}/auth/v1/admin/users/{TID}", headers=H, json={"password": os.urandom(20).hex()})

asyncio.run(main())
