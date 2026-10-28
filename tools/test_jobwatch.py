"""Local tests for `pjob --notify` (the job watcher that wakes a chat) and `brief` (no Supabase, no Kiro Crew).

    python3 tools/test_jobwatch.py

Runs real pjob/brief commands in a temporary HOME, with a fake Supabase and a fake Haiku (lite_ask).
What it cannot cover: a real Haiku answer, a real chat turn picking the note up (steer_pending/claim_next
are the existing paths), the app's "Haiku steered" label (checked in the UI harness).
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

HOME = tempfile.mkdtemp(prefix="jobwatch-home-")
os.environ["HOME"] = HOME
os.environ["KIROCREW_WORKER_CFG"] = json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"})
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(REPO, "worker"))
SKILL = f"{HOME}/.kiro/crew/skills/panda-jobs"
os.makedirs(SKILL)
for f in ("jobs.py", "brief.py"):
    open(f"{SKILL}/{f}", "w").write(open(os.path.join(REPO, "worker/skills/panda-jobs", f)).read())
CHAT = {"id": "11111111-2222-3333-4444-555555555555", "owner": "owner-1"}
SLOT = f"panda-{CHAT['id']}"
json.dump({f"dashboard:{SLOT}": {"sid": "sid-chat"}, "dashboard:other": {"sid": "sid-other"}},
          open(f"{HOME}/.kiro/crew/session_map.json", "w"))
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="jobwatch-cwd-"))
import worker as W  # noqa: E402
W.OWNER = "owner-1"  # on Kaggle worker.py reads the real worker.json first
os.chdir(_cwd)


class FakeSB:
    def __init__(self):
        self.posts = []

    def rest(self, method, table, params=None, body=None, prefer=None):
        if method == "GET" and table == "chats":
            ok = (params or {}).get("slot_id") == f"eq.{SLOT}" or (params or {}).get("id") == f"eq.{CHAT['id']}"
            return [dict(CHAT)] if ok else []
        if method == "POST" and table == "messages":
            self.posts.append(body)
            return [{"id": len(self.posts)}]
        return []


HAIKU = {"calls": [], "reply": "Rendered 300 frames to ~/work/out.mp4, no errors."}


def fake_lite_ask(gw, text, model, name, timeout=60):
    HAIKU["calls"].append((name, text))
    return HAIKU["reply"]


W.lite_ask = fake_lite_ask


def pjob(*args, sid="sid-chat"):
    env = dict(os.environ, HOME=HOME, KIRO_SESSION_ID=sid)
    return subprocess.run([sys.executable, f"{SKILL}/jobs.py", *args], env=env, capture_output=True, text=True)


def wait_end(name, secs=20):
    t0 = time.time()
    while time.time() - t0 < secs:
        if os.path.exists(f"{HOME}/work/jobs/{name}/exit"):
            return
        time.sleep(0.2)
    raise AssertionError(f"{name} did not end")


def test_notify_records_chat_slot():
    r = pjob("start", "t1", "--notify", "--want", "the answer", "--", "echo", "hi")
    assert r.returncode == 0, r.stderr
    info = json.load(open(f"{HOME}/work/jobs/t1/notify"))
    assert info["slot"] == SLOT and info["want"] == "the answer" and info["stall"] == 600, info
    assert "no need to poll" in r.stdout
    wait_end("t1")
    open(f"{HOME}/work/jobs/t1/notified", "w").write("test")


def test_helper_run_has_no_chat_to_wake():
    r = pjob("start", "t2", "--notify", "--", "true", sid="sid-of-a-subagent")
    assert not os.path.exists(f"{HOME}/work/jobs/t2/notify")
    assert "No chat to wake" in r.stdout


def test_short_end_goes_raw_no_haiku():
    sb = FakeSB()
    pjob("start", "t3", "--notify", "--sh", "echo line1; echo line2; exit 3")
    wait_end("t3")
    HAIKU["calls"].clear()
    W.job_watch_step(sb, None)
    assert len(sb.posts) == 1 and not HAIKU["calls"], (sb.posts, HAIKU["calls"])
    m = sb.posts[0]
    assert m["meta"] == {"steer": True, "haiku_steer": True, "job": "t3", "job_event": "failed"}, m["meta"]
    assert m["status"] == "pending" and m["role"] == "user" and m["chat_id"] == CHAT["id"]
    assert "FAILED (exit 3)" in m["content"] and "line2" in m["content"] and "[Job watcher" in m["content"]
    W.job_watch_step(sb, None)
    assert len(sb.posts) == 1, "delivered twice"


def test_long_end_uses_haiku_for_want():
    sb = FakeSB()
    pjob("start", "t4", "--notify", "--want", "frames and output path", "--sh", "for i in $(seq 1 200); do echo frame $i; done")
    wait_end("t4")
    HAIKU["calls"].clear()
    W.job_watch_step(sb, None)
    assert len(sb.posts) == 1 and len(HAIKU["calls"]) == 1
    assert "frames and output path" in HAIKU["calls"][0][1]
    c = sb.posts[0]["content"]
    assert "Haiku: Rendered 300 frames" in c and "finished" in c and "frame 200" in c and "frame 100" not in c


def test_stall_steers_only_when_haiku_says_stuck():
    sb = FakeSB()
    pjob("start", "t5", "--notify", "--sh", "echo started; python3 -c 'while True: pass'")
    time.sleep(1)
    d = f"{HOME}/work/jobs/t5"
    W.job_watch_step(sb, None)                       # first look: remembers the log size
    w = json.load(open(f"{d}/watch.json"))
    w["quiet_since"] -= 700
    json.dump(w, open(f"{d}/watch.json", "w"))
    HAIKU["reply"] = '{"stuck": false, "why": "loading a model"}'
    W.job_watch_step(sb, None)
    assert not sb.posts, "a quiet but fine job must not wake the agent"
    w = json.load(open(f"{d}/watch.json"))
    w["alert_at"] = 0
    json.dump(w, open(f"{d}/watch.json", "w"))
    HAIKU["reply"] = '{"stuck": true, "why": "100% CPU and no output for 11 min"}'
    W.job_watch_step(sb, None)
    assert len(sb.posts) == 1 and sb.posts[0]["meta"]["job_event"] == "stuck", sb.posts
    assert "looks stuck" in sb.posts[0]["content"] and "100% CPU" in sb.posts[0]["content"]
    W.job_watch_step(sb, None)
    assert len(sb.posts) == 1, "second alert must wait at least 20 min"
    pjob("stop", "t5")
    W.job_watch_step(sb, None)
    assert len(sb.posts) == 1, "a job the agent stopped itself must not wake it"
    HAIKU["reply"] = "Rendered 300 frames to ~/work/out.mp4, no errors."


def test_brief_short_and_long():
    stop = threading.Event()
    threading.Thread(target=W.brief_loop, args=(None, stop), daemon=True).start()
    env = dict(os.environ, HOME=HOME)
    r = subprocess.run([sys.executable, f"{SKILL}/brief.py", "--", "echo", "small"], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("exit 0") and "small" in r.stdout, r.stdout
    HAIKU["calls"].clear()
    HAIKU["reply"] = "All 120 tests passed except test_mix (AssertionError)."
    r = subprocess.run([sys.executable, f"{SKILL}/brief.py", "--want", "which tests failed", "--sh",
                        "for i in $(seq 1 300); do echo ok test_$i; done; echo 'Error: test_mix failed'; exit 1"],
                       env=env, capture_output=True, text=True, timeout=30)
    stop.set()
    out = r.stdout
    assert r.returncode == 1, out
    assert "Haiku (which tests failed): All 120 tests passed" in out, out
    assert "Error: test_mix failed" in out and "full log:" in out and "ok test_150" not in out, out
    assert len(out.splitlines()) < 15, out
    assert "which tests failed" in HAIKU["calls"][0][1]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    order = ["test_notify_records_chat_slot", "test_helper_run_has_no_chat_to_wake", "test_short_end_goes_raw_no_haiku",
             "test_long_end_uses_haiku_for_want", "test_stall_steers_only_when_haiku_says_stuck", "test_brief_short_and_long"]
    for name in order:
        globals()[name]()
        print("ok", name)
    print("all passed")
