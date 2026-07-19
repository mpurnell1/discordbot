"""Database persistence tests — round-trip writes/reads, schema migrations."""

import sqlite3


import db as db_module
import shared


# ---------------------------------------------------------------------------
# users table — get_balance / update_balance / peek_balance
# ---------------------------------------------------------------------------
class TestBalance:
    def test_get_balance_creates_row_at_starting_balance(self):
        bal = shared.get_balance(42)
        assert bal == shared.STARTING_BALANCE

    def test_get_balance_returns_existing_balance(self):
        shared.get_balance(42)  # creates row
        shared.update_balance(42, 50)
        assert shared.get_balance(42) == shared.STARTING_BALANCE + 50

    def test_peek_balance_returns_zero_for_missing_user(self):
        assert shared.peek_balance(9999) == 0

    def test_peek_balance_does_not_create_row(self):
        shared.peek_balance(9999)
        # Row should still not exist.
        row = shared.db.execute("SELECT user_id FROM users WHERE user_id = 9999").fetchone()
        assert row is None

    def test_update_balance_is_additive(self):
        shared.update_balance(42, 100)
        shared.update_balance(42, 50)
        assert shared.get_balance(42) == shared.STARTING_BALANCE + 150

    def test_update_balance_accepts_negative(self):
        shared.update_balance(42, -50)
        assert shared.get_balance(42) == shared.STARTING_BALANCE - 50


# ---------------------------------------------------------------------------
# settings table — JSON round-trip
# ---------------------------------------------------------------------------
class TestSettingsRoundtrip:
    def test_roundtrip_string(self):
        shared._save_json_setting("foo", "hello")
        assert shared._load_json_setting("foo", None) == "hello"

    def test_roundtrip_dict(self):
        payload = {"a": 1, "b": [1, 2, 3], "nested": {"x": True}}
        shared._save_json_setting("config", payload)
        assert shared._load_json_setting("config", None) == payload

    def test_load_returns_default_for_missing_key(self):
        assert shared._load_json_setting("nope", "fallback") == "fallback"

    def test_load_returns_default_for_corrupt_json(self):
        # Inject raw bad JSON directly.
        shared.db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("corrupt", "{not json"),
        )
        shared.db.commit()
        assert shared._load_json_setting("corrupt", "default") == "default"

    def test_save_overwrites_existing_value(self):
        shared._save_json_setting("k", "v1")
        shared._save_json_setting("k", "v2")
        assert shared._load_json_setting("k", None) == "v2"


# ---------------------------------------------------------------------------
# guild_settings table
# ---------------------------------------------------------------------------
class TestGuildSettings:
    def test_kids_mode_persists_through_load(self):
        shared.set_kids_mode_guild(1234, True)
        # Clear in-memory and reload from DB.
        shared.guild_runtime_settings.clear()
        shared.load_guild_settings()
        assert shared.is_kids_mode_guild(1234) is True

    def test_kids_mode_default_off_for_unknown_guild(self):
        assert shared.is_kids_mode_guild(99999) is False


# ---------------------------------------------------------------------------
# init_db — idempotency and schema migrations
# ---------------------------------------------------------------------------
class TestInitDbIdempotent:
    def test_init_db_called_twice_is_safe(self):
        # The CREATE TABLE IF NOT EXISTS + ALTER ADD COLUMN guards should let
        # init_db run repeatedly against the same file without error.
        new_db = shared.init_db()
        new_db.close()  # second open of the same file should be fine
        # If it didn't raise, we're good.

    def test_init_db_migrates_legacy_schema(self, tmp_path, monkeypatch):
        """A pre-existing DB missing newer columns should be migrated, not crashed."""
        legacy_path = tmp_path / "legacy.db"
        legacy = sqlite3.connect(legacy_path)
        # Old schema: only the original columns.
        legacy.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, last_daily TEXT DEFAULT '')")
        legacy.execute("INSERT INTO users (user_id, balance) VALUES (1, 500)")
        legacy.commit()
        legacy.close()

        monkeypatch.setenv("DISCORDBOT_DB_PATH", str(legacy_path))
        # init_db on the legacy file should add the missing columns.
        migrated = shared.init_db()

        # All the new columns should now exist.
        cols = {row[1] for row in migrated.execute("PRAGMA table_info(users)").fetchall()}
        for required in (
            "guess_date",
            "guess_count",
            "puzzle_date",
            "puzzle_solved",
            "puzzle_attempts",
            "active_puzzle_type",
            "active_puzzle_answer",
            "active_puzzle_display",
            "active_puzzle_guesses",
            "last_daily_reminder",
        ):
            assert required in cols, f"missing column after migration: {required}"

        # Existing data preserved.
        bal = migrated.execute("SELECT balance FROM users WHERE user_id = 1").fetchone()
        assert bal[0] == 500
        migrated.close()


