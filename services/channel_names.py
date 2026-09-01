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
from typing import Any, Dict, List, Optional
from uuid import UUID

import discord

from models import ChannelName, Club
from scrapers.umamoe_api_scraper import CircleMeta, LiveSnapshot
from services.promotion_calculator import grade_for_rank
from utils.permissions import (
    can_use_channel, describe_channel_access, describe_channel_overwrites,
    missing_channel_permissions, timeout_note, resolution_fingerprint,
    rename_requirements,
)

logger = logging.getLogger(__name__)

# Discord's hard cap on a channel name.
MAX_NAME_LENGTH = 100

# Floor between two scheduled renames of the same channel. Sits comfortably
# inside Discord's bucket while leaving room for one user-driven rename in the
# same window.
MIN_UPDATE_INTERVAL = timedelta(minutes=5)

# Discord's actual limit on renaming one channel. Exceeding it does not raise —
# discord.py sleeps until the bucket clears, and it was measured on 2026-09-01
# sleeping 351 seconds inside a slash command, which simply hangs the command and
# any tick behind it. So the budget is enforced here, before the request is made,
# and a forced rename is refused rather than parked. Observed as repeated
# `429 ... Retrying in 351.81 seconds` after a channel was configured several
# times in a row while it was failing for an unrelated reason.
RENAME_LIMIT = 2
RENAME_WINDOW = timedelta(minutes=10)

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

    Asks for everything the rename needs, which on a voice channel includes
    **Connect**: Discord refuses to let anyone edit a voice channel they cannot
    join. ``None`` means the cached member gave no trustworthy answer and only
    Discord can settle it, so no caller may refuse on it.
    """
    return can_use_channel(channel, me, *rename_requirements(channel))


# When this process renamed each channel, so both the scheduled floor and the
# hard budget hold across the live tick, the daily scrape and any command that
# fires in between.
_rename_times: Dict[int, List[datetime]] = {}


def _recent_renames(channel_id: int, now: datetime) -> List[datetime]:
    """This channel's renames inside the current window, pruned in place."""
    times = [t for t in _rename_times.get(channel_id, []) if now - t < RENAME_WINDOW]
    if times:
        _rename_times[channel_id] = times
    else:
        _rename_times.pop(channel_id, None)
    return times


