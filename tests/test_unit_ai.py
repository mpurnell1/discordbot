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

    # Silas wraps the board in inline code so Discord doesn't eat the blanks as
    # italics. Splitting that on whitespace hands back "`n" and "_`" at the
    # ends — tokens that are neither a letter nor a blank.
    @pytest.mark.parametrize(
        "word_line",
        [
            "Word: `n a _ _ h t _`",  # inline code, as Silas sends it
            "Word: n a \\_ \\_ h t \\_",  # backslash-escaped blanks
            "Word: **n** **a** _ _ **h** **t** _",  # bolded reveals
            "Word: N A _ _ H T _",  # upper case
        ],
    )
    def test_markdown_is_stripped_from_the_board(self, cog, word_line):
        result = cog._parse_silas_hangman(f"{word_line}\nGuessed: a, h, n, t\nLives left: 3")
        assert result is not None
        assert result["word_pattern"] == ["n", "a", "_", "_", "h", "t", "_"]

    def test_guessed_letters_survive_their_separators(self, cog):
        result = cog._parse_silas_hangman("Word: _ _ _ _ _\nGuessed: a, d, e, h, i\nLives left: 3")
        assert result["guessed"] == {"a", "d", "e", "h", "i"}

    def test_unreadable_token_rejects_the_whole_board(self, cog):
        # Dropping the bad token instead would shorten the pattern by one and
        # match the solver against words of the wrong length.
        assert cog._parse_silas_hangman("Word: n a ?? h t _\nGuessed: a\nLives left: 3") is None


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

    def test_solves_the_board_it_lost(self, cog):
        # The game Gary actually lost: "naughty", down to `n a _ _ h t _` with
        # e/i/o/r/s spent. Only one 7-letter word in the 10k list fits, so the
        # next guess has to come out of it — Gary guessed "d" and died.
        letter = cog._pick_hangman_letter(
            ["n", "a", "_", "_", "h", "t", "_"],
            {"a", "e", "h", "i", "n", "o", "r", "s", "t"},
        )
        assert letter in {"u", "g", "y"}

    def test_a_wrapped_board_plays_the_same_as_a_bare_one(self, cog):
        """The regression itself: markdown must not change a single guess.

        With the wrappers left on, no candidate could match, the solver fell
        through to fixed letter-frequency order, and Gary lost all but ~6 % of
        his games instead of winning ~95 % of them."""
        bare = cog._parse_silas_hangman("Word: n a _ _ h t _\nGuessed: a, e, h, i, n, o, r, s, t\nLives left: 1")
        wrapped = cog._parse_silas_hangman("Word: `n a _ _ h t _`\nGuessed: a, e, h, i, n, o, r, s, t\nLives left: 1")
        assert bare == wrapped
        pick = cog._pick_hangman_letter
        assert pick(wrapped["word_pattern"], wrapped["guessed"]) == pick(bare["word_pattern"], bare["guessed"])
        assert pick(bare["word_pattern"], bare["guessed"]) in {"u", "g", "y"}


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

    def test_bolded_countdown_as_silas_sends_it(self, cog):
        # The real refusal is "in **5h 54m**"; the bold reaches the parser.
        assert cog._parse_silas_hangman_cooldown("🔤 Cooldown\nYou can start another hangman in **5h 54m**.") == timedelta(hours=5, minutes=54)
        assert cog._parse_silas_hangman_cooldown("in **0h 0m**") == timedelta(0)

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


# ---------------------------------------------------------------------------
# Hangman threads — Silas plays each game in a public thread under the
# channel `!hm` was typed in. The board, its edits and the guesses all live
# there; the gamble channel sees nothing. Before this, Gary waited on the
# channel, guessed into it, and timed the game out every morning.
# ---------------------------------------------------------------------------

_THREAD_ID = 100000000000000011

