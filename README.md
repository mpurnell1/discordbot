# Discord Bot — Setup Guide

## What You Need
- Python 3.9+ (already on most Pi OS installs)
- A Discord bot token
- An OpenWeatherMap API key (free tier)
- Ollama running on your Windows desktop (for AI features)

---

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

## Step 3: Set Up Ollama on Your Windows Desktop

1. Download Ollama from https://ollama.com and install it
2. Open a terminal and pull a model:
   ```
   ollama pull llama3
   ```
3. Make Ollama accessible on your network — set this environment variable on Windows:
   ```
   OLLAMA_HOST=0.0.0.0:11434
   ```
   You can set this in System → Environment Variables, then restart Ollama.
4. Find your desktop's local IP (run `ipconfig` in cmd, look for your IPv4 address,
   usually something like `192.168.1.XXX`)
5. Test from your Pi:
   ```bash
   curl http://192.168.1.XXX:11434/api/tags
   ```
   If you get a JSON response listing your models, you're good.

**Note:** If it doesn't connect, check Windows Firewall — you may need to allow
inbound connections on port 11434.

## Step 4: Set Up on Raspberry Pi

```bash
ssh pi@your-pi-ip

mkdir ~/discord-bot
# (copy bot.py and requirements.txt into this folder)

cd ~/discord-bot
pip install -r requirements.txt

# Set your tokens
export DISCORD_TOKEN="your-discord-token-here"
export OPENWEATHER_API_KEY="your-openweather-key-here"
export OLLAMA_URL="http://192.168.1.XXX:11434"
export OLLAMA_MODEL="llama3"

# Test run
python3 bot.py
```

## Step 5: Run It 24/7 with systemd

```bash
sudo nano /etc/systemd/system/discord-bot.service
```

Paste this:

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
Environment="OLLAMA_URL=http://192.168.1.XXX:11434"
Environment="OLLAMA_MODEL=llama3"
ExecStart=/usr/bin/python3 bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable discord-bot
sudo systemctl start discord-bot

# Check status / logs
sudo systemctl status discord-bot
journalctl -u discord-bot -f
```

---

## Config Reference

All settings are at the top of `bot.py`:

### Core
| Setting               | Default | What it does                        |
|-----------------------|---------|-------------------------------------|
| `PREFIX`              | `!`     | Command prefix                      |
| `DAILY_AMOUNT`        | `200`   | Coins per daily claim               |
| `NICKNAME_COST`       | `2000`  | Cost to change someone's nickname   |
| `NICKNAME_DURATION_HOURS` | `24` | How long the nickname lasts      |
| `STARTING_BALANCE`    | `100`   | Coins new users start with          |

### Passive Features
| Setting                 | Default | What it does                                     |
|-------------------------|---------|--------------------------------------------------|
| `LATE_NIGHT_START_UTC`  | `7`     | Start of "late night" window in UTC              |
| `LATE_NIGHT_END_UTC`    | `11`    | End of "late night" window in UTC                |
| `LATE_NIGHT_CHANCE`     | `0.4`   | Probability of calling someone out (40%)         |
| `DEAD_CHAT_THRESHOLDS`  | `[60, 180, 360, 720]` | Minutes of silence for each escalation stage |
| `UNSOLICITED_CHANCE`    | `0.12`  | Probability a message gets sent to AI (12%)      |

### Ollama / AI
| Setting        | Default                          | What it does                    |
|----------------|----------------------------------|---------------------------------|
| `OLLAMA_URL`   | `http://192.168.1.100:11434`     | Your desktop's Ollama address   |
| `OLLAMA_MODEL` | `llama3`                         | Which model to use              |

---

## How the Passive Features Work

### Late Night Callout (no AI needed)
When someone sends a message between 1-5am (configurable), there's a 40% chance
the bot @mentions them with a canned response telling them to go to sleep. It only
bugs each person once per night so it's funny, not annoying.

### Dead Chat Escalation (no AI needed)
The bot tracks when the last message was sent. After periods of silence it sends
increasingly unhinged messages:
- **1 hour:** Mild nudge ("so we're just not talking anymore?")
- **3 hours:** Passive aggressive ("I'm starting to think you guys don't even like me")
- **6 hours:** Dramatic ("I've genuinely had more stimulating conversations with error logs")
- **12 hours:** Existential crisis ("if a bot sends a message and nobody reads it, does it make a sound?")

When someone finally sends a message, the timer resets.

### Unsolicited Opinions (needs desktop + Ollama)
About 12% of messages get randomly selected. The bot sends the last ~10 messages
to your Llama model with a system prompt that says "you're an opinionated member
of this group chat." The model either comments on the conversation or responds
with PASS if there's nothing worth saying. If your desktop is off, these just
silently fail — no error messages, the bot just doesn't have opinions that day.

---

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
| `!ask <question>` | Ask the AI anything (needs desktop on) |
| `!help` | Show all commands |
