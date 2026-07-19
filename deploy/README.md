# Deployment

Production is a docker compose stack on the Synology NAS (`~/projects/discordbot`),
deployed from a local Gitea instance on the same box. GitHub stays as CI + offsite
backup and does **not** deploy.

```
git push nas main
   └─> Gitea (NAS :3000, ssh :2222)
        └─> push webhook (HMAC, LAN-only) ─> deployer container (:9000)
             └─> deploy.sh: git reset to origin/main
                            docker compose build bot
                            py_compile gate
                            docker compose up -d bot
```

## Pieces

- `../docker-compose.yml` — `bot` (python:3.13 image) + `deployer` (this listener)
- `../Dockerfile` — the bot image; DB/logs live on the `runtime/` bind mount
- `Dockerfile` — deployer image (docker CLI + python; socket-mounted, so deploys
  never need sudo after first bring-up)
- `webhook_listener.py` — verifies `X-Gitea-Signature`, filters ref, runs deploy.sh
- `deploy.sh` — the redeploy itself; logs to `deploy.log`
- `nas-setup.sh` — one-time idempotent bootstrap (Gitea, admin/token/repo/webhook,
  clone, `.env` skeleton, first `compose up`). Run with sudo on the NAS.

## Secrets

`~/projects/discordbot/.env` on the NAS (never committed): `DISCORD_TOKEN`,
`OPENWEATHER_API_KEY`, `ADMIN_ID`, `OLLAMA_URL`, `WEBHOOK_SECRET`, `DEPLOY_REPO`.
Gitea admin credentials: `~/projects/gitea/admin-credentials.txt`.

## Restoring the old production DB

Stop the bot, drop the file in, start it (migrations handle old schemas):

```bash
sudo docker compose stop bot
cp /path/to/old/bot.db ~/projects/discordbot/runtime/bot.db
sudo docker compose start bot
```
