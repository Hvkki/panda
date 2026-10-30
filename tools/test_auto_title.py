"""Haiku chat titles: only a chat's first turn with the app's placeholder title gets one, and a rename wins.

    python3 tools/test_auto_title.py
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="title-cwd-"))
import worker as W  # noqa: E402

os.chdir(_cwd)
q = "can you make   my agents spin up five remote kaggle notebooks at the same time please"
chat = {"id": "c1", "title": W.first_title(q)}
assert chat["title"] == "can you make my agents spin up five remote kaggl…", chat["title"]
assert W.title_wanted(chat, {"content": q})
assert not W.title_wanted({"id": "c1", "title": "My rename"}, {"content": q})
assert not W.title_wanted(chat, {"content": "a later message"})
assert not W.title_wanted(chat, {"content": q, "meta": {"auto_continue": True}})
assert not W.title_wanted({**chat, "agent": "incognito"}, {"content": q})
assert not W.title_wanted({"id": "d", "title": "Developer"}, {"content": "fix it"})


class FakeSB:
    def __init__(self):
        self.patches = []

    def rest(self, method, table, params=None, body=None):
        self.patches.append((table, params, body))


W.lite_ask = lambda gw, text, model, name, timeout=60: '"Remote Kaggle notebook pool."\nextra line'
sb = FakeSB()
W.auto_title(sb, None, chat, q, "Done: up to 5 notebooks.")
assert sb.patches == [("chats", {"id": "eq.c1", "title": f"eq.{chat['title']}"},
                       {"title": "Remote Kaggle notebook pool"})], sb.patches
W.lite_ask = lambda *a, **k: ""
sb = FakeSB()
W.auto_title(sb, None, chat, q, "x")
assert not sb.patches, "empty answer must not blank the title"
print("ok first turn only, placeholder only, rename-safe, cleaned title")

# Startup backfill: only owner chats still on their placeholder, with an answer; Haiku sees the first few questions.
CHATS = [{"id": "hi1", "title": "hi"}, {"id": "ren", "title": "My name"}, {"id": "inc", "title": "hi", "agent": "incognito"},
         {"id": "noans", "title": "x"}]
MSGS = {"hi1": (["hi", "make me an image of an elephant"], ["Hello!"]), "ren": (["hello"], ["Hi"]),
        "inc": (["hi"], ["Hi"]), "noans": (["x"], [])}


class BackSB(FakeSB):
    def rest(self, method, table, params=None, body=None):
        if method == "PATCH":
            return super().rest(method, table, params, body)
        if table == "chats":
            assert params["owner"] == f"eq.{W.OWNER}"
            return CHATS
        users, answers = MSGS[params["chat_id"][3:]]
        return [{"content": c} for c in (users if params["role"] == "eq.user" else answers)]


seen = []
W.lite_ask = lambda gw, text, *a, **k: seen.append(text) or "Elephant image"
sb = BackSB()
W.backfill_titles(sb, None)
assert [p[1]["id"] for p in sb.patches] == ["eq.hi1"], sb.patches
assert "elephant" in seen[0], "later questions not given to Haiku"
print("ok startup backfill names only placeholder chats with an answer")
print("all passed")
