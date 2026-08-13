"""
Audit trail for admin actions taken through Discord commands.

Mirrors the web dashboard's ``logAudit`` (``umacore-web/src/lib/audit.ts``):
same ``audit_logs`` table, same action vocabulary, so the dashboard's Audit Log
page renders both sources as one timeline. Entries written here carry
``details.via = "discord"`` so the origin of a change stays visible.

Two conventions the dashboard depends on:

* ``details.guild_id`` is stamped on every entry. Guild-scoped actions (manager
  roles, club deletion) have no ``club_id`` to hang off, and the page uses this
  to show them on the club pages of that server.
* ``club_id`` must be left ``None`` for anything that deletes a club —
  ``audit_logs.club_id`` cascades on delete, so an entry pointing at the club it
  just deleted removes itself.

Writes are best-effort: a failed audit insert must never fail the command that
triggered it.
"""
import json
import logging
from typing import Any, Optional
from uuid import UUID

import discord

from config.database import db

logger = logging.getLogger(__name__)


def _as_uuid(value: Any) -> Optional[UUID]:
    """club_id is a UUID column — asyncpg wants a UUID, not its string form."""
    if value is None or isinstance(value, UUID):
        return value
    return UUID(str(value))


async def log_audit(
    interaction: discord.Interaction,
    action: str,
    entity_type: str,
    entity_id: Optional[Any] = None,
    club_id: Optional[Any] = None,
    details: Optional[dict] = None,
) -> None:
    """Record one admin action. Never raises."""
    try:
        payload = {"via": "discord"}
        if interaction.guild_id is not None:
            payload["guild_id"] = str(interaction.guild_id)
        if details:
            payload.update(details)

        # No casts: Postgres infers each parameter's type from the target column.
        await db.execute(
            """
            INSERT INTO audit_logs
                (actor_id, actor_name, action, entity_type, entity_id, club_id, details)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            str(interaction.user.id),
            interaction.user.display_name,
            action,
            entity_type,
            str(entity_id) if entity_id is not None else None,
            _as_uuid(club_id),
            json.dumps(payload),
        )
    except Exception as e:
        # Deliberately swallowed — the command already succeeded.
        logger.warning(f"Audit log write failed for '{action}': {e}")
