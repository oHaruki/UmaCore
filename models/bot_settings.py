"""
Bot Settings data model
"""
from dataclasses import dataclass
from typing import Optional
import logging

from config.database import db

logger = logging.getLogger(__name__)


@dataclass
class BotSettings:
    """Represents bot configuration settings"""
    setting_key: str
    setting_value: str
    
    @classmethod
    async def get(cls, key: str) -> Optional[str]:
        """Get a setting value by key"""
        query = """
            SELECT setting_value
            FROM bot_settings
            WHERE setting_key = $1
        """
        result = await db.fetchval(query, key)
        return result
    
    @classmethod
    async def set(cls, key: str, value: str) -> 'BotSettings':
        """Set a setting value (upsert)"""
        query = """
            INSERT INTO bot_settings (setting_key, setting_value)
            VALUES ($1, $2)
            ON CONFLICT (setting_key) 
            DO UPDATE SET 
                setting_value = $2,
                updated_at = NOW()
            RETURNING setting_key, setting_value
        """
        row = await db.fetchrow(query, key, value)
        logger.info(f"Bot setting updated: {key} = {value}")
        return cls(**dict(row))
    
    @classmethod
    async def get_report_channel_id(cls) -> Optional[int]:
        """Get the report channel ID"""
        value = await cls.get('report_channel_id')
        return int(value) if value else None
    
    @classmethod
    async def get_alert_channel_id(cls) -> Optional[int]:
        """Get the alert channel ID"""
        value = await cls.get('alert_channel_id')
        return int(value) if value else None
    
    @classmethod
    async def set_report_channel_id(cls, channel_id: int):
        """Set the report channel ID"""
        await cls.set('report_channel_id', str(channel_id))
    
    @classmethod
    async def set_alert_channel_id(cls, channel_id: int):
        """Set the alert channel ID"""
        await cls.set('alert_channel_id', str(channel_id))
