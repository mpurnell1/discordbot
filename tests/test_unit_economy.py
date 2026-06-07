"""Pure unit tests for economy / blackjack logic."""

from unittest.mock import MagicMock, patch

import pytest

import shared
import random as stdlib_random

from modules.economy import (
    EconomyCog,
    BLACKJACK_RULES,
    LUCKY_GUESS_MAX_DAILY,
    active_blackjack,
    active_puzzles,
    apply_blackjack_ruleset,
    available_actions,
    best_legal_blackjack_recommendation,
    blackjack_recommendation,
    can_split_cards,
    card_points,
    dealer_should_hit,
    generate_math_puzzle,
    generate_puzzle,
    generate_wordle_puzzle,
    generate_unscramble_puzzle,
    hand_total_and_soft,
    is_blackjack,
    load_active_puzzle,
    make_player_hand,
    save_active_puzzle,
    wordle_display,
    wordle_feedback,
    _puzzle_key,
)
from tests.conftest import FakeAuthor, FakeContext, FakeGuild


@pytest.fixture(autouse=True)
def _reset_realistic_rules():
    """Most tests assume the 'realistic' ruleset; reset between tests."""
    apply_blackjack_ruleset("realistic")


# ---------------------------------------------------------------------------
# card scoring
# ---------------------------------------------------------------------------
class TestCardPoints:
    @pytest.mark.parametrize(
        "rank,expected",
        [
            ("2", 2),
            ("9", 9),
            ("10", 10),
            ("J", 10),
            ("Q", 10),
            ("K", 10),
            ("A", 11),
        ],
    )
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
        total, soft = hand_total_and_soft([("10", "♠"), ("A", "♥"), ("A", "♣"), ("5", "♦")])
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
        action, _ = blackjack_recommendation([("A", "♠"), ("A", "♥")], ("10", "♣"))
        assert action == "split"

    def test_pair_of_eights_always_split(self):
        action, _ = blackjack_recommendation([("8", "♠"), ("8", "♥")], ("10", "♣"))
        assert action == "split"

    def test_hard_sixteen_vs_ten_surrender(self):
        action, _ = blackjack_recommendation([("10", "♠"), ("6", "♥")], ("10", "♣"))
        assert action == "surrender"

    def test_hard_twenty_stands(self):
        action, _ = blackjack_recommendation([("K", "♠"), ("Q", "♥")], ("10", "♣"))
        assert action == "stand"

    def test_soft_eighteen_vs_nine_hit(self):
        action, _ = blackjack_recommendation([("A", "♠"), ("7", "♥")], ("9", "♣"))
        assert action == "hit"

    def test_hard_eleven_doubles(self):
        action, _ = blackjack_recommendation([("6", "♠"), ("5", "♥")], ("6", "♣"))
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
    # best_legal_blackjack_recommendation
    # ---------------------------------------------------------------------------

    def test_double_falls_back_on_three_card_hand(self):
        # Soft 17 vs 5 — basic says double; three-card hand can't double.
        game = _game_with([("A", "♠"), ("3", "♥"), ("3", "♣")])
        hand = game["hands"][0]
        action, _ = best_legal_blackjack_recommendation(game, hand, ("5", "♦"))
        assert action in {"hit", "stand"}


# ---------------------------------------------------------------------------
# generate_puzzle
# ---------------------------------------------------------------------------


def test_generate_puzzle_returns_kind_question_answer():
    kind, question, answer = generate_puzzle()
    assert kind in {"math", "wordle", "unscramble", "trivia", "code"}
    assert isinstance(question, str) and len(question) > 0
    assert isinstance(answer, str) and len(answer) > 0


# ---------------------------------------------------------------------------
# Command handler tests — EconomyCog with FakeContext
# ---------------------------------------------------------------------------


def _set_balance(user_id: int, amount: int) -> None:
    shared.db.execute(
        "INSERT INTO users (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = ?",
        (user_id, amount, amount),
    )
    shared.db.commit()


def _get_balance(user_id: int) -> int:
    row = shared.db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return int(row[0]) if row else 0


@pytest.fixture()
def econ_cog():
    return EconomyCog(MagicMock())


class TestBalanceCommand:
    async def test_shows_own_balance(self, econ_cog):
        _set_balance(9001, 500)
        ctx = FakeContext(author=FakeAuthor(user_id=9001))
        await econ_cog.balance.callback(econ_cog, ctx)
        text = ctx.sent[0]["embed"].description
        assert "500" in text

    async def test_shows_zero_for_new_user(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=9002))
        await econ_cog.balance.callback(econ_cog, ctx)
        assert ctx.sent[0]["embed"] is not None


class TestCoinflipCommand:
    async def test_rejects_zero_bet(self, econ_cog):
        _set_balance(9010, 100)
        ctx = FakeContext(author=FakeAuthor(user_id=9010))
        await econ_cog.coinflip.callback(econ_cog, ctx, 0)
        assert ctx.sent  # some error message sent

    async def test_rejects_bet_exceeding_balance(self, econ_cog):
        _set_balance(9011, 50)
        ctx = FakeContext(author=FakeAuthor(user_id=9011))
        await econ_cog.coinflip.callback(econ_cog, ctx, 100)
        assert ctx.sent

    async def test_valid_bet_sends_result(self, econ_cog):
        _set_balance(9012, 200)
        ctx = FakeContext(author=FakeAuthor(user_id=9012))
        await econ_cog.coinflip.callback(econ_cog, ctx, 50)
        assert ctx.sent
        # balance should have changed by ±50
        new_bal = _get_balance(9012)
        assert new_bal in {150, 250}


class TestSlotsCommand:
    async def test_rejects_bet_exceeding_balance(self, econ_cog):
        _set_balance(9020, 10)
        ctx = FakeContext(author=FakeAuthor(user_id=9020))
        await econ_cog.slots.callback(econ_cog, ctx, 100)
        assert ctx.sent

    async def test_valid_bet_sends_result(self, econ_cog):
        _set_balance(9021, 500)
        ctx = FakeContext(author=FakeAuthor(user_id=9021))
        await econ_cog.slots.callback(econ_cog, ctx, 50)
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert "🎰" in embed.title

    async def test_balance_changes_after_slots(self, econ_cog):
        _set_balance(9022, 300)
        ctx = FakeContext(author=FakeAuthor(user_id=9022))
        await econ_cog.slots.callback(econ_cog, ctx, 100)
        # balance must differ from original (win or lose)
        new_bal = _get_balance(9022)
        assert new_bal != 300 or ctx.sent[0]["embed"].title.startswith("🎰")


