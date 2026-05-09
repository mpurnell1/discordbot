from datetime import datetime, timezone
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import discord
from discord.ext import commands

import shared
from shared import *
from modules import ai, economy, games, misc


def configure_logging() -> logging.Logger:
    logs_dir = Path(__file__).resolve().parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / "bot.log"

    logger = logging.getLogger("garybot")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    # Rotate at UTC midnight and keep 14 daily log files.
    rotating = TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
        utc=True,
    )
    rotating.setFormatter(formatter)

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    logger.addHandler(rotating)
    logger.addHandler(console)
    return logger


logger = configure_logging()


class GaryBot(commands.Bot):
    async def setup_hook(self):
        await games.setup(self)
        logger.info("Loaded GamesCog")
        await economy.setup(self)
        logger.info("Loaded EconomyCog")
        await ai.setup(self)
        logger.info("Loaded AICog")
        await misc.setup(self)
        logger.info("Loaded MiscCog")


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = GaryBot(command_prefix=PREFIX, intents=intents)


@bot.event
async def on_ready():
    shared.bot_start_time = datetime.now(timezone.utc)
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id)


async def notify_admin_guild_join(guild):
    permissions = guild.me.guild_permissions if guild.me else discord.Permissions.none()
    likely_kids_invite = not permissions.manage_messages and not permissions.manage_nicknames
    kids_mode_auto_enabled = False
    if likely_kids_invite:
        set_kids_mode_guild(guild.id, True)
        kids_mode_auto_enabled = True
    owner_text = f"{guild.owner} ({guild.owner_id})" if guild.owner_id else "unknown"
    unforce_sql = (
        "DELETE FROM guild_settings\n"
        f"WHERE guild_id = {guild.id} AND key = 'kids_mode';"
    )
    description = (
        f"Gary was added to **{guild.name}**.\n\n"
        f"Guild ID: `{guild.id}`\n"
        f"Owner: `{owner_text}`\n"
        f"Members: `{guild.member_count}`\n"
        f"Likely kids invite: **{'YES' if likely_kids_invite else 'NO'}**\n"
        f"Kids mode auto-enabled: **{'YES' if kids_mode_auto_enabled else 'NO'}**\n"
        f"Manage Messages: **{permissions.manage_messages}**\n"
        f"Manage Nicknames: **{permissions.manage_nicknames}**\n\n"
        "Un-force kids mode SQL if this server was auto-enabled by mistake:\n"
        f"```sql\n{unforce_sql}\n```"
    )
    try:
        report_channel = bot.get_channel(GUILD_JOIN_REPORT_CHANNEL_ID)
        if report_channel is None:
            report_channel = await bot.fetch_channel(GUILD_JOIN_REPORT_CHANNEL_ID)
        await report_channel.send(embed=make_embed("Gary Joined New Server", description, COLOR_WARNING))
    except discord.HTTPException:
        logger.warning("Could not post guild join report for guild %s", guild.id)
        try:
            admin = bot.get_user(ADMIN_ID) or await bot.fetch_user(ADMIN_ID)
            await admin.send(embed=make_embed("Gary Joined New Server", description, COLOR_WARNING))
        except discord.HTTPException:
            logger.warning("Could not DM admin about guild join for guild %s", guild.id)


@bot.event
async def on_guild_join(guild):
    logger.info("Joined guild %s (%s)", guild.name, guild.id)
    await notify_admin_guild_join(guild)
    target = guild.system_channel
    if target is not None:
        permissions = target.permissions_for(guild.me)
        if not permissions.send_messages:
            target = None
    if target is None:
        for channel in guild.text_channels:
            permissions = channel.permissions_for(guild.me)
            if permissions.send_messages:
                target = channel
                break
    if target is None:
        return
    try:
        await target.send(embed=make_embed(
            "Gary Added",
            f"Gary is ready. Use `{PREFIX}help` to see available commands.",
            COLOR_DEFAULT,
        ))
    except discord.HTTPException:
        logger.warning("Could not send guild join setup message in guild %s", guild.id)


