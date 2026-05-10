from datetime import datetime, timezone
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import discord
from discord.ext import commands

import shared
from shared import (
    TOKEN,
    PREFIX,
    ADMIN_ID,
    DAILY_AMOUNT,
    CENTRAL_TZ,
    COLOR_DEFAULT,
    COLOR_SUCCESS,
    COLOR_WARNING,
    make_embed,
    get_balance,
    update_balance,
    is_daily_available,
    is_kids_mode_guild,
    is_kids_command_allowed,
    is_command_enabled,
    is_feature_allowed,
    set_kids_mode_guild,
    update_activity_streak,
)
from modules import ai, economy, games, misc, stocks


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
        await stocks.setup(self)
        logger.info("Loaded StocksCog")


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
        guild_join_channel_id = shared.runtime_settings.get("guild_join_report_channel_id")
        if not guild_join_channel_id:
            return
        report_channel = bot.get_channel(guild_join_channel_id)
        if report_channel is None:
            report_channel = await bot.fetch_channel(guild_join_channel_id)
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


async def post_kids_log(content: str):
    if not content:
        return
    try:
        kids_log_id = shared.runtime_settings.get("kids_interaction_log_channel_id")
        if not kids_log_id:
            return
        channel = bot.get_channel(kids_log_id)
        if channel is None:
            channel = await bot.fetch_channel(kids_log_id)
        await channel.send(content[:1900])
    except discord.HTTPException:
        logger.warning("Could not post to kids interaction log channel")


async def post_error_log(title: str, body: str):
    channel_id = shared.runtime_settings.get("bug_report_channel_id")
    if not channel_id:
        return
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        await channel.send(embed=make_embed(title, body[:4000], shared.COLOR_ERROR))
    except discord.HTTPException:
        logger.warning("Could not post to error log channel")


def summarize_bot_message(message: discord.Message) -> str:
    parts = []
    if message.content:
        parts.append(message.content)
    for embed in message.embeds:
        if embed.title:
            parts.append(f"**{embed.title}**")
        if embed.description:
            parts.append(embed.description)
        for field in embed.fields:
            parts.append(f"*{field.name}*: {field.value}")
    if message.attachments:
        parts.append(f"[{len(message.attachments)} attachment(s)]")
    return "\n".join(p for p in parts if p) or "(no content)"


@bot.event
async def on_command(ctx):
    shared.command_usage[ctx.command.name] += 1
    logger.info(
        "Command invoked: %s | user=%s (%s) | guild=%s | channel=%s",
        ctx.command.name,
        ctx.author,
        ctx.author.id,
        getattr(ctx.guild, "id", "DM"),
        ctx.channel.id,
    )


@bot.listen("on_message")
async def log_kids_interactions(message):
    if message.guild is None:
        return
    if message.channel.id == shared.runtime_settings.get("kids_interaction_log_channel_id"):
        return
    if not is_kids_mode_guild(message.guild.id):
        return
    if message.author.id == bot.user.id:
        await post_kids_log(
            f"📤 **{message.guild.name}** `#{message.channel}` — Gary\n"
            f"{summarize_bot_message(message)}"
        )
        return
    if message.author.bot:
        return
    if not message.content.startswith(PREFIX):
        return
    await post_kids_log(
        f"📥 **{message.guild.name}** `#{message.channel}` — {message.author} (`{message.author.id}`)\n"
        f"> {message.content}"
    )


@bot.before_invoke
async def log_command_use(ctx):
    guild_id = ctx.guild.id if ctx.guild else None
    shared.log_command(ctx.author.id, ctx.command.name, guild_id)
    shared.command_usage[ctx.command.name] += 1


@bot.before_invoke
async def auto_daily_award(ctx):
    guild_id = ctx.guild.id if ctx.guild else None
    if is_kids_mode_guild(guild_id):
        return
    user_id = ctx.author.id
    now = datetime.now(CENTRAL_TZ)
    available, _ = is_daily_available(user_id, now=now)
    if available:
        prev_row = shared.db.execute("SELECT last_daily FROM users WHERE user_id = ?", (user_id,)).fetchone()
        prev_daily_iso = prev_row[0] if prev_row else None
        update_balance(user_id, DAILY_AMOUNT)
        shared.db.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), user_id))
        shared.db.commit()
        streak, is_milestone, bonus = update_activity_streak(user_id, prev_daily_iso)
        if bonus:
            update_balance(user_id, bonus)
        bal = get_balance(user_id)
        if is_milestone:
            desc = (
                f"You got **{DAILY_AMOUNT}** coins + **{bonus:,}** streak bonus!\n"
                f"🔥 **{streak}-day streak!** Balance: **{bal:,}**"
            )
        elif streak > 1:
            desc = f"You got **{DAILY_AMOUNT}** coins!\n🔥 {streak}-day streak | Balance: **{bal:,}**"
        else:
            desc = f"You got **{DAILY_AMOUNT}** coins!\nBalance: **{bal:,}**"
        await ctx.send(embed=make_embed("💰 Daily Reward!", desc, COLOR_SUCCESS))


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
    "bugreport": "<description>",
    "featurerequest": "<description>",
    "changenick": "@user <nickname>",
    "ask": "<question>",
    "rp": "<character>",
    "unquote": "<id>",
    "give": "@user <amount>",
    "say": "<text>",
    "invite": "[kids]",
    "alias": "",
    "buy": "<TICKER> <qty|all|$coins>",
    "sell": "<TICKER> <qty|all|$coins>",
    "portfolio": "[@user]",
    "settings": (
        "[kids <on|off|status> | gamble <on|off|status|now|channel|report [#channel]>"
        " | passive <unsolicited|silasbanter|silasreact> <0-100>"
        " | ticker <on [#channel]|off|status>]"
    ),
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
        import traceback as _tb
        tb_text = _tb.format_exc()
        logger.exception(
            "Unhandled command error: command=%s user=%s (%s)",
            getattr(ctx.command, "name", "unknown"),
            ctx.author,
            ctx.author.id,
        )
        await ctx.send("That command failed unexpectedly. Check logs and try again.")
        await post_error_log(
            "⚠️ Unhandled Command Error",
            f"**Command:** `{PREFIX}{getattr(ctx.command, 'name', 'unknown')}`\n"
            f"**User:** {ctx.author} (`{ctx.author.id}`)\n"
            f"**Guild:** {getattr(ctx.guild, 'name', 'DM')} (`{getattr(ctx.guild, 'id', 'N/A')}`)\n"
            f"**Error:** `{type(error).__name__}: {error}`\n"
            f"```\n{tb_text[-1500:]}\n```"
        )


@bot.event
async def on_error(event, *args, **kwargs):
    import traceback as _tb
    tb_text = _tb.format_exc()
    logger.exception("Unhandled error in event listener: %s", event)
    await post_error_log(
        "⚠️ Event Listener Error",
        f"**Event:** `{event}`\n```\n{tb_text[-1800:]}\n```"
    )


@bot.event
async def on_disconnect():
    logger.warning("Disconnected from Discord gateway")


@bot.event
async def on_resumed():
    logger.info("Resumed Discord gateway session")


if __name__ == "__main__":
    if not ADMIN_ID:
        raise SystemExit("ADMIN_ID is not set in .env — cannot start bot.")
    if not shared.OLLAMA_URL:
        logger.warning("OLLAMA_URL is not set — AI features will not work.")
    bot.run(TOKEN)
