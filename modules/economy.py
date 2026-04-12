import discord
import random
import json
from datetime import datetime

from discord.ext import commands

from shared import *

# ECONOMY: LUCKY GUESS
# ---------------------------------------------------------------------------
active_puzzles = {}  # user_id -> {"answer": str, "type": str, "display": str}
def load_active_puzzle(user_id: int):
    row = db.execute(
        "SELECT active_puzzle_type, active_puzzle_answer, active_puzzle_display, active_puzzle_guesses "
        "FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    puzzle_type, answer, display, guesses_raw = row
    if not puzzle_type or not answer or not display:
        return None
    puzzle = {
        "type": puzzle_type,
        "answer": answer.lower().strip(),
        "display": display,
    }
    if puzzle_type == "wordle":
        try:
            guesses = json.loads(guesses_raw or "[]")
            if not isinstance(guesses, list):
                guesses = []
        except json.JSONDecodeError:
            guesses = []
        puzzle["guesses"] = [str(g).lower().strip() for g in guesses if isinstance(g, str)]
    return puzzle
def save_active_puzzle(user_id: int, puzzle):
    get_balance(user_id)
    if puzzle is None:
        db.execute(
            "UPDATE users SET active_puzzle_type = '', active_puzzle_answer = '', "
            "active_puzzle_display = '', active_puzzle_guesses = '[]' WHERE user_id = ?",
            (user_id,),
        )
        db.commit()
        return
    guesses = puzzle.get("guesses", []) if puzzle.get("type") == "wordle" else []
    db.execute(
        "UPDATE users SET active_puzzle_type = ?, active_puzzle_answer = ?, active_puzzle_display = ?, "
        "active_puzzle_guesses = ? WHERE user_id = ?",
        (
            puzzle.get("type", ""),
            puzzle.get("answer", ""),
            puzzle.get("display", ""),
            json.dumps(guesses),
            user_id,
        ),
    )
    db.commit()
WORDLE_WORDS = [
    "crane", "slate", "audio", "raise", "stare", "glyph", "dwarf", "knobs",
    "plumb", "frost", "shrug", "traps", "blaze", "chunk", "crimp", "dough",
    "flame", "gripe", "hoist", "joust", "knelt", "lurch", "mirth", "notch",
    "pouch", "quilt", "roast", "swirl", "thump", "vexed", "whirl", "yacht",
    "blunt", "chase", "drift", "forge", "gleam", "hover", "jelly", "knack",
]
UNSCRAMBLE_WORDS = [
    "python", "server", "cursor", "syntax", "binary", "plugin", "socket",
    "kernel", "thread", "buffer", "matrix", "cipher", "router", "branch",
    "module", "render", "signal", "toggle", "vector", "widget", "bridge",
    "portal", "anchor", "beacon", "cipher", "faucet", "gadget", "jumble",
]
TRIVIA = [
    ("What planet is known as the Red Planet?", "mars"),
    ("What is the chemical symbol for gold?", "au"),
    ("How many bits are in a byte?", "8"),
    ("What language is the Linux kernel written in?", "c"),
    ("What does HTTP stand for?", "hypertext transfer protocol"),
    ("What is the largest ocean on Earth?", "pacific"),
    ("What gas do plants absorb from the atmosphere?", "carbon dioxide"),
    ("What is the square root of 144?", "12"),
    ("What year did the World Wide Web go public?", "1991"),
    ("What does CPU stand for?", "central processing unit"),
    ("What element has the atomic number 1?", "hydrogen"),
    ("How many sides does a hexagon have?", "6"),
    ("What is the speed of light in km/s (rounded)?", "300000"),
    ("What programming language was created by Guido van Rossum?", "python"),
    ("What does RAM stand for?", "random access memory"),
]
CODE_PUZZLES = [
    ("What does this print?\n```python\nprint(len('hello' * 3))```", "15"),
    ("What does this print?\n```python\nprint(2 ** 10)```", "1024"),
    ("What does this print?\n```python\nprint('abc'[::-1])```", "cba"),
    ("What does this print?\n```python\nprint(bool([]))```", "false"),
    ("What does this print?\n```python\nprint(type(3.14).__name__)```", "float"),
    ("What does this print?\n```python\nprint(max(3, 1, 4, 1, 5))```", "5"),
    ("What does this print?\n```python\nprint(10 // 3)```", "3"),
    ("What does this print?\n```python\nprint('hello world'.count('l'))```", "3"),
    ("What does this print?\n```python\nprint(sum(range(5)))```", "10"),
    ("What does this print?\n```python\nprint(list(zip([1,2],[3,4])))```", "[(1, 3), (2, 4)]"),
]
def generate_math_puzzle():
    """Generate a math puzzle with multiple operations."""
    kind = random.choice(["solve_x", "evaluate", "remainder", "sequence"])
    if kind == "solve_x":
        # ax + b = c, solve for x
        a = random.choice([2, 3, 4, 5, 6, 7, 8])
        x = random.randint(-15, 15)
        b = random.randint(-20, 20)
        c = a * x + b
        sign = f"+ {b}" if b >= 0 else f"- {abs(b)}"
        question = f"Solve for x: **{a}x {sign} = {c}**"
        return question, str(x)
    elif kind == "evaluate":
        # a * b + c * d
        a, b = random.randint(2, 12), random.randint(2, 12)
        c, d = random.randint(2, 12), random.randint(2, 12)
        answer = a * b + c * d
        question = f"What is **{a} × {b} + {c} × {d}**?"
        return question, str(answer)
    elif kind == "remainder":
        a = random.randint(50, 200)
        b = random.randint(3, 15)
        answer = a % b
        question = f"What is the remainder of **{a} ÷ {b}**?"
        return question, str(answer)
    else:
        # Find the next number in an arithmetic sequence
        start = random.randint(1, 20)
        step = random.randint(2, 10) * random.choice([1, -1])
        seq = [start + step * i for i in range(5)]
        answer = start + step * 5
        display = ", ".join(str(n) for n in seq)
        question = f"What comes next? **{display}, ?**"
        return question, str(answer)
WORDLE_MAX_GUESSES = 6
def wordle_feedback(guess, answer):
    """Return colored tile feedback for a wordle guess."""
    result = ["⬛"] * 5
    answer_chars = list(answer)
    # Green pass: correct letter, correct position
    for i in range(5):
        if guess[i] == answer[i]:
            result[i] = "🟩"
            answer_chars[i] = None
    # Yellow pass: correct letter, wrong position
    for i in range(5):
        if result[i] == "🟩":
            continue
        if guess[i] in answer_chars:
            result[i] = "🟨"
            answer_chars[answer_chars.index(guess[i])] = None
    tiles = "".join(result)
    letters = " ".join(f"**{c}**" for c in guess)
    return f"{tiles}\n{letters}"
def wordle_display(puzzle):
    """Render the full wordle board."""
    lines = []
    for g in puzzle.get("guesses", []):
        lines.append(wordle_feedback(g, puzzle["answer"]))
    remaining = WORDLE_MAX_GUESSES - len(puzzle.get("guesses", []))
    lines.append(f"\nGuesses left: **{remaining}**")
    return "\n".join(lines)
def generate_wordle_puzzle():
    """Generate a wordle puzzle."""
    word = random.choice(WORDLE_WORDS)
    question = f"Guess a 5-letter word! You have **{WORDLE_MAX_GUESSES}** tries."
    return question, word
def generate_unscramble_puzzle():
    word = random.choice(UNSCRAMBLE_WORDS)
    letters = list(word)
    while True:
        random.shuffle(letters)
        scrambled = "".join(letters)
        if scrambled != word:
            break
    question = f"Unscramble this word: **{scrambled}**"
    return question, word
def generate_puzzle():
    """Pick a random puzzle type and generate it."""
    kind = random.choice(["math", "wordle", "unscramble", "trivia", "code"])
    if kind == "math":
        q, a = generate_math_puzzle()
        return kind, q, a
    elif kind == "wordle":
        q, a = generate_wordle_puzzle()
        return kind, q, a
    elif kind == "unscramble":
        q, a = generate_unscramble_puzzle()
        return kind, q, a
    elif kind == "trivia":
        q, a = random.choice(TRIVIA)
        return kind, q, a
    else:
        q, a = random.choice(CODE_PUZZLES)
        return kind, q, a
PUZZLE_TITLES = {
    "math": "🧮 Math Puzzle",
    "wordle": "📝 Word Puzzle",
    "unscramble": "🔀 Unscramble",
    "trivia": "🧠 Trivia",
    "code": "💻 Code Puzzle",
}
SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]
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

