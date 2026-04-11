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
    
    # ---------------------------------------------------------------------------
    # WEATHER
    # ---------------------------------------------------------------------------

    @commands.command()
    async def weather(self, ctx, *, city: str = "Champaign"):
        """Get the weather for a city."""
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
                        return await ctx.send(f"Couldn't find weather for **{city}**.")
                    data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return await ctx.send("Weather service is unavailable right now. Try again in a bit.")
    
        desc = data["weather"][0]["description"].title()
        temp = data["main"]["temp"]
        feels = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        wind = data["wind"]["speed"]
        icon = data["weather"][0]["icon"]
        name = data["name"]
    
        embed = discord.Embed(title=f"Weather in {name}", color=COLOR_DEFAULT)
        embed.set_thumbnail(url=f"https://openweathermap.org/img/wn/{icon}@2x.png")
        embed.add_field(name="Condition", value=desc, inline=True)
        embed.add_field(name="Temp", value=f"{temp:.0f}F", inline=True)
        embed.add_field(name="Feels Like", value=f"{feels:.0f}F", inline=True)
        embed.add_field(name="Humidity", value=f"{humidity}%", inline=True)
        embed.add_field(name="Wind", value=f"{wind:.0f} mph", inline=True)
        await ctx.send(embed=embed)
    
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
        a, b = random.choice(WYR_QUESTIONS)
        embed = make_embed("🤔 Would You Rather...",
            f"🅰️ {a}\n\n**OR**\n\n🅱️ {b}", COLOR_PINK)
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("🅰️")
        await msg.add_reaction("🅱️")
    
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
    
        try:
            original_nick = member.display_name
            await member.edit(nick=new_nick)
        except discord.Forbidden:
            return await ctx.send("I don't have permission to change that user's nickname.")
    
        update_balance(ctx.author.id, -NICKNAME_COST)
        expires = datetime.now(timezone.utc) + timedelta(hours=NICKNAME_DURATION_HOURS)
    
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

            if sec == "gamble":
                if not args:
                    enabled = runtime_settings.get("gary_gamble_enabled", False)
                    channel_id = runtime_settings.get("gary_gamble_channel_id")
                    channel_text = f"<#{int(channel_id)}>" if channel_id else "(not set)"
                    state_text = "ON" if enabled else "OFF"
                    return await ctx.send(f"Gary gambling: **{state_text}** | Channel: {channel_text}")

                action = args[0].strip().lower()
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
                    channel_text = f"<#{int(channel_id)}>" if channel_id else "(not set)"
                    state_text = "ON" if enabled else "OFF"
                    return await ctx.send(f"Gary gambling: **{state_text}** | Channel: {channel_text}")
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
                return await ctx.send(
                    f"Usage: `{PREFIX}settings gamble <on|off|status|now|channel [#channel]>`"
                )

            return await ctx.send(f"Unknown settings section: `{sec}`")

        dead_chat_state = "ON" if runtime_settings.get("dead_chat_enabled", True) else "OFF"
        daily_reminder_state = "ON" if runtime_settings.get("daily_reminder_enabled", True) else "OFF"
        gary_gamble_state = "ON" if runtime_settings.get("gary_gamble_enabled", False) else "OFF"
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
    
        embed = discord.Embed(title="Runtime Settings", color=COLOR_DEFAULT)
        embed.add_field(name="Dead Chat", value=dead_chat_state, inline=True)
        embed.add_field(name="Daily Reminder", value=daily_reminder_state, inline=True)
        embed.add_field(name="Gary Gamble", value=f"{gary_gamble_state}\n{gary_gamble_channel_text}", inline=True)
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
        """Show bot stats: uptime, latency, versions, economy, and command usage."""
        # Uptime
        if shared.bot_start_time:
            delta = datetime.now(timezone.utc) - shared.bot_start_time
            days, rem = divmod(int(delta.total_seconds()), 86400)
            hours, rem = divmod(rem, 3600)
            minutes, _ = divmod(rem, 60)
            parts = []
            if days:
                parts.append(f"{days}d")
            if hours:
                parts.append(f"{hours}h")
            parts.append(f"{minutes}m")
            uptime = " ".join(parts)
        else:
            uptime = "Unknown"
    
        # Economy
        econ = db.execute("SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM users").fetchone()
        total_users, total_coins = econ
        richest = db.execute(
            "SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 1"
        ).fetchone()
        if richest:
            richest_member = ctx.guild.get_member(richest[0])
            richest_str = f"{richest_member.display_name} ({richest[1]})" if richest_member else f"Unknown ({richest[1]})"
        else:
            richest_str = "Nobody"
    
        # Command usage (top 5 this session)
        top_cmds = command_usage.most_common(5)
        if top_cmds:
            usage_str = "\n".join(f"`{PREFIX}{name}` — {count}" for name, count in top_cmds)
        else:
            usage_str = "No commands used yet"
        total_cmds = sum(command_usage.values())
    
        embed = discord.Embed(title="📊 Bot Stats", color=COLOR_DEFAULT)
        embed.add_field(name="Uptime", value=uptime, inline=True)
        embed.add_field(name="Latency", value=f"{self.bot.latency * 1000:.0f}ms", inline=True)
        embed.add_field(name="Versions", value=(
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\n"
            f"discord.py {discord.__version__}"
        ), inline=True)
        embed.add_field(name="Economy", value=(
            f"Users: **{total_users}**\n"
            f"Coins in circulation: **{total_coins}**\n"
            f"Richest: **{richest_str}**"
        ), inline=False)
        embed.add_field(name=f"Top Commands (session: {total_cmds} total)", value=usage_str, inline=False)
        await ctx.send(embed=embed)
    

    @commands.command()
    async def invite(self, ctx):
        """Generate an invite link with the permissions the bot needs."""
        perms = discord.Permissions(
            send_messages=True,
            embed_links=True,
            add_reactions=True,
            manage_nicknames=True,
            read_message_history=True,
            read_messages=True,
        )
        link = discord.utils.oauth_url(self.bot.user.id, permissions=perms)
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
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
        await ctx.send(text)
    
    # ---------------------------------------------------------------------------
    # LEADERBOARD
    # ---------------------------------------------------------------------------

    @commands.command(aliases=["lb", "top"])
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
        embed.add_field(name="Economy", value=(
            f"`{p}daily` - Claim daily coins\n"
            f"`{p}guess <1-10>` - Guess for a free coin (3x/day)\n"
            f"`{p}puzzle` - Daily puzzle for {PUZZLE_REWARD} coins\n"
            f"`{p}balance` - Check balance\n"
            f"`{p}leaderboard` - Top 10 richest"
        ), inline=False)
        embed.add_field(name="Gambling", value=(
            f"`{p}coinflip <amt>` - Double or nothing\n"
            f"`{p}slots <amt>` - Slot machine\n"
            f"`{p}blackjack <amt>` - Play 21"
        ), inline=False)
        embed.add_field(name="Games", value=(
            f"`{p}ttt @user` - Tic-tac-toe\n"
            f"`{p}c4 @user` - Connect 4\n"
            f"`{p}hangman` - Hangman (plain single letters or `{p}g <guess>`)\n"
            f"`{p}forfeit` - Quit current game"
        ), inline=False)
        embed.add_field(name="Weather", value=f"`{p}weather [city]` - Current weather (defaults to Champaign)", inline=False)
        embed.add_field(name="Animals", value=f"`{p}cat` / `{p}dog` - Random pics", inline=False)
        embed.add_field(name="Fun", value=(
            f"`{p}wyr` - Would You Rather\n"
            f"`{p}onthisday` - Historical event today\n"
            f"`{p}changenick @user name` - Change nickname ({NICKNAME_COST} coins)"
        ), inline=False)
        embed.add_field(name="AI", value=(
            f"`{p}ask <question>` - Ask the AI (needs desktop on)\n"
            f"`{p}rp <character>` - Roleplay with Silas\n"
            f"`{p}stoprp` - End roleplay"
        ), inline=False)
        embed.add_field(name="Info", value=(
            f"`{p}stats` - Bot stats and usage\n"
            f"`{p}invite` - Get invite link\n"
            f"`{p}adminhelp` - Admin command list (admin only)"
        ), inline=False)
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
            f"`{p}settings dailyreminder <on|off|status>` - Daily reminder toggle\n"
            f"`{p}settings gamble <on|off|status|now|channel [#channel]>` - Gary autonomous gambling"
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
