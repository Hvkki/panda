"""Offline test of remote.py's resource guard and transfer cap (red-team A7). No network, no Supabase:
remote.py is imported against a fake worker module. Run: python3 tools/test_remote_guard.py
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="rguard-")
FAKE = os.path.join(TMP, "fake_worker.py")
open(FAKE, "w").write(f'''
WORK = {TMP!r}
OWNER = ""
CFG = {{}}
class Supabase:
    pass
def state_key(name):
    return name
''')
os.environ["PANDA_REMOTE_WORKER_PY"] = FAKE
sys.path.insert(0, os.path.join(HERE, "..", "worker"))
import remote as R  # noqa: E402

MB = 1 << 20
fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# 1. stop decision
G = {"a": {"rss": 100 * MB}, "b": {"rss": 900 * MB}}
check("plenty of memory and disk -> no stop", R.guard_decide(G, 8000 * MB, 9000 * MB, 2500 * MB, 1024 * MB, 100, 0) is None)
d = R.guard_decide(G, 1000 * MB, 9000 * MB, 2500 * MB, 1024 * MB, 100, 0)
check("low memory -> stop the biggest group", d and d[0] == "b" and "memory low (1000 MB" in d[1])
d = R.guard_decide(G, 8000 * MB, 500 * MB, 2500 * MB, 1024 * MB, 100, 0)
check("low disk -> stop, reason says disk", d and d[0] == "b" and "disk low (500 MB" in d[1])
check("within GUARD_GAP of the last stop -> wait", R.guard_decide(G, 0, 0, 2500 * MB, 1024 * MB, 105, 100) is None)
check("no task groups -> nothing to stop", R.guard_decide({}, 0, 0, 2500 * MB, 1024 * MB, 100, 0) is None)

# 2. out tar cap
cands = [("o/a", 10 * MB), ("o/huge", 3000 * MB), ("o/b", 1500 * MB), ("o/c", 700 * MB)]
keep, skipped = R.out_plan(cands, 2048 * MB, 1 << 62)
check("file over the cap skipped, total kept within cap",
      [k for k, _ in keep] == ["o/a", "o/b"] and {s[0] for s in skipped} == {"o/huge", "o/c"})
keep, skipped = R.out_plan(cands, 2048 * MB, 100 * MB)
check("low disk budget shrinks the limit", [k for k, _ in keep] == ["o/a"] and "disk low" in skipped[-1][2])
keep, skipped = R.out_plan(cands, 2048 * MB, -5)
check("negative budget -> nothing sent", keep == [] and len(skipped) == 4)
check("default cap is 2048 MB", R.REMOTE_MAX_TAR == 2048 * MB)

# 3. memory reading degrades safely
check("fake worker without _guard_free_from -> MemAvailable or 'never low'", R.guard_mem_free() > 0)
R.W._guard_free_from = lambda *a: 1 / 0
check("a raising worker helper falls back", R.guard_mem_free() > 0)
del R.W._guard_free_from

# 4. group discovery (fake /proc)
procs = {1: {"ppid": 0, "sid": 1, "start": 1, "rss": MB}, 500: {"ppid": 1, "sid": 500, "start": 2, "rss": MB},
         501: {"ppid": 500, "sid": 500, "start": 3, "rss": 2 * MB}, 502: {"ppid": 501, "sid": 502, "start": 4, "rss": 4 * MB},
         600: {"ppid": 1, "sid": 600, "start": 5, "rss": 64 * MB}}
g = R.guard_task_groups(procs, {"t": 500})
check("session + setsid descendant, not pid 1 or unrelated", g["t"]["pids"] == {500, 501, 502} and g["t"]["rss"] == 7 * MB)

# 5. live: real process group gets oom 1000, then is stopped on low memory with a log line
check("no running tasks -> no-op", R.guard_step()["groups"] == 0)
logp = os.path.join(TMP, "log")
open(logp, "w").close()
p = subprocess.Popen(["bash", "-c", "sleep 60 & sleep 60; wait"], start_new_session=True)
R.TASKS["t1"] = {"state": "running", "proc": p, "log": logp}
time.sleep(0.5)
r = R.guard_step(free_fn=lambda: 1 << 40, disk_fn=lambda: 1 << 40)
kids = [q for q, v in R.guard_procs().items() if v["sid"] == p.pid]
oom = [int(open(f"/proc/{q}/oom_score_adj").read()) for q in kids]
check(f"task group marked oom_score_adj=1000 ({len(kids)} procs)", r["killed"] is None and oom and all(v == 1000 for v in oom))
check("remote.py itself not marked", os.getpid() not in R.GUARD_MARKED)
r = R.guard_step(free_fn=lambda: 100 * MB, disk_fn=lambda: 1 << 40)
p.wait(timeout=10)
time.sleep(0.3)
left = [q for q, v in R.guard_procs().items() if v["sid"] == p.pid]
check("low memory -> task group stopped", r["killed"] == "t1" and p.returncode is not None and not left)
check("reason written to the task log", "[remote guard] memory low (100 MB free)" in open(logp).read())
check("task flagged guard_stopped", R.TASKS["t1"].get("guard_stopped", "").startswith("memory low"))

print("PASS" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