def rename_budget_wait(channel_id: int, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds until this channel may be renamed again, or ``None`` if it may now.

    Enforced for forced renames too. Waiting out Discord's bucket inside the
    request means a slash command that appears to hang for minutes, which reads
    as the bot being broken and invites exactly the retry that deepens the hole.
    """
    now = now or datetime.now(timezone.utc)
    times = _recent_renames(channel_id, now)
    if len(times) < RENAME_LIMIT:
        return None
    return (min(times) + RENAME_WINDOW - now).total_seconds()


async def _rename(channel: discord.abc.GuildChannel, name: str) -> bool:
    """Rename one channel. Returns True if the name is now what we wanted."""
    await channel.edit(name=name, reason="Umamusume club figures update")
    _rename_times.setdefault(channel.id, []).append(datetime.now(timezone.utc))
    return True


async def _retry_from_api(bot, channel_id: int, name: str):
    """Fetch the channel from Discord and try once more against that object.

    ``bot.get_channel`` serves the gateway cache, and on 2026-09-01 that cache
    was demonstrably not self-consistent: the overwrites it held granted Manage
    Channels to two targets that both applied to the bot, while
    ``permissions_for`` computed from those same overwrites said the permission
    was absent. Both cannot be true, so cached state stopped being worth
    reasoning about for this path.

    A fetch is authoritative. If the retry succeeds the cache was the whole
    problem and nobody has to be told to reconfigure anything; if it fails the
    same way, the refusal is real and the fetched overwrites are evidence rather
    than another guess.

    Returns ``(succeeded, note)``. Never raises — this runs inside a failure
    handler, which must not produce a second failure.
    """
    try:
        fresh = await bot.fetch_channel(channel_id)
    except Exception as e:
        return False, f"couldn't fetch the channel from Discord: {e}"

    me = getattr(getattr(fresh, 'guild', None), 'me', None)
    needs = rename_requirements(fresh)
    fetched = (
        f"{describe_channel_access(fresh, me, *needs)} | "
        f"{describe_channel_overwrites(fresh, me, *needs)}"
    )

    try:
        await _rename(fresh, name)
        return True, f"succeeded on a freshly fetched channel — the cache was stale. {fetched}"
    except discord.Forbidden as e:
        return False, f"refused again on a fresh fetch (code {e.code}): {e.text} · {fetched}"
    except Exception as e:
        return False, f"failed again on a fresh fetch: {e} · {fetched}"


async def apply_for_club(bot, club: Club, ctx: NameContext, *,
                         force: bool = False,
                         only: Optional[int] = None) -> Dict[str, Any]:
    """Bring this club's tracking channels up to date.

    ``force`` skips the per-channel interval floor and the unchanged-name check.
    It is for user-driven moments — a template just saved, a manual refresh —
    where waiting up to an hour to see whether it worked is the whole problem.

    ``only`` restricts the run to a single channel. Setting one template used to
    rewrite every channel the club owns, which spent other channels' rename
    budget for no reason and, worse, let one channel's success be reported as
    another's: configure a channel that Discord refuses, and the reply said it
    was working because a *different* channel had been renamed in the same pass.

    The totals are kept for callers that just want a tally, but ``per_channel``
    is what a caller should read when it cares about one specific channel —
    which is every caller acting on a user's request about that channel.

    Never raises: a club's channels failing must not take down the tick that
    called this, nor the daily report it runs beside.
    """
    result: Dict[str, Any] = {"updated": 0, "skipped": 0, "failed": 0,
                              "removed": 0, "forbidden": 0, "per_channel": {}}

    def record(channel_id: int, status: str, **detail) -> None:
        result["per_channel"][channel_id] = {"status": status, **detail}

    try:
        rows = await ChannelName.get_enabled_for_club(club.club_id)
    except Exception as e:
        logger.error(f"channel_names: failed to load rows for {club.club_name}: {e}",
                     exc_info=True)
        return result

    if only is not None:
        rows = [r for r in rows if r.channel_id == only]

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
                record(row.channel_id, "empty_template")
                continue

            if not force:
                if name == row.last_rendered:
                    result["skipped"] += 1
                    record(row.channel_id, "unchanged", name=name)
                    continue
                recent = _recent_renames(row.channel_id, now)
                if recent and now - max(recent) < MIN_UPDATE_INTERVAL:
                    # Another path renamed this channel moments ago. Leaving it
                    # for the next pass costs a few minutes of staleness; pushing
                    # through would spend budget a user-driven rename may want.
                    result["skipped"] += 1
                    record(row.channel_id, "too_soon", name=name)
                    continue

            # Applies to forced renames as well: Discord does not refuse an
            # over-budget rename, it makes us wait minutes inside the request.
            wait = rename_budget_wait(row.channel_id, now)
            if wait is not None:
                logger.info(
                    f"channel_names: {club.club_name} → #{row.channel_id} held for "
                    f"{wait:.0f}s — Discord allows {RENAME_LIMIT} renames per "
                    f"{RENAME_WINDOW.total_seconds() / 60:.0f} minutes per channel"
                )
                result["skipped"] += 1
                record(row.channel_id, "rate_limited", name=name, wait=wait)
                continue

            channel = bot.get_channel(row.channel_id)
            if channel is None:
                logger.warning(
                    f"channel_names: channel {row.channel_id} not found for "
                    f"{club.club_name} — leaving it configured in case it is a cache miss"
                )
                result["failed"] += 1
                record(row.channel_id, "not_cached")
                continue

            await _rename(channel, name)
            await row.mark_rendered(name)
            result["updated"] += 1
            record(row.channel_id, "updated", name=name)
            logger.info(f"channel_names: {club.club_name} → #{row.channel_id} = {name!r}")

        except discord.Forbidden as e:
            # Kept configured: this is a permission to grant, not a setting to lose.
            #
            # Report what Discord actually said. 50001 (Missing Access) and 50013
            # (Missing Permissions) call for different fixes, and this used to log
            # a guess at one of them.
            # getattr twice over: a channel reached from a thin cache may carry
            # no guild, and the error path must not raise its own error.
            me = getattr(getattr(channel, 'guild', None), 'me', None)
            needs = rename_requirements(channel)
            access = describe_channel_access(channel, me, *needs)
            lacking = missing_channel_permissions(channel, me, *needs)
            logger.error(
                f"channel_names: Discord refused the rename of {row.channel_id} for "
                f"{club.club_name} — HTTP {e.status}, code {e.code}: {e.text} · {access}"
            )
            logger.error(
                f"channel_names: overwrites on {row.channel_id} as I see them — "
                f"{describe_channel_overwrites(channel, me, *needs)}"
            )
            # How that reading was arrived at. Two of discord.py's paths ignore
            # overwrites entirely and produce a fixed set that looks like a
            # misconfigured channel; this says which one we are on.
            logger.error(
                f"channel_names: how {row.channel_id} resolved — "
                f"{resolution_fingerprint(channel, me)}"
            )

            timed_out = timeout_note(me)
            if timed_out:
                # No point fetching or retrying: a timeout masks every permission
                # and Discord will refuse identically until it is lifted.
                logger.error(
                    f"channel_names: {club.club_name} — the bot is timed out in "
                    f"guild {getattr(getattr(channel, 'guild', None), 'id', '?')}; "
                    f"no permission can take effect until that is lifted"
                )
                note = "skipped — the bot is timed out in this server"
            else:
                # A cached refusal is worth one authoritative round trip, since
                # the gateway cache has been seen disagreeing with itself.
                recovered, note = await _retry_from_api(bot, row.channel_id, name)
                logger.error(
                    f"channel_names: retry from the API on {row.channel_id} — {note}"
                )

                if recovered:
                    await row.mark_rendered(name)
                    result["updated"] += 1
                    record(row.channel_id, "updated", name=name, recovered=True)
                    logger.info(
                        f"channel_names: {club.club_name} → #{row.channel_id} = {name!r} "
                        f"(recovered after a stale-cache refusal)"
                    )
                    continue

            result["failed"] += 1
            result["forbidden"] += 1
            record(row.channel_id, "forbidden", code=e.code, detail=e.text,
                   access=access, missing=lacking, retry=note, timeout=timed_out)

        except discord.RateLimited as e:
            logger.warning(
                f"channel_names: rate limited renaming {row.channel_id} for "
                f"{club.club_name}; {e.retry_after:.0f}s to wait"
            )
            result["skipped"] += 1
            record(row.channel_id, "rate_limited", name=name, wait=e.retry_after)

        except discord.NotFound:
            # The channel is genuinely gone, so the binding can never work again.
            logger.info(
                f"channel_names: channel {row.channel_id} was deleted — "
                f"unbinding it from {club.club_name}"
            )
            await ChannelName.remove(row.channel_id)
            result["removed"] += 1
            record(row.channel_id, "deleted")
        except discord.HTTPException as e:
            logger.warning(
                f"channel_names: failed to rename {row.channel_id} for "
                f"{club.club_name}: {e}"
            )
            result["failed"] += 1
            record(row.channel_id, "http_error", detail=str(e))
        except Exception as e:
            logger.error(
                f"channel_names: unexpected error on {row.channel_id} for "
                f"{club.club_name}: {e}", exc_info=True
            )
            result["failed"] += 1
            record(row.channel_id, "error", detail=str(e))

    return result


async def refresh_now(bot, club: Club, *,
                      only: Optional[int] = None) -> Dict[str, Any]:
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

    empty: Dict[str, Any] = {"updated": 0, "skipped": 0, "failed": 0, "removed": 0,
                             "forbidden": 0, "source": None, "per_channel": {}}

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

    result = await apply_for_club(bot, club, ctx, force=True, only=only)
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
