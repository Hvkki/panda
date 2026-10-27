"""Local tests for the Kaggle hand-off and the work-file snapshot (no Kaggle, no Supabase, no Kiro Crew).

    python3 tools/test_autorestart.py        # plain asserts, or:  python3 -m pytest tools/test_autorestart.py

Covers the pure functions (file selection, trimming, split/join, hand-off timing, predecessor check),
blob_save/blob_load against a fake Storage (whole -> parts -> whole, stale parts removed, checksum),
and a full snapshot + restore of a temporary HOME, compared tree against tree.
What it cannot cover: a real Kaggle push, the `session` function, Kiro Crew itself.
"""
import filecmp
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
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="autorestart-cwd-"))  # worker.py makes ./work at import
import worker as W  # noqa: E402

os.chdir(_cwd)


class FakeStorage:
    """Just the Supabase methods the blob functions use; enforces a per-object size limit like Storage."""

    def __init__(self, limit=50 * 1024 * 1024):
        self.objects, self.limit = {}, limit

    def upload(self, name, path, bucket=None, mime=None):
        data = open(path, "rb").read()
        if len(data) > self.limit:
            raise RuntimeError(f"upload {name}: 413 The object exceeded the maximum allowed size")
        self.objects[name] = data

    def download(self, name, path, bucket=None):
        if name not in self.objects:
            return False
        open(path, "wb").write(self.objects[name])
        return True

    def delete_object(self, name, bucket=None):
        self.objects.pop(name, None)


