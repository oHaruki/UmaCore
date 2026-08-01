"""Tests for the live board.

Covers the three things that could go wrong at 200 clubs: the poll spreading out
instead of bursting, the day-rollover lifecycle (final edit, then a new post),
and the board never being mistaken for finalized data.
"""
import zlib
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scrapers.umamoe_api_scraper import LiveSnapshot, MemberGain, UmaMoeAPIScraper
from services import live_board

UTC = timezone.utc


def snap(**kw):
    base = dict(
        circle_id="1", jst_day=date(2026, 7, 25),
        as_of=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
        live_points=1_100_000_000, live_rank=300,
        monthly_point=1_000_000_000, monthly_rank=310,
        yesterday_points=900_000_000,
        gains=[MemberGain("1", "Alpha", 5_000_000, 60_000_000),
               MemberGain("2", "Beta", 3_000_000, 40_000_000),
               MemberGain("3", "Idle", 0, 20_000_000)],
    )
    base.update(kw)
    return LiveSnapshot(**base)


def club(**kw):
    base = dict(club_id=uuid4(), club_name="TestClub", circle_id="1",
                live_board_channel_id=999, live_board_message_id=None,
                live_board_day=None)
    base.update(kw)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# snapshot maths
# --------------------------------------------------------------------------- #

class TestSnapshotDerivations:
    def test_gained_today_is_live_minus_last_finalize(self):
        assert snap().gained_today == 100_000_000

    def test_gained_today_never_negative(self):
        """Guards a mid-update read where live briefly trails the finalize."""
        assert snap(live_points=999, monthly_point=1_000).gained_today == 0

    def test_rank_delta_positive_means_climbing(self):
        assert snap(live_rank=300, monthly_rank=310).rank_delta == 10
        assert snap(live_rank=320, monthly_rank=310).rank_delta == -10

    def test_top_gainers_sorted_and_capped(self):
        g = snap().top_gainers(2)
        assert [x.name for x in g] == ["Alpha", "Beta"]

    def test_active_today_excludes_idle_members(self):
        assert snap().active_today == 2

    def test_missing_points_degrade_to_none(self):
        assert snap(live_points=None).gained_today is None
        assert snap(live_rank=None).rank_delta is None


class TestMemberGains:
    def test_diffs_the_live_slot_against_the_previous(self):
        members = [{"viewer_id": 1, "trainer_name": "A",
                    "daily_fans": [100, 200, 350]}]
        gains = UmaMoeAPIScraper._member_gains(members, live_slot=2)
        assert gains[0].gained_today == 150
        assert gains[0].month_total == 250        # 350 - baseline 100

    def test_member_who_left_is_skipped(self):
        members = [{"viewer_id": 1, "trainer_name": "Gone",
                    "daily_fans": [100, 200, 0]}]
        assert UmaMoeAPIScraper._member_gains(members, live_slot=2) == []

    def test_member_who_joined_today_shows_no_phantom_gain(self):
        """No prior day to diff against — must not report their whole total."""
        members = [{"viewer_id": 1, "trainer_name": "New",
                    "daily_fans": [0, 0, 500_000_000]}]
        gains = UmaMoeAPIScraper._member_gains(members, live_slot=2)
        assert gains[0].gained_today == 0

    def test_first_slot_has_no_previous_day(self):
        members = [{"viewer_id": 1, "trainer_name": "A", "daily_fans": [100]}]
        assert UmaMoeAPIScraper._member_gains(members, live_slot=0) == []


# --------------------------------------------------------------------------- #
# spreading — the thing that makes 200 clubs viable
# --------------------------------------------------------------------------- #

