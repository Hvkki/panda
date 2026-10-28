#!/usr/bin/env python3
"""pjob: run long commands in the background, keep working, and check on them.

  pjob start NAME -- COMMAND...      start COMMAND in the background (its own log, survives your turn's steps)
  pjob start NAME --sh 'a && b'      the same, with a shell line
  pjob start NAME --notify [--want 'what to report'] [--stall 600] -- COMMAND
                                     Panda wakes this chat when it ends or looks stuck (no polling)
  pjob status [NAME]                 running / done (exit code) / lost, with run time and the last log lines
  pjob wait NAME [--max 300]         block until NAME ends or --max seconds pass (default 240), then status
  pjob wait-any A B C [--max 300]    block until the first of them ends
  pjob log NAME [-n 40]              last lines of the log
  pjob stop NAME                     stop it (whole process group)
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
    return {"name": name, "state": st, "exit": int(ex) if ex.lstrip("-").isdigit() else None,
            "secs": round(end - started) if started else None, "cmd": _read(d, "cmd")[:300],
            "log": os.path.join(d, "log")}


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
    if s["state"] != "missing" and n:
        out = tail(s["name"], n)
        if out:
            print("--- last log lines ---")
            print(out)


SESSION_MAP = os.path.expanduser("~/.kiro/crew/session_map.json")


def my_slot() -> str:
    """The Kiro Crew chat slot this shell belongs to (session_map: 'dashboard:<slot>' -> {sid})."""
    sid = os.environ.get("KIRO_SESSION_ID", "")
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
    os.makedirs(d, exist_ok=True)
    for f in ("exit", "ended", "log", "notify", "notified", "watch.json"):
        try:
            os.remove(os.path.join(d, f))
        except OSError:
            pass
    open(os.path.join(d, "cmd"), "w").write(line)
    open(os.path.join(d, "started"), "w").write(str(time.time()))
    open(os.path.join(d, "boot"), "w").write(_boot())
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
        slot = my_slot()
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
        pid = int(_read(_dir(a.name), "pid", "0") or 0)
        if pid and state(a.name)["state"] == "running":
            try:
                os.killpg(pid, signal.SIGTERM)
                time.sleep(2)
                if _alive(pid):
                    os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
            d = _dir(a.name)
            open(os.path.join(d, "ended"), "w").write(str(time.time()))
            open(os.path.join(d, "exit"), "w").write("-15")
            print(f"stopped {a.name}")
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
