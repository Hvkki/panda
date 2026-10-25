"""Local test of the worker's Kiro Crew features against a real gateway, with Supabase faked.

Needs a Kiro Crew gateway on 127.0.0.1:5476 and `kirocrew` on PATH (it mints the token).
kiro-cli is optional: without it, model turns end with Crew's "kiro-cli not found" error, which
is still a useful check of the error path. No Supabase, Kaggle or Kiro credits are used.

    HOME=<crew home> PATH=<venv with kirocrew>/bin:$PATH python tools/test_worker_local.py
"""
import json
import os
import sys
import time
import uuid

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
import worker as W  # noqa: E402

RESULTS: dict = {}


def check(name, cond, detail=""):
    RESULTS[name] = "ok" if cond else f"FAIL {detail}"
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))


class FakeSB:
    """Just enough of worker.Supabase for handle(), side_ask() and refresh_usage()."""

    def __init__(self):
        self.rows = {"chats": {}, "messages": {}, "worker_sessions": {}}
        self.calls = []
        self.next_id = 100

    def rest(self, method, path, *, params=None, body=None, prefer=""):
        params = params or {}
        self.calls.append((method, path, params, body))
        table = path.split("?")[0]
        if method == "GET":
            if table == "user_settings":
                return [{"settings": {"name": "Tester", "approvals": "trust"}}]
            if table in ("chats", "messages"):
                want = (params.get("id") or "").removeprefix("eq.")
                return [r for k, r in self.rows[table].items() if not want or str(k) == want]
            if table in ("projects", "files"):
                return []
            return []
        if method == "POST":
            self.next_id += 1
            row = {"id": self.next_id, **(body or {})}
            self.rows.setdefault(table, {})[self.next_id] = row
            return [row]
        if method == "PATCH":
            want = (params.get("id") or "").removeprefix("eq.")
            out = []
            for k, r in self.rows.get(table, {}).items():
                if str(k) == want:
                    r.update(body or {})
                    out.append(r)
            return out
        return []

    def upload(self, *a, **k):
        pass

    def download(self, *a, **k):
        return False


