# Discord Bot

A modular friend-group Discord bot with economy, games, AI features, and admin runtime controls.

## Prerequisites

- Python 3.9+
- A [Discord bot token](https://discord.com/developers/applications) with Message Content and Server Members intents enabled
- An [OpenWeatherMap API key](https://openweathermap.org/api) (free tier)
- [Ollama](https://ollama.com) running on a local/network machine (for AI features)

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
OLLAMA_REASONING_MODEL=deepseek-r1:8b
```

Run locally:

```bash
python3 bot.py
```

## Logging

The bot writes rotating logs to:

- `logs/bot.log`

Rotation policy:

- Daily rotation at UTC midnight
- Keep last 14 log files

## Project Structure

- `bot.py`: bootstrap, cog loading, global command error handling
- `shared.py`: config/constants, DB setup, shared helpers/runtime settings
- `modules/ai.py`: AI listeners/tasks and AI commands
- `modules/economy.py`: economy, puzzle, and gambling commands
- `modules/games.py`: ttt/c4/hangman game commands and listeners
- `modules/misc.py`: weather/fun/quotes/admin/help/stats/invite

## Commands

| Command | Description |
|---------|-------------|
| `.daily` | Claim daily coins |
| `.guess <1-10>` | Guess a number for a free coin when broke (3x/day) |
| `.puzzle` / `.solve <answer>` | Daily puzzle flow |
| `.balance [@user]` | Check balance |
| `.leaderboard` | Top 10 richest |
| `.coinflip <amount>` | Double or nothing |
| `.slots <amount>` | Slot machine |
| `.blackjack <amount>` | Blackjack (then `.hit` / `.stand`) |
| `.ttt @user` | Tic-tac-toe (use `.m <1-9>`) |
| `.c4 @user` | Connect 4 (use `.drop <1-7>` or `.m <1-7>`) |
| `.hangman` | Start hangman |
| plain single-letter message | Hangman letter guess (when hangman active) |
| `.g <guess>` | Hangman guess (letter or word) |
| `.forfeit` | Quit current game |
| `.weather [city]` | Current weather (defaults to Champaign) |
| `.cat` / `.dog` | Random animal pics |
| `.wyr` | Would You Rather |
| `.onthisday` | Historical event today |
| `.changenick @user <name>` | Rename someone for 24h (costs coins) |
| `.ask <question>` | Ask AI (Ollama required) |
| `.rp <character>` / `.stoprp` | Silas roleplay controls |
| `.quote` / `.quotes` / `.unquote <id>` | Quote system |
| `.stats` | Bot statistics |
| `.invite` | Bot invite link |
| `.help` | Command list |

## Admin Runtime Settings

Primary admin control command:

- `.settings` -> show runtime settings

Gary autonomous gambling controls are nested under settings:

- `.settings gamble on` -> enable and bind gambling to current channel
- `.settings gamble off` -> disable
- `.settings gamble status` -> show state + bound channel
- `.settings gamble now` -> force one immediate gambling step
- `.settings gamble channel [#channel]` -> set channel (current if omitted)

Other admin controls:

- `.setcommand <command> <on|off>`
- `.setdeadchat <on|off>`
- `.setfeaturemode <feature> <all|off|whitelist|blacklist>`
- `.setfeaturechannels <feature> <add|remove|clear> [#channel ...]`
- `.restart`
- `.give @user <amount>`

## Notes

- Runtime settings are persisted in SQLite `settings` table.
- Feature gating is channel-aware via `feature_channel_rules`.
- Some AI/passive features depend on Ollama availability.
- Gary's autonomous gamble opener uses Silas `!scratches` before low-stakes blackjack.
