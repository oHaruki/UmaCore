"""Biweeks on a club's own 14-day cycle (clubs.period_anchor_date).

Calendar biweeks reset on the 1st and 15th. A club that tallies every 14 days
from its own start date needs periods that run through that date instead,
including across a month end, where cumulative fans restart.
"""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from models.quota_requirement import QuotaSchedule
from services.quota_calculator import QuotaCalculator
from services.report_generator import ReportGenerator

info = QuotaCalculator.get_period_info
gain = QuotaCalculator.period_gain

OCT4 = date(2026, 10, 4)


def window(d, anchor=OCT4, period='biweekly'):
    p = info(period, d, anchor)
    return p['period_start'], p['period_end']


class TestAnchoredWindows:
    def test_tally_days_open_each_biweek(self):
        """Oct 4 -> Oct 18 -> Nov 1, the cycle the club actually runs."""
        starts = []
        d = date(2026, 10, 1)
        while d <= date(2026, 12, 31):
            start, _ = window(d)
            if start not in starts:
                starts.append(start)
            d += timedelta(days=1)
        assert starts == [date(2026, 9, 20), OCT4, date(2026, 10, 18), date(2026, 11, 1),
                          date(2026, 11, 15), date(2026, 11, 29), date(2026, 12, 13),
                          date(2026, 12, 27)]

    def test_runs_are_always_14_days(self):
        d = date(2026, 1, 1)
        while d <= date(2027, 12, 31):
            start, end = window(d)
            assert (end - start).days == 13
            assert start <= d <= end
            d += timedelta(days=1)

    def test_first_and_last_day(self):
        p = info('biweekly', OCT4, OCT4)
        assert (p['period_start'], p['period_end'], p['day_number']) == (OCT4, date(2026, 10, 17), 1)
        p = info('biweekly', date(2026, 10, 17), OCT4)
        assert (p['period_start'], p['day_number']) == (OCT4, 14)

    def test_crosses_the_month_end(self):
        assert window(date(2026, 12, 3)) == (date(2026, 11, 29), date(2026, 12, 12))

    def test_crosses_the_year_end(self):
        assert window(date(2027, 1, 2)) == (date(2026, 12, 27), date(2027, 1, 9))

    def test_dates_before_the_anchor(self):
        assert window(date(2026, 10, 3)) == (date(2026, 9, 20), date(2026, 10, 3))

    def test_any_start_in_the_cycle_is_the_same_cycle(self):
        """A past or future tally day works as the anchor too."""
        for d in (date(2026, 10, 7), date(2026, 11, 30), date(2027, 2, 1)):
            assert window(d, OCT4) == window(d, date(2026, 11, 15)) \
                == window(d, OCT4 - timedelta(days=14 * 20))

    def test_marked_anchored(self):
        p = info('biweekly', date(2026, 10, 7), OCT4)
        assert p['anchored'] is True
        assert p['period_days'] == 14
        assert p['quota_label'] == 'biweek'


class TestCalendarModeUnchanged:
    def test_no_anchor_keeps_calendar_blocks(self):
        p = info('biweekly', date(2026, 10, 7))
        assert (p['period_number'], p['total_periods']) == (1, 3)
        assert (p['period_start'], p['period_end']) == (date(2026, 10, 1), date(2026, 10, 14))
        assert p['anchored'] is False
        assert p['day_number'] == 7

    def test_month_tail_block(self):
        p = info('biweekly', date(2026, 10, 30))
        assert (p['period_start'], p['period_end'], p['day_number']) == (
            date(2026, 10, 29), date(2026, 10, 31), 2)

    def test_weekly_ignores_the_anchor(self):
        p = info('weekly', date(2026, 10, 10), OCT4)
        assert p['anchored'] is False
        assert (p['period_start'], p['period_end']) == (date(2026, 10, 8), date(2026, 10, 14))

    def test_daily_has_no_period(self):
        assert info('daily', date(2026, 10, 10), OCT4) is None


def rows(month_start: date, per_day: int, first_day: int, last_day: int, join_day=None):
    """Cumulative rows for one month: 0 on the join day, +per_day after."""
    out = []
    for day in range(first_day, last_day + 1):
        earned_days = day if join_day is None else day - join_day
        out.append((month_start.replace(day=day), per_day * earned_days))
    return out


