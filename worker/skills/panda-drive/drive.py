#!/usr/bin/env python3
"""drive — Panda's Google Drive (the user's own storage). Panda only sees files it created.

  drive put <file> [folder/sub]   upload any size (resumable); prints the Drive link
  drive get <name-or-id> [dest]   download into ~/outputs (or dest)
  drive ls [folder/sub]           list files
  drive find <words>              search by name
  drive rm <name-or-id>           move to trash
  drive link <name-or-id>         link the user can open
  drive usage                     space used / free

Everything lives under the "Panda" folder of the user's Drive. Credentials: ~/.panda/drive.json
"""
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CFG = os.path.expanduser(os.environ.get("PANDA_DRIVE_CFG", "~/.panda/drive.json"))
API = "https://www.googleapis.com/drive/v3"
UP = "https://www.googleapis.com/upload/drive/v3"
CHUNK = 16 * 2**20  # must be a multiple of 256 KiB
FOLDER = "application/vnd.google-apps.folder"
FIELDS = "id,name,size,mimeType,modifiedTime,webViewLink"


def cfg():
    try:
        return json.load(open(CFG))
    except (OSError, ValueError):
        sys.exit("Google Drive is not connected.")


_tok = {"t": "", "exp": 0.0}


def token():
    if time.time() < _tok["exp"]:
        return _tok["t"]
    c = cfg()
    body = urllib.parse.urlencode({"client_id": c["client_id"], "client_secret": c["client_secret"],
                                   "refresh_token": c["refresh_token"], "grant_type": "refresh_token"}).encode()
    try:
        d = json.loads(urllib.request.urlopen("https://oauth2.googleapis.com/token", data=body, timeout=30).read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Drive sign-in expired or was revoked ({e.code}). The user must reconnect Google Drive.")
    _tok.update(t=d["access_token"], exp=time.time() + int(d.get("expires_in", 3600)) - 60)
    return _tok["t"]


def call(method, url, data=None, headers=None, raw=False, timeout=120):
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": f"Bearer {token()}", **(headers or {})})
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp if raw else json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"Drive error {e.code}: {e.read().decode()[:200]}")
        except urllib.error.URLError as e:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"Drive unreachable: {e}")


