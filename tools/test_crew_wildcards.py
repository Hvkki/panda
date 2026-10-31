"""Map settings (agent.role_models.*) show up as one editable row per key Crew allows."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))
import worker as W  # noqa: E402


class FakeGw:
    def call(self, method, path, body=None, timeout=None):
        if path == "/api/config/schema":
            return {"entries": [
                {"path": "agent.role_models.*", "type": "string", "label": "*"},
                {"path": "agent.role_efforts.*", "type": "string", "label": "*"},
                {"path": "agent.streaming", "type": "boolean", "label": "Streaming"},
                {"path": "agent.sandbox", "type": "string", "label": "Sandbox"},
            ]}
        return {"agent": {"role_models": {"background": "claude-haiku-5.5"}, "streaming": True}}


st = W.crew_settings(FakeGw())
by = {i["p"]: i for i in st["items"]}
fails = 0
for path in ("agent.role_models.background", "agent.role_models.subagent",
             "agent.role_efforts.background", "agent.role_efforts.subagent"):
    ok = by.get(path, {}).get("w") == "patch"
    fails += not ok
    print(("ok  " if ok else "FAIL"), path, by.get(path, {}).get("l"))
checks = [("no '*' rows left", not any(p.endswith(".*") for p in by)),
          ("current value read", by["agent.role_models.background"].get("v") == "claude-haiku-5.5"),
          ("effort row has its choices", "max" in by["agent.role_efforts.subagent"]["x"]["values"]),
          ("sandbox still locked", bool(by["agent.sandbox"].get("k")))]
for name, ok in checks:
    fails += not ok
    print(("ok  " if ok else "FAIL"), name)
sys.exit(1 if fails else 0)
