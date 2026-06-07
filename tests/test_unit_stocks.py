"""Unit tests for the real-US-stocks module.

The yfinance integration is mocked: tests monkeypatch `_fetch_quotes` and
`_validate_symbol` so they never hit the network.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
import pytest

import shared
from modules import stocks
from modules.stocks import (
    CENTRAL_TZ,
    EASTERN_TZ,
    SEED_TICKERS,
    SPARK_BLOCKS,
    StocksCog,
    _fetch_quotes,
    _fmt_shares,
    _validate_symbol,
    add_ticker,
    build_market_lines,
    buy_shares,
    execute_buy,
    execute_sell,
    get_all_prices,
    get_open_options,
    get_portfolio_value,
    get_price,
    get_price_history,
    get_realized_pl,
    get_tickers,
    get_user_holding,
    get_user_holdings,
    init_tickers,
    is_market_open,
    make_sparkline,
    open_option,
    refresh_prices,
    sell_shares,
)
from tests.conftest import FakeAuthor, FakeContext, FakeGuild


@pytest.fixture(autouse=True)
def _seed_tickers():
    # Always start with a clean in-memory history cache and the curated seed list.
    stocks._history_cache.clear()
    init_tickers()
    yield


def _seed_price(ticker: str, price: float, prev_close: float | None = None) -> None:
    refresh_prices(
        {
            ticker: {
                "price": price,
                "prev_close": prev_close if prev_close is not None else price,
                "history": [price],
            }
        }
    )


class TestTickerRegistry:
    def test_seed_populates_curated_list(self):
        tickers = get_tickers()
        assert set(SEED_TICKERS).issubset(tickers.keys())
        assert tickers["AAPL"] == "Apple Inc."

    def test_init_is_idempotent(self):
        before = get_tickers()
        init_tickers()
        assert get_tickers() == before

    def test_user_added_ticker_persists(self):
        add_ticker("BRK-B", "Berkshire Hathaway Class B", added_by=42)
        assert "BRK-B" in get_tickers()
        # adding the same ticker twice is a no-op
        add_ticker("BRK-B", "renamed", added_by=43)
        assert get_tickers()["BRK-B"] == "Berkshire Hathaway Class B"


class TestPriceStore:
    def test_refresh_prices_writes_db_and_cache(self):
        refresh_prices(
            {
                "AAPL": {
                    "price": 170.50,
                    "prev_close": 168.00,
                    "history": [165.0, 166.0, 167.0, 168.0, 170.50],
                }
            }
        )
        assert get_price("AAPL") == pytest.approx(170.50)
        assert get_all_prices()["AAPL"]["prev_close"] == pytest.approx(168.00)
        assert get_price_history("AAPL") == [165.0, 166.0, 167.0, 168.0, 170.50]

    def test_refresh_with_empty_quote_set_is_noop(self):
        refresh_prices({})
        assert get_all_prices() == {}

    def test_subsequent_refresh_overwrites(self):
        _seed_price("AAPL", 100.0, 99.0)
        _seed_price("AAPL", 110.0, 100.0)
        assert get_price("AAPL") == pytest.approx(110.0)
        assert get_all_prices()["AAPL"]["prev_close"] == pytest.approx(100.0)


class TestMarketHours:
    def test_weekday_during_session_is_open(self):
        # Tuesday 2026-05-12 11:00 ET → open
        moment = datetime(2026, 5, 12, 11, 0, tzinfo=EASTERN_TZ)
        assert is_market_open(moment.astimezone(ZoneInfo("UTC"))) is True

    def test_weekday_pre_open_is_closed(self):
        moment = datetime(2026, 5, 12, 9, 0, tzinfo=EASTERN_TZ)  # 9:00 < 9:30
        assert is_market_open(moment.astimezone(ZoneInfo("UTC"))) is False

    def test_weekday_post_close_is_closed(self):
        moment = datetime(2026, 5, 12, 16, 0, tzinfo=EASTERN_TZ)
        assert is_market_open(moment.astimezone(ZoneInfo("UTC"))) is False

    def test_saturday_is_closed_even_during_session_hours(self):
        moment = datetime(2026, 5, 16, 11, 0, tzinfo=EASTERN_TZ)  # Sat
        assert is_market_open(moment.astimezone(ZoneInfo("UTC"))) is False


class TestFractionalHoldings:
    USER_ID = 4242

    def test_fractional_buy_creates_holding(self):
        buy_shares(self.USER_ID, "AAPL", 0.5, 100.00)
        holding = get_user_holding(self.USER_ID, "AAPL")
        assert holding == {"shares": pytest.approx(0.5), "avg_cost": pytest.approx(100.00)}

    def test_blend_avg_cost_with_fractional(self):
        buy_shares(self.USER_ID, "AAPL", 0.5, 100.00)
        buy_shares(self.USER_ID, "AAPL", 1.5, 200.00)
        holding = get_user_holding(self.USER_ID, "AAPL")
        # (0.5*100 + 1.5*200) / 2.0 = (50 + 300) / 2 = 175
        assert holding["shares"] == pytest.approx(2.0)
        assert holding["avg_cost"] == pytest.approx(175.00)

    def test_partial_sell_keeps_remaining_fraction(self):
        buy_shares(self.USER_ID, "AAPL", 1.0, 100.00)
        ok = sell_shares(self.USER_ID, "AAPL", 0.3, 120.00)
        assert ok is True
        holding = get_user_holding(self.USER_ID, "AAPL")
        assert holding["shares"] == pytest.approx(0.7)

    def test_sell_full_position_removes_holding(self):
        buy_shares(self.USER_ID, "AAPL", 0.25, 100.00)
        sell_shares(self.USER_ID, "AAPL", 0.25, 120.00)
        assert get_user_holding(self.USER_ID, "AAPL") is None
        assert get_user_holdings(self.USER_ID) == []

    def test_sell_more_than_owned_fails(self):
        buy_shares(self.USER_ID, "AAPL", 0.5, 100.00)
        ok = sell_shares(self.USER_ID, "AAPL", 1.0, 120.00)
        assert ok is False


class TestRealizedPL:
    USER_ID = 7777

    def test_realized_pl_on_fractional_sell(self):
        buy_shares(self.USER_ID, "AAPL", 2.0, 100.00)
        sell_shares(self.USER_ID, "AAPL", 0.5, 150.00)
        # 0.5 * (150 - 100) = 25
        assert get_realized_pl(self.USER_ID) == pytest.approx(25.00)
        assert get_realized_pl(self.USER_ID, "AAPL") == pytest.approx(25.00)


class TestPortfolioValue:
    USER_ID = 8888

    def test_empty(self):
        assert get_portfolio_value(self.USER_ID) == (0.0, 0.0)

    def test_uses_current_prices_with_fractional_shares(self):
        _seed_price("AAPL", 200.00)
        buy_shares(self.USER_ID, "AAPL", 0.5, 100.00)
        value, cost = get_portfolio_value(self.USER_ID)
        assert value == pytest.approx(100.00)  # 0.5 * 200
        assert cost == pytest.approx(50.00)  # 0.5 * 100


class TestSparkline:
    def test_empty_returns_empty(self):
        assert make_sparkline([]) == ""

    def test_single_value_returns_one_block(self):
        result = make_sparkline([5.0])
        assert len(result) == 1
        assert result in SPARK_BLOCKS

    def test_min_and_max_anchor_to_block_edges(self):
        result = make_sparkline([1.0, 2.0, 3.0, 4.0, 5.0])
        assert result[0] == SPARK_BLOCKS[0]
        assert result[-1] == SPARK_BLOCKS[-1]


class TestQuantityResolver:
    def test_plain_integer(self):
        qty, err = StocksCog._resolve_quantity("5", price=10.0)
        assert qty == pytest.approx(5.0)
        assert err is None

    def test_fractional_quantity(self):
        qty, err = StocksCog._resolve_quantity("0.25", price=10.0)
        assert qty == pytest.approx(0.25)
        assert err is None

    def test_all_requires_position(self):
        qty, err = StocksCog._resolve_quantity("all", price=10.0, max_shares=7.5)
        assert qty == pytest.approx(7.5)
        assert err is None

    def test_all_without_position_errors(self):
        qty, err = StocksCog._resolve_quantity("all", price=10.0, max_shares=0)
        assert qty is None
        assert "no position" in err.lower()

    def test_dollar_amount_returns_fractional_shares(self):
        qty, err = StocksCog._resolve_quantity("$500", price=200.00)
        # 500/200 = 2.5 shares
        assert qty == pytest.approx(2.5)
        assert err is None

    def test_dollar_amount_too_small_errors(self):
        # 1 coin / 1_000_000 price → far below MIN_SHARES
        qty, err = StocksCog._resolve_quantity("$1", price=1_000_000.00)
        assert qty is None
        assert err

    def test_below_min_shares_errors(self):
        qty, err = StocksCog._resolve_quantity("0.00001", price=10.0)
        assert qty is None
        assert err

    def test_garbage_errors(self):
        qty, err = StocksCog._resolve_quantity("banana", price=10.0)
        assert qty is None
        assert err


class TestExecuteBuy:
    USER_ID = 9000

    def test_debits_balance_and_adds_fractional_holding(self):
        shared.db.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (self.USER_ID, 1000))
        shared.db.commit()
        # 0.5 sh of a $200 stock → 100 coins
        new_bal = execute_buy(self.USER_ID, "AAPL", 0.5, 200.00, cost=100)
        assert new_bal == 900
        holding = get_user_holding(self.USER_ID, "AAPL")
        assert holding == {"shares": pytest.approx(0.5), "avg_cost": pytest.approx(200.00)}


class TestExecuteSell:
    USER_ID = 9100

    def _seed_position(self, shares: float, avg_cost: float, balance: int = 0):
        shared.db.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (self.USER_ID, balance))
        shared.db.execute(
            "INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) VALUES (?, ?, ?, ?)",
            (self.USER_ID, "AAPL", shares, avg_cost),
        )
        shared.db.commit()

    def test_credits_proceeds_and_returns_realized_pl(self):
        self._seed_position(shares=2.0, avg_cost=100.00, balance=0)
        result = execute_sell(self.USER_ID, "AAPL", 0.5, price=200.00, proceeds=100)
        assert result is not None
        new_bal, realized = result
        assert new_bal == 100
        # realized = 100 (proceeds) - 100 * 0.5 = 50
        assert realized == pytest.approx(50.00)

    def test_returns_none_when_position_too_small(self):
        self._seed_position(shares=0.1, avg_cost=100.00, balance=0)
        assert execute_sell(self.USER_ID, "AAPL", 0.5, price=200.00, proceeds=100) is None
        assert get_user_holding(self.USER_ID, "AAPL")["shares"] == pytest.approx(0.1)

    def test_full_sell_removes_position(self):
        self._seed_position(shares=0.5, avg_cost=100.00, balance=0)
        execute_sell(self.USER_ID, "AAPL", 0.5, price=200.00, proceeds=100)
        assert get_user_holding(self.USER_ID, "AAPL") is None


class _FailingProxy:
    """Wraps a sqlite3 Connection and raises once on execute() of a matching SQL."""

    def __init__(self, real, fail_substring: str):
        self._real = real
        self._fail = fail_substring
        self._fired = False

    def execute(self, sql, params=()):
        if self._fail in sql and not self._fired:
            self._fired = True
            raise RuntimeError("simulated mid-transaction failure")
        return self._real.execute(sql, params)

    def __enter__(self):
        return self._real.__enter__()

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)

    def commit(self):
        return self._real.commit()

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestAtomicity:
    def test_execute_buy_rolls_back_on_exception(self, monkeypatch):
        user_id = 9200
        shared.db.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, 1000))
        shared.db.commit()
        real_db = shared.db
        proxy = _FailingProxy(real_db, "INSERT INTO stock_trades")
        monkeypatch.setattr(shared, "db", proxy)
        with pytest.raises(RuntimeError):
            execute_buy(user_id, "AAPL", 0.5, 200.00, cost=100)
        monkeypatch.setattr(shared, "db", real_db)
        assert shared.get_balance(user_id) == 1000
        assert get_user_holding(user_id, "AAPL") is None

    def test_execute_sell_rolls_back_on_exception(self, monkeypatch):
        user_id = 9201
        shared.db.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, 100))
        shared.db.execute(
            "INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) VALUES (?, ?, ?, ?)",
            (user_id, "AAPL", 1.0, 100.00),
        )
        shared.db.commit()
        real_db = shared.db
        proxy = _FailingProxy(real_db, "INSERT INTO stock_trades")
        monkeypatch.setattr(shared, "db", proxy)
        with pytest.raises(RuntimeError):
            execute_sell(user_id, "AAPL", 0.5, price=200.00, proceeds=100)
        monkeypatch.setattr(shared, "db", real_db)
        assert get_user_holding(user_id, "AAPL")["shares"] == pytest.approx(1.0)
        assert shared.get_balance(user_id) == 100


class TestLegacyMigration:
    """The shared.init_db migration block refunds and removes positions in
    the old simulated tickers (GARY/SILS/etc.). Verify here by simulating
    the legacy state and re-running the migration logic."""

    def test_legacy_holdings_are_refunded_at_avg_cost_and_dropped(self):
        # Simulate a pre-revamp DB: a legacy ticker with a price that diverged
        # from the user's cost basis. Refund must be at avg_cost (what the
        # user paid), NOT at the simulated last price.
        shared.db.execute(
            "INSERT INTO stock_prices (ticker, price, prev_close, last_updated) "
            "VALUES ('GARY', 42.00, 42.00, '2026-05-15T12:00:00+00:00')"
        )
        shared.db.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (5555, 500))
        shared.db.execute("INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) VALUES (5555, 'GARY', 10, 40.00)")
        shared.db.commit()
        shared.init_db()
        # Refund = 10 * 40 (avg_cost) = 400 → 500 + 400 = 900
        assert get_user_holding(5555, "GARY") is None
        assert shared.get_balance(5555) == 900
        assert shared.db.execute("SELECT 1 FROM stock_prices WHERE ticker = 'GARY'").fetchone() is None
        # And the refund was logged to balance_history for auditability.
        last = shared.db.execute("SELECT balance FROM balance_history WHERE user_id = 5555 ORDER BY id DESC LIMIT 1").fetchone()
        assert last is not None and last[0] == 900

    def test_legacy_migration_creates_user_row_if_missing(self):
        # A holder that has no row in `users` yet still gets refunded.
        shared.db.execute(
            "INSERT INTO stock_prices (ticker, price, prev_close, last_updated) "
            "VALUES ('DOGE', 1.50, 1.50, '2026-05-15T12:00:00+00:00')"
        )
        shared.db.execute("INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) VALUES (6666, 'DOGE', 100, 2.00)")
        shared.db.commit()
        shared.init_db()
        # No prior balance row → refund of 200 creates the row at 200.
        assert shared.get_balance(6666) == 200

    def test_legacy_migration_is_idempotent(self):
        # Second run after the migration completes must not double-refund.
        shared.db.execute(
            "INSERT INTO stock_prices (ticker, price, prev_close, last_updated) "
            "VALUES ('GARY', 42.00, 42.00, '2026-05-15T12:00:00+00:00')"
        )
        shared.db.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (7777, 100))
        shared.db.execute("INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) VALUES (7777, 'GARY', 5, 10.00)")
        shared.db.commit()
        shared.init_db()
        assert shared.get_balance(7777) == 150  # 100 + 5*10
        shared.init_db()  # second run = no-op
        assert shared.get_balance(7777) == 150


# ---------------------------------------------------------------------------
# Crypto alias remapping
# ---------------------------------------------------------------------------

from modules.stocks import CRYPTO_ALIASES  # noqa: E402


class TestCryptoAliases:
    def test_btc_maps_to_btc_usd(self):
        assert CRYPTO_ALIASES["BTC"] == "BTC-USD"

    def test_eth_maps_to_eth_usd(self):
        assert CRYPTO_ALIASES["ETH"] == "ETH-USD"

    def test_sol_maps_to_sol_usd(self):
        assert CRYPTO_ALIASES["SOL"] == "SOL-USD"

    def test_stock_tickers_not_in_aliases(self):
        assert "AAPL" not in CRYPTO_ALIASES
        assert "MSFT" not in CRYPTO_ALIASES
        assert "NVDA" not in CRYPTO_ALIASES

    def test_all_alias_values_end_with_usd(self):
        for short, yf_sym in CRYPTO_ALIASES.items():
            assert yf_sym.endswith("-USD"), f"{short} → {yf_sym} should end with -USD"


# ---------------------------------------------------------------------------
# Options helpers — open / settle
# ---------------------------------------------------------------------------

from modules.stocks import open_option, get_open_options, settle_option, OPTIONS_LEVERAGE  # noqa: E402


def _set_balance(user_id: int, amount: int) -> None:
    shared.db.execute(
        "INSERT INTO users (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = ?",
        (user_id, amount, amount),
    )
    shared.db.commit()


def _get_balance(user_id: int) -> int:
    row = shared.db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return int(row[0]) if row else 0


class TestOpenOption:
    def test_deducts_coins_from_balance(self):
        _set_balance(20001, 500)
        open_option(20001, "AAPL", "call", 200, 150.0)
        assert _get_balance(20001) == 300

    def test_stores_open_option_row(self):
        _set_balance(20002, 1000)
        open_option(20002, "MSFT", "put", 100, 400.0)
        opts = get_open_options(20002)
        assert len(opts) == 1
        o = opts[0]
        assert o["ticker"] == "MSFT"
        assert o["option_type"] == "put"
        assert o["coins_bet"] == 100
        assert abs(o["strike_price"] - 400.0) < 0.01

    def test_returns_positive_option_id(self):
        _set_balance(20003, 500)
        opt_id = open_option(20003, "TSLA", "call", 50, 200.0)
        assert isinstance(opt_id, int) and opt_id > 0

    def test_multiple_options_tracked_separately(self):
        _set_balance(20004, 1000)
        open_option(20004, "NVDA", "call", 200, 800.0)
        open_option(20004, "AMD", "put", 100, 100.0)
        opts = get_open_options(20004)
        assert len(opts) == 2
        assert {o["ticker"] for o in opts} == {"NVDA", "AMD"}


class TestGetOpenOptions:
    def test_returns_only_unsettled(self):
        _set_balance(20010, 500)
        opt_id = open_option(20010, "AAPL", "call", 100, 150.0)
        settle_option(opt_id, 20010, 100, 150.0, 155.0, "call")
        assert get_open_options(20010) == []

    def test_user_filter(self):
        _set_balance(20011, 500)
        _set_balance(20012, 500)
        open_option(20011, "MSFT", "call", 100, 400.0)
        open_option(20012, "MSFT", "put", 100, 400.0)
        assert len(get_open_options(20011)) == 1
        assert len(get_open_options(20012)) == 1

    def test_no_filter_returns_all_users(self):
        _set_balance(20013, 500)
        _set_balance(20014, 500)
        open_option(20013, "SPY", "call", 100, 500.0)
        open_option(20014, "SPY", "put", 100, 500.0)
        # May include rows from other tests in the same session — just assert ≥ 2.
        assert len(get_open_options()) >= 2


class TestSettleOption:
    def test_call_wins_when_price_rises(self):
        _set_balance(20020, 500)
        opt_id = open_option(20020, "AAPL", "call", 200, 100.0)
        pnl, payout = settle_option(opt_id, 20020, 200, 100.0, 110.0, "call")
        assert pnl > 0
        assert payout > 200

    def test_call_loses_when_price_falls(self):
        _set_balance(20021, 500)
        opt_id = open_option(20021, "AAPL", "call", 200, 100.0)
        pnl, payout = settle_option(opt_id, 20021, 200, 100.0, 90.0, "call")
        assert pnl == -200
        assert payout == 0

    def test_put_wins_when_price_falls(self):
        _set_balance(20022, 500)
        opt_id = open_option(20022, "TSLA", "put", 150, 200.0)
        pnl, payout = settle_option(opt_id, 20022, 150, 200.0, 180.0, "put")
        assert pnl > 0
        assert payout > 150

    def test_put_loses_when_price_rises(self):
        _set_balance(20023, 500)
        opt_id = open_option(20023, "TSLA", "put", 150, 200.0)
        pnl, payout = settle_option(opt_id, 20023, 150, 200.0, 220.0, "put")
        assert pnl == -150
        assert payout == 0

    def test_minimum_win_is_1_1x(self):
        # Tiny move still guarantees at least 1.1× on a win.
        _set_balance(20024, 500)
        opt_id = open_option(20024, "SPY", "call", 100, 500.0)
        pnl, payout = settle_option(opt_id, 20024, 100, 500.0, 500.005, "call")
        assert payout >= 110

    def test_10pct_move_gives_2x_payout(self):
        # 10% move × OPTIONS_LEVERAGE(10) → multiplier = 2.0
        _set_balance(20025, 1000)
        opt_id = open_option(20025, "BTC-USD", "call", 100, 50000.0)
        pnl, payout = settle_option(opt_id, 20025, 100, 50000.0, 55000.0, "call")
        expected = int(round(100 * (1.0 + 0.10 * OPTIONS_LEVERAGE)))
        assert abs(payout - expected) <= 1

    def test_win_credits_balance(self):
        _set_balance(20026, 1000)
        opt_id = open_option(20026, "MSFT", "call", 200, 400.0)
        bal_after_open = _get_balance(20026)
        _, payout = settle_option(opt_id, 20026, 200, 400.0, 440.0, "call")
        assert _get_balance(20026) == bal_after_open + payout

    def test_loss_does_not_change_balance(self):
        _set_balance(20027, 1000)
        opt_id = open_option(20027, "NVDA", "call", 300, 800.0)
        bal_after_open = _get_balance(20027)
        _, payout = settle_option(opt_id, 20027, 300, 800.0, 750.0, "call")
        assert payout == 0
        assert _get_balance(20027) == bal_after_open

    @pytest.mark.parametrize("opt_type", ["call", "put"])
    def test_flat_price_loses(self, opt_type):
        _set_balance(20028, 500)
        opt_id = open_option(20028, "AAPL", opt_type, 100, 150.0)
        pnl, _ = settle_option(opt_id, 20028, 100, 150.0, 150.0, opt_type)
        assert pnl == -100

    def test_settled_option_disappears_from_open_list(self):
        _set_balance(20029, 500)
        opt_id = open_option(20029, "GOOGL", "call", 100, 170.0)
        settle_option(opt_id, 20029, 100, 170.0, 180.0, "call")
        assert get_open_options(20029) == []

    def test_double_settle_is_idempotent(self):
        # Second call on an already-settled option must not double-credit the balance.
        _set_balance(20030, 1000)
        opt_id = open_option(20030, "AAPL", "call", 200, 100.0)
        bal_after_open = _get_balance(20030)
        pnl1, payout1 = settle_option(opt_id, 20030, 200, 100.0, 120.0, "call")
        bal_after_first = _get_balance(20030)
        pnl2, payout2 = settle_option(opt_id, 20030, 200, 100.0, 120.0, "call")
        assert (pnl2, payout2) == (0, 0)
        assert _get_balance(20030) == bal_after_first  # no second credit


class TestShortOptions:
    def test_short_call_wins_when_price_falls(self):
        _set_balance(20040, 500)
        opt_id = open_option(20040, "AAPL", "call", 200, 100.0, side="short")
        pnl, payout = settle_option(opt_id, 20040, 200, 100.0, 90.0, "call", side="short")
        assert pnl > 0
        assert payout > 200

    def test_short_call_loses_when_price_rises(self):
        _set_balance(20041, 500)
        opt_id = open_option(20041, "AAPL", "call", 200, 100.0, side="short")
        pnl, payout = settle_option(opt_id, 20041, 200, 100.0, 110.0, "call", side="short")
        assert pnl == -200
        assert payout == 0

    def test_short_put_wins_when_price_rises(self):
        _set_balance(20042, 500)
        opt_id = open_option(20042, "TSLA", "put", 150, 200.0, side="short")
        pnl, payout = settle_option(opt_id, 20042, 150, 200.0, 220.0, "put", side="short")
        assert pnl > 0
        assert payout > 150

    def test_short_put_loses_when_price_falls(self):
        _set_balance(20043, 500)
        opt_id = open_option(20043, "TSLA", "put", 150, 200.0, side="short")
        pnl, payout = settle_option(opt_id, 20043, 150, 200.0, 180.0, "put", side="short")
        assert pnl == -150
        assert payout == 0

    def test_short_deducts_coins_at_open(self):
        _set_balance(20044, 500)
        open_option(20044, "MSFT", "call", 200, 400.0, side="short")
        assert _get_balance(20044) == 300

    def test_short_stored_with_correct_side(self):
        _set_balance(20045, 500)
        open_option(20045, "NVDA", "put", 100, 800.0, side="short")
        opts = get_open_options(20045)
        assert len(opts) == 1
        assert opts[0]["side"] == "short"

    def test_long_default_side_is_long(self):
        _set_balance(20046, 500)
        open_option(20046, "SPY", "call", 100, 500.0)
        opts = get_open_options(20046)
        assert opts[0]["side"] == "long"

    def test_short_flat_price_loses(self):
        _set_balance(20047, 500)
        opt_id = open_option(20047, "AAPL", "call", 100, 150.0, side="short")
        pnl, _ = settle_option(opt_id, 20047, 100, 150.0, 150.0, "call", side="short")
        assert pnl == -100

    def test_short_10pct_move_gives_2x_payout(self):
        _set_balance(20048, 1000)
        opt_id = open_option(20048, "BTC-USD", "call", 100, 50000.0, side="short")
        # Price falls 10% below strike → short call wins
        pnl, payout = settle_option(opt_id, 20048, 100, 50000.0, 45000.0, "call", side="short")
        expected = int(round(100 * (1.0 + 0.10 * OPTIONS_LEVERAGE)))
        assert abs(payout - expected) <= 1


# ---------------------------------------------------------------------------
# _stock_remove — ticker deletion + option voiding
# ---------------------------------------------------------------------------

from modules.stocks import add_ticker, get_tickers  # noqa: E402 (already imported above)


class TestStockRemove:
    """DB-level tests for the _stock_remove admin command."""

    def _make_cog(self):
        import unittest.mock as mock

        bot = mock.MagicMock()
        bot.loop = asyncio.get_event_loop()
        return StocksCog(bot)

    @pytest.mark.asyncio
    async def test_removes_ticker_from_registry(self):
        add_ticker("ZZZ", "ZZZ Corp")
        from tests.conftest import FakeContext, FakeAuthor, FakeGuild

        ctx = FakeContext(
            author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
            guild=FakeGuild(),
        )
        cog = self._make_cog()
        await cog._stock_remove(ctx, "ZZZ")
        assert "ZZZ" not in get_tickers()

    @pytest.mark.asyncio
    async def test_removes_price_history(self):
        add_ticker("ZZA", "ZZA Corp")
        _seed_price("ZZA", 50.0)
        from tests.conftest import FakeContext, FakeAuthor, FakeGuild

        ctx = FakeContext(
            author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
            guild=FakeGuild(),
        )
        cog = self._make_cog()
        await cog._stock_remove(ctx, "ZZA")
        assert get_all_prices().get("ZZA") is None

    @pytest.mark.asyncio
    async def test_refunds_open_options_on_removal(self):
        add_ticker("ZZB", "ZZB Corp")
        _set_balance(30001, 1000)
        opt_id = open_option(30001, "ZZB", "call", 300, 50.0)
        bal_after_open = _get_balance(30001)

        from tests.conftest import FakeContext, FakeAuthor, FakeGuild

        ctx = FakeContext(
            author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
            guild=FakeGuild(),
        )
        cog = self._make_cog()
        await cog._stock_remove(ctx, "ZZB")

        # Coins must be returned.
        assert _get_balance(30001) == bal_after_open + 300
        # Option must be marked settled (voided).
        assert get_open_options(30001) == []

    @pytest.mark.asyncio
    async def test_removal_mentions_voided_options_in_message(self):
        add_ticker("ZZC", "ZZC Corp")
        _set_balance(30002, 500)
        open_option(30002, "ZZC", "put", 100, 80.0)

        from tests.conftest import FakeContext, FakeAuthor, FakeGuild

        ctx = FakeContext(
            author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
            guild=FakeGuild(),
        )
        cog = self._make_cog()
        await cog._stock_remove(ctx, "ZZC")

        last = ctx.last_send
        assert last is not None
        desc = last["embed"].description
        assert "voided" in desc.lower() or "refunded" in desc.lower()

    @pytest.mark.asyncio
    async def test_non_admin_is_rejected(self):
        add_ticker("ZZD", "ZZD Corp")
        from tests.conftest import FakeContext, FakeAuthor, FakeGuild

        ctx = FakeContext(
            author=FakeAuthor(user_id=99999, name="Rando"),
            guild=FakeGuild(),
        )
        cog = self._make_cog()
        await cog._stock_remove(ctx, "ZZD")
        # Ticker must still be listed.
        assert "ZZD" in get_tickers()
        last = ctx.last_send
        assert last is not None
        assert "permission" in last["embed"].title.lower() or "denied" in last["embed"].title.lower()

    @pytest.mark.asyncio
    async def test_removing_unlisted_ticker_sends_error(self):
        ctx = FakeContext(
            author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
            guild=FakeGuild(),
        )
        cog = self._make_cog()
        await cog._stock_remove(ctx, "DOESNOTEXIST")
        last = ctx.last_send
        assert last is not None
        assert last["embed"] is not None  # error embed sent


# ---------------------------------------------------------------------------
# Stocks command handler tests
# ---------------------------------------------------------------------------


def _make_stocks_cog():
    bot = MagicMock()
    bot.loop = asyncio.get_event_loop()
    return StocksCog(bot)


class TestStocksListCommand:
    async def test_sends_embed(self):
        _seed_price("AAPL", 150.0, 148.0)
        ctx = FakeContext()
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx)
        assert ctx.sent
        assert ctx.sent[0]["embed"] is not None

    async def test_shows_all_seed_tickers(self):
        for t in list(SEED_TICKERS)[:3]:
            _seed_price(t, 100.0)
        ctx = FakeContext()
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx)
        embed = ctx.sent[0]["embed"]
        desc = embed.description or ""
        for t in list(SEED_TICKERS)[:3]:
            assert t in desc


class TestStocksDetailCommand:
    async def test_detail_for_known_ticker(self):
        _seed_price("MSFT", 300.0, 295.0)
        ctx = FakeContext(author=FakeAuthor(user_id=50001))
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx, "MSFT")
        embed = ctx.sent[0]["embed"]
        assert "MSFT" in embed.title

    async def test_detail_for_unknown_ticker_sends_error(self):
        ctx = FakeContext()
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx, "XXXXNOTREAL")
        embed = ctx.sent[0]["embed"]
        assert "Unknown" in embed.title or "isn't listed" in (embed.description or "")


class TestStocksAddCommand:
    async def test_add_already_listed_rejected(self):
        ctx = FakeContext()
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx, "add", "AAPL")
        embed = ctx.sent[0]["embed"]
        assert "Already" in embed.title

    async def test_add_invalid_symbol_rejected(self, monkeypatch):
        monkeypatch.setattr(stocks, "_validate_symbol", lambda t: (False, None, None))
        ctx = FakeContext()
        ctx.typing = MagicMock(return_value=_async_cm())
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx, "add", "ZZZNOPE")
        embed = ctx.sent[0]["embed"]
        assert "Unknown" in embed.title

    async def test_add_valid_symbol_registers(self, monkeypatch):
        monkeypatch.setattr(stocks, "_validate_symbol", lambda t: (True, "Test Corp", 42.0))
        monkeypatch.setattr(stocks, "_fetch_quotes", lambda tickers: {})
        ctx = FakeContext()
        ctx.typing = MagicMock(return_value=_async_cm())
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx, "add", "TESTNEW")
        assert "TESTNEW" in get_tickers()
        embed = ctx.sent[0]["embed"]
        assert "Added" in embed.title


def _async_cm():
    """Minimal async context manager for ctx.typing()."""

    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    return _CM()


class TestBuyCommand:
    async def test_missing_args_sends_usage(self):
        ctx = FakeContext(author=FakeAuthor(user_id=60001))
        cog = _make_stocks_cog()
        await cog.buy.callback(cog, ctx, None, None)
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_unknown_ticker_rejected(self):
        ctx = FakeContext(author=FakeAuthor(user_id=60002))
        cog = _make_stocks_cog()
        await cog.buy.callback(cog, ctx, "XXXXFAKE", "1")
        embed = ctx.sent[0]["embed"]
        assert "Unknown" in embed.title

    async def test_buy_with_sufficient_balance(self):
        _set_balance(60003, 10_000)
        _seed_price("AAPL", 100.0)
        ctx = FakeContext(author=FakeAuthor(user_id=60003))
        cog = _make_stocks_cog()
        await cog.buy.callback(cog, ctx, "AAPL", "5")
        embed = ctx.sent[0]["embed"]
        assert "Buy" in embed.title
        holding = get_user_holding(60003, "AAPL")
        assert holding is not None
        assert abs(holding["shares"] - 5.0) < 1e-6

    async def test_buy_broke_rejected(self):
        _set_balance(60004, 0)
        _seed_price("AAPL", 100.0)
        ctx = FakeContext(author=FakeAuthor(user_id=60004))
        cog = _make_stocks_cog()
        await cog.buy.callback(cog, ctx, "AAPL", "10")
        embed = ctx.sent[0]["embed"]
        assert "Broke" in embed.title or "Invalid" in embed.title

    async def test_buy_all_spends_full_balance(self):
        _set_balance(60005, 500)
        _seed_price("NVDA", 100.0)
        ctx = FakeContext(author=FakeAuthor(user_id=60005))
        cog = _make_stocks_cog()
        await cog.buy.callback(cog, ctx, "NVDA", "all")
        holding = get_user_holding(60005, "NVDA")
        assert holding is not None and holding["shares"] > 0


class TestSellCommand:
    async def test_missing_args_sends_usage(self):
        ctx = FakeContext(author=FakeAuthor(user_id=61001))
        cog = _make_stocks_cog()
        await cog.sell.callback(cog, ctx, None, None)
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_no_position_rejected(self):
        ctx = FakeContext(author=FakeAuthor(user_id=61002))
        cog = _make_stocks_cog()
        await cog.sell.callback(cog, ctx, "AAPL", "1")
        embed = ctx.sent[0]["embed"]
        assert "No Position" in embed.title or "Unknown" in embed.title

    async def test_sell_all_clears_position(self):
        _set_balance(61003, 10_000)
        _seed_price("GOOGL", 200.0)
        buy_shares(61003, "GOOGL", 3.0, 200.0)
        ctx = FakeContext(author=FakeAuthor(user_id=61003))
        cog = _make_stocks_cog()
        await cog.sell.callback(cog, ctx, "GOOGL", "all")
        embed = ctx.sent[0]["embed"]
        assert "Sell" in embed.title
        holding = get_user_holding(61003, "GOOGL")
        assert holding is None or holding["shares"] < 1e-6

    async def test_sell_partial(self):
        _set_balance(61004, 10_000)
        _seed_price("TSLA", 150.0)
        buy_shares(61004, "TSLA", 4.0, 150.0)
        ctx = FakeContext(author=FakeAuthor(user_id=61004))
        cog = _make_stocks_cog()
        await cog.sell.callback(cog, ctx, "TSLA", "2")
        holding = get_user_holding(61004, "TSLA")
        assert holding is not None
        assert abs(holding["shares"] - 2.0) < 1e-6


class TestPortfolioCommand:
    async def test_no_holdings_sends_text(self):
        ctx = FakeContext(author=FakeAuthor(user_id=62001))
        cog = _make_stocks_cog()
        await cog.portfolio.callback(cog, ctx)
        assert ctx.sent

    async def test_with_holdings_sends_embed(self):
        _set_balance(62002, 5000)
        _seed_price("AMZN", 180.0)
        buy_shares(62002, "AMZN", 2.0, 180.0)
        ctx = FakeContext(author=FakeAuthor(user_id=62002))
        cog = _make_stocks_cog()
        await cog.portfolio.callback(cog, ctx)
        embed = ctx.sent[0]["embed"]
        assert "AMZN" in (embed.description or "")


class TestCallPutCommands:
    async def test_call_missing_args_sends_usage(self):
        ctx = FakeContext(author=FakeAuthor(user_id=63001))
        cog = _make_stocks_cog()
        await cog.call_option.callback(cog, ctx, None, None)
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_put_missing_args_sends_usage(self):
        ctx = FakeContext(author=FakeAuthor(user_id=63002))
        cog = _make_stocks_cog()
        await cog.put_option.callback(cog, ctx, None, None)
        assert "Usage" in (ctx.sent[0].get("content") or "")

    async def test_call_unknown_ticker_rejected(self):
        ctx = FakeContext(author=FakeAuthor(user_id=63003))
        cog = _make_stocks_cog()
        await cog.call_option.callback(cog, ctx, "XXXXFAKE", 100)
        embed = ctx.sent[0]["embed"]
        assert "Unknown" in embed.title

    async def test_call_broke_rejected(self):
        _set_balance(63004, 50)
        _seed_price("AAPL", 100.0)
        ctx = FakeContext(author=FakeAuthor(user_id=63004))
        cog = _make_stocks_cog()
        await cog.call_option.callback(cog, ctx, "AAPL", 500)
        embed = ctx.sent[0]["embed"]
        assert "Broke" in embed.title

    async def test_call_opens_option(self):
        _set_balance(63005, 1000)
        _seed_price("AAPL", 100.0)
        ctx = FakeContext(author=FakeAuthor(user_id=63005))
        cog = _make_stocks_cog()
        await cog.call_option.callback(cog, ctx, "AAPL", 200)
        embed = ctx.sent[0]["embed"]
        assert "CALL" in embed.title
        assert _get_balance(63005) == 800

    async def test_put_opens_option(self):
        _set_balance(63006, 1000)
        _seed_price("MSFT", 300.0)
        ctx = FakeContext(author=FakeAuthor(user_id=63006))
        cog = _make_stocks_cog()
        await cog.put_option.callback(cog, ctx, "MSFT", 300)
        embed = ctx.sent[0]["embed"]
        assert "PUT" in embed.title


class TestOptionsCommand:
    async def test_no_options_sends_text(self):
        ctx = FakeContext(author=FakeAuthor(user_id=64001))
        cog = _make_stocks_cog()
        await cog.options_cmd.callback(cog, ctx)
        assert ctx.sent

    async def test_with_open_option_shows_embed(self):
        _set_balance(64002, 500)
        _seed_price("AAPL", 100.0)
        open_option(64002, "AAPL", "call", 100, 100.0)
        ctx = FakeContext(author=FakeAuthor(user_id=64002))
        cog = _make_stocks_cog()
        await cog.options_cmd.callback(cog, ctx)
        embed = ctx.sent[0]["embed"]
        assert "AAPL" in (embed.description or "")


# ---------------------------------------------------------------------------
# _fetch_quotes — yfinance unit tests
# ---------------------------------------------------------------------------


class TestFetchQuotes:
    def test_empty_symbols_returns_empty(self):
        assert _fetch_quotes([]) == {}

    def test_single_symbol_success(self, monkeypatch):
        yf_mock = MagicMock()
        df = MagicMock()
        df["Close"].dropna.return_value.tolist.return_value = [100.0, 102.0, 105.0]
        yf_mock.download.return_value = df
        monkeypatch.setitem(sys.modules, "yfinance", yf_mock)

        result = _fetch_quotes(["AAPL"])
        assert "AAPL" in result
        assert result["AAPL"]["price"] == pytest.approx(105.0)
        assert result["AAPL"]["prev_close"] == pytest.approx(102.0)

    def test_single_symbol_only_one_close_prev_equals_price(self, monkeypatch):
        yf_mock = MagicMock()
        df = MagicMock()
        df["Close"].dropna.return_value.tolist.return_value = [100.0]
        yf_mock.download.return_value = df
        monkeypatch.setitem(sys.modules, "yfinance", yf_mock)

        result = _fetch_quotes(["AAPL"])
        assert result["AAPL"]["prev_close"] == pytest.approx(100.0)
        assert result["AAPL"]["price"] == pytest.approx(100.0)

    def test_multiple_symbols(self, monkeypatch):
        yf_mock = MagicMock()

        aapl_col = MagicMock()
        aapl_col.__getitem__.return_value.dropna.return_value.tolist.return_value = [150.0, 155.0]
        msft_col = MagicMock()
        msft_col.__getitem__.return_value.dropna.return_value.tolist.return_value = [300.0, 305.0]

        def _getitem(key):
            if key == "AAPL":
                return aapl_col
            elif key == "MSFT":
                return msft_col
            raise KeyError(key)

        df = MagicMock()
        df.__getitem__ = MagicMock(side_effect=_getitem)
        yf_mock.download.return_value = df
        monkeypatch.setitem(sys.modules, "yfinance", yf_mock)

        result = _fetch_quotes(["AAPL", "MSFT"])
        assert "AAPL" in result
        assert "MSFT" in result
        assert result["AAPL"]["price"] == pytest.approx(155.0)
        assert result["MSFT"]["price"] == pytest.approx(305.0)

    def test_keyerror_skips_symbol(self, monkeypatch):
        yf_mock = MagicMock()
        df = MagicMock()
        df.__getitem__ = MagicMock(side_effect=KeyError)
        yf_mock.download.return_value = df
        monkeypatch.setitem(sys.modules, "yfinance", yf_mock)

        result = _fetch_quotes(["AAPL", "MSFT"])
        assert result == {}

    def test_empty_closes_skips_symbol(self, monkeypatch):
        yf_mock = MagicMock()
        df = MagicMock()
        df["Close"].dropna.return_value.tolist.return_value = []
        yf_mock.download.return_value = df
        monkeypatch.setitem(sys.modules, "yfinance", yf_mock)

        result = _fetch_quotes(["AAPL"])
        assert result == {}


# ---------------------------------------------------------------------------
# _validate_symbol — yfinance unit tests
# ---------------------------------------------------------------------------


class TestValidateSymbol:
    def test_success_with_short_name(self, monkeypatch):
        yf_mock = MagicMock()
        ticker_mock = MagicMock()
        ticker_mock.fast_info = {"last_price": 150.0}
        ticker_mock.info = {"shortName": "Apple Inc.", "longName": None}
        yf_mock.Ticker.return_value = ticker_mock
        monkeypatch.setitem(sys.modules, "yfinance", yf_mock)

        ok, name, price = _validate_symbol("AAPL")
        assert ok is True
        assert price == pytest.approx(150.0)
        assert name == "Apple Inc."

    def test_zero_price_returns_false(self, monkeypatch):
        yf_mock = MagicMock()
        ticker_mock = MagicMock()
        ticker_mock.fast_info = {"last_price": 0.0}
        yf_mock.Ticker.return_value = ticker_mock
        monkeypatch.setitem(sys.modules, "yfinance", yf_mock)

        ok, name, price = _validate_symbol("FAKE")
        assert ok is False
        assert name is None
        assert price is None

    def test_none_fast_info_returns_false(self, monkeypatch):
        yf_mock = MagicMock()
        ticker_mock = MagicMock()
        ticker_mock.fast_info = None
        yf_mock.Ticker.return_value = ticker_mock
        monkeypatch.setitem(sys.modules, "yfinance", yf_mock)

        ok, name, price = _validate_symbol("FAKE")
        assert ok is False

    def test_import_error_returns_false(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "yfinance", None)
        ok, name, price = _validate_symbol("AAPL")
        assert ok is False
        assert name is None
        assert price is None

    def test_info_exception_falls_back_to_symbol(self, monkeypatch):
        yf_mock = MagicMock()
        ticker_mock = MagicMock()
        ticker_mock.fast_info = {"last_price": 100.0}
        ticker_mock.info.get.side_effect = Exception("rate limited")
        yf_mock.Ticker.return_value = ticker_mock
        monkeypatch.setitem(sys.modules, "yfinance", yf_mock)
        ok, name, price = _validate_symbol("AAPL")
        assert ok is True
        assert name == "AAPL"  # falls back to symbol

    def test_exception_returns_false(self, monkeypatch):
        yf_mock = MagicMock()
        yf_mock.Ticker.side_effect = Exception("connection error")
        monkeypatch.setitem(sys.modules, "yfinance", yf_mock)

        ok, name, price = _validate_symbol("BROKEN")
        assert ok is False
        assert price is None

    def test_falls_back_to_symbol_when_no_name(self, monkeypatch):
        yf_mock = MagicMock()
        ticker_mock = MagicMock()
        ticker_mock.fast_info = {"last_price": 42.0}
        ticker_mock.info = {}
        yf_mock.Ticker.return_value = ticker_mock
        monkeypatch.setitem(sys.modules, "yfinance", yf_mock)

        ok, name, price = _validate_symbol("XYZW")
        assert ok is True
        assert name == "XYZW"


# ---------------------------------------------------------------------------
# execute_buy / execute_sell new-user auto-create paths
# ---------------------------------------------------------------------------


class TestExecuteBuyAutoCreate:
    def test_auto_creates_user_row_on_first_buy(self):
        user_id = 95001
        assert shared.db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is None
        new_bal = execute_buy(user_id, "AAPL", 0.5, 100.0, 50)
        assert new_bal == shared.STARTING_BALANCE - 50

    def test_blend_avg_cost_on_second_buy(self):
        user_id = 95002
        shared.db.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, 10000))
        shared.db.commit()
        execute_buy(user_id, "MSFT", 2.0, 100.0, 200)
        execute_buy(user_id, "MSFT", 2.0, 200.0, 400)
        holding = get_user_holding(user_id, "MSFT")
        assert holding["shares"] == pytest.approx(4.0)
        assert holding["avg_cost"] == pytest.approx(150.0)  # (200+400)/4


class TestExecuteSellAutoCreate:
    def test_sell_auto_creates_user_on_no_balance_row(self):
        user_id = 95010
        shared.db.execute(
            "INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) VALUES (?, ?, ?, ?)",
            (user_id, "AAPL", 5.0, 100.0),
        )
        shared.db.commit()
        result = execute_sell(user_id, "AAPL", 5.0, 110.0, 550)
        assert result is not None
        new_bal, _ = result
        assert new_bal == shared.STARTING_BALANCE + 550


class TestOpenOptionAutoCreate:
    def test_open_creates_user_row_if_missing(self):
        user_id = 95020
        assert shared.db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is None
        opt_id = open_option(user_id, "AAPL", "call", 50, 100.0)
        assert opt_id > 0
        assert shared.db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is not None


# ---------------------------------------------------------------------------
# make_sparkline / _fmt_shares edge cases
# ---------------------------------------------------------------------------


class TestSparklineFlat:
    def test_flat_values_returns_middle_block_repeated(self):
        result = make_sparkline([5.0, 5.0, 5.0])
        mid = SPARK_BLOCKS[len(SPARK_BLOCKS) // 2]
        assert result == mid * 3


class TestFmtShares:
    def test_fractional_value_keeps_significant_digits(self):
        assert _fmt_shares(0.1) == "0.1"
        assert _fmt_shares(0.1234) == "0.1234"

    def test_whole_number_strips_decimal(self):
        assert _fmt_shares(3.0) == "3"

    def test_trailing_zeros_stripped(self):
        assert _fmt_shares(1.5000) == "1.5"


# ---------------------------------------------------------------------------
# _resolve_quantity additional edge cases
# ---------------------------------------------------------------------------


class TestResolveQuantityMore:
    def test_none_arg_returns_missing_error(self):
        qty, err = StocksCog._resolve_quantity(None, price=10.0)
        assert qty is None
        assert err and "missing" in err.lower()

    def test_dollar_invalid_string_returns_error(self):
        qty, err = StocksCog._resolve_quantity("$abc", price=10.0)
        assert qty is None
        assert err and "invalid coin" in err.lower()

    def test_dollar_zero_coins_returns_error(self):
        qty, err = StocksCog._resolve_quantity("$0", price=10.0)
        assert qty is None
        assert err


# ---------------------------------------------------------------------------
# buy / sell command edge-case paths
# ---------------------------------------------------------------------------


class TestBuyEdgeCases:
    async def test_no_price_sends_error(self):
        shared.db.execute("DELETE FROM stock_prices WHERE ticker = 'AAPL'")
        stocks._history_cache.clear()
        shared.db.commit()
        _set_balance(96001, 1000)
        ctx = FakeContext(author=FakeAuthor(user_id=96001))
        await _make_stocks_cog().buy.callback(_make_stocks_cog(), ctx, "AAPL", "5")
        embed = ctx.sent[0]["embed"]
        assert embed is not None

    async def test_invalid_qty_sends_error(self):
        _seed_price("AAPL", 100.0)
        _set_balance(96002, 1000)
        ctx = FakeContext(author=FakeAuthor(user_id=96002))
        cog = _make_stocks_cog()
        await cog.buy.callback(cog, ctx, "AAPL", "banana")
        embed = ctx.sent[0]["embed"]
        assert "Invalid" in embed.title


class TestSellEdgeCases:
    async def test_unknown_ticker_sends_error(self):
        ctx = FakeContext(author=FakeAuthor(user_id=96010))
        cog = _make_stocks_cog()
        await cog.sell.callback(cog, ctx, "XXXXNOPE", "1")
        embed = ctx.sent[0]["embed"]
        assert embed is not None

    async def test_no_price_sends_error(self):
        buy_shares(96011, "AAPL", 2.0, 100.0)
        shared.db.execute("DELETE FROM stock_prices WHERE ticker = 'AAPL'")
        stocks._history_cache.clear()
        shared.db.commit()
        ctx = FakeContext(author=FakeAuthor(user_id=96011))
        cog = _make_stocks_cog()
        await cog.sell.callback(cog, ctx, "AAPL", "1")
        embed = ctx.sent[0]["embed"]
        assert embed is not None

    async def test_invalid_qty_sends_error(self):
        _seed_price("MSFT", 300.0)
        buy_shares(96012, "MSFT", 2.0, 300.0)
        ctx = FakeContext(author=FakeAuthor(user_id=96012))
        cog = _make_stocks_cog()
        await cog.sell.callback(cog, ctx, "MSFT", "banana")
        embed = ctx.sent[0]["embed"]
        assert "Invalid" in embed.title

    async def test_more_than_owned_sends_error(self):
        _seed_price("NVDA", 500.0)
        buy_shares(96013, "NVDA", 1.0, 500.0)
        ctx = FakeContext(author=FakeAuthor(user_id=96013))
        cog = _make_stocks_cog()
        await cog.sell.callback(cog, ctx, "NVDA", "10")
        embed = ctx.sent[0]["embed"]
        assert embed is not None

    async def test_execute_sell_none_sends_error(self, monkeypatch):
        _seed_price("TSLA", 200.0)
        buy_shares(96014, "TSLA", 5.0, 200.0)
        monkeypatch.setattr(stocks, "execute_sell", lambda *a, **kw: None)
        ctx = FakeContext(author=FakeAuthor(user_id=96014))
        cog = _make_stocks_cog()
        await cog.sell.callback(cog, ctx, "TSLA", "2")
        embed = ctx.sent[0]["embed"]
        assert embed is not None


# ---------------------------------------------------------------------------
# call/put negative bet and no-price paths
# ---------------------------------------------------------------------------


class TestCallPutEdgeCases:
    async def test_call_negative_bet_opens_short(self):
        _set_balance(96020, 1000)
        _seed_price("AAPL", 100.0)
        ctx = FakeContext(author=FakeAuthor(user_id=96020))
        cog = _make_stocks_cog()
        await cog.call_option.callback(cog, ctx, "AAPL", -100)
        embed = ctx.sent[0]["embed"]
        assert "SHORT" in embed.title

    async def test_call_no_price_rejected(self):
        _set_balance(96021, 1000)
        shared.db.execute("DELETE FROM stock_prices WHERE ticker = 'AAPL'")
        stocks._history_cache.clear()
        shared.db.commit()
        ctx = FakeContext(author=FakeAuthor(user_id=96021))
        cog = _make_stocks_cog()
        await cog.call_option.callback(cog, ctx, "AAPL", 100)
        embed = ctx.sent[0]["embed"]
        assert embed is not None

    async def test_put_negative_bet_rejected(self):
        _set_balance(96022, 1000)
        _seed_price("MSFT", 300.0)
        ctx = FakeContext(author=FakeAuthor(user_id=96022))
        cog = _make_stocks_cog()
        await cog.put_option.callback(cog, ctx, "MSFT", 0)
        embed = ctx.sent[0]["embed"]
        assert "Invalid" in embed.title


# ---------------------------------------------------------------------------
# options_cmd in-the-money display and timezone path
# ---------------------------------------------------------------------------


class TestOptionsCommandMore:
    async def test_in_money_call_shows_green(self):
        _set_balance(96030, 1000)
        _seed_price("AAPL", 115.0)  # above strike 100 → call in money
        open_option(96030, "AAPL", "call", 200, 100.0)
        ctx = FakeContext(author=FakeAuthor(user_id=96030))
        cog = _make_stocks_cog()
        await cog.options_cmd.callback(cog, ctx)
        desc = ctx.sent[0]["embed"].description or ""
        assert "🟢" in desc

    async def test_out_of_money_call_shows_red(self):
        _set_balance(96031, 1000)
        _seed_price("AAPL", 85.0)  # below strike 100 → call out of money
        open_option(96031, "AAPL", "call", 200, 100.0)
        ctx = FakeContext(author=FakeAuthor(user_id=96031))
        cog = _make_stocks_cog()
        await cog.options_cmd.callback(cog, ctx)
        desc = ctx.sent[0]["embed"].description or ""
        assert "🔴" in desc

    async def test_timezone_naive_expires_at_handled(self):
        _set_balance(96032, 500)
        _seed_price("AAPL", 90.0)
        open_option(96032, "AAPL", "put", 100, 100.0)
        shared.db.execute(
            "UPDATE options SET expires_at = '2030-01-01T00:00:00' WHERE user_id = ?", (96032,)
        )
        shared.db.commit()
        ctx = FakeContext(author=FakeAuthor(user_id=96032))
        cog = _make_stocks_cog()
        await cog.options_cmd.callback(cog, ctx)
        assert ctx.sent


# ---------------------------------------------------------------------------
# stocks command dispatch edge cases
# ---------------------------------------------------------------------------


class TestStocksCommandDispatch:
    async def test_list_arg_shows_overview(self):
        _seed_price("AAPL", 150.0)
        ctx = FakeContext()
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx, "list")
        assert ctx.sent[0]["embed"] is not None

    async def test_remove_missing_ticker_arg_sends_usage(self):
        ctx = FakeContext(author=FakeAuthor(user_id=0))  # admin
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx, "remove")
        assert ctx.sent

    async def test_add_missing_ticker_arg_sends_usage(self):
        ctx = FakeContext()
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx, "add")
        assert ctx.sent

    async def test_del_alias_dispatches_to_remove(self):
        ctx = FakeContext(author=FakeAuthor(user_id=0))
        cog = _make_stocks_cog()
        # "del" with no ticker → usage message
        await cog.stocks.callback(cog, ctx, "del")
        assert ctx.sent

    async def test_remove_with_ticker_dispatches_to_stock_remove(self):
        add_ticker("ZZDISP", "ZZDISP Corp")
        ctx = FakeContext(
            author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
            guild=FakeGuild(),
        )
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx, "remove", "ZZDISP")
        assert ctx.sent
        assert "ZZDISP" not in get_tickers()


class TestStocksOverviewNoPrices:
    async def test_no_prices_sends_unavailable_embed(self):
        shared.db.execute("DELETE FROM stock_prices")
        stocks._history_cache.clear()
        shared.db.commit()
        ctx = FakeContext()
        cog = _make_stocks_cog()
        await cog._stock_overview(ctx)
        embed = ctx.sent[0]["embed"]
        assert "Unavailable" in embed.title


class TestStockAddCryptoRemap:
    async def test_btc_remapped_to_btc_usd(self, monkeypatch):
        monkeypatch.setattr(stocks, "_validate_symbol", lambda t: (True, "Bitcoin", 50000.0))
        monkeypatch.setattr(stocks, "_fetch_quotes", lambda tickers: {})
        ctx = FakeContext()
        ctx.typing = MagicMock(return_value=_async_cm())
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx, "add", "BTC")
        assert "BTC-USD" in get_tickers()
        embed = ctx.sent[0]["embed"]
        assert "mapped" in (embed.description or "").lower()


class TestStockAddFetchException:
    async def test_price_history_fetch_error_still_adds_ticker(self, monkeypatch):
        monkeypatch.setattr(stocks, "_validate_symbol", lambda t: (True, "Test Corp", 42.0))

        def _bad_fetch(tickers):
            raise RuntimeError("network error")

        monkeypatch.setattr(stocks, "_fetch_quotes", _bad_fetch)
        ctx = FakeContext()
        ctx.typing = MagicMock(return_value=_async_cm())
        cog = _make_stocks_cog()
        await cog.stocks.callback(cog, ctx, "add", "TESTERR")
        assert "TESTERR" in get_tickers()
        assert "Added" in ctx.sent[0]["embed"].title


class TestStockRemoveWarnings:
    def _make_cog(self):
        bot = MagicMock()
        bot.loop = asyncio.get_event_loop()
        return StocksCog(bot)

    async def test_warns_about_remaining_share_holders(self):
        add_ticker("ZZWARN", "ZZWARN Corp")
        buy_shares(97001, "ZZWARN", 3.0, 50.0)
        ctx = FakeContext(
            author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
            guild=FakeGuild(),
        )
        await self._make_cog()._stock_remove(ctx, "ZZWARN")
        desc = ctx.last_send["embed"].description or ""
        assert "holder" in desc.lower() or "shares" in desc.lower() or "user" in desc.lower()

    async def test_no_warning_when_no_holders_or_options(self):
        add_ticker("ZZCLEAN", "ZZCLEAN Corp")
        ctx = FakeContext(
            author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
            guild=FakeGuild(),
        )
        await self._make_cog()._stock_remove(ctx, "ZZCLEAN")
        desc = ctx.last_send["embed"].description or ""
        # Simple removal message without warning lines
        assert "ZZCLEAN" in desc


class TestStockDetailEdgeCases:
    async def test_no_price_data_sends_error(self):
        shared.db.execute("DELETE FROM stock_prices WHERE ticker = 'AAPL'")
        stocks._history_cache.clear()
        shared.db.commit()
        ctx = FakeContext(author=FakeAuthor(user_id=97010))
        cog = _make_stocks_cog()
        await cog._stock_detail(ctx, "AAPL")
        embed = ctx.sent[0]["embed"]
        assert "No Price" in embed.title

    async def test_no_history_shows_loading_text(self):
        refresh_prices({"MSFT": {"price": 300.0, "prev_close": 295.0, "history": []}})
        ctx = FakeContext(author=FakeAuthor(user_id=97011))
        cog = _make_stocks_cog()
        await cog._stock_detail(ctx, "MSFT")
        desc = ctx.sent[0]["embed"].description or ""
        assert "loading" in desc.lower() or "next tick" in desc.lower()

    async def test_with_holding_shows_position(self):
        _seed_price("GOOGL", 200.0)
        buy_shares(97012, "GOOGL", 2.0, 150.0)
        ctx = FakeContext(author=FakeAuthor(user_id=97012))
        cog = _make_stocks_cog()
        await cog._stock_detail(ctx, "GOOGL")
        desc = ctx.sent[0]["embed"].description or ""
        assert "position" in desc.lower()

    async def test_with_holding_and_realized_pl(self):
        _seed_price("AMZN", 300.0)
        buy_shares(97013, "AMZN", 2.0, 200.0)
        sell_shares(97013, "AMZN", 1.0, 250.0)
        ctx = FakeContext(author=FakeAuthor(user_id=97013))
        cog = _make_stocks_cog()
        await cog._stock_detail(ctx, "AMZN")
        desc = ctx.sent[0]["embed"].description or ""
        assert "realized" in desc.lower() or "Realized" in desc


# ---------------------------------------------------------------------------
# Cog task / lifecycle tests
# ---------------------------------------------------------------------------


class TestStartStockTasks:
    async def test_starts_tasks_when_not_running(self):
        bot = MagicMock()
        cog = StocksCog(bot)
        cog.stock_tick_check = MagicMock()
        cog.stock_tick_check.is_running.return_value = False
        cog.options_settle_check = MagicMock()
        cog.options_settle_check.is_running.return_value = False
        await cog._start_stock_tasks()
        cog.stock_tick_check.start.assert_called_once()
        cog.options_settle_check.start.assert_called_once()

    async def test_does_not_restart_already_running_tasks(self):
        bot = MagicMock()
        cog = StocksCog(bot)
        cog.stock_tick_check = MagicMock()
        cog.stock_tick_check.is_running.return_value = True
        cog.options_settle_check = MagicMock()
        cog.options_settle_check.is_running.return_value = True
        await cog._start_stock_tasks()
        cog.stock_tick_check.start.assert_not_called()
        cog.options_settle_check.start.assert_not_called()


class TestWaitUntilReady:
    async def test_calls_bot_wait_and_refresh(self):
        bot = MagicMock()
        bot.wait_until_ready = AsyncMock()
        cog = StocksCog(bot)
        cog._refresh_all = AsyncMock()
        await cog._wait_until_ready()
        bot.wait_until_ready.assert_called_once()
        cog._refresh_all.assert_called_once()

    async def test_refresh_exception_is_swallowed(self):
        bot = MagicMock()
        bot.wait_until_ready = AsyncMock()
        cog = StocksCog(bot)
        cog._refresh_all = AsyncMock(side_effect=RuntimeError("net fail"))
        await cog._wait_until_ready()  # Should not raise

    async def test_wait_until_ready_opts(self):
        bot = MagicMock()
        bot.wait_until_ready = AsyncMock()
        cog = StocksCog(bot)
        await cog._wait_until_ready_opts()
        bot.wait_until_ready.assert_called_once()


class TestRefreshAll:
    async def test_no_tickers_returns_early(self, monkeypatch):
        fetch_mock = MagicMock()
        monkeypatch.setattr(stocks, "_fetch_quotes", fetch_mock)
        cog = _make_stocks_cog()
        # Clear tickers after cog creation (init_tickers() runs in __init__)
        shared.db.execute("DELETE FROM stock_tickers")
        shared.db.commit()
        await cog._refresh_all()
        fetch_mock.assert_not_called()
        init_tickers()  # restore

    async def test_success_updates_prices(self, monkeypatch):
        monkeypatch.setattr(
            stocks,
            "_fetch_quotes",
            lambda syms: {s: {"price": 100.0, "prev_close": 99.0, "history": [99.0, 100.0]} for s in syms},
        )
        cog = _make_stocks_cog()
        await cog._refresh_all()
        prices = get_all_prices()
        assert len(prices) > 0

    async def test_fetch_exception_is_swallowed(self, monkeypatch):
        def _fail(syms):
            raise RuntimeError("yfinance down")

        monkeypatch.setattr(stocks, "_fetch_quotes", _fail)
        cog = _make_stocks_cog()
        await cog._refresh_all()  # Should not raise


class TestPostMorningAnnouncement:
    async def test_no_channel_id_is_noop(self):
        shared.runtime_settings.pop("ticker_channel_id", None)
        cog = _make_stocks_cog()
        await cog._post_morning_announcement()  # Should not raise

    async def test_channel_not_found_is_noop(self):
        shared.runtime_settings["ticker_channel_id"] = 99991
        bot = MagicMock()
        bot.get_channel.return_value = None
        bot.fetch_channel = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "not found"))
        cog = StocksCog(bot)
        _seed_price("AAPL", 150.0)
        await cog._post_morning_announcement()  # Should not raise

    async def test_sends_embed_when_prices_available(self):
        _seed_price("AAPL", 150.0, 148.0)
        fake_channel = MagicMock()
        fake_channel.send = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = fake_channel
        cog = StocksCog(bot)
        shared.runtime_settings["ticker_channel_id"] = 99992
        await cog._post_morning_announcement()
        fake_channel.send.assert_called_once()

    async def test_channel_none_after_fetch_returns(self):
        shared.runtime_settings["ticker_channel_id"] = 99993
        bot = MagicMock()
        bot.get_channel.return_value = None
        bot.fetch_channel = AsyncMock(return_value=None)
        cog = StocksCog(bot)
        _seed_price("AAPL", 150.0)
        await cog._post_morning_announcement()  # should return at "if channel is None"

    async def test_channel_send_http_exception_swallowed(self):
        _seed_price("AAPL", 150.0, 148.0)
        fake_channel = MagicMock()
        fake_channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "rate limit"))
        bot = MagicMock()
        bot.get_channel.return_value = fake_channel
        cog = StocksCog(bot)
        shared.runtime_settings["ticker_channel_id"] = 99994
        await cog._post_morning_announcement()  # Should not raise

    async def test_no_market_lines_skips_send(self):
        shared.db.execute("DELETE FROM stock_prices")
        stocks._history_cache.clear()
        shared.db.commit()
        fake_channel = MagicMock()
        fake_channel.send = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = fake_channel
        cog = StocksCog(bot)
        shared.runtime_settings["ticker_channel_id"] = 99995
        await cog._post_morning_announcement()
        fake_channel.send.assert_not_called()


class TestStockTickCheck:
    async def test_market_closed_returns_early(self, monkeypatch):
        monkeypatch.setattr(stocks, "is_market_open", lambda: False)
        cog = _make_stocks_cog()
        cog._refresh_all = AsyncMock()
        await cog.stock_tick_check.coro(cog)
        cog._refresh_all.assert_not_called()

    async def test_same_hour_key_returns_early(self, monkeypatch):
        monkeypatch.setattr(stocks, "is_market_open", lambda: True)
        now_central = datetime.now(CENTRAL_TZ)
        hour_key = now_central.strftime("%Y-%m-%d %H")
        shared.runtime_settings["ticker_last_tick_key"] = hour_key
        cog = _make_stocks_cog()
        cog._refresh_all = AsyncMock()
        await cog.stock_tick_check.coro(cog)
        cog._refresh_all.assert_not_called()

    async def test_new_hour_does_refresh_and_tick(self, monkeypatch):
        monkeypatch.setattr(stocks, "is_market_open", lambda: True)
        shared.runtime_settings.pop("ticker_last_tick_key", None)
        shared.runtime_settings.pop("ticker_last_morning_date", None)
        cog = _make_stocks_cog()
        cog._refresh_all = AsyncMock()
        cog._post_morning_announcement = AsyncMock()
        await cog.stock_tick_check.coro(cog)
        cog._refresh_all.assert_called_once()
        cog._post_morning_announcement.assert_called_once()

    async def test_no_morning_post_if_already_done_today(self, monkeypatch):
        monkeypatch.setattr(stocks, "is_market_open", lambda: True)
        shared.runtime_settings.pop("ticker_last_tick_key", None)
        today = datetime.now(CENTRAL_TZ).strftime("%Y-%m-%d")
        shared.runtime_settings["ticker_last_morning_date"] = today
        cog = _make_stocks_cog()
        cog._refresh_all = AsyncMock()
        cog._post_morning_announcement = AsyncMock()
        await cog.stock_tick_check.coro(cog)
        cog._post_morning_announcement.assert_not_called()


class TestOptionsSettleCheck:
    async def test_no_expired_options_is_noop(self):
        cog = _make_stocks_cog()
        # Clears early when no expired rows
        await cog.options_settle_check.coro(cog)

    async def test_settles_expired_call_win(self):
        _set_balance(98001, 1000)
        _seed_price("AAPL", 120.0)
        opt_id = open_option(98001, "AAPL", "call", 200, 100.0)
        shared.db.execute(
            "UPDATE options SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (opt_id,)
        )
        shared.db.commit()
        cog = _make_stocks_cog()
        await cog.options_settle_check.coro(cog)
        assert get_open_options(98001) == []

    async def test_settles_expired_call_loss(self):
        _set_balance(98002, 1000)
        _seed_price("AAPL", 80.0)  # below strike → call loses
        opt_id = open_option(98002, "AAPL", "call", 200, 100.0)
        shared.db.execute(
            "UPDATE options SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (opt_id,)
        )
        shared.db.commit()
        cog = _make_stocks_cog()
        await cog.options_settle_check.coro(cog)
        assert get_open_options(98002) == []

    async def test_settles_with_channel_and_user_mention(self):
        _set_balance(98003, 1000)
        _seed_price("MSFT", 250.0)
        opt_id = open_option(98003, "MSFT", "put", 150, 300.0)
        shared.db.execute(
            "UPDATE options SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (opt_id,)
        )
        shared.db.commit()

        fake_channel = MagicMock()
        fake_channel.send = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = fake_channel
        bot.get_user.return_value = None

        cog = StocksCog(bot)
        shared.runtime_settings["ticker_channel_id"] = 99994
        await cog.options_settle_check.coro(cog)

        fake_channel.send.assert_called_once()
        assert get_open_options(98003) == []

    async def test_settles_with_user_object_for_mention(self):
        _set_balance(98004, 1000)
        _seed_price("NVDA", 600.0)
        opt_id = open_option(98004, "NVDA", "call", 100, 500.0)
        shared.db.execute(
            "UPDATE options SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (opt_id,)
        )
        shared.db.commit()

        fake_user = MagicMock()
        fake_user.mention = "<@98004>"
        fake_channel = MagicMock()
        fake_channel.send = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = fake_channel
        bot.get_user.return_value = fake_user

        cog = StocksCog(bot)
        shared.runtime_settings["ticker_channel_id"] = 99995
        await cog.options_settle_check.coro(cog)

        fake_channel.send.assert_called_once()

    async def test_settles_loss_with_channel(self):
        _set_balance(98010, 1000)
        _seed_price("AAPL", 80.0)  # below strike → call loses
        opt_id = open_option(98010, "AAPL", "call", 150, 100.0)
        shared.db.execute(
            "UPDATE options SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (opt_id,)
        )
        shared.db.commit()

        fake_channel = MagicMock()
        fake_channel.send = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = fake_channel
        bot.get_user.return_value = None

        cog = StocksCog(bot)
        shared.runtime_settings["ticker_channel_id"] = 99998
        await cog.options_settle_check.coro(cog)

        fake_channel.send.assert_called_once()
        desc = fake_channel.send.call_args[1]["embed"].description or ""
        assert "Lost" in desc or "🔴" in desc
        assert get_open_options(98010) == []

    async def test_zero_price_skips_settlement(self):
        _set_balance(98005, 1000)
        opt_id = open_option(98005, "AAPL", "call", 100, 100.0)
        shared.db.execute("DELETE FROM stock_prices WHERE ticker = 'AAPL'")
        stocks._history_cache.clear()
        shared.db.commit()
        shared.db.execute(
            "UPDATE options SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (opt_id,)
        )
        shared.db.commit()
        cog = _make_stocks_cog()
        await cog.options_settle_check.coro(cog)
        # Still open because exit_price was 0
        assert len(get_open_options(98005)) == 1

    async def test_channel_http_exception_swallowed(self):
        _set_balance(98006, 1000)
        _seed_price("AAPL", 150.0)
        opt_id = open_option(98006, "AAPL", "call", 100, 100.0)
        shared.db.execute(
            "UPDATE options SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (opt_id,)
        )
        shared.db.commit()

        fake_channel = MagicMock()
        fake_channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "rate limited"))
        bot = MagicMock()
        bot.get_channel.return_value = fake_channel
        bot.get_user.return_value = None

        cog = StocksCog(bot)
        shared.runtime_settings["ticker_channel_id"] = 99996
        await cog.options_settle_check.coro(cog)  # Should not raise
        assert get_open_options(98006) == []

    async def test_channel_fetch_http_exception_swallowed(self):
        _set_balance(98007, 1000)
        _seed_price("AAPL", 150.0)
        opt_id = open_option(98007, "AAPL", "call", 100, 100.0)
        shared.db.execute(
            "UPDATE options SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (opt_id,)
        )
        shared.db.commit()

        bot = MagicMock()
        bot.get_channel.return_value = None
        bot.fetch_channel = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "not found"))

        cog = StocksCog(bot)
        shared.runtime_settings["ticker_channel_id"] = 99997
        await cog.options_settle_check.coro(cog)  # Should not raise
        assert get_open_options(98007) == []


# ---------------------------------------------------------------------------
# portfolio command with member arg
# ---------------------------------------------------------------------------


class TestPortfolioWithMember:
    async def test_view_another_members_portfolio(self):
        _set_balance(99001, 5000)
        _seed_price("AAPL", 200.0)
        buy_shares(99001, "AAPL", 3.0, 150.0)

        member_mock = MagicMock()
        member_mock.id = 99001
        member_mock.display_name = "OtherUser"

        ctx = FakeContext(author=FakeAuthor(user_id=99002))
        cog = _make_stocks_cog()
        await cog.portfolio.callback(cog, ctx, member_mock)
        embed = ctx.sent[0]["embed"]
        assert "AAPL" in (embed.description or "")

    async def test_empty_portfolio_for_member(self):
        member_mock = MagicMock()
        member_mock.id = 99003
        member_mock.display_name = "EmptyUser"

        ctx = FakeContext(author=FakeAuthor(user_id=99004))
        cog = _make_stocks_cog()
        await cog.portfolio.callback(cog, ctx, member_mock)
        assert ctx.sent  # some message was sent