# The board exactly as Silas opens it: title, art, board, hint, last move.
HANGMAN_BOARD_TEXT = (
    "🔤 Hangman\n```\n  +---+\n  |   |\n      |\n      |\n      |\n      |\n=========```\n"
    "Word: `_ _ _ _ _`\nGuessed: none\nLives left: 6\n\n"
    "Just type a letter or use `!guess`/`!g` to guess the full word!\n\n**Last move:** Game started!"
)
HANGMAN_WON_TEXT = (
    "🎉 You Got It!\n```\n  +---+\n  |   |\n      |\n      |\n      |\n      |\n=========```\n"
    "Word: `a p p l e`\nGuessed: a, e, l, p\nLives left: 6\n\n"
    "The word was `apple`!\n\n**Gary**: +102 🪙 | Balance: 10,131 🪙\n\n"
    "**Last move:** Gary guessed `l` ✅ — word complete! 🎉"
)


class FakeGambleThread(FakeGambleChannel):
    """A public thread under the gamble channel, named the way Silas names
    his hangman threads."""

    def __init__(self, name="🔤 Hangman: Gary", thread_id=_THREAD_ID, parent_id=_GAMBLE_CHANNEL_ID, guild_id=555):
        super().__init__(channel_id=thread_id, guild_id=guild_id)
        self.name = name
        self.parent_id = parent_id
        self.guild = SimpleNamespace(id=guild_id, me=SimpleNamespace(display_name="Gary", name="gary-bot"))


def _hangman_pending(cog):
    """State right after the tick sent `!hm`: a game is expected, none bound."""
    cog.gamble_state["hangman_active"] = True
    cog.gamble_state["hangman_started_at"] = datetime.now(timezone.utc)
    cog.gamble_state["hangman_thread_id"] = None


