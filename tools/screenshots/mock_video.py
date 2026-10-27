"""UI check of the Video tab with Supabase fully mocked (no real account, no Modal, no money).

    python tools/screenshots/mock_video.py <out-dir> <poster.jpg> <clip.mp4> [wide-poster.jpg]

Serves app/dist, fakes the gallery file (jobs.json) with every job state, the poster and clip
downloads, and the worker's video.prompt / video.submit answers. Screenshots phone, tablet and
desktop, opens the screening room, writes a script, and checks nothing threw.
"""
import asyncio
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ARGS = sys.argv[1:]
sys.argv = sys.argv[:2]
import mock_ui as M  # noqa: E402

OUT = M.OUT
POSTER = open(ARGS[1], "rb").read()
CLIP = open(ARGS[2], "rb").read()
WIDE = open(ARGS[3], "rb").read() if len(ARGS) > 3 else POSTER
REPORT: dict = {}
NOW = time.time()
PROMPT = ("integrated_multimodal_description: [Shot 1] A white pelican with a big orange throat pouch rides an old cream "
          "bicycle along a sunny seaside promenade; the camera tracks alongside with small amplitude at medium speed. "
          "[Shot 2] At 00:03.000 the pelican lifts one wing and waves at fast speed.\n"
          "overall_soundscape: bicycle chain ticking, gentle waves, gulls far away.\nnon_diegetic_music: light whistled acoustic tune")


def job(i, **kw):
    base = {"id": f"261027-10{i:02d}00-ab{i:02d}", "group": "g", "created": NOW - 600 * i, "source": "tab", "title": "Pelican on a bicycle",
            "idea": "a pelican riding a bicycle by the sea", "style": "cinematic", "prompt": PROMPT, "ratio": "9:16", "seed": 1234 + i,
            "mode": "clip", "quality": "draft", "w": 512, "h": 896, "secs": 5, "frames": 121, "steps": 8, "loras": [["turbo8", 0.8]],
            "est": {"secs": 117, "usd": 0.117, "usd_high": 0.168, "warm": False, "parts": {"start": 65, "sampling": 29, "other": 23}},
            "status": "done", "stage": "done", "step": 8, "pct": 100, "cost": 0.066, "billed_secs": 66.2, "cold": True,
            "video": f"{M.UID}/video/{i}.mp4", "poster": f"{M.UID}/video/{i}.jpg"}
    base.update(kw)
    return base


JOBS = [
    job(0, status="rendering", stage="sampling", step=3, pct=44, eta=38, started=NOW - 40, title="Samurai in the rain",
        video=None, poster=None, cost=None),
    job(1, status="queued", stage="queued", step=0, pct=0, eta=110, title="Paper boat race", video=None, poster=None, cost=None),
    job(2),
    job(3, quality="final", w=1344, h=768, ratio="16:9", steps=20, cost=0.27, title="Pelican, the final", poster=f"{M.UID}/video/wide.jpg"),
    job(4, status="failed", stage="failed", pct=30, error="The GPU ran out of memory. Try a shorter clip or a draft.",
        video=None, poster=None, cost=0.02, title="Ten-second orbit"),
    job(5, mode="orbit", w=768, h=768, ratio="1:1", secs=3, title="Camera orbit", cost=0.07),
]
FILE = {"v": 1, "at": NOW, "jobs": JOBS, "budget": 10, "spent": 0.43, "rate_usd_s": 0.001001,
        "stats": {"a": 7.0, "b": 1.2, "n": 3, "stage": {"reading prompt": 7.4, "decoding": 7.5, "saving": 1.9, "loading": 13.2}, "cold_up": 23.6},
        "gpu": {"state": "busy", "t": NOW + 3600, "gpu": "NVIDIA RTX PRO 6000"}}
M.COMMAND_RESULTS["video.prompt"] = {"prompt": PROMPT, "by": "writer"}
M.COMMAND_RESULTS["video.submit"] = {"jobs": [job(9, status="queued", stage="queued", pct=0, video=None, poster=None, cost=None,
                                                  title="Pelican on a bicycle")], "total_usd": 0.07, "total_high": 0.1}
BASE_ROUTE = M.handle_route


