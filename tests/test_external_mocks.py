"""Tests for external services: Ollama and OpenWeather, with HTTP mocked.

Tests assert behavior at the function boundary — never hit a real network.
"""
import asyncio
import re

import pytest
from aioresponses import aioresponses

import shared


# Match either URL with or without a trailing query string.
_WEATHER_RE = re.compile(r"https://api\.openweathermap\.org/data/2\.5/weather.*")
_FORECAST_RE = re.compile(r"https://api\.openweathermap\.org/data/2\.5/forecast.*")


# ---------------------------------------------------------------------------
# Ollama: query_ollama / query_ollama_chat
# ---------------------------------------------------------------------------
class TestQueryOllama:
    URL = f"{shared.OLLAMA_URL}/api/chat"

    async def test_returns_content_on_200(self):
        with aioresponses() as m:
            m.post(
                self.URL,
                status=200,
                payload={"message": {"content": "hello world"}},
            )
            result = await shared.query_ollama("system", "prompt")
        assert result == "hello world"

    async def test_returns_none_on_500(self):
        with aioresponses() as m:
            m.post(self.URL, status=500, payload={})
            result = await shared.query_ollama("system", "prompt")
        assert result is None

    async def test_returns_none_on_connection_error(self):
        import aiohttp
        with aioresponses() as m:
            m.post(self.URL, exception=aiohttp.ClientConnectionError("offline"))
            result = await shared.query_ollama("system", "prompt")
        assert result is None

    async def test_returns_none_on_timeout(self):
        with aioresponses() as m:
            m.post(self.URL, exception=asyncio.TimeoutError())
            result = await shared.query_ollama("system", "prompt")
        assert result is None

    async def test_returns_none_when_message_field_missing(self):
        with aioresponses() as m:
            m.post(self.URL, status=200, payload={"unrelated": "data"})
            result = await shared.query_ollama("system", "prompt")
        assert result is None

    async def test_chat_passes_history_through(self):
        history = [{"role": "user", "content": "hi"}]
        with aioresponses() as m:
            m.post(
                self.URL,
                status=200,
                payload={"message": {"content": "ok"}},
            )
            result = await shared.query_ollama_chat(history)
        assert result == "ok"


# ---------------------------------------------------------------------------
# Weather (OpenWeather) — exercise via MiscCog._fetch_weather_embed
# ---------------------------------------------------------------------------
class TestWeatherFetch:
    @pytest.fixture
    def cog(self):
        from modules.misc import MiscCog
        return MiscCog(bot=None)

    async def test_returns_embed_on_200_payload(self, cog):
        payload = {
            "name": "Champaign",
            "main": {"temp": 72.5, "feels_like": 70.0, "humidity": 60},
            "weather": [{"description": "clear sky", "icon": "01d"}],
            "wind": {"speed": 5.5},
        }
        with aioresponses() as m:
            m.get(_WEATHER_RE, status=200, payload=payload)
            embed = await cog._fetch_weather_embed("Champaign", include_forecast=False)

        assert embed is not None
        assert embed.title == "Weather in Champaign"
        # Each field name should appear in the field list.
        names = {f.name for f in embed.fields}
        assert {"Condition", "Temp", "Feels Like", "Humidity", "Wind"} <= names

    async def test_returns_none_on_500(self, cog):
        with aioresponses() as m:
            m.get(_WEATHER_RE, status=500, payload={})
            result = await cog._fetch_weather_embed("Champaign")
        assert result is None

    async def test_returns_none_on_connection_error(self, cog):
        import aiohttp
        with aioresponses() as m:
            m.get(_WEATHER_RE, exception=aiohttp.ClientConnectionError())
            result = await cog._fetch_weather_embed("Champaign")
        assert result is None

    async def test_forecast_appends_daily_summary_field(self, cog):
        current = {
            "name": "Champaign",
            "main": {"temp": 72.5, "feels_like": 70.0, "humidity": 60},
            "weather": [{"description": "clear sky", "icon": "01d"}],
            "wind": {"speed": 5.5},
        }
        # Two synthetic forecast entries for the same day.
        forecast = {
            "list": [
                {
                    "dt": 1762560000,  # arbitrary unix ts
                    "main": {"temp_max": 75.0, "temp_min": 60.0},
                    "weather": [{"description": "sunny"}],
                },
                {
                    "dt": 1762570800,
                    "main": {"temp_max": 78.0, "temp_min": 62.0},
                    "weather": [{"description": "partly cloudy"}],
                },
            ]
        }
        with aioresponses() as m:
            m.get(_WEATHER_RE, status=200, payload=current)
            m.get(_FORECAST_RE, status=200, payload=forecast)
            embed = await cog._fetch_weather_embed("Champaign", include_forecast=True)

        assert embed is not None
        # The forecast section adds a "— Daily Forecast —" header field.
        assert any("Daily Forecast" in str(f.value) for f in embed.fields), \
            f"forecast section not found in fields: {[(f.name, f.value) for f in embed.fields]}"


class TestCleanCity:
    @pytest.fixture
    def cog(self):
        from modules.misc import MiscCog
        return MiscCog(bot=None)

    def test_local_city_gets_il_us_suffix(self, cog):
        assert cog._clean_city("champaign") == "champaign,IL,US"

    def test_already_state_qualified_gets_us_suffix(self, cog):
        assert cog._clean_city("Springfield, MO") == "Springfield, MO,US"

    def test_other_city_passes_through(self, cog):
        assert cog._clean_city("Tokyo") == "Tokyo"

    def test_local_city_case_insensitive(self, cog):
        assert cog._clean_city("URBANA") == "URBANA,IL,US"
