#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/discordbot}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
DEPLOY_REMOTE="${DEPLOY_REMOTE:-origin}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$REPO_DIR/venv}"
SERVICE_NAME="${SERVICE_NAME:-discord-bot.service}"
RESTART_TRIGGER_FILE="${RESTART_TRIGGER_FILE:-$REPO_DIR/.deploy-restart}"
LOCK_FILE="${LOCK_FILE:-/tmp/discordbot-deploy.lock}"
LOG_FILE="${LOG_FILE:-$REPO_DIR/deploy/deploy.log}"

mkdir -p "$(dirname "$LOG_FILE")"

{
  echo "[$(date -Iseconds)] deploy start ref=${GITHUB_REF:-unknown} sha=${GITHUB_AFTER_SHA:-unknown}"

  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "[$(date -Iseconds)] deploy skipped (another deploy is running)"
    exit 0
  fi

  cd "$REPO_DIR"

  git fetch "$DEPLOY_REMOTE" "$DEPLOY_BRANCH"
  git checkout "$DEPLOY_BRANCH"
  git reset --hard "$DEPLOY_REMOTE/$DEPLOY_BRANCH"

  if [[ -x "$VENV_DIR/bin/pip" ]]; then
    "$VENV_DIR/bin/pip" install -r requirements.txt
  else
    "$PYTHON_BIN" -m pip install -r requirements.txt
  fi

  if [[ -x "$VENV_DIR/bin/python" ]]; then
    "$VENV_DIR/bin/python" -m py_compile bot.py
  else
    "$PYTHON_BIN" -m py_compile bot.py
  fi

  touch "$RESTART_TRIGGER_FILE"
  echo "[$(date -Iseconds)] deploy success"
} >>"$LOG_FILE" 2>&1
