#!/usr/bin/env bash
# Redeploy the bot from the local Gitea remote. Runs inside the deployer
# container (repo bind-mounted at /repo, docker socket mounted), triggered by
# webhook_listener.py on a push to the deploy branch.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
DEPLOY_REMOTE="${DEPLOY_REMOTE:-origin}"
LOCK_FILE="${LOCK_FILE:-/tmp/discordbot-deploy.lock}"
LOG_FILE="${LOG_FILE:-$REPO_DIR/deploy/deploy.log}"

mkdir -p "$(dirname "$LOG_FILE")"

{
  echo "[$(date -Iseconds)] deploy start ref=${DEPLOY_REF:-unknown} sha=${DEPLOY_AFTER_SHA:-unknown}"

  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "[$(date -Iseconds)] deploy skipped (another deploy is running)"
    exit 0
  fi

  cd "$REPO_DIR"

  # The repo is owned by the NAS user but this container runs as root.
  git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true

  git fetch "$DEPLOY_REMOTE" "$DEPLOY_BRANCH"
  git checkout "$DEPLOY_BRANCH"
  git reset --hard "$DEPLOY_REMOTE/$DEPLOY_BRANCH"

  docker compose build bot
  # Sanity gate before swapping the running container.
  docker compose run --rm --no-deps --entrypoint python bot -m py_compile bot.py
  docker compose up -d bot

  echo "[$(date -Iseconds)] deploy success"
} >>"$LOG_FILE" 2>&1
