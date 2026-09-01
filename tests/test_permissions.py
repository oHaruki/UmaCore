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
    describe_channel_overwrites, timeout_note, describe_channel_access,
    resolution_fingerprint,
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


class FakeRole:
    """Hashable, and carries no roles of its own — which is how the diagnostic
    tells a role target apart from a member one."""
    def __init__(self, id, name):
        self.id, self.name = id, name


class TestOverwriteDiagnostic:
    """Prints the bot's side of a channel's permissions.

    Added after a refusal that the server's own settings screen said could not
    happen. An override named after the bot is not necessarily one the bot is
    subject to, and that distinction is what the output has to make visible.
    """

    def channel(self, overwrites):
        return SimpleNamespace(
            id=1465928987216711774, name="Crew Rank: 825",
            overwrites=overwrites, category=SimpleNamespace(name="Info"),
            permissions_synced=False,
        )

    def bot(self, role_ids=(1, 2)):
        return SimpleNamespace(
            id=999, roles=[SimpleNamespace(id=i) for i in role_ids]
        )

    def test_marks_an_override_that_does_not_apply_to_the_bot(self):
        """A role called UmaCore that the bot does not actually hold — the exact
        thing that looks correct in the settings screen and does nothing."""
        role = FakeRole(555, "UmaCore")
        out = describe_channel_overwrites(
            self.channel({role: discord.PermissionOverwrite(manage_channels=True)}),
            self.bot(), 'manage_channels',
        )
        assert "applies to me: NO" in out
        assert "manage_channels=allow" in out

    def test_marks_an_override_that_does_apply(self):
        role = FakeRole(2, "UmaCore")
        out = describe_channel_overwrites(
            self.channel({role: discord.PermissionOverwrite(manage_channels=True)}),
            self.bot(role_ids=(1, 2)), 'manage_channels',
        )
        assert "applies to me: yes" in out

    def test_says_so_when_there_are_no_overwrites_at_all(self):
        """Distinguishes 'the setting never saved' from 'it saved as a deny'."""
        out = describe_channel_overwrites(self.channel({}), self.bot(), 'manage_channels')
        assert "no permission overwrites at all" in out

    def test_distinguishes_deny_from_inherit(self):
        role = FakeRole(2, "x")
        out = describe_channel_overwrites(
            self.channel({role: discord.PermissionOverwrite(manage_channels=False)}),
            self.bot(), 'manage_channels',
        )
        assert "manage_channels=deny" in out

    def test_carries_the_channel_and_category_identity(self):
        """So a report can be checked against the right channel — one candidate
        for a refusal is simply looking at a different one."""
        out = describe_channel_overwrites(self.channel({}), self.bot(), 'manage_channels')
        assert "1465928987216711774" in out and "Info" in out

    def test_never_raises_on_an_unreadable_channel(self):
        broken = SimpleNamespace()
        assert "couldn't read" in describe_channel_overwrites(
            broken, self.bot(), 'manage_channels')
        assert describe_channel_overwrites(None, self.bot()) == "no channel"


class TestTimeout:
    """The thing that counterfeits a permission problem exactly.

    A timed-out member keeps only View Channel and Read Message History, and
    discord.py applies that mask last as a conclusive override of every role and
    overwrite. Discord's API does the same. Diagnosed 2026-09-01 after three
    wrong guesses at a refusal whose channel overwrites plainly allowed it.
    """

    def member(self, timed_out, until=None):
        return SimpleNamespace(
            id=1467295225184784488,
            roles=[SimpleNamespace(id=i) for i in (1, 2, 3)],
            is_timed_out=lambda: timed_out,
            timed_out_until=until,
            _perms=discord.Permissions(view_channel=True),
        )

    def channel(self):
        return SimpleNamespace(
            id=1465928987216711774,
            permissions_for=lambda m: m._perms,
        )

    def test_silent_when_the_bot_is_not_timed_out(self):
        assert timeout_note(self.member(False)) is None

    def test_says_so_when_it_is(self):
        note = timeout_note(self.member(True))
        assert "timed out" in note.lower()
        assert "overrides all roles" in note

    def test_explains_that_grants_cannot_help(self):
        """The whole point: the channel's settings are correct and irrelevant."""
        note = timeout_note(self.member(True))
        assert "has any effect until it is lifted" in note

    def test_never_raises_on_something_that_is_not_a_member(self):
        assert timeout_note(object()) is None
        assert timeout_note(None) is None

    def test_the_access_line_leads_with_it(self):
        """It explains every other value on that line — View Channel yes and
        everything else no is the mask, not the server's configuration."""
        out = describe_channel_access(
            self.channel(), self.member(True), 'view_channel', 'manage_channels')
        assert out.startswith("TIMED OUT")
        assert "View Channel: yes" in out and "Manage Channels: no" in out

    def test_the_access_line_is_unchanged_without_a_timeout(self):
        out = describe_channel_access(
            self.channel(), self.member(False), 'view_channel', 'manage_channels')
        assert not out.startswith("TIMED OUT")


class TestResolutionFingerprint:
    """Says how a permission set was arrived at, not just what it says.

    Two of discord.py's paths return a fixed set and never consult the channel's
    overwrites: the timeout mask, and the user-installed-app set returned when
    the guild's @everyone role cannot be resolved. Both read as "View Channel:
    yes, everything else: no", which is indistinguishable from a locked-down
    channel by symptom alone — and that ambiguity survived four rounds of
    diagnosis on 2026-09-01.
    """

    def channel(self, perms, default_role=object(), roles=(1, 2)):
        return SimpleNamespace(
            permissions_for=lambda m: perms,
            guild=SimpleNamespace(id=99, default_role=default_role, roles=list(roles)),
        )

    def member(self, timed_out=False):
        return SimpleNamespace(id=7, is_timed_out=lambda: timed_out)

    def test_names_the_user_installed_set_exactly(self):
        ui = discord.Permissions._user_installed_permissions(in_guild=True)
        out = resolution_fingerprint(self.channel(ui, default_role=None), self.member())
        assert "user-installed-app permission set" in out
        assert "never applied any overwrite" in out

    def test_a_normal_denial_is_not_mistaken_for_it(self):
        """A channel that genuinely denies the permission must not be labelled."""
        out = resolution_fingerprint(
            self.channel(discord.Permissions(view_channel=True)), self.member())
        assert "user-installed" not in out

    def test_reports_a_timeout(self):
        out = resolution_fingerprint(
            self.channel(discord.Permissions(view_channel=True)),
            self.member(timed_out=True))
        assert "timed out" in out

    def test_reports_an_unpopulated_role_cache(self):
        out = resolution_fingerprint(
            self.channel(discord.Permissions(view_channel=True), default_role=None),
            self.member())
        assert "role cache is not populated" in out

    def test_always_carries_the_raw_value_for_a_report(self):
        out = resolution_fingerprint(
            self.channel(discord.Permissions(view_channel=True)), self.member())
        assert "value=" in out

    def test_never_raises(self):
        assert resolution_fingerprint(self.channel(None), None) == "no cached member"
        broken = SimpleNamespace(permissions_for=lambda m: (_ for _ in ()).throw(RuntimeError()))
        assert "unresolvable" in resolution_fingerprint(broken, self.member())
