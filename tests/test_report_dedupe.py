"""The daily report must fire once per club per day, whatever the club's timezone.

Two production bugs live here, both of them the same mistake — the "already
reported" marker being less durable than the report it guards.

1. The marker was an in-memory dict swept against UTC dates. Any club whose local
   date ran ahead of UTC — JST is a day ahead from 15:00 UTC onward, and the live
   board pins such clubs to exactly that moment — had its key evicted on the same
   tick that wrote it, so it re-reported every minute of its trigger hour.
   Observed 2026-08-04: two clubs at 00:00 JST posted 59 and 36 daily reports
   between 15:00 and 15:59 UTC, and one at 01:05 Australia/Sydney alongside them.

2. The dict didn't survive a restart at all. A club counts as due for its whole
   trigger hour, so a bot restarted mid-hour re-ran the check and re-posted the
   report and its kick alerts. Observed 2026-08-13 after a deploy.

The marker now lives in `clubs.last_report_date`, claimed atomically by
`Club.claim_report_day`. `FakeClaims` below is the in-memory stand-in for that
UPDATE; `TestSurvivesRestarts` is the regression test for (2).

The middle of this file is the control: every timezone that was *not* affected by
(1), including the `clubs.timezone` column default, must keep reporting once a day.
"""
import asyncio
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest

import bot.tasks as tasks_module
from bot.tasks import BotTasks
from models import Club

# A club is scraped once a day, so 48h of ticks means two dispatches — for every
# timezone, with or without a live board.
EXPECTED_PER_48H = 2

# config/database.py: `timezone VARCHAR(100) NOT NULL DEFAULT 'Europe/Amsterdam'`
DEFAULT_TIMEZONE = "Europe/Amsterdam"


def make_club(tz, scrape_time=time(16, 0), live_board=False, name="Test"):
    return Club(
        club_id=UUID(int=abs(hash((tz, str(scrape_time), name))) % (1 << 128)),
        club_name=name,
        scrape_url="https://example.invalid",
        circle_id="1",
        guild_id=1,
        daily_quota=1_000_000,
        quota_period="daily",
        timezone=tz,
        scrape_time=scrape_time,
        bomb_trigger_days=3,
        bomb_countdown_days=3,
        bombs_enabled=False,
        image_report_enabled=False,
        is_active=True,
        report_channel_id=1,
        alert_channel_id=1,
        monthly_info_channel_id=None,
        monthly_info_message_id=None,
        created_at=None,
        updated_at=None,
        live_board_channel_id=99 if live_board else None,
    )


class FakeClaims:
    """In-memory stand-in for the `clubs.last_report_date` claim.

    Mirrors `UPDATE ... WHERE last_report_date IS DISTINCT FROM $2 RETURNING`:
    the first caller for a (club, date) wins, everyone after it loses. Unlike the
    dict this replaces, it is owned by the *test*, not by BotTasks — which is the
    whole point, since a restart must not clear it.
    """

    def __init__(self):
        self.last = {}

    async def claim(self, club_id, run_date):
        if self.last.get(club_id) == run_date:
            return False
        self.last[club_id] = run_date
        return True


def run_ticks(monkeypatch, club, *, start="2026-08-03 00:00", hours=48,
              restart_every_minutes=None):
    """Drive the real `scrape_tick` minute by minute against a frozen clock.

    `restart_every_minutes` rebuilds BotTasks on that cadence, simulating a bot
    that keeps being restarted. The claim store deliberately survives, exactly as
    the database column does.

    Returns (dispatch_times_utc, claims).
    """
    real_datetime = tasks_module.datetime

    class FrozenDatetime(real_datetime):
        current = None

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current

    monkeypatch.setattr(tasks_module, "datetime", FrozenDatetime)

    async def fake_get_all_active():
        return [club]

    monkeypatch.setattr(Club, "get_all_active", staticmethod(fake_get_all_active))

    claims = FakeClaims()
    monkeypatch.setattr(Club, "claim_report_day", staticmethod(claims.claim))

    dispatched = []

    def build():
        bot_tasks = BotTasks(object())
        bot_tasks.scheduler.enqueue = (
            lambda c, dispatch_dt, attempt=1: dispatched.append(FrozenDatetime.current)
        )
        return bot_tasks

    t0 = real_datetime.strptime(start, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)

    async def drive():
        bot_tasks = build()
        for i in range(hours * 60):
            if restart_every_minutes and i and i % restart_every_minutes == 0:
                bot_tasks = build()
            FrozenDatetime.current = t0 + timedelta(minutes=i)
            await bot_tasks.scrape_tick.coro(bot_tasks)

    asyncio.run(drive())
    return dispatched, claims


