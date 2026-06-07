"""Tests for external services: Ollama and OpenWeather, with HTTP mocked.

Tests assert behavior at the function boundary — never hit a real network.
"""

import asyncio
from datetime import datetime

import aiohttp
import pytest

import shared
from shared import CENTRAL_TZ
from tests.conftest import mock_http


# ---------------------------------------------------------------------------
# Shared payloads
# ---------------------------------------------------------------------------

_GEO_US = [{"name": "Champaign", "lat": 40.12, "lon": -88.24, "country": "US", "state": "Illinois"}]
_GEO_GB = [{"name": "London", "lat": 51.51, "lon": -0.13, "country": "GB", "state": "England"}]
_GEO_JP = [{"name": "Tokyo", "lat": 35.69, "lon": 139.69, "country": "JP"}]


def _current_payload(name="Champaign"):
    return {
        "name": name,
        "main": {"temp": 72.5, "feels_like": 70.0, "humidity": 60},
        "weather": [{"description": "clear sky", "icon": "01d"}],
        "wind": {"speed": 5.5},
        "sys": {"country": "US"},
    }


# ---------------------------------------------------------------------------
# Ollama: query_ollama / query_ollama_chat
# ---------------------------------------------------------------------------
class TestQueryOllama:
    async def test_returns_content_on_200(self):
        with mock_http({"status": 200, "payload": {"message": {"content": "hello world"}}}):
            result = await shared.query_ollama("system", "prompt")
        assert result == "hello world"

    async def test_returns_none_on_500(self):
        with mock_http({"status": 500, "payload": {}}):
            result = await shared.query_ollama("system", "prompt")
        assert result is None

    async def test_returns_none_on_connection_error(self):
        with mock_http({"exception": aiohttp.ClientConnectionError("offline")}):
            result = await shared.query_ollama("system", "prompt")
        assert result is None

    async def test_returns_none_on_timeout(self):
        with mock_http({"exception": asyncio.TimeoutError()}):
            result = await shared.query_ollama("system", "prompt")
        assert result is None

    async def test_returns_none_when_message_field_missing(self):
        with mock_http({"status": 200, "payload": {"unrelated": "data"}}):
            result = await shared.query_ollama("system", "prompt")
        assert result is None

    async def test_chat_passes_history_through(self):
        history = [{"role": "user", "content": "hi"}]
        with mock_http({"status": 200, "payload": {"message": {"content": "ok"}}}):
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
        with mock_http(
            {"status": 200, "payload": _GEO_US},
            {"status": 200, "payload": _current_payload()},
        ):
            embed = await cog._fetch_weather_embed("Champaign")

        assert embed is not None
        assert embed.title == "Weather in Champaign, IL"
        names = {f.name for f in embed.fields}
        assert {"Condition", "Temp", "Feels Like", "Humidity", "Wind"} <= names

    async def test_returns_none_on_geo_500(self, cog):
        with mock_http({"status": 500, "payload": {}}):
            result = await cog._fetch_weather_embed("Champaign")
        assert result is None

    async def test_returns_none_on_empty_geo_result(self, cog):
        with mock_http({"status": 200, "payload": []}):
            result = await cog._fetch_weather_embed("notacity")
        assert result is None

    async def test_returns_none_on_weather_500(self, cog):
        with mock_http(
            {"status": 200, "payload": _GEO_US},
            {"status": 500, "payload": {}},
        ):
            result = await cog._fetch_weather_embed("Champaign")
        assert result is None

    async def test_returns_none_on_connection_error(self, cog):
        with mock_http({"exception": aiohttp.ClientConnectionError()}):
            result = await cog._fetch_weather_embed("Champaign")
        assert result is None

    async def test_us_city_shows_state_abbreviation(self, cog):
        with mock_http(
            {"status": 200, "payload": _GEO_US},
            {"status": 200, "payload": _current_payload()},
        ):
            embed = await cog._fetch_weather_embed("Champaign")
        assert embed.title == "Weather in Champaign, IL"

    async def test_us_city_without_state_shows_no_suffix(self, cog):
        geo_no_state = [{"name": "Springfield", "lat": 39.8, "lon": -89.6, "country": "US"}]
        with mock_http(
            {"status": 200, "payload": geo_no_state},
            {"status": 200, "payload": _current_payload("Springfield")},
        ):
            embed = await cog._fetch_weather_embed("Springfield")
        assert embed.title == "Weather in Springfield"

    async def test_non_us_city_with_state_shows_state_and_country(self, cog):
        payload = _current_payload("London")
        with mock_http(
            {"status": 200, "payload": _GEO_GB},
            {"status": 200, "payload": payload},
        ):
            embed = await cog._fetch_weather_embed("London")
        assert embed.title == "Weather in London, England, GB"

    async def test_non_us_city_without_state_shows_country_only(self, cog):
        payload = _current_payload("Tokyo")
        with mock_http(
            {"status": 200, "payload": _GEO_JP},
            {"status": 200, "payload": payload},
        ):
            embed = await cog._fetch_weather_embed("Tokyo")
        assert embed.title == "Weather in Tokyo, JP"

    async def test_forecast_appends_daily_summary_field(self, cog):
        forecast = {
            "list": [
                {
                    "dt": 1762560000,
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
        with mock_http(
            {"status": 200, "payload": _GEO_US},
            {"status": 200, "payload": _current_payload()},
            {"status": 200, "payload": forecast},
        ):
            embed = await cog._fetch_weather_embed("Champaign", forecast_mode="multi_day")

        assert embed is not None
        assert any("Daily Forecast" in str(f.value) for f in embed.fields), (
            f"forecast section not found in fields: {[(f.name, f.value) for f in embed.fields]}"
        )

    async def test_today_forecast_adds_single_day_high_low_rain_chance(self, cog):
        today = datetime.now(CENTRAL_TZ)
        forecast = {
            "list": [
                {
                    "dt": int(today.replace(hour=9, minute=0, second=0, microsecond=0).timestamp()),
                    "main": {"temp_max": 74.0, "temp_min": 62.0},
                    "weather": [{"description": "sunny"}],
                    "pop": 0.1,
                },
                {
                    "dt": int(today.replace(hour=15, minute=0, second=0, microsecond=0).timestamp()),
                    "main": {"temp_max": 81.0, "temp_min": 66.0},
                    "weather": [{"description": "light rain"}],
                    "pop": 0.45,
                },
            ]
        }
        with mock_http(
            {"status": 200, "payload": _GEO_US},
            {"status": 200, "payload": _current_payload()},
            {"status": 200, "payload": forecast},
        ):
            embed = await cog._fetch_weather_embed("Champaign", forecast_mode="today")

        fields = {f.name: f.value for f in embed.fields}
        assert "Today's Forecast" in fields
        assert "High: **81°F**" in fields["Today's Forecast"]
        assert "Low: **62°F**" in fields["Today's Forecast"]
        assert "Rain chance: **45%**" in fields["Today's Forecast"]
        assert not any("Daily Forecast" in str(f.value) for f in embed.fields)


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
