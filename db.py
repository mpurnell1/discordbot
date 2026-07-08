"""SQLite connection, schema, and versioned migrations.

Importing this module opens the database (side effect). The path comes from
DISCORDBOT_DB_PATH when set — tests point it at a sandbox file — and defaults
to bot.db next to this file.

Schema changes are versioned via PRAGMA user_version: append a new function
to MIGRATIONS and it runs exactly once per database. Never edit or reorder
existing entries. Migration 1 is a special retrofit for databases created
before versioning existed, so it guards each column-add instead of assuming
a known starting schema.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_BASE_SCHEMA = (
    """
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
        active_puzzle_guesses TEXT DEFAULT '[]',
        activity_streak INTEGER DEFAULT 0,
        activity_streak_max INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS nick_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER,
        target_id INTEGER,
        original_nick TEXT,
        expires_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER,
        quoted_user_id INTEGER,
        quoted_user_name TEXT,
        content TEXT,
        saved_by INTEGER,
        saved_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id INTEGER,
        key TEXT,
        value TEXT NOT NULL,
        PRIMARY KEY (guild_id, key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS command_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        command_name TEXT NOT NULL,
        guild_id INTEGER,
        timestamp TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS balance_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        balance INTEGER NOT NULL,
        timestamp TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS puzzle_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        attempts INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS game_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        opponent_id INTEGER NOT NULL,
        game_type TEXT NOT NULL,
        outcome TEXT NOT NULL,
        timestamp TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gambling_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        game_type TEXT NOT NULL,
        wager INTEGER NOT NULL,
        delta INTEGER NOT NULL,
        timestamp TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gary_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        start_balance INTEGER NOT NULL,
        end_balance INTEGER,
        peak_balance INTEGER NOT NULL,
        outcome TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_prices (
        ticker TEXT PRIMARY KEY,
        price REAL NOT NULL,
        prev_close REAL NOT NULL,
        last_updated TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_holdings (
        user_id INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        shares REAL NOT NULL,
        avg_cost REAL NOT NULL,
        PRIMARY KEY (user_id, ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        action TEXT NOT NULL,
        shares REAL NOT NULL,
        price REAL NOT NULL,
        timestamp TEXT NOT NULL,
        realized_pl REAL
    )
    """,
    # Registry of tradable US tickers. Seeded with a curated list in
    # modules/stocks.py on first boot; users can add more via `.stocks add SYM`.
    """
    CREATE TABLE IF NOT EXISTS stock_tickers (
        ticker TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        added_by INTEGER,
        added_at TEXT NOT NULL
    )
    """,
    """
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
        side TEXT DEFAULT 'long',
        contracts REAL,
        entry_price REAL,
        premium_frac REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        channel_id INTEGER,
        guild_id INTEGER,
        text TEXT NOT NULL,
        next_fire_at TEXT NOT NULL,
        repeat_kind TEXT NOT NULL DEFAULT 'none',
        repeat_interval INTEGER,
        repeat_unit TEXT,
        destination TEXT NOT NULL DEFAULT 'channel',
        created_at TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (active, next_fire_at)",
)


def _add_column_if_missing(db, table: str, column_decl: str):
    column_name = column_decl.split()[0]
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column_name not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column_decl}")


def _migration_1_backfill_pre_versioning_columns(db):
    """Retrofit: add every column that was ever bolted on before versioning.

    Databases arrive here in unknown states (production already has all of
    these; an old backup may have none), so each add is guarded. Later
    migrations start from this known baseline and can use plain ALTERs.
    """
    for col in (
        "last_daily_reminder TEXT DEFAULT ''",
        "guess_date TEXT DEFAULT ''",
        "guess_count INTEGER DEFAULT 0",
        "puzzle_date TEXT DEFAULT ''",
        "puzzle_solved INTEGER DEFAULT 0",
        "puzzle_attempts INTEGER DEFAULT 0",
        "active_puzzle_type TEXT DEFAULT ''",
        "active_puzzle_answer TEXT DEFAULT ''",
        "active_puzzle_display TEXT DEFAULT ''",
        "active_puzzle_guesses TEXT DEFAULT '[]'",
        "activity_streak INTEGER DEFAULT 0",
        "activity_streak_max INTEGER DEFAULT 0",
    ):
        _add_column_if_missing(db, "users", col)
    _add_column_if_missing(db, "stock_trades", "realized_pl REAL")
    for col in (
        "side TEXT DEFAULT 'long'",
        "contracts REAL",
        "entry_price REAL",
        "premium_frac REAL",
    ):
        _add_column_if_missing(db, "options", col)
    # The old per-day sparkline snapshot table is gone — history now comes
    # from yfinance during the hourly tick and is cached in memory.
    db.execute("DROP TABLE IF EXISTS stock_price_history")


# Append-only. Entry N runs exactly once, on databases whose user_version < N.
MIGRATIONS = [
    _migration_1_backfill_pre_versioning_columns,
]


def _retire_legacy_fake_tickers(db):
    """One-time cleanup of the old simulated market, detected by state (not
    version) so it also heals databases restored from pre-migration backups.

    The simulated market's last "price" was itself fabricated, so we refund
    every open position at the user's avg_cost — they get back exactly what
    they paid in, zero realized P/L. Each refund is logged to balance_history
    for auditability. Legacy trade rows are dropped along with the holdings
    and price rows so the yfinance-backed seeding can take over cleanly.
    """
    legacy_tickers = ("GARY", "SILS", "COIN", "DOGE", "WORD", "DEAD")
    placeholders = ",".join("?" * len(legacy_tickers))
    legacy_present = db.execute(
        f"SELECT 1 FROM stock_prices WHERE ticker IN ({placeholders}) LIMIT 1",
        legacy_tickers,
    ).fetchone()
    if not legacy_present:
        return
    legacy_holdings = db.execute(
        f"SELECT user_id, ticker, shares, avg_cost FROM stock_holdings WHERE ticker IN ({placeholders})",
        legacy_tickers,
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
    db.execute(f"DELETE FROM stock_holdings WHERE ticker IN ({placeholders})", legacy_tickers)
    db.execute(f"DELETE FROM stock_trades   WHERE ticker IN ({placeholders})", legacy_tickers)
    db.execute(f"DELETE FROM stock_prices   WHERE ticker IN ({placeholders})", legacy_tickers)


def init_db():
    db_path = os.getenv("DISCORDBOT_DB_PATH") or str(Path(__file__).resolve().parent / "bot.db")
    db = sqlite3.connect(db_path)
    for statement in _BASE_SCHEMA:
        db.execute(statement)
    version = db.execute("PRAGMA user_version").fetchone()[0]
    for number, migration in enumerate(MIGRATIONS[version:], start=version + 1):
        migration(db)
        db.execute(f"PRAGMA user_version = {number}")
    _retire_legacy_fake_tickers(db)
    db.commit()
    return db


db = init_db()
