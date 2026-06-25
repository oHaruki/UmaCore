"""
Internal HTTP API server for web UI integration.
Binds to 127.0.0.1 only — never exposed publicly.
"""
import json
import logging
from uuid import UUID
from datetime import datetime, date, timedelta

import pytz
from aiohttp import web

from utils.timezone_helper import resolve_timezone

from config.database import db
from models import Club
from scrapers import UmaMoeAPIScraper, ChronoGenesisScraper
from services import QuotaCalculator, BombManager, ScrapeContext
from config.settings import USE_UMAMOE_API

logger = logging.getLogger(__name__)


async def _send_json(request: web.Request, data: dict, status: int = 200) -> web.StreamResponse:
    """Send a JSON response, explicitly writing and flushing the body."""
    payload = json.dumps(data).encode('utf-8')
    resp = web.StreamResponse(status=status)
    resp.content_type = 'application/json'
    resp.content_length = len(payload)
    await resp.prepare(request)
    await resp.write(payload)
    await resp.write_eof()
    return resp


async def _backfill_month(club: Club, scraped_data: dict, fetched_year: int, fetched_month: int) -> int:
    """
    Insert quota_history rows for every day in the scraped fans array
    that doesn't already have a record.

    Uma.moe returns daily_fans as a lifetime-converted monthly array where
    fans[i] represents competition results for date(year, month, i).
    join_day is the first index that has data (1-based), so we iterate
    range(join_day, len(fans)) to cover all competition days up to current.
    """
    period_days = {'daily': 1, 'weekly': 7, 'biweekly': 14}.get(club.quota_period, 1)
    default_quota = club.daily_quota

    quota_reqs = await db.fetch(
        "SELECT effective_date, daily_quota FROM quota_requirements "
        "WHERE club_id = $1 ORDER BY effective_date ASC",
        club.club_id
    )

    def quota_for(d: date) -> int:
        q = default_quota
        for row in quota_reqs:
            if row['effective_date'] <= d:
                q = row['daily_quota']
            else:
                break
        return q

    def calc_expected(join_date: date, data_date: date) -> int:
        start_of_month = date(data_date.year, data_date.month, 1)
        start = join_date if join_date >= start_of_month else start_of_month
        total = 0.0
        cur = start
        while cur <= data_date:
            total += quota_for(cur) / period_days
            cur += timedelta(days=1)
        return round(total)

    month_start = date(fetched_year, fetched_month, 1)
    backfilled = 0

    for trainer_id, member_data in scraped_data.items():
        member_row = await db.fetchrow(
            "SELECT member_id, join_date FROM members "
            "WHERE club_id = $1 AND trainer_id = $2 AND is_active = TRUE",
            club.club_id, trainer_id
        )
        if not member_row:
            continue

        member_id = member_row['member_id']
        join_date_val: date = member_row['join_date']
        join_day: int = member_data['join_day']
        fans: list = member_data['fans']

        existing = {
            row['date']: row['deficit_surplus']
            for row in await db.fetch(
                "SELECT date, deficit_surplus FROM quota_history "
                "WHERE member_id = $1 AND date >= $2",
                member_id, month_start
            )
        }

        consecutive_behind = 0

        for i in range(join_day, len(fans)):
            comp_fans = fans[i]
            if comp_fans == 0:
                consecutive_behind = 0
                continue

            comp_date = date(fetched_year, fetched_month, i)

            if comp_date in existing:
                consecutive_behind = (
                    consecutive_behind + 1 if existing[comp_date] < 0 else 0
                )
                continue

            expected = calc_expected(join_date_val, comp_date)
            deficit_surplus = comp_fans - expected
            consecutive_behind = consecutive_behind + 1 if deficit_surplus < 0 else 0

            await db.execute(
                """
                INSERT INTO quota_history
                    (member_id, club_id, date, cumulative_fans, expected_fans,
                     deficit_surplus, days_behind)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (member_id, date) DO NOTHING
                """,
                member_id, club.club_id, comp_date,
                comp_fans, expected, deficit_surplus, consecutive_behind
            )
            backfilled += 1

    return backfilled


