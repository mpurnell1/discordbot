"""Command-level tests for channel-wide group games: memory, trivia, scramble."""
import pytest
from unittest.mock import patch

from modules.games import active_memory_games, active_trivia, active_scrambles
from tests.conftest import FakeAuthor, FakeContext


@pytest.fixture(autouse=True)
def clear_game_state():
    active_memory_games.clear()
    active_trivia.clear()
    active_scrambles.clear()
    yield
    active_memory_games.clear()
    active_trivia.clear()
    active_scrambles.clear()


@pytest.fixture
def cog():
    from modules.games import GamesCog
    return GamesCog(bot=None)


@pytest.fixture
def other_ctx(fake_ctx):
    """A second user in the same channel as fake_ctx."""
    return FakeContext(author=FakeAuthor(user_id=999, name="OtherUser"), channel=fake_ctx.channel)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
class TestMemoryGroupGame:
    async def test_game_keyed_to_channel(self, cog, fake_ctx):
        with patch("asyncio.sleep"):
            await cog.memory.callback(cog,fake_ctx, level=1)
        assert fake_ctx.channel.id in active_memory_games

    async def test_blocks_second_start_in_same_channel(self, cog, fake_ctx):
        with patch("asyncio.sleep"):
            await cog.memory.callback(cog,fake_ctx, level=1)
            await cog.memory.callback(cog,fake_ctx, level=1)
        content = fake_ctx.sent[-1]["content"]
        assert "already" in content.lower()
        assert fake_ctx.channel.id in active_memory_games

    async def test_no_game_message_when_no_active_game(self, cog, fake_ctx):
        await cog.memoryanswer.callback(cog,fake_ctx, answer="cat dog sun")
        assert "No memory game" in fake_ctx.sent[-1]["content"]

    async def test_wrong_answer_keeps_game_alive(self, cog, fake_ctx):
        with patch("asyncio.sleep"):
            await cog.memory.callback(cog,fake_ctx, level=1)
        await cog.memoryanswer.callback(cog,fake_ctx, answer="wrong wrong wrong")
        assert fake_ctx.channel.id in active_memory_games

    async def test_wrong_answer_mentions_user(self, cog, fake_ctx):
        with patch("asyncio.sleep"):
            await cog.memory.callback(cog,fake_ctx, level=1)
        await cog.memoryanswer.callback(cog,fake_ctx, answer="wrong wrong wrong")
        embed = fake_ctx.sent[-1]["embed"]
        assert fake_ctx.author.mention in embed.description

    async def test_correct_answer_ends_game(self, cog, fake_ctx):
        with patch("asyncio.sleep"):
            await cog.memory.callback(cog,fake_ctx, level=1)
        answer = active_memory_games[fake_ctx.channel.id]["answer"]
        await cog.memoryanswer.callback(cog,fake_ctx, answer=answer)
        assert fake_ctx.channel.id not in active_memory_games

    async def test_correct_answer_announces_winner(self, cog, fake_ctx):
        with patch("asyncio.sleep"):
            await cog.memory.callback(cog,fake_ctx, level=1)
        answer = active_memory_games[fake_ctx.channel.id]["answer"]
        await cog.memoryanswer.callback(cog,fake_ctx, answer=answer)
        embed = fake_ctx.sent[-1]["embed"]
        assert fake_ctx.author.mention in embed.description

    async def test_any_user_can_win(self, cog, fake_ctx, other_ctx):
        with patch("asyncio.sleep"):
            await cog.memory.callback(cog,fake_ctx, level=1)
        answer = active_memory_games[fake_ctx.channel.id]["answer"]
        await cog.memoryanswer.callback(cog,other_ctx, answer=answer)
        assert fake_ctx.channel.id not in active_memory_games
        embed = fake_ctx.sent[-1]["embed"]
        assert other_ctx.author.mention in embed.description

    async def test_game_gone_after_win_prevents_double_answer(self, cog, fake_ctx, other_ctx):
        with patch("asyncio.sleep"):
            await cog.memory.callback(cog,fake_ctx, level=1)
        answer = active_memory_games[fake_ctx.channel.id]["answer"]
        await cog.memoryanswer.callback(cog,fake_ctx, answer=answer)
        await cog.memoryanswer.callback(cog,other_ctx, answer=answer)
        assert "No memory game" in fake_ctx.sent[-1]["content"]

    async def test_forfeit_cancels_game(self, cog, fake_ctx):
        with patch("asyncio.sleep"):
            await cog.memory.callback(cog,fake_ctx, level=1)
        await cog.forfeit.callback(cog,fake_ctx)
        assert fake_ctx.channel.id not in active_memory_games
        assert "cancelled" in fake_ctx.sent[-1]["content"].lower()


