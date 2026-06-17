"""
Club editor role permissions.

Maps Discord roles to clubs they are allowed to manage. A user who holds any
role bound to a club may manage that club (and only that club). Holding any
editor role anywhere in a guild also grants the ability to create new clubs.
"""
from typing import List
from uuid import UUID
import logging

from config.database import db

logger = logging.getLogger(__name__)


class ClubPermission:
    """Role <-> club management bindings"""

    @classmethod
    async def add(cls, club_id: UUID, role_id: int) -> None:
        """Bind a role to a club (idempotent)"""
        query = """
            INSERT INTO club_role_permissions (club_id, role_id)
            VALUES ($1, $2)
            ON CONFLICT (club_id, role_id) DO NOTHING
        """
        await db.execute(query, club_id, role_id)
        logger.info(f"Bound role {role_id} to club {club_id}")

    @classmethod
    async def remove(cls, club_id: UUID, role_id: int) -> None:
        """Unbind a role from a club"""
        query = "DELETE FROM club_role_permissions WHERE club_id = $1 AND role_id = $2"
        await db.execute(query, club_id, role_id)
        logger.info(f"Unbound role {role_id} from club {club_id}")

    @classmethod
    async def get_role_ids(cls, club_id: UUID) -> List[int]:
        """All role IDs bound to a club"""
        query = "SELECT role_id FROM club_role_permissions WHERE club_id = $1"
        rows = await db.fetch(query, club_id)
        return [row['role_id'] for row in rows]

    @classmethod
    async def has_any_role(cls, club_id: UUID, role_ids: List[int]) -> bool:
        """True if any of the given roles is bound to this specific club"""
        if not role_ids:
            return False
        query = """
            SELECT 1 FROM club_role_permissions
            WHERE club_id = $1 AND role_id = ANY($2::bigint[])
            LIMIT 1
        """
        return await db.fetchval(query, club_id, role_ids) is not None

    @classmethod
    async def get_editor_roles_in_guild(cls, guild_id: int, role_ids: List[int]) -> List[int]:
        """
        Subset of the given role IDs that are bound to at least one club in this guild.
        Used to decide club-creation rights and which roles to auto-bind to a new club.
        """
        if not role_ids:
            return []
        query = """
            SELECT DISTINCT crp.role_id
            FROM club_role_permissions crp
            JOIN clubs c ON c.club_id = crp.club_id
            WHERE c.guild_id = $1 AND crp.role_id = ANY($2::bigint[])
        """
        rows = await db.fetch(query, guild_id, role_ids)
        return [row['role_id'] for row in rows]

    @classmethod
    async def get_club_ids_for_roles(cls, role_ids: List[int]) -> List[UUID]:
        """All club IDs that any of the given roles can manage"""
        if not role_ids:
            return []
        query = """
            SELECT DISTINCT club_id FROM club_role_permissions
            WHERE role_id = ANY($1::bigint[])
        """
        rows = await db.fetch(query, role_ids)
        return [row['club_id'] for row in rows]
