import discord
import random
import json
from datetime import datetime

from discord.ext import commands

import shared
from shared import (
    PREFIX,
    CENTRAL_TZ,
    ADMIN_ID,
    COLOR_DEFAULT,
    COLOR_SUCCESS,
    COLOR_ERROR,
    COLOR_WARNING,
    LUCKY_GUESS_RANGE,
    LUCKY_GUESS_REWARD,
    LUCKY_GUESS_MAX_DAILY,
    PUZZLE_REWARD,
    PUZZLE_MAX_ATTEMPTS,
    get_balance,
    peek_balance,
    update_balance,
    check_bet,
    make_embed,
    is_kids_mode_guild,
)

# ECONOMY: LUCKY GUESS
# ---------------------------------------------------------------------------
active_puzzles = {}  # (scope, user_id) -> {"answer": str, "type": str, "display": str}

def _puzzle_key(user_id: int, kids_mode: bool):
    return ("kids" if kids_mode else "regular", user_id)

def load_active_puzzle(user_id: int):
    row = shared.db.execute(
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
        shared.db.execute(
            "UPDATE users SET active_puzzle_type = '', active_puzzle_answer = '', "
            "active_puzzle_display = '', active_puzzle_guesses = '[]' WHERE user_id = ?",
            (user_id,),
        )
        shared.db.commit()
        return
    guesses = puzzle.get("guesses", []) if puzzle.get("type") == "wordle" else []
    shared.db.execute(
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
    shared.db.commit()
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
BLACKJACK_RAW_ACTIONS = {"hit", "stand", "double", "split", "surrender"}
BLACKJACK_RULE_PRESETS = {
    "realistic": {
        "decks": 6,
        "reshuffle_at_cards": 78,
        "blackjack_payout": 1.5,  # 3:2
        "dealer_hits_soft_17": False,
        "double_any_two": True,
        "double_after_split": True,
        "allow_split": True,
        "allow_ten_value_split": True,
        "max_hands": 4,
        "resplit_aces": False,
        "draw_to_split_aces": False,
        "late_surrender": True,
        "five_card_charlie": False,
    },
    "arcade": {
        "decks": 6,
        "reshuffle_at_cards": 78,
        "blackjack_payout": 2.0,  # player-favorable: 2:1 blackjack payout
        "dealer_hits_soft_17": False,
        "double_any_two": True,
        "double_after_split": True,
        "allow_split": True,
        "allow_ten_value_split": True,
        "max_hands": 4,
        "resplit_aces": True,
        "draw_to_split_aces": True,
        "late_surrender": True,
        "five_card_charlie": True,
    },
}
BLACKJACK_RULES = {}

BLACKJACK_SUITS = ["♠", "♥", "♦", "♣"]
BLACKJACK_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
blackjack_shoe = []


def get_blackjack_ruleset_name():
    raw = shared.runtime_settings.get("bj_ruleset", "realistic")
    name = str(raw).strip().lower()
    return name if name in BLACKJACK_RULE_PRESETS else "realistic"


def apply_blackjack_ruleset(name: str):
    selected = str(name).strip().lower()
    if selected not in BLACKJACK_RULE_PRESETS:
        selected = "realistic"
    BLACKJACK_RULES.clear()
    BLACKJACK_RULES.update(BLACKJACK_RULE_PRESETS[selected])
    return selected


def make_shoe():
    shoe = []
    for _ in range(BLACKJACK_RULES["decks"]):
        shoe.extend((r, s) for s in BLACKJACK_SUITS for r in BLACKJACK_RANKS)
    random.shuffle(shoe)
    return shoe


def shoe_needs_shuffle():
    return len(blackjack_shoe) <= BLACKJACK_RULES["reshuffle_at_cards"]


def draw_from_shoe():
    global blackjack_shoe
    if not blackjack_shoe or shoe_needs_shuffle():
        blackjack_shoe = make_shoe()
    return blackjack_shoe.pop()


def card_points(rank):
    if rank in ("J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def hand_total_and_soft(hand):
    total = 0
    aces_as_eleven = 0
    for rank, _ in hand:
        total += card_points(rank)
        if rank == "A":
            aces_as_eleven += 1
    while total > 21 and aces_as_eleven:
        total -= 10
        aces_as_eleven -= 1
    return total, aces_as_eleven > 0


def hand_value(hand):
    total, _ = hand_total_and_soft(hand)
    return total


def is_soft_hand(hand):
    _, soft = hand_total_and_soft(hand)
    return soft


def is_blackjack(hand):
    return len(hand) == 2 and hand_value(hand) == 21


def display_hand(hand):
    return " ".join(f"`{r}{s}`" for r, s in hand)


def dealer_up_value(card):
    return card_points(card[0])


def split_rank_key(card):
    rank = card[0]
    if rank in ("10", "J", "Q", "K") and BLACKJACK_RULES["allow_ten_value_split"]:
        return "10"
    return rank


def can_split_cards(hand):
    if len(hand) != 2:
        return False
    return split_rank_key(hand[0]) == split_rank_key(hand[1])


def dealer_should_hit(dealer_hand):
    total, soft = hand_total_and_soft(dealer_hand)
    if total < 17:
        return True
    if total == 17 and soft and BLACKJACK_RULES["dealer_hits_soft_17"]:
        return True
    return False


def make_player_hand(cards, bet, from_split=False, split_aces=False):
    return {
        "cards": cards,
        "bet": bet,
        "from_split": from_split,
        "split_aces": split_aces,
        "stood": False,
        "bust": False,
        "surrendered": False,
        "doubled": False,
        "action_count": 0,
    }


def hand_done(hand):
    return hand["stood"] or hand["bust"] or hand["surrendered"]


def available_actions(game, hand):
    actions = ["stand", "hit"]
    if len(hand["cards"]) == 2:
        if (
            (BLACKJACK_RULES["double_any_two"] or hand_value(hand["cards"]) in (9, 10, 11))
            and (BLACKJACK_RULES["double_after_split"] or not hand["from_split"])
        ):
            actions.append("double")
        can_split = (
            BLACKJACK_RULES["allow_split"]
            and can_split_cards(hand["cards"])
            and len(game["hands"]) < BLACKJACK_RULES["max_hands"]
        )
        if can_split:
            if split_rank_key(hand["cards"][0]) != "A" or BLACKJACK_RULES["resplit_aces"]:
                actions.append("split")
        if BLACKJACK_RULES["late_surrender"] and not hand["from_split"] and hand["action_count"] == 0:
            actions.append("surrender")
    return actions


def advance_to_next_hand(game):
    while game["current_hand"] < len(game["hands"]) and hand_done(game["hands"][game["current_hand"]]):
        game["current_hand"] += 1
    return game["current_hand"] >= len(game["hands"])


def blackjack_recommendation(player_hand, dealer_up_card):
    total = hand_value(player_hand)
    up = dealer_up_value(dealer_up_card)
    soft = is_soft_hand(player_hand)

    if can_split_cards(player_hand):
        pair = split_rank_key(player_hand[0])
        if pair in ("A", "8"):
            return "split", f"pair of {pair}s"
        if pair in ("10", "5"):
            pass
        elif pair == "9":
            if up in (7, 10, 11):
                return "stand", "9,9 vs 7/10/A"
            return "split", "9,9 vs 2-6/8-9"
        elif pair == "7":
            if 2 <= up <= 7:
                return "split", "7,7 vs 2-7"
        elif pair == "6":
            if 2 <= up <= 6:
                return "split", "6,6 vs 2-6"
        elif pair in ("2", "3"):
            if 2 <= up <= 7:
                return "split", f"{pair},{pair} vs 2-7"
        elif pair == "4":
            if 5 <= up <= 6:
                return "split", "4,4 vs 5-6"

    if soft:
        if total >= 19:
            return "stand", "soft 19+"
        if total == 18:
            if up in (3, 4, 5, 6):
                return "double", "soft 18 vs 3-6"
            if up in (9, 10, 11):
                return "hit", "soft 18 vs 9/10/A"
            return "stand", "soft 18 vs 2/7/8"
        if total == 17 and up in (3, 4, 5, 6):
            return "double", "soft 17 vs 3-6"
        if total in (15, 16) and up in (4, 5, 6):
            return "double", f"soft {total} vs 4-6"
        if total in (13, 14) and up in (5, 6):
            return "double", f"soft {total} vs 5-6"
        return "hit", "soft 17 or less"

    if total >= 17:
        return "stand", "hard 17+"
    if total == 16 and up in (9, 10, 11):
        return "surrender", "hard 16 vs 9/10/A"
    if total == 15 and up == 10:
        return "surrender", "hard 15 vs 10"
    if total in (9, 10, 11):
        if total == 9 and 3 <= up <= 6:
            return "double", "hard 9 vs 3-6"
        if total == 10 and 2 <= up <= 9:
            return "double", "hard 10 vs 2-9"
        if total == 11 and 2 <= up <= 10:
            return "double", "hard 11 vs 2-10"
    if 13 <= total <= 16:
        if 2 <= up <= 6:
            return "stand", "hard 13-16 vs 2-6"
        return "hit", "hard 13-16 vs 7-A"
    if total == 12:
        if 4 <= up <= 6:
            return "stand", "hard 12 vs 4-6"
        return "hit", "hard 12 vs 2-3/7-A"
    return "hit", "hard 11 or less"


def best_legal_blackjack_recommendation(game, hand, dealer_up_card):
    base_action, base_reason = blackjack_recommendation(hand["cards"], dealer_up_card)
    legal = set(available_actions(game, hand))
    if base_action in legal:
        return base_action, base_reason

    soft_fallback = {
        "surrender": "hit",
        "double": "hit",
        "split": "hit",
    }
    fallback = soft_fallback.get(base_action, "stand")
    if fallback in legal:
        return fallback, f"{base_reason}; {base_action} unavailable -> {fallback}"
    if "hit" in legal:
        return "hit", f"{base_reason}; {base_action} unavailable -> hit"
    if "stand" in legal:
        return "stand", f"{base_reason}; {base_action} unavailable -> stand"

    # Shouldn't happen, but avoid impossible guidance.
    return next(iter(legal)), f"{base_reason}; adjusted to available action"


apply_blackjack_ruleset(get_blackjack_ruleset_name())

class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener("on_message")
    async def blackjack_raw_action_listener(self, message):
        if message.author.bot or message.content.startswith(PREFIX):
            return
        action = message.content.strip().lower()
        if action not in BLACKJACK_RAW_ACTIONS:
            return
        if message.author.id not in active_blackjack:
            return

        ctx = await self.bot.get_context(message)
        command = self.bot.get_command(action)
        if command is None:
            return
        await ctx.invoke(command)

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
        row = shared.db.execute(
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
        shared.db.execute(
            "UPDATE users SET guess_date = ?, guess_count = ? WHERE user_id = ?",
            (today, guess_count, user_id))
        shared.db.commit()
    
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
        kids_mode = ctx.guild is not None and is_kids_mode_guild(ctx.guild.id)
        key = _puzzle_key(user_id, kids_mode)

        if kids_mode:
            if key not in active_puzzles:
                kind, question, answer = generate_puzzle()
                p = {"answer": answer.lower().strip(), "type": kind, "display": question, "attempts": 0}
                if kind == "wordle":
                    p["guesses"] = []
                active_puzzles[key] = p
            p = active_puzzles[key]
            limit = WORDLE_MAX_GUESSES if p["type"] == "wordle" else PUZZLE_MAX_ATTEMPTS
            attempts = len(p.get("guesses", [])) if p["type"] == "wordle" else p.get("attempts", 0)
            remaining = limit - attempts
            return await ctx.send(embed=make_embed(
                PUZZLE_TITLES[p["type"]],
                f"{p['display']}\n\nAnswer with `{PREFIX}solve <answer>`\n"
                f"Practice puzzle. No coin reward | Attempts left: **{remaining}**"))

        get_balance(user_id)
    
        now = datetime.now(CENTRAL_TZ)
        today = now.strftime("%Y-%m-%d")
        row = shared.db.execute(
            "SELECT puzzle_date, puzzle_solved, puzzle_attempts FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        puzzle_date = row[0] if row else ""
        puzzle_solved = row[1] if row else 0
        puzzle_attempts = row[2] if row else 0
    
        if puzzle_date == today and puzzle_solved:
            active_puzzles.pop(key, None)
            save_active_puzzle(user_id, None)
            return await ctx.send(embed=make_embed("Already Solved", "You already solved today's puzzle! Come back tomorrow.", COLOR_SUCCESS))
        if puzzle_date == today and puzzle_attempts >= PUZZLE_MAX_ATTEMPTS:
            active_puzzles.pop(key, None)
            save_active_puzzle(user_id, None)
            return await ctx.send(embed=make_embed("Out of Attempts", f"You've used all **{PUZZLE_MAX_ATTEMPTS}** attempts today. Try again tomorrow!", COLOR_ERROR))
    
        if puzzle_date != today:
            puzzle_attempts = 0
            active_puzzles.pop(key, None)
            save_active_puzzle(user_id, None)
            shared.db.execute("UPDATE users SET puzzle_date = ?, puzzle_solved = 0, puzzle_attempts = 0 WHERE user_id = ?", (today, user_id))
            shared.db.commit()
    
        if key not in active_puzzles:
            loaded = load_active_puzzle(user_id)
            if loaded:
                active_puzzles[key] = loaded
    
        if key not in active_puzzles:
            kind, question, answer = generate_puzzle()
            p = {"answer": answer.lower().strip(), "type": kind, "display": question}
            if kind == "wordle":
                p["guesses"] = []
            active_puzzles[key] = p
            save_active_puzzle(user_id, p)
    
        p = active_puzzles[key]
        remaining = (
            WORDLE_MAX_GUESSES - len(p.get("guesses", []))
            if p["type"] == "wordle"
            else PUZZLE_MAX_ATTEMPTS - puzzle_attempts
        )
        reward_line = (
            "No coin reward in kids mode"
            if kids_mode
            else f"Reward: **{PUZZLE_REWARD}** coins"
        )
        await ctx.send(embed=make_embed(
            PUZZLE_TITLES[p["type"]],
            f"{p['display']}\n\nAnswer with `{PREFIX}solve <answer>`\n"
            f"{reward_line} | Attempts left: **{remaining}**"))
    

    @commands.command()
    async def solve(self, ctx, *, answer: str):
        """Submit your answer to the daily puzzle."""
        user_id = ctx.author.id
        kids_mode = ctx.guild is not None and is_kids_mode_guild(ctx.guild.id)
        key = _puzzle_key(user_id, kids_mode)
    
        if not kids_mode:
            get_balance(user_id)
        if not kids_mode and key not in active_puzzles:
            loaded = load_active_puzzle(user_id)
            if loaded:
                active_puzzles[key] = loaded
        if key not in active_puzzles:
            return await ctx.send(f"You don't have an active puzzle. Start one with `{PREFIX}puzzle`.")

        p = active_puzzles[key]

        if kids_mode:
            guess = answer.lower().strip()
            if p["type"] == "wordle":
                if len(guess) != 5 or not guess.isalpha():
                    return await ctx.send("Guess must be a 5-letter word.")
                p["guesses"].append(guess)
                if guess == p["answer"]:
                    active_puzzles.pop(key, None)
                    return await ctx.send(embed=make_embed(
                        f"Wordle Solved in {len(p['guesses'])}!",
                        f"{wordle_display(p)}\n\nSolved. No coin reward in kids mode.",
                        COLOR_SUCCESS))
                if len(p["guesses"]) >= WORDLE_MAX_GUESSES:
                    active_puzzles.pop(key, None)
                    return await ctx.send(embed=make_embed(
                        "Wordle Failed",
                        f"{wordle_display(p)}\n\nThe word was **{p['answer']}**.",
                        COLOR_ERROR))
                return await ctx.send(embed=make_embed(
                    "Wordle",
                    f"{wordle_display(p)}\n\nGuess again with `{PREFIX}solve <word>`"))

            p["attempts"] = p.get("attempts", 0) + 1
            if guess == p["answer"]:
                active_puzzles.pop(key, None)
                return await ctx.send(embed=make_embed(
                    "Correct!",
                    "Solved. No coin reward in kids mode.",
                    COLOR_SUCCESS))
            remaining = PUZZLE_MAX_ATTEMPTS - p["attempts"]
            if remaining <= 0:
                correct = p["answer"]
                active_puzzles.pop(key, None)
                return await ctx.send(embed=make_embed(
                    "Out of Attempts",
                    f"The answer was **{correct}**. Start another practice puzzle with `{PREFIX}puzzle`.",
                    COLOR_ERROR))
            return await ctx.send(embed=make_embed(
                "Wrong",
                f"That's not it. Attempts left: **{remaining}**",
                COLOR_ERROR))
    
        now = datetime.now(CENTRAL_TZ)
        today = now.strftime("%Y-%m-%d")
        row = shared.db.execute(
            "SELECT puzzle_date, puzzle_solved, puzzle_attempts FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        puzzle_date = row[0] if row else ""
        puzzle_solved = row[1] if row else 0
        puzzle_attempts = row[2] if row else 0
    
        if puzzle_date == today and puzzle_solved:
            active_puzzles.pop(key, None)
            save_active_puzzle(user_id, None)
            return await ctx.send(embed=make_embed("Already Solved", "You already solved today's puzzle!", COLOR_SUCCESS))
        if puzzle_date == today and puzzle_attempts >= PUZZLE_MAX_ATTEMPTS:
            active_puzzles.pop(key, None)
            save_active_puzzle(user_id, None)
            return await ctx.send(embed=make_embed("Out of Attempts", "No attempts left today!", COLOR_ERROR))
    
        guess = answer.lower().strip()
    
        if p["type"] == "wordle":
            if len(guess) != 5 or not guess.isalpha():
                return await ctx.send("Guess must be a 5-letter word.")
            p["guesses"].append(guess)
            save_active_puzzle(user_id, p)
            if guess == p["answer"]:
                active_puzzles.pop(key, None)
                save_active_puzzle(user_id, None)
                shared.db.execute("UPDATE users SET puzzle_solved = 1 WHERE user_id = ?", (user_id,))
                shared.db.commit()
                reward_text = "Solved. No coin reward in kids mode."
                if not kids_mode:
                    update_balance(user_id, PUZZLE_REWARD)
                    bal = get_balance(user_id)
                    reward_text = f"You earned **{PUZZLE_REWARD}** coins!\nBalance: **{bal}**"
                return await ctx.send(embed=make_embed(
                    f"Wordle Solved in {len(p['guesses'])}!",
                    f"{wordle_display(p)}\n\n{reward_text}",
                    COLOR_SUCCESS))
            if len(p["guesses"]) >= WORDLE_MAX_GUESSES:
                active_puzzles.pop(key, None)
                save_active_puzzle(user_id, None)
                return await ctx.send(embed=make_embed(
                    "Wordle Failed",
                    f"{wordle_display(p)}\n\nThe word was **{p['answer']}**.",
                    COLOR_ERROR))
            return await ctx.send(embed=make_embed(
                "Wordle",
                f"{wordle_display(p)}\n\nGuess again with `{PREFIX}solve <word>`"))
    
        puzzle_attempts += 1
        shared.db.execute("UPDATE users SET puzzle_date = ?, puzzle_attempts = ? WHERE user_id = ?", (today, puzzle_attempts, user_id))
        shared.db.commit()
    
        if guess == p["answer"]:
            active_puzzles.pop(key, None)
            save_active_puzzle(user_id, None)
            shared.db.execute("UPDATE users SET puzzle_solved = 1 WHERE user_id = ?", (user_id,))
            shared.db.commit()
            reward_text = "Solved. No coin reward in kids mode."
            if not kids_mode:
                update_balance(user_id, PUZZLE_REWARD)
                bal = get_balance(user_id)
                reward_text = f"You earned **{PUZZLE_REWARD}** coins!\nBalance: **{bal}**"
            await ctx.send(embed=make_embed(
                "Correct!",
                reward_text,
                COLOR_SUCCESS))
        else:
            remaining = PUZZLE_MAX_ATTEMPTS - puzzle_attempts
            if remaining <= 0:
                correct = p["answer"]
                active_puzzles.pop(key, None)
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
        active_puzzles.pop(_puzzle_key(target.id, False), None)
        active_puzzles.pop(_puzzle_key(target.id, True), None)
        save_active_puzzle(target.id, None)
        shared.db.execute("UPDATE users SET puzzle_solved = 0, puzzle_attempts = 0 WHERE user_id = ?", (target.id,))
        shared.db.commit()
        await ctx.send(f"Puzzle reset for {target.display_name}.")
    

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

    @commands.command(aliases=["slot"])
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

    def _current_blackjack_hand(self, game):
        idx = game["current_hand"]
        if idx >= len(game["hands"]):
            return None
        return game["hands"][idx]

    def _format_hand_line(self, idx, hand, active=False):
        cards = display_hand(hand["cards"])
        total = hand_value(hand["cards"])
        marker = "-> " if active else ""
        label = f"{marker}Hand {idx + 1}"
        flags = []
        if hand["doubled"]:
            flags.append("doubled")
        if hand["surrendered"]:
            flags.append("surrendered")
        if hand["bust"]:
            flags.append("bust")
        if hand["stood"] and not hand["bust"] and not hand["surrendered"]:
            flags.append("stand")
        if BLACKJACK_RULES["five_card_charlie"] and len(hand["cards"]) >= 5 and total <= 21:
            flags.append("charlie")
        suffix = f" ({', '.join(flags)})" if flags else ""
        return f"{label}: {cards} -> **{total}** | Bet: **{hand['bet']}**{suffix}"

    def _build_blackjack_embed(self, game, reveal_dealer=False, footer_note=None):
        dealer_cards = game["dealer"] if reveal_dealer else game["dealer"][:1]
        dealer_suffix = f" -> **{hand_value(game['dealer'])}**" if reveal_dealer else " `??`"
        lines = [f"Dealer: {display_hand(dealer_cards)}{dealer_suffix}", ""]
        for idx, hand in enumerate(game["hands"]):
            lines.append(self._format_hand_line(idx, hand, active=(idx == game["current_hand"] and not reveal_dealer)))

        if not reveal_dealer:
            current = self._current_blackjack_hand(game)
            if current:
                dealer_up = game["dealer"][0]
                actions = ", ".join(f"`{PREFIX}{a}`" for a in available_actions(game, current))
                lines.extend(["", f"Actions: {actions}"])
                if shared.runtime_settings.get("bj_basic_hint_enabled", True):
                    rec_action, rec_reason = best_legal_blackjack_recommendation(game, current, dealer_up)
                    lines.append(f"Basic strategy: **{rec_action}** ({rec_reason})")

        if footer_note:
            lines.extend(["", footer_note])

        return make_embed("Blackjack", "\n".join(lines))

    async def _finish_blackjack_round(self, ctx, game):
        live_hands = [
            hand for hand in game["hands"]
            if not hand["bust"] and not hand["surrendered"]
        ]
        if live_hands:
            while dealer_should_hit(game["dealer"]):
                game["dealer"].append(draw_from_shoe())

        dealer_value = hand_value(game["dealer"])
        dealer_bust = dealer_value > 21
        total_delta = 0
        settlement = []

        for idx, hand in enumerate(game["hands"]):
            hv = hand_value(hand["cards"])
            bet = hand["bet"]

            if hand["surrendered"]:
                loss = bet // 2
                total_delta -= loss
                settlement.append(f"Hand {idx + 1}: surrendered (-{loss})")
                continue

            if hand["bust"]:
                total_delta -= bet
                settlement.append(f"Hand {idx + 1}: bust (-{bet})")
                continue

            if BLACKJACK_RULES["five_card_charlie"] and len(hand["cards"]) >= 5 and hv <= 21:
                total_delta += bet
                settlement.append(f"Hand {idx + 1}: five-card charlie (+{bet})")
                continue

            if dealer_bust or hv > dealer_value:
                total_delta += bet
                settlement.append(f"Hand {idx + 1}: win (+{bet})")
            elif hv < dealer_value:
                total_delta -= bet
                settlement.append(f"Hand {idx + 1}: lose (-{bet})")
            else:
                settlement.append(f"Hand {idx + 1}: push (+0)")

        if total_delta:
            update_balance(ctx.author.id, total_delta)
        new_bal = get_balance(ctx.author.id)

        if total_delta > 0:
            result = f"Net result: **+{total_delta}**"
            color = COLOR_SUCCESS
        elif total_delta < 0:
            result = f"Net result: **{total_delta}**"
            color = COLOR_ERROR
        else:
            result = "Net result: **0**"
            color = COLOR_WARNING

        footer = f"{result}\nBalance: **{new_bal}**\n" + "\n".join(settlement)
        embed = self._build_blackjack_embed(game, reveal_dealer=True, footer_note=footer)
        embed.color = color
        await ctx.send(embed=embed)

    async def _advance_or_finish_blackjack(self, ctx, game):
        if advance_to_next_hand(game):
            del active_blackjack[ctx.author.id]
            await self._finish_blackjack_round(ctx, game)
            return
        await ctx.send(embed=self._build_blackjack_embed(game))

    def _blackjack_rules_summary(self):
        current = get_blackjack_ruleset_name()
        rules = BLACKJACK_RULES
        payout_display = "3:2" if abs(rules["blackjack_payout"] - 1.5) < 1e-9 else f"{rules['blackjack_payout']}:1"
        return (
            "Blackjack ruleset: "
            f"**{current}**\n"
            f"Decks: **{rules['decks']}** | BJ payout: **{payout_display}** | "
            f"S17: **{'yes' if not rules['dealer_hits_soft_17'] else 'no'}** | "
            f"Late surrender: **{'yes' if rules['late_surrender'] else 'no'}** | "
            f"Five-card Charlie: **{'yes' if rules['five_card_charlie'] else 'no'}**"
        )

    @commands.command(aliases=["bjtable"])
    async def bjrules(self, ctx):
        """Show the active blackjack table rules."""
        await ctx.send(self._blackjack_rules_summary())

    @commands.command()
    async def bjhint(self, ctx, mode: str = "status"):
        """Admin only: toggle blackjack basic-strategy hints (`on`, `off`, `status`)."""
        if ctx.author.id != ADMIN_ID:
            return

        action = mode.strip().lower()
        if action == "status":
            enabled = shared.runtime_settings.get("bj_basic_hint_enabled", True)
            return await ctx.send(f"Blackjack strategy hints are **{'ON' if enabled else 'OFF'}**.")

        if action not in {"on", "off"}:
            return await ctx.send(f"Usage: `{PREFIX}bjhint <on|off|status>`")

        enabled = action == "on"
        shared.runtime_settings["bj_basic_hint_enabled"] = enabled
        shared._save_json_setting("bj_basic_hint_enabled", enabled)
        await ctx.send(f"Blackjack strategy hints are now **{'ON' if enabled else 'OFF'}**.")

    @commands.command()
    async def bjruleset(self, ctx, mode: str = "status"):
        """Admin only: set blackjack ruleset (`realistic` or `arcade`) or show status."""
        if ctx.author.id != ADMIN_ID:
            return

        action = mode.strip().lower()
        if action == "status":
            return await ctx.send(self._blackjack_rules_summary())

        if action not in BLACKJACK_RULE_PRESETS:
            return await ctx.send(f"Usage: `{PREFIX}bjruleset <realistic|arcade|status>`")

        if active_blackjack:
            return await ctx.send("Cannot switch blackjack rules while a hand is active. Finish current hands first.")

        selected = apply_blackjack_ruleset(action)
        shared.runtime_settings["bj_ruleset"] = selected
        shared._save_json_setting("bj_ruleset", selected)
        blackjack_shoe.clear()
        await ctx.send(f"Blackjack ruleset set to **{selected}**.")

    @commands.command(aliases=["bj", "21"])
    async def blackjack(self, ctx, amount: int):
        """Play a rules-based blackjack hand with split/double/surrender."""
        if ctx.author.id in active_blackjack:
            return await ctx.send(
                f"You already have a hand going! Use `{PREFIX}hit`, `{PREFIX}stand`, "
                f"`{PREFIX}double`, `{PREFIX}split`, or `{PREFIX}surrender`."
            )
        if await check_bet(ctx, amount):
            return

        player = [draw_from_shoe(), draw_from_shoe()]
        dealer = [draw_from_shoe(), draw_from_shoe()]
        game = {
            "dealer": dealer,
            "hands": [make_player_hand(player, amount)],
            "current_hand": 0,
        }

        player_bj = is_blackjack(player)
        dealer_bj = is_blackjack(dealer)
        if player_bj or dealer_bj:
            total_delta = 0
            if player_bj and dealer_bj:
                note = "Both sides have blackjack. Push."
                color = COLOR_WARNING
            elif player_bj:
                win = int(amount * BLACKJACK_RULES["blackjack_payout"])
                total_delta = win
                note = f"Blackjack pays {BLACKJACK_RULES['blackjack_payout']}:1. You win **+{win}**."
                color = COLOR_SUCCESS
            else:
                total_delta = -amount
                note = f"Dealer has blackjack. You lose **-{amount}**."
                color = COLOR_ERROR

            if total_delta:
                update_balance(ctx.author.id, total_delta)
            new_bal = get_balance(ctx.author.id)
            await ctx.send(embed=make_embed(
                "Blackjack",
                f"Dealer: {display_hand(dealer)} -> **{hand_value(dealer)}**\n"
                f"Hand 1: {display_hand(player)} -> **{hand_value(player)}** | Bet: **{amount}**\n\n"
                f"{note}\nBalance: **{new_bal}**",
                color,
            ))
            return

        active_blackjack[ctx.author.id] = game
        await ctx.send(embed=self._build_blackjack_embed(game))

    @commands.command()
    async def hit(self, ctx):
        """Draw another card in blackjack."""
        game = active_blackjack.get(ctx.author.id)
        if not game:
            return await ctx.send(
                f"You don't have an active blackjack hand. Start one with `{PREFIX}blackjack <amount>`."
            )

        hand = self._current_blackjack_hand(game)
        if hand is None:
            del active_blackjack[ctx.author.id]
            return await ctx.send("Blackjack state reset. Start a new hand.")

        if "hit" not in available_actions(game, hand):
            return await ctx.send("You cannot hit this hand right now.")

        hand["cards"].append(draw_from_shoe())
        hand["action_count"] += 1
        hv = hand_value(hand["cards"])
        if hv > 21:
            hand["bust"] = True
        elif hv == 21:
            hand["stood"] = True

        await self._advance_or_finish_blackjack(ctx, game)

    @commands.command()
    async def stand(self, ctx):
        """Stand with your current hand in blackjack."""
        game = active_blackjack.get(ctx.author.id)
        if not game:
            return await ctx.send("You don't have an active blackjack hand.")

        hand = self._current_blackjack_hand(game)
        if hand is None:
            del active_blackjack[ctx.author.id]
            return await ctx.send("Blackjack state reset. Start a new hand.")

        hand["stood"] = True
        hand["action_count"] += 1
        await self._advance_or_finish_blackjack(ctx, game)

    @commands.command()
    async def double(self, ctx):
        """Double down: double your bet, draw once, then stand."""
        game = active_blackjack.get(ctx.author.id)
        if not game:
            return await ctx.send("You don't have an active blackjack hand.")

        hand = self._current_blackjack_hand(game)
        if hand is None:
            del active_blackjack[ctx.author.id]
            return await ctx.send("Blackjack state reset. Start a new hand.")

        if "double" not in available_actions(game, hand):
            return await ctx.send("You cannot double this hand right now.")

        extra = hand["bet"]
        if get_balance(ctx.author.id) < extra:
            return await ctx.send(f"You need at least **{extra}** coins available to double.")

        hand["bet"] += extra
        hand["doubled"] = True
        hand["cards"].append(draw_from_shoe())
        hand["action_count"] += 1
        if hand_value(hand["cards"]) > 21:
            hand["bust"] = True
        else:
            hand["stood"] = True

        await self._advance_or_finish_blackjack(ctx, game)

    @commands.command()
    async def split(self, ctx):
        """Split a pair into two hands."""
        game = active_blackjack.get(ctx.author.id)
        if not game:
            return await ctx.send("You don't have an active blackjack hand.")

        hand = self._current_blackjack_hand(game)
        if hand is None:
            del active_blackjack[ctx.author.id]
            return await ctx.send("Blackjack state reset. Start a new hand.")

        if "split" not in available_actions(game, hand):
            return await ctx.send("You cannot split this hand right now.")

        extra = hand["bet"]
        if get_balance(ctx.author.id) < extra:
            return await ctx.send(f"You need at least **{extra}** coins available to split.")

        c1, c2 = hand["cards"]
        pair_key = split_rank_key(c1)
        split_aces = pair_key == "A"
        hand_a = make_player_hand([c1, draw_from_shoe()], hand["bet"], from_split=True, split_aces=split_aces)
        hand_b = make_player_hand([c2, draw_from_shoe()], hand["bet"], from_split=True, split_aces=split_aces)

        idx = game["current_hand"]
        game["hands"][idx] = hand_a
        game["hands"].insert(idx + 1, hand_b)

        if split_aces and not BLACKJACK_RULES["draw_to_split_aces"]:
            hand_a["stood"] = True
            hand_b["stood"] = True

        if hand_value(hand_a["cards"]) == 21:
            hand_a["stood"] = True

        await self._advance_or_finish_blackjack(ctx, game)

    @commands.command()
    async def surrender(self, ctx):
        """Late surrender: forfeit half your current hand's bet."""
        game = active_blackjack.get(ctx.author.id)
        if not game:
            return await ctx.send("You don't have an active blackjack hand.")

        hand = self._current_blackjack_hand(game)
        if hand is None:
            del active_blackjack[ctx.author.id]
            return await ctx.send("Blackjack state reset. Start a new hand.")

        if "surrender" not in available_actions(game, hand):
            return await ctx.send("You cannot surrender this hand right now.")

        hand["surrendered"] = True
        hand["action_count"] += 1
        await self._advance_or_finish_blackjack(ctx, game)

async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
