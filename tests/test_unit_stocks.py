"""Unit tests for the simulated stock market module."""
from __future__ import annotations

import random

import pytest

import shared
from modules.stocks import (
    PRICE_CEILING,
    PRICE_FLOOR,
    SPARK_BLOCKS,
    TICKERS,
    StocksCog,
    advance_prices,
    buy_shares,
    execute_buy,
    execute_sell,
    get_all_prices,
    get_portfolio_value,
    get_price,
    get_price_history,
    get_realized_pl,
    get_user_holding,
    get_user_holdings,
    init_stock_prices,
    make_sparkline,
    record_price_snapshot,
    sell_shares,
    snapshot_prev_close,
)


@pytest.fixture(autouse=True)
def _seed_prices():
    init_stock_prices()
    yield


class TestInit:
    def test_every_ticker_seeded_at_base(self):
        prices = get_all_prices()
        assert set(prices.keys()) == set(TICKERS.keys())
        for ticker, cfg in TICKERS.items():
            assert prices[ticker]["price"] == pytest.approx(cfg["base"])
            assert prices[ticker]["prev_close"] == pytest.approx(cfg["base"])

    def test_init_is_idempotent(self):
        # Mutate a price, re-init, and verify the existing row is left alone.
        shared.db.execute(
            "UPDATE stock_prices SET price = 999.99 WHERE ticker = 'GARY'"
        )
        shared.db.commit()
        init_stock_prices()
        assert get_price("GARY") == pytest.approx(999.99)


class TestPriceTick:
    def test_advance_keeps_prices_in_bounds(self):
        random.seed(0)
        for _ in range(50):
            advance_prices()
        for ticker in TICKERS:
            price = get_price(ticker)
            assert PRICE_FLOOR <= price <= PRICE_CEILING

    def test_advance_does_not_touch_prev_close(self):
        random.seed(1)
        before = {t: d["prev_close"] for t, d in get_all_prices().items()}
        advance_prices()
        after = {t: d["prev_close"] for t, d in get_all_prices().items()}
        assert before == after

    def test_snapshot_aligns_prev_close_with_price(self):
        random.seed(2)
        advance_prices()
        snapshot_prev_close()
        prices = get_all_prices()
        for ticker, data in prices.items():
            assert data["prev_close"] == pytest.approx(data["price"])

    def test_floor_clamps_after_huge_negative_shock(self):
        # Force a near-zero price to exercise the floor clamp.
        shared.db.execute(
            "UPDATE stock_prices SET price = ? WHERE ticker = 'DOGE'",
            (PRICE_FLOOR,),
        )
        shared.db.commit()
        random.seed(3)
        for _ in range(10):
            advance_prices()
        assert get_price("DOGE") >= PRICE_FLOOR


class TestHoldings:
    USER_ID = 4242

    def test_buy_creates_holding_with_correct_avg_cost(self):
        buy_shares(self.USER_ID, "GARY", 5, 40.00)
        holding = get_user_holding(self.USER_ID, "GARY")
        assert holding == {"shares": 5, "avg_cost": pytest.approx(40.00)}

    def test_buy_more_blends_avg_cost(self):
        buy_shares(self.USER_ID, "GARY", 10, 40.00)
        buy_shares(self.USER_ID, "GARY", 10, 60.00)
        holding = get_user_holding(self.USER_ID, "GARY")
        assert holding["shares"] == 20
        assert holding["avg_cost"] == pytest.approx(50.00)

    def test_sell_partial_keeps_avg_cost(self):
        buy_shares(self.USER_ID, "GARY", 10, 40.00)
        ok = sell_shares(self.USER_ID, "GARY", 4, 55.00)
        assert ok is True
        holding = get_user_holding(self.USER_ID, "GARY")
        assert holding["shares"] == 6
        assert holding["avg_cost"] == pytest.approx(40.00)

    def test_sell_all_removes_position(self):
        buy_shares(self.USER_ID, "GARY", 3, 40.00)
        sell_shares(self.USER_ID, "GARY", 3, 50.00)
        assert get_user_holding(self.USER_ID, "GARY") is None
        assert get_user_holdings(self.USER_ID) == []

    def test_sell_more_than_owned_fails(self):
        buy_shares(self.USER_ID, "GARY", 2, 40.00)
        ok = sell_shares(self.USER_ID, "GARY", 5, 50.00)
        assert ok is False
        holding = get_user_holding(self.USER_ID, "GARY")
        assert holding["shares"] == 2

    def test_sell_with_no_position_fails(self):
        ok = sell_shares(self.USER_ID, "DOGE", 1, 3.00)
        assert ok is False

    def test_trades_logged(self):
        buy_shares(self.USER_ID, "GARY", 2, 40.00)
        sell_shares(self.USER_ID, "GARY", 1, 50.00)
        rows = shared.db.execute(
            "SELECT action, shares, price FROM stock_trades WHERE user_id = ? ORDER BY id",
            (self.USER_ID,),
        ).fetchall()
        assert rows == [("buy", 2, 40.00), ("sell", 1, 50.00)]


