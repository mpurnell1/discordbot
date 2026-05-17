"""Unit tests for the real-US-stocks module.

The yfinance integration is mocked: tests monkeypatch `_fetch_quotes` and
`_validate_symbol` so they never hit the network.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import shared
from modules import stocks
from modules.stocks import (
    EASTERN_TZ,
    SEED_TICKERS,
    SPARK_BLOCKS,
    StocksCog,
    add_ticker,
    buy_shares,
    execute_buy,
    execute_sell,
    get_all_prices,
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
    refresh_prices,
    sell_shares,
)


@pytest.fixture(autouse=True)
def _seed_tickers():
    # Always start with a clean in-memory history cache and the curated seed list.
    stocks._history_cache.clear()
    init_tickers()
    yield


def _seed_price(ticker: str, price: float, prev_close: float | None = None) -> None:
    refresh_prices({ticker: {
        "price": price,
        "prev_close": prev_close if prev_close is not None else price,
        "history": [price],
    }})


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
        refresh_prices({"AAPL": {
            "price": 170.50,
            "prev_close": 168.00,
            "history": [165.0, 166.0, 167.0, 168.0, 170.50],
        }})
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
        assert cost == pytest.approx(50.00)    # 0.5 * 100


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
        shared.db.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?)", (self.USER_ID, 1000)
        )
        shared.db.commit()
        # 0.5 sh of a $200 stock → 100 coins
        new_bal = execute_buy(self.USER_ID, "AAPL", 0.5, 200.00, cost=100)
        assert new_bal == 900
        holding = get_user_holding(self.USER_ID, "AAPL")
        assert holding == {"shares": pytest.approx(0.5), "avg_cost": pytest.approx(200.00)}


class TestExecuteSell:
    USER_ID = 9100

    def _seed_position(self, shares: float, avg_cost: float, balance: int = 0):
        shared.db.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?)", (self.USER_ID, balance)
        )
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
        shared.db.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, 1000)
        )
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
        shared.db.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, 100)
        )
        shared.db.execute(
            "INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) "
            "VALUES (?, ?, ?, ?)",
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

    def test_legacy_holdings_are_refunded_and_dropped(self):
        # Simulate a pre-revamp DB: legacy ticker price + holding.
        shared.db.execute(
            "INSERT INTO stock_prices (ticker, price, prev_close, last_updated) "
            "VALUES ('GARY', 42.00, 42.00, '2026-05-15T12:00:00+00:00')"
        )
        shared.db.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?)", (5555, 500)
        )
        shared.db.execute(
            "INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) "
            "VALUES (5555, 'GARY', 10, 40.00)"
        )
        shared.db.commit()
        # Re-run the legacy purge by reinitializing the schema.
        # init_db is idempotent and re-running it triggers the migration block.
        shared.init_db()
        # Holding gone, balance credited by 10 * 42 = 420 → 500 + 420 = 920
        assert get_user_holding(5555, "GARY") is None
        assert shared.get_balance(5555) == 920
        # The legacy ticker row is gone too.
        assert shared.db.execute(
            "SELECT 1 FROM stock_prices WHERE ticker = 'GARY'"
        ).fetchone() is None
