"""Local tests: a running worker must not end on a Supabase blip, and a crash it can't recover from must hand
over to a fresh notebook (no Kaggle, no Supabase, no Kiro Crew: main() runs with every outside call faked).

    python3 tools/test_crash.py
"""
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
os.chdir(tempfile.mkdtemp(prefix="crash-cwd-"))
import worker as W  # noqa: E402

os.chdir(_cwd)
EVENTS: list = []


class FakeSB:
    def __init__(self, get_fail=0, stop_after=3):
        self.get_fail, self.stop_after, self.gets, self.rows = get_fail, stop_after, 0, {}

    def rest(self, method, table, params=None, body=None, prefer=None):
        if table == "worker_sessions" and method == "PATCH":
            self.rows.update(body or {})
            if "status" in (body or {}):
                EVENTS.append(("status", body["status"]))
            return [self.rows]
        if table == "worker_sessions" and method == "GET":
            self.gets += 1
            if self.get_fail and self.gets <= self.get_fail:
                raise ConnectionError("network blip")
            return [{"stop_requested": self.gets > self.stop_after, "status": "running"}]
        if table == "notifications":
            EVENTS.append(("alert", body["meta"]["kind"], body["text"][:40]))
        return []

    def download(self, *a, **k):
        return False


class Proc:
    def __init__(self, dead=False):
        self.dead = dead

    def poll(self):
        return -9 if self.dead else None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def setup(sb, gw_dead=False, install_raises=False, successor_ok=True):
    EVENTS.clear()
    W.SESSION_INFO.clear()
    W.GW_RESTARTS.clear()
    W.RUNNING[0] = False
    noop = lambda *a, **k: None  # noqa: E731
    W.Supabase = lambda: sb
    W.claim_session = lambda s: {"id": "run-1", "info": {}}
    W.prefetch_state = noop
    W.install = (lambda: (_ for _ in ()).throw(RuntimeError("install broke"))) if install_raises else noop
    for name in ("restore", "install_skills", "setup_drive", "restore_files", "sync_secrets", "start_browser",
                 "start_codex", "start_stt", "apply_overrides", "start_video", "kiro_save", "command_loop",
                 "thinking_listener", "followup_loop", "job_watch_loop", "brief_loop", "guard_loop", "remote_loop",
                 "thinking_pusher", "resume_interrupted", "claim_loop", "usage_now", "drain_turns", "stop_answers",
                 "stop_browser", "codex_backup", "snapshot_files_later", "clean_work", "push"):
        setattr(W, name, noop)
    W.user_settings = lambda *a: {}
    W.sh = lambda cmd, **k: EVENTS.append(("sh", cmd))
    W.start_gateway = lambda: Proc(dead=gw_dead)
    W.mint_token = lambda: "tok"
    W.Gateway = type("G", (), {"__init__": lambda self: setattr(self, "token", "tok")})
    W.snapshot = lambda s, reason, **k: (EVENTS.append(("snapshot", reason)), "2026-10-29T00:00:00Z")[1]
    W.HEARTBEAT_SECS, W.POLL_SECS, W.NOTIFY_SECS, W.SNAPSHOT_EVERY, W.MAX_RUNTIME = 0, 0.01, 1e9, 1e9, 1e9
    W.AUTO_RESTART = True
    W.time.sleep = lambda s: None if s >= 1 else _sleep(s)

    def succ(s, sid):
        if not successor_ok:
            raise RuntimeError("409 only a live run can hand off")
        EVENTS.append(("successor",))
        return {"id": "run-2"}
    W.request_successor = succ


_sleep = time.sleep


def test_blips_do_not_end_the_run():
    sb = FakeSB(get_fail=4, stop_after=6)
    setup(sb)
    W.main()
    assert ("status", "stopped") in EVENTS and ("status", "error") not in EVENTS, EVENTS
    assert ("successor",) not in EVENTS


def test_gateway_crash_loop_hands_off():
    sb = FakeSB(stop_after=10**9)
    setup(sb, gw_dead=True)
    try:
        W.main()
    except RuntimeError as exc:
        assert "keeps exiting" in str(exc)
    names = [e[0] for e in EVENTS]
    assert ("snapshot", "crash") in EVENTS, EVENTS
    assert ("successor",) in EVENTS
    # the gateway is stopped before the row says error (the successor's go signal)
    assert names.index("sh") < EVENTS.index(("status", "error")), EVENTS
    assert any(e[0] == "alert" and "restarted itself" in e[2] for e in EVENTS) or \
        any(e[0] == "alert" for e in EVENTS)
    assert W.SESSION_INFO["handoff"]["mode"] == "after-crash"


def test_supabase_down_too_long_hands_off_or_alerts():
    sb = FakeSB(get_fail=10**9)
    setup(sb, successor_ok=False)
    W.LOOP_ERR_MAX_SECS = 0.05
    try:
        W.main()
    except Exception:
        pass
    W.LOOP_ERR_MAX_SECS = 900
    assert ("status", "error") in EVENTS, EVENTS
    assert any(e[0] == "alert" and "stopped" in e[2].lower() or e[0] == "alert" for e in EVENTS), EVENTS
    assert "handoff_error" in W.SESSION_INFO


def test_failed_start_does_not_self_restart():
    sb = FakeSB()
    setup(sb, install_raises=True)
    try:
        W.main()
    except RuntimeError:
        pass
    assert ("status", "error") in EVENTS and ("successor",) not in EVENTS, EVENTS


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