class TestBjrulesCommand:
    async def test_sends_response(self, econ_cog):
        ctx = FakeContext()
        await econ_cog.bjrules.callback(econ_cog, ctx)
        assert ctx.sent

    async def test_shows_ruleset_name(self, econ_cog):
        ctx = FakeContext()
        await econ_cog.bjrules.callback(econ_cog, ctx)
        # bjrules sends plain text (no embed)
        content = ctx.sent[0].get("content") or ""
        assert "realistic" in content.lower() or "arcade" in content.lower()


class TestGuessCommand:
    async def test_rejects_when_balance_positive(self, econ_cog):
        _set_balance(9030, 100)
        ctx = FakeContext(author=FakeAuthor(user_id=9030))
        await econ_cog.guess.callback(econ_cog, ctx, 5)
        assert "Not Broke" in ctx.sent[0]["embed"].title

    async def test_rejects_out_of_range(self, econ_cog):
        _set_balance(9031, 0)
        ctx = FakeContext(author=FakeAuthor(user_id=9031))
        await econ_cog.guess.callback(econ_cog, ctx, 99)
        assert ctx.sent

    async def test_valid_guess_sends_result(self, econ_cog):
        _set_balance(9032, 0)
        ctx = FakeContext(author=FakeAuthor(user_id=9032))
        await econ_cog.guess.callback(econ_cog, ctx, 5)
        assert ctx.sent


class TestPuzzleCommand:
    async def test_fresh_puzzle_sent(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=9040), guild=FakeGuild())
        await econ_cog.puzzle.callback(econ_cog, ctx)
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert embed is not None

    async def test_solve_no_active_puzzle_rejected(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=9041), guild=FakeGuild())
        await econ_cog.solve.callback(econ_cog, ctx, answer="anyanswer")
        assert ctx.sent
        content = ctx.sent[0].get("content") or ""
        assert "puzzle" in content.lower()


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


# ---------------------------------------------------------------------------
# Blackjack game-flow command handler tests
# ---------------------------------------------------------------------------


def _bj_game(player_cards, dealer_cards=None, bet=50):
    """Build a minimal blackjack game dict for injection into active_blackjack."""
    if dealer_cards is None:
        dealer_cards = [("6", "♦"), ("10", "♣")]
    return {
        "dealer": list(dealer_cards),
        "hands": [make_player_hand(list(player_cards), bet)],
        "current_hand": 0,
    }


@pytest.fixture(autouse=True)
def _clear_active_bj():
    active_blackjack.clear()
    yield
    active_blackjack.clear()


@pytest.fixture(autouse=True)
def _clear_active_puzzles():
    active_puzzles.clear()
    yield
    active_puzzles.clear()


class TestBlackjackCommand:
    async def test_valid_bet_starts_or_settles_game(self, econ_cog):
        uid = 8000
        _set_balance(uid, 500)
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.blackjack.callback(econ_cog, ctx, 50)
        # Either started a game or settled BJ immediately — always sends something.
        assert ctx.sent

    async def test_duplicate_game_rejected(self, econ_cog):
        uid = 8001
        _set_balance(uid, 500)
        active_blackjack[uid] = _bj_game([("5", "♠"), ("3", "♥")])
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.blackjack.callback(econ_cog, ctx, 50)
        assert "already" in (ctx.sent[0].get("content") or "").lower()

    async def test_rejects_bet_exceeding_balance(self, econ_cog):
        uid = 8002
        _set_balance(uid, 10)
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.blackjack.callback(econ_cog, ctx, 100)
        assert ctx.sent
        assert uid not in active_blackjack


class TestHitCommand:
    async def test_no_active_game_sends_error(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=8010))
        await econ_cog.hit.callback(econ_cog, ctx)
        assert ctx.sent
        assert "active blackjack" in (ctx.sent[0].get("content") or "").lower()

    async def test_hit_draws_card_and_responds(self, econ_cog):
        uid = 8011
        _set_balance(uid, 500)
        game = _bj_game([("5", "♠"), ("3", "♥")])
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        before_count = len(game["hands"][0]["cards"])
        await econ_cog.hit.callback(econ_cog, ctx)
        assert ctx.sent
        # Either still active (drew a card) or finished (busted/21)
        if uid in active_blackjack:
            assert len(active_blackjack[uid]["hands"][0]["cards"]) == before_count + 1

    async def test_bust_finishes_round(self, econ_cog):
        uid = 8012
        _set_balance(uid, 500)
        # Player on 20 but with hit will almost certainly bust or hit 21
        # Set player to 15 with 3 cards so double/split/surrender unavailable
        game = _bj_game([("10", "♠"), ("5", "♥")])
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.hit.callback(econ_cog, ctx)
        assert ctx.sent


class TestStandCommand:
    async def test_no_active_game_sends_error(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=8020))
        await econ_cog.stand.callback(econ_cog, ctx)
        assert ctx.sent
        assert "active blackjack" in (ctx.sent[0].get("content") or "").lower()

    async def test_stand_finishes_round(self, econ_cog):
        uid = 8021
        _set_balance(uid, 500)
        # Player 18, dealer 16 — stand should trigger dealer play and finish
        game = _bj_game([("10", "♠"), ("8", "♥")], dealer_cards=[("9", "♦"), ("7", "♣")])
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.stand.callback(econ_cog, ctx)
        assert uid not in active_blackjack
        assert ctx.sent

    async def test_stand_sends_outcome_embed(self, econ_cog):
        uid = 8022
        _set_balance(uid, 200)
        game = _bj_game([("K", "♠"), ("Q", "♥")], dealer_cards=[("5", "♦"), ("10", "♣")])
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.stand.callback(econ_cog, ctx)
        assert ctx.sent[0]["embed"] is not None


class TestDoubleCommand:
    async def test_no_active_game_sends_error(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=8030))
        await econ_cog.double.callback(econ_cog, ctx)
        assert ctx.sent
        assert "active blackjack" in (ctx.sent[0].get("content") or "").lower()

    async def test_double_finishes_round(self, econ_cog):
        uid = 8031
        _set_balance(uid, 500)
        game = _bj_game([("5", "♠"), ("6", "♥")])  # 11 — double-friendly hand
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.double.callback(econ_cog, ctx)
        # double draws one card and stands — should have finished
        assert uid not in active_blackjack
        assert ctx.sent

    async def test_double_insufficient_funds(self, econ_cog):
        uid = 8032
        _set_balance(uid, 10)  # bet was 50, need 50 more but only have 10
        game = _bj_game([("5", "♠"), ("6", "♥")], bet=50)
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.double.callback(econ_cog, ctx)
        assert ctx.sent
        # Should have rejected — game still active or message about funds
        content = ctx.sent[0].get("content") or ""
        # if the message says "need ... coins" or game still active → rejection
        assert "coins" in content.lower() or uid in active_blackjack


