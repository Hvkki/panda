"""UI check of the Video tab's Autopilot, Supabase fully mocked (no real account, no Modal, no money).

    python tools/screenshots/mock_autopilot.py <out-dir> <poster.jpg> <clip.mp4> [wide-poster.jpg]

Builds on mock_video.py: same fake gallery, plus autopilot runs in every state (directing, filming the
final with the draft peeking, done, failed) and Modal's bill. Screenshots phone, tablet and desktop,
starts a run, opens the credit sheet and the notes, and checks nothing threw.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mock_video as MV  # noqa: E402  (reads the same argv)

M, NOW, job = MV.M, MV.NOW, MV.job
OUT, REPORT = MV.OUT, MV.REPORT


def log(*steps):
    return [{"t": NOW - 30 * (len(steps) - i), "step": s, "text": t} for i, (s, t) in enumerate(steps)]


DIRECTED = [("director", "Reading your idea"), ("director", "Picking the shape, length, look and sound"),
            ("script", "Wrote the script: 16:9, 6 s, cinematic")]
CHOICE = {"ratio": "16:9", "secs": 6, "style": "cinematic", "audio": True}
WHY = "A seaside road wants width, so it's wide, with the camera riding alongside."
PROMPT = MV.PROMPT

MV.JOBS += [
    job(20, run="r1", phase="draft", quality="draft", w=896, h=512, ratio="16:9", secs=6, title="Pelican by the sea",
        check={"ok": True, "black": 0, "frozen": 0}, poster=f"{M.UID}/video/wide.jpg"),
    job(21, run="r1", phase="final", status="rendering", stage="sampling", step=9, steps=20, pct=50, eta=95, quality="final",
        w=1344, h=768, ratio="16:9", secs=6, title="Pelican by the sea", video=None, poster=None, cost=None),
    job(22, run="r3", phase="final", quality="final", w=768, h=1344, secs=5, steps=20, cost=0.27, title="Red panda chef"),
]
RUNS = [
    {"id": "r0", "created": NOW - 5, "source": "tab", "idea": "a tiny astronaut watering a plant on the moon", "plan": "full",
     "status": "directing", "step": "director", "title": "a tiny astronaut watering a plant on the moon", "jobs": [], "phases": {},
     "log": log(*DIRECTED[:2])},
    {"id": "r1", "created": NOW - 200, "source": "tab", "idea": "a pelican riding a bicycle along the sea at golden hour", "plan": "full",
     "status": "filming", "step": "final", "title": "Pelican by the sea", "choice": CHOICE, "why": WHY, "prompt": PROMPT, "by": "director",
     "jobs": [job(20)["id"], job(21)["id"]], "phases": {"draft": job(20)["id"], "final": job(21)["id"]},
     "log": log(*DIRECTED, ("draft", "Filming a quick draft"), ("check", "Checked the draft: it moves and it's not dark"),
                ("final", "Filming the final in full quality, same take"))},
    {"id": "r3", "created": NOW - 900, "source": "chat", "idea": "un panda rosso chef che gira i pancake all'alba", "plan": "full",
     "status": "done", "step": "done", "title": "Panda chef all'alba", "choice": {"ratio": "9:16", "secs": 5, "style": "dreamy", "audio": True},
     "why": "Verticale per il panda da solo, luce rosa dell'alba.", "prompt": PROMPT, "by": "director", "best": job(22)["id"],
     "jobs": [job(22)["id"]], "phases": {"final": job(22)["id"]}, "cost": 0.34, "log": log(*DIRECTED, ("done", "Ready to watch"))},
    {"id": "r4", "created": NOW - 1500, "source": "tab", "idea": "aura edit of a samurai in the rain", "plan": "quick",
     "status": "failed", "step": "failed", "title": "Samurai aura", "choice": {"ratio": "9:16", "secs": 7, "style": "aura", "audio": True},
     "by": "guess", "error": "The GPU ran out of memory. Try a shorter clip or a draft.", "jobs": [], "phases": {}, "cost": 0.04,
     "log": log(*DIRECTED, ("draft", "Filming a quick draft"), ("failed", "The GPU ran out of memory."))},
]
MV.FILE.update(v=2, runs=RUNS, modal={"at": NOW - 240, "used": 9.98, "credit": 30, "panda": 4.71, "other": 5.27, "pending": 0.27,
                                      "booked": 0.36, "now": 10.25, "cap": 30, "left": 19.39, "month": "2026-10", "source": "modal",
                                      "counted_to": NOW - 2000})
NEW_RUN = {**RUNS[0], "id": "r9", "created": NOW, "idea": "a paper boat racing down a rainy street",
           "title": "a paper boat racing down a rainy street", "log": log(("director", "Reading your idea"))}
M.COMMAND_RESULTS["video.auto"] = {"run": NEW_RUN}
M.COMMAND_RESULTS["video.usage"] = {**MV.FILE["modal"], "at": NOW + 5}


async def main():
    srv = M.serve()
    async with M.async_playwright() as p:
        b = await p.chromium.launch(executable_path=M.CHROME, args=["--no-sandbox", "--disable-gpu"])
        for name, vp, theme in (("phone", {"width": 390, "height": 844}, "light"), ("tablet", {"width": 820, "height": 1180}, "dark"),
                                ("desktop", {"width": 1366, "height": 860}, "light")):
            M.ST.theme = theme
            settings = {**M.SETTINGS, "theme": theme}
            ctx = await b.new_context(viewport=vp, device_scale_factor=2, service_workers="block", has_touch=name != "desktop")
            await ctx.add_init_script(f"localStorage.setItem('sb-ufvskhagspdjslomzwjh-auth-token', {json.dumps(json.dumps(M.SESSION_TOKEN))});"
                                      f"localStorage.setItem('panda.settings', {json.dumps(json.dumps(settings))});"
                                      "localStorage.removeItem('panda.video.pilot'); localStorage.removeItem('panda.video.auto.idea');")
            await ctx.route(f"{M.SB}/**", MV.route)
            page = await ctx.new_page()
            errs = REPORT.setdefault(f"{name}_errors", [])
            page.on("pageerror", lambda e, errs=errs: errs.append(str(e)[:200]))
            page.on("console", lambda m, errs=errs: errs.append(m.text[:200]) if m.type == "error" and "favicon" not in m.text else None)
            await page.goto(f"{M.APP}/?v=video")
            await page.wait_for_selector(".ap-desk", timeout=20000)
            await page.wait_for_selector(".ap-run", timeout=10000)
            await asyncio.sleep(1.5)
            REPORT[f"{name}_runs"] = await page.locator(".ap-run").count()
            REPORT[f"{name}_meter"] = await page.locator(".ap-meter").inner_text()
            REPORT[f"{name}_steps_now"] = await page.locator(".ap-steps li.now").count()
            REPORT[f"{name}_on_set"] = await page.locator(".vs-q").count()
            await page.screenshot(path=f"{OUT}/auto-{name}-top.png")
            await page.locator(".ap-runs").scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            await page.screenshot(path=f"{OUT}/auto-{name}-runs.png")
            await page.screenshot(path=f"{OUT}/auto-{name}-full.png", full_page=True)
            # notes on the filming run
            await page.locator(".ap-run.filming .ap-log-toggle").click()
            REPORT[f"{name}_notes"] = await page.locator(".ap-run.filming .ap-log li").count()
            await page.locator(".ap-run.filming").screenshot(path=f"{OUT}/auto-{name}-filming-card.png")
            # credit sheet
            await page.evaluate("window.scrollTo(0,0); document.querySelector('.vs-page')?.scrollTo(0,0)")
            await page.locator(".ap-meter").click()
            await page.wait_for_selector(".ap-sheet", timeout=4000)
            await page.locator(".ap-sheet button:has-text('Read the bill now')").click()
            await asyncio.sleep(0.6)
            await page.locator(".ap-sheet").screenshot(path=f"{OUT}/auto-{name}-credit.png")
            REPORT[f"{name}_sheet"] = (await page.locator(".ap-sheet").inner_text())[:200]
            await page.locator(".ap-sheet .icon-btn").click()
            # start a run
            await page.locator(".ap-idea textarea").fill("a paper boat racing down a rainy street")
            await page.locator(".ap-plan").nth(1).click()
            await page.locator(".ap-finder").screenshot(path=f"{OUT}/auto-{name}-finder.png")
            await page.locator(".ap-go").click()
            await asyncio.sleep(1.2)
            REPORT[f"{name}_runs_after"] = await page.locator(".ap-run").count()
            REPORT[f"{name}_err"] = await page.locator(".vs-err").all_inner_texts()
            # manual mode still there
            await page.locator(".ap-switch button").nth(1).click()
            await page.wait_for_selector(".vs-slate", timeout=4000)
            REPORT[f"{name}_manual"] = await page.locator(".vs-slate").count()
            await ctx.close()
        await b.close()
    srv.shutdown()
    REPORT["commands_sent"] = [(c.get("op"), c.get("args")) for c in M.ST.commands.values()]
    print(json.dumps(REPORT, indent=1)[:4000])


if __name__ == "__main__":
    asyncio.run(main())
