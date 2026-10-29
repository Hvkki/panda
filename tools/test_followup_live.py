"""Local test: a follow-up turn (helper/job wake, no Panda stream) shows live text, steps, thinking and
timeline, then ends as one done reply that keeps its steps. Fake Supabase and gateway; no network."""
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
os.chdir(tempfile.mkdtemp(prefix="fu-cwd-"))
import worker as W  # noqa: E402
os.chdir(_cwd)

ROWS = {}


class SB:
    def rest(self, method, table, params=None, body=None, prefer=None):
        if method == "POST":
            rid = len(ROWS) + 1
            ROWS[rid] = dict(body)
            return [{"id": rid}]
        rid = int(str((params or {}).get("id", "eq.0")).split(".")[1])
        if method == "PATCH":
            ROWS[rid].update(body)
            return [ROWS[rid]]
        if method == "DELETE":
            ROWS.pop(rid, None)
            return []
        return [{"cancel": False}]


class GW:
    total, running = 3, False

    def call(self, method, path, timeout=None, **kw):
        if "limit=" in path and int(path.split("limit=")[1]) > 1:
            return {"total": self.total, "running": self.running,
                    "messages": [{"role": "assistant", "content": "Render done, all 3 scenes pass."}]}
        return {"total": self.total, "running": self.running}


W.TL_AFTER, W.TL_EVERY = 0, 0
W.narrate = lambda *a, **k: {"now": "checking the render", "next": "write up", "why": "job ended",
                             "step_left": 5, "total_left": 20}
W.machine_now = lambda: {"cmds": [], "cpus": 4, "load": 1.0}
W.publish_outputs = lambda *a, **k: []
W.push = lambda *a, **k: None
W.scan_outputs = lambda: {}
sb, gw = SB(), GW()
W.FOLLOW["c1"] = {"slot": "s1", "owner": "o", "seen": 3, "row": None, "before": None, "until": time.time() + 99}

gw.running = True
W.followup_step(sb, gw)                       # turn starts: placeholder row + live view
rid = W.FOLLOW["c1"]["row"]
assert rid and "s1" in W.LIVE and W.THINK_REPLY["s1"] == rid, "live view not armed"
with W.THINK_LOCK:                            # what the websocket listener would add
    W.LIVE["s1"]["tools"] += ["Running: pjob log render", "Reading frames"]
    W.LIVE["s1"]["at"] += [0.5, 1.0]
    W.LIVE["s1"]["text"] = "Checking the render."
gw.total = 5                                  # Crew appends rows while running
W.followup_step(sb, gw)
time.sleep(0.3)
W.followup_step(sb, gw)                       # second pass carries the narrator's note
m = ROWS[rid]["meta"]
assert ROWS[rid]["content"] == "Checking the render.", ROWS[rid]
assert m["n_steps"] == 2 and m["tools"][-1] == "Reading frames", m
assert m["tl"] and m["tl"].get("now") == "checking the render" and m["tl"].get("run"), m
gw.total, gw.running = 6, False
W.followup_step(sb, gw)                       # turn over: done row, steps kept, live view gone
assert ROWS[rid]["status"] == "done" and "all 3 scenes" in ROWS[rid]["content"], ROWS[rid]
assert ROWS[rid]["meta"]["n_steps"] == 2 and "s1" not in W.LIVE and "s1" not in W.THINK_REPLY
print("ok: follow-up turn streams text, 2 steps, timeline; ends done with steps kept")
