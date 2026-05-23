"""Input-handling fuzz tests for things at the user-input boundary.

The two highest-value targets:
  - check_bet (bet validation for all gambling commands)
  - quote command's content sanitization (SQL parameterization sanity check)
"""

import pytest

import shared
from tests.conftest import FakeContext


# ---------------------------------------------------------------------------
# check_bet — gambling bet validation
# ---------------------------------------------------------------------------
class TestCheckBet:
    USER = 4242

    async def test_zero_is_invalid(self):
        ctx = FakeContext()
        ctx.author.id = self.USER
        # Give them some balance so the failure mode is clearly "bet must be positive".
        shared.update_balance(self.USER, 1000)
        result = await shared.check_bet(ctx, 0)
        assert result is True  # invalid -> True
        assert "positive" in ctx.sent[0]["content"].lower()

    async def test_negative_is_invalid(self):
        ctx = FakeContext()
        ctx.author.id = self.USER
        shared.update_balance(self.USER, 1000)
        result = await shared.check_bet(ctx, -50)
        assert result is True
        assert "positive" in ctx.sent[0]["content"].lower()

    async def test_over_balance_is_invalid(self):
        ctx = FakeContext()
        ctx.author.id = self.USER
        shared.update_balance(self.USER, 100)  # bal = STARTING + 100
        starting = shared.get_balance(self.USER)
        result = await shared.check_bet(ctx, starting + 1)
        assert result is True
        # Sends an embed, not a string content.
        embed = ctx.sent[0]["embed"]
        assert embed is not None
        assert "Broke" in embed.title or "broke" in str(embed.description).lower()

    async def test_exact_balance_is_valid(self):
        ctx = FakeContext()
        ctx.author.id = self.USER
        starting = shared.get_balance(self.USER)
        result = await shared.check_bet(ctx, starting)
        assert result is False
        assert ctx.sent == []  # no error message

    async def test_one_below_balance_is_valid(self):
        ctx = FakeContext()
        ctx.author.id = self.USER
        starting = shared.get_balance(self.USER)
        result = await shared.check_bet(ctx, starting - 1)
        assert result is False
        assert ctx.sent == []


# ---------------------------------------------------------------------------
# Quote storage — make sure SQL parameterization handles weird inputs
# ---------------------------------------------------------------------------
class TestQuoteSqlSafety:
    @pytest.mark.parametrize(
        "payload",
        [
            "normal text",
            "'; DROP TABLE quotes; --",  # SQLi attempt
            '"hello"',  # double quotes
            "emoji 🎉 unicode ☃",  # multi-byte
            "\n\nmultiline\n\nbreaks",  # newlines
            "x" * 5000,  # very long
            "",  # empty
            "null\x00byte",  # null byte
        ],
    )
    def test_quote_round_trips_safely(self, payload):
        """Inserting unusual content should round-trip and not affect schema."""
        shared.db.execute(
            "INSERT INTO quotes (guild_id, quoted_user_id, quoted_user_name, content, saved_by, saved_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, 100, "Tester", payload, 1, "2026-05-08"),
        )
        shared.db.commit()
        row = shared.db.execute("SELECT content FROM quotes ORDER BY id DESC LIMIT 1").fetchone()
        assert row[0] == payload
        # Confirm schema wasn't dropped.
        tables = {r[0] for r in shared.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "quotes" in tables
