"""Panda worker: runs Claude Code on a Kaggle notebook and serves it to the Panda web app.

No database. The only moving parts are:
  * Kaggle      runs this script, and holds the private dataset with the config (worker.json).
  * Claude Code the real `claude` CLI, logged in with your subscription through the long-lived
                token from `claude setup-token` (stored in worker.json as claude_oauth_token).
  * A tunnel    cloudflared exposes the small HTTP server below to the internet, so the web app on
                Vercel can reach it. A named tunnel (cloudflared_token in worker.json) has a fixed
                URL. Without it a free quick tunnel is used and its URL is published to a tiny
                Kaggle dataset (endpoint_dataset) that the web app's server function reads.

Chats live in the browser. This worker keeps only Claude Code's own session files for the run, and
the web app re-sends the conversation when a session is gone (for example after a restart).

worker.json (private Kaggle dataset, attached to the notebook):
  claude_oauth_token  optional   from `claude setup-token`; the app's "Connect Claude" page can set it
  app_password        required   what you type in the app; also the API bearer token
  cloudflared_token   optional   token of a named Cloudflare tunnel (stable URL)
  endpoint_dataset    optional   "user/panda-endpoint", used with quick tunnels
  kaggle_api_token    optional   to publish the quick-tunnel URL
  kaggle_username     optional   same
  secret_dataset      optional   "user/panda-secret"; where "Connect Claude" saves the new token
  max_runtime_secs    optional   default 39600 (11 h, Kaggle stops at 12 h)

Nothing secret is printed.
"""
from __future__ import annotations

import fcntl
import glob
import hmac
import json
import mimetypes
import os
import pty
import re
import struct
import shutil
import subprocess
import sys
import tempfile
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ── config ───────────────────────────────────────────────────────────────────
_cfg_path = (glob.glob("/kaggle/input/**/worker.json", recursive=True) or [None])[0]
CFG = json.load(open(_cfg_path)) if _cfg_path else json.loads(os.environ["PANDA_WORKER_CFG"])
PASSWORD = CFG["app_password"]
MAX_RUNTIME = int(CFG.get("max_runtime_secs", 11 * 3600))
PORT = int(CFG.get("port", 8787))
ON_KAGGLE = os.path.isdir("/kaggle/working")
WORK = Path("/kaggle/working" if ON_KAGGLE else os.path.abspath("work"))
WORKSPACE = WORK / "workspace"       # Claude Code's working directory; the app's Files page lists it
WORKSPACE.mkdir(parents=True, exist_ok=True)
HOME = Path.home()
BIN = HOME / ".local" / "bin"
T0 = time.time()
MAX_UPLOAD = 100 * 1024 * 1024
MODELS = {"default", "opus", "sonnet", "haiku"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}

ENV = dict(os.environ, CLAUDE_CODE_OAUTH_TOKEN=CFG.get("claude_oauth_token", ""), IS_SANDBOX="1",  # root inside a disposable VM
           PATH=f"{BIN}:{os.environ.get('PATH', '')}")
ENV.pop("ANTHROPIC_API_KEY", None)  # an API key would override the subscription login
STOP = threading.Event()


def scrub(s) -> str:
    s = "" if s is None else str(s)
    for secret in (CFG.get("claude_oauth_token"), PASSWORD, CFG.get("cloudflared_token"), CFG.get("kaggle_api_token")):
        if secret:
            s = s.replace(secret, "***")
    return s


def log(*a):
    print(time.strftime("%H:%M:%S"), scrub(" ".join(map(str, a))), flush=True)


def sh(cmd: str, timeout: int = 900, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, shell=True, env=ENV, capture_output=True, text=True, timeout=timeout)
    log(f"$ {cmd[:60]} -> rc={r.returncode}")
    if r.returncode != 0:
        log("  ", (r.stderr or r.stdout)[-500:])
        if check:
            raise RuntimeError(f"command failed: {cmd[:60]}")
    return r


