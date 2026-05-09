"""Time-dependent tests: daily-claim availability, 5am Central scratch reset."""
from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

import shared
from shared import is_daily_available, CENTRAL_TZ


# ---------------------------------------------------------------------------
# is_daily_available — has an injectable `now=` so we don't need freezegun here
# ---------------------------------------------------------------------------
class TestIsDailyAvailable:
    USER = 7

    def _set_last_daily(self, when_iso: str):
        shared.get_balance(self.USER)  # ensure row exists
        shared.db.execute(
            "UPDATE users SET last_daily = ? WHERE user_id = ?",
            (when_iso, self.USER),
        )
        shared.db.commit()

    def test_no_prior_claim_is_available(self):
        shared.get_balance(self.USER)
        available, remaining = is_daily_available(self.USER)
        assert available is True
        assert remaining == timedelta(0)

    def test_just_claimed_is_unavailable(self):
        now = datetime.now(CENTRAL_TZ)
        self._set_last_daily(now.isoformat())
        available, remaining = is_daily_available(self.USER, now=now)
        assert available is False
        assert remaining > timedelta(0)
        assert remaining <= timedelta(hours=24)

    def test_just_under_24h_unavailable(self):
        now = datetime.now(CENTRAL_TZ)
        last = now - timedelta(hours=23, minutes=59)
        self._set_last_daily(last.isoformat())
        available, remaining = is_daily_available(self.USER, now=now)
        assert available is False
        # Should be ~1 minute remaining.
        assert timedelta(seconds=30) < remaining < timedelta(minutes=2)

    def test_exactly_24h_available(self):
        now = datetime.now(CENTRAL_TZ)
        last = now - timedelta(hours=24)
        self._set_last_daily(last.isoformat())
        available, remaining = is_daily_available(self.USER, now=now)
        assert available is True
        assert remaining == timedelta(0)

    def test_naive_timestamp_coerced_to_utc(self):
        """Legacy rows may have naive ISO strings — they should still parse."""
        # 25 hours ago, but written without timezone info.
        now = datetime.now(timezone.utc)
        legacy = (now - timedelta(hours=25)).replace(tzinfo=None).isoformat()
        self._set_last_daily(legacy)
        available, _ = is_daily_available(self.USER, now=now.astimezone(CENTRAL_TZ))
        assert available is True


# ---------------------------------------------------------------------------
# AICog._scratch_reset_key — 5am Central rollover
# ---------------------------------------------------------------------------
class TestScratchResetKey:
    @pytest.fixture
    def cog(self):
        from modules.ai import AICog
        # Construct an AICog without going through full bot setup.
        # Its __init__ touches the DB but not the bot client.
        bot = type("BotStub", (), {})()
        return AICog(bot)

    def test_4_59am_central_returns_previous_day_key(self, cog):
        # 4:59 AM Central on 2026-05-08 -> previous-day key 2026-05-07.
        moment = datetime(2026, 5, 8, 4, 59, tzinfo=CENTRAL_TZ).astimezone(timezone.utc)
        key = cog._scratch_reset_key(moment)
        assert key == "2026-05-07"

    def test_5_00am_central_returns_same_day_key(self, cog):
        # 5:00 AM Central on 2026-05-08 -> 2026-05-08.
        moment = datetime(2026, 5, 8, 5, 0, tzinfo=CENTRAL_TZ).astimezone(timezone.utc)
        key = cog._scratch_reset_key(moment)
        assert key == "2026-05-08"

    def test_noon_central_returns_same_day_key(self, cog):
        moment = datetime(2026, 5, 8, 12, 0, tzinfo=CENTRAL_TZ).astimezone(timezone.utc)
        key = cog._scratch_reset_key(moment)
        assert key == "2026-05-08"

    def test_just_before_midnight_returns_same_day_key(self, cog):
        # 23:59 Central on 5/8 -> 2026-05-08 (still same logical day).
        moment = datetime(2026, 5, 8, 23, 59, tzinfo=CENTRAL_TZ).astimezone(timezone.utc)
        key = cog._scratch_reset_key(moment)
        assert key == "2026-05-08"

    def test_midnight_central_returns_previous_day_key(self, cog):
        # 00:00 Central on 5/9 -> still part of 5/8's logical "day"
        # because the cutover is 5am, not midnight.
        moment = datetime(2026, 5, 9, 0, 0, tzinfo=CENTRAL_TZ).astimezone(timezone.utc)
        key = cog._scratch_reset_key(moment)
        assert key == "2026-05-08"

    def test_utc_input_works(self, cog):
        """The function takes UTC; make sure timezone conversion is correct."""
        # 10:00 UTC on 2026-01-15 = 04:00 CST (CT is UTC-6 in winter)
        # -> still previous day under 5am rule = 2026-01-14
        moment = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        key = cog._scratch_reset_key(moment)
        assert key == "2026-01-14"

    def test_dst_spring_forward(self, cog):
        """DST 2026: spring forward March 8 02:00 CST -> 03:00 CDT."""
        # 09:00 UTC on 2026-03-08 = 04:00 CDT (after DST jump)
        moment = datetime(2026, 3, 8, 9, 0, tzinfo=timezone.utc)
        key = cog._scratch_reset_key(moment)
        # 04:00 CDT is before 5am -> previous day = 2026-03-07
        assert key == "2026-03-07"

        # 11:00 UTC = 06:00 CDT -> same day = 2026-03-08
        moment2 = datetime(2026, 3, 8, 11, 0, tzinfo=timezone.utc)
        key2 = cog._scratch_reset_key(moment2)
        assert key2 == "2026-03-08"
