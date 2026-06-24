"""Reminder feature: `.remindme "text"` opens an interactive card to schedule a
one-off or repeating reminder, delivered in a channel or via DM.

All times are interpreted and displayed in Central time (America/Chicago) to
match the rest of the bot; they are stored in the DB as UTC ISO strings. A
background loop fires due reminders and reschedules repeating ones, so reminders
survive a restart.

The time-parsing and scheduling logic lives in module-level pure functions
(`parse_when`, `compute_next_fire`) so it can be unit-tested without Discord.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta, timezone

import discord
from discord.ext import commands, tasks

import shared
from shared import CENTRAL_TZ, COLOR_DEFAULT, COLOR_ERROR, COLOR_SUCCESS, PREFIX, make_embed

logger = logging.getLogger(__name__)

MAX_ACTIVE_REMINDERS = 25
MAX_TEXT_LEN = 500
CHECK_INTERVAL_SECONDS = 30

# Unit aliases for relative durations like "90m" / "2h" / "1d" / "1w".
_MINUTE_UNITS = {"m", "min", "mins", "minute", "minutes"}
_HOUR_UNITS = {"h", "hr", "hrs", "hour", "hours"}
_DAY_UNITS = {"d", "day", "days"}
_WEEK_UNITS = {"w", "wk", "wks", "week", "weeks"}

REPEAT_LABELS = {
    "none": "Does not repeat",
    "daily": "Every day",
    "weekly": "Every week",
    "monthly": "Every month",
    "weekdays": "Every weekday (Mon–Fri)",
    "interval": "Custom interval",
}

# Shown in the custom-time modal so users know exactly what formats work.
WHEN_EXAMPLES = (
    "Relative: 30m · 90m · 2h · 1h30m · 1d · 1w\n"
    "Clock time (today/next): 9am · 9:30pm · 14:00\n"
    "Day + time: tomorrow 9am · tonight · today 5pm\n"
    "Date: 7/1 2pm · jul 1 2:30pm · 2026-07-01 14:30"
)


# ---------------------------------------------------------------------------
# TIME PARSING (pure)
# ---------------------------------------------------------------------------
def _parse_relative(s: str) -> timedelta | None:
    """Parse a pure relative duration like '90m', '2h', '1h30m', '1w'.

    Returns a positive timedelta, or None if the string isn't a pure duration.
    """
    s = s.strip()
    if not s or ":" in s or "/" in s:
        return None
    tokens = re.findall(r"(\d+)\s*([a-zA-Z]+)", s)
    if not tokens:
        return None
    # The whole string must be made of <num><unit> tokens (nothing left over).
    leftover = re.sub(r"(\d+)\s*([a-zA-Z]+)", "", s).strip()
    if leftover:
        return None
    total = timedelta()
    for num, unit in tokens:
        n = int(num)
        u = unit.lower()
        if u in _MINUTE_UNITS:
            total += timedelta(minutes=n)
        elif u in _HOUR_UNITS:
            total += timedelta(hours=n)
        elif u in _DAY_UNITS:
            total += timedelta(days=n)
        elif u in _WEEK_UNITS:
            total += timedelta(weeks=n)
        else:
            return None
    return total if total.total_seconds() > 0 else None


def _parse_time_of_day(s: str) -> time | None:
    s = s.strip().replace(" ", "")
    for fmt in ("%I:%M%p", "%I%p", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


# (format, has_year, has_date) — date means month/day present.
_ABS_FORMATS = (
    ("%Y-%m-%d %H:%M", True, True),
    ("%Y-%m-%d %I:%M%p", True, True),
    ("%Y-%m-%d %I%p", True, True),
    ("%m/%d %H:%M", False, True),
    ("%m/%d %I:%M%p", False, True),
    ("%m/%d %I%p", False, True),
    ("%b %d %H:%M", False, True),
    ("%b %d %I:%M%p", False, True),
    ("%b %d %I%p", False, True),
    ("%B %d %H:%M", False, True),
    ("%B %d %I:%M%p", False, True),
    ("%B %d %I%p", False, True),
)


def _parse_absolute(s: str, now: datetime) -> datetime | None:
    """Parse an explicit date/time. Year defaults to the current year (rolling
    forward if already past); a bare clock time resolves to the next occurrence."""
    # Glue a bare am/pm onto the preceding number ("2 pm" -> "2pm") so the
    # %I%p formats match, without disturbing other spacing ("jul 1 2pm").
    norm = re.sub(r"(\d)\s*([ap]m)\b", r"\1\2", s)
    for fmt, has_year, _has_date in _ABS_FORMATS:
        try:
            parsed = datetime.strptime(norm, fmt)
        except ValueError:
            continue
        year = parsed.year if has_year else now.year
        dt = datetime(year, parsed.month, parsed.day, parsed.hour, parsed.minute, tzinfo=now.tzinfo)
        if not has_year and dt < now:
            dt = dt.replace(year=year + 1)
        return dt
    # Bare clock time → today, or tomorrow if already past.
    t = _parse_time_of_day(s)
    if t is not None:
        dt = datetime.combine(now.date(), t, tzinfo=now.tzinfo)
        if dt <= now:
            dt += timedelta(days=1)
        return dt
    return None


def parse_when(text: str, now: datetime) -> datetime:
    """Parse a user 'when' string into an aware Central datetime.

    `now` must be an aware Central datetime. Raises ValueError on anything
    unparseable. Supports relative durations, bare clock times, today/tonight/
    tomorrow prefixes, and explicit dates. See WHEN_EXAMPLES.
    """
    s = text.strip().lower()
    if not s:
        raise ValueError("Please enter a time.")
    if s.startswith("in "):
        s = s[3:].strip()

    delta = _parse_relative(s)
    if delta is not None:
        return now + delta

    m = re.match(r"^(today|tonight|tomorrow)\b(.*)$", s)
    if m:
        keyword, rest = m.group(1), m.group(2).strip()
        if keyword == "tomorrow":
            day = now.date() + timedelta(days=1)
            rest = rest or "9am"
        else:  # today / tonight
            day = now.date()
            if keyword == "tonight":
                rest = rest or "8pm"
            else:
                rest = rest or "9am"
        rest = rest.lstrip("@ ").strip()
        if rest.startswith("at "):
            rest = rest[3:].strip()
        t = _parse_time_of_day(rest)
        if t is None:
            raise ValueError(f"Couldn't read the time '{rest}'.")
        return datetime.combine(day, t, tzinfo=now.tzinfo)

    dt = _parse_absolute(s, now)
    if dt is not None:
        return dt
    raise ValueError(f"Couldn't understand '{text}'.")


# ---------------------------------------------------------------------------
# SCHEDULING (pure)
# ---------------------------------------------------------------------------
def _add_month(dt: datetime) -> datetime:
    """Advance one calendar month, clamping the day to the new month's length."""
    month = dt.month + 1
    year = dt.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    # Clamp day (e.g. Jan 31 -> Feb 28).
    for day in (dt.day, 30, 29, 28):
        try:
            return dt.replace(year=year, month=month, day=day)
        except ValueError:
            continue
    return dt.replace(year=year, month=month, day=28)