async def handle_sync(request: web.Request) -> web.StreamResponse:
    try:
        body = await request.json()
    except Exception:
        return await _send_json(request, {'error': 'Invalid JSON body'}, status=400)

    club_id_str = body.get('club_id')
    if not club_id_str:
        return await _send_json(request, {'error': 'club_id required'}, status=400)

    try:
        club_id = UUID(club_id_str)
    except ValueError:
        return await _send_json(request, {'error': 'Invalid club_id'}, status=400)

    club = await Club.get_by_id(club_id)
    if not club:
        return await _send_json(request, {'error': 'Club not found'}, status=404)
    if not club.is_active:
        return await _send_json(request, {'error': 'Club is not active'}, status=400)

    if USE_UMAMOE_API:
        if not club.circle_id:
            return await _send_json(request, {'error': 'Club has no circle_id configured'}, status=400)
        if not club.is_circle_id_valid():
            return await _send_json(request, {'error': 'Invalid circle_id (must be numeric)'}, status=400)
        scraper = UmaMoeAPIScraper(club.circle_id)
    else:
        scraper = ChronoGenesisScraper(club.scrape_url)

    result: dict | None = None
    error: str | None = None

    try:
        async with ScrapeContext(club.club_id, f"web_sync_{club.club_name}"):
            club_tz = resolve_timezone(club.timezone)
            current_date = datetime.now(club_tz).date()

            scraped_data = await scraper.scrape()
            current_day = scraper.get_current_day()

            data_date = scraper.get_data_date()
            if data_date:
                current_date = data_date

            if not scraped_data:
                error = 'Scraper returned no data'
            else:
                quota_calculator = QuotaCalculator()
                new_members, updated_members = await quota_calculator.process_scraped_data(
                    club.club_id, scraped_data, current_date, current_day,
                    quota_period=club.quota_period
                )

                bomb_manager = BombManager()
                await bomb_manager.check_and_activate_bombs(club, current_date)
                await bomb_manager.check_and_deactivate_bombs(club.club_id, current_date)

                fetched_year = getattr(scraper, '_fetched_year', None) or current_date.year
                fetched_month = getattr(scraper, '_fetched_month', None) or current_date.month
                backfilled = await _backfill_month(club, scraped_data, fetched_year, fetched_month)

                if backfilled:
                    logger.info(f"Backfilled {backfilled} missing quota_history rows for {club.club_name}")

                result = {
                    'success': True,
                    'club_name': club.club_name,
                    'date': str(current_date),
                    'new_members': new_members,
                    'updated_members': updated_members,
                    'backfilled': backfilled,
                }

    except Exception as e:
        logger.error(f"Web sync failed for {club.club_name}: {e}", exc_info=True)
        error = str(e)

    if error:
        return await _send_json(request, {'error': error}, status=500)
    return await _send_json(request, result)


async def handle_recalculate(request: web.Request) -> web.StreamResponse:
    try:
        body = await request.json()
    except Exception:
        return await _send_json(request, {'error': 'Invalid JSON body'}, status=400)

    club_id_str = body.get('club_id')
    if not club_id_str:
        return await _send_json(request, {'error': 'club_id required'}, status=400)

    try:
        club_id = UUID(club_id_str)
    except ValueError:
        return await _send_json(request, {'error': 'Invalid club_id'}, status=400)

    club = await Club.get_by_id(club_id)
    if not club:
        return await _send_json(request, {'error': 'Club not found'}, status=404)

    period_days = {'daily': 1, 'weekly': 7, 'biweekly': 14}.get(club.quota_period, 1)
    default_quota = club.daily_quota

    quota_reqs = await db.fetch(
        "SELECT effective_date, daily_quota FROM quota_requirements "
        "WHERE club_id = $1 ORDER BY effective_date ASC",
        club_id
    )

    def quota_for(d: date) -> int:
        q = default_quota
        for row in quota_reqs:
            if row['effective_date'] <= d:
                q = row['daily_quota']
            else:
                break
        return q

    def calc_expected(join_date: date, data_date: date) -> int:
        month_start = date(data_date.year, data_date.month, 1)
        start = join_date if join_date >= month_start else month_start
        total = 0.0
        cur = start
        while cur <= data_date:
            total += quota_for(cur) / period_days
            cur += timedelta(days=1)
        return round(total)

    today = date.today()
    month_start = date(today.year, today.month, 1)

    members = await db.fetch(
        """
        SELECT DISTINCT m.member_id, m.join_date
        FROM members m
        JOIN quota_history qh ON qh.member_id = m.member_id
        WHERE m.club_id = $1 AND qh.date >= $2
        """,
        club_id, month_start
    )

    updated = 0
    for member in members:
        history = await db.fetch(
            """
            SELECT id, date, cumulative_fans
            FROM quota_history
            WHERE member_id = $1 AND date >= $2
            ORDER BY date ASC
            """,
            member['member_id'], month_start
        )

        consecutive_behind = 0
        for row in history:
            expected = calc_expected(member['join_date'], row['date'])
            deficit_surplus = row['cumulative_fans'] - expected
            consecutive_behind = consecutive_behind + 1 if deficit_surplus < 0 else 0
            await db.execute(
                """
                UPDATE quota_history
                SET expected_fans = $1, deficit_surplus = $2, days_behind = $3
                WHERE id = $4
                """,
                expected, deficit_surplus, consecutive_behind, row['id']
            )
            updated += 1

    logger.info(f"Recalculated {updated} quota_history rows for club {club_id}")
    return await _send_json(request, {'recalculated': updated})


