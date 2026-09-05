"""Drive the scraper across a full month, including the month boundary.

Runs a simulated club from late June into early August against
:mod:`tests.fake_umamoe`, checking at every step that the scraper reads a
*finalized* slot, attributes it to a sane date, and never skips, repeats or
reverses a day. The month boundary is the interesting part — it is otherwise
only reachable by waiting for one.
"""
import logging
from datetime import date, datetime, timedelta, timezone

import pytest

from scrapers.base_scraper import StaleDataError
from scrapers.umamoe_api_scraper import JOINED_BEFORE_MONTH, UmaMoeAPIScraper
from tests.fake_umamoe import FakeMember, FakeUmaMoe, default_roster, jst_day_of

UTC = timezone.utc


def make_scraper(backend: FakeUmaMoe, now_utc: datetime) -> UmaMoeAPIScraper:
    """A scraper whose HTTP layer is replaced by the fake backend."""
    s = UmaMoeAPIScraper(str(backend.circle_id), now_utc=now_utc)

    async def fake_fetch(_session, year, month):
        return backend.payload(year, month, now_utc)

    s._fetch_month = fake_fetch          # type: ignore[assignment]

    class NullSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    s._session = lambda: NullSession()   # type: ignore[assignment]
    return s


def at(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


def daily_ticks(start: date, end: date, hour: int, minute: int = 0):
    """One UTC moment per calendar day in [start, end]."""
    d = start
    while d <= end:
        yield datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC)
        d += timedelta(days=1)


@pytest.fixture
def backend():
    return FakeUmaMoe(members=default_roster())


# --------------------------------------------------------------------------- #
# The core sweep
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("hour,label", [
    (8, "morning club (pre-finalize)"),
    (13, "13:00 UTC — matches the real Horsecore club"),
    (14, "14:00 UTC — matches most real clubs"),
    (17, "17:00 UTC — the old default"),
    (23, "late evening club"),
])
def test_full_month_sweep_reads_only_finalized_days(backend, hour, label, caplog):
    """Across Jun 28 -> Aug 3, every scrape must land on a closed JST day."""
    seen = []
    with caplog.at_level(logging.ERROR):
        for now in daily_ticks(date(2026, 6, 28), date(2026, 8, 3), hour):
            scraper = make_scraper(backend, now)
            import asyncio
            data = asyncio.run(scraper.scrape())
            target = scraper.get_slot_target()

            in_progress = jst_day_of(now)
            assert target.jst_day < in_progress, (
                f"{label} at {now}: read JST day {target.jst_day} but "
                f"{in_progress} is still in progress"
            )
            assert target.is_live is False
            assert data, f"{label} at {now}: no members parsed"
            seen.append((now, target, scraper.get_data_date()))

    # No checksum error may have fired anywhere in the sweep.
    errs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errs, f"{label}: checksum errors during sweep:\n" + "\n".join(errs[:5])

    # data_date advances exactly one day per calendar day, month boundaries
    # included: the slot IS the competition day, so nothing is skipped or repeated.
    dates = [d for _, _, d in seen]
    for prev, nxt in zip(dates, dates[1:]):
        step = (nxt - prev).days
        assert step == 1, f"{label}: {prev} -> {nxt} advanced {step} days"
    assert len(dates) == len(set(dates)), f"{label}: a date was reported twice"

    # Every member total must be non-zero somewhere in the sweep: a club-wide
    # zero would mean we reported a month's day 1 (the boundary regression).
    for now, target, d in seen:
        scraper = make_scraper(backend, now)
        import asyncio
        parsed = asyncio.run(scraper.scrape())
        total = sum(m["fans"][-1] for m in parsed.values())
        assert total > 0, f"{label} at {now}: club total is 0 (reported {d})"


@pytest.mark.parametrize("hour", [8, 13, 14, 17, 23])
def test_slot_always_reconciles_with_monthly_point(backend, hour):
    """The slot read must reconcile with uma.moe's own finalized club total.

    Compared using the leaver-inclusive total, which is how uma.moe computes
    monthly_point — the quota-facing parse deliberately drops members who left.
    """
    import asyncio
    for now in daily_ticks(date(2026, 6, 28), date(2026, 8, 3), hour):
        scraper = make_scraper(backend, now)
        asyncio.run(scraper.scrape())
        target = scraper.get_slot_target()
        meta = scraper.get_meta()

        payload = backend.payload(target.year, target.month, now)
        ours = UmaMoeAPIScraper._checksum_total(payload["members"], target.slot)

        day_gain = meta.monthly_point - meta.yesterday_points
        if day_gain > 0:
            off_by_days = abs(ours - meta.monthly_point) / day_gain
            assert off_by_days < 0.5, (
                f"{now}: reconciled {ours:,} vs monthly_point {meta.monthly_point:,} "
                f"= {off_by_days:.1%} of a day — wrong slot"
            )


