"""Environment variables and tunable constants.

Everything here is static configuration: read once from the environment (via
.env) or hardcoded as a gameplay knob. Settings that admins can change at
runtime live in the SQLite `settings` table instead — see shared.py.
"""

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

CENTRAL_TZ = ZoneInfo("America/Chicago")

TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_TOKEN_HERE")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "YOUR_API_KEY_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PREFIX = "."

# --- Economy ---
DAILY_AMOUNT = 200
NICKNAME_COST = 2000
NICKNAME_DURATION_HOURS = 24
STARTING_BALANCE = 100

# Daily activity streak bonuses: streak length -> bonus coins.
STREAK_MILESTONES = {7: 100, 30: 500, 100: 2000}

# --- Embed colors ---
COLOR_DEFAULT = 0x5865F2  # Discord blurple
COLOR_SUCCESS = 0x57F287  # Green
COLOR_ERROR = 0xED4245  # Red
COLOR_WARNING = 0xFEE75C  # Yellow
COLOR_PINK = 0xEB459E
COLOR_ORANGE = 0xE67E22
COLOR_GOLD = 0xF1C40F

# --- Ollama ---
OLLAMA_URL = os.getenv("OLLAMA_URL", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_REASONING_MODEL = os.getenv("OLLAMA_REASONING_MODEL", "deepseek-r1:8b")

# Late night callout: hours in US Central time that count as "late".
# The callout chance is the late_night_chance_pct runtime setting.
LATE_NIGHT_START = 1  # 1am Central
LATE_NIGHT_END = 5  # 5am Central

# Dead chat: minutes of silence before escalating
DEAD_CHAT_THRESHOLDS = [60, 180, 360, 720]  # 1hr, 3hr, 6hr, 12hr
DEAD_CHAT_CHANNEL = "bot-spam"  # Only send dead chat messages in this channel

# --- Lucky guess config ---
LUCKY_GUESS_RANGE = 10  # Guess 1-N
LUCKY_GUESS_REWARD = 1  # Coins awarded on correct guess
LUCKY_GUESS_MAX_DAILY = 3  # Max attempts per day

# --- Daily puzzle config ---
PUZZLE_REWARD = 50  # Coins awarded for solving daily puzzle
PUZZLE_MAX_ATTEMPTS = 3  # Max wrong answers before lockout
