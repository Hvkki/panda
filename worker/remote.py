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


def run_task(tid: str):
    x = TASKS[tid]
    d = f"{WORK}/remote-{tid}"
    os.makedirs(d, exist_ok=True)
    logp = f"{d}/log"
    logf = open(logp, "ab")
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
            if not W.blob_load(sb, f"remote/{RID}/tasks/{tid}/in.tar.gz", arc):
                raise RuntimeError("input files missing in storage")
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
            rc = subprocess.call(["bash", "-c", setup], cwd=cwd, stdout=logf, stderr=subprocess.STDOUT)
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
        if x.get("cancelled"):
            st["state"] = "cancelled"
            note("stopped on request")
        else:
            st["state"] = "done" if rc == 0 else "failed"
            back = files_changed_since(task.get("out") or [], started)
            if back:
                arc = f"{d}/out.tar.gz"
                with tarfile.open(arc, "w:gz", compresslevel=1) as t:
                    for rel in back:
                        t.add(os.path.join(HOME, rel), arcname=rel, recursive=False)
                W.blob_save(sb, f"remote/{RID}/tasks/{tid}/out.tar.gz", arc)
                st["out"] = {"files": len(back), "bytes": os.path.getsize(arc)}
                os.remove(arc)
            note(f"exit {rc}; sending back {len(back)} changed files")
    except Exception as exc:
        note(f"error: {str(exc)[:300]}")
        st["state"], st["exit"] = "failed", st["exit"] if st["exit"] is not None else 255
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
    beat(state="stopped")


if __name__ == "__main__":
    main()
