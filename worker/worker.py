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

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import contextlib
import traceback
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

import requests

# ── config ───────────────────────────────────────────────────────────────────
CFG_PATH = (glob.glob("/kaggle/input/**/worker.json", recursive=True) or [None])[0]
CFG = json.load(open(CFG_PATH)) if CFG_PATH else json.loads(os.environ["KIROCREW_WORKER_CFG"])
SB_URL = CFG["supabase_url"].rstrip("/")
SB_ANON = CFG["supabase_publishable_key"]
OWNER = CFG["owner_id"]  # replaced by whoever pressed Start (claim_session)
SECRETS = [CFG["kiro_api_key"], CFG["worker_password"], CFG.get("vapid_private", ""),
           (CFG.get("drive") or {}).get("refresh_token", ""), (CFG.get("drive") or {}).get("client_secret", "")]
FILES_BUCKET = "panda-files"
OUTPUTS = os.path.expanduser("~/outputs")
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
def install():
    sh("curl -fsSL https://cli.kiro.dev/install | bash", check=True)
    sh("curl -fsSL https://download.crew.kiro.dev/cli.sh | sh", timeout=1500, check=True)
    sh("kiro-cli --version; kirocrew --version")
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


def sync_secrets(sb: "Supabase") -> list:
    """API keys from the app's vault -> env files the agent and MCP servers read."""
    rows = sb.rest("GET", "secrets", params={"owner": f"eq.{OWNER}", "select": "name,value"}) or []
    for r in rows:
        if r["value"] not in SECRETS:
            SECRETS.append(r["value"])  # never let a key appear in logs or replies
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
    log(f"vault: {len(rows)} key(s) available")
    return SECRET_NAMES


# State the Crew snapshot does not carry: Crew's chat transcripts (crew/sessions) and
# kiro-cli's own sessions, agents and steering. Lock files are never saved: a stale
# lock makes kiro-cli refuse to resume the session on the next run.
EXTRA_DIRS = ["crew/sessions", "sessions/cli", "agents", "steering"]


def _no_locks(info: tarfile.TarInfo):
    return None if info.name.endswith(".lock") else info


def restore(sb: Supabase) -> str:
    snap = f"{WORK}/restore-crew.tar.gz"
    extra = f"{WORK}/restore-kiro.tar.gz"
    got = sb.download("latest-crew.tar.gz", snap)
    if got:
        sh(f"kirocrew restore {snap} --mode replace --force", timeout=900)
    if sb.download("latest-kiro.tar.gz", extra):
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
    sb.upload("latest-crew.tar.gz", files[-1])
    extra = f"{WORK}/kiro-extra.tar.gz"
    with tarfile.open(extra, "w:gz") as t:
        for d in EXTRA_DIRS:
            p = f"{HOME}/.kiro/{d}"
            if os.path.isdir(p):
                t.add(p, arcname=d, filter=_no_locks)
    sb.upload("latest-kiro.tar.gz", extra)
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


class Gateway:
    def __init__(self):
        self.token = mint_token()

    def h(self) -> dict:
        return {"Cookie": f"mc_token_{PORT}={self.token}", "Content-Type": "application/json"}

    def call(self, method: str, path: str, body=None, timeout: int = 60):
        r = requests.request(method, f"{GW}{path}", json=body, headers=self.h(), timeout=timeout)
        if r.status_code in (401, 403):
            self.token = mint_token()
            r = requests.request(method, f"{GW}{path}", json=body, headers=self.h(), timeout=timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"gateway {method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if "json" in r.headers.get("content-type", "") else {}

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


def final_reply(gw: Gateway, slot: str) -> str:
    """Authoritative final text: the slot's last assistant row in history."""
    try:
        hist = gw.call("GET", f"/api/chat/slots/{slot}?limit=10")
    except Exception as exc:
        log("history read failed:", exc)
        return ""
    msgs = hist.get("messages") or hist.get("history") or []
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") in ("assistant", "agent", "bot"):
            v = m.get("content") or m.get("text") or ""
            return v if isinstance(v, str) else json.dumps(v)
    return ""


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
        "status": "eq.done", "order": "id.desc", "limit": str(limit), "select": "role,content"})
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
    if not f or not sb.download(BROWSER_BLOB, tmp):
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
        sb.upload(BROWSER_BLOB, tmp, mime="application/octet-stream")
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