def _step_once(dt: datetime, kind: str, interval: int | None, unit: str | None) -> datetime:
    if kind == "daily":
        return dt + timedelta(days=1)
    if kind == "weekly":
        return dt + timedelta(weeks=1)
    if kind == "weekdays":
        nxt = dt + timedelta(days=1)
        while nxt.weekday() >= 5:  # Sat=5, Sun=6
            nxt += timedelta(days=1)
        return nxt
    if kind == "monthly":
        return _add_month(dt)
    if kind == "interval":
        n = max(1, int(interval or 1))
        if unit in _HOUR_UNITS or unit == "hours":
            return dt + timedelta(hours=n)
        return dt + timedelta(days=n)
    raise ValueError(f"Unknown repeat kind: {kind}")


def compute_next_fire(current: datetime, kind: str, interval: int | None, unit: str | None, now: datetime) -> datetime | None:
    """Next fire time after `current` for a repeating reminder, advanced past
    `now` so a long downtime doesn't cause a burst of catch-up firings. Returns
    None for one-off ('none') reminders."""
    if kind == "none" or not kind:
        return None
    nxt = _step_once(current, kind, interval, unit)
    guard = 0
    while nxt <= now and guard < 100000:
        nxt = _step_once(nxt, kind, interval, unit)
        guard += 1
    return nxt


