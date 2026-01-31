"""
Scheduled tasks for the Discord bot
"""
import discord
from discord.ext import tasks
from datetime import datetime
import logging
import pytz
import asyncio

from models import Club, Member
from scrapers import ChronoGenesisScraper, UmaMoeAPIScraper
from services import QuotaCalculator, BombManager, ReportGenerator, NotificationService, ScrapeLockManager, ScrapeContext
from config.settings import USE_UMAMOE_API

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
        
        logger.info("Multi-club tasks configured - will check all clubs hourly")
    
    def start_tasks(self):
        """Start all scheduled tasks"""
        self.hourly_check.start()
        logger.info("Scheduled tasks started (checking all clubs hourly)")
    
    def stop_tasks(self):
        """Stop all scheduled tasks"""
        self.hourly_check.cancel()
        logger.info("Scheduled tasks stopped")
    
    @tasks.loop(hours=1)
    async def hourly_check(self):
        """Check every hour if it's time to run any club's daily report"""
        logger.info("=" * 80)
        logger.info("Hourly check - scanning all clubs...")
        logger.info("=" * 80)
        
        try:
            clubs = await Club.get_all_active()
            logger.info(f"Found {len(clubs)} active club(s)")
            
            for club in clubs:
                try:
                    club_tz = pytz.timezone(club.timezone)
                    now_in_club_tz = datetime.now(club_tz)
                    current_date = now_in_club_tz.date()
                    
                    target_hour = club.scrape_time.hour
                    target_minute = club.scrape_time.minute
                    
                    if (now_in_club_tz.hour == target_hour and 
                        now_in_club_tz.minute >= target_minute):
                        
                        run_key = f"{club.club_id}_{current_date}"
                        if self.last_runs.get(run_key):
                            logger.debug(f"{club.club_name}: Already ran today ({current_date})")
                            continue
                        
                        logger.info(f"⏰ Time to check {club.club_name} ({now_in_club_tz.strftime('%H:%M')} {club.timezone})")
                        
                        self.last_runs[run_key] = True
                        
                        asyncio.create_task(self.daily_check_for_club(club))
                    else:
                        logger.debug(f"{club.club_name}: Not time yet (current: {now_in_club_tz.strftime('%H:%M')}, target: {target_hour:02d}:{target_minute:02d} {club.timezone})")
                
                except Exception as e:
                    logger.error(f"Error checking club {club.club_name}: {e}", exc_info=True)
                    continue
        
        except Exception as e:
            logger.error(f"Error in hourly_check: {e}", exc_info=True)
    
    async def daily_check_for_club(self, club: Club):
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
                    return
                
                if not alert_channel:
                    logger.warning(f"Alert channel not found for {club.club_name}, using report channel")
                    alert_channel = report_channel
                
                club_tz = pytz.timezone(club.timezone)
                current_datetime = datetime.now(club_tz)
                current_date = current_datetime.date()
                
                max_retries = 3
                retry_delay = 10
                
                scraped_data = None
                current_day = None
                last_error = None
                
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
                        return
                    
                    if not club.is_circle_id_valid():
                        logger.error(f"Invalid circle_id format for {club.club_name}: '{club.circle_id}' (must be numeric)")
                        error_embed = self.report_generator.create_error_report(
                            club.club_name,
                            club.get_circle_id_help_message()
                        )
                        await report_channel.send(embed=error_embed)
                        return
                    
                    scraper = UmaMoeAPIScraper(club.circle_id)
                    logger.info(f"Using Uma.moe API scraper for {club.club_name} (circle_id: {club.circle_id})")
                else:
                    scraper = ChronoGenesisScraper(club.scrape_url)
                    logger.info(f"Using ChronoGenesis scraper for {club.club_name}")
                
                # STEP 2: Try to scrape with retries
                for attempt in range(1, max_retries + 1):
                    try:
                        logger.info(f"🔍 Scraping {club.club_name} (attempt {attempt}/{max_retries})...")
                        scraped_data = await scraper.scrape()
                        current_day = scraper.get_current_day()
                        
                        if scraped_data:
                            logger.info(f"✅ Scraping successful for {club.club_name} ({len(scraped_data)} members found)")
                            break
                        else:
                            raise ValueError("Scraper returned empty data")
                            
                    except Exception as e:
                        last_error = e
                        logger.error(f"❌ Scraping failed for {club.club_name} (attempt {attempt}/{max_retries}): {e}")
                        
                        if attempt < max_retries:
                            logger.info(f"Retrying in {retry_delay} seconds...")
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2
                
                # STEP 3: Handle scraping failure
                if not scraped_data:
                    error_msg = (
                        f"Failed to scrape data after {max_retries} attempts.\n\n"
                        f"**Last error:** {str(last_error)}\n\n"
                        f"**Possible causes:**\n"
                        f"• Website is down or blocked\n"
                        f"• Cookie consent popup changed\n"
                        f"• Network timeout\n"
                        f"• Website structure changed"
                    )
                    logger.error(f"Scraping failed for {club.club_name}: {error_msg}")
                    
                    error_embed = self.report_generator.create_error_report(club.club_name, error_msg)
                    await report_channel.send(embed=error_embed)
                    await report_channel.send(
                        f"⚠️ **Manual intervention required for {club.club_name}!**\n"
                        f"Administrators can run `/force_check club:{club.club_name}` to retry manually."
                    )
                    return
                
                # STEP 4: Process the scraped data
                try:
                    logger.info(f"⚙️ Processing scraped data for {club.club_name}...")
                    new_members, updated_members = await self.quota_calculator.process_scraped_data(
                        club.club_id, scraped_data, current_date, current_day
                    )
                    
                    logger.info(f"✅ Data processed for {club.club_name}: {updated_members} members updated, {new_members} new members")
                    
                except Exception as e:
                    logger.error(f"❌ Error processing scraped data for {club.club_name}: {e}", exc_info=True)
                    error_embed = self.report_generator.create_error_report(
                        club.club_name,
                        f"Data processing failed: {str(e)}"
                    )
                    await report_channel.send(embed=error_embed)
                    return
                
                # STEP 5: Bomb management
                try:
                    logger.info(f"💣 Checking for bomb activations in {club.club_name}...")
                    newly_activated_bombs = await self.bomb_manager.check_and_activate_bombs(club, current_date)
                    
                    logger.info(f"⏳ Updating bomb countdowns for {club.club_name}...")
                    await self.bomb_manager.update_bomb_countdowns(club.club_id, current_date)
                    
                    logger.info(f"✅ Checking for bomb deactivations in {club.club_name}...")
                    deactivated_bombs = await self.bomb_manager.check_and_deactivate_bombs(club.club_id, current_date)
                    
                    logger.info(f"🚨 Checking for expired bombs in {club.club_name}...")
                    members_to_kick = await self.bomb_manager.check_expired_bombs(club.club_id)
                    
                    logger.info(f"Bomb management complete for {club.club_name}: {len(newly_activated_bombs)} activated, "
                               f"{len(deactivated_bombs)} deactivated, {len(members_to_kick)} to kick")
                    
                except Exception as e:
                    logger.error(f"❌ Error during bomb management for {club.club_name}: {e}", exc_info=True)
                    newly_activated_bombs = []
                    deactivated_bombs = []
                    members_to_kick = []
                
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
                    status_summary = await self.quota_calculator.get_member_status_summary(club.club_id, current_date)
                    if status_summary['behind']:
                        logger.info(f"📨 Sending deficit notifications for {club.club_name}...")
                        await self.notification_service.send_deficit_notifications(club.club_name, status_summary['behind'])
                    
                except Exception as e:
                    logger.error(f"❌ Error sending DM notifications for {club.club_name}: {e}", exc_info=True)
                
                # STEP 7: Generate and send reports
                try:
                    logger.info(f"📊 Generating daily report for {club.club_name}...")
                    status_summary = await self.quota_calculator.get_member_status_summary(club.club_id, current_date)
                    bombs_data = await self.bomb_manager.get_active_bombs_with_members(club.club_id)
                    
                    daily_reports = self.report_generator.create_daily_report(
                        club.club_name, club.daily_quota, status_summary, bombs_data, current_date
                    )
                    
                    for embed in daily_reports:
                        await report_channel.send(embed=embed)
                    
                    logger.info(f"✅ Daily report sent for {club.club_name} ({len(daily_reports)} embed(s))")
                    
                    if deactivated_bombs:
                        deactivation_embed = self.report_generator.create_bomb_deactivation_report(club.club_name, deactivated_bombs)
                        await report_channel.send(embed=deactivation_embed)
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
                        
                        alert_embed = self.report_generator.create_bomb_activation_alert(club.club_name, bomb_data)
                        await alert_channel.send(embed=alert_embed)
                        logger.info(f"💣 Sent bomb activation alert for {club.club_name} ({len(bomb_data)} member(s))")
                    
                    if members_to_kick:
                        kick_embed = self.report_generator.create_kick_alert(club.club_name, members_to_kick)
                        await alert_channel.send(embed=kick_embed)
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
            except:
                pass
    
    @hourly_check.before_loop
    async def before_hourly_check(self):
        """Wait for bot to be ready before starting tasks"""
        await self.bot.wait_until_ready()
        logger.info("Bot ready, multi-club hourly check loop starting")
