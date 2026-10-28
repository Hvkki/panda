"""Stop-speed test for the chunked ~/.kiro save (kiro_save / kiro_restore), no network.

    python tools/test_kiro_chunks.py [SOURCE_KIRO_DIR] [--old]

SOURCE_KIRO_DIR defaults to a fake ~/.kiro made here (1.4 GB of transcript-like jsonl, like the real
one). Times a first save, an unchanged save, a save after one big chat grows, and a restore into a fresh
folder (checked byte for byte). --old also times the previous method (tar.gz of everything) to compare.
Prints one JSON report; on Kaggle it also writes /kaggle/working/result.json.
"""
import hashlib
import json
import os
import random
import shutil
import sys
import tarfile
import tempfile
import threading
import time

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
import worker as W  # noqa: E402

MB = 1 << 20
UPLINK = 4 * MB  # what the owner saw Kaggle upload at; used to turn bytes into seconds


class FakeStore:
    """Stands in for Supabase Storage: put/get/delete_object, counting what goes over the wire."""
    def __init__(self):
        self.d, self.up, self.lock = {}, 0, threading.Lock()

    def put(self, name, data, mime="application/octet-stream"):
        with self.lock:
            self.d[name] = bytes(data)
            self.up += len(data)

    def get(self, name):
        return self.d.get(name)

    def delete_object(self, name):
        self.d.pop(name, None)


def fake_kiro(base, total=1400 * MB):
    """Transcript-like jsonl: repeated JSON with random ids and some base64 (compresses ~3x, like real)."""
    rnd = random.Random(7)
    sizes = [310, 162, 150, 147, 140] + [20] * 20 + [1] * 90
    scale = total / (sum(sizes) * MB)
    os.makedirs(f"{base}/sessions/cli", exist_ok=True)
    os.makedirs(f"{base}/crew/sessions", exist_ok=True)
    for i, s in enumerate(sizes):
        want = int(s * MB * scale)
        with open(f"{base}/sessions/cli/s{i:03d}.jsonl", "wb") as f:
            n = 0
            while n < want:
                blob = rnd.randbytes(600).hex() if rnd.random() < 0.3 else ""
                line = json.dumps({"role": rnd.choice(["user", "assistant", "tool"]), "id": rnd.getrandbits(64),
                                   "text": "Running a command to check the mix output " * rnd.randint(1, 30),
                                   "data": blob}) + "\n"
                f.write(line.encode())
                n += len(line)
        open(f"{base}/sessions/cli/s{i:03d}.json", "w").write(json.dumps({"id": i}))
    open(f"{base}/sessions/cli/s000.lock", "w").write("x")


def tree_sha(base):
    h = hashlib.sha256()
    for arc, p in sorted(W._kiro_walk(base)):
        h.update(arc.encode())
        h.update(open(p, "rb").read())
    return h.hexdigest()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tmp = tempfile.mkdtemp(prefix="kirochunks-")
    if args:
        src = os.path.abspath(args[0])
        base = f"{tmp}/src"
        for d in W.EXTRA_DIRS:  # copy, so the test never touches the live folder
            if os.path.isdir(f"{src}/{d}"):
                shutil.copytree(f"{src}/{d}", f"{base}/{d}", ignore=shutil.ignore_patterns("*.lock"))
    else:
        base = f"{tmp}/src"
        fake_kiro(base, int(os.environ.get("KIRO_TEST_MB", "1400")) * MB)
    raw = sum(os.path.getsize(p) for _, p in W._kiro_walk(base))
    R = {"raw_mb": raw // MB, "cpus": os.cpu_count()}
    sb = FakeStore()

    def timed(label, fn):
        u0, t0 = sb.up, time.time()
        out = fn()
        secs = time.time() - t0
        R[label] = {"secs": round(secs, 1), "up_mb": round((sb.up - u0) / MB, 1),
                    "est_secs_at_4MBps": round(secs + (sb.up - u0) / UPLINK, 1), **(out if isinstance(out, dict) else {})}

    timed("first_save", lambda: W.kiro_save(sb, base))
    timed("unchanged_save", lambda: W.kiro_save(sb, base))
    big = max((p for _, p in W._kiro_walk(base)), key=os.path.getsize)
    with open(big, "ab") as f:  # one long chat answers a few more turns
        f.write(b'{"role":"assistant","text":"more"}\n' * 30000)
    timed("after_chat_grew", lambda: W.kiro_save(sb, base))
    os.remove(next(p for _, p in W._kiro_walk(base) if p.endswith(".json")))  # kiro-cli deletes a session
    timed("after_delete", lambda: W.kiro_save(sb, base))
    R["store_objects"] = len(sb.d)
    R["store_mb"] = round(sum(map(len, sb.d.values())) / MB, 1)

    dest = f"{tmp}/restored"
    W._kiro["files"] = {}
    t0 = time.time()
    n = W.kiro_restore(sb, dest)
    R["restore"] = {"files": n, "secs": round(time.time() - t0, 1),
                    "est_secs_at_4MBps": round(time.time() - t0 + R["store_mb"] * MB / UPLINK, 1)}
    R["restore_identical"] = tree_sha(base) == tree_sha(dest)
    R["lock_files_skipped"] = not any(a.endswith(".lock") for a, _ in W._kiro_walk(dest))
    timed("save_after_restore", lambda: W.kiro_save(sb, dest))  # mtimes kept: nothing re-uploads

    # Start path: prefetch while Kiro installs, then restore() moves the staged files into ~/.kiro.
    home = f"{tmp}/home"
    os.makedirs(f"{home}/.kiro", exist_ok=True)
    real = (W.HOME, W.WORK, W.blob_load, W.sh)
    W.HOME, W.WORK = home, f"{tmp}/kwork"
    os.makedirs(W.WORK, exist_ok=True)
    W.blob_load = lambda sb_, name, p: False  # no Crew snapshot in this fake store
    W.sh = lambda *a, **k: None
    try:
        t0 = time.time()
        pre = W.prefetch_state(sb)
        W.restore(sb, pre)
        R["start_restore"] = {"secs": round(time.time() - t0, 1),
                              "identical": tree_sha(dest) == tree_sha(f"{home}/.kiro"),
                              "work_left": os.listdir(W.WORK)}
    finally:
        W.HOME, W.WORK, W.blob_load, W.sh = real

    if "--old" in sys.argv:
        t0 = time.time()
        arc = f"{tmp}/old.tar.gz"
        with tarfile.open(arc, "w:gz") as t:
            for d in W.EXTRA_DIRS:
                if os.path.isdir(f"{base}/{d}"):
                    W.tar_add_live(t, f"{base}/{d}", d)
        secs, size = time.time() - t0, os.path.getsize(arc)
        R["old_method"] = {"pack_secs": round(secs, 1), "up_mb": size // MB,
                           "est_secs_at_4MBps": round(secs + size / UPLINK, 1)}
    shutil.rmtree(tmp, ignore_errors=True)
    ok = (R["restore_identical"] and R["unchanged_save"]["up_mb"] == 0 and R["save_after_restore"]["up_mb"] == 0
          and R["start_restore"]["identical"] and not R["start_restore"]["work_left"])
    R["ok"] = ok
    print(json.dumps(R, indent=1))
    if os.path.isdir("/kaggle/working"):
        json.dump(R, open("/kaggle/working/result.json", "w"), indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
