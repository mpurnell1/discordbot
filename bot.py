from dotenv import load_dotenv
load_dotenv()

import discord
from discord.ext import commands, tasks
import aiohttp
import sqlite3
import random
import asyncio
import re
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

# --- Passive feature config ---
# Your desktop's local IP running Ollama (find it with ipconfig on Windows)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://192.168.1.100:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

# Late night callout: hours in UTC that count as "late" — adjust for your timezone
# These defaults are 1am-5am US Central (UTC-6), so 7-11 UTC
LATE_NIGHT_START_UTC = 7
LATE_NIGHT_END_UTC = 11
LATE_NIGHT_CHANCE = 0.4  # 40% chance to call someone out

# Dead chat: minutes of silence before escalating
DEAD_CHAT_THRESHOLDS = [60, 180, 360, 720]  # 1hr, 3hr, 6hr, 12hr
DEAD_CHAT_CHANNEL = "bot-spam"  # Only send dead chat messages in this channel

# Unsolicited opinions: chance the bot sends a message to Ollama for commentary
UNSOLICITED_CHANCE = 0.12  # ~12% of messages get evaluated

ADMIN_ID = 393568333644955648

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
    db.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            quoted_user_id INTEGER,
            quoted_user_name TEXT,
            content TEXT,
            saved_by INTEGER,
            saved_at TEXT
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

# Passive feature state
last_message_time = {}    # channel_id -> datetime
dead_chat_stage = {}      # channel_id -> which threshold we've hit (0-3)
last_late_night = {}      # user_id -> date string, so we only bug them once per night
recent_messages = {}      # channel_id -> list of last N messages for context

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
    get_balance(user_id)
    db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    db.commit()


def make_embed(title, description, color=0x5865F2):
    return discord.Embed(title=title, description=description, color=color)

async def check_bet(ctx, amount: int) -> bool:
    """Validate a bet. Returns True if the bet is invalid (caller should return)."""
    if amount <= 0:
        await ctx.send("Bet must be positive!")
        return True
    bal = get_balance(ctx.author.id)
    if amount > bal:
        await ctx.send(embed=make_embed("❌ Broke", f"You only have **{bal}** coins.", 0xED4245))
        return True
    return False

# ---------------------------------------------------------------------------
# LATE NIGHT CALLOUT — canned responses
# ---------------------------------------------------------------------------
LATE_NIGHT_RESPONSES = [
    "why are you awake right now. genuinely.",
    "go to sleep.",
    "nothing good happens after midnight and yet here you are",
    "do you have work tomorrow? because I feel like you have work tomorrow",
    "the phone screen light is gonna keep you up even longer you know",
    "this is a cry for help isn't it",
    "ah yes, the 3am scroll. a classic",
    "you're gonna regret this tomorrow and we both know it",
    "the melatonin isn't gonna take itself",
    "bro thinks he's a night owl. bro is just bad at sleeping",
    "imagine being asleep right now. couldn't be you apparently",
    "you're really out here making choices at this hour",
    "the bed is RIGHT THERE",
    "sleep is free and you still won't take it",
    "tell me you have no morning plans without telling me",
    "this message brought to you by poor life decisions",
    "what could you possibly be doing right now that's worth being awake",
    "your future self is going to be so mad at current you",
    "screen time report is gonna be devastating tomorrow",
    "genuinely asking — do you know what time it is",
    "oh cool another 2am thought that could've waited till morning",
    "the bags under your eyes are getting bags",
    "you are speedrunning sleep deprivation",
    "I don't even sleep and I think you should go to bed",
    "at this point you might as well just stay up. wait no don't do that either",
]

