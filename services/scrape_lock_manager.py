"""
Scrape lock manager to prevent concurrent scraping conflicts
"""
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID
import logging
import asyncio

from config.database import db

logger = logging.getLogger(__name__)


class ScrapeLockManager:
    """Manages scraping locks to prevent concurrent scrapes"""
    
    LOCK_TIMEOUT_MINUTES = 30  # Auto-release locks older than 30 minutes
    
    @staticmethod
    async def acquire_lock(club_id: UUID, locked_by: str = "bot") -> bool:
        """
        Try to acquire a scrape lock for a club
        
        Args:
            club_id: Club UUID
            locked_by: Identifier for who locked it
        
        Returns:
            True if lock acquired, False if already locked
        """
        try:
            # First, clean up stale locks
            await ScrapeLockManager._cleanup_stale_locks()
            
            # Try to insert lock
            query = """
                INSERT INTO scrape_locks (club_id, locked_at, locked_by)
                VALUES ($1, NOW(), $2)
                ON CONFLICT (club_id) DO NOTHING
                RETURNING club_id
            """
            result = await db.fetchrow(query, club_id, locked_by)
            
            if result:
                logger.info(f"Acquired scrape lock for club {club_id}")
                return True
            else:
                logger.warning(f"Could not acquire scrape lock for club {club_id} - already locked")
                return False
                
        except Exception as e:
            logger.error(f"Error acquiring scrape lock: {e}")
            return False
    
    @staticmethod
    async def release_lock(club_id: UUID):
        """Release a scrape lock"""
        try:
            query = "DELETE FROM scrape_locks WHERE club_id = $1"
            await db.execute(query, club_id)
            logger.info(f"Released scrape lock for club {club_id}")
        except Exception as e:
            logger.error(f"Error releasing scrape lock: {e}")
    
    @staticmethod
    async def is_locked(club_id: UUID) -> bool:
        """Check if a club is currently locked"""
        try:
            # Clean up stale locks first
            await ScrapeLockManager._cleanup_stale_locks()
            
            query = "SELECT club_id FROM scrape_locks WHERE club_id = $1"
            result = await db.fetchval(query, club_id)
            return result is not None
        except Exception as e:
            logger.error(f"Error checking scrape lock: {e}")
            return False
    
    @staticmethod
    async def get_lock_info(club_id: UUID) -> Optional[dict]:
        """Get information about a lock"""
        try:
            query = """
                SELECT club_id, locked_at, locked_by
                FROM scrape_locks
                WHERE club_id = $1
            """
            row = await db.fetchrow(query, club_id)
            if row:
                return dict(row)
            return None
        except Exception as e:
            logger.error(f"Error getting lock info: {e}")
            return None
    
    @staticmethod
    async def wait_for_lock(club_id: UUID, locked_by: str = "bot", 
                           max_wait_minutes: int = 10, check_interval: int = 30) -> bool:
        """
        Wait for a lock to become available
        
        Args:
            club_id: Club UUID
            locked_by: Identifier for who is waiting
            max_wait_minutes: Maximum time to wait
            check_interval: Seconds between checks
        
        Returns:
            True if lock acquired, False if timeout
        """
        end_time = datetime.now() + timedelta(minutes=max_wait_minutes)
        
        while datetime.now() < end_time:
            if await ScrapeLockManager.acquire_lock(club_id, locked_by):
                return True
            
            logger.info(f"Waiting for scrape lock on club {club_id}...")
            await asyncio.sleep(check_interval)
        
        logger.error(f"Timeout waiting for scrape lock on club {club_id}")
        return False
    
    @staticmethod
    async def _cleanup_stale_locks():
        """Remove locks older than LOCK_TIMEOUT_MINUTES"""
        try:
            timeout_threshold = datetime.now() - timedelta(minutes=ScrapeLockManager.LOCK_TIMEOUT_MINUTES)
            
            query = """
                DELETE FROM scrape_locks 
                WHERE locked_at < $1
                RETURNING club_id
            """
            result = await db.fetch(query, timeout_threshold)
            
            if result:
                count = len(result)
                logger.warning(f"Cleaned up {count} stale scrape lock(s)")
                
        except Exception as e:
            logger.error(f"Error cleaning up stale locks: {e}")
    
    @staticmethod
    async def force_release_all():
        """Force release all locks (use with caution)"""
        try:
            query = "DELETE FROM scrape_locks"
            await db.execute(query)
            logger.warning("Force released all scrape locks")
        except Exception as e:
            logger.error(f"Error force releasing locks: {e}")


class ScrapeContext:
    """Context manager for scrape locks"""
    
    def __init__(self, club_id: UUID, locked_by: str = "bot"):
        self.club_id = club_id
        self.locked_by = locked_by
        self.lock_acquired = False
    
    async def __aenter__(self):
        """Acquire lock when entering context"""
        self.lock_acquired = await ScrapeLockManager.acquire_lock(
            self.club_id, self.locked_by
        )
        if not self.lock_acquired:
            raise RuntimeError(f"Could not acquire scrape lock for club {self.club_id}")
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Release lock when exiting context"""
        if self.lock_acquired:
            await ScrapeLockManager.release_lock(self.club_id)
        return False
