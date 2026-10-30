"""Kept-alive Supabase connections: calls reuse one connection per thread and get faster; Storage still
round-trips. Uses the real project as Panda's worker (worker.json), under a throwaway folder.

    python3 tools/test_keepalive.py
"""
import os
import statistics
import sys
import tempfile
import threading
import time
import uuid

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
os.chdir(tempfile.mkdtemp())
import worker as W  # noqa: E402

sb = W.Supabase()


def median_ms(fn, n=10):
    ts = []
    for _ in range(n):
        t0 = time.time()
        fn()
        ts.append((time.time() - t0) * 1000)
    return statistics.median(ts[1:])


def old_way():          # what every call did before: a fresh connection
    r = requests.get(f"{W.SB_URL}/rest/v1/chats", params={"select": "id", "limit": "1"}, headers=sb.headers(), timeout=30)
    r.raise_for_status()


new = median_ms(lambda: sb.rest("GET", "chats", params={"select": "id", "limit": "1"}))
old = median_ms(old_way)
print(f"ok latency: {old:.0f} ms per call before, {new:.0f} ms kept-alive")
assert new < old, "kept-alive should not be slower"

mine = W.http()
assert W.http() is mine, "the same thread reuses its session"
other = []
t = threading.Thread(target=lambda: other.append(W.http()))
t.start()
t.join()
assert other[0] is not mine, "each thread has its own session"
print("ok sessions: one per thread, reused")

W.OWNER = f"test-keepalive-{uuid.uuid4().hex[:8]}"
key = W.state_key("probe.bin")
data = os.urandom(300 << 10)
src = os.path.join(tempfile.mkdtemp(), "p.bin")
open(src, "wb").write(data)
try:
    sb.upload(key, src)
    assert sb.get(key) == data and sb.get_fresh(key) == data
    dest = src + ".back"
    assert sb.download(key, dest) and open(dest, "rb").read() == data
    assert sb.download(W.state_key("missing.bin"), dest) is False
    names = [o["name"] for o in sb.list(W.state_key("").rstrip("/"))]
    assert key in names, names
    for _ in range(30):       # early returns must hand the connection back, or the pool runs dry
        sb.download(W.state_key("missing.bin"), dest)
    print("ok storage: upload, get, fresh get, download, missing object, list; no leaked connections")
finally:
    sb.delete_object(key)
print("all passed")
