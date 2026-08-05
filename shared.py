"""Runtime state, settings persistence, and shared DB helpers.

Importing this module has side effects: it opens the SQLite database (via
db.py) and loads runtime settings from it. Tests must set DISCORDBOT_DB_PATH
before importing anything that imports shared — tests/conftest.py does this.

Static configuration lives in config.py, canned responses and prompts in
content.py, and the Ollama HTTP client in ollama_client.py. Their names are
re-exported here because most of the codebase (and the test suite) reaches
them through `shared`.
"""

import asyncio
import functools
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import discord

from config import (  # noqa: F401
    ADMIN_ID,
    CENTRAL_TZ,
    COLOR_DEFAULT,
    COLOR_ERROR,
    COLOR_GOLD,
    COLOR_ORANGE,
    COLOR_PINK,
    COLOR_SUCCESS,
    COLOR_WARNING,
    DAILY_AMOUNT,
    DEAD_CHAT_CHANNEL,
    DEAD_CHAT_THRESHOLDS,
    LATE_NIGHT_END,
    LATE_NIGHT_START,
    LUCKY_GUESS_MAX_DAILY,
    LUCKY_GUESS_RANGE,
    LUCKY_GUESS_REWARD,
    NICKNAME_COST,
    NICKNAME_DURATION_HOURS,
    OLLAMA_MODEL,
    OLLAMA_REASONING_MODEL,
    OLLAMA_URL,
    OPENWEATHER_API_KEY,
    PREFIX,
    PUZZLE_MAX_ATTEMPTS,
    PUZZLE_REWARD,
    STARTING_BALANCE,
    STREAK_MILESTONES,
    TOKEN,
)
from content import (  # noqa: F401
    ASK_SYSTEM_PROMPT,
    DEAD_CHAT_RESPONSES,
    KIDS_MODE_BLOCKED_COMMANDS,
    KIDS_MODE_BLOCKED_FEATURES,
    KIDS_MODE_SUMMARY,
    LATE_NIGHT_RESPONSES,
    PROTECTED_ADMIN_COMMANDS,
    SILAS_BANTER_PROMPT,
    SILAS_REACTIONS,
    UNSOLICITED_SYSTEM_PROMPT,
)
from db import db, init_db  # noqa: F401
from ollama_client import clean_reasoning, query_ollama, query_ollama_chat  # noqa: F401

# ---------------------------------------------------------------------------
# RUNTIME STATE (in-memory, resets on restart)
# ---------------------------------------------------------------------------

# Passive feature state
last_message_time = {}  # channel_id -> datetime
dead_chat_stage = {}  # channel_id -> threshold stage hit; -1 means no threshold hit yet
last_late_night = {}  # user_id -> date string, so we only bug them once per night
recent_messages = {}  # channel_id -> list of last N messages for context
bot_start_time = None  # set in on_ready
command_usage = Counter()  # command name -> count (resets on restart)
messages_seen = 0  # non-self messages observed (resets on restart)

# --- Silas roleplay state ---
active_silas_rp = {}  # channel_id -> {"character": str, "history": list}


# ---------------------------------------------------------------------------
# RUNTIME SETTINGS REGISTRY
#
# Each setting is declared exactly once: (default, coercer). The coercer turns
# whatever was stored (or passed to set_setting) into a valid value; if it
# raises TypeError/ValueError the default wins. Loading and saving both go
# through the same coercion, so a bad DB value can never poison the runtime.
# ---------------------------------------------------------------------------
def _optional_id(raw):
    return int(raw) if raw else None


def _copy_dict(raw):
    return dict(raw)


def _passthrough(raw):
    return raw


def _int_between(lo: int, hi: int):
    return lambda raw: max(lo, min(hi, int(raw)))


def _float_between(lo: float, hi: float):
    return lambda raw: max(lo, min(hi, float(raw)))


def _choice(*options: str):
    def coerce(raw):
        value = str(raw).strip().lower()
        if value not in options:
            raise ValueError(f"expected one of {options}, got {raw!r}")
        return value

    return coerce