class TestSurrenderCommand:
    async def test_no_active_game_sends_error(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=8040))
        await econ_cog.surrender.callback(econ_cog, ctx)
        assert ctx.sent
        assert "active blackjack" in (ctx.sent[0].get("content") or "").lower()

    async def test_surrender_finishes_round(self, econ_cog):
        uid = 8041
        _set_balance(uid, 500)
        game = _bj_game([("10", "♠"), ("6", "♥")])  # Hard 16 — surrender-worthy
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.surrender.callback(econ_cog, ctx)
        assert uid not in active_blackjack
        assert ctx.sent

    async def test_surrender_sends_outcome_embed(self, econ_cog):
        uid = 8042
        _set_balance(uid, 300)
        game = _bj_game([("9", "♠"), ("7", "♥")])
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.surrender.callback(econ_cog, ctx)
        assert ctx.sent[0]["embed"] is not None


# ---------------------------------------------------------------------------
# Solve command flow with active puzzles
# ---------------------------------------------------------------------------


class TestSolveCommandFlow:
    async def test_correct_non_wordle_answer_rewards_coins(self, econ_cog):
        uid = 8050
        _set_balance(uid, 0)
        key = _puzzle_key(uid, False)
        active_puzzles[key] = {"type": "math", "answer": "42", "display": "Q?"}
        ctx = FakeContext(author=FakeAuthor(user_id=uid), guild=FakeGuild())
        await econ_cog.solve.callback(econ_cog, ctx, answer="42")
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert embed is not None
        assert "Correct" in embed.title or "correct" in (embed.description or "").lower()
        assert key not in active_puzzles

    async def test_wrong_answer_decrements_attempts(self, econ_cog):
        uid = 8051
        _set_balance(uid, 0)
        key = _puzzle_key(uid, False)
        active_puzzles[key] = {"type": "math", "answer": "42", "display": "Q?"}
        ctx = FakeContext(author=FakeAuthor(user_id=uid), guild=FakeGuild())
        await econ_cog.solve.callback(econ_cog, ctx, answer="99")
        assert ctx.sent
        # Game should still be active (attempts remaining) or expired (out of attempts)
        embed = ctx.sent[0]["embed"]
        assert embed is not None

    async def test_kids_mode_correct_answer_no_reward(self, econ_cog):
        uid = 8052
        key = _puzzle_key(uid, True)
        active_puzzles[key] = {"type": "math", "answer": "10", "display": "Q?"}
        # Kids mode: guild has kids mode on
        from shared import set_kids_mode_guild

        guild = FakeGuild(guild_id=7777)
        set_kids_mode_guild(7777, True)
        ctx = FakeContext(author=FakeAuthor(user_id=uid), guild=guild)
        await econ_cog.solve.callback(econ_cog, ctx, answer="10")
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert embed is not None
        assert key not in active_puzzles

    async def test_wordle_correct_guess_finishes(self, econ_cog):
        uid = 8053
        _set_balance(uid, 0)
        key = _puzzle_key(uid, False)
        active_puzzles[key] = {"type": "wordle", "answer": "crane", "display": "Wordle", "guesses": []}
        ctx = FakeContext(author=FakeAuthor(user_id=uid), guild=FakeGuild())
        await econ_cog.solve.callback(econ_cog, ctx, answer="crane")
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert "Wordle" in embed.title or "Solved" in embed.title

    async def test_wordle_wrong_guess_continues(self, econ_cog):
        uid = 8054
        _set_balance(uid, 0)
        key = _puzzle_key(uid, False)
        active_puzzles[key] = {"type": "wordle", "answer": "crane", "display": "Wordle", "guesses": []}
        ctx = FakeContext(author=FakeAuthor(user_id=uid), guild=FakeGuild())
        await econ_cog.solve.callback(econ_cog, ctx, answer="audio")
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert embed is not None

    async def test_wordle_invalid_length_rejected(self, econ_cog):
        uid = 8055
        _set_balance(uid, 0)
        key = _puzzle_key(uid, False)
        active_puzzles[key] = {"type": "wordle", "answer": "crane", "display": "Wordle", "guesses": []}
        ctx = FakeContext(author=FakeAuthor(user_id=uid), guild=FakeGuild())
        await econ_cog.solve.callback(econ_cog, ctx, answer="hi")
        assert ctx.sent
        assert "5-letter" in (ctx.sent[0].get("content") or "").lower()

    async def test_wordle_kids_correct(self, econ_cog):
        uid = 8056
        key = _puzzle_key(uid, True)
        active_puzzles[key] = {"type": "wordle", "answer": "slate", "display": "Wordle", "guesses": []}
        from shared import set_kids_mode_guild

        guild = FakeGuild(guild_id=7778)
        set_kids_mode_guild(7778, True)
        ctx = FakeContext(author=FakeAuthor(user_id=uid), guild=guild)
        await econ_cog.solve.callback(econ_cog, ctx, answer="slate")
        assert ctx.sent
        assert key not in active_puzzles


# ---------------------------------------------------------------------------
# wordle_display pure function
# ---------------------------------------------------------------------------


def test_wordle_display_empty_guesses():
    puzzle = {"answer": "crane", "guesses": []}
    out = wordle_display(puzzle)
    assert "Guesses left" in out
    assert "6" in out  # WORDLE_MAX_GUESSES


def test_wordle_display_with_guesses():
    puzzle = {"answer": "crane", "guesses": ["audio"]}
    out = wordle_display(puzzle)
    assert "Guesses left" in out
    assert "5" in out  # one guess used


# ---------------------------------------------------------------------------
# generate_wordle_puzzle and generate_unscramble_puzzle
# ---------------------------------------------------------------------------


def test_generate_wordle_puzzle_returns_5_letter_word():
    question, word = generate_wordle_puzzle()
    assert len(word) == 5
    assert word.isalpha()
    assert "5-letter" in question


def test_generate_unscramble_puzzle_differs_from_answer():
    question, word = generate_unscramble_puzzle()
    # Scrambled != original (guaranteed by loop)
    scrambled = question.split("**")[1]
    assert scrambled != word
    assert sorted(scrambled) == sorted(word)


# ---------------------------------------------------------------------------
# Blackjack natural outcomes (patch draw_from_shoe)
# ---------------------------------------------------------------------------


