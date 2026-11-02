#!/bin/bash
# Build index.html from the vault content and publish it to GitHub.
# Commits index.html and the embed.html redirect stub only. Use --dry-run to build and show the change without committing.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONTENT="${GD_DIGEST_CONTENT:-$HOME/Vaults/2026-PkWr/70 Sandpit/Sandpit - GD Digest/Claude - GD Digest}"
export GD_DIGEST_CONTENT="$CONTENT"

[ -s "$CONTENT/gd-digest.md" ] || { echo "Stop: $CONTENT/gd-digest.md is missing or empty (vault moved or not synced?)"; exit 1; }
cd "$REPO"
git checkout -- index.html embed.html 2>/dev/null || true   # discard any earlier dry-run output
if [ -n "$(git status --porcelain --untracked-files=no -- . ':!index.html' ':!embed.html')" ]; then
  echo "Stop: uncommitted changes in the repo:"; git status --short; exit 1
fi
git pull --ff-only
python3 build/build-embed.py

if [ -z "$(git status --porcelain -- index.html embed.html)" ]; then echo "index.html unchanged: nothing to publish"; exit 0; fi
git status --short -- index.html embed.html
if [ "${1:-}" = "--dry-run" ]; then
  echo "Dry run: not committed. The next run discards this build."; exit 0
fi
git add index.html embed.html
git commit -m "$(date +%d.%m.%y) Update"
git push
