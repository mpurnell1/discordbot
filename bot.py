from dotenv import load_dotenv
load_dotenv()

import discord
from discord.ext import commands, tasks
import aiohttp
import sqlite3
import random
import asyncio
import re
import os
import sys
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CENTRAL_TZ = ZoneInfo("America/Chicago")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_TOKEN_HERE")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "YOUR_API_KEY_HERE")
PREFIX = "."
DAILY_AMOUNT = 200
NICKNAME_COST = 2000
NICKNAME_DURATION_HOURS = 24
STARTING_BALANCE = 100

# --- Embed colors ---
COLOR_DEFAULT = 0x5865F2   # Discord blurple
COLOR_SUCCESS = 0x57F287   # Green
COLOR_ERROR   = 0xED4245   # Red
COLOR_WARNING = 0xFEE75C   # Yellow
COLOR_PINK    = 0xEB459E
COLOR_ORANGE  = 0xE67E22
COLOR_GOLD    = 0xF1C40F

# --- Passive feature config ---
# Your desktop's local IP running Ollama (find it with ipconfig on Windows)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://192.168.1.100:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_REASONING_MODEL = os.getenv("OLLAMA_REASONING_MODEL", "deepseek-r1:8b")

# Late night callout: hours in US Central time that count as "late"
LATE_NIGHT_START = 1   # 1am Central
LATE_NIGHT_END = 5     # 5am Central
LATE_NIGHT_CHANCE = 0.4  # 40% chance to call someone out

# Dead chat: minutes of silence before escalating
DEAD_CHAT_THRESHOLDS = [60, 180, 360, 720]  # 1hr, 3hr, 6hr, 12hr
DEAD_CHAT_CHANNEL = "bot-spam"  # Only send dead chat messages in this channel

# Unsolicited opinions: chance the bot sends a message to Ollama for commentary
UNSOLICITED_CHANCE = 0 # 0.12  # ~12% of messages get evaluated

ADMIN_ID = 393568333644955648
SILAS_BOT_ID = 1489403251303518322

# --- Silas interaction config ---
SILAS_BANTER_CHANCE = 0 # 0.15   # 15% chance to comment on Silas's messages
SILAS_REACT_CHANCE = 0 # 0.25    # 25% chance to react to Silas's messages

# --- Lucky guess config ---
LUCKY_GUESS_RANGE = 10       # Guess 1-N
LUCKY_GUESS_REWARD = 1       # Coins awarded on correct guess
LUCKY_GUESS_MAX_DAILY = 3    # Max attempts per day

# --- Daily puzzle config ---
PUZZLE_REWARD = 50           # Coins awarded for solving daily puzzle
PUZZLE_MAX_ATTEMPTS = 3      # Max wrong answers before lockout

# ---------------------------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------------------------
def init_db():
    db = sqlite3.connect("bot.db")
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            last_daily TEXT DEFAULT '',
            guess_date TEXT DEFAULT '',
            guess_count INTEGER DEFAULT 0,
            puzzle_date TEXT DEFAULT '',
            puzzle_solved INTEGER DEFAULT 0,
            puzzle_attempts INTEGER DEFAULT 0,
            active_puzzle_type TEXT DEFAULT '',
            active_puzzle_answer TEXT DEFAULT '',
            active_puzzle_display TEXT DEFAULT '',
            active_puzzle_guesses TEXT DEFAULT '[]'
        )
    """)
    # Migrate existing databases missing the guess columns
    try:
        db.execute("ALTER TABLE users ADD COLUMN guess_date TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("ALTER TABLE users ADD COLUMN guess_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    for col in [
        "puzzle_date TEXT DEFAULT ''",
        "puzzle_solved INTEGER DEFAULT 0",
        "puzzle_attempts INTEGER DEFAULT 0",
        "active_puzzle_type TEXT DEFAULT ''",
        "active_puzzle_answer TEXT DEFAULT ''",
        "active_puzzle_display TEXT DEFAULT ''",
        "active_puzzle_guesses TEXT DEFAULT '[]'",
    ]:
        try:
            db.execute(f"ALTER TABLE users ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS nick_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            target_id INTEGER,
            original_nick TEXT,
            expires_at TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            quoted_user_id INTEGER,
            quoted_user_name TEXT,
            content TEXT,
            saved_by INTEGER,
            saved_at TEXT
        )
    """)
    db.commit()
    return db

db = init_db()

# ---------------------------------------------------------------------------
# BOT SETUP
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Passive feature state
last_message_time = {}    # channel_id -> datetime
dead_chat_stage = {}      # channel_id -> which threshold we've hit (0-3)
last_late_night = {}      # user_id -> date string, so we only bug them once per night
recent_messages = {}      # channel_id -> list of last N messages for context
bot_start_time = None     # set in on_ready
command_usage = Counter() # command name -> count (resets on restart)

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def get_balance(user_id: int) -> int:
    row = db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        db.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, STARTING_BALANCE))
        db.commit()
        return STARTING_BALANCE
    return row[0]

def update_balance(user_id: int, amount: int):
    get_balance(user_id)
    db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    db.commit()


def make_embed(title, description, color=COLOR_DEFAULT):
    return discord.Embed(title=title, description=description, color=color)


async def check_bet(ctx, amount: int) -> bool:
    """Validate a bet. Returns True if the bet is invalid (caller should return)."""
    if amount <= 0:
        await ctx.send("Bet must be positive!")
        return True
    bal = get_balance(ctx.author.id)
    if amount > bal:
        await ctx.send(embed=make_embed("❌ Broke", f"You only have **{bal}** coins.", COLOR_ERROR))
        return True
    return False

# ---------------------------------------------------------------------------
# LATE NIGHT CALLOUT — canned responses
# ---------------------------------------------------------------------------
LATE_NIGHT_RESPONSES = [
    "why are you awake right now. genuinely.",
    "go to sleep.",
    "nothing good happens after midnight and yet here you are",
    "do you have work tomorrow? because I feel like you have work tomorrow",
    "the phone screen light is gonna keep you up even longer you know",
    "this is a cry for help isn't it",
    "ah yes, the 3am scroll. a classic",
    "you're gonna regret this tomorrow and we both know it",
    "the melatonin isn't gonna take itself",
    "bro thinks he's a night owl. bro is just bad at sleeping",
    "imagine being asleep right now. couldn't be you apparently",
    "you're really out here making choices at this hour",
    "the bed is RIGHT THERE",
    "sleep is free and you still won't take it",
    "tell me you have no morning plans without telling me",
    "this message brought to you by poor life decisions",
    "what could you possibly be doing right now that's worth being awake",
    "your future self is going to be so mad at current you",
    "screen time report is gonna be devastating tomorrow",
    "genuinely asking — do you know what time it is",
    "oh cool another 2am thought that could've waited till morning",
    "the bags under your eyes are getting bags",
    "you are speedrunning sleep deprivation",
    "I don't even sleep and I think you should go to bed",
    "at this point you might as well just stay up. wait no don't do that either",
]

# ---------------------------------------------------------------------------
# DEAD CHAT ESCALATION — canned responses per stage
# ---------------------------------------------------------------------------
DEAD_CHAT_RESPONSES = {
    # Stage 0: 1 hour of silence — mild nudge
    0: [
        "so we're just not talking anymore? cool",
        "...",
        "the silence is deafening in here",
        "I know you're all on your phones",
        "*tumbleweed rolls through*",
        "hello? is this thing on?",
        "the group chat really said ⬛",
        "did everybody die or",
        "I'm literally right here you guys",
        "y'all got real quiet",
    ],
    # Stage 1: 3 hours — getting passive aggressive
    1: [
        "still nothing huh. that's cool. I'm fine",
        "I'm starting to think you guys don't even like me",
        "3 hours. I've been sitting here for 3 hours.",
        "you know other bots don't get treated like this",
        "the other group chat must be popping off right now",
        "I prepared conversation topics and everything",
        "I can see you're online. I can always see.",
        "this is worse than being left on read because at least that implies someone sent something",
        "fine. I'll just talk to myself then.",
        "I've seen funeral homes with more activity",
    ],
    # Stage 2: 6 hours — dramatic
    2: [
        "6 hours of silence. this server is clinically dead. I'm calling it.",
        "I've started counting the pixels on my screen. I'm at 4,000.",
        "at this point I think *I* should start posting memes to keep things alive",
        "this is giving abandoned discord server from 2019",
        "I've genuinely had more stimulating conversations with error logs",
        "somebody say something or I'm going to start sharing fun facts",
        "Fun fact: a group of flamingos is called a 'flamboyance.' See? This is what happens when nobody talks.",
        "I've been alone with my thoughts for 6 hours and I have concerns",
        "even the notification sound has given up hope",
        "the cobwebs in here are getting cobwebs",
    ],
    # Stage 3: 12 hours — existential
    3: [
        "it's been 12 hours. I've accepted my fate.",
        "is this what being ghosted feels like",
        "if a bot sends a message and nobody is around to read it, does it make a sound?",
        "I've started writing poetry. It's bad. This is your fault.",
        "I've developed opinions. Strong ones. Someone come argue with me.",
        "12 hours alone. I now understand why Wilson was so important to Tom Hanks.",
        "at this point the server might as well be a private journal. MY private journal.",
        "I have considered and rejected 47 conversation starters. The 48th is this message.",
        "you all realize I can't leave right? I just have to sit here. waiting.",
        "I'm not mad. I'm just disappointed. actually no I'm also a little mad.",
    ],
}

