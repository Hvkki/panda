"""Kaggle full-copy store: A/B datasets, the manifest switches only after Kaggle lists the new file, and
a pull fetches exactly the file the manifest names.

    python3 tools/test_kaggle_store.py           hermetic (fake Kaggle, in-memory manifests)
    python3 tools/test_kaggle_store.py --real    also a real round-trip through the kaggle CLI with the
                                                 worker.json creds, on throwaway datasets panda-kgtest-*
"""
import glob
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
import worker as W  # noqa: E402

REAL = "--real" in sys.argv
saved = {k: getattr(W, k) for k in ("_manifest_get", "_put_manifest", "_forget_old", "kaggle_push_file",
                                     "kaggle_pull_file", "kaggle_listed", "OWNER", "PRIMARY_OWNER")}
MAN = {}
W._manifest_get = lambda sb, name: MAN.get(name)
W._put_manifest = lambda sb, name, man: MAN.__setitem__(name, man)
forgot = []
W._forget_old = lambda sb, name, old, **k: forgot.append(old)
W.WORK = tempfile.mkdtemp()

# Gating: no creds -> nothing runs.
W.KAGGLE_USER, W._KAGGLE_KEY, W._kaggle_ok[0] = "", "", None
assert W.kaggle_ok() is False
src = os.path.join(W.WORK, "a.bin")
open(src, "wb").write(os.urandom(4000))
assert W.blob_save_kaggle(None, "latest-files.tar.gz", src) is None
print("ok gating: no creds -> no-op")

# Per-account slots: the primary account and a second one never share a dataset.
W.KAGGLE_USER, W._KAGGLE_KEY, W._kaggle_ok[0] = "u", "k", True
W.OWNER = W.PRIMARY_OWNER = "owner-1"
a, b = W.kaggle_slots()
assert (a, b) == ("u/panda-work-a", "u/panda-work-b"), (a, b)
W.OWNER = "D3D22793-aaaa-bbbb"
a2, b2 = W.kaggle_slots()
assert a2 == "u/panda-work-d3d22793-a" and {a2, b2}.isdisjoint({a, b}), (a2, b2)
W.OWNER = "owner-1"
print("ok slots: A/B per account")

# Fake Kaggle: a dataset holds exactly the files of its newest pushed version.
KG = {}
LISTED = {"ok": True}


def fake_push(path, slug, fname, wait=None):
    if not LISTED["ok"]:
        return False                        # pushed, but never listed (processing failed or too slow)
    KG[slug] = {fname: open(path, "rb").read()}
    return True


def fake_pull(slug, fname, dest, wait=None):
    data = KG.get(slug, {}).get(fname)
    if data is None:
        return False
    open(dest, "wb").write(data)
    return True


W.kaggle_push_file, W.kaggle_pull_file = fake_push, fake_pull
got = W.blob_save_kaggle(None, "latest-files.tar.gz", src, extra={"sig": "s1", "files": 3})
m1 = MAN["latest-files.tar.gz"]
assert got and m1["kaggle"]["slug"] == a and m1["kaggle"]["file"].startswith("latest-files-"), m1
assert m1["sig"] == "s1" and m1["parts"] == [] and m1["sha256"] == W.sha256_file(src)
open(src, "wb").write(os.urandom(5000))
W.blob_save_kaggle(None, "latest-files.tar.gz", src)
m2 = MAN["latest-files.tar.gz"]
assert m2["kaggle"]["slug"] == b and m2["kaggle"]["file"] != m1["kaggle"]["file"], m2
assert KG[a] and KG[b], "the copy in A must still be there while B is the newest"
W.blob_save_kaggle(None, "latest-files.tar.gz", src)
assert MAN["latest-files.tar.gz"]["kaggle"]["slug"] == a
print("ok save: copies alternate A -> B -> A, the other slot keeps the previous copy")

# A push Kaggle never lists: the manifest keeps naming the last good copy, nothing is forgotten.
before, nforgot = dict(MAN["latest-files.tar.gz"]), len(forgot)
LISTED["ok"] = False
assert W.blob_save_kaggle(None, "latest-files.tar.gz", src) is None
assert MAN["latest-files.tar.gz"] == before and len(forgot) == nforgot
LISTED["ok"] = True
print("ok failed push: last good copy still named, nothing deleted")

# Load: exactly the named file, checksum verified.
dest = os.path.join(W.WORK, "restored.bin")
assert W.blob_load(None, "latest-files.tar.gz", dest) and open(dest, "rb").read() == open(src, "rb").read()
MAN["latest-files.tar.gz"]["sha256"] = "0" * 64
try:
    W.blob_load(None, "latest-files.tar.gz", dest)
    raise AssertionError("bad checksum not caught")
except RuntimeError as e:
    assert "checksum" in str(e), e
print("ok load: named file fetched, a wrong checksum is refused")

for k, v in saved.items():
    setattr(W, k, v)

if REAL:
    import tarfile
    cfg = json.load(open(glob.glob("/kaggle/input/**/worker.json", recursive=True)[0]))
    W.KAGGLE_USER, W._KAGGLE_KEY, W._kaggle_ok[0] = cfg["kaggle_username"], cfg["kaggle_key"], None
    assert W.kaggle_ok()
    slug = f"{W.KAGGLE_USER}/panda-kgtest-{int(time.time()) % 100000}"
    blob = os.path.join(W.WORK, "blob.pbak")
    for i in (1, 2):                          # 1 creates the dataset, 2 is a new version of it
        payload = os.path.join(W.WORK, f"payload{i}.bin")
        open(payload, "wb").write(os.urandom(3 << 20))
        with tarfile.open(blob, "w:gz", compresslevel=1) as t:   # a real gzip'd tar, like a work-file copy
            t.add(payload, arcname=f"work/payload{i}.bin")
        fname = f"latest-files-test{i}-{int(time.time())}.pbak"
        t0 = time.time()
        assert W.kaggle_push_file(blob, slug, fname, wait=600), f"push {i} failed"
        print(f"ok real push {i}: listed after {time.time() - t0:.0f} s")
        assert W.kaggle_listed(slug, fname) is True
        out = os.path.join(W.WORK, "pulled.bin")
        assert W.kaggle_pull_file(slug, fname, out, wait=60) and W.sha256_file(out) == W.sha256_file(blob)
        print(f"ok real pull {i}: same bytes")
    assert W.kaggle_listed(slug, fname.replace("test2", "test1")) is False, "old version should be gone"
    assert W.kaggle_pull_file(slug, "no-such-file.pbak", out, wait=0) is False
    print(f"ok real: only the newest copy is listed; a missing file is a clean False ({slug})")
print("all passed")
