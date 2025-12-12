"""
Administrative commands for quota management
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, time
import logging
import pytz
import asyncio

from scrapers import ChronoGenesisScraper
from services import QuotaCalculator, BombManager, ReportGenerator, MonthlyInfoService
from models import Member, QuotaRequirement, BotSettings, Club

logger = logging.getLogger(__name__)


class AdminCommands(commands.Cog):
    """Administrative commands for quota management"""
    
    def __init__(self, bot):
        self.bot = bot
        self.quota_calculator = QuotaCalculator()
        self.bomb_manager = BombManager()
        self.report_generator = ReportGenerator()
        self.monthly_info_service = MonthlyInfoService()
    
    async def club_autocomplete(self, interaction: discord.Interaction, current: str):
        """Autocomplete for club names"""
        try:
            club_names = await Club.get_all_names()
            return [
                app_commands.Choice(name=name, value=name)
                for name in club_names
                if current.lower() in name.lower()
            ][:25]
        except Exception as e:
            logger.error(f"Error in club autocomplete: {e}")
            return []
    
    @app_commands.command(name="quota", description="Set the daily quota requirement")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_quota(self, interaction: discord.Interaction, amount: int, club: str):
        """Set the daily quota requirement"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if amount < 0:
                await interaction.followup.send("❌ Quota amount must be positive")
                return
            
            if amount > 10_000_000:
                await interaction.followup.send("❌ Quota amount seems unreasonably high (>10M). Please check your input.")
                return
            
            # Get current date in club's timezone
            club_tz = pytz.timezone(club_obj.timezone)
            current_datetime = datetime.now(club_tz)
            current_date = current_datetime.date()
            
            set_by = f"{interaction.user.name}#{interaction.user.discriminator}"
            quota_req = await QuotaRequirement.create(
                club_id=club_obj.club_id,
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
                title=f"✅ Quota Updated - {club}",
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
            logger.info(f"Quota set to {amount:,} for {club} by {set_by} effective {current_date}")
            
        except Exception as e:
            logger.error(f"Error in set_quota: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="quota_history", description="View quota history for current month")
    @app_commands.checks.has_permissions(administrator=True)
    async def quota_history(self, interaction: discord.Interaction, club: str):
        """View all quota changes for the current month"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            club_tz = pytz.timezone(club_obj.timezone)
            current_datetime = datetime.now(club_tz)
            current_date = current_datetime.date()
            
            quota_reqs = await QuotaRequirement.get_all_current_month(club_obj.club_id, current_date)
            
            if not quota_reqs:
                embed = discord.Embed(
                    title=f"📊 Quota History - {club}",
                    description=f"No quota changes this month. Using default: **{club_obj.daily_quota:,} fans/day**",
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow()
                )
                await interaction.followup.send(embed=embed)
                return
            
            embed = discord.Embed(
                title=f"📊 Quota History - {club} - Current Month",
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
    async def force_check(self, interaction: discord.Interaction, club: str):
        """Manually trigger the daily check"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            club_tz = pytz.timezone(club_obj.timezone)
            current_datetime = datetime.now(club_tz)
            current_date = current_datetime.date()
            
            report_channel = self.bot.get_channel(club_obj.report_channel_id)
            alert_channel = self.bot.get_channel(club_obj.alert_channel_id or club_obj.report_channel_id)
            
            if not report_channel:
                await interaction.followup.send(f"❌ Report channel not configured for {club}. Use `/set_report_channel` first.")
                return
            
            if not alert_channel:
                alert_channel = report_channel
            
            # Scrape with retry logic
            max_retries = 3
            retry_delay = 10
            scraped_data = None
            current_day = None
            
            scraper = ChronoGenesisScraper(club_obj.scrape_url)
            
            for attempt in range(1, max_retries + 1):
                try:
                    await interaction.followup.send(f"🔄 Scraping {club} (attempt {attempt}/{max_retries})...")
                    scraped_data = await scraper.scrape()
                    current_day = scraper.get_current_day()
                    
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
                club_obj.club_id, scraped_data, current_date, current_day
            )
            
            newly_activated = await self.bomb_manager.check_and_activate_bombs(club_obj, current_date)
            await self.bomb_manager.update_bomb_countdowns(club_obj.club_id, current_date)
            deactivated = await self.bomb_manager.check_and_deactivate_bombs(club_obj.club_id, current_date)
            members_to_kick = await self.bomb_manager.check_expired_bombs(club_obj.club_id)
            
            status_summary = await self.quota_calculator.get_member_status_summary(club_obj.club_id, current_date)
            bombs_data = await self.bomb_manager.get_active_bombs_with_members(club_obj.club_id)
            
            daily_reports = self.report_generator.create_daily_report(
                club_obj.club_name, club_obj.daily_quota, status_summary, bombs_data, current_date
            )
            
            for embed in daily_reports:
                await report_channel.send(embed=embed)
            
            # Send bomb deactivation report if any
            if deactivated:
                deactivation_embed = self.report_generator.create_bomb_deactivation_report(club_obj.club_name, deactivated)
                await report_channel.send(embed=deactivation_embed)
                logger.info(f"✅ Bomb deactivation report sent ({len(deactivated)} member(s))")
            
            if newly_activated:
                bomb_data = []
                for bomb in newly_activated:
                    member = await Member.get_by_id(bomb.member_id)
                    bomb_data.append({'bomb': bomb, 'member': member})
                alert = self.report_generator.create_bomb_activation_alert(club_obj.club_name, bomb_data)
                await alert_channel.send(embed=alert)
            
            if members_to_kick:
                kick_alert = self.report_generator.create_kick_alert(club_obj.club_name, members_to_kick)
                await alert_channel.send(embed=kick_alert)
            
            # Final success message
            if deactivated:
                await interaction.followup.send(
                    f"✅ Check complete for {club}: {updated_members} members updated, {new_members} new members, {len(deactivated)} bombs defused"
                )
            else:
                await interaction.followup.send(
                    f"✅ Check complete for {club}: {updated_members} members updated, {new_members} new members"
                )
            
        except Exception as e:
            logger.error(f"Error in force_check: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="add_member", description="Manually add a new member")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_member(self, interaction: discord.Interaction, 
                         trainer_name: str, join_date: str, club: str, trainer_id: str = None):
        """Manually add a member (format: YYYY-MM-DD)"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            join_date_obj = datetime.strptime(join_date, "%Y-%m-%d").date()
            
            if trainer_id:
                existing = await Member.get_by_trainer_id(club_obj.club_id, trainer_id)
            else:
                existing = await Member.get_by_name(club_obj.club_id, trainer_name)
                
            if existing:
                await interaction.followup.send(f"❌ Member '{trainer_name}' already exists in {club}")
                return
            
            member = await Member.create(club_obj.club_id, trainer_name, join_date_obj, trainer_id)
            
            await interaction.followup.send(
                f"✅ Added member to {club}: {trainer_name} (joined {join_date}, ID: {trainer_id or 'N/A'})"
            )
            
        except ValueError:
            await interaction.followup.send("❌ Invalid date format. Use YYYY-MM-DD")
        except Exception as e:
            logger.error(f"Error in add_member: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="deactivate_member", description="Manually deactivate a member")
    @app_commands.checks.has_permissions(administrator=True)
    async def deactivate_member(self, interaction: discord.Interaction, trainer_name: str, club: str):
        """Manually deactivate a member"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            member = await Member.get_by_name(club_obj.club_id, trainer_name)
            
            if not member:
                await interaction.followup.send(f"❌ Member '{trainer_name}' not found in {club}")
                return
            
            if not member.is_active:
                await interaction.followup.send(f"ℹ️ {trainer_name} is already inactive")
                return
            
            await member.deactivate(manual=True)
            
            embed = discord.Embed(
                title=f"✅ Member Manually Deactivated - {club}",
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
            logger.info(f"Manually deactivated member: {trainer_name} in {club} by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in deactivate_member: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="activate_member", description="Manually reactivate a deactivated member")
    @app_commands.checks.has_permissions(administrator=True)
    async def activate_member(self, interaction: discord.Interaction, trainer_name: str, club: str):
        """Manually reactivate a member"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            member = await Member.get_by_name(club_obj.club_id, trainer_name)
            
            if not member:
                await interaction.followup.send(f"❌ Member '{trainer_name}' not found in {club}")
                return
            
            if member.is_active:
                await interaction.followup.send(f"ℹ️ {trainer_name} is already active")
                return
            
            await member.activate()
            
            embed = discord.Embed(
                title=f"✅ Member Reactivated - {club}",
                description=f"**{trainer_name}** has been reactivated.",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Manually reactivated member: {trainer_name} in {club} by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in activate_member: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="bomb_status", description="View all active bombs")
    @app_commands.checks.has_permissions(administrator=True)
    async def bomb_status(self, interaction: discord.Interaction, club: str):
        """View all active bombs"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            bombs_data = await self.bomb_manager.get_active_bombs_with_members(club_obj.club_id)
            
            if not bombs_data:
                await interaction.followup.send(f"✅ No active bombs in {club}!")
                return
            
            embed = discord.Embed(
                title=f"💣 Active Bombs - {club}",
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
    
    # Apply autocomplete to all commands
    set_quota.autocomplete('club')(club_autocomplete)
    quota_history.autocomplete('club')(club_autocomplete)
    force_check.autocomplete('club')(club_autocomplete)
    add_member.autocomplete('club')(club_autocomplete)
    deactivate_member.autocomplete('club')(club_autocomplete)
    activate_member.autocomplete('club')(club_autocomplete)
    bomb_status.autocomplete('club')(club_autocomplete)