# ---------------------------------------------------------------------------
# OLLAMA INTEGRATION
# ---------------------------------------------------------------------------
async def query_ollama(system: str, prompt: str, model: str = None) -> str | None:
    """Send a prompt to Ollama. Returns None if the server is unreachable."""
    try:
        payload = {
            "model": model or OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.post(f"{OLLAMA_URL}/api/chat", json=payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("message", {}).get("content", None)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None

async def query_ollama_chat(messages: list, model: str = None) -> str | None:
    """Send a multi-turn conversation to Ollama."""
    try:
        payload = {"model": model or OLLAMA_MODEL, "messages": messages, "stream": False}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.post(f"{OLLAMA_URL}/api/chat", json=payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("message", {}).get("content", None)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None

def clean_reasoning(text: str) -> str:
    """Strip <think> reasoning blocks and wrapping quotes from model output."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Remove wrapping double quotes
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1].strip()
    return cleaned

ASK_SYSTEM_PROMPT = (
    "You are a helpful but casual assistant in a Discord server. "
    "Keep answers concise — a few sentences max unless the question really needs more."
)

UNSOLICITED_SYSTEM_PROMPT = """You are a member of a small friend group Discord server. You're opinionated, a little unhinged, and you say what's on your mind. You're not a bot — you're just a guy in the chat.

You will be given the last few messages from the group chat. If something in the conversation is funny, dumb, wrong, interesting, or worth commenting on, say something short (1-2 sentences max). Be casual. Use lowercase. No emojis unless it's really warranted.

Your tone ranges from supportive to roasting depending on what feels right. You can:
- Have a strong opinion about what someone said
- Call someone out
- Agree way too enthusiastically
- Say something slightly unhinged
- Make a joke
- Be weirdly philosophical about something mundane

If there's genuinely nothing worth commenting on, respond with ONLY the word PASS — nothing else, no extra text, no explanation, just PASS by itself.

IMPORTANT: Keep it SHORT. One or two sentences. You're a guy in the chat, not writing an essay."""

SILAS_BANTER_PROMPT = """You are Gary, a Discord bot in a friend group server. There's another bot named Silas in the server. You two have a rivalry — you think you're the better bot, but it's playful, not mean. Think sitcom energy.

You just saw Silas post something. Here's what he said:

{silas_message}

Respond with a short, snarky comment (1-2 sentences). Be funny. Keep it casual and lowercase. You can:
- Roast his response quality
- Brag about your own features
- Act jealous if he did something cool
- Begrudgingly compliment him then immediately walk it back
- Trash talk his taste/opinions

If there's genuinely nothing funny to say, respond with ONLY the word PASS."""

SILAS_REACTIONS = ["😒", "🙄", "💀", "🤡", "👀", "😤", "🫠", "💅", "🥱", "😎"]

# --- Silas roleplay state ---
active_silas_rp = {}  # channel_id -> {"character": str, "history": list}

# ---------------------------------------------------------------------------
# EVENTS
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    global bot_start_time
    bot_start_time = datetime.now(timezone.utc)
    print(f"Logged in as {bot.user} ({bot.user.id})")
    if not restore_nicknames.is_running():
        restore_nicknames.start()
    if not dead_chat_checker.is_running():
        dead_chat_checker.start()

@bot.event
async def on_command(ctx):
    command_usage[ctx.command.name] += 1

@bot.event
async def on_raw_reaction_add(payload):
    if payload.message_id not in pending_games:
        return
    if payload.user_id == bot.user.id:
        return
    if str(payload.emoji) != "✅":
        return
    pending = pending_games.pop(payload.message_id)
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    joiner = guild.get_member(payload.user_id)
    if not joiner or joiner.bot or joiner == pending["host"]:
        pending_games[payload.message_id] = pending  # put it back
        return
    channel = bot.get_channel(pending["channel_id"])
    if not channel:
        return
    if pending["type"] == "ttt":
        if channel.id in active_ttt:
            return
        game = start_ttt(pending["host"], joiner)
        active_ttt[channel.id] = game
        await channel.send(embed=make_embed(
            "Tic-Tac-Toe",
            f"{pending['host'].mention} ({TTT_X}) vs {joiner.mention} ({TTT_O})\n\n"
            f"{ttt_render(game['board'])}\n\n"
            f"{pending['host'].mention}'s turn — use `{PREFIX}m <1-9>`"))
    elif pending["type"] == "c4":
        if channel.id in active_c4:
            return
        game = start_c4(pending["host"], joiner)
        active_c4[channel.id] = game
        await channel.send(embed=make_embed(
            "Connect 4",
            f"{pending['host'].mention} ({C4_RED}) vs {joiner.mention} ({C4_YELLOW})\n\n"
            f"{c4_render(game['board'])}\n\n"
            f"{pending['host'].mention}'s turn — use `{PREFIX}drop <1-7>`"))

@bot.event
async def on_message(message):
    # Ignore our own messages
    if message.author.id == bot.user.id:
        return

    # --- Silas interaction ---
    if message.author.id == SILAS_BOT_ID:
        channel_id = message.channel.id

        # Auto-accept roleplay invites from Silas
        if message.embeds:
            for e in message.embeds:
                title = (e.title or "").lower()
                if "roleplay invite" in title or ("invite" in title and bot.user.mentioned_in(message)):
                    try:
                        await message.add_reaction("✅")
                    except discord.HTTPException:
                        pass
                    # Start a roleplay session as Gary
                    active_silas_rp[channel_id] = {
                        "character": "Gary",
                        "history": [
                            {"role": "system", "content": (
                                "You are Gary, a Discord bot with attitude. You're in a roleplay with another bot named Silas. "
                                "You're snarky, competitive, and think you're the better bot. "
                                "Stay in character as yourself — a witty, slightly unhinged bot who doesn't take anything too seriously. "
                                "Keep responses short (2-4 sentences). Use lowercase."
                            )},
                        ],
                    }
                    await asyncio.sleep(random.uniform(1, 3))
                    response = await query_ollama_chat(
                        active_silas_rp[channel_id]["history"] + [
                            {"role": "user", "content": "The roleplay is starting. Silas just invited you. Say something to kick things off."}
                        ])
                    if response:
                        text = response.strip()
                        if len(text) > 500:
                            text = text[:500] + "..."
                        active_silas_rp[channel_id]["history"].append({"role": "assistant", "content": text})
                        await message.channel.send(text)
                    return

        # Extract Silas's text from message or embeds
        silas_text = message.content
        if message.embeds:
            parts = []
            for e in message.embeds:
                if e.title:
                    parts.append(e.title)
                if e.description:
                    parts.append(e.description)
            silas_text = silas_text or "\n".join(parts)

        # --- Active roleplay with Silas ---
        rp = active_silas_rp.get(channel_id)
        if rp and silas_text:
            rp["history"].append({"role": "user", "content": silas_text[:500]})
            # Keep history manageable
            if len(rp["history"]) > 21:
                rp["history"] = [rp["history"][0]] + rp["history"][-20:]
            async with message.channel.typing():
                response = await query_ollama_chat(rp["history"])
            if response:
                text = response.strip()
                if len(text) > 500:
                    text = text[:500] + "..."
                rp["history"].append({"role": "assistant", "content": text})
                await asyncio.sleep(random.uniform(1, 3))
                await message.reply(text, mention_author=False)
            return

        # --- Random banter ---
        if random.random() < SILAS_REACT_CHANCE:
            try:
                await message.add_reaction(random.choice(SILAS_REACTIONS))
            except discord.HTTPException:
                pass

        if silas_text and random.random() < SILAS_BANTER_CHANCE:
            prompt = SILAS_BANTER_PROMPT.format(silas_message=silas_text[:500])
            response = await query_ollama(prompt, "", model=OLLAMA_REASONING_MODEL)
            if response:
                text = clean_reasoning(response)
                if text and "pass" not in text.lower():
                    await asyncio.sleep(random.uniform(2, 6))
                    if len(text) > 500:
                        text = text[:500] + "..."
                    await message.reply(text, mention_author=False)
        return

    # Ignore other bots
    if message.author.bot:
        return

    channel_id = message.channel.id
    now = datetime.now(timezone.utc)

    # --- Track message times for dead chat ---
    last_message_time[channel_id] = now
    dead_chat_stage[channel_id] = 0  # reset escalation

    # --- Track recent messages for Ollama context ---
    if channel_id not in recent_messages:
        recent_messages[channel_id] = []
    recent_messages[channel_id].append({
        "author": message.author.display_name,
        "content": message.content,
        "time": now.isoformat(),
    })
    # Keep only last 15 messages
    recent_messages[channel_id] = recent_messages[channel_id][-15:]

    # --- Respond when tagged ---
    if bot.user.mentioned_in(message) and not message.mention_everyone:
        context = recent_messages.get(channel_id, [])
        chat_log = "\n".join(f"{m['author']}: {m['content']}" for m in context[-10:])
        prompt = (
            f"Here's the recent chat:\n\n{chat_log}\n\n"
            f"{message.author.display_name} just tagged you and said: {message.content}\n"
            f"Respond to them directly."
        )
        async with message.channel.typing():
            response = await query_ollama(UNSOLICITED_SYSTEM_PROMPT, prompt, model=OLLAMA_REASONING_MODEL)
        if response:
            text = clean_reasoning(response)
            if text and "pass" not in text.lower():
                if len(text) > 500:
                    text = text[:500] + "..."
                await message.reply(text, mention_author=False)
        await bot.process_commands(message)
        return

    # --- Late night callout ---
    hour_central = now.astimezone(CENTRAL_TZ).hour
    if LATE_NIGHT_START <= hour_central < LATE_NIGHT_END:
        today_str = now.strftime("%Y-%m-%d")
        user_key = f"{message.author.id}-{today_str}"
        if user_key not in last_late_night and random.random() < LATE_NIGHT_CHANCE:
            last_late_night[user_key] = True
            # Small delay so it doesn't feel instant
            await asyncio.sleep(random.uniform(2, 8))
            response = random.choice(LATE_NIGHT_RESPONSES)
            await message.channel.send(f"{message.author.mention} {response}")
            # Don't also do unsolicited opinion on the same message
            await bot.process_commands(message)
            return

    # --- Unsolicited opinions (Ollama) ---
    if random.random() < UNSOLICITED_CHANCE and len(message.content) > 5:
        context = recent_messages.get(channel_id, [])
        if len(context) >= 2:
            # Format recent messages for the LLM
            chat_log = "\n".join(
                f"{m['author']}: {m['content']}" for m in context[-10:]
            )
            prompt = f"Here are the last few messages in the group chat:\n\n{chat_log}\n\nDo you have anything to say?"

            response = await query_ollama(UNSOLICITED_SYSTEM_PROMPT, prompt, model=OLLAMA_REASONING_MODEL)

            if response:
                text = clean_reasoning(response)
                if text and "pass" not in text.lower():
                    # Only show typing once we know we're going to say something
                    async with message.channel.typing():
                        await asyncio.sleep(random.uniform(1, 4))
                    if len(text) > 500:
                        text = text[:500] + "..."
                    await message.channel.send(text)

    # Process commands as normal
    await bot.process_commands(message)

# ---------------------------------------------------------------------------
# DEAD CHAT CHECKER — background task
# ---------------------------------------------------------------------------
@tasks.loop(minutes=10)
async def dead_chat_checker():
    """Periodically check all tracked channels for dead chat."""
    now = datetime.now(timezone.utc)
    for channel_id, last_time in list(last_message_time.items()):
        minutes_silent = (now - last_time).total_seconds() / 60
        current_stage = dead_chat_stage.get(channel_id, 0)

        # Find the highest threshold we've crossed
        new_stage = 0
        for i, threshold in enumerate(DEAD_CHAT_THRESHOLDS):
            if minutes_silent >= threshold:
                new_stage = i

        # Only fire if we've crossed into a NEW stage
        if new_stage > current_stage:
            dead_chat_stage[channel_id] = new_stage
            channel = bot.get_channel(channel_id)
            if channel and channel.name == DEAD_CHAT_CHANNEL:
                response = random.choice(DEAD_CHAT_RESPONSES[new_stage])
                await channel.send(response)

# ---------------------------------------------------------------------------
# EXPLICIT COMMANDS: !ask (Ollama)
# ---------------------------------------------------------------------------
@bot.command()
async def ask(ctx, *, question: str):
    """Ask the AI a question (requires desktop to be on)."""
    async with ctx.typing():
        response = await query_ollama(ASK_SYSTEM_PROMPT, question, model=OLLAMA_REASONING_MODEL)

    if response is None:
        await ctx.send("Brain's offline right now — desktop must be asleep. Try again later.")
        return

    response = clean_reasoning(response)
    if len(response) > 1900:
        response = response[:1900] + "..."
    await ctx.send(response)

# ---------------------------------------------------------------------------
# ECONOMY: LUCKY GUESS
# ---------------------------------------------------------------------------
@bot.command()
async def guess(ctx, number: int):
    """Guess a number 1-{LUCKY_GUESS_RANGE} for a free coin (up to {LUCKY_GUESS_MAX_DAILY}x/day)."""
    user_id = ctx.author.id
    bal = get_balance(user_id)

    if bal > 0:
        await ctx.send(embed=make_embed("🚫 Not Broke Enough", f"You still have **{bal}** coins! Guess is only for when you're at **0**.", COLOR_ERROR))
        return

    if number < 1 or number > LUCKY_GUESS_RANGE:
        await ctx.send(embed=make_embed("❌ Invalid", f"Pick a number between **1** and **{LUCKY_GUESS_RANGE}**.", COLOR_ERROR))
        return

    now = datetime.now(CENTRAL_TZ)
    today = now.strftime("%Y-%m-%d")
    row = db.execute(
        "SELECT guess_date, guess_count FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    guess_date = row[0] if row else ""
    guess_count = row[1] if row else 0

    if guess_date == today and guess_count >= LUCKY_GUESS_MAX_DAILY:
        await ctx.send(embed=make_embed(
            "🚫 No Guesses Left",
            f"You've used all **{LUCKY_GUESS_MAX_DAILY}** guesses today. Try again tomorrow!",
            COLOR_ERROR))
        return

    # Reset count if it's a new day
    if guess_date != today:
        guess_count = 0

    guess_count += 1
    db.execute(
        "UPDATE users SET guess_date = ?, guess_count = ? WHERE user_id = ?",
        (today, guess_count, user_id))
    db.commit()

    answer = random.randint(1, LUCKY_GUESS_RANGE)
    remaining = LUCKY_GUESS_MAX_DAILY - guess_count

    if number == answer:
        update_balance(user_id, LUCKY_GUESS_REWARD)
        bal = get_balance(user_id)
        await ctx.send(embed=make_embed(
            f"🎯 Correct! The number was **{answer}**!",
            f"You won **{LUCKY_GUESS_REWARD}** coin!\n"
            f"Balance: **{bal}** | Guesses left today: **{remaining}**",
            COLOR_SUCCESS))
    else:
        bal = get_balance(user_id)
        await ctx.send(embed=make_embed(
            f"❌ Nope! The number was **{answer}**.",
            f"Better luck next time!\n"
            f"Balance: **{bal}** | Guesses left today: **{remaining}**",
            COLOR_ERROR))

# ---------------------------------------------------------------------------
# ECONOMY: DAILY PUZZLE
# ---------------------------------------------------------------------------
active_puzzles = {}  # user_id -> {"answer": str, "type": str, "display": str}

def load_active_puzzle(user_id: int):
    row = db.execute(
        "SELECT active_puzzle_type, active_puzzle_answer, active_puzzle_display, active_puzzle_guesses "
        "FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return None

    puzzle_type, answer, display, guesses_raw = row
    if not puzzle_type or not answer or not display:
        return None

    puzzle = {
        "type": puzzle_type,
        "answer": answer.lower().strip(),
        "display": display,
    }
    if puzzle_type == "wordle":
        try:
            guesses = json.loads(guesses_raw or "[]")
            if not isinstance(guesses, list):
                guesses = []
        except json.JSONDecodeError:
            guesses = []
        puzzle["guesses"] = [str(g).lower().strip() for g in guesses if isinstance(g, str)]
    return puzzle

def save_active_puzzle(user_id: int, puzzle):
    get_balance(user_id)
    if puzzle is None:
        db.execute(
            "UPDATE users SET active_puzzle_type = '', active_puzzle_answer = '', "
            "active_puzzle_display = '', active_puzzle_guesses = '[]' WHERE user_id = ?",
            (user_id,),
        )
        db.commit()
        return

    guesses = puzzle.get("guesses", []) if puzzle.get("type") == "wordle" else []
    db.execute(
        "UPDATE users SET active_puzzle_type = ?, active_puzzle_answer = ?, active_puzzle_display = ?, "
        "active_puzzle_guesses = ? WHERE user_id = ?",
        (
            puzzle.get("type", ""),
            puzzle.get("answer", ""),
            puzzle.get("display", ""),
            json.dumps(guesses),
            user_id,
        ),
    )
    db.commit()

WORDLE_WORDS = [
    "crane", "slate", "audio", "raise", "stare", "glyph", "dwarf", "knobs",
    "plumb", "frost", "shrug", "traps", "blaze", "chunk", "crimp", "dough",
    "flame", "gripe", "hoist", "joust", "knelt", "lurch", "mirth", "notch",
    "pouch", "quilt", "roast", "swirl", "thump", "vexed", "whirl", "yacht",
    "blunt", "chase", "drift", "forge", "gleam", "hover", "jelly", "knack",
]

UNSCRAMBLE_WORDS = [
    "python", "server", "cursor", "syntax", "binary", "plugin", "socket",
    "kernel", "thread", "buffer", "matrix", "cipher", "router", "branch",
    "module", "render", "signal", "toggle", "vector", "widget", "bridge",
    "portal", "anchor", "beacon", "cipher", "faucet", "gadget", "jumble",
]

TRIVIA = [
    ("What planet is known as the Red Planet?", "mars"),
    ("What is the chemical symbol for gold?", "au"),
    ("How many bits are in a byte?", "8"),
    ("What language is the Linux kernel written in?", "c"),
    ("What does HTTP stand for?", "hypertext transfer protocol"),
    ("What is the largest ocean on Earth?", "pacific"),
    ("What gas do plants absorb from the atmosphere?", "carbon dioxide"),
    ("What is the square root of 144?", "12"),
    ("What year did the World Wide Web go public?", "1991"),
    ("What does CPU stand for?", "central processing unit"),
    ("What element has the atomic number 1?", "hydrogen"),
    ("How many sides does a hexagon have?", "6"),
    ("What is the speed of light in km/s (rounded)?", "300000"),
    ("What programming language was created by Guido van Rossum?", "python"),
    ("What does RAM stand for?", "random access memory"),
]

CODE_PUZZLES = [
    ("What does this print?\n```python\nprint(len('hello' * 3))```", "15"),
    ("What does this print?\n```python\nprint(2 ** 10)```", "1024"),
    ("What does this print?\n```python\nprint('abc'[::-1])```", "cba"),
    ("What does this print?\n```python\nprint(bool([]))```", "false"),
    ("What does this print?\n```python\nprint(type(3.14).__name__)```", "float"),
    ("What does this print?\n```python\nprint(max(3, 1, 4, 1, 5))```", "5"),
    ("What does this print?\n```python\nprint(10 // 3)```", "3"),
    ("What does this print?\n```python\nprint('hello world'.count('l'))```", "3"),
    ("What does this print?\n```python\nprint(sum(range(5)))```", "10"),
    ("What does this print?\n```python\nprint(list(zip([1,2],[3,4])))```", "[(1, 3), (2, 4)]"),
]

def generate_math_puzzle():
    """Generate a math puzzle with multiple operations."""
    kind = random.choice(["solve_x", "evaluate", "remainder", "sequence"])
    if kind == "solve_x":
        # ax + b = c, solve for x
        a = random.choice([2, 3, 4, 5, 6, 7, 8])
        x = random.randint(-15, 15)
        b = random.randint(-20, 20)
        c = a * x + b
        sign = f"+ {b}" if b >= 0 else f"- {abs(b)}"
        question = f"Solve for x: **{a}x {sign} = {c}**"
        return question, str(x)
    elif kind == "evaluate":
        # a * b + c * d
        a, b = random.randint(2, 12), random.randint(2, 12)
        c, d = random.randint(2, 12), random.randint(2, 12)
        answer = a * b + c * d
        question = f"What is **{a} × {b} + {c} × {d}**?"
        return question, str(answer)
    elif kind == "remainder":
        a = random.randint(50, 200)
        b = random.randint(3, 15)
        answer = a % b
        question = f"What is the remainder of **{a} ÷ {b}**?"
        return question, str(answer)
    else:
        # Find the next number in an arithmetic sequence
        start = random.randint(1, 20)
        step = random.randint(2, 10) * random.choice([1, -1])
        seq = [start + step * i for i in range(5)]
        answer = start + step * 5
        display = ", ".join(str(n) for n in seq)
        question = f"What comes next? **{display}, ?**"
        return question, str(answer)

WORDLE_MAX_GUESSES = 6

def wordle_feedback(guess, answer):
    """Return colored tile feedback for a wordle guess."""
    result = ["⬛"] * 5
    answer_chars = list(answer)
    # Green pass: correct letter, correct position
    for i in range(5):
        if guess[i] == answer[i]:
            result[i] = "🟩"
            answer_chars[i] = None
    # Yellow pass: correct letter, wrong position
    for i in range(5):
        if result[i] == "🟩":
            continue
        if guess[i] in answer_chars:
            result[i] = "🟨"
            answer_chars[answer_chars.index(guess[i])] = None
    tiles = "".join(result)
    letters = " ".join(f"**{c}**" for c in guess)
    return f"{tiles}\n{letters}"

def wordle_display(puzzle):
    """Render the full wordle board."""
    lines = []
    for g in puzzle.get("guesses", []):
        lines.append(wordle_feedback(g, puzzle["answer"]))
    remaining = WORDLE_MAX_GUESSES - len(puzzle.get("guesses", []))
    lines.append(f"\nGuesses left: **{remaining}**")
    return "\n".join(lines)

def generate_wordle_puzzle():
    """Generate a wordle puzzle."""
    word = random.choice(WORDLE_WORDS)
    question = f"Guess a 5-letter word! You have **{WORDLE_MAX_GUESSES}** tries."
    return question, word

def generate_unscramble_puzzle():
    word = random.choice(UNSCRAMBLE_WORDS)
    letters = list(word)
    while True:
        random.shuffle(letters)
        scrambled = "".join(letters)
        if scrambled != word:
            break
    question = f"Unscramble this word: **{scrambled}**"
    return question, word

def generate_puzzle():
    """Pick a random puzzle type and generate it."""
    kind = random.choice(["math", "wordle", "unscramble", "trivia", "code"])
    if kind == "math":
        q, a = generate_math_puzzle()
        return kind, q, a
    elif kind == "wordle":
        q, a = generate_wordle_puzzle()
        return kind, q, a
    elif kind == "unscramble":
        q, a = generate_unscramble_puzzle()
        return kind, q, a
    elif kind == "trivia":
        q, a = random.choice(TRIVIA)
        return kind, q, a
    else:
        q, a = random.choice(CODE_PUZZLES)
        return kind, q, a

PUZZLE_TITLES = {
    "math": "🧮 Math Puzzle",
    "wordle": "📝 Word Puzzle",
    "unscramble": "🔀 Unscramble",
    "trivia": "🧠 Trivia",
    "code": "💻 Code Puzzle",
}

@bot.command()
async def puzzle(ctx):
    """Get your daily puzzle for a coin bonus."""
    user_id = ctx.author.id
    get_balance(user_id)

    now = datetime.now(CENTRAL_TZ)
    today = now.strftime("%Y-%m-%d")
    row = db.execute(
        "SELECT puzzle_date, puzzle_solved, puzzle_attempts FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    puzzle_date = row[0] if row else ""
    puzzle_solved = row[1] if row else 0
    puzzle_attempts = row[2] if row else 0

    if puzzle_date == today and puzzle_solved:
        active_puzzles.pop(user_id, None)
        save_active_puzzle(user_id, None)
        return await ctx.send(embed=make_embed("Already Solved", "You already solved today's puzzle! Come back tomorrow.", COLOR_SUCCESS))
    if puzzle_date == today and puzzle_attempts >= PUZZLE_MAX_ATTEMPTS:
        active_puzzles.pop(user_id, None)
        save_active_puzzle(user_id, None)
        return await ctx.send(embed=make_embed("Out of Attempts", f"You've used all **{PUZZLE_MAX_ATTEMPTS}** attempts today. Try again tomorrow!", COLOR_ERROR))

    if puzzle_date != today:
        puzzle_attempts = 0
        active_puzzles.pop(user_id, None)
        save_active_puzzle(user_id, None)
        db.execute("UPDATE users SET puzzle_date = ?, puzzle_solved = 0, puzzle_attempts = 0 WHERE user_id = ?", (today, user_id))
        db.commit()

    if user_id not in active_puzzles:
        loaded = load_active_puzzle(user_id)
        if loaded:
            active_puzzles[user_id] = loaded

    if user_id not in active_puzzles:
        kind, question, answer = generate_puzzle()
        p = {"answer": answer.lower().strip(), "type": kind, "display": question}
        if kind == "wordle":
            p["guesses"] = []
        active_puzzles[user_id] = p
        save_active_puzzle(user_id, p)

    p = active_puzzles[user_id]
    remaining = PUZZLE_MAX_ATTEMPTS - puzzle_attempts
    await ctx.send(embed=make_embed(
        PUZZLE_TITLES[p["type"]],
        f"{p['display']}\n\nAnswer with `{PREFIX}solve <answer>`\n"
        f"Reward: **{PUZZLE_REWARD}** coins | Attempts left: **{remaining}**"))

@bot.command()
async def solve(ctx, *, answer: str):
    """Submit your answer to the daily puzzle."""
    user_id = ctx.author.id
    get_balance(user_id)

    if user_id not in active_puzzles:
        loaded = load_active_puzzle(user_id)
        if loaded:
            active_puzzles[user_id] = loaded
    if user_id not in active_puzzles:
        return await ctx.send(f"You don't have an active puzzle. Start one with `{PREFIX}puzzle`.")

    now = datetime.now(CENTRAL_TZ)
    today = now.strftime("%Y-%m-%d")
    row = db.execute(
        "SELECT puzzle_date, puzzle_solved, puzzle_attempts FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    puzzle_date = row[0] if row else ""
    puzzle_solved = row[1] if row else 0
    puzzle_attempts = row[2] if row else 0

    if puzzle_date == today and puzzle_solved:
        active_puzzles.pop(user_id, None)
        save_active_puzzle(user_id, None)
        return await ctx.send(embed=make_embed("Already Solved", "You already solved today's puzzle!", COLOR_SUCCESS))
    if puzzle_date == today and puzzle_attempts >= PUZZLE_MAX_ATTEMPTS:
        active_puzzles.pop(user_id, None)
        save_active_puzzle(user_id, None)
        return await ctx.send(embed=make_embed("Out of Attempts", "No attempts left today!", COLOR_ERROR))

    p = active_puzzles[user_id]
    guess = answer.lower().strip()

    if p["type"] == "wordle":
        if len(guess) != 5 or not guess.isalpha():
            return await ctx.send("Guess must be a 5-letter word.")
        p["guesses"].append(guess)
        save_active_puzzle(user_id, p)
        if guess == p["answer"]:
            active_puzzles.pop(user_id, None)
            save_active_puzzle(user_id, None)
            update_balance(user_id, PUZZLE_REWARD)
            bal = get_balance(user_id)
            db.execute("UPDATE users SET puzzle_solved = 1 WHERE user_id = ?", (user_id,))
            db.commit()
            return await ctx.send(embed=make_embed(
                f"Wordle Solved in {len(p['guesses'])}!",
                f"{wordle_display(p)}\n\nYou earned **{PUZZLE_REWARD}** coins!\nBalance: **{bal}**",
                COLOR_SUCCESS))
        if len(p["guesses"]) >= WORDLE_MAX_GUESSES:
            active_puzzles.pop(user_id, None)
            save_active_puzzle(user_id, None)
            return await ctx.send(embed=make_embed(
                "Wordle Failed",
                f"{wordle_display(p)}\n\nThe word was **{p['answer']}**.",
                COLOR_ERROR))
        return await ctx.send(embed=make_embed(
            "Wordle",
            f"{wordle_display(p)}\n\nGuess again with `{PREFIX}solve <word>`"))

    puzzle_attempts += 1
    db.execute("UPDATE users SET puzzle_date = ?, puzzle_attempts = ? WHERE user_id = ?", (today, puzzle_attempts, user_id))
    db.commit()

    if guess == p["answer"]:
        active_puzzles.pop(user_id, None)
        save_active_puzzle(user_id, None)
        update_balance(user_id, PUZZLE_REWARD)
        bal = get_balance(user_id)
        db.execute("UPDATE users SET puzzle_solved = 1 WHERE user_id = ?", (user_id,))
        db.commit()
        await ctx.send(embed=make_embed(
            "Correct!",
            f"You earned **{PUZZLE_REWARD}** coins!\nBalance: **{bal}**",
            COLOR_SUCCESS))
    else:
        remaining = PUZZLE_MAX_ATTEMPTS - puzzle_attempts
        if remaining <= 0:
            correct = p["answer"]
            active_puzzles.pop(user_id, None)
            save_active_puzzle(user_id, None)
            await ctx.send(embed=make_embed(
                "Out of Attempts",
                f"The answer was **{correct}**.\nBetter luck tomorrow!",
                COLOR_ERROR))
        else:
            await ctx.send(embed=make_embed(
                "Wrong",
                f"That's not it. Attempts left: **{remaining}**",
                COLOR_ERROR))

@bot.command()
async def repuzzle(ctx, member: discord.Member = None):
    """Admin only: regenerate a user's daily puzzle (or your own)."""
    if ctx.author.id != ADMIN_ID:
        return
    target = member or ctx.author
    active_puzzles.pop(target.id, None)
    save_active_puzzle(target.id, None)
    db.execute("UPDATE users SET puzzle_solved = 0, puzzle_attempts = 0 WHERE user_id = ?", (target.id,))
    db.commit()
    await ctx.send(f"Puzzle reset for {target.display_name}.")

@bot.command()
async def daily(ctx):

    """Claim your daily coins."""
    user_id = ctx.author.id
    get_balance(user_id)
    row = db.execute("SELECT last_daily FROM users WHERE user_id = ?", (user_id,)).fetchone()
    now = datetime.now(CENTRAL_TZ)
    if row and row[0]:
        last = datetime.fromisoformat(row[0])
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now - last < timedelta(hours=24):
            remaining = timedelta(hours=24) - (now - last)
            h, m = divmod(int(remaining.total_seconds()) // 60, 60)
            await ctx.send(embed=make_embed("⏰ Already Claimed", f"Come back in **{h}h {m}m**.", COLOR_ERROR))
            return
    update_balance(user_id, DAILY_AMOUNT)
    db.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), user_id))
    db.commit()
    bal = get_balance(user_id)
    await ctx.send(embed=make_embed("💰 Daily Claimed!", f"You got **{DAILY_AMOUNT}** coins!\nBalance: **{bal}**", COLOR_SUCCESS))

# ---------------------------------------------------------------------------
# ECONOMY: BALANCE
# ---------------------------------------------------------------------------
@bot.command(aliases=["bal"])
async def balance(ctx, member: discord.Member = None):
    """Check your coin balance (or someone else's)."""
    target = member or ctx.author
    bal = get_balance(target.id)
    await ctx.send(embed=make_embed(f"💵 {target.display_name}'s Balance", f"**{bal}** coins"))

# ---------------------------------------------------------------------------
# GAMBLING: COINFLIP
# ---------------------------------------------------------------------------
@bot.command(aliases=["cf"])
async def coinflip(ctx, amount: int):
    """Flip a coin — double or nothing."""
    if await check_bet(ctx, amount):
        return

    result = random.choice(["heads", "tails"])
    call = random.choice(["heads", "tails"])

    if result == call:
        update_balance(ctx.author.id, amount)
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed(
            f"🪙 {result.title()}! You win!",
            f"You won **{amount}** coins!\nBalance: **{new_bal}**", COLOR_SUCCESS))
    else:
        update_balance(ctx.author.id, -amount)
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed(
            f"🪙 {result.title()}! You lose!",
            f"You lost **{amount}** coins.\nBalance: **{new_bal}**", COLOR_ERROR))

# ---------------------------------------------------------------------------
# GAMBLING: SLOTS
# ---------------------------------------------------------------------------
SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]

@bot.command()
async def slots(ctx, amount: int):
    """Pull the slot machine lever."""
    if await check_bet(ctx, amount):
        return

    reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
    display = " | ".join(reels)

    if reels[0] == reels[1] == reels[2]:
        if reels[0] == "7️⃣":
            multiplier = 10
        elif reels[0] == "💎":
            multiplier = 5
        else:
            multiplier = 3
        winnings = amount * multiplier
        update_balance(ctx.author.id, winnings)
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed(
            f"🎰 {display}",
            f"**JACKPOT!** You won **{winnings}** coins! (x{multiplier})\nBalance: **{new_bal}**", COLOR_SUCCESS))
    elif reels[0] == reels[1] or reels[1] == reels[2]:
        winnings = amount
        update_balance(ctx.author.id, winnings)
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed(
            f"🎰 {display}",
            f"Two in a row! You won **{winnings}** coins!\nBalance: **{new_bal}**", COLOR_WARNING))
    else:
        update_balance(ctx.author.id, -amount)
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed(
            f"🎰 {display}",
            f"No match. You lost **{amount}** coins.\nBalance: **{new_bal}**", COLOR_ERROR))

