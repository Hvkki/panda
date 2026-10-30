"""`pb upload` (agent issue #36): the path policy, and the three ways the engine puts a file into a page.

    python3 tools/test_browser_upload.py

1. upload_paths (the engine's trust boundary) as a pure function: only regular files under
   ~/outputs, ~/work, ~/uploads; symlinks resolved; 2 GB cap.
2. Real headless Chromium on a local page: a visible <input type=file>, a button that opens the system
   file picker (hidden input clicked by JS), and the "one hidden input" fallback, using the same
   Playwright calls as the engine's upload op.
"""
import asyncio
import http.server
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker/skills/panda-browser"))
import engine as E  # noqa: E402

home = Path(tempfile.mkdtemp(prefix="upl-home-"))
for d in ("outputs", "work/tmp", "uploads", ".panda", ".kiro"):
    (home / d).mkdir(parents=True)
ok_file = home / "work/tmp/banner.png"
ok_file.write_bytes(b"\x89PNG fake banner")
(home / ".panda/secrets.env").write_text("export K=v\n")
(home / "outputs/evil.png").symlink_to(home / ".panda/secrets.env")
(home / "outputs/etc").symlink_to("/etc")


def refused(p, why):
    try:
        E.upload_paths([p], home)
    except ValueError as exc:
        assert why in str(exc), (p, str(exc))
        return
    raise AssertionError(f"accepted {p}")


assert E.upload_paths([str(ok_file)], home) == [str(ok_file.resolve())]
assert E.upload_paths(["work/tmp/banner.png"], home) == [str(ok_file.resolve())], "relative to home"
refused(str(home / ".panda/secrets.env"), "only files under")
refused("/etc/passwd", "only files under")
refused(str(home / "outputs/evil.png"), "only files under")              # a link pointing at the keys
refused(str(home / "outputs/etc/passwd"), "only files under")            # a link to a folder outside
refused(str(home / "work/tmp/../../.kiro/x"), "only files under")
refused(str(home / "outputs/missing.png"), "file not found")
refused(str(home / "work/tmp"), "not a regular file")
refused("", "bad file path")
old = E.UPLOAD_MAX
E.UPLOAD_MAX = 10
refused(str(ok_file), "more than 2 GB")
E.UPLOAD_MAX = old
try:
    E.upload_paths([], home)
    raise AssertionError("empty list accepted")
except ValueError:
    pass
print("ok path policy: outputs/work/uploads only, links resolved, size cap")

PAGE = b"""<!doctype html><html><body>
<input id="vis" type="file" onchange="document.title='vis:'+this.files[0].name">
<button id="btn" onclick="document.getElementById('hid').click()">Upload banner</button>
<input id="hid" type="file" style="display:none" onchange="document.title='hid:'+this.files[0].name">
</body></html>"""
PAGE2 = b"""<!doctype html><html><body><div id="drop">Drop a file here</div>
<input id="only" type="file" style="display:none" onchange="document.title='only:'+this.files[0].name">
</body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE2 if self.path.startswith("/two") else PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"


async def upload(page, loc, files):
    """The engine's strategy, step by step (kept in sync with engine.py's upload op)."""
    kind = ((await loc.evaluate("e => e.tagName + ':' + (e.type || '')", timeout=3000)) or "").lower()
    if kind == "input:file":
        await loc.set_input_files(files, timeout=10000)
        return "file input"
    try:
        async with page.expect_file_chooser(timeout=4000) as fc:
            await loc.click(timeout=4000)
        await (await fc.value).set_files(files, timeout=10000)
        return "file picker"
    except Exception:
        inputs = page.locator("input[type=file]")
        if await inputs.count() == 1:
            await inputs.first.set_input_files(files, timeout=10000)
            return "the page's file input"
    return ""


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        kw = {"executable_path": os.environ["PANDA_CHROMIUM"]} if os.environ.get("PANDA_CHROMIUM") else {}
        b = await p.chromium.launch(headless=True, **kw)
        page = await b.new_page()
        await page.goto(base + "/")
        f = E.upload_paths([str(ok_file)], home)
        assert await upload(page, page.locator("#vis"), f) == "file input"
        assert await page.title() == "vis:banner.png", await page.title()
        assert await upload(page, page.locator("#btn"), f) == "file picker"
        assert await page.title() == "hid:banner.png", await page.title()
        await page.goto(base + "/two")
        assert await upload(page, page.locator("#drop"), f) == "the page's file input"
        assert await page.title() == "only:banner.png", await page.title()
        await b.close()
    print("ok real page: file input, file picker button, single hidden input")


asyncio.run(main())
srv.shutdown()
print("all passed")
