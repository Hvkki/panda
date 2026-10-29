"""Local test: streaming writes stay small and paced (the 29 Oct database overload).

While a reply streams, each write carries only the newest THINK_LIVE_MAX characters of thinking, asks
back only the cancel flag, and a big row is written at most every STREAM_GAP_MAX s; the final write
carries the full thinking. thinking_pusher leaves chat turns to their flusher, writes a follow-up's newest
thinking at most every THINK_PUSH_EVERY s, and never writes after the reply ended. A follow-up's final
write carries its full thinking. Fake Supabase and gateway; no network. Takes about 15 s.
"""
import json
import os
import sys
import tempfile
import threading
import time
os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="sw-cwd-"))
import worker as W  # noqa: E402
os.chdir(_cwd)

FULL_THINK = "".join(f"step {i}: thinking about the scene layout and the bench height\n" for i in range(500))
assert len(FULL_THINK) > 25_000

# ── 1. helpers ─────────────────────────────────────────────────────────────
assert W.live_thinking("short") == "short"
lt = W.live_thinking(FULL_THINK)
assert lt.startswith("…\n\n") and len(lt) <= W.THINK_LIVE_MAX + 3 and FULL_THINK.endswith(lt[3:]), len(lt)
assert lt[3:].startswith("step "), lt[:40]                      # starts at a line
assert W.stream_gap(1000) == W.STREAM_GAP_MIN and W.stream_gap(10 ** 6) == W.STREAM_GAP_MAX
assert abs(W.stream_gap(9000) - 0.75) < 1e-9
print("ok: live_thinking keeps the newest 4000 chars from a line start; stream_gap is 0.4-2 s by size")

# ── fakes ──────────────────────────────────────────────────────────────────
CALLS: list = []   # (time, method, table, params, body)
ROWS: dict = {}
LOCK = threading.Lock()


class SB:
    def rest(self, method, table, params=None, body=None, prefer=""):
        params = dict(params or {})
        with LOCK:
            CALLS.append((time.time(), method, table, params, json.loads(json.dumps(body)) if body else body))
            if table == "chats":
                return [{"id": "c1", "owner": "o", "slot_id": "slot-1", "title": "t", "model": None, "effort": None}]
            if table == "messages" and method == "POST":
                rid = 100 + len(ROWS)
                ROWS[rid] = dict(body)
                return [{"id": rid, **body}]
            if table == "messages" and method == "PATCH":
                ROWS.setdefault(int(params["id"].split(".")[1]), {}).update(body)
                return [{"cancel": False}] if params.get("select") == "cancel" else []
            if table == "messages" and method == "DELETE":
                ROWS.pop(int(params["id"].split(".")[1]), None)
                return []
            if table == "messages":
                return [{"cancel": False}]
            return []

    def put(self, *a, **k):
        pass


def writes(rid):
    with LOCK:
        return [c for c in CALLS if c[1] == "PATCH" and c[2] == "messages" and c[3].get("id") == f"eq.{rid}"]


class GW:
    """A turn: 1.5 s of thinking (30 chunks), then ~7 s of answer text in 2 KB chunks."""
    def stream_chat(self, slot, prompt):
        for i in range(0, len(FULL_THINK), 1000):
            yield {"type": "thinking", "content": FULL_THINK[i:i + 1000]}
            time.sleep(0.05)
        t_end = time.time() + 7
        while time.time() < t_end:
            yield {"type": "chunk", "content": "word " * 400}
            time.sleep(0.05)

    def call(self, method, path, timeout=None, **kw):
        return {}


W.user_settings = lambda sb, owner: {"liveTimeline": False, "notifyReplies": False}
W.ensure_slot = lambda sb, gw, chat, settings: ("slot-1", False)
W.fetch_attachments = lambda *a, **k: []
W.context_lost = lambda *a, **k: ""
W.project_preamble = lambda *a, **k: ""
W.app_hints = lambda *a, **k: ""
W.scan_outputs = lambda: {}
W.turn_result = lambda gw, slot, want_stats=True: {"text": "", "errors": [], "stats": {}}
W.context_usage = lambda gw, slot, t0, usage: usage
W.publish_outputs = lambda *a, **k: []
W.push = lambda *a, **k: None
sb = SB()

