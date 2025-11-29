"""
Bomb warning system data model
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional, List
from uuid import UUID
import logging

from config.database import db

logger = logging.getLogger(__name__)


@dataclass
class Bomb:
    """Represents an active bomb warning for a member"""
    bomb_id: Optional[UUID]
    member_id: UUID
    activation_date: date
    days_remaining: int
    is_active: bool
    deactivation_date: Optional[date]
    
    @classmethod
    async def create(cls, member_id: UUID, activation_date: date, days_remaining: int) -> 'Bomb':
        """Create a new bomb warning"""
        query = """
            INSERT INTO bombs (member_id, activation_date, days_remaining)
            VALUES ($1, $2, $3)
            RETURNING bomb_id, member_id, activation_date, days_remaining, is_active, deactivation_date
        """
        row = await db.fetchrow(query, member_id, activation_date, days_remaining)
        logger.warning(f"Bomb activated for member {member_id} with {days_remaining} days remaining")
        return cls(**dict(row))
    
    @classmethod
    async def get_active_for_member(cls, member_id: UUID) -> Optional['Bomb']:
        """Get active bomb for a member"""
        query = """
            SELECT bomb_id, member_id, activation_date, days_remaining, is_active, deactivation_date
            FROM bombs
            WHERE member_id = $1 AND is_active = TRUE
            ORDER BY activation_date DESC
            LIMIT 1
        """
        row = await db.fetchrow(query, member_id)
        if row:
            return cls(**dict(row))
        return None
    
    @classmethod
    async def get_all_active(cls) -> List['Bomb']:
        """Get all active bombs"""
        query = """
            SELECT bomb_id, member_id, activation_date, days_remaining, is_active, deactivation_date
            FROM bombs
            WHERE is_active = TRUE
            ORDER BY days_remaining ASC, activation_date ASC
        """
        rows = await db.fetch(query)
        return [cls(**dict(row)) for row in rows]
    
    async def deactivate(self, deactivation_date: date):
        """Deactivate the bomb (member got back on track)"""
        query = """
            UPDATE bombs
            SET is_active = FALSE, deactivation_date = $1
            WHERE bomb_id = $2
        """
        await db.execute(query, deactivation_date, self.bomb_id)
        self.is_active = False
        self.deactivation_date = deactivation_date
        logger.info(f"Bomb deactivated for member {self.member_id}")
    
    async def decrement_days(self):
        """Decrement days remaining"""
        if self.days_remaining > 0:
            self.days_remaining -= 1
            query = """
                UPDATE bombs
                SET days_remaining = $1
                WHERE bomb_id = $2
            """
            await db.execute(query, self.days_remaining, self.bomb_id)
            logger.info(f"Bomb countdown for member {self.member_id}: {self.days_remaining} days remaining")
    
    @classmethod
    async def clear_all(cls):
        """Clear all bombs (for monthly reset)"""
        query = "DELETE FROM bombs"
        await db.execute(query)
        logger.info("Cleared all bombs for monthly reset")
