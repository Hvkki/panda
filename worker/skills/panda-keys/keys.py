#!/usr/bin/env python3
"""panda-key: save API keys from a file into the owner's vault (Customize -> API keys).

  panda-key add NAME --from FILE [--field F] [--replace] [--delete-source]
        one key. FILE holds just the key, or is an env/JSON file: the value of field F (default NAME).
  panda-key import FILE [--only A,B] [--prefix P] [--replace] [--delete-source]
        every NAME=VALUE (or JSON "name": "value") pair in FILE.
  panda-key fields FILE        the field NAMES a file holds (never the values)
  panda-key list               names already in the vault

Keys in the vault survive every restart and reach every chat as $NAME (source ~/.panda/secrets.env).
Values are never printed. An existing key is kept unless you pass --replace. Developer variables
(DEV__...) cannot be set here.
"""
import argparse
import json
import os
import re
import sys
import time
import uuid

OUTBOX = os.path.expanduser("~/.panda/keys-out")
SECRETS = os.path.expanduser("~/.panda/secrets.env")
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
MAX_FILE = 256 * 1024


def norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", name.strip().upper())


def parse(path: str) -> dict:
    """{field: value}. A file with one bare line (no '=' or ':') is {'': value}."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        sys.exit(f"panda-key: {path}: no such file")
    if os.path.getsize(path) > MAX_FILE:
        sys.exit(f"panda-key: {path} is over {MAX_FILE >> 10} KB; that is not a key file")
    text = open(path, encoding="utf-8", errors="replace").read().lstrip("\ufeff")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int, float)) and str(v).strip()}
    except ValueError:
        pass
    out, bare = {}, []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_.-]*)(?:\s*=\s*|:\s+)(.*)$", s)
        if m:
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            if v:
                out[m.group(1)] = v
        else:
            bare.append(s)
    if not out and len(bare) == 1:
        return {"": bare[0]}
    return out


def send(keys: list, note: str, wait: float | None = None) -> list:
    wait = float(os.environ.get("PANDA_KEY_WAIT", "60")) if wait is None else wait
    os.makedirs(OUTBOX, mode=0o700, exist_ok=True)
    rid = uuid.uuid4().hex[:16]
    tmp = os.path.join(OUTBOX, f".{rid}.tmp")
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        json.dump({"keys": keys, "note": note[:160]}, f)
    os.rename(tmp, os.path.join(OUTBOX, f"{rid}.req"))
    done = os.path.join(OUTBOX, f"{rid}.done")
    t0 = time.time()
    while time.time() - t0 < wait:
        if os.path.exists(done):
            try:
                return json.load(open(done)).get("results") or []
            finally:
                os.remove(done)
        time.sleep(0.5)
    req = os.path.join(OUTBOX, f"{rid}.req")
    if os.path.exists(req):
        os.remove(req)       # never leave a key lying around
        sys.exit("panda-key: Panda's worker did not pick this up (it needs a Stop and Start once to know "
                 "this command). Nothing was saved.")
    sys.exit("panda-key: no answer from Panda's worker yet; check Customize -> API keys in a minute.")


def vault_names() -> list:
    try:
        return [m.group(1) for m in re.finditer(r"^export ([A-Z][A-Z0-9_]*)=", open(SECRETS).read(), re.M)]
    except OSError:
        return []


def main():
    ap = argparse.ArgumentParser(prog="panda-key", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="op", required=True)
    s = sub.add_parser("add")
    s.add_argument("name")
    s.add_argument("--from", dest="src", required=True)
    s.add_argument("--field")
    s.add_argument("--replace", action="store_true")
    s.add_argument("--delete-source", action="store_true")
    s = sub.add_parser("import")
    s.add_argument("src")
    s.add_argument("--only")
    s.add_argument("--prefix", default="")
    s.add_argument("--replace", action="store_true")
    s.add_argument("--delete-source", action="store_true")
    s = sub.add_parser("fields")
    s.add_argument("src")
    sub.add_parser("list")
    a = ap.parse_args()

    if a.op == "list":
        names = vault_names()
        print("\n".join(names) if names else "no keys in the vault yet")
        return
    pairs = parse(a.src)
    if a.op == "fields":
        print("\n".join(k or "(the whole file is one key)" for k in pairs) or "no NAME=VALUE fields found")
        return
    if a.op == "add":
        name = norm(a.name)
        field = a.field if a.field is not None else a.name
        val = pairs.get(field)
        if val is None:
            val = next((v for k, v in pairs.items() if norm(k) == norm(field)), None)
        if val is None and list(pairs) == [""]:
            val = pairs[""]
        if val is None:
            sys.exit(f"panda-key: no field {field!r} in {a.src}. Fields there: "
                     + (", ".join(k for k in pairs if k) or "none") + " (use --field)")
        keys = [{"name": name, "value": val, "replace": a.replace}]
    else:
        only = {norm(x) for x in a.only.split(",")} if a.only else None
        keys = []
        for k, v in pairs.items():
            n = norm(a.prefix + k) if k else ""
            if n and (only is None or norm(k) in only or n in only):
                keys.append({"name": n, "value": v, "replace": a.replace})
        if not keys:
            sys.exit(f"panda-key: nothing to import from {a.src} (fields: {', '.join(k for k in pairs if k) or 'none'})")
    bad = [k["name"] for k in keys if not NAME_RE.fullmatch(k["name"])]
    if bad:
        sys.exit(f"panda-key: not valid key names: {', '.join(bad)} (A-Z, 0-9, _; start with a letter)")
    results = send(keys, f"Saved by Panda from {os.path.basename(a.src)} on {time.strftime('%d %b %H:%M UTC', time.gmtime())}")
    ok = True
    for r in results:
        st = r.get("status", "?")
        print(f"{r.get('name')}: {st}" + (" (already in the vault; pass --replace to overwrite)" if st == "exists" else ""))
        ok = ok and st in ("saved", "replaced", "exists")
    if ok and results:
        print("Use it as $NAME after `source ~/.panda/secrets.env`; it stays across restarts.")
        if a.delete_source:
            os.remove(os.path.expanduser(a.src))
            print(f"deleted {a.src}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
