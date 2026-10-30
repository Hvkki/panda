"""Work-files save/restore: a full copy plus EVERY change since it, never trimmed.

Regressions:
- 28 Oct: Drive refused the copy and a TRIMMED copy (newest files only) replaced the full one.
- 29 Oct 18:08 and 30 Oct 04:59 (agent issues #32, #40): the change-only copy was trimmed to 200 MB,
  oldest changes first, so restarts came back with broken git repos, empty folders and lost videos;
  node_modules under ~/work was never saved.
Runs against the real Storage under a throwaway account folder (users/test-delta-*/), with a temp home
and a fake Kaggle/Drive; never touches the real copy.  Run: python3 tools/test_files_delta.py
"""
import os
import shutil
import sys
import tempfile
import time
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
W._kaggle_ok[0] = False
alerts = []
W.files_alert = lambda sb, text, kind="save": alerts.append((kind, text))
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


def save(**kw):
    time.sleep(1.1)        # tar keeps whole-second mtimes: a change must land in a later second
    return W.files_snapshot(sb, "test", **kw)


# What is saved: ~/work/tmp is scratch (never saved); node_modules is kept under ~/work only.
for rel in ("work/tmp/render/frames.bin", "work/studio/keep.bin", "work/studio/node_modules/x/index.js",
            "dev/repo/node_modules/y/index.js"):
    os.makedirs(os.path.dirname(f"{H}/{rel}"), exist_ok=True)
    open(f"{H}/{rel}", "wb").write(b"data")
_arcs = {e.arc for e in W.select_files(H, ["work", "dev"])[0]}
assert "work/studio/keep.bin" in _arcs and "work/studio/node_modules/x/index.js" in _arcs, _arcs
assert not any(a.startswith("work/tmp/") for a in _arcs), "work/tmp not skipped"
assert "dev/repo/node_modules/y/index.js" not in _arcs, "node_modules in a code tree should be skipped"
shutil.rmtree(H)
print("ok selection: work/tmp skipped, node_modules kept under ~/work only")

# The plan is made before packing: a stop never makes a full copy; big changes or an old copy do.
E = W.Entry
ents = [E(f"work/f{i}", "", 1 << 20, 100, "file") for i in range(5)]
idx = {"id": "x", "saved_at": W.now_iso(), "files": {e.arc: [e.size, e.mtime] for e in ents}}
assert W.files_plan(ents, idx, True, time.time(), True, True)[0] == "delta"
assert W.files_plan(ents, idx, False, time.time(), False, True)[0] == "full"
assert W.files_plan(ents, idx, True, time.time(), False, True) == ("delta", "0 files changed")
big = ents + [E("work/video.mp4", "", 150 << 20, 200, "file")]
assert W.files_plan(big, idx, True, time.time(), False, True)[0] == "full", "a 150 MB change needs a full copy"
old = dict(idx, saved_at="2026-01-01T00:00:00+00:00")
assert W.files_plan(ents, old, True, time.time(), False, True)[0] == "full"
assert W.files_plan(big, idx, True, time.time(), False, False)[0] == "delta", "no big store: changes only"
print("ok plan: decided before packing")

