"""Local test for the hang watcher (worker.hang_step). Uses its own `sleep` / busy processes under a FAKE
kiro-cli-chat parent in a fake process table, so it never looks at or stops a real agent command.

    python3 tools/test_hang.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="hang-cwd-"))
import worker as W  # noqa: E402

os.chdir(_cwd)
W.JOBS_DIR = tempfile.mkdtemp(prefix="hang-jobs-")     # no real pjob jobs in the groups
W.GUARD_LOG = os.path.join(W.JOBS_DIR, "guard.log")
W.HANG_MIN_SECS, W.HANG_WINDOW, W.HANG_EVERY = 0, 2, 1
FAKE_PARENT = 999_999_001


def table(*pids):
    real = W._guard_procs()
    procs = {FAKE_PARENT: {"comm": "kiro-cli-chat", "ppid": 1, "sid": FAKE_PARENT, "start": 1, "rss": 0}}
    for p in pids:
        procs[p] = dict(real[p], ppid=FAKE_PARENT)
    return procs


def run(procs, verdict, slot, kill_secs=10**9):
    W.HANG.clear()
    W.HANG_KILL_SECS = kill_secs
    W.lite_ask = lambda *a, **k: json.dumps(verdict)
    got = {"steer": [], "kill": []}
    kw = dict(procs=procs, slot_fn=lambda pid: slot, chat_fn=lambda s: {"id": "chat-1", "owner": W.OWNER},
              deliver=lambda chat, text, root: got["steer"].append(text),
              kill=lambda g, pr: got["kill"].append(g["root"]))
    t = time.time()
    W.hang_step(None, None, now=t, **kw)
    time.sleep(2.2)
    out = W.hang_step(None, None, now=t + 2.2, **kw)
    return out, got


idle = subprocess.Popen(["sleep", "300"])
busy = subprocess.Popen([sys.executable, "-c", "while True: pass"])
time.sleep(0.5)
try:
    out, got = run(table(idle.pid), {"stuck": True, "why": "waits forever"}, "slot-1")
    assert out == [(idle.pid, "steer")] and "looks hung" in got["steer"][0] and not got["kill"], (out, got)
    print("ok: idle inline command in a chat -> Haiku steer")

    out, got = run(table(idle.pid), {"stuck": True}, "", kill_secs=0)
    assert out == [(idle.pid, "stop")] and got["kill"] == [idle.pid] and not got["steer"], (out, got)
    print("ok: idle command of a helper (no chat) -> stopped")

    out, got = run(table(idle.pid), {"stuck": True}, "", kill_secs=10**9)
    assert out == [] and not got["kill"], (out, got)
    print("ok: helper command not stopped before HANG_KILL_SECS")

    out, got = run(table(idle.pid), {"stuck": False, "why": "a deliberate sleep"}, "slot-1")
    assert out == [(idle.pid, "ok")] and not got["steer"] and not got["kill"], (out, got)
    print("ok: Haiku says normal -> nothing")

    out, got = run(table(busy.pid), {"stuck": True}, "slot-1", kill_secs=0)
    assert out == [] and not got["steer"] and not got["kill"], (out, got)
    print("ok: busy command (100% CPU) is never flagged")
    assert idle.poll() is None and busy.poll() is None
finally:
    idle.kill()
    busy.kill()
