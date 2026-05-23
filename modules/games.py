import discord
import asyncio
import random
from collections import Counter
from datetime import datetime, timezone

from discord.ext import commands, tasks

from shared import (
    PREFIX,
    COLOR_DEFAULT,
    COLOR_SUCCESS,
    COLOR_ERROR,
    COLOR_WARNING,
    make_embed,
    log_game_win,
    log_game_draw,
)

active_ttt = {}  # channel_id -> game state
active_c4 = {}  # channel_id -> game state
pending_games = {}  # message_id -> {"type": "ttt"/"c4", "host": Member, "channel_id": int, "created_at": datetime}
active_math_quiz = {}  # user_id -> {"answer": int, "question": str}
active_memory_games = {}  # user_id -> {"answer": str}
active_trivia = {}  # user_id -> {"answer": str, "explanation": str}
active_scrambles = {}  # user_id -> {"answer": str}
active_timers = {}  # user_id -> seconds
PENDING_GAME_TIMEOUT = 300  # seconds before a pending invite is cleaned up

TTT_EMPTY = "⬛"
TTT_X = "❌"
TTT_O = "⭕"


def ttt_render(board):
    rows = []
    for r in range(3):
        cells = []
        for c in range(3):
            idx = r * 3 + c
            if board[idx] == "X":
                cells.append(TTT_X)
            elif board[idx] == "O":
                cells.append(TTT_O)
            else:
                cells.append(f"{idx + 1}\N{COMBINING ENCLOSING KEYCAP}")
        rows.append("".join(cells))
    return "\n".join(rows)


def ttt_check_winner(board):
    lines = [
        (0, 1, 2),
        (3, 4, 5),
        (6, 7, 8),  # rows
        (0, 3, 6),
        (1, 4, 7),
        (2, 5, 8),  # cols
        (0, 4, 8),
        (2, 4, 6),  # diags
    ]
    for a, b, c in lines:
        if board[a] == board[b] == board[c] and board[a] in ("X", "O"):
            return board[a]
    if all(cell in ("X", "O") for cell in board):
        return "draw"
    return None


def start_ttt(host, opponent):
    game = {
        "board": list(range(9)),
        "players": {"X": host, "O": opponent},
        "turn": "X",
    }
    return game


C4_ROWS = 6
C4_COLS = 7
C4_EMPTY = "⚫"
C4_RED = "🔴"
C4_YELLOW = "🟡"


def c4_render(board):
    header = "".join(f"{i + 1}\N{COMBINING ENCLOSING KEYCAP}" for i in range(C4_COLS))
    rows = []
    for r in range(C4_ROWS):
        cells = []
        for c in range(C4_COLS):
            val = board[r][c]
            if val == "R":
                cells.append(C4_RED)
            elif val == "Y":
                cells.append(C4_YELLOW)
            else:
                cells.append(C4_EMPTY)
        rows.append("".join(cells))
    return header + "\n" + "\n".join(rows)


def c4_drop(board, col, piece):
    for r in range(C4_ROWS - 1, -1, -1):
        if board[r][col] is None:
            board[r][col] = piece
            return r
    return -1


def c4_check_winner(board):
    for r in range(C4_ROWS):
        for c in range(C4_COLS):
            piece = board[r][c]
            if piece is None:
                continue
            # horizontal
            if c + 3 < C4_COLS and all(board[r][c + i] == piece for i in range(4)):
                return piece
            # vertical
            if r + 3 < C4_ROWS and all(board[r + i][c] == piece for i in range(4)):
                return piece
            # diagonal down-right
            if r + 3 < C4_ROWS and c + 3 < C4_COLS and all(board[r + i][c + i] == piece for i in range(4)):
                return piece
            # diagonal down-left
            if r + 3 < C4_ROWS and c - 3 >= 0 and all(board[r + i][c - i] == piece for i in range(4)):
                return piece
    if all(board[0][c] is not None for c in range(C4_COLS)):
        return "draw"
    return None


def start_c4(host, opponent):
    board = [[None] * C4_COLS for _ in range(C4_ROWS)]
    game = {
        "board": board,
        "players": {"R": host, "Y": opponent},
        "turn": "R",
    }
    return game


active_hangman = {}  # channel_id -> game state
hangman_msg = {}  # channel_id -> Message to edit in place