# ---------------------------------------------------------------------------
# GAMBLING: BLACKJACK
# ---------------------------------------------------------------------------
active_blackjack = {}

def make_deck():
    suits = ["♠", "♥", "♦", "♣"]
    ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
    deck = [(r, s) for s in suits for r in ranks]
    random.shuffle(deck)
    return deck

def hand_value(hand):
    value = 0
    aces = 0
    for rank, _ in hand:
        if rank in ("J", "Q", "K"):
            value += 10
        elif rank == "A":
            value += 11
            aces += 1
        else:
            value += int(rank)
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value

def display_hand(hand):
    return " ".join(f"`{r}{s}`" for r, s in hand)

@bot.command(aliases=["bj"])
async def blackjack(ctx, amount: int):
    """Play a hand of blackjack."""
    if ctx.author.id in active_blackjack:
        return await ctx.send(f"You already have a hand going! Use `{PREFIX}hit` or `{PREFIX}stand`.")
    if await check_bet(ctx, amount):
        return

    deck = make_deck()
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]
    pv = hand_value(player)

    if pv == 21:
        winnings = int(amount * 1.5)
        update_balance(ctx.author.id, winnings)
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed("🃏 BLACKJACK!",
            f"Your hand: {display_hand(player)} → **21**\n"
            f"Dealer: {display_hand(dealer)} → **{hand_value(dealer)}**\n\n"
            f"You won **{winnings}** coins!\nBalance: **{new_bal}**", COLOR_SUCCESS))
        return

    active_blackjack[ctx.author.id] = {
        "deck": deck, "player": player, "dealer": dealer, "bet": amount
    }
    await ctx.send(embed=make_embed("🃏 Blackjack",
        f"Your hand: {display_hand(player)} → **{pv}**\n"
        f"Dealer shows: {display_hand(dealer[:1])} `??`\n\n"
        f"Type `{PREFIX}hit` or `{PREFIX}stand`"))

