"""
Channel and bot settings commands
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import logging
import pytz

from models import Club
from services import MonthlyInfoService

logger = logging.getLogger(__name__)


class SettingsCommands(commands.Cog):
    """Channel and bot configuration commands"""
    
    def __init__(self, bot):
        self.bot = bot
        self.monthly_info_service = MonthlyInfoService()
    
    async def club_autocomplete(self, interaction: discord.Interaction, current: str):
        """Autocomplete for club names visible in this guild"""
        try:
            club_names = await Club.get_names_for_guild(interaction.guild_id)
            return [
                app_commands.Choice(name=name, value=name)
                for name in club_names
                if current.lower() in name.lower()
            ][:25]
        except Exception as e:
            logger.error(f"Error in club autocomplete: {e}")
            return []
    
    @app_commands.command(name="set_report_channel", description="Set the channel for daily reports")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_report_channel(self, interaction: discord.Interaction, channel: discord.TextChannel, club: str):
        """Set the channel where daily reports will be posted"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return
            
            await club_obj.set_channels(report_channel_id=channel.id)
            
            embed = discord.Embed(
                title=f"✅ Report Channel Updated - {club}",
                description=f"Daily reports will now be posted to {channel.mention}",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Report channel for {club} set to {channel.name} ({channel.id}) by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in set_report_channel: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="set_alert_channel", description="Set the channel for alerts (bombs, kicks)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_alert_channel(self, interaction: discord.Interaction, channel: discord.TextChannel, club: str):
        """Set the channel where alerts will be posted"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return
            
            await club_obj.set_channels(alert_channel_id=channel.id)
            
            embed = discord.Embed(
                title=f"✅ Alert Channel Updated - {club}",
                description=f"Alerts (bomb warnings, kick notifications) will now be posted to {channel.mention}",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Alert channel for {club} set to {channel.name} ({channel.id}) by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in set_alert_channel: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="channel_settings", description="View current channel configuration")
    @app_commands.checks.has_permissions(administrator=True)
    async def channel_settings(self, interaction: discord.Interaction, club: str):
        """View current channel settings"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return
            
            embed = discord.Embed(
                title=f"⚙️ Channel Settings - {club}",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            # Report channel
            if club_obj.report_channel_id:
                report_channel = self.bot.get_channel(club_obj.report_channel_id)
                if report_channel:
                    embed.add_field(
                        name="📊 Daily Reports Channel",
                        value=f"{report_channel.mention} (ID: {club_obj.report_channel_id})",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="📊 Daily Reports Channel",
                        value=f"⚠️ Channel not found (ID: {club_obj.report_channel_id})",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="📊 Daily Reports Channel",
                    value="❌ Not configured",
                    inline=False
                )
            
            # Alert channel
            if club_obj.alert_channel_id:
                alert_channel = self.bot.get_channel(club_obj.alert_channel_id)
                if alert_channel:
                    embed.add_field(
                        name="🚨 Alerts Channel",
                        value=f"{alert_channel.mention} (ID: {club_obj.alert_channel_id})",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="🚨 Alerts Channel",
                        value=f"⚠️ Channel not found (ID: {club_obj.alert_channel_id})",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="🚨 Alerts Channel",
                    value="⚠️ Not configured (using reports channel)",
                    inline=False
                )
            
            # Monthly info board
            channel_id, message_id = await club_obj.get_monthly_info_location()
            if channel_id and message_id:
                info_channel = self.bot.get_channel(channel_id)
                if info_channel:
                    embed.add_field(
                        name="📋 Monthly Info Board",
                        value=f"{info_channel.mention}\n[Jump to message](https://discord.com/channels/{interaction.guild_id}/{channel_id}/{message_id})",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="📋 Monthly Info Board",
                        value="⚠️ Channel not found",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="📋 Monthly Info Board",
                    value="❌ Not posted (use `/post_monthly_info`)",
                    inline=False
                )
            
            embed.set_footer(text="Use /set_report_channel and /set_alert_channel to configure")
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in channel_settings: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="post_monthly_info", description="Post the monthly info board (auto-updates)")
    @app_commands.checks.has_permissions(administrator=True)
    async def post_monthly_info(self, interaction: discord.Interaction, club: str, channel: discord.TextChannel = None):
        """Post or update the monthly information board"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return
            
            # Use current channel if none specified
            target_channel = channel or interaction.channel
            
            club_tz = pytz.timezone(club_obj.timezone)
            current_datetime = datetime.now(club_tz)
            current_date = current_datetime.date()
            
            embed = await self.monthly_info_service.create_monthly_info_embed(
                club_obj.club_id, 
                club_obj.club_name, 
                current_date
            )
            
            message = await target_channel.send(embed=embed)
            
            # Save the message location so it can be auto-updated later
            await club_obj.set_monthly_info_location(target_channel.id, message.id)
            
            embed_response = discord.Embed(
                title="✅ Monthly Info Board Posted",
                description=f"Posted in {target_channel.mention} for **{club_obj.club_name}**\n\n"
                           f"This message will auto-update when quota changes.\n"
                           f"[Jump to board](https://discord.com/channels/{interaction.guild_id}/{target_channel.id}/{message.id})",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            embed_response.add_field(
                name="📝 Note",
                value="The board location has been saved and will persist through bot restarts.",
                inline=False
            )
            
            await interaction.followup.send(embed=embed_response)
            logger.info(f"Monthly info board posted for {club_obj.club_name} in {target_channel.name} by {interaction.user} - saved location")
            
        except Exception as e:
            logger.error(f"Error in post_monthly_info: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    # Apply autocomplete
    set_report_channel.autocomplete('club')(club_autocomplete)
    set_alert_channel.autocomplete('club')(club_autocomplete)
    channel_settings.autocomplete('club')(club_autocomplete)
    post_monthly_info.autocomplete('club')(club_autocomplete)


async def setup(bot):
    """Setup function for loading the cog"""
    await bot.add_cog(SettingsCommands(bot))