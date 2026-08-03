"""Tests for QuotaSchedule — the in-memory quota resolver.

Replaces a per-date lookup that cost up to two queries each and was called once
per day per member, so these assertions are about behaviour equivalence: the
resolver must answer exactly what the old SQL answered.
"""
from datetime import date

import pytest

from models.quota_requirement import QuotaSchedule


def sched(pairs, default=1_000_000):
    return QuotaSchedule.from_pairs(pairs, default)


class TestResolution:
    def test_empty_schedule_uses_club_default(self):
        s = sched([], default=5_000_000)
        assert s.for_date(date(2026, 7, 15)) == 5_000_000

    def test_date_before_first_requirement_uses_default(self):
        s = sched([(date(2026, 7, 10), 9_000_000)], default=5_000_000)
        assert s.for_date(date(2026, 7, 9)) == 5_000_000

    def test_effective_date_is_inclusive(self):
        s = sched([(date(2026, 7, 10), 9_000_000)], default=5_000_000)
        assert s.for_date(date(2026, 7, 10)) == 9_000_000

    def test_latest_applicable_requirement_wins(self):
        s = sched([
            (date(2026, 7, 1), 2_000_000),
            (date(2026, 7, 10), 9_000_000),
            (date(2026, 7, 20), 3_000_000),
        ], default=5_000_000)
        assert s.for_date(date(2026, 7, 5)) == 2_000_000
        assert s.for_date(date(2026, 7, 10)) == 9_000_000
        assert s.for_date(date(2026, 7, 19)) == 9_000_000
        assert s.for_date(date(2026, 7, 20)) == 3_000_000
        assert s.for_date(date(2026, 7, 25)) == 3_000_000

    def test_requirements_carry_across_months(self):
        """A July requirement still applies in August — this is why the old
        month-rollover delete was wrong to remove them."""
        s = sched([(date(2026, 7, 5), 9_000_000)], default=5_000_000)
        assert s.for_date(date(2026, 8, 20)) == 9_000_000

    def test_input_order_does_not_matter(self):
        forward = sched([(date(2026, 7, 1), 1), (date(2026, 7, 10), 2)])
        reverse = sched([(date(2026, 7, 10), 2), (date(2026, 7, 1), 1)])
        for day in (1, 5, 10, 30):
            d = date(2026, 7, day)
            assert forward.for_date(d) == reverse.for_date(d)


class TestEquivalenceWithOldSql:
    """The replaced query was:

        SELECT daily_quota FROM quota_requirements
        WHERE club_id = $1 AND effective_date <= $2
        ORDER BY effective_date DESC LIMIT 1

    ...falling back to the club default when nothing matched.
    """

    REQS = [
        (date(2026, 6, 15), 4_000_000),
        (date(2026, 7, 1), 7_000_000),
        (date(2026, 7, 18), 2_500_000),
    ]
    DEFAULT = 1_000_000

    def reference(self, check_date):
        applicable = [(d, q) for d, q in self.REQS if d <= check_date]
        if not applicable:
            return self.DEFAULT
        return max(applicable, key=lambda pair: pair[0])[1]

    @pytest.mark.parametrize("day", range(1, 32))
    def test_matches_reference_across_july(self, day):
        s = sched(self.REQS, self.DEFAULT)
        d = date(2026, 7, day)
        assert s.for_date(d) == self.reference(d)

    @pytest.mark.parametrize("day", range(1, 31))
    def test_matches_reference_across_june(self, day):
        s = sched(self.REQS, self.DEFAULT)
        d = date(2026, 6, day)
        assert s.for_date(d) == self.reference(d)


class TestExpectedFansUsesSchedule:
    """calculate_expected_fans must accept a pre-loaded schedule and not query."""

    def test_sums_quota_skipping_the_join_day(self):
        import asyncio
        from services.quota_calculator import QuotaCalculator

        s = sched([], default=1_000_000)
        # Joined Jul 1, reporting Jul 5: the join day carries no quota, so 4 days.
        got = asyncio.run(QuotaCalculator.calculate_expected_fans(
            club_id=None, member_join_date=date(2026, 7, 1),
            current_date=date(2026, 7, 5), schedule=s))
        assert got == 4_000_000

    def test_joined_before_this_month_counts_from_the_first(self):
        import asyncio
        from services.quota_calculator import QuotaCalculator

        s = sched([], default=1_000_000)
        got = asyncio.run(QuotaCalculator.calculate_expected_fans(
            club_id=None, member_join_date=date(2026, 5, 9),
            current_date=date(2026, 7, 4), schedule=s))
        assert got == 4_000_000      # Jul 1..4, no join day to skip

    def test_weekly_period_divides_the_stored_amount(self):
        import asyncio
        from services.quota_calculator import QuotaCalculator

        s = sched([], default=7_000_000)
        got = asyncio.run(QuotaCalculator.calculate_expected_fans(
            club_id=None, member_join_date=date(2026, 5, 9),
            current_date=date(2026, 7, 7), quota_period='weekly', schedule=s))
        assert got == 7_000_000      # 7 days x (7M / 7)

    def test_mid_month_quota_change_is_honoured(self):
        import asyncio
        from services.quota_calculator import QuotaCalculator

        s = sched([(date(2026, 7, 3), 2_000_000)], default=1_000_000)
        got = asyncio.run(QuotaCalculator.calculate_expected_fans(
            club_id=None, member_join_date=date(2026, 6, 1),
            current_date=date(2026, 7, 4), schedule=s))
        # Jul 1,2 at 1M; Jul 3,4 at 2M
        assert got == 1_000_000 * 2 + 2_000_000 * 2

    def test_passing_a_schedule_issues_no_queries(self):
        """The point of the change: no DB access when a schedule is supplied.

        club_id is None here — any attempt to load would raise.
        """
        import asyncio
        from services.quota_calculator import QuotaCalculator

        s = sched([], default=1_000_000)
        asyncio.run(QuotaCalculator.calculate_expected_fans(
            club_id=None, member_join_date=date(2026, 7, 1),
            current_date=date(2026, 7, 28), schedule=s))