class TestRealizedPL:
    USER_ID = 7777

    def test_realized_pl_recorded_on_sell(self):
        buy_shares(self.USER_ID, "GARY", 10, 40.00)
        sell_shares(self.USER_ID, "GARY", 4, 55.00)
        # 4 sh * (55 - 40) = +60
        assert get_realized_pl(self.USER_ID) == pytest.approx(60.00)
        assert get_realized_pl(self.USER_ID, "GARY") == pytest.approx(60.00)

    def test_realized_pl_negative_when_selling_at_loss(self):
        buy_shares(self.USER_ID, "DOGE", 5, 4.00)
        sell_shares(self.USER_ID, "DOGE", 5, 1.00)
        # 5 * (1 - 4) = -15
        assert get_realized_pl(self.USER_ID) == pytest.approx(-15.00)

    def test_realized_pl_filtered_by_ticker(self):
        buy_shares(self.USER_ID, "GARY", 2, 40.00)
        buy_shares(self.USER_ID, "DOGE", 10, 5.00)
        sell_shares(self.USER_ID, "GARY", 1, 50.00)
        sell_shares(self.USER_ID, "DOGE", 10, 8.00)
        assert get_realized_pl(self.USER_ID, "GARY") == pytest.approx(10.00)
        assert get_realized_pl(self.USER_ID, "DOGE") == pytest.approx(30.00)
        assert get_realized_pl(self.USER_ID) == pytest.approx(40.00)

    def test_realized_pl_zero_when_no_sells(self):
        buy_shares(self.USER_ID, "GARY", 2, 40.00)
        assert get_realized_pl(self.USER_ID) == 0.0


class TestPortfolioValue:
    USER_ID = 8888

    def test_empty_portfolio(self):
        value, cost = get_portfolio_value(self.USER_ID)
        assert value == 0.0
        assert cost == 0.0

    def test_value_uses_current_prices(self):
        buy_shares(self.USER_ID, "GARY", 5, 40.00)
        # Force a known current price for deterministic check.
        shared.db.execute("UPDATE stock_prices SET price = 50.00 WHERE ticker = 'GARY'")
        shared.db.commit()
        value, cost = get_portfolio_value(self.USER_ID)
        assert value == pytest.approx(250.00)
        assert cost == pytest.approx(200.00)


class TestPriceHistory:
    def test_snapshot_dedupes_per_date(self):
        record_price_snapshot("2026-05-01")
        record_price_snapshot("2026-05-01")  # same date should overwrite, not duplicate
        rows = shared.db.execute(
            "SELECT COUNT(*) FROM stock_price_history WHERE ticker = 'GARY'"
        ).fetchone()
        assert rows[0] == 1

    def test_history_returned_in_chronological_order(self):
        for i, date in enumerate(["2026-05-01", "2026-05-02", "2026-05-03"]):
            shared.db.execute(
                "UPDATE stock_prices SET price = ? WHERE ticker = 'GARY'",
                (40.0 + i,),
            )
            shared.db.commit()
            record_price_snapshot(date)
        history = get_price_history("GARY", days=10)
        assert history == [40.0, 41.0, 42.0]

    def test_history_respects_day_limit(self):
        for i in range(10):
            shared.db.execute(
                "UPDATE stock_prices SET price = ? WHERE ticker = 'GARY'",
                (40.0 + i,),
            )
            shared.db.commit()
            record_price_snapshot(f"2026-05-{i + 1:02d}")
        history = get_price_history("GARY", days=3)
        assert history == [47.0, 48.0, 49.0]


