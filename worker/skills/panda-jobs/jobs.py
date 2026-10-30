#!/usr/bin/env python3
"""pjob: run long commands in the background, keep working, and check on them.

  pjob start NAME -- COMMAND...      start COMMAND in the background (its own log, survives your turn's steps)
  pjob start NAME --sh 'a && b'      the same, with a shell line
  pjob start NAME --notify [--want 'what to report'] [--stall 600] -- COMMAND
                                     Panda wakes this chat when it ends or looks stuck (no polling)
  pjob start NAME --remote [--files P ...] [--out P ...] [--setup 'CMD'] --notify -- COMMAND
                                     run it on remote Panda (its own 4 CPUs / ~30 GB), files sent and brought back
  pjob start NAME --remote --agent [--model M] [--effort E] [--files P ...] --notify -- "TASK TEXT"
                                     an agent does TASK on remote Panda; results come back in ~/work/remote/NAME
  pjob status [NAME]                 running / done (exit code) / lost, with run time and the last log lines
  pjob wait NAME [--max 300]         block until NAME ends or --max seconds pass (default 240), then status
  pjob wait-any A B C [--max 300]    block until the first of them ends
  pjob log NAME [-n 40]              last lines of the log
  pjob stop NAME                     stop it: every process of the job (also `timeout`-wrapped commands)
  pjob list                          every job, newest first

Jobs live in ~/work/jobs/<NAME>/ (cmd, pid, log, exit, started, ended). A job that was running when
Panda restarted shows as "lost": its process is gone, but its log and partial output stay.
"""
import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time

ROOT = os.path.expanduser("~/work/jobs")
BOOT = "/proc/sys/kernel/random/boot_id"


def _boot() -> str:
    try:
        return open(BOOT).read().strip()
    except OSError:
        return ""


def _dir(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,47}", name or ""):
        sys.exit("pjob: NAME must be letters, digits, . _ - (max 48)")
    return os.path.join(ROOT, name)


def _read(d: str, f: str, default=""):
    try:
        return open(os.path.join(d, f)).read().strip()
    except OSError:
        return default


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A zombie or a recycled pid is not our job.
    try:
        stat = open(f"/proc/{pid}/stat").read()
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def state(name: str) -> dict:
    d = _dir(name)
    if not os.path.isdir(d):
        return {"name": name, "state": "missing"}
    started = float(_read(d, "started", "0") or 0)
    ended = _read(d, "ended")
    ex = _read(d, "exit")
    pid = int(_read(d, "pid", "0") or 0)
    if ex != "":
        st = "done" if ex == "0" else "stopped" if ex in ("-15", "143", "137") else "failed"
    elif pid and _read(d, "boot") == _boot() and _alive(pid):
        st = "running"
    else:
        st = "lost"   # Panda restarted (or the process was killed) before it finished
    end = float(ended) if ended else time.time()
    out = {"name": name, "state": st, "exit": int(ex) if ex.lstrip("-").isdigit() else None,
           "secs": round(end - started) if started else None, "cmd": _read(d, "cmd")[:300],
           "log": os.path.join(d, "log")}
    guard = _read(d, "guard")
    if guard and st != "running":
        out["guard"] = guard[:300]     # Panda's resource guard stopped it (low memory or disk)
    return out


def tail(name: str, n: int = 15) -> str:
    path = os.path.join(_dir(name), "log")
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    # progress bars rewrite one line with \r: keep only the last state of each line
    lines = [ln.split("\r")[-1] for ln in data.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines[-n:])


def show(s: dict, n: int = 12):
    t = f"{s['secs']}s" if s.get("secs") is not None else "?"
    extra = f" exit={s['exit']}" if s.get("exit") is not None else ""
    print(f"{s['name']}: {s['state']}{extra} after {t}")
    if s.get("guard"):
        print(f"  stopped by Panda's guard: {s['guard']}")
    if s["state"] != "missing" and n:
        out = tail(s["name"], n)
        if out:
            print("--- last log lines ---")
            print(out)


SESSION_MAP = os.path.expanduser("~/.kiro/crew/session_map.json")
CREW_DIR = os.path.expanduser("~/.kiro/crew")


