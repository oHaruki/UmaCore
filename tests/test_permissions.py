"""Tests for the manager permission checks.

Built around one production incident: on 2026-08-05 a server administrator was
refused ``/remove_club`` on guild 1426560692792317186 while Discord's own payload
said he held administrator. ``is_admin`` was recomputing permissions from the
cached member object, which resolves to base permissions whenever the cached
guild is thin — silently turning an admin into a nobody.
"""
import asyncio
from types import SimpleNamespace

import discord
import pytest

from utils import permissions
from utils.permissions import (
    is_admin, is_full_manager, missing_channel_permissions, can_use_channel,
)

GUILD = 1426560692932317186


def interaction(*, payload_admin=False, member_admin=False, roles=(), guild=True):
    """A stand-in interaction.

    ``payload_admin`` is what Discord sent; ``member_admin`` is what discord.py
    would recompute from cache. They are separate on purpose — the whole point is
    what happens when they disagree.
    """
    user = SimpleNamespace(
        id=1065273301896798328,
        guild_permissions=discord.Permissions(administrator=member_admin),
        roles=[SimpleNamespace(id=r) for r in roles],
    )
    return SimpleNamespace(
        user=user,
        guild_id=GUILD,
        guild=SimpleNamespace(id=GUILD) if guild else None,
        permissions=discord.Permissions(administrator=payload_admin),
    )


@pytest.fixture
def no_manager_roles(monkeypatch):
    """No role-based access, so only the admin path can grant anything."""
    async def none(guild_id, role_ids):
        return False

    async def listing(guild_id):
        return [GUILD]                       # the @everyone binding, as in prod
    monkeypatch.setattr(permissions.GuildManagerRole, "has_any_role", none)
    monkeypatch.setattr(permissions.GuildManagerRole, "get_role_ids", listing)


class TestIsAdmin:
    def test_trusts_the_permissions_discord_sent(self):
        """The regression: cache says no, payload says yes. Payload wins."""
        assert is_admin(interaction(payload_admin=True, member_admin=False)) is True

    def test_still_accepts_a_cached_member_object(self):
        """No payload permissions (older/edge contexts) — don't lose the admin."""
        i = interaction(member_admin=True)
        i.permissions = None
        assert is_admin(i) is True

    def test_non_admin_stays_denied(self):
        assert is_admin(interaction()) is False

    def test_agreement_is_unremarkable(self):
        assert is_admin(interaction(payload_admin=True, member_admin=True)) is True


class TestFullManager:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_the_incident(self, no_manager_roles):
        """An admin whose roles came back empty must still be a full manager.

        This is the exact shape logged in production: user_type=Member,
        roles_seen=[], member_perms.admin=False, payload_perms.admin=True.
        """
        i = interaction(payload_admin=True, member_admin=False, roles=())
        assert self._run(is_full_manager(i)) is True

    def test_everyone_binding_grants_nothing_by_itself(self, no_manager_roles):
        """@everyone is stripped before the lookup, so a non-admin holding only it
        is denied — which is what made the success embed a false promise."""
        i = interaction(roles=(GUILD,))
        assert self._run(is_full_manager(i)) is False

    def test_real_manager_role_still_works(self, monkeypatch):
        async def has_role(guild_id, role_ids):
            return 999 in role_ids
        monkeypatch.setattr(permissions.GuildManagerRole, "has_any_role", has_role)
        i = interaction(roles=(GUILD, 999))
        assert self._run(is_full_manager(i)) is True

    def test_plain_member_is_denied(self, no_manager_roles):
        assert self._run(is_full_manager(interaction(roles=(GUILD, 42)))) is False


