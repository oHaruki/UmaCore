"""Tests for the JST slot mapping.

The reference cases come from a live API probe on 2026-07-25 08:10 UTC against
circles 452414222 and 883951941, where member sums reconciled as:

    slot[22] == yesterday_points
    slot[23] == monthly_point   (last closed JST day)
    slot[24] == live_points     (in progress)
"""
import pytest
from datetime import date, datetime, timezone

from utils.jst_calendar import (
    resolve_finalized, resolve_live, next_rollover_utc, parse_api_timestamp,
    in_progress_jst_day, last_closed_jst_day,
)


def utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


class TestVerifiedProbe:
    """Locks in the exact mapping measured against the live API."""

    PROBE = utc(2026, 7, 25, 8, 10)

    def test_finalized_slot_matches_monthly_point(self):
        t = resolve_finalized(self.PROBE)
        assert (t.year, t.month, t.slot) == (2026, 7, 23)
        assert t.jst_day == date(2026, 7, 24)
        assert t.is_live is False

    def test_live_slot_matches_live_points(self):
        t = resolve_live(self.PROBE)
        assert (t.year, t.month, t.slot) == (2026, 7, 24)
        assert t.jst_day == date(2026, 7, 25)
        assert t.is_live is True

    def test_live_is_exactly_one_slot_ahead_of_finalized(self):
        assert resolve_live(self.PROBE).slot == resolve_finalized(self.PROBE).slot + 1


class TestLegacyLabellingPreserved:
    """The 17:00 UTC default club must keep reading the slot it always did.

    Before the live-data change this was the only time the old `slot > 0` probe
    happened to land on finalized data, so it defines the labelling contract that
    existing quota_history rows were written under.
    """

    def test_default_scrape_time_unchanged(self):
        t = resolve_finalized(utc(2026, 7, 25, 17, 0))
        assert t.slot == 24                      # same slot the old code read
        assert t.data_date == date(2026, 7, 24)  # same date it was labelled

    def test_early_scrape_now_reads_one_slot_earlier(self):
        """The actual bug fix: at 08:00 UTC the old code read the live slot."""
        t = resolve_finalized(utc(2026, 7, 25, 8, 0))
        assert t.slot == 23                      # old code wrongly read 24
        assert t.data_date == date(2026, 7, 23)


class TestRollover:
    def test_flips_exactly_at_1500_utc(self):
        before = resolve_finalized(utc(2026, 7, 25, 14, 59))
        after = resolve_finalized(utc(2026, 7, 25, 15, 1))
        assert before.slot == 23
        assert after.slot == 24

    def test_next_rollover_same_day(self):
        assert next_rollover_utc(utc(2026, 7, 25, 8, 0)) == utc(2026, 7, 25, 15, 0)

    def test_next_rollover_wraps_to_tomorrow(self):
        assert next_rollover_utc(utc(2026, 7, 25, 17, 0)) == utc(2026, 7, 26, 15, 0)


class TestMonthBoundary:
    def test_first_jst_day_of_month_reads_previous_month_array(self):
        """08:00 UTC Aug 1: JST Aug 1 in progress, so July 31 is the last close."""
        t = resolve_finalized(utc(2026, 8, 1, 8, 0))
        assert (t.year, t.month, t.slot) == (2026, 7, 30)
        assert t.jst_day == date(2026, 7, 31)
        assert t.is_cross_month is False

    def test_new_months_day_one_is_deferred(self):
        """17:00 UTC Aug 1: JST Aug 1 has closed, but a month's day 1 has no
        earlier slot to measure monthly fans from, so reporting it would show a
        club-wide zero under a July date. July's final day is kept instead."""
        t = resolve_finalized(utc(2026, 8, 1, 17, 0))
        assert (t.year, t.month, t.slot) == (2026, 7, 30)
        assert t.jst_day == date(2026, 7, 31)
        assert t.data_date == date(2026, 7, 30)

    def test_new_month_opens_on_day_two(self):
        t = resolve_finalized(utc(2026, 8, 2, 17, 0))
        assert (t.year, t.month, t.slot) == (2026, 8, 1)
        assert t.data_date == date(2026, 8, 1)

    def test_finalized_target_never_crosses_months(self):
        """Because day 1 is deferred, a persisted slot and its date always share a
        month — so month-scoped aggregation can never straddle a boundary."""
        for month in range(1, 13):
            for day in (1, 2, 15, 28):
                for hour in (0, 8, 14, 15, 17, 23):
                    t = resolve_finalized(utc(2026, month, day, hour, 0))
                    assert t.is_cross_month is False, t.describe()

    def test_month_with_30_days(self):
        t = resolve_finalized(utc(2026, 7, 1, 8, 0))
        assert (t.year, t.month, t.slot) == (2026, 6, 29)
        assert t.jst_day == date(2026, 6, 30)