HANGMAN_WORDS = [
    "python",
    "discord",
    "hangman",
    "keyboard",
    "monitor",
    "algorithm",
    "function",
    "variable",
    "database",
    "network",
    "browser",
    "terminal",
    "computer",
    "program",
    "internet",
    "software",
    "hardware",
    "graphics",
    "security",
    "download",
    "elephant",
    "giraffe",
    "penguin",
    "dolphin",
    "octopus",
    "butterfly",
    "squirrel",
    "mushroom",
    "sandwich",
    "umbrella",
    "airplane",
    "mountain",
    "treasure",
    "volcano",
    "dinosaur",
    "astronaut",
    "chocolate",
    "pineapple",
    "strawberry",
    "watermelon",
    "adventure",
    "birthday",
    "carnival",
    "dominoes",
    "firework",
    "galaxies",
    "harmonica",
    "illusion",
    "jukebox",
    "kangaroo",
    "labyrinth",
    "macaroni",
]

HANGMAN_STAGES = [
    "```\n  +---+\n      |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n  |   |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|   |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|\\  |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|\\  |\n /    |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|\\  |\n / \\  |\n      |\n=========```",
]

LETTER_PRIORITY = "etaoinshrdlucmfwypvbgkjqxz"

MEMORY_SYMBOLS = ["cat", "dog", "sun", "moon", "star", "tree", "fish", "book", "ball", "kite"]

SCRAMBLE_WORDS = [
    "apple",
    "banana",
    "basket",
    "butter",
    "castle",
    "circle",
    "dragon",
    "flower",
    "forest",
    "garden",
    "guitar",
    "island",
    "jacket",
    "kitten",
    "ladder",
    "magnet",
    "monkey",
    "orange",
    "pencil",
    "planet",
    "pocket",
    "rabbit",
    "rocket",
    "school",
    "silver",
    "soccer",
    "summer",
    "turtle",
    "window",
    "yellow",
]

TRIVIA_QUESTIONS = [
    {
        "question": "Which planet is known as the Red Planet?",
        "choices": ["Mars", "Venus", "Jupiter", "Neptune"],
        "answer": "A",
        "explanation": "Mars is called the Red Planet because of iron oxide on its surface.",
    },
    {
        "question": "How many sides does a triangle have?",
        "choices": ["2", "3", "4", "5"],
        "answer": "B",
        "explanation": "A triangle has 3 sides.",
    },
    {
        "question": "What do bees make?",
        "choices": ["Milk", "Honey", "Bread", "Cheese"],
        "answer": "B",
        "explanation": "Bees make honey.",
    },
    {
        "question": "Which animal is the largest land animal?",
        "choices": ["Giraffe", "Elephant", "Horse", "Bear"],
        "answer": "B",
        "explanation": "Elephants are the largest land animals.",
    },
    {
        "question": "What is H2O commonly called?",
        "choices": ["Salt", "Water", "Oxygen", "Sugar"],
        "answer": "B",
        "explanation": "H2O is water.",
    },
    {
        "question": "Which direction does the sun rise from?",
        "choices": ["North", "South", "East", "West"],
        "answer": "C",
        "explanation": "The sun rises in the east.",
    },
    {
        "question": "How many continents are there?",
        "choices": ["5", "6", "7", "8"],
        "answer": "C",
        "explanation": "There are 7 continents.",
    },
    {
        "question": "What is the first letter of the alphabet?",
        "choices": ["A", "B", "C", "D"],
        "answer": "A",
        "explanation": "A is the first letter of the alphabet.",
    },
]


def hangman_render(game):
    word_display = " ".join(letter if letter in game["guessed"] else "\\_" for letter in game["word"])
    wrong = sorted(game["wrong"])
    wrong_str = ", ".join(wrong) if wrong else "None"
    return (
        f"{HANGMAN_STAGES[len(game['wrong'])]}\n"
        f"**{word_display}**\n"
        f"Wrong guesses: {wrong_str}\n"
        f"Guesses left: **{6 - len(game['wrong'])}**"
    )


def hangman_candidates(game):
    target_len = len(game["word"])
    revealed = game["guessed"]
    wrong_letters = {w for w in game["wrong"] if len(w) == 1 and w.isalpha()}

    candidates = []
    for word in HANGMAN_WORDS:
        if len(word) != target_len:
            continue
        if any(ch in word for ch in wrong_letters):
            continue

        ok = True
        for idx, actual in enumerate(game["word"]):
            should_show = actual in revealed
            if should_show and word[idx] != actual:
                ok = False
                break
            if not should_show and word[idx] in revealed:
                ok = False
                break
        if ok:
            candidates.append(word)
    return candidates


