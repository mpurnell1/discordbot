import random
from datetime import datetime, timezone

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

# Curated friend-group ticker set.
# drift / vol are per-tick; ticks fire hourly between TRADING_HOUR_START and
# TRADING_HOUR_END Central (16 ticks/day). drift/vol are scaled so the daily
# variance is roughly the same as a 2-tick/day model would produce.
TICKERS = {
    "GARY": {"name": "Gary Holdings, Inc.",  "base": 42.00, "drift":  0.00005,  "vol": 0.0065},
    "SILS": {"name": "Silas Systems",         "base": 11.00, "drift": -0.000025, "vol": 0.016},
    "COIN": {"name": "CoinFlip Index Fund",   "base": 98.00, "drift":  0.00004,  "vol": 0.0032},
    "DOGE": {"name": "Dogecoin Lite",         "base":  3.00, "drift":  0.0,      "vol": 0.035},
    "WORD": {"name": "Wordle Worldwide",      "base": 25.00, "drift":  0.00004,  "vol": 0.009},
    "DEAD": {"name": "DeadChat Media Group",  "base": 50.00, "drift": -0.00009,  "vol": 0.0125},
}

TRADING_HOUR_START = 8   # 8 AM Central — first tick of the day, also the announcement
TRADING_HOUR_END = 23    # 11 PM Central — last tick of the day (inclusive)

PRICE_FLOOR = 0.50
PRICE_CEILING = 100_000.00

SPARK_BLOCKS = "▁▂▃▄▅▆▇█"
SPARK_DAYS = 7


def init_stock_prices() -> None:
    for ticker, cfg in TICKERS.items():
        row = shared.db.execute(
            "SELECT 1 FROM stock_prices WHERE ticker = ?", (ticker,)
        ).fetchone()
        if not row:
            now_iso = datetime.now(timezone.utc).isoformat()
            shared.db.execute(
                "INSERT INTO stock_prices (ticker, price, prev_close, last_updated) "
                "VALUES (?, ?, ?, ?)",
                (ticker, cfg["base"], cfg["base"], now_iso),
            )
    shared.db.commit()


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


def advance_prices() -> None:
    """Move every ticker by drift + a Gaussian shock. Bounded by floor/ceiling."""
    now_iso = datetime.now(timezone.utc).isoformat()
    for ticker, cfg in TICKERS.items():
        row = shared.db.execute(
            "SELECT price FROM stock_prices WHERE ticker = ?", (ticker,)
        ).fetchone()
        if not row:
            continue
        current = float(row[0])
        shock = random.gauss(0, cfg["vol"])
        new_price = current * (1 + cfg["drift"] + shock)
        new_price = max(PRICE_FLOOR, min(PRICE_CEILING, new_price))
        shared.db.execute(
            "UPDATE stock_prices SET price = ?, last_updated = ? WHERE ticker = ?",
            (new_price, now_iso, ticker),
        )
    shared.db.commit()


def snapshot_prev_close() -> None:
    """Set prev_close = current price for every ticker. Called after morning announcement."""
    shared.db.execute("UPDATE stock_prices SET prev_close = price")
    shared.db.commit()


def get_user_holdings(user_id: int) -> list[dict]:
    rows = shared.db.execute(
        "SELECT ticker, shares, avg_cost FROM stock_holdings "
        "WHERE user_id = ? AND shares > 0",
        (user_id,),
    ).fetchall()
    return [
        {"ticker": t, "shares": int(s), "avg_cost": float(c)}
        for t, s, c in rows
    ]


def get_user_holding(user_id: int, ticker: str) -> dict | None:
    row = shared.db.execute(
        "SELECT shares, avg_cost FROM stock_holdings "
        "WHERE user_id = ? AND ticker = ?",
        (user_id, ticker),
    ).fetchone()
    if not row or int(row[0]) == 0:
        return None
    return {"shares": int(row[0]), "avg_cost": float(row[1])}


def buy_shares(user_id: int, ticker: str, shares: int, price: float) -> None:
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


def sell_shares(user_id: int, ticker: str, shares: int, price: float) -> bool:
    """Holdings-only sell. Test-facing helper; production should use execute_sell."""
    existing = get_user_holding(user_id, ticker)
    if not existing or existing["shares"] < shares:
        return False
    realized = (price - existing["avg_cost"]) * shares
    new_shares = existing["shares"] - shares
    if new_shares == 0:
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


