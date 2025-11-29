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
    effective_date: date
    daily_quota: int
    set_by: Optional[str]
    
    @classmethod
    async def create(cls, effective_date: date, daily_quota: int, set_by: str = None) -> 'QuotaRequirement':
        """Create a new quota requirement"""
        query = """
            INSERT INTO quota_requirements (effective_date, daily_quota, set_by)
            VALUES ($1, $2, $3)
            RETURNING id, effective_date, daily_quota, set_by
        """
        row = await db.fetchrow(query, effective_date, daily_quota, set_by)
        logger.info(f"Quota requirement created: {daily_quota:,} fans/day effective {effective_date} (set by {set_by})")
        return cls(**dict(row))
    
    @classmethod
    async def get_quota_for_date(cls, check_date: date) -> int:
        """
        Get the applicable daily quota for a specific date
        
        Args:
            check_date: The date to check
        
        Returns:
            The daily quota amount (defaults to DAILY_QUOTA from settings if none found)
        """
        query = """
            SELECT daily_quota
            FROM quota_requirements
            WHERE effective_date <= $1
            ORDER BY effective_date DESC
            LIMIT 1
        """
        result = await db.fetchval(query, check_date)
        
        if result is not None:
            return result
        
        # No quota requirement found, use default from settings
        from config.settings import DAILY_QUOTA
        return DAILY_QUOTA
    
    @classmethod
    async def get_all_for_month(cls, year: int, month: int) -> List['QuotaRequirement']:
        """
        Get all quota requirements for a specific month
        
        Args:
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
            SELECT id, effective_date, daily_quota, set_by
            FROM quota_requirements
            WHERE effective_date >= $1 AND effective_date < $2
            ORDER BY effective_date ASC
        """
        rows = await db.fetch(query, start_date, end_date)
        return [cls(**dict(row)) for row in rows]
    
    @classmethod
    async def get_all_current_month(cls, current_date: date) -> List['QuotaRequirement']:
        """Get all quota requirements for the current month"""
        return await cls.get_all_for_month(current_date.year, current_date.month)
    
    @classmethod
    async def clear_all(cls):
        """Clear all quota requirements (for monthly reset)"""
        query = "DELETE FROM quota_requirements"
        await db.execute(query)
        logger.info("Cleared all quota requirements for monthly reset")