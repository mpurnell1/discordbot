"""Unit tests for MiscCog simple commands."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from modules.misc import MiscCog, _truncate_text, format_quote
from tests.conftest import FakeAuthor, FakeContext, FakeGuild


@pytest.fixture()
def misc_cog():
    bot = MagicMock()
    bot.user.id = 123456
    return MiscCog(bot)


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


class TestTruncateText:
    def test_short_text_unchanged(self):
        assert _truncate_text("hello", 100) == "hello"

    def test_long_text_truncated(self):
        result = _truncate_text("a" * 200, 50)
        assert len(result) <= 50
        assert result.endswith("...")

    def test_empty_string(self):
        assert _truncate_text("", 100) == ""

    def test_exact_limit_unchanged(self):
        text = "x" * 50
        assert _truncate_text(text, 50) == text


class TestFormatQuote:
    def test_includes_content_and_author(self):
        result = format_quote("hello world", "@user", "2025-01-01")
        assert "hello world" in result
        assert "@user" in result

    def test_includes_short_year(self):
        # format_quote uses "2k{YY}" format, e.g. "2025-01-01" → "2k25"
        result = format_quote("text", "@someone", "2025-06-15")
        assert "2k25" in result


# ---------------------------------------------------------------------------
# Command handler tests
# ---------------------------------------------------------------------------


class TestWyrCommand:
    async def test_sends_embed(self, misc_cog):
        ctx = FakeContext()
        await misc_cog.wyr.callback(misc_cog, ctx)
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert embed is not None
        assert "Would You Rather" in embed.title

    async def test_embed_has_two_options(self, misc_cog):
        ctx = FakeContext()
        await misc_cog.wyr.callback(misc_cog, ctx)
        body = ctx.sent[0]["embed"].description or ""
        assert "🅰️" in body and "🅱️" in body


class TestJokeCommand:
    async def test_sends_embed(self, misc_cog):
        ctx = FakeContext()
        await misc_cog.joke.callback(misc_cog, ctx)
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert embed is not None
        assert "Joke" in embed.title

    async def test_punchline_hidden_in_spoiler(self, misc_cog):
        ctx = FakeContext()
        await misc_cog.joke.callback(misc_cog, ctx)
        body = ctx.sent[0]["embed"].description or ""
        assert "||" in body


class TestInviteCommand:
    async def test_standard_invite_sends_embed(self, misc_cog):
        ctx = FakeContext()
        await misc_cog.invite.callback(misc_cog, ctx)
        assert ctx.sent
        embed = ctx.sent[0]["embed"]
        assert "Invite" in embed.title

    async def test_kids_invite_sends_kids_embed(self, misc_cog):
        ctx = FakeContext()
        await misc_cog.invite.callback(misc_cog, ctx, "kids")
        embed = ctx.sent[0]["embed"]
        assert "Kids" in embed.title

    async def test_kids_invite_mentions_kids_mode(self, misc_cog):
        ctx = FakeContext()
        await misc_cog.invite.callback(misc_cog, ctx, "kids")
        body = ctx.sent[0]["embed"].description or ""
        assert "kids" in body.lower()