@pytest.mark.parametrize("hour", [8, 14, 17])
def test_parsed_total_never_exceeds_umamoe_total(backend, hour):
    """The quota-facing parse drops leavers, so it can trail monthly_point but
    must never exceed it — exceeding would mean double-counting."""
    import asyncio
    for now in daily_ticks(date(2026, 7, 2), date(2026, 8, 3), hour):
        scraper = make_scraper(backend, now)
        parsed = asyncio.run(scraper.scrape())
        meta = scraper.get_meta()
        ours = sum(m["fans"][-1] for m in parsed.values())
        if meta.monthly_point:
            assert ours <= meta.monthly_point * 1.01, (
                f"{now}: parsed {ours:,} exceeds monthly_point {meta.monthly_point:,}"
            )


# --------------------------------------------------------------------------- #
# Month boundary specifics
# --------------------------------------------------------------------------- #

class TestMonthBoundary:
    def test_fetches_previous_month_before_rollover(self, backend):
        """Aug 1 08:00 UTC still reads July, because JST Jul 31 is the last close."""
        import asyncio
        s = make_scraper(backend, at(2026, 8, 1, 8))
        asyncio.run(s.scrape())
        t = s.get_slot_target()
        assert (t.year, t.month) == (2026, 7)
        assert t.jst_day == date(2026, 7, 31)
        assert t.slot == 30

    def test_julys_final_day_is_jst_august_1(self, backend):
        """Aug 1 17:00 UTC: JST Aug 1 closed, and it is July's LAST competition
        day — July slot 31, reported as July 31."""
        import asyncio
        s = make_scraper(backend, at(2026, 8, 1, 17))
        parsed = asyncio.run(s.scrape())
        t = s.get_slot_target()
        assert (t.year, t.month, t.slot) == (2026, 7, 31)
        assert t.jst_day == date(2026, 8, 1)
        assert t.data_date == date(2026, 7, 31)
        assert sum(m["fans"][-1] for m in parsed.values()) > 0

    def test_august_opens_on_jst_august_2(self, backend):
        import asyncio
        s = make_scraper(backend, at(2026, 8, 2, 17))
        asyncio.run(s.scrape())
        t = s.get_slot_target()
        assert (t.year, t.month, t.slot) == (2026, 8, 1)
        assert t.data_date == date(2026, 8, 1)

    def test_june_has_30_days_but_32_slots(self, backend):
        """Reading Jun 30 must not run off the end of a short month."""
        import asyncio
        s = make_scraper(backend, at(2026, 7, 1, 8))
        parsed = asyncio.run(s.scrape())
        t = s.get_slot_target()
        assert (t.year, t.month, t.slot) == (2026, 6, 29)
        assert parsed

    def test_no_data_date_gap_across_the_boundary(self, backend):
        """The Jul->Aug transition must not skip a reported date.

        A 14:00 UTC club reads before each day's finalize, so it lags a day — but
        the sequence is still strictly contiguous across the boundary.
        """
        import asyncio
        dates = []
        for now in daily_ticks(date(2026, 7, 30), date(2026, 8, 4), 14):
            s = make_scraper(backend, now)
            asyncio.run(s.scrape())
            dates.append(s.get_data_date())

        assert dates == [date(2026, 7, 28), date(2026, 7, 29), date(2026, 7, 30),
                         date(2026, 7, 31), date(2026, 8, 1), date(2026, 8, 2)]
        assert dates == sorted(dates), "reported dates went backwards"


# --------------------------------------------------------------------------- #
# Roster edge cases the real API showed us
# --------------------------------------------------------------------------- #

