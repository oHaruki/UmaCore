"""Tests for the slot-index checksum guard.

Calibration comes from six real clubs measured 2026-07-25, whose parsed totals
differed from uma.moe's monthly_point by 1.9M-7.0M fans regardless of club size
(monthly points spanning 125M-1.66B). Because that residual is roughly constant
in absolute terms, it is a large *percentage* on a small club — so the guard
scales against one day's fans, which is the size of an actual off-by-one.
"""
import logging

from scrapers.umamoe_api_scraper import UmaMoeAPIScraper, CircleMeta


SLOT = 3


def make_scraper(monthly_point, yesterday_points):
    s = UmaMoeAPIScraper("999")
    s._meta = CircleMeta(monthly_point=monthly_point, yesterday_points=yesterday_points)
    return s


def members_totalling(total):
    """A raw members array whose leaver-inclusive club total is `total`."""
    fans = [0] * (SLOT + 1)
    fans[0] = 1_000            # baseline (first positive)
    fans[SLOT] = 1_000 + total
    return [{"viewer_id": 1, "trainer_name": "T", "daily_fans": fans}]


def errors_from(caplog, scraper, members, expected, label="monthly_point"):
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        scraper._verify_checksum(members, SLOT, expected, label)
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestChecksumTotalCountsLeavers:
    """monthly_point counts members who left; the parse drops them, so the
    checksum must use the leaver-inclusive total or it flags ordinary churn."""

    def test_active_member_counted_at_target_slot(self):
        m = [{"daily_fans": [100, 200, 300, 400]}]
        assert UmaMoeAPIScraper._checksum_total(m, 3) == 300

    def test_leaver_counted_up_to_last_active_day(self):
        """Zeros after slot 1 mean they left; their gains still count."""
        m = [{"daily_fans": [100, 250, 0, 0]}]
        assert UmaMoeAPIScraper._checksum_total(m, 3) == 150

    def test_negative_transfer_marker_ignored_as_baseline(self):
        """Observed on the real club member 'Histo'."""
        m = [{"daily_fans": [-707_279_154, 1_000, 1_500, 2_000]}]
        assert UmaMoeAPIScraper._checksum_total(m, 3) == 1_000

    def test_member_with_a_single_day_contributes_nothing(self):
        m = [{"daily_fans": [500, 0, 0, 0]}]
        assert UmaMoeAPIScraper._checksum_total(m, 3) == 0


class TestRealWorldClubsPass:
    """Every club measured on 2026-07-25 must pass."""

    # (name, monthly_point, yesterday_points, parsed_total)
    CLUBS = [
        ("WingNight", 1_656_751_635, 1_500_008_760, 1_653_921_172),
        ("UnStble", 1_628_160_851, 1_430_627_051, 1_622_663_923),
        ("Turfcore", 124_898_261, 105_005_778, 121_620_634),
    ]

    def test_all_pass(self, caplog):
        for name, mp, yp, parsed in self.CLUBS:
            s = make_scraper(mp, yp)
            errs = errors_from(caplog, s, members_totalling(parsed), mp)
            assert not errs, f"{name} should not be flagged: {[r.message for r in errs]}"

    def test_turfcore_would_fail_a_flat_percentage(self):
        """Regression: a flat 2% rule flagged the smallest club for ordinary churn."""
        mp, parsed = 124_898_261, 121_620_634
        assert abs(parsed - mp) / mp > 0.02          # would trip a flat 2% rule
        day_gain = mp - 105_005_778
        assert abs(parsed - mp) / day_gain < 0.5      # but is well under half a day


class TestSlotErrorIsCaught:
    """The guard must still fire when we really do read the wrong slot."""

    MONTHLY, YESTERDAY = 1_656_751_635, 1_500_008_760
    DAY_GAIN = MONTHLY - YESTERDAY   # 156,742,875

    def test_off_by_one_slot_backwards(self, caplog):
        """Reading yesterday's slot: off by a full day."""
        s = make_scraper(self.MONTHLY, self.YESTERDAY)
        errs = errors_from(caplog, s, members_totalling(self.YESTERDAY), self.MONTHLY)
        assert errs, "a full-day discrepancy must be flagged"
        assert "wrong" in errs[0].message

    def test_off_by_one_slot_forwards(self, caplog):
        """Reading the live in-progress slot — the actual bug this replaced."""
        s = make_scraper(self.MONTHLY, self.YESTERDAY)
        live_total = 1_782_053_296        # measured live_points for this club
        errs = errors_from(caplog, s, members_totalling(live_total), self.MONTHLY)
        assert errs, "reading the live slot must be flagged"

    def test_just_over_half_a_day_is_flagged(self, caplog):
        s = make_scraper(self.MONTHLY, self.YESTERDAY)
        total = self.MONTHLY - int(self.DAY_GAIN * 0.55)
        assert errors_from(caplog, s, members_totalling(total), self.MONTHLY)

    def test_just_under_half_a_day_passes(self, caplog):
        s = make_scraper(self.MONTHLY, self.YESTERDAY)
        total = self.MONTHLY - int(self.DAY_GAIN * 0.45)
        assert not errors_from(caplog, s, members_totalling(total), self.MONTHLY)


