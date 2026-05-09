# Discord Bot

[![CI](https://github.com/mpurnell1/discordbot/actions/workflows/ci.yml/badge.svg)](https://github.com/mpurnell1/discordbot/actions/workflows/ci.yml)
[![Security](https://github.com/mpurnell1/discordbot/actions/workflows/security.yml/badge.svg)](https://github.com/mpurnell1/discordbot/actions/workflows/security.yml)
![Python](https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

A modular friend-group Discord bot with economy, games, AI features, and admin runtime controls.

## Prerequisites

- Python 3.10+
- A [Discord bot token](https://discord.com/developers/applications) with Message Content and Server Members intents enabled
- An [OpenWeatherMap API key](https://openweathermap.org/api) (free tier)
- [Ollama](https://ollama.com) running on a local/network machine (for AI features)
- On Windows: the `tzdata` package (included in requirements.txt)

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
ADMIN_ID=your-discord-user-id
OLLAMA_URL=http://your-ollama-host:11434
OLLAMA_MODEL=llama3
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

Daily coins are awarded automatically the first time a user runs any command after their daily resets (5 AM Central) — there is no `.daily` command.

| Command | Aliases | Description |
|---------|---------|-------------|
| `.guess <1-10>` | | Guess a number for a free coin when broke (3x/day) |
| `.puzzle` / `.solve <answer>` | | Daily puzzle flow |
| `.balance [@user]` | `.bal` | Check balance |
| `.leaderboard` | `.lb`, `.top` | Top 10 richest |
| `.coinflip <amount>` | `.cf` | Double or nothing |
| `.slots <amount>` | | Slot machine |
| `.blackjack <amount>` | `.bj` | Blackjack (then `.hit` / `.stand`) |
| `.ttt @user` | | Tic-tac-toe (use `.m <1-9>`) |
| `.c4 @user` | | Connect 4 (use `.drop <1-7>` or `.m <1-7>`) |
| `.hangman` | | Start hangman |
| plain single-letter message | | Hangman letter guess (when hangman active) |
| `.g <guess>` | | Hangman guess (letter or word) |
| `.rps <rock\|paper\|scissors>` | | Rock Paper Scissors |
| `.roll [sides]` | | Roll a die |
| `.mathgame` / `.mathanswer <answer>` | `.mathquiz`, `.mathans` | Quick arithmetic game |
| `.memory` / `.memoryanswer <sequence>` | `.memanswer`, `.memans` | Memory sequence game |
| `.trivia` / `.triviaanswer <A-D>` | `.ta` | Kid-safe multiple-choice trivia |
| `.scramble` / `.unscramble <word>` | | Kid-safe word scramble |
| `.timer <seconds>` | | Simple timer, capped at one hour |
| `.forfeit` | | Quit current game |
| `.weather [city] [forecast]` | | Current weather (defaults to Champaign); append `forecast` for a 4-day outlook |
| `.cat` / `.dog` | | Random animal pics |
| `.wyr` | | Would You Rather |
| `.onthisday` | | Historical event today |
| `.changenick @user <name>` | | Rename someone for 24h (costs coins) |
| `.ask <question>` | | Ask AI (Ollama required) |
| `.rp <character>` / `.stoprp` | | Silas roleplay controls |
| `.quote` / `.quotes` / `.unquote <id>` | | Quote system (unquote is admin only) |
| `.stats [@user]` | `.stat` | Your puzzle/game/economy/gambling stats; tag someone for head-to-head game record |
| `.botstat` | `.botstats` | Runtime bot stats — uptime, commands used, messages seen (admin only) |
| `.invite [kids]` | | Bot invite link |
| `.help` | | Command list |
| `.adminhelp` | | Admin command list (admin only) |

## Admin Runtime Settings

Primary admin control command:

- `.settings` -> show all runtime settings

### Kids mode

Kids mode is server-specific and persisted in SQLite. It is intended for servers where Gary should keep safe utility/game features while removing unpredictable or adult-leaning behavior.

- `.invite kids` -> low-permission invite link that auto-enables kids mode when Gary joins
- `.settings kids on` or `.kidsmode on` -> enable kids mode for the current server
- `.settings kids off` or `.kidsmode off` -> disable kids mode for the current server
- `.settings kids status` or `.kidsmode status` -> show the current server policy

Kids mode disables:

- Economy commands: `.guess`, `.balance`, `.leaderboard`, `.give`, `.repuzzle` (and the auto-daily-award)
- Gambling commands: `.coinflip`, `.slots`, `.blackjack`, blackjack actions, and `.bjrules`
- All AI: `.ask`, `.rp`, `.stoprp`, mention replies, unsolicited AI, Silas roleplay/banter/reacts, and autonomous gambling in that server
- All passive behavior: dead-chat callouts, late-night callouts, unsolicited AI, and other background chat reactions
- Social/moderation-risk commands: `.changenick`, `.quote`, `.quotes`, `.unquote`
- Uncurated external text content: `.onthisday`, `.invite`

Kids mode keeps:

- Games: tic-tac-toe, Connect 4, Hangman, Rock Paper Scissors, dice rolls, quick math quiz, memory, trivia, scramble, practice puzzle with no coin reward, timer
- Learning/utility: weather, kid-safe Would You Rather, clean jokes, `.stats`
- Animal pics: `.cat`, `.dog`
- Operational command: `.help`

Gary infers a kids invite from the low-permission shape: no Manage Messages and no Manage Nicknames. If that shape is detected on join, Gary writes `kids_mode=true` for the server immediately and posts a join report with un-force SQL.

### Gary autonomous gambling

- `.settings gamble on` -> enable and bind gambling to current channel
- `.settings gamble off` -> disable
- `.settings gamble status` -> show state + bound channel
- `.settings gamble now` -> force one immediate gambling step
- `.settings gamble channel [#channel]` -> set channel (current if omitted)
- `.settings gamble report [#channel]` -> set telemetry report channel (current if omitted)

### Daily 8 AM weather alert

Posts current weather plus a 4-day forecast at 8 AM Central in the configured channel.

- `.settings weather on [#channel]` -> enable in current channel (or specified channel)
- `.settings weather off` -> disable
- `.settings weather status` -> show state, channel, and city
- `.settings weather city <name>` -> set the city used for alerts (defaults to Champaign)

### Passive AI features

- `.settings passive` -> show all passive chances
- `.settings passive unsolicited <0-100>` -> unsolicited AI commentary chance (%)
- `.settings passive silasbanter <0-100>` -> Silas banter chance (%)
- `.settings passive silasreact <0-100>` -> Silas reaction chance (%)

### Other admin controls

- `.setcommand <command> <on|off>`
- `.setdeadchat <on|off>`
- `.setfeaturemode <feature> <all|off|whitelist|blacklist>`
- `.setfeaturechannels <feature> <add|remove|clear> [#channel ...]`
- `.restart`
- `.give @user <amount>`
- `.say <text>` -> delete your message and post as Gary
- `.repuzzle [@user]` -> regenerate a user's daily puzzle

## Notes

- Runtime settings are persisted in SQLite `settings` table.
- Feature gating is channel-aware via `feature_channel_rules`.
- Some AI/passive features depend on Ollama availability.
- Gary's autonomous gamble opener uses Silas `!scratches` before low-stakes blackjack.
- Passive AI chances (unsolicited, Silas banter/react) default to 0% and must be enabled via `.settings passive`.