def best_hangman_letter(game):
    candidates = hangman_candidates(game)
    tried = set(game["guessed"]) | {w for w in game["wrong"] if len(w) == 1 and w.isalpha()}

    # Fallback to fixed frequency order if candidate set is empty.
    if not candidates:
        for ch in LETTER_PRIORITY:
            if ch not in tried:
                return ch, 0
        return None, 0

    counts = Counter()
    for word in candidates:
        for ch in set(word):
            if ch not in tried:
                counts[ch] += 1

    if not counts:
        return None, len(candidates)

    best = sorted(
        counts.items(),
        key=lambda item: (-item[1], LETTER_PRIORITY.index(item[0]) if item[0] in LETTER_PRIORITY else 99),
    )[0][0]
    return best, len(candidates)


class GamesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener("on_ready")
    async def _start_games_tasks(self):
        if not self.cleanup_pending_games.is_running():
            self.cleanup_pending_games.start()

    @tasks.loop(minutes=5)
    async def cleanup_pending_games(self):
        """Remove stale game invites that nobody accepted."""
        now = datetime.now(timezone.utc)
        stale = [
            mid for mid, info in pending_games.items() if (now - info.get("created_at", now)).total_seconds() > PENDING_GAME_TIMEOUT
        ]
        for mid in stale:
            del pending_games[mid]

    @commands.Cog.listener("on_raw_reaction_add")
    async def on_raw_reaction_add(self, payload):
        if payload.message_id not in pending_games:
            return
        if payload.user_id == self.bot.user.id:
            return
        if str(payload.emoji) != "✅":
            return
        pending = pending_games.pop(payload.message_id)
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        joiner = guild.get_member(payload.user_id)
        if not joiner or joiner.bot or joiner == pending["host"]:
            pending_games[payload.message_id] = pending  # put it back
            return
        channel = self.bot.get_channel(pending["channel_id"])
        if not channel:
            return
        if pending["type"] == "ttt":
            if channel.id in active_ttt:
                return
            game = start_ttt(pending["host"], joiner)
            active_ttt[channel.id] = game
            await channel.send(
                embed=make_embed(
                    "Tic-Tac-Toe",
                    f"{pending['host'].mention} ({TTT_X}) vs {joiner.mention} ({TTT_O})\n\n"
                    f"{ttt_render(game['board'])}\n\n"
                    f"{pending['host'].mention}'s turn — use `{PREFIX}m <1-9>`",
                )
            )
        elif pending["type"] == "c4":
            if channel.id in active_c4:
                return
            game = start_c4(pending["host"], joiner)
            active_c4[channel.id] = game
            await channel.send(
                embed=make_embed(
                    "Connect 4",
                    f"{pending['host'].mention} ({C4_RED}) vs {joiner.mention} ({C4_YELLOW})\n\n"
                    f"{c4_render(game['board'])}\n\n"
                    f"{pending['host'].mention}'s turn — use `{PREFIX}drop <1-7>`",
                )
            )

    # QUICK GAMES
    # ---------------------------------------------------------------------------

    @commands.command()
    async def rps(self, ctx, choice: str):
        """Play rock-paper-scissors against Gary."""
        aliases = {
            "r": "rock",
            "rock": "rock",
            "p": "paper",
            "paper": "paper",
            "s": "scissors",
            "scissor": "scissors",
            "scissors": "scissors",
        }
        player = aliases.get(choice.strip().lower())
        if player is None:
            return await ctx.send(f"Usage: `{PREFIX}rps <rock|paper|scissors>`")

        gary = random.choice(["rock", "paper", "scissors"])
        wins = {
            "rock": "scissors",
            "paper": "rock",
            "scissors": "paper",
        }
        if player == gary:
            result = "Tie."
            color = COLOR_WARNING
        elif wins[player] == gary:
            result = "You win."
            color = COLOR_SUCCESS
        else:
            result = "Gary wins."
            color = COLOR_ERROR

        await ctx.send(
            embed=make_embed(
                "Rock Paper Scissors",
                f"You picked **{player}**.\nGary picked **{gary}**.\n\n{result}",
                color,
            )
        )

    @commands.command()
    async def roll(self, ctx, sides: int = 6):
        """Roll a die. Defaults to 6 sides."""
        if sides < 2 or sides > 1000:
            return await ctx.send("Pick between 2 and 1000 sides.")
        await ctx.send(f"{ctx.author.mention} rolled **{random.randint(1, sides)}** on a d{sides}.")

    def _new_math_question(self):
        kind = random.choice(["add", "subtract", "multiply"])
        if kind == "add":
            a = random.randint(5, 50)
            b = random.randint(5, 50)
            return f"{a} + {b}", a + b
        if kind == "subtract":
            a = random.randint(20, 99)
            b = random.randint(1, a)
            return f"{a} - {b}", a - b
        a = random.randint(2, 12)
        b = random.randint(2, 12)
        return f"{a} x {b}", a * b

    @commands.command(aliases=["mathquiz"])
    async def mathgame(self, ctx):
        """Start a quick arithmetic question."""
        question, answer = self._new_math_question()
        active_math_quiz[ctx.author.id] = {"question": question, "answer": answer}
        await ctx.send(
            embed=make_embed(
                "Math Game",
                f"{ctx.author.mention}, what is **{question}**?\nUse `{PREFIX}mathanswer <answer>`.",
                COLOR_DEFAULT,
            )
        )

    @commands.command(aliases=["mathans"])
    async def mathanswer(self, ctx, answer: int):
        """Answer your current math game question."""
        quiz = active_math_quiz.get(ctx.author.id)
        if not quiz:
            return await ctx.send(f"No math question active. Start one with `{PREFIX}mathgame`.")
        if answer == quiz["answer"]:
            del active_math_quiz[ctx.author.id]
            return await ctx.send(
                embed=make_embed(
                    "Correct",
                    f"Nice. **{quiz['question']} = {quiz['answer']}**",
                    COLOR_SUCCESS,
                )
            )
        await ctx.send(
            embed=make_embed(
                "Try Again",
                f"Not quite. Try another answer or start over with `{PREFIX}mathgame`.",
                COLOR_WARNING,
            )
        )

    @commands.command()
    async def memory(self, ctx, level: int = 1):
        """Start a channel-wide memory challenge — first to repeat the sequence wins."""
        if ctx.channel.id in active_memory_games:
            return await ctx.send("There's already a memory game in this channel.")
        level = max(1, min(5, level))
        length = level + 2
        sequence = [random.choice(MEMORY_SYMBOLS) for _ in range(length)]
        answer = " ".join(sequence)
        active_memory_games[ctx.channel.id] = {"answer": answer}
        msg = await ctx.send(
            embed=make_embed(
                "Memory Game",
                f"Memorize this sequence:\n\n**{answer}**\n\nHiding in 8 seconds — first to `{PREFIX}memoryanswer <sequence>` wins!",
                COLOR_DEFAULT,
            )
        )
        await asyncio.sleep(8)
        if ctx.channel.id not in active_memory_games:
            return
        try:
            await msg.edit(
                embed=make_embed(
                    "Memory Game",
                    f"Sequence hidden! Use `{PREFIX}memoryanswer <sequence>` to answer.",
                    COLOR_DEFAULT,
                )
            )
        except discord.HTTPException:
            pass

    @commands.command(aliases=["memanswer", "memans"])
    async def memoryanswer(self, ctx, *, answer: str):
        """Answer the active memory challenge."""
        game = active_memory_games.get(ctx.channel.id)
        if not game:
            return await ctx.send(f"No memory game active. Start one with `{PREFIX}memory`.")
        normalized = " ".join(answer.lower().strip().split())
        if normalized == game["answer"]:
            del active_memory_games[ctx.channel.id]
            return await ctx.send(
                embed=make_embed(
                    "Correct!",
                    f"{ctx.author.mention} got it!",
                    COLOR_SUCCESS,
                )
            )
        await ctx.send(
            embed=make_embed(
                "Not Quite",
                f"{ctx.author.mention} got it wrong — others can still try!",
                COLOR_WARNING,
            )
        )

    @commands.command()
    async def trivia(self, ctx):
        """Start a channel-wide trivia question — first correct answer wins."""
        if ctx.channel.id in active_trivia:
            return await ctx.send("There's already a trivia question active in this channel.")
        item = random.choice(TRIVIA_QUESTIONS)
        letters = ["A", "B", "C", "D"]
        lines = [f"**{letter}.** {choice}" for letter, choice in zip(letters, item["choices"])]
        active_trivia[ctx.channel.id] = {
            "answer": item["answer"],
            "explanation": item["explanation"],
            "wrong_users": set(),
        }
        await ctx.send(
            embed=make_embed(
                "Trivia",
                item["question"] + "\n\n" + "\n".join(lines) + f"\n\nFirst to `{PREFIX}triviaanswer <A|B|C|D>` wins!",
                COLOR_DEFAULT,
            )
        )

    @commands.command(aliases=["ta"])
    async def triviaanswer(self, ctx, answer: str):
        """Answer the active trivia question."""
        game = active_trivia.get(ctx.channel.id)
        if not game:
            return await ctx.send(f"No trivia question active. Start one with `{PREFIX}trivia`.")
        if ctx.author.id in game["wrong_users"]:
            return await ctx.send(f"{ctx.author.mention} you already used your guess.", delete_after=5)
        choice = answer.strip().upper()
        if choice not in {"A", "B", "C", "D"}:
            return await ctx.send(f"Usage: `{PREFIX}triviaanswer <A|B|C|D>`")
        if choice == game["answer"]:
            del active_trivia[ctx.channel.id]
            return await ctx.send(
                embed=make_embed(
                    "Correct!",
                    f"{ctx.author.mention} got it!\n{game['explanation']}",
                    COLOR_SUCCESS,
                )
            )
        game["wrong_users"].add(ctx.author.id)
        await ctx.send(
            embed=make_embed(
                "Not Quite",
                f"{ctx.author.mention} got it wrong — others can still try!",
                COLOR_WARNING,
            )
        )

    @commands.command()
    async def scramble(self, ctx):
        """Start a channel-wide word scramble — first to unscramble wins."""
        if ctx.channel.id in active_scrambles:
            return await ctx.send("There's already a scramble active in this channel.")
        word = random.choice(SCRAMBLE_WORDS)
        letters = list(word)
        while True:
            random.shuffle(letters)
            scrambled = "".join(letters)
            if scrambled != word:
                break
        active_scrambles[ctx.channel.id] = {"answer": word}
        await ctx.send(
            embed=make_embed(
                "Word Scramble",
                f"Unscramble this word: **{scrambled}**\nFirst to `{PREFIX}unscramble <word>` wins!",
                COLOR_DEFAULT,
            )
        )

    @commands.command()
    async def unscramble(self, ctx, *, answer: str):
        """Answer the active word scramble."""
        game = active_scrambles.get(ctx.channel.id)
        if not game:
            return await ctx.send(f"No scramble active. Start one with `{PREFIX}scramble`.")
        guess = answer.lower().strip()
        if guess == game["answer"]:
            del active_scrambles[ctx.channel.id]
            return await ctx.send(
                embed=make_embed(
                    "Correct!",
                    f"{ctx.author.mention} got it! The word was **{game['answer']}**.",
                    COLOR_SUCCESS,
                )
            )
        await ctx.send(
            embed=make_embed(
                "Try Again",
                f"Not quite, {ctx.author.mention} — others can still try!",
                COLOR_WARNING,
            )
        )

    @commands.command(aliases=["time"])
    async def timer(self, ctx, seconds: int):
        """Start a simple timer, capped at one hour."""
        if seconds < 1 or seconds > 3600:
            return await ctx.send("Timer must be between 1 and 3600 seconds.")
        if ctx.author.id in active_timers:
            return await ctx.send("You already have a timer running.")
        active_timers[ctx.author.id] = seconds
        try:
            await ctx.send(f"Timer started for **{seconds}** seconds.")
            await asyncio.sleep(seconds)
            await ctx.send(f"{ctx.author.mention} timer done.")
        finally:
            active_timers.pop(ctx.author.id, None)

    # GAMES: TIC-TAC-TOE
    # ---------------------------------------------------------------------------

    @commands.command()
    async def ttt(self, ctx, opponent: discord.Member = None):
        """Start a tic-tac-toe game. Tag someone or let others react to join."""
        if ctx.channel.id in active_ttt:
            return await ctx.send("There's already a game in this channel. Finish it first.")
        if opponent:
            if opponent.bot or opponent == ctx.author:
                return await ctx.send("You can't play against yourself or a bot.")
            game = start_ttt(ctx.author, opponent)
            active_ttt[ctx.channel.id] = game
            await ctx.send(
                embed=make_embed(
                    "Tic-Tac-Toe",
                    f"{ctx.author.mention} ({TTT_X}) vs {opponent.mention} ({TTT_O})\n\n"
                    f"{ttt_render(game['board'])}\n\n"
                    f"{ctx.author.mention}'s turn — use `{PREFIX}m <1-9>`",
                )
            )
        else:
            msg = await ctx.send(embed=make_embed("Tic-Tac-Toe", f"{ctx.author.mention} wants to play! React with ✅ to join."))
            await msg.add_reaction("✅")
            pending_games[msg.id] = {
                "type": "ttt",
                "host": ctx.author,
                "channel_id": ctx.channel.id,
                "created_at": datetime.now(timezone.utc),
            }

    @commands.command()
    async def m(self, ctx, pos: int):
        """Make a move in tic-tac-toe (1-9) or connect 4 (1-7)."""
        # Try tic-tac-toe first
        ttt_game = active_ttt.get(ctx.channel.id)
        if ttt_game:
            current = ttt_game["players"][ttt_game["turn"]]
            if ctx.author != current:
                return
            if pos < 1 or pos > 9 or ttt_game["board"][pos - 1] in ("X", "O"):
                return await ctx.send("Invalid move.")
            ttt_game["board"][pos - 1] = ttt_game["turn"]
            winner = ttt_check_winner(ttt_game["board"])
            if winner == "draw":
                x, o = ttt_game["players"]["X"], ttt_game["players"]["O"]
                del active_ttt[ctx.channel.id]
                log_game_draw(x.id, o.id, "ttt")
                return await ctx.send(embed=make_embed("Tic-Tac-Toe — Draw!", ttt_render(ttt_game["board"]), COLOR_WARNING))
            if winner:
                loser_piece = "O" if winner == "X" else "X"
                w = ttt_game["players"][winner]
                loser = ttt_game["players"][loser_piece]
                del active_ttt[ctx.channel.id]
                log_game_win(w.id, loser.id, "ttt")
                return await ctx.send(
                    embed=make_embed(f"Tic-Tac-Toe — {w.display_name} Wins!", ttt_render(ttt_game["board"]), COLOR_SUCCESS)
                )
            ttt_game["turn"] = "O" if ttt_game["turn"] == "X" else "X"
            nxt = ttt_game["players"][ttt_game["turn"]]
            return await ctx.send(
                embed=make_embed("Tic-Tac-Toe", f"{ttt_render(ttt_game['board'])}\n\n{nxt.mention}'s turn — use `{PREFIX}m <1-9>`")
            )

        # Try connect 4
        c4_game = active_c4.get(ctx.channel.id)
        if c4_game:
            current = c4_game["players"][c4_game["turn"]]
            if ctx.author != current:
                return
            if pos < 1 or pos > C4_COLS:
                return await ctx.send(f"Pick a column between 1 and {C4_COLS}.")
            row = c4_drop(c4_game["board"], pos - 1, c4_game["turn"])
            if row == -1:
                return await ctx.send("That column is full.")
            winner = c4_check_winner(c4_game["board"])
            if winner == "draw":
                r_p, y_p = c4_game["players"]["R"], c4_game["players"]["Y"]
                del active_c4[ctx.channel.id]
                log_game_draw(r_p.id, y_p.id, "c4")
                return await ctx.send(embed=make_embed("Connect 4 — Draw!", c4_render(c4_game["board"]), COLOR_WARNING))
            if winner:
                loser_piece = "Y" if winner == "R" else "R"
                w = c4_game["players"][winner]
                loser = c4_game["players"][loser_piece]
                del active_c4[ctx.channel.id]
                log_game_win(w.id, loser.id, "c4")
                return await ctx.send(
                    embed=make_embed(f"Connect 4 — {w.display_name} Wins!", c4_render(c4_game["board"]), COLOR_SUCCESS)
                )
            c4_game["turn"] = "Y" if c4_game["turn"] == "R" else "R"
            nxt = c4_game["players"][c4_game["turn"]]
            return await ctx.send(
                embed=make_embed("Connect 4", f"{c4_render(c4_game['board'])}\n\n{nxt.mention}'s turn — use `{PREFIX}drop <1-7>`")
            )

    @commands.command(aliases=["ff", "quit", "stop"])
    async def forfeit(self, ctx):
        """Forfeit the current tic-tac-toe or connect 4 game."""
        game = active_ttt.get(ctx.channel.id)
        if game and ctx.author in game["players"].values():
            opponent = next(p for p in game["players"].values() if p != ctx.author)
            del active_ttt[ctx.channel.id]
            log_game_win(opponent.id, ctx.author.id, "ttt")
            return await ctx.send(f"{ctx.author.display_name} forfeited the tic-tac-toe game.")
        game = active_c4.get(ctx.channel.id)
        if game and ctx.author in game["players"].values():
            opponent = next(p for p in game["players"].values() if p != ctx.author)
            del active_c4[ctx.channel.id]
            log_game_win(opponent.id, ctx.author.id, "c4")
            return await ctx.send(f"{ctx.author.display_name} forfeited the connect 4 game.")
        game = active_hangman.get(ctx.channel.id)
        if game:
            del active_hangman[ctx.channel.id]
            hangman_msg.pop(ctx.channel.id, None)
            return await ctx.send(f"Hangman game ended. The word was **{game['word']}**.")
        if ctx.channel.id in active_memory_games:
            del active_memory_games[ctx.channel.id]
            return await ctx.send("Memory game cancelled.")
        if ctx.channel.id in active_trivia:
            answer = active_trivia[ctx.channel.id]["answer"]
            del active_trivia[ctx.channel.id]
            return await ctx.send(f"Trivia cancelled. The answer was **{answer}**.")
        if ctx.channel.id in active_scrambles:
            word = active_scrambles[ctx.channel.id]["answer"]
            del active_scrambles[ctx.channel.id]
            return await ctx.send(f"Scramble cancelled. The word was **{word}**.")
        await ctx.send("No active game to forfeit.")

    # ---------------------------------------------------------------------------
    # GAMES: CONNECT 4
    # ---------------------------------------------------------------------------

    @commands.command()
    async def c4(self, ctx, opponent: discord.Member = None):
        """Start a connect 4 game. Tag someone or let others react to join."""
        if ctx.channel.id in active_c4:
            return await ctx.send("There's already a game in this channel. Finish it first.")
        if opponent:
            if opponent.bot or opponent == ctx.author:
                return await ctx.send("You can't play against yourself or a bot.")
            game = start_c4(ctx.author, opponent)
            active_c4[ctx.channel.id] = game
            await ctx.send(
                embed=make_embed(
                    "Connect 4",
                    f"{ctx.author.mention} ({C4_RED}) vs {opponent.mention} ({C4_YELLOW})\n\n"
                    f"{c4_render(game['board'])}\n\n"
                    f"{ctx.author.mention}'s turn — use `{PREFIX}drop <1-7>`",
                )
            )
        else:
            msg = await ctx.send(embed=make_embed("Connect 4", f"{ctx.author.mention} wants to play! React with ✅ to join."))
            await msg.add_reaction("✅")
            pending_games[msg.id] = {
                "type": "c4",
                "host": ctx.author,
                "channel_id": ctx.channel.id,
                "created_at": datetime.now(timezone.utc),
            }

    @commands.command()
    async def drop(self, ctx, col: int):
        """Drop a piece in connect 4 (column 1-7)."""
        game = active_c4.get(ctx.channel.id)
        if not game:
            return
        current = game["players"][game["turn"]]
        if ctx.author != current:
            return
        if col < 1 or col > C4_COLS:
            return await ctx.send(f"Pick a column between 1 and {C4_COLS}.")
        row = c4_drop(game["board"], col - 1, game["turn"])
        if row == -1:
            return await ctx.send("That column is full.")
        winner = c4_check_winner(game["board"])
        if winner == "draw":
            r_p, y_p = game["players"]["R"], game["players"]["Y"]
            del active_c4[ctx.channel.id]
            log_game_draw(r_p.id, y_p.id, "c4")
            return await ctx.send(embed=make_embed("Connect 4 — Draw!", c4_render(game["board"]), COLOR_WARNING))
        if winner:
            loser_piece = "Y" if winner == "R" else "R"
            w = game["players"][winner]
            loser = game["players"][loser_piece]
            del active_c4[ctx.channel.id]
            log_game_win(w.id, loser.id, "c4")
            return await ctx.send(embed=make_embed(f"Connect 4 — {w.display_name} Wins!", c4_render(game["board"]), COLOR_SUCCESS))
        game["turn"] = "Y" if game["turn"] == "R" else "R"
        nxt = game["players"][game["turn"]]
        await ctx.send(
            embed=make_embed("Connect 4", f"{c4_render(game['board'])}\n\n{nxt.mention}'s turn — use `{PREFIX}drop <1-7>`")
        )

    # ---------------------------------------------------------------------------
    # GAMES: HANGMAN
    # ---------------------------------------------------------------------------
    async def _hangman_update(self, channel, embed):
        """Edit the tracked hangman message in place, or send a new one."""
        msg = hangman_msg.get(channel.id)
        if msg:
            try:
                await msg.edit(embed=embed)
                return
            except discord.HTTPException:
                pass
        hangman_msg[channel.id] = await channel.send(embed=embed)

    async def _hangman_end(self, channel, embed):
        """Send a final hangman result and clean up the tracked message."""
        hangman_msg.pop(channel.id, None)

        await channel.send(embed=embed)

    async def _guess_hangman(self, channel, guess_raw: str):
        game = active_hangman.get(channel.id)
        if not game:
            return False

        guess = guess_raw.lower().strip()
        if not guess or not guess.isalpha():
            return False

        # Word guess path (only via .g command)
        if len(guess) > 1:
            if guess == game["word"]:
                del active_hangman[channel.id]
                await self._hangman_end(
                    channel,
                    make_embed(
                        "Hangman - You Win!",
                        f"The word was **{game['word']}**!\n{HANGMAN_STAGES[len(game['wrong'])]}",
                        COLOR_SUCCESS,
                    ),
                )
                return True
            game["wrong"].append(guess)
            if len(game["wrong"]) >= 6:
                del active_hangman[channel.id]
                await self._hangman_end(
                    channel,
                    make_embed("Hangman - Game Over", f"The word was **{game['word']}**.\n{HANGMAN_STAGES[6]}", COLOR_ERROR),
                )
                return True
            await self._hangman_update(channel, make_embed("Hangman", hangman_render(game)))
            return True

        # Single-letter guess path
        letter = guess
        if letter in game["guessed"] or letter in game["wrong"]:
            await self._hangman_update(
                channel, make_embed("Hangman", f"{hangman_render(game)}\n\n**{letter}** was already guessed.")
            )
            return True
        if letter in game["word"]:
            game["guessed"].add(letter)
            if all(ch in game["guessed"] for ch in game["word"]):
                del active_hangman[channel.id]
                await self._hangman_end(
                    channel,
                    make_embed(
                        "Hangman - You Win!",
                        f"The word was **{game['word']}**!\n{HANGMAN_STAGES[len(game['wrong'])]}",
                        COLOR_SUCCESS,
                    ),
                )
                return True
        else:
            game["wrong"].append(letter)
            if len(game["wrong"]) >= 6:
                del active_hangman[channel.id]
                await self._hangman_end(
                    channel,
                    make_embed("Hangman - Game Over", f"The word was **{game['word']}**.\n{HANGMAN_STAGES[6]}", COLOR_ERROR),
                )
                return True
        await self._hangman_update(channel, make_embed("Hangman", hangman_render(game)))
        return True

    @commands.command(aliases=["hang", "hm"])
    async def hangman(self, ctx, player: discord.Member = None):
        """Start a hangman game. Tag someone to invite them, or play solo."""
        if ctx.channel.id in active_hangman:
            return await ctx.send("There's already a hangman game in this channel. Finish it first.")
        word = random.choice(HANGMAN_WORDS)
        players = {ctx.author.id}
        if player:
            players.add(player.id)
        game = {
            "word": word,
            "guessed": set(),
            "wrong": [],
            "started_by": ctx.author,
            "players": players,
        }
        active_hangman[ctx.channel.id] = game
        msg = f"{ctx.author.mention} started a hangman game!"
        if player:
            msg = f"{ctx.author.mention} started a hangman game with {player.mention}!"
        msg += f" Type a single letter, or use `{PREFIX}g <guess>` for letter/word guesses."
        hangman_msg[ctx.channel.id] = await ctx.send(embed=make_embed("Hangman", f"{msg}\n\n{hangman_render(game)}"))

    @commands.Cog.listener("on_message")
    async def hangman_letter_listener(self, message):
        if message.author.bot:
            return
        game = active_hangman.get(message.channel.id)
        if not game:
            return
        if message.author.id not in game.get("players", set()):
            return
        content = message.content.strip()
        if not content or content.startswith(PREFIX):
            return
        # Plain-message guesses are single-letter only.
        if len(content) != 1 or not content.isalpha():
            return
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        await self._guess_hangman(message.channel, content)

    @commands.command()
    async def g(self, ctx, guess: str):
        """Guess a letter or whole word in hangman."""
        game = active_hangman.get(ctx.channel.id)
        if not game:
            return await ctx.send("No active hangman game in this channel.")
        if ctx.author.id not in game.get("players", set()):
            return
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        await self._guess_hangman(ctx.channel, guess)


async def setup(bot):
    await bot.add_cog(GamesCog(bot))