class TestSparkline:
    def test_empty_returns_empty(self):
        assert make_sparkline([]) == ""

    def test_single_value_returns_one_block(self):
        result = make_sparkline([5.0])
        assert len(result) == 1
        assert result in SPARK_BLOCKS

    def test_constant_series_renders_uniform(self):
        result = make_sparkline([10.0, 10.0, 10.0])
        assert len(result) == 3
        assert len(set(result)) == 1

    def test_min_and_max_anchor_to_block_edges(self):
        result = make_sparkline([1.0, 2.0, 3.0, 4.0, 5.0])
        assert result[0] == SPARK_BLOCKS[0]
        assert result[-1] == SPARK_BLOCKS[-1]


class TestQuantityResolver:
    def test_plain_integer(self):
        qty, err = StocksCog._resolve_quantity("5", price=10.0)
        assert qty == 5
        assert err is None

    def test_all_requires_position(self):
        qty, err = StocksCog._resolve_quantity("all", price=10.0, max_shares=7)
        assert qty == 7
        assert err is None

    def test_all_without_position_errors(self):
        qty, err = StocksCog._resolve_quantity("all", price=10.0, max_shares=0)
        assert qty is None
        assert "no position" in err.lower()

    def test_dollar_amount_buys_as_many_whole_shares_as_fit(self):
        qty, err = StocksCog._resolve_quantity("$500", price=42.00)
        # floor(500 / 42) = 11
        assert qty == 11
        assert err is None

    def test_dollar_amount_with_commas(self):
        qty, err = StocksCog._resolve_quantity("$1,500", price=100.00)
        assert qty == 15
        assert err is None

    def test_dollar_amount_too_small_errors(self):
        qty, err = StocksCog._resolve_quantity("$5", price=42.00)
        assert qty is None
        assert "0 shares" in err

    def test_negative_quantity_errors(self):
        qty, err = StocksCog._resolve_quantity("-3", price=10.0)
        assert qty is None
        assert err

    def test_garbage_errors(self):
        qty, err = StocksCog._resolve_quantity("banana", price=10.0)
        assert qty is None
        assert err

    def test_all_with_zero_max_shares_gives_neutral_error(self):
        # Used by both buy (max_affordable) and sell (max_position).
        qty, err = StocksCog._resolve_quantity("all", price=10.0, max_shares=0)
        assert qty is None
        # Message should not assume a sell context.
        assert "sell" not in err.lower()


class TestExecuteBuy:
    USER_ID = 9000

    def test_debits_balance_and_adds_holding_atomically(self):
        # Seed a balance so the test isn't dependent on STARTING_BALANCE.
        shared.db.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?)", (self.USER_ID, 1000)
        )
        shared.db.commit()
        new_bal = execute_buy(self.USER_ID, "GARY", 5, 40.00, cost=200)
        assert new_bal == 800
        assert shared.get_balance(self.USER_ID) == 800
        holding = get_user_holding(self.USER_ID, "GARY")
        assert holding == {"shares": 5, "avg_cost": pytest.approx(40.00)}

    def test_records_balance_history_row(self):
        shared.db.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?)", (self.USER_ID, 500)
        )
        shared.db.commit()
        execute_buy(self.USER_ID, "GARY", 1, 40.00, cost=40)
        rows = shared.db.execute(
            "SELECT balance FROM balance_history WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (self.USER_ID,),
        ).fetchone()
        assert rows[0] == 460

    def test_creates_user_row_if_missing(self):
        # No prior INSERT — should auto-create at STARTING_BALANCE then debit.
        new_bal = execute_buy(self.USER_ID, "GARY", 1, 10.00, cost=10)
        assert new_bal == shared.STARTING_BALANCE - 10