class TestNoDayReference:
    """Without yesterday_points the check is skipped entirely.

    That field is only absent while a competition period is turning over, and the
    circle totals are unreliable exactly then. Measured across the 2026-08
    rollover: July's monthly_point fell from 1,837,269,789 to 1,165,044,213
    between reads while the member rows were unchanged, which flagged all four
    real clubs at 63-91%. Comparing against a figure known to be wrong produces
    only false alarms.
    """

    def test_small_club_churn_not_flagged(self, caplog):
        s = make_scraper(124_898_261, None)
        assert not errors_from(caplog, s, members_totalling(121_620_634), 124_898_261)

    def test_large_deviation_not_flagged_either(self, caplog):
        """The 2026-08 case: a huge gap caused by the reference, not the parse."""
        s = make_scraper(1_165_044_213, None)
        assert not errors_from(caplog, s, members_totalling(1_902_368_089), 1_165_044_213)

    def test_still_flags_when_a_day_reference_exists(self, caplog):
        """The guard must keep working on an ordinary day."""
        s = make_scraper(1_656_751_635, 1_500_008_760)
        assert errors_from(caplog, s, members_totalling(1_500_008_760), 1_656_751_635)


class TestGuardIsSafe:
    def test_no_expected_total_is_noop(self, caplog):
        s = make_scraper(None, None)
        assert not errors_from(caplog, s, members_totalling(123), None)

    def test_empty_members_is_noop(self, caplog):
        s = make_scraper(1_000_000, 900_000)
        assert not errors_from(caplog, s, [], 1_000_000)

    def test_zero_or_negative_day_gain_falls_back(self, caplog):
        """A non-positive day gain must not divide by zero."""
        s = make_scraper(1_000_000, 1_000_000)
        assert s._reference_day_gain() is None
        errors_from(caplog, s, members_totalling(999_000), 1_000_000)  # must not raise


class TestFreshnessGate:
    """The gate decides whether a day has finalized. Getting it wrong loses a
    whole day of history: the scheduler retries, gives up, and writes nothing.

    Measured 2026-08-02 across the month rollover:
        yesterday_updated  2026-08-01T15:01:00Z   the finalize, on every circle
        last_updated       2026-08-01T18:06:30Z   touched at unrelated times
                           2026-08-01T10:00:28Z   earlier the same day
    Gating on last_updated alone rejected a day that had already finalized.
    """
    from datetime import date as _date

    TARGET = None  # built per-test

    def _target(self):
        from utils.jst_calendar import SlotTarget
        from datetime import date
        return SlotTarget(year=2026, month=7, slot=31, jst_day=date(2026, 8, 1),
                          data_date=date(2026, 7, 31), is_live=False)

    def _check(self, last_updated, yesterday_updated):
        from scrapers.base_scraper import StaleDataError
        s = UmaMoeAPIScraper("1")
        s._meta = CircleMeta(last_updated=last_updated,
                             yesterday_updated=yesterday_updated)
        try:
            s._assert_finalized(self._target())
            return False
        except StaleDataError:
            return True

    def _utc(self, y, mo, d, h, mi=0, s=0):
        from datetime import datetime, timezone
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)

    def test_accepts_a_finalized_day_despite_a_stale_last_updated(self):
        """The 2026-08-01 regression that cost a day of history."""
        assert not self._check(self._utc(2026, 8, 1, 10, 0, 28),
                               self._utc(2026, 8, 1, 15, 1))

    def test_rejects_when_every_timestamp_predates_the_close(self):
        assert self._check(self._utc(2026, 8, 1, 10, 0, 28),
                           self._utc(2026, 7, 31, 15, 1))

    def test_accepts_when_last_updated_is_itself_the_finalize(self):
        """The ordinary case, where the two fields agree."""
        assert not self._check(self._utc(2026, 8, 1, 15, 0, 44), None)

    def test_accepts_on_yesterday_updated_alone(self):
        assert not self._check(None, self._utc(2026, 8, 1, 15, 1))

    def test_permissive_when_no_timestamp_is_present(self):
        """Never block on a missing field — losing a day is worse than a stale read."""
        assert not self._check(None, None)