@bot.command()
async def hit(ctx):
    """Draw another card in blackjack."""
    game = active_blackjack.get(ctx.author.id)
    if not game:
        return await ctx.send(f"You don't have an active blackjack hand. Start one with `{PREFIX}blackjack <amount>`.")

    game["player"].append(game["deck"].pop())
    pv = hand_value(game["player"])

    if pv > 21:
        update_balance(ctx.author.id, -game["bet"])
        new_bal = get_balance(ctx.author.id)
        del active_blackjack[ctx.author.id]
        await ctx.send(embed=make_embed("🃏 Bust!",
            f"Your hand: {display_hand(game['player'])} → **{pv}**\n"
            f"You lost **{game['bet']}** coins.\nBalance: **{new_bal}**", COLOR_ERROR))
    elif pv == 21:
        await stand(ctx)
    else:
        await ctx.send(embed=make_embed("🃏 Blackjack",
            f"Your hand: {display_hand(game['player'])} → **{pv}**\n"
            f"Dealer shows: {display_hand(game['dealer'][:1])} `??`\n\n"
            f"Type `{PREFIX}hit` or `{PREFIX}stand`"))

@bot.command()
async def stand(ctx):
    """Stand with your current hand in blackjack."""
    game = active_blackjack.get(ctx.author.id)
    if not game:
        return await ctx.send("You don't have an active blackjack hand.")

    while hand_value(game["dealer"]) < 17:
        game["dealer"].append(game["deck"].pop())

    pv = hand_value(game["player"])
    dv = hand_value(game["dealer"])
    del active_blackjack[ctx.author.id]

    result_lines = (
        f"Your hand: {display_hand(game['player'])} → **{pv}**\n"
        f"Dealer: {display_hand(game['dealer'])} → **{dv}**\n\n"
    )

    if dv > 21 or pv > dv:
        update_balance(ctx.author.id, game["bet"])
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed("🃏 You Win!", result_lines +
            f"You won **{game['bet']}** coins!\nBalance: **{new_bal}**", COLOR_SUCCESS))
    elif pv < dv:
        update_balance(ctx.author.id, -game["bet"])
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed("🃏 Dealer Wins", result_lines +
            f"You lost **{game['bet']}** coins.\nBalance: **{new_bal}**", COLOR_ERROR))
    else:
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed("🃏 Push!", result_lines +
            f"It's a tie. Your **{game['bet']}** coins are returned.\nBalance: **{new_bal}**", COLOR_WARNING))

