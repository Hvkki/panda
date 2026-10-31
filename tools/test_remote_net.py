"""Remote jobs ride out a dropped connection instead of dying. No network.

A queued remote job died after 1h15m of waiting with "Connection reset by peer" (Oct 2026): one
failed Storage read ended it. Now get_fresh retries, and remote_task keeps polling for up to
REMOTE_NET_GRACE while Supabase is unreachable.

    python3 tools/test_remote_net.py
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
os.environ["PANDA_REMOTE_BOX"] = BOX = tempfile.mkdtemp(prefix="net-box-")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="net-cwd-"))
import requests  # noqa: E402
import worker as W  # noqa: E402

os.chdir(_cwd)
W.time.sleep = lambda s: None


class Resp:
    def __init__(self, code, body=b"", js=None):
        self.status_code, self.content, self._js = code, body, js
        self.text = json.dumps(js) if js is not None else body.decode()

    def json(self):
        return self._js


class FakeHTTP:
    def __init__(self, plan):
        self.plan = list(plan)

    def _next(self):
        x = self.plan.pop(0)
        if isinstance(x, Exception):
            raise x
        return x

    post = get = lambda self, *a, **k: self._next()


class FakeSB:
    headers = lambda self, extra=None: {}


reset = requests.ConnectionError("('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))")
signed = Resp(200, js={"signedURL": "/object/sign/x?token=t"})

# 1. get_fresh: a reset, a 503, then the object.
W.http = lambda: FakeHTTP([reset, Resp(503, b"busy"), signed, Resp(200, b"DATA")])
h = W.http()
W.http = lambda: h
assert W.Supabase.get_fresh(FakeSB(), "k") == b"DATA"
# 2. A missing object is still None at once (no retries).
h = FakeHTTP([Resp(400, js={"statusCode": "404", "error": "not_found"})])
assert W.Supabase.get_fresh(FakeSB(), "k") is None and not h.plan
# 3. An outage that never ends raises, it is not taken for "missing".
h = FakeHTTP([reset] * 6)
try:
    W.Supabase.get_fresh(FakeSB(), "k")
    raise AssertionError("no error")
except RuntimeError as exc:
    assert "kept refusing" in str(exc), exc
print("ok get_fresh retries resets and 5xx, None only for a missing object, raises when it stays down")

# 4. remote_task: status reads fail 5 times while queued, then the task finishes: exit 0, not 255.
W.remote_pick = lambda sb, tid, avoid=(): "r2"
W.remote_ensure = lambda sb, rid, note, cancelled: True
W.remote_put_json = lambda *a, **k: None
fails = {"n": 0}


def get_json(sb, rid, *parts):
    if parts == ("beat.json",):
        return {"state": "running", "at": W.time.time(), "running": ["x"]}
    if fails["n"] < 5:
        fails["n"] += 1
        raise RuntimeError("storage kept refusing remote/r2/tasks/t/status.json (ConnectionError)")
    return {"state": "done", "exit": 0}


W.remote_get_json = get_json


def run(tid):
    W.remote_task(None, {"tid": tid, "cmd": "true"})
    return open(f"{BOX}/jobs/{tid}/exit").read(), open(f"{BOX}/jobs/{tid}/log").read()


rc, logtxt = run("t-ok")
assert rc == "0" and "error:" not in logtxt, (rc, logtxt)
# 5. Past REMOTE_NET_GRACE it gives up with the error in the log.
fails["n"] = -10**6
W.REMOTE_NET_GRACE = -1
rc, logtxt = run("t-down")
assert rc == "255" and "error: storage kept refusing" in logtxt, (rc, logtxt)
print("ok a queued remote job survives a Supabase outage, and gives up after the grace time")
print("all passed")