@bot.event
async def on_command(ctx):
    command_usage[ctx.command.name] += 1
    logger.info(
        "Command invoked: %s | user=%s (%s) | guild=%s | channel=%s",
        ctx.command.name,
        ctx.author,
        ctx.author.id,
        getattr(ctx.guild, "id", "DM"),
        ctx.channel.id,
    )


@bot.before_invoke
async def auto_daily_award(ctx):
    user_id = ctx.author.id
    now = datetime.now(CENTRAL_TZ)
    available, _ = is_daily_available(user_id, now=now)
    if available:
        update_balance(user_id, DAILY_AMOUNT)
        db.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), user_id))
        db.commit()
        bal = get_balance(user_id)
        await ctx.send(embed=make_embed("💰 Daily Reward!", f"You got **{DAILY_AMOUNT}** coins!\nBalance: **{bal}**", COLOR_SUCCESS))


@bot.check
async def command_gatekeeper(ctx):
    command_name = ctx.command.name.lower()
    guild_id = ctx.guild.id if ctx.guild else None
    if is_kids_mode_guild(guild_id) and not is_kids_command_allowed(command_name):
        raise commands.CheckFailure("That command is disabled because this server is in kids mode.")
    if ctx.author.id == ADMIN_ID:
        return True
    if not is_command_enabled(command_name):
        raise commands.CheckFailure("That command is currently disabled by an admin.")
    if not is_feature_allowed(f"cmd:{command_name}", ctx.channel.id, guild_id):
        raise commands.CheckFailure("That command is not allowed in this channel.")
    return True


COMMAND_USAGE = {
    "guess": "<number 1-10>",
    "solve": "<answer>",
    "ttt": "[@opponent]",
    "c4": "[@opponent]",
    "m": "<1-9>",
    "drop": "<1-7>",
    "g": "<guess>",
    "rps": "<rock|paper|scissors>",
    "roll": "[sides]",
    "mathgame": "",
    "mathanswer": "<answer>",
    "memory": "[level 1-5]",
    "memoryanswer": "<sequence>",
    "trivia": "",
    "triviaanswer": "<A|B|C|D>",
    "scramble": "",
    "unscramble": "<word>",
    "timer": "<seconds>",
    "coinflip": "<amount>",
    "slots": "<amount>",
    "blackjack": "<amount>",
    "bjrules": "",
    "weather": "<city>",
    "changenick": "@user <nickname>",
    "ask": "<question>",
    "rp": "<character>",
    "unquote": "<id>",
    "give": "@user <amount>",
    "setcommand": "<command> <on|off>",
    "setdeadchat": "<on|off>",
    "setfeaturemode": "<feature> <all|off|whitelist|blacklist>",
    "setfeaturechannels": "<feature> <add|remove|clear> [#channel ...]",
    "bjruleset": "<realistic|arcade|status>",
    "bjhint": "<on|off|status>",
    "say": "<text>",
    "kidsmode": "<on|off|status>",
    "invite": "[kids]",
    "settings": "[kids <on|off|status> | gamble <on|off|status|now|channel|report [#channel]> | passive <unsolicited|silasbanter|silasreact> <0-100>]",
}


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        command = ctx.command.name
        args = COMMAND_USAGE.get(command)
        logger.warning(
            "Command argument error: %s | user=%s (%s) | error=%s",
            command,
            ctx.author,
            ctx.author.id,
            error,
        )
        await ctx.send(f"Usage: `{PREFIX}{command} {args}`" if args else f"Check `{PREFIX}help` for usage.")
    elif isinstance(error, commands.CheckFailure):
        logger.warning(
            "Command blocked by check: %s | user=%s (%s) | reason=%s",
            getattr(ctx.command, "name", "unknown"),
            ctx.author,
            ctx.author.id,
            error,
        )
        await ctx.send(str(error))
    elif isinstance(error, commands.CommandNotFound):
        logger.info("Unknown command from %s (%s)", ctx.author, ctx.author.id)
    else:
        logger.exception(
            "Unhandled command error: command=%s user=%s (%s)",
            getattr(ctx.command, "name", "unknown"),
            ctx.author,
            ctx.author.id,
        )
        await ctx.send("That command failed unexpectedly. Check logs and try again.")


bot.run(TOKEN)