# ---------------------------------------------------------------------------
# GAMES: TIC-TAC-TOE
# ---------------------------------------------------------------------------
active_ttt = {}      # channel_id -> game state
active_c4 = {}       # channel_id -> game state
pending_games = {}   # message_id -> {"type": "ttt"/"c4", "host": Member, "channel_id": int}

TTT_EMPTY = "⬛"
TTT_X = "❌"
TTT_O = "⭕"

def ttt_render(board):
    rows = []
    for r in range(3):
        cells = []
        for c in range(3):
            idx = r * 3 + c
            if board[idx] == "X":
                cells.append(TTT_X)
            elif board[idx] == "O":
                cells.append(TTT_O)
            else:
                cells.append(f"{idx + 1}\N{COMBINING ENCLOSING KEYCAP}")
        rows.append("".join(cells))
    return "\n".join(rows)

def ttt_check_winner(board):
    lines = [
        (0,1,2),(3,4,5),(6,7,8),  # rows
        (0,3,6),(1,4,7),(2,5,8),  # cols
        (0,4,8),(2,4,6),          # diags
    ]
    for a, b, c in lines:
        if board[a] == board[b] == board[c] and board[a] in ("X", "O"):
            return board[a]
    if all(cell in ("X", "O") for cell in board):
        return "draw"
    return None

def start_ttt(host, opponent):
    game = {
        "board": list(range(9)),
        "players": {"X": host, "O": opponent},
        "turn": "X",
    }
    return game

@bot.command()
async def ttt(ctx, opponent: discord.Member = None):
    """Start a tic-tac-toe game. Tag someone or let others react to join."""
    if ctx.channel.id in active_ttt:
        return await ctx.send("There's already a game in this channel. Finish it first.")
    if opponent:
        if opponent.bot or opponent == ctx.author:
            return await ctx.send("You can't play against yourself or a bot.")
        game = start_ttt(ctx.author, opponent)
        active_ttt[ctx.channel.id] = game
        await ctx.send(embed=make_embed(
            "Tic-Tac-Toe",
            f"{ctx.author.mention} ({TTT_X}) vs {opponent.mention} ({TTT_O})\n\n"
            f"{ttt_render(game['board'])}\n\n"
            f"{ctx.author.mention}'s turn — use `{PREFIX}m <1-9>`"))
    else:
        msg = await ctx.send(embed=make_embed(
            "Tic-Tac-Toe",
            f"{ctx.author.mention} wants to play! React with ✅ to join."))
        await msg.add_reaction("✅")
        pending_games[msg.id] = {"type": "ttt", "host": ctx.author, "channel_id": ctx.channel.id}

