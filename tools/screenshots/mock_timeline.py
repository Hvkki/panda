"""UI check of the live timeline (right column on wide screens, sheet on phones), Supabase mocked.

    python tools/screenshots/mock_timeline.py <out-dir>
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

# A long-running answer: the streaming reply started 31 minutes ago, five steps, a Haiku note.
START = M.NOW - 31 * 60
reply = next(m for m in M.MESSAGES if m["id"] == 6)
reply["created_at"] = M.iso(START)
reply["meta"] = {
    "model": "claude-opus-5.5", "secs": 31 * 60 - 3,
    "tools": ["Reading SKILL.md:1", "Writing qc.py", "Running: python3 /root/work/tts/make_vo.py --takes 4",
              "Running: modal run h3_batch.py::batch --jobs jobs.json", "Running: python3 /root/work/tts/qc.py vo2/*.wav"],
    "steps_at": [4.0, 20.0, 41.0, 300.0, 1500.0], "n_steps": 5,
    "tl": {"now": "Checking each voice take for clear speech", "next": "Mixing the music under the voice",
           "at": 1700.0, "step": 4, "step_end": 2100.0, "done_end": 2700.0, "typ": 380},
}


async def main():
    M.serve()
    async with M.async_playwright() as p:
        b = await p.chromium.launch(executable_path=M.CHROME, args=["--no-sandbox", "--disable-gpu"])
        for name, vp in (("desktop", {"width": 1400, "height": 860}), ("phone", {"width": 390, "height": 844})):
            ctx = await b.new_context(viewport=vp, device_scale_factor=2, service_workers="block", has_touch=name == "phone")
            await ctx.add_init_script(f"localStorage.setItem('sb-ufvskhagspdjslomzwjh-auth-token', {json.dumps(json.dumps(M.SESSION_TOKEN))});"
                                      f"localStorage.setItem('panda.settings', {json.dumps(json.dumps(M.SETTINGS))});")
            await ctx.route(f"{M.SB}/**", M.handle_route)
            page = await ctx.new_page()
            await page.route_web_socket(re.compile(r"wss://ufvskhagspdjslomzwjh\.supabase\.co/realtime/.*"), M.on_ws)
            page.on("pageerror", lambda e: REPORT.setdefault("page_errors", []).append(str(e)[:200]))
            await page.goto(f"{M.APP}/?c={M.CHAT_A}")
            if name == "phone":  # phones start with the pill; tap it to open the sheet
                await page.wait_for_selector(".timeline-pill", timeout=20000)
                REPORT["phone_pill_text"] = await page.locator(".timeline-pill").inner_text()
                await page.screenshot(path=f"{OUT}/timeline-phone-pill.png")
                await page.locator(".timeline-pill").click()
            await page.wait_for_selector(".timeline", timeout=20000)
            await asyncio.sleep(1.2)
            REPORT[f"{name}_now"] = await page.locator(".timeline-now").inner_text()
            REPORT[f"{name}_steps"] = await page.locator(".timeline-list li").count()
            await page.screenshot(path=f"{OUT}/timeline-{name}.png")
            await page.locator(".timeline-head .icon-btn").click()
            await asyncio.sleep(0.3)
            REPORT[f"{name}_pill_after_close"] = await page.locator(".timeline-pill").is_visible()
            await page.locator(".timeline-pill").click()
            REPORT[f"{name}_reopened"] = await page.locator(".timeline").is_visible()
            await ctx.close()
        await b.close()
    print(json.dumps(REPORT, indent=1, ensure_ascii=False))


asyncio.run(main())
