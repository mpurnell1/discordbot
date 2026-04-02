import discord
from discord.ext import commands, tasks
import aiohttp
import sqlite3
import random
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_TOKEN_HERE")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "YOUR_API_KEY_HERE")
PREFIX = "!"
DAILY_AMOUNT = 200
NICKNAME_COST = 2000
NICKNAME_DURATION_HOURS = 24
STARTING_BALANCE = 100

# ---------------------------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------------------------
def init_db():
    db = sqlite3.connect("bot.db")
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            last_daily TEXT DEFAULT ''
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS nick_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            target_id INTEGER,
            original_nick TEXT,
            expires_at TEXT
        )
    """)
    db.commit()
    return db

db = init_db()

# ---------------------------------------------------------------------------
# BOT SETUP
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def get_balance(user_id: int) -> int:
    row = db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        db.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, STARTING_BALANCE))
        db.commit()
        return STARTING_BALANCE
    return row[0]

def update_balance(user_id: int, amount: int):
    get_balance(user_id)  # ensure row exists
    db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    db.commit()

def set_balance(user_id: int, amount: int):
    get_balance(user_id)
    db.execute("UPDATE users SET balance = ? WHERE user_id = ?", (amount, user_id))
    db.commit()

def make_embed(title, description, color=0x5865F2):
    return discord.Embed(title=title, description=description, color=color)

# ---------------------------------------------------------------------------
# EVENTS
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    restore_nicknames.start()

# ---------------------------------------------------------------------------
# ECONOMY: DAILY
# ---------------------------------------------------------------------------
@bot.command()
async def daily(ctx):
    """Claim your daily coins."""
    user_id = ctx.author.id
    get_balance(user_id)  # ensure row
    row = db.execute("SELECT last_daily FROM users WHERE user_id = ?", (user_id,)).fetchone()
    now = datetime.now(timezone.utc)
    if row and row[0]:
        last = datetime.fromisoformat(row[0])
        if now - last < timedelta(hours=24):
            remaining = timedelta(hours=24) - (now - last)
            h, m = divmod(int(remaining.total_seconds()) // 60, 60)
            await ctx.send(embed=make_embed("⏰ Already Claimed", f"Come back in **{h}h {m}m**.", 0xED4245))
            return
    update_balance(user_id, DAILY_AMOUNT)
    db.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), user_id))
    db.commit()
    bal = get_balance(user_id)
    await ctx.send(embed=make_embed("💰 Daily Claimed!", f"You got **{DAILY_AMOUNT}** coins!\nBalance: **{bal}**", 0x57F287))

# ---------------------------------------------------------------------------
# ECONOMY: BALANCE
# ---------------------------------------------------------------------------
@bot.command(aliases=["bal"])
async def balance(ctx, member: discord.Member = None):
    """Check your coin balance (or someone else's)."""
    target = member or ctx.author
    bal = get_balance(target.id)
    await ctx.send(embed=make_embed(f"💵 {target.display_name}'s Balance", f"**{bal}** coins"))

# ---------------------------------------------------------------------------
# GAMBLING: COINFLIP
# ---------------------------------------------------------------------------
@bot.command(aliases=["cf"])
async def coinflip(ctx, amount: int):
    """Flip a coin — double or nothing."""
    if amount <= 0:
        return await ctx.send("Bet must be positive!")
    bal = get_balance(ctx.author.id)
    if amount > bal:
        return await ctx.send(embed=make_embed("❌ Broke", f"You only have **{bal}** coins.", 0xED4245))

    result = random.choice(["heads", "tails"])
    call = random.choice(["heads", "tails"])

    if result == call:
        update_balance(ctx.author.id, amount)
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed(
            f"🪙 {result.title()}! You win!",
            f"You won **{amount}** coins!\nBalance: **{new_bal}**", 0x57F287))
    else:
        update_balance(ctx.author.id, -amount)
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed(
            f"🪙 {result.title()}! You lose!",
            f"You lost **{amount}** coins.\nBalance: **{new_bal}**", 0xED4245))