# ---------------------------------------------------------------------------
# Trivia
# ---------------------------------------------------------------------------
class TestTriviaGroupGame:
    async def test_game_keyed_to_channel(self, cog, fake_ctx):
        await cog.trivia.callback(cog,fake_ctx)
        assert fake_ctx.channel.id in active_trivia

    async def test_blocks_second_start_in_same_channel(self, cog, fake_ctx):
        await cog.trivia.callback(cog,fake_ctx)
        await cog.trivia.callback(cog,fake_ctx)
        assert "already" in fake_ctx.sent[-1]["content"].lower()

    async def test_no_game_message_when_no_active_game(self, cog, fake_ctx):
        await cog.triviaanswer.callback(cog,fake_ctx, answer="A")
        assert "No trivia" in fake_ctx.sent[-1]["content"]

    async def test_correct_answer_ends_game(self, cog, fake_ctx):
        await cog.trivia.callback(cog,fake_ctx)
        correct = active_trivia[fake_ctx.channel.id]["answer"]
        await cog.triviaanswer.callback(cog,fake_ctx, answer=correct)
        assert fake_ctx.channel.id not in active_trivia

    async def test_correct_answer_announces_winner(self, cog, fake_ctx):
        await cog.trivia.callback(cog,fake_ctx)
        correct = active_trivia[fake_ctx.channel.id]["answer"]
        await cog.triviaanswer.callback(cog,fake_ctx, answer=correct)
        embed = fake_ctx.sent[-1]["embed"]
        assert fake_ctx.author.mention in embed.description

    async def test_any_user_can_win(self, cog, fake_ctx, other_ctx):
        await cog.trivia.callback(cog,fake_ctx)
        correct = active_trivia[fake_ctx.channel.id]["answer"]
        await cog.triviaanswer.callback(cog,other_ctx, answer=correct)
        assert fake_ctx.channel.id not in active_trivia
        embed = fake_ctx.sent[-1]["embed"]
        assert other_ctx.author.mention in embed.description

    async def test_wrong_answer_keeps_game_alive(self, cog, fake_ctx):
        await cog.trivia.callback(cog,fake_ctx)
        correct = active_trivia[fake_ctx.channel.id]["answer"]
        wrong = next(a for a in ("A", "B", "C", "D") if a != correct)
        await cog.triviaanswer.callback(cog,fake_ctx, answer=wrong)
        assert fake_ctx.channel.id in active_trivia

    async def test_wrong_answer_locks_out_user(self, cog, fake_ctx):
        await cog.trivia.callback(cog,fake_ctx)
        correct = active_trivia[fake_ctx.channel.id]["answer"]
        wrong = next(a for a in ("A", "B", "C", "D") if a != correct)
        await cog.triviaanswer.callback(cog,fake_ctx, answer=wrong)
        assert fake_ctx.author.id in active_trivia[fake_ctx.channel.id]["wrong_users"]

    async def test_locked_out_user_cannot_answer_again(self, cog, fake_ctx):
        await cog.trivia.callback(cog,fake_ctx)
        correct = active_trivia[fake_ctx.channel.id]["answer"]
        wrong = next(a for a in ("A", "B", "C", "D") if a != correct)
        await cog.triviaanswer.callback(cog,fake_ctx, answer=wrong)
        await cog.triviaanswer.callback(cog,fake_ctx, answer=correct)
        assert fake_ctx.channel.id in active_trivia

    async def test_other_user_can_answer_after_wrong_guess(self, cog, fake_ctx, other_ctx):
        await cog.trivia.callback(cog,fake_ctx)
        correct = active_trivia[fake_ctx.channel.id]["answer"]
        wrong = next(a for a in ("A", "B", "C", "D") if a != correct)
        await cog.triviaanswer.callback(cog,fake_ctx, answer=wrong)
        await cog.triviaanswer.callback(cog,other_ctx, answer=correct)
        assert fake_ctx.channel.id not in active_trivia

    async def test_forfeit_reveals_answer(self, cog, fake_ctx):
        await cog.trivia.callback(cog,fake_ctx)
        correct = active_trivia[fake_ctx.channel.id]["answer"]
        await cog.forfeit.callback(cog,fake_ctx)
        assert fake_ctx.channel.id not in active_trivia
        assert correct in fake_ctx.sent[-1]["content"]


