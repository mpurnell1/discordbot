"""Smoke test: bot boots, all cogs load, every documented command registers."""
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
        "help", "adminhelp", "leaderboard", "alias",
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
    assert embed.title == "Bot Aliases"
    assert "Admin" not in fields
    assert "`.balance` - `.bal`" in fields["Economy"]
    assert "`.leaderboard` - `.lb`, `.top`" in fields["Economy"]
    assert "`.coinflip` - `.cf`" in fields["Gambling"]
    assert "`.slots` - `.slot`" in fields["Gambling"]
    assert "`.blackjack` - `.bj`, `.21`" in fields["Gambling"]
    assert "`.hangman` - `.hang`, `.hm`" in fields["Games"]
    assert "`.forfeit` - `.ff`, `.quit`, `.stop`" in fields["Games"]
    assert "`.weather` - `.w`" in fields["Weather"]
    assert "`.alias` - `.aliases`" in fields["Info"]


async def test_alias_command_lists_admin_aliases_for_admin(loaded_bot):
    cog = loaded_bot.get_cog("MiscCog")
    ctx = FakeContext(author=FakeAuthor(user_id=bot_module.ADMIN_ID))

    await cog.alias.callback(cog, ctx)

    fields = {field.name: field.value for field in ctx.sent[0]["embed"].fields}
    assert "Admin" in fields
    assert "`.kidsmode` - `none`" in fields["Admin"]


async def test_requested_aliases_resolve_to_commands(loaded_bot):
    expected = {
        "w": "weather",
        "lb": "leaderboard",
        "bj": "blackjack",
        "21": "blackjack",
        "slot": "slots",
        "ff": "forfeit",
        "quit": "forfeit",
        "stop": "forfeit",
    }
    for alias, command_name in expected.items():
        assert loaded_bot.get_command(alias).name == command_name