# ---------------------------------------------------------------------------
# GAMBLING: SLOTS
# ---------------------------------------------------------------------------
SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]

@bot.command()
async def slots(ctx, amount: int):
    """Pull the slot machine lever."""
    if amount <= 0:
        return await ctx.send("Bet must be positive!")
    bal = get_balance(ctx.author.id)
    if amount > bal:
        return await ctx.send(embed=make_embed("❌ Broke", f"You only have **{bal}** coins.", 0xED4245))

    reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
    display = " | ".join(reels)

    if reels[0] == reels[1] == reels[2]:
        if reels[0] == "7️⃣":
            multiplier = 10
        elif reels[0] == "💎":
            multiplier = 5
        else:
            multiplier = 3
        winnings = amount * multiplier
        update_balance(ctx.author.id, winnings)
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed(
            f"🎰 {display}",
            f"**JACKPOT!** You won **{winnings}** coins! (x{multiplier})\nBalance: **{new_bal}**", 0x57F287))
    elif reels[0] == reels[1] or reels[1] == reels[2]:
        winnings = amount
        update_balance(ctx.author.id, winnings)
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed(
            f"🎰 {display}",
            f"Two in a row! You won **{winnings}** coins!\nBalance: **{new_bal}**", 0xFEE75C))
    else:
        update_balance(ctx.author.id, -amount)
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed(
            f"🎰 {display}",
            f"No match. You lost **{amount}** coins.\nBalance: **{new_bal}**", 0xED4245))

# ---------------------------------------------------------------------------
# GAMBLING: BLACKJACK
# ---------------------------------------------------------------------------
active_blackjack = {}

def make_deck():
    suits = ["♠", "♥", "♦", "♣"]
    ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
    deck = [(r, s) for s in suits for r in ranks]
    random.shuffle(deck)
    return deck

def hand_value(hand):
    value = 0
    aces = 0
    for rank, _ in hand:
        if rank in ("J", "Q", "K"):
            value += 10
        elif rank == "A":
            value += 11
            aces += 1
        else:
            value += int(rank)
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value

def display_hand(hand):
    return " ".join(f"`{r}{s}`" for r, s in hand)

@bot.command(aliases=["bj"])
async def blackjack(ctx, amount: int):
    """Play a hand of blackjack."""
    if amount <= 0:
        return await ctx.send("Bet must be positive!")
    if ctx.author.id in active_blackjack:
        return await ctx.send("You already have a hand going! Use `!hit` or `!stand`.")
    bal = get_balance(ctx.author.id)
    if amount > bal:
        return await ctx.send(embed=make_embed("❌ Broke", f"You only have **{bal}** coins.", 0xED4245))

    deck = make_deck()
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]

    pv = hand_value(player)

    if pv == 21:
        winnings = int(amount * 1.5)
        update_balance(ctx.author.id, winnings)
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed("🃏 BLACKJACK!",
            f"Your hand: {display_hand(player)} → **21**\n"
            f"Dealer: {display_hand(dealer)} → **{hand_value(dealer)}**\n\n"
            f"You won **{winnings}** coins!\nBalance: **{new_bal}**", 0x57F287))
        return

    active_blackjack[ctx.author.id] = {
        "deck": deck, "player": player, "dealer": dealer, "bet": amount
    }

    await ctx.send(embed=make_embed("🃏 Blackjack",
        f"Your hand: {display_hand(player)} → **{pv}**\n"
        f"Dealer shows: {display_hand(dealer[:1])} `??`\n\n"
        f"Type `!hit` or `!stand`"))