class TestPollSpreading:
    def slot(self, club_id):
        return zlib.crc32(str(club_id).encode()) % 60

    def test_slot_is_stable_across_calls(self):
        cid = uuid4()
        assert self.slot(cid) == self.slot(cid)

    def test_slot_is_always_a_valid_minute(self):
        for _ in range(500):
            assert 0 <= self.slot(uuid4()) < 60

    def test_two_hundred_clubs_spread_without_a_burst(self):
        """The whole point: no minute may carry a large share of the load.

        Deterministic ids so this cannot flake — 200 items across 60 buckets has a
        long enough tail that random ones occasionally exceed any tight bound.
        """
        from uuid import UUID
        slots = [self.slot(UUID(int=i * 2654435761)) for i in range(200)]
        counts = [slots.count(m) for m in range(60)]
        assert max(counts) <= 12, f"worst minute holds {max(counts)} clubs"
        assert sum(counts) == 200

    def test_load_averages_out(self):
        slots = [self.slot(uuid4()) for _ in range(600)]
        used = len(set(slots))
        assert used >= 50, f"only {used}/60 minutes used — poor spread"


# --------------------------------------------------------------------------- #
# embed
# --------------------------------------------------------------------------- #

class TestEmbed:
    def _all(self, **kw):
        return live_board.build_embeds(club(), snap(**kw))

    def test_live_board_is_marked_not_final(self):
        e = self._all()[0]
        assert "live board" in e.title.lower()
        assert "not final" in e.description.lower()

    def test_closed_board_says_final(self):
        e = live_board.build_embeds(club(), snap(), closed=True)[0]
        assert "final" in e.description.lower()

    def test_summary_shows_todays_gain_and_rank(self):
        names = [f.name for f in self._all()[0].fields]
        assert "Fans today" in names
        assert "Rank" in names

    def test_summary_counts_raced_and_idle(self):
        summary = next(f for f in self._all()[0].fields if "Summary" in f.name)
        assert "Raced today: 2" in summary.value
        assert "Not yet: 1" in summary.value

    def test_every_member_appears_somewhere(self):
        """The whole roster is listed, not just a top-N."""
        body = " ".join((e.description or "") for e in self._all())
        for name in ("Alpha", "Beta", "Idle"):
            assert name in body, f"{name} missing from the board"

    def test_racers_and_idlers_are_separated(self):
        embeds = self._all()
        raced = next(e for e in embeds if "Raced" in e.title)
        idle = next(e for e in embeds if "No Fans Yet" in e.title)
        assert "Alpha" in raced.description and "Idle" not in raced.description
        assert "Idle" in idle.description

    def test_racers_are_ranked_by_todays_gain(self):
        raced = next(e for e in self._all() if "Raced" in e.title)
        assert raced.description.index("Alpha") < raced.description.index("Beta")

    def test_empty_day_says_so_rather_than_showing_nothing(self):
        body = " ".join((e.description or "") for e in self._all(gains=[]))
        assert "nobody has raced yet" in body.lower()

    def test_footer_carries_the_data_timestamp(self):
        assert "12:00 UTC" in self._all()[0].footer.text

    def test_fits_discord_limits_for_a_full_roster(self):
        """30 members must fit one message: <=10 embeds and <6000 characters."""
        gains = [MemberGain(str(i), f"LongTrainerName{i:02d}", 5_000_000 - i * 1000,
                            40_000_000 + i) for i in range(30)]
        embeds = live_board.build_embeds(club(), snap(gains=gains))
        total = sum(len((e.description or "") + (e.title or "")
                        + "".join(f.name + f.value for f in e.fields)) for e in embeds)
        assert len(embeds) <= 10, f"{len(embeds)} embeds exceeds Discord's limit"
        assert total < 6000, f"{total} chars exceeds the per-message budget"
        body = " ".join((e.description or "") for e in embeds)
        assert body.count("LongTrainerName") == 30, "not every member was listed"


# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #

class FakeMessage:
    """Records each edit as the list of embeds the board sent."""
    def __init__(self, mid): self.id, self.edits = mid, []
    async def edit(self, **kw): self.edits.append(kw.get("embeds") or [kw["embed"]])


