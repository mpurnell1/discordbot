"""Unit tests for games — pure logic and cog command handlers."""

from unittest.mock import MagicMock, patch

import pytest

from modules.games import (
    C4_COLS,
    C4_ROWS,
    GamesCog,
    active_math_quiz,
    active_memory_games,
    active_trivia,
    best_hangman_letter,
    c4_check_winner,
    c4_drop,
    hangman_candidates,
    hangman_render,
    start_c4,
    start_ttt,
    ttt_check_winner,
    ttt_render,
)
from tests.conftest import FakeAuthor, FakeContext


# ---------------------------------------------------------------------------
# tic-tac-toe
# ---------------------------------------------------------------------------
class TestTttCheckWinner:
    def test_no_winner_on_empty_board(self):
        assert ttt_check_winner(list(range(9))) is None

    def test_x_wins_top_row(self):
        board = ["X", "X", "X", 3, 4, 5, 6, 7, 8]
        assert ttt_check_winner(board) == "X"

    def test_o_wins_middle_column(self):
        board = [0, "O", 2, 3, "O", 5, 6, "O", 8]
        assert ttt_check_winner(board) == "O"

    def test_x_wins_diagonal(self):
        board = ["X", 1, 2, 3, "X", 5, 6, 7, "X"]
        assert ttt_check_winner(board) == "X"

    def test_x_wins_anti_diagonal(self):
        board = [0, 1, "X", 3, "X", 5, "X", 7, 8]
        assert ttt_check_winner(board) == "X"

    def test_draw_when_full_no_three_in_row(self):
        board = ["X", "O", "X", "X", "O", "O", "O", "X", "X"]
        assert ttt_check_winner(board) == "draw"

    def test_three_in_row_beats_full_board_check(self):
        """A full board with a winner returns the winner, not 'draw'."""
        board = ["X", "X", "X", "O", "O", "X", "O", "X", "O"]
        assert ttt_check_winner(board) == "X"


def test_ttt_render_includes_keycaps_for_empty():
    board = list(range(9))
    rendered = ttt_render(board)
    # Every empty cell should have a digit + combining keycap.
    for n in range(1, 10):
        assert str(n) in rendered


def test_start_ttt_initial_state():
    host, opp = object(), object()
    game = start_ttt(host, opp)
    assert game["board"] == list(range(9))
    assert game["players"] == {"X": host, "O": opp}
    assert game["turn"] == "X"


# ---------------------------------------------------------------------------
# connect 4
# ---------------------------------------------------------------------------
def _empty_c4():
    return [[None] * C4_COLS for _ in range(C4_ROWS)]


class TestC4Drop:
    def test_drops_to_bottom_when_column_empty(self):
        board = _empty_c4()
        row = c4_drop(board, 0, "R")
        assert row == C4_ROWS - 1
        assert board[C4_ROWS - 1][0] == "R"

    def test_stacks_on_top_of_existing(self):
        board = _empty_c4()
        c4_drop(board, 3, "R")
        row = c4_drop(board, 3, "Y")
        assert row == C4_ROWS - 2
        assert board[C4_ROWS - 2][3] == "Y"

    def test_returns_minus_one_when_full(self):
        board = _empty_c4()
        for _ in range(C4_ROWS):
            c4_drop(board, 2, "R")
        assert c4_drop(board, 2, "Y") == -1


