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
from utils.permissions import is_admin, is_full_manager

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
