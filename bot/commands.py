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
from models import Member, QuotaHistory, Bomb, QuotaRequirement, BotSettings

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
    
    @app_commands.command(name="set_report_channel", description="Set the channel for daily reports")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_report_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set the channel where daily reports will be posted"""
        await interaction.response.defer()
        
        try:
            await BotSettings.set_report_channel_id(channel.id)
            
            embed = discord.Embed(
                title="✅ Report Channel Updated",
                description=f"Daily reports will now be posted to {channel.mention}",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Report channel set to {channel.name} ({channel.id}) by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in set_report_channel: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="set_alert_channel", description="Set the channel for alerts (bombs, kicks)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_alert_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set the channel where alerts (bomb activations, kick warnings) will be posted"""
        await interaction.response.defer()
        
        try:
            await BotSettings.set_alert_channel_id(channel.id)
            
            embed = discord.Embed(
                title="✅ Alert Channel Updated",
                description=f"Alerts (bomb warnings, kick notifications) will now be posted to {channel.mention}",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Alert channel set to {channel.name} ({channel.id}) by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in set_alert_channel: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="channel_settings", description="View current channel configuration")
    @app_commands.checks.has_permissions(administrator=True)
    async def channel_settings(self, interaction: discord.Interaction):
        """View current channel settings"""
        await interaction.response.defer()
        
        try:
            report_channel_id = await BotSettings.get_report_channel_id()
            alert_channel_id = await BotSettings.get_alert_channel_id()
            
            embed = discord.Embed(
                title="⚙️ Channel Settings",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            # Report channel
            if report_channel_id:
                report_channel = self.bot.get_channel(report_channel_id)
                if report_channel:
                    embed.add_field(
                        name="📊 Daily Reports Channel",
                        value=f"{report_channel.mention} (ID: {report_channel_id})",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="📊 Daily Reports Channel",
                        value=f"⚠️ Channel not found (ID: {report_channel_id})",
                        inline=False
                    )
            else:
                from config.settings import CHANNEL_ID
                if CHANNEL_ID:
                    channel = self.bot.get_channel(CHANNEL_ID)
                    embed.add_field(
                        name="📊 Daily Reports Channel",
                        value=f"Using .env fallback: {channel.mention if channel else f'ID: {CHANNEL_ID}'}",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="📊 Daily Reports Channel",
                        value="❌ Not configured",
                        inline=False
                    )
            
            # Alert channel
            if alert_channel_id:
                alert_channel = self.bot.get_channel(alert_channel_id)
                if alert_channel:
                    embed.add_field(
                        name="🚨 Alerts Channel",
                        value=f"{alert_channel.mention} (ID: {alert_channel_id})",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="🚨 Alerts Channel",
                        value=f"⚠️ Channel not found (ID: {alert_channel_id})",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="🚨 Alerts Channel",
                    value="⚠️ Not configured (using reports channel)",
                    inline=False
                )
            
            embed.set_footer(text="Use /set_report_channel and /set_alert_channel to configure")
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in channel_settings: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="quota", description="Set the daily quota requirement (from today onwards)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_quota(self, interaction: discord.Interaction, amount: int):
        """
        Set the daily quota requirement
        
        Args:
            amount: Daily fan quota (e.g., 1000000 for 1M fans/day)
        """
        await interaction.response.defer()
        
        try:
            # Validate amount
            if amount < 0:
                await interaction.followup.send("❌ Quota amount must be positive")
                return
            
            if amount > 10_000_000:
                await interaction.followup.send("❌ Quota amount seems unreasonably high (>10M). Please check your input.")
                return
            
            # Get current date in configured timezone
            current_datetime = datetime.now(self.timezone)
            current_date = current_datetime.date()
            
            # Create quota requirement
            set_by = f"{interaction.user.name}#{interaction.user.discriminator}"
            quota_req = await QuotaRequirement.create(
                effective_date=current_date,
                daily_quota=amount,
                set_by=set_by
            )
            
            # Format amount nicely
            if amount >= 1_000_000:
                formatted = f"{amount / 1_000_000:.1f}M"
            elif amount >= 1_000:
                formatted = f"{amount / 1_000:.1f}K"
            else:
                formatted = str(amount)
            
            # Create success embed
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
                value="This quota applies **from today onwards**. Previous days are unaffected.",
                inline=False
            )
            
            await interaction.followup.send(embed=embed)
            
            logger.info(f"Quota set to {amount:,} by {set_by} effective {current_date}")
            
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
            
            # Get all quota requirements for current month
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
                # Format amount
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
            # Get current date
            current_datetime = datetime.now(self.timezone)
            current_date = current_datetime.date()
            
            # Get channels
            report_channel_id = await BotSettings.get_report_channel_id()
            alert_channel_id = await BotSettings.get_alert_channel_id()
            
            # Fallback to CHANNEL_ID from .env if not set
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
                    import asyncio
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
            
            if not scraped_data:
                await interaction.followup.send("❌ Failed to scrape data after all retries")
                return
            
            # Process
            await interaction.followup.send("⚙️ Processing data...")
            new_members, updated_members = await self.quota_calculator.process_scraped_data(
                scraped_data, current_date, current_day
            )
            
            # Bombs
            newly_activated = await self.bomb_manager.check_and_activate_bombs(current_date)
            await self.bomb_manager.update_bomb_countdowns(current_date)
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
                await report_channel.send(embed=embed)
            
            # Alerts
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