class TestDenialLogging:
    """Denials were silent, which is why the incident took a deploy to diagnose."""

    def test_logs_both_permission_readings(self, no_manager_roles, caplog):
        with caplog.at_level("INFO"):
            asyncio.run(is_full_manager(interaction(roles=(GUILD,))))
        msg = caplog.text
        assert "Manager check DENIED" in msg
        assert "member_perms.admin" in msg and "payload_perms.admin" in msg

    def test_calls_out_an_everyone_binding(self, no_manager_roles, caplog):
        with caplog.at_level("INFO"):
            asyncio.run(is_full_manager(interaction(roles=(GUILD,))))
        assert "can never match" in caplog.text

    def test_logging_never_breaks_the_check(self, monkeypatch):
        """A broken logger must not turn a denial into a crash mid-command."""
        async def boom(guild_id):
            raise RuntimeError("db down")
        monkeypatch.setattr(permissions.GuildManagerRole, "get_role_ids", boom)

        async def none(guild_id, role_ids):
            return False
        monkeypatch.setattr(permissions.GuildManagerRole, "has_any_role", none)
        assert asyncio.run(is_full_manager(interaction(roles=(GUILD, 7)))) is False


# --------------------------------------------------------------------------- #
# channel permissions
# --------------------------------------------------------------------------- #

class BotMember:
    """The bot as discord.py cached it. ``roles`` is the tell: a member whose
    roles never resolved carries @everyone alone and reads as holding nothing."""
    def __init__(self, roles=2, **perms):
        self._perms = discord.Permissions(**perms)
        self.roles = [SimpleNamespace(id=i) for i in range(roles)]


class BotChannel:
    def __init__(self, raises=False):
        self._raises = raises

    def permissions_for(self, member):
        if self._raises:
            raise RuntimeError("partial guild")
        return member._perms


class TestChannelPermissions:
    """Same incident as above, one layer down: a permission the bot holds reading
    False because the cached member never got its roles."""

    def test_empty_list_when_everything_is_held(self):
        me = BotMember(manage_channels=True, view_channel=True)
        assert missing_channel_permissions(
            BotChannel(), me, 'manage_channels', 'view_channel') == []

    def test_names_exactly_what_is_missing(self):
        me = BotMember(view_channel=True, send_messages=True)
        assert missing_channel_permissions(
            BotChannel(), me, 'view_channel', 'send_messages', 'embed_links'
        ) == ['Embed Links']

    def test_labels_match_discord_s_own_wording(self):
        me = BotMember()
        assert missing_channel_permissions(
            BotChannel(), me, 'manage_channels') == ['Manage Channels']

    def test_unknown_rather_than_missing_on_an_unresolved_role_list(self):
        """The failure that blocked channel-name setup: @everyone alone is not
        evidence the bot lacks the permission."""
        assert missing_channel_permissions(
            BotChannel(), BotMember(roles=1), 'manage_channels') is None
        assert missing_channel_permissions(
            BotChannel(), BotMember(roles=0), 'manage_channels') is None

    def test_a_resolved_member_can_still_be_genuinely_missing_it(self):
        assert missing_channel_permissions(
            BotChannel(), BotMember(roles=4), 'manage_channels') == ['Manage Channels']

    def test_unknown_without_a_cached_member(self):
        assert missing_channel_permissions(BotChannel(), None, 'manage_channels') is None

    def test_unknown_when_resolving_raises(self):
        assert missing_channel_permissions(
            BotChannel(raises=True), BotMember(roles=4), 'manage_channels') is None

    def test_a_held_permission_is_never_reported_unknown(self):
        """An unresolved role list cannot mask a True — permissions_for can only
        under-report, so anything it does grant is real."""
        me = BotMember(roles=1, manage_channels=True)
        assert missing_channel_permissions(BotChannel(), me, 'manage_channels') == []

    def test_can_use_channel_collapses_to_three_states(self):
        assert can_use_channel(BotChannel(), BotMember(manage_channels=True),
                               'manage_channels') is True
        assert can_use_channel(BotChannel(), BotMember(roles=4),
                               'manage_channels') is False
        assert can_use_channel(BotChannel(), None, 'manage_channels') is None
