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
from shared import *

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

def format_quote(content, name, date, prefix=""):
    year = date[2:4] if date else "??"
    short = content[:80] + "..." if len(content) > 80 else content
    return f'{prefix}> "{short}"\n\\- {name} 2k{year}'



class MiscCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener("on_ready")
    async def _start_misc_tasks(self):
        if not self.restore_nicknames.is_running():
            self.restore_nicknames.start()
        if not self.weather_alert_check.is_running():
            self.weather_alert_check.start()
    
    # ---------------------------------------------------------------------------
    # WEATHER
    # ---------------------------------------------------------------------------

    async def _fetch_weather_embed(self, city: str, title_prefix: str = "Weather in"):
        """Fetch weather and return an embed, or None on failure."""
        cleaned = city.strip()
        if cleaned.lower() in LOCAL_CITIES:
            cleaned = cleaned + ",IL,US"
        elif re.match(r'^.+,\s*[A-Za-z]{2}$', cleaned):
            cleaned = cleaned + ",US"

        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {"q": cleaned, "appid": OPENWEATHER_API_KEY, "units": "imperial"}
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
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
        embed.add_field(name="Temp", value=f"{temp:.0f}F", inline=True)
        embed.add_field(name="Feels Like", value=f"{feels:.0f}F", inline=True)
        embed.add_field(name="Humidity", value=f"{humidity}%", inline=True)
        embed.add_field(name="Wind", value=f"{wind:.0f} mph", inline=True)
        return embed

    @commands.command()
    async def weather(self, ctx, *, city: str = "Champaign"):
        """Get the weather for a city."""
        embed = await self._fetch_weather_embed(city)
        if embed is None:
            return await ctx.send(f"Couldn't find weather for **{city}** (or service unavailable).")
        await ctx.send(embed=embed)

    @tasks.loop(minutes=5)
    async def weather_alert_check(self):
        """Send daily 8 AM Central weather alert to the configured channel."""
        channel_id = runtime_settings.get("weather_alert_channel_id")
        if not channel_id:
            return
        now_central = datetime.now(CENTRAL_TZ)
        if now_central.hour != 8:
            return
        today_key = now_central.strftime("%Y-%m-%d")
        if runtime_settings.get("weather_alert_last_date") == today_key:
            return
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            return
        city = runtime_settings.get("weather_alert_city", "Champaign")
        embed = await self._fetch_weather_embed(city, title_prefix="☀️ Good Morning —")
        if embed is None:
            return
        await channel.send(embed=embed)
        runtime_settings["weather_alert_last_date"] = today_key
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
    # ON THIS DAY
    # ---------------------------------------------------------------------------

    @commands.command()
    async def onthisday(self, ctx):
        """Get a random historical event that happened on this day."""
        today = datetime.now(CENTRAL_TZ)
        url = f"https://byabbe.se/on-this-day/{today.month}/{today.day}/events.json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return await ctx.send("Couldn't fetch historical events right now.")
                data = await resp.json()
    
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
        existing = db.execute(
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
        db.execute(
            "DELETE FROM nick_changes WHERE guild_id = ? AND target_id = ?",
            (ctx.guild.id, member.id),
        )
        db.execute(
            "INSERT INTO nick_changes (guild_id, target_id, original_nick, expires_at) VALUES (?, ?, ?, ?)",
            (ctx.guild.id, member.id, original_nick, expires.isoformat())
        )
        db.commit()
    
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
        rows = db.execute("SELECT id, guild_id, target_id, original_nick, expires_at FROM nick_changes").fetchall()
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
                db.execute("DELETE FROM nick_changes WHERE id = ?", (row_id,))
        db.commit()
    
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
        db.execute(
            "INSERT INTO quotes (guild_id, quoted_user_id, quoted_user_name, content, saved_by, saved_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ctx.guild.id, msg.author.id, msg.author.display_name, msg.content, ctx.author.id, now)
        )
        db.commit()
        await ctx.send(format_quote(msg.content, msg.author.mention, now))
    

    @commands.command()
    @commands.guild_only()
    async def quotes(self, ctx, flag: str = ""):
        """Show recent quotes. Admin can use .quotes ids to show IDs."""
        show_ids = flag.lower() == "ids" and ctx.author.id == ADMIN_ID
        rows = db.execute(
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
        row = db.execute("SELECT id FROM quotes WHERE id = ? AND guild_id = ?", (quote_id, ctx.guild.id)).fetchone()
        if not row:
            return await ctx.send(f"Quote #{quote_id} not found.")
        db.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
        db.commit()
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
    async def kidsmode(self, ctx, action: str = "status"):
        """Admin only: enable, disable, or show kids mode for this server."""
        await self._set_kids_mode(ctx, action)

    @commands.command()
    async def setcommand(self, ctx, command_name: str, state: str):
        """Admin only: enable/disable a command globally."""
        if ctx.author.id != ADMIN_ID:
            return
        command_input = command_name.strip().lower()
        target = self.bot.get_command(command_input)
        if target is None:
            return await ctx.send(f"Unknown command: `{command_input}`")
        canonical_name = target.name.lower()
        if canonical_name in PROTECTED_ADMIN_COMMANDS:
            return await ctx.send("That admin control command cannot be disabled.")
        value = state.strip().lower()
        if value not in {"on", "off"}:
            return await ctx.send(f"Usage: `{PREFIX}setcommand <command> <on|off>`")
        toggles = runtime_settings.get("command_toggles", {})
        toggles[canonical_name] = (value == "on")
        runtime_settings["command_toggles"] = toggles
        shared._save_json_setting("command_toggles", toggles)
        await ctx.send(f"`{PREFIX}{canonical_name}` is now **{value.upper()}**.")
    
    

    @commands.command()
    async def setdeadchat(self, ctx, state: str):
        """Admin only: enable/disable dead chat callouts."""
        if ctx.author.id != ADMIN_ID:
            return
        value = state.strip().lower()
        if value not in {"on", "off"}:
            return await ctx.send(f"Usage: `{PREFIX}setdeadchat <on|off>`")
        enabled = value == "on"
        runtime_settings["dead_chat_enabled"] = enabled
        shared._save_json_setting("dead_chat_enabled", enabled)
        # Clear tracking so toggling on doesn't instantly fire stale escalation.
        last_message_time.clear()
        dead_chat_stage.clear()
        await ctx.send(f"Dead chat is now **{value.upper()}**.")
    
    

    @commands.command()
    async def setfeaturemode(self, ctx, feature: str, mode: str):
        """Admin only: configure channel policy mode for a feature."""
        if ctx.author.id != ADMIN_ID:
            return
        normalized_feature = normalize_feature_name(feature)
        normalized_mode = mode.strip().lower()
        if normalized_mode not in {"all", "off", "whitelist", "blacklist"}:
            return await ctx.send(
                f"Usage: `{PREFIX}setfeaturemode <feature> <all|off|whitelist|blacklist>`"
            )
        rules = runtime_settings.get("feature_channel_rules", {})
        existing = rules.get(normalized_feature, {"channels": []})
        existing["mode"] = normalized_mode
        existing["channels"] = [int(c) for c in existing.get("channels", [])]
        rules[normalized_feature] = existing
        runtime_settings["feature_channel_rules"] = rules
        shared._save_json_setting("feature_channel_rules", rules)
        await ctx.send(f"Feature `{normalized_feature}` mode is now **{normalized_mode}**.")
    
    

    @commands.command()
    async def setfeaturechannels(self, ctx, feature: str, action: str):
        """Admin only: add/remove/clear channel list for a feature rule."""
        if ctx.author.id != ADMIN_ID:
            return
        normalized_feature = normalize_feature_name(feature)
        normalized_action = action.strip().lower()
        if normalized_action not in {"add", "remove", "clear"}:
            return await ctx.send(
                f"Usage: `{PREFIX}setfeaturechannels <feature> <add|remove|clear> [#channel ...]`"
            )
        rules = runtime_settings.get("feature_channel_rules", {})
        rule = rules.get(normalized_feature, {"mode": "all", "channels": []})
        channels = {int(c) for c in rule.get("channels", [])}
    
        if normalized_action == "clear":
            channels.clear()
        else:
            mentioned_channels = ctx.message.channel_mentions
            if not mentioned_channels:
                return await ctx.send(
                    f"Mention one or more channels. Example: `{PREFIX}setfeaturechannels {normalized_feature} {normalized_action} #general`"
                )
            ids = {c.id for c in mentioned_channels}
            if normalized_action == "add":
                channels |= ids
            else:
                channels -= ids
    
        rule["channels"] = sorted(channels)
        rules[normalized_feature] = rule
        runtime_settings["feature_channel_rules"] = rules
        shared._save_json_setting("feature_channel_rules", rules)
        pretty_channels = ", ".join(f"<#{cid}>" for cid in rule["channels"]) or "(none)"
        await ctx.send(f"Feature `{normalized_feature}` channels: {pretty_channels}")
    
    

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

            if sec in {"dailyreminder", "daily"}:
                if not args:
                    enabled = runtime_settings.get("daily_reminder_enabled", True)
                    state_text = "ON" if enabled else "OFF"
                    return await ctx.send(f"Daily reminders: **{state_text}**")
                action = args[0].strip().lower()
                if action == "on":
                    runtime_settings["daily_reminder_enabled"] = True
                    shared._save_json_setting("daily_reminder_enabled", True)
                    return await ctx.send("Daily reminders are now **ON**.")
                if action == "off":
                    runtime_settings["daily_reminder_enabled"] = False
                    shared._save_json_setting("daily_reminder_enabled", False)
                    return await ctx.send("Daily reminders are now **OFF**.")
                if action == "status":
                    enabled = runtime_settings.get("daily_reminder_enabled", True)
                    state_text = "ON" if enabled else "OFF"
                    return await ctx.send(f"Daily reminders: **{state_text}**")
                return await ctx.send(
                    f"Usage: `{PREFIX}settings dailyreminder <on|off|status>`"
                )

            if sec == "passive":
                keys = {
                    "unsolicited": "unsolicited_chance_pct",
                    "silasbanter": "silas_banter_chance_pct",
                    "silasreact": "silas_react_chance_pct",
                }
                if not args:
                    lines = [
                        f"Unsolicited AI: **{runtime_settings.get('unsolicited_chance_pct', 0)}%**",
                        f"Silas banter: **{runtime_settings.get('silas_banter_chance_pct', 0)}%**",
                        f"Silas react: **{runtime_settings.get('silas_react_chance_pct', 0)}%**",
                    ]
                    return await ctx.send("\n".join(lines))
                target = args[0].strip().lower()
                if target not in keys:
                    return await ctx.send(
                        f"Usage: `{PREFIX}settings passive <unsolicited|silasbanter|silasreact> <0-100>`"
                    )
                if len(args) < 2:
                    current = runtime_settings.get(keys[target], 0)
                    return await ctx.send(f"`{target}` is **{current}%**")
                try:
                    value = int(args[1])
                except ValueError:
                    return await ctx.send("Value must be an integer percent (0-100).")
                value = max(0, min(100, value))
                runtime_settings[keys[target]] = value
                shared._save_json_setting(keys[target], value)
                return await ctx.send(f"`{target}` is now **{value}%**.")

            if sec == "gamble":
                if not args:
                    enabled = runtime_settings.get("gary_gamble_enabled", False)
                    channel_id = runtime_settings.get("gary_gamble_channel_id")
                    report_id = runtime_settings.get("gary_gamble_report_channel_id")
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
                    runtime_settings["gary_gamble_enabled"] = True
                    runtime_settings["gary_gamble_channel_id"] = ctx.channel.id
                    shared._save_json_setting("gary_gamble_enabled", True)
                    shared._save_json_setting("gary_gamble_channel_id", ctx.channel.id)
                    return await ctx.send(
                        f"Gary autonomous gambling is now **ON** in {ctx.channel.mention}."
                    )
                if action == "off":
                    runtime_settings["gary_gamble_enabled"] = False
                    shared._save_json_setting("gary_gamble_enabled", False)
                    return await ctx.send("Gary autonomous gambling is now **OFF**.")
                if action == "status":
                    enabled = runtime_settings.get("gary_gamble_enabled", False)
                    channel_id = runtime_settings.get("gary_gamble_channel_id")
                    report_id = runtime_settings.get("gary_gamble_report_channel_id")
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
                    runtime_settings["gary_gamble_channel_id"] = target.id
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
                    runtime_settings["gary_gamble_report_channel_id"] = target.id
                    shared._save_json_setting("gary_gamble_report_channel_id", target.id)
                    return await ctx.send(f"Gamble report channel set to {target.mention}.")
                return await ctx.send(
                    f"Usage: `{PREFIX}settings gamble <on|off|status|now|channel|report [#channel]>`"
                )

            if sec == "weather":
                channel_id = runtime_settings.get("weather_alert_channel_id")
                city = runtime_settings.get("weather_alert_city", "Champaign")
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
                    runtime_settings["weather_alert_channel_id"] = target.id
                    shared._save_json_setting("weather_alert_channel_id", target.id)
                    return await ctx.send(
                        f"Daily weather alert is now **ON** in {target.mention} for **{city}** at 8 AM Central."
                    )
                if action == "off":
                    runtime_settings["weather_alert_channel_id"] = None
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
                    runtime_settings["weather_alert_city"] = new_city
                    shared._save_json_setting("weather_alert_city", new_city)
                    return await ctx.send(f"Weather alert city set to **{new_city}**.")
                return await ctx.send(
                    f"Usage: `{PREFIX}settings weather <on [#channel]|off|status|city <name>>`"
                )

            return await ctx.send(f"Unknown settings section: `{sec}`")

        dead_chat_state = "ON" if runtime_settings.get("dead_chat_enabled", True) else "OFF"
        kids_mode_state = "ON" if ctx.guild and is_kids_mode_guild(ctx.guild.id) else "OFF"
        daily_reminder_state = "ON" if runtime_settings.get("daily_reminder_enabled", True) else "OFF"
        gary_gamble_state = "ON" if runtime_settings.get("gary_gamble_enabled", False) else "OFF"
        bj_ruleset = str(runtime_settings.get("bj_ruleset", "realistic")).upper()
        bj_hint_state = "ON" if runtime_settings.get("bj_basic_hint_enabled", True) else "OFF"
        gary_gamble_channel = runtime_settings.get("gary_gamble_channel_id")
        gary_gamble_channel_text = f"<#{int(gary_gamble_channel)}>" if gary_gamble_channel else "(not set)"
        disabled = sorted(
            name for name, enabled in runtime_settings.get("command_toggles", {}).items() if not enabled
        )
        disabled_str = ", ".join(f"`{PREFIX}{c}`" for c in disabled) if disabled else "None"
    
        rule_lines = []
        for feature, rule in sorted(runtime_settings.get("feature_channel_rules", {}).items()):
            mode = rule.get("mode", "all")
            channels = rule.get("channels", [])
            if channels:
                channel_str = ", ".join(f"<#{int(cid)}>" for cid in channels)
            else:
                channel_str = "(none)"
            rule_lines.append(f"`{feature}`: **{mode}** {channel_str}")
        rules_str = "\n".join(rule_lines) if rule_lines else "No feature channel rules set."
    
        unsolicited_pct = runtime_settings.get("unsolicited_chance_pct", 0)
        silas_banter_pct = runtime_settings.get("silas_banter_chance_pct", 0)
        silas_react_pct = runtime_settings.get("silas_react_chance_pct", 0)
        passive_value = (
            f"Unsolicited: **{unsolicited_pct}%**\n"
            f"Silas banter: **{silas_banter_pct}%**\n"
            f"Silas react: **{silas_react_pct}%**"
        )

        embed = discord.Embed(title="Runtime Settings", color=COLOR_DEFAULT)
        embed.add_field(name="Kids Mode", value=kids_mode_state, inline=True)
        embed.add_field(name="Dead Chat", value=dead_chat_state, inline=True)
        embed.add_field(name="Daily Reminder", value=daily_reminder_state, inline=True)
        embed.add_field(name="Gary Gamble", value=f"{gary_gamble_state}\n{gary_gamble_channel_text}", inline=True)
        weather_channel_id = runtime_settings.get("weather_alert_channel_id")
        weather_state = "ON" if weather_channel_id else "OFF"
        weather_text = f"<#{int(weather_channel_id)}>" if weather_channel_id else "(not set)"
        weather_city = runtime_settings.get("weather_alert_city", "Champaign")
        embed.add_field(name="Weather Alert", value=f"{weather_state}\n{weather_text}\n{weather_city}", inline=True)
        embed.add_field(name="BJ Ruleset", value=bj_ruleset, inline=True)
        embed.add_field(name="BJ Hint", value=bj_hint_state, inline=True)
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
        db.commit()
        python = sys.executable
        os.execv(python, [python] + sys.argv)
    
    

    @commands.command()
    async def stats(self, ctx):
        """Show runtime bot stats in a compact operational view."""
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

        total_cmds = sum(command_usage.values())
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

        unsolicited_pct = runtime_settings.get("unsolicited_chance_pct", 0)
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
    # LEADERBOARD
    # ---------------------------------------------------------------------------

    @commands.command(aliases=["lb", "top"])
    @commands.guild_only()
    async def leaderboard(self, ctx):
        """Show the richest users in the server."""
        rows = db.execute("SELECT user_id, balance FROM users ORDER BY balance DESC").fetchall()
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

    @commands.command()
    async def help(self, ctx):
        """Show all commands."""
        embed = discord.Embed(title="Bot Commands", color=COLOR_DEFAULT)
        p = PREFIX
        kids_mode = ctx.guild is not None and is_kids_mode_guild(ctx.guild.id)
        if not kids_mode:
            embed.add_field(name="Economy", value=(
                f"`{p}daily` - Claim daily coins\n"
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
        embed.add_field(name="Weather", value=f"`{p}weather [city]` - Current weather (defaults to Champaign)", inline=False)
        if not kids_mode:
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
                f"`{p}invite` / `{p}invite kids` - Get invite link"
            ), inline=False)
        if not kids_mode:
            embed.add_field(name="Quotes", value=(
                f"`{p}quote` - Reply to a message to save it\n"
                f"`{p}quotes` - Show recent quotes"
            ), inline=False)
        await ctx.send(embed=embed)

    @commands.command()
    async def adminhelp(self, ctx):
        """Show admin-only commands."""
        if ctx.author.id != ADMIN_ID:
            return
        p = PREFIX
        embed = discord.Embed(title="Admin Commands", color=COLOR_DEFAULT)
        embed.add_field(name="Runtime", value=(
            f"`{p}settings` - Show runtime settings\n"
            f"`{p}settings kids <on|off|status>` - Server kids mode\n"
            f"`{p}kidsmode <on|off|status>` - Shortcut for kids mode\n"
            f"`{p}settings dailyreminder <on|off|status>` - Daily reminder toggle\n"
            f"`{p}settings gamble <on|off|status|now|channel|report [#channel]>` - Gary autonomous gambling\n"
            f"`{p}settings weather <on [#channel]|off|status|city <name>>` - Daily 8 AM weather alert\n"
            f"`{p}settings passive <unsolicited|silasbanter|silasreact> <0-100>` - Passive AI chances\n"
            f"`{p}bjruleset <realistic|arcade|status>` - Blackjack table ruleset\n"
            f"`{p}bjhint <on|off|status>` - Basic strategy hint toggle"
        ), inline=False)
        embed.add_field(name="Feature Gates", value=(
            f"`{p}setcommand <command> <on|off>` - Toggle command\n"
            f"`{p}setdeadchat <on|off>` - Toggle dead chat\n"
            f"`{p}setfeaturemode <feature> <all|off|whitelist|blacklist>` - Feature policy\n"
            f"`{p}setfeaturechannels <feature> <add|remove|clear> #channel` - Feature channels"
        ), inline=False)
        embed.add_field(name="Admin Utils", value=(
            f"`{p}say <text>` - Make Gary post as bot (deletes your command)\n"
            f"`{p}give @user <amount>` - Add/remove coins\n"
            f"`{p}restart` - Restart process"
        ), inline=False)
        await ctx.send(embed=embed)

async def setup(bot):
    bot.remove_command("help")
    await bot.add_cog(MiscCog(bot))
