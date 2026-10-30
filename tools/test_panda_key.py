"""panda-key (agent issue #41): an agent saves a key from an uploaded file into the vault.

    python3 tools/test_panda_key.py

Runs the real command in a temp HOME against the real worker loop (keys_loop) with a fake vault.
Checks: values never printed, existing keys kept unless --replace, DEV__ refused, secrets.env reloaded
and safe for bash ($ and ` stay literal), the request file never left behind.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading

HOME = tempfile.mkdtemp(prefix="pkey-home-")
os.environ["KIROCREW_WORKER_CFG"] = json.dumps({
    "supabase_url": "https://example.invalid", "supabase_publishable_key": "publishable-fake", "owner_id": "owner-1",
    "kiro_api_key": "ksk_fake_for_local_tests", "worker_password": "fake-worker-password-123",
    "worker_email": "worker@example.invalid"})
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(REPO, "worker"))
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp())
import worker as W  # noqa: E402
os.chdir(_cwd)
W.OWNER, W.HOME = "owner-1", HOME
W.PANDA_SECRETS = f"{HOME}/.panda/secrets.env"
W.KEYS_OUTBOX = f"{HOME}/.panda/keys-out"
CMD = os.path.join(REPO, "worker/skills/panda-keys/keys.py")


class FakeVault:
    def __init__(self):
        self.rows, self.notes = {}, []

    def rest(self, method, path, params=None, body=None, prefer=""):
        if method == "POST" and path == "rpc/worker_set_secret":
            assert body["p_owner"] == "owner-1"
            n, had = body["p_name"], body["p_name"] in self.rows
            if had and not body["p_replace"]:
                return "exists"
            self.rows[n] = body["p_value"]
            return "replaced" if had else "saved"
        if method == "GET" and path == "secrets":
            return [{"name": k, "value": v} for k, v in self.rows.items()]
        if method == "POST" and path == "notifications":
            self.notes.append(body)
            return None
        raise AssertionError((method, path))


vault = FakeVault()
stop = threading.Event()
threading.Thread(target=W.keys_loop, args=(vault, stop), daemon=True).start()
SECRET_VALUES = ["sk-aaaa1111", "two two", "c3-$HOME-`id`", "bare-key-xyz", "new-a-value"]


def run(*args, wait="20"):
    env = dict(os.environ, HOME=HOME, PANDA_KEY_WAIT=wait)
    r = subprocess.run([sys.executable, CMD, *args], env=env, capture_output=True, text=True, timeout=60)
    out = r.stdout + r.stderr
    for v in SECRET_VALUES:
        assert v not in out, f"a key value was printed: {out}"
    return r.returncode, out


up = f"{HOME}/uploads"
os.makedirs(up)
open(f"{up}/env.txt", "w").write("# keys\nAK=sk-aaaa1111\nBK=\"two two\"\nexport CK='c3-$HOME-`id`'\n")
open(f"{up}/bare.txt", "w").write("bare-key-xyz\n")
open(f"{up}/env2.txt", "w").write("AK=new-a-value\n")

rc, out = run("fields", f"{up}/env.txt")
assert rc == 0 and out.split() == ["AK", "BK", "CK"], out
print("ok fields: names only")

rc, out = run("import", f"{up}/env.txt", "--only", "AK,CK")
assert rc == 0 and "AK: saved" in out and "CK: saved" in out and "BK:" not in out, out
assert vault.rows == {"AK": "sk-aaaa1111", "CK": "c3-$HOME-`id`"}, vault.rows
got = subprocess.run(["bash", "-c", f"source {W.PANDA_SECRETS} && printf '%s|%s' \"$AK\" \"$CK\""],
                     capture_output=True, text=True, env={"PATH": os.environ["PATH"], "HOME": "/nowhere"}).stdout
assert got == "sk-aaaa1111|c3-$HOME-`id`", f"secrets.env changed the value: {got!r}"
assert vault.notes and "AK, CK" in vault.notes[-1]["text"] and "sk-" not in json.dumps(vault.notes), vault.notes
print("ok import: only the asked keys, secrets.env reloaded and bash-safe, owner told without values")

rc, out = run("add", "DK", "--from", f"{up}/bare.txt")
assert rc == 0 and "DK: saved" in out and vault.rows["DK"] == "bare-key-xyz", out
print("ok add: a bare one-line key file")

rc, out = run("add", "AK", "--from", f"{up}/env2.txt")
assert rc == 0 and "AK: exists" in out and vault.rows["AK"] == "sk-aaaa1111", out
rc, out = run("add", "AK", "--from", f"{up}/env2.txt", "--replace")
assert rc == 0 and "AK: replaced" in out and vault.rows["AK"] == "new-a-value", out
print("ok replace: an existing key changes only with --replace")

rc, out = run("add", "DEV__GITHUB_TOKEN", "--from", f"{up}/bare.txt")
assert rc == 1 and "Developer variables" in out and "DEV__GITHUB_TOKEN" not in vault.rows, out
rc, out = run("add", "EK", "--from", f"{up}/env.txt", "--field", "NOPE")
assert rc != 0 and "Fields there: AK, BK, CK" in out, out
print("ok refused: DEV__ names, a missing field lists names only")

rc, out = run("add", "FK", "--from", f"{up}/bare.txt", "--delete-source")
assert rc == 0 and not os.path.exists(f"{up}/bare.txt"), out
print("ok --delete-source")

stop.set()
import time  # noqa: E402
time.sleep(2.5)
open(f"{up}/bare.txt", "w").write("bare-key-xyz\n")
rc, out = run("add", "GK", "--from", f"{up}/bare.txt", wait="1")
assert rc != 0 and "did not pick this up" in out, out
left = [n for n in os.listdir(W.KEYS_OUTBOX) if not n.startswith(".")]
assert not left, f"a request file was left behind: {left}"
assert oct(os.stat(W.KEYS_OUTBOX).st_mode & 0o777) == "0o700"
print("ok no worker: nothing saved, nothing left on disk")
print("all passed")
