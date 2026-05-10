"""Tests for activity streaks and Gary session tracking."""
from datetime import date, datetime, timedelta
from unittest.mock import patch

import shared
from shared import (
    STREAK_MILESTONES,
    get_activity_streak,
    get_gary_gamble_stats,
    log_gary_session_end,
    log_gary_session_start,
    update_activity_streak,
    update_gary_session_peak,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_USER = 1001
_CENTRAL = shared.CENTRAL_TZ

def _iso(d: date, hour: int = 12) -> str:
    """Return a Central-timezone ISO string for the given date and hour."""
    return datetime(d.year, d.month, d.day, hour, 0, 0, tzinfo=_CENTRAL).isoformat()

def _ensure_user():
    shared.get_balance(_USER)

def _patch_today(d: date):
    """Context manager: freeze shared.datetime.now(CENTRAL_TZ) to return `d` at noon."""
    fake_now = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=_CENTRAL)
    return patch("shared.datetime", wraps=datetime_with_fixed_now(fake_now))

class datetime_with_fixed_now:
    """Wraps datetime but overrides now() to return a fixed value."""
    def __init__(self, fixed: datetime):
        self._fixed = fixed
        self._real = datetime

    def now(self, tz=None):
        if tz is not None:
            return self._fixed.astimezone(tz)
        return self._fixed

    def fromisoformat(self, s):
        return self._real.fromisoformat(s)

    def __call__(self, *args, **kwargs):
        return self._real(*args, **kwargs)


# ---------------------------------------------------------------------------
# Activity streak: basic increment / reset
# ---------------------------------------------------------------------------

