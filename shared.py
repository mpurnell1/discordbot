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
from pathlib import Path
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
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://REDACTED_IP:11434")
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

ADMIN_ID = REDACTED_ADMIN_ID
GUILD_JOIN_REPORT_CHANNEL_ID = REDACTED_CHANNEL_ID
SILAS_BOT_ID = REDACTED_BOT_ID

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
    db_path = Path(__file__).resolve().parent / "bot.db"
    db = sqlite3.connect(db_path)
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            last_daily TEXT DEFAULT '',
            last_daily_reminder TEXT DEFAULT '',
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
    try:
        db.execute("ALTER TABLE users ADD COLUMN last_daily_reminder TEXT DEFAULT ''")
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
    db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER,
            key TEXT,
            value TEXT NOT NULL,
            PRIMARY KEY (guild_id, key)
        )
    """)
    db.commit()
    return db

db = init_db()

# ---------------------------------------------------------------------------
# BOT SETUP
# ---------------------------------------------------------------------------

# Passive feature state
last_message_time = {}    # channel_id -> datetime
dead_chat_stage = {}      # channel_id -> threshold stage hit; -1 means no threshold hit yet
last_late_night = {}      # user_id -> date string, so we only bug them once per night
recent_messages = {}      # channel_id -> list of last N messages for context
bot_start_time = None     # set in on_ready
command_usage = Counter() # command name -> count (resets on restart)
messages_seen = 0         # non-self messages observed (resets on restart)
SETTINGS_DEFAULTS = {
    "dead_chat_enabled": False,
    "command_toggles": {},
    "feature_channel_rules": {},
    "gary_gamble_enabled": False,
    "gary_gamble_channel_id": None,
    "gary_gamble_report_channel_id": None,
    "bj_ruleset": "realistic",
    "bj_basic_hint_enabled": True,
    "weather_alert_channel_id": None,
    "weather_alert_city": "Champaign",
    "weather_alert_last_date": None,
    # Percent chances (0-100) for passive AI features. Stored as int.
    "unsolicited_chance_pct": 0,
    "silas_banter_chance_pct": 0,
    "silas_react_chance_pct": 0,
}
PROTECTED_ADMIN_COMMANDS = {
    "adminhelp",
    "kidsmode",
    "setcommand",
    "setdeadchat",
    "setfeaturemode",
    "setfeaturechannels",
    "bjruleset",
    "bjhint",
    "settings",
    "restart",
}

KIDS_MODE_BLOCKED_COMMANDS = {
    # Economy is intentionally excluded from kids mode.
    "guess",
    "repuzzle",
    "balance",
    "leaderboard",
    "give",
    "invite",
    "stats",
    # Gambling and blackjack-adjacent actions.
    "coinflip",
    "slots",
    "blackjack",
    "hit",
    "stand",
    "double",
    "split",
    "surrender",
    "bjrules",
    # Free-form AI and bot-to-bot roleplay are not predictable enough for kids mode.
    "ask",
    "rp",
    "stoprp",
    # Moderation/social commands that can be used to embarrass or preserve messages.
    "changenick",
    "quote",
    "quotes",
    "unquote",
    # External uncurated content is not exposed in kids mode.
    "cat",
    "dog",
    "onthisday",
}

KIDS_MODE_BLOCKED_FEATURES = {
    "cmd:coinflip",
    "cmd:slots",
    "cmd:blackjack",
    "cmd:hit",
    "cmd:stand",
    "cmd:double",
    "cmd:split",
    "cmd:surrender",
    "cmd:bjrules",
    "cmd:ask",
    "cmd:rp",
    "cmd:stoprp",
    "cmd:changenick",
    "cmd:quote",
    "cmd:quotes",
    "cmd:unquote",
    "cmd:cat",
    "cmd:dog",
    "cmd:onthisday",
    "cmd:guess",
    "cmd:repuzzle",
    "cmd:balance",
    "cmd:leaderboard",
    "cmd:give",
    "cmd:invite",
    "cmd:stats",
    "dead_chat",
    "late_night",
    "mention_reply",
    "silas",
    "unsolicited_ai",
}

KIDS_MODE_SUMMARY = (
    "Kids mode keeps Gary useful while removing unpredictable or adult-leaning behavior:\n"
    "- disables economy and coin rewards\n"
    "- disables gambling commands and blackjack actions\n"
    "- disables all AI and passive behavior, including reminders, Silas handling, mention replies, unsolicited AI, late-night roasts, and dead-chat callouts\n"
    "- disables nickname changes, quote saving/browsing, and uncurated external content\n"
    "- keeps curated games, weather, and help"
)

# Loaded from SQLite on startup and updated live by admin commands.
runtime_settings = dict(SETTINGS_DEFAULTS)
guild_runtime_settings = {}

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

def peek_balance(user_id: int) -> int:
    """Read-only balance check — returns 0 if the user has no row."""
    row = db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else 0

def update_balance(user_id: int, amount: int):
    get_balance(user_id)
    db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    db.commit()


def make_embed(title, description, color=COLOR_DEFAULT):
    return discord.Embed(title=title, description=description, color=color)


def get_last_daily_time(user_id: int):
    get_balance(user_id)
    row = db.execute("SELECT last_daily FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row or not row[0]:
        return None
    last = datetime.fromisoformat(row[0])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last


def is_daily_available(user_id: int, now=None):
    if now is None:
        now = datetime.now(CENTRAL_TZ)
    last = get_last_daily_time(user_id)
    if last is None:
        return True, timedelta(0)
    if now - last >= timedelta(hours=24):
        return True, timedelta(0)
    return False, timedelta(hours=24) - (now - last)


def _load_json_setting(key: str, default):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return default


def _save_json_setting(key: str, value):
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
    db.commit()


def _load_guild_json_setting(guild_id: int, key: str, default):
    row = db.execute(
        "SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?",
        (guild_id, key),
    ).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return default


def _save_guild_json_setting(guild_id: int, key: str, value):
    db.execute(
        "INSERT INTO guild_settings (guild_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value",
        (guild_id, key, json.dumps(value)),
    )
    db.commit()


def load_guild_settings():
    guild_runtime_settings.clear()
    rows = db.execute(
        "SELECT guild_id, key, value FROM guild_settings"
    ).fetchall()
    for guild_id, key, raw in rows:
        settings = guild_runtime_settings.setdefault(int(guild_id), {})
        try:
            settings[key] = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue


def is_kids_mode_guild(guild_id: int | None) -> bool:
    if not guild_id:
        return False
    settings = guild_runtime_settings.get(int(guild_id), {})
    return bool(settings.get("kids_mode", False))


def set_kids_mode_guild(guild_id: int, enabled: bool):
    guild_id = int(guild_id)
    settings = guild_runtime_settings.setdefault(guild_id, {})
    settings["kids_mode"] = bool(enabled)
    _save_guild_json_setting(guild_id, "kids_mode", bool(enabled))


def is_kids_command_allowed(command_name: str) -> bool:
    return command_name.strip().lower() not in KIDS_MODE_BLOCKED_COMMANDS


def load_runtime_settings():
    runtime_settings["dead_chat_enabled"] = bool(
        _load_json_setting("dead_chat_enabled", SETTINGS_DEFAULTS["dead_chat_enabled"])
    )
    runtime_settings["command_toggles"] = _load_json_setting(
        "command_toggles", SETTINGS_DEFAULTS["command_toggles"]
    )
    runtime_settings["feature_channel_rules"] = _load_json_setting(
        "feature_channel_rules", SETTINGS_DEFAULTS["feature_channel_rules"]
    )
    runtime_settings["gary_gamble_enabled"] = bool(
        _load_json_setting("gary_gamble_enabled", SETTINGS_DEFAULTS["gary_gamble_enabled"])
    )
    channel_val = _load_json_setting(
        "gary_gamble_channel_id", SETTINGS_DEFAULTS["gary_gamble_channel_id"]
    )
    runtime_settings["gary_gamble_channel_id"] = int(channel_val) if channel_val else None
    report_val = _load_json_setting(
        "gary_gamble_report_channel_id", SETTINGS_DEFAULTS["gary_gamble_report_channel_id"]
    )
    runtime_settings["gary_gamble_report_channel_id"] = int(report_val) if report_val else None
    bj_ruleset = _load_json_setting("bj_ruleset", SETTINGS_DEFAULTS["bj_ruleset"])
    runtime_settings["bj_ruleset"] = (
        str(bj_ruleset).strip().lower()
        if str(bj_ruleset).strip().lower() in {"realistic", "arcade"}
        else SETTINGS_DEFAULTS["bj_ruleset"]
    )
    runtime_settings["bj_basic_hint_enabled"] = bool(
        _load_json_setting("bj_basic_hint_enabled", SETTINGS_DEFAULTS["bj_basic_hint_enabled"])
    )
    weather_channel_val = _load_json_setting(
        "weather_alert_channel_id", SETTINGS_DEFAULTS["weather_alert_channel_id"]
    )
    runtime_settings["weather_alert_channel_id"] = int(weather_channel_val) if weather_channel_val else None
    runtime_settings["weather_alert_city"] = str(
        _load_json_setting("weather_alert_city", SETTINGS_DEFAULTS["weather_alert_city"])
    )
    runtime_settings["weather_alert_last_date"] = _load_json_setting(
        "weather_alert_last_date", SETTINGS_DEFAULTS["weather_alert_last_date"]
    )
    for key in ("unsolicited_chance_pct", "silas_banter_chance_pct", "silas_react_chance_pct"):
        raw = _load_json_setting(key, SETTINGS_DEFAULTS[key])
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = SETTINGS_DEFAULTS[key]
        runtime_settings[key] = max(0, min(100, value))


def normalize_feature_name(feature: str) -> str:
    return feature.strip().lower().replace(" ", "_")


def is_command_enabled(command_name: str) -> bool:
    toggles = runtime_settings.get("command_toggles", {})
    return bool(toggles.get(command_name, True))


def is_feature_allowed(feature: str, channel_id: int, guild_id: int | None = None) -> bool:
    normalized = normalize_feature_name(feature)
    if is_kids_mode_guild(guild_id) and normalized in KIDS_MODE_BLOCKED_FEATURES:
        return False
    rules = runtime_settings.get("feature_channel_rules", {})
    rule = rules.get(normalized)
    if not rule:
        return True
    mode = rule.get("mode", "all")
    channels = {int(c) for c in rule.get("channels", [])}
    if mode == "off":
        return False
    if mode == "whitelist":
        return channel_id in channels
    if mode == "blacklist":
        return channel_id not in channels
    return True


def get_feature_rule(feature: str):
    rules = runtime_settings.get("feature_channel_rules", {})
    return rules.get(normalize_feature_name(feature))


def load_gary_gamble_state():
    raw = _load_json_setting(
        "gary_gamble_state",
        {
            "day": "",
            "scratchoffs_used": 0,
            "blackjack_active": False,
            "last_action_at": None,
        },
    )
    if not isinstance(raw, dict):
        raw = {}
    return {
        "day": str(raw.get("day", "")),
        "scratchoffs_used": max(0, int(raw.get("scratchoffs_used", 0))),
        "blackjack_active": bool(raw.get("blackjack_active", False)),
        "last_action_at": raw.get("last_action_at"),
        "blackjack_started_at": raw.get("blackjack_started_at"),
        "hangman_active": bool(raw.get("hangman_active", False)),
        "hangman_started_at": raw.get("hangman_started_at"),
        "hangman_ended_at": raw.get("hangman_ended_at"),
        "last_report_key": raw.get("last_report_key"),
        "last_known_balance": (
            int(raw.get("last_known_balance"))
            if raw.get("last_known_balance") is not None
            else None
        ),
        "session_anchor_balance": (
            int(raw.get("session_anchor_balance"))
            if raw.get("session_anchor_balance") is not None
            else None
        ),
    }


def save_gary_gamble_state(state: dict):
    payload = {
        "day": str(state.get("day", "")),
        "scratchoffs_used": max(0, int(state.get("scratchoffs_used", 0))),
        "blackjack_active": bool(state.get("blackjack_active", False)),
        "last_action_at": state.get("last_action_at"),
        "blackjack_started_at": state.get("blackjack_started_at"),
        "hangman_active": bool(state.get("hangman_active", False)),
        "hangman_started_at": state.get("hangman_started_at"),
        "hangman_ended_at": state.get("hangman_ended_at"),
        "last_report_key": state.get("last_report_key"),
        "last_known_balance": (
            int(state.get("last_known_balance"))
            if state.get("last_known_balance") is not None
            else None
        ),
        "session_anchor_balance": (
            int(state.get("session_anchor_balance"))
            if state.get("session_anchor_balance") is not None
            else None
        ),
    }
    _save_json_setting("gary_gamble_state", payload)


load_runtime_settings()
load_guild_settings()

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

