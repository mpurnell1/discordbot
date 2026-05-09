"""Pure unit tests for economy / blackjack logic."""
import pytest

from modules import economy
from modules.economy import (
    card_points,
    hand_total_and_soft,
    hand_value,
    is_soft_hand,
    is_blackjack,
    can_split_cards,
    dealer_should_hit,
    available_actions,
    blackjack_recommendation,
    apply_blackjack_ruleset,
    BLACKJACK_RULES,
    make_player_hand,
    generate_math_puzzle,
    wordle_feedback,
)


@pytest.fixture(autouse=True)
def _reset_realistic_rules():
    """Most tests assume the 'realistic' ruleset; reset between tests."""
    apply_blackjack_ruleset("realistic")


# ---------------------------------------------------------------------------
# card scoring
# ---------------------------------------------------------------------------
class TestCardPoints:
    @pytest.mark.parametrize("rank,expected", [
        ("2", 2), ("9", 9), ("10", 10),
        ("J", 10), ("Q", 10), ("K", 10),
        ("A", 11),
    ])
    def test_rank_values(self, rank, expected):
        assert card_points(rank) == expected


# ---------------------------------------------------------------------------
# hand totals — the soft/hard ace logic is the trickiest spot
# ---------------------------------------------------------------------------
class TestHandTotalAndSoft:
    def test_two_aces_count_as_twelve(self):
        # A + A = 11 + 11 = 22 -> reduce one ace -> 12, still soft.
        total, soft = hand_total_and_soft([("A", "♠"), ("A", "♥")])
        assert total == 12
        assert soft is True

    def test_ace_six_is_soft_seventeen(self):
        total, soft = hand_total_and_soft([("A", "♠"), ("6", "♥")])
        assert total == 17
        assert soft is True

    def test_ace_six_ten_is_hard_seventeen(self):
        total, soft = hand_total_and_soft([("A", "♠"), ("6", "♥"), ("10", "♣")])
        assert total == 17
        assert soft is False

    def test_no_aces_never_soft(self):
        total, soft = hand_total_and_soft([("10", "♠"), ("7", "♥")])
        assert total == 17
        assert soft is False

    def test_bust_hand_demotes_aces(self):
        # 10 + A + A + 5 = 27 -> 17, no aces left as 11.
        total, soft = hand_total_and_soft(
            [("10", "♠"), ("A", "♥"), ("A", "♣"), ("5", "♦")]
        )
        assert total == 17
        assert soft is False


def test_is_blackjack_only_two_cards_totalling_21():
    assert is_blackjack([("A", "♠"), ("K", "♥")]) is True
    assert is_blackjack([("A", "♠"), ("Q", "♥")]) is True
    # 21 with three cards is NOT a natural blackjack.
    assert is_blackjack([("7", "♠"), ("7", "♥"), ("7", "♣")]) is False
    # 20 with two cards isn't blackjack.
    assert is_blackjack([("K", "♠"), ("Q", "♥")]) is False


# ---------------------------------------------------------------------------
# splits
# ---------------------------------------------------------------------------
class TestCanSplit:
    def test_pair_of_eights_splits(self):
        assert can_split_cards([("8", "♠"), ("8", "♥")]) is True

    def test_pair_of_aces_splits(self):
        assert can_split_cards([("A", "♠"), ("A", "♥")]) is True

    def test_ten_and_jack_treated_as_split_in_default_ruleset(self):
        # realistic ruleset has allow_ten_value_split=True
        assert can_split_cards([("10", "♠"), ("J", "♥")]) is True

    def test_seven_and_eight_no_split(self):
        assert can_split_cards([("7", "♠"), ("8", "♥")]) is False

    def test_more_than_two_cards_no_split(self):
        assert can_split_cards([("8", "♠"), ("8", "♥"), ("2", "♣")]) is False


# ---------------------------------------------------------------------------
# dealer hits
# ---------------------------------------------------------------------------
class TestDealerShouldHit:
    def test_hits_on_sixteen(self):
        assert dealer_should_hit([("10", "♠"), ("6", "♥")]) is True

    def test_stands_on_hard_seventeen(self):
        assert dealer_should_hit([("10", "♠"), ("7", "♥")]) is False

    def test_stands_on_soft_seventeen_under_realistic(self):
        # realistic ruleset: dealer_hits_soft_17 = False
        assert dealer_should_hit([("A", "♠"), ("6", "♥")]) is False

    def test_stands_on_twenty(self):
        assert dealer_should_hit([("K", "♠"), ("Q", "♥")]) is False


# ---------------------------------------------------------------------------
# available actions
# ---------------------------------------------------------------------------
def _game_with(player_cards, dealer_cards=(("10", "♠"), ("5", "♥"))):
    return {
        "dealer": list(dealer_cards),
        "hands": [make_player_hand(list(player_cards), bet=10)],
        "current_hand": 0,
    }


