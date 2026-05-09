from unittest.mock import AsyncMock

import discord

import shared
from tests.conftest import FakeAuthor, FakeContext, FakeGuild


class FakeReportMessage:
    def __init__(self, channel, message_id=5000, embed=None):
        self.channel = channel
        self.id = message_id
        self.jump_url = f"https://discord.com/channels/1/{channel.id}/{message_id}"
        self.embeds = [embed] if embed is not None else []
        self.add_reaction = AsyncMock()
        self.remove_reaction = AsyncMock()
        self.edit = AsyncMock()


class FakeReportChannel:
    def __init__(self, channel_id):
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.sent = []
        self._next_id = 5000
        self._messages = {}

    async def send(self, content=None, *, embed=None, **kwargs):
        self._next_id += 1
        message = FakeReportMessage(self, self._next_id, embed)
        self._messages[message.id] = message
        self.sent.append({"content": content, "embed": embed, "message": message, **kwargs})
        return message

    async def fetch_message(self, message_id):
        return self._messages[message_id]


class FakeBot:
    def __init__(self, channels):
        self.channels = channels
        self.user = FakeAuthor(user_id=42, name="Gary")
        self.added_views = []

    def add_view(self, view):
        self.added_views.append(view)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id):
        return self.channels[channel_id]


class HistoryChannel:
    def __init__(self, channel_id=100, messages=None):
        self.id = channel_id
        self.name = "bug-source"
        self.mention = f"<#{channel_id}>"
        self.sent = []
        self.history_messages = messages or []

    def history(self, **kwargs):
        self.history_kwargs = kwargs

        async def _iter_messages():
            messages = list(self.history_messages)
            if not kwargs.get("oldest_first", False):
                messages = list(reversed(messages))
            limit = kwargs.get("limit")
            if limit is not None:
                messages = messages[:limit]
            for message in messages:
                yield message

        return _iter_messages()

    async def send(self, content=None, *, embed=None, **kwargs):
        self.sent.append({"content": content, "embed": embed, **kwargs})
        return FakeReportMessage(self)


class PriorMessage:
    def __init__(self, author_name, content):
        self.author = FakeAuthor(name=author_name)
        self.clean_content = content
        self.attachments = []
        self.embeds = []


def _fields(embed):
    return {field.name: field.value for field in embed.fields}


def _report_cog():
    bug_channel = FakeReportChannel(shared.BUG_REPORT_CHANNEL_ID)
    feature_channel = FakeReportChannel(shared.FEATURE_REQUEST_CHANNEL_ID)
    tracking_channel = FakeReportChannel(shared.REQUEST_TRACKING_CHANNEL_ID)
    bot = FakeBot({
        bug_channel.id: bug_channel,
        feature_channel.id: feature_channel,
        tracking_channel.id: tracking_channel,
    })
    from modules.misc import MiscCog

    return MiscCog(bot), bug_channel, feature_channel, tracking_channel


async def test_bugreport_posts_public_report_and_tracking_controls():
    cog, bug_channel, _, tracking_channel = _report_cog()
    source_channel = HistoryChannel(messages=[
        PriorMessage("Alice", "first context"),
        PriorMessage("Bob", "second context"),
    ])
    ctx = FakeContext(
        author=FakeAuthor(user_id=123, name="Reporter"),
        guild=FakeGuild(guild_id=999, name="Bug Guild"),
        channel=source_channel,
    )
    ctx.message.id = 111
    ctx.message.jump_url = "https://discord.com/channels/999/100/111"

    await cog.bugreport.callback(cog, ctx, description="Slots paid the wrong amount")

    public = bug_channel.sent[0]
    tracking = tracking_channel.sent[0]
    public_fields = _fields(public["embed"])
    tracking_fields = _fields(tracking["embed"])

    assert "view" not in public
    assert isinstance(tracking["view"], discord.ui.View)
    assert "Slots paid the wrong amount" in public_fields["Message"]
    assert "Bug Guild" in public_fields["Location"]
    assert "<@123>" in public_fields["Reported By"]
    assert "Alice" in public_fields["Last 5 Messages Before Report"]
    assert "Bob" in public_fields["Last 5 Messages Before Report"]
    assert "Public Report Tracking Card" in tracking_fields
    assert "Sorry you ran into an issue!" in ctx.sent[-1]["content"]
    assert "Track the status of your report here:" in ctx.sent[-1]["content"]
    assert bug_channel.sent[0]["message"].jump_url in ctx.sent[-1]["content"]


