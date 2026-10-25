#!/usr/bin/env python3
"""Search free, licensed images (Openverse + Wikimedia Commons). No API key needed."""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request

UA = {"User-Agent": "PandaImageSearch/1.0 (personal assistant)"}


def get(url: str):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read())


def openverse(q: str, n: int):
    d = get("https://api.openverse.org/v1/images/?" + urllib.parse.urlencode({"q": q, "page_size": n, "mature": "false"}))
    return [{"title": r.get("title") or q, "url": r.get("url"), "thumb": r.get("thumbnail"),
             "page": r.get("foreign_landing_url"), "license": f"CC {r.get('license', '').upper()} {r.get('license_version') or ''}".strip(),
             "by": r.get("creator") or "unknown"} for r in d.get("results", []) if r.get("url")]


def wikimedia(q: str, n: int):
    d = get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {q}",
        "gsrnamespace": 6, "gsrlimit": n, "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 1024}))
    out = []
    for p in (d.get("query", {}).get("pages", {}) or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        strip = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()
        out.append({"title": p.get("title", "").replace("File:", "").rsplit(".", 1)[0], "url": ii.get("thumburl") or ii.get("url"),
                    "page": ii.get("descriptionurl"), "license": strip(meta.get("LicenseShortName", {}).get("value")) or "see page",
                    "by": strip(meta.get("Artist", {}).get("value"))[:60] or "unknown"})
    return [o for o in out if o["url"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-n", type=int, default=6)
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--out", default="", help="where downloads go (default ~/work/images/<query>, a working folder "
                                              "that does NOT become a download card)")
    a = ap.parse_args()
    results, errors = [], []
    for fn in (openverse, wikimedia):
        try:
            results += fn(a.query, a.n)
        except Exception as e:  # one source down should not stop the other
            errors.append(f"{fn.__name__}: {e}")
        if len(results) >= a.n:
            break
    results = results[: a.n]
    slug = re.sub(r"[^a-z0-9]+", "-", a.query.lower()).strip("-")[:40] or "images"
    folder = os.path.expanduser(a.out) if a.out else os.path.expanduser(f"~/work/images/{slug}")
    for i, r in enumerate(results, 1):
        local = ""
        if a.download:
            try:
                os.makedirs(folder, exist_ok=True)
                ext = os.path.splitext(urllib.parse.urlparse(r["url"]).path)[1][:5] or ".jpg"
                local = os.path.join(folder, f"{i:02d}{ext}")
                with open(local, "wb") as f:
                    f.write(urllib.request.urlopen(urllib.request.Request(r["url"], headers=UA), timeout=30).read())
            except Exception:
                local = ""
        print(f"![{r['title'][:80]}]({r['url']})")
        print(f"*{r['by']} · {r['license']} · [source]({r['page']})*" + (f" · saved `{local}`" if local else ""))
        print()
    if not results:
        print("No images found." + (" Errors: " + "; ".join(errors) if errors else ""))


if __name__ == "__main__":
    main()
