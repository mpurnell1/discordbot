import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import random
import re
import os
import sys
from datetime import datetime, timedelta, timezone

import shared
from shared import (
    PREFIX,
    CENTRAL_TZ,
    ADMIN_ID,
    OPENWEATHER_API_KEY,
    OLLAMA_URL,
    OLLAMA_MODEL,
    OLLAMA_REASONING_MODEL,
    NICKNAME_COST,
    NICKNAME_DURATION_HOURS,
    PUZZLE_REWARD,
    COLOR_DEFAULT,
    COLOR_SUCCESS,
    COLOR_ERROR,
    COLOR_WARNING,
    COLOR_PINK,
    COLOR_ORANGE,
    COLOR_GOLD,
    KIDS_MODE_SUMMARY,
    PROTECTED_ADMIN_COMMANDS,
    make_embed,
    get_balance,
    update_balance,
    is_kids_mode_guild,
    set_kids_mode_guild,
    normalize_feature_name,
)

LOCAL_CITIES = {"champaign", "urbana", "savoy", "mattoon", "mahomet", "sidney", "tuscola"}

WYR_QUESTIONS = [
    ("Always be 10 minutes late", "Always be 20 minutes early"),
    ("Have unlimited pizza for life", "Have unlimited tacos for life"),
    ("Be able to fly", "Be able to read minds"),
    ("Live without music", "Live without movies"),
    ("Have no internet for a month", "Have no phone for a month"),
    ("Fight 100 duck-sized horses", "Fight 1 horse-sized duck"),
    ("Always have to whisper", "Always have to shout"),
    ("Have unlimited money but no friends", "Have no money but amazing friends"),
    ("Be able to talk to animals", "Speak every human language"),
    ("Live in the past", "Live in the future"),
    ("Have super strength", "Have super speed"),
    ("Never eat cheese again", "Never eat chocolate again"),
    ("Be famous and broke", "Be unknown and rich"),
    ("Have a rewind button for your life", "Have a pause button"),
    ("Be stuck in a horror movie", "Be stuck in a rom-com"),
    ("Always be slightly itchy", "Always be slightly sticky"),
    ("Only eat raw food", "Only eat canned food"),
    ("Have a personal chef", "Have a personal chauffeur"),
    ("Know the date of your death", "Know the cause of your death"),
    ("Have hands for feet", "Have feet for hands"),
]

KIDS_WYR_QUESTIONS = [
    ("Have a pet dragon", "Have a pet unicorn"),
    ("Visit the moon", "Visit the bottom of the ocean"),
    ("Be able to talk to animals", "Be able to speak every language"),
    ("Have ice cream for dessert", "Have cookies for dessert"),
    ("Be super fast", "Be super strong"),
    ("Live in a treehouse", "Live in a castle"),
    ("Be great at drawing", "Be great at music"),
    ("Have a robot helper", "Have a magic backpack"),
    ("Explore a jungle", "Explore a coral reef"),
    ("Always know the answer in math", "Always spell every word correctly"),
    ("Ride a flying bike", "Ride a tiny train"),
    ("Have recess twice a day", "Have art class every day"),
]

CLEAN_JOKES = [
    ("Why did the teddy bear skip dessert?", "Because it was already stuffed."),
    ("What do you call a sleeping bull?", "A bulldozer."),
    ("Why did the math book look sad?", "Because it had too many problems."),
    ("What has hands but cannot clap?", "A clock."),
    ("Why did the cookie go to the nurse?", "Because it felt crummy."),
    ("What do planets like to read?", "Comet books."),
    ("Why was the broom late?", "It overswept."),
    ("What do you call cheese that is not yours?", "Nacho cheese."),
    ("Why did the bicycle fall over?", "Because it was two tired."),
    ("What kind of tree fits in your hand?", "A palm tree."),
]

BUG_REPORT_STATUSES = [
    ("new", "New", "🆕", discord.ButtonStyle.secondary),
    ("identified", "Identified", "🔎", discord.ButtonStyle.primary),
    ("fixing", "Fixing", "🛠️", discord.ButtonStyle.primary),
    ("testing", "Testing", "🧪", discord.ButtonStyle.primary),
    ("patched", "Patched", "✅", discord.ButtonStyle.success),
    ("need_info", "Need Info", "❓", discord.ButtonStyle.secondary),
    ("closed", "Closed", "📦", discord.ButtonStyle.secondary),
]

FEATURE_REQUEST_STATUSES = [
    ("new", "New", "🆕", discord.ButtonStyle.secondary),
    ("reviewing", "Reviewing", "🔎", discord.ButtonStyle.primary),
    ("planned", "Planned", "🗓️", discord.ButtonStyle.primary),
    ("building", "Building", "🛠️", discord.ButtonStyle.primary),
    ("shipped", "Shipped", "✅", discord.ButtonStyle.success),
    ("wontfix", "Won't Fix", "🚫", discord.ButtonStyle.danger),
    ("closed", "Closed", "📦", discord.ButtonStyle.secondary),
]


def _status_map(kind: str):
    statuses = BUG_REPORT_STATUSES if kind == "bug" else FEATURE_REQUEST_STATUSES
    return {key: (label, emoji, style) for key, label, emoji, style in statuses}


def _truncate_text(text: str, limit: int = 1000) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _format_user(user) -> str:
    return f"{user.mention} (`{user.id}`)"


class ReportStatusButton(discord.ui.Button):
    def __init__(self, kind: str, status_key: str, label: str, emoji: str, style: discord.ButtonStyle):
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=f"gary:{kind}:status:{status_key}",
            row=0 if status_key in {"new", "identified", "reviewing", "planned", "fixing", "building"} else 1,
        )
        self.kind = kind
        self.status_key = status_key

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != ADMIN_ID:
            return await interaction.response.send_message(
                "Only Matt can update report statuses.",
                ephemeral=True,
            )
        status_data = _status_map(self.kind).get(self.status_key)
        if status_data is None:
            return await interaction.response.send_message("Unknown status.", ephemeral=True)

        label, emoji, _ = status_data
        message = interaction.message
        embed = message.embeds[0] if message and message.embeds else discord.Embed()
        report_channel_id, report_message_id = self._get_public_report_ids(embed)
        embed.color = COLOR_SUCCESS if self.status_key in {"patched", "shipped"} else COLOR_DEFAULT
        if embed.fields:
            embed.set_field_at(0, name="Status", value=f"{emoji} **{label}**", inline=True)
        else:
            embed.add_field(name="Status", value=f"{emoji} **{label}**", inline=True)
        embed.set_footer(text=f"Status updated by {interaction.user} at {datetime.now(CENTRAL_TZ):%Y-%m-%d %I:%M %p %Z}")

        await interaction.response.edit_message(embed=embed, view=self.view)
        await self._sync_status_reaction(message, emoji, interaction.client.user)
        if report_channel_id and report_message_id:
            await self._update_public_report(
                interaction.client,
                report_channel_id,
                report_message_id,
                emoji,
                label,
                interaction.user,
            )

    def _get_public_report_ids(self, embed: discord.Embed):
        for field in embed.fields:
            if field.name != "Public Report Tracking Card":
                continue
            channel_match = re.search(r"Channel ID: `(\d+)`", field.value)
            message_match = re.search(r"Message ID: `(\d+)`", field.value)
            if channel_match and message_match:
                return int(channel_match.group(1)), int(message_match.group(1))
        return None, None

    async def _update_public_report(
        self,
        client: discord.Client,
        channel_id: int,
        message_id: int,
        emoji: str,
        label: str,
        admin_user,
    ):
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
            report_message = await channel.fetch_message(message_id)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            return
        if report_message.embeds:
            report_embed = report_message.embeds[0]
            report_embed.color = COLOR_SUCCESS if self.status_key in {"patched", "shipped"} else COLOR_DEFAULT
            if report_embed.fields:
                report_embed.set_field_at(0, name="Status", value=f"{emoji} **{label}**", inline=True)
            else:
                report_embed.add_field(name="Status", value=f"{emoji} **{label}**", inline=True)
            report_embed.set_footer(text=f"Status updated by {admin_user} at {datetime.now(CENTRAL_TZ):%Y-%m-%d %I:%M %p %Z}")
            try:
                await report_message.edit(embed=report_embed)
            except discord.HTTPException:
                pass
        await self._sync_status_reaction(report_message, emoji, client.user)

    async def _sync_status_reaction(self, message: discord.Message, active_emoji: str, bot_user: discord.ClientUser):
        if message is None:
            return
        for _, emoji, _ in _status_map(self.kind).values():
            if emoji == active_emoji:
                continue
            try:
                await message.remove_reaction(emoji, bot_user)
            except (discord.Forbidden, discord.HTTPException):
                pass
        try:
            await message.add_reaction(active_emoji)
        except discord.HTTPException:
            pass


