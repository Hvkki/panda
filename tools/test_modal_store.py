"""Modal volume store: unique path per copy, the manifest switches only after Modal lists the file at its
full size, the previous copy is removed after the switch, keys come from this account's own vault.

    python3 tools/test_modal_store.py          hermetic (fake Modal, in-memory manifests)
    python3 tools/test_modal_store.py --real   also a real round-trip with the vault's MODAL_TOKEN_* keys
                                               (from ~/.panda/secrets.env) on a throwaway volume, deleted after
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
import worker as W  # noqa: E402

REAL = "--real" in sys.argv
W.WORK = tempfile.mkdtemp()
real_mstore = W._mstore
MAN = {}
W._manifest_get = lambda sb, name: MAN.get(name)
W._put_manifest = lambda sb, name, man: MAN.__setitem__(name, man)
W.drive_rm = lambda fid: None


class Vault:
    def __init__(self, rows):
        self.rows, self.calls, self.deleted = rows, 0, []

    def delete_object(self, name):
        self.deleted.append(name)

    def rest(self, method, path, params=None, body=None, prefer=""):
        assert method == "GET" and path == "secrets", (method, path)
        self.calls += 1
        want = params["name"][4:-1].split(",")
        return [r for r in self.rows if r["owner"] == params["owner"][3:] and r["name"] in want]


# Keys: this account's own vault pair; cached; an account without keys gets None.
W._modal_store.update(owner=None, env=None, at=0.0, pkg=True)
v = Vault([{"owner": "owner-1", "name": "MODAL_TOKEN_ID", "value": "ak-test-id-123"},
           {"owner": "owner-1", "name": "MODAL_TOKEN_SECRET", "value": "as-test-secret-456"},
           {"owner": "owner-1", "name": "OTHER", "value": "x"}])
W.OWNER = "owner-1"
env = W.modal_store_env(v)
assert env == {"MODAL_TOKEN_ID": "ak-test-id-123", "MODAL_TOKEN_SECRET": "as-test-secret-456"}, env
assert "as-test-secret-456" in W.SECRETS, "the secret must be scrubbed from logs"
W.modal_store_env(v)
assert v.calls == 1, "keys are cached"
W.OWNER = "someone-else"
assert W.modal_store_env(v) is None and not W.modal_store_ok(v), "an account without Modal keys has no store"
W.OWNER = "owner-1"
W._modal_store["at"] = 0.0
print("ok keys: own vault pair, cached, none for an account without keys")

# Fake Modal volume.
VOL, FAIL = {}, {"put": False, "short": False}


def fake_mstore(sb, op, *args, volume=None, timeout=None):
    vol = VOL.setdefault(volume or W.MODAL_STORE_VOLUME, {})
    if op == "put":
        if FAIL["put"]:
            return None
        data = open(args[0], "rb").read()
        vol[args[1]] = data[:-1] if FAIL["short"] else data
        return {"ok": True}
    if op == "ls":
        return [{"path": p, "size": len(d)} for p, d in vol.items() if p.startswith(args[0].rstrip("/") + "/")]
    if op == "get":
        if args[0] not in vol:
            return None
        open(args[1], "wb").write(vol[args[0]])
        return {"ok": True, "size": len(vol[args[0]])}
    if op == "rm":
        n = sum(1 for p in args if vol.pop(p, None) is not None)
        return {"ok": True, "removed": n}
    raise AssertionError(op)


W._mstore = fake_mstore
src = os.path.join(W.WORK, "a.bin")
open(src, "wb").write(os.urandom(5000))
got = W.blob_save_modal(v, "latest-files.tar.gz", src, extra={"sig": "s1", "files": 2})
m1 = MAN["latest-files.tar.gz"]
assert got and m1["modal"]["volume"] == "panda-store" and m1["sha256"] == W.sha256_file(src), m1
assert re.fullmatch(r"/owner1/latest-files\.tar\.gz/\d+[0-9a-f]{4}\.pbak", m1["modal"]["path"]), m1
assert m1["sig"] == "s1" and m1["parts"] == []
open(src, "wb").write(os.urandom(6000))
W.blob_save_modal(v, "latest-files.tar.gz", src)
m2 = MAN["latest-files.tar.gz"]
assert m2["modal"]["path"] != m1["modal"]["path"] and list(VOL["panda-store"]) == [m2["modal"]["path"]], VOL
print("ok save: unique path per copy, the previous copy removed after the switch")

for what in ("put", "short"):
    FAIL[what] = True
    assert W.blob_save_modal(v, "latest-files.tar.gz", src) is None
    FAIL[what] = False
    assert MAN["latest-files.tar.gz"] == m2 and list(VOL["panda-store"]) == [m2["modal"]["path"]], (what, VOL)
print("ok failure: a failed or short upload keeps the last good copy, leaves nothing behind")

dest = os.path.join(W.WORK, "back.bin")
assert W.blob_load(v, "latest-files.tar.gz", dest) and open(dest, "rb").read() == open(src, "rb").read()
MAN["latest-files.tar.gz"] = dict(m2, sha256="0" * 64)
try:
    W.blob_load(v, "latest-files.tar.gz", dest)
    raise AssertionError("a wrong checksum was accepted")
except RuntimeError as exc:
    assert "checksum" in str(exc), exc
MAN["latest-files.tar.gz"] = dict(m2, modal={"volume": "panda-store", "path": "/owner1/gone.pbak"})
W.time.sleep, _sleep = (lambda s: None), W.time.sleep
try:
    W.blob_load(v, "latest-files.tar.gz", dest)
    raise AssertionError("a missing file was accepted")
except RuntimeError as exc:
    assert "Modal volume" in str(exc), exc
finally:
    W.time.sleep = _sleep
print("ok load: the named file, checksum checked, a missing file is a clear error")

if REAL:
    W._mstore = real_mstore
    keys = dict(re.findall(r"^export (MODAL_TOKEN_ID|MODAL_TOKEN_SECRET)=['\"]?([^'\"\n]+)",
                           open(os.path.expanduser("~/.panda/secrets.env")).read(), re.M))
    assert len(keys) == 2, "MODAL_TOKEN_ID / MODAL_TOKEN_SECRET not in ~/.panda/secrets.env"
    W._modal_store.update(owner=W.OWNER, env=dict(keys), at=W.time.time() + 3600, pkg=None)
    W.MODAL_STORE_VOLUME = f"panda-store-test-{uuid.uuid4().hex[:6]}"
    MAN.clear()
    try:
        big = os.path.join(W.WORK, "big.pbak")
        open(big, "wb").write(os.urandom(20 << 20))
        t0 = W.time.time()
        assert W.blob_save_modal(v, "latest-files.tar.gz", big), "real save failed"
        first = MAN["latest-files.tar.gz"]["modal"]["path"]
        print(f"ok real save: 20 MB in {W.time.time() - t0:.0f} s")
        out = os.path.join(W.WORK, "big.back")
        assert W.blob_load(v, "latest-files.tar.gz", out) and W.sha256_file(out) == W.sha256_file(big)
        print("ok real load: same bytes")
        assert W.blob_save_modal(v, "latest-files.tar.gz", big)
        listed = W._mstore(v, "ls", W.modal_store_dir("latest-files.tar.gz"))
        assert [e["path"] for e in listed] == [MAN["latest-files.tar.gz"]["modal"]["path"]] != [first], listed
        print("ok real: the previous copy is gone, only the newest is listed")
    finally:
        r = subprocess.run([sys.executable, "-m", "modal", "volume", "delete", W.MODAL_STORE_VOLUME, "--yes"],
                           env={**os.environ, **keys}, capture_output=True, text=True, timeout=120)
        print("test volume deleted" if r.returncode == 0 else f"could not delete {W.MODAL_STORE_VOLUME}")
print("all passed")
