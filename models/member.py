"""
Member data model
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional
from uuid import UUID
import logging

from config.database import db

logger = logging.getLogger(__name__)


@dataclass
class Member:
    """Represents a club member"""
    member_id: Optional[UUID]
    club_id: UUID
    trainer_id: Optional[str]
    trainer_name: str
    join_date: date
    is_active: bool
    manually_deactivated: bool
    last_seen: date
    
    @classmethod
    async def create(cls, club_id: UUID, trainer_name: str, join_date: date, trainer_id: Optional[str] = None) -> 'Member':
        """Create a new member"""
        query = """
            INSERT INTO members (club_id, trainer_id, trainer_name, join_date, last_seen)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING member_id, club_id, trainer_id, trainer_name, join_date, is_active, manually_deactivated, last_seen
        """
        row = await db.fetchrow(query, club_id, trainer_id, trainer_name, join_date, join_date)
        logger.info(f"Created new member: {trainer_name} (ID: {trainer_id}) for club {club_id}")
        return cls(**dict(row))
    
    @classmethod
    async def get_by_trainer_id(cls, club_id: UUID, trainer_id: str) -> Optional['Member']:
        """Get member by trainer ID within a club"""
        query = """
            SELECT member_id, club_id, trainer_id, trainer_name, join_date, is_active, manually_deactivated, last_seen
            FROM members
            WHERE club_id = $1 AND trainer_id = $2
        """
        row = await db.fetchrow(query, club_id, trainer_id)
        if row:
            return cls(**dict(row))
        return None
    
    @classmethod
    async def get_by_name(cls, club_id: UUID, trainer_name: str) -> Optional['Member']:
        """Get member by trainer name within a club"""
        query = """
            SELECT member_id, club_id, trainer_id, trainer_name, join_date, is_active, manually_deactivated, last_seen
            FROM members
            WHERE club_id = $1 AND trainer_name = $2
        """
        row = await db.fetchrow(query, club_id, trainer_name)
        if row:
            return cls(**dict(row))
        return None
    
    @classmethod
    async def get_by_id(cls, member_id: UUID) -> Optional['Member']:
        """Get member by UUID"""
        query = """
            SELECT member_id, club_id, trainer_id, trainer_name, join_date, is_active, manually_deactivated, last_seen
            FROM members
            WHERE member_id = $1
        """
        row = await db.fetchrow(query, member_id)
        if row:
            return cls(**dict(row))
        return None
    
    @classmethod
    async def get_all_active(cls, club_id: UUID) -> list['Member']:
        """Get all active members for a club"""
        query = """
            SELECT member_id, club_id, trainer_id, trainer_name, join_date, is_active, manually_deactivated, last_seen
            FROM members
            WHERE club_id = $1 AND is_active = TRUE
            ORDER BY trainer_name
        """
        rows = await db.fetch(query, club_id)
        return [cls(**dict(row)) for row in rows]
    
    async def update_last_seen(self, last_seen: date):
        """Update last seen date"""
        query = """
            UPDATE members
            SET last_seen = $1, updated_at = NOW()
            WHERE member_id = $2
        """
        await db.execute(query, last_seen, self.member_id)
        self.last_seen = last_seen
    
    async def update_name(self, new_name: str):
        """Update trainer name"""
        query = """
            UPDATE members
            SET trainer_name = $1, updated_at = NOW()
            WHERE member_id = $2
        """
        await db.execute(query, new_name, self.member_id)
        self.trainer_name = new_name
        logger.info(f"Updated trainer name to: {new_name} (ID: {self.trainer_id})")
    
    async def deactivate(self, manual: bool = False):
        """Deactivate member"""
        query = """
            UPDATE members
            SET is_active = FALSE, manually_deactivated = $1, updated_at = NOW()
            WHERE member_id = $2
        """
        await db.execute(query, manual, self.member_id)
        self.is_active = False
        self.manually_deactivated = manual
        
        if manual:
            logger.info(f"Manually deactivated member: {self.trainer_name}")
        else:
            logger.info(f"Auto-deactivated member: {self.trainer_name}")
    
    async def activate(self):
        """Activate member and clear manual deactivation flag"""
        query = """
            UPDATE members
            SET is_active = TRUE, manually_deactivated = FALSE, updated_at = NOW()
            WHERE member_id = $1
        """
        await db.execute(query, self.member_id)
        self.is_active = True
        self.manually_deactivated = False
        logger.info(f"Activated member: {self.trainer_name}")
