"""Pure unit tests for helpers in shared.py — DB-free where possible."""

import pytest

import shared
from shared import (
    clean_reasoning,
    normalize_feature_name,
    is_kids_command_allowed,
    is_feature_allowed,
    KIDS_MODE_BLOCKED_COMMANDS,
)


# ---------------------------------------------------------------------------
# clean_reasoning — strips <think>...</think> blocks and wrapping quotes
# ---------------------------------------------------------------------------
class TestCleanReasoning:
    def test_strips_think_block(self):
        text = "<think>internal reasoning</think>\nthe actual answer"
        assert clean_reasoning(text) == "the actual answer"

    def test_strips_multiline_think_block(self):
        text = "<think>line one\nline two\nline three</think>\nfinal answer"
        assert clean_reasoning(text) == "final answer"

    def test_strips_wrapping_double_quotes(self):
        assert clean_reasoning('"hello"') == "hello"

    def test_does_not_strip_inner_quotes(self):
        assert clean_reasoning('he said "hi" to me') == 'he said "hi" to me'

    def test_no_thinking_no_quotes_passes_through(self):
        assert clean_reasoning("just text") == "just text"

    def test_strips_think_and_quotes_together(self):
        text = '<think>reasoning</think>\n"the answer"'
        assert clean_reasoning(text) == "the answer"


# ---------------------------------------------------------------------------
# normalize_feature_name
# ---------------------------------------------------------------------------
class TestNormalizeFeatureName:
    def test_lowercases_and_trims(self):
        assert normalize_feature_name("  Dead Chat  ") == "dead_chat"

    def test_replaces_spaces_with_underscores(self):
        assert normalize_feature_name("late night") == "late_night"

    def test_idempotent(self):
        once = normalize_feature_name("Mention Reply")
        twice = normalize_feature_name(once)
        assert once == twice == "mention_reply"


# ---------------------------------------------------------------------------
# is_kids_command_allowed
# ---------------------------------------------------------------------------
class TestIsKidsCommandAllowed:
    @pytest.mark.parametrize("blocked", sorted(KIDS_MODE_BLOCKED_COMMANDS))
    def test_blocked_commands_disallowed(self, blocked):
        assert is_kids_command_allowed(blocked) is False

    @pytest.mark.parametrize("allowed", ["help", "ttt", "c4", "rps", "weather", "joke", "cat", "dog"])
    def test_curated_commands_allowed(self, allowed):
        assert is_kids_command_allowed(allowed) is True

    def test_case_and_whitespace_normalized(self):
        # 'BLACKJACK' is in KIDS_MODE_BLOCKED_COMMANDS as 'blackjack'.
        assert is_kids_command_allowed("  BLACKJACK  ") is False


# ---------------------------------------------------------------------------
# is_feature_allowed — depends on runtime_settings; conftest resets it
# ---------------------------------------------------------------------------
class TestIsFeatureAllowed:
    def test_default_allows_everything(self):
        # No rules configured -> allowed.
        assert is_feature_allowed("dead_chat", channel_id=1, guild_id=None) is True

    def test_off_mode_blocks_all_channels(self):
        shared.runtime_settings["feature_channel_rules"] = {
            "dead_chat": {"mode": "off", "channels": []},
        }
        assert is_feature_allowed("dead_chat", channel_id=1) is False
        assert is_feature_allowed("dead_chat", channel_id=999) is False

    def test_whitelist_only_allows_listed(self):
        shared.runtime_settings["feature_channel_rules"] = {
            "dead_chat": {"mode": "whitelist", "channels": [42]},
        }
        assert is_feature_allowed("dead_chat", channel_id=42) is True
        assert is_feature_allowed("dead_chat", channel_id=43) is False

    def test_blacklist_blocks_listed(self):
        shared.runtime_settings["feature_channel_rules"] = {
            "dead_chat": {"mode": "blacklist", "channels": [42]},
        }
        assert is_feature_allowed("dead_chat", channel_id=42) is False
        assert is_feature_allowed("dead_chat", channel_id=43) is True

    def test_kids_mode_blocks_blocked_features_regardless_of_channel_rule(self):
        shared.set_kids_mode_guild(1234, True)
        # Kids-mode features are blocked regardless of channel rule.
        assert is_feature_allowed("cmd:slots", channel_id=1, guild_id=1234) is False
        # Curated features (not in KIDS_MODE_BLOCKED_FEATURES) still pass.
        assert is_feature_allowed("cmd:weather", channel_id=1, guild_id=1234) is True
        assert is_feature_allowed("cmd:ttt", channel_id=1, guild_id=1234) is True


