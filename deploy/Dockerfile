# Deployer image: receives Gitea push webhooks and redeploys the bot.
# docker:cli ships the docker client + compose plugin (the daemon is the
# host's, reached through the mounted socket).
FROM docker:cli

RUN apk add --no-cache python3 git bash

EXPOSE 9000

# REPO_DIR is the canonical host path of the repo, mounted at the same path
# inside this container (see docker-compose.yml).
CMD ["sh", "-c", "python3 \"$REPO_DIR/deploy/webhook_listener.py\""]
