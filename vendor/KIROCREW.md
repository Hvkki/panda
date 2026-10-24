# Kiro Crew (upstream dependency)

pinned_commit: ebdac55640c15307ab77e1e6c9d2ea6917d43373
repository: https://github.com/kirodotdev/KiroCrew
license: Apache-2.0

Panda runs Kiro Crew **unmodified**. The worker installs it on Kaggle with the official installer
(`https://download.crew.kiro.dev/cli.sh`, version 0.9.0 when this was written). This repository does
not copy Kiro Crew.

`scripts/fetch-kirocrew.sh` checks out the pinned commit into `vendor/KiroCrew` for reading and
local work. The authoring copy had no uncommitted changes.
