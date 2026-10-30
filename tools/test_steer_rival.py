"""Tests for two fixes from 30 Oct (fake Supabase and fake Kiro Crew, no network):

1. settle_steers only settles the steers of the turn that just ended, finds a queued steer that Crew gave
   no queue id for (by its sendId, else by its text), and never leaves a steer "queued". Old queued steers
   used to all run again at the chat's next Stop (8 messages from 28-29 Oct re-ran one after another).
2. rival_run / yield_to_rival / boot_beating: never two runs on one account. The older live run keeps the
   account; a hand-off pair and runs asked to stop are not rivals; a starting run heartbeats.

Run: python3 tools/test_steer_rival.py
"""
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
import worker as W  # noqa: E402

W.OWNER = "owner-1"
CHAT = "chat-1"


def iso(secs_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=secs_ago)).isoformat()


class FakeSB:
    def __init__(self, messages=None, sessions=None):
        self.messages = {m["id"]: m for m in (messages or [])}
        self.sessions = sessions or []
        self.posts, self.patches = [], []

    def rest(self, method, table, params=None, body=None, prefer=None):
        p = params or {}
        if table == "messages" and method == "GET":
            rows = [m for m in self.messages.values()
                    if f"eq.{m['chat_id']}" == p.get("chat_id", f"eq.{m['chat_id']}")
                    and f"eq.{m['status']}" == p.get("status", f"eq.{m['status']}")
                    and f"eq.{(m.get('meta') or {}).get('steered')}" == p.get("meta->>steered", "eq.None")
                    and m["id"] > int(p.get("id", "gt.0")[3:])]
            return [dict(m) for m in sorted(rows, key=lambda m: m["id"])]
        if table == "messages" and method == "PATCH":
            mid = int(p["id"][3:])
            m = self.messages[mid]
            if "status" in p and f"eq.{m['status']}" != p["status"]:
                return []
            self.patches.append((mid, body))
            m.update(body)
            return [dict(m)]
        if table == "messages" and method == "POST":
            self.posts.append(body)
            return [{"id": 999}]
        if table == "notifications" and method == "POST":
            self.posts.append(body)
            return []
        if table == "worker_sessions" and method == "GET":
            assert p.get("stop_requested") == "not.is.true", p
            assert p.get("owner") == "eq.owner-1", p
            ok = p["status"][4:-1].split(",")
            return [dict(r) for r in self.sessions if r["status"] in ok and not r.get("stop_requested")]
        if table == "worker_sessions" and method == "PATCH":
            self.patches.append((p["id"], body))
            return []
        return []


class FakeGW:
    def __init__(self, queue=None, delete_status=None):
        self.queue = queue or []           # Crew's queue for the slot
        self.delete_status = delete_status or {}
        self.calls = []

    def call(self, method, path, body=None, timeout=None):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("/api/chat/slots/"):
            return {"total": 10, "running": False, "queue": self.queue}
        if method == "DELETE":
            qid = path.rsplit("/", 1)[-1]
            st = self.delete_status.get(qid, 200)
            if st != 200:
                raise W.GatewayError(method, path, st, {"error": "x"})
            return {"ok": True}
        if method == "POST" and path == "/api/chat":
            return {"ok": True, "queued": True}   # Crew's STEER_REQUEUED answer: no queue_id
        return {}


def msg(mid, steered, **meta):
    return {"id": mid, "chat_id": CHAT, "role": "user", "status": "done", "content": f"steer {mid}",
            "meta": {"steered": steered, **meta}}


armed = []
W.watch_followups = lambda sb, gw, chat, slot: armed.append((chat["id"], slot))
W.FOLLOW.clear()

