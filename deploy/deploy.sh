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

  # Self-heal: if this deployer was created with the pre-fix /repo mount, its
  # compose calls can't work (the host daemon resolves paths host-side). A
  # detached helper container — unaffected by this container being replaced —
  # recreates the stack at the canonical path, then the next push deploys
  # normally through the new deployer.
  CANONICAL_DIR="/volume1/homes/ratthew/projects/discordbot"
  if [ "$REPO_DIR" != "$CANONICAL_DIR" ]; then
    echo "[$(date -Iseconds)] legacy mount detected (REPO_DIR=$REPO_DIR); spawning self-heal helper"
    docker run -d --rm \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -v "$CANONICAL_DIR:$CANONICAL_DIR" -w "$CANONICAL_DIR" \
      docker:cli sh -c "sleep 3 && docker compose up -d --build deployer bot >>$CANONICAL_DIR/deploy/deploy.log 2>&1"
    echo "[$(date -Iseconds)] self-heal helper started; deployer will be recreated"
    exit 0
  fi

  docker compose build bot
  # Sanity gate before swapping the running container.
  docker compose run --rm --no-deps --entrypoint python bot -m py_compile bot.py
  docker compose up -d bot

  echo "[$(date -Iseconds)] deploy success"
} >>"$LOG_FILE" 2>&1