class TestHangmanThreads:
    def test_thread_scope_follows_the_parent_channel(self):
        cog, channel = _gamble_cog()
        assert cog._gamble_scope(channel) == "channel"
        assert cog._gamble_scope(FakeGambleThread()) == "thread"
        assert cog._gamble_scope(FakeGambleThread(parent_id=_GAMBLE_CHANNEL_ID + 1)) is None
        assert cog._gamble_scope(FakeGambleChannel(channel_id=_GAMBLE_CHANNEL_ID + 1)) is None

    async def test_board_in_garys_thread_is_played_there(self):
        cog, channel = _gamble_cog()
        _hangman_pending(cog)
        thread = FakeGambleThread()

        await cog._handle_silas_gambling_message(_silas_message(thread), HANGMAN_BOARD_TEXT)

        assert channel.sent == []
        assert len(thread.sent) == 1 and thread.sent[0].isalpha() and len(thread.sent[0]) == 1
        assert cog.gamble_state["hangman_thread_id"] == thread.id

    async def test_someone_elses_thread_is_left_alone(self):
        cog, channel = _gamble_cog()
        _hangman_pending(cog)
        thread = FakeGambleThread(name="🔤 Hangman: Bob")

        await cog._handle_silas_gambling_message(_silas_message(thread), HANGMAN_BOARD_TEXT)

        assert thread.sent == [] and channel.sent == []
        assert cog.gamble_state["hangman_thread_id"] is None

    async def test_no_hangman_outstanding_means_no_thread_is_adopted(self):
        cog, channel = _gamble_cog()
        thread = FakeGambleThread()

        await cog._handle_silas_gambling_message(_silas_message(thread), HANGMAN_BOARD_TEXT)

        assert thread.sent == []
        assert cog.gamble_state["hangman_thread_id"] is None

    async def test_only_the_bound_thread_is_played(self):
        cog, channel = _gamble_cog()
        _hangman_pending(cog)
        mine = FakeGambleThread()
        other = FakeGambleThread(thread_id=_THREAD_ID + 1)  # also named for Gary

        await cog._handle_silas_gambling_message(_silas_message(mine), HANGMAN_BOARD_TEXT)
        await cog._handle_silas_gambling_message(_silas_message(other), HANGMAN_BOARD_TEXT)

        assert len(mine.sent) == 1
        assert other.sent == []
        assert cog.gamble_state["hangman_thread_id"] == mine.id

    async def test_a_thread_off_another_channel_is_ignored(self):
        cog, channel = _gamble_cog()
        _hangman_pending(cog)
        thread = FakeGambleThread(parent_id=_GAMBLE_CHANNEL_ID + 1)

        await cog._handle_silas_gambling_message(_silas_message(thread), HANGMAN_BOARD_TEXT)

        assert thread.sent == []
        assert cog.gamble_state["hangman_thread_id"] is None

    async def test_finishing_the_game_releases_the_thread(self):
        cog, channel = _gamble_cog()
        _hangman_pending(cog)
        thread = FakeGambleThread()
        cog.gamble_state["hangman_thread_id"] = thread.id

        await cog._handle_silas_gambling_message(_silas_message(thread), HANGMAN_WON_TEXT)

        assert cog.gamble_state["hangman_active"] is False
        assert cog.gamble_state["hangman_thread_id"] is None
        assert cog.gamble_state["hangman_ended_at"] is not None
        # The payout line carries a balance; it is Gary's, so it counts.
        assert cog.gamble_state["last_known_balance"] == 10131
        assert thread.sent == []

    async def test_cooldown_refusal_releases_a_stale_thread(self):
        cog, channel = _gamble_cog()
        _hangman_pending(cog)
        cog.gamble_state["hangman_thread_id"] = _THREAD_ID

        await cog._handle_silas_gambling_message(_silas_message(channel), COOLDOWN_TEXT)

        assert cog.gamble_state["hangman_active"] is False
        assert cog.gamble_state["hangman_thread_id"] is None

    async def test_blackjack_words_in_a_thread_are_not_a_hand(self):
        """A `!session` table under the gamble channel shows other players'
        hands. None of it is Gary's blackjack, which he deals in the channel."""
        cog, channel = _gamble_cog()
        cog.gamble_state["blackjack_active"] = True
        _hangman_pending(cog)
        thread = FakeGambleThread(name="New Gambling Thread")

        await cog._handle_silas_gambling_message(
            _silas_message(thread),
            "🃏 Blackjack\n**Dealer:** J♦  🂠\n**Gary (12):** Q♠  2♠\n\nUse the buttons below, or type `hit`, `stand`, or `double`.",
        )

        assert thread.sent == [] and channel.sent == []
        assert cog.gamble_state["blackjack_active"] is True

    async def test_the_current_blackjack_prompt_still_gets_an_answer(self):
        cog, channel = _gamble_cog()
        cog.gamble_state["blackjack_active"] = True

        await cog._handle_silas_gambling_message(
            _silas_message(channel),
            "🃏 Blackjack\n**Dealer:** J♦  🂠\n**Gary (16):** Q♠  6♠\n\nUse the buttons below, or type `hit`, `stand`, or `double`.",
        )

        assert channel.sent == ["hit"]

    async def test_stale_game_clear_releases_the_thread(self):
        cog, channel = _gamble_cog()
        _ready_to_gamble(cog)
        cog.gamble_state["hangman_active"] = True
        cog.gamble_state["hangman_started_at"] = datetime.now(timezone.utc) - AICog.HM_ACTIVE_TIMEOUT - timedelta(minutes=1)
        cog.gamble_state["hangman_thread_id"] = _THREAD_ID

        result = await cog.run_gamble_step(bypass_cooldown=True)

        assert result.startswith("Cleared stale")
        assert cog.gamble_state["hangman_thread_id"] is None

    async def test_a_natural_settled_during_the_send_ends_the_hand(self):
        cog, channel = _gamble_cog()
        _ready_to_gamble(cog)
        cog.gamble_state["morning_hangman_done"] = True

        async def send_then_silas_replies(content=None, **kwargs):
            channel.sent.append(content)
            await cog._handle_silas_gambling_message(
                _silas_message(channel),
                "🃏 Blackjack!\n**Dealer (14):** 5♦  9♦\n**Gary (21):** A♠  J♣\n\n**Gary** wins **3,087** 🪙! Balance: 13,116 🪙",
            )

        channel.send = send_then_silas_replies

        await cog.run_gamble_step(bypass_cooldown=True)

        assert channel.sent[0].startswith("!bj ")
        assert cog.gamble_state["blackjack_active"] is False
        assert cog.gamble_state["last_known_balance"] == 13116

    async def test_stale_blackjack_stands_once_then_frees_the_table(self):
        cog, channel = _gamble_cog()
        _ready_to_gamble(cog)
        cog.gamble_state["morning_hangman_done"] = True
        cog.gamble_state["blackjack_active"] = True
        cog.gamble_state["last_action_at"] = datetime.now(timezone.utc) - AICog.BJ_ACTIVE_TIMEOUT - timedelta(minutes=1)

        await cog.run_gamble_step(bypass_cooldown=True)

        assert channel.sent == ["stand"]
        assert cog.gamble_state["blackjack_active"] is False


