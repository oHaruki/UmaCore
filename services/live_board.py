"""The live board: one self-editing message per competition day, per club.

Aligned to uma.moe's own clock rather than the club's report time. A competition
day opens at 15:00 UTC, the board is posted then, edited as live figures arrive,
and gets one final edit with the finalized total once the day closes — so the
message left behind is an exact record of that day. Then a new one is posted.

**Display only.** Nothing here writes quota history, activates bombs or sends
DMs. Those belong to the daily report, which runs on the club's own schedule off
finalized data — counting an unfinished day would penalise people for a day that
has not happened yet.

Opt-in: a club is polled only once an admin sets a channel, so enabling this for
one club costs 24 API calls a day and enabling it for none costs nothing.
"""
import logging
from datetime import date, datetime, timezone
from typing import List, Optional, Tuple

import discord

from models import Club
from scrapers.umamoe_api_scraper import LiveSnapshot, MemberGain, UmaMoeAPIScraper
from utils.jst_calendar import competition_day, resolve_live
from config.settings import COLOR_INFO, COLOR_ON_TRACK, COLOR_BEHIND

logger = logging.getLogger(__name__)

TOP_N = 10


def _fmt(n: Optional[int]) -> str:
    if n is None:
        return "—"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,}"


def _span(gain: MemberGain, comp: date, *, idle: bool = False) -> str:
    """What period a member's running total actually covers.

    Everyone present at the month's baseline is measured from it, so "total" is
    accurate for them. A mid-month joiner is measured from the day they arrived,
    which makes their figure look small beside a full month's — say which day it
    starts from rather than leave the two looking comparable.
    """
    slot = gain.joined_slot
    if not 1 <= slot < comp.day:
        return "this month" if idle else "total"
    since = comp.replace(day=slot)
    return f"since {since:%b} {since.day}"


def _split(lines: List[str], max_length: int = 1900) -> List[str]:
    """Chunk lines into blocks that fit an embed description.

    Chunks larger than the daily report's 1000 because these embeds share one
    message rather than being sent separately: a ~30-member roster then lands in
    a single block instead of being split across two, and the whole board still
    sits far inside the 6000-character per-message budget.
    """
    sections, current, length = [], [], 0
    for line in lines:
        if length + len(line) + 1 > max_length and current:
            sections.append("\n".join(current))
            current, length = [line], len(line) + 1
        else:
            current.append(line)
            length += len(line) + 1
    if current:
        sections.append("\n".join(current))
    return sections