def parse_interval(text: str) -> tuple[int, str]:
    """Parse a custom repeat interval like '2 days' / '6h' into (n, unit) where
    unit is 'hours' or 'days'. Raises ValueError if unrecognized."""
    s = text.strip().lower()
    m = re.match(r"^(?:every\s+)?(\d+)\s*([a-zA-Z]+)$", s)
    if not m:
        raise ValueError("Use a format like '2 days' or '6 hours'.")
    n = int(m.group(1))
    u = m.group(2)
    if n < 1:
        raise ValueError("Interval must be at least 1.")
    if u in _HOUR_UNITS:
        return n, "hours"
    if u in _DAY_UNITS:
        return n, "days"
    raise ValueError("Interval unit must be hours or days.")


# ---------------------------------------------------------------------------
# TIME <-> DB HELPERS
# ---------------------------------------------------------------------------
def _to_utc_iso(central_dt: datetime) -> str:
    return central_dt.astimezone(timezone.utc).isoformat()


def _from_utc_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CENTRAL_TZ)


def _format_when_safe(central_dt: datetime) -> str:
    # %-d / %-I aren't portable (Windows); format then strip leading zeros.
    day = central_dt.day
    hour12 = central_dt.strftime("%I").lstrip("0") or "12"
    return f"{central_dt:%a %b} {day}, {central_dt:%Y} at {hour12}:{central_dt:%M %p %Z}"


# ---------------------------------------------------------------------------
# DB OPS
# ---------------------------------------------------------------------------
def create_reminder(
    user_id: int,
    channel_id: int | None,
    guild_id: int | None,
    text: str,
    next_fire_central: datetime,
    repeat_kind: str = "none",
    repeat_interval: int | None = None,
    repeat_unit: str | None = None,
    destination: str = "channel",
) -> int:
    now_iso = datetime.now(timezone.utc).isoformat()
    with shared.db:
        cur = shared.db.execute(
            "INSERT INTO reminders (user_id, channel_id, guild_id, text, next_fire_at, "
            "repeat_kind, repeat_interval, repeat_unit, destination, created_at, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (
                user_id,
                channel_id,
                guild_id,
                text,
                _to_utc_iso(next_fire_central),
                repeat_kind,
                repeat_interval,
                repeat_unit,
                destination,
                now_iso,
            ),
        )
        return int(cur.lastrowid)


def _row_to_dict(r) -> dict:
    return {
        "id": int(r[0]),
        "user_id": int(r[1]),
        "channel_id": int(r[2]) if r[2] is not None else None,
        "guild_id": int(r[3]) if r[3] is not None else None,
        "text": r[4],
        "next_fire_at": r[5],
        "repeat_kind": r[6] or "none",
        "repeat_interval": int(r[7]) if r[7] is not None else None,
        "repeat_unit": r[8],
        "destination": r[9] or "channel",
    }


_SELECT_COLS = "id, user_id, channel_id, guild_id, text, next_fire_at, repeat_kind, repeat_interval, repeat_unit, destination"


