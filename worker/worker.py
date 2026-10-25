"""Kiro Crew worker for Kaggle: Supabase relay <-> Kiro Crew gateway.

Lifecycle of one run (started by the user, never by itself):
  1. install kiro-cli + Kiro Crew, restore the last snapshot from Supabase Storage
  2. start the gateway (unsandboxed: Kaggle blocks user namespaces)
  3. claim the user's worker_sessions row, heartbeat it
  4. answer pending messages: one Kiro Crew chat slot per Supabase chat, replies streamed
  5. mirror Crew notifications (cron results) into the notifications table
  6. snapshot every SNAPSHOT_EVERY seconds; on stop_requested or the time guard:
     final snapshot, mark stopped, shut down. It never starts another run.

Config comes from worker.json in an attached private dataset. No secret is printed.
"""
from __future__ import annotations

import base64
import glob
import json
import os
import re
import shlex
import shutil
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
    # Cheap model for background work: auto-titles, memory consolidation, heartbeats
    # all run Kiro Crew's "background" role. GLM-5 keeps those nearly free.
    sh("kirocrew config set agent.role_models '{\"background\": \"glm-5\"}'")
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


PANDA_SECRETS = os.path.expanduser("~/.panda/secrets.env")
SECRET_NAMES: list = []


DEV_PREFIX = "DEV__"          # vault names of the Developer tab's env variables (stored without it in dev.env)
DEV_ENV = os.path.expanduser("~/.panda/dev.env")
DEV_ROWS: list = []           # [{name, value}] with the prefix removed
DEV_LOCK = threading.Lock()
DEV_TURNS = {"n": 0}          # developer turns running now; dev.env exists only while > 0


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


