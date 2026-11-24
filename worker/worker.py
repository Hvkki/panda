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
  dev_env             optional   {"NAME": "value"} only the Developer chat sees (set in the app)
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
import uuid
import termios
import threading
import time
import urllib.parse
import urllib.request
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
MODELS = {"default", "opus", "sonnet", "haiku", "fable", "opusplan"}
MODEL_ID = re.compile(r"^claude-[a-z0-9.-]{3,40}(\[1m\])?$")  # full ids like claude-opus-5-5
EFFORTS = {"low", "medium", "high", "xhigh", "max"}

ENV = dict(os.environ, CLAUDE_CODE_OAUTH_TOKEN=CFG.get("claude_oauth_token", ""), IS_SANDBOX="1",  # root inside a disposable VM
           PATH=f"{BIN}:{os.environ.get('PATH', '')}")
ENV.pop("ANTHROPIC_API_KEY", None)  # an API key would override the subscription login
STOP = threading.Event()


def scrub(s) -> str:
    s = "" if s is None else str(s)
    for secret in (CFG.get("claude_oauth_token"), PASSWORD, CFG.get("cloudflared_token"), CFG.get("kaggle_api_token"),
                   *(CFG.get("dev_env") or {}).values(), *(CFG.get("env") or {}).values()):
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
TURNS: dict = {}   # chat_id -> the latest turn's buffered events, so a dropped stream can resume


def session_exists(sid: str) -> bool:
    return bool(sid) and bool(glob.glob(str(HOME / ".claude" / "projects" / "*" / f"{sid}.jsonl")))


def tool_label(name: str, inp: dict) -> str:
    inp = inp or {}
    detail = (inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url")
              or inp.get("query") or inp.get("description") or "")
    return f"{name}: {str(detail).strip().splitlines()[0][:140]}" if detail else name


DEV_PROMPT = """This is Panda's Developer chat. Here you may change Panda itself.
Panda is a web app (React, Vite, in app/) on Vercel plus this Kaggle worker (worker/worker.py), from the
GitHub repo {repo}, branch {branch}. The repo is already checked out in ./panda: work there.
{git}
Save the owner's plan limits: every step re-sends this whole conversation, so keep it small. Use Grep and read
only the lines you need (styles.css and worker.py are large), and cut long command output with `| tail -20`.
To publish (needs VERCEL_TOKEN): cd panda/app && npm ci && cd .. && python3 scripts/deploy-app.py
(it builds the app first and stops if the build fails). That one deploy updates both the web app and
worker.py (the worker downloads it from the deployment), so worker changes take effect the next time the
worker is started. Run `python3 -m py_compile worker/worker.py` after worker edits: a broken deploy breaks
Panda for the owner.
Model facts (no need to look them up): Opus 5.5, Sonnet 5.5, Haiku 5.5 and Fable 5.2 all have a 1M-token
context window.
Developer variables available in your environment: {names}. If one you need is missing, ask the owner to add
it in Developer -> Env variables. Never print their values."""
GIT_PUSH = "Commit and push your changes to that branch (GITHUB_TOKEN is set; the remote already uses it)."
GIT_LOCAL = ("There is no GITHUB_TOKEN, so you cannot push to GitHub. Commit locally if you like; Panda saves "
             "every change in ./panda (committed or not) to the owner's private Kaggle dataset after each turn "
             "and restores it on the next run. Tell the owner that changes are live on Vercel but not on GitHub.")
DEV_PATCH_MAX = 8 << 20


def dev_repo(repo: str, branch: str, token: str) -> str:
    """Make sure ./panda exists in the developer folder, with the saved changes re-applied. Returns a note
    for Claude when something went wrong."""
    d = WORKSPACE / "developer" / "panda"
    url = f"https://x-access-token:{token}@github.com/{repo}.git" if token else f"https://github.com/{repo}.git"
    run = lambda *a, **k: subprocess.run(a, cwd=k.get("cwd", d), capture_output=True, text=True, timeout=600)
    if not (d / ".git").exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        r = run("git", "clone", "--branch", branch, url, str(d), cwd=d.parent)
        if r.returncode != 0:
            return "Cloning the repo failed: " + scrub(r.stderr)[-300:]
        run("git", "config", "user.name", "Panda Developer")
        run("git", "config", "user.email", "panda@users.noreply.github.com")
        patch = CFG.get("dev_patch") or ""
        if patch:
            (d.parent / "saved-changes.patch").write_text(patch)
            r = run("git", "apply", "--3way", "--whitespace=nowarn", str(d.parent / "saved-changes.patch"))
            if r.returncode != 0:
                return ("Your saved changes from an earlier run did not apply cleanly to the current branch; "
                        "they are in ../saved-changes.patch. " + scrub(r.stderr)[-300:])
            run("git", "reset", "-q")
    else:
        run("git", "remote", "set-url", "origin", url)
    return ""