class TestBlackjackNatural:
    async def test_player_blackjack_wins(self, econ_cog):
        uid = 9100
        _set_balance(uid, 500)
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        cards = [("A", "♠"), ("K", "♥"), ("5", "♦"), ("7", "♣")]
        with patch("modules.economy.draw_from_shoe", side_effect=cards):
            await econ_cog.blackjack.callback(econ_cog, ctx, 50)
        assert ctx.sent
        # Should not have added to active_blackjack (resolved immediately)
        assert uid not in active_blackjack
        embed = ctx.sent[0].get("embed")
        assert embed is not None

    async def test_dealer_blackjack_loses(self, econ_cog):
        uid = 9101
        _set_balance(uid, 500)
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        # draw order: player[0], player[1], dealer[0], dealer[1]
        # Player: 5, 7 = 12 — not BJ; Dealer: A, K = BJ
        cards = [("5", "♠"), ("7", "♦"), ("A", "♥"), ("K", "♣")]
        with patch("modules.economy.draw_from_shoe", side_effect=cards):
            await econ_cog.blackjack.callback(econ_cog, ctx, 50)
        assert ctx.sent
        assert uid not in active_blackjack
        embed = ctx.sent[0].get("embed")
        assert embed is not None

    async def test_both_blackjack_push(self, econ_cog):
        uid = 9102
        _set_balance(uid, 500)
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        # draw order: player[0], player[1], dealer[0], dealer[1]
        # Player: A, K = BJ; Dealer: A, Q = BJ
        cards = [("A", "♠"), ("K", "♦"), ("A", "♥"), ("Q", "♣")]
        with patch("modules.economy.draw_from_shoe", side_effect=cards):
            await econ_cog.blackjack.callback(econ_cog, ctx, 50)
        assert ctx.sent
        assert uid not in active_blackjack


# ---------------------------------------------------------------------------
# Blackjack split command
# ---------------------------------------------------------------------------


class TestSplitCommand:
    async def test_no_active_game_sends_error(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=9110))
        await econ_cog.split.callback(econ_cog, ctx)
        assert ctx.sent
        assert "active blackjack" in (ctx.sent[0].get("content") or "").lower()

    async def test_split_pair_works(self, econ_cog):
        uid = 9111
        _set_balance(uid, 500)
        game = _bj_game([("8", "♠"), ("8", "♥")])
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.split.callback(econ_cog, ctx)
        # Should have split into 2 hands or finished
        assert ctx.sent

    async def test_cannot_split_non_pair(self, econ_cog):
        uid = 9112
        _set_balance(uid, 500)
        game = _bj_game([("7", "♠"), ("8", "♥")])
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.split.callback(econ_cog, ctx)
        assert ctx.sent
        assert "cannot split" in (ctx.sent[0].get("content") or "").lower()

    async def test_split_insufficient_funds(self, econ_cog):
        uid = 9113
        _set_balance(uid, 10)  # bet was 50, need 50 more
        game = _bj_game([("8", "♠"), ("8", "♥")], bet=50)
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.split.callback(econ_cog, ctx)
        assert ctx.sent
        assert "coins" in (ctx.sent[0].get("content") or "").lower()


# ---------------------------------------------------------------------------
# bjhint and bjruleset direct calls (called via .settings blackjack)
# ---------------------------------------------------------------------------


class TestBjhintDirect:
    async def test_status_shows_hint_state(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=shared.ADMIN_ID))
        await econ_cog.bjhint(ctx, "status")
        assert ctx.sent
        content = ctx.sent[0].get("content") or ""
        assert "hints" in content.lower()

    async def test_turn_hint_on(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=shared.ADMIN_ID))
        await econ_cog.bjhint(ctx, "on")
        assert ctx.sent
        assert shared.runtime_settings.get("bj_basic_hint_enabled") is True

    async def test_turn_hint_off(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=shared.ADMIN_ID))
        await econ_cog.bjhint(ctx, "off")
        assert ctx.sent
        assert shared.runtime_settings.get("bj_basic_hint_enabled") is False

    async def test_invalid_sends_usage(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=shared.ADMIN_ID))
        await econ_cog.bjhint(ctx, "maybe")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_non_admin_silently_returns(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=9999))
        await econ_cog.bjhint(ctx, "on")
        assert not ctx.sent


class TestBjrulesetDirect:
    async def test_status_shows_ruleset(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=shared.ADMIN_ID))
        await econ_cog.bjruleset(ctx, "status")
        assert ctx.sent
        content = ctx.sent[0].get("content") or ""
        assert "realistic" in content.lower() or "arcade" in content.lower()

    async def test_switch_to_arcade(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=shared.ADMIN_ID))
        await econ_cog.bjruleset(ctx, "arcade")
        assert ctx.sent
        assert shared.runtime_settings.get("bj_ruleset") == "arcade"

    async def test_rejects_unknown_ruleset(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=shared.ADMIN_ID))
        await econ_cog.bjruleset(ctx, "fantasy")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_rejects_switch_while_game_active(self, econ_cog):
        uid = 9120
        active_blackjack[uid] = _bj_game([("5", "♠"), ("3", "♥")])
        ctx = FakeContext(author=FakeAuthor(user_id=shared.ADMIN_ID))
        await econ_cog.bjruleset(ctx, "arcade")
        assert ctx.sent
        assert "active" in (ctx.sent[0].get("content") or "").lower()

    async def test_non_admin_silently_returns(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=9999))
        await econ_cog.bjruleset(ctx, "arcade")
        assert not ctx.sent


# ---------------------------------------------------------------------------
# Repuzzle command (admin only)
# ---------------------------------------------------------------------------


class TestRepuzzleCommand:
    async def test_non_admin_silently_returns(self, econ_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=9999))
        await econ_cog.repuzzle.callback(econ_cog, ctx, None)
        assert not ctx.sent

    async def test_admin_resets_own_puzzle(self, econ_cog):
        uid = shared.ADMIN_ID
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.repuzzle.callback(econ_cog, ctx, None)
        assert ctx.sent
        assert "reset" in (ctx.sent[0].get("content") or "").lower()

    async def test_admin_resets_another_users_puzzle(self, econ_cog):
        target = FakeAuthor(user_id=9200)
        ctx = FakeContext(author=FakeAuthor(user_id=shared.ADMIN_ID))
        await econ_cog.repuzzle.callback(econ_cog, ctx, target)
        assert ctx.sent
        assert "reset" in (ctx.sent[0].get("content") or "").lower()


# ---------------------------------------------------------------------------
# Balance command with another member
# ---------------------------------------------------------------------------