drive_calls = []
try:
    # 1. a full copy that fits in Storage
    put("dev/repo/.git/HEAD", b"ref: refs/heads/main\n")
    for i in range(3):
        put(f"work/studio/old{i}.bin", os.urandom(500 << 10))
    put("work/studio/node_modules/lib/index.js", b"module.exports = 1\n")
    info = save()
    assert info["where"] == "Storage" and info["files"] == 5, info
    base_bytes = sb.get_fresh(W.state_key(W.FILES_BLOB))
    assert base_bytes, "no full copy in Storage"
    check("full copy")

    # 2. it grows past the Storage limit, no Kaggle/Drive: the full copy stays, the changes go on top
    for i in range(3):
        put(f"work/studio/new{i}.bin", os.urandom(500 << 10))
    put("work/studio/old0.bin", b"edited later\n")
    info = save()
    assert info["where"].startswith("Storage (changes") and info["files"] == 4, info
    assert sb.get_fresh(W.state_key(W.FILES_BLOB)) == base_bytes, "the full copy was replaced"
    check("too big for Storage: full copy + changes")

    # 3. the changes alone outgrow the Storage budget: the old code dropped the oldest; now all stay
    put("work/rsnap/a/studio.bin", os.urandom(1200 << 10))
    put("work/voice/line01.wav", os.urandom(1200 << 10))
    alerts.clear()
    info = save()
    assert info["files"] == 6 and info["archive_bytes"] > W.FILES_STORAGE_MAX, info
    assert any(k == "big-delta" for k, _ in alerts), alerts
    check("big change-only copy, nothing trimmed")

    # 4. Drive refuses a full copy: the owner is told, the changes are saved on top, no retry for an hour
    W.DRIVE_CFG = f"{tmp}/drive.json"
    open(W.DRIVE_CFG, "w").write("{}")
    W.blob_save_drive = lambda *a, **k: (drive_calls.append(1) and None)
    W.FILES_DELTA_RAW_MAX = 1 * W.MiB
    alerts.clear()
    put("work/studio/new3.bin", os.urandom(300 << 10))
    info = save()
    assert len(drive_calls) == 1 and info["where"].startswith("Storage (changes"), (drive_calls, info)
    assert any(k == "full-failed" for k, _ in alerts), alerts
    put("work/studio/new4.bin", os.urandom(100 << 10))
    info = save()
    assert len(drive_calls) == 1, "a failed full copy must not be retried at every save"
    assert sb.get_fresh(W.state_key(W.FILES_BLOB)) == base_bytes
    check("Drive refused: full copy + changes")

    # 5. Kaggle is the home of the big full copy (fake Kaggle in memory)
    KG = {}

    def fake_push(path, slug, fname, wait=None):
        KG[slug] = {fname: open(path, "rb").read()}
        return True

    def fake_pull(slug, fname, dest, wait=None):
        if fname not in KG.get(slug, {}):
            return False
        open(dest, "wb").write(KG[slug][fname])
        return True

    W.kaggle_push_file, W.kaggle_pull_file, W._kaggle_ok[0] = fake_push, fake_pull, True
    W.KAGGLE_USER = "u"
    W._files_full["fail_at"] = 0.0
    put("work/studio/new5.bin", os.urandom(200 << 10))
    info = save()
    assert info["where"] == "Kaggle dataset", info
    man = W._manifest_get(sb, W.FILES_BLOB)
    slug_a = man["kaggle"]["slug"]
    assert slug_a.endswith("-a") and not sb.exists(W.state_key(W.FILES_BLOB)), "Storage copy not removed"
    assert sb.get_fresh(W.state_key(W.FILES_DELTA_META)) is None, "old change-only copy left behind"
    assert not drive_calls[1:], "Drive used although Kaggle took the copy"
    check("full copy in Kaggle")

    # 6. a small change: only the changes, on top of the Kaggle copy
    put("work/studio/old1.bin", b"edited again\n")
    info = save()
    assert info["where"].startswith("Storage (changes") and info["files"] == 1, info
    check("Kaggle copy + changes")

    # 7. at Stop only the changes are saved, even when a full copy would be due
    put("work/studio/new6.bin", os.urandom(1500 << 10))
    info = save(budget=120)
    assert info["where"].startswith("Storage (changes"), info
    check("stop: changes only")

    # 8. the next full copy goes to the other dataset; the previous one is untouched
    info = save()          # nothing changed: skipped
    assert info == {"unchanged": True}, info
    put("work/studio/new7.bin", os.urandom(100 << 10))
    info = save()
    man2 = W._manifest_get(sb, W.FILES_BLOB)
    assert info["where"] == "Kaggle dataset" and man2["kaggle"]["slug"].endswith("-b"), (info, man2)
    assert KG[slug_a], "the copy in the A dataset must still exist"
    check("second full copy in the B dataset")

    # 9. a change-only copy made for an OLDER full copy is never applied over a newer one
    sb.put(W.state_key(W.FILES_DELTA_META), b'{"base_id": "someone-else", "files": 1}', mime="application/json")
    check("stale change-only copy ignored")
    sb.delete_object(W.state_key(W.FILES_DELTA_META))

    # 10. a full copy still in Drive (from before Kaggle) moves to Kaggle at the next save; the Drive file goes
    DRIVE = {}

    def fake_drive_save(sb_, name, path, extra=None):
        old = W._manifest_get(sb_, name)
        fid = f"drive-{len(DRIVE)}"
        DRIVE[fid] = open(path, "rb").read()
        W._put_manifest(sb_, name, {"v": 1, "size": os.path.getsize(path), "sha256": W.sha256_file(path),
                                    "parts": [], "drive": {"id": fid}, "saved_at": W.now_iso(), **(extra or {})})
        W._forget_old(sb_, name, old, keep_single=False, keep_manifest=True)
        return {"size": os.path.getsize(path), "parts": 0, "drive": fid}

    W.blob_save_drive = fake_drive_save
    W.drive_rm = lambda fid: DRIVE.pop(fid, None)
    W.drive_get = lambda fid, dest: bool(open(dest, "wb").write(DRIVE[fid])) if fid in DRIVE else False
    W._kaggle_ok[0] = False
    put("work/studio/new8.bin", os.urandom(1200 << 10))
    info = save()
    assert info["where"] == "Google Drive" and len(DRIVE) == 1, info
    check("full copy in Drive (no Kaggle)")
    W._kaggle_ok[0] = True
    put("work/studio/new9.bin", os.urandom(50 << 10))       # a small change: normally only a change-only copy
    info = save()
    assert info["where"] == "Kaggle dataset", info
    assert not DRIVE, "the Drive copy should be deleted once the Kaggle copy is in"
    check("Drive copy moved to Kaggle")
    put("work/studio/new10.bin", os.urandom(50 << 10))
    assert save()["where"].startswith("Storage (changes"), "a Kaggle copy must not be redone at every save"
finally:
    for n in (W.FILES_BLOB, W.FILES_DELTA_BLOB):
        W._forget_old(sb, n, W._manifest_get(sb, n), keep_single=False, keep_manifest=False)
    for n in (W.FILES_INDEX, W.FILES_DELTA_META):
        sb.delete_object(W.state_key(n))
    shutil.rmtree(tmp, ignore_errors=True)
print("all passed")
