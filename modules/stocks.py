"""Real US stock market integration backed by Yahoo Finance (via yfinance).

Prices are refreshed hourly during NYSE trading hours (9:30–16:00 ET, Mon–Fri).
Trades are allowed 24/7 at the last known close so the bot stays fun on
evenings and weekends. Fractional shares are supported (positions stored as
REAL); coin balances remain integers and trade cost/proceeds are rounded.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

import shared
from shared import (
    PREFIX,
    CENTRAL_TZ,
    COLOR_DEFAULT,
    COLOR_SUCCESS,
    COLOR_ERROR,
    COLOR_GOLD,
    get_balance,
    make_embed,
)

logger = logging.getLogger(__name__)

EASTERN_TZ = ZoneInfo("America/New_York")

# Maps common crypto short-names to their Yahoo Finance symbols so users can
# type `.stocks add BTC` instead of `.stocks add BTC-USD`.
CRYPTO_ALIASES: dict[str, str] = {
    "BTC":   "BTC-USD",
    "ETH":   "ETH-USD",
    "SOL":   "SOL-USD",
    "ADA":   "ADA-USD",
    "DOGE":  "DOGE-USD",
    "XRP":   "XRP-USD",
    "AVAX":  "AVAX-USD",
    "DOT":   "DOT-USD",
    "LINK":  "LINK-USD",
    "LTC":   "LTC-USD",
    "BCH":   "BCH-USD",
    "BNB":   "BNB-USD",
    "SHIB":  "SHIB-USD",
    "UNI":   "UNI-USD",
    "ATOM":  "ATOM-USD",
    "MATIC": "MATIC-USD",
}

# Options paper-trading config.
OPTIONS_LEVERAGE = 10      # 1% price move → 10% return on the bet
OPTIONS_EXPIRY_HOURS = 24

# Curated seed list. Anything users add via `.stocks add SYM` is appended to
# the same `stock_tickers` table — this dict only matters on first boot or
# when the table is empty.
SEED_TICKERS: dict[str, str] = {
    "AAPL":  "Apple Inc.",
    "MSFT":  "Microsoft Corporation",
    "GOOGL": "Alphabet Inc. (Class A)",
    "AMZN":  "Amazon.com, Inc.",
    "NVDA":  "NVIDIA Corporation",
    "META":  "Meta Platforms, Inc.",
    "TSLA":  "Tesla, Inc.",
    "AMD":   "Advanced Micro Devices, Inc.",
    "NFLX":  "Netflix, Inc.",
    "DIS":   "The Walt Disney Company",
    "SPY":   "SPDR S&P 500 ETF",
    "QQQ":   "Invesco QQQ Trust",
}

# Trading session bounds in US Eastern time. yfinance returns the most recent
# close any time we ask, so outside these hours we simply skip the fetch and
# keep the last known price on display.
NYSE_OPEN_HOUR = 9
NYSE_OPEN_MIN = 30
NYSE_CLOSE_HOUR = 16  # 4 PM ET

# A trade has to move at least this many shares AND cost at least 1 coin.
MIN_SHARES = 1e-4

SPARK_BLOCKS = "▁▂▃▄▅▆▇█"
SPARK_DAYS = 7

# In-memory cache of recent daily closes per ticker (populated by the tick).
# {ticker: [close_oldest, ..., close_newest]}
_history_cache: dict[str, list[float]] = {}


# ---------------------------------------------------------------------------
# YFINANCE GLUE — kept thin so tests can monkeypatch `_fetch_quotes` directly
# without needing yfinance installed.
# ---------------------------------------------------------------------------
def _fetch_quotes(symbols: list[str]) -> dict[str, dict]:
    """Blocking fetch — returns {ticker: {price, prev_close, history, name?}}.
    Missing or invalid tickers are simply absent from the result. Network or
    parse errors raise so the caller can log and skip the tick."""
    import yfinance as yf  # imported lazily so tests don't need the package

    if not symbols:
        return {}
    # `period="8d"` gives us 7 daily closes plus the current one; `group_by`
    # keeps the multi-index sane when we pass >1 symbol.
    data = yf.download(
        tickers=" ".join(symbols),
        period="8d",
        interval="1d",
        progress=False,
        auto_adjust=False,
        threads=True,
        group_by="ticker",
    )
    out: dict[str, dict] = {}
    for sym in symbols:
        try:
            if len(symbols) == 1:
                closes = data["Close"].dropna().tolist()
            else:
                closes = data[sym]["Close"].dropna().tolist()
        except (KeyError, AttributeError):
            continue
        if not closes:
            continue
        price = float(closes[-1])
        prev_close = float(closes[-2]) if len(closes) >= 2 else price
        out[sym] = {
            "price": price,
            "prev_close": prev_close,
            "history": [float(c) for c in closes[-SPARK_DAYS:]],
        }
    return out


def _validate_symbol(symbol: str) -> tuple[bool, str | None, float | None]:
    """Blocking yfinance lookup for `.stocks add`. Returns (ok, display_name, last_price).
    last_price comes directly from fast_info so it works after hours too."""
    try:
        import yfinance as yf
    except ImportError:
        return False, None, None
    try:
        t = yf.Ticker(symbol)
        info = getattr(t, "fast_info", None)
        last = info["last_price"] if info else None
        if last is None or float(last) <= 0:
            return False, None, None
        price = float(last)
        name = None
        try:
            name = t.info.get("shortName") or t.info.get("longName")
        except Exception:
            pass
        return True, (name or symbol), price
    except Exception:
        return False, None, None


# ---------------------------------------------------------------------------
# TICKER REGISTRY
# ---------------------------------------------------------------------------
def get_tickers() -> dict[str, str]:
    rows = shared.db.execute("SELECT ticker, name FROM stock_tickers").fetchall()
    return {t: n for t, n in rows}


def add_ticker(symbol: str, name: str, added_by: int | None = None) -> None:
    shared.db.execute(
        "INSERT OR IGNORE INTO stock_tickers (ticker, name, added_by, added_at) "
        "VALUES (?, ?, ?, ?)",
        (symbol, name, added_by, datetime.now(timezone.utc).isoformat()),
    )
    shared.db.commit()


def init_tickers() -> None:
    """Seed the registry with the curated list on first boot (idempotent)."""
    existing = {row[0] for row in shared.db.execute("SELECT ticker FROM stock_tickers")}
    if existing:
        return
    now = datetime.now(timezone.utc).isoformat()
    for sym, name in SEED_TICKERS.items():
        shared.db.execute(
            "INSERT OR IGNORE INTO stock_tickers (ticker, name, added_by, added_at) "
            "VALUES (?, ?, NULL, ?)",
            (sym, name, now),
        )
    shared.db.commit()


# ---------------------------------------------------------------------------
# PRICE STORE
# ---------------------------------------------------------------------------
def get_price(ticker: str) -> float | None:
    row = shared.db.execute(
        "SELECT price FROM stock_prices WHERE ticker = ?", (ticker,)
    ).fetchone()
    return float(row[0]) if row else None


def get_all_prices() -> dict:
    rows = shared.db.execute(
        "SELECT ticker, price, prev_close FROM stock_prices"
    ).fetchall()
    return {
        t: {"price": float(p), "prev_close": float(pc)}
        for t, p, pc in rows
    }


def _upsert_quote(ticker: str, price: float, prev_close: float) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    shared.db.execute(
        "INSERT INTO stock_prices (ticker, price, prev_close, last_updated) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET price = excluded.price, "
        "prev_close = excluded.prev_close, last_updated = excluded.last_updated",
        (ticker, price, prev_close, now_iso),
    )


def refresh_prices(quotes: dict[str, dict]) -> None:
    """Apply a fetched batch into stock_prices and the history cache."""
    if not quotes:
        return
    for ticker, q in quotes.items():
        _upsert_quote(ticker, q["price"], q["prev_close"])
        if q.get("history"):
            _history_cache[ticker] = list(q["history"])
    shared.db.commit()


def get_price_history(ticker: str, days: int = SPARK_DAYS) -> list[float]:
    hist = _history_cache.get(ticker, [])
    return hist[-days:]


# ---------------------------------------------------------------------------
# TRADING HOURS
# ---------------------------------------------------------------------------
def is_market_open(now: datetime | None = None) -> bool:
    """True iff the time is during NYSE regular hours on a weekday.
    Doesn't account for federal market holidays — yfinance simply returns the
    most recent close on those days, which the tick happily re-applies."""
    now = (now or datetime.now(timezone.utc)).astimezone(EASTERN_TZ)
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return False
    open_min = NYSE_OPEN_HOUR * 60 + NYSE_OPEN_MIN
    close_min = NYSE_CLOSE_HOUR * 60
    cur_min = now.hour * 60 + now.minute
    return open_min <= cur_min < close_min


# ---------------------------------------------------------------------------
# HOLDINGS
# ---------------------------------------------------------------------------
def get_user_holdings(user_id: int) -> list[dict]:
    rows = shared.db.execute(
        "SELECT ticker, shares, avg_cost FROM stock_holdings "
        "WHERE user_id = ? AND shares > 0",
        (user_id,),
    ).fetchall()
    return [
        {"ticker": t, "shares": float(s), "avg_cost": float(c)}
        for t, s, c in rows
    ]


def get_user_holding(user_id: int, ticker: str) -> dict | None:
    row = shared.db.execute(
        "SELECT shares, avg_cost FROM stock_holdings "
        "WHERE user_id = ? AND ticker = ?",
        (user_id, ticker),
    ).fetchone()
    if not row or float(row[0]) <= 0:
        return None
    return {"shares": float(row[0]), "avg_cost": float(row[1])}


def buy_shares(user_id: int, ticker: str, shares: float, price: float) -> None:
    """Holdings-only buy. Test-facing helper; production should use execute_buy."""
    cost = shares * price
    existing = get_user_holding(user_id, ticker)
    if existing:
        new_shares = existing["shares"] + shares
        new_avg = (existing["shares"] * existing["avg_cost"] + cost) / new_shares
        shared.db.execute(
            "UPDATE stock_holdings SET shares = ?, avg_cost = ? "
            "WHERE user_id = ? AND ticker = ?",
            (new_shares, new_avg, user_id, ticker),
        )
    else:
        shared.db.execute(
            "INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) "
            "VALUES (?, ?, ?, ?)",
            (user_id, ticker, shares, price),
        )
    shared.db.execute(
        "INSERT INTO stock_trades (user_id, ticker, action, shares, price, timestamp) "
        "VALUES (?, ?, 'buy', ?, ?, ?)",
        (user_id, ticker, shares, price, datetime.now(timezone.utc).isoformat()),
    )
    shared.db.commit()


def sell_shares(user_id: int, ticker: str, shares: float, price: float) -> bool:
    """Holdings-only sell. Test-facing helper; production should use execute_sell."""
    existing = get_user_holding(user_id, ticker)
    if not existing or existing["shares"] + 1e-9 < shares:
        return False
    realized = (price - existing["avg_cost"]) * shares
    new_shares = existing["shares"] - shares
    if new_shares <= MIN_SHARES:
        shared.db.execute(
            "DELETE FROM stock_holdings WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        )
    else:
        shared.db.execute(
            "UPDATE stock_holdings SET shares = ? WHERE user_id = ? AND ticker = ?",
            (new_shares, user_id, ticker),
        )
    shared.db.execute(
        "INSERT INTO stock_trades (user_id, ticker, action, shares, price, timestamp, realized_pl) "
        "VALUES (?, ?, 'sell', ?, ?, ?, ?)",
        (user_id, ticker, shares, price, datetime.now(timezone.utc).isoformat(), realized),
    )
    shared.db.commit()
    return True


def execute_buy(user_id: int, ticker: str, shares: float, price: float, cost: int) -> int:
    """Atomically deduct cost, add shares, log trade and balance history.
    Returns the new balance. All writes happen in a single transaction."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with shared.db:
        row = shared.db.execute(
            "SELECT balance FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            shared.db.execute(
                "INSERT INTO users (user_id, balance) VALUES (?, ?)",
                (user_id, shared.STARTING_BALANCE),
            )
            current_bal = shared.STARTING_BALANCE
        else:
            current_bal = int(row[0])
        new_bal = current_bal - cost
        shared.db.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (new_bal, user_id),
        )
        shared.db.execute(
            "INSERT INTO balance_history (user_id, balance, timestamp) VALUES (?, ?, ?)",
            (user_id, new_bal, now_iso),
        )
        existing = shared.db.execute(
            "SELECT shares, avg_cost FROM stock_holdings WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
        if existing:
            new_shares = float(existing[0]) + shares
            new_avg = (float(existing[0]) * float(existing[1]) + shares * price) / new_shares
            shared.db.execute(
                "UPDATE stock_holdings SET shares = ?, avg_cost = ? "
                "WHERE user_id = ? AND ticker = ?",
                (new_shares, new_avg, user_id, ticker),
            )
        else:
            shared.db.execute(
                "INSERT INTO stock_holdings (user_id, ticker, shares, avg_cost) "
                "VALUES (?, ?, ?, ?)",
                (user_id, ticker, shares, price),
            )
        shared.db.execute(
            "INSERT INTO stock_trades (user_id, ticker, action, shares, price, timestamp) "
            "VALUES (?, ?, 'buy', ?, ?, ?)",
            (user_id, ticker, shares, price, now_iso),
        )
    return new_bal


def execute_sell(
    user_id: int, ticker: str, shares: float, price: float, proceeds: int
) -> tuple[int, float] | None:
    """Atomically remove shares, credit proceeds, log trade with realized P/L
    computed from the SAME `proceeds` value the command displays. Returns
    (new_balance, realized_pl) or None if the position is too small."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with shared.db:
        existing = shared.db.execute(
            "SELECT shares, avg_cost FROM stock_holdings WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
        if existing is None or float(existing[0]) + 1e-9 < shares:
            return None
        avg_cost = float(existing[1])
        realized = float(proceeds) - avg_cost * shares
        new_position = float(existing[0]) - shares
        if new_position <= MIN_SHARES:
            shared.db.execute(
                "DELETE FROM stock_holdings WHERE user_id = ? AND ticker = ?",
                (user_id, ticker),
            )
        else:
            shared.db.execute(
                "UPDATE stock_holdings SET shares = ? WHERE user_id = ? AND ticker = ?",
                (new_position, user_id, ticker),
            )
        row = shared.db.execute(
            "SELECT balance FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            shared.db.execute(
                "INSERT INTO users (user_id, balance) VALUES (?, ?)",
                (user_id, shared.STARTING_BALANCE),
            )
            current_bal = shared.STARTING_BALANCE
        else:
            current_bal = int(row[0])
        new_bal = current_bal + proceeds
        shared.db.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (new_bal, user_id),
        )
        shared.db.execute(
            "INSERT INTO balance_history (user_id, balance, timestamp) VALUES (?, ?, ?)",
            (user_id, new_bal, now_iso),
        )
        shared.db.execute(
            "INSERT INTO stock_trades (user_id, ticker, action, shares, price, timestamp, realized_pl) "
            "VALUES (?, ?, 'sell', ?, ?, ?, ?)",
            (user_id, ticker, shares, price, now_iso, realized),
        )
    return new_bal, realized


def get_realized_pl(user_id: int, ticker: str | None = None) -> float:
    if ticker is None:
        row = shared.db.execute(
            "SELECT COALESCE(SUM(realized_pl), 0) FROM stock_trades "
            "WHERE user_id = ? AND action = 'sell'",
            (user_id,),
        ).fetchone()
    else:
        row = shared.db.execute(
            "SELECT COALESCE(SUM(realized_pl), 0) FROM stock_trades "
            "WHERE user_id = ? AND action = 'sell' AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
    return float(row[0]) if row else 0.0


def get_portfolio_value(user_id: int) -> tuple[float, float]:
    holdings = get_user_holdings(user_id)
    if not holdings:
        return 0.0, 0.0
    prices = get_all_prices()
    value = sum(prices.get(h["ticker"], {}).get("price", 0.0) * h["shares"] for h in holdings)
    cost = sum(h["avg_cost"] * h["shares"] for h in holdings)
    return value, cost


# ---------------------------------------------------------------------------
# OPTIONS
# ---------------------------------------------------------------------------
def open_option(user_id: int, ticker: str, opt_type: str, coins_bet: int, strike: float) -> int:
    """Deduct coins_bet, insert an unsettled option row. Returns the new option id."""
    now_iso = datetime.now(timezone.utc).isoformat()
    expires_iso = (datetime.now(timezone.utc) + timedelta(hours=OPTIONS_EXPIRY_HOURS)).isoformat()
    opt_id: int | None = None
    with shared.db:
        row = shared.db.execute(
            "SELECT balance FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            shared.db.execute(
                "INSERT INTO users (user_id, balance) VALUES (?, ?)",
                (user_id, shared.STARTING_BALANCE),
            )
            current_bal = shared.STARTING_BALANCE
        else:
            current_bal = int(row[0])
        shared.db.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (current_bal - coins_bet, user_id),
        )
        cursor = shared.db.execute(
            "INSERT INTO options "
            "(user_id, ticker, option_type, coins_bet, strike_price, opened_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, ticker, opt_type, coins_bet, strike, now_iso, expires_iso),
        )
        opt_id = cursor.lastrowid
    return opt_id


def get_open_options(user_id: int | None = None) -> list[dict]:
    """Return all unsettled options, optionally filtered to a single user."""
    if user_id is not None:
        rows = shared.db.execute(
            "SELECT id, user_id, ticker, option_type, coins_bet, strike_price, opened_at, expires_at "
            "FROM options WHERE settled = 0 AND user_id = ? ORDER BY expires_at",
            (user_id,),
        ).fetchall()
    else:
        rows = shared.db.execute(
            "SELECT id, user_id, ticker, option_type, coins_bet, strike_price, opened_at, expires_at "
            "FROM options WHERE settled = 0 ORDER BY expires_at",
        ).fetchall()
    return [
        {
            "id": int(r[0]), "user_id": int(r[1]), "ticker": r[2],
            "option_type": r[3], "coins_bet": int(r[4]),
            "strike_price": float(r[5]), "opened_at": r[6], "expires_at": r[7],
        }
        for r in rows
    ]


def settle_option(
    opt_id: int, user_id: int, coins_bet: int, strike: float, exit_price: float, opt_type: str
) -> tuple[int, int]:
    """Mark option settled, credit payout to user. Returns (pnl, payout).
    pnl is negative (−coins_bet) on a loss; positive on a win."""
    pct_change = abs(exit_price - strike) / strike if strike > 0 else 0.0
    won = (exit_price > strike) if opt_type == "call" else (exit_price < strike)
    if won:
        multiplier = max(1.1, 1.0 + pct_change * OPTIONS_LEVERAGE)
        payout = int(round(coins_bet * multiplier))
        pnl = payout - coins_bet
    else:
        payout = 0
        pnl = -coins_bet

    now_iso = datetime.now(timezone.utc).isoformat()
    with shared.db:
        if payout > 0:
            shared.db.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (payout, user_id),
            )
        shared.db.execute(
            "UPDATE options SET settled = 1, exit_price = ?, pnl = ?, settled_at = ? WHERE id = ?",
            (exit_price, pnl, now_iso, opt_id),
        )
    return pnl, payout


# ---------------------------------------------------------------------------
# DISPLAY HELPERS
# ---------------------------------------------------------------------------
def make_sparkline(values: list[float]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return SPARK_BLOCKS[len(SPARK_BLOCKS) // 2]
    lo, hi = min(values), max(values)
    if hi == lo:
        return SPARK_BLOCKS[len(SPARK_BLOCKS) // 2] * len(values)
    span = hi - lo
    last = len(SPARK_BLOCKS) - 1
    return "".join(SPARK_BLOCKS[int((v - lo) / span * last)] for v in values)


def _fmt_shares(s: float) -> str:
    """Format share counts: whole numbers stay whole; fractions show up to 4 dp."""
    if abs(s - round(s)) < 1e-6:
        return f"{int(round(s))}"
    return f"{s:.4f}".rstrip("0").rstrip(".")


def build_market_lines() -> list[str]:
    tickers = get_tickers()
    prices = get_all_prices()
    rows = []
    for ticker in tickers:
        data = prices.get(ticker)
        if not data:
            continue
        prev = data["prev_close"] or data["price"]
        pct = (data["price"] - prev) / prev * 100 if prev else 0.0
        rows.append((ticker, data["price"], pct))
    rows.sort(key=lambda r: -r[2])
    lines = []
    for ticker, price, pct in rows:
        arrow = "🟢" if pct >= 0 else "🔴"
        name = tickers.get(ticker, "")
        lines.append(
            f"`{ticker:<5}` **${price:>10,.2f}**  {arrow} `{pct:+6.2f}%`  _{name}_"
        )
    return lines


# ---------------------------------------------------------------------------
# COG
# ---------------------------------------------------------------------------
class StocksCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        init_tickers()

    @commands.Cog.listener("on_ready")
    async def _start_stock_tasks(self):
        if not self.stock_tick_check.is_running():
            self.stock_tick_check.start()
        if not self.options_settle_check.is_running():
            self.options_settle_check.start()

    @tasks.loop(minutes=5)
    async def stock_tick_check(self):
        """During NYSE hours, refresh quotes hourly and post a morning embed
        on the first tick of each trading day. Outside market hours we do
        nothing — last close stays on display."""
        now_central = datetime.now(CENTRAL_TZ)
        if not is_market_open():
            return

        today_key = now_central.strftime("%Y-%m-%d")
        hour_key = now_central.strftime("%Y-%m-%d %H")
        last_tick_key = shared.runtime_settings.get("ticker_last_tick_key")
        last_morning = shared.runtime_settings.get("ticker_last_morning_date")

        if last_tick_key == hour_key:
            return

        await self._refresh_all()

        if last_morning != today_key:
            await self._post_morning_announcement()
            shared.runtime_settings["ticker_last_morning_date"] = today_key
            shared._save_json_setting("ticker_last_morning_date", today_key)

        shared.runtime_settings["ticker_last_tick_key"] = hour_key
        shared._save_json_setting("ticker_last_tick_key", hour_key)

    @stock_tick_check.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()
        # Best-effort first fetch so prices are populated even before the
        # first scheduled tick fires (or if the bot only ever runs after-hours).
        try:
            await self._refresh_all()
        except Exception as e:
            logger.warning("Initial stock price fetch failed: %s", e)

    async def _refresh_all(self):
        symbols = list(get_tickers().keys())
        if not symbols:
            return
        try:
            quotes = await asyncio.to_thread(_fetch_quotes, symbols)
        except Exception as e:
            logger.warning("yfinance fetch failed: %s", e)
            return
        refresh_prices(quotes)

    async def _post_morning_announcement(self):
        channel_id = shared.runtime_settings.get("ticker_channel_id")
        if not channel_id:
            return
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return
        if channel is None:
            return
        lines = build_market_lines()
        if not lines:
            return
        embed = discord.Embed(
            title="📈 Daily Market Open",
            description="\n".join(lines),
            color=COLOR_GOLD,
        )
        embed.set_footer(text=f"Use {PREFIX}buy / {PREFIX}sell / {PREFIX}portfolio to trade")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ---------------------------------------------------------------------------
    # COMMANDS
    # ---------------------------------------------------------------------------
    @commands.command(aliases=["stox", "market", "ticker"])
    async def stocks(self, ctx, *args):
        """`.stocks` lists prices; `.stocks <TICKER>` shows detail;
        `.stocks add <TICKER>` registers a new ticker; `.stocks remove <TICKER>` deletes one (admin)."""
        if not args:
            return await self._stock_overview(ctx)
        first = args[0].lower()
        if first == "add":
            if len(args) < 2:
                return await ctx.send(f"Usage: `{PREFIX}stocks add <TICKER>`")
            return await self._stock_add(ctx, args[1].upper())
        if first in ("remove", "delete", "del", "rm"):
            if len(args) < 2:
                return await ctx.send(f"Usage: `{PREFIX}stocks remove <TICKER>`")
            return await self._stock_remove(ctx, args[1].upper())
        if first in ("list", "all"):
            return await self._stock_overview(ctx)
        return await self._stock_detail(ctx, args[0].upper())

    async def _stock_overview(self, ctx):
        lines = build_market_lines()
        if not lines:
            return await ctx.send(embed=make_embed(
                "Market Unavailable",
                "No prices loaded yet — try again in a minute.",
                COLOR_ERROR,
            ))
        embed = discord.Embed(
            title="📊 US Stock Market",
            description="\n".join(lines),
            color=COLOR_DEFAULT,
        )
        market_state = "🟢 Open" if is_market_open() else "🔴 Closed (showing last close)"
        embed.set_footer(
            text=(
                f"{market_state} · {PREFIX}stocks <TICKER> for detail · "
                f"{PREFIX}buy / {PREFIX}sell / {PREFIX}portfolio"
            )
        )
        await ctx.send(embed=embed)

    async def _stock_add(self, ctx, ticker: str):
        # Remap common crypto short-names (e.g. BTC → BTC-USD) so users don't
        # accidentally add a random small-cap stock with the same ticker.
        remapped = CRYPTO_ALIASES.get(ticker)
        remap_note = f" (mapped `{ticker}` → `{remapped}` for crypto)" if remapped else ""
        if remapped:
            ticker = remapped

        tickers = get_tickers()
        if ticker in tickers:
            return await ctx.send(embed=make_embed(
                "Already Listed",
                f"`{ticker}` is already tradable.",
                COLOR_ERROR,
            ))
        async with ctx.typing():
            ok, name, bootstrap_price = await asyncio.to_thread(_validate_symbol, ticker)
        if not ok:
            return await ctx.send(embed=make_embed(
                "Unknown Symbol",
                f"`{ticker}` doesn't look like a valid ticker on Yahoo Finance.",
                COLOR_ERROR,
            ))
        add_ticker(ticker, name or ticker, added_by=ctx.author.id)
        # Bootstrap with the validation price immediately so the ticker appears
        # in .stocks right away — even after hours or if the history fetch fails.
        if bootstrap_price and bootstrap_price > 0:
            _upsert_quote(ticker, bootstrap_price, bootstrap_price)
            shared.db.commit()
        # Then try a full 7-day history fetch for the sparkline.
        try:
            quotes = await asyncio.to_thread(_fetch_quotes, [ticker])
            refresh_prices(quotes)
        except Exception as e:
            logger.warning("Price history fetch failed for %s after add: %s", ticker, e)
        await ctx.send(embed=make_embed(
            "🟢 Ticker Added",
            f"`{ticker}` ({name}) is now tradable.{remap_note}\nUse `{PREFIX}stocks {ticker}` to view.",
            COLOR_SUCCESS,
        ))

    async def _stock_remove(self, ctx, ticker: str):
        if ctx.author.id != shared.ADMIN_ID:
            return await ctx.send(embed=make_embed(
                "Permission Denied", "Only admins can remove tickers.", COLOR_ERROR
            ))
        tickers = get_tickers()
        if ticker not in tickers:
            return await ctx.send(embed=make_embed(
                "Not Listed", f"`{ticker}` isn't in the ticker list.", COLOR_ERROR
            ))
        # Warn if anyone has an open position in this ticker.
        holders = shared.db.execute(
            "SELECT COUNT(*) FROM stock_holdings WHERE ticker = ? AND shares > 0", (ticker,)
        ).fetchone()[0]
        open_opts = shared.db.execute(
            "SELECT COUNT(*) FROM options WHERE ticker = ? AND settled = 0", (ticker,)
        ).fetchone()[0]
        warnings = []
        if holders:
            warnings.append(f"⚠️ {holders} user(s) still hold shares of `{ticker}`")
        if open_opts:
            warnings.append(f"⚠️ {open_opts} open option(s) on `{ticker}`")

        shared.db.execute("DELETE FROM stock_tickers WHERE ticker = ?", (ticker,))
        shared.db.execute("DELETE FROM stock_prices  WHERE ticker = ?", (ticker,))
        _history_cache.pop(ticker, None)
        shared.db.commit()

        note = "\n" + "\n".join(warnings) if warnings else ""
        await ctx.send(embed=make_embed(
            "🗑️ Ticker Removed",
            f"`{ticker}` has been removed from the market.{note}",
            COLOR_WARNING,
        ))

    async def _stock_detail(self, ctx, ticker: str):
        tickers = get_tickers()
        if ticker not in tickers:
            return await ctx.send(embed=make_embed(
                "Unknown Ticker",
                f"`{ticker}` isn't listed. Use `{PREFIX}stocks` to see tickers "
                f"or `{PREFIX}stocks add {ticker}` to add it.",
                COLOR_ERROR,
            ))
        prices = get_all_prices()
        data = prices.get(ticker, {})
        if not data:
            return await ctx.send(embed=make_embed(
                "No Price Yet",
                f"No quote for `{ticker}` yet — try again in a minute.",
                COLOR_ERROR,
            ))
        price = data.get("price", 0.0)
        prev = data.get("prev_close", price) or price
        pct = (price - prev) / prev * 100 if prev else 0.0
        arrow = "🟢" if pct >= 0 else "🔴"

        history = get_price_history(ticker, SPARK_DAYS)
        if history:
            spark_lo, spark_hi = min(history), max(history)
            spark = make_sparkline(history)
            spark_line = f"`{spark}`  ${spark_lo:,.2f} → ${spark_hi:,.2f} ({len(history)}d)"
        else:
            spark_line = "_(history loading — appears after the next tick)_"

        lines = [
            f"**{tickers[ticker]}**",
            f"Price: **${price:,.2f}**  {arrow} `{pct:+.2f}%` vs prev close",
            f"7-day: {spark_line}",
        ]

        holding = get_user_holding(ctx.author.id, ticker)
        if holding:
            value = price * holding["shares"]
            cost_basis = holding["avg_cost"] * holding["shares"]
            pl = value - cost_basis
            pl_pct = (pl / cost_basis * 100) if cost_basis else 0.0
            pl_arrow = "🟢" if pl >= 0 else "🔴"
            lines.append("")
            lines.append(
                f"**Your position:** {_fmt_shares(holding['shares'])} sh @ "
                f"${holding['avg_cost']:.2f} → ${value:,.2f} {pl_arrow} "
                f"`{pl:+,.2f} ({pl_pct:+.1f}%)`"
            )
            realized = get_realized_pl(ctx.author.id, ticker)
            if realized != 0:
                r_arrow = "🟢" if realized >= 0 else "🔴"
                lines.append(f"Realized P/L on {ticker}: {r_arrow} `{realized:+,.2f}`")

        embed = make_embed(f"📈 {ticker}", "\n".join(lines), COLOR_DEFAULT)
        embed.set_footer(text=f"{PREFIX}buy {ticker} <qty|all|$coins>  ·  {PREFIX}sell {ticker} <qty|all|$coins>")
        await ctx.send(embed=embed)

    @staticmethod
    def _resolve_quantity(
        arg: str, price: float, max_shares: float | None = None
    ) -> tuple[float | None, str | None]:
        """Parse a 'qty', 'all', or '$coins' argument into a fractional share count.
        For buy, the caller passes max_shares = max affordable share count
        given the user's balance; for sell, it's the user's position size.
        Returns (shares, error_message). Shares may be fractional."""
        if arg is None:
            return None, "Missing quantity."
        arg = arg.strip().lower()
        if arg == "all":
            if max_shares is None or max_shares <= MIN_SHARES:
                return None, "Nothing to apply `all` to (no position or no coins)."
            return float(max_shares), None
        if arg.startswith("$"):
            try:
                coins = int(arg[1:].replace(",", ""))
            except ValueError:
                return None, "Invalid coin amount. Example: `$500`."
            if coins <= 0 or price <= 0:
                return None, "Coin amount must be positive."
            qty = coins / price
            if qty < MIN_SHARES:
                return None, f"${coins:,} buys too little at ${price:,.2f}."
            return qty, None
        try:
            qty = float(arg)
        except ValueError:
            return None, "Quantity must be a number, `all`, or `$<coins>`."
        if qty < MIN_SHARES:
            return None, f"Quantity must be at least {MIN_SHARES}."
        return qty, None

    @commands.command()
    async def buy(self, ctx, ticker: str = None, shares: str = None):
        """Buy shares (fractional allowed). `qty` accepts a number, `all`, or `$<coins>`."""
        if ticker is None or shares is None:
            return await ctx.send(f"Usage: `{PREFIX}buy <TICKER> <qty|all|$coins>`")
        ticker = ticker.upper()
        if ticker not in get_tickers():
            return await ctx.send(embed=make_embed(
                "Unknown Ticker",
                f"`{ticker}` isn't listed. Use `{PREFIX}stocks add {ticker}` to add it.",
                COLOR_ERROR,
            ))
        price = get_price(ticker)
        if price is None or price <= 0:
            return await ctx.send(embed=make_embed("No Price", "Price unavailable.", COLOR_ERROR))
        bal = get_balance(ctx.author.id)
        max_affordable = (bal / price) if price > 0 else 0.0
        qty, err = self._resolve_quantity(shares, price, max_shares=max_affordable)
        if err:
            return await ctx.send(embed=make_embed("Invalid", err, COLOR_ERROR))
        cost = max(1, int(round(price * qty)))
        if cost > bal:
            return await ctx.send(embed=make_embed(
                "❌ Broke",
                f"That'd cost **{cost:,}** coins; you only have **{bal:,}**.",
                COLOR_ERROR,
            ))
        new_bal = execute_buy(ctx.author.id, ticker, qty, price, cost)
        await ctx.send(embed=make_embed(
            "🟢 Buy Filled",
            f"Bought **{_fmt_shares(qty)}** sh of `{ticker}` @ **${price:,.2f}** for **{cost:,}** coins.\n"
            f"Balance: **{new_bal:,}**",
            COLOR_SUCCESS,
        ))

    @commands.command()
    async def sell(self, ctx, ticker: str = None, shares: str = None):
        """Sell shares (fractional allowed). `qty` accepts a number, `all`, or `$<coins>` worth."""
        if ticker is None or shares is None:
            return await ctx.send(f"Usage: `{PREFIX}sell <TICKER> <qty|all|$coins>`")
        ticker = ticker.upper()
        if ticker not in get_tickers():
            return await ctx.send(embed=make_embed(
                "Unknown Ticker", f"`{ticker}` isn't listed.", COLOR_ERROR,
            ))
        existing = get_user_holding(ctx.author.id, ticker)
        if not existing:
            return await ctx.send(embed=make_embed(
                "No Position", f"You don't own any `{ticker}`.", COLOR_ERROR,
            ))
        price = get_price(ticker)
        if price is None or price <= 0:
            return await ctx.send(embed=make_embed("No Price", "Price unavailable.", COLOR_ERROR))
        qty, err = self._resolve_quantity(shares, price, max_shares=existing["shares"])
        if err:
            return await ctx.send(embed=make_embed("Invalid", err, COLOR_ERROR))
        if qty > existing["shares"] + 1e-9:
            return await ctx.send(embed=make_embed(
                "Invalid",
                f"You only have **{_fmt_shares(existing['shares'])}** sh of `{ticker}`.",
                COLOR_ERROR,
            ))
        proceeds = max(1, int(round(price * qty)))
        result = execute_sell(ctx.author.id, ticker, qty, price, proceeds)
        if result is None:
            return await ctx.send(embed=make_embed(
                "Position Changed",
                "Your position changed before the order could fill — try again.",
                COLOR_ERROR,
            ))
        new_bal, realized = result
        pl_str = f"+{realized:,.2f}" if realized >= 0 else f"{realized:,.2f}"
        await ctx.send(embed=make_embed(
            "🔴 Sell Filled",
            f"Sold **{_fmt_shares(qty)}** sh of `{ticker}` @ **${price:,.2f}** for **{proceeds:,}** coins.\n"
            f"P/L vs cost: **{pl_str}** coins\nBalance: **{new_bal:,}**",
            COLOR_SUCCESS,
        ))

    @commands.command(aliases=["port"])
    async def portfolio(self, ctx, member: discord.Member = None):
        """Show stock holdings and unrealized P/L."""
        target = member or ctx.author
        holdings = get_user_holdings(target.id)
        if not holdings:
            who = "You have" if target.id == ctx.author.id else f"{target.display_name} has"
            return await ctx.send(
                f"{who} no holdings. Use `{PREFIX}buy <TICKER> <qty|all|$coins>` to start."
            )
        prices = get_all_prices()
        lines = []
        total_value = 0.0
        total_cost = 0.0
        for h in holdings:
            cur_price = prices.get(h["ticker"], {}).get("price", 0.0)
            value = cur_price * h["shares"]
            cost_basis = h["avg_cost"] * h["shares"]
            pl = value - cost_basis
            pl_pct = (pl / cost_basis * 100) if cost_basis else 0.0
            arrow = "🟢" if pl >= 0 else "🔴"
            total_value += value
            total_cost += cost_basis
            lines.append(
                f"`{h['ticker']:<5}` {_fmt_shares(h['shares']):>8} sh @ ${h['avg_cost']:.2f} → "
                f"${cur_price:,.2f} = **${value:,.2f}** {arrow} `{pl:+,.2f} ({pl_pct:+.1f}%)`"
            )
        net = total_value - total_cost
        net_arrow = "🟢" if net >= 0 else "🔴"
        net_pct = (net / total_cost * 100) if total_cost else 0.0
        lines.append("")
        lines.append(
            f"**Portfolio value:** ${total_value:,.2f}  (cost ${total_cost:,.2f})"
        )
        lines.append(
            f"**Unrealized P/L:** {net_arrow} `{net:+,.2f} ({net_pct:+.1f}%)`"
        )
        await ctx.send(embed=make_embed(
            f"📊 {target.display_name}'s Portfolio",
            "\n".join(lines),
            COLOR_DEFAULT,
        ))


    # ---------------------------------------------------------------------------
    # OPTIONS COMMANDS
    # ---------------------------------------------------------------------------

    @commands.command(name="call", aliases=["callopt"])
    async def call_option(self, ctx, ticker: str = None, coins: int = None):
        """Buy a call — win coins if the price goes UP in 24h (10× leverage paper trade)."""
        await self._open_option(ctx, "call", ticker, coins)

    @commands.command(name="put", aliases=["putopt"])
    async def put_option(self, ctx, ticker: str = None, coins: int = None):
        """Buy a put — win coins if the price goes DOWN in 24h (10× leverage paper trade)."""
        await self._open_option(ctx, "put", ticker, coins)

    async def _open_option(self, ctx, opt_type: str, ticker: str | None, coins: int | None):
        cmd = "call" if opt_type == "call" else "put"
        if ticker is None or coins is None:
            return await ctx.send(f"Usage: `{PREFIX}{cmd} <TICKER> <coins>`")
        ticker = CRYPTO_ALIASES.get(ticker.upper(), ticker.upper())
        if ticker not in get_tickers():
            return await ctx.send(embed=make_embed(
                "Unknown Ticker",
                f"`{ticker}` isn't listed. Use `{PREFIX}stocks add {ticker}` to add it first.",
                COLOR_ERROR,
            ))
        if coins <= 0:
            return await ctx.send(embed=make_embed("Invalid", "Bet must be positive.", COLOR_ERROR))
        bal = get_balance(ctx.author.id)
        if coins > bal:
            return await ctx.send(embed=make_embed(
                "❌ Broke",
                f"That'd cost **{coins:,}** coins; you only have **{bal:,}**.",
                COLOR_ERROR,
            ))
        price = get_price(ticker)
        if price is None or price <= 0:
            return await ctx.send(embed=make_embed(
                "No Price", "Price unavailable right now — try again in a minute.", COLOR_ERROR
            ))

        expiry_dt = datetime.now(timezone.utc) + timedelta(hours=OPTIONS_EXPIRY_HOURS)
        open_option(ctx.author.id, ticker, opt_type, coins, price)

        direction = "📈 **UP**" if opt_type == "call" else "📉 **DOWN**"
        type_label = "CALL" if opt_type == "call" else "PUT"
        icon = "📈" if opt_type == "call" else "📉"
        await ctx.send(embed=make_embed(
            f"{icon} {type_label} Opened — `{ticker}`",
            f"You bet **{coins:,}** coins that `{ticker}` goes {direction}.\n"
            f"Strike: **${price:,.2f}** | Expires: <t:{int(expiry_dt.timestamp())}:R>\n\n"
            f"**Win:** bet × max(1.1, 1 + |move%| × {OPTIONS_LEVERAGE}×)\n"
            f"**Lose:** forfeit entire bet\n\n"
            f"Use `{PREFIX}options` to track your positions.",
            COLOR_SUCCESS,
        ))

    @commands.command(name="options", aliases=["opts", "positions", "pos"])
    async def options_cmd(self, ctx, member: discord.Member = None):
        """Show open call/put positions with live estimated P/L."""
        target = member or ctx.author
        opts = get_open_options(target.id)
        if not opts:
            who = "You have" if target.id == ctx.author.id else f"{target.display_name} has"
            return await ctx.send(
                f"{who} no open options. Use `{PREFIX}call` or `{PREFIX}put` to open one."
            )
        prices = get_all_prices()
        lines = []
        for o in opts:
            current = prices.get(o["ticker"], {}).get("price", 0.0)
            type_label = "📈 CALL" if o["option_type"] == "call" else "📉 PUT"
            pct = (current - o["strike_price"]) / o["strike_price"] * 100 if o["strike_price"] else 0.0
            in_money = (current > o["strike_price"]) if o["option_type"] == "call" else (current < o["strike_price"])
            if in_money:
                multiplier = max(1.1, 1.0 + abs(pct) / 100 * OPTIONS_LEVERAGE)
                est_payout = int(round(o["coins_bet"] * multiplier))
                est_pnl = est_payout - o["coins_bet"]
                est_str = f"🟢 +{est_pnl:,} (×{multiplier:.2f})"
            else:
                est_str = f"🔴 −{o['coins_bet']:,} (out of the money)"
            expires_at = datetime.fromisoformat(o["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            lines.append(
                f"{type_label} `{o['ticker']}` — bet **{o['coins_bet']:,}** @ ${o['strike_price']:,.2f}\n"
                f"  Now: ${current:,.2f} ({pct:+.2f}%) | Est. P/L: {est_str}\n"
                f"  Expires: <t:{int(expires_at.timestamp())}:R>"
            )
        embed = make_embed(
            f"📋 {target.display_name}'s Options",
            "\n\n".join(lines),
            COLOR_DEFAULT,
        )
        embed.set_footer(
            text=f"Auto-settles after {OPTIONS_EXPIRY_HOURS}h · {PREFIX}call / {PREFIX}put to open"
        )
        await ctx.send(embed=embed)

    # ---------------------------------------------------------------------------
    # OPTIONS SETTLEMENT TASK
    # ---------------------------------------------------------------------------

    @tasks.loop(minutes=10)
    async def options_settle_check(self):
        """Settle expired options and post results to the ticker channel."""
        now = datetime.now(timezone.utc)
        rows = shared.db.execute(
            "SELECT id, user_id, ticker, option_type, coins_bet, strike_price "
            "FROM options WHERE settled = 0 AND expires_at <= ?",
            (now.isoformat(),),
        ).fetchall()
        if not rows:
            return

        prices = get_all_prices()
        channel_id = shared.runtime_settings.get("ticker_channel_id")
        channel = None
        if channel_id:
            try:
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                pass

        for opt_id, user_id, ticker, opt_type, coins_bet, strike in rows:
            exit_price = prices.get(ticker, {}).get("price", 0.0)
            if exit_price <= 0:
                continue  # No price yet — retry next tick

            pnl, payout = settle_option(
                int(opt_id), int(user_id), int(coins_bet), float(strike), exit_price, opt_type
            )

            if channel is not None:
                type_label = "📈 CALL" if opt_type == "call" else "📉 PUT"
                if pnl >= 0:
                    result_str = f"🟢 Won **+{pnl:,}** coins (paid out {payout:,})"
                else:
                    result_str = f"🔴 Lost **{abs(pnl):,}** coins (forfeited bet)"
                try:
                    member = self.bot.get_user(user_id)
                    user_str = member.mention if member else f"<@{user_id}>"
                    await channel.send(embed=make_embed(
                        f"Options Settled — {type_label} `{ticker}`",
                        f"{user_str} | Strike: ${float(strike):,.2f} → Exit: ${exit_price:,.2f}\n"
                        f"{result_str}",
                        shared.COLOR_SUCCESS if pnl >= 0 else shared.COLOR_ERROR,
                    ))
                except discord.HTTPException:
                    pass

    @options_settle_check.before_loop
    async def _wait_until_ready_opts(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(StocksCog(bot))