class FakeChannel:
    def __init__(self): self.posted, self._messages, self._next = [], {}, 1000
    async def send(self, embed=None, embeds=None):
        self._next += 1
        msg = FakeMessage(self._next)
        self._messages[msg.id] = msg
        self.posted.append(embeds or [embed])
        return msg
    async def fetch_message(self, mid):
        if mid not in self._messages:
            import discord
            raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "gone")
        return self._messages[mid]


class FakeBot:
    def __init__(self, channel): self._c = channel
    def get_channel(self, _): return self._c


@pytest.fixture
def wired(monkeypatch):
    """live_board with a fake Discord and a stubbed scraper."""
    channel = FakeChannel()
    state = {"snapshot": snap(), "saved": []}

    class FakeScraper:
        def __init__(self, *a, **kw): pass
        async def fetch_live(self): return state["snapshot"]

    monkeypatch.setattr(live_board, "UmaMoeAPIScraper", FakeScraper)
    return SimpleNamespace(bot=FakeBot(channel), channel=channel, state=state)


def attach_persistence(c, state):
    async def set_msg(message_id, jst_day):
        c.live_board_message_id, c.live_board_day = message_id, jst_day
        state["saved"].append((message_id, jst_day))
    c.set_live_board_message = set_msg
    return c


class TestLifecycle:
    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_first_run_posts_a_new_board(self, wired):
        c = attach_persistence(club(), wired.state)
        self._run(live_board.update_club(wired.bot, c, now_utc=datetime(2026, 7, 25, 12, tzinfo=UTC)))
        assert len(wired.channel.posted) == 1
        assert c.live_board_day == date(2026, 7, 25)

    def test_same_day_edits_rather_than_reposting(self, wired):
        c = attach_persistence(club(), wired.state)
        now = datetime(2026, 7, 25, 12, tzinfo=UTC)
        self._run(live_board.update_club(wired.bot, c, now_utc=now))
        self._run(live_board.update_club(wired.bot, c, now_utc=now))
        self._run(live_board.update_club(wired.bot, c, now_utc=now))
        assert len(wired.channel.posted) == 1, "reposted instead of editing"
        msg = wired.channel._messages[c.live_board_message_id]
        assert len(msg.edits) == 2

    def test_day_rollover_closes_the_old_board_then_opens_a_new_one(self, wired):
        c = attach_persistence(club(), wired.state)
        # Day 25 in progress.
        self._run(live_board.update_club(wired.bot, c, now_utc=datetime(2026, 7, 25, 12, tzinfo=UTC)))
        first_id = c.live_board_message_id

        # Past 15:00 UTC: JST day 26 is now in progress.
        wired.state["snapshot"] = snap(jst_day=date(2026, 7, 26))
        self._run(live_board.update_club(wired.bot, c, now_utc=datetime(2026, 7, 25, 16, tzinfo=UTC)))

        assert len(wired.channel.posted) == 2, "did not open a board for the new day"
        assert c.live_board_message_id != first_id
        assert c.live_board_day == date(2026, 7, 26)

        closing = wired.channel._messages[first_id].edits[-1][0]
        assert "final" in closing.description.lower(), "old board was not closed out"

    def test_closing_edit_uses_the_finalized_figures(self, wired):
        c = attach_persistence(club(), wired.state)
        self._run(live_board.update_club(wired.bot, c, now_utc=datetime(2026, 7, 25, 12, tzinfo=UTC)))
        first_id = c.live_board_message_id

        wired.state["snapshot"] = snap(
            jst_day=date(2026, 7, 26),
            monthly_point=1_150_000_000,     # day 25's finalized total
            yesterday_points=1_000_000_000,  # day 24's
        )
        self._run(live_board.update_club(wired.bot, c, now_utc=datetime(2026, 7, 25, 16, tzinfo=UTC)))

        closing = wired.channel._messages[first_id].edits[-1][0]
        fans = next(f for f in closing.fields if f.name == "Fans today")
        assert "150" in fans.value, f"expected the finalized day gain, got {fans.value}"

    def test_deleted_message_is_reposted(self, wired):
        c = attach_persistence(club(), wired.state)
        now = datetime(2026, 7, 25, 12, tzinfo=UTC)
        self._run(live_board.update_club(wired.bot, c, now_utc=now))
        wired.channel._messages.clear()          # someone deleted it
        self._run(live_board.update_club(wired.bot, c, now_utc=now))
        assert len(wired.channel.posted) == 2

    def test_failed_fetch_changes_nothing(self, wired, monkeypatch):
        class DeadScraper:
            def __init__(self, *a, **kw): pass
            async def fetch_live(self): return None
        monkeypatch.setattr(live_board, "UmaMoeAPIScraper", DeadScraper)

        c = attach_persistence(club(), wired.state)
        status = self._run(live_board.update_club(wired.bot, c, now_utc=datetime(2026, 7, 25, 12, tzinfo=UTC)))
        assert status == "no_data"
        assert wired.channel.posted == []
        assert c.live_board_message_id is None


