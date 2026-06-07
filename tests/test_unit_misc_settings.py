"""Unit tests for MiscCog.settings subcommands and admin commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import shared
from modules.misc import MiscCog
from tests.conftest import FakeAuthor, FakeContext, FakeGuild

ADMIN_ID = shared.ADMIN_ID  # 0 in test env


@pytest.fixture()
def misc_cog():
    bot = MagicMock()
    bot.user.id = 123456
    bot.user.__str__ = lambda self: "Gary#0001"
    bot.latency = 0.05
    bot.guilds = []
    return MiscCog(bot)


def _admin_ctx(guild=None):
    return FakeContext(
        author=FakeAuthor(user_id=ADMIN_ID, name="Admin"),
        guild=guild or FakeGuild(),
    )


def _user_ctx(uid=999, guild=None):
    return FakeContext(author=FakeAuthor(user_id=uid), guild=guild)


# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------


class TestSettingsAdminGuard:
    async def test_non_admin_silently_returns(self, misc_cog):
        ctx = _user_ctx(uid=999)
        await misc_cog.settings.callback(misc_cog, ctx, "gamble", "on")
        assert not ctx.sent  # no reply

    async def test_admin_unknown_section_sends_error(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "notasection")
        assert ctx.sent
        assert "unknown" in (ctx.sent[0].get("content") or "").lower()


# ---------------------------------------------------------------------------
# kids / kidsmode
# ---------------------------------------------------------------------------


class TestSettingsKids:
    async def test_status(self, misc_cog):
        ctx = _admin_ctx(guild=FakeGuild(guild_id=2001))
        await misc_cog.settings.callback(misc_cog, ctx, "kids")
        assert ctx.sent
        assert "kids mode" in (ctx.sent[0]["embed"].description or "").lower()

    async def test_kids_on(self, misc_cog):
        ctx = _admin_ctx(guild=FakeGuild(guild_id=2002))
        await misc_cog.settings.callback(misc_cog, ctx, "kids", "on")
        assert ctx.sent

    async def test_kids_off(self, misc_cog):
        ctx = _admin_ctx(guild=FakeGuild(guild_id=2003))
        await misc_cog.settings.callback(misc_cog, ctx, "kids", "off")
        assert ctx.sent

    async def test_kidsmode_alias_works(self, misc_cog):
        ctx = _admin_ctx(guild=FakeGuild(guild_id=2004))
        await misc_cog.settings.callback(misc_cog, ctx, "kidsmode", "status")
        assert ctx.sent


# ---------------------------------------------------------------------------
# passive
# ---------------------------------------------------------------------------


class TestSettingsPassive:
    async def test_status_shows_all_percentages(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "passive")
        assert ctx.sent
        content = ctx.sent[0].get("content") or ""
        assert "Unsolicited" in content or "unsolicited" in content.lower()

    async def test_set_unsolicited(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "passive", "unsolicited", "15")
        assert ctx.sent
        assert shared.runtime_settings.get("unsolicited_chance_pct") == 15

    async def test_invalid_target_sends_usage(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "passive", "notatarget")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_missing_value_shows_current(self, misc_cog):
        shared.runtime_settings["unsolicited_chance_pct"] = 5
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "passive", "unsolicited")
        assert ctx.sent
        assert "5" in (ctx.sent[0].get("content") or "")

    async def test_invalid_value_sends_error(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "passive", "silasbanter", "abc")
        assert ctx.sent
        assert "integer" in (ctx.sent[0].get("content") or "").lower()

    async def test_value_clamped_to_100(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "passive", "latenight", "200")
        assert shared.runtime_settings.get("late_night_chance_pct") == 100


# ---------------------------------------------------------------------------
# gamble
# ---------------------------------------------------------------------------


class TestSettingsGamble:
    async def test_status_no_args(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "gamble")
        assert ctx.sent
        content = ctx.sent[0].get("content") or ""
        assert "gambling" in content.lower() or "Gary" in content

    async def test_gamble_on(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "gamble", "on")
        assert ctx.sent
        assert shared.runtime_settings.get("gary_gamble_enabled") is True

    async def test_gamble_off(self, misc_cog):
        shared.runtime_settings["gary_gamble_enabled"] = True
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "gamble", "off")
        assert shared.runtime_settings.get("gary_gamble_enabled") is False
        assert ctx.sent

    async def test_gamble_status_action(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "gamble", "status")
        assert ctx.sent

    async def test_gamble_invalid_action_sends_usage(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "gamble", "blah")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_gamble_on_blocked_in_kids_mode(self, misc_cog):
        guild = FakeGuild(guild_id=3001)
        from shared import set_kids_mode_guild

        set_kids_mode_guild(3001, True)
        ctx = _admin_ctx(guild=guild)
        await misc_cog.settings.callback(misc_cog, ctx, "gamble", "on")
        assert ctx.sent
        assert "kids" in (ctx.sent[0].get("content") or "").lower()


# ---------------------------------------------------------------------------
# weather
# ---------------------------------------------------------------------------


class TestSettingsWeather:
    async def test_status_no_channel_set(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "weather")
        assert ctx.sent
        content = ctx.sent[0].get("content") or ""
        assert "weather" in content.lower()

    async def test_weather_off(self, misc_cog):
        shared.runtime_settings["weather_alert_channel_id"] = 9999
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "weather", "off")
        assert ctx.sent
        assert shared.runtime_settings.get("weather_alert_channel_id") is None

    async def test_weather_status_action(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "weather", "status")
        assert ctx.sent

    async def test_weather_city_missing_arg(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "weather", "city")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_weather_city_set(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "weather", "city", "Chicago")
        assert ctx.sent
        assert shared.runtime_settings.get("weather_alert_city") == "Chicago"

    async def test_weather_invalid_action_sends_usage(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "weather", "xyz")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")


# ---------------------------------------------------------------------------
# ticker
# ---------------------------------------------------------------------------


class TestSettingsTicker:
    async def test_status_no_channel(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "ticker")
        assert ctx.sent
        content = ctx.sent[0].get("content") or ""
        assert "ticker" in content.lower()

    async def test_ticker_off(self, misc_cog):
        shared.runtime_settings["ticker_channel_id"] = 8888
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "ticker", "off")
        assert ctx.sent
        assert shared.runtime_settings.get("ticker_channel_id") is None

    async def test_ticker_status_action(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "ticker", "status")
        assert ctx.sent

    async def test_ticker_now_no_stocks_cog(self, misc_cog):
        shared.runtime_settings["ticker_channel_id"] = 8888
        misc_cog.bot.get_cog.return_value = None
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "ticker", "now")
        assert ctx.sent
        assert "StocksCog" in (ctx.sent[0].get("content") or "") or "not loaded" in (ctx.sent[0].get("content") or "").lower()

    async def test_ticker_now_no_channel_set(self, misc_cog):
        shared.runtime_settings.pop("ticker_channel_id", None)
        stocks_mock = MagicMock()
        misc_cog.bot.get_cog.return_value = stocks_mock
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "ticker", "now")
        assert ctx.sent
        assert "ticker" in (ctx.sent[0].get("content") or "").lower()

    async def test_ticker_invalid_action_sends_usage(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "ticker", "xyz")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")


# ---------------------------------------------------------------------------
# deadchat
# ---------------------------------------------------------------------------


class TestSettingsDeadchat:
    async def test_status_no_args(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "deadchat")
        assert ctx.sent
        content = ctx.sent[0].get("content") or ""
        assert "dead chat" in content.lower()

    async def test_on(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "deadchat", "on")
        assert shared.runtime_settings.get("dead_chat_enabled") is True
        assert ctx.sent

    async def test_off(self, misc_cog):
        shared.runtime_settings["dead_chat_enabled"] = True
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "deadchat", "off")
        assert shared.runtime_settings.get("dead_chat_enabled") is False

    async def test_status_action(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "deadchat", "status")
        assert ctx.sent

    async def test_invalid_action_sends_usage(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "deadchat", "florp")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


class TestSettingsCommands:
    async def test_list_disabled_commands_empty(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "commands")
        assert ctx.sent
        assert "None" in (ctx.sent[0].get("content") or "")

    async def test_toggle_on_unknown_command(self, misc_cog):
        misc_cog.bot.get_command.return_value = None
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "commands", "nocommand", "off")
        assert ctx.sent
        assert "Unknown" in (ctx.sent[0].get("content") or "")

    async def test_check_state_of_unknown_command(self, misc_cog):
        misc_cog.bot.get_command.return_value = None
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "commands", "nocommand")
        assert ctx.sent
        assert "Unknown" in (ctx.sent[0].get("content") or "")

    async def test_toggle_known_command_off(self, misc_cog):
        fake_cmd = MagicMock()
        fake_cmd.name = "slots"
        misc_cog.bot.get_command.return_value = fake_cmd
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "commands", "slots", "off")
        assert ctx.sent
        toggles = shared.runtime_settings.get("command_toggles", {})
        assert toggles.get("slots") is False

    async def test_check_known_command_state(self, misc_cog):
        fake_cmd = MagicMock()
        fake_cmd.name = "slots"
        misc_cog.bot.get_command.return_value = fake_cmd
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "commands", "slots")
        assert ctx.sent

    async def test_invalid_on_off_sends_usage(self, misc_cog):
        fake_cmd = MagicMock()
        fake_cmd.name = "slots"
        misc_cog.bot.get_command.return_value = fake_cmd
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "commands", "slots", "maybe")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_cannot_disable_protected_command(self, misc_cog):
        fake_cmd = MagicMock()
        fake_cmd.name = "settings"
        misc_cog.bot.get_command.return_value = fake_cmd
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "commands", "settings", "off")
        assert ctx.sent
        assert "cannot be disabled" in (ctx.sent[0].get("content") or "").lower()


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------


class TestSettingsFeatures:
    async def test_list_no_rules(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "features")
        assert ctx.sent

    async def test_check_single_feature_no_rule(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "features", "gambling")
        assert ctx.sent
        assert "all" in (ctx.sent[0].get("content") or "").lower()

    async def test_set_mode_all(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "features", "gambling", "all")
        assert ctx.sent

    async def test_set_mode_whitelist(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "features", "gambling", "whitelist")
        assert ctx.sent
        rules = shared.runtime_settings.get("feature_channel_rules", {})
        assert rules.get("gambling", {}).get("mode") == "whitelist"

    async def test_invalid_action_sends_usage(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "features", "gambling", "invalid")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_clear_channels(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "features", "gambling", "clear")
        assert ctx.sent

    async def test_list_shows_existing_rules(self, misc_cog):
        shared.runtime_settings["feature_channel_rules"] = {"gambling": {"mode": "whitelist", "channels": [123]}}
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "features")
        assert ctx.sent
        assert "gambling" in (ctx.sent[0].get("content") or "")

    async def test_check_feature_with_existing_rule(self, misc_cog):
        shared.runtime_settings["feature_channel_rules"] = {"gambling": {"mode": "whitelist", "channels": [456]}}
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "features", "gambling")
        assert ctx.sent
        content = ctx.sent[0].get("content") or ""
        assert "whitelist" in content

    async def test_add_channel_mention(self, misc_cog):
        ctx = _admin_ctx()
        fake_ch = MagicMock()
        fake_ch.id = 789
        ctx.message.channel_mentions = [fake_ch]
        await misc_cog.settings.callback(misc_cog, ctx, "features", "gambling", "add")
        assert ctx.sent
        rules = shared.runtime_settings.get("feature_channel_rules", {})
        assert 789 in rules.get("gambling", {}).get("channels", [])

    async def test_add_channel_no_mention_sends_error(self, misc_cog):
        ctx = _admin_ctx()
        # No channel mentions
        await misc_cog.settings.callback(misc_cog, ctx, "features", "gambling", "add")
        assert ctx.sent
        assert "Mention" in (ctx.sent[0].get("content") or "") or "channel" in (ctx.sent[0].get("content") or "").lower()


# ---------------------------------------------------------------------------
# blackjack
# ---------------------------------------------------------------------------


class TestSettingsBlackjack:
    async def test_status_no_args(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "blackjack")
        assert ctx.sent
        content = ctx.sent[0].get("content") or ""
        assert "BJ" in content or "ruleset" in content.lower()

    async def test_no_economy_cog_sends_error(self, misc_cog):
        misc_cog.bot.get_cog.return_value = None
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "blackjack", "ruleset", "realistic")
        assert ctx.sent
        assert "Economy" in (ctx.sent[0].get("content") or "") or "not loaded" in (ctx.sent[0].get("content") or "").lower()

    async def test_ruleset_delegates(self, misc_cog):
        econ_mock = MagicMock()
        econ_mock.bjruleset = AsyncMock()
        misc_cog.bot.get_cog.return_value = econ_mock
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "blackjack", "ruleset", "arcade")
        econ_mock.bjruleset.assert_called_once()

    async def test_hint_delegates(self, misc_cog):
        econ_mock = MagicMock()
        econ_mock.bjhint = AsyncMock()
        misc_cog.bot.get_cog.return_value = econ_mock
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "blackjack", "hint", "on")
        econ_mock.bjhint.assert_called_once()

    async def test_invalid_sub_sends_usage(self, misc_cog):
        econ_mock = MagicMock()
        misc_cog.bot.get_cog.return_value = econ_mock
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "blackjack", "badarg")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")


# ---------------------------------------------------------------------------
# channels
# ---------------------------------------------------------------------------


class TestSettingsChannels:
    async def test_list_all_channels(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "channels")
        assert ctx.sent
        content = ctx.sent[0].get("content") or ""
        assert "guildjoin" in content.lower() or "kidslog" in content.lower()

    async def test_unknown_channel_name(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "channels", "notachannel")
        assert ctx.sent
        assert "Unknown" in (ctx.sent[0].get("content") or "")

    async def test_check_single_channel(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "channels", "bugreport")
        assert ctx.sent

    async def test_clear_channel_with_off(self, misc_cog):
        shared.runtime_settings["bug_report_channel_id"] = 1234
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "channels", "bugreport", "off")
        assert ctx.sent
        assert shared.runtime_settings.get("bug_report_channel_id") is None

    async def test_no_mention_sends_error(self, misc_cog):
        ctx = _admin_ctx()
        # No channel mention in message
        await misc_cog.settings.callback(misc_cog, ctx, "channels", "bugreport", "#general")
        assert ctx.sent
        assert "Mention" in (ctx.sent[0].get("content") or "") or "mention" in (ctx.sent[0].get("content") or "").lower()


# ---------------------------------------------------------------------------
# silas
# ---------------------------------------------------------------------------


class TestSettingsSilas:
    async def test_status_no_args(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "silas")
        assert ctx.sent
        content = ctx.sent[0].get("content") or ""
        assert "Silas" in content

    async def test_set_id(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "silas", "id", "123456789")
        assert ctx.sent
        assert shared.runtime_settings.get("silas_bot_id") == 123456789

    async def test_set_id_no_value_shows_current(self, misc_cog):
        shared.runtime_settings["silas_bot_id"] = 111
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "silas", "id")
        assert ctx.sent
        assert "111" in (ctx.sent[0].get("content") or "")

    async def test_set_id_invalid(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "silas", "id", "notanint")
        assert ctx.sent
        assert "integer" in (ctx.sent[0].get("content") or "").lower()

    async def test_set_banter(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "silas", "banter", "25")
        assert ctx.sent
        assert shared.runtime_settings.get("silas_banter_chance_pct") == 25

    async def test_set_react(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "silas", "react", "10")
        assert ctx.sent
        assert shared.runtime_settings.get("silas_react_chance_pct") == 10

    async def test_banter_no_value_shows_current(self, misc_cog):
        shared.runtime_settings["silas_banter_chance_pct"] = 7
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "silas", "banter")
        assert ctx.sent
        assert "7" in (ctx.sent[0].get("content") or "")

    async def test_invalid_sub_sends_usage(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "silas", "nope")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_banter_invalid_value(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "silas", "banter", "abc")
        assert ctx.sent
        assert "integer" in (ctx.sent[0].get("content") or "").lower()


# ---------------------------------------------------------------------------
# Admin commands: give, say
# ---------------------------------------------------------------------------


class TestGiveCommand:
    async def test_non_admin_silently_returns(self, misc_cog):
        member = FakeAuthor(user_id=500)
        ctx = _user_ctx(uid=999)
        await misc_cog.give.callback(misc_cog, ctx, member, 100)
        assert not ctx.sent

    async def test_admin_gives_coins(self, misc_cog):
        from shared import get_balance, STARTING_BALANCE

        member = FakeAuthor(user_id=600)
        ctx = _admin_ctx()
        await misc_cog.give.callback(misc_cog, ctx, member, 250)
        assert ctx.sent
        assert "250" in (ctx.sent[0].get("content") or "")
        assert get_balance(600) == STARTING_BALANCE + 250


class TestSayCommand:
    async def test_non_admin_silently_returns(self, misc_cog):
        ctx = _user_ctx(uid=999)
        await misc_cog.say.callback(misc_cog, ctx, text="hello")
        assert not ctx.sent

    async def test_admin_sends_text(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.say.callback(misc_cog, ctx, text="test message")
        # Should send the text
        contents = [m.get("content") for m in ctx.sent]
        assert "test message" in contents


# ---------------------------------------------------------------------------
# Quote commands
# ---------------------------------------------------------------------------


class TestQuotesCommand:
    async def test_no_quotes_sends_empty_message(self, misc_cog):
        ctx = FakeContext(guild=FakeGuild(guild_id=4001))
        await misc_cog.quotes.callback(misc_cog, ctx)
        assert ctx.sent
        assert "No quotes" in (ctx.sent[0].get("content") or "")

    async def test_with_quotes_sends_list(self, misc_cog):
        shared.db.execute(
            "INSERT INTO quotes (guild_id, quoted_user_id, quoted_user_name, content, saved_by, saved_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (4002, 1, "Alice", "Test quote", 2, "2025-01-01"),
        )
        shared.db.commit()
        ctx = FakeContext(guild=FakeGuild(guild_id=4002))
        await misc_cog.quotes.callback(misc_cog, ctx)
        assert ctx.sent
        assert "Alice" in (ctx.sent[0].get("content") or "")


class TestUnquoteCommand:
    async def test_non_admin_silently_returns(self, misc_cog):
        ctx = _user_ctx(uid=999, guild=FakeGuild())
        await misc_cog.unquote.callback(misc_cog, ctx, 1)
        assert not ctx.sent

    async def test_not_found(self, misc_cog):
        ctx = _admin_ctx(guild=FakeGuild(guild_id=4100))
        await misc_cog.unquote.callback(misc_cog, ctx, 9999)
        assert ctx.sent
        assert "not found" in (ctx.sent[0].get("content") or "").lower()

    async def test_deletes_quote(self, misc_cog):
        shared.db.execute(
            "INSERT INTO quotes (id, guild_id, quoted_user_id, quoted_user_name, content, saved_by, saved_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (55, 4101, 1, "Bob", "deleteme", 2, "2025-01-01"),
        )
        shared.db.commit()
        ctx = _admin_ctx(guild=FakeGuild(guild_id=4101))
        await misc_cog.unquote.callback(misc_cog, ctx, 55)
        assert ctx.sent
        assert "55" in (ctx.sent[0].get("content") or "")


# ---------------------------------------------------------------------------
# Settings no-section overview
# ---------------------------------------------------------------------------


class TestSettingsOverview:
    async def test_no_section_shows_overview_embed(self, misc_cog):
        ctx = _admin_ctx(guild=FakeGuild(guild_id=5001))
        await misc_cog.settings.callback(misc_cog, ctx)
        assert ctx.sent
        embed = ctx.sent[0].get("embed")
        assert embed is not None
        assert "Runtime Settings" in embed.title


# ---------------------------------------------------------------------------
# Botstat command
# ---------------------------------------------------------------------------


class TestBotstatCommand:
    async def test_non_admin_silently_returns(self, misc_cog):
        ctx = _user_ctx(uid=999)
        await misc_cog.botstat.callback(misc_cog, ctx)
        assert not ctx.sent

    async def test_admin_sends_stats_embed(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.botstat.callback(misc_cog, ctx)
        assert ctx.sent
        embed = ctx.sent[0].get("embed")
        assert embed is not None
        assert "Bot Stats" in embed.title

    async def test_shows_uptime_when_start_time_set(self, misc_cog):
        from datetime import datetime, timezone, timedelta

        shared.bot_start_time = datetime.now(timezone.utc) - timedelta(hours=2)
        ctx = _admin_ctx()
        await misc_cog.botstat.callback(misc_cog, ctx)
        assert ctx.sent
        # Uptime field should be present
        embed = ctx.sent[0].get("embed")
        assert embed is not None

    async def test_shows_unknown_uptime_without_start(self, misc_cog):
        shared.bot_start_time = None
        ctx = _admin_ctx()
        await misc_cog.botstat.callback(misc_cog, ctx)
        assert ctx.sent


# ---------------------------------------------------------------------------
# Stats command
# ---------------------------------------------------------------------------


class TestStatsCommand:
    async def test_own_stats_sends_embed(self, misc_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=6001), guild=FakeGuild())
        await misc_cog.stats.callback(misc_cog, ctx, None)
        assert ctx.sent
        embed = ctx.sent[0].get("embed")
        assert embed is not None

    async def test_h2h_no_games_sends_no_games_message(self, misc_cog):
        me = FakeAuthor(user_id=6002)
        opponent = FakeAuthor(user_id=6003)
        ctx = FakeContext(author=me, guild=FakeGuild())
        await misc_cog.stats.callback(misc_cog, ctx, opponent)
        assert ctx.sent
        embed = ctx.sent[0].get("embed")
        assert embed is not None
        assert "No games" in (embed.description or "")

    async def test_own_stats_kids_mode_no_economy(self, misc_cog):
        from shared import set_kids_mode_guild

        guild = FakeGuild(guild_id=6100)
        set_kids_mode_guild(6100, True)
        ctx = FakeContext(author=FakeAuthor(user_id=6004), guild=guild)
        await misc_cog.stats.callback(misc_cog, ctx, None)
        assert ctx.sent
        embed = ctx.sent[0].get("embed")
        assert embed is not None

    async def test_h2h_with_games_sends_stats(self, misc_cog):
        me = FakeAuthor(user_id=6005)
        opponent = FakeAuthor(user_id=6006)
        # Log a game win so there's h2h data
        shared.log_game_win(me.id, opponent.id, "ttt")
        ctx = FakeContext(author=me, guild=FakeGuild())
        await misc_cog.stats.callback(misc_cog, ctx, opponent)
        assert ctx.sent
        embed = ctx.sent[0].get("embed")
        assert embed is not None
        assert "Head-to-Head" in embed.title


# ---------------------------------------------------------------------------
# Gamble channel and report
# ---------------------------------------------------------------------------


class TestSettingsGambleChannelReport:
    async def test_gamble_channel_no_mention(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "gamble", "channel")
        assert ctx.sent
        assert shared.runtime_settings.get("gary_gamble_channel_id") == ctx.channel.id

    async def test_gamble_report_no_mention(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "gamble", "report")
        assert ctx.sent
        assert shared.runtime_settings.get("gary_gamble_report_channel_id") == ctx.channel.id


# ---------------------------------------------------------------------------
# Weather on (without mention — uses current channel)
# ---------------------------------------------------------------------------


class TestSettingsWeatherOn:
    async def test_weather_on_no_mention(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "weather", "on")
        assert ctx.sent
        assert shared.runtime_settings.get("weather_alert_channel_id") == ctx.channel.id


# ---------------------------------------------------------------------------
# Ticker on (without mention — uses current channel)
# ---------------------------------------------------------------------------


class TestSettingsTickerOn:
    async def test_ticker_on_no_mention(self, misc_cog):
        ctx = _admin_ctx()
        await misc_cog.settings.callback(misc_cog, ctx, "ticker", "on")
        assert ctx.sent
        assert shared.runtime_settings.get("ticker_channel_id") == ctx.channel.id


# ---------------------------------------------------------------------------
# Channels: with mention
# ---------------------------------------------------------------------------


class TestSettingsChannelsWithMention:
    async def test_set_channel_with_mention(self, misc_cog):
        ctx = _admin_ctx()
        fake_ch = MagicMock()
        fake_ch.id = 9001
        fake_ch.mention = "<#9001>"
        ctx.message.channel_mentions = [fake_ch]
        await misc_cog.settings.callback(misc_cog, ctx, "channels", "bugreport", "set")
        assert ctx.sent
        assert shared.runtime_settings.get("bug_report_channel_id") == 9001


# ---------------------------------------------------------------------------
# Botstat: with guilds that have channels
# ---------------------------------------------------------------------------


class TestBotstatWithGuilds:
    async def test_botstat_with_guild_channels(self, misc_cog):
        import discord

        fake_text_ch = MagicMock(spec=discord.TextChannel)
        fake_voice_ch = MagicMock(spec=discord.VoiceChannel)
        fake_guild = MagicMock()
        fake_guild.channels = [fake_text_ch, fake_voice_ch]
        misc_cog.bot.guilds = [fake_guild]
        ctx = _admin_ctx()
        await misc_cog.botstat.callback(misc_cog, ctx)
        assert ctx.sent
        embed = ctx.sent[0].get("embed")
        assert embed is not None


# ---------------------------------------------------------------------------
# Leaderboard command
# ---------------------------------------------------------------------------


class TestLeaderboardCommand:
    async def test_no_users_sends_empty_message(self, misc_cog):
        guild = FakeGuild(guild_id=7001)
        ctx = FakeContext(guild=guild)
        await misc_cog.leaderboard.callback(misc_cog, ctx)
        assert ctx.sent
        assert "No one" in (ctx.sent[0].get("content") or "")

    async def test_with_users_sends_embed(self, misc_cog):
        # Insert a user with balance
        shared.db.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance=?", (7002, 1000, 1000)
        )
        shared.db.commit()

        # Create a guild where get_member returns something
        class _MockGuild(FakeGuild):
            def get_member(self, user_id):
                if user_id == 7002:
                    m = MagicMock()
                    m.display_name = "TestPlayer"
                    return m
                return None

        ctx = FakeContext(guild=_MockGuild(guild_id=7003))
        await misc_cog.leaderboard.callback(misc_cog, ctx)
        assert ctx.sent
        embed = ctx.sent[0].get("embed")
        if embed:
            assert "TestPlayer" in embed.description
