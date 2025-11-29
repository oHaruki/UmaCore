"""
Scheduled tasks for the Discord bot
"""
import discord
from discord.ext import tasks
from datetime import datetime, time
import logging
import pytz

from config.settings import CHANNEL_ID, TIMEZONE, DAILY_REPORT_TIME, SCRAPE_URL
from scrapers import ChronoGenesisScraper
from services import QuotaCalculator, BombManager, ReportGenerator
from models import Member

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
        self.report_time = time(hour=hour, minute=minute, tzinfo=self.timezone)
    
    def start_tasks(self):
        """Start all scheduled tasks"""
        self.daily_check.start()
        logger.info("Scheduled tasks started")
    
    def stop_tasks(self):
        """Stop all scheduled tasks"""
        self.daily_check.cancel()
        logger.info("Scheduled tasks stopped")
    
    @tasks.loop(time=time(hour=16, minute=0))  # Will be overridden by actual timezone time
    async def daily_check(self):
        """Daily quota check and report generation"""
        logger.info("Starting daily check...")
        
        try:
            # Get the channel
            channel = self.bot.get_channel(CHANNEL_ID)
            if not channel:
                logger.error(f"Channel {CHANNEL_ID} not found")
                return
            
            # Get current date in the configured timezone
            current_datetime = datetime.now(self.timezone)
            current_date = current_datetime.date()
            
            # Scrape the website
            logger.info("Scraping website...")
            scraped_data = await self.scraper.scrape()
            current_day = self.scraper.get_current_day()
            
            if not scraped_data:
                error_embed = self.report_generator.create_error_report(
                    "Failed to scrape data from website"
                )
                await channel.send(embed=error_embed)
                return
            
            # Process scraped data
            logger.info("Processing scraped data...")
            new_members, updated_members = await self.quota_calculator.process_scraped_data(
                scraped_data, current_date, current_day
            )
            
            # Check and activate bombs
            logger.info("Checking for bomb activations...")
            newly_activated_bombs = await self.bomb_manager.check_and_activate_bombs(current_date)
            
            # Update bomb countdowns
            logger.info("Updating bomb countdowns...")
            await self.bomb_manager.update_bomb_countdowns()
            
            # Check and deactivate bombs (members back on track)
            logger.info("Checking for bomb deactivations...")
            deactivated_bombs = await self.bomb_manager.check_and_deactivate_bombs(current_date)
            
            # Check for expired bombs (members to kick)
            logger.info("Checking for expired bombs...")
            members_to_kick = await self.bomb_manager.check_expired_bombs()
            
            # Get status summary
            status_summary = await self.quota_calculator.get_member_status_summary(current_date)
            bombs_data = await self.bomb_manager.get_active_bombs_with_members()
            
            # Generate and send daily report
            logger.info("Generating daily report...")
            daily_report = self.report_generator.create_daily_report(
                status_summary, bombs_data, current_date
            )
            await channel.send(embed=daily_report)
            
            # Send bomb activation alerts if any
            if newly_activated_bombs:
                bomb_data = []
                for bomb in newly_activated_bombs:
                    member = await Member.get_by_id(bomb.member_id)
                    bomb_data.append({'bomb': bomb, 'member': member})
                
                alert_embed = self.report_generator.create_bomb_activation_alert(bomb_data)
                await channel.send(embed=alert_embed)
            
            # Send kick alerts if any
            if members_to_kick:
                kick_embed = self.report_generator.create_kick_alert(members_to_kick)
                await channel.send(embed=kick_embed)
            
            # Log summary
            logger.info(f"Daily check complete: {updated_members} members updated, "
                       f"{new_members} new members, {len(newly_activated_bombs)} bombs activated, "
                       f"{len(deactivated_bombs)} bombs deactivated, {len(members_to_kick)} members to kick")
            
        except Exception as e:
            logger.error(f"Error during daily check: {e}", exc_info=True)
            
            try:
                channel = self.bot.get_channel(CHANNEL_ID)
                if channel:
                    error_embed = self.report_generator.create_error_report(
                        f"An error occurred during the daily check: {str(e)}"
                    )
                    await channel.send(embed=error_embed)
            except Exception as send_error:
                logger.error(f"Failed to send error message: {send_error}")
    
    @daily_check.before_loop
    async def before_daily_check(self):
        """Wait for bot to be ready before starting tasks"""
        await self.bot.wait_until_ready()
        logger.info("Bot ready, daily check task will run at configured time")