def browser_forget(sb: "Supabase") -> dict:
    """Delete every saved login: running engine, local file and the encrypted copy."""
    note = ""
    if _browser_proc["p"] and _browser_proc["p"].poll() is None:
        r = sh(f"{sys.executable} {SKILL_DIR}/pb.py forget", timeout=60)
        note = (r.stdout or "").strip()[:100]
    for path in (BROWSER_STATE,):
        with contextlib.suppress(OSError):
            os.remove(path)
    sb.delete_object(BROWSER_BLOB)
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


def publish_outputs(sb: Supabase, chat: dict, message_id: int, before: dict) -> list:
    """Upload files the turn created or changed in ~/outputs; return [(local_path, file_row)]."""
    published = []
    for path, (mtime, size) in sorted(scan_outputs().items()):
        if before.get(path) == (mtime, size) or size == 0:
            continue
        rel = os.path.relpath(path, OUTPUTS)
        if size > DRIVE_MIN:
            d = drive_put(path)
            if d:
                try:
                    row = sb.rest("POST", "files", prefer="return=representation", body={
                        "owner": chat["owner"], "chat_id": chat["id"], "message_id": message_id,
                        "project_id": chat.get("project_id"), "kind": "output", "name": os.path.basename(rel),
                        "path": f"drive:{d['id']}", "mime": mimetypes.guess_type(path)[0] or "application/octet-stream",
                        "size": size})[0]
                    published.append((path, row))
                except Exception as exc:
                    log("drive file row failed:", exc)
            else:
                log(f"skipped {rel}: over 45 MB and Drive is not connected")
            continue
        obj = f"{chat['owner']}/chats/{chat['id']}/{int(time.time())}-{safe_name(os.path.basename(rel))}"
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        try:
            sb.upload(obj, path, bucket=FILES_BUCKET, mime=mime)
            row = sb.rest("POST", "files", prefer="return=representation", body={
                "owner": chat["owner"], "chat_id": chat["id"], "message_id": message_id,
                "project_id": chat.get("project_id"), "kind": "output", "name": os.path.basename(rel),
                "path": obj, "mime": mime, "size": size})[0]
            published.append((path, row))
        except Exception as exc:
            log("file publish failed:", exc)
        if len(published) >= 20:
            break
    if published:
        log(f"published {len(published)} file(s)")
    return published


def link_files(text: str, published: list) -> str:
    """Point the reply's local paths at the uploaded copies (the app resolves panda-file:)."""
    for local, row in published:
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


