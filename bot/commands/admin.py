"""
Administrative commands for quota management
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import logging
import pytz
import asyncio

from config.settings import TIMEZONE, SCRAPE_URL
from scrapers import ChronoGenesisScraper
from services import QuotaCalculator, BombManager, ReportGenerator, MonthlyInfoService
from models import Member, QuotaRequirement, BotSettings

logger = logging.getLogger(__name__)


class AdminCommands(commands.Cog):
    """Administrative commands for quota management"""
    
    def __init__(self, bot):
        self.bot = bot
        self.scraper = ChronoGenesisScraper(SCRAPE_URL)
        self.quota_calculator = QuotaCalculator()
        self.bomb_manager = BombManager()
        self.report_generator = ReportGenerator()
        self.monthly_info_service = MonthlyInfoService()
        self.timezone = pytz.timezone(TIMEZONE)
    
    @app_commands.command(name="quota", description="Set the daily quota requirement")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_quota(self, interaction: discord.Interaction, amount: int):
        """Set the daily quota requirement"""
        await interaction.response.defer()
        
        try:
            if amount < 0:
                await interaction.followup.send("❌ Quota amount must be positive")
                return
            
            if amount > 10_000_000:
                await interaction.followup.send("❌ Quota amount seems unreasonably high (>10M). Please check your input.")
                return
            
            current_datetime = datetime.now(self.timezone)
            current_date = current_datetime.date()
            
            set_by = f"{interaction.user.name}#{interaction.user.discriminator}"
            quota_req = await QuotaRequirement.create(
                effective_date=current_date,
                daily_quota=amount,
                set_by=set_by
            )
            
            # Format amount
            if amount >= 1_000_000:
                formatted = f"{amount / 1_000_000:.1f}M"
            elif amount >= 1_000:
                formatted = f"{amount / 1_000:.1f}K"
            else:
                formatted = str(amount)
            
            embed = discord.Embed(
                title="✅ Quota Updated",
                description=f"Daily quota has been set to **{formatted} fans/day**",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="Effective Date",
                value=current_date.strftime('%Y-%m-%d'),
                inline=True
            )
            
            embed.add_field(
                name="Exact Amount",
                value=f"{amount:,} fans",
                inline=True
            )
            
            embed.add_field(
                name="Set By",
                value=set_by,
                inline=True
            )
            
            embed.add_field(
                name="ℹ️ Important",
                value="This quota applies from today onwards. Previous days are unaffected.",
                inline=False
            )
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Quota set to {amount:,} by {set_by} effective {current_date}")
            
            # Auto-update monthly info board if it exists
            try:
                monthly_info_message_id = await BotSettings.get_monthly_info_message_id()
                monthly_info_channel_id = await BotSettings.get_monthly_info_channel_id()
                
                if monthly_info_message_id and monthly_info_channel_id:
                    monthly_channel = self.bot.get_channel(monthly_info_channel_id)
                    if monthly_channel:
                        try:
                            monthly_message = await monthly_channel.fetch_message(monthly_info_message_id)
                            updated_embed = await self.monthly_info_service.create_monthly_info_embed(current_date)
                            await monthly_message.edit(embed=updated_embed)
                            logger.info("Auto-updated monthly info board after quota change")
                        except (discord.NotFound, discord.Forbidden) as e:
                            logger.warning(f"Could not auto-update monthly info board: {e}")
            except Exception as e:
                logger.error(f"Error auto-updating monthly info board: {e}")
            
        except Exception as e:
            logger.error(f"Error in set_quota: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="quota_history", description="View quota history for current month")
    @app_commands.checks.has_permissions(administrator=True)
    async def quota_history(self, interaction: discord.Interaction):
        """View all quota changes for the current month"""
        await interaction.response.defer()
        
        try:
            current_datetime = datetime.now(self.timezone)
            current_date = current_datetime.date()
            
            quota_reqs = await QuotaRequirement.get_all_current_month(current_date)
            
            if not quota_reqs:
                from config.settings import DAILY_QUOTA
                embed = discord.Embed(
                    title="📊 Quota History",
                    description=f"No quota changes this month. Using default: **{DAILY_QUOTA:,} fans/day**",
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow()
                )
                await interaction.followup.send(embed=embed)
                return
            
            embed = discord.Embed(
                title="📊 Quota History - Current Month",
                description=f"Showing {len(quota_reqs)} quota change(s)",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            for quota_req in quota_reqs:
                amount = quota_req.daily_quota
                if amount >= 1_000_000:
                    formatted = f"{amount / 1_000_000:.1f}M"
                elif amount >= 1_000:
                    formatted = f"{amount / 1_000:.1f}K"
                else:
                    formatted = str(amount)
                
                embed.add_field(
                    name=f"{quota_req.effective_date.strftime('%B %d, %Y')}",
                    value=f"**{formatted} fans/day** ({amount:,})\n"
                          f"Set by: {quota_req.set_by or 'Unknown'}",
                    inline=False
                )
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in quota_history: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="force_check", description="Manually trigger a quota check and report")
    @app_commands.checks.has_permissions(administrator=True)
    async def force_check(self, interaction: discord.Interaction):
        """Manually trigger the daily check"""
        await interaction.response.defer()
        
        try:
            current_datetime = datetime.now(self.timezone)
            current_date = current_datetime.date()
            
            report_channel_id = await BotSettings.get_report_channel_id()
            alert_channel_id = await BotSettings.get_alert_channel_id()
            
            if not report_channel_id:
                from config.settings import CHANNEL_ID
                report_channel_id = CHANNEL_ID
            
            if not alert_channel_id:
                alert_channel_id = report_channel_id
            
            report_channel = self.bot.get_channel(report_channel_id)
            alert_channel = self.bot.get_channel(alert_channel_id)
            
            if not report_channel:
                await interaction.followup.send(f"❌ Report channel not found. Use `/set_report_channel` first.")
                return
            
            if not alert_channel:
                alert_channel = report_channel
            
            # Scrape with retry logic
            max_retries = 3
            retry_delay = 10
            scraped_data = None
            current_day = None
            
            for attempt in range(1, max_retries + 1):
                try:
                    await interaction.followup.send(f"🔄 Scraping website (attempt {attempt}/{max_retries})...")
                    scraped_data = await self.scraper.scrape()
                    current_day = self.scraper.get_current_day()
                    
                    if scraped_data:
                        break
                except Exception as e:
                    if attempt == max_retries:
                        raise
                    await interaction.followup.send(f"⚠️ Attempt {attempt} failed, retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
            
            if not scraped_data:
                await interaction.followup.send("❌ Failed to scrape data after all retries")
                return
            
            await interaction.followup.send("⚙️ Processing data...")
            new_members, updated_members = await self.quota_calculator.process_scraped_data(
                scraped_data, current_date, current_day
            )
            
            newly_activated = await self.bomb_manager.check_and_activate_bombs(current_date)
            await self.bomb_manager.update_bomb_countdowns(current_date)
            deactivated = await self.bomb_manager.check_and_deactivate_bombs(current_date)
            members_to_kick = await self.bomb_manager.check_expired_bombs()
            
            status_summary = await self.quota_calculator.get_member_status_summary(current_date)
            bombs_data = await self.bomb_manager.get_active_bombs_with_members()
            
            daily_reports = self.report_generator.create_daily_report(
                status_summary, bombs_data, current_date
            )
            
            for embed in daily_reports:
                await report_channel.send(embed=embed)
            
            if newly_activated:
                bomb_data = []
                for bomb in newly_activated:
                    member = await Member.get_by_id(bomb.member_id)
                    bomb_data.append({'bomb': bomb, 'member': member})
                alert = self.report_generator.create_bomb_activation_alert(bomb_data)
                await alert_channel.send(embed=alert)
            
            if members_to_kick:
                kick_alert = self.report_generator.create_kick_alert(members_to_kick)
                await alert_channel.send(embed=kick_alert)
            
            await interaction.followup.send(
                f"✅ Check complete: {updated_members} members updated, {new_members} new members"
            )
            
        except Exception as e:
            logger.error(f"Error in force_check: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="add_member", description="Manually add a new member")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_member(self, interaction: discord.Interaction, 
                         trainer_name: str, join_date: str, trainer_id: str = None):
        """Manually add a member (format: YYYY-MM-DD)"""
        await interaction.response.defer()
        
        try:
            join_date_obj = datetime.strptime(join_date, "%Y-%m-%d").date()
            
            if trainer_id:
                existing = await Member.get_by_trainer_id(trainer_id)
            else:
                existing = await Member.get_by_name(trainer_name)
                
            if existing:
                await interaction.followup.send(f"❌ Member '{trainer_name}' already exists")
                return
            
            member = await Member.create(trainer_name, join_date_obj, trainer_id)
            
            await interaction.followup.send(
                f"✅ Added member: {trainer_name} (joined {join_date}, ID: {trainer_id or 'N/A'})"
            )
            
        except ValueError:
            await interaction.followup.send("❌ Invalid date format. Use YYYY-MM-DD")
        except Exception as e:
            logger.error(f"Error in add_member: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="deactivate_member", description="Manually deactivate a member")
    @app_commands.checks.has_permissions(administrator=True)
    async def deactivate_member(self, interaction: discord.Interaction, trainer_name: str):
        """Manually deactivate a member"""
        await interaction.response.defer()
        
        try:
            member = await Member.get_by_name(trainer_name)
            
            if not member:
                await interaction.followup.send(f"❌ Member '{trainer_name}' not found")
                return
            
            if not member.is_active:
                await interaction.followup.send(f"ℹ️ {trainer_name} is already inactive")
                return
            
            await member.deactivate(manual=True)
            
            embed = discord.Embed(
                title="✅ Member Manually Deactivated",
                description=f"**{trainer_name}** has been deactivated and will not be auto-reactivated.",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(
                name="ℹ️ Note",
                value="This member will stay inactive even if they appear in scraped data. "
                      "Use `/activate_member` to reactivate them.",
                inline=False
            )
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Manually deactivated member: {trainer_name} by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in deactivate_member: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="activate_member", description="Manually reactivate a deactivated member")
    @app_commands.checks.has_permissions(administrator=True)
    async def activate_member(self, interaction: discord.Interaction, trainer_name: str):
        """Manually reactivate a member"""
        await interaction.response.defer()
        
        try:
            member = await Member.get_by_name(trainer_name)
            
            if not member:
                await interaction.followup.send(f"❌ Member '{trainer_name}' not found")
                return
            
            if member.is_active:
                await interaction.followup.send(f"ℹ️ {trainer_name} is already active")
                return
            
            await member.activate()
            
            embed = discord.Embed(
                title="✅ Member Reactivated",
                description=f"**{trainer_name}** has been reactivated.",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Manually reactivated member: {trainer_name} by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in activate_member: {e}", exc_info=True)
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
            
            for item in bombs_data[:25]:
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