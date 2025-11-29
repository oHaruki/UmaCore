"""
PostgreSQL database connection management
"""
import asyncpg
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class Database:
    """Async PostgreSQL database manager with connection pooling"""
    
    def __init__(self, url: str):
        self.url = url
        self.pool: Optional[asyncpg.Pool] = None
    
    async def connect(self):
        """Create connection pool"""
        try:
            self.pool = await asyncpg.create_pool(
                self.url,
                min_size=1,
                max_size=5,
                command_timeout=60
            )
            logger.info("Database connection pool established")
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise
    
    async def disconnect(self):
        """Close connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("Database connection pool closed")
    
    async def execute(self, query: str, *args) -> str:
        """Execute a query (INSERT, UPDATE, DELETE)"""
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)
    
    async def fetch(self, query: str, *args) -> List[asyncpg.Record]:
        """Fetch multiple rows"""
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)
    
    async def fetchrow(self, query: str, *args) -> Optional[asyncpg.Record]:
        """Fetch single row"""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)
    
    async def fetchval(self, query: str, *args) -> Any:
        """Fetch single value"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)
    
    async def initialize_schema(self):
        """Initialize database schema"""
        schema_sql = """
        -- Enable UUID extension
        CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
        
        -- Members table
        CREATE TABLE IF NOT EXISTS members (
            member_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            trainer_id VARCHAR(50) UNIQUE,  -- Trainer ID from game (unique identifier)
            trainer_name VARCHAR(100) NOT NULL,  -- Display name (can change, NOT unique)
            join_date DATE NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            last_seen DATE NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Migration: Add trainer_id column if it doesn't exist
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='members' AND column_name='trainer_id'
            ) THEN
                ALTER TABLE members ADD COLUMN trainer_id VARCHAR(50);
                RAISE NOTICE 'Added trainer_id column';
            END IF;
        END $$;
        
        -- Migration: Remove UNIQUE constraint from trainer_name if it exists
        DO $$
        BEGIN
            -- Drop the unique constraint on trainer_name if it exists
            IF EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'members_trainer_name_key'
            ) THEN
                ALTER TABLE members DROP CONSTRAINT members_trainer_name_key;
                RAISE NOTICE 'Removed UNIQUE constraint from trainer_name';
            END IF;
        END $$;
        
        -- Migration: Add UNIQUE constraint to trainer_id if it doesn't exist
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'members_trainer_id_key'
            ) THEN
                ALTER TABLE members ADD CONSTRAINT members_trainer_id_key UNIQUE (trainer_id);
                RAISE NOTICE 'Added UNIQUE constraint to trainer_id';
            END IF;
        END $$;
        
        -- Quota history table
        CREATE TABLE IF NOT EXISTS quota_history (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            member_id UUID NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
            date DATE NOT NULL,
            cumulative_fans BIGINT NOT NULL,
            expected_fans BIGINT NOT NULL,
            deficit_surplus BIGINT NOT NULL,
            days_behind INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(member_id, date)
        );
        
        -- Bombs table
        CREATE TABLE IF NOT EXISTS bombs (
            bomb_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            member_id UUID NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
            activation_date DATE NOT NULL,
            days_remaining INTEGER NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            deactivation_date DATE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Quota requirements table (NEW - for dynamic quota management)
        CREATE TABLE IF NOT EXISTS quota_requirements (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            effective_date DATE NOT NULL,
            daily_quota BIGINT NOT NULL,
            set_by VARCHAR(100),
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Scrape history table
        CREATE TABLE IF NOT EXISTS scrape_history (
            scrape_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            scrape_date TIMESTAMPTZ NOT NULL,
            success BOOLEAN NOT NULL,
            error_message TEXT,
            members_scraped INTEGER,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Indexes for performance
        CREATE INDEX IF NOT EXISTS idx_quota_history_member_date 
            ON quota_history(member_id, date DESC);
        CREATE INDEX IF NOT EXISTS idx_bombs_active 
            ON bombs(member_id) WHERE is_active = TRUE;
        CREATE INDEX IF NOT EXISTS idx_members_active 
            ON members(is_active) WHERE is_active = TRUE;
        CREATE INDEX IF NOT EXISTS idx_members_trainer_id
            ON members(trainer_id);
        CREATE INDEX IF NOT EXISTS idx_quota_requirements_date
            ON quota_requirements(effective_date DESC);
        """
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(schema_sql)
            logger.info("Database schema initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize schema: {e}")
            raise


# Global database instance
db = Database(url="")  # Will be set in main.py