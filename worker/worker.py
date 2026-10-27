"""Kiro Crew worker for Kaggle: Supabase relay <-> Kiro Crew gateway.

Lifecycle of one run (started by the user, never by itself):
  1. install kiro-cli + Kiro Crew, restore the last snapshot from Supabase Storage
  2. start the gateway (unsandboxed: Kaggle blocks user namespaces)
  3. claim the user's worker_sessions row, heartbeat it
  4. answer pending messages: one Kiro Crew chat slot per Supabase chat, replies streamed
  5. mirror Crew notifications (cron results) into the notifications table
  6. snapshot every SNAPSHOT_EVERY seconds (Crew state, kiro sessions and the owner's work files:
     ~/dev ~/work ~/outputs ~/projects ~/uploads ~/.panda); on stop_requested: final snapshot,
     mark stopped, shut down. Pressing Stop never starts another run.
  7. hand-off: HANDOFF_LEAD before MAX_RUNTIME (auto_restart, on by default) it asks the `session`
     function for a successor run of the same account. The successor installs, then waits until this
     run has stopped taking messages, drained, snapshotted and marked itself stopped; only then it
     restores and serves. Two workers never answer the same account at once.

Config comes from worker.json in an attached private dataset. No secret is printed.
"""
from __future__ import annotations

import base64
import glob
import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time
import uuid
import contextlib
import traceback
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from urllib.parse import quote

import requests

# ── config ───────────────────────────────────────────────────────────────────
CFG_PATH = (glob.glob("/kaggle/input/**/worker.json", recursive=True) or [None])[0]
CFG = json.load(open(CFG_PATH)) if CFG_PATH else json.loads(os.environ["KIROCREW_WORKER_CFG"])
SB_URL = CFG["supabase_url"].rstrip("/")
SB_ANON = CFG["supabase_publishable_key"]
OWNER = CFG["owner_id"]  # replaced by whoever pressed Start (claim_session)
PRIMARY_OWNER = CFG["owner_id"]  # the account worker.json was made for (its Drive, the legacy state paths)
# The session function writes the worker_sessions id of the Start press here when it pushes this
# file to the account's own Kaggle notebook, so each run serves exactly the person who started it.
INJECTED_SESSION_ID = ""


def state_key(name: str) -> str:
    """Storage path of this account's state (snapshots, browser logins).

    The primary account keeps the original paths; every other account has its own folder, so two
    workers running at the same time never overwrite each other.
    """
    return name if OWNER == PRIMARY_OWNER else f"users/{OWNER}/{name}"
SECRETS = [CFG["kiro_api_key"], CFG["worker_password"], CFG.get("vapid_private", ""),
           (CFG.get("drive") or {}).get("refresh_token", ""), (CFG.get("drive") or {}).get("client_secret", "")]
FILES_BUCKET = "panda-files"
OUTPUTS = os.path.expanduser("~/outputs")          # deliverables -> app file cards
WORKDIR = os.path.expanduser("~/work")             # working files (images for a deck, scratch) -> not published
MAX_PARALLEL_CHATS = 3

WORK = "/kaggle/working" if os.path.isdir("/kaggle/working") else os.path.abspath("work")
os.makedirs(WORK, exist_ok=True)
HOME = os.path.expanduser("~")
BIN = f"{HOME}/.local/bin"
PORT = 5476
GW = f"http://127.0.0.1:{PORT}"
BUCKET = "kirocrew-state"
POLL_SECS = 2
HEARTBEAT_SECS = 15
NOTIFY_SECS = 20
SNAPSHOT_EVERY = int(CFG.get("snapshot_every_secs", 1800))
MAX_RUNTIME = int(CFG.get("max_runtime_secs", 11 * 3600))  # stop cleanly before Kaggle's 12 h cap


def _flag(value, default: bool) -> bool:
    """A setting that may arrive as a bool, 0/1 or 'false'/'off'/'no'; None means 'use the default'."""
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "off", "no")
    return bool(value)