@bot.command()
async def hit(ctx):
    """Draw another card in blackjack."""
    game = active_blackjack.get(ctx.author.id)
    if not game:
        return await ctx.send("You don't have an active blackjack hand. Start one with `!blackjack <amount>`.")

    game["player"].append(game["deck"].pop())
    pv = hand_value(game["player"])

    if pv > 21:
        update_balance(ctx.author.id, -game["bet"])
        new_bal = get_balance(ctx.author.id)
        del active_blackjack[ctx.author.id]
        await ctx.send(embed=make_embed("🃏 Bust!",
            f"Your hand: {display_hand(game['player'])} → **{pv}**\n"
            f"You lost **{game['bet']}** coins.\nBalance: **{new_bal}**", 0xED4245))
    elif pv == 21:
        await stand(ctx)  # auto-stand on 21
    else:
        await ctx.send(embed=make_embed("🃏 Blackjack",
            f"Your hand: {display_hand(game['player'])} → **{pv}**\n"
            f"Dealer shows: {display_hand(game['dealer'][:1])} `??`\n\n"
            f"Type `!hit` or `!stand`"))

@bot.command()
async def stand(ctx):
    """Stand with your current hand in blackjack."""
    game = active_blackjack.get(ctx.author.id)
    if not game:
        return await ctx.send("You don't have an active blackjack hand.")

    # Dealer draws to 17
    while hand_value(game["dealer"]) < 17:
        game["dealer"].append(game["deck"].pop())

    pv = hand_value(game["player"])
    dv = hand_value(game["dealer"])
    del active_blackjack[ctx.author.id]

    result_lines = (
        f"Your hand: {display_hand(game['player'])} → **{pv}**\n"
        f"Dealer: {display_hand(game['dealer'])} → **{dv}**\n\n"
    )

    if dv > 21 or pv > dv:
        update_balance(ctx.author.id, game["bet"])
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed("🃏 You Win!", result_lines +
            f"You won **{game['bet']}** coins!\nBalance: **{new_bal}**", 0x57F287))
    elif pv < dv:
        update_balance(ctx.author.id, -game["bet"])
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed("🃏 Dealer Wins", result_lines +
            f"You lost **{game['bet']}** coins.\nBalance: **{new_bal}**", 0xED4245))
    else:
        new_bal = get_balance(ctx.author.id)
        await ctx.send(embed=make_embed("🃏 Push!", result_lines +
            f"It's a tie. Your **{game['bet']}** coins are returned.\nBalance: **{new_bal}**", 0xFEE75C))

# ---------------------------------------------------------------------------
# WEATHER
# ---------------------------------------------------------------------------
@bot.command()
async def weather(ctx, *, city: str):
    """Get the weather for a city."""
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": city, "appid": OPENWEATHER_API_KEY, "units": "imperial"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return await ctx.send(f"Couldn't find weather for **{city}**.")
            data = await resp.json()

    desc = data["weather"][0]["description"].title()
    temp = data["main"]["temp"]
    feels = data["main"]["feels_like"]
    humidity = data["main"]["humidity"]
    wind = data["wind"]["speed"]
    icon = data["weather"][0]["icon"]
    name = data["name"]

    embed = discord.Embed(title=f"🌤️ Weather in {name}", color=0x5865F2)
    embed.set_thumbnail(url=f"https://openweathermap.org/img/wn/{icon}@2x.png")
    embed.add_field(name="Condition", value=desc, inline=True)
    embed.add_field(name="Temp", value=f"{temp:.0f}°F", inline=True)
    embed.add_field(name="Feels Like", value=f"{feels:.0f}°F", inline=True)
    embed.add_field(name="Humidity", value=f"{humidity}%", inline=True)
    embed.add_field(name="Wind", value=f"{wind:.0f} mph", inline=True)
    await ctx.send(embed=embed)

# ---------------------------------------------------------------------------
# ANIMALS
# ---------------------------------------------------------------------------
@bot.command()
async def cat(ctx):
    """Random cat picture."""
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.thecatapi.com/v1/images/search") as resp:
            data = await resp.json()
            embed = discord.Embed(title="🐱 Random Cat", color=0xFEE75C)
            embed.set_image(url=data[0]["url"])
            await ctx.send(embed=embed)

@bot.command()
async def dog(ctx):
    """Random dog picture."""
    async with aiohttp.ClientSession() as session:
        async with session.get("https://dog.ceo/api/breeds/image/random") as resp:
            data = await resp.json()
            embed = discord.Embed(title="🐶 Random Dog", color=0xFEE75C)
            embed.set_image(url=data[0] if isinstance(data, list) else data["message"])
            await ctx.send(embed=embed)

