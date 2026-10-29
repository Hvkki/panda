"""premote: run one command on remote Panda (a second Kaggle notebook) and mirror its log here.

Agents use it through pjob, so the job watcher, --notify and pjob stop work as for local jobs:

  pjob start NAME --remote [--files P ...] [--out P ...] [--setup 'CMD'] --notify --want '...' -- COMMAND

This process is only a proxy: it hands the request to Panda's worker (~/.panda/remote/req) and copies
~/.panda/remote/jobs/<tid>/log to its output until the exit file appears, then exits with that code.
Stopping it (pjob stop) asks the worker to stop the remote command too.
"""
import argparse
import json
import os
import signal
import sys
import time
import uuid

BOX = os.environ.get("PANDA_REMOTE_BOX") or os.path.expanduser("~/.panda/remote")


def main():
    ap = argparse.ArgumentParser(prog="premote")
    ap.add_argument("op", choices=["run"])
    ap.add_argument("--name", default="job")
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--out", nargs="*", default=[])
    ap.add_argument("--setup", default="")
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--cmd", required=True)
    ap.add_argument("--agent", action="store_true")
    ap.add_argument("--model", default="claude-sonnet-5.5")
    ap.add_argument("--effort", default="medium")
    a = ap.parse_args()
    tid = f"{a.name}-{int(time.time())}-{uuid.uuid4().hex[:4]}"
    d = f"{BOX}/jobs/{tid}"
    os.makedirs(f"{BOX}/req", exist_ok=True)
    agent = None
    if a.agent:
        res = os.path.expanduser(f"~/work/remote/{a.name}")
        os.makedirs(res, exist_ok=True)
        agent = {"prompt": a.cmd, "model": a.model, "effort": a.effort, "results": res}
        a.out = list(a.out or a.files) + [res]
        a.cwd = res

    def on_term(*_):
        os.makedirs(d, exist_ok=True)
        open(f"{d}/cancel", "w").write(str(time.time()))
        print("[remote] stop requested", flush=True)
        sys.exit(143)

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    req = {"tid": tid, "cmd": a.cmd, "cwd": os.path.abspath(a.cwd), "setup": a.setup,
           "files": [os.path.abspath(os.path.expanduser(p)) for p in a.files],
           "out": [os.path.abspath(os.path.expanduser(p)) for p in a.out], "at": time.time()}
    if agent:
        req["agent"] = agent
    tmp = f"{BOX}/req/.{tid}.tmp"
    json.dump(req, open(tmp, "w"))
    os.rename(tmp, f"{BOX}/req/{tid}.json")
    print(f"[remote] queued as {tid}", flush=True)
    t0, pos = time.time(), 0
    while True:
        done = os.path.exists(f"{d}/exit")      # read before the log: the worker writes exit last
        try:
            with open(f"{d}/log", "rb") as f:
                f.seek(pos)
                chunk = f.read()
            if chunk:
                sys.stdout.buffer.write(chunk)
                sys.stdout.flush()
                pos += len(chunk)
        except OSError:
            if time.time() - t0 > 60 and not os.path.isdir(d):
                print("[remote] Panda's worker did not pick this up: it needs a Stop and Start to run "
                      "remote jobs", flush=True)
                sys.exit(3)
        if done:
            try:
                sys.exit(int(open(f"{d}/exit").read().strip() or 255))
            except ValueError:
                sys.exit(255)
        time.sleep(1)


if __name__ == "__main__":
    main()
