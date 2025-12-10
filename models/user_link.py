"""
User Link data model for linking Discord users to trainers
"""
from dataclasses import dataclass
from typing import Optional, List
from uuid import UUID
import logging

from config.database import db

logger = logging.getLogger(__name__)


@dataclass
class UserLink:
    """Represents a link between a Discord user and a club member"""
    discord_user_id: int
    member_id: UUID
    notify_on_bombs: bool
    notify_on_deficit: bool
    created_at: Optional[str]
    updated_at: Optional[str]
    
    @classmethod
    async def create(cls, discord_user_id: int, member_id: UUID, 
                     notify_on_bombs: bool = True, notify_on_deficit: bool = False) -> 'UserLink':
        """Create a new user link"""
        query = """
            INSERT INTO user_links (discord_user_id, member_id, notify_on_bombs, notify_on_deficit)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (discord_user_id) 
            DO UPDATE SET 
                member_id = $2,
                notify_on_bombs = $3,
                notify_on_deficit = $4,
                updated_at = NOW()
            RETURNING discord_user_id, member_id, notify_on_bombs, notify_on_deficit, 
                      created_at, updated_at
        """
        row = await db.fetchrow(query, discord_user_id, member_id, notify_on_bombs, notify_on_deficit)
        logger.info(f"User link created/updated: Discord ID {discord_user_id} -> Member {member_id}")
        return cls(**dict(row))
    
    @classmethod
    async def get_by_discord_id(cls, discord_user_id: int) -> Optional['UserLink']:
        """Get user link by Discord user ID"""
        query = """
            SELECT discord_user_id, member_id, notify_on_bombs, notify_on_deficit, created_at, updated_at
            FROM user_links
            WHERE discord_user_id = $1
        """
        row = await db.fetchrow(query, discord_user_id)
        if row:
            return cls(**dict(row))
        return None
    
    @classmethod
    async def get_by_member_id(cls, member_id: UUID) -> Optional['UserLink']:
        """Get user link by member ID"""
        query = """
            SELECT discord_user_id, member_id, notify_on_bombs, notify_on_deficit, created_at, updated_at
            FROM user_links
            WHERE member_id = $1
        """
        row = await db.fetchrow(query, member_id)
        if row:
            return cls(**dict(row))
        return None
    
    @classmethod
    async def get_all_with_bomb_notifications(cls) -> List['UserLink']:
        """Get all users who want bomb notifications"""
        query = """
            SELECT discord_user_id, member_id, notify_on_bombs, notify_on_deficit, created_at, updated_at
            FROM user_links
            WHERE notify_on_bombs = TRUE
        """
        rows = await db.fetch(query)
        return [cls(**dict(row)) for row in rows]
    
    @classmethod
    async def get_all_with_deficit_notifications(cls) -> List['UserLink']:
        """Get all users who want deficit notifications"""
        query = """
            SELECT discord_user_id, member_id, notify_on_bombs, notify_on_deficit, created_at, updated_at
            FROM user_links
            WHERE notify_on_deficit = TRUE
        """
        rows = await db.fetch(query)
        return [cls(**dict(row)) for row in rows]
    
    async def update_notifications(self, notify_on_bombs: bool, notify_on_deficit: bool):
        """Update notification preferences"""
        query = """
            UPDATE user_links
            SET notify_on_bombs = $1, notify_on_deficit = $2, updated_at = NOW()
            WHERE discord_user_id = $3
        """
        await db.execute(query, notify_on_bombs, notify_on_deficit, self.discord_user_id)
        self.notify_on_bombs = notify_on_bombs
        self.notify_on_deficit = notify_on_deficit
        logger.info(f"Updated notifications for Discord ID {self.discord_user_id}")
    
    @classmethod
    async def delete(cls, discord_user_id: int):
        """Delete a user link"""
        query = "DELETE FROM user_links WHERE discord_user_id = $1"
        await db.execute(query, discord_user_id)
        logger.info(f"Deleted user link for Discord ID {discord_user_id}")
