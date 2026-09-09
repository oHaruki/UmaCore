"""
Persistence for the event feed: who gets announcements, and what has been sent.

Announcement identity is global rather than per channel. A channel added later
therefore starts from whatever is already announced instead of replaying months
of history into a fresh channel; ``/events`` covers "what's on right now".
"""
import logging
from dataclasses import dataclass
from typing import Optional

from config.database import db

logger = logging.getLogger(__name__)


@dataclass
class EventFeedChannel:
    guild_id: int
    channel_id: int
    ping_role_id: Optional[int]

    @classmethod
    def _from_row(cls, row) -> 'EventFeedChannel':
        return cls(
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            ping_role_id=row["ping_role_id"],
        )

    @classmethod
    async def all(cls) -> list['EventFeedChannel']:
        rows = await db.fetch(
            "SELECT guild_id, channel_id, ping_role_id FROM event_feed_channels"
        )
        return [cls._from_row(r) for r in rows]

    @classmethod
    async def get(cls, guild_id: int) -> Optional['EventFeedChannel']:
        row = await db.fetchrow(
            "SELECT guild_id, channel_id, ping_role_id FROM event_feed_channels "
            "WHERE guild_id = $1",
            guild_id,
        )
        return cls._from_row(row) if row else None

    @classmethod
    async def set(cls, guild_id: int, channel_id: int,
                  ping_role_id: Optional[int] = None) -> 'EventFeedChannel':
        row = await db.fetchrow(
            """
            INSERT INTO event_feed_channels (guild_id, channel_id, ping_role_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id) DO UPDATE
                SET channel_id = $2, ping_role_id = $3, updated_at = NOW()
            RETURNING guild_id, channel_id, ping_role_id
            """,
            guild_id, channel_id, ping_role_id,
        )
        logger.info(f"Event feed for guild {guild_id} → channel {channel_id} "
                    f"(ping role {ping_role_id})")
        return cls._from_row(row)

    @classmethod
    async def clear(cls, guild_id: int) -> bool:
        result = await db.execute(
            "DELETE FROM event_feed_channels WHERE guild_id = $1", guild_id
        )
        # asyncpg returns the command tag, e.g. "DELETE 1" / "DELETE 0".
        removed = result.split()[-1] != "0"
        if removed:
            logger.info(f"Event feed disabled for guild {guild_id}")
        return removed


class AnnouncedEvents:
    """The set of event keys already posted."""

    @staticmethod
    async def keys() -> set[str]:
        rows = await db.fetch("SELECT event_key FROM announced_events")
        return {r["event_key"] for r in rows}

    @staticmethod
    async def count() -> int:
        return await db.fetchval("SELECT COUNT(*) FROM announced_events") or 0

    @staticmethod
    async def mark(key: str, kind: str, name: str,
                   start_ts: int, end_ts: Optional[int]) -> None:
        await db.execute(
            """
            INSERT INTO announced_events (event_key, kind, name, start_ts, end_ts)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (event_key) DO NOTHING
            """,
            key, kind, name, start_ts, end_ts,
        )

    @classmethod
    async def mark_many(cls, events) -> None:
        for event in events:
            await cls.mark(event.key, event.kind, event.name, event.start, event.end)