def dev_snapshot(branch: str) -> None:
    """Save everything in ./panda that is not on GitHub's branch (commits, edits, new files) as one patch."""
    d = WORKSPACE / "developer" / "panda"
    if not (d / ".git").exists():
        return
    try:
        subprocess.run(["git", "add", "-A"], cwd=d, capture_output=True, timeout=120)
        r = subprocess.run(["git", "diff", "--binary", "--cached", f"origin/{branch}"], cwd=d,
                           capture_output=True, text=True, timeout=120)
        subprocess.run(["git", "reset", "-q"], cwd=d, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        log("dev snapshot failed:", e)
        return
    if r.returncode != 0:
        log("dev snapshot failed:", r.stderr[-200:])
        return
    patch = r.stdout
    if len(patch) > DEV_PATCH_MAX:
        log("dev changes too large to save:", len(patch))
        return
    if patch != (CFG.get("dev_patch") or ""):
        with CFG_LOCK:
            CFG["dev_patch"] = patch
        save_soon()


def folder_for(req: dict) -> Path:
    if req.get("dev"):
        name = "developer"
    elif req.get("scheduled"):
        name = "scheduled"
    else:
        name = re.sub(r"[^A-Za-z0-9_-]", "", str(req.get("folder") or ""))[:64]
        name = f"projects/{name}" if name else ""
    d = WORKSPACE / name if name else WORKSPACE
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_claude(req: dict, emit) -> None:
    """Run one turn. emit(event: dict) is called for every piece of progress."""
    chat = str(req.get("chat_id") or "chat")
    model = str(req.get("model") or "")
    model = model if model in MODELS or MODEL_ID.match(model) else "default"
    effort = req.get("effort") if req.get("effort") in EFFORTS else "medium"
    prompt = str(req.get("message") or "")
    sid = str(req.get("session_id") or "")
    if sid and not session_exists(sid):
        sid = ""
        hist = req.get("history") or []
        if hist and not prompt.startswith("/"):  # the session is gone (new run): give Claude the earlier conversation as plain text
            lines = [f"{'User' if h.get('role') == 'user' else 'Assistant'}: {h.get('content', '')}"
                     for h in hist[-40:] if h.get("content")]
            prompt = ("[Earlier conversation, restored because the session was lost]\n" + "\n\n".join(lines)
                      + "\n[End of earlier conversation]\n\n" + prompt)
    cmd = ["claude", "-p"]
    if req.get("dev"):  # the claude-api reference skill alone is ~46k tokens; Panda work never needs it
        cmd += ["--disallowedTools", "Skill(claude-api)"]
    cmd += ["--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--permission-mode", "bypassPermissions", "--effort", effort]
    if model != "default":
        cmd += ["--model", model]
    if sid:
        cmd += ["--resume", sid]
    system = str(req.get("system") or "")[:20000]
    env = dict(ENV, **(CFG.get("env") or {}))
    if req.get("dev"):
        dev_env = CFG.get("dev_env") or {}
        env.update(dev_env)
        branch = dev_env.get("PANDA_BRANCH") or "claude/amazing-mccarthy-ejh4o9"
        repo = dev_env.get("PANDA_REPO") or "hvkki/panda"
        token = dev_env.get("GITHUB_TOKEN", "")
        emit({"t": "tool", "id": "dev-repo", "label": "Preparing the Panda repo"})
        note = dev_repo(repo, branch, token)
        emit({"t": "tool_done", "id": "dev-repo", "error": bool(note)})
        system = DEV_PROMPT.format(repo=repo, branch=branch, git=GIT_PUSH if token else GIT_LOCAL,
                                   names=", ".join(sorted(dev_env)) or "none yet") + (
            f"\n\nNote: {note}" if note else "") + ("\n\n" + system if system else "")
    system = "\n\n".join(x for x in (extra_system(req), system) if x)
    if system:
        cmd += ["--append-system-prompt", system]
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd=folder_for(req), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
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
    ctx = 0  # tokens in the context window after the latest API call
    try:
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "system" and ev.get("subtype") == "init":
                note_commands(bool(req.get("dev")), ev)
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
            elif kind == "rate_limit_event":
                note_limits(ev.get("rate_limit_info") or {})
                emit({"t": "limits"})
            elif kind == "assistant":
                u = (ev.get("message") or {}).get("usage") or {}
                if ev.get("parent_tool_use_id") is None and u.get("input_tokens") is not None:
                    ctx = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                            "cache_read_input_tokens", "output_tokens"))
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
                      "usage": turn_usage(ev, ctx), "turns": ev.get("num_turns")})
        p.wait(timeout=30)
    finally:
        if p.poll() is None:
            p.kill()
        with RUN_LOCK:
            if RUNNING.get(chat) is p:
                RUNNING.pop(chat, None)
        if req.get("dev"):
            dev_snapshot(branch)
    if not done:
        tail = " ".join(errors)[-400:] or f"claude exited with code {p.returncode}"
        emit({"t": "error", "message": tail})


# ── slash commands ───────────────────────────────────────────────────────────
COMMANDS: dict = {}   # "dev" / "chat" -> {"at": time, "commands": [...], "skills": [...]}