def _stat(pid: int) -> list:
    """Fields of /proc/PID/stat after the command name: [0] state, [1] ppid, [19] start time."""
    try:
        return open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return []


def slot_of_pid(pid: int) -> str:
    """The chat slot a process belongs to, found by walking up to the chat's kiro-cli launcher.

    Kiro Crew writes ~/.kiro/crew/session_pid_<launcher pid>.txt = '<slot key>\\n<start time>' for each
    chat's session (helpers get none). The start time guards against a reused pid or a file restored
    from an older run. This does not depend on KIRO_SESSION_ID, which can go stale in session_map.json.
    """
    for _ in range(64):
        f = _stat(pid)
        if not f:
            return ""
        try:
            key, start = (open(os.path.join(CREW_DIR, f"session_pid_{pid}.txt")).read().split("\n") + [""])[:2]
        except OSError:
            key = start = ""
        if key.strip() and start.strip() == f[19]:
            key = key.strip()
            return key.split(":", 1)[1] if ":" in key else key
        pid = int(f[1])
        if pid <= 1:
            return ""
    return ""


def _procs() -> dict:
    """pid -> (ppid, session id, start time) for every live process."""
    out = {}
    for n in os.listdir("/proc"):
        if n.isdigit():
            f = _stat(int(n))
            if len(f) > 19 and f[0] != "Z":
                try:
                    out[int(n)] = (int(f[1]), int(f[3]), f[19])
                except ValueError:
                    pass
    return out


def job_procs(pid: int) -> dict:
    """Every process of the job whose wrapper is `pid`: pid -> start time.

    pjob starts each job in its own session, so its members share session id == pid even after the
    wrapper dies and they are re-parented to PID 1. GNU `timeout` (and anything else that calls
    setpgid) leaves the process GROUP but stays in the session, which is why stopping only the group
    used to leave `timeout -k 10 600 node render.mjs` running. Descendants by parent pid are added too,
    for a command that started its own session (setsid). Linux never reuses a pid while it is still a
    live session id, so a dead leader's session members are always this job's."""
    procs = _procs()
    if not pid or pid <= 1:
        return {}
    kids: dict = {}
    for q, (pp, _, _) in procs.items():
        kids.setdefault(pp, []).append(q)
    keep = {os.getpid()}
    q = os.getpid()
    for _ in range(64):        # never stop pjob itself or the shell that called it
        f = _stat(q)
        if not f or int(f[1]) <= 1:
            break
        q = int(f[1])
        keep.add(q)
    todo = [q for q, (_, sid, _) in procs.items() if sid == pid] + ([pid] if pid in procs else [])
    seen: dict = {}
    while todo:
        q = todo.pop()
        if q in seen or q not in procs or q <= 1 or q in keep:
            continue
        seen[q] = procs[q][2]
        todo += kids.get(q, [])
    return seen


def stop_procs(pid: int, grace: float = 3.0) -> int:
    """TERM every process of the job, wait up to `grace` seconds, then KILL what is left (also anything
    the job started meanwhile). Returns how many processes were signalled."""
    hit = job_procs(pid)

    def live(q, start):
        f = _stat(q)
        return len(f) > 19 and f[19] == start and f[0] != "Z"

    for q, start in hit.items():
        if live(q, start):
            try:
                os.kill(q, signal.SIGTERM)
            except OSError:
                pass
    t0 = time.time()
    while time.time() - t0 < grace and any(live(q, s) for q, s in hit.items()):
        time.sleep(0.2)
    rest = {**job_procs(pid), **{q: s for q, s in hit.items() if live(q, s)}}
    for q, start in rest.items():
        if live(q, start):
            try:
                os.kill(q, signal.SIGKILL)
            except OSError:
                pass
    return len(set(hit) | set(rest))


def my_slot() -> str:
    """The Kiro Crew chat slot this shell belongs to ('' in a helper run or outside any chat)."""
    slot = slot_of_pid(os.getpid())
    if slot:
        return slot
    sid = os.environ.get("KIRO_SESSION_ID", "")  # fallback: session_map 'dashboard:<slot>' -> {sid}
    if not sid:
        return ""
    try:
        m = json.load(open(SESSION_MAP))
    except (OSError, ValueError):
        return ""
    for key, v in (m.items() if isinstance(m, dict) else []):
        if isinstance(v, dict) and v.get("sid") == sid:
            return key.split(":", 1)[1] if ":" in key else key
    return ""


