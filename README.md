# Discord Bot

A friend group Discord bot with an economy system, gambling, weather, and AI-powered passive features.

## Prerequisites

- Python 3.9+
- A [Discord bot token](https://discord.com/developers/applications) with Message Content and Server Members intents enabled
- An [OpenWeatherMap API key](https://openweathermap.org/api) (free tier)
- [Ollama](https://ollama.com) running on a local network machine (for AI features)

## Setup

```bash
git clone <repo-url> && cd discordbot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
DISCORD_TOKEN=your-discord-token
OPENWEATHER_API_KEY=your-openweather-key
OLLAMA_URL=http://192.168.1.XXX:11434
OLLAMA_MODEL=dolphin3:8b
```

Test it:

```bash
python3 bot.py
```

## Running as a Service (Raspberry Pi / Linux)

```bash
sudo cp discord-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now discord-bot
```

Check logs:

```bash
journalctl -u discord-bot -f
```

## Commands

| Command | Description |
|---------|-------------|
| `!daily` | Claim daily coins |
| `!balance` | Check your balance |
| `!leaderboard` | Top 10 richest |
| `!coinflip <amount>` | Double or nothing |
| `!slots <amount>` | Slot machine |
| `!blackjack <amount>` | Play 21 (then `!hit` / `!stand`) |
| `!weather [city]` | Current weather (defaults to Champaign) |
| `!cat` | Random cat pic |
| `!dog` | Random dog pic |
| `!wyr` | Would You Rather |
| `!onthisday` | Historical event today |
| `!changenick @user <name>` | Rename someone for 24h (2000 coins) |
| `!ask <question>` | Ask the AI (needs Ollama running) |
| `!quote` | Reply to a message to save it |
| `!quotes` | Show recent quotes |
| `!help` | Show all commands |

## Configuration

Tunable settings are at the top of `bot.py`. Environment variables are loaded from `.env` via python-dotenv.
