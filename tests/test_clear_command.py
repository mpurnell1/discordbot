"""Tests for the .clear admin command."""
import pytest
from unittest.mock import AsyncMock, MagicMock

import discord

import shared
from tests.conftest import FakeAuthor, FakeContext, FakeGuild


class FakeClearMessage:
    """Fake Discord message for history."""
    def __init__(self, message_id, author_id, author_name, content="test", is_bot=False):
        self.id = message_id
        self.content = content
        self.author = FakeAuthor(user_id=author_id, name=author_name, is_bot=is_bot)
        self.delete = AsyncMock()
        self.channel = None


class FakeClearChannel:
    """Fake channel with message history support."""
    def __init__(self, channel_id=100, name="test"):
        self.id = channel_id
        self.name = name
        self.mention = f"<#{channel_id}>"
        self.sent = []
        self.history_messages = []

    def history(self, limit=None):
        """Return an async iterator of messages in reverse order."""
        async def _iter_messages():
            messages = list(self.history_messages)
            if limit is not None:
                messages = messages[:limit]
            for message in messages:
                yield message

        return _iter_messages()

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})
        msg = MagicMock()
        msg.add_reaction = AsyncMock()
        return msg


class FakeClearBot:
    def __init__(self):
        self.user = FakeAuthor(user_id=42, name="Gary", is_bot=True)
    
    def add_view(self, view):
        """Mock add_view for ReportStatusView in __init__."""
        pass


def _misc_cog():
    """Create a MiscCog instance with a fake bot."""
    bot = FakeClearBot()
    from modules.misc import MiscCog
    return MiscCog(bot)


def _ctx_with_deletable_message(**kwargs):
    """Create a FakeContext where ctx.message can be deleted."""
    ctx = FakeContext(**kwargs)
    ctx.message.delete = AsyncMock()
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clear_requires_admin():
    """Non-admin should get no response."""
    cog = _misc_cog()
    channel = FakeClearChannel()
    ctx = _ctx_with_deletable_message(
        author=FakeAuthor(user_id=999, name="NotAdmin"),
        guild=FakeGuild(),
        channel=channel,
    )

    await cog.clear.callback(cog, ctx, "5")

    assert len(ctx.sent) == 0  # Admin check blocks response
    # Command message should still be deleted
    assert ctx.message.delete.called


@pytest.mark.asyncio
async def test_clear_rejects_missing_count():
    """Should reject if no count argument."""
    cog = _misc_cog()
    channel = FakeClearChannel()
    ctx = _ctx_with_deletable_message(
        author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
        guild=FakeGuild(),
        channel=channel,
    )

    await cog.clear.callback(cog, ctx, "")

    assert len(ctx.sent) == 1
    assert "Usage:" in ctx.sent[0]["content"]
    assert "clear <n>" in ctx.sent[0]["content"]
    assert ctx.message.delete.called


@pytest.mark.asyncio
async def test_clear_rejects_non_numeric():
    """Should reject non-numeric count."""
    cog = _misc_cog()
    channel = FakeClearChannel()
    ctx = _ctx_with_deletable_message(
        author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
        guild=FakeGuild(),
        channel=channel,
    )

    await cog.clear.callback(cog, ctx, "notanumber")

    assert len(ctx.sent) == 1
    assert "not a valid number" in ctx.sent[0]["content"]
    assert ctx.message.delete.called


@pytest.mark.asyncio
async def test_clear_rejects_zero_or_negative():
    """Should reject zero or negative numbers."""
    cog = _misc_cog()
    channel = FakeClearChannel()
    ctx = _ctx_with_deletable_message(
        author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
        guild=FakeGuild(),
        channel=channel,
    )

    await cog.clear.callback(cog, ctx, "0")

    assert len(ctx.sent) == 1
    assert "greater than 0" in ctx.sent[0]["content"]
    assert ctx.message.delete.called

    # Reset for next test
    ctx = _ctx_with_deletable_message(
        author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
        guild=FakeGuild(),
        channel=channel,
    )
    ctx.sent.clear()

    await cog.clear.callback(cog, ctx, "-5")

    assert len(ctx.sent) == 1
    assert "greater than 0" in ctx.sent[0]["content"]
    assert ctx.message.delete.called


