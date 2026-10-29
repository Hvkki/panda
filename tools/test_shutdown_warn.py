"""Shutdown-warning test (B5): ~1 h before the runtime cap, every BUSY slot gets one steer telling the
agent to checkpoint, and it never fires twice. No network: a fake gateway captures the calls.
Run: python3 tools/test_shutdown_warn.py"""
import json
import os
import sys
import time

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
import worker as W  # noqa: E402

calls = []


class GW:
    def call(self, method, path, body, timeout=30):
        calls.append((method, path, body))
        return {"steered": True}


# 10 minutes before the cap, two agents mid-turn.
W.T0 = time.time() - (W.MAX_RUNTIME - 10 * 60)
W.BUSY_SLOTS = {"chatA": {"slot": "slot-1", "since": 0}, "chatB": {"slot": "slot-2", "since": 0}}
W.SHUTDOWN_WARNED = [False]

W.warn_before_shutdown(None, GW())
assert len(calls) == 2, f"expected 2 steers, got {len(calls)}"
assert all(c[0] == "POST" and c[1] == "/api/chat" for c in calls), calls
assert all(c[2].get("steer") is True for c in calls), "not sent as a steer"
assert {c[2]["slot"] for c in calls} == {"slot-1", "slot-2"}, "wrong slots"
assert "restart" in calls[0][2]["message"].lower() and "~/work" in calls[0][2]["message"], calls[0][2]["message"]
print("ok warn: both busy slots steered once, steer=True, message tells them to checkpoint")

# Fires only once.
W.warn_before_shutdown(None, GW())
assert len(calls) == 2, "warned twice -- SHUTDOWN_WARNED not honoured"
print("ok once: a second call sends nothing")

# No busy slots -> no steer, still marked warned.
W.SHUTDOWN_WARNED = [False]
W.BUSY_SLOTS = {}
calls.clear()
W.warn_before_shutdown(None, GW())
assert calls == [] and W.SHUTDOWN_WARNED[0] is True, "should no-op cleanly with no busy slots"
print("ok idle: no busy slots -> no steer, warned flag set")
print("all passed")
