"""End-to-end write-path tests against a real Postgres.

Skipped unless ``SELFTEST_ADMIN_URL`` is set — it points at a database whose role
may ``CREATE DATABASE``. Each run creates a throwaway database, builds the schema
from scratch (so the migration path in ``initialize_schema`` is exercised as on a
fresh install), drives a simulated month through the full pipeline, then drops it.
Nothing outside the throwaway database is touched.

    SELFTEST_ADMIN_URL="postgresql://user:pw@host/neondb" python -m pytest \
        tests/test_db_write_path.py -v

Covers what the fake-API simulation cannot: quota_history writes, the month
rollover, bomb lifecycle, and idempotency under repeated scrapes.
"""
import asyncio
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

ADMIN_URL = os.getenv("SELFTEST_ADMIN_URL")
pytestmark = pytest.mark.skipif(
    not ADMIN_URL, reason="set SELFTEST_ADMIN_URL to run write-path tests")

UTC = timezone.utc


def _clean(url: str) -> str:
    """asyncpg rejects sslmode / channel_binding in the query string."""
    out = re.sub(r"[?&](sslmode|channel_binding)=[^&]*", "", url)
    return out


def _swap_db(url: str, dbname: str) -> str:
    base = _clean(url).rsplit("/", 1)[0]
    return f"{base}/{dbname}"


# --------------------------------------------------------------------------- #
# throwaway database
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def test_db_url():
    import asyncpg
    name = f"umacore_selftest_{uuid.uuid4().hex[:8]}"

    async def create():
        conn = await asyncpg.connect(_clean(ADMIN_URL), ssl="require", timeout=30)
        try:
            await conn.execute(f'CREATE DATABASE "{name}"')
        finally:
            await conn.close()

    async def drop():
        conn = await asyncpg.connect(_clean(ADMIN_URL), ssl="require", timeout=30)
        try:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()", name)
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await conn.close()

    asyncio.run(create())
    try:
        yield _swap_db(ADMIN_URL, name)
    finally:
        asyncio.run(drop())


@pytest.fixture(scope="module")
def prepared_db(test_db_url):
    """Build the schema once, in its own event loop."""
    async def setup():
        from config.database import db
        db.url = test_db_url
        await db.connect()
        await db.initialize_schema()
        await db.disconnect()

    asyncio.run(setup())
    return test_db_url


def run_db(url, coro_fn):
    """Run one async test body against a clean database.

    Everything happens inside a single ``asyncio.run`` because an asyncpg pool is
    bound to the loop that created it — connecting in one loop and querying in
    another is what "Event loop is closed" means here.
    """
    async def wrapper():
        from config.database import db
        db.url = url
        await db.connect()
        try:
            await db.execute(
                "TRUNCATE clubs, members, quota_history, bombs, "
                "quota_requirements, club_rank_history CASCADE")
            return await coro_fn(db)
        finally:
            await db.disconnect()

    return asyncio.run(wrapper())


async def _make_club(name="SimClub", circle_id="452414222", **kw):
    from models import Club
    from datetime import time as dtime
    return await Club.create(
        club_name=name, scrape_url="https://example.invalid",
        circle_id=circle_id, guild_id=1,
        daily_quota=kw.get("daily_quota", 5_000_000),
        timezone="UTC", scrape_time=kw.get("scrape_time", dtime(17, 0)),
        bomb_trigger_days=kw.get("bomb_trigger_days", 3),
        bomb_countdown_days=kw.get("bomb_countdown_days", 7),
    )


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #

class TestSchemaBuildsClean:
    def test_all_expected_tables_exist(self, prepared_db):
        async def go(db):
            rows = await db.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'")
            return {r["tablename"] for r in rows}
        tables = run_db(prepared_db, go)
        for t in ("clubs", "members", "quota_history", "bombs",
                  "quota_requirements", "club_rank_history", "api_usage"):
            assert t in tables, f"{t} missing after initialize_schema()"

    def test_migrated_columns_present_on_fresh_install(self, prepared_db):
        """The guarded ALTERs must apply on an empty database, not just an old one."""
        async def go(db):
            rows = await db.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='clubs'")
            return {r["column_name"] for r in rows}
        cols = run_db(prepared_db, go)
        for c in ("circle_id", "guild_id", "bombs_enabled", "quota_period",
                  "public_slug", "image_report_enabled",
                  "monthly_info_channel_id", "monthly_info_message_id"):
            assert c in cols, f"clubs.{c} missing on a fresh install"

    def test_idempotent(self, prepared_db):
        """Running it twice must not fail."""
        run_db(prepared_db, lambda db: db.initialize_schema())


