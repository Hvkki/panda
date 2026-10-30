"""Remote Panda pool: up to 5 notebooks (r1-r5) and how remote_pick spreads tasks over them. No network.

    python3 tools/test_remote_pool.py
"""
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
os.environ["PANDA_REMOTE_BOX"] = tempfile.mkdtemp(prefix="pool-box-")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="pool-cwd-"))
import worker as W  # noqa: E402

os.chdir(_cwd)
assert W.REMOTE_IDS == ["r1", "r2", "r3", "r4", "r5"], W.REMOTE_IDS
BEATS: dict = {}
W.remote_get_json = lambda sb, rid, *p: BEATS.get(rid) if p == ("beat.json",) else None


def up(*rids):
    for r in rids:
        BEATS[r] = {"state": "running", "at": time.time(), "running": []}


def reset():
    BEATS.clear()
    for r in W.REMOTE_IDS:
        W.REMOTE[r] = {"asked": 0.0, "queue": {"tasks": {}}, "failed": 0.0}


def queue(rid, tid):     # what remote_task does once the task is on the notebook's queue
    W.remote_release(rid, tid)
    W.REMOTE[rid]["queue"]["tasks"][tid] = {"cancel": False}


# 1. Nothing running: the first task starts r1; the 2nd shares r1 (2 slots each) while it boots.
reset()
a = W.remote_pick(None, "t1")
W.REMOTE[a]["asked"] = time.time()     # remote_ensure asked Kaggle for it
b = W.remote_pick(None, "t2")
assert (a, b) == ("r1", "r1"), (a, b)
# 2. r1 full: the next tasks open r2, r3, r4, r5 in turn.
got = []
for i in range(3, 11):
    r = W.remote_pick(None, f"t{i}")
    W.REMOTE[r]["asked"] = W.REMOTE[r]["asked"] or time.time()
    got.append(r)
assert got == ["r2", "r2", "r3", "r3", "r4", "r4", "r5", "r5"], got
# 3. All 5 full: a waiting task goes to the least busy one, never a 6th notebook.
queue("r3", "t5")
W.remote_release("r3", "t6")           # t6 finished on r3
r = W.remote_pick(None, "t11")
assert r == "r3", r
print("ok fills r1..r5 two tasks each, then waits on the least busy, never a 6th")

# 4. A live notebook with a free slot beats starting a new one.
reset()
up("r2")
assert W.remote_pick(None, "x") == "r2"
# 5. A notebook whose start just failed (e.g. Kaggle's limit) is skipped for 10 min.
reset()
W.REMOTE["r1"]["failed"] = time.time()
assert W.remote_pick(None, "y") == "r2"
# 6. avoid= (the retry after a failed start) is respected.
reset()
assert W.remote_pick(None, "z", avoid=["r1", "r2"]) == "r3"
print("ok prefers live notebooks, skips a failed start, honours avoid")

# 7. remote_status lists every notebook that ever beat, and the panel's old fields still describe one.
reset()
up("r1", "r4")
st = W.remote_status(None)
assert [x["id"] for x in st["boxes"]] == ["r1", "r4"] and st["alive"] and st["max"] == 5, st
assert st["remote_id"] == "r1" and st["state"] == "running", st
print("ok status lists all live notebooks")
print("all passed")
