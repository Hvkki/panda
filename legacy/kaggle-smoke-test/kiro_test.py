"""Kaggle smoke test: run kiro-cli headless on a Kaggle CPU run.

Reads KIRO_API_KEY from the attached private dataset, never prints it,
and writes only the agent's reply to /kaggle/working/result.json.
"""
import glob
import json
import os
import subprocess
import time

out = {"started": time.strftime("%Y-%m-%d %H:%M:%S")}

key_files = glob.glob("/kaggle/input/**/kiro_key.txt", recursive=True)
if not key_files:
    out["error"] = "kiro_key.txt not found in /kaggle/input"
else:
    env = dict(os.environ, KIRO_API_KEY=open(key_files[0]).read().strip())
    subprocess.run("curl -fsSL https://cli.kiro.dev/install | bash", shell=True, check=True,
                   stdout=subprocess.DEVNULL)
    kiro = os.path.expanduser("~/.local/bin/kiro-cli")
    out["version"] = subprocess.run([kiro, "--version"], capture_output=True, text=True).stdout.strip()
    t0 = time.time()
    r = subprocess.run(
        [kiro, "chat", "--no-interactive",
         "Write one catchy LinkedIn hook (max 20 words) for a research tool that summarizes papers."],
        env=env, capture_output=True, text=True, timeout=300,
    )
    out["seconds"] = round(time.time() - t0, 1)
    out["returncode"] = r.returncode
    # strip any accidental key echo before saving
    out["reply"] = r.stdout.replace(env["KIRO_API_KEY"], "***")[-2000:]
    out["stderr_tail"] = r.stderr.replace(env["KIRO_API_KEY"], "***")[-500:]

json.dump(out, open("/kaggle/working/result.json", "w"), indent=2)
print(json.dumps({k: v for k, v in out.items() if k != "stderr_tail"}, indent=2))