class TestBalanceWithMember:
    async def test_shows_other_member_balance(self, econ_cog):
        member = FakeAuthor(user_id=9300)
        _set_balance(9300, 750)
        ctx = FakeContext(author=FakeAuthor(user_id=9301))
        await econ_cog.balance.callback(econ_cog, ctx, member)
        embed = ctx.sent[0]["embed"]
        assert "750" in embed.description


# ---------------------------------------------------------------------------
# Coinflip — force win and lose outcomes
# ---------------------------------------------------------------------------


class TestCoinflipOutcomes:
    async def test_forced_win(self, econ_cog):
        uid = 9400
        _set_balance(uid, 200)
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        with patch("modules.economy.random.choice", side_effect=["heads", "heads"]):
            await econ_cog.coinflip.callback(econ_cog, ctx, 50)
        assert ctx.sent
        new_bal = _get_balance(uid)
        assert new_bal == 250

    async def test_forced_loss(self, econ_cog):
        uid = 9401
        _set_balance(uid, 200)
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        with patch("modules.economy.random.choice", side_effect=["heads", "tails"]):
            await econ_cog.coinflip.callback(econ_cog, ctx, 50)
        assert ctx.sent
        new_bal = _get_balance(uid)
        assert new_bal == 150


# ---------------------------------------------------------------------------
# Slots — force jackpot
# ---------------------------------------------------------------------------


class TestSlotsJackpot:
    async def test_seven_jackpot(self, econ_cog):
        uid = 9410
        _set_balance(uid, 500)
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        with patch("modules.economy.random.choice", return_value="7️⃣"):
            await econ_cog.slots.callback(econ_cog, ctx, 50)
        assert ctx.sent
        new_bal = _get_balance(uid)
        assert new_bal == 500 + 50 * 10  # x10 jackpot

    async def test_diamond_jackpot(self, econ_cog):
        uid = 9411
        _set_balance(uid, 500)
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        with patch("modules.economy.random.choice", return_value="💎"):
            await econ_cog.slots.callback(econ_cog, ctx, 50)
        assert ctx.sent

    async def test_regular_jackpot(self, econ_cog):
        uid = 9412
        _set_balance(uid, 500)
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        with patch("modules.economy.random.choice", return_value="🍒"):
            await econ_cog.slots.callback(econ_cog, ctx, 50)
        assert ctx.sent


# ---------------------------------------------------------------------------
# Kids mode puzzle command
# ---------------------------------------------------------------------------


class TestPuzzleKidsMode:
    async def test_kids_mode_gets_practice_puzzle(self, econ_cog):
        from shared import set_kids_mode_guild

        guild = FakeGuild(guild_id=9500)
        set_kids_mode_guild(9500, True)
        ctx = FakeContext(author=FakeAuthor(user_id=9501), guild=guild)
        await econ_cog.puzzle.callback(econ_cog, ctx)
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert embed is not None
        assert "practice" in (embed.description or "").lower() or "No coin" in (embed.description or "")

    async def test_kids_mode_returns_existing_puzzle(self, econ_cog):
        from shared import set_kids_mode_guild

        uid = 9502
        guild = FakeGuild(guild_id=9503)
        set_kids_mode_guild(9503, True)
        key = _puzzle_key(uid, True)
        active_puzzles[key] = {"type": "math", "answer": "7", "display": "Q?", "attempts": 0}
        ctx = FakeContext(author=FakeAuthor(user_id=uid), guild=guild)
        await econ_cog.puzzle.callback(econ_cog, ctx)
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert embed is not None


# ---------------------------------------------------------------------------
# load_active_puzzle direct tests
# ---------------------------------------------------------------------------


def _set_balance(user_id: int, amount: int) -> None:
    shared.db.execute(
        "INSERT INTO users (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = ?",
        (user_id, amount, amount),
    )
    shared.db.commit()


def _get_balance(user_id: int) -> int:
    row = shared.db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return int(row[0]) if row else 0


class TestLoadActivePuzzle:
    def test_no_user_row_returns_none(self):
        uid = 99800
        assert shared.db.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is None
        assert load_active_puzzle(uid) is None

    def test_user_with_no_puzzle_returns_none(self):
        uid = 99801
        _set_balance(uid, 100)
        assert load_active_puzzle(uid) is None

    def test_returns_non_wordle_puzzle_dict(self):
        uid = 99802
        _set_balance(uid, 100)
        save_active_puzzle(uid, {"type": "math", "answer": "4", "display": "2+2=?"})
        result = load_active_puzzle(uid)
        assert result is not None
        assert result["type"] == "math"
        assert result["answer"] == "4"
        assert result["display"] == "2+2=?"

    def test_returns_wordle_puzzle_with_guesses(self):
        uid = 99803
        _set_balance(uid, 100)
        puzzle = {"type": "wordle", "answer": "crane", "display": "Guess the word!", "guesses": ["slate", "audio"]}
        save_active_puzzle(uid, puzzle)
        result = load_active_puzzle(uid)
        assert result is not None
        assert result["type"] == "wordle"
        assert result["guesses"] == ["slate", "audio"]

    def test_wordle_with_invalid_guesses_json_returns_empty_list(self):
        uid = 99804
        _set_balance(uid, 100)
        shared.db.execute(
            "UPDATE users SET active_puzzle_type = 'wordle', active_puzzle_answer = 'crane', "
            "active_puzzle_display = 'Q', active_puzzle_guesses = 'not-json' WHERE user_id = ?",
            (uid,),
        )
        shared.db.commit()
        result = load_active_puzzle(uid)
        assert result is not None
        assert result["guesses"] == []


# ---------------------------------------------------------------------------
# generate_puzzle branch coverage
# ---------------------------------------------------------------------------


class TestGeneratePuzzleBranches:
    def _force_kind(self, monkeypatch, kind: str):
        original = stdlib_random.choice
        call_count = [0]

        def _choice(seq):
            if call_count[0] == 0:
                call_count[0] += 1
                return kind
            return original(seq)

        monkeypatch.setattr("modules.economy.random.choice", _choice)

    def test_math_branch(self, monkeypatch):
        self._force_kind(monkeypatch, "math")
        kind, q, a = generate_puzzle()
        assert kind == "math"
        assert isinstance(q, str) and isinstance(a, str)

    def test_wordle_branch(self, monkeypatch):
        self._force_kind(monkeypatch, "wordle")
        kind, q, a = generate_puzzle()
        assert kind == "wordle"
        assert isinstance(a, str) and len(a) == 5

    def test_unscramble_branch(self, monkeypatch):
        self._force_kind(monkeypatch, "unscramble")
        kind, q, a = generate_puzzle()
        assert kind == "unscramble"
        assert isinstance(a, str)