def build_embeds(club: Club, snap: LiveSnapshot, *, closed: bool = False) -> List[discord.Embed]:
    """Render the board, mirroring the daily report's layout.

    Returns a list because a Discord message can carry up to 10 embeds — the same
    shape the daily report uses, except those are separate messages and these all
    live in the one message that gets edited.

    Every member is listed, split into "raced today" and "not yet", which parallels
    the report's on-track/behind split, plus "joined today" for members whose first
    day cannot be measured. A circle holds ~30 members, so the whole roster fits
    comfortably inside one message's 6000-character budget.
    """
    # Emoji use deliberately mirrors create_daily_report: one conventional marker
    # per section header and none in the body. The live/closed distinction is
    # carried by the colour and the wording, not by a different icon.
    title = f"📊 Live Board - {club.club_name}"
    if closed:
        colour = COLOR_ON_TRACK
        blurb = ("This competition day has **closed**. Numbers are final.\n"
                 "The next day's board appears once Uma.moe publishes it — "
                 "usually within an hour or so.")
    else:
        colour = COLOR_INFO
        blurb = "Updating through the day — **not final** until the day closes."

    stamp = snap.as_of.strftime("%H:%M UTC") if snap.as_of else "unknown"
    # Members on their first day are pulled out before the raced/idle split. Their
    # slot mixes fans earned before and after they arrived, so both their figures
    # come out as 0 — which would otherwise park them among people who genuinely
    # haven't raced and read as a reprimand on the day they joined.
    fresh = [g for g in snap.gains if g.is_new]
    counted = [g for g in snap.gains if not g.is_new]
    raced = [g for g in counted if g.gained_today > 0]
    idle = [g for g in counted if g.gained_today <= 0]
    raced.sort(key=lambda g: g.gained_today, reverse=True)
    idle.sort(key=lambda g: g.month_total, reverse=True)
    fresh.sort(key=lambda g: g.name.lower())

    comp = competition_day(snap.jst_day)

    # ---- summary -------------------------------------------------------------
    summary = discord.Embed(
        title=title,
        description=f"**Competition day:** {comp:%B %d}\n{blurb}",
        colour=colour,
        timestamp=datetime.now(timezone.utc),
    )
    summary.add_field(name="Fans today", value=f"**{_fmt(snap.gained_today)}**", inline=True)

    if snap.live_rank:
        delta = snap.rank_delta
        move = f" ({delta:+d})" if delta else ""
        summary.add_field(name="Rank", value=f"#{snap.live_rank:,}{move}", inline=True)
    else:
        # Uma.moe serves no live_rank until it starts publishing live data for a
        # newly opened competition month. Say so rather than silently dropping the
        # field, and don't substitute monthly_rank — measured across the 2026-08
        # rollover, the circle-level rank and point fields were still shifting by
        # hundreds of millions between reads.
        summary.add_field(name="Rank", value="— *not published yet*", inline=True)

    if snap.live_points:
        summary.add_field(name="Month total", value=_fmt(snap.live_points), inline=True)

    tally = (f"**Total Members:** {len(snap.gains)}\n"
             f"Raced today: {len(raced)}\n"
             f"Not yet: {len(idle)}")
    if fresh:
        tally += f"\nJoined today: {len(fresh)} (not counted)"
    summary.add_field(name="📈 Summary", value=tally, inline=False)
    summary.set_footer(text=f"Umamusume Quota Tracker - {club.club_name} · uma.moe as of {stamp}")
    embeds = [summary]

    # ---- everyone who raced --------------------------------------------------
    if raced:
        lines = [
            f"`{i:>2}` **{g.name}**: +{_fmt(g.gained_today)} ({_fmt(g.month_total)} {_span(g, comp)})"
            for i, g in enumerate(raced, 1)
        ]
        for idx, section in enumerate(_split(lines)):
            embeds.append(discord.Embed(
                title="✅ Raced Today" if idx == 0 else f"✅ Raced Today (continued {idx + 1})",
                description=section,
                colour=COLOR_ON_TRACK,
            ))

    # ---- everyone who hasn't -------------------------------------------------
    if idle:
        lines = [f"**{g.name}**: {_fmt(g.month_total)} {_span(g, comp, idle=True)}" for g in idle]
        for idx, section in enumerate(_split(lines)):
            embeds.append(discord.Embed(
                title="⚠️ No Fans Yet Today" if idx == 0
                      else f"⚠️ No Fans Yet Today (continued {idx + 1})",
                description=section,
                colour=COLOR_BEHIND,
            ))

    # ---- everyone who arrived today ------------------------------------------
    if fresh:
        lines = [f"**{g.name}**" for g in fresh]
        for idx, section in enumerate(_split(lines)):
            embeds.append(discord.Embed(
                title="🆕 Joined Today" if idx == 0
                      else f"🆕 Joined Today (continued {idx + 1})",
                description=(
                    f"{section}\n\n_Not counted today._ Uma.moe records a whole day "
                    f"per member and nothing about the moment they joined, so their "
                    f"first day mixes fans earned before and after arriving. "
                    f"They start counting tomorrow."
                ) if idx == 0 else section,
                colour=COLOR_INFO,
            ))

    if not snap.gains:
        embeds.append(discord.Embed(
            title="✅ Raced Today",
            description="_nobody has raced yet today_",
            colour=COLOR_BEHIND,
        ))

    return embeds[:10]          # Discord allows at most 10 embeds per message


def build_embed(club: Club, snap: LiveSnapshot, *, closed: bool = False) -> discord.Embed:
    """The summary embed alone. Kept for callers that want a single embed."""
    return build_embeds(club, snap, closed=closed)[0]


async def _resolve_channel(bot, club: Club) -> Optional[discord.abc.Messageable]:
    channel = bot.get_channel(club.live_board_channel_id)
    if channel is None:
        logger.warning(
            f"Live board channel {club.live_board_channel_id} not found for "
            f"{club.club_name} — leaving it configured in case it is a cache miss"
        )
    return channel


async def _post_new(bot, club: Club, snap: LiveSnapshot) -> bool:
    channel = await _resolve_channel(bot, club)
    if channel is None:
        return False
    try:
        msg = await channel.send(embeds=build_embeds(club, snap))
        await club.set_live_board_message(msg.id, snap.jst_day)
        logger.info(f"Live board opened for {club.club_name} (JST {snap.jst_day})")
        return True
    except discord.Forbidden:
        logger.error(
            f"No permission to post the live board for {club.club_name} in "
            f"channel {club.live_board_channel_id}"
        )
    except discord.HTTPException as e:
        logger.warning(f"Failed to post live board for {club.club_name}: {e}")
    return False