# ---------------------------------------------------------------------------
# load_runtime_settings — persisted values survive a reload (restart)
# ---------------------------------------------------------------------------
class TestRuntimeSettingsReload:
    @pytest.mark.parametrize(
        "key",
        ["unsolicited_chance_pct", "silas_banter_chance_pct", "silas_react_chance_pct", "late_night_chance_pct"],
    )
    def test_passive_pct_survives_reload(self, key):
        shared._save_json_setting(key, 40)
        shared.runtime_settings[key] = 0  # simulate a fresh process
        shared.load_runtime_settings()
        assert shared.runtime_settings[key] == 40

    def test_passive_pct_clamped_on_load(self):
        shared._save_json_setting("late_night_chance_pct", 250)
        shared.load_runtime_settings()
        assert shared.runtime_settings["late_night_chance_pct"] == 100


# ---------------------------------------------------------------------------
# set_setting — single write path: parse, apply, persist
# ---------------------------------------------------------------------------
class TestSetSetting:
    def test_unknown_key_raises(self):
        with pytest.raises(KeyError):
            shared.set_setting("not_a_real_setting", 1)

    def test_parse_and_clamp_applied(self):
        assert shared.set_setting("unsolicited_chance_pct", 250) == 100
        assert shared.runtime_settings["unsolicited_chance_pct"] == 100

    def test_value_persists_across_reload(self):
        shared.set_setting("weather_alert_city", "Mattoon")
        shared.runtime_settings["weather_alert_city"] = "Champaign"  # simulate restart
        shared.load_runtime_settings()
        assert shared.runtime_settings["weather_alert_city"] == "Mattoon"

    def test_channel_id_coerced_to_optional_int(self):
        assert shared.set_setting("ticker_channel_id", "12345") == 12345
        assert shared.set_setting("ticker_channel_id", None) is None

    def test_registry_covers_all_defaults(self):
        assert set(shared.SETTINGS_REGISTRY) == set(shared.SETTINGS_DEFAULTS)


# ---------------------------------------------------------------------------
# GambleState — persisted JSON shape stays byte-compatible with the old dicts
# ---------------------------------------------------------------------------
class TestGambleState:
    _LEGACY_KEYS = [
        "day",
        "scratchoffs_used",
        "blackjack_active",
        "last_action_at",
        "blackjack_started_at",
        "hangman_active",
        "hangman_started_at",
        "hangman_ended_at",
        "last_report_key",
        "last_known_balance",
        "session_anchor_balance",
        "morning_hangman_done",
        "session_id",
    ]

    def test_json_dict_keys_match_legacy_shape(self):
        assert list(shared.GambleState().to_json_dict().keys()) == self._LEGACY_KEYS

    def test_round_trip_through_settings(self):
        from datetime import datetime, timezone

        state = shared.load_gary_gamble_state()
        state["day"] = "2026-07-06"
        state["scratchoffs_used"] = 3
        state["blackjack_active"] = True
        state["last_action_at"] = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
        state["last_known_balance"] = 4200
        shared.save_gary_gamble_state(state)

        loaded = shared.load_gary_gamble_state()
        assert loaded["day"] == "2026-07-06"
        assert loaded["scratchoffs_used"] == 3
        assert loaded["blackjack_active"] is True
        assert loaded["last_action_at"] == datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
        assert loaded["last_known_balance"] == 4200
        assert loaded["session_id"] is None

    def test_naive_datetime_string_assumed_utc(self):
        from datetime import timezone

        state = shared.GambleState.from_json_dict({"last_action_at": "2026-07-06T12:00:00"})
        assert state.last_action_at.tzinfo == timezone.utc

    def test_unknown_key_assignment_rejected(self):
        with pytest.raises(KeyError):
            shared.GambleState()["not_a_field"] = 1


# ---------------------------------------------------------------------------
# extract_message_text
# ---------------------------------------------------------------------------
class TestExtractMessageText:
    def _msg(self, content="", embeds=()):
        class _E:
            def __init__(self, title, description):
                self.title = title
                self.description = description

        class _M:
            pass

        m = _M()
        m.content = content
        m.embeds = [_E(t, d) for t, d in embeds]
        return m

    def test_content_only(self):
        assert shared.extract_message_text(self._msg("hello")) == "hello"

    def test_content_and_embed_parts(self):
        m = self._msg("body", embeds=[("Title", "Desc")])
        assert shared.extract_message_text(m) == "body\nTitle\nDesc"

    def test_empty_parts_skipped(self):
        m = self._msg("", embeds=[("", "only desc")])
        assert shared.extract_message_text(m) == "only desc"


# ---------------------------------------------------------------------------
# is_kids_mode_guild
# ---------------------------------------------------------------------------
class TestKidsModeGuild:
    def test_default_off(self):
        assert shared.is_kids_mode_guild(1) is False

    def test_set_then_read(self):
        shared.set_kids_mode_guild(1, True)
        assert shared.is_kids_mode_guild(1) is True
        shared.set_kids_mode_guild(1, False)
        assert shared.is_kids_mode_guild(1) is False

    def test_per_guild_isolation(self):
        shared.set_kids_mode_guild(1, True)
        assert shared.is_kids_mode_guild(2) is False

    def test_none_guild_id_returns_false(self):
        assert shared.is_kids_mode_guild(None) is False