class TestPeriodGain:
    def test_mid_month_window(self):
        oct_rows = rows(date(2026, 10, 1), 1_000_000, 1, 10)
        assert gain(oct_rows, OCT4, date(2026, 10, 10)) == 7_000_000

    def test_window_opening_on_the_1st(self):
        nov_rows = rows(date(2026, 11, 1), 1_000_000, 1, 5)
        assert gain(nov_rows, date(2026, 11, 1), date(2026, 11, 5)) == 5_000_000

    def test_across_the_month_end(self):
        """Nov 29 - Dec 3: two November days plus three December days."""
        history = rows(date(2026, 11, 1), 1_000_000, 1, 30) + rows(date(2026, 12, 1), 1_000_000, 1, 3)
        assert gain(history, date(2026, 11, 29), date(2026, 12, 3)) == 5_000_000

    def test_full_cross_month_period(self):
        history = rows(date(2026, 11, 1), 2_000_000, 1, 30) + rows(date(2026, 12, 1), 2_000_000, 1, 12)
        assert gain(history, date(2026, 11, 29), date(2026, 12, 12)) == 28_000_000

    def test_joined_inside_the_period(self):
        """No row before the period, and the join day itself is +0."""
        history = rows(date(2026, 10, 1), 1_000_000, 10, 15, join_day=10)
        assert gain(history, OCT4, date(2026, 10, 15)) == 5_000_000

    def test_joined_before_the_period_this_month(self):
        history = rows(date(2026, 10, 1), 1_000_000, 2, 12, join_day=2)
        assert gain(history, OCT4, date(2026, 10, 12)) == 9_000_000

    def test_missing_baseline_day_uses_the_last_earlier_row(self):
        """Not 0, which would credit the whole month to this period."""
        history = [r for r in rows(date(2026, 10, 1), 1_000_000, 1, 10) if r[0].day != 3]
        assert gain(history, OCT4, date(2026, 10, 10)) == 8_000_000

    def test_ignores_rows_after_through(self):
        history = rows(date(2026, 10, 1), 1_000_000, 1, 20)
        assert gain(history, OCT4, date(2026, 10, 10)) == 7_000_000

    def test_nothing_in_the_window(self):
        history = rows(date(2026, 10, 1), 1_000_000, 1, 3)
        assert gain(history, OCT4, date(2026, 10, 3)) == 0
        assert gain([], OCT4, date(2026, 10, 10)) == 0

    def test_older_months_never_count(self):
        history = rows(date(2026, 9, 1), 9_000_000, 1, 30) + rows(date(2026, 10, 1), 1_000_000, 1, 6)
        assert gain(history, OCT4, date(2026, 10, 6)) == 3_000_000


class TestPeriodQuota:
    def test_full_anchored_period(self):
        s = QuotaSchedule.from_pairs([], default_quota=14_000_000)
        p = info('biweekly', date(2026, 11, 30), OCT4)
        assert QuotaCalculator.period_quota(s, p) == 14_000_000

    def test_quota_change_partway_through(self):
        s = QuotaSchedule.from_pairs([(date(2026, 10, 11), 28_000_000)], default_quota=14_000_000)
        p = info('biweekly', date(2026, 10, 7), OCT4)
        assert QuotaCalculator.period_quota(s, p) == 7_000_000 + 14_000_000

    def test_calendar_tail_is_prorated(self):
        s = QuotaSchedule.from_pairs([], default_quota=14_000_000)
        p = info('biweekly', date(2026, 10, 30))
        assert QuotaCalculator.period_quota(s, p) == 3_000_000


def summary(period_info, period_fans, surplus=1_000_000):
    member = SimpleNamespace(trainer_name="Steady")
    history = SimpleNamespace(cumulative_fans=50_000_000, deficit_surplus=surplus, days_behind=0)
    item = {'member': member, 'history': history, 'period_fans': period_fans,
            'period_info': period_info}
    return {'on_track': [item] if surplus >= 0 else [], 'behind': [] if surplus >= 0 else [item],
            'total_members': 1, 'period_info': period_info}


class TestReport:
    def render(self, period_info, period_fans, **kw):
        embeds = ReportGenerator().create_daily_report(
            club_name="Club", daily_quota=14_000_000,
            status_summary=summary(period_info, period_fans, **kw), bombs_data=[],
            report_date=date(2026, 12, 3), quota_period='biweekly', club_timezone='UTC')
        return embeds[0].description, embeds[1].description

    def test_anchored_period_line(self):
        p = info('biweekly', date(2026, 12, 3), OCT4)
        p['period_quota'] = 14_000_000
        header, line = self.render(p, 5_000_000)
        assert "**Period:** Biweek Nov 29 – Dec 12 (day 5 of 14)" in header
        assert line == "**Steady**: 5.0M/14.0M this biweek (+1.0M overall)"

    def test_calendar_period_line_unchanged(self):
        p = info('biweekly', date(2026, 10, 7))
        p['period_quota'] = 14_000_000
        header, _ = self.render(p, 7_000_000)
        assert "**Period:** Biweek 1 of 3 (Oct 01 – Oct 14)" in header

    def test_behind_line_uses_period_fans(self):
        p = info('biweekly', date(2026, 12, 3), OCT4)
        p['period_quota'] = 14_000_000
        _, line = self.render(p, 2_500_000, surplus=-3_000_000)
        assert line == "**Steady**: 2.5M/14.0M this biweek (-3.0M overall)"