async def _edit_existing(bot, club: Club, snap: LiveSnapshot, *, closed: bool) -> bool:
    """Edit the tracked message. Returns False if it is gone and must be reposted."""
    channel = await _resolve_channel(bot, club)
    if channel is None:
        return True                      # cache miss: don't repost, just skip
    try:
        msg = await channel.fetch_message(club.live_board_message_id)
        await msg.edit(embeds=build_embeds(club, snap, closed=closed))
        return True
    except discord.NotFound:
        logger.info(f"Live board message for {club.club_name} was deleted — reposting")
        return False
    except discord.Forbidden:
        logger.error(f"No permission to edit the live board for {club.club_name}")
        return True
    except discord.HTTPException as e:
        logger.warning(f"Failed to edit live board for {club.club_name}: {e}")
        return True


async def update_club(bot, club: Club, *, now_utc: Optional[datetime] = None) -> str:
    """Bring one club's board up to date. See :func:`refresh` for the detail."""
    status, _ = await refresh(bot, club, now_utc=now_utc)
    return status


async def refresh(bot, club: Club, *,
                  now_utc: Optional[datetime] = None) -> Tuple[str, Optional[LiveSnapshot]]:
    """Bring one club's board up to date.

    Handles the three cases: no board yet, same day (edit), and the day having
    rolled over (final edit, then open a new one).

    A single fetch covers the rollover: after 15:00 UTC the response carries both
    the finalized total for the day that just closed and the opening figures for
    the new one.

    Returns one of:
        ``"posted"``   a new board was opened
        ``"edited"``   the existing board was updated in place
        ``"no_data"``  uma.moe has nothing for this day yet; board left untouched
        ``"failed"``   the message could not be sent or edited

    Paired with the snapshot it read, so a caller wanting the same figures for
    something else — the channel-name updater does — reuses this fetch instead of
    making a second identical call a moment later.
    """
    target = resolve_live(now_utc)

    # The tracked board's day has ended. Close it out FIRST and independently of
    # whether the new day has any data yet, so the archived message is a complete
    # record and never sits there claiming to still be updating.
    if (club.live_board_message_id and club.live_board_day
            and club.live_board_day != target.jst_day):
        if not await _close_out(bot, club):
            # Couldn't reach that day's final numbers. Leave the board exactly as
            # it is and retry next cycle rather than abandoning it half-finished.
            return "no_data", None

    snap = await UmaMoeAPIScraper(club.circle_id, now_utc=now_utc).fetch_live()
    if snap is None:
        return "no_data", None

    if not club.live_board_message_id or not club.live_board_day:
        return ("posted" if await _post_new(bot, club, snap) else "failed"), snap

    if await _edit_existing(bot, club, snap, closed=False):
        return "edited", snap
    return ("posted" if await _post_new(bot, club, snap) else "failed"), snap


def _midday_utc(jst_day: date) -> datetime:
    """A UTC moment that falls inside the given JST day.

    JST day D runs (D-1) 15:00 UTC -> D 15:00 UTC, so 03:00 UTC on D is always
    within it. Used to resolve a past day's slot without duplicating the mapping.
    """
    return datetime(jst_day.year, jst_day.month, jst_day.day, 3, 0, tzinfo=timezone.utc)


async def _close_out(bot, club: Club) -> bool:
    """Finalise the tracked board and release it, so the next run opens a new one.

    Fetches the closing day's *own* slot rather than reusing the current one: the
    live fetch describes the day now in progress, and reusing it left the archived
    message with an empty roster — 'Total Members: 0' on a day that had 29 people
    racing. The archived board is meant to be that day's record, so it has to carry
    that day's numbers.
    """
    closing_day = club.live_board_day
    finished = await UmaMoeAPIScraper(
        club.circle_id, now_utc=_midday_utc(closing_day)
    ).fetch_live()

    if finished is None:
        logger.warning(
            f"Could not fetch final figures for {club.club_name} JST {closing_day}; "
            f"leaving the board untouched and retrying next cycle"
        )
        return False

    closing = LiveSnapshot(
        circle_id=finished.circle_id,
        jst_day=closing_day,
        as_of=finished.as_of,
        # The day has closed, so its slot now holds the finalized totals and its
        # gains are final. monthly_point/yesterday_points bracket the day.
        live_points=finished.monthly_point,
        live_rank=finished.monthly_rank,
        monthly_point=finished.yesterday_points,
        monthly_rank=finished.monthly_rank,
        gains=finished.gains,
    )
    await _edit_existing(bot, club, closing, closed=True)
    logger.info(
        f"Live board closed for {club.club_name} "
        f"(JST {closing_day}, {len(finished.gains)} members recorded)"
    )

    # Release it: the message belongs to a finished day, so the next successful
    # fetch opens a fresh board rather than overwriting the archive.
    await club.set_live_board_message(None, None)
    return True