async def handle_guild_roles(request: web.Request) -> web.StreamResponse:
    """List assignable roles for a guild, from the bot's gateway cache.

    Used by the web dashboard to populate the club-editor role picker, since
    Discord only exposes the full role list to the bot token, not user OAuth.
    """
    guild_id_str = request.rel_url.query.get('guild_id')
    if not guild_id_str:
        return await _send_json(request, {'error': 'guild_id required'}, status=400)

    try:
        guild_id = int(guild_id_str)
    except ValueError:
        return await _send_json(request, {'error': 'Invalid guild_id'}, status=400)

    bot = request.app.get('bot')
    if bot is None:
        return await _send_json(request, {'error': 'Bot unavailable'}, status=503)

    guild = bot.get_guild(guild_id)
    if guild is None:
        return await _send_json(
            request,
            {'error': 'Guild not found (bot is not in this server or not ready yet)'},
            status=404,
        )

    roles = [
        {
            'id': str(role.id),
            'name': role.name,
            'color': role.color.value,
            'position': role.position,
            'managed': role.managed,
        }
        for role in guild.roles
        if not role.is_default()  # exclude @everyone
    ]
    roles.sort(key=lambda r: r['position'], reverse=True)
    return await _send_json(request, {'roles': roles})


async def handle_bot_guilds(request: web.Request) -> web.StreamResponse:
    """List the guilds the bot is actually a member of.

    The web dashboard intersects this with the user's admin guilds so you can
    only add a club to a server the bot is present in.
    """
    bot = request.app.get('bot')
    if bot is None:
        return await _send_json(request, {'guilds': []}, status=503)
    guilds = [{'id': str(g.id), 'name': g.name} for g in bot.guilds]
    return await _send_json(request, {'guilds': guilds})


async def handle_health(request: web.Request) -> web.StreamResponse:
    return await _send_json(request, {'status': 'ok'})


async def handle_logs(request: web.Request) -> web.StreamResponse:
    import os
    from config.settings import LOG_FILE
    raw_n = request.rel_url.query.get('lines', '200')
    all_lines = raw_n == 'all'
    n = None if all_lines else max(1, min(int(raw_n), 50000))
    try:
        log_path = LOG_FILE if os.path.isabs(LOG_FILE) else os.path.join(os.getcwd(), LOG_FILE)
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        out = [l.rstrip() for l in lines] if all_lines else [l.rstrip() for l in lines[-n:]]
        return await _send_json(request, {'lines': out, 'total': len(lines)})
    except FileNotFoundError:
        return await _send_json(request, {'lines': [], 'error': 'Log file not found'})


def create_app(bot=None) -> web.Application:
    app = web.Application()
    app['bot'] = bot
    app.router.add_post('/sync', handle_sync)
    app.router.add_post('/recalculate', handle_recalculate)
    app.router.add_get('/guild_roles', handle_guild_roles)
    app.router.add_get('/bot_guilds', handle_bot_guilds)
    app.router.add_get('/health', handle_health)
    app.router.add_get('/logs', handle_logs)
    return app


async def start_api_server(port: int, bot=None) -> web.AppRunner:
    app = create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', port)
    await site.start()
    logger.info(f"Internal API server listening on http://127.0.0.1:{port}")
    return runner
