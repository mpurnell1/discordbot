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

    @pytest.mark.parametrize("allowed", ["help", "ttt", "c4", "rps", "weather", "joke"])
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