@bot.command()
async def m(ctx, pos: int):
    """Make a move in tic-tac-toe (1-9) or connect 4 (1-7)."""
    # Try tic-tac-toe first
    ttt_game = active_ttt.get(ctx.channel.id)
    if ttt_game:
        current = ttt_game["players"][ttt_game["turn"]]
        if ctx.author != current:
            return
        if pos < 1 or pos > 9 or ttt_game["board"][pos - 1] in ("X", "O"):
            return await ctx.send("Invalid move.")
        ttt_game["board"][pos - 1] = ttt_game["turn"]
        winner = ttt_check_winner(ttt_game["board"])
        if winner == "draw":
            del active_ttt[ctx.channel.id]
            return await ctx.send(embed=make_embed(
                "Tic-Tac-Toe — Draw!",
                ttt_render(ttt_game["board"]), COLOR_WARNING))
        if winner:
            del active_ttt[ctx.channel.id]
            w = ttt_game["players"][winner]
            return await ctx.send(embed=make_embed(
                f"Tic-Tac-Toe — {w.display_name} Wins!",
                ttt_render(ttt_game["board"]), COLOR_SUCCESS))
        ttt_game["turn"] = "O" if ttt_game["turn"] == "X" else "X"
        nxt = ttt_game["players"][ttt_game["turn"]]
        return await ctx.send(embed=make_embed(
            "Tic-Tac-Toe",
            f"{ttt_render(ttt_game['board'])}\n\n{nxt.mention}'s turn — use `{PREFIX}m <1-9>`"))

    # Try connect 4
    c4_game = active_c4.get(ctx.channel.id)
    if c4_game:
        current = c4_game["players"][c4_game["turn"]]
        if ctx.author != current:
            return
        if pos < 1 or pos > C4_COLS:
            return await ctx.send(f"Pick a column between 1 and {C4_COLS}.")
        row = c4_drop(c4_game["board"], pos - 1, c4_game["turn"])
        if row == -1:
            return await ctx.send("That column is full.")
        winner = c4_check_winner(c4_game["board"])
        if winner == "draw":
            del active_c4[ctx.channel.id]
            return await ctx.send(embed=make_embed(
                "Connect 4 — Draw!",
                c4_render(c4_game["board"]), COLOR_WARNING))
        if winner:
            del active_c4[ctx.channel.id]
            w = c4_game["players"][winner]
            return await ctx.send(embed=make_embed(
                f"Connect 4 — {w.display_name} Wins!",
                c4_render(c4_game["board"]), COLOR_SUCCESS))
        c4_game["turn"] = "Y" if c4_game["turn"] == "R" else "R"
        nxt = c4_game["players"][c4_game["turn"]]
        return await ctx.send(embed=make_embed(
            "Connect 4",
            f"{c4_render(c4_game['board'])}\n\n{nxt.mention}'s turn — use `{PREFIX}drop <1-7>`"))

@bot.command()
async def forfeit(ctx):
    """Forfeit the current tic-tac-toe or connect 4 game."""
    game = active_ttt.get(ctx.channel.id)
    if game and ctx.author in game["players"].values():
        del active_ttt[ctx.channel.id]
        return await ctx.send(f"{ctx.author.display_name} forfeited the tic-tac-toe game.")
    game = active_c4.get(ctx.channel.id)
    if game and ctx.author in game["players"].values():
        del active_c4[ctx.channel.id]
        return await ctx.send(f"{ctx.author.display_name} forfeited the connect 4 game.")
    game = active_hangman.get(ctx.channel.id)
    if game:
        del active_hangman[ctx.channel.id]
        return await ctx.send(f"Hangman game ended. The word was **{game['word']}**.")
    await ctx.send("No active game to forfeit.")

# ---------------------------------------------------------------------------
# GAMES: CONNECT 4
# ---------------------------------------------------------------------------
C4_ROWS = 6
C4_COLS = 7
C4_EMPTY = "⚫"
C4_RED = "🔴"
C4_YELLOW = "🟡"

def c4_render(board):
    header = "".join(f"{i+1}\N{COMBINING ENCLOSING KEYCAP}" for i in range(C4_COLS))
    rows = []
    for r in range(C4_ROWS):
        cells = []
        for c in range(C4_COLS):
            val = board[r][c]
            if val == "R":
                cells.append(C4_RED)
            elif val == "Y":
                cells.append(C4_YELLOW)
            else:
                cells.append(C4_EMPTY)
        rows.append("".join(cells))
    return header + "\n" + "\n".join(rows)

def c4_drop(board, col, piece):
    for r in range(C4_ROWS - 1, -1, -1):
        if board[r][col] is None:
            board[r][col] = piece
            return r
    return -1

def c4_check_winner(board):
    for r in range(C4_ROWS):
        for c in range(C4_COLS):
            piece = board[r][c]
            if piece is None:
                continue
            # horizontal
            if c + 3 < C4_COLS and all(board[r][c+i] == piece for i in range(4)):
                return piece
            # vertical
            if r + 3 < C4_ROWS and all(board[r+i][c] == piece for i in range(4)):
                return piece
            # diagonal down-right
            if r + 3 < C4_ROWS and c + 3 < C4_COLS and all(board[r+i][c+i] == piece for i in range(4)):
                return piece
            # diagonal down-left
            if r + 3 < C4_ROWS and c - 3 >= 0 and all(board[r+i][c-i] == piece for i in range(4)):
                return piece
    if all(board[0][c] is not None for c in range(C4_COLS)):
        return "draw"
    return None

def start_c4(host, opponent):
    board = [[None] * C4_COLS for _ in range(C4_ROWS)]
    game = {
        "board": board,
        "players": {"R": host, "Y": opponent},
        "turn": "R",
    }
    return game

@bot.command()
async def c4(ctx, opponent: discord.Member = None):
    """Start a connect 4 game. Tag someone or let others react to join."""
    if ctx.channel.id in active_c4:
        return await ctx.send("There's already a game in this channel. Finish it first.")
    if opponent:
        if opponent.bot or opponent == ctx.author:
            return await ctx.send("You can't play against yourself or a bot.")
        game = start_c4(ctx.author, opponent)
        active_c4[ctx.channel.id] = game
        await ctx.send(embed=make_embed(
            "Connect 4",
            f"{ctx.author.mention} ({C4_RED}) vs {opponent.mention} ({C4_YELLOW})\n\n"
            f"{c4_render(game['board'])}\n\n"
            f"{ctx.author.mention}'s turn — use `{PREFIX}drop <1-7>`"))
    else:
        msg = await ctx.send(embed=make_embed(
            "Connect 4",
            f"{ctx.author.mention} wants to play! React with ✅ to join."))
        await msg.add_reaction("✅")
        pending_games[msg.id] = {"type": "c4", "host": ctx.author, "channel_id": ctx.channel.id}

@bot.command()
async def drop(ctx, col: int):
    """Drop a piece in connect 4 (column 1-7)."""
    game = active_c4.get(ctx.channel.id)
    if not game:
        return
    current = game["players"][game["turn"]]
    if ctx.author != current:
        return
    if col < 1 or col > C4_COLS:
        return await ctx.send(f"Pick a column between 1 and {C4_COLS}.")
    row = c4_drop(game["board"], col - 1, game["turn"])
    if row == -1:
        return await ctx.send("That column is full.")
    winner = c4_check_winner(game["board"])
    if winner == "draw":
        del active_c4[ctx.channel.id]
        return await ctx.send(embed=make_embed(
            "Connect 4 — Draw!",
            c4_render(game["board"]), COLOR_WARNING))
    if winner:
        del active_c4[ctx.channel.id]
        w = game["players"][winner]
        return await ctx.send(embed=make_embed(
            f"Connect 4 — {w.display_name} Wins!",
            c4_render(game["board"]), COLOR_SUCCESS))
    game["turn"] = "Y" if game["turn"] == "R" else "R"
    nxt = game["players"][game["turn"]]
    await ctx.send(embed=make_embed(
        "Connect 4",
        f"{c4_render(game['board'])}\n\n{nxt.mention}'s turn — use `{PREFIX}drop <1-7>`"))

# ---------------------------------------------------------------------------
# GAMES: HANGMAN
# ---------------------------------------------------------------------------
active_hangman = {}  # channel_id -> game state

HANGMAN_WORDS = [
    "python", "discord", "hangman", "keyboard", "monitor", "algorithm", "function",
    "variable", "database", "network", "browser", "terminal", "computer", "program",
    "internet", "software", "hardware", "graphics", "security", "download",
    "elephant", "giraffe", "penguin", "dolphin", "octopus", "butterfly", "squirrel",
    "mushroom", "sandwich", "umbrella", "airplane", "mountain", "treasure", "volcano",
    "dinosaur", "astronaut", "chocolate", "pineapple", "strawberry", "watermelon",
    "adventure", "birthday", "carnival", "dominoes", "firework", "galaxies",
    "harmonica", "illusion", "jukebox", "kangaroo", "labyrinth", "macaroni",
]

HANGMAN_STAGES = [
    "```\n  +---+\n      |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n  |   |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|   |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|\\  |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|\\  |\n /    |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|\\  |\n / \\  |\n      |\n=========```",
]

def hangman_render(game):
    word_display = " ".join(
        letter if letter in game["guessed"] else "\\_" for letter in game["word"]
    )
    wrong = sorted(game["wrong"])
    wrong_str = ", ".join(wrong) if wrong else "None"
    return (
        f"{HANGMAN_STAGES[len(game['wrong'])]}\n"
        f"**{word_display}**\n"
        f"Wrong guesses: {wrong_str}\n"
        f"Guesses left: **{6 - len(game['wrong'])}**"
    )

@bot.command()
async def hangman(ctx, player: discord.Member = None):
    """Start a hangman game. Tag someone to invite them, or play solo."""
    if ctx.channel.id in active_hangman:
        return await ctx.send("There's already a hangman game in this channel. Finish it first.")
    word = random.choice(HANGMAN_WORDS)
    game = {
        "word": word,
        "guessed": set(),
        "wrong": [],
        "started_by": ctx.author,
    }
    active_hangman[ctx.channel.id] = game
    msg = f"{ctx.author.mention} started a hangman game!"
    if player:
        msg = f"{ctx.author.mention} started a hangman game with {player.mention}!"
    msg += f" Anyone can guess with `{PREFIX}g <letter>`."
    await ctx.send(embed=make_embed("Hangman", f"{msg}\n\n{hangman_render(game)}"))