# ── 1. settle_steers ──────────────────────────────────────────────────────────
sb = FakeSB([
    msg(821, "queued", steer_slot="s1", steer_qid=""),          # old (before this turn): left alone
    msg(1110, "queued", steer_slot="s1", steer_qid=""),         # old
    msg(1215, "queued", steer_slot="s1", steer_qid="q-ok"),     # this turn, queue id, taken back
    msg(1216, "queued", steer_slot="s1", steer_qid="q-gone"),   # this turn, Crew ran it already (404)
    msg(1217, "queued", steer_slot="s1", steer_qid="q-sent"),   # this turn, Crew has it in its chat (409)
    msg(1218, "queued", steer_slot="s1", steer_qid=""),         # no id, found by sendId
    msg(1219, "queued", steer_slot="s1", steer_qid=""),         # no id, found by text
    msg(1220, "queued", steer_slot="s1", steer_qid=""),         # no id, not queued: Crew's own turn
])
gw = FakeGW(queue=[{"id": "q-sid", "content": "redacted", "meta": {"sendId": W.steer_send_id(1218)}},
                   {"id": "q-txt", "content": "steer 1219"}],
            delete_status={"q-gone": 404, "q-sent": 409})
W.settle_steers(sb, gw, CHAT, 1213, "owner-1")
st = {mid: (m["status"], m["meta"].get("steered")) for mid, m in sb.messages.items()}
assert st[821] == ("done", "queued") and st[1110] == ("done", "queued"), f"old steers touched: {st}"
assert st[1215] == ("pending", "requeued"), st[1215]
assert st[1216] == ("done", "crew") and st[1217] == ("done", "crew"), (st[1216], st[1217])
assert st[1218] == ("pending", "requeued") and st[1219] == ("pending", "requeued"), (st[1218], st[1219])
assert st[1220] == ("done", "crew"), st[1220]
deleted = [p for m, p, _ in gw.calls if m == "DELETE"]
assert deleted == ["/api/chat/slots/s1/queue/q-ok", "/api/chat/slots/s1/queue/q-gone",
                   "/api/chat/slots/s1/queue/q-sent", "/api/chat/slots/s1/queue/q-sid",
                   "/api/chat/slots/s1/queue/q-txt"], deleted
for mid in (1215, 1218, 1220):
    assert not {"steer", "steer_slot", "steer_qid"} & set(sb.messages[mid]["meta"]), sb.messages[mid]["meta"]
assert armed == [(CHAT, "s1")], f"follow-ups not armed once for Crew's answer: {armed}"
print("ok settle: only this turn's steers; queue id found by sendId/text; 404/409 -> crew; none left queued")

# A second pass finds nothing left to do for this turn.
gw.calls.clear()
W.settle_steers(sb, gw, CHAT, 1213, "owner-1")
assert not [c for c in gw.calls if c[0] == "DELETE"], gw.calls
print("ok settle: idempotent")

# Already watched for follow-ups: not armed twice.
armed.clear()
W.FOLLOW[CHAT] = {"slot": "s1"}
sb2 = FakeSB([msg(1300, "queued", steer_slot="s1", steer_qid="")])
W.settle_steers(sb2, FakeGW(), CHAT, 1299, "owner-1")
assert sb2.messages[1300]["meta"]["steered"] == "crew" and armed == [], (sb2.messages[1300], armed)
W.FOLLOW.clear()
print("ok settle: an already-watched chat is not re-armed")

# steer_pending tags the steer with its sendId and records the slot.
sb3 = FakeSB([{"id": 1400, "chat_id": CHAT, "role": "user", "status": "pending", "content": "go left",
               "meta": {"steer": True}}])
sb3.rest_orig = sb3.rest


def rest3(method, table, params=None, body=None, prefer=None):
    if method == "GET" and table == "messages" and (params or {}).get("meta->>steer") == "eq.true":
        return [dict(m) for m in sb3.messages.values() if m["status"] == "pending"]
    return sb3.rest_orig(method, table, params, body, prefer)


sb3.rest = rest3
gw3 = FakeGW()
with W.BUSY_LOCK:
    W.BUSY_SLOTS[CHAT] = {"slot": "s9", "since": time.time() - 10}
W.steer_pending(sb3, gw3)
with W.BUSY_LOCK:
    W.BUSY_SLOTS.pop(CHAT, None)
post = [b for m, p, b in gw3.calls if m == "POST"][0]
assert post["steer"] is True and post["meta"] == {"sendId": "panda-steer-1400"}, post
m = sb3.messages[1400]
assert m["status"] == "done" and m["meta"]["steered"] == "queued" and m["meta"]["steer_slot"] == "s9", m
print("ok steer_pending: sendId sent, queued steer remembers its slot")

