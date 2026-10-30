"""Snapshot-part GC: only generations no manifest names, after a grace time; unreadable manifests leave
their blob alone; the pre-chunk kiro copy goes once the chunk index superseded it for a day. Hermetic.

    python3 tools/test_parts_gc.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("KIROCREW_WORKER_CFG", json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"}))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))
import worker as W  # noqa: E402

NOW = datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc)
W.OWNER = W.PRIMARY_OWNER = "owner-1"


def ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat()


class SB:
    def __init__(self, objs, manifests, broken=()):
        self.objs = {o["name"]: o for o in objs}
        self.manifests, self.broken, self.deleted = manifests, set(broken), []

    def list(self, prefix):
        assert prefix == "", prefix
        return list(self.objs.values())

    def get(self, name):
        if name in self.broken:
            raise RuntimeError("storage kept refusing it")
        m = self.manifests.get(name)
        return json.dumps(m).encode() if m is not None else None

    def delete_object(self, name):
        self.deleted.append(name)
        self.objs.pop(name, None)


W._safe_delete = lambda sb, name: sb.delete_object(name)


def o(name, hours, mb=40):
    return {"name": name, "size": mb << 20, "updated_at": ago(hours)}


G_LIVE, G_OLD, G_NEW = "1793300000aaaa", "1793200000bbbb", "1793390000cccc"
objs = [
    o(f"latest-crew.tar.gz.{G_LIVE}.p000", 1), o(f"latest-crew.tar.gz.{G_LIVE}.p001", 1),
    o(f"latest-crew.tar.gz.{G_OLD}.p000", 50),                  # an older generation nobody names
    o(f"latest-crew.tar.gz.{G_NEW}.p000", 2),                   # being uploaded right now (young)
    o(f"latest-crew.tar.gz.parts.json", 1, 0),
    o(f"orphan.tar.gz.{G_OLD}.p000", 24 * 8),                   # no manifest at all, 8 days old
    o(f"orphan2.tar.gz.{G_OLD}.p000", 24 * 3),                  # no manifest, only 3 days old
    o(f"latest-files.tar.gz.{G_OLD}.p000", 100),                # its manifest cannot be read
    o(f"latest-kiro.tar.gz.{G_OLD}.p000", 60), o(f"latest-kiro.tar.gz.{G_LIVE}.p000", 40),
    o("latest-kiro.tar.gz", 70, 46), o("latest-kiro.tar.gz.parts.json", 40, 0),
    o("kiro-index.json", 0.1, 0),
]
manifests = {"latest-crew.tar.gz.parts.json": {"parts": [f"latest-crew.tar.gz.{G_LIVE}.p000",
                                                         f"latest-crew.tar.gz.{G_LIVE}.p001"]},
             "latest-kiro.tar.gz.parts.json": {"parts": [f"latest-kiro.tar.gz.{G_LIVE}.p000"]}}
now = NOW.timestamp()

sb = SB(objs, manifests, broken={"latest-files.tar.gz.parts.json"})
dry = W.gc_part_generations(sb, now, dry=True)
assert not sb.deleted and dry["removed"] == 6, dry
got = W.gc_part_generations(sb, now)
gone = set(sb.deleted)
assert gone == {f"latest-crew.tar.gz.{G_OLD}.p000", f"orphan.tar.gz.{G_OLD}.p000",
                f"latest-kiro.tar.gz.{G_OLD}.p000", f"latest-kiro.tar.gz.{G_LIVE}.p000",
                "latest-kiro.tar.gz", "latest-kiro.tar.gz.parts.json"}, sorted(gone)
assert got["legacy"] and got["freed"] == (40 * 4 + 46) << 20, got
print("ok sweep: unnamed old generations, manifest-less parts after 7 days, the superseded pre-chunk copy")
assert f"latest-crew.tar.gz.{G_LIVE}.p000" not in gone and f"latest-crew.tar.gz.{G_NEW}.p000" not in gone
assert f"orphan2.tar.gz.{G_OLD}.p000" not in gone and f"latest-files.tar.gz.{G_OLD}.p000" not in gone
print("ok kept: the named generation, a young upload, a 3-day manifest-less part, an unreadable manifest")

# The pre-chunk copy is kept while the index has not yet superseded it for a day.
objs2 = [o("latest-kiro.tar.gz.parts.json", 5, 0), o(f"latest-kiro.tar.gz.{G_LIVE}.p000", 5),
         o(f"latest-kiro.tar.gz.{G_OLD}.p000", 30), o("kiro-index.json", 1, 0)]
sb2 = SB(objs2, {"latest-kiro.tar.gz.parts.json": {"parts": [f"latest-kiro.tar.gz.{G_LIVE}.p000"]}})
got2 = W.gc_part_generations(sb2, now)
assert not got2["legacy"] and sb2.deleted == [f"latest-kiro.tar.gz.{G_OLD}.p000"], sb2.deleted
sb3 = SB([o("latest-kiro.tar.gz.parts.json", 50, 0), o(f"latest-kiro.tar.gz.{G_LIVE}.p000", 50)],
         {"latest-kiro.tar.gz.parts.json": {"parts": [f"latest-kiro.tar.gz.{G_LIVE}.p000"]}})
assert not W.gc_part_generations(sb3, now)["legacy"] and not sb3.deleted, "no index: the old copy is all there is"
print("ok legacy: kept while recent, and kept for good when there is no chunk index")
print("all passed")