# --------------------------------------------------------------------------- #
# month rollover — the logic that replaced the destructive heuristic
# --------------------------------------------------------------------------- #

class TestMonthRollover:
    def test_does_not_fire_mid_month(self, prepared_db):
        async def go(db):
            from services.quota_calculator import QuotaCalculator
            club = await _make_club()
            qc = QuotaCalculator()
            await qc.process_scraped_data(
                club.club_id,
                {"1": {"name": "A", "trainer_id": "1", "fans": [0, 1_000_000], "join_day": 1}},
                date(2026, 7, 10), 2)
            return await qc.handle_month_rollover(club.club_id, date(2026, 7, 11))
        assert run_db(prepared_db, go) is False

    def test_fires_on_month_change(self, prepared_db):
        async def go(db):
            from services.quota_calculator import QuotaCalculator
            club = await _make_club()
            qc = QuotaCalculator()
            await qc.process_scraped_data(
                club.club_id,
                {"1": {"name": "A", "trainer_id": "1", "fans": [0, 1_000_000], "join_day": 1}},
                date(2026, 7, 30), 2)
            return await qc.handle_month_rollover(club.club_id, date(2026, 8, 2))
        assert run_db(prepared_db, go) is True

    def test_preserves_quota_history(self, prepared_db):
        """The old heuristic DELETEd it. It must survive a rollover."""
        async def go(db):
            from services.quota_calculator import QuotaCalculator
            club = await _make_club()
            qc = QuotaCalculator()
            for day, fans in ((10, 3_000_000), (11, 6_000_000)):
                await qc.process_scraped_data(
                    club.club_id,
                    {"1": {"name": "A", "trainer_id": "1",
                           "fans": [0] * (day - 1) + [fans], "join_day": 1}},
                    date(2026, 7, day), day)
            before = await db.fetchval(
                "SELECT count(*) FROM quota_history WHERE club_id=$1", club.club_id)
            await qc.handle_month_rollover(club.club_id, date(2026, 8, 2))
            after = await db.fetchval(
                "SELECT count(*) FROM quota_history WHERE club_id=$1", club.club_id)
            return before, after
        before, after = run_db(prepared_db, go)
        assert before > 0
        assert after == before, "quota_history was destroyed by the rollover"

    def test_preserves_quota_requirements(self, prepared_db):
        """The old delete silently wiped admins' configured quota schedules."""
        async def go(db):
            from services.quota_calculator import QuotaCalculator
            from models import QuotaRequirement
            club = await _make_club()
            qc = QuotaCalculator()
            await QuotaRequirement.create(club.club_id, date(2026, 7, 5),
                                          9_000_000, set_by="admin")
            await qc.process_scraped_data(
                club.club_id,
                {"1": {"name": "A", "trainer_id": "1", "fans": [0, 1_000_000], "join_day": 1}},
                date(2026, 7, 30), 2)
            await qc.handle_month_rollover(club.club_id, date(2026, 8, 2))
            surviving = await db.fetchval(
                "SELECT count(*) FROM quota_requirements WHERE club_id=$1", club.club_id)
            still_applies = await QuotaRequirement.get_quota_for_date(
                club.club_id, date(2026, 8, 5))
            return surviving, still_applies
        surviving, still_applies = run_db(prepared_db, go)
        assert surviving == 1, "quota requirement was deleted by the rollover"
        assert still_applies == 9_000_000, "configured quota stopped applying"

    def test_expires_previous_month_bombs_but_keeps_history(self, prepared_db):
        async def go(db):
            from services.quota_calculator import QuotaCalculator
            from models import Bomb, Member
            club = await _make_club()
            qc = QuotaCalculator()
            await qc.process_scraped_data(
                club.club_id,
                {"1": {"name": "A", "trainer_id": "1", "fans": [0, 1_000_000], "join_day": 1}},
                date(2026, 7, 30), 2)
            member = (await Member.get_all_active(club.club_id))[0]
            await Bomb.create(member_id=member.member_id, club_id=club.club_id,
                              activation_date=date(2026, 7, 20), days_remaining=7)
            active_before = len(await Bomb.get_all_active(club.club_id))
            await qc.handle_month_rollover(club.club_id, date(2026, 8, 2))
            active_after = len(await Bomb.get_all_active(club.club_id))
            rows = await db.fetchval(
                "SELECT count(*) FROM bombs WHERE club_id=$1", club.club_id)
            return active_before, active_after, rows
        before, after, rows = run_db(prepared_db, go)
        assert before == 1
        assert after == 0, "last month's bomb still active in the new month"
        assert rows == 1, "bomb row was deleted instead of deactivated"

    def test_resets_manual_deactivation(self, prepared_db):
        async def go(db):
            from services.quota_calculator import QuotaCalculator
            club = await _make_club()
            qc = QuotaCalculator()
            await qc.process_scraped_data(
                club.club_id,
                {"1": {"name": "A", "trainer_id": "1", "fans": [0, 1_000_000], "join_day": 1}},
                date(2026, 7, 30), 2)
            await db.execute(
                "UPDATE members SET manually_deactivated = TRUE WHERE club_id=$1",
                club.club_id)
            await qc.handle_month_rollover(club.club_id, date(2026, 8, 2))
            return await db.fetchval(
                "SELECT count(*) FROM members WHERE club_id=$1 AND manually_deactivated",
                club.club_id)
        assert run_db(prepared_db, go) == 0

    def test_transferred_member_does_not_wipe_the_month(self, prepared_db):
        """The exact scenario the old >50%-drop heuristic mis-fired on."""
        async def go(db):
            from services.quota_calculator import QuotaCalculator
            club = await _make_club()
            qc = QuotaCalculator()
            # An established member builds history.
            for day in (10, 11, 12):
                await qc.process_scraped_data(
                    club.club_id,
                    {"1": {"name": "Veteran", "trainer_id": "1",
                           "fans": [0] * (day - 1) + [day * 10_000_000], "join_day": 1}},
                    date(2026, 7, day), day)
            before = await db.fetchval(
                "SELECT count(*) FROM quota_history WHERE club_id=$1", club.club_id)
            # Now a transfer joins showing a far smaller monthly figure — a >50%
            # drop versus the stored total.
            await qc.process_scraped_data(
                club.club_id,
                {"1": {"name": "Veteran", "trainer_id": "1",
                       "fans": [0] * 12 + [130_000_000], "join_day": 1},
                 "2": {"name": "Transfer", "trainer_id": "2",
                       "fans": [0] * 12 + [500_000], "join_day": 13}},
                date(2026, 7, 13), 13)
            after = await db.fetchval(
                "SELECT count(*) FROM quota_history WHERE club_id=$1", club.club_id)
            return before, after
        before, after = run_db(prepared_db, go)
        assert after > before, "history shrank — a reset was wrongly triggered"


