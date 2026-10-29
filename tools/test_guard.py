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

# --- guard_free reclaimable-cache unit test (pure, no processes) --------------------------------
# Bug 2026-10-29: on a box with 16.6 GB of file cache (mostly ACTIVE), the guard counted the cache
# as used, saw ~1.3 GB free, and killed every job. Clean page cache (inactive_file + active_file)
# is reclaimed before OOM and must read as free.
_g, _kb = 1 << 30, 1 << 10
_meminfo = f"MemTotal: {32 * _g // _kb} kB\nMemAvailable: {22 * _g // _kb} kB\n"
_mem_max, _mem_cur = f"{32 * _g}\n", f"{int(30.9 * _g)}\n"
_stat_full = (f"anon {9 * _g}\nfile {int(16.6 * _g)}\n"
              f"inactive_file {int(1.3 * _g)}\nactive_file {int(15.3 * _g)}\n")
_free_new = W._guard_free_from(_meminfo, _mem_max, _mem_cur, _stat_full)
assert _free_new >> 20 > 10000, f"active_file not counted as free: {_free_new >> 20} MB"
# Old behaviour (inactive_file only) would have tripped the 2500 MB floor -- that was the bug.
_free_old = W._guard_free_from(_meminfo, _mem_max, _mem_cur, f"anon {9 * _g}\ninactive_file {int(1.3 * _g)}\n")
assert _free_old >> 20 < 2500, f"expected inactive-only logic to look low, got {_free_old >> 20} MB"
# A genuinely full box (no reclaimable cache, low MemAvailable) must still read as low.
_free_fullbox = W._guard_free_from(f"MemAvailable: {int(0.4 * _g) // _kb} kB\n", _mem_max,
                                   f"{int(31.6 * _g)}\n", f"anon {32 * _g}\n")
assert _free_fullbox >> 20 < 2500, f"full box should read low, got {_free_fullbox >> 20} MB"
# No cgroup limit -> just the host's MemAvailable.
assert W._guard_free_from(_meminfo, "max\n", _mem_cur, _stat_full) >> 20 == 22 * 1024
print(f"ok guard_free: active+inactive cache counts as free ({_free_new >> 20} MB vs inactive-only {_free_old >> 20} MB)")

# --- OOM-protection pass: raise large NON-core procs to 1000 so the kernel never kills the gateway.
# The container cannot lower the gateway's own score, so protection is relative. The old group-only
# marking missed a detached/re-parented hog (not a pjob, not a kiro-cli-chat child) -- this closes it.
_procs = {
    1:    {"comm": "python3", "rss": 500 << 20, "start": 1},        # worker -> skip (pid 1)
    406:  {"comm": "kirocrew", "rss": 500 << 20, "start": 2},       # gateway -> skip (cmdline)
    900:  {"comm": "kiro-cli-chat", "rss": 300 << 20, "start": 3},  # chat/agent runner -> skip (comm)
    901:  {"comm": "python3", "rss": 300 << 20, "start": 4},        # MCP server -> skip (cmdline)
    1234: {"comm": "python3", "rss": 800 << 20, "start": 5},        # DETACHED HOG -> target
    1235: {"comm": "node", "rss": 10 << 20, "start": 6},            # too small -> skip
}
_cmd = {406: "kirocrew gateway --no-open --no-tunnel", 901: "python3 -m kirocrew mcp-core"}
_targets = W._guard_protect_targets(_procs, 64 << 20, cmd_fn=lambda q: _cmd.get(q, ""))
assert _targets == [1234], f"OOM-protect targets wrong: {_targets}"
assert W._guard_disk_free("/") > 0, "disk free read failed"
print("ok oom-protect: only the large non-core hog is raised; gateway/mcp/chat/worker skipped")

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

# The kill step must only ever see the test's own job: on the live machine the biggest group can be a
# real command of another chat (this test once stopped a 3.9 GB one that way).
_real_groups = W.guard_groups
W.guard_groups = lambda procs: [g for g in _real_groups(procs) if g["job"] == "hog"]

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

# Disk arm: memory is healthy, but low free disk on WORK must also stop the biggest group.
W.GUARD_LAST_KILL[0] = 0
p2 = subprocess.Popen(["bash", "-c", "python3 -c 'b=bytearray(200<<20); import time; time.sleep(60)' & wait"],
                      start_new_session=True)
d2 = f"{W.JOBS_DIR}/hog2"
os.makedirs(d2)
open(f"{d2}/pid", "w").write(str(p2.pid))
open(f"{d2}/log", "w").write("working\n")
time.sleep(1.5)
W.guard_groups = lambda procs: [g for g in _real_groups(procs) if g["job"] == "hog2"]
r = W.guard_step(free_fn=lambda: 1 << 40, disk_fn=lambda: 100 << 20, min_disk=1 << 30)
assert r["killed"] and r["killed"]["job"] == "hog2", r["killed"]
time.sleep(0.5)
p2.poll()
assert p2.returncode is not None, "disk arm did not stop the job"
assert "disk low" in open(W.GUARD_LOG).read(), open(W.GUARD_LOG).read()[-200:]
print("ok disk-arm:", open(W.GUARD_LOG).read().strip().splitlines()[-1][:120])
