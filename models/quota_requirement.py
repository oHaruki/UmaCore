"""
Quota Requirement data model for dynamic quota management
"""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from typing import Dict, Optional, List, Tuple
from uuid import UUID
import logging

from config.database import db

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuotaSchedule:
    """A club's quota timeline, resolvable for any date without further queries.

    Exists because resolving one date at a time is quadratic in practice.
    ``calculate_expected_fans`` sums the quota for every day of the month, per
    member, and each lookup cost up to two queries — so a 30-member club on day
    25 issued roughly 1500 sequential round trips per scrape. ``/recalculate``
    was worse still, nesting that inside a per-history-row loop.

    Load once, resolve in memory: two queries per scrape regardless of club size
    or how far into the month it is.
    """
    effective_dates: Tuple[date, ...]     # ascending, one entry per distinct date
    quotas: Tuple[int, ...]               # parallel to effective_dates
    default_quota: int

    @classmethod
    async def load(cls, club_id: UUID) -> "QuotaSchedule":
        rows = await db.fetch(
            """
            SELECT effective_date, daily_quota
            FROM quota_requirements
            WHERE club_id = $1
            ORDER BY effective_date ASC, created_at ASC NULLS FIRST
            """,
            club_id,
        )
        # Collapse duplicate effective_dates, newest wins. The SQL this replaces
        # used `ORDER BY effective_date DESC LIMIT 1` with no tiebreaker, so ties
        # resolved arbitrarily; newest-wins is at least deterministic.
        by_date: Dict[date, int] = {}
        for r in rows:
            by_date[r["effective_date"]] = r["daily_quota"]
        ordered = sorted(by_date.items())

        from models import Club
        club = await Club.get_by_id(club_id)

        return cls(
            effective_dates=tuple(d for d, _ in ordered),
            quotas=tuple(q for _, q in ordered),
            default_quota=club.daily_quota if club else 1_000_000,
        )

    def for_date(self, check_date: date) -> int:
        """The applicable daily quota: the latest requirement effective on or
        before ``check_date``, else the club's default."""
        i = bisect_right(self.effective_dates, check_date) - 1
        return self.quotas[i] if i >= 0 else self.default_quota

    @classmethod
    def from_pairs(cls, pairs, default_quota: int) -> "QuotaSchedule":
        """Build directly from ``(effective_date, quota)`` pairs. For tests."""
        by_date = dict(pairs)
        ordered = sorted(by_date.items())
        return cls(
            effective_dates=tuple(d for d, _ in ordered),
            quotas=tuple(q for _, q in ordered),
            default_quota=default_quota,
        )


@dataclass
class QuotaRequirement:
    """Represents a quota requirement setting"""
    id: Optional[UUID]
    club_id: UUID
    effective_date: date
    daily_quota: int
    set_by: Optional[str]
    
    @classmethod
    async def create(cls, club_id: UUID, effective_date: date, daily_quota: int, set_by: str = None) -> 'QuotaRequirement':
        """Create a new quota requirement"""
        query = """
            INSERT INTO quota_requirements (club_id, effective_date, daily_quota, set_by)
            VALUES ($1, $2, $3, $4)
            RETURNING id, club_id, effective_date, daily_quota, set_by
        """
        row = await db.fetchrow(query, club_id, effective_date, daily_quota, set_by)
        logger.info(f"Quota requirement created for club {club_id}: {daily_quota:,} fans/day effective {effective_date} (set by {set_by})")
        return cls(**dict(row))
    
    @classmethod
    async def get_quota_for_date(cls, club_id: UUID, check_date: date) -> int:
        """
        Get the applicable daily quota for a specific date in a club.

        Convenience wrapper for one-off lookups (reports, cards, info boards).
        Delegates to :class:`QuotaSchedule` so the resolution rule lives in one
        place. **Do not call this in a loop** — load a ``QuotaSchedule`` once and
        use ``for_date`` instead, or you reintroduce a query per iteration.

        Args:
            club_id: Club UUID
            check_date: The date to check

        Returns:
            The daily quota amount (defaults to club's default quota if none found)
        """
        schedule = await QuotaSchedule.load(club_id)
        return schedule.for_date(check_date)
    
    @classmethod
    async def get_all_for_month(cls, club_id: UUID, year: int, month: int) -> List['QuotaRequirement']:
        """
        Get all quota requirements for a specific month in a club
        
        Args:
            club_id: Club UUID
            year: Year (e.g., 2024)
            month: Month (1-12)
        
        Returns:
            List of QuotaRequirement objects sorted by effective_date
        """
        from datetime import date as date_class
        start_date = date_class(year, month, 1)
        
        # Calculate last day of month
        if month == 12:
            end_date = date_class(year + 1, 1, 1)
        else:
            end_date = date_class(year, month + 1, 1)
        
        query = """
            SELECT id, club_id, effective_date, daily_quota, set_by
            FROM quota_requirements
            WHERE club_id = $1 AND effective_date >= $2 AND effective_date < $3
            ORDER BY effective_date ASC
        """
        rows = await db.fetch(query, club_id, start_date, end_date)
        return [cls(**dict(row)) for row in rows]
    
    @classmethod
    async def get_all_current_month(cls, club_id: UUID, current_date: date) -> List['QuotaRequirement']:
        """Get all quota requirements for the current month in a club"""
        return await cls.get_all_for_month(club_id, current_date.year, current_date.month)
    
    @classmethod
    async def delete_by_date_and_amount(cls, club_id: UUID, effective_date: date, daily_quota: int) -> int:
        """Delete a specific quota requirement by date and amount. Returns number of rows deleted."""
        query = """
            DELETE FROM quota_requirements
            WHERE club_id = $1 AND effective_date = $2 AND daily_quota = $3
        """
        result = await db.execute(query, club_id, effective_date, daily_quota)
        count = int(result.split()[-1])
        logger.info(f"Deleted {count} quota requirement(s) for club {club_id}: {daily_quota:,} fans/day on {effective_date}")
        return count

    @classmethod
    async def clear_all(cls, club_id: UUID):
        """Clear all quota requirements for a club (for monthly reset)"""
        query = "DELETE FROM quota_requirements WHERE club_id = $1"
        await db.execute(query, club_id)
        logger.info(f"Cleared all quota requirements for club {club_id} (monthly reset)")