# --------------------------------------------------------------------------- #
# full pipeline across a month boundary
# --------------------------------------------------------------------------- #

class TestFullMonthPipeline:
    def test_month_of_scrapes_produces_contiguous_history(self, prepared_db):
        """Drive the real scraper + real DB writes across the Jul->Aug boundary."""
        async def go(db):
            from services.quota_calculator import QuotaCalculator
            from tests.fake_umamoe import FakeUmaMoe, default_roster
            from tests.test_month_simulation import make_scraper

            club = await _make_club(daily_quota=2_000_000)
            qc = QuotaCalculator()
            backend = FakeUmaMoe(members=default_roster())

            written = []
            d = date(2026, 7, 20)
            while d <= date(2026, 8, 5):
                now = datetime(d.year, d.month, d.day, 17, tzinfo=UTC)
                scraper = make_scraper(backend, now)
                parsed = await scraper.scrape()
                data_date = scraper.get_data_date()
                await qc.process_scraped_data(
                    club.club_id, parsed, data_date, scraper.get_current_day())
                written.append(data_date)
                d += timedelta(days=1)

            rows = await db.fetch(
                "SELECT date, count(*) n, sum(cumulative_fans) total "
                "FROM quota_history WHERE club_id=$1 GROUP BY date ORDER BY date",
                club.club_id)
            return written, [(r["date"], r["n"], r["total"]) for r in rows]

        written, rows = run_db(prepared_db, go)
        dates = [r[0] for r in rows]

        assert dates == sorted(set(written)), "stored dates differ from reported ones"
        # Within a month, stored dates must be contiguous.
        for month in (7, 8):
            mdays = [d.day for d in dates if d.month == month]
            assert mdays == list(range(min(mdays), max(mdays) + 1)), \
                f"gap in month {month}: {mdays}"
        # Nobody may be recorded with zero fans club-wide on any day.
        for d, n, total in rows:
            assert total and total > 0, f"{d}: club total {total}"

    def test_repeated_scrape_is_idempotent(self, prepared_db):
        """The month-boundary repeat rewrites a row with identical values."""
        async def go(db):
            from services.quota_calculator import QuotaCalculator
            from tests.fake_umamoe import FakeUmaMoe, default_roster
            from tests.test_month_simulation import make_scraper

            club = await _make_club()
            qc = QuotaCalculator()
            backend = FakeUmaMoe(members=default_roster())
            now = datetime(2026, 7, 25, 17, tzinfo=UTC)

            snapshots = []
            for _ in range(3):
                scraper = make_scraper(backend, now)
                parsed = await scraper.scrape()
                await qc.process_scraped_data(
                    club.club_id, parsed, scraper.get_data_date(),
                    scraper.get_current_day())
                rows = await db.fetch(
                    "SELECT member_id, date, cumulative_fans, expected_fans, "
                    "deficit_surplus, days_behind FROM quota_history "
                    "WHERE club_id=$1 ORDER BY member_id, date", club.club_id)
                snapshots.append([tuple(r) for r in rows])
            return snapshots

        snaps = run_db(prepared_db, go)
        assert snaps[0] == snaps[1] == snaps[2], "repeat scrape changed stored state"
        assert snaps[0], "nothing was written"


