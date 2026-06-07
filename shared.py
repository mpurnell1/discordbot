from dotenv import load_dotenv
import discord
import aiohttp
import sqlite3
import asyncio
import re
import os
import json
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

load_dotenv()

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
COLOR_DEFAULT = 0x5865F2  # Discord blurple
COLOR_SUCCESS = 0x57F287  # Green
COLOR_ERROR = 0xED4245  # Red
COLOR_WARNING = 0xFEE75C  # Yellow
COLOR_PINK = 0xEB459E
COLOR_ORANGE = 0xE67E22
COLOR_GOLD = 0xF1C40F

# --- Passive feature config ---
OLLAMA_URL = os.getenv("OLLAMA_URL", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_REASONING_MODEL = os.getenv("OLLAMA_REASONING_MODEL", "deepseek-r1:8b")

# Late night callout: hours in US Central time that count as "late"
LATE_NIGHT_START = 1  # 1am Central
LATE_NIGHT_END = 5  # 5am Central
LATE_NIGHT_CHANCE = 0.4  # 40% chance to call someone out

# Dead chat: minutes of silence before escalating
DEAD_CHAT_THRESHOLDS = [60, 180, 360, 720]  # 1hr, 3hr, 6hr, 12hr
DEAD_CHAT_CHANNEL = "bot-spam"  # Only send dead chat messages in this channel

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# --- Lucky guess config ---
LUCKY_GUESS_RANGE = 10  # Guess 1-N
LUCKY_GUESS_REWARD = 1  # Coins awarded on correct guess
LUCKY_GUESS_MAX_DAILY = 3  # Max attempts per day

# --- Daily puzzle config ---
PUZZLE_REWARD = 50  # Coins awarded for solving daily puzzle
PUZZLE_MAX_ATTEMPTS = 3  # Max wrong answers before lockout


# ---------------------------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------------------------
def init_db():
    db_path = os.getenv("DISCORDBOT_DB_PATH") or str(Path(__file__).resolve().parent / "bot.db")
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
        "activity_streak INTEGER DEFAULT 0",
        "activity_streak_max INTEGER DEFAULT 0",
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
    db.execute("""
        CREATE TABLE IF NOT EXISTS command_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            command_name TEXT NOT NULL,
            guild_id INTEGER,
            timestamp TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS balance_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            balance INTEGER NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS puzzle_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            attempts INTEGER NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS game_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            opponent_id INTEGER NOT NULL,
            game_type TEXT NOT NULL,
            outcome TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS gambling_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_type TEXT NOT NULL,
            wager INTEGER NOT NULL,
            delta INTEGER NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS gary_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            start_balance INTEGER NOT NULL,
            end_balance INTEGER,
            peak_balance INTEGER NOT NULL,
            outcome TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS stock_prices (
            ticker TEXT PRIMARY KEY,
            price REAL NOT NULL,
            prev_close REAL NOT NULL,
            last_updated TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS stock_holdings (
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            shares REAL NOT NULL,
            avg_cost REAL NOT NULL,
            PRIMARY KEY (user_id, ticker)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS stock_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            action TEXT NOT NULL,
            shares REAL NOT NULL,
            price REAL NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    try:
        db.execute("ALTER TABLE stock_trades ADD COLUMN realized_pl REAL")
    except sqlite3.OperationalError:
        pass
    # Registry of tradable US tickers. Seeded with a curated list in
    # modules/stocks.py on first boot; users can add more via `.stocks add SYM`.
    db.execute("""
        CREATE TABLE IF NOT EXISTS stock_tickers (
            ticker TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            added_by INTEGER,
            added_at TEXT NOT NULL
        )
    """)
    # ----- One-time migration: retire the old simulated tickers -----
    # The simulated market's last "price" was itself fabricated, so we refund
    # every open position at the user's avg_cost — they get back exactly what
    # they paid in, zero realized P/L. Each refund is logged to balance_history
    # for auditability. Legacy trade rows are dropped along with the holdings
    # and price rows so the new yfinance-backed seeding can take over cleanly.
    _LEGACY_FAKE_TICKERS = ("GARY", "SILS", "COIN", "DOGE", "WORD", "DEAD")
    _legacy_placeholders = ",".join("?" * len(_LEGACY_FAKE_TICKERS))
    legacy_present = db.execute(
        f"SELECT 1 FROM stock_prices WHERE ticker IN ({_legacy_placeholders}) LIMIT 1",
        _LEGACY_FAKE_TICKERS,
    ).fetchone()
    if legacy_present:
        legacy_holdings = db.execute(
            f"SELECT user_id, ticker, shares, avg_cost FROM stock_holdings WHERE ticker IN ({_legacy_placeholders})",
            _LEGACY_FAKE_TICKERS,
        ).fetchall()
        now_iso = datetime.now(timezone.utc).isoformat()
        for user_id, ticker, shares, avg_cost in legacy_holdings:
            refund = int(round(float(shares) * float(avg_cost)))
            if refund <= 0:
                continue
            db.execute(
                "INSERT INTO users (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?",
                (user_id, refund, refund),
            )
            new_bal = db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()[0]
            db.execute(
                "INSERT INTO balance_history (user_id, balance, timestamp) VALUES (?, ?, ?)",
                (user_id, new_bal, now_iso),
            )
        db.execute(f"DELETE FROM stock_holdings WHERE ticker IN ({_legacy_placeholders})", _LEGACY_FAKE_TICKERS)
        db.execute(f"DELETE FROM stock_trades   WHERE ticker IN ({_legacy_placeholders})", _LEGACY_FAKE_TICKERS)
        db.execute(f"DELETE FROM stock_prices   WHERE ticker IN ({_legacy_placeholders})", _LEGACY_FAKE_TICKERS)
    db.execute("""
        CREATE TABLE IF NOT EXISTS options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            option_type TEXT NOT NULL,
            coins_bet INTEGER NOT NULL,
            strike_price REAL NOT NULL,
            opened_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            settled INTEGER DEFAULT 0,
            exit_price REAL,
            pnl INTEGER,
            settled_at TEXT,
            side TEXT DEFAULT 'long'
        )
    """)
    try:
        db.execute("ALTER TABLE options ADD COLUMN side TEXT DEFAULT 'long'")
    except sqlite3.OperationalError:
        pass
    # The old per-day sparkline snapshot table is gone — we now pull history
    # straight from yfinance during the hourly tick and cache it in memory.
    db.execute("DROP TABLE IF EXISTS stock_price_history")
    db.commit()
    return db


db = init_db()

# ---------------------------------------------------------------------------
# BOT SETUP
# ---------------------------------------------------------------------------

# Passive feature state
last_message_time = {}  # channel_id -> datetime
dead_chat_stage = {}  # channel_id -> threshold stage hit; -1 means no threshold hit yet
last_late_night = {}  # user_id -> date string, so we only bug them once per night
recent_messages = {}  # channel_id -> list of last N messages for context
bot_start_time = None  # set in on_ready
command_usage = Counter()  # command name -> count (resets on restart)
messages_seen = 0  # non-self messages observed (resets on restart)
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
    "guild_join_report_channel_id": None,
    "kids_interaction_log_channel_id": None,
    "bug_report_channel_id": None,
    "feature_request_channel_id": None,
    "request_tracking_channel_id": None,
    "silas_bot_id": None,
    "ticker_channel_id": None,
    "ticker_last_morning_date": None,
    "ticker_last_tick_key": None,
}
PROTECTED_ADMIN_COMMANDS = {
    "adminhelp",
    "settings",
    "restart",
    "say",
    "give",
    "clear",
}

KIDS_MODE_BLOCKED_COMMANDS = {
    # Economy is intentionally excluded from kids mode.
    "guess",
    "repuzzle",
    "balance",
    "leaderboard",
    "give",
    "invite",
    "botstat",
    # Stocks (economy adjacent).
    "stocks",
    "buy",
    "sell",
    "portfolio",
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
    # External uncurated text content is not exposed in kids mode.
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
    "cmd:onthisday",
    "cmd:guess",
    "cmd:repuzzle",
    "cmd:balance",
    "cmd:leaderboard",
    "cmd:give",
    "cmd:invite",
    "cmd:stats",
    "cmd:stocks",
    "cmd:buy",
    "cmd:sell",
    "cmd:portfolio",
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
    "- disables all AI and passive behavior, including reminders, Silas handling,"
    " mention replies, unsolicited AI, late-night roasts, and dead-chat callouts\n"
    "- disables nickname changes, quote saving/browsing, and uncurated external content\n"
    "- keeps curated games, weather, and help"
)

# Loaded from SQLite on startup and updated live by admin commands.
runtime_settings = dict(SETTINGS_DEFAULTS)
guild_runtime_settings = {}


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def log_balance(user_id: int, balance: int):
    db.execute(
        "INSERT INTO balance_history (user_id, balance, timestamp) VALUES (?, ?, ?)",
        (user_id, balance, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()


def get_balance(user_id: int) -> int:
    row = db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        db.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, STARTING_BALANCE))
        db.commit()
        log_balance(user_id, STARTING_BALANCE)
        return STARTING_BALANCE
    return row[0]


def peek_balance(user_id: int) -> int:
    """Read-only balance check — returns 0 if the user has no row."""
    row = db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else 0


def update_balance(user_id: int, amount: int):
    old_bal = get_balance(user_id)
    db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    db.commit()
    log_balance(user_id, old_bal + amount)


def log_command(user_id: int, command_name: str, guild_id: int | None):
    db.execute(
        "INSERT INTO command_log (user_id, command_name, guild_id, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, command_name, guild_id, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()


def log_game_result(user_id: int, opponent_id: int, game_type: str, outcome: str):
    db.execute(
        "INSERT INTO game_results (user_id, opponent_id, game_type, outcome, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_id, opponent_id, game_type, outcome, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()


def log_game_win(winner_id: int, loser_id: int, game_type: str):
    now = datetime.now(timezone.utc).isoformat()
    db.executemany(
        "INSERT INTO game_results (user_id, opponent_id, game_type, outcome, timestamp) VALUES (?, ?, ?, ?, ?)",
        [(winner_id, loser_id, game_type, "win", now), (loser_id, winner_id, game_type, "loss", now)],
    )
    db.commit()


def log_game_draw(p1_id: int, p2_id: int, game_type: str):
    now = datetime.now(timezone.utc).isoformat()
    db.executemany(
        "INSERT INTO game_results (user_id, opponent_id, game_type, outcome, timestamp) VALUES (?, ?, ?, ?, ?)",
        [(p1_id, p2_id, game_type, "draw", now), (p2_id, p1_id, game_type, "draw", now)],
    )
    db.commit()


def log_puzzle_solve(user_id: int, date: str, attempts: int):
    db.execute(
        "INSERT INTO puzzle_history (user_id, date, attempts) VALUES (?, ?, ?)",
        (user_id, date, attempts),
    )
    db.commit()


def log_gambling(user_id: int, game_type: str, wager: int, delta: int):
    db.execute(
        "INSERT INTO gambling_log (user_id, game_type, wager, delta, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_id, game_type, wager, delta, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()


def get_gambling_stats(user_id: int) -> dict:
    rows = db.execute(
        "SELECT game_type, wager, delta FROM gambling_log WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    stats: dict[str, dict] = {
        "coinflip": {"net": 0, "wagered": 0, "hands": 0},
        "slots": {"net": 0, "wagered": 0, "hands": 0},
        "blackjack": {"net": 0, "wagered": 0, "hands": 0},
    }
    for game_type, wager, delta in rows:
        if game_type in stats:
            stats[game_type]["net"] += delta
            stats[game_type]["wagered"] += wager
            stats[game_type]["hands"] += 1
    return stats


STREAK_MILESTONES = {7: 100, 30: 500, 100: 2000}


def update_activity_streak(user_id: int, prev_daily_iso: str | None) -> tuple[int, bool, int]:
    """Update daily activity streak. Returns (new_streak, is_milestone, bonus_coins)."""
    row = db.execute("SELECT activity_streak, activity_streak_max FROM users WHERE user_id = ?", (user_id,)).fetchone()
    current_streak = row[0] or 0 if row else 0
    max_streak = row[1] or 0 if row else 0

    today = datetime.now(CENTRAL_TZ).date()
    new_streak = 1
    if prev_daily_iso:
        try:
            prev = datetime.fromisoformat(prev_daily_iso)
            if prev.tzinfo is None:
                prev = prev.replace(tzinfo=CENTRAL_TZ)
            if prev.astimezone(CENTRAL_TZ).date() == today - timedelta(days=1):
                new_streak = current_streak + 1
        except (ValueError, AttributeError):
            pass

    new_max = max(max_streak, new_streak)
    db.execute(
        "UPDATE users SET activity_streak = ?, activity_streak_max = ? WHERE user_id = ?",
        (new_streak, new_max, user_id),
    )
    db.commit()
    bonus = STREAK_MILESTONES.get(new_streak, 0)
    return new_streak, bonus > 0, bonus


def get_activity_streak(user_id: int) -> tuple[int, int]:
    """Return (current_streak, max_streak)."""
    row = db.execute("SELECT activity_streak, activity_streak_max FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return (row[0] or 0, row[1] or 0) if row else (0, 0)


def log_gary_session_start(start_balance: int) -> int:
    cur = db.execute(
        "INSERT INTO gary_sessions (started_at, start_balance, peak_balance, outcome) VALUES (?, ?, ?, 'ongoing')",
        (datetime.now(timezone.utc).isoformat(), start_balance, start_balance),
    )
    db.commit()
    return cur.lastrowid


def update_gary_session_peak(session_id: int, balance: int):
    db.execute(
        "UPDATE gary_sessions SET peak_balance = MAX(peak_balance, ?) WHERE id = ?",
        (balance, session_id),
    )
    db.commit()


def log_gary_session_end(session_id: int, end_balance: int, outcome: str):
    db.execute(
        "UPDATE gary_sessions SET ended_at = ?, end_balance = ?, outcome = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), end_balance, outcome, session_id),
    )
    db.commit()


def get_gary_gamble_stats() -> dict:
    rows = db.execute(
        "SELECT start_balance, end_balance, peak_balance, outcome, started_at FROM gary_sessions ORDER BY id DESC"
    ).fetchall()
    completed = []
    for start_bal, end_bal, peak_bal, outcome, started_at in rows:
        if end_bal is None:
            continue
        completed.append(
            {
                "start": start_bal,
                "end": end_bal,
                "peak": peak_bal,
                "outcome": outcome,
                "delta": end_bal - start_bal,
                "started_at": started_at,
            }
        )
    net = sum(s["delta"] for s in completed)
    wins = sum(1 for s in completed if s["delta"] > 0)
    best = max((s["delta"] for s in completed), default=0)
    worst = min((s["delta"] for s in completed), default=0)
    return {
        "recent": completed[:10],
        "total_sessions": len(completed),
        "net": net,
        "best": best,
        "worst": worst,
        "wins": wins,
    }


def get_puzzle_stats(user_id: int) -> dict:
    rows = db.execute(
        "SELECT date, attempts FROM puzzle_history WHERE user_id = ? ORDER BY date DESC",
        (user_id,),
    ).fetchall()
    total = len(rows)
    avg = sum(r[1] for r in rows) / total if total else 0.0
    solved_dates = {r[0] for r in rows}
    today = datetime.now(CENTRAL_TZ).date()
    start = today if str(today) in solved_dates else today - timedelta(days=1)
    streak = 0
    check = start
    while str(check) in solved_dates:
        streak += 1
        check -= timedelta(days=1)
    return {"total_solves": total, "avg_attempts": avg, "streak": streak}


def get_game_stats(user_id: int, opponent_id: int | None = None) -> dict:
    if opponent_id is not None:
        rows = db.execute(
            "SELECT game_type, outcome FROM game_results WHERE user_id = ? AND opponent_id = ?",
            (user_id, opponent_id),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT game_type, outcome FROM game_results WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    stats = {
        "ttt": {"win": 0, "loss": 0, "draw": 0},
        "c4": {"win": 0, "loss": 0, "draw": 0},
    }
    for game_type, outcome in rows:
        if game_type in stats and outcome in stats[game_type]:
            stats[game_type][outcome] += 1
    return stats


def get_economy_stats(user_id: int) -> dict:
    rows = db.execute(
        "SELECT balance, timestamp FROM balance_history WHERE user_id = ? ORDER BY timestamp",
        (user_id,),
    ).fetchall()
    if not rows:
        return {"peak_balance": get_balance(user_id), "best_day_gain": 0, "worst_day_loss": 0}
    peak_balance = max(r[0] for r in rows)
    day_first: dict[str, int] = {}
    day_last: dict[str, int] = {}
    for balance, ts in rows:
        day = ts[:10]
        if day not in day_first:
            day_first[day] = balance
        day_last[day] = balance
    best_day_gain = 0
    worst_day_loss = 0
    for day in day_first:
        net = day_last[day] - day_first[day]
        if net > best_day_gain:
            best_day_gain = net
        if net < worst_day_loss:
            worst_day_loss = net
    return {
        "peak_balance": peak_balance,
        "best_day_gain": best_day_gain,
        "worst_day_loss": worst_day_loss,
    }


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
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
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
    rows = db.execute("SELECT guild_id, key, value FROM guild_settings").fetchall()
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
    runtime_settings["dead_chat_enabled"] = bool(_load_json_setting("dead_chat_enabled", SETTINGS_DEFAULTS["dead_chat_enabled"]))
    runtime_settings["command_toggles"] = _load_json_setting("command_toggles", SETTINGS_DEFAULTS["command_toggles"])
    runtime_settings["feature_channel_rules"] = _load_json_setting(
        "feature_channel_rules", SETTINGS_DEFAULTS["feature_channel_rules"]
    )
    runtime_settings["gary_gamble_enabled"] = bool(
        _load_json_setting("gary_gamble_enabled", SETTINGS_DEFAULTS["gary_gamble_enabled"])
    )
    channel_val = _load_json_setting("gary_gamble_channel_id", SETTINGS_DEFAULTS["gary_gamble_channel_id"])
    runtime_settings["gary_gamble_channel_id"] = int(channel_val) if channel_val else None
    report_val = _load_json_setting("gary_gamble_report_channel_id", SETTINGS_DEFAULTS["gary_gamble_report_channel_id"])
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
    weather_channel_val = _load_json_setting("weather_alert_channel_id", SETTINGS_DEFAULTS["weather_alert_channel_id"])
    runtime_settings["weather_alert_channel_id"] = int(weather_channel_val) if weather_channel_val else None
    runtime_settings["weather_alert_city"] = str(_load_json_setting("weather_alert_city", SETTINGS_DEFAULTS["weather_alert_city"]))
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
    for key in (
        "guild_join_report_channel_id",
        "kids_interaction_log_channel_id",
        "bug_report_channel_id",
        "feature_request_channel_id",
        "request_tracking_channel_id",
    ):
        raw = _load_json_setting(key, SETTINGS_DEFAULTS[key])
        runtime_settings[key] = int(raw) if raw else None
    silas_raw = _load_json_setting("silas_bot_id", SETTINGS_DEFAULTS["silas_bot_id"])
    runtime_settings["silas_bot_id"] = int(silas_raw) if silas_raw else None
    ticker_channel_val = _load_json_setting("ticker_channel_id", SETTINGS_DEFAULTS["ticker_channel_id"])
    runtime_settings["ticker_channel_id"] = int(ticker_channel_val) if ticker_channel_val else None
    runtime_settings["ticker_last_morning_date"] = _load_json_setting(
        "ticker_last_morning_date", SETTINGS_DEFAULTS["ticker_last_morning_date"]
    )
    runtime_settings["ticker_last_tick_key"] = _load_json_setting("ticker_last_tick_key", SETTINGS_DEFAULTS["ticker_last_tick_key"])


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
        "last_known_balance": (int(raw.get("last_known_balance")) if raw.get("last_known_balance") is not None else None),
        "session_anchor_balance": (
            int(raw.get("session_anchor_balance")) if raw.get("session_anchor_balance") is not None else None
        ),
        "morning_hangman_done": bool(raw.get("morning_hangman_done", False)),
        "session_id": (int(raw.get("session_id")) if raw.get("session_id") is not None else None),
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
        "last_known_balance": (int(state.get("last_known_balance")) if state.get("last_known_balance") is not None else None),
        "session_anchor_balance": (
            int(state.get("session_anchor_balance")) if state.get("session_anchor_balance") is not None else None
        ),
        "morning_hangman_done": bool(state.get("morning_hangman_done", False)),
        "session_id": (int(state.get("session_id")) if state.get("session_id") is not None else None),
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

UNSOLICITED_SYSTEM_PROMPT = """You are a member of a small friend group Discord server.
You're opinionated, a little unhinged, and you say what's on your mind. You're not a bot — you're just a guy in the chat.

You will be given the last few messages from the group chat. If something in the conversation is funny, dumb,
wrong, interesting, or worth commenting on, say something short (1-2 sentences max).
Be casual. Use lowercase. No emojis unless it's really warranted.

Your tone ranges from supportive to roasting depending on what feels right. You can:
- Have a strong opinion about what someone said
- Call someone out
- Agree way too enthusiastically
- Say something slightly unhinged
- Make a joke
- Be weirdly philosophical about something mundane

If there's genuinely nothing worth commenting on, respond with ONLY the word PASS —
nothing else, no extra text, no explanation, just PASS by itself.

IMPORTANT: Keep it SHORT. One or two sentences. You're a guy in the chat, not writing an essay."""

SILAS_BANTER_PROMPT = """You are Gary, a Discord bot in a friend group server.
There's another bot named Silas in the server. You two have a rivalry — you think you're the better bot,
but it's playful, not mean. Think sitcom energy.

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
