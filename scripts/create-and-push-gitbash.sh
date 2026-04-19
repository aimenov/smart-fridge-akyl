#!/usr/bin/env bash
# Create github.com/<you>/smart-fridge-akyl and push branch "master" using Git + curl only.
#
# Automated (needs a classic PAT with "repo" scope):
#   export GITHUB_TOKEN=ghp_your_token
#   bash scripts/create-and-push-gitbash.sh
#
# Manual (website only, then Git Credential Manager):
#   Create EMPTY repo "smart-fridge-akyl" at https://github.com/new
#   git remote add origin https://github.com/YOUR_USER/smart-fridge-akyl.git
#   git push -u origin master

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
REPO_NAME="smart-fridge-akyl"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "GITHUB_TOKEN is not set."
  echo ""
  echo "Automated: create a classic PAT with 'repo' at https://github.com/settings/tokens/new"
  echo "  export GITHUB_TOKEN=ghp_xxxxxxxx"
  echo "  bash scripts/create-and-push-gitbash.sh"
  echo ""
  echo "Manual: create an EMPTY repo named ${REPO_NAME} at https://github.com/new"
  echo "  git remote add origin https://github.com/YOUR_USER/${REPO_NAME}.git"
  echo "  git push -u origin master"
  exit 1
fi

TMP="$(mktemp)"
CODE=$(curl -sS -o "$TMP" -w "%{http_code}" \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  https://api.github.com/user/repos \
  -d "{\"name\":\"${REPO_NAME}\",\"private\":false}")

if [[ "$CODE" == "201" ]]; then
  echo "GitHub API: repository '${REPO_NAME}' created."
elif [[ "$CODE" == "422" ]]; then
  echo "GitHub API: repository may already exist (422). Continuing."
else
  echo "GitHub API error HTTP ${CODE}:"
  cat "$TMP"
  rm -f "$TMP"
  exit 1
fi
rm -f "$TMP"

LOGIN=$(curl -sS -H "Authorization: Bearer ${GITHUB_TOKEN}" https://api.github.com/user \
  | sed -n 's/.*"login"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

if [[ -z "${LOGIN}" ]]; then
  echo "Could not read GitHub login from API response."
  exit 1
fi

echo "Authenticated as: ${LOGIN}"

git remote remove origin 2>/dev/null || true
git remote add origin "https://${LOGIN}:${GITHUB_TOKEN}@github.com/${LOGIN}/${REPO_NAME}.git"

git push -u origin master

git remote set-url origin "https://github.com/${LOGIN}/${REPO_NAME}.git"

echo ""
echo "Done. origin -> https://github.com/${LOGIN}/${REPO_NAME} (token removed from stored remote URL)"
echo "Next pushes: git push   (Git Credential Manager may prompt.)"