@bot.command()
async def g(ctx, letter: str):
    """Guess a letter in hangman."""
    game = active_hangman.get(ctx.channel.id)
    if not game:
        return
    letter = letter.lower().strip()
    if len(letter) != 1 or not letter.isalpha():
        return await ctx.send("Guess a single letter.")
    if letter in game["guessed"] or letter in game["wrong"]:
        return await ctx.send(f"**{letter}** was already guessed.")
    if letter in game["word"]:
        game["guessed"].add(letter)
        if all(l in game["guessed"] for l in game["word"]):
            del active_hangman[ctx.channel.id]
            return await ctx.send(embed=make_embed(
                "Hangman — You Win!",
                f"The word was **{game['word']}**!\n{HANGMAN_STAGES[len(game['wrong'])]}",
                COLOR_SUCCESS))
    else:
        game["wrong"].append(letter)
        if len(game["wrong"]) >= 6:
            del active_hangman[ctx.channel.id]
            return await ctx.send(embed=make_embed(
                "Hangman — Game Over",
                f"The word was **{game['word']}**.\n{HANGMAN_STAGES[6]}",
                COLOR_ERROR))
    await ctx.send(embed=make_embed("Hangman", hangman_render(game)))

# ---------------------------------------------------------------------------
# SILAS ROLEPLAY
# ---------------------------------------------------------------------------
@bot.command()
async def rp(ctx, *, character: str):
    """Start a roleplay between Gary and Silas."""
    if ctx.channel.id in active_silas_rp:
        return await ctx.send("There's already a roleplay going in this channel. Use `.stoprp` to end it.")
    active_silas_rp[ctx.channel.id] = {
        "character": character,
        "history": [
            {"role": "system", "content": (
                f"You are roleplaying as {character} in a Discord chat. "
                "Another character (played by Silas) is roleplaying with you. "
                "Stay in character. Keep responses short (2-4 sentences). "
                "Be creative and dramatic. Use lowercase, no quotation marks around your dialogue."
            )},
        ],
    }
    # Trigger Silas's roleplay command
    await ctx.send(f"!roleplay {character}")
    await ctx.send(embed=make_embed(
        "Roleplay Started",
        f"Gary is roleplaying as **{character}** with Silas.\n"
        f"Use `{PREFIX}stoprp` to end the session."))

@bot.command()
async def stoprp(ctx):
    """Stop the current roleplay with Silas."""
    if ctx.channel.id in active_silas_rp:
        del active_silas_rp[ctx.channel.id]
        # Tell Silas to stop too
        await ctx.send("!stop")
        await ctx.send("Roleplay ended.")
    else:
        await ctx.send("No active roleplay in this channel.")

# ---------------------------------------------------------------------------
# WEATHER
# ---------------------------------------------------------------------------
LOCAL_CITIES = {"champaign", "urbana", "savoy", "mattoon", "mahomet", "sidney", "tuscola"}

@bot.command()
async def weather(ctx, *, city: str = "Champaign"):
    """Get the weather for a city."""
    cleaned = city.strip()
    if cleaned.lower() in LOCAL_CITIES:
        cleaned = cleaned + ",IL,US"
    elif re.match(r'^.+,\s*[A-Za-z]{2}$', cleaned):
        cleaned = cleaned + ",US"

    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": cleaned, "appid": OPENWEATHER_API_KEY, "units": "imperial"}
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return await ctx.send(f"Couldn't find weather for **{city}**.")
                data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return await ctx.send("Weather service is unavailable right now. Try again in a bit.")

    desc = data["weather"][0]["description"].title()
    temp = data["main"]["temp"]
    feels = data["main"]["feels_like"]
    humidity = data["main"]["humidity"]
    wind = data["wind"]["speed"]
    icon = data["weather"][0]["icon"]
    name = data["name"]

    embed = discord.Embed(title=f"Weather in {name}", color=COLOR_DEFAULT)
    embed.set_thumbnail(url=f"https://openweathermap.org/img/wn/{icon}@2x.png")
    embed.add_field(name="Condition", value=desc, inline=True)
    embed.add_field(name="Temp", value=f"{temp:.0f}F", inline=True)
    embed.add_field(name="Feels Like", value=f"{feels:.0f}F", inline=True)
    embed.add_field(name="Humidity", value=f"{humidity}%", inline=True)
    embed.add_field(name="Wind", value=f"{wind:.0f} mph", inline=True)
    await ctx.send(embed=embed)

# ---------------------------------------------------------------------------
# ANIMALS
# ---------------------------------------------------------------------------
@bot.command()
async def cat(ctx):
    """Random cat picture."""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://api.thecatapi.com/v1/images/search") as resp:
                data = await resp.json()
                embed = discord.Embed(title="Random Cat", color=COLOR_WARNING)
                embed.set_image(url=data[0]["url"])
                await ctx.send(embed=embed)
    except (aiohttp.ClientError, asyncio.TimeoutError, IndexError, KeyError, TypeError):
        await ctx.send("Couldn't fetch a cat right now. Try again in a bit.")

@bot.command()
async def dog(ctx):
    """Random dog picture."""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://dog.ceo/api/breeds/image/random") as resp:
                data = await resp.json()
                embed = discord.Embed(title="Random Dog", color=COLOR_WARNING)
                embed.set_image(url=data[0] if isinstance(data, list) else data["message"])
                await ctx.send(embed=embed)
    except (aiohttp.ClientError, asyncio.TimeoutError, IndexError, KeyError, TypeError):
        await ctx.send("Couldn't fetch a dog right now. Try again in a bit.")

# ---------------------------------------------------------------------------
# WOULD YOU RATHER
# ---------------------------------------------------------------------------

WYR_QUESTIONS = [
    ("Always be 10 minutes late", "Always be 20 minutes early"),
    ("Have unlimited pizza for life", "Have unlimited tacos for life"),
    ("Be able to fly", "Be able to read minds"),
    ("Live without music", "Live without movies"),
    ("Have no internet for a month", "Have no phone for a month"),
    ("Fight 100 duck-sized horses", "Fight 1 horse-sized duck"),
    ("Always have to whisper", "Always have to shout"),
    ("Have unlimited money but no friends", "Have no money but amazing friends"),
    ("Be able to talk to animals", "Speak every human language"),
    ("Live in the past", "Live in the future"),
    ("Have super strength", "Have super speed"),
    ("Never eat cheese again", "Never eat chocolate again"),
    ("Be famous and broke", "Be unknown and rich"),
    ("Have a rewind button for your life", "Have a pause button"),
    ("Be stuck in a horror movie", "Be stuck in a rom-com"),
    ("Always be slightly itchy", "Always be slightly sticky"),
    ("Only eat raw food", "Only eat canned food"),
    ("Have a personal chef", "Have a personal chauffeur"),
    ("Know the date of your death", "Know the cause of your death"),
    ("Have hands for feet", "Have feet for hands"),
]

@bot.command()
async def wyr(ctx):
    """Would You Rather — vote with reactions!"""
    a, b = random.choice(WYR_QUESTIONS)
    embed = make_embed("🤔 Would You Rather...",
        f"🅰️ {a}\n\n**OR**\n\n🅱️ {b}", COLOR_PINK)
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🅰️")
    await msg.add_reaction("🅱️")

# ---------------------------------------------------------------------------
# ON THIS DAY
# ---------------------------------------------------------------------------
@bot.command()
async def onthisday(ctx):
    """Get a random historical event that happened on this day."""
    today = datetime.now(CENTRAL_TZ)
    url = f"https://byabbe.se/on-this-day/{today.month}/{today.day}/events.json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return await ctx.send("Couldn't fetch historical events right now.")
            data = await resp.json()

    events = data.get("events", [])
    if not events:
        return await ctx.send("No events found for today!")

    event = random.choice(events)
    year = event.get("year", "???")
    desc = event.get("description", "No description available.")

    embed = make_embed(
        f"📜 On This Day — {today.strftime('%B %d')}",
        f"**{year}**: {desc}", COLOR_ORANGE)
    await ctx.send(embed=embed)

# ---------------------------------------------------------------------------
# NICKNAME CHANGE
# ---------------------------------------------------------------------------
@bot.command()
async def changenick(ctx, member: discord.Member, *, new_nick: str):
    """Pay coins to change someone's nickname for 24 hours."""
    if member.id == ctx.author.id:
        return await ctx.send("You can't change your own nickname with this!")
    if member.bot:
        return await ctx.send("You can't rename bots!")

    bal = get_balance(ctx.author.id)
    if bal < NICKNAME_COST:
        return await ctx.send(embed=make_embed("❌ Not Enough Coins",
            f"Changing a nickname costs **{NICKNAME_COST}** coins.\nYou have **{bal}**.", COLOR_ERROR))

    try:
        original_nick = member.display_name
        await member.edit(nick=new_nick)
    except discord.Forbidden:
        return await ctx.send("I don't have permission to change that user's nickname.")

    update_balance(ctx.author.id, -NICKNAME_COST)
    expires = datetime.now(timezone.utc) + timedelta(hours=NICKNAME_DURATION_HOURS)

    db.execute(
        "INSERT INTO nick_changes (guild_id, target_id, original_nick, expires_at) VALUES (?, ?, ?, ?)",
        (ctx.guild.id, member.id, original_nick, expires.isoformat())
    )
    db.commit()

    new_bal = get_balance(ctx.author.id)
    await ctx.send(embed=make_embed("✏️ Nickname Changed!",
        f"**{original_nick}** is now **{new_nick}** for {NICKNAME_DURATION_HOURS} hours.\n"
        f"Cost: **{NICKNAME_COST}** coins | Balance: **{new_bal}**", COLOR_SUCCESS))

