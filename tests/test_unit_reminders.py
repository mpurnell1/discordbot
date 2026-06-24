"""Unit tests for the reminders feature: time parsing, scheduling, DB ops,
the firing loop, and the .remindme / .reminders commands."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.reminders import (
    CENTRAL_TZ,
    MAX_ACTIVE_REMINDERS,
    RemindersCog,
    _add_month,
    compute_next_fire,
    count_active,
    create_reminder,
    deactivate_reminder,
    describe_repeat,
    get_due_reminders,
    get_user_reminders,
    parse_interval,
    parse_when,
    reschedule_reminder,
)
from tests.conftest import FakeAuthor, FakeContext, FakeGuild

# A fixed reference "now": Tuesday, June 23 2026, 10:00 AM Central.
NOW = datetime(2026, 6, 23, 10, 0, tzinfo=CENTRAL_TZ)


# ---------------------------------------------------------------------------
# parse_when
# ---------------------------------------------------------------------------
class TestParseWhen:
    def test_relative_minutes(self):
        assert parse_when("90m", NOW) == NOW + timedelta(minutes=90)

    def test_relative_hours(self):
        assert parse_when("2h", NOW) == NOW + timedelta(hours=2)

    def test_relative_combined(self):
        assert parse_when("1h30m", NOW) == NOW + timedelta(hours=1, minutes=30)

    def test_relative_days_and_weeks(self):
        assert parse_when("1d", NOW) == NOW + timedelta(days=1)
        assert parse_when("1w", NOW) == NOW + timedelta(weeks=1)

    def test_strips_leading_in(self):
        assert parse_when("in 2h", NOW) == NOW + timedelta(hours=2)

    def test_bare_clock_future_today(self):
        # 2pm is later today.
        assert parse_when("2pm", NOW) == NOW.replace(hour=14, minute=0)

    def test_bare_clock_past_rolls_to_tomorrow(self):
        # 9am already passed (now is 10am) -> tomorrow 9am.
        assert parse_when("9am", NOW) == (NOW + timedelta(days=1)).replace(hour=9, minute=0)

    def test_tomorrow_with_time(self):
        assert parse_when("tomorrow 9am", NOW) == (NOW + timedelta(days=1)).replace(hour=9, minute=0)

    def test_tonight_default_8pm(self):
        assert parse_when("tonight", NOW) == NOW.replace(hour=20, minute=0)

    def test_today_with_time(self):
        assert parse_when("today 5pm", NOW) == NOW.replace(hour=17, minute=0)

    def test_slash_date(self):
        assert parse_when("7/1 2pm", NOW) == datetime(2026, 7, 1, 14, 0, tzinfo=CENTRAL_TZ)

    def test_iso_datetime(self):
        assert parse_when("2026-07-01 14:30", NOW) == datetime(2026, 7, 1, 14, 30, tzinfo=CENTRAL_TZ)

    def test_month_name_date(self):
        assert parse_when("jul 1 2:30pm", NOW) == datetime(2026, 7, 1, 14, 30, tzinfo=CENTRAL_TZ)

    def test_past_dateless_rolls_to_next_year(self):
        # Jan 1 already passed this year -> next year.
        assert parse_when("1/1 9am", NOW) == datetime(2027, 1, 1, 9, 0, tzinfo=CENTRAL_TZ)

    @pytest.mark.parametrize("bad", ["", "   ", "blah", "next tuesday-ish", "soon"])
    def test_invalid_raises(self, bad):
        with pytest.raises(ValueError):
            parse_when(bad, NOW)


# ---------------------------------------------------------------------------
# parse_interval
# ---------------------------------------------------------------------------
class TestParseInterval:
    def test_days(self):
        assert parse_interval("2 days") == (2, "days")

    def test_hours_short(self):
        assert parse_interval("6h") == (6, "hours")

    def test_every_prefix(self):
        assert parse_interval("every 3 days") == (3, "days")

    @pytest.mark.parametrize("bad", ["5 weeks", "0 days", "abc", "day", "-1h"])
    def test_invalid_raises(self, bad):
        with pytest.raises(ValueError):
            parse_interval(bad)


# ---------------------------------------------------------------------------
# compute_next_fire / _add_month
# ---------------------------------------------------------------------------
class TestComputeNextFire:
    def test_none_returns_none(self):
        assert compute_next_fire(NOW, "none", None, None, NOW) is None

    def test_daily(self):
        assert compute_next_fire(NOW, "daily", None, None, NOW) == NOW + timedelta(days=1)

    def test_weekly(self):
        assert compute_next_fire(NOW, "weekly", None, None, NOW) == NOW + timedelta(weeks=1)

    def test_interval_hours(self):
        assert compute_next_fire(NOW, "interval", 6, "hours", NOW) == NOW + timedelta(hours=6)

    def test_interval_days(self):
        assert compute_next_fire(NOW, "interval", 3, "days", NOW) == NOW + timedelta(days=3)

    def test_weekdays_skips_weekend(self):
        # From a Friday, the next weekday fire must land Mon–Fri and be in the future.
        friday = datetime(2026, 6, 26, 9, 0, tzinfo=CENTRAL_TZ)
        nxt = compute_next_fire(friday, "weekdays", None, None, friday)
        assert nxt.weekday() < 5
        assert nxt > friday

    def test_advances_past_now_after_downtime(self):
        # Bot was down 10 days; a daily reminder should jump to the next future
        # occurrence, not replay every missed day.
        stale = NOW - timedelta(days=10)
        nxt = compute_next_fire(stale, "daily", None, None, NOW)
        assert nxt > NOW
        assert nxt <= NOW + timedelta(days=1)

    def test_add_month_clamps_day(self):
        # Jan 31 -> Feb 28 (2026 is not a leap year).
        assert _add_month(datetime(2026, 1, 31, 9, 0, tzinfo=CENTRAL_TZ)) == datetime(2026, 2, 28, 9, 0, tzinfo=CENTRAL_TZ)

    def test_monthly_rolls_over_year(self):
        dec = datetime(2026, 12, 15, 9, 0, tzinfo=CENTRAL_TZ)
        assert compute_next_fire(dec, "monthly", None, None, dec) == datetime(2027, 1, 15, 9, 0, tzinfo=CENTRAL_TZ)


def test_describe_repeat():
    assert describe_repeat("none", None, None) == "Does not repeat"
    assert describe_repeat("daily", None, None) == "Every day"
    assert describe_repeat("interval", 2, "days") == "Every 2 days"


# ---------------------------------------------------------------------------
# DB ops
# ---------------------------------------------------------------------------
def _make_reminder(user_id=5, channel_id=100, when=None, **kw):
    when = when or (NOW + timedelta(hours=1))
    return create_reminder(user_id, channel_id, 1000, "buy milk", when, **kw)


class TestDbOps:
    def test_create_and_count(self):
        rid = _make_reminder()
        assert rid > 0
        assert count_active(5) == 1

    def test_get_user_reminders_filters_by_user(self):
        _make_reminder(user_id=5)
        _make_reminder(user_id=6)
        assert len(get_user_reminders(5)) == 1
        assert len(get_user_reminders(6)) == 1

    def test_get_due_only_returns_past(self):
        _make_reminder(user_id=5, when=NOW - timedelta(minutes=5))  # due
        _make_reminder(user_id=5, when=NOW + timedelta(hours=2))  # future
        due = get_due_reminders(NOW.astimezone(timezone.utc))
        assert len(due) == 1
        assert due[0]["text"] == "buy milk"

    def test_deactivate_wrong_user_is_noop(self):
        rid = _make_reminder(user_id=5)
        assert deactivate_reminder(rid, user_id=999) is False
        assert count_active(5) == 1

    def test_deactivate_right_user(self):
        rid = _make_reminder(user_id=5)
        assert deactivate_reminder(rid, user_id=5) is True
        assert count_active(5) == 0
        assert get_user_reminders(5) == []

    def test_reschedule_moves_next_fire(self):
        rid = _make_reminder(user_id=5, when=NOW - timedelta(minutes=1))
        ref = NOW.astimezone(timezone.utc)
        assert len(get_due_reminders(ref)) == 1
        reschedule_reminder(rid, NOW + timedelta(days=1))
        assert get_due_reminders(ref) == []


# ---------------------------------------------------------------------------
# Firing loop
# ---------------------------------------------------------------------------
def _real_past():
    """A clearly-past Central time (the firing loop reads the real clock)."""
    return datetime.now(CENTRAL_TZ) - timedelta(minutes=5)


def _fake_bot():
    bot = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.get_channel.return_value = channel
    bot.fetch_channel = AsyncMock(return_value=channel)
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user.return_value = user
    bot.fetch_user = AsyncMock(return_value=user)
    return bot, channel, user


class TestReminderFiring:
    async def test_one_off_fires_and_retires(self):
        bot, channel, _ = _fake_bot()
        cog = RemindersCog(bot)
        _make_reminder(user_id=5, channel_id=100, when=_real_past())
        await cog.reminder_check.coro(cog)
        channel.send.assert_called_once()
        assert "<@5>" in channel.send.call_args.kwargs.get("content", "")
        assert count_active(5) == 0  # one-off deactivated

    async def test_repeating_fires_and_reschedules(self):
        bot, channel, _ = _fake_bot()
        cog = RemindersCog(bot)
        _make_reminder(user_id=5, channel_id=100, when=_real_past(), repeat_kind="daily")
        await cog.reminder_check.coro(cog)
        channel.send.assert_called_once()
        assert count_active(5) == 1  # still active
        # Next fire moved into the future.
        assert get_due_reminders(datetime.now(timezone.utc)) == []

    async def test_dm_destination_sends_dm(self):
        bot, channel, user = _fake_bot()
        cog = RemindersCog(bot)
        _make_reminder(user_id=5, channel_id=100, when=_real_past(), destination="dm")
        await cog.reminder_check.coro(cog)
        user.send.assert_called_once()
        channel.send.assert_not_called()

    async def test_delivery_failure_does_not_wedge(self):
        bot, channel, _ = _fake_bot()
        channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "boom"))
        cog = RemindersCog(bot)
        _make_reminder(user_id=5, channel_id=100, when=_real_past())
        await cog.reminder_check.coro(cog)  # must not raise
        assert count_active(5) == 0  # still retired despite the failure


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def _cog():
    return RemindersCog(MagicMock())


class TestRemindMeCommand:
    async def test_no_text_sends_usage(self):
        ctx = FakeContext(author=FakeAuthor(user_id=5), guild=FakeGuild())
        await _cog().remindme.callback(_cog(), ctx, text=None)
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_opens_card(self):
        ctx = FakeContext(author=FakeAuthor(user_id=5), guild=FakeGuild())
        await _cog().remindme.callback(_cog(), ctx, text="buy milk")
        embed = ctx.sent[0]["embed"]
        assert "Reminder" in embed.title
        assert "buy milk" in (embed.description or "")
        assert ctx.sent[0].get("view") is not None

    async def test_too_long_rejected(self):
        ctx = FakeContext(author=FakeAuthor(user_id=5), guild=FakeGuild())
        await _cog().remindme.callback(_cog(), ctx, text="x" * 600)
        assert "too long" in (ctx.sent[0].get("content") or "").lower()

    async def test_max_active_rejected(self):
        for _ in range(MAX_ACTIVE_REMINDERS):
            _make_reminder(user_id=7)
        ctx = FakeContext(author=FakeAuthor(user_id=7), guild=FakeGuild())
        await _cog().remindme.callback(_cog(), ctx, text="one more")
        assert "active reminders" in (ctx.sent[0].get("content") or "").lower()


class TestRemindersCommand:
    async def test_empty_list(self):
        ctx = FakeContext(author=FakeAuthor(user_id=5), guild=FakeGuild())
        await _cog().reminders.callback(_cog(), ctx)
        assert "no active reminders" in (ctx.sent[0].get("content") or "").lower()

    async def test_list_shows_items(self):
        _make_reminder(user_id=5)
        ctx = FakeContext(author=FakeAuthor(user_id=5), guild=FakeGuild())
        await _cog().reminders.callback(_cog(), ctx)
        embed = ctx.sent[0]["embed"]
        assert "buy milk" in (embed.description or "")

    async def test_cancel_existing(self):
        rid = _make_reminder(user_id=5)
        ctx = FakeContext(author=FakeAuthor(user_id=5), guild=FakeGuild())
        await _cog().reminders.callback(_cog(), ctx, "cancel", rid)
        assert "cancelled" in (ctx.sent[0].get("content") or "").lower()
        assert count_active(5) == 0

    async def test_cancel_missing(self):
        ctx = FakeContext(author=FakeAuthor(user_id=5), guild=FakeGuild())
        await _cog().reminders.callback(_cog(), ctx, "cancel", 9999)
        assert "no active reminder" in (ctx.sent[0].get("content") or "").lower()