# ── install ──────────────────────────────────────────────────────────────────
def install():
    BIN.mkdir(parents=True, exist_ok=True)
    if not shutil.which("claude", path=ENV["PATH"]):
        r = sh("curl -fsSL https://claude.ai/install.sh | bash", timeout=900)
        if r.returncode != 0 or not shutil.which("claude", path=ENV["PATH"]):
            sh("npm install -g @anthropic-ai/claude-code", timeout=900, check=True)
    if not shutil.which("cloudflared", path=ENV["PATH"]):
        sh(f"curl -fsSL -o {BIN}/cloudflared "
           "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
           f" && chmod +x {BIN}/cloudflared", check=True)
    # Skip the first-run onboarding screens; auth comes from CLAUDE_CODE_OAUTH_TOKEN.
    cj = HOME / ".claude.json"
    data = json.loads(cj.read_text()) if cj.exists() else {}
    data["hasCompletedOnboarding"] = True
    cj.write_text(json.dumps(data))
    sh("claude --version")


# ── Claude Code runner ───────────────────────────────────────────────────────
RUNNING: dict[str, subprocess.Popen] = {}   # chat id -> live claude process
RUN_LOCK = threading.Lock()


def session_exists(sid: str) -> bool:
    return bool(sid) and bool(glob.glob(str(HOME / ".claude" / "projects" / "*" / f"{sid}.jsonl")))


def tool_label(name: str, inp: dict) -> str:
    inp = inp or {}
    detail = (inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url")
              or inp.get("query") or inp.get("description") or "")
    return f"{name}: {str(detail).strip().splitlines()[0][:140]}" if detail else name