def note_commands(dev: bool, init: dict) -> None:
    """Remember the slash commands Claude Code reports in its init event (minus terminal-only ones)."""
    term = set(init.get("terminal_slash_commands") or [])
    cmds = [c for c in init.get("slash_commands") or [] if isinstance(c, str) and not c.startswith("_") and c not in term]
    COMMANDS["dev" if dev else "chat"] = {"at": time.time(), "commands": cmds, "skills": init.get("skills") or []}


def slash_commands(dev: bool) -> dict:
    """Claude Code's slash commands. Starts `claude` just long enough to read its init event (no API call)."""
    key = "dev" if dev else "chat"
    hit = COMMANDS.get(key)
    if hit and time.time() - hit["at"] < 900:
        return hit
    cwd = folder_for({"dev": dev})
    try:
        p = subprocess.Popen(["claude", "-p", "--output-format", "stream-json", "--verbose"], cwd=cwd, env=ENV,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        p.stdin.write("/context")  # a local command: even if the read below is slow, no model call is made
        p.stdin.close()
        timer = threading.Timer(60, p.kill)
        timer.start()
        try:
            for line in p.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    note_commands(dev, ev)
                    break
        finally:
            timer.cancel()
            p.kill()
            p.wait(timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        log("reading slash commands failed:", e)
    return COMMANDS.get(key) or {"at": 0, "commands": [], "skills": []}


# ── token and plan usage ─────────────────────────────────────────────────────
LIMITS: dict = {}          # window ("five_hour", "seven_day"…) -> {"pct": 0-100, "resets_at": epoch secs}
USAGE_CACHE: dict = {"at": 0.0, "data": None}
WINDOW_KEYS = ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet", "seven_day_oauth_apps")


def turn_usage(ev: dict, ctx: int) -> dict:
    """Token totals for one turn, from Claude Code's result event."""
    u = ev.get("usage") or {}
    mu = ev.get("modelUsage") or {}
    window = max([int(m.get("contextWindow") or 0) for m in mu.values() if isinstance(m, dict)] or [0])
    tot = lambda k: sum(int(m.get(k) or 0) for m in mu.values() if isinstance(m, dict))
    return {
        "input": tot("inputTokens") or int(u.get("input_tokens") or 0),
        "output": tot("outputTokens") or int(u.get("output_tokens") or 0),
        "cache_read": tot("cacheReadInputTokens") or int(u.get("cache_read_input_tokens") or 0),
        "cache_write": tot("cacheCreationInputTokens") or int(u.get("cache_creation_input_tokens") or 0),
        "cost": round(float(ev.get("total_cost_usd") or 0), 4),
        "ctx": ctx, "window": window or 1000000,
    }


def _pct(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v * 100 if v <= 1.0 else v, 1)  # fractions (0-1) or percents


def _epoch(v) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def note_limits(info: dict):
    """Remember plan usage from a rate_limit_event of a running chat."""
    for k, w in (info.get("unifiedWindows") or {}).items():
        if isinstance(w, dict) and _pct(w.get("utilization")) is not None:
            LIMITS[k] = {"pct": _pct(w.get("utilization")), "resets_at": _epoch(w.get("resetsAt"))}
    k = info.get("rateLimitType")
    if k and _pct(info.get("utilization")) is not None:
        LIMITS[k] = {"pct": _pct(info.get("utilization")), "resets_at": _epoch(info.get("resetsAt"))}
    if LIMITS:
        LIMITS["_at"] = time.time()


def _oauth_usage(token: str) -> dict:
    """The same endpoint Claude Code's /usage reads."""
    rq = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "User-Agent": "claude-cli",
        "Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=15) as r:
        d = json.load(r)
    out = {}
    for k, w in d.items():
        if isinstance(w, dict) and _pct(w.get("utilization")) is not None and k != "extra_usage":
            out[k] = {"pct": _pct(w["utilization"]), "resets_at": _epoch(w.get("resets_at"))}
    ex = d.get("extra_usage")
    if isinstance(ex, dict) and ex.get("is_enabled"):
        out["extra_usage"] = {"pct": _pct(ex.get("utilization")) or 0, "resets_at": None,
                              "used": ex.get("used_credits"), "limit": ex.get("monthly_limit")}
    return out


def plan_usage(force: bool = False) -> dict:
    if not force and USAGE_CACHE["data"] and time.time() - USAGE_CACHE["at"] < 60:
        return USAGE_CACHE["data"]
    token = CFG.get("claude_oauth_token") or ""
    windows, source, err = {}, "", ""
    if token:
        for name, fn in (("account", _oauth_usage),):
            try:
                windows = fn(token)
            except Exception as exc:
                err = scrub(exc)
                continue
            if windows:
                source = name
                break
    if not windows and LIMITS:
        windows, source = {k: v for k, v in LIMITS.items() if not k.startswith("_")}, "chat"
    elif windows:
        for k, v in LIMITS.items():  # fresher numbers seen in chats fill gaps
            if not k.startswith("_") and k not in windows:
                windows[k] = v
    data = {"windows": windows, "source": source, "at": int(time.time()),
            "error": "" if windows else ("Connect Claude first" if not token else
                                         "Plan usage shows up after your first message in this run")}
    USAGE_CACHE.update(at=time.time(), data=data)
    return data


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
    save_soon(0.5)
    log("claude token updated")
    return {"ok": True}


SAVE_CFG_LOCK = threading.Lock()


def save_config() -> bool:
    """Write worker.json back to the private dataset so the next run has the new token."""
    ds = CFG.get("secret_dataset") or (CFG.get("kaggle_username") and f"{CFG['kaggle_username']}/panda-secret")
    if not ds:
        return False
    with SAVE_CFG_LOCK:
        with CFG_LOCK:
            text = json.dumps(CFG)
        ok = kaggle_push(ds, {"worker.json": text}, "saved from the Panda app", private=True)
    log("settings saved" if ok else "settings save failed")
    return ok

# ── saved state: skills, connectors, keys, memory, schedules ─────────────────
# Everything the app customizes lives in worker.json (the private dataset), so it survives restarts.
# Saves are batched: a change is written to Kaggle a few seconds after the last edit.
SAVE = {"timer": None, "state": "saved"}
SAVE_LOCK = threading.Lock()
CFG_LOCK = threading.Lock()
NAME_RE = r"[A-Z][A-Z0-9_]{0,63}"
SKILL_RE = r"[a-z0-9][a-z0-9-]{0,63}"


def settings() -> dict:
    return {"browser": True, "memory": True, **(CFG.get("settings") or {})}


def _flush():
    with SAVE_LOCK:
        SAVE["timer"] = None
    ok = save_config()
    with SAVE_LOCK:
        if SAVE["timer"] is None:
            SAVE["state"] = "saved" if ok else "error"


def save_soon(delay: float = 4):
    with SAVE_LOCK:
        if SAVE["timer"]:
            SAVE["timer"].cancel()
        SAVE["state"] = "saving"
        t = threading.Timer(delay, _flush)
        t.daemon = True
        SAVE["timer"] = t
        t.start()


def flush_now():
    with SAVE_LOCK:
        t = SAVE["timer"]
        if t:
            t.cancel()
    if t:
        _flush()


def set_vars(key: str, changes: dict) -> dict:
    with CFG_LOCK:
        env = dict(CFG.get(key) or {})
        for name, value in changes.items():
            if not re.fullmatch(NAME_RE, str(name)):
                return {"error": f"Bad name: {name}. Use CAPITALS, digits and _."}
            if value is None or value == "":
                env.pop(name, None)
            else:
                env[name] = str(value)
        CFG[key] = env
    save_soon()
    return {"names": sorted(env)}


SKILLS_DIR = HOME / ".claude" / "skills"
MEMORY_FILE = HOME / ".claude" / "CLAUDE.md"
MEM = {"mtime": 0.0}


def apply_skills():
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    want = CFG.get("skills") or {}
    for d in SKILLS_DIR.iterdir():
        if (d / ".panda").exists() and d.name not in want:
            shutil.rmtree(d, ignore_errors=True)
    for name, text in want.items():
        d = SKILLS_DIR / name
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text(text)
        (d / ".panda").write_text("managed by the Panda app\n")


def apply_memory():
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(CFG.get("memory") or "")
    MEM["mtime"] = MEMORY_FILE.stat().st_mtime


def watch_memory():
    """Claude may edit ~/.claude/CLAUDE.md itself ("remember that…"): keep the saved copy in step."""
    try:
        m = MEMORY_FILE.stat().st_mtime
    except OSError:
        return
    if m != MEM["mtime"]:
        MEM["mtime"] = m
        text = MEMORY_FILE.read_text()
        if text != (CFG.get("memory") or ""):
            CFG["memory"] = text
            log("memory changed by Claude, saving")
            save_soon()


BROWSER_MCP = {"type": "stdio", "command": "npx",
               "args": ["-y", "@playwright/mcp@latest", "--cdp-endpoint", "http://127.0.0.1:9222"]}
MCP_APPLIED: set = set()


def apply_mcp():
    want = dict(CFG.get("mcp") or {})
    if settings()["browser"]:
        want["browser"] = BROWSER_MCP
    for name in list(MCP_APPLIED - set(want)):
        sh(f"claude mcp remove -s user {name}", timeout=60)
        MCP_APPLIED.discard(name)
    for name, conf in want.items():
        sh(f"claude mcp remove -s user {name} >/dev/null 2>&1; claude mcp add-json -s user {name} "
           f"'{json.dumps(conf).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'", timeout=60)
        MCP_APPLIED.add(name)


def mask_mcp(conf: dict) -> dict:
    c = json.loads(json.dumps(conf))
    for k in ("headers", "env"):
        if isinstance(c.get(k), dict):
            c[k] = {n: "••••" for n in c[k]}
    return c


def extra_system(req: dict) -> str:
    parts = []
    if settings()["memory"]:
        parts.append("Long-term memory: ~/.claude/CLAUDE.md is loaded in every chat. When the user asks you to "
                     "remember something, or tells you a lasting preference or fact about themselves, add it "
                     "there briefly. Keep it short and tidy.")
    if settings()["browser"]:
        parts.append("You have a real web browser through the `browser` MCP tools. The user can watch it and "
                     "take over in the app's Browser panel; if a page needs a login or a captcha, ask them to "
                     "do that step in the Browser panel, then continue.")
    if CFG.get("env"):
        parts.append("API keys available as environment variables: " + ", ".join(sorted(CFG["env"])) + ".")
    return "\n\n".join(parts)


# ── scheduled tasks and notifications ────────────────────────────────────────
NOTES_MAX = 60


def notify(note: dict):
    note = {"id": uuid_hex(), "at": int(time.time()), **note}
    with CFG_LOCK:
        CFG["notifications"] = ([note] + (CFG.get("notifications") or []))[:NOTES_MAX]
    save_soon()


def uuid_hex() -> str:
    return os.urandom(8).hex()


def schedule_due(s: dict, now: float) -> bool:
    if not s.get("enabled", True):
        return False
    last = float(s.get("last_run") or 0)
    if s.get("kind") == "interval":
        every = max(15, int(s.get("minutes") or 60)) * 60
        return now - max(last, float(s.get("created") or 0)) >= every
    # daily at HH:MM in the user's time zone (tz = minutes from JS getTimezoneOffset)
    local = time.gmtime(now - int(s.get("tz") or 0) * 60)
    hh, mm = (int(x) for x in str(s.get("time") or "09:00").split(":"))
    days = s.get("days") or [0, 1, 2, 3, 4, 5, 6]  # 0 = Monday
    if local.tm_wday not in days or (local.tm_hour, local.tm_min) < (hh, mm):
        return False
    last_local = time.gmtime(last - int(s.get("tz") or 0) * 60) if last else None
    return not last_local or (last_local.tm_year, last_local.tm_yday) != (local.tm_year, local.tm_yday)


def run_schedule(s: dict):
    out = {"text": "", "session_id": "", "error": False}

    def emit(ev):
        if ev.get("t") == "done":
            out.update(text=ev.get("text") or "", session_id=ev.get("session_id") or "", error=bool(ev.get("error")))
        elif ev.get("t") == "error":
            out.update(text=ev.get("message") or "failed", error=True)

    log("scheduled task:", s.get("name"))
    try:
        run_claude({"chat_id": "sched-" + s["id"], "message": s.get("prompt") or "", "model": s.get("model"),
                    "effort": s.get("effort") or "medium", "scheduled": True}, emit)
    except Exception as exc:
        out.update(text=scrub(exc), error=True)
    notify({"kind": "scheduled", "title": s.get("name") or "Scheduled task", "prompt": s.get("prompt") or "",
            "text": out["text"], "session_id": out["session_id"], "error": out["error"], "schedule": s["id"]})


def scheduler():
    while not STOP.wait(30):
        now = time.time()
        for s in list(CFG.get("schedules") or []):
            if schedule_due(s, now) and ("sched-" + s["id"]) not in RUNNING:
                s["last_run"] = now
                save_soon()
                threading.Thread(target=run_schedule, args=(s,), daemon=True).start()


def upsert_schedule(s: dict) -> dict:
    if not str(s.get("prompt") or "").strip():
        return {"error": "The task needs a prompt."}
    with CFG_LOCK:
        items = list(CFG.get("schedules") or [])
        s = {k: s.get(k) for k in ("id", "name", "prompt", "kind", "minutes", "time", "days", "tz", "model",
                                   "effort", "enabled") if k in s}
        old = next((x for x in items if x["id"] == s.get("id")), None)
        if old:
            old.update(s)
        else:
            s.update(id=s.get("id") or uuid_hex(), created=time.time(), last_run=0)
            items.append(s)
        CFG["schedules"] = items
    save_soon()
    return {"schedules": items}


# ── live browser (Chromium with DevTools; Claude drives it through Playwright MCP) ──
BROWSER = {"state": "off", "proc": None, "error": ""}
CDP = "http://127.0.0.1:9222"


def start_browser():
    if not settings()["browser"]:
        return
    BROWSER["state"] = "installing"
    try:
        sh("npx -y playwright@latest install --with-deps chromium", timeout=1200, check=True)
        exe = sorted(glob.glob(str(HOME / ".cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
        if not exe:
            raise RuntimeError("chromium not found after install")
        prof = WORK / "browser-profile"
        prof.mkdir(exist_ok=True)
        BROWSER["proc"] = subprocess.Popen(
            [exe[-1], "--headless=new", "--remote-debugging-port=9222", "--remote-debugging-address=127.0.0.1",
             "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", f"--user-data-dir={prof}",
             "--window-size=1280,800", "--no-first-run", "about:blank"],
            env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            if cdp_pages() is not None:
                break
            time.sleep(1)
        sh("npx -y @playwright/mcp@latest --help >/dev/null", timeout=600)  # warm the npx cache
        BROWSER["state"] = "ready"
        log("browser ready")
    except Exception as exc:
        BROWSER.update(state="error", error=scrub(exc)[:300])
        log("browser failed:", exc)


def cdp_pages():
    try:
        with urllib.request.urlopen(CDP + "/json", timeout=3) as r:
            return [p for p in json.load(r) if p.get("type") == "page"]
    except Exception:
        return None


def cdp(tab: str, calls: list, browser: bool = False) -> list:
    """Run DevTools commands on a tab (or on the browser itself); returns their results in order."""
    from websockets.sync.client import connect
    if browser:
        with urllib.request.urlopen(CDP + "/json/version", timeout=3) as r:
            ws_url = json.load(r)["webSocketDebuggerUrl"]
    else:
        pages = cdp_pages() or []
        page = next((p for p in pages if p["id"] == tab), pages[0] if pages else None)
        if not page:
            raise RuntimeError("no browser tab")
        ws_url = page["webSocketDebuggerUrl"]
    out = []
    with connect(ws_url, max_size=None, open_timeout=5) as ws:
        for i, (method, params) in enumerate(calls, 1):
            ws.send(json.dumps({"id": i, "method": method, "params": params}))
            while True:
                msg = json.loads(ws.recv(timeout=20))
                if msg.get("id") == i:
                    if "error" in msg:
                        raise RuntimeError(msg["error"].get("message", "devtools error"))
                    out.append(msg.get("result") or {})
                    break
    return out


KEYS = {"Enter": 13, "Backspace": 8, "Tab": 9, "Escape": 27, "ArrowDown": 40, "ArrowUp": 38,
        "ArrowLeft": 37, "ArrowRight": 39}


def browser_act(a: dict) -> dict:
    tab, kind = str(a.get("tab") or ""), a.get("type")
    if kind == "click":
        x, y = float(a["x"]), float(a["y"])
        cdp(tab, [("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})
                  for t in ("mouseMoved", "mousePressed", "mouseReleased")])
    elif kind == "type":
        cdp(tab, [("Input.insertText", {"text": str(a.get("text") or "")})])
    elif kind == "key":
        k = str(a.get("key"))
        code = KEYS.get(k, 0)
        extra = {"text": "\r"} if k == "Enter" else {}
        cdp(tab, [("Input.dispatchKeyEvent", {"type": "keyDown", "key": k, "code": k, "windowsVirtualKeyCode": code, **extra}),
                  ("Input.dispatchKeyEvent", {"type": "keyUp", "key": k, "code": k, "windowsVirtualKeyCode": code})])
    elif kind == "scroll":
        cdp(tab, [("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": float(a.get("x") or 400),
                                                "y": float(a.get("y") or 400), "deltaX": 0, "deltaY": float(a.get("dy") or 400)})])
    elif kind == "nav":
        url = str(a.get("url") or "").strip()
        if url and not re.match(r"^[a-z]+:", url):
            url = ("https://" + url) if "." in url and " " not in url else "https://www.google.com/search?q=" + urllib.parse.quote(url)
        cdp(tab, [("Page.navigate", {"url": url})])
    elif kind in ("back", "forward", "reload"):
        js = {"back": "history.back()", "forward": "history.forward()", "reload": "location.reload()"}[kind]
        cdp(tab, [("Runtime.evaluate", {"expression": js})])
    elif kind == "size":  # phone or desktop window; pages re-layout to it
        w, h = max(320, min(int(a.get("w") or 1280), 1920)), max(480, min(int(a.get("h") or 800), 1400))
        pages = cdp_pages() or []
        page = next((p for p in pages if p["id"] == tab), pages[0] if pages else None)
        if not page:
            return {"error": "no browser tab"}
        win = cdp("", [("Browser.getWindowForTarget", {"targetId": page["id"]})], browser=True)[0]["windowId"]
        cdp("", [("Browser.setWindowBounds", {"windowId": win, "bounds": {"windowState": "normal"}}),
                 ("Browser.setWindowBounds", {"windowId": win, "bounds": {"width": w, "height": h}})], browser=True)
    elif kind == "new":
        urllib.request.urlopen(urllib.request.Request(CDP + "/json/new?about:blank", method="PUT"), timeout=5).read()
    elif kind == "close":
        urllib.request.urlopen(CDP + "/json/close/" + urllib.parse.quote(tab), timeout=5).read()
    else:
        return {"error": "unknown action"}
    return {"ok": True}


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
        self.send_header("Access-Control-Allow-Headers", "authorization, content-type, x-file-name, x-folder, x-dev")
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
        try:
            return self._get()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            return self.send_json({"error": scrub(exc)}, 400)

    def _get(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self.send_json({"ok": True, "uptime": int(time.time() - T0)})
        if not self.authed():
            return self.send_json({"error": "unauthorized"}, 401)
        if u.path == "/chat/events":
            q = parse_qs(u.query)
            with RUN_LOCK:
                turn = TURNS.get((q.get("chat_id") or [""])[0])
            if turn and (q.get("turn") or [""])[0] not in ("", turn["id"]):
                turn = None  # a newer turn replaced the one the app is waiting for
            return self.follow(turn, int((q.get("from") or ["0"])[0] or 0))
        if u.path == "/status":
            return self.send_json({"ok": True, "uptime": int(time.time() - T0), "max": MAX_RUNTIME,
                                   "busy": list(RUNNING), "claude": bool(CFG.get("claude_oauth_token"))})
        q = parse_qs(u.query)
        if u.path == "/devenv":
            return self.send_json({"names": sorted(CFG.get("dev_env") or {})})
        if u.path == "/state":
            return self.send_json({
                "skills": CFG.get("skills") or {}, "mcp": {k: mask_mcp(v) for k, v in (CFG.get("mcp") or {}).items()},
                "env": sorted(CFG.get("env") or {}), "dev_env": sorted(CFG.get("dev_env") or {}),
                "memory": CFG.get("memory") or "", "schedules": CFG.get("schedules") or [],
                "settings": settings(), "save": SAVE["state"], "browser": BROWSER["state"],
                "browser_error": BROWSER["error"], "uptime": int(time.time() - T0), "max": MAX_RUNTIME,
                "claude": bool(CFG.get("claude_oauth_token"))})
        if u.path == "/commands":
            return self.send_json(slash_commands(q.get("dev", [""])[0] == "1"))
        if u.path == "/usage":
            return self.send_json(plan_usage(force=q.get("fresh", [""])[0] == "1"))
        if u.path == "/notifications":
            return self.send_json({"notifications": CFG.get("notifications") or []})
        if u.path == "/mcp/status":
            r = subprocess.run(["claude", "mcp", "list"], env=ENV, capture_output=True, text=True, timeout=90)
            return self.send_json({"text": scrub(r.stdout or r.stderr)[-4000:]})
        if u.path == "/browser/status":
            pages = cdp_pages()
            return self.send_json({"state": BROWSER["state"] if pages is not None or BROWSER["state"] != "ready" else "error",
                                   "error": BROWSER["error"],
                                   "tabs": [{"id": p["id"], "title": p.get("title", ""), "url": p.get("url", "")} for p in pages or []]})
        if u.path == "/browser/shot":
            tab = q.get("tab", [""])[0]
            vp = cdp(tab, [("Page.getLayoutMetrics", {})])[0].get("cssVisualViewport") or {}
            shot = {"format": "jpeg", "quality": int(q.get("q", ["60"])[0])}
            if 0 < vp.get("clientWidth", 0) < 700:  # phone-sized window: capture at 2x so text is sharp
                shot["clip"] = {"x": vp.get("pageX", 0), "y": vp.get("pageY", 0), "width": vp["clientWidth"],
                                "height": vp["clientHeight"], "scale": 2}
            import base64
            img = base64.b64decode(cdp(tab, [("Page.captureScreenshot", shot)])[0]["data"])
            self.send_response(200)
            self.cors()
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-View", f'{int(vp.get("clientWidth", 0))}x{int(vp.get("clientHeight", 0))}')
            self.send_header("Access-Control-Expose-Headers", "X-View")
            self.send_header("Content-Length", str(len(img)))
            self.end_headers()
            self.wfile.write(img)
            return
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
            if u.path in ("/devenv", "/env"):
                key = "dev_env" if u.path == "/devenv" else "env"
                return self.send_json(set_vars(key, json.loads(self.body() or b"{}").get("set") or {}))
            if u.path == "/delete":
                return self.delete_file(str(json.loads(self.body() or b"{}").get("path") or ""))
            if u.path in ("/skills", "/mcp", "/memory", "/settings", "/schedules", "/schedules/run",
                          "/notifications/clear", "/browser/act"):
                return self.send_json(self.change(u.path, json.loads(self.body(4 << 20) or b"{}")))
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

    def change(self, path: str, b: dict) -> dict:
        if path == "/browser/act":
            return browser_act(b)
        if path == "/schedules/run":
            s = next((x for x in CFG.get("schedules") or [] if x["id"] == b.get("id")), None)
            if not s:
                return {"error": "no such task"}
            threading.Thread(target=run_schedule, args=(s,), daemon=True).start()
            return {"ok": True}
        if path == "/schedules":
            if b.get("delete"):
                with CFG_LOCK:
                    CFG["schedules"] = [x for x in CFG.get("schedules") or [] if x["id"] != b["delete"]]
                save_soon()
                return {"schedules": CFG["schedules"]}
            return upsert_schedule(b.get("schedule") or {})
        if path == "/notifications/clear":
            with CFG_LOCK:
                CFG["notifications"] = [n for n in CFG.get("notifications") or [] if b.get("id") and n["id"] != b["id"]]
            save_soon()
            return {"notifications": CFG["notifications"]}
        if path == "/skills":
            name = str(b.get("name") or "")
            if not re.fullmatch(SKILL_RE, name):
                return {"error": "Skill names use lowercase letters, digits and dashes."}
            with CFG_LOCK:
                skills = dict(CFG.get("skills") or {})
                if b.get("content"):
                    skills[name] = str(b["content"])
                else:
                    skills.pop(name, None)
                CFG["skills"] = skills
            apply_skills()
        elif path == "/mcp":
            name = str(b.get("name") or "")
            if not re.fullmatch(SKILL_RE, name) or name == "browser":
                return {"error": "Connector names use lowercase letters, digits and dashes (not 'browser')."}
            conf = b.get("config")
            with CFG_LOCK:
                mcp = dict(CFG.get("mcp") or {})
                if conf:
                    old = mcp.get(name) or {}
                    for k in ("headers", "env"):  # keep secret values the app only saw masked
                        if isinstance(conf.get(k), dict):
                            conf[k] = {n: (old.get(k, {}).get(n, "") if v == "••••" else v) for n, v in conf[k].items()}
                    mcp[name] = conf
                else:
                    mcp.pop(name, None)
                CFG["mcp"] = mcp
            apply_mcp()
        elif path == "/memory":
            CFG["memory"] = str(b.get("text") or "")
            apply_memory()
        elif path == "/settings":
            before = settings()
            CFG["settings"] = {**(CFG.get("settings") or {}), **{k: bool(v) for k, v in (b.get("patch") or {}).items()
                                                                 if k in ("browser", "memory")}}
            if settings()["browser"] != before["browser"]:
                apply_mcp()
                if settings()["browser"] and BROWSER["state"] in ("off", "error"):
                    threading.Thread(target=start_browser, daemon=True).start()
        save_soon()
        return {"ok": True}

    def delete_file(self, rel: str):
        target = (WORKSPACE / rel).resolve()
        if WORKSPACE.resolve() not in target.parents or not target.is_file():
            return self.send_json({"error": "not found"}, 404)
        target.unlink()
        return self.send_json({"ok": True})

    def chat(self, req: dict):
        """Start a turn in the background and stream its events. The turn does not depend on this
        connection: if the phone drops it, /chat/events picks the stream up again."""
        chat = str(req.get("chat_id") or "chat")
        turn = {"id": uuid.uuid4().hex, "events": [], "done": False, "cond": threading.Condition(), "at": time.time()}
        with RUN_LOCK:
            TURNS[chat] = turn
            for k in [k for k, t in TURNS.items() if t["done"] and time.time() - t["at"] > 3600]:
                TURNS.pop(k, None)

        def emit(ev: dict):
            with turn["cond"]:
                turn["events"].append(ev)
                turn["cond"].notify_all()

        def work():
            try:
                run_claude(req, emit)
            except Exception as exc:
                emit({"t": "error", "message": scrub(exc)})
            finally:
                with turn["cond"]:
                    turn["done"] = True
                    turn["at"] = time.time()
                    turn["cond"].notify_all()

        threading.Thread(target=work, daemon=True).start()
        self.follow(turn, 0)

    def follow(self, turn: dict | None, start: int):
        """Stream a turn's events from index `start` until it ends. Every frame carries its index."""
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            if turn is None:
                self.wfile.write(b'data: {"t": "gone"}\n\n')
                self.wfile.flush()
                return
            self.wfile.write(f'data: {json.dumps({"t": "turn", "id": turn["id"]})}\n\n'.encode())
            self.wfile.flush()
            i = max(0, start)
            while True:
                with turn["cond"]:
                    if i >= len(turn["events"]) and not turn["done"]:
                        turn["cond"].wait(15)
                    batch = turn["events"][i:]
                    done = turn["done"]
                out = b"".join(f"data: {json.dumps(dict(ev, i=i + n))}\n\n".encode() for n, ev in enumerate(batch))
                i += len(batch)
                self.wfile.write(out or b": ping\n\n")
                self.wfile.flush()
                if done and i >= len(turn["events"]):
                    return
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # the phone went away; the turn keeps running

    def upload(self):
        name = os.path.basename(self.headers.get("X-File-Name") or "upload.bin") or "upload.bin"
        data = self.body(MAX_UPLOAD)
        base = folder_for({"folder": self.headers.get("X-Folder") or "", "dev": self.headers.get("X-Dev") == "1"})
        dest = base / "uploads"
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / name
        if target.exists():
            target = dest / f"{int(time.time())}-{name}"
        target.write_bytes(data)
        self.send_json({"path": str(target.relative_to(base)), "size": len(data)})

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
    apply_skills()
    apply_memory()
    apply_mcp()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"api listening on 127.0.0.1:{PORT}")
    tunnel, url = start_tunnel()
    if url:
        log("PANDA_URL=" + url)
        threading.Thread(target=publish_endpoint, args=(url,), daemon=True).start()
    log("ready")
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=start_browser, daemon=True).start()
    while not STOP.is_set():
        watch_memory()
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
    watch_memory()
    flush_now()
    if BROWSER["proc"]:
        BROWSER["proc"].kill()
    tunnel.terminate()
    srv.shutdown()
    log("stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("fatal:", type(exc).__name__, exc)
        sys.exit(1)