class TestYearBoundary:
    def test_new_year_defers_to_december(self):
        """Jan 1 is a month's day 1, so December's final day is reported."""
        t = resolve_finalized(utc(2027, 1, 1, 17, 0))
        assert (t.year, t.month, t.slot) == (2026, 12, 30)
        assert t.jst_day == date(2026, 12, 31)
        assert t.data_date == date(2026, 12, 30)

    def test_new_year_opens_on_jan_2(self):
        t = resolve_finalized(utc(2027, 1, 2, 17, 0))
        assert (t.year, t.month, t.slot) == (2027, 1, 1)
        assert t.data_date == date(2027, 1, 1)

    def test_last_day_of_year(self):
        t = resolve_finalized(utc(2026, 12, 31, 8, 0))
        assert (t.year, t.month, t.slot) == (2026, 12, 29)
        assert t.jst_day == date(2026, 12, 30)


class TestJstDayHelpers:
    def test_in_progress_and_last_closed_are_adjacent(self):
        n = utc(2026, 7, 25, 8, 10)
        assert (in_progress_jst_day(n) - last_closed_jst_day(n)).days == 1

    def test_naive_datetime_treated_as_utc(self):
        aware = resolve_finalized(utc(2026, 7, 25, 8, 10))
        naive = resolve_finalized(datetime(2026, 7, 25, 8, 10))
        assert aware == naive


class TestTimestampParsing:
    def test_parses_api_format(self):
        assert parse_api_timestamp("2026-07-25T06:20:01Z") == utc(2026, 7, 25, 6, 20).replace(second=1)

    @pytest.mark.parametrize("bad", [None, "", "not-a-date", 12345])
    def test_bad_input_returns_none(self, bad):
        assert parse_api_timestamp(bad) is None


class TestRolloverSlot:
    """Day 1 of a month lives in the PREVIOUS month's array.

    Every daily_fans array is 32 slots regardless of month length, and the slot
    past the month's last day holds day 1 of the following month. Verified
    2026-08-01 on circle 860280110: July (31 days) populated slots 0..31 with
    slot[31] = August 1, and June slot[30] == July slot[0].

    This is not cosmetic — uma.moe rejects a request for a month it considers
    unstarted (HTTP 400 "circle month cannot be in the future"), so on day 1 the
    new month cannot be fetched at all.
    """

    def test_day_one_reads_the_previous_months_rollover_slot(self):
        from utils.jst_calendar import slot_location
        assert slot_location(date(2026, 8, 1)) == (2026, 7, 31)   # July has 31 days

    def test_day_one_after_a_30_day_month(self):
        from utils.jst_calendar import slot_location
        assert slot_location(date(2026, 7, 1)) == (2026, 6, 30)   # June has 30 days

    def test_day_one_of_january_reads_december(self):
        from utils.jst_calendar import slot_location
        assert slot_location(date(2027, 1, 1)) == (2026, 12, 31)

    def test_day_one_after_february(self):
        from utils.jst_calendar import slot_location
        assert slot_location(date(2026, 3, 1)) == (2026, 2, 28)

    def test_other_days_use_their_own_month(self):
        from utils.jst_calendar import slot_location
        assert slot_location(date(2026, 8, 2)) == (2026, 8, 1)
        assert slot_location(date(2026, 7, 25)) == (2026, 7, 24)

    def test_live_target_on_day_one_never_requests_the_new_month(self):
        """The regression: requesting August on Aug 1 returns HTTP 400."""
        t = resolve_live(utc(2026, 8, 1, 12, 49))
        assert (t.year, t.month) == (2026, 7), "would request an unstarted month"
        assert t.slot == 31
        assert t.jst_day == date(2026, 8, 1)
        assert t.is_cross_month is True

    def test_slot_index_always_within_the_array(self):
        """32 slots, so the rollover index must never exceed 31."""
        from utils.jst_calendar import slot_location
        for month in range(1, 13):
            _, _, slot = slot_location(date(2026, month, 1))
            assert 0 <= slot <= 31, f"month {month} rollover slot {slot} out of range"
