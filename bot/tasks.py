"""
Scheduled tasks for the Discord bot
"""
import discord
from discord.ext import tasks
from datetime import datetime, time, timedelta, timezone
import logging
import asyncio

import os
import zlib
from pathlib import Path

from models import Club, Member, ClubRankHistory, QuotaRequirement
from scrapers import ChronoGenesisScraper, UmaMoeAPIScraper, StaleDataError
from services import (
    QuotaCalculator, BombManager, ReportGenerator, NotificationService,
    ScrapeLockManager, ScrapeContext, ScrapeScheduler,
)
from services.tally_renderer import generate_tally_image
from utils.timezone_helper import resolve_timezone
from utils.jst_calendar import ROLLOVER_UTC_HOUR
from services.backup_service import create_backup, find_pg_dump
from services.live_board import update_club as update_live_board
from config.settings import (
    USE_UMAMOE_API, SCRAPE_ROLLOVER_GRACE_SEC, SCRAPE_MAX_FRESHNESS_RETRIES,
    SCRAPE_FRESHNESS_RETRY_DELAY_SEC, SCRAPE_MAX_CONCURRENCY,
    DATABASE_URL, DB_BACKUP_ENABLED, DB_BACKUP_DIR, DB_BACKUP_KEEP,
    DB_BACKUP_UTC_TIME, DB_BACKUP_TIMEOUT_SEC,
)

logger = logging.getLogger(__name__)


