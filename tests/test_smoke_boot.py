"""Smoke test: bot boots, all cogs load, every documented command registers."""
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot as bot_module
from tests.conftest import FakeAuthor, FakeContext


@pytest.fixture
async def loaded_bot():
    """Run setup_hook once per test so each gets a fresh cog set."""
    # Remove any cogs that may have been added by a previous test.
    for cog_name in list(bot_module.bot.cogs.keys()):
        await bot_module.bot.remove_cog(cog_name)
    await bot_module.bot.setup_hook()
    yield bot_module.bot


async def test_all_four_cogs_load(loaded_bot):
    cog_names = set(loaded_bot.cogs.keys())
    assert cog_names == {"GamesCog", "EconomyCog", "AICog", "MiscCog", "StocksCog"}


async def test_every_documented_command_is_registered(loaded_bot):
    """Every key in COMMAND_USAGE must correspond to a registered command."""
    registered = {c.name for c in loaded_bot.commands}
    for name in bot_module.COMMAND_USAGE:
        assert name in registered, f"Command `{name}` documented in COMMAND_USAGE but not registered"


async def test_no_command_name_collisions(loaded_bot):
    """A command name should never be registered twice across cogs."""
    names = [c.name for c in loaded_bot.commands]
    duplicates = [n for n in names if names.count(n) > 1]
    assert not duplicates, f"Duplicate command names: {set(duplicates)}"


async def test_critical_commands_exist(loaded_bot):
    """Spot-check the handful of commands that absolutely must exist."""
    expected = {
        "balance", "blackjack", "coinflip", "slots", "puzzle", "solve",
        "ttt", "c4", "hangman", "weather", "ask", "settings",
        "help", "adminhelp", "leaderboard", "alias", "bugreport", "featurerequest",
    }
    registered = {c.name for c in loaded_bot.commands}
    missing = expected - registered
    assert not missing, f"Missing critical commands: {missing}"


async def test_alias_command_lists_public_command_aliases(loaded_bot):
    cog = loaded_bot.get_cog("MiscCog")
    ctx = FakeContext()

    await cog.alias.callback(cog, ctx)

    embed = ctx.sent[0]["embed"]
    fields = {field.name: field.value for field in embed.fields}
    output = "\n".join(fields.values())
    assert embed.title == "Bot Aliases"
    assert "Admin" not in fields
    for command in loaded_bot.commands:
        if not command.aliases or command.name in cog.ADMIN_ALIAS_COMMANDS:
            continue
        aliases = ", ".join(f"`.{alias}`" for alias in command.aliases)
        assert f"`.{command.name}` - {aliases}" in output


async def test_alias_command_lists_admin_aliases_for_admin(loaded_bot):
    cog = loaded_bot.get_cog("MiscCog")
    ctx = FakeContext(author=FakeAuthor(user_id=bot_module.ADMIN_ID))

    await cog.alias.callback(cog, ctx)

    fields = {field.name: field.value for field in ctx.sent[0]["embed"].fields}
    assert "Admin" in fields
    for command in loaded_bot.commands:
        if not command.aliases or command.name not in cog.ADMIN_ALIAS_COMMANDS:
            continue
        aliases = ", ".join(f"`.{alias}`" for alias in command.aliases)
        assert f"`.{command.name}` - {aliases}" in fields["Admin"]


async def test_requested_aliases_resolve_to_commands(loaded_bot):
    for command in loaded_bot.commands:
        for alias in command.aliases:
            assert loaded_bot.get_command(alias).name == command.name


async def test_help_command_info_field_includes_alias(loaded_bot):
    cog = loaded_bot.get_cog("MiscCog")
    ctx = FakeContext()

    await cog.help.callback(cog, ctx)

    embed = ctx.sent[0]["embed"]
    fields = {field.name: field.value for field in embed.fields}
    assert "`.alias`" in fields["Info"]
    assert "`.stats [@user]` - Your stats or head-to-head record" in fields["Info"]
    assert "Bot stats and usage" not in fields["Info"]


async def test_adminhelp_command_lists_botstat_for_runtime_stats(loaded_bot):
    cog = loaded_bot.get_cog("MiscCog")
    ctx = FakeContext(author=FakeAuthor(user_id=bot_module.ADMIN_ID))

    await cog.adminhelp.callback(cog, ctx)

    fields = {field.name: field.value for field in ctx.sent[0]["embed"].fields}
    assert "`.botstat` - Runtime bot stats and usage" in fields["Admin Utils"]
    assert "`.garystats` - Gary autonomous gambling stats" in fields["Admin Utils"]
    assert "`.repuzzle [@user]` - Regenerate a daily puzzle" in fields["Admin Utils"]


async def test_raw_blackjack_action_dispatches_when_hand_is_active():
    from modules.economy import EconomyCog, active_blackjack

    author = FakeAuthor(user_id=1234)
    ctx = MagicMock()
    ctx.invoke = AsyncMock()
    command = MagicMock()
    bot = MagicMock()
    bot.get_context = AsyncMock(return_value=ctx)
    bot.get_command = MagicMock(return_value=command)
    cog = EconomyCog(bot)
    message = MagicMock()
    message.author = author
    message.content = "stand"
    active_blackjack[author.id] = {"active": True}

    try:
        await cog.blackjack_raw_action_listener(message)
    finally:
        active_blackjack.pop(author.id, None)

    bot.get_command.assert_called_once_with("stand")
    ctx.invoke.assert_awaited_once_with(command)


async def test_raw_blackjack_action_ignores_chat_without_active_hand():
    from modules.economy import EconomyCog

    author = FakeAuthor(user_id=5678)
    bot = MagicMock()
    bot.get_context = AsyncMock()
    cog = EconomyCog(bot)
    message = MagicMock()
    message.author = author
    message.content = "hit"

    await cog.blackjack_raw_action_listener(message)

    bot.get_context.assert_not_awaited()
