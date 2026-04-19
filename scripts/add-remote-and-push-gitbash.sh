#!/usr/bin/env bash
# After creating an EMPTY repo 'smart-fridge-akyl' on GitHub (via website):
#   bash scripts/add-remote-and-push-gitbash.sh YOUR_GITHUB_USERNAME
set -euo pipefail
USER="${1:?Usage: $0 YOUR_GITHUB_USERNAME}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/${USER}/smart-fridge-akyl.git"
git push -u origin master
