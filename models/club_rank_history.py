"""
Club Rank History data model
"""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from uuid import UUID
import logging

from config.database import db

logger = logging.getLogger(__name__)


@dataclass
class ClubRankHistory:
    """Represents a club's rank snapshot on a given date"""
    id: Optional[UUID]
    club_id: UUID
    date: date
    club_rank: Optional[int]
    monthly_rank: Optional[int]
    scraped_at: datetime

    @classmethod
    async def save(cls, club_id: UUID, record_date: date,
                   club_rank: Optional[int], monthly_rank: Optional[int]) -> 'ClubRankHistory':
        """Upsert a rank snapshot for the given date (one record per club per day)."""
        query = """
            INSERT INTO club_rank_history (club_id, date, club_rank, monthly_rank, scraped_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (club_id, date)
            DO UPDATE SET
                club_rank = $3,
                monthly_rank = $4,
                scraped_at = NOW()
            RETURNING id, club_id, date, club_rank, monthly_rank, scraped_at
        """
        row = await db.fetchrow(query, club_id, record_date, club_rank, monthly_rank)
        return cls(**dict(row))

    @classmethod
    async def get_latest_monthly_rank(cls, club_id: UUID) -> Optional[int]:
        """Return the most recent known monthly_rank for a club, or None.

        Used to order the default-time scrape clump: clubs with no history yet
        (new clubs) return None and are dispatched on a fixed safe delay.
        """
        query = """
            SELECT monthly_rank
            FROM club_rank_history
            WHERE club_id = $1 AND monthly_rank IS NOT NULL
            ORDER BY date DESC
            LIMIT 1
        """
        row = await db.fetchrow(query, club_id)
        return row['monthly_rank'] if row else None

    @classmethod
    async def get_previous(cls, club_id: UUID, before_date: date) -> Optional['ClubRankHistory']:
        """Return the most recent rank record strictly before before_date."""
        query = """
            SELECT id, club_id, date, club_rank, monthly_rank, scraped_at
            FROM club_rank_history
            WHERE club_id = $1 AND date < $2
            ORDER BY date DESC
            LIMIT 1
        """
        row = await db.fetchrow(query, club_id, before_date)
        if row:
            return cls(**dict(row))
        return None
