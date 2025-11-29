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
    date: date
    cumulative_fans: int
    expected_fans: int
    deficit_surplus: int
    days_behind: int
    
    @classmethod
    async def create(cls, member_id: UUID, date: date, cumulative_fans: int,
                     expected_fans: int, deficit_surplus: int, days_behind: int) -> 'QuotaHistory':
        """Create or update quota history for a date"""
        query = """
            INSERT INTO quota_history 
                (member_id, date, cumulative_fans, expected_fans, deficit_surplus, days_behind)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (member_id, date) 
            DO UPDATE SET 
                cumulative_fans = $3,
                expected_fans = $4,
                deficit_surplus = $5,
                days_behind = $6
            RETURNING id, member_id, date, cumulative_fans, expected_fans, deficit_surplus, days_behind
        """
        row = await db.fetchrow(query, member_id, date, cumulative_fans, 
                                expected_fans, deficit_surplus, days_behind)
        return cls(**dict(row))
    
    @classmethod
    async def get_latest_for_member(cls, member_id: UUID) -> Optional['QuotaHistory']:
        """Get the most recent quota history for a member"""
        query = """
            SELECT id, member_id, date, cumulative_fans, expected_fans, deficit_surplus, days_behind
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
            SELECT id, member_id, date, cumulative_fans, expected_fans, deficit_surplus, days_behind
            FROM quota_history
            WHERE member_id = $1
            ORDER BY date DESC
            LIMIT $2
        """
        rows = await db.fetch(query, member_id, n)
        return [cls(**dict(row)) for row in rows]
    
    @classmethod
    async def get_for_date(cls, date: date) -> List['QuotaHistory']:
        """Get all quota histories for a specific date"""
        query = """
            SELECT id, member_id, date, cumulative_fans, expected_fans, deficit_surplus, days_behind
            FROM quota_history
            WHERE date = $1
        """
        rows = await db.fetch(query, date)
        return [cls(**dict(row)) for row in rows]
    
    @classmethod
    async def check_consecutive_behind_days(cls, member_id: UUID, check_days: int) -> int:
        """
        Check how many consecutive days a member has been behind quota
        Returns: number of consecutive days behind (0 if currently on track)
        """
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
    async def clear_all(cls):
        """Clear all quota history (for monthly reset)"""
        query = "DELETE FROM quota_history"
        await db.execute(query)
        logger.info("Cleared all quota history for monthly reset")