def fires(monkeypatch, club, **kw):
    return run_ticks(monkeypatch, club, **kw)[0]


class TestEasternClubsReportOnce:
    """The regression: a local date ahead of UTC must not evict the run key."""

    @pytest.mark.parametrize("tz", ["Asia/Tokyo", "JST", "Asia/tokyo", "Asia/Seoul"])
    def test_midnight_jst_does_not_repeat(self, monkeypatch, tz):
        """Akitoya (00:00 JST) and wutdahelly (00:00 Asia/Tokyo) in production.

        Parametrised over the spellings actually stored in the clubs table —
        `resolve_timezone` normalises all of them to UTC+9.
        """
        club = make_club(tz, scrape_time=time(0, 0))
        assert len(fires(monkeypatch, club)) == EXPECTED_PER_48H

    def test_sydney_after_midnight_does_not_repeat(self, monkeypatch):
        """Meowmusume: 01:05 Australia/Sydney, re-dispatched every minute."""
        club = make_club("Australia/Sydney", scrape_time=time(1, 5))
        assert len(fires(monkeypatch, club)) == EXPECTED_PER_48H

    def test_furthest_zone_ahead_does_not_repeat(self, monkeypatch):
        """UTC+14 is the largest offset in the tz database."""
        club = make_club("Pacific/Kiritimati", scrape_time=time(4, 0))
        assert len(fires(monkeypatch, club)) == EXPECTED_PER_48H

    def test_live_board_pins_an_eastern_club_to_local_midnight(self, monkeypatch):
        """The live board forces 15:00 UTC, which is 00:00 JST — the worst case.

        This is the path that makes the bug reachable for *any* eastern club,
        whatever scrape time its admin configured.
        """
        club = make_club("Asia/Tokyo", scrape_time=time(16, 0), live_board=True)
        assert club.live_board_enabled
        assert len(fires(monkeypatch, club)) == EXPECTED_PER_48H

    def test_the_repeat_was_confined_to_the_trigger_hour(self, monkeypatch):
        """Sanity check on the shape of the failure, not just the count.

        The old behaviour fired every minute of the hour the club was due and
        then stopped by itself — which is why it looked like it "fixed itself".
        """
        club = make_club("Asia/Tokyo", scrape_time=time(0, 0))
        assert [t.strftime("%H:%M") for t in fires(monkeypatch, club)] == \
            ["15:00", "15:00"]


class TestDefaultTimezoneUnaffected:
    """`Europe/Amsterdam` is the column default, so most clubs are on it.

    It was never broken and must stay unbroken: its local date always equals the
    UTC date at any plausible report time, so widening the keep-window changes
    nothing for it.
    """

    def test_default_timezone_reports_once_a_day(self, monkeypatch):
        club = make_club(DEFAULT_TIMEZONE, scrape_time=time(16, 0))
        assert len(fires(monkeypatch, club)) == EXPECTED_PER_48H

    def test_default_timezone_with_live_board_reports_once_a_day(self, monkeypatch):
        """Live board pins it to 17:00 CEST — same local date, still once."""
        club = make_club(DEFAULT_TIMEZONE, live_board=True)
        assert len(fires(monkeypatch, club)) == EXPECTED_PER_48H

    def test_default_timezone_fires_at_its_configured_time(self, monkeypatch):
        """Not just the right count — the right moment. 16:00 CEST = 14:00 UTC."""
        club = make_club(DEFAULT_TIMEZONE, scrape_time=time(16, 0))
        assert [t.strftime("%H:%M") for t in fires(monkeypatch, club)] == \
            ["14:00", "14:00"]

    @pytest.mark.parametrize("hour", range(24))
    def test_default_timezone_at_every_hour_of_the_day(self, monkeypatch, hour):
        """Exhaustive over the clock, so no configured time can regress."""
        club = make_club(DEFAULT_TIMEZONE, scrape_time=time(hour, 0))
        assert len(fires(monkeypatch, club)) == EXPECTED_PER_48H

    def test_default_timezone_across_the_dst_change(self, monkeypatch):
        """CEST -> CET on 2026-10-25. The 03:00 hour repeats locally."""
        club = make_club(DEFAULT_TIMEZONE, scrape_time=time(3, 0))
        assert len(fires(monkeypatch, club, start="2026-10-24 00:00")) == \
            EXPECTED_PER_48H