class TestRosterEdgeCases:
    def _run(self, backend, when):
        import asyncio
        s = make_scraper(backend, when)
        return asyncio.run(s.scrape()), s

    def test_member_who_left_is_dropped(self, backend):
        """'Quitter' leaves Jul 18; after that their slot is 0 and they vanish."""
        parsed, _ = self._run(backend, at(2026, 7, 25, 17))
        assert "1005" not in parsed

    def test_member_present_before_leaving_is_counted(self, backend):
        parsed, _ = self._run(backend, at(2026, 7, 10, 17))
        assert "1005" in parsed

    def test_late_joiner_absent_then_present(self, backend):
        """'LateJoin' joins Jul 9."""
        before, _ = self._run(backend, at(2026, 7, 5, 17))
        after, _ = self._run(backend, at(2026, 7, 20, 17))
        assert "1004" not in before
        assert "1004" in after

    def test_negative_transfer_marker_never_leaks(self, backend):
        """'Transferred' carries a negative marker; no fan value may go negative."""
        parsed, _ = self._run(backend, at(2026, 7, 25, 17))
        assert "1006" in parsed
        for vid, m in parsed.items():
            assert all(f >= 0 for f in m["fans"]), f"negative fans for {vid}: {m['fans']}"

    def test_join_day_detected_for_late_joiner(self, backend):
        """'LateJoin' first races on JST July 9, which is competition day 8.

        join_day is a competition day, matching data_date and the slot index — the
        old value of 9 came from treating the slot as a JST day.
        """
        parsed, _ = self._run(backend, at(2026, 7, 20, 17))
        assert parsed["1004"]["join_day"] == 8

    def test_continuing_member_reports_no_join_day(self, backend):
        """'Steady' joined June 1, so July is not a month they joined in.

        Their total sits in slot 0 — the June-close baseline — which is proof they
        predate July. Reporting day 1 here (the old ``max(1, baseline_idx)``) made
        them indistinguishable from a genuine day-1 joiner, and the quota
        calculator's join-day exemption then waived a day they had really earned.
        """
        parsed, _ = self._run(backend, at(2026, 7, 20, 17))
        assert parsed["1001"]["join_day"] == JOINED_BEFORE_MONTH

    def test_day_one_joiner_still_reports_day_one(self):
        """The counterpart: a genuine day-1 joiner keeps its exemption.

        August competition day 1 is JST August *2* — JST August 1 is July's last
        day, which is why 'BoundaryJoin' (joined JST Aug 1) reads as predating
        August rather than opening it. This member first races on JST Aug 2, so
        August's slot 0 is empty for them, and that emptiness is the whole
        difference from a continuing member. Their gain on day 1 is 0, so waiving
        its quota costs nothing — which is what the exemption is for.
        """
        backend = FakeUmaMoe(members=[
            FakeMember(2001, "DayOne", 200_000_000, 2_500_000, joined=date(2026, 8, 2)),
        ])
        parsed, _ = self._run(backend, at(2026, 8, 5, 17))
        assert parsed["2001"]["join_day"] == 1
        assert parsed["2001"]["fans"][1] == 0

    def test_boundary_joiner_predates_the_month_it_appears_in(self, backend):
        """'BoundaryJoin' first races JST Aug 1 — that is July competition day 31.

        So by the time August's own days begin they are already an established
        member, their total sits in August's slot 0, and August day 1 is an
        ordinary earning day for them.
        """
        parsed, _ = self._run(backend, at(2026, 8, 3, 17))
        assert parsed["1007"]["join_day"] == JOINED_BEFORE_MONTH
        assert parsed["1007"]["fans"][1] > 0

    def test_boundary_joiner_appears_in_august(self, backend):
        """'BoundaryJoin' starts Aug 1 — absent in July, present once August is read."""
        july, _ = self._run(backend, at(2026, 7, 25, 17))
        assert "1007" not in july
        august, s = self._run(backend, at(2026, 8, 3, 17))
        assert (s.get_slot_target().year, s.get_slot_target().month) == (2026, 8)
        assert "1007" in august


# --------------------------------------------------------------------------- #
# Freshness behaviour through a single day
# --------------------------------------------------------------------------- #

class TestFreshnessThroughADay:
    def test_stale_raised_when_finalize_has_not_run(self, backend):
        """Just after 15:00 UTC, before this circle's write, must raise stale."""
        import asyncio
        backend.finalize_lag_sec = 600          # this circle finalizes at 15:10
        s = make_scraper(backend, at(2026, 7, 20, 15, 2))
        with pytest.raises(StaleDataError):
            asyncio.run(s.scrape())

    def test_succeeds_once_finalize_has_run(self, backend):
        import asyncio
        backend.finalize_lag_sec = 600
        s = make_scraper(backend, at(2026, 7, 20, 15, 15))
        assert asyncio.run(s.scrape())

    def test_hourly_polling_never_changes_the_finalized_answer(self, backend):
        """The whole point: the finalized total must not drift during the day.

        Polls every hour across a JST day and asserts the finalized slot's value
        is identical every time, even though the live slot is growing.
        """
        import asyncio
        totals, live_totals = set(), set()
        # JST Jul 20 runs 19 Jul 15:00 UTC -> 20 Jul 15:00 UTC.
        for hour_offset in range(0, 24):
            now = at(2026, 7, 19, 15, 30) + timedelta(hours=hour_offset)
            s = make_scraper(backend, now)
            parsed = asyncio.run(s.scrape())
            totals.add(sum(m["fans"][-1] for m in parsed.values()))
            live = asyncio.run(make_scraper(backend, now).fetch_live())
            if live and live.live_points:
                live_totals.add(live.live_points)

        assert len(totals) == 1, f"finalized total drifted during the day: {sorted(totals)}"
        assert len(live_totals) > 1, "live total should grow through the day"


# --------------------------------------------------------------------------- #
# The regression this all exists for
# --------------------------------------------------------------------------- #

def test_pre_fix_slot_choice_would_have_been_wrong(backend):
    """Reproduce the old `slot > 0` probe and show it picks the live slot.

    Guards against anyone reintroducing availability-probing: at 08:00 UTC the
    old rule sees a populated in-progress slot and treats it as finished.
    """
    import asyncio
    now = at(2026, 7, 25, 8)
    payload = backend.payload(2026, 7, now)
    sample = payload["members"][0]["daily_fans"]

    old_slot = now.day - 1                     # old code: index = calendar day - 1
    assert sample[old_slot] > 0                # ...and it looked "available"

    s = make_scraper(backend, now)
    asyncio.run(s.scrape())
    new_slot = s.get_slot_target().slot

    assert new_slot == old_slot - 1, "fix must read one slot earlier than the old probe"

    circle = payload["circle"]
    assert circle["live_points"] > circle["monthly_point"], (
        "the old slot held a still-growing total"
    )
