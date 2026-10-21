"""Tests for the per-club switch that shows the day's gain instead of the surplus."""
from datetime import date
from types import SimpleNamespace

from services.quota_calculator import QuotaCalculator
from services.report_generator import ReportGenerator


def item(day_fans, surplus=256_400_000, days_behind=0, **extra):
    member = SimpleNamespace(trainer_name="lies")
    history = SimpleNamespace(cumulative_fans=330_658_122, deficit_surplus=surplus,
                              days_behind=days_behind)
    return {'member': member, 'history': history, 'day_fans': day_fans, **extra}


def line(it, is_behind=False, quota_period='daily', daily_gain=True):
    return ReportGenerator()._format_member_line(it, is_behind, quota_period, daily_gain=daily_gain)


class TestDailyLines:
    def test_off_keeps_the_surplus(self):
        assert line(item(14_700_000), daily_gain=False) == \
            "**lies**: +256.4M (330,658,122 total)"

    def test_on_track_shows_the_days_gain(self):
        assert line(item(14_700_000)) == "**lies**: +14.7M (330,658,122 total)"

    def test_behind_keeps_the_deficit_next_to_the_gain(self):
        it = item(3_000_000, surplus=-12_300_000, days_behind=2)
        assert line(it, is_behind=True) == "**lies**: +3.0M (-12.3M, 2 days behind)"

    def test_join_day_has_no_gain(self):
        assert line(item(None)) == "**lies**: joined today (330,658,122 total)"


class TestPeriodLines:
    def test_weekly_swaps_overall_for_today(self):
        info = {'period_quota': 56_000_000, 'quota_label': 'week'}
        it = item(9_000_000, period_fans=30_000_000, period_info=info)
        assert line(it, quota_period='weekly') == \
            "**lies**: 30.0M/56.0M this week (+9.0M today)"


class TestDayGain:
    def gain(self, rows, day):
        return QuotaCalculator.period_gain(rows, day, day)

    def test_against_the_previous_day(self):
        rows = [(date(2026, 10, 18), 300_000_000), (date(2026, 10, 19), 314_700_000)]
        assert self.gain(rows, date(2026, 10, 19)) == 14_700_000

    def test_bridges_a_missing_day(self):
        rows = [(date(2026, 10, 17), 300_000_000), (date(2026, 10, 19), 310_000_000)]
        assert self.gain(rows, date(2026, 10, 19)) == 10_000_000

    def test_first_of_month_restarts(self):
        rows = [(date(2026, 9, 30), 400_000_000), (date(2026, 10, 1), 8_000_000)]
        assert self.gain(rows, date(2026, 10, 1)) == 8_000_000

    def test_no_row_today_is_zero(self):
        rows = [(date(2026, 10, 18), 300_000_000)]
        assert self.gain(rows, date(2026, 10, 19)) == 0
