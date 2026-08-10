"""Unit tests for the pure helper functions in AICog.

No Discord gateway, no Ollama — these test the blackjack/hangman parsing and
Gary autonomous-gambling math that can be exercised in isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import shared
from modules.ai import AICog, is_pass_reply
from modules.blackjack_core import (
    blackjack_recommendation,
    card_points,
    is_soft_shown_total,
    recommend_hit_stand,
)


@pytest.fixture()
def cog():
    return AICog(MagicMock())


# ---------------------------------------------------------------------------
# is_pass_reply — the model's decline-to-comment sentinel
# ---------------------------------------------------------------------------


class TestIsPassReply:
    @pytest.mark.parametrize("text", ["PASS", "pass", " Pass ", '"PASS"', "*pass*", "PASS.", "`pass`"])
    def test_bare_pass_is_filtered(self, text):
        assert is_pass_reply(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "I'll pass on that one",
            "the password is wrong",
            "passing thoughts only",
            "hard pass, that idea is terrible",
            "not a sentinel",
        ],
    )
    def test_real_replies_are_not_filtered(self, text):
        assert is_pass_reply(text) is False


# ---------------------------------------------------------------------------
# _extract_balance_from_text
# ---------------------------------------------------------------------------


class TestExtractBalance:
    def test_balance_label(self, cog):
        assert cog._extract_balance_from_text("Balance: 12,345 🪙") == 12345

    def test_wallet_label(self, cog):
        assert cog._extract_balance_from_text("Wallet: 1,000 🪙") == 1000

    def test_name_colon_amount(self, cog):
        assert cog._extract_balance_from_text("Gary: 500 🪙") == 500

    def test_no_coin_emoji_returns_none(self, cog):
        assert cog._extract_balance_from_text("Balance: 500") is None

    def test_zero_balance(self, cog):
        assert cog._extract_balance_from_text("Balance: 0 🪙") == 0

    def test_returns_none_on_garbage(self, cog):
        assert cog._extract_balance_from_text("nothing here") is None


# ---------------------------------------------------------------------------
# _compute_blackjack_bet
# ---------------------------------------------------------------------------


class TestComputeBlackjackBet:
    def test_below_min_balance_returns_zero(self, cog):
        assert cog._compute_blackjack_bet(0, 1000) == 0

    def test_returns_min_pct_when_below_anchor_ratio(self, cog):
        # balance < 80% of anchor → minimum bet percentage
        result = cog._compute_blackjack_bet(500, 1000)
        assert result == cog.BJ_BET_PCT_MIN

    def test_returns_base_pct_at_anchor(self, cog):
        anchor = 5000
        result = cog._compute_blackjack_bet(anchor, anchor)
        assert cog.BJ_BET_PCT_MIN <= result <= cog.BJ_BET_PCT_MAX

    def test_scales_up_above_anchor(self, cog):
        low = cog._compute_blackjack_bet(5000, 5000)
        high = cog._compute_blackjack_bet(8000, 5000)
        assert high >= low

    def test_caps_at_max_pct(self, cog):
        result = cog._compute_blackjack_bet(100_000, 5000)
        assert result <= cog.BJ_BET_PCT_MAX


# ---------------------------------------------------------------------------
# blackjack_core helpers used by the Silas gambler
# ---------------------------------------------------------------------------


class TestBjRankValue:
    @pytest.mark.parametrize("rank,expected", [("J", 10), ("Q", 10), ("K", 10), ("A", 11), ("2", 2), ("10", 10)])
    def test_rank_values(self, rank, expected):
        assert card_points(rank) == expected

    def test_case_insensitive(self):
        assert card_points("j") == 10


class TestBjIsSoft:
    def test_ace_six_is_soft_17(self):
        assert is_soft_shown_total(["A", "6"], 17) is True

    def test_ace_six_ten_is_hard_17(self):
        # ace forced to count as 1 to avoid bust
        assert is_soft_shown_total(["A", "6", "10"], 17) is False

    def test_no_ace_never_soft(self):
        assert is_soft_shown_total(["10", "7"], 17) is False

    def test_two_aces_twelve_is_soft(self):
        assert is_soft_shown_total(["A", "A"], 12) is True


class TestRecommendBlackjackAction:
    def test_stands_on_hard_17_plus(self):
        assert recommend_hit_stand(17, 6, False) == "stand"
        assert recommend_hit_stand(20, 10, False) == "stand"

    def test_hits_hard_16_vs_9(self):
        assert recommend_hit_stand(16, 9, False) == "hit"

    def test_stands_hard_13_vs_5(self):
        assert recommend_hit_stand(13, 5, False) == "stand"

    def test_hits_soft_18_vs_9(self):
        assert recommend_hit_stand(18, 9, True) == "hit"

    def test_stands_soft_19(self):
        assert recommend_hit_stand(19, 6, True) == "stand"

    def test_hits_hard_8(self):
        assert recommend_hit_stand(8, 7, False) == "hit"


class TestSharedBlackjackCore:
    """Both cogs must draw their blackjack knowledge from blackjack_core."""

    def test_economy_reexports_are_core_objects(self):
        from modules import blackjack_core, economy

        assert economy.card_points is blackjack_core.card_points
        assert economy.blackjack_recommendation is blackjack_core.blackjack_recommendation
        assert economy.BLACKJACK_RULES is blackjack_core.BLACKJACK_RULES

    def test_ai_cog_no_longer_defines_private_copies(self, cog):
        for name in ("_bj_rank_value", "_bj_is_soft", "_recommend_blackjack_action"):
            assert not hasattr(cog, name)

    @pytest.mark.parametrize(
        "total,dealer_up,soft",
        [(17, 6, False), (16, 9, False), (13, 5, False), (18, 9, True), (19, 6, True), (12, 4, False), (12, 3, False)],
    )
    def test_hit_stand_table_agrees_with_full_strategy_when_restricted(self, total, dealer_up, soft):
        # Wherever the full table already answers hit/stand, the restricted
        # table must give the same answer (spot checks, not exhaustive).
        hands = {
            (17, False): [("10", "♠"), ("7", "♥")],
            (16, False): [("10", "♠"), ("6", "♥")],
            (13, False): [("10", "♠"), ("3", "♥")],
            (12, False): [("10", "♠"), ("2", "♥")],
            (18, True): [("A", "♠"), ("7", "♥")],
            (19, True): [("A", "♠"), ("8", "♥")],
        }
        full_action, _ = blackjack_recommendation(hands[(total, soft)], (str(dealer_up), "♣"))
        if full_action in ("hit", "stand"):
            assert recommend_hit_stand(total, dealer_up, soft) == full_action


# ---------------------------------------------------------------------------
# _extract_card_ranks
# ---------------------------------------------------------------------------


class TestExtractCardRanks:
    def test_plain_cards(self, cog):
        assert cog._extract_card_ranks("J♦ Q♠") == ["J", "Q"]

    def test_backtick_wrapped(self, cog):
        assert cog._extract_card_ranks("`Q♠` `2♠`") == ["Q", "2"]

    def test_ten(self, cog):
        assert cog._extract_card_ranks("10♣") == ["10"]

    def test_ignores_placeholder(self, cog):
        # ?? hidden cards should not extract
        assert cog._extract_card_ranks("J♦ ??") == ["J"]

    def test_empty_segment(self, cog):
        assert cog._extract_card_ranks("") == []


# ---------------------------------------------------------------------------
# _parse_blackjack_prompt
# ---------------------------------------------------------------------------


class TestParseBlackjackPrompt:
    def test_parses_total_and_dealer(self, cog):
        text = "Dealer: J♦ 🂠\nGary (12): Q♠ 2♠"
        result = cog._parse_blackjack_prompt(text)
        assert result is not None
        total, dealer_up, soft = result
        assert total == 12
        assert dealer_up == 10  # J = 10

    def test_backtick_format(self, cog):
        text = "Dealer: `J♦` ??\nGary (17): `10♠` `7♣`"
        result = cog._parse_blackjack_prompt(text)
        assert result is not None
        total, dealer_up, soft = result
        assert total == 17
        assert dealer_up == 10

    def test_soft_hand_detected(self, cog):
        text = "Dealer: `6♦` ??\nGary (17): `A♠` `6♥`"
        result = cog._parse_blackjack_prompt(text)
        assert result is not None
        total, dealer_up, soft = result
        assert total == 17
        assert soft is True

    def test_missing_dealer_returns_none(self, cog):
        assert cog._parse_blackjack_prompt("Gary (12): Q♠ 2♠") is None

    def test_missing_gary_returns_none(self, cog):
        assert cog._parse_blackjack_prompt("Dealer: J♦ 🂠") is None


# ---------------------------------------------------------------------------
# _parse_silas_hangman
# ---------------------------------------------------------------------------


class TestParseSilasHangman:
    def test_active_game(self, cog):
        text = "Word: _ _ _ e _\nGuessed: a, t\nLives left: 4"
        result = cog._parse_silas_hangman(text)
        assert result is not None
        assert result["status"] == "active"
        assert result["lives"] == 4
        assert result["word_pattern"] == ["_", "_", "_", "e", "_"]
        assert "a" in result["guessed"]
        assert "t" in result["guessed"]

    def test_game_over_lost(self, cog):
        result = cog._parse_silas_hangman("game over — the word was apple")
        assert result is not None
        assert result["status"] == "lost"

    def test_game_won(self, cog):
        result = cog._parse_silas_hangman("You got it! The word was apple!")
        assert result is not None
        assert result["status"] == "won"

    def test_none_on_unrecognised(self, cog):
        assert cog._parse_silas_hangman("random message with nothing relevant") is None

    def test_no_guesses_yet(self, cog):
        text = "Word: _ _ _ _ _\nGuessed: none\nLives left: 6"
        result = cog._parse_silas_hangman(text)
        assert result is not None
        assert result["guessed"] == set()


# ---------------------------------------------------------------------------
# _pick_hangman_letter
# ---------------------------------------------------------------------------


class TestPickHangmanLetter:
    def test_returns_a_letter(self, cog):
        # simple 5-letter word, no guesses yet
        letter = cog._pick_hangman_letter(["_", "_", "_", "_", "_"], set())
        assert letter is not None
        assert len(letter) == 1
        assert letter.isalpha()

    def test_avoids_already_guessed(self, cog):
        guessed = {"e", "a", "t", "r", "s"}
        letter = cog._pick_hangman_letter(["_", "_", "_", "_", "_"], guessed)
        if letter is not None:
            assert letter not in guessed

    def test_with_revealed_letters(self, cog):
        # "apple" with a revealed
        letter = cog._pick_hangman_letter(["a", "_", "_", "_", "_"], {"a"})
        assert letter != "a"


# ---------------------------------------------------------------------------
# Silas hangman refusals — cooldown and "Already Playing"
#
# Both of these are messages Silas sends INSTEAD of a board, and both used to
# leave Gary's state saying "no hangman running, cooldown long expired" — so
# the next tick sent `!hm` into the same refusal, every tick, for days.
# ---------------------------------------------------------------------------

_GAMBLE_CHANNEL_ID = 100000000000000009

# Exactly as Silas sends them: an embed title plus description, which
# extract_message_text() joins with a newline before the handler sees it.
COOLDOWN_TEXT = "Cooldown\nYou can start another hangman in 5h 54m."
COOLDOWN_ZERO_TEXT = "Cooldown\nYou can start another hangman in 0h 0m."
HANGMAN_BUSY_TEXT = "Already Playing\nJust type your guess directly!"
BLACKJACK_BUSY_TEXT = "Already Playing\nFinish your current blackjack hand first."


class FakeGambleChannel:
    def __init__(self, channel_id=_GAMBLE_CHANNEL_ID, guild_id=555):
        self.id = channel_id
        self.guild = SimpleNamespace(id=guild_id)
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)
        return MagicMock()


def _gamble_cog(channel=None):
    """An AICog wired to a fake gamble channel, with the feature enabled."""
    channel = channel or FakeGambleChannel()
    shared.runtime_settings["gary_gamble_enabled"] = True
    shared.runtime_settings["gary_gamble_channel_id"] = channel.id
    bot = MagicMock()
    bot.get_channel.return_value = channel
    cog = AICog(bot)
    return cog, channel


def _silas_message(channel):
    return SimpleNamespace(channel=channel, guild=SimpleNamespace(id=channel.guild.id))


def _ready_to_gamble(cog, *, balance=10029):
    """Skip past scratch-offs and balance discovery to the hangman branches."""
    now = datetime.now(timezone.utc)
    cog.gamble_state["day"] = cog._scratch_reset_key(now)
    cog.gamble_state["scratchoffs_used"] = 3
    cog.gamble_state["last_known_balance"] = balance
    cog.gamble_state["session_anchor_balance"] = balance
    cog.gamble_state["session_id"] = 1


class TestParseSilasHangmanCooldown:
    def test_hours_and_minutes(self, cog):
        assert cog._parse_silas_hangman_cooldown(COOLDOWN_TEXT) == timedelta(hours=5, minutes=54)

    def test_zero_parses_as_zero(self, cog):
        assert cog._parse_silas_hangman_cooldown(COOLDOWN_ZERO_TEXT) == timedelta(0)

    def test_minutes_only(self, cog):
        assert cog._parse_silas_hangman_cooldown("You can start another hangman in 45m.") == timedelta(minutes=45)

    def test_hours_only(self, cog):
        assert cog._parse_silas_hangman_cooldown("You can start another hangman in 2h.") == timedelta(hours=2)

    def test_no_countdown_returns_none(self, cog):
        assert cog._parse_silas_hangman_cooldown("Cooldown") is None


class TestHangmanCooldownLeft:
    def test_ready_when_nothing_recorded(self, cog):
        assert cog._hangman_cooldown_left(datetime.now(timezone.utc)) == timedelta(0)

    def test_refusal_countdown_is_respected(self, cog):
        now = datetime.now(timezone.utc)
        cog.gamble_state["hangman_cooldown_until"] = now + timedelta(hours=5)
        assert cog._hangman_cooldown_left(now) > timedelta(hours=4)

    def test_stale_end_time_alone_reads_ready(self, cog):
        now = datetime.now(timezone.utc)
        cog.gamble_state["hangman_ended_at"] = now - timedelta(days=3)
        assert cog._hangman_cooldown_left(now) == timedelta(0)

    def test_takes_the_longer_of_the_two_clocks(self, cog):
        now = datetime.now(timezone.utc)
        # A game finished 10 minutes ago, so ~5h50m of the 6h wait is left;
        # an older refusal only claimed 1h. The finished game must win.
        cog.gamble_state["hangman_ended_at"] = now - timedelta(minutes=10)
        cog.gamble_state["hangman_cooldown_until"] = now + timedelta(hours=1)
        assert cog._hangman_cooldown_left(now) > timedelta(hours=5)


class TestSilasRefusals:
    async def test_cooldown_records_silas_countdown(self):
        cog, channel = _gamble_cog()
        cog.gamble_state["hangman_active"] = True
        await cog._handle_silas_gambling_message(_silas_message(channel), COOLDOWN_TEXT)

        assert channel.sent == []
        assert cog.gamble_state["hangman_active"] is False
        left = cog._hangman_cooldown_left(datetime.now(timezone.utc))
        assert timedelta(hours=5) < left <= timedelta(hours=5, minutes=54)

    async def test_zero_countdown_backs_off_by_the_floor_not_six_hours(self):
        cog, channel = _gamble_cog()
        await cog._handle_silas_gambling_message(_silas_message(channel), COOLDOWN_ZERO_TEXT)
        left = cog._hangman_cooldown_left(datetime.now(timezone.utc))
        # "0h 0m" is a rounded-down refusal, not permission to retry now — but
        # it is still Silas's answer, so it must not be read as "no countdown
        # here" and replaced with the full six hours.
        assert timedelta(minutes=4) < left <= AICog.HM_COOLDOWN_MIN_BACKOFF

    async def test_refusal_stops_the_next_tick_resending_hm(self):
        """The production loop, end to end: three days of `!hm` every tick."""
        cog, channel = _gamble_cog()
        _ready_to_gamble(cog)
        cog.gamble_state["hangman_ended_at"] = datetime.now(timezone.utc) - timedelta(days=3)

        assert await cog.run_gamble_step(bypass_cooldown=True)
        assert channel.sent == ["!hm"]

        await cog._handle_silas_gambling_message(_silas_message(channel), COOLDOWN_TEXT)
        await cog.run_gamble_step(bypass_cooldown=True)
        assert channel.sent.count("!hm") == 1

    async def test_hangman_busy_is_adopted_not_stood_on(self):
        cog, channel = _gamble_cog()
        await cog._handle_silas_gambling_message(_silas_message(channel), HANGMAN_BUSY_TEXT)

        assert "stand" not in channel.sent
        assert len(channel.sent) == 1
        assert channel.sent[0].isalpha() and len(channel.sent[0]) == 1
        assert cog.gamble_state["hangman_active"] is True

    async def test_adopted_game_stops_the_next_tick_resending_hm(self):
        cog, channel = _gamble_cog()
        _ready_to_gamble(cog)
        cog.gamble_state["hangman_ended_at"] = datetime.now(timezone.utc) - timedelta(days=3)

        await cog._handle_silas_gambling_message(_silas_message(channel), HANGMAN_BUSY_TEXT)
        await cog.run_gamble_step(bypass_cooldown=True)
        assert "!hm" not in channel.sent

    async def test_blackjack_busy_still_stands(self):
        cog, channel = _gamble_cog()
        await cog._handle_silas_gambling_message(_silas_message(channel), BLACKJACK_BUSY_TEXT)
        assert channel.sent == ["stand"]
        assert cog.gamble_state["hangman_active"] is False

    async def test_adopted_game_plays_on_from_the_board_silas_renders(self):
        cog, channel = _gamble_cog()
        message = _silas_message(channel)
        await cog._handle_silas_gambling_message(message, HANGMAN_BUSY_TEXT)
        probe = channel.sent[0]

        await cog._handle_silas_gambling_message(message, "Word: _ _ _ e _\nGuessed: e\nLives left: 5")
        assert len(channel.sent) == 2
        assert channel.sent[1] != probe