async def route(r):
    path = r.request.url.split("?")[0]
    if "/storage/v1/object/sign/" in path and r.request.method == "GET":
        if path.endswith("jobs.json"):
            return await r.fulfill(status=200, content_type="application/json", body=json.dumps(FILE))
        if path.endswith(".mp4"):
            return await r.fulfill(status=200, content_type="video/mp4", body=CLIP)
        if path.endswith("wide.jpg"):
            return await r.fulfill(status=200, content_type="image/jpeg", body=WIDE)
        if path.endswith(".jpg"):
            return await r.fulfill(status=200, content_type="image/jpeg", body=POSTER)
    return await BASE_ROUTE(r)


async def main():
    srv = M.serve()
    async with M.async_playwright() as p:
        b = await p.chromium.launch(executable_path=M.CHROME, args=["--no-sandbox", "--disable-gpu", "--autoplay-policy=no-user-gesture-required"])
        for name, vp, theme in (("phone", {"width": 390, "height": 844}, "light"), ("tablet", {"width": 820, "height": 1180}, "dark"),
                                ("desktop", {"width": 1366, "height": 860}, "light")):
            M.ST.theme = theme
            settings = {**M.SETTINGS, "theme": theme}
            ctx = await b.new_context(viewport=vp, device_scale_factor=2, service_workers="block", has_touch=name != "desktop")
            await ctx.add_init_script(f"localStorage.setItem('sb-ufvskhagspdjslomzwjh-auth-token', {json.dumps(json.dumps(M.SESSION_TOKEN))});"
                                      f"localStorage.setItem('panda.settings', {json.dumps(json.dumps(settings))});"
                                      "localStorage.removeItem('panda.video.draft');")
            await ctx.route(f"{M.SB}/**", route)
            page = await ctx.new_page()
            errs = REPORT.setdefault(f"{name}_errors", [])
            page.on("pageerror", lambda e, errs=errs: errs.append(str(e)[:200]))
            page.on("console", lambda m, errs=errs: errs.append(m.text[:200]) if m.type == "error" and "favicon" not in m.text else None)
            await page.goto(f"{M.APP}/?v=video")
            await page.wait_for_selector(".vs-slate", timeout=20000)
            await page.wait_for_selector(".vs-card img", timeout=10000)
            await asyncio.sleep(1.2)
            REPORT[f"{name}_cards"] = await page.locator(".vs-card").count()
            REPORT[f"{name}_on_set"] = await page.locator(".vs-q").count()
            REPORT[f"{name}_draft_price"] = await page.locator(".vs-half.primary .vs-price b").inner_text()
            await page.screenshot(path=f"{OUT}/video-{name}-top.png")
            await page.screenshot(path=f"{OUT}/video-{name}-full.png", full_page=True)
            # the side column (queue + gallery) scrolled into view
            await page.locator(".vs-gallery").scroll_into_view_if_needed()
            await asyncio.sleep(0.4)
            await page.screenshot(path=f"{OUT}/video-{name}-gallery.png")
            # write the script
            await page.locator(".vs-idea textarea").fill("a pelican riding a bicycle by the sea")
            await page.locator(".vs-write").click()
            await page.wait_for_selector(".vs-script-body", timeout=8000)
            REPORT[f"{name}_script_fields"] = await page.locator(".vs-sf textarea").count()
            await page.locator(".vs-script").scroll_into_view_if_needed()
            await page.screenshot(path=f"{OUT}/video-{name}-script.png")
            # make a draft
            await page.locator(".vs-half.primary .btn").click()
            await asyncio.sleep(1.0)
            REPORT[f"{name}_after_make_on_set"] = await page.locator(".vs-q").count()
            REPORT[f"{name}_err"] = await page.locator(".vs-err").all_inner_texts()
            # the screening room
            await page.locator(".vs-card:not(.failed)").first.click()
            await page.wait_for_selector(".vs-screen video", timeout=8000)
            await asyncio.sleep(1.5)
            REPORT[f"{name}_video_ready"] = await page.evaluate("document.querySelector('.vs-screen video').readyState")
            await page.screenshot(path=f"{OUT}/video-{name}-screening.png")
            await page.keyboard.press("Escape")
            await ctx.close()
        await b.close()
    srv.shutdown()
    sent = [c.get("op") for c in M.ST.commands.values()]
    REPORT["commands_sent"] = sent
    print(json.dumps(REPORT, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
