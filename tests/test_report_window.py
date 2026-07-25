"""Tests for the competition-window line in the daily report.

The window was hardcoded as "16:00 CEST" for every club. uma.moe closes a
competition day at 15:00 UTC, which is 17:00 CEST in summer and 16:00 CET in
winter — so the old string was wrong about the hour in summer, wrong about the
label in winter, and wrong about the timezone entirely for any club outside
central Europe.
"""
from datetime import date

import pytest

from services.report_generator import ReportGenerator


def window(report_date, tz=None):
    return ReportGenerator._competition_window(report_date, tz)


class TestAmsterdamSummer:
    """CEST is UTC+2, so the 15:00 UTC close lands at 17:00 local."""

    def test_shows_17_00_not_16_00(self):
        got = window(date(2026, 7, 24), "Europe/Amsterdam")
        assert "17:00" in got
        assert "16:00" not in got

    def test_labels_the_zone_cest(self):
        assert window(date(2026, 7, 24), "Europe/Amsterdam").endswith("CEST")

    def test_spans_the_reported_day_to_the_next(self):
        got = window(date(2026, 7, 24), "Europe/Amsterdam")
        assert got == "July 24, 17:00 ~ July 25, 17:00 CEST"


class TestAmsterdamWinter:
    """CET is UTC+1, so the same close lands at 16:00 local — and the old
    hardcoded string had the right hour here but called it CEST."""

    def test_shifts_with_dst(self):
        got = window(date(2026, 1, 15), "Europe/Amsterdam")
        assert "16:00" in got
        assert got.endswith("CET")


class TestOtherTimezones:
    """The old string showed CEST to every club regardless of configuration."""

    @pytest.mark.parametrize("tz,expected_hour", [
        ("UTC", "15:00"),
        ("Asia/Tokyo", "00:00"),        # JST midnight — the actual boundary
        ("America/New_York", "11:00"),  # EDT, UTC-4 in July
    ])
    def test_renders_in_the_clubs_own_zone(self, tz, expected_hour):
        assert expected_hour in window(date(2026, 7, 24), tz)

    def test_tokyo_boundary_is_midnight(self):
        """Sanity check on the premise: a JST competition day starts at 00:00 JST."""
        assert window(date(2026, 7, 24), "Asia/Tokyo").startswith("July 25, 00:00")


class TestFallbacks:
    def test_no_timezone_falls_back_to_utc(self):
        got = window(date(2026, 7, 24))
        assert "15:00" in got and got.endswith("UTC")

    def test_unknown_timezone_does_not_raise(self):
        got = window(date(2026, 7, 24), "Not/AZone")
        assert "15:00" in got and got.endswith("UTC")

    def test_month_boundary_rolls_the_date(self):
        got = window(date(2026, 7, 31), "UTC")
        assert got == "July 31, 15:00 ~ August 01, 15:00 UTC"


class TestUsedByTheReport:
    def test_daily_report_embeds_the_window(self):
        gen = ReportGenerator()
        embeds = gen.create_daily_report(
            club_name="Test", daily_quota=1_000_000,
            status_summary={"on_track": [], "behind": [], "total_members": 0,
                            "period_info": None},
            bombs_data=[], report_date=date(2026, 7, 24),
            club_timezone="Europe/Amsterdam",
        )
        assert embeds
        assert "17:00" in embeds[0].description
        assert "CEST" in embeds[0].description
