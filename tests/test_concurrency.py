"""Concurrency tests.

The bot runs single-threaded under asyncio — no actual parallelism. So the
"race" we care about is whether sync DB sections inside async coroutines
form proper critical sections (no `await` between read and write of the
same row).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock


import shared
import bot as bot_module


# ---------------------------------------------------------------------------
# update_balance: sequential calls compose correctly (algebraic sum)
# ---------------------------------------------------------------------------
def test_many_sequential_update_balance_calls_sum_correctly():
    user = 8001
    shared.get_balance(user)
    starting = shared.get_balance(user)

    deltas = list(range(-50, 51))  # net 0 across 101 calls
    for d in deltas:
        shared.update_balance(user, d)

    assert shared.get_balance(user) == starting + sum(deltas)


# ---------------------------------------------------------------------------
# auto_daily_award: two near-simultaneous awards must not double-credit
# ---------------------------------------------------------------------------
def _make_ctx(user_id: int):
    ctx = MagicMock()
    ctx.author.id = user_id
    ctx.send = AsyncMock()
    return ctx


async def test_two_concurrent_awards_credit_at_most_once():
    """Two coroutines firing auto_daily_award at the same time should
    credit DAILY_AMOUNT exactly once — the second has to see the
    last_daily timestamp from the first."""
    user = 8100
    shared.get_balance(user)
    shared.db.execute(
        "UPDATE users SET last_daily = ? WHERE user_id = ?",
        ((datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(), user),
    )
    shared.db.commit()
    starting = shared.get_balance(user)

    await asyncio.gather(
        bot_module.auto_daily_award(_make_ctx(user)),
        bot_module.auto_daily_award(_make_ctx(user)),
    )

    final = shared.get_balance(user)
    # In the actual single-threaded asyncio model, the entire await-free
    # critical section finishes before the second coroutine runs, so
    # the second one observes last_daily already set and skips.
    assert final - starting == shared.DAILY_AMOUNT, f"expected exactly one award, got delta={final - starting}"


async def test_many_concurrent_awards_credit_at_most_once():
    """Same contract under heavier concurrency."""
    user = 8101
    shared.get_balance(user)
    shared.db.execute(
        "UPDATE users SET last_daily = ? WHERE user_id = ?",
        ((datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(), user),
    )
    shared.db.commit()
    starting = shared.get_balance(user)

    await asyncio.gather(*(bot_module.auto_daily_award(_make_ctx(user)) for _ in range(20)))

    final = shared.get_balance(user)
    assert final - starting == shared.DAILY_AMOUNT


# ---------------------------------------------------------------------------
# Distinct users get independent awards
# ---------------------------------------------------------------------------
async def test_distinct_users_each_get_one_award():
    users = list(range(8200, 8210))
    starts = {}
    yesterday = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    for u in users:
        shared.get_balance(u)
        shared.db.execute(
            "UPDATE users SET last_daily = ? WHERE user_id = ?",
            (yesterday, u),
        )
        starts[u] = shared.get_balance(u)
    shared.db.commit()

    await asyncio.gather(*(bot_module.auto_daily_award(_make_ctx(u)) for u in users))

    for u in users:
        delta = shared.get_balance(u) - starts[u]
        assert delta == shared.DAILY_AMOUNT, f"user {u} got delta={delta}, expected {shared.DAILY_AMOUNT}"
