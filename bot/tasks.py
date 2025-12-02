"""
Scheduled tasks for the Discord bot
"""
import discord
from discord.ext import tasks
from datetime import datetime, time, date as date_class
import logging
import pytz
import asyncio

from config.settings import CHANNEL_ID, TIMEZONE, DAILY_REPORT_TIME, SCRAPE_URL
from scrapers import ChronoGenesisScraper
from services import QuotaCalculator, BombManager, ReportGenerator
from models import Member, BotSettings

logger = logging.getLogger(__name__)


class BotTasks:
    """Manages scheduled tasks for the bot"""
    
    def __init__(self, bot):
        self.bot = bot
        self.scraper = ChronoGenesisScraper(SCRAPE_URL)
        self.quota_calculator = QuotaCalculator()
        self.bomb_manager = BombManager()
        self.report_generator = ReportGenerator()
        self.timezone = pytz.timezone(TIMEZONE)
        
        # Parse daily report time (e.g., "16:00")
        hour, minute = map(int, DAILY_REPORT_TIME.split(':'))
        self.target_hour = hour
        self.target_minute = minute
        
        # Track last run date to prevent duplicate runs
        self.last_run_date = None
        
        logger.info(f"Tasks configured to run daily at {DAILY_REPORT_TIME} {TIMEZONE}")
    
    def start_tasks(self):
        """Start all scheduled tasks"""
        self.hourly_check.start()
        logger.info("Scheduled tasks started (checking hourly for target time)")
    
    def stop_tasks(self):
        """Stop all scheduled tasks"""
        self.hourly_check.cancel()
        logger.info("Scheduled tasks stopped")
    
    @tasks.loop(hours=1)
    async def hourly_check(self):
        """
        Check every hour if it's time to run the daily report
        This fixes the timezone bug by checking the configured timezone
        """
        # Get current time in configured timezone
        now = datetime.now(self.timezone)
        current_date = now.date()
        
        # Check if we're at or past the target time
        if now.hour == self.target_hour and now.minute >= self.target_minute:
            # Check if we already ran today
            if self.last_run_date == current_date:
                logger.debug(f"Daily check already completed today ({current_date})")
                return
            
            # Run the daily check
            logger.info(f"⏰ Target time reached ({now.strftime('%H:%M')} {TIMEZONE}), starting daily check...")
            self.last_run_date = current_date
            await self.daily_check()
    
    async def daily_check(self):
        """Daily quota check and report generation with error recovery"""
        logger.info("=" * 60)
        logger.info("Starting daily check...")
        logger.info("=" * 60)
        
        # Get channels (report and alert)
        report_channel_id = await BotSettings.get_report_channel_id()
        alert_channel_id = await BotSettings.get_alert_channel_id()
        
        # Fallback to CHANNEL_ID from .env if not set in database
        if not report_channel_id:
            report_channel_id = CHANNEL_ID
        
        # If alert channel not set, use report channel
        if not alert_channel_id:
            alert_channel_id = report_channel_id
        
        report_channel = self.bot.get_channel(report_channel_id)
        alert_channel = self.bot.get_channel(alert_channel_id)
        
        if not report_channel:
            logger.error(f"Report channel {report_channel_id} not found")
            return
        
        if not alert_channel:
            logger.warning(f"Alert channel {alert_channel_id} not found, using report channel")
            alert_channel = report_channel
        
        # Get current date in the configured timezone
        current_datetime = datetime.now(self.timezone)
        current_date = current_datetime.date()
        
        # Retry configuration
        max_retries = 3
        retry_delay = 10  # seconds
        
        scraped_data = None
        current_day = None
        last_error = None
        
        # STEP 1: Try to scrape with retries
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"🔍 Scraping attempt {attempt}/{max_retries}...")
                scraped_data = await self.scraper.scrape()
                current_day = self.scraper.get_current_day()
                
                if scraped_data:
                    logger.info(f"✅ Scraping successful on attempt {attempt} ({len(scraped_data)} members found)")
                    break
                else:
                    raise ValueError("Scraper returned empty data")
                    
            except Exception as e:
                last_error = e
                logger.error(f"❌ Scraping failed (attempt {attempt}/{max_retries}): {e}")
                
                if attempt < max_retries:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
        
        # STEP 2: Handle scraping failure
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
            logger.error(error_msg)
            
            error_embed = self.report_generator.create_error_report(error_msg)
            await report_channel.send(embed=error_embed)
            await report_channel.send(
                "⚠️ **Manual intervention required!**\n"
                "Administrators can run `/force_check` to retry manually."
            )
            return  # Exit early, don't process anything
        
        # STEP 3: Process the scraped data
        try:
            logger.info("⚙️ Processing scraped data...")
            new_members, updated_members = await self.quota_calculator.process_scraped_data(
                scraped_data, current_date, current_day
            )
            
            logger.info(f"✅ Data processed: {updated_members} members updated, {new_members} new members")
            
        except Exception as e:
            logger.error(f"❌ Error processing scraped data: {e}", exc_info=True)
            error_embed = self.report_generator.create_error_report(
                f"Data processing failed: {str(e)}"
            )
            await report_channel.send(embed=error_embed)
            return  # Exit if processing fails
        
        # STEP 4: Bomb management
        try:
            logger.info("💣 Checking for bomb activations...")
            newly_activated_bombs = await self.bomb_manager.check_and_activate_bombs(current_date)
            
            logger.info("⏳ Updating bomb countdowns...")
            await self.bomb_manager.update_bomb_countdowns(current_date)
            
            logger.info("✅ Checking for bomb deactivations...")
            deactivated_bombs = await self.bomb_manager.check_and_deactivate_bombs(current_date)
            
            logger.info("🚨 Checking for expired bombs...")
            members_to_kick = await self.bomb_manager.check_expired_bombs()
            
            logger.info(f"Bomb management complete: {len(newly_activated_bombs)} activated, "
                       f"{len(deactivated_bombs)} deactivated, {len(members_to_kick)} to kick")
            
        except Exception as e:
            logger.error(f"❌ Error during bomb management: {e}", exc_info=True)
            # Continue anyway - we can still send reports
            newly_activated_bombs = []
            deactivated_bombs = []
            members_to_kick = []
        
        # STEP 5: Generate and send reports
        try:
            logger.info("📊 Generating daily report...")
            status_summary = await self.quota_calculator.get_member_status_summary(current_date)
            bombs_data = await self.bomb_manager.get_active_bombs_with_members()
            
            daily_reports = self.report_generator.create_daily_report(
                status_summary, bombs_data, current_date
            )
            
            # Send all report embeds to report channel
            for embed in daily_reports:
                await report_channel.send(embed=embed)
            
            logger.info(f"✅ Daily report sent ({len(daily_reports)} embed(s))")
            
        except Exception as e:
            logger.error(f"❌ Error generating/sending daily report: {e}", exc_info=True)
            error_embed = self.report_generator.create_error_report(
                f"Failed to generate daily report: {str(e)}"
            )
            await report_channel.send(embed=error_embed)
        
        # STEP 6: Send alerts to alert channel
        try:
            # Send bomb activation alerts if any
            if newly_activated_bombs:
                bomb_data = []
                for bomb in newly_activated_bombs:
                    member = await Member.get_by_id(bomb.member_id)
                    bomb_data.append({'bomb': bomb, 'member': member})
                
                alert_embed = self.report_generator.create_bomb_activation_alert(bomb_data)
                await alert_channel.send(embed=alert_embed)
                logger.info(f"💣 Sent bomb activation alert for {len(bomb_data)} member(s)")
            
            # Send kick alerts if any
            if members_to_kick:
                kick_embed = self.report_generator.create_kick_alert(members_to_kick)
                await alert_channel.send(embed=kick_embed)
                logger.info(f"🚨 Sent kick alert for {len(members_to_kick)} member(s)")
            
        except Exception as e:
            logger.error(f"❌ Error sending alerts: {e}", exc_info=True)
        
        # STEP 7: Final summary
        logger.info("=" * 60)
        logger.info(f"✅ Daily check complete!")
        logger.info(f"   • Members updated: {updated_members}")
        logger.info(f"   • New members: {new_members}")
        logger.info(f"   • Bombs activated: {len(newly_activated_bombs)}")
        logger.info(f"   • Bombs deactivated: {len(deactivated_bombs)}")
        logger.info(f"   • Members to kick: {len(members_to_kick)}")
        logger.info("=" * 60)
    
    @hourly_check.before_loop
    async def before_hourly_check(self):
        """Wait for bot to be ready before starting tasks"""
        await self.bot.wait_until_ready()
        logger.info(f"Bot ready, will check hourly for {DAILY_REPORT_TIME} {TIMEZONE}")