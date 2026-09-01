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
from typing import List, Optional
import logging

import discord

from models import ClubPermission, GuildManagerRole

logger = logging.getLogger(__name__)


# Human labels for the channel permissions the bot asks about, so a command can
# name what is missing in the words Discord's own UI uses.
_PERMISSION_LABELS = {
    'manage_channels': 'Manage Channels',
    'view_channel': 'View Channel',
    'send_messages': 'Send Messages',
    'embed_links': 'Embed Links',
    'manage_messages': 'Manage Messages',
    'read_message_history': 'Read Message History',
}


def missing_channel_permissions(channel, me, *names: str) -> Optional[List[str]]:
    """Which of ``names`` the bot lacks on ``channel`` — ``None`` when unknown.

    Three answers, and the third is the point:

    * ``[]``      — the bot holds all of them
    * ``['...']`` — it is genuinely missing these
    * ``None``    — the cache gives no trustworthy answer; ask Discord instead

    ``permissions_for`` recomputes permissions locally from the cached guild and
    member. ``guild.me`` can arrive with an unpopulated role list — most reliably
    when the bot runs without the members intent, but also on a partial guild —
    and every role-granted permission then resolves to the ``@everyone`` baseline.
    A permission the bot actually holds reads False.

    :func:`is_admin` above documents the same trap for administrator, where a
    real server admin was refused a command while the interaction payload said
    administrator was True the whole time.

    So callers must treat ``None`` as "unknown", and treat a populated list as a
    warning rather than a verdict: only the API call itself is authoritative.
    Refusing to act on this reading is what turns a stale cache into a user who
    cannot set up a working feature.
    """
    if me is None or channel is None:
        return None

    try:
        perms = channel.permissions_for(me)
    except Exception:
        return None

    missing = [
        _PERMISSION_LABELS.get(n, n.replace('_', ' ').title())
        for n in names
        if not getattr(perms, n, False)
    ]

    if not missing:
        return []

    # A member whose roles never resolved carries @everyone alone. That is
    # indistinguishable from a bot that genuinely holds no roles, and the two
    # mistakes do not cost the same: a wrong "missing" blocks someone whose setup
    # is already correct, while a wrong "fine" costs one clear error from Discord.
    roles = getattr(me, 'roles', None)
    if not roles or len(roles) <= 1:
        return None

    return missing


def can_use_channel(channel, me, *names: str) -> Optional[bool]:
    """``missing_channel_permissions`` as a yes/no/unknown."""
    missing = missing_channel_permissions(channel, me, *names)
    return None if missing is None else not missing


def _member_role_ids(interaction: discord.Interaction) -> List[int]:
    """Role IDs the invoking member holds (empty in DMs / for non-Member users)."""
    user = interaction.user
    roles = getattr(user, 'roles', None)
    if not roles:
        return []
    # The @everyone role (== guild id) is excluded; it is never a meaningful binding.
    return [r.id for r in roles if r.id != interaction.guild_id]


def is_admin(interaction: discord.Interaction) -> bool:
    """True if the invoking member has Discord administrator in this guild.

    Reads ``interaction.permissions`` — the permissions Discord computed and sent
    with the interaction — rather than ``user.guild_permissions``, which discord.py
    recomputes locally from the cached guild and member.

    That local route fails open-ended: when the cached guild is a partial object
    its role cache is empty and ``owner_id`` is unset, so every member resolves to
    base permissions and administrator silently reads False. Measured 2026-08-05
    on guild 1426560692932317186, where a server admin was refused ``/remove_club``
    while ``interaction.permissions.administrator`` was True the whole time — the
    same reading ``app_commands.checks.has_permissions`` uses, which is why the
    admin-gated ``/add_manager_role`` had let him through minutes earlier.

    Administrator cannot be revoked by channel overwrites, so the channel-resolved
    permissions in the payload carry it faithfully.
    """
    perms = getattr(interaction, 'permissions', None)
    if perms is not None and perms.administrator:
        return True
    # Fall back to the member object for any context that carries no payload
    # permissions; it can only add a True, never mask one.
    member_perms = getattr(interaction.user, 'guild_permissions', None)
    return bool(member_perms and member_perms.administrator)


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
        await _log_denial(interaction, role_ids)
        return False
    if await GuildManagerRole.has_any_role(interaction.guild_id, role_ids):
        return True
    await _log_denial(interaction, role_ids)
    return False


async def _log_denial(interaction: discord.Interaction, role_ids: List[int]) -> None:
    """Record why a manager check failed. Denials were previously silent, which
    made "why can't I delete a club?" unanswerable from the server.

    Two fields matter most and are easy to confuse:

    ``member_perms``  administrator as computed from the cached Member object —
                      what :func:`is_admin` actually gates on.
    ``payload_perms`` administrator as Discord sent it in the interaction —
                      what ``app_commands.checks.has_permissions`` gates on.

    They should agree. When they don't, the user IS an admin and the member
    object failed to resolve, which silently empties their role list too and so
    fails both halves of the check at once.
    """
    try:
        user = interaction.user
        guild_id = interaction.guild_id
        member_perms = getattr(user, 'guild_permissions', None)
        payload_perms = getattr(interaction, 'permissions', None)
        managers = await GuildManagerRole.get_role_ids(guild_id) if guild_id else []
        everyone = [r for r in managers if r == guild_id]

        logger.info(
            "Manager check DENIED: user=%s (%s) guild=%s user_type=%s "
            "guild_cached=%s member_perms.admin=%s payload_perms.admin=%s "
            "roles_seen=%s manager_roles=%s%s",
            user, user.id, guild_id, type(user).__name__,
            interaction.guild is not None,
            getattr(member_perms, 'administrator', None),
            getattr(payload_perms, 'administrator', None),
            role_ids, managers,
            f" [@everyone bound as manager: {everyone} — can never match]" if everyone else "",
        )
    except Exception as e:                       # never break a command to log
        logger.warning(f"Could not log manager denial: {e}")


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
