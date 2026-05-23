"""Unit tests for the pure helper functions in AICog.

No Discord gateway, no Ollama — these test the blackjack/hangman parsing and
Gary autonomous-gambling math that can be exercised in isolation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from modules.ai import AICog


@pytest.fixture()
def cog():
    return AICog(MagicMock())


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
# _bj_rank_value
# ---------------------------------------------------------------------------


class TestBjRankValue:
    @pytest.mark.parametrize("rank,expected", [("J", 10), ("Q", 10), ("K", 10), ("A", 11), ("2", 2), ("10", 10)])
    def test_rank_values(self, cog, rank, expected):
        assert cog._bj_rank_value(rank) == expected

    def test_case_insensitive(self, cog):
        assert cog._bj_rank_value("j") == 10


# ---------------------------------------------------------------------------
# _bj_is_soft
# ---------------------------------------------------------------------------


class TestBjIsSoft:
    def test_ace_six_is_soft_17(self, cog):
        assert cog._bj_is_soft(["A", "6"], 17) is True

    def test_ace_six_ten_is_hard_17(self, cog):
        # ace forced to count as 1 to avoid bust
        assert cog._bj_is_soft(["A", "6", "10"], 17) is False

    def test_no_ace_never_soft(self, cog):
        assert cog._bj_is_soft(["10", "7"], 17) is False

    def test_two_aces_twelve_is_soft(self, cog):
        assert cog._bj_is_soft(["A", "A"], 12) is True


# ---------------------------------------------------------------------------
# _recommend_blackjack_action
# ---------------------------------------------------------------------------


class TestRecommendBlackjackAction:
    def test_stands_on_hard_17_plus(self, cog):
        assert cog._recommend_blackjack_action(17, 6, False) == "stand"
        assert cog._recommend_blackjack_action(20, 10, False) == "stand"

    def test_hits_hard_16_vs_9(self, cog):
        assert cog._recommend_blackjack_action(16, 9, False) == "hit"

    def test_stands_hard_13_vs_5(self, cog):
        assert cog._recommend_blackjack_action(13, 5, False) == "stand"

    def test_hits_soft_18_vs_9(self, cog):
        assert cog._recommend_blackjack_action(18, 9, True) == "hit"

    def test_stands_soft_19(self, cog):
        assert cog._recommend_blackjack_action(19, 6, True) == "stand"

    def test_hits_hard_8(self, cog):
        assert cog._recommend_blackjack_action(8, 7, False) == "hit"


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
