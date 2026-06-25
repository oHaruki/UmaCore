"""
Per-club permission checks for slash commands.

Authorization model:
  - Discord administrators can manage / create / delete any club in their guild.
  - A user holding a role bound to a club (via club_role_permissions) can manage
    that club ONLY — never any other club.
  - Holding any editor role grants the ability to create new clubs, which are
    auto-bound to the creator's editor roles.
  - Club deletion stays administrator-only regardless of editor roles.
"""
from typing import List
import logging

import discord

from models import ClubPermission, GuildManagerRole

logger = logging.getLogger(__name__)


def _member_role_ids(interaction: discord.Interaction) -> List[int]:
    """Role IDs the invoking member holds (empty in DMs / for non-Member users)."""
    user = interaction.user
    roles = getattr(user, 'roles', None)
    if not roles:
        return []
    # The @everyone role (== guild id) is excluded; it is never a meaningful binding.
    return [r.id for r in roles if r.id != interaction.guild_id]


def is_admin(interaction: discord.Interaction) -> bool:
    """True if the invoking member has Discord administrator in this guild."""
    perms = getattr(interaction.user, 'guild_permissions', None)
    return bool(perms and perms.administrator)


async def is_full_manager(interaction: discord.Interaction) -> bool:
    """
    True if the user has full management powers over this guild's clubs:
    Discord administrator, OR holds a guild manager role.
    A full manager can manage/create/delete every club in the guild and assign
    club-editor roles (but cannot assign manager roles — that's admin-only).
    """
    if is_admin(interaction):
        return True
    role_ids = _member_role_ids(interaction)
    if not role_ids or interaction.guild_id is None:
        return False
    return await GuildManagerRole.has_any_role(interaction.guild_id, role_ids)


async def can_manage_club(interaction: discord.Interaction, club) -> bool:
    """
    True if the user may manage this specific club:
    full manager (admin / manager role), OR holds a role bound to THIS club.
    """
    if await is_full_manager(interaction):
        return True
    role_ids = _member_role_ids(interaction)
    if not role_ids:
        return False
    return await ClubPermission.has_any_role(club.club_id, role_ids)


async def creator_role_ids(interaction: discord.Interaction) -> List[int]:
    """
    Editor roles the user holds in this guild (roles bound to at least one club).
    Non-empty => the user may create new clubs; the returned roles are auto-bound
    to any club they create.
    """
    role_ids = _member_role_ids(interaction)
    if not role_ids:
        return []
    return await ClubPermission.get_editor_roles_in_guild(interaction.guild_id, role_ids)


async def can_create_club(interaction: discord.Interaction) -> bool:
    """True if the user may create a club: full manager OR holds any editor role."""
    if await is_full_manager(interaction):
        return True
    return len(await creator_role_ids(interaction)) > 0


async def ensure_can_manage(interaction: discord.Interaction, club) -> bool:
    """
    Guard for per-club management commands. Assumes the interaction has already
    been deferred. Sends an ephemeral-style error and returns False if denied.
    """
    if await can_manage_club(interaction, club):
        return True
    await interaction.followup.send(
        f"❌ You don't have permission to manage **{club.club_name}**.\n"
        f"You need Discord administrator, or a role assigned to this club by an admin."
    )
    return False