# ── 2. one run per account ────────────────────────────────────────────────────
ME, PRED, SUCC, OLD, YOUNG = "me", "pred", "succ", "old", "young"
W.SESSION_INFO.clear()
W.SESSION_INFO["handoff_from"] = PRED
my_start = iso(600)


def run(rid, status, started_ago, beat_ago, **info):
    return {"id": rid, "status": status, "started_at": iso(started_ago),
            "heartbeat_at": iso(beat_ago) if beat_ago is not None else None, "info": info}


assert W.rival_run(FakeSB(sessions=[run(ME, "starting", 600, 5)]), ME, my_start) is None
print("ok rival: alone -> none")

older = run(OLD, "running", 3600, 10)
assert W.rival_run(FakeSB(sessions=[run(ME, "starting", 600, 5), older]), ME, my_start)["id"] == OLD
print("ok rival: an older live run wins")

assert W.rival_run(FakeSB(sessions=[run(YOUNG, "running", 60, 5)]), ME, my_start) is None
print("ok rival: a younger run is not ours to yield to")

pair = [run(PRED, "running", 9 * 3600, 5), run(SUCC, "starting", 120, 5, handoff_from=ME)]
assert W.rival_run(FakeSB(sessions=pair), ME, my_start) is None
print("ok rival: hand-off predecessor and successor are not rivals")

assert W.rival_run(FakeSB(sessions=[run(OLD, "running", 3600, 300)]), ME, my_start) is None
print("ok rival: an older run with a stale heartbeat is dead")

restoring = run(OLD, "starting", 23 * 60, 10)        # today's case: old start time, but heartbeating
assert W.rival_run(FakeSB(sessions=[restoring]), ME, my_start)["id"] == OLD
assert W.rival_run(FakeSB(sessions=[run(OLD, "starting", 23 * 60, 400)]), ME, my_start) is None
print("ok rival: a starting run counts while it heartbeats, not after")

asked = dict(older, stop_requested=True)
assert W.rival_run(FakeSB(sessions=[asked]), ME, my_start) is None
print("ok rival: a run asked to stop is not a rival")

# The younger of two runs that both started from the app yields; the older keeps going.
W.SESSION_INFO.pop("handoff_from")
a, b = run("a", "starting", 300, 5), run("b", "starting", 200, 5)
both = FakeSB(sessions=[a, b])
assert W.rival_run(both, "b", b["started_at"])["id"] == "a"
assert W.rival_run(both, "a", a["started_at"]) is None
print("ok rival: of two app starts the older keeps the account")

# yield_to_rival: stopped, reason recorded, owner told; nothing else written.
sb4 = FakeSB()
done = []
W.yield_to_rival(sb4, older, lambda **kw: done.append(kw))
assert done and done[0]["status"] == "stopped" and "duplicate" in done[0]["info"]["stop_reason"], done
assert sb4.posts and sb4.posts[0]["meta"]["kind"] == "duplicate_run", sb4.posts
print("ok yield: marked stopped as a duplicate, owner notified")

# boot_beating heartbeats until told to stop.
W.HEARTBEAT_SECS = 0.05
sb5, stop = FakeSB(), threading.Event()
t = threading.Thread(target=W.boot_beating, args=(sb5, ME, stop), daemon=True)
t.start()
time.sleep(0.4)
stop.set()
t.join(2)
beats = [b for i, b in sb5.patches if b.get("heartbeat_at")]
assert not t.is_alive() and len(beats) >= 3, (t.is_alive(), len(beats))
print(f"ok boot beat: {len(beats)} beats, stops when the run is up")

# main() checks for a rival before anything else and again before its first save.
src = open(W.__file__).read()
main_src = src[src.index("def main():"):]
i_rival = main_src.index("rival_run")
assert i_rival < main_src.index("prefetch_state(") < main_src.index("restore(sb, pre)")
assert main_src.index("rival_run", i_rival + 1) < main_src.index("kiro_save, sb")
assert main_src.index("boot_beating") < main_src.index("install()")
print("ok main: rival checks before the restore and before the first save; boot beat starts before install")
print("all passed")