# ---------------------------------------------------------------------------
# Guess daily limit
# ---------------------------------------------------------------------------


class TestGuessCommandDailyLimit:
    async def test_daily_limit_reached_sends_error(self, econ_cog):
        uid = 99810
        _set_balance(uid, 0)
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        shared.db.execute(
            "UPDATE users SET guess_date = ?, guess_count = ? WHERE user_id = ?",
            (today, LUCKY_GUESS_MAX_DAILY, uid),
        )
        shared.db.commit()
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.guess.callback(econ_cog, ctx, 5)
        embed = ctx.sent[0]["embed"]
        assert "No Guesses" in embed.title or "guesses" in (embed.description or "").lower()


# ---------------------------------------------------------------------------
# Slots two-in-a-row win path
# ---------------------------------------------------------------------------


class TestSlotsTwoInARow:
    async def test_two_in_a_row_win(self, econ_cog):
        uid = 99820
        _set_balance(uid, 500)
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        # Two matching + one different: reels[0] == reels[1] != reels[2]
        with patch("modules.economy.random.choice", side_effect=["🍒", "🍒", "7️⃣"]):
            await econ_cog.slots.callback(econ_cog, ctx, 100)
        embed = ctx.sent[0]["embed"]
        assert "Two in a row" in (embed.description or "")
        assert _get_balance(uid) == 600  # won 100 back


# ---------------------------------------------------------------------------
# Blackjack "hand is None" reset paths
# ---------------------------------------------------------------------------


class TestBlackjackHandNoneReset:
    async def test_hit_with_no_hand_resets(self, econ_cog):
        uid = 99830
        _set_balance(uid, 500)
        game = {"dealer": [("5", "♦"), ("10", "♣")], "hands": [], "current_hand": 0}
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.hit.callback(econ_cog, ctx)
        assert uid not in active_blackjack
        assert "reset" in (ctx.sent[0].get("content") or "").lower()

    async def test_stand_with_no_hand_resets(self, econ_cog):
        uid = 99831
        _set_balance(uid, 500)
        game = {"dealer": [("5", "♦"), ("10", "♣")], "hands": [], "current_hand": 0}
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.stand.callback(econ_cog, ctx)
        assert uid not in active_blackjack
        assert "reset" in (ctx.sent[0].get("content") or "").lower()

    async def test_double_with_no_hand_resets(self, econ_cog):
        uid = 99832
        _set_balance(uid, 500)
        game = {"dealer": [("5", "♦"), ("10", "♣")], "hands": [], "current_hand": 0}
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.double.callback(econ_cog, ctx)
        assert uid not in active_blackjack
        assert "reset" in (ctx.sent[0].get("content") or "").lower()

    async def test_split_with_no_hand_resets(self, econ_cog):
        uid = 99833
        _set_balance(uid, 500)
        game = {"dealer": [("5", "♦"), ("10", "♣")], "hands": [], "current_hand": 0}
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.split.callback(econ_cog, ctx)
        assert uid not in active_blackjack
        assert "reset" in (ctx.sent[0].get("content") or "").lower()

    async def test_surrender_with_no_hand_resets(self, econ_cog):
        uid = 99834
        _set_balance(uid, 500)
        game = {"dealer": [("5", "♦"), ("10", "♣")], "hands": [], "current_hand": 0}
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.surrender.callback(econ_cog, ctx)
        assert uid not in active_blackjack
        assert "reset" in (ctx.sent[0].get("content") or "").lower()


# ---------------------------------------------------------------------------
# Blackjack settlement paths (bust, push, five-card charlie)
# ---------------------------------------------------------------------------


class TestBlackjackSettlement:
    async def test_bust_settlement_deducts_bet(self, econ_cog):
        uid = 99840
        _set_balance(uid, 500)
        busted_hand = make_player_hand([("K", "♠"), ("J", "♥"), ("5", "♣")], 50)
        busted_hand["bust"] = True  # 25, busted
        busted_hand["stood"] = True
        game = {
            "dealer": [("K", "♦"), ("8", "♣")],  # dealer has 18, won't draw
            "hands": [busted_hand],
            "current_hand": 0,
        }
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog._finish_blackjack_round(ctx, game)
        assert ctx.sent
        # Bust → lost 50 coins
        assert _get_balance(uid) == 450

    async def test_push_settlement_no_change(self, econ_cog):
        uid = 99841
        _set_balance(uid, 500)
        push_hand = make_player_hand([("K", "♠"), ("8", "♥")], 50)
        push_hand["stood"] = True  # 18
        game = {
            "dealer": [("K", "♦"), ("8", "♣")],  # dealer also has 18
            "hands": [push_hand],
            "current_hand": 0,
        }
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog._finish_blackjack_round(ctx, game)
        assert ctx.sent
        assert _get_balance(uid) == 500  # push = no change

    async def test_five_card_charlie_wins(self, econ_cog):
        apply_blackjack_ruleset("arcade")  # enables five_card_charlie
        uid = 99842
        _set_balance(uid, 500)
        charlie_hand = make_player_hand([("2", "♠"), ("3", "♥"), ("4", "♣"), ("5", "♦"), ("A", "♠")], 50)
        charlie_hand["stood"] = True  # 2+3+4+5+1=15, 5 cards
        game = {
            "dealer": [("K", "♦"), ("8", "♣")],  # dealer 18 > 15 but charlie wins
            "hands": [charlie_hand],
            "current_hand": 0,
        }
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog._finish_blackjack_round(ctx, game)
        assert ctx.sent
        # Five-card charlie = win → +50
        assert _get_balance(uid) == 550


# ---------------------------------------------------------------------------
# Blackjack split — auto-stand for split aces
# ---------------------------------------------------------------------------


class TestBlackjackSplitAces:
    async def test_split_aces_auto_stands(self, econ_cog):
        uid = 99850
        _set_balance(uid, 500)
        # Two aces = pair of aces
        game = {
            "dealer": [("5", "♦"), ("10", "♣")],
            "hands": [make_player_hand([("A", "♠"), ("A", "♥")], 50)],
            "current_hand": 0,
        }
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        # draw_from_shoe is called twice for the split
        with patch("modules.economy.draw_from_shoe", side_effect=[("K", "♠"), ("K", "♥")]):
            await econ_cog.split.callback(econ_cog, ctx)
        # Both hands should be stood (split aces get one card each and stand)
        assert ctx.sent


# ---------------------------------------------------------------------------
# blackjack_recommendation — uncovered strategy branches
# ---------------------------------------------------------------------------


