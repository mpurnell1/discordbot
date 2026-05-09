"""Test fixtures for the discordbot suite.

The DB path is redirected to a tmp file before any project import so cogs
opening connections through `shared.db` land in the test sandbox, never
touching the real `bot.db`.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# CRITICAL: redirect the DB before importing shared (or anything that imports it)
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="discordbot_tests_"))
os.environ["DISCORDBOT_DB_PATH"] = str(_TEST_DB_DIR / "test.db")
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("OPENWEATHER_API_KEY", "test-key")

# Make repo root importable (so `import shared`, `from modules import ...` work).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import shared  # noqa: E402  (must come after env-var setup)


# --------------------------------------------------------------------------
# DB / settings reset
# --------------------------------------------------------------------------
_TABLES = (
    "users", "nick_changes", "quotes", "settings", "guild_settings",
    "command_log", "balance_history", "puzzle_history", "game_results", "gambling_log",
    "gary_sessions",
)


@pytest.fixture(autouse=True)
def reset_state():
    """Wipe DB tables and in-memory state between tests."""
    for table in _TABLES:
        shared.db.execute(f"DELETE FROM {table}")
    shared.db.commit()

    shared.runtime_settings.clear()
    shared.runtime_settings.update(shared.SETTINGS_DEFAULTS)
    shared.guild_runtime_settings.clear()
    shared.last_message_time.clear()
    shared.dead_chat_stage.clear()
    shared.last_late_night.clear()
    shared.recent_messages.clear()
    shared.active_silas_rp.clear()
    shared.command_usage.clear()
    shared.messages_seen = 0
    shared.bot_start_time = None
    yield


# --------------------------------------------------------------------------
# FakeContext — minimal stand-in for discord.ext.commands.Context
# --------------------------------------------------------------------------
class FakeAuthor:
    def __init__(self, user_id: int = 1, name: str = "TestUser", is_bot: bool = False):
        self.id = user_id
        self.name = name
        self.display_name = name
        self.mention = f"<@{user_id}>"
        self.bot = is_bot


class FakeChannel:
    def __init__(self, channel_id: int = 100, name: str = "general"):
        self.id = channel_id
        self.name = name
        self.mention = f"<#{channel_id}>"
        self.sent: list[dict[str, Any]] = []

    async def send(self, content=None, *, embed=None, **kwargs):
        self.sent.append({"content": content, "embed": embed, **kwargs})
        msg = MagicMock()
        msg.add_reaction = AsyncMock()
        msg.edit = AsyncMock()
        return msg


class FakeGuild:
    def __init__(self, guild_id: int = 1000, name: str = "TestGuild"):
        self.id = guild_id
        self.name = name
        self.owner_id = 99
        self.owner = MagicMock(spec=["__str__"])
        self.member_count = 5
        self.me = MagicMock()
        self.me.guild_permissions = MagicMock(manage_messages=True, manage_nicknames=True)
        self.system_channel = None
        self.text_channels: list[Any] = []

    def get_member(self, user_id):
        return None


class FakeMessage:
    def __init__(self, channel: FakeChannel, content: str = ""):
        self.channel = channel
        self.content = content
        self.reference = None
        self.channel_mentions: list[Any] = []

    async def delete(self):
        pass


class FakeContext:
    """Minimal Context stand-in. Records sends; supports embed and content."""

    def __init__(
        self,
        *,
        author: FakeAuthor | None = None,
        guild: FakeGuild | None = None,
        channel: FakeChannel | None = None,
    ):
        self.author = author or FakeAuthor()
        self.guild = guild
        self.channel = channel or FakeChannel()
        self.message = FakeMessage(self.channel)
        self.sent: list[dict[str, Any]] = self.channel.sent  # alias

    async def send(self, content=None, *, embed=None, **kwargs):
        return await self.channel.send(content, embed=embed, **kwargs)

    def typing(self):
        # discord.py's Context.typing() returns a context manager object,
        # not a coroutine — used as `async with ctx.typing():`.
        return _NullContextManager()

    @property
    def last_send(self) -> dict[str, Any] | None:
        return self.sent[-1] if self.sent else None


class _NullContextManager:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.fixture
def fake_ctx():
    """Default FakeContext with a guild, in a non-kids-mode server."""
    return FakeContext(guild=FakeGuild())


@pytest.fixture
def admin_ctx():
    """FakeContext where author is the admin."""
    return FakeContext(
        author=FakeAuthor(user_id=shared.ADMIN_ID, name="Admin"),
        guild=FakeGuild(),
    )
