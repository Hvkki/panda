#!/usr/bin/env python3
"""Build Panda's Android app (android/, a Trusted Web Activity) into a signed APK.

Needs JDK 17+, an Android SDK in $ANDROID_HOME, Gradle on PATH (or $GRADLE), and
SUPABASE_URL + SUPABASE_SECRET_KEY. The signing key is kept in Supabase Storage
(private bucket kirocrew-state, config/android/), never in git, so every machine
builds with the SAME key and a new APK installs over the old one. If no key is
stored yet, one is made and uploaded. The key's fingerprint is written to
app/public/.well-known/assetlinks.json; deploy the app after it changes, or the
Android app shows a URL bar.

Usage: python3 scripts/build-apk.py [--version-code N]   -> prints the APK path.
"""
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANDROID = ROOT / "android"
WORK = Path.home() / "work" / "android"
KEY, PASS = WORK / "panda.jks", WORK / "panda.jks.pass"
REMOTE = "kirocrew-state/config/android"
PACKAGE = "com.hvkki.panda"


def storage(method, name, data=None):
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"]
    path = f"authenticated/{REMOTE}/{name}" if method == "GET" else f"{REMOTE}/{name}"
    req = urllib.request.Request(f"{url}/storage/v1/object/{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {key}", "apikey": key,
        "x-upsert": "true", "Content-Type": "application/octet-stream"})
    return urllib.request.urlopen(req, timeout=60).read()


def ensure_key():
    WORK.mkdir(parents=True, exist_ok=True)
    try:
        KEY.write_bytes(storage("GET", "panda.jks"))
        PASS.write_bytes(storage("GET", "panda.jks.pass"))
        print("build-apk: using the stored signing key")
    except urllib.error.HTTPError as e:
        if e.code not in (400, 404):
            raise
        pw = secrets.token_urlsafe(24)
        KEY.unlink(missing_ok=True)
        subprocess.run(["keytool", "-genkeypair", "-keystore", str(KEY), "-alias", "panda",
                        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                        "-storepass", pw, "-keypass", pw, "-dname", "CN=Panda"],
                       check=True, capture_output=True)
        PASS.write_text(pw)
        storage("POST", "panda.jks", KEY.read_bytes())
        storage("POST", "panda.jks.pass", pw.encode())
        print("build-apk: made a new signing key and stored it")
    os.chmod(KEY, 0o600)
    os.chmod(PASS, 0o600)
    return PASS.read_text().strip()


def fingerprint(pw):
    out = subprocess.run(["keytool", "-list", "-v", "-keystore", str(KEY), "-alias", "panda",
                          "-storepass", pw], check=True, capture_output=True, text=True).stdout
    return next(l.split(":", 1)[1].strip() for l in out.splitlines() if l.strip().startswith("SHA256:"))


def main():
    pw = ensure_key()
    links = ROOT / "app" / "public" / ".well-known" / "assetlinks.json"
    links.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps([{"relation": ["delegate_permission/common.handle_all_urls"],
                        "target": {"namespace": "android_app", "package_name": PACKAGE,
                                   "sha256_cert_fingerprints": [fingerprint(pw)]}}], indent=2) + "\n"
    if not links.exists() or links.read_text() != body:
        links.write_text(body)
        print(f"build-apk: wrote {links.relative_to(ROOT)} -- deploy the app so it goes live")
    env = dict(os.environ, PANDA_KEYSTORE=str(KEY), PANDA_KEYSTORE_PASS=pw)
    args = [os.environ.get("GRADLE", "gradle"), "-q", "--no-daemon", "assembleRelease"]
    if "--version-code" in sys.argv:
        n = sys.argv[sys.argv.index("--version-code") + 1]
        args.append(f"-PversionCode={int(n)}")
    subprocess.run(args, cwd=ANDROID, env=env, check=True)
    print(ANDROID / "app" / "build" / "outputs" / "apk" / "release" / "app-release.apk")


if __name__ == "__main__":
    main()