def dealer_up_value(card):
    rank = card[0]
    if rank in ("J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)

def is_soft_hand(hand):
    total = 0
    aces = 0
    for rank, _ in hand:
        if rank in ("J", "Q", "K"):
            total += 10
        elif rank == "A":
            total += 11
            aces += 1
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    # Soft hand means at least one ace still valued as 11.
    return any(rank == "A" for rank, _ in hand) and total <= 21 and aces > 0

def blackjack_recommendation(player_hand, dealer_up_card):
    total = hand_value(player_hand)
    up = dealer_up_value(dealer_up_card)
    soft = is_soft_hand(player_hand)

    if soft:
        if total >= 19:
            return "stand", "soft 19+"
        if total == 18:
            if up in (9, 10, 11):
                return "hit", "soft 18 vs 9/10/A"
            return "stand", "soft 18 vs 2-8"
        return "hit", "soft 17 or less"

    if total >= 17:
        return "stand", "hard 17+"
    if 13 <= total <= 16:
        if 2 <= up <= 6:
            return "stand", "hard 13-16 vs 2-6"
        return "hit", "hard 13-16 vs 7-A"
    if total == 12:
        if 4 <= up <= 6:
            return "stand", "hard 12 vs 4-6"
        return "hit", "hard 12 vs 2-3/7-A"
    return "hit", "hard 11 or less"

class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def guess(self, ctx, number: int):
        """Guess a number 1-{LUCKY_GUESS_RANGE} for a free coin (up to {LUCKY_GUESS_MAX_DAILY}x/day)."""
        user_id = ctx.author.id
        bal = get_balance(user_id)
    
        if bal > 0:
            await ctx.send(embed=make_embed("🚫 Not Broke Enough", f"You still have **{bal}** coins! Guess is only for when you're at **0**.", COLOR_ERROR))
            return
    
        if number < 1 or number > LUCKY_GUESS_RANGE:
            await ctx.send(embed=make_embed("❌ Invalid", f"Pick a number between **1** and **{LUCKY_GUESS_RANGE}**.", COLOR_ERROR))
            return
    
        now = datetime.now(CENTRAL_TZ)
        today = now.strftime("%Y-%m-%d")
        row = db.execute(
            "SELECT guess_date, guess_count FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        guess_date = row[0] if row else ""
        guess_count = row[1] if row else 0
    
        if guess_date == today and guess_count >= LUCKY_GUESS_MAX_DAILY:
            await ctx.send(embed=make_embed(
                "🚫 No Guesses Left",
                f"You've used all **{LUCKY_GUESS_MAX_DAILY}** guesses today. Try again tomorrow!",
                COLOR_ERROR))
            return
    
        # Reset count if it's a new day
        if guess_date != today:
            guess_count = 0
    
        guess_count += 1
        db.execute(
            "UPDATE users SET guess_date = ?, guess_count = ? WHERE user_id = ?",
            (today, guess_count, user_id))
        db.commit()
    
        answer = random.randint(1, LUCKY_GUESS_RANGE)
        remaining = LUCKY_GUESS_MAX_DAILY - guess_count
    
        if number == answer:
            update_balance(user_id, LUCKY_GUESS_REWARD)
            bal = get_balance(user_id)
            await ctx.send(embed=make_embed(
                f"🎯 Correct! The number was **{answer}**!",
                f"You won **{LUCKY_GUESS_REWARD}** coin!\n"
                f"Balance: **{bal}** | Guesses left today: **{remaining}**",
                COLOR_SUCCESS))
        else:
            bal = get_balance(user_id)
            await ctx.send(embed=make_embed(
                f"❌ Nope! The number was **{answer}**.",
                f"Better luck next time!\n"
                f"Balance: **{bal}** | Guesses left today: **{remaining}**",
                COLOR_ERROR))
    
    # ---------------------------------------------------------------------------
    # ECONOMY: DAILY PUZZLE
    # ---------------------------------------------------------------------------

    @commands.command()
    async def puzzle(self, ctx):
        """Get your daily puzzle for a coin bonus."""
        user_id = ctx.author.id
        get_balance(user_id)
    
        now = datetime.now(CENTRAL_TZ)
        today = now.strftime("%Y-%m-%d")
        row = db.execute(
            "SELECT puzzle_date, puzzle_solved, puzzle_attempts FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        puzzle_date = row[0] if row else ""
        puzzle_solved = row[1] if row else 0
        puzzle_attempts = row[2] if row else 0
    
        if puzzle_date == today and puzzle_solved:
            active_puzzles.pop(user_id, None)
            save_active_puzzle(user_id, None)
            return await ctx.send(embed=make_embed("Already Solved", "You already solved today's puzzle! Come back tomorrow.", COLOR_SUCCESS))
        if puzzle_date == today and puzzle_attempts >= PUZZLE_MAX_ATTEMPTS:
            active_puzzles.pop(user_id, None)
            save_active_puzzle(user_id, None)
            return await ctx.send(embed=make_embed("Out of Attempts", f"You've used all **{PUZZLE_MAX_ATTEMPTS}** attempts today. Try again tomorrow!", COLOR_ERROR))
    
        if puzzle_date != today:
            puzzle_attempts = 0
            active_puzzles.pop(user_id, None)
            save_active_puzzle(user_id, None)
            db.execute("UPDATE users SET puzzle_date = ?, puzzle_solved = 0, puzzle_attempts = 0 WHERE user_id = ?", (today, user_id))
            db.commit()
    
        if user_id not in active_puzzles:
            loaded = load_active_puzzle(user_id)
            if loaded:
                active_puzzles[user_id] = loaded
    
        if user_id not in active_puzzles:
            kind, question, answer = generate_puzzle()
            p = {"answer": answer.lower().strip(), "type": kind, "display": question}
            if kind == "wordle":
                p["guesses"] = []
            active_puzzles[user_id] = p
            save_active_puzzle(user_id, p)
    
        p = active_puzzles[user_id]
        remaining = PUZZLE_MAX_ATTEMPTS - puzzle_attempts
        await ctx.send(embed=make_embed(
            PUZZLE_TITLES[p["type"]],
            f"{p['display']}\n\nAnswer with `{PREFIX}solve <answer>`\n"
            f"Reward: **{PUZZLE_REWARD}** coins | Attempts left: **{remaining}**"))
    

    @commands.command()
    async def solve(self, ctx, *, answer: str):
        """Submit your answer to the daily puzzle."""
        user_id = ctx.author.id
        get_balance(user_id)
    
        if user_id not in active_puzzles:
            loaded = load_active_puzzle(user_id)
            if loaded:
                active_puzzles[user_id] = loaded
        if user_id not in active_puzzles:
            return await ctx.send(f"You don't have an active puzzle. Start one with `{PREFIX}puzzle`.")
    
        now = datetime.now(CENTRAL_TZ)
        today = now.strftime("%Y-%m-%d")
        row = db.execute(
            "SELECT puzzle_date, puzzle_solved, puzzle_attempts FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        puzzle_date = row[0] if row else ""
        puzzle_solved = row[1] if row else 0
        puzzle_attempts = row[2] if row else 0
    
        if puzzle_date == today and puzzle_solved:
            active_puzzles.pop(user_id, None)
            save_active_puzzle(user_id, None)
            return await ctx.send(embed=make_embed("Already Solved", "You already solved today's puzzle!", COLOR_SUCCESS))
        if puzzle_date == today and puzzle_attempts >= PUZZLE_MAX_ATTEMPTS:
            active_puzzles.pop(user_id, None)
            save_active_puzzle(user_id, None)
            return await ctx.send(embed=make_embed("Out of Attempts", "No attempts left today!", COLOR_ERROR))
    
        p = active_puzzles[user_id]
        guess = answer.lower().strip()
    
        if p["type"] == "wordle":
            if len(guess) != 5 or not guess.isalpha():
                return await ctx.send("Guess must be a 5-letter word.")
            p["guesses"].append(guess)
            save_active_puzzle(user_id, p)
            if guess == p["answer"]:
                active_puzzles.pop(user_id, None)
                save_active_puzzle(user_id, None)
                update_balance(user_id, PUZZLE_REWARD)
                bal = get_balance(user_id)
                db.execute("UPDATE users SET puzzle_solved = 1 WHERE user_id = ?", (user_id,))
                db.commit()
                return await ctx.send(embed=make_embed(
                    f"Wordle Solved in {len(p['guesses'])}!",
                    f"{wordle_display(p)}\n\nYou earned **{PUZZLE_REWARD}** coins!\nBalance: **{bal}**",
                    COLOR_SUCCESS))
            if len(p["guesses"]) >= WORDLE_MAX_GUESSES:
                active_puzzles.pop(user_id, None)
                save_active_puzzle(user_id, None)
                return await ctx.send(embed=make_embed(
                    "Wordle Failed",
                    f"{wordle_display(p)}\n\nThe word was **{p['answer']}**.",
                    COLOR_ERROR))
            return await ctx.send(embed=make_embed(
                "Wordle",
                f"{wordle_display(p)}\n\nGuess again with `{PREFIX}solve <word>`"))
    
        puzzle_attempts += 1
        db.execute("UPDATE users SET puzzle_date = ?, puzzle_attempts = ? WHERE user_id = ?", (today, puzzle_attempts, user_id))
        db.commit()
    
        if guess == p["answer"]:
            active_puzzles.pop(user_id, None)
            save_active_puzzle(user_id, None)
            update_balance(user_id, PUZZLE_REWARD)
            bal = get_balance(user_id)
            db.execute("UPDATE users SET puzzle_solved = 1 WHERE user_id = ?", (user_id,))
            db.commit()
            await ctx.send(embed=make_embed(
                "Correct!",
                f"You earned **{PUZZLE_REWARD}** coins!\nBalance: **{bal}**",
                COLOR_SUCCESS))
        else:
            remaining = PUZZLE_MAX_ATTEMPTS - puzzle_attempts
            if remaining <= 0:
                correct = p["answer"]
                active_puzzles.pop(user_id, None)
                save_active_puzzle(user_id, None)
                await ctx.send(embed=make_embed(
                    "Out of Attempts",
                    f"The answer was **{correct}**.\nBetter luck tomorrow!",
                    COLOR_ERROR))
            else:
                await ctx.send(embed=make_embed(
                    "Wrong",
                    f"That's not it. Attempts left: **{remaining}**",
                    COLOR_ERROR))
    

    @commands.command()
    async def repuzzle(self, ctx, member: discord.Member = None):
        """Admin only: regenerate a user's daily puzzle (or your own)."""
        if ctx.author.id != ADMIN_ID:
            return
        target = member or ctx.author
        active_puzzles.pop(target.id, None)
        save_active_puzzle(target.id, None)
        db.execute("UPDATE users SET puzzle_solved = 0, puzzle_attempts = 0 WHERE user_id = ?", (target.id,))
        db.commit()
        await ctx.send(f"Puzzle reset for {target.display_name}.")
    

    @commands.command()
    async def daily(self, ctx):
    
        """Claim your daily coins."""
        user_id = ctx.author.id
        now = datetime.now(CENTRAL_TZ)
        available, remaining = is_daily_available(user_id, now=now)
        if not available:
            h, m = divmod(int(remaining.total_seconds()) // 60, 60)
            await ctx.send(embed=make_embed("⏰ Already Claimed", f"Come back in **{h}h {m}m**.", COLOR_ERROR))
            return
        update_balance(user_id, DAILY_AMOUNT)
        db.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), user_id))
        db.commit()
        bal = get_balance(user_id)
        await ctx.send(embed=make_embed("💰 Daily Claimed!", f"You got **{DAILY_AMOUNT}** coins!\nBalance: **{bal}**", COLOR_SUCCESS))
    
    # ---------------------------------------------------------------------------
    # ECONOMY: BALANCE
    # ---------------------------------------------------------------------------

    @commands.command(aliases=["bal"])
    async def balance(self, ctx, member: discord.Member = None):
        """Check your coin balance (or someone else's)."""
        target = member or ctx.author
        bal = peek_balance(target.id) if member else get_balance(target.id)
        await ctx.send(embed=make_embed(f"💵 {target.display_name}'s Balance", f"**{bal}** coins"))
    
    # ---------------------------------------------------------------------------
    # GAMBLING: COINFLIP
    # ---------------------------------------------------------------------------

    @commands.command(aliases=["cf"])
    async def coinflip(self, ctx, amount: int):
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
                f"You won **{amount}** coins!\nBalance: **{new_bal}**", COLOR_SUCCESS))
        else:
            update_balance(ctx.author.id, -amount)
            new_bal = get_balance(ctx.author.id)
            await ctx.send(embed=make_embed(
                f"🪙 {result.title()}! You lose!",
                f"You lost **{amount}** coins.\nBalance: **{new_bal}**", COLOR_ERROR))
    
    # ---------------------------------------------------------------------------
    # GAMBLING: SLOTS
    # ---------------------------------------------------------------------------

    @commands.command()
    async def slots(self, ctx, amount: int):
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
                f"**JACKPOT!** You won **{winnings}** coins! (x{multiplier})\nBalance: **{new_bal}**", COLOR_SUCCESS))
        elif reels[0] == reels[1] or reels[1] == reels[2]:
            winnings = amount
            update_balance(ctx.author.id, winnings)
            new_bal = get_balance(ctx.author.id)
            await ctx.send(embed=make_embed(
                f"🎰 {display}",
                f"Two in a row! You won **{winnings}** coins!\nBalance: **{new_bal}**", COLOR_WARNING))
        else:
            update_balance(ctx.author.id, -amount)
            new_bal = get_balance(ctx.author.id)
            await ctx.send(embed=make_embed(
                f"🎰 {display}",
                f"No match. You lost **{amount}** coins.\nBalance: **{new_bal}**", COLOR_ERROR))
    
    # ---------------------------------------------------------------------------
    # GAMBLING: BLACKJACK
    # ---------------------------------------------------------------------------

    @commands.command(aliases=["bj"])
    async def blackjack(self, ctx, amount: int):
        """Play a hand of blackjack."""
        if ctx.author.id in active_blackjack:
            return await ctx.send(f"You already have a hand going! Use `{PREFIX}hit` or `{PREFIX}stand`.")
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
                f"You won **{winnings}** coins!\nBalance: **{new_bal}**", COLOR_SUCCESS))
            return
    
        active_blackjack[ctx.author.id] = {
            "deck": deck, "player": player, "dealer": dealer, "bet": amount
        }
        await ctx.send(embed=make_embed("🃏 Blackjack",
            f"Your hand: {display_hand(player)} → **{pv}**\n"
            f"Dealer shows: {display_hand(dealer[:1])} `??`\n\n"
            f"Type `{PREFIX}hit` or `{PREFIX}stand`"))
    

    @commands.command()
    async def hit(self, ctx):
        """Draw another card in blackjack."""
        game = active_blackjack.get(ctx.author.id)
        if not game:
            return await ctx.send(f"You don't have an active blackjack hand. Start one with `{PREFIX}blackjack <amount>`.")
    
        game["player"].append(game["deck"].pop())
        pv = hand_value(game["player"])
    
        if pv > 21:
            update_balance(ctx.author.id, -game["bet"])
            new_bal = get_balance(ctx.author.id)
            del active_blackjack[ctx.author.id]
            await ctx.send(embed=make_embed("🃏 Bust!",
                f"Your hand: {display_hand(game['player'])} → **{pv}**\n"
                f"You lost **{game['bet']}** coins.\nBalance: **{new_bal}**", COLOR_ERROR))
        elif pv == 21:
            await self.stand(ctx)
        else:
            await ctx.send(embed=make_embed("🃏 Blackjack",
                f"Your hand: {display_hand(game['player'])} → **{pv}**\n"
                f"Dealer shows: {display_hand(game['dealer'][:1])} `??`\n\n"
                f"Type `{PREFIX}hit` or `{PREFIX}stand`"))
    

    @commands.command()
    async def stand(self, ctx):
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
                f"You won **{game['bet']}** coins!\nBalance: **{new_bal}**", COLOR_SUCCESS))
        elif pv < dv:
            update_balance(ctx.author.id, -game["bet"])
            new_bal = get_balance(ctx.author.id)
            await ctx.send(embed=make_embed("🃏 Dealer Wins", result_lines +
                f"You lost **{game['bet']}** coins.\nBalance: **{new_bal}**", COLOR_ERROR))
        else:
            new_bal = get_balance(ctx.author.id)
            await ctx.send(embed=make_embed("🃏 Push!", result_lines +
                f"It's a tie. Your **{game['bet']}** coins are returned.\nBalance: **{new_bal}**", COLOR_WARNING))
    


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