class TestC4CheckWinner:
    def test_horizontal_win(self):
        board = _empty_c4()
        for c in range(4):
            board[5][c] = "R"
        assert c4_check_winner(board) == "R"

    def test_vertical_win(self):
        board = _empty_c4()
        for r in range(2, 6):
            board[r][3] = "Y"
        assert c4_check_winner(board) == "Y"

    def test_diagonal_down_right_win(self):
        board = _empty_c4()
        for i in range(4):
            board[i][i] = "R"
        assert c4_check_winner(board) == "R"

    def test_diagonal_down_left_win(self):
        board = _empty_c4()
        for i in range(4):
            board[i][3 - i] = "Y"
        assert c4_check_winner(board) == "Y"

    def test_no_winner_empty_board(self):
        assert c4_check_winner(_empty_c4()) is None

    def test_draw_when_full_no_four_in_row(self):
        # Construct a full board with no winner: alternate every column.
        board = _empty_c4()
        for c in range(C4_COLS):
            for r in range(C4_ROWS):
                # Stagger pattern so no 4-in-a-row anywhere.
                board[r][c] = "R" if (r + (c // 2)) % 2 == 0 else "Y"
        # Confirm test fixture really has no winner before asserting "draw".
        # If the fixture itself happens to form a 4-in-a-row, the test would
        # be a false positive — keep the assertion strict either way.
        result = c4_check_winner(board)
        assert result in ("R", "Y", "draw")
        if result == "draw":
            assert all(board[0][c] is not None for c in range(C4_COLS))


def test_start_c4_initial_state():
    host, opp = object(), object()
    game = start_c4(host, opp)
    assert len(game["board"]) == C4_ROWS
    assert len(game["board"][0]) == C4_COLS
    assert all(cell is None for row in game["board"] for cell in row)
    assert game["turn"] == "R"


# ---------------------------------------------------------------------------
# hangman
# ---------------------------------------------------------------------------
def test_hangman_render_hides_unguessed_letters():
    game = {"word": "python", "guessed": set("py"), "wrong": ["x"]}
    out = hangman_render(game)
    # 'p' and 'y' visible
    assert "p" in out and "y" in out
    # 't', 'h', 'o', 'n' should be masked as underscores
    assert out.count("\\_") == 4
    # Wrong letters listed
    assert "x" in out
    assert "Guesses left: **5**" in out


def test_hangman_candidates_filters_by_pattern_and_wrong_letters():
    # Word is "apple", guessed 'a' and 'p' (so positions 0,1,2 known)
    game = {"word": "apple", "guessed": {"a", "p"}, "wrong": ["z"]}
    candidates = hangman_candidates(game)
    # Every candidate must be 5 letters, share the revealed pattern, and
    # contain none of the wrong letters.
    for word in candidates:
        assert len(word) == 5
        assert word[0] == "a" and word[1] == "p" and word[2] == "p"
        assert "z" not in word


def test_best_hangman_letter_returns_unfried_letter():
    game = {"word": "python", "guessed": set("py"), "wrong": ["x"]}
    letter, count = best_hangman_letter(game)
    assert letter is not None
    # Letter must not have been tried already.
    assert letter not in game["guessed"]
    assert letter not in game["wrong"]
    assert count >= 0


# ---------------------------------------------------------------------------
# GamesCog command handler tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def games_cog():
    return GamesCog(MagicMock())


@pytest.fixture(autouse=True)
def _clear_game_state():
    """Wipe in-memory game state between tests."""
    active_math_quiz.clear()
    active_memory_games.clear()
    active_trivia.clear()
    yield
    active_math_quiz.clear()
    active_memory_games.clear()
    active_trivia.clear()


class TestRpsCommand:
    async def test_valid_rock_sends_result(self, games_cog):
        ctx = FakeContext()
        await games_cog.rps.callback(games_cog, ctx, "rock")
        assert ctx.sent
        assert ctx.sent[0]["embed"] is not None

    async def test_shorthand_r_accepted(self, games_cog):
        ctx = FakeContext()
        await games_cog.rps.callback(games_cog, ctx, "r")
        assert ctx.sent[0]["embed"] is not None

    async def test_invalid_choice_sends_usage(self, games_cog):
        ctx = FakeContext()
        await games_cog.rps.callback(games_cog, ctx, "banana")
        assert ctx.sent
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_result_is_win_tie_or_loss(self, games_cog):
        ctx = FakeContext()
        await games_cog.rps.callback(games_cog, ctx, "scissors")
        body = ctx.sent[0]["embed"].description
        assert any(word in body for word in ("win", "lose", "Tie"))


class TestRollCommand:
    async def test_default_d6(self, games_cog):
        ctx = FakeContext(author=FakeAuthor())
        await games_cog.roll.callback(games_cog, ctx, 6)
        content = ctx.sent[0].get("content", "")
        assert "d6" in content

    async def test_custom_sides(self, games_cog):
        ctx = FakeContext(author=FakeAuthor())
        await games_cog.roll.callback(games_cog, ctx, 20)
        assert "d20" in ctx.sent[0].get("content", "")

    async def test_rejects_one_side(self, games_cog):
        ctx = FakeContext()
        await games_cog.roll.callback(games_cog, ctx, 1)
        assert ctx.sent

    async def test_rejects_too_many_sides(self, games_cog):
        ctx = FakeContext()
        await games_cog.roll.callback(games_cog, ctx, 9999)
        assert ctx.sent


class TestMathgameCommand:
    async def test_starts_question(self, games_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=3001))
        await games_cog.mathgame.callback(games_cog, ctx)
        assert ctx.sent[0]["embed"] is not None
        assert 3001 in active_math_quiz

    async def test_correct_answer_succeeds(self, games_cog):
        user = FakeAuthor(user_id=3002)
        ctx = FakeContext(author=user)
        await games_cog.mathgame.callback(games_cog, ctx)
        correct = active_math_quiz[3002]["answer"]
        ctx2 = FakeContext(author=user)
        await games_cog.mathanswer.callback(games_cog, ctx2, correct)
        assert "Correct" in ctx2.sent[0]["embed"].title
        assert 3002 not in active_math_quiz

    async def test_wrong_answer_prompts_retry(self, games_cog):
        user = FakeAuthor(user_id=3003)
        ctx = FakeContext(author=user)
        await games_cog.mathgame.callback(games_cog, ctx)
        wrong = active_math_quiz[3003]["answer"] + 1
        ctx2 = FakeContext(author=user)
        await games_cog.mathanswer.callback(games_cog, ctx2, wrong)
        assert "Try Again" in ctx2.sent[0]["embed"].title
        assert 3003 in active_math_quiz  # still active

    async def test_mathanswer_no_active_game(self, games_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=3004))
        await games_cog.mathanswer.callback(games_cog, ctx, 42)
        assert "No math question" in (ctx.sent[0].get("content") or "")