# ---------------------------------------------------------------------------
# Take-profit report — the numbers in it must be the ones it just wrote
# ---------------------------------------------------------------------------

_REPORT_CHANNEL_ID = 100000000000000010


class TestTakeProfitReport:
    def _cog_with_report_channel(self):
        gamble = FakeGambleChannel()
        report = FakeGambleChannel(channel_id=_REPORT_CHANNEL_ID)
        shared.runtime_settings["gary_gamble_enabled"] = True
        shared.runtime_settings["gary_gamble_channel_id"] = gamble.id
        shared.runtime_settings["gary_gamble_report_channel_id"] = report.id
        bot = MagicMock()
        bot.get_channel.side_effect = lambda cid: report if int(cid) == report.id else gamble
        return AICog(bot), report

    async def test_report_shows_the_new_anchor_not_the_one_it_replaced(self):
        cog, report = self._cog_with_report_channel()
        _ready_to_gamble(cog, balance=24072)
        # The numbers from the filed bug report: 24072 is past 1.5x of 15571.
        cog.gamble_state["session_anchor_balance"] = 15571
        cog.gamble_state["morning_hangman_done"] = True

        await cog.run_gamble_step(bypass_cooldown=True)

        summary = next(m for m in report.sent if "Take-profit" in m)
        assert "Anchor: 24072" in summary
        assert "15571" not in summary
        assert cog.gamble_state["session_anchor_balance"] == 24072


# ---------------------------------------------------------------------------
# Sessions logged while stopped out
#
# A stop-loss clears session_id, and the anchor survives it on purpose — it is
# the line the balance has to climb back over. But "no session yet" alone used
# to be enough to open one, and the stop-loss below closed it again before the
# same tick returned. Every 30 seconds, for three months: 170,348 of the
# 170,485 rows in the live `gary_sessions` table were that one zero-delta row.
# ---------------------------------------------------------------------------


class TestStoppedOutSessionChurn:
    def _stopped_out(self, balance=22719, anchor=30373):
        """Gary's live state when this was found: 22719 against a 22779 line."""
        cog, channel = _gamble_cog()
        _ready_to_gamble(cog, balance=balance)
        cog.gamble_state["session_anchor_balance"] = anchor
        cog.gamble_state["session_id"] = None
        cog.gamble_state["morning_hangman_done"] = True
        cog.gamble_state["hangman_ended_at"] = datetime.now(timezone.utc)
        return cog, channel

    async def test_a_tick_under_the_line_logs_no_session(self):
        cog, _ = self._stopped_out()
        before = shared.get_gary_gamble_stats()["total_sessions"]

        for _ in range(5):
            result = await cog.run_gamble_step(bypass_cooldown=True)
            assert result.startswith("BJ stop-loss")

        assert shared.get_gary_gamble_stats()["total_sessions"] == before
        assert cog.gamble_state["session_id"] is None

    async def test_a_recovered_balance_still_opens_one(self):
        # 22780 clears the 22779 line by a coin — a hangman win is all it takes.
        cog, _ = self._stopped_out(balance=22780)
        before = shared.get_gary_gamble_stats()["total_sessions"]

        await cog.run_gamble_step(bypass_cooldown=True)

        assert shared.get_gary_gamble_stats()["total_sessions"] == before + 1
        assert cog.gamble_state["session_id"] is not None
