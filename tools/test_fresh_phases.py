"""Offline test of "Start each phase fresh" (Settings -> Chat & tools). No network, no Supabase, no agent run:
remote.py is imported against a fake worker module, pjob's settings read and argument parsing run in a temp HOME.
Run: python3 tools/test_fresh_phases.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="fresh-")
FAKE = os.path.join(TMP, "fake_worker.py")
open(FAKE, "w").write(f'''
WORK = {TMP!r}
OWNER = ""
CFG = {{"kiro_api_key": "k"}}
SECRET_NAMES = ["GROQ_API_KEY"]
class Supabase:
    pass
def state_key(name):
    return name
''')
os.environ["PANDA_REMOTE_WORKER_PY"] = FAKE
os.environ["HOME"] = TMP
sys.path.insert(0, os.path.join(HERE, "..", "worker"))
import remote as R  # noqa: E402

R.HOME = TMP
R.SESSIONS = os.path.join(TMP, ".kiro", "sessions", "cli")
R.ensure_kiro = lambda note: "/bin/kiro-cli"
fails = []


def check(ok, what):
    print(("ok: " if ok else "FAIL: ") + what)
    if not ok:
        fails.append(what)


# A finished agent with a saved conversation.
res = "work/remote/demo"
sd = os.path.join(TMP, res, ".session")
os.makedirs(sd)
sid = "399d81de-78e8-4d86-8db4-202f88e74b7e"
open(os.path.join(sd, sid + ".jsonl"), "w").write("{}\n")
json.dump({"session_state": {"conversation_metadata": {"user_turn_metadatas": [
    {"metering_usage": [{"unit": "credit", "value": 89.16}]}]}}}, open(os.path.join(sd, sid + ".json"), "w"))


def run(agent):
    d = tempfile.mkdtemp(dir=TMP)
    notes = []
    cmd, _ = R.agent_cmd({"agent": {"prompt": "Render the final cut.", "model": "claude-sonnet-5.5",
                                    "effort": "medium", "results": res, **agent}}, d, notes.append)
    return cmd, open(os.path.join(d, "prompt.md")).read(), notes


cmd, prompt, notes = run({"resume": True})
check("--resume-id " + sid in cmd and prompt.startswith("The main Panda chat continues"),
      "fresh off: pjob continue resumes the saved conversation")

cmd, prompt, notes = run({"resume": True, "fresh": True})
check("--resume-id" not in cmd, "fresh on: the old conversation is not resumed")
check("REPORT.md first" in prompt and "Render the final cut." in prompt and "API keys" in prompt,
      "fresh on: the new agent gets the full brief, the REPORT.md handoff and the next step")
check(any("fresh agent" in n for n in notes), "fresh on: the job log says it is a fresh agent")

cmd, prompt, notes = run({})
check("--resume-id" not in cmd and "REPORT.md first" not in prompt, "a plain new agent is unchanged")

# pjob reads the setting the worker mirrors.
JOBS = os.path.join(HERE, "..", "worker", "skills", "panda-jobs")
sys.path.insert(0, JOBS)
import jobs as J  # noqa: E402

prefs = os.path.join(TMP, ".panda", "agent_prefs.json")
check(J.fresh_phases() is False, "no settings file: fresh off")
os.makedirs(os.path.dirname(prefs), exist_ok=True)
json.dump({"fresh_phases": True}, open(prefs, "w"))
check(J.fresh_phases() is True, "setting on: pjob sees it")

# --fresh / --keep parse and are exclusive.
out = subprocess.run([sys.executable, os.path.join(JOBS, "jobs.py"), "continue", "x", "--fresh", "--keep"],
                     capture_output=True, text=True, env={**os.environ})
check(out.returncode != 0 and "not allowed" in out.stderr, "--fresh and --keep can't be used together")
out = subprocess.run([sys.executable, os.path.join(JOBS, "jobs.py"), "continue", "nojob", "--keep"],
                     capture_output=True, text=True, env={**os.environ})
check("not a remote agent job" in (out.stderr + out.stdout), "--keep parses (stops at the missing job)")

# The worker mirrors the app setting to the file (real worker.py function, copied out by source).
src = open(os.path.join(HERE, "..", "worker", "worker.py")).read()
start = src.index("AGENT_PREFS = ")
end = src.index("def ensure_slot(")
ns = {"os": os, "json": json, "contextlib": __import__("contextlib")}
exec(src[start:end], ns)
ns["write_agent_prefs"]({"freshPhases": False})
check(json.load(open(prefs)) == {"fresh_phases": False}, "worker writes the setting off")
ns["write_agent_prefs"]({"freshPhases": True})
check(json.load(open(prefs)) == {"fresh_phases": True}, "worker writes the setting on")

print("ALL OK" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
