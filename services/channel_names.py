"""Channel names that track a club's figures.

Renames a Discord channel — normally a locked voice channel used as a display
board — to show a club's rank or fan total, so nobody has to keep editing it by
hand.

Fed by both existing update paths, from data already in hand:

* the **hourly live tick**, off the same ``LiveSnapshot`` the live board renders,
  so a club running the board pays no extra uma.moe calls for this;
* the **daily scrape**, off the circle metadata the daily report already reads,
  which is what a club with no live board gets.

**The rate limit is the whole design.** Discord throttles channel renames to two
per ten minutes *per channel*, and discord.py does not raise on that — it sleeps
until the bucket clears, which would stall the tick that called it. So:

* a rendered name identical to the last one written costs no API call at all
  (``last_rendered``), and rank rarely moves between polls;
* scheduled updates additionally hold a per-channel floor of
  ``MIN_UPDATE_INTERVAL``, whatever combination of paths fires;
* only an explicit user action (setting a template, or a manual refresh) bypasses
  that floor, which is what makes the first change after saving instant.
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

import discord

from models import ChannelName, Club
from scrapers.umamoe_api_scraper import CircleMeta, LiveSnapshot
from services.promotion_calculator import grade_for_rank
from utils.permissions import can_use_channel

logger = logging.getLogger(__name__)

# Discord's hard cap on a channel name.
MAX_NAME_LENGTH = 100

# Floor between two scheduled renames of the same channel. Sits comfortably
# inside Discord's 2-per-10-minutes bucket while leaving room for one user-driven
# rename in the same window.
MIN_UPDATE_INTERVAL = timedelta(minutes=5)

# Shown when a figure isn't available — a new competition month serves no live
# rank for a while, and saying so beats printing a stale or invented number.
PLACEHOLDER = "—"

# Every token, with the help text the slash command and the web UI both show.
TOKENS: Dict[str, str] = {
    "club": "Club name",
    "rank": "Current rank — live while a day is running, otherwise the monthly rank",
    "monthly_rank": "Finalized monthly rank",
    "live_rank": "In-progress live rank (blank until uma.moe publishes it)",
    "yesterday_rank": "Rank as of yesterday",
    "last_month_rank": "Final rank of last month",
    "grade": "Club grade for the current rank, e.g. B+",
    "delta": "Rank movement, e.g. +3, -2 or =",
    "fans": "Month total, compact (1.84B)",
    "fans_full": "Month total with separators (1,837,269,789)",
    "fans_today": "Fans earned so far today, compact",
    "fans_today_full": "Fans earned so far today, with separators",
    "members": "Number of members in the circle",
}

_TOKEN_RE = re.compile(r"\{([a-z_]+)\}")


def _compact(n: Optional[int]) -> str:
    """Compact figure for a name that has ~100 characters to work with.

    Goes up to billions, unlike the live board's helper: a circle's monthly total
    passes a billion mid-month, and '1837.3M' in a channel name is unreadable.
    """
    if n is None:
        return PLACEHOLDER
    for unit, size in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if abs(n) >= size:
            return f"{n / size:.2f}{unit}" if abs(n) < size * 10 else f"{n / size:.1f}{unit}"
    return str(n)


def _num(n: Optional[int]) -> str:
    return PLACEHOLDER if n is None else f"{n:,}"


def _rank(n: Optional[int]) -> str:
    return PLACEHOLDER if n is None else f"{n:,}"


def _delta(n: Optional[int]) -> str:
    """Rank movement. Positive means the club climbed (a lower rank number)."""
    if n is None:
        return PLACEHOLDER
    if n == 0:
        return "="
    return f"{n:+d}"


@dataclass
class NameContext:
    """One club's figures at a moment, in the form templates are rendered from.

    Deliberately flat and source-agnostic: the live tick and the daily scrape
    each build one of these, and everything downstream stops caring which path
    it came from.
    """
    club_name: str
    rank: Optional[int] = None
    monthly_rank: Optional[int] = None
    live_rank: Optional[int] = None
    yesterday_rank: Optional[int] = None
    last_month_rank: Optional[int] = None
    delta: Optional[int] = None
    fans: Optional[int] = None
    fans_today: Optional[int] = None
    members: Optional[int] = None

    def values(self) -> Dict[str, str]:
        return {
            "club": self.club_name,
            "rank": _rank(self.rank),
            "monthly_rank": _rank(self.monthly_rank),
            "live_rank": _rank(self.live_rank),
            "yesterday_rank": _rank(self.yesterday_rank),
            "last_month_rank": _rank(self.last_month_rank),
            "grade": grade_for_rank(self.rank) or PLACEHOLDER,
            "delta": _delta(self.delta),
            "fans": _compact(self.fans),
            "fans_full": _num(self.fans),
            "fans_today": _compact(self.fans_today),
            "fans_today_full": _num(self.fans_today),
            "members": _num(self.members),
        }


def context_from_live(club: Club, snap: LiveSnapshot) -> NameContext:
    """Build a context from the live board's snapshot — the hourly path.

    ``live_rank`` leads because that is the number people are watching mid-day,
    but a freshly opened competition month serves none for a while, so it falls
    back to the monthly rank rather than showing a blank.
    """
    return NameContext(
        club_name=club.club_name,
        rank=snap.live_rank or snap.monthly_rank,
        monthly_rank=snap.monthly_rank,
        live_rank=snap.live_rank,
        delta=snap.rank_delta,
        fans=snap.live_points or snap.monthly_point,
        fans_today=snap.gained_today,
        members=len(snap.gains) or None,
    )


def context_from_meta(club: Club, meta: CircleMeta) -> NameContext:
    """Build a context from circle metadata — the daily-scrape path.

    Everything here is finalized: the day the report covers has closed, so
    ``monthly_point`` is that day's closing total and the movement is measured
    against yesterday rather than against an in-progress figure.
    """
    today = None
    if meta.monthly_point is not None and meta.yesterday_points is not None:
        today = max(0, meta.monthly_point - meta.yesterday_points)

    movement = None
    if meta.yesterday_rank is not None and meta.monthly_rank is not None:
        movement = meta.yesterday_rank - meta.monthly_rank

    return NameContext(
        club_name=club.club_name,
        rank=meta.monthly_rank,
        monthly_rank=meta.monthly_rank,
        live_rank=meta.live_rank,
        yesterday_rank=meta.yesterday_rank,
        last_month_rank=meta.last_month_rank,
        delta=movement,
        fans=meta.monthly_point,
        fans_today=today,
        members=meta.member_count,
    )


def unknown_tokens(template: str) -> List[str]:
    """Tokens in a template that render to nothing. Empty means it's valid."""
    return sorted({t for t in _TOKEN_RE.findall(template) if t not in TOKENS})


