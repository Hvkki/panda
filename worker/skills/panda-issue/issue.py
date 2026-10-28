#!/usr/bin/env python3
"""panda-issue: report a problem with Panda itself to the owner's Developer tab.

The report is only written to a local outbox (~/.panda/issues). The worker picks it up, shows it
in Developer -> Issues, and the owner decides whether the Developer chat should fix it. Nothing here
needs a key or touches Panda's code.

  panda-issue "Short title" --details "what happened, what you tried, the exact error"
  panda-issue "Short title" --details "..." --severity high     # low | normal | high
  panda-issue --list                                            # reports not yet picked up
"""
import argparse
import json
import os
import secrets
import sys
import time

OUTBOX = os.path.expanduser("~/.panda/issues")
SENT = os.path.join(OUTBOX, "sent.log")   # titles already reported (worker appends), for dedupe
MAX_PER_HOUR = 5


def _recent_titles() -> list:
    out = []
    for name in os.listdir(OUTBOX):
        if name.endswith(".json"):
            try:
                out.append(json.load(open(os.path.join(OUTBOX, name))).get("title", ""))
            except Exception:
                pass
    if os.path.exists(SENT):
        cutoff = time.time() - 86400
        for line in open(SENT, encoding="utf-8", errors="replace"):
            at, _, title = line.rstrip("\n").partition("\t")
            if at.replace(".", "", 1).isdigit() and float(at) > cutoff:
                out.append(title)
    return [t.strip().lower() for t in out]


def main() -> int:
    ap = argparse.ArgumentParser(prog="panda-issue", description=__doc__.split("\n")[0])
    ap.add_argument("title", nargs="?", help="one-line summary of the problem")
    ap.add_argument("--details", "-d", default="", help="what happened, steps, exact error text")
    ap.add_argument("--severity", "-s", choices=["low", "normal", "high"], default="normal")
    ap.add_argument("--list", action="store_true", help="show reports waiting to be picked up")
    a = ap.parse_args()
    os.makedirs(OUTBOX, exist_ok=True)

    if a.list:
        names = sorted(n for n in os.listdir(OUTBOX) if n.endswith(".json"))
        for n in names:
            print("-", json.load(open(os.path.join(OUTBOX, n))).get("title"))
        print(f"{len(names)} waiting" if names else "Nothing waiting; earlier reports are already in the Developer tab.")
        return 0

    title = " ".join((a.title or "").split())[:160]
    if len(title) < 5:
        print("Give a short title (at least 5 characters).", file=sys.stderr)
        return 2
    if title.lower() in _recent_titles():
        print("Already reported in the last day; no new report made.")
        return 0
    hour = [n for n in os.listdir(OUTBOX) if n.endswith(".json")
            and time.time() - os.path.getmtime(os.path.join(OUTBOX, n)) < 3600]
    if len(hour) >= MAX_PER_HOUR:
        print(f"{MAX_PER_HOUR} reports are already waiting; try again later.", file=sys.stderr)
        return 3

    rec = {"title": title, "details": a.details.strip()[:6000], "severity": a.severity,
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = os.path.join(OUTBOX, f"{int(time.time())}-{secrets.token_hex(3)}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(tmp, path)
    print("Reported. The owner will see it in Developer -> Issues and decide whether to fix it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
