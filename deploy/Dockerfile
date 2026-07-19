# Deployer image: receives Gitea push webhooks and redeploys the bot.
# docker:cli ships the docker client + compose plugin (the daemon is the
# host's, reached through the mounted socket).
FROM docker:cli

RUN apk add --no-cache python3 git bash

EXPOSE 9000

CMD ["python3", "/repo/deploy/webhook_listener.py"]