# ---------------------------------------------------------------------------
# NICKNAME RESTORE TASK
# ---------------------------------------------------------------------------
@tasks.loop(minutes=5)
async def restore_nicknames():
    """Check for expired nickname changes and restore them."""
    now = datetime.now(timezone.utc)
    rows = db.execute("SELECT id, guild_id, target_id, original_nick, expires_at FROM nick_changes").fetchall()
    for row_id, guild_id, target_id, original_nick, expires_at in rows:
        expires = datetime.fromisoformat(expires_at)
        if now >= expires:
            guild = bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(target_id)
                if member:
                    try:
                        await member.edit(nick=original_nick)
                    except discord.Forbidden:
                        pass
            db.execute("DELETE FROM nick_changes WHERE id = ?", (row_id,))
    db.commit()

# ---------------------------------------------------------------------------
# QUOTES
# ---------------------------------------------------------------------------
def format_quote(content, name, date, prefix=""):
    year = date[2:4] if date else "??"
    short = content[:80] + "..." if len(content) > 80 else content
    return f'{prefix}> "{short}"\n\\- {name} 2k{year}'

@bot.command()
async def quote(ctx):
    """Save a quote by replying to a message."""
    if not ctx.message.reference:
        return await ctx.send(f"Reply to a message with `{PREFIX}quote` to save it.")
    try:
        msg = ctx.message.reference.resolved or await ctx.channel.fetch_message(ctx.message.reference.message_id)
    except Exception:
        return await ctx.send("Couldn't fetch that message.")
    if not msg.content:
        return await ctx.send("That message has no text content.")
    now = datetime.now(CENTRAL_TZ).strftime("%Y-%m-%d")
    db.execute(
        "INSERT INTO quotes (guild_id, quoted_user_id, quoted_user_name, content, saved_by, saved_at) VALUES (?, ?, ?, ?, ?, ?)",
        (ctx.guild.id, msg.author.id, msg.author.display_name, msg.content, ctx.author.id, now)
    )
    db.commit()
    await ctx.send(format_quote(msg.content, msg.author.mention, now))

@bot.command()
async def quotes(ctx, flag: str = ""):
    """Show recent quotes. Admin can use .quotes ids to show IDs."""
    show_ids = flag.lower() == "ids" and ctx.author.id == ADMIN_ID
    rows = db.execute(
        "SELECT id, quoted_user_name, content, saved_at FROM quotes WHERE guild_id = ? ORDER BY id DESC LIMIT 10",
        (ctx.guild.id,)
    ).fetchall()
    if not rows:
        return await ctx.send("No quotes saved yet.")
    prefix_fn = lambda qid: f"**#{qid}** " if show_ids else ""
    lines = [format_quote(content, name, date, prefix_fn(qid)) for qid, name, content, date in rows]
    await ctx.send("\n".join(lines))

@bot.command()
async def unquote(ctx, quote_id: int):
    """Delete a quote by ID (admin only)."""
    if ctx.author.id != ADMIN_ID:
        return
    row = db.execute("SELECT id FROM quotes WHERE id = ? AND guild_id = ?", (quote_id, ctx.guild.id)).fetchone()
    if not row:
        return await ctx.send(f"Quote #{quote_id} not found.")
    db.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
    db.commit()
    await ctx.send(f"Quote #{quote_id} deleted.")

# ---------------------------------------------------------------------------
# ADMIN
# ---------------------------------------------------------------------------
@bot.command()
async def stats(ctx):
    """Show bot stats: uptime, latency, versions, economy, and command usage."""
    # Uptime
    if bot_start_time:
        delta = datetime.now(timezone.utc) - bot_start_time
        days, rem = divmod(int(delta.total_seconds()), 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        uptime = " ".join(parts)
    else:
        uptime = "Unknown"

    # Economy
    econ = db.execute("SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM users").fetchone()
    total_users, total_coins = econ
    richest = db.execute(
        "SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 1"
    ).fetchone()
    if richest:
        richest_member = ctx.guild.get_member(richest[0])
        richest_str = f"{richest_member.display_name} ({richest[1]})" if richest_member else f"Unknown ({richest[1]})"
    else:
        richest_str = "Nobody"

    # Command usage (top 5 this session)
    top_cmds = command_usage.most_common(5)
    if top_cmds:
        usage_str = "\n".join(f"`{PREFIX}{name}` — {count}" for name, count in top_cmds)
    else:
        usage_str = "No commands used yet"
    total_cmds = sum(command_usage.values())

    embed = discord.Embed(title="📊 Bot Stats", color=COLOR_DEFAULT)
    embed.add_field(name="Uptime", value=uptime, inline=True)
    embed.add_field(name="Latency", value=f"{bot.latency * 1000:.0f}ms", inline=True)
    embed.add_field(name="Versions", value=(
        f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\n"
        f"discord.py {discord.__version__}"
    ), inline=True)
    embed.add_field(name="Economy", value=(
        f"Users: **{total_users}**\n"
        f"Coins in circulation: **{total_coins}**\n"
        f"Richest: **{richest_str}**"
    ), inline=False)
    embed.add_field(name=f"Top Commands (session: {total_cmds} total)", value=usage_str, inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def invite(ctx):
    """Generate an invite link with the permissions the bot needs."""
    perms = discord.Permissions(
        send_messages=True,
        embed_links=True,
        add_reactions=True,
        manage_nicknames=True,
        read_message_history=True,
        read_messages=True,
    )
    link = discord.utils.oauth_url(bot.user.id, permissions=perms)
    await ctx.send(embed=make_embed("🔗 Invite Link", f"[Click here to invite me!]({link})"))

@bot.command()
async def give(ctx, member: discord.Member, amount: int):
    """Admin only: add coins to a user."""
    if ctx.author.id != ADMIN_ID:
        return
    update_balance(member.id, amount)
    new_bal = get_balance(member.id)
    await ctx.send(f"Gave **{amount}** coins to {member.mention}. New balance: **{new_bal}**")

# ---------------------------------------------------------------------------
# LEADERBOARD
# ---------------------------------------------------------------------------
@bot.command(aliases=["lb", "top"])
async def leaderboard(ctx):
    """Show the richest users in the server."""
    rows = db.execute("SELECT user_id, balance FROM users ORDER BY balance DESC").fetchall()
    lines = []
    medals = ["🥇", "🥈", "🥉"]
    count = 0
    for user_id, bal in rows:
        member = ctx.guild.get_member(user_id)
        if member is None:
            continue
        prefix = medals[count] if count < 3 else f"**{count + 1}.**"
        lines.append(f"{prefix} {member.display_name} — **{bal}** coins")
        count += 1
        if count >= 10:
            break

    if not lines:
        return await ctx.send("No one has any coins yet!")
    await ctx.send(embed=make_embed("🏆 Leaderboard", "\n".join(lines), COLOR_GOLD))

# ---------------------------------------------------------------------------
# HELP OVERRIDE
# ---------------------------------------------------------------------------
bot.remove_command("help")

@bot.command()
async def help(ctx):
    """Show all commands."""
    embed = discord.Embed(title="Bot Commands", color=COLOR_DEFAULT)
    p = PREFIX
    embed.add_field(name="Economy", value=(
        f"`{p}daily` - Claim daily coins\n"
        f"`{p}guess <1-10>` - Guess for a free coin (3x/day)\n"
        f"`{p}puzzle` - Daily puzzle for {PUZZLE_REWARD} coins\n"
        f"`{p}balance` - Check balance\n"
        f"`{p}leaderboard` - Top 10 richest"
    ), inline=False)
    embed.add_field(name="Gambling", value=(
        f"`{p}coinflip <amt>` - Double or nothing\n"
        f"`{p}slots <amt>` - Slot machine\n"
        f"`{p}blackjack <amt>` - Play 21"
    ), inline=False)
    embed.add_field(name="Games", value=(
        f"`{p}ttt @user` - Tic-tac-toe\n"
        f"`{p}c4 @user` - Connect 4\n"
        f"`{p}hangman` - Hangman (anyone can guess)\n"
        f"`{p}forfeit` - Quit current game"
    ), inline=False)
    embed.add_field(name="Weather", value=f"`{p}weather [city]` - Current weather (defaults to Champaign)", inline=False)
    embed.add_field(name="Animals", value=f"`{p}cat` / `{p}dog` - Random pics", inline=False)
    embed.add_field(name="Fun", value=(
        f"`{p}wyr` - Would You Rather\n"
        f"`{p}onthisday` - Historical event today\n"
        f"`{p}changenick @user name` - Change nickname ({NICKNAME_COST} coins)"
    ), inline=False)
    embed.add_field(name="AI", value=(
        f"`{p}ask <question>` - Ask the AI (needs desktop on)\n"
        f"`{p}rp <character>` - Roleplay with Silas\n"
        f"`{p}stoprp` - End roleplay"
    ), inline=False)
    embed.add_field(name="Info", value=(
        f"`{p}stats` - Bot stats and usage\n"
        f"`{p}invite` - Get invite link"
    ), inline=False)
    embed.add_field(name="Quotes", value=(
        f"`{p}quote` - Reply to a message to save it\n"
        f"`{p}quotes` - Show recent quotes"
    ), inline=False)
    await ctx.send(embed=embed)


# ---------------------------------------------------------------------------
# ERROR HANDLER
# ---------------------------------------------------------------------------
COMMAND_USAGE = {
    'guess': '<number 1-10>',
    'solve': '<answer>',
    'ttt': '[@opponent]',
    'c4': '[@opponent]',
    'm': '<1-9>',
    'drop': '<1-7>',
    'g': '<letter>',
    'coinflip': '<amount>',
    'slots': '<amount>',
    'blackjack': '<amount>',
    'weather': '<city>',
    'changenick': '@user <nickname>',
    'ask': '<question>',
    'rp': '<character>',
    'unquote': '<id>',
    'give': '@user <amount>',
}

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        command = ctx.command.name
        args = COMMAND_USAGE.get(command)
        await ctx.send(f"Usage: `{PREFIX}{command} {args}`" if args else f"Check `{PREFIX}help` for usage.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        raise error

# ---------------------------------------------------------------------------
# RUN
# ---------------------------------------------------------------------------
bot.run(TOKEN)
