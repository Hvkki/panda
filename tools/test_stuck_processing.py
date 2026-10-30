"""A question left 'processing' after its answer ended locked the chat ("Panda is working").

    python3 tools/test_stuck_processing.py

1. _patch_retry gets the status write through a Supabase timeout instead of dropping it.
2. The restart sweep closes a 'processing' question whose answer is already done/error, re-queues one with
   no answer, and leaves one whose answer is still streaming alone.
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="stuck-cwd-"))
import worker as W  # noqa: E402

os.chdir(_cwd)
W.time.sleep = lambda s: None


class FlakySB:
    def __init__(self, fails):
        self.fails, self.patches = fails, []

    def rest(self, method, table, params=None, body=None):
        if self.fails:
            self.fails -= 1
            raise TimeoutError("Read timed out. (read timeout=30)")
        self.patches.append((params["id"], body))


sb = FlakySB(2)
assert W._patch_retry(sb, 7, {"status": "error"}) is True
assert sb.patches == [("eq.7", {"status": "error"})], sb.patches
assert W._patch_retry(FlakySB(9), 7, {"status": "error"}) is False   # gives up, never raises
print("ok status write retried through a Supabase timeout")

ROWS = {  # id -> row
    10: {"id": 10, "chat_id": "a", "role": "user", "status": "processing"},
    11: {"id": 11, "chat_id": "a", "role": "assistant", "status": "error", "reply_to": 10},
    20: {"id": 20, "chat_id": "b", "role": "user", "status": "processing"},
    30: {"id": 30, "chat_id": "c", "role": "user", "status": "processing"},
    31: {"id": 31, "chat_id": "c", "role": "assistant", "status": "done", "reply_to": 30},
}


class FakeSB:
    def rest(self, method, table, params=None, body=None):
        p = params or {}
        if method == "PATCH":
            ROWS[int(p["id"][3:])].update(body)
            return None
        rows = list(ROWS.values())
        if p.get("role") == "eq.assistant":
            return []   # no cut-off replies in this test
        if p.get("role") == "eq.user":
            return [r for r in rows if r["role"] == "user" and r["status"] == "processing"]
        if "reply_to" in p:
            return [r for r in rows if r.get("reply_to") == int(p["reply_to"][3:])]
        return []


W.resume_interrupted(FakeSB())
assert ROWS[10]["status"] == "error", ROWS[10]
assert ROWS[30]["status"] == "done", ROWS[30]
assert ROWS[20]["status"] == "pending", ROWS[20]
print("ok restart closes answered questions and re-queues unanswered ones")
print("all passed")
