"""UI check of the Approve / Deny buttons on "skill awaiting review" notifications, Supabase mocked.

    python tools/screenshots/mock_notifs.py <out-dir>
"""
import asyncio
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv = sys.argv[:2]
import mock_ui as M  # noqa: E402

OUT = M.OUT
BODY = ("**auto/{slug}** — {desc}\n\nGenerated from a session. Needs your approval before it can be used."
        "\n\n**Triggers:** {trig}")
NOTIFS = [
    {"id": 3, "owner": M.UID, "source": "skills", "read": False, "created_at": M.iso(M.NOW - 60),
     "text": BODY.format(slug="codex-cli-image-generation", desc="Make pictures with Codex CLI", trig="generate image"),
     "meta": {"title": "New skill awaiting review", "kind": "skills", "slug": "codex-cli-image-generation"}},
    {"id": 2, "owner": M.UID, "source": "skills", "read": False, "created_at": M.iso(M.NOW - 3600),
     "text": BODY.format(slug="test-webapp-ui-mock-playwright", desc="Check a UI change with a mock", trig="ui test"),
     "meta": {"title": "New skill awaiting review", "kind": "skills"}},  # older row: slug only in the body
    {"id": 1, "owner": M.UID, "source": "cron", "read": True, "created_at": M.iso(M.NOW - 7200),
     "text": "Daily digest is ready.", "meta": {"title": "Cron: digest"}},
]
REPORT: dict = {"gw_calls": [], "patches": []}


async def route(r):
    u = urllib.parse.urlparse(r.request.url)
    if u.path.startswith("/rest/v1/notifications"):
        if r.request.method == "PATCH":
            REPORT["patches"].append(json.loads(r.request.post_data or "{}"))
        return await r.fulfill(status=200, content_type="application/json",
                               body=json.dumps(NOTIFS if r.request.method == "GET" else []))
    if u.path.startswith("/rest/v1/commands") and r.request.method == "POST":
        body = json.loads(r.request.post_data or "{}")
        if body.get("op") == "gw":
            REPORT["gw_calls"].append(f'{body["args"]["method"]} {body["args"]["path"]}')
    return await M.handle_route(r)


async def main():
    srv = M.serve()
    async with M.async_playwright() as p:
        b = await p.chromium.launch(executable_path=M.CHROME, args=["--no-sandbox", "--disable-gpu"])
        for theme, vp in (("light", {"width": 390, "height": 844}), ("dark", {"width": 820, "height": 1180})):
            M.ST.theme = theme
            ctx = await b.new_context(viewport=vp, device_scale_factor=2, service_workers="block", has_touch=True)
            await ctx.add_init_script(f"localStorage.setItem('sb-ufvskhagspdjslomzwjh-auth-token', {json.dumps(json.dumps(M.SESSION_TOKEN))});"
                                      f"localStorage.setItem('panda.settings', {json.dumps(json.dumps({**M.SETTINGS, 'theme': theme}))});")
            await ctx.route(f"{M.SB}/**", route)
            page = await ctx.new_page()
            await page.route_web_socket(re.compile(r"wss://ufvskhagspdjslomzwjh\.supabase\.co/realtime/.*"), M.on_ws)
            page.on("pageerror", lambda e: REPORT.setdefault("page_errors", []).append(str(e)[:200]))
            await page.goto(f"{M.APP}/?panel=notifications")
            await page.wait_for_selector(".notif-actions", timeout=20000)
            REPORT[f"{theme}_button_rows"] = await page.locator(".notif-actions").count()
            await page.screenshot(path=f"{OUT}/notifs-{theme}-before.png")
            if theme == "light":
                await page.locator(".notif-actions").nth(0).get_by_text("Approve").click()
                await page.wait_for_selector(".notif-decision.approved", timeout=15000)
                await page.locator(".notif-actions").nth(0).get_by_text("Deny").click()
                await page.wait_for_selector(".notif-decision.denied", timeout=15000)
                REPORT["decisions"] = await page.locator(".notif-decision").all_inner_texts()
                await page.screenshot(path=f"{OUT}/notifs-light-after.png")
            await ctx.close()
        await b.close()
    srv.shutdown()
    print(json.dumps(REPORT, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
