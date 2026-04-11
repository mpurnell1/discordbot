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


@bot.check
async def command_gatekeeper(ctx):
    if ctx.author.id == ADMIN_ID:
        return True
    command_name = ctx.command.name.lower()
    if not is_command_enabled(command_name):
        raise commands.CheckFailure("That command is currently disabled by an admin.")
    if not is_feature_allowed(f"cmd:{command_name}", ctx.channel.id):
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
    "coinflip": "<amount>",
    "slots": "<amount>",
    "blackjack": "<amount>",
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
    "settings": "[dailyreminder <on|off|status> | gamble <on|off|status|now|channel [#channel]>]",
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
        raise error


bot.run(TOKEN)