class TestMonthBoundaryGains:
    """JST day 1 of a calendar month is the PREVIOUS competition month's last day,
    so it reads that month's array and needs no special case."""

    def test_last_day_of_a_month_diffs_within_the_same_array(self):
        from utils.jst_calendar import resolve_live
        target = resolve_live(datetime(2026, 8, 1, 12, 49, tzinfo=UTC))
        assert (target.year, target.month, target.slot) == (2026, 7, 31)

        members = [{"viewer_id": i, "trainer_name": f"M{i}",
                    "daily_fans": [1_000_000] + [0] * 29 + [5_000_000, 5_500_000]}
                   for i in range(5)]
        gains = UmaMoeAPIScraper._member_gains(members, target.slot)
        assert len(gains) == 5, "members were dropped at the month boundary"
        assert all(g.gained_today == 500_000 for g in gains)
        # Month total still measured from slot 0, July's baseline.
        assert all(g.month_total == 4_500_000 for g in gains)


class TestUnpublishedMonth:
    """A newly opened competition month exists before uma.moe publishes rows for it.

    Observed 2026-08-01 17:23 UTC: August returned HTTP 200 with an empty member
    list, null live fields, and July's monthly_point still attached. Rendering that
    replaced a good board with 'Total Members: 0'.
    """

    def test_no_snapshot_when_there_are_no_member_rows(self, monkeypatch):
        import asyncio
        from scrapers import umamoe_api_scraper as mod

        async def empty_month(_self, _session, year, month):
            return {"circle": {"monthly_point": 1_837_269_789, "live_points": None,
                               "live_rank": None, "last_live_update": None},
                    "members": []}
        monkeypatch.setattr(mod.UmaMoeAPIScraper, "_fetch_month", empty_month)

        class NullSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
        monkeypatch.setattr(mod.UmaMoeAPIScraper, "_session", lambda self: NullSession())

        s = mod.UmaMoeAPIScraper("1", now_utc=datetime(2026, 8, 1, 17, 23, tzinfo=UTC))
        assert asyncio.run(s.fetch_live()) is None

    def test_existing_board_is_left_alone(self, wired, monkeypatch):
        """The previous day's board must survive an unpublished new month."""
        import asyncio
        c = attach_persistence(club(), wired.state)
        asyncio.run(live_board.update_club(
            wired.bot, c, now_utc=datetime(2026, 7, 25, 12, tzinfo=UTC)))
        assert len(wired.channel.posted) == 1
        original = c.live_board_message_id
        edits_before = len(wired.channel._messages[original].edits)

        class Empty:
            def __init__(self, *a, **kw): pass
            async def fetch_live(self): return None
        monkeypatch.setattr(live_board, "UmaMoeAPIScraper", Empty)

        status = asyncio.run(live_board.update_club(
            wired.bot, c, now_utc=datetime(2026, 8, 1, 17, tzinfo=UTC)))
        assert status == "no_data"
        assert len(wired.channel.posted) == 1, "posted an empty board for the new month"
        assert c.live_board_message_id == original, "rolled over with no data"
        assert len(wired.channel._messages[original].edits) == edits_before, \
            "blanked the existing board"


