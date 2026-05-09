"""Snapshot-style tests for embed structure.

These lock in the user-visible shape of key embeds. If they start failing,
either fix the code (regression) or update the assertion (intentional change).
"""
import discord
import pytest

import shared
from tests.conftest import FakeContext, FakeAuthor


def _embed_fields(embed: discord.Embed):
    """Return a dict-of-name->value for easy assertion."""
    return {f.name: f.value for f in embed.fields}


# ---------------------------------------------------------------------------
# .balance — the simplest economy embed
# ---------------------------------------------------------------------------
async def test_balance_embed_shape():
    from modules.economy import EconomyCog
    cog = EconomyCog(bot=None)

    user = 9001
    ctx = FakeContext(author=FakeAuthor(user_id=user, name="Alice"))
    shared.update_balance(user, 250)

    await cog.balance.callback(cog, ctx, member=None)

    embed = ctx.sent[0]["embed"]
    assert isinstance(embed, discord.Embed)
    assert "Alice" in embed.title
    expected_bal = shared.STARTING_BALANCE + 250
    assert f"**{expected_bal}**" in embed.description
    assert "coins" in embed.description


# ---------------------------------------------------------------------------
# .leaderboard — when empty
# ---------------------------------------------------------------------------
async def test_leaderboard_embed_when_no_members():
    from modules.misc import MiscCog
    cog = MiscCog(bot=None)
    ctx = FakeContext()
    # Force the guild lookup to return no members.
    ctx.guild = type("FakeGuild", (), {"id": 1, "get_member": lambda self, uid: None})()

    # No users in DB -> "no one has any coins" branch.
    await cog.leaderboard.callback(cog, ctx)
    msg = ctx.sent[0]
    assert msg["content"] == "No one has any coins yet!" or (
        msg.get("embed") is None and "No one" in (msg.get("content") or "")
    )


# ---------------------------------------------------------------------------
# Daily reward embed shape (from auto_daily_award)
# ---------------------------------------------------------------------------
async def test_daily_reward_embed_shape():
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock
    import bot as bot_module

    user = 9100
    shared.get_balance(user)
    shared.db.execute(
        "UPDATE users SET last_daily = ? WHERE user_id = ?",
        ((datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(), user),
    )
    shared.db.commit()

    sent = []

    async def capture_send(*args, **kwargs):
        sent.append({"args": args, "kwargs": kwargs})

    ctx = MagicMock()
    ctx.author.id = user
    ctx.send = AsyncMock(side_effect=capture_send)

    await bot_module.auto_daily_award(ctx)

    assert len(sent) == 1
    embed = sent[0]["kwargs"].get("embed")
    assert embed is not None
    assert "Daily" in embed.title
    assert str(shared.DAILY_AMOUNT) in embed.description
    assert "Balance" in embed.description


# ---------------------------------------------------------------------------
# .bjrules — produces a single-line summary
# ---------------------------------------------------------------------------
async def test_bjrules_summary_format():
    from modules.economy import EconomyCog, apply_blackjack_ruleset
    apply_blackjack_ruleset("realistic")

    cog = EconomyCog(bot=None)
    ctx = FakeContext()
    await cog.bjrules.callback(cog, ctx)

    msg = ctx.sent[0]["content"]
    assert msg is not None
    # Required pieces of the summary string.
    assert "Blackjack ruleset" in msg
    assert "realistic" in msg.lower()
    assert "Decks" in msg
    assert "BJ payout" in msg


# ---------------------------------------------------------------------------
# Error embed shape: check_bet "broke" message
# ---------------------------------------------------------------------------
async def test_check_bet_broke_embed_shape():
    user = 9200
    shared.update_balance(user, -shared.STARTING_BALANCE + 5)  # bal = 5
    ctx = FakeContext(author=FakeAuthor(user_id=user))

    invalid = await shared.check_bet(ctx, 1000)
    assert invalid is True

    embed = ctx.sent[0]["embed"]
    assert isinstance(embed, discord.Embed)
    assert embed.color.value == shared.COLOR_ERROR
    assert "Broke" in embed.title
    assert "**5**" in embed.description  # current balance shown
