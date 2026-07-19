"""Tests for bot.py's command lifecycle hooks (on_command, before_invoke, error handler)."""

from unittest.mock import AsyncMock, MagicMock, patch

from discord.ext import commands

import bot as bot_module
import shared
from tests.conftest import FakeContext, FakeGuild


def _ctx_with_command(name: str) -> FakeContext:
    ctx = FakeContext(guild=FakeGuild())
    ctx.command = MagicMock()
    ctx.command.name = name
    return ctx


async def test_command_invocation_counts_usage_exactly_once():
    """One invocation fires on_command plus the before_invoke hook; together
    they must increment the usage counter exactly once."""
    ctx = _ctx_with_command("roll")
    before = shared.command_usage["roll"]

    await bot_module.on_command(ctx)
    await bot_module.pre_invoke_hooks(ctx)

    assert shared.command_usage["roll"] == before + 1


async def test_single_before_invoke_hook_chains_all_pre_invoke_work():
    """discord.py keeps only one before_invoke hook (later registrations
    replace earlier ones), so both command logging and the daily award must
    run through the single registered hook."""
    assert bot_module.bot._before_invoke is bot_module.pre_invoke_hooks

    ctx = _ctx_with_command("roll")

    def _logged_rolls():
        row = shared.db.execute(
            "SELECT COUNT(*) FROM command_log WHERE user_id = ? AND command_name = 'roll'",
            (ctx.author.id,),
        ).fetchone()
        return row[0]

    before = _logged_rolls()
    await bot_module.pre_invoke_hooks(ctx)
    assert _logged_rolls() == before + 1


async def test_unhandled_error_report_includes_real_traceback():
    """The error report must show the wrapped exception and its traceback, not
    the 'NoneType: None' that format_exc() yields outside an except block."""
    ctx = _ctx_with_command("roll")

    def _boom():
        raise ValueError("kaboom")

    try:
        _boom()
    except ValueError as exc:
        error = commands.CommandInvokeError(exc)

    with patch.object(bot_module, "post_error_log", new=AsyncMock()) as post_log:
        await bot_module.on_command_error(ctx, error)

    body = post_log.await_args.args[1]
    assert "ValueError: kaboom" in body
    assert "_boom" in body  # the real traceback, not an empty one
    assert "NoneType: None" not in body


async def test_check_failures_send_their_message():
    """Ordinary CheckFailures (kids mode, disabled commands) explain
    themselves in the channel."""
    ctx = _ctx_with_command("slots")

    await bot_module.on_command_error(ctx, commands.CheckFailure("That command is currently disabled by an admin."))

    assert ctx.sent[0]["content"] == "That command is currently disabled by an admin."
