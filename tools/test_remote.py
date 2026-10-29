"""Remote Panda end-to-end test against the real Supabase Storage (worker account), with remote.py running
either locally (its own HOME, default) or on a real Kaggle notebook (--kaggle, needs KAGGLE_API_TOKEN).

Goes through the real chain: pjob start --remote -> premote proxy -> worker remote_loop -> Storage ->
remote.py -> back. Cases: files there and back + exit 0, an exit code, pjob stop cancels the remote command.
Run: python3 tools/test_remote.py [--kaggle]
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
# Its own request box: the live worker on this machine watches ~/.panda/remote and must not take these.
os.environ["PANDA_REMOTE_BOX"] = os.path.expanduser("~/work/rtest-box")
sys.path.insert(0, os.path.join(HERE, "..", "worker"))
import worker as W  # noqa: E402

KAGGLE = "--kaggle" in sys.argv
RID = "r4" if KAGGLE else "rtest"
W.REMOTE_ID = RID
HOME = os.path.expanduser("~")
SRC = f"{HOME}/work/rtest-src"
JOBS = os.path.join(HERE, "..", "worker", "skills", "panda-jobs", "jobs.py")
TEST_KERNEL = os.environ.get("PANDA_REMOTE_TEST_KERNEL", "vladraf/panda-remote-test")
sb = W.Supabase()
procs = []


def start_local(_sb) -> str:
    rhome = f"{HOME}/work/rtest-remote-home"
    shutil.rmtree(rhome, ignore_errors=True)
    os.makedirs(rhome)
    env = {**os.environ, "HOME": rhome, "PANDA_REMOTE_ID": RID, "PANDA_REMOTE_OWNER": W.OWNER,
           "PANDA_REMOTE_WORKER_PY": os.path.join(HERE, "..", "worker", "worker.py"), "PANDA_REMOTE_IDLE": "120"}
    procs.append(subprocess.Popen([sys.executable, os.path.join(HERE, "..", "worker", "remote.py")], env=env,
                                  stdout=open(f"{HOME}/work/rtest-remote.log", "w"), stderr=subprocess.STDOUT))
    return "local"


def start_kaggle(_sb) -> str:
    """What the session function's remoteStart does, with the dev token."""
    text = open(os.path.join(HERE, "..", "worker", "remote.py")).read()
    text = (text.replace('INJECTED_OWNER = ""', f'INJECTED_OWNER = "{W.OWNER}"', 1)
                .replace('INJECTED_REMOTE_ID = ""', f'INJECTED_REMOTE_ID = "{RID}"', 1)
                .replace('INJECTED_MAIN_SESSION = ""', 'INJECTED_MAIN_SESSION = "test"', 1))
    # remote.py imports the LIVE code/worker.py from Storage; this test needs the new one there (deploy first).
    r = requests.post("https://api.kaggle.com/v1/kernels.KernelsApiService/SaveKernel",
                      headers={"Authorization": f"Bearer {os.environ['KAGGLE_API_TOKEN']}"},
                      json={"slug": TEST_KERNEL, "newTitle": TEST_KERNEL.split("/")[1].replace("-", " "), "text": text,
                            "language": "python", "kernelType": "script", "isPrivate": True, "enableGpu": False,
                            "enableTpu": False, "enableInternet": True,
                            "datasetDataSources": [os.environ["KAGGLE_DATASET"]], "competitionDataSources": [],
                            "kernelDataSources": [], "modelDataSources": [], "categoryIds": [],
                            "sessionTimeoutSeconds": 3600}, timeout=120)
    out = r.json()
    assert r.ok and not out.get("error"), (r.status_code, str(out)[:300])
    return f"{TEST_KERNEL} v{out.get('versionNumber')}"


W.REMOTE_STARTER = start_kaggle if KAGGLE else start_local


def pjob(*args) -> str:
    return subprocess.run([sys.executable, JOBS, *args], capture_output=True, text=True, cwd=SRC).stdout


def wait_end(name, mx):
    t0 = time.time()
    while time.time() - t0 < mx:
        st = json.loads(pjob("status", name, "--json"))
        if st["state"] != "running":
            return st
        time.sleep(2)
    raise AssertionError(f"{name} still running after {mx}s")


