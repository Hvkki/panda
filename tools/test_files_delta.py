"""Work-files save/restore when the full copy cannot be replaced (Drive full, or too big for Storage).

Regression for 28 Oct: Drive refused the 1 GB copy, the worker saved a TRIMMED copy (newest files only)
over the full one, and the next Start restored only files changed in the last ~20 minutes.
Now the full copy stays and only the changes are saved on top. Runs against the real Storage under a
throwaway account folder (users/test-delta-*/), with a temp home; never touches the real copy.
Run: python3 tools/test_files_delta.py
"""
import os
import shutil
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
import worker as W  # noqa: E402

tmp = tempfile.mkdtemp()
W.HOME, W.WORK = f"{tmp}/home", f"{tmp}/work"
os.makedirs(W.WORK)
W.FILES_ROOTS = ["work", "dev"]
W.OWNER = f"test-delta-{uuid.uuid4().hex[:8]}"
W.DRIVE_CFG = f"{tmp}/no-drive.json"
W.FILES_STORAGE_MAX = 2 * W.MiB
alerts = []
W.files_alert = lambda sb, text: alerts.append(text)
sb = W.Supabase()
H = W.HOME
want = {}


def put(rel, data):
    p = f"{H}/{rel}"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
    want[rel] = data


def check(label):
    shutil.rmtree(H)
    W._files_restore["failed"] = False
    assert W.restore_files(sb) == "restored", label
    got = {}
    for dp, _, fs in os.walk(H):
        for f in fs:
            got[os.path.relpath(os.path.join(dp, f), H)] = open(os.path.join(dp, f), "rb").read()
    missing = sorted(set(want) - set(got))
    wrong = sorted(k for k in want if k in got and got[k] != want[k])
    assert not missing and not wrong, f"{label}: missing {missing}, wrong {wrong}"
    print(f"ok {label}: all {len(want)} files back")


# ~/work/tmp is regenerable scratch (render frames, temp output) and must never be saved -- it used
# to re-upload GBs on every backup. select_files skips it; work/studio (real work) is still saved.
os.makedirs(f"{H}/work/tmp/render", exist_ok=True)
open(f"{H}/work/tmp/render/frames.bin", "wb").write(b"scratch")
os.makedirs(f"{H}/work/studio", exist_ok=True)
open(f"{H}/work/studio/keep.bin", "wb").write(b"real")
_arcs = {e.arc for e in W.select_files(H, ["work"])[0]}
assert "work/studio/keep.bin" in _arcs, _arcs
assert not any(a.startswith("work/tmp/") for a in _arcs), f"work/tmp not skipped: {sorted(_arcs)}"
shutil.rmtree(f"{H}/work")                 # clean slate for the Storage round-trip below
print("ok tmp-skip: ~/work/tmp excluded from the save set")

try:
    # 1. a full copy that fits in Storage
    put("dev/repo/.git/HEAD", b"ref: refs/heads/main\n")
    for i in range(3):
        put(f"work/studio/old{i}.bin", os.urandom(500 << 10))
    info = W.files_snapshot(sb, "test")
    assert info["where"] == "Storage" and info["files"] == 4, info
    base_bytes = sb.get_fresh(W.state_key(W.FILES_BLOB))
    assert base_bytes, "no full copy in Storage"
    check("full copy")

    # 2. it grows past the Storage limit (no Drive): the old code kept only the newest files here
    for i in range(3):
        put(f"work/studio/new{i}.bin", os.urandom(500 << 10))
    put("work/studio/old0.bin", b"edited later\n")
    info = W.files_snapshot(sb, "test")
    assert info["where"].startswith("Storage (changes"), info
    assert info["files"] == 4, info                        # 3 new + 1 edited, not the untouched old ones
    assert sb.get_fresh(W.state_key(W.FILES_BLOB)) == base_bytes, "the full copy was replaced"
    check("too big for Storage: full copy + changes")

    # 3. Drive refuses the copy (full): same, and the owner is told
    W.DRIVE_CFG = f"{tmp}/drive.json"
    open(W.DRIVE_CFG, "w").write("{}")
    W.blob_save_drive = lambda *a, **k: None
    put("work/studio/new3.bin", os.urandom(300 << 10))
    info = W.files_snapshot(sb, "test")
    assert info["where"].startswith("Storage (changes") and info["files"] == 5, info
    assert alerts and "Google Drive" in alerts[0], alerts
    check("Drive refused: full copy + changes")

    # 4. a later full copy fits again: it replaces the base and the change-only copy is dropped
    W.FILES_STORAGE_MAX = 50 * W.MiB
    W.DRIVE_CFG = f"{tmp}/no-drive.json"
    W._files_sync["sig"] = None          # the restore above marked the disk as saved
    info = W.files_snapshot(sb, "test")
    assert info["where"] == "Storage" and info["files"] == 8, info
    assert sb.get_fresh(W.state_key(W.FILES_DELTA_META)) is None, "old change-only copy left behind"
    check("new full copy")

    # 5. a change-only copy made for an OLDER full copy is never applied over a newer one
    sb.put(W.state_key(W.FILES_DELTA_META), b'{"base_id": "someone-else", "files": 1}', mime="application/json")
    check("stale change-only copy ignored")
finally:
    for n in (W.FILES_BLOB, W.FILES_DELTA_BLOB):
        W._forget_old(sb, n, W._manifest_get(sb, n), keep_single=False, keep_manifest=False)
    for n in (W.FILES_INDEX, W.FILES_DELTA_META):
        sb.delete_object(W.state_key(n))
    shutil.rmtree(tmp, ignore_errors=True)
print("all passed")