async def test_bugreport_uses_immediate_five_prior_messages_in_chronological_order():
    cog, bug_channel, _, _ = _report_cog()
    source_channel = HistoryChannel(messages=[
        PriorMessage("Old", "stale 1"),
        PriorMessage("Old", "stale 2"),
        PriorMessage("Reporter", "1"),
        PriorMessage("Reporter", "2"),
        PriorMessage("Reporter", "3"),
        PriorMessage("Reporter", "4"),
        PriorMessage("Reporter", "5"),
    ])
    ctx = FakeContext(guild=FakeGuild(), channel=source_channel)
    ctx.message.id = 333
    ctx.message.jump_url = "https://discord.com/channels/999/100/333"

    await cog.bugreport.callback(cog, ctx, description="History order check")

    context = _fields(bug_channel.sent[0]["embed"])["Last 5 Messages Before Report"]
    assert source_channel.history_kwargs == {"limit": 5, "before": ctx.message}
    assert "stale 1" not in context
    assert "stale 2" not in context
    assert context.index("**Reporter:** 1") < context.index("**Reporter:** 2")
    assert context.index("**Reporter:** 2") < context.index("**Reporter:** 3")
    assert context.index("**Reporter:** 3") < context.index("**Reporter:** 4")
    assert context.index("**Reporter:** 4") < context.index("**Reporter:** 5")


async def test_featurerequest_posts_without_recent_message_context():
    cog, _, feature_channel, tracking_channel = _report_cog()
    ctx = FakeContext(
        author=FakeAuthor(user_id=456, name="Requester"),
        guild=FakeGuild(guild_id=888, name="Feature Guild"),
        channel=HistoryChannel(messages=[PriorMessage("Alice", "do not include me")]),
    )
    ctx.message.id = 222
    ctx.message.jump_url = "https://discord.com/channels/888/100/222"

    await cog.featurerequest.callback(cog, ctx, description="Add weekly leaderboards")

    public_fields = _fields(feature_channel.sent[0]["embed"])
    tracking_fields = _fields(tracking_channel.sent[0]["embed"])

    assert "Last 5 Messages Before Report" not in public_fields
    assert "Add weekly leaderboards" in public_fields["Message"]
    assert "Thank you for your feedback!" in ctx.sent[-1]["content"]
    assert "Track the status of your request here:" in ctx.sent[-1]["content"]
    assert feature_channel.sent[0]["message"].jump_url in ctx.sent[-1]["content"]


async def test_status_button_updates_tracking_and_public_report():
    from modules.misc import ReportStatusButton, ReportStatusView

    public_channel = FakeReportChannel(shared.BUG_REPORT_CHANNEL_ID)
    public_embed = discord.Embed(title="Bug Report")
    public_embed.add_field(name="Status", value="🆕 **New**", inline=True)
    public_message = FakeReportMessage(public_channel, 7001, public_embed)
    public_channel._messages[public_message.id] = public_message

    tracking_embed = discord.Embed(title="Bug Report Tracking")
    tracking_embed.add_field(name="Status", value="🆕 **New**", inline=True)
    tracking_embed.add_field(
        name="Public Report Tracking Card",
        value=(
            f"[Jump to report]({public_message.jump_url})\n"
            f"Channel ID: `{public_channel.id}`\n"
            f"Message ID: `{public_message.id}`"
        ),
        inline=False,
    )
    tracking_message = FakeReportMessage(FakeReportChannel(shared.REQUEST_TRACKING_CHANNEL_ID), 8001, tracking_embed)

    bot = FakeBot({public_channel.id: public_channel})
    interaction = AsyncMock()
    interaction.user = FakeAuthor(user_id=shared.ADMIN_ID, name="Matt")
    interaction.client = bot
    interaction.message = tracking_message
    interaction.response.edit_message = AsyncMock()

    button = ReportStatusButton("bug", "patched", "Patched", "✅", discord.ButtonStyle.success)
    button._view = ReportStatusView("bug")
    await button.callback(interaction)

    assert "✅ **Patched**" == tracking_message.embeds[0].fields[0].value
    assert "✅ **Patched**" == public_message.embeds[0].fields[0].value
    interaction.response.edit_message.assert_awaited_once()
    public_message.edit.assert_awaited_once()
    public_message.add_reaction.assert_awaited_with("✅")
