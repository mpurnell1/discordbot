"""Regression tests — one per past bug, named after it.

Each test pins down behavior the team intentionally established. If one starts
failing, look at the bug it's named after before "fixing" the test.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock


import shared
import bot as bot_module


# ---------------------------------------------------------------------------
# Bug: daily reset was using midnight UTC, leading to weird claim windows
# Commit: AGENTS.md gotcha — _scratch_reset_key uses 5am Central
# ---------------------------------------------------------------------------
def test_daily_reset_uses_5am_central_not_utc():
    """The 'logical day' for scratch resets cuts at 5am Central, not UTC midnight."""
    from modules.ai import AICog

    cog = AICog(MagicMock())

    # Just before 5am Central on 2026-05-08 -> previous day's key.
    before = datetime(2026, 5, 8, 4, 59, tzinfo=shared.CENTRAL_TZ).astimezone(timezone.utc)
    after = datetime(2026, 5, 8, 5, 0, tzinfo=shared.CENTRAL_TZ).astimezone(timezone.utc)

    assert cog._scratch_reset_key(before) == "2026-05-07"
    assert cog._scratch_reset_key(after) == "2026-05-08"


# ---------------------------------------------------------------------------
# Bug: kids invite did not auto-enable kids mode
# Commit: e961734 "Auto-enable kids mode for kids invites"
# ---------------------------------------------------------------------------
async def test_kids_invite_auto_enables_kids_mode():
    """When the bot joins a guild without manage_messages/manage_nicknames,
    kids mode should auto-enable for that guild."""
    guild = MagicMock()
    guild.id = 999_001
    guild.name = "low-perm-guild"
    guild.owner_id = 1
    guild.owner = "owner"
    guild.member_count = 3
    guild.me.guild_permissions.manage_messages = False
    guild.me.guild_permissions.manage_nicknames = False

    # The notify call tries to fetch the report channel and send a message;
    # stub those out.
    bot_module.bot.get_channel = MagicMock(return_value=None)
    bot_module.bot.fetch_channel = AsyncMock(return_value=MagicMock(send=AsyncMock()))
    bot_module.bot.get_user = MagicMock(return_value=None)
    bot_module.bot.fetch_user = AsyncMock(return_value=MagicMock(send=AsyncMock()))

    assert shared.is_kids_mode_guild(guild.id) is False
    await bot_module.notify_admin_guild_join(guild)
    assert shared.is_kids_mode_guild(guild.id) is True


async def test_full_perm_invite_does_not_auto_enable_kids_mode():
    """Inverse: a normal invite (manage_messages/nicks) should NOT enable kids mode."""
    guild = MagicMock()
    guild.id = 999_002
    guild.name = "full-perm-guild"
    guild.owner_id = 1
    guild.owner = "owner"
    guild.member_count = 3
    guild.me.guild_permissions.manage_messages = True
    guild.me.guild_permissions.manage_nicknames = True

    bot_module.bot.get_channel = MagicMock(return_value=None)
    bot_module.bot.fetch_channel = AsyncMock(return_value=MagicMock(send=AsyncMock()))
    bot_module.bot.get_user = MagicMock(return_value=None)
    bot_module.bot.fetch_user = AsyncMock(return_value=MagicMock(send=AsyncMock()))

    await bot_module.notify_admin_guild_join(guild)
    assert shared.is_kids_mode_guild(guild.id) is False


# ---------------------------------------------------------------------------
# Bug: .daily was a separate command users had to remember
# Commit: 1f2b183 "Replace .daily command with auto-award on first command use"
# ---------------------------------------------------------------------------
async def test_first_command_of_day_awards_daily_via_before_invoke():
    """The auto_daily_award before_invoke hook awards on the first command of the day."""
    user_id = 5555
    # Manually set last_daily to 25 hours ago -> daily should be available.
    shared.get_balance(user_id)
    yesterday = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    shared.db.execute(
        "UPDATE users SET last_daily = ? WHERE user_id = ?",
        (yesterday, user_id),
    )
    shared.db.commit()
    starting_bal = shared.get_balance(user_id)

    # Build a minimal ctx the hook can use.
    ctx = MagicMock()
    ctx.author.id = user_id
    ctx.send = AsyncMock()

    await bot_module.auto_daily_award(ctx)

    new_bal = shared.get_balance(user_id)
    assert new_bal == starting_bal + shared.DAILY_AMOUNT, "First-command auto-daily should add DAILY_AMOUNT"
    # The hook should have sent the reward embed.
    assert ctx.send.await_count == 1


async def test_second_command_within_24h_does_not_re_award():
    """Daily is once per 24h — second command doesn't re-award."""
    user_id = 5556
    shared.get_balance(user_id)
    just_now = datetime.now(timezone.utc).isoformat()
    shared.db.execute(
        "UPDATE users SET last_daily = ? WHERE user_id = ?",
        (just_now, user_id),
    )
    shared.db.commit()
    bal_before = shared.get_balance(user_id)

    ctx = MagicMock()
    ctx.author.id = user_id
    ctx.send = AsyncMock()
    await bot_module.auto_daily_award(ctx)

    assert shared.get_balance(user_id) == bal_before
    assert ctx.send.await_count == 0


# ---------------------------------------------------------------------------
# Bug: auto_daily_award had no kids mode guard — kids servers received daily
# Commit: fix "auto_daily_award skips kids mode guilds"
# ---------------------------------------------------------------------------
async def test_auto_daily_not_awarded_in_kids_guild():
    """auto_daily_award must not award coins in a kids mode guild."""
    user_id = 5557
    kids_guild_id = 888_001
    shared.set_kids_mode_guild(kids_guild_id, True)
    shared.get_balance(user_id)
    # Daily is available (never claimed).
    bal_before = shared.get_balance(user_id)

    ctx = MagicMock()
    ctx.author.id = user_id
    ctx.guild.id = kids_guild_id
    ctx.send = AsyncMock()

    await bot_module.auto_daily_award(ctx)

    assert shared.get_balance(user_id) == bal_before, "No coins should be awarded in a kids mode guild"
    assert ctx.send.await_count == 0


async def test_auto_daily_still_awarded_in_normal_guild():
    """auto_daily_award must still award coins in a non-kids guild."""
    user_id = 5558
    normal_guild_id = 888_002
    # Ensure kids mode is off for this guild (default).
    shared.set_kids_mode_guild(normal_guild_id, False)
    shared.get_balance(user_id)
    bal_before = shared.get_balance(user_id)

    ctx = MagicMock()
    ctx.author.id = user_id
    ctx.guild.id = normal_guild_id
    ctx.send = AsyncMock()

    await bot_module.auto_daily_award(ctx)

    assert shared.get_balance(user_id) == bal_before + shared.DAILY_AMOUNT
    assert ctx.send.await_count == 1