def get_due_reminders(now_utc: datetime) -> list[dict]:
    rows = shared.db.execute(
        f"SELECT {_SELECT_COLS} FROM reminders WHERE active = 1 AND next_fire_at <= ? ORDER BY next_fire_at",
        (now_utc.astimezone(timezone.utc).isoformat(),),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_user_reminders(user_id: int) -> list[dict]:
    rows = shared.db.execute(
        f"SELECT {_SELECT_COLS} FROM reminders WHERE active = 1 AND user_id = ? ORDER BY next_fire_at",
        (user_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_active(user_id: int) -> int:
    row = shared.db.execute("SELECT COUNT(*) FROM reminders WHERE active = 1 AND user_id = ?", (user_id,)).fetchone()
    return int(row[0]) if row else 0


def deactivate_reminder(reminder_id: int, user_id: int | None = None) -> bool:
    """Mark a reminder inactive. If user_id is given, only that user's reminder
    is affected (returns False if it didn't match). Returns True if a row changed."""
    if user_id is None:
        cur = shared.db.execute("UPDATE reminders SET active = 0 WHERE id = ? AND active = 1", (reminder_id,))
    else:
        cur = shared.db.execute(
            "UPDATE reminders SET active = 0 WHERE id = ? AND user_id = ? AND active = 1",
            (reminder_id, user_id),
        )
    shared.db.commit()
    return cur.rowcount > 0


def reschedule_reminder(reminder_id: int, next_fire_central: datetime) -> None:
    shared.db.execute(
        "UPDATE reminders SET next_fire_at = ? WHERE id = ?",
        (_to_utc_iso(next_fire_central), reminder_id),
    )
    shared.db.commit()


def describe_repeat(kind: str, interval: int | None, unit: str | None) -> str:
    if kind == "interval" and interval:
        return f"Every {interval} {unit}"
    return REPEAT_LABELS.get(kind, "Does not repeat")


# ---------------------------------------------------------------------------
# INTERACTIVE CARD
# ---------------------------------------------------------------------------
class _CustomTimeModal(discord.ui.Modal, title="Custom reminder time"):
    def __init__(self, view: "ReminderConfigView"):
        super().__init__()
        self._view = view
        self.when_input = discord.ui.TextInput(
            label="When?",
            placeholder="e.g. 90m · tomorrow 9am · 7/1 2pm · 2026-07-01 14:30",
            required=True,
            max_length=100,
        )
        self.add_item(self.when_input)

    async def on_submit(self, interaction: discord.Interaction):
        now = datetime.now(CENTRAL_TZ)
        try:
            when = parse_when(str(self.when_input.value), now)
        except ValueError as exc:
            return await interaction.response.send_message(f"⚠️ {exc}\n\n**Formats:**\n{WHEN_EXAMPLES}", ephemeral=True)
        if when <= now:
            return await interaction.response.send_message("⚠️ That time is in the past.", ephemeral=True)
        self._view.when = when
        await interaction.response.edit_message(embed=self._view.render_embed(), view=self._view)


class _IntervalModal(discord.ui.Modal, title="Custom repeat interval"):
    def __init__(self, view: "ReminderConfigView"):
        super().__init__()
        self._view = view
        self.interval_input = discord.ui.TextInput(
            label="Repeat every…",
            placeholder="e.g. 2 days · 6 hours",
            required=True,
            max_length=30,
        )
        self.add_item(self.interval_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n, unit = parse_interval(str(self.interval_input.value))
        except ValueError as exc:
            return await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
        self._view.repeat_kind = "interval"
        self._view.repeat_interval = n
        self._view.repeat_unit = unit
        await interaction.response.edit_message(embed=self._view.render_embed(), view=self._view)


class ReminderConfigView(discord.ui.View):
    def __init__(self, cog: "RemindersCog", author_id: int, text: str, channel_id: int | None, guild_id: int | None):
        super().__init__(timeout=300)
        self.cog = cog
        self.author_id = author_id
        self.text = text
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.when: datetime | None = None
        self.repeat_kind = "none"
        self.repeat_interval: int | None = None
        self.repeat_unit: str | None = None
        self.destination = "channel"
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your reminder card.", ephemeral=True)
            return False
        return True

    def render_embed(self) -> discord.Embed:
        when_str = _format_when_safe(self.when) if self.when else "*not set — pick a time*"
        dest_str = "📣 This channel" if self.destination == "channel" else "✉️ Direct message"
        embed = make_embed(
            "⏰ New Reminder",
            f"**Reminder:** {self.text}\n\n"
            f"**When:** {when_str}\n"
            f"**Repeat:** {describe_repeat(self.repeat_kind, self.repeat_interval, self.repeat_unit)}\n"
            f"**Deliver to:** {dest_str}",
            COLOR_DEFAULT,
        )
        embed.set_footer(text="Pick a time, then press Create. Card expires in 5 minutes.")
        return embed

    async def _set_when_relative(self, interaction: discord.Interaction, delta: timedelta):
        self.when = datetime.now(CENTRAL_TZ) + delta
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    async def _set_when_clock(self, interaction: discord.Interaction, spec: str):
        self.when = parse_when(spec, datetime.now(CENTRAL_TZ))
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    @discord.ui.button(label="+1h", style=discord.ButtonStyle.secondary, row=0)
    async def preset_1h(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_when_relative(interaction, timedelta(hours=1))

    @discord.ui.button(label="+3h", style=discord.ButtonStyle.secondary, row=0)
    async def preset_3h(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_when_relative(interaction, timedelta(hours=3))

    @discord.ui.button(label="Tonight 8pm", style=discord.ButtonStyle.secondary, row=0)
    async def preset_tonight(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_when_clock(interaction, "tonight")

    @discord.ui.button(label="Tomorrow 9am", style=discord.ButtonStyle.secondary, row=0)
    async def preset_tomorrow(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_when_clock(interaction, "tomorrow 9am")

    @discord.ui.button(label="Custom…", style=discord.ButtonStyle.primary, row=0)
    async def preset_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_CustomTimeModal(self))

    @discord.ui.select(
        placeholder="Repeat… (default: does not repeat)",
        row=1,
        options=[
            discord.SelectOption(label="Does not repeat", value="none", default=True),
            discord.SelectOption(label="Every day", value="daily"),
            discord.SelectOption(label="Every week", value="weekly"),
            discord.SelectOption(label="Every month", value="monthly"),
            discord.SelectOption(label="Every weekday (Mon–Fri)", value="weekdays"),
            discord.SelectOption(label="Custom interval…", value="interval"),
        ],
    )
    async def repeat_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        choice = select.values[0]
        if choice == "interval":
            return await interaction.response.send_modal(_IntervalModal(self))
        self.repeat_kind = choice
        self.repeat_interval = None
        self.repeat_unit = None
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    @discord.ui.button(label="📣 Here", style=discord.ButtonStyle.secondary, row=2)
    async def dest_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.destination = "channel"
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    @discord.ui.button(label="✉️ DM me", style=discord.ButtonStyle.secondary, row=2)
    async def dest_dm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.destination = "dm"
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    @discord.ui.button(label="Create", style=discord.ButtonStyle.success, row=3)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.when is None:
            return await interaction.response.send_message("⚠️ Pick a time first.", ephemeral=True)
        if self.when <= datetime.now(CENTRAL_TZ):
            return await interaction.response.send_message("⚠️ That time is in the past — pick another.", ephemeral=True)
        create_reminder(
            user_id=self.author_id,
            channel_id=self.channel_id,
            guild_id=self.guild_id,
            text=self.text,
            next_fire_central=self.when,
            repeat_kind=self.repeat_kind,
            repeat_interval=self.repeat_interval,
            repeat_unit=self.repeat_unit,
            destination=self.destination,
        )
        dest_str = "in this channel" if self.destination == "channel" else "via DM"
        repeat_str = describe_repeat(self.repeat_kind, self.repeat_interval, self.repeat_unit)
        embed = make_embed(
            "✅ Reminder Set",
            f"I'll remind you {dest_str} about:\n**{self.text}**\n\n"
            f"**First:** {_format_when_safe(self.when)}\n**Repeat:** {repeat_str}",
            COLOR_SUCCESS,
        )
        self.stop()
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, row=3)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            embed=make_embed("Reminder Cancelled", "No reminder was created.", COLOR_ERROR), view=None
        )

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=make_embed("Reminder Card Expired", "The card timed out. Run the command again.", COLOR_ERROR),
                    view=None,
                )
            except discord.HTTPException:
                pass


# ---------------------------------------------------------------------------
# COG
# ---------------------------------------------------------------------------
class RemindersCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener("on_ready")
    async def _start_reminder_task(self):
        if not self.reminder_check.is_running():
            self.reminder_check.start()

    def cog_unload(self):
        self.reminder_check.cancel()

    @commands.command(name="remindme", aliases=["remind", "reminder"])
    async def remindme(self, ctx, *, text: str = None):
        """Open a card to schedule a reminder: `.remindme "buy milk"`."""
        if not text or not text.strip():
            return await ctx.send(f'Usage: `{PREFIX}remindme "what to be reminded about"`')
        text = text.strip().strip('"').strip()
        if not text:
            return await ctx.send(f'Usage: `{PREFIX}remindme "what to be reminded about"`')
        if len(text) > MAX_TEXT_LEN:
            return await ctx.send(f"That reminder is too long (max {MAX_TEXT_LEN} characters).")
        if count_active(ctx.author.id) >= MAX_ACTIVE_REMINDERS:
            return await ctx.send(
                f"You already have {MAX_ACTIVE_REMINDERS} active reminders. Use `{PREFIX}reminders` to cancel some first."
            )
        guild_id = ctx.guild.id if ctx.guild else None
        view = ReminderConfigView(self, ctx.author.id, text, ctx.channel.id, guild_id)
        view.message = await ctx.send(embed=view.render_embed(), view=view)

    @commands.command(name="reminders", aliases=["myreminders", "remindlist"])
    async def reminders(self, ctx, action: str = None, reminder_id: int = None):
        """List your active reminders, or cancel one: `.reminders cancel <id>`."""
        if action and action.lower() in {"cancel", "delete", "remove", "rm"}:
            if reminder_id is None:
                return await ctx.send(f"Usage: `{PREFIX}reminders cancel <id>`")
            ok = deactivate_reminder(reminder_id, user_id=ctx.author.id)
            if ok:
                return await ctx.send(f"✅ Cancelled reminder `#{reminder_id}`.")
            return await ctx.send(f"No active reminder `#{reminder_id}` of yours found.")

        items = get_user_reminders(ctx.author.id)
        if not items:
            return await ctx.send(f'You have no active reminders. Make one with `{PREFIX}remindme "..."`.')
        lines = []
        for r in items:
            when = _format_when_safe(_from_utc_iso(r["next_fire_at"]))
            repeat = describe_repeat(r["repeat_kind"], r["repeat_interval"], r["repeat_unit"])
            dest = "DM" if r["destination"] == "dm" else "channel"
            text = r["text"] if len(r["text"]) <= 80 else r["text"][:77] + "..."
            lines.append(f"`#{r['id']}` **{text}**\n  ⏰ {when} · 🔁 {repeat} · 📍 {dest}")
        embed = make_embed("⏰ Your Reminders", "\n\n".join(lines), COLOR_DEFAULT)
        embed.set_footer(text=f"Cancel one with {PREFIX}reminders cancel <id>")
        await ctx.send(embed=embed)

    # -----------------------------------------------------------------------
    # BACKGROUND FIRING
    # -----------------------------------------------------------------------
    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def reminder_check(self):
        now_utc = datetime.now(timezone.utc)
        try:
            due = get_due_reminders(now_utc)
        except Exception:
            logger.exception("reminder_check failed to query due reminders")
            return
        for r in due:
            try:
                await self._deliver(r)
            except discord.HTTPException:
                logger.warning("Failed to deliver reminder %s", r["id"])
            except Exception:
                logger.exception("Unexpected error delivering reminder %s", r["id"])
            # Reschedule repeating, otherwise retire it. Done regardless of
            # delivery outcome so a broken target can't wedge the loop.
            nxt = compute_next_fire(
                _from_utc_iso(r["next_fire_at"]),
                r["repeat_kind"],
                r["repeat_interval"],
                r["repeat_unit"],
                datetime.now(CENTRAL_TZ),
            )
            if nxt is None:
                deactivate_reminder(r["id"])
            else:
                reschedule_reminder(r["id"], nxt)

    @reminder_check.before_loop
    async def _before_reminder_check(self):
        await self.bot.wait_until_ready()

    async def _deliver(self, r: dict):
        repeat = describe_repeat(r["repeat_kind"], r["repeat_interval"], r["repeat_unit"])
        embed = make_embed("⏰ Reminder", r["text"], COLOR_DEFAULT)
        if r["repeat_kind"] != "none":
            embed.set_footer(text=f"🔁 {repeat} · {PREFIX}reminders cancel {r['id']} to stop")
        if r["destination"] == "dm":
            user = self.bot.get_user(r["user_id"]) or await self.bot.fetch_user(r["user_id"])
            await user.send(content=f"<@{r['user_id']}>", embed=embed)
            return
        channel = self.bot.get_channel(r["channel_id"])
        if channel is None and r["channel_id"] is not None:
            channel = await self.bot.fetch_channel(r["channel_id"])
        if channel is not None:
            await channel.send(content=f"<@{r['user_id']}>", embed=embed)


async def setup(bot):
    await bot.add_cog(RemindersCog(bot))
