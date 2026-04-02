# Discord Bot — Setup Guide

## What You Need
- Python 3.9+ (already on most Pi OS installs)
- A Discord bot token
- An OpenWeatherMap API key (free tier)

## Step 1: Create the Discord Bot

1. Go to https://discord.com/developers/applications
2. Click **New Application**, give it a name
3. Go to the **Bot** tab, click **Reset Token**, and copy the token
4. Under **Privileged Gateway Intents**, enable:
   - **Message Content Intent**
   - **Server Members Intent**
5. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Add Reactions`, `Manage Nicknames`
6. Copy the generated URL, paste it in your browser, and invite the bot to your server

## Step 2: Get an OpenWeatherMap API Key

1. Sign up at https://openweathermap.org/api
2. The free tier gives you 1,000 calls/day — plenty for a friend group
3. Copy your API key

## Step 3: Set Up on Raspberry Pi

```bash
# SSH into your Pi or open a terminal
ssh pi@your-pi-ip

# Clone or copy the bot files to your Pi
mkdir ~/discord-bot
# (copy bot.py and requirements.txt into this folder)

# Install dependencies
cd ~/discord-bot
pip install -r requirements.txt

# Set your tokens as environment variables
export DISCORD_TOKEN="your-discord-token-here"
export OPENWEATHER_API_KEY="your-openweather-key-here"

# Test run
python3 bot.py
```

## Step 4: Run It 24/7 with systemd

Create a service file so the bot starts on boot and restarts if it crashes:

```bash
sudo nano /etc/systemd/system/discord-bot.service
```

Paste this (update the paths/user if needed):

```ini
[Unit]
Description=Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/discord-bot
Environment="DISCORD_TOKEN=your-token-here"
Environment="OPENWEATHER_API_KEY=your-api-key-here"
ExecStart=/usr/bin/python3 bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable discord-bot
sudo systemctl start discord-bot

# Check status
sudo systemctl status discord-bot

# View logs
journalctl -u discord-bot -f
```

## Config You Can Tweak

These are all at the top of `bot.py`:

| Setting               | Default | What it does                        |
|-----------------------|---------|-------------------------------------|
| `PREFIX`              | `!`     | Command prefix                      |
| `DAILY_AMOUNT`        | `200`   | Coins per daily claim               |
| `NICKNAME_COST`       | `2000`  | Cost to change someone's nickname   |
| `NICKNAME_DURATION_HOURS` | `24` | How long the nickname lasts      |
| `STARTING_BALANCE`    | `100`   | Coins new users start with          |

## Commands

| Command | Description |
|---------|-------------|
| `!daily` | Claim your daily coins |
| `!balance` | Check your balance |
| `!leaderboard` | Top 10 richest players |
| `!coinflip <amount>` | 50/50 double or nothing |
| `!slots <amount>` | Slot machine |
| `!blackjack <amount>` | Play 21 (then `!hit` / `!stand`) |
| `!weather <city>` | Current weather |
| `!cat` | Random cat pic |
| `!dog` | Random dog pic |
| `!wyr` | Would You Rather |
| `!onthisday` | Historical event on today's date |
| `!changenick @user <name>` | Rename someone for 24h (2000 coins) |
| `!help` | Show all commands |
