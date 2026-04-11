import asyncio
import random
import re
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from shared import *


class AICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener("on_ready")
    async def _start_ai_tasks(self):
        if not self.dead_chat_checker.is_running():
            self.dead_chat_checker.start()
    

    @commands.Cog.listener("on_message")
    async def on_message(self, message):
        # Ignore our own messages
        if message.author.id == self.bot.user.id:
            return
    
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
            if random.random() < SILAS_REACT_CHANCE:
                try:
                    await message.add_reaction(random.choice(SILAS_REACTIONS))
                except discord.HTTPException:
                    pass
    
            if silas_text and random.random() < SILAS_BANTER_CHANCE:
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
    
        # --- Daily reminder (once per day per user) ---
        if not message.content.strip().lower().startswith(f"{PREFIX}daily"):
            today = now_central.strftime("%Y-%m-%d")
            user_id = message.author.id
            if daily_reminder_sent.get(user_id) != today:
                available, _ = is_daily_available(user_id, now=now_central)
                if available:
                    daily_reminder_sent[user_id] = today
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
        if (
            is_feature_allowed("unsolicited_ai", channel_id)
            and random.random() < UNSOLICITED_CHANCE
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