# --------------------------------------------------------------------------- #
# bomb lifecycle
# --------------------------------------------------------------------------- #

class TestBombLifecycle:
    def test_bomb_activates_after_consecutive_behind_days(self, prepared_db):
        async def go(db):
            from services.quota_calculator import QuotaCalculator
            from services.bomb_manager import BombManager
            from models import Bomb
            club = await _make_club(daily_quota=10_000_000, bomb_trigger_days=3)
            qc, bm = QuotaCalculator(), BombManager()
            for day in (1, 2, 3, 4):
                await qc.process_scraped_data(
                    club.club_id,
                    {"1": {"name": "Slacker", "trainer_id": "1",
                           "fans": [0] * (day - 1) + [100_000], "join_day": 1}},
                    date(2026, 7, day), day)
                await bm.check_and_activate_bombs(club, date(2026, 7, day))
            return len(await Bomb.get_all_active(club.club_id))
        assert run_db(prepared_db, go) >= 1, "no bomb after four days far behind quota"

    def test_expire_before_leaves_current_month_bombs_alone(self, prepared_db):
        async def go(db):
            from models import Bomb, Member
            from services.quota_calculator import QuotaCalculator
            club = await _make_club()
            qc = QuotaCalculator()
            await qc.process_scraped_data(
                club.club_id,
                {"1": {"name": "A", "trainer_id": "1", "fans": [0, 1_000_000], "join_day": 1}},
                date(2026, 8, 3), 2)
            member = (await Member.get_all_active(club.club_id))[0]
            await Bomb.create(member_id=member.member_id, club_id=club.club_id,
                              activation_date=date(2026, 8, 2), days_remaining=7)
            await Bomb.expire_before(club.club_id, date(2026, 8, 1))
            return len(await Bomb.get_all_active(club.club_id))
        assert run_db(prepared_db, go) == 1, "a current-month bomb was expired"