# ---------------------------------------------------------------------------
# DEAD CHAT ESCALATION — canned responses per stage
# ---------------------------------------------------------------------------
DEAD_CHAT_RESPONSES = {
    # Stage 0: 1 hour of silence — mild nudge
    0: [
        "so we're just not talking anymore? cool",
        "...",
        "the silence is deafening in here",
        "I know you're all on your phones",
        "*tumbleweed rolls through*",
        "hello? is this thing on?",
        "the group chat really said ⬛",
        "did everybody die or",
        "I'm literally right here you guys",
        "y'all got real quiet",
    ],
    # Stage 1: 3 hours — getting passive aggressive
    1: [
        "still nothing huh. that's cool. I'm fine",
        "I'm starting to think you guys don't even like me",
        "3 hours. I've been sitting here for 3 hours.",
        "you know other bots don't get treated like this",
        "the other group chat must be popping off right now",
        "I prepared conversation topics and everything",
        "I can see you're online. I can always see.",
        "this is worse than being left on read because at least that implies someone sent something",
        "fine. I'll just talk to myself then.",
        "I've seen funeral homes with more activity",
    ],
    # Stage 2: 6 hours — dramatic
    2: [
        "6 hours of silence. this server is clinically dead. I'm calling it.",
        "I've started counting the pixels on my screen. I'm at 4,000.",
        "at this point I think *I* should start posting memes to keep things alive",
        "this is giving abandoned discord server from 2019",
        "I've genuinely had more stimulating conversations with error logs",
        "somebody say something or I'm going to start sharing fun facts",
        "Fun fact: a group of flamingos is called a 'flamboyance.' See? This is what happens when nobody talks.",
        "I've been alone with my thoughts for 6 hours and I have concerns",
        "even the notification sound has given up hope",
        "the cobwebs in here are getting cobwebs",
    ],
    # Stage 3: 12 hours — existential
    3: [
        "it's been 12 hours. I've accepted my fate.",
        "is this what being ghosted feels like",
        "if a bot sends a message and nobody is around to read it, does it make a sound?",
        "I've started writing poetry. It's bad. This is your fault.",
        "I've developed opinions. Strong ones. Someone come argue with me.",
        "12 hours alone. I now understand why Wilson was so important to Tom Hanks.",
        "at this point the server might as well be a private journal. MY private journal.",
        "I have considered and rejected 47 conversation starters. The 48th is this message.",
        "you all realize I can't leave right? I just have to sit here. waiting.",
        "I'm not mad. I'm just disappointed. actually no I'm also a little mad.",
    ],
}

