#!/usr/bin/env python3
"""brief: run a command and get back only what you asked for, so long output never fills the chat.

  brief --want "did it pass, which tests failed" -- npm test
  brief --want "final LUFS and any clipping" --sh 'cd ~/work/mix && python3 mix_full.py'
  brief -- make build                        (no --want: Haiku gives a short general summary)

Short output (up to --lines lines, default 40) is printed as is, with no Haiku call. Longer output is
saved to a log, and Panda's Haiku answers --want from it in a few lines. The exit code, the error lines
and the log path are always printed untouched, so nothing important can be summarised away: grep the
log yourself when you need a detail. brief exits with the command's own exit code.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid

LOGS = os.path.expanduser("~/work/brief")
BOX = os.path.expanduser("~/.panda/brief")          # worker.py brief_loop answers the requests here
ERR = re.compile(r"(error|traceback|exception|failed|fatal|panic|not found|denied|killed|segmentation)", re.I)
WAIT_SECS = 60


def error_lines(lines: list, n: int = 12) -> list:
    out = [ln for ln in lines if ERR.search(ln)]
    return out[-n:]


def ask_haiku(req: dict) -> str:
    """Hand the request to Panda's worker (it holds the gateway login) and wait for Haiku's answer."""
    rid = uuid.uuid4().hex[:12]
    os.makedirs(f"{BOX}/req", exist_ok=True)
    os.makedirs(f"{BOX}/resp", exist_ok=True)
    tmp = f"{BOX}/req/.{rid}.tmp"
    json.dump(req, open(tmp, "w"))
    os.replace(tmp, f"{BOX}/req/{rid}.json")
    resp = f"{BOX}/resp/{rid}.json"
    t0 = time.time()
    while time.time() - t0 < WAIT_SECS:
        if os.path.exists(resp):
            try:
                data = json.load(open(resp))
            except ValueError:
                time.sleep(0.3)
                continue
            os.remove(resp)
            return str(data.get("text") or "").strip()
        time.sleep(0.5)
    try:  # nobody answered (worker busy or not running): withdraw the request
        os.remove(f"{BOX}/req/{rid}.json")
    except OSError:
        pass
    return ""


def main():
    argv = sys.argv[1:]
    rest = []
    if "--" in argv:
        i = argv.index("--")
        argv, rest = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog="brief", description=__doc__.split("\n")[0])
    ap.add_argument("--want", default="", help="what you need to know from the output")
    ap.add_argument("--sh", help="a shell line instead of a command after --")
    ap.add_argument("--lines", type=int, default=40, help="print output as is up to this many lines")
    ap.add_argument("--cwd")
    a = ap.parse_args(argv)
    line = a.sh if a.sh else shlex.join(rest)
    if not line.strip():
        sys.exit("brief: give a command after -- or use --sh")

    os.makedirs(LOGS, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (rest[0] if rest else line.split()[0]).rsplit("/", 1)[-1])[:24] or "cmd"
    log = f"{LOGS}/{time.strftime('%Y%m%d-%H%M%S')}-{slug}.log"
    t0 = time.time()
    with open(log, "wb") as f:
        p = subprocess.run(["bash", "-c", line], cwd=a.cwd or None, stdin=subprocess.DEVNULL, stdout=f,
                           stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    secs = time.time() - t0
    raw = open(log, "rb").read().decode("utf-8", "replace")
    lines = [ln.split("\r")[-1] for ln in raw.split("\n")]   # keep the last state of progress-bar lines
    while lines and not lines[-1].strip():
        lines.pop()
    head = f"exit {p.returncode} · {secs:.0f}s · {len(lines)} lines"

    if len(lines) <= a.lines and len(raw) <= 6000:
        print(head)
        if lines:
            print("\n".join(lines))
        os.remove(log)  # nothing to look up later
        sys.exit(p.returncode)

    text = "\n".join(lines)
    if len(text) > 40000:  # the start (setup, config) and the end (results, errors) matter most
        text = text[:5000] + "\n[... middle of the output cut ...]\n" + text[-35000:]
    answer = ask_haiku({"cmd": line[:600], "want": a.want[:400], "exit": p.returncode, "secs": round(secs),
                        "lines": len(lines), "text": text})
    print(f"{head} · full log: {log}")
    if answer:
        print(f"Haiku{' (' + a.want[:80] + ')' if a.want else ''}: {answer}")
    else:
        print("(Haiku was not available; showing the end of the output)")
    errs = error_lines(lines)
    if errs:
        print("--- error lines (as printed) ---")
        print("\n".join(e[:300] for e in errs))
    print("--- last lines ---")
    print("\n".join(ln[:300] for ln in lines[-(5 if answer else 15):]))
    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
