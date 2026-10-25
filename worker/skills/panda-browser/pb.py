#!/usr/bin/env python3
"""pb — drive Panda's real browser from the command line.

  pb open <url|search words>    go to a page (prints the page as a ref-tagged outline)
  pb snap [--full]              re-read the current page
  pb click <ref>                click an element by its ref (e.g. e12)   | pb click --xy 400 300
  pb type <ref> "text" [--submit]
  pb select <ref> "value" ["value2" …]   choose option(s) in a dropdown
  pb hover <ref>                point at an element (menus that open on hover)
  pb press <Key>                Enter, Tab, Escape, ArrowDown, Control+a …
  pb scroll [down|up] [pixels]
  pb back | forward | reload
  pb text [--full]              readable text of the page (cheaper than a snapshot for articles)
  pb shot [name.png] [--full]   save a screenshot to ~/outputs (shows up as a file card)
  pb tabs | pb tab <n> | pb newtab [url] | pb closetab
  pb wait <seconds> | pb wait --text "Done"
  pb handoff "message"          ask the USER to take over (login, captcha, payment); waits until they press Done
  pb forget                     delete every saved login and cookie

Actions print the new page outline automatically; add --no-snap to skip it.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

PORT = int(os.environ.get("PANDA_BROWSER_PORT", "5480"))
BASE = f"http://127.0.0.1:{PORT}"
ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine.py")


def healthy() -> bool:
    try:
        return json.load(urllib.request.urlopen(BASE + "/health", timeout=2)).get("ok", False)
    except Exception:
        return False


def engine_starting() -> bool:
    """True when an engine process exists but is not answering yet (e.g. downloading Chromium)."""
    try:
        pid = int(open(os.path.expanduser("~/.panda/engine.pid")).read().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def installing() -> bool:
    return os.path.exists(os.path.expanduser("~/.panda/browser-installing"))


def ensure_engine():
    if healthy():
        return
    if installing():  # the worker is installing Chromium and will start the engine itself
        for _ in range(900):
            time.sleep(1)
            if healthy():
                return
            if not installing() and not engine_starting():
                break
    if healthy():
        return
    if not engine_starting():
        os.makedirs(os.path.expanduser("~/.panda"), exist_ok=True)
        log = open(os.path.expanduser("~/.panda/browser-engine.log"), "a")
        subprocess.Popen([sys.executable, ENGINE], stdout=log, stderr=log, start_new_session=True)
    for _ in range(600):  # up to 5 min: the first start may download Chromium
        time.sleep(0.5)
        if healthy():
            return
        if not engine_starting():
            break
    sys.exit("The browser could not start. See ~/.panda/browser-engine.log")


def call(req: dict, timeout=900) -> dict:
    ensure_engine()
    data = json.dumps(req).encode()
    r = urllib.request.Request(BASE + "/do", data=data, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def parse(argv: list[str]) -> dict:
    flags = {a for a in argv if a in ("--full", "--no-snap", "--submit")}
    a = [x for x in argv if x not in flags]
    base = {"full": "--full" in flags, "no_snap": "--no-snap" in flags}
    if not a:
        sys.exit(__doc__)
    op, rest = a[0], a[1:]
    if op in ("open",):
        return {"op": "open", "url": " ".join(rest), **base}
    if op in ("snap", "snapshot"):
        return {"op": "snap", **base}
    if op == "click":
        if rest and rest[0] == "--xy":
            return {"op": "click", "x": rest[1], "y": rest[2], **base}
        return {"op": "click", "ref": rest[0], **base}
    if op == "type":
        return {"op": "type", "ref": rest[0], "text": " ".join(rest[1:]), "submit": "--submit" in flags, **base}
    if op == "select":
        if len(rest) < 2:
            sys.exit("usage: pb select <ref> \"value\" [\"value2\" …]")
        return {"op": "select", "ref": rest[0], "values": rest[1:], **base}
    if op == "hover":
        return {"op": "hover", "ref": rest[0], **base}
    if op == "press":
        return {"op": "press", "key": rest[0], **base}
    if op == "scroll":
        direction = rest[0] if rest else "down"
        px = float(rest[1]) if len(rest) > 1 else 600
        return {"op": "scroll", "dy": px if direction == "down" else -px, **base}
    if op in ("back", "forward", "reload", "text", "tabs", "closetab", "forget"):
        return {"op": op, **base}
    if op == "shot":
        return {"op": "shot", "name": rest[0] if rest else "", "full": base["full"]}
    if op == "tab":
        return {"op": "tab", "i": int(rest[0]), **base}
    if op == "newtab":
        return {"op": "newtab", "url": " ".join(rest), **base}
    if op == "wait":
        if rest and rest[0] == "--text":
            return {"op": "wait", "text": " ".join(rest[1:]), **base}
        return {"op": "wait", "secs": float(rest[0]) if rest else 2, **base}
    if op == "handoff":
        return {"op": "handoff", "message": " ".join(rest), **base}
    sys.exit(f"unknown command {op!r}\n\n{__doc__}")


def main():
    try:
        out = call(parse(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception as exc:
        sys.exit(f"browser error: {exc}")
    if not out.get("ok"):
        print("ERROR:", out.get("error"))
        if out.get("url"):
            print("URL:", out["url"])
        sys.exit(1)
    if out.get("note"):
        print(out["note"])
    if out.get("saved"):
        print("Saved:", out["saved"])
    if "url" in out:
        print("URL:", out["url"])
        print("Title:", out.get("title", ""))
    if out.get("tabs"):
        print("Tabs:\n" + "\n".join(out["tabs"]))
    if out.get("text"):
        print("\n" + out["text"])
    if out.get("snapshot"):
        print("\n" + out["snapshot"])


if __name__ == "__main__":
    main()