def cmd_start(a, rest):
    d = _dir(a.name)
    if os.path.isdir(d) and state(a.name)["state"] == "running":
        sys.exit(f"pjob: {a.name} is already running (pjob status {a.name})")
    line = a.sh if a.sh else shlex.join(rest)
    if not line.strip():
        sys.exit("pjob: give a command after -- or use --sh")
    shown = line
    if a.agent and not a.remote:
        sys.exit("pjob: --agent needs --remote (for a local helper agent use spawn_run)")
    if a.remote:
        home = os.path.expanduser("~")
        for p in a.files or []:
            full = os.path.realpath(os.path.expanduser(p))
            if not full.startswith(home + "/") or not os.path.exists(full):
                sys.exit(f"pjob: --files {p}: not found under {home}")
        premote = os.path.join(os.path.dirname(os.path.abspath(__file__)), "premote.py")
        extra = []
        if a.agent:
            # The task text is the "command"; the agent's results land in ~/work/remote/NAME, brought back
            # with anything under --out (default: --files).
            line = a.sh if a.sh else " ".join(rest)
            extra = ["--agent", f"--model={a.model or 'claude-sonnet-5.5'}", f"--effort={a.effort or 'medium'}"]
        line = shlex.join([sys.executable, premote, "run", "--name", a.name, "--cwd", a.cwd or os.getcwd(),
                           "--files", *(a.files or []), "--out", *(a.out or []), f"--setup={a.setup or ''}",
                           *extra, f"--cmd={line}"])
        shown = f"remote {'agent' if a.agent else ''}: {shown}".replace("remote :", "remote:")
    os.makedirs(d, exist_ok=True)
    for f in ("exit", "ended", "log", "notify", "notified", "watch.json", "guard", "slot"):
        try:
            os.remove(os.path.join(d, f))
        except OSError:
            pass
    open(os.path.join(d, "cmd"), "w").write(shown)
    open(os.path.join(d, "started"), "w").write(str(time.time()))
    open(os.path.join(d, "boot"), "w").write(_boot())
    slot = my_slot()
    if slot:   # even without --notify: if Panda's guard has to stop this job, it tells this chat
        open(os.path.join(d, "slot"), "w").write(slot)
    # The wrapper records the exit code and end time itself, so status works after this process is gone.
    q = shlex.quote
    wrapper = (f"cd {q(a.cwd or os.getcwd())} && ( {line} ) ; rc=$? ; "
               f"date +%s.%N > {q(d)}/ended ; echo $rc > {q(d)}/exit")
    log = open(os.path.join(d, "log"), "ab")
    p = subprocess.Popen(["bash", "-c", wrapper], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    open(os.path.join(d, "pid"), "w").write(str(p.pid))
    print(f"started {a.name} (pid {p.pid}). Log: {d}/log")
    if a.notify:
        if not slot:  # a helper (subagent) run or a shell outside any chat: nobody to wake
            print("No chat to wake from here (helper run): wait with "
                  f"`pjob wait {a.name} --max 900` in few long slices instead of polling often.")
            return
        # Panda's worker watches the job for free and wakes this chat (a "Haiku steered" message) when it
        # ends, or when its log goes quiet for --stall seconds and Haiku judges it stuck.
        json.dump({"slot": slot, "sid": os.environ.get("KIRO_SESSION_ID", ""), "want": (a.want or "")[:400],
                   "stall": max(120, min(int(a.stall), 6 * 3600)), "at": time.time()},
                  open(os.path.join(d, "notify"), "w"))
        print("Panda will message you in this chat when it ends or looks stuck: no need to poll it. "
              "Do other work, or end your turn with a short note.")
    else:
        print(f"Check with: pjob status {a.name}   ·   wait with: pjob wait {a.name} --max 240")


def _wait(names, mx: float):
    t0 = time.time()
    while True:
        sts = [state(n) for n in names]
        ended = [s for s in sts if s["state"] != "running"]
        if ended or time.time() - t0 >= mx:
            return sts, bool(ended)
        time.sleep(min(5, max(0.5, mx - (time.time() - t0))))


def main():
    ap = argparse.ArgumentParser(prog="pjob", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="op", required=True)
    s = sub.add_parser("start"); s.add_argument("name"); s.add_argument("--sh"); s.add_argument("--cwd")
    s.add_argument("--notify", action="store_true", help="wake this chat when the job ends or looks stuck")
    s.add_argument("--want", help="with --notify: what to report from the log when it ends")
    s.add_argument("--stall", type=float, default=600, help="with --notify: log quiet this many seconds = check if stuck")
    s.add_argument("--remote", action="store_true", help="run on remote Panda (a second Kaggle notebook)")
    s.add_argument("--files", nargs="+", help="with --remote: files/folders under ~ to send first")
    s.add_argument("--out", nargs="+", help="with --remote: folders/files to bring back (default: --files)")
    s.add_argument("--setup", help="with --remote: install step run once per remote start (pip/apt/npm)")
    s.add_argument("--agent", action="store_true",
                   help="with --remote: the text after -- is a TASK for an agent on remote Panda (spends credits)")
    s.add_argument("--model", help="with --agent: model id (default claude-sonnet-5.5)")
    s.add_argument("--effort", help="with --agent: low / medium / high / xhigh / max (default medium)")
    s = sub.add_parser("status"); s.add_argument("name", nargs="?"); s.add_argument("-n", type=int, default=12)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("wait"); s.add_argument("name"); s.add_argument("--max", type=float, default=240)
    s.add_argument("-n", type=int, default=12)
    s = sub.add_parser("wait-any"); s.add_argument("names", nargs="+"); s.add_argument("--max", type=float, default=240)
    s = sub.add_parser("log"); s.add_argument("name"); s.add_argument("-n", type=int, default=40)
    s = sub.add_parser("stop"); s.add_argument("name")
    sub.add_parser("list")
    argv = sys.argv[1:]
    rest = []
    if "--" in argv:
        i = argv.index("--")
        argv, rest = argv[:i], argv[i + 1:]
    a = ap.parse_args(argv)
    os.makedirs(ROOT, exist_ok=True)

    if a.op == "start":
        cmd_start(a, rest)
    elif a.op == "status":
        names = [a.name] if a.name else sorted(os.listdir(ROOT))
        sts = [state(n) for n in names]
        if a.json:
            print(json.dumps(sts if not a.name else sts[0]))
        else:
            for st in sts:
                show(st, a.n if a.name else 0)
    elif a.op == "wait":
        mx = max(1.0, min(a.max, 1500))   # never block one step for more than 25 minutes
        (st,), ended = _wait([a.name], mx)
        show(st, a.n)
        if not ended:
            print(f"(still running after waiting {int(mx)}s; wait again, or do other work and check later)")
    elif a.op == "wait-any":
        sts, ended = _wait(a.names, max(1.0, min(a.max, 1500)))
        for st in sts:
            show(st, 6 if st["state"] != "running" else 0)
    elif a.op == "log":
        print(tail(a.name, a.n))
    elif a.op == "stop":
        d = _dir(a.name)
        pid = int(_read(d, "pid", "0") or 0)
        st = state(a.name)["state"]
        same_boot = _read(d, "boot") == _boot()
        if pid and same_boot and (st == "running" or (st == "lost" and job_procs(pid))):
            n = stop_procs(pid)
            if st == "running":
                open(os.path.join(d, "ended"), "w").write(str(time.time()))
                open(os.path.join(d, "exit"), "w").write("-15")
            print(f"stopped {a.name} ({n} process{'es' if n != 1 else ''})")
        else:
            print(f"{a.name} is not running")
    elif a.op == "list":
        names = sorted(os.listdir(ROOT), key=lambda n: -float(_read(os.path.join(ROOT, n), "started", "0") or 0))
        for n in names[:40]:
            show(state(n), 0)
        if not names:
            print("no jobs yet")


if __name__ == "__main__":
    main()
