"""Remote Panda: a second Kaggle notebook that runs heavy commands for the main Panda.

The main worker asks the `session` function (action "remote") to push this file to the account's
"<notebook>-remote-<n>" notebook. It signs in as the same worker account (worker.json in the attached
dataset), imports the live worker.py for its Supabase client and blob helpers, and then only PULLS work:
nothing listens on the network. Everything goes through the kirocrew-state bucket under
remote/<id>/ (state_key, so per account):

  queue.json                 main -> remote: {"tasks": {tid: {"cancel": bool}}, "stop": bool}  (main writes)
  tasks/<tid>/task.json      main -> remote: cmd, cwd, setup, out paths, has_input
  tasks/<tid>/in.tar.gz      main -> remote: the --files (blob_save: parts + manifest)
  tasks/<tid>/status.json    remote -> main: state, exit, log_bytes, log_from, out          (remote writes)
  tasks/<tid>/log            remote -> main: the log (whole, or its last LOG_TAIL bytes)
  tasks/<tid>/out.tar.gz     remote -> main: files under the out paths changed by the command
  beat.json                  remote -> main: heartbeat, free memory, load, running tasks, tools

It stops itself when idle for IDLE_SECS, when the owner has no live main run for 5 min, when queue.json
says stop, or at MAX_SECS (Kaggle ends runs at 12 h).
"""
import contextlib
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time

import requests

INJECTED_OWNER = ""
INJECTED_REMOTE_ID = ""
INJECTED_MAIN_SESSION = ""

OWNER = INJECTED_OWNER or os.environ.get("PANDA_REMOTE_OWNER", "")
RID = INJECTED_REMOTE_ID or os.environ.get("PANDA_REMOTE_ID", "r1")
IDLE_SECS = int(os.environ.get("PANDA_REMOTE_IDLE", "1200"))
MAX_SECS = int(os.environ.get("PANDA_REMOTE_MAX", str(11 * 3600)))
MAX_PARALLEL = int(os.environ.get("PANDA_REMOTE_PARALLEL", "2"))
LOG_TAIL = 512 * 1024
HOME = os.path.expanduser("~")
T0 = time.time()
BOOT = open("/proc/sys/kernel/random/boot_id").read().strip()


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load_worker():
    """Sign in with worker.json, fetch the live worker.py and import it (Supabase client, blob helpers)."""
    if os.environ.get("PANDA_REMOTE_WORKER_PY"):          # local tests: use the checkout
        path = os.environ["PANDA_REMOTE_WORKER_PY"]
    else:
        cfg_path = glob.glob("/kaggle/input/**/worker.json", recursive=True)[0]
        cfg = json.load(open(cfg_path))
        url = cfg["supabase_url"].rstrip("/")
        r = requests.post(f"{url}/auth/v1/token?grant_type=password", headers={"apikey": cfg["supabase_publishable_key"]},
                          json={"email": cfg["worker_email"], "password": cfg["worker_password"]}, timeout=30)
        r.raise_for_status()
        code = requests.get(f"{url}/storage/v1/object/authenticated/kirocrew-state/code/worker.py", timeout=120,
                            headers={"apikey": cfg["supabase_publishable_key"],
                                     "Authorization": f"Bearer {r.json()['access_token']}"})
        code.raise_for_status()
        path = "/kaggle/working/panda_worker.py"
        open(path, "wb").write(code.content)
    sys.path.insert(0, os.path.dirname(path))
    name = os.path.basename(path)[:-3]
    W = __import__(name)
    if OWNER:
        W.OWNER = OWNER       # state_key(): the same per-account folder the main worker uses
    return W


W = load_worker()
WORK = W.WORK
sb = W.Supabase()


def key(*parts) -> str:
    return W.state_key("/".join(["remote", RID, *parts]))


def get_json(*parts):
    raw = getattr(sb, "get_fresh", sb.get)(key(*parts))   # never a CDN copy of an overwritten object
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def put_json(obj, *parts):
    sb.put(key(*parts), json.dumps(obj).encode(), mime="application/json")


KIRO_LOCK = threading.Lock()
KIRO_CLI = os.path.join(HOME, ".local", "bin", "kiro-cli")
SAFE_ARG = re.compile(r"^[A-Za-z0-9._\-]{1,40}$")