def run_claude(req: dict, emit) -> None:
    """Run one turn. emit(event: dict) is called for every piece of progress."""
    chat = str(req.get("chat_id") or "chat")
    model = req.get("model") if req.get("model") in MODELS else "default"
    effort = req.get("effort") if req.get("effort") in EFFORTS else "medium"
    prompt = str(req.get("message") or "")
    sid = str(req.get("session_id") or "")
    if sid and not session_exists(sid):
        sid = ""
        hist = req.get("history") or []
        if hist:  # the session is gone (new run): give Claude the earlier conversation as plain text
            lines = [f"{'User' if h.get('role') == 'user' else 'Assistant'}: {h.get('content', '')}"
                     for h in hist[-40:] if h.get("content")]
            prompt = ("[Earlier conversation, restored because the session was lost]\n" + "\n\n".join(lines)
                      + "\n[End of earlier conversation]\n\n" + prompt)
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--permission-mode", "bypassPermissions", "--effort", effort]
    if model != "default":
        cmd += ["--model", model]
    if sid:
        cmd += ["--resume", sid]
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd=WORKSPACE, env=ENV, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, bufsize=1)
    with RUN_LOCK:
        old = RUNNING.get(chat)
        if old and old.poll() is None:
            old.kill()
        RUNNING[chat] = p
    errors: list[str] = []
    threading.Thread(target=lambda: errors.extend(scrub(l) for l in p.stderr), daemon=True).start()
    p.stdin.write(prompt)
    p.stdin.close()
    seen_tools: set = set()
    sent_session = ""
    done = False
    try:
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "system" and ev.get("session_id") and ev["session_id"] != sent_session:
                sent_session = ev["session_id"]
                emit({"t": "session", "id": sent_session})
            elif kind == "stream_event":
                e = ev.get("event") or {}
                if e.get("type") == "content_block_delta":
                    d = e.get("delta") or {}
                    if d.get("type") == "text_delta":
                        emit({"t": "text", "d": d.get("text", "")})
                    elif d.get("type") == "thinking_delta":
                        emit({"t": "thinking", "d": d.get("thinking", "")})
                elif e.get("type") == "content_block_start":
                    b = e.get("content_block") or {}
                    if b.get("type") == "tool_use" and b.get("id") not in seen_tools:
                        seen_tools.add(b["id"])
                        emit({"t": "tool", "id": b["id"], "label": b.get("name", "tool")})
            elif kind == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "tool_use":
                        seen_tools.add(b.get("id"))
                        emit({"t": "tool", "id": b.get("id"), "label": tool_label(b.get("name", "tool"), b.get("input"))})
            elif kind == "user":
                for b in (ev.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        emit({"t": "tool_done", "id": b.get("tool_use_id"), "error": bool(b.get("is_error"))})
            elif kind == "result":
                done = True
                emit({"t": "done", "session_id": ev.get("session_id"), "text": ev.get("result") or "",
                      "error": bool(ev.get("is_error")), "secs": round(time.time() - t0, 1),
                      "usage": ev.get("usage"), "turns": ev.get("num_turns")})
        p.wait(timeout=30)
    finally:
        if p.poll() is None:
            p.kill()
        with RUN_LOCK:
            if RUNNING.get(chat) is p:
                RUNNING.pop(chat, None)
    if not done:
        tail = " ".join(errors)[-400:] or f"claude exited with code {p.returncode}"
        emit({"t": "error", "message": tail})


# ── Claude login from the phone ──────────────────────────────────────────────
# Runs `claude setup-token` in a terminal on the worker. The app shows its login link; you sign in
# on claude.ai and paste the code you get back; the printed token is saved to the secret dataset.
ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
LOGIN: dict = {"proc": None, "fd": None, "buf": ""}
LOGIN_LOCK = threading.Lock()


def _login_text() -> str:
    return ANSI.sub("", LOGIN["buf"]).replace("\r", "")


def _login_wait(pattern: str, secs: float) -> re.Match | None:
    end = time.time() + secs
    while time.time() < end:
        m = re.search(pattern, _login_text())
        if m:
            return m
        p = LOGIN["proc"]
        if p is None or p.poll() is not None:
            return re.search(pattern, _login_text())
        time.sleep(0.3)
    return None


def _login_tail() -> str:
    lines = [l.strip() for l in _login_text().splitlines() if l.strip()]
    return scrub(" | ".join(lines[-6:]))[-500:]


def login_stop():
    p, fd = LOGIN["proc"], LOGIN["fd"]
    if p and p.poll() is None:
        p.kill()
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    LOGIN.update(proc=None, fd=None)


def login_start() -> dict:
    with LOGIN_LOCK:
        login_stop()
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 60, 1000, 0, 0))  # wide: no wrapped URL
        env = dict(ENV, TERM="xterm-256color", COLUMNS="1000", LINES="60")
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        p = subprocess.Popen(["claude", "setup-token"], cwd=WORK, env=env, stdin=slave, stdout=slave,
                             stderr=slave, start_new_session=True, close_fds=True)
        os.close(slave)
        LOGIN.update(proc=p, fd=master, buf="")

        def reader():
            while True:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                LOGIN["buf"] = (LOGIN["buf"] + chunk.decode("utf-8", "replace"))[-200000:]

        threading.Thread(target=reader, daemon=True).start()
    m = _login_wait(r"https://\S*oauth/authorize\S+", 45)
    if not m:
        return {"error": "Claude did not show a login link. " + _login_tail()}
    return {"url": m.group(0)}


def login_code(code: str) -> dict:
    code = code.strip()
    if not code or LOGIN["proc"] is None or LOGIN["proc"].poll() is not None:
        return {"error": "The login expired. Tap Connect again."}
    os.write(LOGIN["fd"], code.encode())
    time.sleep(0.5)
    os.write(LOGIN["fd"], b"\r")
    m = _login_wait(r"sk-ant-oat01-[A-Za-z0-9_\-]{20,}", 90)
    if not m:
        tail = _login_tail()
        login_stop()
        return {"error": "No token came back. Check the code and try again. " + tail}
    token = m.group(0)
    login_stop()
    CFG["claude_oauth_token"] = token
    ENV["CLAUDE_CODE_OAUTH_TOKEN"] = token
    saved = save_config()
    log("claude token updated", "and saved" if saved else "(not saved to the dataset)")
    return {"ok": True, "saved": saved}


