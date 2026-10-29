"""Kaggle backup-tier test: blob_save_kaggle writes a manifest pointing at the dataset (push is faked),
blob_load follows the kaggle branch, gating (no creds) and the throttle both hold. Hermetic.
Run: python3 tools/test_kaggle_store.py"""
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

# In-memory manifest store standing in for Supabase.
MAN = {}
W._manifest_get = lambda sb, name: MAN.get(name)
W._put_manifest = lambda sb, name, man: MAN.__setitem__(name, man)
W._forget_old = lambda *a, **k: None
W.state_key = lambda k: k
W.WORK = tempfile.mkdtemp()

# Gating: no creds -> kaggle_ok False, blob_save_kaggle is a no-op.
W.KAGGLE_USER, W._KAGGLE_KEY, W.KAGGLE_BACKUP, W._kaggle_ok[0] = "", "", "", None
assert W.kaggle_ok() is False
tmpf = os.path.join(W.WORK, "a.bin")
open(tmpf, "wb").write(b"x" * 1000)
assert W.blob_save_kaggle(None, "latest-files.tar.gz", tmpf) is None, "should no-op without creds"
print("ok gating: no creds -> no-op")

# With creds + a faked push: manifest gets a kaggle pointer.
W.KAGGLE_USER, W._KAGGLE_KEY, W.KAGGLE_BACKUP, W._kaggle_ok[0] = "u", "k", "u/panda-backups", True
pushed = []
W.kaggle_push_file = lambda path, name: (pushed.append((path, name)) or {"version": "v1"})
W._kaggle_last[0] = 0.0
got = W.blob_save_kaggle(None, "latest-files.tar.gz", tmpf, extra={"sig": "abc", "files": 3})
assert got and got["kaggle"] == "u/panda-backups", got
man = MAN["latest-files.tar.gz"]
assert man["kaggle"]["slug"] == "u/panda-backups" and man["kaggle"]["file"] == "latest-files.tar.gz", man
assert man["sig"] == "abc" and man["parts"] == [], man
assert pushed and pushed[0][1] == "latest-files.tar.gz", pushed
print("ok save: manifest carries the kaggle pointer, file pushed")

# Throttle: a second save within the interval is skipped (keeps last full copy).
assert W.blob_save_kaggle(None, "latest-files.tar.gz", tmpf) is None, "throttle not honoured"
print("ok throttle: second save within interval skipped")

# blob_load follows the kaggle branch: faked pull writes the bytes back, checksum verified.
W.kaggle_pull_file = lambda name, dest: (open(dest, "wb").write(b"x" * 1000) and True) or True
dest = os.path.join(W.WORK, "restored.bin")
assert W.blob_load(None, "latest-files.tar.gz", dest) is True
assert os.path.getsize(dest) == 1000, "pull did not restore the file"
print("ok load: kaggle branch pulls + checksum matches")

# A bad checksum is rejected.
MAN["latest-files.tar.gz"]["sha256"] = "deadbeef"
try:
    W.blob_load(None, "latest-files.tar.gz", dest)
    raise AssertionError("bad checksum not caught")
except RuntimeError as e:
    assert "checksum" in str(e), e
print("ok checksum: corrupted restore rejected")
print("all passed")
