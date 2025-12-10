"""
Channel and bot settings commands
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import logging
import pytz

from models import BotSettings
from services import MonthlyInfoService
from config.settings import TIMEZONE

logger = logging.getLogger(__name__)


class SettingsCommands(commands.Cog):
    """Channel and bot configuration commands"""
    
    def __init__(self, bot):
        self.bot = bot
        self.monthly_info_service = MonthlyInfoService()
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
        """Set the channel where alerts will be posted"""
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
    
    @app_commands.command(name="post_monthly_info", description="Post the monthly info board (auto-updates)")
    @app_commands.checks.has_permissions(administrator=True)
    async def post_monthly_info(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        """Post or update the monthly information board"""
        await interaction.response.defer()
        
        try:
            # Use current channel if none specified
            target_channel = channel or interaction.channel
            
            # Get current date
            current_datetime = datetime.now(self.timezone)
            current_date = current_datetime.date()
            
            # Generate embed
            embed = await self.monthly_info_service.create_monthly_info_embed(current_date)
            
            # Check if monthly info already exists
            existing_message_id = await BotSettings.get_monthly_info_message_id()
            existing_channel_id = await BotSettings.get_monthly_info_channel_id()
            
            if existing_message_id and existing_channel_id:
                # Try to update existing message
                try:
                    existing_channel = self.bot.get_channel(existing_channel_id)
                    if existing_channel:
                        existing_message = await existing_channel.fetch_message(existing_message_id)
                        await existing_message.edit(embed=embed)
                        
                        await interaction.followup.send(
                            f"✅ Updated existing monthly info board in {existing_channel.mention}"
                        )
                        return
                except (discord.NotFound, discord.Forbidden):
                    logger.warning(f"Could not find/edit existing monthly info message")
            
            # Post new message
            message = await target_channel.send(embed=embed)
            
            # Save location
            await BotSettings.set_monthly_info_location(target_channel.id, message.id)
            
            embed_response = discord.Embed(
                title="✅ Monthly Info Board Posted",
                description=f"Posted in {target_channel.mention}\n\n"
                           f"This message will auto-update when quota changes.",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            await interaction.followup.send(embed=embed_response)
            logger.info(f"Monthly info board posted in {target_channel.name} by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in post_monthly_info: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")