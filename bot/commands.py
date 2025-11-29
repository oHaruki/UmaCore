"""
Discord bot commands
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, date
import logging
import pytz

from config.settings import TIMEZONE, SCRAPE_URL
from scrapers import ChronoGenesisScraper
from services import QuotaCalculator, BombManager, ReportGenerator
from models import Member, QuotaHistory, Bomb

logger = logging.getLogger(__name__)


class QuotaCommands(commands.Cog):
    """Admin commands for the quota tracker"""
    
    def __init__(self, bot):
        self.bot = bot
        self.scraper = ChronoGenesisScraper(SCRAPE_URL)
        self.quota_calculator = QuotaCalculator()
        self.bomb_manager = BombManager()
        self.report_generator = ReportGenerator()
        self.timezone = pytz.timezone(TIMEZONE)
    
    @app_commands.command(name="force_check", description="Manually trigger a quota check and report")
    @app_commands.checks.has_permissions(administrator=True)
    async def force_check(self, interaction: discord.Interaction):
        """Manually trigger the daily check"""
        await interaction.response.defer()
        
        try:
            # Get current date
            current_datetime = datetime.now(self.timezone)
            current_date = current_datetime.date()
            
            # Scrape
            await interaction.followup.send("🔄 Scraping website...")
            scraped_data = await self.scraper.scrape()
            current_day = self.scraper.get_current_day()
            
            if not scraped_data:
                await interaction.followup.send("❌ Failed to scrape data")
                return
            
            # Process
            await interaction.followup.send("⚙️ Processing data...")
            new_members, updated_members = await self.quota_calculator.process_scraped_data(
                scraped_data, current_date, current_day
            )
            
            # Bombs
            newly_activated = await self.bomb_manager.check_and_activate_bombs(current_date)
            await self.bomb_manager.update_bomb_countdowns()
            deactivated = await self.bomb_manager.check_and_deactivate_bombs(current_date)
            members_to_kick = await self.bomb_manager.check_expired_bombs()
            
            # Report
            status_summary = await self.quota_calculator.get_member_status_summary(current_date)
            bombs_data = await self.bomb_manager.get_active_bombs_with_members()
            
            daily_reports = self.report_generator.create_daily_report(
                status_summary, bombs_data, current_date
            )
            
            # Send all report embeds
            for embed in daily_reports:
                await interaction.followup.send(embed=embed)
            
            # Alerts
            if newly_activated:
                bomb_data = []
                for bomb in newly_activated:
                    member = await Member.get_by_id(bomb.member_id)
                    bomb_data.append({'bomb': bomb, 'member': member})
                alert = self.report_generator.create_bomb_activation_alert(bomb_data)
                await interaction.followup.send(embed=alert)
            
            if members_to_kick:
                kick_alert = self.report_generator.create_kick_alert(members_to_kick)
                await interaction.followup.send(embed=kick_alert)
            
            await interaction.followup.send(
                f"✅ Check complete: {updated_members} members updated, {new_members} new members"
            )
            
        except Exception as e:
            logger.error(f"Error in force_check: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="member_status", description="View status of a specific member")
    async def member_status(self, interaction: discord.Interaction, trainer_name: str):
        """Get detailed status for a specific member"""
        await interaction.response.defer()
        
        try:
            member = await Member.get_by_name(trainer_name)
            
            if not member:
                await interaction.followup.send(f"❌ Member '{trainer_name}' not found")
                return
            
            # Get latest history
            latest_history = await QuotaHistory.get_latest_for_member(member.member_id)
            
            if not latest_history:
                await interaction.followup.send(f"No quota data found for {trainer_name}")
                return
            
            # Check for active bomb
            active_bomb = await Bomb.get_active_for_member(member.member_id)
            
            # Create embed
            embed = discord.Embed(
                title=f"Member Status: {trainer_name}",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="📅 Member Info",
                value=f"**Joined:** {member.join_date.strftime('%Y-%m-%d')}\n"
                      f"**Trainer ID:** {member.trainer_id or 'N/A'}\n"
                      f"**Status:** {'Active' if member.is_active else 'Inactive'}\n"
                      f"**Last Seen:** {member.last_seen.strftime('%Y-%m-%d')}",
                inline=False
            )
            
            status_emoji = "✅" if latest_history.deficit_surplus >= 0 else "⚠️"
            deficit_text = f"+{latest_history.deficit_surplus:,}" if latest_history.deficit_surplus >= 0 else f"{latest_history.deficit_surplus:,}"
            
            embed.add_field(
                name=f"{status_emoji} Quota Status",
                value=f"**Current Fans:** {latest_history.cumulative_fans:,}\n"
                      f"**Expected Fans:** {latest_history.expected_fans:,}\n"
                      f"**Deficit/Surplus:** {deficit_text}\n"
                      f"**Days Behind:** {latest_history.days_behind}",
                inline=False
            )
            
            if active_bomb:
                embed.add_field(
                    name="💣 Active Bomb",
                    value=f"**Activated:** {active_bomb.activation_date.strftime('%Y-%m-%d')}\n"
                          f"**Days Remaining:** {active_bomb.days_remaining}",
                    inline=False
                )
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in member_status: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="bomb_status", description="View all active bombs")
    @app_commands.checks.has_permissions(administrator=True)
    async def bomb_status(self, interaction: discord.Interaction):
        """View all active bombs"""
        await interaction.response.defer()
        
        try:
            bombs_data = await self.bomb_manager.get_active_bombs_with_members()
            
            if not bombs_data:
                await interaction.followup.send("✅ No active bombs!")
                return
            
            embed = discord.Embed(
                title="💣 Active Bombs",
                description=f"Total: {len(bombs_data)}",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            
            for item in bombs_data[:25]:  # Discord limit
                member = item['member']
                bomb = item['bomb']
                history = item['history']
                
                deficit = abs(history.deficit_surplus)
                
                embed.add_field(
                    name=f"{member.trainer_name}",
                    value=f"**Days Remaining:** {bomb.days_remaining}\n"
                          f"**Behind by:** {deficit:,} fans\n"
                          f"**Activated:** {bomb.activation_date.strftime('%Y-%m-%d')}",
                    inline=True
                )
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in bomb_status: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="add_member", description="Manually add a new member")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_member(self, interaction: discord.Interaction, 
                         trainer_name: str, join_date: str, trainer_id: str = None):
        """Manually add a member (format: YYYY-MM-DD)"""
        await interaction.response.defer()
        
        try:
            # Parse date
            join_date_obj = datetime.strptime(join_date, "%Y-%m-%d").date()
            
            # Check if exists
            if trainer_id:
                existing = await Member.get_by_trainer_id(trainer_id)
            else:
                existing = await Member.get_by_name(trainer_name)
                
            if existing:
                await interaction.followup.send(f"❌ Member '{trainer_name}' already exists")
                return
            
            # Create member
            member = await Member.create(trainer_name, join_date_obj, trainer_id)
            
            await interaction.followup.send(
                f"✅ Added member: {trainer_name} (joined {join_date}, ID: {trainer_id or 'N/A'})"
            )
            
        except ValueError:
            await interaction.followup.send("❌ Invalid date format. Use YYYY-MM-DD")
        except Exception as e:
            logger.error(f"Error in add_member: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="deactivate_member", description="Deactivate a member")
    @app_commands.checks.has_permissions(administrator=True)
    async def deactivate_member(self, interaction: discord.Interaction, trainer_name: str):
        """Deactivate a member"""
        await interaction.response.defer()
        
        try:
            member = await Member.get_by_name(trainer_name)
            
            if not member:
                await interaction.followup.send(f"❌ Member '{trainer_name}' not found")
                return
            
            await member.deactivate()
            await interaction.followup.send(f"✅ Deactivated: {trainer_name}")
            
        except Exception as e:
            logger.error(f"Error in deactivate_member: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")


async def setup(bot):
    """Setup function for loading the cog"""
    await bot.add_cog(QuotaCommands(bot))