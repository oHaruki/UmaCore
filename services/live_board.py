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
from typing import List, Optional

import discord

from models import Club
from scrapers.umamoe_api_scraper import LiveSnapshot, UmaMoeAPIScraper
from utils.jst_calendar import resolve_live
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
    the report's on-track/behind split. A circle holds ~30 members, so the whole
    roster fits comfortably inside one message's 6000-character budget.
    """
    if closed:
        title = f"📗 Live Board — {club.club_name}"
        colour = COLOR_ON_TRACK
        blurb = "This competition day has **closed**. Numbers are final."
    else:
        title = f"📈 Live Board — {club.club_name}"
        colour = COLOR_INFO
        blurb = "Updating through the day — **not final** until the day closes."

    stamp = snap.as_of.strftime("%H:%M UTC") if snap.as_of else "unknown"
    raced = [g for g in snap.gains if g.gained_today > 0]
    idle = [g for g in snap.gains if g.gained_today <= 0]
    raced.sort(key=lambda g: g.gained_today, reverse=True)
    idle.sort(key=lambda g: g.month_total, reverse=True)

    # ---- summary -------------------------------------------------------------
    summary = discord.Embed(
        title=title,
        description=f"**Day:** {snap.jst_day:%B %d} (JST)\n{blurb}",
        colour=colour,
        timestamp=datetime.now(timezone.utc),
    )
    summary.add_field(name="Fans today", value=f"**{_fmt(snap.gained_today)}**", inline=True)

    if snap.live_rank:
        delta = snap.rank_delta
        move = f" ({delta:+d})" if delta else ""
        summary.add_field(name="Rank", value=f"#{snap.live_rank:,}{move}", inline=True)

    if snap.live_points:
        summary.add_field(name="Month total", value=_fmt(snap.live_points), inline=True)

    summary.add_field(
        name="📈 Summary",
        value=(f"**Total Members:** {len(snap.gains)}\n"
               f"🏇 Raced today: {len(raced)}\n"
               f"💤 Not yet: {len(idle)}"),
        inline=False,
    )
    summary.set_footer(text=f"Umamusume Quota Tracker - {club.club_name} · uma.moe as of {stamp}")
    embeds = [summary]

    # ---- everyone who raced --------------------------------------------------
    if raced:
        lines = [
            f"`{i:>2}` **{g.name}**: +{_fmt(g.gained_today)} ({_fmt(g.month_total)} total)"
            for i, g in enumerate(raced, 1)
        ]
        for idx, section in enumerate(_split(lines)):
            embeds.append(discord.Embed(
                title="🏇 Raced Today" if idx == 0 else f"🏇 Raced Today (continued {idx + 1})",
                description=section,
                colour=COLOR_ON_TRACK,
            ))

    # ---- everyone who hasn't -------------------------------------------------
    if idle:
        lines = [f"**{g.name}**: {_fmt(g.month_total)} this month" for g in idle]
        for idx, section in enumerate(_split(lines)):
            embeds.append(discord.Embed(
                title="💤 No Fans Yet Today" if idx == 0
                      else f"💤 No Fans Yet Today (continued {idx + 1})",
                description=section,
                colour=COLOR_BEHIND,
            ))

    if not snap.gains:
        embeds.append(discord.Embed(
            title="🏇 Raced Today",
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
        logger.info(f"📈 Live board opened for {club.club_name} (JST {snap.jst_day})")
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


async def update_club(bot, club: Club, *, now_utc: Optional[datetime] = None) -> bool:
    """Bring one club's board up to date. Returns True if anything was sent.

    Handles the three cases: no board yet, same day (edit), and the day having
    rolled over (final edit, then open a new one).

    A single fetch covers the rollover: after 15:00 UTC the response carries both
    the finalized total for the day that just closed and the opening figures for
    the new one.
    """
    target = resolve_live(now_utc)
    snap = await UmaMoeAPIScraper(club.circle_id, now_utc=now_utc).fetch_live()
    if snap is None:
        return False

    # First run, or the tracked message vanished.
    if not club.live_board_message_id or not club.live_board_day:
        return await _post_new(bot, club, snap)

    if club.live_board_day == target.jst_day:
        ok = await _edit_existing(bot, club, snap, closed=False)
        return ok if ok else await _post_new(bot, club, snap)

    # The day rolled over. Close out the old message with the finalized figures,
    # then open a new board for the day now in progress.
    closing = LiveSnapshot(
        circle_id=snap.circle_id,
        jst_day=club.live_board_day,
        as_of=snap.as_of,
        # For the closed day, the finalized month total is the yardstick and the
        # day's own gain is monthly_point - yesterday_points.
        live_points=snap.monthly_point,
        live_rank=snap.monthly_rank,
        monthly_point=snap.yesterday_points,
        monthly_rank=snap.monthly_rank,
        gains=[],           # per-member deltas describe the *new* day, not this one
    )
    await _edit_existing(bot, club, closing, closed=True)
    logger.info(f"📗 Live board closed for {club.club_name} (JST {club.live_board_day})")
    return await _post_new(bot, club, snap)
