"""
Club data model for multi-club support with circle_id validation
"""
from dataclasses import dataclass
from datetime import time
from typing import Optional, List
from uuid import UUID
import logging

from config.database import db

logger = logging.getLogger(__name__)


@dataclass
class Club:
    """Represents a club configuration"""
    club_id: UUID
    club_name: str
    scrape_url: str
    circle_id: Optional[str]
    guild_id: Optional[int]
    daily_quota: int
    timezone: str
    scrape_time: time
    bomb_trigger_days: int
    bomb_countdown_days: int
    is_active: bool
    report_channel_id: Optional[int]
    alert_channel_id: Optional[int]
    monthly_info_channel_id: Optional[int]
    monthly_info_message_id: Optional[int]
    created_at: Optional[str]
    updated_at: Optional[str]
    
    @classmethod
    async def create(cls, club_name: str, scrape_url: str, circle_id: Optional[str] = None,
                     guild_id: Optional[int] = None,
                     daily_quota: int = 1000000, timezone: str = 'Europe/Amsterdam', 
                     scrape_time: time = None, bomb_trigger_days: int = 3, 
                     bomb_countdown_days: int = 7) -> 'Club':
        """Create a new club"""
        if scrape_time is None:
            scrape_time = time(16, 0)
        
        query = """
            INSERT INTO clubs (club_name, scrape_url, circle_id, guild_id, daily_quota, timezone, scrape_time, 
                             bomb_trigger_days, bomb_countdown_days)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING club_id, club_name, scrape_url, circle_id, guild_id, daily_quota, timezone, scrape_time,
                     bomb_trigger_days, bomb_countdown_days, is_active, report_channel_id,
                     alert_channel_id, monthly_info_channel_id, monthly_info_message_id,
                     created_at, updated_at
        """
        row = await db.fetchrow(query, club_name, scrape_url, circle_id, guild_id, daily_quota, timezone, 
                                scrape_time, bomb_trigger_days, bomb_countdown_days)
        logger.info(f"Created new club: {club_name} (circle_id: {circle_id}, guild_id: {guild_id})")
        return cls(**dict(row))
    
    @classmethod
    async def get_by_id(cls, club_id: UUID) -> Optional['Club']:
        """Get club by ID"""
        query = """
            SELECT club_id, club_name, scrape_url, circle_id, guild_id, daily_quota, timezone, scrape_time,
                   bomb_trigger_days, bomb_countdown_days, is_active, report_channel_id,
                   alert_channel_id, monthly_info_channel_id, monthly_info_message_id,
                   created_at, updated_at
            FROM clubs
            WHERE club_id = $1
        """
        row = await db.fetchrow(query, club_id)
        if row:
            return cls(**dict(row))
        return None
    
    @classmethod
    async def get_by_name(cls, club_name: str) -> Optional['Club']:
        """Get club by name"""
        query = """
            SELECT club_id, club_name, scrape_url, circle_id, guild_id, daily_quota, timezone, scrape_time,
                   bomb_trigger_days, bomb_countdown_days, is_active, report_channel_id,
                   alert_channel_id, monthly_info_channel_id, monthly_info_message_id,
                   created_at, updated_at
            FROM clubs
            WHERE club_name = $1
        """
        row = await db.fetchrow(query, club_name)
        if row:
            return cls(**dict(row))
        return None
    
    @classmethod
    async def get_all_active(cls) -> List['Club']:
        """Get all active clubs"""
        query = """
            SELECT club_id, club_name, scrape_url, circle_id, guild_id, daily_quota, timezone, scrape_time,
                   bomb_trigger_days, bomb_countdown_days, is_active, report_channel_id,
                   alert_channel_id, monthly_info_channel_id, monthly_info_message_id,
                   created_at, updated_at
            FROM clubs
            WHERE is_active = TRUE
            ORDER BY club_name
        """
        rows = await db.fetch(query)
        return [cls(**dict(row)) for row in rows]
    
    @classmethod
    async def get_all(cls) -> List['Club']:
        """Get all clubs (active and inactive)"""
        query = """
            SELECT club_id, club_name, scrape_url, circle_id, guild_id, daily_quota, timezone, scrape_time,
                   bomb_trigger_days, bomb_countdown_days, is_active, report_channel_id,
                   alert_channel_id, monthly_info_channel_id, monthly_info_message_id,
                   created_at, updated_at
            FROM clubs
            ORDER BY club_name
        """
        rows = await db.fetch(query)
        return [cls(**dict(row)) for row in rows]
    
    @classmethod
    async def get_all_for_guild(cls, guild_id: int) -> List['Club']:
        """Get clubs registered to a specific guild, plus any pre-migration clubs (guild_id IS NULL)"""
        query = """
            SELECT club_id, club_name, scrape_url, circle_id, guild_id, daily_quota, timezone, scrape_time,
                   bomb_trigger_days, bomb_countdown_days, is_active, report_channel_id,
                   alert_channel_id, monthly_info_channel_id, monthly_info_message_id,
                   created_at, updated_at
            FROM clubs
            WHERE guild_id = $1 OR guild_id IS NULL
            ORDER BY club_name
        """
        rows = await db.fetch(query, guild_id)
        return [cls(**dict(row)) for row in rows]
    
    @classmethod
    async def get_all_names(cls) -> List[str]:
        """Get all active club names for autocomplete"""
        query = """
            SELECT club_name
            FROM clubs
            WHERE is_active = TRUE
            ORDER BY club_name
        """
        rows = await db.fetch(query)
        return [row['club_name'] for row in rows]
    
    @classmethod
    async def get_names_for_guild(cls, guild_id: int) -> List[str]:
        """Get active club names belonging to a specific guild, for autocomplete"""
        query = """
            SELECT club_name
            FROM clubs
            WHERE is_active = TRUE AND (guild_id = $1 OR guild_id IS NULL)
            ORDER BY club_name
        """
        rows = await db.fetch(query, guild_id)
        return [row['club_name'] for row in rows]
    
    async def update_settings(self, **kwargs):
        """Update club settings"""
        valid_fields = {'scrape_url', 'circle_id', 'daily_quota', 'timezone', 'scrape_time', 
                       'bomb_trigger_days', 'bomb_countdown_days', 'report_channel_id',
                       'alert_channel_id'}
        
        updates = {k: v for k, v in kwargs.items() if k in valid_fields}
        if not updates:
            return
        
        # Convert scrape_time string to time object if needed
        if 'scrape_time' in updates and isinstance(updates['scrape_time'], str):
            from datetime import time as time_class
            hour, minute = map(int, updates['scrape_time'].split(':'))
            updates['scrape_time'] = time_class(hour=hour, minute=minute)
        
        set_clause = ', '.join([f"{k} = ${i+2}" for i, k in enumerate(updates.keys())])
        values = [self.club_id] + list(updates.values())
        
        query = f"""
            UPDATE clubs
            SET {set_clause}, updated_at = NOW()
            WHERE club_id = $1
        """
        await db.execute(query, *values)
        
        for k, v in updates.items():
            setattr(self, k, v)
        
        logger.info(f"Updated club settings for {self.club_name}: {updates}")
    
    async def set_channels(self, report_channel_id: Optional[int] = None, 
                          alert_channel_id: Optional[int] = None):
        """
        Update one or both channel settings.
        Only modifies columns for arguments that are explicitly passed as non-None,
        leaving the other column untouched.
        """
        updates = {}
        if report_channel_id is not None:
            updates['report_channel_id'] = report_channel_id
        if alert_channel_id is not None:
            updates['alert_channel_id'] = alert_channel_id
        
        if not updates:
            return
        
        set_parts = []
        values = [self.club_id]
        for i, (col, val) in enumerate(updates.items(), start=2):
            set_parts.append(f"{col} = ${i}")
            values.append(val)
        
        query = f"""
            UPDATE clubs
            SET {', '.join(set_parts)}, updated_at = NOW()
            WHERE club_id = $1
        """
        await db.execute(query, *values)
        
        for k, v in updates.items():
            setattr(self, k, v)
        logger.info(f"Updated channels for {self.club_name}: {updates}")
    
    async def set_monthly_info_location(self, channel_id: int, message_id: int):
        """Set the monthly info message location for this club"""
        query = """
            UPDATE clubs
            SET monthly_info_channel_id = $2, monthly_info_message_id = $3, updated_at = NOW()
            WHERE club_id = $1
        """
        await db.execute(query, self.club_id, channel_id, message_id)
        self.monthly_info_channel_id = channel_id
        self.monthly_info_message_id = message_id
        logger.info(f"Set monthly info location for {self.club_name}: channel={channel_id}, message={message_id}")
    
    async def get_monthly_info_location(self) -> tuple[Optional[int], Optional[int]]:
        """Get the monthly info message location (channel_id, message_id)"""
        return self.monthly_info_channel_id, self.monthly_info_message_id
    
    async def deactivate(self):
        """Deactivate club"""
        query = """
            UPDATE clubs
            SET is_active = FALSE, updated_at = NOW()
            WHERE club_id = $1
        """
        await db.execute(query, self.club_id)
        self.is_active = False
        logger.info(f"Deactivated club: {self.club_name}")
    
    async def activate(self):
        """Activate club"""
        query = """
            UPDATE clubs
            SET is_active = TRUE, updated_at = NOW()
            WHERE club_id = $1
        """
        await db.execute(query, self.club_id)
        self.is_active = True
        logger.info(f"Activated club: {self.club_name}")
    
    async def delete(self):
        """
        Permanently delete club and all associated data.
        Cascades to members, quota_history, bombs, quota_requirements, scrape_locks.
        """
        query = "DELETE FROM clubs WHERE club_id = $1"
        await db.execute(query, self.club_id)
        logger.warning(f"Permanently deleted club: {self.club_name} (club_id: {self.club_id})")
    
    def belongs_to_guild(self, guild_id: int) -> bool:
        """
        Check whether this club is accessible from a given guild.
        Clubs without guild_id (created before the column existed) are
        treated as accessible until the backfill populates their value.
        """
        if self.guild_id is None:
            return True
        return self.guild_id == guild_id
    
    def get_scrape_time_str(self) -> str:
        """Get scrape time as HH:MM string"""
        if isinstance(self.scrape_time, time):
            return self.scrape_time.strftime('%H:%M')
        return str(self.scrape_time)
    
    def is_circle_id_valid(self) -> bool:
        """Check if circle_id is in the correct numeric format for Uma.moe API"""
        if not self.circle_id:
            return False
        return self.circle_id.isdigit()
    
    def get_uma_moe_url(self) -> str:
        """Get the Uma.moe URL for this club"""
        if self.circle_id and self.circle_id.isdigit():
            return f"https://uma.moe/circles/{self.circle_id}"
        return "https://uma.moe/circles/"
    
    def get_circle_id_help_message(self) -> str:
        """Get helpful error message for invalid circle_id"""
        return (
            f"⚠️ **Invalid Circle ID for {self.club_name}**\n\n"
            f"The circle_id must be a **numeric ID** from Uma.moe, not a club name.\n\n"
            f"**How to find your Circle ID:**\n"
            f"1. Go to https://uma.moe/circles/\n"
            f"2. Search for your club: **{self.club_name}**\n"
            f"3. Click on your club\n"
            f"4. Copy the **number** at the end of the URL\n"
            f"   Example: `https://uma.moe/circles/860280110` → Circle ID is `860280110`\n\n"
            f"**To fix this:**\n"
            f"Use `/edit_club club:{self.club_name} circle_id:<numeric_id>`"
        )