# Hand-off to a fresh Kaggle run before the time guard. On by default. Turn it off with
# "auto_restart": false in worker.json (the Kaggle dataset) or PANDA_AUTO_RESTART=0 in the environment;
# the environment wins.
AUTO_RESTART = _flag(os.environ.get("PANDA_AUTO_RESTART"), _flag(CFG.get("auto_restart"), True))
HANDOFF_LEAD = max(300, min(int(CFG.get("handoff_lead_secs", 25 * 60)), 40 * 60))
HANDOFF_LEAD = min(HANDOFF_LEAD, MAX_RUNTIME // 2)       # a short test run still hands off after half its time
HANDOFF_MODE = "sequential" if CFG.get("handoff_mode") == "sequential" else "overlap"
AGENT = CFG.get("agent", "")

ENV = dict(os.environ, KIRO_API_KEY=CFG["kiro_api_key"], KIROCREW_ALLOW_UNSANDBOXED="1",
           PATH=f"{BIN}:{os.environ.get('PATH', '')}")
T0 = time.time()


def scrub(s) -> str:
    s = "" if s is None else str(s)
    for sec in SECRETS:
        if sec:
            s = s.replace(sec, "***")
    return s


def log(*a):
    print(time.strftime("%H:%M:%S"), scrub(" ".join(map(str, a))), flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sh(cmd: str, timeout: int = 900, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, shell=True, env=ENV, capture_output=True, text=True, timeout=timeout)
    log(f"$ {cmd.split()[0]} {cmd.split()[1] if len(cmd.split()) > 1 else ''} -> rc={r.returncode}")
    if r.returncode != 0:
        log("  stderr:", (r.stderr or r.stdout)[-600:])
        if check:
            raise RuntimeError(f"command failed: {cmd.split()[0]}")
    return r


# ── Supabase (as the restricted worker user, never the secret key) ──────────
class Supabase:
    def __init__(self):
        self.token = ""
        self.refresh = ""
        self.expires = 0.0
        self.lock = threading.Lock()
        self._login()

    def _auth(self, grant: str, body: dict):
        r = requests.post(f"{SB_URL}/auth/v1/token?grant_type={grant}", json=body,
                          headers={"apikey": SB_ANON}, timeout=30)
        r.raise_for_status()
        d = r.json()
        self.token, self.refresh = d["access_token"], d["refresh_token"]
        self.expires = time.time() + int(d.get("expires_in", 3600)) - 120

    def _login(self):
        self._auth("password", {"email": CFG["worker_email"], "password": CFG["worker_password"]})
        log("supabase: worker signed in")

    def headers(self, extra: dict | None = None) -> dict:
        with self.lock:
            if time.time() > self.expires:
                try:
                    self._auth("refresh_token", {"refresh_token": self.refresh})
                except Exception:
                    self._login()
            h = {"apikey": SB_ANON, "Authorization": f"Bearer {self.token}",
                 "Content-Type": "application/json"}
        h.update(extra or {})
        return h

    def rest(self, method: str, path: str, *, params=None, body=None, prefer: str = ""):
        h = self.headers({"Prefer": prefer} if prefer else None)
        r = requests.request(method, f"{SB_URL}/rest/v1/{path}", params=params, json=body,
                             headers=h, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"supabase {method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.text else None

    def upload(self, name: str, path: str, bucket: str = BUCKET, mime: str = "application/gzip"):
        with open(path, "rb") as f:
            r = requests.post(f"{SB_URL}/storage/v1/object/{bucket}/{quote(name)}", data=f, timeout=600,
                              headers=self.headers({"x-upsert": "true", "Content-Type": mime}))
        if r.status_code >= 400:
            raise RuntimeError(f"upload {name}: {r.status_code} {r.text[:200]}")

    def delete_object(self, name: str, bucket: str = BUCKET):
        requests.delete(f"{SB_URL}/storage/v1/object/{bucket}/{quote(name)}", headers=self.headers(), timeout=60)

    def download(self, name: str, path: str, bucket: str = BUCKET) -> bool:
        r = requests.get(f"{SB_URL}/storage/v1/object/authenticated/{bucket}/{quote(name)}",
                         headers=self.headers(), timeout=600, stream=True)
        if r.status_code != 200:
            return False
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):  # decodes gzip/br; r.raw would not
                f.write(chunk)
        return True


# ── Kiro Crew lifecycle ──────────────────────────────────────────────────────
CREW_VERSION = {"v": ""}


def install():
    sh("curl -fsSL https://cli.kiro.dev/install | bash", check=True)
    sh("curl -fsSL https://download.crew.kiro.dev/cli.sh | sh", timeout=1500, check=True)
    r = sh("kiro-cli --version; kirocrew --version")
    m = re.search(r"kirocrew\s+v?([0-9][\w.\-]*)", r.stdout or "")
    CREW_VERSION["v"] = m.group(1) if m else ""
    # Lean agent: drop desktop-only tool servers (computer use, dashboard guide, crew mate).
    # Their definitions were sent with EVERY message and cost tokens for nothing here.
    os.makedirs(f"{HOME}/.kiro/crew", exist_ok=True)
    with open(f"{HOME}/.kiro/crew/agent.json", "w") as f:
        json.dump({"tools": ["execute_bash", "fs_read", "fs_write", "code", "grep", "glob", "web_fetch", "web_search",
                             "tool_search", "session", "@kirocrew-cron", "@kirocrew-core"]}, f)
    sh("kirocrew setup --agent-only")
    sh("kirocrew config set agent.sandbox_allow_unsandboxed_exec true")
    # Tool Search: by default Crew only hides tool specs once they pass 5% of the window / 50k tokens, so
    # all ~80 Crew tools (~40k+ tokens) were sent with every new chat. 0/0 = always load them on demand.
    sh("kirocrew config set agent.tool_search true")
    sh("kirocrew config set agent.tool_search_min_pct 0")
    sh("kirocrew config set agent.tool_search_min_tokens 0")
    # Cheap model for background work: auto-titles, memory consolidation, heartbeats
    # all run Kiro Crew's "background" role. Haiku 5.5 is fast and cheap and writes better than GLM-5.
    sh("kirocrew config set agent.role_models '{\"background\": \"claude-haiku-5.5\"}'")
    # Libraries the Panda skills use (PDF/DOCX/PPTX, image work, phone push).
    sh(f"{sys.executable} -m pip install -q reportlab python-docx python-pptx pillow pywebpush websocket-client", timeout=600)


def install_skills(sb: "Supabase"):
    """Panda's own skills (docs, image search) live in Storage as one archive."""
    arc = f"{WORK}/skills.tar.gz"
    if sb.download("code/skills.tar.gz", arc):
        dest = f"{HOME}/.kiro/crew/skills"
        os.makedirs(dest, exist_ok=True)
        with tarfile.open(arc) as t:
            t.extractall(dest, filter="data")
        log("skills installed:", ", ".join(sorted(os.listdir(dest)))[:300])
    install_skill_commands()


# Skill helpers as real commands. The skills used to tell the agent to define a shell function
# (`pb() { python3 …; }`), and Kiro Crew refuses to auto-approve a command that redefines a program
# name: the step then waited for an approval nobody saw, and the chat seemed to stop answering.
SKILL_COMMANDS = {"pb": "panda-browser/pb.py", "drive": "panda-drive/drive.py", "pvideo": "panda-video/video.py"}


def install_skill_commands():
    bindir = f"{HOME}/.local/bin"
    os.makedirs(bindir, exist_ok=True)
    for name, rel in SKILL_COMMANDS.items():
        dst = f"{bindir}/{name}"
        with open(dst, "w") as f:
            f.write(f'#!/bin/sh\nexec python3 "$HOME/.kiro/crew/skills/{rel}" "$@"\n')
        os.chmod(dst, 0o755)


PANDA_SECRETS = os.path.expanduser("~/.panda/secrets.env")
SECRET_NAMES: list = []


DEV_PREFIX = "DEV__"          # vault names of the Developer tab's env variables (stored without it in dev.env)
DEV_ENV = os.path.expanduser("~/.panda/dev.env")
DEV_ROWS: list = []           # [{name, value}] with the prefix removed
DEV_LOCK = threading.Lock()
DEV_TURNS = {"n": 0}          # developer turns running now; dev.env exists only while > 0
DEV_LATEST: dict = {}         # config/dev-latest.json: where the newest Panda work is (scripts/mark-latest.py)


def refresh_dev_latest(sb: "Supabase") -> None:
    """Read which branch/commit holds the newest Panda work, written by scripts/mark-latest.py."""
    path = f"{HOME}/.panda/dev-latest.json"
    try:
        if sb.download("config/dev-latest.json", path):
            data = json.load(open(path))
            if isinstance(data, dict):
                DEV_LATEST.clear()
                DEV_LATEST.update(data)
    except Exception as exc:
        log("dev-latest read failed:", type(exc).__name__)


def _latest_line() -> str:
    def when(e) -> str:
        return f"{str(e.get('at', ''))[:16].replace('T', ' ')} UTC"

    def one(e) -> str:
        return f"{e.get('branch')} @ {e.get('commit')} ({when(e)})"
    last = DEV_LATEST.get("latest")
    if not isinstance(last, dict) or not last.get("branch"):
        return ("- Newest work: not recorded yet. After you push or deploy, run "
                "`python3 scripts/mark-latest.py push|app|worker` so the next Developer agent knows.\n")
    s = (f"- Newest work: branch `{last['branch']}`, commit {last.get('commit')} — \"{last.get('subject', '')}\" "
         f"({last.get('part')}, {when(last)}).\n")
    live = [f"{k} live from {one(DEV_LATEST[k])}" for k in ("app", "worker") if isinstance(DEV_LATEST.get(k), dict)]
    if live:
        s += "  " + "; ".join(live) + ".\n"
    return s + ("  Start from that branch ($PANDA_DEV_BRANCH) unless the user names another. After every push run "
                "`python3 scripts/mark-latest.py push` (the deploy scripts record app/worker themselves).\n")


def sync_secrets(sb: "Supabase") -> list:
    """API keys from the app's vault -> env files the agent and MCP servers read.

    Developer variables (vault names starting with DEV__) are kept apart: they never go into
    secrets.env or Crew's .env, and are written to ~/.panda/dev.env only while a turn of the
    Developer chat runs.
    """
    rows = sb.rest("GET", "secrets", params={"owner": f"eq.{OWNER}", "select": "name,value"}) or []
    for r in rows:
        if r["value"] not in SECRETS:
            SECRETS.append(r["value"])  # never let a key appear in logs or replies
    with DEV_LOCK:
        DEV_ROWS[:] = [{"name": r["name"][len(DEV_PREFIX):], "value": r["value"]} for r in rows
                       if r["name"].startswith(DEV_PREFIX) and len(r["name"]) > len(DEV_PREFIX)]
        if DEV_TURNS["n"] > 0:
            _write_dev_env()
    rows = [r for r in rows if not r["name"].startswith(DEV_PREFIX)]
    os.makedirs(os.path.dirname(PANDA_SECRETS), exist_ok=True)
    lines = [f"export {r['name']}={json.dumps(r['value'])}" for r in rows]
    with open(os.open(PANDA_SECRETS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write("\n".join(lines) + "\n")
    # Crew's .env: MCP connectors use ${env:NAME} in headers/env.
    env_path = f"{HOME}/.kiro/crew/.env"
    keep = []
    if os.path.exists(env_path):
        skip = False
        for ln in open(env_path).read().splitlines():
            if ln.strip() == "# >>> panda vault":
                skip = True
            elif ln.strip() == "# <<< panda vault":
                skip = False
            elif not skip:
                keep.append(ln)
    block = ["# >>> panda vault"] + [f"{r['name']}={r['value']}" for r in rows] + ["# <<< panda vault"]
    with open(os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write("\n".join(keep + block) + "\n")
    SECRET_NAMES[:] = [r["name"] for r in rows]
    log(f"vault: {len(rows)} key(s) available, {len(DEV_ROWS)} developer variable(s)")
    return SECRET_NAMES


def _write_dev_env():
    os.makedirs(os.path.dirname(DEV_ENV), exist_ok=True)
    lines = [f"export {r['name']}={shlex.quote(r['value'])}" for r in DEV_ROWS]
    last = DEV_LATEST.get("latest") if isinstance(DEV_LATEST.get("latest"), dict) else {}
    if last.get("branch") and not any(r["name"] == "PANDA_DEV_BRANCH" for r in DEV_ROWS):
        lines.append(f"export PANDA_DEV_BRANCH={shlex.quote(str(last['branch']))}")
    with open(os.open(DEV_ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write("\n".join(lines) + "\n")


def dev_begin():
    with DEV_LOCK:
        DEV_TURNS["n"] += 1
        _write_dev_env()


def dev_end():
    with DEV_LOCK:
        DEV_TURNS["n"] = max(0, DEV_TURNS["n"] - 1)
        if DEV_TURNS["n"] == 0:
            with contextlib.suppress(OSError):
                os.remove(DEV_ENV)


def is_developer_chat(settings: dict, chat: dict) -> bool:
    return bool(settings.get("developerChatId")) and settings.get("developerChatId") == chat.get("id")


DEV_REPO = "https://github.com/Hvkki/panda"


DEV_MARK = ("[DEVELOPER CHAT] This message is from the Developer chat: the [DEVELOPER MODE] instructions "
            "given earlier in this conversation apply. No need to verify it.\n\n")


def dev_preamble() -> str:
    names = ", ".join(r["name"] for r in DEV_ROWS) or "(none yet — the user adds them in Developer → Env variables)"
    return f"""[DEVELOPER MODE]
This is the Developer chat. Here, and only here, the user may ask you to change Panda itself: its
web app (UI), the worker, its skills or its prompt. Elsewhere you never touch Panda's own code.

- Source: {DEV_REPO} (public). Work in ~/dev/panda: clone it the first time, otherwise `git fetch`.
  Use branch $PANDA_DEV_BRANCH if it is set, else the branch the user names, else the default branch.
  Read README.md, docs/architecture.md and docs/runbook.md before the first change.
{_latest_line()}- Developer env variables, for this chat only: {names}.
  Load them inside a bash command with `set -a; . ~/.panda/dev.env; set +a`. The file exists only
  while a Developer turn runs. Never print, echo, log, commit or paste their values.
- Web app (app/, Vite + React + TypeScript): `cd app && npm ci && npm run build` must pass before any
  deploy. Deploy to production with `python3 scripts/deploy-app.py` (needs VERCEL_TOKEN).
  If node/npm is missing, install Node 22 under ~/.local first.
- Worker, skills, prompt (worker/): `bash scripts/deploy-worker.sh` (needs SUPABASE_URL and
  SUPABASE_SECRET_KEY) uploads them; a running worker keeps its old code until the user presses Stop
  and Start. To try a skill change right away, also copy it into ~/.kiro/crew/skills/.
- Save every change in git: commit on a branch with a clear message and push it
  (`git push https://x-access-token:$GITHUB_TOKEN@github.com/Hvkki/panda.git HEAD:<branch>` needs GITHUB_TOKEN).
  If there is no GITHUB_TOKEN, say so and keep the commit locally.
- Do exactly what the user asked, no more. For a bigger change, say the plan in two or three lines
  first. Deploy only when the user asked for it (or clearly wants it live). Afterwards tell them what
  changed, where, and how to undo it (git revert, or redeploy the previous commit).
- Never weaken security: the owner-only checks, row-level security, the relay allow-list
  (GW_ALLOWED in worker/worker.py) and the browser's private-address block stay as they are unless
  the user explicitly asks and understands the risk.
[END DEVELOPER MODE]

"""


# State the Crew snapshot does not carry: Crew's chat transcripts (crew/sessions) and
# kiro-cli's own sessions, agents and steering. Lock files are never saved: a stale
# lock makes kiro-cli refuse to resume the session on the next run.
EXTRA_DIRS = ["crew/sessions", "sessions/cli", "agents", "steering"]


def _no_locks(info: tarfile.TarInfo):
    return None if info.name.endswith(".lock") else info


def tar_add_live(t: tarfile.TarFile, root: str, arcname: str):
    """Add a directory that other programs are still writing to. kiro-cli creates and deletes session
    files all the time, so a file listed a moment ago may be gone when it is read: skip it instead of
    failing the whole snapshot (that error used to crash the worker: '[Errno 2] No such file ...')."""
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        base = arcname if rel == "." else f"{arcname}/{rel}"
        for name in sorted(filenames):
            if name.endswith(".lock"):
                continue
            path = os.path.join(dirpath, name)
            try:
                t.add(path, arcname=f"{base}/{name}", recursive=False)
            except (FileNotFoundError, NotADirectoryError):
                continue


# ── Big state in Supabase Storage: split into parts ─────────────────────────
# A Free Supabase project refuses any object over 50 MB (the Pro plan allows far more:
# https://supabase.com/docs/guides/storage/uploads/file-limits), and the Crew snapshot alone is already
# ~49 MB. So every snapshot file is stored whole when it is under STATE_PART_BYTES, otherwise as
# <name>.<generation>.pNNN parts plus a small <name>.parts.json manifest that is written LAST: until it
# is replaced, a restore still reads the previous complete set.
MiB = 1 << 20
STATE_PART_BYTES = max(1, int(CFG.get("state_part_mb", 40))) * MiB


def sha256_file(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(MiB), b""):
            h.update(chunk)
    return h.hexdigest()


def split_file(path: str, part_bytes: int, out_prefix: str) -> list:
    """Cut `path` into <out_prefix>.p000, .p001 … of at most part_bytes each; returns their paths."""
    parts = []
    with open(path, "rb") as src:
        while True:
            p = f"{out_prefix}.p{len(parts):03d}"
            left, wrote = part_bytes, 0
            with open(p, "wb") as out:
                while left > 0:
                    chunk = src.read(min(MiB, left))
                    if not chunk:
                        break
                    out.write(chunk)
                    left -= len(chunk)
                    wrote += len(chunk)
            if wrote == 0 and parts:
                os.remove(p)
                break
            parts.append(p)
            if left > 0:  # short part: that was the end of the file
                break
    return parts


def join_parts(parts: list, dest: str) -> str:
    """Concatenate parts into dest; returns the sha256 of the result."""
    import hashlib
    h = hashlib.sha256()
    with open(dest, "wb") as out:
        for p in parts:
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(MiB), b""):
                    h.update(chunk)
                    out.write(chunk)
    return h.hexdigest()


def _manifest_name(name: str) -> str:
    return f"{name}.parts.json"


def _manifest_get(sb: "Supabase", name: str) -> dict | None:
    tmp = f"{WORK}/manifest-{uuid.uuid4().hex}.json"
    try:
        if sb.download(state_key(_manifest_name(name)), tmp):
            data = json.load(open(tmp))
            return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        pass
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)
    return None


def _upload_retry(sb: "Supabase", key: str, path: str, mime: str = "application/gzip", tries: int = 3):
    for i in range(tries):
        try:
            return sb.upload(key, path, mime=mime)
        except Exception as exc:
            if i == tries - 1:
                raise
            log(f"upload {key.rsplit('/', 1)[-1]} failed ({str(exc)[:80]}); retrying")
            time.sleep(3 * (i + 1))


def _forget_old(sb: "Supabase", name: str, old: dict | None, keep_single: bool, keep_manifest: bool):
    if old:
        for n in old.get("parts") or []:
            sb.delete_object(state_key(n))
        if old.get("drive"):
            drive_rm(old["drive"].get("id", ""))
    if not keep_single:
        sb.delete_object(state_key(name))
    if not keep_manifest:
        sb.delete_object(state_key(_manifest_name(name)))


def _put_manifest(sb: "Supabase", name: str, man: dict):
    mp = f"{WORK}/manifest-{uuid.uuid4().hex}.json"
    with open(mp, "w") as f:
        json.dump(man, f)
    try:
        _upload_retry(sb, state_key(_manifest_name(name)), mp, mime="application/json")
    finally:
        with contextlib.suppress(OSError):
            os.remove(mp)


def blob_save(sb: "Supabase", name: str, path: str, part_bytes: int | None = None,
              extra: dict | None = None) -> dict:
    """Store `path` as `name` (whole, or in parts when bigger than part_bytes). Returns what was done."""
    part_bytes = part_bytes or STATE_PART_BYTES
    size = os.path.getsize(path)
    old = _manifest_get(sb, name)
    if size <= part_bytes:
        _upload_retry(sb, state_key(name), path)
        _forget_old(sb, name, old, keep_single=True, keep_manifest=False)
        return {"size": size, "parts": 1}
    gen = f"{int(time.time())}{uuid.uuid4().hex[:4]}"  # never equal to an earlier generation
    local = split_file(path, part_bytes, f"{WORK}/blob-{gen}")
    names = []
    try:
        for i, p in enumerate(local):
            n = f"{name}.{gen}.p{i:03d}"
            _upload_retry(sb, state_key(n), p)
            names.append(n)
            os.remove(p)
    finally:
        for p in local:
            with contextlib.suppress(OSError):
                os.remove(p)
    _put_manifest(sb, name, {"v": 1, "size": size, "sha256": sha256_file(path), "parts": names,
                             "saved_at": now_iso(), **(extra or {})})
    _forget_old(sb, name, old, keep_single=False, keep_manifest=True)
    return {"size": size, "parts": len(names)}


def blob_save_drive(sb: "Supabase", name: str, path: str, extra: dict | None = None) -> dict | None:
    """For an archive too big for Storage: put it in Google Drive; Storage keeps only the manifest."""
    old = _manifest_get(sb, name)
    got = drive_put(path, "state")
    if not got:
        return None
    size = os.path.getsize(path)
    _put_manifest(sb, name, {"v": 1, "size": size, "sha256": sha256_file(path), "parts": [],
                             "drive": {"id": got["id"], "link": got.get("link", "")},
                             "saved_at": now_iso(), **(extra or {})})
    _forget_old(sb, name, old, keep_single=False, keep_manifest=True)
    return {"size": size, "parts": 0, "drive": got["id"]}


def blob_load(sb: "Supabase", name: str, dest: str) -> bool:
    """Fetch what blob_save / blob_save_drive stored (or an old single object). False if there is none."""
    man = _manifest_get(sb, name)
    if not man:
        return sb.download(state_key(name), dest)
    if man.get("drive"):
        if not drive_get(man["drive"].get("id", ""), dest):
            raise RuntimeError("the saved work files are in Google Drive but could not be fetched")
        sha = sha256_file(dest)
    else:
        local = []
        try:
            for i, n in enumerate(man.get("parts") or []):
                p = f"{WORK}/restore-part-{i:03d}"
                if not sb.download(state_key(n), p):
                    raise RuntimeError(f"saved part {n} is missing")
                local.append(p)
            sha = join_parts(local, dest)
        finally:
            for p in local:
                with contextlib.suppress(OSError):
                    os.remove(p)
    if man.get("sha256") and sha != man["sha256"]:
        with contextlib.suppress(OSError):
            os.remove(dest)
        raise RuntimeError(f"{name}: the parts do not add up (checksum differs)")
    return True


# ── The owner's work files: kept across runs ────────────────────────────────
# Kaggle starts every run on a clean disk. Besides Crew's state, these folders of the home directory are
# saved (snapshot) and put back before Kiro Crew starts (restore_files).
FILES_ENABLED = _flag(CFG.get("save_files"), True)


def _extra_roots(value) -> list:
    """Extra home folders from worker.json ("state_extra_roots"); never '..', never credential folders."""
    out = []
    for r in value if isinstance(value, list) else []:
        r = str(r).strip().strip("/")
        if r and ".." not in r.split("/") and r.split("/")[0] not in (".ssh", ".aws", ".gnupg", ".kiro", ".codex", ".config"):
            out.append(r)
    return out


FILES_ROOTS = ["dev", "work", "outputs", "projects", "uploads", ".panda"] + _extra_roots(CFG.get("state_extra_roots"))
FILE_MAX_BYTES = int(CFG.get("state_max_file_mb", 100)) * MiB       # a single file bigger than this is not saved
FILES_STORAGE_MAX = int(CFG.get("state_storage_max_mb", 200)) * MiB  # bigger archives go to Drive (or are trimmed)
FILES_BLOB = "latest-files.tar.gz"
ALWAYS_SKIP_DIRS = {"node_modules", "__pycache__", ".cache"}
CODE_ROOTS = {"dev", "projects"}                                      # code trees: build output is not saved
CODE_SKIP_DIRS = {"dist", "build", ".venv", "venv", ".next", ".pytest_cache", ".mypy_cache", ".tox"}
# ~/.panda also holds machine state the worker rebuilds itself, and secrets that must not sit in Storage
# unencrypted: the vault copy, the Drive login, Developer variables, browser cookies (saved encrypted
# elsewhere), the voice libraries (reinstalled), pids and logs.
PANDA_SKIP_DIRS = {"browser", "stt-lib"}
PANDA_SKIP_FILES = {"dev.env", "secrets.env", "drive.json", "browser.json", "dev-latest.json", "engine.pid",
                    "codex-installing", "browser-installing"}


class Entry(NamedTuple):
    arc: str        # path inside the archive, relative to the home directory
    path: str
    size: int
    mtime: int      # whole seconds: that is what a tar archive keeps
    kind: str       # "dir" | "file" | "link"


def _dir_skipped(root: str, depth: int, name: str) -> bool:
    if name in ALWAYS_SKIP_DIRS:
        return True
    if root in CODE_ROOTS and name in CODE_SKIP_DIRS:
        return True
    return root == ".panda" and depth == 1 and name in PANDA_SKIP_DIRS


def _file_skipped(root: str, depth: int, name: str) -> bool:
    if name.endswith(".pyc"):
        return True
    return root == ".panda" and depth == 1 and (name in PANDA_SKIP_FILES or name.endswith(".log"))


def select_files(home: str, roots: list, max_file: int | None = None, exclude: tuple = ()) -> tuple:
    """What to save. Returns (entries sorted by arc, skipped as [(arc, size, why)]).

    Only regular files, directories and symlinks; never anything under `exclude` (the worker's own
    scratch folder). A file over max_file is skipped and listed.
    """
    max_file = FILE_MAX_BYTES if max_file is None else max_file
    entries, skipped = [], []
    excl = tuple(os.path.abspath(e) for e in exclude)
    for root in roots:
        base = os.path.join(home, root)
        if not os.path.isdir(base) or os.path.islink(base):
            continue
        top = root.split("/")[0]
        for dirpath, dirnames, filenames in os.walk(base):
            rel = os.path.relpath(dirpath, base)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if any(dirpath == e or dirpath.startswith(e + os.sep) for e in excl):
                dirnames[:] = []
                continue
            try:
                entries.append(Entry(os.path.relpath(dirpath, home), dirpath, 0, int(os.lstat(dirpath).st_mtime), "dir"))
            except OSError:
                dirnames[:] = []
                continue
            keep = []
            for d in sorted(dirnames):
                full = os.path.join(dirpath, d)
                if _dir_skipped(top, depth + 1, d):
                    continue
                if os.path.islink(full):  # os.walk lists a link to a folder here but does not enter it
                    entries.append(Entry(os.path.relpath(full, home), full, 0, int(os.lstat(full).st_mtime), "link"))
                    continue
                keep.append(d)
            dirnames[:] = keep
            for n in sorted(filenames):
                full = os.path.join(dirpath, n)
                if _file_skipped(top, depth + 1, n):
                    continue
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                arc = os.path.relpath(full, home)
                if stat.S_ISLNK(st.st_mode):
                    entries.append(Entry(arc, full, 0, int(st.st_mtime), "link"))
                elif not stat.S_ISREG(st.st_mode):
                    continue  # sockets, pipes, devices
                elif st.st_size > max_file:
                    skipped.append((arc, st.st_size, f"bigger than {max_file // MiB} MB"))
                else:
                    entries.append(Entry(arc, full, st.st_size, int(st.st_mtime), "file"))
    entries.sort(key=lambda e: e.arc)
    return entries, skipped


def files_signature(entries: list) -> str:
    """Changes when any saved file is added, removed, resized or touched (folders do not count)."""
    import hashlib
    h = hashlib.sha1()
    for e in entries:
        if e.kind != "dir":
            h.update(f"{e.arc}|{e.kind}|{e.size}|{e.mtime}\n".encode())
    return h.hexdigest()


def trim_to_budget(entries: list, budget: int) -> tuple:
    """Drop the oldest files until the files add up to at most `budget` bytes. Returns (kept, dropped)."""
    total = sum(e.size for e in entries if e.kind == "file")
    if total <= budget:
        return entries, []
    drop = set()
    for e in sorted((e for e in entries if e.kind == "file"), key=lambda e: (e.mtime, e.arc)):
        if total <= budget:
            break
        drop.add(e.arc)
        total -= e.size
    return ([e for e in entries if e.arc not in drop],
            [(e.arc, e.size, "over the storage budget") for e in entries if e.arc in drop])


class _FixedSize:
    """A file read as exactly `size` bytes: a file that grows or shrinks while it is packed must not
    corrupt the archive (a missing tail is zeros; the file is saved again at the next snapshot)."""

    def __init__(self, f, size: int):
        self.f, self.left = f, size

    def read(self, n: int = -1) -> bytes:
        n = self.left if n is None or n < 0 else min(n, self.left)
        data = self.f.read(n)
        if len(data) < n:
            data += b"\0" * (n - len(data))
        self.left -= n
        return data


def write_archive(entries: list, dest: str) -> list:
    """tar.gz of the entries (paths as arc names). Returns the arcs that could not be read."""
    failed = []
    with tarfile.open(dest, "w:gz", compresslevel=6) as t:
        for e in entries:
            try:
                ti = t.gettarinfo(e.path, arcname=e.arc)
                if ti is None:
                    continue
                if ti.isreg():
                    with open(e.path, "rb") as f:
                        t.addfile(ti, _FixedSize(f, ti.size))
                else:
                    t.addfile(ti)
            except OSError:
                failed.append(e.arc)
    return failed


def extract_archive(archive: str, home: str, roots: list) -> tuple:
    """Unpack into `home`. Member by member: one bad member (an absolute link, an unwritable path) is
    skipped and counted, never fatal. Only members under `roots` are accepted. Returns (extracted, skipped)."""
    ok = bad = 0
    top = {r.split("/")[0] for r in roots}
    with tarfile.open(archive) as t:
        for m in t:
            first = m.name.split("/")[0]
            if first not in top or m.name.startswith("/") or ".." in m.name.split("/"):
                bad += 1
                continue
            try:
                t.extract(m, home, filter="data")
                ok += 1
            except (tarfile.TarError, OSError):
                bad += 1
    return ok, bad


_files_sync = {"sig": None}
_files_lock = threading.Lock()


def files_snapshot(sb: "Supabase", reason: str) -> dict | None:
    """Save the owner's work files. Skips the upload when nothing changed since the last save."""
    if not FILES_ENABLED:
        return None
    with _files_lock:
        entries, skipped = select_files(HOME, FILES_ROOTS, exclude=(WORK,))
        for arc, size, why in skipped:
            log(f"files: not saved, {why}: ~/{arc} ({size // MiB} MB)")
        sig = files_signature(entries)
        if sig == _files_sync["sig"]:
            return {"unchanged": True}
        archive = f"{WORK}/files.tar.gz"
        drive_ok = os.path.exists(DRIVE_CFG)
        raw = sum(e.size for e in entries if e.kind == "file")
        chosen, dropped, budget = entries, [], None
        try:
            for _ in range(4):
                if budget is not None:
                    chosen, dropped = trim_to_budget(entries, budget)
                failed = write_archive(chosen, archive)
                size = os.path.getsize(archive)
                if size <= FILES_STORAGE_MAX or drive_ok:
                    break
                # No Drive for this account: keep fewer files (oldest first) until the archive fits in Storage.
                budget = int(sum(e.size for e in chosen if e.kind == "file") * FILES_STORAGE_MAX / size * 0.9)
            for arc, sz, why in dropped:
                log(f"files: not saved, {why}: ~/{arc} ({sz // MiB} MB)")
            for arc in failed:
                log(f"files: could not read ~/{arc}")
            extra = {"sig": sig, "files": sum(1 for e in chosen if e.kind == "file")}
            done = where = None
            if size > FILES_STORAGE_MAX and drive_ok:
                done, where = blob_save_drive(sb, FILES_BLOB, archive, extra), "Google Drive"
                if done is None:  # Drive failed: a trimmed copy in Storage beats nothing
                    chosen, dropped = trim_to_budget(entries, int(raw * FILES_STORAGE_MAX / size * 0.9))
                    write_archive(chosen, archive)
                    extra["files"] = sum(1 for e in chosen if e.kind == "file")
            if done is None:
                done, where = blob_save(sb, FILES_BLOB, archive, extra=extra), "Storage"
            _files_sync["sig"] = None if dropped else sig  # a trimmed save is tried again next time
            info = {"files": extra["files"], "raw_bytes": raw, "archive_bytes": done["size"],
                    "parts": done["parts"], "where": where, "skipped": len(skipped) + len(dropped)}
            log(f"files saved ({reason}): {info['files']} files, {raw / MiB:.1f} MB -> {done['size'] / MiB:.1f} MB, "
                f"{info['parts']} part(s) in {where}, {info['skipped']} skipped")
            return info
        finally:
            with contextlib.suppress(OSError):
                os.remove(archive)


def restore_files(sb: "Supabase") -> str:
    """Put the saved work files back. Runs before Kiro Crew starts; a broken copy never stops Panda."""
    if not FILES_ENABLED:
        return "off"
    tmp = f"{WORK}/restore-files.tar.gz"
    try:
        if not blob_load(sb, FILES_BLOB, tmp):
            log("files: nothing saved yet")
            return "none"
        ok, bad = extract_archive(tmp, HOME, FILES_ROOTS)
        entries, _ = select_files(HOME, FILES_ROOTS, exclude=(WORK,))
        _files_sync["sig"] = files_signature(entries)
        log(f"files restored: {ok} items" + (f", {bad} skipped" if bad else ""))
        return "restored"
    except Exception as exc:
        log("files could not be restored:", scrub(exc)[:200])
        return "failed"
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def restore(sb: Supabase) -> str:
    snap = f"{WORK}/restore-crew.tar.gz"
    extra = f"{WORK}/restore-kiro.tar.gz"
    got = blob_load(sb, "latest-crew.tar.gz", snap)
    if got:
        sh(f"kirocrew restore {snap} --mode replace --force", timeout=900)
    if blob_load(sb, "latest-kiro.tar.gz", extra):
        with tarfile.open(extra) as t:
            t.extractall(f"{HOME}/.kiro", filter="data")
        for lock in glob.glob(f"{HOME}/.kiro/sessions/cli/*.lock"):
            os.remove(lock)
    log("restore:", "from snapshot" if got else "fresh start (no snapshot yet)")
    return "restored" if got else "fresh"


def _save_files_note(sb: Supabase, reason: str):
    try:
        info = files_snapshot(sb, reason)
        if info and not info.get("unchanged"):
            SESSION_INFO["files"] = info
    except Exception as exc:  # the chat state is already saved; report it, do not fail the whole stop
        log("files snapshot failed:", scrub(exc)[:200])


def snapshot(sb: Supabase, reason: str, files: bool = True) -> str:
    out = f"{WORK}/snap"
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    sh(f"kirocrew snapshot {out} --keep 1", timeout=900)
    found = sorted(glob.glob(f"{out}/*.tar.gz"))
    if not found:
        raise RuntimeError("snapshot produced no file")
    done = blob_save(sb, "latest-crew.tar.gz", found[-1])
    extra = f"{WORK}/kiro-extra.tar.gz"
    with tarfile.open(extra, "w:gz") as t:
        for d in EXTRA_DIRS:
            p = f"{HOME}/.kiro/{d}"
            if os.path.isdir(p):
                tar_add_live(t, p, d)
    blob_save(sb, "latest-kiro.tar.gz", extra)
    log(f"snapshot uploaded ({reason}), {done['size'] // 1024} KiB in {done['parts']} part(s)")
    if files:
        _save_files_note(sb, reason)
    return now_iso()


def snapshot_files_later(sb: Supabase, reason: str):
    """Periodic work-file save in the background, so a big copy never holds up the message loop."""
    threading.Thread(target=_save_files_note, args=(sb, reason), daemon=True).start()


def start_gateway() -> subprocess.Popen:
    logf = open(f"{WORK}/gateway.log", "w")
    p = subprocess.Popen(["kirocrew", "gateway", "--no-open", "--no-tunnel"], env=ENV,
                         stdout=logf, stderr=subprocess.STDOUT)
    for _ in range(120):
        time.sleep(2)
        try:
            requests.get(f"{GW}/", timeout=3)
            log("gateway up")
            return p
        except Exception:
            if p.poll() is not None:
                raise RuntimeError("gateway exited during startup")
    raise RuntimeError("gateway did not come up")


def mint_token() -> str:
    r = sh("kirocrew token --ttl 13h", timeout=60, check=True)
    m = re.search(r"token=([A-Za-z0-9._~\-]+)", r.stdout)
    if not m:
        raise RuntimeError("could not parse gateway token")
    return m.group(1)


class GatewayError(RuntimeError):
    """A gateway answer >= 400. `str()` is Crew's own message, which the app shows as is."""

    def __init__(self, method: str, path: str, status: int, data):
        self.status, self.data = status, data
        msg = data.get("error") if isinstance(data, dict) else None
        super().__init__(str(msg or f"gateway {method} {path.split('?')[0]}: {status} {str(data)[:200]}"))


class Gateway:
    def __init__(self):
        self.token = mint_token()

    def h(self) -> dict:
        return {"Cookie": f"mc_token_{PORT}={self.token}", "Content-Type": "application/json"}

    def call(self, method: str, path: str, body=None, timeout: int = 60):
        r = requests.request(method, f"{GW}{path}", json=body, headers=self.h(), timeout=timeout)
        if r.status_code in (401, 403):  # an expired token; one retry with a fresh one
            self.token = mint_token()
            r = requests.request(method, f"{GW}{path}", json=body, headers=self.h(), timeout=timeout)
        json_body = "json" in r.headers.get("content-type", "")
        if r.status_code >= 400:
            try:
                data = r.json() if json_body else r.text[:300]
            except ValueError:
                data = r.text[:300]
            raise GatewayError(method, path, r.status_code, data)
        return r.json() if json_body else {}

    def stream_chat(self, slot: str, message: str):
        with requests.post(f"{GW}/api/chat", json={"message": message, "slot": slot},
                           headers=self.h(), stream=True, timeout=(30, None)) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"chat: {r.status_code} {r.text[:300]}")
            if "application/json" in r.headers.get("content-type", ""):
                yield {"receipt": r.json()}
                return
            for raw in r.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                payload = raw[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    yield {"text": payload}


# ── live thinking: Crew broadcasts it only on its websocket (chat_thinking) ─
THINK = {}          # slot -> accumulated thinking text of the running turn
THINK_REPLY = {}    # slot -> assistant message id being streamed
THINK_SENT = {}     # slot -> length already written to Supabase
THINK_LOCK = threading.Lock()
SIDE: dict = {}  # side-chat (/btw) run id -> {"slot", "text", "final"?, "error"?, "at"}, from the websocket
SIDE_LOCK = threading.Lock()
CTX = {}            # slot -> latest context_usage reading from Crew's websocket


def context_usage(gw: "Gateway", slot: str, since: float, streamed: dict | None) -> dict | None:
    """The chat's context window use after this turn: {pct, used_tokens?, window_tokens?, reset?}.

    Crew broadcasts it on the websocket right after the turn (after the SSE closed); when that
    didn't arrive, the slot detail carries the same reading as context_pct.
    """
    def clean(d: dict) -> dict:
        out = {"pct": round(float(d["pct"]), 1)}
        if d.get("used_tokens") and d.get("window_tokens"):
            out.update(used_tokens=int(d["used_tokens"]), window_tokens=int(d["window_tokens"]))
        else:
            out["reset"] = True
        return out

    for _ in range(12):  # up to ~3 s for the websocket reading
        with THINK_LOCK:
            d = CTX.get(slot)
        if d and d.get("at", 0) >= since and not (float(d.get("pct") or 0) <= 0 and streamed
                                                   and float(streamed.get("pct") or 0) > 0):
            return clean(d)
        time.sleep(0.25)
    if streamed and isinstance(streamed.get("pct"), (int, float)):
        return clean(streamed)
    try:
        s = gw.call("GET", f"/api/chat/slots/{slot}?limit=1", timeout=20)
        if isinstance(s, dict) and isinstance(s.get("context_pct"), (int, float)):
            return clean({"pct": s["context_pct"], "used_tokens": s.get("context_used_tokens"),
                          "window_tokens": s.get("context_window_tokens")})
    except Exception as exc:
        log("context reading:", type(exc).__name__)
    return streamed


def thinking_listener(gw: "Gateway", stop: threading.Event):
    import websocket  # websocket-client
    while not stop.is_set():
        try:
            ws = websocket.create_connection(f"ws://127.0.0.1:{PORT}/api/ws", timeout=30,
                                             header=[f"Cookie: mc_token_{PORT}={gw.token}"])
            ws.settimeout(30)
            log("thinking stream connected")
            while not stop.is_set():
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") == "chat_thinking":
                    d = msg.get("data") or {}
                    slot = d.get("slot")
                    with THINK_LOCK:
                        if slot in THINK_REPLY:
                            THINK[slot] = THINK.get(slot, "") + str(d.get("content") or "")
                elif msg.get("type") == "context_usage":
                    # Crew measures the context after the turn, when the chat stream has already ended.
                    d = msg.get("data") or {}
                    if d.get("slot") and isinstance(d.get("pct"), (int, float)):
                        with THINK_LOCK:
                            CTX[d["slot"]] = {**d, "at": time.time()}
                elif msg.get("type") == "chat.side_result":
                    # /btw answers only arrive here: Crew has no GET route for the side chat.
                    d = msg.get("data") or {}
                    if d.get("role") == "assistant" and d.get("run_id"):
                        with SIDE_LOCK:
                            r = SIDE.setdefault(d["run_id"], {"slot": d.get("slot"), "text": "", "at": time.time()})
                            if d.get("final"):
                                r.update(final=str(d.get("content") or r["text"]), error=bool(d.get("is_error")))
                            else:
                                r["text"] += str(d.get("content") or "")
                            for k in [k for k, v in SIDE.items() if time.time() - v["at"] > 1800]:
                                SIDE.pop(k, None)
            ws.close()
        except Exception as exc:
            log("thinking stream:", type(exc).__name__, str(exc)[:100])
        stop.wait(3)


def thinking_pusher(sb: "Supabase", stop: threading.Event):
    """Write new thinking to the streaming reply every ~0.7 s (the chat stream is silent meanwhile)."""
    while not stop.is_set():
        with THINK_LOCK:
            todo = [(slot, THINK_REPLY[slot], THINK.get(slot, "")) for slot in THINK_REPLY
                    if len(THINK.get(slot, "")) != THINK_SENT.get(slot, 0)]
        for slot, rid, text in todo:
            try:
                sb.rest("PATCH", "messages", params={"id": f"eq.{rid}"}, body={"thinking": scrub(text)})
                THINK_SENT[slot] = len(text)
            except Exception as exc:
                log("thinking push:", type(exc).__name__)
        stop.wait(0.7)


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]+")


def clean_step(text: str) -> str:
    """Tool step text for the app: no emojis, no stray punctuation."""
    return _EMOJI.sub("", scrub(text or "")).strip(" :-")[:220]


# ── message handling ────────────────────────────────────────────────────────
def chunk_text(c: dict) -> str:
    """Best-effort assistant text from one SSE chunk (format is not documented)."""
    if not isinstance(c, dict) or c.get("role") in ("user", "tool", "system", "queued"):
        return ""
    for k in ("text", "content", "delta", "chunk"):
        v = c.get(k)
        if isinstance(v, str):
            return v
    return ""


_COMPACTION_PREFIXES = ("✅ Conversation compacted", "Conversation compacted:", "✅ Compacted:")


def is_compaction_notice(text) -> bool:
    """Crew's own compaction receipt (it carries the whole internal summary): never the answer."""
    return isinstance(text, str) and text.lstrip().startswith(_COMPACTION_PREFIXES)


def _row_text(m: dict) -> str:
    v = m.get("content") or m.get("text") or ""
    return v if isinstance(v, str) else json.dumps(v)


def turn_result(gw: Gateway, slot: str, want_stats: bool = True) -> dict:
    """What the turn that just ended left in the slot history.

    Only rows after the last user row count, so a turn that wrote no answer can never pick up
    the previous turn's text. Returns the last assistant text, Crew's per-turn stats
    (`meta.turn_stats`: credits, elapsed_ms, ttft_ms, model) and any error rows.
    """
    out = {"text": "", "stats": None, "errors": []}
    for attempt in range(3 if want_stats else 1):
        try:
            hist = gw.call("GET", f"/api/chat/slots/{slot}?limit=40")
        except Exception as exc:
            log("history read failed:", exc)
            return out
        msgs = [m for m in (hist.get("messages") or hist.get("history") or []) if isinstance(m, dict)]
        last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
        text, stats, errors = "", None, []
        for m in msgs[last_user + 1:]:
            role = m.get("role")
            if (m.get("meta") or {}).get("kind") in ("compaction", "session_reload") or is_compaction_notice(_row_text(m)):
                continue  # Crew's own notices, not the answer
            if role in ("assistant", "agent", "bot"):
                text = _row_text(m) or text
                st = (m.get("meta") or {}).get("turn_stats")
                if isinstance(st, dict):
                    stats = st
            elif role == "error":
                errors.append(_row_text(m))
        out = {"text": text, "stats": stats, "errors": errors}
        # Crew attaches the stats just before the stream ends; give a late write one more look.
        if stats or not text or attempt == 2:
            return out
        time.sleep(0.8)
    return out


# ── slash commands ──────────────────────────────────────────────────────────
# Kiro Crew's chat commands (GET /api/slash-commands) plus its /plain macro. Crew only treats a
# message as a command when the command is the FIRST word, so these are sent exactly as typed:
# no attachments, history replay, project or app context in front of them.
CREW_COMMANDS = {"/agent", "/changelog", "/clear", "/code", "/compact", "/context", "/experiment", "/goal",
                 "/help", "/hooks", "/issue", "/logdump", "/mcp", "/model", "/prompts", "/todos", "/tools",
                 "/usage", "/workflow", "/plain"}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[=>]")


def command_word(text: str) -> str:
    parts = (text or "").strip().split(None, 1)
    word = parts[0].lower() if parts else ""
    return word if word in CREW_COMMANDS else ""


def knowledge_block(gw: Gateway, query: str) -> str:
    """`/kb <query>`: the knowledge-library matches as the context block Crew's dashboard injects."""
    try:
        d = gw.call("GET", f"/api/knowledge/search-for-context?q={quote(query)}&limit=6", timeout=90)
    except Exception as exc:
        log("knowledge search failed:", exc)
        return ""
    items = [i for i in (d.get("results") or []) if isinstance(i, dict) and i.get("content")]
    if not items:
        return ""
    parts = ["[KNOWLEDGE CONTEXT — injected by user from knowledge library]\n"]
    for it in items:
        src = f" ({it['source']})" if it.get("source") else ""
        parts.append(f"## {it.get('title') or 'Untitled'}{src}\n{it['content']}\n")
    parts.append("[END KNOWLEDGE CONTEXT]\n")
    return "\n".join(parts)


READY_SLOTS: set = set()  # slots verified + put in trust mode during this run


def live_slots(gw: Gateway) -> set:
    try:
        data = gw.call("GET", "/api/chat/slots")
    except Exception as exc:
        log("slot list failed:", exc)
        return set()
    rows = data if isinstance(data, list) else (data.get("slots") or [])
    keys = set()
    for s in rows:
        if isinstance(s, dict):
            keys.update(str(s[k]) for k in ("key", "id", "slot", "name") if s.get(k))
        elif isinstance(s, str):
            keys.add(s)
    return keys


EFFORT_WIRE = {"low": "low", "medium": "medium", "high": "high", "extra": "xhigh", "max": "max"}
SLOT_OPTS: dict = {}  # slot -> (model, effort, approvals) applied during this run


def user_settings(sb: Supabase, owner: str) -> dict:
    rows = sb.rest("GET", "user_settings", params={"owner": f"eq.{owner}", "select": "settings"})
    return (rows[0]["settings"] if rows else {}) or {}


def ensure_slot(sb: Supabase, gw: Gateway, chat: dict, settings: dict) -> tuple[str, bool]:
    """Return (slot, needs_replay); applies the chat's model, effort and approval mode."""
    slot = chat.get("slot_id")
    needs_replay = False
    # A slot key saved before the fix below can be a slug of the chat's URL title; such a key
    # breaks /api/chat/slots/{slot}/side and 404s. Force a clean recreate when the stored key
    # is not path-safe (contains anything outside [A-Za-z0-9._-]).
    bad_key = bool(slot) and not re.fullmatch(r"[A-Za-z0-9._-]+", slot)
    if not slot or bad_key or (slot not in READY_SLOTS and slot not in live_slots(gw)):
        agent = chat["agent"] or AGENT
        # Crew slugifies the name into the slot KEY, and that key goes into URL paths
        # (/api/chat/slots/{slot}/...). A free-form title that is a URL ("https://m.youtube.com/...")
        # slugs into a key the /side sub-route can't match -> 404. Name the slot by the stable chat id,
        # which is already a clean uuid-like token, so the derived key is always path-safe.
        slot_name = f"panda-{chat['id']}"[:60]
        created = gw.call("POST", "/api/chat/slots",
                          {"name": slot_name, **({"agent": agent} if agent else {})})
        new = created.get("key") or created.get("id") or created.get("slot") or created.get("name")
        if not new:
            raise RuntimeError(f"slot create returned no key: {list(created)}")
        needs_replay = bool(slot)  # it existed before, so Crew lost it
        slot = new
        sb.rest("PATCH", "chats", params={"id": f"eq.{chat['id']}"}, body={"slot_id": slot})
    READY_SLOTS.add(slot)

    approvals = "trust" if settings.get("approvals", "ask") == "trust" else "normal"
    want = (chat.get("model") or "claude-sonnet-5.5", EFFORT_WIRE.get(chat.get("effort") or "medium", "medium"),
            approvals)
    have = SLOT_OPTS.get(slot, (None, None, None))
    for i, (path, body) in enumerate((
            (f"/api/chat/slots/{slot}/model", {"model": want[0]}),
            (f"/api/chat/slots/{slot}/reasoning-effort", {"reasoning_effort": want[1]}),
            ("/api/chat/mode", {"mode": want[2], "slot": slot}))):
        if have[i] != want[i]:
            try:
                gw.call("POST", path, body)
            except Exception as exc:  # e.g. a model without effort levels
                log(f"slot option not applied ({path.rsplit('/', 1)[-1]}):", exc)
    SLOT_OPTS[slot] = want
    return slot, needs_replay


# /compact writes a summary of the chat; a small fast model does that well for a fraction of the credits.
# Switch the slot to it just for the /compact turn. SLOT_OPTS then no longer matches the chat's model, so
# the next message's ensure_slot() switches the chat straight back to the model the user picked.
COMPACT_MODEL = str(CFG.get("compact_model", "claude-haiku-5.5"))


def use_compact_model(gw: "Gateway", slot: str):
    if not COMPACT_MODEL:
        return
    try:
        gw.call("POST", f"/api/chat/slots/{slot}/model", {"model": COMPACT_MODEL})
        have = SLOT_OPTS.get(slot, (None, None, None))
        SLOT_OPTS[slot] = (COMPACT_MODEL, None, have[2])  # effort re-applied with the chat model next turn
    except Exception as exc:
        log("compact model not applied:", exc)

def replay_prefix(sb: Supabase, chat_id: str, before_id: int, limit: int = 30) -> str:
    rows = sb.rest("GET", "messages", params={
        "chat_id": f"eq.{chat_id}", "id": f"lt.{before_id}", "role": "in.(user,assistant)",
        "status": "eq.done", "order": "id.desc", "limit": str(limit), "select": "role,content,meta"})
    rows = [r for r in rows or [] if not (r.get("meta") or {}).get("command")]  # commands are not conversation
    if not rows:
        return ""
    lines = [f"{'User' if r['role'] == 'user' else 'You'}: {r['content'][:2000]}" for r in reversed(rows)]
    return ("[Earlier messages of this conversation, restored from the app's history. "
            "Treat them as our prior conversation.]\n" + "\n\n".join(lines) +
            "\n[End of restored history]\n\n")


def permission_id(c: dict) -> str:
    for src in (c.get("meta"), c):
        if isinstance(src, dict) and src.get("request_id"):
            return str(src["request_id"])
    try:
        return str(json.loads(c.get("cls") or "{}").get("request_id") or "")
    except (TypeError, ValueError):
        return ""


def is_thinking(c: dict) -> bool:
    kind = f"{c.get('type', '')} {c.get('cls', '')}".lower()
    return "thinking" in kind or "reasoning" in kind


def wait_queued(sb: Supabase, gw: Gateway, slot: str, qid: str, reply_id: int, meta: dict,
                cancelled: threading.Event, limit: float = 1800) -> None:
    """Crew queued the message because this chat's previous turn is still running (e.g. a long
    tool step). Before, the worker took the instant empty receipt as the answer and showed nothing.
    Now the reply says it waits, and the queued turn's answer is read once that turn has run."""
    sb.rest("PATCH", "messages", params={"id": f"eq.{reply_id}"}, body={"meta": meta})
    t0, seen_run = time.time(), False
    while time.time() - t0 < limit:
        time.sleep(2.5)
        try:
            row = sb.rest("GET", "messages", params={"id": f"eq.{reply_id}", "select": "cancel"})
            if row and row[0].get("cancel"):
                cancelled.set()
                with contextlib.suppress(Exception):
                    gw.call("POST", f"/api/chat/slots/{slot}/stop")
                return
            d = gw.call("GET", f"/api/chat/slots/{slot}?limit=1")
        except Exception as exc:
            log("queued wait:", type(exc).__name__)
            continue
        in_queue = any(str(q.get("id") or "") == qid for q in (d.get("queue") or []) if isinstance(q, dict))
        if not in_queue and d.get("running"):
            seen_run = True
        elif not in_queue and not d.get("running") and (seen_run or time.time() - t0 > 6):
            return
    log(f"queued message still waiting after {int(limit)}s")


def ask_approval(sb: Supabase, gw: Gateway, chat: dict, slot: str, reply_id: int, c: dict) -> str:
    """Show an approval card in the app and wait for the user's answer (max 9 min)."""
    rid = permission_id(c)
    tool = clean_step(c.get("content")) or "a tool"
    card = sb.rest("POST", "messages", prefer="return=representation", body={
        "chat_id": chat["id"], "owner": chat["owner"], "role": "tool", "status": "pending",
        "content": tool, "reply_to": reply_id, "meta": {"request_id": rid, "kind": "approval"}})[0]
    sb.rest("PATCH", "messages", params={"id": f"eq.{reply_id}"}, body={"status": "waiting"})
    decision, t0 = "", time.time()
    while time.time() - t0 < 540:
        row = sb.rest("GET", "messages", params={"id": f"eq.{card['id']}", "select": "decision"})[0]
        if row["decision"]:
            decision = row["decision"]
            break
        time.sleep(1.5)
    action = {"approved": "approved", "trust": "trust", "rejected": "rejected"}.get(decision, "rejected")
    if rid:
        try:
            gw.call("POST", f"/api/chat/slots/{slot}/approve", {"action": action, "request_id": rid})
        except Exception as exc:
            log("approve call failed:", exc)
    sb.rest("PATCH", "messages", params={"id": f"eq.{card['id']}"},
            body={"status": "done", "decision": decision or "rejected",
                  "meta": {"request_id": rid, "kind": "approval", "timed_out": not decision}})
    sb.rest("PATCH", "messages", params={"id": f"eq.{reply_id}"}, body={"status": "streaming"})
    log(f"approval {action}{' (timeout)' if not decision else ''}: {tool[:60]}")
    return action


# ── browser: Chromium engine + encrypted login persistence ────────────────
BROWSER_DIR = os.path.expanduser("~/.panda/browser")
BROWSER_STATE = f"{BROWSER_DIR}/state.json"
BROWSER_BLOB = "browser/state.enc"
BROWSER_CFG = os.path.expanduser("~/.panda/browser.json")
SKILL_DIR = os.path.expanduser("~/.kiro/crew/skills/panda-browser")
_browser_proc = {"p": None}
_browser_sync = {"mtime": 0.0, "cfg": None}


def _fernet():
    key = CFG.get("browser_key")
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


def browser_restore(sb: "Supabase"):
    """Download + decrypt the saved logins (cookies) so Panda is still signed in."""
    f = _fernet()
    tmp = f"{WORK}/browser-state.enc"
    if not f or not sb.download(state_key(BROWSER_BLOB), tmp):
        return
    try:
        os.makedirs(BROWSER_DIR, exist_ok=True)
        with open(os.open(BROWSER_STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as out:
            out.write(f.decrypt(open(tmp, "rb").read()))
        log("browser logins restored")
    except Exception as exc:  # wrong key or corrupt blob: start with a clean browser, never crash
        log("browser logins could not be restored:", type(exc).__name__)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def browser_backup(sb: "Supabase", force: bool = False):
    """Encrypt + upload the logins when they changed. Cookies never leave the machine unencrypted."""
    f = _fernet()
    try:
        m = os.stat(BROWSER_STATE).st_mtime
    except OSError:
        return
    if not f or (not force and m == _browser_sync["mtime"]):
        return
    tmp = f"{WORK}/browser-state.enc"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as out:
        out.write(f.encrypt(open(BROWSER_STATE, "rb").read()))
    try:
        sb.upload(state_key(BROWSER_BLOB), tmp, mime="application/octet-stream")
        _browser_sync["mtime"] = m
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def browser_config(sb: "Supabase"):
    """Tell the engine which private channel to stream the live view on."""
    topic = (user_settings(sb, OWNER) or {}).get("browserTopic") or ""
    cfg = {"supabase_url": SB_URL, "anon": SB_ANON, "topic": topic}
    if cfg != _browser_sync["cfg"]:
        os.makedirs(os.path.dirname(BROWSER_CFG), exist_ok=True)
        with open(os.open(BROWSER_CFG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump(cfg, f)
        _browser_sync["cfg"] = cfg


def _install_browser_and_run(sb: "Supabase"):
    """Background thread: install Playwright + Chromium, then supervise the engine."""
    try:
        sh(f"{sys.executable} -m pip install -q playwright aiohttp websockets", timeout=600)
        sh(f"{sys.executable} -m playwright install --with-deps chromium", timeout=1500)
    except Exception as exc:
        log("browser install failed:", exc)
        return
    finally:
        with contextlib.suppress(OSError):
            os.remove(os.path.expanduser("~/.panda/browser-installing"))
    while not _browser_proc.get("stop"):
        p = _browser_proc["p"]
        if p is None or p.poll() is not None:
            logf = open(os.path.expanduser("~/.panda/browser-engine.log"), "a")
            _browser_proc["p"] = subprocess.Popen([sys.executable, f"{SKILL_DIR}/engine.py"], env=ENV, stdout=logf,
                                                  stderr=subprocess.STDOUT, start_new_session=True)
            log("browser engine started")
        time.sleep(5)


def start_browser(sb: "Supabase"):
    os.makedirs(os.path.expanduser("~/.panda"), exist_ok=True)
    # Tells `pb` the browser is being installed, so it waits instead of starting a second engine.
    open(os.path.expanduser("~/.panda/browser-installing"), "w").write(str(os.getpid()))
    browser_restore(sb)
    browser_config(sb)
    threading.Thread(target=_install_browser_and_run, args=(sb,), daemon=True).start()


def stop_browser(sb: "Supabase"):
    _browser_proc["stop"] = True
    p = _browser_proc["p"]
    if p and p.poll() is None:
        p.terminate()  # the engine saves the logins on SIGTERM
        try:
            p.wait(timeout=20)
        except Exception:
            p.kill()
    try:
        browser_backup(sb, force=True)
    except Exception as exc:
        log("final browser backup failed:", exc)


# ── Codex (pictures with the user's ChatGPT plan): login kept across runs ────
# Kaggle starts every run on a clean disk, so the login is saved encrypted (same key as the browser
# logins) and put back at start, and Codex itself is installed again. Only the login and settings
# are kept, not Codex's logs, caches or pictures.
CODEX_VERSION = "0.164.0"  # same pin as skills/panda-image/make_image.py
CODEX_HOME = os.path.expanduser("~/.codex")
CODEX_FILES = ("auth.json", "config.toml")
CODEX_BLOB = "codex/login.enc"
CODEX_FLAG = os.path.expanduser("~/.panda/codex-installing")  # make_image.py waits while it exists
_codex_sync = {"sig": None}


def _codex_sig() -> tuple:
    sig = []
    for n in CODEX_FILES:
        try:
            st = os.stat(os.path.join(CODEX_HOME, n))
            sig.append((n, st.st_mtime, st.st_size))
        except OSError:
            pass
    return tuple(sig)


def _signed_in(sig) -> bool:
    return any(n == "auth.json" for n, *_ in (sig or ()))


def codex_restore(sb: "Supabase") -> bool:
    """Download + decrypt the saved Codex login, so pictures work without signing in again."""
    f = _fernet()
    tmp = f"{WORK}/codex-login.enc"
    if not f or not sb.download(state_key(CODEX_BLOB), tmp):
        return False
    try:
        files = json.loads(f.decrypt(open(tmp, "rb").read()))
        os.makedirs(CODEX_HOME, exist_ok=True)
        for n, b64 in files.items():
            if n in CODEX_FILES:
                p = os.path.join(CODEX_HOME, n)
                with open(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as out:
                    out.write(base64.b64decode(b64))
        _codex_sync["sig"] = _codex_sig()
        log("codex login restored")
        return True
    except Exception as exc:  # wrong key or corrupt blob: the user signs in again, never crash
        log("codex login could not be restored:", type(exc).__name__)
        return False
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def codex_backup(sb: "Supabase", force: bool = False):
    """Encrypt + upload the Codex login when it changed (a sign-in, a token refresh, a sign-out)."""
    f = _fernet()
    sig = _codex_sig()
    if not f or (not force and sig == _codex_sync["sig"]):
        return
    if not _signed_in(sig):
        if _signed_in(_codex_sync["sig"]):  # signed out during this run: forget the saved login too
            sb.delete_object(state_key(CODEX_BLOB))
            log("codex login removed (signed out)")
        _codex_sync["sig"] = sig
        return
    files = {n: base64.b64encode(open(os.path.join(CODEX_HOME, n), "rb").read()).decode()
             for n, *_ in sig}
    tmp = f"{WORK}/codex-login.enc"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as out:
        out.write(f.encrypt(json.dumps(files).encode()))
    try:
        sb.upload(state_key(CODEX_BLOB), tmp, mime="application/octet-stream")
        _codex_sync["sig"] = sig
        log("codex login saved")
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def _codex_install():
    try:
        if not shutil.which("codex", path=ENV["PATH"]):
            sh(f"npm install -g @openai/codex@{CODEX_VERSION}", timeout=600)
        log("codex ready" if shutil.which("codex", path=ENV["PATH"]) else "codex install failed")
    except Exception as exc:
        log("codex install failed:", exc)
    finally:
        with contextlib.suppress(OSError):
            os.remove(CODEX_FLAG)


def start_codex(sb: "Supabase"):
    """Put the Codex login back; if there is one, install Codex in the background (~20 s)."""
    if not codex_restore(sb):
        return
    os.makedirs(os.path.dirname(CODEX_FLAG), exist_ok=True)
    open(CODEX_FLAG, "w").close()
    threading.Thread(target=_codex_install, daemon=True).start()


# ── Voice input: free local Whisper (faster-whisper on the worker's CPU) ─────
# The app records the user's voice and sends it as an `stt` command; this turns it into text.
# Its packages live in their own folder so they never change Kiro Crew's numpy or huggingface-hub.
STT_LIB = os.path.expanduser("~/.panda/stt-lib")
STT_PKGS = "faster-whisper==1.2.1 av==16.1.0"
STT_MAX_BYTES = 4 * 1024 * 1024        # about 4 minutes of phone audio
STT_LANGS = {"en", "it", "uk"}
STT_SERVER = r'''
import json, os, sys
from faster_whisper import WhisperModel
models = {}
def get(name):
    if name not in models:
        models[name] = WhisperModel(name, device="cpu", compute_type="int8",
                                    cpu_threads=max(1, (os.cpu_count() or 2) - 1),
                                    download_root=os.path.expanduser("~/.cache/panda-whisper"))
    return models[name]
get("base")
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    try:
        req = json.loads(line)
        lang = req.get("lang") or None
        # base is fast and good in English; small is needed for Italian and Ukrainian.
        segs, info = get("base" if lang == "en" else "small").transcribe(
            req["path"], language=lang, beam_size=1, vad_filter=True, condition_on_previous_text=False)
        out = {"text": " ".join(s.text.strip() for s in segs).strip(), "secs": round(info.duration, 1)}
    except Exception as exc:
        out = {"error": str(exc)[:300]}
    print(json.dumps(out), flush=True)
'''
_stt = {"p": None, "state": "off", "error": "", "lock": threading.Lock()}


def _stt_spawn():
    script = f"{STT_LIB}/panda_stt_server.py"
    with open(script, "w") as f:
        f.write(STT_SERVER)
    env = dict(ENV, PYTHONPATH=STT_LIB, HF_HUB_DISABLE_TELEMETRY="1")
    p = subprocess.Popen([sys.executable, "-u", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1)
    first = p.stdout.readline()  # waits for the model to load (downloads it the first time)
    if '"ready"' not in first:
        p.kill()
        raise RuntimeError("voice model did not load")
    _stt["p"] = p


def _stt_install():
    """Background thread: install faster-whisper, then load the model so the first clip is quick."""
    _stt["state"] = "installing"
    try:
        if not os.path.isdir(f"{STT_LIB}/faster_whisper"):
            os.makedirs(STT_LIB, exist_ok=True)
            sh(f"{sys.executable} -m pip install -q --target {shlex.quote(STT_LIB)} {STT_PKGS}", timeout=900, check=True)
        with _stt["lock"]:
            _stt_spawn()
        _stt["state"] = "ready"
        log("voice input ready")
    except Exception as exc:
        _stt.update(state="failed", error=str(exc)[:200])
        log("voice input install failed:", exc)


def start_stt():
    threading.Thread(target=_stt_install, daemon=True).start()


# ── video: MiniMax H3 on the owner's Modal app (engine: skills/panda-video/video.py) ──
MODAL_PKG = "modal==1.6.2"  # the client the panda-h3 app was deployed with
VIDEO = {"engine": None, "state": "off", "error": "", "slot": "", "lock": threading.Lock()}


def video_llm(gw: "Gateway"):
    """Prompt writer: one short turn on a small private Crew slot (kirocrew-lite, Sonnet, low effort)."""
    def ask(text: str) -> str:
        with VIDEO["lock"]:
            created = gw.call("POST", "/api/chat/slots", {"name": "Video prompt writer", "agent": "kirocrew-lite"})
            slot = created.get("key") or created.get("id") or created.get("slot") or created.get("name")
            if not slot:
                raise RuntimeError("no writer slot")
            try:
                for path, body in ((f"/api/chat/slots/{slot}/model", {"model": "claude-sonnet-5.5"}),
                                   (f"/api/chat/slots/{slot}/reasoning-effort", {"reasoning_effort": "low"})):
                    try:
                        gw.call("POST", path, body)
                    except Exception:
                        pass
                out, done = {"text": ""}, threading.Event()

                def run():
                    try:
                        for c in gw.stream_chat(slot, text):
                            if not isinstance(c, dict):
                                continue
                            if c.get("type") == "chunk":
                                out["text"] += chunk_text(c)
                            elif c.get("type") == "assistant" and c.get("content"):
                                out["text"] = c["content"] if len(c["content"]) >= len(out["text"]) else out["text"]
                            elif c.get("type") in ("permission", "error"):
                                break  # a writer never needs tools; give up and use the template
                    finally:
                        done.set()
                threading.Thread(target=run, daemon=True).start()
                if not done.wait(120):
                    _safe(gw.call, "POST", f"/api/chat/slots/{slot}/stop", {})
                    raise TimeoutError("the writer took too long")
                return out["text"]
            finally:
                _safe(gw.call, "DELETE", f"/api/chat/slots/{slot}")
    return ask


def _video_install(sb: "Supabase", gw: "Gateway"):
    VIDEO["state"] = "installing"
    try:
        try:
            import modal  # noqa: F401
        except ImportError:
            sh(f"{sys.executable} -m pip install -q {MODAL_PKG}", timeout=900, check=True)
        path = f"{HOME}/.kiro/crew/skills/panda-video"
        if path not in sys.path:
            sys.path.insert(0, path)
        import video as pvideo  # noqa: PLC0415  (skills/panda-video/video.py)
        eng = pvideo.Engine(sb, SB_URL, lambda: OWNER, log=log, llm=video_llm(gw),
                            notify=lambda title, body: push(sb, OWNER, title, body, "./?v=video"))
        eng.start()
        VIDEO.update(engine=eng, state="ready")
        log("video engine ready")
    except Exception as exc:
        VIDEO.update(state="failed", error=_reason(exc)[:200])
        log("video engine failed:", exc)


def start_video(sb: "Supabase", gw: "Gateway"):
    threading.Thread(target=_video_install, args=(sb, gw), daemon=True).start()


def video_command(op: str, args: dict) -> dict:
    if VIDEO["state"] == "installing":
        deadline = time.time() + 150
        while VIDEO["state"] == "installing" and time.time() < deadline:
            time.sleep(1)
    eng = VIDEO["engine"]
    if not eng:
        raise RuntimeError("video is not ready yet" + (f": {VIDEO['error']}" if VIDEO["error"] else ""))
    fn = {"video.prompt": eng.write_prompt, "video.estimate": eng.estimate, "video.submit": eng.submit,
          "video.cancel": eng.cancel, "video.delete": eng.delete, "video.auto": eng.auto,
          "video.auto_cancel": eng.auto_cancel, "video.auto_dismiss": eng.auto_dismiss,
          "video.usage": eng.usage_now}[op]
    return fn(args)


def speech_to_text(args: dict) -> dict:
    if _stt["state"] == "installing":
        deadline = time.time() + 120  # the first minute or two after Start
        while _stt["state"] == "installing" and time.time() < deadline:
            time.sleep(1)
    if _stt["state"] != "ready":
        raise RuntimeError("voice input is not ready yet" + (f": {_stt['error']}" if _stt["error"] else ""))
    raw = base64.b64decode(str(args.get("audio") or ""), validate=True)
    if not raw:
        raise ValueError("no audio")
    if len(raw) > STT_MAX_BYTES:
        raise ValueError("recording is too long")
    mime = str(args.get("mime") or "")
    ext = ".m4a" if "mp4" in mime or "aac" in mime else ".ogg" if "ogg" in mime else ".webm"
    lang = str(args.get("lang") or "")
    path = f"{WORK}/stt-{uuid.uuid4().hex}{ext}"
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
        f.write(raw)
    try:
        with _stt["lock"]:  # one clip at a time; the model uses every core anyway
            p = _stt["p"]
            if p is None or p.poll() is not None:
                _stt_spawn()
                p = _stt["p"]
            p.stdin.write(json.dumps({"path": path, "lang": lang if lang in STT_LANGS else None}) + "\n")
            p.stdin.flush()
            reply = json.loads(p.stdout.readline() or '{"error": "voice server stopped"}')
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
    if reply.get("error"):
        raise RuntimeError("could not read the recording" if "Invalid data" in reply["error"] else reply["error"])
    return {"text": reply.get("text", ""), "secs": reply.get("secs")}


ENGINE = f"http://127.0.0.1:{os.environ.get('PANDA_BROWSER_PORT', '5480')}"


def engine_call(req: dict, timeout: float = 10) -> dict:
    r = requests.post(f"{ENGINE}/do", json=req, timeout=timeout)
    return r.json()


def browser_snapshot(sb: "Supabase", chat: dict, reply_id: int, since: float) -> dict | None:
    """If the browser was used during this turn, keep its final screen with the reply.

    The live view ends with the worker; the saved frame lets the chat show what Panda's browser
    looked like at the end of that answer (the GPT-style card), even days later.
    """
    try:
        # Ask the engine itself: `pb` can start it too, so the worker's own process handle may be stale.
        st = engine_call({"op": "status"}, timeout=4)
    except Exception:
        return None  # no browser running
    try:
        if not st.get("ok") or float(st.get("last_agent_ts") or 0) < since:
            return None
        fr = engine_call({"op": "frame", "quality": 72}, timeout=15)
        if not fr.get("ok") or not fr.get("jpeg"):
            return None
        tmp = f"{WORK}/browser-frame-{reply_id}.jpg"
        with open(tmp, "wb") as f:
            f.write(base64.b64decode(fr["jpeg"]))
        obj = f"{chat['owner']}/browser/{chat['id']}/{reply_id}.jpg"
        try:
            sb.upload(obj, tmp, bucket=FILES_BUCKET, mime="image/jpeg")
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)
        return {"url": scrub(fr.get("url", ""))[:500], "title": scrub(fr.get("title", ""))[:200], "shot": obj,
                "w": fr.get("w"), "h": fr.get("h"), "at": now_iso()}
    except Exception as exc:
        log("browser snapshot skipped:", type(exc).__name__, str(exc)[:80])
        return None


def browser_forget(sb: "Supabase") -> dict:
    """Delete every saved login: running engine, local file and the encrypted copy."""
    note = ""
    if _browser_proc["p"] and _browser_proc["p"].poll() is None:
        r = sh(f"{sys.executable} {SKILL_DIR}/pb.py forget", timeout=60)
        note = (r.stdout or "").strip()[:100]
    for path in (BROWSER_STATE,):
        with contextlib.suppress(OSError):
            os.remove(path)
    sb.delete_object(state_key(BROWSER_BLOB))
    _browser_sync["mtime"] = 0.0
    return {"forgotten": True, "note": note}


# ── Google Drive (the user's own storage, drive.file scope) ───────────────
DRIVE_CFG = os.path.expanduser("~/.panda/drive.json")
DRIVE_TOOL = os.path.expanduser("~/.kiro/crew/skills/panda-drive/drive.py")
DRIVE_MIN = 45 * 2**20  # bigger outputs go to Drive (Supabase file limit is 50 MB)


def setup_drive():
    d = CFG.get("drive")
    if not d:
        return False
    if OWNER != PRIMARY_OWNER:  # worker.json's Drive belongs to the primary account, not to this user
        log("google drive: not connected for this account (big files stay in Storage)")
        return False
    os.makedirs(os.path.dirname(DRIVE_CFG), exist_ok=True)
    with open(os.open(DRIVE_CFG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(d, f)
    log("google drive connected")
    return True


def drive_get(file_id: str, dest: str) -> bool:
    """Download a Drive file by id to `dest` (the saved work files, when they were too big for Storage)."""
    if not file_id or not os.path.exists(DRIVE_CFG):
        return False
    r = subprocess.run([sys.executable, DRIVE_TOOL, "get", file_id, dest], capture_output=True, text=True, timeout=3600)
    if r.returncode != 0 or not os.path.isfile(dest):
        log("drive download failed:", scrub(r.stdout + r.stderr)[-300:])
        return False
    return True


def drive_rm(file_id: str) -> None:
    """Move a Drive file to the trash (the previous copy of the saved work files)."""
    if file_id and os.path.exists(DRIVE_CFG):
        with contextlib.suppress(Exception):
            subprocess.run([sys.executable, DRIVE_TOOL, "rm", file_id], capture_output=True, text=True, timeout=300)


def drive_put(path: str, folder: str = "outputs") -> dict | None:
    if not os.path.exists(DRIVE_CFG):
        return None
    r = subprocess.run([sys.executable, DRIVE_TOOL, "put", path, folder], capture_output=True, text=True, timeout=3600)
    out = r.stdout
    m_id = re.search(r"^Id: (\S+)", out, re.M)
    if r.returncode != 0 or not m_id:
        log("drive upload failed:", scrub(r.stdout + r.stderr)[-300:])
        return None
    m_link = re.search(r"^Link: (\S+)", out, re.M)
    return {"id": m_id.group(1), "link": m_link.group(1) if m_link else ""}


# ── files: agent outputs -> app cards, app uploads -> agent ────────────────
def scan_outputs() -> dict:
    found = {}
    for root, _, names in os.walk(OUTPUTS):
        for n in names:
            p = os.path.join(root, n)
            try:
                st = os.stat(p)
                found[p] = (st.st_mtime, st.st_size)
            except OSError:
                pass
    return found


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip()[:120] or "file"


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")
_MD_TARGET = re.compile(r"\]\(\s*<?([^)>\s]+)")     # the target of ](…) or ![…](…)
_MD_IMG = re.compile(r"!\[[^\]]*\]\(\s*<?([^)>\s]+)")  # only ![…](…) image embeds
_MD_CODE = re.compile(r"`([^`\n]+)`")


def _reply_refs(text: str) -> tuple[set, set]:
    """(all referenced targets, image-embedded targets) from the reply's Markdown."""
    text = text or ""
    targets = {t.strip() for t in _MD_TARGET.findall(text)} | {t.strip() for t in _MD_CODE.findall(text)}
    return targets, {t.strip() for t in _MD_IMG.findall(text)}


def _resolve_local(target: str) -> str | None:
    """A referenced path that points to a real file under ~/outputs or ~/work, or an image Kiro Crew
    keeps beside a chat (nothing else)."""
    t = (target or "").strip().strip("<>")
    if not t or t.startswith("panda-file:") or re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I):
        return None
    p = os.path.abspath(os.path.expanduser(t))
    roots = (OUTPUTS, WORKDIR)
    if not any(p == r or p.startswith(r + os.sep) for r in roots) and not _crew_attachment(p):
        return None
    return p if os.path.isfile(p) else None


CREW_SESSIONS = os.path.expanduser("~/.kiro/crew/sessions")
_ATTACH_PREFIX = re.compile(r"^[0-9a-f]{16}-")  # Crew names its copies <sha256 prefix>-<name>


def _crew_attachment(p: str) -> bool:
    """An image in sessions/<chat>.attachments/. When Crew saves a turn it copies every image the
    reply shows with ![](…) there and rewrites the path, so the final text names that copy, not the
    file the agent made (in ~/work, ~/.codex, …)."""
    parent = os.path.dirname(p)
    return (os.path.dirname(parent) == CREW_SESSIONS and parent.endswith(".attachments")
            and p.lower().endswith(IMAGE_EXT) and not os.path.islink(p))


def _referenced(path: str, targets: set) -> bool:
    base = os.path.basename(path)
    forms = {path, path.replace(HOME, "~", 1), os.path.relpath(path, OUTPUTS) if path.startswith(OUTPUTS) else path}
    for t in targets:
        t = t.strip().strip("<>")
        if t in forms or os.path.basename(t.rstrip("/")) == base:
            return True
    return False


def _publish_one(sb: Supabase, chat: dict, message_id: int, path: str, size: int) -> dict | None:
    name = os.path.basename(path)
    if _crew_attachment(path):
        name = _ATTACH_PREFIX.sub("", name) or name
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    body = {"owner": chat["owner"], "chat_id": chat["id"], "message_id": message_id,
            "project_id": chat.get("project_id"), "kind": "output", "name": name, "mime": mime, "size": size}
    try:
        if size > DRIVE_MIN:
            d = drive_put(path)
            if not d:
                log(f"skipped {name}: over 45 MB and Drive is not connected")
                return None
            body["path"] = f"drive:{d['id']}"
        else:
            obj = f"{chat['owner']}/chats/{chat['id']}/{int(time.time()*1000)}-{safe_name(name)}"
            sb.upload(obj, path, bucket=FILES_BUCKET, mime=mime)
            body["path"] = obj
        return sb.rest("POST", "files", prefer="return=representation", body=body)[0]
    except Exception as exc:
        log("file publish failed:", exc)
        return None


# Chats answer in parallel and share ~/outputs, so "every new file there" is only this turn's when no
# other turn ran at the same time. Otherwise a picture made in one chat showed up in another.
TURNS: dict = {}          # "chat:msg" -> True once another turn overlapped it
TURNS_LOCK = threading.Lock()
CLAIMED: dict = {}        # path -> (mtime, size) already published by some turn
# The turn's own steps used the browser (the browser is shared too, so its last screen is not proof).
USED_BROWSER = re.compile(r"panda-browser|\bpb(?:\.py)?\s+(open|click|type|select|hover|scroll|snap|text|shot|"
                          r"handoff|press|tab|back|reload|wait|newtab)", re.I)


def turn_begin(key: str) -> None:
    with TURNS_LOCK:
        for k in TURNS:
            TURNS[k] = True
        TURNS[key] = bool(TURNS)


def turn_end(key: str) -> bool:
    with TURNS_LOCK:
        return TURNS.pop(key, False)


def publish_outputs(sb: Supabase, chat: dict, message_id: int, before: dict, reply: str = "",
                    overlapped: bool = False) -> list:
    """Upload the turn's deliverables; return [(local_path, file_row, inline)].

    What counts as a deliverable:
      - files the reply names (a link, an image embed, or a backtick path), so a deck built from
        photos publishes only the deck, not every photo it used;
      - if the reply names none, every new file in ~/outputs (a one-file answer that wasn't named).
    Working files in ~/work are published only when the reply points at them (e.g. an inline image).
    `inline` is set for images embedded with ![](…): the app shows them in the text, not as a card.
    """
    targets, img_targets = _reply_refs(reply)
    new = {p: sz for p, (mt, sz) in scan_outputs().items()
           if before.get(p) != (mt, sz) and sz > 0 and CLAIMED.get(p) != (mt, sz)}
    for t in targets | img_targets:  # inline images etc. the agent kept in ~/work
        p = _resolve_local(t)
        if p and p not in new:
            new[p] = os.path.getsize(p)
    referenced = {p for p in new if _referenced(p, targets | img_targets)}
    chosen = referenced or (set() if overlapped else {p for p in new if p.startswith(OUTPUTS + os.sep)})
    published = []
    for path in sorted(chosen):
        row = _publish_one(sb, chat, message_id, path, new[path])
        if not row:
            continue
        inline = path.lower().endswith(IMAGE_EXT) and _referenced(path, img_targets)
        published.append((path, row, inline))
        with contextlib.suppress(OSError):
            st = os.stat(path)
            CLAIMED[path] = (st.st_mtime, st.st_size)
        if len(published) >= 20:
            break
    if published:
        log(f"published {len(published)} file(s)" + (f", {sum(i for _, _, i in published)} inline" if any(i for _, _, i in published) else ""))
    return published


def link_files(text: str, published: list) -> str:
    """Point the reply's local paths at the uploaded copies (the app resolves panda-file:)."""
    for local, row, _inline in published:
        for form in (local, local.replace(HOME, "~", 1)):
            text = text.replace(f"]({form})", f"](panda-file:{row['id']})")
            text = text.replace(f"](<{form}>)", f"](panda-file:{row['id']})")
            text = text.replace(f"`{form}`", f"`{os.path.basename(local)}`")
    return text


def fetch_attachments(sb: Supabase, chat: dict, msg: dict) -> list:
    out = []
    for a in (msg.get("meta") or {}).get("attachments", [])[:10]:
        dest = os.path.expanduser(f"~/uploads/{chat['id']}/{safe_name(a.get('name') or 'file')}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if a.get("path") and sb.download(a["path"], dest, bucket=FILES_BUCKET):
            out.append(dest)
    return out


PROJECT_APPLIED: dict = {}  # slot -> project_id whose context the slot already has


def project_preamble(sb: Supabase, chat: dict, slot: str, fresh: bool) -> str:
    pid = chat.get("project_id")
    if not pid or (not fresh and PROJECT_APPLIED.get(slot) == pid):
        return ""
    rows = sb.rest("GET", "projects", params={"id": f"eq.{pid}", "select": "*"})
    if not rows:
        return ""
    p = rows[0]
    paths = []
    for f in sb.rest("GET", "files", params={"project_id": f"eq.{pid}", "kind": "eq.project", "select": "name,path"}) or []:
        dest = os.path.expanduser(f"~/projects/{pid}/{safe_name(f['name'])}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest) or sb.download(f["path"], dest, bucket=FILES_BUCKET):
            paths.append(dest)
    PROJECT_APPLIED[slot] = pid
    parts = [f"[PROJECT: {p['name']}]"]
    if p.get("description"):
        parts.append(p["description"])
    if p.get("instructions"):
        parts.append("Project instructions (follow them in this conversation):\n" + p["instructions"])
    if paths:
        parts.append("Project files (read them when relevant):\n" + "\n".join(f"- `{x}`" for x in paths))
    return "\n".join(parts) + "\n[END PROJECT]\n\n"


# ── phone push notifications ───────────────────────────────────────────────
def push(sb: Supabase, owner: str, title: str, body: str, url: str = "./"):
    key = CFG.get("vapid_private")
    if not key:
        return
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return
    try:
        subs = sb.rest("GET", "push_subscriptions", params={"owner": f"eq.{owner}", "select": "*"}) or []
    except Exception as exc:  # e.g. migration 003 not run yet: never break a turn over a notification
        log("push skipped:", type(exc).__name__)
        return
    for s in subs:
        try:
            webpush({"endpoint": s["endpoint"], "keys": s["keys"]},
                    json.dumps({"title": title, "body": scrub(body)[:180], "url": url}),
                    vapid_private_key=key, vapid_claims={"sub": CFG.get("vapid_sub", "mailto:panda@example.com")},
                    ttl=3600)
        except WebPushException as exc:
            code = getattr(getattr(exc, "response", None), "status_code", 0)
            if code in (404, 410):  # the phone unsubscribed
                sb.rest("DELETE", "push_subscriptions", params={"endpoint": f"eq.{s['endpoint']}"})
            else:
                log("push failed:", code)
        except Exception as exc:
            log("push failed:", type(exc).__name__)


PENDING_HINTS: set = set()   # slots that still need the app context (a command came first)
PENDING_REPLAY: set = set()  # slots Crew lost whose history replay waits for the next normal message


def app_hints(settings: dict) -> str:
    hints = []
    if settings.get("name"):
        hints.append(f"The user's name is {settings['name']}.")
    if SECRET_NAMES:
        hints.append("API keys stored in the user's vault (load with `source ~/.panda/secrets.env` "
                     "inside a bash command, never print them): " + ", ".join(SECRET_NAMES) + ".")
    return ("[APP CONTEXT] " + " ".join(hints) + "\n\n") if hints else ""


def finish_without_model(sb: Supabase, chat: dict, msg: dict, text: str, meta: dict):
    """Answer in the app without a model turn (e.g. `/kb` with nothing found)."""
    sb.rest("POST", "messages", body={"chat_id": chat["id"], "owner": chat["owner"], "role": "assistant",
                                      "status": "done", "reply_to": msg["id"], "content": text, "meta": meta})
    sb.rest("PATCH", "messages", params={"id": f"eq.{msg['id']}"}, body={"status": "done"})


# Commands after which the conversation no longer carries what Panda put in front of it (the app
# context, the Developer instructions, the project): the next message brings it again.
RESET_COMMANDS = {"/clear", "/compact"}


def context_lost(sb: Supabase, chat_id: str, before_id: int) -> str:
    """Why this chat's context must be given again, or "" if it still has it.

    Read from the chat's own rows, so it also holds after a worker restart: a /clear or /compact
    since the last normal message, or Crew compacting by itself (the context reading dropped a lot
    during the last answer).
    """
    try:
        rows = sb.rest("GET", "messages", params={
            "chat_id": f"eq.{chat_id}", "id": f"lt.{before_id}", "role": "in.(user,assistant)",
            "order": "id.desc", "limit": "24", "select": "role,content,meta"}) or []
    except Exception as exc:
        log("context check:", type(exc).__name__)
        return ""
    for r in rows:  # newest first, back to the last normal message
        if r["role"] != "user":
            continue
        word = command_word(r.get("content") or "")
        if word in RESET_COMMANDS:
            return word
        if not word:
            break
    pcts = [((r.get("meta") or {}).get("usage") or {}).get("pct") for r in rows
            if r["role"] == "assistant" and not (r.get("meta") or {}).get("command")]
    pcts = [p for p in pcts if isinstance(p, (int, float))][:2]
    if len(pcts) == 2 and pcts[1] >= 35 and pcts[0] < pcts[1] * 0.55:
        return "auto-compaction"
    return ""


def handle(sb: Supabase, gw: Gateway, msg: dict):
    chat = sb.rest("GET", "chats", params={"id": f"eq.{msg['chat_id']}", "select": "*"})[0]
    settings = user_settings(sb, chat["owner"])
    fresh_slot = not chat.get("slot_id")
    slot, needs_replay = ensure_slot(sb, gw, chat, settings)
    raw = msg["content"] or ""
    cmd = command_word(raw)
    kb = None if cmd else re.match(r"^\s*/kb\s+(.+)$", raw, re.S | re.I)
    if cmd:
        # Sent as typed (Crew matches the first word). Context the app would add waits for the next message.
        prompt = _ANSI.sub("", raw.strip())
        if fresh_slot or needs_replay or cmd in RESET_COMMANDS:
            PENDING_HINTS.add(slot)
        if cmd in RESET_COMMANDS:
            PROJECT_APPLIED.pop(slot, None)
        if cmd == "/compact":
            use_compact_model(gw, slot)
        if needs_replay:
            PENDING_REPLAY.add(slot)
        log(f"chat {chat['id']}: command {cmd}")
    else:
        prompt = raw
        if kb:
            query = kb.group(1).strip()
            block = knowledge_block(gw, query)
            if not block:
                finish_without_model(sb, chat, msg, f"Nothing in your knowledge library matches “{query[:200]}”.",
                                     {"command": "/kb"})
                return
            prompt = block + "\n" + query
        attached = fetch_attachments(sb, chat, msg)
        if attached:
            prompt = ("[Files the user attached (read them):\n" + "\n".join(f"- `{p}`" for p in attached) +
                      "]\n\n" + prompt)
        if needs_replay or slot in PENDING_REPLAY:
            PENDING_REPLAY.discard(slot)
            prompt = replay_prefix(sb, chat["id"], msg["id"]) + prompt
            log(f"chat {chat['id']}: Crew lost the slot, replayed history from Supabase")
        lost = "" if (fresh_slot or needs_replay or slot in PENDING_HINTS) else context_lost(sb, chat["id"], msg["id"])
        if lost:
            PENDING_HINTS.add(slot)
            log(f"chat {chat['id']}: context given again after {lost}")
        prompt = project_preamble(sb, chat, slot, fresh_slot or needs_replay or bool(lost) or slot in PENDING_HINTS) + prompt
        dev_chat = is_developer_chat(settings, chat)
        if fresh_slot or needs_replay or slot in PENDING_HINTS:
            PENDING_HINTS.discard(slot)
            prompt = app_hints(settings) + (dev_preamble() if dev_chat else "") + prompt
        elif dev_chat:
            # Every message of the Developer chat says so (one short line), so the agent never has
            # to guess whether the full instructions from earlier still apply.
            prompt = DEV_MARK + _latest_line() + "\n" + prompt
    before = scan_outputs()
    turn_begin(reply_key := f"{chat['id']}:{msg['id']}")
    base_meta = {"model": chat.get("model"), "effort": chat.get("effort")}
    if cmd or kb:
        base_meta["command"] = cmd or "/kb"
    reply = sb.rest("POST", "messages", prefer="return=representation",
                    body={"chat_id": chat["id"], "owner": chat["owner"], "role": "assistant",
                          "status": "streaming", "reply_to": msg["id"], "meta": base_meta})[0]
    text, thinking, tools, usage, t0 = "", "", [], None, time.time()
    with THINK_LOCK:
        THINK[slot], THINK_REPLY[slot], THINK_SENT[slot] = "", reply["id"], 0
    meta = lambda: {**base_meta, "replayed": needs_replay,
                    "tools": tools[-30:], "usage": usage, "secs": round(time.time() - t0, 1)}
    # Pushes run on a small background thread too, so a quiet stretch (a long tool call, the model
    # thinking before its first token) still delivers the last text and thinking instead of holding
    # them until the next stream event. The main loop only marks the state dirty.
    push_lock, stream_done, cancelled = threading.Lock(), threading.Event(), threading.Event()
    state = {"dirty": False, "last": 0.0}

    def push_now():
        nonlocal thinking
        with push_lock:
            with THINK_LOCK:
                thinking = THINK.get(slot, "")
            if not (text or thinking or tools) or cancelled.is_set():
                return
            state["dirty"], state["last"] = False, time.time()
            row = sb.rest("PATCH", "messages", params={"id": f"eq.{reply['id']}"}, prefer="return=representation",
                          body={"content": scrub(text), "thinking": scrub(thinking), "meta": meta()})
            if row and row[0].get("cancel") and not cancelled.is_set():  # "Stop generating" pressed in the app
                cancelled.set()
                try:
                    gw.call("POST", f"/api/chat/slots/{slot}/stop")
                except Exception as exc:
                    log("stop call failed:", exc)

    def flusher():
        seen_think, last_check = 0, 0.0
        while not stream_done.wait(0.4):
            # "Stop" sets messages.cancel; look for it on its own clock, so it also lands before the
            # first word and during a long silent tool call (when there is nothing to push).
            if not cancelled.is_set() and time.time() - last_check >= 1.5:
                last_check = time.time()
                try:
                    row = sb.rest("GET", "messages", params={"id": f"eq.{reply['id']}", "select": "cancel"})
                    if row and row[0].get("cancel"):
                        cancelled.set()
                        log(f"chat {chat['id']}: stop pressed")
                        gw.call("POST", f"/api/chat/slots/{slot}/stop")
                except Exception as exc:
                    log("stop check failed:", type(exc).__name__)
            with THINK_LOCK:
                n = len(THINK.get(slot, ""))
            if n != seen_think:
                seen_think, state["dirty"] = n, True
            if state["dirty"] and time.time() - state["last"] >= 0.4:
                try:
                    push_now()
                except Exception as exc:
                    log("stream push failed:", type(exc).__name__)

    seg_break = False  # a tool ran since the last text: the next text starts a new paragraph
    compacted = False  # Crew compacted the context during this turn
    threading.Thread(target=flusher, daemon=True).start()
    if not cmd:
        with BUSY_LOCK:
            BUSY_SLOTS[chat["id"]] = {"slot": slot, "since": time.time()}
    queued_id = ""  # Crew queued this message: the chat's previous turn is still running
    try:
        for c in gw.stream_chat(slot, prompt):
            if not isinstance(c, dict):
                continue
            if isinstance(c.get("receipt"), dict) and c["receipt"].get("queued"):
                queued_id = str(c["receipt"].get("queue_id") or "") or "?"
                continue
            kind = c.get("type")
            if kind == "permission":
                ask_approval(sb, gw, chat, slot, reply["id"], c)
                continue
            if kind == "tool":
                step = clean_step(c.get("content"))
                if step and (not tools or tools[-1] != step):
                    tools.append(step)
                seg_break = bool(text)
                state["last"] = 0.0  # show the new step in the app right away
            elif kind == "context_usage":
                try:
                    reading = json.loads(c.get("content") or "{}")
                    # A compaction mid-turn sends a bogus 0 % reading: keep the last real one.
                    if not (isinstance(reading.get("pct"), (int, float)) and reading["pct"] <= 0 and usage):
                        usage = reading
                except ValueError:
                    pass
            elif kind == "error":
                tools.append("error: " + clean_step(c.get("content")))
            elif is_thinking(c):
                with THINK_LOCK:
                    THINK[slot] = THINK.get(slot, "") + (c.get("content") or c.get("text") or "")
            elif is_compaction_notice(chunk_text(c) if kind == "chunk" else c.get("content")):
                # Crew's own "✅ Conversation compacted: <summary>" — an internal summary, not the answer.
                tools.append("context compacted")
                seg_break = bool(text)
                compacted = True
            elif kind == "chunk":
                piece = chunk_text(c)
                if piece and seg_break:
                    # Text before and after a tool call arrives with no separator ("I'll check.Done.").
                    if not text.endswith(("\n", " ")) and not piece.startswith(("\n", " ")):
                        piece = "\n\n" + piece
                    seg_break = False
                text += piece
            elif kind == "assistant" and c.get("content"):
                text = c["content"] if len(c["content"]) >= len(text) else text
            state["dirty"] = True
            if time.time() - state["last"] > 0.4:
                push_now()
            if cancelled.is_set():
                tools.append("stopped by you")
                break
        if queued_id:
            wait_queued(sb, gw, slot, queued_id, reply["id"], {**meta(), "queued": True}, cancelled)
        if cancelled.is_set() and "stopped by you" not in tools:
            tools.append("stopped by you")
        stream_done.set()
        with BUSY_LOCK:
            BUSY_SLOTS.pop(chat["id"], None)
        with push_lock:  # wait out a push still in flight, so it cannot land after the final write
            pass
        time.sleep(0.3)  # let the last thinking frames land
        with THINK_LOCK:
            thinking = THINK.get(slot, thinking)
            THINK_REPLY.pop(slot, None)
        res = turn_result(gw, slot, want_stats=not cmd)
        usage = context_usage(gw, slot, t0, usage)
        final = res["text"] or text
        # Crew's history joins the segments of a turn with no separator, and after a steer it keeps
        # only the part after the steered message. Keep the streamed copy (paragraph breaks, the
        # whole turn) whenever it holds every word of the history text.
        squash = lambda v: re.sub(r"\s+", "", v or "")
        if text and final != text and squash(final) and squash(final) in squash(text):
            final = text
        if cmd:
            final = _ANSI.sub("", final)
        if not final.strip() and res["errors"]:
            raise RuntimeError(res["errors"][-1][:400])
        published = publish_outputs(sb, chat, reply["id"], before, final, overlapped=turn_end(reply_key))
        final = scrub(link_files(final, published))
        # Inline images show in the text; only the rest become download cards.
        done_meta = {**meta(), "files": [row["id"] for _, row, inline in published if not inline]}
        stats = res["stats"] or {}
        if isinstance(stats.get("credits"), (int, float)):
            done_meta["credits"] = round(float(stats["credits"]), 4)
        if stats:
            done_meta["turn_stats"] = {k: stats[k] for k in ("elapsed_ms", "ttft_ms", "model", "credits", "retries")
                                       if k in stats}
        shot = browser_snapshot(sb, chat, reply["id"], t0) if USED_BROWSER.search("\n".join(tools)) else None
        if shot:
            done_meta["browser"] = shot
        for attempt in range(3):  # a late network blip must not turn a finished answer into an error
            try:
                sb.rest("PATCH", "messages", params={"id": f"eq.{reply['id']}"},
                        body={"content": final, "thinking": scrub(thinking), "status": "done", "meta": done_meta})
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
        _safe(lambda: sb.rest("PATCH", "messages", params={"id": f"eq.{msg['id']}"}, body={"status": "done"}))
        if (not cmd and USED_HELPERS.search("\n".join(tools))) or chat["id"] in FOLLOW:
            watch_followups(sb, gw, chat, slot)
        USAGE_STATE["poke"] = True  # refresh the account total soon
        log(f"answered message {msg['id']} ({len(final)} chars, {len(thinking)} thinking, "
            f"{done_meta.get('credits', '?')} credits)")
        if settings.get("notifyReplies", True) and time.time() - t0 > 20:  # only long answers
            push(sb, chat["owner"], f"Panda replied · {chat.get('title') or 'chat'}"[:60],
                 re.sub(r"[#*_`>\[\]]", "", final)[:180], f"./?c={chat['id']}")
    except Exception as exc:
        stream_done.set()
        with BUSY_LOCK:
            BUSY_SLOTS.pop(chat["id"], None)
        turn_end(reply_key)
        with push_lock:
            pass
        with THINK_LOCK:
            THINK_REPLY.pop(slot, None)
        err = scrub(exc)[:500]
        sb.rest("PATCH", "messages", params={"id": f"eq.{reply['id']}"},
                body={"content": scrub(text), "thinking": scrub(thinking), "status": "error",
                      "meta": {**meta(), "error": err}})
        sb.rest("PATCH", "messages", params={"id": f"eq.{msg['id']}"}, body={"status": "error"})
        log("message failed:", err)


def approve_background(gw: Gateway, sb: Supabase):
    """Cron/subagent approvals have no chat to show a card in; follow the user's setting."""
    try:
        pending = gw.call("GET", "/api/approvals")
    except Exception:
        return
    trust = user_settings(sb, OWNER).get("background_trust", True)
    for a in pending if isinstance(pending, list) else []:
        rid = a.get("id") or a.get("request_id")
        if rid:
            try:
                gw.call("POST", f"/api/approvals/{rid}/{'approve' if trust else 'reject'}")
                log(f"background approval {'approved' if trust else 'rejected'}")
            except Exception as exc:
                log("background approval failed:", exc)


BUSY_CHATS: set = set()
BUSY_LOCK = threading.Lock()
BUSY_SLOTS: dict = {}  # chat id -> {"slot", "since"} while that chat's turn is streaming (for steering)

# ── follow-ups: answers Crew writes into a slot with no Panda message behind them ──
# A chat that starts helpers (spawn_run) ends its turn right away. When they finish, Kiro Crew
# injects a "[Subagent completion event]" into the same slot and Panda answers it there, but no
# Panda message asked for it, so nothing carried that answer to the app. The watcher below keeps
# each such slot's history count; new rows written while the chat is idle in Panda become a new
# assistant message in the chat (streaming placeholder while Crew is still on it).
USED_HELPERS = re.compile(r"spawn|sub-?agent|helper|workflow_run", re.I)
FOLLOW: dict = {}        # chat id -> {"slot", "owner", "seen", "until", "row", "before"}
FOLLOW_LOCK = threading.Lock()
FOLLOW_HOURS = 4


def _slot_view(gw: Gateway, slot: str, limit: int = 1) -> dict:
    d = gw.call("GET", f"/api/chat/slots/{slot}?limit={max(1, min(limit, 60))}", timeout=20)
    return d if isinstance(d, dict) else {}


def watch_followups(sb: Supabase, gw: Gateway, chat: dict, slot: str):
    """Start (or re-arm) watching a chat's slot from its current history length."""
    try:
        total = int(_slot_view(gw, slot).get("total") or 0)
    except Exception as exc:
        log("follow-up watch:", type(exc).__name__)
        return
    with FOLLOW_LOCK:
        old = FOLLOW.get(chat["id"]) or {}
        FOLLOW[chat["id"]] = {"slot": slot, "owner": chat["owner"], "seen": total, "row": None, "before": None,
                              "until": time.time() + FOLLOW_HOURS * 3600}
    if old.get("row"):  # a follow-up placeholder the new turn overtook: its text is in that turn now
        _safe(lambda: sb.rest("DELETE", "messages", params={"id": f"eq.{old['row']}"}))


def followup_step(sb: Supabase, gw: Gateway):
    with FOLLOW_LOCK:
        items = list(FOLLOW.items())
    with BUSY_LOCK:
        busy = set(BUSY_SLOTS)
    for chat_id, w in items:
        if time.time() > w["until"] and not w["row"]:
            with FOLLOW_LOCK:
                FOLLOW.pop(chat_id, None)
            continue
        if chat_id in busy or chat_id in BUSY_CHATS:
            continue  # a Panda turn is running; its end re-arms the count
        try:
            view = _slot_view(gw, w["slot"])
        except Exception:
            continue
        total, running = int(view.get("total") or 0), bool(view.get("running"))
        if total < w["seen"]:  # the slot was cleared or recreated
            w["seen"] = total
            continue
        if total == w["seen"] and not (running and not w["row"]):
            continue
        chat = {"id": chat_id, "owner": w["owner"]}
        if not w["row"]:
            w["before"] = scan_outputs()
            w["row"] = sb.rest("POST", "messages", prefer="return=representation", body={
                "chat_id": chat_id, "owner": w["owner"], "role": "assistant", "status": "streaming",
                "content": "", "meta": {"followup": True}})[0]["id"]
            log(f"chat {chat_id}: Panda is answering on its own (helpers finished)")
        if running:
            try:  # "Stop" on Panda's own follow-up answer
                row = sb.rest("GET", "messages", params={"id": f"eq.{w['row']}", "select": "cancel"})
                if row and row[0].get("cancel") and not w.get("stopped"):
                    w["stopped"] = True
                    gw.call("POST", f"/api/chat/slots/{w['slot']}/stop")
            except Exception:
                pass
            continue  # fill it in when the turn is over
        try:
            rows = (_slot_view(gw, w["slot"], total - w["seen"]).get("messages") or [])[-(total - w["seen"]):]
        except Exception:
            continue
        text = "\n\n".join(t for t in (_row_text(m) for m in rows if isinstance(m, dict)
                                       and m.get("role") in ("assistant", "agent", "bot")
                                       and (m.get("meta") or {}).get("kind") not in ("compaction", "session_reload"))
                if t.strip() and not is_compaction_notice(t))
        w["seen"] = total
        if not text.strip():
            sb.rest("DELETE", "messages", params={"id": f"eq.{w['row']}"})
        else:
            published = publish_outputs(sb, chat, w["row"], w["before"] or {}, text)
            text = scrub(link_files(text, published))
            sb.rest("PATCH", "messages", params={"id": f"eq.{w['row']}"}, body={
                "content": text, "status": "done",
                "meta": {"followup": True, "files": [r["id"] for _, r, inline in published if not inline]}})
            push(sb, w["owner"], "Panda finished with the helpers", re.sub(r"[#*_`>\[\]]", "", text)[:180],
                 f"./?c={chat_id}")
        w["row"], w["before"], w["stopped"] = None, None, False


def followup_loop(sb: Supabase, gw: Gateway, stop: threading.Event):
    while not stop.wait(6):
        try:
            followup_step(sb, gw)
        except Exception as exc:
            log("follow-up check:", type(exc).__name__)


def steer_pending(sb: Supabase, gw: Gateway):
    """Messages sent with "steer" while their chat is answering go INTO the running turn.

    Kiro Crew injects them mid-turn (POST /api/chat with steer=true answers JSON instead of a new
    stream). When the turn cannot take a steer, Crew queues it and the running stream answers it
    next, so either way the reply keeps flowing into the assistant row already on screen. A steer
    for a chat that is no longer busy is left alone: claim_next runs it as an ordinary turn.
    """
    with BUSY_LOCK:
        live = {c: dict(v) for c, v in BUSY_SLOTS.items() if time.time() - v["since"] > 1.5}
    if not live:
        return
    rows = sb.rest("GET", "messages", params={"status": "eq.pending", "role": "eq.user", "owner": f"eq.{OWNER}",
                                              "meta->>steer": "eq.true", "order": "id.asc", "limit": "10",
                                              "select": "id,chat_id,content,meta"})
    for r in rows or []:
        info = live.get(r["chat_id"])
        if not info or not (r.get("content") or "").strip():
            continue
        got = sb.rest("PATCH", "messages", prefer="return=representation",
                      params={"id": f"eq.{r['id']}", "status": "eq.pending"}, body={"status": "processing"})
        if not got:
            continue
        meta = dict(r.get("meta") or {})
        try:
            res = gw.call("POST", "/api/chat", {"message": r["content"], "slot": info["slot"], "steer": True}, timeout=30)
            meta["steered"] = "steered" if res.get("steered") else "queued"
            sb.rest("PATCH", "messages", params={"id": f"eq.{r['id']}"}, body={"status": "done", "meta": meta})
            log(f"chat {r['chat_id']}: message {r['id']} {meta['steered']} into the running turn")
        except Exception as exc:
            # Not delivered: run it as a normal turn once the current one ends.
            meta.pop("steer", None)
            sb.rest("PATCH", "messages", params={"id": f"eq.{r['id']}"}, body={"status": "pending", "meta": meta})
            log("steer failed, will run next:", type(exc).__name__)


def claim_next(sb: Supabase):
    """Oldest pending message whose chat is not already answering (chats run in parallel).

    Only the person who pressed Start is served: another account's message is never picked up,
    even though the worker account can read every row.
    """
    rows = sb.rest("GET", "messages", params={"status": "eq.pending", "role": "eq.user", "owner": f"eq.{OWNER}",
                                               "order": "id.asc", "limit": "20", "select": "id,chat_id,meta"})
    for r in rows or []:
        with BUSY_LOCK:
            if r["chat_id"] in BUSY_CHATS or len(BUSY_CHATS) >= MAX_PARALLEL_CHATS:
                continue
        body = {"status": "processing"}
        if (r.get("meta") or {}).get("steer"):  # its turn ended before the steer: run it as a normal turn
            body["meta"] = {k: v for k, v in r["meta"].items() if k != "steer"}
        got = sb.rest("PATCH", "messages", prefer="return=representation",
                      params={"id": f"eq.{r['id']}", "status": "eq.pending"}, body=body)
        if got:
            with BUSY_LOCK:
                BUSY_CHATS.add(r["chat_id"])
            return got[0]
    return None


def claim_loop(sb: Supabase, gw: Gateway, pool: ThreadPoolExecutor, stop: threading.Event):
    """Pick up steers and new messages every POLL_SECS, independent of the main loop's chores."""
    while not stop.is_set():
        try:
            steer_pending(sb, gw)
        except Exception as exc:
            log("steer check:", type(exc).__name__)
        try:
            msg = claim_next(sb)
        except Exception as exc:  # a network blip: try again next poll, never take Panda down
            log("claim:", type(exc).__name__)
            msg = None
        if not msg:
            stop.wait(POLL_SECS)
            continue
        try:
            if stop.is_set():
                raise RuntimeError("stopping")
            pool.submit(run_message, sb, gw, msg)
        except RuntimeError:  # stopping: hand the message back so the next run answers it
            with BUSY_LOCK:
                BUSY_CHATS.discard(msg["chat_id"])
            with contextlib.suppress(Exception):
                sb.rest("PATCH", "messages", params={"id": f"eq.{msg['id']}"}, body={"status": "pending"})
            return


def run_message(sb: Supabase, gw: Gateway, msg: dict):
    dev = False
    try:
        chat = sb.rest("GET", "chats", params={"id": f"eq.{msg['chat_id']}", "select": "id,owner"})[0]
        dev = is_developer_chat(user_settings(sb, chat["owner"]), chat)
    except Exception:
        pass
    if dev:  # developer env variables exist on disk only while the Developer chat is answering
        refresh_dev_latest(sb)
        dev_begin()
    try:
        handle(sb, gw, msg)
    except Exception as exc:  # handle() records its own errors; this guards the thread
        log("turn crashed:", exc)
    finally:
        if dev:
            dev_end()
        with BUSY_LOCK:
            BUSY_CHATS.discard(msg["chat_id"])


# ── Kiro Crew settings the app may change ────────────────────────────────────
# Crew's own PATCH /api/config/kirocrew allow-list decides what is editable at all. On top of it,
# these stay locked from the phone: they move a security boundary, undo the worker's own setup, or
# only matter in Crew's desktop dashboard. The app shows the reason next to the setting.
LOCKED_SETTINGS = {
    "agent.provider": "the worker depends on this agent runtime",
    "agent.acp_backend": "the worker depends on this agent runtime",
    "agent.member_acp_backend": "the worker depends on this agent runtime",
    "agent.kas_bundle": "the worker depends on this agent runtime",
    "agent.approval_mode": "tool approvals follow Panda's own setting (Settings → Chat & tools)",
    "agent.yolo_duration": "auto-approve windows are a security setting",
    "agent.sandbox": "the sandbox is a security boundary",
    "agent.sandbox_allow_no_isolation": "the sandbox is a security boundary",
    "agent.sandbox_allow_unsandboxed_exec": "the worker sets this itself on Kaggle",
    "agent.dangerously_skip_permissions": "it would skip every approval",
    "agent.jail": "the sandbox is a security boundary",
    "auto_update": "the worker installs Kiro Crew fresh on every start",
    "session.pool_agent": "the worker manages Kiro Crew's agents",
    "skills.approval_required": "approving new skills is a security setting",
    "skills.extra_paths": "it would load skills from other folders",
    "memory.writes_enabled": "Panda sets it from Settings → Memory every time it starts",
}
LOCKED_PREFIXES = {
    "dashboard.tailscale.": "it changes who can reach the gateway",
    "dashboard.terminal.": "the terminal is part of the desktop dashboard",
    "instances.": "it manages other machines over SSH",
    "telemetry.": "telemetry stays as the worker sets it",
    "decisions.": "it can send data to another service",
    "computer_use.": "computer use needs a desktop",
    "tunnel.": "it changes who can reach the gateway",
    "mcp_gateway.": "the worker manages the tool gateway",
}
DESKTOP_ONLY = {"dashboard.sso_login_flags", "dashboard.prevent_sleep", "dashboard.update_nudge",
                "dashboard.folder_sort", "dashboard.session_sort", "dashboard.recent_tint_count",
                "dashboard.mcp_probe_timeout_secs", "dashboard.crewmate_threads", "dashboard.dynamic_dashboard_cards"}
# Dashboard settings that change the agent itself (written with PUT /api/dashboard/config).
DASH_KEYS = {"verbosity", "default_memory_mode", "merge_queued_messages"}
_APP_MODEL_PATH = re.compile(r"^apps\.[a-z0-9]+(?:-[a-z0-9]+)*\.model$")
_EFFORTS = ["", "low", "medium", "high", "xhigh", "max"]
# Used when the installed Kiro Crew can't be asked for its own list (taken from Crew 0.11).
FALLBACK_EDITABLE = {
    "agent.model": {"type": "str", "max_len": 64}, "agent.role_models.background": {"type": "str", "max_len": 64},
    "agent.role_models.subagent": {"type": "str", "max_len": 64}, "agent.fallback_model": {"type": "str", "max_len": 64},
    "agent.refusal_fallback_model": {"type": "str", "max_len": 64},
    "agent.reasoning_effort": {"type": "enum", "values": _EFFORTS},
    "agent.role_efforts.background": {"type": "enum", "values": _EFFORTS},
    "agent.role_efforts.subagent": {"type": "enum", "values": _EFFORTS},
    "agent.tool_search": {"type": "bool"}, "agent.completion_keep": {"type": "enum", "values": ["head", "tail", "both"]},
    "agent.completion_keep_chars": {"type": "int", "min": 0, "max": 512000},
    "agent.soft_stop_budget_secs": {"type": "float", "min": 0.5, "max": 60.0},
    "agent.chat_turn_timeout_secs": {"type": "int", "min": 300, "max": 86400},
    "session.timeout_secs": {"type": "int", "min": 0, "max": 86400},
    "session.autocompact_pct": {"type": "float", "min": 5.0, "max": 90.0},
    "session.compact_wait_secs": {"type": "float", "min": 0.0, "max": 3600.0},
    "session.pool_size": {"type": "int", "min": 0, "max": 10}, "session.pool_ttl_secs": {"type": "int", "min": 0, "max": 7200},
    "session_summary.enabled": {"type": "bool"}, "monitoring.prefer_structured_arming": {"type": "bool"},
    "dashboard.title_refresh_every_turns": {"type": "int", "min": 0, "max": 1000},
    "dashboard.user_role": {"type": "enum", "values": ["", "developer", "designer", "product-manager", "data-ml",
                                                       "it-ops", "other"]},
    "dashboard.user_role_other": {"type": "str", "max_len": 60},
    "dashboard.user_technical_level": {"type": "enum", "values": ["", "codes", "somewhat-technical", "non-technical"]},
    "skills.auto_create_from_sessions": {"type": "bool"}, "knowledge.auto_add_documents": {"type": "bool"},
    "knowledge.auto_ingest_artifacts": {"type": "bool"}, "knowledge.extraction_model": {"type": "str"},
    "knowledge.extraction_effort": {"type": "enum", "values": _EFFORTS},
}
_SPEC_DUMP = r'''
import json
from kiro_crew.dashboard.handlers.core_config_schema import _EDITABLE_CONFIG
out = {}
for k, s in _EDITABLE_CONFIG.items():
    d = {"type": s.get("type")}
    for f in ("min", "max", "max_len"):
        if isinstance(s.get(f), (int, float)) and not isinstance(s.get(f), bool):
            d[f] = s[f]
    vals = s.get("values")
    if vals is None and callable(s.get("values_fn")):
        try:
            vals = list(s["values_fn"]())
        except Exception:
            vals = None
    if vals is not None:
        d["values"] = [v for v in vals if isinstance(v, (str, int, float, bool))]
    out[k] = d
print("PANDA_SPEC " + json.dumps(out))
'''
_SPEC = {"spec": None, "live": False}
_MISSING = object()


def locked_reason(path: str) -> str:
    if path in LOCKED_SETTINGS:
        return LOCKED_SETTINGS[path]
    for prefix, why in LOCKED_PREFIXES.items():
        if path.startswith(prefix):
            return why
    if path in DESKTOP_ONLY:
        return "it only affects Kiro Crew's desktop dashboard"
    return ""


def dig(d, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _crew_pythons() -> list:
    """Interpreters that may have kiro_crew installed (pipx venv, managed venv, the launcher's shebang)."""
    cands = []
    exe = shutil.which("kirocrew", path=ENV.get("PATH"))
    if exe:
        try:
            first = open(exe, "rb").readline().decode(errors="replace").strip()
        except OSError:
            first = ""
        if first.startswith("#!"):
            parts = first[2:].split()
            if parts and os.path.basename(parts[0]) == "env":
                parts = parts[1:]
            if parts and "python" in os.path.basename(parts[0]):
                cands.append(parts[0])
        cands += glob.glob(os.path.join(os.path.dirname(os.path.realpath(exe)), "python*"))
    for pat in ("~/.kiro/crew-venv/bin/python3", "~/.local/pipx/venvs/kirocrew/bin/python3",
                "~/.local/share/pipx/venvs/kirocrew/bin/python3"):
        cands += glob.glob(os.path.expanduser(pat))
    seen, out = set(), []
    for c in cands:
        if c not in seen and os.path.exists(c):
            seen.add(c)
            out.append(c)
    return out


def editable_spec() -> dict:
    """Crew's own list of editable settings (with bounds), read from the installed package."""
    if _SPEC["spec"] is not None:
        return _SPEC["spec"]
    for py in _crew_pythons():
        try:
            r = subprocess.run([py, "-c", _SPEC_DUMP], env=ENV, capture_output=True, text=True, timeout=90)
            line = next((ln for ln in (r.stdout or "").splitlines() if ln.startswith("PANDA_SPEC ")), "")
            if line:
                _SPEC.update(spec=json.loads(line[11:]), live=True)
                return _SPEC["spec"]
        except Exception as exc:
            log("settings spec read failed:", type(exc).__name__)
    _SPEC.update(spec=dict(FALLBACK_EDITABLE), live=False)
    return _SPEC["spec"]


def crew_settings(gw: Gateway) -> dict:
    """Every Kiro Crew setting (schema + current value), marked with how the app may change it."""
    schema = gw.call("GET", "/api/config/schema", timeout=90)
    entries = schema.get("entries") if isinstance(schema, dict) else schema
    cfg = gw.call("GET", "/api/config/kirocrew", timeout=60)
    spec = editable_spec()
    items = []
    for e in entries or []:
        if not isinstance(e, dict) or e.get("hasChildren") or e.get("deprecated") or not e.get("path"):
            continue
        path = str(e["path"])
        it = {"p": path, "t": e.get("type") or "", "l": str(e.get("label") or path)[:120]}
        h = str(e.get("help") or "").strip()
        if h:
            it["h"] = h if len(h) <= 320 else h[:317].rstrip() + "…"
        if e.get("enumValues"):
            it["e"] = list(e["enumValues"])[:60]
        if e.get("defaultValue") is not None and not e.get("sensitive"):
            it["d"] = e["defaultValue"]
        val = dig(cfg, path)
        if val is not _MISSING:
            it["v"] = val
        if e.get("requiresRestart"):
            it["r"] = 1
        if e.get("sensitive"):
            it["s"] = 1
        why = locked_reason(path)
        dash = path.startswith("dashboard.") and path[10:] in DASH_KEYS
        if why:
            it["k"] = why
        elif path in spec or _APP_MODEL_PATH.match(path):
            it["w"] = "patch"
            it["x"] = spec.get(path) or {"type": "str", "max_len": 64}
        elif dash:
            it["w"] = "dash"
        items.append(it)
    backend = dig(cfg, "agent.acp_backend")
    return {"items": items, "version": CREW_VERSION["v"], "backend": backend if isinstance(backend, str) else "",
            "spec": "live" if _SPEC["live"] else "builtin", "at": now_iso()}


def crew_set(gw: Gateway, path: str, value) -> dict:
    """Change one Kiro Crew setting the way Crew's own Settings page does."""
    why = locked_reason(path)
    if why:
        raise ValueError(f"This setting can't be changed from Panda: {why}.")
    if path.startswith("dashboard.") and path[10:] in DASH_KEYS:
        gw.call("PUT", "/api/dashboard/config", {path[10:]: value})
        return {"ok": True, "path": path, "value": value}
    r = gw.call("PATCH", "/api/config/kirocrew", {"path": path, "value": value})
    stored = dig(r, path) if isinstance(r, dict) else _MISSING
    out = {"ok": True, "path": path, "value": value if stored is _MISSING else stored}
    if isinstance(r, dict) and r.get("clamp_notice"):
        out["clamp"] = r["clamp_notice"]
    return out


def apply_overrides(gw: Gateway, overrides) -> None:
    """Settings changed in the app are kept in Supabase too, so they survive a lost snapshot."""
    if not isinstance(overrides, dict):
        return
    done = 0
    for path, value in overrides.items():
        try:
            crew_set(gw, str(path), value)
            done += 1
        except Exception as exc:
            log(f"setting {path} not re-applied:", str(exc)[:120])
    if done:
        log(f"re-applied {done} Kiro Crew setting(s) from the app")


def crew_commands(gw: Gateway) -> dict:
    cmds = []
    try:
        data = gw.call("GET", "/api/slash-commands")
        cmds = [{"name": str(c["name"]), "description": str(c.get("description") or "")}
                for c in (data if isinstance(data, list) else []) if isinstance(c, dict) and c.get("name")]
    except Exception as exc:
        log("slash commands read failed:", exc)
    backend = ""
    with contextlib.suppress(Exception):
        b = dig(gw.call("GET", "/api/config/kirocrew"), "agent.acp_backend")
        backend = b if isinstance(b, str) else ""
    return {"commands": cmds, "backend": backend, "version": CREW_VERSION["v"]}


# ── credit usage ─────────────────────────────────────────────────────────────
SESSION_INFO: dict = {}      # worker_sessions.info of this run; always written whole
USAGE_STATE = {"at": 0.0, "poke": False, "sid": ""}


def _reason(exc) -> str:
    return scrub(exc)[:200]


KIRO_USAGE_HOSTS = ("https://codewhisperer.us-east-1.amazonaws.com", "https://q.eu-central-1.amazonaws.com")


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def kiro_plan_by_api_key() -> dict:
    """The Kiro plan total, read with Panda's own KIRO_API_KEY.

    Kiro Crew skips this under API-key sign-in (reason "api_key_auth"), but Kiro's
    GetUsageLimits accepts the key when the request says it is one (TokenType: API_KEY).
    Returns {} when it can't be read, so the caller keeps whatever Crew said.
    """
    key = CFG.get("kiro_api_key") or ""
    if not key:
        return {}
    headers = {"Authorization": f"Bearer {key}", "TokenType": "API_KEY",
               "Content-Type": "application/x-amz-json-1.0",
               "X-Amz-Target": "AmazonCodeWhispererService.GetUsageLimits",
               "User-Agent": "AmazonQ-For-CLI/1.24.0 ua/2.0 os/linux lang/rust Panda"}
    data = None
    for host in KIRO_USAGE_HOSTS:
        try:
            r = requests.post(host, json={"origin": "AI_EDITOR", "resourceType": "AGENTIC_REQUEST"},
                              headers=headers, timeout=20, allow_redirects=False)
        except requests.RequestException as exc:
            log("kiro plan read failed:", type(exc).__name__)
            continue
        if r.status_code == 200:
            data = r.json()
            break
        log("kiro plan read:", host.split("//")[1].split(".")[0], r.status_code)
    if not isinstance(data, dict):
        return {}
    rows = [b for b in data.get("usageBreakdownList") or [] if isinstance(b, dict)]
    b = next((x for x in rows if x.get("resourceType") == "CREDIT"), rows[0] if len(rows) == 1 else None)
    if not b:
        return {}
    used = _num(b.get("currentUsageWithPrecision")) or _num(b.get("currentUsage")) or 0.0
    limit = _num(b.get("usageLimitWithPrecision")) or _num(b.get("usageLimit"))
    if not limit:
        return {}
    out: dict = {"available": True, "source": "kiro_api_key", "credits_used": round(used, 2),
                 "credits_plan": round(limit, 2), "percentage": round(used / limit * 100, 1)}
    over = _num(b.get("currentOveragesWithPrecision")) or _num(b.get("currentOverages"))
    if over:
        out["credits_overage"] = round(over, 2)
    if _num(b.get("overageRate")):
        out["overage_rate"] = b["overageRate"]
    title = (data.get("subscriptionInfo") or {}).get("subscriptionTitle")
    if isinstance(title, str) and title:
        out["plan"] = title.title()  # "KIRO PRO+" -> "Kiro Pro+"
    reset = _num(b.get("nextDateReset")) or _num(data.get("nextDateReset"))
    if reset:
        out["resets"] = datetime.fromtimestamp(reset, timezone.utc).strftime("%Y-%m-%d")
    bonus = []
    for x in b.get("bonuses") or []:
        if not isinstance(x, dict) or not _num(x.get("usageLimit")):
            continue
        item = {"name": str(x.get("displayName") or "Bonus credits")[:60],
                "used": round(_num(x.get("currentUsage")) or 0, 2), "total": round(x["usageLimit"], 2)}
        exp = _num(x.get("expiresAt"))
        if exp:
            item["days_left"] = max(0, int((exp - time.time()) // 86400))
        bonus.append(item)
    ft = b.get("freeTrialInfo") or {}
    if isinstance(ft, dict) and ft.get("freeTrialStatus") == "ACTIVE" and _num(ft.get("usageLimit")):
        item = {"name": "Free trial", "used": round(_num(ft.get("currentUsage")) or 0, 2),
                "total": round(ft["usageLimit"], 2)}
        if _num(ft.get("freeTrialExpiry")):
            item["days_left"] = max(0, int((ft["freeTrialExpiry"] - time.time()) // 86400))
        bonus.append(item)
    if bonus:
        out["bonus_credits"] = bonus
    return out


def crew_spend_split() -> dict:
    """This month's gateway credits: chat replies vs background work (titles, memory, other).

    Read from Kiro Crew's per-turn records (~/.kiro/crew/usage/tokens/*.jsonl). These live on this
    machine only, so they start fresh at each Start; `since` says from when they count.
    """
    month = time.strftime("%Y-%m")
    out = {"replies": 0.0, "background": 0.0, "titles": 0.0, "memory": 0.0, "other": 0.0, "since": None}
    for path in sorted(glob.glob(f"{HOME}/.kiro/crew/usage/tokens/{month}-*.jsonl")):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict) or r.get("_type") != "tokens":
                    continue
                try:
                    c = float(r.get("credits") or 0)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(c):
                    continue
                ts = str(r.get("ts") or "")
                if ts and (out["since"] is None or ts < out["since"]):
                    out["since"] = ts
                slot, surface = str(r.get("slot") or ""), str(r.get("surface") or "")
                kind = surface.split(":", 1)[0]
                if slot.startswith("_") or kind in ("bg", "cron", "subagent", "spawn", "workflow", "task", "heartbeat"):
                    out["background"] += c
                    part = "titles" if surface == "bg:bg_oneliner" else "memory" if surface == "bg:consolidation" else "other"
                    out[part] += c
                else:
                    out["replies"] += c
    for k in ("replies", "background", "titles", "memory", "other"):
        out[k] = round(out[k], 2)
    return out


def refresh_usage(sb: Supabase, gw: Gateway, force: bool = False) -> dict | None:
    """Kiro account credits (used / plan / reset date) and this gateway's credits per day.

    Stored in worker_sessions.info.usage, so the app can show the last known numbers even when
    Panda is offline. Refreshed every 5 minutes, a minute after a reply, or when the app asks.
    """
    now = time.time()
    gap = 60 if USAGE_STATE["poke"] else 300
    if not force and now - USAGE_STATE["at"] < gap:
        return None
    USAGE_STATE.update(at=now, poke=False)
    account: dict = {}
    try:
        d = gw.call("GET", "/api/sessions/usage", timeout=30)
        account = (d.get("usage") if isinstance(d, dict) else None) or {}
        if not account:
            USAGE_STATE["at"] = now - 240  # Crew fills its cache in the background: look again in a minute
    except Exception as exc:
        account = {"available": False, "reason": _reason(exc)}
    daily: list = []
    try:
        k = gw.call("GET", "/api/usage/kiro", timeout=45)
        for row in ((k.get("sessions") or {}).get("daily_history") or [])[-35:]:
            if isinstance(row, dict) and row.get("date"):
                daily.append({"date": str(row["date"]), "credits": round(float(row.get("credits") or 0), 4),
                              "messages": int(row.get("messages") or 0)})
        billing = k.get("billing") or {}
        if billing and not account.get("credits_plan"):
            account = {**account, **billing}
    except Exception:
        pass
    if not account.get("credits_plan"):
        # Crew can't read the plan under API-key sign-in ("api_key_auth"): read it with the key.
        try:
            own = kiro_plan_by_api_key()
        except Exception as exc:
            own = {}
            log("kiro plan read failed:", _reason(exc))
        if own:
            account = own
    usage = {"account": account, "daily": daily, "at": now_iso()}
    with contextlib.suppress(Exception):
        usage["split"] = crew_spend_split()
    SESSION_INFO["usage"] = usage
    if USAGE_STATE["sid"]:
        try:
            sb.rest("PATCH", "worker_sessions", params={"id": f"eq.{USAGE_STATE['sid']}"}, body={"info": SESSION_INFO})
        except Exception as exc:
            log("usage save failed:", type(exc).__name__)
    return usage


def usage_now(sb: Supabase, gw: Gateway, refresh: bool) -> dict:
    if refresh:
        with contextlib.suppress(Exception):  # 409 = Crew is already refreshing
            gw.call("POST", "/api/sessions/usage/refresh", {}, timeout=90)
    return refresh_usage(sb, gw, force=True) or SESSION_INFO.get("usage") or {}


# ── side chat: a quick question that doesn't interrupt the running answer ────
def side_ask(sb: Supabase, gw: Gateway, chat_id: str, question: str, timeout: float = 180) -> dict:
    rows = sb.rest("GET", "chats", params={"id": f"eq.{chat_id}", "owner": f"eq.{OWNER}", "select": "*"})
    if not rows:
        raise ValueError("chat not found")
    question = question.strip()[:4000]
    if not question:
        raise ValueError("ask a question after /btw")
    slot, _ = ensure_slot(sb, gw, rows[0], user_settings(sb, OWNER))
    q = quote(slot, safe="")  # slot can be an old URL-derived key; keep the path well-formed
    gw.call("POST", f"/api/chat/slots/{q}/side/open", {})
    t0 = time.time()
    with SIDE_LOCK:
        known = set(SIDE)
    r = gw.call("POST", f"/api/chat/slots/{q}/side/turn", {"question": question})
    run_id = r.get("run_id") or ""
    # The answer streams over the dashboard websocket (chat.side_result); thinking_listener
    # collects it in SIDE. A queued question gets its run id only when it starts: take the first
    # new side run on this slot.
    while time.time() - t0 < timeout:
        time.sleep(0.8)
        with SIDE_LOCK:
            if not run_id:
                fresh = [k for k, v in SIDE.items() if k not in known and v.get("slot") == slot]
                run_id = fresh[0] if fresh else ""
            got = dict(SIDE.get(run_id) or {}) if run_id else {}
        if "final" in got:
            return {"answer": scrub(_ANSI.sub("", got["final"])), "error": got.get("error", False)}
    with SIDE_LOCK:
        partial = (SIDE.get(run_id) or {}).get("text", "") if run_id else ""
    if partial:
        return {"answer": scrub(_ANSI.sub("", partial)), "error": False}
    return {"answer": "", "error": True, "note": "No answer yet. Try again in a moment."}


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as exc:
        log(f"{getattr(fn, '__name__', 'task')} failed:", _reason(exc))
        return None


# ── terminal: a persistent shell on the worker the owner can type into ───────
# Owner-only (run_command checks the row's owner). It is the owner's own Kaggle machine, the same
# shell the agent already uses; every line is scrubbed of known secrets. One shell, kept across
# commands so cwd and variables persist. A command that runs past its timeout resets the shell.
class Terminal:
    MARK = "\x1e__panda_end_"

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.buf = bytearray()
        self.buf_lock = threading.Lock()
        self.cwd = os.path.expanduser("~")

    def _spawn(self):
        env = dict(ENV, PS1="", PS2="", TERM="dumb", PAGER="cat", GIT_PAGER="cat", DEBIAN_FRONTEND="noninteractive",
                   HOME=HOME)
        self.proc = subprocess.Popen(["bash", "--noprofile", "--norc"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     cwd=os.path.expanduser("~"), env=env, bufsize=0)
        self.cwd = os.path.expanduser("~")
        with self.buf_lock:
            self.buf = bytearray()
        threading.Thread(target=self._drain, args=(self.proc,), daemon=True).start()

    def _drain(self, proc):
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                with self.buf_lock:
                    self.buf.extend(chunk)
        except Exception:
            pass

    def run(self, command: str, timeout: int = 60) -> dict:
        command = str(command)
        if len(command) > 20000:
            raise ValueError("command too long")
        with self.lock:
            if not (self.proc and self.proc.poll() is None):
                self._spawn()
            mark = f"{self.MARK}{uuid.uuid4().hex}"
            mb = mark.encode()
            script = f"{command}\nprintf '%s%s:%s\\n' {shlex.quote(mark)} \"$?\" \"$PWD\"\n"
            try:
                self.proc.stdin.write(script.encode())
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                self._spawn()
                return {"output": "(the terminal was reset)", "rc": None, "cwd": self.cwd, "reset": True}
            deadline = time.time() + timeout
            while time.time() < deadline:
                with self.buf_lock:
                    data = bytes(self.buf)
                idx = data.find(mb)
                if idx != -1:
                    nl = data.find(b"\n", idx)
                    if nl != -1:
                        tail = data[idx + len(mb):nl].decode(errors="replace")
                        rc, _, cwd = tail.partition(":")
                        self.cwd = cwd or self.cwd
                        with self.buf_lock:
                            del self.buf[:]
                        return {"output": scrub(data[:idx].decode(errors="replace"))[-40000:],
                                "rc": int(rc) if rc.lstrip("-").isdigit() else None, "cwd": self.cwd}
                if self.proc.poll() is not None:
                    return {"output": scrub(data.decode(errors="replace"))[-40000:], "rc": None, "cwd": self.cwd, "reset": True}
                time.sleep(0.1)
            with self.buf_lock:
                data = bytes(self.buf)
            with contextlib.suppress(Exception):
                self.proc.kill()
            self._spawn()
            return {"output": scrub(data.decode(errors="replace"))[-40000:] + f"\n⏱ stopped after {timeout}s — the terminal was reset.",
                    "rc": None, "cwd": self.cwd, "timeout": True}


TERMINAL = Terminal()


def terminal_run(args: dict) -> dict:
    out = TERMINAL.run(args.get("input", ""), timeout=max(2, min(120, int(args.get("timeout") or 60))))
    out["home"] = HOME
    return out


# ── disk browser: read-only look at the worker's files (like Kiro Crew's) ────
# Everything stays under the home folder; files that may hold secrets can be listed but not opened,
# and all output is scrubbed.
_FS_DENY = re.compile(r"(\.env|secrets\.env|\.enc|\.key|\.pem|id_rsa|drive\.json|browser\.json|\.sqlite\d?|\.db)$", re.I)
_FS_HIDE_DIRS = {".git", "node_modules", "__pycache__", ".cache"}


def _fs_safe(path) -> str:
    p = os.path.abspath(os.path.expanduser(str(path or HOME)))
    if p != HOME and not p.startswith(HOME + os.sep):
        raise ValueError("outside the home folder")
    return p


def _fs_rel(p: str) -> str:
    rel = os.path.relpath(p, HOME)
    return "~" if rel == "." else "~/" + rel


def fs_list(path) -> dict:
    p = _fs_safe(path)
    if not os.path.isdir(p):
        raise ValueError("not a folder")
    entries = []
    for name in os.listdir(p):
        full = os.path.join(p, name)
        try:
            st = os.lstat(full)
        except OSError:
            continue
        is_dir = os.path.isdir(full)
        entries.append({"name": name, "dir": is_dir, "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime,
                        "sensitive": bool(_FS_DENY.search(name))})
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    quick = [d for d in ("outputs", "work", "projects", "uploads", ".kiro/crew") if os.path.isdir(os.path.join(HOME, d))]
    return {"path": p, "rel": _fs_rel(p), "home": HOME,
            "parent": os.path.dirname(p) if (p != HOME and p.startswith(HOME + os.sep)) else None,
            "quick": quick, "entries": entries[:2000]}


def fs_read(path) -> dict:
    p = _fs_safe(path)
    if not os.path.isfile(p):
        raise ValueError("not a file")
    base = {"path": p, "rel": _fs_rel(p), "name": os.path.basename(p)}
    if _FS_DENY.search(os.path.basename(p)):
        return {**base, "denied": "This file may hold secrets, so it can't be opened here."}
    size = os.path.getsize(p)
    base["size"] = size
    if size > 512 * 1024:
        return {**base, "too_big": True}
    with open(p, "rb") as f:
        raw = f.read()
    try:
        return {**base, "text": scrub(raw.decode("utf-8"))}
    except UnicodeDecodeError:
        return {**base, "binary": True}


# ── commands relay: the app talks to Crew through here ───────────────────────
# A security boundary. The app may only reach these gateway routes. Reads are broad; writes are
# limited to what the app's pages use, and setting changes go through the rules above.
_RELAY_READ = re.compile(
    r"^/api/(skills|mcp|mcp-gateway|crons|lessons|models|effort-levels|notifications|memory|knowledge|"
    r"artifacts|agents|workspaces|spawn|subagents|tasks|config|status|system|slash-commands|sessions/usage|"
    r"usage|acp-backends|dashboard/config|workflows|prompts)(/|\?|$)")
_RELAY_WRITE = [
    ({"POST", "PUT", "PATCH", "DELETE"}, re.compile(r"^/api/(skills|mcp|mcp-gateway|crons)(/|\?|$)")),
    ({"POST"}, re.compile(r"^/api/notifications/ack$")),
    ({"POST"}, re.compile(r"^/api/sessions/usage/refresh$")),
    ({"PATCH"}, re.compile(r"^/api/config/kirocrew$")),
    ({"PUT"}, re.compile(r"^/api/dashboard/config$")),
]


def relay_check(method: str, path: str, body) -> None:
    """Raise unless the app may call this gateway route."""
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE") or ".." in path or "\\" in path or "//" in path:
        raise ValueError(f"not allowed: {method} {path}")
    if method == "GET":
        if not _RELAY_READ.match(path):
            raise ValueError(f"not allowed: {method} {path}")
        return
    if not any(method in methods and rx.match(path) for methods, rx in _RELAY_WRITE):
        raise ValueError(f"not allowed: {method} {path}")
    if path == "/api/config/kirocrew":
        target = str((body or {}).get("path", "")) if isinstance(body, dict) else ""
        why = locked_reason(target)
        if not target or why:
            raise ValueError(f"not allowed from the app: {target or 'no path'}" + (f" ({why})" if why else ""))
    if path == "/api/dashboard/config":
        extra = set(body or {}) - DASH_KEYS if isinstance(body, dict) else {"body"}
        if extra:
            raise ValueError("not allowed from the app: " + ", ".join(sorted(map(str, extra))))


def run_command(sb: Supabase, gw: Gateway, cmd: dict):
    op, args = cmd["op"], cmd.get("args") or {}
    try:
        if cmd.get("owner") != OWNER:
            raise PermissionError("this Panda is serving another account")
        if op == "gw":
            method = str(args.get("method", "GET")).upper()
            path = str(args.get("path", ""))
            relay_check(method, path, args.get("body"))
            result = gw.call(method, path, args.get("body"), timeout=120)
        elif op == "crew.commands":
            result = crew_commands(gw)
        elif op == "crew.settings":
            result = crew_settings(gw)
        elif op == "crew.set":
            result = crew_set(gw, str(args.get("path", "")), args.get("value"))
        elif op == "usage":
            result = usage_now(sb, gw, bool(args.get("refresh")))
        elif op == "side":
            result = side_ask(sb, gw, str(args.get("chat_id", "")), str(args.get("question", "")))
        elif op == "term":
            result = terminal_run(args)
        elif op == "fs.list":
            result = fs_list(args.get("path"))
        elif op == "fs.read":
            result = fs_read(args.get("path"))
        elif op == "secrets.sync":
            result = {"names": sync_secrets(sb)}
        elif op == "browser.forget":
            result = browser_forget(sb)
        elif op == "stt":
            result = speech_to_text(args)
        elif op in ("video.prompt", "video.estimate", "video.submit", "video.cancel", "video.delete", "video.auto",
                    "video.auto_cancel", "video.auto_dismiss", "video.usage"):
            result = video_command(op, args)
        elif op == "ping":
            result = {"ok": True, "uptime_secs": round(time.time() - T0), "crew": CREW_VERSION["v"]}
        else:
            raise ValueError(f"unknown op {op}")
        sb.rest("PATCH", "commands", params={"id": f"eq.{cmd['id']}"},
                body={"status": "done", "result": json.loads(scrub(json.dumps(result if result is not None else {}))),
                      **({"args": {}} if op == "stt" else {})})  # the voice clip is not kept
    except Exception as exc:
        sb.rest("PATCH", "commands", params={"id": f"eq.{cmd['id']}"},
                body={"status": "error", "result": {"error": scrub(exc)[:500]},
                      **({"args": {}} if op == "stt" else {})})


# Requests the app makes while Panda is offline time out in the app after a minute; running them
# hours later (on the next Start) would surprise the user, so they expire. These two are meant to
# wait for the next start, and are safe to run late.
RUN_LATE_OPS = ("browser.forget", "secrets.sync")
CMD_POOL = ThreadPoolExecutor(max_workers=4)


def command_loop(sb: Supabase, gw: Gateway, stop: threading.Event):
    last_expire = 0.0
    while not stop.is_set():
        if time.time() - last_expire > 30:
            last_expire = time.time()
            try:
                cutoff = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
                sb.rest("PATCH", "commands", params={
                    "status": "eq.pending", "owner": f"eq.{OWNER}", "created_at": f"lt.{cutoff}",
                    "op": "not.in.(" + ",".join(f'"{o}"' for o in RUN_LATE_OPS) + ")"},
                    body={"status": "error", "result": {"error": "expired: Panda was offline"}})
            except Exception as exc:
                log("command expiry:", str(exc)[:160])
        try:
            rows = sb.rest("GET", "commands", params={"status": "eq.pending", "owner": f"eq.{OWNER}",
                                                       "order": "id.asc", "limit": "8", "select": "*"}) or []
            for c in rows:
                got = sb.rest("PATCH", "commands", prefer="return=representation",
                              params={"id": f"eq.{c['id']}", "status": "eq.pending"}, body={"status": "running"})
                if got:
                    CMD_POOL.submit(run_command, sb, gw, got[0])
        except Exception as exc:
            log("command loop:", exc)
        stop.wait(1.0)


def mirror_notifications(sb: Supabase, gw: Gateway, seen: set):
    try:
        data = gw.call("GET", "/api/notifications")
    except Exception as exc:
        log("notifications read failed:", exc)
        return
    for n in data.get("notifications", []):
        ts = str(n.get("ts") or n.get("id") or "")
        if not ts or ts in seen or n.get("read"):
            continue
        seen.add(ts)
        # Restored snapshots bring old notifications back; Supabase decides what is new.
        if sb.rest("GET", "notifications", params={"owner": f"eq.{OWNER}", "meta->>ts": f"eq.{ts}",
                                                   "select": "id", "limit": "1"}):
            continue
        # Crew notification rows: kind, source, channel, priority, title, body, ts, job_id
        body = n.get("body") or n.get("text") or n.get("message") or n.get("title") or ""
        sb.rest("POST", "notifications", body={
            "owner": OWNER, "source": str(n.get("source") or n.get("kind") or "crew")[:40],
            "text": scrub(body)[:8000],
            "meta": {"ts": ts, "title": scrub(n.get("title") or "")[:300], "kind": n.get("kind"),
                     "priority": n.get("priority"), "job_id": n.get("job_id"),
                     # pending-skill rows: lets the app's Approve / Deny buttons name the candidate
                     "slug": str((n.get("meta") or {}).get("slug") or "")[:80] or None}})
        push(sb, OWNER, (n.get("title") or "Panda").replace("Cron: ", "⏰ ")[:60], body, "./?panel=notifications")
        try:
            gw.call("POST", "/api/notifications/ack", {"ts": ts})
        except Exception:
            pass


def claim_session(sb: Supabase) -> dict:
    """Claim the Start press this run serves; its owner is who we serve.

    The session function writes the press's id into INJECTED_SESSION_ID. Without it (an older
    function), take the oldest unclaimed press of the last 20 minutes: the PATCH only succeeds while
    the row is still 'requested', so two workers booting at the same time never take the same one.
    """
    global OWNER
    # kaggle_kernel stays what the session function wrote ("owner/notebook vN"): it tells which
    # account's notebook this run is on.
    body = {"status": "starting", "heartbeat_at": now_iso()}
    want = INJECTED_SESSION_ID or os.environ.get("PANDA_SESSION_ID", "")
    if want:
        ids = [want]
    else:
        since = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        rows = sb.rest("GET", "worker_sessions", params={
            "status": "eq.requested", "started_at": f"gte.{since}", "order": "started_at.asc", "limit": "10",
            "select": "id"}) or []
        ids = [r["id"] for r in rows]
    for sid in ids:
        params = {"id": f"eq.{sid}"} if want else {"id": f"eq.{sid}", "status": "eq.requested"}
        got = sb.rest("PATCH", "worker_sessions", params=params, body=body, prefer="return=representation")
        if got:
            if got[0].get("owner"):
                OWNER = got[0]["owner"]
            log(f"serving session {sid}" + ("" if OWNER == PRIMARY_OWNER else " (own state folder)"))
            return got[0]
    return sb.rest("POST", "worker_sessions", body={"owner": OWNER, **body,
                                                    "kaggle_kernel": os.environ.get("KAGGLE_KERNEL_RUN_TYPE", "local")},
                   prefer="return=representation")[0]


# ── Hand-off to a fresh Kaggle run ───────────────────────────────────────────
# Kaggle ends a run at 12 h and the worker stops itself at MAX_RUNTIME. Instead of going offline, about
# HANDOFF_LEAD earlier it asks the `session` Edge Function for a successor run. The worker has no Kaggle
# credential of its own (the Kaggle token lives only in that function's secrets), and it does not need one:
# action "handoff" starts the next run of the SAME account through the same code path as the app's Start.
#
# Never two workers on one account:
#   successor: installs, marks its row info.handoff_ready, then WAITS (heartbeating) until the old run's row is
#              stopped / error / silent; only then restores the snapshot and serves.
#   old run:   keeps serving until the successor says it is ready, then stops claiming messages and
#              commands, finishes turns in flight, snapshots, stops Kiro Crew and marks itself stopped.
#              At MAX_RUNTIME it stops anyway, ready or not.
# "sequential" mode (worker.json handoff_mode) does it in the plain order: drain, snapshot, request the
# successor, exit. It is the fallback if Kaggle ever cancels a running version when a new one is pushed.
HANDOFF_RETRY_SECS = 120
HANDOFF_MAX_TRIES = 5
HANDOFF_WAIT_MAX = HANDOFF_LEAD + 20 * 60   # a successor never waits longer than its predecessor can live
PRED_STALE_SECS = 180                       # a predecessor with no heartbeat for this long is gone


def parse_ts(value) -> float | None:
    """Epoch seconds from a PostgREST timestamp. Python 3.10 rejects 1-2 digit fractions, so normalise."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    s = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], s, count=1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def handoff_due(elapsed: float, max_runtime: float, lead: float, tries: int, last_try: float | None, now: float,
                have_successor: bool = False, retry_secs: float = HANDOFF_RETRY_SECS,
                max_tries: int = HANDOFF_MAX_TRIES) -> bool:
    """Is it time to ask for a successor? From (max_runtime - lead) on, once, retried every retry_secs."""
    if have_successor or tries >= max_tries:
        return False
    if elapsed < max_runtime - lead:
        return False
    return last_try is None or now - last_try >= retry_secs


def predecessor_gone(row: dict | None, now: float, stale_secs: float = PRED_STALE_SECS) -> bool:
    """May a successor take over? True once the old run's row says stopped / error, was deleted, or has not
    beaten for stale_secs (the machine died). A 'requested'/'starting' row is not gone."""
    if row is None:
        return True
    status = row.get("status")
    if status in ("stopped", "error"):
        return True
    if status in ("running", "stopping"):
        beat = parse_ts(row.get("heartbeat_at"))
        return beat is None or now - beat > stale_secs
    return False


def request_successor(sb: Supabase, sid: str) -> dict:
    """Ask the session function for the next run of this account. Returns the new worker_sessions row."""
    r = requests.post(f"{SB_URL}/functions/v1/session", json={"action": "handoff", "session_id": sid},
                      headers=sb.headers(), timeout=120)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or body.get("error") or not (body.get("session") or {}).get("id"):
        raise RuntimeError(f"{r.status_code} {scrub(body.get('error') or r.text)[:200]}")
    return body["session"]


def handoff_step(sb: Supabase, sid: str, ho: dict, set_sess, at_heartbeat: bool) -> str | None:
    """One look at the hand-off, from the main loop. Returns "handoff" when this run should now drain and stop."""
    if not AUTO_RESTART:
        return None
    now = time.time()
    if ho["to"] is None:
        if not handoff_due(now - T0, MAX_RUNTIME, HANDOFF_LEAD, ho["tries"], ho["last"], now):
            return None
        ho["tries"] += 1
        ho["last"] = now
        if HANDOFF_MODE == "sequential":
            return "handoff"
        try:
            # Insurance: if Kaggle were to cancel this run when the new version is pushed, nothing is lost.
            with beating(sb, sid):
                snapshot(sb, "pre-handoff")
            nxt = request_successor(sb, sid)
            ho["to"] = nxt["id"]
            SESSION_INFO["handoff"] = {"to": nxt["id"], "at": now_iso(), "mode": HANDOFF_MODE}
            set_sess(info=SESSION_INFO)
            log(f"hand-off: successor {nxt['id']} requested; serving until it is ready")
        except Exception as exc:
            log(f"hand-off: could not request the next run ({ho['tries']}/{HANDOFF_MAX_TRIES}):", scrub(exc)[:200])
        return None
    if not at_heartbeat:
        return None
    rows = sb.rest("GET", "worker_sessions", params={"id": f"eq.{ho['to']}", "select": "status,info"}) or []
    nxt = rows[0] if rows else {}
    if nxt.get("status") in ("error", "stopped") or not rows:
        log("hand-off: the successor ended before it was ready; will ask again")
        ho["to"] = None
        SESSION_INFO.pop("handoff", None)
        return None
    if nxt.get("status") == "starting" and (nxt.get("info") or {}).get("handoff_ready"):
        log("hand-off: successor is ready; no new messages from here on")
        return "handoff"
    return None


def wait_for_predecessor(sb: Supabase, sid: str, pred: str, set_sess) -> str:
    """Successor side: install is done; wait until the old run has stopped. Returns "go" or "stop"."""
    SESSION_INFO["handoff_ready"] = True
    SESSION_INFO["handoff_ready_at"] = now_iso()
    set_sess(info=SESSION_INFO, heartbeat_at=now_iso())
    log(f"hand-off: ready; waiting for run {pred} to stop")
    t0, last_hb = time.time(), 0.0
    while True:
        now = time.time()
        if now - last_hb > HEARTBEAT_SECS:
            row = (sb.rest("GET", "worker_sessions", params={"id": f"eq.{sid}", "select": "stop_requested"}) or [{}])[0]
            if row.get("stop_requested"):
                return "stop"
            set_sess(heartbeat_at=now_iso())
            last_hb = now
        rows = sb.rest("GET", "worker_sessions",
                       params={"id": f"eq.{pred}", "select": "status,heartbeat_at"}) or []
        if predecessor_gone(rows[0] if rows else None, time.time()):
            log(f"hand-off: run {pred} is done after {round(time.time() - t0)} s of waiting")
            return "go"
        if time.time() - t0 > HANDOFF_WAIT_MAX:
            raise RuntimeError(f"the previous run {pred} did not stop in {HANDOFF_WAIT_MAX // 60} minutes")
        time.sleep(5)


def keep_beating(sb: Supabase, sid: str, stop: threading.Event):
    """Heartbeat while the run drains and snapshots, so the app never sees it as dead mid-save."""
    while not stop.wait(HEARTBEAT_SECS):
        with contextlib.suppress(Exception):
            sb.rest("PATCH", "worker_sessions", params={"id": f"eq.{sid}"}, body={"heartbeat_at": now_iso()})


@contextlib.contextmanager
def beating(sb: Supabase, sid: str):
    stop = threading.Event()
    threading.Thread(target=keep_beating, args=(sb, sid, stop), daemon=True).start()
    try:
        yield
    finally:
        stop.set()


def main():
    sb = Supabase()
    sess = claim_session(sb)
    sid = sess["id"]
    SESSION_INFO.update(sess.get("info") or {})   # keeps info.handoff_from when this run is a successor

    def set_sess(**kw):
        sb.rest("PATCH", "worker_sessions", params={"id": f"eq.{sid}"}, body=kw)

    gw_proc = None
    ho = {"to": None, "tries": 0, "last": None}
    try:
        install()
        pred = SESSION_INFO.get("handoff_from")
        if pred:  # successor: do not touch the account's state until the previous run has saved it
            if wait_for_predecessor(sb, sid, pred, set_sess) == "stop":
                SESSION_INFO["stop_reason"] = "stopped before taking over"
                set_sess(status="stopped", stopped_at=now_iso(), info=SESSION_INFO)
                return
        restored = restore(sb)
        files_state = restore_files(sb)
        # The app's system prompt lives in Storage so it can be edited without a redeploy.
        os.makedirs(f"{HOME}/.kiro/crew", exist_ok=True)
        if sb.download("config/prompt.md", f"{HOME}/.kiro/crew/prompt.md"):
            log("system prompt: custom (from storage)")
        os.makedirs(OUTPUTS, exist_ok=True)
        os.makedirs(WORKDIR, exist_ok=True)
        install_skills(sb)
        setup_drive()
        try:
            sync_secrets(sb)
        except Exception as exc:
            log("vault sync failed:", exc)
        # Settings → Memory: off means Crew reads memory but stops writing new memories.
        mem_on = user_settings(sb, OWNER).get("memory", True)
        sh(f"kirocrew config set memory.writes_enabled {'true' if mem_on else 'false'}")
        start_browser(sb)
        _safe(start_codex, sb)
        start_stt()
        gw_proc = start_gateway()
        gw = Gateway()
        # Kiro Crew settings changed in the app, re-applied in case the snapshot predates them.
        apply_overrides(gw, user_settings(sb, OWNER).get("crewOverrides"))
        start_video(sb, gw)
        stop_evt = threading.Event()
        threading.Thread(target=command_loop, args=(sb, gw, stop_evt), daemon=True).start()
        threading.Thread(target=thinking_listener, args=(gw, stop_evt), daemon=True).start()
        threading.Thread(target=followup_loop, args=(sb, gw, stop_evt), daemon=True).start()
        threading.Thread(target=thinking_pusher, args=(sb, stop_evt), daemon=True).start()
        pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL_CHATS)
        # Messages are picked up on their own thread: a slow snapshot or usage refresh in the main loop
        # used to leave new messages "Queued" for minutes, and a network blip in claim_next crashed the run.
        threading.Thread(target=claim_loop, args=(sb, gw, pool, stop_evt), daemon=True).start()
        SESSION_INFO.update(restored=restored, files_restored=files_state, startup_secs=round(time.time() - T0), crew=CREW_VERSION["v"])
        SESSION_INFO.pop("handoff_ready", None)
        set_sess(status="running", heartbeat_at=now_iso(), info=SESSION_INFO)
        USAGE_STATE["sid"] = sid
        # Warm the Kiro account total now (a forced scrape can take ~a minute), so the app shows a
        # real number instead of 0 as soon as it opens, not after Crew's 10-minute cache fills.
        threading.Thread(target=lambda: _safe(usage_now, sb, gw, True), daemon=True).start()
        log("running; waiting for messages")

        seen: set = set()
        last_hb = last_nt = 0.0
        last_snap = time.time()
        reason = "stop requested"
        while True:
            if gw_proc.poll() is not None:
                raise RuntimeError("gateway process exited")
            if time.time() - last_hb > HEARTBEAT_SECS:
                row = sb.rest("GET", "worker_sessions",
                              params={"id": f"eq.{sid}", "select": "stop_requested"})[0]
                if row["stop_requested"]:
                    break
                set_sess(heartbeat_at=now_iso())
                last_hb = time.time()
                beat = True
            else:
                beat = False
            if handoff_step(sb, sid, ho, set_sess, beat) == "handoff":
                reason = "handoff"
                break
            if time.time() - T0 > MAX_RUNTIME:
                reason = "time guard"
                break
            if time.time() - last_nt > NOTIFY_SECS:
                # These can take over a minute together (uploads, usage scrape): keep beating meanwhile,
                # or the app sees no heartbeat for 90 s and shows Panda as Offline.
                with beating(sb, sid):
                    try:
                        mirror_notifications(sb, gw, seen)
                        approve_background(gw, sb)
                    except Exception as exc:
                        log("notifications:", type(exc).__name__)
                    try:
                        browser_config(sb)
                        browser_backup(sb)
                    except Exception as exc:
                        log("browser sync:", type(exc).__name__)
                    try:
                        codex_backup(sb)
                    except Exception as exc:
                        log("codex sync:", type(exc).__name__)
                    try:
                        refresh_usage(sb, gw)
                    except Exception as exc:
                        log("usage refresh:", type(exc).__name__)
                last_nt = time.time()
            if time.time() - last_snap > SNAPSHOT_EVERY:
                # A failed periodic save must never take Panda down; the next one (or the stop) retries.
                try:
                    with beating(sb, sid):
                        set_sess(last_snapshot=snapshot(sb, "periodic", files=False))
                    snapshot_files_later(sb, "periodic")
                except Exception as exc:
                    log("periodic snapshot failed, will retry:", scrub(exc)[:200])
                last_snap = time.time()
            time.sleep(POLL_SECS)

        log("stopping:", reason)
        stop_evt.set()
        set_sess(status="stopping")
        # Keep beating while turns finish and the snapshot uploads: the app and the `session` function
        # treat a run that is silent for 90 s as dead.
        with beating(sb, sid):
            pool.shutdown(wait=True, cancel_futures=False)  # let answers in flight finish
            stop_browser(sb)
            _safe(codex_backup, sb, True)
            snap_at = snapshot(sb, reason)
            # A successor is requested after the snapshot when it was not requested earlier (sequential
            # mode, or the early tries failed). Only a time-based stop hands off; Stop never does.
            if AUTO_RESTART and reason in ("handoff", "time guard") and ho["to"] is None:
                try:
                    nxt = request_successor(sb, sid)
                    SESSION_INFO["handoff"] = {"to": nxt["id"], "at": now_iso(), "mode": "after-stop"}
                    log(f"hand-off: successor {nxt['id']} requested")
                except Exception as exc:
                    SESSION_INFO["handoff_error"] = scrub(exc)[:300]
                    log("hand-off: could not request the next run:", scrub(exc)[:200])
            # Stop Kiro Crew before the row says "stopped": that row is the successor's go signal, and two
            # gateways must never run Crew's scheduled jobs for one account at once.
            if gw_proc and gw_proc.poll() is None:
                sh("kirocrew stop", timeout=60)
        SESSION_INFO.update(stop_reason=reason, runtime_secs=round(time.time() - T0))
        set_sess(status="stopped", stopped_at=now_iso(), last_snapshot=snap_at, info=SESSION_INFO)
    except Exception as exc:
        log("FATAL:", exc)
        log(traceback.format_exc()[-1500:])
        try:
            SESSION_INFO["error"] = scrub(exc)[:500]
            set_sess(status="error", stopped_at=now_iso(), info=SESSION_INFO)
        except Exception:
            pass
        raise
    finally:
        if gw_proc and gw_proc.poll() is None:
            sh("kirocrew stop", timeout=60)
            try:
                gw_proc.wait(timeout=30)
            except Exception:
                gw_proc.kill()
        gl = f"{WORK}/gateway.log"
        if os.path.exists(gl):  # scrub secrets from the log Kaggle keeps as output
            open(gl, "w").write(scrub(open(gl, errors="replace").read()))
    log("worker finished")


if __name__ == "__main__":
    sys.exit(main())