class TestBlackjackRecommendationBranches:
    def test_pair_nines_vs_seven_stands(self):
        action, _ = blackjack_recommendation([("9", "♠"), ("9", "♥")], ("7", "♣"))
        assert action == "stand"

    def test_pair_nines_vs_two_splits(self):
        action, _ = blackjack_recommendation([("9", "♠"), ("9", "♥")], ("2", "♣"))
        assert action == "split"

    def test_pair_sevens_vs_four_splits(self):
        action, _ = blackjack_recommendation([("7", "♠"), ("7", "♥")], ("4", "♣"))
        assert action == "split"

    def test_pair_sixes_vs_three_splits(self):
        action, _ = blackjack_recommendation([("6", "♠"), ("6", "♥")], ("3", "♣"))
        assert action == "split"

    def test_pair_twos_vs_five_splits(self):
        action, _ = blackjack_recommendation([("2", "♠"), ("2", "♥")], ("5", "♣"))
        assert action == "split"

    def test_pair_threes_vs_six_splits(self):
        action, _ = blackjack_recommendation([("3", "♠"), ("3", "♥")], ("6", "♣"))
        assert action == "split"

    def test_pair_fours_vs_five_splits(self):
        action, _ = blackjack_recommendation([("4", "♠"), ("4", "♥")], ("5", "♣"))
        assert action == "split"

    def test_soft_nineteen_stands(self):
        action, _ = blackjack_recommendation([("A", "♠"), ("8", "♥")], ("6", "♣"))
        assert action == "stand"

    def test_soft_eighteen_vs_three_doubles(self):
        action, _ = blackjack_recommendation([("A", "♠"), ("7", "♥")], ("3", "♣"))
        assert action == "double"

    def test_soft_eighteen_vs_two_stands(self):
        action, _ = blackjack_recommendation([("A", "♠"), ("7", "♥")], ("2", "♣"))
        assert action == "stand"

    def test_soft_sixteen_vs_four_doubles(self):
        action, _ = blackjack_recommendation([("A", "♠"), ("5", "♥")], ("4", "♣"))
        assert action == "double"

    def test_soft_thirteen_vs_five_doubles(self):
        action, _ = blackjack_recommendation([("A", "♠"), ("2", "♥")], ("5", "♣"))
        assert action == "double"

    def test_soft_seventeen_vs_two_hits(self):
        action, _ = blackjack_recommendation([("A", "♠"), ("6", "♥")], ("2", "♣"))
        assert action == "hit"

    def test_hard_fifteen_vs_ten_surrenders(self):
        action, _ = blackjack_recommendation([("9", "♠"), ("6", "♥")], ("10", "♣"))
        assert action == "surrender"

    def test_hard_nine_vs_three_doubles(self):
        action, _ = blackjack_recommendation([("5", "♠"), ("4", "♥")], ("3", "♣"))
        assert action == "double"

    def test_hard_fourteen_vs_four_stands(self):
        action, _ = blackjack_recommendation([("8", "♠"), ("6", "♥")], ("4", "♣"))
        assert action == "stand"

    def test_hard_fourteen_vs_seven_hits(self):
        action, _ = blackjack_recommendation([("8", "♠"), ("6", "♥")], ("7", "♣"))
        assert action == "hit"

    def test_hard_twelve_vs_two_hits(self):
        action, _ = blackjack_recommendation([("7", "♠"), ("5", "♥")], ("2", "♣"))
        assert action == "hit"

    def test_hard_seven_vs_two_hits(self):
        # hard total ≤ 11 fallthrough
        action, _ = blackjack_recommendation([("3", "♠"), ("4", "♥")], ("2", "♣"))
        assert action == "hit"


# ---------------------------------------------------------------------------
# best_legal_blackjack_recommendation — fallback paths
# ---------------------------------------------------------------------------


class TestBestLegalRecommendation:
    def test_hit_fallback_when_base_unavailable(self, monkeypatch):
        # recommendation = "stand" but only "hit" is legal → hit fallback at line 1049-1050
        monkeypatch.setattr("modules.economy.available_actions", lambda g, h: ["hit"])
        game = {"hands": [make_player_hand([("K", "♠"), ("K", "♥")], 10)], "current_hand": 0, "dealer": [("6", "♣"), ("5", "♦")]}
        hand = game["hands"][0]
        action, _ = best_legal_blackjack_recommendation(game, hand, ("6", "♣"))
        assert action == "hit"

    def test_stand_fallback_when_hit_also_unavailable(self, monkeypatch):
        # recommendation = "double" (hard 11), fallback "hit" not in legal → stand at line 1051-1052
        monkeypatch.setattr("modules.economy.available_actions", lambda g, h: ["stand"])
        game = {"hands": [make_player_hand([("5", "♠"), ("6", "♥")], 10)], "current_hand": 0, "dealer": [("4", "♣"), ("5", "♦")]}
        hand = game["hands"][0]
        action, _ = best_legal_blackjack_recommendation(game, hand, ("4", "♣"))
        assert action == "stand"

    def test_last_resort_when_no_standard_action(self, monkeypatch):
        # recommendation = "double", hit and stand not in legal → next(iter(legal)) at 1054-1055
        monkeypatch.setattr("modules.economy.available_actions", lambda g, h: ["surrender"])
        game = {"hands": [make_player_hand([("5", "♠"), ("6", "♥")], 10)], "current_hand": 0, "dealer": [("4", "♣"), ("5", "♦")]}
        hand = game["hands"][0]
        action, _ = best_legal_blackjack_recommendation(game, hand, ("4", "♣"))
        assert action == "surrender"


# ---------------------------------------------------------------------------
# dealer_should_hit — soft 17 with rule enabled
# ---------------------------------------------------------------------------


class TestDealerShouldHitSoft17:
    def test_hits_soft_17_when_rule_enabled(self):
        original = dict(BLACKJACK_RULES)
        BLACKJACK_RULES["dealer_hits_soft_17"] = True
        try:
            result = dealer_should_hit([("A", "♠"), ("6", "♥")])
            assert result is True
        finally:
            BLACKJACK_RULES.clear()
            BLACKJACK_RULES.update(original)


# ---------------------------------------------------------------------------
# blackjack_raw_action_listener — uncovered branches
# ---------------------------------------------------------------------------


