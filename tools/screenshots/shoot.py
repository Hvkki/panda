import asyncio, glob, json, os, subprocess, sys, time
import requests
from playwright.async_api import async_playwright

URL = "https://ufvskhagspdjslomzwjh.supabase.co"
ANON = "sb_publishable_fZpIgjFiiMbLIyehPMxARQ_zCcjBZRT"
SECRET = os.environ["SB_SECRET"]
HOME = "/projects/sandbox/.pwhome/home2"
CHROME = "/inner_container/opt/playwright/chromium-1232/chrome-linux64/chrome"
SK = "/projects/sandbox/kirocrew-relay/worker/skills/panda-browser"
OUT = "/projects/sandbox/.kiro/artifacts/screenshots/"
PW = "Shoot-" + os.urandom(6).hex()

H = {"apikey": SECRET, "Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"}
requests.put(f"{URL}/auth/v1/admin/users/3793df2a-2133-49e9-835e-1d9aac1c43f6", headers=H, json={"password": PW})
rows = requests.get(f"{URL}/rest/v1/user_settings?owner=eq.3793df2a-2133-49e9-835e-1d9aac1c43f6&select=settings", headers=H).json()
topic = (rows[0]["settings"] if rows else {}).get("browserTopic") or "pbr-shoot-" + os.urandom(6).hex()
os.makedirs(f"{HOME}/.panda", exist_ok=True)
json.dump({"supabase_url": URL, "anon": ANON, "topic": topic}, open(f"{HOME}/.panda/browser.json", "w"))
env = dict(os.environ, HOME=HOME, PANDA_CHROMIUM=CHROME, PANDA_BROWSER_PORT="5482", PANDA_OUTPUTS=f"{HOME}/outputs")
eng = subprocess.Popen([sys.executable, f"{SK}/engine.py"], env=env, stdout=open(f"{HOME}/e.log", "w"), stderr=subprocess.STDOUT)
pb = lambda *a: subprocess.run([sys.executable, f"{SK}/pb.py", *a], env=env, capture_output=True, text=True, timeout=120)


async def main():
    try:
        for _ in range(60):
            await asyncio.sleep(0.5)
            if pb("tabs").returncode == 0:
                break
        pb("open", "https://en.wikipedia.org/wiki/Red_panda")
        async with async_playwright() as p:
            b = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox", "--disable-crash-reporter", "--disable-gpu"])
            page = await (await b.new_context(viewport={"width": 412, "height": 860}, device_scale_factor=2)).new_page()
            await page.goto("file:///projects/sandbox/.kiro/artifacts/embra-preview.html")
            await page.wait_for_selector("input[type=email]", timeout=20000)
            await page.fill("input[type=email]", "tester@kirocrew.local")
            await page.fill("input[type=password]", PW)
            await page.click("button.btn.primary")
            await page.wait_for_selector("button.browser-btn", timeout=40000)
            await page.screenshot(path=OUT + "panda-topbar-browser.png")
            await page.click("button.browser-btn")
            await page.wait_for_selector(".bp-stage img", timeout=30000)
            await asyncio.sleep(1.5)
            await page.screenshot(path=OUT + "panda-browser-panel.png")
            # Panda asks for help
            asyncio.get_event_loop().run_in_executor(None, lambda: pb("handoff", "Please log in to your account, then press Done"))
            await page.wait_for_selector(".bp-banner.needs_user", timeout=20000)
            await asyncio.sleep(0.8)
            await page.screenshot(path=OUT + "panda-browser-needs-you.png")
            await page.click(".bp-banner .btn.small-btn, .bp-banner .btn")
            await asyncio.sleep(1)
            await page.screenshot(path=OUT + "panda-browser-done.png")
            await b.close()
        print("shots ok")
    finally:
        eng.terminate()
        try:
            eng.wait(10)
        except Exception:
            eng.kill()
        # leave the tester locked
        requests.put(f"{URL}/auth/v1/admin/users/3793df2a-2133-49e9-835e-1d9aac1c43f6", headers=H, json={"password": os.urandom(20).hex()})

asyncio.run(main())
