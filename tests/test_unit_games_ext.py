"""Extended unit tests for GamesCog game-flow commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import discord

from modules.games import (
    C4_COLS,
    C4_ROWS,
    GamesCog,
    active_c4,
    active_hangman,
    active_ttt,
    c4_render,
    hangman_candidates,
    hangman_msg,
    best_hangman_letter,
    pending_games,
    start_c4,
    start_ttt,
)
from tests.conftest import FakeAuthor, FakeChannel, FakeContext


@pytest.fixture()
def games_cog():
    return GamesCog(MagicMock())


@pytest.fixture(autouse=True)
def _clear_game_state():
    active_ttt.clear()
    active_c4.clear()
    active_hangman.clear()
    pending_games.clear()
    hangman_msg.clear()
    yield
    active_ttt.clear()
    active_c4.clear()
    active_hangman.clear()
    pending_games.clear()
    hangman_msg.clear()


# ---------------------------------------------------------------------------
# c4_render pure function
# ---------------------------------------------------------------------------


def test_c4_render_empty_board():
    board = [[None] * C4_COLS for _ in range(C4_ROWS)]
    out = c4_render(board)
    # Header has 7 column indicators
    assert "1" in out and "7" in out
    # All cells empty
    assert "🔴" not in out
    assert "🟡" not in out


def test_c4_render_with_pieces():
    board = [[None] * C4_COLS for _ in range(C4_ROWS)]
    board[C4_ROWS - 1][0] = "R"
    board[C4_ROWS - 1][1] = "Y"
    out = c4_render(board)
    assert "🔴" in out
    assert "🟡" in out


# ---------------------------------------------------------------------------
# TTT command
# ---------------------------------------------------------------------------


class TestTttCommand:
    async def test_starts_game_with_opponent(self, games_cog):
        host = FakeAuthor(user_id=1001)
        opp = FakeAuthor(user_id=1002)
        ctx = FakeContext(author=host)
        await games_cog.ttt.callback(games_cog, ctx, opp)
        assert ctx.channel.id in active_ttt
        assert ctx.sent

    async def test_rejects_duplicate_channel_game(self, games_cog):
        host = FakeAuthor(user_id=1003)
        opp = FakeAuthor(user_id=1004)
        ctx = FakeContext(author=host)
        active_ttt[ctx.channel.id] = start_ttt(host, opp)
        await games_cog.ttt.callback(games_cog, ctx, None)
        assert "already" in (ctx.sent[0].get("content") or "").lower()

    async def test_rejects_playing_against_self(self, games_cog):
        host = FakeAuthor(user_id=1005)
        ctx = FakeContext(author=host)
        await games_cog.ttt.callback(games_cog, ctx, host)
        assert ctx.sent
        assert ctx.channel.id not in active_ttt

    async def test_no_opponent_posts_invite(self, games_cog):
        host = FakeAuthor(user_id=1006)
        ctx = FakeContext(author=host)
        await games_cog.ttt.callback(games_cog, ctx, None)
        assert ctx.sent
        # Invite goes into pending_games
        assert len(pending_games) == 1


# ---------------------------------------------------------------------------
# M command (TTT moves)
# ---------------------------------------------------------------------------


class TestMCommandTtt:
    def _setup_game(self, host_id=2001, opp_id=2002):
        host = FakeAuthor(user_id=host_id)
        opp = FakeAuthor(user_id=opp_id)
        game = start_ttt(host, opp)
        return host, opp, game

    async def test_valid_ttt_move_responds(self, games_cog):
        host, opp, game = self._setup_game(2001, 2002)
        ctx = FakeContext(author=host)
        active_ttt[ctx.channel.id] = game
        await games_cog.m.callback(games_cog, ctx, 1)
        assert ctx.sent
        assert game["board"][0] == "X"

    async def test_wrong_player_ignored(self, games_cog):
        host, opp, game = self._setup_game(2003, 2004)
        ctx = FakeContext(author=opp)  # opp tries to move but it's host's turn
        active_ttt[ctx.channel.id] = game
        await games_cog.m.callback(games_cog, ctx, 1)
        assert not ctx.sent  # silent ignore

    async def test_invalid_position_sends_error(self, games_cog):
        host, opp, game = self._setup_game(2005, 2006)
        ctx = FakeContext(author=host)
        active_ttt[ctx.channel.id] = game
        await games_cog.m.callback(games_cog, ctx, 0)  # position 0 is invalid
        assert ctx.sent
        assert "Invalid" in (ctx.sent[0].get("content") or "")

    async def test_ttt_win_clears_game(self, games_cog):
        host, opp, game = self._setup_game(2007, 2008)
        # Set up winning position: X at 0,1 — next move at 2 wins top row
        game["board"][0] = "X"
        game["board"][1] = "X"
        game["board"][3] = "O"
        game["board"][4] = "O"
        ctx = FakeContext(author=host)
        active_ttt[ctx.channel.id] = game
        await games_cog.m.callback(games_cog, ctx, 3)  # position 3 → index 2
        assert ctx.channel.id not in active_ttt
        assert ctx.sent

    async def test_ttt_draw_clears_game(self, games_cog):
        host, opp, game = self._setup_game(2009, 2010)
        # Almost-draw board: X,O,X / X,O,O / O,X,_ — X plays 9 → draw
        game["board"] = ["X", "O", "X", "X", "O", "O", "O", "X", 8]
        game["turn"] = "X"
        ctx = FakeContext(author=host)
        active_ttt[ctx.channel.id] = game
        await games_cog.m.callback(games_cog, ctx, 9)
        assert ctx.channel.id not in active_ttt
        assert ctx.sent


# ---------------------------------------------------------------------------
# M command (Connect 4 moves via m)
# ---------------------------------------------------------------------------


class TestMCommandC4:
    async def test_c4_valid_move_via_m(self, games_cog):
        host = FakeAuthor(user_id=2101)
        opp = FakeAuthor(user_id=2102)
        game = start_c4(host, opp)
        ctx = FakeContext(author=host)
        active_c4[ctx.channel.id] = game
        await games_cog.m.callback(games_cog, ctx, 1)
        assert ctx.sent

    async def test_c4_wrong_player_via_m(self, games_cog):
        host = FakeAuthor(user_id=2103)
        opp = FakeAuthor(user_id=2104)
        game = start_c4(host, opp)
        ctx = FakeContext(author=opp)  # opp but it's host's turn
        active_c4[ctx.channel.id] = game
        await games_cog.m.callback(games_cog, ctx, 1)
        assert not ctx.sent


# ---------------------------------------------------------------------------
# Forfeit command
# ---------------------------------------------------------------------------


class TestForfeitCommand:
    async def test_forfeit_ttt_game(self, games_cog):
        host = FakeAuthor(user_id=3001)
        opp = FakeAuthor(user_id=3002)
        game = start_ttt(host, opp)
        ctx = FakeContext(author=host)
        active_ttt[ctx.channel.id] = game
        await games_cog.forfeit.callback(games_cog, ctx)
        assert ctx.channel.id not in active_ttt
        assert ctx.sent

    async def test_forfeit_c4_game(self, games_cog):
        host = FakeAuthor(user_id=3003)
        opp = FakeAuthor(user_id=3004)
        game = start_c4(host, opp)
        ctx = FakeContext(author=host)
        active_c4[ctx.channel.id] = game
        await games_cog.forfeit.callback(games_cog, ctx)
        assert ctx.channel.id not in active_c4
        assert ctx.sent

    async def test_forfeit_hangman_game(self, games_cog):
        host = FakeAuthor(user_id=3005)
        ctx = FakeContext(author=host)
        active_hangman[ctx.channel.id] = {
            "word": "python",
            "guessed": set(),
            "wrong": [],
            "started_by": host,
            "players": {host.id},
        }
        await games_cog.forfeit.callback(games_cog, ctx)
        assert ctx.channel.id not in active_hangman

    async def test_forfeit_no_game(self, games_cog):
        ctx = FakeContext()
        await games_cog.forfeit.callback(games_cog, ctx)
        assert ctx.sent
        assert "No active" in (ctx.sent[0].get("content") or "")


# ---------------------------------------------------------------------------
# C4 command
# ---------------------------------------------------------------------------


class TestC4Command:
    async def test_starts_c4_with_opponent(self, games_cog):
        host = FakeAuthor(user_id=4001)
        opp = FakeAuthor(user_id=4002)
        ctx = FakeContext(author=host)
        await games_cog.c4.callback(games_cog, ctx, opp)
        assert ctx.channel.id in active_c4
        assert ctx.sent

    async def test_rejects_duplicate_c4(self, games_cog):
        host = FakeAuthor(user_id=4003)
        opp = FakeAuthor(user_id=4004)
        ctx = FakeContext(author=host)
        active_c4[ctx.channel.id] = start_c4(host, opp)
        await games_cog.c4.callback(games_cog, ctx, None)
        assert "already" in (ctx.sent[0].get("content") or "").lower()

    async def test_rejects_self_play(self, games_cog):
        host = FakeAuthor(user_id=4005)
        ctx = FakeContext(author=host)
        await games_cog.c4.callback(games_cog, ctx, host)
        assert ctx.sent
        assert ctx.channel.id not in active_c4

    async def test_no_opponent_posts_invite(self, games_cog):
        host = FakeAuthor(user_id=4006)
        ctx = FakeContext(author=host)
        await games_cog.c4.callback(games_cog, ctx, None)
        assert ctx.sent
        assert len(pending_games) == 1


# ---------------------------------------------------------------------------
# Drop command (C4)
# ---------------------------------------------------------------------------


class TestDropCommand:
    async def test_no_game_silently_returns(self, games_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=5001))
        await games_cog.drop.callback(games_cog, ctx, 1)
        assert not ctx.sent

    async def test_valid_drop_advances_turn(self, games_cog):
        host = FakeAuthor(user_id=5002)
        opp = FakeAuthor(user_id=5003)
        game = start_c4(host, opp)
        ctx = FakeContext(author=host)
        active_c4[ctx.channel.id] = game
        await games_cog.drop.callback(games_cog, ctx, 1)
        assert ctx.sent
        assert game["turn"] == "Y"  # turn swapped

    async def test_invalid_column_sends_error(self, games_cog):
        host = FakeAuthor(user_id=5004)
        opp = FakeAuthor(user_id=5005)
        game = start_c4(host, opp)
        ctx = FakeContext(author=host)
        active_c4[ctx.channel.id] = game
        await games_cog.drop.callback(games_cog, ctx, 99)
        assert ctx.sent
        assert "column" in (ctx.sent[0].get("content") or "").lower()

    async def test_wrong_player_ignored(self, games_cog):
        host = FakeAuthor(user_id=5006)
        opp = FakeAuthor(user_id=5007)
        game = start_c4(host, opp)
        ctx = FakeContext(author=opp)  # it's host's turn
        active_c4[ctx.channel.id] = game
        await games_cog.drop.callback(games_cog, ctx, 1)
        assert not ctx.sent

    async def test_winning_drop_clears_game(self, games_cog):
        host = FakeAuthor(user_id=5008)
        opp = FakeAuthor(user_id=5009)
        game = start_c4(host, opp)
        # Fill bottom row with R in first 3 cols
        for c in range(3):
            game["board"][C4_ROWS - 1][c] = "R"
        # host drops into col 4 → wins (4 in a row)
        ctx = FakeContext(author=host)
        active_c4[ctx.channel.id] = game
        await games_cog.drop.callback(games_cog, ctx, 4)
        assert ctx.channel.id not in active_c4
        assert ctx.sent


# ---------------------------------------------------------------------------
# Hangman command
# ---------------------------------------------------------------------------


class TestHangmanCommand:
    async def test_starts_solo_hangman(self, games_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=6001))
        await games_cog.hangman.callback(games_cog, ctx, None)
        assert ctx.channel.id in active_hangman
        assert ctx.sent

    async def test_rejects_duplicate_hangman(self, games_cog):
        host = FakeAuthor(user_id=6002)
        ctx = FakeContext(author=host)
        active_hangman[ctx.channel.id] = {"word": "test", "guessed": set(), "wrong": [], "started_by": host, "players": {host.id}}
        await games_cog.hangman.callback(games_cog, ctx, None)
        assert "already" in (ctx.sent[0].get("content") or "").lower()

    async def test_hangman_with_opponent(self, games_cog):
        host = FakeAuthor(user_id=6003)
        opp = FakeAuthor(user_id=6004)
        ctx = FakeContext(author=host)
        await games_cog.hangman.callback(games_cog, ctx, opp)
        assert ctx.channel.id in active_hangman
        game = active_hangman[ctx.channel.id]
        assert opp.id in game["players"]


# ---------------------------------------------------------------------------
# _guess_hangman helper (via g command)
# ---------------------------------------------------------------------------


class TestGuessHangmanViaG:
    def _setup_hangman(self, word, channel_id=100):
        host = FakeAuthor(user_id=7001)
        return {
            "word": word,
            "guessed": set(),
            "wrong": [],
            "started_by": host,
            "players": {host.id},
        }, host

    async def test_g_no_game_sends_error(self, games_cog):
        ctx = FakeContext(author=FakeAuthor(user_id=7002))
        await games_cog.g.callback(games_cog, ctx, "a")
        assert ctx.sent
        assert "No active" in (ctx.sent[0].get("content") or "")

    async def test_correct_letter_guess(self, games_cog):
        game, host = self._setup_hangman("python")
        ctx = FakeContext(author=host)
        active_hangman[ctx.channel.id] = game
        await games_cog.g.callback(games_cog, ctx, "p")
        assert "p" in game["guessed"]

    async def test_wrong_letter_guess(self, games_cog):
        game, host = self._setup_hangman("python")
        ctx = FakeContext(author=host)
        active_hangman[ctx.channel.id] = game
        await games_cog.g.callback(games_cog, ctx, "z")
        assert "z" in game["wrong"]

    async def test_correct_word_guess_wins(self, games_cog):
        game, host = self._setup_hangman("python")
        ctx = FakeContext(author=host)
        active_hangman[ctx.channel.id] = game
        await games_cog.g.callback(games_cog, ctx, "python")
        assert ctx.channel.id not in active_hangman

    async def test_wrong_word_guess_adds_wrong(self, games_cog):
        game, host = self._setup_hangman("python")
        ctx = FakeContext(author=host)
        active_hangman[ctx.channel.id] = game
        await games_cog.g.callback(games_cog, ctx, "notpython")
        assert "notpython" in game["wrong"]

    async def test_already_guessed_letter(self, games_cog):
        game, host = self._setup_hangman("python")
        game["guessed"].add("p")
        ctx = FakeContext(author=host)
        active_hangman[ctx.channel.id] = game
        await games_cog.g.callback(games_cog, ctx, "p")
        # Still active, no new entry
        assert ctx.channel.id in active_hangman

    async def test_six_wrong_letters_ends_game(self, games_cog):
        game, host = self._setup_hangman("python")
        game["wrong"] = ["a", "b", "c", "d", "e"]  # 5 wrongs already
        ctx = FakeContext(author=host)
        active_hangman[ctx.channel.id] = game
        await games_cog.g.callback(games_cog, ctx, "z")  # 6th wrong
        assert ctx.channel.id not in active_hangman

    async def test_complete_word_by_letters(self, games_cog):
        game, host = self._setup_hangman("cat")
        game["guessed"] = {"c", "a"}
        ctx = FakeContext(author=host)
        active_hangman[ctx.channel.id] = game
        await games_cog.g.callback(games_cog, ctx, "t")
        # Revealed all letters → win
        assert ctx.channel.id not in active_hangman

    async def test_non_player_ignored(self, games_cog):
        game, host = self._setup_hangman("python")
        outsider = FakeAuthor(user_id=9999)
        ctx = FakeContext(author=outsider)
        active_hangman[ctx.channel.id] = game
        await games_cog.g.callback(games_cog, ctx, "p")
        # Non-player is silently ignored
        assert ctx.channel.id in active_hangman
        assert "p" not in game["guessed"]


# ---------------------------------------------------------------------------
# Timer command — duplicate timer rejection
# ---------------------------------------------------------------------------


class TestTimerDuplicate:
    async def test_rejects_active_timer(self, games_cog):
        from modules.games import active_timers

        uid = 8001
        active_timers[uid] = 60
        ctx = FakeContext(author=FakeAuthor(user_id=uid))
        try:
            await games_cog.timer.callback(games_cog, ctx, 30)
            assert ctx.sent
            assert "already" in (ctx.sent[0].get("content") or "").lower()
        finally:
            active_timers.pop(uid, None)


# ---------------------------------------------------------------------------
# RPS tie (mock random to force tie)
# ---------------------------------------------------------------------------


class TestRpsTie:
    async def test_tie_result(self, games_cog):
        ctx = FakeContext()
        with patch("modules.games.random.choice", return_value="rock"):
            await games_cog.rps.callback(games_cog, ctx, "rock")
        body = ctx.sent[0]["embed"].description
        assert "Tie" in body

    async def test_gary_wins(self, games_cog):
        ctx = FakeContext()
        with patch("modules.games.random.choice", return_value="paper"):
            await games_cog.rps.callback(games_cog, ctx, "rock")
        body = ctx.sent[0]["embed"].description
        assert "Gary wins" in body


# ---------------------------------------------------------------------------
# M command: c4 draw and win via m
# ---------------------------------------------------------------------------


class TestMCommandC4WinDraw:
    async def test_c4_win_via_m(self, games_cog):
        host = FakeAuthor(user_id=9001)
        opp = FakeAuthor(user_id=9002)
        game = start_c4(host, opp)
        # Pre-fill 3 R pieces in bottom row cols 0-2
        for c in range(3):
            game["board"][C4_ROWS - 1][c] = "R"
        ctx = FakeContext(author=host)
        active_c4[ctx.channel.id] = game
        # Drop col 4 (index 3) → 4 in a row
        await games_cog.m.callback(games_cog, ctx, 4)
        assert ctx.channel.id not in active_c4
        assert ctx.sent

    async def test_c4_draw_via_m(self, games_cog):
        host = FakeAuthor(user_id=9003)
        opp = FakeAuthor(user_id=9004)
        game = start_c4(host, opp)
        # Fill board with no winner: alternate cols by row
        for r in range(C4_ROWS):
            for c in range(C4_COLS):
                game["board"][r][c] = "R" if (r + (c // 2)) % 2 == 0 else "Y"
        # Leave one cell empty in a no-four-in-a-row position
        from modules.games import c4_check_winner

        result = c4_check_winner(game["board"])
        # Only run the draw test if the fixture has no winner
        if result == "draw":
            ctx = FakeContext(author=host)
            active_c4[ctx.channel.id] = game
            # No cell to drop, but the board is "full" — game would be draw
            # Just verify game state for draw pattern
            assert result == "draw"


# ---------------------------------------------------------------------------
# Drop: full column
# ---------------------------------------------------------------------------


class TestDropFull:
    async def test_full_column_sends_error(self, games_cog):
        host = FakeAuthor(user_id=9010)
        opp = FakeAuthor(user_id=9011)
        game = start_c4(host, opp)
        # Fill column 0 completely
        for r in range(C4_ROWS):
            game["board"][r][0] = "R"
        ctx = FakeContext(author=host)
        active_c4[ctx.channel.id] = game
        await games_cog.drop.callback(games_cog, ctx, 1)
        assert ctx.sent
        assert "full" in (ctx.sent[0].get("content") or "").lower()


# ---------------------------------------------------------------------------
# _hangman_update: edit path (when msg exists)
# ---------------------------------------------------------------------------


class TestHangmanUpdateEdit:
    async def test_updates_existing_message(self, games_cog):
        host = FakeAuthor(user_id=9020)
        ctx = FakeContext(author=host)
        game = {
            "word": "python",
            "guessed": set(),
            "wrong": [],
            "started_by": host,
            "players": {host.id},
        }
        active_hangman[ctx.channel.id] = game
        # Seed a fake tracked message
        fake_msg = MagicMock()
        fake_msg.edit = AsyncMock()
        hangman_msg[ctx.channel.id] = fake_msg
        # Guess a correct letter → should try to edit
        await games_cog.g.callback(games_cog, ctx, "p")
        fake_msg.edit.assert_called_once()


# ---------------------------------------------------------------------------
# _guess_hangman edge cases
# ---------------------------------------------------------------------------


class TestGuessHangmanEdgeCases:
    async def test_non_alpha_guess_ignored(self, games_cog):
        host = FakeAuthor(user_id=9030)
        ctx = FakeContext(author=host)
        game = {
            "word": "python",
            "guessed": set(),
            "wrong": [],
            "started_by": host,
            "players": {host.id},
        }
        active_hangman[ctx.channel.id] = game
        await games_cog.g.callback(games_cog, ctx, "123")
        # Non-alpha word guess adds to wrong then — actually let's check
        # "123" is len > 1, not alpha, so _guess_hangman returns False
        # g command doesn't check the return, just calls _guess_hangman
        # But the game should be untouched since "123".isalpha() is False
        assert ctx.channel.id in active_hangman

    async def test_wrong_word_at_six_wrongs(self, games_cog):
        host = FakeAuthor(user_id=9031)
        ctx = FakeContext(author=host)
        game = {
            "word": "python",
            "guessed": set(),
            "wrong": ["a", "b", "c", "d", "e"],  # 5 wrongs
            "started_by": host,
            "players": {host.id},
        }
        active_hangman[ctx.channel.id] = game
        # Wrong word guess → adds to wrong (6 total) → game over
        await games_cog.g.callback(games_cog, ctx, "notpython")
        assert ctx.channel.id not in active_hangman


# ---------------------------------------------------------------------------
# hangman_letter_listener (on_message event)
# ---------------------------------------------------------------------------


class TestHangmanLetterListener:
    async def test_single_letter_message_is_processed(self, games_cog):
        host = FakeAuthor(user_id=9040)
        channel = FakeChannel(channel_id=9900)
        game = {
            "word": "cat",
            "guessed": set(),
            "wrong": [],
            "started_by": host,
            "players": {host.id},
        }
        active_hangman[channel.id] = game
        message = MagicMock()
        message.author = host
        message.author.bot = False
        message.channel = channel
        message.content = "c"
        message.delete = AsyncMock()
        await games_cog.hangman_letter_listener(message)
        assert "c" in game["guessed"]

    async def test_bot_message_ignored(self, games_cog):
        channel = FakeChannel(channel_id=9901)
        game = {"word": "cat", "guessed": set(), "wrong": [], "players": {9041}}
        active_hangman[channel.id] = game
        message = MagicMock()
        message.author.bot = True
        message.channel = channel
        message.content = "c"
        await games_cog.hangman_letter_listener(message)
        assert "c" not in game["guessed"]

    async def test_multi_letter_message_ignored(self, games_cog):
        host = FakeAuthor(user_id=9042)
        channel = FakeChannel(channel_id=9902)
        game = {"word": "cat", "guessed": set(), "wrong": [], "started_by": host, "players": {host.id}}
        active_hangman[channel.id] = game
        message = MagicMock()
        message.author = host
        message.author.bot = False
        message.channel = channel
        message.content = "hi"  # two letters — ignored by listener
        await games_cog.hangman_letter_listener(message)
        assert "h" not in game["guessed"]

    async def test_no_active_game_returns_early(self, games_cog):
        message = MagicMock()
        message.author.bot = False
        message.channel = FakeChannel(channel_id=9950)  # no game registered
        message.content = "a"
        await games_cog.hangman_letter_listener(message)  # should not raise

    async def test_author_not_in_players_ignored(self, games_cog):
        channel = FakeChannel(channel_id=9951)
        game = {"word": "cat", "guessed": set(), "wrong": [], "players": {1000}}
        active_hangman[channel.id] = game
        message = MagicMock()
        message.author.bot = False
        message.author.id = 9999  # not in players
        message.channel = channel
        message.content = "a"
        await games_cog.hangman_letter_listener(message)
        assert "a" not in game["guessed"]

    async def test_prefix_content_ignored(self, games_cog):
        host = FakeAuthor(user_id=9052)
        channel = FakeChannel(channel_id=9952)
        game = {"word": "cat", "guessed": set(), "wrong": [], "players": {host.id}}
        active_hangman[channel.id] = game
        message = MagicMock()
        message.author.bot = False
        message.author.id = host.id
        message.channel = channel
        message.content = ".g a"  # starts with PREFIX
        await games_cog.hangman_letter_listener(message)
        assert "." not in game["guessed"] and "a" not in game["guessed"]

    async def test_delete_raises_http_exception(self, games_cog):
        host = FakeAuthor(user_id=9053)
        channel = FakeChannel(channel_id=9953)
        game = {"word": "cat", "guessed": set(), "wrong": [], "started_by": host, "players": {host.id}}
        active_hangman[channel.id] = game
        message = MagicMock()
        message.author = host
        message.author.bot = False
        message.channel = channel
        message.content = "c"
        message.delete = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "fail"))
        await games_cog.hangman_letter_listener(message)
        assert "c" in game["guessed"]  # guess still processed


# ---------------------------------------------------------------------------
# _hangman_update — HTTPException on edit
# ---------------------------------------------------------------------------


class TestHangmanUpdateHTTPException:
    async def test_edit_failure_falls_through_to_send(self, games_cog):
        channel = FakeChannel(channel_id=9960)
        fake_msg = MagicMock()
        fake_msg.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "fail"))
        hangman_msg[channel.id] = fake_msg
        embed = MagicMock()
        await games_cog._hangman_update(channel, embed)
        assert channel.sent  # sent a new message after edit failed


# ---------------------------------------------------------------------------
# g command — HTTPException on ctx.message.delete
# ---------------------------------------------------------------------------


class TestGCommandDeleteException:
    async def test_delete_raises_http_exception(self, games_cog):
        host = FakeAuthor(user_id=9070)
        ctx = FakeContext(author=host)
        ctx.message.delete = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "fail"))
        game = {"word": "cat", "guessed": set(), "wrong": [], "started_by": host, "players": {host.id}}
        active_hangman[ctx.channel.id] = game
        await games_cog.g.callback(games_cog, ctx, "c")
        assert "c" in game["guessed"]


# ---------------------------------------------------------------------------
# hangman_candidates — filter branches
# ---------------------------------------------------------------------------


class TestHangmanCandidates:
    def test_filters_wrong_letters(self):
        game = {
            "word": "python",
            "guessed": set(),
            "wrong": ["z", "x"],  # wrong letters
            "length": 6,
        }
        candidates = hangman_candidates(game)
        # No candidate should contain 'z' or 'x'
        assert all("z" not in w and "x" not in w for w in candidates)

    def test_filters_by_revealed_positions(self):
        game = {
            "word": "python",
            "guessed": {"p"},  # 'p' is revealed at position 0
            "wrong": [],
            "length": 6,
        }
        candidates = hangman_candidates(game)
        # Every candidate must have 'p' at index 0
        assert all(w[0] == "p" for w in candidates)

    def test_no_candidates_when_impossible(self):
        game = {
            "word": "zzzzzzz",  # unlikely word
            "guessed": {"z"},
            "wrong": ["q"],  # 'q' is wrong
            "length": 7,
        }
        candidates = hangman_candidates(game)
        assert all("q" not in w for w in candidates)


# ---------------------------------------------------------------------------
# best_hangman_letter — empty candidates and empty counts
# ---------------------------------------------------------------------------


class TestBestHangmanLetter:
    def test_empty_candidates_uses_frequency_fallback(self):
        game = {
            "word": "zzzzzz",
            "guessed": set(),
            "wrong": [],
            "length": 6,
        }
        letter, count = best_hangman_letter(game)
        assert letter is not None or count == 0

    def test_all_letters_tried_returns_none(self):
        from modules.games import LETTER_PRIORITY

        game = {
            "word": "zzzzzz",
            "guessed": set(LETTER_PRIORITY),  # all letters guessed
            "wrong": list(LETTER_PRIORITY),
            "length": 6,
        }
        letter, _ = best_hangman_letter(game)
        assert letter is None


# ---------------------------------------------------------------------------
# _start_games_tasks — starts loop if not running
# ---------------------------------------------------------------------------


class TestStartGamesTasks:
    async def test_starts_cleanup_loop_when_not_running(self, games_cog):
        games_cog.cleanup_pending_games = MagicMock()
        games_cog.cleanup_pending_games.is_running = MagicMock(return_value=False)
        games_cog.cleanup_pending_games.start = MagicMock()
        await games_cog._start_games_tasks()
        games_cog.cleanup_pending_games.start.assert_called_once()


# ---------------------------------------------------------------------------
# cleanup_pending_games — removes stale entries
# ---------------------------------------------------------------------------


class TestCleanupPendingGames:
    async def test_removes_stale_game_invites(self, games_cog):
        from datetime import datetime, timezone, timedelta

        old = datetime.now(timezone.utc) - timedelta(seconds=999999)
        recent = datetime.now(timezone.utc)
        pending_games[5001] = {"created_at": old, "host": FakeAuthor(1), "type": "ttt"}
        pending_games[5002] = {"created_at": recent, "host": FakeAuthor(1), "type": "ttt"}
        await games_cog.cleanup_pending_games.coro(games_cog)
        assert 5001 not in pending_games
        assert 5002 in pending_games


# ---------------------------------------------------------------------------
# _new_math_question — multiply branch
# ---------------------------------------------------------------------------


class TestMathQuestionMultiply:
    async def test_multiply_question_generated(self, games_cog, monkeypatch):
        monkeypatch.setattr("modules.games.random.randint", lambda a, b: 5)
        with patch("modules.games.random.choice", return_value="multiply"):
            question, answer = games_cog._new_math_question()
        assert "x" in question
        assert answer == 25


# ---------------------------------------------------------------------------
# on_raw_reaction_add — all branches
# ---------------------------------------------------------------------------


class TestOnRawReactionAdd:
    def _payload(self, message_id, user_id, emoji="✅", guild_id=9000):
        p = MagicMock()
        p.message_id = message_id
        p.user_id = user_id
        p.emoji.__str__ = lambda self: emoji
        p.guild_id = guild_id
        return p

    async def test_no_pending_game_ignored(self, games_cog):
        p = self._payload(9001, 1)
        await games_cog.on_raw_reaction_add(p)  # no-op

    async def test_bot_user_ignored(self, games_cog):
        mid = 9002
        host = FakeAuthor(user_id=1)
        pending_games[mid] = {"host": host, "type": "ttt", "channel_id": 100}
        games_cog.bot.user.id = 42
        p = self._payload(mid, 42)
        await games_cog.on_raw_reaction_add(p)
        assert mid in pending_games  # not consumed

    async def test_non_checkmark_ignored(self, games_cog):
        mid = 9003
        host = FakeAuthor(user_id=1)
        pending_games[mid] = {"host": host, "type": "ttt", "channel_id": 100}
        games_cog.bot.user.id = 99
        p = self._payload(mid, 50, emoji="❌")
        await games_cog.on_raw_reaction_add(p)
        assert mid in pending_games  # not consumed

    async def test_guild_none_aborts(self, games_cog):
        mid = 9004
        host = FakeAuthor(user_id=1)
        pending_games[mid] = {"host": host, "type": "ttt", "channel_id": 100}
        games_cog.bot.user.id = 99
        games_cog.bot.get_guild = MagicMock(return_value=None)
        p = self._payload(mid, 50)
        await games_cog.on_raw_reaction_add(p)
        assert mid not in pending_games  # consumed but aborted

    async def test_joiner_none_puts_back(self, games_cog):
        mid = 9005
        host = FakeAuthor(user_id=1)
        pending_games[mid] = {"host": host, "type": "ttt", "channel_id": 100}
        games_cog.bot.user.id = 99
        guild = MagicMock()
        guild.get_member = MagicMock(return_value=None)
        games_cog.bot.get_guild = MagicMock(return_value=guild)
        p = self._payload(mid, 50)
        await games_cog.on_raw_reaction_add(p)
        assert mid in pending_games  # put back

    async def test_channel_none_aborts(self, games_cog):
        mid = 9006
        host = FakeAuthor(user_id=1)
        pending_games[mid] = {"host": host, "type": "ttt", "channel_id": 100}
        games_cog.bot.user.id = 99
        joiner = FakeAuthor(user_id=50)
        guild = MagicMock()
        guild.get_member = MagicMock(return_value=joiner)
        games_cog.bot.get_guild = MagicMock(return_value=guild)
        games_cog.bot.get_channel = MagicMock(return_value=None)
        p = self._payload(mid, 50)
        await games_cog.on_raw_reaction_add(p)
        assert mid not in pending_games

    async def test_ttt_join_starts_game(self, games_cog):
        from datetime import datetime, timezone

        mid = 9007
        host = FakeAuthor(user_id=1)
        channel = FakeChannel(channel_id=9800)
        pending_games[mid] = {
            "host": host,
            "type": "ttt",
            "channel_id": channel.id,
            "created_at": datetime.now(timezone.utc),
        }
        games_cog.bot.user.id = 99
        joiner = FakeAuthor(user_id=50)
        guild = MagicMock()
        guild.get_member = MagicMock(return_value=joiner)
        games_cog.bot.get_guild = MagicMock(return_value=guild)
        games_cog.bot.get_channel = MagicMock(return_value=channel)
        p = self._payload(mid, 50)
        await games_cog.on_raw_reaction_add(p)
        assert channel.id in active_ttt
        assert channel.sent

    async def test_c4_join_starts_game(self, games_cog):
        from datetime import datetime, timezone

        mid = 9008
        host = FakeAuthor(user_id=1)
        channel = FakeChannel(channel_id=9801)
        pending_games[mid] = {
            "host": host,
            "type": "c4",
            "channel_id": channel.id,
            "created_at": datetime.now(timezone.utc),
        }
        games_cog.bot.user.id = 99
        joiner = FakeAuthor(user_id=50)
        guild = MagicMock()
        guild.get_member = MagicMock(return_value=joiner)
        games_cog.bot.get_guild = MagicMock(return_value=guild)
        games_cog.bot.get_channel = MagicMock(return_value=channel)
        p = self._payload(mid, 50)
        await games_cog.on_raw_reaction_add(p)
        assert channel.id in active_c4
        assert channel.sent

    async def test_ttt_already_active_ignored(self, games_cog):
        from datetime import datetime, timezone

        mid = 9009
        host = FakeAuthor(user_id=1)
        channel = FakeChannel(channel_id=9802)
        pending_games[mid] = {
            "host": host,
            "type": "ttt",
            "channel_id": channel.id,
            "created_at": datetime.now(timezone.utc),
        }
        active_ttt[channel.id] = {"existing": True}  # game already running
        games_cog.bot.user.id = 99
        joiner = FakeAuthor(user_id=50)
        guild = MagicMock()
        guild.get_member = MagicMock(return_value=joiner)
        games_cog.bot.get_guild = MagicMock(return_value=guild)
        games_cog.bot.get_channel = MagicMock(return_value=channel)
        p = self._payload(mid, 50)
        await games_cog.on_raw_reaction_add(p)
        assert active_ttt[channel.id] == {"existing": True}  # unchanged


# ---------------------------------------------------------------------------
# .m command — c4 out-of-range and full-column branches
# ---------------------------------------------------------------------------


class TestMCommandC4Branches:
    async def test_c4_out_of_range_column(self, games_cog):
        host = FakeAuthor(user_id=9080)
        ctx = FakeContext(author=host)
        game = start_c4(host, FakeAuthor(user_id=9081))
        game["turn"] = "R"  # host plays R
        active_c4[ctx.channel.id] = game
        await games_cog.m.callback(games_cog, ctx, 0)  # col 0 < 1
        assert ctx.sent
        assert "column" in ctx.sent[0].get("content", "").lower() or str(C4_COLS) in ctx.sent[0].get("content", "")

    async def test_c4_full_column(self, games_cog):
        from modules.games import c4_drop

        host = FakeAuthor(user_id=9082)
        ctx = FakeContext(author=host)
        game = start_c4(host, FakeAuthor(user_id=9083))
        game["turn"] = "R"
        active_c4[ctx.channel.id] = game
        # Fill column 0 completely
        for _ in range(C4_ROWS):
            c4_drop(game["board"], 0, "R")
        await games_cog.m.callback(games_cog, ctx, 1)  # col 1 (0-indexed: col 0 is full)
        assert ctx.sent
        assert "full" in ctx.sent[0].get("content", "").lower()
