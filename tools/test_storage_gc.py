"""storage_gc test: delete only kiro-chunks the current index does not reference AND older than the
grace window; keep referenced chunks and recently-uploaded ones. Fake Supabase, no network.
Run: python3 tools/test_storage_gc.py"""
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

W.state_key = lambda k: k
now = time.time()
import datetime
iso = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")

INDEX = {"v": 1, "chunk": 8 << 20, "files": {
    "sessions/cli/a.jsonl": {"size": 10, "mtime_ns": 1, "chunks": ["aaaa", "bbbb"]},
}}
# chunks in storage: aaaa/bbbb referenced; cccc old orphan (delete); dddd fresh orphan (keep)
OBJS = [
    {"name": "kiro-chunks/aaaa.z", "size": 1 << 20, "updated_at": iso(now - 7200)},
    {"name": "kiro-chunks/bbbb.z", "size": 1 << 20, "updated_at": iso(now - 7200)},
    {"name": "kiro-chunks/cccc.z", "size": 5 << 20, "updated_at": iso(now - 7200)},  # orphan, old -> delete
    {"name": "kiro-chunks/dddd.z", "size": 3 << 20, "updated_at": iso(now - 60)},    # orphan, fresh -> keep
]
deleted = []


class FakeSB:
    def get_fresh(self, name, bucket=W.BUCKET):
        return json.dumps(INDEX).encode() if name == "kiro-index.json" else None
    def list(self, prefix, bucket=W.BUCKET):
        assert prefix == "kiro-chunks", prefix
        return OBJS
    def list_tree(self, prefix, bucket=W.BUCKET):
        return []   # no stale remote tars in this test
    def delete_object(self, name, bucket=W.BUCKET):
        deleted.append(name)

r = W.storage_gc(FakeSB())
assert r["removed"] == 1 and r["referenced"] == 2 and r["total"] == 4, r
assert deleted == ["kiro-chunks/cccc.z"], deleted
print("ok storage_gc:", r, "deleted", deleted)

# No index -> no-op.
class Empty:
    def get_fresh(self, name, bucket=W.BUCKET):
        return None
assert W.storage_gc(Empty()).get("skipped") == "no index"
print("ok storage_gc: no index -> no-op")
print("all passed")
