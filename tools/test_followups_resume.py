"""B1 test: a restart must not silently drop chats awaiting helpers/jobs. persist_followups mirrors the
in-memory FOLLOW set to Storage; resume_followups re-queues a continue for each still-fresh chat and
clears the mirror. Fake Supabase, no network.
Run: python3 tools/test_followups_resume.py"""
import json
import os
import sys
import time

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
import worker as W  # noqa: E402


class FakeSB:
    def __init__(self):
        self.store = {}       # Storage blobs
        self.posts = []       # queued messages
        self.pending = set()  # chat ids that already have a pending continue

    def put(self, key, data, mime=None):
        self.store[key] = data

    def get_fresh(self, key):
        return self.store.get(key)

    def delete_object(self, key):
        self.store.pop(key, None)

    def rest(self, method, table, params=None, body=None, **kw):
        params = params or {}
        if method == "GET" and table == "messages":
            cid = (params.get("chat_id") or "").replace("eq.", "")
            return [{"id": "x"}] if cid in self.pending else []
        if method == "POST" and table == "messages":
            self.posts.append(body)
            return [{"id": "new"}]
        return []


W.state_key = lambda k: f"state/{k}"
W.OWNER = "owner-1"
sb = FakeSB()
now = time.time()

# persist mirrors FOLLOW, change-gated.
W._FOLLOW_SIG[0] = None
W.FOLLOW = {"c1": {"owner": "owner-1", "slot": "s1", "until": now + 3600, "row": None}}
W.persist_followups(sb)
assert W.state_key(W.FOLLOWUPS_KEY) in sb.store, "FOLLOW not mirrored to Storage"
_writes = len(sb.store)
W.persist_followups(sb)  # no change -> no rewrite
assert W._FOLLOW_SIG[0] == ("c1",), W._FOLLOW_SIG[0]
print("ok persist: FOLLOW mirrored, change-gated")

# A restart: blob has a fresh chat, an expired one, and one already continued.
sb.store[W.state_key(W.FOLLOWUPS_KEY)] = json.dumps({
    "c1": {"owner": "owner-1", "slot": "s1", "until": now + 3600},   # fresh -> continue
    "c2": {"owner": "owner-1", "slot": "s2", "until": now - 10},     # expired -> skip
    "c3": {"owner": "owner-1", "slot": "s3", "until": now + 3600},   # already pending -> skip
}).encode()
sb.pending = {"c3"}
W.resume_followups(sb)

chats = {p["chat_id"] for p in sb.posts}
assert chats == {"c1"}, f"expected only c1 continued, got {chats}"
assert sb.posts[0]["content"] == W.FOLLOW_CONTINUE_TEXT, "wrong continue text"
assert sb.posts[0]["meta"]["auto_continue"] is True, sb.posts[0]["meta"]
assert W.state_key(W.FOLLOWUPS_KEY) not in sb.store, "mirror not cleared after resume"
print("ok resume: fresh chat continued; expired + already-pending skipped; mirror cleared")

# Empty / missing mirror is a clean no-op.
sb.posts.clear()
W.resume_followups(sb)
assert sb.posts == [], "should do nothing with no mirror"
print("ok empty: no mirror -> no continues")
print("all passed")