class ReportStatusView(discord.ui.View):
    def __init__(self, kind: str):
        super().__init__(timeout=None)
        statuses = BUG_REPORT_STATUSES if kind == "bug" else FEATURE_REQUEST_STATUSES
        for status_key, label, emoji, style in statuses:
            self.add_item(ReportStatusButton(kind, status_key, label, emoji, style))


def format_quote(content, name, date, prefix=""):
    year = date[2:4] if date else "??"
    short = content[:80] + "..." if len(content) > 80 else content
    return f'{prefix}> "{short}"\n\\- {name} 2k{year}'



class MiscCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if self.bot is not None:
            self.bot.add_view(ReportStatusView("bug"))
            self.bot.add_view(ReportStatusView("feature"))

    @commands.Cog.listener("on_ready")
    async def _start_misc_tasks(self):
        if not self.restore_nicknames.is_running():
            self.restore_nicknames.start()
        if not self.weather_alert_check.is_running():
            self.weather_alert_check.start()
    
    # ---------------------------------------------------------------------------
    # WEATHER
    # ---------------------------------------------------------------------------

    def _clean_city(self, city: str) -> str:
        cleaned = city.strip()
        if cleaned.lower() in LOCAL_CITIES:
            return cleaned + ",IL,US"
        if re.match(r'^.+,\s*[A-Za-z]{2}$', cleaned):
            return cleaned + ",US"
        return cleaned

    async def _fetch_weather_embed(self, city: str, title_prefix: str = "Weather in", include_forecast: bool = False):
        """Fetch current weather (and optionally daily forecast) and return an embed, or None on failure."""
        cleaned = self._clean_city(city)
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {"q": cleaned, "appid": OPENWEATHER_API_KEY, "units": "imperial"}
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                forecast_days = None
                if include_forecast:
                    forecast_days = await self._fetch_forecast_days(session, cleaned)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

        desc = data["weather"][0]["description"].title()
        temp = data["main"]["temp"]
        feels = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        wind = data["wind"]["speed"]
        icon = data["weather"][0]["icon"]
        name = data["name"]

        embed = discord.Embed(title=f"{title_prefix} {name}", color=COLOR_DEFAULT)
        embed.set_thumbnail(url=f"https://openweathermap.org/img/wn/{icon}@2x.png")
        embed.add_field(name="Condition", value=desc, inline=True)
        embed.add_field(name="Temp", value=f"{temp:.0f}°F", inline=True)
        embed.add_field(name="Feels Like", value=f"{feels:.0f}°F", inline=True)
        embed.add_field(name="Humidity", value=f"{humidity}%", inline=True)
        embed.add_field(name="Wind", value=f"{wind:.0f} mph", inline=True)

        if forecast_days:
            embed.add_field(name="​", value="**— Daily Forecast —**", inline=False)
            for label, day in list(forecast_days.items())[:4]:
                cond = day["descs"][0] if day["descs"] else "—"
                embed.add_field(
                    name=label,
                    value=f"{day['high']:.0f}° / {day['low']:.0f}°F\n{cond}",
                    inline=True,
                )

        return embed

    async def _fetch_forecast_days(self, session: aiohttp.ClientSession, cleaned_city: str):
        """Fetch 5-day/3-hour forecast and return an ordered dict of daily summaries."""
        url = "https://api.openweathermap.org/data/2.5/forecast"
        params = {"q": cleaned_city, "appid": OPENWEATHER_API_KEY, "units": "imperial", "cnt": 40}
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

        days = {}
        for entry in data.get("list", []):
            dt_central = datetime.fromtimestamp(entry["dt"], tz=CENTRAL_TZ)
            label = dt_central.strftime("%a %b ") + str(dt_central.day)
            if label not in days:
                days[label] = {"high": -999.0, "low": 999.0, "descs": []}
            days[label]["high"] = max(days[label]["high"], entry["main"]["temp_max"])
            days[label]["low"] = min(days[label]["low"], entry["main"]["temp_min"])
            desc = entry["weather"][0]["description"].title()
            if desc not in days[label]["descs"]:
                days[label]["descs"].append(desc)
        return days or None

    @commands.command(aliases=["w"])
    async def weather(self, ctx, *, city: str = "Champaign"):
        """Get the weather for a city. Append 'forecast' for a daily forecast."""
        include_forecast = False
        if city.lower() == "forecast":
            city = "Champaign"
            include_forecast = True
        elif city.lower().endswith(" forecast"):
            city = city[:-9].strip()
            include_forecast = True
        embed = await self._fetch_weather_embed(city, include_forecast=include_forecast)
        if embed is None:
            return await ctx.send(f"Couldn't find weather for **{city}** (or service unavailable).")
        await ctx.send(embed=embed)

    @tasks.loop(minutes=5)
    async def weather_alert_check(self):
        """Send daily 8 AM Central weather alert to the configured channel."""
        channel_id = shared.runtime_settings.get("weather_alert_channel_id")
        if not channel_id:
            return
        now_central = datetime.now(CENTRAL_TZ)
        if now_central.hour != 8:
            return
        today_key = now_central.strftime("%Y-%m-%d")
        if shared.runtime_settings.get("weather_alert_last_date") == today_key:
            return
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            return
        city = shared.runtime_settings.get("weather_alert_city", "Champaign")
        embed = await self._fetch_weather_embed(city, title_prefix="☀️ Good Morning —", include_forecast=True)
        if embed is None:
            return
        await channel.send(embed=embed)
        shared.runtime_settings["weather_alert_last_date"] = today_key
        shared._save_json_setting("weather_alert_last_date", today_key)
    
    # ---------------------------------------------------------------------------
    # ANIMALS
    # ---------------------------------------------------------------------------

    @commands.command()
    async def cat(self, ctx):
        """Random cat picture."""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://api.thecatapi.com/v1/images/search") as resp:
                    data = await resp.json()
                    embed = discord.Embed(title="Random Cat", color=COLOR_WARNING)
                    embed.set_image(url=data[0]["url"])
                    await ctx.send(embed=embed)
        except (aiohttp.ClientError, asyncio.TimeoutError, IndexError, KeyError, TypeError):
            await ctx.send("Couldn't fetch a cat right now. Try again in a bit.")
    

    @commands.command()
    async def dog(self, ctx):
        """Random dog picture."""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://dog.ceo/api/breeds/image/random") as resp:
                    data = await resp.json()
                    embed = discord.Embed(title="Random Dog", color=COLOR_WARNING)
                    embed.set_image(url=data[0] if isinstance(data, list) else data["message"])
                    await ctx.send(embed=embed)
        except (aiohttp.ClientError, asyncio.TimeoutError, IndexError, KeyError, TypeError):
            await ctx.send("Couldn't fetch a dog right now. Try again in a bit.")
    
    # ---------------------------------------------------------------------------
    # WOULD YOU RATHER
    # ---------------------------------------------------------------------------
    

    @commands.command()
    async def wyr(self, ctx):
        """Would You Rather — vote with reactions!"""
        pool = KIDS_WYR_QUESTIONS if ctx.guild and is_kids_mode_guild(ctx.guild.id) else WYR_QUESTIONS
        a, b = random.choice(pool)
        embed = make_embed("🤔 Would You Rather...",
            f"🅰️ {a}\n\n**OR**\n\n🅱️ {b}", COLOR_PINK)
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("🅰️")
        await msg.add_reaction("🅱️")

    @commands.command()
    async def joke(self, ctx):
        """Tell a clean curated joke."""
        setup, punchline = random.choice(CLEAN_JOKES)
        await ctx.send(embed=make_embed("Joke", f"{setup}\n\n||{punchline}||", COLOR_WARNING))

    # ---------------------------------------------------------------------------
    # REPORTS
    # ---------------------------------------------------------------------------

    async def _get_report_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        return channel

    def _build_report_embed(
        self,
        *,
        kind: str,
        ctx,
        description: str,
        recent_messages: str | None = None,
    ):
        is_bug = kind == "bug"
        title = "Bug Report" if is_bug else "Feature Request"
        status_label, status_emoji, _ = _status_map(kind)["new"]
        embed = discord.Embed(
            title=title,
            color=COLOR_WARNING if is_bug else COLOR_DEFAULT,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Status", value=f"{status_emoji} **{status_label}**", inline=True)
        embed.add_field(name="Reported By", value=ctx.author.mention, inline=True)
        embed.add_field(
            name="Location",
            value=f"**Server:** {ctx.guild.name}\n**Channel:** {ctx.channel.mention}",
            inline=False,
        )
        embed.add_field(name="Message", value=_truncate_text(description, 1024), inline=False)
        if is_bug:
            embed.add_field(
                name="Last 5 Messages Before Report",
                value=recent_messages or "(No recent messages available.)",
                inline=False,
            )
        embed.add_field(name="Report Command", value=f"[Jump to command]({ctx.message.jump_url})", inline=False)
        embed.set_footer(text=f"Report ID source message: {ctx.message.id}")
        return embed

    def _build_tracking_embed(self, public_embed: discord.Embed, report_message: discord.Message):
        tracking_embed = public_embed.copy()
        tracking_embed.title = f"{public_embed.title} Tracking"
        tracking_embed.add_field(
            name="Public Report Tracking Card",
            value=(
                f"[Jump to report]({report_message.jump_url})\n"
                f"Channel ID: `{report_message.channel.id}`\n"
                f"Message ID: `{report_message.id}`"
            ),
            inline=False,
        )
        return tracking_embed

    async def _format_recent_messages(self, ctx):
        try:
            newest_messages = [
                message
                async for message in ctx.channel.history(
                    limit=5,
                    before=ctx.message,
                )
            ]
        except (discord.Forbidden, discord.HTTPException):
            return "(I could not read message history in that channel.)"
        messages = list(reversed(newest_messages))
        if not messages:
            return "(No prior messages in this channel.)"

        lines = []
        for message in messages:
            author = getattr(message.author, "display_name", str(message.author))
            content = message.clean_content or ""
            if message.attachments:
                content = f"{content} [{len(message.attachments)} attachment(s)]".strip()
            if message.embeds:
                content = f"{content} [{len(message.embeds)} embed(s)]".strip()
            content = content or "(no text content)"
            lines.append(f"**{author}:** {_truncate_text(content, 160)}")
        return _truncate_text("\n".join(lines), 1024)

    async def _submit_report(self, ctx, *, kind: str, description: str):
        if ctx.guild is None:
            return await ctx.send("Reports need server/channel context, so run this in the server where it happened.")
        if not description.strip():
            command = "bugreport" if kind == "bug" else "featurerequest"
            return await ctx.send(f"Usage: `{PREFIX}{command} <description>`")

        channel_id = shared.runtime_settings.get("bug_report_channel_id") if kind == "bug" else shared.runtime_settings.get("feature_request_channel_id")
        tracking_channel_id = shared.runtime_settings.get("request_tracking_channel_id")
        if not channel_id or not tracking_channel_id:
            return await ctx.send("Report channels aren't configured yet.")
        try:
            report_channel = await self._get_report_channel(channel_id)
            tracking_channel = await self._get_report_channel(tracking_channel_id)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            return await ctx.send("I couldn't reach the report channel. Matt may need to check my channel access.")

        recent_messages = await self._format_recent_messages(ctx) if kind == "bug" else None
        embed = self._build_report_embed(
            kind=kind,
            ctx=ctx,
            description=description,
            recent_messages=recent_messages,
        )
        try:
            report_message = await report_channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await report_message.add_reaction(_status_map(kind)["new"][1])
            tracking_embed = self._build_tracking_embed(embed, report_message)
            tracking_message = await tracking_channel.send(
                embed=tracking_embed,
                view=ReportStatusView(kind),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await tracking_message.add_reaction(_status_map(kind)["new"][1])
        except discord.HTTPException:
            return await ctx.send("I couldn't post that report. Matt may need to check my report-channel permissions.")

        if kind == "bug":
            await ctx.send(
                f"Sorry you ran into an issue! Track the status of your report here: {report_message.jump_url}"
            )
        else:
            await ctx.send(
                f"Thank you for your feedback! Track the status of your request here: {report_message.jump_url}"
            )

    @commands.command(aliases=["bug", "issue", "report"])
    async def bugreport(self, ctx, *, description: str = ""):
        """Report a bug with server, channel, reporter, and recent context."""
        await self._submit_report(ctx, kind="bug", description=description)

    @commands.command(aliases=["feature", "request", "fr", "feat"])
    async def featurerequest(self, ctx, *, description: str = ""):
        """Request a feature with status tracking."""
        await self._submit_report(ctx, kind="feature", description=description)
    
    # ---------------------------------------------------------------------------
    # ON THIS DAY
    # ---------------------------------------------------------------------------

    @commands.command()
    async def onthisday(self, ctx):
        """Get a random historical event that happened on this day."""
        today = datetime.now(CENTRAL_TZ)
        url = f"https://byabbe.se/on-this-day/{today.month}/{today.day}/events.json"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return await ctx.send("Couldn't fetch historical events right now.")
                    data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return await ctx.send("Couldn't fetch historical events right now.")

        events = data.get("events", [])
        if not events:
            return await ctx.send("No events found for today!")
    
        event = random.choice(events)
        year = event.get("year", "???")
        desc = event.get("description", "No description available.")
    
        embed = make_embed(
            f"📜 On This Day — {today.strftime('%B %d')}",
            f"**{year}**: {desc}", COLOR_ORANGE)
        await ctx.send(embed=embed)
    
    # ---------------------------------------------------------------------------
    # NICKNAME CHANGE
    # ---------------------------------------------------------------------------

    @commands.command()
    @commands.guild_only()
    async def changenick(self, ctx, member: discord.Member, *, new_nick: str):
        """Pay coins to change someone's nickname for 24 hours."""
        if member.id == ctx.author.id:
            return await ctx.send("You can't change your own nickname with this!")
        if member.bot:
            return await ctx.send("You can't rename bots!")
    
        bal = get_balance(ctx.author.id)
        if bal < NICKNAME_COST:
            return await ctx.send(embed=make_embed("❌ Not Enough Coins",
                f"Changing a nickname costs **{NICKNAME_COST}** coins.\nYou have **{bal}**.", COLOR_ERROR))
    
        # If this user is already under an active changenick, preserve the
        # truly-original nick from the existing row instead of capturing the
        # current (already-overridden) display name.
        existing = shared.db.execute(
            "SELECT original_nick FROM nick_changes WHERE guild_id = ? AND target_id = ? "
            "ORDER BY id ASC LIMIT 1",
            (ctx.guild.id, member.id),
        ).fetchone()
        original_nick = existing[0] if existing else member.display_name

        try:
            await member.edit(nick=new_nick)
        except discord.Forbidden:
            return await ctx.send("I don't have permission to change that user's nickname.")

        update_balance(ctx.author.id, -NICKNAME_COST)
        expires = datetime.now(timezone.utc) + timedelta(hours=NICKNAME_DURATION_HOURS)

        # Replace any prior pending row(s) for this target so the restore task
        # only fires once and uses the preserved original nick above.
        shared.db.execute(
            "DELETE FROM nick_changes WHERE guild_id = ? AND target_id = ?",
            (ctx.guild.id, member.id),
        )
        shared.db.execute(
            "INSERT INTO nick_changes (guild_id, target_id, original_nick, expires_at) VALUES (?, ?, ?, ?)",
            (ctx.guild.id, member.id, original_nick, expires.isoformat())
        )
        shared.db.commit()
    
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed("✏️ Nickname Changed!",
            f"**{original_nick}** is now **{new_nick}** for {NICKNAME_DURATION_HOURS} hours.\n"
            f"Cost: **{NICKNAME_COST}** coins | Balance: **{new_bal}**", COLOR_SUCCESS))
    
    # ---------------------------------------------------------------------------
    # NICKNAME RESTORE TASK
    # ---------------------------------------------------------------------------

    @tasks.loop(minutes=5)
    async def restore_nicknames(self):
        """Check for expired nickname changes and restore them."""
        now = datetime.now(timezone.utc)
        rows = shared.db.execute("SELECT id, guild_id, target_id, original_nick, expires_at FROM nick_changes").fetchall()
        for row_id, guild_id, target_id, original_nick, expires_at in rows:
            expires = datetime.fromisoformat(expires_at)
            if now >= expires:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    member = guild.get_member(target_id)
                    if member:
                        try:
                            await member.edit(nick=original_nick)
                        except discord.Forbidden:
                            pass
                shared.db.execute("DELETE FROM nick_changes WHERE id = ?", (row_id,))
        shared.db.commit()
    
    # ---------------------------------------------------------------------------
    # QUOTES
    # ---------------------------------------------------------------------------

    @commands.command()
    @commands.guild_only()
    async def quote(self, ctx):
        """Save a quote by replying to a message."""
        if not ctx.message.reference:
            return await ctx.send(f"Reply to a message with `{PREFIX}quote` to save it.")
        try:
            msg = ctx.message.reference.resolved or await ctx.channel.fetch_message(ctx.message.reference.message_id)
        except Exception:
            return await ctx.send("Couldn't fetch that message.")
        if not msg.content:
            return await ctx.send("That message has no text content.")
        now = datetime.now(CENTRAL_TZ).strftime("%Y-%m-%d")
        shared.db.execute(
            "INSERT INTO quotes (guild_id, quoted_user_id, quoted_user_name, content, saved_by, saved_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ctx.guild.id, msg.author.id, msg.author.display_name, msg.content, ctx.author.id, now)
        )
        shared.db.commit()
        await ctx.send(format_quote(msg.content, msg.author.mention, now))
    

    @commands.command()
    @commands.guild_only()
    async def quotes(self, ctx, flag: str = ""):
        """Show recent quotes. Admin can use .quotes ids to show IDs."""
        show_ids = flag.lower() == "ids" and ctx.author.id == ADMIN_ID
        rows = shared.db.execute(
            "SELECT id, quoted_user_name, content, saved_at FROM quotes WHERE guild_id = ? ORDER BY id DESC LIMIT 10",
            (ctx.guild.id,)
        ).fetchall()
        if not rows:
            return await ctx.send("No quotes saved yet.")
        prefix_fn = lambda qid: f"**#{qid}** " if show_ids else ""
        lines = [format_quote(content, name, date, prefix_fn(qid)) for qid, name, content, date in rows]
        await ctx.send("\n".join(lines))
    

    @commands.command()
    @commands.guild_only()
    async def unquote(self, ctx, quote_id: int):
        """Delete a quote by ID (admin only)."""
        if ctx.author.id != ADMIN_ID:
            return
        row = shared.db.execute("SELECT id FROM quotes WHERE id = ? AND guild_id = ?", (quote_id, ctx.guild.id)).fetchone()
        if not row:
            return await ctx.send(f"Quote #{quote_id} not found.")
        shared.db.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
        shared.db.commit()
        await ctx.send(f"Quote #{quote_id} deleted.")
    
    # ---------------------------------------------------------------------------
    # ADMIN
    # ---------------------------------------------------------------------------

    async def _set_kids_mode(self, ctx, action: str = "status"):
        if ctx.author.id != ADMIN_ID:
            return
        if ctx.guild is None:
            return await ctx.send("Kids mode is server-specific. Run this in the target server.")

        value = action.strip().lower()
        if value in {"", "status"}:
            enabled = is_kids_mode_guild(ctx.guild.id)
            state = "ON" if enabled else "OFF"
            return await ctx.send(embed=make_embed(
                "Kids Mode",
                f"Kids mode is **{state}** for **{ctx.guild.name}**.\n\n{KIDS_MODE_SUMMARY}",
                COLOR_DEFAULT,
            ))
        if value not in {"on", "off"}:
            return await ctx.send(f"Usage: `{PREFIX}kidsmode <on|off|status>`")

        enabled = value == "on"
        set_kids_mode_guild(ctx.guild.id, enabled)
        state = "ON" if enabled else "OFF"
        await ctx.send(embed=make_embed(
            "Kids Mode Updated",
            f"Kids mode is now **{state}** for **{ctx.guild.name}**.\n\n{KIDS_MODE_SUMMARY}",
            COLOR_SUCCESS if enabled else COLOR_WARNING,
        ))

    
    

    
    

    
    

    
    

    @commands.command()
    async def settings(self, ctx, section: str = "", *args):
        """Show runtime settings or manage sections (example: .settings gamble on)."""
        if ctx.author.id != ADMIN_ID:
            return

        if section:
            sec = section.strip().lower()
            if sec in {"kids", "kidsmode"}:
                action = args[0] if args else "status"
                return await self._set_kids_mode(ctx, action)

            if sec == "passive":
                keys = {
                    "unsolicited": "unsolicited_chance_pct",
                    "silasbanter": "silas_banter_chance_pct",
                    "silasreact": "silas_react_chance_pct",
                }
                if not args:
                    lines = [
                        f"Unsolicited AI: **{shared.runtime_settings.get('unsolicited_chance_pct', 0)}%**",
                        f"Silas banter: **{shared.runtime_settings.get('silas_banter_chance_pct', 0)}%**",
                        f"Silas react: **{shared.runtime_settings.get('silas_react_chance_pct', 0)}%**",
                    ]
                    return await ctx.send("\n".join(lines))
                target = args[0].strip().lower()
                if target not in keys:
                    return await ctx.send(
                        f"Usage: `{PREFIX}settings passive <unsolicited|silasbanter|silasreact> <0-100>`"
                    )
                if len(args) < 2:
                    current = shared.runtime_settings.get(keys[target], 0)
                    return await ctx.send(f"`{target}` is **{current}%**")
                try:
                    value = int(args[1])
                except ValueError:
                    return await ctx.send("Value must be an integer percent (0-100).")
                value = max(0, min(100, value))
                shared.runtime_settings[keys[target]] = value
                shared._save_json_setting(keys[target], value)
                return await ctx.send(f"`{target}` is now **{value}%**.")

            if sec == "gamble":
                if not args:
                    enabled = shared.runtime_settings.get("gary_gamble_enabled", False)
                    channel_id = shared.runtime_settings.get("gary_gamble_channel_id")
                    report_id = shared.runtime_settings.get("gary_gamble_report_channel_id")
                    channel_text = f"<#{int(channel_id)}>" if channel_id else "(not set)"
                    report_text = f"<#{int(report_id)}>" if report_id else "(fallback)"
                    state_text = "ON" if enabled else "OFF"
                    return await ctx.send(
                        f"Gary gambling: **{state_text}** | Channel: {channel_text} | Report: {report_text}"
                    )

                action = args[0].strip().lower()
                if ctx.guild and is_kids_mode_guild(ctx.guild.id) and action not in {"off", "status"}:
                    return await ctx.send("Gary autonomous gambling cannot be configured from a kids-mode server.")
                if action == "on":
                    shared.runtime_settings["gary_gamble_enabled"] = True
                    shared.runtime_settings["gary_gamble_channel_id"] = ctx.channel.id
                    shared._save_json_setting("gary_gamble_enabled", True)
                    shared._save_json_setting("gary_gamble_channel_id", ctx.channel.id)
                    return await ctx.send(
                        f"Gary autonomous gambling is now **ON** in {ctx.channel.mention}."
                    )
                if action == "off":
                    shared.runtime_settings["gary_gamble_enabled"] = False
                    shared._save_json_setting("gary_gamble_enabled", False)
                    return await ctx.send("Gary autonomous gambling is now **OFF**.")
                if action == "status":
                    enabled = shared.runtime_settings.get("gary_gamble_enabled", False)
                    channel_id = shared.runtime_settings.get("gary_gamble_channel_id")
                    report_id = shared.runtime_settings.get("gary_gamble_report_channel_id")
                    channel_text = f"<#{int(channel_id)}>" if channel_id else "(not set)"
                    report_text = f"<#{int(report_id)}>" if report_id else "(fallback)"
                    state_text = "ON" if enabled else "OFF"
                    return await ctx.send(
                        f"Gary gambling: **{state_text}** | Channel: {channel_text} | Report: {report_text}"
                    )
                if action == "channel":
                    target = ctx.channel
                    if ctx.message.channel_mentions:
                        target = ctx.message.channel_mentions[0]
                    shared.runtime_settings["gary_gamble_channel_id"] = target.id
                    shared._save_json_setting("gary_gamble_channel_id", target.id)
                    return await ctx.send(f"Gary gambling channel set to {target.mention}.")
                if action == "now":
                    ai_cog = self.bot.get_cog("AICog")
                    if ai_cog is None:
                        return await ctx.send("AICog is not loaded.")
                    result = await ai_cog.run_gamble_step(bypass_cooldown=True)
                    return await ctx.send(f"Gamble action: {result}")
                if action == "report":
                    target = ctx.channel
                    if ctx.message.channel_mentions:
                        target = ctx.message.channel_mentions[0]
                    shared.runtime_settings["gary_gamble_report_channel_id"] = target.id
                    shared._save_json_setting("gary_gamble_report_channel_id", target.id)
                    return await ctx.send(f"Gamble report channel set to {target.mention}.")
                return await ctx.send(
                    f"Usage: `{PREFIX}settings gamble <on|off|status|now|channel|report [#channel]>`"
                )

            if sec == "weather":
                channel_id = shared.runtime_settings.get("weather_alert_channel_id")
                city = shared.runtime_settings.get("weather_alert_city", "Champaign")
                channel_text = f"<#{int(channel_id)}>" if channel_id else "(not set)"
                if not args:
                    state = "ON" if channel_id else "OFF"
                    return await ctx.send(
                        f"Daily weather alert: **{state}** in {channel_text} for **{city}** (8 AM Central)"
                    )
                action = args[0].strip().lower()
                if action == "on":
                    target = ctx.channel
                    if ctx.message.channel_mentions:
                        target = ctx.message.channel_mentions[0]
                    shared.runtime_settings["weather_alert_channel_id"] = target.id
                    shared._save_json_setting("weather_alert_channel_id", target.id)
                    return await ctx.send(
                        f"Daily weather alert is now **ON** in {target.mention} for **{city}** at 8 AM Central."
                    )
                if action == "off":
                    shared.runtime_settings["weather_alert_channel_id"] = None
                    shared._save_json_setting("weather_alert_channel_id", None)
                    return await ctx.send("Daily weather alert is now **OFF**.")
                if action == "status":
                    state = "ON" if channel_id else "OFF"
                    return await ctx.send(
                        f"Daily weather alert: **{state}** in {channel_text} for **{city}**"
                    )
                if action == "city":
                    if len(args) < 2:
                        return await ctx.send(f"Usage: `{PREFIX}settings weather city <city name>`")
                    new_city = " ".join(args[1:]).strip()
                    shared.runtime_settings["weather_alert_city"] = new_city
                    shared._save_json_setting("weather_alert_city", new_city)
                    return await ctx.send(f"Weather alert city set to **{new_city}**.")
                return await ctx.send(
                    f"Usage: `{PREFIX}settings weather <on [#channel]|off|status|city <name>>`"
                )

            if sec == "deadchat":
                if not args:
                    state = "ON" if shared.runtime_settings.get("dead_chat_enabled", False) else "OFF"
                    return await ctx.send(f"Dead chat is **{state}**.")
                action = args[0].strip().lower()
                if action == "status":
                    state = "ON" if shared.runtime_settings.get("dead_chat_enabled", False) else "OFF"
                    return await ctx.send(f"Dead chat is **{state}**.")
                if action not in {"on", "off"}:
                    return await ctx.send(f"Usage: `{PREFIX}settings deadchat <on|off|status>`")
                enabled = action == "on"
                shared.runtime_settings["dead_chat_enabled"] = enabled
                shared._save_json_setting("dead_chat_enabled", enabled)
                shared.last_message_time.clear()
                shared.dead_chat_stage.clear()
                return await ctx.send(f"Dead chat is now **{action.upper()}**.")

            if sec in {"commands", "command"}:
                if not args:
                    toggles = shared.runtime_settings.get("command_toggles", {})
                    disabled = sorted(name for name, en in toggles.items() if not en)
                    disabled_str = ", ".join(f"`{PREFIX}{c}`" for c in disabled) if disabled else "None"
                    return await ctx.send(f"Disabled commands: {disabled_str}")
                command_input = args[0].strip().lower()
                if len(args) < 2:
                    target = self.bot.get_command(command_input)
                    if target is None:
                        return await ctx.send(f"Unknown command: `{command_input}`")
                    canonical_name = target.name.lower()
                    toggles = shared.runtime_settings.get("command_toggles", {})
                    state = "ON" if toggles.get(canonical_name, True) else "OFF"
                    return await ctx.send(f"`{PREFIX}{canonical_name}` is **{state}**.")
                state_arg = args[1].strip().lower()
                if state_arg not in {"on", "off"}:
                    return await ctx.send(f"Usage: `{PREFIX}settings commands <command> <on|off>`")
                target = self.bot.get_command(command_input)
                if target is None:
                    return await ctx.send(f"Unknown command: `{command_input}`")
                canonical_name = target.name.lower()
                if canonical_name in PROTECTED_ADMIN_COMMANDS:
                    return await ctx.send("That command cannot be disabled.")
                toggles = shared.runtime_settings.get("command_toggles", {})
                toggles[canonical_name] = (state_arg == "on")
                shared.runtime_settings["command_toggles"] = toggles
                shared._save_json_setting("command_toggles", toggles)
                return await ctx.send(f"`{PREFIX}{canonical_name}` is now **{state_arg.upper()}**.")

            if sec in {"features", "feature"}:
                rules = shared.runtime_settings.get("feature_channel_rules", {})
                if not args:
                    if not rules:
                        return await ctx.send("No feature channel rules set.")
                    lines = []
                    for feat, rule in sorted(rules.items()):
                        mode = rule.get("mode", "all")
                        ch_str = ", ".join(f"<#{cid}>" for cid in rule.get("channels", [])) or "(none)"
                        lines.append(f"`{feat}`: **{mode}** {ch_str}")
                    return await ctx.send("\n".join(lines))
                feat = normalize_feature_name(args[0])
                if len(args) < 2:
                    rule = rules.get(feat)
                    if not rule:
                        return await ctx.send(f"`{feat}`: **all** (no rule set)")
                    mode = rule.get("mode", "all")
                    ch_str = ", ".join(f"<#{cid}>" for cid in rule.get("channels", [])) or "(none)"
                    return await ctx.send(f"`{feat}`: **{mode}** {ch_str}")
                action = args[1].strip().lower()
                if action in {"all", "off", "whitelist", "blacklist"}:
                    existing = rules.get(feat, {"channels": []})
                    existing["mode"] = action
                    existing["channels"] = [int(c) for c in existing.get("channels", [])]
                    rules[feat] = existing
                    shared.runtime_settings["feature_channel_rules"] = rules
                    shared._save_json_setting("feature_channel_rules", rules)
                    return await ctx.send(f"`{feat}` mode is now **{action}**.")
                if action in {"add", "remove", "clear"}:
                    rule = rules.get(feat, {"mode": "all", "channels": []})
                    channels = {int(c) for c in rule.get("channels", [])}
                    if action == "clear":
                        channels.clear()
                    else:
                        mentioned = ctx.message.channel_mentions
                        if not mentioned:
                            return await ctx.send(
                                f"Mention one or more channels. Example: `{PREFIX}settings features {feat} {action} #general`"
                            )
                        ids = {c.id for c in mentioned}
                        channels = channels | ids if action == "add" else channels - ids
                    rule["channels"] = sorted(channels)
                    rules[feat] = rule
                    shared.runtime_settings["feature_channel_rules"] = rules
                    shared._save_json_setting("feature_channel_rules", rules)
                    ch_str = ", ".join(f"<#{cid}>" for cid in rule["channels"]) or "(none)"
                    return await ctx.send(f"`{feat}` channels: {ch_str}")
                return await ctx.send(
                    f"Usage: `{PREFIX}settings features <feature> <all|off|whitelist|blacklist|add|remove|clear [#channels]>`"
                )

            if sec == "blackjack":
                economy_cog = self.bot.get_cog("EconomyCog")
                if economy_cog is None:
                    return await ctx.send("Economy cog is not loaded.")
                if not args:
                    ruleset = str(shared.runtime_settings.get("bj_ruleset", "realistic")).upper()
                    hint = "ON" if shared.runtime_settings.get("bj_basic_hint_enabled", True) else "OFF"
                    return await ctx.send(f"BJ Ruleset: **{ruleset}** | Hints: **{hint}**")
                sub = args[0].strip().lower()
                rest = args[1].strip().lower() if len(args) > 1 else "status"
                if sub in {"ruleset", "rules"}:
                    return await economy_cog.bjruleset(ctx, rest)
                if sub == "hint":
                    return await economy_cog.bjhint(ctx, rest)
                return await ctx.send(f"Usage: `{PREFIX}settings blackjack <ruleset|hint> [value]`")

            if sec == "channels":
                _CHANNEL_KEYS = {
                    "guildjoin": "guild_join_report_channel_id",
                    "kidslog": "kids_interaction_log_channel_id",
                    "bugreport": "bug_report_channel_id",
                    "featurerequest": "feature_request_channel_id",
                    "tracking": "request_tracking_channel_id",
                }
                if not args:
                    lines = []
                    for name, key in _CHANNEL_KEYS.items():
                        val = shared.runtime_settings.get(key)
                        lines.append(f"`{name}`: {f'<#{val}>' if val else '(not set)'}")
                    return await ctx.send("\n".join(lines))
                name = args[0].strip().lower()
                if name not in _CHANNEL_KEYS:
                    opts = ", ".join(f"`{k}`" for k in _CHANNEL_KEYS)
                    return await ctx.send(f"Unknown channel: `{name}`. Options: {opts}")
                key = _CHANNEL_KEYS[name]
                if len(args) < 2:
                    val = shared.runtime_settings.get(key)
                    return await ctx.send(f"`{name}`: {f'<#{val}>' if val else '(not set)'}")
                if args[1].strip().lower() == "off":
                    shared.runtime_settings[key] = None
                    shared._save_json_setting(key, None)
                    return await ctx.send(f"`{name}` channel cleared.")
                mentioned = ctx.message.channel_mentions
                if not mentioned:
                    return await ctx.send(
                        f"Mention a channel or use `off`. Example: `{PREFIX}settings channels {name} #general`"
                    )
                shared.runtime_settings[key] = mentioned[0].id
                shared._save_json_setting(key, mentioned[0].id)
                return await ctx.send(f"`{name}` channel set to {mentioned[0].mention}.")

            if sec == "silas":
                silas_id = shared.runtime_settings.get("silas_bot_id")
                banter = shared.runtime_settings.get("silas_banter_chance_pct", 0)
                react = shared.runtime_settings.get("silas_react_chance_pct", 0)
                if not args:
                    id_text = f"`{silas_id}`" if silas_id else "(not set)"
                    return await ctx.send(
                        f"Silas bot ID: {id_text}\nBanter: **{banter}%** | React: **{react}%**"
                    )
                sub = args[0].strip().lower()
                if sub == "id":
                    if len(args) < 2:
                        id_text = f"`{silas_id}`" if silas_id else "(not set)"
                        return await ctx.send(f"Silas bot ID: {id_text}")
                    try:
                        new_id = int(args[1].strip())
                    except ValueError:
                        return await ctx.send("Bot ID must be an integer.")
                    shared.runtime_settings["silas_bot_id"] = new_id
                    shared._save_json_setting("silas_bot_id", new_id)
                    return await ctx.send(f"Silas bot ID set to `{new_id}`.")
                if sub in {"banter", "react"}:
                    key = f"silas_{sub}_chance_pct"
                    if len(args) < 2:
                        val = shared.runtime_settings.get(key, 0)
                        return await ctx.send(f"Silas {sub}: **{val}%**")
                    try:
                        value = max(0, min(100, int(args[1])))
                    except ValueError:
                        return await ctx.send("Value must be an integer (0-100).")
                    shared.runtime_settings[key] = value
                    shared._save_json_setting(key, value)
                    return await ctx.send(f"Silas {sub} is now **{value}%**.")
                return await ctx.send(f"Usage: `{PREFIX}settings silas <id|banter|react> [value]`")

            return await ctx.send(f"Unknown settings section: `{sec}`")

        dead_chat_state = "ON" if shared.runtime_settings.get("dead_chat_enabled", True) else "OFF"
        kids_mode_state = "ON" if ctx.guild and is_kids_mode_guild(ctx.guild.id) else "OFF"
        gary_gamble_state = "ON" if shared.runtime_settings.get("gary_gamble_enabled", False) else "OFF"
        bj_ruleset = str(shared.runtime_settings.get("bj_ruleset", "realistic")).upper()
        bj_hint_state = "ON" if shared.runtime_settings.get("bj_basic_hint_enabled", True) else "OFF"
        gary_gamble_channel = shared.runtime_settings.get("gary_gamble_channel_id")
        gary_gamble_channel_text = f"<#{int(gary_gamble_channel)}>" if gary_gamble_channel else "(not set)"
        disabled = sorted(
            name for name, enabled in shared.runtime_settings.get("command_toggles", {}).items() if not enabled
        )
        disabled_str = ", ".join(f"`{PREFIX}{c}`" for c in disabled) if disabled else "None"
    
        rule_lines = []
        for feature, rule in sorted(shared.runtime_settings.get("feature_channel_rules", {}).items()):
            mode = rule.get("mode", "all")
            channels = rule.get("channels", [])
            if channels:
                channel_str = ", ".join(f"<#{int(cid)}>" for cid in channels)
            else:
                channel_str = "(none)"
            rule_lines.append(f"`{feature}`: **{mode}** {channel_str}")
        rules_str = "\n".join(rule_lines) if rule_lines else "No feature channel rules set."
    
        unsolicited_pct = shared.runtime_settings.get("unsolicited_chance_pct", 0)
        silas_banter_pct = shared.runtime_settings.get("silas_banter_chance_pct", 0)
        silas_react_pct = shared.runtime_settings.get("silas_react_chance_pct", 0)
        passive_value = (
            f"Unsolicited: **{unsolicited_pct}%**\n"
            f"Silas banter: **{silas_banter_pct}%**\n"
            f"Silas react: **{silas_react_pct}%**"
        )

        embed = discord.Embed(title="Runtime Settings", color=COLOR_DEFAULT)
        embed.add_field(name="Kids Mode", value=kids_mode_state, inline=True)
        embed.add_field(name="Dead Chat", value=dead_chat_state, inline=True)
        embed.add_field(name="Gary Gamble", value=f"{gary_gamble_state}\n{gary_gamble_channel_text}", inline=True)
        weather_channel_id = shared.runtime_settings.get("weather_alert_channel_id")
        weather_state = "ON" if weather_channel_id else "OFF"
        weather_text = f"<#{int(weather_channel_id)}>" if weather_channel_id else "(not set)"
        weather_city = shared.runtime_settings.get("weather_alert_city", "Champaign")
        embed.add_field(name="Weather Alert", value=f"{weather_state}\n{weather_text}\n{weather_city}", inline=True)
        _channel_keys = (
            "guild_join_report_channel_id", "kids_interaction_log_channel_id",
            "bug_report_channel_id", "feature_request_channel_id", "request_tracking_channel_id",
        )
        channels_set = sum(1 for k in _channel_keys if shared.runtime_settings.get(k))
        silas_id = shared.runtime_settings.get("silas_bot_id")
        embed.add_field(name="BJ Ruleset", value=bj_ruleset, inline=True)
        embed.add_field(name="BJ Hint", value=bj_hint_state, inline=True)
        embed.add_field(name="Channels", value=f"{channels_set}/5 set (`.settings channels`)", inline=True)
        embed.add_field(name="Silas ID", value=f"`{silas_id}`" if silas_id else "(not set)", inline=True)
        embed.add_field(name="Passive AI", value=passive_value, inline=False)
        embed.add_field(name="Disabled Commands", value=disabled_str, inline=False)
        embed.add_field(
            name="Feature Rules",
            value=rules_str[:1024],
            inline=False,
        )
        embed.set_footer(text="Command features use cmd:<command> (example: cmd:ask)")
        await ctx.send(embed=embed)
    
    

    @commands.command()
    async def restart(self, ctx):
        """Admin only: restart this bot process."""
        if ctx.author.id != ADMIN_ID:
            return
        await ctx.send("Restarting bot process now...")
        shared.db.commit()
        python = sys.executable
        os.execv(python, [python] + sys.argv)
    
    

    @commands.command()
    async def clear(self, ctx, count: str = ""):
        """Admin only: delete the last <n> messages from Gary."""
        try:
            if ctx.author.id != ADMIN_ID:
                return
            
            if not count.strip():
                await ctx.send(f"Usage: `{PREFIX}clear <n>` (deletes the last n messages from Gary)")
                return
            
            try:
                n = int(count)
            except ValueError:
                await ctx.send(f"'{count}' is not a valid number.")
                return
            
            if n <= 0:
                await ctx.send("Number must be greater than 0.")
                return
            
            if n > 100:
                await ctx.send("Cannot delete more than 100 messages at once (safety limit).")
                return
            
            deleted_count = 0
            try:
                async for message in ctx.channel.history(limit=500):
                    if deleted_count >= n:
                        break
                    if message.author.id == self.bot.user.id:
                        try:
                            await message.delete()
                            deleted_count += 1
                        except discord.Forbidden:
                            await ctx.send(f"❌ Permission denied deleting a message (deleted {deleted_count} before error).")
                            return
                        except discord.HTTPException:
                            await ctx.send(f"❌ Error deleting a message (deleted {deleted_count} before error).")
                            return
            except discord.Forbidden:
                await ctx.send("❌ I don't have permission to read message history in this channel.")
                return
        finally:
            # Always try to delete the command message itself
            try:
                await ctx.message.delete()
            except (discord.HTTPException, discord.Forbidden):
                pass
    
    

    @commands.command(aliases=["botstats"])
    async def botstat(self, ctx):
        """Admin only: show runtime bot stats."""
        if ctx.author.id != ADMIN_ID:
            return
        now = datetime.now(timezone.utc)
        if shared.bot_start_time:
            delta = now - shared.bot_start_time
            uptime_seconds = int(delta.total_seconds())
            days, rem = divmod(uptime_seconds, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, _ = divmod(rem, 60)
            uptime = f"{days}d {hours}h {minutes}m"
            up_minutes = max(delta.total_seconds() / 60.0, 1e-9)
        else:
            uptime = "Unknown"
            up_minutes = 1.0

        total_cmds = sum(shared.command_usage.values())
        messages = shared.messages_seen
        msg_rate = messages / up_minutes

        guilds = self.bot.guilds
        text_channels = 0
        voice_channels = 0
        for guild in guilds:
            for ch in guild.channels:
                if isinstance(ch, discord.TextChannel):
                    text_channels += 1
                elif isinstance(ch, discord.VoiceChannel):
                    voice_channels += 1

        unsolicited_pct = shared.runtime_settings.get("unsolicited_chance_pct", 0)
        passive_enabled = f"{unsolicited_pct}%" if unsolicited_pct > 0 else "Disabled"
        ai_status = "Configured" if OLLAMA_URL else "Unavailable"

        embed = discord.Embed(title="📊 Bot Stats", color=COLOR_DEFAULT)
        embed.add_field(
            name="🤖 Bot",
            value=f"{self.bot.user}\n`{self.bot.user.id}`",
            inline=False,
        )
        embed.add_field(name="💬 Commands Ran", value=f"{total_cmds} commands", inline=True)
        embed.add_field(name="📨 Messages", value=f"{messages} ({msg_rate:.2f}/min)", inline=True)
        embed.add_field(name="⏱️ Uptime", value=uptime, inline=True)
        embed.add_field(
            name="🌐 Presence",
            value=(
                f"{len(guilds)} servers\n"
                f"{text_channels} text channels\n"
                f"{voice_channels} voice channels"
            ),
            inline=False,
        )
        embed.add_field(
            name="🟢 AI Status",
            value=(
                f"Status: {ai_status} · Passive: {passive_enabled}\n"
                f"Ask model: {OLLAMA_REASONING_MODEL}\n"
                f"Roleplay model: {OLLAMA_MODEL}"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Latency: {self.bot.latency * 1000:.0f}ms")
        await ctx.send(embed=embed)
    

    @commands.command()
    async def invite(self, ctx, mode: str = ""):
        """Generate an invite link with the permissions the bot needs."""
        kids_invite = mode.strip().lower() in {"kid", "kids", "kidsmode"}
        perms = discord.Permissions(
            send_messages=True,
            embed_links=True,
            add_reactions=True,
            manage_messages=not kids_invite,
            manage_nicknames=not kids_invite,
            read_message_history=True,
            read_messages=True,
        )
        link = discord.utils.oauth_url(self.bot.user.id, permissions=perms)
        if kids_invite:
            description = (
                f"[Click here to invite me in a low-permission setup.]({link})\n\n"
                "This low-permission invite automatically enables kids mode when Gary joins. "
                "Matt will also get a join report with the server ID and an un-force SQL fallback.\n\n"
                f"{KIDS_MODE_SUMMARY}"
            )
            return await ctx.send(embed=make_embed("Kids Mode Invite", description, COLOR_SUCCESS))
        await ctx.send(embed=make_embed("🔗 Invite Link", f"[Click here to invite me!]({link})"))
    

    @commands.command()
    async def give(self, ctx, member: discord.Member, amount: int):
        """Admin only: add coins to a user."""
        if ctx.author.id != ADMIN_ID:
            return
        update_balance(member.id, amount)
        new_bal = get_balance(member.id)
        await ctx.send(f"Gave **{amount}** coins to {member.mention}. New balance: **{new_bal}**")


    @commands.command()
    async def say(self, ctx, *, text: str):
        """Admin only: delete command message and make Gary say text."""
        if ctx.author.id != ADMIN_ID:
            return
        deleted = True
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            deleted = False
        await ctx.send(text)
        if not deleted:
            await ctx.send("I couldn't delete your `.say` command message (missing permissions).")
    
    # ---------------------------------------------------------------------------
    # STATS
    # ---------------------------------------------------------------------------

    @commands.command(aliases=["stat"])
    async def stats(self, ctx, member: discord.Member = None):
        """View your personal stats, or head-to-head game record vs someone."""
        kids = ctx.guild is not None and is_kids_mode_guild(ctx.guild.id)

        if member:
            game_stats = shared.get_game_stats(ctx.author.id, member.id)
            ttt = game_stats["ttt"]
            c4 = game_stats["c4"]
            total = sum(ttt.values()) + sum(c4.values())
            if total == 0:
                return await ctx.send(embed=make_embed(
                    f"vs {member.display_name}",
                    "No games played against this person yet.",
                ))
            return await ctx.send(embed=make_embed(
                f"Head-to-Head vs {member.display_name}",
                f"**Tic-Tac-Toe**: {ttt['win']}W / {ttt['loss']}L / {ttt['draw']}D\n"
                f"**Connect 4**: {c4['win']}W / {c4['loss']}L / {c4['draw']}D",
            ))

        user_id = ctx.author.id
        puzzle = shared.get_puzzle_stats(user_id)
        games = shared.get_game_stats(user_id)
        streak_label = f"**{puzzle['streak']}** day{'s' if puzzle['streak'] != 1 else ''}"
        ttt = games["ttt"]
        c4 = games["c4"]

        embed = discord.Embed(
            title=f"📊 {ctx.author.display_name}'s Stats",
            color=shared.COLOR_DEFAULT,
        )
        embed.add_field(
            name="🧩 Puzzles",
            value=(
                f"Streak: {streak_label}\n"
                f"Total solves: **{puzzle['total_solves']}**\n"
                f"Avg attempts: **{puzzle['avg_attempts']:.1f}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="🎮 Games",
            value=(
                f"TTT: {ttt['win']}W / {ttt['loss']}L / {ttt['draw']}D\n"
                f"C4:  {c4['win']}W / {c4['loss']}L / {c4['draw']}D"
            ),
            inline=True,
        )
        if not kids:
            econ = shared.get_economy_stats(user_id)
            worst = econ["worst_day_loss"]
            embed.add_field(
                name="💰 Economy",
                value=(
                    f"Peak balance: **{econ['peak_balance']:,}**\n"
                    f"Best day: **+{econ['best_day_gain']:,}**\n"
                    f"Worst day: **{worst:,}**"
                ),
                inline=True,
            )
            gamble = shared.get_gambling_stats(user_id)
            cf, sl, bj = gamble["coinflip"], gamble["slots"], gamble["blackjack"]

            def _fmt_gambling(g: dict) -> str:
                sign = "+" if g["net"] >= 0 else ""
                return f"{g['hands']} hands, **{sign}{g['net']:,}**"

            embed.add_field(
                name="🎲 Gambling",
                value=(
                    f"Coinflip: {_fmt_gambling(cf)}\n"
                    f"Slots: {_fmt_gambling(sl)}\n"
                    f"Blackjack: {_fmt_gambling(bj)}"
                ),
                inline=True,
            )
        await ctx.send(embed=embed)

    # ---------------------------------------------------------------------------
    # LEADERBOARD
    # ---------------------------------------------------------------------------

    @commands.command(aliases=["lb", "top"])
    @commands.guild_only()
    async def leaderboard(self, ctx):
        """Show the richest users in the server."""
        rows = shared.db.execute("SELECT user_id, balance FROM users ORDER BY balance DESC").fetchall()
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        count = 0
        for user_id, bal in rows:
            member = ctx.guild.get_member(user_id)
            if member is None:
                continue
            prefix = medals[count] if count < 3 else f"**{count + 1}.**"
            lines.append(f"{prefix} {member.display_name} — **{bal}** coins")
            count += 1
            if count >= 10:
                break
    
        if not lines:
            return await ctx.send("No one has any coins yet!")
        await ctx.send(embed=make_embed("🏆 Leaderboard", "\n".join(lines), COLOR_GOLD))
    
    # ---------------------------------------------------------------------------
    # HELP OVERRIDE
    # ---------------------------------------------------------------------------

    def _alias_line(self, command_name: str) -> str | None:
        command = self.bot.get_command(command_name) if self.bot else None
        if command is None or not command.aliases:
            return None
        aliases = ", ".join(f"`{PREFIX}{alias}`" for alias in command.aliases)
        return f"`{PREFIX}{command.name}` - {aliases}"

    def _alias_lines(self, command_names: list[str]) -> str:
        lines = [line for name in command_names if (line := self._alias_line(name))]
        return "\n".join(lines)

    def _add_alias_field(self, embed, name: str, command_names: list[str]) -> None:
        value = self._alias_lines(command_names)
        if value:
            embed.add_field(name=name, value=value, inline=False)

    @commands.command()
    async def help(self, ctx):
        """Show all commands."""
        embed = discord.Embed(title="Bot Commands", color=COLOR_DEFAULT)
        p = PREFIX
        kids_mode = ctx.guild is not None and is_kids_mode_guild(ctx.guild.id)
        if not kids_mode:
            embed.add_field(name="Economy", value=(
                f"`{p}guess <1-10>` - Guess for a free coin (3x/day)\n"
                f"`{p}puzzle` - Daily puzzle for {PUZZLE_REWARD} coins\n"
                f"`{p}balance` - Check balance\n"
                f"`{p}leaderboard` - Top 10 richest"
            ), inline=False)
        if not kids_mode:
            embed.add_field(name="Gambling", value=(
                f"`{p}coinflip <amt>` - Double or nothing\n"
                f"`{p}slots <amt>` - Slot machine\n"
                f"`{p}blackjack <amt>` - Play blackjack\n"
                f"`{p}hit|stand|double|split|surrender` - Blackjack actions\n"
                f"`{p}bjrules` - Show current blackjack table rules"
            ), inline=False)
        puzzle_help = (
            f"`{p}puzzle` / `{p}solve <answer>` - Practice puzzle"
            if kids_mode
            else f"`{p}puzzle` / `{p}solve <answer>` - Daily puzzle"
        )
        embed.add_field(name="Games", value=(
            f"`{p}ttt @user` - Tic-tac-toe\n"
            f"`{p}c4 @user` - Connect 4\n"
            f"`{p}hangman` - Hangman (plain single letters or `{p}g <guess>`)\n"
            f"`{p}rps <rock|paper|scissors>` - Rock Paper Scissors\n"
            f"`{p}roll [sides]` - Roll a die\n"
            f"`{p}mathgame` / `{p}mathanswer <answer>` - Quick math quiz\n"
            f"`{p}memory` / `{p}memoryanswer <sequence>` - Memory game\n"
            f"`{p}trivia` / `{p}triviaanswer <A-D>` - Trivia\n"
            f"`{p}scramble` / `{p}unscramble <word>` - Word scramble\n"
            f"{puzzle_help}\n"
            f"`{p}timer <seconds>` - Start a timer\n"
            f"`{p}forfeit` - Quit current game"
        ), inline=False)
        embed.add_field(name="Weather", value=f"`{p}weather [city]` - Current weather (defaults to Champaign)\n`{p}weather [city] forecast` - Current weather + daily forecast", inline=False)
        embed.add_field(name="Animals", value=f"`{p}cat` / `{p}dog` - Random pics", inline=False)
        fun_lines = [
            f"`{p}wyr` - Would You Rather",
            f"`{p}joke` - Clean joke",
        ]
        if not kids_mode:
            fun_lines.append(f"`{p}onthisday` - Historical event today")
            fun_lines.append(f"`{p}changenick @user name` - Change nickname ({NICKNAME_COST} coins)")
        embed.add_field(name="Fun", value="\n".join(fun_lines), inline=False)
        if not kids_mode:
            embed.add_field(name="AI", value=(
                f"`{p}ask <question>` - Ask the AI (needs desktop on)\n"
                f"`{p}rp <character>` - Roleplay with Silas\n"
                f"`{p}stoprp` - End roleplay"
            ), inline=False)
        if not kids_mode:
            embed.add_field(name="Info", value=(
                f"`{p}stats` - Bot stats and usage\n"
                f"`{p}invite` / `{p}invite kids` - Get invite link\n"
                f"`{p}alias` - Show command aliases\n"
                f"`{p}bugreport <description>` - Report a bug\n"
                f"`{p}featurerequest <description>` - Request a feature"
            ), inline=False)
        if not kids_mode:
            embed.add_field(name="Quotes", value=(
                f"`{p}quote` - Reply to a message to save it\n"
                f"`{p}quotes` - Show recent quotes"
            ), inline=False)
        await ctx.send(embed=embed)

    @commands.command(aliases=["aliases"])
    async def alias(self, ctx):
        """Show command aliases."""
        embed = discord.Embed(title="Bot Aliases", color=COLOR_DEFAULT)
        kids_mode = ctx.guild is not None and is_kids_mode_guild(ctx.guild.id)
        if not kids_mode:
            self._add_alias_field(embed, "Economy", [
                "guess", "puzzle", "balance", "leaderboard"
            ])
            self._add_alias_field(embed, "Gambling", [
                "coinflip", "slots", "blackjack", "hit", "stand", "double",
                "split", "surrender", "bjrules"
            ])
        self._add_alias_field(embed, "Games", [
            "ttt", "c4", "hangman", "g", "rps", "roll", "mathgame",
            "mathanswer", "memory", "memoryanswer", "trivia", "triviaanswer",
            "scramble", "unscramble", "solve", "timer", "forfeit"
        ])
        self._add_alias_field(embed, "Weather", ["weather"])
        if not kids_mode:
            self._add_alias_field(embed, "Animals", ["cat", "dog"])
        fun_commands = ["wyr", "joke"]
        if not kids_mode:
            fun_commands.extend(["onthisday", "changenick"])
        self._add_alias_field(embed, "Fun", fun_commands)
        if not kids_mode:
            self._add_alias_field(embed, "AI", ["ask", "rp", "stoprp"])
            self._add_alias_field(embed, "Info", [
                "stats", "invite", "bugreport", "featurerequest", "alias"
            ])
            self._add_alias_field(embed, "Quotes", ["quote", "quotes", "unquote"])
        if ctx.author.id == ADMIN_ID:
            self._add_alias_field(embed, "Admin", [
                "adminhelp", "settings", "say", "give", "clear", "restart"
            ])
        await ctx.send(embed=embed)

    @commands.command()
    async def adminhelp(self, ctx):
        """Show admin-only commands."""
        if ctx.author.id != ADMIN_ID:
            return
        p = PREFIX
        embed = discord.Embed(title="Admin Commands", color=COLOR_DEFAULT)
        embed.add_field(name="Runtime", value=(
            f"`{p}settings` - Show all runtime settings\n"
            f"`{p}settings kids <on|off|status>` - Server kids mode\n"
            f"`{p}settings gamble <on|off|status|now|channel|report [#ch]>` - Gary gambling\n"
            f"`{p}settings weather <on [#ch]|off|status|city <name>>` - 8 AM weather alert\n"
            f"`{p}settings deadchat <on|off|status>` - Dead chat callouts\n"
            f"`{p}settings passive <unsolicited|silasbanter|silasreact> <0-100>` - Passive AI\n"
            f"`{p}settings blackjack <ruleset|hint> [value]` - Blackjack settings\n"
            f"`{p}settings commands <command> <on|off>` - Toggle command globally\n"
            f"`{p}settings features <feature> <mode|add|remove|clear [#ch]>` - Feature gates\n"
            f"`{p}settings channels <name> [#channel|off]` - Configure channel IDs\n"
            f"`{p}settings silas <id|banter|react> [value]` - Silas bot config"
        ), inline=False)
        embed.add_field(name="Admin Utils", value=(
            f"`{p}say <text>` - Make Gary post as bot (deletes your command)\n"
            f"`{p}give @user <amount>` - Add/remove coins\n"
            f"`{p}clear <n>` - Delete last n messages from Gary\n"
            f"`{p}restart` - Restart process"
        ), inline=False)
        await ctx.send(embed=embed)

async def setup(bot):
    bot.remove_command("help")
    await bot.add_cog(MiscCog(bot))