def execute_buy(user_id: int, ticker: str, shares: int, price: float, cost: int) -> int:
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
            new_shares = int(existing[0]) + shares
            new_avg = (int(existing[0]) * float(existing[1]) + shares * price) / new_shares
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
    user_id: int, ticker: str, shares: int, price: float, proceeds: int
) -> tuple[int, float] | None:
    """Atomically remove shares, credit proceeds, log trade with realized P/L
    computed from the SAME `proceeds` value the command displays (so stored
    realized_pl never drifts from the per-sell P/L line). Returns
    (new_balance, realized_pl) or None if the position is too small."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with shared.db:
        existing = shared.db.execute(
            "SELECT shares, avg_cost FROM stock_holdings WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
        if existing is None or int(existing[0]) < shares:
            return None
        avg_cost = float(existing[1])
        realized = float(proceeds) - avg_cost * shares
        new_position = int(existing[0]) - shares
        if new_position == 0:
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
    """Sum lifetime realized P/L from stock sales for a user (optionally filtered by ticker)."""
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
    """Return (current_market_value, total_cost_basis) of a user's holdings."""
    holdings = get_user_holdings(user_id)
    if not holdings:
        return 0.0, 0.0
    prices = get_all_prices()
    value = sum(prices.get(h["ticker"], {}).get("price", 0.0) * h["shares"] for h in holdings)
    cost = sum(h["avg_cost"] * h["shares"] for h in holdings)
    return value, cost


def record_price_snapshot(date_key: str | None = None) -> None:
    """Persist one daily close per ticker for sparkline history."""
    if date_key is None:
        date_key = datetime.now(CENTRAL_TZ).strftime("%Y-%m-%d")
    for ticker in TICKERS:
        price = get_price(ticker)
        if price is None:
            continue
        shared.db.execute(
            "INSERT INTO stock_price_history (ticker, date, price) VALUES (?, ?, ?) "
            "ON CONFLICT(ticker, date) DO UPDATE SET price = excluded.price",
            (ticker, date_key, price),
        )
    shared.db.commit()


def get_price_history(ticker: str, days: int = SPARK_DAYS) -> list[float]:
    rows = shared.db.execute(
        "SELECT price FROM stock_price_history WHERE ticker = ? "
        "ORDER BY date DESC LIMIT ?",
        (ticker, days),
    ).fetchall()
    return [float(r[0]) for r in reversed(rows)]


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


def build_market_lines() -> list[str]:
    prices = get_all_prices()
    rows = []
    for ticker, data in prices.items():
        prev = data["prev_close"] or data["price"]
        pct = (data["price"] - prev) / prev * 100 if prev else 0.0
        rows.append((ticker, data["price"], pct))
    rows.sort(key=lambda r: -r[2])
    lines = []
    for ticker, price, pct in rows:
        arrow = "🟢" if pct >= 0 else "🔴"
        name = TICKERS.get(ticker, {}).get("name", "")
        lines.append(
            f"`{ticker:<5}` **${price:>8,.2f}**  {arrow} `{pct:+6.2f}%`  _{name}_"
        )
    return lines


class StocksCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        init_stock_prices()

    @commands.Cog.listener("on_ready")
    async def _start_stock_tasks(self):
        if not self.stock_tick_check.is_running():
            self.stock_tick_check.start()

    # ---------------------------------------------------------------------------
    # SCHEDULED TICKS
    # ---------------------------------------------------------------------------
    @tasks.loop(minutes=5)
    async def stock_tick_check(self):
        """Hourly tick during trading hours; first tick of the day also posts the morning embed."""
        now = datetime.now(CENTRAL_TZ)
        if now.hour < TRADING_HOUR_START or now.hour > TRADING_HOUR_END:
            return

        today_key = now.strftime("%Y-%m-%d")
        hour_key = now.strftime("%Y-%m-%d %H")
        last_tick_key = shared.runtime_settings.get("ticker_last_tick_key")
        last_morning = shared.runtime_settings.get("ticker_last_morning_date")

        if last_tick_key == hour_key:
            return

        # First tick of the day: snapshot prev_close BEFORE advancing so it
        # captures yesterday's last price. This makes the morning embed show
        # a real overnight gap, AND keeps prev_close anchored to yesterday's
        # close for the rest of the day so mid-day `.stocks` comparisons
        # match what every stock app calls "today's change".
        if last_morning != today_key:
            snapshot_prev_close()
            advance_prices()
            await self._post_morning_announcement()
            record_price_snapshot(today_key)
            shared.runtime_settings["ticker_last_morning_date"] = today_key
            shared._save_json_setting("ticker_last_morning_date", today_key)
        else:
            advance_prices()

        shared.runtime_settings["ticker_last_tick_key"] = hour_key
        shared._save_json_setting("ticker_last_tick_key", hour_key)

    @stock_tick_check.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()

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
    async def stocks(self, ctx, ticker: str = None):
        """Show current prices, or a per-ticker detail view if a ticker is given."""
        if ticker:
            return await self._stock_detail(ctx, ticker.upper())
        lines = build_market_lines()
        embed = discord.Embed(
            title="📊 Stock Market",
            description="\n".join(lines),
            color=COLOR_DEFAULT,
        )
        embed.set_footer(
            text=f"{PREFIX}stocks <TICKER> for detail · {PREFIX}buy / {PREFIX}sell / {PREFIX}portfolio"
        )
        await ctx.send(embed=embed)

    async def _stock_detail(self, ctx, ticker: str):
        if ticker not in TICKERS:
            return await ctx.send(embed=make_embed(
                "Unknown Ticker",
                f"`{ticker}` isn't listed. Use `{PREFIX}stocks` to see tickers.",
                COLOR_ERROR,
            ))
        cfg = TICKERS[ticker]
        prices = get_all_prices()
        data = prices.get(ticker, {})
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
            spark_line = "_(no daily history yet — sparkline appears after the next morning tick)_"

        lines = [
            f"**{cfg['name']}**",
            f"Price: **${price:,.2f}**  {arrow} `{pct:+.2f}%` since yesterday",
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
                f"**Your position:** {holding['shares']} sh @ ${holding['avg_cost']:.2f} → "
                f"${value:,.2f} {pl_arrow} `{pl:+,.0f} ({pl_pct:+.1f}%)`"
            )
            realized = get_realized_pl(ctx.author.id, ticker)
            if realized != 0:
                r_arrow = "🟢" if realized >= 0 else "🔴"
                lines.append(f"Realized P/L on {ticker}: {r_arrow} `{realized:+,.0f}`")

        embed = make_embed(f"📈 {ticker}", "\n".join(lines), COLOR_DEFAULT)
        embed.set_footer(text=f"{PREFIX}buy {ticker} <qty|all|$coins>  ·  {PREFIX}sell {ticker} <qty|all|$coins>")
        await ctx.send(embed=embed)

    @staticmethod
    def _resolve_quantity(arg: str, price: float, max_shares: int | None = None) -> tuple[int | None, str | None]:
        """Parse a 'qty', 'all', or '$coins' argument into a share count.
        For sell, max_shares is the user's position size; for buy, the caller
        passes the max affordable share count given the user's balance.
        Returns (shares, error_message). On error, shares is None."""
        if arg is None:
            return None, "Missing quantity."
        arg = arg.strip().lower()
        if arg == "all":
            if max_shares is None or max_shares <= 0:
                return None, "Nothing to apply `all` to (no position or no coins)."
            return max_shares, None
        if arg.startswith("$"):
            try:
                coins = int(arg[1:].replace(",", ""))
            except ValueError:
                return None, "Invalid coin amount. Example: `$500`."
            if coins <= 0 or price <= 0:
                return None, "Coin amount must be positive."
            qty = int(coins // price)
            if qty <= 0:
                return None, f"${coins:,} buys 0 shares at ${price:,.2f}."
            return qty, None
        try:
            qty = int(arg)
        except ValueError:
            return None, "Quantity must be a whole number, `all`, or `$<coins>`."
        if qty <= 0:
            return None, "Quantity must be positive."
        return qty, None

    @commands.command()
    async def buy(self, ctx, ticker: str = None, shares: str = None):
        """Buy shares. `qty` accepts a whole number, `all` (spend full balance), or `$<coins>`."""
        if ticker is None or shares is None:
            return await ctx.send(f"Usage: `{PREFIX}buy <TICKER> <qty|all|$coins>`")
        ticker = ticker.upper()
        if ticker not in TICKERS:
            return await ctx.send(embed=make_embed(
                "Unknown Ticker",
                f"`{ticker}` isn't listed. Use `{PREFIX}stocks` to see tickers.",
                COLOR_ERROR,
            ))
        price = get_price(ticker)
        if price is None:
            return await ctx.send(embed=make_embed("Market Closed", "Price unavailable.", COLOR_ERROR))
        bal = get_balance(ctx.author.id)
        max_affordable = int(bal // price) if price > 0 else 0
        qty, err = self._resolve_quantity(shares, price, max_shares=max_affordable)
        if err:
            return await ctx.send(embed=make_embed("Invalid", err, COLOR_ERROR))
        cost = int(round(price * qty))
        if cost <= 0:
            cost = 1
        if cost > bal:
            return await ctx.send(embed=make_embed(
                "❌ Broke",
                f"That'd cost **{cost:,}** coins; you only have **{bal:,}**.",
                COLOR_ERROR,
            ))
        new_bal = execute_buy(ctx.author.id, ticker, qty, price, cost)
        await ctx.send(embed=make_embed(
            "🟢 Buy Filled",
            f"Bought **{qty}** sh of `{ticker}` @ **${price:,.2f}** for **{cost:,}** coins.\n"
            f"Balance: **{new_bal:,}**",
            COLOR_SUCCESS,
        ))

    @commands.command()
    async def sell(self, ctx, ticker: str = None, shares: str = None):
        """Sell shares. `qty` accepts a whole number, `all`, or `$<coins>` worth."""
        if ticker is None or shares is None:
            return await ctx.send(f"Usage: `{PREFIX}sell <TICKER> <qty|all|$coins>`")
        ticker = ticker.upper()
        if ticker not in TICKERS:
            return await ctx.send(embed=make_embed(
                "Unknown Ticker", f"`{ticker}` isn't listed.", COLOR_ERROR,
            ))
        existing = get_user_holding(ctx.author.id, ticker)
        if not existing:
            return await ctx.send(embed=make_embed(
                "No Position", f"You don't own any `{ticker}`.", COLOR_ERROR,
            ))
        price = get_price(ticker)
        if price is None:
            return await ctx.send(embed=make_embed("Market Closed", "Price unavailable.", COLOR_ERROR))
        qty, err = self._resolve_quantity(shares, price, max_shares=existing["shares"])
        if err:
            return await ctx.send(embed=make_embed("Invalid", err, COLOR_ERROR))
        if qty > existing["shares"]:
            return await ctx.send(embed=make_embed(
                "Invalid",
                f"You only have **{existing['shares']}** sh of `{ticker}`.",
                COLOR_ERROR,
            ))
        proceeds = int(round(price * qty))
        result = execute_sell(ctx.author.id, ticker, qty, price, proceeds)
        if result is None:
            return await ctx.send(embed=make_embed(
                "Position Changed",
                "Your position changed before the order could fill — try again.",
                COLOR_ERROR,
            ))
        new_bal, realized = result
        pl_str = f"+{realized:,.0f}" if realized >= 0 else f"{realized:,.0f}"
        await ctx.send(embed=make_embed(
            "🔴 Sell Filled",
            f"Sold **{qty}** sh of `{ticker}` @ **${price:,.2f}** for **{proceeds:,}** coins.\n"
            f"P/L vs cost: **{pl_str}** coins\nBalance: **{new_bal:,}**",
            COLOR_SUCCESS,
        ))

    @commands.command(aliases=["port"])
    async def portfolio(self, ctx, member: discord.Member = None):
        """Show your stock holdings and unrealized P/L."""
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
                f"`{h['ticker']:<5}` {h['shares']:>4} sh @ ${h['avg_cost']:.2f} → "
                f"${cur_price:.2f} = **${value:,.2f}** {arrow} `{pl:+,.0f} ({pl_pct:+.1f}%)`"
            )
        net = total_value - total_cost
        net_arrow = "🟢" if net >= 0 else "🔴"
        net_pct = (net / total_cost * 100) if total_cost else 0.0
        lines.append("")
        lines.append(
            f"**Portfolio value:** ${total_value:,.2f}  (cost ${total_cost:,.2f})"
        )
        lines.append(
            f"**Unrealized P/L:** {net_arrow} `{net:+,.0f} ({net_pct:+.1f}%)`"
        )
        await ctx.send(embed=make_embed(
            f"📊 {target.display_name}'s Portfolio",
            "\n".join(lines),
            COLOR_DEFAULT,
        ))


async def setup(bot):
    await bot.add_cog(StocksCog(bot))
