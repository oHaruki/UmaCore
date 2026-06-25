"""
Guild manager roles.

A role listed here can manage every club in its guild — create, edit, delete,
and assign club-editor roles — i.e. the role-based equivalent of a Discord
administrator for this bot. Assigning manager roles themselves stays restricted
to Discord administrators (no self-escalation).
"""
from typing import List
import logging

from config.database import db

logger = logging.getLogger(__name__)


class GuildManagerRole:
    """Guild-wide manager role bindings"""

    @classmethod
    async def add(cls, guild_id: int, role_id: int) -> None:
        query = """
            INSERT INTO guild_manager_roles (guild_id, role_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id, role_id) DO NOTHING
        """
        await db.execute(query, guild_id, role_id)
        logger.info(f"Added manager role {role_id} for guild {guild_id}")

    @classmethod
    async def remove(cls, guild_id: int, role_id: int) -> None:
        query = "DELETE FROM guild_manager_roles WHERE guild_id = $1 AND role_id = $2"
        await db.execute(query, guild_id, role_id)
        logger.info(f"Removed manager role {role_id} for guild {guild_id}")

    @classmethod
    async def get_role_ids(cls, guild_id: int) -> List[int]:
        query = "SELECT role_id FROM guild_manager_roles WHERE guild_id = $1"
        rows = await db.fetch(query, guild_id)
        return [row['role_id'] for row in rows]

    @classmethod
    async def has_any_role(cls, guild_id: int, role_ids: List[int]) -> bool:
        """True if any of the given roles is a manager role in this guild."""
        if not role_ids:
            return False
        query = """
            SELECT 1 FROM guild_manager_roles
            WHERE guild_id = $1 AND role_id = ANY($2::bigint[])
            LIMIT 1
        """
        return await db.fetchval(query, guild_id, role_ids) is not None