def render(template: str, ctx: NameContext) -> str:
    """Substitute tokens and clamp to what Discord will accept.

    Unknown tokens are left as written rather than swallowed — a typo showing up
    verbatim in the channel name is a faster diagnosis than a silent blank.
    """
    values = ctx.values()
    out = _TOKEN_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)
    return out.strip()[:MAX_NAME_LENGTH]


def preview(template: str) -> str:
    """Render a template against representative figures, for help text and the UI."""
    return render(template, NameContext(
        club_name="Example Club", rank=87, monthly_rank=87, live_rank=87,
        yesterday_rank=91, last_month_rank=104, delta=4,
        fans=1_837_269_789, fans_today=42_300_000, members=30,
    ))


def can_rename(channel, me) -> Optional[bool]:
    """Whether the bot can rename ``channel`` — ``None`` when we cannot tell.

    A named wrapper over the shared check, since this is the one permission this
    module cares about. ``None`` means the cached member gave no trustworthy
    answer and only Discord can settle it, so no caller may refuse on it.
    """
    return can_use_channel(channel, me, 'manage_channels')


# Last time this process renamed each channel, so the floor holds across the
# live tick, the daily scrape and any command that fires in between.
_last_rename: Dict[int, datetime] = {}


async def _rename(channel: discord.abc.GuildChannel, name: str) -> bool:
    """Rename one channel. Returns True if the name is now what we wanted."""
    await channel.edit(name=name, reason="Umamusume club figures update")
    _last_rename[channel.id] = datetime.now(timezone.utc)
    return True


