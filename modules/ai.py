import asyncio
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord.ext import commands, tasks

import shared
from shared import *


class AICog(commands.Cog):
    BJ_BET_PCT = 0.025
    BJ_BET_CAP = 75
    BJ_MIN_BALANCE = 200
    BJ_STOP_LOSS_PCT = 0.35
    BJ_TAKE_PROFIT_PCT = 0.60
    GAMBLE_ACTION_COOLDOWN = timedelta(minutes=1)
    BJ_ACTIVE_TIMEOUT = timedelta(minutes=3)
    HM_ACTIVE_TIMEOUT = timedelta(minutes=3)
    GAMBLE_REPORT_CHANNEL_ID_FALLBACK = REDACTED_CHANNEL_ID
    GAMBLE_REPORT_INTERVAL = timedelta(minutes=5)

    def __init__(self, bot):
        self.bot = bot
        loaded = load_gary_gamble_state()
        last_action_raw = loaded.get("last_action_at")
        blackjack_started_raw = loaded.get("blackjack_started_at")
        hangman_started_raw = loaded.get("hangman_started_at")
        last_action = None
        blackjack_started = None
        hangman_started = None
        for raw, name in [
            (last_action_raw, "last_action"),
            (blackjack_started_raw, "blackjack_started"),
            (hangman_started_raw, "hangman_started"),
        ]:
            if isinstance(raw, str) and raw:
                try:
                    dt = datetime.fromisoformat(raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    dt = None
                if name == "last_action":
                    last_action = dt
                elif name == "blackjack_started":
                    blackjack_started = dt
                else:
                    hangman_started = dt
        self.gamble_state = {
            "day": loaded.get("day", ""),
            "scratchoffs_used": loaded.get("scratchoffs_used", 0),
            "blackjack_active": loaded.get("blackjack_active", False),
            "last_action_at": last_action,
            "blackjack_started_at": blackjack_started,
            "hangman_active": loaded.get("hangman_active", False),
            "hangman_started_at": hangman_started,
            "last_known_balance": loaded.get("last_known_balance"),
            "session_anchor_balance": loaded.get("session_anchor_balance"),
        }
        self._last_gamble_report_at = None
        self._last_gamble_result = None

    def _persist_gamble_state(self):
        last_action = self.gamble_state.get("last_action_at")
        blackjack_started = self.gamble_state.get("blackjack_started_at")
        hangman_started = self.gamble_state.get("hangman_started_at")
        save_gary_gamble_state(
            {
                "day": self.gamble_state.get("day", ""),
                "scratchoffs_used": self.gamble_state.get("scratchoffs_used", 0),
                "blackjack_active": self.gamble_state.get("blackjack_active", False),
                "last_action_at": last_action.isoformat() if isinstance(last_action, datetime) else None,
                "blackjack_started_at": blackjack_started.isoformat() if isinstance(blackjack_started, datetime) else None,
                "hangman_active": self.gamble_state.get("hangman_active", False),
                "hangman_started_at": hangman_started.isoformat() if isinstance(hangman_started, datetime) else None,
                "last_known_balance": self.gamble_state.get("last_known_balance"),
                "session_anchor_balance": self.gamble_state.get("session_anchor_balance"),
            }
        )

    def _extract_balance_from_text(self, text: str):
        patterns = [
            r"balance[^0-9\-]*([0-9][0-9,]*)",
            r"\b([0-9][0-9,]*)\s*(?:coins?|🪙)\b",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, flags=re.IGNORECASE)
            if not m:
                continue
            raw = m.group(1).replace(",", "")
            try:
                value = int(raw)
                if value >= 0:
                    return value
            except ValueError:
                continue
        return None

    def _compute_blackjack_bet(self, balance: int, anchor: int):
        if balance < self.BJ_MIN_BALANCE:
            return 0
        if anchor <= 0:
            anchor = balance

        ratio = balance / anchor if anchor else 1.0
        risk_scale = 1.0
        if ratio < 0.65:
            risk_scale = 0.4
        elif ratio < 0.80:
            risk_scale = 0.6

        raw = int(balance * self.BJ_BET_PCT * risk_scale)
        bet = max(1, raw)
        bet = min(bet, self.BJ_BET_CAP, balance)
        return bet

    async def _send_gamble_report(self, summary: str, force: bool = False):
        report_id = runtime_settings.get("gary_gamble_report_channel_id") or self.GAMBLE_REPORT_CHANNEL_ID_FALLBACK
        channel = self.bot.get_channel(int(report_id))
        if channel is None:
            return
        now = datetime.now(timezone.utc)
        if (
            not force
            and self._last_gamble_report_at is not None
            and now - self._last_gamble_report_at < self.GAMBLE_REPORT_INTERVAL
        ):
            return

        last_action = self.gamble_state.get("last_action_at")
        cooldown_left = "ready"
        if isinstance(last_action, datetime):
            remaining = self.GAMBLE_ACTION_COOLDOWN - (now - last_action)
            if remaining.total_seconds() > 0:
                cooldown_left = f"{int(remaining.total_seconds())}s"
        balance = self.gamble_state.get("last_known_balance")
        anchor = self.gamble_state.get("session_anchor_balance")
        next_bet = (
            self._compute_blackjack_bet(balance, anchor or balance)
            if isinstance(balance, int) and balance > 0
            else None
        )
        await channel.send(
            "Gary Gamble Report\n"
            f"Summary: {summary}\n"
            f"Day: {self.gamble_state.get('day')} | Scratches used: {self.gamble_state.get('scratchoffs_used')} | "
            f"Blackjack active: {self.gamble_state.get('blackjack_active')}\n"
            f"Balance: {balance if balance is not None else 'unknown'} | "
            f"Anchor: {anchor if anchor is not None else 'unknown'} | "
            f"Next bet: {next_bet if next_bet is not None else 'n/a'} | "
            f"Cooldown: {cooldown_left}"
        )
        self._last_gamble_report_at = now

    def _scratch_reset_key(self, now_utc: datetime) -> str:
        """Return logical daily key where a new day starts at 5:00 AM Central."""
        now_central = now_utc.astimezone(CENTRAL_TZ)
        if now_central.hour < 5:
            now_central -= timedelta(days=1)
        return now_central.strftime("%Y-%m-%d")

    def _bj_rank_value(self, rank: str) -> int:
        rank = rank.upper()
        if rank in {"J", "Q", "K"}:
            return 10
        if rank == "A":
            return 11
        return int(rank)

    def _bj_is_soft(self, ranks: list[str], total: int) -> bool:
        # Soft if at least one ace is effectively counted as 11 in the shown total.
        hard_total = 0
        aces = 0
        for rank in ranks:
            if rank == "A":
                hard_total += 1
                aces += 1
            else:
                hard_total += self._bj_rank_value(rank)
        if aces == 0:
            return False
        return total <= 21 and (hard_total + 10 == total)

    def _recommend_blackjack_action(self, total: int, dealer_up: int, soft: bool) -> str:
        if soft:
            if total >= 19:
                return "stand"
            if total == 18:
                return "hit" if dealer_up in (9, 10, 11) else "stand"
            return "hit"

        if total >= 17:
            return "stand"
        if 13 <= total <= 16:
            return "stand" if 2 <= dealer_up <= 6 else "hit"
        if total == 12:
            return "stand" if 4 <= dealer_up <= 6 else "hit"
        return "hit"
    def _parse_blackjack_prompt(self, text: str):
        # Support both old and new Silas prompt formats:
        # Old: "Your hand: `A♠` `7♦` → **18**", "Dealer shows: `K♣` `??`"
        # New: "Dealer: J♦ 🂠", "Gary (12): Q♠ 2♠"  (player name varies)
        total = None
        dealer_up = None
        ranks = []

        old_total = re.search(r"your hand:.*?\*\*(\d+)\*\*", text, flags=re.IGNORECASE | re.DOTALL)
        # Match player name before (total): e.g. "Gary (17):", "You (12):"
        # Exclude "Dealer" to avoid matching the dealer's line.
        new_total = re.search(r"(?:^|\n)\s*(?!dealer)\w+\s*\((\d+)\)\s*:", text, flags=re.IGNORECASE | re.MULTILINE)
        if old_total:
            total = int(old_total.group(1))
        elif new_total:
            total = int(new_total.group(1))

        old_dealer = re.search(r"dealer shows:.*?`([^`]+)`", text, flags=re.IGNORECASE | re.DOTALL)
        new_dealer = re.search(r"dealer[^:]*:\s*([0-9]{1,2}|[JQKA])[♠♥♦♣]", text, flags=re.IGNORECASE)
        if old_dealer:
            dealer_card = old_dealer.group(1).strip()
            dealer_rank = dealer_card[:-1] if len(dealer_card) > 1 else dealer_card
            dealer_up = 10 if dealer_rank == "10" else self._bj_rank_value(dealer_rank[0])
        elif new_dealer:
            dealer_up = self._bj_rank_value(new_dealer.group(1).upper())

        hand_line_old = re.search(r"your hand:(.*?)(?:\n|$)", text, flags=re.IGNORECASE | re.DOTALL)
        if hand_line_old:
            for token in re.findall(r"`([^`]+)`", hand_line_old.group(1)):
                token = token.strip()
                if token == "??":
                    continue
                rank = token[:-1] if len(token) > 1 else token
                if rank == "10":
                    ranks.append("10")
                elif rank:
                    ranks.append(rank[0].upper())
        else:
            # Match "Gary (17): 9♣ 8♣" or "You (12): Q♠ 2♠" — exclude Dealer line
            hand_line_new = re.search(r"(?!dealer)\w+\s*\(\d+\)\s*:\s*([^\n]+)", text, flags=re.IGNORECASE)
            if hand_line_new:
                cards = re.findall(r"([0-9]{1,2}|[JQKA])[♠♥♦♣]", hand_line_new.group(1), flags=re.IGNORECASE)
                for card_rank in cards:
                    ranks.append(card_rank.upper())

        if total is None or dealer_up is None:
            return None
        soft = self._bj_is_soft(ranks, total)
        return total, dealer_up, soft

    def _parse_silas_hangman(self, text: str):
        """Parse Silas's hangman message. Returns dict or None."""
        lower = text.lower()
        # Detect game end
        if "game over" in lower or "the word was" in lower and "lives left: 0" in lower:
            return {"status": "lost"}
        if "you got it" in lower or ("the word was" in lower and "word complete" in lower):
            return {"status": "won"}

        # Parse active game state
        word_match = re.search(r"word:\s*(.+)", text, flags=re.IGNORECASE)
        guessed_match = re.search(r"guessed:\s*(.+)", text, flags=re.IGNORECASE)
        lives_match = re.search(r"lives left:\s*(\d+)", text, flags=re.IGNORECASE)
        if not word_match or not lives_match:
            return None

        # "Word: _ _ _ e _" -> ['_', '_', '_', 'e', '_']
        word_raw = word_match.group(1).strip()
        word_pattern = [ch for ch in word_raw.split() if ch]
        if not word_pattern:
            return None

        lives = int(lives_match.group(1))

        guessed_raw = guessed_match.group(1).strip() if guessed_match else "none"
        if guessed_raw.lower() == "none":
            guessed = set()
        else:
            guessed = {ch.strip().lower() for ch in guessed_raw.split(",") if ch.strip().isalpha() and len(ch.strip()) == 1}

        return {"status": "active", "word_pattern": word_pattern, "guessed": guessed, "lives": lives}

    _silas_wordlist = None

    @classmethod
    def _load_silas_wordlist(cls):
        if cls._silas_wordlist is None:
            path = Path(__file__).resolve().parent.parent / "data" / "wordlist_10k.txt"
            try:
                cls._silas_wordlist = [
                    w.strip().lower() for w in path.read_text(encoding="utf-8").splitlines()
                    if w.strip()
                ]
            except FileNotFoundError:
                cls._silas_wordlist = []
        return cls._silas_wordlist

    def _pick_hangman_letter(self, word_pattern, guessed):
        """Pick the best letter for Silas's hangman.

        Priority: 10k word list candidates -> Gary's HANGMAN_WORDS -> letter frequency.
        """
        from modules.games import best_hangman_letter, LETTER_PRIORITY

        revealed = {ch for ch in word_pattern if ch != '_'}
        wrong = {ch for ch in guessed if ch not in revealed}
        tried = guessed | revealed
        target_len = len(word_pattern)

        # --- Tier 1: 10k word list ---
        candidates = []
        for word in self._load_silas_wordlist():
            if len(word) != target_len:
                continue
            if any(ch in word for ch in wrong):
                continue
            match = True
            for i, pat in enumerate(word_pattern):
                if pat != '_':
                    if word[i] != pat:
                        match = False
                        break
                else:
                    if word[i] in revealed:
                        match = False
                        break
            if match:
                candidates.append(word)

        if candidates:
            counts = {}
            for word in candidates:
                for ch in set(word):
                    if ch not in tried:
                        counts[ch] = counts.get(ch, 0) + 1
            if counts:
                return max(counts, key=counts.get)

        # --- Tier 2: Gary's built-in word list (covers edge cases) ---
        word_str = "".join(ch if ch != '_' else '\x00' for ch in word_pattern)
        game = {"word": word_str, "guessed": revealed, "wrong": list(wrong)}
        letter, _ = best_hangman_letter(game)
        if letter:
            return letter

        # --- Tier 3: raw letter frequency ---
        for ch in LETTER_PRIORITY:
            if ch not in tried:
                return ch
        return None

    async def _handle_silas_gambling_message(self, message, silas_text: str):
        channel_id = runtime_settings.get("gary_gamble_channel_id")
        if not runtime_settings.get("gary_gamble_enabled", False):
            return
        if not channel_id or message.channel.id != int(channel_id):
            return

        lower = silas_text.lower()
        bal = self._extract_balance_from_text(silas_text)
        if bal is not None:
            self.gamble_state["last_known_balance"] = bal
            self._persist_gamble_state()

        # Mark hand resolved for result-style blackjack messages.
        resolved_blackjack = (
            ("blackjack" in lower and "balance:" in lower)
            or "dealer wins" in lower
            or "gary wins" in lower
            or "you win" in lower
            or "push" in lower
            or "bust" in lower
        )
        if resolved_blackjack:
            self.gamble_state["blackjack_active"] = False
            self.gamble_state["blackjack_started_at"] = None
            self._persist_gamble_state()
            return

        if "hit" in lower and "stand" in lower and self.gamble_state["blackjack_active"]:
            if self.gamble_state.get("blackjack_started_at") is None:
                self.gamble_state["blackjack_started_at"] = datetime.now(timezone.utc)
                self._persist_gamble_state()
            parsed = self._parse_blackjack_prompt(silas_text)
            if parsed is None:
                await self._send_gamble_report(
                    f"BJ parse failed, falling back to stand. Text: {silas_text[:200]}", force=True
                )
                await message.channel.send("stand")
                return
            total, dealer_up, soft = parsed
            action = self._recommend_blackjack_action(total, dealer_up, soft)
            await message.channel.send(action)
            return

        # --- Hangman handling ---
        hangman = self._parse_silas_hangman(silas_text)
        if hangman and self.gamble_state.get("hangman_active"):
            if hangman["status"] in ("won", "lost"):
                self.gamble_state["hangman_active"] = False
                self.gamble_state["hangman_started_at"] = None
                self._persist_gamble_state()
                return
            if hangman["status"] == "active":
                if self.gamble_state.get("hangman_started_at") is None:
                    self.gamble_state["hangman_started_at"] = datetime.now(timezone.utc)
                    self._persist_gamble_state()
                letter = self._pick_hangman_letter(hangman["word_pattern"], hangman["guessed"])
                if letter:
                    await message.channel.send(letter)

    async def run_gamble_step(self, bypass_cooldown: bool = False) -> str:
        """Run one autonomous gambling decision step."""
        if not runtime_settings.get("gary_gamble_enabled", False):
            return "Gary autonomous gambling is OFF."

        channel_id = runtime_settings.get("gary_gamble_channel_id")
        if not channel_id:
            return "Gamble channel is not set."

        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            return "Configured gamble channel is unavailable."

        now = datetime.now(timezone.utc)
        cycle_key = self._scratch_reset_key(now)
        if self.gamble_state["day"] != cycle_key:
            self.gamble_state["day"] = cycle_key
            self.gamble_state["scratchoffs_used"] = 0
            self.gamble_state["blackjack_active"] = False
            self.gamble_state["blackjack_started_at"] = None
            self.gamble_state["hangman_active"] = False
            self.gamble_state["hangman_started_at"] = None
            self.gamble_state["session_anchor_balance"] = None
            self._persist_gamble_state()

        # Clear stale active games
        for game, started_key, timeout in [
            ("blackjack_active", "blackjack_started_at", self.BJ_ACTIVE_TIMEOUT),
            ("hangman_active", "hangman_started_at", self.HM_ACTIVE_TIMEOUT),
        ]:
            started_at = self.gamble_state.get(started_key)
            if (
                self.gamble_state.get(game)
                and isinstance(started_at, datetime)
                and now - started_at > timeout
            ):
                self.gamble_state[game] = False
                self.gamble_state[started_key] = None
                self._persist_gamble_state()
                return f"Cleared stale {game} after timeout."

        last_action = self.gamble_state["last_action_at"]
        if (
            not bypass_cooldown
            and last_action
            and now - last_action < self.GAMBLE_ACTION_COOLDOWN
        ):
            return "Cooldown active; next action will happen automatically."

        if self.gamble_state["scratchoffs_used"] < 3:
            await channel.send("!scratches")
            self.gamble_state["scratchoffs_used"] = 3
            self.gamble_state["last_action_at"] = now
            self._persist_gamble_state()
            return "Sent `!scratches`."

        balance = self.gamble_state.get("last_known_balance")
        if balance is None:
            await channel.send("!balance")
            self.gamble_state["last_action_at"] = now
            self._persist_gamble_state()
            return "Requested balance with `!balance`."

        anchor = self.gamble_state.get("session_anchor_balance")
        if anchor is None:
            self.gamble_state["session_anchor_balance"] = balance
            anchor = balance
            self._persist_gamble_state()

        bj_stopped = (
            balance <= int(anchor * (1.0 - self.BJ_STOP_LOSS_PCT))
            or balance >= int(anchor * (1.0 + self.BJ_TAKE_PROFIT_PCT))
        )
        if bj_stopped:
            if self.gamble_state.get("hangman_active"):
                return "Hangman in progress; waiting for Silas."
            # Hangman is free — no cooldown needed between games.
            await channel.send("!hang")
            self.gamble_state["hangman_active"] = True
            self.gamble_state["hangman_started_at"] = now
            self._persist_gamble_state()
            reason = "stop-loss" if balance <= int(anchor * (1.0 - self.BJ_STOP_LOSS_PCT)) else "take-profit"
            return f"BJ {reason} at {balance}; started hangman."

        if not self.gamble_state["blackjack_active"]:
            bet = self._compute_blackjack_bet(balance, anchor)
            if bet <= 0:
                return f"Balance {balance} below minimum bankroll threshold."
            await channel.send(f"!bj {bet}")
            self.gamble_state["blackjack_active"] = True
            self.gamble_state["blackjack_started_at"] = now
            self.gamble_state["last_action_at"] = now
            self._persist_gamble_state()
            return f"Started blackjack with `!bj {bet}`."

        return "Blackjack hand already active; waiting to play hit/stand."

    @commands.Cog.listener("on_ready")
    async def _start_ai_tasks(self):
        if not self.dead_chat_checker.is_running():
            self.dead_chat_checker.start()
        if not self.silas_gambler.is_running():
            self.silas_gambler.start()
    

    @commands.Cog.listener("on_raw_message_edit")
    async def on_raw_message_edit(self, payload):
        """Handle Silas editing his hangman message in place."""
        if not self.gamble_state.get("hangman_active"):
            return
        channel_id = runtime_settings.get("gary_gamble_channel_id")
        if not channel_id or payload.channel_id != int(channel_id):
            return
        # Fetch the full message to get author and content
        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return
        if message.author.id != SILAS_BOT_ID:
            return
        silas_text = message.content
        if message.embeds:
            parts = []
            for e in message.embeds:
                if e.title:
                    parts.append(e.title)
                if e.description:
                    parts.append(e.description)
            silas_text = silas_text or "\n".join(parts)
        if not silas_text:
            return
        await self._handle_silas_gambling_message(message, silas_text)

    @commands.Cog.listener("on_message")
    async def on_message(self, message):
        # Ignore our own messages
        if message.author.id == self.bot.user.id:
            return
        shared.messages_seen += 1
    
        # --- Silas interaction ---
        if message.author.id == SILAS_BOT_ID:
            channel_id = message.channel.id
            if not is_feature_allowed("silas", channel_id):
                return
    
            # Auto-accept roleplay invites from Silas
            if message.embeds:
                for e in message.embeds:
                    title = (e.title or "").lower()
                    if "roleplay invite" in title or ("invite" in title and self.bot.user.mentioned_in(message)):
                        try:
                            await message.add_reaction("✅")
                        except discord.HTTPException:
                            pass
                        # Start a roleplay session as Gary
                        active_silas_rp[channel_id] = {
                            "character": "Gary",
                            "history": [
                                {"role": "system", "content": (
                                    "You are Gary, a Discord bot with attitude. You're in a roleplay with another bot named Silas. "
                                    "You're snarky, competitive, and think you're the better bot. "
                                    "Stay in character as yourself — a witty, slightly unhinged bot who doesn't take anything too seriously. "
                                    "Keep responses short (2-4 sentences). Use lowercase."
                                )},
                            ],
                        }
                        await asyncio.sleep(random.uniform(1, 3))
                        response = await query_ollama_chat(
                            active_silas_rp[channel_id]["history"] + [
                                {"role": "user", "content": "The roleplay is starting. Silas just invited you. Say something to kick things off."}
                            ])
                        if response:
                            text = response.strip()
                            if len(text) > 500:
                                text = text[:500] + "..."
                            active_silas_rp[channel_id]["history"].append({"role": "assistant", "content": text})
                            await message.channel.send(text)
                        return
    
            # Extract Silas's text from message or embeds
            silas_text = message.content
            if message.embeds:
                parts = []
                for e in message.embeds:
                    if e.title:
                        parts.append(e.title)
                    if e.description:
                        parts.append(e.description)
                silas_text = silas_text or "\n".join(parts)

            await self._handle_silas_gambling_message(message, silas_text or "")
    
            # --- Active roleplay with Silas ---
            rp = active_silas_rp.get(channel_id)
            if rp and silas_text:
                rp["history"].append({"role": "user", "content": silas_text[:500]})
                # Keep history manageable
                if len(rp["history"]) > 21:
                    rp["history"] = [rp["history"][0]] + rp["history"][-20:]
                async with message.channel.typing():
                    response = await query_ollama_chat(rp["history"])
                if response:
                    text = response.strip()
                    if len(text) > 500:
                        text = text[:500] + "..."
                    rp["history"].append({"role": "assistant", "content": text})
                    await asyncio.sleep(random.uniform(1, 3))
                    await message.reply(text, mention_author=False)
                return
    
            # --- Random banter ---
            silas_react_chance = runtime_settings.get("silas_react_chance_pct", 0) / 100.0
            silas_banter_chance = runtime_settings.get("silas_banter_chance_pct", 0) / 100.0
            if random.random() < silas_react_chance:
                try:
                    await message.add_reaction(random.choice(SILAS_REACTIONS))
                except discord.HTTPException:
                    pass

            if silas_text and random.random() < silas_banter_chance:
                prompt = SILAS_BANTER_PROMPT.format(silas_message=silas_text[:500])
                response = await query_ollama(prompt, "", model=OLLAMA_REASONING_MODEL)
                if response:
                    text = clean_reasoning(response)
                    if text and "pass" not in text.lower():
                        await asyncio.sleep(random.uniform(2, 6))
                        if len(text) > 500:
                            text = text[:500] + "..."
                        await message.reply(text, mention_author=False)
            return
    
        # Ignore other bots
        if message.author.bot:
            return
    
        channel_id = message.channel.id
        now = datetime.now(timezone.utc)
        now_central = now.astimezone(CENTRAL_TZ)
    
        # --- Track message times for dead chat ---
        if runtime_settings.get("dead_chat_enabled", True) and is_feature_allowed("dead_chat", channel_id):
            last_message_time[channel_id] = now
            dead_chat_stage[channel_id] = -1  # reset escalation
    
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
    
        # --- Daily reminder (DB-backed throttle; never on command messages) ---
        content = message.content.strip()
        if (
            runtime_settings.get("daily_reminder_enabled", True)
            and is_feature_allowed("daily_reminder", channel_id)
            and content
            and not content.startswith(PREFIX)
        ):
            user_id = message.author.id
            last_reminder = get_last_daily_reminder_time(user_id)
            reminder_ok = (
                last_reminder is None
                or (now - last_reminder) >= timedelta(hours=24)
            )
            if reminder_ok:
                available, _ = is_daily_available(user_id, now=now_central)
                if available:
                    set_last_daily_reminder_time(user_id, when=now)
                    await message.reply(
                        f"Your daily is ready. Use `{PREFIX}daily`.",
                        mention_author=False
                    )
    
        # --- Respond when tagged ---
        if (
            self.bot.user.mentioned_in(message)
            and not message.mention_everyone
            and is_feature_allowed("mention_reply", channel_id)
        ):
            context = recent_messages.get(channel_id, [])
            chat_log = "\n".join(f"{m['author']}: {m['content']}" for m in context[-10:])
            prompt = (
                f"Here's the recent chat:\n\n{chat_log}\n\n"
                f"{message.author.display_name} just tagged you and said: {message.content}\n"
                f"Respond to them directly."
            )
            async with message.channel.typing():
                response = await query_ollama(UNSOLICITED_SYSTEM_PROMPT, prompt, model=OLLAMA_REASONING_MODEL)
            if response:
                text = clean_reasoning(response)
                if text and "pass" not in text.lower():
                    if len(text) > 500:
                        text = text[:500] + "..."
                    await message.reply(text, mention_author=False)
            return
    
        # --- Late night callout ---
        hour_central = now.astimezone(CENTRAL_TZ).hour
        if (
            LATE_NIGHT_START <= hour_central < LATE_NIGHT_END
            and is_feature_allowed("late_night", channel_id)
        ):
            today_str = now.strftime("%Y-%m-%d")
            user_key = f"{message.author.id}-{today_str}"
            if user_key not in last_late_night and random.random() < LATE_NIGHT_CHANCE:
                last_late_night[user_key] = True
                # Small delay so it doesn't feel instant
                await asyncio.sleep(random.uniform(2, 8))
                response = random.choice(LATE_NIGHT_RESPONSES)
                await message.channel.send(f"{message.author.mention} {response}")
                # Don't also do unsolicited opinion on the same message
                return
    
        # --- Unsolicited opinions (Ollama) ---
        unsolicited_chance = runtime_settings.get("unsolicited_chance_pct", 0) / 100.0
        if (
            is_feature_allowed("unsolicited_ai", channel_id)
            and random.random() < unsolicited_chance
            and len(message.content) > 5
        ):
            context = recent_messages.get(channel_id, [])
            if len(context) >= 2:
                # Format recent messages for the LLM
                chat_log = "\n".join(
                    f"{m['author']}: {m['content']}" for m in context[-10:]
                )
                prompt = f"Here are the last few messages in the group chat:\n\n{chat_log}\n\nDo you have anything to say?"
    
                response = await query_ollama(UNSOLICITED_SYSTEM_PROMPT, prompt, model=OLLAMA_REASONING_MODEL)
    
                if response:
                    text = clean_reasoning(response)
                    if text and "pass" not in text.lower():
                        # Only show typing once we know we're going to say something
                        async with message.channel.typing():
                            await asyncio.sleep(random.uniform(1, 4))
                        if len(text) > 500:
                            text = text[:500] + "..."
                        await message.channel.send(text)
    
        # Process commands as normal
    
    
    # DEAD CHAT CHECKER — background task
    # ---------------------------------------------------------------------------

    @tasks.loop(minutes=10)
    async def dead_chat_checker(self):
        """Periodically check all tracked channels for dead chat."""
        if not runtime_settings.get("dead_chat_enabled", True):
            return
        now = datetime.now(timezone.utc)
        for channel_id, last_time in list(last_message_time.items()):
            if not is_feature_allowed("dead_chat", channel_id):
                continue
            minutes_silent = (now - last_time).total_seconds() / 60
            current_stage = dead_chat_stage.get(channel_id, -1)
    
            # Find the highest threshold we've crossed
            new_stage = -1
            for i, threshold in enumerate(DEAD_CHAT_THRESHOLDS):
                if minutes_silent >= threshold:
                    new_stage = i
    
            # Only fire if we've crossed into a NEW stage
            if new_stage > current_stage:
                dead_chat_stage[channel_id] = new_stage
                channel = self.bot.get_channel(channel_id)
                if channel and channel.name == DEAD_CHAT_CHANNEL:
                    response = random.choice(DEAD_CHAT_RESPONSES[new_stage])
                    await channel.send(response)

    @tasks.loop(seconds=30)
    async def silas_gambler(self):
        """Autonomous gambler for Silas economy (settings-controlled)."""
        # Don't run or report when the feature is off — otherwise the report
        # channel gets a throttled "OFF" message every 5 minutes forever.
        if not runtime_settings.get("gary_gamble_enabled", False):
            return
        result = await self.run_gamble_step(bypass_cooldown=False)
        is_idle = result.startswith((
            "Stop-loss",
            "Take-profit",
            "Cooldown active",
            "Blackjack hand already active",
            "Balance",
        ))
        # Idle/terminal states report once then go silent until something changes.
        if is_idle and result == self._last_gamble_result:
            return
        self._last_gamble_result = result
        force = result.startswith(
            (
                "Sent `!scratches`.",
                "Started blackjack with",
                "Requested balance",
            )
        )
        await self._send_gamble_report(result, force=force)
    
    # ---------------------------------------------------------------------------
    # EXPLICIT COMMANDS: !ask (Ollama)
    # ---------------------------------------------------------------------------

    @commands.command()
    async def ask(self, ctx, *, question: str):
        """Ask the AI a question (requires desktop to be on)."""
        async with ctx.typing():
            response = await query_ollama(ASK_SYSTEM_PROMPT, question, model=OLLAMA_REASONING_MODEL)
    
        if response is None:
            await ctx.send("Brain's offline right now — desktop must be asleep. Try again later.")
            return
    
        response = clean_reasoning(response)
        if len(response) > 1900:
            response = response[:1900] + "..."
        await ctx.send(response)
    
    
    # SILAS ROLEPLAY
    # ---------------------------------------------------------------------------

    @commands.command()
    async def rp(self, ctx, *, character: str):
        """Start a roleplay between Gary and Silas."""
        if ctx.channel.id in active_silas_rp:
            return await ctx.send("There's already a roleplay going in this channel. Use `.stoprp` to end it.")
        active_silas_rp[ctx.channel.id] = {
            "character": character,
            "history": [
                {"role": "system", "content": (
                    f"You are roleplaying as {character} in a Discord chat. "
                    "Another character (played by Silas) is roleplaying with you. "
                    "Stay in character. Keep responses short (2-4 sentences). "
                    "Be creative and dramatic. Use lowercase, no quotation marks around your dialogue."
                )},
            ],
        }
        # Trigger Silas's roleplay command
        await ctx.send(f"!roleplay {character}")
        await ctx.send(embed=make_embed(
            "Roleplay Started",
            f"Gary is roleplaying as **{character}** with Silas.\n"
            f"Use `{PREFIX}stoprp` to end the session."))
    
    

    @commands.command()
    async def stoprp(self, ctx):
        """Stop the current roleplay with Silas."""
        if ctx.channel.id in active_silas_rp:
            del active_silas_rp[ctx.channel.id]
            # Tell Silas to stop too
            await ctx.send("!stop")
            await ctx.send("Roleplay ended.")
        else:
            await ctx.send("No active roleplay in this channel.")
    

async def setup(bot):
    await bot.add_cog(AICog(bot))

