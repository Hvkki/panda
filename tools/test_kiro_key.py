"""Settings -> Credits key change (kiro_key_command), without uploading anything.

Runs on a live Panda machine: it reads the real config dataset and checks the real key with Kiro, but
intercepts the `kaggle datasets version` upload and only inspects what would have gone up.
    python3 tools/test_kiro_key.py
"""
import importlib.util
import json
import os
import sys

sys.argv = ["x"]
spec = importlib.util.spec_from_file_location("w", os.path.join(os.path.dirname(__file__), "..", "worker", "worker.py"))
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

st = w.kiro_key_command({})
assert st["tail"].startswith("…") and st["can_change"], st
print("status ok:", {k: v for k, v in st.items() if k != "tail"})
print("dataset:", w._config_dataset())

for bad in ("short", "x" * 40):
    try:
        w.kiro_key_command({"action": "set", "key": bad})
        raise SystemExit(f"accepted a bad key ({len(bad)} chars)")
    except ValueError as e:
        print("rejected:", e)

cur = w.CFG["kiro_api_key"]
try:
    w.kiro_key_command({"action": "set", "key": cur})
    raise SystemExit("accepted the key already in use")
except ValueError as e:
    print("same key:", e)

# The full path with a key Kiro accepts (the current one): pretend another key is saved, so it isn't "the same".
w.KIRO_KEY_NEXT["key"] = "placeholder-not-a-key"
real, seen = w._kaggle_run, {}


def fake(args, t):
    if args[:2] == ["datasets", "version"]:
        d = args[args.index("-p") + 1]
        c = json.load(open(os.path.join(d, "worker.json")))
        seen.update(fields=len(c), replaced=c["kiro_api_key"] == cur, files=sorted(os.listdir(d)),
                    mode=oct(os.stat(os.path.join(d, "worker.json")).st_mode & 0o777))

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()
    return real(args, t)


w._kaggle_run = fake
r = w.kiro_key_command({"action": "set", "key": cur})
assert r["ok"] and r["next_tail"] == w._key_tail(cur) and seen["replaced"], (r, seen)
assert seen["files"] == ["dataset-metadata.json", "worker.json"], seen
assert cur in w.SECRETS
print("set ok:", {k: v for k, v in r.items() if k != "next_tail"}, seen)
print("PASS")