def dev_preamble() -> str:
    names = ", ".join(r["name"] for r in DEV_ROWS) or "(none yet — the user adds them in Developer → Env variables)"
    return f"""[DEVELOPER MODE]
This is the Developer chat. Here, and only here, the user may ask you to change Panda itself: its
web app (UI), the worker, its skills or its prompt. Elsewhere you never touch Panda's own code.

- Source: {DEV_REPO} (public). Work in ~/dev/panda: clone it the first time, otherwise `git fetch`.
  Use branch $PANDA_DEV_BRANCH if it is set, else the branch the user names, else the default branch.
  Read README.md, docs/architecture.md and docs/runbook.md before the first change.
- Developer env variables, for this chat only: {names}.
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


def restore(sb: Supabase) -> str:
    snap = f"{WORK}/restore-crew.tar.gz"
    extra = f"{WORK}/restore-kiro.tar.gz"
    got = sb.download(state_key("latest-crew.tar.gz"), snap)
    if got:
        sh(f"kirocrew restore {snap} --mode replace --force", timeout=900)
    if sb.download(state_key("latest-kiro.tar.gz"), extra):
        with tarfile.open(extra) as t:
            t.extractall(f"{HOME}/.kiro", filter="data")
        for lock in glob.glob(f"{HOME}/.kiro/sessions/cli/*.lock"):
            os.remove(lock)
    log("restore:", "from snapshot" if got else "fresh start (no snapshot yet)")
    return "restored" if got else "fresh"


def snapshot(sb: Supabase, reason: str) -> str:
    out = f"{WORK}/snap"
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    sh(f"kirocrew snapshot {out} --keep 1", timeout=900)
    files = sorted(glob.glob(f"{out}/*.tar.gz"))
    if not files:
        raise RuntimeError("snapshot produced no file")
    sb.upload(state_key("latest-crew.tar.gz"), files[-1])
    extra = f"{WORK}/kiro-extra.tar.gz"
    with tarfile.open(extra, "w:gz") as t:
        for d in EXTRA_DIRS:
            p = f"{HOME}/.kiro/{d}"
            if os.path.isdir(p):
                t.add(p, arcname=d, filter=_no_locks)
    sb.upload(state_key("latest-kiro.tar.gz"), extra)
    log(f"snapshot uploaded ({reason}), {os.path.getsize(files[-1]) // 1024} KiB")
    return now_iso()


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
        if d and d.get("at", 0) >= since:
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
    if not slot or (slot not in READY_SLOTS and slot not in live_slots(gw)):
        agent = chat["agent"] or AGENT
        created = gw.call("POST", "/api/chat/slots",
                          {"name": (chat["title"] or "chat")[:60], **({"agent": agent} if agent else {})})
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


def drive_put(path: str) -> dict | None:
    if not os.path.exists(DRIVE_CFG):
        return None
    r = subprocess.run([sys.executable, DRIVE_TOOL, "put", path, "outputs"], capture_output=True, text=True, timeout=3600)
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
    """A referenced path that points to a real file under ~/outputs or ~/work (nothing else)."""
    t = (target or "").strip().strip("<>")
    if not t or t.startswith("panda-file:") or re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I):
        return None
    p = os.path.abspath(os.path.expanduser(t))
    roots = (OUTPUTS, WORKDIR)
    if not any(p == r or p.startswith(r + os.sep) for r in roots):
        return None
    return p if os.path.isfile(p) else None


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


def publish_outputs(sb: Supabase, chat: dict, message_id: int, before: dict, reply: str = "") -> list:
    """Upload the turn's deliverables; return [(local_path, file_row, inline)].

    What counts as a deliverable:
      - files the reply names (a link, an image embed, or a backtick path), so a deck built from
        photos publishes only the deck, not every photo it used;
      - if the reply names none, every new file in ~/outputs (a one-file answer that wasn't named).
    Working files in ~/work are published only when the reply points at them (e.g. an inline image).
    `inline` is set for images embedded with ![](…): the app shows them in the text, not as a card.
    """
    targets, img_targets = _reply_refs(reply)
    new = {p: sz for p, (mt, sz) in scan_outputs().items() if before.get(p) != (mt, sz) and sz > 0}
    for t in targets | img_targets:  # inline images etc. the agent kept in ~/work
        p = _resolve_local(t)
        if p and p not in new:
            new[p] = os.path.getsize(p)
    referenced = {p for p in new if _referenced(p, targets | img_targets)}
    chosen = referenced or {p for p in new if p.startswith(OUTPUTS + os.sep)}
    published = []
    for path in sorted(chosen):
        row = _publish_one(sb, chat, message_id, path, new[path])
        if not row:
            continue
        inline = path.lower().endswith(IMAGE_EXT) and _referenced(path, img_targets)
        published.append((path, row, inline))
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
        if fresh_slot or needs_replay or slot in PENDING_HINTS:
            PENDING_HINTS.discard(slot)
            prompt = app_hints(settings) + (dev_preamble() if is_developer_chat(settings, chat) else "") + prompt
    before = scan_outputs()
    base_meta = {"model": chat.get("model"), "effort": chat.get("effort")}
    if cmd or kb:
        base_meta["command"] = cmd or "/kb"
    reply = sb.rest("POST", "messages", prefer="return=representation",
                    body={"chat_id": chat["id"], "owner": chat["owner"], "role": "assistant",
                          "status": "streaming", "reply_to": msg["id"], "meta": base_meta})[0]
    text, thinking, tools, usage, last_push, t0 = "", "", [], None, 0.0, time.time()
    with THINK_LOCK:
        THINK[slot], THINK_REPLY[slot], THINK_SENT[slot] = "", reply["id"], 0
    meta = lambda: {**base_meta, "replayed": needs_replay,
                    "tools": tools[-30:], "usage": usage, "secs": round(time.time() - t0, 1)}
    try:
        for c in gw.stream_chat(slot, prompt):
            if not isinstance(c, dict):
                continue
            kind = c.get("type")
            if kind == "permission":
                ask_approval(sb, gw, chat, slot, reply["id"], c)
                continue
            if kind == "tool":
                step = clean_step(c.get("content"))
                if step and (not tools or tools[-1] != step):
                    tools.append(step)
                last_push = 0.0  # show the new step in the app right away
            elif kind == "context_usage":
                try:
                    usage = json.loads(c.get("content") or "{}")
                except ValueError:
                    pass
            elif kind == "error":
                tools.append("error: " + clean_step(c.get("content")))
            elif is_thinking(c):
                with THINK_LOCK:
                    THINK[slot] = THINK.get(slot, "") + (c.get("content") or c.get("text") or "")
            elif kind == "chunk":
                text += chunk_text(c)
            elif kind == "assistant" and c.get("content"):
                text = c["content"] if len(c["content"]) >= len(text) else text
            with THINK_LOCK:
                thinking = THINK.get(slot, "")
            if time.time() - last_push > 0.5 and (text or thinking or tools):
                row = sb.rest("PATCH", "messages", params={"id": f"eq.{reply['id']}"},
                              prefer="return=representation",
                              body={"content": scrub(text), "thinking": scrub(thinking), "meta": meta()})
                last_push = time.time()
                if row and row[0].get("cancel"):  # "Stop generating" pressed in the app
                    try:
                        gw.call("POST", f"/api/chat/slots/{slot}/stop")
                    except Exception as exc:
                        log("stop call failed:", exc)
                    tools.append("stopped by you")
                    break
        time.sleep(0.3)  # let the last thinking frames land
        with THINK_LOCK:
            thinking = THINK.get(slot, thinking)
            THINK_REPLY.pop(slot, None)
        res = turn_result(gw, slot, want_stats=not cmd)
        usage = context_usage(gw, slot, t0, usage)
        final = res["text"] or text
        if cmd:
            final = _ANSI.sub("", final)
        if not final.strip() and res["errors"]:
            raise RuntimeError(res["errors"][-1][:400])
        published = publish_outputs(sb, chat, reply["id"], before, final)
        final = scrub(link_files(final, published))
        # Inline images show in the text; only the rest become download cards.
        done_meta = {**meta(), "files": [row["id"] for _, row, inline in published if not inline]}
        stats = res["stats"] or {}
        if isinstance(stats.get("credits"), (int, float)):
            done_meta["credits"] = round(float(stats["credits"]), 4)
        if stats:
            done_meta["turn_stats"] = {k: stats[k] for k in ("elapsed_ms", "ttft_ms", "model", "credits", "retries")
                                       if k in stats}
        shot = browser_snapshot(sb, chat, reply["id"], t0)
        if shot:
            done_meta["browser"] = shot
        sb.rest("PATCH", "messages", params={"id": f"eq.{reply['id']}"},
                body={"content": final, "thinking": scrub(thinking), "status": "done", "meta": done_meta})
        sb.rest("PATCH", "messages", params={"id": f"eq.{msg['id']}"}, body={"status": "done"})
        USAGE_STATE["poke"] = True  # refresh the account total soon
        log(f"answered message {msg['id']} ({len(final)} chars, {len(thinking)} thinking, "
            f"{done_meta.get('credits', '?')} credits)")
        if settings.get("notifyReplies", True) and time.time() - t0 > 20:  # only long answers
            push(sb, chat["owner"], f"Panda replied · {chat.get('title') or 'chat'}"[:60],
                 re.sub(r"[#*_`>\[\]]", "", final)[:180], f"./?c={chat['id']}")
    except Exception as exc:
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


def claim_next(sb: Supabase):
    """Oldest pending message whose chat is not already answering (chats run in parallel).

    Only the person who pressed Start is served: another account's message is never picked up,
    even though the worker account can read every row.
    """
    rows = sb.rest("GET", "messages", params={"status": "eq.pending", "role": "eq.user", "owner": f"eq.{OWNER}",
                                               "order": "id.asc", "limit": "20", "select": "id,chat_id"})
    for r in rows or []:
        with BUSY_LOCK:
            if r["chat_id"] in BUSY_CHATS or len(BUSY_CHATS) >= MAX_PARALLEL_CHATS:
                continue
        got = sb.rest("PATCH", "messages", prefer="return=representation",
                      params={"id": f"eq.{r['id']}", "status": "eq.pending"}, body={"status": "processing"})
        if got:
            with BUSY_LOCK:
                BUSY_CHATS.add(r["chat_id"])
            return got[0]
    return None


def run_message(sb: Supabase, gw: Gateway, msg: dict):
    dev = False
    try:
        chat = sb.rest("GET", "chats", params={"id": f"eq.{msg['chat_id']}", "select": "id,owner"})[0]
        dev = is_developer_chat(user_settings(sb, chat["owner"]), chat)
    except Exception:
        pass
    if dev:  # developer env variables exist on disk only while the Developer chat is answering
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
    usage = {"account": account, "daily": daily, "at": now_iso()}
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
    gw.call("POST", f"/api/chat/slots/{slot}/side/open", {})
    r = gw.call("POST", f"/api/chat/slots/{slot}/side/turn", {"question": question})
    run_id = r.get("run_id") or ""
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(1.2)
        st = gw.call("GET", f"/api/chat/slots/{slot}/side")
        msgs = [m for m in (st.get("messages") or []) if isinstance(m, dict)]
        if not run_id:  # it was queued behind another side question: find the run it got
            mine = [m for m in msgs if m.get("role") == "user" and (m.get("content") or "").strip() == question]
            run_id = (mine[-1].get("run_id") or "") if mine else ""
            continue
        answer = [m for m in msgs if m.get("run_id") == run_id and m.get("role") == "assistant"]
        if answer and not st.get("running"):
            text = "\n\n".join(_row_text(m) for m in answer if _row_text(m))
            return {"answer": scrub(_ANSI.sub("", text)), "error": any(m.get("is_error") for m in answer)}
        if not st.get("running") and not answer and time.time() - t0 > 8:
            return {"answer": "", "error": True, "note": "The side chat ended without an answer."}
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
        elif op == "ping":
            result = {"ok": True, "uptime_secs": round(time.time() - T0), "crew": CREW_VERSION["v"]}
        else:
            raise ValueError(f"unknown op {op}")
        sb.rest("PATCH", "commands", params={"id": f"eq.{cmd['id']}"},
                body={"status": "done", "result": json.loads(scrub(json.dumps(result if result is not None else {})))})
    except Exception as exc:
        sb.rest("PATCH", "commands", params={"id": f"eq.{cmd['id']}"},
                body={"status": "error", "result": {"error": scrub(exc)[:500]}})


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
                     "priority": n.get("priority"), "job_id": n.get("job_id")}})
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


def main():
    sb = Supabase()
    sess = claim_session(sb)
    sid = sess["id"]

    def set_sess(**kw):
        sb.rest("PATCH", "worker_sessions", params={"id": f"eq.{sid}"}, body=kw)

    gw_proc = None
    try:
        install()
        restored = restore(sb)
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
        gw_proc = start_gateway()
        gw = Gateway()
        # Kiro Crew settings changed in the app, re-applied in case the snapshot predates them.
        apply_overrides(gw, user_settings(sb, OWNER).get("crewOverrides"))
        stop_evt = threading.Event()
        threading.Thread(target=command_loop, args=(sb, gw, stop_evt), daemon=True).start()
        threading.Thread(target=thinking_listener, args=(gw, stop_evt), daemon=True).start()
        threading.Thread(target=thinking_pusher, args=(sb, stop_evt), daemon=True).start()
        pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL_CHATS)
        SESSION_INFO.update(restored=restored, startup_secs=round(time.time() - T0), crew=CREW_VERSION["v"])
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
            if time.time() - T0 > MAX_RUNTIME:
                reason = "time guard"
                break
            if time.time() - last_nt > NOTIFY_SECS:
                mirror_notifications(sb, gw, seen)
                approve_background(gw, sb)
                try:
                    browser_config(sb)
                    browser_backup(sb)
                except Exception as exc:
                    log("browser sync:", type(exc).__name__)
                try:
                    refresh_usage(sb, gw)
                except Exception as exc:
                    log("usage refresh:", type(exc).__name__)
                last_nt = time.time()
            if time.time() - last_snap > SNAPSHOT_EVERY:
                set_sess(last_snapshot=snapshot(sb, "periodic"))
                last_snap = time.time()
            msg = claim_next(sb)
            if msg:
                pool.submit(run_message, sb, gw, msg)
            else:
                time.sleep(POLL_SECS)

        log("stopping:", reason)
        stop_evt.set()
        pool.shutdown(wait=True, cancel_futures=False)  # let answers in flight finish
        stop_browser(sb)
        set_sess(status="stopping")
        snap_at = snapshot(sb, reason)
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
