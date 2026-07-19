#!/usr/bin/env bash
# One-time NAS bootstrap: Gitea + the bot stack under ~/projects.
#
# Run as:  sudo bash nas-setup.sh
# Needs:   Container Manager installed (provides docker + compose).
#
# Idempotent — safe to rerun; each phase skips work already done.
# Root is only needed because the docker CLI on DSM is root-only; after this,
# deploys run inside the deployer container and never need sudo again.
set -euo pipefail

NAS_USER="${NAS_USER:-ratthew}"
NAS_IP="${NAS_IP:-192.168.40.183}"
NAS_HOME="$(eval echo "~$NAS_USER")"
PROJECTS_DIR="$NAS_HOME/projects"
GITEA_DIR="$PROJECTS_DIR/gitea"
BOT_DIR="$PROJECTS_DIR/discordbot"
GITEA_URL="http://127.0.0.1:3000"
CREDS_FILE="$GITEA_DIR/admin-credentials.txt"
GITEA_ADMIN="$NAS_USER"
GITEA_EMAIL="${GITEA_EMAIL:-matt.purnell98@gmail.com}"
REPO_NAME="discordbot"
REPO_WAIT_MINUTES="${REPO_WAIT_MINUTES:-15}"

command -v docker >/dev/null || { echo "docker not found — install Container Manager first"; exit 1; }

randhex() { od -vN 24 -An -tx1 /dev/urandom | tr -d " \n"; }

# --- Phase 1: Gitea -------------------------------------------------------
mkdir -p "$GITEA_DIR/data"
if [ ! -f "$GITEA_DIR/docker-compose.yml" ]; then
  cat > "$GITEA_DIR/docker-compose.yml" <<EOF
services:
  gitea:
    image: gitea/gitea:1.24
    restart: unless-stopped
    environment:
      USER_UID: "1026"
      USER_GID: "100"
      GITEA__server__ROOT_URL: http://$NAS_IP:3000/
      GITEA__server__SSH_PORT: "2222"
      GITEA__security__INSTALL_LOCK: "true"
      GITEA__service__DISABLE_REGISTRATION: "true"
      GITEA__database__DB_TYPE: sqlite3
      # Webhook targets are LAN addresses (the deployer on this same box).
      GITEA__webhook__ALLOWED_HOST_LIST: private
    ports:
      - "3000:3000"
      - "2222:22"
    volumes:
      - ./data:/data
      - /etc/localtime:/etc/localtime:ro
EOF
fi

( cd "$GITEA_DIR" && docker compose up -d )

echo "waiting for Gitea to come up..."
for _ in $(seq 1 60); do
  curl -fsS "$GITEA_URL/api/healthz" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "$GITEA_URL/api/healthz" >/dev/null || { echo "Gitea did not come up"; exit 1; }

# --- Phase 2: admin user + API token -------------------------------------
if [ ! -f "$CREDS_FILE" ]; then
  ADMIN_PASS="$(randhex)"
  docker exec -u git "$(cd "$GITEA_DIR" && docker compose ps -q gitea)" \
    gitea admin user create --admin --username "$GITEA_ADMIN" --password "$ADMIN_PASS" \
    --email "$GITEA_EMAIL" --must-change-password=false
  TOKEN_LINE="$(docker exec -u git "$(cd "$GITEA_DIR" && docker compose ps -q gitea)" \
    gitea admin user generate-access-token --username "$GITEA_ADMIN" --token-name nas-setup --scopes all --raw)"
  {
    echo "gitea url:  http://$NAS_IP:3000/"
    echo "user:       $GITEA_ADMIN"
    echo "password:   $ADMIN_PASS"
    echo "api token:  $TOKEN_LINE"
  } > "$CREDS_FILE"
  chmod 600 "$CREDS_FILE"
  chown "$NAS_USER" "$CREDS_FILE"
fi
API_TOKEN="$(awk '/api token:/ {print $3}' "$CREDS_FILE")"
api() { curl -fsS -H "Authorization: token $API_TOKEN" -H "Content-Type: application/json" "$@"; }

# --- Phase 3: repo, push keys, webhook ------------------------------------
if ! api "$GITEA_URL/api/v1/repos/$GITEA_ADMIN/$REPO_NAME" >/dev/null 2>&1; then
  api -X POST "$GITEA_URL/api/v1/user/repos" \
    -d "{\"name\":\"$REPO_NAME\",\"private\":true,\"default_branch\":\"main\"}" >/dev/null
  echo "created repo $GITEA_ADMIN/$REPO_NAME"
