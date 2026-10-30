"""The browser's private-address guard, and big uploads through it (issue: 300 MB YouTube upload hung).

    python3 tools/test_browser_guard.py

Runs the engine's real _start_guard/_guard_check on a throwaway headless Chromium (never the live engine):
1. a 300 MB POST from a page goes through at full speed (Playwright routing used to copy every body into
   Python and crashed its driver with EPIPE);
2. the guard still blocks a blocked address in the tab, in a popup and in a cross-site iframe, and in a
   context made after it started (the engine's "forget" makes a new one).
"""
import asyncio
import http.server
import multiprocessing
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker/skills/panda-browser"))
import engine as E  # noqa: E402

SIZE = 300 << 20
tmp = tempfile.mkdtemp(prefix="guard-")
big = os.path.join(tmp, "big.bin")
with open(big, "wb") as f:
    f.truncate(SIZE)   # sparse: no disk or time spent writing it

PAGE = b"""<!doctype html><input type=file id=f><script>
document.getElementById('f').onchange = async e => {
  const r = await fetch('/up', {method: 'POST', body: e.target.files[0]}); window.done = await r.text(); };
</script>"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def do_POST(self):
        left = n = int(self.headers["Content-Length"])
        while left > 0:
            left -= len(self.rfile.read(min(left, 1 << 20)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(str(n).encode())


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
port = srv.server_port
# Its own process: a server thread here would fight the test's event loop for the GIL and time that, not the browser.
server = multiprocessing.get_context("fork").Process(target=srv.serve_forever, daemon=True)
server.start()
# The test server is local, which the real rule blocks: here only paths with "blockme" are "private".
E.url_ok = lambda url: (("blockme" not in url), "private, local and internal addresses are blocked")


class Probe:
    """Just what the guard methods touch on the Engine."""
    _start_guard = E.Engine._start_guard
    _guard_check = E.Engine._guard_check

    def __init__(self, browser):
        self.browser, self.guard, self.main_frame_id, self.page, self.loads = browser, None, "", None, 0

    def _loading(self, page, on):
        self.loads += on


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        kw = {"executable_path": os.environ["PANDA_CHROMIUM"]} if os.environ.get("PANDA_CHROMIUM") else {}
        b = await p.chromium.launch(headless=True, **kw)
        eng = Probe(b)
        await eng._start_guard()
        ctx = await b.new_context()
        page = await ctx.new_page()
        s = await ctx.new_cdp_session(page)
        eng.main_frame_id = (await s.send("Target.getTargetInfo"))["targetInfo"]["targetId"]
        await page.goto(f"http://127.0.0.1:{port}/")
        assert eng.loads >= 1, "main-frame load did not turn 'loading' on"

        t = time.time()
        await page.set_input_files("#f", big)
        await page.wait_for_function("window.done", timeout=60000)
        secs = time.time() - t
        assert await page.evaluate("window.done") == str(SIZE)
        assert secs < 40, f"300 MB upload took {secs:.0f}s"
        print(f"ok 300 MB upload through the guard in {secs:.1f}s")

        try:
            await page.goto(f"http://127.0.0.1:{port}/blockme")
            raise AssertionError("tab navigation not blocked")
        except Exception as exc:
            assert "ERR_BLOCKED_BY_CLIENT" in str(exc), exc
        await asyncio.sleep(0.5)   # let Chromium finish showing its error page first
        await page.goto(f"http://127.0.0.1:{port}/")
        async with ctx.expect_page() as pp:
            await page.evaluate(f"window.open('http://127.0.0.1:{port}/blockme-popup')")
        pop = await pp.value
        await pop.wait_for_load_state()
        assert pop.url.startswith("chrome-error://"), f"popup loaded {pop.url}"
        await page.evaluate(f"document.body.innerHTML = '<iframe src=\"http://localhost:{port}/blockme-frame\"></iframe>'")
        await asyncio.sleep(1.5)
        assert not any("blockme" in f.url for f in page.frames), [f.url for f in page.frames]
        ctx2 = await b.new_context()
        p2 = await ctx2.new_page()
        try:
            await p2.goto(f"http://127.0.0.1:{port}/blockme-new-context")
            raise AssertionError("new context not guarded")
        except Exception as exc:
            assert "ERR_BLOCKED_BY_CLIENT" in str(exc), exc
        print("ok blocked in tab, popup, cross-site iframe and a new context")
        await b.close()


asyncio.run(main())
server.terminate()
os.remove(big)
print("all passed")