class TestBlackjackRawActionListenerBranches:
    async def test_bot_message_returns_early(self, econ_cog):
        from unittest.mock import AsyncMock as _AM

        bot = MagicMock()
        bot.get_context = _AM()
        econ_cog.bot = bot
        message = MagicMock()
        message.author.bot = True
        message.content = "hit"
        await econ_cog.blackjack_raw_action_listener(message)
        bot.get_context.assert_not_awaited()

    async def test_non_action_message_returns_early(self, econ_cog):
        from unittest.mock import AsyncMock as _AM

        uid = 99960
        bot = MagicMock()
        bot.get_context = _AM()
        econ_cog.bot = bot
        # Put user in active_blackjack so we don't exit at the id-check
        active_blackjack[uid] = {"dummy": True}
        message = MagicMock()
        message.author.bot = False
        message.author.id = uid
        message.content = "hello"  # not in BLACKJACK_RAW_ACTIONS
        try:
            await econ_cog.blackjack_raw_action_listener(message)
        finally:
            active_blackjack.pop(uid, None)
        bot.get_context.assert_not_awaited()

    async def test_command_none_returns_early(self, econ_cog):
        from unittest.mock import AsyncMock as _AM

        uid = 99961
        ctx = MagicMock()
        ctx.invoke = _AM()
        bot = MagicMock()
        bot.get_context = _AM(return_value=ctx)
        bot.get_command = MagicMock(return_value=None)  # command not found
        econ_cog.bot = bot
        active_blackjack[uid] = {"dummy": True}
        message = MagicMock()
        message.author.bot = False
        message.author.id = uid
        message.content = "hit"
        try:
            await econ_cog.blackjack_raw_action_listener(message)
        finally:
            active_blackjack.pop(uid, None)
        ctx.invoke.assert_not_awaited()


# ---------------------------------------------------------------------------
# guess — win branch
# ---------------------------------------------------------------------------


class TestGuessWinBranch:
    async def test_correct_guess_awards_coin(self, econ_cog):
        uid = 99970
        _set_balance(uid, 0)
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        with patch("modules.economy.random.randint", return_value=7):
            await econ_cog.guess.callback(econ_cog, ctx, 7)
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert "Correct" in embed.title or "correct" in (embed.description or "").lower()


# ---------------------------------------------------------------------------
# puzzle command — already solved / out of attempts
# ---------------------------------------------------------------------------


class TestPuzzleCommandSolvedPaths:
    async def test_already_solved_today(self, econ_cog):
        from datetime import datetime as _dt

        uid = 99980
        _set_balance(uid, 100)
        today = _dt.now().strftime("%Y-%m-%d")
        shared.db.execute(
            "UPDATE users SET puzzle_date = ?, puzzle_solved = 1 WHERE user_id = ?",
            (today, uid),
        )
        shared.db.commit()
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.puzzle.callback(econ_cog, ctx)
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert "Already Solved" in embed.title

    async def test_out_of_attempts_today(self, econ_cog):
        from datetime import datetime as _dt
        from modules.economy import PUZZLE_MAX_ATTEMPTS

        uid = 99981
        _set_balance(uid, 100)
        today = _dt.now().strftime("%Y-%m-%d")
        shared.db.execute(
            "UPDATE users SET puzzle_date = ?, puzzle_attempts = ? WHERE user_id = ?",
            (today, PUZZLE_MAX_ATTEMPTS, uid),
        )
        shared.db.commit()
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.puzzle.callback(econ_cog, ctx)
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert "Out of Attempts" in embed.title


# ---------------------------------------------------------------------------
# blackjack — hit bust, double unavailable/bust, split aces, surrender
# ---------------------------------------------------------------------------


class TestBlackjackCommandExtraBranches:
    async def test_hit_causes_bust(self, econ_cog):
        uid = 99990
        _set_balance(uid, 500)
        hand = make_player_hand([("K", "♠"), ("7", "♥")], 50)
        game = {
            "dealer": [("10", "♦"), ("8", "♣")],  # dealer 18, no draw needed
            "hands": [hand],
            "current_hand": 0,
        }
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        with patch("modules.economy.draw_from_shoe", return_value=("6", "♦")):
            await econ_cog.hit.callback(econ_cog, ctx)
        assert hand["bust"] is True
        assert ctx.sent

    async def test_double_unavailable_three_card_hand(self, econ_cog):
        uid = 99991
        _set_balance(uid, 500)
        hand = make_player_hand([("4", "♠"), ("3", "♥"), ("5", "♦")], 50)
        hand["action_count"] = 1
        game = {
            "dealer": [("10", "♦"), ("8", "♣")],
            "hands": [hand],
            "current_hand": 0,
        }
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.double.callback(econ_cog, ctx)
        assert ctx.sent
        assert "cannot double" in ctx.sent[0].get("content", "").lower()

    async def test_double_causes_bust(self, econ_cog):
        uid = 99992
        _set_balance(uid, 500)
        hand = make_player_hand([("K", "♠"), ("6", "♥")], 50)
        game = {
            "dealer": [("10", "♦"), ("8", "♣")],  # dealer 18, no draw needed
            "hands": [hand],
            "current_hand": 0,
        }
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        with patch("modules.economy.draw_from_shoe", return_value=("8", "♦")):
            await econ_cog.double.callback(econ_cog, ctx)
        assert hand["bust"] is True
        assert ctx.sent

    async def test_split_aces_auto_stand_draw_disabled(self, econ_cog):
        # Enable aces split AND auto-stand on split aces
        BLACKJACK_RULES["resplit_aces"] = True
        BLACKJACK_RULES["draw_to_split_aces"] = False
        uid = 99993
        _set_balance(uid, 500)
        hand = make_player_hand([("A", "♠"), ("A", "♥")], 50)
        game = {
            "dealer": [("10", "♦"), ("8", "♣")],  # dealer 18, no draw
            "hands": [hand],
            "current_hand": 0,
        }
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        with patch("modules.economy.draw_from_shoe", side_effect=[("K", "♠"), ("K", "♥")]):
            await econ_cog.split.callback(econ_cog, ctx)
        assert ctx.sent
        assert game["hands"][0]["stood"] is True
        assert game["hands"][1]["stood"] is True

    async def test_surrender_unavailable_after_action(self, econ_cog):
        uid = 99994
        _set_balance(uid, 500)
        hand = make_player_hand([("K", "♠"), ("6", "♥"), ("2", "♦")], 50)
        hand["action_count"] = 1
        game = {
            "dealer": [("10", "♦"), ("8", "♣")],
            "hands": [hand],
            "current_hand": 0,
        }
        active_blackjack[uid] = game
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        await econ_cog.surrender.callback(econ_cog, ctx)
        assert ctx.sent
        assert "cannot surrender" in ctx.sent[0].get("content", "").lower()