SETTINGS_REGISTRY = {
    "dead_chat_enabled": (False, bool),
    "command_toggles": ({}, _copy_dict),
    "feature_channel_rules": ({}, _copy_dict),
    "gary_gamble_enabled": (False, bool),
    "gary_gamble_channel_id": (None, _optional_id),
    "gary_gamble_report_channel_id": (None, _optional_id),
    "bj_ruleset": ("realistic", _choice("realistic", "arcade")),
    "bj_basic_hint_enabled": (True, bool),
    "weather_alert_channel_id": (None, _optional_id),
    "weather_alert_city": ("Champaign", str),
    "weather_alert_last_date": (None, _passthrough),
    # Percent chances (0-100) for passive AI features. Stored as int.
    "unsolicited_chance_pct": (0, _int_between(0, 100)),
    "silas_banter_chance_pct": (0, _int_between(0, 100)),
    "silas_react_chance_pct": (0, _int_between(0, 100)),
    "late_night_chance_pct": (0, _int_between(0, 100)),
    "guild_join_report_channel_id": (None, _optional_id),
    "kids_interaction_log_channel_id": (None, _optional_id),
    "bug_report_channel_id": (None, _optional_id),
    "feature_request_channel_id": (None, _optional_id),
    "request_tracking_channel_id": (None, _optional_id),
    "silas_bot_id": (None, _optional_id),
    "ticker_channel_id": (None, _optional_id),
    "ticker_last_morning_date": (None, _passthrough),
    "ticker_last_tick_key": (None, _passthrough),
    # Options paper-trading knobs (see modules/stocks.py). vol sets the ATM
    # premium, floor caps far-OTM leverage, expiry is the contract life in hours.
    "options_vol": (0.06, _float_between(0.005, 1.0)),
    "options_premium_floor": (0.005, _float_between(0.0005, 0.5)),
    "options_expiry_hours": (24, _int_between(1, 720)),
}

SETTINGS_DEFAULTS = {key: default for key, (default, _) in SETTINGS_REGISTRY.items()}

# Loaded from SQLite on startup and updated live by admin commands.
runtime_settings = dict(SETTINGS_DEFAULTS)
guild_runtime_settings = {}


def _coerce_setting(key: str, raw):
    default, coercer = SETTINGS_REGISTRY[key]
    try:
        return coercer(raw)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def admin_only(func):
    """Make an async cog method silently no-op unless invoked by the admin.

    Wraps the callback itself (rather than registering a commands.check) so
    the gate also holds when callbacks are called directly — which is how the
    test suite and internal dispatchers like .settings drive these methods.
    """

    @functools.wraps(func)
    async def wrapper(self, ctx, *args, **kwargs):
        if ctx.author.id != ADMIN_ID:
            return None
        return await func(self, ctx, *args, **kwargs)

    return wrapper


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


logger = logging.getLogger("garybot")

LOOP_RESTART_DELAY_SECONDS = 60


async def restart_loop_on_error(loop, error):
    """Body of a `@<loop>.error` handler: log the failure and bring the loop back.

    discord.ext.tasks only retries the network errors in its reconnect list —
    anything else (a sqlite3.OperationalError, a discord.Forbidden, a KeyError
    of our own) ends the loop for the rest of the process. Restarting from the
    error callback is the supported way back: the loop's task is still running
    at this point, so restart() can attach its done callback and cancel it.

    restart() runs the next iteration immediately, so wait first — a fault that
    never clears then costs one cycle a minute instead of spinning.
    """
    logger.error("Task loop %s failed — restarting in %ds", loop.coro.__name__, LOOP_RESTART_DELAY_SECONDS, exc_info=error)
    await asyncio.sleep(LOOP_RESTART_DELAY_SECONDS)
    loop.restart()


def extract_message_text(message) -> str:
    """Join a message's content with its embeds' titles and descriptions."""
    parts = []
    if message.content:
        parts.append(message.content)
    for embed in message.embeds:
        if embed.title:
            parts.append(embed.title)
        if embed.description:
            parts.append(embed.description)
    return "\n".join(parts)


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
    for key, (default, _) in SETTINGS_REGISTRY.items():
        raw = _load_json_setting(key, default)
        runtime_settings[key] = _coerce_setting(key, raw)