class TestOtherZonesStillReportOnce:
    """Every other zone in the production clubs table."""

    @pytest.mark.parametrize("tz,scrape", [
        ("Europe/London", time(16, 0)),
        ("Europe/Paris", time(17, 0)),
        ("America/New_York", time(11, 0)),
        ("America/Chicago", time(11, 0)),
        ("America/Los_Angeles", time(8, 5)),
        ("America/Lima", time(10, 0)),
        ("UTC", time(15, 0)),
        ("Etc/Universal", time(15, 0)),
        ("Asia/Manila", time(23, 0)),
        ("Asia/Jakarta", time(22, 0)),
        ("Asia/Shanghai", time(23, 0)),          # UTC+8, the last safe offset
        ("Pacific/Midway", time(4, 0)),          # UTC-11, a day *behind*
    ])
    def test_reports_once_a_day(self, monkeypatch, tz, scrape):
        club = make_club(tz, scrape_time=scrape)
        assert len(fires(monkeypatch, club)) == EXPECTED_PER_48H

    @pytest.mark.parametrize("tz", ["est", "EST", "CET", "cet", "gmt", "Eastern",
                                    "Netherlands/Amsterdam", "Southamerica/Lima",
                                    "Not/AZone", ""])
    def test_junk_timezone_strings_report_once_a_day(self, monkeypatch, tz):
        """`resolve_timezone` coerces these; whatever it picks must still dedupe."""
        club = make_club(tz, scrape_time=time(16, 0))
        assert len(fires(monkeypatch, club)) == EXPECTED_PER_48H


class TestSurvivesRestarts:
    """The 2026-08-13 regression: restarting must not re-post the report.

    A club is due for its whole trigger hour, so with the marker held in process
    memory every restart inside that hour ran the check again — duplicate report,
    duplicate kick alerts, duplicate DMs.
    """

    def test_restart_inside_the_trigger_hour_does_not_repeat(self, monkeypatch):
        """Restart every minute for two days. Still two reports."""
        club = make_club(DEFAULT_TIMEZONE, scrape_time=time(16, 0))
        assert len(fires(monkeypatch, club, restart_every_minutes=1)) == \
            EXPECTED_PER_48H

    def test_restart_storm_on_an_eastern_club(self, monkeypatch):
        """The worst case: a live-board JST club, whose whole trigger hour is
        15:00 UTC, restarted every minute of it."""
        club = make_club("Asia/Tokyo", scrape_time=time(0, 0), live_board=True)
        assert len(fires(monkeypatch, club, restart_every_minutes=1)) == \
            EXPECTED_PER_48H

    def test_a_restart_still_reports_a_day_it_has_not_done(self, monkeypatch):
        """The guard must not overshoot: restarts are fine, missing a day is not."""
        club = make_club(DEFAULT_TIMEZONE, scrape_time=time(16, 0))
        fired, claims = run_ticks(monkeypatch, club, restart_every_minutes=17)
        assert [t.strftime("%d %H:%M") for t in fired] == ["03 14:00", "04 14:00"]
        assert claims.last[club.club_id] == date(2026, 8, 4)


class TestTheClaimItself:
    """`FakeClaims` has to behave like the SQL it stands in for."""

    def test_first_caller_wins_and_the_rest_lose(self):
        claims = FakeClaims()
        day = date(2026, 8, 13)
        assert asyncio.run(claims.claim("club", day)) is True
        assert asyncio.run(claims.claim("club", day)) is False
        assert asyncio.run(claims.claim("club", day)) is False

    def test_a_new_date_can_be_claimed_again(self):
        claims = FakeClaims()
        assert asyncio.run(claims.claim("club", date(2026, 8, 13))) is True
        assert asyncio.run(claims.claim("club", date(2026, 8, 14))) is True

    def test_clubs_claim_independently(self):
        claims = FakeClaims()
        day = date(2026, 8, 13)
        assert asyncio.run(claims.claim("a", day)) is True
        assert asyncio.run(claims.claim("b", day)) is True