class TestUpdateStatus:
    """update_club distinguishes outcomes so callers can explain themselves."""

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_first_run_reports_posted(self, wired):
        c = attach_persistence(club(), wired.state)
        assert self._run(live_board.update_club(
            wired.bot, c, now_utc=datetime(2026, 7, 25, 12, tzinfo=UTC))) == "posted"

    def test_second_run_reports_edited(self, wired):
        c = attach_persistence(club(), wired.state)
        now = datetime(2026, 7, 25, 12, tzinfo=UTC)
        self._run(live_board.update_club(wired.bot, c, now_utc=now))
        assert self._run(live_board.update_club(wired.bot, c, now_utc=now)) == "edited"

    def test_rollover_reports_posted(self, wired):
        c = attach_persistence(club(), wired.state)
        self._run(live_board.update_club(
            wired.bot, c, now_utc=datetime(2026, 7, 25, 12, tzinfo=UTC)))
        wired.state["snapshot"] = snap(jst_day=date(2026, 7, 26))
        assert self._run(live_board.update_club(
            wired.bot, c, now_utc=datetime(2026, 7, 25, 16, tzinfo=UTC))) == "posted"


class TestClosedBoardKeepsItsRoster:
    """The archived board is that day's permanent record, so it must keep the
    members who raced. It previously closed with an empty roster because the
    closing edit reused the fetch describing the NEW day."""

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_closed_board_lists_the_members_who_raced(self, wired):
        c = attach_persistence(club(), wired.state)
        self._run(live_board.update_club(
            wired.bot, c, now_utc=datetime(2026, 7, 25, 12, tzinfo=UTC)))
        original = c.live_board_message_id

        wired.state["snapshot"] = snap(jst_day=date(2026, 7, 26))
        self._run(live_board.update_club(
            wired.bot, c, now_utc=datetime(2026, 7, 25, 16, tzinfo=UTC)))

        closing = wired.channel._messages[original].edits[-1]
        body = " ".join((e.description or "") for e in closing)
        summary = next(f for e in closing for f in e.fields if "Summary" in f.name)

        assert "Total Members: 0" not in summary.value, "archived board lost its roster"
        assert "Alpha" in body and "Beta" in body
        assert "final" in closing[0].description.lower()

    def test_closed_board_is_released_so_the_next_day_gets_its_own(self, wired):
        c = attach_persistence(club(), wired.state)
        self._run(live_board.update_club(
            wired.bot, c, now_utc=datetime(2026, 7, 25, 12, tzinfo=UTC)))
        original = c.live_board_message_id

        wired.state["snapshot"] = snap(jst_day=date(2026, 7, 26))
        self._run(live_board.update_club(
            wired.bot, c, now_utc=datetime(2026, 7, 25, 16, tzinfo=UTC)))

        assert c.live_board_message_id != original, "kept writing to the archive"
        assert len(wired.channel.posted) == 2

    def test_a_closed_board_is_never_reopened(self, wired):
        """Later refreshes must not resurrect an archived day's message."""
        c = attach_persistence(club(), wired.state)
        self._run(live_board.update_club(
            wired.bot, c, now_utc=datetime(2026, 7, 25, 12, tzinfo=UTC)))
        original = c.live_board_message_id

        wired.state["snapshot"] = snap(jst_day=date(2026, 7, 26))
        for _ in range(3):
            self._run(live_board.update_club(
                wired.bot, c, now_utc=datetime(2026, 7, 25, 16, tzinfo=UTC)))

        assert len(wired.channel.posted) == 2, "reposted more than once for one day"
        last = wired.channel._messages[original].edits[-1]
        assert "final" in last[0].description.lower(), "archive stopped saying final"