AGENT_BRIEF = """You are a helper agent running on remote Panda, a second Kaggle machine (4 CPUs, ~30 GB) of the
user's Panda. The main Panda chat gave you the task below; nobody reads your messages live.
- Files the main chat sent are at the same paths under ~ as on the main machine.
- Put every result in {results} (and change files under the sent folders only if the task says so).
  Only those come back to the main machine. Finish by writing {results}/REPORT.md: what you did, the
  result, the paths of the files you made, and anything that failed. Keep it short.
- There is no browser, no Kiro Crew tools and no user to ask here. If you cannot do something, say so in
  REPORT.md instead of guessing.
- Save credits: combine steps, keep command output short (tail, grep), and stop when the task is done.

TASK:
{prompt}
"""


def ensure_kiro(note) -> str:
    """kiro-cli for agent tasks, installed once per remote start (~20-40 s)."""
    with KIRO_LOCK:
        if not os.path.exists(KIRO_CLI):
            t1 = time.time()
            note("installing kiro-cli for the agent")
            rc = subprocess.call(["bash", "-c", "curl -fsSL https://cli.kiro.dev/install | bash"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
            if rc != 0 or not os.path.exists(KIRO_CLI):
                raise RuntimeError(f"kiro-cli install failed (exit {rc})")
            note(f"kiro-cli ready in {time.time() - t1:.0f}s")
    return KIRO_CLI


def agent_cmd(task: dict, d: str, note) -> tuple[str, dict]:
    """The bash command and env that run an agent task headless with the account's Kiro API key."""
    ag = task["agent"]
    model, effort = ag.get("model") or "claude-sonnet-5.5", ag.get("effort") or "medium"
    if not SAFE_ARG.match(model) or not SAFE_ARG.match(effort):
        raise RuntimeError("bad model or effort name")
    results = os.path.join(HOME, ag.get("results") or "work/remote/task")
    os.makedirs(results, exist_ok=True)
    pf = f"{d}/prompt.md"
    open(pf, "w").write(AGENT_BRIEF.format(results=results, prompt=ag.get("prompt") or ""))
    cli = ensure_kiro(note)
    note(f"agent: {model} / {effort}, results in ~/{ag.get('results')}")
    cmd = (f"{shlex.quote(cli)} chat --no-interactive --trust-all-tools --wrap never "
           f"--model {model} --effort {effort} \"$(cat {shlex.quote(pf)})\"")
    env = {"KIRO_API_KEY": W.CFG["kiro_api_key"], "NO_COLOR": "1"}
    return cmd, env


TASKS: dict = {}          # tid -> {"proc", "state", "thread"}
SETUP_DONE: set = set()
LOCK = threading.Lock()


def tools() -> dict:
    return {t: bool(shutil.which(t)) for t in ("ffmpeg", "node", "npm", "git", "chromium", "nvidia-smi")}


def free_mb() -> int:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return -1


def beat(state: str = "running"):
    with LOCK:
        running = [t for t, x in TASKS.items() if x["state"] in ("starting", "running")]
    put_json({"state": state, "at": time.time(), "boot": BOOT, "started": T0, "main": INJECTED_MAIN_SESSION,
              "free_mb": free_mb(), "load": round(os.getloadavg()[0], 1), "cpus": os.cpu_count(),
              "running": running, "tools": TOOLS}, "beat.json")


def owner_live() -> bool:
    try:
        rows = sb.rest("GET", "worker_sessions", params={
            "owner": f"eq.{OWNER}", "select": "status,heartbeat_at,stop_requested",
            "order": "started_at.desc", "limit": "3"}) or []
    except Exception:
        return True          # can't tell: keep going, the idle timer still ends us
    for r in rows:
        b = W.parse_ts(r.get("heartbeat_at"))
        if r.get("status") in ("running", "stopping", "starting") and not r.get("stop_requested") \
                and b is not None and time.time() - b < 300:
            return True
    return False


def files_changed_since(paths: list, since: float) -> list:
    """Relative paths (to HOME) of files under `paths` modified at or after `since`."""
    out = []
    for p in paths:
        full = os.path.join(HOME, p)
        if os.path.isfile(full):
            cands = [full]
        else:
            cands = [os.path.join(dp, f) for dp, _, fs in os.walk(full) for f in fs]
        for c in cands:
            try:
                if os.path.isfile(c) and not os.path.islink(c) and os.path.getmtime(c) >= since:
                    out.append(os.path.relpath(c, HOME))
            except OSError:
                pass
    return out


# ── Resource guard (red-team A7) ───────────────────────────────────────────────────────────────────────
# The main worker's guard (worker.py guard_step) knows its own layout (kiro-cli-chat children, pjob jobs).
# Here every task is ONE `bash -c` started by run_task in its own session (start_new_session), so a task's
# group is that session plus any descendant that left it. Every GUARD_EVERY s: raise oom_score_adj to 1000
# on every task process (the kernel then kills a task, never remote.py), and when free memory or free disk
# on the work volume drops below its floor, stop the biggest task group and say why in its log.
# Every step is wrapped: no /proc, no cgroup files or no statvfs make it a no-op, never a crash.
GUARD_MIN_FREE = int(os.environ.get("PANDA_GUARD_MIN_FREE_MB", "2500")) << 20     # same floors as main
GUARD_MIN_DISK = int(os.environ.get("PANDA_GUARD_MIN_DISK_MB", "1024")) << 20
GUARD_EVERY = 2
GUARD_GAP = 10                       # s between two stops, so the kernel can hand the memory back first
GUARD_MARKED: dict = {}              # pid -> start ticks, so a recycled pid is marked again
GUARD_LAST_KILL = [0.0]
GUARD_STOP = threading.Event()
# Transfer caps: a runaway output must not fill this disk, the bucket or the egress.
REMOTE_MAX_TAR_MB = int(os.environ.get("REMOTE_MAX_TAR_MB", "2048"))              # out.tar.gz
REMOTE_MAX_TAR = REMOTE_MAX_TAR_MB << 20
REMOTE_MAX_IN = int(os.environ.get("REMOTE_MAX_IN_MB", "3000")) << 20             # = main's remote_max_in_mb


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def guard_procs() -> dict:
    """pid -> {ppid, sid, start, rss} from /proc; {} when /proc is unreadable."""
    out = {}
    try:
        page, names = os.sysconf("SC_PAGE_SIZE"), os.listdir("/proc")
    except (OSError, ValueError):
        return out
    for d in names:
        if not d.isdigit():
            continue
        try:
            st = _read(f"/proc/{d}/stat")
            f = st[st.rindex(")") + 2:].split()
            out[int(d)] = {"ppid": int(f[1]), "sid": int(f[3]), "start": int(f[19]), "rss": int(f[21]) * page}
        except (ValueError, IndexError):
            pass
    return out


def guard_task_groups(procs: dict, roots: dict) -> dict:
    """tid -> {"root", "pids", "rss"}: the task's session plus every descendant (one that called setsid
    too). Never pid 1 or remote.py itself."""
    kids: dict = {}
    for pid, p in procs.items():
        kids.setdefault(p["ppid"], []).append(pid)
    me, out = os.getpid(), {}
    for tid, root in roots.items():
        pids, todo = set(), [root] + [q for q, p in procs.items() if p["sid"] == root]
        while todo:
            q = todo.pop()
            if q in pids or q not in procs:
                continue
            pids.add(q)
            todo += kids.get(q, [])
        pids -= {1, me}
        if pids:
            out[tid] = {"root": root, "pids": pids, "rss": sum(procs[q]["rss"] for q in pids)}
    return out


def guard_mem_free() -> int:
    """Usable bytes: worker.py's accounting (host MemAvailable vs cgroup limit + reclaimable cache) when the
    imported worker has it, else MemAvailable alone. 1<<62 (= never low) when nothing can be read."""
    fn = getattr(W, "_guard_free_from", None)
    files = [_read(p) for p in ("/proc/meminfo", "/sys/fs/cgroup/memory.max",
                                "/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.stat")]
    if fn:
        try:
            return int(fn(*files))
        except Exception:
            pass
    for line in files[0].splitlines():
        if line.startswith("MemAvailable:"):
            try:
                return int(line.split()[1]) << 10
            except (ValueError, IndexError):
                pass
    return 1 << 62


def guard_disk_free() -> int:
    """Free bytes on the tightest of the work volume (task dirs, tars) and HOME (task files)."""
    free = 1 << 62
    for path in (WORK, HOME):
        try:
            s = os.statvfs(path)
            free = min(free, s.f_bavail * s.f_frsize)
        except OSError:
            pass
    return free


def guard_decide(groups: dict, free: int, disk: int, min_free: int, min_disk: int,
                 now: float, last_kill: float, gap: float = GUARD_GAP):
    """Pure: (tid, reason) of the task group to stop, or None."""
    low_mem, low_disk = free < min_free, disk < min_disk
    if not (low_mem or low_disk) or not groups or now - last_kill <= gap:
        return None
    tid = max(groups, key=lambda t: groups[t]["rss"])
    reason = f"memory low ({free >> 20} MB free)" if low_mem else f"disk low ({disk >> 20} MB free)"
    return tid, reason


def out_plan(cands: list, cap: int, budget: int) -> tuple:
    """Pure: which (rel, size) files go into out.tar.gz. A file bigger than `cap` is skipped; files are
    then taken in order while the total stays within min(cap, budget). Returns (keep, skipped), skipped
    as [(rel, size, why)]."""
    limit = max(0, min(cap, budget))
    keep, skipped, total = [], [], 0
    for rel, size in cands:
        if size > cap:
            skipped.append((rel, size, f"over the {cap >> 20} MB cap"))
        elif total + size > limit:
            skipped.append((rel, size, f"total would pass {limit >> 20} MB" + (" (disk low)" if budget < cap else "")))
        else:
            keep.append((rel, size))
            total += size
    return keep, skipped


def _alive(pid: int, start: int) -> bool:
    try:
        return int(_read(f"/proc/{pid}/stat").rsplit(")", 1)[1].split()[19]) == start
    except (ValueError, IndexError):
        return False


def guard_step(free_fn=None, disk_fn=None, min_free: int = None, min_disk: int = None) -> dict:
    with LOCK:
        roots = {tid: x["proc"].pid for tid, x in TASKS.items()
                 if x.get("proc") is not None and x["proc"].returncode is None}
    if not roots:
        return {"groups": 0, "marked": 0, "killed": None}
    procs = guard_procs()
    groups = guard_task_groups(procs, roots)
    marked = 0
    for g in groups.values():
        for q in g["pids"]:
            if GUARD_MARKED.get(q) == procs[q]["start"]:
                continue
            try:
                if int(_read(f"/proc/{q}/oom_score_adj").strip() or "0") < 1000:
                    with open(f"/proc/{q}/oom_score_adj", "w") as f:
                        f.write("1000")
                GUARD_MARKED[q] = procs[q]["start"]
                marked += 1
            except (OSError, ValueError):
                pass
    for q in [q for q in GUARD_MARKED if q not in procs]:
        GUARD_MARKED.pop(q, None)
    free, disk = (free_fn or guard_mem_free)(), (disk_fn or guard_disk_free)()
    pick = guard_decide(groups, free, disk, GUARD_MIN_FREE if min_free is None else min_free,
                        GUARD_MIN_DISK if min_disk is None else min_disk, time.time(), GUARD_LAST_KILL[0])
    if not pick:
        return {"groups": len(groups), "marked": marked, "killed": None}
    tid, reason = pick
    g = groups[tid]
    msg = (f"{reason}: stopped this task (pid {g['root']}, {g['rss'] >> 20} MB in {len(g['pids'])} processes) "
           f"so remote Panda keeps running. Use less memory/disk (fewer parallel workers, smaller outputs) and retry.")
    x = TASKS.get(tid) or {}
    x["guard_stopped"] = reason
    say("guard:", tid, msg)
    if x.get("log"):
        try:
            with open(x["log"], "ab") as f:
                f.write(f"\n[remote guard] {msg}\n".encode())
        except OSError:
            pass
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(OSError):
            os.killpg(g["root"], sig)
        for q in g["pids"]:                  # descendants that left the session
            with contextlib.suppress(OSError):
                if _alive(q, procs[q]["start"]):
                    os.kill(q, sig)
        if sig == signal.SIGTERM:
            time.sleep(3)
    GUARD_LAST_KILL[0] = time.time()
    return {"groups": len(groups), "marked": marked, "killed": tid}


def guard_loop(stop: threading.Event):
    while not stop.wait(GUARD_EVERY):
        try:
            guard_step()
        except Exception as exc:
            say("guard:", type(exc).__name__)


def run_task(tid: str):
    x = TASKS[tid]
    d = f"{WORK}/remote-{tid}"
    os.makedirs(d, exist_ok=True)
    logp = f"{d}/log"
    logf = open(logp, "ab")
    x["log"] = logp                      # the guard writes its reason here
    st = {"state": "starting", "exit": None, "log_bytes": 0, "log_from": 0, "out": None, "at": time.time()}

    def push_status():
        size = os.path.getsize(logp)
        frm = max(0, size - LOG_TAIL)
        with open(logp, "rb") as f:
            f.seek(frm)
            sb.put(key("tasks", tid, "log"), f.read(), mime="text/plain")
        st.update(log_bytes=size, log_from=frm, at=time.time())
        put_json(st, "tasks", tid, "status.json")

    def note(text: str):
        logf.write(f"[remote] {text}\n".encode())
        logf.flush()

    try:
        task = get_json("tasks", tid, "task.json")
        if not task:
            raise RuntimeError("task.json missing")
        put_json(st, "tasks", tid, "status.json")
        if task.get("has_input"):
            t1 = time.time()
            arc = f"{d}/in.tar.gz"
            man = None
            with contextlib.suppress(Exception):
                man = W._manifest_get(sb, f"remote/{RID}/tasks/{tid}/in.tar.gz")
            want = int((man or {}).get("size") or 0)
            room = guard_disk_free() - GUARD_MIN_DISK
            if want > REMOTE_MAX_IN or want > room:
                raise RuntimeError(f"input files refused: {want >> 20} MB packed, cap {REMOTE_MAX_IN >> 20} MB, "
                                   f"{max(0, room) >> 20} MB of disk to spare")
            if not W.blob_load(sb, f"remote/{RID}/tasks/{tid}/in.tar.gz", arc):
                raise RuntimeError("input files missing in storage")
            got = os.path.getsize(arc)
            try:
                with tarfile.open(arc) as t:
                    unpacked = sum(m.size for m in t if m.isfile())
            except (tarfile.TarError, OSError):
                unpacked = 0                 # extract_archive reports a broken archive itself
            room = guard_disk_free() - GUARD_MIN_DISK
            if got > REMOTE_MAX_IN or unpacked > room:
                os.remove(arc)
                raise RuntimeError(f"input files refused: {got >> 20} MB packed / {unpacked >> 20} MB unpacked, "
                                   f"cap {REMOTE_MAX_IN >> 20} MB packed, {max(0, room) >> 20} MB of disk to spare")
            tops = sorted({m.split("/")[0] for m in task.get("files") or []})
            ok, bad = W.extract_archive(arc, HOME, tops)
            note(f"got {ok} input files ({os.path.getsize(arc) >> 20} MB) in {time.time() - t1:.0f}s"
                 + (f", {bad} skipped" if bad else ""))
            os.remove(arc)
        cwd = os.path.join(HOME, task.get("cwd") or ".")     # relative to HOME, like the files
        os.makedirs(cwd, exist_ok=True)
        setup = (task.get("setup") or "").strip()
        h = hashlib.sha1(setup.encode()).hexdigest()
        if setup and h not in SETUP_DONE:
            note(f"setup: {setup[:200]}")
            sp = subprocess.Popen(["bash", "-c", setup], cwd=cwd, stdin=subprocess.DEVNULL, stdout=logf,
                                  stderr=subprocess.STDOUT, start_new_session=True)   # guarded + cancellable
            x["proc"] = sp
            rc = sp.wait()
            if x.get("guard_stopped"):
                raise RuntimeError(f"setup stopped by the guard: {x['guard_stopped']}")
            if x.get("cancelled"):
                raise RuntimeError("stopped on request during setup")
            if rc != 0:
                raise RuntimeError(f"setup failed with exit {rc}")
            SETUP_DONE.add(h)
        started = time.time() - 1
        cmd, extra_env = task["cmd"], {}
        if task.get("agent"):
            cmd, extra_env = agent_cmd(task, d, note)
        note(f"running on remote Panda ({os.cpu_count()} CPUs, {free_mb()} MB free)")
        p = subprocess.Popen(["bash", "-c", cmd], cwd=cwd, stdin=subprocess.DEVNULL, stdout=logf,
                             stderr=subprocess.STDOUT, start_new_session=True,
                             env={**os.environ, "PYTHONUNBUFFERED": "1", "PANDA_REMOTE": RID, **extra_env})
        x["proc"], x["state"], st["state"] = p, "running", "running"
        last = 0.0
        while p.poll() is None:
            if time.time() - last >= 5:
                push_status()
                last = time.time()
            time.sleep(1)
        rc = p.returncode
        st["exit"] = rc
        if x.get("guard_stopped"):
            st["guard"] = x["guard_stopped"]
            note(f"stopped by the remote guard: {x['guard_stopped']}")
        if x.get("cancelled"):
            st["state"] = "cancelled"
            note("stopped on request")
        else:
            st["state"] = "done" if rc == 0 else "failed"
            cands = []
            for rel in files_changed_since(task.get("out") or [], started):
                with contextlib.suppress(OSError):
                    cands.append((rel, os.path.getsize(os.path.join(HOME, rel))))
            keep, skipped = out_plan(cands, REMOTE_MAX_TAR, guard_disk_free() - GUARD_MIN_DISK)
            for rel, size, why in skipped[:50]:
                note(f"not sent back: ~/{rel} ({size >> 20} MB, {why})")
            if len(skipped) > 50:
                note(f"not sent back: {len(skipped) - 50} more files")
            back = [rel for rel, _ in keep]
            if back:
                arc = f"{d}/out.tar.gz"
                with tarfile.open(arc, "w:gz", compresslevel=1) as t:
                    for rel in back:
                        t.add(os.path.join(HOME, rel), arcname=rel, recursive=False)
                if os.path.getsize(arc) > REMOTE_MAX_TAR:      # gzip of incompressible data can grow a little
                    note(f"not sent back: out.tar.gz is {os.path.getsize(arc) >> 20} MB, over the "
                         f"{REMOTE_MAX_TAR_MB} MB cap")
                    back = []
                else:
                    W.blob_save(sb, f"remote/{RID}/tasks/{tid}/out.tar.gz", arc)
                    st["out"] = {"files": len(back), "bytes": os.path.getsize(arc)}
                os.remove(arc)
            note(f"exit {rc}; sending back {len(back)} changed files"
                 + (f", {len(skipped)} skipped by the {REMOTE_MAX_TAR_MB} MB cap" if skipped else ""))
    except Exception as exc:
        note(f"error: {str(exc)[:300]}")
        st["state"] = "cancelled" if x.get("cancelled") else "failed"
        st["exit"] = st["exit"] if st["exit"] is not None else (143 if x.get("cancelled") else 255)
    finally:
        logf.close()
        x["state"] = st["state"]
        try:
            push_status()
        except Exception as exc:
            say("status push failed", tid, exc)
        shutil.rmtree(d, ignore_errors=True)


def main():
    global TOOLS
    TOOLS = tools()
    say(f"remote Panda {RID} for {OWNER[:8]}…, tools {TOOLS}")
    threading.Thread(target=guard_loop, args=(GUARD_STOP,), daemon=True, name="guard").start()
    beat()
    last_beat = last_owner_ok = time.time()
    last_work = time.time()
    reason = "?"
    while True:
        q = get_json("queue.json") or {}
        tasks = q.get("tasks") or {}
        with LOCK:
            for tid, flags in tasks.items():
                if tid not in TASKS:
                    TASKS[tid] = {"state": "queued", "proc": None}
                x = TASKS[tid]
                if flags.get("cancel") and x["state"] in ("queued", "starting", "running") and not x.get("cancelled"):
                    x["cancelled"] = True
                    if x.get("proc") and x["proc"].poll() is None:
                        try:
                            os.killpg(x["proc"].pid, signal.SIGTERM)
                            threading.Timer(5, lambda pr=x["proc"]: pr.poll() is None and os.killpg(pr.pid, signal.SIGKILL)).start()
                        except OSError:
                            pass
                    elif x["state"] == "queued":
                        x["state"] = "cancelled"
                        put_json({"state": "cancelled", "exit": 143, "log_bytes": 0, "log_from": 0, "out": None,
                                  "at": time.time()}, "tasks", tid, "status.json")
            busy = sum(1 for x in TASKS.values() if x["state"] in ("starting", "running"))
            for tid, x in TASKS.items():
                if x["state"] == "queued" and busy < MAX_PARALLEL and tid in tasks:
                    x["state"] = "starting"
                    x["thread"] = threading.Thread(target=run_task, args=(tid,), daemon=True)
                    x["thread"].start()
                    busy += 1
            for tid in [t for t, x in TASKS.items() if t not in tasks and x["state"] not in ("starting", "running")]:
                TASKS.pop(tid, None)      # main removed it from the queue: it has the result
        now = time.time()
        if busy or tasks:
            last_work = now
        if now - last_beat >= 20:
            beat()
            last_beat = now
        if now - last_owner_ok >= 60:
            if owner_live():
                last_owner_ok = now
            elif now - last_owner_ok >= 300 and not busy:
                reason = "no live main Panda for 5 min"
                break
        if q.get("stop") and not busy:
            reason = "asked to stop"
            break
        if now - last_work >= IDLE_SECS:
            reason = f"idle for {IDLE_SECS // 60} min"
            break
        if now - T0 >= MAX_SECS:
            reason = "time limit"
            break
        time.sleep(3)
    say("stopping:", reason)
    GUARD_STOP.set()
    beat(state="stopped")


if __name__ == "__main__":
    main()