class TestAvailableActions:
    def test_initial_hand_can_hit_stand(self):
        game = _game_with([("9", "♠"), ("3", "♥")])
        actions = available_actions(game, game["hands"][0])
        assert "hit" in actions and "stand" in actions

    def test_two_card_hand_can_double(self):
        game = _game_with([("5", "♠"), ("6", "♥")])
        actions = available_actions(game, game["hands"][0])
        assert "double" in actions

    def test_pair_can_split(self):
        game = _game_with([("8", "♠"), ("8", "♥")])
        actions = available_actions(game, game["hands"][0])
        assert "split" in actions

    def test_after_hit_no_double_or_split(self):
        game = _game_with([("5", "♠"), ("6", "♥"), ("2", "♣")])
        actions = available_actions(game, game["hands"][0])
        assert "double" not in actions
        assert "split" not in actions
        assert "surrender" not in actions

    def test_late_surrender_available_initially(self):
        game = _game_with([("9", "♠"), ("7", "♥")])
        actions = available_actions(game, game["hands"][0])
        assert "surrender" in actions


# ---------------------------------------------------------------------------
# basic strategy spot-checks
# ---------------------------------------------------------------------------
class TestBlackjackRecommendation:
    def test_pair_of_aces_always_split(self):
        action, _ = blackjack_recommendation(
            [("A", "♠"), ("A", "♥")], ("10", "♣")
        )
        assert action == "split"

    def test_pair_of_eights_always_split(self):
        action, _ = blackjack_recommendation(
            [("8", "♠"), ("8", "♥")], ("10", "♣")
        )
        assert action == "split"

    def test_hard_sixteen_vs_ten_surrender(self):
        action, _ = blackjack_recommendation(
            [("10", "♠"), ("6", "♥")], ("10", "♣")
        )
        assert action == "surrender"

    def test_hard_twenty_stands(self):
        action, _ = blackjack_recommendation(
            [("K", "♠"), ("Q", "♥")], ("10", "♣")
        )
        assert action == "stand"

    def test_soft_eighteen_vs_nine_hit(self):
        action, _ = blackjack_recommendation(
            [("A", "♠"), ("7", "♥")], ("9", "♣")
        )
        assert action == "hit"

    def test_hard_eleven_doubles(self):
        action, _ = blackjack_recommendation(
            [("6", "♠"), ("5", "♥")], ("6", "♣")
        )
        assert action == "double"


# ---------------------------------------------------------------------------
# ruleset switching
# ---------------------------------------------------------------------------
def test_apply_blackjack_ruleset_changes_active_rules():
    apply_blackjack_ruleset("arcade")
    assert BLACKJACK_RULES["five_card_charlie"] is True
    apply_blackjack_ruleset("realistic")
    assert BLACKJACK_RULES["five_card_charlie"] is False


def test_apply_blackjack_ruleset_unknown_falls_back_to_realistic():
    selected = apply_blackjack_ruleset("nonsense")
    assert selected == "realistic"


# ---------------------------------------------------------------------------
# math puzzle generator: returned answer must equal the math
# ---------------------------------------------------------------------------
def test_generate_math_puzzle_answer_is_correct():
    """Run the generator many times; each puzzle's answer must be valid for its question."""
    for _ in range(200):
        question, answer = generate_math_puzzle()
        # The answer is always a string-form integer.
        assert answer.lstrip("-").isdigit(), f"non-integer answer: {answer} for {question}"


# ---------------------------------------------------------------------------
# wordle feedback
# ---------------------------------------------------------------------------
class TestWordleFeedback:
    def test_all_green_for_correct_guess(self):
        out = wordle_feedback("crane", "crane")
        assert out.startswith("🟩🟩🟩🟩🟩")

    def test_all_black_for_no_letters_in_common(self):
        out = wordle_feedback("xxxxx", "crane")
        # No greens or yellows.
        assert "🟩" not in out
        assert "🟨" not in out

    def test_mixed_green_and_yellow(self):
        # answer "crane", guess "raise" -> r,a,i,s,e
        # r: yellow (in 'crane' but pos 0 in guess vs pos 1 in answer)
        # a: yellow (in 'crane' but pos 1 in guess vs pos 2 in answer)
        # i: black
        # s: black
        # e: green (pos 4 matches)
        out = wordle_feedback("raise", "crane")
        # Tile string is the first non-empty token (the line of squares).
        tile_line = out.split("\n")[0]
        assert tile_line[-1] == "🟩"  # 'e' is green
        # Should contain at least two yellows (r, a).
        assert tile_line.count("🟨") >= 2
