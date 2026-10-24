#!/usr/bin/env bash
# Check out Kiro Crew (upstream) at the commit pinned in vendor/KIROCREW.md.
set -euo pipefail
cd "$(dirname "$0")/.."
SHA="$(sed -n 's/^pinned_commit: *//p' vendor/KIROCREW.md | head -n1)"
[ -n "$SHA" ] || { echo "no pinned_commit line in vendor/KIROCREW.md" >&2; exit 1; }
if [ ! -d vendor/KiroCrew/.git ]; then
  git clone https://github.com/kirodotdev/KiroCrew.git vendor/KiroCrew
fi
git -C vendor/KiroCrew fetch --quiet origin
git -C vendor/KiroCrew checkout --quiet "$SHA"
echo "Kiro Crew checked out at $SHA"