# ---------------------------------------------------------------------------
# WOULD YOU RATHER
# ---------------------------------------------------------------------------
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

@bot.command()
async def wyr(ctx):
    """Would You Rather — vote with reactions!"""
    a, b = random.choice(WYR_QUESTIONS)
    embed = make_embed("🤔 Would You Rather...",
        f"🅰️ {a}\n\n**OR**\n\n🅱️ {b}", 0xEB459E)
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🅰️")
    await msg.add_reaction("🅱️")

# ---------------------------------------------------------------------------
# ON THIS DAY
# ---------------------------------------------------------------------------
@bot.command()
async def onthisday(ctx):
    """Get a random historical event that happened on this day."""
    today = datetime.now()
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
        f"**{year}**: {desc}", 0xE67E22)
    await ctx.send(embed=embed)

# ---------------------------------------------------------------------------
# NICKNAME CHANGE
# ---------------------------------------------------------------------------
@bot.command()
async def changenick(ctx, member: discord.Member, *, new_nick: str):
    """Pay coins to change someone's nickname for 24 hours."""
    if member.id == ctx.author.id:
        return await ctx.send("You can't change your own nickname with this!")
    if member.bot:
        return await ctx.send("You can't rename bots!")

    bal = get_balance(ctx.author.id)
    if bal < NICKNAME_COST:
        return await ctx.send(embed=make_embed("❌ Not Enough Coins",
            f"Changing a nickname costs **{NICKNAME_COST}** coins.\nYou have **{bal}**.", 0xED4245))

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
        f"Cost: **{NICKNAME_COST}** coins | Balance: **{new_bal}**", 0x57F287))

# ---------------------------------------------------------------------------
# NICKNAME RESTORE TASK
# ---------------------------------------------------------------------------
@tasks.loop(minutes=5)
async def restore_nicknames():
    """Check for expired nickname changes and restore them."""
    now = datetime.now(timezone.utc)
    rows = db.execute("SELECT id, guild_id, target_id, original_nick, expires_at FROM nick_changes").fetchall()
    for row_id, guild_id, target_id, original_nick, expires_at in rows:
        expires = datetime.fromisoformat(expires_at)
        if now >= expires:
            guild = bot.get_guild(guild_id)
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
# LEADERBOARD
# ---------------------------------------------------------------------------
@bot.command(aliases=["lb", "top"])
async def leaderboard(ctx):
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
    await ctx.send(embed=make_embed("🏆 Leaderboard", "\n".join(lines), 0xF1C40F))

# ---------------------------------------------------------------------------
# HELP OVERRIDE (nicer embed)
# ---------------------------------------------------------------------------
bot.remove_command("help")

@bot.command()
async def help(ctx):
    """Show all commands."""
    embed = discord.Embed(title="📖 Bot Commands", color=0x5865F2)
    embed.add_field(name="💰 Economy", value=(
        "`!daily` — Claim daily coins\n"
        "`!balance` — Check balance\n"
        "`!leaderboard` — Top 10 richest"
    ), inline=False)
    embed.add_field(name="🎲 Gambling", value=(
        "`!coinflip <amt>` — Double or nothing\n"
        "`!slots <amt>` — Slot machine\n"
        "`!blackjack <amt>` — Play 21"
    ), inline=False)
    embed.add_field(name="🌤️ Weather", value="`!weather <city>` — Current weather", inline=False)
    embed.add_field(name="🐾 Animals", value="`!cat` / `!dog` — Random pics", inline=False)
    embed.add_field(name="🎉 Fun", value=(
        "`!wyr` — Would You Rather\n"
        "`!onthisday` — Historical event today\n"
        f"`!changenick @user name` — Change nickname ({NICKNAME_COST} coins)"
    ), inline=False)
    await ctx.send(embed=embed)

# ---------------------------------------------------------------------------
# RUN
# ---------------------------------------------------------------------------
bot.run(TOKEN)