async def apply_for_club(bot, club: Club, ctx: NameContext, *,
                         force: bool = False) -> Dict[str, int]:
    """Bring every channel bound to this club up to date.

    ``force`` skips the per-channel interval floor and the unchanged-name check.
    It is for user-driven moments — a template just saved, a manual refresh —
    where waiting up to an hour to see whether it worked is the whole problem.

    Never raises: a club's channels failing must not take down the tick that
    called this, nor the daily report it runs beside.
    """
    result = {"updated": 0, "skipped": 0, "failed": 0, "removed": 0, "forbidden": 0}

    try:
        rows = await ChannelName.get_enabled_for_club(club.club_id)
    except Exception as e:
        logger.error(f"channel_names: failed to load rows for {club.club_name}: {e}",
                     exc_info=True)
        return result

    now = datetime.now(timezone.utc)

    for row in rows:
        try:
            name = render(row.template, ctx)
            if not name:
                logger.warning(
                    f"channel_names: template {row.template!r} for {club.club_name} "
                    f"rendered empty — skipping channel {row.channel_id}"
                )
                result["skipped"] += 1
                continue

            if not force:
                if name == row.last_rendered:
                    result["skipped"] += 1
                    continue
                last = _last_rename.get(row.channel_id)
                if last and now - last < MIN_UPDATE_INTERVAL:
                    # Another path renamed this channel moments ago. Leaving it
                    # for the next pass costs a few minutes of staleness; pushing
                    # through would park us in Discord's rename bucket and make
                    # the caller sleep there.
                    result["skipped"] += 1
                    continue

            channel = bot.get_channel(row.channel_id)
            if channel is None:
                logger.warning(
                    f"channel_names: channel {row.channel_id} not found for "
                    f"{club.club_name} — leaving it configured in case it is a cache miss"
                )
                result["failed"] += 1
                continue

            await _rename(channel, name)
            await row.mark_rendered(name)
            result["updated"] += 1
            logger.info(f"channel_names: {club.club_name} → #{row.channel_id} = {name!r}")

        except discord.Forbidden:
            # Kept configured: this is a permission to grant, not a setting to lose.
            logger.error(
                f"channel_names: missing Manage Channels on {row.channel_id} for "
                f"{club.club_name}"
            )
            result["failed"] += 1
            result["forbidden"] += 1
        except discord.NotFound:
            # The channel is genuinely gone, so the binding can never work again.
            logger.info(
                f"channel_names: channel {row.channel_id} was deleted — "
                f"unbinding it from {club.club_name}"
            )
            await ChannelName.remove(row.channel_id)
            result["removed"] += 1
        except discord.HTTPException as e:
            logger.warning(
                f"channel_names: failed to rename {row.channel_id} for "
                f"{club.club_name}: {e}"
            )
            result["failed"] += 1
        except Exception as e:
            logger.error(
                f"channel_names: unexpected error on {row.channel_id} for "
                f"{club.club_name}: {e}", exc_info=True
            )
            result["failed"] += 1

    return result


async def refresh_now(bot, club: Club) -> Dict[str, int]:
    """Fetch fresh figures and update this club's channels immediately.

    The instant first change after a template is saved, and what ``/channel_name``
    and the dashboard's refresh both call. Tries the live route first, since that
    is the figure the hourly path would have written anyway, and falls back to a
    finalized scrape when uma.moe has no live rows yet — which is exactly the
    state a newly opened competition month is in, and no reason to leave someone
    staring at an unchanged channel wondering whether they set it up right.

    The returned dict carries ``source``: ``"live"``, ``"daily"``, or ``None``
    when no figures could be read at all.
    """
    from scrapers.umamoe_api_scraper import UmaMoeAPIScraper
    from utils.rate_limiter import PRIORITY_INTERACTIVE

    empty = {"updated": 0, "skipped": 0, "failed": 0, "removed": 0,
             "forbidden": 0, "source": None}

    if not club.is_circle_id_valid():
        logger.warning(
            f"channel_names: cannot refresh {club.club_name} — circle_id is not set"
        )
        return empty

    scraper = UmaMoeAPIScraper(club.circle_id, priority=PRIORITY_INTERACTIVE)
    snap = await scraper.fetch_live()

    if snap is not None:
        ctx, source = context_from_live(club, snap), "live"
    else:
        # fetch_live builds its metadata locally and leaves the scraper's own
        # empty, so the fallback has to actually run the finalized scrape to have
        # anything to read.
        try:
            await scraper.scrape()
        except Exception as e:
            logger.warning(f"channel_names: no figures available for {club.club_name}: {e}")
            return empty
        meta = scraper.get_meta()
        if meta.monthly_rank is None and meta.monthly_point is None:
            return empty
        ctx, source = context_from_meta(club, meta), "daily"

    result = await apply_for_club(bot, club, ctx, force=True)
    result["source"] = source
    return result


async def clubs_needing_updates() -> List[Club]:
    """Active clubs that want their channels renamed on the hourly tick.

    Separate from the live board's own list: a club can want renamed channels
    without wanting a board message posted, and it still needs the live fetch.
    """
    club_ids = set(await ChannelName.club_ids_with_templates())
    if not club_ids:
        return []
    return [c for c in await Club.get_all_active()
            if c.club_id in club_ids and c.is_circle_id_valid()]