# ---------------------------------------------------------------------------
# Scramble
# ---------------------------------------------------------------------------
class TestScrambleGroupGame:
    async def test_game_keyed_to_channel(self, cog, fake_ctx):
        await cog.scramble.callback(cog,fake_ctx)
        assert fake_ctx.channel.id in active_scrambles

    async def test_blocks_second_start_in_same_channel(self, cog, fake_ctx):
        await cog.scramble.callback(cog,fake_ctx)
        await cog.scramble.callback(cog,fake_ctx)
        assert "already" in fake_ctx.sent[-1]["content"].lower()

    async def test_no_game_message_when_no_active_game(self, cog, fake_ctx):
        await cog.unscramble.callback(cog,fake_ctx, answer="apple")
        assert "No scramble" in fake_ctx.sent[-1]["content"]

    async def test_wrong_answer_keeps_game_alive(self, cog, fake_ctx):
        await cog.scramble.callback(cog,fake_ctx)
        await cog.unscramble.callback(cog,fake_ctx, answer="zzzzz")
        assert fake_ctx.channel.id in active_scrambles

    async def test_wrong_answer_mentions_user(self, cog, fake_ctx):
        await cog.scramble.callback(cog,fake_ctx)
        await cog.unscramble.callback(cog,fake_ctx, answer="zzzzz")
        embed = fake_ctx.sent[-1]["embed"]
        assert fake_ctx.author.mention in embed.description

    async def test_correct_answer_ends_game(self, cog, fake_ctx):
        await cog.scramble.callback(cog,fake_ctx)
        word = active_scrambles[fake_ctx.channel.id]["answer"]
        await cog.unscramble.callback(cog,fake_ctx, answer=word)
        assert fake_ctx.channel.id not in active_scrambles

    async def test_correct_answer_announces_winner(self, cog, fake_ctx):
        await cog.scramble.callback(cog,fake_ctx)
        word = active_scrambles[fake_ctx.channel.id]["answer"]
        await cog.unscramble.callback(cog,fake_ctx, answer=word)
        embed = fake_ctx.sent[-1]["embed"]
        assert fake_ctx.author.mention in embed.description
        assert word in embed.description

    async def test_any_user_can_win(self, cog, fake_ctx, other_ctx):
        await cog.scramble.callback(cog,fake_ctx)
        word = active_scrambles[fake_ctx.channel.id]["answer"]
        await cog.unscramble.callback(cog,other_ctx, answer=word)
        assert fake_ctx.channel.id not in active_scrambles
        embed = fake_ctx.sent[-1]["embed"]
        assert other_ctx.author.mention in embed.description

    async def test_game_gone_after_win_prevents_double_answer(self, cog, fake_ctx, other_ctx):
        await cog.scramble.callback(cog,fake_ctx)
        word = active_scrambles[fake_ctx.channel.id]["answer"]
        await cog.unscramble.callback(cog,fake_ctx, answer=word)
        await cog.unscramble.callback(cog,other_ctx, answer=word)
        assert "No scramble" in fake_ctx.sent[-1]["content"]

    async def test_forfeit_reveals_word(self, cog, fake_ctx):
        await cog.scramble.callback(cog,fake_ctx)
        word = active_scrambles[fake_ctx.channel.id]["answer"]
        await cog.forfeit.callback(cog,fake_ctx)
        assert fake_ctx.channel.id not in active_scrambles
        assert word in fake_ctx.sent[-1]["content"]