@pytest.mark.asyncio
async def test_clear_rejects_over_100():
    """Should reject numbers over 100 (safety limit)."""
    cog = _misc_cog()
    channel = FakeClearChannel()
    ctx = _ctx_with_deletable_message(
        author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
        guild=FakeGuild(),
        channel=channel,
    )

    await cog.clear.callback(cog, ctx, "101")

    assert len(ctx.sent) == 1
    assert "100" in ctx.sent[0]["content"]
    assert "safety limit" in ctx.sent[0]["content"]
    assert ctx.message.delete.called


@pytest.mark.asyncio
async def test_clear_deletes_gary_messages_only():
    """Should only delete Gary's messages."""
    cog = _misc_cog()
    channel = FakeClearChannel()

    # Create message history: Gary, User, Gary, User, Gary
    gary_id = cog.bot.user.id
    channel.history_messages = [
        FakeClearMessage(5, gary_id, "Gary", is_bot=True),
        FakeClearMessage(4, 123, "User", is_bot=False),
        FakeClearMessage(3, gary_id, "Gary", is_bot=True),
        FakeClearMessage(2, 124, "OtherUser", is_bot=False),
        FakeClearMessage(1, gary_id, "Gary", is_bot=True),
    ]

    ctx = _ctx_with_deletable_message(
        author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
        guild=FakeGuild(),
        channel=channel,
    )

    await cog.clear.callback(cog, ctx, "2")

    # Should have deleted 2 Gary messages (5 and 3) silently (no response)
    assert len(ctx.sent) == 0
    assert channel.history_messages[0].delete.called  # Message 5
    assert not channel.history_messages[1].delete.called  # Message 4 (User)
    assert channel.history_messages[2].delete.called  # Message 3
    # Command message should be deleted
    assert ctx.message.delete.called


@pytest.mark.asyncio
async def test_clear_stops_at_limit():
    """Should stop after finding n Gary messages."""
    cog = _misc_cog()
    channel = FakeClearChannel()

    gary_id = cog.bot.user.id
    channel.history_messages = [
        FakeClearMessage(5, gary_id, "Gary", is_bot=True),
        FakeClearMessage(4, gary_id, "Gary", is_bot=True),
        FakeClearMessage(3, gary_id, "Gary", is_bot=True),
        FakeClearMessage(2, gary_id, "Gary", is_bot=True),
    ]

    ctx = _ctx_with_deletable_message(
        author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
        guild=FakeGuild(),
        channel=channel,
    )

    await cog.clear.callback(cog, ctx, "2")

    # Should have deleted 2 messages (5 and 4) silently, not 3 and 2
    assert len(ctx.sent) == 0
    assert channel.history_messages[0].delete.called
    assert channel.history_messages[1].delete.called
    assert not channel.history_messages[2].delete.called
    assert not channel.history_messages[3].delete.called
    assert ctx.message.delete.called


@pytest.mark.asyncio
async def test_clear_none_found():
    """Should report when no Gary messages are found."""
    cog = _misc_cog()
    channel = FakeClearChannel()

    # Only user messages
    channel.history_messages = [
        FakeClearMessage(5, 123, "User", is_bot=False),
        FakeClearMessage(4, 124, "User", is_bot=False),
    ]

    ctx = FakeContext(
        author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
        guild=FakeGuild(),
        channel=channel,
    )

    await cog.clear.callback(cog, ctx, "5")

    # Should silently do nothing if no messages found
    assert len(ctx.sent) == 0


@pytest.mark.asyncio
async def test_clear_partial_deletion_on_permission_error():
    """Should report error after partially deleting."""
    cog = _misc_cog()
    channel = FakeClearChannel()

    gary_id = cog.bot.user.id
    msg1 = FakeClearMessage(5, gary_id, "Gary", is_bot=True)
    msg2 = FakeClearMessage(4, gary_id, "Gary", is_bot=True)
    msg2.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Permission denied"))

    channel.history_messages = [msg1, msg2]

    ctx = _ctx_with_deletable_message(
        author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
        guild=FakeGuild(),
        channel=channel,
    )

    await cog.clear.callback(cog, ctx, "5")

    assert len(ctx.sent) == 1
    assert "Permission denied" in ctx.sent[0]["content"]
    assert "deleted 1 before error" in ctx.sent[0]["content"]
    # Command message should still be deleted
    assert ctx.message.delete.called
