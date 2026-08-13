"""
Rank-promotion calculator.

Answers "how many more fans does this club need to climb to a better rank?" — e.g.
a club sitting at rank 107 wanting to get back into the Top 100.

Club ranks are relative/positional, so the fans needed to reach rank ``N`` is the
monthly fan total of the club currently at rank ``N`` minus our club's monthly fan
total. Both figures come from uma.moe (see ``scrapers/umamoe_leaderboard``). The
result is therefore an **estimate**: per-club fan snapshots are staggered in time,
and rivals keep gaining too, so the real climb usually takes longer.
"""
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import List, Optional
from uuid import UUID
import calendar
import logging

from config.database import db
from config.settings import CLUB_RANK_GRADES, PROMOTION_MILESTONES
from models.club import Club
from scrapers.umamoe_leaderboard import fetch_entry_at_rank, fetch_own_standing

logger = logging.getLogger(__name__)


@dataclass
class PromotionResult:
    """Outcome of a promotion computation for one club."""
    current_rank: int
    target_rank: int
    our_fans: int
    target_fans: int
    fans_needed: int                 # extra fans required to overtake the target rank
    daily_rate: Optional[int]        # what the club currently gains per day (None if unknown)
    days_remaining: int              # days left in the competition month
    extra_per_day: Optional[int]     # extra fans/day ON TOP of current pace to reach target by month-end
    is_milestone: bool               # True when target_rank came from PROMOTION_MILESTONES
    already_reached: bool = False    # True when the club is already at/above the target
    rate_is_average: bool = False    # True when daily_rate is a month-to-date average (vs a recent day-over-day gain)


def next_milestone(current_rank: int, milestones: Optional[List[int]] = None) -> Optional[int]:
    """
    Return the best milestone strictly above (numerically below) the current rank —
    i.e. the closest promotion target that is an actual improvement — or ``None`` when
    the club already sits at or above the top milestone.

    Example: rank 107 → 100; rank 45 → 30; rank 600 → 500; rank 1483 → 1000; rank 7 → None.
    """
    pool = milestones if milestones is not None else PROMOTION_MILESTONES
    better = [m for m in pool if m < current_rank]
    return max(better) if better else None


def grade_for_rank(rank: Optional[int]) -> Optional[str]:
    """The club grade a leaderboard position sits in, e.g. 1483 → 'B+'.

    ``None`` for ranks past the bottom band, where the game shows no grade.
    """
    if rank is None:
        return None
    for bound, grade in CLUB_RANK_GRADES:
        if rank <= bound:
            return grade
    return None


async def _recent_daily_rate(club_id: UUID) -> Optional[int]:
    """
    Day-over-day gain in the club's total monthly fans, or ``None``.

    Sums active members' cumulative fans per day and diffs the two most recent
    dates — but only when they are consecutive calendar days, so a bot that skipped
    several days doesn't report a multi-day jump as a single day's pace. A
    non-positive delta (e.g. across a monthly reset) is treated as unknown.
    """
    query = """
        SELECT qh.date AS d, SUM(qh.cumulative_fans) AS total
        FROM quota_history qh
        JOIN members m ON m.member_id = qh.member_id
        WHERE qh.club_id = $1 AND m.is_active = TRUE
        GROUP BY qh.date
        ORDER BY qh.date DESC
        LIMIT 2
    """
    rows = await db.fetch(query, club_id)
    if len(rows) < 2:
        return None
    if (rows[0]["d"] - rows[1]["d"]).days != 1:
        return None
    rate = int(rows[0]["total"]) - int(rows[1]["total"])
    return rate if rate > 0 else None


def _avg_daily_rate(our_fans: int, ref: Optional[datetime] = None) -> Optional[int]:
    """
    Month-to-date average daily gain: monthly fans ÷ competition days elapsed.

    Always available from a single standing read, so it's the fallback when there's
    no clean recent day-over-day delta (e.g. a dev bot that only runs occasionally).
    uma.moe data lags ~1 day, so we divide by completed days (day-of-month − 1).
    """
    ref = ref or datetime.now()
    days_elapsed = max(1, ref.day - 1)
    rate = our_fans // days_elapsed
    return rate if rate > 0 else None


async def compute_promotion(
    club: Club,
    target_rank: Optional[int] = None,
    current_rank: Optional[int] = None,
    our_fans: Optional[int] = None,
) -> Optional[PromotionResult]:
    """
    Compute how many fans ``club`` needs to reach ``target_rank``.

    ``target_rank`` defaults to the next milestone above the club's current rank.
    ``current_rank`` / ``our_fans`` may be supplied by callers that already have a
    fresh standing (e.g. the daily report) to skip the extra API read; otherwise
    they're fetched from uma.moe. Returns ``None`` when the standing can't be
    determined (no API key, unknown circle, network error).
    """
    if current_rank is None or our_fans is None:
        standing = await fetch_own_standing(club.circle_id)
        if not standing:
            return None
        current_rank = standing.get("monthly_rank")
        our_fans = standing.get("monthly_point")
    if current_rank is None or our_fans is None:
        return None

    now = datetime.now()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_remaining = max(1, days_in_month - now.day)

    is_milestone = target_rank is None
    if target_rank is None:
        target_rank = next_milestone(current_rank)
        if target_rank is None:
            # Already at or above the top milestone — nothing to climb toward.
            return PromotionResult(
                current_rank=current_rank, target_rank=current_rank,
                our_fans=our_fans, target_fans=our_fans, fans_needed=0,
                daily_rate=None, days_remaining=days_remaining, extra_per_day=None,
                is_milestone=True, already_reached=True,
            )

    if current_rank <= target_rank:
        return PromotionResult(
            current_rank=current_rank, target_rank=target_rank,
            our_fans=our_fans, target_fans=our_fans, fans_needed=0,
            daily_rate=None, days_remaining=days_remaining, extra_per_day=None,
            is_milestone=is_milestone, already_reached=True,
        )

    entry = await fetch_entry_at_rank(target_rank)
    if not entry:
        return None
    target_fans = entry.get("monthly_point")
    if target_fans is None:
        return None

    # +1 fan to actually overtake the club holding the target position.
    fans_needed = max(0, target_fans - our_fans + 1)

    # Current pace: prefer an accurate recent day-over-day gain, fall back to a
    # month-to-date average so we can still show a figure when daily history is sparse.
    daily_rate = await _recent_daily_rate(club.club_id)
    rate_is_average = False
    if not daily_rate:
        daily_rate = _avg_daily_rate(our_fans)
        rate_is_average = daily_rate is not None

    # Extra fans/day ON TOP of the current pace needed to close the gap by month-end.
    extra_per_day = ceil(fans_needed / days_remaining) if fans_needed > 0 else 0

    return PromotionResult(
        current_rank=current_rank, target_rank=target_rank,
        our_fans=our_fans, target_fans=target_fans, fans_needed=fans_needed,
        daily_rate=daily_rate, days_remaining=days_remaining, extra_per_day=extra_per_day,
        is_milestone=is_milestone, already_reached=False, rate_is_average=rate_is_average,
    )