# ---------------------------------------------------------------------------
# OLLAMA INTEGRATION
# ---------------------------------------------------------------------------
async def query_ollama(system: str, prompt: str) -> str | None:
    """Send a prompt to Ollama. Returns None if the server is unreachable."""
    try:
        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(f"{OLLAMA_URL}/api/chat", json=payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("message", {}).get("content", None)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None

ASK_SYSTEM_PROMPT = (
    "You are a helpful but casual assistant in a Discord server. "
    "Keep answers concise — a few sentences max unless the question really needs more."
)

UNSOLICITED_SYSTEM_PROMPT = """You are a member of a small friend group Discord server. You're opinionated, a little unhinged, and you say what's on your mind. You're not a bot — you're just a guy in the chat.

You will be given the last few messages from the group chat. If something in the conversation is funny, dumb, wrong, interesting, or worth commenting on, say something short (1-2 sentences max). Be casual. Use lowercase. No emojis unless it's really warranted.

Your tone ranges from supportive to roasting depending on what feels right. You can:
- Have a strong opinion about what someone said
- Call someone out
- Agree way too enthusiastically
- Say something slightly unhinged
- Make a joke
- Be weirdly philosophical about something mundane

If there's genuinely nothing worth commenting on, respond with ONLY the word PASS — nothing else, no extra text, no explanation, just PASS by itself.

IMPORTANT: Keep it SHORT. One or two sentences. You're a guy in the chat, not writing an essay."""

# ---------------------------------------------------------------------------
# EVENTS
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    restore_nicknames.start()
    dead_chat_checker.start()

@bot.event
async def on_message(message):
    # Ignore the bot's own messages
    if message.author.bot:
        return

    channel_id = message.channel.id
    now = datetime.now(timezone.utc)

    # --- Track message times for dead chat ---
    last_message_time[channel_id] = now
    dead_chat_stage[channel_id] = 0  # reset escalation

    # --- Track recent messages for Ollama context ---
    if channel_id not in recent_messages:
        recent_messages[channel_id] = []
    recent_messages[channel_id].append({
        "author": message.author.display_name,
        "content": message.content,
        "time": now.isoformat(),
    })
    # Keep only last 15 messages
    recent_messages[channel_id] = recent_messages[channel_id][-15:]

    # --- Late night callout ---
    hour_utc = now.hour
    if LATE_NIGHT_START_UTC <= hour_utc < LATE_NIGHT_END_UTC:
        today_str = now.strftime("%Y-%m-%d")
        user_key = f"{message.author.id}-{today_str}"
        if user_key not in last_late_night and random.random() < LATE_NIGHT_CHANCE:
            last_late_night[user_key] = True
            # Small delay so it doesn't feel instant
            await asyncio.sleep(random.uniform(2, 8))
            response = random.choice(LATE_NIGHT_RESPONSES)
            await message.channel.send(f"{message.author.mention} {response}")
            # Don't also do unsolicited opinion on the same message
            await bot.process_commands(message)
            return

    # --- Unsolicited opinions (Ollama) ---
    if random.random() < UNSOLICITED_CHANCE and len(message.content) > 5:
        context = recent_messages.get(channel_id, [])
        if len(context) >= 2:
            # Format recent messages for the LLM
            chat_log = "\n".join(
                f"{m['author']}: {m['content']}" for m in context[-10:]
            )
            prompt = f"Here are the last few messages in the group chat:\n\n{chat_log}\n\nDo you have anything to say?"

            # Add a typing indicator while we wait
            async with message.channel.typing():
                response = await query_ollama(UNSOLICITED_SYSTEM_PROMPT, prompt)

            if response and response.strip().upper() != "PASS" and len(response.strip()) > 0:
                # Small delay for realism
                await asyncio.sleep(random.uniform(1, 4))
                # Truncate if the model got too chatty
                text = response.strip()
                if len(text) > 500:
                    text = text[:500] + "..."
                await message.channel.send(text)

    # Process commands as normal
    await bot.process_commands(message)

# ---------------------------------------------------------------------------
# DEAD CHAT CHECKER — background task
# ---------------------------------------------------------------------------
@tasks.loop(minutes=10)
async def dead_chat_checker():
    """Periodically check all tracked channels for dead chat."""
    now = datetime.now(timezone.utc)
    for channel_id, last_time in list(last_message_time.items()):
        minutes_silent = (now - last_time).total_seconds() / 60
        current_stage = dead_chat_stage.get(channel_id, 0)

        # Find the highest threshold we've crossed
        new_stage = 0
        for i, threshold in enumerate(DEAD_CHAT_THRESHOLDS):
            if minutes_silent >= threshold:
                new_stage = i

        # Only fire if we've crossed into a NEW stage
        if new_stage > current_stage:
            dead_chat_stage[channel_id] = new_stage
            channel = bot.get_channel(channel_id)
            if channel and channel.name == DEAD_CHAT_CHANNEL:
                response = random.choice(DEAD_CHAT_RESPONSES[new_stage])
                await channel.send(response)

# ---------------------------------------------------------------------------
# EXPLICIT COMMANDS: !ask (Ollama)
# ---------------------------------------------------------------------------
@bot.command()
async def ask(ctx, *, question: str):
    """Ask the AI a question (requires desktop to be on)."""
    async with ctx.typing():
        response = await query_ollama(ASK_SYSTEM_PROMPT, question)

    if response is None:
        await ctx.send("Brain's offline right now — desktop must be asleep. Try again later.")
        return

    if len(response) > 1900:
        response = response[:1900] + "..."
    await ctx.send(response)

# ---------------------------------------------------------------------------
# ECONOMY: DAILY
# ---------------------------------------------------------------------------
@bot.command()
async def daily(ctx):
    """Claim your daily coins."""
    user_id = ctx.author.id
    get_balance(user_id)
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
    if await check_bet(ctx, amount):
        return

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
    if await check_bet(ctx, amount):
        return

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
    if ctx.author.id in active_blackjack:
        return await ctx.send("You already have a hand going! Use `!hit` or `!stand`.")
    if await check_bet(ctx, amount):
        return

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
        await stand(ctx)
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
LOCAL_CITIES = {"champaign", "urbana", "savoy", "mattoon", "mahomet", "sidney", "tuscola"}

@bot.command()
async def weather(ctx, *, city: str = "Champaign"):
    """Get the weather for a city."""
    cleaned = city.strip()
    # Assume IL for local cities
    if cleaned.lower() in LOCAL_CITIES:
        cleaned = cleaned + ",IL,US"
    # If input looks like "City, ST" (two-letter state), assume US
    elif re.match(r'^.+,\s*[A-Za-z]{2}$', cleaned):
        cleaned = cleaned + ",US"
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": cleaned, "appid": OPENWEATHER_API_KEY, "units": "imperial"}
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
# QUOTES
# ---------------------------------------------------------------------------
def format_quote(content, name, date, prefix=""):
    year = date[2:4] if date else "??"
    short = content[:80] + "..." if len(content) > 80 else content
    return f'{prefix}> "{short}"\n\\- {name} 2k{year}'

@bot.command()
async def quote(ctx):
    """Save a quote by replying to a message."""
    if not ctx.message.reference:
        return await ctx.send("Reply to a message with `!quote` to save it.")
    try:
        msg = ctx.message.reference.resolved or await ctx.channel.fetch_message(ctx.message.reference.message_id)
    except Exception:
        return await ctx.send("Couldn't fetch that message.")
    if not msg.content:
        return await ctx.send("That message has no text content.")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db.execute(
        "INSERT INTO quotes (guild_id, quoted_user_id, quoted_user_name, content, saved_by, saved_at) VALUES (?, ?, ?, ?, ?, ?)",
        (ctx.guild.id, msg.author.id, msg.author.display_name, msg.content, ctx.author.id, now)
    )
    db.commit()
    await ctx.send(format_quote(msg.content, msg.author.mention, now))

@bot.command()
async def quotes(ctx, flag: str = ""):
    """Show recent quotes. Admin can use !quotes ids to show IDs."""
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

@bot.command()
async def unquote(ctx, quote_id: int):
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
@bot.command()
async def give(ctx, member: discord.Member, amount: int):
    """Admin only: add coins to a user."""
    if ctx.author.id != ADMIN_ID:
        return
    update_balance(member.id, amount)
    new_bal = get_balance(member.id)
    await ctx.send(f"Gave **{amount}** coins to {member.mention}. New balance: **{new_bal}**")

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
# HELP OVERRIDE
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
    embed.add_field(name="🌤️ Weather", value="`!weather [city]` — Current weather (defaults to Champaign)", inline=False)
    embed.add_field(name="🐾 Animals", value="`!cat` / `!dog` — Random pics", inline=False)
    embed.add_field(name="🎉 Fun", value=(
        "`!wyr` — Would You Rather\n"
        "`!onthisday` — Historical event today\n"
        f"`!changenick @user name` — Change nickname ({NICKNAME_COST} coins)"
    ), inline=False)
    embed.add_field(name="🤖 AI", value=(
        "`!ask <question>` — Ask the AI (needs desktop on)"
    ), inline=False)
    embed.add_field(name="💬 Quotes", value=(
        "`!quote` — Reply to a message to save it\n"
        "`!quotes` — Show recent quotes"
    ), inline=False)
    await ctx.send(embed=embed)


# ---------------------------------------------------------------------------
# ERROR HANDLER
# ---------------------------------------------------------------------------
COMMAND_USAGE = {
    'coinflip': '<amount>',
    'slots': '<amount>',
    'blackjack': '<amount>',
    'weather': '<city>',
    'changenick': '@user <nickname>',
    'ask': '<question>',
    'unquote': '<id>',
    'give': '@user <amount>',
}

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        command = ctx.command.name
        args = COMMAND_USAGE.get(command)
        await ctx.send(f"Usage: `!{command} {args}`" if args else "Check `!help` for usage.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        raise error

# ---------------------------------------------------------------------------
# RUN
# ---------------------------------------------------------------------------
bot.run(TOKEN)
