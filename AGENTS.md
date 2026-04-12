# Agents Guide

## Project Overview

Discord bot ("Gary") built with discord.py. Four cog modules:

- **`modules/ai.py`**: Ollama AI integration, autonomous gambling/hangman against Silas bot, passive commentary
- **`modules/economy.py`**: Currency system, daily rewards, gambling (coinflip/slots/blackjack)
- **`modules/games.py`**: Tic-tac-toe, Connect 4, Hangman
- **`modules/misc.py`**: Utility commands (weather, quotes, admin controls, stats)

`shared.py` is the central config — env vars, constants, DB helpers, economy functions, Ollama wrappers, runtime settings.

## Key gotchas

- Command prefix is `.` (not `!`)
- **Runtime settings are cached in memory** at startup from SQLite. Editing the DB directly won't affect the running bot — use the admin commands or restart.
- Daily resets use **5am Central time**, not midnight UTC. The `_scratch_reset_key()` logic subtracts a day if hour < 5.
- **Ollama is optional** — if it's down, `query_ollama` returns `None` and AI features silently degrade.
- **No test suite** — testing is manual against the live Discord server.

## Deployment

The bot runs on a Raspberry Pi accessible via Tailscale at hostname `rpi` (user `mpurnell`).
The repo lives at `/opt/discordbot` and runs inside a virtualenv at `/opt/discordbot/venv`.

### Auto-deploy pipeline

Pushing to `main` triggers an automatic deploy:

1. **GitHub webhook** sends a push event to the RPi via Tailscale Funnel (`https://rpi.tail557b4c.ts.net/github-webhook`).
2. **`discordbot-webhook.service`** (Python listener on port 9000) verifies the signature and runs `deploy/deploy.sh`.
3. **`deploy.sh`** does `git pull --ff-only`, `pip install -r requirements.txt`, a compile check, then touches `/opt/discordbot/.deploy-restart`.
4. **`discord-bot-watcher.path`** detects the file change and triggers `discord-bot-watcher.service`, which restarts `discord-bot.service`.

No manual intervention needed. Just push to `main`.

### Systemd services

| Service | Purpose |
|---|---|
| `discord-bot.service` | The bot itself (`/opt/discordbot/venv/bin/python bot.py`) |
| `discordbot-webhook.service` | GitHub webhook listener (port 9000) |
| `discord-bot-watcher.path` | Watches `.deploy-restart` file for changes |
| `discord-bot-watcher.service` | Restarts `discord-bot.service` when triggered |

### Useful commands (run via `ssh mpurnell@rpi`)

```bash
# Bot status and logs
systemctl status discord-bot.service
journalctl -u discord-bot.service -f            # live tail
journalctl -u discord-bot.service --since "1h ago"

# Application log files
cat /opt/discordbot/logs/bot.log                 # current log
ls /opt/discordbot/logs/                         # rotated logs

# Deploy status and logs
systemctl status discordbot-webhook.service
cat /opt/discordbot/deploy/deploy.log

# Manual restart (if needed)
sudo systemctl restart discord-bot.service
```
