"""Kaggle feasibility test for Kiro Crew on a CPU run.

Times each step and records results in /kaggle/working/crew_result.json.
The Kiro key comes from the attached private dataset and is scrubbed from all output.
"""
import glob
import json
import os
import shutil
import subprocess
import time
import urllib.request

R = {"steps": []}
KEY = open(glob.glob("/kaggle/input/**/kiro_key.txt", recursive=True)[0]).read().strip()
HOME = os.path.expanduser("~")
BIN = f"{HOME}/.local/bin"
env = dict(os.environ, KIRO_API_KEY=KEY, PATH=f"{BIN}:{os.environ['PATH']}")


def clean(s):
    return (s or "").replace(KEY, "***")[-1500:]


def step(name, cmd, timeout=900, background=False):
    t0 = time.time()
    if background:
        log = open(f"/kaggle/working/{name}.log", "w")
        p = subprocess.Popen(cmd, shell=True, env=env, stdout=log, stderr=subprocess.STDOUT)
        R["steps"].append({"step": name, "started_bg": True})
        return p
    try:
        r = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True, timeout=timeout)
        rec = {"step": name, "rc": r.returncode, "secs": round(time.time() - t0, 1),
               "out": clean(r.stdout), "err": clean(r.stderr)}
    except subprocess.TimeoutExpired:
        rec = {"step": name, "rc": "timeout", "secs": timeout}
    R["steps"].append(rec)
    print(name, rec.get("rc"), rec.get("secs"), flush=True)
    return rec


def du(path):
    r = subprocess.run(f"du -sh {path} 2>/dev/null | cut -f1", shell=True, capture_output=True, text=True)
    return r.stdout.strip()


try:
    step("install_kiro_cli", "curl -fsSL https://cli.kiro.dev/install | bash")
    step("install_kirocrew", "curl -fsSL https://download.crew.kiro.dev/cli.sh | sh", timeout=1500)
    step("versions", "kiro-cli --version; kirocrew --version")
    step("setup_agent", "kirocrew setup --agent-only")
    step("doctor", "kirocrew doctor", timeout=300)
    step("chat_one_shot",
         'kirocrew chat -m "In one sentence, what can you do for a marketing researcher?"', timeout=600)

    gw = step("gateway", "kirocrew gateway --no-open --no-tunnel", background=True)
    up = False
    for _ in range(60):
        time.sleep(5)
        try:
            urllib.request.urlopen("http://127.0.0.1:5476/", timeout=3)
            up = True
            break
        except Exception:
            pass
    R["gateway_up"] = up
    step("status", "kirocrew status", timeout=120)
    step("cron_add",
         'kirocrew cron add --help | head -40', timeout=120)
    step("cron_list", "kirocrew cron list", timeout=120)
    step("snapshot", "kirocrew snapshot /kaggle/working/snap --keep 1", timeout=600)
    step("snap_files", "ls -la /kaggle/working/snap")
    step("stop", "kirocrew stop", timeout=120)
    try:
        gw.wait(timeout=60)
    except Exception:
        gw.kill()
    R["sizes"] = {"kiro_cli": du(f"{BIN}/kiro-cli*"), "local": du(f"{HOME}/.local"),
                  "kiro_home": du(f"{HOME}/.kiro"), "snap": du("/kaggle/working/snap")}
    R["disk_free"] = shutil.disk_usage("/kaggle/working").free // 2**30
except Exception as exc:  # record and keep going so we always get a result file
    R["fatal"] = clean(repr(exc))

for f in glob.glob("/kaggle/working/*.log"):
    txt = clean(open(f, errors="replace").read())
    open(f, "w").write(txt)
json.dump(R, open("/kaggle/working/crew_result.json", "w"), indent=2)
print("done")