shutil.rmtree(SRC, ignore_errors=True)
os.makedirs(SRC)
open(f"{SRC}/input.txt", "w").write("seven\n")
stop = threading.Event()
threading.Thread(target=W.remote_loop, args=(sb, stop), daemon=True).start()
try:
    t0 = time.time()
    print(pjob("start", "rt-ok", "--remote", "--files", SRC, "--sh",
               'cat input.txt; python3 -c "print(sum(range(10**7)))"; echo "made on $PANDA_REMOTE" > out.txt; '
               'nproc; ffmpeg -version 2>/dev/null | head -1; node --version 2>/dev/null; free -g | sed -n 2p').strip())
    st = wait_end("rt-ok", 1200)
    logtxt = open(st["log"]).read()
    print(f"--- rt-ok after {time.time() - t0:.0f}s ---\n" + "\n".join(logtxt.splitlines()[-12:]))
    assert st["state"] == "done" and st["exit"] == 0, st
    assert "seven" in logtxt and "49999995000000" in logtxt, "input file or output missing in the log"
    assert "sending back 1 changed files" in logtxt and "brought back 1 file" in logtxt, "out.txt not sent back"
    assert open(f"{SRC}/out.txt").read().strip() == f"made on {RID}", "out.txt not brought back"
    print("ok: files there and back, exit 0")

    print(pjob("start", "rt-fail", "--remote", "--sh", "echo about to fail; exit 7").strip())
    st = wait_end("rt-fail", 300)
    assert st["exit"] == 7, st
    print("ok: exit code 7 passed through")

    print(pjob("start", "rt-stop", "--remote", "--sh", "echo sleeping; sleep 600").strip())
    for _ in range(60):
        if "sleeping" in open(json.loads(pjob("status", "rt-stop", "--json"))["log"]).read():
            break
        time.sleep(2)
    print(pjob("stop", "rt-stop").strip())
    t1 = time.time()
    while time.time() - t1 < 90:
        b = W.remote_get_json(sb, "beat.json") or {}
        if not b.get("running") and time.time() - b.get("at", 0) < 30 and b.get("at", 0) > t1:
            break
        time.sleep(3)
    b = W.remote_get_json(sb, "beat.json") or {}
    assert not b.get("running"), f"remote still runs {b.get('running')}"
    print(f"ok: pjob stop ended the remote command in {time.time() - t1:.0f}s")
    print("remote tools:", b.get("tools"), "cpus", b.get("cpus"), "free MB", b.get("free_mb"))
    if "--big" in sys.argv:
        big = f"{HOME}/work/rtest-big"
        shutil.rmtree(big, ignore_errors=True)
        os.makedirs(big)
        with open(f"{big}/in.bin", "wb") as f:
            f.write(os.urandom(120 << 20))
        t3 = time.time()
        pjob("start", "rt-big", "--remote", "--files", big, "--sh",
             f"cd {big} && sha256sum in.bin | cut -c1-16 && head -c 60M /dev/urandom > result.bin && echo made")
        st = wait_end("rt-big", 900)
        tail = "\n".join(open(st["log"]).read().splitlines()[-6:])
        assert st["exit"] == 0 and os.path.getsize(f"{big}/result.bin") == 60 << 20, (st, tail)
        print(f"ok: 120 MB there, 60 MB back in {time.time() - t3:.0f}s\n{tail}")
        shutil.rmtree(big, ignore_errors=True)
    if "--agent" in sys.argv:
        res = f"{HOME}/work/remote/rt-agent"
        shutil.rmtree(res, ignore_errors=True)
        t4 = time.time()
        print(pjob("start", "rt-agent", "--remote", "--agent", "--model", "claude-haiku-5.5", "--effort", "low",
                   "--files", SRC, "--sh",
                   f"Read {SRC}/input.txt. Write the word it contains in UPPERCASE, alone, to {res}/upper.txt. "
                   "Then write REPORT.md as told.").strip())
        st = wait_end("rt-agent", 1500)
        tail = "\n".join(open(st["log"]).read().splitlines()[-8:])
        print(f"--- rt-agent after {time.time() - t4:.0f}s ---\n{tail}")
        assert st["exit"] == 0, st
        assert open(f"{res}/upper.txt").read().strip() == "SEVEN", "agent result not brought back"
        assert os.path.exists(f"{res}/REPORT.md"), "no REPORT.md"
        print("ok: remote agent did the task, result and REPORT.md came back")
    leftover = [n for n in os.listdir(f"{W.REMOTE_BOX}/req")] if os.path.isdir(f"{W.REMOTE_BOX}/req") else []
    assert not leftover, leftover
finally:
    W.remote_queue_edit(sb, lambda q: q.__setitem__("stop", True))
    t2 = time.time()
    while time.time() - t2 < 60:
        b = W.remote_get_json(sb, "beat.json") or {}
        if b.get("state") == "stopped":
            print("ok: remote stopped itself when asked")
            break
        time.sleep(3)
    stop.set()
    for p in procs:
        try:
            p.wait(30)
        except subprocess.TimeoutExpired:
            p.kill()
    for n in ("queue.json", "beat.json"):
        sb.delete_object(W.state_key(W.rkey(n)))
