"""Smoke test: bot boots, all cogs load, every documented command registers."""
import pytest

import bot as bot_module


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
    assert cog_names == {"GamesCog", "EconomyCog", "AICog", "MiscCog"}


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
        "ttt", "c4", "hangman", "weather", "ask", "kidsmode", "settings",
        "help", "adminhelp", "leaderboard",
    }
    registered = {c.name for c in loaded_bot.commands}
    missing = expected - registered
    assert not missing, f"Missing critical commands: {missing}"