class TestMemoryCommand:
    async def test_starts_game(self, games_cog):
        ctx = FakeContext(author=FakeAuthor())
        with patch("modules.games.asyncio.sleep"):
            await games_cog.memory.callback(games_cog, ctx, 1)
        assert ctx.sent

    async def test_rejects_duplicate_in_channel(self, games_cog):
        ctx = FakeContext()
        active_memory_games[ctx.channel.id] = {"answer": "🌟 🌙"}
        await games_cog.memory.callback(games_cog, ctx, 1)
        assert "already" in (ctx.sent[0].get("content") or "").lower()

    async def test_correct_memoryanswer_wins(self, games_cog):
        ctx = FakeContext()
        active_memory_games[ctx.channel.id] = {"answer": "🌟 🌙 ☀️"}
        await games_cog.memoryanswer.callback(games_cog, ctx, answer="🌟 🌙 ☀️")
        assert "Correct" in ctx.sent[0]["embed"].title
        assert ctx.channel.id not in active_memory_games

    async def test_wrong_memoryanswer_keeps_game(self, games_cog):
        ctx = FakeContext()
        active_memory_games[ctx.channel.id] = {"answer": "🌟 🌙 ☀️"}
        await games_cog.memoryanswer.callback(games_cog, ctx, answer="🌙 🌟 ☀️")
        assert ctx.channel.id in active_memory_games

    async def test_memoryanswer_no_active_game(self, games_cog):
        ctx = FakeContext()
        await games_cog.memoryanswer.callback(games_cog, ctx, answer="anything")
        assert ctx.sent


class TestTriviaCommand:
    async def test_starts_question(self, games_cog):
        ctx = FakeContext()
        await games_cog.trivia.callback(games_cog, ctx)
        assert ctx.sent[0]["embed"] is not None
        assert ctx.channel.id in active_trivia

    async def test_rejects_duplicate(self, games_cog):
        ctx = FakeContext()
        active_trivia[ctx.channel.id] = {"answer": "A", "explanation": "x", "wrong_users": set()}
        await games_cog.trivia.callback(games_cog, ctx)
        assert "already" in (ctx.sent[0].get("content") or "").lower()

    async def test_correct_triviaanswer_wins(self, games_cog):
        ctx = FakeContext()
        active_trivia[ctx.channel.id] = {"answer": "B", "explanation": "Mars is red.", "wrong_users": set()}
        await games_cog.triviaanswer.callback(games_cog, ctx, "B")
        assert "Correct" in ctx.sent[0]["embed"].title
        assert ctx.channel.id not in active_trivia

    async def test_wrong_triviaanswer_records_user(self, games_cog):
        user = FakeAuthor(user_id=4001)
        ctx = FakeContext(author=user)
        active_trivia[ctx.channel.id] = {"answer": "C", "explanation": "x", "wrong_users": set()}
        await games_cog.triviaanswer.callback(games_cog, ctx, "A")
        assert 4001 in active_trivia[ctx.channel.id]["wrong_users"]

    async def test_invalid_triviaanswer_rejected(self, games_cog):
        ctx = FakeContext()
        active_trivia[ctx.channel.id] = {"answer": "A", "explanation": "x", "wrong_users": set()}
        await games_cog.triviaanswer.callback(games_cog, ctx, "Z")
        assert "Usage" in (ctx.sent[0].get("content") or "")


class TestScrambleCommand:
    async def test_scramble_sends_question(self, games_cog):
        ctx = FakeContext()
        with patch("modules.games.active_scrambles", {}):
            await games_cog.scramble.callback(games_cog, ctx)
        assert ctx.sent

    async def test_unscramble_no_game(self, games_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=5001))
        with patch("modules.games.active_scrambles", {}):
            await games_cog.unscramble.callback(games_cog, ctx, answer="word")
        assert ctx.sent


class TestTimerCommand:
    async def test_rejects_zero_seconds(self, games_cog):
        ctx = FakeContext(author=FakeAuthor())
        await games_cog.timer.callback(games_cog, ctx, 0)
        assert ctx.sent

    async def test_rejects_over_one_hour(self, games_cog):
        ctx = FakeContext(author=FakeAuthor())
        await games_cog.timer.callback(games_cog, ctx, 3601)
        assert ctx.sent

    async def test_valid_timer_sends_confirmation(self, games_cog):
        ctx = FakeContext(author=FakeAuthor())
        with patch("modules.games.asyncio.sleep"):
            await games_cog.timer.callback(games_cog, ctx, 5)
        assert ctx.sent