# ── 2. a chat turn ────────────────────────────────────────────────────────
stop = threading.Event()
threading.Thread(target=W.thinking_pusher, args=(sb, stop), daemon=True).start()
W.handle(sb, GW(), {"id": 1, "chat_id": "c1", "content": "hello", "meta": {}})
stop.set()
rid = min(k for k in ROWS if k >= 100)        # the reply (ids < 100 are the user's message)
ws = writes(rid)
stream = [c for c in ws if "thinking" in (c[4] or {}) and "status" not in c[4]]
final = [c for c in ws if (c[4] or {}).get("status") == "done"]
assert stream, "no streaming writes"
assert all(c[3].get("select") == "cancel" for c in stream), "a streaming write asked for the whole row back"
assert max(len(c[4]["thinking"]) for c in stream) <= W.THINK_LIVE_MAX + 3, "streamed the full thinking"
assert all("content" in c[4] for c in stream), "thinking_pusher wrote a chat turn's row"
assert final and final[-1][4]["thinking"] == W.scrub(FULL_THINK), "the final write lost the full thinking"
big = [c for c in stream if len(c[4]["content"]) > 30_000]
gaps = [b[0] - a[0] for a, b in zip(big, big[1:])]
assert len(gaps) >= 2 and min(gaps) >= W.STREAM_GAP_MAX - 0.15, gaps
assert rid not in W.STREAM_ROWS, "reply left in STREAM_ROWS"
print(f"ok: chat turn: {len(stream)} streaming writes over ~8.5 s, thinking <= {W.THINK_LIVE_MAX} chars, "
      f"cancel-only return, big rows every >= {min(gaps):.1f} s, final write has all {len(FULL_THINK)} chars")

# ── 3. thinking_pusher on a follow-up row ─────────────────────────────────
with W.THINK_LOCK:
    W.THINK_REPLY["s9"], W.THINK["s9"], W.THINK_SENT["s9"] = 900, FULL_THINK, 0
stop = threading.Event()
threading.Thread(target=W.thinking_pusher, args=(sb, stop), daemon=True).start()
time.sleep(1.0)
w1 = writes(900)
assert len(w1) == 1 and len(w1[0][4]["thinking"]) <= W.THINK_LIVE_MAX + 3, [len(c[4]["thinking"]) for c in w1]
with W.THINK_LOCK:
    W.THINK["s9"] += "more thinking\n" * 10
time.sleep(0.3)
assert len(writes(900)) == 1, "wrote again before THINK_PUSH_EVERY"
time.sleep(1.6)
assert len(writes(900)) == 2, "did not write the grown thinking"
W.THINK_PUSH_LOCK.acquire()                   # a push that is already decided waits here...
with W.THINK_LOCK:
    W.THINK["s9"] += "last words\n"
time.sleep(2.2)
with W.THINK_LOCK:                            # ...while the follow-up ends
    W.THINK_REPLY.pop("s9", None)
W.THINK_PUSH_LOCK.release()
time.sleep(0.6)
stop.set()
assert len(writes(900)) == 2, "a thinking push landed after the reply ended"
print("ok: thinking_pusher: follow-ups only, newest part, <= 1 write per 1.5 s, never after the end")

# ── 4. a follow-up's final write carries all its thinking ─────────────────
class GW2:
    total, running = 3, False

    def call(self, method, path, timeout=None, **kw):
        if "limit=" in path and int(path.split("limit=")[1]) > 1:
            return {"total": self.total, "running": self.running,
                    "messages": [{"role": "assistant", "content": "All done."}]}
        return {"total": self.total, "running": self.running}


W.narrate = lambda *a, **k: {"now": "", "next": "", "why": "", "step_left": None, "total_left": None}
W.machine_now = lambda: {"cmds": [], "cpus": 4, "load": 1.0}
gw2 = GW2()
W.FOLLOW["c2"] = {"slot": "s2", "owner": "o", "seen": 3, "row": None, "before": None, "until": time.time() + 99}
gw2.running = True
W.followup_step(sb, gw2)
rid2 = W.FOLLOW["c2"]["row"]
assert rid2 and W.THINK_REPLY.get("s2") == rid2, "follow-up not armed"
with W.THINK_LOCK:
    W.THINK["s2"] = FULL_THINK
gw2.total, gw2.running = 6, False
W.followup_step(sb, gw2)
assert ROWS[rid2]["status"] == "done" and ROWS[rid2].get("thinking") == W.scrub(FULL_THINK), \
    (ROWS[rid2].get("status"), len(ROWS[rid2].get("thinking") or ""))
print("ok: follow-up final write carries the full thinking")
print("all passed")