def make_tree(home):
    def put(rel, data=b"x", mtime=None):
        p = os.path.join(home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(data)
        if mtime:
            os.utime(p, (mtime, mtime))
    put("dev/panda/app/src/App.tsx", b"app")
    put("dev/panda/app/node_modules/react/index.js", b"junk")
    put("dev/panda/app/dist/index.html", b"junk")
    put("dev/panda/.git/objects/ab/cdef", b"keep")
    put("dev/panda/pkg/__pycache__/a.cpython-313.pyc", b"junk")
    put("dev/panda/.venv/bin/python", b"junk")
    put("dev/panda/.cache/x", b"junk")
    put("work/deck/photo.png", os.urandom(300_000))
    put("work/huge.bin", b"\0" * 3000)
    put("outputs/report.pdf", b"%PDF" + os.urandom(1000))
    put("outputs/dist/keep-me.txt", b"deliverable in a folder called dist")
    put("projects/p1/notes.md", b"# notes")
    put("uploads/photo.jpg", os.urandom(5000))
    put(".panda/notes.txt", b"owner note")
    for secret in ("dev.env", "secrets.env", "drive.json", "browser.json", "engine.pid", "browser-engine.log"):
        put(f".panda/{secret}", b"SECRET")
    put(".panda/browser/state.json", b"cookies")
    put(".panda/stt-lib/faster_whisper/__init__.py", b"lib")
    put("Documents/other.txt", b"not a saved root")
    os.symlink("app/src/App.tsx", os.path.join(home, "dev/panda/link-rel"))
    os.symlink("/etc/hostname", os.path.join(home, "dev/panda/link-abs"))
    os.makedirs(os.path.join(home, "work/empty-dir"))


def arcs(entries):
    return {e.arc for e in entries}


def test_select_files():
    with tempfile.TemporaryDirectory() as home:
        make_tree(home)
        entries, skipped = W.select_files(home, W.FILES_ROOTS, max_file=2000)
        a = arcs(entries)
        assert "dev/panda/app/src/App.tsx" in a and "dev/panda/.git/objects/ab/cdef" in a
        assert "work/deck/photo.png" not in a  # 300 KB > the 2000 B test cap
        assert [s[0] for s in skipped].count("work/huge.bin") == 1 and any(s[0] == "work/deck/photo.png" for s in skipped)
        for gone in ("dev/panda/app/node_modules/react/index.js", "dev/panda/app/dist/index.html",
                     "dev/panda/pkg/__pycache__/a.cpython-313.pyc", "dev/panda/.venv/bin/python", "dev/panda/.cache/x",
                     ".panda/dev.env", ".panda/secrets.env", ".panda/drive.json", ".panda/browser.json",
                     ".panda/engine.pid", ".panda/browser-engine.log", ".panda/browser/state.json",
                     ".panda/stt-lib/faster_whisper/__init__.py", "Documents/other.txt"):
            assert gone not in a, gone
        assert ".panda/notes.txt" in a and "outputs/report.pdf" in a
        assert "outputs/dist/keep-me.txt" in a, "build-output names are only pruned in code trees"
        assert "work/empty-dir" in a and "dev/panda/link-rel" in a
        # exclude: the worker's own scratch dir is never packed
        entries2, _ = W.select_files(home, W.FILES_ROOTS, exclude=(os.path.join(home, "work", "deck"),))
        assert not any(e.arc.startswith("work/deck") for e in entries2)


def test_signature_and_trim():
    with tempfile.TemporaryDirectory() as home:
        make_tree(home)
        e1, _ = W.select_files(home, W.FILES_ROOTS)
        s1 = W.files_signature(e1)
        assert s1 == W.files_signature(W.select_files(home, W.FILES_ROOTS)[0])
        open(os.path.join(home, "outputs/new.txt"), "w").write("n")
        assert W.files_signature(W.select_files(home, W.FILES_ROOTS)[0]) != s1
    mk = lambda arc, size, mt: W.Entry(arc, "/x/" + arc, size, mt, "file")  # noqa: E731
    es = [mk("a", 100, 1), mk("b", 100, 2), mk("c", 100, 3), W.Entry("d", "/x/d", 0, 0, "dir")]
    kept, dropped = W.trim_to_budget(es, 250)
    assert [d[0] for d in dropped] == ["a"] and {e.arc for e in kept} == {"b", "c", "d"}
    assert W.trim_to_budget(es, 300)[1] == []
    kept, dropped = W.trim_to_budget(es, 0)
    assert len(dropped) == 3


def test_split_join():
    with tempfile.TemporaryDirectory() as d:
        for size in (0, 1, 999, 1000, 1001, 2500, 3000):
            src = os.path.join(d, "src")
            data = os.urandom(size)
            open(src, "wb").write(data)
            parts = W.split_file(src, 1000, os.path.join(d, f"o{size}"))
            assert all(os.path.getsize(p) <= 1000 for p in parts)
            assert len(parts) == max(1, -(-size // 1000)), (size, len(parts))
            out = os.path.join(d, "joined")
            sha = W.join_parts(parts, out)
            assert open(out, "rb").read() == data and sha == W.sha256_file(src)


def test_timing():
    M, L = 11 * 3600, 25 * 60
    due = lambda el, tries=0, last=None, now=0, have=False: W.handoff_due(el, M, L, tries, last, now, have)  # noqa: E731
    assert not due(0) and not due(M - L - 1)
    assert due(M - L) and due(M - 1)
    assert not due(M - L, have=True)
    assert not due(M - L, tries=1, last=1000, now=1060)          # retry only after 120 s
    assert due(M - L, tries=1, last=1000, now=1120)
    assert not due(M - L, tries=W.HANDOFF_MAX_TRIES, last=0, now=10_000)
    assert 300 <= W.HANDOFF_LEAD <= 40 * 60


def test_timestamps_and_predecessor():
    assert abs(W.parse_ts("2026-10-27T09:57:29.56+00:00") - W.parse_ts("2026-10-27T09:57:29.560000+00:00")) < 1e-6
    assert W.parse_ts("2026-10-27T09:57:29Z") == W.parse_ts("2026-10-27T09:57:29+00:00")
    assert W.parse_ts(None) is None and W.parse_ts("garbage") is None
    now = W.parse_ts("2026-10-27T12:00:00+00:00")
    iso = lambda dt: time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - dt))  # noqa: E731
    assert W.predecessor_gone(None, now)
    assert W.predecessor_gone({"status": "stopped"}, now) and W.predecessor_gone({"status": "error"}, now)
    assert not W.predecessor_gone({"status": "running", "heartbeat_at": iso(10)}, now)
    assert not W.predecessor_gone({"status": "stopping", "heartbeat_at": iso(100)}, now)   # draining, still beating
    assert W.predecessor_gone({"status": "running", "heartbeat_at": iso(W.PRED_STALE_SECS + 5)}, now)  # machine died
    assert W.predecessor_gone({"status": "running", "heartbeat_at": None}, now)
    assert not W.predecessor_gone({"status": "starting", "heartbeat_at": None}, now)


def test_flag():
    assert W._flag(None, True) and not W._flag(None, False) and W._flag("", True)
    assert not W._flag("0", True) and not W._flag("false", True) and not W._flag(False, True)
    assert W._flag("1", False) and W._flag(True, False)


def test_blob_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        W.WORK = d
        st = FakeStorage(limit=1000)
        big = os.urandom(2500)
        src, dst = os.path.join(d, "src"), os.path.join(d, "dst")
        open(src, "wb").write(big)
        assert W.blob_save(st, "x.tar.gz", src, part_bytes=1000)["parts"] == 3
        assert "owner-1" or True
        assert W.blob_load(st, "x.tar.gz", dst) and open(dst, "rb").read() == big
        names = sorted(st.objects)
        assert len(names) == 4 and any(n.endswith(".parts.json") for n in names)
        # a second save leaves exactly one complete set
        big2 = os.urandom(1800)
        open(src, "wb").write(big2)
        assert W.blob_save(st, "x.tar.gz", src, part_bytes=1000)["parts"] == 2
        assert len(st.objects) == 3, sorted(st.objects)
        assert W.blob_load(st, "x.tar.gz", dst) and open(dst, "rb").read() == big2
        # back to whole: manifest and parts are removed
        open(src, "wb").write(b"small")
        assert W.blob_save(st, "x.tar.gz", src, part_bytes=1000)["parts"] == 1
        assert sorted(st.objects) == ["x.tar.gz"]
        assert W.blob_load(st, "x.tar.gz", dst) and open(dst, "rb").read() == b"small"
        # an old single object (no manifest) still restores
        st2 = FakeStorage()
        st2.objects["y.tar.gz"] = b"legacy"
        assert W.blob_load(st2, "y.tar.gz", dst) and open(dst, "rb").read() == b"legacy"
        assert not W.blob_load(FakeStorage(), "none.tar.gz", dst)
        # corrupted part -> checksum error, never a silently wrong restore
        open(src, "wb").write(big)
        W.blob_save(st, "z.tar.gz", src, part_bytes=1000)
        victim = next(n for n in st.objects if n.endswith(".p001"))
        st.objects[victim] = b"\0" * len(st.objects[victim])
        try:
            W.blob_load(st, "z.tar.gz", dst)
            raise AssertionError("checksum should have failed")
        except RuntimeError as exc:
            assert "checksum" in str(exc)
        # the old limit: a 49 MB-class file is refused whole but stored in parts
        st3 = FakeStorage(limit=50 * 1024 * 1024)
        open(src, "wb").write(b"\1" * (60 * 1024 * 1024))
        assert W.blob_save(st3, "big.tar.gz", src)["parts"] == 2


def tree_equal(a, b):
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False, (cmp.left_only, cmp.right_only, cmp.diff_files)
    for sub in cmp.common_dirs:
        ok, why = tree_equal(os.path.join(a, sub), os.path.join(b, sub))
        if not ok:
            return ok, why
    return True, None


def test_snapshot_restore_temp_home():
    with tempfile.TemporaryDirectory() as root:
        home1, home2, work = (os.path.join(root, n) for n in ("home1", "home2", "work"))
        for p in (home1, home2, work):
            os.makedirs(p)
        make_tree(home1)
        saved = (W.HOME, W.WORK, W.STATE_PART_BYTES, W._files_sync["sig"], W.FILE_MAX_BYTES)
        try:
            W.HOME, W.WORK, W.STATE_PART_BYTES, W.FILE_MAX_BYTES = home1, work, 100_000, 2 * 1024 * 1024
            st = FakeStorage(limit=150_000)
            info = W.files_snapshot(st, "test")
            assert info["parts"] >= 3 and info["where"] == "Storage" and info["skipped"] == 0, info
            assert W.files_snapshot(st, "again") == {"unchanged": True}      # nothing changed: no upload
            # a fresh machine: restore into an empty home, then compare to the original
            W.HOME, W._files_sync["sig"] = home2, None
            assert W.restore_files(st) == "restored"
            expect = os.path.join(root, "expect")
            os.makedirs(expect)
            # what must come back = the selection of home1 (symlink to an absolute path is skipped by the data filter)
            entries, _ = W.select_files(home1, W.FILES_ROOTS)
            for e in entries:
                dst = os.path.join(expect, e.arc)
                if e.kind == "dir":
                    os.makedirs(dst, exist_ok=True)
                elif e.kind == "file":
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    os.link(e.path, dst)
                elif os.readlink(e.path).startswith("/"):
                    continue  # an absolute link is refused on restore (tarfile "data" filter)
                else:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    os.symlink(os.readlink(e.path), dst)
            got = {a for a in (os.path.relpath(os.path.join(dp, f), home2) for dp, _, fs in os.walk(home2) for f in fs if not os.path.islink(os.path.join(dp, f)))}
            want = {e.arc for e in entries if e.kind == "file"}
            assert got == want, (got ^ want)
            ok, why = tree_equal(expect, home2)
            assert ok, why
            assert os.path.islink(os.path.join(home2, "dev/panda/link-rel"))
            assert os.path.isdir(os.path.join(home2, "work/empty-dir"))
            assert not os.path.exists(os.path.join(home2, ".panda/dev.env"))
            assert not os.path.exists(os.path.join(home2, "dev/panda/app/node_modules"))
            # restored state is "in sync": no re-upload right after a restore
            assert W.files_snapshot(st, "after-restore") == {"unchanged": True}
            # a file over the cap is logged and skipped, the rest is still saved
            open(os.path.join(home2, "work/too-big.bin"), "wb").write(b"\0" * (W.FILE_MAX_BYTES + 1))
            assert W.files_snapshot(st, "cap-only") == {"unchanged": True}   # a skipped file alone changes nothing
            open(os.path.join(home2, "work/new.txt"), "w").write("new")
            info = W.files_snapshot(st, "cap")
            assert info["skipped"] == 1, info
        finally:
            W.HOME, W.WORK, W.STATE_PART_BYTES, W._files_sync["sig"], W.FILE_MAX_BYTES = saved


def test_over_storage_goes_to_drive_or_is_trimmed():
    with tempfile.TemporaryDirectory() as root:
        home, work = os.path.join(root, "h"), os.path.join(root, "w")
        os.makedirs(home)
        os.makedirs(work)
        for i in range(6):
            p = os.path.join(home, f"outputs/f{i}.bin")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(os.urandom(100_000))
            os.utime(p, (1000 + i, 1000 + i))
        saved = (W.HOME, W.WORK, W.FILES_STORAGE_MAX, W.DRIVE_CFG, W.drive_put, W.drive_get, W.drive_rm, W._files_sync["sig"])
        drive = {}
        try:
            W.HOME, W.WORK, W.FILES_STORAGE_MAX = home, work, 250_000
            W._files_sync["sig"] = None
            st = FakeStorage()
            # no Drive: oldest files are dropped until the archive fits
            W.DRIVE_CFG = os.path.join(root, "no-drive.json")
            info = W.files_snapshot(st, "trim")
            assert info["where"] == "Storage" and 0 < info["skipped"] < 6 and info["archive_bytes"] <= 250_000, info
            # Drive connected: whole archive goes to Drive, Storage keeps only the manifest
            open(os.path.join(root, "drive.json"), "w").write("{}")
            W.DRIVE_CFG = os.path.join(root, "drive.json")

            def fake_put(path, folder="outputs"):
                drive["id"] = "D" * 33
                drive["data"] = open(path, "rb").read()
                return {"id": drive["id"], "link": "https://drive.example/x"}

            def fake_get(fid, dest):
                open(dest, "wb").write(drive["data"])
                return fid == drive["id"]
            W.drive_put, W.drive_get, W.drive_rm = fake_put, fake_get, lambda fid: drive.setdefault("rm", fid)
            W._files_sync["sig"] = None
            info = W.files_snapshot(st, "drive")
            assert info["where"] == "Google Drive" and info["skipped"] == 0, info
            assert [n for n in st.objects if not n.endswith(".parts.json")] == []
            home2 = os.path.join(root, "h2")
            os.makedirs(home2)
            W.HOME, W._files_sync["sig"] = home2, None
            assert W.restore_files(st) == "restored"
            assert len(os.listdir(os.path.join(home2, "outputs"))) == 6
        finally:
            (W.HOME, W.WORK, W.FILES_STORAGE_MAX, W.DRIVE_CFG, W.drive_put, W.drive_get, W.drive_rm, W._files_sync["sig"]) = saved


def test_extract_refuses_escapes():
    import io
    import tarfile
    with tempfile.TemporaryDirectory() as d:
        arc, home = os.path.join(d, "a.tar.gz"), os.path.join(d, "home")
        os.makedirs(home)
        with tarfile.open(arc, "w:gz") as t:
            for name, data in (("work/ok.txt", b"ok"), ("../escape.txt", b"bad"), ("/abs.txt", b"bad"),
                               ("elsewhere/x.txt", b"bad")):
                ti = tarfile.TarInfo(name)
                ti.size = len(data)
                t.addfile(ti, io.BytesIO(data))
            ln = tarfile.TarInfo("work/abs-link")
            ln.type, ln.linkname = tarfile.SYMTYPE, "/etc/passwd"
            t.addfile(ln)
        ok, bad = W.extract_archive(arc, home, W.FILES_ROOTS)
        assert ok == 1 and bad == 4, (ok, bad)
        assert os.path.exists(os.path.join(home, "work/ok.txt")) and not os.path.exists(os.path.join(d, "escape.txt"))


class FakeRows:
    """worker_sessions only: rest(GET/PATCH) by id."""

    def __init__(self, rows):
        self.rows = rows

    def rest(self, method, path, *, params=None, body=None, prefer=""):
        rid = params["id"].replace("eq.", "")
        if method == "PATCH":
            self.rows[rid].update(body)
            return None
        return [self.rows[rid]] if rid in self.rows else []


def test_handoff_step_overlap_and_sequential():
    saved = (W.T0, W.AUTO_RESTART, W.HANDOFF_MODE, W.snapshot, W.request_successor, W.MAX_RUNTIME, W.HANDOFF_LEAD)
    calls = []
    try:
        W.MAX_RUNTIME, W.HANDOFF_LEAD, W.AUTO_RESTART, W.HANDOFF_MODE = 11 * 3600, 25 * 60, True, "overlap"
        W.snapshot = lambda sb, reason, files=True: calls.append(("snapshot", reason))
        W.request_successor = lambda sb, sid: calls.append(("request", sid)) or {"id": "S2"}
        rows = {"S1": {"status": "running"}, "S2": {"status": "starting", "info": {}}}
        sb = FakeRows(rows)
        sets = []
        ho = {"to": None, "tries": 0, "last": None}
        W.T0 = time.time() - 3600                                   # 1 h in: nothing yet
        assert W.handoff_step(sb, "S1", ho, lambda **kw: sets.append(kw), True) is None and not calls
        W.T0 = time.time() - (11 * 3600 - 25 * 60 + 5)               # 25 min before the guard
        assert W.handoff_step(sb, "S1", ho, lambda **kw: sets.append(kw), False) is None
        assert calls == [("snapshot", "pre-handoff"), ("request", "S1")] and ho["to"] == "S2"
        assert W.SESSION_INFO["handoff"]["to"] == "S2" and sets
        # successor not ready: keep serving; a second call never asks again
        assert W.handoff_step(sb, "S1", ho, lambda **kw: None, True) is None and len(calls) == 2
        rows["S2"]["info"] = {"handoff_ready": True}
        assert W.handoff_step(sb, "S1", ho, lambda **kw: None, False) is None   # only looks on heartbeat ticks
        assert W.handoff_step(sb, "S1", ho, lambda **kw: None, True) == "handoff"
        # successor died: ask again after the retry gap
        rows["S2"] = {"status": "error", "info": {}}
        ho = {"to": "S2", "tries": 1, "last": time.time() - 500}
        assert W.handoff_step(sb, "S1", ho, lambda **kw: None, True) is None and ho["to"] is None
        assert W.handoff_step(sb, "S1", ho, lambda **kw: None, False) is None and ho["to"] == "S2"
        # request keeps failing: bounded number of tries
        W.request_successor = lambda sb, sid: (_ for _ in ()).throw(RuntimeError("502 kaggle"))
        ho = {"to": None, "tries": 0, "last": None}
        for i in range(10):
            W.handoff_step(sb, "S1", ho, lambda **kw: None, False)
            ho["last"] = (ho["last"] or 0) - 1000
        assert ho["tries"] == W.HANDOFF_MAX_TRIES and ho["to"] is None
        # sequential: leaves the loop at once, successor is requested after the final snapshot (main)
        W.HANDOFF_MODE = "sequential"
        ho = {"to": None, "tries": 0, "last": None}
        assert W.handoff_step(sb, "S1", ho, lambda **kw: None, False) == "handoff"
        # switched off: never
        W.AUTO_RESTART = False
        ho = {"to": None, "tries": 0, "last": None}
        assert W.handoff_step(sb, "S1", ho, lambda **kw: None, True) is None
    finally:
        (W.T0, W.AUTO_RESTART, W.HANDOFF_MODE, W.snapshot, W.request_successor, W.MAX_RUNTIME, W.HANDOFF_LEAD) = saved
        W.SESSION_INFO.clear()


def test_wait_for_predecessor():
    saved = (W.time.sleep, W.HEARTBEAT_SECS)
    try:
        polls = []
        rows = {"S1": {"status": "stopping", "heartbeat_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())},
                "S2": {"stop_requested": False}}
        sb = FakeRows(rows)

        def sleep(_):
            polls.append(1)
            if len(polls) == 2:
                rows["S1"]["status"] = "stopped"
        W.time.sleep = sleep
        assert W.wait_for_predecessor(sb, "S2", "S1", lambda **kw: rows["S2"].update(kw)) == "go"
        assert len(polls) == 2 and W.SESSION_INFO["handoff_ready"] is True
        # the user pressed Stop while the successor waited: it must not take over
        rows["S1"]["status"] = "stopping"
        rows["S2"]["stop_requested"] = True
        assert W.wait_for_predecessor(sb, "S2", "S1", lambda **kw: None) == "stop"
    finally:
        W.time.sleep, W.HEARTBEAT_SECS = saved
        W.SESSION_INFO.clear()


if __name__ == "__main__":
    names = [n for n in sorted(globals()) if n.startswith("test_")]
    for n in names:
        globals()[n]()
        print("  ok  ", n)
    print(f"{len(names)} tests passed")