def main():
    gw = W.Gateway()
    print("relay rules")
    allowed = [("GET", "/api/slash-commands", None), ("GET", "/api/sessions/usage", None),
               ("GET", "/api/usage/kiro", None), ("POST", "/api/sessions/usage/refresh", {}),
               ("PATCH", "/api/config/kirocrew", {"path": "session.autocompact_pct", "value": 60}),
               ("PUT", "/api/dashboard/config", {"verbosity": "concise"}), ("POST", "/api/crons/abc/run", None),
               ("GET", "/api/config/schema", None)]
    denied = [("PATCH", "/api/config/kirocrew", {"path": "agent.sandbox", "value": "off"}),
              ("PATCH", "/api/config/kirocrew", {"path": "dashboard.tailscale.enabled", "value": True}),
              ("PUT", "/api/dashboard/config", {"use_builtin_browser": False}),
              ("PUT", "/api/agent/config", {}), ("POST", "/api/agents", {}), ("POST", "/api/system/restart", {}),
              ("GET", "/api/secrets", None), ("GET", "/api/skills/../secrets", None), ("DELETE", "/api/config/kirocrew", None),
              ("POST", "/api/chat/mode", {"mode": "yolo"})]
    for m, p, b in allowed:
        try:
            W.relay_check(m, p, b)
            check(f"allow {m} {p}", True)
        except Exception as exc:
            check(f"allow {m} {p}", False, str(exc))
    for m, p, b in denied:
        try:
            W.relay_check(m, p, b)
            check(f"deny {m} {p} {b or ''}", False, "was allowed")
        except ValueError:
            check(f"deny {m} {p} {b or ''}", True)

    print("slash commands")
    cmds = W.crew_commands(gw)
    names = {c["name"] for c in cmds["commands"]}
    check("crew.commands lists /compact and /usage", {"/compact", "/usage", "/goal"} <= names, str(names))
    check("crew.commands reports the backend", bool(cmds["backend"]), str(cmds))
    check("command_word", W.command_word("/compact now") == "/compact" and W.command_word("/etc/hosts x") == ""
          and W.command_word("hello") == "" and W.command_word("  /Usage") == "/usage")

    print("settings")
    t0 = time.time()
    st = W.crew_settings(gw)
    items = st["items"]
    size = len(json.dumps(st))
    writable = [i for i in items if i.get("w")]
    locked = [i for i in items if i.get("k")]
    print(f"  {len(items)} settings, {len(writable)} editable, {len(locked)} locked, {size // 1024} KB, "
          f"spec={st['spec']}, {time.time() - t0:.1f}s")
    check("settings: every leaf", len(items) > 400, str(len(items)))
    check("settings: live editable spec from the installed Crew", st["spec"] == "live")
    check("settings: payload under 400 KB", size < 400_000, str(size))
    byp = {i["p"]: i for i in items}
    check("settings: autocompact editable", byp.get("session.autocompact_pct", {}).get("w") == "patch")
    check("settings: verbosity via dashboard config", byp.get("dashboard.verbosity", {}).get("w") == "dash")
    check("settings: sandbox locked", bool(byp.get("agent.sandbox", {}).get("k")))
    check("settings: secrets never sent", all("v" not in i or i["v"] in (None, "", "••••••••") for i in items if i.get("s")))

    old = byp["session.autocompact_pct"].get("v")
    r = W.crew_set(gw, "session.autocompact_pct", 61)
    check("crew.set autocompact", r.get("value") in (61, 61.0), str(r))
    r = W.crew_set(gw, "dashboard.verbosity", "concise")
    check("crew.set verbosity", r.get("ok"), str(r))
    try:
        W.crew_set(gw, "session.autocompact_pct", 500)
        check("crew.set rejects out of range", False, "accepted 500")
    except W.GatewayError as exc:
        check("crew.set rejects out of range", "between" in str(exc), str(exc))
    try:
        W.crew_set(gw, "agent.sandbox", "off")
        check("crew.set refuses a locked key", False)
    except ValueError as exc:
        check("crew.set refuses a locked key", "can't be changed" in str(exc), str(exc))
    if old is not None:
        W.crew_set(gw, "session.autocompact_pct", old)

    print("usage")
    sb = FakeSB()
    W.USAGE_STATE.update(sid="sess-1", at=0)
    sb.rows["worker_sessions"]["sess-1"] = {"id": "sess-1"}
    u = W.usage_now(sb, gw, refresh=False)
    check("usage has account + daily + at", set(u) >= {"account", "daily", "at"}, str(u)[:200])
    check("usage saved to worker_sessions.info", "usage" in sb.rows["worker_sessions"]["sess-1"].get("info", {}))

    print("chat turns (commands, /kb, a model turn)")
    chat_id = str(uuid.uuid4())
    sb.rows["chats"][chat_id] = {"id": chat_id, "owner": "owner-1", "title": "local test", "agent": "", "slot_id": None,
                                 "model": "claude-sonnet-5.5", "effort": "medium", "project_id": None}

    def send(text):
        sb.next_id += 1
        mid = sb.next_id
        sb.rows["messages"][mid] = {"id": mid, "chat_id": chat_id, "owner": "owner-1", "role": "user",
                                    "content": text, "status": "processing", "meta": {}}
        W.handle(sb, gw, sb.rows["messages"][mid])
        return [r for r in sb.rows["messages"].values() if r.get("reply_to") == mid]

    rep = send("/goal status")
    check("command reply text", rep and "goal" in (rep[-1].get("content") or "").lower(), str(rep)[:200])
    check("command reply marked", rep and rep[-1].get("meta", {}).get("command") == "/goal", str(rep)[:200])
    check("command reply done", rep and rep[-1].get("status") == "done")
    slot = sb.rows["chats"][chat_id]["slot_id"]
    check("app context waits for the next normal message", slot in W.PENDING_HINTS)
    rep = send("/kb llamas in the andes")
    check("/kb with an empty library answers without a model turn",
          rep and "knowledge library" in rep[-1].get("content", ""), str(rep)[:200])
    rep = send("/workflow")
    check("second command, same slot", rep and "workflow" in rep[-1].get("content", "").lower())
    rep = send("hello")
    last = rep[-1] if rep else {}
    if last.get("status") == "error":
        check("model turn without kiro-cli ends as an error, not stale text",
              "kiro" in (last.get("meta", {}).get("error") or "").lower() and "workflow" not in (last.get("content") or ""),
              str(last)[:300])
    else:
        check("model turn answered", bool(last.get("content")), str(last)[:300])
        check("model turn has credits", isinstance(last.get("meta", {}).get("credits"), float), str(last.get("meta"))[:300])
    check("app context sent with the first normal message", slot not in W.PENDING_HINTS)

    print("side chat")
    t0 = time.time()
    s = W.side_ask(sb, gw, chat_id, "what is 2+2?", timeout=40)
    check("side chat returns an answer or a clear note", bool(s.get("answer") or s.get("note")), str(s))
    print(f"  side: {json.dumps(s)[:160]} ({time.time() - t0:.0f}s)")

    bad = [k for k, v in RESULTS.items() if v != "ok"]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed" + (f"; failed: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