def save_config() -> bool:
    """Write worker.json back to the private dataset so the next run has the new token."""
    ds = CFG.get("secret_dataset") or (CFG.get("kaggle_username") and f"{CFG['kaggle_username']}/panda-secret")
    if not ds:
        return False
    return kaggle_push(ds, {"worker.json": json.dumps(CFG)}, "token from Connect Claude", private=True)

# ── HTTP API ─────────────────────────────────────────────────────────────────
FAILS = {"n": 0, "until": 0.0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "panda"

    def log_message(self, *a):  # keep the notebook log quiet
        pass

    # helpers
    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "authorization, content-type, x-file-name")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Max-Age", "86400")

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self, limit=1 << 20) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("body too large")
        return self.rfile.read(n)

    def authed(self) -> bool:
        if time.time() < FAILS["until"]:
            return False
        got = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if hmac.compare_digest(got.encode(), PASSWORD.encode()):
            FAILS["n"] = 0
            return True
        FAILS["n"] += 1
        if FAILS["n"] >= 5:  # slow down guessing
            FAILS["until"] = time.time() + 30
            FAILS["n"] = 0
        return False

    # routes
    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self.send_json({"ok": True, "uptime": int(time.time() - T0)})
        if not self.authed():
            return self.send_json({"error": "unauthorized"}, 401)
        if u.path == "/status":
            return self.send_json({"ok": True, "uptime": int(time.time() - T0), "max": MAX_RUNTIME,
                                   "busy": list(RUNNING), "claude": bool(CFG.get("claude_oauth_token"))})
        if u.path == "/files":
            return self.send_json({"files": list_files()})
        if u.path == "/file":
            return self.send_file(parse_qs(u.query).get("path", [""])[0])
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if not self.authed():
            return self.send_json({"error": "unauthorized"}, 401)
        try:
            if u.path == "/chat":
                return self.chat(json.loads(self.body() or b"{}"))
            if u.path == "/cancel":
                chat = str(json.loads(self.body() or b"{}").get("chat_id"))
                with RUN_LOCK:
                    p = RUNNING.get(chat)
                if p and p.poll() is None:
                    p.kill()
                return self.send_json({"ok": True})
            if u.path == "/login/start":
                return self.send_json(login_start())
            if u.path == "/login/code":
                return self.send_json(login_code(str(json.loads(self.body() or b"{}").get("code") or "")))
            if u.path == "/upload":
                return self.upload()
            if u.path == "/stop":
                self.send_json({"ok": True})
                STOP.set()
                return
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            return self.send_json({"error": scrub(exc)}, 400)
        self.send_json({"error": "not found"}, 404)

    def chat(self, req: dict):
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        lock = threading.Lock()
        alive = threading.Event()

        def emit(ev: dict):
            data = f"data: {json.dumps(ev)}\n\n".encode()
            with lock:
                self.wfile.write(data)
                self.wfile.flush()

        def pinger():
            while not alive.wait(15):
                try:
                    with lock:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except Exception:
                    return

        threading.Thread(target=pinger, daemon=True).start()
        try:
            run_claude(req, emit)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the phone went away; the turn keeps running until Claude finishes or /cancel
        except Exception as exc:
            try:
                emit({"t": "error", "message": scrub(exc)})
            except Exception:
                pass
        finally:
            alive.set()
            self.close_connection = True

    def upload(self):
        name = os.path.basename(self.headers.get("X-File-Name") or "upload.bin") or "upload.bin"
        data = self.body(MAX_UPLOAD)
        dest = WORKSPACE / "uploads"
        dest.mkdir(exist_ok=True)
        target = dest / name
        if target.exists():
            target = dest / f"{int(time.time())}-{name}"
        target.write_bytes(data)
        self.send_json({"path": str(target.relative_to(WORKSPACE)), "size": len(data)})

    def send_file(self, rel: str):
        target = (WORKSPACE / rel).resolve()
        if WORKSPACE.resolve() not in target.parents or not target.is_file():
            return self.send_json({"error": "not found"}, 404)
        size = target.stat().st_size
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(target, "rb") as f:
            shutil.copyfileobj(f, self.wfile)


