#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/home/alphacode/finage}"
PM2_APP="${PM2_APP:-finage-bot}"
REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"

echo "Deploy directory: ${DEPLOY_DIR}"
echo "PM2 app: ${PM2_APP}"
echo "Tracking: ${REMOTE}/${BRANCH}"

cd "${DEPLOY_DIR}"

if [[ -n "$(git status --porcelain)" ]]; then
  echo
  echo "Warning: local working tree has uncommitted changes:"
  git status --short
  echo
fi

echo "Fetching latest source..."
git fetch "${REMOTE}"

current_head="$(git rev-parse --short HEAD)"
remote_head="$(git rev-parse --short "${REMOTE}/${BRANCH}")"

if [[ "${current_head}" == "${remote_head}" ]]; then
  echo "Already up to date at ${current_head}."
else
  echo "Updating ${current_head} -> ${remote_head}..."
  git pull --ff-only "${REMOTE}" "${BRANCH}"
fi

echo
echo "Restarting ${PM2_APP}..."
pm2 restart "${PM2_APP}"

echo
echo "Deployment verification:"
echo "HEAD: $(git rev-parse --short HEAD)"
echo "${REMOTE}/${BRANCH}: $(git rev-parse --short "${REMOTE}/${BRANCH}")"
pm2 status
