"""Unit tests for the pure helper functions in AICog.

No Discord gateway, no Ollama — these test the blackjack/hangman parsing and
Gary autonomous-gambling math that can be exercised in isolation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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