# ---------------------------------------------------------------------------
# init_db — versioned migration runner (PRAGMA user_version)
# ---------------------------------------------------------------------------
EXPECTED_TABLES = {
    "users",
    "nick_changes",
    "quotes",
    "settings",
    "guild_settings",
    "command_log",
    "balance_history",
    "puzzle_history",
    "game_results",
    "gambling_log",
    "gary_sessions",
    "stock_prices",
    "stock_holdings",
    "stock_trades",
    "stock_tickers",
    "options",
    "reminders",
}


class TestSchemaVersioning:
    def test_fresh_db_is_stamped_with_latest_version(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DISCORDBOT_DB_PATH", str(tmp_path / "fresh.db"))
        db = shared.init_db()
        (version,) = db.execute("PRAGMA user_version").fetchone()
        assert version == len(db_module.MIGRATIONS)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert EXPECTED_TABLES <= tables
        db.close()

    def test_applied_migrations_are_skipped_on_reopen(self, tmp_path, monkeypatch):
        path = tmp_path / "versioned.db"
        monkeypatch.setenv("DISCORDBOT_DB_PATH", str(path))
        first = shared.init_db()
        first.execute("INSERT INTO users (user_id, balance) VALUES (7, 250)")
        first.commit()
        first.close()

        calls = []

        def spy(db, _baseline=db_module._migration_1_backfill_pre_versioning_columns):
            calls.append(1)
            _baseline(db)

        # init_db reads the migration list from the db module, so patch it there.
        monkeypatch.setattr(db_module, "MIGRATIONS", [spy])
        second = shared.init_db()
        (version,) = second.execute("PRAGMA user_version").fetchone()
        assert version == len(db_module.MIGRATIONS)
        assert calls == []  # already at version 1 — nothing re-runs
        bal = second.execute("SELECT balance FROM users WHERE user_id = 7").fetchone()
        assert bal[0] == 250
        second.close()

    def test_legacy_unversioned_db_is_baselined(self, tmp_path, monkeypatch):
        """A pre-versioning DB (tables exist, user_version = 0) gets the baseline
        applied safely: missing columns appear and the version is stamped."""
        path = tmp_path / "legacy.db"
        legacy = sqlite3.connect(path)
        legacy.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, last_daily TEXT DEFAULT '')")
        legacy.execute("INSERT INTO users (user_id, balance) VALUES (1, 500)")
        legacy.commit()
        (version,) = legacy.execute("PRAGMA user_version").fetchone()
        assert version == 0
        legacy.close()

        monkeypatch.setenv("DISCORDBOT_DB_PATH", str(path))
        migrated = shared.init_db()
        (version,) = migrated.execute("PRAGMA user_version").fetchone()
        assert version == len(db_module.MIGRATIONS)
        cols = {row[1] for row in migrated.execute("PRAGMA table_info(users)").fetchall()}
        assert "activity_streak" in cols
        assert migrated.execute("SELECT balance FROM users WHERE user_id = 1").fetchone()[0] == 500
        migrated.close()
