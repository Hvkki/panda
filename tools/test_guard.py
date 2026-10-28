"""Resource guard test: marks agent commands expendable (oom 1000, nice 10) and, on low memory, stops
the biggest one. Uses a temp jobs dir and a fake memory reading; never touches Panda's own processes.
Run: python3 tools/test_guard.py [--list]"""
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
import worker as W  # noqa: E402

if "--list" in sys.argv:
    for g in W.guard_groups(W._guard_procs()):
        print(g["rss"] >> 20, "MB", g["job"] or W._guard_cmdline(g["root"])[:90])
    print("free MB", W.guard_free() >> 20)
    sys.exit(0)

tmp = tempfile.mkdtemp()
W.JOBS_DIR, W.GUARD_LOG = f"{tmp}/jobs", f"{tmp}/guard.log"
d = f"{W.JOBS_DIR}/hog"
os.makedirs(d)
# A pjob-style job: own session, a child holding ~1.5 GB.
p = subprocess.Popen(["bash", "-c", "python3 -c 'b=bytearray(1500<<20); import time; time.sleep(120)' & wait"],
                     start_new_session=True)
open(f"{d}/pid", "w").write(str(p.pid))
open(f"{d}/log", "w").write("working\n")
time.sleep(1.5)

r = W.guard_step(free_fn=lambda: 1 << 40)
job = [g for g in W.guard_groups(W._guard_procs()) if g["job"] == "hog"][0]
assert r["killed"] is None and len(job["pids"]) >= 2, r
for q in job["pids"]:
    assert open(f"/proc/{q}/oom_score_adj").read().strip() == "1000", q
    assert os.getpriority(os.PRIO_PROCESS, q) >= 10, q
assert open(f"/proc/{os.getppid()}/oom_score_adj").read().strip() == "1000"   # my tool shell, too
assert open(f"/proc/{os.getpid()}/oom_score_adj").read().strip() == "1000"
gw = [q for q, x in W._guard_procs().items() if "kirocrew gateway" in W._guard_cmdline(q)]
assert gw and all(open(f"/proc/{q}/oom_score_adj").read().strip() == "0" for q in gw), "gateway marked!"
print(f"ok mark: job {len(job['pids'])} procs, {job['rss'] >> 20} MB, oom 1000 nice 10; gateway untouched")

r = W.guard_step(free_fn=lambda: 100 << 20)
assert r["killed"] and r["killed"]["job"] == "hog", r["killed"]
time.sleep(0.5)
p.poll()
assert p.returncode is not None, "job still alive"
assert all(not os.path.exists(f"/proc/{q}") or "Z" in open(f"/proc/{q}/stat").read().split(") ")[1][:1]
           for q in job["pids"])
assert "[Panda guard]" in open(f"{d}/log").read()
print("ok kill:", open(W.GUARD_LOG).read().strip()[:160])
r = W.guard_step(free_fn=lambda: 100 << 20)
assert r["killed"] is None, "cooldown ignored"
print("ok cooldown")
