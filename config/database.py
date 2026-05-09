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
        """Initialize database schema with multi-club support"""
        schema_sql = """
        -- Enable UUID extension
        CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
        
        -- Clubs table
        CREATE TABLE IF NOT EXISTS clubs (
            club_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            club_name VARCHAR(100) UNIQUE NOT NULL,
            scrape_url TEXT NOT NULL,
            circle_id VARCHAR(100),
            daily_quota BIGINT NOT NULL DEFAULT 1000000,
            quota_period VARCHAR(10) NOT NULL DEFAULT 'daily',
            timezone VARCHAR(100) NOT NULL DEFAULT 'Europe/Amsterdam',
            scrape_time TIME NOT NULL DEFAULT '16:00',
            bomb_trigger_days INTEGER NOT NULL DEFAULT 3,
            bomb_countdown_days INTEGER NOT NULL DEFAULT 7,
            bombs_enabled BOOLEAN DEFAULT TRUE,
            is_active BOOLEAN DEFAULT TRUE,
            report_channel_id BIGINT,
            alert_channel_id BIGINT,
            monthly_info_channel_id BIGINT,
            monthly_info_message_id BIGINT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Migration: Add circle_id column if it doesn't exist
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='clubs' AND column_name='circle_id'
            ) THEN
                ALTER TABLE clubs ADD COLUMN circle_id VARCHAR(100);
                RAISE NOTICE 'Added circle_id column to clubs';
            END IF;
        END $$;
        
        -- Migration: Add monthly info tracking columns if they don't exist
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='clubs' AND column_name='monthly_info_channel_id'
            ) THEN
                ALTER TABLE clubs ADD COLUMN monthly_info_channel_id BIGINT;
                ALTER TABLE clubs ADD COLUMN monthly_info_message_id BIGINT;
                RAISE NOTICE 'Added monthly_info tracking columns to clubs';
            END IF;
        END $$;
        
        -- Migration: Add guild_id column if it doesn't exist
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='clubs' AND column_name='guild_id'
            ) THEN
                ALTER TABLE clubs ADD COLUMN guild_id BIGINT;
                RAISE NOTICE 'Added guild_id column to clubs';
            END IF;
        END $$;

        -- Migration: Add bombs_enabled column if it doesn't exist
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='clubs' AND column_name='bombs_enabled'
            ) THEN
                ALTER TABLE clubs ADD COLUMN bombs_enabled BOOLEAN DEFAULT TRUE;
                RAISE NOTICE 'Added bombs_enabled column to clubs';
            END IF;
        END $$;

        -- Migration: Add quota_period column if it doesn't exist
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='clubs' AND column_name='quota_period'
            ) THEN
                ALTER TABLE clubs ADD COLUMN quota_period VARCHAR(10) NOT NULL DEFAULT 'daily';
                RAISE NOTICE 'Added quota_period column to clubs';
            END IF;
        END $$;

        -- Migration: Add public_enabled column if it doesn't exist
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='clubs' AND column_name='public_enabled'
            ) THEN
                ALTER TABLE clubs ADD COLUMN public_enabled BOOLEAN NOT NULL DEFAULT FALSE;
                RAISE NOTICE 'Added public_enabled column to clubs';
            END IF;
        END $$;

        -- Migration: Add public_slug column if it doesn't exist
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='clubs' AND column_name='public_slug'
            ) THEN
                ALTER TABLE clubs ADD COLUMN public_slug TEXT;
                RAISE NOTICE 'Added public_slug column to clubs';
            END IF;
        END $$;

        -- Migration: Set public_slug from circle_id (authoritative source)
        -- Clubs sharing a circle_id get suffixes: 481227375, 481227375-2, 481227375-3, ...
        DO $$
        BEGIN
            UPDATE clubs SET public_slug = NULL;
            UPDATE clubs c
            SET public_slug = CASE WHEN ranked.rn = 1 THEN ranked.circle_id
                                   ELSE ranked.circle_id || '-' || ranked.rn::text END
            FROM (
                SELECT club_id, circle_id,
                       ROW_NUMBER() OVER (PARTITION BY circle_id ORDER BY club_name, club_id) AS rn
                FROM clubs
                WHERE circle_id IS NOT NULL AND circle_id != ''
            ) ranked
            WHERE c.club_id = ranked.club_id;
            RAISE NOTICE 'Synced public_slug from circle_id';
        END $$;

        -- Migration: Create partial unique index on public_slug if it doesn't exist
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'clubs' AND indexname = 'clubs_public_slug_unique'
            ) THEN
                CREATE UNIQUE INDEX clubs_public_slug_unique ON clubs(public_slug) WHERE public_slug IS NOT NULL;
                RAISE NOTICE 'Created unique index on public_slug';
            END IF;
        END $$;

        -- Members table
        CREATE TABLE IF NOT EXISTS members (
            member_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            club_id UUID NOT NULL REFERENCES clubs(club_id) ON DELETE CASCADE,
            trainer_id VARCHAR(50),
            trainer_name VARCHAR(100) NOT NULL,
            join_date DATE NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            manually_deactivated BOOLEAN DEFAULT FALSE,
            last_seen DATE NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Migration: Add club_id column if it doesn't exist
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='members' AND column_name='club_id'
            ) THEN
                ALTER TABLE members ADD COLUMN club_id UUID REFERENCES clubs(club_id) ON DELETE CASCADE;
                RAISE NOTICE 'Added club_id column to members';
            END IF;
        END $$;
        
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
        
        -- Migration: Add manually_deactivated column if it doesn't exist
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='members' AND column_name='manually_deactivated'
            ) THEN
                ALTER TABLE members ADD COLUMN manually_deactivated BOOLEAN DEFAULT FALSE;
                RAISE NOTICE 'Added manually_deactivated column';
            END IF;
        END $$;
        
        -- Quota history table
        CREATE TABLE IF NOT EXISTS quota_history (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            member_id UUID NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
            club_id UUID NOT NULL REFERENCES clubs(club_id) ON DELETE CASCADE,
            date DATE NOT NULL,
            cumulative_fans BIGINT NOT NULL,
            expected_fans BIGINT NOT NULL,
            deficit_surplus BIGINT NOT NULL,
            days_behind INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(member_id, date)
        );
        
        -- Migration: Add club_id to quota_history if it doesn't exist
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='quota_history' AND column_name='club_id'
            ) THEN
                ALTER TABLE quota_history ADD COLUMN club_id UUID REFERENCES clubs(club_id) ON DELETE CASCADE;
                RAISE NOTICE 'Added club_id column to quota_history';
            END IF;
        END $$;
        
        -- Bombs table
        CREATE TABLE IF NOT EXISTS bombs (
            bomb_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            member_id UUID NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
            club_id UUID NOT NULL REFERENCES clubs(club_id) ON DELETE CASCADE,
            activation_date DATE NOT NULL,
            days_remaining INTEGER NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            deactivation_date DATE,
            last_countdown_update DATE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Migration: Add last_countdown_update column to bombs if it doesn't exist
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='bombs' AND column_name='last_countdown_update'
            ) THEN
                ALTER TABLE bombs ADD COLUMN last_countdown_update DATE;
                UPDATE bombs SET last_countdown_update = activation_date WHERE last_countdown_update IS NULL;
                RAISE NOTICE 'Added last_countdown_update column to bombs table';
            END IF;
        END $$;
        
        -- Migration: Add club_id to bombs if it doesn't exist
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='bombs' AND column_name='club_id'
            ) THEN
                ALTER TABLE bombs ADD COLUMN club_id UUID REFERENCES clubs(club_id) ON DELETE CASCADE;
                RAISE NOTICE 'Added club_id column to bombs';
            END IF;
        END $$;
        
        -- Quota requirements table
        CREATE TABLE IF NOT EXISTS quota_requirements (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            club_id UUID NOT NULL REFERENCES clubs(club_id) ON DELETE CASCADE,
            effective_date DATE NOT NULL,
            daily_quota BIGINT NOT NULL,
            set_by VARCHAR(100),
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Migration: Add club_id to quota_requirements if it doesn't exist
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='quota_requirements' AND column_name='club_id'
            ) THEN
                ALTER TABLE quota_requirements ADD COLUMN club_id UUID REFERENCES clubs(club_id) ON DELETE CASCADE;
                RAISE NOTICE 'Added club_id column to quota_requirements';
            END IF;
        END $$;
        
        -- Scrape history table
        CREATE TABLE IF NOT EXISTS scrape_history (
            scrape_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            club_id UUID NOT NULL REFERENCES clubs(club_id) ON DELETE CASCADE,
            scrape_date TIMESTAMPTZ NOT NULL,
            success BOOLEAN NOT NULL,
            error_message TEXT,
            members_scraped INTEGER,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Migration: Add club_id to scrape_history if it doesn't exist
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='scrape_history' AND column_name='club_id'
            ) THEN
                ALTER TABLE scrape_history ADD COLUMN club_id UUID REFERENCES clubs(club_id) ON DELETE CASCADE;
                RAISE NOTICE 'Added club_id column to scrape_history';
            END IF;
        END $$;
        
        -- Bot settings table
        CREATE TABLE IF NOT EXISTS bot_settings (
            setting_key VARCHAR(100) PRIMARY KEY,
            setting_value TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- User links table
        CREATE TABLE IF NOT EXISTS user_links (
            discord_user_id BIGINT PRIMARY KEY,
            member_id UUID NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
            notify_on_bombs BOOLEAN DEFAULT TRUE,
            notify_on_deficit BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Scrape locks table
        CREATE TABLE IF NOT EXISTS scrape_locks (
            club_id UUID PRIMARY KEY REFERENCES clubs(club_id) ON DELETE CASCADE,
            locked_at TIMESTAMPTZ NOT NULL,
            locked_by VARCHAR(100) NOT NULL
        );
        
        -- Club rank history table
        CREATE TABLE IF NOT EXISTS club_rank_history (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            club_id UUID NOT NULL REFERENCES clubs(club_id) ON DELETE CASCADE,
            date DATE NOT NULL,
            club_rank INTEGER,
            monthly_rank INTEGER,
            scraped_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(club_id, date)
        );

        -- Migration: Add club_rank_history table if it doesn't exist (handled by CREATE TABLE IF NOT EXISTS above)
        CREATE INDEX IF NOT EXISTS idx_club_rank_history_club_date
            ON club_rank_history(club_id, date DESC);

        -- Indexes for performance
        CREATE INDEX IF NOT EXISTS idx_members_club_id ON members(club_id);
        CREATE INDEX IF NOT EXISTS idx_quota_history_club_id ON quota_history(club_id);
        CREATE INDEX IF NOT EXISTS idx_bombs_club_id ON bombs(club_id);
        CREATE INDEX IF NOT EXISTS idx_quota_requirements_club_id ON quota_requirements(club_id);
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
        CREATE INDEX IF NOT EXISTS idx_user_links_member_id
            ON user_links(member_id);
        
        -- Unique constraint for trainer_id per club
        DROP INDEX IF EXISTS members_trainer_id_key;
        CREATE UNIQUE INDEX IF NOT EXISTS members_trainer_id_club_unique
            ON members(trainer_id, club_id) WHERE trainer_id IS NOT NULL;

        -- Audit log table (web dashboard actions)
        CREATE TABLE IF NOT EXISTS audit_logs (
            id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
            actor_id    TEXT        NOT NULL,
            actor_name  TEXT        NOT NULL,
            action      TEXT        NOT NULL,
            entity_type TEXT        NOT NULL,
            entity_id   TEXT,
            club_id     UUID        REFERENCES clubs(club_id) ON DELETE CASCADE,
            details     JSONB,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_audit_logs_club_created
            ON audit_logs(club_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_logs_created
            ON audit_logs(created_at DESC);
        """
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(schema_sql)
            logger.info("Database schema initialized successfully with multi-club support and monthly info tracking")
        except Exception as e:
            logger.error(f"Failed to initialize schema: {e}")
            raise


# Global database instance
db = Database(url="")