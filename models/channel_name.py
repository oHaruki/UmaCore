"""
Channel name templates.

One row per Discord channel that a club renames to display its figures — usually
a locked voice channel sitting at the top of a server, which is what people
otherwise keep renaming by hand.

A row, not a column on ``clubs``, because a club may drive several channels at
once: rank in one, monthly fans in another, today's gain in a third.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from uuid import UUID
import logging

from config.database import db

logger = logging.getLogger(__name__)

_COLUMNS = """id, club_id, channel_id, template, enabled, last_rendered,
              last_updated, created_at"""


@dataclass
class ChannelName:
    """A channel whose name tracks one club's figures."""
    id: UUID
    club_id: UUID
    channel_id: int
    template: str
    enabled: bool
    last_rendered: Optional[str]
    last_updated: Optional[datetime]
    created_at: Optional[datetime]

    @classmethod
    async def upsert(cls, club_id: UUID, channel_id: int, template: str) -> 'ChannelName':
        """Bind a channel to a template, replacing any existing binding.

        ``channel_id`` is unique across the table, so re-pointing a channel at a
        different club moves it rather than giving it two competing templates.
        ``last_rendered`` is cleared so the next update always writes, even if the
        new template happens to render to the name the channel already has.
        """
        row = await db.fetchrow(
            f"""
            INSERT INTO club_channel_names (club_id, channel_id, template, enabled)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (channel_id) DO UPDATE SET
                club_id = $1, template = $3, enabled = TRUE, last_rendered = NULL
            RETURNING {_COLUMNS}
            """,
            club_id, channel_id, template,
        )
        logger.info(f"Channel name template set for channel {channel_id}: {template!r}")
        return cls(**dict(row))

    @classmethod
    async def get_for_club(cls, club_id: UUID) -> List['ChannelName']:
        rows = await db.fetch(
            f"SELECT {_COLUMNS} FROM club_channel_names WHERE club_id = $1 "
            f"ORDER BY created_at",
            club_id,
        )
        return [cls(**dict(r)) for r in rows]

    @classmethod
    async def get_enabled_for_club(cls, club_id: UUID) -> List['ChannelName']:
        rows = await db.fetch(
            f"SELECT {_COLUMNS} FROM club_channel_names "
            f"WHERE club_id = $1 AND enabled = TRUE ORDER BY created_at",
            club_id,
        )
        return [cls(**dict(r)) for r in rows]

    @classmethod
    async def get_by_channel(cls, channel_id: int) -> Optional['ChannelName']:
        row = await db.fetchrow(
            f"SELECT {_COLUMNS} FROM club_channel_names WHERE channel_id = $1",
            channel_id,
        )
        return cls(**dict(row)) if row else None

    @classmethod
    async def club_ids_with_templates(cls) -> List[UUID]:
        """Clubs with at least one enabled template.

        Used to widen the hourly poll: a club can want renamed channels without
        wanting a live board message, and it still needs the live fetch.
        """
        rows = await db.fetch(
            "SELECT DISTINCT club_id FROM club_channel_names WHERE enabled = TRUE"
        )
        return [r['club_id'] for r in rows]

    @classmethod
    async def remove(cls, channel_id: int) -> bool:
        """Unbind a channel. Returns False if it wasn't bound."""
        row = await db.fetchrow(
            "DELETE FROM club_channel_names WHERE channel_id = $1 RETURNING channel_id",
            channel_id,
        )
        return row is not None

    async def set_enabled(self, enabled: bool) -> None:
        await db.execute(
            "UPDATE club_channel_names SET enabled = $2 WHERE id = $1",
            self.id, enabled,
        )
        self.enabled = enabled

    async def mark_rendered(self, rendered: str) -> None:
        """Record the name just written, so an unchanged name costs no API call."""
        await db.execute(
            "UPDATE club_channel_names SET last_rendered = $2, last_updated = NOW() "
            "WHERE id = $1",
            self.id, rendered,
        )
        self.last_rendered = rendered