def esc(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def root():
    c = cfg()
    if c.get("folder_id"):
        return c["folder_id"]
    f = call("POST", f"{API}/files?fields=id", json.dumps({"name": "Panda", "mimeType": FOLDER}).encode(),
             {"Content-Type": "application/json"})
    c["folder_id"] = f["id"]
    with open(os.open(CFG, os.O_WRONLY | os.O_TRUNC | os.O_CREAT, 0o600), "w") as out:
        json.dump(c, out)
    return f["id"]


def folder(path, create=True):
    pid = root()
    for part in [p for p in (path or "").strip("/").split("/") if p]:
        found = call("GET", f"{API}/files?" + urllib.parse.urlencode({
            "q": f"name='{esc(part)}' and '{pid}' in parents and mimeType='{FOLDER}' and trashed=false",
            "fields": "files(id)"}))["files"]
        if found:
            pid = found[0]["id"]
        elif create:
            pid = call("POST", f"{API}/files?fields=id",
                       json.dumps({"name": part, "mimeType": FOLDER, "parents": [pid]}).encode(),
                       {"Content-Type": "application/json"})["id"]
        else:
            sys.exit(f"No folder named {path!r}")
    return pid


def resolve(ref):
    """A Drive file id, or a (partial) file name."""
    if len(ref) >= 25 and all(ch.isalnum() or ch in "-_" for ch in ref):
        return call("GET", f"{API}/files/{ref}?fields={FIELDS}")
    hits = call("GET", f"{API}/files?" + urllib.parse.urlencode({
        "q": f"name contains '{esc(ref)}' and mimeType!='{FOLDER}' and trashed=false",
        "fields": f"files({FIELDS})", "orderBy": "modifiedTime desc", "pageSize": 10}))["files"]
    exact = [h for h in hits if h["name"] == ref]
    if exact or len(hits) == 1:
        return (exact or hits)[0]
    if not hits:
        sys.exit(f"No file matching {ref!r}")
    sys.exit("Several files match; use the id:\n" + "\n".join(f"  {h['id']}  {h['name']}" for h in hits))


def human(n):
    n = int(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def put(path, dest=""):
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        sys.exit(f"No such file: {path}")
    size = os.path.getsize(path)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    meta = {"name": os.path.basename(path), "parents": [folder(dest)]}
    start = call("POST", f"{UP}/files?uploadType=resumable&fields={FIELDS}", json.dumps(meta).encode(),
                 {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": mime,
                  "X-Upload-Content-Length": str(size)}, raw=True)
    loc = start.headers["Location"]
    sent, result, t0 = 0, None, time.time()
    with open(path, "rb") as f:
        while sent < size or size == 0:
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            hdr = {"Content-Length": str(len(chunk)),
                   "Content-Range": f"bytes {sent}-{end}/{size}" if size else "bytes */0"}
            for attempt in range(5):
                try:
                    resp = urllib.request.urlopen(urllib.request.Request(loc, data=chunk, method="PUT", headers=hdr), timeout=600)
                    result = json.loads(resp.read())
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 308:  # chunk accepted, more to send
                        break
                    if e.code in (500, 502, 503, 504) and attempt < 4:
                        time.sleep(2 ** attempt)
                        continue
                    sys.exit(f"Upload failed: {e.code} {e.read().decode()[:200]}")
            sent += len(chunk)
            if size > CHUNK:
                print(f"  {sent * 100 // size}%", file=sys.stderr)
            if size == 0 or result:
                break
    print(f"Uploaded {result['name']} ({human(size)}) in {time.time() - t0:.0f}s")
    print(f"Link: {result.get('webViewLink')}")
    print(f"Id: {result['id']}")
    return result


def get(ref, dest=""):
    f = resolve(ref)
    out = os.path.expanduser(dest or f"~/outputs/{f['name']}")
    if os.path.isdir(out):
        out = os.path.join(out, f["name"])
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    resp = call("GET", f"{API}/files/{f['id']}?alt=media", raw=True, timeout=600)
    with open(out, "wb") as w:
        while True:
            b = resp.read(1 << 20)
            if not b:
                break
            w.write(b)
    print(f"Downloaded {f['name']} ({human(os.path.getsize(out))}) to {out}")


def ls(path=""):
    pid = folder(path, create=False)
    items = call("GET", f"{API}/files?" + urllib.parse.urlencode({
        "q": f"'{pid}' in parents and trashed=false", "fields": f"files({FIELDS})",
        "orderBy": "folder,modifiedTime desc", "pageSize": 200}))["files"]
    if not items:
        print("(empty)")
    for i in items:
        kind = "📁" if i["mimeType"] == FOLDER else "  "
        print(f"{kind} {i['name']:<48} {human(i.get('size', 0)):>9}  {i['modifiedTime'][:16]}  {i['id']}")


def find(words):
    items = call("GET", f"{API}/files?" + urllib.parse.urlencode({
        "q": f"name contains '{esc(words)}' and trashed=false", "fields": f"files({FIELDS})", "pageSize": 50}))["files"]
    for i in items:
        print(f"{i['name']:<48} {human(i.get('size', 0)):>9}  {i['id']}")
    if not items:
        print("No matches.")


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help", "help"):
        sys.exit(__doc__)
    op, rest = a[0], a[1:]
    if op == "put" and rest:
        put(rest[0], rest[1] if len(rest) > 1 else "")
    elif op == "get" and rest:
        get(rest[0], rest[1] if len(rest) > 1 else "")
    elif op == "ls":
        ls(rest[0] if rest else "")
    elif op == "find" and rest:
        find(" ".join(rest))
    elif op == "rm" and rest:
        f = resolve(rest[0])
        call("PATCH", f"{API}/files/{f['id']}", json.dumps({"trashed": True}).encode(), {"Content-Type": "application/json"})
        print(f"Moved {f['name']} to the trash")
    elif op == "link" and rest:
        print(resolve(rest[0]).get("webViewLink"))
    elif op == "usage":
        q = call("GET", f"{API}/about?fields=storageQuota")["storageQuota"]
        lim, use = int(q.get("limit", 0)), int(q.get("usage", 0))
        print(f"Used {human(use)} of {human(lim)} — {human(lim - use)} free")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