class TestExecuteSell:
    USER_ID = 9100

    def _seed_position(self, shares: int, avg_cost: float, balance: int = 0):
        shared.db.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?)", (self.USER_ID, balance)
        )
        shared.db.execute(
            "INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) VALUES (?, ?, ?, ?)",
            (self.USER_ID, "GARY", shares, avg_cost),
        )
        shared.db.commit()

    def test_credits_proceeds_and_returns_realized_pl(self):
        self._seed_position(shares=10, avg_cost=40.00, balance=100)
        result = execute_sell(self.USER_ID, "GARY", 4, price=55.00, proceeds=220)
        assert result is not None
        new_bal, realized = result
        assert new_bal == 320
        assert realized == pytest.approx(60.00)  # 220 - 40 * 4

    def test_realized_pl_uses_int_proceeds_not_float_price(self):
        # Critical: stored realized_pl must equal what the .sell command displays.
        # If sell price * shares would round (e.g. 1.55 * 3 = 4.65 → int(round(4.65)) = 5),
        # the stored realized must use 5, not 4.65.
        self._seed_position(shares=10, avg_cost=1.00, balance=0)
        # price 1.55, qty 3 → proceeds = int(round(4.65)) = 5
        result = execute_sell(self.USER_ID, "GARY", 3, price=1.55, proceeds=5)
        new_bal, realized = result
        # realized = 5 (int proceeds) - 1.00 * 3 = 2.00, NOT 4.65 - 3 = 1.65
        assert realized == pytest.approx(2.00)
        # And get_realized_pl must agree.
        assert get_realized_pl(self.USER_ID, "GARY") == pytest.approx(2.00)

    def test_returns_none_when_position_too_small(self):
        self._seed_position(shares=2, avg_cost=40.00, balance=0)
        assert execute_sell(self.USER_ID, "GARY", 5, price=50.00, proceeds=250) is None
        # Position should be untouched.
        assert get_user_holding(self.USER_ID, "GARY")["shares"] == 2

    def test_full_sell_removes_position(self):
        self._seed_position(shares=3, avg_cost=40.00, balance=0)
        execute_sell(self.USER_ID, "GARY", 3, price=50.00, proceeds=150)
        assert get_user_holding(self.USER_ID, "GARY") is None


class _FailingProxy:
    """Wraps a sqlite3 Connection and raises once on execute() of a matching SQL.
    Delegates everything else (including with-statement semantics) to the real
    connection so the underlying transaction still rolls back on exit."""

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
    """Both helpers wrap the entire trade in a single sqlite3 transaction.
    If anything inside the `with shared.db:` block raises, NO writes persist."""

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
            execute_buy(user_id, "GARY", 5, 40.00, cost=200)
        monkeypatch.setattr(shared, "db", real_db)

        # Balance must NOT have been deducted, and no holding should exist.
        assert shared.get_balance(user_id) == 1000
        assert get_user_holding(user_id, "GARY") is None
        assert shared.db.execute(
            "SELECT COUNT(*) FROM stock_trades WHERE user_id = ?", (user_id,)
        ).fetchone()[0] == 0

    def test_execute_sell_rolls_back_on_exception(self, monkeypatch):
        user_id = 9201
        shared.db.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, 100)
        )
        shared.db.execute(
            "INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) "
            "VALUES (?, ?, ?, ?)",
            (user_id, "GARY", 10, 40.00),
        )
        shared.db.commit()

        real_db = shared.db
        proxy = _FailingProxy(real_db, "INSERT INTO stock_trades")
        monkeypatch.setattr(shared, "db", proxy)
        with pytest.raises(RuntimeError):
            execute_sell(user_id, "GARY", 5, price=50.00, proceeds=250)
        monkeypatch.setattr(shared, "db", real_db)

        # Holding must still be 10 shares; balance must still be 100.
        assert get_user_holding(user_id, "GARY")["shares"] == 10
        assert shared.get_balance(user_id) == 100
