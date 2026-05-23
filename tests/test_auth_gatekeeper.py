"""Authorization tests for the global command_gatekeeper check."""

from unittest.mock import MagicMock

import pytest
from discord.ext import commands

import shared
import bot as bot_module


def _ctx(*, user_id: int = 1, guild_id: int | None = 1000, channel_id: int = 100, command_name: str = "balance"):
    ctx = MagicMock()
    ctx.author.id = user_id
    ctx.guild = MagicMock(id=guild_id) if guild_id else None
    ctx.channel.id = channel_id
    ctx.command.name = command_name
    return ctx


# ---------------------------------------------------------------------------
# admin bypass
# ---------------------------------------------------------------------------
async def test_admin_bypasses_disabled_command():
    shared.runtime_settings["command_toggles"] = {"slots": False}
    ctx = _ctx(user_id=shared.ADMIN_ID, command_name="slots")
    assert await bot_module.command_gatekeeper(ctx) is True


# ---------------------------------------------------------------------------
# kids-mode blocking
# ---------------------------------------------------------------------------
async def test_kids_mode_blocks_blocked_command_for_admin_too():
    """Kids mode blocks regardless of admin status — it's per-server policy."""
    shared.set_kids_mode_guild(1000, True)
    ctx = _ctx(user_id=shared.ADMIN_ID, command_name="slots")
    with pytest.raises(commands.CheckFailure):
        await bot_module.command_gatekeeper(ctx)


async def test_kids_mode_allows_curated_command():
    shared.set_kids_mode_guild(1000, True)
    ctx = _ctx(command_name="ttt")
    assert await bot_module.command_gatekeeper(ctx) is True


async def test_kids_mode_off_does_not_block_blocked_command():
    """A blocked-in-kids-mode command runs normally outside kids mode."""
    ctx = _ctx(command_name="slots")
    assert await bot_module.command_gatekeeper(ctx) is True


# ---------------------------------------------------------------------------
# disabled-command toggle
# ---------------------------------------------------------------------------
async def test_disabled_command_blocks_non_admin():
    shared.runtime_settings["command_toggles"] = {"slots": False}
    ctx = _ctx(user_id=42, command_name="slots")
    with pytest.raises(commands.CheckFailure):
        await bot_module.command_gatekeeper(ctx)


async def test_enabled_command_passes():
    shared.runtime_settings["command_toggles"] = {"slots": True}
    ctx = _ctx(user_id=42, command_name="slots")
    assert await bot_module.command_gatekeeper(ctx) is True


# ---------------------------------------------------------------------------
# channel rules
# ---------------------------------------------------------------------------
async def test_whitelist_blocks_outside_listed_channel():
    shared.runtime_settings["feature_channel_rules"] = {
        "cmd:slots": {"mode": "whitelist", "channels": [100]},
    }
    ctx = _ctx(user_id=42, channel_id=999, command_name="slots")
    with pytest.raises(commands.CheckFailure):
        await bot_module.command_gatekeeper(ctx)


async def test_whitelist_allows_listed_channel():
    shared.runtime_settings["feature_channel_rules"] = {
        "cmd:slots": {"mode": "whitelist", "channels": [100]},
    }
    ctx = _ctx(user_id=42, channel_id=100, command_name="slots")
    assert await bot_module.command_gatekeeper(ctx) is True


async def test_blacklist_blocks_listed_channel():
    shared.runtime_settings["feature_channel_rules"] = {
        "cmd:slots": {"mode": "blacklist", "channels": [100]},
    }
    ctx = _ctx(user_id=42, channel_id=100, command_name="slots")
    with pytest.raises(commands.CheckFailure):
        await bot_module.command_gatekeeper(ctx)


async def test_no_rule_means_allowed():
    ctx = _ctx(user_id=42, command_name="slots")
    assert await bot_module.command_gatekeeper(ctx) is True


# ---------------------------------------------------------------------------
# DM context (guild=None)
# ---------------------------------------------------------------------------
async def test_dm_context_does_not_apply_kids_mode():
    """is_kids_mode_guild(None) is False, so DMs aren't affected by kids mode."""
    ctx = _ctx(guild_id=None, command_name="slots")
    assert await bot_module.command_gatekeeper(ctx) is True