def handle(sb: Supabase, gw: Gateway, msg: dict):
    chat = sb.rest("GET", "chats", params={"id": f"eq.{msg['chat_id']}", "select": "*"})[0]
    settings = user_settings(sb, chat["owner"])
    fresh_slot = not chat.get("slot_id")
    slot, needs_replay = ensure_slot(sb, gw, chat, settings)
    prompt = msg["content"]
    attached = fetch_attachments(sb, chat, msg)
    if attached:
        prompt = ("[Files the user attached (read them):\n" + "\n".join(f"- `{p}`" for p in attached) +
                  "]\n\n" + prompt)
    if needs_replay:
        prompt = replay_prefix(sb, chat["id"], msg["id"]) + prompt
        log(f"chat {chat['id']}: Crew lost the slot, replayed history from Supabase")
    prompt = project_preamble(sb, chat, slot, fresh_slot or needs_replay) + prompt
    if fresh_slot or needs_replay:
        name = settings.get("name")
        hints = []
        if name:
            hints.append(f"The user's name is {name}.")
        if SECRET_NAMES:
            hints.append("API keys stored in the user's vault (load with `source ~/.panda/secrets.env` "
                         "inside a bash command, never print them): " + ", ".join(SECRET_NAMES) + ".")
        if hints:
            prompt = "[APP CONTEXT] " + " ".join(hints) + "\n\n" + prompt
    before = scan_outputs()
    reply = sb.rest("POST", "messages", prefer="return=representation",
                    body={"chat_id": chat["id"], "owner": chat["owner"], "role": "assistant",
                          "status": "streaming", "reply_to": msg["id"],
                          "meta": {"model": chat.get("model"), "effort": chat.get("effort")}})[0]
    text, thinking, tools, usage, last_push, t0 = "", "", [], None, 0.0, time.time()
    with THINK_LOCK:
        THINK[slot], THINK_REPLY[slot], THINK_SENT[slot] = "", reply["id"], 0
    meta = lambda: {"model": chat.get("model"), "effort": chat.get("effort"), "replayed": needs_replay,
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
        final = final_reply(gw, slot) or text
        published = publish_outputs(sb, chat, reply["id"], before)
        final = scrub(link_files(final, published))
        sb.rest("PATCH", "messages", params={"id": f"eq.{reply['id']}"},
                body={"content": final, "thinking": scrub(thinking), "status": "done",
                      "meta": {**meta(), "files": [r["id"] for _, r in published]}})
        sb.rest("PATCH", "messages", params={"id": f"eq.{msg['id']}"}, body={"status": "done"})
        log(f"answered message {msg['id']} ({len(final)} chars, {len(thinking)} thinking)")
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
    """Oldest pending message whose chat is not already answering (chats run in parallel)."""
    rows = sb.rest("GET", "messages", params={"status": "eq.pending", "role": "eq.user",
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
    try:
        handle(sb, gw, msg)
    except Exception as exc:  # handle() records its own errors; this guards the thread
        log("turn crashed:", exc)
    finally:
        with BUSY_LOCK:
            BUSY_CHATS.discard(msg["chat_id"])


# ── commands relay: the app's Customize pages talk to Crew through here ────
GW_ALLOWED = re.compile(
    r"^/api/(skills|mcp|mcp-gateway|crons|lessons|models|effort-levels|notifications|memory|knowledge|"
    r"artifacts|agents|workspaces|spawn|subagents|tasks|config|status|system)(/|\?|$)")


def run_command(sb: Supabase, gw: Gateway, cmd: dict):
    op, args = cmd["op"], cmd.get("args") or {}
    try:
        if op == "gw":
            method = str(args.get("method", "GET")).upper()
            path = str(args.get("path", ""))
            if method not in ("GET", "POST", "PUT", "PATCH", "DELETE") or not GW_ALLOWED.match(path) or ".." in path:
                raise ValueError(f"not allowed: {method} {path}")
            result = gw.call(method, path, args.get("body"), timeout=120)
        elif op == "secrets.sync":
            result = {"names": sync_secrets(sb)}
        elif op == "browser.forget":
            result = browser_forget(sb)
        elif op == "ping":
            result = {"ok": True, "uptime_secs": round(time.time() - T0)}
        else:
            raise ValueError(f"unknown op {op}")
        sb.rest("PATCH", "commands", params={"id": f"eq.{cmd['id']}"},
                body={"status": "done", "result": json.loads(scrub(json.dumps(result if result is not None else {})))})
    except Exception as exc:
        sb.rest("PATCH", "commands", params={"id": f"eq.{cmd['id']}"},
                body={"status": "error", "result": {"error": scrub(exc)[:500]}})


def command_loop(sb: Supabase, gw: Gateway, stop: threading.Event):
    while not stop.is_set():
        try:
            rows = sb.rest("GET", "commands", params={"status": "eq.pending", "order": "id.asc",
                                                       "limit": "5", "select": "*"}) or []
            for c in rows:
                got = sb.rest("PATCH", "commands", prefer="return=representation",
                              params={"id": f"eq.{c['id']}", "status": "eq.pending"}, body={"status": "running"})
                if got:
                    run_command(sb, gw, got[0])
        except Exception as exc:
            log("command loop:", exc)
        stop.wait(1.5)


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
    """Claim the newest 'requested' session (the Start press); its owner is who we serve."""
    global OWNER
    rows = sb.rest("GET", "worker_sessions", params={
        "status": "in.(requested,starting)", "order": "started_at.desc", "limit": "1", "select": "*"})
    if rows and rows[0].get("owner"):
        OWNER = rows[0]["owner"]
    body = {"status": "starting", "heartbeat_at": now_iso(),
            "kaggle_kernel": os.environ.get("KAGGLE_KERNEL_RUN_TYPE", "local")}
    if rows:
        return sb.rest("PATCH", "worker_sessions", params={"id": f"eq.{rows[0]['id']}"},
                       body=body, prefer="return=representation")[0]
    return sb.rest("POST", "worker_sessions", body={"owner": OWNER, **body},
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
        stop_evt = threading.Event()
        threading.Thread(target=command_loop, args=(sb, gw, stop_evt), daemon=True).start()
        threading.Thread(target=thinking_listener, args=(gw, stop_evt), daemon=True).start()
        threading.Thread(target=thinking_pusher, args=(sb, stop_evt), daemon=True).start()
        pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL_CHATS)
        set_sess(status="running", heartbeat_at=now_iso(),
                 info={"restored": restored, "startup_secs": round(time.time() - T0)})
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
        set_sess(status="stopped", stopped_at=now_iso(), last_snapshot=snap_at,
                 info={"restored": restored, "stop_reason": reason,
                       "runtime_secs": round(time.time() - T0)})
    except Exception as exc:
        log("FATAL:", exc)
        log(traceback.format_exc()[-1500:])
        try:
            set_sess(status="error", stopped_at=now_iso(), info={"error": scrub(exc)[:500]})
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