def test_streak_first_day_is_one():
    _ensure_user()
    with patch("shared.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 9, 12, 0, 0, tzinfo=_CENTRAL)
        mock_dt.fromisoformat = datetime.fromisoformat
        streak, is_milestone, bonus = update_activity_streak(_USER, None)
    assert streak == 1
    assert not is_milestone
    assert bonus == 0


def test_streak_increments_on_consecutive_days():
    _ensure_user()
    today = date(2026, 5, 9)
    yesterday_iso = _iso(today - timedelta(days=1))

    shared.db.execute(
        "UPDATE users SET activity_streak = 3, activity_streak_max = 3 WHERE user_id = ?", (_USER,)
    )
    shared.db.commit()

    with patch("shared.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 9, 12, 0, 0, tzinfo=_CENTRAL)
        mock_dt.fromisoformat = datetime.fromisoformat
        streak, _, _ = update_activity_streak(_USER, yesterday_iso)

    assert streak == 4
    assert get_activity_streak(_USER) == (4, 4)


def test_streak_resets_on_missed_day():
    _ensure_user()
    today = date(2026, 5, 9)
    two_days_ago_iso = _iso(today - timedelta(days=2))

    shared.db.execute(
        "UPDATE users SET activity_streak = 10, activity_streak_max = 10 WHERE user_id = ?", (_USER,)
    )
    shared.db.commit()

    with patch("shared.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 9, 12, 0, 0, tzinfo=_CENTRAL)
        mock_dt.fromisoformat = datetime.fromisoformat
        streak, _, _ = update_activity_streak(_USER, two_days_ago_iso)

    assert streak == 1
    current, best = get_activity_streak(_USER)
    assert current == 1
    assert best == 10  # max preserved


def test_streak_max_updates_when_new_high():
    _ensure_user()
    today = date(2026, 5, 9)
    yesterday_iso = _iso(today - timedelta(days=1))

    shared.db.execute(
        "UPDATE users SET activity_streak = 6, activity_streak_max = 6 WHERE user_id = ?", (_USER,)
    )
    shared.db.commit()

    with patch("shared.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 9, 12, 0, 0, tzinfo=_CENTRAL)
        mock_dt.fromisoformat = datetime.fromisoformat
        streak, is_milestone, bonus = update_activity_streak(_USER, yesterday_iso)

    assert streak == 7
    assert is_milestone
    assert bonus == STREAK_MILESTONES[7]
    _, best = get_activity_streak(_USER)
    assert best == 7


def test_streak_max_preserved_through_reset():
    _ensure_user()
    today = date(2026, 5, 9)
    two_days_ago_iso = _iso(today - timedelta(days=2))

    shared.db.execute(
        "UPDATE users SET activity_streak = 29, activity_streak_max = 45 WHERE user_id = ?", (_USER,)
    )
    shared.db.commit()

    with patch("shared.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 9, 12, 0, 0, tzinfo=_CENTRAL)
        mock_dt.fromisoformat = datetime.fromisoformat
        streak, _, _ = update_activity_streak(_USER, two_days_ago_iso)

    assert streak == 1
    _, best = get_activity_streak(_USER)
    assert best == 45  # max unchanged by reset


def test_streak_empty_prev_daily_treated_as_new():
    """Empty string last_daily (DEFAULT) should start streak at 1, not crash."""
    _ensure_user()
    with patch("shared.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 9, 12, 0, 0, tzinfo=_CENTRAL)
        mock_dt.fromisoformat = datetime.fromisoformat
        streak, _, _ = update_activity_streak(_USER, "")
    assert streak == 1


def test_streak_milestone_30():
    _ensure_user()
    today = date(2026, 5, 9)
    yesterday_iso = _iso(today - timedelta(days=1))

    shared.db.execute(
        "UPDATE users SET activity_streak = 29, activity_streak_max = 29 WHERE user_id = ?", (_USER,)
    )
    shared.db.commit()

    with patch("shared.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 9, 12, 0, 0, tzinfo=_CENTRAL)
        mock_dt.fromisoformat = datetime.fromisoformat
        streak, is_milestone, bonus = update_activity_streak(_USER, yesterday_iso)

    assert streak == 30
    assert is_milestone
    assert bonus == STREAK_MILESTONES[30]


def test_streak_no_bonus_at_non_milestone():
    _ensure_user()
    today = date(2026, 5, 9)
    yesterday_iso = _iso(today - timedelta(days=1))

    shared.db.execute(
        "UPDATE users SET activity_streak = 5, activity_streak_max = 5 WHERE user_id = ?", (_USER,)
    )
    shared.db.commit()

    with patch("shared.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 9, 12, 0, 0, tzinfo=_CENTRAL)
        mock_dt.fromisoformat = datetime.fromisoformat
        streak, is_milestone, bonus = update_activity_streak(_USER, yesterday_iso)

    assert streak == 6
    assert not is_milestone
    assert bonus == 0


def test_get_activity_streak_returns_zero_for_new_user():
    _ensure_user()
    current, best = get_activity_streak(_USER)
    assert current == 0
    assert best == 0


# ---------------------------------------------------------------------------
# Gary session tracking
# ---------------------------------------------------------------------------

def test_session_start_creates_ongoing_row():
    sid = log_gary_session_start(1000)
    stats = get_gary_gamble_stats()
    # ongoing sessions are excluded from completed stats
    assert stats["total_sessions"] == 0
    # but the row exists
    row = shared.db.execute("SELECT outcome, start_balance FROM gary_sessions WHERE id = ?", (sid,)).fetchone()
    assert row is not None
    assert row[0] == "ongoing"
    assert row[1] == 1000


def test_session_peak_update():
    sid = log_gary_session_start(1000)
    update_gary_session_peak(sid, 1200)
    update_gary_session_peak(sid, 1100)  # lower — should not decrease
    row = shared.db.execute("SELECT peak_balance FROM gary_sessions WHERE id = ?", (sid,)).fetchone()
    assert row[0] == 1200


def test_session_end_stop_loss():
    sid = log_gary_session_start(1000)
    log_gary_session_end(sid, 750, "stop_loss")
    stats = get_gary_gamble_stats()
    assert stats["total_sessions"] == 1
    assert stats["net"] == -250
    assert stats["worst"] == -250
    assert stats["best"] == -250
    assert stats["wins"] == 0


def test_session_end_take_profit():
    sid = log_gary_session_start(1000)
    log_gary_session_end(sid, 1500, "take_profit")
    stats = get_gary_gamble_stats()
    assert stats["total_sessions"] == 1
    assert stats["net"] == 500
    assert stats["best"] == 500
    assert stats["wins"] == 1


def test_multiple_sessions_aggregate_correctly():
    s1 = log_gary_session_start(1000)
    log_gary_session_end(s1, 1400, "take_profit")  # +400
    s2 = log_gary_session_start(1400)
    log_gary_session_end(s2, 1050, "stop_loss")    # -350
    log_gary_session_start(1050)
    # s3 still ongoing — should be excluded

    stats = get_gary_gamble_stats()
    assert stats["total_sessions"] == 2
    assert stats["net"] == 50
    assert stats["best"] == 400
    assert stats["worst"] == -350
    assert stats["wins"] == 1


def test_recent_sessions_capped_at_ten():
    for i in range(12):
        sid = log_gary_session_start(1000)
        log_gary_session_end(sid, 1100 + i, "take_profit")

    stats = get_gary_gamble_stats()
    assert stats["total_sessions"] == 12
    assert len(stats["recent"]) == 10


def test_stats_empty_when_no_sessions():
    stats = get_gary_gamble_stats()
    assert stats["total_sessions"] == 0
    assert stats["net"] == 0
    assert stats["best"] == 0
    assert stats["worst"] == 0
    assert stats["wins"] == 0
    assert stats["recent"] == []