def set_setting(key: str, value):
    """Coerce `value`, apply it to runtime_settings, and persist it.

    The only supported write path for runtime settings — assigning to
    runtime_settings directly is silently reverted on the next restart.
    Returns the coerced value actually stored.
    """
    if key not in SETTINGS_REGISTRY:
        raise KeyError(f"Unknown runtime setting: {key}")
    coerced = _coerce_setting(key, value)
    runtime_settings[key] = coerced
    _save_json_setting(key, coerced)
    return coerced


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


@dataclass
class GambleState:
    """State for Gary's autonomous gambling loop (modules/ai.py).

    Persisted as JSON under the 'gary_gamble_state' settings key: datetimes are
    ISO strings on disk and aware datetimes (UTC assumed if naive) in memory.
    Field order matches the historical JSON key order.
    """

    day: str = ""
    scratchoffs_used: int = 0
    blackjack_active: bool = False
    last_action_at: datetime | None = None
    blackjack_started_at: datetime | None = None
    hangman_active: bool = False
    hangman_started_at: datetime | None = None
    hangman_ended_at: datetime | None = None
    last_report_key: str | None = None
    last_known_balance: int | None = None
    session_anchor_balance: int | None = None
    morning_hangman_done: bool = False
    session_id: int | None = None

    # Dict-style shims so call sites can keep using state["key"] / state.get("key").
    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        if not hasattr(self, key):
            raise KeyError(key)
        setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)

    @staticmethod
    def _parse_dt(value) -> datetime | None:
        if not (isinstance(value, str) and value):
            return None
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    @classmethod
    def from_json_dict(cls, raw) -> "GambleState":
        if not isinstance(raw, dict):
            raw = {}

        def _opt_int(value):
            return int(value) if value is not None else None

        return cls(
            day=str(raw.get("day", "")),
            scratchoffs_used=max(0, int(raw.get("scratchoffs_used", 0))),
            blackjack_active=bool(raw.get("blackjack_active", False)),
            last_action_at=cls._parse_dt(raw.get("last_action_at")),
            blackjack_started_at=cls._parse_dt(raw.get("blackjack_started_at")),
            hangman_active=bool(raw.get("hangman_active", False)),
            hangman_started_at=cls._parse_dt(raw.get("hangman_started_at")),
            hangman_ended_at=cls._parse_dt(raw.get("hangman_ended_at")),
            last_report_key=raw.get("last_report_key"),
            last_known_balance=_opt_int(raw.get("last_known_balance")),
            session_anchor_balance=_opt_int(raw.get("session_anchor_balance")),
            morning_hangman_done=bool(raw.get("morning_hangman_done", False)),
            session_id=_opt_int(raw.get("session_id")),
        )

    def to_json_dict(self) -> dict:
        def _iso(value):
            return value.isoformat() if isinstance(value, datetime) else None

        return {
            "day": str(self.day),
            "scratchoffs_used": max(0, int(self.scratchoffs_used)),
            "blackjack_active": bool(self.blackjack_active),
            "last_action_at": _iso(self.last_action_at),
            "blackjack_started_at": _iso(self.blackjack_started_at),
            "hangman_active": bool(self.hangman_active),
            "hangman_started_at": _iso(self.hangman_started_at),
            "hangman_ended_at": _iso(self.hangman_ended_at),
            "last_report_key": self.last_report_key,
            "last_known_balance": (int(self.last_known_balance) if self.last_known_balance is not None else None),
            "session_anchor_balance": (int(self.session_anchor_balance) if self.session_anchor_balance is not None else None),
            "morning_hangman_done": bool(self.morning_hangman_done),
            "session_id": (int(self.session_id) if self.session_id is not None else None),
        }


def load_gary_gamble_state() -> GambleState:
    return GambleState.from_json_dict(_load_json_setting("gary_gamble_state", {}))


def save_gary_gamble_state(state: GambleState):
    _save_json_setting("gary_gamble_state", state.to_json_dict())


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