class BotTasks:
    """Manages scheduled tasks for the bot"""

    def __init__(self, bot):
        self.bot = bot
        self.quota_calculator = QuotaCalculator()
        self.bomb_manager = BombManager()
        self.report_generator = ReportGenerator()
        self.notification_service = NotificationService(bot)

        # Track last run per club per day (club_id_YYYY-MM-DD -> True)
        self.last_runs = {}

        # Parse the backup time once; fall back to a quiet default if malformed.
        try:
            bh, bm = map(int, DB_BACKUP_UTC_TIME.split(":"))
            self._backup_time = time(bh, bm)
        except Exception:
            logger.warning(
                f"Invalid DB_BACKUP_UTC_TIME '{DB_BACKUP_UTC_TIME}', defaulting to 03:30 UTC"
            )
            self._backup_time = time(3, 30)

        # Time-ordered dispatcher: the tick enqueues due clubs, the scheduler
        # releases them in dispatch order and re-queues fetches that came back
        # stale (uma.moe hadn't finalized that circle yet).
        self.scheduler = ScrapeScheduler(
            worker=self._scheduled_worker,
            concurrency=SCRAPE_MAX_CONCURRENCY,
            max_retries=SCRAPE_MAX_FRESHNESS_RETRIES,
            retry_delay=SCRAPE_FRESHNESS_RETRY_DELAY_SEC,
        )

        logger.info("Multi-club tasks configured - time-ordered scheduler, tick every minute")

    def start_tasks(self):
        """Start all scheduled tasks"""
        self.scheduler.start()
        self.scrape_tick.start()
        self.live_board_tick.start()
        if DB_BACKUP_ENABLED:
            self.daily_backup.change_interval(time=self._backup_time)
            self.daily_backup.start()
            # Resolve the directory so the log is unambiguous — DB_BACKUP_DIR is
            # relative to the working directory, which systemd sets.
            logger.info(
                f"Daily DB backup enabled at {self._backup_time.strftime('%H:%M')} UTC "
                f"→ {Path(DB_BACKUP_DIR).resolve()} (keeping {DB_BACKUP_KEEP})"
            )
            # Check the one external dependency now rather than discovering it
            # missing at 03:30, when nobody is watching the logs.
            if not find_pg_dump():
                logger.warning(
                    "⚠️ pg_dump not found — daily backups WILL FAIL. Install it with "
                    "`sudo apt install postgresql-client`, or set PG_DUMP_PATH if it "
                    "lives somewhere unusual. Check with /backup status."
                )
        else:
            logger.info("Daily DB backup disabled (DB_BACKUP_ENABLED=false)")
        logger.info("Scheduled tasks started (per-minute tick + rank-ordered scheduler)")

    def stop_tasks(self):
        """Stop all scheduled tasks"""
        self.scrape_tick.cancel()
        self.live_board_tick.cancel()
        if self.daily_backup.is_running():
            self.daily_backup.cancel()
        self.scheduler.stop()
        logger.info("Scheduled tasks stopped")

    @tasks.loop(time=time(3, 30))
    async def daily_backup(self):
        """Dump the database once a day and prune to the retention limit.

        Runs in-process so backups need no cron entry or VPS-side setup. Failures
        are logged and never interrupt scraping — a missing backup is a problem to
        fix, not a reason to stop reporting.
        """
        result = await create_backup(
            database_url=DATABASE_URL,
            backup_dir=Path(DB_BACKUP_DIR),
            keep=DB_BACKUP_KEEP,
            timeout_sec=DB_BACKUP_TIMEOUT_SEC,
        )
        if not result.ok:
            logger.error(f"❌ Daily DB backup failed: {result.error}")

    @daily_backup.before_loop
    async def before_daily_backup(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def live_board_tick(self):
        """Refresh live boards, spread evenly across the hour.

        Each club gets a stable slot minute derived from its id, so 200 clubs
        become ~3 API calls a minute instead of a 200-call burst that would
        saturate the shared limiter and stall interactive commands behind it.
        The spread also matches reality: uma.moe's live batch reaches
        lower-ranked circles several minutes after the top ones.
        """
        now_utc = datetime.now(timezone.utc)

        try:
            clubs = await Club.get_live_board_clubs()
        except Exception as e:
            logger.error(f"live_board_tick: failed to load clubs: {e}", exc_info=True)
            return

        if not clubs:
            return

        due = [c for c in clubs if self._live_slot_minute(c) == now_utc.minute]
        for club in due:
            try:
                await update_live_board(self.bot, club, now_utc=now_utc)
            except Exception as e:
                # One club's board must never take down the others.
                logger.error(
                    f"live_board_tick: {club.club_name} failed: {e}", exc_info=True
                )

    @staticmethod
    def _live_slot_minute(club: Club) -> int:
        """Stable minute-of-hour for a club, so the load spreads deterministically."""
        return zlib.crc32(str(club.club_id).encode()) % 60

    @live_board_tick.before_loop
    async def before_live_board_tick(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def scrape_tick(self):
        """Every minute, enqueue any club that's reached its scrape time.

        Enqueueing (not running) lets the scheduler release clubs in rank order
        and stagger the 17:00 default clump across the rollout window.
        """
        now_utc = datetime.now(timezone.utc)

        try:
            clubs = await Club.get_all_active()
        except Exception as e:
            logger.error(f"scrape_tick: failed to load clubs: {e}", exc_info=True)
            return

        for club in clubs:
            try:
                club_tz = resolve_timezone(club.timezone)
                now_in_club_tz = now_utc.astimezone(club_tz)
                current_date = now_in_club_tz.date()

                effective = self._effective_scrape_time(club, now_utc)
                target_hour = effective.hour
                target_minute = effective.minute

                if not (now_in_club_tz.hour == target_hour and now_in_club_tz.minute >= target_minute):
                    continue

                run_key = f"{club.club_id}_{current_date}"
                if self.last_runs.get(run_key):
                    continue
                self.last_runs[run_key] = True

                dispatch_dt, held = await self._compute_dispatch_utc(club, now_utc)
                tag = "held for rollover grace" if held else "on time"
                pinned = " · pinned to day close (live board)" if club.live_board_enabled else ""
                logger.info(
                    f"⏰ {club.club_name} due ({now_in_club_tz.strftime('%H:%M')} {club.timezone}) — "
                    f"dispatch {dispatch_dt.strftime('%H:%M:%S')} UTC [{tag}]{pinned}"
                )
                self.scheduler.enqueue(club, dispatch_dt)

            except Exception as e:
                logger.error(f"scrape_tick: error for {club.club_name}: {e}", exc_info=True)
                continue

        self._prune_last_runs(now_utc)

    @staticmethod
    def _effective_scrape_time(club: Club, now_utc: datetime) -> time:
        """When this club should be scraped, in its own timezone.

        Clubs running a live board are pinned to the competition day close
        (15:00 UTC) rather than their configured time. The board finalises that
        day at the close, so scraping earlier would leave the daily report a day
        behind the board it sits next to — two surfaces disagreeing about the
        same club on the same screen.

        Clubs without a live board keep whatever time they set.

        Recomputed from ``now_utc`` on every tick, so the local equivalent of
        15:00 UTC follows the club's DST rather than drifting an hour twice a year.
        """
        if not club.live_board_enabled:
            return club.scrape_time

        tz = resolve_timezone(club.timezone)
        close_local = now_utc.replace(
            hour=ROLLOVER_UTC_HOUR, minute=0, second=0, microsecond=0
        ).astimezone(tz)
        return time(close_local.hour, close_local.minute)

    def _prune_last_runs(self, now_utc: datetime) -> None:
        """Drop run keys older than two days.

        The dict is keyed ``{club_id}_{date}`` and was never cleaned, so it grew
        by one entry per club per day for the process's lifetime.
        """
        keep = {str((now_utc - timedelta(days=d)).date()) for d in range(3)}
        stale = [k for k in self.last_runs if k.rsplit("_", 1)[-1] not in keep]
        for k in stale:
            del self.last_runs[k]

    async def _compute_dispatch_utc(self, club: Club, now_utc: datetime):
        """Decide when a due club should actually be fetched.

        Returns (dispatch_dt_utc, blocked_by_rollover).

        A club's target JST day finalizes at 15:00 UTC, so a scrape scheduled in
        the minutes right after that can beat uma.moe's write for this particular
        circle. Hold those until a short grace period has passed.

        This used to estimate readiness from the club's rank against an assumed
        ~20 circles/s rollout. That guess is no longer needed: the scraper now
        reads ``circle.last_updated`` and raises StaleDataError when the target day
        genuinely hasn't finalized, so the scheduler re-queues on fact rather than
        prediction. (Measured 2026-07-25: the top 100 circles all finalized within
        ~3s of each other, so the rank model was also mis-calibrated.)
        """
        club_tz = resolve_timezone(club.timezone)
        effective = self._effective_scrape_time(club, now_utc)
        target_local = club_tz.localize(
            datetime.combine(now_utc.astimezone(club_tz).date(),
                             time(effective.hour, effective.minute))
        )
        target_utc = target_local.astimezone(timezone.utc)

        # The finalize for the day this scrape will read.
        rollover = target_utc.replace(
            hour=ROLLOVER_UTC_HOUR, minute=0, second=0, microsecond=0
        )
        if target_utc < rollover:
            # Scheduled before today's finalize — it reads the previous JST day,
            # which settled 24h ago. Nothing to wait for.
            return target_utc, False

        fresh_at = rollover + timedelta(seconds=SCRAPE_ROLLOVER_GRACE_SEC)
        if target_utc >= fresh_at:
            return target_utc, False
        return fresh_at, True

    async def _scheduled_worker(self, club: Club, attempt: int, is_final: bool) -> str:
        """Adapter the scheduler calls. Returns 'ok' | 'stale' | 'failed'."""
        return await self.daily_check_for_club(club, attempt=attempt, is_final=is_final)

    async def daily_check_for_club(self, club: Club, attempt: int = 1, is_final: bool = True) -> str:
        """Daily quota check and report generation for a specific club"""
        logger.info("=" * 80)
        logger.info(f"Starting daily check for {club.club_name}")
        logger.info("=" * 80)

        try:
            async with ScrapeContext(club.club_id, f"tasks_{club.club_name}"):
                report_channel = self.bot.get_channel(club.report_channel_id)
                alert_channel = self.bot.get_channel(club.alert_channel_id or club.report_channel_id)

                if not report_channel:
                    logger.error(f"Report channel {club.report_channel_id} not found for {club.club_name}")
                    return "failed"

                if not alert_channel:
                    logger.warning(f"Alert channel not found for {club.club_name}, using report channel")
                    alert_channel = report_channel

                club_tz = resolve_timezone(club.timezone)
                current_datetime = datetime.now(club_tz)
                current_date = current_datetime.date()

                max_retries = 3
                retry_delay = 10

                scraped_data = None
                current_day = None
                last_error = None
                stale = False

                # STEP 1: Select and initialize scraper with validation
                if USE_UMAMOE_API:
                    if not club.circle_id:
                        logger.error(f"No circle_id configured for {club.club_name} (required when Uma.moe API is enabled)")
                        error_embed = self.report_generator.create_error_report(
                            club.club_name,
                            f"⚠️ **Missing Circle ID for {club.club_name}**\n\n"
                            f"Uma.moe API is enabled but no circle_id has been set.\n\n"
                            f"**To fix this:**\n"
                            f"Use `/edit_club club:{club.club_name} circle_id:<numeric_id>`\n\n"
                            f"**How to find your Circle ID:**\n"
                            f"1. Go to https://uma.moe/circles/\n"
                            f"2. Search for **{club.club_name}**\n"
                            f"3. Copy the number from the URL"
                        )
                        await report_channel.send(embed=error_embed)
                        return "failed"

                    if not club.is_circle_id_valid():
                        logger.error(f"Invalid circle_id format for {club.club_name}: '{club.circle_id}' (must be numeric)")
                        error_embed = self.report_generator.create_error_report(
                            club.club_name,
                            club.get_circle_id_help_message()
                        )
                        await report_channel.send(embed=error_embed)
                        return "failed"

                    scraper = UmaMoeAPIScraper(club.circle_id)
                    logger.info(f"Using Uma.moe API scraper for {club.club_name} (circle_id: {club.circle_id})")
                else:
                    scraper = ChronoGenesisScraper(club.scrape_url)
                    logger.info(f"Using ChronoGenesis scraper for {club.club_name}")

                # STEP 2: Scrape with retries
                for scrape_attempt in range(1, max_retries + 1):
                    try:
                        logger.info(f"🔍 Scraping {club.club_name} (attempt {scrape_attempt}/{max_retries})...")
                        scraped_data = await scraper.scrape()
                        current_day = scraper.get_current_day()

                        if scraped_data:
                            logger.info(f"✅ Scraping successful for {club.club_name} ({len(scraped_data)} members found)")
                            break
                        else:
                            raise ValueError("Scraper returned empty data")

                    except StaleDataError as e:
                        # Data isn't a failure — it just hasn't rolled out yet. Don't
                        # burn the quick local retries (rollout takes minutes); let the
                        # scheduler re-queue this club on a longer, rank-aware delay.
                        last_error = e
                        stale = True
                        logger.warning(f"🕒 {club.club_name} data not fresh yet (scheduler attempt {attempt}): {e}")
                        break

                    except Exception as e:
                        last_error = e
                        logger.error(f"❌ Scraping failed for {club.club_name} (attempt {scrape_attempt}/{max_retries}): {e}")

                        if scrape_attempt < max_retries:
                            logger.info(f"Retrying in {retry_delay} seconds...")
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2

                # STEP 2b: Data still rolling out — hand back to the scheduler to re-queue
                if stale and not scraped_data:
                    if is_final:
                        logger.error(
                            f"{club.club_name}: data still not fresh after {SCRAPE_MAX_FRESHNESS_RETRIES} "
                            f"scheduler attempts — giving up for today."
                        )
                        error_embed = self.report_generator.create_error_report(
                            club.club_name,
                            "⏳ **Data not available yet**\n\n"
                            "Uma.moe hadn't finished rolling out today's update for this club "
                            "after several retries. This is usually temporary.\n\n"
                            f"Run `/force_check club:{club.club_name}` in a bit to retry manually."
                        )
                        await report_channel.send(embed=error_embed)
                    return "stale"

                # STEP 3: Handle scraping failure
                if not scraped_data:
                    error_msg = (
                        f"Failed to scrape data after {max_retries} attempts.\n\n"
                        f"**Last error:** {str(last_error)}\n\n"
                        f"**Most likely cause:**\n"
                        f"• Data for the target day not yet finalized on Uma.moe\n"
                        f"• Uma.moe finalizes each competition day at 15:00 UTC (00:00 JST)\n\n"
                        f"**Other possible causes:**\n"
                        f"• Uma.moe API is down or unreachable\n"
                        f"• Network timeout\n"
                        f"• Invalid circle_id\n\n"
                        f"**What to do:**\n"
                        f"• Wait a few hours and try `/force_check` again\n"
                        f"• Check uma.moe directly to verify data availability"
                    )
                    logger.error(f"Scraping failed for {club.club_name}: {error_msg}")

                    error_embed = self.report_generator.create_error_report(club.club_name, error_msg)
                    await report_channel.send(embed=error_embed)
                    await report_channel.send(
                        f"⚠️ **Manual intervention required for {club.club_name}!**\n"
                        f"Administrators can run `/force_check club:{club.club_name}` to retry manually."
                    )
                    return "failed"

                # Use the scraper's data date in case of previous-month fallback (e.g. Day 1)
                data_date = scraper.get_data_date()
                if data_date:
                    current_date = data_date
                    logger.info(f"Using scraper's data date: {current_date} (previous-month fallback)")

                # Extract and persist club rank data (Uma.moe API only)
                rank_data = None
                if isinstance(scraper, UmaMoeAPIScraper):
                    monthly_rank = scraper.get_monthly_rank()
                    last_month_rank = scraper.get_last_month_rank()
                    yesterday_rank = scraper.get_yesterday_rank()

                    if monthly_rank is not None:
                        try:
                            await ClubRankHistory.save(club.club_id, current_date, monthly_rank, monthly_rank)
                        except Exception as e:
                            logger.error(f"Failed to save rank data for {club.club_name}: {e}", exc_info=True)

                        rank_data = {
                            'monthly_rank': monthly_rank,
                            'last_month_rank': last_month_rank,
                            'yesterday_rank': yesterday_rank,
                        }
                        logger.info(
                            f"Rank data for {club.club_name}: "
                            f"monthly={monthly_rank}, yesterday={yesterday_rank}, "
                            f"last_month={last_month_rank}"
                        )

                        # Best-effort: how far to the next rank milestone. Never let
                        # this optional enrichment break the daily report.
                        try:
                            from services.promotion_calculator import compute_promotion
                            promo = await compute_promotion(club)
                            if promo and not promo.already_reached and promo.fans_needed:
                                rank_data['promotion'] = {
                                    'target_rank': promo.target_rank,
                                    'fans_needed': promo.fans_needed,
                                    'extra_per_day': promo.extra_per_day,
                                    'days_remaining': promo.days_remaining,
                                }
                        except Exception as e:
                            logger.warning(f"Promotion calc failed for {club.club_name}: {e}")

                # STEP 4: Process the scraped data
                try:
                    logger.info(f"⚙️ Processing scraped data for {club.club_name}...")
                    new_members, updated_members = await self.quota_calculator.process_scraped_data(
                        club.club_id, scraped_data, current_date, current_day,
                        quota_period=club.quota_period
                    )
                    logger.info(f"✅ Data processed for {club.club_name}: {updated_members} members updated, {new_members} new members")

                except Exception as e:
                    logger.error(f"❌ Error processing scraped data for {club.club_name}: {e}", exc_info=True)
                    error_embed = self.report_generator.create_error_report(
                        club.club_name,
                        f"Data processing failed: {str(e)}"
                    )
                    await report_channel.send(embed=error_embed)
                    return "failed"

                # STEP 5: Bomb management
                newly_activated_bombs = []
                deactivated_bombs = []
                members_to_kick = []

                if club.bombs_enabled:
                    try:
                        logger.info(f"💣 Checking for bomb activations in {club.club_name}...")
                        newly_activated_bombs = await self.bomb_manager.check_and_activate_bombs(club, current_date)

                        logger.info(f"⏳ Updating bomb countdowns for {club.club_name}...")
                        await self.bomb_manager.update_bomb_countdowns(club.club_id, current_date)

                        logger.info(f"✅ Checking for bomb deactivations in {club.club_name}...")
                        deactivated_bombs = await self.bomb_manager.check_and_deactivate_bombs(club.club_id, current_date)

                        logger.info(f"🚨 Checking for expired bombs in {club.club_name}...")
                        members_to_kick = await self.bomb_manager.check_expired_bombs(club.club_id)

                        logger.info(
                            f"Bomb management complete for {club.club_name}: "
                            f"{len(newly_activated_bombs)} activated, "
                            f"{len(deactivated_bombs)} deactivated, "
                            f"{len(members_to_kick)} to kick"
                        )

                    except Exception as e:
                        logger.error(f"❌ Error during bomb management for {club.club_name}: {e}", exc_info=True)
                        newly_activated_bombs = []
                        deactivated_bombs = []
                        members_to_kick = []
                else:
                    logger.info(f"⏭️ Skipping bomb management for {club.club_name} (bombs disabled)")

                # STEP 6: Send DM notifications to linked users
                try:
                    if newly_activated_bombs:
                        logger.info(f"📨 Sending bomb activation DMs for {club.club_name}...")
                        await self.notification_service.send_bomb_notifications(club.club_name, newly_activated_bombs)

                    if deactivated_bombs:
                        logger.info(f"📨 Sending bomb deactivation DMs for {club.club_name}...")
                        for item in deactivated_bombs:
                            member = item['member']
                            await self.notification_service.send_bomb_deactivation_notification(club.club_name, member)

                    # Send deficit notifications
                    status_summary = await self.quota_calculator.get_member_status_summary(
                        club.club_id, current_date, quota_period=club.quota_period
                    )
                    if status_summary['behind']:
                        logger.info(f"📨 Sending deficit notifications for {club.club_name}...")
                        await self.notification_service.send_deficit_notifications(club.club_name, status_summary['behind'])

                except Exception as e:
                    logger.error(f"❌ Error sending DM notifications for {club.club_name}: {e}", exc_info=True)

                # STEP 7: Generate and send reports
                try:
                    logger.info(f"📊 Generating daily report for {club.club_name}...")
                    status_summary = await self.quota_calculator.get_member_status_summary(
                        club.club_id, current_date, quota_period=club.quota_period
                    )

                    if club.bombs_enabled:
                        bombs_data = await self.bomb_manager.get_active_bombs_with_members(club.club_id)
                    else:
                        bombs_data = []

                    effective_quota = await QuotaRequirement.get_quota_for_date(club.club_id, current_date)

                    if club.image_report_enabled:
                        monthly_rank = rank_data.get("monthly_rank") if rank_data else None
                        img_path = None
                        try:
                            img_path = await generate_tally_image(
                                club.club_id, club.club_name, current_date,
                                daily_quota=effective_quota, monthly_rank=monthly_rank,
                            )
                            await report_channel.send(file=discord.File(str(img_path), filename="quota_report.png"))
                            logger.info(f"✅ Tally image report sent for {club.club_name}")
                        except Exception as img_err:
                            logger.error(f"❌ Tally image failed for {club.club_name}, falling back to embeds: {img_err}", exc_info=True)
                            daily_reports = self.report_generator.create_daily_report(
                                club.club_name, effective_quota, status_summary, bombs_data, current_date,
                                rank_data=rank_data, quota_period=club.quota_period,
                                club_timezone=club.timezone,
                            )
                            for embed in daily_reports:
                                await report_channel.send(embed=embed)
                        finally:
                            if img_path and img_path.exists():
                                os.unlink(img_path)
                    else:
                        daily_reports = self.report_generator.create_daily_report(
                            club.club_name, effective_quota, status_summary, bombs_data, current_date,
                            rank_data=rank_data, quota_period=club.quota_period,
                            club_timezone=club.timezone,
                        )
                        for embed in daily_reports:
                            await report_channel.send(embed=embed)
                        logger.info(f"✅ Daily report sent for {club.club_name} ({len(daily_reports)} embed(s))")

                    if deactivated_bombs:
                        deactivation_embeds = self.report_generator.create_bomb_deactivation_report(
                            club.club_name, deactivated_bombs
                        )
                        for embed in deactivation_embeds:
                            await report_channel.send(embed=embed)
                        logger.info(f"✅ Bomb deactivation report sent for {club.club_name} ({len(deactivated_bombs)} member(s))")

                except Exception as e:
                    logger.error(f"❌ Error generating/sending daily report for {club.club_name}: {e}", exc_info=True)
                    error_embed = self.report_generator.create_error_report(
                        club.club_name,
                        f"Failed to generate daily report: {str(e)}"
                    )
                    await report_channel.send(embed=error_embed)

                # STEP 8: Send alerts to alert channel
                try:
                    if newly_activated_bombs:
                        bomb_data = []
                        for bomb in newly_activated_bombs:
                            member = await Member.get_by_id(bomb.member_id)
                            bomb_data.append({'bomb': bomb, 'member': member})

                        for embed in self.report_generator.create_bomb_activation_alert(club.club_name, bomb_data):
                            await alert_channel.send(embed=embed)
                        logger.info(f"💣 Sent bomb activation alert for {club.club_name} ({len(bomb_data)} member(s))")

                    if members_to_kick:
                        for embed in self.report_generator.create_kick_alert(club.club_name, members_to_kick):
                            await alert_channel.send(embed=embed)
                        logger.info(f"🚨 Sent kick alert for {club.club_name} ({len(members_to_kick)} member(s))")

                except Exception as e:
                    logger.error(f"❌ Error sending alerts for {club.club_name}: {e}", exc_info=True)

                # STEP 9: Final summary
                logger.info("=" * 80)
                logger.info(f"✅ Daily check complete for {club.club_name}!")
                logger.info(f"   • Members updated: {updated_members}")
                logger.info(f"   • New members: {new_members}")
                logger.info(f"   • Bombs activated: {len(newly_activated_bombs)}")
                logger.info(f"   • Bombs deactivated: {len(deactivated_bombs)}")
                logger.info(f"   • Members to kick: {len(members_to_kick)}")
                logger.info("=" * 80)

                return "ok"

        except Exception as e:
            logger.error(f"Fatal error in daily check for {club.club_name}: {e}", exc_info=True)

            try:
                report_channel = self.bot.get_channel(club.report_channel_id)
                if report_channel:
                    error_embed = self.report_generator.create_error_report(
                        club.club_name,
                        f"Fatal error during daily check: {str(e)}"
                    )
                    await report_channel.send(embed=error_embed)
            except Exception:
                pass

            return "failed"

    @scrape_tick.before_loop
    async def before_scrape_tick(self):
        """Wait for bot to be ready before starting tasks"""
        await self.bot.wait_until_ready()
        logger.info("Bot ready, per-minute scrape tick starting")