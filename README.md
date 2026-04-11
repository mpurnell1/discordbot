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
| `.daily` | Claim daily coins |
| `.guess <1-10>` | Guess a number for a free coin when broke (3x/day) |
| `.balance` | Check your balance |
| `.leaderboard` | Top 10 richest |
| `.coinflip <amount>` | Double or nothing |
| `.slots <amount>` | Slot machine |
| `.blackjack <amount>` | Play 21 (then `.hit` / `.stand`) |
| `.weather [city]` | Current weather (defaults to Champaign) |
| `.cat` | Random cat pic |
| `.dog` | Random dog pic |
| `.wyr` | Would You Rather |
| `.onthisday` | Historical event today |
| `.changenick @user <name>` | Rename someone for 24h (2000 coins) |
| `.ttt @user` | Tic-tac-toe (use `.m <1-9>` to move) |
| `.c4 @user` | Connect 4 (use `.drop <1-7>` to play) |
| `.hangman` | Hangman — anyone can guess with `.g <letter>` |
| `.forfeit` | Quit the current game |
| `.ask <question>` | Ask the AI (needs Ollama running) |
| `.quote` | Reply to a message to save it |
| `.quotes` | Show recent quotes |
| `.stats` | Bot stats: uptime, latency, versions, economy, command usage |
| `.invite` | Generate an invite link with required permissions |
| `.help` | Show all commands |

## Configuration

Tunable settings are at the top of `bot.py`. Environment variables are loaded from `.env` via python-dotenv.

| Setting | Default | What it does |
|---------|---------|--------------|
| `PREFIX` | `.` | Command prefix |
| `DAILY_AMOUNT` | `200` | Coins per daily claim |
| `NICKNAME_COST` | `2000` | Cost to change someone's nickname |
| `NICKNAME_DURATION_HOURS` | `24` | How long the nickname lasts |
| `STARTING_BALANCE` | `100` | Coins new users start with |
| `LUCKY_GUESS_RANGE` | `10` | Guess a number from 1 to N |
| `LUCKY_GUESS_REWARD` | `1` | Coins awarded on correct guess |
| `LUCKY_GUESS_MAX_DAILY` | `3` | Max guess attempts per day |
| `LATE_NIGHT_START` | `1` | Start of "late night" window (1am Central) |
| `LATE_NIGHT_END` | `5` | End of "late night" window (5am Central) |
| `LATE_NIGHT_CHANCE` | `0.4` | Probability of calling someone out (40%) |
| `DEAD_CHAT_THRESHOLDS` | `[60, 180, 360, 720]` | Minutes of silence for each escalation stage |
| `UNSOLICITED_CHANCE` | `0.12` | Probability a message gets sent to AI (12%) |
| `COLOR_DEFAULT` | `0x5865F2` | Embed color for info (Discord blurple) |
| `COLOR_SUCCESS` | `0x57F287` | Embed color for wins (green) |
| `COLOR_ERROR` | `0xED4245` | Embed color for errors/losses (red) |
| `COLOR_WARNING` | `0xFEE75C` | Embed color for neutral/partial (yellow) |
| `COLOR_PINK` | `0xEB459E` | Embed color for Would You Rather |
| `COLOR_ORANGE` | `0xE67E22` | Embed color for On This Day |
| `COLOR_GOLD` | `0xF1C40F` | Embed color for leaderboard |
