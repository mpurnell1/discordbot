from datetime import datetime, timezone

import discord
from discord.ext import commands

from shared import *
from modules import ai, economy, games, misc


class GaryBot(commands.Bot):
    async def setup_hook(self):
        await games.setup(self)
        await economy.setup(self)
        await ai.setup(self)
        await misc.setup(self)


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = GaryBot(command_prefix=PREFIX, intents=intents)


@bot.event
async def on_ready():
    global bot_start_time
    bot_start_time = datetime.now(timezone.utc)


@bot.event
async def on_command(ctx):
    command_usage[ctx.command.name] += 1


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
    "settings": "",
}


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        command = ctx.command.name
        args = COMMAND_USAGE.get(command)
        await ctx.send(f"Usage: `{PREFIX}{command} {args}`" if args else f"Check `{PREFIX}help` for usage.")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send(str(error))
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        raise error


bot.run(TOKEN)
