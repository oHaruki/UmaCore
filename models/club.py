"""
Club data model for multi-club support
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
    daily_quota: int
    timezone: str
    scrape_time: time
    bomb_trigger_days: int
    bomb_countdown_days: int
    is_active: bool
    report_channel_id: Optional[int]
    alert_channel_id: Optional[int]
    created_at: Optional[str]
    updated_at: Optional[str]
    
    @classmethod
    async def create(cls, club_name: str, scrape_url: str, daily_quota: int = 1000000,
                     timezone: str = 'Europe/Amsterdam', scrape_time: time = None,
                     bomb_trigger_days: int = 3, bomb_countdown_days: int = 7) -> 'Club':
        """Create a new club"""
        # Default scrape time if not provided
        if scrape_time is None:
            scrape_time = time(16, 0)  # 16:00
        
        query = """
            INSERT INTO clubs (club_name, scrape_url, daily_quota, timezone, scrape_time, 
                             bomb_trigger_days, bomb_countdown_days)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING club_id, club_name, scrape_url, daily_quota, timezone, scrape_time,
                     bomb_trigger_days, bomb_countdown_days, is_active, report_channel_id,
                     alert_channel_id, created_at, updated_at
        """
        row = await db.fetchrow(query, club_name, scrape_url, daily_quota, timezone, 
                                scrape_time, bomb_trigger_days, bomb_countdown_days)
        logger.info(f"Created new club: {club_name}")
        return cls(**dict(row))
    
    @classmethod
    async def get_by_id(cls, club_id: UUID) -> Optional['Club']:
        """Get club by ID"""
        query = """
            SELECT club_id, club_name, scrape_url, daily_quota, timezone, scrape_time,
                   bomb_trigger_days, bomb_countdown_days, is_active, report_channel_id,
                   alert_channel_id, created_at, updated_at
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
            SELECT club_id, club_name, scrape_url, daily_quota, timezone, scrape_time,
                   bomb_trigger_days, bomb_countdown_days, is_active, report_channel_id,
                   alert_channel_id, created_at, updated_at
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
            SELECT club_id, club_name, scrape_url, daily_quota, timezone, scrape_time,
                   bomb_trigger_days, bomb_countdown_days, is_active, report_channel_id,
                   alert_channel_id, created_at, updated_at
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
            SELECT club_id, club_name, scrape_url, daily_quota, timezone, scrape_time,
                   bomb_trigger_days, bomb_countdown_days, is_active, report_channel_id,
                   alert_channel_id, created_at, updated_at
            FROM clubs
            ORDER BY club_name
        """
        rows = await db.fetch(query)
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
    
    async def update_settings(self, **kwargs):
        """Update club settings"""
        valid_fields = {'scrape_url', 'daily_quota', 'timezone', 'scrape_time', 
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
        """Set report and alert channels"""
        query = """
            UPDATE clubs
            SET report_channel_id = $2, alert_channel_id = $3, updated_at = NOW()
            WHERE club_id = $1
        """
        await db.execute(query, self.club_id, report_channel_id, alert_channel_id)
        self.report_channel_id = report_channel_id
        self.alert_channel_id = alert_channel_id
        logger.info(f"Updated channels for {self.club_name}")
    
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
    
    def get_scrape_time_str(self) -> str:
        """Get scrape time as HH:MM string"""
        if isinstance(self.scrape_time, time):
            return self.scrape_time.strftime('%H:%M')
        return str(self.scrape_time)