fi

# Any pubkeys staged at gitea/authorized-push-keys.pub get push access.
if [ -f "$GITEA_DIR/authorized-push-keys.pub" ]; then
  while IFS= read -r key; do
    [ -n "$key" ] || continue
    api -X POST "$GITEA_URL/api/v1/user/keys" \
      -d "{\"title\":\"pushkey-$(date +%s%N)\",\"key\":\"$key\"}" >/dev/null 2>&1 || true
  done < "$GITEA_DIR/authorized-push-keys.pub"
fi

WEBHOOK_SECRET=""
if [ -f "$BOT_DIR/.env" ]; then
  WEBHOOK_SECRET="$(awk -F= '/^WEBHOOK_SECRET=/ {print $2}' "$BOT_DIR/.env")"
fi
[ -n "$WEBHOOK_SECRET" ] || WEBHOOK_SECRET="$(randhex)"

HOOKS="$(api "$GITEA_URL/api/v1/repos/$GITEA_ADMIN/$REPO_NAME/hooks")"
if ! echo "$HOOKS" | grep -q ":9000/webhook"; then
  api -X POST "$GITEA_URL/api/v1/repos/$GITEA_ADMIN/$REPO_NAME/hooks" -d "{
    \"type\": \"gitea\", \"active\": true, \"events\": [\"push\"],
    \"config\": {\"url\": \"http://$NAS_IP:9000/webhook\", \"content_type\": \"json\", \"secret\": \"$WEBHOOK_SECRET\"}
  }" >/dev/null
  echo "created push webhook -> http://$NAS_IP:9000/webhook"
fi

# --- Phase 4: wait for the repo to receive its first push -----------------
echo "waiting up to $REPO_WAIT_MINUTES min for main to be pushed to Gitea..."
for _ in $(seq 1 $((REPO_WAIT_MINUTES * 6))); do
  if api "$GITEA_URL/api/v1/repos/$GITEA_ADMIN/$REPO_NAME/branches/main" >/dev/null 2>&1; then
    PUSHED=1
    break
  fi
  sleep 10
done
[ "${PUSHED:-}" = 1 ] || { echo "no main branch yet — rerun this script after pushing"; exit 1; }

# --- Phase 5: clone + bot stack -------------------------------------------
if [ ! -d "$BOT_DIR/.git" ]; then
  git clone "http://$GITEA_ADMIN:$API_TOKEN@127.0.0.1:3000/$GITEA_ADMIN/$REPO_NAME.git" "$BOT_DIR"
  # Container-side fetches go through the compose network gateway-published port.
  git -C "$BOT_DIR" remote set-url origin "http://$GITEA_ADMIN:$API_TOKEN@$NAS_IP:3000/$GITEA_ADMIN/$REPO_NAME.git"
fi

# Clean up artifacts from the pre-fix deployer that ran compose under the
# wrong project name ("repo").
docker ps -aq --filter "name=repo-bot-run" | xargs -r docker rm -f >/dev/null 2>&1 || true
docker network rm repo_default >/dev/null 2>&1 || true

mkdir -p "$BOT_DIR/runtime/logs"
if [ ! -f "$BOT_DIR/.env" ]; then
  cat > "$BOT_DIR/.env" <<EOF
DISCORD_TOKEN=FILL_ME_IN
OPENWEATHER_API_KEY=FILL_ME_IN
ADMIN_ID=FILL_ME_IN
OLLAMA_URL=http://192.168.40.218:11434
WEBHOOK_SECRET=$WEBHOOK_SECRET
DEPLOY_REPO=$GITEA_ADMIN/$REPO_NAME
EOF
  chmod 600 "$BOT_DIR/.env"
fi
chown -R "$NAS_USER" "$BOT_DIR"

( cd "$BOT_DIR" && docker compose up -d --build )

echo
echo "=== done ==="
echo "Gitea:        http://$NAS_IP:3000/  (credentials: $CREDS_FILE)"
echo "Bot stack:    $BOT_DIR (docker compose ps to inspect)"
echo "Fill in $BOT_DIR/.env (DISCORD_TOKEN etc.), then: docker compose restart bot"
