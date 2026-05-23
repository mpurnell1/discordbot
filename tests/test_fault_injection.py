"""Fault injection: external services down, DB locked.

The bot is supposed to degrade gracefully — no unhandled exceptions, user
gets a sensible message.
"""

from unittest.mock import AsyncMock, patch

from aioresponses import aioresponses

import shared
from tests.conftest import FakeContext


# ---------------------------------------------------------------------------
# Ollama down -> .ask sends a fallback message, no exception
# ---------------------------------------------------------------------------
async def test_ask_command_degrades_when_ollama_returns_none():
    from modules.ai import AICog

    cog = AICog(bot=AsyncMock())
    ctx = FakeContext()

    with patch("modules.ai.query_ollama", new=AsyncMock(return_value=None)):
        # `ask` is the cog method; call its underlying callback directly.
        await cog.ask.callback(cog, ctx, question="anything")

    assert len(ctx.sent) == 1
    msg = ctx.sent[0]
    # Fallback string.
    assert msg["content"] is not None
    assert "offline" in msg["content"].lower() or "brain" in msg["content"].lower()


async def test_ask_command_handles_aiohttp_failure_inside_query():
    """If aiohttp itself raises (mid-call), shared.query_ollama should swallow
    it and the .ask command should still respond."""
    from modules.ai import AICog

    cog = AICog(bot=AsyncMock())
    ctx = FakeContext()

    with aioresponses() as m:
        import aiohttp

        m.post(
            f"{shared.OLLAMA_URL}/api/chat",
            exception=aiohttp.ClientConnectionError("desktop sleeping"),
        )
        await cog.ask.callback(cog, ctx, question="anything")

    assert len(ctx.sent) == 1


# ---------------------------------------------------------------------------
# Weather API down -> .weather command sends a "couldn't find" message
# ---------------------------------------------------------------------------
async def test_weather_command_handles_api_failure(monkeypatch):
    from modules.misc import MiscCog

    cog = MiscCog(bot=None)
    ctx = FakeContext()

    async def _fake_fetch(*args, **kwargs):
        return None  # simulate the fetch helper returning None on failure

    monkeypatch.setattr(cog, "_fetch_weather_embed", _fake_fetch)
    await cog.weather.callback(cog, ctx, city="Champaign")

    assert len(ctx.sent) == 1
    assert "Couldn't find" in ctx.sent[0]["content"]


# ---------------------------------------------------------------------------
# Settings load with corrupt JSON falls back to defaults instead of crashing
# ---------------------------------------------------------------------------
def test_load_runtime_settings_recovers_from_corrupt_json():
    # Plant garbage in settings table.
    shared.db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("dead_chat_enabled", "{not json"),
    )
    shared.db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("command_toggles", "garbage"),
    )
    shared.db.commit()

    # Should not raise.
    shared.load_runtime_settings()

    # Defaults should be in place after the recovery.
    assert shared.runtime_settings["dead_chat_enabled"] == shared.SETTINGS_DEFAULTS["dead_chat_enabled"]
    assert shared.runtime_settings["command_toggles"] == shared.SETTINGS_DEFAULTS["command_toggles"]