def list_files() -> list:
    out = []
    for root, dirs, files in os.walk(WORKSPACE):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")]
        for fn in files:
            if fn.startswith("."):
                continue
            p = Path(root) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({"path": str(p.relative_to(WORKSPACE)), "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda f: -f["mtime"])
    return out[:500]


# ── tunnel ───────────────────────────────────────────────────────────────────
def start_tunnel() -> tuple[subprocess.Popen, str]:
    """Returns (process, public url or '' for a named tunnel whose URL is fixed)."""
    if CFG.get("cloudflared_token"):
        p = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "run", "--token", CFG["cloudflared_token"]],
                             env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("named tunnel started (url is configured in Cloudflare)")
        return p, ""
    p = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
                         env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    found: list = []

    def reader():
        for line in p.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not found:
                found.append(m.group(0))

    threading.Thread(target=reader, daemon=True).start()
    for _ in range(90):
        if found:
            return p, found[0]
        if p.poll() is not None:
            break
        time.sleep(1)
    raise RuntimeError("cloudflared did not give a tunnel URL")


def kaggle_push(ds: str, files: dict, message: str, private: bool = False) -> bool:
    d = Path(tempfile.mkdtemp(prefix="panda-ds-"))
    try:
        for name, text in files.items():
            (d / name).write_text(text)
        meta = {"id": ds, "title": ds.split("/")[1], "licenses": [{"name": "other"}]}
        if private:
            meta["isPrivate"] = True
        (d / "dataset-metadata.json").write_text(json.dumps(meta))
        env = dict(ENV, KAGGLE_CONFIG_DIR=str(d / ".kaggle"))
        if CFG.get("kaggle_api_token"):
            env["KAGGLE_API_TOKEN"] = CFG["kaggle_api_token"]
        if CFG.get("kaggle_username"):
            env["KAGGLE_USERNAME"] = CFG["kaggle_username"]
        r = subprocess.run(["kaggle", "datasets", "version", "-p", str(d), "-m", message],
                           env=env, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            log(f"dataset {ds} update failed: {(r.stderr or r.stdout)[-300:]}")
        return r.returncode == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def publish_endpoint(url: str):
    """Quick tunnels get a new URL every run: write it to the endpoint dataset for the web app."""
    ds = CFG.get("endpoint_dataset")
    if not ds or not url:
        log("endpoint not published (no endpoint_dataset):", url)
        return
    if kaggle_push(ds, {"endpoint.json": json.dumps({"url": url, "started_at": int(T0)})}, f"endpoint {int(time.time())}"):
        log("endpoint published")


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    install()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"api listening on 127.0.0.1:{PORT}")
    tunnel, url = start_tunnel()
    if url:
        log("PANDA_URL=" + url)
        threading.Thread(target=publish_endpoint, args=(url,), daemon=True).start()
    log("ready")
    while not STOP.is_set():
        if time.time() - T0 > MAX_RUNTIME:
            log("time limit reached, stopping")
            break
        if tunnel.poll() is not None:
            log("tunnel exited, stopping")
            break
        STOP.wait(5)
    with RUN_LOCK:
        for p in RUNNING.values():
            if p.poll() is None:
                p.kill()
    login_stop()
    tunnel.terminate()
    srv.shutdown()
    log("stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("fatal:", type(exc).__name__, exc)
        sys.exit(1)
