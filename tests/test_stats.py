"""Tests for stats tracking: puzzle history, game results, balance history."""
from datetime import date, timedelta

import pytest

import shared


# ---------------------------------------------------------------------------
# Puzzle stats
# ---------------------------------------------------------------------------

class TestGetPuzzleStats:
    USER = 2001

    def _log(self, days_ago: int, attempts: int = 1):
        d = str(date.today() - timedelta(days=days_ago))
        shared.log_puzzle_solve(self.USER, d, attempts)

    def test_no_history_returns_zeros(self):
        s = shared.get_puzzle_stats(self.USER)
        assert s["total_solves"] == 0
        assert s["streak"] == 0
        assert s["avg_attempts"] == 0.0

    def test_solved_today_streak_one(self):
        self._log(0)
        s = shared.get_puzzle_stats(self.USER)
        assert s["streak"] == 1
        assert s["total_solves"] == 1

    def test_solved_yesterday_only_streak_one(self):
        self._log(1)
        s = shared.get_puzzle_stats(self.USER)
        assert s["streak"] == 1

    def test_consecutive_days_streak(self):
        for days_ago in range(5):
            self._log(days_ago)
        s = shared.get_puzzle_stats(self.USER)
        assert s["streak"] == 5

    def test_gap_breaks_streak(self):
        self._log(0)
        self._log(1)
        # gap on day 2
        self._log(3)
        s = shared.get_puzzle_stats(self.USER)
        assert s["streak"] == 2

    def test_avg_attempts(self):
        shared.log_puzzle_solve(self.USER, str(date.today()), 1)
        shared.log_puzzle_solve(self.USER, str(date.today() - timedelta(days=1)), 3)
        s = shared.get_puzzle_stats(self.USER)
        assert s["avg_attempts"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Game stats
# ---------------------------------------------------------------------------

class TestGetGameStats:
    USER = 2002
    OPP = 2003

    def test_no_history_returns_zeros(self):
        s = shared.get_game_stats(self.USER)
        assert s["ttt"] == {"win": 0, "loss": 0, "draw": 0}
        assert s["c4"] == {"win": 0, "loss": 0, "draw": 0}

    def test_win_recorded(self):
        shared.log_game_result(self.USER, self.OPP, "ttt", "win")
        s = shared.get_game_stats(self.USER)
        assert s["ttt"]["win"] == 1

    def test_both_players_recorded_on_win(self):
        shared.log_game_result(self.USER, self.OPP, "ttt", "win")
        shared.log_game_result(self.OPP, self.USER, "ttt", "loss")
        winner = shared.get_game_stats(self.USER)
        loser = shared.get_game_stats(self.OPP)
        assert winner["ttt"]["win"] == 1 and winner["ttt"]["loss"] == 0
        assert loser["ttt"]["loss"] == 1 and loser["ttt"]["win"] == 0

    def test_head_to_head_filtered(self):
        third = 2004
        shared.log_game_result(self.USER, self.OPP, "ttt", "win")
        shared.log_game_result(self.USER, third, "ttt", "loss")
        h2h = shared.get_game_stats(self.USER, self.OPP)
        overall = shared.get_game_stats(self.USER)
        assert h2h["ttt"]["win"] == 1 and h2h["ttt"]["loss"] == 0
        assert overall["ttt"]["win"] == 1 and overall["ttt"]["loss"] == 1

    def test_draw_counts_for_both(self):
        shared.log_game_result(self.USER, self.OPP, "c4", "draw")
        shared.log_game_result(self.OPP, self.USER, "c4", "draw")
        assert shared.get_game_stats(self.USER)["c4"]["draw"] == 1
        assert shared.get_game_stats(self.OPP)["c4"]["draw"] == 1

    def test_ttt_and_c4_tracked_separately(self):
        shared.log_game_result(self.USER, self.OPP, "ttt", "win")
        shared.log_game_result(self.USER, self.OPP, "c4", "loss")
        s = shared.get_game_stats(self.USER)
        assert s["ttt"]["win"] == 1 and s["ttt"]["loss"] == 0
        assert s["c4"]["loss"] == 1 and s["c4"]["win"] == 0


# ---------------------------------------------------------------------------
# Economy stats
# ---------------------------------------------------------------------------

class TestGetEconomyStats:
    USER = 2005

    def test_no_history_returns_current_balance(self):
        shared.get_balance(self.USER)  # create row
        s = shared.get_economy_stats(self.USER)
        assert s["peak_balance"] == shared.STARTING_BALANCE
        assert s["best_day_gain"] == 0
        assert s["worst_day_loss"] == 0

    def test_peak_balance_tracked(self):
        shared.update_balance(self.USER, 500)
        shared.update_balance(self.USER, -200)
        s = shared.get_economy_stats(self.USER)
        assert s["peak_balance"] == shared.STARTING_BALANCE + 500

    def test_best_day_gain(self):
        shared.update_balance(self.USER, 1000)
        s = shared.get_economy_stats(self.USER)
        assert s["best_day_gain"] >= 1000

    def test_worst_day_loss(self):
        shared.update_balance(self.USER, 500)
        shared.update_balance(self.USER, -800)
        s = shared.get_economy_stats(self.USER)
        assert s["worst_day_loss"] < 0


# ---------------------------------------------------------------------------
# log_command
# ---------------------------------------------------------------------------

def test_log_command_persists():
    shared.log_command(3001, "balance", 9999)
    row = shared.db.execute(
        "SELECT command_name, guild_id FROM command_log WHERE user_id = 3001"
    ).fetchone()
    assert row == ("balance", 9999)


def test_log_command_dm_guild_null():
    shared.log_command(3002, "help", None)
    row = shared.db.execute(
        "SELECT guild_id FROM command_log WHERE user_id = 3002"
    ).fetchone()
    assert row[0] is None


# ---------------------------------------------------------------------------
# Gambling stats
# ---------------------------------------------------------------------------

class TestGetGamblingStats:
    USER = 2006

    def test_no_history_returns_zeros(self):
        s = shared.get_gambling_stats(self.USER)
        assert s["coinflip"] == {"net": 0, "wagered": 0, "hands": 0}
        assert s["slots"] == {"net": 0, "wagered": 0, "hands": 0}
        assert s["blackjack"] == {"net": 0, "wagered": 0, "hands": 0}

    def test_win_adds_to_net(self):
        shared.log_gambling(self.USER, "coinflip", 100, 100)
        s = shared.get_gambling_stats(self.USER)
        assert s["coinflip"]["net"] == 100
        assert s["coinflip"]["wagered"] == 100
        assert s["coinflip"]["hands"] == 1

    def test_loss_subtracts_from_net(self):
        shared.log_gambling(self.USER, "slots", 50, -50)
        s = shared.get_gambling_stats(self.USER)
        assert s["slots"]["net"] == -50

    def test_net_accumulates_across_sessions(self):
        shared.log_gambling(self.USER, "blackjack", 200, 200)
        shared.log_gambling(self.USER, "blackjack", 200, -200)
        shared.log_gambling(self.USER, "blackjack", 100, 100)
        s = shared.get_gambling_stats(self.USER)
        assert s["blackjack"]["net"] == 100
        assert s["blackjack"]["hands"] == 3
        assert s["blackjack"]["wagered"] == 500

    def test_game_types_tracked_independently(self):
        shared.log_gambling(self.USER, "coinflip", 10, 10)
        shared.log_gambling(self.USER, "slots", 20, -20)
        s = shared.get_gambling_stats(self.USER)
        assert s["coinflip"]["net"] == 10
        assert s["slots"]["net"] == -20
        assert s["blackjack"]["net"] == 0
