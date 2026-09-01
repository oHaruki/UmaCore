"""Tests for channel names that track a club's figures.

Two things could go wrong here at any real scale, and both are covered:
Discord's rename throttle being provoked (the updater must not call the API when
nothing changed, or twice in quick succession), and a template rendering to
something Discord will reject.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import discord
import pytest

from scrapers.umamoe_api_scraper import CircleMeta, LiveSnapshot, MemberGain
from services import channel_names as cn

UTC = timezone.utc


def club(**kw):
    base = dict(club_id=uuid4(), club_name="TestClub", circle_id="1",
                live_board_channel_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


def snap(**kw):
    base = dict(
        circle_id="1", jst_day=date(2026, 7, 25),
        as_of=datetime(2026, 7, 25, 12, tzinfo=UTC),
        live_points=1_837_269_789, live_rank=87,
        monthly_point=1_795_000_000, monthly_rank=91,
        gains=[MemberGain("1", "Alpha", 5_000_000, 60_000_000),
               MemberGain("2", "Beta", 3_000_000, 40_000_000)],
    )
    base.update(kw)
    return LiveSnapshot(**base)


def meta(**kw):
    base = dict(name="TestClub", member_count=30, monthly_rank=87,
                last_month_rank=104, yesterday_rank=91, live_rank=None,
                monthly_point=1_837_269_789, yesterday_points=1_795_000_000,
                live_points=None)
    base.update(kw)
    return CircleMeta(**base)


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

class TestRendering:
    def test_live_context_prefers_the_live_rank(self):
        ctx = cn.context_from_live(club(), snap())
        assert cn.render("Rank #{rank}", ctx) == "Rank #87"

    def test_live_context_falls_back_when_no_live_rank_is_published(self):
        """A newly opened competition month serves none for a while — showing the
        monthly rank beats showing a dash on a channel everyone can see."""
        ctx = cn.context_from_live(club(), snap(live_rank=None))
        assert cn.render("Rank #{rank}", ctx) == "Rank #91"

    def test_daily_context_reads_the_finalized_figures(self):
        ctx = cn.context_from_meta(club(), meta())
        assert cn.render("#{rank} · {fans}", ctx) == "#87 · 1.84B"

    def test_daily_context_derives_the_day_from_the_two_totals(self):
        ctx = cn.context_from_meta(club(), meta())
        assert cn.render("{fans_today}", ctx) == "42.3M"

    def test_delta_signs_a_climb_as_positive(self):
        """live 87 vs monthly 91 means the club moved four places up."""
        assert cn.render("{delta}", cn.context_from_live(club(), snap())) == "+4"
        assert cn.render("{delta}", cn.context_from_live(
            club(), snap(live_rank=95))) == "-4"

    def test_delta_reads_as_flat_rather_than_plus_zero(self):
        assert cn.render("{delta}", cn.context_from_live(
            club(), snap(live_rank=91))) == "="

    def test_compact_figures_stay_short_enough_for_a_name(self):
        # Two decimals under 10 units, one above — every case stays <= 6 chars.
        for value, expected in ((1_837_269_789, "1.84B"), (42_300_000, "42.3M"),
                                (999, "999"), (1_250, "1.25K")):
            ctx = cn.NameContext(club_name="c", fans=value)
            assert cn.render("{fans}", ctx) == expected

    def test_missing_figures_render_as_a_placeholder_not_none(self):
        assert cn.render("{rank}", cn.NameContext(club_name="c")) == cn.PLACEHOLDER

    def test_thousands_separators_on_a_large_rank(self):
        ctx = cn.NameContext(club_name="c", rank=12345)
        assert cn.render("#{rank}", ctx) == "#12,345"

    def test_grade_comes_from_the_rank(self):
        ctx = cn.context_from_live(club(), snap())
        assert cn.render("{grade}", ctx) not in ("", cn.PLACEHOLDER)

    def test_literal_text_and_emoji_survive(self):
        ctx = cn.context_from_live(club(), snap())
        assert cn.render("🏆│Rank {rank}", ctx) == "🏆│Rank 87"

    def test_name_is_clamped_to_discord_s_limit(self):
        ctx = cn.context_from_live(club(), snap())
        out = cn.render("x" * 200 + "{rank}", ctx)
        assert len(out) == cn.MAX_NAME_LENGTH

    def test_unknown_tokens_are_reported_for_validation(self):
        assert cn.unknown_tokens("Rank {rank} {bogus} {alsobad}") == ["alsobad", "bogus"]
        assert cn.unknown_tokens("Rank {rank} · {fans}") == []

    def test_unknown_tokens_render_verbatim_rather_than_vanishing(self):
        """A typo visible in the channel name is diagnosed in seconds; a silent
        blank is not."""
        ctx = cn.context_from_live(club(), snap())
        assert cn.render("{rank} {bogus}", ctx) == "87 {bogus}"

    def test_preview_never_shows_placeholders(self):
        for token in cn.TOKENS:
            assert cn.PLACEHOLDER not in cn.preview("{" + token + "}")


# --------------------------------------------------------------------------- #
# applying — where the rate limit lives
# --------------------------------------------------------------------------- #

class FakeChannel:
    def __init__(self, cid=555, raises=None, guild=None):
        self.id, self.name, self.edits, self._raises = cid, "old-name", [], raises
        self.guild = guild

    async def edit(self, *, name, reason=None):
        if self._raises:
            raise self._raises
        self.name = name
        self.edits.append(name)


class FakeBot:
    def __init__(self, channel): self._c = channel
    def get_channel(self, _): return self._c


class FakeRow:
    """Stands in for a ChannelName row, recording what got persisted."""
    def __init__(self, template="Rank #{rank}", channel_id=555, last_rendered=None):
        self.id = uuid4()
        self.channel_id, self.template = channel_id, template
        self.last_rendered, self.enabled = last_rendered, True

    async def mark_rendered(self, rendered):
        self.last_rendered = rendered


@pytest.fixture
def wired(monkeypatch):
    """channel_names with a fake Discord and stubbed persistence."""
    state = {"rows": [FakeRow()], "removed": []}

    async def get_enabled(_club_id):
        return state["rows"]

    async def remove(channel_id):
        state["removed"].append(channel_id)
        return True

    monkeypatch.setattr(cn.ChannelName, "get_enabled_for_club", get_enabled)
    monkeypatch.setattr(cn.ChannelName, "remove", remove)
    cn._last_rename.clear()
    channel = FakeChannel()
    return SimpleNamespace(bot=FakeBot(channel), channel=channel, state=state)


def run(coro):
    return asyncio.run(coro)


class TestApply:
    def _ctx(self):
        return cn.context_from_live(club(), snap())

    def test_writes_the_rendered_name(self, wired):
        result = run(cn.apply_for_club(wired.bot, club(), self._ctx()))
        assert wired.channel.edits == ["Rank #87"]
        assert result["updated"] == 1

    def test_an_unchanged_name_costs_no_api_call(self, wired):
        """The whole reason renames stay inside Discord's throttle: rank rarely
        moves between two polls an hour apart."""
        wired.state["rows"] = [FakeRow(last_rendered="Rank #87")]
        result = run(cn.apply_for_club(wired.bot, club(), self._ctx()))
        assert wired.channel.edits == []
        assert result["skipped"] == 1

    def test_a_second_scheduled_update_holds_the_interval_floor(self, wired):
        """Two paths can fall due minutes apart — the live tick and the daily
        scrape. The second must not push us into the rename bucket."""
        run(cn.apply_for_club(wired.bot, club(), self._ctx()))
        wired.state["rows"][0].last_rendered = "something else"
        result = run(cn.apply_for_club(wired.bot, club(), self._ctx()))
        assert wired.channel.edits == ["Rank #87"]
        assert result["skipped"] == 1

    def test_the_floor_expires(self, wired):
        run(cn.apply_for_club(wired.bot, club(), self._ctx()))
        cn._last_rename[555] = (datetime.now(timezone.utc)
                                - cn.MIN_UPDATE_INTERVAL - timedelta(seconds=1))
        wired.state["rows"][0].last_rendered = "stale"
        result = run(cn.apply_for_club(wired.bot, club(), self._ctx()))
        assert result["updated"] == 1

    def test_force_bypasses_both_guards(self, wired):
        """Setting a template has to show its effect immediately — waiting an
        hour to find out whether it reads right is the problem being solved."""
        run(cn.apply_for_club(wired.bot, club(), self._ctx()))
        wired.state["rows"][0].last_rendered = "Rank #87"
        result = run(cn.apply_for_club(wired.bot, club(), self._ctx(), force=True))
        assert wired.channel.edits == ["Rank #87", "Rank #87"]
        assert result["updated"] == 1

    def test_the_floor_stays_within_discord_s_bucket(self):
        """Two renames per ten minutes is the documented limit; the scheduled
        floor must leave room for one user-driven rename beside it."""
        assert cn.MIN_UPDATE_INTERVAL >= timedelta(minutes=5)

    def test_an_empty_render_is_never_sent(self, wired):
        wired.state["rows"] = [FakeRow(template="   ")]
        result = run(cn.apply_for_club(wired.bot, club(), self._ctx()))
        assert wired.channel.edits == []
        assert result["skipped"] == 1

    def test_a_missing_permission_keeps_the_binding(self, wired):
        """A permission to grant, not a setting to lose."""
        wired.channel._raises = discord.Forbidden(
            SimpleNamespace(status=403, reason="x"), "nope")
        result = run(cn.apply_for_club(wired.bot, club(), self._ctx()))
        assert result["failed"] == 1
        assert wired.state["removed"] == []

    def test_a_deleted_channel_is_unbound(self, wired):
        wired.channel._raises = discord.NotFound(
            SimpleNamespace(status=404, reason="x"), "gone")
        result = run(cn.apply_for_club(wired.bot, club(), self._ctx()))
        assert result["removed"] == 1
        assert wired.state["removed"] == [555]

    def test_an_uncached_channel_is_left_configured(self, wired):
        wired.bot = SimpleNamespace(get_channel=lambda _: None)
        result = run(cn.apply_for_club(wired.bot, club(), self._ctx()))
        assert result["failed"] == 1
        assert wired.state["removed"] == []

    def test_one_bad_channel_does_not_stop_the_others(self, wired):
        good = FakeChannel(cid=777)
        bad = FakeChannel(cid=888, raises=discord.Forbidden(
            SimpleNamespace(status=403, reason="x"), "nope"))
        wired.state["rows"] = [FakeRow(channel_id=888), FakeRow(channel_id=777)]
        bot = SimpleNamespace(get_channel=lambda cid: {777: good, 888: bad}[cid])
        result = run(cn.apply_for_club(bot, club(), self._ctx()))
        assert good.edits == ["Rank #87"]
        assert (result["updated"], result["failed"]) == (1, 1)

    def test_several_channels_render_their_own_templates(self, wired):
        a, b = FakeChannel(cid=111), FakeChannel(cid=222)
        wired.state["rows"] = [FakeRow(template="Rank #{rank}", channel_id=111),
                               FakeRow(template="Fans {fans}", channel_id=222)]
        bot = SimpleNamespace(get_channel=lambda cid: {111: a, 222: b}[cid])
        run(cn.apply_for_club(bot, club(), self._ctx()))
        assert a.edits == ["Rank #87"]
        assert b.edits == ["Fans 1.84B"]


# --------------------------------------------------------------------------- #
# the permission pre-check
# --------------------------------------------------------------------------- #

class FakeMember:
    """A cached bot member. An empty role list is the failure being guarded."""
    def __init__(self, manage: bool, roles: int = 2):
        self._manage = manage
        self.roles = [object()] * roles

    def perms(self):
        return SimpleNamespace(manage_channels=self._manage)


class PermChannel:
    def __init__(self, member_perms=None, raises=False):
        self._perms, self._raises = member_perms, raises

    def permissions_for(self, member):
        if self._raises:
            raise RuntimeError("partial guild")
        return member.perms()


class TestCanRename:
    def test_true_when_the_permission_resolves(self):
        assert cn.can_rename(PermChannel(), FakeMember(manage=True)) is True

    def test_false_only_when_roles_actually_resolved(self):
        assert cn.can_rename(PermChannel(), FakeMember(manage=False, roles=3)) is False

    def test_unknown_when_the_bot_member_is_not_cached(self):
        """guild.me is None on a partial guild — never report that as a refusal."""
        assert cn.can_rename(PermChannel(), None) is None

    def test_unknown_when_the_role_cache_looks_empty(self):
        """Without the members intent the bot can resolve to @everyone alone, which
        reads as no permission while it holds one. utils.permissions.is_admin
        documents the same trap for administrator."""
        assert cn.can_rename(PermChannel(), FakeMember(manage=False, roles=1)) is None
        assert cn.can_rename(PermChannel(), FakeMember(manage=False, roles=0)) is None

    def test_unknown_when_resolving_raises(self):
        assert cn.can_rename(PermChannel(raises=True), FakeMember(manage=False)) is None


class TestForbiddenIsReportedSeparately:
    def test_a_refusal_is_counted_apart_from_other_failures(self, wired):
        """The command says different things for 'grant this permission' and
        'something else went wrong'."""
        wired.channel._raises = discord.Forbidden(
            SimpleNamespace(status=403, reason="x"), "nope")
        result = run(cn.apply_for_club(wired.bot, club(),
                                       cn.context_from_live(club(), snap())))
        assert result["forbidden"] == 1
        assert result["failed"] == 1

    def test_an_uncached_channel_is_not_a_permission_problem(self, wired):
        wired.bot = SimpleNamespace(get_channel=lambda _: None)
        result = run(cn.apply_for_club(wired.bot, club(),
                                       cn.context_from_live(club(), snap())))
        assert result["forbidden"] == 0
        assert result["failed"] == 1


class TestForbiddenIsExplained:
    """A refusal has to carry Discord's own answer. 50001 and 50013 send an admin
    to different settings, and the first version of this reported a guess at one
    of them, which sent people to change something that was already right."""

    def _forbidden(self, code, text="nope"):
        return discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"),
            {"code": code, "message": text},
        )

    def test_the_error_code_is_kept(self, wired):
        wired.channel._raises = self._forbidden(50013, "Missing Permissions")
        result = run(cn.apply_for_club(wired.bot, club(),
                                       cn.context_from_live(club(), snap())))
        assert result["code"] == 50013
        assert result["detail"] == "Missing Permissions"

    def test_missing_access_is_kept_apart_from_missing_permissions(self, wired):
        wired.channel._raises = self._forbidden(50001, "Missing Access")
        result = run(cn.apply_for_club(wired.bot, club(),
                                       cn.context_from_live(club(), snap())))
        assert result["code"] == 50001

    def test_our_own_reading_is_recorded_beside_it(self, wired):
        """When the cache says the permission is held and Discord still refuses,
        that disagreement is the diagnosis."""
        wired.channel._raises = self._forbidden(50013)
        result = run(cn.apply_for_club(wired.bot, club(),
                                       cn.context_from_live(club(), snap())))
        assert isinstance(result["access"], str) and result["access"]

    def test_a_channel_with_no_guild_does_not_break_the_error_path(self, wired):
        """A thin cache can hand back a channel carrying no guild; the handler
        for one failure must not raise a second."""
        wired.channel._raises = self._forbidden(50013)
        wired.channel.guild = None
        result = run(cn.apply_for_club(wired.bot, club(),
                                       cn.context_from_live(club(), snap())))
        assert result["forbidden"] == 1


class TestForbiddenAdvice:
    """The advice has to name the right setting for the code."""

    def _advice(self, code):
        from bot.commands.settings import _forbidden_advice
        return _forbidden_advice({"code": code}, SimpleNamespace(mention="#vc"))

    def test_missing_access_points_at_view_channel(self):
        text = self._advice(50001)
        assert "View Channel" in text

    def test_missing_permissions_points_at_manage_channels(self):
        text = self._advice(50013)
        assert "Manage Channels" in text
        assert "category" in text

    def test_an_unknown_code_still_says_something_useful(self):
        text = self._advice(0)
        assert "View Channel" in text and "Manage Channels" in text
