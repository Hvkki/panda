#!/usr/bin/env python3
"""Make a new picture with Codex, signed in with the user's ChatGPT plan, and save it as a file.

  make_image.py "<what to draw>" ~/outputs/<name>.png   one picture (about 45 s)
  make_image.py --status                                 signed in? how much of the plan's limits is used
  make_image.py --login                                  one-time sign-in: prints a link and a code

Prints where the picture was saved, how long it took, and the plan's limits (% used).
Exit codes: 0 ok, 1 failed, 3 Codex is not signed in, 4 the plan's limit is reached.
The worker saves the Codex login (encrypted) and installs Codex again on every start.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

CODEX_VERSION = "0.164.0"  # pinned; worker.py installs the same version when a run starts
HOME = os.path.expanduser("~")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
GEN_DIR = os.path.join(CODEX_HOME, "generated_images")
SESS_DIR = os.path.join(CODEX_HOME, "sessions")
SCRATCH = os.path.join(HOME, "work", "codex-img")
LOGIN_LOG = os.path.join(HOME, "work", "codex-login.log")
INSTALLING = os.path.join(HOME, ".panda", "codex-installing")  # set by the worker while it installs
TIMEOUT = 300

PROMPT = (
    "Create exactly one image with your built-in image generation tool, then reply with the single word: done.\n"
    "Call the image tool right away. Do not read skill files, run shell commands, or copy, move or save "
    "files: the image is saved automatically.\n\n"
    "Image: {prompt}"
)

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
LIMIT_WORDS = re.compile(r"usage limit|rate limit|limit reached|too many requests|hit your", re.I)


def say(*a):
    print(*a, flush=True)


def ensure_codex() -> str:
    """Path of the codex binary, installing the pinned version when it is missing."""
    for _ in range(90):  # the worker may be installing it right now (first ~30 s of a run)
        if shutil.which("codex") or not os.path.exists(INSTALLING):
            break
        time.sleep(2)
    exe = shutil.which("codex")
    if exe:
        return exe
    npm = shutil.which("npm")
    if not npm:
        say("Codex is not installed and npm is missing, so it cannot be installed here.")
        sys.exit(1)
    say(f"Installing Codex {CODEX_VERSION} (once per run, about 20 s)...")
    r = subprocess.run([npm, "install", "-g", f"@openai/codex@{CODEX_VERSION}"],
                       capture_output=True, text=True, timeout=600)
    exe = shutil.which("codex")
    if r.returncode != 0 or not exe:
        say("Codex install failed:", (r.stderr or r.stdout)[-400:])
        sys.exit(1)
    return exe


def logged_in(exe: str) -> bool:
    r = subprocess.run([exe, "login", "status"], capture_output=True, text=True, timeout=60)
    out = (r.stdout + r.stderr).lower()
    return r.returncode == 0 and "logged in" in out and "not logged in" not in out


def _pngs() -> set:
    return set(glob.glob(os.path.join(GEN_DIR, "**", "*.png"), recursive=True))


def _saved_paths(obj, found: list):
    """Every savedPath / saved_path value in one JSON event (Codex's image result names its file)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("savedPath", "saved_path") and isinstance(v, str):
                found.append(v)
            else:
                _saved_paths(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _saved_paths(v, found)


def _messages(obj, found: list):
    """Error texts in one JSON event (error / turn.failed events)."""
    if isinstance(obj, dict):
        if str(obj.get("type", "")) in ("error", "turn.failed", "stream_error"):
            err = obj.get("error")
            m = obj.get("message") or (err.get("message") if isinstance(err, dict) else err)
            if isinstance(m, str) and m:
                found.append(m)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _messages(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _messages(v, found)


def limits(thread: str = "") -> tuple[dict | None, float]:
    """The plan's limits as Codex last reported them: (rate_limits, when) from a session log."""
    pat = f"rollout-*{thread}.jsonl" if thread else "rollout-*.jsonl"
    files = glob.glob(os.path.join(SESS_DIR, "**", pat), recursive=True)
    if not files:
        return None, 0.0
    path = max(files, key=os.path.getmtime)
    found = None
    with open(path, errors="replace") as f:
        for line in f:
            if '"rate_limits"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            rl = (d.get("payload") or {}).get("rate_limits") if isinstance(d, dict) else None
            if isinstance(rl, dict) and rl.get("primary"):
                found = rl
    return found, os.path.getmtime(path)


def _window(w: dict) -> str:
    mins = int(w.get("window_minutes") or 0)
    name = "weekly" if mins >= 7 * 24 * 60 else f"{round(mins / 60)}-hour" if mins else "plan"
    s = f"{name} limit {w.get('used_percent', 0):g}% used"
    if w.get("resets_at"):
        at = datetime.fromtimestamp(int(w["resets_at"]), timezone.utc)
        s += f" (resets {at:%a %d %b %H:%M} UTC)"
    return s


def limits_line(rl: dict | None) -> str:
    if not rl:
        return "Plan limits: not reported yet."
    parts = [_window(rl[k]) for k in ("primary", "secondary") if isinstance(rl.get(k), dict)]
    return "ChatGPT plan: " + "; ".join(parts)


def _target(out: str) -> str:
    """Where to write: the asked path, never overwriting an existing file (name-2.png, name-3.png…)."""
    out = os.path.abspath(os.path.expanduser(out))
    if os.path.isdir(out):
        out = os.path.join(out, "picture.png")
    root, ext = os.path.splitext(out)
    if ext.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        root, ext = out, ".png"
    path, n = root + ext, 2
    while os.path.exists(path):
        path, n = f"{root}-{n}{ext}", n + 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _save(src: str, dest: str) -> str:
    ext = os.path.splitext(dest)[1].lower()
    if ext == ".png":
        shutil.copyfile(src, dest)
        return dest
    try:
        from PIL import Image
        img = Image.open(src)
        if ext in (".jpg", ".jpeg"):
            img = img.convert("RGB")
        img.save(dest, quality=92)
    except Exception:  # no Pillow: keep the PNG
        dest = os.path.splitext(dest)[0] + ".png"
        shutil.copyfile(src, dest)
    return dest


def _size(path: str) -> str:
    mb = os.path.getsize(path) / 2 ** 20
    try:
        from PIL import Image
        w, h = Image.open(path).size
        return f"{w}x{h}, {mb:.1f} MB"
    except Exception:
        return f"{mb:.1f} MB"


def generate(exe: str, prompt: str, out: str) -> int:
    os.makedirs(SCRATCH, exist_ok=True)
    before = _pngs()
    t0 = time.time()
    cmd = [exe, "exec", "--skip-git-repo-check", "--json", "--sandbox", "read-only",
           "-c", 'model_reasoning_effort="low"', "-C", SCRATCH, PROMPT.format(prompt=prompt.strip())]
    errors: list = []
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, cwd=SCRATCH)
        stdout, stderr = r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = ""
        errors.append(f"Codex did not finish within {TIMEOUT} s.")
    secs = time.time() - t0
    thread, saved = "", []
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("thread_id") and not thread:
            thread = str(ev["thread_id"])
        _saved_paths(ev, saved)
        _messages(ev, errors)
    made = [p for p in saved if os.path.isfile(p)] or sorted(_pngs() - before, key=os.path.getmtime)
    rl, _ = limits(thread)
    if not made:
        why = " ".join(dict.fromkeys(errors)) or ANSI.sub("", stderr).strip()[-400:] or "no picture came back"
        say(f"No picture was made after {secs:.0f} s: {why}")
        say(limits_line(rl))
        if LIMIT_WORDS.search(why):
            return 4
        if not logged_in(exe):
            say("Codex is not signed in. Run: make_image.py --login")
            return 3
        return 1
    dest = _save(made[-1], _target(out))
    say(f"Saved: {dest} ({_size(dest)})")
    say(f"Time: {secs:.0f} s")
    say(limits_line(rl))
    say(f"Show it with: ![<caption>]({dest})")
    return 0


def login(exe: str) -> int:
    if logged_in(exe):
        say("Codex is already signed in.")
        return 0
    os.makedirs(os.path.dirname(LOGIN_LOG), exist_ok=True)
    with open(LOGIN_LOG, "w") as log:  # keeps running after this script ends, until the user signs in
        subprocess.Popen([exe, "login", "--device-auth"], stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    text = ""
    for _ in range(60):
        time.sleep(0.5)
        text = ANSI.sub("", open(LOGIN_LOG, errors="replace").read())
        if re.search(r"https://\S+", text) and re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b", text):
            break
    say(text.strip()[-1500:] or "Codex printed nothing yet; check again in a few seconds: cat " + LOGIN_LOG)
    say("\nGive the user the link and the code (valid 15 minutes). After they sign in, "
        "run make_image.py --status to confirm. The login is kept across restarts.")
    return 0


def status(exe: str) -> int:
    ok = logged_in(exe)
    say("Codex: signed in with ChatGPT" if ok else "Codex: not signed in (run make_image.py --login)")
    rl, when = limits()
    if rl:
        age = datetime.fromtimestamp(when, timezone.utc)
        say(limits_line(rl) + f" (as of {age:%H:%M} UTC, the last time Codex ran)")
    else:
        say("Plan limits: unknown until the first picture is made.")
    return 0 if ok else 3


def main() -> int:
    ap = argparse.ArgumentParser(description="Make a picture with Codex (the user's ChatGPT plan).")
    ap.add_argument("prompt", nargs="?", help="what to draw")
    ap.add_argument("out", nargs="?", default="~/outputs/picture.png", help="where to save it (.png, .jpg or .webp)")
    ap.add_argument("--status", action="store_true", help="signed in? limits used")
    ap.add_argument("--login", action="store_true", help="start the one-time sign-in")
    a = ap.parse_args()
    exe = ensure_codex()
    if a.login:
        return login(exe)
    if a.status:
        return status(exe)
    if not a.prompt:
        ap.error("say what to draw")
    if not logged_in(exe):
        say("Codex is not signed in. Run: make_image.py --login")
        return 3
    return generate(exe, a.prompt, a.out)


if __name__ == "__main__":
    sys.exit(main())
