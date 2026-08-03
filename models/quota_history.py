"""
Quota History data model
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional, List
from uuid import UUID
import logging

from config.database import db

logger = logging.getLogger(__name__)


@dataclass
class QuotaHistory:
    """Represents a member's daily quota tracking"""
    id: Optional[UUID]
    member_id: UUID
    club_id: UUID
    date: date
    cumulative_fans: int
    expected_fans: int
    deficit_surplus: int
    days_behind: int
    
    @classmethod
    async def create(cls, member_id: UUID, club_id: UUID, date: date, cumulative_fans: int,
                     expected_fans: int, deficit_surplus: int, days_behind: int) -> 'QuotaHistory':
        """Create or update quota history for a date.

        ``created_at`` is refreshed on update, so it means "when this row was last
        written by a scrape" rather than "first inserted". The web club page reads
        ``MAX(created_at)`` as the last-scrape time for its staleness badge, and a
        rewrite of an existing date is still a scrape that ran.

        This matters at the month boundary: a new month's day 1 is deferred (see
        utils.jst_calendar.resolve_finalized), so the 1st rewrites the previous
        month's final row instead of inserting a new date. Leaving the timestamp
        untouched would show every club as days stale at the start of each month.
        """
        query = """
            INSERT INTO quota_history
                (member_id, club_id, date, cumulative_fans, expected_fans, deficit_surplus, days_behind)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (member_id, date)
            DO UPDATE SET
                cumulative_fans = $4,
                expected_fans = $5,
                deficit_surplus = $6,
                days_behind = $7,
                created_at = NOW()
            RETURNING id, member_id, club_id, date, cumulative_fans, expected_fans, deficit_surplus, days_behind
        """
        row = await db.fetchrow(query, member_id, club_id, date, cumulative_fans, 
                                expected_fans, deficit_surplus, days_behind)
        return cls(**dict(row))
    
    @classmethod
    async def get_latest_for_member(cls, member_id: UUID) -> Optional['QuotaHistory']:
        """Get the most recent quota history for a member"""
        query = """
            SELECT id, member_id, club_id, date, cumulative_fans, expected_fans, deficit_surplus, days_behind
            FROM quota_history
            WHERE member_id = $1
            ORDER BY date DESC
            LIMIT 1
        """
        row = await db.fetchrow(query, member_id)
        if row:
            return cls(**dict(row))
        return None
    
    @classmethod
    async def get_last_n_days(cls, member_id: UUID, n: int) -> List['QuotaHistory']:
        """Get last N days of history for a member"""
        query = """
            SELECT id, member_id, club_id, date, cumulative_fans, expected_fans, deficit_surplus, days_behind
            FROM quota_history
            WHERE member_id = $1
            ORDER BY date DESC
            LIMIT $2
        """
        rows = await db.fetch(query, member_id, n)
        return [cls(**dict(row)) for row in rows]
    
    @classmethod
    async def get_for_member_date(cls, member_id: UUID, target_date: date) -> Optional['QuotaHistory']:
        """Get a specific member's quota history for a given date"""
        query = """
            SELECT id, member_id, club_id, date, cumulative_fans, expected_fans, deficit_surplus, days_behind
            FROM quota_history
            WHERE member_id = $1 AND date = $2
        """
        row = await db.fetchrow(query, member_id, target_date)
        if row:
            return cls(**dict(row))
        return None

    @classmethod
    async def get_for_date(cls, club_id: UUID, date: date) -> List['QuotaHistory']:
        """Get all quota histories for a specific date in a club"""
        query = """
            SELECT id, member_id, club_id, date, cumulative_fans, expected_fans, deficit_surplus, days_behind
            FROM quota_history
            WHERE club_id = $1 AND date = $2
        """
        rows = await db.fetch(query, club_id, date)
        return [cls(**dict(row)) for row in rows]
    
    @classmethod
    async def check_consecutive_behind_days(cls, member_id: UUID, check_days: int, current_date: date = None) -> int:
        """
        Check how many consecutive days a member has been behind quota.
        Only counts days within the same month as current_date to avoid
        February history carrying over into March.
        Returns: number of consecutive days behind (0 if currently on track)
        """
        if current_date is not None:
            query = """
                WITH recent_days AS (
                    SELECT date, deficit_surplus
                    FROM quota_history
                    WHERE member_id = $1
                      AND date_part('year', date) = date_part('year', $3::date)
                      AND date_part('month', date) = date_part('month', $3::date)
                    ORDER BY date DESC
                    LIMIT $2
                )
                SELECT COUNT(*) as consecutive_behind
                FROM (
                    SELECT date, deficit_surplus,
                           ROW_NUMBER() OVER (ORDER BY date DESC) as rn
                    FROM recent_days
                    WHERE deficit_surplus < 0
                    ORDER BY date DESC
                ) sub
                WHERE rn <= $2
                AND (SELECT deficit_surplus FROM recent_days ORDER BY date DESC LIMIT 1) < 0
            """
            result = await db.fetchval(query, member_id, check_days, current_date)
        else:
            query = """
                WITH recent_days AS (
                    SELECT date, deficit_surplus
                    FROM quota_history
                    WHERE member_id = $1
                    ORDER BY date DESC
                    LIMIT $2
                )
                SELECT COUNT(*) as consecutive_behind
                FROM (
                    SELECT date, deficit_surplus,
                           ROW_NUMBER() OVER (ORDER BY date DESC) as rn
                    FROM recent_days
                    WHERE deficit_surplus < 0
                    ORDER BY date DESC
                ) sub
                WHERE rn <= $2
                AND (SELECT deficit_surplus FROM recent_days ORDER BY date DESC LIMIT 1) < 0
            """
            result = await db.fetchval(query, member_id, check_days)
        return result or 0
    
    @classmethod
    async def get_current_month_for_club(cls, club_id: UUID, year: int, month: int):
        """Get all quota history rows for a club in a given month, joined with trainer names.
        Returns raw asyncpg records with (date, cumulative_fans, trainer_name)."""
        query = """
            SELECT qh.date, qh.cumulative_fans, m.trainer_name
            FROM quota_history qh
            JOIN members m ON m.member_id = qh.member_id
            WHERE qh.club_id = $1
              AND date_part('year', qh.date) = $2
              AND date_part('month', qh.date) = $3
              AND m.is_active = TRUE
            ORDER BY qh.date ASC
        """
        return await db.fetch(query, club_id, year, month)

    @classmethod
    async def clear_all(cls, club_id: UUID):
        """Clear all quota history for a club (for monthly reset)"""
        query = "DELETE FROM quota_history WHERE club_id = $1"
        await db.execute(query, club_id)
        logger.info(f"Cleared all quota history for club {club_id} (monthly reset)")
