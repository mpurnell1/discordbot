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

    def test_legacy_holdings_are_refunded_at_avg_cost_and_dropped(self):
        # Simulate a pre-revamp DB: a legacy ticker with a price that diverged
        # from the user's cost basis. Refund must be at avg_cost (what the
        # user paid), NOT at the simulated last price.
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
        shared.init_db()
        # Refund = 10 * 40 (avg_cost) = 400 → 500 + 400 = 900
        assert get_user_holding(5555, "GARY") is None
        assert shared.get_balance(5555) == 900
        assert shared.db.execute(
            "SELECT 1 FROM stock_prices WHERE ticker = 'GARY'"
        ).fetchone() is None
        # And the refund was logged to balance_history for auditability.
        last = shared.db.execute(
            "SELECT balance FROM balance_history WHERE user_id = 5555 "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert last is not None and last[0] == 900

    def test_legacy_migration_creates_user_row_if_missing(self):
        # A holder that has no row in `users` yet still gets refunded.
        shared.db.execute(
            "INSERT INTO stock_prices (ticker, price, prev_close, last_updated) "
            "VALUES ('DOGE', 1.50, 1.50, '2026-05-15T12:00:00+00:00')"
        )
        shared.db.execute(
            "INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) "
            "VALUES (6666, 'DOGE', 100, 2.00)"
        )
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
        shared.db.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?)", (7777, 100)
        )
        shared.db.execute(
            "INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) "
            "VALUES (7777, 'GARY', 5, 10.00)"
        )
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
        "INSERT INTO users (user_id, balance) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET balance = ?",
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
        open_option(20004, "AMD",  "put",  100, 100.0)
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
        open_option(20012, "MSFT", "put",  100, 400.0)
        assert len(get_open_options(20011)) == 1
        assert len(get_open_options(20012)) == 1

    def test_no_filter_returns_all_users(self):
        _set_balance(20013, 500)
        _set_balance(20014, 500)
        open_option(20013, "SPY", "call", 100, 500.0)
        open_option(20014, "SPY", "put",  100, 500.0)
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
