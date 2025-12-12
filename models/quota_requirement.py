"""
Quota Requirement data model for dynamic quota management
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional, List
from uuid import UUID
import logging

from config.database import db

logger = logging.getLogger(__name__)


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
        Get the applicable daily quota for a specific date in a club
        
        Args:
            club_id: Club UUID
            check_date: The date to check
        
        Returns:
            The daily quota amount (defaults to club's default quota if none found)
        """
        query = """
            SELECT daily_quota
            FROM quota_requirements
            WHERE club_id = $1 AND effective_date <= $2
            ORDER BY effective_date DESC
            LIMIT 1
        """
        result = await db.fetchval(query, club_id, check_date)
        
        if result is not None:
            return result
        
        # No quota requirement found, use club's default quota
        from models import Club
        club = await Club.get_by_id(club_id)
        return club.daily_quota if club else 1000000
    
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
    async def clear_all(cls, club_id: UUID):
        """Clear all quota requirements for a club (for monthly reset)"""
        query = "DELETE FROM quota_requirements WHERE club_id = $1"
        await db.execute(query, club_id)
        logger.info(f"Cleared all quota requirements for club {club_id} (monthly reset)")
